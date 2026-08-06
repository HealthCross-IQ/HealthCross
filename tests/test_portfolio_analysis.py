"""Tests for app/scoring/rules/portfolio_analysis.py - checks
HealthCross's own already-booked book against the New Business rate card.
"""
from datetime import date

from app.scoring.rules.portfolio_analysis import (
    age_bands_from_rate_cards,
    analyze_portfolio_member,
    earned_premium_fraction,
    group_claims_by_beneficiary,
    normalize_subgroup_key,
    resolve_group_product,
    resolve_master_client,
    summarize_burning_cost_by_age_gender,
    summarize_burning_cost_overall,
    summarize_population_mix,
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


def test_resolve_group_product_prefers_the_member_s_own_product_name_over_the_uploaded_mapping():
    # Starting Aug 2026 underwriting adds PRODUCTNAME directly into the
    # membership export itself instead of a separate Group->Product mapping
    # upload - when present on the member's own row, it wins outright.
    mapping = {"Acme Sub LLC": "Bronze"}
    member = _member(product_name="Platinum")
    assert resolve_group_product(member, mapping) == "Platinum"


def test_resolve_group_product_falls_back_to_the_uploaded_mapping_without_a_product_name():
    # An older-format export with no PRODUCTNAME column at all still works
    # exactly as before via the separate mapping upload.
    mapping = {"Acme Sub LLC": "Bronze"}
    member = _member(product_name=None)
    assert resolve_group_product(member, mapping) == "Bronze"


def test_analyze_portfolio_member_computes_standard_premium_via_the_rate_card():
    mapping = {"Acme Sub LLC": "Bronze"}
    result = analyze_portfolio_member(_member(), mapping, RATE_CARDS, [], {})
    assert result["in_scope"] is True
    assert result["product"] == "Bronze"
    assert result["network"] == "MSH Regular"
    assert result["standard_premium"] == 2000.0
    assert result["actual_premium"] == 2500.0
    assert result["warnings"] == []


def test_analyze_portfolio_member_carries_nationality_and_marital_status_through():
    # Both are read from the member for pricing/zone purposes already but
    # weren't being surfaced in the result dict - needed for a demographic
    # rollup (see demographic_summary) to reuse census_demographic_summary,
    # which reads these two fields directly.
    result = analyze_portfolio_member(
        _member(nationality="India", marital_status="married"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {}
    )
    assert result["nationality"] == "India"
    assert result["marital_status"] == "married"


def test_analyze_portfolio_member_flags_msh_intl_network_as_out_of_scope():
    result = analyze_portfolio_member(_member(network_type_raw="MSH INTL Network"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {})
    assert result["in_scope"] is False
    assert "reason" in result


def test_analyze_portfolio_member_still_carries_demographic_fields_when_out_of_scope():
    # Regression test: an out-of-scope member's demographic facts (age,
    # gender, relation, nationality, ...) were being dropped entirely by
    # the early-return, which silently mis-bucketed their real gender/
    # nationality as "Other"/unmapped in a book-wide demographic rollup
    # (see demographic_summary) even though the source data was right
    # there - no rate-card price applying to them doesn't make their
    # actual age or gender any less real.
    result = analyze_portfolio_member(
        _member(network_type_raw="MSH INTL Network", gender="M", age=45, nationality="UK", relation="employee"),
        {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {},
    )
    assert result["in_scope"] is False
    assert result["gender"] == "M"
    assert result["age"] == 45
    assert result["nationality"] == "UK"
    assert result["relation"] == "employee"
    assert result.get("product") is None
    assert result.get("network") is None


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
    claims_by_ben = {"ACM0001": [{"date_of_treatment": None, "final_amount": 1234.56}]}
    result = analyze_portfolio_member(_member(), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], claims_by_ben)
    assert result["actual_claims"] == 1234.56


def test_analyze_portfolio_member_defaults_actual_claims_to_zero_without_a_match():
    result = analyze_portfolio_member(_member(), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {})
    assert result["actual_claims"] == 0.0


def test_analyze_portfolio_member_segregates_paid_and_outstanding_claims():
    claims_by_ben = {
        "ACM0001": [
            {"date_of_treatment": None, "final_amount": 900.0, "claim_status": "Paid Claims"},
            {"date_of_treatment": None, "final_amount": 300.0, "claim_status": "Outstanding Claims"},
        ]
    }
    result = analyze_portfolio_member(_member(), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], claims_by_ben)
    assert result["actual_claims_paid"] == 900.0
    assert result["actual_claims_outstanding"] == 300.0
    # Total always reconciles back to paid + outstanding.
    assert result["actual_claims"] == 1200.0


def test_analyze_portfolio_member_treats_validated_claims_as_paid():
    # A newer HealthCross Claims export template uses "Validated Claims"
    # instead of "Paid Claims" for a settled claim - same meaning, just a
    # different word (the real bug this fixes: every claim in that export
    # was silently landing in "outstanding" since only the literal word
    # "paid" was recognized).
    claims_by_ben = {
        "ACM0001": [
            {"date_of_treatment": None, "final_amount": 900.0, "claim_status": "Validated Claims"},
            {"date_of_treatment": None, "final_amount": 300.0, "claim_status": "Outstanding Claims"},
        ]
    }
    result = analyze_portfolio_member(_member(), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], claims_by_ben)
    assert result["actual_claims_paid"] == 900.0
    assert result["actual_claims_outstanding"] == 300.0


def test_analyze_portfolio_member_treats_unrecognized_status_as_outstanding():
    claims_by_ben = {"ACM0001": [{"date_of_treatment": None, "final_amount": 500.0, "claim_status": "Pending Review"}]}
    result = analyze_portfolio_member(_member(), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], claims_by_ben)
    assert result["actual_claims_paid"] == 0.0
    assert result["actual_claims_outstanding"] == 500.0


