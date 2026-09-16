#!/usr/bin/env python3
"""Effort in hours, with its provenance attached.

Precedence is deliberate: a board that publishes its own price-to-time ladder is
authoritative and free, so it is never overridden by a guess. Only the gap gets
estimated, and only once — results are cached by content hash, so a run that
finds no new bounties makes no API calls at all.

Provenance travels with the number because the three sources are not equally
trustworthy and a report that hides which one produced a given figure invites
the reader to believe the weakest of them.
"""

import json
import os
import pathlib
import shutil
import subprocess

STATED = "stated"        # the board's own published ladder
MODELED = "modeled"      # Claude read the issue
HEURISTIC = "heuristic"  # fitted curve; no key available

CACHE_PATH = pathlib.Path(__file__).resolve().parent.parent / "state" / "estimates.json"

# comma publishes this on its bounty board, in its own words:
#   <=$100 "a good intro"      $300 "a day of work"
#   $500 "a few days"          $1k+ "a week of work"
# Converted at 8h/day, taking the midpoint of ranged phrasings.
COMMA_LADDER = ((100, 3), (300, 8), (500, 20), (float("inf"), 40))

# tinygrad publishes a finer one on its sheet:
#   $100 "a few line change, could be 10 minutes"
#   $200 "a couple hours to a day"    $500 "a couple days, a few prereqs"
#   $1000 "some refactoring to core"  $1000+ "a solid week+"
TINYGRAD_LADDER = ((100, 0.5), (200, 6), (500, 16), (1000, 40), (float("inf"), 60))

LADDERS = {"comma": COMMA_LADDER, "tinygrad": TINYGRAD_LADDER}

# Fitted from the two published ladders above, for boards that publish none.
# Wrong wherever a board prices differently — Expensify's $250-500 issues are
# often 1-2h React fixes, which this curve overstates — so it is the last resort
# and is always labeled as such in the report.
FALLBACK_LADDER = ((100, 2), (300, 8), (500, 20), (1000, 40), (float("inf"), 60))

# Expensify publishes no price on the issue at all; the figure lives in the
# Upwork job. Its own contributing guide and the public bounty listings put the
# common band at $250-500, so an unpriced Expensify issue is scored at the
# bottom of that band rather than dropped.
ASSUMED_AMOUNT = {"expensify": 250}


def _ladder_hours(amount: int, ladder) -> float:
    for cap, hours in ladder:
        if amount <= cap:
            return hours
    return ladder[-1][1]


def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))


def _ask_claude(b) -> float | None:
    """Estimate hours by having Claude read the issue. None if unavailable.

    Shells out to the `claude` CLI rather than taking an SDK dependency, so the
    tool stays stdlib-only at runtime and simply degrades when no CLI is present.
    """
    if not (os.environ.get("ANTHROPIC_API_KEY") or shutil.which("claude")):
        return None
    prompt = (
        "Estimate how many engineer-hours this open-source issue takes for a "
        "competent contributor who does not yet know the codebase. Reply with "
        "one number, nothing else.\n\n"
        f"Repo: {b.ref}\nTitle: {b.title}\n\n{b.body[:4000]}"
    )
    try:
        r = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            return None
        return float(r.stdout.strip().split()[0].rstrip("h"))
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def effort_hours(b, cache: dict | None = None, allow_model: bool = True):
    """Return (hours, provenance). Never returns 0 or a negative."""
    ladder = LADDERS.get(b.source)
    amount = b.amount_usd if b.amount_usd is not None else ASSUMED_AMOUNT.get(b.source)

    if ladder is not None and amount is not None:
        return max(0.25, _ladder_hours(amount, ladder)), STATED

    if cache is not None and b.body_hash in cache:
        return max(0.25, float(cache[b.body_hash])), MODELED

    if allow_model:
        modeled = _ask_claude(b)
        if modeled and modeled > 0:
            if cache is not None:
                cache[b.body_hash] = modeled
            return max(0.25, modeled), MODELED

    return max(0.25, _ladder_hours(amount or 250, FALLBACK_LADDER)), HEURISTIC
