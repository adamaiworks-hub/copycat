#!/usr/bin/env python3
"""
Kalshi CopyCat Bot
──────────────────────────────────────────────────────────────────
Monitors top 20 Polymarket traders per category, finds matching
Kalshi markets, and executes copy trades directly on Kalshi.

Architecture:
  • TraderAnalyzer  — category-level top-20 lists + win rate stats
  • EventMatcher    — Polymarket title + date → Kalshi ticker + side
  • KalshiClient    — RSA-signed API execution (reused from bot.py)

Config (kalshi_copycat_config.json, updated live by dashboard):
  wager_mode: "fixed" | "pct" | "scaled"
  wager_fixed: 10           (USD per trade in fixed mode)
  wager_pct: 2              (% of Kalshi balance in pct mode)
  wager_scale_base: 10      (base USD for scaled mode)
  wager_scale_max: 3.0      (max multiplier in scaled mode)
  win_rate_threshold: 0.85  (global minimum; override per trader)
  daily_loss_limit: 200
  max_daily_trades: 30
  trader_overrides: {}      (per-address overrides set via dashboard)
  category_filters: {}      (per-category enable/threshold overrides)
"""

import base64
import json
import logging
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv

from kalshi_trader_analyzer import TraderAnalyzer, detect_category, extract_subjects
from kalshi_event_matcher    import EventMatcher

load_dotenv()

# ─── Config ────────────────────────────────────────────────────────────────────

API_KEY_ID       = os.getenv("KALSHI_API_KEY_ID", "")
PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "private_key.pem")
BASE_URL         = "https://trading-api.kalshi.com/trade-api/v2"
CLOUD_URL        = os.getenv("CLOUD_DASHBOARD_URL",
                             "https://cloud-dashboard-production-0532.up.railway.app")
LICENSE_KEY      = os.getenv("COPYCAT_LICENSE_KEY", "")
POLL_SECS        = int(os.getenv("POLL_SECS", "30"))
CONFIG_FILE      = Path("kalshi_copycat_config.json")
LOG_FILE         = Path("kalshi_copycat_trades.jsonl")

DEFAULT_CONFIG = {
    "wager_mode":         "fixed",
    "wager_fixed":        10,
    "wager_pct":          2,
    "wager_scale_base":   10,
    "wager_scale_max":    3.0,
    "win_rate_threshold": 0.85,
    "daily_loss_limit":   200,
    "max_daily_trades":   30,
    "trader_overrides":   {},
    "category_filters":   {},
}

# ─── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("kalshi-copycat")


def log_event(event_type: str, data: dict):
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "type": event_type, **data}
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(rec) + "\n")
    if event_type not in ("heartbeat", "sync"):
        log.info(f"[{event_type}] {data}")


# ─── Config helpers ────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_FILE.read_text())}
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ─── Kalshi API client ─────────────────────────────────────────────────────────

