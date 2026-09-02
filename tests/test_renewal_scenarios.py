"""Every scenario is the house ladder with a different incurred figure.

That is the only thing worth pinning hard. A "what if we strip the big
claim" panel that builds its own price puts a second renewal premium on
the same screen, and the reader has no way to know which one the house
would actually quote - which is the exact failure the Renewal Bench hero
already had.
"""
import pytest

from app.scoring.rules.renewal_rating import (
    MINIMUM_RENEWAL_INCREASE_PCT,
    renewal_from_loss_ratio,
)
from app.scoring.rules.renewal_scenarios import (
    DEFAULT_LARGE_CLAIM_THRESHOLD,
    claims_for_members,
    large_claim_total,
    price_scenario,
    scenario_rows,
)

EXPIRING = 991_265.0
INCURRED = 877_626.0
LOADING = 0.215


def adjustment(key, label, amount, available=True, note=None):
    return {"key": key, "label": label, "amount": amount,
            "available": available, "note": note}


class TestOneScenario:
    def test_it_is_the_ladder_with_the_removal_taken_out(self):
        row = price_scenario("Large claims", EXPIRING, INCURRED, removed=188_400.0,
                             loading_pct=LOADING)
        ladder = renewal_from_loss_ratio(
            (INCURRED - 188_400.0) / EXPIRING, EXPIRING, 0.075, LOADING,
            minimum_increase_pct=MINIMUM_RENEWAL_INCREASE_PCT)
        assert row["required_premium"] == ladder["required_premium"]
        assert row["renewal_increase_pct"] == ladder["renewal_increase_pct"]
        assert row["loss_ratio"] == ladder["loss_ratio"]

    def test_removing_nothing_prices_the_account_as_reported(self):
        row = price_scenario("As reported", EXPIRING, INCURRED, loading_pct=LOADING)
        ladder = renewal_from_loss_ratio(INCURRED / EXPIRING, EXPIRING, 0.075, LOADING)
        assert row["required_premium"] == ladder["required_premium"]
        assert row["removed"] == 0.0

    def test_a_removal_always_lowers_the_price(self):
        full = price_scenario("full", EXPIRING, INCURRED, loading_pct=LOADING)
        stripped = price_scenario("stripped", EXPIRING, INCURRED, removed=200_000.0,
                                  loading_pct=LOADING)
        assert stripped["required_premium"] < full["required_premium"]

    def test_ibnr_is_not_stripped_alongside_the_claim(self):
        # Incurred is paid + outstanding + IBNR. Removing a claim takes it
        # out of paid; the IBNR tail projects the ongoing run rate, not
        # the one-off, so shrinking it too would remove the same event
        # twice. adjusted_incurred is exactly incurred - removed.
        row = price_scenario("x", EXPIRING, INCURRED, removed=100_000.0, loading_pct=LOADING)
        assert row["adjusted_incurred"] == pytest.approx(INCURRED - 100_000.0)

    def test_removing_more_than_the_incurred_clamps_at_zero(self):
        # Two adjustments can genuinely overlap - a large claim belonging
        # to a member who is also leaving - and a negative loss ratio
        # would price a renewal on a refund.
        row = price_scenario("everything", EXPIRING, INCURRED, removed=INCURRED * 5,
                             loading_pct=LOADING)
        assert row["adjusted_incurred"] == 0.0
        assert row["loss_ratio"] == 0.0
        # The house floor is what is left.
        assert row["renewal_increase_pct"] == pytest.approx(
            MINIMUM_RENEWAL_INCREASE_PCT * 100, abs=0.01)
        assert row["floor_applied"] is True

    def test_a_negative_removal_is_not_an_increase_in_disguise(self):
        row = price_scenario("x", EXPIRING, INCURRED, removed=-500_000.0, loading_pct=LOADING)
        assert row["removed"] == 0.0
        assert row["adjusted_incurred"] == pytest.approx(INCURRED)

    def test_it_reports_the_experience_beside_the_floored_ask(self):
        row = price_scenario("tiny", EXPIRING, 50_000.0, loading_pct=LOADING)
        assert row["floor_applied"] is True
        assert row["experience_increase_pct"] < row["renewal_increase_pct"]

    def test_a_non_positive_expiring_premium_is_refused(self):
        with pytest.raises(ValueError):
            price_scenario("x", 0.0, INCURRED, loading_pct=LOADING)


