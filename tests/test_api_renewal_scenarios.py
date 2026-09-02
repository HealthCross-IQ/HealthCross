"""The scenarios panel and the five-step bar, end to end.

The single property worth defending: every scenario is Method 1's own
ladder with a different incurred figure. If any row could be produced by
some other arithmetic, the panel would put two renewal premiums on one
screen - which is the failure the Renewal Bench hero already had once.
"""
from datetime import date

import pytest

from app.models import db_models as models

HOUSE_FEES = {"tpa_fee_pct": 0.065, "commission_pct": 0.15,
              "hc_fee_pct": 0.065, "qic_fee_pct": 0.05}

POLICY_START = date(2025, 10, 1)


def _create_case(client, **overrides):
    payload = {"broker_name": "Broker", "company_name": "Amazonico", "industry": "trading"}
    payload.update(overrides)
    return client.post("/cases", json=payload).json()["id"]


def _ledger(client, case_id, lines):
    """lines: [(month, amount, patient_id)]"""
    db = client.db_session_local()
    for i, (month, amount, patient) in enumerate(lines):
        db.add(models.ClaimsLedgerEntry(
            case_id=case_id, patient_id=patient, claim_id=f"C{i}",
            claim_status="Paid Claims",
            policy_start_date=POLICY_START,
            policy_end_date=date(2026, 10, 1),
            date_of_treatment=date(month[0], month[1], 10),
            ip_op_maternity="IP", diagnosis_code="J209",
            diagnosis_description="Acute bronchitis", final_amount=amount,
        ))
    db.commit()
    db.close()


def _census(client, case_id, count=20):
    db = client.db_session_local()
    for i in range(count):
        db.add(models.CensusRecord(case_id=case_id, employee_ref=f"E{i}", age=35,
                                   relation="employee"))
    db.commit()
    db.close()


@pytest.fixture()
def case(client):
    """A case with one catastrophic claim and several ordinary ones."""
    case_id = _create_case(client)
    client.patch(f"/cases/{case_id}",
                 json={**HOUSE_FEES, "current_annual_premium": 3_000_000})
    _ledger(client, case_id, [
        ((2025, 10), 120_000.0, "BIG"),
        ((2025, 11), 40_000.0, "P1"),
        ((2025, 12), 40_000.0, "P2"),
        ((2026, 1), 40_000.0, "P3"),
    ])
    _census(client, case_id)
    return case_id


