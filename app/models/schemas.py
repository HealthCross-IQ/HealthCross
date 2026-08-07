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
    current_annual_premium: Optional[float] = None


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
    current_annual_premium: Optional[float] = None


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
    is_active: bool
    trained_sample_size: int
    training_metrics: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None
    created_at: datetime


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


class FeeRateCardCreate(BaseModel):
    channel: str  # "broker" / "direct"
    tier_band: str  # "bronze_silver" / "gold_platinum"
    fee_pct: float
    effective_from: date
    notes: Optional[str] = None


class FeeRateCardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel: str
    tier_band: str
    fee_pct: float
    effective_from: date
    is_active: bool
    notes: Optional[str] = None
    created_at: datetime


class PaymentTrackerEntryCreate(BaseModel):
    invoice_mode: Optional[str] = None
    source_name: Optional[str] = None
    channel: str  # "broker" / "direct" / "group"
    division: Optional[str] = None
    client_code: Optional[str] = None
    main_policy_holder: Optional[str] = None
    sub_group_name: Optional[str] = None
    policy_no: Optional[str] = None
    policy_period_from: Optional[date] = None
    policy_period_to: Optional[date] = None
    endorsement_no: Optional[str] = None
    endorsement_type: Optional[str] = None
    doc_date: Optional[date] = None
    due_date: Optional[date] = None
    doc_no: Optional[str] = None
    doc_code: Optional[str] = None
    client_doc_no: Optional[str] = None
    invoice_amount: Optional[float] = None
    premium_excl_vat: float
    basmah: Optional[float] = None
    icp: Optional[float] = None
    client_vat: Optional[float] = None
    client_payment_status: Optional[str] = None
    healthcross_doc: Optional[str] = None
    client_premium_amount_excl_tax: Optional[float] = None
    product: str
    # Set to force a negotiated/Group case-to-case rate instead of looking
    # one up on the FeeRateCard - mirrors the source tracker's "manual calc"
    # convention. Also required when `product` mixes tiers (e.g.
    # "Gold/Bronze") since the rate card can't band that automatically.
    manual_fee_pct: Optional[float] = None
    invoice_type: Optional[str] = None
    invoice_status: Optional[str] = None
    invoice_raised_period: Optional[str] = None
    hc_payment_status: Optional[str] = None
    payment_receive_date: Optional[date] = None
    notes: Optional[str] = None


class PaymentTrackerEntryUpdate(BaseModel):
    client_payment_status: Optional[str] = None
    healthcross_doc: Optional[str] = None
    invoice_status: Optional[str] = None
    invoice_raised_period: Optional[str] = None
    hc_payment_status: Optional[str] = None
    payment_receive_date: Optional[date] = None
    payment_receive_note: Optional[str] = None
    notes: Optional[str] = None


class PaymentTrackerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    invoice_mode: Optional[str] = None
    source_name: Optional[str] = None
    channel: str
    division: Optional[str] = None
    client_code: Optional[str] = None
    main_policy_holder: Optional[str] = None
    sub_group_name: Optional[str] = None
    policy_no: Optional[str] = None
    policy_period_from: Optional[date] = None
    policy_period_to: Optional[date] = None
    endorsement_no: Optional[str] = None
    endorsement_type: Optional[str] = None
    doc_date: Optional[date] = None
    due_date: Optional[date] = None
    doc_no: Optional[str] = None
    doc_no_raw: Optional[str] = None
    doc_code: Optional[str] = None
    client_doc_no: Optional[str] = None
    invoice_amount: Optional[float] = None
    premium_excl_vat: Optional[float] = None
    basmah: Optional[float] = None
    icp: Optional[float] = None
    client_vat: Optional[float] = None
    client_payment_status: Optional[str] = None
    healthcross_doc: Optional[str] = None
    client_premium_amount_excl_tax: Optional[float] = None
    product: Optional[str] = None
    is_manual_fee: bool
    hc_fee_pct: Optional[float] = None
    hc_fees: Optional[float] = None
    vat_pct: Optional[float] = None
    vat_amount: Optional[float] = None
    total_value: Optional[float] = None
    invoice_type: Optional[str] = None
    invoice_status: Optional[str] = None
    invoice_raised_period: Optional[str] = None
    hc_payment_status: Optional[str] = None
    payment_receive_date: Optional[date] = None
    payment_receive_note: Optional[str] = None
    source_batch: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class QicSoaLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doc_no: Optional[str] = None
    doc_no_raw: Optional[str] = None
    tran_code: Optional[str] = None
    doc_date: Optional[date] = None
    tran_type: Optional[str] = None
    doc_due_date: Optional[date] = None
    lob_code: Optional[str] = None
    policy_no: Optional[str] = None
    insured_name: Optional[str] = None
    insured_code: Optional[str] = None
    currency: Optional[str] = None
    doc_desc: Optional[str] = None
    debit_amount: float
    credit_amount: float
    dr_cr: Optional[str] = None
    sequence_no: Optional[int] = None
    policy_from_date: Optional[date] = None
    policy_to_date: Optional[date] = None
    cust_code: Optional[str] = None
    cust_name: Optional[str] = None
    endorsement_no: Optional[str] = None
    pol_comms_doc: Optional[str] = None
    branch: Optional[str] = None
    gross_amount: Optional[float] = None
    age_band: Optional[str] = None
    prod_code: Optional[str] = None
    cust_group_code: Optional[str] = None
    cust_group_name: Optional[str] = None
    broker_name: Optional[str] = None
    control_account: Optional[str] = None
    cal_year: Optional[str] = None
    doc_created_by: Optional[str] = None
    installment_number: Optional[str] = None
    endorsement_type: Optional[str] = None
    statement_period: Optional[str] = None
    imported_at: datetime


class BankTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_number: Optional[str] = None
    txn_date: Optional[date] = None
    value_date: Optional[date] = None
    reference_number: Optional[str] = None
    description: Optional[str] = None
    credit_amount: float
    debit_amount: float
    balance: Optional[float] = None
    currency: str
    statement_period: Optional[str] = None
    imported_at: datetime


class EmployeeCreate(BaseModel):
    full_name: str
    role_title: Optional[str] = None
    monthly_salary: float
    currency: str = "AED"
    start_date: Optional[date] = None
    notes: Optional[str] = None


class EmployeeUpdate(BaseModel):
    full_name: Optional[str] = None
    role_title: Optional[str] = None
    monthly_salary: Optional[float] = None
    currency: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    role_title: Optional[str] = None
    monthly_salary: float
    currency: str
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    is_active: bool
    notes: Optional[str] = None
    created_at: datetime
    # End-of-service gratuity, computed fresh on every read (see
    # app/finance/end_of_service.py) - never stored, since it changes daily
    # for an active employee. years_of_service/gratuity are None only when
    # start_date isn't set yet.
    years_of_service: Optional[float] = None
    end_of_service_gratuity: Optional[float] = None


class RecurringExpenseCreate(BaseModel):
    name: str
    category: str
    default_amount: Optional[float] = None
    currency: str = "AED"
    expense_type: str  # "fixed" / "variable"
    notes: Optional[str] = None


class RecurringExpenseUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    default_amount: Optional[float] = None
    currency: Optional[str] = None
    expense_type: Optional[str] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class RecurringExpenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    category: str
    default_amount: Optional[float] = None
    currency: str
    expense_type: str
    is_active: bool
    notes: Optional[str] = None
    created_at: datetime


class ExpenseEntryCreate(BaseModel):
    period: date  # any day in the month is accepted; normalized to the 1st
    category: str
    expense_type: str  # "fixed" / "variable"
    description: Optional[str] = None
    amount: float
    currency: str = "AED"
    employee_id: Optional[int] = None
    recurring_expense_id: Optional[int] = None
    payment_date: Optional[date] = None
    notes: Optional[str] = None


class ExpenseEntryUpdate(BaseModel):
    category: Optional[str] = None
    expense_type: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[float] = None
    payment_date: Optional[date] = None
    notes: Optional[str] = None


class ExpenseEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    period: date
    category: str
    expense_type: str
    description: Optional[str] = None
    amount: float
    currency: str
    employee_id: Optional[int] = None
    recurring_expense_id: Optional[int] = None
    payment_date: Optional[date] = None
    source: str
    notes: Optional[str] = None
    created_at: datetime


class PolicyReconciliationRow(BaseModel):
    policy_no: str
    client_name: Optional[str] = None
    doc_nos: List[str] = []
    tracker_outstanding_amount: Optional[float] = None
    tracker_outstanding_count: int = 0
    client_soa_amount: Optional[float] = None
    client_soa_count: int = 0
    variance: Optional[float] = None
    # "matched" / "amount_mismatch" / "missing_in_client_soa" /
    # "settled_in_tracker_but_open_in_client_soa" / "missing_in_tracker"
    status: str


class TrackerClientSoaReconciliationOut(BaseModel):
    statement_period: Optional[str] = None
    total_policies_outstanding_in_tracker: int
    total_policies_in_client_soa: int
    matched_count: int
    mismatched_count: int
    missing_in_client_soa_count: int
    settled_in_tracker_but_open_in_client_soa_count: int
    missing_in_tracker_count: int
    rows: List[PolicyReconciliationRow]


class SoaPeriodComparisonRow(BaseModel):
    doc_no: str
    period_a_amount: Optional[float] = None
    period_b_amount: Optional[float] = None
    variance: Optional[float] = None
    status: str  # "unchanged" / "changed" / "only_in_a" / "only_in_b"


class SoaPeriodComparisonOut(BaseModel):
    period_a: str
    period_b: str
    changed_count: int
    only_in_a_count: int
    only_in_b_count: int
    rows: List[SoaPeriodComparisonRow]


class BankReconciliationRow(BaseModel):
    tracker_entry_id: Optional[int] = None
    doc_no: Optional[str] = None
    client_name: Optional[str] = None
    total_value: Optional[float] = None
    payment_receive_date: Optional[date] = None
    bank_transaction_id: Optional[int] = None
    bank_credit_amount: Optional[float] = None
    bank_txn_date: Optional[date] = None
    status: str  # "matched" / "no_bank_match" / "unmatched_bank_credit"


class BankReconciliationOut(BaseModel):
    matched_count: int
    unmatched_tracker_count: int
    unmatched_bank_count: int
    rows: List[BankReconciliationRow]


class CashFlowMonth(BaseModel):
    period: str  # "2026-07"
    inflow: float
    outflow: float
    net: float
    cumulative: float


class CashFlowOut(BaseModel):
    year: int
    months: List[CashFlowMonth]
    total_inflow: float
    total_outflow: float
    total_net: float


class ExpenseForecastMonth(BaseModel):
    period: str
    fixed: float
    variable: float
    total: float
    is_actual: bool  # True if based on real ExpenseEntry rows, False if forecast


class ExpenseForecastOut(BaseModel):
    year: int
    months: List[ExpenseForecastMonth]
    total_actual: float
    total_forecast: float
    assumptions: Dict[str, Any]


class FinanceSummaryOut(BaseModel):
    total_hc_fees_invoiced: float
    total_hc_fees_received: float
    total_outstanding: float
    ytd_expenses: float
    ytd_net: float
    as_of: datetime


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
