"""The price points on the table, each with the loss ratio it lands on.

An underwriter chooses between premiums, not percentages - the technical
one, the one the broker will carry, the one that keeps the account. The
question that separates them is the same for all of them and was never
on the page: at this premium, where does the loss ratio land?
"""
import pytest

from app.scoring.rules.experience_pricing import HOUSE_TARGET_LOSS_RATIO
from app.scoring.rules.renewal_options import (
    DECISION_ACCEPT,
    DECISION_REJECT,
    DECISION_REVIEW,
    REVIEW_BAND_PCT,
    minimum_acceptable_premium,
    premium_build_up,
    renewal_options,
)

# Serviceplan's own shape: 168.2% earned, 26.5% loading, 7.5 points.
EXPIRING = 1_882_801.0
INCURRED = 3_167_446.0
LOADING = 0.265
PTS = 0.075
TRENDED = INCURRED + EXPIRING * PTS
TECHNICAL = (INCURRED + EXPIRING * PTS) / (1 - LOADING)


def by_key(result, key):
    return next(o for o in result["options"] if o["key"] == key)


class TestTheProjection:
    def test_the_technical_price_always_lands_on_one_minus_the_loading(self):
        # Not a coincidence - it is what the ladder means. The technical
        # premium is the one at which trended claims consume precisely
        # the part of the premium not spoken for by expenses.
        result = renewal_options(EXPIRING, TECHNICAL, TRENDED, loading_pct=LOADING)
        assert by_key(result, "technical")["projected_loss_ratio"] == pytest.approx(
            1 - LOADING, abs=0.0001)
        assert result["technical_projected_loss_ratio"] == pytest.approx(1 - LOADING, abs=0.0001)

    def test_every_option_is_measured_against_the_same_denominator(self):
        result = renewal_options(EXPIRING, TECHNICAL, TRENDED, loading_pct=LOADING, quoted=[
            {"label": "Commercial", "premium": 4_200_000.0},
            {"label": "Retention", "premium": 3_800_000.0},
        ])
        for option in result["options"]:
            if option["premium"]:
                assert option["projected_loss_ratio"] == pytest.approx(
                    TRENDED / option["premium"], abs=0.0001)

    def test_it_projects_on_trended_claims_not_last_years(self):
        # The premium covers next year. Using the raw incurred figure
        # flatters every option by the whole of inflation.
        result = renewal_options(EXPIRING, TECHNICAL, TRENDED, loading_pct=LOADING)
        assert result["trended_claims"] == pytest.approx(TRENDED, abs=0.01)
        assert TRENDED > INCURRED

    def test_the_current_premium_is_on_the_table_too(self):
        # "Do nothing" is an option, and it should be priced like one.
        result = renewal_options(EXPIRING, TECHNICAL, TRENDED, loading_pct=LOADING)
        current = by_key(result, "expiring")
        assert current["premium"] == EXPIRING
        assert current["change_pct"] == 0
        assert current["projected_loss_ratio"] > 1.6
        assert current["decision"] == DECISION_REJECT


class TestTheMinimumAcceptable:
    def test_it_is_derived_from_the_claims_not_typed(self):
        # A remembered figure goes stale the moment the claims file is
        # refreshed and nobody notices.
        assert minimum_acceptable_premium(TRENDED) == pytest.approx(
            TRENDED / HOUSE_TARGET_LOSS_RATIO, abs=0.01)

    def test_it_lands_exactly_on_the_house_maximum(self):
        result = renewal_options(EXPIRING, TECHNICAL, TRENDED, loading_pct=LOADING)
        minimum = by_key(result, "minimum_acceptable")
        assert minimum["projected_loss_ratio"] == pytest.approx(
            HOUSE_TARGET_LOSS_RATIO, abs=0.0001)

    def test_a_different_target_moves_it(self):
        result = renewal_options(EXPIRING, TECHNICAL, TRENDED, loading_pct=LOADING,
                                 target_loss_ratio=0.85)
        assert by_key(result, "minimum_acceptable")["projected_loss_ratio"] == pytest.approx(
            0.85, abs=0.0001)

    def test_no_claims_means_no_minimum_rather_than_a_division(self):
        assert minimum_acceptable_premium(None) is None
        assert minimum_acceptable_premium(0.0) is None


