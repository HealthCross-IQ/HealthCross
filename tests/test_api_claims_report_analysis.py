import io
from datetime import date

import pandas as pd

from app.api import routes_cases
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


def test_claims_projection_loading_override_via_query_param(client):
    case_id = _create_case_with_census(client, member_count=212)
    _insert_claims_report(client, case_id)

    default_resp = client.get(f"/cases/{case_id}/claims-projection")
    higher_loading_resp = client.get(f"/cases/{case_id}/claims-projection", params={"loading_pct": 0.40})

    assert higher_loading_resp.json()["assumptions_used"]["loading_pct"] == 0.40
    # Dividing by (1 - loading) - a higher loading means a higher required
    # premium to cover the same underlying claims.
    assert higher_loading_resp.json()["final_projected_claims"] > default_resp.json()["final_projected_claims"]


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


def _fake_parsed_report(period_start: str, period_end: str, total_paid: float, monthly_paid=None) -> dict:
    return {
        "policy_number": "607836-001",
        "policy_effective_date": date.fromisoformat(period_start),
        "policy_expiry_date": date.fromisoformat(period_end),
        "report_period_start": date.fromisoformat(period_start),
        "report_period_end": date.fromisoformat(period_end),
        "report_production_date": date.fromisoformat(period_end),
        "total_paid": total_paid,
        "incurred_not_reported": 10_000.0,
        "opening_members": 100,
        "closing_members": 110,
        "diagnosis_breakdown": [],
        "provider_breakdown": [],
        "claims_by_type": [],
        "treatment_type_breakdown": [],
        "claims_by_member_type_value": [],
        "claims_by_member_type_count": [],
        "monthly_paid": monthly_paid or [],
    }


def _upload_fake_claims_report(client, monkeypatch, case_id, period_start, period_end, total_paid, filename="report.pdf", monthly_paid=None):
    monkeypatch.setattr(
        routes_cases, "parse_claims_report",
        lambda file, name: _fake_parsed_report(period_start, period_end, total_paid, monthly_paid),
    )
    return client.post(
        f"/cases/{case_id}/claims",
        files={"file": (filename, b"%PDF-1.4 fake", "application/pdf")},
    )


def _fake_parsed_report_no_period(total_paid: float) -> dict:
    # Simulates a report whose date format the parser didn't recognize -
    # every other field still parses fine, but report_period_start stays
    # None, same as a real parsing gap would leave it.
    report = _fake_parsed_report("2025-08-29", "2026-08-28", total_paid)
    report["report_period_start"] = None
    report["report_period_end"] = None
    return report


def _upload_fake_claims_report_no_period(client, monkeypatch, case_id, total_paid, filename="report.pdf"):
    monkeypatch.setattr(
        routes_cases, "parse_claims_report",
        lambda file, name: _fake_parsed_report_no_period(total_paid),
    )
    return client.post(
        f"/cases/{case_id}/claims",
        files={"file": (filename, b"%PDF-1.4 fake", "application/pdf")},
    )


def test_uploading_a_new_report_period_keeps_the_previous_year(client, monkeypatch):
    case_id = _create_case_with_census(client)
    resp = _upload_fake_claims_report(client, monkeypatch, case_id, "2024-08-29", "2025-08-28", 992_049.0)
    assert resp.status_code == 200
    resp = _upload_fake_claims_report(client, monkeypatch, case_id, "2025-08-29", "2026-08-28", 1_315_830.0)
    assert resp.status_code == 200

    resp = client.get(f"/cases/{case_id}/claims-reports")
    assert resp.status_code == 200
    reports = resp.json()
    assert len(reports) == 2
    assert {r["total_paid"] for r in reports} == {992_049.0, 1_315_830.0}


def test_reuploading_the_same_report_period_replaces_only_that_year(client, monkeypatch):
    case_id = _create_case_with_census(client)
    _upload_fake_claims_report(client, monkeypatch, case_id, "2024-08-29", "2025-08-28", 992_049.0)
    _upload_fake_claims_report(client, monkeypatch, case_id, "2025-08-29", "2026-08-28", 1_315_830.0)
    # A corrected re-issue of the SAME 2025-2026 period.
    _upload_fake_claims_report(client, monkeypatch, case_id, "2025-08-29", "2026-08-28", 1_400_000.0)

    resp = client.get(f"/cases/{case_id}/claims-reports")
    reports = resp.json()
    assert len(reports) == 2
    totals = {r["total_paid"] for r in reports}
    assert totals == {992_049.0, 1_400_000.0}


