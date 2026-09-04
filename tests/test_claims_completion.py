"""How much of a treatment month's cost has actually been received yet,
built from the book's own claims history - not the flat 30-day tail.

The two failure modes this exists to catch, verified directly:
unweighted averaging letting a thin cohort drag the curve around, and
treating a pre-date_reception claim as though it were still unreceived.
"""
from datetime import date

import pytest

from app.scoring.rules.claims_completion import (
    DEFAULT_MIN_COHORT_AMOUNT,
    annual_claims_projection,
    completion_adjusted_monthly,
    completion_curve,
    large_claims_by_month,
    price_annual_claims,
)

AS_OF = date(2026, 8, 31)  # month (2026, 8)


def _claim(treated: date, received, amount: float, claim_id=None, patient_id=None, diagnosis=None) -> dict:
    return {
        "date_of_treatment": treated, "date_reception": received, "final_amount": amount,
        "claim_id": claim_id, "patient_id": patient_id, "diagnosis_description": diagnosis,
    }


def _spread(origin: date, amounts_by_lag: dict) -> list:
    """One claim per lag, dated `lag` calendar months after `origin`."""
    claims = []
    for lag, amount in amounts_by_lag.items():
        year = origin.year + (origin.month - 1 + lag) // 12
        month = (origin.month - 1 + lag) % 12 + 1
        claims.append(_claim(origin, date(year, month, 15), amount))
    return claims


class TestDollarWeighting:
    def test_a_thin_cohort_barely_moves_a_large_one(self):
        # Two separate ORIGIN MONTHS (cohorts), not one big claim and one
        # small claim in the same month - the thing being tested is
        # weighting ACROSS cohorts. A naive average of the two cohorts'
        # own lag-0 completions would read (100% + 10%) / 2 = 55%.
        # Dollar-weighted, the AED 50,000 cohort cannot move a
        # AED 1,000,000 one by much.
        big = _spread(date(2025, 10, 1), {0: 1_000_000.0})            # Oct: 100% by lag 0
        small = _spread(date(2025, 11, 1), {0: 5_000.0, 1: 45_000.0})  # Nov: 10% by lag 0

        curve = completion_curve(big + small, AS_OF)
        lag0 = next(p for p in curve["points"] if p["lag_months"] == 0)

        assert lag0["completion"] == pytest.approx(1_005_000 / 1_050_000, abs=0.0001)
        assert lag0["completion"] > 0.90  # nowhere near the naive 55% midpoint

    def test_a_cohort_below_the_credibility_floor_is_excluded_outright(self):
        oct_ = _spread(date(2025, 10, 1), {0: 1_000_000.0})
        dec_ = _spread(date(2025, 12, 1), {0: 1_000_000.0})
        # A third origin month, thin enough to be a data artifact rather
        # than a signal - below DEFAULT_MIN_COHORT_AMOUNT even though it
        # is otherwise a perfectly normal claim.
        thin = _spread(date(2025, 11, 1), {0: 200.0, 3: 1_800.0})
        assert sum(c["final_amount"] for c in thin) < DEFAULT_MIN_COHORT_AMOUNT

        curve = completion_curve(oct_ + dec_ + thin, AS_OF)

        assert "2025-11" not in curve["cohorts_used"]
        assert set(curve["cohorts_used"]) == {"2025-10", "2025-12"}
        lag0 = next(p for p in curve["points"] if p["lag_months"] == 0)
        # Unaffected by the thin cohort at all - not even partially.
        assert lag0["completion"] == 1.0


