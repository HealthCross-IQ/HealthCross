"""Tests for app/scoring/rules/portfolio_analysis.py - checks
HealthCross's own already-booked book against the New Business rate card.
"""
from datetime import date, timedelta

import pytest

from app.scoring.rules.portfolio_analysis import (
    DEFAULT_EXPENSE_RATIO_PCT,
    _claim_matches_period,
    actual_claims_for_member,
    account_calendar_loss_ratio_rows,
    account_loss_ratio_rows,
    account_loss_ratio_totals,
    age_bands_from_rate_cards,
    analyze_portfolio_member,
    claims_above_thresholds,
    earned_premium_fraction,
    executive_portfolio_summary,
    group_claims_by_beneficiary,
    ibnr_for_member,
    normalize_subgroup_key,
    recurring_high_cost_members,
    renewal_due_accounts,
    resolve_client_opex_pct,
    resolve_group_product,
    resolve_master_client,
    summarize_burning_cost_by_age_gender,
    summarize_burning_cost_overall,
    summarize_by_group_size_band,
    summarize_new_vs_renewal,
    summarize_population_mix,
    summarize_portfolio,
    top_claims_by_value,
    top_members_by_total_claims,
    nationality_risk_table,
    period_overlap_days,
    utilization_by_benefit_category,
    utilization_by_encounter_type,
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
    # Product and network are just as real as the demographics: these
    # members ARE sold a product, and their network is a genuine one that
    # simply is not a UAE rate-card network. Carrying both lets them roll
    # up under the product they actually hold instead of "Unmapped".
    assert result["product"] == "Bronze"
    assert result["network"] == "MSH INTL Network"
    assert result.get("standard_premium") is None  # the one figure that does need a rate card


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


def test_ibnr_for_member_is_the_paid_claims_run_rate_projected_over_30_days():
    # policy_start 2026-01-01, as_of 2026-03-02 -> 60 days elapsed.
    as_of = date(2026, 3, 2)
    member = _member(policy_start_date=date(2026, 1, 1))
    claims = [
        {"date_of_treatment": date(2026, 1, 15), "final_amount": 500.0, "claim_status": "Paid Claims"},
        {"date_of_treatment": date(2026, 2, 20), "final_amount": 100.0, "claim_status": "Paid Claims"},
        {"date_of_treatment": date(2026, 2, 25), "final_amount": 300.0, "claim_status": "Outstanding Claims"},  # not Paid - excluded
    ]
    claims_by_ben = {member["beneficiary_id"]: claims}
    # (500 + 100) paid / 60 elapsed days * 30 = 300.0
    assert ibnr_for_member(member, claims_by_ben, as_of) == 300.0


def test_ibnr_for_member_excludes_paid_claims_dated_after_as_of():
    as_of = date(2026, 3, 2)
    member = _member(policy_start_date=date(2026, 1, 1))
    claims = [
        {"date_of_treatment": date(2026, 1, 15), "final_amount": 600.0, "claim_status": "Paid Claims"},
        {"date_of_treatment": date(2026, 3, 15), "final_amount": 9000.0, "claim_status": "Paid Claims"},  # after as_of - excluded
    ]
    claims_by_ben = {member["beneficiary_id"]: claims}
    assert ibnr_for_member(member, claims_by_ben, as_of) == 300.0


def test_ibnr_for_member_is_zero_when_no_time_has_elapsed_yet():
    as_of = date(2026, 1, 1)
    member = _member(policy_start_date=date(2026, 1, 1))
    claims_by_ben = {member["beneficiary_id"]: [
        {"date_of_treatment": date(2026, 1, 1), "final_amount": 500.0, "claim_status": "Paid Claims"},
    ]}
    assert ibnr_for_member(member, claims_by_ben, as_of) == 0.0


def test_ibnr_for_member_is_zero_once_the_policy_has_run_past_a_full_year():
    # report date more than 365 days after policy_start_date - the account
    # is already closed out and has had a full year for claims to filter
    # through, so no IBNR regardless of recent paid activity.
    as_of = date(2026, 8, 15)
    member = _member(policy_start_date=date(2025, 1, 1))
    claims_by_ben = {member["beneficiary_id"]: [
        {"date_of_treatment": date(2026, 8, 1), "final_amount": 500.0, "claim_status": "Paid Claims"},
    ]}
    assert ibnr_for_member(member, claims_by_ben, as_of) == 0.0


def test_ibnr_for_member_is_zero_without_a_policy_start_date():
    as_of = date(2026, 8, 15)
    member = _member(policy_start_date=None)
    claims_by_ben = {member["beneficiary_id"]: [
        {"date_of_treatment": date(2026, 8, 1), "final_amount": 500.0, "claim_status": "Paid Claims"},
    ]}
    assert ibnr_for_member(member, claims_by_ben, as_of) == 0.0


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


def test_executive_portfolio_summary_computes_level_1_kpis():
    # as_of is exactly 30 days after policy_start_date, so the IBNR run
    # rate (total paid so far / elapsed days * 30) reduces to exactly the
    # total paid so far - a convenient identity for a clean assertion.
    as_of = date(2026, 1, 31)
    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1", contract="Acme Sub LLC", master_contract="Acme Holdings", policy_start_date=date(2026, 1, 1)),
            {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [],
            {"M1": [{"date_of_treatment": date(2026, 1, 15), "final_amount": 1000.0, "claim_status": "Paid Claims"}]},
            as_of=as_of,
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M2", gender="F", contract="Other Sub LLC", master_contract="Other Holdings", policy_start_date=date(2026, 1, 1)),
            {"Other Sub LLC": "Gold"}, RATE_CARDS, [],
            {"M2": [{"date_of_treatment": date(2026, 1, 20), "final_amount": 500.0, "claim_status": "Paid Claims"}]},
            as_of=as_of,
        ),
    ]
    summary = executive_portfolio_summary(results)
    assert summary["total_groups"] == 2  # two distinct master clients
    assert summary["total_members"] == 2
    assert summary["written_premium"] == 5000.0  # 2 x 2500 actual_gross_premium, unprorated
    assert summary["earned_premium"] == 5000.0  # fully earned - both start exactly at as_of's year
    # actual_claims (1000+500) + IBNR (each member's 30-day-elapsed run
    # rate equals its own paid total exactly, since neither policy expired)
    assert summary["incurred_claims"] == 3000.0
    assert summary["loss_ratio"] == round(3000.0 / 5000.0, 4)
    assert summary["expense_ratio_pct"] == 0.33
    assert summary["combined_ratio"] == round(summary["loss_ratio"] + 0.33, 4)
    assert summary["average_premium_per_member"] == 2500.0


def test_executive_portfolio_summary_counts_groups_by_master_client_not_subgroup():
    results = [
        analyze_portfolio_member(_member(beneficiary_id="M1", contract="Sub A", master_contract="Big Corp"), {}, RATE_CARDS, [], {}),
        analyze_portfolio_member(_member(beneficiary_id="M2", contract="Sub B", master_contract="Big Corp"), {}, RATE_CARDS, [], {}),
    ]
    summary = executive_portfolio_summary(results)
    assert summary["total_groups"] == 1  # same master, 2 subgroups
    assert summary["total_members"] == 2


def test_executive_portfolio_summary_includes_out_of_scope_members_in_headcount():
    results = [
        analyze_portfolio_member(_member(beneficiary_id="M1"), {}, RATE_CARDS, [], {}),
        analyze_portfolio_member(_member(beneficiary_id="M2", network_type_raw="MSH Intl Network"), {}, RATE_CARDS, [], {}),
    ]
    summary = executive_portfolio_summary(results)
    assert summary["total_members"] == 2  # group/member counts aren't a pricing question