def test_reuploading_after_a_failed_period_parse_replaces_the_null_period_report(client, monkeypatch):
    # First upload's date format wasn't recognized yet (report_period_start
    # stayed None); a later re-upload of the SAME report, once the parser
    # is fixed, should replace that stale unparsed row rather than
    # piling up next to it.
    case_id = _create_case_with_census(client)
    _upload_fake_claims_report_no_period(client, monkeypatch, case_id, 992_049.0)
    _upload_fake_claims_report(client, monkeypatch, case_id, "2025-08-29", "2026-08-28", 992_049.0)

    resp = client.get(f"/cases/{case_id}/claims-reports")
    reports = resp.json()
    assert len(reports) == 1
    assert reports[0]["report_period_start"] == "2025-08-29"
    assert reports[0]["total_paid"] == 992_049.0


def test_reuploading_after_a_failed_period_parse_keeps_the_null_report_when_two_years_already_exist(client, monkeypatch):
    # With 2+ real report-years already on file, a null-period row could
    # legitimately be one of THOSE years' own report that just failed to
    # parse - deleting it on an unrelated new upload would lose real
    # history, so it's left alone instead.
    case_id = _create_case_with_census(client)
    _upload_fake_claims_report(client, monkeypatch, case_id, "2023-08-29", "2024-08-28", 800_000.0)
    _upload_fake_claims_report(client, monkeypatch, case_id, "2024-08-29", "2025-08-28", 992_049.0)
    _upload_fake_claims_report_no_period(client, monkeypatch, case_id, 500_000.0)
    _upload_fake_claims_report(client, monkeypatch, case_id, "2025-08-29", "2026-08-28", 1_315_830.0)

    resp = client.get(f"/cases/{case_id}/claims-reports")
    reports = resp.json()
    assert len(reports) == 4
    totals = {r["total_paid"] for r in reports}
    assert totals == {800_000.0, 992_049.0, 500_000.0, 1_315_830.0}


def test_list_claims_reports_sorted_oldest_to_newest(client, monkeypatch):
    case_id = _create_case_with_census(client)
    _upload_fake_claims_report(client, monkeypatch, case_id, "2025-08-29", "2026-08-28", 1_315_830.0)
    _upload_fake_claims_report(client, monkeypatch, case_id, "2024-08-29", "2025-08-28", 992_049.0)

    resp = client.get(f"/cases/{case_id}/claims-reports")
    reports = resp.json()
    assert [r["report_period_start"] for r in reports] == ["2024-08-29", "2025-08-29"]


def test_claims_report_comparison_returns_a_row_per_year(client, monkeypatch):
    case_id = _create_case_with_census(client)
    _upload_fake_claims_report(client, monkeypatch, case_id, "2024-08-29", "2025-08-28", 992_049.0)
    _upload_fake_claims_report(client, monkeypatch, case_id, "2025-08-29", "2026-08-28", 1_315_830.0)

    resp = client.get(f"/cases/{case_id}/claims-report-comparison")
    assert resp.status_code == 200
    body = resp.json()
    years = [(r["year"], r["total_paid"]) for r in body["reports"]]
    assert years == [(2024, 992_049.0), (2025, 1_315_830.0)]


def _six_full_months(total):
    per_month = total / 6
    return [{"year": 2025, "month": m, "paid": per_month, "partial": False} for m in ["Sep", "Oct", "Nov", "Dec", "Jan", "Feb"]]


def test_claims_report_comparison_burning_cost_matches_the_standard_projection_formula(client, monkeypatch):
    from app.scoring.rules.claims_projection import ClaimsProjectionAssumptions

    case_id = _create_case_with_census(client)
    # Both fake reports use opening_members=100/closing_members=110 (see
    # _fake_parsed_report) -> avg_report_members = 105 for each. 6 full
    # months of monthly_paid so the standard avg-month -> annualized ->
    # +IBNR -> / avg population formula (same one the single-year Claims
    # projection card uses) can actually be computed, rather than the
    # simpler (but understating, for a <12-month report) total_paid /
    # avg_members shortcut.
    _upload_fake_claims_report(
        client, monkeypatch, case_id, "2024-08-29", "2025-08-28", 992_049.0, monthly_paid=_six_full_months(600_000.0),
    )
    _upload_fake_claims_report(
        client, monkeypatch, case_id, "2025-08-29", "2026-08-28", 1_315_830.0, monthly_paid=_six_full_months(900_000.0),
    )

    resp = client.get(f"/cases/{case_id}/claims-report-comparison")
    assert resp.status_code == 200
    rows = resp.json()["reports"]
    y2024, y2025 = rows[0], rows[1]

    ibnr_pct = ClaimsProjectionAssumptions().ibnr_pct

    def expected_burning_cost(six_month_total):
        avg_month = six_month_total / 6
        with_ibnr = avg_month * 12 * (1 + ibnr_pct)
        return round(with_ibnr / 105, 2)  # avg_report_members = (100 + 110) / 2

    assert y2024["burning_cost_per_member"] == expected_burning_cost(600_000.0)
    assert y2025["burning_cost_per_member"] == expected_burning_cost(900_000.0)

    # The first (oldest) year has nothing earlier to compare against.
    assert y2024["total_paid_pct_change"] is None
    assert y2024["burning_cost_pct_change"] is None

    expected_total_pct = round((1_315_830.0 - 992_049.0) / 992_049.0 * 100, 2)
    assert y2025["total_paid_pct_change"] == expected_total_pct
    # Burning cost moves by the same % as its own 6-month total here since
    # both years share the same average member count and IBNR assumption.
    expected_burning_pct = round((900_000.0 - 600_000.0) / 600_000.0 * 100, 2)
    assert y2025["burning_cost_pct_change"] == expected_burning_pct


