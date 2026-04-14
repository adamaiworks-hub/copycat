#!/usr/bin/env python3
"""
Polymarket Trader Analyzer for Kalshi CopyCat
──────────────────────────────────────────────
• Fetches top 150 Polymarket traders from Gamma leaderboard
• For each trader, pulls their resolved trade history
• Calculates win rate per category → subcategory (team / coin / topic)
• Ranks top 20 traders per category
• Caches everything in SQLite (rebuilt every 6 hours)
"""

import json
import re
import sqlite3
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("kalshi-copycat.analyzer")

GAMMA     = "https://gamma-api.polymarket.com"
DATA_API  = "https://data-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

CACHE_DB    = Path("kalshi_copycat_cache.db")
CACHE_TTL   = 6 * 3600    # rebuild rankings every 6 hours
MARKET_TTL  = 3600        # cache market metadata 1 hour
TOP_PER_CAT = 20          # top traders per category
POOL_SIZE   = 150         # traders pulled from leaderboard for analysis
MIN_TRADES  = 5           # minimum resolved trades to count a category stat

# ─── Category detection ────────────────────────────────────────────────────────

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "NBA":         ["nba","lakers","celtics","warriors","bulls","heat","knicks","bucks",
                    "76ers","nuggets","suns","clippers","nets","hawks","mavericks","mavs",
                    "spurs","rockets","pistons","wizards","cavaliers","cavs","pacers",
                    "raptors","magic","hornets","thunder","timberwolves","jazz",
                    "trail blazers","grizzlies","pelicans","kings","basketball"],
    "NFL":         ["nfl","patriots","cowboys","packers","49ers","chiefs","ravens","bills",
                    "bengals","dolphins","broncos","raiders","chargers","seahawks","rams",
                    "cardinals","falcons","saints","buccaneers","panthers","bears","lions",
                    "vikings","colts","titans","jaguars","texans","steelers","giants",
                    "eagles","commanders","jets","football","super bowl"],
    "MLB":         ["mlb","yankees","red sox","dodgers","cubs","cardinals","giants","mets",
                    "phillies","braves","astros","rangers","baseball","world series"],
    "NHL":         ["nhl","bruins","maple leafs","canadiens","blackhawks","penguins",
                    "capitals","lightning","flyers","hockey","stanley cup"],
    "Soccer":      ["soccer","premier league","champions league","la liga","mls","world cup",
                    "arsenal","chelsea","manchester","real madrid","barcelona","liverpool",
                    "bundesliga","serie a","ligue 1"],
    "Tennis":      ["tennis","wimbledon","french open","australian open","us open tennis",
                    "djokovic","alcaraz","swiatek","medvedev"],
    "MMA/UFC":     ["ufc","mma","boxing","fight","knockout","championship fight","bellator"],
    "Golf":        ["golf","pga","masters","ryder cup","the open championship"],
    "F1":          ["formula 1","f1","grand prix","verstappen","ferrari","mercedes f1"],
    "Bitcoin":     ["bitcoin","btc"],
    "Ethereum":    ["ethereum","eth"],
    "Solana":      ["solana","sol "],
    "XRP":         ["xrp","ripple"],
    "DOGE":        ["dogecoin","doge"],
    "Crypto":      ["crypto","defi","blockchain","token","binance","bnb","cardano","ada",
                    "avax","avalanche","matic","polygon","chainlink","link","pepe","shib"],
    "US Politics": ["trump","biden","harris","election","senate","congress","president",
                    "republican","democrat","white house","supreme court","vote","ballot"],
    "International":["putin","zelensky","ukraine","russia","china","nato","geopolit",
                     "uk election","macron","modi","netanyahu"],
    "Economics":   ["inflation","gdp","recession","interest rate","unemployment","cpi",
                    "federal reserve","fed rate","s&p 500","dow jones","nasdaq","stock market"],
    "Entertainment":["oscars","grammy","emmy","box office","taylor swift","beyonce"],
}

PARENT_CATEGORY: dict[str, str] = {
    "NBA":"Sports","NFL":"Sports","MLB":"Sports","NHL":"Sports",
    "Soccer":"Sports","Tennis":"Sports","MMA/UFC":"Sports","Golf":"Sports","F1":"Sports",
    "Bitcoin":"Crypto","Ethereum":"Crypto","Solana":"Crypto","XRP":"Crypto","DOGE":"Crypto",
    "Crypto":"Crypto",
    "US Politics":"Politics","International":"Politics",
    "Economics":"Economics","Entertainment":"Entertainment",
}


def detect_category(title: str, tags: list | None = None) -> tuple[str, str]:
    """Returns (parent_category, subcategory). E.g. ('Sports', 'NBA')"""
    combined = (title or "").lower() + " " + " ".join(tags or []).lower()
    best, best_n = "Other", 0
    for cat, kws in CATEGORY_KEYWORDS.items():
        n = sum(1 for kw in kws if kw in combined)
        if n > best_n:
            best_n, best = n, cat
    return PARENT_CATEGORY.get(best, "Other"), best


