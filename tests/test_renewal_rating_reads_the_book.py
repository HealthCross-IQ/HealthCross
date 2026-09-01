"""The renewal scorecard must report the account's loss ratio the way
the Portfolio Loss Ratio screen does - app/api/routes_analysis.py's
_account_rating_from_book.

NOMADA EVENTS #40 is why this file exists. The Renewal Bench scorecard
read the account at 75.6% and +21.28%; the Loss Ratio screen, on the
same book as of 15 August, had it at 83.6% gross and 108.6% net. Three
separate causes, all pushing the renewal ask DOWN:

  - The scorecard read a per-case claims ledger, not the book, so it was
    working from an older upload.
  - full_months_only drops the last month in the ledger. That is right
    for averaging a part month and wrong for a claims TOTAL: it lost AED
    1,953.89 of outstanding, reporting 6,671.57 where the ledger held
    8,625.46.
  - It divided by the case's current_annual_premium, which a renewal
    opened from the book sets to headcount x each member's full annual
    rate - 114,488, where the account's own gross premium was 90,347.

Underwriting quoted +21% on an account that needs +54%.

The fix is not a more careful reimplementation. It is that the scorecard
now CALLS account_loss_ratio_rows_for_book - the same function behind the
Loss Ratio screen - so the two cannot drift apart again.
"""
from datetime import date

from app.models import db_models as models

TERM_START = date(2025, 10, 1)
TERM_END = date(2026, 9, 30)
AS_OF = date(2026, 8, 15)


def _member(bid, gross, actual, start=TERM_START, end=TERM_END):
    return {
        "beneficiary_id": bid, "contract": "NOMADA EVENTS", "master_contract": "NOMADA EVENTS",
        "relation": "employee", "age": 35, "gender": "M",
        "policy_start_date": TERM_START, "policy_end_date": TERM_END,
        "member_start_date": start, "member_end_date": end,
        "gross_premium": gross, "actual_gross_premium": actual,
    }


def _claim(bid, amount, when, status="Paid Claims"):
    return {"patient_id": bid, "final_amount": amount, "claim_status": status,
            "date_of_treatment": when}


def _seed_book(client, members, claims):
    db = client.db_session_local()
    db.bulk_insert_mappings(models.PortfolioMember, members)
    db.bulk_insert_mappings(models.PortfolioClaimEntry, claims)
    db.add(models.PortfolioDataSnapshot(data_as_of_date=AS_OF))
    db.commit()
    db.close()


def _nomada(client):
    """Sixteen lives, claims in every month including the one a ledger
    export would have cut through.
    """
    members = [_member(f"N{i}", 6_000.0, 5_646.69) for i in range(16)]
    claims = []
    for month in range(10, 13):
        claims.append(_claim("N0", 4_000.0, date(2025, month, 10)))
    for month in range(1, 8):
        claims.append(_claim("N1", 5_000.0, date(2026, month, 10)))
    # The month a ledger drops, carrying outstanding that then vanishes.
    claims.append(_claim("N2", 1_953.89, date(2026, 8, 5), status="Outstanding Claims"))
    claims.append(_claim("N3", 6_671.57, date(2026, 3, 5), status="Outstanding Claims"))
    _seed_book(client, members, claims)
    return members, claims


def _case(client, company="NOMADA EVENTS", premium=114_488.0):
    case_id = client.post("/cases", json={
        "broker_name": "Broker", "company_name": company, "industry": "events",
    }).json()["id"]
    client.patch(f"/cases/{case_id}", json={
        "business_type": "existing", "current_annual_premium": premium,
    })
    return case_id


def _ledger(client, case_id, monthly):
    db = client.db_session_local()
    for i, ((year, month), amount) in enumerate(monthly.items()):
        db.add(models.ClaimsLedgerEntry(
            case_id=case_id, patient_id=f"P{i}", claim_id=f"C{i}",
            claim_status="Paid Claims",
            policy_start_date=TERM_START, policy_end_date=TERM_END,
            date_of_treatment=date(year, month, 10),
            ip_op_maternity="OP", final_amount=amount,
        ))
    db.commit()
    db.close()


