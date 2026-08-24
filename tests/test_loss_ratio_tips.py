"""Data-driven loss ratio tips - app/scoring/rules/loss_ratio_tips.py."""
import pytest

from app.scoring.rules.loss_ratio_tips import MIN_MATERIAL_SHARE, loss_ratio_tips


def _claim(amount, patient="P1", category="Consultation", provider="Clinic A",
           dx=None, ip_op="OP", status="Paid Claims"):
    return {
        "patient_id": patient, "final_amount": amount, "medical_category": category,
        "provider_name": provider, "diagnosis_code": dx, "ip_op_maternity": ip_op,
        "claim_status": status,
    }


def _ids(result):
    return [t["id"] for t in result["tips"]]


def test_no_claims_produces_no_tips_rather_than_an_error():
    result = loss_ratio_tips([])
    assert result["tips"] == []
    assert result["total_claims"] == 0.0


def test_concentration_fires_when_a_few_members_carry_the_book():
    # 99 members at 100 each, one at 100,000 - textbook concentration.
    claims = [_claim(100, patient=f"P{i}") for i in range(99)] + [_claim(100_000, patient="WHALE")]
    result = loss_ratio_tips(claims)
    tip = next(t for t in result["tips"] if t["id"] == "claims_concentration")
    assert "WHALE" not in tip["title"]  # it names the size of the cohort, not the member
    assert tip["opportunity_aed"] > 0
    assert "assumption" in tip["basis"].lower()


def test_an_evenly_spread_book_gets_no_concentration_tip():
    claims = [_claim(1000, patient=f"P{i}") for i in range(200)]
    assert "claims_concentration" not in _ids(loss_ratio_tips(claims))


def test_pharmacy_tip_fires_only_when_pharmacy_is_material():
    heavy = [_claim(900, category="Pharmacy"), _claim(100, category="Consultation")]
    assert "pharmacy_management" in _ids(loss_ratio_tips(heavy))

    light = [_claim(5, category="Pharmacy")] + [_claim(1000, category="Consultation", patient=f"P{i}") for i in range(10)]
    assert "pharmacy_management" not in _ids(loss_ratio_tips(light))


def test_chronic_tip_reads_real_icd_codes():
    # E11 diabetes and I10 hypertension are both chronic chapters.
    claims = [_claim(5000, dx="E119"), _claim(5000, dx="I10"), _claim(100, dx="S060")]
    result = loss_ratio_tips(claims)
    tip = next(t for t in result["tips"] if t["id"] == "chronic_disease")
    assert tip["opportunity_aed"] > 0
    chapters = {e["chapter"] for e in tip["evidence"]}
    assert any("endocrine" in c for c in chapters)


def _providers(spec, category="Diagnostic", n=40):
    """spec: {provider: cost-per-claim} - n claims each."""
    out = []
    for provider, cost in spec.items():
        out += [_claim(cost, provider=provider, category=category) for _ in range(n)]
    return out


def test_provider_tip_needs_enough_claims_per_provider_to_be_a_signal():
    # 5x apart, but only 3 claims each - a sample, not a signal.
    thin = ([_claim(100, provider="Cheap", category="Diagnostic") for _ in range(3)]
            + [_claim(500, provider="Dear", category="Diagnostic") for _ in range(3)])
    assert "provider_steering" not in _ids(loss_ratio_tips(thin))


def test_provider_tip_needs_enough_providers_to_take_quartiles_from():
    # Three providers cannot support a quartile comparison - with so few,
    # the "spread" is just the two extremes wearing a different name.
    three = _providers({"A": 100, "B": 300, "C": 500})
    assert "provider_steering" not in _ids(loss_ratio_tips(three))


def test_provider_spread_is_measured_between_quartiles_not_extremes():
    # One wildly expensive outlier must not define the spread. Quartile
    # providers here are 200 and 400, so 2.0x - not the 20x the extremes
    # would give.
    claims = _providers({"A": 100, "B": 200, "C": 300, "D": 400, "E": 2000})
    tip = next(t for t in loss_ratio_tips(claims)["tips"] if t["id"] == "provider_steering")
    assert tip["evidence"][0]["spread"] == pytest.approx(2.0)


def test_a_provider_with_a_heavier_case_mix_cannot_manufacture_a_spread():
    # Same typical claim at both, but one also does a handful of very
    # expensive procedures. On means that reads as a large spread; on
    # medians - what a typical claim costs - it correctly reads as none.
    claims = _providers({"A": 100, "B": 100, "C": 100, "D": 100})
    claims += [_claim(50_000, provider="D", category="Diagnostic") for _ in range(5)]
    assert "provider_steering" not in _ids(loss_ratio_tips(claims))


def test_a_normal_amount_of_provider_variation_is_not_a_finding():
    claims = _providers({"A": 100, "B": 105, "C": 110, "D": 115})
    assert "provider_steering" not in _ids(loss_ratio_tips(claims))


