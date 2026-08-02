"""Canonical benefit-category mapping for the detailed insurer comparison.

Rather than showing every insurer's own exact wording as its own row (which
produces a mostly-empty table since insurers rarely word the same benefit
identically - Bupa's "Overall Annual Maximum" vs Cigna's "Plan Annual
Maximum" vs Sukoon's "Indemnity Limit" are all the same benefit), each raw
row extracted from a TOB is mapped onto one of this fixed 36-category
master list (agreed with the underwriting team) before being placed into
the comparison. A plan only shows "-" for a category it genuinely doesn't
offer, not because its own wording didn't match another plan's.

Matching runs against a row's own section text plus its label (whichever
carries the real signal varies by document - see
app/ingestion/international_tob.py's docstring on section vs label
layout), so a document that only groups by section (e.g. Sukoon's, where
every dental sub-item's own label never says "dental") still resolves to
the right category via its section banner.

DISPLAY_ORDER is the agreed presentation order (grouped by section, as
originally proposed and confirmed). MATCH_ORDER is deliberately different
- most specific category first - so e.g. "Maternity Annual Limit" or
"Dental Annual Limit" get first crack at a row before the generic
"Annual/Indemnity Maximum" bucket would otherwise swallow it.
"""
import re
from typing import Any, Dict, List, Optional