LEDGER_MONTHS = {
    (2025, 10): 5_000.0, (2025, 11): 5_000.0, (2025, 12): 5_000.0,
    (2026, 1): 5_000.0, (2026, 2): 5_000.0, (2026, 3): 5_000.0,
    (2026, 4): 5_000.0, (2026, 5): 5_000.0, (2026, 6): 5_000.0,
    (2026, 7): 2_000.0,
}


# --- the scorecard and the loss ratio screen must agree ------------------

def test_the_scorecard_reports_the_same_loss_ratio_as_the_loss_ratio_screen(client):
    _nomada(client)
    case_id = _case(client)
    _ledger(client, case_id, LEDGER_MONTHS)

    rating = client.get(f"/cases/{case_id}/renewal-rating").json()
    screen = client.get("/portfolio-analysis/account-loss-ratio",
                        params={"client": "NOMADA EVENTS", "as_of": AS_OF.isoformat()}).json()

    book = rating["from_book"]
    row = max(screen["rows"], key=lambda r: r["policy_start_date"])
    for field in ("paid", "outstanding", "ibnr", "incurred_claims",
                  "earned_premium", "net_premium", "gross_loss_ratio", "net_loss_ratio"):
        assert book[field] == row[field], f"{field} disagrees between the two screens"


def test_the_book_figures_are_attached_to_the_rating(client):
    _nomada(client)
    case_id = _case(client)
    _ledger(client, case_id, LEDGER_MONTHS)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["from_book"]["source"] == "Portfolio Loss Ratio (the uploaded book)"
    assert body["from_book"]["as_of"] == AS_OF.isoformat()
    # The measure an underwriter acts on has to be there, not derivable.
    assert body["from_book"]["gross_loss_ratio"] is not None
    assert body["from_book"]["net_loss_ratio"] is not None


# --- the premium that broke NOMADA ---------------------------------------

def test_a_case_premium_that_disagrees_with_the_book_is_flagged(client):
    # 114,488 on the case against the book's own gross premium. Nothing
    # on the old card said the two differed, and the ratio quietly used
    # the larger one.
    _nomada(client)
    case_id = _case(client, premium=114_488.0)
    _ledger(client, case_id, LEDGER_MONTHS)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["from_book"]["gross_premium"] != 114_488.0
    assert body["premium_disagrees_with_book"] is True


def test_a_case_premium_matching_the_book_is_not_flagged(client):
    _nomada(client)
    case_id = _case(client)
    _ledger(client, case_id, LEDGER_MONTHS)
    book_premium = client.get(f"/cases/{case_id}/renewal-rating").json()["from_book"]["gross_premium"]

    client.patch(f"/cases/{case_id}", json={"current_annual_premium": book_premium})
    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["premium_disagrees_with_book"] is False


# --- nothing may vanish silently -----------------------------------------

def test_the_book_counts_the_month_a_ledger_would_have_dropped(client):
    # The ledger path drops its last month. The book has no such notion,
    # and an August outstanding claim has to be in the incurred figure.
    _nomada(client)
    case_id = _case(client)
    _ledger(client, case_id, LEDGER_MONTHS)

    book = client.get(f"/cases/{case_id}/renewal-rating").json()["from_book"]
    assert book["outstanding"] == 1_953.89 + 6_671.57


def test_the_ledger_path_reports_what_its_excluded_months_hold(client):
    # Still the fallback for a case not on the book, so it must say what
    # it dropped rather than leaving the claims unaccounted for.
    case_id = _case(client)
    _ledger(client, case_id, LEDGER_MONTHS)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["excluded_months"] == ["2026-07"]
    assert body["excluded_paid"] == 2_000.0


