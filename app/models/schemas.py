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