class TestReceptionDateHandling:
    def test_a_claim_with_no_reception_date_is_excluded_not_treated_as_unreceived(self):
        # Paid before date_reception existed as a field: certainly
        # received, timing unknown. Counting it as "still outstanding"
        # would invent IBNR that was never there.
        oct_ = _spread(date(2025, 10, 1), {0: 1_000_000.0})
        nov_ = _spread(date(2025, 11, 1), {0: 1_000_000.0})
        legacy = [_claim(date(2025, 10, 1), None, 500_000.0)]

        curve = completion_curve(oct_ + nov_ + legacy, AS_OF)
        lag0 = next(p for p in curve["points"] if p["lag_months"] == 0)
        assert lag0["dollars_behind"] == pytest.approx(2_000_000.0, abs=0.01)

    def test_completion_adjusted_monthly_excludes_the_same_legacy_lines(self):
        curve = completion_curve(
            _spread(date(2025, 10, 1), {0: 1_000_000.0}) +
            _spread(date(2025, 11, 1), {0: 1_000_000.0}),
            AS_OF,
        )
        account_claims = _spread(date(2025, 10, 1), {0: 200_000.0}) + [
            _claim(date(2025, 10, 1), None, 900_000.0),  # legacy, no reception date
        ]
        rows = completion_adjusted_monthly(account_claims, AS_OF, curve)
        row = next(r for r in rows if r["month"] == "2025-10")
        assert row["received"] == pytest.approx(200_000.0, abs=0.01)


class TestInsufficientData:
    def test_fewer_than_two_mature_cohorts_refuses_a_curve(self):
        curve = completion_curve(_spread(date(2025, 10, 1), {0: 1_000_000.0}), AS_OF)
        assert curve["insufficient_data"] is True
        assert curve["points"] == []

    def test_an_empty_book_refuses_a_curve_rather_than_dividing_by_zero(self):
        curve = completion_curve([], AS_OF)
        assert curve["insufficient_data"] is True

    def test_completion_adjusted_monthly_returns_nothing_without_a_curve(self):
        curve = completion_curve(_spread(date(2025, 10, 1), {0: 1_000_000.0}), AS_OF)
        rows = completion_adjusted_monthly(
            _spread(date(2025, 10, 1), {0: 200_000.0}), AS_OF, curve)
        assert rows == []


class TestTheCurveShape:
    def test_a_cohort_too_young_for_a_lag_does_not_contribute_to_it(self):
        # Two mature cohorts of different ages: the younger one has not
        # lived long enough to have a lag-8 data point, and must not be
        # silently treated as 0% or 100% complete at that lag.
        older = _spread(date(2025, 10, 1), {0: 400_000.0, 8: 100_000.0})   # age 10 at AS_OF
        younger = _spread(date(2026, 1, 1), {0: 400_000.0, 4: 100_000.0})  # age 7 at AS_OF

        curve = completion_curve(older + younger, AS_OF, min_maturity_months=6)
        lag8 = next(p for p in curve["points"] if p["lag_months"] == 8)
        # Only the older cohort could possibly have reached lag 8 by
        # AS_OF - the younger one is excluded from this point, not
        # counted as incomplete against it.
        assert lag8["cohort_count"] == 1
        assert lag8["completion"] == pytest.approx(1.0, abs=0.0001)

    def test_it_climbs_then_flattens_the_way_the_serviceplan_book_did(self):
        # Five mature cohorts sharing the same underlying development
        # pattern (48% / 94% / 98% / 99.9% by lag 0-3), the shape found
        # by hand on the real book.
        pattern = {0: 0.479, 1: 0.465, 2: 0.04, 3: 0.016}  # incremental shares
        claims = []
        for origin_month in (10, 11, 12, 1, 2):
            year = 2025 if origin_month >= 10 else 2026
            origin = date(year, origin_month, 1)
            claims += _spread(origin, {lag: share * 500_000 for lag, share in pattern.items()})

        curve = completion_curve(claims, AS_OF, min_maturity_months=6)
        by_lag = {p["lag_months"]: p["completion"] for p in curve["points"]}
        assert by_lag[0] == pytest.approx(0.479, abs=0.001)
        assert by_lag[1] == pytest.approx(0.944, abs=0.001)
        assert by_lag[2] == pytest.approx(0.984, abs=0.001)
        assert by_lag[3] == pytest.approx(1.0, abs=0.001)
        # Monotonically non-decreasing - a later lag can only add to what
        # an earlier one already saw.
        lags = sorted(by_lag)
        assert all(by_lag[lags[i]] <= by_lag[lags[i + 1]] for i in range(len(lags) - 1))


