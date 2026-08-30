"""tinygrad — a public Google Sheet, read via its CSV export.

Known limitation, and it is structural: the sheet encodes lock state as cell
COLOUR (uncolored = up for grabs, yellow = locked, blue = pending review,
green = complete), and CSV export drops formatting entirely. The `GitHub Owner`
column is the available proxy — a row with an owner has been claimed — so a
yellow row with an empty owner cell reads here as open when it is not. The
report says so rather than pretending the status is authoritative.
"""

import csv
import hashlib
import io
import urllib.request

from bounty import (HARDWARE_GPU, HARDWARE_NONE, STATUS_LOCKED, STATUS_OPEN,
                    Bounty)
from sources import SourceError, guard_count

SHEET = "1WKHbT-7KOgjEawq5h5Ic1qUWzpfAzuD_J06N1JwOCGs"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET}/export?format=csv&gid=0"
MIN_EXPECTED = 5

SOURCE = "tinygrad"


def _hardware(raw: str) -> str:
    t = (raw or "").strip().lower()
    if not t or t.startswith("none"):
        return HARDWARE_NONE
    return HARDWARE_GPU


def _amount(raw: str) -> int | None:
    t = (raw or "").replace("$", "").replace(",", "").strip()
    if not t:
        return None
    try:
        return int(float(t))
    except ValueError:
        return None


def fetch(**_) -> list[Bounty]:
    try:
        with urllib.request.urlopen(CSV_URL, timeout=60) as resp:
            text = resp.read().decode("utf-8")
    except OSError as exc:
        raise SourceError(f"tinygrad: sheet unreachable: {exc}") from exc

    out = []
    for row in csv.DictReader(io.StringIO(text)):
        desc = (row.get("Short Description") or "").strip()
        amount = _amount(row.get("Value", ""))
        # Rows past the bounty table are the sheet's prose: rules, the price
        # ladder, the hiring path. They have a description but no price.
        if not desc or amount is None:
            continue
        owner = (row.get("GitHub Owner") or "").strip()
        link = (row.get("Link") or "").strip()
        out.append(Bounty(
            source=SOURCE,
            # Hash the full description: the sheet has no ids, and truncating
            # the text collides — two live rows differ only at "MOCKGPU_ARCH=
            # rdna4" vs "cdna4", well past any sensible prefix length.
            ref="tinygrad#" + hashlib.sha256(desc.encode()).hexdigest()[:10],
            title=desc,
            url=link or "https://bounties.tinygrad.org/",
            amount_usd=amount,
            amount_is_stated=True,
            hardware=_hardware(row.get("Hardware Required", "")),
            status=STATUS_LOCKED if owner else STATUS_OPEN,
            competitors=1 if owner else 0,
            # The sheet carries no per-row timestamp, so a claimed row cannot be
            # aged and its lock can never be shown as lapsed. Better to leave
            # the field empty than to invent a date the sheet never stated.
            updated_at="",
            stack=("python",),
            body=desc,
        ))
    return guard_count(SOURCE, out, MIN_EXPECTED, "sheet layout may have changed")
