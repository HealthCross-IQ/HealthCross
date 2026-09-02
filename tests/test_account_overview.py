"""The dashboard's two new readings.

Everything else on the account dashboard is assembled from functions
that already exist, so these are the only two that can drift on their
own - and the monthly split is the one most likely to be misread, since
a month that looks like deterioration is often a month the TPA has not
settled yet.
"""
from datetime import date

import pytest

from app.scoring.rules.account_overview import (
    book_position,
    claims_by_month,
    data_window,
    loss_ratio_by_period,
    monthly_burning_cost,
)

PAID = "Paid Claims"
OUTSTANDING = "Outstanding Claims"


def claim(day, amount, status=PAID):
    return {"date_of_treatment": day, "final_amount": amount, "claim_status": status}


class TestClaimsByMonth:
    def test_splits_paid_from_outstanding(self):
        rows = claims_by_month([
            claim(date(2026, 4, 3), 1000.0, PAID),
            claim(date(2026, 4, 20), 400.0, OUTSTANDING),
        ])
        assert rows == [{
            "month": "2026-04", "paid": 1000.0, "outstanding": 400.0,
            "total": 1400.0, "claim_count": 2,
        }]

    def test_an_unknown_status_counts_as_outstanding(self):
        # Matches _is_paid_claim_status: a future status value must not
        # silently become a settled claim.
        rows = claims_by_month([claim(date(2026, 4, 3), 500.0, "Under Review")])
        assert rows[0]["outstanding"] == 500.0
        assert rows[0]["paid"] == 0.0

    def test_a_blank_status_counts_as_outstanding(self):
        rows = claims_by_month([claim(date(2026, 4, 3), 500.0, None)])
        assert rows[0]["outstanding"] == 500.0

    def test_validated_counts_as_paid(self):
        rows = claims_by_month([claim(date(2026, 4, 3), 500.0, "Validated Claims")])
        assert rows[0]["paid"] == 500.0

    def test_quiet_months_are_zero_rows_not_gaps(self):
        rows = claims_by_month([
            claim(date(2026, 4, 3), 100.0),
            claim(date(2026, 7, 3), 100.0),
        ])
        assert [r["month"] for r in rows] == ["2026-04", "2026-05", "2026-06", "2026-07"]
        assert rows[1]["total"] == 0.0
        assert rows[1]["claim_count"] == 0

    def test_the_gap_spans_a_year_end(self):
        rows = claims_by_month([
            claim(date(2025, 11, 3), 100.0),
            claim(date(2026, 2, 3), 100.0),
        ])
        assert [r["month"] for r in rows] == ["2025-11", "2025-12", "2026-01", "2026-02"]

    def test_months_limits_to_the_most_recent(self):
        rows = claims_by_month(
            [claim(date(2026, m, 1), 100.0) for m in range(1, 9)], months=3
        )
        assert [r["month"] for r in rows] == ["2026-06", "2026-07", "2026-08"]

    def test_undated_claims_are_dropped_not_bucketed(self):
        rows = claims_by_month([
            claim(date(2026, 4, 3), 100.0),
            {"date_of_treatment": None, "final_amount": 9999.0, "claim_status": PAID},
        ])
        assert len(rows) == 1
        assert rows[0]["total"] == 100.0

    def test_no_claims_is_an_empty_list(self):
        assert claims_by_month([]) == []

    def test_a_missing_amount_is_zero_not_an_error(self):
        rows = claims_by_month([
            {"date_of_treatment": date(2026, 4, 3), "final_amount": None,
             "claim_status": PAID},
        ])
        assert rows[0]["paid"] == 0.0
        assert rows[0]["claim_count"] == 1


def book_row(client, loss_ratio, outstanding, incurred, members, premium):
    return {
        "master_client": client,
        "gross_loss_ratio": loss_ratio,
        "outstanding": outstanding,
        "incurred_claims": incurred,
        "member_count": members,
        "gross_premium": premium,
    }


KAF = book_row("KAF", 2.486, 451519.0, 877626.0, 123, 991265.0)
BOOK = [
    KAF,
    book_row("A", 0.55, 30000.0, 300000.0, 100, 600000.0),
    book_row("B", 0.71, 90000.0, 500000.0, 200, 700000.0),
    book_row("C", 0.88, 40000.0, 400000.0, 150, 500000.0),
    book_row("D", 1.20, 63000.0, 350000.0, 80, 300000.0),
]


