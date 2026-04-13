#!/usr/bin/env python3
"""
CopyCat Discord Channel Seeder
────────────────────────────────
Posts pre-written alerts to each Discord channel so they look active on launch.
Run once after setting up the bot and creating channels.

Usage:
  python3 discord_seeder.py
"""

import asyncio
import os
import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN    = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))

CHANNELS = {
    "announcements":    int(os.getenv("DISCORD_CH_ANNOUNCEMENTS",     "0")),
    "general":          int(os.getenv("DISCORD_CH_GENERAL",           "0")),
    "alerts_all":       int(os.getenv("DISCORD_CH_ALERTS_ALL",        "0")),
    "alerts_crypto":    int(os.getenv("DISCORD_CH_ALERTS_CRYPTO",     "0")),
    "alerts_politics":  int(os.getenv("DISCORD_CH_ALERTS_POLITICS",   "0")),
    "alerts_sports":    int(os.getenv("DISCORD_CH_ALERTS_SPORTS",     "0")),
    "alerts_economics": int(os.getenv("DISCORD_CH_ALERTS_ECONOMICS",  "0")),
    "wins":             int(os.getenv("DISCORD_CH_WINS",              "0")),
}

# ─── Seed content ──────────────────────────────────────────────────────────────

ANNOUNCEMENTS_POSTS = [
    {
        "title": "🐱 Welcome to CopyCat Intelligence Network",
        "color": 0x3fb950,
        "description": (
            "This is the official CopyCat Discord — your home for Polymarket smart money signals, "
            "community discussion, and daily trade alerts.\n\n"
            "**Free for everyone:**\n"
            "• #general — open discussion\n"
            "• #market-chat channels — talk crypto, politics, sports, economics\n"
            "• #setup-help — bot support\n\n"
            "**Members only ($49/mo):**\n"
            "• 🔔 Daily morning alerts in every category channel\n"
            "• 📊 Leaderboard updates every 6 hours\n"
            "• 💬 Private members lounge\n\n"
            "→ [Upgrade here](https://copycat.adam-works.co)"
        ),
    },
    {
        "title": "🤖 Meet the CopyCat Agent",
        "color": 0x5865f2,
        "description": (
            "Our autonomous AI agent is now live and running 24/7.\n\n"
            "**What it does:**\n"
            "• Posts a morning brief every day at 8 AM\n"
            "• Monitors Polymarket for smart money signals\n"
            "• Routes alerts to the right category channel automatically\n"
            "• Updates the leaderboard every 6 hours\n\n"
            "You can interact with it using these commands:\n"
            "`!leaderboard` · `!markets` · `!status`\n\n"
            "Never miss a move again."
        ),
    },
]

ALERTS_ALL_POSTS = [
    {
        "title": "🐱 CopyCat Morning Brief — Launch Day",
        "color": 0x3fb950,
        "description": "Daily smart money signals. Be first to know what top traders are watching.",
        "fields": [
            {
                "name": "📊 Leaderboard (This Week)",
                "value": (
                    "🥇 **ProfitProphet**  +$6,753,996  ·  $13.0M vol\n"
                    "🥈 **AlphaWhale_X**  +$4,210,440  ·  $8.4M vol\n"
                    "🥉 **Sigma_Calls**  +$2,887,330  ·  $5.1M vol\n"
                    "4️⃣ **TruthTrader**  +$1,940,220  ·  $3.8M vol\n"
                    "5️⃣ **MarketMaven**  +$1,204,880  ·  $2.2M vol"
                ),
                "inline": False,
            },
            {
                "name": "🔥 Markets Moving Right Now",
                "value": (
                    "₿ **Will Bitcoin hit $120K before July?**\n"
                    "   `Yes` @ **$0.58**  ·  24h vol $2.4M\n\n"
                    "🏛️ **Will Trump extend tariffs past 90 days?**\n"
                    "   `Yes` @ **$0.73**  ·  24h vol $1.8M\n\n"
                    "🏆 **NBA Finals 2026 — Will OKC win?**\n"
                    "   `Yes` @ **$0.44**  ·  24h vol $890K\n\n"
                    "📈 **Fed rate cut at July 2026 meeting?**\n"
                    "   `Yes` @ **$0.38**  ·  24h vol $3.1M"
                ),
                "inline": False,
            },
            {"name": "⚙️ Running CopyCat?", "value": "Your bot is already trading these automatically.", "inline": True},
            {"name": "🆕 Not a member?", "value": "[Get started →](https://copycat.adam-works.co)", "inline": True},
        ],
    },
]

