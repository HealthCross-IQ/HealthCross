"""GET /cases/{id}/claims-development end to end.

A second reading of an account's own claims, built from how long this
book's claims actually take to be received - not the house's flat
30-day IBNR tail. Interactive rather than a single number: large claims
are flagged, not stripped, and which months feed the projection is an
input, not a decision this endpoint makes for anyone.
"""
from datetime import date

import pytest

from app.models import db_models as models

TERM_START = date(2025, 10, 1)
TERM_END = date(2026, 9, 30)
AS_OF = date(2026, 8, 31)

HOUSE_FEES = {"tpa_fee_pct": 0.065, "commission_pct": 0.15,
              "hc_fee_pct": 0.065, "qic_fee_pct": 0.05}


def _member(bid, contract, gross=6_000.0, actual=5_646.69):
    return {
        "beneficiary_id": bid, "contract": contract, "master_contract": contract,
        "relation": "employee", "age": 35, "gender": "M",
        "policy_start_date": TERM_START, "policy_end_date": TERM_END,
        "member_start_date": TERM_START, "member_end_date": TERM_END,
        "gross_premium": gross, "actual_gross_premium": actual,
    }


def _claim(bid, amount, treated, received=None, status="Paid Claims", claim_id=None):
    return {
        "patient_id": bid, "final_amount": amount, "claim_status": status,
        "date_of_treatment": treated, "date_reception": received,
        "claim_id": claim_id or f"{bid}-{treated.isoformat()}-{amount}",
    }


@pytest.fixture()
def seeded_book(client):
    """Two background cohorts old enough and large enough to build a
    trustworthy completion curve (received within days of treatment, so
    the curve reads close to 100% everywhere - keeps the arithmetic in
    each test predictable), plus TESTCO's own claims: an ordinary month,
    a month with one large claim, and a leaver whose claims should never
    reach the projection at all.
    """
    db = client.db_session_local()
    members = [_member(f"BG{i}", "Background Co") for i in range(5)]
    members += [_member(f"T{i}", "TESTCO") for i in range(5)]
    # A leaver: on the roster, but their own membership window ends well
    # before the term does.
    members.append({
        **_member("T9", "TESTCO"),
        "member_end_date": date(2026, 3, 15),
    })
    db.bulk_insert_mappings(models.PortfolioMember, members)

    claims = []
    # Background cohorts - old (>= 6 months before AS_OF) and each above
    # the AED 10,000 credibility floor, received almost immediately.
    for month, day in ((10, 1), (11, 1)):
        for i in range(5):
            treated = date(2025, month, 10)
            claims.append(_claim(f"BG{i}", 5_000.0, treated, treated))

    # TESTCO's own claims.
    claims.append(_claim("T0", 8_000.0, date(2026, 6, 5), date(2026, 6, 10)))
    claims.append(_claim("T1", 7_000.0, date(2026, 6, 12), date(2026, 6, 18)))
    # A large claim in July - a candidate for exclusion.
    claims.append(_claim(
        "T2", 60_000.0, date(2026, 7, 3), date(2026, 7, 9), claim_id="LARGE-1"))
    claims.append(_claim("T3", 5_000.0, date(2026, 7, 8), date(2026, 7, 14)))
    # The leaver's claim - happened while covered, must never reach the
    # projection regardless of month selection or exclusion toggles.
    claims.append(_claim("T9", 40_000.0, date(2026, 2, 1), date(2026, 2, 5)))

    db.bulk_insert_mappings(models.PortfolioClaimEntry, claims)
    db.add(models.PortfolioDataSnapshot(data_as_of_date=AS_OF))
    db.commit()
    db.close()


def _case(client, company="TESTCO"):
    case_id = client.post("/cases", json={
        "broker_name": "Broker", "company_name": company, "industry": "events",
    }).json()["id"]
    client.patch(f"/cases/{case_id}", json={
        **HOUSE_FEES, "business_type": "existing", "current_annual_premium": 100_000.0,
    })
    return case_id