def test_executive_portfolio_summary_includes_out_of_scope_members_own_premium_and_claims():
    # Regression test: an out-of-scope member's written_premium/actual_claims/
    # ibnr were silently missing from analyze_portfolio_member's early-return
    # dict, so Written Premium/Incurred Claims quietly undercounted a real
    # book by however much its out-of-scope members' own premium/claims came
    # to - Total Groups/Members already counted them (member/group counts
    # aren't a pricing question), Written Premium/Incurred Claims should too.
    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1", network_type_raw="MSH Intl Network", policy_start_date=date(2026, 1, 1)),
            {}, RATE_CARDS, [],
            {"M1": [{"date_of_treatment": date(2026, 1, 15), "final_amount": 100.0, "claim_status": "Paid Claims"}]},
            as_of=date(2026, 1, 31),
        ),
    ]
    summary = executive_portfolio_summary(results)
    assert summary["written_premium"] == 2500.0  # _member()'s own actual_gross_premium, unprorated
    assert summary["incurred_claims"] == 200.0  # 100 actual_claims + 100 ibnr (30-day-elapsed run rate == its own paid total)


def test_executive_portfolio_summary_expense_ratio_is_overridable():
    results = [analyze_portfolio_member(_member(beneficiary_id="M1"), {}, RATE_CARDS, [], {"M1": [{"date_of_treatment": None, "final_amount": 1000.0}]})]
    summary = executive_portfolio_summary(results, expense_ratio_pct=0.25)
    assert summary["expense_ratio_pct"] == 0.25
    assert summary["combined_ratio"] == round(summary["loss_ratio"] + 0.25, 4)


def test_executive_portfolio_summary_uses_real_client_opex_where_uploaded():
    # M1's client has a real 20% OPEX on file - M2's client doesn't, so it
    # falls back to the flat 33% default. Both premiums are equal (2500
    # each), so the blended expense ratio should land exactly halfway
    # between 20% and 33%.
    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1", contract="Has Opex Co", master_contract="Has Opex Co"), {}, RATE_CARDS, [], {},
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M2", contract="No Opex Co", master_contract="No Opex Co"), {}, RATE_CARDS, [], {},
        ),
    ]
    summary = executive_portfolio_summary(
        results, opex_records_by_client={"Has Opex Co": [{"start_date": None, "end_date": None, "opex_pct": 0.20}]}
    )
    assert summary["expense_ratio_pct"] == round((0.20 + 0.33) / 2, 4)


def test_executive_portfolio_summary_blends_opex_when_no_client_has_a_real_figure():
    results = [analyze_portfolio_member(_member(beneficiary_id="M1"), {}, RATE_CARDS, [], {})]
    summary = executive_portfolio_summary(results, opex_records_by_client={})
    assert summary["expense_ratio_pct"] == DEFAULT_EXPENSE_RATIO_PCT


def test_resolve_client_opex_pct_picks_the_record_covering_the_members_policy_period():
    # Same client renewed with a DIFFERENT loading each year - each
    # member's own policy_start_date should pick the record that
    # actually covers their own policy period, not just the first/last one.
    records = {
        "Acme Holdings": [
            {"start_date": date(2024, 1, 1), "end_date": date(2024, 12, 31), "opex_pct": 0.30},
            {"start_date": date(2025, 1, 1), "end_date": date(2025, 12, 31), "opex_pct": 0.22},
        ]
    }
    assert resolve_client_opex_pct("Acme Holdings", date(2024, 6, 1), records, DEFAULT_EXPENSE_RATIO_PCT) == 0.30
    assert resolve_client_opex_pct("Acme Holdings", date(2025, 6, 1), records, DEFAULT_EXPENSE_RATIO_PCT) == 0.22


def test_resolve_client_opex_pct_falls_back_to_default_outside_any_covered_window():
    records = {"Acme Holdings": [{"start_date": date(2025, 1, 1), "end_date": date(2025, 12, 31), "opex_pct": 0.22}]}
    assert resolve_client_opex_pct("Acme Holdings", date(2026, 6, 1), records, DEFAULT_EXPENSE_RATIO_PCT) == DEFAULT_EXPENSE_RATIO_PCT


def test_resolve_client_opex_pct_uses_the_single_undated_record_regardless_of_policy_start():
    # The common case - a client with just one flat OPEX figure, no dates
    # at all, should apply regardless of the member's own policy period.
    records = {"Acme Holdings": [{"start_date": None, "end_date": None, "opex_pct": 0.25}]}
    assert resolve_client_opex_pct("Acme Holdings", date(2026, 6, 1), records, DEFAULT_EXPENSE_RATIO_PCT) == 0.25
    assert resolve_client_opex_pct("Acme Holdings", None, records, DEFAULT_EXPENSE_RATIO_PCT) == 0.25


def test_client_opex_pct_on_file_tells_a_real_loading_from_the_house_default():
    # 33% shown flat reads as this account's own loading, and nothing on
    # the screen lets a reader see that nobody supplied it. Every figure
    # resting on it is then partly assumed. The two answers come from one
    # walk of the records, so they cannot drift on which record wins.
    from app.scoring.rules.portfolio_analysis import client_opex_pct_on_file

    records = {"Acme Holdings": [{"start_date": None, "end_date": None, "opex_pct": 0.25}]}
    assert client_opex_pct_on_file("Acme Holdings", date(2026, 6, 1), records) == 0.25
    assert client_opex_pct_on_file("Nobody Ltd", date(2026, 6, 1), records) is None
    assert client_opex_pct_on_file("Acme Holdings", date(2026, 6, 1), None) is None
    assert client_opex_pct_on_file(None, date(2026, 6, 1), records) is None

    dated = {"Acme Holdings": [
        {"start_date": date(2025, 1, 1), "end_date": date(2025, 12, 31), "opex_pct": 0.22},
    ]}
    # Outside every window is not on file, however many records exist.
    assert client_opex_pct_on_file("Acme Holdings", date(2026, 6, 1), dated) is None
    assert client_opex_pct_on_file("Acme Holdings", date(2025, 6, 1), dated) == 0.22


def test_account_loss_ratio_rows_flag_a_loading_nobody_supplied():
    members = [_lr_member("Acme Holdings", date(2025, 1, 1),
                          paid=50_000.0, outstanding=10_000.0, gross=200_000.0)]

    rows = account_loss_ratio_rows(members, as_of=date(2025, 6, 30))
    assert rows[0]["loading_pct"] == DEFAULT_EXPENSE_RATIO_PCT
    assert rows[0]["loading_is_default"] is True

    on_file = {"Acme Holdings": [{"start_date": None, "end_date": None, "opex_pct": 0.21}]}
    rows = account_loss_ratio_rows(members, as_of=date(2025, 6, 30),
                                   opex_records_by_client=on_file)
    assert rows[0]["loading_pct"] == 0.21
    assert rows[0]["loading_is_default"] is False


def test_summarize_by_group_size_band_pools_groups_by_headcount():
    # "Small Co" has 2 members (band 1-10), "Big Co" has 3 members split
    # across two subgroups sharing the same master_contract (band 1-10
    # too, since 3 members still falls in that band) - own claims are
    # pooled per band, not per group.
    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="S1", contract="Small Co", master_contract="Small Co"),
            {}, RATE_CARDS, [], {"S1": [{"date_of_treatment": None, "final_amount": 1000.0, "claim_status": "Paid Claims"}]},
        ),
        analyze_portfolio_member(_member(beneficiary_id="S2", contract="Small Co", master_contract="Small Co"), {}, RATE_CARDS, [], {}),
        analyze_portfolio_member(_member(beneficiary_id="B1", contract="Big Co", master_contract="Big Co"), {}, RATE_CARDS, [], {}),
        analyze_portfolio_member(_member(beneficiary_id="B2", contract="Big Co", master_contract="Big Co"), {}, RATE_CARDS, [], {}),
        analyze_portfolio_member(_member(beneficiary_id="B3", contract="Big Co", master_contract="Big Co"), {}, RATE_CARDS, [], {}),
    ]
    rows = summarize_by_group_size_band(results)
    band = next(r for r in rows if r["band"] == "1-10")
    assert band["group_count"] == 2  # Small Co + Big Co
    assert band["member_count"] == 5
    assert band["average_group_size"] == 2.5
    assert band["actual_claims"] == 1000.0
    assert band["actual_premium"] == 5 * 2500.0  # 5 members x _member()'s own actual_gross_premium


