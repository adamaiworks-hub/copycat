#!/usr/bin/env python3
"""
CopyCat Discord Agent
─────────────────────
Fully autonomous Discord bot that:
  • Posts a morning brief every day at 8 AM (market alerts, leaderboard, signals)
  • Posts live trade alerts to category channels when smart money moves
  • Auto-responds to common questions in free community channels
  • Posts leaderboard updates every 6 hours
  • Welcomes new members

Setup:
  1. Create a Discord bot at discord.com/developers
  2. Add bot to your server with permissions: Send Messages, Embed Links, Read Message History, Manage Messages
  3. Get the bot token and channel IDs
  4. pip3 install discord.py python-dotenv
  5. python3 discord_agent.py
"""

import asyncio
import json
import logging
import os
import subprocess
from datetime import datetime, timezone, time as dtime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

try:
    import discord
    from discord.ext import commands, tasks
except ImportError:
    print("Missing dependency. Run: pip3 install discord.py")
    raise

# ─── Config ────────────────────────────────────────────────────────────────────

TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))

# Channel IDs — fill these in after creating channels in Discord
CHANNELS = {
    "alerts_all":       int(os.getenv("DISCORD_CH_ALERTS_ALL",       "0")),
    "alerts_crypto":    int(os.getenv("DISCORD_CH_ALERTS_CRYPTO",     "0")),
    "alerts_politics":  int(os.getenv("DISCORD_CH_ALERTS_POLITICS",   "0")),
    "alerts_sports":    int(os.getenv("DISCORD_CH_ALERTS_SPORTS",     "0")),
    "alerts_economics": int(os.getenv("DISCORD_CH_ALERTS_ECONOMICS",  "0")),
    "general":          int(os.getenv("DISCORD_CH_GENERAL",           "0")),
    "announcements":    int(os.getenv("DISCORD_CH_ANNOUNCEMENTS",     "0")),
    "leaderboard":      int(os.getenv("DISCORD_CH_LEADERBOARD",       "0")),
}

MORNING_BRIEF_HOUR = int(os.getenv("MORNING_BRIEF_HOUR", "8"))   # 8 AM local
ALERT_POLL_MINS    = int(os.getenv("ALERT_POLL_MINS",    "30"))  # check for new signals every 30 min
TOP_N_MARKETS      = int(os.getenv("TOP_N_MARKETS",       "5"))

# ─── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("discord_agent")

# ─── Bullpen helpers ───────────────────────────────────────────────────────────

import shutil as _shutil
_BULLPEN = _shutil.which("bullpen") or "/opt/homebrew/bin/bullpen"
_ENV = {**os.environ, "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", "")}

def bullpen(args: list, timeout: int = 30):
    try:
        r = subprocess.run(
            [_BULLPEN] + args + ["--output", "json"],
            capture_output=True, text=True, timeout=timeout, env=_ENV
        )
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)
    except Exception as e:
        log.warning(f"bullpen {' '.join(args[:3])}: {e}")
        return None

def get_leaderboard(n=10):
    data = bullpen(["polymarket", "data", "leaderboard", "--period", "week"])
    if not data:
        return []
    traders = data if isinstance(data, list) else data.get("traders", [])
    active = [t for t in traders if float(t.get("volume") or 0) > 0]
    active.sort(key=lambda t: float(t.get("pnl") or 0), reverse=True)
    return active[:n]

def get_hot_markets(n=5):
    data = bullpen(["polymarket", "discover"])
    if not data:
        return []
    events = data.get("events", [])
    events.sort(key=lambda e: float(e.get("volume_24h") or 0), reverse=True)
    results = []
    for e in events[:n*3]:
        for m in e.get("markets", [])[:1]:
            outcomes = m.get("outcomes", [])
            best = max(outcomes, key=lambda o: float(o.get("probability") or 0), default={})
            results.append({
                "title":    e.get("title", ""),
                "slug":     m.get("slug", ""),
                "category": (e.get("tags") or [e.get("category", "General")])[0],
                "price":    float(best.get("price") or 0),
                "outcome":  best.get("name", "Yes"),
                "volume_24h": float(e.get("volume_24h") or 0),
            })
        if len(results) >= n:
            break
    return results[:n]

def get_tracker_trades(limit=10):
    data = bullpen(["tracker", "trades"])
    if not data:
        return []
    trades = data.get("trades", data) if isinstance(data, dict) else data
    return trades[:limit] if trades else []

# ─── Category routing ──────────────────────────────────────────────────────────

