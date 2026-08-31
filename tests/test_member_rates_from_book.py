""""0 of 178 members rated" beside a case record showing millions -
the rates exist, they are just on the book rather than on the case.
"""
from datetime import date


def _case_with_census(client, refs):
    from app.models import db_models as models

    db = client.db_session_local()
    case = models.Case(broker_name="B", company_name="Safran", industry="aviation",
                       business_type="existing", current_annual_premium=3_235_630.0)
    db.add(case)
    db.commit()
    db.refresh(case)
    db.add_all([models.CensusRecord(case_id=case.id, employee_ref=r, category="A",
                                    age=35, gender="M", relation="employee") for r in refs])
    db.commit()
    case_id = case.id
    db.close()
    return case_id


def _book(client, rates, policy_end=date(2026, 9, 30)):
    from app.models import db_models as models

    db = client.db_session_local()
    db.bulk_insert_mappings(models.PortfolioMember, [
        {"beneficiary_id": ref, "contract": "Safran", "master_contract": "Safran",
         "gross_premium": rate, "actual_gross_premium": rate,
         "policy_start_date": date(2025, 10, 1), "policy_end_date": policy_end,
         "member_start_date": date(2025, 10, 1), "member_end_date": policy_end}
        for ref, rate in rates.items()])
    db.commit()
    db.close()


def test_rates_are_filled_from_the_book(client):
    case_id = _case_with_census(client, ["S1", "S2", "S3"])
    _book(client, {"S1": 17_000.0, "S2": 18_000.0, "S3": 19_000.0})

    body = client.post(f"/cases/{case_id}/member-rates/from-book").json()
    assert body["filled_from_book"] == 3
    assert body["unmatched_count"] == 0


def test_a_rate_already_on_the_case_is_not_overwritten_by_the_book(client):
    # An underwriter's typed rate outranks the book's. Anything else
    # makes the button destructive, and a destructive button gets pressed.
    from app.models import db_models as models

    case_id = _case_with_census(client, ["S1", "S2"])
    _book(client, {"S1": 17_000.0, "S2": 18_000.0})
    db = client.db_session_local()
    record = db.query(models.CensusRecord).filter_by(case_id=case_id, employee_ref="S1").one()
    record.existing_annual_rate = 25_000.0
    db.commit()
    db.close()

    body = client.post(f"/cases/{case_id}/member-rates/from-book").json()
    assert body["filled_from_book"] == 1
    assert body["already_rated"] == 1
    rates = {m["employee_ref"]: m["existing_annual_rate"] for m in body["members"]}
    assert rates["S1"] == 25_000.0


def test_running_it_twice_changes_nothing_the_second_time(client):
    case_id = _case_with_census(client, ["S1", "S2"])
    _book(client, {"S1": 17_000.0, "S2": 18_000.0})
    assert client.post(f"/cases/{case_id}/member-rates/from-book").json()["filled_from_book"] == 2
    second = client.post(f"/cases/{case_id}/member-rates/from-book").json()
    assert second["filled_from_book"] == 0
    assert second["already_rated"] == 2


def test_a_member_the_book_has_never_heard_of_is_named_not_shrugged_past(client):
    # A census/roster mismatch is worth looking at. A count alone tells
    # you there is a problem without telling you where.
    case_id = _case_with_census(client, ["S1", "GHOST"])
    _book(client, {"S1": 17_000.0})

    body = client.post(f"/cases/{case_id}/member-rates/from-book").json()
    assert body["filled_from_book"] == 1
    assert body["unmatched_count"] == 1
    assert body["unmatched"][0]["employee_ref"] == "GHOST"


def test_the_renewing_terms_rate_wins_when_a_member_has_several(client):
    # A member appears once per policy year. The renewal is priced off
    # the term being renewed, so the latest policy end is the rate.
    from app.models import db_models as models

    case_id = _case_with_census(client, ["S1"])
    _book(client, {"S1": 12_000.0}, policy_end=date(2025, 9, 30))
    db = client.db_session_local()
    db.bulk_insert_mappings(models.PortfolioMember, [
        {"beneficiary_id": "S1", "contract": "Safran", "gross_premium": 17_000.0,
         "actual_gross_premium": 17_000.0, "policy_start_date": date(2025, 10, 1),
         "policy_end_date": date(2026, 9, 30), "member_start_date": date(2025, 10, 1),
         "member_end_date": date(2026, 9, 30)}])
    db.commit()
    db.close()

    body = client.post(f"/cases/{case_id}/member-rates/from-book").json()
    rates = {m["employee_ref"]: m["existing_annual_rate"] for m in body["members"]}
    assert rates["S1"] == 17_000.0


def test_a_case_with_no_census_says_so(client):
    from app.models import db_models as models

    db = client.db_session_local()
    case = models.Case(broker_name="B", company_name="Empty", industry="x")
    db.add(case)
    db.commit()
    db.refresh(case)
    case_id = case.id
    db.close()

    resp = client.post(f"/cases/{case_id}/member-rates/from-book")
    assert resp.status_code == 400
    assert "census" in resp.json()["detail"].lower()


# --- when the import fails, say why --------------------------------------

def _xlsx_of(rows):
    import io

    import pandas as pd

    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False)
    buf.seek(0)
    return buf


def test_a_sheet_that_is_not_a_rate_card_names_what_it_found(client):
    # "Import failed" with the cause in a toast that fades is a dead end.
    # The message has to say which layout was expected and what the sheet
    # actually contained.
    case_id = client.post("/cases", json={
        "broker_name": "B", "company_name": "C", "industry": "trading"}).json()["id"]
    resp = client.post(
        f"/cases/{case_id}/member-rates/import-rate-card",
        files={"file": ("census.xlsx", _xlsx_of([{"Employee": "A", "DOB": "1990-01-01"}]),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Category, Age Band and Gross Premium" in detail
    assert "Employee" in detail, "the message must quote what the sheet actually held"


def test_a_file_pandas_cannot_open_is_a_400_not_a_500(client):
    case_id = client.post("/cases", json={
        "broker_name": "B", "company_name": "C", "industry": "trading"}).json()["id"]
    resp = client.post(
        f"/cases/{case_id}/member-rates/import-rate-card",
        files={"file": ("broken.xlsx", b"this is not a workbook", "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "Could not read that workbook" in resp.json()["detail"]