def test_analyze_portfolio_member_only_counts_claims_within_its_own_policy_period():
    # A renewed member appears as TWO separate rows (one per policy year)
    # sharing the SAME beneficiary ID - claims dated in 2025 must only
    # count against the 2025 row, and 2026-dated claims only against the
    # 2026 row, not both (the real bug this fixes: claims were previously
    # summed by beneficiary ID alone, with no date check, so the same
    # claims got double counted into every policy year that ID appeared in).
    claims_by_ben = {
        "ACM0001": [
            {"date_of_treatment": date(2025, 6, 1), "final_amount": 900.0},
            {"date_of_treatment": date(2026, 6, 1), "final_amount": 4800.0},
        ]
    }
    row_2025 = analyze_portfolio_member(
        _member(policy_start_date=date(2025, 1, 1), policy_end_date=date(2026, 1, 1)),
        {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], claims_by_ben, as_of=date(2026, 6, 1),
    )
    row_2026 = analyze_portfolio_member(
        _member(policy_start_date=date(2026, 1, 1), policy_end_date=date(2027, 1, 1)),
        {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], claims_by_ben, as_of=date(2026, 6, 1),
    )
    assert row_2025["actual_claims"] == 900.0
    assert row_2026["actual_claims"] == 4800.0


def test_analyze_portfolio_member_uses_member_window_not_full_policy_term_for_a_mid_term_subgroup_transfer():
    # A real scenario found in the actual book: an employee transfers
    # between subgroups mid-term (e.g. DEVERE ACUMA -> ACUMA LLC) and
    # appears as TWO member rows that both carry the SAME overall
    # policy_start/policy_end (the one underlying MSH policy term) but
    # different, non-overlapping member_start/member_end windows (their
    # own actual enrollment sub-period under each subgroup). Matching
    # claims against the full policy term (instead of the member's own
    # window) double counted every claim in the year against BOTH rows -
    # this was a distinct bug from the renewal-year case above, since here
    # policy_start/policy_end are identical on both rows.
    claims_by_ben = {
        "M1": [
            {"date_of_treatment": date(2025, 6, 1), "final_amount": 1000.0, "claim_status": "Paid Claims"},
            {"date_of_treatment": date(2025, 10, 1), "final_amount": 2000.0, "claim_status": "Paid Claims"},
        ]
    }
    early_subgroup_row = analyze_portfolio_member(
        _member(
            beneficiary_id="M1",
            policy_start_date=date(2025, 5, 25), policy_end_date=date(2026, 5, 24),
            member_start_date=date(2025, 5, 25), member_end_date=date(2025, 8, 20),
        ),
        {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], claims_by_ben,
    )
    later_subgroup_row = analyze_portfolio_member(
        _member(
            beneficiary_id="M1",
            policy_start_date=date(2025, 5, 25), policy_end_date=date(2026, 5, 24),
            member_start_date=date(2025, 8, 20), member_end_date=date(2026, 5, 24),
        ),
        {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], claims_by_ben,
    )
    # The June 1 claim falls only within the early sub-period, the Oct 1
    # claim only within the later one - each counts exactly once.
    assert early_subgroup_row["actual_claims"] == 1000.0
    assert later_subgroup_row["actual_claims"] == 2000.0


def test_analyze_portfolio_member_falls_back_to_policy_dates_when_member_dates_missing():
    claims_by_ben = {"M1": [{"date_of_treatment": date(2025, 6, 1), "final_amount": 500.0}]}
    result = analyze_portfolio_member(
        _member(beneficiary_id="M1", policy_start_date=date(2025, 1, 1), policy_end_date=date(2026, 1, 1)),
        {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], claims_by_ben,
    )
    assert result["actual_claims"] == 500.0


def test_analyze_portfolio_member_counts_undated_claims_regardless_of_period():
    # A claim with no date_of_treatment can't be matched to a specific
    # period - it's still counted rather than silently dropped.
    claims_by_ben = {"ACM0001": [{"date_of_treatment": None, "final_amount": 500.0}]}
    result = analyze_portfolio_member(
        _member(policy_start_date=date(2025, 1, 1), policy_end_date=date(2026, 1, 1)),
        {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], claims_by_ben,
    )
    assert result["actual_claims"] == 500.0