class TestBookPosition:
    def test_kaf_is_at_the_top_of_the_book(self):
        position = book_position(KAF, BOOK)
        assert position["loss_ratio_percentile"] == 100
        assert position["accounts"] == 5

    def test_the_median_is_the_middle_of_five(self):
        position = book_position(KAF, BOOK)
        assert position["book_median_loss_ratio"] == pytest.approx(0.88)

    def test_the_median_of_an_even_book_is_the_average_of_the_middle_two(self):
        position = book_position(KAF, BOOK[:4])
        assert position["book_median_loss_ratio"] == pytest.approx((0.71 + 0.88) / 2)

    def test_outstanding_share_is_reported_against_the_book(self):
        position = book_position(KAF, BOOK)
        assert position["outstanding_share"] == pytest.approx(451519.0 / 877626.0)
        assert position["book_median_outstanding_share"] == pytest.approx(0.18)

    def test_per_life_figures(self):
        position = book_position(KAF, BOOK)
        assert position["premium_per_life"] == pytest.approx(991265.0 / 123)
        assert position["claims_per_life"] == pytest.approx(877626.0 / 123)

    def test_a_median_account_lands_mid_book(self):
        position = book_position(BOOK[3], BOOK)
        assert 50 <= position["loss_ratio_percentile"] <= 70

    def test_the_best_account_is_never_percentile_zero(self):
        # A rank of 0 reads as missing data rather than as "the best on
        # the book", so the scale starts at 1.
        position = book_position(BOOK[1], BOOK)
        assert position["loss_ratio_percentile"] == 20

    def test_no_row_is_none(self):
        assert book_position(None, BOOK) is None

    def test_no_book_is_none(self):
        assert book_position(KAF, []) is None

    def test_accounts_with_no_loss_ratio_are_left_out_of_the_median(self):
        book = BOOK + [book_row("E", None, 0.0, 0.0, 10, 0.0)]
        position = book_position(KAF, book)
        assert position["book_median_loss_ratio"] == pytest.approx(0.88)

    def test_the_input_rows_are_never_mutated(self):
        before = [dict(r) for r in BOOK]
        book_position(KAF, BOOK)
        assert BOOK == before


class TestDataWindow:
    def test_first_and_last_treatment_date(self):
        window = data_window([
            claim(date(2026, 4, 3), 1.0),
            claim(date(2026, 8, 29), 1.0),
            claim(date(2026, 6, 1), 1.0),
        ])
        assert window == {"from": date(2026, 4, 3), "to": date(2026, 8, 29)}

    def test_no_dated_claims_is_two_nones(self):
        assert data_window([]) == {"from": None, "to": None}


# --- an account with more than one year on the book ---------------------

def period(start, loss_ratio, members=100, incurred=500_000.0, expired=True):
    return {
        "policy_start_date": start, "days": 365 if expired else 130, "expired": expired,
        "member_count": members, "paid": incurred * 0.8, "outstanding": incurred * 0.15,
        "ibnr": incurred * 0.05, "incurred_claims": incurred,
        "gross_premium": 1_000_000.0, "earned_premium": 1_000_000.0 if expired else 356_164.0,
        "gross_loss_ratio": loss_ratio,
        "net_loss_ratio": (loss_ratio / 0.785) if loss_ratio is not None else None,
        "loading_pct": 0.215, "loading_is_default": False,
    }


