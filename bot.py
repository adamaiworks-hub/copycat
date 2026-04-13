#!/usr/bin/env python3
"""
Kalshi Market Making Bot
Automatically quotes bid/ask spreads across all liquid markets.
"""

import os
import time
import uuid
import base64
import logging
import signal
import sys
from dataclasses import dataclass, field
from typing import Optional

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv

load_dotenv()

# ─── Configuration ─────────────────────────────────────────────────────────────

API_KEY_ID       = os.getenv("KALSHI_API_KEY_ID", "")
PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "private_key.pem")
BASE_URL         = "https://trading-api.kalshi.com/trade-api/v2"

SPREAD_CENTS     = int(os.getenv("SPREAD_CENTS",    "4"))    # minimum spread to quote into (¢)
HALF_SPREAD      = SPREAD_CENTS // 2                         # each side of mid
QUOTE_SIZE       = int(os.getenv("QUOTE_SIZE",      "5"))    # contracts per quote
MAX_POS_PER_MKT  = int(os.getenv("MAX_POS_PER_MKT", "50"))  # max net position per market
MAX_TOTAL_USD    = float(os.getenv("MAX_TOTAL_USD",  "500")) # max total $ deployed
MIN_VOLUME       = int(os.getenv("MIN_VOLUME",       "1000"))# min 24h volume to consider
REFRESH_SECS     = int(os.getenv("REFRESH_SECS",     "30"))  # seconds between cycles
STALE_SECS       = int(os.getenv("STALE_SECS",       "120")) # cancel quotes older than this

# ─── Auth / Signing ────────────────────────────────────────────────────────────

def load_private_key():
    with open(PRIVATE_KEY_PATH, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)

_private_key = None

def get_private_key():
    global _private_key
    if _private_key is None:
        _private_key = load_private_key()
    return _private_key

def sign(method: str, path: str) -> tuple[str, str]:
    """Returns (timestamp_ms_str, base64_signature)."""
    ts = str(int(time.time() * 1000))
    message = (ts + method.upper() + path).encode()
    sig = get_private_key().sign(message, padding.PKCS1v15(), hashes.SHA256())
    return ts, base64.b64encode(sig).decode()

# ─── API Client ────────────────────────────────────────────────────────────────

class KalshiClient:
    def __init__(self):
        self.session = requests.Session()

    def _headers(self, method: str, path: str) -> dict:
        ts, sig = sign(method, path)
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
        )
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict):
        r = self.session.post(
            f"{BASE_URL}{path}",
            headers=self._headers("POST", path),
            json=body,
        )
        r.raise_for_status()
        return r.json()

    def _delete(self, path: str):
        r = self.session.delete(
            f"{BASE_URL}{path}",
            headers=self._headers("DELETE", path),
        )
        r.raise_for_status()
        return r.json()

    # ── Portfolio ──────────────────────────────────────────────────────────────

    def get_balance(self) -> float:
        data = self._get("/portfolio/balance")
        return data["balance"]["available_balance"] / 100  # cents → dollars

    def get_positions(self) -> dict:
        data = self._get("/portfolio/positions")
        return {p["ticker"]: p["position"] for p in data.get("market_positions", [])}

    def get_open_orders(self) -> list:
        data = self._get("/portfolio/orders", status="resting")
        return data.get("orders", [])

    # ── Markets ────────────────────────────────────────────────────────────────

    def get_markets(self, limit=200) -> list:
        data = self._get("/markets", status="open", limit=limit)
        return data.get("markets", [])

    def get_orderbook(self, ticker: str) -> dict:
        return self._get(f"/markets/{ticker}/orderbook")

    # ── Orders ─────────────────────────────────────────────────────────────────

    def place_limit_order(self, ticker: str, side: str, action: str, price: int, count: int) -> str:
        """Returns order_id. side='yes'|'no', action='buy'|'sell', price=1-99 cents."""
        body = {
            "ticker":           ticker,
            "client_order_id":  str(uuid.uuid4()),
            "side":             side,
            "action":           action,
            "count":            count,
            "type":             "limit",
            "yes_price":        price,
        }
        resp = self._post("/portfolio/orders", body)
        return resp["order"]["id"]

    def cancel_order(self, order_id: str):
        self._delete(f"/portfolio/orders/{order_id}")

# ─── Market Maker ──────────────────────────────────────────────────────────────

log = logging.getLogger("kalshi-mm")

@dataclass
class Quote:
    order_id:   str
    ticker:     str
    action:     str   # 'buy' or 'sell'
    price:      int
    placed_at:  float = field(default_factory=time.time)