class TestTheTable:
    def test_as_reported_is_always_first_and_never_removed(self):
        rows = scenario_rows(EXPIRING, INCURRED,
                             [adjustment("large", "Strip large claims", 188_400.0)],
                             loading_pct=LOADING)
        assert rows[0]["key"] == "as_reported"
        assert rows[0]["removed"] == 0.0

    def test_each_adjustment_gets_its_own_row(self):
        rows = scenario_rows(EXPIRING, INCURRED, [
            adjustment("large", "Strip large claims", 188_400.0),
            adjustment("leavers", "Remove exiting members", 31_905.0),
        ], loading_pct=LOADING)
        assert [r["key"] for r in rows] == ["as_reported", "large", "leavers", "combined"]

    def test_the_combined_row_strips_everything_that_moves(self):
        rows = scenario_rows(EXPIRING, INCURRED, [
            adjustment("large", "Strip large claims", 188_400.0),
            adjustment("leavers", "Remove exiting members", 31_905.0),
        ], loading_pct=LOADING)
        combined = next(r for r in rows if r["key"] == "combined")
        assert combined["removed"] == pytest.approx(220_305.0)

    def test_a_single_adjustment_gets_no_combined_row(self):
        # It would repeat the row above it verbatim.
        rows = scenario_rows(EXPIRING, INCURRED,
                             [adjustment("large", "Strip large claims", 188_400.0)],
                             loading_pct=LOADING)
        assert "combined" not in [r["key"] for r in rows]

    def test_an_unavailable_lever_is_shown_doing_nothing_rather_than_hidden(self):
        rows = scenario_rows(EXPIRING, INCURRED, [
            adjustment("benefit", "Benefit change", 0.0, available=False,
                       note="No revised benefit table uploaded"),
        ], loading_pct=LOADING)
        benefit = next(r for r in rows if r["key"] == "benefit")
        assert benefit["removed"] == 0.0
        assert benefit["note"] == "No revised benefit table uploaded"
        assert benefit["required_premium"] == rows[0]["required_premium"]

    def test_an_available_lever_with_nothing_to_strip_does_not_trigger_combined(self):
        rows = scenario_rows(EXPIRING, INCURRED, [
            adjustment("large", "Strip large claims", 188_400.0),
            adjustment("nonrec", "Strip non-recurring", 0.0),
        ], loading_pct=LOADING)
        assert "combined" not in [r["key"] for r in rows]

    def test_rows_get_cheaper_as_more_is_stripped(self):
        # A loss-making account, so the floor is nowhere near and each
        # strip shows its own effect.
        rows = scenario_rows(EXPIRING, 2_464_284.0, [
            adjustment("large", "Strip large claims", 188_400.0),
            adjustment("leavers", "Remove exiting members", 31_905.0),
        ], loading_pct=LOADING)
        by_key = {r["key"]: r for r in rows}
        assert (by_key["combined"]["required_premium"]
                < by_key["large"]["required_premium"]
                < by_key["leavers"]["required_premium"]
                < by_key["as_reported"]["required_premium"])

    def test_once_the_ask_is_at_the_floor_stripping_more_stops_helping(self):
        # Both of these strip an account down past the point where its own
        # experience asks for less than the house minimum, so both quote
        # the minimum. Two identical premiums in the table is the honest
        # answer - the second adjustment genuinely buys nothing - and a
        # reader who does not see floor_applied would think the table was
        # broken.
        rows = scenario_rows(EXPIRING, INCURRED, [
            adjustment("large", "Strip large claims", 188_400.0),
            adjustment("leavers", "Remove exiting members", 31_905.0),
        ], loading_pct=LOADING)
        by_key = {r["key"]: r for r in rows}
        assert by_key["large"]["required_premium"] == by_key["combined"]["required_premium"]
        assert by_key["large"]["floor_applied"] is True
        assert by_key["combined"]["floor_applied"] is True
        # And the experience underneath the two is not the same figure.
        assert (by_key["combined"]["experience_increase_pct"]
                < by_key["large"]["experience_increase_pct"])

    def test_an_override_is_its_own_row_carrying_its_reason(self):
        rows = scenario_rows(EXPIRING, INCURRED, [], loading_pct=LOADING,
                             override_premium=1_691_265.0,
                             override_reason="Client will not accept more")
        override = rows[-1]
        assert override["key"] == "override"
        assert override["required_premium"] == 1_691_265.0
        assert override["renewal_increase_pct"] == pytest.approx(70.62, abs=0.01)
        assert override["note"] == "Client will not accept more"
        # It is a decision, not a loss ratio - it has no experience
        # behind it and must not pretend to.
        assert override["loss_ratio"] is None
        assert override["adjusted_incurred"] is None

    def test_no_override_means_no_override_row(self):
        rows = scenario_rows(EXPIRING, INCURRED, [], loading_pct=LOADING)
        assert "override" not in [r["key"] for r in rows]

    def test_every_row_carries_the_same_shape(self):
        rows = scenario_rows(EXPIRING, INCURRED, [
            adjustment("large", "Strip large claims", 188_400.0),
            adjustment("leavers", "Remove exiting members", 31_905.0),
        ], loading_pct=LOADING, override_premium=1_500_000.0)
        keys = set(rows[0])
        for row in rows:
            assert set(row) == keys