def test_analyze_portfolio_member_counts_all_claims_when_member_has_no_policy_dates():
    # Without this member's own policy dates, there's no period to match
    # against - fall back to counting everything for that beneficiary.
    claims_by_ben = {
        "ACM0001": [
            {"date_of_treatment": date(2025, 6, 1), "final_amount": 900.0},
            {"date_of_treatment": date(2026, 6, 1), "final_amount": 4800.0},
        ]
    }
    result = analyze_portfolio_member(_member(), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], claims_by_ben)
    assert result["actual_claims"] == 5700.0


def test_group_claims_by_beneficiary_groups_multiple_claim_lines():
    claims = [
        {"patient_id": "ACM0001", "final_amount": 100.0},
        {"patient_id": "ACM0001", "final_amount": 50.0},
        {"patient_id": "ACM0002", "final_amount": 200.0},
        {"patient_id": None, "final_amount": 999.0},
    ]
    grouped = group_claims_by_beneficiary(claims)
    assert grouped == {
        "ACM0001": [
            {"patient_id": "ACM0001", "final_amount": 100.0},
            {"patient_id": "ACM0001", "final_amount": 50.0},
        ],
        "ACM0002": [{"patient_id": "ACM0002", "final_amount": 200.0}],
    }


def test_summarize_portfolio_rolls_up_by_product_and_computes_loss_ratios():
    results = [
        analyze_portfolio_member(_member(beneficiary_id="M1"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {"M1": [{"date_of_treatment": None, "final_amount": 1000.0}]}),
        analyze_portfolio_member(_member(beneficiary_id="M2", gender="F"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {"M2": [{"date_of_treatment": None, "final_amount": 3000.0}]}),
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
    assert bronze["earned_member_years"] == 2.0  # no policy dates set - each member defaults to fully earned
    assert bronze["burning_cost"] == round(4000.0 / 2.0, 2)


def test_summarize_portfolio_segregates_paid_and_outstanding_claims_and_reconciles():
    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [],
            {"M1": [
                {"date_of_treatment": None, "final_amount": 1000.0, "claim_status": "Paid Claims"},
                {"date_of_treatment": None, "final_amount": 400.0, "claim_status": "Outstanding Claims"},
            ]},
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M2", gender="F"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [],
            {"M2": [{"date_of_treatment": None, "final_amount": 3000.0, "claim_status": "Paid Claims"}]},
        ),
    ]
    rows = summarize_portfolio(results, "product")
    bronze = next(r for r in rows if r["product"] == "Bronze")
    assert bronze["actual_claims_paid"] == 4000.0
    assert bronze["actual_claims_outstanding"] == 400.0
    assert bronze["actual_claims"] == bronze["actual_claims_paid"] + bronze["actual_claims_outstanding"]


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
            "actual_claims_paid": 0.0,
            "actual_claims_outstanding": 0.0,
            "loss_ratio_vs_standard": None,
            "loss_ratio_vs_actual": 0.0,
            "actual_vs_standard_pct": None,
            "earned_member_years": 1.0,
            "burning_cost": 0.0,
            "policy_start_date": None,
            "network": "MSH Regular",
        }
    ]


def test_summarize_portfolio_by_client_groups_by_contract_falling_back_to_master():
    results = [
        analyze_portfolio_member(_member(beneficiary_id="M1"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {"M1": [{"date_of_treatment": None, "final_amount": 500.0}]}),
        analyze_portfolio_member(
            _member(beneficiary_id="M2", contract=None, master_contract="Other Holdings"),
            {}, RATE_CARDS, [], {"M2": [{"date_of_treatment": None, "final_amount": 1500.0}]},
        ),
    ]
    rows = summarize_portfolio(results, "client")
    by_client = {r["client"]: r for r in rows}
    assert by_client["Acme Sub LLC"]["member_count"] == 1
    assert by_client["Acme Sub LLC"]["actual_claims"] == 500.0
    assert by_client["Other Holdings"]["member_count"] == 1
    assert by_client["Other Holdings"]["actual_claims"] == 1500.0


def test_summarize_portfolio_by_gender_and_relation():
    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1", gender="M", relation="employee"),
            {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {"M1": [{"date_of_treatment": None, "final_amount": 1000.0}]},
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M2", gender="F", relation="spouse"),
            {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {"M2": [{"date_of_treatment": None, "final_amount": 4000.0}]},
        ),
    ]
    by_gender = {r["gender"]: r for r in summarize_portfolio(results, "gender")}
    assert by_gender["M"]["actual_claims"] == 1000.0
    assert by_gender["F"]["actual_claims"] == 4000.0

    by_relation = {r["relation"]: r for r in summarize_portfolio(results, "relation")}
    assert by_relation["employee"]["actual_claims"] == 1000.0
    assert by_relation["spouse"]["actual_claims"] == 4000.0
    # Spouse burning cost running well above employee's, as expected.
    assert by_relation["spouse"]["burning_cost"] > by_relation["employee"]["burning_cost"]


def test_summarize_portfolio_burning_cost_uses_earned_member_years_not_headcount():
    from datetime import date as _date

    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1", policy_start_date=_date(2026, 1, 1), policy_end_date=_date(2027, 1, 1)),
            {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {"M1": [{"date_of_treatment": None, "final_amount": 1000.0}]}, as_of=_date(2026, 7, 1),
        ),
    ]
    rows = summarize_portfolio(results, "product")
    bronze = next(r for r in rows if r["product"] == "Bronze")
    # ~6 months elapsed -> ~0.5 earned member-years, so burning cost is
    # roughly double the raw claims-per-head figure.
    assert 0.0 < bronze["earned_member_years"] < 1.0
    assert bronze["burning_cost"] == round(1000.0 / bronze["earned_member_years"], 2)