class TestLossRatioByPeriod:
    def test_oldest_first_however_the_rows_arrive(self):
        rows = loss_ratio_by_period([period("2026-04-24", 2.41), period("2025-04-24", 0.68)])
        assert [r["policy_start_date"] for r in rows] == ["2025-04-24", "2026-04-24"]

    def test_the_move_is_in_points_not_a_percentage_of_a_percentage(self):
        # "Up 173 points" is a fact about the account. "Up 254%" is a fact
        # about the arithmetic and reads as a premium change.
        rows = loss_ratio_by_period([period("2025-04-24", 0.68), period("2026-04-24", 2.41)])
        assert rows[1]["change_pts"] == pytest.approx(173.0, abs=0.1)

    def test_the_first_year_has_nothing_to_move_against(self):
        rows = loss_ratio_by_period([period("2025-04-24", 0.68)])
        assert rows[0]["change_pts"] is None

    def test_an_improving_account_moves_down(self):
        rows = loss_ratio_by_period([period("2025-04-24", 1.20), period("2026-04-24", 0.85)])
        assert rows[1]["change_pts"] == pytest.approx(-35.0, abs=0.1)

    def test_a_year_with_no_loss_ratio_does_not_become_the_baseline(self):
        # An account with no earned premium in one year would otherwise
        # make the following year's move meaningless.
        rows = loss_ratio_by_period([
            period("2024-04-24", 0.68), period("2025-04-24", None), period("2026-04-24", 0.90)])
        assert rows[1]["change_pts"] is None
        assert rows[2]["change_pts"] == pytest.approx(22.0, abs=0.1)

    def test_a_running_year_is_flagged_as_a_part_year(self):
        rows = loss_ratio_by_period([period("2026-04-24", 2.41, expired=False)])
        assert rows[0]["part_year"] is True
        assert loss_ratio_by_period([period("2025-04-24", 0.68)])[0]["part_year"] is False

    def test_no_rows_is_an_empty_list(self):
        assert loss_ratio_by_period([]) == []


class TestMonthlyBurningCost:
    def _members(self, n, start=date(2026, 4, 24), end=date(2027, 4, 23)):
        return [{"member_start_date": start, "member_end_date": end} for _ in range(n)]

    def test_claims_over_the_exposure_actually_carried(self):
        rows = monthly_burning_cost(
            [claim(date(2026, 5, 10), 12_000.0)],
            self._members(10), date(2026, 4, 24), date(2027, 4, 23),
            up_to=date(2026, 5, 31))
        may = next(r for r in rows if r["month"] == "2026-05")
        assert may["erp"] == pytest.approx(10.0)
        assert may["burning_cost"] == pytest.approx(1_200.0)

    def test_a_mid_month_joiner_is_a_fraction_of_a_life_not_a_whole_one(self):
        # Dividing by the closing headcount flatters the early months,
        # which is the shape that makes a deteriorating account look
        # steady.
        members = self._members(9) + [{"member_start_date": date(2026, 5, 21),
                                       "member_end_date": date(2027, 4, 23)}]
        rows = monthly_burning_cost([claim(date(2026, 5, 10), 12_000.0)], members,
                                    date(2026, 4, 24), date(2027, 4, 23),
                                    up_to=date(2026, 5, 31))
        may = next(r for r in rows if r["month"] == "2026-05")
        assert 9.0 < may["erp"] < 10.0
        assert may["burning_cost"] > 1_200.0

    def test_months_the_data_does_not_reach_are_dropped_not_shown_at_nil(self):
        rows = monthly_burning_cost([claim(date(2026, 5, 10), 1_000.0)], self._members(5),
                                    date(2026, 4, 24), date(2027, 4, 23),
                                    up_to=date(2026, 6, 30))
        assert [r["month"] for r in rows] == ["2026-04", "2026-05", "2026-06"]

    def test_a_quiet_month_inside_the_window_is_a_real_zero(self):
        rows = monthly_burning_cost([claim(date(2026, 4, 25), 1_000.0)], self._members(5),
                                    date(2026, 4, 24), date(2027, 4, 23),
                                    up_to=date(2026, 6, 30))
        june = next(r for r in rows if r["month"] == "2026-06")
        assert june["incurred"] == 0.0
        assert june["burning_cost"] == 0.0

    def test_paid_and_outstanding_are_still_told_apart(self):
        rows = monthly_burning_cost([
            claim(date(2026, 5, 10), 8_000.0, PAID),
            claim(date(2026, 5, 20), 4_000.0, OUTSTANDING),
        ], self._members(10), date(2026, 4, 24), date(2027, 4, 23), up_to=date(2026, 5, 31))
        may = next(r for r in rows if r["month"] == "2026-05")
        assert may["paid"] == 8_000.0
        assert may["outstanding"] == 4_000.0
        assert may["burning_cost"] == pytest.approx(1_200.0)

    def test_no_exposure_gives_no_burning_cost_rather_than_a_division(self):
        rows = monthly_burning_cost([claim(date(2026, 5, 10), 1_000.0)], [],
                                    date(2026, 4, 24), date(2027, 4, 23),
                                    up_to=date(2026, 5, 31))
        assert all(r["burning_cost"] is None for r in rows)