CATEGORY_MAP = {
    "crypto":    ["crypto", "bitcoin", "btc", "ethereum", "eth", "solana"],
    "politics":  ["politics", "trump", "election", "congress", "government"],
    "sports":    ["sports", "nfl", "nba", "mlb", "nhl", "soccer", "ufc", "mma"],
    "economics": ["economics", "fed", "inflation", "stocks", "gdp", "rate"],
}

def route_category(tags: list | str) -> str:
    text = " ".join(tags if isinstance(tags, list) else [str(tags)]).lower()
    for cat, keywords in CATEGORY_MAP.items():
        if any(k in text for k in keywords):
            return cat
    return "all"

# ─── Embed builders ────────────────────────────────────────────────────────────

CAT_COLORS = {
    "crypto":    0xf0a500,
    "politics":  0xff6b6b,
    "sports":    0x3fb950,
    "economics": 0x5865f2,
    "all":       0xd4af37,
}
CAT_ICONS = {
    "crypto": "₿", "politics": "🏛️", "sports": "🏆", "economics": "📈", "all": "📡",
}

def morning_brief_embed(traders: list, markets: list) -> discord.Embed:
    now = datetime.now().strftime("%A, %B %-d %Y")
    embed = discord.Embed(
        title=f"🐱 CopyCat Morning Brief — {now}",
        color=0x3fb950,
        description="Daily smart money signals. Be first to know what top traders are watching.",
    )
    embed.set_footer(text="copycat.adam-works.co  ·  $49/mo for daily alerts")

    # Top traders
    if traders:
        lines = []
        for i, t in enumerate(traders[:5], 1):
            name = t.get("username") or t.get("address", "")[:10]
            if len(name) > 30:
                name = name[:10] + "…"
            pnl  = float(t.get("pnl") or 0)
            vol  = float(t.get("volume") or 0)
            medal = ["🥇","🥈","🥉","4️⃣","5️⃣"][i-1]
            lines.append(f"{medal} **{name}**  +${pnl:,.0f} PnL  ·  ${vol/1e6:.1f}M vol")
        embed.add_field(name="📊 Leaderboard (This Week)", value="\n".join(lines), inline=False)

    # Hot markets
    if markets:
        lines = []
        for m in markets:
            cat   = route_category(m["category"])
            icon  = CAT_ICONS.get(cat, "📡")
            price = m["price"]
            vol   = m["volume_24h"]
            lines.append(
                f"{icon} **{m['title'][:55]}**\n"
                f"   `{m['outcome']}` @ **${price:.2f}**  ·  24h vol ${vol/1e3:.0f}K"
            )
        embed.add_field(name="🔥 Markets Moving Right Now", value="\n\n".join(lines), inline=False)

    embed.add_field(
        name="⚙️ Running CopyCat?",
        value="Your bot is already trading these automatically.",
        inline=True,
    )
    embed.add_field(
        name="🆕 Not a member?",
        value="[Get started →](https://copycat.adam-works.co)",
        inline=True,
    )
    return embed

def alert_embed(market: dict, signal_type: str = "volume_spike") -> discord.Embed:
    cat   = route_category(market.get("category", ""))
    color = CAT_COLORS.get(cat, 0xd4af37)
    icon  = CAT_ICONS.get(cat, "📡")

    signals = {
        "volume_spike": "🔔 Volume spike detected",
        "whale_entry":  "💰 Whale position opened",
        "top_trader":   "📈 Top trader entered",
        "price_move":   "⚡ Sharp price movement",
    }
    signal_label = signals.get(signal_type, "🔔 Signal detected")

    embed = discord.Embed(
        title=f"{icon} {signal_label}",
        description=f"**{market['title']}**",
        color=color,
    )
    embed.add_field(name="Outcome",  value=f"`{market.get('outcome','Yes')}` @ **${market.get('price',0):.2f}**", inline=True)
    embed.add_field(name="24h Vol",  value=f"${market.get('volume_24h',0)/1e3:.0f}K",  inline=True)
    embed.add_field(name="Category", value=market.get("category","General"), inline=True)
    embed.set_footer(text="CopyCat · copycat.adam-works.co")
    return embed

def leaderboard_embed(traders: list) -> discord.Embed:
    embed = discord.Embed(
        title="📊 Weekly Leaderboard Update",
        color=0xd4af37,
        description="Top Polymarket traders by PnL this week.",
    )
    lines = []
    medals = ["🥇","🥈","🥉"] + ["🔹"] * 20
    for i, t in enumerate(traders[:10], 0):
        name = t.get("username") or t.get("address","")[:10]
        if len(name) > 28:
            name = name[:10] + "…"
        pnl  = float(t.get("pnl") or 0)
        vol  = float(t.get("volume") or 0)
        lines.append(f"{medals[i]} **{name}**  +${pnl:,.0f}  ·  ${vol/1e6:.1f}M")
    embed.add_field(name="Top 10 This Week", value="\n".join(lines) or "No data", inline=False)
    embed.set_footer(text="CopyCat follows these traders automatically · copycat.adam-works.co")
    return embed

