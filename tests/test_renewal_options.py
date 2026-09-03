"""The price points on the table, each with the loss ratio it lands on.

An underwriter chooses between premiums, not percentages - the technical
one, the one the broker will carry, the one that keeps the account. The
question that separates them is the same for all of them and was never
on the page: at this premium, where does the loss ratio land?
"""
import pytest

from app.scoring.rules.renewal_options import (
    DECISION_ACCEPT,
    DECISION_REJECT,
    DECISION_REVIEW,
    HOUSE_MAXIMUM_COMBINED_RATIO,
    REVIEW_BAND_PCT,
    acceptance_loss_ratio,
    minimum_acceptable_premium,
    premium_build_up,
    renewal_options,
)

# Serviceplan's own shape: 168.2% earned, 26.5% loading, 7.5 points.
EXPIRING = 1_882_801.0
LOSS_RATIO = 1.682
INCURRED = LOSS_RATIO * EXPIRING
LOADING = 0.265
PTS = 0.075
TRENDED = INCURRED + EXPIRING * PTS
TECHNICAL = (INCURRED + EXPIRING * PTS) / (1 - LOADING)
# The acceptance line as it comes out on THIS account's expense load:
# 105% combined less a 26.5% loading is a pure loss ratio of 78.5%.
ACCEPTANCE_LR = HOUSE_MAXIMUM_COMBINED_RATIO - LOADING


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

    def test_the_current_premium_is_projected_but_not_judged(self):
        # It is the reference the other rows are measured against, not an
        # option anybody is choosing. On an account whose house-maximum
        # verdict comes out "accept" while the ladder asks +24.7%, a pill
        # in that row reads as advice to do nothing - the one thing the
        # table is not saying. The projection stays, because "renewed
        # flat, this account lands at 175.7%" is the point.
        result = renewal_options(EXPIRING, TECHNICAL, TRENDED, loading_pct=LOADING)
        current = by_key(result, "expiring")
        assert current["premium"] == EXPIRING
        assert current["change_pct"] == 0
        assert current["projected_loss_ratio"] > 1.6
        assert current["decision"] is None


