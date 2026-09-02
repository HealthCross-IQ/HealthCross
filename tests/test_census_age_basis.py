"""Member ages must say which date they were struck at, and follow it.

Age is derived once, when a census is uploaded, from whatever the case's
policy_start_date held at that moment - and then stored. Nothing
recomputed it, so setting the policy start date AFTER uploading left
every age derived against the old value, silently. The field's own hint
read "Policy start date is used to work out member ages", present tense,
as though it were live.

KIKO MILANO is why this file exists. Its grid showed 18-40 = 52 and
41-59 = 6; on the case's actual policy start date it is 50 and 8. Two
employees born Aug 1985 and Nov 1984 sat a band too low, understating
the expiring premium by AED 16,250 and - once the renewal increase was
applied to that understated base - the ask by about 27,000.
"""
import io
from datetime import date

import pandas as pd

from app.models import db_models as models

# The two KIKO employees whose band moves, plus one who never does.
KIKO_LIKE = [
    {"Category": "A", "Gender": "Male", "DOB": date(1985, 8, 22),
     "Marital Status": "Married", "Relation": "EMPLOYEE", "Emirates": "Dubai"},
    {"Category": "A", "Gender": "Female", "DOB": date(1984, 11, 6),
     "Marital Status": "Married", "Relation": "EMPLOYEE", "Emirates": "Dubai"},
    {"Category": "A", "Gender": "Female", "DOB": date(1995, 3, 1),
     "Marital Status": "Single", "Relation": "EMPLOYEE", "Emirates": "Dubai"},
]


def _census_file(rows=KIKO_LIKE):
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, index=False)
    buf.seek(0)
    return buf


def _case(client, policy_start=None):
    case_id = client.post("/cases", json={
        "broker_name": "B", "company_name": "KIKO MIDDLE EAST", "industry": "retail",
    }).json()["id"]
    patch = {"business_type": "existing", "current_annual_premium": 997_050.0}
    if policy_start:
        patch["policy_start_date"] = policy_start.isoformat()
    client.patch(f"/cases/{case_id}", json=patch)
    return case_id


