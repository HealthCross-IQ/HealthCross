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
