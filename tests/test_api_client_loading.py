"""Maintaining each client's own OPEX/loading by hand -
GET/POST/DELETE /portfolio-analysis/client-loading.
"""
import io
from datetime import date

import openpyxl

from app.models import db_models as models


def _client_master_xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Client Name (Master)", "OPEX", "Start Date", "End Date"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _upload_sheet(client, rows):
    return client.post(
        "/portfolio-analysis/client-master/upload",
        files={"file": ("client_master.xlsx", _client_master_xlsx(rows), "application/octet-stream")},
    )


def _add(client, **payload):
    payload.setdefault("master_client_name", "ELLISDON CONSTRUCTION INC")
    payload.setdefault("opex_pct", 0.285)
    return client.post("/portfolio-analysis/client-loading", json=payload)


def test_a_client_loading_can_be_added_by_hand(client):
    resp = _add(client, start_date="2026-01-01", end_date="2027-01-01")
    assert resp.status_code == 200
    body = resp.json()
    assert body["opex_pct"] == 0.285
    assert body["manually_edited"] is True


def test_an_existing_record_is_edited_rather_than_duplicated(client):
    record_id = _add(client).json()["id"]
    _add(client, id=record_id, opex_pct=0.29)

    records = client.get("/portfolio-analysis/client-loading").json()["records"]
    assert len(records) == 1
    assert records[0]["opex_pct"] == 0.29


def test_the_same_client_may_hold_more_than_one_dated_window(client):
    # A loading changes at renewal, and both windows have to stay: an
    # earlier one is what makes a past year's combined ratio reproducible.
    _add(client, opex_pct=0.270, start_date="2025-01-01", end_date="2026-01-01")
    _add(client, opex_pct=0.285, start_date="2026-01-01", end_date="2027-01-01")

    records = client.get("/portfolio-analysis/client-loading").json()["records"]
    assert [r["opex_pct"] for r in records] == [0.285, 0.270]  # newest window first


def test_a_loading_typed_as_a_percent_is_refused_rather_than_stored_as_2650_percent(client):
    assert _add(client, opex_pct=26.5).status_code == 400


def test_a_record_with_no_loading_or_no_name_is_refused(client):
    assert _add(client, opex_pct=None).status_code == 400
    assert _add(client, master_client_name="   ").status_code == 400


def test_an_end_date_before_its_start_is_refused(client):
    assert _add(client, start_date="2026-05-01", end_date="2026-01-01").status_code == 400


def test_editing_a_record_that_does_not_exist_is_a_404(client):
    assert _add(client, id=9999).status_code == 404


def test_one_open_window_can_be_removed_without_touching_the_others(client):
    old = _add(client, opex_pct=0.270, start_date="2030-01-01", end_date="2031-01-01").json()["id"]
    _add(client, opex_pct=0.285, start_date="2031-01-01", end_date="2032-01-01")

    assert client.delete(f"/portfolio-analysis/client-loading/{old}").status_code == 200
    records = client.get("/portfolio-analysis/client-loading").json()["records"]
    assert [r["opex_pct"] for r in records] == [0.285]
    assert client.delete(f"/portfolio-analysis/client-loading/{old}").status_code == 404


# --- a closed window is fixed -------------------------------------------
#
# Once a window's end date has passed, every combined ratio reported for
# that period was measured against the loading in it. A renewal that
# changed the loading wants a NEW window; reaching back into the old one
# silently changes a number that has already been seen. The single
# legitimate exception is a figure that was typed wrongly, and it has to
# be said out loud rather than assumed.

def _closed_window(client, **overrides):
    payload = {"opex_pct": 0.27, "start_date": "2020-01-01", "end_date": "2021-01-01"}
    payload.update(overrides)
    return _add(client, **payload).json()["id"]


def test_a_closed_window_cannot_be_edited_by_accident(client):
    record_id = _closed_window(client)
    resp = _add(client, id=record_id, opex_pct=0.31, start_date="2020-01-01", end_date="2021-01-01")
    assert resp.status_code == 409
    assert "add a new window instead" in resp.json()["detail"].lower()


def test_a_closed_window_cannot_be_deleted_by_accident(client):
    record_id = _closed_window(client)
    assert client.delete(f"/portfolio-analysis/client-loading/{record_id}").status_code == 409


def test_a_wrongly_typed_figure_can_still_be_corrected_when_said_out_loud(client):
    record_id = _closed_window(client, opex_pct=0.027)  # a misplaced decimal
    resp = _add(
        client, id=record_id, opex_pct=0.27,
        start_date="2020-01-01", end_date="2021-01-01", correcting_an_error=True,
    )
    assert resp.status_code == 200
    assert resp.json()["opex_pct"] == 0.27


