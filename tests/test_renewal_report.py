"""One document for a renewal account - app/reports/renewal_report.py.

The Renewal Bench printed as seven pages of screen cards and left an
underwriter to assemble the argument from figures spread across them.
This is the argument on one page, and every figure comes off the book,
so the document cannot disagree with the Loss Ratio screen it was read
from.
"""
from datetime import date

from app.reports.renewal_report import render_renewal_report
from tests.test_renewal_rating_reads_the_book import (  # noqa: F401 - the client fixture
    AS_OF,
    _case,
    _nomada,
)


def _report(client):
    _nomada(client)
    case_id = _case(client)
    return client.get(f"/cases/{case_id}/renewal-report"), case_id


def test_the_report_is_built_from_the_book(client):
    resp, _ = _report(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["book"]["as_of"] == AS_OF.isoformat()
    assert body["book"]["gross_loss_ratio"] is not None


def test_it_carries_the_claims_analysis_not_just_the_ratio(client):
    resp, _ = _report(client)
    body = resp.json()
    assert body["monthly"], "a renewal document without a claims trend is a number, not an argument"
    assert body["top_claimants"], "who the cost is decides what the renewal conversation is"
    assert body["census"]["member_count"] > 0
    assert body["census"]["age_bands"]


def test_the_document_renders_and_names_its_own_basis(client):
    resp, _ = _report(client)
    html = render_renewal_report(resp.json(), today=date(2026, 8, 31))
    assert "Renewal Review" in html
    # The two loadings on the page are different things and saying so is
    # the difference between a document and a set of numbers.
    assert "book&rsquo;s own expense allowance" in html
    assert "Portfolio Loss Ratio book" in html


def test_the_separator_is_not_printed_as_an_entity(client):
    # esc() on our own markup printed "&middot;" on the page.
    resp, _ = _report(client)
    html = render_renewal_report(resp.json())
    assert "&amp;middot;" not in html


def test_an_account_not_on_the_book_is_refused_rather_than_half_reported(client):
    _nomada(client)
    case_id = _case(client, company="SOMEBODY ELSE")
    resp = client.get(f"/cases/{case_id}/renewal-report")
    assert resp.status_code == 400
    assert "matches no account on the book" in resp.json()["detail"]


def test_the_html_endpoint_serves_a_document(client):
    _, case_id = _report(client)
    resp = client.get(f"/cases/{case_id}/renewal-report.html")
    assert resp.status_code == 200
    assert resp.text.lstrip().startswith("<!doctype html>")


def test_the_document_says_which_loading_each_figure_is_net_of():
    """Two different loadings can legitimately appear in one document -
    the net loss ratio is a BOOK figure and uses the book's expense
    allowance, while the ask is priced on the account's entered fee
    split. A reader who saw 33.0% in the KPI strip and 21.5% in the
    ladder three inches below had no way to tell that from an error.
    """
    from app.reports.renewal_report import _net_ratio_note

    assumed = _net_ratio_note(
        {"loading_pct": 0.33, "loading_is_default": True},
        {"assumptions_used": {"loading_pct": 0.215}})
    assert "house-average" in assumed
    assert "has not been supplied" in assumed
    assert "21.5%" in assumed

    on_file = _net_ratio_note(
        {"loading_pct": 0.215, "loading_is_default": False},
        {"assumptions_used": {"loading_pct": 0.215}})
    assert "own 21.5% expense allowance" in on_file
    # The two agree, so there is nothing to explain.
    assert "The ask below" not in on_file