CATEGORIES: Dict[str, Dict] = {
    "Annual/Indemnity Maximum": {
        "group": "General",
        "keywords": [
            "annual maximum", "indemnity limit", "overall annual", "plan annual maximum", "sum insured",
            "annual aggregate limit", "annual policy limit",
        ],
    },
    "Area of Cover": {
        "group": "General",
        "keywords": [
            "area of cover", "geographical cover", "territory for elective", "geographical scope",
            "basic territory", "area of coverage",
        ],
    },
    "Home Country Cover": {
        "group": "General",
        "keywords": ["home country cover"],
    },
    "Pre-existing & Chronic Conditions": {
        "group": "General",
        "keywords": ["pre-existing condition", "pre existing condition", "chronic condition"],
    },
    "Congenital Conditions": {
        "group": "General",
        "keywords": ["congenital"],
    },
    "Network / Provider Tier": {
        "group": "General",
        "keywords": ["medical providers network", "available network", "network in the uae", "medical network"],
    },
    "Room Type / Accommodation": {
        "group": "Inpatient",
        "keywords": [
            "room accommodation", "hospital accommodation", "room & board", "room and board",
            "room type", "hospital room",
        ],
    },
    "Companion / Parental Accommodation": {
        "group": "Inpatient",
        "keywords": ["companion accommodation", "parental accommodation", "accompanying family", "accompanying person"],
    },
    "ICU / Intensive Care": {
        "group": "Inpatient",
        "keywords": ["intensive care", "intensive therapy", " icu", "coronary care", "high dependency"],
    },
    "Surgery": {
        "group": "Inpatient",
        "keywords": ["surgical fees", "surgical operations", "surgery"],
    },
    "Organ Transplant": {
        "group": "Inpatient",
        "keywords": ["organ transplant", "transplant services"],
    },
    "Cancer Treatment": {
        "group": "Inpatient",
        "keywords": ["cancer treatment", "cancer support"],
    },
    "Kidney Dialysis": {
        "group": "Inpatient",
        "keywords": ["dialysis"],
    },
    "GP / Specialist Consultation": {
        "group": "Outpatient",
        "keywords": [
            "general practitioner", "specialist or consultant", "out-patient consultation",
            "outpatient consultation", "gp consultation", "gp/specialist", "gp / specialist",
            # Deliberately no bare "specialist consultation" - some documents
            # (HealthCROSS Global's, in particular) also have an INPATIENT
            # "Specialist Consultations" row of their own, and a loose match
            # here would let that unrelated inpatient row claim this
            # outpatient-only category before the real "GP/Specialist
            # Consultations" row (further down the document) ever got a
            # chance to (first match per plan wins).
        ],
    },
    "Diagnostics (Lab/X-ray/Imaging)": {
        "group": "Outpatient",
        "keywords": ["diagnostic investigation", "diagnostic services", "laboratory", "x-ray", "pathology"],
    },
    "Physiotherapy": {
        "group": "Outpatient",
        "keywords": ["physiotherapy"],
    },
    "Prescribed Medicines / Pharmacy": {
        "group": "Outpatient",
        "keywords": ["prescribed medicine", "prescribed pharmaceutical", "prescribed drugs", "pharmacy"],
    },
    "Outpatient Co-insurance/Deductible": {
        "group": "Outpatient",
        "keywords": [
            "outpatient co-insurance", "out-patient co-insurance", "outpatient deductible", "outpatient coinsurance",
            # Some documents (HealthCROSS Global's) state an outpatient
            # copay against a specific service line ("Laboratory, Radiology
            # and Pathology Tests-Copay") rather than one general
            # "Outpatient Co-insurance" row - the "-Copay" is what marks it
            # as the co-payment figure rather than the service's own
            # coverage/limit, which belongs to Diagnostics instead.
            "-copay",
        ],
    },
    "Antenatal Care": {
        "group": "Maternity",
        "keywords": [
            "antenatal", "ante natal", "ante-natal",
            # Some documents (HealthCROSS Global's, in particular) don't use
            # the word "antenatal" at all - their maternity OUTPATIENT limit
            # IS the antenatal-checkups benefit, as distinct from their
            # maternity INPATIENT limit (the delivery/hospital-stay cost,
            # mapped to Maternity Annual Limit below).
            "maternity outpatient", "maternity out-patient",
        ],
    },
    "Normal Delivery": {
        "group": "Maternity",
        "keywords": ["normal delivery"],
    },
    "C-Section": {
        "group": "Maternity",
        "keywords": ["c-section", "caesarean", "cesarean"],
    },
    "Maternity Complications": {
        "group": "Maternity",
        "keywords": ["maternity complication"],
    },
    "Newborn Cover": {
        "group": "Maternity",
        "keywords": ["newborn", "new-born", "new born cover"],
    },
    "Maternity Annual Limit": {
        "group": "Maternity",
        "keywords": [
            "maternity and childbirth cover", "maternity limit", "maternity annual",
            "maternity inpatient", "maternity in-patient", "maternity benefit",
            # Bare catch-all, checked last among the maternity categories -
            # every more specific one (Antenatal, Normal Delivery, C-Section,
            # Complications, Newborn) already gets first pick in MATCH_ORDER,
            # so this only catches a maternity row that doesn't fit any of
            # those (e.g. an odd punctuation variant like "Maternity- Limit"
            # that the more specific phrases above don't happen to match).
            "maternity",
        ],
    },
    "Dental Annual Limit": {
        "group": "Dental",
        "keywords": ["dental benefit", "dental limit", "annual dental cover", "dental cover", "dental annual"],
    },
    "Dental Co-insurance": {
        "group": "Dental",
        "keywords": ["dental co-insurance", "dental co-payment", "dental treatment copay", "dental copay"],
    },
    "Optical Annual Limit": {
        "group": "Optical",
        "keywords": ["optical benefit", "optical limit", "annual optical cover", "optical annual", "optical cover"],
    },
    "Optical Co-insurance": {
        "group": "Optical",
        "keywords": ["optical co-insurance", "optical co-payment", "optical copay"],
    },
    "Health Check-up": {
        "group": "Wellness",
        "keywords": [
            "health check-up", "health check up", "health check,", "health check/",
            "wellness health check", "routine health examination", "wellness package",
            # Bupa's own wellness/screening benefit is worded as a "Wellness -"
            # prefixed list of the specific tests it covers, rather than a
            # phrase containing "health check" at all.
            "wellness - mammogram",
        ],
    },
    "Adult Vaccinations": {
        "group": "Wellness",
        "keywords": ["vaccination", "vaccine", "influenza"],
    },
    "Cancer Screening": {
        "group": "Wellness",
        "keywords": [
            "cancer screening", "breast cancer screening", "prostate cancer screening", "diabetes screening",
            "pap smear", "mammogram", "colonoscopy",
        ],
    },
    "Alternative Medicine Limit": {
        "group": "Alternative Medicine",
        "keywords": [
            "alternative medicine", "homeopathy", "ayurvedic", "ayurveda",
            "complementary and alternative treatment", "complementary and alternative medicine",
        ],
    },
    "Emergency Medical Evacuation & Repatriation": {
        "group": "Assistance",
        "keywords": ["emergency medical evacuation", "medical evacuation", "repatriation", "second medical opinion"],
    },
    "Work-related Injuries": {
        "group": "Special Cover",
        "keywords": ["work-related injur", "work related injur", "occupational injur"],
    },
    "Passive War Risk": {
        "group": "Special Cover",
        "keywords": ["passive war risk", "war risk"],
    },
    "Psychiatric Treatment": {
        "group": "Special Cover",
        "keywords": ["psychiatric", "psychotherap", "mental health"],
    },
}

