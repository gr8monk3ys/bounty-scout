"""Regression tests. Every case here is a defect the first live run produced."""

import datetime as dt
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import pytest
import estimate
import score
from bounty import (HARDWARE_NONE, STATUS_LOCKED, STATUS_OPEN, Bounty,
                    parse_amount)

TODAY = dt.date(2026, 8, 30)


def mk(**kw):
    base = dict(source="comma", ref="r#1", title="t", url="", amount_usd=500,
                amount_is_stated=True, hardware=HARDWARE_NONE,
                status=STATUS_OPEN, competitors=0, updated_at="2026-08-29")
    base.update(kw)
    return Bounty(**base)


class TestAmountParsing:
    @pytest.mark.parametrize("title,expected", [
        ("[$10k bounty] Ford F-150 2026 (TRON) support", 10000),
        ("[Bounty $35000] Welford Two-Pass Statistics", 35000),
        ("[Bounty $5k] ttnn.log_sigmoid", 5000),
        ("[Bounty $2,500] Perf - top-k/sort/moe", 2500),
        ("[$300 Bounty] Toyota: cancel cruise without chime", 300),
        ("[$250] Show balance info for third-party cards", 250),
    ])
    def test_reads_every_convention_in_use(self, title, expected):
        assert parse_amount(title) == expected

    def test_absent_amount_is_none_not_zero(self):
        # A missing price must stay distinguishable from a zero one, or the
        # unpriced boards rank last instead of being reported separately.
        assert parse_amount("Migrate cabana from Qt to imgui") is None


class TestStaleLocks:
    def test_undated_lock_is_never_stale(self):
        # tinygrad's sheet publishes no per-row timestamp. Treating the missing
        # date as infinitely old marked every claimed row free for the taking.
        b = mk(source="tinygrad", status=STATUS_LOCKED, updated_at="")
        assert score.lock_is_stale(b, TODAY) is False

    def test_lock_past_the_boards_own_window_is_stale(self):
        b = mk(source="comma", status=STATUS_LOCKED, updated_at="2026-08-01")
        assert score.lock_is_stale(b, TODAY) is True

    def test_lock_inside_the_window_is_not(self):
        b = mk(source="comma", status=STATUS_LOCKED, updated_at="2026-08-28")
        assert score.lock_is_stale(b, TODAY) is False

    def test_expensify_assignments_never_lapse(self):
        # An assigned Expensify issue is a signed Upwork contract, not a soft
        # claim. Ageing it out would advertise someone's paid work as free.
        b = mk(source="expensify", status=STATUS_LOCKED, updated_at="2020-01-01")
        assert score.lock_is_stale(b, TODAY) is False


class TestMoneyScore:
    def test_unpriced_is_none_not_zero(self):
        assert score.money_score(mk(amount_usd=None), 8, TODAY) is None

    def test_competitors_divide_the_expectation(self):
        alone = score.money_score(mk(amount_usd=100, competitors=0), 10, TODAY)
        crowded = score.money_score(mk(amount_usd=100, competitors=11), 10, TODAY)
        assert alone == pytest.approx(10.0)
        assert crowded == pytest.approx(10.0 / 12)

    def test_live_lock_scores_zero(self):
        b = mk(status=STATUS_LOCKED, updated_at="2026-08-29")
        assert score.money_score(b, 8, TODAY) == 0.0


class TestBonusesDoNotCorruptUnits:
    def test_bonus_is_a_multiplier_the_caller_applies_to_rank_only(self):
        # The first run printed "$405/h" for a bounty paying $250/h because the
        # freshness bonus was multiplied into the money figure. bonuses() must
        # hand back a separate multiplier, never a modified rate.
        b = mk(amount_usd=10000, competitors=0, first_seen=TODAY.isoformat())
        rate = score.money_score(b, 40, TODAY)
        mult, why = score.bonuses(b, TODAY)
        assert rate == pytest.approx(250.0)
        assert mult > 1.0 and "new" in why


class TestEffortLadders:
    @pytest.mark.parametrize("source,amount,hours", [
        ("comma", 100, 3), ("comma", 300, 8), ("comma", 500, 20),
        ("comma", 10000, 40), ("tinygrad", 100, 0.5), ("tinygrad", 1000, 40),
    ])
    def test_published_ladders_are_authoritative(self, source, amount, hours):
        h, prov = estimate.effort_hours(
            mk(source=source, amount_usd=amount), cache={}, allow_model=False)
        assert (h, prov) == (hours, estimate.STATED)

    def test_boards_without_a_ladder_fall_back_and_say_so(self):
        h, prov = estimate.effort_hours(
            mk(source="tenstorrent", amount_usd=35000), cache={}, allow_model=False)
        assert prov == estimate.HEURISTIC and h > 0

    def test_effort_is_never_zero(self):
        # A zero would divide by zero in money_score.
        h, _ = estimate.effort_hours(mk(amount_usd=1), cache={}, allow_model=False)
        assert h > 0

    def test_cache_prevents_a_second_model_call(self):
        b = mk(source="tenstorrent", amount_usd=999, title="x", body="y")
        cache = {b.body_hash: 12.0}
        h, prov = estimate.effort_hours(b, cache=cache, allow_model=False)
        assert (h, prov) == (12.0, estimate.MODELED)


class TestRefIdentity:
    def test_near_identical_titles_get_distinct_refs(self):
        # Two live tinygrad rows differ only at "MOCKGPU_ARCH=rdna4" vs
        # "cdna4", well past any sensible prefix truncation.
        import hashlib
        a = "all tests passing in emulator in CI with MOCKGPU_ARCH=rdna4"
        b = "all tests passing in emulator in CI with MOCKGPU_ARCH=cdna4"
        ref = lambda d: "tinygrad#" + hashlib.sha256(d.encode()).hexdigest()[:10]
        assert ref(a) != ref(b)


class TestExternalWinRate:
    """Measured payout reality, not the board's description of itself."""

    def test_expensify_is_discounted_for_an_automated_contributor_seat(self):
        # 60 closed Help Wanted issues: 7 payment summaries, 5 to reviewers/C+,
        # 0 to an external contributor. MelvinBot proposes first and implements.
        rate = score.money_score(mk(source="expensify", amount_usd=250), 8, TODAY)
        assert rate == pytest.approx(250 / 8 * score.EXTERNAL_WIN_RATE["expensify"])

    def test_boards_that_demonstrably_pay_outsiders_are_undiscounted(self):
        rate = score.money_score(mk(source="tinygrad", amount_usd=500), 16, TODAY)
        assert rate == pytest.approx(500 / 16)
