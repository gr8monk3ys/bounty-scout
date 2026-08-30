"""Board adapters. Each returns a list of normalized Bounty records.

Every adapter declares MIN_EXPECTED — the count below which its result is
treated as a fetch failure rather than a drained board. This exists because the
opposite behaviour has already cost this fleet real time: a sweep that forgot
INCLUDE_BOTS=1 reported 19 open PRs when there were 137, and nothing in the run
looked wrong. A board that has genuinely emptied is rare; an adapter broken by a
board changing shape is not. Fail loudly and let a human decide which happened.
"""

import json
import subprocess
import time

_TRANSIENT = ("timeout", "connection reset", "502", "503", "rate limit",
              "temporarily unavailable", "bad gateway")


class SourceError(RuntimeError):
    """An adapter could not trust its own result."""


def run_gh(argv, retries=4):
    """subprocess.run for gh, retrying transient network and rate failures."""
    delay = 2.0
    r = None
    for attempt in range(retries + 1):
        r = subprocess.run(argv, capture_output=True, text=True)
        if r.returncode == 0:
            return r
        if attempt < retries and any(
            s in (r.stderr or "").lower() for s in _TRANSIENT
        ):
            time.sleep(delay)
            delay *= 2
            continue
        return r
    return r


def gh_json(argv, retries=4):
    r = run_gh(argv, retries=retries)
    if r.returncode != 0:
        raise SourceError(f"gh failed: {' '.join(argv[:4])}...: {r.stderr.strip()[:300]}")
    try:
        return json.loads(r.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise SourceError(f"gh returned non-JSON for {' '.join(argv[:4])}...") from exc


def guard_count(source: str, items: list, minimum: int, note: str = "") -> list:
    if len(items) < minimum:
        raise SourceError(
            f"{source}: got {len(items)} bounties, expected >={minimum}. "
            f"Treating as a fetch failure, not an empty board. {note}".strip()
        )
    return items
