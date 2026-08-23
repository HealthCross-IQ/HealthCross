"""Member-level renewal movement - app/scoring/rules/member_movement.py.
Who left, who joined, and whose claims carry forward.
"""
from datetime import date

from app.scoring.rules.member_movement import (
    MATCH_DEMOGRAPHIC,
    MATCH_EXACT_DOB,
    claims_by_member_ref,
    match_members,
    movement_with_claims,
)


def _m(ref, dob=None, age=40, gender="F", relation="Principal", nationality="India", rate=10000.0):
    return {
        "employee_ref": ref,
        "date_of_birth": dob,
        "age": age,
        "gender": gender,
        "relation": relation,
        "nationality": nationality,
        "existing_annual_rate": rate,
    }


def _claim(ref, amount, status="Paid Claims"):
    return {"patient_id": ref, "final_amount": amount, "claim_status": status}


def test_the_same_person_a_year_older_is_not_a_leaver_plus_a_joiner():
    # The bug this whole module exists to avoid: age advances by one
    # between censuses, so matching on age alone reports everyone as
    # having left and an equal number of strangers as having joined.
    expiring = [_m("A1", dob=date(1981, 3, 27), age=44)]
    renewal = [_m(None, dob=date(1981, 3, 27), age=45)]
    result = match_members(expiring, renewal)
    assert result["continuing_count"] == 1
    assert result["leaver_count"] == 0
    assert result["joiner_count"] == 0
    assert result["continuing"][0]["match"] == MATCH_EXACT_DOB


def test_dob_matching_wins_before_demographics_can_steal_a_member():
    # Two members share relation/gender/nationality; only DOB separates
    # them. The exact match must claim its partner first.
    expiring = [
        _m("A1", dob=date(1990, 1, 1), age=35),
        _m("A2", dob=date(1985, 1, 1), age=40),
    ]
    renewal = [_m(None, dob=date(1985, 1, 1), age=41)]
    result = match_members(expiring, renewal)
    assert result["continuing"][0]["expiring"]["employee_ref"] == "A2"
    assert [r["employee_ref"] for r in result["leavers"]] == ["A1"]


def test_members_without_dob_still_match_on_demographics_and_the_year_shift():
    expiring = [_m("A1", age=30, gender="M", nationality="France")]
    renewal = [_m(None, age=31, gender="M", nationality="France")]
    result = match_members(expiring, renewal)
    assert result["continuing_count"] == 1
    assert result["continuing"][0]["match"] == MATCH_DEMOGRAPHIC
    assert result["exact_match_count"] == 0
    assert result["demographic_match_count"] == 1


def test_a_different_nationality_is_a_different_person():
    expiring = [_m("A1", age=30, nationality="India")]
    renewal = [_m(None, age=31, nationality="France")]
    result = match_members(expiring, renewal)
    assert result["leaver_count"] == 1
    assert result["joiner_count"] == 1


def test_a_newborn_is_a_joiner_not_a_replacement_for_a_leaver():
    # A scheme can shed four members and gain two newborns while the
    # headcount only says "-2" - the whole reason counts aren't enough.
    expiring = [_m(f"A{i}", dob=date(1980, 1, i + 1)) for i in range(4)]
    renewal = [_m(None, dob=date(2026, 6, 1), age=0, relation="Child")]
    result = match_members(expiring, renewal)
    assert result["leaver_count"] == 4
    assert result["joiner_count"] == 1
    assert result["renewal_count"] - result["expiring_count"] == -3


def test_claims_roll_up_per_member_splitting_paid_from_outstanding():
    by_ref = claims_by_member_ref([
        _claim("A1", 100.0),
        _claim("A1", 50.0, status="Outstanding Claims"),
        _claim("A2", 30.0, status="Validated"),
    ])
    assert by_ref["A1"] == {"paid": 100.0, "outstanding": 50.0, "incurred": 150.0, "claim_count": 2}
    assert by_ref["A2"]["paid"] == 30.0