def test_summarize_portfolio_rejects_an_unknown_group_by_field():
    import pytest as _pytest

    with _pytest.raises(ValueError):
        summarize_portfolio([], "not_a_real_field")


def test_earned_premium_fraction_prorates_a_partially_elapsed_policy():
    # 3 months into a 12-month policy - a quarter of the annual premium has
    # actually been "earned" against the risk covered so far.
    fraction = earned_premium_fraction(date(2026, 1, 1), date(2027, 1, 1), date(2026, 4, 2))
    assert fraction == 91 / 365


def test_earned_premium_fraction_caps_at_one_once_policy_has_ended():
    fraction = earned_premium_fraction(date(2025, 1, 1), date(2026, 1, 1), date(2026, 6, 1))
    assert fraction == 1.0


def test_earned_premium_fraction_defaults_to_fully_earned_when_dates_missing():
    assert earned_premium_fraction(None, None, date(2026, 4, 2)) == 1.0
    assert earned_premium_fraction(date(2026, 1, 1), None, date(2026, 4, 2)) == 1.0


def test_earned_premium_fraction_floors_at_zero_before_policy_starts():
    fraction = earned_premium_fraction(date(2026, 6, 1), date(2027, 6, 1), date(2026, 1, 1))
    assert fraction == 0.0


