"""An underwriter can quote a different increase to the one the
experience produces - and every screen quotes the same one.

A renewal is a negotiation. An account can be held below what its claims
ask for to keep a relationship, or pushed above it. Before this the
portal only ever quoted the arithmetic, so the figure on the renewal
list, the member rates and the printed review was not the figure being
sent to the client.

The override never touches the computed number. Both come back, with
increase_source saying which is being quoted - the same way the 9% floor
reports experience_increase_pct beside renewal_increase_pct. "This
account needs 18%" and "we are asking 12%" are different facts and one
number cannot carry both.
"""
from tests.test_account_loading_is_never_assumed import _renewal_case, HOUSE_FEES  # noqa: F401


def _set_override(client, case_id, pct):
    return client.patch(f"/cases/{case_id}",
                        json={"renewal_increase_override_pct": pct})


def test_without_an_override_the_experience_is_quoted(client):
    case_id = _renewal_case(client, on_the_book=True)
    body = client.get(f"/cases/{case_id}/renewal-rating").json()

    assert body["increase_source"] == "computed"
    assert "computed_increase_pct" not in body


def test_an_override_replaces_the_ask_and_the_required_premium(client):
    case_id = _renewal_case(client, on_the_book=True)
    computed = client.get(f"/cases/{case_id}/renewal-rating").json()

    _set_override(client, case_id, 12.0)
    body = client.get(f"/cases/{case_id}/renewal-rating").json()

    assert body["increase_source"] == "override"
    assert body["renewal_increase_pct"] == 12.0
    assert body["required_premium"] == round(
        body["renewal_base_premium"] * 1.12, 2)
    # The account's own ask is still reported, untouched.
    assert body["computed_increase_pct"] == computed["renewal_increase_pct"]
    assert body["computed_required_premium"] == computed["required_premium"]


def test_the_override_reaches_the_member_rates_grid(client):
    case_id = _renewal_case(client, on_the_book=True)
    _set_override(client, case_id, 12.0)

    grid = client.get(f"/cases/{case_id}/member-rates").json()
    assert grid["case_renewal_increase_pct"] == 12.0
    assert grid["increase_source"] == "override"
    assert grid["computed_increase_pct"] is not None


def test_the_override_reaches_the_renewal_list(client):
    # The list and the case must not disagree - that was the whole point
    # of pointing the board at Method 1 in the first place.
    case_id = _renewal_case(client, on_the_book=True)
    _set_override(client, case_id, 12.0)

    row = next(r for r in client.get("/cases/renewal-summary").json()["cases"]
               if r["id"] == case_id)
    bench = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert row["suggested_increase_pct"] == 12.0
    assert row["suggested_increase_pct"] == bench["renewal_increase_pct"]


def test_the_override_reaches_the_printed_review(client):
    case_id = _renewal_case(client, on_the_book=True)
    _set_override(client, case_id, 12.0)

    payload = client.get(f"/cases/{case_id}/renewal-report").json()
    assert payload["rating"]["renewal_increase_pct"] == 12.0


def test_an_override_below_the_house_floor_is_still_honoured(client):
    # The floor is what the house asks for absent a decision. An
    # underwriter deciding otherwise IS the decision, so it stands - and
    # the computed figure beside it still shows the floor was in play.
    case_id = _renewal_case(client, premium=100_000_000.0, on_the_book=True)
    _set_override(client, case_id, 3.0)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["renewal_increase_pct"] == 3.0
    assert body["computed_increase_pct"] == 9.0


def test_clearing_the_override_goes_back_to_the_experience(client):
    case_id = _renewal_case(client, on_the_book=True)
    computed = client.get(f"/cases/{case_id}/renewal-rating").json()["renewal_increase_pct"]

    _set_override(client, case_id, 12.0)
    _set_override(client, case_id, None)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["increase_source"] == "computed"
    assert body["renewal_increase_pct"] == computed