def test_leavers_claims_are_reported_separately_not_folded_into_continuing():
    # The finding that changes the price: the members who left took most
    # of the year's claims with them.
    expiring = [
        _m("STAY", dob=date(1990, 1, 1), age=35),
        _m("GONE", dob=date(1970, 1, 1), age=55),
    ]
    renewal = [_m(None, dob=date(1990, 1, 1), age=36)]
    claims = [_claim("STAY", 5_000.0), _claim("GONE", 95_000.0)]

    result = movement_with_claims(expiring, renewal, claims)
    assert result["total_incurred"] == 100_000.0
    assert result["continuing_claims"]["incurred"] == 5_000.0
    assert result["leaver_claims"]["incurred"] == 95_000.0
    assert result["leaver_claims_share"] == 0.95
    assert result["leavers"][0]["employee_ref"] == "GONE"


def test_joiners_carry_no_claims_history():
    expiring = [_m("A1", dob=date(1990, 1, 1))]
    renewal = [
        _m(None, dob=date(1990, 1, 1), age=41),
        _m(None, dob=date(2026, 6, 1), age=0, relation="Child"),
    ]
    result = movement_with_claims(expiring, renewal, [_claim("A1", 2_000.0)])
    assert result["joiner_count"] == 1
    assert result["continuing_claims"]["incurred"] == 2_000.0
    assert result["total_incurred"] == 2_000.0


def test_claims_for_nobody_on_the_census_are_reported_not_silently_dropped():
    # A ledger and a census that aren't the same population would
    # otherwise produce a confident-looking but meaningless split.
    expiring = [_m("A1", dob=date(1990, 1, 1))]
    renewal = [_m(None, dob=date(1990, 1, 1), age=41)]
    result = movement_with_claims(expiring, renewal, [_claim("A1", 100.0), _claim("STRANGER", 900.0)])
    assert result["unattributed_incurred"] == 900.0
    assert result["continuing_claims"]["incurred"] == 100.0


def test_movement_with_no_claims_at_all_is_a_clean_zero():
    expiring = [_m("A1", dob=date(1990, 1, 1))]
    renewal = [_m(None, dob=date(1990, 1, 1), age=41)]
    result = movement_with_claims(expiring, renewal, [])
    assert result["total_incurred"] == 0.0
    assert result["leaver_claims_share"] == 0.0
    assert result["claims_matched"] is False


# --- the vocabulary the two sources actually use ------------------------
# The Membership export calls a principal "Main Insured"; a broker census
# calls the same person "Self" or "Employee". Classifying them differently
# splits one population in two and makes it impossible to pair a principal
# against themselves at renewal.

def test_self_main_insured_and_employee_are_all_the_same_relation():
    from app.ingestion.census import _classify_relation
    for term in ("Self", "SELF", "Main Insured", "MAIN INSURED", "main insured",
                 "Employee", "Employees", "Principal", "Member", "Primary Insured"):
        assert _classify_relation(term) == "employee", term


def test_dependents_still_classify_to_their_own_relations():
    from app.ingestion.census import _classify_relation
    assert _classify_relation("Spouse") == "spouse"
    assert _classify_relation("Wife") == "spouse"
    assert _classify_relation("Partner") == "spouse"
    assert _classify_relation("Child") == "child"
    assert _classify_relation("Son") == "child"
    assert _classify_relation("Parent") == "other"


def test_a_principal_matches_across_the_two_vocabularies():
    # End to end: the book says "Main Insured", the renewal census says
    # "Self", and it is one continuing member - not a leaver plus a joiner.
    from app.ingestion.census import _classify_relation
    expiring = [_m("A1", dob=date(1981, 3, 27), age=44, relation=_classify_relation("Main Insured"))]
    renewal = [_m(None, dob=date(1981, 3, 27), age=45, relation=_classify_relation("Self"))]
    result = match_members(expiring, renewal)
    assert result["continuing_count"] == 1
    assert result["leaver_count"] == 0
