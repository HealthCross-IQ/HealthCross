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

    # Set when this case was opened straight off HealthCross's own book
    # rather than keyed in by hand - the PortfolioMember master client
    # (resolve_master_client) it was derived from. Makes the link both
    # ways: the Renewal Due List can tell which accounts already have a
    # case open, and a case opened this way can always be traced back to
    # the account whose membership seeded it. Null on any manually-created
    # case, which is every case that isn't on the book.
    portfolio_master_client = Column(String, nullable=True, index=True)

    # The renewal premium's own component breakdown - what the loading %
    # in app/scoring/rules/renewal_rating.py actually consists of, broken
    # into its real named pieces rather than one blended number. Nullable:
    # falls back to DEFAULT_TPA_FEE_PCT/DEFAULT_COMMISSION_PCT/
    # DEFAULT_HC_FEE_PCT/DEFAULT_QIC_FEE_PCT (which sum to
    # DEFAULT_LOADING_PCT) when unset, so existing cases don't need to be
    # touched. Risk Premium (the pure claims-funding cost) isn't stored
    # separately - it's always whatever's left over (see
    # premium_component_breakdown). qic_fee_pct is QIC's own margin on
    # top of funding claims, distinct from Risk Premium.
    tpa_fee_pct = Column(Float, nullable=True)
    commission_pct = Column(Float, nullable=True)
    hc_fee_pct = Column(Float, nullable=True)
    qic_fee_pct = Column(Float, nullable=True)

    census_records = relationship("CensusRecord", back_populates="case", cascade="all, delete-orphan")
    benefit_plans = relationship("BenefitPlan", back_populates="case", cascade="all, delete-orphan")
    claims_records = relationship("ClaimsRecord", back_populates="case", cascade="all, delete-orphan")
    claims_reports = relationship("ClaimsReport", back_populates="case", cascade="all, delete-orphan")
    claims_ledger_entries = relationship("ClaimsLedgerEntry", back_populates="case", cascade="all, delete-orphan")
    scorecards = relationship("Scorecard", back_populates="case", cascade="all, delete-orphan")
    outcome = relationship("Outcome", back_populates="case", uselist=False, cascade="all, delete-orphan")
    new_business_quotes = relationship("NewBusinessQuote", back_populates="case", cascade="all, delete-orphan")


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
    # This member's own current/expiring individual rate, and an optional
    # manual override for their renewal rate - see
    # app/api/routes_analysis.py's get_member_rates, which fills in the
    # renewal rate for any member without an override as
    # existing_annual_rate grossed up by the case's own renewal_increase_pct
    # (calculate_renewal_rating), not a separate stored value, so it always
    # stays in sync with the case-level renewal calculation.
    existing_annual_rate = Column(Float, nullable=True)
    new_annual_rate_override = Column(Float, nullable=True)

    case = relationship("Case", back_populates="census_records")


