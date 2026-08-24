"""Why a case will not price - app/scoring/rules/quote_readiness.py."""
from app.scoring.rules.quote_readiness import REQUIRED_FIELDS, quote_readiness


def _plan(category, product="Gold", network="MSH Platinum", tpa="NAS", name=None):
    return {
        "plan_name": name or f"Category {category}",
        "category": category, "product": product, "network": network, "tpa": tpa,
    }


def _issues(result):
    return [b["issue"] for b in result["blockers"]]


def test_a_fully_configured_case_can_price():
    result = quote_readiness({"A": 10}, [_plan("A")])
    assert result["can_price"] is True
    assert result["blockers"] == []
    assert result["categories"][0]["sources"]["product"] == "benefits"


def test_one_unresolved_category_blocks_the_whole_case():
    # Auto-quoting is all-or-nothing, so a partial count is not progress -
    # reporting "1 of 2 ready" as though it were halfway would mislead.
    result = quote_readiness({"A": 10, "B": 5}, [_plan("A")])
    assert result["ready_category_count"] == 1
    assert result["category_count"] == 2
    assert result["can_price"] is False


def test_a_partially_configured_category_names_the_missing_fields():
    result = quote_readiness({"A": 10}, [_plan("A", tpa=None)])
    row = result["categories"][0]
    assert row["missing"] == ["tpa"]
    assert row["ready"] is False
    assert "tpa" in _issues(result)[0]


def test_two_of_three_fields_is_the_same_as_none():
    result = quote_readiness({"A": 10}, [_plan("A", network=None, tpa=None)])
    assert result["can_price"] is False


def test_a_plan_whose_category_matches_no_member_is_a_mistake_not_a_wait():
    # The failure that never resolves itself: the work is done, the plan
    # is complete, and it is invisible because the letter does not match.
    result = quote_readiness({"A": 10}, [_plan("Z")])
    assert result["orphan_benefit_plans"][0]["category"] == "Z"
    severities = {b["severity"] for b in result["blockers"]}
    assert "mistake" in severities
    assert any("no census member is in" in b["issue"] for b in result["blockers"])


def test_a_plan_with_no_category_at_all_is_reported():
    # A plan called "Category A" with an empty category field matches
    # nothing - it looks correct on screen and is invisible to pricing.
    result = quote_readiness({"A": 10}, [_plan(None, name="Category A")])
    assert result["uncategorised_benefit_plans"][0]["plan_name"] == "Category A"
    assert any("no category set" in b["issue"] for b in result["blockers"])


def test_category_letters_match_case_and_whitespace_insensitively():
    result = quote_readiness({"A": 10}, [_plan(" a ")])
    assert result["can_price"] is True
    assert result["orphan_benefit_plans"] == []


def test_a_census_with_no_categories_says_so_first():
    result = quote_readiness({}, [])
    assert result["can_price"] is False
    assert result["blockers"][0]["issue"] == "No categories on the census"
    assert "Census" in result["blockers"][0]["fix_at"]


def test_an_explicit_offer_beats_the_benefit_plan():
    result = quote_readiness(
        {"A": 10},
        [_plan("A", product="Gold")],
        offers=[{"category": "A", "product": "Platinum", "network": "MSH Platinum", "tpa": "NAS"}],
    )
    row = result["categories"][0]
    assert row["product"] == "Platinum"
    assert row["sources"]["product"] == "offer"


def test_a_prior_quote_fills_in_only_what_nothing_else_does():
    result = quote_readiness(
        {"A": 10},
        [_plan("A", tpa=None)],
        prior_quote_categories=[{"category": "A", "product": "Bronze", "network": "X", "tpa": "NAS"}],
    )
    row = result["categories"][0]
    assert row["product"] == "Gold"          # the plan wins
    assert row["sources"]["product"] == "benefits"
    assert row["tpa"] == "NAS"               # only the gap comes from the prior quote
    assert row["sources"]["tpa"] == "prior quote"
    assert result["can_price"] is True


def test_where_each_value_came_from_is_reported():
    # A figure inherited from a stale prior quote and one the user set
    # deliberately look identical on screen and mean very different things.
    result = quote_readiness(
        {"A": 10}, [],
        prior_quote_categories=[{"category": "A", "product": "Gold", "network": "X", "tpa": "NAS"}],
    )
    assert set(result["categories"][0]["sources"].values()) == {"prior quote"}
    assert result["can_price"] is True


def test_a_case_with_no_plans_at_all_still_lists_every_category():
    result = quote_readiness({"A": 10, "B": 5}, [])
    assert [r["category"] for r in result["categories"]] == ["A", "B"]
    assert all(r["missing"] == list(REQUIRED_FIELDS) for r in result["categories"])
    assert all(r["has_benefit_plan"] is False for r in result["categories"])


def test_blockers_tell_you_where_to_fix_each_one():
    result = quote_readiness({"A": 10, "B": 5}, [_plan("Z")])
    assert all(b["fix_at"] for b in result["blockers"])
    assert all(b["detail"] for b in result["blockers"])


# --- the word that stopped a correctly-configured case pricing ----------

def test_a_tobs_category_wording_matches_a_census_letter():
    """A table of benefits is written for humans and titles its sections
    "Category A" or "Cat A"; a census column just says "A". Both mean the
    same category. Matching them literally meant a case where every field
    was correctly filled in - plan complete, offer set - still priced
    nothing, invisible because of a word.
    """
    from app.api.routes_new_business_rating import _normalize_category as n
    for wording in ("A", "a", " A ", "Cat A", "CAT A", "Category A",
                    "category-A", "Class A", "Plan A", "Tier A", "CAT-A"):
        assert n(wording) == "A", wording


def test_a_non_letter_category_keeps_its_own_name():
    from app.api.routes_new_business_rating import _normalize_category as n
    assert n("Category VIP") == "VIP"
    assert n("Plan Executive") == "EXECUTIVE"


def test_a_category_genuinely_called_class_or_plan_survives():
    # The prefix is only stripped when something is left after it -
    # otherwise these would normalize to nothing at all.
    from app.api.routes_new_business_rating import _normalize_category as n
    assert n("Class") == "CLASS"
    assert n("Plan") == "PLAN"
    assert n("Cat") == "CAT"


def test_a_tob_plan_and_a_census_letter_now_resolve_together():
    # End to end: the TOB parsed its section title as "Cat A", the census
    # says "A", and the case must price.
    from app.api.routes_new_business_rating import _normalize_category as n
    result = quote_readiness(
        {n("A"): 10},
        [{**_plan("A"), "category": n("Cat A")}],
    )
    assert result["can_price"] is True
    assert result["orphan_benefit_plans"] == []
