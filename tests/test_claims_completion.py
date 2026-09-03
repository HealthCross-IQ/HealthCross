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
    completion_adjusted_monthly,
    completion_curve,
)

AS_OF = date(2026, 8, 31)  # month (2026, 8)


def _claim(treated: date, received, amount: float) -> dict:
    return {"date_of_treatment": treated, "date_reception": received, "final_amount": amount}


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
