"""Deriving a renewal case's opening state straight from the book -
app/scoring/rules/renewal_intake.py. Plain member dicts, no database.
"""
from datetime import date

from app.scoring.rules.renewal_intake import (
    account_members,
    census_rows_from_members,
    claim_belongs_to_term,
    current_term,
    current_term_members,
    member_annual_rate,
    renewal_intake_profile,
    term_member_windows,
)


def _member(**overrides):
    base = {
        "beneficiary_id": "B1",
        "contract": "Acme Sub LLC",
        "master_client_name": "Acme Holdings",
        "category": "A",
        "region": "Dubai",
        "product_name": "Gold",
        "relation": "Principal",
        "policy_start_date": date(2025, 5, 1),
        "policy_end_date": date(2026, 5, 1),
        "member_start_date": date(2025, 5, 1),
        "member_end_date": date(2026, 5, 1),
        "gross_premium": 12000.0,
        "actual_gross_premium": 12000.0,
    }
    base.update(overrides)
    return base


def test_account_members_rolls_subgroups_up_into_their_master():
    members = [
        _member(beneficiary_id="B1", contract="Acme Sub One", master_client_name="Acme Holdings"),
        _member(beneficiary_id="B2", contract="Acme Sub Two", master_client_name="Acme Holdings"),
        _member(beneficiary_id="B3", contract="Other Co", master_client_name="Other Holdings"),
    ]
    picked = account_members(members, "Acme Holdings")
    assert {m["beneficiary_id"] for m in picked} == {"B1", "B2"}


def test_account_members_matches_the_client_name_case_insensitively():
    members = [_member(master_client_name="Acme Holdings")]
    assert len(account_members(members, "  acme holdings ")) == 1


def test_current_term_is_the_latest_policy_year_not_every_row():
    members = [
        _member(beneficiary_id="B1", policy_start_date=date(2024, 5, 1), policy_end_date=date(2025, 5, 1)),
        _member(beneficiary_id="B1", policy_start_date=date(2025, 5, 1), policy_end_date=date(2026, 5, 1)),
        _member(beneficiary_id="B2", policy_start_date=date(2025, 5, 1), policy_end_date=date(2026, 5, 1)),
    ]
    assert current_term(members) == (date(2025, 5, 1), date(2026, 5, 1))
    assert len(current_term_members(members)) == 2


def test_current_term_start_is_the_earliest_start_on_that_term():
    # A member endorsed on mid-term still carries the scheme's own term
    # dates; a stray later start must not pull the term start forward.
    members = [
        _member(policy_start_date=date(2025, 5, 1), policy_end_date=date(2026, 5, 1)),
        _member(policy_start_date=date(2025, 9, 15), policy_end_date=date(2026, 5, 1)),
    ]
    assert current_term(members)[0] == date(2025, 5, 1)


def test_undated_rows_still_seed_a_census_rather_than_an_empty_one():
    members = [_member(policy_start_date=None, policy_end_date=None)]
    assert current_term(members) == (None, None)
    assert len(current_term_members(members)) == 1


def test_annual_rate_uses_the_full_year_premium_not_the_pro_rated_one():
    # A member who joined in month 10 is on the same annual rate as
    # everyone else in their category - they've just been charged less
    # of it. The rate is what the renewal is priced off.
    late_joiner = _member(gross_premium=12000.0, actual_gross_premium=3000.0)
    assert member_annual_rate(late_joiner) == 12000.0


def test_annual_rate_falls_back_to_the_pro_rated_premium_when_no_annual_one():
    assert member_annual_rate(_member(gross_premium=None, actual_gross_premium=3000.0)) == 3000.0
    assert member_annual_rate(_member(gross_premium=None, actual_gross_premium=None)) is None