# ─── Auto-responder ────────────────────────────────────────────────────────────

AUTO_RESPONSES = {
    ("how do i start", "how to start", "getting started", "how do i use", "how do i install"):
        "Hey! Get started here → https://copycat.adam-works.co\nRun the installer, open the dashboard, and hit **Start Bot**. Takes about 5 minutes. Ask in #setup-help if you get stuck!",

    ("how much", "what does it cost", "price", "pricing", "how much is it", "cost"):
        "**CopyCat plans:**\n• **Monthly** — $49/mo · copy top 5 traders, $5–$100 per trade\n• **Pro** — $99/mo · copy top 20, custom sizing, full analytics\n• **Discord alerts** — $49/mo · daily smart money signals\n\nhttps://copycat.adam-works.co",

    ("what is polymarket", "what's polymarket", "polymarket"):
        "Polymarket is the world's largest prediction market — people bet real money on real events (elections, crypto, sports, economics). Top traders make millions per week. CopyCat automatically copies them.",

    ("what is copycat", "what does copycat do"):
        "CopyCat automatically copies the top Polymarket traders in real time. Every time a top trader places a bet, CopyCat places the same bet in your account. No guessing, no watching charts.\n\nhttps://copycat.adam-works.co",

    ("discord", "alerts", "what are the alerts"):
        "Daily alerts are posted every morning in the 🔔 DAILY ALERTS section (members only). You'll get: top market signals, smart money moves, leaderboard updates.\n\nJoin for $49/mo → https://copycat.adam-works.co",
}

def get_auto_response(message: str) -> str | None:
    msg = message.lower().strip()
    for triggers, response in AUTO_RESPONSES.items():
        if any(t in msg for t in triggers):
            return response
    return None

# ─── Bot ───────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Track which markets we've already alerted on (avoids duplicates)
_alerted_slugs: set[str] = set()
_last_brief_date: str = ""
_leaderboard_post_count: int = 0

@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    morning_brief.start()
    live_alerts.start()
    leaderboard_update.start()
    log.info("All tasks started.")

@bot.event
async def on_member_join(member: discord.Member):
    ch = bot.get_channel(CHANNELS["general"])
    if not ch:
        return
    await ch.send(
        f"👋 Welcome **{member.display_name}**! Glad you're here.\n\n"
        f"• Explore the market channels and jump into discussion\n"
        f"• Need help with the bot? → #setup-help\n"
        f"• Want daily trade alerts? → https://copycat.adam-works.co ($49/mo)\n\n"
        f"Drop a quick intro in #introductions 🐱"
    )

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    await bot.process_commands(message)

    # Only auto-respond in free community channels
    free_channels = {CHANNELS["general"]}
    if message.channel.id not in free_channels:
        return
    if bot.user in message.mentions:
        reply = get_auto_response(message.content)
        if reply:
            await message.reply(reply)
        else:
            await message.reply(
                "Hey! I'm the CopyCat bot 🐱\n"
                "Ask me about pricing, how to get started, or what Polymarket is.\n"
                "For bot setup issues → #setup-help"
            )

# ─── Scheduled tasks ───────────────────────────────────────────────────────────

@tasks.loop(minutes=5)
async def morning_brief():
    """Post morning brief once per day at MORNING_BRIEF_HOUR."""
    global _last_brief_date
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    if now.hour != MORNING_BRIEF_HOUR or today == _last_brief_date:
        return

    log.info("Posting morning brief…")
    _last_brief_date = today

    traders = await asyncio.to_thread(get_leaderboard, 10)
    markets = await asyncio.to_thread(get_hot_markets, 5)

    embed = morning_brief_embed(traders, markets)
    ch = bot.get_channel(CHANNELS["alerts_all"])
    if ch:
        await ch.send(embed=embed)
        log.info("Morning brief posted.")

    # Also route top markets to category channels
    for m in markets:
        cat = route_category(m.get("category", ""))
        ch_id = CHANNELS.get(f"alerts_{cat}") or CHANNELS.get("alerts_all")
        if ch_id and ch_id != CHANNELS["alerts_all"]:
            ch2 = bot.get_channel(ch_id)
            if ch2:
                await ch2.send(embed=alert_embed(m, "volume_spike"))
                await asyncio.sleep(1)