def test_the_annualised_ibnr_is_reported_beside_the_annualised_base(client):
    # The card printed a to-date IBNR next to annualised everything else
    # and its own column stopped adding up.
    case_id = _case(client)
    _ledger(client, case_id, LEDGER_MONTHS)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    detail = body["ibnr_detail"]
    assert detail["annualized_ibnr"] > detail["ibnr"]
    assert round(body["annualized_paid_and_outstanding"] + detail["annualized_ibnr"], 2) == \
        body["annualized_incurred_claims"]


# --- a case that is not on the book at all -------------------------------

def test_a_case_with_no_book_still_rates_off_its_ledger(client):
    case_id = _case(client, company="NOT ON THE BOOK")
    _ledger(client, case_id, LEDGER_MONTHS)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert "from_book" not in body
    assert body["annualized_incurred_claims"] > 0


def test_an_account_absent_from_an_uploaded_book_still_rates_off_its_ledger(client):
    _nomada(client)
    case_id = _case(client, company="SOME OTHER COMPANY")
    _ledger(client, case_id, LEDGER_MONTHS)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert "from_book" not in body
    assert body["annualized_incurred_claims"] > 0


# --- matching the case to the account on the book ------------------------

def test_the_account_is_matched_on_the_master_client_not_the_raw_contract(client):
    # _run_analysis's own client filter compares the contract field with
    # ==. A case named for the account still has to find it, so matching
    # is on the resolved master client, trimmed and case-folded.
    _nomada(client)
    case_id = _case(client, company="  nomada events  ")
    _ledger(client, case_id, LEDGER_MONTHS)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert "from_book" in body, body.get("from_book_unavailable")
    assert body["from_book"]["gross_loss_ratio"] is not None


def test_a_name_that_matches_nothing_says_so_and_names_the_near_misses(client):
    # Silence here is what stranded NOMADA: a card falling back to the
    # stale ledger looks exactly like one that never tried.
    _nomada(client)
    case_id = _case(client, company="NOMADA EVENTS ORGANIZING AND MANAGING LLC")
    _ledger(client, case_id, LEDGER_MONTHS)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert "from_book" not in body
    u = body["from_book_unavailable"]
    assert "matches no account on the book" in u["reason"]
    assert "NOMADA EVENTS" in u["closest_accounts_on_the_book"]


def test_no_book_uploaded_is_reported_as_that_rather_than_a_name_problem(client):
    case_id = _case(client, company="ANYTHING")
    _ledger(client, case_id, LEDGER_MONTHS)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert "No membership has been uploaded" in body["from_book_unavailable"]["reason"]


# --- the headline KPI ----------------------------------------------------

def test_the_bench_headline_loss_ratio_comes_from_the_book(client):
    # The KPI strip is the number people read and quote. It showed
    # Method A's 75.6% while the book had the account at 83.6%.
    _nomada(client)
    case_id = _case(client)
    _ledger(client, case_id, LEDGER_MONTHS)

    summary = client.get(f"/cases/{case_id}/renewal-bench-summary").json()
    book = client.get(f"/cases/{case_id}/renewal-rating").json()["from_book"]
    assert summary["kpis"]["actual_loss_ratio"] == book["gross_loss_ratio"]
    assert "from the book" in summary["kpis"]["loss_ratio_basis"]
    assert summary["kpis"]["loss_ratio_net"] == book["net_loss_ratio"]


def test_the_headline_says_when_it_is_falling_back_to_the_ledger(client):
    case_id = _case(client, company="NOT ON THE BOOK")
    _ledger(client, case_id, LEDGER_MONTHS)

    kpis = client.get(f"/cases/{case_id}/renewal-bench-summary").json()["kpis"]
    assert kpis["loss_ratio_basis"] == "Method A, from this case's claims ledger"
    assert kpis.get("loss_ratio_net") is None


# --- both methods, not just the panel above them -------------------------