ALERTS_CRYPTO_POSTS = [
    {
        "title": "₿ Crypto Signal — Whale Entry Detected",
        "color": 0xf0a500,
        "description": "**Will Bitcoin hit $120K before July 2026?**",
        "fields": [
            {"name": "Outcome", "value": "`Yes` @ **$0.58**", "inline": True},
            {"name": "24h Volume", "value": "$2.4M", "inline": True},
            {"name": "Signal", "value": "Top-ranked wallet added 48K YES shares overnight", "inline": False},
            {"name": "Context", "value": "BTC holding above $105K. Institutional accumulation pattern continuing. Whale entered at $0.52 — already up 11%.", "inline": False},
        ],
    },
    {
        "title": "Ξ Crypto Signal — Volume Spike",
        "color": 0xf0a500,
        "description": "**Will Ethereum hit $4K in 2026?**",
        "fields": [
            {"name": "Outcome", "value": "`Yes` @ **$0.41**", "inline": True},
            {"name": "24h Volume", "value": "$1.1M — 4× 7-day avg", "inline": True},
            {"name": "Signal", "value": "Unusual volume spike — 3 top-10 traders entered simultaneously", "inline": False},
            {"name": "Context", "value": "ETH consolidating after breaking $3,800. Options market shows heavy call buying. Smart money positioning for breakout.", "inline": False},
        ],
    },
    {
        "title": "◎ Crypto Signal — Smart Money Exit",
        "color": 0xf85149,
        "description": "**Will Solana hit $300 in Q2 2026?**",
        "fields": [
            {"name": "Outcome", "value": "`Yes` @ **$0.29** ↓", "inline": True},
            {"name": "24h Volume", "value": "$670K", "inline": True},
            {"name": "Signal", "value": "Top trader sold 60% of YES position — possible exit signal", "inline": False},
            {"name": "Context", "value": "Price moved from $0.38 to $0.29 in 48h. Whale who entered at $0.42 is exiting with a loss — watch for further downside.", "inline": False},
        ],
    },
]

ALERTS_POLITICS_POSTS = [
    {
        "title": "🏛️ Politics Signal — Smart Money Entered",
        "color": 0xff6b6b,
        "description": "**Will Trump extend tariffs past the 90-day pause?**",
        "fields": [
            {"name": "Outcome", "value": "`Yes` @ **$0.73**", "inline": True},
            {"name": "24h Volume", "value": "$1.8M", "inline": True},
            {"name": "Signal", "value": "3 top-10 traders added large YES positions this morning", "inline": False},
            {"name": "Context", "value": "Negotiations with China stalled again. Insiders pricing high probability of extension. Smart money at $0.68 already up 7%.", "inline": False},
        ],
    },
    {
        "title": "🗳️ Politics Signal — Volume Spike",
        "color": 0xff6b6b,
        "description": "**Will there be a US government shutdown in 2026?**",
        "fields": [
            {"name": "Outcome", "value": "`Yes` @ **$0.34**", "inline": True},
            {"name": "24h Volume", "value": "$940K — 3× avg", "inline": True},
            {"name": "Signal", "value": "Sudden volume spike after congressional stalemate reported", "inline": False},
            {"name": "Context", "value": "Budget deadline approaching. Market moved from $0.22 to $0.34 in 12 hours. Top traders positioning YES ahead of vote.", "inline": False},
        ],
    },
    {
        "title": "🌍 Politics Signal — Whale Position",
        "color": 0xff6b6b,
        "description": "**Will Ukraine ceasefire deal be signed before July 2026?**",
        "fields": [
            {"name": "Outcome", "value": "`No` @ **$0.71**", "inline": True},
            {"name": "24h Volume", "value": "$2.2M", "inline": True},
            {"name": "Signal", "value": "Largest single-wallet position opened on NO in past 30 days", "inline": False},
            {"name": "Context", "value": "$180K NO position from wallet with 78% win rate. Negotiations reportedly breaking down behind the scenes.", "inline": False},
        ],
    },
]

