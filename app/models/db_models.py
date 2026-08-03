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
    # Fallback reference date for age-band calculations when the uploaded
    # census file has no per-row effective-date column of its own - see
    # app/ingestion/census.py's default_policy_start_date param.
    policy_start_date = Column(Date, nullable=True)
    submitted_at = Column(DateTime, default=datetime.datetime.utcnow)
    status = Column(SAEnum(CaseStatus), default=CaseStatus.SUBMITTED)

    # "new" (a fresh quote, no prior insurer relationship to price off of) or
    # "existing" (a renewal - the group's own claims ledger and current
    # premium are available, enabling the renewal-increase calculation in
    # app/scoring/rules/renewal_rating.py rather than just a burning-cost
    # quote for a new group).
    business_type = Column(String, nullable=True)
    # The expiring/current year's premium for an existing-business renewal -
    # distinct from target_premium (a broker's target for a NEW quote).
    current_annual_premium = Column(Float, nullable=True)

    census_records = relationship("CensusRecord", back_populates="case", cascade="all, delete-orphan")
    benefit_plans = relationship("BenefitPlan", back_populates="case", cascade="all, delete-orphan")
    claims_records = relationship("ClaimsRecord", back_populates="case", cascade="all, delete-orphan")
    claims_reports = relationship("ClaimsReport", back_populates="case", cascade="all, delete-orphan")
    claims_ledger_entries = relationship("ClaimsLedgerEntry", back_populates="case", cascade="all, delete-orphan")
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
    # The scheme's own fixed policy term (same for every row - e.g. a
    # broker's "Eff Date"/"Exp Date" columns), vs. this individual
    # member's own endorsement dates onto the scheme, which can fall
    # short of it if they joined late or left early - see
    # app/scoring/rules/exposed_risk_population.py.
    policy_start_date = Column(Date, nullable=True)
    policy_end_date = Column(Date, nullable=True)
    member_start_date = Column(Date, nullable=True)
    member_end_date = Column(Date, nullable=True)

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

    # "existing" (the incumbent plan, uploaded via /benefits) or "quoted" (a
    # new insurer's proposal for the same case, uploaded via /quote) - lets
    # both live on the same case at once so they can be compared, without
    # either upload deleting the other's rows. See app/ingestion/quote_pdf.py
    # and app/scoring/rules/benefits_comparison.py.
    role = Column(String, nullable=False, default="existing")
    category = Column(String, nullable=True)  # broker/insurer category label, e.g. "A" / "B"
    gross_premium = Column(Float, nullable=True)

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


