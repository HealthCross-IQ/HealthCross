import pytest

from app.finance.fee_engine import FeeRate, compute_hc_fee

RATES = [
    FeeRate("broker", "bronze_silver", 0.065),
    FeeRate("broker", "gold_platinum", 0.05),
    FeeRate("direct", "bronze_silver", 0.115),
    FeeRate("direct", "gold_platinum", 0.10),
]


def test_direct_silver_matches_rate_card():
    result = compute_hc_fee("direct", "Silver", 235851.25, RATES)
    assert result["hc_fee_pct"] == 0.115
    assert result["hc_fees"] == pytest.approx(27122.89, abs=0.01)
    assert result["vat_amount"] == pytest.approx(1356.14, abs=0.01)
    assert result["total_value"] == pytest.approx(28479.03, abs=0.01)
    assert result["is_manual_fee"] is False


def test_broker_gold_platinum_uses_lower_rate():
    result = compute_hc_fee("broker", "Platinum", 100000, RATES)
    assert result["hc_fee_pct"] == 0.05
    assert result["hc_fees"] == 5000.0


def test_mixed_tier_product_requires_manual_rate():
    with pytest.raises(ValueError):
        compute_hc_fee("broker", "Gold/Bronze", 113975.63, RATES)

    result = compute_hc_fee("broker", "Gold/Bronze", 113975.63, RATES, manual_fee_pct=0.065)
    assert result["is_manual_fee"] is True
    assert result["hc_fee_pct"] == 0.065
    assert result["details"]["rate_source"] == "manual"


def test_group_channel_always_requires_manual_rate():
    with pytest.raises(ValueError):
        compute_hc_fee("group", "Gold", 50000, RATES)

    result = compute_hc_fee("group", "Gold", 50000, RATES, manual_fee_pct=0.08)
    assert result["is_manual_fee"] is True
    assert result["hc_fees"] == 4000.0


def test_missing_rate_card_row_raises():
    with pytest.raises(ValueError):
        compute_hc_fee("direct", "Silver", 1000, rate_cards=[])