class TestTheMinimumAcceptable:
    def test_it_is_derived_from_the_claims_not_typed(self):
        # A remembered figure goes stale the moment the claims file is
        # refreshed and nobody notices.
        assert minimum_acceptable_premium(TRENDED, ACCEPTANCE_LR) == pytest.approx(
            TRENDED / ACCEPTANCE_LR, abs=0.01)

    def test_it_lands_on_the_house_combined_ratio(self):
        result = renewal_options(EXPIRING, TECHNICAL, TRENDED, loading_pct=LOADING)
        minimum = by_key(result, "minimum_acceptable")
        # The projection is a PURE loss ratio, and at the minimum
        # acceptable premium it plus the loading is the combined line.
        assert minimum["projected_loss_ratio"] == pytest.approx(ACCEPTANCE_LR, abs=0.0001)
        assert minimum["projected_loss_ratio"] + LOADING == pytest.approx(
            HOUSE_MAXIMUM_COMBINED_RATIO, abs=0.0001)

    def test_a_tighter_combined_ratio_moves_it_up(self):
        result = renewal_options(EXPIRING, TECHNICAL, TRENDED, loading_pct=LOADING,
                                 combined_ratio=1.00)
        row = by_key(result, "minimum_acceptable")
        assert row["projected_loss_ratio"] == pytest.approx(1.00 - LOADING, abs=0.0001)
        # At break-even there is no room at all: the lowest writable
        # premium IS the technical premium.
        assert row["premium"] == pytest.approx(TECHNICAL, abs=1.0)

    def test_the_same_combined_line_is_a_different_loss_ratio_on_each_account(self):
        # Which is the whole reason the line is held as a combined ratio.
        # Two accounts at 78.5% claims are not equally acceptable if one
        # pays 26.5% of premium away in expenses and the other 33%.
        assert acceptance_loss_ratio(0.265) == pytest.approx(0.785, abs=0.0001)
        assert acceptance_loss_ratio(0.33) == pytest.approx(0.72, abs=0.0001)
        assert acceptance_loss_ratio(None) is None

    def test_a_looser_line_than_the_house_is_refused_not_clamped(self):
        # Tighter is an underwriting judgement about one account. Looser
        # is a change to the house's own position, and that is an
        # escalation rather than a text box - clamping it silently would
        # show a number nobody asked for and say nothing about it.
        with pytest.raises(ValueError) as refused:
            renewal_options(EXPIRING, TECHNICAL, TRENDED, loading_pct=LOADING,
                            combined_ratio=HOUSE_MAXIMUM_COMBINED_RATIO + 0.01)
        assert "authority sign-off" in str(refused.value)

    def test_no_loading_means_no_acceptance_line_rather_than_a_guess(self):
        # A renewal with no fee split entered is not priced at all, so
        # there is nothing to be permissive about.
        result = renewal_options(EXPIRING, TECHNICAL, TRENDED, loading_pct=None)
        assert result["target_loss_ratio"] is None
        assert result["minimum_acceptable_premium"] is None
        assert by_key(result, "technical")["decision"] is None

    def test_no_claims_means_no_minimum_rather_than_a_division(self):
        assert minimum_acceptable_premium(None, ACCEPTANCE_LR) is None
        assert minimum_acceptable_premium(0.0, ACCEPTANCE_LR) is None

    def test_the_house_minimum_increase_floors_it_on_a_good_account(self):
        # NOMADA's shape: an account performing well enough that its own
        # trended claims allow 91,003 against an expiring 103,487. A row
        # reading "minimum acceptable 91,003, accept" invites a 12%
        # REDUCTION on an account whose renewal ask is +24.7%, and the
        # house does not write that - so the table must not print it as
        # though it would.
        expiring, trended, loading = 103_487.0, 60_000.0, 0.33
        acceptance = acceptance_loss_ratio(loading)
        by_claims = minimum_acceptable_premium(trended, acceptance)
        assert by_claims < expiring

        floored = minimum_acceptable_premium(trended, acceptance,
                                             expiring_annual_premium=expiring)
        assert floored == pytest.approx(expiring * 1.09, abs=0.01)

        result = renewal_options(expiring, 129_034.39, trended, loading_pct=loading)
        assert result["minimum_is_house_floor"] is True
        assert result["minimum_by_loss_ratio"] == by_claims
        row = by_key(result, "minimum_acceptable")
        assert row["premium"] == floored
        assert "house minimum increase" in row["note"]

    def test_the_reference_rows_carry_no_verdict(self):
        # The expiring premium is what the others are measured against
        # and the minimum acceptable IS the line, so a verdict on it is
        # circular.
        result = renewal_options(EXPIRING, TECHNICAL, TRENDED, loading_pct=LOADING)
        assert by_key(result, "expiring")["decision"] is None
        assert by_key(result, "minimum_acceptable")["decision"] is None
        assert by_key(result, "technical")["decision"] == DECISION_ACCEPT


