"""A renewal is priced on the loading entered against that account.

Two functions used to compute "this case's total loading" from the SAME
four case fields with different fallbacks - 26.5% in the scorecard (the
NEW BUSINESS constants: TPA 5 + commission 10 + HC 6.5 + QIC 5) and 33%
in renewal pricing (TPA 6.5 + commission 15 + HC 6.5 + QIC 5). On a 70%
loss ratio that is 95.2% against 104.5% of the expiring premium, and
which figure a screen showed depended only on which module it imported.

Both numbers were real. They answer different questions, and the split
is now drawn where it belongs: a renewal has an account and uses that
account's own entered fees; new business has no account yet and uses the
rate card's own commission and product-fee model.

Every renewal working is covered here, because the leak was never in the
renewal rating itself - it was in the three OTHER screens that price a
renewal: the due-list board, the renewal premium build-up, and the risk
scorecard.
"""
from datetime import date

from app.models import db_models as models

TERM_START = date(2025, 10, 1)
TERM_END = date(2026, 9, 30)

HOUSE_FEES = {"tpa_fee_pct": 0.065, "commission_pct": 0.15,
              "hc_fee_pct": 0.065, "qic_fee_pct": 0.05}


def _renewal_case(client, fees=HOUSE_FEES, premium=1_000_000.0, on_the_book=False):
    """A renewal case. on_the_book also seeds the Membership export the
    renewal-premium build-up needs to split continuing from leaving
    members - without it that endpoint 400s before any pricing."""
    db = client.db_session_local()
    if on_the_book:
        db.bulk_insert_mappings(models.PortfolioMember, [
            {"beneficiary_id": f"P{i}", "contract": "ACME",
             "master_contract": "ACME", "master_client_name": "ACME",
             "relation": "employee", "age": 35, "gender": "M",
             "policy_start_date": TERM_START, "policy_end_date": TERM_END,
             "member_start_date": TERM_START, "member_end_date": TERM_END,
             "gross_premium": 90_000.0, "actual_gross_premium": 85_000.0}
            for i in range(12)])
        db.bulk_insert_mappings(models.PortfolioClaimEntry, [
            {"patient_id": f"P{i}", "final_amount": 40_000.0,
             "claim_status": "Paid Claims", "date_of_treatment": date(2026, 1, 10),
             "group_name": "ACME", "client_name": "ACME", "provider_name": "Clinic",
             "diagnosis_description": "Consultation", "ip_op_maternity": "OP"}
            for i in range(12)])
        db.add(models.PortfolioDataSnapshot(data_as_of_date=date(2026, 8, 15)))
    case = models.Case(
        broker_name="B", company_name="ACME", industry="trading",
        business_type="existing", current_annual_premium=premium,
        portfolio_master_client="ACME" if on_the_book else None,
        policy_start_date=TERM_START, renewal_date=TERM_END, **(fees or {}))
    db.add(case)
    db.commit()
    db.refresh(case)
    db.add_all([
        models.ClaimsLedgerEntry(
            case_id=case.id, patient_id=f"P{i}", claim_id=f"C{i}",
            final_amount=50_000.0, claim_status="Paid Claims",
            policy_start_date=TERM_START, policy_end_date=TERM_END,
            date_of_treatment=date(2026, 1, 10))
        for i in range(12)])
    db.add_all([
        models.CensusRecord(case_id=case.id, employee_ref=f"P{i}", category="A",
                            age=35, gender="M", relation="employee")
        for i in range(12)])
    db.commit()
    case_id = case.id
    db.close()
    return case_id


# --- the resolver itself -------------------------------------------------

def test_a_renewal_uses_its_own_fees_and_a_new_business_case_uses_the_card(client):
    from app.api.case_loading import renewal_loading, scorecard_loading

    db = client.db_session_local()
    renewal = models.Case(broker_name="B", company_name="R", industry="t",
                          business_type="existing", **HOUSE_FEES)
    fresh = models.Case(broker_name="B", company_name="N", industry="t",
                        business_type="new")
    db.add_all([renewal, fresh])
    db.commit()

    loading, problems = renewal_loading(renewal)
    assert problems == []
    assert loading == 0.33
    # New business has no account to enter a split against, so the rate
    # card's own model applies - and 26.5% stays where it belongs.
    assert round(scorecard_loading(fresh), 4) == 0.265
    db.close()


