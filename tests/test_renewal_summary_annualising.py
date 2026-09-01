"""Claims to date belong against premium earned to date - both over the
same elapsed part of the year, neither side projected.
app/api/routes_cases.py's get_renewal_summary.
"""
from datetime import date


# A renewal is priced on the loading entered against that account, so a
# case under test states its fee split the way a real one does.
HOUSE_FEES = {"tpa_fee_pct": 0.065, "commission_pct": 0.15,
              "hc_fee_pct": 0.065, "qic_fee_pct": 0.05}


def _case(client, *, premium, start, renewal, claims, fees=HOUSE_FEES):
    from app.models import db_models as models

    db = client.db_session_local()
    case = models.Case(broker_name="B", company_name="Safran", industry="aviation",
                       business_type="existing", current_annual_premium=premium,
                       policy_start_date=start, renewal_date=renewal, **(fees or {}))
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


def test_the_list_carries_no_target_loss_ratio_at_all(client):
    # The board used to re-price every account to a house target loss
    # ratio. That is a different question from what the account costs, and
    # it gave a different answer to the case's own Renewal Bench. The
    # renewal ladder has no target in it - the account's own ratio, plus
    # inflation in points, grossed up for its own loading, floored at the
    # house minimum - so the board no longer advertises one.
    _case(client, premium=1_000_000.0, start=date(2025, 10, 1),
          renewal=date(2026, 9, 30), claims=[500_000.0])
    _, body = _row(client, as_of="2026-08-15")

    assert "target_net_loss_ratio" not in body
    assert "trend_pct" not in body
    # What it does carry is the ladder's own inflation, in POINTS.
    assert body["inflation_pts"] == 0.075

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