def test_claims_report_comparison_burning_cost_is_none_without_six_full_months(client, monkeypatch):
    case_id = _create_case_with_census(client)
    _upload_fake_claims_report(client, monkeypatch, case_id, "2024-08-29", "2025-08-28", 992_049.0)

    resp = client.get(f"/cases/{case_id}/claims-report-comparison")
    assert resp.status_code == 200
    row = resp.json()["reports"][0]
    assert row["burning_cost_per_member"] is None


def test_claims_report_endpoints_accept_report_id_to_pick_a_specific_year(client, monkeypatch):
    case_id = _create_case_with_census(client)
    _upload_fake_claims_report(client, monkeypatch, case_id, "2024-08-29", "2025-08-28", 992_049.0)
    _upload_fake_claims_report(client, monkeypatch, case_id, "2025-08-29", "2026-08-28", 1_315_830.0)

    reports = client.get(f"/cases/{case_id}/claims-reports").json()
    older_report_id = next(r["id"] for r in reports if r["total_paid"] == 992_049.0)

    resp = client.get(f"/cases/{case_id}/claims-report", params={"report_id": older_report_id})
    assert resp.status_code == 200
    assert resp.json()["total_paid"] == 992_049.0

    # Omitting report_id still defaults to the latest (2025-2026) period.
    resp = client.get(f"/cases/{case_id}/claims-report")
    assert resp.json()["total_paid"] == 1_315_830.0


def test_claims_report_id_404s_when_it_belongs_to_a_different_case(client, monkeypatch):
    case_id_a = _create_case_with_census(client)
    case_id_b = _create_case_with_census(client)
    _upload_fake_claims_report(client, monkeypatch, case_id_a, "2025-08-29", "2026-08-28", 1_315_830.0)
    report_id = client.get(f"/cases/{case_id_a}/claims-reports").json()[0]["id"]

    resp = client.get(f"/cases/{case_id_b}/claims-report", params={"report_id": report_id})
    assert resp.status_code == 404


def test_delete_claims_report_removes_it(client, monkeypatch):
    case_id = _create_case_with_census(client)
    _upload_fake_claims_report(client, monkeypatch, case_id, "2024-08-29", "2025-08-28", 992_049.0)
    _upload_fake_claims_report(client, monkeypatch, case_id, "2025-08-29", "2026-08-28", 1_315_830.0)
    reports = client.get(f"/cases/{case_id}/claims-reports").json()
    stale_id = next(r["id"] for r in reports if r["total_paid"] == 992_049.0)

    resp = client.delete(f"/cases/{case_id}/claims-reports/{stale_id}")
    assert resp.status_code == 204

    reports = client.get(f"/cases/{case_id}/claims-reports").json()
    assert len(reports) == 1
    assert reports[0]["total_paid"] == 1_315_830.0


def test_delete_claims_report_404s_for_a_report_belonging_to_a_different_case(client, monkeypatch):
    case_id_a = _create_case_with_census(client)
    case_id_b = _create_case_with_census(client)
    _upload_fake_claims_report(client, monkeypatch, case_id_a, "2025-08-29", "2026-08-28", 1_315_830.0)
    report_id = client.get(f"/cases/{case_id_a}/claims-reports").json()[0]["id"]

    resp = client.delete(f"/cases/{case_id_b}/claims-reports/{report_id}")
    assert resp.status_code == 404


def test_delete_claims_report_404s_for_missing_report(client):
    case_id = _create_case_with_census(client)
    resp = client.delete(f"/cases/{case_id}/claims-reports/999999")
    assert resp.status_code == 404
