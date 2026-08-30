"""Adapter guards. These protect the failure mode, not the happy path."""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import pytest
from sources import SourceError, guard_count


class TestEmptyBoardGuard:
    def test_a_short_result_raises_rather_than_reporting_a_drained_board(self):
        # The INCLUDE_BOTS failure: a sweep reported 19 open PRs when there were
        # 137, and nothing in the run looked wrong. A board that has genuinely
        # emptied is rare; an adapter broken by a board changing shape is not.
        with pytest.raises(SourceError, match="not an empty board"):
            guard_count("comma", [], minimum=5)

    def test_a_full_result_passes_through_untouched(self):
        items = list(range(10))
        assert guard_count("comma", items, minimum=5) is items


class TestExpensifyProposalCounting:
    """Comment count was not merely inflated — it mis-ordered the board."""

    def test_proposal_heading_matches_every_level_in_use(self):
        from sources.expensify import _PROPOSAL_RE
        for body in ("## Proposal\nsome text", "# Proposal", "#### proposal",
                     "## Proposal\n### Please re-state"):
            assert _PROPOSAL_RE.search(body), body

    def test_ordinary_comments_are_not_proposals(self):
        from sources.expensify import _PROPOSAL_RE
        for body in ("I can reproduce this", "Triggered auto assignment",
                     "see the proposal above", "📣 @someone You have been hired!"):
            assert not _PROPOSAL_RE.search(body), body

    def test_cache_key_invalidates_on_a_new_comment(self):
        # The comment total is the invalidation check: a thread that has not
        # gained a comment cannot have gained a proposal, so a re-run of an
        # unchanged board makes no extra API calls.
        from sources.expensify import _proposal_count
        cache = {"123:29": 3}
        assert _proposal_count(123, 29, cache) == 3      # served from cache
        assert "123:30" not in cache                      # a 30th comment misses