class TestTheDecision:
    def _decisions(self, *premiums):
        result = renewal_options(
            EXPIRING, TECHNICAL, TRENDED, loading_pct=LOADING,
            quoted=[{"label": f"Option {i}", "premium": p, "key": f"o{i}"}
                    for i, p in enumerate(premiums)])
        return [by_key(result, f"o{i}")["decision"] for i in range(len(premiums))]

    def test_at_or_above_the_minimum_is_writable(self):
        minimum = minimum_acceptable_premium(TRENDED)
        assert self._decisions(TECHNICAL, minimum, minimum + 1) == [DECISION_ACCEPT] * 3

    def test_just_below_is_a_conversation_not_a_refusal(self):
        minimum = minimum_acceptable_premium(TRENDED)
        assert self._decisions(minimum * (1 - REVIEW_BAND_PCT / 2)) == [DECISION_REVIEW]

    def test_well_below_is_refused(self):
        minimum = minimum_acceptable_premium(TRENDED)
        assert self._decisions(minimum * (1 - REVIEW_BAND_PCT) - 1) == [DECISION_REJECT]

    def test_the_band_edge_is_still_a_review(self):
        minimum = minimum_acceptable_premium(TRENDED)
        assert self._decisions(minimum * (1 - REVIEW_BAND_PCT)) == [DECISION_REVIEW]

    def test_an_option_with_no_premium_yet_gets_no_verdict(self):
        result = renewal_options(EXPIRING, TECHNICAL, TRENDED, loading_pct=LOADING,
                                 quoted=[{"label": "Broker target", "premium": None,
                                          "key": "broker"}])
        broker = by_key(result, "broker")
        assert broker["premium"] is None
        assert broker["decision"] is None
        assert broker["projected_loss_ratio"] is None


class TestTheWaterfallAddsUp:
    def test_the_steps_reach_the_final_bar(self):
        # A waterfall whose steps do not reach its own total is worse
        # than no waterfall: it invites the reader to check, and the
        # check fails. Serviceplan's drawn as 1.88 + 1.29 + 0.31 + 0.72
        # = 4.20 under a final bar reading 4.50.
        rows = premium_build_up(EXPIRING, INCURRED, PTS, LOADING)
        steps = [r for r in rows if r["amount"] is not None]
        assert sum(r["amount"] for r in steps) == pytest.approx(rows[-1]["running"], abs=1.0)

    def test_each_running_total_is_the_one_before_plus_the_step(self):
        rows = premium_build_up(EXPIRING, INCURRED, PTS, LOADING)
        running = 0.0
        for row in rows:
            if row["amount"] is None:
                continue
            running = row["amount"] if row["label"] == "Expiring premium" else running + row["amount"]
            assert row["running"] == pytest.approx(running, abs=0.01)

    def test_inflation_is_points_of_the_expiring_premium(self):
        # Not a percentage of the claims - that is the other formula, and
        # on a loss-making account it is much larger.
        rows = premium_build_up(EXPIRING, INCURRED, PTS, LOADING)
        inflation = next(r for r in rows if "inflation" in r["label"].lower())
        assert inflation["amount"] == pytest.approx(EXPIRING * PTS, abs=0.01)
        assert inflation["amount"] != pytest.approx(INCURRED * PTS, abs=1.0)

    def test_it_ends_on_the_ladder_premium(self):
        rows = premium_build_up(EXPIRING, INCURRED, PTS, LOADING)
        assert rows[-1]["running"] == pytest.approx(TECHNICAL, abs=1.0)

    def test_the_required_premium_passed_in_wins_over_the_reconstruction(self):
        # Where the caller has Method 1's own figure - floored, rounded,
        # or overridden - the waterfall must end on THAT, not on a
        # rebuild of it a few dirhams away.
        rows = premium_build_up(EXPIRING, INCURRED, PTS, LOADING, required_premium=4_501_303.0)
        assert rows[-1]["running"] == 4_501_303.0

    def test_no_claims_is_no_waterfall(self):
        assert premium_build_up(EXPIRING, None, PTS, LOADING) == []