ALERTS_SPORTS_POSTS = [
    {
        "title": "🏆 Sports Signal — Top Trader Entered",
        "color": 0x3fb950,
        "description": "**Will OKC Thunder win the 2026 NBA Finals?**",
        "fields": [
            {"name": "Outcome", "value": "`Yes` @ **$0.44**", "inline": True},
            {"name": "24h Volume", "value": "$890K", "inline": True},
            {"name": "Signal", "value": "Top-ranked sports trader added 22K YES shares", "inline": False},
            {"name": "Context", "value": "OKC swept the conference semifinals. This trader is 71% on NBA markets this season. Price up from $0.38 in 48h.", "inline": False},
        ],
    },
    {
        "title": "🏈 Sports Signal — Volume Spike",
        "color": 0x3fb950,
        "description": "**Will the Chiefs win Super Bowl LXI?**",
        "fields": [
            {"name": "Outcome", "value": "`Yes` @ **$0.31**", "inline": True},
            {"name": "24h Volume", "value": "$1.3M — 5× avg", "inline": True},
            {"name": "Signal", "value": "Massive volume spike after early QB injury news cleared", "inline": False},
            {"name": "Context", "value": "Mahomes injury scare resolved — market snapped back from $0.18 to $0.31. Smart money loaded up at $0.20.", "inline": False},
        ],
    },
    {
        "title": "⛳ Sports Signal — Whale Exit",
        "color": 0xf0a500,
        "description": "**Will Scottie Scheffler win the 2026 Masters?**",
        "fields": [
            {"name": "Outcome", "value": "`Yes` @ **$0.19** ↓", "inline": True},
            {"name": "24h Volume", "value": "$430K", "inline": True},
            {"name": "Signal", "value": "Large YES position partially exited before tournament start", "inline": False},
            {"name": "Context", "value": "Whale who held 35K shares since $0.12 sold 15K. Locking profit — or knows something. Watch for further exit.", "inline": False},
        ],
    },
]

ALERTS_ECONOMICS_POSTS = [
    {
        "title": "📈 Economics Signal — Smart Money",
        "color": 0x5865f2,
        "description": "**Will the Fed cut rates at the July 2026 meeting?**",
        "fields": [
            {"name": "Outcome", "value": "`Yes` @ **$0.38**", "inline": True},
            {"name": "24h Volume", "value": "$3.1M", "inline": True},
            {"name": "Signal", "value": "Leaderboard #1 wallet entered YES — largest econ position this month", "inline": False},
            {"name": "Context", "value": "Core PCE came in soft. Futures pricing 62% cut probability. This wallet called the last 3 FOMC decisions correctly.", "inline": False},
        ],
    },
    {
        "title": "💵 Economics Signal — Volume Spike",
        "color": 0x5865f2,
        "description": "**Will US CPI be below 3% by June 2026?**",
        "fields": [
            {"name": "Outcome", "value": "`Yes` @ **$0.55**", "inline": True},
            {"name": "24h Volume", "value": "$1.6M — 4× avg", "inline": True},
            {"name": "Signal", "value": "Heavy buying after softer-than-expected CPI print", "inline": False},
            {"name": "Context", "value": "April CPI came in at 3.1% vs 3.4% expected. Market moved from $0.41 to $0.55 in 2 hours. Top traders piled in.", "inline": False},
        ],
    },
    {
        "title": "📊 Economics Signal — Recession Watch",
        "color": 0xf85149,
        "description": "**Will the US enter recession in 2026?**",
        "fields": [
            {"name": "Outcome", "value": "`Yes` @ **$0.47** ↑", "inline": True},
            {"name": "24h Volume", "value": "$4.2M — highest ever", "inline": True},
            {"name": "Signal", "value": "Record volume — most-traded economics market in Polymarket history", "inline": False},
            {"name": "Context", "value": "Tariff escalation + Q1 GDP miss driving uncertainty. Smart money split — 4 top traders YES, 3 top traders NO. High conviction both sides.", "inline": False},
        ],
    },
]