def test_a_wrongly_added_closed_window_can_still_be_removed_when_said_out_loud(client):
    record_id = _closed_window(client)
    assert client.delete(
        f"/portfolio-analysis/client-loading/{record_id}?correcting_an_error=true"
    ).status_code == 200


def test_an_open_ended_window_never_counts_as_closed(client):
    record_id = _add(client, opex_pct=0.27, start_date="2020-01-01", end_date=None).json()["id"]
    assert _add(client, id=record_id, opex_pct=0.28, start_date="2020-01-01").status_code == 200


def test_the_screen_is_told_which_windows_are_fixed(client):
    # So a closed row renders locked, rather than letting someone type
    # into it and only discovering the rule when the save is refused.
    _closed_window(client)
    _add(client, opex_pct=0.28, start_date="2030-01-01", end_date="2031-01-01")

    records = client.get("/portfolio-analysis/client-loading").json()["records"]
    assert {r["opex_pct"]: r["window_has_closed"] for r in records} == {0.28: False, 0.27: True}


# --- the sheet and the hand edits have to coexist -----------------------

def test_a_hand_edited_record_survives_the_next_client_master_upload(client):
    # The failure this prevents is silent: a figure someone had just
    # corrected reverting to whatever the sheet still says, moving every
    # combined ratio on that account with nothing on screen to show it.
    _add(client, master_client_name="ELLISDON CONSTRUCTION INC", opex_pct=0.285)
    _upload_sheet(client, [["ELLISDON CONSTRUCTION INC", 0.27], ["SAFRAN AEROSYSTEMS", 0.245]])

    records = client.get("/portfolio-analysis/client-loading").json()["records"]
    edited = [r for r in records if r["manually_edited"]]
    assert len(edited) == 1
    assert edited[0]["opex_pct"] == 0.285


def test_a_second_upload_replaces_the_rows_the_sheet_owns(client):
    _upload_sheet(client, [["SAFRAN AEROSYSTEMS", 0.245]])
    _upload_sheet(client, [["SAFRAN AEROSYSTEMS", 0.250]])

    records = client.get("/portfolio-analysis/client-loading").json()["records"]
    assert len(records) == 1
    assert records[0]["opex_pct"] == 0.250


def test_deleting_a_hand_edited_record_lets_the_sheet_populate_it_again(client):
    # The way back out of pinning a client to a hand-entered figure.
    record_id = _add(client, master_client_name="SAFRAN AEROSYSTEMS", opex_pct=0.30).json()["id"]
    client.delete(f"/portfolio-analysis/client-loading/{record_id}")
    _upload_sheet(client, [["SAFRAN AEROSYSTEMS", 0.245]])

    records = client.get("/portfolio-analysis/client-loading").json()["records"]
    assert [r["opex_pct"] for r in records] == [0.245]
    assert records[0]["manually_edited"] is False


# --- the accounts still running on the default --------------------------

def _add_members(client, name, lives, premium):
    db = client.db_session_local()
    db.bulk_insert_mappings(models.PortfolioMember, [
        dict(
            beneficiary_id=f"{name}-{i}", contract=name, master_contract=name,
            gross_premium=premium, policy_start_date=date(2025, 1, 1), policy_end_date=date(2026, 1, 1),
        )
        for i in range(lives)
    ])
    db.commit()
    db.close()


def test_accounts_with_no_loading_record_are_named(client):
    # An account measured against an assumption looks identical to one
    # measured against a known figure everywhere else in the portal.
    _add_members(client, "AEROSTRUCTURES MIDDLE EAST", lives=3, premium=1000.0)
    body = client.get("/portfolio-analysis/client-loading").json()

    gap = body["without_a_record"][0]
    assert gap["master_client_name"] == "AEROSTRUCTURES MIDDLE EAST"
    assert gap["lives"] == 3
    assert gap["gross_premium"] == 3000.0
    assert body["default_opex_pct"] is not None


def test_an_account_stops_being_a_gap_once_it_has_a_record(client):
    _add_members(client, "AEROSTRUCTURES MIDDLE EAST", lives=3, premium=1000.0)
    _add(client, master_client_name="AEROSTRUCTURES MIDDLE EAST", opex_pct=0.26)

    assert client.get("/portfolio-analysis/client-loading").json()["without_a_record"] == []


def test_a_client_named_with_different_spacing_or_case_is_not_reported_as_missing(client):
    _add_members(client, "Aerostructures  Middle East", lives=2, premium=1000.0)
    _add(client, master_client_name="AEROSTRUCTURES MIDDLE EAST", opex_pct=0.26)

    assert client.get("/portfolio-analysis/client-loading").json()["without_a_record"] == []
