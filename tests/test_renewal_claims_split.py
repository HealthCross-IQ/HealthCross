"""Renewing on last year's headline prices a population that will not
be there - app/scoring/rules/renewal_intake.py's claims_by_member_status.
"""
from datetime import date

import pytest

from app.scoring.rules.renewal_intake import claims_by_member_status, continuing_and_leaving

TERM_START, TERM_END = date(2025, 10, 1), date(2026, 9, 30)


def _m(bid, end=TERM_END, premium=12_000.0, start=TERM_START):
    return {
        "beneficiary_id": bid, "policy_start_date": TERM_START, "policy_end_date": TERM_END,
        "member_start_date": start, "member_end_date": end,
        "actual_gross_premium": premium, "gross_premium": premium,
    }


def _c(bid, amount, when=date(2026, 3, 1), status="Paid Claims"):
    return {"patient_id": bid, "final_amount": amount, "date_of_treatment": when,
            "claim_status": status}


def _by_beneficiary(claims):
    out = {}
    for c in claims:
        out.setdefault(c["patient_id"], []).append(c)
    return out


# --- who is continuing ---------------------------------------------------

def test_a_member_whose_cover_ends_on_the_term_end_is_continuing():
    # The term runs to the end of that day. Treating it as a leaver would
    # empty the renewing population entirely on a normal account.
    continuing, leaving = continuing_and_leaving([_m("A"), _m("B")])
    assert len(continuing) == 2
    assert leaving == []


def test_a_member_who_left_mid_term_is_not_renewing():
    continuing, leaving = continuing_and_leaving([_m("A"), _m("B", end=date(2026, 4, 23))])
    assert [m["beneficiary_id"] for m in continuing] == ["A"]
    assert [m["beneficiary_id"] for m in leaving] == ["B"]


def test_the_split_date_defaults_to_the_terms_own_end():
    # Not to today. "Who is covered now" and "who walks into the new
    # policy year" are different questions, and only the second one
    # prices a renewal.
    split = claims_by_member_status([_m("A"), _m("B", end=date(2026, 4, 23))], {})
    assert split["as_at"] == TERM_END


# --- the split -----------------------------------------------------------

def _serviceplan_shaped():
    """Five stayers and one leaver, the leaver costing more per head on
    a part-year premium - the real Serviceplan shape in miniature.
    """
    members = [_m(f"S{i}") for i in range(5)] + [_m("L1", end=date(2026, 4, 23), premium=6_000.0)]
    claims = [_c(f"S{i}", 10_000.0) for i in range(5)] + [_c("L1", 20_000.0, when=date(2026, 2, 1))]
    return members, _by_beneficiary(claims)


def test_claims_and_premium_land_in_the_right_bucket():
    split = claims_by_member_status(*_serviceplan_shaped())
    assert split["continuing"]["member_count"] == 5
    assert split["continuing"]["incurred"] == 50_000.0
    assert split["continuing"]["premium"] == 60_000.0
    assert split["leaving"]["member_count"] == 1
    assert split["leaving"]["incurred"] == 20_000.0
    assert split["leaving"]["premium"] == 6_000.0


def test_a_leaver_on_a_part_year_premium_shows_a_high_ratio_without_claiming_more():
    # Their premium is prorated and their claims are not. That is the
    # whole of the effect, and reading it as anti-selection would load a
    # renewal for something that is not happening.
    split = claims_by_member_status(*_serviceplan_shaped())
    assert split["leaving"]["loss_ratio"] > split["continuing"]["loss_ratio"] * 2


def test_the_ratio_excluding_leavers_is_the_number_the_renewal_turns_on():
    split = claims_by_member_status(*_serviceplan_shaped())
    assert split["loss_ratio_excluding_leavers"] == split["continuing"]["loss_ratio"]
    assert split["loss_ratio_excluding_leavers"] < split["total"]["loss_ratio"]


