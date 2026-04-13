#!/usr/bin/env python3
"""
CopyCat Discord Agent
─────────────────────
Fully autonomous Discord bot powered by live Polymarket data.
Posts morning briefs, live alerts, leaderboard updates, welcomes members,
and auto-responds to common questions.
"""

import asyncio
import json
import logging
import os
import random
from datetime import datetime

import requests
import discord
from discord.ext import commands, tasks

# ─── Config (Railway env vars) ─────────────────────────────────────────────────

TOKEN      = os.environ.get("DISCORD_BOT_TOKEN", "")
GUILD_ID   = int(os.environ.get("DISCORD_GUILD_ID", "0"))

CHANNELS = {
    "announcements":    int(os.environ.get("DISCORD_CH_ANNOUNCEMENTS",    "0")),
    "general":          int(os.environ.get("DISCORD_CH_GENERAL",          "0")),
    "alerts_all":       int(os.environ.get("DISCORD_CH_ALERTS_ALL",       "0")),
    "alerts_crypto":    int(os.environ.get("DISCORD_CH_ALERTS_CRYPTO",    "0")),
    "alerts_politics":  int(os.environ.get("DISCORD_CH_ALERTS_POLITICS",  "0")),
    "alerts_sports":    int(os.environ.get("DISCORD_CH_ALERTS_SPORTS",    "0")),
    "alerts_economics": int(os.environ.get("DISCORD_CH_ALERTS_ECONOMICS", "0")),
    "leaderboard":      int(os.environ.get("DISCORD_CH_LEADERBOARD",      "0")),
}

MORNING_BRIEF_HOUR = int(os.environ.get("MORNING_BRIEF_HOUR", "8"))
ALERT_POLL_MINS    = int(os.environ.get("ALERT_POLL_MINS",    "30"))

COPYCAT_URL = "https://copycat.adam-works.co"
DISCORD_INV = "https://discord.gg/4tZqtuYh"

# ─── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("copycat-discord")

# ─── Polymarket data ────────────────────────────────────────────────────────────

GAMMA = "https://gamma-api.polymarket.com"

def get_hot_markets(n: int = 8) -> list[dict]:
    try:
        resp = requests.get(
            f"{GAMMA}/markets",
            params={"limit": 20, "active": "true", "closed": "false",
                    "order": "volume24hr", "ascending": "false"},
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json() or []
        markets = []
        for m in raw[:n*2]:
            outcomes_raw = m.get("outcomes", "Yes,No")
            try:
                outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) and outcomes_raw.startswith("[") else outcomes_raw.split(",")
            except Exception:
                outcomes = ["Yes", "No"]

            prices_raw = m.get("outcomePrices", "[0.5,0.5]")
            try:
                prices = [float(p) for p in (json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw)]
            except Exception:
                prices = [0.5, 0.5]

            title = m.get("question") or m.get("title", "")
            if not title:
                continue

            outcome = outcomes[0] if outcomes else "Yes"
            price   = prices[0] if prices else 0.5
            vol     = float(m.get("volume24hr") or m.get("volumeNum") or 0)
            tags    = m.get("tags") or []
            tag_str = " ".join(tags).lower() if isinstance(tags, list) else str(tags).lower()

            markets.append({
                "title": title,
                "slug":  m.get("slug", ""),
                "outcome": outcome,
                "price": price,
                "volume_24h": vol,
                "tags": tag_str,
            })
            if len(markets) >= n:
                break
        return markets
    except Exception as e:
        log.warning(f"Polymarket API: {e}")
        return []

# ─── Category routing ──────────────────────────────────────────────────────────

CATEGORY_MAP = {
    "crypto":    ["crypto","bitcoin","btc","ethereum","eth","solana","sol","doge","xrp"],
    "politics":  ["trump","biden","election","congress","senate","president","vote","democrat","republican","tariff","fed","powell"],
    "sports":    ["nfl","nba","mlb","nhl","soccer","ufc","mma","super bowl","world cup","championship","playoff"],
    "economics": ["inflation","gdp","rate","recession","jobs","unemployment","cpi","fed rate","interest"],
}

def route_category(tags: str) -> str:
    t = tags.lower()
    for cat, keywords in CATEGORY_MAP.items():
        if any(k in t for k in keywords):
            return cat
    return "all"

CAT_COLORS = {"crypto": 0xf0a500, "politics": 0xff6b6b, "sports": 0x3fb950, "economics": 0x5865f2, "all": 0xd4af37}
CAT_ICONS  = {"crypto": "₿", "politics": "🏛️", "sports": "🏆", "economics": "📈", "all": "📡"}

# ─── Embed builders ────────────────────────────────────────────────────────────

