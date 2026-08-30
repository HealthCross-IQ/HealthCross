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


class FeeRateCard(Base):
    """HC's commission rate table: the % of premium (excl. VAT) HealthCross
    earns on medical insurance sold through the platform, banded by sales
    channel (broker-introduced vs. direct) and plan tier. A negotiated
    Group/case-to-case rate isn't stored here at all - it's entered directly
    as a manual override on the PaymentTrackerEntry it applies to (see
    PaymentTrackerEntry.is_manual_fee), exactly mirroring the "manual calc"
    convention already used in the working payment tracker.

    Versioned like ScoringWeightSet - a rate change is a new row with a
    fresh effective_from rather than an edit, so historical
    PaymentTrackerEntry rows stay explainable against whatever rate applied
    when they were invoiced.
    """

    __tablename__ = "fee_rate_cards"

    id = Column(Integer, primary_key=True)
    channel = Column(String, nullable=False)  # "broker" / "direct"
    tier_band = Column(String, nullable=False)  # "bronze_silver" / "gold_platinum"
    fee_pct = Column(Float, nullable=False)
    effective_from = Column(Date, nullable=False)
    is_active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class PaymentTrackerEntry(Base):
    """One row per QIC document (a premium invoice or a credit/cancellation
    endorsement) that HealthCross earns a fee on - the same shape as the
    real "Payment Tracker" working sheet: a client-facing leg (what QIC
    invoiced the client/broker, and whether the client has paid), and an
    HC-facing leg (HC's own fee on that premium, invoiced to the client, and
    whether HC has collected it).

    `doc_no` is normalized (see app.finance.common.normalize_doc_no) so it
    joins cleanly against QicSoaLine.doc_no despite the two systems
    formatting the same document number differently ("128-93727" here vs.
    "128 - 93727" in a QIC export).

    Company-wide, not tied to any underwriting Case - a Case is a submission
    being risk-scored; a PaymentTrackerEntry is a bound policy/endorsement
    already earning HC a fee.
    """

    __tablename__ = "payment_tracker_entries"

    id = Column(Integer, primary_key=True)

    invoice_mode = Column(String, nullable=True)  # "Manual" / "Auto"
    source_name = Column(String, nullable=True)  # broker company name, or "Direct Channel"
    channel = Column(String, nullable=False)  # "broker" / "direct" / "group"
    division = Column(String, nullable=True)  # branch, e.g. "Dubai Branch"
    client_code = Column(String, nullable=True)
    main_policy_holder = Column(String, nullable=True)
    sub_group_name = Column(String, nullable=True)
    policy_no = Column(String, nullable=True)
    policy_period_from = Column(Date, nullable=True)
    policy_period_to = Column(Date, nullable=True)
    endorsement_no = Column(String, nullable=True)
    endorsement_type = Column(String, nullable=True)  # Inception / Addition / Deletion / Modification

    doc_date = Column(Date, nullable=True)
    due_date = Column(Date, nullable=True)
    # Normalized join key against QicSoaLine.doc_no - see class docstring.
    doc_no = Column(String, nullable=True, index=True)
    doc_no_raw = Column(String, nullable=True)
    doc_code = Column(String, nullable=True)  # "128" (debit) / "228" (credit) prefix
    client_doc_no = Column(String, nullable=True)

    invoice_amount = Column(Float, nullable=True)
    premium_excl_vat = Column(Float, nullable=True)
    basmah = Column(Float, nullable=True)
    icp = Column(Float, nullable=True)
    client_vat = Column(Float, nullable=True)
    client_payment_status = Column(String, nullable=True)  # "Settled" / "Outstanding"
    healthcross_doc = Column(String, nullable=True)  # internal HC doc/invoice number, once raised
    client_premium_amount_excl_tax = Column(Float, nullable=True)

    product = Column(String, nullable=True)  # tier label, e.g. "Silver", "Gold/Bronze" (mixed group)
    # True when the fee wasn't computed from FeeRateCard - a negotiated
    # Group/case-to-case rate, or a mixed-tier Product the rate card can't
    # band cleanly. Mirrors the source sheet's literal "manual calc" entries.
    is_manual_fee = Column(Boolean, default=False)
    hc_fee_pct = Column(Float, nullable=True)
    hc_fees = Column(Float, nullable=True)
    vat_pct = Column(Float, default=0.05)
    vat_amount = Column(Float, nullable=True)
    total_value = Column(Float, nullable=True)

    invoice_type = Column(String, nullable=True)  # "Debit" / "Credit"
    invoice_status = Column(String, nullable=True)  # "Due for collection" / "Outstanding"
    invoice_raised_period = Column(String, nullable=True)  # free-text label, e.g. "Sept'25", "Raised Mar 26"
    hc_payment_status = Column(String, nullable=True)  # "Received" / blank
    payment_receive_date = Column(Date, nullable=True)
    # Raw text when the source's payment-receive value wasn't a clean date
    # (e.g. "Received Oct'25") - kept verbatim rather than dropped.
    payment_receive_note = Column(String, nullable=True)

    source_batch = Column(String, nullable=True)  # which upload this row came from, for audit/traceability
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class QicSoaLine(Base):
    """One raw line from a QIC Statement of Account export - the ground
    truth HealthCross reconciles its own PaymentTrackerEntry rows against.
    QIC exports vary in shape (a "Debit LC"/"Credit LC" pair of columns in
    one export, a single signed AMOUNT + Dr/Cr flag in another) - both are
    normalized to the same debit_amount/credit_amount/dr_cr fields here by
    app.ingestion.qic_soa so every downstream query only deals with one
    shape regardless of which export produced it.

    `doc_no` is normalized the same way as PaymentTrackerEntry.doc_no - see
    that class's docstring.
    """

    __tablename__ = "qic_soa_lines"

    id = Column(Integer, primary_key=True)

    doc_no = Column(String, nullable=True, index=True)
    doc_no_raw = Column(String, nullable=True)
    tran_code = Column(String, nullable=True)
    doc_date = Column(Date, nullable=True)
    tran_type = Column(String, nullable=True)
    doc_due_date = Column(Date, nullable=True)
    lob_code = Column(String, nullable=True)
    policy_no = Column(String, nullable=True)
    insured_name = Column(String, nullable=True)
    insured_code = Column(String, nullable=True)
    currency = Column(String, nullable=True)
    doc_desc = Column(Text, nullable=True)
    debit_amount = Column(Float, default=0)
    credit_amount = Column(Float, default=0)
    dr_cr = Column(String, nullable=True)  # "D" / "C"
    sequence_no = Column(Integer, nullable=True)
    policy_from_date = Column(Date, nullable=True)
    policy_to_date = Column(Date, nullable=True)
    cust_code = Column(String, nullable=True)
    cust_name = Column(String, nullable=True)
    endorsement_no = Column(String, nullable=True)
    pol_comms_doc = Column(String, nullable=True)
    branch = Column(String, nullable=True)
    gross_amount = Column(Float, nullable=True)
    age_band = Column(String, nullable=True)
    prod_code = Column(String, nullable=True)
    cust_group_code = Column(String, nullable=True)
    cust_group_name = Column(String, nullable=True)
    broker_name = Column(String, nullable=True)
    control_account = Column(Text, nullable=True)
    cal_year = Column(String, nullable=True)
    doc_created_by = Column(String, nullable=True)
    installment_number = Column(String, nullable=True)
    endorsement_type = Column(String, nullable=True)

    # Label for which SOA export this line came from (e.g. "2026-06",
    # "2026-07 recon") - set at upload time, lets a period-over-period
    # comparison of two QIC SOA exports be run without re-uploading.
    statement_period = Column(String, nullable=True, index=True)
    imported_at = Column(DateTime, default=datetime.datetime.utcnow)