def test_summarize_by_group_size_band_assigns_the_correct_band_by_size():
    def _group(name, size):
        return [
            analyze_portfolio_member(
                _member(beneficiary_id=f"{name}{i}", contract=name, master_contract=name), {}, RATE_CARDS, [], {},
            )
            for i in range(size)
        ]

    results = _group("A", 5) + _group("B", 30) + _group("C", 75) + _group("D", 150)
    rows = {r["band"]: r for r in summarize_by_group_size_band(results)}
    assert rows["1-10"]["group_count"] == 1
    assert rows["1-10"]["member_count"] == 5
    assert rows["11-50"]["group_count"] == 1
    assert rows["11-50"]["member_count"] == 30
    assert rows["51-100"]["group_count"] == 1
    assert rows["51-100"]["member_count"] == 75
    assert rows["100+"]["group_count"] == 1
    assert rows["100+"]["member_count"] == 150


def test_summarize_by_group_size_band_includes_out_of_scope_members_own_premium_and_claims():
    # Group headcount/premium/claims pooling shouldn't quietly drop an
    # out-of-scope member's own contribution - same fix as
    # executive_portfolio_summary's own out-of-scope regression.
    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1", network_type_raw="MSH Intl Network", contract="Acme", master_contract="Acme"),
            {}, RATE_CARDS, [], {},
        ),
    ]
    rows = summarize_by_group_size_band(results)
    band = next(r for r in rows if r["band"] == "1-10")
    assert band["member_count"] == 1
    assert band["actual_premium"] == 2500.0


def test_summarize_new_vs_renewal_classifies_by_distinct_policy_years():
    results = [
        # "One Year Co" has only one policy year on file - New Business.
        analyze_portfolio_member(
            _member(beneficiary_id="N1", contract="One Year Co", master_contract="One Year Co", policy_start_date=date(2026, 1, 1)),
            {}, RATE_CARDS, [], {},
        ),
        # "Renewed Co" has two members on two different policy years - Renewal.
        analyze_portfolio_member(
            _member(beneficiary_id="R1", contract="Renewed Co", master_contract="Renewed Co", policy_start_date=date(2025, 1, 1)),
            {}, RATE_CARDS, [], {},
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="R2", contract="Renewed Co", master_contract="Renewed Co", policy_start_date=date(2026, 1, 1)),
            {}, RATE_CARDS, [], {},
        ),
    ]
    rows = {r["classification"]: r for r in summarize_new_vs_renewal(results)}
    assert rows["New Business"]["group_count"] == 1
    assert rows["New Business"]["member_count"] == 1
    assert rows["Renewal"]["group_count"] == 1
    assert rows["Renewal"]["member_count"] == 2


def _claim(**overrides):
    base = {
        "patient_id": "P1", "group_name": "Acme Sub LLC", "client_name": "Acme Holdings",
        "provider_name": "Some Hospital", "diagnosis_description": "Some diagnosis",
        "date_of_treatment": date(2026, 6, 1), "final_amount": 1000.0,
    }
    base.update(overrides)
    return base


def test_top_claims_by_value_ranks_individual_claim_lines_highest_first():
    claims = [
        _claim(patient_id="P1", final_amount=5000.0),
        _claim(patient_id="P2", final_amount=250000.0),
        _claim(patient_id="P3", final_amount=80000.0),
    ]
    top = top_claims_by_value(claims, top_n=2)
    assert [c["patient_id"] for c in top] == ["P2", "P3"]
    assert top[0]["final_amount"] == 250000.0


def test_top_members_by_total_claims_carries_diagnoses_and_member_status():
    from datetime import date as _d
    claims = [
        _claim(patient_id="P1", final_amount=60_000.0, diagnosis_description="Cancer treatment",
               policy_end_date=_d(2026, 1, 1), member_end_date=_d(2026, 1, 1)),
        _claim(patient_id="P1", final_amount=55_000.0, diagnosis_description="Cancer follow-up",
               policy_end_date=_d(2026, 1, 1), member_end_date=_d(2026, 1, 1)),
        # Left early - member_end_date falls short of the policy's own end date.
        _claim(patient_id="P2", final_amount=90_000.0, diagnosis_description="Appendicitis",
               policy_end_date=_d(2026, 1, 1), member_end_date=_d(2025, 6, 1)),
        # Neither date on file at all.
        _claim(patient_id="P3", final_amount=70_000.0, diagnosis_description="Fracture"),
    ]
    rows = {r["patient_id"]: r for r in top_members_by_total_claims(claims, top_n=10)}
    assert rows["P1"]["total_claims"] == 115_000.0
    assert rows["P1"]["diagnoses"] == ["Cancer follow-up", "Cancer treatment"]
    assert rows["P1"]["member_status"] == "Active"
    assert rows["P2"]["member_status"] == "Deleted"
    assert rows["P3"]["member_status"] == "Unknown"


def test_utilization_by_encounter_type_splits_op_ip_maternity():
    claims = [
        _claim(ip_op_maternity="OP", final_amount=100.0),
        _claim(ip_op_maternity="OP", final_amount=200.0),
        _claim(ip_op_maternity="IP", final_amount=5000.0),
        _claim(ip_op_maternity="MATERNITY", final_amount=3000.0),
    ]
    rows = {r["encounter_type"]: r for r in utilization_by_encounter_type(claims)}
    assert rows["Op"]["claim_count"] == 2
    assert rows["Op"]["total_value"] == 300.0
    assert rows["Op"]["average_value"] == 150.0
    assert rows["Ip"]["total_value"] == 5000.0
    assert rows["Maternity"]["total_value"] == 3000.0
    # Percentages should sum to ~100% across all rows (each row's own
    # rounding to 1 decimal place can drift the total by a tenth or so).
    assert abs(sum(r["pct_of_total"] for r in rows.values()) - 100.0) < 0.5


def test_utilization_by_benefit_category_relabels_known_categories():
    claims = [
        _claim(medical_category="PHARMACY", final_amount=100.0),
        _claim(medical_category="VISION CARE", final_amount=200.0),
        _claim(medical_category="PSYCHIATRY", final_amount=300.0),
        _claim(medical_category="PARAMEDICAL", final_amount=400.0),
    ]
    rows = {r["category"]: r for r in utilization_by_benefit_category(claims)}
    assert rows["Pharmacy"]["total_value"] == 100.0
    assert rows["Optical"]["total_value"] == 200.0
    assert rows["Mental Health"]["total_value"] == 300.0
    # PARAMEDICAL with no treatment recorded is NOT assumed to be
    # physiotherapy - that assumption is what previously let alternative
    # therapy hide inside a "Physiotherapy" row. Without the treatment
    # there is nothing to classify it on, so it stays unclassified.
    assert rows["Other Paramedical"]["total_value"] == 400.0
    assert "Physiotherapy" not in rows


def test_utilization_by_benefit_category_combines_dental_categories():
    claims = [
        _claim(medical_category="GENERAL DENTAL", final_amount=100.0),
        _claim(medical_category="ORTHODONTIA", final_amount=200.0),
        _claim(medical_category="DENTAL PROSTHESIS", final_amount=300.0),
    ]
    rows = {r["category"]: r for r in utilization_by_benefit_category(claims)}
    assert rows["Dental"]["claim_count"] == 3
    assert rows["Dental"]["total_value"] == 600.0


def test_utilization_by_benefit_category_keeps_unmapped_categories_under_their_own_name():
    # LABORATORY has no equivalent on the user's requested taxonomy - shown
    # under its own real name rather than folded into a vague "Other".
    claims = [_claim(medical_category="LABORATORY", final_amount=500.0)]
    rows = {r["category"]: r for r in utilization_by_benefit_category(claims)}
    assert rows["Laboratory"]["total_value"] == 500.0


def test_top_members_by_total_claims_sums_across_claim_lines():
    claims = [
        _claim(patient_id="P1", final_amount=30000.0),
        _claim(patient_id="P1", final_amount=40000.0),  # P1's total (70k) beats P2's single claim
        _claim(patient_id="P2", final_amount=60000.0),
    ]
    top = top_members_by_total_claims(claims, top_n=10)
    assert top[0]["patient_id"] == "P1"
    assert top[0]["total_claims"] == 70000.0
    assert top[0]["claim_count"] == 2
    assert top[1]["patient_id"] == "P2"


