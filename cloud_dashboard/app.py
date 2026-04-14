#!/usr/bin/env python3
"""
CopyCat Cloud Dashboard — Flask backend
Accessible from any device. Login with license key.
Bot syncs trades and heartbeat here automatically.
"""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

import requests as http
from flask import Flask, jsonify, request, make_response, send_from_directory

app = Flask(__name__, static_folder="static")

SECRET      = os.environ.get("SESSION_SECRET", "copycat-cloud-2026")
DB_PATH     = Path(os.environ.get("DB_PATH", "cloud.db"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://copycat-production-655e.up.railway.app")
ADMIN_KEY   = os.environ.get("ADMIN_KEY", "copycat-admin-2026")

TIER_LABELS = {
    "MONTHLY":           "CopyCat Monthly",
    "PRO":               "CopyCat Pro",
    "PRO_KALSHI":        "CopyCat Pro + Kalshi",
    "KALSHI_STANDALONE": "Kalshi Bot",
    "TRIAL":             "Trial",
}
TIER_LIMITS = {
    "MONTHLY":           {"max_traders":5,  "custom_sizing":True, "min_wager":5,  "max_wager":100,   "max_wager_pct":20,  "pro_analytics":False,"kalshi_bot":False},
    "PRO":               {"max_traders":20, "custom_sizing":True, "min_wager":5,  "max_wager":10000, "max_wager_pct":100, "pro_analytics":True, "kalshi_bot":False},
    "PRO_KALSHI":        {"max_traders":20, "custom_sizing":True, "min_wager":5,  "max_wager":10000, "max_wager_pct":100, "pro_analytics":True, "kalshi_bot":True},
    "KALSHI_STANDALONE": {"max_traders":5,  "custom_sizing":True, "min_wager":5,  "max_wager":10000, "max_wager_pct":100, "pro_analytics":False,"kalshi_bot":True},
    "TRIAL":             {"max_traders":3,  "custom_sizing":False,"min_wager":5,  "max_wager":10,    "max_wager_pct":5,   "pro_analytics":False,"kalshi_bot":False},
}

# ─── Database ──────────────────────────────────────────────────────────────────

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            token       TEXT PRIMARY KEY,
            license_key TEXT NOT NULL,
            tier        TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT NOT NULL,
            ts          TEXT NOT NULL,
            type        TEXT NOT NULL,
            slug        TEXT,
            outcome     TEXT,
            price       REAL,
            amount      REAL,
            success     INTEGER DEFAULT 1,
            UNIQUE(license_key, ts, type, slug)
        );
        CREATE TABLE IF NOT EXISTS heartbeats (
            license_key TEXT PRIMARY KEY,
            last_seen   TEXT NOT NULL,
            running     INTEGER DEFAULT 0,
            version     TEXT
        );
        CREATE TABLE IF NOT EXISTS configs (
            license_key TEXT PRIMARY KEY,
            config_json TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
    """)
    con.commit()
    con.close()

def get_db():
    return sqlite3.connect(DB_PATH)

# ─── Auth helpers ──────────────────────────────────────────────────────────────

def make_token(key: str) -> str:
    return hashlib.sha256(f"{key}:{SECRET}".encode()).hexdigest()

def validate_key(key: str) -> dict | None:
    if not key or not key.startswith("CB-"):
        return None
    try:
        resp = http.get(
            f"{WEBHOOK_URL}/admin/licenses",
            headers={"X-Admin-Key": ADMIN_KEY},
            timeout=8,
        )
        if resp.ok:
            for lic in resp.json():
                if lic["key"] == key:
                    tier = lic.get("tier", "MONTHLY")
                    limits = TIER_LIMITS.get(tier, TIER_LIMITS["MONTHLY"])
                    return {"key": key, "tier": tier, "label": TIER_LABELS.get(tier, tier), **limits}
    except Exception:
        pass
    # Fallback: accept any CB-TIER-HEX key
    parts = key.split("-")
    if len(parts) >= 3:
        tier = parts[1]
        limits = TIER_LIMITS.get(tier, TIER_LIMITS["MONTHLY"])
        return {"key": key, "tier": tier, "label": TIER_LABELS.get(tier, tier), **limits}
    return None

def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.cookies.get("cc_token") or request.headers.get("X-Auth-Token", "")
        if not token:
            return jsonify({"error": "unauthorized"}), 401
        con = get_db()
        row = con.execute("SELECT license_key, tier FROM sessions WHERE token=?", (token,)).fetchone()
        con.close()
        if not row:
            return jsonify({"error": "unauthorized"}), 401
        request.license_key = row[0]
        request.tier = row[1]
        return f(*args, **kwargs)
    return wrapper

# ─── Auth endpoints ────────────────────────────────────────────────────────────

@app.post("/api/auth/login")
def login():
    key = (request.json or {}).get("key", "").strip().upper()
    if not key:
        return jsonify({"error": "License key required"}), 400
    lic = validate_key(key)
    if not lic:
        return jsonify({"error": "Invalid license key"}), 401
    token = make_token(key)
    con = get_db()
    con.execute(
        "INSERT OR REPLACE INTO sessions (token, license_key, tier, created_at) VALUES (?,?,?,?)",
        (token, key, lic["tier"], datetime.now(timezone.utc).isoformat())
    )
    con.commit()
    con.close()
    resp = make_response(jsonify({"ok": True, "tier": lic["tier"], "lic": lic}))
    resp.set_cookie("cc_token", token, max_age=30*24*3600, httponly=True, samesite="Lax")
    return resp

@app.post("/api/auth/logout")
def logout():
    token = request.cookies.get("cc_token", "")
    if token:
        con = get_db()
        con.execute("DELETE FROM sessions WHERE token=?", (token,))
        con.commit()
        con.close()
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie("cc_token")
    return resp

@app.get("/api/me")
def api_me():
    token = request.cookies.get("cc_token", "")
    if not token:
        return jsonify({}), 200
    con = get_db()
    row = con.execute("SELECT license_key, tier FROM sessions WHERE token=?", (token,)).fetchone()
    con.close()
    if not row:
        return jsonify({}), 200
    key, tier = row
    limits = TIER_LIMITS.get(tier, TIER_LIMITS["MONTHLY"])
    return jsonify({"license_key": key, "tier": tier,
                    "lic": {"label": TIER_LABELS.get(tier, tier), **limits}})

# ─── Bot sync ──────────────────────────────────────────────────────────────────

@app.post("/api/sync")
def bot_sync():
    key = request.headers.get("X-License-Key", "")
    if not key or not key.startswith("CB-"):
        return jsonify({"error": "invalid key"}), 401
    body = request.json or {}

    con = get_db()
    con.execute(
        "INSERT OR REPLACE INTO heartbeats (license_key, last_seen, running, version) VALUES (?,?,?,?)",
        (key, datetime.now(timezone.utc).isoformat(),
         int(body.get("running", False)), body.get("version", ""))
    )
    for t in body.get("trades", []):
        ts = t.get("ts") or datetime.now(timezone.utc).isoformat()
        try:
            con.execute(
                "INSERT OR IGNORE INTO trades (license_key,ts,type,slug,outcome,price,amount,success) VALUES (?,?,?,?,?,?,?,?)",
                (key, ts, t.get("type",""), t.get("slug",""), t.get("outcome",""),
                 float(t.get("price") or 0), float(t.get("amount") or 0), int(t.get("success", 1)))
            )
        except Exception:
            pass
    con.commit()
    row = con.execute("SELECT config_json FROM configs WHERE license_key=?", (key,)).fetchone()
    con.close()
    return jsonify({"ok": True, "config": json.loads(row[0]) if row else {}})

# ─── Dashboard API ─────────────────────────────────────────────────────────────

@app.get("/api/status")
@require_auth
def api_status():
    con = get_db()
    row = con.execute(
        "SELECT last_seen, running, version FROM heartbeats WHERE license_key=?",
        (request.license_key,)
    ).fetchone()
    con.close()
    if not row:
        return jsonify({"running": False, "last_seen": None, "online": False})
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row[0])).total_seconds()
    return jsonify({
        "running":   bool(row[1]),
        "last_seen": row[0],
        "online":    age < 120,
        "version":   row[2],
        "tier":      request.tier,
    })

@app.get("/api/trades")
@require_auth
def api_trades():
    limit = min(int(request.args.get("limit", 50)), 500)
    con = get_db()
    rows = con.execute(
        "SELECT ts,type,slug,outcome,price,amount,success FROM trades WHERE license_key=? ORDER BY ts DESC LIMIT ?",
        (request.license_key, limit)
    ).fetchall()
    con.close()
    return jsonify([
        {"ts":r[0],"type":r[1],"slug":r[2],"outcome":r[3],"price":r[4],"amount":r[5],"success":bool(r[6])}
        for r in rows
    ])

@app.get("/api/stats")
@require_auth
def api_stats():
    con = get_db()
    rows = con.execute(
        "SELECT type,price,amount,success,ts,slug,outcome FROM trades WHERE license_key=? ORDER BY ts DESC",
        (request.license_key,)
    ).fetchall()
    con.close()
    buys   = [r for r in rows if r[0]=="copy_buy"  and r[3]]
    errors = [r for r in rows if r[0]=="error"]
    return jsonify({
        "total_trades":   len([r for r in rows if r[0] in ("copy_buy","copy_sell") and r[3]]),
        "total_invested": round(sum(r[2] or 0 for r in buys), 2),
        "errors":         len(errors),
        "recent_trades":  [{"ts":r[4],"type":r[0],"slug":r[5],"outcome":r[6],"price":r[1],"amount":r[2]} for r in rows[:10]],
    })

@app.get("/api/config")
@require_auth
def api_config():
    con = get_db()
    row = con.execute("SELECT config_json FROM configs WHERE license_key=?", (request.license_key,)).fetchone()
    con.close()
    defaults = {"wager_mode":"fixed","wager_fixed":10,"wager_pct":2,
                "daily_loss_limit":100,"max_daily_trades":20,
                "scan_all_simultaneously":False,"categories":{},"selected_traders":[]}
    return jsonify(json.loads(row[0]) if row else defaults)

@app.post("/api/config")
@require_auth
def save_config():
    cfg = request.json or {}
    con = get_db()
    con.execute(
        "INSERT OR REPLACE INTO configs (license_key, config_json, updated_at) VALUES (?,?,?)",
        (request.license_key, json.dumps(cfg), datetime.now(timezone.utc).isoformat())
    )
    con.commit()
    con.close()
    return jsonify({"ok": True})

@app.get("/api/traders")
@require_auth
def api_traders():
    """Top traders from Polymarket leaderboard (category-filtered)."""
    category = request.args.get("category", "")   # e.g. "NBA", "Bitcoin", ""
    limit    = min(int(request.args.get("limit", 20)), 100)
    period   = request.args.get("period", "weekly")
    try:
        resp = http.get(
            "https://gamma-api.polymarket.com/leaderboard",
            params={"limit": limit * 5, "period": period},   # over-fetch for filtering
            timeout=10,
        )
        if resp.ok:
            data    = resp.json()
            traders = data if isinstance(data, list) else data.get("data", data.get("traders", []))
            # Attach rank
            for i, t in enumerate(traders):
                t["rank"] = i + 1
            return jsonify(traders[:limit])
    except Exception:
        pass
    return jsonify([
        {"address": f"0x{i:040x}", "username": f"trader_{i}",
         "pnl": (20-i)*3200, "volume": (20-i)*150000, "rank": i+1}
        for i in range(1, min(limit+1, 21))
    ])


@app.get("/api/trader/<address>")
@require_auth
def api_trader_profile(address: str):
    """
    Full trader profile: overall PnL + win rate breakdown by category → team/coin.
    Proxies to Polymarket data-api and gamma-api, builds the stats server-side.
    Cached per-session for 30 minutes to avoid hammering Polymarket.
    """
    address = address.lower()
    cache_key = f"trader_profile:{address}"

    # Simple in-memory cache (Flask process)
    import time
    cache = app.config.setdefault("_trader_cache", {})
    cached = cache.get(cache_key)
    if cached and time.time() - cached["ts"] < 1800:
        return jsonify(cached["data"])

    # Pull trade history from Polymarket
    GAMMA    = "https://gamma-api.polymarket.com"
    DATA_API = "https://data-api.polymarket.com"

    trades = []
    try:
        r = http.get(f"{DATA_API}/activity", params={"user": address, "limit": 500}, timeout=15)
        if r.ok:
            trades = r.json() if isinstance(r.json(), list) else []
    except Exception:
        pass

    if not trades:
        try:
            r = http.get(f"https://clob.polymarket.com/trades",
                         params={"maker_address": address, "limit": 500}, timeout=15)
            if r.ok:
                d = r.json()
                trades = d.get("data", d) if isinstance(d, dict) else d
        except Exception:
            pass

    # Pull basic profile info from leaderboard
    username = address[:10]
    poly_pnl = 0.0
    try:
        r = http.get(f"{GAMMA}/leaderboard", params={"limit": 300, "period": "all"}, timeout=10)
        if r.ok:
            board = r.json()
            board = board if isinstance(board, list) else board.get("data", [])
            for t in board:
                if (t.get("address") or "").lower() == address:
                    username = t.get("pseudonym") or t.get("username") or address[:10]
                    poly_pnl = float(t.get("pnl") or 0)
                    break
    except Exception:
        pass

    # Build category/subcategory stats
    CATEGORY_KEYWORDS = {
        "NBA":["nba","lakers","celtics","warriors","bulls","heat","knicks","bucks","nuggets","suns"],
        "NFL":["nfl","patriots","cowboys","packers","49ers","chiefs","ravens","bills","bengals"],
        "MLB":["mlb","yankees","red sox","dodgers","mets","braves","astros","baseball"],
        "NHL":["nhl","bruins","maple leafs","canadiens","penguins","capitals","hockey"],
        "Soccer":["soccer","premier league","champions league","arsenal","chelsea","manchester"],
        "Tennis":["tennis","wimbledon","french open","djokovic","alcaraz"],
        "MMA/UFC":["ufc","mma","boxing","fight"],
        "Golf":["golf","pga","masters"],
        "Bitcoin":["bitcoin","btc"],"Ethereum":["ethereum","eth"],
        "Solana":["solana","sol "],"XRP":["xrp","ripple"],"DOGE":["dogecoin","doge"],
        "Crypto":["crypto","defi","blockchain","token","binance","bnb","cardano"],
        "US Politics":["trump","biden","harris","election","senate","congress","president"],
        "International":["ukraine","russia","china","nato","putin","zelensky"],
        "Economics":["inflation","gdp","recession","interest rate","fed rate","s&p"],
    }
    PARENT = {
        "NBA":"Sports","NFL":"Sports","MLB":"Sports","NHL":"Sports",
        "Soccer":"Sports","Tennis":"Sports","MMA/UFC":"Sports","Golf":"Sports",
        "Bitcoin":"Crypto","Ethereum":"Crypto","Solana":"Crypto","XRP":"Crypto",
        "DOGE":"Crypto","Crypto":"Crypto",
        "US Politics":"Politics","International":"Politics",
        "Economics":"Economics",
    }

    def _detect(title):
        combined = (title or "").lower()
        best, bn = "Other", 0
        for cat, kws in CATEGORY_KEYWORDS.items():
            n = sum(1 for k in kws if k in combined)
            if n > bn:
                bn, best = n, cat
        return PARENT.get(best, "Other"), best

    # Market metadata cache within this request
    mkt_cache = {}
    def _get_mkt(slug):
        if slug in mkt_cache:
            return mkt_cache[slug]
        try:
            r2 = http.get(f"{GAMMA}/markets", params={"slug": slug}, timeout=8)
            if r2.ok:
                d2 = r2.json()
                mkt = d2[0] if isinstance(d2, list) and d2 else (d2 if isinstance(d2, dict) else {})
                mkt_cache[slug] = mkt
                return mkt
        except Exception:
            pass
        return {}

    stats = {}
    total_resolved = total_wins = 0
    total_estimated_pnl = 0.0

    for raw in (trades or []):
        action = (raw.get("action") or raw.get("side") or raw.get("type") or "").upper()
        if action not in ("BUY","B","MARKET_BUY"):
            continue
        slug    = (raw.get("market_slug") or raw.get("slug") or raw.get("conduit_id") or "")
        outcome = (raw.get("outcome") or raw.get("side_name") or "YES").upper()
        outcome = "YES" if outcome in ("YES","Y") else "NO"
        price   = float(raw.get("price") or 0.5)
        shares  = float(raw.get("size") or raw.get("shares") or 0)
        size_usd= float(raw.get("usdcSize") or raw.get("amount") or 0)
        title   = raw.get("title") or raw.get("question") or ""
        if not slug:
            continue
        mkt = _get_mkt(slug)
        resolution = (mkt.get("resolution") or "").upper().strip()
        if not resolution or resolution in ("","N/A","PENDING"):
            continue
        if not title:
            title = mkt.get("question") or mkt.get("title") or slug
        won  = (outcome == resolution) or (outcome == "YES" and resolution in ("YES","Y","1"))
        pnl  = (shares * (1 - price)) if won else (-shares * price)
        if shares == 0 and size_usd > 0 and price > 0:
            pnl = size_usd * (1/price - 1) if won else -size_usd
        total_resolved += 1
        total_wins     += int(won)
        total_estimated_pnl += pnl
        parent_cat, sub_cat = _detect(title)
        sub_data = (stats
                    .setdefault(parent_cat, {})
                    .setdefault(sub_cat, {"trades":0,"wins":0,"pnl":0.0}))
        sub_data["trades"] += 1
        sub_data["wins"]   += int(won)
        sub_data["pnl"]    += pnl

    # Add win rates
    rows = []
    for parent, cats in stats.items():
        for sub_cat, sd in cats.items():
            t = sd["trades"]
            rows.append({
                "parent":   parent,
                "category": sub_cat,
                "trades":   t,
                "wins":     sd["wins"],
                "win_rate": round(sd["wins"] / t, 4) if t else 0.0,
                "pnl":      round(sd["pnl"], 2),
            })
    rows.sort(key=lambda x: x["pnl"], reverse=True)

    profile = {
        "address":          address,
        "username":         username,
        "overall_win_rate": round(total_wins / total_resolved, 4) if total_resolved else 0.0,
        "total_trades":     total_resolved,
        "estimated_pnl":    round(total_estimated_pnl, 2),
        "poly_pnl":         round(poly_pnl, 2),
        "breakdown":        rows,
    }
    cache[cache_key] = {"ts": time.time(), "data": profile}
    return jsonify(profile)


@app.get("/api/kalshi/filters")
@require_auth
def get_kalshi_filters():
    """Get the current Kalshi CopyCat filter config for this user."""
    con = get_db()
    row = con.execute(
        "SELECT config_json FROM configs WHERE license_key=?",
        (request.license_key,)
    ).fetchone()
    con.close()
    if row:
        cfg = json.loads(row[0])
        return jsonify(cfg.get("kalshi_filters", {}))
    return jsonify({})


@app.post("/api/kalshi/filters")
@require_auth
def save_kalshi_filters():
    """Save Kalshi-specific filter overrides."""
    filters = request.json or {}
    con = get_db()
    row = con.execute(
        "SELECT config_json FROM configs WHERE license_key=?",
        (request.license_key,)
    ).fetchone()
    cfg = json.loads(row[0]) if row else {}
    cfg["kalshi_filters"] = filters
    con.execute(
        "INSERT OR REPLACE INTO configs (license_key, config_json, updated_at) VALUES (?,?,?)",
        (request.license_key, json.dumps(cfg), datetime.now(timezone.utc).isoformat())
    )
    con.commit()
    con.close()
    return jsonify({"ok": True})

# ─── Static assets ─────────────────────────────────────────────────────────────

@app.get("/")
@app.get("/dashboard")
def index():
    return send_from_directory("static", "index.html")

@app.get("/manifest.json")
def manifest():
    return jsonify({
        "name":"CopyCat","short_name":"CopyCat",
        "description":"Auto-copy top Polymarket traders",
        "start_url":"/","display":"standalone",
        "background_color":"#0d1117","theme_color":"#3fb950",
        "icons":[{"src":"/icon.svg","sizes":"any","type":"image/svg+xml","purpose":"any maskable"}],
    })

@app.get("/icon.svg")
def icon():
    from flask import Response
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect width="100" height="100" rx="20" fill="#161b22"/><text x="50" y="62" font-size="55" text-anchor="middle">&#x1F431;</text></svg>'
    return Response(svg, mimetype="image/svg+xml")

@app.get("/sw.js")
def sw():
    from flask import Response
    js = "const C='cc-v1';self.addEventListener('install',e=>{self.skipWaiting();});self.addEventListener('activate',e=>{self.clients.claim();});self.addEventListener('fetch',e=>{if(e.request.url.includes('/api/'))return;e.respondWith(fetch(e.request).catch(()=>caches.match('/')));});"
    return Response(js, mimetype="application/javascript")

@app.get("/health")
def health():
    return jsonify({"ok": True})

# ─── Boot ──────────────────────────────────────────────────────────────────────

init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    app.run(host="0.0.0.0", port=port)