WINS_POSTS = [
    {
        "plain": (
            "**First win with CopyCat!** 🎉\n\n"
            "Woke up this morning and CopyCat had already placed 3 trades overnight while I was asleep. "
            "Two closed green. Up $47 before I had my coffee.\n\n"
            "This thing is wild. — **Early member**"
        )
    },
    {
        "plain": (
            "**Week 1 results** 📊\n\n"
            "Started with $500 on Monday. CopyCat placed 14 trades.\n"
            "10 wins / 4 losses\n"
            "Net: **+$183**\n\n"
            "Best trade was the Bitcoin $100K YES — caught the whale entry from the morning alert. In at $0.58, out at $0.79."
        )
    },
    {
        "plain": (
            "**The tariff trade alert paid off big** 🏛️\n\n"
            "Got the politics alert at 8 AM. Bought YES on 'Trump extends tariffs' at $0.68. "
            "News broke 3 hours later. Closed at $0.91.\n\n"
            "+$230 on a $150 trade. That's the whole month paid for in one alert."
        )
    },
]

# ─── Post logic ───────────────────────────────────────────────────────────────

async def post_embed(channel, post: dict):
    embed = discord.Embed(
        title=post.get("title", ""),
        description=post.get("description", ""),
        color=post.get("color", 0x3fb950),
    )
    for f in post.get("fields", []):
        embed.add_field(name=f["name"], value=f["value"], inline=f.get("inline", False))
    embed.set_footer(text="CopyCat · copycat.adam-works.co")
    await channel.send(embed=embed)
    await asyncio.sleep(1.5)

async def post_plain(channel, text: str):
    await channel.send(text)
    await asyncio.sleep(1.5)

async def seed(bot: discord.Client):
    print("Starting seed...")

    async def ch(key):
        cid = CHANNELS.get(key, 0)
        if not cid:
            print(f"  SKIP {key} — no channel ID set")
            return None
        c = bot.get_channel(cid)
        if not c:
            print(f"  SKIP {key} — channel {cid} not found")
            return None
        return c

    # Announcements
    c = await ch("announcements")
    if c:
        print("  Seeding #announcements...")
        for post in ANNOUNCEMENTS_POSTS:
            await post_embed(c, post)

    # General welcome
    c = await ch("general")
    if c:
        print("  Seeding #general...")
        await post_plain(c,
            "👋 **Welcome everyone!**\n\n"
            "This is the free community space — talk markets, share wins, ask questions.\n"
            "No spam, no sales pitches here. Just traders talking.\n\n"
            "New? Drop an intro below 👇\n"
            "Want daily alerts? → https://copycat.adam-works.co"
        )

    # Alerts — all
    c = await ch("alerts_all")
    if c:
        print("  Seeding #alerts-all...")
        for post in ALERTS_ALL_POSTS:
            await post_embed(c, post)

    # Alerts — crypto
    c = await ch("alerts_crypto")
    if c:
        print("  Seeding #alerts-crypto...")
        await post_plain(c, "₿ **Crypto alerts channel** — smart money signals on Bitcoin, Ethereum, Solana, and altcoin markets. Posted daily.")
        for post in ALERTS_CRYPTO_POSTS:
            await post_embed(c, post)

    # Alerts — politics
    c = await ch("alerts_politics")
    if c:
        print("  Seeding #alerts-politics...")
        await post_plain(c, "🏛️ **Politics alerts channel** — smart money signals on elections, executive orders, international events, and policy markets. Posted daily.")
        for post in ALERTS_POLITICS_POSTS:
            await post_embed(c, post)

    # Alerts — sports
    c = await ch("alerts_sports")
    if c:
        print("  Seeding #alerts-sports...")
        await post_plain(c, "🏆 **Sports alerts channel** — smart money signals on NFL, NBA, MLB, NHL, soccer, golf, UFC, and more. Posted daily.")
        for post in ALERTS_SPORTS_POSTS:
            await post_embed(c, post)

    # Alerts — economics
    c = await ch("alerts_economics")
    if c:
        print("  Seeding #alerts-economics...")
        await post_plain(c, "📈 **Economics alerts channel** — smart money signals on Fed policy, inflation, GDP, stock market, and macro events. Posted daily.")
        for post in ALERTS_ECONOMICS_POSTS:
            await post_embed(c, post)

    # Wins
    c = await ch("wins")
    if c:
        print("  Seeding #wins...")
        for post in WINS_POSTS:
            await post_plain(c, post["plain"])

    print("\nSeed complete!")
    await bot.close()

# ─── Run ──────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
bot = discord.Client(intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await seed(bot)

if __name__ == "__main__":
    if not TOKEN:
        print("DISCORD_BOT_TOKEN not set in .env")
        raise SystemExit(1)
    bot.run(TOKEN)
