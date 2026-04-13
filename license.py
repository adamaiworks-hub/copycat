"""
CopyCat License Validation
Tiers: MONTHLY | PRO | PRO_KALSHI | TRIAL
"""

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LICENSE_FILE = Path(os.getenv("LICENSE_FILE", Path.home() / ".copycat" / "license.key"))

# ─── Tier definitions ──────────────────────────────────────────────────────────

TIER_LIMITS = {
    "TRIAL": {
        "max_traders":    3,
        "custom_sizing":  False,
        "min_wager":      10,
        "max_wager":      10,
        "max_wager_pct":  2,
        "kalshi_bot":     False,
        "pro_analytics":  False,
        "label":          "Trial",
    },
    "MONTHLY": {
        "max_traders":    5,
        "custom_sizing":  True,
        "min_wager":      5,
        "max_wager":      100,
        "max_wager_pct":  20,
        "kalshi_bot":     False,
        "pro_analytics":  False,
        "label":          "Monthly",
    },
    "PRO": {
        "max_traders":    20,
        "custom_sizing":  True,
        "min_wager":      5,
        "max_wager":      10000,
        "max_wager_pct":  100,
        "kalshi_bot":     False,
        "pro_analytics":  True,
        "label":          "Pro",
    },
    "PRO_KALSHI": {
        "max_traders":    20,
        "custom_sizing":  True,
        "min_wager":      5,
        "max_wager":      10000,
        "max_wager_pct":  100,
        "kalshi_bot":     True,
        "pro_analytics":  True,
        "label":          "Pro + Kalshi",
    },
}

# ─── License data class ────────────────────────────────────────────────────────

@dataclass
class License:
    valid:    bool
    tier:     str
    key:      str
    error:    str = ""

    @property
    def limits(self) -> dict:
        return TIER_LIMITS.get(self.tier, TIER_LIMITS["TRIAL"])

    @property
    def max_traders(self) -> int:
        return self.limits["max_traders"]

    @property
    def custom_sizing(self) -> bool:
        return self.limits["custom_sizing"]

    @property
    def min_wager(self) -> float:
        return float(self.limits.get("min_wager", 5))

    @property
    def max_wager(self) -> float:
        return float(self.limits.get("max_wager", 10000))

    @property
    def max_wager_pct(self) -> float:
        return float(self.limits.get("max_wager_pct", 100))

    @property
    def kalshi_bot(self) -> bool:
        return self.limits["kalshi_bot"]

    @property
    def pro_analytics(self) -> bool:
        return self.limits["pro_analytics"]

    @property
    def label(self) -> str:
        return self.limits["label"]

# ─── Validation ────────────────────────────────────────────────────────────────

def _parse_tier(key: str) -> Optional[str]:
    """
    Key format: CB-{TIER}-{16_HEX_CHARS}
    Examples:
      CB-MONTHLY-a1b2c3d4e5f6g7h8
      CB-PRO-a1b2c3d4e5f6g7h8
      CB-PRO_KALSHI-a1b2c3d4e5f6g7h8
      CB-TRIAL-a1b2c3d4e5f6g7h8
    """
    parts = key.strip().upper().split("-", 2)
    if len(parts) < 3 or parts[0] != "CB":
        return None
    tier = parts[1] if len(parts) == 3 else f"{parts[1]}_{parts[2].split('-')[0]}"
    # Handle PRO_KALSHI which has an extra segment
    raw = key.strip().upper()
    for t in TIER_LIMITS:
        if raw.startswith(f"CB-{t}-"):
            suffix = raw[len(f"CB-{t}-"):]
            if len(suffix) == 16 and all(c in "0123456789ABCDEF" for c in suffix):
                return t
    return None

def validate_key(key: str) -> License:
    """
    Validate a license key.
    In production, swap the body of this function to call your licensing server.
    """
    if not key:
        return License(valid=False, tier="TRIAL", key="", error="No license key provided.")

    tier = _parse_tier(key)
    if tier is None:
        return License(valid=False, tier="TRIAL", key=key,
                       error="Invalid key format. Expected: CB-{TIER}-{16 hex chars}")

    return License(valid=True, tier=tier, key=key)

def load_license() -> License:
    """Load and validate the license from disk."""
    if not LICENSE_FILE.exists():
        return License(valid=False, tier="TRIAL", key="",
                       error="No license found. Run the installer or enter your key in Settings.")
    key = LICENSE_FILE.read_text().strip()
    return validate_key(key)

def save_license(key: str) -> License:
    """Save a license key to disk and return its validation result."""
    lic = validate_key(key)
    if lic.valid:
        LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
        LICENSE_FILE.write_text(key.strip())
    return lic

# ─── Key generator (for you to use when selling) ──────────────────────────────

def generate_key(tier: str) -> str:
    """
    Generate a valid license key for a given tier.
    Use this server-side when a customer completes payment.
    """
    import secrets
    if tier not in TIER_LIMITS:
        raise ValueError(f"Unknown tier: {tier}. Must be one of {list(TIER_LIMITS)}")
    suffix = secrets.token_hex(8).upper()
    return f"CB-{tier}-{suffix}"

if __name__ == "__main__":
    # Quick test / key generator utility
    import sys
    if len(sys.argv) == 2:
        tier = sys.argv[1].upper()
        key = generate_key(tier)
        print(f"Generated key: {key}")
        lic = validate_key(key)
        print(f"Tier: {lic.tier} | Valid: {lic.valid} | Max traders: {lic.max_traders}")
    else:
        print("Usage: python3 license.py MONTHLY|PRO|PRO_KALSHI|TRIAL")
