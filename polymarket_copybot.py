#!/usr/bin/env python3
"""
Polymarket Copy Trading Bot
- Follows top 10 traders by weekly PnL
- Mirrors buys immediately; mirrors sells proportionally
- Logs every trade + error to trades_log.jsonl
- Auto-pauses on daily loss limit
"""

import json
import os
import subprocess
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from license import load_license
from bot_config import load_config, market_matches_filters

load_dotenv()

# ─── License check ─────────────────────────────────────────────────────────────

_license = load_license()
if not _license.valid:
    print(f"[CopyCat] No valid license: {_license.error}")
    print("[CopyCat] Enter your license key in the dashboard Settings tab.")

# ─── Configuration (tier-aware) ────────────────────────────────────────────────

def get_daily_loss_limit() -> float:
    cfg = load_config()
    return float(cfg.get("daily_loss_limit", os.getenv("DAILY_LOSS_LIMIT", "100")))
POLL_INTERVAL_SECS = int(os.getenv("POLL_INTERVAL_SECS",   "30"))
TOP_N_TRADERS      = _license.max_traders  # enforced by tier
LOG_FILE           = Path(os.getenv("TRADE_LOG_FILE", "trades_log.jsonl"))

def get_trade_amount_usd() -> float:
    """Read wager from bot_config (live, so dashboard changes take effect without restart)."""
    cfg = load_config()
    lic = load_license()

    if cfg.get("wager_mode") == "pct":
        pct = float(cfg.get("wager_pct", 2))
        pct = min(pct, lic.max_wager_pct)
        # TODO: wire real portfolio balance; for now fall back to fixed
        amount = float(cfg.get("wager_fixed", 10))
    else:
        amount = float(cfg.get("wager_fixed", 10))

    # Clamp to tier limits regardless of config
    amount = max(lic.min_wager, min(lic.max_wager, amount))
    return amount

# ─── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("copycat")

# ─── Trade log (JSONL — one JSON object per line) ──────────────────────────────