class CensusSnapshot(Base):
    """This case's own census relation-mix (Employees/Spouses/Children/...)
    member counts, captured right before a fresh census upload replaces
    CensusRecord - see app/api/routes_cases.py's upload_census. Lets
    Census Movement (Renewal Bench) compare the expiring census against
    the newly-uploaded renewal one, category by category, without
    needing to keep every old member-level row around just for this one
    comparison. One case has at most one snapshot - its own most recent
    "before" state - wholesale-replaced the same way CensusRecord itself
    is on every fresh upload.
    """

    __tablename__ = "census_snapshots"

    id = Column(Integer, primary_key=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    relation = Column(String, nullable=True)
    member_count = Column(Integer, nullable=False)
    captured_at = Column(DateTime, default=datetime.datetime.utcnow)


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

    # This category's own New Business rate-card Product/Network/TPA pick,
    # set on an EXISTING-role plan in the Benefits tab - the source of
    # truth for that category's New Business Quote (see
    # routes_new_business_rating._resolve_auto_quote_categories), so each
    # category (client) prices against its own real network rather than
    # one blanket case-wide default. Distinct from network_type above,
    # which is only the coarse in_country/regional/worldwide classification
    # used for benefits comparison, not a rate-card network identifier.
    nb_product = Column(String, nullable=True)
    nb_network = Column(String, nullable=True)
    nb_tpa = Column(String, nullable=True)

    # Set on an EXISTING-role plan to pin which QUOTED-role plan it should
    # be compared against in /benefits-comparison, overriding the automatic
    # category-letter/plan-name match - needed because an insurer's own
    # category naming is never guaranteed to line up with HealthCross's
    # quote categories (e.g. an incumbent's "Bronze/Silver/Gold" tiers vs a
    # quote's "CAT A/B/C"). Self-referential rather than a separate mapping
    # table since each existing plan only ever needs one counterpart.
    matched_quote_plan_id = Column(Integer, ForeignKey("benefit_plans.id"), nullable=True)

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
    # The specific treatment performed - the only field separating true
    # physiotherapy from alternative therapy inside "PARAMEDICAL".
    medical_act = Column(String, nullable=True)
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

    case = relationship("Case", back_populates="new_business_quotes")


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


class PortfolioMember(Base):
    """One member row from HealthCross's own book-wide membership export
    (see app/ingestion/portfolio_members.py) - powers Portfolio Analysis
    (app/scoring/rules/portfolio_analysis.py), which checks the already-
    booked $50M+ book's real experience against the New Business rate
    card, rather than a single case's own census. Wholesale-replaced on
    each fresh upload, like RateCard/BenefitVariantRate - this is a
    point-in-time snapshot ("as of" a given date), not a history to
    accumulate.

    `contract`/`master_contract` are this book's own sub-group/master-group
    names (e.g. "VL Consulting DWC-LLC" under master "VALUELABS - VL
    CONSULTING") - the join key into GroupProductMapping on an
    older-format export where `product_name`/`master_client_name` (see
    below) aren't populated.
    """

    __tablename__ = "portfolio_members"

    id = Column(Integer, primary_key=True)
    beneficiary_id = Column(String, nullable=False)  # joins to PortfolioClaimEntry.patient_id
    contract = Column(String, nullable=True)
    master_contract = Column(String, nullable=True)
    # Populated directly from the export's own "Master Client Name"/
    # PRODUCTNAME columns starting Aug 2026 - see app/ingestion/
    # portfolio_members.py. None on an older-format export, in which case
    # the separate GroupProductMapping/SubgroupMasterMapping uploads are
    # still the source of truth (see resolve_group_product/
    # resolve_master_client in app/scoring/rules/portfolio_analysis.py).
    master_client_name = Column(String, nullable=True)
    product_name = Column(String, nullable=True)
    policy_number = Column(String, nullable=True)
    msh_policy_number = Column(String, nullable=True)
    category = Column(String, nullable=True)  # this book's own raw category code, e.g. "QIC/HC/BR/FDG/DXB/A"
    network_type_raw = Column(String, nullable=True)  # see app/reference/network_type_mapping.py
    age = Column(Integer, nullable=True)
    gender = Column(String, nullable=True)
    marital_status = Column(String, nullable=True)
    relation = Column(String, nullable=True)
    nationality = Column(String, nullable=True)
    nationality_zone = Column(String, nullable=True)
    residence_emirate = Column(String, nullable=True)
    region = Column(String, nullable=True)  # Dubai / Abu Dhabi / Northern Emirates - see emirate_regions.py
    policy_start_date = Column(Date, nullable=True)
    policy_end_date = Column(Date, nullable=True)
    member_start_date = Column(Date, nullable=True)
    member_end_date = Column(Date, nullable=True)
    gross_premium = Column(Float, nullable=True)
    actual_gross_premium = Column(Float, nullable=True)
    net_premium = Column(Float, nullable=True)
    actual_net_premium = Column(Float, nullable=True)
    tpa_fee = Column(Float, nullable=True)
    source_filename = Column(String, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)


class PortfolioClaimEntry(Base):
    """One claim line from HealthCross's own book-wide claims export (see
    app/ingestion/portfolio_claims.py) - same per-claim-line shape as
    ClaimsLedgerEntry, but book-wide rather than scoped to one case, and
    carrying the group/policy identifiers a book-wide export needs
    (ClaimsLedgerEntry has no case-independent group identity to carry,
    since it's always attached to the one case it was uploaded for).
    Wholesale-replaced on each fresh upload, like PortfolioMember.
    """

    __tablename__ = "portfolio_claim_entries"

    id = Column(Integer, primary_key=True)
    patient_id = Column(String, nullable=True)  # joins to PortfolioMember.beneficiary_id
    claim_id = Column(String, nullable=True)
    claim_status = Column(String, nullable=True)
    group_name = Column(String, nullable=True)
    client_name = Column(String, nullable=True)
    msh_policy_number = Column(String, nullable=True)
    policy_start_date = Column(Date, nullable=True)
    policy_end_date = Column(Date, nullable=True)
    member_start_date = Column(Date, nullable=True)
    member_end_date = Column(Date, nullable=True)
    date_of_treatment = Column(Date, nullable=True)
    relation = Column(String, nullable=True)
    ip_op_maternity = Column(String, nullable=True)
    medical_category = Column(String, nullable=True)
    # The specific treatment performed - the only field separating true
    # physiotherapy from alternative therapy inside "PARAMEDICAL".
    medical_act = Column(String, nullable=True)
    provider_name = Column(String, nullable=True)
    diagnosis_code = Column(String, nullable=True)
    diagnosis_description = Column(String, nullable=True)
    claimed_amount = Column(Float, nullable=True)
    final_amount = Column(Float, nullable=True)
    source_filename = Column(String, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)


class GroupProductMapping(Base):
    """Which New Business Product (Platinum/Gold/Silver/Bronze) a real
    booked group is actually on - not captured on the membership export
    itself (PRODUCTNAME is blank in practice), so underwriting supplies it
    separately, keyed by this book's own contract/master-contract name.
    Wholesale-replaced on each fresh upload, like PortfolioMember.
    """

    __tablename__ = "group_product_mappings"

    id = Column(Integer, primary_key=True)
    group_name = Column(String, nullable=False)  # matches PortfolioMember.contract or .master_contract
    product = Column(String, nullable=False)  # Platinum / Gold / Silver / Bronze
    source_filename = Column(String, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)


class SubgroupMasterMapping(Base):
    """Which master policy/group a real subgroup belongs to - parsed from
    the dedicated Subgroup -> Master Group mapping sheet underwriting
    maintains separately (see app/ingestion/subgroup_mapping.py), since
    PortfolioMember's own MASTERCONTRACT column on the system export isn't
    a reliable source for this (observed in practice just duplicating the
    subgroup's own name). Wholesale-replaced on each fresh upload.
    """

    __tablename__ = "subgroup_master_mappings"

    id = Column(Integer, primary_key=True)
    subgroup_name = Column(String, nullable=False)  # matches PortfolioMember.contract
    master_name = Column(String, nullable=False)
    source_filename = Column(String, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)


class ClientMasterInfo(Base):
    """Per-master-client reference data underwriting maintains separately
    from the Membership/Claims exports - principally each client's own
    real OPEX/Loading % (commission + TPA + admin + HC/management fees,
    as a fraction of premium), used in place of the flat
    DEFAULT_EXPENSE_RATIO_PCT assumption for Combined Ratio wherever a
    client's own real figure is on file. A client's real loading can
    change from one renewal to the next, so the SAME master_client_name
    can appear on more than one row here, each its own dated record
    (start_date/end_date) - see resolve_client_opex_pct, which picks
    whichever record's own date window actually covers a given member's
    policy period, so an earlier and later renewal's loading are never
    blended into one figure. Product is carried along for reference/
    display only - it's still sourced from GroupProductMapping/the
    membership export's own PRODUCTNAME where those are more
    authoritative. Wholesale-replaced on each fresh upload, like the
    other client-level mapping tables.
    """

    __tablename__ = "client_master_info"

    id = Column(Integer, primary_key=True)
    master_client_name = Column(String, nullable=False)
    opex_pct = Column(Float, nullable=True)
    product = Column(String, nullable=True)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    source_filename = Column(String, nullable=True)
    uploaded_at = Column(DateTime, default=datetime.datetime.utcnow)


class PortfolioDataSnapshot(Base):
    """Single-row setting remembering the book's own data-as-of (production/
    extract) date - e.g. the real Members/Claims exports are each named
    "as of 15072026", meaning that's when the data was actually pulled,
    which can be weeks before an analysis is actually run. Earned-premium
    proration (see app/scoring/rules/portfolio_analysis.py) should measure
    elapsed policy time against THIS date, not the calendar day someone
    happens to click "Run analysis" - otherwise a stale-but-unrefreshed
    book looks more "earned" than the data can actually support. Captured
    once (via upload or a direct set) and reused as the default for every
    subsequent summary/member-detail call until updated again.
    """

    __tablename__ = "portfolio_data_snapshot"

    id = Column(Integer, primary_key=True)
    data_as_of_date = Column(Date, nullable=True)