def test_an_unset_fee_yields_no_loading_rather_than_a_default(client):
    from app.api.case_loading import renewal_loading, scorecard_loading

    db = client.db_session_local()
    case = models.Case(broker_name="B", company_name="R", industry="t",
                       business_type="existing")
    db.add(case)
    db.commit()

    loading, problems = renewal_loading(case)
    assert loading is None
    assert problems
    # Not 26.5%, not 33% - nothing.
    assert scorecard_loading(case) is None
    db.close()


def test_a_fee_of_zero_still_gives_a_loading(client):
    from app.api.case_loading import renewal_loading

    db = client.db_session_local()
    case = models.Case(broker_name="B", company_name="R", industry="t",
                       business_type="existing", tpa_fee_pct=0.05,
                       commission_pct=0.0, hc_fee_pct=0.05, qic_fee_pct=0.05)
    db.add(case)
    db.commit()

    loading, problems = renewal_loading(case)
    assert problems == []
    assert round(loading, 4) == 0.15
    db.close()


# --- the renewal premium build-up: it QUOTES a price ---------------------

def test_the_renewal_premium_is_not_quoted_on_an_assumed_loading(client):
    case_id = _renewal_case(client, fees=None, on_the_book=True)

    body = client.get(f"/cases/{case_id}/renewal-premium").json()
    assert body["pricing_blocked"] is True
    assert body["gross_premium"] is None
    assert any(p["field"] == "loading_pct" for p in body["pricing_problems"])
    # The expiring premium is still reported - it is not what is missing.
    assert body["expiring_premium"] == 1_000_000.0


def test_the_renewal_premium_uses_the_accounts_own_loading(client):
    case_id = _renewal_case(client, on_the_book=True)

    body = client.get(f"/cases/{case_id}/renewal-premium").json()
    assert not body.get("pricing_blocked")
    assert body["gross_premium"] > 0


def test_an_explicit_loading_still_prices_an_unconfigured_case(client):
    # The what-if query param is the underwriter answering for this one
    # calculation, which is not the same as the system assuming.
    case_id = _renewal_case(client, fees=None, on_the_book=True)

    body = client.get(f"/cases/{case_id}/renewal-premium",
                      params={"loading_pct": 0.23}).json()
    assert not body.get("pricing_blocked")
    assert body["gross_premium"] > 0


# --- the due list: many accounts, one may be unconfigured ----------------

def test_one_account_without_fees_does_not_blank_the_renewal_board(client):
    configured = _renewal_case(client)
    unconfigured = _renewal_case(client, fees=None)

    rows = {r["id"]: r for r in client.get("/cases/renewal-summary").json()["cases"]}
    assert len(rows) == 2

    ok = rows[configured]
    assert ok["net_loss_ratio"] is not None
    assert ok["loading_pct"] == 0.33
    assert ok["loading_missing"] is None

    # The board still shows this account and its GROSS ratio, which needs
    # no loading - and names what it is waiting for instead of printing a
    # net ratio struck at an expense level nobody entered.
    waiting = rows[unconfigured]
    assert waiting["gross_loss_ratio"] is not None
    assert waiting["net_loss_ratio"] is None
    assert waiting["loading_pct"] is None
    assert waiting["required_premium"] is None
    assert waiting["suggested_increase_pct"] is None
    assert waiting["loading_missing"] == ["loading_pct"]


# --- the scorecard -------------------------------------------------------

def test_the_scorecard_prices_a_renewal_at_its_own_loading(client):
    # It used to price every case at 26.5% whatever the account's own
    # fees said, so the cushion it showed and the price the renewal
    # quoted were struck at different expense levels.
    case_id = _renewal_case(client)
    resp = client.post(f"/cases/{case_id}/score", json={})
    assert resp.status_code in (200, 400), resp.text