def test_analyze_portfolio_member_prorates_premium_by_earned_fraction():
    member = _member(
        policy_start_date=date(2026, 1, 1),
        policy_end_date=date(2027, 1, 1),
        actual_gross_premium=12000.0,
    )
    result = analyze_portfolio_member(
        member, {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {}, as_of=date(2026, 4, 2),
    )
    raw_fraction = 91 / 365
    assert result["earned_premium_fraction"] == round(raw_fraction, 4)
    assert result["standard_premium"] == round(2000.0 * raw_fraction, 2)
    assert result["actual_premium"] == round(12000.0 * raw_fraction, 2)


def test_analyze_portfolio_member_handles_age_outside_every_rate_card_band():
    # Age 70 has no matching row in RATE_CARDS (only an 18-40 band exists) -
    # price_member returns net_total=None, which must not crash when
    # multiplied by the earned fraction.
    result = analyze_portfolio_member(
        _member(age=70), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {},
    )
    assert result["standard_premium"] is None
    assert any("No rate card entry" in w for w in result["warnings"])


def test_analyze_portfolio_member_treats_missing_policy_dates_as_fully_earned():
    result = analyze_portfolio_member(_member(), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {}, as_of=date(2026, 4, 2))
    assert result["earned_premium_fraction"] == 1.0
    assert result["standard_premium"] == 2000.0
    assert result["actual_premium"] == 2500.0


def test_analyze_portfolio_member_master_client_rolls_up_subgroups_under_one_master():
    result = analyze_portfolio_member(
        _member(contract="Acme Sub LLC", master_contract="Acme Holdings"),
        {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {},
    )
    assert result["client"] == "Acme Sub LLC"  # subgroup-level
    assert result["master_client"] == "Acme Holdings"  # master-level


def test_analyze_portfolio_member_master_client_falls_back_to_contract_when_no_master():
    result = analyze_portfolio_member(
        _member(contract="Acme Sub LLC", master_contract=None),
        {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {},
    )
    assert result["master_client"] == "Acme Sub LLC"


def test_resolve_master_client_prefers_the_uploaded_mapping_over_the_raw_field():
    # The real PortfolioMember.master_contract field is often blank/unreliable
    # on the system export - the uploaded Subgroup->Master mapping (from the
    # same file as Group->Product) is the authoritative source when present.
    mapping = {normalize_subgroup_key("Acme Sub LLC"): "Acme Holdings (correct)"}
    member = {"contract": "Acme Sub LLC", "master_contract": "Acme Sub LLC"}  # raw field wrongly duplicates the subgroup
    assert resolve_master_client(member, mapping) == "Acme Holdings (correct)"


def test_resolve_master_client_prefers_the_member_s_own_master_client_name_over_the_uploaded_mapping():
    # Starting Aug 2026 underwriting adds "Master Client Name" directly into
    # the membership export itself instead of a separate Subgroup->Master
    # mapping upload - when present on the member's own row, it wins outright,
    # even over an uploaded mapping that would otherwise say something else.
    mapping = {normalize_subgroup_key("Acme Sub LLC"): "Wrong Master From Upload"}
    member = {"contract": "Acme Sub LLC", "master_contract": None, "master_client_name": "Acme Holdings (correct)"}
    assert resolve_master_client(member, mapping) == "Acme Holdings (correct)"


def test_resolve_master_client_falls_back_to_the_uploaded_mapping_without_a_master_client_name():
    # An older-format export with no "Master Client Name" column at all still
    # works exactly as before via the separate mapping upload.
    mapping = {normalize_subgroup_key("Acme Sub LLC"): "Acme Holdings (correct)"}
    member = {"contract": "Acme Sub LLC", "master_contract": "Acme Sub LLC", "master_client_name": None}
    assert resolve_master_client(member, mapping) == "Acme Holdings (correct)"


def test_resolve_master_client_matches_despite_whitespace_and_case_differences():
    # Real spreadsheets prepared by hand aren't perfectly consistent about
    # this - a stray trailing space or different capitalization between the
    # membership export's CONTRACT column and the manually-typed mapping
    # sheet shouldn't silently break the roll-up.
    mapping = {normalize_subgroup_key("Acme  Sub LLC "): "Acme Holdings (correct)"}
    member = {"contract": "acme sub llc", "master_contract": "acme sub llc"}
    assert resolve_master_client(member, mapping) == "Acme Holdings (correct)"


def test_resolve_master_client_falls_back_to_raw_field_when_not_in_mapping():
    assert resolve_master_client({"contract": "Beta Sub LLC", "master_contract": "Beta Holdings"}, {}) == "Beta Holdings"
    assert resolve_master_client({"contract": "Beta Sub LLC", "master_contract": None}, {}) == "Beta Sub LLC"
    assert resolve_master_client({"contract": "Beta Sub LLC", "master_contract": "Beta Holdings"}, None) == "Beta Holdings"


def test_analyze_portfolio_member_master_client_uses_the_uploaded_mapping():
    result = analyze_portfolio_member(
        _member(contract="Acme Sub LLC", master_contract="Acme Sub LLC"),  # raw field wrongly duplicates subgroup
        {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {},
        subgroup_master_by_name={normalize_subgroup_key("Acme Sub LLC"): "Acme Holdings (correct)"},
    )
    assert result["master_client"] == "Acme Holdings (correct)"


def test_summarize_portfolio_by_master_client_combines_multiple_subgroups():
    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1", contract="Acme Sub A", master_contract="Acme Holdings"),
            {"Acme Sub A": "Bronze"}, RATE_CARDS, [], {"M1": [{"date_of_treatment": None, "final_amount": 1000.0}]},
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M2", contract="Acme Sub B", master_contract="Acme Holdings"),
            {"Acme Sub B": "Bronze"}, RATE_CARDS, [], {"M2": [{"date_of_treatment": None, "final_amount": 500.0}]},
        ),
    ]
    by_master = {r["master_client"]: r for r in summarize_portfolio(results, "master_client")}
    assert by_master["Acme Holdings"]["member_count"] == 2
    assert by_master["Acme Holdings"]["actual_claims"] == 1500.0

    # The two subgroups still show up separately under group_by=client.
    by_subgroup = {r["client"]: r for r in summarize_portfolio(results, "client")}
    assert set(by_subgroup) == {"Acme Sub A", "Acme Sub B"}


def test_analyze_portfolio_member_exposes_policy_year_from_policy_start_date():
    result = analyze_portfolio_member(
        _member(policy_start_date=date(2026, 5, 1), policy_end_date=date(2027, 4, 30)),
        {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {},
    )
    assert result["policy_year"] == "2026"


def test_analyze_portfolio_member_policy_year_is_none_without_policy_start_date():
    result = analyze_portfolio_member(_member(), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {})
    assert result["policy_year"] is None


def test_summarize_portfolio_by_policy_year_separates_renewal_cohorts():
    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1", policy_start_date=date(2025, 5, 1), policy_end_date=date(2026, 4, 30)),
            {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {"M1": [{"date_of_treatment": None, "final_amount": 1000.0}]}, as_of=date(2026, 4, 2),
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M2", policy_start_date=date(2026, 5, 1), policy_end_date=date(2027, 4, 30)),
            {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {"M2": [{"date_of_treatment": None, "final_amount": 4000.0}]}, as_of=date(2026, 6, 1),
        ),
    ]
    by_year = {r["policy_year"]: r for r in summarize_portfolio(results, "policy_year")}
    assert by_year["2025"]["member_count"] == 1
    assert by_year["2025"]["actual_claims"] == 1000.0
    assert by_year["2026"]["member_count"] == 1
    assert by_year["2026"]["actual_claims"] == 4000.0


def test_analyze_portfolio_member_exposes_category_and_policy_start_date():
    result = analyze_portfolio_member(
        _member(category="Category A", policy_start_date=date(2026, 5, 1), policy_end_date=date(2027, 4, 30)),
        {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {},
    )
    assert result["category"] == "Category A"
    assert result["policy_start_date"] == date(2026, 5, 1)


def test_summarize_portfolio_by_category_separates_benefit_tiers():
    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1", category="Category A"),
            {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {"M1": [{"date_of_treatment": None, "final_amount": 1000.0}]},
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M2", category="Category B"),
            {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {"M2": [{"date_of_treatment": None, "final_amount": 3000.0}]},
        ),
    ]
    by_category = {r["category"]: r for r in summarize_portfolio(results, "category")}
    assert by_category["Category A"]["actual_claims"] == 1000.0
    assert by_category["Category B"]["actual_claims"] == 3000.0


def test_summarize_portfolio_rows_carry_their_own_product_network_and_policy_start_date():
    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1", category="Category A", policy_start_date=date(2026, 1, 1), policy_end_date=date(2026, 12, 31)),
            {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {},
        ),
    ]
    by_category = {r["category"]: r for r in summarize_portfolio(results, "category")}
    row = by_category["Category A"]
    assert row["product"] == "Bronze"
    assert row["network"] == "MSH Regular"
    assert row["policy_start_date"] == "2026-01-01"


def test_summarize_portfolio_grouping_by_product_does_not_clobber_the_group_key_with_the_representative_field():
    # "product" is both the group-by key AND (for other dimensions) a
    # representative extra field - grouping BY product must still show the
    # real Bronze/Gold key, not have it silently overwritten.
    results = [analyze_portfolio_member(_member(), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {})]
    rows = summarize_portfolio(results, "product")
    assert rows[0]["product"] == "Bronze"
    assert "network" in rows[0]


def test_age_bands_from_rate_cards_returns_distinct_sorted_bands():
    bands = age_bands_from_rate_cards(RATE_CARDS)
    assert bands == [(18, 40)]


def test_summarize_burning_cost_by_age_gender_matches_rate_card_structure():
    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1", gender="M", age=25),
            {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {"M1": [{"date_of_treatment": None, "final_amount": 2000.0}]},
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M2", gender="F", age=35),
            {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {"M2": [{"date_of_treatment": None, "final_amount": 8000.0}]},
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M3", gender="M", age=70),
            {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {"M3": [{"date_of_treatment": None, "final_amount": 500.0}]},
        ),
    ]
    rows = summarize_burning_cost_by_age_gender(results, RATE_CARDS)
    by_key = {(r["age_band"], r["gender"]): r for r in rows}
    assert by_key[("18-40", "M")]["actual_claims"] == 2000.0
    assert by_key[("18-40", "M")]["burning_cost"] == 2000.0  # 1 fully-earned member-year
    assert by_key[("18-40", "F")]["actual_claims"] == 8000.0
    # Age 70 falls outside every rate-card band (18-40 only in this fixture).
    assert by_key[("Unmapped age", "M")]["actual_claims"] == 500.0


def test_summarize_burning_cost_by_product_network_groups_by_the_priced_pair():
    from app.scoring.rules.portfolio_analysis import summarize_burning_cost_by_product_network

    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [],
            {"M1": [{"date_of_treatment": None, "final_amount": 2000.0}]},
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M2"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [],
            {"M2": [{"date_of_treatment": None, "final_amount": 3000.0}]},
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M3"), {"Acme Sub LLC": "Gold"}, RATE_CARDS, [],
            {"M3": [{"date_of_treatment": None, "final_amount": 4000.0}]},
        ),
    ]
    rows = summarize_burning_cost_by_product_network(results)
    by_key = {(r["product"], r["network"]): r for r in rows}
    assert by_key[("Bronze", "MSH Regular")]["actual_claims"] == 5000.0
    assert by_key[("Bronze", "MSH Regular")]["member_count"] == 2
    assert by_key[("Gold", "MSH Regular")]["actual_claims"] == 4000.0


def test_summarize_burning_cost_by_product_network_skips_members_missing_either_field():
    from app.scoring.rules.portfolio_analysis import summarize_burning_cost_by_product_network

    # A member missing a Product mapping never gets priced, so has no
    # network resolved either (analyze_portfolio_member only prices when
    # BOTH are known) - shouldn't show up as a bogus (None, ...) bucket.
    results = [analyze_portfolio_member(_member(), {}, RATE_CARDS, [], {})]
    rows = summarize_burning_cost_by_product_network(results)
    assert rows == []


def test_summarize_burning_cost_by_product_network_age_gender_groups_by_all_four():
    from app.scoring.rules.portfolio_analysis import summarize_burning_cost_by_product_network_age_gender

    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1", gender="M", age=25), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [],
            {"M1": [{"date_of_treatment": None, "final_amount": 2000.0}]},
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M2", gender="F", age=35), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [],
            {"M2": [{"date_of_treatment": None, "final_amount": 8000.0}]},
        ),
        analyze_portfolio_member(
            # Same Product/Network as M1, but Gold's own age band/gender slice
            # must not blend into Bronze's - different product entirely.
            _member(beneficiary_id="M3", gender="M", age=25), {"Acme Sub LLC": "Gold"}, RATE_CARDS, [],
            {"M3": [{"date_of_treatment": None, "final_amount": 4000.0}]},
        ),
    ]
    rows = summarize_burning_cost_by_product_network_age_gender(results, RATE_CARDS)
    by_key = {(r["product"], r["network"], r["age_band"], r["gender"]): r for r in rows}
    assert by_key[("Bronze", "MSH Regular", "18-40", "M")]["actual_claims"] == 2000.0
    assert by_key[("Bronze", "MSH Regular", "18-40", "F")]["actual_claims"] == 8000.0
    assert by_key[("Gold", "MSH Regular", "18-40", "M")]["actual_claims"] == 4000.0
    assert len(rows) == 3


