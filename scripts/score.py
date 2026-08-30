#!/usr/bin/env python3
"""Scoring policy. Change what ranks well by editing the constants here.

Two tracks, deliberately not collapsed into one number. For a PC-only
contributor they rank almost inversely: the money is on Expensify, which
carries no hiring path, and the hiring signal is on comma and tinygrad, whose
PC-reachable tier is nine bounties deep and heavily contested. A single score
would average that tension away — the whole point is to see both.

MONEY  = amount / hours / (1 + competitors)
SIGNAL = source weight * amount tier * winnability

The competitor divisor treats the race as uniform odds among everyone already
in it. That is pessimistic — a careful contributor beats the median drive-by PR
— but a model that assumed you win would rank a $100 bounty with twelve open
PRs above real work, which is exactly the trap this tool exists to avoid.
"""

import datetime as dt
import math

from bounty import STATUS_LOCKED

# How long a board leaves a lock in place with no forward progress before the
# bounty is available again. Both boards publish these; neither enforces them
# automatically, so a lock older than its own window is a bounty that is open
# in fact while still reading as taken. That gap is the only uncrowded edge
# this tool can find, so it is a first-class output rather than a footnote.
LOCK_LAPSE_DAYS = {
    "comma": 7,        # "times out after a week of no progress"
    "tinygrad": 5,     # "stays locked if I see forward progress in last 5 days"
    # Never. An assigned Expensify issue is a signed Upwork contract with a paid
    # contributor on it, not a soft claim that decays. Ageing it out would put
    # someone else's paid work at the top of the report as though it were free.
    "expensify": None,
}
DEFAULT_LOCK_LAPSE_DAYS = 14   # unstated elsewhere; conservative

# Does landing this convert into interviews? tinygrad states bounties are the
# only path to a job there; comma describes "a continuous spectrum from external
# contributor to full-time engineer". Expensify is contract work via Upwork with
# no such path attached, so its signal is near zero however well it pays.
SOURCE_SIGNAL = {
    "tinygrad": 1.00,
    "comma": 0.90,
    "tenstorrent": 0.60,
    "expensify": 0.15,
}
DEFAULT_SOURCE_SIGNAL = 0.30

# The share of a board's payouts that actually reach an OUTSIDE contributor,
# measured from closed issues rather than assumed from the board's own docs.
#
# Expensify is 0.05 because its contributor seat is largely automated. MelvinBot
# posts the first proposal and is reviewed first; contributors may only propose a
# "meaningfully different" approach; Melvin then implements and the Contributor+
# owns the PR. Measured 2026-08-30 across the 60 most recently closed Help Wanted
# issues: 7 carried a payment summary, 5 paid a reviewer/C+, and NONE recorded an
# external contributor as owed. Live queue entries read "Contributor: @x does not
# require payment (Contractor)" beside "Reviewer: @y owed $250".
#
# It is not 0.0 because only 7 of 60 recorded a summary at all and Expensify also
# pays through Upwork, which leaves no GitHub trace — absence of evidence, not
# evidence of absence. But a board whose visible payouts never reach an outsider
# must not outrank one whose payouts demonstrably do.
EXTERNAL_WIN_RATE = {
    "expensify": 0.05,
    "comma": 1.0,
    "tinygrad": 1.0,
    "tenstorrent": 1.0,
}
DEFAULT_EXTERNAL_WIN_RATE = 1.0

# A bounty seen for the first time in the last few days has not yet been found
# by everyone else. Competition counts lag reality by roughly this long, so
# newness is scored directly instead of being inferred from a stale count.
FRESH_DAYS = 4
FRESH_BONUS = 1.35

# In-stack work is faster and likelier to pass review. Derived from the fleet's
# own languages rather than declared by hand.
KNOWN_STACK = {"typescript", "javascript", "python", "react"}
STACK_BONUS = 1.20


def _days_since(iso: str | None, today: dt.date | None = None) -> float:
    if not iso:
        return float("inf")
    today = today or dt.date.today()
    try:
        when = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).date()
    except ValueError:
        return float("inf")
    return (today - when).days


def lock_is_stale(b, today: dt.date | None = None) -> bool:
    """True when a locked bounty has outlived its own board's lapse window.

    A bounty whose board publishes no timestamp can never be shown as lapsed.
    `_days_since` returns infinity for a missing date, so testing it directly
    would mark every undated lock stale — which is exactly backwards, and would
    have declared all of tinygrad's claimed rows and every assigned Expensify
    issue free for the taking.
    """
    if b.status != STATUS_LOCKED:
        return False
    if not b.updated_at:
        return False
    window = LOCK_LAPSE_DAYS.get(b.source, DEFAULT_LOCK_LAPSE_DAYS)
    if window is None:
        return False
    age = _days_since(b.updated_at, today)
    return age != float("inf") and age > window


def is_claimable(b, today: dt.date | None = None) -> bool:
    """Can someone start on this right now and expect to be paid for it?"""
    if b.status == STATUS_LOCKED:
        return lock_is_stale(b, today)
    return True


def money_score(b, hours: float, today: dt.date | None = None) -> float | None:
    """Expected dollars per hour. None when the board publishes no price.

    None is not zero. An unpriced bounty is unranked on this track and reported
    separately; folding it in as zero would bury Expensify, which is both
    unpriced and the only board here with real volume.
    """
    if b.amount_usd is None:
        return None
    if not is_claimable(b, today):
        return 0.0
    if hours <= 0:
        return 0.0
    payout = EXTERNAL_WIN_RATE.get(b.source, DEFAULT_EXTERNAL_WIN_RATE)
    return (b.amount_usd / hours) / (1 + max(0, b.competitors)) * payout


def signal_score(b, today: dt.date | None = None) -> float:
    """Hiring-signal value of landing this, on an arbitrary 0-100 scale."""
    if not is_claimable(b, today):
        return 0.0
    weight = SOURCE_SIGNAL.get(b.source, DEFAULT_SOURCE_SIGNAL)
    # Bigger bounties are more substantial merged work, but the returns are
    # sharply diminishing — a $10k car port is not 100x the signal of a $100 fix.
    tier = math.log10(max(b.amount_usd or 100, 100))
    winnability = 1 / (1 + max(0, b.competitors))
    return 100 * weight * tier * winnability


def bonuses(b, today: dt.date | None = None) -> tuple[float, list[str]]:
    """Ranking multiplier and its reasons.

    This scales the SORT KEY only. It must never be multiplied into the money
    figure itself: "$405/h" for a bounty actually paying $250/h is not a
    weighted score, it is a wrong number in real units, and the report is read
    for those units.
    """
    mult, why = 1.0, []
    if _days_since(b.first_seen, today) <= FRESH_DAYS:
        mult *= FRESH_BONUS
        why.append("new")
    if KNOWN_STACK & {s.lower() for s in b.stack}:
        mult *= STACK_BONUS
        why.append("in-stack")
    if lock_is_stale(b, today):
        why.append("lock lapsed")
    return mult, why