# Agreed presentation order - grouped by section, as originally proposed.
DISPLAY_ORDER: List[str] = [
    "Annual/Indemnity Maximum", "Area of Cover", "Home Country Cover",
    "Pre-existing & Chronic Conditions", "Congenital Conditions",
    "Network / Provider Tier",
    "Room Type / Accommodation", "Companion / Parental Accommodation",
    "ICU / Intensive Care", "Surgery", "Organ Transplant", "Cancer Treatment",
    "Kidney Dialysis",
    "GP / Specialist Consultation", "Diagnostics (Lab/X-ray/Imaging)",
    "Physiotherapy", "Prescribed Medicines / Pharmacy",
    "Outpatient Co-insurance/Deductible",
    "Antenatal Care", "Normal Delivery", "C-Section", "Maternity Complications",
    "Newborn Cover", "Maternity Annual Limit",
    "Dental Annual Limit", "Dental Co-insurance",
    "Optical Annual Limit", "Optical Co-insurance",
    "Health Check-up", "Adult Vaccinations", "Cancer Screening",
    "Alternative Medicine Limit",
    "Emergency Medical Evacuation & Repatriation",
    "Work-related Injuries", "Passive War Risk", "Psychiatric Treatment",
]

# Matching priority - most specific first, so a more specific category
# claims a row before a more generic one (e.g. "Annual/Indemnity Maximum",
# checked last) gets a chance to swallow it.
MATCH_ORDER: List[str] = [
    "Antenatal Care", "Normal Delivery", "C-Section", "Maternity Complications",
    "Newborn Cover", "Maternity Annual Limit",
    # Co-insurance checked before its matching Annual Limit category - the
    # limit's own keywords (e.g. "dental benefit") match the whole section
    # a co-insurance row also sits in, so checking the limit first would
    # claim every row in that section for the limit and the co-insurance
    # category would never be reached.
    "Dental Co-insurance", "Dental Annual Limit",
    "Optical Co-insurance", "Optical Annual Limit",
    # Checked before Diagnostics/GP-Specialist/Physiotherapy/Pharmacy for the
    # same reason as Dental/Optical Co-insurance above - a service-specific
    # copay row (e.g. "Laboratory, Radiology and Pathology Tests-Copay")
    # would otherwise be claimed by Diagnostics' own "laboratory"/"pathology"
    # keywords before this category ever got a chance.
    "Outpatient Co-insurance/Deductible",
    "Alternative Medicine Limit",
    "Health Check-up", "Adult Vaccinations", "Cancer Screening",
    "Emergency Medical Evacuation & Repatriation",
    "Work-related Injuries", "Passive War Risk", "Psychiatric Treatment",
    "Congenital Conditions",
    "Room Type / Accommodation", "Companion / Parental Accommodation",
    "ICU / Intensive Care", "Organ Transplant", "Cancer Treatment", "Kidney Dialysis", "Surgery",
    "GP / Specialist Consultation", "Diagnostics (Lab/X-ray/Imaging)", "Physiotherapy",
    "Prescribed Medicines / Pharmacy",
    "Home Country Cover", "Area of Cover",
    "Network / Provider Tier",
    "Pre-existing & Chronic Conditions",
    "Annual/Indemnity Maximum",
]

