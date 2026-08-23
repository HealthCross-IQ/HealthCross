from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class CaseCreate(BaseModel):
    broker_name: str
    company_name: str
    industry: str
    region: Optional[str] = None
    employee_count_declared: Optional[int] = None
    business_type: Optional[str] = None  # "new" or "existing"
    existing_insurer: Optional[str] = None
    current_annual_premium: Optional[float] = None
    target_premium: Optional[float] = None
    policy_start_date: Optional[date] = None
    tpa_fee_pct: Optional[float] = None
    commission_pct: Optional[float] = None
    hc_fee_pct: Optional[float] = None
    qic_fee_pct: Optional[float] = None


class RenewalIntakeRequest(BaseModel):
    """Open the renewal case for one account already on HealthCross's own
    book - see routes_portfolio_analysis's open_renewal_intake. Only the
    account name is required, because everything else is derived from the
    Membership export rather than typed in; broker/industry are accepted
    up front purely so the underwriter doesn't have to go back and edit
    the case when they already know them.
    """

    master_client: str
    broker_name: Optional[str] = None
    industry: Optional[str] = None
    reseed_census: bool = False


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    broker_name: str
    company_name: str
    industry: str
    region: Optional[str] = None
    employee_count_declared: Optional[int] = None
    existing_insurer: Optional[str] = None
    years_with_existing_insurer: Optional[int] = None
    target_premium: Optional[float] = None
    claims_available: Optional[bool] = None
    renewal_date: Optional[date] = None
    status: str
    submitted_at: datetime
    business_type: Optional[str] = None
    current_annual_premium: Optional[float] = None
    portfolio_master_client: Optional[str] = None
    policy_start_date: Optional[date] = None
    tpa_fee_pct: Optional[float] = None
    commission_pct: Optional[float] = None
    hc_fee_pct: Optional[float] = None
    qic_fee_pct: Optional[float] = None


class CensusRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_ref: Optional[str] = None
    category: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    marital_status: Optional[str] = None
    relation: Optional[str] = None
    emirates: Optional[str] = None
    salary_band: Optional[str] = None
    nationality: Optional[str] = None
    nationality_zone: Optional[str] = None
    dependents_count: int
    join_date: Optional[date] = None
    policy_start_date: Optional[date] = None
    policy_end_date: Optional[date] = None
    member_start_date: Optional[date] = None
    member_end_date: Optional[date] = None
    existing_annual_rate: Optional[float] = None
    new_annual_rate_override: Optional[float] = None


class MemberRateIn(BaseModel):
    census_record_id: int
    existing_annual_rate: Optional[float] = None
    new_annual_rate_override: Optional[float] = None


class BenefitPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    plan_name: Optional[str] = None
    annual_limit: Optional[float] = None
    room_type: Optional[str] = None
    deductible: float
    co_insurance_pct: float
    network_type: Optional[str] = None
    maternity_covered: bool
    maternity_limit: Optional[float] = None
    dental_covered: bool
    optical_covered: bool
    chronic_covered: bool
    pre_existing_covered: bool
    member_count: Optional[int] = None
    source_format: Optional[str] = None
    standard_summary: Optional[Dict[str, str]] = None
    raw_ocr_text: Optional[str] = None
    role: str = "existing"
    category: Optional[str] = None
    gross_premium: Optional[float] = None
    matched_quote_plan_id: Optional[int] = None
    nb_product: Optional[str] = None
    nb_network: Optional[str] = None
    nb_tpa: Optional[str] = None


class CaseCompletenessOut(BaseModel):
    census_count: int
    existing_benefit_plan_count: int
    quoted_benefit_plan_count: int
    claims_record_count: int
    claims_report_count: int
    claims_ledger_entry_count: int
    scorecard_count: int
    has_census: bool
    has_benefits: bool
    has_quote: bool
    has_claims: bool
    has_claims_ledger: bool
    has_scorecard: bool
    ready_to_score: bool
    latest_risk_tier: Optional[str] = None