def test_method_a_reports_the_books_loss_ratio_not_the_ledgers(client):
    # Fixing the panel and leaving Method A alone left the wrong number
    # on the same card, three lines further down.
    _nomada(client)
    case_id = _case(client, premium=114_488.0)
    _ledger(client, case_id, LEDGER_MONTHS)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["rating_source"] == "book"
    assert body["actual_loss_ratio"] == body["from_book"]["gross_loss_ratio"]


def test_both_methods_price_against_the_books_premium(client):
    # Not the case record's. That single substitution was the whole of
    # the +21% quoted on an account needing far more.
    _nomada(client)
    case_id = _case(client, premium=114_488.0)
    _ledger(client, case_id, LEDGER_MONTHS)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    book_premium = body["from_book"]["gross_premium"]
    assert body["current_annual_premium"] == book_premium
    assert body["method_b"]["current_annual_premium"] == book_premium
    # The case record's own figure stays visible so the mismatch can be
    # named without printing the book's number on both sides of it.
    assert body["case_current_annual_premium"] == 114_488.0
    assert body["premium_disagrees_with_book"] is True


def test_the_two_methods_differ_only_in_how_they_reserve(client):
    _nomada(client)
    case_id = _case(client)
    _ledger(client, case_id, LEDGER_MONTHS)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["annualized_paid_and_outstanding"] == \
        body["method_b"]["annualized_paid_and_outstanding"]
    assert body["method_b"]["annualized_incurred_claims"] != body["annualized_incurred_claims"]


def test_reading_the_book_discards_no_month(client):
    _nomada(client)
    case_id = _case(client)
    _ledger(client, case_id, LEDGER_MONTHS)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["excluded_months"] == []
    assert body["excluded_paid"] == 0.0


def test_an_account_on_the_book_needs_no_case_ledger_at_all(client):
    # The book brings its own claims and its own premium; requiring a
    # per-case upload to see them is what made the stale one load.
    _nomada(client)
    case_id = _case(client)

    resp = client.get(f"/cases/{case_id}/renewal-rating")
    assert resp.status_code == 200
    assert resp.json()["rating_source"] == "book"


def test_the_ledger_fallback_still_names_itself(client):
    case_id = _case(client, company="NOT ON THE BOOK")
    _ledger(client, case_id, LEDGER_MONTHS)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["rating_source"] == "case claims ledger"


# --- what date it measures to when nobody says ---------------------------

def test_with_no_report_date_it_measures_to_the_data_not_today(client):
    # This is why NOMADA still looked wrong after the source was fixed.
    # The Loss Ratio screen showed 83.6% because a report date of 15
    # August was typed into it; the card, given none, fell back to today
    # and earned premium through weeks the claims file does not reach -
    # 79.3% on identical data. Today is always wrong in the same
    # direction, and it drifts further every day the extract sits.
    _nomada(client)
    case_id = _case(client)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["from_book"]["as_of"] == AS_OF.isoformat()


def test_the_stored_extract_date_still_wins_over_the_last_claim(client):
    # A file produced on the 20th whose last claim fell on the 14th has
    # six more days of exposure that simply had no claims in them.
    _nomada(client)
    db = client.db_session_local()
    snapshot = db.query(models.PortfolioDataSnapshot).first()
    snapshot.data_as_of_date = date(2026, 8, 20)
    db.commit()
    db.close()
    case_id = _case(client)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["from_book"]["as_of"] == "2026-08-20"


# --- the two premiums are different questions ----------------------------

def _rate_the_census(client, case_id, rates):
    db = client.db_session_local()
    for i, rate in enumerate(rates):
        db.add(models.CensusRecord(case_id=case_id, employee_ref=f"E{i}", category="A",
                                   age=35, relation="employee", existing_annual_rate=rate))
    db.commit()
    db.close()


