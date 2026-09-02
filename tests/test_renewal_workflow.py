"""The five steps, and the one distinction that makes them worth having.

"Blocked" and "not started" look the same on a progress bar and mean
completely different things. A renewal whose fee split was never entered
is not waiting its turn - it is stopped, and someone has to go and enter
four numbers. That is the reading the Renewal Bench never gave: the way
to find out an account could not be priced was to scroll thirteen panels
to the pricing card and read that the price had been withheld.
"""
from app.scoring.rules.renewal_workflow import (
    STATE_BLOCKED,
    STATE_DONE,
    STATE_TODO,
    renewal_workflow,
    workflow_state,
)

LOADING_PROBLEM = [{
    "field": "loading_pct",
    "value": None,
    "message": "The renewal loading is not set on this case: the TPA fee has no value.",
}]


def ready(**overrides):
    """A case with everything it needs."""
    facts = {
        "census_member_count": 123,
        "incurred_claims": 877_626.0,
        "claims_source": "the uploaded book",
        "loading_problems": [],
        "loading_pct": 0.215,
        "adjustments_available": 2,
        "adjustments_applied": 0,
        "required_premium": 2_445_710.0,
        "renewal_increase_pct": 146.7,
        "pricing_problems": None,
        "increase_source": "computed increase",
        "quote_settled": True,
    }
    facts.update(overrides)
    return renewal_workflow(**facts)


def states(steps):
    return {s["key"]: s["state"] for s in steps}


class TestTheShape:
    def test_there_are_five_steps_in_order(self):
        assert [s["key"] for s in ready()] == [
            "census", "claims", "adjustments", "pricing", "quote"]

    def test_every_step_can_be_clicked_through_to_a_panel(self):
        for step in ready():
            assert step["anchor"]
            assert step["label"]

    def test_a_finished_renewal_is_five_of_five(self):
        assert set(states(ready()).values()) == {STATE_DONE}
        assert workflow_state(ready())["ready_to_quote"] is True


class TestBlockedIsNotTodo:
    def test_an_unentered_loading_blocks_adjustments_rather_than_leaving_it_pending(self):
        steps = ready(loading_problems=LOADING_PROBLEM, required_premium=None,
                      pricing_problems=LOADING_PROBLEM, increase_source=None)
        assert states(steps)["adjustments"] == STATE_BLOCKED
        assert states(steps)["census"] == STATE_DONE
        assert states(steps)["claims"] == STATE_DONE

    def test_the_blocked_step_says_what_to_go_and_do(self):
        steps = ready(loading_problems=LOADING_PROBLEM, required_premium=None,
                      pricing_problems=LOADING_PROBLEM, increase_source=None)
        blocked = next(s for s in steps if s["state"] == STATE_BLOCKED)
        assert "TPA fee" in blocked["blocker"]

    def test_a_withheld_price_blocks_pricing_too(self):
        steps = ready(loading_problems=LOADING_PROBLEM, required_premium=None,
                      pricing_problems=LOADING_PROBLEM, increase_source=None)
        assert states(steps)["pricing"] == STATE_BLOCKED

    def test_no_claims_yet_is_todo_not_blocked(self):
        # Nothing is wrong with this renewal; it just has not been fed
        # yet. Colouring it as a blocker would send someone looking for a
        # problem that does not exist.
        steps = ready(incurred_claims=None, required_premium=None,
                      pricing_problems=None, increase_source=None)
        assert states(steps)["claims"] == STATE_TODO
        assert states(steps)["adjustments"] == STATE_TODO
        assert states(steps)["pricing"] == STATE_TODO
        assert workflow_state(steps)["blocked"] is False

    def test_no_census_yet_is_todo(self):
        steps = ready(census_member_count=None)
        assert states(steps)["census"] == STATE_TODO
        assert steps[0]["blocker"]


class TestWhatEachStepSays:
    def test_census_reports_the_headcount(self):
        assert "123 lives" in ready()[0]["detail"]

    def test_claims_report_the_incurred_and_where_it_came_from(self):
        detail = ready()[1]["detail"]
        assert "877,626" in detail
        assert "the uploaded book" in detail

    def test_adjustments_report_the_loading_that_will_be_priced_with(self):
        assert "21.5%" in ready()[2]["detail"]

    def test_adjustments_report_how_many_levers_are_applied(self):
        assert "2 available" in ready()[2]["detail"]
        assert "1 of 2 applied" in ready(adjustments_applied=1)[2]["detail"]

    def test_an_account_with_nothing_to_strip_says_so(self):
        assert "Nothing to strip" in ready(adjustments_available=0)[2]["detail"]

    def test_pricing_reports_the_premium_and_the_increase(self):
        detail = ready()[3]["detail"]
        assert "2,445,710" in detail
        assert "+146.7%" in detail

    def test_quote_names_which_increase_is_being_quoted(self):
        assert "computed increase" in ready()[4]["detail"]
        assert "override" in ready(increase_source="override")[4]["detail"]


class TestQuote:
    def test_a_priced_renewal_nobody_has_settled_on_is_not_quoted(self):
        # Every priced renewal HAS a computed ask, so treating that as
        # "quoted" would mark every account on the book finished the
        # moment it was priced, and the step would say nothing at all.
        steps = ready(quote_settled=False)
        assert states(steps)["pricing"] == STATE_DONE
        assert states(steps)["quote"] == STATE_TODO
        assert "not confirmed yet" in steps[4]["detail"]

    def test_an_unpriced_renewal_is_never_quoted(self):
        steps = ready(required_premium=None, pricing_problems=None,
                      increase_source="override", quote_settled=True)
        assert states(steps)["quote"] == STATE_TODO
        assert steps[4]["detail"] == "Nothing to quote yet"


class TestSummary:
    def test_it_names_the_step_the_renewal_is_sitting_on(self):
        steps = ready(loading_problems=LOADING_PROBLEM, required_premium=None,
                      pricing_problems=LOADING_PROBLEM, increase_source=None)
        summary = workflow_state(steps)
        assert summary["current_step"] == "adjustments"
        assert summary["blocked"] is True
        assert "TPA fee" in summary["blocker"]
        assert summary["ready_to_quote"] is False

    def test_it_counts_what_is_done(self):
        summary = workflow_state(ready(quote_settled=False))
        assert summary["steps_done"] == 4
        assert summary["steps_total"] == 5
        assert summary["current_step"] == "quote"

    def test_a_finished_renewal_reports_its_last_step_as_current(self):
        summary = workflow_state(ready())
        assert summary["current_step"] == "quote"
        assert summary["blocked"] is False
        assert summary["blocker"] is None
