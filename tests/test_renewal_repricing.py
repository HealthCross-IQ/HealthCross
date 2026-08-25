"""An account's renewal is decided by a handful of people, and a total
hides them - app/scoring/rules/renewal_repricing.py.
"""
from datetime import date

import pytest

from app.scoring.rules.renewal_repricing import (
    annualise,
    member_claim_ranking,
    monthly_totals,
    premium_for,
    reprice,
)
from app.scoring.rules.renewal_intake import term_member_windows

TERM_START, TERM_END = date(2025, 10, 1), date(2026, 10, 1)


def _m(bid, relation="Main Insured", premium=14_550.0):
    return {"beneficiary_id": bid, "relation": relation,
            "policy_start_date": TERM_START, "policy_end_date": TERM_END,
            "member_start_date": TERM_START, "member_end_date": TERM_END,
            "gross_premium": premium, "actual_gross_premium": premium}


def _claims(bid, per_month, months):
    return [{"patient_id": bid, "final_amount": per_month, "claim_status": "Paid Claims",
             "date_of_treatment": date(y, m, 15), "diagnosis_description": "Something"}
            for (y, m) in months]


def _fixture():
    members = [_m("BIG"), _m("A"), _m("B")]
    months = [(2025, 11), (2025, 12)] + [(2026, m) for m in range(1, 8)]
    by = {
        "BIG": _claims("BIG", 50_000.0, months),
        "A": _claims("A", 5_000.0, months),
        "B": _claims("B", 5_000.0, months),
    }
    return members, by, term_member_windows(members)


# --- annualising ---------------------------------------------------------

def test_a_part_month_is_not_averaged_in_as_though_it_were_whole():
    # The month an export is cut in is partly there. Averaging it with
    # full months drags the run-rate down by whatever is missing - always
    # downward, which is the direction that flatters an account.
    monthly = {(2026, m): 100_000.0 for m in range(1, 8)}
    monthly[(2026, 8)] = 40_000.0                       # cut on the 15th
    run = annualise(monthly, data_to=date(2026, 8, 15))
    assert run["full_months"] == 7
    assert run["months_dropped"] == ["2026-08"]
    assert run["average_full_month"] == 100_000.0
    assert run["annualised"] == 1_200_000.0


def test_including_the_part_month_would_have_understated_it():
    monthly = {**{(2026, m): 100_000.0 for m in range(1, 8)}, (2026, 8): 40_000.0}
    honest = annualise(monthly, data_to=date(2026, 8, 15))["annualised"]
    naive = sum(monthly.values()) / len(monthly) * 12
    assert honest > naive


def test_the_number_of_months_it_rests_on_is_reported():
    # A run-rate off three months is not the same claim as one off
    # eleven, and the reader should not have to ask.
    run = annualise({(2026, 1): 10.0, (2026, 2): 20.0}, data_to=date(2026, 2, 28))
    assert run["full_months"] == 2
    assert run["months_used"] == ["2026-01", "2026-02"]


def test_no_complete_month_gives_no_run_rate_rather_than_a_guess():
    run = annualise({(2026, 8): 40_000.0}, data_to=date(2026, 8, 15))
    assert run["annualised"] is None
    assert "no complete month" in run["note"]


def test_nothing_at_all_is_not_a_zero_run_rate():
    assert annualise({})["annualised"] is None


# --- the price -----------------------------------------------------------

def test_the_premium_is_grossed_up_not_marked_up():
    # The loading is a share of the premium, so the claims-funding part
    # is premium x (1 - loading). Marking claims up instead understates
    # the price, and by more the higher the loading.
    assert premium_for(1_000_000, 0.265, 0.95) == pytest.approx(1_432_151.81, abs=1)
    assert premium_for(1_000_000, 0.265, 0.95) > 1_000_000 * 1.265


def test_no_claims_gives_no_price():
    assert premium_for(None, 0.265, 0.95) is None
    assert premium_for(1_000, 0.265, 0.0) is None


# --- holding a member out ------------------------------------------------

def test_holding_the_big_claimant_out_lowers_the_price():
    members, by, windows = _fixture()
    r = reprice(members, by, windows, current_premium=43_650.0,
                loading_pct=0.265, target_loss_ratio=0.95, exclude=["BIG"],
                data_to=date(2026, 7, 31))
    assert r["excluding"]["required_premium"] < r["as_priced"]["required_premium"]
    assert r["worth_of_exclusion"] > 0


