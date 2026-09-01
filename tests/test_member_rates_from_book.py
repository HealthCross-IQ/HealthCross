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
    assert "a category column, an age-band column and a premium column" in detail
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


# --- the layouts an insurer's rate card actually arrives in --------------

def _premium_summary_sheet(rows):
    return [["Category", "Age Band", "Gross Premium"]] + rows


def test_the_rate_grid_is_found_on_a_later_sheet(client):
    # A quote workbook puts the benefits on the front tab and the premium
    # grid behind it. Reading only sheet one reported "no rate table" on
    # a file that plainly contains one.
    import io

    import pandas as pd

    from app.ingestion.premium_summary_rate_card import parse_premium_summary_rate_card

    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as writer:
        pd.DataFrame([{"Category": "A", "Emirates": "AUH", "TPA": "MSH MENA"}]).to_excel(
            writer, sheet_name="Benefits", index=False)
        pd.DataFrame(_premium_summary_sheet([
            ["Category A - Male", "0-17", 7933],
            ["Category A - Female", "18-40", 11865],
        ])).to_excel(writer, sheet_name="Premium Summary", index=False, header=False)
    buf.seek(0)

    rates = parse_premium_summary_rate_card(buf)["rates"]
    assert len(rates) == 2
    assert rates[0] == {"category": "A", "gender": "M", "age_low": 0, "age_high": 17,
                        "premium": 7933.0}


def test_category_and_gender_are_read_however_the_insurer_spells_them():
    # "Category A Male" was the only accepted spelling, so the commoner
    # "Category A - Male" parsed as nothing and the grid came back empty.
    from app.ingestion.premium_summary_rate_card import _CATEGORY_GENDER_RE

    for text, category, gender in [
        ("Category A Male", "A", "male"),
        ("Category A - Male", "A", "male"),
        ("Cat A - Female", "A", "female"),
        ("CATEGORY B – MALE", "B", "male"),
        ("Category C (Female)", "C", "female"),
        ("Category D: Female", "D", "female"),
        ("A - Male", "A", "male"),
    ]:
        match = _CATEGORY_GENDER_RE.search(text)
        assert match, f"{text!r} should parse"
        assert match.group(1).upper() == category
        assert match.group(2).lower() == gender


def test_a_row_naming_no_gender_is_not_treated_as_a_rate():
    from app.ingestion.premium_summary_rate_card import _CATEGORY_GENDER_RE

    assert _CATEGORY_GENDER_RE.search("Category A") is None
    assert _CATEGORY_GENDER_RE.search("Total") is None


def test_the_failure_names_every_sheet_it_looked_at(client):
    import io

    import pandas as pd

    case_id = client.post("/cases", json={
        "broker_name": "B", "company_name": "C", "industry": "trading"}).json()["id"]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as writer:
        pd.DataFrame([{"Category": "A", "Network": "MSH Platinum"}]).to_excel(
            writer, sheet_name="Benefits", index=False)
        pd.DataFrame([{"Member": "X"}]).to_excel(writer, sheet_name="Census", index=False)
    buf.seek(0)
    resp = client.post(
        f"/cases/{case_id}/member-rates/import-rate-card",
        files={"file": ("quote.xlsx", buf,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Benefits" in detail and "Census" in detail, "name the sheets that were searched"


def test_the_grid_is_found_under_the_words_insurers_actually_use():
    # Only "Category"/"Age Band"/"Gross Premium" were accepted. The same
    # grid written "Age Group"/"Annual Premium" reported no rate table at
    # all - and on MPH's card it sits below a page of benefits on a
    # single sheet named "Plan Details", so the first rows never showed it.
    import io

    import pandas as pd

    from app.ingestion.premium_summary_rate_card import parse_premium_summary_rate_card

    buf = io.BytesIO()
    pd.DataFrame([
        ["Category", "Emirates", "TPA", "Network", "Zone", "Product"],
        ["A", "AUH", "MSH MENA", "MSH Platinum", "Worldwide Excluding USA", "Silver"],
        ["Pre-existing & Chronic conditions", "Covered up to policy limit"],
        [None, None],
        ["Category", "Age Group", "Annual Premium"],
        ["Category A - Male", "0-17", 7933],
        ["Category A - Female", "18-40", 11865],
    ]).to_excel(buf, sheet_name="Plan Details", index=False, header=False)
    buf.seek(0)

    rates = parse_premium_summary_rate_card(buf)["rates"]
    assert len(rates) == 2
    assert rates[0]["premium"] == 7933.0


def test_a_benefits_sheet_with_a_stray_premium_word_is_not_mistaken_for_a_grid():
    # All three columns must appear on one row, which is what keeps the
    # widened labels from matching a benefits page.
    import io

    import pandas as pd

    from app.ingestion.premium_summary_rate_card import parse_premium_summary_rate_card

    buf = io.BytesIO()
    pd.DataFrame([
        ["Benefit", "Premium"],
        ["Pre-existing & Chronic conditions", "Covered up to policy limit"],
    ]).to_excel(buf, index=False, header=False)
    buf.seek(0)

    try:
        parse_premium_summary_rate_card(buf)
        raise AssertionError("a benefits sheet must not parse as a rate grid")
    except ValueError as e:
        assert "Could not find the rate table" in str(e)


def test_the_failure_reports_rows_that_mention_a_premium_or_an_age():
    # Five rows off the top is useless on a sheet whose grid sits below a
    # page of benefits; the message has to say where to look.
    import io

    import pandas as pd

    from app.ingestion.premium_summary_rate_card import parse_premium_summary_rate_card

    buf = io.BytesIO()
    pd.DataFrame([
        ["Plan", "Network"],
        ["Silver", "MSH Platinum"],
        [None, None],
        ["Age Group", "Something Else"],
    ]).to_excel(buf, index=False, header=False)
    buf.seek(0)

    try:
        parse_premium_summary_rate_card(buf)
        raise AssertionError("should not parse")
    except ValueError as e:
        assert "Rows mentioning a premium or an age" in str(e)
        assert "age group" in str(e)