class BenefitPlanMatchUpdate(BaseModel):
    # The quoted-role plan this existing-role plan should be compared
    # against in /benefits-comparison, or null to clear a manual mapping
    # and go back to the automatic category-letter/plan-name match.
    quoted_plan_id: Optional[int] = None


class BenefitSummaryUpdate(BaseModel):
    # Manual corrections to the standard 12-field summary (see
    # app/scoring/rules/benefits_summary.py's STANDARD_FIELDS) - keyed by
    # field name, e.g. {"annual_limit": "USD 4,000,000"}. Mainly for
    # OCR-extracted plans (scanned PDFs), where automatic extraction is
    # best-effort and some fields often come back unresolved - an empty
    # string clears a field back to unresolved rather than storing blank
    # text as if it were a real value from the document.
    fields: Dict[str, Optional[str]]
    # Renames the plan/category itself (e.g. "OCR extract (verify against
    # source)" -> "Cat B - Dubai") - omitted/blank leaves the name as-is.
    plan_name: Optional[str] = None
    # This category's own broker/insurer category label (e.g. "A") and its
    # New Business rate-card Product/Network/TPA pick - the source of
    # truth the New Business Quote prices against for this category (see
    # BenefitPlan.nb_product/nb_network/nb_tpa). Omitted leaves the
    # existing value as-is; an explicit "" clears it back to unset.
    category: Optional[str] = None
    nb_product: Optional[str] = None
    nb_network: Optional[str] = None
    nb_tpa: Optional[str] = None


class ManualBenefitPlanCreate(BaseModel):
    # Adds a brand-new, blank existing-role benefit plan for an
    # underwriter to fill in entirely by hand - for a document OCR
    # couldn't usefully read at all, where correcting individual fields on
    # an OCR-extracted plan isn't enough to start from.
    plan_name: str = "New plan"
    # Sets the category letter (e.g. "A") right away, not just the display
    # name - without this, a later append-mode upload for that same
    # category can't match this plan by category (they're both named
    # "Category A" but only one has category="A" set), so it gets added
    # alongside it instead of replacing it.
    category: Optional[str] = None


class ClaimsRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    member_ref: Optional[str] = None
    claim_date: Optional[date] = None
    service_type: Optional[str] = None
    diagnosis_category: Optional[str] = None
    amount_billed: Optional[float] = None
    amount_paid: Optional[float] = None
    policy_year: Optional[int] = None


class ClaimsLedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: Optional[str] = None
    claim_id: Optional[str] = None
    claim_status: Optional[str] = None
    policy_start_date: Optional[date] = None
    policy_end_date: Optional[date] = None
    member_start_date: Optional[date] = None
    member_end_date: Optional[date] = None
    date_of_treatment: Optional[date] = None
    relation: Optional[str] = None
    ip_op_maternity: Optional[str] = None
    medical_category: Optional[str] = None
    provider_name: Optional[str] = None
    diagnosis_code: Optional[str] = None
    diagnosis_description: Optional[str] = None
    claimed_amount: Optional[float] = None
    final_amount: Optional[float] = None


class CaseUpdate(BaseModel):
    business_type: Optional[str] = None
    existing_insurer: Optional[str] = None
    current_annual_premium: Optional[float] = None
    target_premium: Optional[float] = None
    policy_start_date: Optional[date] = None
    tpa_fee_pct: Optional[float] = None
    commission_pct: Optional[float] = None
    hc_fee_pct: Optional[float] = None
    qic_fee_pct: Optional[float] = None


class ClaimsReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    policy_number: Optional[str] = None
    policy_effective_date: Optional[date] = None
    policy_expiry_date: Optional[date] = None
    report_period_start: Optional[date] = None
    report_period_end: Optional[date] = None
    report_production_date: Optional[date] = None
    total_paid: Optional[float] = None
    incurred_not_reported: Optional[float] = None
    opening_members: Optional[int] = None
    closing_members: Optional[int] = None
    diagnosis_breakdown: Optional[List[Dict[str, Any]]] = None
    provider_breakdown: Optional[List[Dict[str, Any]]] = None
    claims_by_type: Optional[List[Dict[str, Any]]] = None
    treatment_type_breakdown: Optional[List[Dict[str, Any]]] = None
    claims_by_member_type_value: Optional[List[Dict[str, Any]]] = None
    claims_by_member_type_count: Optional[List[Dict[str, Any]]] = None
    monthly_paid: Optional[List[Dict[str, Any]]] = None
    created_at: datetime