assert set(DISPLAY_ORDER) == set(MATCH_ORDER) == set(CATEGORIES.keys()), (
    "DISPLAY_ORDER, MATCH_ORDER, and CATEGORIES must all name exactly the same categories"
)


# A row's label is sometimes just the bare word "Co-insurance"/
# "Coinsurance" with no qualifier of its own (the qualifier - "Dental",
# "Optical" - only appears in the row's SECTION banner, several rows
# earlier) - a plain substring match against "section + label" combined
# can't tell these apart from a keyword phrase like "dental co-insurance"
# alone, since "dental" and "co-insurance" never appear adjacent in the
# combined text. Checked only when the label truly has no qualifier of
# its own, so a label like "Optical Co-Insurance (Opted level)" (which
# already matches its category directly) is unaffected.
_BARE_COINSURANCE_LABELS = {"co-insurance", "coinsurance"}
_SECTION_QUALIFIED_COINSURANCE = [
    ("dental", "Dental Co-insurance"),
    ("optical", "Optical Co-insurance"),
    ("outpatient", "Outpatient Co-insurance/Deductible"),
]

# Some documents (Bupa's, in particular) state the limit itself as a bare
# one-word label - just "Dental" or "Optical" - rather than a phrase like
# "Dental Benefit"/"Optical Limit". A loose substring keyword ("dental")
# would risk matching an unrelated row whose note happens to mention the
# word in passing (e.g. "optical treatment following dental surgery"),
# so this only fires when the label IS that bare word, nothing else.
_BARE_LIMIT_LABELS = {
    "dental": "Dental Annual Limit",
    "optical": "Optical Annual Limit",
    "network": "Network / Provider Tier",
}

# Some documents lead the label with the category word followed by the
# specific items it covers (e.g. Maxmed's "OPTICAL Prescribed Lenses,
# Annual Eye Exam, Frames and Contact lenses") rather than a clean phrase
# like "Optical Benefit". A prefix match (label STARTS WITH "optical ")
# is safe against false positives in a way a bare substring anywhere in
# the text wouldn't be (that could match an unrelated row's note
# mentioning the word in passing).
_LIMIT_LABEL_PREFIXES = [("optical", "Optical Annual Limit"), ("dental", "Dental Annual Limit")]


def map_label_to_category(section: Optional[str], label: Optional[str]) -> Optional[str]:
    """Returns the canonical category name this row belongs to, or None if
    it doesn't match any of the fixed categories (kept per-plan in an
    "Other benefits" appendix instead of being silently dropped).
    """
    normalized_label = (label or "").strip().lower()
    if normalized_label in _BARE_COINSURANCE_LABELS:
        section_lower = (section or "").lower()
        for section_keyword, category in _SECTION_QUALIFIED_COINSURANCE:
            if section_keyword in section_lower:
                return category
    if normalized_label in _BARE_LIMIT_LABELS:
        return _BARE_LIMIT_LABELS[normalized_label]

    search_text = f"{section or ''} {label or ''}".lower()
    for name in MATCH_ORDER:
        if any(keyword in search_text for keyword in CATEGORIES[name]["keywords"]):
            return name

    # Nothing matched a specific phrase - fall back to a prefix check
    # (checked last, after every more specific keyword had its chance, so
    # e.g. "Optical Co-payment" is still caught by Optical Co-insurance's
    # own keyword above rather than landing here).
    for prefix, category in _LIMIT_LABEL_PREFIXES:
        if normalized_label.startswith(prefix + " "):
            return category
    return None