def test_the_loss_ratio_and_the_renewal_increase_use_different_premiums(client):
    # The book's gross premium is pro-rata for additions and deletions.
    # That is the right denominator for a loss ratio - it is what the
    # account actually earned - and the wrong base for a renewal, which
    # covers a whole year at current rates for the headcount renewing.
    _nomada(client)
    case_id = _case(client)
    _rate_the_census(client, case_id, [10_801.0] * 5 + [5_498.0] * 9)   # NOMADA's own: 103,486

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["current_annual_premium"] == body["from_book"]["gross_premium"]
    assert body["renewal_base_premium"] == 103_487.0
    assert body["expiring_premium"]["source"] == "each renewing member's own existing annual rate"
    # The ratio still ties to the book; only the increase moved.
    assert body["actual_loss_ratio"] == body["from_book"]["gross_loss_ratio"]


def test_the_increase_is_quoted_against_the_annualised_expiring_premium(client):
    _nomada(client)
    case_id = _case(client)
    _rate_the_census(client, case_id, [10_801.0] * 5 + [5_498.0] * 9)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    expected = round((body["required_premium"] / 103_487.0 - 1) * 100, 2)
    assert body["renewal_increase_pct"] == expected
    assert body["method_b"]["renewal_base_premium"] == 103_487.0


def test_without_member_rates_it_falls_back_to_the_books_active_members(client):
    _nomada(client)
    case_id = _case(client)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["expiring_premium"]["source"] == "the book's active members at their annual rates"
    # 16 lives at their own annual rate, not the pro-rata gross.
    assert body["renewal_base_premium"] == 16 * 6_000.0
    assert body["renewal_base_premium"] != body["from_book"]["gross_premium"]


# --- the house ladder ----------------------------------------------------

def test_the_renewal_carries_the_loss_ratio_not_the_absolute_claims(client):
    # The failure this replaces: annualising claims over elapsed days and
    # dividing by the expiring premium puts the numerator on the pro-rata
    # basis and the denominator on the annualised one. On NOMADA the
    # expiring premium is 14.5% larger than the premium the claims were
    # earned against, so 83.6% read as 73.0% and +18.3% became +1.9%.
    _nomada(client)
    case_id = _case(client)
    _rate_the_census(client, case_id, [10_801.0] * 5 + [5_498.0] * 9)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    ladder = body["ladder"]
    assert ladder["loss_ratio"] == body["from_book"]["gross_loss_ratio"]
    assert body["actual_loss_ratio"] == body["from_book"]["gross_loss_ratio"]


def test_inflation_is_added_in_points_not_multiplied(client):
    from app.scoring.rules.renewal_rating import renewal_from_loss_ratio

    out = renewal_from_loss_ratio(0.836, 103_486.0, inflation_pts=0.075, loading_pct=0.23)
    # 83.6 + 7.5 = 91.1, the house convention - not 83.6 x 1.075 = 89.9.
    assert out["trended_loss_ratio"] == 0.911
    assert out["required_share_of_expiring"] == round(0.911 / 0.77, 4)
    assert out["renewal_increase_pct"] == 18.31
    assert out["required_premium"] == 122_436.03


def test_the_loading_is_a_gross_up_not_a_mark_up(client):
    from app.scoring.rules.renewal_rating import renewal_from_loss_ratio

    out = renewal_from_loss_ratio(0.80, 100_000.0, inflation_pts=0.0, loading_pct=0.25)
    # Premium x (1 - loading) is what funds claims, so 80% / 0.75, not
    # 80% x 1.25 - the mark-up understates by the square of the loading.
    # Read off the experience, since 6.7% is under the house floor.
    assert out["experience_share_of_expiring"] == round(0.80 / 0.75, 4)
    assert out["experience_required_premium"] != 100_000.0 * 0.80 * 1.25