class TestExtrapolationBeyondTheCurve:
    def test_a_lag_past_the_deepest_computed_point_holds_flat(self):
        curve = completion_curve(
            _spread(date(2025, 1, 1), {0: 500_000.0}) +
            _spread(date(2025, 2, 1), {0: 500_000.0}),
            AS_OF, min_maturity_months=6, max_lag_months=3,
        )
        deepest = curve["points"][-1]
        account_claims = _spread(date(2024, 1, 1), {0: 300_000.0})  # lag far beyond 3
        rows = completion_adjusted_monthly(account_claims, AS_OF, curve)
        assert rows[0]["completion"] == deepest["completion"]


class TestMonthlyBuildUp:
    def test_completed_is_received_divided_by_completion(self):
        curve = completion_curve(
            _spread(date(2025, 1, 1), {0: 200_000.0, 1: 800_000.0}) +
            _spread(date(2025, 2, 1), {0: 200_000.0, 1: 800_000.0}),
            AS_OF, min_maturity_months=6,
        )
        # Both cohorts land at exactly 20% by lag 0.
        recent = _claim(date(2026, 7, 1), date(2026, 7, 20), 50_000.0)  # lag 1 at AS_OF
        rows = completion_adjusted_monthly([recent], AS_OF, curve)
        row = rows[0]
        assert row["received"] == 50_000.0
        assert row["completed"] == pytest.approx(50_000.0 / row["completion"], abs=0.01)

    def test_rows_come_back_oldest_month_first(self):
        curve = completion_curve(
            _spread(date(2025, 1, 1), {0: 500_000.0}) +
            _spread(date(2025, 2, 1), {0: 500_000.0}),
            AS_OF, min_maturity_months=6,
        )
        claims = (
            _spread(date(2026, 3, 1), {0: 10_000.0}) +
            _spread(date(2026, 1, 1), {0: 10_000.0})
        )
        rows = completion_adjusted_monthly(claims, AS_OF, curve)
        assert [r["month"] for r in rows] == ["2026-01", "2026-03"]

    def test_a_named_claim_line_is_pulled_out_before_completion_is_applied(self):
        curve = completion_curve(
            _spread(date(2025, 1, 1), {0: 500_000.0}) +
            _spread(date(2025, 2, 1), {0: 500_000.0}),
            AS_OF, min_maturity_months=6,
        )
        ordinary = _claim(date(2026, 7, 1), date(2026, 7, 5), 40_000.0, claim_id="C1")
        large = _claim(date(2026, 7, 1), date(2026, 7, 5), 130_000.0, claim_id="C2")

        with_it = completion_adjusted_monthly([ordinary, large], AS_OF, curve)
        without_it = completion_adjusted_monthly(
            [ordinary, large], AS_OF, curve, exclude_claim_ids=["C2"])

        assert with_it[0]["received"] == pytest.approx(170_000.0, abs=0.01)
        assert without_it[0]["received"] == pytest.approx(40_000.0, abs=0.01)
        # The completion factor itself is unchanged by which claims are
        # in the month - it describes the book's own reporting lag, not
        # this account's claim mix.
        assert with_it[0]["completion"] == without_it[0]["completion"]


class TestLargeClaimsByMonth:
    def test_only_lines_at_or_above_the_threshold_are_flagged(self):
        claims = [
            _claim(date(2026, 3, 1), date(2026, 3, 5), 49_999.0, claim_id="small"),
            _claim(date(2026, 3, 1), date(2026, 3, 5), 50_000.0, claim_id="big",
                   patient_id="M1", diagnosis="Osteonecrosis"),
        ]
        flagged = large_claims_by_month(claims, threshold=50_000.0)
        assert list(flagged.keys()) == ["2026-03"]
        assert [c["claim_id"] for c in flagged["2026-03"]] == ["big"]
        assert flagged["2026-03"][0]["patient_id"] == "M1"
        assert flagged["2026-03"][0]["diagnosis"] == "Osteonecrosis"

    def test_a_month_with_nothing_large_is_not_a_key_at_all(self):
        claims = [_claim(date(2026, 3, 1), date(2026, 3, 5), 1_000.0, claim_id="c1")]
        assert large_claims_by_month(claims, threshold=50_000.0) == {}


