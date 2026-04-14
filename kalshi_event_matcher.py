#!/usr/bin/env python3
"""
Kalshi Event Matcher
──────────────────────────────────────────────────────────────────
Given a Polymarket trade (market title, outcome, end date), finds
the matching Kalshi ticker and determines which side to buy.

Matching strategy:
  1. Refresh / cache all open Kalshi markets every 30 min
  2. For each Polymarket trade, extract keywords + event date
  3. Score all Kalshi markets on keyword overlap + date proximity
  4. Verify same trade type (winner-pick vs spread/total)
  5. Map Polymarket YES/NO → Kalshi yes/no side
"""

import re
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

log = logging.getLogger("kalshi-copycat.matcher")

# Phrases that confirm a "who wins" market
WINNER_SIGNALS = frozenset([
    "win", "wins", "winner", "beat", "beats", "defeat",
    "champion", "championship", "title", "who will",
    "reach", "exceed", "above", "below", "hit",          # price targets
    "clinch", "advance", "qualify",
])

# Phrases that disqualify a winner market (it's a prop / spread / total)
NOT_WINNER = [
    "over/under", "total points", "total score", "total runs",
    "spread", "ats ", "against the spread", "handicap",
    "first to score", "how many", "halftime score", "quarter",
    "series length", "game 1", "game 2", "game 3", "game 4", "game 5",
    "player props", "anytime scorer", "first basket",
    "assists", "rebounds", "strikeouts", "home runs",
    "distance covered", "fastest lap",
]

NOISE_WORDS = frozenset([
    "will","the","a","an","in","on","at","to","be","of","by","for","do",
    "who","win","wins","winner","beat","vs","versus","game","match","bout",
    "series","2025","2026","nba","nfl","mlb","nhl","ufc","mma",
    "and","or","is","are","its","have","has","had","was","were",
    "season","week","day","night","tonight","monday","tuesday","wednesday",
    "thursday","friday","saturday","sunday","january","february","march",
    "april","may","june","july","august","september","october","november",
    "december","jan","feb","mar","apr","jun","jul","aug","sep","oct",
    "nov","dec",
])


