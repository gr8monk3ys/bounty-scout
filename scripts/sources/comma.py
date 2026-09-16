"""comma.ai — GitHub Projects board, with a documented degraded mode.

The board is the only complete source. `Value ($)`, `Required Hardware` and the
`Locked` status are project FIELDS, not issue text: only 6 of 27 bounties repeat
their price in the title, and none state their hardware gate anywhere else. So a
token without `read:project` cannot see most of this board, and — this is the
part worth guarding — what it can see looks like a perfectly healthy result.

Rather than silently report a third of the board, the degraded path marks every
amount as inferred and every hardware gate as unknown, and asks the caller to
warn. Fix with:  gh auth refresh -s read:project
"""

import json

from bounty import (HARDWARE_CAR, HARDWARE_DEVICE, HARDWARE_NONE,
                    HARDWARE_UNKNOWN, STATUS_LOCKED, STATUS_OPEN, Bounty,
                    parse_amount)
from sources import SourceError, gh_json, guard_count, run_gh
from sources._github import crossref_pr_count

SOURCE = "comma"
PROJECT_NUMBER = 26
MIN_EXPECTED = 5

_HARDWARE_FIELD = {
    "no hardware required": HARDWARE_NONE,
    "comma 3x/4": HARDWARE_DEVICE,
    "comma 3x/4 + car": HARDWARE_CAR,
}

_QUERY = """
query($org:String!, $num:Int!, $after:String) {
  organization(login:$org) {
    projectV2(number:$num) {
      items(first:100, after:$after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          fieldValues(first:20) {
            nodes {
              ... on ProjectV2ItemFieldNumberValue { number field { ... on ProjectV2FieldCommon { name } } }
              ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2FieldCommon { name } } }
            }
          }
          content {
            ... on Issue {
              number title url updatedAt state
              repository { nameWithOwner }
              timelineItems(first:100, itemTypes:[CROSS_REFERENCED_EVENT]) { totalCount }
            }
          }
        }
      }
    }
  }
}
"""


def _fields(node) -> dict:
    out = {}
    for fv in (node.get("fieldValues") or {}).get("nodes") or []:
        name = ((fv.get("field") or {}).get("name") or "").strip()
        if not name:
            continue
        out[name.lower()] = fv.get("number", fv.get("name"))
    return out


def _from_board(warn) -> list[Bounty] | None:
    """Full board via the projects API. None when scope is missing."""
    nodes, cursor = [], None
    while True:
        argv = ["gh", "api", "graphql", "-f", f"query={_QUERY}",
                "-F", "org=commaai", "-F", f"num={PROJECT_NUMBER}"]
        argv += ["-F", f"after={cursor}"] if cursor else ["-F", "after="]
        r = run_gh(argv, retries=2)
        if r.returncode != 0:
            if "INSUFFICIENT_SCOPES" in (r.stderr or "") or "read:project" in (r.stderr or ""):
                return None
            raise SourceError(f"comma: board query failed: {r.stderr.strip()[:200]}")
        page = json.loads(r.stdout)["data"]["organization"]["projectV2"]["items"]
        nodes.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    out = []
    for n in nodes:
        content = n.get("content") or {}
        f = _fields(n)
        status = str(f.get("status") or "").strip().lower()
        if status == "done":
            continue
        amount = f.get("value ($)")
        hardware = _HARDWARE_FIELD.get(
            str(f.get("required hardware") or "").strip().lower(), HARDWARE_UNKNOWN)
        # Draft board items carry a title but no issue; they are real bounties
        # (four of comma's car ports are drafts) so they are kept, not dropped.
        repo = (content.get("repository") or {}).get("nameWithOwner", "")
        number = content.get("number")
        title = content.get("title") or f.get("title") or "(draft item)"
        out.append(Bounty(
            source=SOURCE,
            ref=f"{repo}#{number}" if repo and number else f"comma-draft:{title[:40]}",
            title=title,
            url=content.get("url") or "https://comma.ai/bounties",
            amount_usd=int(amount) if amount is not None else None,
            amount_is_stated=amount is not None,
            hardware=hardware,
            status=STATUS_LOCKED if status == "locked" else STATUS_OPEN,
            competitors=(content.get("timelineItems") or {}).get("totalCount", 0),
            updated_at=content.get("updatedAt", ""),
            stack=("python",),
            body=title,
        ))
    return out


def _from_issues(warn) -> list[Bounty]:
    """Degraded path: label search. Sees a fraction of the board."""
    warn("comma: no `read:project` scope — reading labelled issues instead of "
         "the board. This sees roughly 10 of 27 bounties, cannot read any "
         "hardware gate, and only reads a price where the title repeats one. "
         "Fix: gh auth refresh -s read:project")
    rows = gh_json([
        "gh", "search", "issues", "--owner", "commaai", "--label", "bounty",
        "--state", "open", "--limit", "100",
        "--json", "repository,number,title,url,updatedAt",
    ])
    out = []
    for r in rows:
        repo = (r.get("repository") or {}).get("nameWithOwner", "")
        number = r.get("number")
        amount = parse_amount(r.get("title", ""))
        out.append(Bounty(
            source=SOURCE,
            ref=f"{repo}#{number}",
            title=r.get("title", ""),
            url=r.get("url", ""),
            amount_usd=amount,
            amount_is_stated=amount is not None,
            hardware=HARDWARE_UNKNOWN,
            status=STATUS_OPEN,
            competitors=crossref_pr_count(repo, number) if repo and number else 0,
            updated_at=r.get("updatedAt", ""),
            stack=("python",),
            body=r.get("title", ""),
        ))
    return out


def fetch(warn=lambda _m: None, **_) -> list[Bounty]:
    board = _from_board(warn)
    items = board if board is not None else _from_issues(warn)
    return guard_count(SOURCE, items, MIN_EXPECTED, "board or label search changed shape")
