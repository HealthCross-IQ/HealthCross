"""A broker's renewal census against the book's own roster -
app/scoring/rules/renewal_intake.py's compare_against_supplied_census.

Two things this has to get right, both of which it got wrong first.

A name on the census that is not on the account's roster is not
automatically a joiner. KIKO is booked as seven contracts and quoted as
one; read against the lead entity alone the comparison reported 26
joiners, every one of whom was already on the book under a sibling
contract. A joiner with no history is an underwriting problem, so
reporting 26 of them where there were none would have priced the renewal
on a fiction.

And both scenarios are always returned. The census roster is the smaller,
better-looking one - it drops five members carrying 9% of the claims -
and a comparison that showed only that would be a discount with no
argument attached to it.
"""
from datetime import date

from app.scoring.rules.renewal_intake import compare_against_supplied_census

TERM_START, TERM_END = date(2025, 10, 1), date(2026, 9, 30)


def _m(bid, premium=12_000.0, end=TERM_END, relation="employee", age=35):
    return {
        "beneficiary_id": bid, "relation": relation, "age": age, "gender": "M",
        "policy_start_date": TERM_START, "policy_end_date": TERM_END,
        "member_start_date": TERM_START, "member_end_date": end,
        "actual_gross_premium": premium, "gross_premium": premium,
    }


def _claims(**by_member):
    return {
        bid: [{"patient_id": bid, "final_amount": amount, "claim_status": "Paid Claims",
               "date_of_treatment": date(2026, 3, 1)}]
        for bid, amount in by_member.items()
    }


AS_OF = date(2026, 7, 1)


# --- who is staying, leaving, joining ------------------------------------

def test_a_member_the_census_leaves_out_is_leaving():
    members = [_m("A"), _m("B"), _m("C")]
    out = compare_against_supplied_census(members, ["A", "B"], _claims(A=1000, B=2000, C=9000),
                                          as_of=AS_OF)
    assert out["staying"]["member_count"] == 2
    assert out["leaving"]["member_count"] == 1
    assert [m["beneficiary_id"] for m in out["leaving"]["members"]] == ["C"]
    assert out["leaving"]["claims"] == 9000


def test_leavers_come_back_worst_first():
    # The renewal conversation is about the expensive ones. An
    # alphabetical list buries them.
    members = [_m("A"), _m("B"), _m("C")]
    out = compare_against_supplied_census(members, [], _claims(A=100, B=9000, C=500), as_of=AS_OF)
    assert [m["beneficiary_id"] for m in out["leaving"]["members"]] == ["B", "C", "A"]


def test_a_name_that_is_nowhere_on_the_book_is_a_joiner():
    out = compare_against_supplied_census([_m("A")], ["A", "NEW1"], _claims(A=1000), as_of=AS_OF)
    assert out["joining"] == {"member_count": 1, "references": ["NEW1"]}


def test_a_name_on_a_sibling_contract_is_not_counted_as_a_joiner():
    # KIKO's shape: the census spans the group, the roster is one entity.
    # Counting the rest as joiners invents 26 lives with no history.
    out = compare_against_supplied_census(
        [_m("A")], ["A", "S1", "S2", "NEW1"], _claims(A=1000), as_of=AS_OF,
        elsewhere_in_book={"S1": "KIKO IMEA FZE", "S2": "KIKO COSMETICS LLC"},
    )
    assert out["joining"]["references"] == ["NEW1"]
    assert out["on_other_contracts"]["member_count"] == 2
    assert [a["master_client"] for a in out["on_other_contracts"]["accounts"]] == [
        "KIKO IMEA FZE", "KIKO COSMETICS LLC",
    ]
    assert "already in the portfolio" in out["on_other_contracts"]["note"]


def test_nothing_is_said_about_other_contracts_when_there_are_none():
    out = compare_against_supplied_census([_m("A")], ["A"], _claims(A=1000), as_of=AS_OF)
    assert out["on_other_contracts"]["member_count"] == 0
    assert out["on_other_contracts"]["note"] is None


# --- both scenarios, never one -------------------------------------------

def test_both_rosters_are_priced():
    members = [_m("A"), _m("B"), _m("C")]
    out = compare_against_supplied_census(members, ["A", "B"], _claims(A=1000, B=1000, C=9000),
                                          as_of=AS_OF)
    on_book, on_census = out["on_book_roster"], out["on_supplied_census"]
    assert on_book["active_member_count"] == 3
    assert on_census["active_member_count"] == 2
    # Dropping the member who is 82% of the claims has to move the ratio,
    # and has to move it downward - that is the whole point of showing
    # the first figure beside it.
    assert on_census["loss_ratio"] < on_book["loss_ratio"]


def test_the_census_roster_keeps_rows_that_are_not_on_the_active_list():
    # Prior policy years and members already off risk carry the term
    # windows the loss ratio is measured inside. Filtering them out with
    # the absent actives would silently shorten the year.
    prior = _m("A", end=TERM_END)
    prior["policy_start_date"], prior["policy_end_date"] = date(2024, 10, 1), date(2025, 9, 30)
    out = compare_against_supplied_census([_m("A"), _m("B"), prior], ["A"],
                                          _claims(A=1000, B=1000), as_of=AS_OF)
    assert out["on_supplied_census"]["active_member_count"] == 1