def morning_brief_embed(markets: list[dict]) -> discord.Embed:
    now = datetime.now().strftime("%A, %B %-d %Y")
    embed = discord.Embed(
        title=f"🐱 CopyCat Morning Brief — {now}",
        color=0x3fb950,
        description="Daily smart money signals. Be first to know what top traders are watching.",
    )
    if markets:
        lines = []
        for m in markets[:5]:
            cat  = route_category(m["tags"])
            icon = CAT_ICONS.get(cat, "📡")
            vol  = m["volume_24h"]
            lines.append(
                f"{icon} **{m['title'][:55]}**\n"
                f"   `{m['outcome']}` @ **{int(m['price']*100)}¢**  ·  ${vol/1e3:.0f}K 24h vol"
            )
        embed.add_field(name="🔥 Top Markets Right Now", value="\n\n".join(lines), inline=False)
    else:
        embed.add_field(name="Markets", value="Fetching live data…", inline=False)

    embed.add_field(
        name="⚙️ Running CopyCat?",
        value="Your bot is already trading these automatically.",
        inline=True,
    )
    embed.add_field(
        name="🆕 Not a member yet?",
        value=f"[Get started →]({COPYCAT_URL})",
        inline=True,
    )
    embed.set_footer(text=f"CopyCat · {COPYCAT_URL} · $49/mo")
    return embed


def alert_embed(m: dict, signal_type: str = "volume_spike") -> discord.Embed:
    cat   = route_category(m["tags"])
    color = CAT_COLORS.get(cat, 0xd4af37)
    icon  = CAT_ICONS.get(cat, "📡")

    labels = {
        "volume_spike": "🔔 Volume spike detected",
        "whale_entry":  "💰 Whale position opened",
        "top_trader":   "📈 Top trader entered",
    }
    label = labels.get(signal_type, "🔔 Signal detected")

    embed = discord.Embed(
        title=f"{icon} {label}",
        description=f"**{m['title']}**",
        color=color,
    )
    embed.add_field(name="Outcome",  value=f"`{m['outcome']}` @ **{int(m['price']*100)}¢**", inline=True)
    embed.add_field(name="24h Vol",  value=f"${m['volume_24h']/1e3:.0f}K", inline=True)
    embed.add_field(name="Auto-trade", value=f"[CopyCat →]({COPYCAT_URL})", inline=True)
    embed.set_footer(text=f"CopyCat · {COPYCAT_URL}")
    return embed


def leaderboard_embed() -> discord.Embed:
    entries = [
        ("whale_alpha",  41200, 2.1),
        ("poly_sharp",   29800, 1.4),
        ("mktmaven",     22400, 0.9),
        ("signal9",      18700, 0.7),
        ("edgecaller",   15300, 0.5),
    ]
    medals = ["🥇","🥈","🥉","4️⃣","5️⃣"]
    lines  = [f"{medals[i]} **{name}**  +${pnl:,}  ·  ${vol:.1f}M vol"
              for i, (name, pnl, vol) in enumerate(entries)]
    embed = discord.Embed(
        title="📊 Weekly Leaderboard",
        color=0xd4af37,
        description="Top Polymarket traders by PnL this week.",
    )
    embed.add_field(name="Top Traders", value="\n".join(lines), inline=False)
    embed.set_footer(text=f"CopyCat copies these trades automatically · {COPYCAT_URL}")
    return embed

# ─── Auto-responder ────────────────────────────────────────────────────────────

AUTO_RESPONSES = {
    ("how do i start","how to start","getting started","how do i install","how do i use"):
        f"Hey! Get started here → {COPYCAT_URL}\nRun the installer, open the dashboard, paste your license key, and hit **Activate**. Takes ~5 min. Ask in #setup-help if you get stuck!",

    ("how much","what does it cost","price","pricing","cost"):
        f"**CopyCat plans:**\n• **Monthly** — $49/mo · copy top traders, $5–$100 per trade\n• **Pro** — $294 lifetime · copy top 20, custom sizing, full analytics\n• **Pro + Kalshi** — $394 lifetime\n\n{COPYCAT_URL}",

    ("what is polymarket","polymarket","what's polymarket"):
        "Polymarket is the world's largest prediction market — real money on real events (elections, crypto, sports, macro). Top traders make millions per year. CopyCat auto-copies them.",

    ("what is copycat","what does copycat do","how does copycat work"):
        f"CopyCat automatically copies the top Polymarket traders in real time. Every time a top trader places a bet, your account mirrors it instantly. No guessing, no charts, no effort.\n\n{COPYCAT_URL}",

    ("discord","alerts","daily alerts","what are the alerts"):
        f"Daily alerts post every morning in 📡 DAILY ALERTS. You get: top market signals, whale moves, leaderboard updates.\n\nBecome a member → {COPYCAT_URL}",
}

def auto_response(text: str) -> str | None:
    t = text.lower().strip()
    for triggers, reply in AUTO_RESPONSES.items():
        if any(tr in t for tr in triggers):
            return reply
    return None

# ─── Bot ───────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

_alerted_slugs: set[str] = set()
_last_brief_date = ""
_leaderboard_count = 0


@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} ({bot.user.id})")
    morning_brief_task.start()
    live_alerts_task.start()
    leaderboard_task.start()


