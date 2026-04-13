"""
CopyCat — Bot Configuration Manager
Stores and loads user preferences: wager sizing, trader selection, category filters.
"""

import json
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "bot_config.json"

# ─── Category hierarchy ────────────────────────────────────────────────────────

CATEGORY_HIERARCHY = {
    "sports": {
        "label": "Sports",
        "icon": "🏆",
        "children": {
            "nfl":    {"label": "NFL",    "icon": "🏈", "teams": ["Chiefs","Eagles","Cowboys","49ers","Ravens","Bills","Bengals","Lions","Packers","Bears","Patriots","Rams","Chargers","Raiders","Broncos","Seahawks","Saints","Falcons","Panthers","Buccaneers","Vikings","Giants","Jets","Commanders","Cardinals","Steelers","Browns","Titans","Colts","Jaguars","Texans","Dolphins"]},
            "nba":    {"label": "NBA",    "icon": "🏀", "teams": ["Lakers","Celtics","Warriors","Bucks","Nuggets","Heat","Nets","76ers","Suns","Clippers","Bulls","Knicks","Hawks","Mavericks","Grizzlies","Pelicans","Jazz","Spurs","Thunder","Trail Blazers","Rockets","Kings","Pistons","Pacers","Hornets","Magic","Wizards","Cavaliers","Raptors","Timberwolves"]},
            "mlb":    {"label": "MLB",    "icon": "⚾", "teams": ["Yankees","Dodgers","Red Sox","Cubs","Giants","Mets","Astros","Braves","Cardinals","Phillies","Blue Jays","Padres","Mariners","Rays","Orioles","Tigers","Twins","White Sox","Angels","Athletics","Rangers","Reds","Brewers","Pirates","Nationals","Marlins","Rockies","Diamondbacks","Guardians","Royals"]},
            "nhl":    {"label": "NHL",    "icon": "🏒", "teams": ["Bruins","Rangers","Maple Leafs","Canadiens","Blackhawks","Penguins","Capitals","Lightning","Oilers","Flames","Canucks","Jets","Senators","Red Wings","Panthers","Stars","Blues","Avalanche","Golden Knights","Wild","Ducks","Sharks","Kings","Coyotes","Sabres","Flyers","Islanders","Devils","Blue Jackets","Predators","Hurricanes","Kraken"]},
            "soccer": {"label": "Soccer", "icon": "⚽", "teams": ["Manchester City","Real Madrid","Barcelona","Bayern Munich","PSG","Liverpool","Arsenal","Chelsea","Juventus","Inter Milan","AC Milan","Atletico Madrid","Borussia Dortmund","Manchester United","Tottenham","Newcastle","Napoli","Lazio","Porto","Benfica"]},
            "mma":    {"label": "MMA/UFC","icon": "🥊", "teams": []},
            "golf":   {"label": "Golf",   "icon": "⛳", "teams": []},
            "tennis": {"label": "Tennis", "icon": "🎾", "teams": []},
            "f1":     {"label": "F1",     "icon": "🏎️", "teams": ["Red Bull","Ferrari","Mercedes","McLaren","Aston Martin","Alpine","Williams","AlphaTauri","Alfa Romeo","Haas"]},
        }
    },
    "crypto": {
        "label": "Crypto",
        "icon": "₿",
        "children": {
            "bitcoin":  {"label": "Bitcoin",  "icon": "₿",  "teams": []},
            "ethereum": {"label": "Ethereum", "icon": "Ξ",  "teams": []},
            "solana":   {"label": "Solana",   "icon": "◎",  "teams": []},
            "altcoins": {"label": "Altcoins", "icon": "🪙", "teams": []},
        }
    },
    "politics": {
        "label": "Politics",
        "icon": "🏛️",
        "children": {
            "us_elections":    {"label": "US Elections",    "icon": "🗳️", "teams": []},
            "trump":           {"label": "Trump / Admin",   "icon": "🇺🇸", "teams": []},
            "congress":        {"label": "Congress",        "icon": "🏛️", "teams": []},
            "international":   {"label": "International",   "icon": "🌍", "teams": []},
        }
    },
    "economics": {
        "label": "Economics",
        "icon": "📈",
        "children": {
            "fed":       {"label": "Federal Reserve", "icon": "🏦", "teams": []},
            "inflation": {"label": "Inflation / CPI", "icon": "💵", "teams": []},
            "stocks":    {"label": "Stock Market",    "icon": "📊", "teams": []},
            "gdp":       {"label": "GDP / Jobs",      "icon": "💼", "teams": []},
        }
    },
}