class ClaimsProjectionOut(BaseModel):
    avg_month: float
    annualized: float
    with_ibnr: float
    opening_members: int
    closing_members: int
    avg_report_members: float
    burning_cost_per_member: float
    projected_current_group: float
    trended: float
    credible: float
    final_projected_claims: float
    assumptions_used: Dict[str, float]
    months_used: List[str]


class DiagnosisExposureRow(BaseModel):
    label: str
    value: float
    count: int
    ip_value: float
    ip_count: int
    avg_per_claim: float
    ip_avg_per_claim: float
    classification: str
    high_exposure: bool
    note: str
    flags: List[str]


class ScorecardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    weight_set_id: int
    demographic_risk: float
    claims_experience_risk: float
    benefit_richness_risk: float
    industry_risk: float
    credibility_factor: float
    composite_score: float
    risk_tier: str
    suggested_loading_pct: float
    details: Dict[str, Any]
    created_at: datetime


class ScoreRequest(BaseModel):
    estimated_annual_premium: Optional[float] = None


class OutcomeCreate(BaseModel):
    bound: bool
    final_premium: Optional[float] = None
    actual_loss_ratio: Optional[float] = None


class OutcomeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    bound: bool
    final_premium: Optional[float] = None
    actual_loss_ratio: Optional[float] = None
    profitable: Optional[bool] = None
    recorded_at: datetime


class WeightSetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: int
    w_demographic: float
    w_claims_experience: float
    w_benefit_richness: float
    w_industry: float
    zone_1_asia_multiplier: float
    zone_2_middle_east_multiplier: float
    zone_3_europe_americas_multiplier: float
    zone_4_other_multiplier: float
    zone_1_asia_maternity_multiplier: float
    zone_2_middle_east_maternity_multiplier: float
    zone_3_europe_americas_maternity_multiplier: float
    zone_1_asia_network_multiplier: float
    zone_2_middle_east_network_multiplier: float
    zone_3_europe_americas_network_multiplier: float
    overage_age_threshold: int
    overage_loading_cap: float
    is_active: bool
    trained_sample_size: int
    training_metrics: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None
    created_at: datetime


class WeightSetUpdate(BaseModel):
    """Manual adjustment of the active weight set - e.g. after reviewing
    portfolio outcomes and deciding a factor should move without waiting
    for (or instead of) the automatic recalibration loop. Any field left
    unset carries the current active value forward unchanged. Always
    creates a new version (never edits in place) so the adjustment history
    stays auditable the same way automatic recalibration already is.
    """

    w_demographic: Optional[float] = None
    w_claims_experience: Optional[float] = None
    w_benefit_richness: Optional[float] = None
    w_industry: Optional[float] = None
    zone_1_asia_multiplier: Optional[float] = None
    zone_2_middle_east_multiplier: Optional[float] = None
    zone_3_europe_americas_multiplier: Optional[float] = None
    zone_4_other_multiplier: Optional[float] = None
    zone_1_asia_maternity_multiplier: Optional[float] = None
    zone_2_middle_east_maternity_multiplier: Optional[float] = None
    zone_3_europe_americas_maternity_multiplier: Optional[float] = None
    zone_1_asia_network_multiplier: Optional[float] = None
    zone_2_middle_east_network_multiplier: Optional[float] = None
    zone_3_europe_americas_network_multiplier: Optional[float] = None
    overage_age_threshold: Optional[int] = None
    overage_loading_cap: Optional[float] = None
    notes: Optional[str] = None


class InsurerTierPreferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    insurer_name: str
    suggested_product: str
    updated_at: datetime


class InsurerTierPreferenceUpsert(BaseModel):
    insurer_name: str
    suggested_product: str


class RecalibrationResult(BaseModel):
    recalibrated: bool
    reason: Optional[str] = None
    new_weight_set: Optional[WeightSetOut] = None
    metrics: Optional[Dict[str, Any]] = None


class ReferenceBenefitPlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    insurer_name: str
    plan_label: str
    source_filename: Optional[str] = None
    benefit_rows: List[Dict[str, Any]]
    created_at: datetime


class ReferenceBenefitPlanSummary(BaseModel):
    """Lightweight listing row - omits `benefit_rows` since a library
    listing only needs to let the user pick plans by name, not show every
    row up front.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    insurer_name: str
    plan_label: str
    source_filename: Optional[str] = None
    row_count: int
    created_at: datetime


class PlanDetailsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    broker_name: Optional[str] = None
    industry: Optional[str] = None
    existing_insurer: Optional[str] = None
    years_with_existing_insurer: Optional[int] = None
    target_premium: Optional[float] = None
    claims_available: Optional[bool] = None
    renewal_date: Optional[date] = None
    region: Optional[str] = None
    updated_fields: List[str]


class RateCardUploadOut(BaseModel):
    rows_ingested: int
    products: List[str]
    regions: List[str]
    networks: List[str]


class BenefitVariantRateUploadOut(BaseModel):
    rows_ingested: int
    variant_names: List[str]


class NetworkOptionOut(BaseModel):
    network: str
    tpa: str


class RateCardOptionsOut(BaseModel):
    products: List[str]
    regions: List[str]
    # Product -> the networks/TPAs a case on that product can use.
    product_networks: Dict[str, List[NetworkOptionOut]]


class VariantOptionOut(BaseModel):
    option_value: str
    direction: str
    impact_type: str
    impact_value: float


class CategoryRatingInput(BaseModel):
    category: str
    product: str
    network: str
    tpa: str
    commission_pct: Optional[float] = None
    variant_selections: Dict[str, str] = {}


class NewBusinessQuoteRequest(BaseModel):
    categories: List[CategoryRatingInput]


class NewBusinessQuoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    categories: List[Dict[str, Any]]
    case_gross_annual_premium: float
    result: Dict[str, Any]
    opportunity_assessment: Optional[Dict[str, Any]] = None
    created_at: datetime


class PortfolioUploadOut(BaseModel):
    rows_ingested: int


class PortfolioSummaryRow(BaseModel):
    model_config = ConfigDict(extra="allow")  # the group-by key itself is a dynamic field name

    member_count: int
    priced_member_count: int
    standard_premium: float
    actual_premium: float
    actual_claims: float
    ibnr: float = 0.0  # incurred-but-not-reported reserve estimate (see ibnr_for_member)
    loss_ratio_vs_standard: Optional[float] = None
    loss_ratio_vs_actual: Optional[float] = None
    loss_ratio_incl_ibnr: Optional[float] = None  # (Paid + Outstanding + IBNR) / Earned Premium
    actual_vs_standard_pct: Optional[float] = None
    earned_member_years: float = 0.0
    burning_cost: Optional[float] = None  # actual claims per earned member-year (AED per member per annum)
    claim_count: int = 0
    claim_frequency: Optional[float] = None  # claims per earned member-year
    claim_severity: Optional[float] = None  # average AED cost per claim


class PortfolioSummaryOut(BaseModel):
    group_by: str
    rows: List[PortfolioSummaryRow]
    total_members: int
    out_of_scope_member_count: int
    unmapped_product_member_count: int
    unmapped_network_member_count: int


class PortfolioDataAsOfIn(BaseModel):
    data_as_of_date: date


class PortfolioDataAsOfOut(BaseModel):
    data_as_of_date: Optional[date] = None


class ChatQuestionIn(BaseModel):
    question: str


class ChatAnswerOut(BaseModel):
    answer: str