def test_price_case_against_burning_cost_applies_the_same_loading_as_the_rate_card_quote():
    from app.scoring.rules.new_business_rating import category_loading_pct, gross_up
    from app.scoring.rules.portfolio_analysis import price_case_against_burning_cost

    census = [
        {"category": "A", "age": 25, "gender": "M", "marital_status": "single", "relation": "employee", "emirates": "Dubai"},
        {"category": "A", "age": 35, "gender": "F", "marital_status": "married", "relation": "spouse", "emirates": "Dubai"},
    ]
    categories = [{"category": "A", "product": "Bronze", "network": "MSH Regular", "tpa": "MSH MENA", "variant_selections": {}}]
    burning_cost_rows = [
        {"product": "Bronze", "network": "MSH Regular", "age_band": "18-40", "gender": "M", "burning_cost": 2500.0},
        {"product": "Bronze", "network": "MSH Regular", "age_band": "18-40", "gender": "F", "burning_cost": 3500.0},
    ]
    result = price_case_against_burning_cost(census, categories, RATE_CARDS, burning_cost_rows)
    cat = result["categories"][0]
    assert cat["net_annual_premium"] == 6000.0  # 2500 + 3500, both members matched
    assert cat["priced_member_count"] == 2
    expected_loading = category_loading_pct("Bronze", None)
    assert cat["loading_pct"] == round(expected_loading, 4)
    assert cat["gross_annual_premium"] == round(gross_up(6000.0, expected_loading), 2)
    assert result["case_gross_annual_premium"] == cat["gross_annual_premium"]