def test_outstanding_is_counted_as_incurred_not_dropped():
    members = [_m("A")]
    claims = _by_beneficiary([_c("A", 1_000.0), _c("A", 400.0, status="Outstanding Claims")])
    split = claims_by_member_status(members, claims)
    assert split["continuing"]["paid"] == 1_000.0
    assert split["continuing"]["outstanding"] == 400.0
    assert split["continuing"]["incurred"] == 1_400.0


def test_a_claim_outside_the_members_own_window_does_not_count():
    # A leaver's claim after they left belongs to nobody's exposure here.
    members = [_m("L1", end=date(2026, 4, 23))]
    claims = _by_beneficiary([_c("L1", 5_000.0, when=date(2026, 8, 1))])
    split = claims_by_member_status(members, claims)
    assert split["leaving"]["incurred"] == 0.0


def test_shares_are_reported_so_the_two_can_be_compared():
    split = claims_by_member_status(*_serviceplan_shaped())
    assert split["leaver_share_of_members"] == pytest.approx(1 / 6, abs=1e-3)
    assert split["leaver_share_of_claims"] == pytest.approx(20_000 / 70_000, abs=1e-3)
    # The point of showing both: a leaver group that is 17% of the
    # members and 29% of the claims is not a rounding difference.
    assert split["leaver_share_of_claims"] > split["leaver_share_of_members"]


def test_an_account_with_no_leavers_reports_zeroes_rather_than_nothing():
    split = claims_by_member_status([_m("A"), _m("B")], _by_beneficiary([_c("A", 500.0)]))
    assert split["leaving"]["member_count"] == 0
    assert split["leaving"]["incurred"] == 0.0
    assert split["leaving"]["loss_ratio"] is None
    assert split["loss_ratio_excluding_leavers"] == split["total"]["loss_ratio"]


def test_no_premium_gives_no_ratio_rather_than_a_division_by_zero():
    split = claims_by_member_status([_m("A", premium=0.0)], _by_beneficiary([_c("A", 900.0)]))
    assert split["continuing"]["loss_ratio"] is None
    assert split["total"]["loss_ratio"] is None


# --- the endpoint --------------------------------------------------------

def _seed(client, members, claims):
    from app.models import db_models as models

    def rows(db):
        db.bulk_insert_mappings(models.PortfolioMember, [
            {**m, "contract": client, "master_contract": client} for m in members])
        db.bulk_insert_mappings(models.PortfolioClaimEntry, claims)
        db.commit()
    return rows


def test_the_endpoint_splits_an_accounts_claims(client):
    db = client.db_session_local()
    _seed("SERVICEPLAN MIDDLE EAST FZ LLC",
          [_m("A"), _m("B"), _m("L1", end=date(2026, 4, 23), premium=6_000.0)],
          [{"patient_id": "A", "final_amount": 9_000.0, "claim_status": "Paid Claims",
            "date_of_treatment": date(2026, 3, 1)},
           {"patient_id": "L1", "final_amount": 15_000.0, "claim_status": "Paid Claims",
            "date_of_treatment": date(2026, 2, 1)}])(db)
    db.close()

    resp = client.get("/portfolio-analysis/renewal-claims-split/SERVICEPLAN MIDDLE EAST FZ LLC")
    assert resp.status_code == 200
    body = resp.json()
    assert body["continuing"]["member_count"] == 2
    assert body["continuing"]["incurred"] == 9_000.0
    assert body["leaving"]["member_count"] == 1
    assert body["leaving"]["incurred"] == 15_000.0
    assert body["loss_ratio_excluding_leavers"] < body["total"]["loss_ratio"]


def test_the_endpoint_404s_for_an_account_that_is_not_on_the_book(client):
    db = client.db_session_local()
    _seed("Acme", [_m("A")], [])(db)
    db.close()
    assert client.get("/portfolio-analysis/renewal-claims-split/Nobody Ltd").status_code == 404


def test_the_endpoint_400s_before_any_membership_is_uploaded(client):
    assert client.get("/portfolio-analysis/renewal-claims-split/Acme").status_code == 400


# --- the cut date --------------------------------------------------------