# --- when the two files do not share identifiers -------------------------

def test_a_census_sharing_no_references_is_reported_as_not_a_comparison():
    out = compare_against_supplied_census([_m("A"), _m("B")], ["X1", "X2"],
                                          _claims(A=1000, B=1000), as_of=AS_OF)
    assert out["reliable"] is False
    assert "do not share member identifiers" in out["warning"]


def test_a_matching_census_is_reliable_and_silent():
    out = compare_against_supplied_census([_m("A")], ["A"], _claims(A=1000), as_of=AS_OF)
    assert out["reliable"] is True
    assert out["warning"] is None


def test_references_are_matched_after_whitespace_is_stripped():
    # Exports carry trailing spaces; a comparison that treats "A " and
    # "A" as different people reports every member as both a leaver and
    # a joiner.
    out = compare_against_supplied_census([_m("A")], [" A "], _claims(A=1000), as_of=AS_OF)
    assert out["staying"]["member_count"] == 1
    assert out["joining"]["member_count"] == 0


def test_an_empty_census_leaves_everybody_leaving_rather_than_raising():
    out = compare_against_supplied_census([_m("A"), _m("B")], [], _claims(A=1000), as_of=AS_OF)
    assert out["leaving"]["member_count"] == 2
    assert out["reliable"] is False


# --- the endpoint --------------------------------------------------------

def _xlsx(refs):
    import io

    import pandas as pd

    buf = io.BytesIO()
    pd.DataFrame([{"Dependent_Insured_Number": r, "Relation": "Employee", "Gender": "M"}
                  for r in refs]).to_excel(buf, index=False)
    buf.seek(0)
    return buf


def _seed(client, contract, members, claims=()):
    from app.models import db_models as models

    db = client.db_session_local()
    db.bulk_insert_mappings(models.PortfolioMember, [
        {**m, "contract": contract, "master_contract": contract} for m in members])
    db.bulk_insert_mappings(models.PortfolioClaimEntry, list(claims))
    db.commit()
    db.close()


