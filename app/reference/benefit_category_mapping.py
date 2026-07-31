"""Canonical benefit-category mapping for the detailed insurer comparison.

Rather than showing every insurer's own exact wording as its own row (which
produces a mostly-empty table since insurers rarely word the same benefit
identically - Bupa's "Overall Annual Maximum" vs Cigna's "Plan Annual
Maximum" vs Sukoon's "Indemnity Limit" are all the same benefit), each raw
row extracted from a TOB is mapped onto one of this fixed 38-category
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
        "keywords": ["annual maximum", "indemnity limit", "overall annual", "plan annual maximum", "sum insured"],
    },
    "Area of Cover": {
        "group": "General",
        "keywords": ["area of cover", "geographical cover", "territory for elective", "geographical scope", "basic territory"],
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
        "keywords": ["medical providers network", "available network", "network in the uae"],
    },
    "Emergency Treatment (In/Out of Network)": {
        "group": "General",
        "keywords": ["emergency treatment", "emergency in uae", "emergency abroad"],
    },
    "Elective Treatment (In/Out of Network)": {
        "group": "General",
        "keywords": ["elective treatment", "elective abroad", "elective in uae"],
    },
    "Room Type / Accommodation": {
        "group": "Inpatient",
        "keywords": ["room accommodation", "hospital accommodation", "room & board", "room and board", "room type"],
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
        "keywords": ["organ transplant"],
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
            "outpatient consultation", "gp consultation",
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
        "keywords": ["outpatient co-insurance", "out-patient co-insurance", "outpatient deductible", "outpatient coinsurance"],
    },
    "Antenatal Care": {
        "group": "Maternity",
        "keywords": ["antenatal", "ante natal", "ante-natal"],
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
            "maternity outpatient", "maternity inpatient", "maternity in-patient",
            "maternity out-patient", "maternity benefit",
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
        "keywords": ["optical co-insurance", "optical copay"],
    },
    "Health Check-up": {
        "group": "Wellness",
        "keywords": ["health check-up", "health check up", "wellness health check", "routine health examination"],
    },
    "Adult Vaccinations": {
        "group": "Wellness",
        "keywords": ["vaccination", "vaccine"],
    },
    "Cancer Screening": {
        "group": "Wellness",
        "keywords": ["cancer screening", "breast cancer screening", "prostate cancer screening", "diabetes screening"],
    },
    "Alternative Medicine Limit": {
        "group": "Alternative Medicine",
        "keywords": ["alternative medicine", "homeopathy", "ayurvedic"],
    },
    "Emergency Medical Evacuation & Repatriation": {
        "group": "Assistance",
        "keywords": ["emergency medical evacuation", "medical evacuation", "repatriation", "second medical opinion"],
    },
    "Work-related Injuries": {
        "group": "Exclusions",
        "keywords": ["work-related injur", "work related injur", "occupational injur"],
    },
    "Passive War Risk": {
        "group": "Exclusions",
        "keywords": ["passive war risk", "war risk"],
    },
    "Psychiatric Treatment": {
        "group": "Exclusions",
        "keywords": ["psychiatric", "psychotherap", "mental health"],
    },
}

# Agreed presentation order - grouped by section, as originally proposed.
DISPLAY_ORDER: List[str] = [
    "Annual/Indemnity Maximum", "Area of Cover", "Home Country Cover",
    "Pre-existing & Chronic Conditions", "Congenital Conditions",
    "Network / Provider Tier", "Emergency Treatment (In/Out of Network)",
    "Elective Treatment (In/Out of Network)",
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
    "Dental Annual Limit", "Dental Co-insurance",
    "Optical Annual Limit", "Optical Co-insurance",
    "Alternative Medicine Limit",
    "Health Check-up", "Adult Vaccinations", "Cancer Screening",
    "Emergency Medical Evacuation & Repatriation",
    "Work-related Injuries", "Passive War Risk", "Psychiatric Treatment",
    "Congenital Conditions",
    "Room Type / Accommodation", "Companion / Parental Accommodation",
    "ICU / Intensive Care", "Organ Transplant", "Cancer Treatment", "Kidney Dialysis", "Surgery",
    "GP / Specialist Consultation", "Diagnostics (Lab/X-ray/Imaging)", "Physiotherapy",
    "Prescribed Medicines / Pharmacy", "Outpatient Co-insurance/Deductible",
    # "Area of Cover" ahead of Emergency/Elective Treatment - a real "Basic
    # Territory for Elective & Emergency treatment" label contains both
    # "elective"/"emergency treatment" AND the area-of-cover signal, and
    # it's the latter that row is actually about.
    "Home Country Cover", "Area of Cover",
    "Emergency Treatment (In/Out of Network)", "Elective Treatment (In/Out of Network)",
    "Network / Provider Tier",
    "Pre-existing & Chronic Conditions",
    "Annual/Indemnity Maximum",
]

assert set(DISPLAY_ORDER) == set(MATCH_ORDER) == set(CATEGORIES.keys()), (
    "DISPLAY_ORDER, MATCH_ORDER, and CATEGORIES must all name exactly the same categories"
)


def map_label_to_category(section: Optional[str], label: Optional[str]) -> Optional[str]:
    """Returns the canonical category name this row belongs to, or None if
    it doesn't match any of the fixed categories (kept per-plan in an
    "Other benefits" appendix instead of being silently dropped).
    """
    search_text = f"{section or ''} {label or ''}".lower()
    for name in MATCH_ORDER:
        if any(keyword in search_text for keyword in CATEGORIES[name]["keywords"]):
            return name
    return None


# Bridges this 38-category master list onto the older, fixed 11-field
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
            category_values[category] = row.get("value")

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