def _load_key():
    with open(PRIVATE_KEY_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


_private_key = None


def _get_key():
    global _private_key
    if _private_key is None:
        _private_key = _load_key()
    return _private_key


def _sign(method: str, path: str) -> tuple[str, str]:
    ts  = str(int(time.time() * 1000))
    msg = (ts + method.upper() + path).encode()
    sig = _get_key().sign(msg, padding.PKCS1v15(), hashes.SHA256())
    return ts, base64.b64encode(sig).decode()


class KalshiClient:
    def __init__(self):
        self.session = requests.Session()

    def _headers(self, method: str, path: str) -> dict:
        ts, sig = _sign(method, path)
        return {
            "KALSHI-ACCESS-KEY":       API_KEY_ID,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "Content-Type":            "application/json",
        }

    def _get(self, path: str, **params):
        r = self.session.get(
            f"{BASE_URL}{path}",
            headers=self._headers("GET", path),
            params={k: v for k, v in params.items() if v is not None},
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict):
        r = self.session.post(
            f"{BASE_URL}{path}",
            headers=self._headers("POST", path),
            json=body,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def get_balance(self) -> float:
        data = self._get("/portfolio/balance")
        return data["balance"]["available_balance"] / 100  # cents → dollars

    def get_positions(self) -> dict:
        """Returns {ticker: net_position}"""
        data = self._get("/portfolio/positions")
        return {p["ticker"]: p.get("position", 0)
                for p in data.get("market_positions", [])}

    def get_fills(self, limit: int = 100) -> list:
        """Recent filled orders — used to calculate daily PnL."""
        data = self._get("/portfolio/fills", limit=limit)
        return data.get("fills", [])

    def place_order(self, ticker: str, side: str, count: int, price_cents: int) -> str:
        """Place a market-price limit order. Returns order_id."""
        body = {
            "ticker":          ticker,
            "client_order_id": str(uuid.uuid4()),
            "side":            side,      # "yes" | "no"
            "action":          "buy",
            "count":           count,
            "type":            "limit",
            "yes_price":       price_cents if side == "yes" else (100 - price_cents),
        }
        resp = self._post("/portfolio/orders", body)
        return resp["order"]["id"]

    def get_market(self, ticker: str) -> dict:
        return self._get(f"/markets/{ticker}")

    def get_event(self, event_ticker: str) -> dict:
        return self._get(f"/events/{event_ticker}")


# ─── Position sizing ───────────────────────────────────────────────────────────

def compute_trade_size(cfg: dict, kalshi_client: KalshiClient,
                       win_rate: float, price_cents: int) -> int:
    """
    Returns number of contracts to buy.
    - fixed:  wager_fixed USD / price
    - pct:    (balance * wager_pct / 100) / price
    - scaled: base * multiplier / price
      multiplier = 1.0 + (win_rate - 0.85) * (max_mult - 1.0) / 0.15
      (linear from 1.0× at 85% up to max_mult× at 100%)
    """
    if price_cents <= 0 or price_cents >= 100:
        return 0

    price_usd = price_cents / 100

    mode = cfg.get("wager_mode", "fixed")

    if mode == "pct":
        try:
            balance = kalshi_client.get_balance()
        except Exception:
            balance = float(cfg.get("wager_fixed", 10))
        pct    = float(cfg.get("wager_pct", 2)) / 100
        amount = balance * pct

    elif mode == "scaled":
        base     = float(cfg.get("wager_scale_base", 10))
        max_mult = float(cfg.get("wager_scale_max", 3.0))
        mult     = 1.0 + max(0.0, win_rate - 0.85) * (max_mult - 1.0) / 0.15
        mult     = min(mult, max_mult)
        amount   = base * mult

    else:  # fixed
        amount = float(cfg.get("wager_fixed", 10))

    return max(1, int(amount / price_usd))


# ─── Main bot class ────────────────────────────────────────────────────────────

class KalshiCopyCat:
    def __init__(self):
        self.client   = KalshiClient()
        self.analyzer = TraderAnalyzer()
        self.matcher  = EventMatcher(self.client)

        # address → last-checked timestamp
        self.last_checked: dict[str, float] = {}

        # Our Kalshi positions: {ticker: contracts}
        self.positions: dict[str, int] = {}

        # Daily tracking
        self.day_str:       str   = ""
        self.daily_trades:  int   = 0
        self.daily_pnl:     float = 0.0

        # Shutdown flag
        self.running = True
        signal.signal(signal.SIGINT,  self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        log.info(f"Kalshi CopyCat starting — trade log → {LOG_FILE}")
        if not API_KEY_ID or not Path(PRIVATE_KEY_PATH).exists():
            log.error("KALSHI_API_KEY_ID or private key not configured. Exiting.")
            sys.exit(1)

        # Verify connection
        try:
            bal = self.client.get_balance()
            log.info(f"Kalshi balance: ${bal:.2f}")
        except Exception as e:
            log.error(f"Kalshi auth failed: {e}")
            sys.exit(1)

    def _shutdown(self, *_):
        log.info("Shutting down Kalshi CopyCat…")
        self.running = False
        self._sync_cloud(final=True)
        sys.exit(0)

    # ── Daily bookkeeping ─────────────────────────────────────────────────────

    def _reset_day_if_new(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.day_str:
            self.day_str      = today
            self.daily_trades = 0
            self.daily_pnl    = 0.0
            log.info(f"New trading day: {today}")

    def _within_daily_limits(self, cfg: dict) -> bool:
        self._reset_day_if_new()
        max_trades = int(cfg.get("max_daily_trades", 30))
        loss_limit = float(cfg.get("daily_loss_limit", 200))
        if self.daily_trades >= max_trades:
            log.warning(f"Max daily trades reached ({max_trades}).")
            return False
        if self.daily_pnl <= -loss_limit:
            log.warning(f"Daily loss limit hit (${self.daily_pnl:.2f}).")
            return False
        return True

    # ── Accuracy gate ─────────────────────────────────────────────────────────

    def _passes_filter(self, address: str, parent_cat: str, sub_cat: str,
                       subjects: list[str], cfg: dict) -> tuple[bool, float]:
        """
        Returns (should_copy, win_rate_used).
        Checks global threshold → category override → trader override.
        """
        global_thresh = float(cfg.get("win_rate_threshold", 0.85))

        # Per-category override
        cat_cfg   = cfg.get("category_filters", {}).get(sub_cat, {})
        cat_on    = cat_cfg.get("enabled", True)
        cat_thresh= float(cat_cfg.get("threshold", global_thresh))
        if not cat_on:
            return False, 0.0

        # Per-trader override
        trader_cfg   = cfg.get("trader_overrides", {}).get(address, {})
        trader_on    = trader_cfg.get("enabled", True)
        trader_cats  = trader_cfg.get("categories", {})
        t_cat_cfg    = trader_cats.get(sub_cat, {})
        t_cat_on     = t_cat_cfg.get("enabled", True)
        t_cat_thresh = float(t_cat_cfg.get("threshold", cat_thresh))
        if not trader_on or not t_cat_on:
            return False, 0.0

        # Get the most specific win rate available
        best_rate = 0.0
        for subj in subjects:
            r = self.analyzer.get_win_rate(address, parent_cat, sub_cat, subj)
            if r > best_rate:
                best_rate = r
        if best_rate == 0.0:
            best_rate = self.analyzer.get_win_rate(address, parent_cat, sub_cat)

        passes = best_rate >= t_cat_thresh
        return passes, best_rate

    # ── Trade execution ───────────────────────────────────────────────────────

    def _execute_copy(self, trade: dict, kalshi_ticker: str, kalshi_side: str,
                      win_rate: float, cfg: dict):
        """Place order on Kalshi and log the result."""
        try:
            mkt_data  = self.client.get_market(kalshi_ticker)
            market    = mkt_data.get("market", mkt_data)
            yes_price = int(market.get("yes_price") or market.get("yes_bid") or 50)
            price_cts = yes_price if kalshi_side == "yes" else (100 - yes_price)

            # Trader-specific wager override
            trader_cfg = cfg.get("trader_overrides", {}).get(trade["address"], {})
            if "wager_fixed" in trader_cfg:
                amount_usd = float(trader_cfg["wager_fixed"])
                count      = max(1, int(amount_usd / (price_cts / 100)))
            else:
                count = compute_trade_size(cfg, self.client, win_rate, price_cts)

            if count <= 0:
                log.warning(f"Skip {kalshi_ticker} — computed 0 contracts")
                return

            order_id = self.client.place_order(kalshi_ticker, kalshi_side, count, price_cts)

            cost_usd = round(count * price_cts / 100, 2)
            self.daily_trades += 1
            self.daily_pnl    -= cost_usd   # pending; will settle on resolution

            log_event("kalshi_copy_buy", {
                "trader":      trade.get("username", trade["address"][:10]),
                "address":     trade["address"],
                "poly_slug":   trade.get("slug", ""),
                "poly_title":  trade.get("title", ""),
                "poly_outcome":trade.get("outcome", ""),
                "kalshi_tick": kalshi_ticker,
                "kalshi_side": kalshi_side,
                "count":       count,
                "price_cts":   price_cts,
                "cost_usd":    cost_usd,
                "win_rate":    win_rate,
                "order_id":    order_id,
            })

            log.info(
                f"COPY BUY  {trade.get('username','?'):<20} "
                f"{kalshi_ticker} {kalshi_side.upper()} "
                f"{count} contracts @ {price_cts}¢  "
                f"(${cost_usd:.2f}, win_rate={win_rate:.0%})"
            )

        except Exception as e:
            log.error(f"Order failed {kalshi_ticker}: {e}")
            log_event("error", {
                "action":      "kalshi_copy_buy",
                "kalshi_tick": kalshi_ticker,
                "trader":      trade.get("address", ""),
                "error":       str(e),
            })

    # ── Main poll ─────────────────────────────────────────────────────────────

    def poll(self):
        cfg = load_config()
        if not self._within_daily_limits(cfg):
            return

        followed = self.analyzer.get_followed_addresses()
        if not followed:
            log.warning("No followed traders yet (analysis still building).")
            return

        for address in followed:
            since = self.last_checked.get(address, time.time() - 300)
            self.last_checked[address] = time.time()

            try:
                new_buys = self.analyzer.get_recent_buys(address, since)
            except Exception as e:
                log.debug(f"Error fetching trades for {address[:10]}: {e}")
                continue

            for trade in new_buys:
                if not self._within_daily_limits(cfg):
                    return

                slug    = (trade.get("market_slug") or trade.get("slug") or
                           trade.get("conduit_id") or "")
                outcome = (trade.get("outcome") or trade.get("side_name") or "YES").upper()
                title   = trade.get("title") or trade.get("question") or ""
                end_iso = trade.get("end_date_iso") or trade.get("end_date") or ""

                if not title and slug:
                    mkt = self.analyzer.get_market_info(slug)
                    title   = mkt.get("question") or mkt.get("title") or slug
                    end_iso = mkt.get("end_date_iso") or mkt.get("end_time") or ""
                    tags    = mkt.get("tags") or []
                else:
                    mkt  = {}
                    tags = []

                parent_cat, sub_cat = detect_category(title, tags)
                subjects            = extract_subjects(title, sub_cat)

                # Build enriched trade dict for logging
                trade_info = {
                    "address":  address,
                    "username": trade.get("pseudonym") or trade.get("username") or address[:10],
                    "slug":     slug,
                    "title":    title,
                    "outcome":  outcome,
                    "category": sub_cat,
                }

                # Accuracy gate
                passes, win_rate = self._passes_filter(
                    address, parent_cat, sub_cat, subjects, cfg
                )
                if not passes:
                    log.debug(
                        f"Skip {trade_info['username']} {sub_cat} — "
                        f"win rate {win_rate:.0%} below threshold"
                    )
                    continue

                # Find matching Kalshi market
                match = self.matcher.match(title, outcome, end_iso)
                if not match:
                    log.debug(f"No Kalshi match for: '{title[:50]}'")
                    continue

                kalshi_ticker, kalshi_side = match
                self._execute_copy(trade_info, kalshi_ticker, kalshi_side, win_rate, cfg)

    # ── Cloud dashboard sync ──────────────────────────────────────────────────

    def _sync_cloud(self, final: bool = False):
        if not LICENSE_KEY or not CLOUD_URL:
            return
        try:
            # Collect recent log entries to upload
            trades_to_sync = []
            if LOG_FILE.exists():
                lines = LOG_FILE.read_text().strip().splitlines()
                for line in lines[-50:]:    # last 50 events
                    try:
                        trades_to_sync.append(json.loads(line))
                    except Exception:
                        pass

            requests.post(
                f"{CLOUD_URL}/api/sync",
                headers={"X-License-Key": LICENSE_KEY},
                json={
                    "running":  not final,
                    "version":  "kalshi-copycat-1.0",
                    "trades":   trades_to_sync,
                },
                timeout=10,
            )
        except Exception as e:
            log.debug(f"Cloud sync failed: {e}")

    # ── Run loop ──────────────────────────────────────────────────────────────

    def run(self):
        log.info("═" * 60)
        log.info("Kalshi CopyCat — Polymarket intelligence → Kalshi execution")
        log.info("Press Ctrl-C to stop")
        log.info("═" * 60)

        last_sync   = 0.0
        last_rebuild = 0.0

        # Kick off initial category ranking build in-band
        # (takes a few minutes on first run; subsequent runs use cache)
        log.info("Building initial category rankings (this takes a few minutes on first run)…")
        try:
            self.analyzer.build_category_rankings()
        except Exception as e:
            log.warning(f"Initial ranking build error: {e}")

        while self.running:
            loop_start = time.time()

            # Rebuild rankings every 6 h
            if time.time() - last_rebuild >= 6 * 3600:
                try:
                    self.analyzer.build_category_rankings()
                    last_rebuild = time.time()
                except Exception as e:
                    log.warning(f"Rankings rebuild error: {e}")

            # Poll for new trades
            try:
                self.poll()
            except Exception as e:
                log.error(f"Poll error: {e}")

            # Sync to cloud every 30 s
            if time.time() - last_sync >= 30:
                self._sync_cloud()
                last_sync = time.time()

            # Sleep remainder of interval
            elapsed = time.time() - loop_start
            sleep_for = max(0, POLL_SECS - elapsed)
            if sleep_for > 0:
                time.sleep(sleep_for)


# ─── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        log.info(f"Created default config → {CONFIG_FILE}")

    bot = KalshiCopyCat()
    bot.run()
