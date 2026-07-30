import datetime
import enum

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base


class CaseStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    SCORED = "scored"
    BOUND = "bound"
    DECLINED = "declined"
    LAPSED = "lapsed"


class Case(Base):
    __tablename__ = "cases"

    id = Column(Integer, primary_key=True)
    broker_name = Column(String, nullable=False)
    company_name = Column(String, nullable=False)
    industry = Column(String, nullable=False)
    region = Column(String, nullable=True)
    employee_count_declared = Column(Integer, nullable=True)
    existing_insurer = Column(String, nullable=True)
    years_with_existing_insurer = Column(Integer, nullable=True)
    target_premium = Column(Float, nullable=True)
    claims_available = Column(Boolean, nullable=True)
    renewal_date = Column(Date, nullable=True)
    submitted_at = Column(DateTime, default=datetime.datetime.utcnow)
    status = Column(SAEnum(CaseStatus), default=CaseStatus.SUBMITTED)

    census_records = relationship("CensusRecord", back_populates="case", cascade="all, delete-orphan")
    benefit_plans = relationship("BenefitPlan", back_populates="case", cascade="all, delete-orphan")
    claims_records = relationship("ClaimsRecord", back_populates="case", cascade="all, delete-orphan")
    claims_reports = relationship("ClaimsReport", back_populates="case", cascade="all, delete-orphan")
    scorecards = relationship("Scorecard", back_populates="case", cascade="all, delete-orphan")
    outcome = relationship("Outcome", back_populates="case", uselist=False, cascade="all, delete-orphan")


class CensusRecord(Base):
    __tablename__ = "census_records"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    employee_ref = Column(String, nullable=True)
    category = Column(String, nullable=True)  # broker plan tier, e.g. A/B/C/D
    age = Column(Integer, nullable=True)
    gender = Column(String, nullable=True)  # "M" / "F"
    marital_status = Column(String, nullable=True)  # "married" / "single"
    relation = Column(String, nullable=True)  # "employee" / "spouse" / "child" / "other"
    emirates = Column(String, nullable=True)
    salary_band = Column(String, nullable=True)
    nationality = Column(String, nullable=True)
    nationality_zone = Column(String, nullable=True)
    dependents_count = Column(Integer, default=0)
    join_date = Column(Date, nullable=True)

    case = relationship("Case", back_populates="census_records")


class BenefitPlan(Base):
    __tablename__ = "benefit_plans"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    plan_name = Column(String, nullable=True)
    annual_limit = Column(Float, nullable=True)
    room_type = Column(String, nullable=True)  # ward / semi_private / private
    deductible = Column(Float, default=0)
    co_insurance_pct = Column(Float, default=0)
    network_type = Column(String, nullable=True)  # in_country / regional / worldwide
    maternity_covered = Column(Boolean, default=False)
    maternity_limit = Column(Float, nullable=True)
    dental_covered = Column(Boolean, default=False)
    optical_covered = Column(Boolean, default=False)
    chronic_covered = Column(Boolean, default=True)
    pre_existing_covered = Column(Boolean, default=False)
    member_count = Column(Integer, nullable=True)

    # Populated when parsed from an insurer's table-of-benefits PDF rather
    # than a generic spreadsheet - see app/ingestion/benefits_pdf.py and
    # app/scoring/rules/benefits_summary.py for the fixed 10-field format.
    source_format = Column(String, nullable=True)  # "xlsx" / "csv" / "pdf" / "pdf-ocr"
    standard_summary = Column(JSON, nullable=True)

    # Populated only for scanned (image-only) PDFs parsed via OCR fallback
    # (app/ingestion/benefits_ocr.py) - the full per-page OCR text, kept so
    # a human can search/verify values the low-confidence OCR extraction
    # couldn't map cleanly.
    raw_ocr_text = Column(Text, nullable=True)

    case = relationship("Case", back_populates="benefit_plans")


class ClaimsRecord(Base):
    __tablename__ = "claims_records"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    member_ref = Column(String, nullable=True)
    claim_date = Column(Date, nullable=True)
    service_type = Column(String, nullable=True)
    diagnosis_category = Column(String, nullable=True)
    amount_billed = Column(Float, nullable=True)
    amount_paid = Column(Float, nullable=True)
    policy_year = Column(Integer, nullable=True)

    case = relationship("Case", back_populates="claims_records")


class ClaimsReport(Base):
    """An aggregate claims-experience report (e.g. the DHA Mandated Format
    claims report), distinct from ClaimsRecord's per-claim line items.

    Feeds app/scoring/rules/claims_projection.py (burning-cost projection)
    and app/reference/diagnosis_classification.py (exposure flagging).
    """

    __tablename__ = "claims_reports"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    policy_number = Column(String, nullable=True)
    policy_effective_date = Column(Date, nullable=True)
    policy_expiry_date = Column(Date, nullable=True)
    report_period_start = Column(Date, nullable=True)
    report_period_end = Column(Date, nullable=True)
    report_production_date = Column(Date, nullable=True)
    total_paid = Column(Float, nullable=True)
    incurred_not_reported = Column(Float, nullable=True)
    opening_members = Column(Integer, nullable=True)
    closing_members = Column(Integer, nullable=True)
    diagnosis_breakdown = Column(JSON, nullable=True)
    provider_breakdown = Column(JSON, nullable=True)
    claims_by_type = Column(JSON, nullable=True)
    monthly_paid = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    case = relationship("Case", back_populates="claims_reports")


class Scorecard(Base):
    __tablename__ = "scorecards"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    weight_set_id = Column(Integer, ForeignKey("scoring_weight_sets.id"), nullable=False)
    demographic_risk = Column(Float)
    claims_experience_risk = Column(Float)
    benefit_richness_risk = Column(Float)
    industry_risk = Column(Float)
    credibility_factor = Column(Float)
    composite_score = Column(Float)
    risk_tier = Column(String)
    suggested_loading_pct = Column(Float)
    details = Column(JSON)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    case = relationship("Case", back_populates="scorecards")
    weight_set = relationship("ScoringWeightSet")


class ScoringWeightSet(Base):
    __tablename__ = "scoring_weight_sets"

    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False)

    w_demographic = Column(Float, default=0.30)
    w_claims_experience = Column(Float, default=0.35)
    w_benefit_richness = Column(Float, default=0.20)
    w_industry = Column(Float, default=0.15)

    # Learnable nationality-zone risk multipliers. Start neutral; recalibrated
    # from real case outcomes as the feedback loop accumulates data.
    zone_1_asia_multiplier = Column(Float, default=1.0)
    zone_2_middle_east_multiplier = Column(Float, default=1.0)
    zone_3_europe_americas_multiplier = Column(Float, default=1.0)
    zone_4_other_multiplier = Column(Float, default=1.0)

    is_active = Column(Boolean, default=False)
    trained_sample_size = Column(Integer, default=0)
    training_metrics = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class Outcome(Base):
    __tablename__ = "outcomes"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False, unique=True)
    scorecard_id = Column(Integer, ForeignKey("scorecards.id"), nullable=False)
    bound = Column(Boolean, nullable=False)
    final_premium = Column(Float, nullable=True)
    actual_loss_ratio = Column(Float, nullable=True)
    profitable = Column(Boolean, nullable=True)
    recorded_at = Column(DateTime, default=datetime.datetime.utcnow)

    case = relationship("Case", back_populates="outcome")
    scorecard = relationship("Scorecard")