class HealthCrossFeeStatementLine(Base):
    """One line from a QIC "Statement of Outstanding" addressed to
    HealthCross itself (Customer Code 216331 = Dubai, 293276 = Abu Dhabi) -
    what QIC owes HC for policy-linked fees/commission. Unlike QicSoaLine's
    "Gross Amount", there's no single amount column here: `credit_amount`
    minus `debit_amount` is the real net-owed-to-HC figure per line,
    verified against this file's own printed "Net Due to You" total -
    QIC's own "Transaction Type" labels (Others/TPA Fee/Other Fee) turned
    out to all represent real fee amounts when validated that way, so
    reconciliation nets every row regardless of that label.

    `doc_no` here matches PaymentTrackerEntry.healthcross_doc, NOT
    PaymentTrackerEntry.doc_no (the client-side QIC document number) - the
    two are different QIC document series.

    `division` is a per-*policy* attribute (which office administers that
    policy) read straight from the row - it is NOT reliable for telling
    which of the two branch statements (Dubai/Abu Dhabi) a row came from;
    the Abu Dhabi statement's own rows can themselves read Division =
    "Dubai Branch". `statement_customer_code` is the statement's own true
    identity (216331 = Dubai, 293276 = Abu Dhabi, from the file's own
    header block) - re-uploading a file only replaces rows sharing both
    its statement_period and its statement_customer_code, so uploading one
    branch never wipes the other's rows.
    """

    __tablename__ = "health_cross_fee_statement_lines"

    id = Column(Integer, primary_key=True)

    doc_no = Column(String, nullable=True, index=True)
    doc_no_raw = Column(String, nullable=True)
    doc_date = Column(Date, nullable=True)
    due_date = Column(Date, nullable=True)
    policy_no = Column(String, nullable=True, index=True)
    assured_name = Column(String, nullable=True)
    invoice_no = Column(String, nullable=True)
    debit_amount = Column(Float, default=0)
    credit_amount = Column(Float, default=0)
    transaction_type = Column(String, nullable=True)
    division = Column(String, nullable=True)  # per-policy office, NOT per-statement branch - see docstring
    statement_customer_code = Column(String, nullable=True, index=True)  # "216331" (Dubai) / "293276" (Abu Dhabi)
    policy_from_date = Column(Date, nullable=True)
    policy_to_date = Column(Date, nullable=True)
    age_band = Column(String, nullable=True)

    statement_period = Column(String, nullable=True, index=True)
    imported_at = Column(DateTime, default=datetime.datetime.utcnow)


