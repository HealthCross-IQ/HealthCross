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
            "annual aggregate limit", "annual policy limit", "annual benefit limit",
        ],
    },
    "Area of Cover": {
        "group": "General",
        "keywords": [
            "area of cover", "geographical cover", "territory for elective", "geographical scope",
            "basic territory", "area of coverage", "territorial limit",
        ],
    },
    "Pre-existing & Chronic Conditions": {
        "group": "General",
        "keywords": [
            "pre-existing condition", "pre existing condition", "chronic condition",
            # Cigna Smart Care's own label for this row is rendered as an
            # icon graphic rather than real text, so it's recovered via OCR
            # (app/ingestion/international_tob.py's _text_then_ocr) - which
            # reads it inconsistently ("Pre-ExIsSting CONGITIONS", "rre-
            # Existing Conditions" depending on render resolution). "including
            # chronic" is the one substring that survives every OCR pass.
            "including chronic",
        ],
    },
    "Congenital Conditions": {
        "group": "Additional Benefits",
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
            # Cigna's own wording never pairs "room" directly with
            # "accommodation" or "hospital" - it's "Accommodation on a
            # private room basis for in-patient treatment" (Global Care)
            # or "Accommodation costs for in-patient treatment" (Smart
            # Care), bundled into a bullet list under the generic
            # "Hospital charges for:" label - which also contains a
            # "Prescribed medicines..." bullet, so without a keyword here
            # this whole row instead falls through to Prescribed
            # Medicines / Pharmacy's own keyword further down.
            "private room basis", "accommodation costs for",
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
        "keywords": [
            "cancer treatment", "cancer support",
            # Cigna's own label/section is "Oncology treatment", never a
            # phrase containing "cancer treatment" at all.
            "oncology",
        ],
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
            # Cigna's own label is the bare "Routine out-patient" (paired
            # with "Routine in-patient" for the delivery/hospital-stay
            # side) - the word "antenatal" only appears in its
            # clarification note, never the label or section itself.
            "routine out-patient",
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
        # Cigna's own label is "Complications of pregnancy and childbirth"
        # (covering elective/emergency c-sections too) rather than a
        # phrase containing "maternity complication" at all.
        "keywords": ["maternity complication", "complications of pregnancy"],
    },
    "Newborn Cover": {
        "group": "Maternity",
        "keywords": ["newborn", "new-born", "new born cover"],
    },
    "Maternity Co-insurance": {
        "group": "Maternity",
        "keywords": [
            "inpatient maternity", "maternity co-insurance", "maternity coinsurance",
            # Cigna Smart Care's own label is "Routine out-patient
            # co-insurance" (paired with "Routine in-patient" for the
            # limit itself) - checked ahead of Antenatal Care in
            # MATCH_ORDER, whose own "routine out-patient" keyword would
            # otherwise claim this co-insurance row before this more
            # specific one got a chance. Requires "routine" - a bare
            # "out-patient co-insurance" also exists as its own generic,
            # unrelated row in the same document (the plan's overall
            # out-patient co-insurance, nothing to do with maternity).
            "routine out-patient co-insurance",
            # HealthCROSS Global's own template splits this into two rows,
            # one per side, rather than stating a single co-insurance
            # figure - "Maternity inpatient- Copay" and "Maternity
            # Outpatient Deductible" (both under "Maternity Benefits (For
            # Married Females):"). Both contain "maternity inpatient"/
            # "maternity outpatient", which Maternity Annual Limit and
            # Antenatal Care's own keywords would otherwise claim first,
            # so these need to be specific enough to intercept only the
            # copay/deductible row, not the limit/coverage row next to it.
            "maternity inpatient- copay", "maternity outpatient deductible",
        ],
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
        "keywords": [
            "optical benefit", "optical limit", "annual optical cover", "optical annual", "optical cover",
            # Cigna calls this section "Vision benefits" rather than
            # "Optical" at all, and its own bare "Annual Maximum" row (just
            # covering the eye exam itself, paid in full) isn't the real
            # dollar limit - the frames/lenses limit sits on this separate,
            # more specific row instead ("Expenses for: ... Prescribed
            # lenses to correct vision ... US$500 per year of insurance").
            "prescribed lenses",
        ],
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
            # Cigna's own label is "Routine adult physical examinations",
            # under a "Wellbeing benefits" section - neither says "health
            # check" or "wellness" at all.
            "routine adult physical exam",
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
        "group": "Additional Benefits",
        "keywords": [
            "alternative medicine", "homeopathy", "ayurvedic", "ayurveda",
            "complementary and alternative treatment", "complementary and alternative medicine",
        ],
    },
    "Emergency Medical Evacuation & Repatriation": {
        "group": "Assistance",
        "keywords": [
            "emergency medical evacuation", "medical evacuation", "repatriation", "second medical opinion",
            # Cigna's own label is the bare "International emergency
            # services" - the actual evacuation/repatriation wording only
            # appears in its lettered sub-list clarification, not the label.
            "international emergency services",
        ],
    },
    "Work-related Injuries": {
        "group": "Additional Benefits",
        "keywords": ["work-related injur", "work related injur", "occupational injur"],
    },
    "Passive War Risk": {
        "group": "Additional Benefits",
        "keywords": ["passive war risk", "war risk"],
    },
    "Psychiatric Treatment": {
        "group": "Additional Benefits",
        "keywords": ["psychiatric", "psychotherap", "mental health"],
    },
}

