"""Expensify — the volume board, and the only one that is proposal-first.

`Help Wanted` is the actionable label: it means the Upwork job is posted and the
issue is accepting proposals. The wider `External` label (~390 issues) includes
work not yet open for hire, so ranking against it would overstate what is
available.

Most prices ARE on the issue, in the title, as "[$250] Show balance info...":
32 of the 46 currently open Help Wanted issues carry one. The remainder keep the
figure only in the Upwork job, and those stay None so the scorer can supply a
band-bottom assumption rather than a zero.

Competition is the number of PROPOSALS, counted from comment bodies. The race
here happens before any code is written: contributors post a proposal under a
"## Proposal" heading and one is selected.

Raw comment count was the first attempt and it is not merely inflated, it is
mis-ordered: #94981 has 29 comments and 3 proposals, #96975 has 33 and 8. Half
of every thread is melvin-bot. Ranking on it put the board at ~$1/hour and
sorted it wrongly among itself.

Counting costs one extra API call per issue, made cheap by keying the cache on
the issue's comment count — if no comment has been added, no proposal has been
either.

Assignees are NOT an availability signal here and must not be read as one. All
46 open Help Wanted issues are assigned, as are the first 100 External ones —
melvin-bot attaches an internal engineer and a Contributor+ reviewer to every
issue as a matter of course. Treating that as "taken" emptied the board and made
the highest-volume source in the tool report nothing. The `Help Wanted` label is
itself the availability signal: it means the Upwork job is posted and proposals
are open.
"""

import json
import pathlib
import re

from bounty import HARDWARE_NONE, STATUS_OPEN, Bounty, parse_amount
from sources import SourceError, gh_json, guard_count

# Contributors post under a "## Proposal" heading; the level varies.
_PROPOSAL_RE = re.compile(r"^#{1,4}\s*proposal\b", re.IGNORECASE | re.MULTILINE)

_CACHE = pathlib.Path(__file__).resolve().parents[2] / "state" / "proposals.json"


def _load_cache() -> dict:
    if _CACHE.exists():
        try:
            return json.loads(_CACHE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True))


def _proposal_count(number: int, comment_count: int, cache: dict) -> int:
    """Real proposals on an issue. Cached against the comment count.

    A thread whose comment total has not moved cannot have gained a proposal,
    so the cache key doubles as the invalidation check and a re-run of an
    unchanged board makes no extra calls at all.
    """
    key = f"{number}:{comment_count}"
    if key in cache:
        return cache[key]
    try:
        bodies = gh_json([
            "gh", "api", f"repos/Expensify/App/issues/{number}/comments?per_page=100",
            "-H", "Accept: application/vnd.github+json",
        ], retries=2)
    except SourceError:
        return 0
    n = sum(
        1 for c in bodies
        if not (c.get("user") or {}).get("login", "").lower().count("bot")
        and _PROPOSAL_RE.search(c.get("body") or "")
    )
    cache[key] = n
    return n

SOURCE = "expensify"
MIN_EXPECTED = 5
LABEL = "Help Wanted"


def fetch(warn=lambda _m: None, limit: int = 100, **_) -> list[Bounty]:
    rows = gh_json([
        "gh", "api",
        f"repos/Expensify/App/issues?labels={LABEL.replace(' ', '%20')}"
        f"&state=open&per_page={min(limit, 100)}",
        "-H", "Accept: application/vnd.github+json",
    ])
    cache = _load_cache()
    out = []
    for r in rows:
        if "pull_request" in r:
            continue
        amount = parse_amount(r.get("title", ""))
        proposals = _proposal_count(
            r.get("number"), int(r.get("comments") or 0), cache)
        out.append(Bounty(
            source=SOURCE,
            ref=f"Expensify/App#{r.get('number')}",
            title=r.get("title", ""),
            url=r.get("html_url", ""),
            amount_usd=amount,
            amount_is_stated=amount is not None,
            hardware=HARDWARE_NONE,
            status=STATUS_OPEN,
            competitors=proposals,
            updated_at=r.get("updated_at", ""),
            stack=("typescript", "react"),
            body=(r.get("body") or "")[:6000],
        ))
    _save_cache(cache)
    return guard_count(SOURCE, out, MIN_EXPECTED, f"label {LABEL!r} may have been renamed")