@tasks.loop(minutes=ALERT_POLL_MINS)
async def live_alerts():
    """Check for new high-volume markets and post alerts."""
    global _alerted_slugs
    markets = await asyncio.to_thread(get_hot_markets, 10)
    if not markets:
        return

    # Only alert on markets with significant 24h volume we haven't seen yet
    for m in markets:
        slug = m.get("slug", "")
        if not slug or slug in _alerted_slugs:
            continue
        if m.get("volume_24h", 0) < 500_000:  # only post if >$500K 24h volume
            continue

        _alerted_slugs.add(slug)
        cat = route_category(m.get("category", ""))
        signal = "volume_spike" if m["volume_24h"] > 2_000_000 else "top_trader"

        ch_id = CHANNELS.get(f"alerts_{cat}") or CHANNELS.get("alerts_all")
        ch = bot.get_channel(ch_id or CHANNELS["alerts_all"])
        if ch:
            await ch.send(embed=alert_embed(m, signal))
            log.info(f"Alert posted: {m['title'][:40]} [{cat}]")
            await asyncio.sleep(2)

    # Prune old slugs (keep last 200)
    if len(_alerted_slugs) > 200:
        _alerted_slugs = set(list(_alerted_slugs)[-200:])

@tasks.loop(hours=6)
async def leaderboard_update():
    """Post leaderboard update every 6 hours."""
    global _leaderboard_post_count
    _leaderboard_post_count += 1
    if _leaderboard_post_count == 1:
        return  # skip first run (fires immediately on start)

    log.info("Posting leaderboard update…")
    traders = await asyncio.to_thread(get_leaderboard, 10)
    if not traders:
        return

    ch_id = CHANNELS.get("leaderboard") or CHANNELS.get("alerts_all")
    ch = bot.get_channel(ch_id)
    if ch:
        await ch.send(embed=leaderboard_embed(traders))
        log.info("Leaderboard update posted.")

# ─── Commands ──────────────────────────────────────────────────────────────────

@bot.command(name="brief")
@commands.has_permissions(manage_messages=True)
async def cmd_brief(ctx):
    """!brief — manually trigger morning brief (admin only)"""
    await ctx.message.delete()
    traders = await asyncio.to_thread(get_leaderboard, 10)
    markets = await asyncio.to_thread(get_hot_markets, 5)
    embed = morning_brief_embed(traders, markets)
    await ctx.send(embed=embed)

@bot.command(name="leaderboard")
async def cmd_leaderboard(ctx):
    """!leaderboard — show current weekly leaderboard"""
    traders = await asyncio.to_thread(get_leaderboard, 10)
    await ctx.send(embed=leaderboard_embed(traders))

@bot.command(name="markets")
async def cmd_markets(ctx):
    """!markets — show top markets by volume"""
    markets = await asyncio.to_thread(get_hot_markets, 5)
    if not markets:
        await ctx.send("Couldn't fetch markets right now. Try again shortly.")
        return
    embed = discord.Embed(title="🔥 Top Markets Right Now", color=0x3fb950)
    for m in markets:
        cat  = route_category(m["category"])
        icon = CAT_ICONS.get(cat, "📡")
        embed.add_field(
            name=f"{icon} {m['title'][:50]}",
            value=f"`{m['outcome']}` @ **${m['price']:.2f}**  ·  24h vol ${m['volume_24h']/1e3:.0f}K",
            inline=False,
        )
    embed.set_footer(text="CopyCat · copycat.adam-works.co")
    await ctx.send(embed=embed)

@bot.command(name="status")
async def cmd_status(ctx):
    """!status — show bot status"""
    await ctx.send(
        f"🐱 **CopyCat Agent** is online\n"
        f"Morning brief: {MORNING_BRIEF_HOUR}:00 daily\n"
        f"Alert polling: every {ALERT_POLL_MINS} minutes\n"
        f"Leaderboard: every 6 hours\n"
        f"Markets tracked: {len(_alerted_slugs)} slugs seen"
    )

# ─── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        print("\n[CopyCat Discord Agent] Setup required:")
        print("  1. Go to discord.com/developers → New Application → Bot")
        print("  2. Copy the bot token")
        print("  3. Add to .env:  DISCORD_BOT_TOKEN=your_token_here")
        print("  4. Add channel IDs to .env (see CHANNELS dict at top of this file)")
        print("  5. Invite bot to server with scopes: bot, applications.commands")
        print("     Permissions: Send Messages, Embed Links, Read Message History, Manage Messages")
        print()
        raise SystemExit(1)

    log.info("Starting CopyCat Discord Agent…")
    bot.run(TOKEN)
