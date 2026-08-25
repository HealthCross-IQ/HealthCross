"""A part-year numerator over a whole-year denominator is not a loss
ratio - app/api/routes_cases.py's get_renewal_summary.
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


def test_a_part_year_ledger_is_annualised_before_it_is_compared_with_premium(client):
    # Ten and a half months into the year, the raw sum divided by a full
    # year's premium understated Safran at 86% and suggested giving 6%
    # back, while the same account's Renewal Bench asked for +26%.
    _case(client, premium=3_235_630.0, start=date(2025, 10, 1),
          renewal=date(2026, 9, 30), claims=[2_043_338.0])
    row, _ = _row(client, as_of="2026-08-15")

    assert row["incurred_claims_to_date"] == 2_043_338.0
    assert row["annualised"] is True
    assert row["incurred_claims"] > row["incurred_claims_to_date"]
    assert row["elapsed_days"] < 365


def test_annualising_turns_the_answer_from_a_reduction_into_an_increase(client):
    _case(client, premium=3_235_630.0, start=date(2025, 10, 1),
          renewal=date(2026, 9, 30), claims=[2_043_338.0])
    row, _ = _row(client, as_of="2026-08-15")
    assert row["suggested_increase_pct"] > 0, "the account needs more premium, not less"


def test_a_completed_year_is_not_annualised_again(client):
    # Once the term is over the figure is complete. Scaling it up a
    # second time would inflate every expired account on the list.
    _case(client, premium=1_000_000.0, start=date(2025, 1, 1),
          renewal=date(2025, 12, 31), claims=[800_000.0])
    row, _ = _row(client, as_of="2026-08-15")
    assert row["annualised"] is False
    assert row["incurred_claims"] == 800_000.0


def test_a_case_with_no_policy_start_is_left_as_measured(client):
    # Nothing to measure elapsed time against. Reporting the raw sum is
    # honest; inventing a term to scale it by is not.
    _case(client, premium=1_000_000.0, start=None, renewal=None, claims=[500_000.0])
    row, _ = _row(client)
    assert row["elapsed_days"] is None
    assert row["incurred_claims"] == 500_000.0
    assert row["annualised"] is False


def test_the_list_prices_to_the_house_target_not_to_break_even(client):
    # It defaulted to a net loss ratio of 1.0 while new business priced
    # to 95%, so the same book was held to two different targets
    # depending on which screen it was looked at from.
    from app.scoring.rules.experience_pricing import HOUSE_TARGET_LOSS_RATIO

    _case(client, premium=1_000_000.0, start=date(2025, 10, 1),
          renewal=date(2026, 9, 30), claims=[500_000.0])
    _, body = _row(client, as_of="2026-08-15")
    assert body["target_net_loss_ratio"] == HOUSE_TARGET_LOSS_RATIO


def test_both_the_measured_and_the_projected_figure_are_reported(client):
    # So a reader can see what was counted and what was inferred from it,
    # rather than being handed one number and asked to trust it.
    _case(client, premium=1_000_000.0, start=date(2025, 10, 1),
          renewal=date(2026, 9, 30), claims=[400_000.0])
    row, _ = _row(client, as_of="2026-08-15")
    assert row["incurred_claims_to_date"] == 400_000.0
    assert row["incurred_claims"] != row["incurred_claims_to_date"]
    assert row["elapsed_days"]
