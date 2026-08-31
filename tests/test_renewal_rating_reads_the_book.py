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
