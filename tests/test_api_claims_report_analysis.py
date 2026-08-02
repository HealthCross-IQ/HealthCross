import io

import pandas as pd

from app.models import db_models as models
from app.scoring.rules.benefits_summary import STANDARD_FIELDS


def _xlsx_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    buf.seek(0)
    return buf.read()


def _create_case_with_census(client, member_count=212):
    resp = client.post(
        "/cases",
        json={"broker_name": "Broker A", "company_name": "LEGRAND SNC FZE", "industry": "trading"},
    )
    case_id = resp.json()["id"]

    rows = [
        {"Category": "D", "Gender": "M", "DOB": "1994-02-15", "Marital Status": "Single", "Relation": "Employee", "Nationality": "Indian"}
        for _ in range(member_count)
    ]
    census_df = pd.DataFrame(rows)
    resp = client.post(
        f"/cases/{case_id}/census",
        files={"file": ("census.xlsx", _xlsx_bytes(census_df), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    return case_id


def _insert_claims_report(client, case_id, **overrides):
    db = client.db_session_local()
    defaults = dict(
        case_id=case_id,
        policy_number="54773",
        opening_members=161,
        closing_members=227,
        total_paid=1_772_027.0,
        incurred_not_reported=421_387.0,
        diagnosis_breakdown=[
            {"label": "NEOPLASMS", "value": 381126.0, "count": 52, "ip_value": 346113.0, "ip_count": 6},
            {"label": "DENTAL/ORAL DISEASES", "value": 128613.0, "count": 146, "ip_value": 0.0, "ip_count": 0},
            {"label": "Pregnancy, Childbirth And The Puerperium", "value": 90000.0, "count": 12, "ip_value": 70000.0, "ip_count": 8},
        ],
        provider_breakdown=[
            {"provider": f"PROVIDER {i}", "value": float(1000 * (12 - i))} for i in range(12)
        ],
        treatment_type_breakdown=[
            {"type": "In-Patient", "value": 526709.0},
            {"type": "Out-Patient", "value": 740105.0},
            {"type": "Pharmacy", "value": 303065.0},
            {"type": "Dental", "value": 113401.0},
            {"type": "Optical", "value": 80272.0},
            {"type": "Not Yet Classified", "value": 8475.0},
        ],
        claims_by_member_type_value=[
            {"relation": "Employee", "in_patient": 99786.0, "out_patient": 331844.0, "pharmacy": 122566.0, "dental": 56008.0, "optical": 39920.0, "not_yet_classified": 3741.0, "total": 653865.0},
            {"relation": "Spouse", "in_patient": 382229.0, "out_patient": 289781.0, "pharmacy": 121788.0, "dental": 25231.0, "optical": 16364.0, "not_yet_classified": 907.0, "total": 836299.0},
            {"relation": "Dependents", "in_patient": 44695.0, "out_patient": 118480.0, "pharmacy": 58711.0, "dental": 32162.0, "optical": 23989.0, "not_yet_classified": 3826.0, "total": 281863.0},
        ],
        monthly_paid=[
            {"year": 2025, "month": "Sep", "paid": 8870.0, "partial": True},
            {"year": 2025, "month": "Oct", "paid": 203861.0, "partial": False},
            {"year": 2025, "month": "Nov", "paid": 216391.0, "partial": False},
            {"year": 2025, "month": "Dec", "paid": 175170.0, "partial": False},
            {"year": 2026, "month": "Jan", "paid": 502079.0, "partial": False},
            {"year": 2026, "month": "Feb", "paid": 157146.0, "partial": False},
            {"year": 2026, "month": "Mar", "paid": 155289.0, "partial": False},
        ],
    )
    defaults.update(overrides)
    report = models.ClaimsReport(**defaults)
    db.add(report)
    db.commit()
    db.refresh(report)
    db.close()
    return report


def test_get_claims_report_returns_the_latest_report(client):
    case_id = _create_case_with_census(client)
    _insert_claims_report(client, case_id)

    resp = client.get(f"/cases/{case_id}/claims-report")
    assert resp.status_code == 200
    body = resp.json()
    assert body["opening_members"] == 161
    assert body["closing_members"] == 227
    assert body["total_paid"] == 1_772_027.0


def test_get_claims_report_404_when_none_uploaded(client):
    case_id = _create_case_with_census(client)
    resp = client.get(f"/cases/{case_id}/claims-report")
    assert resp.status_code == 404


def test_claims_projection_matches_the_hand_worked_example(client):
    case_id = _create_case_with_census(client, member_count=212)
    _insert_claims_report(client, case_id)

    resp = client.get(f"/cases/{case_id}/claims-projection")
    assert resp.status_code == 200
    body = resp.json()

    assert body["months_used"] == ["Oct 2025", "Nov 2025", "Dec 2025", "Jan 2026", "Feb 2026", "Mar 2026"]
    assert round(body["final_projected_claims"]) == 4554856
    assert body["opening_members"] == 161
    assert body["closing_members"] == 227


def test_claims_projection_credibility_override_via_query_param(client):
    case_id = _create_case_with_census(client, member_count=212)
    _insert_claims_report(client, case_id)

    default_resp = client.get(f"/cases/{case_id}/claims-projection")
    lower_credibility_resp = client.get(f"/cases/{case_id}/claims-projection", params={"credibility_pct": 0.5})

    assert lower_credibility_resp.json()["assumptions_used"]["credibility_pct"] == 0.5
    assert lower_credibility_resp.json()["final_projected_claims"] < default_resp.json()["final_projected_claims"]


def test_diagnosis_exposure_flags_cancer_as_chronic_and_high_exposure(client):
    case_id = _create_case_with_census(client)
    _insert_claims_report(client, case_id)

    resp = client.get(f"/cases/{case_id}/diagnosis-exposure")
    assert resp.status_code == 200
    rows = resp.json()

    neoplasms = next(r for r in rows if r["label"] == "NEOPLASMS")
    assert neoplasms["classification"] == "chronic"
    assert neoplasms["high_exposure"] is True
    assert "possible_large_or_shock_claim" in neoplasms["flags"]

    dental = next(r for r in rows if r["label"] == "DENTAL/ORAL DISEASES")
    assert dental["classification"] == "non_chronic"
    assert dental["high_exposure"] is False

    # sorted by value descending
    assert rows[0]["label"] == "NEOPLASMS"


def test_claims_report_breakdown_top_providers_and_treatment_types(client):
    case_id = _create_case_with_census(client, member_count=212)
    _insert_claims_report(client, case_id)

    resp = client.get(f"/cases/{case_id}/claims-report-breakdown")
    assert resp.status_code == 200
    body = resp.json()

    assert len(body["top_providers"]) == 10
    assert body["top_providers"][0]["provider"] == "PROVIDER 0"
    assert body["top_providers"][0]["value"] == 12000.0
    assert body["top_providers"][0]["pct_of_total"] == round(100 * 12000.0 / 1_772_027.0, 1)

    types = {row["type"]: row for row in body["treatment_type_breakdown"]}
    assert types["In-Patient"]["value"] == 526709.0
    assert types["In-Patient"]["pct_of_total"] == round(100 * 526709.0 / 1_772_027.0, 1)
    # every category's own % share adds up to the whole report (row 14
    # partitions the full total_paid, see app/ingestion/claims_report.py)
    assert round(sum(row["pct_of_total"] for row in body["treatment_type_breakdown"])) == 100

    assert body["maternity"]["label"] == "Pregnancy, Childbirth And The Puerperium"
    assert body["maternity"]["value"] == 90000.0
    assert body["maternity"]["pct_of_total"] == round(100 * 90000.0 / 1_772_027.0, 1)

    # enough months/census/members here to also run the projection and
    # annualize each category by its % share
    assert "final_projected_claims" in body
    total_annualized = sum(row["annualized"] for row in body["treatment_type_breakdown"])
    assert round(total_annualized) == round(body["final_projected_claims"])
    assert body["maternity"]["annualized"] > 0


def test_claims_report_breakdown_member_type_split_with_burning_cost_per_relation(client):
    resp = client.post(
        "/cases",
        json={"broker_name": "Broker A", "company_name": "Mixed Relation Co", "industry": "trading"},
    )
    case_id = resp.json()["id"]

    # 100 employees, 60 spouses, 52 children/other - proportioned so the
    # burning-cost-per-member math below is easy to hand-check.
    rows = (
        [{"Category": "D", "Gender": "M", "DOB": "1990-01-01", "Marital Status": "Married", "Relation": "Employee", "Nationality": "Indian"} for _ in range(100)]
        + [{"Category": "D", "Gender": "F", "DOB": "1990-01-01", "Marital Status": "Married", "Relation": "Spouse", "Nationality": "Indian"} for _ in range(60)]
        + [{"Category": "D", "Gender": "M", "DOB": "2015-01-01", "Marital Status": "Single", "Relation": "Child", "Nationality": "Indian"} for _ in range(52)]
    )
    census_df = pd.DataFrame(rows)
    resp = client.post(
        f"/cases/{case_id}/census",
        files={"file": ("census.xlsx", _xlsx_bytes(census_df), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200

    _insert_claims_report(client, case_id, opening_members=161, closing_members=212)

    resp = client.get(f"/cases/{case_id}/claims-report-breakdown")
    assert resp.status_code == 200
    body = resp.json()

    rows_by_relation = {r["relation"]: r for r in body["claims_by_member_type"]}
    assert set(rows_by_relation) == {"Employee", "Spouse", "Dependents"}

    employee = rows_by_relation["Employee"]
    assert employee["member_count"] == 100
    # Employee's own share of the In-Patient column total (99,786 of
    # 526,709 - the same 3 relations' in_patient values summed).
    assert employee["pct_of_column"]["in_patient"] == round(100 * 99786.0 / 526709.0, 1)

    dependents = rows_by_relation["Dependents"]
    assert dependents["member_count"] == 52  # Child -> folded into "Dependents"

    spouse = rows_by_relation["Spouse"]
    assert spouse["member_count"] == 60

    # Enough months/census/members here to also annualize and divide by
    # each relation's own member count.
    assert "final_projected_claims" in body
    for entry in body["claims_by_member_type"]:
        assert entry["annualized_total"] > 0
        assert entry["burning_cost_per_member"] == round(entry["annualized_total"] / entry["member_count"], 2)

    # The three relations' annualized totals add up to the overall
    # projected annual claims figure (they partition the same total_paid).
    total_annualized = sum(e["annualized_total"] for e in body["claims_by_member_type"])
    assert round(total_annualized) == round(body["final_projected_claims"])


def test_burning_cost_per_member_is_zero_not_none_for_a_relation_with_no_claims(client):
    # A relation that genuinely filed zero claims this period (annualized
    # total == 0.0) still has real members - burning cost per member should
    # report 0.0, not silently disappear as if it couldn't be computed at
    # all (a falsy-but-valid 0.0 must not be treated the same as "missing").
    resp = client.post(
        "/cases",
        json={"broker_name": "Broker A", "company_name": "Zero Claims Co", "industry": "trading"},
    )
    case_id = resp.json()["id"]

    rows = (
        [{"Category": "D", "Gender": "M", "DOB": "1990-01-01", "Marital Status": "Married", "Relation": "Employee", "Nationality": "Indian"} for _ in range(100)]
        + [{"Category": "D", "Gender": "F", "DOB": "1990-01-01", "Marital Status": "Married", "Relation": "Spouse", "Nationality": "Indian"} for _ in range(60)]
        + [{"Category": "D", "Gender": "M", "DOB": "2015-01-01", "Marital Status": "Single", "Relation": "Child", "Nationality": "Indian"} for _ in range(52)]
    )
    census_df = pd.DataFrame(rows)
    resp = client.post(
        f"/cases/{case_id}/census",
        files={"file": ("census.xlsx", _xlsx_bytes(census_df), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200

    _insert_claims_report(
        client,
        case_id,
        opening_members=161,
        closing_members=212,
        claims_by_member_type_value=[
            {"relation": "Employee", "in_patient": 99786.0, "out_patient": 331844.0, "pharmacy": 122566.0, "dental": 56008.0, "optical": 39920.0, "not_yet_classified": 3741.0, "total": 653865.0},
            {"relation": "Spouse", "in_patient": 382229.0, "out_patient": 289781.0, "pharmacy": 121788.0, "dental": 25231.0, "optical": 16364.0, "not_yet_classified": 907.0, "total": 836299.0},
            # Dependents filed nothing at all this period.
            {"relation": "Dependents", "in_patient": 0.0, "out_patient": 0.0, "pharmacy": 0.0, "dental": 0.0, "optical": 0.0, "not_yet_classified": 0.0, "total": 0.0},
        ],
    )

    resp = client.get(f"/cases/{case_id}/claims-report-breakdown")
    assert resp.status_code == 200
    body = resp.json()

    dependents = next(r for r in body["claims_by_member_type"] if r["relation"] == "Dependents")
    assert dependents["member_count"] == 52
    assert dependents["annualized_total"] == 0.0
    assert dependents["burning_cost_per_member"] == 0.0


def test_claims_report_breakdown_without_enough_months_skips_annualization(client):
    case_id = _create_case_with_census(client, member_count=212)
    _insert_claims_report(
        client,
        case_id,
        monthly_paid=[{"year": 2025, "month": "Oct", "paid": 203861.0, "partial": False}],
    )

    resp = client.get(f"/cases/{case_id}/claims-report-breakdown")
    assert resp.status_code == 200
    body = resp.json()
    assert "final_projected_claims" not in body
    assert all("annualized" not in row for row in body["treatment_type_breakdown"])


def test_benefits_summary_uses_standard_fields(client):
    resp = client.post(
        "/cases",
        json={"broker_name": "Broker A", "company_name": "Widgets LLC", "industry": "trading"},
    )
    case_id = resp.json()["id"]

    benefits_df = pd.DataFrame(
        [
            {
                "Plan": "Standard",
                "Annual Limit": 250000,
                "Room & Board": "Semi-Private",
                "Deductible": 100,
                "Coinsurance %": 90,
                "Area of Cover": "In-Country",
                "Maternity Cover": "Yes",
                "Members": 3,
            }
        ]
    )
    resp = client.post(
        f"/cases/{case_id}/benefits",
        files={"file": ("tob.xlsx", _xlsx_bytes(benefits_df), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200

    resp = client.get(f"/cases/{case_id}/benefits-summary")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert set(body[0]["summary"].keys()) == set(STANDARD_FIELDS)
