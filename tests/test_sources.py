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