def log_event(event_type: str, data: dict):
    """Append a structured event to the JSONL log file."""
    record = {
        "ts":   datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        **data,
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")
    log.debug(f"Logged: {event_type} — {data}")

# ─── Bullpen CLI helpers ────────────────────────────────────────────────────────

def bullpen(args: list[str], timeout: int = 30) -> dict | list | None:
    cmd = ["bullpen"] + args + ["--output", "json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            log.warning(f"bullpen {' '.join(args[:3])} failed: {result.stderr.strip()[:200]}")
            return None
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        log.warning(f"bullpen {' '.join(args[:3])} timed out after {timeout}s")
        return None
    except json.JSONDecodeError as e:
        log.warning(f"JSON parse error on bullpen {' '.join(args[:3])}: {e}")
        return None
    except Exception as e:
        log.warning(f"bullpen error: {e}")
        return None

def get_leaderboard() -> list[dict]:
    data = bullpen(["polymarket", "data", "leaderboard", "--period", "week"])
    if not data:
        return []
    active = [t for t in data if float(t.get("volume") or 0) > 0]
    active.sort(key=lambda t: float(t.get("pnl") or 0), reverse=True)
    return active[:TOP_N_TRADERS]

def get_followed_trades() -> list:
    data = bullpen(["tracker", "trades"])
    if not data:
        return []
    return data.get("trades", data) if isinstance(data, dict) else data

def get_our_positions() -> dict:
    """Returns {slug: {outcome: shares}} for our current open positions."""
    data = bullpen(["polymarket", "positions"])
    if not data:
        return {}
    positions = data.get("positions", data) if isinstance(data, dict) else data
    result = {}
    for p in positions:
        slug    = p.get("market_slug") or p.get("slug", "")
        outcome = p.get("outcome", "Yes")
        shares  = float(p.get("size") or p.get("shares") or 0)
        if slug and shares > 0:
            result.setdefault(slug, {})[outcome] = shares
    return result

def get_daily_pnl() -> float:
    data = bullpen(["portfolio", "pnl"])
    if not data:
        return 0.0
    return float(data.get("today_pnl") or 0)

def place_buy(slug: str, outcome: str, amount_usd: float) -> bool:
    cmd = ["bullpen", "polymarket", "buy", slug, outcome, str(amount_usd), "--yes"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except Exception as e:
        raise RuntimeError(f"buy command failed: {e}")

def place_sell(slug: str, outcome: str, shares: float) -> bool:
    cmd = ["bullpen", "polymarket", "sell", slug, outcome, str(shares), "--yes"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except Exception as e:
        raise RuntimeError(f"sell command failed: {e}")

# ─── CopyCat ──────────────────────────────────────────────────────────────────

class CopyCat:
    def __init__(self):
        self.traders:        list[dict]                    = []
        self.trader_addrs:   set[str]                      = set()
        self.seen_trade_ids: set[str]                      = set()

        # Track each followed trader's cumulative position per market
        # {address: {slug: {outcome: net_shares}}}
        self.trader_positions: dict[str, dict[str, dict[str, float]]] = {}

        self.daily_pnl_baseline: float = 0.0
        self.day_str:            str   = ""
        self.running:            bool  = True

        log.info(f"Trade log → {LOG_FILE.resolve()}")

    # ── Trader list ────────────────────────────────────────────────────────────

    def refresh_traders(self):
        log.info("Refreshing trader list from leaderboard…")
        self.traders = get_leaderboard()
        self.trader_addrs = {t.get("address", "").lower() for t in self.traders}
        if self.traders:
            log.info(f"Now following {len(self.traders)} traders:")
            for t in self.traders:
                log.info(f"  {t.get('username','?'):<30}  PnL=${float(t.get('pnl') or 0):>10,.0f}  Vol=${float(t.get('volume') or 0):>10,.0f}")
        else:
            log.warning("No active traders found — will retry.")

    # ── Daily loss guard ───────────────────────────────────────────────────────

    def within_daily_limit(self) -> bool:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.day_str:
            self.day_str = today
            self.daily_pnl_baseline = get_daily_pnl()
            log.info(f"New day. PnL baseline: ${self.daily_pnl_baseline:.2f}")

        daily_limit = get_daily_loss_limit()
        change = get_daily_pnl() - self.daily_pnl_baseline
        if change <= -daily_limit:
            log.warning(f"Daily loss limit hit (down ${abs(change):.2f}). Pausing.")
            log_event("daily_limit_hit", {"loss": round(abs(change), 2)})
            return False
        return True

    # ── Trade extraction ───────────────────────────────────────────────────────

    def extract_trade(self, trade: dict) -> dict | None:
        """Pull the key fields we care about from a raw trade object."""
        slug    = trade.get("market_slug") or trade.get("slug") or trade.get("conduit_id", "")
        outcome = trade.get("outcome") or trade.get("side_name", "Yes")
        action  = (trade.get("action") or trade.get("side") or "buy").lower()
        shares  = float(trade.get("size") or trade.get("shares") or trade.get("amount") or 0)
        price   = float(trade.get("price") or trade.get("yes_price") or 0)
        addr    = (trade.get("maker") or trade.get("address") or "").lower()
        name    = trade.get("pseudonym") or trade.get("username") or addr[:10]
        tid     = trade.get("id") or trade.get("transaction_hash") or trade.get("trade_id", "")

        if not slug or not tid:
            return None

        return {
            "trade_id": tid,
            "trader":   name,
            "address":  addr,
            "slug":     slug,
            "outcome":  outcome,
            "action":   action,    # "buy" or "sell"
            "shares":   shares,
            "price":    price,
        }

    # ── Position tracking for followed traders ─────────────────────────────────

    def update_trader_position(self, address: str, slug: str, outcome: str, action: str, shares: float):
        pos = self.trader_positions.setdefault(address, {}).setdefault(slug, {})
        held = pos.get(outcome, 0.0)
        if action == "buy":
            pos[outcome] = held + shares
        else:
            pos[outcome] = max(0.0, held - shares)

    def get_trader_held_shares(self, address: str, slug: str, outcome: str) -> float:
        return self.trader_positions.get(address, {}).get(slug, {}).get(outcome, 0.0)

    # ── Execute a copy trade ───────────────────────────────────────────────────

    def copy_trade(self, t: dict):
        slug    = t["slug"]
        outcome = t["outcome"]
        action  = t["action"]
        trader  = t["trader"]

        # ── BUY: wager from config ─────────────────────────────────────────────
        if action == "buy":
            trade_usd = get_trade_amount_usd()
            log.info(f"COPY BUY  | {trader} | {slug} | {outcome} | ${trade_usd}")
            try:
                success = place_buy(slug, outcome, trade_usd)
                log_event("copy_buy", {
                    "trader":  trader,
                    "slug":    slug,
                    "outcome": outcome,
                    "usd":     trade_usd,
                    "success": success,
                })
                if not success:
                    log.warning(f"Buy failed: {slug} {outcome}")
            except Exception as e:
                log.error(f"Buy error: {e}")
                log_event("error", {"action": "buy", "slug": slug, "outcome": outcome, "error": str(e)})

        # ── SELL: proportional ─────────────────────────────────────────────────
        elif action == "sell":
            trader_held_before = self.get_trader_held_shares(t["address"], slug, outcome)
            if trader_held_before <= 0:
                # No history — treat as full exit
                sell_pct = 1.0
            else:
                sell_pct = min(1.0, t["shares"] / trader_held_before)

            # Look up our current position in this market
            our_positions = get_our_positions()
            our_shares = our_positions.get(slug, {}).get(outcome, 0.0)

            if our_shares <= 0:
                log.info(f"SKIP SELL | {trader} | {slug} | {outcome} — we hold no position")
                return

            shares_to_sell = round(our_shares * sell_pct, 2)
            if shares_to_sell <= 0:
                return

            log.info(f"COPY SELL | {trader} | {slug} | {outcome} | {shares_to_sell} shares ({sell_pct*100:.0f}% of our {our_shares})")
            try:
                success = place_sell(slug, outcome, shares_to_sell)
                log_event("copy_sell", {
                    "trader":       trader,
                    "slug":         slug,
                    "outcome":      outcome,
                    "shares":       shares_to_sell,
                    "sell_pct":     round(sell_pct, 4),
                    "success":      success,
                })
                if not success:
                    log.warning(f"Sell failed: {slug} {outcome}")
            except Exception as e:
                log.error(f"Sell error: {e}")
                log_event("error", {"action": "sell", "slug": slug, "outcome": outcome, "error": str(e)})

    # ── Main poll cycle ────────────────────────────────────────────────────────

    def poll(self):
        raw_trades = get_followed_trades()
        if not raw_trades:
            return

        # Read config fresh each cycle — reflects dashboard changes immediately
        cfg = load_config()
        selected_traders = set(a.lower() for a in cfg.get("selected_traders", []))

        new_count = 0
        for raw in raw_trades:
            t = self.extract_trade(raw)
            if not t:
                continue

            # Skip if already processed
            if t["trade_id"] in self.seen_trade_ids:
                self.update_trader_position(t["address"], t["slug"], t["outcome"], t["action"], t["shares"])
                continue

            # Skip trades from traders we're not following (leaderboard filter)
            if t["address"] and t["address"] not in self.trader_addrs:
                continue

            # Skip trades from traders not in the user's Pro selection (if any selected)
            if selected_traders and t["address"] not in selected_traders:
                continue

            # Skip markets that don't match the user's category filters
            if not market_matches_filters(t["slug"], t.get("title", ""), cfg):
                log.debug(f"SKIP (category filter) | {t['slug']}")
                self.seen_trade_ids.add(t["trade_id"])
                self.update_trader_position(t["address"], t["slug"], t["outcome"], t["action"], t["shares"])
                continue

            # Skip stale trades (older than 2× poll interval)
            ts_raw = raw.get("timestamp") or raw.get("created_at", "")
            if ts_raw:
                try:
                    trade_time = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - trade_time).total_seconds()
                    if age > POLL_INTERVAL_SECS * 2:
                        self.seen_trade_ids.add(t["trade_id"])
                        self.update_trader_position(t["address"], t["slug"], t["outcome"], t["action"], t["shares"])
                        continue
                except Exception:
                    pass

            self.seen_trade_ids.add(t["trade_id"])
            self.update_trader_position(t["address"], t["slug"], t["outcome"], t["action"], t["shares"])

            # Log the observed trade
            log_event("observed_trade", {k: v for k, v in t.items() if k != "trade_id"})

            # Copy it
            try:
                self.copy_trade(t)
                new_count += 1
            except Exception as e:
                log.error(f"Unexpected error copying trade {t['trade_id']}: {e}")
                log_event("error", {"trade_id": t["trade_id"], "error": str(e)})

        if new_count:
            log.info(f"Copied {new_count} new trade(s) this cycle.")

    # ── Run loop ───────────────────────────────────────────────────────────────

    def run(self):
        log.info("Polymarket CopyCat starting…")
        amt = get_trade_amount_usd()
        log.info(f"Config: amount=${amt}  daily_limit=-${DAILY_LOSS_LIMIT}  poll={POLL_INTERVAL_SECS}s  traders={TOP_N_TRADERS}")
        log_event("bot_start", {"amount_usd": amt, "daily_loss_limit": DAILY_LOSS_LIMIT})

        self.refresh_traders()
        cycle = 0

        while self.running:
            try:
                cycle += 1

                # Refresh leaderboard every hour
                if cycle % (3600 // POLL_INTERVAL_SECS) == 0:
                    self.refresh_traders()

                if not self.traders:
                    log.warning("No traders loaded — sleeping.")
                    time.sleep(POLL_INTERVAL_SECS)
                    continue

                if not self.within_daily_limit():
                    time.sleep(300)
                    continue

                self.poll()

            except KeyboardInterrupt:
                log.info("Stopped by user.")
                log_event("bot_stop", {"reason": "keyboard_interrupt"})
                self.running = False
                break
            except Exception as e:
                # Log the error but keep looping — never crash
                log.error(f"Unhandled error in main loop: {e}", exc_info=True)
                log_event("error", {"context": "main_loop", "error": str(e)})

            time.sleep(POLL_INTERVAL_SECS)


# ─── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    CopyCat().run()
