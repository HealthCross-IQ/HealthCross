"""Claims to date belong against premium earned to date - both over the
same elapsed part of the year, neither side projected.
app/api/routes_cases.py's get_renewal_summary.
"""
from datetime import date


def _case(client, *, premium, start, renewal, claims):
    from app.models import db_models as models

    db = client.db_session_local()
    case = models.Case(broker_name="B", company_name="Safran", industry="aviation",
                       business_type="existing", current_annual_premium=premium,
                       policy_start_date=start, renewal_date=renewal)
    db.add(case)
    db.commit()
    db.refresh(case)
    db.add_all([models.ClaimsLedgerEntry(
        case_id=case.id, patient_id=f"P{i}", claim_id=f"C{i}",
        final_amount=amount, claim_status="Paid Claims",
        date_of_treatment=start) for i, amount in enumerate(claims)])
    db.commit()
    case_id = case.id
    db.close()
    return case_id


def _row(client, **params):
    body = client.get("/cases/renewal-summary", params=params).json()
    return body["cases"][0], body


def test_premium_is_earned_down_rather_than_claims_projected_up(client):
    # Both halves of the ratio cover the same elapsed period. Earning
    # the premium down is a measurement; projecting the claims up
    # asserts the rest of the year looks like the part observed, which
    # on an account carried by one family is exactly what fails.
    _case(client, premium=3_235_630.0, start=date(2025, 10, 1),
          renewal=date(2026, 9, 30), claims=[2_043_338.0])
    row, _ = _row(client, as_of="2026-08-15")

    assert row["paid"] == 2_043_338.0
    assert row["earned_fraction"] < 1.0
    assert row["earned_premium"] < row["current_annual_premium"]
    # Claims are reported as measured, plus the IBNR tail - never scaled.
    assert row["incurred_claims"] == row["paid"] + row["outstanding"] + row["ibnr"]


def test_annualising_turns_the_answer_from_a_reduction_into_an_increase(client):
    _case(client, premium=3_235_630.0, start=date(2025, 10, 1),
          renewal=date(2026, 9, 30), claims=[2_043_338.0])
    row, _ = _row(client, as_of="2026-08-15")
    assert row["suggested_increase_pct"] > 0, "the account needs more premium, not less"


def test_a_completed_year_earns_its_whole_premium_and_carries_no_ibnr(client):
    # Past expiry both sides are final: the premium is fully earned and
    # claims have had a year to filter through.
    _case(client, premium=1_000_000.0, start=date(2025, 1, 1),
          renewal=date(2025, 12, 31), claims=[800_000.0])
    row, _ = _row(client, as_of="2026-08-15")
    assert row["earned_fraction"] == 1.0
    assert row["ibnr"] == 0.0
    assert row["incurred_claims"] == 800_000.0


def test_a_case_with_no_policy_start_earns_its_whole_premium(client):
    # Nothing to measure elapsed time against. Treating the premium as
    # fully earned is the conservative reading - it cannot understate
    # the loss ratio.
    _case(client, premium=1_000_000.0, start=None, renewal=None, claims=[500_000.0])
    row, _ = _row(client)
    assert row["elapsed_days"] is None
    assert row["earned_fraction"] == 1.0
    assert row["incurred_claims"] == 500_000.0


def test_the_list_prices_to_the_house_target_not_to_break_even(client):
    # It defaulted to a net loss ratio of 1.0 while new business priced
    # to 95%, so the same book was held to two different targets
    # depending on which screen it was looked at from.
    from app.scoring.rules.experience_pricing import HOUSE_TARGET_LOSS_RATIO

    _case(client, premium=1_000_000.0, start=date(2025, 10, 1),
          renewal=date(2026, 9, 30), claims=[500_000.0])
    _, body = _row(client, as_of="2026-08-15")
    assert body["target_net_loss_ratio"] == HOUSE_TARGET_LOSS_RATIO


def test_the_build_up_is_reported_line_by_line(client):
    # So the figure can be checked against a spreadsheet rather than
    # trusted whole - which is how the 71% against 112% was caught.
    _case(client, premium=1_000_000.0, start=date(2025, 10, 1),
          renewal=date(2026, 9, 30), claims=[400_000.0])
    row, _ = _row(client, as_of="2026-08-15")
    for field in ("paid", "outstanding", "ibnr", "earned_premium",
                  "earned_fraction", "current_annual_premium", "elapsed_days"):
        assert field in row, field
    assert row["ibnr"] > 0, "a part-run year carries an unreported tail"