class TestWhatCanBeStripped:
    def test_large_claims_are_counted_by_line_not_by_member_total(self):
        # A member who reaches 60,000 through forty ordinary claims has an
        # ordinary year, not a one-off, and stripping them would price
        # away the account's real experience.
        claims = [
            {"patient_id": "A", "final_amount": 120_000.0},
            {"patient_id": "B", "final_amount": 1_500.0},
        ] + [{"patient_id": "B", "final_amount": 1_500.0} for _ in range(39)]
        result = large_claim_total(claims)
        assert result["amount"] == 120_000.0
        assert result["claim_count"] == 1
        assert result["member_count"] == 1
        assert result["beneficiary_ids"] == ["A"]

    def test_the_threshold_is_the_books_own_large_loss_line(self):
        # AED 50,000, confirmed house policy - and taken FROM Portfolio
        # Analysis's own large-loss line rather than written again, so
        # the two screens that say "large claim" cannot come to mean
        # different amounts.
        from app.scoring.rules.portfolio_analysis import DEFAULT_LARGE_CLAIM_THRESHOLDS

        assert DEFAULT_LARGE_CLAIM_THRESHOLD == 50_000.0
        assert DEFAULT_LARGE_CLAIM_THRESHOLD == DEFAULT_LARGE_CLAIM_THRESHOLDS[0]

    def test_a_claim_exactly_on_the_threshold_counts(self):
        result = large_claim_total([{"patient_id": "A", "final_amount": 50_000.0}])
        assert result["claim_count"] == 1

    def test_a_custom_threshold_is_honoured(self):
        claims = [{"patient_id": "A", "final_amount": 60_000.0}]
        assert large_claim_total(claims, threshold=100_000.0)["amount"] == 0.0

    def test_no_large_claims_is_zero_not_an_error(self):
        assert large_claim_total([])["amount"] == 0.0
        assert large_claim_total([])["beneficiary_ids"] == []

    def test_leavers_take_their_whole_claims_history_with_them(self):
        claims = [
            {"patient_id": "A", "final_amount": 10_000.0},
            {"patient_id": "A", "final_amount": 5_000.0},
            {"patient_id": "B", "final_amount": 90_000.0},
        ]
        assert claims_for_members(claims, ["A"]) == 15_000.0
        assert claims_for_members(claims, ["A", "B"]) == 105_000.0

    def test_nobody_leaving_removes_nothing(self):
        claims = [{"patient_id": "A", "final_amount": 10_000.0}]
        assert claims_for_members(claims, []) == 0.0
        assert claims_for_members(claims, [None]) == 0.0

    def test_an_unknown_member_removes_nothing(self):
        claims = [{"patient_id": "A", "final_amount": 10_000.0}]
        assert claims_for_members(claims, ["Z"]) == 0.0
