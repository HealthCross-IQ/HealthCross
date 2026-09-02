"""Two documents, from one set of figures.

A renewal had three printouts across two tabs - a "one page" Review that
printed across four, an Internal summary and an External one - plus two
"print this tab" buttons. Nothing said which was which, and the browser
stamped its own date, URL and page counter on every one of them.

Now: a Summary (the decision) and a Report (the file). Section 1 of the
Report IS the Summary, built by the same function, so the short document
and the long one cannot quote different numbers for the same account.
"""
from datetime import date

from app.reports.renewal_report import render_renewal_report, render_renewal_summary
from tests.test_renewal_rating_reads_the_book import (  # noqa: F401 - the client fixture
    _case,
    _nomada,
    _rate_the_census,
)


def _payload(client, override=None):
    _nomada(client)
    case_id = _case(client)
    _rate_the_census(client, case_id, [10_801.0] * 5 + [5_498.0] * 9)
    if override is not None:
        client.patch(f"/cases/{case_id}", json={"renewal_increase_override_pct": override})
    return case_id, client.get(f"/cases/{case_id}/renewal-report").json()


# --- the two documents ---------------------------------------------------

def test_the_summary_is_the_decision_and_nothing_else(client):
    _, payload = _payload(client)
    html = render_renewal_summary(payload)

    for wanted in ("Renewal Summary", "The renewal ask", "Premium"):
        assert wanted in html
    # The explaining sections belong to the report, not here.
    for unwanted in ("Census</h2>", "Benefits</h2>", "Claims</h2>", "Basis</h2>"):
        assert unwanted not in html


def test_the_report_carries_the_census_benefits_and_claims(client):
    _, payload = _payload(client)
    html = render_renewal_report(payload)

    for section in ("Census", "Benefits", "Claims", "Basis"):
        assert f"<h2>{section}</h2>" in html
    assert 'class="sec-no">02' in html
    assert 'class="sec-no">05' in html


def test_both_documents_quote_the_same_ask(client):
    # The whole reason section 1 is a shared function.
    _, payload = _payload(client)
    summary, report = render_renewal_summary(payload), render_renewal_report(payload)

    inc = payload["rating"]["renewal_increase_pct"]
    needle = f'{abs(inc)}%'
    assert needle in summary and needle in report


# --- the override defect the samples showed -----------------------------

def test_an_overridden_ask_is_never_printed_beside_the_computed_one_unlabelled(client):
    # The sample PDF showed "1,176,702 / +60.0%" in its KPI strip and
    # "1,221,425 / +66.08%" in its ladder, three inches apart, with
    # nothing distinguishing them - and a footnote reading "166.1% of the
    # annualised expiring premium" against a figure that was 160%.
    _, payload = _payload(client, override=12.0)
    html = render_renewal_summary(payload)

    assert payload["rating"]["increase_source"] == "override"
    # Both figures still appear - but each is now named.
    assert "Quoted &mdash; what we are asking" in html
    assert "Experience &mdash; what the account asks for" in html
    assert "Quoted increase" in html
    assert "an override" in html
    # And the share footnote no longer describes the other number.
    assert "the experience asks" in html


def test_without_an_override_no_comparison_is_drawn(client):
    _, payload = _payload(client)
    html = render_renewal_summary(payload)

    assert "Quoted &mdash; what we are asking" not in html
    assert "Renewal increase" in html


# --- read or download, never "print" ------------------------------------

def test_each_document_carries_its_own_toolbar_and_download(client):
    _, payload = _payload(client)
    for html in (render_renewal_summary(payload), render_renewal_report(payload)):
        assert 'class="toolbar"' in html
        assert "Download PDF" in html
        # The toolbar is screen furniture and must never reach the paper.
        assert ".toolbar{display:none}" in html
    # A4 with real margins, so the page is laid out rather than scaled.
    assert "@page{size:A4" in render_renewal_report(payload)


def test_the_client_view_folds_away_the_internal_sections(client):
    _, payload = _payload(client)
    html = render_renewal_report(payload)

    assert 'data-view="client"] .internal-only{display:none' in html
    assert "sec internal-only" in html
    assert "setView('client')" in html


# --- and the endpoints serve them ---------------------------------------

def test_both_documents_are_served(client):
    case_id, _ = _payload(client)
    for path in ("renewal-summary.html", "renewal-report.html"):
        resp = client.get(f"/cases/{case_id}/{path}")
        assert resp.status_code == 200, path
        assert resp.text.lstrip().startswith("<!doctype html>")


# --- and it has to fit on paper -----------------------------------------

def test_the_print_rules_constrain_the_page_to_A4(client):
    # The screen paper is 920px wide, wider than A4's printable area, and
    # the first print of this ran off the right edge - taking the masthead
    # bar and the fourth KPI with it. Measured in Chromium: the summary is
    # one A4 page without an override and two with one, where the quoted-
    # against-computed comparison earns the space.
    _, payload = _payload(client)
    css = render_renewal_summary(payload)

    assert "@page{size:A4" in css
    assert ".paper,.doc{max-width:100%" in css
    # The toolbar is screen furniture; the browser's own header is what
    # made the old documents look like screenshots.
    assert ".toolbar{display:none}" in css
