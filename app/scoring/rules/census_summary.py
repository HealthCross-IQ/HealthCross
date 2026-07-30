"""Census demographic breakdown - the underwriter-facing view of the same
raw census rows that feed `demographic_risk()`, but reported as plain
counts/percentages (age bands, gender, marital status, relation,
nationality zone mix) rather than a risk multiplier. Kept as its own module
since it answers "what does this group look like" rather than "how risky is
it" - the latter is `app/scoring/rules/demographic.py`.
"""
from statistics import mean
from typing import List

from app.reference.nationality_zones import ALL_ZONES, ZONE_OTHER
from app.scoring.rules.demographic import (
    AGE_BANDS,
    INFANT_AGE_MAX,
    MATERNITY_AGE_MAX,
    MATERNITY_AGE_MIN,
)


def _age_band_label(low: int, high: int) -> str:
    return f"{low}-{high}"


def _pct(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def census_demographic_summary(census: List[dict]) -> dict:
    total = len(census)
    if not total:
        return {"total_members": 0}

    ages = [m["age"] for m in census if m.get("age") is not None]

    age_band_counts = {_age_band_label(low, high): 0 for low, high, _ in AGE_BANDS}
    age_band_gender_counts = {_age_band_label(low, high): {"M": 0, "F": 0} for low, high, _ in AGE_BANDS}
    for m in census:
        age = m.get("age")
        if age is None:
            continue
        for low, high, _ in AGE_BANDS:
            if low <= age <= high:
                band = _age_band_label(low, high)
                age_band_counts[band] += 1
                if m.get("gender") in ("M", "F"):
                    age_band_gender_counts[band][m["gender"]] += 1
                break

    gender_counts = {"M": 0, "F": 0, "Other": 0}
    for m in census:
        gender_counts[m.get("gender") if m.get("gender") in ("M", "F") else "Other"] += 1

    marital_status_counts: dict = {}
    marital_status_gender_counts: dict = {}
    for m in census:
        status = (m.get("marital_status") or "Unknown").title()
        marital_status_counts[status] = marital_status_counts.get(status, 0) + 1
        marital_status_gender_counts.setdefault(status, {"M": 0, "F": 0})
        if m.get("gender") in ("M", "F"):
            marital_status_gender_counts[status][m["gender"]] += 1

    relation_counts: dict = {}
    relation_gender_counts: dict = {}
    for m in census:
        relation = (m.get("relation") or "Other").title()
        relation_counts[relation] = relation_counts.get(relation, 0) + 1
        relation_gender_counts.setdefault(relation, {"M": 0, "F": 0})
        if m.get("gender") in ("M", "F"):
            relation_gender_counts[relation][m["gender"]] += 1

    zone_counts = {zone: 0 for zone in ALL_ZONES}
    for m in census:
        zone_counts[m.get("nationality_zone") or ZONE_OTHER] += 1

    married_female_count = sum(
        1 for m in census if m.get("gender") == "F" and (m.get("marital_status") or "").lower() == "married"
    )
    maternity_risk_count = sum(
        1
        for m in census
        if m.get("gender") == "F"
        and (m.get("marital_status") or "").lower() == "married"
        and m.get("age") is not None
        and MATERNITY_AGE_MIN <= m["age"] <= MATERNITY_AGE_MAX
    )
    female_spouse_count = sum(1 for m in census if (m.get("relation") or "").lower() == "spouse" and m.get("gender") == "F")
    male_spouse_count = sum(1 for m in census if (m.get("relation") or "").lower() == "spouse" and m.get("gender") == "M")
    infant_count = sum(
        1 for m in census if (m.get("relation") or "").lower() == "child" and (m.get("age") or 0) <= INFANT_AGE_MAX
    )
    favorable_children_count = sum(
        1 for m in census if (m.get("relation") or "").lower() == "child" and (m.get("age") or 0) > INFANT_AGE_MAX
    )

    employees = [m for m in census if (m.get("relation") or "").lower() == "employee"]
    male_employees = sum(1 for m in employees if m.get("gender") == "M")

    return {
        "total_members": total,
        "avg_age": round(mean(ages), 1) if ages else None,
        "age_band_counts": age_band_counts,
        "age_band_pct": {band: _pct(count, total) for band, count in age_band_counts.items()},
        "age_band_gender_counts": age_band_gender_counts,
        "gender_counts": gender_counts,
        "gender_pct": {g: _pct(c, total) for g, c in gender_counts.items()},
        "marital_status_counts": marital_status_counts,
        "marital_status_pct": {s: _pct(c, total) for s, c in marital_status_counts.items()},
        "marital_status_gender_counts": marital_status_gender_counts,
        "relation_counts": relation_counts,
        "relation_pct": {r: _pct(c, total) for r, c in relation_counts.items()},
        "relation_gender_counts": relation_gender_counts,
        "nationality_zone_counts": zone_counts,
        "nationality_zone_pct": {z: _pct(c, total) for z, c in zone_counts.items()},
        "married_female_count": married_female_count,
        "married_female_pct": _pct(married_female_count, total),
        "maternity_risk_count": maternity_risk_count,
        "maternity_risk_pct": _pct(maternity_risk_count, total),
        "female_spouse_count": female_spouse_count,
        "male_spouse_count": male_spouse_count,
        "infant_count": infant_count,
        "favorable_children_count": favorable_children_count,
        "employee_count": len(employees),
        "male_employees": male_employees,
        "male_ratio_employees": _pct(male_employees, len(employees)) if employees else None,
    }