def test_claims_above_thresholds_counts_and_totals_each_bucket():
    claims = [_claim(final_amount=40000.0), _claim(final_amount=60000.0), _claim(final_amount=300000.0)]
    rows = claims_above_thresholds(claims, thresholds=(50_000.0, 250_000.0))
    by_threshold = {r["threshold"]: r for r in rows}
    assert by_threshold[50_000.0]["claim_count"] == 2  # 60k and 300k
    assert by_threshold[50_000.0]["total_value"] == 360000.0
    assert by_threshold[250_000.0]["claim_count"] == 1
    assert by_threshold[250_000.0]["total_value"] == 300000.0


def test_recurring_high_cost_members_requires_multiple_large_claims():
    claims = [
        # P1: 3 separate claims each >= the 50k threshold - recurring.
        _claim(patient_id="P1", final_amount=60000.0),
        _claim(patient_id="P1", final_amount=55000.0),
        _claim(patient_id="P1", final_amount=70000.0),
        # P2: one single huge claim - NOT recurring, just one catastrophic line.
        _claim(patient_id="P2", final_amount=500000.0),
    ]
    recurring = recurring_high_cost_members(claims, claim_threshold=50_000.0, min_claim_count=3)
    patient_ids = [r["patient_id"] for r in recurring]
    assert "P1" in patient_ids
    assert "P2" not in patient_ids
    p1 = next(r for r in recurring if r["patient_id"] == "P1")
    assert p1["large_claim_count"] == 3
    assert p1["total_claims"] == 185000.0


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


def test_summarize_portfolio_rolls_up_ibnr_and_computes_loss_ratio_incl_ibnr():
    # as_of is exactly 30 days after M1's policy_start_date, so its IBNR
    # run rate (total paid so far / elapsed days * 30) equals its total
    # paid so far exactly - a clean identity for the assertion below.
    as_of = date(2026, 1, 31)
    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1", policy_start_date=date(2026, 1, 1)),
            {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [],
            {"M1": [
                {"date_of_treatment": date(2026, 1, 15), "final_amount": 1000.0, "claim_status": "Paid Claims"},
            ]},
            as_of=as_of,
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M2", gender="F", policy_start_date=date(2024, 1, 1)),  # already expired
            {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [],
            {"M2": [
                {"date_of_treatment": date(2026, 1, 15), "final_amount": 500.0, "claim_status": "Paid Claims"},
            ]},
            as_of=as_of,
        ),
    ]
    rows = summarize_portfolio(results, "product")
    bronze = next(r for r in rows if r["product"] == "Bronze")
    # M1's own paid-to-date run rate counts as IBNR (its policy hasn't
    # expired); M2's identical claim does NOT, since M2's policy started
    # more than 365 days before as_of.
    assert bronze["ibnr"] == 1000.0
    assert bronze["actual_claims"] == 1500.0
    assert bronze["actual_premium"] == 5000.0
    assert bronze["loss_ratio_incl_ibnr"] == round((1500.0 + 1000.0) / 5000.0, 4)
    assert bronze["loss_ratio_incl_ibnr"] != bronze["loss_ratio_vs_actual"]