class TestAnnualClaimsProjection:
    def test_every_month_included_is_the_ordinary_case(self):
        rows = [{"month": "2026-01", "completed": 100_000.0},
                {"month": "2026-02", "completed": 200_000.0}]
        result = annual_claims_projection(rows)
        assert result["included_months"] == ["2026-01", "2026-02"]
        assert result["months_filled"] == 10
        # 300,000 real + 10 months at the 150,000 average.
        assert result["annual_claims"] == pytest.approx(300_000.0 + 150_000.0 * 10, abs=0.01)

    def test_excluding_a_month_removes_it_from_the_total_and_the_average(self):
        rows = [{"month": "2026-01", "completed": 100_000.0},
                {"month": "2026-02", "completed": 200_000.0},
                {"month": "2026-03", "completed": 900_000.0}]  # a spike, excluded
        result = annual_claims_projection(rows, included_months=["2026-01", "2026-02"])
        assert result["excluded_months"] == ["2026-03"]
        assert result["average_included"] == pytest.approx(150_000.0, abs=0.01)
        assert result["months_filled"] == 10
        assert result["annual_claims"] == pytest.approx(300_000.0 + 150_000.0 * 10, abs=0.01)
        # The excluded month's own huge figure plays no part at all -
        # neither in the total nor in what fills the other ten months.
        assert 900_000.0 not in [result["annual_claims"]]

    def test_a_month_can_be_fed_back_in_as_its_own_row_by_including_it(self):
        # The excluded-then-added-back technique: March counts at its OWN
        # value (not the average), while the months that are genuinely
        # unobserved still get the average.
        rows = [{"month": "2026-01", "completed": 100_000.0},
                {"month": "2026-02", "completed": 200_000.0},
                {"month": "2026-03", "completed": 900_000.0}]
        result = annual_claims_projection(
            rows, included_months=["2026-01", "2026-02", "2026-03"])
        assert result["average_included"] == pytest.approx(400_000.0, abs=0.01)
        assert result["months_filled"] == 9
        assert result["annual_claims"] == pytest.approx(
            1_200_000.0 + 400_000.0 * 9, abs=0.01)

    def test_no_months_included_returns_no_projection_rather_than_zero(self):
        rows = [{"month": "2026-01", "completed": 100_000.0}]
        result = annual_claims_projection(rows, included_months=[])
        assert result["annual_claims"] is None
        assert result["months_filled"] == 12

    def test_more_observed_months_than_the_policy_year_fills_nothing_further(self):
        rows = [{"month": f"2026-{m:02d}", "completed": 10_000.0} for m in range(1, 13)]
        result = annual_claims_projection(rows, total_policy_months=12)
        assert result["months_filled"] == 0
        assert result["annual_claims"] == pytest.approx(120_000.0, abs=0.01)


class TestPriceAnnualClaims:
    def test_trends_as_a_straight_percentage_not_points_on_a_ratio(self):
        # 1,000,000 claims, 10% trend -> 1,100,000, NOT the house ladder's
        # "add 10 points to a loss ratio" convention - there is no ratio
        # here, only money.
        result = price_annual_claims(
            1_000_000.0, expiring_annual_premium=800_000.0,
            loading_pct=0.25, inflation_pct=0.10)
        assert result["trended_claims"] == pytest.approx(1_100_000.0, abs=0.01)
        assert result["required_premium"] == pytest.approx(1_100_000.0 / 0.75, abs=0.01)

    def test_the_projected_loss_ratio_is_exactly_one_minus_loading(self):
        result = price_annual_claims(
            1_000_000.0, expiring_annual_premium=800_000.0,
            loading_pct=0.25, inflation_pct=0.10)
        assert result["projected_loss_ratio"] == pytest.approx(0.75, abs=0.0001)

    def test_the_house_floor_lifts_a_low_ask(self):
        result = price_annual_claims(
            100.0, expiring_annual_premium=1_000_000.0,
            loading_pct=0.25, inflation_pct=0.10, minimum_increase_pct=0.09)
        assert result["floor_applied"] is True
        assert result["required_premium"] == pytest.approx(1_090_000.0, abs=0.01)

    def test_no_claims_withholds_the_price_rather_than_dividing_by_zero(self):
        result = price_annual_claims(
            None, expiring_annual_premium=800_000.0, loading_pct=0.25, inflation_pct=0.10)
        assert result["required_premium"] is None