class ClaimsLedgerEntry(Base):
    """One row per per-claim-line item from an insurer/TPA's raw claims
    ledger export (e.g. the "ServicePlan" format: PATIENT_ID, CLAIM_ID,
    DATE_OF_TREATMENT, DIAGNOSIS_CODE, Final Amount in AED, etc.) - only
    available for existing-business renewals, where the group's own claims
    history (not a third-party's, rescaled) directly drives the renewal
    increase calculation. Distinct from both ClaimsRecord (a simpler
    generic spreadsheet) and ClaimsReport (a pre-aggregated summary report
    like the DHA Mandated Format) - this is raw, line-level detail, letting
    top-patient and top-diagnosis breakdowns be computed directly rather
    than trusting a report's own pre-computed top-10.
    """
    __tablename__ = "claims_ledger_entries"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    patient_id = Column(String, nullable=True)
    claim_id = Column(String, nullable=True)
    claim_status = Column(String, nullable=True)  # "Paid Claims" / "Outstanding Claims"
    policy_start_date = Column(Date, nullable=True)
    policy_end_date = Column(Date, nullable=True)
    # The scheme's own fixed term above, vs. this individual member's own
    # enrollment dates - can fall short of it if they joined late or left
    # early (see top_patients_by_final_amount's member_status).
    member_start_date = Column(Date, nullable=True)
    member_end_date = Column(Date, nullable=True)
    date_of_treatment = Column(Date, nullable=True)
    relation = Column(String, nullable=True)
    ip_op_maternity = Column(String, nullable=True)
    medical_category = Column(String, nullable=True)
    provider_name = Column(String, nullable=True)
    diagnosis_code = Column(String, nullable=True)
    diagnosis_description = Column(String, nullable=True)
    claimed_amount = Column(Float, nullable=True)
    final_amount = Column(Float, nullable=True)

    case = relationship("Case", back_populates="claims_ledger_entries")


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
    treatment_type_breakdown = Column(JSON, nullable=True)
    claims_by_member_type_value = Column(JSON, nullable=True)
    claims_by_member_type_count = Column(JSON, nullable=True)
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

    # Learnable interaction effects: how much more/less a zone's maternity
    # loading and a zone's exposure to expensive/broad network tiers should
    # count, beyond the flat zone multipliers above. Same recalibration
    # pattern - start neutral (1.0), tuned once enough outcomes accumulate.
    zone_1_asia_maternity_multiplier = Column(Float, default=1.0)
    zone_2_middle_east_maternity_multiplier = Column(Float, default=1.0)
    zone_3_europe_americas_maternity_multiplier = Column(Float, default=1.0)

    zone_1_asia_network_multiplier = Column(Float, default=1.0)
    zone_2_middle_east_network_multiplier = Column(Float, default=1.0)
    zone_3_europe_americas_network_multiplier = Column(Float, default=1.0)

    # A member older than this age carries an extra loading on top of their
    # own age-band multiplier, scaled by what fraction of the census is over
    # it - a distinct signal from the age bands themselves (see
    # app/scoring/rules/demographic.py), tunable without changing the bands.
    overage_age_threshold = Column(Integer, default=50)
    overage_loading_cap = Column(Float, default=0.15)

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


class ReferenceBenefitPlan(Base):
    """One insurer/tier's table of benefits, uploaded once into a shared
    reference library rather than attached to any particular case - powers
    the detailed international (and later local) insurer comparison, where
    a broker picks any combination of previously-uploaded plans to view
    side by side. Distinct from `BenefitPlan`, which is always a specific
    case's existing or quoted plan.

    `benefit_rows` keeps every row verbatim, as extracted, rather than
    forcing each insurer's own wording onto the standard 11-field summary -
    a real comparison across e.g. Cigna/Bupa/Allianz/MSH needs each
    insurer's actual benefit lines, including ones the others don't have.
    """

    __tablename__ = "reference_benefit_plans"

    id = Column(Integer, primary_key=True)
    insurer_name = Column(String, nullable=False)
    plan_label = Column(String, nullable=False)
    source_filename = Column(String, nullable=True)
    # [{"section": "In-patient", "label": "Hospital accommodation", "value": "Full Refund"}, ...]
    benefit_rows = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class RateCard(Base):
    """HealthCross's own New Business manual/book rate card - one row per
    Product x Region x Network x age band, giving the annual base price per
    member before any benefit-variant loading (see BenefitVariantRate) or
    commission/fee gross-up (see app/scoring/rules/new_business_rating.py).

    Uploaded wholesale from an internal rate-card spreadsheet (see
    app/ingestion/rate_cards.py) - a fresh upload replaces the whole table
    rather than merging, since this is a single point-in-time rate sheet,
    not a history to accumulate.

    male_price/female_price carry a different meaning depending on region:
    for Dubai/Northern Emirates they're the literal Male/Female price; for
    Abu Dhabi the same two columns instead price Employee/Dependant (Abu
    Dhabi's own regulated scheme rates by membership role, not gender) -
    this is exactly how the source spreadsheet itself reuses the columns,
    so it's kept as-is rather than invented as two separately-named fields.
    """

    __tablename__ = "rate_cards"

    id = Column(Integer, primary_key=True)
    product = Column(String, nullable=False)  # Platinum / Gold / Silver / Bronze
    region = Column(String, nullable=False)  # Dubai / Abu Dhabi / Northern Emirates
    network = Column(String, nullable=False)
    tpa = Column(String, nullable=False)  # MSH MENA / NAS Neuron
    from_age = Column(Integer, nullable=False)
    to_age = Column(Integer, nullable=False)
    male_price = Column(Float, nullable=False)  # Employee price, for Abu Dhabi
    female_price = Column(Float, nullable=False)  # Dependant price, for Abu Dhabi
    # Flat AED maternity surcharge for a married female aged 18-50 - None
    # where the source sheet says "Not Applicable" (outside that age band),
    # 0 where it says so explicitly (still "applicable" as a concept, just
    # priced at nil - e.g. Dubai/Northern Emirates today).
    married_female_surcharge = Column(Float, nullable=True)
    zone = Column(String, nullable=True)
    source_filename = Column(String, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)


