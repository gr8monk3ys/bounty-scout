"""Expensify — the volume board, and the only one that is proposal-first.

`Help Wanted` is the actionable label: it means the Upwork job is posted and the
issue is accepting proposals. The wider `External` label (~390 issues) includes
work not yet open for hire, so ranking against it would overstate what is
available.

Most prices ARE on the issue, in the title, as "[$250] Show balance info...":
32 of the 46 currently open Help Wanted issues carry one. The remainder keep the
figure only in the Upwork job, and those stay None so the scorer can supply a
band-bottom assumption rather than a zero.

Competition is proxied by comment count, not by pull requests, because the race
here happens before any code is written: contributors post proposals and one is
selected. The proxy is noisy (bot comments inflate it) but monotone, which is
all the ranking needs.

Assignees are NOT an availability signal here and must not be read as one. All
46 open Help Wanted issues are assigned, as are the first 100 External ones —
melvin-bot attaches an internal engineer and a Contributor+ reviewer to every
issue as a matter of course. Treating that as "taken" emptied the board and made
the highest-volume source in the tool report nothing. The `Help Wanted` label is
itself the availability signal: it means the Upwork job is posted and proposals
are open.
"""

from bounty import HARDWARE_NONE, STATUS_OPEN, Bounty, parse_amount
from sources import gh_json, guard_count

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
    out = []
    for r in rows:
        if "pull_request" in r:
            continue
        amount = parse_amount(r.get("title", ""))
        out.append(Bounty(
            source=SOURCE,
            ref=f"Expensify/App#{r.get('number')}",
            title=r.get("title", ""),
            url=r.get("html_url", ""),
            amount_usd=amount,
            amount_is_stated=amount is not None,
            hardware=HARDWARE_NONE,
            status=STATUS_OPEN,
            competitors=int(r.get("comments") or 0),
            updated_at=r.get("updated_at", ""),
            stack=("typescript", "react"),
            body=(r.get("body") or "")[:6000],
        ))
    return guard_count(SOURCE, out, MIN_EXPECTED, f"label {LABEL!r} may have been renamed")
