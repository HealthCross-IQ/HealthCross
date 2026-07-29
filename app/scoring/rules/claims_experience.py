from typing import List, Optional

LARGE_CLAIM_THRESHOLD = 50_000
CREDIBILITY_FULL_MEMBER_MONTHS = 1200  # roughly 100 lives for 12 months


def _loss_ratio_multiplier(loss_ratio: float) -> float:
    if loss_ratio < 0.60:
        return 0.80
    if loss_ratio < 0.80:
        return 1.00
    if loss_ratio < 1.00:
        return 1.30
    if loss_ratio < 1.30:
        return 1.60
    return 2.00


def claims_experience_risk(
    claims: List[dict],
    member_count: int,
    estimated_annual_premium: Optional[float] = None,
) -> dict:
    if not claims or not member_count:
        return {
            "score": 1.0,
            "credibility": 0.0,
            "loss_ratio": None,
            "large_claims_count": 0,
            "claim_frequency": None,
        }

    total_paid = sum(c.get("amount_paid") or 0 for c in claims)
    large_claims = [c for c in claims if (c.get("amount_paid") or 0) >= LARGE_CLAIM_THRESHOLD]
    # Cap large claims for experience rating; the excess sits with reinsurance/pooling.
    pooled_paid = sum(min(c.get("amount_paid") or 0, LARGE_CLAIM_THRESHOLD) for c in claims)

    years = sorted({c.get("policy_year") for c in claims if c.get("policy_year")})
    num_years = max(len(years), 1)

    member_months = member_count * 12 * num_years
    credibility = min(1.0, member_months / CREDIBILITY_FULL_MEMBER_MONTHS)

    if estimated_annual_premium and estimated_annual_premium > 0:
        premium_basis = estimated_annual_premium * num_years
    else:
        # Crude PMPM benchmark fallback when no premium indication is supplied.
        premium_basis = member_count * 250 * 12 * num_years

    loss_ratio = pooled_paid / premium_basis if premium_basis else None
    raw_loss_ratio = total_paid / premium_basis if premium_basis else None
    claim_frequency = len(claims) / member_count

    experience_multiplier = _loss_ratio_multiplier(loss_ratio) if loss_ratio is not None else 1.0
    manual_multiplier = 1.0  # neutral baseline for groups without credible experience

    blended_score = credibility * experience_multiplier + (1 - credibility) * manual_multiplier

    return {
        "score": round(blended_score, 4),
        "credibility": round(credibility, 3),
        "loss_ratio": round(loss_ratio, 3) if loss_ratio is not None else None,
        "raw_loss_ratio": round(raw_loss_ratio, 3) if raw_loss_ratio is not None else None,
        "large_claims_count": len(large_claims),
        "claim_frequency": round(claim_frequency, 3),
    }
