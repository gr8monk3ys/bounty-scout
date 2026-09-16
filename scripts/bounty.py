#!/usr/bin/env python3
"""The normalized bounty record — the one shape every board adapter produces.

Two fields exist to prevent a specific class of bug rather than to carry data:

`amount_usd` is None, never 0, when a board publishes no price. Expensify keeps
its price in the Upwork job rather than the GitHub issue, and coercing that to
zero would rank the highest-volume board dead last. This is the same shape as
counting `requirements*.txt` as a lockfile: a missing value silently read as a
known one.

`amount_is_stated` separates what a board published from what we inferred. An
inferred amount is still useful for ranking and must never be rendered as though
the board promised it.
"""

import hashlib
import re
from dataclasses import dataclass, field, replace

# Hardware gates, normalized across boards so the reachability filter is one
# comparison instead of four board-specific rules. Ordered least to most gated.
HARDWARE_NONE = "none"        # a PC is enough
HARDWARE_GPU = "gpu"          # a consumer NVIDIA/AMD card
HARDWARE_DEVICE = "device"    # vendor hardware (comma 3X/4)
HARDWARE_CAR = "car"          # a vehicle, directly or via a Discord tester
HARDWARE_SILICON = "silicon"  # accelerator silicon (Tenstorrent)
HARDWARE_UNKNOWN = "unknown"  # the board states a gate we could not read

STATUS_OPEN = "open"
STATUS_LOCKED = "locked"


@dataclass(frozen=True)
class Bounty:
    source: str
    ref: str                      # "commaai/openpilot#33207" — stable identity
    title: str
    url: str
    amount_usd: int | None
    amount_is_stated: bool
    hardware: str
    status: str
    competitors: int              # people already in: PRs, claims, proposals
    updated_at: str               # ISO8601; drives the stale-lock computation
    stack: tuple[str, ...] = ()
    body: str = field(default="", repr=False)
    first_seen: str | None = None  # filled from state, not from the board

    @property
    def body_hash(self) -> str:
        """Cache key for effort estimates.

        Keyed on title+body so an edited issue is re-estimated and an unchanged
        one never costs an API call twice.
        """
        h = hashlib.sha256()
        h.update(self.title.encode("utf-8"))
        h.update(b"\x00")
        h.update(self.body.encode("utf-8"))
        return h.hexdigest()[:16]

    def with_first_seen(self, when: str) -> "Bounty":
        return replace(self, first_seen=when)


# Matches the amount conventions actually in use on the boards:
#   comma/opendbc  "[$10k bounty] Ford F-150"   "[$300 Bounty] Toyota: ..."
#   Tenstorrent    "[Bounty $35000] Welford"    "[Bounty $5k] ttnn.log_sigmoid"
#                  "[Bounty $2,500] Perf - ..."
_AMOUNT_RE = re.compile(
    r"\[\s*(?:bounty\s*)?\$\s*([\d,]+(?:\.\d+)?)\s*(k?)\s*(?:bounty)?[^\]]*\]",
    re.IGNORECASE,
)


def parse_amount(text: str) -> int | None:
    """Pull a dollar amount out of an issue title.

    Returns None rather than 0 when no amount is present — the caller must be
    able to tell "no price published" from "priced at nothing".
    """
    m = _AMOUNT_RE.search(text or "")
    if not m:
        return None
    raw, k = m.group(1).replace(",", ""), m.group(2)
    try:
        value = float(raw)
    except ValueError:
        return None
    if k.lower() == "k":
        value *= 1000
    return int(value)