def test_summarize_portfolio_computes_claim_frequency_and_severity():
    # Frequency = claims per earned member-year (exposure-adjusted);
    # severity = average AED cost per claim - two portfolios can share the
    # same loss ratio for very different reasons (many small claims vs. a
    # few large ones), which is exactly what these two numbers tell apart.
    results = [
        analyze_portfolio_member(
            _member(beneficiary_id="M1"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [],
            {"M1": [
                {"date_of_treatment": None, "final_amount": 100.0, "claim_status": "Paid Claims"},
                {"date_of_treatment": None, "final_amount": 200.0, "claim_status": "Paid Claims"},
            ]},
        ),
        analyze_portfolio_member(
            _member(beneficiary_id="M2", gender="F"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [],
            {"M2": [{"date_of_treatment": None, "final_amount": 4700.0, "claim_status": "Paid Claims"}]},
        ),
    ]
    rows = summarize_portfolio(results, "product")
    bronze = next(r for r in rows if r["product"] == "Bronze")
    assert bronze["claim_count"] == 3
    assert bronze["earned_member_years"] == 2.0  # no policy dates set - each member defaults to fully earned
    assert bronze["claim_frequency"] == round(3 / 2.0, 3)
    assert bronze["claim_severity"] == round(5000.0 / 3, 2)  # 100+200+4700 total, 3 claims


def test_summarize_portfolio_claim_frequency_and_severity_are_none_without_claims():
    results = [analyze_portfolio_member(_member(), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {})]
    rows = summarize_portfolio(results, "product")
    bronze = next(r for r in rows if r["product"] == "Bronze")
    assert bronze["claim_count"] == 0
    assert bronze["claim_frequency"] == 0.0  # earned_member_years is still 1.0 here, so this is a real 0 rate
    assert bronze["claim_severity"] is None  # can't average over zero claims


def test_summarize_portfolio_includes_out_of_scope_members_under_their_own_product():
    # A member the rate card cannot price still holds a real product and
    # still carries real premium and claims - excluding them understated
    # every account they belonged to. They roll up under their own
    # product, flagged by out_of_scope_member_count.
    results = [
        analyze_portfolio_member(_member(beneficiary_id="M1"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {}),
        analyze_portfolio_member(_member(beneficiary_id="M2", network_type_raw="MSH Intl Network"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {}),
    ]
    rows = summarize_portfolio(results, "product")

    assert len(rows) == 1
    row = rows[0]
    assert row["product"] == "Bronze"
    assert row["member_count"] == 2
    assert row["out_of_scope_member_count"] == 1
    assert row["priced_member_count"] == 1  # only one of them can be rate-card priced


def test_summarize_portfolio_measures_vs_standard_against_the_priced_members_only():
    # The rate-card comparisons must not count an unpriceable member's
    # claims against the priced members' standard premium - that would
    # overstate the ratio purely because of who the card covers.
    priced_only = [
        analyze_portfolio_member(_member(beneficiary_id="M1"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {}),
    ]
    with_unpriceable = priced_only + [
        analyze_portfolio_member(_member(beneficiary_id="M2", network_type_raw="MSH Intl Network"), {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {}),
    ]

    a = summarize_portfolio(priced_only, "product")[0]
    b = summarize_portfolio(with_unpriceable, "product")[0]

    assert b["loss_ratio_vs_standard"] == a["loss_ratio_vs_standard"]
    assert b["actual_vs_standard_pct"] == a["actual_vs_standard_pct"]
    # ...while the whole-bucket figures do grow with the extra member.
    assert b["actual_premium"] > a["actual_premium"]


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
            "ibnr": 0.0,
            "out_of_scope_member_count": 0,
            "loss_ratio_vs_standard": None,
            "loss_ratio_vs_actual": 0.0,
            "loss_ratio_incl_ibnr": 0.0,
            "actual_vs_standard_pct": None,
            "earned_member_years": 1.0,
            "burning_cost": 0.0,
            "claim_count": 0,
            "claim_frequency": 0.0,
            "claim_severity": None,
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


def test_price_case_against_burning_cost_excludes_a_low_credibility_bucket():
    # A bucket with a huge burning_cost but too few earned member-years
    # behind it (one large claim on a handful of members) must be excluded
    # the same way a missing bucket is, not trusted as a real rate - see
    # MIN_CREDIBLE_MEMBER_YEARS.
    from app.scoring.rules.portfolio_analysis import price_case_against_burning_cost

    census = [{"category": "A", "age": 30, "gender": "M", "marital_status": "single", "relation": "employee", "emirates": "Dubai"}]
    categories = [{"category": "A", "product": "Bronze", "network": "MSH Regular", "tpa": "MSH MENA", "variant_selections": {}}]
    burning_cost_rows = [
        {
            "product": "Bronze", "network": "MSH Regular", "age_band": "18-40", "gender": "M",
            "burning_cost": 284217.04, "low_credibility": True,
        },
    ]
    result = price_case_against_burning_cost(census, categories, RATE_CARDS, burning_cost_rows)
    cat = result["categories"][0]
    assert cat["priced_member_count"] == 0
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


def test_renewal_due_accounts_includes_only_clients_within_the_window():
    members = [
        _member(beneficiary_id="M1", master_contract="Due Soon Co", policy_end_date=date(2026, 3, 15)),
        _member(beneficiary_id="M2", master_contract="Due Soon Co", policy_end_date=date(2026, 3, 15)),
        _member(beneficiary_id="M3", master_contract="Too Far Out Co", policy_end_date=date(2026, 12, 1)),
        _member(beneficiary_id="M4", master_contract="Already Past Co", policy_end_date=date(2026, 1, 1)),
    ]
    due = renewal_due_accounts(members, within_days=60, as_of=date(2026, 2, 1))
    assert [d["master_client"] for d in due] == ["Due Soon Co"]
    assert due[0]["member_count"] == 2
    assert due[0]["policy_end_date"] == date(2026, 3, 15)
    assert due[0]["days_until_renewal"] == 42


def test_renewal_due_accounts_sorts_by_soonest_first():
    members = [
        _member(beneficiary_id="M1", master_contract="Later Co", policy_end_date=date(2026, 3, 20)),
        _member(beneficiary_id="M2", master_contract="Sooner Co", policy_end_date=date(2026, 2, 10)),
    ]
    due = renewal_due_accounts(members, within_days=60, as_of=date(2026, 2, 1))
    assert [d["master_client"] for d in due] == ["Sooner Co", "Later Co"]


def test_renewal_due_accounts_uses_the_latest_end_date_seen_per_client():
    # A stray row with a missing/earlier date shouldn't pull the whole
    # client's own renewal date earlier than its real shared term.
    members = [
        _member(beneficiary_id="M1", master_contract="Acme Holdings", policy_end_date=date(2026, 3, 15)),
        _member(beneficiary_id="M2", master_contract="Acme Holdings", policy_end_date=None),
    ]
    due = renewal_due_accounts(members, within_days=60, as_of=date(2026, 2, 1))
    assert due[0]["policy_end_date"] == date(2026, 3, 15)
    assert due[0]["member_count"] == 2


def test_renewal_due_accounts_skips_clients_with_no_end_date_at_all():
    members = [_member(beneficiary_id="M1", master_contract="No Date Co", policy_end_date=None)]
    assert renewal_due_accounts(members, within_days=60, as_of=date(2026, 2, 1)) == []


def _lr_member(master_client, policy_start, paid=0.0, outstanding=0.0, gross=0.0, claim_count=0):
    return {
        "master_client": master_client,
        "policy_start_date": policy_start,
        "actual_claims_paid": paid,
        "actual_claims_outstanding": outstanding,
        "written_premium": gross,
        "claim_count": claim_count,
    }


def test_account_loss_ratio_rows_reproduce_the_books_own_loss_ratio_sheet():
    # Real figures for BIC BRED (SUISSE) from HealthCross's own LOSS RATIO
    # sheet, report date 2026-07-15: Days 320, IBNR 13,422.11,
    # Incurred 171,294.61, Earned 231,221.90, Net 166,479.77,
    # Gross LR 0.7408, Net LR 1.0289.
    members = [_lr_member("BIC BRED (SUISSE)", date(2025, 8, 30),
                          paid=143_169.20, outstanding=14_703.30, gross=263_737.479452)]
    opex = {"BIC BRED (SUISSE)": [{"start_date": None, "end_date": None, "opex_pct": 0.28}]}

    rows = account_loss_ratio_rows(members, as_of=date(2026, 7, 15), opex_records_by_client=opex)

    assert len(rows) == 1
    row = rows[0]
    assert row["days"] == 320  # inclusive of the effective date itself
    assert row["expired"] is False
    assert row["ibnr"] == pytest.approx(13_422.11, abs=0.01)
    assert row["incurred_claims"] == pytest.approx(171_294.61, abs=0.01)
    assert row["earned_premium"] == pytest.approx(231_221.90, abs=0.01)
    assert row["net_premium"] == pytest.approx(166_479.77, abs=0.01)
    assert row["gross_loss_ratio"] == pytest.approx(0.7408, abs=0.0001)
    assert row["net_loss_ratio"] == pytest.approx(1.0289, abs=0.0001)


def test_account_loss_ratio_expired_policy_has_no_ibnr_and_earns_the_full_premium():
    # ACQUISIT DMCC from the same sheet: 441 days elapsed, so the policy
    # term has fully run - IBNR is zero and the FULL gross premium is
    # earned rather than prorating past 100%.
    members = [_lr_member("ACQUISIT DMCC", date(2025, 5, 1),
                          paid=385_239.86, outstanding=1_288.64, gross=883_707.167123)]
    opex = {"ACQUISIT DMCC": [{"start_date": None, "end_date": None, "opex_pct": 0.23}]}

    row = account_loss_ratio_rows(members, as_of=date(2026, 7, 15), opex_records_by_client=opex)[0]

    assert row["days"] == 441
    assert row["expired"] is True
    assert row["ibnr"] == 0.0
    assert row["incurred_claims"] == pytest.approx(386_528.50, abs=0.01)
    assert row["earned_premium"] == pytest.approx(883_707.17, abs=0.01)  # full gross, not prorated
    assert row["gross_loss_ratio"] == pytest.approx(0.4374, abs=0.0001)


def test_account_loss_ratio_keeps_each_policy_period_as_its_own_row():
    # A client that has already renewed has members on two policy periods
    # in the same upload - each earns and reserves separately, so an
    # expired year's settled claims never blend into the open year's.
    members = [
        _lr_member("ACQUISIT DMCC", date(2025, 5, 1), paid=385_239.86, outstanding=1_288.64, gross=883_707.17),
        _lr_member("ACQUISIT DMCC", date(2026, 5, 1), paid=85_428.60, outstanding=104_329.06, gross=886_982.28),
    ]
    rows = account_loss_ratio_rows(members, as_of=date(2026, 7, 15))

    assert len(rows) == 2
    expired, current = rows[0], rows[1]
    assert expired["policy_start_date"] == "2025-05-01"
    assert expired["ibnr"] == 0.0
    assert current["policy_start_date"] == "2026-05-01"
    assert current["days"] == 76
    assert current["ibnr"] == pytest.approx(33_721.82, abs=0.01)


class TestLossRatioGroupBy:
    """'with Group' (the default, master_client) combines a client's own
    subgroups into one row; 'without Group' (group_by="client") breaks
    the same book back out by raw subgroup - "Loss ratio with or without
    Group" in the underwriter's own words.
    """

    def _sub(self, master, subgroup, policy_start, paid=0.0, outstanding=0.0, gross=0.0):
        return {
            "master_client": master, "client": subgroup, "policy_start_date": policy_start,
            "actual_claims_paid": paid, "actual_claims_outstanding": outstanding,
            "written_premium": gross, "claim_count": 0,
        }

    def test_default_combines_subgroups_into_one_master_client_row(self):
        members = [
            self._sub("ACME GROUP", "ACME DXB", date(2026, 1, 1), paid=10_000.0, gross=50_000.0),
            self._sub("ACME GROUP", "ACME AUH", date(2026, 1, 1), paid=5_000.0, gross=30_000.0),
        ]
        rows = account_loss_ratio_rows(members, as_of=date(2026, 6, 1))
        assert len(rows) == 1
        assert rows[0]["master_client"] == "ACME GROUP"
        assert rows[0]["paid"] == pytest.approx(15_000.0, abs=0.01)

    def test_group_by_client_breaks_the_same_book_out_by_subgroup(self):
        members = [
            self._sub("ACME GROUP", "ACME DXB", date(2026, 1, 1), paid=10_000.0, gross=50_000.0),
            self._sub("ACME GROUP", "ACME AUH", date(2026, 1, 1), paid=5_000.0, gross=30_000.0),
        ]
        rows = account_loss_ratio_rows(members, as_of=date(2026, 6, 1), group_by="client")
        assert len(rows) == 2
        by_name = {r["master_client"]: r for r in rows}
        assert set(by_name) == {"ACME DXB", "ACME AUH"}
        assert by_name["ACME DXB"]["paid"] == pytest.approx(10_000.0, abs=0.01)
        assert by_name["ACME AUH"]["paid"] == pytest.approx(5_000.0, abs=0.01)
        # Same total either way - grouping only changes how it is sliced.
        assert sum(r["paid"] for r in rows) == pytest.approx(15_000.0, abs=0.01)

    def test_an_unknown_group_by_is_rejected(self):
        with pytest.raises(ValueError):
            account_loss_ratio_rows([self._sub("A", "A", date(2026, 1, 1))],
                                    as_of=date(2026, 6, 1), group_by="nonsense")

    def test_a_subgroup_missing_its_own_client_field_falls_back_to_master(self):
        # A member result that never got a "client" value (shouldn't
        # happen from real ingestion, but nothing should crash on it) -
        # falls back to master_client rather than dropping the row.
        member = self._sub("ACME GROUP", None, date(2026, 1, 1), paid=1_000.0)
        rows = account_loss_ratio_rows([member], as_of=date(2026, 6, 1), group_by="client")
        assert len(rows) == 1
        assert rows[0]["master_client"] == "ACME GROUP"


def test_account_loss_ratio_has_no_ibnr_when_nothing_has_been_paid_yet():
    # A brand-new policy with outstanding-only claims has no paid run rate
    # to project a 30-day tail from.
    members = [_lr_member("NEW CO", date(2026, 7, 1), paid=0.0, outstanding=5_000.0, gross=100_000.0)]
    row = account_loss_ratio_rows(members, as_of=date(2026, 7, 15))[0]

    assert row["days"] == 15
    assert row["ibnr"] == 0.0
    assert row["incurred_claims"] == 5_000.0


def test_account_loss_ratio_totals_recompute_ratios_from_summed_amounts():
    members = [
        _lr_member("SMALL CO", date(2026, 1, 1), paid=90_000.0, outstanding=0.0, gross=100_000.0),
        _lr_member("BIG CO", date(2026, 1, 1), paid=100_000.0, outstanding=0.0, gross=1_000_000.0),
    ]
    rows = account_loss_ratio_rows(members, as_of=date(2026, 7, 15))
    totals = account_loss_ratio_totals(rows)

    assert totals["account_count"] == 2
    # Weighted by the summed amounts, NOT the mean of the two rows' own
    # very different ratios - the small account must not carry equal weight.
    expected = totals["incurred_claims"] / totals["earned_premium"]
    assert totals["gross_loss_ratio"] == pytest.approx(expected, abs=0.0001)
    naive_mean = sum(r["gross_loss_ratio"] for r in rows) / 2
    assert totals["gross_loss_ratio"] != pytest.approx(naive_mean, abs=0.01)


def test_account_loss_ratio_can_report_on_either_premium_column():
    # The Membership export carries BOTH GrossPremium and
    # ActualGrossPremium; only the latter has ever fed the analysis, so a
    # book total built on one will not match the other.
    members = [{
        "master_client": "SOME CO", "policy_start_date": date(2026, 1, 1),
        "actual_claims_paid": 50_000.0, "actual_claims_outstanding": 0.0,
        "written_premium": 100_000.0,        # ActualGrossPremium
        "booked_gross_premium": 140_000.0,   # GrossPremium
        "claim_count": 3,
    }]

    actual = account_loss_ratio_rows(members, as_of=date(2026, 7, 15))[0]
    booked = account_loss_ratio_rows(members, as_of=date(2026, 7, 15), premium_basis="booked")[0]

    assert actual["premium_basis"] == "actual"
    assert actual["gross_premium"] == 100_000.0
    assert booked["premium_basis"] == "booked"
    assert booked["gross_premium"] == 140_000.0
    # Same claims over a larger premium base - the booked basis reports a
    # materially lower loss ratio, which is exactly why the basis has to
    # be stated rather than assumed.
    assert booked["gross_loss_ratio"] < actual["gross_loss_ratio"]


def test_account_loss_ratio_rejects_an_unknown_premium_basis():
    members = [{
        "master_client": "SOME CO", "policy_start_date": date(2026, 1, 1),
        "actual_claims_paid": 0.0, "actual_claims_outstanding": 0.0,
        "written_premium": 100_000.0, "claim_count": 0,
    }]
    with pytest.raises(ValueError):
        account_loss_ratio_rows(members, as_of=date(2026, 7, 15), premium_basis="net")


def test_utilization_splits_paramedical_by_the_actual_treatment():
    # PARAMEDICAL holds physiotherapy, every alternative therapy on the
    # book, and nursing. Reporting it as one "Physiotherapy" row (as this
    # used to) overstated physiotherapy and hid alternative treatment
    # completely - the category alone cannot tell them apart, only the
    # treatment can.
    claims = [
        {"medical_category": "PARAMEDICAL", "medical_act": "Physical Therapist", "final_amount": 1000.0},
        {"medical_category": "PARAMEDICAL", "medical_act": "Kinésithérapeute", "final_amount": 500.0},
        {"medical_category": "PARAMEDICAL", "medical_act": "Ayuverdic", "final_amount": 800.0},
        {"medical_category": "PARAMEDICAL", "medical_act": "Osteopath", "final_amount": 200.0},
        {"medical_category": "PARAMEDICAL", "medical_act": "Nursing services", "final_amount": 100.0},
    ]
    rows = {r["category"]: r for r in utilization_by_benefit_category(claims)}

    assert rows["Physiotherapy"]["total_value"] == 1500.0
    assert rows["Alternative Treatment"]["total_value"] == 1000.0
    assert rows["Other Paramedical"]["total_value"] == 100.0


def test_utilization_still_labels_the_other_categories_as_before():
    claims = [
        {"medical_category": "PHARMACY", "final_amount": 100.0},
        {"medical_category": "VISION CARE", "final_amount": 50.0},
        {"medical_category": "GENERAL DENTAL", "final_amount": 30.0},
        {"medical_category": "ORTHODONTIA", "final_amount": 20.0},
    ]
    rows = {r["category"]: r for r in utilization_by_benefit_category(claims)}

    assert rows["Pharmacy"]["total_value"] == 100.0
    assert rows["Optical"]["total_value"] == 50.0
    assert rows["Dental"]["total_value"] == 50.0  # the dental categories still combine
    assert rows["Dental"]["average_value"] == 25.0  # 50.0 across 2 claims (General Dental + Orthodontia)


def _nat_member(nationality, zone, claims, exposure, age=35, gender="M", premium=None, ibnr=0.0):
    return {
        "nationality": nationality, "nationality_zone": zone,
        "actual_claims": claims, "ibnr": ibnr,
        "actual_premium": premium,
        "earned_premium_fraction": exposure, "age": age, "gender": gender,
    }


def test_nationality_risk_table_blends_a_thin_nationality_toward_its_zone():
    # 40 members of a cheap nationality set the zone rate; one expensive
    # member with almost no exposure must not price at their raw rate.
    members = [_nat_member("Indian", "zone_1_asia", 1_000.0, 1.0) for _ in range(40)]
    members.append(_nat_member("Sudanese", "zone_1_asia", 12_000.0, 1.0))

    rows = {r["nationality"]: r for r in nationality_risk_table(members)}
    thin = rows["Sudanese"]

    assert thin["earned_member_years"] == 1.0
    assert thin["burning_cost"] == 12_000.0          # its own raw experience
    assert thin["credibility"] == 0.1                 # sqrt(1/100)
    # Blended lands far below the raw rate - one member-year cannot carry a 12x load.
    assert thin["credible_burning_cost"] < 3_000.0


def test_nationality_risk_table_lets_a_well_evidenced_nationality_keep_its_own_rate():
    members = [_nat_member("Indian", "zone_1_asia", 1_000.0, 1.0) for _ in range(120)]
    members += [_nat_member("Other", "zone_1_asia", 5_000.0, 1.0) for _ in range(5)]

    rows = {r["nationality"]: r for r in nationality_risk_table(members)}
    indian = rows["Indian"]

    assert indian["credibility"] == 1.0  # 120 member-years is past the standard
    assert indian["credible_burning_cost"] == indian["burning_cost"]


def test_nationality_risk_table_carries_the_mix_so_confounding_can_be_checked():
    # A nationality can look expensive because it is older or more female
    # rather than because of nationality - the mix is what makes that
    # visible instead of baking a confounded signal into a rate.
    members = [_nat_member("Egyptian", "zone_2_middle_east", 5_000.0, 1.0, age=50, gender="F")
               for _ in range(10)]
    members += [_nat_member("Egyptian", "zone_2_middle_east", 5_000.0, 1.0, age=40, gender="M")
                for _ in range(10)]

    row = nationality_risk_table(members)[0]
    assert row["avg_age"] == 45.0
    assert row["female_pct"] == 50.0
    assert row["member_count"] == 20


def test_nationality_risk_table_relativity_is_capped():
    members = [_nat_member("Cheap", "zone_1_asia", 100.0, 60.0)]
    members += [_nat_member("Wild", "zone_1_asia", 900_000.0, 60.0)]

    rows = {r["nationality"]: r for r in nationality_risk_table(members)}
    assert rows["Wild"]["relativity"] <= 2.0
    assert rows["Cheap"]["relativity"] >= 0.5


def test_nationality_risk_table_skips_members_with_no_nationality():
    members = [_nat_member(None, "zone_1_asia", 1_000.0, 1.0),
               _nat_member("Indian", "zone_1_asia", 1_000.0, 1.0)]
    rows = nationality_risk_table(members)
    assert [r["nationality"] for r in rows] == ["Indian"]


def test_nationality_risk_table_reports_erp_headcount_premium_and_plain_loss_ratio():
    # member_count doubles as the exposed risk population (ERP) headcount;
    # loss_ratio is incurred (claims + IBNR) over the nationality's own
    # actual premium - the reinsurance-facing reading sitting alongside
    # burning_cost's per-member-year one.
    members = [
        _nat_member("Indian", "zone_1_asia", 800.0, 1.0, premium=1000.0, ibnr=200.0),
        _nat_member("Indian", "zone_1_asia", 800.0, 1.0, premium=1000.0, ibnr=200.0),
    ]
    row = nationality_risk_table(members)[0]
    assert row["member_count"] == 2
    assert row["actual_premium"] == 2000.0
    assert row["incurred_claims"] == 2000.0  # (800+200) * 2
    assert row["loss_ratio"] == 1.0


def test_nationality_risk_table_loss_ratio_is_none_without_premium():
    members = [_nat_member("Indian", "zone_1_asia", 800.0, 1.0)]
    row = nationality_risk_table(members)[0]
    assert row["actual_premium"] == 0.0
    assert row["loss_ratio"] is None


def test_nationality_risk_table_marks_which_nationalities_can_be_priced_on_today():
    # A small book means most nationalities sit below full credibility, so
    # "can I act on this one yet" has to be explicit rather than something
    # read off a credibility number.
    members = [_nat_member("Indian", "zone_1_asia", 1_000.0, 1.0) for _ in range(80)]
    members += [_nat_member("Syrian", "zone_1_asia", 1_000.0, 1.0) for _ in range(4)]

    rows = {r["nationality"]: r for r in nationality_risk_table(members)}

    assert rows["Indian"]["pricing_ready"] is True     # 80 yrs -> 89% credibility
    assert rows["Syrian"]["pricing_ready"] is False    # 4 yrs -> 20%
    # A thin nationality still gets a factor - it is real information, and
    # it is what crosses the line as the book grows.
    assert rows["Syrian"]["relativity"] is not None


def test_nationality_risk_table_says_how_much_growth_reaches_full_credibility():
    members = [_nat_member("Egyptian", "zone_2_middle_east", 1_000.0, 1.0) for _ in range(30)]
    row = nationality_risk_table(members, full_credibility_member_years=100.0)[0]

    assert row["earned_member_years"] == 30.0
    assert row["member_years_to_full_credibility"] == 70.0


def test_pricing_readiness_threshold_is_adjustable():
    members = [_nat_member("Indian", "zone_1_asia", 1_000.0, 1.0) for _ in range(30)]
    lenient = nationality_risk_table(members, pricing_credibility=0.4)[0]
    strict = nationality_risk_table(members, pricing_credibility=0.9)[0]

    assert lenient["pricing_ready"] is True    # 30 yrs -> 55% credibility
    assert strict["pricing_ready"] is False


def test_period_overlap_days_counts_both_endpoints():
    assert period_overlap_days(date(2026, 1, 1), date(2026, 1, 10),
                               date(2026, 1, 5), date(2026, 1, 20)) == 6  # 5th..10th
    assert period_overlap_days(date(2026, 1, 1), date(2026, 1, 10),
                               date(2026, 2, 1), date(2026, 2, 10)) == 0
    assert period_overlap_days(None, date(2026, 1, 10), date(2026, 1, 1), date(2026, 1, 5)) == 0


def _cal_member(policy_start, policy_end, premium, beneficiary_id="B1"):
    return {
        "beneficiary_id": beneficiary_id, "master_client": "ACME",
        "policy_start_date": policy_start, "policy_end_date": policy_end,
        "written_premium": premium, "booked_gross_premium": premium,
    }


def test_calendar_basis_splits_a_year_spanning_policy_across_both_years():
    # 1 May 2025 - 1 May 2026, reported at 15 Jul 2026. The policy touches
    # two calendar years and its premium must land in each by the days
    # falling there - not wholly in the year it incepted.
    members = [_cal_member(date(2025, 5, 1), date(2026, 5, 1), 365_000.0)]
    claims = {"B1": [
        {"date_of_treatment": date(2025, 8, 10), "final_amount": 1_000.0, "claim_status": "Paid Claims"},
        {"date_of_treatment": date(2026, 3, 10), "final_amount": 4_000.0, "claim_status": "Paid Claims"},
    ]}

    rows = {r["calendar_year"]: r for r in
            account_calendar_loss_ratio_rows(members, claims, as_of=date(2026, 7, 15))}

    assert set(rows) == {2025, 2026}
    # 1 May - 31 Dec 2025 inclusive = 245 days; 1 Jan - 1 May 2026 = 121 days.
    assert rows[2025]["days"] == 245
    assert rows[2026]["days"] == 121
    assert rows[2025]["earned_premium"] == pytest.approx(365_000.0 * 245 / 365, abs=0.01)
    assert rows[2026]["earned_premium"] == pytest.approx(365_000.0 * 121 / 365, abs=0.01)
    # Each year keeps only the claims actually treated inside it.
    assert rows[2025]["paid"] == 1_000.0
    assert rows[2026]["paid"] == 4_000.0


def test_calendar_basis_aggregates_two_policies_into_one_year_for_a_renewed_client():
    # A renewed client's calendar year holds the tail of the expiring
    # policy AND the start of the new one - the whole point of the basis.
    members = [
        _cal_member(date(2025, 5, 1), date(2026, 5, 1), 365_000.0, beneficiary_id="B1"),
        _cal_member(date(2026, 5, 1), date(2027, 5, 1), 365_000.0, beneficiary_id="B2"),
    ]
    rows = {r["calendar_year"]: r for r in
            account_calendar_loss_ratio_rows(members, {}, as_of=date(2026, 7, 15))}

    assert rows[2026]["member_count"] == 2  # both policies contribute
    # Expiring policy 1 Jan-1 May (121d) + new policy 1 May-15 Jul (76d).
    expected = 365_000.0 * 121 / 365 + 365_000.0 * 76 / 365
    assert rows[2026]["earned_premium"] == pytest.approx(expected, abs=0.01)


def test_calendar_basis_reserves_ibnr_only_for_a_year_still_open():
    # A two-year policy still running at the report date: 2025 is closed
    # and fully developed, 2026 is only part-run and still has a tail.
    members = [_cal_member(date(2025, 1, 1), date(2027, 1, 1), 365_000.0)]
    claims = {"B1": [
        {"date_of_treatment": date(2025, 8, 10), "final_amount": 30_000.0, "claim_status": "Paid Claims"},
        {"date_of_treatment": date(2026, 3, 10), "final_amount": 30_000.0, "claim_status": "Paid Claims"},
    ]}
    rows = {r["calendar_year"]: r for r in
            account_calendar_loss_ratio_rows(members, claims, as_of=date(2026, 7, 15))}

    # 2025 closed long before the report date - its claims are in.
    assert rows[2025]["ibnr"] == 0.0
    assert rows[2025]["expired"] is True
    # 2026's window runs to the report date, so it still has a tail.
    assert rows[2026]["ibnr"] > 0.0
    assert rows[2026]["expired"] is False


def test_calendar_basis_treats_a_year_whose_policy_already_expired_as_developed():
    # The window ends when the POLICY ends, not at the year end - a policy
    # that expired in May and is reported in July has no tail left, even
    # though its calendar year is still running.
    members = [_cal_member(date(2025, 5, 1), date(2026, 5, 1), 365_000.0)]
    claims = {"B1": [{"date_of_treatment": date(2026, 3, 10), "final_amount": 30_000.0,
                      "claim_status": "Paid Claims"}]}
    rows = {r["calendar_year"]: r for r in
            account_calendar_loss_ratio_rows(members, claims, as_of=date(2026, 7, 15))}

    assert rows[2026]["days"] == 121          # 1 Jan - 1 May, not to year end
    assert rows[2026]["ibnr"] == 0.0
    assert rows[2026]["expired"] is True


def test_calendar_basis_ignores_claims_outside_the_members_own_enrollment():
    # A member who left mid-year must not pick up claims from after they
    # left, even though those fall inside the calendar window.
    members = [{
        "beneficiary_id": "B1", "master_client": "ACME",
        "policy_start_date": date(2026, 1, 1), "policy_end_date": date(2026, 12, 31),
        "member_start_date": date(2026, 1, 1), "member_end_date": date(2026, 3, 31),
        "written_premium": 100_000.0,
    }]
    claims = {"B1": [
        {"date_of_treatment": date(2026, 2, 10), "final_amount": 500.0, "claim_status": "Paid Claims"},
        {"date_of_treatment": date(2026, 6, 10), "final_amount": 9_000.0, "claim_status": "Paid Claims"},
    ]}
    row = account_calendar_loss_ratio_rows(members, claims, as_of=date(2026, 7, 15))[0]
    assert row["paid"] == 500.0


def test_analyze_portfolio_member_carries_the_dates_a_calendar_split_needs():
    # Regression: analyze_portfolio_member returned policy_start_date but
    # not policy_end_date, so calendar-year splitting fell back to
    # start==end and collapsed every window to a single day - earned
    # premium came out ~250x too low with no error anywhere. Unit tests
    # missed it because they built member dicts by hand; only the real
    # pipeline shape exposed it.
    result = analyze_portfolio_member(
        _member(
            policy_start_date=date(2025, 5, 1), policy_end_date=date(2026, 5, 1),
            member_start_date=date(2025, 5, 1), member_end_date=date(2026, 5, 1),
        ),
        {"Acme Sub LLC": "Bronze"}, RATE_CARDS, [], {},
    )
    assert result["policy_start_date"] == date(2025, 5, 1)
    assert result["policy_end_date"] == date(2026, 5, 1)
    assert result["member_start_date"] == date(2025, 5, 1)
    assert result["member_end_date"] == date(2026, 5, 1)


def test_calendar_split_reconciles_back_to_the_full_annual_premium():
    # The two halves of a year-spanning policy must sum to what the same
    # policy earns in full - a split that loses or duplicates premium
    # would quietly misstate every calendar-year loss ratio.
    members = [_cal_member(date(2025, 5, 1), date(2026, 5, 1), 365_000.0)]
    rows = account_calendar_loss_ratio_rows(members, {}, as_of=date(2026, 7, 15))

    total_days = sum(r["days"] for r in rows)
    total_earned = sum(r["earned_premium"] for r in rows)
    assert total_days == 366                      # 1 May 2025 - 1 May 2026 inclusive
    assert total_earned == pytest.approx(365_000.0 * 366 / 365, abs=0.01)


def test_calendar_basis_does_not_double_count_a_year_spanning_policys_written_premium():
    # Written premium belongs to the inception year only. Adding the annual
    # figure to every window a policy touches inflated any book-wide Gross
    # total by the number of years the policy spanned - 2x here.
    members = [_cal_member(date(2025, 5, 1), date(2026, 5, 1), 365_000.0)]
    rows = account_calendar_loss_ratio_rows(members, {}, as_of=date(2026, 7, 15))

    by_year = {r["calendar_year"]: r for r in rows}
    assert by_year[2025]["gross_premium"] == 365_000.0   # incepts here
    assert by_year[2026]["gross_premium"] == 0.0         # runs through, not written here
    assert sum(r["gross_premium"] for r in rows) == 365_000.0
    # Earned still spreads across both years, unlike written.
    assert by_year[2025]["earned_premium"] > 0
    assert by_year[2026]["earned_premium"] > 0


def test_calendar_basis_counts_a_renewal_boundary_claim_only_once():
    # The expiring policy ends and the new one starts on the SAME day, so
    # both windows contain it and both fall in the same calendar year. A
    # claim treated that day is one payment and must be counted once.
    members = [
        _cal_member(date(2025, 5, 1), date(2026, 5, 1), 365_000.0, beneficiary_id="B1"),
        _cal_member(date(2026, 5, 1), date(2027, 5, 1), 365_000.0, beneficiary_id="B1"),
    ]
    claims = {"B1": [
        {"date_of_treatment": date(2026, 5, 1), "final_amount": 250.0, "claim_status": "Paid Claims"},
    ]}
    rows = {r["calendar_year"]: r for r in
            account_calendar_loss_ratio_rows(members, claims, as_of=date(2026, 7, 15))}

    assert rows[2026]["paid"] == 250.0
    assert rows[2026]["claim_count"] == 1


def test_a_claim_on_the_renewal_date_belongs_to_the_incoming_policy():
    # The export's policy end date IS the next policy's start date, so the
    # period is half-open: a claim treated that day is the incoming
    # policy's, never the expiring one's, and never both.
    expiring = (date(2025, 5, 1), date(2026, 5, 1))
    incoming = (date(2026, 5, 1), date(2027, 5, 1))
    boundary = date(2026, 5, 1)

    assert _claim_matches_period(boundary, *expiring) is False
    assert _claim_matches_period(boundary, *incoming) is True
    # The day before still belongs to the expiring policy.
    assert _claim_matches_period(date(2026, 4, 30), *expiring) is True


def test_an_annual_policy_covers_exactly_365_days():
    # Counting the end date too would make a one-year policy 366 days.
    start, end = date(2025, 5, 1), date(2026, 5, 1)
    covered = sum(1 for i in range((end - start).days + 1)
                  if _claim_matches_period(start + timedelta(days=i), start, end))
    assert covered == 365


def test_a_malformed_same_day_period_still_matches_its_own_day():
    # Half-open would match nothing at all here; falling back keeps a real
    # claim rather than silently dropping every one for that member.
    day = date(2026, 3, 10)
    assert _claim_matches_period(day, day, day) is True
    assert _claim_matches_period(date(2026, 3, 11), day, day) is False


def test_renewal_boundary_claim_is_counted_once_across_both_policy_years():
    # End to end: the member renews, and the boundary claim lands in the
    # incoming policy's totals only.
    claims = {"B1": [
        {"date_of_treatment": date(2026, 5, 1), "final_amount": 250.0, "claim_status": "Paid Claims"},
    ]}
    expiring = _member(beneficiary_id="B1", policy_start_date=date(2025, 5, 1),
                       policy_end_date=date(2026, 5, 1))
    incoming = _member(beneficiary_id="B1", policy_start_date=date(2026, 5, 1),
                       policy_end_date=date(2027, 5, 1))

    a = actual_claims_for_member(expiring, claims)
    b = actual_claims_for_member(incoming, claims)

    assert a["total"] == 0.0 and a["count"] == 0
    assert b["total"] == 250.0 and b["count"] == 1