def test_both_methods_run_the_same_ladder_off_their_own_reserve(client):
    _nomada(client)
    case_id = _case(client)
    _rate_the_census(client, case_id, [10_801.0] * 5 + [5_498.0] * 9)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    a, b = body, body["method_b"]
    assert a["renewal_base_premium"] == b["renewal_base_premium"]
    # Method B reserves with a flat load, so its ratio and therefore its
    # ask are higher; nothing else about the two differs.
    assert b["actual_loss_ratio"] > a["actual_loss_ratio"]
    assert b["renewal_increase_pct"] > a["renewal_increase_pct"]


def test_a_zero_expiring_premium_is_refused_rather_than_divided_by(client):
    import pytest

    from app.scoring.rules.renewal_rating import renewal_from_loss_ratio

    with pytest.raises(ValueError):
        renewal_from_loss_ratio(0.9, 0.0)
    with pytest.raises(ValueError):
        renewal_from_loss_ratio(0.9, 100_000.0, loading_pct=1.0)


# --- the house floor -----------------------------------------------------

def test_an_account_asking_for_less_than_nine_percent_renews_at_nine():
    from app.scoring.rules.renewal_rating import renewal_from_loss_ratio

    out = renewal_from_loss_ratio(0.50, 100_000.0, inflation_pts=0.075, loading_pct=0.23)
    assert out["experience_increase_pct"] < 9
    assert out["renewal_increase_pct"] == 9.0
    assert out["required_premium"] == 109_000.0
    assert out["floor_applied"] is True


def test_the_floor_never_pulls_a_bigger_ask_down():
    from app.scoring.rules.renewal_rating import renewal_from_loss_ratio

    out = renewal_from_loss_ratio(0.836, 103_486.0, inflation_pts=0.075, loading_pct=0.23)
    assert out["renewal_increase_pct"] == 18.31
    assert out["floor_applied"] is False


def test_the_experience_figure_survives_the_floor():
    # "Needs 9%" and "needs -25% and the house floor is 9%" are different
    # conversations; folding the floor into the experience would leave a
    # single number that cannot tell them apart.
    from app.scoring.rules.renewal_rating import renewal_from_loss_ratio

    out = renewal_from_loss_ratio(0.20, 100_000.0, inflation_pts=0.075, loading_pct=0.23)
    assert out["experience_increase_pct"] == round((0.275 / 0.77 - 1) * 100, 2)
    assert out["experience_required_premium"] < out["required_premium"]
    assert out["renewal_increase_pct"] == 9.0


def test_a_loss_making_account_is_unaffected_by_the_floor():
    from app.scoring.rules.renewal_rating import renewal_from_loss_ratio

    out = renewal_from_loss_ratio(1.20, 100_000.0, inflation_pts=0.075, loading_pct=0.23)
    assert out["floor_applied"] is False
    assert out["renewal_increase_pct"] == out["experience_increase_pct"]


def test_the_floor_can_be_turned_off_for_a_what_if():
    from app.scoring.rules.renewal_rating import renewal_from_loss_ratio

    out = renewal_from_loss_ratio(0.50, 100_000.0, inflation_pts=0.075,
                                  loading_pct=0.23, minimum_increase_pct=None)
    assert out["floor_applied"] is False
    assert out["renewal_increase_pct"] < 0


def test_the_floor_reaches_the_card(client):
    _nomada(client)
    case_id = _case(client)
    _rate_the_census(client, case_id, [10_801.0] * 5 + [5_498.0] * 9)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    for m in (body, body["method_b"]):
        assert "floor_applied" in m
        assert "experience_increase_pct" in m
        assert m["minimum_increase_pct"] == 0.09


# --- inputs are validated before anything is priced ----------------------

