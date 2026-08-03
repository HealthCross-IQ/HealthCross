"""Tests for app/scoring/rules/portfolio_analysis.py - checks
HealthCross's own already-booked book against the New Business rate card.
"""
from app.scoring.rules.portfolio_analysis import (
    analyze_portfolio_member,
    claims_total_by_beneficiary,
    resolve_group_product,
    summarize_portfolio,
)

RATE_CARDS = [
    {"product": "Bronze", "region": "Dubai", "network": "MSH Regular", "tpa": "MSH MENA",
     "from_age": 18, "to_age": 40, "male_price": 2000.0, "female_price": 2200.0, "married_female_surcharge": 0.0},
    {"product": "Gold", "region": "Dubai", "network": "MSH Platinum", "tpa": "MSH MENA",
     "from_age": 18, "to_age": 40, "male_price": 4000.0, "female_price": 4400.0, "married_female_surcharge": 0.0},
]


def _member(**overrides):
    base = {
        "beneficiary_id": "ACM0001",
        "contract": "Acme Sub LLC",
        "master_contract": "Acme Holdings",
        "network_type_raw": "Regular",
        "age": 30,
        "gender": "M",
        "marital_status": "single",
        "relation": "employee",
        "nationality_zone": "zone_1_asia",
        "residence_emirate": "Dubai",
        "region": "Dubai",
        "actual_gross_premium": 2500.0,
    }
    base.update(overrides)
    return base


def test_resolve_group_product_prefers_subgroup_over_master():
    mapping = {"Acme Sub LLC": "Bronze", "Acme Holdings": "Gold"}
    assert resolve_group_product(_member(), mapping) == "Bronze"


def test_resolve_group_product_falls_back_to_master_contract():
    mapping = {"Acme Holdings": "Gold"}
    assert resolve_group_product(_member(), mapping) == "Gold"


def test_resolve_group_product_returns_none_when_unmapped():
    assert resolve_group_product(_member(), {}) is None


def test_analyze_portfolio_member_computes_standard_premium_via_the_rate_card():
    mapping = {"Acme Sub LLC": "Bronze"}
    result = analyze_portfolio_member(_member(), mapping, RATE_CARDS, [], {})
    assert result["in_scope"] is True
    assert result["product"] == "Bronze"
    assert result["network"] == "MSH Regular"
    assert result["standard_premium"] == 2000.0
    assert result["actual_premium"] == 2500.0
    assert result["warnings"] == []


def test_analyze_portfolio_member_flags_msh_intl_network_as_out_of_scope():
    result = analyze_portfolio_member(_member(network_type_raw="MSH INTL Network"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {})
    assert result["in_scope"] is False
    assert "reason" in result


def test_analyze_portfolio_member_warns_when_no_product_mapping_found():
    result = analyze_portfolio_member(_member(), {}, RATE_CARDS, [], {})
    assert result["in_scope"] is True
    assert result["product"] is None
    assert result["standard_premium"] is None
    assert any("No Product mapping" in w for w in result["warnings"])


def test_analyze_portfolio_member_warns_on_unrecognized_network_type():
    result = analyze_portfolio_member(_member(network_type_raw="Some New Tier"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {})
    assert result["in_scope"] is True
    assert result["network"] is None
    assert result["standard_premium"] is None
    assert any("Unrecognized network type" in w for w in result["warnings"])


def test_analyze_portfolio_member_sums_actual_claims_for_that_beneficiary():
    claims_by_ben = {"ACM0001": 1234.56}
    result = analyze_portfolio_member(_member(), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], claims_by_ben)
    assert result["actual_claims"] == 1234.56


def test_analyze_portfolio_member_defaults_actual_claims_to_zero_without_a_match():
    result = analyze_portfolio_member(_member(), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {})
    assert result["actual_claims"] == 0.0


def test_claims_total_by_beneficiary_sums_multiple_claim_lines():
    claims = [
        {"patient_id": "ACM0001", "final_amount": 100.0},
        {"patient_id": "ACM0001", "final_amount": 50.0},
        {"patient_id": "ACM0002", "final_amount": 200.0},
        {"patient_id": None, "final_amount": 999.0},
    ]
    totals = claims_total_by_beneficiary(claims)
    assert totals == {"ACM0001": 150.0, "ACM0002": 200.0}


def test_summarize_portfolio_rolls_up_by_product_and_computes_loss_ratios():
    results = [
        analyze_portfolio_member(_member(beneficiary_id="M1"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {"M1": 1000.0}),
        analyze_portfolio_member(_member(beneficiary_id="M2", gender="F"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {"M2": 3000.0}),
    ]
    rows = summarize_portfolio(results, "product")
    bronze = next(r for r in rows if r["product"] == "Bronze")
    assert bronze["member_count"] == 2
    assert bronze["priced_member_count"] == 2
    assert bronze["standard_premium"] == 2000.0 + 2200.0
    assert bronze["actual_premium"] == 2500.0 + 2500.0
    assert bronze["actual_claims"] == 1000.0 + 3000.0
    assert bronze["loss_ratio_vs_standard"] == round(4000.0 / 4200.0, 4)
    assert bronze["actual_vs_standard_pct"] == round((5000.0 - 4200.0) / 4200.0 * 100, 2)


def test_summarize_portfolio_excludes_out_of_scope_members():
    results = [
        analyze_portfolio_member(_member(beneficiary_id="M1"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {}),
        analyze_portfolio_member(_member(beneficiary_id="M2", network_type_raw="MSH Intl Network"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {}),
    ]
    rows = summarize_portfolio(results, "product")
    assert sum(r["member_count"] for r in rows) == 1


def test_summarize_portfolio_groups_unmapped_members_together():
    results = [analyze_portfolio_member(_member(), {}, RATE_CARDS, [], {})]
    rows = summarize_portfolio(results, "product")
    assert rows == [
        {
            "product": "Unmapped",
            "member_count": 1,
            "priced_member_count": 0,
            "standard_premium": 0.0,
            "actual_premium": 2500.0,
            "actual_claims": 0.0,
            "loss_ratio_vs_standard": None,
            "loss_ratio_vs_actual": 0.0,
            "actual_vs_standard_pct": None,
        }
    ]


def test_summarize_portfolio_rejects_an_unknown_group_by_field():
    import pytest as _pytest

    with _pytest.raises(ValueError):
        summarize_portfolio([], "not_a_real_field")
