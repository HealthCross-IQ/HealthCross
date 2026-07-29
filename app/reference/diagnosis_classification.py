"""Chronic-vs-non-chronic classification for standard DHA/ICD-chapter
diagnosis groupings, as they appear on UAE health insurance claims reports.

Used to build the top-N diagnosis exposure summary: which categories are
chronic (ongoing cost drivers), which are typically acute/one-off, and
which warrant an explicit high-exposure flag (cancer, heart disease,
kidney failure) regardless of classification.
"""
from typing import Dict

CHRONIC = "chronic"
NON_CHRONIC = "non_chronic"
MIXED = "mixed"

# Keyed by the diagnosis grouping label as it appears on DHA-mandated claims
# reports. Extend as new groupings are seen on real reports.
DIAGNOSIS_CLASSIFICATION: Dict[str, dict] = {
    "neoplasms": {
        "classification": CHRONIC,
        "high_exposure": True,
        "note": "Cancer - typically the largest single cost driver by value despite a low claim count.",
    },
    "endocrine, nutritional, metabolic, immunity": {
        "classification": CHRONIC,
        "high_exposure": False,
        "note": "Diabetes, thyroid, etc. - high frequency, low severity per claim, managed chronic care.",
    },
    "diseases of musculoskeletal system and tissues": {
        "classification": MIXED,
        "high_exposure": False,
        "note": "Likely blends chronic joint/back conditions with one-off trauma/fractures.",
    },
    "diseases of nervous system and sense organs": {
        "classification": MIXED,
        "high_exposure": False,
        "note": "May include Optical claims grouped under this ICD chapter alongside genuine neuro conditions.",
    },
    "diseases of circulatory system": {
        "classification": CHRONIC,
        "high_exposure": True,
        "note": "Heart disease - watch in-patient claim frequency as an early utilization signal.",
    },
    "dental/oral diseases": {
        "classification": NON_CHRONIC,
        "high_exposure": False,
        "note": "Routine dental benefit utilization, not a disease-risk category.",
    },
    "diseases of respiratory system": {
        "classification": MIXED,
        "high_exposure": False,
        "note": "Usually high-frequency/low-severity acute infections; watch for chronic asthma/COPD concentration.",
    },
    "symptoms, signs and ill-defined conditions": {
        "classification": NON_CHRONIC,
        "high_exposure": False,
        "note": "Undiagnosed/workup codes by definition.",
    },
    "diseases of digestive system": {
        "classification": MIXED,
        "high_exposure": False,
        "note": "Can include chronic GI conditions or acute issues; usually immaterial unless concentrated.",
    },
    "diseases of genitourinary system": {
        "classification": MIXED,
        "high_exposure": True,
        "note": "Where kidney disease/dialysis claims would appear - monitor even if currently small; catastrophic if it emerges.",
    },
}

# In-patient claim-average thresholds used to flag likely one-off/shock
# claims or data-quality artifacts within a diagnosis grouping.
LARGE_CLAIM_IP_AVG_THRESHOLD = 30_000  # AED per admission - flag as a probable large/shock claim
LOW_IP_AVG_THRESHOLD = 1_000  # AED per admission - flag as a likely day-case/coding artifact


def _normalize(label: str) -> str:
    return " ".join(label.strip().lower().split())


def classify_diagnosis_group(label: str) -> dict:
    """Return chronic/exposure classification for a diagnosis grouping label.

    Falls back to an "unclassified" entry (mixed, not flagged) for labels
    not yet in the reference table, rather than raising - so an unfamiliar
    grouping from a new claims report format never blocks the analysis.
    """
    return DIAGNOSIS_CLASSIFICATION.get(
        _normalize(label),
        {"classification": MIXED, "high_exposure": False, "note": "Not yet classified - review manually."},
    )


def flag_diagnosis_group(value: float, count: int, ip_value: float, ip_count: int) -> dict:
    """Compute per-claim averages and one-off/data-quality flags for a diagnosis grouping."""
    avg_per_claim = value / count if count else 0.0
    ip_avg_per_claim = ip_value / ip_count if ip_count else 0.0

    flags = []
    if ip_count and ip_avg_per_claim >= LARGE_CLAIM_IP_AVG_THRESHOLD:
        flags.append("possible_large_or_shock_claim")
    if ip_count and ip_avg_per_claim < LOW_IP_AVG_THRESHOLD:
        flags.append("possible_daycase_coding_artifact")

    return {
        "avg_per_claim": round(avg_per_claim, 2),
        "ip_avg_per_claim": round(ip_avg_per_claim, 2),
        "flags": flags,
    }