def extract_subjects(title: str, subcategory: str) -> list[str]:
    """Extract team names / coin names from market title for sub-category PnL."""
    if subcategory in ("Bitcoin","Ethereum","Solana","XRP","DOGE"):
        return [subcategory]
    if PARENT_CATEGORY.get(subcategory) == "Crypto":
        for kw in ["bitcoin","btc","ethereum","eth","solana","sol","xrp","doge","bnb"]:
            if kw in title.lower():
                return [kw.upper()]
    if PARENT_CATEGORY.get(subcategory) == "Sports":
        m = re.search(r'([A-Z][a-zA-Z ]{2,22}?)\s+(?:vs?\.?|@|versus)\s+([A-Z][a-zA-Z ]{2,22})', title)
        if m:
            return [m.group(1).strip(), m.group(2).strip()]
        m = re.search(r'[Ww]ill\s+(?:the\s+)?([A-Z][a-zA-Z ]{2,22}?)\s+(?:win|beat|make)', title)
        if m:
            return [m.group(1).strip()]
    return []


# ─── SQLite cache ──────────────────────────────────────────────────────────────

def init_cache():
    con = sqlite3.connect(CACHE_DB)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS trader_profiles (
            address    TEXT PRIMARY KEY,
            username   TEXT,
            data_json  TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS category_rankings (
            category   TEXT PRIMARY KEY,
            data_json  TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS market_meta (
            slug       TEXT PRIMARY KEY,
            data_json  TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
    """)
    con.commit()
    con.close()


# ─── Polymarket API helpers ────────────────────────────────────────────────────

_session = requests.Session()
_session.headers["User-Agent"] = "CopyCat/1.0"


def _gamma(path: str, **params) -> list | dict:
    try:
        r = _session.get(f"{GAMMA}{path}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug(f"Gamma {path}: {e}")
        return []


def _data(path: str, **params) -> list | dict:
    try:
        r = _session.get(f"{DATA_API}{path}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.debug(f"DataAPI {path}: {e}")
        return []


def _clob(path: str, **params) -> list | dict:
    try:
        r = _session.get(f"{CLOB_API}{path}", params=params, timeout=15)
        r.raise_for_status()
        d = r.json()
        return d.get("data", d) if isinstance(d, dict) else d
    except Exception as e:
        log.debug(f"CLOB {path}: {e}")
        return []


# ─── Main analyzer class ───────────────────────────────────────────────────────

class TraderAnalyzer:
    def __init__(self):
        init_cache()
        self._category_cache: dict = {}
        self._profile_cache:  dict = {}
        self._last_build:     float = 0.0

    # ── Leaderboard ───────────────────────────────────────────────────────────

    def fetch_leaderboard(self, limit: int = POOL_SIZE) -> list[dict]:
        data = _gamma("/leaderboard", limit=limit, period="weekly")
        if not data:
            return []
        traders = data if isinstance(data, list) else data.get("data", data.get("traders", []))
        return [t for t in traders if float(t.get("volume") or 0) > 0]

    # ── Market metadata ───────────────────────────────────────────────────────

    def get_market_info(self, slug: str) -> dict:
        if not slug:
            return {}
        con = sqlite3.connect(CACHE_DB)
        row = con.execute(
            "SELECT data_json, updated_at FROM market_meta WHERE slug=?", (slug,)
        ).fetchone()
        con.close()
        if row and (time.time() - row[1]) < MARKET_TTL:
            return json.loads(row[0])

        data = _gamma("/markets", slug=slug)
        if not data:
            return {}
        market = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
        if market:
            con = sqlite3.connect(CACHE_DB)
            con.execute(
                "INSERT OR REPLACE INTO market_meta (slug, data_json, updated_at) VALUES (?,?,?)",
                (slug, json.dumps(market), time.time())
            )
            con.commit()
            con.close()
        return market

    # ── User trade history ────────────────────────────────────────────────────

    def fetch_user_trades(self, address: str, limit: int = 500) -> list[dict]:
        """Pull a trader's trade history; try multiple API sources."""
        # 1. Polymarket data-api activity feed
        trades = _data("/activity", user=address, limit=limit)
        if isinstance(trades, list) and trades:
            return trades
        # 2. CLOB trades endpoint
        trades = _clob("/trades", maker_address=address, limit=limit)
        if isinstance(trades, list) and trades:
            return trades
        # 3. Gamma positions (less info but available)
        return []

    # ── Win rate analysis ─────────────────────────────────────────────────────

    def calculate_trader_stats(self, address: str, username: str = "",
                                polymarket_pnl: float = 0.0) -> dict:
        """
        Full profile with win rates per parent_category → subcategory → subject.
        Uses SQLite cache; only recomputes when stale.
        """
        con = sqlite3.connect(CACHE_DB)
        row = con.execute(
            "SELECT data_json, updated_at FROM trader_profiles WHERE address=?", (address,)
        ).fetchone()
        con.close()
        if row and (time.time() - row[1]) < CACHE_TTL:
            return json.loads(row[0])

        log.info(f"  Analyzing {username or address[:12]}…")
        raw_trades = self.fetch_user_trades(address, limit=500)

        # stats[parent_cat][sub_cat] = {trades, wins, pnl, subcats:{subject:{trades,wins,pnl}}}
        stats: dict = {}
        total_resolved = total_wins = 0
        total_pnl = 0.0

        for raw in raw_trades:
            action = (raw.get("action") or raw.get("side") or raw.get("type") or "").upper()
            if action not in ("BUY", "B", "MARKET_BUY"):
                continue

            slug    = (raw.get("market_slug") or raw.get("slug") or
                       raw.get("conduit_id") or raw.get("market") or "")
            outcome = (raw.get("outcome") or raw.get("side_name") or
                       raw.get("asset_id", "")[-3:] or "YES").upper()
            if outcome not in ("YES","NO","Y","N"):
                outcome = "YES"
            outcome = "YES" if outcome in ("YES","Y") else "NO"

            price   = float(raw.get("price") or raw.get("yes_price") or 0.5)
            size_usd= float(raw.get("usdcSize") or raw.get("amount") or 0)
            shares  = float(raw.get("size") or raw.get("shares") or 0)
            title   = raw.get("title") or raw.get("question") or ""

            if not slug:
                continue

            mkt = self.get_market_info(slug)
            resolution = (mkt.get("resolution") or "").upper().strip()
            if not resolution or resolution in ("", "N/A", "PENDING"):
                continue  # skip unresolved

            if not title:
                title = mkt.get("question") or mkt.get("title") or ""
            tags = mkt.get("tags") or mkt.get("categories") or []

            won = (outcome == resolution) or (outcome == "YES" and resolution in ("YES","Y","1","TRUE"))

            # PnL estimate: contracts × (1-price) if win, else -contracts × price
            if shares > 0:
                pnl = shares * (1 - price) if won else -shares * price
            elif size_usd > 0 and price > 0:
                shares_est = size_usd / price
                pnl = size_usd * (1/price - 1) if won else -size_usd
            else:
                pnl = 0.0

            parent_cat, sub_cat = detect_category(title, tags)
            subjects = extract_subjects(title, sub_cat)

            total_resolved += 1
            total_wins += int(won)
            total_pnl += pnl

            # Roll up: stats[parent][sub]
            sub_data = (stats
                        .setdefault(parent_cat, {})
                        .setdefault(sub_cat, {"trades":0,"wins":0,"pnl":0.0,"subcats":{}}))
            sub_data["trades"] += 1
            sub_data["wins"]   += int(won)
            sub_data["pnl"]    += pnl

            for subj in subjects:
                sd = sub_data["subcats"].setdefault(subj, {"trades":0,"wins":0,"pnl":0.0})
                sd["trades"] += 1
                sd["wins"]   += int(won)
                sd["pnl"]    += pnl

        def _add_rates(level: dict):
            for v in level.values():
                if not isinstance(v, dict) or "trades" not in v:
                    continue
                t = v["trades"]
                v["win_rate"] = round(v["wins"] / t, 4) if t else 0.0
                v["pnl"]      = round(v["pnl"], 2)
                if "subcats" in v:
                    _add_rates(v["subcats"])

        for cat_block in stats.values():
            _add_rates(cat_block)

        profile = {
            "address":          address,
            "username":         username,
            "overall_win_rate": round(total_wins / total_resolved, 4) if total_resolved else 0.0,
            "total_trades":     total_resolved,
            "total_pnl":        round(total_pnl, 2),
            "polymarket_pnl":   round(polymarket_pnl, 2),
            "categories":       stats,
            "analyzed_at":      datetime.now(timezone.utc).isoformat(),
        }

        con = sqlite3.connect(CACHE_DB)
        con.execute(
            "INSERT OR REPLACE INTO trader_profiles "
            "(address, username, data_json, updated_at) VALUES (?,?,?,?)",
            (address, username, json.dumps(profile), time.time())
        )
        con.commit()
        con.close()

        return profile

    # ── Category rankings ─────────────────────────────────────────────────────

    def build_category_rankings(self) -> dict:
        """
        Fetches top POOL_SIZE traders, analyzes each, returns
        {subcategory: [top_20_traders sorted by cat win_rate]}.
        """
        if time.time() - self._last_build < CACHE_TTL and self._category_cache:
            return self._category_cache

        log.info(f"Building category rankings (pool = {POOL_SIZE} traders)…")
        leaderboard = self.fetch_leaderboard(POOL_SIZE)
        if not leaderboard:
            log.warning("Empty leaderboard — retaining stale cache.")
            return self._category_cache

        rankings: dict[str, list] = {}

        for i, trader in enumerate(leaderboard):
            address  = (trader.get("address") or "").lower()
            username = (trader.get("pseudonym") or trader.get("username") or
                        address[:10] or f"trader_{i}")
            poly_pnl = float(trader.get("pnl") or 0)
            if not address:
                continue

            try:
                profile = self.calculate_trader_stats(address, username, poly_pnl)
                self._profile_cache[address] = profile
            except Exception as e:
                log.debug(f"Skipping {username}: {e}")
                continue

            for parent_cat, cat_data in profile.get("categories", {}).items():
                for sub_cat, sd in cat_data.items():
                    if sd.get("trades", 0) < MIN_TRADES:
                        continue
                    rankings.setdefault(sub_cat, []).append({
                        "address":    address,
                        "username":   username,
                        "win_rate":   sd["win_rate"],
                        "trades":     sd["trades"],
                        "wins":       sd["wins"],
                        "pnl":        sd["pnl"],
                        "parent_cat": parent_cat,
                        "subcategory":sub_cat,
                        "poly_pnl":   poly_pnl,
                    })

        for cat in list(rankings):
            rankings[cat].sort(key=lambda x: x["win_rate"], reverse=True)
            rankings[cat] = rankings[cat][:TOP_PER_CAT]

        self._category_cache = rankings
        self._last_build = time.time()

        con = sqlite3.connect(CACHE_DB)
        for cat, traders in rankings.items():
            con.execute(
                "INSERT OR REPLACE INTO category_rankings "
                "(category, data_json, updated_at) VALUES (?,?,?)",
                (cat, json.dumps(traders), time.time())
            )
        con.commit()
        con.close()

        cats_summary = {c: len(v) for c, v in rankings.items()}
        log.info(f"Rankings built: {cats_summary}")
        return rankings

    # ── Public helpers ────────────────────────────────────────────────────────

    def get_followed_addresses(self) -> set[str]:
        """All unique addresses being monitored across all categories."""
        rankings = self.build_category_rankings()
        return {t["address"] for traders in rankings.values() for t in traders}

    def get_top_traders(self, category: str | None = None) -> list[dict]:
        rankings = self.build_category_rankings()
        if category:
            return rankings.get(category, [])
        seen, out = set(), []
        for traders in rankings.values():
            for t in traders:
                if t["address"] not in seen:
                    seen.add(t["address"])
                    out.append(t)
        return out

    def get_profile(self, address: str) -> dict:
        """Return cached profile or fetch fresh."""
        address = address.lower()
        if address in self._profile_cache:
            return self._profile_cache[address]
        con = sqlite3.connect(CACHE_DB)
        row = con.execute(
            "SELECT data_json FROM trader_profiles WHERE address=?", (address,)
        ).fetchone()
        con.close()
        if row:
            p = json.loads(row[0])
            self._profile_cache[address] = p
            return p
        return {}

    def get_win_rate(self, address: str, parent_cat: str,
                     sub_cat: str, subject: str | None = None) -> float:
        """Return win rate for a specific category (and optional subject like 'Lakers')."""
        profile = self.get_profile(address)
        if not profile:
            return 0.0
        sub_data = profile.get("categories", {}).get(parent_cat, {}).get(sub_cat, {})
        if not sub_data:
            return 0.0
        if subject:
            subj_data = sub_data.get("subcats", {}).get(subject, {})
            if subj_data and subj_data.get("trades", 0) >= MIN_TRADES:
                return subj_data["win_rate"]
        return sub_data.get("win_rate", 0.0)

    def get_recent_buys(self, address: str, since_ts: float) -> list[dict]:
        """New BUY trades from a trader since `since_ts` (unix timestamp)."""
        raw = self.fetch_user_trades(address, limit=50)
        result = []
        for t in raw:
            action = (t.get("action") or t.get("side") or t.get("type") or "").upper()
            if action not in ("BUY", "B", "MARKET_BUY"):
                continue
            ts_raw = t.get("timestamp") or t.get("created_at") or t.get("ts") or 0
            if isinstance(ts_raw, str):
                try:
                    ts_val = datetime.fromisoformat(
                        ts_raw.replace("Z", "+00:00")
                    ).timestamp()
                except Exception:
                    ts_val = 0.0
            else:
                ts_val = float(ts_raw or 0)
            if ts_val and ts_val > since_ts:
                result.append({**t, "_ts": ts_val})
        return result