class TestTheDecision:
    def _decisions(self, *premiums):
        result = renewal_options(
            EXPIRING, TECHNICAL, TRENDED, loading_pct=LOADING,
            quoted=[{"label": f"Option {i}", "premium": p, "key": f"o{i}"}
                    for i, p in enumerate(premiums)])
        return [by_key(result, f"o{i}")["decision"] for i in range(len(premiums))]

    def test_at_or_above_the_minimum_is_writable(self):
        minimum = minimum_acceptable_premium(TRENDED, ACCEPTANCE_LR)
        assert self._decisions(TECHNICAL, minimum, minimum + 1) == [DECISION_ACCEPT] * 3

    def test_just_below_is_a_conversation_not_a_refusal(self):
        minimum = minimum_acceptable_premium(TRENDED, ACCEPTANCE_LR)
        assert self._decisions(minimum * (1 - REVIEW_BAND_PCT / 2)) == [DECISION_REVIEW]

    def test_well_below_is_refused(self):
        minimum = minimum_acceptable_premium(TRENDED, ACCEPTANCE_LR)
        assert self._decisions(minimum * (1 - REVIEW_BAND_PCT) - 1) == [DECISION_REJECT]

    def test_the_band_edge_is_still_a_review(self):
        minimum = minimum_acceptable_premium(TRENDED, ACCEPTANCE_LR)
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
        rows = premium_build_up(EXPIRING, LOSS_RATIO, PTS, LOADING)
        steps = [r for r in rows if r["amount"] is not None]
        assert sum(r["amount"] for r in steps) == pytest.approx(rows[-1]["running"], abs=1.0)

    def test_each_running_total_is_the_one_before_plus_the_step(self):
        rows = premium_build_up(EXPIRING, LOSS_RATIO, PTS, LOADING)
        running = 0.0
        for row in rows:
            if row["amount"] is None:
                continue
            running = row["amount"] if row["label"] == "Expiring premium" else running + row["amount"]
            assert row["running"] == pytest.approx(running, abs=0.01)

    def test_inflation_is_points_of_the_expiring_premium(self):
        # Not a percentage of the claims - that is the other formula, and
        # on a loss-making account it is much larger.
        rows = premium_build_up(EXPIRING, LOSS_RATIO, PTS, LOADING)
        inflation = next(r for r in rows if "inflation" in r["label"].lower())
        assert inflation["amount"] == pytest.approx(EXPIRING * PTS, abs=0.01)
        assert inflation["amount"] != pytest.approx(INCURRED * PTS, abs=1.0)

    def test_it_ends_on_the_ladder_premium(self):
        rows = premium_build_up(EXPIRING, LOSS_RATIO, PTS, LOADING)
        assert rows[-1]["running"] == pytest.approx(TECHNICAL, abs=1.0)

    def test_the_required_premium_passed_in_wins_over_the_reconstruction(self):
        # Where the caller has Method 1's own figure - floored, rounded,
        # or overridden - the waterfall must end on THAT, not on a
        # rebuild of it a few dirhams away.
        rows = premium_build_up(EXPIRING, LOSS_RATIO, PTS, LOADING, required_premium=4_501_303.0)
        assert rows[-1]["running"] == 4_501_303.0

    def test_no_claims_is_no_waterfall(self):
        assert premium_build_up(EXPIRING, None, PTS, LOADING) == []

    def test_it_is_built_on_the_ratio_not_on_annualised_claims(self):
        # The two are different numbers on any account with mid-term
        # movement: claims are earned against the PRO-RATA premium, so
        # putting the annualised claims figure over the larger expiring
        # premium silently improves the ratio - the basis mix
        # renewal_rating.py exists to prevent.
        #
        # NOMADA's own shape: 78,961 earned against 103,487 expiring.
        # Feeding the annualised claims in reached 114,128 where the
        # ladder asks 129,034, and the 14,907 then hid inside the
        # reconciling step wearing the label "house minimum".
        expiring, earned, ratio, pts, loading = 103_487.0, 78_960.84, 0.7604, 0.075, 0.33
        annualised = ratio * earned
        rows = premium_build_up(expiring, ratio, pts, loading)
        ladder = expiring * (ratio + pts) / (1 - loading)

        assert rows[-1]["running"] == pytest.approx(ladder, abs=1.0)
        # And nothing had to be reconciled away to get there.
        assert not [r for r in rows if r["label"] in ("House minimum", "Underwriter adjustment")]
        # The claims step is the ratio's own money, not the annualised
        # figure, and on this account they are 9,987 apart.
        claims = next(r for r in rows if r["label"] == "Claims experience")
        assert claims["running"] == pytest.approx(ratio * expiring, abs=1.0)
        assert claims["running"] != pytest.approx(annualised, abs=100.0)