def test_price_case_against_burning_cost_flags_members_with_no_matching_bucket():
    from app.scoring.rules.portfolio_analysis import price_case_against_burning_cost

    census = [{"category": "A", "age": 70, "gender": "M", "marital_status": "single", "relation": "employee", "emirates": "Dubai"}]
    categories = [{"category": "A", "product": "Bronze", "network": "MSH Regular", "tpa": "MSH MENA", "variant_selections": {}}]
    result = price_case_against_burning_cost(census, categories, RATE_CARDS, burning_cost_rows=[])
    cat = result["categories"][0]
    assert cat["priced_member_count"] == 0
    assert cat["member_count"] == 1
    assert cat["net_annual_premium"] == 0.0
    assert any("No booked-book burning cost" in w for w in cat["warnings"])


def test_burning_cost_lookup_network_maps_nas_networks_to_their_msh_equivalent():
    from app.scoring.rules.portfolio_analysis import _burning_cost_lookup_network

    assert _burning_cost_lookup_network("Comprehensive") == "MSH Platinum"
    assert _burning_cost_lookup_network("GN") == "MSH Comprehensive"
    assert _burning_cost_lookup_network("GN excluding Mediclinic and American") == "MSH Comprehensive"
    assert _burning_cost_lookup_network("GN Excluding American & Mediclinic Group") == "MSH Comprehensive"
    assert _burning_cost_lookup_network("Restricted +++") == "MSH Premium"
    assert _burning_cost_lookup_network("Restricted+++") == "MSH Premium"
    assert _burning_cost_lookup_network("Restricted") == "MSH Enhanced"
    assert _burning_cost_lookup_network("Super Restricted+ Zulaikha") == "MSH Regular"
    # Already-MSH networks pass through unchanged, not just recognized NAS ones.
    assert _burning_cost_lookup_network("MSH Platinum") == "MSH Platinum"
    assert _burning_cost_lookup_network(None) is None