class BenefitVariantRate(Base):
    """One selectable option for one benefit variant (Annual Limit,
    Deductible, OP Copay, Pharmacy Copay/Limit, Dental Copay/Limit, Optical
    Copay/Limit, Maternity Limit, Alternative Medicine, Pre-existing &
    Chronic Conditions), scoped by Region x TPA x Network - a network's
    variant pricing is the same regardless of which Product tier is using
    it, so this table has no Product column of its own (see RateCard for
    the Product-level base price).

    Each (region, tpa, network, variant_name) group has exactly one "Base"
    row (the option already included in the product's base rate, zero
    impact) plus Upgrade/Downgrade rows, each carrying a signed impact on
    top of that member's own base rate - see
    app/scoring/rules/new_business_rating.py for how impact_type/direction
    combine into an actual AED adjustment.

    Uploaded wholesale the same way as RateCard - a fresh upload replaces
    the whole table.
    """

    __tablename__ = "benefit_variant_rates"

    id = Column(Integer, primary_key=True)
    variant_name = Column(String, nullable=False)
    option_value = Column(String, nullable=False)
    direction = Column(String, nullable=False)  # Base / Upgrade / Downgrade
    impact_type = Column(String, nullable=False)  # Percent / Fixed / Currency / Text
    impact_value = Column(Float, nullable=False)
    is_default = Column(Boolean, default=False)
    region = Column(String, nullable=False)
    tpa = Column(String, nullable=False)
    network = Column(String, nullable=False)
    zone = Column(String, nullable=True)
    source_filename = Column(String, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)


class NewBusinessQuote(Base):
    """One computed New Business rate-card quote for a case - see
    app/scoring/rules/new_business_rating.py. `categories` is the broker's
    own input (Product/Network/TPA + variant selections per category)
    verbatim, and `result` is the full price_case() output, so a past quote
    can be displayed or re-derived without needing to replay the broker's
    choices against whatever the rate card looks like today.
    """

    __tablename__ = "new_business_quotes"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    categories = Column(JSON, nullable=False)
    case_gross_annual_premium = Column(Float, nullable=False)
    result = Column(JSON, nullable=False)
    opportunity_assessment = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    case = relationship("Case")


class InsurerTierPreference(Base):
    """Which New Business Product tier an existing insurer's book typically
    suggests as a starting point for the tier-ladder comparison (see
    app/reference/product_tiers.py) - e.g. a group currently with Allianz/
    Cigna Global Care/BUPA is usually worth quoting from Platinum first.
    Admin-editable (not a hardcoded table) so underwriting can retune which
    insurer maps to which tier as portfolio experience accumulates, without
    a code change.
    """

    __tablename__ = "insurer_tier_preferences"

    id = Column(Integer, primary_key=True)
    insurer_name = Column(String, nullable=False, unique=True)
    suggested_product = Column(String, nullable=False)  # Platinum / Gold / Silver / Bronze
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