def _roster(policy_end, member_end, n=5):
    """One account, in whichever end-date convention an export uses."""
    return [
        {"beneficiary_id": f"M{i}", "policy_start_date": date(2025, 10, 1),
         "policy_end_date": policy_end, "member_start_date": date(2025, 10, 1),
         "member_end_date": member_end, "gross_premium": 12_000.0}
        for i in range(n)
    ] + [
        {"beneficiary_id": "L1", "policy_start_date": date(2025, 10, 1),
         "policy_end_date": policy_end, "member_start_date": date(2025, 10, 1),
         "member_end_date": date(2026, 4, 24), "gross_premium": 12_000.0}
    ]


@pytest.mark.parametrize("policy_end,member_end", [
    (date(2026, 10, 1), date(2026, 10, 1)),   # the claims export
    (date(2026, 9, 30), date(2026, 9, 30)),   # the membership export
])
def test_either_export_convention_gives_the_same_split(policy_end, member_end):
    # The two exports put this scheme's policy end a day apart. A rule
    # written against policy_end_date returns a confident, silent zero on
    # the other one - every member reads as deleted and nothing says so.
    from app.scoring.rules.renewal_intake import roster_term_end

    roster = _roster(policy_end, member_end)
    assert roster_term_end(roster)[0] == member_end
    split = claims_by_member_status(roster, {})
    assert split["continuing"]["member_count"] == 5
    assert split["leaving"]["member_count"] == 1


def test_exports_disagreeing_about_the_same_day_is_flagged_not_swallowed():
    from app.scoring.rules.renewal_intake import roster_term_end

    roster = _roster(date(2026, 10, 1), date(2026, 9, 30))
    term_end, warning = roster_term_end(roster)
    assert term_end == date(2026, 9, 30), "the roster wins, not the policy field"
    assert warning and "disagree" in warning
    assert claims_by_member_status(roster, {})["warning"] == warning


def test_a_roster_with_no_common_end_date_refuses_to_guess():
    # Six members, six different end dates. Whichever one happens to sort
    # highest is not the term end, and treating it as one would silently
    # classify five of six as deleted.
    from app.scoring.rules.renewal_intake import roster_term_end

    roster = [
        {"beneficiary_id": f"M{i}", "policy_start_date": date(2025, 10, 1),
         "policy_end_date": date(2026, 10, 1), "member_start_date": date(2025, 10, 1),
         "member_end_date": date(2026, m, 1), "gross_premium": 12_000.0}
        for i, m in enumerate(range(1, 7))
    ]
    term_end, warning = roster_term_end(roster)
    assert term_end is None
    assert "Set the cut date explicitly" in warning


def test_a_supplied_cut_date_beats_the_roster():
    # The whole point of making it an input: an underwriter pricing to a
    # different date says so, and is told the figure is theirs.
    roster = _roster(date(2026, 10, 1), date(2026, 10, 1))
    split = claims_by_member_status(roster, {}, as_at=date(2026, 4, 1))
    assert split["as_at"] == date(2026, 4, 1)
    assert split["cut_date_source"] == "supplied"
    # At 1 April the member who left on 24 April is still on risk.
    assert split["continuing"]["member_count"] == 6
    assert split["leaving"]["member_count"] == 0


def test_a_derived_cut_date_says_it_was_derived():
    split = claims_by_member_status(_roster(date(2026, 10, 1), date(2026, 10, 1)), {})
    assert split["cut_date_source"] == "roster"


def test_the_endpoint_accepts_a_cut_date(client):
    db = client.db_session_local()
    _seed("Acme", [_m("A"), _m("B", end=date(2026, 4, 23))], [])(db)
    db.close()

    late = client.get("/portfolio-analysis/renewal-claims-split/Acme",
                      params={"as_at": "2026-09-30"}).json()
    early = client.get("/portfolio-analysis/renewal-claims-split/Acme",
                       params={"as_at": "2026-04-01"}).json()
    assert late["leaving"]["member_count"] == 1
    assert early["leaving"]["member_count"] == 0
    assert early["cut_date_source"] == "supplied"
