"""GET /portfolio-analysis/annual-limit-exposure."""
from datetime import date

from app.models import db_models as models


def _upload_claims(client, rows):
    db = client.db_session_local()
    db.bulk_insert_mappings(models.PortfolioClaimEntry, rows)
    db.commit()
    db.close()


def _claim(patient_id, day, amount, client_name="Acme Holding"):
    return dict(
        patient_id=patient_id,
        date_of_treatment=day,
        final_amount=amount,
        client_name=client_name,
        group_name=client_name,
    )


def test_annual_limit_exposure_reports_breaches_at_each_limit(client):
    _upload_claims(client, [
        _claim("A", date(2025, 3, 1), 1_400_000),
        _claim("B", date(2025, 3, 1), 600_000),
        _claim("C", date(2025, 3, 1), 5_000),
    ])

    resp = client.get("/portfolio-analysis/annual-limit-exposure?limit=500000&limit=1000000")
    assert resp.status_code == 200
    body = resp.json()
    assert body["member_count"] == 3
    at_500k, at_1m = body["rows"]
    assert at_500k["members_above"] == 2
    assert at_1m["members_above"] == 1


def test_annual_limit_exposure_can_name_the_members_a_limit_would_cut_off(client):
    _upload_claims(client, [
        _claim("A", date(2025, 3, 1), 1_400_000),
        _claim("B", date(2025, 3, 1), 5_000),
    ])

    resp = client.get("/portfolio-analysis/annual-limit-exposure?limit=500000&members_above=500000")
    assert resp.status_code == 200
    named = resp.json()["members_above_limit"]
    assert [m["patient_id"] for m in named] == ["A"]
    assert named[0]["above_limit"] == 900_000


def test_annual_limit_exposure_can_be_scoped_to_one_master_client(client):
    _upload_claims(client, [
        _claim("A", date(2025, 3, 1), 1_400_000, client_name="Acme Holding"),
        _claim("B", date(2025, 3, 1), 1_400_000, client_name="Other Client"),
    ])

    resp = client.get("/portfolio-analysis/annual-limit-exposure?limit=500000&master_client=Acme Holding")
    assert resp.status_code == 200
    assert resp.json()["rows"][0]["members_above"] == 1


def test_annual_limit_exposure_says_so_when_no_claims_are_uploaded(client):
    resp = client.get("/portfolio-analysis/annual-limit-exposure")
    assert resp.status_code == 400
    assert "No claims uploaded" in resp.json()["detail"]


# --- the same figure, asked of a quote's own limits ----------------------

def test_case_annual_limit_exposure_reads_the_limit_off_the_quote(client):
    from app.models import db_models as models

    _upload_claims(client, [
        _claim("A", date(2025, 3, 1), 5_000_000),
        _claim("B", date(2025, 3, 1), 5_000),
    ])
    case_id = client.post(
        "/cases", json={"broker_name": "B", "company_name": "C", "industry": "hotel"}
    ).json()["id"]

    db = client.db_session_local()
    db.add(models.NewBusinessQuote(
        case_id=case_id,
        categories=[{
            "category": "A", "product": "Bronze", "network": "Net A", "tpa": "T",
            "commission_pct": None, "variant_selections": {"Annual Limit": "AED 1,000,000"},
        }],
        case_gross_annual_premium=0.0,
        result={"categories": []},
    ))
    db.commit()
    db.close()

    resp = client.get(f"/cases/{case_id}/annual-limit-exposure")
    assert resp.status_code == 200
    body = resp.json()
    assert [c["category"] for c in body["categories"]] == ["A"]
    assert body["categories"][0]["limit_aed"] == 1_000_000
    assert body["categories"][0]["members_above"] == 1
    assert body["categories"][0]["spend_above_limit"] == 4_000_000


def test_case_annual_limit_exposure_404s_without_a_quote(client):
    case_id = client.post(
        "/cases", json={"broker_name": "B", "company_name": "C", "industry": "hotel"}
    ).json()["id"]
    assert client.get(f"/cases/{case_id}/annual-limit-exposure").status_code == 404