def _post(client, master_client, refs, **params):
    return client.post(
        f"/portfolio-analysis/renewal-census-comparison/{master_client}",
        files={"file": ("census.xlsx", _xlsx(refs),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        params=params,
    )


def _claim(bid, amount, when=date(2026, 3, 1)):
    return {"patient_id": bid, "final_amount": amount, "claim_status": "Paid Claims",
            "date_of_treatment": when}


def test_the_endpoint_compares_a_census_against_the_book(client):
    _seed(client, "KIKO MIDDLE EAST FZ LLC", [_m("A"), _m("B"), _m("C")],
          [_claim("A", 9_000.0), _claim("C", 40_000.0)])

    body = _post(client, "KIKO MIDDLE EAST FZ LLC", ["A", "B"], as_of="2026-07-01").json()
    assert body["rows_read"] == 2 and body["rows_with_a_reference"] == 2
    assert body["staying"]["member_count"] == 2
    assert [m["beneficiary_id"] for m in body["leaving"]["members"]] == ["C"]
    # Both scenarios, priced.
    assert body["on_book_roster"]["active_member_count"] == 3
    assert body["on_supplied_census"]["active_member_count"] == 2


def test_a_census_name_on_a_sibling_contract_is_named_not_counted_as_a_joiner(client):
    # The KIKO shape: the group's census, one entity's roster.
    _seed(client, "KIKO MIDDLE EAST FZ LLC", [_m("A")])
    _seed(client, "KIKO COSMETICS LLC", [_m("S1"), _m("S2")])

    body = _post(client, "KIKO MIDDLE EAST FZ LLC", ["A", "S1", "S2", "NEW1"]).json()
    assert body["joining"]["references"] == ["NEW1"]
    assert body["on_other_contracts"]["accounts"] == [
        {"master_client": "KIKO COSMETICS LLC", "member_count": 2, "references": ["S1", "S2"]},
    ]


def test_including_the_sibling_contracts_prices_the_group_as_one(client):
    _seed(client, "KIKO MIDDLE EAST FZ LLC", [_m("A")], [_claim("A", 9_000.0)])
    _seed(client, "KIKO COSMETICS LLC", [_m("S1"), _m("S2")], [_claim("S1", 5_000.0)])

    body = _post(client, "KIKO MIDDLE EAST FZ LLC", ["A", "S1", "S2"],
                 include=["KIKO COSMETICS LLC"], as_of="2026-07-01").json()
    assert body["also_included"] == ["KIKO COSMETICS LLC"]
    assert body["staying"]["member_count"] == 3
    assert body["staying"]["claims"] == 14_000.0
    assert body["on_other_contracts"]["member_count"] == 0
    assert body["joining"]["member_count"] == 0


def test_a_census_with_no_reference_column_is_refused_rather_than_guessed_at(client):
    import io

    import pandas as pd

    _seed(client, "Acme", [_m("A")])
    buf = io.BytesIO()
    pd.DataFrame([{"Relation": "Employee", "Gender": "M", "Age": 30}]).to_excel(buf, index=False)
    buf.seek(0)
    resp = client.post("/portfolio-analysis/renewal-census-comparison/Acme",
                       files={"file": ("census.xlsx", buf, "application/octet-stream")})
    assert resp.status_code == 400
    assert "no member reference column" in resp.json()["detail"]


def test_the_endpoint_404s_for_an_account_that_is_not_on_the_book(client):
    _seed(client, "Acme", [_m("A")])
    assert _post(client, "Nobody Ltd", ["A"]).status_code == 404


def test_the_endpoint_400s_before_any_membership_is_uploaded(client):
    assert _post(client, "Acme", ["A"]).status_code == 400


# --- coming back after coming off risk -----------------------------------

def test_a_member_off_risk_before_the_cut_date_is_returning_not_joining():
    # KIKO's census carries two of these, one off risk since July and one
    # since August. Both have a year of claims behind them; reading them
    # as new lives would price them off the rate card with their own
    # history sitting in the file.
    left = _m("R1", end=date(2026, 7, 9))
    out = compare_against_supplied_census([_m("A"), left], ["A", "R1"],
                                          _claims(A=1000, R1=25_000), as_of=AS_OF)
    assert out["joining"]["member_count"] == 0
    assert out["returning"]["member_count"] == 1
    assert out["returning"]["claims"] == 25_000
    assert out["returning"]["members"][0]["cover_ended"] == date(2026, 7, 9)


def test_a_returning_member_is_in_neither_loss_ratio():
    # Neither roster has them on risk at the cut date, so neither ratio
    # can carry them - saying so beats letting a reader assume they are
    # in one of the two.
    left = _m("R1", end=date(2026, 7, 9))
    out = compare_against_supplied_census([_m("A"), left], ["A", "R1"],
                                          _claims(A=1000, R1=25_000), as_of=AS_OF)
    assert out["on_book_roster"]["active_member_count"] == 1
    assert out["on_supplied_census"]["active_member_count"] == 1


def test_a_sibling_contract_still_wins_over_returning():
    # A name on another live contract is a scoping question; only a name
    # on THIS account that has come off risk is a returning member.
    out = compare_against_supplied_census(
        [_m("A")], ["A", "S1"], _claims(A=1000), as_of=AS_OF,
        elsewhere_in_book={"S1": "KIKO COSMETICS LLC"},
    )
    assert out["returning"]["member_count"] == 0
    assert out["on_other_contracts"]["member_count"] == 1


def test_an_account_with_nobody_returning_says_zero():
    out = compare_against_supplied_census([_m("A")], ["A", "NEW1"], _claims(A=1000), as_of=AS_OF)
    assert out["returning"]["member_count"] == 0
    assert out["joining"]["references"] == ["NEW1"]


# --- what date the year is measured to -----------------------------------

def test_the_year_is_measured_to_the_data_not_to_today():
    # Earning premium to today against claims that stop at the extract
    # date credits the account with premium for weeks nobody has
    # reported a claim in yet, and it flatters the ratio by more the
    # longer the extract sits. KIKO's 15 August file read on 27 August
    # came out 4.7 points better earned to today.
    from app.scoring.rules.renewal_intake import renewal_loss_ratio

    claims = {"A": [{"patient_id": "A", "final_amount": 5000.0, "claim_status": "Paid Claims",
                     "date_of_treatment": date(2026, 3, 1)}]}
    out = renewal_loss_ratio([_m("A")], claims)
    assert out["as_of"] == date(2026, 3, 1)
    assert out["as_of_source"] == "the last day the claims data covers"


def test_an_explicit_date_still_wins():
    from app.scoring.rules.renewal_intake import renewal_loss_ratio

    claims = {"A": [{"patient_id": "A", "final_amount": 5000.0, "claim_status": "Paid Claims",
                     "date_of_treatment": date(2026, 3, 1)}]}
    out = renewal_loss_ratio([_m("A")], claims, as_of=date(2026, 8, 15))
    assert out["as_of"] == date(2026, 8, 15)
    assert out["as_of_source"] == "supplied"


def test_the_extract_date_is_read_across_the_book_not_the_account():
    # An account with a quiet August has not stopped being covered.
    from app.scoring.rules.renewal_intake import data_covered_to

    claims = {"A": [{"date_of_treatment": date(2026, 3, 1)}],
              "Z": [{"date_of_treatment": date(2026, 8, 15)}]}
    assert data_covered_to(claims) == date(2026, 8, 15)


def test_no_dated_claims_at_all_falls_back_rather_than_raising():
    from app.scoring.rules.renewal_intake import data_covered_to

    assert data_covered_to({}) is None
    assert data_covered_to({"A": [{"final_amount": 1.0}]}) is None