def _upload(client, case_id, rows=KIKO_LIKE):
    return client.post(
        f"/cases/{case_id}/census",
        files={"file": ("census.xlsx", _census_file(rows),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )


def _ages(client, case_id):
    db = client.db_session_local()
    rows = db.query(models.CensusRecord).filter_by(case_id=case_id).all()
    out = sorted((r.date_of_birth, r.age, r.age_as_of) for r in rows)
    db.close()
    return out


# --- the basis is recorded ----------------------------------------------

def test_an_age_records_the_date_it_was_struck_at(client):
    case_id = _case(client, policy_start=date(2026, 10, 3))
    _upload(client, case_id)

    for dob, age, as_of in _ages(client, case_id):
        assert as_of == date(2026, 10, 3), f"{dob} carries no basis"


def test_an_age_taken_from_the_files_own_column_records_no_basis(client):
    # Nothing is known about how the broker worked it out, so it is not
    # re-derivable and must never be flagged as stale or recomputed.
    case_id = _case(client, policy_start=date(2026, 10, 3))
    _upload(client, case_id, [{"Category": "A", "Gender": "Male", "Age": 44,
                               "Relation": "EMPLOYEE", "Emirates": "Dubai"}])

    (row,) = _ages(client, case_id)
    _, age, as_of = row
    assert age == 44
    assert as_of is None


# --- saving the date re-derives the ages --------------------------------

def test_setting_the_policy_start_date_reworks_the_stored_ages(client):
    # KIKO exactly: uploaded against the expiring term, then the date
    # corrected to the renewal term.
    case_id = _case(client, policy_start=date(2025, 10, 1))
    _upload(client, case_id)

    before = {dob: age for dob, age, _ in _ages(client, case_id)}
    assert before[date(1985, 8, 22)] == 40      # 18-40
    assert before[date(1984, 11, 6)] == 40      # 18-40

    client.patch(f"/cases/{case_id}", json={"policy_start_date": "2026-10-03"})

    after = {dob: age for dob, age, _ in _ages(client, case_id)}
    assert after[date(1985, 8, 22)] == 41       # 41-59
    assert after[date(1984, 11, 6)] == 41       # 41-59
    # And the basis moves with them, so nothing is left claiming the old date.
    assert all(as_of == date(2026, 10, 3) for _, _, as_of in _ages(client, case_id))


def test_a_broker_supplied_age_is_left_exactly_as_given(client):
    # Re-deriving an age with no date of birth behind it would be
    # inventing one.
    case_id = _case(client, policy_start=date(2025, 10, 1))
    _upload(client, case_id, [{"Category": "A", "Gender": "Male", "Age": 44,
                               "Relation": "EMPLOYEE", "Emirates": "Dubai"}])

    client.patch(f"/cases/{case_id}", json={"policy_start_date": "2026-10-03"})
    (_, age, as_of) = _ages(client, case_id)[0]
    assert age == 44
    assert as_of is None


# --- and the staleness is visible until it is fixed ---------------------

def test_a_stale_basis_is_reported_on_the_rate_grid(client):
    case_id = _case(client, policy_start=date(2025, 10, 1))
    _upload(client, case_id)

    # The date is corrected directly in the database, so the ages are NOT
    # re-derived - the state a case was left in before this existed.
    db = client.db_session_local()
    db.query(models.Case).filter_by(id=case_id).update(
        {"policy_start_date": date(2026, 10, 3)})
    db.commit()
    db.close()

    warning = client.get(f"/cases/{case_id}/member-rates").json()["age_basis_warning"]
    assert warning is not None
    # Three members change AGE across the year; only two change BAND,
    # and it is the band that carries the rate.
    assert warning["members_whose_band_changes"] == 2
    assert "01 Oct 2025" in warning["message"]
    assert "03 Oct 2026" in warning["message"]


def test_no_warning_once_the_basis_matches(client):
    case_id = _case(client, policy_start=date(2026, 10, 3))
    _upload(client, case_id)

    assert client.get(f"/cases/{case_id}/member-rates").json()["age_basis_warning"] is None


def test_no_warning_when_the_case_has_no_policy_start_date(client):
    # Nothing to be out of step WITH.
    case_id = _case(client)
    _upload(client, case_id)

    assert client.get(f"/cases/{case_id}/member-rates").json()["age_basis_warning"] is None


def test_changing_the_date_clears_the_warning(client):
    case_id = _case(client, policy_start=date(2025, 10, 1))
    _upload(client, case_id)
    client.patch(f"/cases/{case_id}", json={"policy_start_date": "2026-10-03"})

    assert client.get(f"/cases/{case_id}/member-rates").json()["age_basis_warning"] is None


# --- the book's contract code is not a broker category ------------------

def test_a_book_seeded_census_carries_the_brokers_category_letter():
    # The book stores a category as the full policy code. Seeding a census
    # from it copied that verbatim, so a case whose census said A and B
    # came back reading QIC/HC/BR/KKM/DXB/A and QIC/HC/BR/KKM/BHD/B - and
    # every screen that groups by category split one account into a row
    # per booked entity.
    from app.scoring.rules.renewal_intake import broker_category, census_rows_from_members

    assert broker_category("QIC/HC/BR/KKM/DXB/A") == "A"
    assert broker_category("QIC/HC/BR/KKM/BHD/B") == "B"
    # A plain category is already what the broker wrote - left alone.
    assert broker_category("A") == "A"
    assert broker_category(None) is None
    assert broker_category("") is None

    rows = census_rows_from_members([
        {"beneficiary_id": "K1", "category": "QIC/HC/BR/KKM/DXB/A", "gender": "M"},
        {"beneficiary_id": "K2", "category": "QIC/HC/BR/KKM/BHD/B", "gender": "F"},
    ])
    assert [r["category"] for r in rows] == ["A", "B"]
