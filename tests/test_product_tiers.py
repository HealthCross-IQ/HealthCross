from app.reference.product_tiers import NETWORK_RICHNESS_ORDER, PRODUCT_TIER_ORDER, tier_ladder


def test_tier_ladder_shows_one_tier_either_side():
    assert tier_ladder("Silver") == ["Gold", "Silver", "Bronze"]


def test_tier_ladder_bounded_at_the_top():
    assert tier_ladder("Platinum") == ["Platinum", "Gold"]


def test_tier_ladder_bounded_at_the_bottom():
    assert tier_ladder("Bronze") == ["Silver", "Bronze"]


def test_tier_ladder_unknown_product_returns_itself():
    assert tier_ladder("Not A Real Product") == ["Not A Real Product"]


def test_product_tier_order_and_network_richness_order_are_internally_consistent():
    assert len(PRODUCT_TIER_ORDER) == len(set(PRODUCT_TIER_ORDER)) == 4
    for tpa, order in NETWORK_RICHNESS_ORDER.items():
        assert len(order) == len(set(order)), f"{tpa} network order has duplicates"