def test_the_price_with_everybody_in_is_always_returned_too():
    # A figure produced by holding someone out is only a price if they
    # are not renewing. Showing it alone invites it to be quoted as
    # though it were.
    members, by, windows = _fixture()
    r = reprice(members, by, windows, current_premium=43_650.0,
                loading_pct=0.265, target_loss_ratio=0.95, exclude=["BIG"],
                data_to=date(2026, 7, 31))
    assert r["as_priced"]["required_premium"] is not None
    assert r["excluded_members"] == [{"beneficiary_id": "BIG", "relation": "Main Insured"}]


def test_every_exclusion_carries_its_condition_in_writing():
    members, by, windows = _fixture()
    r = reprice(members, by, windows, 43_650.0, 0.265, 0.95, exclude=["BIG"],
                data_to=date(2026, 7, 31))
    assert "not renewing" in r["caveat"]


def test_excluding_nobody_leaves_no_caveat_and_no_second_column():
    members, by, windows = _fixture()
    r = reprice(members, by, windows, 43_650.0, 0.265, 0.95, data_to=date(2026, 7, 31))
    assert r["excluding"] is None
    assert r["caveat"] is None


def test_the_increase_is_measured_against_the_premium_actually_charged():
    members, by, windows = _fixture()
    r = reprice(members, by, windows, current_premium=43_650.0,
                loading_pct=0.265, target_loss_ratio=0.95, data_to=date(2026, 7, 31))
    a = r["as_priced"]
    assert a["increase_vs_current_pct"] == pytest.approx(a["required_premium"] / 43_650.0 - 1, abs=1e-4)


def test_no_current_premium_gives_no_increase_rather_than_a_wrong_one():
    members, by, windows = _fixture()
    r = reprice(members, by, windows, current_premium=None,
                loading_pct=0.265, target_loss_ratio=0.95, data_to=date(2026, 7, 31))
    assert r["as_priced"]["increase_vs_current_pct"] is None


# --- who the cost actually is --------------------------------------------

def test_the_ranking_puts_the_account_where_it_actually_is():
    members, by, windows = _fixture()
    rows = member_claim_ranking(members, by, windows)
    assert rows[0]["beneficiary_id"] == "BIG"
    assert rows[0]["share_of_claims"] > 0.8


def test_the_ranking_carries_the_monthly_run_so_a_one_off_can_be_told_from_a_condition():
    # The whole of whether holding someone out is defensible: a finished
    # event and treatment still running look identical in a total.
    members, by, windows = _fixture()
    row = next(r for r in member_claim_ranking(members, by, windows) if r["beneficiary_id"] == "BIG")
    assert row["months_with_claims"] == 9
    assert len(row["monthly"]) == 9
    assert row["monthly"][0]["month"] == "2025-11"


def test_a_member_who_never_claimed_is_left_out_of_the_ranking():
    members, by, windows = _fixture()
    members.append(_m("QUIET"))
    assert "QUIET" not in {r["beneficiary_id"] for r in member_claim_ranking(members, by, windows)}


def test_monthly_totals_respect_an_exclusion():
    members, by, windows = _fixture()
    everyone = monthly_totals(members, by, windows)
    without = monthly_totals(members, by, windows, exclude=["BIG"])
    assert everyone[(2026, 1)] == 60_000.0
    assert without[(2026, 1)] == 10_000.0


# --- the endpoint --------------------------------------------------------

def test_the_endpoint_prices_an_account_and_ranks_who_it_is(client):
    from app.models import db_models as models

    db = client.db_session_local()
    members, by, _ = _fixture()
    db.bulk_insert_mappings(models.PortfolioMember, [
        {**m, "contract": "Acme", "master_contract": "Acme"} for m in members])
    db.bulk_insert_mappings(models.PortfolioClaimEntry, [
        {"patient_id": c["patient_id"], "final_amount": c["final_amount"],
         "claim_status": c["claim_status"], "date_of_treatment": c["date_of_treatment"],
         "policy_start_date": TERM_START, "policy_end_date": TERM_END,
         "member_start_date": TERM_START, "member_end_date": TERM_END}
        for rows in by.values() for c in rows])
    db.commit()
    db.close()

    r = client.get("/portfolio-analysis/renewal-repricing/Acme").json()
    assert r["as_priced"]["member_count"] == 3
    assert r["top_claimants"][0]["beneficiary_id"] == "BIG"
    assert r["excluding"] is None

    held = client.get("/portfolio-analysis/renewal-repricing/Acme",
                      params={"exclude": ["BIG"]}).json()
    assert held["excluding"]["required_premium"] < held["as_priced"]["required_premium"]
    assert held["worth_of_exclusion"] > 0
    assert held["caveat"]