# Keywords used to match Polymarket market slugs/titles to categories
CATEGORY_KEYWORDS = {
    "nfl":         ["nfl","super-bowl","touchdown","quarterback","chiefs","eagles","cowboys","49ers","ravens","bills","bengals","lions","packers","bears","patriots","rams","chargers","raiders","broncos","seahawks","saints","falcons","panthers","buccaneers","vikings","giants","jets","commanders","cardinals","steelers","browns","titans","colts","jaguars","texans","dolphins"],
    "nba":         ["nba","basketball","lakers","celtics","warriors","bucks","nuggets","heat","nets","76ers","suns","clippers","bulls","knicks","hawks","mavericks","grizzlies","pelicans","jazz","spurs","thunder","trail-blazers","rockets","kings","pistons","pacers","hornets","magic","wizards","cavaliers","raptors","timberwolves"],
    "mlb":         ["mlb","baseball","world-series","yankees","dodgers","red-sox","cubs","giants","mets","astros","braves","cardinals","phillies","blue-jays","padres","mariners","rays","orioles","tigers","twins","white-sox","angels","athletics","rangers","reds","brewers","pirates","nationals","marlins","rockies","diamondbacks","guardians","royals"],
    "nhl":         ["nhl","hockey","stanley-cup","bruins","rangers","maple-leafs","canadiens","blackhawks","penguins","capitals","lightning","oilers","flames","canucks","jets","senators","red-wings","panthers","stars","blues","avalanche","golden-knights","wild","ducks","sharks","kings","sabres","flyers","islanders","devils","predators","hurricanes","kraken"],
    "soccer":      ["soccer","football","premier-league","champions-league","fifa","world-cup","la-liga","bundesliga","serie-a","ligue-1","manchester","real-madrid","barcelona","bayern","psg","liverpool","arsenal","chelsea","juventus"],
    "mma":         ["ufc","mma","boxing","fight","knockout","champion"],
    "golf":        ["golf","pga","masters","us-open","british-open","ryder-cup"],
    "tennis":      ["tennis","wimbledon","us-open","french-open","australian-open","grand-slam","djokovic","federer","nadal","serena"],
    "f1":          ["formula-1","f1","grand-prix","verstappen","hamilton","ferrari","red-bull","mercedes","mclaren"],
    "bitcoin":     ["bitcoin","btc","satoshi"],
    "ethereum":    ["ethereum","eth","ether"],
    "solana":      ["solana","sol"],
    "altcoins":    ["xrp","ripple","dogecoin","doge","cardano","ada","polygon","matic","chainlink","link","avalanche","avax","polkadot","dot","shiba","pepe","altcoin"],
    "us_elections":["election","vote","ballot","2024","2026","2028","senate-race","house-race","governor"],
    "trump":       ["trump","maga","executive-order","tariff","white-house","administration"],
    "congress":    ["congress","senate","house","bill","legislation","filibuster","speaker"],
    "international":["uk","europe","china","russia","ukraine","nato","israel","iran","north-korea","xi","putin","macron","modi"],
    "fed":         ["federal-reserve","fed","fomc","interest-rate","rate-cut","rate-hike","powell","basis-points"],
    "inflation":   ["inflation","cpi","pce","deflation","price-index"],
    "stocks":      ["s&p","nasdaq","dow","stock-market","sp500","spy","qqq","bull","bear","recession"],
    "gdp":         ["gdp","unemployment","jobs","payroll","labor","recession","growth"],
}

# ─── Default config ────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "wager_mode":        "fixed",   # "fixed" or "pct"
    "wager_fixed":       10,        # dollars
    "wager_pct":         2,         # percent of balance
    "daily_loss_limit":  100,       # dollars
    "selected_traders":  [],        # list of addresses — empty = copy all
    "categories": {
        "sports":    {"enabled": True,  "sports": [], "teams": []},
        "crypto":    {"enabled": True,  "sports": [], "teams": []},
        "politics":  {"enabled": True,  "sports": [], "teams": []},
        "economics": {"enabled": True,  "sports": [], "teams": []},
    },
    "scan_all_simultaneously": True,
    "tos_accepted": False,
}

# ─── Load / save ───────────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return DEFAULT_CONFIG.copy()
    try:
        with open(CONFIG_FILE) as f:
            saved = json.load(f)
        # Merge with defaults so new keys always exist
        config = DEFAULT_CONFIG.copy()
        config.update(saved)
        return config
    except Exception:
        return DEFAULT_CONFIG.copy()

def save_config(config: dict):
    CONFIG_FILE.write_text(json.dumps(config, indent=2))

def market_matches_filters(slug: str, title: str, config: dict) -> bool:
    """
    Returns True if a market slug/title matches the user's category filters.
    If all categories disabled or no filter set, returns True (scan everything).
    """
    cats = config.get("categories", {})
    any_enabled = any(v.get("enabled") for v in cats.values())
    if not any_enabled:
        return True  # nothing filtered = scan all

    text = (slug + " " + title).lower().replace(" ", "-")

    for cat_key, cat_cfg in cats.items():
        if not cat_cfg.get("enabled"):
            continue

        selected_sports = cat_cfg.get("sports", [])
        selected_teams  = cat_cfg.get("teams", [])

        # Check top-level category
        if cat_key in CATEGORY_HIERARCHY:
            children = CATEGORY_HIERARCHY[cat_key].get("children", {})

            # If specific sub-sports selected, check those
            if selected_sports:
                for sport_key in selected_sports:
                    keywords = CATEGORY_KEYWORDS.get(sport_key, [])
                    # If specific teams selected within this sport, check teams
                    if selected_teams:
                        for team in selected_teams:
                            if team.lower().replace(" ", "-") in text:
                                return True
                    else:
                        if any(kw in text for kw in keywords):
                            return True
            else:
                # No specific sport — match anything in the category
                for sport_key in children:
                    keywords = CATEGORY_KEYWORDS.get(sport_key, [])
                    if any(kw in text for kw in keywords):
                        return True

    return False
