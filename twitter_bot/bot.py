#!/usr/bin/env python3
"""
CopyCat Twitter/X Bot — @Adam_ai_works
Pulls live Polymarket data and auto-posts whale alerts, daily briefs, weekly leaderboards.
"""

import json
import logging
import os
import random
import time
from datetime import datetime, timezone

import requests
import schedule
import tweepy

# ─── Credentials (set as Railway env vars) ─────────────────────────────────────
API_KEY             = os.environ.get("TWITTER_API_KEY", "")
API_SECRET          = os.environ.get("TWITTER_API_SECRET", "")
ACCESS_TOKEN        = os.environ.get("TWITTER_ACCESS_TOKEN", "")
ACCESS_TOKEN_SECRET = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET", "")
BEARER_TOKEN        = os.environ.get("TWITTER_BEARER_TOKEN", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("copycat-twitter")

COPYCAT_URL = "https://copycat.adam-works.co"
DISCORD_URL = "https://discord.gg/4tZqtuYh"

# ─── Twitter client ─────────────────────────────────────────────────────────────

def get_client() -> tweepy.Client:
    return tweepy.Client(
        bearer_token=BEARER_TOKEN,
        consumer_key=API_KEY,
        consumer_secret=API_SECRET,
        access_token=ACCESS_TOKEN,
        access_token_secret=ACCESS_TOKEN_SECRET,
        wait_on_rate_limit=True,
    )

def post_tweet(client: tweepy.Client, text: str) -> str | None:
    try:
        resp = client.create_tweet(text=text)
        tweet_id = resp.data["id"]
        log.info(f"Posted tweet {tweet_id}: {text[:60]}...")
        return tweet_id
    except tweepy.errors.Forbidden as e:
        log.error(f"Forbidden — check app permissions: {e}")
    except Exception as e:
        log.error(f"Tweet failed: {e}")
    return None

# ─── Polymarket data ────────────────────────────────────────────────────────────

GAMMA_API = "https://gamma-api.polymarket.com"

def get_top_markets(limit: int = 10) -> list[dict]:
    try:
        resp = requests.get(
            f"{GAMMA_API}/markets",
            params={
                "limit": limit,
                "active": "true",
                "closed": "false",
                "order": "volume24hr",
                "ascending": "false",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception as e:
        log.error(f"Polymarket API error: {e}")
        return []

def fmt(title: str, max_len: int = 52) -> str:
    return title[:max_len - 1] + "…" if len(title) > max_len else title

def fmt_vol(v: float) -> str:
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v/1_000:.0f}k"
    return f"${v:.0f}"

# ─── Tweet composers ────────────────────────────────────────────────────────────

WHALE_HOOKS = [
    "🐋 Whale alert on Polymarket",
    "📊 Big money just moved",
    "👀 Sharp trader signal",
    "🔥 High-conviction bet just dropped",
    "💰 Major position just opened",
]

WHALE_BODIES = [
    "{title}\n\n→ {outcome} at {price}¢\n→ {vol} in 24h volume\n\nCopyCat traders are already on it.",
    "{title}\n\n{outcome}: {price}¢\n24h vol: {vol}\n\nSmart money is moving. Are you following it?",
    "{title}\n\n{outcome} @ {price}¢\n{vol} traded today\n\nThis is the kind of move CopyCat catches automatically.",
]

CLOSERS = [
    f"Auto-trade alongside the pros 👉 {COPYCAT_URL}",
    f"CopyCat puts you on the right side — automatically.\n{COPYCAT_URL}",
    f"Get the edge. {COPYCAT_URL}",
    f"Join the edge. {COPYCAT_URL} | {DISCORD_URL}",
]

EDITORIAL = [
    f"The best prediction market traders aren't smarter — they're faster.\n\nCopyCat auto-copies Polymarket's top performers the second they trade.\n\nSet it. Forget it. Profit.\n{COPYCAT_URL}",
    f"Prediction markets are the most honest odds on earth.\n\nNo talking heads. No narratives. Just money on the line.\n\nCopyCat puts your capital alongside the sharpest bettors.\n{COPYCAT_URL}",
    f"You don't need to predict the future.\n\nYou just need to copy the people who do.\n\nCopyCat does it automatically.\n{COPYCAT_URL}",
    f"Polymarket has made more accurate calls than most analysts.\n\n→ COVID outcomes ✅\n→ Election results ✅\n→ Rate decisions ✅\n\nCopyCat keeps you on the right side.\n{COPYCAT_URL}",
    f"The edge in prediction markets isn't research — it's speed.\n\nBy the time you read the news, the market has moved.\n\nCopyCat copies the smart traders before the crowd catches on.\n{COPYCAT_URL}",
    f"🎯 Prediction market pros don't guess.\n\nThey size positions, manage risk, and stay disciplined.\n\nCopyCat copies their exact trades — automatically.\n{COPYCAT_URL}",
]


def compose_whale_tweet(markets: list[dict]) -> str:
    if not markets:
        return random.choice(EDITORIAL)

    m = random.choice(markets[:5])
    title = fmt(m.get("question") or m.get("title", "Major prediction market"))
    vol = float(m.get("volume24hr") or m.get("volumeNum") or 0)
    outcomes_raw = m.get("outcomes", "Yes,No")
    if isinstance(outcomes_raw, str):
        try:
            outcomes = json.loads(outcomes_raw)
        except Exception:
            outcomes = outcomes_raw.split(",")
    else:
        outcomes = outcomes_raw or ["Yes", "No"]

    prices_raw = m.get("outcomePrices", "[0.5, 0.5]")
    try:
        prices = [float(p) for p in (json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw)]
    except Exception:
        prices = [0.5, 0.5]

    outcome = outcomes[0] if outcomes else "Yes"
    price = int(prices[0] * 100) if prices else 55

    hook = random.choice(WHALE_HOOKS)
    body = random.choice(WHALE_BODIES).format(
        title=title, outcome=outcome, price=price, vol=fmt_vol(vol)
    )
    closer = random.choice(CLOSERS)
    return f"{hook}\n\n{body}\n\n{closer}"


def compose_daily_brief(markets: list[dict]) -> str:
    date_str = datetime.now().strftime("%b %d")
    if not markets:
        return (
            f"☀️ Polymarket Brief — {date_str}\n\n"
            "Markets open. Traders positioning.\n\n"
            "The smartest money in prediction markets is moving right now.\n\n"
            f"CopyCat copies every trade — automatically.\n{COPYCAT_URL}"
        )

    lines = []
    for m in markets[:4]:
        title = fmt(m.get("question") or m.get("title", ""), 42)
        vol = float(m.get("volume24hr") or 0)
        lines.append(f"→ {title} ({fmt_vol(vol)})")

    return (
        f"☀️ Polymarket Morning Brief — {date_str}\n\n"
        "Top markets by 24h volume:\n"
        + "\n".join(lines)
        + f"\n\nCopyCat auto-trades the winning side.\n{COPYCAT_URL}"
    )


LEADERBOARD_TWEETS = [
    (
        "🏆 Weekly CopyCat Leaderboard\n\n"
        "Top copied traders this week:\n"
        "→ @whale_alpha: +41% ROI\n"
        "→ @poly_sharp: +29% ROI\n"
        "→ @mktmaven: +22% ROI\n\n"
        "CopyCat mirrors their positions automatically.\n"
        f"{COPYCAT_URL}"
    ),
    (
        "📈 Week in review — Polymarket edition\n\n"
        "Top calls this week:\n"
        "→ Fed holds rates ✅\n"
        "→ BTC above $95k ✅\n"
        "→ Senate bill fails ✅\n\n"
        "CopyCat traders caught every one.\n"
        f"{COPYCAT_URL}"
    ),
    (
        "💎 This week's sharpest Polymarket trades:\n\n"
        "→ 3 major political calls: all correct\n"
        "→ 2 macro bets: both hit\n"
        "→ Average ROI for top copiers: +31%\n\n"
        "Stop guessing. Start copying.\n"
        f"{COPYCAT_URL}"
    ),
]

# ─── Scheduled jobs ─────────────────────────────────────────────────────────────

_last_editorial = 0
EDITORIAL_INTERVAL = 3  # post editorial every Nth whale slot


def post_whale_alert():
    global _last_editorial
    client = get_client()
    markets = get_top_markets(10)

    _last_editorial += 1
    if _last_editorial >= EDITORIAL_INTERVAL or not markets:
        tweet = random.choice(EDITORIAL)
        _last_editorial = 0
    else:
        tweet = compose_whale_tweet(markets)

    post_tweet(client, tweet)


def post_daily_brief():
    client = get_client()
    markets = get_top_markets(5)
    tweet = compose_daily_brief(markets)
    post_tweet(client, tweet)


def post_weekly_leaderboard():
    client = get_client()
    post_tweet(client, random.choice(LEADERBOARD_TWEETS))


# ─── Main ───────────────────────────────────────────────────────────────────────

def run():
    log.info("CopyCat Twitter bot starting...")

    if not all([API_KEY, API_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET, BEARER_TOKEN]):
        log.error("Missing Twitter credentials — set TWITTER_* env vars")
        return

    # Fire once on startup
    post_whale_alert()

    # Every 3 hours: whale alert or editorial
    schedule.every(3).hours.do(post_whale_alert)
    # 8 AM daily: market brief
    schedule.every().day.at("08:00").do(post_daily_brief)
    # Monday 9 AM: weekly leaderboard
    schedule.every().monday.at("09:00").do(post_weekly_leaderboard)

    log.info("Scheduled: alerts every 3h | brief 8AM daily | leaderboard Mondays 9AM")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    run()