class TestClaimsDevelopment:
    def test_reports_the_accounts_own_months(self, client, seeded_book):
        case_id = _case(client)
        body = client.get(f"/cases/{case_id}/claims-development").json()

        assert body["insufficient_data"] is False
        months = {row["month"] for row in body["monthly"]}
        assert months == {"2026-06", "2026-07"}
        # The leaver's February claim never shows up at all.
        assert "2026-02" not in months

    def test_the_leaver_is_reported_separately_not_folded_in(self, client, seeded_book):
        case_id = _case(client)
        body = client.get(f"/cases/{case_id}/claims-development").json()

        assert body["leavers"] == [{"patient_id": "T9", "incurred": 40_000.0}]
        total_in_months = sum(row["received"] for row in body["monthly"])
        assert total_in_months == pytest.approx(80_000.0, abs=0.01)  # not 120,000

    def test_a_large_claim_is_flagged_not_stripped_by_default(self, client, seeded_book):
        case_id = _case(client)
        body = client.get(f"/cases/{case_id}/claims-development").json()

        assert "2026-07" in body["large_claims_flagged"]
        flagged = body["large_claims_flagged"]["2026-07"]
        assert [c["claim_id"] for c in flagged] == ["LARGE-1"]
        assert flagged[0]["amount"] == 60_000.0
        # Still counted in the month by default - flagged is not excluded.
        july = next(r for r in body["monthly"] if r["month"] == "2026-07")
        assert july["received"] == pytest.approx(65_000.0, abs=0.01)

    def test_excluding_the_large_claim_removes_it_from_its_month(self, client, seeded_book):
        case_id = _case(client)
        body = client.get(
            f"/cases/{case_id}/claims-development",
            params={"exclude_claim_ids": "LARGE-1"},
        ).json()

        july = next(r for r in body["monthly"] if r["month"] == "2026-07")
        assert july["received"] == pytest.approx(5_000.0, abs=0.01)
        assert body["excluded_claim_ids"] == ["LARGE-1"]

    def test_include_months_controls_the_projection_not_just_the_display(self, client, seeded_book):
        case_id = _case(client)
        everything = client.get(f"/cases/{case_id}/claims-development").json()
        just_june = client.get(
            f"/cases/{case_id}/claims-development",
            params={"include_months": "2026-06"},
        ).json()

        assert everything["projection"]["included_months"] == ["2026-06", "2026-07"]
        assert just_june["projection"]["included_months"] == ["2026-06"]
        assert just_june["projection"]["excluded_months"] == ["2026-07"]
        assert just_june["projection"]["annual_claims"] != everything["projection"]["annual_claims"]

    def test_the_projection_prices_through_to_a_required_premium(self, client, seeded_book):
        case_id = _case(client)
        body = client.get(f"/cases/{case_id}/claims-development").json()

        pricing = body["pricing"]
        assert pricing["annual_claims"] == body["projection"]["annual_claims"]
        assert pricing["required_premium"] is not None
        assert pricing["projected_loss_ratio"] is not None

    def test_a_case_with_no_book_experience_is_a_400(self, client, seeded_book):
        case_id = _case(client, company="Nobody On The Book LLC")
        resp = client.get(f"/cases/{case_id}/claims-development")
        assert resp.status_code == 400

    def test_an_unknown_case_is_a_404(self, client, seeded_book):
        assert client.get("/cases/999999/claims-development").status_code == 404


class TestInsufficientData:
    def test_a_book_with_no_reception_dates_reports_insufficient_data(self, client):
        # A book uploaded before date_reception existed, or before it has
        # ever been re-uploaded since - every claim has the field NULL.
        db = client.db_session_local()
        db.bulk_insert_mappings(models.PortfolioMember, [_member("T0", "TESTCO")])
        db.bulk_insert_mappings(models.PortfolioClaimEntry, [
            {"patient_id": "T0", "final_amount": 5_000.0, "claim_status": "Paid Claims",
             "date_of_treatment": date(2026, 6, 1), "date_reception": None},
        ])
        db.add(models.PortfolioDataSnapshot(data_as_of_date=AS_OF))
        db.commit()
        db.close()

        case_id = _case(client)
        body = client.get(f"/cases/{case_id}/claims-development").json()
        assert body["insufficient_data"] is True
        assert "reason" in body
