"""Recalibrates scoring parameters from recorded case outcomes.

Two things get recalibrated independently, both guarded by a minimum sample
size and a cap on how much a single run may move a parameter (so one batch
of outcomes can't whipsaw the scorecard):

1. Top-level factor weights (demographic / claims experience / benefit
   richness / industry) via logistic regression of each case's four
   component risk scores against whether it turned out profitable.
2. Nationality-zone multipliers, which start neutral (1.0) by policy and are
   the primary thing this system is meant to *learn* rather than assert -
   via logistic regression of each case's zone-mix fractions against the
   same profitability outcome.
3. Two zone interaction effects, same neutral-start/learned-not-asserted
   pattern: zone x maternity-exposure, and zone x network-tier richness
   (e.g. "married Arab women on a Platinum network are higher risk" is a
   hypothesis to be confirmed from outcomes, not hardcoded).
"""
from typing import Dict, List

import numpy as np

from app.reference.nationality_zones import ALL_ZONES

MIN_SAMPLE_SIZE = 20
MAX_RELATIVE_WEIGHT_CHANGE = 0.15
MAX_ZONE_MULTIPLIER_CHANGE = 0.10
ZONE_MULTIPLIER_FLOOR = 0.5
ZONE_MULTIPLIER_CEILING = 2.0

_WEIGHT_KEYS = ["w_demographic", "w_claims_experience", "w_benefit_richness", "w_industry"]
_FEATURE_KEYS = ["demographic_risk", "claims_experience_risk", "benefit_richness_risk", "industry_risk"]


def _fit_profitability_model(X: np.ndarray, y: np.ndarray):
    if len(set(y.tolist())) < 2:
        return None
    # Imported here rather than at module level - sklearn pulls in a native
    # extension (_pairwise_fast) that some locked-down Windows environments
    # block outright (an Application Control / WDAC policy), which would
    # otherwise crash the entire app at startup just to support this one
    # admin-only recalibration feature.
    from sklearn.linear_model import LogisticRegression

    model = LogisticRegression(max_iter=1000)
    model.fit(X, y)
    return model


def recalibrate_weights(samples: List[dict], current_weights: Dict[str, float]) -> dict:
    """samples: [{demographic_risk, claims_experience_risk, benefit_richness_risk, industry_risk, profitable}]"""
    if len(samples) < MIN_SAMPLE_SIZE:
        return {
            "recalibrated": False,
            "reason": f"Need at least {MIN_SAMPLE_SIZE} outcomes with results, have {len(samples)}.",
            "weights": current_weights,
        }

    X = np.array([[s[key] for key in _FEATURE_KEYS] for s in samples])
    y = np.array([1 if s["profitable"] else 0 for s in samples])

    model = _fit_profitability_model(X, y)
    if model is None:
        return {
            "recalibrated": False,
            "reason": "Outcomes need both profitable and unprofitable examples to learn from.",
            "weights": current_weights,
        }

    # Higher risk score should predict LOWER profitability; flip sign so
    # weights stay positive "risk importance" values.
    raw_importances = np.clip(-model.coef_[0], a_min=0, a_max=None)
    if raw_importances.sum() == 0:
        raw_importances = np.array([current_weights[k] for k in _WEIGHT_KEYS])
    normalized = raw_importances / raw_importances.sum()

    new_weights = {}
    for key, proposed in zip(_WEIGHT_KEYS, normalized):
        old = current_weights[key]
        capped_delta = float(np.clip(proposed - old, -MAX_RELATIVE_WEIGHT_CHANGE, MAX_RELATIVE_WEIGHT_CHANGE))
        new_weights[key] = max(0.01, old + capped_delta)

    total = sum(new_weights.values())
    new_weights = {k: round(v / total, 4) for k, v in new_weights.items()}

    return {
        "recalibrated": True,
        "weights": new_weights,
        "metrics": {"train_accuracy": round(model.score(X, y), 4), "sample_size": len(samples)},
    }


def _recalibrate_zone_interaction(samples: List[dict], current_multipliers: Dict[str, float], mix_key: str) -> dict:
    if len(samples) < MIN_SAMPLE_SIZE:
        return {
            "recalibrated": False,
            "reason": f"Need at least {MIN_SAMPLE_SIZE} outcomes with results, have {len(samples)}.",
            "multipliers": current_multipliers,
        }

    X = np.array([[s[mix_key].get(zone, 0.0) for zone in ALL_ZONES] for s in samples])
    y = np.array([1 if s["profitable"] else 0 for s in samples])

    model = _fit_profitability_model(X, y)
    if model is None:
        return {
            "recalibrated": False,
            "reason": "Outcomes need both profitable and unprofitable examples to learn from.",
            "multipliers": current_multipliers,
        }

    # A zone whose presence predicts LOWER profitability should get a higher
    # risk multiplier, so again flip the sign of the learned coefficient.
    coefficients = -model.coef_[0]
    # Center coefficients so the average nudge is ~0 (a multiplier vector
    # shouldn't drift the whole book up or down, only re-rank the zones).
    centered = coefficients - coefficients.mean()

    new_multipliers = {}
    for zone, delta in zip(ALL_ZONES, centered):
        old = current_multipliers[zone]
        capped_delta = float(np.clip(delta, -MAX_ZONE_MULTIPLIER_CHANGE, MAX_ZONE_MULTIPLIER_CHANGE))
        new_multipliers[zone] = round(
            min(ZONE_MULTIPLIER_CEILING, max(ZONE_MULTIPLIER_FLOOR, old + capped_delta)), 4
        )

    return {
        "recalibrated": True,
        "multipliers": new_multipliers,
        "metrics": {"train_accuracy": round(model.score(X, y), 4), "sample_size": len(samples)},
    }


def recalibrate_zone_multipliers(samples: List[dict], current_multipliers: Dict[str, float]) -> dict:
    """samples: [{zone_mix: {zone: fraction, ...}, profitable}]"""
    return _recalibrate_zone_interaction(samples, current_multipliers, "zone_mix")


def recalibrate_zone_maternity_multipliers(samples: List[dict], current_multipliers: Dict[str, float]) -> dict:
    """samples: [{zone_maternity_mix: {zone: fraction of members who are BOTH
    maternity-risk and in that zone, ...}, profitable}]. Learns whether a
    zone's maternity exposure specifically (not just its overall headcount)
    predicts profitability - e.g. the user's observation that maternity
    utilization patterns differ by nationality zone.
    """
    return _recalibrate_zone_interaction(samples, current_multipliers, "zone_maternity_mix")


def recalibrate_zone_network_multipliers(samples: List[dict], current_multipliers: Dict[str, float]) -> dict:
    """samples: [{zone_network_mix: {zone: fraction-of-members-in-zone times
    the case's network_tier_score, ...}, profitable}]. Learns whether a
    zone's presence on a rich/expensive network (e.g. MSH Platinum) predicts
    profitability differently than the same zone on a cheap network.
    """
    return _recalibrate_zone_interaction(samples, current_multipliers, "zone_network_mix")