# Agreed presentation order - grouped by section, as originally proposed.
DISPLAY_ORDER: List[str] = [
    "Annual/Indemnity Maximum", "Area of Cover",
    "Pre-existing & Chronic Conditions",
    "Network / Provider Tier",
    "Room Type / Accommodation", "Companion / Parental Accommodation",
    "ICU / Intensive Care", "Surgery", "Organ Transplant", "Cancer Treatment",
    "Kidney Dialysis",
    "GP / Specialist Consultation", "Diagnostics (Lab/X-ray/Imaging)",
    "Physiotherapy", "Prescribed Medicines / Pharmacy",
    "Outpatient Co-insurance/Deductible",
    "Antenatal Care", "Normal Delivery", "C-Section", "Maternity Complications",
    "Newborn Cover", "Maternity Annual Limit", "Maternity Co-insurance",
    "Dental Annual Limit", "Dental Co-insurance",
    "Optical Annual Limit", "Optical Co-insurance",
    "Health Check-up", "Adult Vaccinations", "Cancer Screening",
    "Emergency Medical Evacuation & Repatriation",
    "Congenital Conditions", "Alternative Medicine Limit",
    "Work-related Injuries", "Passive War Risk", "Psychiatric Treatment",
]

# Matching priority - most specific first, so a more specific category
# claims a row before a more generic one (e.g. "Annual/Indemnity Maximum",
# checked last) gets a chance to swallow it.
MATCH_ORDER: List[str] = [
    # Checked before Antenatal Care/Maternity Annual Limit - Maternity
    # Annual Limit's own bare "maternity" catch-all keyword is especially
    # greedy (it matches ANY maternity-adjacent row, e.g. Sukoon's
    # "Inpatient Maternity: 10%" co-insurance line), and Cigna Smart
    # Care's own "Routine out-patient co-insurance" row would otherwise be
    # claimed by Antenatal Care's own "routine out-patient" keyword before
    # this more specific co-insurance category ever got a chance.
    "Maternity Co-insurance",
    "Antenatal Care", "Normal Delivery", "C-Section", "Maternity Complications",
    "Newborn Cover",
    # Co-insurance checked before its matching Annual Limit category - the
    # limit's own keywords (e.g. "dental benefit") match the whole section
    # a co-insurance row also sits in, so checking the limit first would
    # claim every row in that section for the limit and the co-insurance
    # category would never be reached.
    "Maternity Annual Limit",
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
    "Area of Cover",
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


# A word wrapping across a line inside a hyphenated compound (e.g.
# "Non-emergency work-\nrelated injuries", "Routine out-patient co-\n
# insurance") re-extracts as the hyphen followed by a stray space
# ("work- related", "co- insurance") rather than the clean "work-related"/
# "co-insurance" a keyword is written against - collapsing "-\s+" to "-"
# on both the search text and each keyword before comparing closes this
# whole class of mismatch regardless of exactly where a given document
# happens to wrap, rather than needing a new keyword variant hand-added
# every time a fresh real-world example turns up.
_HYPHEN_WRAP_RE = re.compile(r"-\s+")


def _normalize_hyphen_wrap(text: str) -> str:
    return _HYPHEN_WRAP_RE.sub("-", text)


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

    search_text = _normalize_hyphen_wrap(f"{section or ''} {label or ''}".lower())
    for name in MATCH_ORDER:
        if any(_normalize_hyphen_wrap(keyword) in search_text for keyword in CATEGORIES[name]["keywords"]):
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

    Some of these documents (Sukoon's, in particular) also list a second,
    narrower network name right after the primary one with no punctuation
    or marker between them at all (e.g. "Edge CCAD" - Edge is the primary
    network, CCAD a separate, narrower one) since the line break that
    originally separated them on the page is long since collapsed into a
    plain space by the time this value reaches here. Only the first word
    is the primary network name callers actually want.
    """
    if category != "Network / Provider Tier":
        return value
    match = _NETWORK_COINSURANCE_MARKER_RE.search(value)
    trimmed = value[: match.start()].strip() if match else value
    return trimmed.split()[0] if trimmed else trimmed


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


# Cigna's own optical (and some other benefit) limits state the co-pay
# inline in the same cell as the dollar limit itself ("US $500 per year of
# insurance Co-pay: NIL", or "...Co-insurance: 20%" on Cigna Smart Care's
# own documents) rather than as its own row, so a plain per-row category
# match never reaches a distinct "Optical Co-insurance" value - the whole
# cell already belongs to Optical Annual Limit. Pulled out of that
# already-matched value instead of requiring its own row.
_COPAY_CLAUSE_RE = re.compile(r"co-?(?:pay|insurance):\s*([^.;\n]+)", re.IGNORECASE)


def extract_copay_clause(limit_value: Optional[str]) -> Optional[str]:
    if not limit_value:
        return None
    match = _COPAY_CLAUSE_RE.search(limit_value)
    if not match:
        return None
    clause = match.group(1).strip().rstrip(".")
    return clause or None


# Cigna's own dental table never states one flat co-insurance percentage -
# only a per-class breakdown ("Class one Investigative and Preventative
# treatment: NIL co-pay", "Class two ...: 20% co-pay", "Class three ...:
# 50% co-pay"), each its own row rather than a single "Dental Co-insurance"
# line the normal per-row category match could find directly.
_DENTAL_CLASS_LABEL_RE = re.compile(r"^class (one|two|three)\b", re.IGNORECASE)
_DENTAL_CLASS_ORDER = ("one", "two", "three")


def dental_class_coinsurance_from_rows(rows: List[Dict[str, str]]) -> Optional[str]:
    """Combines a Cigna-style "Class one/two/three" dental co-insurance
    breakdown into one descriptive value, in class order, or None if this
    document doesn't have that per-class row shape at all.
    """
    by_class: Dict[str, str] = {}
    for row in rows:
        match = _DENTAL_CLASS_LABEL_RE.match((row.get("label") or "").strip())
        if match and "dental" in (row.get("section") or "").lower():
            by_class.setdefault(match.group(1).lower(), row.get("value") or "")
    if not by_class:
        return None
    return "; ".join(f"Class {cls}: {by_class[cls]}" for cls in _DENTAL_CLASS_ORDER if cls in by_class)


# Cigna Global Care's own table never states a maternity co-insurance/copay
# figure anywhere (unlike its dental/optical equivalents) because it's
# always NIL for this insurer - there's no row for the normal per-row
# category match to find at all, so this has to be supplied as a known
# default for this document family rather than extracted. "In-Cigna
# Healthcare network"/"Out-of-Cigna Healthcare network" is this format's
# own network-tier column header, occurring in effectively every Cigna
# Global Care export, so its presence reliably identifies the family
# without depending on any prose elsewhere that a different export might
# phrase differently or omit.
_CIGNA_MARKER_RE = re.compile(r"cigna", re.IGNORECASE)


def looks_like_cigna_globalcare(rows: List[Dict[str, str]]) -> bool:
    return any(_CIGNA_MARKER_RE.search(f"{row.get('label', '')} {row.get('value', '')}") for row in rows)


# Cigna Smart Care never gives out-patient antenatal care its own limit
# row at all (unlike Global Care's plain "Routine out-patient" row) - the
# only row mentioning it is "Routine out-patient co-insurance", which
# rightly belongs to Maternity Co-insurance instead (see MATCH_ORDER), so
# a plain per-row category match never gives Antenatal Care a value of
# its own. "Pregnancy benefits and services as per DHA mandate" is this
# document family's own distinctive clarification wording for exactly
# this benefit, buried in that row's note rather than its label/section.
_ANTENATAL_NOTE_RE = re.compile(r"pregnancy benefits and services", re.IGNORECASE)


def antenatal_care_covered_from_rows(rows: List[Dict[str, str]]) -> Optional[str]:
    if any(_ANTENATAL_NOTE_RE.search(row.get("note") or "") for row in rows):
        return "Covered"
    return None


# HealthCROSS Global's own template splits Maternity Co-insurance across
# two separate rows rather than stating one figure - "Maternity inpatient-
# Copay" and "Maternity Outpatient Deductible" - so a plain per-row
# category match (first match per plan wins) would silently drop whichever
# one it saw second. Combines both into one descriptive value instead,
# same approach as dental_class_coinsurance_from_rows.
_MATERNITY_INPATIENT_COPAY_RE = re.compile(r"maternity\s+inpatient-?\s*copay", re.IGNORECASE)
_MATERNITY_OUTPATIENT_DEDUCTIBLE_RE = re.compile(r"maternity\s+outpatient\s+deductible", re.IGNORECASE)


def healthcross_global_maternity_coinsurance_from_rows(rows: List[Dict[str, str]]) -> Optional[str]:
    parts = []
    for row in rows:
        label = (row.get("label") or "").strip()
        if _MATERNITY_INPATIENT_COPAY_RE.search(label):
            parts.append(f"Inpatient Copay: {row.get('value') or ''}")
        elif _MATERNITY_OUTPATIENT_DEDUCTIBLE_RE.search(label):
            parts.append(f"Outpatient Deductible: {row.get('value') or ''}")
    if not parts:
        return None
    return "; ".join(parts)


# Bridges this 36-category master list onto the older, fixed 12-field
# standard summary (app/scoring/rules/benefits_summary.py) used by the
# per-case existing/quoted plan review - a single-tier document (e.g.
# Sukoon's own "Category 1" layout) doesn't fit Bupa's multi-tier-per-
# column table shape at all, so it otherwise falls all the way through
# to the crude label-proximity text fallback. Several standard fields
# collapse onto one category here (e.g. "coinsurance" and "deductible"
# both draw on network/co-insurance wording) rather than each getting
# its own dedicated category, matching the coarser granularity the
# 12-field summary already accepted from the Bupa-specific extractor.
CATEGORY_TO_STANDARD_FIELD = {
    "Area of Cover": "area_of_cover",
    "Annual/Indemnity Maximum": "annual_limit",
    "Pre-existing & Chronic Conditions": "pre_existing_chronic_limit",
    "Maternity Annual Limit": "maternity_limit",
    "Maternity Co-insurance": "maternity_coinsurance",
    "Dental Annual Limit": "dental",
    "Optical Annual Limit": "optical",
    "Outpatient Co-insurance/Deductible": "coinsurance",
    "Alternative Medicine Limit": "alternative_or_complementary_treatment",
    "Prescribed Medicines / Pharmacy": "pharmacy_limit_and_coinsurance",
    "Health Check-up": "health_screening_wellness",
    # Not one of the fixed standard-summary fields (app/scoring/rules/
    # benefits_summary.py) - feeds BenefitPlan.network_type directly (see
    # to_case_benefit_plan_fields below), same column the CAT-style/
    # labeled-row parsers already populate for other insurer layouts.
    "Network / Provider Tier": "network",
}


def build_standard_summary_from_rows(rows: List[Dict[str, str]]) -> Dict[str, str]:
    """Maps a document's raw {"section", "label", "value"} rows (as
    extracted by app/ingestion/international_tob.py) onto the fixed
    12-field standard summary, via the same category matching used for
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
    # Newborn). Complications is stated as covered "up to indemnity
    # limit" - i.e. it IS the scheme's real maternity ceiling, not just
    # one itemized amount among several - so it takes priority over the
    # smaller per-procedure figures (Normal Delivery/C-Section) whenever
    # present, rather than being folded into a combined itemized string
    # that would rather report Normal Delivery's usually-smaller figure.
    if "Maternity Annual Limit" not in category_values:
        if "Maternity Complications" in category_values:
            category_values["Maternity Annual Limit"] = category_values["Maternity Complications"]
        else:
            itemized = [
                (label, category_values[cat])
                for cat, label in (
                    ("Normal Delivery", "Normal Delivery"),
                    ("C-Section", "C-Section"),
                    ("Antenatal Care", "Antenatal"),
                )
                if cat in category_values
            ]
            if itemized:
                category_values["Maternity Annual Limit"] = "; ".join(f"{label}: {value}" for label, value in itemized)

    if (
        "Maternity Co-insurance" not in category_values
        and "Maternity Annual Limit" in category_values
        and looks_like_cigna_globalcare(rows)
    ):
        category_values["Maternity Co-insurance"] = "NIL"

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
        "network_type": summary.get("network"),
    }