def test_an_override_does_not_resurrect_a_blocked_price(client):
    # No fee split means no price at all. An override is a decision about
    # the ASK, not a way past a missing input - the premium it would be
    # applied to does not exist.
    case_id = _renewal_case(client, fees=None, on_the_book=True)
    _set_override(client, case_id, 12.0)

    body = client.get(f"/cases/{case_id}/renewal-rating").json()
    assert body["pricing_blocked"] is True
    assert body["renewal_increase_pct"] is None


def test_the_new_business_rate_tab_is_available_on_a_renewal(client):
    # 0d20ec5 hid it, on the reasoning that New Business and Renewal are
    # separate workflows. The rate card is not a workflow though - it is a
    # price, and a renewal needs it: renewal-vs-new-business was already
    # quoting a card price the underwriter had no way to see or configure.
    import pathlib
    markup = (pathlib.Path(__file__).resolve().parent.parent
              / "app" / "static" / "index.html").read_text()
    assert "if (t.id === 'new-business') return c.business_type !== 'existing';" not in markup
    # Still headed for what it is on each side.
    assert "'New Business Rate' : t.label" in markup


def test_the_card_price_is_reachable_for_a_renewal_case(client):
    # The comparison behind that tab has to actually answer on a renewal,
    # not 404 - that is the whole reason for showing it.
    case_id = _renewal_case(client, on_the_book=True)
    resp = client.get(f"/cases/{case_id}/renewal-vs-new-business")
    assert resp.status_code == 200


def test_a_renewal_is_not_offered_a_third_partys_price_grid(client):
    # That import writes straight into Member Rates and overwrites what is
    # there - and on a renewal what is there is the account's own expiring
    # rates off the book or the uploaded census. It was also the box that
    # kept failing, because the file reached for on a renewal is a Plan
    # Details export with no premiums in it. HealthCross's own rate card,
    # which is what a renewal needs, stays.
    import pathlib
    markup = (pathlib.Path(__file__).resolve().parent.parent
              / "app" / "static" / "index.html").read_text()
    assert "${c.business_type === 'existing' ? '' : `" in markup
    # The handler survives the input not being on the page.
    assert "if (!input) return;" in markup
    # The card itself - the thing that works - is untouched.
    assert 'id="nb-file-pricing"' in markup
    assert 'id="nb-file-variants"' in markup


def test_the_new_business_tab_leads_with_the_benefits_import():
    # "Drop the benefits export and get a price" configures every category
    # and prices it in one step. It used to sit at the BOTTOM of the tab,
    # under the admin rate-card uploads and the manual dropdowns, so the
    # page opened on the two things a user does least and buried the one
    # they came for - which is how a Plan Details file kept being fed to
    # the price-grid box at the top instead.
    import pathlib
    markup = (pathlib.Path(__file__).resolve().parent.parent
              / "app" / "static" / "index.html").read_text()

    start = markup.index("Start here &mdash; price the uploaded benefits")
    manual = markup.index("Or set each category by hand")
    admin = markup.index("${uploadCard}\n    <div id=\"nb-readiness-area\">")
    assert start < manual < admin, "the import must come before the manual setup and the admin upload"


def test_the_renewal_documents_are_all_on_the_renewal_bench():
    # A renewal's three outputs sat on two different tabs - the Review on
    # the Renewal Bench, the Internal and External summaries on CLAIMS -
    # with nothing saying which was which or who each was for.
    import pathlib
    markup = (pathlib.Path(__file__).resolve().parent.parent
              / "app" / "static" / "index.html").read_text()

    bench = markup.index('data-tab="renewal-bench"')
    for label in ("Renewal Review &mdash; the meeting",
                  "Internal &mdash; the file",
                  "External &mdash; the client"):
        assert markup.index(label) > bench, f"{label} is not on the Renewal Bench"
    # And not left behind on the claims tab.
    claims = markup.index('data-tab="claims"')
    assert markup.index("printRenewalClientSummary('internal')") > bench > claims