class MarketMaker:
    def __init__(self, client: KalshiClient):
        self.client = client
        # ticker → [buy_quote, sell_quote]
        self.active_quotes: dict[str, list[Quote]] = {}
        self.running = True

        # Graceful shutdown on Ctrl-C or SIGTERM
        signal.signal(signal.SIGINT,  self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def _shutdown(self, *_):
        log.info("Shutdown signal received — cancelling all open quotes.")
        self.running = False
        self._cancel_all()
        sys.exit(0)

    # ── Orderbook helpers ──────────────────────────────────────────────────────

    def best_bid_ask(self, ob: dict) -> tuple[Optional[int], Optional[int]]:
        """
        Kalshi orderbooks: 'yes' list = YES bids (descending), 'no' list = NO bids (descending).
        A NO bid at price P is equivalent to a YES ask at (100 - P).
        """
        yes_bids = ob.get("orderbook", {}).get("yes", [])
        no_bids  = ob.get("orderbook", {}).get("no",  [])
        best_bid = yes_bids[0][0] if yes_bids else None
        best_ask = (100 - no_bids[0][0]) if no_bids else None
        return best_bid, best_ask

    # ── Quote pricing with inventory skew ──────────────────────────────────────

    def compute_quotes(self, best_bid: int, best_ask: int, net_pos: int) -> tuple[int, int]:
        """
        Centers quotes at mid and applies a gentle skew based on current position:
        - Long position → lower our bid, raise our ask (we want to sell, not buy more)
        - Short position → raise our bid, lower our ask
        """
        mid = (best_bid + best_ask) / 2
        skew = net_pos * 0.15  # 0.15¢ per contract of inventory

        buy_price  = round(mid - HALF_SPREAD - skew)
        sell_price = round(mid + HALF_SPREAD - skew)

        buy_price  = max(1,  min(98, buy_price))
        sell_price = max(2,  min(99, sell_price))

        if buy_price >= sell_price:
            buy_price = sell_price - 1

        return buy_price, sell_price

    # ── Order management ───────────────────────────────────────────────────────

    def _cancel_ticker(self, ticker: str):
        for q in self.active_quotes.pop(ticker, []):
            try:
                self.client.cancel_order(q.order_id)
                log.debug(f"Cancelled {q.action} on {ticker} (order {q.order_id[:8]}…)")
            except Exception as e:
                log.warning(f"Cancel failed ({q.order_id[:8]}…): {e}")

    def _cancel_all(self):
        try:
            for order in self.client.get_open_orders():
                try:
                    self.client.cancel_order(order["id"])
                except Exception as e:
                    log.warning(f"Cancel failed: {e}")
        except Exception as e:
            log.error(f"Could not fetch open orders for cleanup: {e}")
        self.active_quotes.clear()

    def _quotes_stale(self, ticker: str) -> bool:
        qs = self.active_quotes.get(ticker, [])
        if not qs:
            return True
        return time.time() - min(q.placed_at for q in qs) > STALE_SECS

    # ── Main loop ──────────────────────────────────────────────────────────────

    def run(self):
        log.info("Kalshi market maker started.")
        log.info(f"Config: spread={SPREAD_CENTS}¢  size={QUOTE_SIZE}  max_pos={MAX_POS_PER_MKT}  refresh={REFRESH_SECS}s")

        while self.running:
            try:
                self._cycle()
            except Exception as e:
                log.error(f"Cycle error: {e}", exc_info=True)

            time.sleep(REFRESH_SECS)

    def _cycle(self):
        balance = self.client.get_balance()
        log.info(f"── Cycle start  balance=${balance:.2f} ──")

        if balance < 10:
            log.warning("Balance < $10 — pausing quotes.")
            return

        markets   = self.client.get_markets(limit=200)
        positions = self.client.get_positions()

        # Total $ deployed = sum of YES contracts held * their cost (rough)
        total_deployed = sum(abs(v) for v in positions.values()) * 0.50
        if total_deployed >= MAX_TOTAL_USD:
            log.warning(f"Total deployed ${total_deployed:.0f} >= limit ${MAX_TOTAL_USD:.0f} — skipping new quotes.")
            return

        quoted = 0
        for mkt in markets:
            if not self.running:
                break

            ticker = mkt.get("ticker", "")
            volume = mkt.get("volume", 0)
            status = mkt.get("status", "")

            if status != "open" or volume < MIN_VOLUME:
                continue

            net_pos = positions.get(ticker, 0)
            if abs(net_pos) >= MAX_POS_PER_MKT:
                log.info(f"Position cap hit on {ticker} ({net_pos:+d}) — skipping.")
                continue

            try:
                ob = self.client.get_orderbook(ticker)
                best_bid, best_ask = self.best_bid_ask(ob)

                if best_bid is None or best_ask is None:
                    continue

                spread = best_ask - best_bid
                if spread < SPREAD_CENTS:
                    continue  # not enough room to earn

                # Cancel stale quotes before re-quoting
                if self._quotes_stale(ticker):
                    self._cancel_ticker(ticker)

                    buy_p, sell_p = self.compute_quotes(best_bid, best_ask, net_pos)

                    buy_id  = self.client.place_limit_order(ticker, "yes", "buy",  buy_p,  QUOTE_SIZE)
                    sell_id = self.client.place_limit_order(ticker, "yes", "sell", sell_p, QUOTE_SIZE)

                    self.active_quotes[ticker] = [
                        Quote(buy_id,  ticker, "buy",  buy_p),
                        Quote(sell_id, ticker, "sell", sell_p),
                    ]

                    log.info(f"  {ticker:<40} bid={buy_p:2d}¢  ask={sell_p:2d}¢  mkt_spread={spread:2d}¢  pos={net_pos:+d}")
                    quoted += 1

            except requests.HTTPError as e:
                log.warning(f"  HTTP {e.response.status_code} on {ticker}: {e.response.text[:120]}")
            except Exception as e:
                log.warning(f"  Error on {ticker}: {e}")

        log.info(f"── Cycle done   quoted={quoted} markets ──")


# ─── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    if not API_KEY_ID:
        sys.exit("ERROR: Set KALSHI_API_KEY_ID in your .env file.")
    if not os.path.exists(PRIVATE_KEY_PATH):
        sys.exit(f"ERROR: Private key not found at '{PRIVATE_KEY_PATH}'. Set KALSHI_PRIVATE_KEY_PATH in .env.")

    client = KalshiClient()
    MarketMaker(client).run()
