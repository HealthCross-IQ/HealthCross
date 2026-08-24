"""What the price should have been, and what actually went out -
app/scoring/rules/price_comparison.py.
"""
from app.scoring.rules.price_comparison import (
    DISCOUNT_GIVEN,
    PREMIUM_ADDED,
    PRICE_HELD,
    compare_prices,
    discount_effect,
    implied_loss_ratio,
    issued_price_from_plans,
)

LOADING = 0.265


def test_loss_ratio_is_measured_against_the_premium_that_funds_claims():
    # Never against gross premium: the loading is already committed to
    # commission, TPA, admin and fees and cannot pay a claim. Measuring
    # against gross flatters every account by exactly the loading.
    assert implied_loss_ratio(1_000_000, 735_000, LOADING) == 1.0
    assert round(implied_loss_ratio(1_000_000, 735_000, 0.0), 3) == 0.735


def test_a_five_percent_discount_costs_more_than_five_percent_of_loss_ratio():
    # The discount comes off the claims-funding, not off margin, and the
    # expected claims do not move - so the ratio rises by 1/0.95.
    effect = discount_effect(
        card_price=1_000_000, issued_price=950_000, expected_claims=661_500, loading_pct=LOADING
    )
    assert effect["direction"] == DISCOUNT_GIVEN
    assert effect["pct"] == -0.05
    assert effect["implied_loss_ratio_before"] == 0.9
    # 90% / 0.95 = 94.7%, not 95%.
    assert round(effect["implied_loss_ratio_after"], 3) == 0.947
    assert round(effect["loss_ratio_movement"], 3) == 0.047


def test_an_account_at_ninety_five_goes_to_a_hundred_on_five_percent():
    effect = discount_effect(1_000_000, 950_000, 698_250, LOADING)
    assert round(effect["implied_loss_ratio_after"], 2) == 1.00


def test_a_quote_issued_above_the_card_is_reported_too():
    # Just as much a decision somebody made, and just as invisible on
    # every other screen.
    effect = discount_effect(1_000_000, 1_080_000, 661_500, LOADING)
    assert effect["direction"] == PREMIUM_ADDED
    assert effect["pct"] == 0.08
    assert effect["implied_loss_ratio_after"] < effect["implied_loss_ratio_before"]


def test_a_rounding_difference_is_not_a_discount():
    # The two prices are one number written twice - not a decision.
    assert discount_effect(1_000_000, 999_800, 661_500, LOADING)["direction"] == PRICE_HELD


def test_no_issued_quote_is_not_a_hundred_percent_discount():
    assert discount_effect(1_000_000, None, 661_500, LOADING) is None


# --- the four prices together -------------------------------------------

def test_the_three_gaps_answer_three_different_questions():
    result = compare_prices(
        expected_claims=661_500, risk_price=900_000, card_price=1_000_000,
        issued_price=950_000, loading_pct=LOADING, member_count=100,
    )
    # The card charges 11.1% more than the book says this group costs.
    assert round(result["gaps"]["card_vs_risk_pct"], 3) == 0.111
    # 5% was given away in the room.
    assert result["gaps"]["issued_vs_card_pct"] == -0.05
    # And the deal actually done still clears the risk price by 5.6%.
    assert round(result["gaps"]["issued_vs_risk_pct"], 3) == 0.056


def test_a_missing_price_is_reported_as_missing_not_as_zero():
    # A case with no issued quote has not been discounted by 100%, and a
    # zero here would say exactly that on screen.
    result = compare_prices(661_500, 900_000, 1_000_000, None, LOADING, 100)
    assert result["prices"]["issued_price"] is None
    assert result["implied_loss_ratio"]["at_issued_price"] is None
    assert result["discount"] is None


def test_per_member_figures_are_given_for_every_price_that_exists():
    result = compare_prices(661_500, 900_000, 1_000_000, 950_000, LOADING, 100)
    assert result["per_member"]["issued_price"] == 9_500.0
    assert result["per_member"]["expected_claims"] == 6_615.0


def test_no_member_count_does_not_invent_a_per_member_figure():
    result = compare_prices(661_500, 900_000, 1_000_000, 950_000, LOADING, None)
    assert result["per_member"]["issued_price"] is None


# --- reading the issued premium off the document ------------------------

def test_the_issued_premium_is_the_sum_of_the_documents_categories():
    # A quote goes to the broker as one number however many category
    # tables sit behind it. Real figures off the Haworth quote.
    issued = issued_price_from_plans([
        {"gross_premium": 179_192.0, "member_count": 9, "category": "A"},
        {"gross_premium": 246_310.0, "member_count": 14, "category": "B"},
    ])
    assert issued["issued_price"] == 425_502.0
    assert issued["member_count"] == 23
    assert issued["categories_priced"] == 2


def test_a_category_the_parser_could_not_price_is_named_not_counted_as_free():
    issued = issued_price_from_plans([
        {"gross_premium": 179_192.0, "member_count": 9, "category": "A"},
        {"gross_premium": None, "member_count": 14, "category": "B"},
    ])
    assert issued["issued_price"] == 179_192.0
    assert issued["categories_without_a_premium"] == ["B"]


def test_no_quoted_document_at_all_gives_no_issued_price():
    issued = issued_price_from_plans([])
    assert issued["issued_price"] is None
    assert issued["member_count"] is None