class EventMatcher:
    def __init__(self, kalshi_client):
        self.client          = kalshi_client
        self._markets:       list[dict] = []
        self._last_refresh:  float      = 0.0
        self._match_cache:   dict       = {}   # poly_cache_key → (ticker, side) | None

    # ── Market refresh ────────────────────────────────────────────────────────

    def refresh(self, force: bool = False):
        """Refresh Kalshi market index every 30 minutes."""
        if not force and time.time() - self._last_refresh < 1800:
            return
        log.info("Refreshing Kalshi market index…")
        markets, cursor = [], None
        try:
            while True:
                params = {"limit": 200, "status": "open"}
                if cursor:
                    params["cursor"] = cursor
                data   = self.client._get("/markets", **params)
                batch  = data.get("markets", [])
                markets.extend(batch)
                cursor = data.get("cursor")
                if not cursor or not batch:
                    break
            self._markets       = markets
            self._last_refresh  = time.time()
            self._match_cache.clear()   # stale after refresh
            log.info(f"Kalshi market index: {len(markets)} open markets")
        except Exception as e:
            log.warning(f"Market refresh error: {e}")

    # ── Classification helpers ─────────────────────────────────────────────────

    @staticmethod
    def is_winner_market(title: str) -> bool:
        tl = title.lower()
        for phrase in NOT_WINNER:
            if phrase in tl:
                return False
        words = set(re.sub(r"[^\w\s]", " ", tl).split())
        return bool(words & WINNER_SIGNALS)

    @staticmethod
    def tokenize(title: str) -> set[str]:
        words = re.sub(r"[^\w\s]", " ", title.lower()).split()
        return {w for w in words if w not in NOISE_WORDS and len(w) >= 3}

    def _score(self, poly_title: str, kalshi_title: str) -> float:
        pk = self.tokenize(poly_title)
        kk = self.tokenize(kalshi_title)
        if not pk or not kk:
            return 0.0
        overlap = pk & kk
        return len(overlap) / max(len(pk), len(kk))

    @staticmethod
    def _parse_dt(s: str) -> Optional[datetime]:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    # ── Subject extraction (for side mapping) ─────────────────────────────────

    @staticmethod
    def extract_subject(title: str) -> str:
        """
        Pull the primary entity (team name, coin, candidate) from a title.
        E.g. "Will the Lakers beat the Heat?" → "Lakers"
        """
        m = re.search(
            r"[Ww]ill\s+(?:the\s+)?([A-Z][a-zA-Z ]{2,28}?)\s+"
            r"(?:win|beat|defeat|reach|finish|advance|clinch|make)",
            title,
        )
        if m:
            return m.group(1).strip()
        m = re.search(r"^([A-Z][a-zA-Z ]{2,24}?)\s+(?:vs?\.?|@|versus)\s+", title)
        if m:
            return m.group(1).strip()
        return ""

    def _map_side(self, poly_title: str, poly_outcome: str,
                  kalshi_title: str) -> str:
        """
        Returns "yes" or "no" — the Kalshi side that corresponds to
        the Polymarket outcome.
        """
        poly_out = poly_outcome.upper()

        poly_subj   = self.extract_subject(poly_title).lower()
        kalshi_subj = self.extract_subject(kalshi_title).lower()

        # If we can't determine subjects, default to same side
        if not poly_subj or not kalshi_subj:
            return "yes" if poly_out == "YES" else "no"

        # Build word sets for each subject
        poly_words   = set(poly_subj.split())
        kalshi_words = set(kalshi_subj.split())
        same_subject = bool(poly_words & kalshi_words)

        if poly_out == "YES":
            # We believe the poly subject wins → buy "yes" if Kalshi is about the same entity
            return "yes" if same_subject else "no"
        else:  # NO on Polymarket = we think the poly subject loses
            return "no" if same_subject else "yes"

    # ── Main matching entry point ─────────────────────────────────────────────

    def match(
        self,
        poly_title:   str,
        poly_outcome: str,
        poly_end_iso: str = "",
    ) -> Optional[tuple[str, str]]:
        """
        Find the best Kalshi market for a Polymarket trade.

        Returns (kalshi_ticker, kalshi_side) or None if no good match.
          kalshi_side = "yes" | "no"
        """
        self.refresh()

        cache_key = f"{poly_title}::{poly_outcome}"
        if cache_key in self._match_cache:
            return self._match_cache[cache_key]

        # Polymarket market must be a winner-pick type
        if not self.is_winner_market(poly_title):
            self._match_cache[cache_key] = None
            return None

        poly_end = self._parse_dt(poly_end_iso)

        best_ticker = None
        best_side   = None
        best_score  = 0.30   # minimum match threshold

        for mkt in self._markets:
            title = mkt.get("title") or mkt.get("question") or ""
            if not title:
                continue

            # Kalshi market must also be a winner-pick type
            if not self.is_winner_market(title):
                continue

            score = self._score(poly_title, title)
            if score <= best_score:
                continue

            # Date proximity check (within 48 h if we have both dates)
            if poly_end:
                close = self._parse_dt(
                    mkt.get("close_time") or mkt.get("expiration_time") or ""
                )
                if close and abs((close - poly_end).total_seconds()) > 48 * 3600:
                    continue

            side = self._map_side(poly_title, poly_outcome, title)
            best_ticker = mkt["ticker"]
            best_side   = side
            best_score  = score

        result = (best_ticker, best_side) if best_ticker else None
        self._match_cache[cache_key] = result

        if result:
            log.debug(f"Match: '{poly_title[:50]}' → {best_ticker} side={best_side} (score={best_score:.2f})")
        else:
            log.debug(f"No Kalshi match for: '{poly_title[:60]}'")

        return result