def test_profile_annualises_premium_and_keeps_the_booked_figure_separate():
    members = [
        _member(beneficiary_id="B1", gross_premium=12000.0, actual_gross_premium=12000.0),
        _member(beneficiary_id="B2", gross_premium=12000.0, actual_gross_premium=3000.0),
    ]
    profile = renewal_intake_profile(members, "Acme Holdings")
    assert profile["member_count"] == 2
    assert profile["rated_member_count"] == 2
    assert profile["annualised_premium"] == 24000.0
    assert profile["booked_premium"] == 15000.0
    assert profile["average_annual_rate"] == 12000.0
    assert profile["region"] == "Dubai"
    assert profile["product"] == "Gold"


def test_profile_breaks_existing_premium_down_per_category():
    members = [
        _member(beneficiary_id="B1", category="A", gross_premium=12000.0),
        _member(beneficiary_id="B2", category="A", gross_premium=8000.0),
        _member(beneficiary_id="B3", category="B", gross_premium=5000.0),
    ]
    by_category = {b["category"]: b for b in renewal_intake_profile(members, "Acme Holdings")["by_category"]}
    assert by_category["A"]["member_count"] == 2
    assert by_category["A"]["annual_premium"] == 20000.0
    assert by_category["A"]["average_rate"] == 10000.0
    assert by_category["B"]["annual_premium"] == 5000.0


def test_profile_counts_but_excludes_earlier_policy_terms():
    members = [
        _member(beneficiary_id="B1", policy_start_date=date(2024, 5, 1), policy_end_date=date(2025, 5, 1)),
        _member(beneficiary_id="B1", policy_start_date=date(2025, 5, 1), policy_end_date=date(2026, 5, 1)),
    ]
    profile = renewal_intake_profile(members, "Acme Holdings")
    assert profile["member_count"] == 1
    assert profile["prior_term_member_count"] == 1
    assert profile["policy_start_date"] == date(2025, 5, 1)


def test_profile_of_an_unknown_client_is_empty_rather_than_an_error():
    assert renewal_intake_profile([_member()], "Nobody Ltd")["member_count"] == 0


def test_census_rows_carry_the_existing_rate_so_premium_adds_up_per_member():
    rows = census_rows_from_members([_member(beneficiary_id="B7", category="C", gross_premium=9000.0)])
    assert rows[0]["employee_ref"] == "B7"
    assert rows[0]["category"] == "C"
    assert rows[0]["existing_annual_rate"] == 9000.0
    assert rows[0]["member_end_date"] == date(2026, 5, 1)


def test_claims_on_the_renewal_date_belong_to_the_incoming_term_not_the_expiring_one():
    # The export's POLICY_END_DATE equals the next term's start, so the
    # period rule is half-open - the same rule the Loss Ratio board uses.
    expiring = term_member_windows([
        _member(policy_start_date=date(2025, 5, 1), policy_end_date=date(2026, 5, 1),
                member_start_date=date(2025, 5, 1), member_end_date=date(2026, 5, 1)),
    ])
    incoming = term_member_windows([
        _member(policy_start_date=date(2026, 5, 1), policy_end_date=date(2027, 5, 1),
                member_start_date=date(2026, 5, 1), member_end_date=date(2027, 5, 1)),
    ])
    boundary = date(2026, 5, 1)
    assert claim_belongs_to_term("B1", boundary, expiring) is False
    assert claim_belongs_to_term("B1", boundary, incoming) is True
    assert claim_belongs_to_term("B1", date(2025, 6, 1), expiring) is True


def test_a_claim_for_someone_not_on_the_account_never_belongs_to_it():
    windows = term_member_windows([_member(beneficiary_id="B1")])
    assert claim_belongs_to_term("SOMEONE-ELSE", date(2025, 6, 1), windows) is False
    assert claim_belongs_to_term(None, date(2025, 6, 1), windows) is False


def test_member_windows_prefer_the_members_own_dates_over_the_scheme_term():
    windows = term_member_windows([
        _member(member_start_date=date(2025, 9, 1), member_end_date=date(2026, 5, 1)),
    ])
    assert claim_belongs_to_term("B1", date(2025, 6, 1), windows) is False
    assert claim_belongs_to_term("B1", date(2025, 10, 1), windows) is True