def test_missing_provider_names_are_reported_as_a_data_gap_not_a_saving():
    claims = [_claim(1000, provider=""), _claim(1000, provider="Clinic A")]
    tip = next(t for t in loss_ratio_tips(claims)["tips"] if t["id"] == "provider_data_gap")
    assert tip["opportunity_aed"] is None
    assert "no saving is claimed" in tip["basis"].lower()


def test_maternity_is_framed_as_a_pricing_issue_with_no_saving_claimed():
    claims = [_claim(50_000, ip_op="MATERNITY"), _claim(50_000, ip_op="OP")]
    tip = next(t for t in loss_ratio_tips(claims)["tips"] if t["id"] == "maternity_pricing")
    assert tip["opportunity_aed"] is None
    assert tip["category"] == "Pricing"


def test_large_claim_tip_uses_the_threshold_it_is_given():
    claims = [_claim(150_000, patient="BIG")] + [_claim(1000, patient=f"P{i}") for i in range(100)]
    assert "large_claims" in _ids(loss_ratio_tips(claims, large_claim_threshold=100_000))
    assert "large_claims" not in _ids(loss_ratio_tips(claims, large_claim_threshold=500_000))


def test_repricing_tip_measures_the_real_gap_rather_than_assuming_one():
    rows = [
        {"master_client": "UNDER", "net_premium": 100_000, "incurred_claims": 160_000},
        {"master_client": "FINE", "net_premium": 100_000, "incurred_claims": 50_000},
    ]
    tip = next(t for t in loss_ratio_tips([_claim(1000)], account_rows=rows)["tips"]
               if t["id"] == "underpriced_accounts")
    assert tip["opportunity_aed"] == 60_000
    assert [e["master_client"] for e in tip["evidence"]] == ["UNDER"]
    assert "not an assumption" in tip["basis"].lower()


def test_no_repricing_tip_when_every_account_pays_its_way():
    rows = [{"master_client": "FINE", "net_premium": 100_000, "incurred_claims": 50_000}]
    assert "underpriced_accounts" not in _ids(loss_ratio_tips([_claim(1000)], account_rows=rows))


def test_assumptions_are_overridable_and_move_the_numbers():
    claims = [_claim(900, category="Pharmacy"), _claim(100, category="Consultation")]
    low = loss_ratio_tips(claims, assumptions={"pharmacy_generic_reduction": 0.05})
    high = loss_ratio_tips(claims, assumptions={"pharmacy_generic_reduction": 0.30})
    low_tip = next(t for t in low["tips"] if t["id"] == "pharmacy_management")
    high_tip = next(t for t in high["tips"] if t["id"] == "pharmacy_management")
    assert high_tip["opportunity_aed"] > low_tip["opportunity_aed"]
    assert high["assumptions"]["pharmacy_generic_reduction"] == 0.30


def test_quantified_tips_rank_above_unquantified_ones():
    claims = ([_claim(100, patient=f"P{i}") for i in range(99)]
              + [_claim(100_000, patient="WHALE", ip_op="MATERNITY")])
    tips = loss_ratio_tips(claims)["tips"]
    quantified = [t["opportunity_aed"] is not None for t in tips]
    # All the Nones sit at the end, and the quantified ones descend.
    assert quantified == sorted(quantified, reverse=True)
    amounts = [t["opportunity_aed"] for t in tips if t["opportunity_aed"] is not None]
    assert amounts == sorted(amounts, reverse=True)


def test_every_tip_carries_its_own_basis_so_no_number_is_unexplained():
    claims = ([_claim(100, patient=f"P{i}", dx="E119") for i in range(99)]
              + [_claim(100_000, patient="WHALE", category="Pharmacy")])
    for tip in loss_ratio_tips(claims)["tips"]:
        assert tip["basis"], tip["id"]
        assert tip["action"], tip["id"]
        assert tip["finding"], tip["id"]


def test_the_materiality_floor_is_what_keeps_trivial_findings_out():
    assert MIN_MATERIAL_SHARE > 0
    # Pharmacy at well under the floor stays silent.
    claims = [_claim(1, category="Pharmacy")] + [_claim(1000, category="Consultation", patient=f"P{i}") for i in range(50)]
    assert "pharmacy_management" not in _ids(loss_ratio_tips(claims))


def test_concentration_is_measured_as_a_multiple_not_just_a_share():
    # The bug this guards: on a perfectly even book the top 5% carry
    # exactly 5% of claims, which clears any materiality floor while
    # being the precise opposite of a concentration finding.
    from app.scoring.rules.loss_ratio_tips import MIN_CONCENTRATION_MULTIPLE
    assert MIN_CONCENTRATION_MULTIPLE > 1.0

    even = [_claim(1000, patient=f"P{i}") for i in range(200)]
    assert "claims_concentration" not in _ids(loss_ratio_tips(even))

    # Same book, but the top 5% carry ~8x their own weight.
    skewed = ([_claim(1000, patient=f"P{i}") for i in range(190)]
              + [_claim(15_000, patient=f"BIG{i}") for i in range(10)])
    assert "claims_concentration" in _ids(loss_ratio_tips(skewed))