def scenarios(client, case_id, **params):
    resp = client.get(f"/cases/{case_id}/renewal-scenarios", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def by_key(body, key):
    return next(r for r in body["scenarios"] if r["key"] == key)


class TestEveryRowIsMethod1:
    def test_as_reported_is_the_renewal_rating_itself(self, client, case):
        body = scenarios(client, case)
        rating = client.get(f"/cases/{case}/renewal-rating").json()
        row = by_key(body, "as_reported")
        assert row["required_premium"] == rating["required_premium"]
        assert row["renewal_increase_pct"] == rating["renewal_increase_pct"]

    def test_every_row_is_the_ladder_on_its_own_adjusted_incurred(self, client, case):
        from app.scoring.rules.renewal_rating import renewal_from_loss_ratio

        body = scenarios(client, case)
        for row in body["scenarios"]:
            if row["key"] == "override":
                continue
            ladder = renewal_from_loss_ratio(
                row["adjusted_incurred"] / body["expiring_annual_premium"],
                body["expiring_annual_premium"],
                body["inflation_pts"],
                body["loading_pct"],
            )
            assert row["required_premium"] == ladder["required_premium"]

    def test_the_removal_comes_straight_off_the_incurred(self, client, case):
        body = scenarios(client, case)
        row = by_key(body, "large_claims")
        assert row["adjusted_incurred"] == pytest.approx(
            body["incurred_claims"] - row["removed"], abs=0.01)


class TestWhatCanBeStripped:
    def test_the_catastrophic_claim_is_the_large_claim_lever(self, client, case):
        body = scenarios(client, case)
        large = next(a for a in body["adjustments"] if a["key"] == "large_claims")
        assert large["available"] is True
        assert large["amount"] == 120_000.0
        assert "1 claim lines" in large["note"] or "1 claim" in large["note"]

    def test_a_claim_amount_is_stripped_without_being_annualised(self, client, case):
        # The point of stripping a catastrophic admission is that it
        # happened once. Annualising it before removing it would take out
        # twelve of an event that occurred one time.
        body = scenarios(client, case)
        assert by_key(body, "large_claims")["removed"] == 120_000.0

    def test_raising_the_threshold_leaves_nothing_to_strip(self, client, case):
        body = scenarios(client, case, large_claim_threshold=500_000)
        large = next(a for a in body["adjustments"] if a["key"] == "large_claims")
        assert large["available"] is False
        assert large["amount"] == 0.0
        # And the row is still shown, doing nothing, with its reason.
        assert by_key(body, "large_claims")["required_premium"] == \
            by_key(body, "as_reported")["required_premium"]

    def test_a_lever_with_nothing_behind_it_says_why(self, client, case):
        body = scenarios(client, case)
        notes = {a["key"]: a["note"] for a in body["adjustments"]}
        assert "one-off" in notes["non_recurring"]
        assert "benefit table" in notes["benefit_change"]

    def test_all_four_levers_are_always_reported(self, client, case):
        body = scenarios(client, case)
        assert [a["key"] for a in body["adjustments"]] == [
            "large_claims", "exiting_members", "non_recurring", "benefit_change"]

    def test_a_case_on_its_own_ledger_cannot_read_leavers(self, client, case):
        body = scenarios(client, case)
        leavers = next(a for a in body["adjustments"] if a["key"] == "exiting_members")
        assert leavers["available"] is False
        assert "ledger" in leavers["note"]


class TestBlocked:
    def test_an_unentered_loading_withholds_every_scenario(self, client):
        case_id = _create_case(client, company_name="No Fees Ltd")
        client.patch(f"/cases/{case_id}", json={"current_annual_premium": 3_000_000})
        _ledger(client, case_id, [((2025, 10), 100_000.0, "P1"),
                                  ((2025, 11), 100_000.0, "P2"),
                                  ((2025, 12), 100_000.0, "P3"),
                                  ((2026, 1), 100_000.0, "P4")])
        _census(client, case_id)

        body = scenarios(client, case_id)
        assert body["pricing_blocked"] is True
        assert body["scenarios"] == []
        assert body["pricing_problems"]
        # The levers are still reported - what is withheld is the price,
        # not the account.
        assert len(body["adjustments"]) == 4

    def test_a_case_with_no_experience_at_all_is_a_400(self, client):
        case_id = _create_case(client, company_name="Empty Ltd")
        client.patch(f"/cases/{case_id}", json={**HOUSE_FEES, "current_annual_premium": 1_000})
        resp = client.get(f"/cases/{case_id}/renewal-scenarios")
        assert resp.status_code == 400

    def test_an_unknown_case_is_a_404(self, client):
        assert client.get("/cases/999999/renewal-scenarios").status_code == 404


class TestOverride:
    def test_an_override_appears_as_its_own_row_beside_the_computed_ones(self, client, case):
        client.patch(f"/cases/{case}", json={"renewal_increase_override_pct": 12.0})
        body = scenarios(client, case)
        override = by_key(body, "override")
        assert override["renewal_increase_pct"] == 12.0
        assert override["required_premium"] == pytest.approx(3_000_000 * 1.12, abs=1)
        # It is a decision, not experience, and must not pretend to have
        # a loss ratio behind it.
        assert override["loss_ratio"] is None
        assert override["note"]

    def test_the_computed_rows_are_untouched_by_an_override(self, client, case):
        before = by_key(scenarios(client, case), "large_claims")["required_premium"]
        client.patch(f"/cases/{case}", json={"renewal_increase_override_pct": 12.0})
        after = by_key(scenarios(client, case), "large_claims")["required_premium"]
        assert after == before


class TestOneMethod1:
    def test_a_case_on_its_own_ledger_is_priced_by_the_house_ladder(self, client, case):
        # calculate_renewal_rating trends by MULTIPLYING the claims;
        # the house ladder ADDS inflation to the loss ratio in points.
        # The book path overlaid the ladder and the ledger path did not,
        # so Method 1 meant two different formulas depending on which
        # upload the case happened to match - on this case, 4,680,440 of
        # incurred against 3,000,000 of premium is +108.3% one way and
        # +113.65% the other, AED 160,552 apart.
        from app.scoring.rules.renewal_rating import renewal_from_loss_ratio

        rating = client.get(f"/cases/{case}/renewal-rating").json()
        assert rating["rating_source"] == "case claims ledger"

        ladder = renewal_from_loss_ratio(
            rating["actual_loss_ratio"],
            rating["renewal_base_premium"],
            rating["assumptions_used"]["inflation_pct"],
            rating["assumptions_used"]["loading_pct"],
        )
        assert rating["required_premium"] == ladder["required_premium"]
        assert rating["renewal_increase_pct"] == ladder["renewal_increase_pct"]

    def test_the_multiply_the_claims_answer_is_not_what_is_quoted(self, client, case):
        rating = client.get(f"/cases/{case}/renewal-rating").json()
        assumptions = rating["assumptions_used"]
        multiplied = (rating["annualized_incurred_claims"] * (1 + assumptions["inflation_pct"])
                      / (1 - assumptions["loading_pct"]))
        # The two agree only at a 100% loss ratio. Below it the points
        # version asks for MORE (7.5 points of premium is more than 7.5%
        # of a small claims figure); above it, less. Either way the
        # quoted number is the ladder's, not this one.
        lr = rating["actual_loss_ratio"]
        assert lr != pytest.approx(1.0, abs=0.01)
        assert rating["required_premium"] != pytest.approx(multiplied, abs=1.0)
        if lr < 1.0:
            assert rating["required_premium"] > multiplied
        else:
            assert rating["required_premium"] < multiplied

    def test_both_methods_run_the_same_ladder_on_the_ledger_path_too(self, client, case):
        from app.scoring.rules.renewal_rating import renewal_from_loss_ratio

        rating = client.get(f"/cases/{case}/renewal-rating").json()
        method_b = rating["method_b"]
        ladder_b = renewal_from_loss_ratio(
            method_b["actual_loss_ratio"],
            method_b["renewal_base_premium"],
            rating["assumptions_used"]["inflation_pct"],
            rating["assumptions_used"]["loading_pct"],
        )
        assert method_b["required_premium"] == ladder_b["required_premium"]

    def test_the_method_gap_is_between_the_two_premiums_actually_shown(self, client, case):
        rating = client.get(f"/cases/{case}/renewal-rating").json()
        assert rating["method_gap"] == pytest.approx(
            rating["method_b"]["required_premium"] - rating["required_premium"], abs=0.01)

    def test_the_bench_hero_the_scenarios_and_the_rating_all_agree(self, client, case):
        # The three places an underwriter reads a renewal premium.
        rating = client.get(f"/cases/{case}/renewal-rating").json()
        bench = client.get(f"/cases/{case}/renewal-bench-summary").json()
        base = by_key(scenarios(client, case), "as_reported")
        assert bench["drivers"]["recommended_premium"] == rating["required_premium"]
        assert base["required_premium"] == rating["required_premium"]


class TestTheStepBar:
    def test_the_bench_summary_carries_five_steps(self, client, case):
        body = client.get(f"/cases/{case}/renewal-bench-summary").json()
        assert [s["key"] for s in body["workflow"]] == [
            "census", "claims", "adjustments", "pricing", "quote"]

    def test_a_priced_case_is_four_of_five_until_someone_settles_the_ask(self, client, case):
        state = client.get(f"/cases/{case}/renewal-bench-summary").json()["workflow_state"]
        assert state["steps_done"] == 4
        assert state["current_step"] == "quote"
        assert state["blocked"] is False

    def test_recording_an_override_settles_the_quote(self, client, case):
        client.patch(f"/cases/{case}", json={"renewal_increase_override_pct": 12.0})
        state = client.get(f"/cases/{case}/renewal-bench-summary").json()["workflow_state"]
        assert state["ready_to_quote"] is True
        assert state["steps_done"] == 5

    def test_an_unentered_loading_blocks_at_adjustments_not_at_pricing(self, client):
        # The reading the bench never gave: the way to find out an account
        # could not be priced was to scroll thirteen panels down and read
        # that the price had been withheld.
        case_id = _create_case(client, company_name="No Fees Ltd")
        client.patch(f"/cases/{case_id}", json={"current_annual_premium": 3_000_000})
        _ledger(client, case_id, [((2025, 10), 100_000.0, "P1"),
                                  ((2025, 11), 100_000.0, "P2"),
                                  ((2025, 12), 100_000.0, "P3"),
                                  ((2026, 1), 100_000.0, "P4")])
        _census(client, case_id)

        body = client.get(f"/cases/{case_id}/renewal-bench-summary").json()
        states = {s["key"]: s["state"] for s in body["workflow"]}
        assert states["census"] == "done"
        assert states["claims"] == "done"
        assert states["adjustments"] == "blocked"
        assert body["workflow_state"]["current_step"] == "adjustments"
        assert "loading" in body["workflow_state"]["blocker"].lower()

    def test_every_step_can_be_clicked_through_to_a_panel(self, client, case):
        body = client.get(f"/cases/{case}/renewal-bench-summary").json()
        for step in body["workflow"]:
            assert step["anchor"]
            assert step["detail"]

    def test_the_steps_reduce_the_page_rather_than_only_scrolling_it(self):
        # A wizard that never hides anything is a labelled scrollbar. Each
        # panel declares which step it belongs to, and the filter is
        # always reversible with the hidden count on screen.
        import pathlib
        markup = (pathlib.Path(__file__).resolve().parent.parent
                  / "app" / "static" / "index.html").read_text()
        for step in ("census", "claims", "adjustments", "pricing", "quote"):
            assert f'data-rb-step="{step}"' in markup
        assert "function rbSelectStep(" in markup
        assert "rbShowEveryPanel()" in markup
        # An author display rule outranks the browser's own [hidden], so
        # the .rb-grid-2 panels stayed on screen while el.hidden was true.
        assert '.rb-scope [data-rb-step][hidden] { display: none !important; }' in markup

    def test_a_panel_reload_does_not_silently_unfilter_the_page(self):
        import pathlib
        markup = (pathlib.Path(__file__).resolve().parent.parent
                  / "app" / "static" / "index.html").read_text()
        # The step bar is rebuilt on every summary load; the filter has to
        # be put back on it or a reload un-filters under the reader.
        assert markup.count("_rbApplyStepFilter()") >= 2

    def test_a_withheld_price_does_not_blank_the_whole_bench(self):
        # A blocked case returns no drivers, and the premium card read
        # them anyway - which threw, which took down the loader that
        # paints the header, the KPI strip and the step bar. The account
        # with the most to say showed an empty page, with nothing naming
        # the fee split that was stopping it.
        import pathlib
        markup = (pathlib.Path(__file__).resolve().parent.parent
                  / "app" / "static" / "index.html").read_text()
        assert "if (!r.drivers) {" in markup
        assert "Withheld. A renewal is not priced on an assumed loading" in markup

    def test_the_step_bar_survives_a_blocked_case_in_the_payload(self, client):
        case_id = _create_case(client, company_name="No Fees Ltd")
        client.patch(f"/cases/{case_id}", json={"current_annual_premium": 3_000_000})
        _ledger(client, case_id, [((2025, 10), 100_000.0, "P1"),
                                  ((2025, 11), 100_000.0, "P2"),
                                  ((2025, 12), 100_000.0, "P3"),
                                  ((2026, 1), 100_000.0, "P4")])
        _census(client, case_id)
        body = client.get(f"/cases/{case_id}/renewal-bench-summary").json()
        assert body["drivers"] is None
        assert len(body["workflow"]) == 5
        # Claims are on file even though the priced incurred is withheld.
        claims = next(s for s in body["workflow"] if s["key"] == "claims")
        assert claims["state"] == "done"
        assert "ledger" in claims["detail"]


class TestTheUploadBlock:
    """Roughly 400px of drop zone and accordion opened every case, on
    every visit, forever - right on day one and wrong on every day after,
    when the files are in and the work is reading the numbers."""

    def _markup(self):
        import pathlib
        return (pathlib.Path(__file__).resolve().parent.parent
                / "app" / "static" / "index.html").read_text()

    def test_the_uploads_collapse_behind_one_summary_line(self):
        markup = self._markup()
        assert '<details class="case-uploads" id="case-uploads">' in markup
        assert 'id="case-uploads-summary"' in markup
        assert "function _renderUploadsSummary(" in markup

    def test_it_opens_by_itself_only_when_something_required_is_missing(self):
        markup = self._markup()
        assert "block.open = missing.length > 0;" in markup
        # Claims, quote and scorecard are useful; their absence is not a
        # reason to keep the upload UI open.
        assert "s.has_claims_ledger" in markup

    def test_a_click_is_the_user_but_a_toggle_is_not(self):
        # toggle fires on our own steering too, and would mark every
        # automatic open as a decision the user made.
        markup = self._markup()
        assert "onclick=\"document.getElementById('case-uploads').dataset.userToggled = '1'\"" in markup
        assert "if (!block.dataset.userToggled)" in markup

    def test_opening_a_renewal_from_the_book_still_reaches_the_drop_zone(self):
        # scrollIntoView on an element inside a closed <details> does
        # nothing, so the block is opened first.
        markup = self._markup()
        i = markup.find("const uploads = document.getElementById('case-uploads');")
        j = markup.find("zone.scrollIntoView", i)
        assert i != -1 and j != -1 and j > i