_NETWORK_COINSURANCE_MARKER_RE = re.compile(r"\b(IP|OP|Pharmacy|Maternity)\s*[:\-]", re.IGNORECASE)


USD_TO_AED_RATE = 3.6725

# Matches a USD-denominated amount however the document happened to write
# it ("USD 7,500,000", "US$ 7,500,000", "US $200", "$1,000,000") - the
# amount itself always follows one of these prefixes directly, so the
# match captures exactly the number to convert, nothing else in the cell.
_USD_AMOUNT_RE = re.compile(r"(?:USD|US\s?\$|\$)\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)


def unify_currency_to_aed(value: Optional[str], rate: float = USD_TO_AED_RATE) -> Optional[str]:
    """Appends the AED equivalent right after the first USD amount in a
    value, so every plan's limits can be read on the same scale without
    losing the source document's own wording. Left untouched when the
    value already states an AED figure of its own (Bupa's documents
    already give one, e.g. "USD 4,700,000 (AED 17,260,750)") or has no
    USD amount to convert in the first place.
    """
    if not value or "aed" in value.lower():
        return value
    match = _USD_AMOUNT_RE.search(value)
    if not match:
        return value
    amount = float(match.group(1).replace(",", ""))
    aed_amount = amount * rate
    aed_text = f" (AED {aed_amount:,.0f})"
    insert_at = match.end()
    return value[:insert_at] + aed_text + value[insert_at:]


def clean_category_value(category: str, value: str) -> str:
    """Some documents cram the network/provider tier name into the same
    cell as a per-category co-insurance breakdown that follows it (e.g.
    Sukoon's "Edge CCAD IP: 20% OP (excluding Pharmacy): 20% Pharmacy:
    10% ..." row) - only the tier name itself (everything before the
    first such marker) belongs in this category; the co-insurance detail
    that follows isn't part of "which network", so it's trimmed rather
    than shown as if it were the network name.
    """
    if category != "Network / Provider Tier":
        return value
    match = _NETWORK_COINSURANCE_MARKER_RE.search(value)
    return value[: match.start()].strip() if match else value


# Some documents (Bupa's, in particular) never give "Maternity
# Complications" its own row - it's one clause folded into the same combined
# "Maternity and childbirth" value as the overall limit ("...Complications
# of maternity and childbirth: Paid in full"), so a plain per-row category
# match never reaches it (the whole value already belongs to Maternity
# Annual Limit). Pulled out of that already-matched value instead of
# requiring its own row.
_MATERNITY_COMPLICATIONS_CLAUSE_RE = re.compile(
    r"complications? of maternity(?: and childbirth)?\s*:?\s*([^.;]+)", re.IGNORECASE
)


def extract_maternity_complications_clause(maternity_annual_limit_value: Optional[str]) -> Optional[str]:
    if not maternity_annual_limit_value:
        return None
    match = _MATERNITY_COMPLICATIONS_CLAUSE_RE.search(maternity_annual_limit_value)
    if not match:
        return None
    clause = match.group(1).strip().rstrip(".")
    return clause or None


# Bridges this 36-category master list onto the older, fixed 11-field
# standard summary (app/scoring/rules/benefits_summary.py) used by the
# per-case existing/quoted plan review - a single-tier document (e.g.
# Sukoon's own "Category 1" layout) doesn't fit Bupa's multi-tier-per-
# column table shape at all, so it otherwise falls all the way through
# to the crude label-proximity text fallback. Several standard fields
# collapse onto one category here (e.g. "coinsurance" and "deductible"
# both draw on network/co-insurance wording) rather than each getting
# its own dedicated category, matching the coarser granularity the
# 11-field summary already accepted from the Bupa-specific extractor.
CATEGORY_TO_STANDARD_FIELD = {
    "Area of Cover": "area_of_cover",
    "Annual/Indemnity Maximum": "annual_limit",
    "Pre-existing & Chronic Conditions": "pre_existing_chronic_limit",
    "Maternity Annual Limit": "maternity_limit",
    "Dental Annual Limit": "dental",
    "Optical Annual Limit": "optical",
    "Outpatient Co-insurance/Deductible": "coinsurance",
    "Alternative Medicine Limit": "alternative_or_complementary_treatment",
    "Prescribed Medicines / Pharmacy": "pharmacy_limit_and_coinsurance",
    "Health Check-up": "health_screening_wellness",
}


def build_standard_summary_from_rows(rows: List[Dict[str, str]]) -> Dict[str, str]:
    """Maps a document's raw {"section", "label", "value"} rows (as
    extracted by app/ingestion/international_tob.py) onto the fixed
    11-field standard summary, via the same category matching used for
    the detailed comparison - so any document family the detailed
    comparison already handles well also gets a usable Summary instead of
    the crude text-proximity fallback.
    """
    category_values: Dict[str, str] = {}
    for row in rows:
        category = map_label_to_category(row.get("section"), row.get("label"))
        if category and category not in category_values:
            category_values[category] = clean_category_value(category, row.get("value"))

    # Some documents (Sukoon's, in particular) never state one combined
    # "Maternity Annual Limit" figure - only itemized amounts per
    # procedure (Antenatal, Normal Delivery, C-Section, Complications,
    # Newborn). Rather than reporting maternity as "not specified" when
    # it plainly is covered, just itemized, combine whichever of those
    # are actually present into one descriptive value.
    if "Maternity Annual Limit" not in category_values:
        itemized = [
            (label, category_values[cat])
            for cat, label in (
                ("Normal Delivery", "Normal Delivery"),
                ("C-Section", "C-Section"),
                ("Maternity Complications", "Complications"),
                ("Antenatal Care", "Antenatal"),
            )
            if cat in category_values
        ]
        if itemized:
            category_values["Maternity Annual Limit"] = "; ".join(f"{label}: {value}" for label, value in itemized)

    return {
        field: category_values[category_name]
        for category_name, field in CATEGORY_TO_STANDARD_FIELD.items()
        if category_name in category_values
    }


_FIRST_AMOUNT_RE = re.compile(r"[\d,]+(?:\.\d+)?")
_NOT_COVERED_RE = re.compile(r"\bnot covered\b", re.IGNORECASE)


def _first_amount(text: Optional[str]) -> Optional[float]:
    """Local-market documents state limits as e.g. "1,000,000/-" or
    "5,000/- pppy" rather than the "USD 1,000,000" format
    app/ingestion/benefits_pdf.py's own _first_usd_amount expects, so this
    just takes the first number-looking token wherever it falls.
    """
    match = _FIRST_AMOUNT_RE.search(text or "")
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def to_case_benefit_plan_fields(summary: Dict[str, str]) -> Dict[str, Any]:
    """Best-effort mapping from build_standard_summary_from_rows' output
    onto the numeric/boolean BenefitPlan columns the scoring engine reads
    - same purpose as app/ingestion/benefits_pdf.py's to_benefit_plan_fields,
    generalized for documents whose limits aren't stated as "USD ####".
    """
    dental_text = summary.get("dental", "")
    optical_text = summary.get("optical", "")
    chronic_text = summary.get("pre_existing_chronic_limit", "")
    maternity_text = summary.get("maternity_limit", "")

    return {
        "annual_limit": _first_amount(summary.get("annual_limit")),
        "maternity_covered": bool(maternity_text) and not _NOT_COVERED_RE.search(maternity_text),
        "maternity_limit": _first_amount(maternity_text),
        "dental_covered": bool(dental_text) and not _NOT_COVERED_RE.search(dental_text),
        "optical_covered": bool(optical_text) and not _NOT_COVERED_RE.search(optical_text),
        "pre_existing_covered": bool(chronic_text) and not _NOT_COVERED_RE.search(chronic_text),
        "chronic_covered": bool(chronic_text) and not _NOT_COVERED_RE.search(chronic_text),
    }