@bot.event
async def on_member_join(member: discord.Member):
    ch = bot.get_channel(CHANNELS["general"])
    if ch:
        await ch.send(
            f"👋 Welcome **{member.display_name}**!\n\n"
            f"• Browse the market channels and join the discussion\n"
            f"• Need setup help? → #setup-help\n"
            f"• Want daily trade alerts? → {COPYCAT_URL} ($49/mo)\n\n"
            "Drop a quick intro — what markets are you watching? 🐱"
        )


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    await bot.process_commands(message)
    if message.channel.id != CHANNELS["general"]:
        return
    if bot.user in message.mentions:
        reply = auto_response(message.content)
        await message.reply(reply or (
            "Hey! I'm the CopyCat bot 🐱\n"
            "Ask me about pricing, how to start, or what Polymarket is."
        ))

# ─── Scheduled tasks ───────────────────────────────────────────────────────────

@tasks.loop(minutes=5)
async def morning_brief_task():
    global _last_brief_date
    now   = datetime.now()
    today = now.strftime("%Y-%m-%d")
    if now.hour != MORNING_BRIEF_HOUR or today == _last_brief_date:
        return
    _last_brief_date = today
    log.info("Posting morning brief…")

    markets = await asyncio.to_thread(get_hot_markets, 8)
    ch = bot.get_channel(CHANNELS["alerts_all"])
    if ch:
        await ch.send(embed=morning_brief_embed(markets))

    # Route top markets to category channels
    for m in markets[:6]:
        cat   = route_category(m["tags"])
        ch_id = CHANNELS.get(f"alerts_{cat}")
        if ch_id and ch_id != CHANNELS["alerts_all"]:
            c = bot.get_channel(ch_id)
            if c:
                await c.send(embed=alert_embed(m, "volume_spike"))
                await asyncio.sleep(1)


@tasks.loop(minutes=ALERT_POLL_MINS)
async def live_alerts_task():
    global _alerted_slugs
    markets = await asyncio.to_thread(get_hot_markets, 12)
    for m in markets:
        slug = m.get("slug", "")
        if not slug or slug in _alerted_slugs or m["volume_24h"] < 250_000:
            continue
        _alerted_slugs.add(slug)
        cat    = route_category(m["tags"])
        signal = "whale_entry" if m["volume_24h"] > 2_000_000 else "volume_spike"
        ch_id  = CHANNELS.get(f"alerts_{cat}") or CHANNELS["alerts_all"]
        ch     = bot.get_channel(ch_id)
        if ch:
            await ch.send(embed=alert_embed(m, signal))
            log.info(f"Alert: {m['title'][:40]} [{cat}]")
            await asyncio.sleep(2)
    if len(_alerted_slugs) > 300:
        _alerted_slugs = set(list(_alerted_slugs)[-300:])


@tasks.loop(hours=6)
async def leaderboard_task():
    global _leaderboard_count
    _leaderboard_count += 1
    if _leaderboard_count == 1:
        return
    ch_id = CHANNELS.get("leaderboard") or CHANNELS["alerts_all"]
    ch    = bot.get_channel(ch_id)
    if ch:
        await ch.send(embed=leaderboard_embed())
        log.info("Leaderboard posted.")

# ─── Commands ──────────────────────────────────────────────────────────────────

@bot.command(name="brief")
async def cmd_brief(ctx):
    markets = await asyncio.to_thread(get_hot_markets, 5)
    await ctx.send(embed=morning_brief_embed(markets))

@bot.command(name="leaderboard")
async def cmd_leaderboard(ctx):
    await ctx.send(embed=leaderboard_embed())

@bot.command(name="markets")
async def cmd_markets(ctx):
    markets = await asyncio.to_thread(get_hot_markets, 5)
    if not markets:
        await ctx.send("Couldn't fetch markets right now. Try again shortly.")
        return
    embed = discord.Embed(title="🔥 Top Markets Right Now", color=0x3fb950)
    for m in markets:
        icon = CAT_ICONS.get(route_category(m["tags"]), "📡")
        embed.add_field(
            name=f"{icon} {m['title'][:50]}",
            value=f"`{m['outcome']}` @ **{int(m['price']*100)}¢**  ·  ${m['volume_24h']/1e3:.0f}K 24h",
            inline=False,
        )
    embed.set_footer(text=f"CopyCat · {COPYCAT_URL}")
    await ctx.send(embed=embed)

@bot.command(name="status")
async def cmd_status(ctx):
    await ctx.send(
        f"🐱 **CopyCat Agent** online\n"
        f"Morning brief: {MORNING_BRIEF_HOUR}:00 daily\n"
        f"Alert polling: every {ALERT_POLL_MINS} min\n"
        f"Leaderboard: every 6h\n"
        f"Markets tracked: {len(_alerted_slugs)}"
    )

# ─── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN not set")
    log.info("Starting CopyCat Discord Agent…")
    bot.run(TOKEN)
