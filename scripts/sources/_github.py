"""Shared GitHub helpers used by more than one adapter."""

from sources import SourceError, gh_json


def crossref_pr_count(repo: str, number: int) -> int:
    """How many pull requests already reference this issue.

    This is the universal competition signal: it works on any board hosted on
    GitHub, needs only `repo` scope, and counts the thing that actually matters
    — people who have written code, not people who commented "I'll take this".
    """
    try:
        events = gh_json([
            "gh", "api", f"repos/{repo}/issues/{number}/timeline",
            "--paginate", "-H", "Accept: application/vnd.github+json",
        ], retries=2)
    except SourceError:
        return 0
    refs = set()
    for e in events:
        if e.get("event") != "cross-referenced":
            continue
        src = (e.get("source") or {}).get("issue") or {}
        if "pull_request" in src:
            refs.add(src.get("number"))
    return len(refs)
