#!/usr/bin/env python3
"""
CopyCat Dashboard
Run: python3 dashboard.py
Open: http://localhost:5001
"""

import json
import os
import signal
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from flask import Flask, jsonify, request

sys.path.insert(0, str(Path(__file__).parent))
from license import load_license, save_license, License
from bot_config import load_config, save_config, CATEGORY_HIERARCHY

# ─── Config ────────────────────────────────────────────────────────────────────

BOT_SCRIPT = Path(__file__).parent / "polymarket_copycat.py"
LOG_FILE   = Path(__file__).parent / "trades_log.jsonl"
PID_FILE   = Path(__file__).parent / ".bot.pid"
PORT       = 5001

app = Flask(__name__)

# ─── Bot management ────────────────────────────────────────────────────────────

def bot_pid() -> Optional[int]:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, OSError):
        PID_FILE.unlink(missing_ok=True)
        return None

def bot_running() -> bool:
    return bot_pid() is not None

@app.post("/api/bot/start")
def start_bot():
    if bot_running():
        return jsonify({"ok": False, "msg": "Bot already running"})
    proc = subprocess.Popen(
        [sys.executable, str(BOT_SCRIPT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    PID_FILE.write_text(str(proc.pid))
    return jsonify({"ok": True, "pid": proc.pid})

@app.post("/api/bot/stop")
def stop_bot():
    pid = bot_pid()
    if pid is None:
        return jsonify({"ok": False, "msg": "Bot not running"})
    try:
        os.kill(pid, signal.SIGTERM)
        PID_FILE.unlink(missing_ok=True)
        return jsonify({"ok": True})
    except OSError as e:
        return jsonify({"ok": False, "msg": str(e)})

@app.get("/api/bot/status")
def bot_status():
    lic = load_license()
    return jsonify({
        "running": bot_running(),
        "pid":     bot_pid(),
        "tier":    lic.tier,
        "label":   lic.label,
        "valid":   lic.valid,
    })

# ─── License ───────────────────────────────────────────────────────────────────

@app.get("/api/license")
def get_license():
    lic = load_license()
    return jsonify({
        "valid":          lic.valid,
        "tier":           lic.tier,
        "label":          lic.label,
        "error":          lic.error,
        "max_traders":    lic.max_traders,
        "custom_sizing":  lic.custom_sizing,
        "min_wager":      lic.min_wager,
        "max_wager":      lic.max_wager,
        "max_wager_pct":  lic.max_wager_pct,
        "pro_analytics":  lic.pro_analytics,
        "kalshi_bot":     lic.kalshi_bot,
    })

@app.post("/api/license")
def activate_license():
    key = (request.json or {}).get("key", "")
    lic = save_license(key)
    return jsonify({
        "valid": lic.valid,
        "tier":  lic.tier,
        "label": lic.label,
        "error": lic.error,
    })

# ─── Log parsing ───────────────────────────────────────────────────────────────

def load_log() -> list:
    if not LOG_FILE.exists():
        return []
    events = []
    with open(LOG_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return events

def compute_stats(events: list) -> dict:
    copy_buys  = [e for e in events if e["type"] == "copy_buy"  and e.get("success")]
    copy_sells = [e for e in events if e["type"] == "copy_sell" and e.get("success")]
    errors     = [e for e in events if e["type"] == "error"]

    total_trades   = len(copy_buys) + len(copy_sells)
    total_invested = len(copy_buys) * 10.0

    # Build per-market round-trips from observed_trade prices
    observed   = [e for e in events if e["type"] == "observed_trade"]
    buy_prices = defaultdict(list)
    winners, losers = 0, 0
    realized_pnl = 0.0

    for o in observed:
        key    = f"{o.get('slug','')}__{o.get('outcome','')}"
        action = o.get("action", "")
        price  = float(o.get("price") or 0)

        if action == "buy" and price > 0:
            buy_prices[key].append(price)
        elif action == "sell" and price > 0 and buy_prices[key]:
            avg_cost  = sum(buy_prices[key]) / len(buy_prices[key])
            cost_norm = avg_cost if avg_cost <= 1 else avg_cost / 100
            sell_norm = price    if price    <= 1 else price    / 100
            if sell_norm > cost_norm and cost_norm > 0:
                winners += 1
                realized_pnl += (sell_norm - cost_norm) / cost_norm * 10.0
            elif cost_norm > 0:
                losers += 1
                realized_pnl -= (cost_norm - sell_norm) / cost_norm * 10.0
            buy_prices[key] = []

    resolved = winners + losers
    win_rate = round(winners / resolved * 100, 1) if resolved > 0 else None

    return {
        "total_trades":   total_trades,
        "total_buys":     len(copy_buys),
        "total_sells":    len(copy_sells),
        "total_invested": round(total_invested, 2),
        "realized_pnl":   round(realized_pnl, 2),
        "winners":        winners,
        "losers":         losers,
        "win_rate":       win_rate,
        "errors":         len(errors),
    }

def format_trades(events: list) -> list:
    relevant = {"copy_buy", "copy_sell", "error"}
    trades = [e for e in events if e.get("type") in relevant]
    trades.sort(key=lambda e: e.get("ts", ""), reverse=True)
    out = []
    for t in trades[:100]:
        ts = t.get("ts", "")
        try:
            tstr = datetime.fromisoformat(ts).astimezone().strftime("%b %d  %H:%M:%S")
        except Exception:
            tstr = ts[:19]
        out.append({
            "time":    tstr,
            "type":    t["type"],
            "trader":  t.get("trader", "—"),
            "slug":    t.get("slug", "—"),
            "outcome": t.get("outcome", "—"),
            "action":  "BUY" if t["type"] == "copy_buy" else ("SELL" if t["type"] == "copy_sell" else "ERR"),
            "amount":  f"${t['usd']}" if t.get("usd") else (f"{t.get('shares','?')} sh" if t.get("shares") else "—"),
            "success": t.get("success", False),
            "error":   t.get("error", ""),
        })
    return out

# ─── Pro analytics ─────────────────────────────────────────────────────────────

def compute_analytics(events: list) -> dict:
    """Pro-only: P&L over time + per-trader breakdown."""

    # ── P&L over time (daily buckets) ──────────────────────────
    daily = defaultdict(lambda: {"invested": 0.0, "pnl": 0.0, "trades": 0})
    observed = [e for e in events if e["type"] == "observed_trade"]
    buy_prices = defaultdict(list)

    for o in observed:
        key    = f"{o.get('slug','')}__{o.get('outcome','')}"
        action = o.get("action", "")
        price  = float(o.get("price") or 0)
        ts     = o.get("ts", "")
        try:
            day = datetime.fromisoformat(ts).strftime("%Y-%m-%d")
        except Exception:
            day = "unknown"

        if action == "buy" and price > 0:
            buy_prices[key].append((price, day))
        elif action == "sell" and price > 0 and buy_prices[key]:
            avg_cost, buy_day = buy_prices[key][0][0], buy_prices[key][0][1]
            cost_norm = avg_cost if avg_cost <= 1 else avg_cost / 100
            sell_norm = price    if price    <= 1 else price    / 100
            if cost_norm > 0:
                pnl = (sell_norm - cost_norm) / cost_norm * 10.0
                daily[day]["pnl"] += pnl
                daily[day]["trades"] += 1
            buy_prices[key] = []

    for e in events:
        if e["type"] == "copy_buy" and e.get("success"):
            ts = e.get("ts", "")
            try:
                day = datetime.fromisoformat(ts).strftime("%Y-%m-%d")
            except Exception:
                day = "unknown"
            daily[day]["invested"] += 10.0
            daily[day]["trades"] += 1

    # Build cumulative P&L series
    pnl_series = []
    cumulative = 0.0
    for day in sorted(daily):
        cumulative += daily[day]["pnl"]
        pnl_series.append({
            "date":       day,
            "daily_pnl":  round(daily[day]["pnl"], 2),
            "cumulative": round(cumulative, 2),
            "trades":     daily[day]["trades"],
        })

    # ── Per-trader breakdown ────────────────────────────────────
    trader_stats = defaultdict(lambda: {
        "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0
    })
    buy_prices_by_trader = defaultdict(lambda: defaultdict(list))

    for o in observed:
        trader = o.get("trader", "unknown")
        key    = f"{o.get('slug','')}__{o.get('outcome','')}"
        action = o.get("action", "")
        price  = float(o.get("price") or 0)

        if action == "buy" and price > 0:
            buy_prices_by_trader[trader][key].append(price)
            trader_stats[trader]["trades"] += 1
        elif action == "sell" and price > 0 and buy_prices_by_trader[trader][key]:
            avg_cost  = buy_prices_by_trader[trader][key][0]
            cost_norm = avg_cost if avg_cost <= 1 else avg_cost / 100
            sell_norm = price    if price    <= 1 else price    / 100
            if cost_norm > 0:
                pnl = (sell_norm - cost_norm) / cost_norm * 10.0
                trader_stats[trader]["pnl"] += pnl
                if pnl >= 0:
                    trader_stats[trader]["wins"] += 1
                else:
                    trader_stats[trader]["losses"] += 1
            buy_prices_by_trader[trader][key] = []

    trader_table = []
    for name, s in trader_stats.items():
        resolved = s["wins"] + s["losses"]
        trader_table.append({
            "trader":   name,
            "trades":   s["trades"],
            "wins":     s["wins"],
            "losses":   s["losses"],
            "win_rate": round(s["wins"] / resolved * 100, 1) if resolved > 0 else None,
            "pnl":      round(s["pnl"], 2),
        })
    trader_table.sort(key=lambda x: x["pnl"], reverse=True)

    return {"pnl_series": pnl_series, "traders": trader_table}

# ─── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def api_stats():
    events = load_log()
    return jsonify({"stats": compute_stats(events), "trades": format_trades(events)})

@app.get("/api/positions")
def api_positions():
    """Fetch open positions from bullpen."""
    data = _bullpen_json(["polymarket", "positions"], timeout=20)
    if data is None:
        return jsonify({"positions": [], "error": "Could not fetch positions"})
    positions = data.get("positions", data) if isinstance(data, dict) else data
    out = []
    for p in (positions or []):
        slug    = p.get("market_slug") or p.get("slug", "")
        outcome = p.get("outcome", "Yes")
        shares  = float(p.get("size") or p.get("shares") or 0)
        value   = float(p.get("current_value") or p.get("value") or 0)
        price   = float(p.get("price") or p.get("yes_price") or 0)
        title   = p.get("title") or p.get("market_title") or slug
        if slug and shares > 0:
            out.append({"slug": slug, "outcome": outcome, "shares": round(shares, 4),
                        "value": round(value, 2), "price": round(price, 4), "title": title})
    return jsonify({"positions": out})

@app.post("/api/positions/close")
def api_close_position():
    """Sell all or part of a position."""
    data   = request.json or {}
    slug    = data.get("slug", "")
    outcome = data.get("outcome", "Yes")
    shares  = float(data.get("shares", 0))
    if not slug or shares <= 0:
        return jsonify({"ok": False, "error": "Missing slug or shares"})
    cmd = [_BULLPEN, "polymarket", "sell", slug, outcome, str(shares), "--yes"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=_ENV)
        ok = result.returncode == 0
        return jsonify({"ok": ok, "output": result.stdout[:300] if ok else result.stderr[:300]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.get("/api/analytics")
def api_analytics():
    lic = load_license()
    if not lic.pro_analytics:
        return jsonify({"error": "pro_required"}), 403
    return jsonify(compute_analytics(load_log()))

# ─── Config API ────────────────────────────────────────────────────────────────

@app.get("/api/config")
def api_get_config():
    return jsonify(load_config())

@app.post("/api/config")
def api_save_config():
    data = request.json or {}
    cfg = load_config()
    # Merge top-level keys
    for k in ("wager_mode", "wager_fixed", "wager_pct", "daily_loss_limit",
              "selected_traders", "categories", "scan_all_simultaneously",
              "tos_accepted"):
        if k in data:
            cfg[k] = data[k]

    # Enforce tier wager limits server-side
    lic = load_license()
    cfg["wager_fixed"] = float(max(lic.min_wager, min(lic.max_wager, float(cfg.get("wager_fixed", 10)))))
    cfg["wager_pct"]   = float(min(lic.max_wager_pct, float(cfg.get("wager_pct", 2))))

    save_config(cfg)
    return jsonify({"ok": True, "config": cfg})

# ── Trader cache (avoids hammering bullpen on every tab switch) ────────────
_trader_cache: dict = {}   # {(category, limit): (ts, results)}
TRADER_CACHE_TTL = 300     # 5 minutes

import shutil as _shutil
_BULLPEN = _shutil.which("bullpen") or "/opt/homebrew/bin/bullpen"
# Ensure homebrew PATH is available in subprocess calls
_ENV = {**os.environ, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", "")}

def _bullpen_json(args: list, timeout: int = 14):
    """Run a bullpen command and return parsed JSON, or None on failure."""
    try:
        r = subprocess.run(
            [_BULLPEN] + args + ["--output", "json"],
            capture_output=True, text=True, timeout=timeout, env=_ENV
        )
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)
    except Exception:
        return None

def _global_leaderboard(limit: int) -> list:
    """Pull top traders from the weekly Polymarket leaderboard."""
    data = _bullpen_json(["polymarket", "data", "leaderboard", "--period", "week"])
    if not data or not isinstance(data, list):
        return []
    out = []
    for i, t in enumerate(data[:limit]):
        name = t.get("pseudonym") or t.get("username") or t.get("address", f"Trader {i+1}")
        out.append({
            "name":       name,
            "address":    t.get("address", ""),
            "trades":     0,
            "wins": 0, "losses": 0, "win_rate": None,
            "pnl":        round(float(t.get("pnl") or 0), 2),
            "weekly_pnl": round(float(t.get("pnl") or 0), 2),
            "volume":     round(float(t.get("volume") or 0), 2),
            "rank":       i + 1,
            "cat_trades": 0,
            "score":      float(t.get("pnl") or 0),
        })
    return out

def _extract_slugs_from_search(raw) -> list:
    """
    bullpen polymarket search returns:
      {"events": [{"markets": [{"slug": ...}]}, ...], ...}
    Extract all market slugs, sorted by parent event volume.
    """
    slugs = []
    if not raw or not isinstance(raw, dict):
        return slugs
    for event in raw.get("events", []):
        vol = float(event.get("volume") or 0)
        for m in event.get("markets", []):
            slug = m.get("slug") or m.get("id", "")
            if slug:
                slugs.append((vol, slug))
    # highest volume first
    slugs.sort(reverse=True)
    return [s for _, s in slugs]

def _category_traders(category: str, limit: int) -> list:
    """
    Find top traders for a sub-category by:
    1. Searching for the most liquid markets using category keywords
    2. Pulling top holders of those markets (bullpen polymarket holders)
    3. Ranking holders by shares held across category markets
    """
    from bot_config import CATEGORY_KEYWORDS
    keywords = CATEGORY_KEYWORDS.get(category, [category])

    all_holders: dict = {}   # {display_name -> {shares, markets}}
    markets_checked = 0

    for kw in keywords[:4]:
        if markets_checked >= 3:
            break

        raw = _bullpen_json(["polymarket", "search", kw], timeout=14)
        slugs = _extract_slugs_from_search(raw)

        for slug in slugs[:2]:
            markets_checked += 1

            holders_raw = _bullpen_json(["polymarket", "holders", slug], timeout=12)
            if not holders_raw:
                continue
            # holders is a plain list: [{rank, display_name, shares, usd_value}, ...]
            holder_list = holders_raw if isinstance(holders_raw, list) else []
            if not holder_list:
                continue

            for h in holder_list[:30]:
                name = (h.get("display_name") or h.get("pseudonym")
                        or h.get("username") or "").strip()
                if not name:
                    continue
                shares = float(h.get("shares") or h.get("usd_value") or 0)
                rank   = int(h.get("rank") or 999)
                if name not in all_holders:
                    all_holders[name] = {"shares": 0.0, "markets": 0, "best_rank": rank}
                all_holders[name]["shares"]    += shares
                all_holders[name]["markets"]   += 1
                all_holders[name]["best_rank"]  = min(all_holders[name]["best_rank"], rank)

        if len(all_holders) >= limit:
            break

    if not all_holders:
        return _global_leaderboard(limit)

    ranked = sorted(all_holders.items(), key=lambda x: x[1]["shares"], reverse=True)
    out = []
    for i, (name, d) in enumerate(ranked[:limit]):
        out.append({
            "name":       name,
            "address":    name,   # display_name used as identifier
            "trades":     d["markets"],
            "wins": 0, "losses": 0, "win_rate": None,
            "pnl":        round(d["shares"], 2),
            "weekly_pnl": round(d["shares"], 2),
            "volume":     round(d["shares"], 2),
            "rank":       i + 1,
            "cat_trades": d["markets"],
            "score":      d["shares"],
        })
    return out

@app.get("/api/traders")
def api_traders():
    """
    GET /api/traders?category=<key>&limit=10|20&refresh=0|1
    Returns top traders for a sub-category (bitcoin, nba, congress, fed, …)
    by finding the most liquid markets in that category and ranking their
    top holders by total position value.  Falls back to global leaderboard
    when category is blank or no market data is found.
    Results are cached for 5 minutes.
    """
    category = request.args.get("category", "").lower().strip()
    limit    = min(int(request.args.get("limit", 20)), 50)
    refresh  = request.args.get("refresh", "0") == "1"

    cache_key = (category, limit)
    cached    = _trader_cache.get(cache_key)

    if cached and not refresh and (time.time() - cached[0]) < TRADER_CACHE_TTL:
        traders = cached[1]
    else:
        if category:
            traders = _category_traders(category, limit)
        else:
            traders = _global_leaderboard(limit)
        _trader_cache[cache_key] = (time.time(), traders)

    cfg      = load_config()
    selected = set(cfg.get("selected_traders", []))
    for t in traders:
        t["selected"] = t["name"] in selected or t["address"] in selected

    return jsonify({
        "traders":  traders,
        "selected": list(selected),
        "category": category,
        "total":    len(traders),
    })

@app.get("/api/categories")
def api_categories():
    return jsonify(CATEGORY_HIERARCHY)

_BULLPEN_CREDS = Path.home() / ".bullpen" / "credentials.json"

@app.get("/api/connection")
def api_connection():
    """
    Check bullpen install + login state by reading ~/.bullpen/credentials.json
    directly — avoids subprocess hangs from commands that need a TTY.
    """
    bullpen_installed = os.path.isfile(_BULLPEN)
    if not bullpen_installed:
        return jsonify({"bullpen_installed": False, "logged_in": False,
                        "address": None, "error": "bullpen not installed"})
    try:
        creds = json.loads(_BULLPEN_CREDS.read_text()) if _BULLPEN_CREDS.exists() else {}
        address   = creds.get("polymarket_address") or creds.get("polygon_signing_address", "")
        logged_in = bool(address and creds.get("access_token"))
        short     = (address[:8] + "…" + address[-6:]) if address else ""
        return jsonify({
            "bullpen_installed": True,
            "logged_in":         logged_in,
            "address":           address,
            "short":             short,
        })
    except Exception as e:
        return jsonify({"bullpen_installed": True, "logged_in": False,
                        "address": None, "error": str(e)})

# ─── Dashboard HTML ─────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>CopyCat</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#3fb950">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="CopyCat">
<link rel="apple-touch-icon" href="/icon-192.png">
<meta name="mobile-web-app-capable" content="yes">
<meta name="application-name" content="CopyCat">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px;min-height:100vh}

/* ── Header ── */
header{display:flex;align-items:center;justify-content:space-between;padding:16px 28px;background:#161b22;border-bottom:1px solid #21262d;gap:12px;flex-wrap:wrap}
.logo{font-size:17px;font-weight:700;color:#e6edf3;white-space:nowrap}.logo span{color:#3fb950}
.header-right{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.tier-badge{padding:4px 12px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;background:#21262d;color:#8b949e;border:1px solid #30363d}
.tier-badge.monthly{background:#1e2d3d;color:#58a6ff;border-color:#388bfd}
.tier-badge.pro{background:#2d1f3d;color:#d2a8ff;border-color:#8957e5}
.tier-badge.pro_kalshi{background:#2d1a2d;color:#f0883e;border-color:#d18616}
.tier-badge.trial{background:#21262d;color:#8b949e;border-color:#30363d}
.status-pill{display:flex;align-items:center;gap:7px;padding:6px 14px;border-radius:20px;font-size:13px;font-weight:600;background:#1a2d1e;color:#3fb950;border:1px solid #2ea043;transition:all .3s;white-space:nowrap}
.status-pill.stopped{background:#2d1a1a;color:#f85149;border-color:#da3633}
.status-dot{width:8px;height:8px;border-radius:50%;background:#3fb950;box-shadow:0 0 6px #3fb950;animation:pulse 2s infinite}
.status-pill.stopped .status-dot{background:#f85149;box-shadow:0 0 6px #f85149;animation:none}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.btn{padding:8px 18px;border-radius:8px;border:none;font-size:13px;font-weight:600;cursor:pointer;transition:all .2s;white-space:nowrap}
.btn-stop{background:#da3633;color:#fff}.btn-stop:hover{background:#f85149}
.btn-start{background:#2ea043;color:#fff}.btn-start:hover{background:#3fb950}
.btn-ghost{background:transparent;color:#8b949e;border:1px solid #30363d}.btn-ghost:hover{color:#e6edf3;border-color:#8b949e}
.last-ref{font-size:11px;color:#8b949e;white-space:nowrap}

/* ── Tabs ── */
.tabs{display:flex;gap:0;padding:0 28px;background:#161b22;border-bottom:1px solid #21262d}
.tab{padding:12px 18px;font-size:13px;font-weight:500;color:#8b949e;cursor:pointer;border-bottom:2px solid transparent;transition:all .2s}
.tab:hover{color:#e6edf3}
.tab.active{color:#e6edf3;border-bottom-color:#3fb950}
.tab.pro-tab{position:relative}
.pro-pip{display:inline-block;width:6px;height:6px;border-radius:50%;background:#8957e5;margin-left:5px;vertical-align:middle}
.tab-pane{display:none}.tab-pane.active{display:block}

/* ── Stats ── */
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:16px;padding:24px 28px 0}
@media(max-width:900px){.stats{grid-template-columns:repeat(2,1fr)}}
.card{background:#161b22;border:1px solid #21262d;border-radius:12px;padding:20px 22px}
.card-label{font-size:11px;text-transform:uppercase;letter-spacing:.8px;color:#8b949e;margin-bottom:10px}
.card-value{font-size:28px;font-weight:700;line-height:1}
.card-value.green{color:#3fb950}.card-value.red{color:#f85149}.card-value.dim{color:#8b949e}
.card-sub{margin-top:6px;font-size:12px;color:#8b949e}
.wl-bar{display:flex;height:5px;border-radius:3px;overflow:hidden;margin-top:10px;background:#21262d}
.wl-win{background:#3fb950;transition:width .5s}.wl-loss{background:#f85149;transition:width .5s}

/* ── Section ── */
.section{padding:24px 28px}
.section-title{font-size:15px;font-weight:600;margin-bottom:14px;color:#e6edf3}
.table-wrap{border:1px solid #21262d;border-radius:12px;overflow:hidden}
table{width:100%;border-collapse:collapse}
thead{background:#161b22}
th{padding:11px 16px;text-align:left;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.7px;color:#8b949e;white-space:nowrap}
tbody tr{border-top:1px solid #21262d;transition:background .15s}
tbody tr:hover{background:#161b22}
td{padding:11px 16px;color:#c9d1d9;white-space:nowrap}
.badge{display:inline-block;padding:3px 9px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.4px}
.badge-buy{background:#1a2d1e;color:#3fb950;border:1px solid #2ea043}
.badge-sell{background:#1e2533;color:#58a6ff;border:1px solid #388bfd}
.badge-err{background:#2d1a1a;color:#f85149;border:1px solid #da3633}
.badge-yes{background:#1a2d1e;color:#3fb950}
.badge-no{background:#2d1a1a;color:#f85149}
.slug{max-width:200px;overflow:hidden;text-overflow:ellipsis;color:#8b949e;font-size:12px}
.time-cell{color:#8b949e;font-size:12px;font-variant-numeric:tabular-nums}
.empty{text-align:center;padding:56px 0;color:#8b949e}
.empty p{margin-top:8px;font-size:13px}

/* ── Analytics ── */
.analytics-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;padding:24px 28px}
@media(max-width:900px){.analytics-grid{grid-template-columns:1fr}}
.chart-card{background:#161b22;border:1px solid #21262d;border-radius:12px;padding:22px}
.chart-card.full{grid-column:1/-1}
.chart-title{font-size:13px;font-weight:600;color:#8b949e;margin-bottom:16px}
.chart-wrap{position:relative;height:220px}
.pro-lock{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:60px 0;color:#8b949e;gap:10px}
.pro-lock h3{font-size:16px;color:#e6edf3}
.pro-lock p{font-size:13px;text-align:center;max-width:320px;line-height:1.5}
.upgrade-btn{margin-top:8px;padding:10px 24px;background:#8957e5;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer}
.upgrade-btn:hover{background:#a371f7}

/* ── Settings ── */
.settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;padding:24px 28px}
@media(max-width:700px){.settings-grid{grid-template-columns:1fr}}
.setting-card{background:#161b22;border:1px solid #21262d;border-radius:12px;padding:22px}
.setting-card h3{font-size:14px;font-weight:600;margin-bottom:16px;color:#e6edf3}
.setting-row{display:flex;justify-content:space-between;align-items:center;padding:10px 0;border-bottom:1px solid #21262d;font-size:13px}
.setting-row:last-child{border-bottom:none}
.setting-label{color:#8b949e}
.setting-val{color:#e6edf3;font-weight:500}
.setting-val.green{color:#3fb950}.setting-val.red{color:#f85149}
.license-form{display:flex;gap:10px;margin-top:16px}
.license-input{flex:1;background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:8px 12px;color:#e6edf3;font-size:13px;font-family:monospace}
.license-input:focus{outline:none;border-color:#8957e5}
.license-msg{margin-top:10px;font-size:12px;padding:8px 12px;border-radius:6px}
.license-msg.ok{background:#1a2d1e;color:#3fb950}.license-msg.err{background:#2d1a1a;color:#f85149}

/* ── Configure tab ── */
.configure-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;padding:24px 28px}
.config-card--traders{grid-column:1/-1}
@media(max-width:700px){.configure-grid{grid-template-columns:1fr}}
.config-card{background:#161b22;border:1px solid #21262d;border-radius:12px;padding:22px}
.config-card h3{font-size:14px;font-weight:600;margin-bottom:16px;color:#e6edf3}
.config-card.full-width{grid-column:1/-1}
.wager-toggle{display:flex;gap:0;margin-bottom:16px;border:1px solid #30363d;border-radius:8px;overflow:hidden}
.wager-toggle button{flex:1;padding:8px;background:transparent;border:none;color:#8b949e;font-size:13px;font-weight:600;cursor:pointer;transition:all .2s}
.wager-toggle button.active{background:#2ea043;color:#fff}
.wager-row{display:flex;align-items:center;gap:12px;margin-bottom:12px}
.wager-row label{font-size:13px;color:#8b949e;min-width:80px}
.config-select{background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:8px 12px;color:#e6edf3;font-size:13px;cursor:pointer;flex:1}
.config-select:focus{outline:none;border-color:#3fb950}
.config-save-btn{width:100%;padding:10px;background:#2ea043;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;margin-top:12px}
.config-save-btn:hover{background:#3fb950}
.config-msg{margin-top:8px;font-size:12px;padding:8px 12px;border-radius:6px;display:none}
.config-msg.ok{background:#1a2d1e;color:#3fb950}.config-msg.err{background:#2d1a1a;color:#f85149}
.trader-list{display:flex;flex-direction:column;gap:6px;max-height:380px;overflow-y:auto;margin-bottom:12px}
.trader-item{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:8px;background:#0d1117;border:1px solid #21262d;cursor:pointer;transition:border-color .2s}
.trader-item:hover{border-color:#3fb950}
.trader-item.is-selected{border-color:#2ea043;background:#0f1f13}
.trader-item input[type=checkbox]{accent-color:#3fb950;width:15px;height:15px;cursor:pointer;flex-shrink:0}
.trader-rank{font-size:11px;color:#8b949e;font-weight:700;min-width:26px;text-align:center}
.trader-rank.gold{color:#d4af37}.trader-rank.silver{color:#a0a0a0}.trader-rank.bronze{color:#cd7f32}
.trader-name{font-size:13px;font-weight:600;color:#e6edf3;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.trader-addr-small{font-size:10px;font-family:monospace;color:#8b949e}
.trader-stats{display:flex;gap:14px;flex-shrink:0}
.trader-stat{text-align:right}
.trader-stat-val{font-size:13px;font-weight:600;color:#e6edf3}
.trader-stat-val.green{color:#3fb950}.trader-stat-val.red{color:#f85149}
.trader-stat-lbl{font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.4px}
.trader-loading{color:#8b949e;font-size:13px;text-align:center;padding:32px 0}
.trader-empty{color:#8b949e;font-size:13px;text-align:center;padding:32px 0}
.trader-empty span{display:block;font-size:24px;margin-bottom:8px}
.trader-input-row{display:flex;gap:8px;margin-top:4px}
.trader-input{flex:1;background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:8px 12px;color:#e6edf3;font-size:12px;font-family:monospace}
.trader-input:focus{outline:none;border-color:#3fb950}
.trader-add-btn{padding:8px 14px;background:#21262d;border:1px solid #30363d;border-radius:8px;color:#e6edf3;font-size:12px;cursor:pointer;white-space:nowrap}
.trader-add-btn:hover{border-color:#3fb950;color:#3fb950}
.cat-section{margin-bottom:12px}
.cat-header{display:flex;align-items:center;gap:10px;padding:8px 0;cursor:pointer;border-bottom:1px solid #21262d}
.cat-icon{font-size:16px;width:24px;text-align:center}
.cat-label{flex:1;font-size:13px;font-weight:600;color:#e6edf3}
.cat-toggle{position:relative;display:inline-block;width:36px;height:20px;flex-shrink:0}
.cat-toggle input{opacity:0;width:0;height:0}
.cat-slider{position:absolute;inset:0;background:#21262d;border-radius:20px;cursor:pointer;transition:.3s}
.cat-slider:before{content:'';position:absolute;height:14px;width:14px;left:3px;bottom:3px;background:#8b949e;border-radius:50%;transition:.3s}
.cat-toggle input:checked+.cat-slider{background:#2ea043}
.cat-toggle input:checked+.cat-slider:before{transform:translateX(16px);background:#fff}
.cat-children{padding:8px 0 4px 32px;display:flex;flex-wrap:wrap;gap:8px}
.sport-chip{padding:5px 12px;border-radius:20px;border:1px solid #30363d;background:#0d1117;font-size:12px;color:#8b949e;cursor:pointer;transition:all .2s}
.sport-chip:hover{border-color:#3fb950;color:#3fb950}
.sport-chip.active{background:#1a2d1e;border-color:#2ea043;color:#3fb950;font-weight:600}
.scan-toggle-row{display:flex;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid #21262d}
.scan-toggle-row:last-child{border-bottom:none}
.scan-label{font-size:13px;color:#e6edf3}
.scan-sub{font-size:11px;color:#8b949e;margin-top:2px}

/* ── Close position button ── */
.btn-close-pos{padding:5px 14px;border-radius:6px;border:1px solid rgba(248,81,73,.4);background:rgba(45,26,26,.6);color:#f85149;font-size:12px;font-weight:600;cursor:pointer;transition:all .2s;white-space:nowrap}
.btn-close-pos:hover{background:#da3633;border-color:#da3633;color:#fff}
.btn-close-pos:disabled{opacity:.4;cursor:not-allowed}
.pos-title{max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:#c9d1d9}

/* ── ToS modal ── */
.tos-bg{position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:200;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(8px)}
.tos-modal{background:#161b22;border:1px solid #30363d;border-radius:20px;padding:0;max-width:560px;width:94%;overflow:hidden;box-shadow:0 24px 80px rgba(0,0,0,.7)}
.tos-header{padding:28px 32px 20px;border-bottom:1px solid #21262d;text-align:center}
.tos-header h2{font-size:20px;font-weight:700;color:#e6edf3;margin-bottom:4px}
.tos-header p{font-size:12px;color:#8b949e}
.tos-body{padding:20px 32px;max-height:340px;overflow-y:auto;font-size:12px;color:#8b949e;line-height:1.8}
.tos-body h4{color:#e6edf3;font-size:13px;margin:14px 0 4px}
.tos-body p{margin-bottom:8px}
.tos-body strong{color:#f85149}
.tos-footer{padding:20px 32px;border-top:1px solid #21262d;display:flex;flex-direction:column;gap:10px}
.tos-check{display:flex;align-items:flex-start;gap:10px;font-size:13px;color:#8b949e;cursor:pointer}
.tos-check input{accent-color:#3fb950;width:16px;height:16px;margin-top:1px;flex-shrink:0;cursor:pointer}
.tos-agree-btn{width:100%;padding:13px;background:#21262d;color:#8b949e;border:none;border-radius:10px;font-size:14px;font-weight:600;cursor:not-allowed;transition:all .2s}
.tos-agree-btn.ready{background:#2ea043;color:#fff;cursor:pointer}
.tos-agree-btn.ready:hover{background:#3fb950}

/* ── Onboarding modal ── */
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.75);z-index:100;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(6px)}
.modal{background:#161b22;border:1px solid #30363d;border-radius:20px;padding:0;max-width:520px;width:93%;overflow:hidden;box-shadow:0 24px 80px rgba(0,0,0,.6)}
.modal-header{padding:28px 32px 0;text-align:center}
.modal-header .cat-icon-big{font-size:48px;margin-bottom:12px;display:block}
.modal-header h2{font-size:22px;font-weight:700;margin-bottom:6px;color:#e6edf3}
.modal-header p{color:#8b949e;font-size:13px;line-height:1.6}
/* Step tracker */
.modal-steps{display:flex;align-items:center;justify-content:center;gap:0;padding:24px 32px 0}
.modal-step{display:flex;flex-direction:column;align-items:center;gap:6px;flex:1}
.step-circle{width:32px;height:32px;border-radius:50%;border:2px solid #30363d;background:#0d1117;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;color:#8b949e;transition:all .3s}
.step-circle.done{background:#2ea043;border-color:#2ea043;color:#fff}
.step-circle.active{background:#0d1117;border-color:#3fb950;color:#3fb950;box-shadow:0 0 0 3px rgba(63,185,80,.2)}
.step-label{font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;white-space:nowrap}
.step-label.active{color:#3fb950}
.step-line{flex:1;height:2px;background:#21262d;margin-bottom:18px;max-width:60px}
.step-line.done{background:#2ea043}
/* Step panes */
.modal-body{padding:24px 32px 32px}
.modal-input{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:10px;padding:11px 14px;color:#e6edf3;font-size:13px;font-family:monospace;margin-bottom:12px}
.modal-input:focus{outline:none;border-color:#8957e5}
.modal-btn{width:100%;padding:13px;background:#2ea043;color:#fff;border:none;border-radius:10px;font-size:14px;font-weight:600;cursor:pointer;margin-bottom:8px;transition:background .2s}
.modal-btn:hover{background:#3fb950}
.modal-btn.secondary{background:transparent;color:#8b949e;border:1px solid #30363d}
.modal-btn.secondary:hover{color:#e6edf3;border-color:#8b949e}
.modal-btn:disabled{opacity:.5;cursor:not-allowed}
.modal-error{color:#f85149;font-size:12px;margin-bottom:10px;padding:8px 12px;background:#2d1a1a;border-radius:6px}
.modal-success{color:#3fb950;font-size:12px;margin-bottom:10px;padding:8px 12px;background:#1a2d1e;border-radius:6px}
/* Command block */
.cmd-block{background:#0d1117;border:1px solid #30363d;border-radius:10px;padding:14px 16px;margin:16px 0;display:flex;align-items:center;justify-content:space-between;gap:12px}
.cmd-text{font-family:monospace;font-size:15px;color:#3fb950;font-weight:600}
.cmd-copy{background:#21262d;border:1px solid #30363d;border-radius:6px;color:#8b949e;font-size:11px;font-weight:600;padding:5px 10px;cursor:pointer;white-space:nowrap;transition:all .2s}
.cmd-copy:hover{background:#30363d;color:#e6edf3}
.cmd-copy.copied{background:#1a2d1e;border-color:#2ea043;color:#3fb950}
/* Connection status */
.conn-status{display:flex;align-items:center;gap:10px;padding:12px 16px;border-radius:10px;margin:16px 0;font-size:13px;font-weight:500}
.conn-status.checking{background:#1c2128;color:#8b949e;border:1px solid #30363d}
.conn-status.connected{background:#1a2d1e;color:#3fb950;border:1px solid #2ea043}
.conn-status.disconnected{background:#2d1a1a;color:#f85149;border:1px solid #da3633}
.conn-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.conn-status.checking .conn-dot{background:#8b949e}
.conn-status.connected .conn-dot{background:#3fb950;box-shadow:0 0 6px #3fb950;animation:pulse 2s infinite}
.conn-status.disconnected .conn-dot{background:#f85149}
/* Step 3 checklist */
.check-list{list-style:none;text-align:left;margin:16px 0}
.check-list li{display:flex;align-items:center;gap:10px;padding:8px 0;font-size:13px;color:#8b949e;border-bottom:1px solid #21262d}
.check-list li:last-child{border-bottom:none}
.check-list li.ok{color:#e6edf3}
.check-list li .chk{font-size:16px;width:20px;text-align:center}
</style>
</head>
<body>

<!-- Terms of Service modal -->
<div class="tos-bg" id="tosModal" style="display:none">
  <div class="tos-modal">
    <div class="tos-header">
      <h2>⚠️ Risk Disclosure &amp; Terms of Use</h2>
      <p>Please read and agree before using CopyCat</p>
    </div>
    <div class="tos-body">
      <h4>1. Not Financial Advice</h4>
      <p>CopyCat is an automation tool only. Nothing produced by this software constitutes financial advice, investment advice, or a recommendation to buy or sell any financial instrument. You are solely responsible for all trading decisions made through this platform.</p>

      <h4>2. <strong>No Liability for Losses</strong></h4>
      <p><strong>Trading on prediction markets involves substantial risk of loss.</strong> You may lose some or all of the funds you deploy. The developers and operators of CopyCat accept no responsibility whatsoever for any financial losses, missed trades, software errors, or any other damages arising from your use of this software.</p>

      <h4>3. Past Performance</h4>
      <p>Historical performance of any trader shown in CopyCat does not guarantee future results. Markets are unpredictable. Copying a top trader does not ensure profits.</p>

      <h4>4. You Assume All Risk</h4>
      <p>By using CopyCat you acknowledge that: (a) you are trading your own funds at your own risk; (b) you have read and understood the risks; (c) CopyCat and its creators bear zero financial or legal responsibility for outcomes.</p>

      <h4>5. No Warranties</h4>
      <p>This software is provided "as is" without warranty of any kind. We do not guarantee uptime, accuracy of trade execution, or that the bot will function without errors.</p>

      <h4>6. Jurisdiction</h4>
      <p>You are responsible for ensuring your use of prediction markets and this software complies with all laws in your jurisdiction. CopyCat makes no representations about legality of use in any specific location.</p>
    </div>
    <div class="tos-footer">
      <label class="tos-check">
        <input type="checkbox" id="tosCheck" onchange="tosCheckChanged()">
        I have read and agree to the Risk Disclosure and Terms of Use. I understand I may lose money and that CopyCat bears no financial responsibility for my trading outcomes.
      </label>
      <button class="tos-agree-btn" id="tosAgreeBtn" onclick="tosAgree()" disabled>
        I Agree — Enter Dashboard
      </button>
    </div>
  </div>
</div>

<!-- Onboarding modal -->
<div class="modal-bg" id="onboarding" style="display:none">
  <div class="modal">

    <!-- Step tracker -->
    <div class="modal-header">
      <span class="cat-icon-big">🐱</span>
      <h2>Welcome to CopyCat</h2>
      <p>Mirror the best Polymarket traders automatically.<br>Let's get you set up in 3 steps.</p>
    </div>

    <div class="modal-steps">
      <div class="modal-step">
        <div class="step-circle active" id="sc0">1</div>
        <div class="step-label active" id="sl0">License</div>
      </div>
      <div class="step-line" id="line01"></div>
      <div class="modal-step">
        <div class="step-circle" id="sc1">2</div>
        <div class="step-label" id="sl1">Connect</div>
      </div>
      <div class="step-line" id="line12"></div>
      <div class="modal-step">
        <div class="step-circle" id="sc2">3</div>
        <div class="step-label" id="sl2">Ready</div>
      </div>
    </div>

    <div class="modal-body">

      <!-- Step 1: License -->
      <div id="ob-step0">
        <p style="color:#8b949e;font-size:13px;margin-bottom:16px">Enter the license key from your purchase email.</p>
        <input class="modal-input" id="ob-key" placeholder="CB-PRO-XXXXXXXXXXXXXXXX" autocomplete="off" />
        <div class="modal-error" id="ob-err" style="display:none"></div>
        <button class="modal-btn" onclick="obActivate()">Activate License →</button>
        <button class="modal-btn secondary" onclick="obGoStep(1)">I'll activate later</button>
      </div>

      <!-- Step 2: Bullpen login -->
      <div id="ob-step1" style="display:none">
        <p style="color:#8b949e;font-size:13px;margin-bottom:4px">CopyCat uses <strong style="color:#e6edf3">Bullpen CLI</strong> to connect to your Polymarket wallet — no password needed.</p>

        <div class="cmd-block">
          <span class="cmd-text">bullpen login</span>
          <button class="cmd-copy" id="copyBtn" onclick="copyCmd()">Copy</button>
        </div>

        <ol style="font-size:12px;color:#8b949e;line-height:1.8;margin-bottom:16px;padding-left:18px">
          <li>Copy the command above</li>
          <li>Open <strong style="color:#e6edf3">Terminal</strong> (press ⌘ Space → type Terminal)</li>
          <li>Paste &amp; press Enter — a browser window will open</li>
          <li>Approve the connection in your browser</li>
          <li>Come back here and click <strong style="color:#3fb950">Check Connection</strong></li>
        </ol>

        <div class="conn-status checking" id="connStatus">
          <div class="conn-dot"></div>
          <span id="connText">Not checked yet — click below to verify</span>
        </div>

        <button class="modal-btn" id="checkConnBtn" onclick="obCheckConn()">Check Connection</button>
        <button class="modal-btn secondary" onclick="obGoStep(2)">Skip — I'll connect later</button>
      </div>

      <!-- Step 3: All done -->
      <div id="ob-step2" style="display:none">
        <p style="color:#8b949e;font-size:13px;margin-bottom:4px">You're all set. Here's your setup summary:</p>
        <ul class="check-list" id="readyChecklist">
          <li id="ck-license"><span class="chk">⬜</span> License activated</li>
          <li id="ck-bullpen"><span class="chk">⬜</span> Polymarket connected via Bullpen</li>
          <li class="ok"><span class="chk">✅</span> Dashboard running</li>
        </ul>
        <p style="font-size:12px;color:#8b949e;margin-bottom:16px;line-height:1.6">Hit <strong style="color:#3fb950">Start Bot</strong> on the Overview tab when you're ready to begin copying trades.</p>
        <button class="modal-btn" onclick="obDone()">Open Dashboard →</button>
      </div>

    </div>
  </div>
</div>

<!-- Header -->
<header>
  <div class="logo">Copy<span>Cat</span></div>
  <div class="header-right">
    <div class="tier-badge trial" id="tierBadge">Loading…</div>
    <div class="status-pill stopped" id="statusPill">
      <div class="status-dot"></div>
      <span id="statusText">Stopped</span>
    </div>
    <button class="btn btn-start" id="botBtn" onclick="toggleBot()">Start Bot</button>
    <button id="install-btn" class="btn btn-ghost" onclick="installApp()" style="display:none;align-items:center;gap:6px;font-size:12px;padding:6px 12px">
      ⬇️ Install App
    </button>
    <div class="last-ref" id="lastRef">—</div>
  </div>
</header>

<!-- Tabs -->
<div class="tabs">
  <div class="tab active" onclick="showTab('overview',this)">Overview</div>
  <div class="tab pro-tab" onclick="showTab('analytics',this)">Analytics<span class="pro-pip"></span></div>
  <div class="tab" onclick="showTab('configure',this)">Configure</div>
  <div class="tab" onclick="showTab('settings',this)">Settings</div>
</div>

<!-- Overview tab -->
<div class="tab-pane active" id="tab-overview">
  <div class="stats">
    <div class="card">
      <div class="card-label">Realized P&L</div>
      <div class="card-value dim" id="sPnl">—</div>
      <div class="card-sub" id="sInvested">—</div>
    </div>
    <div class="card">
      <div class="card-label">Win Rate</div>
      <div class="card-value dim" id="sWinRate">—</div>
      <div class="card-sub" id="sResolved">—</div>
    </div>
    <div class="card">
      <div class="card-label">Total Trades</div>
      <div class="card-value dim" id="sTrades">—</div>
      <div class="card-sub" id="sBuySell">—</div>
    </div>
    <div class="card">
      <div class="card-label">Winners</div>
      <div class="card-value green" id="sWinners">—</div>
      <div class="card-sub">completed round-trips</div>
    </div>
    <div class="card">
      <div class="card-label">Losers</div>
      <div class="card-value red" id="sLosers">—</div>
      <div class="wl-bar"><div class="wl-win" id="wlW" style="width:0%"></div><div class="wl-loss" id="wlL" style="width:100%"></div></div>
    </div>
  </div>

  <div class="section">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
      <div class="section-title" style="margin-bottom:0">Open Positions</div>
      <button class="btn btn-ghost" style="font-size:12px;padding:5px 12px" onclick="loadPositions()">↺ Refresh</button>
    </div>
    <div id="positionsWrap">
      <div class="table-wrap">
        <table>
          <thead><tr><th>Market</th><th>Outcome</th><th>Shares</th><th>Est. Value</th><th>Price</th><th style="text-align:right">Action</th></tr></thead>
          <tbody id="positionsBody"><tr><td colspan="6"><div class="empty">Loading positions…</div></td></tr></tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Copied Trades</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Time</th><th>Trader</th><th>Market</th><th>Outcome</th><th>Action</th><th>Amount</th><th>Status</th></tr></thead>
        <tbody id="tradeBody"><tr><td colspan="7"><div class="empty">Loading…</div></td></tr></tbody>
      </table>
    </div>
  </div>
</div>

<!-- Analytics tab (Pro) -->
<div class="tab-pane" id="tab-analytics">
  <div id="analytics-locked" class="pro-lock" style="display:none">
    <span style="font-size:32px">📊</span>
    <h3>Pro Analytics</h3>
    <p>Upgrade to Pro to unlock P&L charts, per-trader breakdowns, and deeper performance insights.</p>
    <button class="upgrade-btn" onclick="showTab('settings',document.querySelector('.tab:last-child'))">Upgrade → Settings</button>
  </div>
  <div id="analytics-content" style="display:none">
    <div class="analytics-grid">
      <div class="chart-card full">
        <div class="chart-title">Cumulative P&L Over Time</div>
        <div class="chart-wrap"><canvas id="pnlChart"></canvas></div>
      </div>
      <div class="chart-card full">
        <div class="chart-title">Per-Trader Performance</div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Trader</th><th>Trades Copied</th><th>Wins</th><th>Losses</th><th>Win Rate</th><th>P&L Attributed</th></tr></thead>
            <tbody id="traderBody"><tr><td colspan="6"><div class="empty">No data yet.</div></td></tr></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Configure tab -->
<div class="tab-pane" id="tab-configure">
  <div class="configure-grid">

    <!-- Wager Sizing -->
    <div class="config-card">
      <h3>Wager Sizing</h3>
      <div class="wager-toggle">
        <button id="wagerFixedBtn" class="active" onclick="setWagerMode('fixed')">Fixed $</button>
        <button id="wagerPctBtn" onclick="setWagerMode('pct')">% of Balance</button>
      </div>
      <div class="wager-row" id="wagerFixedRow">
        <label>Per trade</label>
        <select class="config-select" id="wagerFixedVal" onchange="markConfigDirty()">
          <option value="5">$5</option>
          <option value="10" selected>$10</option>
          <option value="20">$20</option>
          <option value="50">$50</option>
          <option value="100">$100</option>
          <option value="200">$200</option>
          <option value="500">$500</option>
          <option value="1000">$1,000</option>
          <option value="2500">$2,500</option>
          <option value="5000">$5,000</option>
        </select>
      </div>
      <div class="wager-row" id="wagerPctRow" style="display:none">
        <label>% per trade</label>
        <select class="config-select" id="wagerPctVal" onchange="markConfigDirty()">
          <option value="1">1%</option>
          <option value="2" selected>2%</option>
          <option value="3">3%</option>
          <option value="5">5%</option>
          <option value="10">10%</option>
          <option value="15">15%</option>
          <option value="20">20%</option>
          <option value="25">25%</option>
          <option value="50">50%</option>
          <option value="100">100%</option>
        </select>
      </div>
      <p id="wagerLimitHint" style="font-size:11px;color:#f0a500;margin-top:4px;display:none"></p>
      <div class="wager-row" style="margin-top:8px;padding-top:12px;border-top:1px solid #21262d">
        <label style="color:#f85149;font-size:13px;min-width:80px">Daily Stop</label>
        <select class="config-select" id="dailyLossLimit" onchange="markConfigDirty()" style="border-color:rgba(248,81,73,.3)">
          <option value="25">-$25</option>
          <option value="50">-$50</option>
          <option value="100" selected>-$100</option>
          <option value="200">-$200</option>
          <option value="500">-$500</option>
          <option value="1000">-$1,000</option>
          <option value="9999">No limit</option>
        </select>
      </div>
      <p style="font-size:11px;color:#8b949e;margin-top:6px">Bot auto-pauses for the day when this loss is reached.</p>
      <button class="config-save-btn" onclick="saveWager()">Save Wager Settings</button>
      <div class="config-msg" id="wagerMsg"></div>
    </div>

    <!-- Multi-market scanning -->
    <div class="config-card">
      <h3>Scanning Mode</h3>
      <div class="scan-toggle-row">
        <div>
          <div class="scan-label">Scan All Markets Simultaneously</div>
          <div class="scan-sub">Copies trades across all filtered markets at once</div>
        </div>
        <label class="cat-toggle" style="margin-left:12px">
          <input type="checkbox" id="scanAllToggle" onchange="saveScanMode()">
          <span class="cat-slider"></span>
        </label>
      </div>
      <div style="margin-top:16px;font-size:12px;color:#8b949e;line-height:1.6">
        <strong style="color:#e6edf3">On:</strong> Monitors all matching markets at once for maximum coverage.<br>
        <strong style="color:#e6edf3">Off:</strong> Scans markets sequentially (lower resource usage).
      </div>
    </div>

    <!-- Trader Selector (Pro) -->
    <div class="config-card config-card--traders" id="traderSelectorCard">
      <h3>Trader Selection <span class="pro-pip" style="margin-left:6px"></span></h3>
      <div id="traderSelectorLocked" style="display:none;color:#8b949e;font-size:13px;padding:12px 0">
        Upgrade to Pro to select specific traders to copy.
      </div>
      <div id="traderSelectorContent">
        <p style="font-size:12px;color:#8b949e;margin-bottom:12px">Browse top traders by category. Check the ones you want to copy.</p>

        <!-- Category + limit selectors -->
        <div style="display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap">
          <div style="flex:1;min-width:160px">
            <div style="font-size:11px;color:#8b949e;margin-bottom:5px;text-transform:uppercase;letter-spacing:.6px">Category</div>
            <select class="config-select" id="traderCatSelect" onchange="loadTradersByCategory()">
              <option value="">🌐 All Categories</option>
              <optgroup label="── Crypto ──">
                <option value="bitcoin">₿ Bitcoin</option>
                <option value="ethereum">Ξ Ethereum</option>
                <option value="solana">◎ Solana</option>
                <option value="altcoins">🪙 Altcoins</option>
              </optgroup>
              <optgroup label="── Sports ──">
                <option value="nfl">🏈 NFL</option>
                <option value="nba">🏀 NBA</option>
                <option value="mlb">⚾ MLB</option>
                <option value="nhl">🏒 NHL</option>
                <option value="soccer">⚽ Soccer</option>
                <option value="mma">🥊 MMA / UFC</option>
                <option value="golf">⛳ Golf</option>
                <option value="tennis">🎾 Tennis</option>
                <option value="f1">🏎️ Formula 1</option>
              </optgroup>
              <optgroup label="── Politics ──">
                <option value="us_elections">🗳️ US Elections</option>
                <option value="trump">🇺🇸 Trump / Admin</option>
                <option value="congress">🏛️ Congress</option>
                <option value="international">🌍 International</option>
              </optgroup>
              <optgroup label="── Economics ──">
                <option value="fed">🏦 Federal Reserve</option>
                <option value="inflation">💵 Inflation / CPI</option>
                <option value="stocks">📊 Stock Market</option>
                <option value="gdp">💼 GDP / Jobs</option>
              </optgroup>
            </select>
          </div>
          <div style="min-width:100px">
            <div style="font-size:11px;color:#8b949e;margin-bottom:5px;text-transform:uppercase;letter-spacing:.6px">Show Top</div>
            <select class="config-select" id="traderLimitSelect" onchange="loadTradersByCategory()">
              <option value="10">Top 10</option>
              <option value="20" selected>Top 20</option>
            </select>
          </div>
          <div style="display:flex;align-items:flex-end">
            <button class="trader-add-btn" onclick="loadTradersByCategory(true)" style="height:38px">↺ Refresh</button>
          </div>
        </div>

        <!-- Trader list -->
        <div class="trader-list" id="traderList">
          <div style="color:#8b949e;font-size:13px;text-align:center;padding:24px 0">Select a category above…</div>
        </div>

        <!-- Selected count badge -->
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px;margin-bottom:6px">
          <span id="traderSelCount" style="font-size:12px;color:#8b949e">0 traders selected</span>
          <button onclick="clearAllTraders()" style="font-size:12px;color:#8b949e;background:none;border:none;cursor:pointer;text-decoration:underline">Clear all</button>
        </div>

        <!-- Manual add -->
        <div class="trader-input-row">
          <input class="trader-input" id="newTraderAddr" placeholder="Paste trader address manually…" />
          <button class="trader-add-btn" onclick="addTraderManually()">+ Add</button>
        </div>
        <button class="config-save-btn" onclick="saveTraders()">Save Trader Selection</button>
        <div class="config-msg" id="traderMsg"></div>
      </div>
    </div>

    <!-- Category Filters (Pro) -->
    <div class="config-card" id="categoryCard">
      <h3>Category Filters <span class="pro-pip" style="margin-left:6px"></span></h3>
      <div id="categoryLocked" style="display:none;color:#8b949e;font-size:13px;padding:12px 0">
        Upgrade to Pro to filter by category and sport.
      </div>
      <div id="categoryContent">
        <p style="font-size:12px;color:#8b949e;margin-bottom:12px">Enable categories and drill down to specific sports or teams.</p>
        <div id="catSections"></div>
        <button class="config-save-btn" onclick="saveCategories()">Save Filters</button>
        <div class="config-msg" id="catMsg"></div>
      </div>
    </div>

  </div>
</div>

<!-- Settings tab -->
<div class="tab-pane" id="tab-settings">
  <div class="settings-grid">
    <div class="setting-card">
      <h3>License</h3>
      <div class="setting-row"><span class="setting-label">Status</span><span class="setting-val green" id="setLicStatus">—</span></div>
      <div class="setting-row"><span class="setting-label">Tier</span><span class="setting-val" id="setLicTier">—</span></div>
      <div class="setting-row"><span class="setting-label">Max Traders</span><span class="setting-val" id="setMaxTraders">—</span></div>
      <div class="setting-row"><span class="setting-label">Pro Analytics</span><span class="setting-val" id="setProAnalytics">—</span></div>
      <div class="setting-row"><span class="setting-label">Kalshi Bot</span><span class="setting-val" id="setKalshi">—</span></div>
      <div class="license-form">
        <input class="license-input" id="licKeyInput" placeholder="CB-PRO-XXXXXXXXXXXXXXXX" />
        <button class="btn btn-ghost" onclick="activateLicense()">Activate</button>
      </div>
      <div class="license-msg" id="licMsg" style="display:none"></div>
    </div>

    <div class="setting-card">
      <h3>Bot Configuration</h3>
      <div class="setting-row"><span class="setting-label">Trade Amount</span><span class="setting-val">$10 per trade</span></div>
      <div class="setting-row"><span class="setting-label">Daily Loss Limit</span><span class="setting-val">$100</span></div>
      <div class="setting-row"><span class="setting-label">Poll Interval</span><span class="setting-val">30 seconds</span></div>
      <div class="setting-row"><span class="setting-label">Log File</span><span class="setting-val" style="font-size:11px;color:#8b949e">trades_log.jsonl</span></div>
      <p style="margin-top:14px;font-size:12px;color:#8b949e;line-height:1.5">Edit <code style="background:#0d1117;padding:2px 6px;border-radius:4px">.env</code> in the CopyCat folder to change these values.</p>
    </div>
  </div>
</div>

<script>
let botRunning = false;
let isPro = false;
let pnlChart = null;
let obStep = 0;

// ── Onboarding ──────────────────────────────────────────────────────────────

let obLicenseValid = false;
let obConnected    = false;

async function checkOnboarding() {
  const [lic, conn] = await Promise.all([
    fetch('/api/license').then(r => r.json()),
    fetch('/api/connection').then(r => r.json()).catch(() => ({})),
  ]);
  obLicenseValid = lic.valid;
  obConnected    = conn.logged_in;
  if (!lic.valid || !conn.logged_in) {
    document.getElementById('onboarding').style.display = 'flex';
    // If license already valid, jump to step 2
    if (lic.valid) obGoStep(1);
  }
}

function obGoStep(n) {
  // Hide all step panes
  [0,1,2].forEach(i => {
    document.getElementById(`ob-step${i}`).style.display = i === n ? 'block' : 'none';
  });
  // Update step circles
  [0,1,2].forEach(i => {
    const circ  = document.getElementById(`sc${i}`);
    const label = document.getElementById(`sl${i}`);
    if (i < n) {
      circ.className  = 'step-circle done';
      circ.textContent = '✓';
      label.className = 'step-label';
    } else if (i === n) {
      circ.className  = 'step-circle active';
      circ.textContent = i + 1;
      label.className = 'step-label active';
    } else {
      circ.className  = 'step-circle';
      circ.textContent = i + 1;
      label.className = 'step-label';
    }
  });
  // Update connector lines
  if (document.getElementById('line01'))
    document.getElementById('line01').className = 'step-line' + (n > 0 ? ' done' : '');
  if (document.getElementById('line12'))
    document.getElementById('line12').className = 'step-line' + (n > 1 ? ' done' : '');
  // If going to step 3, populate checklist
  if (n === 2) obPopulateChecklist();
}

async function obActivate() {
  const key = document.getElementById('ob-key').value.trim();
  const err = document.getElementById('ob-err');
  err.style.display = 'none';
  if (!key) { err.textContent = 'Please enter your license key.'; err.style.display = 'block'; return; }
  const res = await fetch('/api/license', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({key})
  }).then(r => r.json());
  if (res.valid) {
    obLicenseValid = true;
    obGoStep(1);
  } else {
    err.textContent = res.error || 'Invalid license key — check your email and try again.';
    err.style.display = 'block';
  }
}

function copyCmd() {
  navigator.clipboard.writeText('bullpen login').then(() => {
    const btn = document.getElementById('copyBtn');
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('copied'); }, 2000);
  });
}

async function obCheckConn() {
  const btn    = document.getElementById('checkConnBtn');
  const status = document.getElementById('connStatus');
  const text   = document.getElementById('connText');
  btn.disabled = true;
  btn.textContent = 'Checking…';
  status.className = 'conn-status checking';
  text.textContent = 'Checking connection…';

  const conn = await fetch('/api/connection').then(r => r.json()).catch(() => ({}));
  btn.disabled = false;
  btn.textContent = 'Check Connection';

  if (conn.logged_in) {
    obConnected = true;
    status.className = 'conn-status connected';
    const short = conn.address ? conn.address.slice(0,8)+'…'+conn.address.slice(-6) : '';
    text.textContent = '✓ Connected' + (short ? `  ·  ${short}` : '');
    btn.textContent = 'Continue →';
    btn.onclick = () => obGoStep(2);
  } else if (!conn.bullpen_installed) {
    status.className = 'conn-status disconnected';
    text.textContent = '✗ Bullpen not found — run: brew install bullpenfi/tap/bullpen';
  } else {
    status.className = 'conn-status disconnected';
    text.textContent = '✗ Not connected — open Terminal, run bullpen login, then try again';
  }
}

function obPopulateChecklist() {
  const licEl = document.getElementById('ck-license');
  const conEl = document.getElementById('ck-bullpen');
  if (obLicenseValid) {
    licEl.className = 'ok';
    licEl.innerHTML = '<span class="chk">✅</span> License activated';
  } else {
    licEl.innerHTML = '<span class="chk">⚠️</span> License not activated — go to Settings';
  }
  if (obConnected) {
    conEl.className = 'ok';
    conEl.innerHTML = '<span class="chk">✅</span> Polymarket connected via Bullpen';
  } else {
    conEl.innerHTML = '<span class="chk">⚠️</span> Polymarket not connected — run bullpen login';
  }
}

function obDone() {
  document.getElementById('onboarding').style.display = 'none';
  refresh();
}

// ── Tabs ────────────────────────────────────────────────────────────────────

function showTab(name, el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('tab-' + name).classList.add('active');
  if (name === 'analytics') loadAnalytics();
  if (name === 'configure') loadConfigure();
  if (name === 'settings') loadSettings();
}

// ── Bot ─────────────────────────────────────────────────────────────────────

async function toggleBot() {
  const ep = botRunning ? '/api/bot/stop' : '/api/bot/start';
  if (!botRunning) requestNotifPermission();  // ask for notif permission on first start
  await fetch(ep, {method:'POST'});
  await new Promise(r => setTimeout(r, 800));
  refresh();
}

function updateBotStatus(running, tier, label, valid) {
  botRunning = running;
  const pill = document.getElementById('statusPill');
  const text = document.getElementById('statusText');
  const btn  = document.getElementById('botBtn');

  pill.className = 'status-pill' + (running ? '' : ' stopped');
  text.textContent = running ? 'Running' : 'Stopped';
  btn.className = 'btn ' + (running ? 'btn-stop' : 'btn-start');
  btn.textContent = running ? 'Stop Bot' : 'Start Bot';

  const badge = document.getElementById('tierBadge');
  badge.textContent = valid ? label : 'No License';
  badge.className = 'tier-badge ' + (tier || 'trial').toLowerCase().replace('_kalshi','_kalshi');

  isPro = (tier === 'PRO' || tier === 'PRO_KALSHI');
}

// ── Stats ───────────────────────────────────────────────────────────────────

function updateStats(s) {
  const pnl = s.realized_pnl;
  const pnlEl = document.getElementById('sPnl');
  pnlEl.textContent = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2);
  pnlEl.className = 'card-value ' + (pnl > 0 ? 'green' : pnl < 0 ? 'red' : 'dim');
  document.getElementById('sInvested').textContent = '$' + s.total_invested.toFixed(2) + ' deployed';

  const wr = s.win_rate;
  const wrEl = document.getElementById('sWinRate');
  wrEl.textContent = wr !== null ? wr + '%' : '—';
  wrEl.className = 'card-value ' + (wr >= 50 ? 'green' : wr !== null ? 'red' : 'dim');
  document.getElementById('sResolved').textContent = (s.winners + s.losers) + ' resolved';

  document.getElementById('sTrades').textContent = s.total_trades || '—';
  document.getElementById('sBuySell').textContent = s.total_buys + ' buys · ' + s.total_sells + ' sells';
  document.getElementById('sWinners').textContent = s.winners || '—';
  document.getElementById('sLosers').textContent  = s.losers  || '—';

  const tot = s.winners + s.losers;
  document.getElementById('wlW').style.width = tot > 0 ? (s.winners/tot*100)+'%' : '0%';
  document.getElementById('wlL').style.width = tot > 0 ? (s.losers/tot*100)+'%'  : '100%';
}

// ── Trade feed ───────────────────────────────────────────────────────────────

function updateTrades(trades) {
  const tbody = document.getElementById('tradeBody');
  if (!trades.length) {
    tbody.innerHTML = '<tr><td colspan="7"><div class="empty">No trades yet.<p>Start the bot and trades will appear here.</p></div></td></tr>';
    return;
  }
  tbody.innerHTML = trades.map(t => {
    const ab = t.action === 'BUY' ? '<span class="badge badge-buy">BUY</span>'
             : t.action === 'SELL' ? '<span class="badge badge-sell">SELL</span>'
             : '<span class="badge badge-err">ERR</span>';
    const ob = t.outcome === 'Yes' ? '<span class="badge badge-yes">YES</span>'
             : '<span class="badge badge-no">NO</span>';
    const st = t.type === 'error'
      ? '<span style="color:#f85149;font-size:11px">' + (t.error||'error') + '</span>'
      : (t.success ? '<span style="color:#3fb950">✓</span>' : '<span style="color:#f85149">✗</span>');
    return `<tr>
      <td class="time-cell">${t.time}</td>
      <td>${t.trader}</td>
      <td class="slug" title="${t.slug}">${t.slug}</td>
      <td>${t.type==='error'?'—':ob}</td>
      <td>${ab}</td>
      <td>${t.amount}</td>
      <td>${st}</td>
    </tr>`;
  }).join('');
}

// ── Pro Analytics ────────────────────────────────────────────────────────────

async function loadAnalytics() {
  if (!isPro) {
    document.getElementById('analytics-locked').style.display = 'flex';
    document.getElementById('analytics-content').style.display = 'none';
    return;
  }
  document.getElementById('analytics-locked').style.display = 'none';
  document.getElementById('analytics-content').style.display = 'block';

  const data = await fetch('/api/analytics').then(r => r.json()).catch(() => null);
  if (!data) return;

  // P&L chart
  const labels = data.pnl_series.map(d => d.date);
  const values = data.pnl_series.map(d => d.cumulative);
  const ctx = document.getElementById('pnlChart').getContext('2d');
  if (pnlChart) pnlChart.destroy();
  pnlChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Cumulative P&L ($)',
        data: values,
        borderColor: '#3fb950',
        backgroundColor: 'rgba(63,185,80,.08)',
        fill: true,
        tension: 0.3,
        pointRadius: 3,
        pointBackgroundColor: '#3fb950',
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#8b949e', font:{size:11} }, grid: { color: '#21262d' } },
        y: { ticks: { color: '#8b949e', font:{size:11}, callback: v => '$'+v.toFixed(2) }, grid: { color: '#21262d' } }
      }
    }
  });

  // Per-trader table
  const tbody = document.getElementById('traderBody');
  if (!data.traders.length) {
    tbody.innerHTML = '<tr><td colspan="6"><div class="empty">No trader data yet.</div></td></tr>';
    return;
  }
  tbody.innerHTML = data.traders.map(t => {
    const pnlColor = t.pnl >= 0 ? '#3fb950' : '#f85149';
    const wr = t.win_rate !== null ? t.win_rate + '%' : '—';
    return `<tr>
      <td style="font-weight:500">${t.trader}</td>
      <td>${t.trades}</td>
      <td style="color:#3fb950">${t.wins}</td>
      <td style="color:#f85149">${t.losses}</td>
      <td>${wr}</td>
      <td style="color:${pnlColor};font-weight:600">${t.pnl>=0?'+$':'-$'}${Math.abs(t.pnl).toFixed(2)}</td>
    </tr>`;
  }).join('');
}

// ── Settings ─────────────────────────────────────────────────────────────────

async function loadSettings() {
  const lic = await fetch('/api/license').then(r => r.json());
  document.getElementById('setLicStatus').textContent   = lic.valid ? 'Active' : 'Not activated';
  document.getElementById('setLicStatus').className     = 'setting-val ' + (lic.valid ? 'green' : 'red');
  document.getElementById('setLicTier').textContent     = lic.label || '—';
  document.getElementById('setMaxTraders').textContent  = lic.max_traders;
  document.getElementById('setProAnalytics').textContent = lic.pro_analytics ? '✓ Included' : '✗ Monthly only';
  document.getElementById('setKalshi').textContent      = lic.kalshi_bot ? '✓ Included' : '✗ Add-on ($99)';
}

async function activateLicense() {
  const key = document.getElementById('licKeyInput').value.trim();
  const res = await fetch('/api/license', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({key})
  }).then(r => r.json());
  const msg = document.getElementById('licMsg');
  msg.style.display = 'block';
  msg.className = 'license-msg ' + (res.valid ? 'ok' : 'err');
  msg.textContent = res.valid ? `✓ ${res.label} license activated!` : (res.error || 'Invalid key');
  if (res.valid) { await loadSettings(); refresh(); }
}

// ── Terms of Service ──────────────────────────────────────────────────────────

async function checkToS() {
  const cfg = await fetch('/api/config').then(r => r.json()).catch(() => ({}));
  if (!cfg.tos_accepted) {
    document.getElementById('tosModal').style.display = 'flex';
  } else {
    // ToS already accepted — go straight to onboarding check
    checkOnboarding();
  }
}

function tosCheckChanged() {
  const checked = document.getElementById('tosCheck').checked;
  const btn = document.getElementById('tosAgreeBtn');
  btn.disabled = !checked;
  btn.classList.toggle('ready', checked);
}

async function tosAgree() {
  await fetch('/api/config', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({tos_accepted: true})
  });
  document.getElementById('tosModal').style.display = 'none';
  // Now check onboarding
  checkOnboarding();
}

// ── Configure tab ─────────────────────────────────────────────────────────────

let configDirty = false;
let currentConfig = {};
let categoryHierarchy = {};
let knownTraders = [];

function markConfigDirty() { configDirty = true; }

async function loadConfigure() {
  const [cfg, catsRes, licRes] = await Promise.all([
    fetch('/api/config').then(r => r.json()),
    fetch('/api/categories').then(r => r.json()),
    fetch('/api/license').then(r => r.json()),
  ]);
  currentConfig = cfg;
  categoryHierarchy = catsRes;
  const proUser = licRes.pro_analytics;
  window._licRes = licRes;

  // Pre-populate selected traders from saved config
  selectedTraders = new Set(cfg.selected_traders || []);

  // Wager
  const mode = cfg.wager_mode || 'fixed';
  setWagerMode(mode, true);
  document.getElementById('wagerFixedVal').value  = String(cfg.wager_fixed      || 10);
  document.getElementById('wagerPctVal').value    = String(cfg.wager_pct        || 2);
  document.getElementById('dailyLossLimit').value = String(cfg.daily_loss_limit || 100);
  applyWagerLimits(licRes);

  // Scan mode
  document.getElementById('scanAllToggle').checked = !!cfg.scan_all_simultaneously;

  // Trader selector
  if (!proUser) {
    document.getElementById('traderSelectorLocked').style.display = 'block';
    document.getElementById('traderSelectorContent').style.display = 'none';
  } else {
    document.getElementById('traderSelectorLocked').style.display = 'none';
    document.getElementById('traderSelectorContent').style.display = 'block';
    updateSelCount();
    loadTradersByCategory();
  }

  // Category filters
  if (!proUser) {
    document.getElementById('categoryLocked').style.display = 'block';
    document.getElementById('categoryContent').style.display = 'none';
  } else {
    document.getElementById('categoryLocked').style.display = 'none';
    document.getElementById('categoryContent').style.display = 'block';
    renderCategories(categoryHierarchy, cfg.categories || {});
  }
}

function applyWagerLimits(lic) {
  const maxFixed = lic.max_wager    || 10000;
  const minFixed = lic.min_wager    || 5;
  const maxPct   = lic.max_wager_pct || 100;

  // Hide options outside tier limits
  document.querySelectorAll('#wagerFixedVal option').forEach(opt => {
    const v = parseInt(opt.value);
    opt.hidden = (v < minFixed || v > maxFixed);
  });
  document.querySelectorAll('#wagerPctVal option').forEach(opt => {
    opt.hidden = parseInt(opt.value) > maxPct;
  });

  // Clamp selected value if it's now hidden
  const fixedSel = document.getElementById('wagerFixedVal');
  if (fixedSel.selectedOptions[0]?.hidden) {
    fixedSel.value = String(Math.min(maxFixed, Math.max(minFixed,
      parseInt(fixedSel.value) || minFixed)));
  }
  const pctSel = document.getElementById('wagerPctVal');
  if (pctSel.selectedOptions[0]?.hidden) {
    pctSel.value = String(maxPct);
  }

  // Show tier hint for bounded tiers
  const hint = document.getElementById('wagerLimitHint');
  if (maxFixed < 10000) {
    hint.textContent = `Monthly plan: $${minFixed}–$${maxFixed} fixed or up to ${maxPct}% per trade. Upgrade to Pro for unlimited sizing.`;
    hint.style.display = 'block';
  } else {
    hint.style.display = 'none';
  }
}

function setWagerMode(mode, silent) {
  const isFixed = mode === 'fixed';
  document.getElementById('wagerFixedBtn').classList.toggle('active', isFixed);
  document.getElementById('wagerPctBtn').classList.toggle('active', !isFixed);
  document.getElementById('wagerFixedRow').style.display = isFixed ? 'flex' : 'none';
  document.getElementById('wagerPctRow').style.display   = isFixed ? 'none' : 'flex';
  if (!silent) markConfigDirty();
}

async function saveWager() {
  const mode      = document.getElementById('wagerFixedBtn').classList.contains('active') ? 'fixed' : 'pct';
  const fixed     = parseInt(document.getElementById('wagerFixedVal').value);
  const pct       = parseInt(document.getElementById('wagerPctVal').value);
  const dailyStop = parseInt(document.getElementById('dailyLossLimit').value);
  const res = await fetch('/api/config', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({wager_mode: mode, wager_fixed: fixed, wager_pct: pct, daily_loss_limit: dailyStop})
  }).then(r => r.json());
  showConfigMsg('wagerMsg', res.ok);
}

async function saveScanMode() {
  const val = document.getElementById('scanAllToggle').checked;
  await fetch('/api/config', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({scan_all_simultaneously: val})
  });
}

// ── Trader list (category-aware) ─────────────────────────────────────────────

let selectedTraders = new Set(); // persists across category switches
let currentTraderPage = [];

async function loadTradersByCategory(forceRefresh) {
  const cat   = document.getElementById('traderCatSelect').value;
  const limit = document.getElementById('traderLimitSelect').value;
  const container = document.getElementById('traderList');
  const catLabel  = document.getElementById('traderCatSelect').selectedOptions[0]?.text || 'traders';

  container.innerHTML = `<div class="trader-loading">
    <div style="margin-bottom:8px">🔍 Finding top ${limit} traders for <strong style="color:#e6edf3">${catLabel}</strong>…</div>
    <div style="font-size:11px;color:#8b949e">Searching markets &amp; ranking holders — may take a few seconds</div>
  </div>`;

  const qs  = `category=${encodeURIComponent(cat)}&limit=${limit}${forceRefresh ? '&refresh=1' : ''}`;
  const data = await fetch(`/api/traders?${qs}`).then(r => r.json()).catch(() => null);
  if (!data) {
    container.innerHTML = '<div class="trader-empty"><span>⚠️</span>Could not load traders. Is bullpen running?</div>';
    return;
  }

  currentTraderPage = data.traders || [];
  (data.selected || []).forEach(a => selectedTraders.add(a));
  renderTraderList(currentTraderPage);
  updateSelCount();
}

function rankClass(rank) {
  if (rank === 1) return 'gold';
  if (rank === 2) return 'silver';
  if (rank === 3) return 'bronze';
  return '';
}

function renderTraderList(traders) {
  const container = document.getElementById('traderList');
  if (!traders.length) {
    container.innerHTML = '<div class="trader-empty"><span>🔍</span>No traders found for this category yet.<br><small>Start the bot to accumulate trade data, or try "All Categories".</small></div>';
    return;
  }

  container.innerHTML = traders.map((t, i) => {
    const rank    = t.rank < 900 ? t.rank : i + 1;
    const sel     = selectedTraders.has(t.name) || selectedTraders.has(t.address);
    const chk     = sel ? 'checked' : '';
    const selCls  = sel ? 'is-selected' : '';
    const rkCls   = rankClass(rank);
    const pnl     = t.weekly_pnl || t.pnl || 0;
    const pnlStr  = pnl >= 0 ? `+$${pnl.toLocaleString()}` : `-$${Math.abs(pnl).toLocaleString()}`;
    const pnlCls  = pnl >= 0 ? 'green' : 'red';
    const wr      = t.win_rate !== null && t.win_rate !== undefined ? t.win_rate + '%' : '—';
    const catTr   = t.cat_trades > 0 ? `${t.cat_trades} in cat` : (t.trades ? `${t.trades} trades` : 'live data');
    const shortAddr = t.address && t.address.length > 12 ? t.address.slice(0,6)+'…'+t.address.slice(-4) : (t.address || '');

    return `<label class="trader-item ${selCls}" onclick="toggleTrader(event,'${t.name}','${t.address}')">
      <input type="checkbox" ${chk} value="${t.name}" onclick="event.stopPropagation();toggleTrader(event,'${t.name}','${t.address}')">
      <span class="trader-rank ${rkCls}">#${rank}</span>
      <div style="flex:1;min-width:0">
        <div class="trader-name">${t.name}</div>
        <div class="trader-addr-small">${shortAddr} · ${catTr}</div>
      </div>
      <div class="trader-stats">
        <div class="trader-stat">
          <div class="trader-stat-val ${pnlCls}">${pnlStr}</div>
          <div class="trader-stat-lbl">Weekly P&L</div>
        </div>
        <div class="trader-stat">
          <div class="trader-stat-val">${wr}</div>
          <div class="trader-stat-lbl">Win Rate</div>
        </div>
      </div>
    </label>`;
  }).join('');
}

function toggleTrader(e, name, addr) {
  // Avoid double-firing from checkbox click
  if (e.target.tagName === 'INPUT') {
    if (e.target.checked) { selectedTraders.add(name); if (addr) selectedTraders.add(addr); }
    else { selectedTraders.delete(name); selectedTraders.delete(addr); }
  } else {
    const key = name;
    if (selectedTraders.has(key)) { selectedTraders.delete(key); selectedTraders.delete(addr); }
    else { selectedTraders.add(key); if (addr) selectedTraders.add(addr); }
    // Update checkbox visually
    const cb = e.currentTarget.querySelector('input[type=checkbox]');
    if (cb) cb.checked = selectedTraders.has(key);
    e.currentTarget.classList.toggle('is-selected', selectedTraders.has(key));
  }
  updateSelCount();
  markConfigDirty();
}

function updateSelCount() {
  const el = document.getElementById('traderSelCount');
  if (el) el.textContent = selectedTraders.size > 0
    ? `${selectedTraders.size} trader(s) selected`
    : 'None selected — copies all tracked traders';
}

function clearAllTraders() {
  selectedTraders.clear();
  renderTraderList(currentTraderPage);
  updateSelCount();
  markConfigDirty();
}

function addTraderManually() {
  const inp  = document.getElementById('newTraderAddr');
  const addr = inp.value.trim();
  if (!addr) return;
  inp.value = '';
  selectedTraders.add(addr);
  currentTraderPage.unshift({name: addr, address: addr, trades: 0, pnl: 0, weekly_pnl: 0, win_rate: null, rank: 999, cat_trades: 0});
  renderTraderList(currentTraderPage);
  updateSelCount();
  markConfigDirty();
}

function getSelectedTraders() {
  return [...selectedTraders];
}

async function saveTraders() {
  const sel = getSelectedTraders();
  const res = await fetch('/api/config', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({selected_traders: sel})
  }).then(r => r.json());
  showConfigMsg('traderMsg', res.ok, sel.length ? `${sel.length} trader(s) saved` : 'Copying all tracked traders');
}

// ── Category filters ──────────────────────────────────────────────────────────

function renderCategories(hierarchy, savedCats) {
  const container = document.getElementById('catSections');
  container.innerHTML = '';
  for (const [catKey, cat] of Object.entries(hierarchy)) {
    const catCfg = savedCats[catKey] || {enabled: true, sports: [], teams: []};
    const enabledChk = catCfg.enabled ? 'checked' : '';
    const children = cat.children || {};

    let sportChips = '';
    for (const [sportKey, sport] of Object.entries(children)) {
      const isActive = (catCfg.sports || []).includes(sportKey);
      sportChips += `<div class="sport-chip ${isActive ? 'active' : ''}" data-cat="${catKey}" data-sport="${sportKey}" onclick="toggleSport(this)">${sport.icon || ''} ${sport.label}</div>`;
    }

    const childrenHtml = Object.keys(children).length
      ? `<div class="cat-children" id="catChildren-${catKey}">${sportChips}</div>` : '';

    container.innerHTML += `
      <div class="cat-section">
        <div class="cat-header">
          <span class="cat-icon">${cat.icon || ''}</span>
          <span class="cat-label">${cat.label}</span>
          <label class="cat-toggle" onclick="event.stopPropagation()">
            <input type="checkbox" ${enabledChk} data-cat="${catKey}" onchange="markConfigDirty()">
            <span class="cat-slider"></span>
          </label>
        </div>
        ${childrenHtml}
      </div>`;
  }
}

function toggleSport(el) {
  el.classList.toggle('active');
  markConfigDirty();
}

function getCategoryConfig() {
  const cats = {};
  document.querySelectorAll('.cat-section').forEach(sec => {
    const toggle = sec.querySelector('.cat-toggle input');
    if (!toggle) return;
    const catKey = toggle.dataset.cat;
    const enabled = toggle.checked;
    const sports = [...sec.querySelectorAll('.sport-chip.active')].map(c => c.dataset.sport);
    cats[catKey] = {enabled, sports, teams: []};
  });
  return cats;
}

async function saveCategories() {
  const categories = getCategoryConfig();
  const res = await fetch('/api/config', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({categories})
  }).then(r => r.json());
  showConfigMsg('catMsg', res.ok);
}

function showConfigMsg(id, ok, text) {
  const el = document.getElementById(id);
  el.style.display = 'block';
  el.className = 'config-msg ' + (ok ? 'ok' : 'err');
  el.textContent = ok ? ('✓ ' + (text || 'Saved!')) : '✗ Failed to save';
  setTimeout(() => { el.style.display = 'none'; }, 3000);
}

// ── Open Positions ────────────────────────────────────────────────────────────

async function loadPositions() {
  const tbody = document.getElementById('positionsBody');
  tbody.innerHTML = '<tr><td colspan="6"><div class="empty">Loading…</div></td></tr>';
  const data = await fetch('/api/positions').then(r => r.json()).catch(() => null);
  if (!data || data.error) {
    tbody.innerHTML = `<tr><td colspan="6"><div class="empty">Could not load positions.<p style="font-size:12px">${data?.error||'Is bullpen connected?'}</p></div></td></tr>`;
    return;
  }
  const positions = data.positions || [];
  if (!positions.length) {
    tbody.innerHTML = '<tr><td colspan="6"><div class="empty">No open positions.<p>The bot will show positions here once it places trades.</p></div></td></tr>';
    return;
  }
  tbody.innerHTML = positions.map(p => {
    const ob = p.outcome === 'Yes'
      ? '<span class="badge badge-yes">YES</span>'
      : '<span class="badge badge-no">NO</span>';
    const val = p.value > 0 ? `$${p.value.toFixed(2)}` : '—';
    const price = p.price > 0 ? (p.price < 1 ? `$${(p.price*100).toFixed(1)}¢` : `$${p.price.toFixed(2)}`) : '—';
    const slug = p.slug || '';
    const outcome = p.outcome || 'Yes';
    const shares = p.shares;
    return `<tr>
      <td class="pos-title" title="${p.title}">${p.title || slug}</td>
      <td>${ob}</td>
      <td style="font-variant-numeric:tabular-nums">${shares.toFixed(2)}</td>
      <td style="color:#3fb950;font-weight:600">${val}</td>
      <td style="color:#8b949e">${price}</td>
      <td style="text-align:right">
        <button class="btn-close-pos" id="close-${slug}-${outcome}"
          onclick="closePosition('${slug}','${outcome}',${shares},this)">
          Close ✕
        </button>
      </td>
    </tr>`;
  }).join('');
}

async function closePosition(slug, outcome, shares, btn) {
  if (!confirm(`Close ${shares.toFixed(2)} shares of ${slug} (${outcome})?\n\nThis will sell your entire position.`)) return;
  btn.disabled = true;
  btn.textContent = 'Closing…';
  const res = await fetch('/api/positions/close', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({slug, outcome, shares})
  }).then(r => r.json()).catch(() => ({ok: false, error: 'Network error'}));

  if (res.ok) {
    btn.textContent = '✓ Closed';
    btn.style.color = '#3fb950';
    btn.style.borderColor = '#2ea043';
    // Reload positions after a short delay
    setTimeout(loadPositions, 1500);
  } else {
    btn.disabled = false;
    btn.textContent = 'Failed ✕';
    btn.style.color = '#f85149';
    setTimeout(() => { btn.textContent = 'Close ✕'; btn.style.color = ''; }, 3000);
    console.error('Close failed:', res.error || res.output);
  }
}

// ── Push Notifications ────────────────────────────────────────────────────────

let _lastTradeTs = localStorage.getItem('cc_last_trade_ts') || '';
let _notifGranted = Notification.permission === 'granted';

async function requestNotifPermission() {
  if (Notification.permission === 'default') {
    const perm = await Notification.requestPermission();
    _notifGranted = perm === 'granted';
  }
}

function showTradeNotif(trade) {
  if (!_notifGranted) return;
  const market  = (trade.slug || trade.market || 'Market').replace(/-/g,' ').slice(0,50);
  const outcome = trade.outcome || 'Yes';
  const price   = trade.price   ? `@ ${Math.round(trade.price*100)}¢` : '';
  const amt     = trade.amount  ? `$${trade.amount}` : '';
  const action  = trade.type === 'copy_sell' ? '📤 Sold' : '📥 Copied';
  const body    = `${action}: ${market} · ${outcome} ${price} ${amt}`.trim();
  try {
    const n = new Notification('CopyCat Trade', {
      body,
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      tag: trade.ts || Date.now(),
      renotify: true,
      vibrate: [200, 100, 200],
    });
    n.onclick = () => { window.focus(); n.close(); };
  } catch(e) {}
}

function checkNewTrades(trades) {
  if (!trades || !trades.length) return;
  const latest = trades[0];
  const ts = latest.ts || latest.timestamp || '';
  if (ts && ts !== _lastTradeTs) {
    _lastTradeTs = ts;
    localStorage.setItem('cc_last_trade_ts', ts);
    // Only notify if not first load
    if (localStorage.getItem('cc_notif_init')) showTradeNotif(latest);
    else localStorage.setItem('cc_notif_init', '1');
  }
}

// ── Main refresh ──────────────────────────────────────────────────────────────

async function refresh() {
  try {
    const [statsRes, botRes] = await Promise.all([
      fetch('/api/stats'),
      fetch('/api/bot/status'),
    ]);
    const {stats, trades} = await statsRes.json();
    const bot = await botRes.json();
    updateStats(stats);
    updateTrades(trades);
    updateBotStatus(bot.running, bot.tier, bot.label, bot.valid);
    document.getElementById('lastRef').textContent = 'Updated ' + new Date().toLocaleTimeString();
    checkNewTrades(trades);
  } catch(e) { console.error('Refresh failed', e); }
}

// ── Init ──────────────────────────────────────────────────────────────────────
checkToS();   // ToS first — calls checkOnboarding() after agreement
loadSettings();
refresh();
loadPositions();
setInterval(refresh, 5000);
setInterval(loadPositions, 30000);  // Refresh positions every 30s

// ── PWA Service Worker ────────────────────────────────────────────────────────
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').then(reg => {
      console.log('SW registered:', reg.scope);
    }).catch(err => console.log('SW failed:', err));
  });
}

// ── PWA Install prompt ────────────────────────────────────────────────────────
let deferredPrompt = null;
window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  deferredPrompt = e;
  const btn = document.getElementById('install-btn');
  if (btn) btn.style.display = 'flex';
});
window.addEventListener('appinstalled', () => {
  deferredPrompt = null;
  const btn = document.getElementById('install-btn');
  if (btn) btn.style.display = 'none';
});
function installApp() {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  deferredPrompt.userChoice.then(() => { deferredPrompt = null; });
}
</script>
</body>
</html>"""

@app.get("/")
def index():
    return HTML

@app.get("/manifest.json")
def manifest():
    return jsonify({
        "name": "CopyCat",
        "short_name": "CopyCat",
        "description": "Auto-copy top Polymarket traders",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0d1117",
        "theme_color": "#3fb950",
        "orientation": "portrait-primary",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
        "categories": ["finance", "utilities"],
        "shortcuts": [
            {"name": "Start Bot",  "url": "/?action=start",  "description": "Start the CopyCat bot"},
            {"name": "Stop Bot",   "url": "/?action=stop",   "description": "Stop the CopyCat bot"},
        ],
    })

@app.get("/sw.js")
def service_worker():
    sw = """
const CACHE = 'copycat-v1';
const OFFLINE = ['/'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(OFFLINE)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
  ));
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  if (e.request.url.includes('/api/')) return;
  e.respondWith(
    fetch(e.request).catch(() => caches.match('/'))
  );
});

self.addEventListener('push', e => {
  const data = e.data ? e.data.json() : {};
  e.waitUntil(self.registration.showNotification(data.title || 'CopyCat', {
    body: data.body || 'New trade alert',
    icon: '/icon-192.png',
    badge: '/icon-192.png',
    vibrate: [200, 100, 200],
    data: { url: data.url || '/' },
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(clients.openWindow(e.notification.data.url || '/'));
});
"""
    from flask import Response
    return Response(sw, mimetype="application/javascript")

# Placeholder icons (green cat emoji rendered as SVG → PNG via browser)
@app.get("/icon-<size>.png")
def icon(size):
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">
  <rect width="{size}" height="{size}" rx="24" fill="#161b22"/>
  <text x="50%" y="54%" font-size="{int(size)//2}" text-anchor="middle" dominant-baseline="middle">🐱</text>
</svg>"""
    from flask import Response
    return Response(svg, mimetype="image/svg+xml")

if __name__ == "__main__":
    print(f"\n  CopyCat Dashboard  →  http://localhost:{PORT}\n")
    app.run(host="0.0.0.0", port=PORT, debug=False)
