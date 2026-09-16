"""Tenstorrent — bounties live in issue titles on tt-metal, e.g. "[Bounty $5k] ...".

There is no board and no published price ladder, so amounts come from titles and
effort is estimated. Most of this work needs Tenstorrent silicon to test against;
nothing in the issue states that, so the gate is set from the repo rather than
read, and marked as such.
"""

from bounty import HARDWARE_SILICON, STATUS_LOCKED, STATUS_OPEN, Bounty, parse_amount
from sources import gh_json, guard_count
from sources._github import crossref_pr_count

SOURCE = "tenstorrent"
MIN_EXPECTED = 3


def fetch(warn=lambda _m: None, **_) -> list[Bounty]:
    rows = gh_json([
        "gh", "search", "issues", "--owner", "tenstorrent", "--state", "open",
        "bounty in:title", "--limit", "100",
        "--json", "repository,number,title,url,updatedAt,assignees",
    ])
    out = []
    for r in rows:
        amount = parse_amount(r.get("title", ""))
        if amount is None:
            continue
        repo = (r.get("repository") or {}).get("nameWithOwner", "")
        number = r.get("number")
        assignees = r.get("assignees") or []
        out.append(Bounty(
            source=SOURCE,
            ref=f"{repo}#{number}",
            title=r.get("title", ""),
            url=r.get("url", ""),
            amount_usd=amount,
            amount_is_stated=True,
            hardware=HARDWARE_SILICON,
            status=STATUS_LOCKED if assignees else STATUS_OPEN,
            competitors=len(assignees) + crossref_pr_count(repo, number),
            updated_at=r.get("updatedAt", ""),
            stack=("python", "c++"),
            body=r.get("title", ""),
        ))
    return guard_count(SOURCE, out, MIN_EXPECTED, "title convention may have changed")