def test_price_case_against_burning_cost_substitutes_the_msh_equivalent_for_a_nas_network():
    from app.scoring.rules.portfolio_analysis import price_case_against_burning_cost

    census = [{"category": "A", "age": 25, "gender": "M", "marital_status": "single", "relation": "employee", "emirates": "Dubai"}]
    # Category priced on NAS's own "Restricted" network - no NAS burning
    # cost bucket will ever exist (the book is entirely MSH), but its MSH
    # equivalent ("MSH Enhanced", per Enhanced=Restricted) does.
    categories = [{"category": "A", "product": "Bronze", "network": "Restricted", "tpa": "NAS", "variant_selections": {}}]
    burning_cost_rows = [
        {"product": "Bronze", "network": "MSH Enhanced", "age_band": "18-40", "gender": "M", "burning_cost": 3000.0},
    ]
    result = price_case_against_burning_cost(census, categories, RATE_CARDS, burning_cost_rows)
    cat = result["categories"][0]
    assert cat["priced_member_count"] == 1
    assert cat["net_annual_premium"] == 3000.0
    assert not cat["warnings"]


def test_demographic_summary_reuses_census_demographic_summary_fields():
    from app.scoring.rules.portfolio_analysis import demographic_summary

    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1", gender="M", age=25, nationality="India"),
            {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {},
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M2", gender="F", age=35, nationality="Philippines"),
            {"Acme Sub LLC": "Gold"}, RATE_CARDS, [], {},
        ),
    ]
    summary = demographic_summary(results)
    assert summary["total_members"] == 2
    assert summary["gender_counts"] == {"M": 1, "F": 1, "Other": 0}
    assert summary["nationality_zone_top5"]["zone_1_asia"] == [
        {"nationality": "India", "count": 1}, {"nationality": "Philippines", "count": 1},
    ]
    assert summary["product_counts"] == {"Bronze": 1, "Gold": 1}
    assert summary["network_counts"] == {"MSH Regular": 2}


def test_demographic_summary_counts_out_of_scope_members_toward_the_headline_total():
    # total_members must match the same headline count every other
    # Portfolio Analysis view reports (see /insights' total_members=
    # len(results)) - an out-of-scope member is still a real person in the
    # population even though no rate-card price applies to them, so they
    # aren't silently dropped from the total the way they are from
    # Product/Network counts (which they genuinely have none of).
    from app.scoring.rules.portfolio_analysis import demographic_summary

    results = [
        analyze_portfolio_member(_member(beneficiary_id="M1"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {}),
        analyze_portfolio_member(_member(beneficiary_id="M2", network_type_raw="MSH INTL Network"), {}, RATE_CARDS, [], {}),
    ]
    summary = demographic_summary(results)
    assert summary["total_members"] == 2
    assert summary["out_of_scope_member_count"] == 1
    assert summary["product_counts"] == {"Bronze": 1}
    # The out-of-scope member's real gender (M, same as _member()'s
    # default) must still be counted correctly, not lumped into "Other"
    # just because they fall outside the rate card's pricing scope.
    assert summary["gender_counts"] == {"M": 2, "F": 0, "Other": 0}


def test_summarize_burning_cost_overall_aggregates_the_whole_book():
    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [],
            {"M1": [{"date_of_treatment": None, "final_amount": 2000.0}]},
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M2"), {"Acme Sub LLC": "Gold"}, RATE_CARDS, [],
            {"M2": [{"date_of_treatment": None, "final_amount": 4000.0}]},
        ),
    ]
    overall = summarize_burning_cost_overall(results)
    assert overall["member_count"] == 2
    assert overall["actual_claims"] == 6000.0
    assert overall["burning_cost"] is not None


def test_summarize_burning_cost_overall_returns_none_for_no_earned_exposure():
    assert summarize_burning_cost_overall([]) is None


def test_summarize_population_mix_reports_zone_and_gender_percentages():
    results = [
        analyze_portfolio_member(_member(beneficiary_id="M1", nationality_zone="zone_1_asia", gender="M", age=30), {}, RATE_CARDS, [], {}),
        analyze_portfolio_member(_member(beneficiary_id="M2", nationality_zone="zone_1_asia", gender="F", age=40), {}, RATE_CARDS, [], {}),
        analyze_portfolio_member(_member(beneficiary_id="M3", nationality_zone="zone_2_middle_east", gender="M", age=50), {}, RATE_CARDS, [], {}),
    ]
    mix = summarize_population_mix(results)
    assert mix["member_count"] == 3
    assert mix["avg_age"] == 40.0
    assert mix["nationality_zone_mix"]["zone_1_asia"] == round(2 / 3, 4)
    assert mix["nationality_zone_mix"]["zone_2_middle_east"] == round(1 / 3, 4)
    assert mix["gender_mix"]["M"] == round(2 / 3, 4)
    assert mix["gender_mix"]["F"] == round(1 / 3, 4)


def test_summarize_population_mix_excludes_out_of_scope_members():
    results = [
        analyze_portfolio_member(_member(beneficiary_id="M1"), {}, RATE_CARDS, [], {}),
        analyze_portfolio_member(_member(beneficiary_id="M2", network_type_raw="MSH Intl Network"), {}, RATE_CARDS, [], {}),
    ]
    mix = summarize_population_mix(results)
    assert mix["member_count"] == 1


def test_summarize_population_mix_returns_none_for_an_empty_book():
    assert summarize_population_mix([]) is None