class BankTransaction(Base):
    """One raw line from an HC bank account statement export - used to
    confirm a QIC payment marked "Received" in the PaymentTrackerEntry
    ledger actually landed in the bank (see app.finance.reconciliation),
    and as the source-of-truth for treasury/cash-flow reporting.
    """

    __tablename__ = "bank_transactions"

    id = Column(Integer, primary_key=True)

    account_number = Column(String, nullable=True)
    txn_date = Column(Date, nullable=True)
    value_date = Column(Date, nullable=True)
    reference_number = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    credit_amount = Column(Float, default=0)
    debit_amount = Column(Float, default=0)
    balance = Column(Float, nullable=True)
    currency = Column(String, default="AED")

    statement_period = Column(String, nullable=True, index=True)
    imported_at = Column(DateTime, default=datetime.datetime.utcnow)


class Employee(Base):
    """HC payroll roster. monthly_salary is the recurring default used both
    to display current headcount cost and to auto-generate this month's
    salary ExpenseEntry rows (see POST /finance/expenses/generate) - an
    individual month's actual paid amount can still differ (e.g. a partial
    month, an installment) since that's recorded on the ExpenseEntry itself,
    never by editing this roster row.

    `basic_salary` is optional and distinct from `monthly_salary` - UAE
    end-of-service gratuity is legally calculated on basic salary alone,
    which can be lower than the total monthly package once housing/
    transport/other allowances are added. When not set, gratuity falls
    back to `monthly_salary` (see routes_finance.py's _with_end_of_service),
    matching this app's original behavior before the two were split apart.
    """

    __tablename__ = "employees"

    id = Column(Integer, primary_key=True)
    full_name = Column(String, nullable=False)
    role_title = Column(String, nullable=True)
    monthly_salary = Column(Float, nullable=False)
    basic_salary = Column(Float, nullable=True)
    currency = Column(String, default="AED")
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class RecurringExpense(Base):
    """A template for a recurring non-salary expense (e.g. Nivotime's portal
    support/maintenance fee, ABH's outsourced accounting fee, Etisalat).
    `default_amount` is None for a variable-cost item like Etisalat, whose
    actual monthly amount depends on usage and is entered fresh each period
    on its ExpenseEntry rather than defaulted from here.
    """

    __tablename__ = "recurring_expenses"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)  # e.g. "software", "telecom", "outsourced_services", "rent"
    default_amount = Column(Float, nullable=True)
    currency = Column(String, default="AED")
    expense_type = Column(String, nullable=False)  # "fixed" / "variable"
    is_active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class ExpenseEntry(Base):
    """One actual expense for one calendar month - salaries (linked to
    Employee), recurring non-salary costs (linked to RecurringExpense), and
    genuinely one-off expenses (neither link set) all live in this single
    table so cash-flow and forecasting only need to sum one place.
    """

    __tablename__ = "expense_entries"

    id = Column(Integer, primary_key=True)
    period = Column(Date, nullable=False, index=True)  # first-of-month marker, e.g. 2026-07-01
    category = Column(String, nullable=False)
    expense_type = Column(String, nullable=False)  # "fixed" / "variable"
    description = Column(String, nullable=True)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="AED")

    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    recurring_expense_id = Column(Integer, ForeignKey("recurring_expenses.id"), nullable=True)

    payment_date = Column(Date, nullable=True)
    source = Column(String, default="manual")  # "manual" / "generated" / "bank_matched"
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    employee = relationship("Employee")
    recurring_expense = relationship("RecurringExpense")


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