def test_an_impossible_loading_blocks_the_price_instead_of_producing_one(client):
    # 86.5% loading leaves 13.5% to fund claims, divides by 0.135, and
    # turned an account running at 44% into an ask of +283%. A wrong
    # input must be reported as one, not priced.
    _nomada(client)
    case_id = _case(client)
    _rate_the_census(client, case_id, [10_801.0] * 5 + [5_498.0] * 9)
    client.patch(f"/cases/{case_id}", json={
        "tpa_fee_pct": 0.065, "commission_pct": 0.60, "hc_fee_pct": 0.15, "qic_fee_pct": 0.05})

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["pricing_blocked"] is True
    # Present but withheld, not absent. Nine other panels read this dict
    # and a missing key took every one of them down with a KeyError.
    assert body["required_premium"] is None
    assert body["renewal_increase_pct"] is None
    assert body["method_b"]["required_premium"] is None
    assert any(p["field"] == "loading_pct" for p in body["pricing_problems"])
    # The account's own experience is still reported - it is not the
    # thing that is wrong.
    assert body["from_book"]["gross_loss_ratio"] is not None


def test_a_sane_case_is_not_blocked(client):
    _nomada(client)
    case_id = _case(client)
    _rate_the_census(client, case_id, [10_801.0] * 5 + [5_498.0] * 9)
    client.patch(f"/cases/{case_id}", json={
        "tpa_fee_pct": 0.055, "commission_pct": 0.10, "hc_fee_pct": 0.045, "qic_fee_pct": 0.03})

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert not body.get("pricing_blocked")
    assert body["required_premium"] > 0


def test_every_bad_input_is_reported_at_once():
    from app.scoring.rules.renewal_rating import pricing_input_problems

    problems = pricing_input_problems(
        loss_ratio=-0.1, expiring_annual_premium=0.0,
        inflation_pts=0.9, loading_pct=0.95, member_count=10)
    fields = {p["field"] for p in problems}
    # Four fixes should not need four submissions.
    assert fields == {"loss_ratio", "expiring_annual_premium", "inflation_pts", "loading_pct"}


def test_a_premium_that_looks_monthly_is_caught():
    from app.scoring.rules.renewal_rating import pricing_input_problems

    problems = pricing_input_problems(expiring_annual_premium=900.0, member_count=14)
    assert any("too low to be an annual medical premium" in p["message"] for p in problems)


def test_a_normal_case_reports_no_problems():
    from app.scoring.rules.renewal_rating import pricing_input_problems

    assert pricing_input_problems(
        loss_ratio=0.836, expiring_annual_premium=103_486.0,
        inflation_pts=0.075, loading_pct=0.23, member_count=14) == []


def test_blocking_the_price_does_not_blank_the_rest_of_the_case(client):
    # Withholding one figure must not take down the member-rate table,
    # the bench KPIs or the new-business comparison - all of which read
    # the same dict.
    _nomada(client)
    case_id = _case(client)
    _rate_the_census(client, case_id, [10_801.0] * 5 + [5_498.0] * 9)
    _ledger(client, case_id, LEDGER_MONTHS)
    client.patch(f"/cases/{case_id}", json={
        "tpa_fee_pct": 0.065, "commission_pct": 0.60, "hc_fee_pct": 0.15, "qic_fee_pct": 0.05})

    rates = client.get(f"/cases/{case_id}/member-rates")
    assert rates.status_code == 200
    body = rates.json()
    assert len(body["members"]) == 14
    # The expiring premium is still there to read and to type against.
    assert body["existing_premium"]["total_existing_premium"] == 103_487.0
    # Override-only, because there is no computed increase to apply.
    assert body["case_renewal_increase_pct"] is None

    assert client.get(f"/cases/{case_id}/renewal-bench-summary").status_code == 200


def test_the_new_business_comparison_works_without_a_case_ledger(client):
    # A renewal priced off the book has no per-case claims ledger, which
    # is exactly the renewal this comparison is most useful for.
    _nomada(client)
    case_id = _case(client)
    _rate_the_census(client, case_id, [10_801.0] * 5 + [5_498.0] * 9)

    resp = client.get(f"/cases/{case_id}/renewal-vs-new-business")
    assert resp.status_code == 200
    body = resp.json()
    assert body["renewal_required_premium"] is not None
