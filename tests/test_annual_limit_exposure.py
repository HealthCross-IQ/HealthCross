"""How much of the book an annual limit would have to pay -
app/scoring/rules/annual_limit_exposure.py.
"""
from datetime import date

from app.scoring.rules.annual_limit_exposure import (
    annual_limit_exposure,
    exposure_for_quoted_limits,
    member_rolling_year_peaks,
    members_above_limit,
    parse_limit_to_aed,
)


def _claim(patient_id, day, amount, **extra):
    return dict(patient_id=patient_id, date_of_treatment=day, final_amount=amount, **extra)


# --- the rolling year ---------------------------------------------------

def test_a_members_peak_is_their_worst_twelve_months_not_their_lifetime():
    claims = [
        _claim("A", date(2024, 1, 10), 400_000),
        _claim("A", date(2026, 1, 10), 300_000),  # two years later - a different limit year
    ]
    peaks = member_rolling_year_peaks(claims)
    assert peaks["A"]["peak_rolling_year"] == 400_000
    assert peaks["A"]["total_claims"] == 700_000


def test_claims_either_side_of_a_new_year_are_one_limit_year_not_two():
    # This is the member the report exists to find, and bucketing by
    # calendar year reports neither half of them as a breach.
    claims = [
        _claim("A", date(2025, 12, 1), 600_000),
        _claim("A", date(2026, 1, 15), 600_000),
    ]
    assert member_rolling_year_peaks(claims)["A"]["peak_rolling_year"] == 1_200_000


def test_a_window_is_365_days_inclusive_of_both_ends():
    # Day 1 and day 365 are the same policy year; day 366 is not.
    within = member_rolling_year_peaks([
        _claim("A", date(2025, 1, 1), 100),
        _claim("A", date(2025, 12, 31), 100),
    ])
    beyond = member_rolling_year_peaks([
        _claim("A", date(2025, 1, 1), 100),
        _claim("A", date(2026, 1, 2), 100),
    ])
    assert within["A"]["peak_rolling_year"] == 200
    assert beyond["A"]["peak_rolling_year"] == 100


def test_an_undated_claim_is_reported_rather_than_dropped_or_misplaced():
    # A member whose only large claim has no treatment date must not read
    # as a member who never claimed.
    claims = [_claim("A", None, 900_000), _claim("A", date(2025, 5, 1), 10_000)]
    peak = member_rolling_year_peaks(claims)["A"]
    assert peak["undated_claims"] == 900_000
    assert peak["total_claims"] == 910_000
    assert peak["peak_rolling_year"] == 10_000


# --- the exposure table -------------------------------------------------

BOOK = [
    _claim("A", date(2025, 3, 1), 1_400_000),
    _claim("B", date(2025, 3, 1), 600_000),
    _claim("C", date(2025, 3, 1), 250_000),
    _claim("D", date(2025, 3, 1), 5_000),
]


def test_counts_are_of_members_not_of_claim_lines():
    # An annual limit is breached by a person over a year, not by an
    # invoice - three lines under the limit are not three breaches.
    claims = [_claim("A", date(2025, 3, day), 200_000) for day in (1, 2, 3)]
    row = annual_limit_exposure(claims, [500_000])["rows"][0]
    assert row["members_above"] == 1


def test_reports_who_breaches_and_by_how_much_at_each_limit():
    report = annual_limit_exposure(BOOK, [500_000, 1_000_000])
    at_500k, at_1m = report["rows"]
    assert at_500k["members_above"] == 2  # A and B
    assert at_500k["spend_above_limit"] == (1_400_000 - 500_000) + (600_000 - 500_000)
    assert at_1m["members_above"] == 1  # A only
    assert at_1m["spend_above_limit"] == 400_000


def test_a_member_exactly_at_the_limit_has_not_breached_it():
    row = annual_limit_exposure([_claim("A", date(2025, 3, 1), 500_000)], [500_000])["rows"][0]
    assert row["members_above"] == 0


def test_rows_come_back_in_limit_order_however_they_were_asked_for():
    rows = annual_limit_exposure(BOOK, [1_000_000, 250_000])["rows"]
    assert [r["limit_aed"] for r in rows] == [250_000, 1_000_000]


def test_share_of_members_is_measured_against_everyone_who_claimed():
    report = annual_limit_exposure(BOOK, [1_000_000])
    assert report["member_count"] == 4
    assert report["rows"][0]["share_of_members"] == 0.25


def test_a_book_with_no_claims_does_not_divide_by_zero():
    report = annual_limit_exposure([], [500_000])
    assert report["member_count"] == 0
    assert report["rows"][0]["share_of_members"] == 0.0
    assert report["rows"][0]["members_above"] == 0


def test_names_the_members_a_limit_would_have_cut_off():
    breaching = members_above_limit(BOOK, 500_000)
    assert [m["patient_id"] for m in breaching] == ["A", "B"]
    assert breaching[0]["above_limit"] == 900_000


# --- limits as a table of benefits writes them --------------------------

def test_a_usd_limit_is_read_in_aed():
    assert parse_limit_to_aed("US$7,500,000 per year of insurance") == 7_500_000 * 3.6725


def test_a_limit_that_is_not_a_number_is_not_read_as_zero():
    # "Covered up to Policy Limit" is a real answer, and showing it as
    # zero would rank it as the harshest limit on the table.
    assert parse_limit_to_aed("Covered up to Policy Limit") is None
    assert parse_limit_to_aed("Not specified in source document") is None


def test_the_quoted_limits_report_names_a_category_it_could_not_check():
    # "We could not read category B's limit" and "category B is fine"
    # are the same silence otherwise.
    report = exposure_for_quoted_limits(
        BOOK, {"A": "USD 1,000,000", "B": "Covered up to Policy Limit"}
    )
    assert report["categories_without_a_readable_limit"] == ["B"]
    assert [c["category"] for c in report["categories"]] == ["A"]
    assert report["categories"][0]["limit_aed"] == 1_000_000 * 3.6725


def test_two_categories_on_the_same_limit_are_both_reported():
    report = exposure_for_quoted_limits(BOOK, {"A": "AED 500,000", "B": "AED 500,000"})
    assert [c["category"] for c in report["categories"]] == ["A", "B"]
    assert report["categories"][0]["members_above"] == report["categories"][1]["members_above"]
