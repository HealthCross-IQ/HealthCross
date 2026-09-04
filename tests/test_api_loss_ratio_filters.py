"""GET /portfolio-analysis/account-loss-ratio's status (active/expired)
and group_by (master_client/client) filters.
"""
from datetime import date

import pytest

from app.models import db_models as models


def _member(bid, contract, master_contract, policy_start, policy_end,
            gross=100_000.0, actual=100_000.0):
    return {
        "beneficiary_id": bid, "contract": contract, "master_contract": master_contract,
        "relation": "employee", "age": 35, "gender": "M",
        "policy_start_date": policy_start, "policy_end_date": policy_end,
        "member_start_date": policy_start, "member_end_date": policy_end,
        "gross_premium": gross, "actual_gross_premium": actual,
    }


@pytest.fixture()
def seeded(client):
    db = client.db_session_local()
    db.bulk_insert_mappings(models.PortfolioMember, [
        # ACME GROUP: two subgroups, both on a currently-open policy.
        _member("A1", "ACME DXB", "ACME GROUP", date(2026, 1, 1), date(2027, 1, 1)),
        _member("A2", "ACME AUH", "ACME GROUP", date(2026, 1, 1), date(2027, 1, 1)),
        # ZETA CO: a policy that has already fully run its term.
        _member("Z1", "ZETA CO", "ZETA CO", date(2024, 1, 1), date(2025, 1, 1)),
    ])
    db.add(models.PortfolioDataSnapshot(data_as_of_date=date(2026, 6, 1)))
    db.commit()
    db.close()


class TestStatusFilter:
    def test_no_status_returns_both_active_and_expired(self, client, seeded):
        r = client.get("/portfolio-analysis/account-loss-ratio").json()
        names = {row["master_client"] for row in r["rows"]}
        assert names == {"ACME GROUP", "ZETA CO"}

    def test_active_only_excludes_the_expired_policy(self, client, seeded):
        r = client.get("/portfolio-analysis/account-loss-ratio", params={"status": "active"}).json()
        names = {row["master_client"] for row in r["rows"]}
        assert names == {"ACME GROUP"}
        assert all(row["expired"] is False for row in r["rows"])

    def test_expired_only_excludes_the_active_policy(self, client, seeded):
        r = client.get("/portfolio-analysis/account-loss-ratio", params={"status": "expired"}).json()
        names = {row["master_client"] for row in r["rows"]}
        assert names == {"ZETA CO"}
        assert all(row["expired"] is True for row in r["rows"])

    def test_an_unknown_status_is_a_400(self, client, seeded):
        resp = client.get("/portfolio-analysis/account-loss-ratio", params={"status": "sideways"})
        assert resp.status_code == 400


class TestGroupByFilter:
    def test_default_combines_the_subgroups_into_one_row(self, client, seeded):
        r = client.get("/portfolio-analysis/account-loss-ratio").json()
        assert r["group_by"] == "master_client"
        acme_rows = [row for row in r["rows"] if row["master_client"] == "ACME GROUP"]
        assert len(acme_rows) == 1
        assert acme_rows[0]["member_count"] == 2

    def test_client_breaks_the_same_book_out_by_subgroup(self, client, seeded):
        r = client.get("/portfolio-analysis/account-loss-ratio", params={"group_by": "client"}).json()
        names = {row["master_client"] for row in r["rows"]}
        assert {"ACME DXB", "ACME AUH"} <= names
        acme_rows = [row for row in r["rows"] if row["master_client"] in ("ACME DXB", "ACME AUH")]
        assert all(row["member_count"] == 1 for row in acme_rows)

    def test_an_unknown_group_by_is_a_400(self, client, seeded):
        resp = client.get("/portfolio-analysis/account-loss-ratio", params={"group_by": "nonsense"})
        assert resp.status_code == 400

    def test_group_by_client_is_refused_on_the_calendar_basis(self, client, seeded):
        resp = client.get("/portfolio-analysis/account-loss-ratio",
                          params={"group_by": "client", "year_basis": "calendar"})
        assert resp.status_code == 400
