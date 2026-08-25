"""Industry as a rating factor - switched off for now.

The table below is a set of opening guesses, not house numbers, and the
two it is most confidently wrong about are the two that matter: it
prices education and professional services at 0.90, a discount, where
the underwriting view is that schools, universities and legal practices
are among the riskier accounts on the book. A factor that moves the
price in the wrong direction is worse than no factor at all, so until
there are real numbers to put here it does not move the price.

Nothing is deleted. The multipliers stay exactly as they were, and
turning the factor back on is this one flag - so replacing the guesses
with measured figures is a data change rather than a rewrite.
"""

#: While this is False every industry rates neutral, and the industry
#: slot is dropped from the scorecard's weighting rather than held at a
#: forced-neutral 1.0 - see app/scoring/engine.py, which does the same
#: for claims experience when a case has no claims. Holding weight for a
#: factor nobody is measuring dilutes the factors that are.
INDUSTRY_RATING_ENABLED = False

INDUSTRY_RISK_MULTIPLIERS = {
    "construction": 1.35,
    "mining": 1.40,
    "manufacturing": 1.15,
    "oil_and_gas": 1.30,
    "logistics_transport": 1.20,
    "hospitality": 1.10,
    "healthcare": 1.10,
    "retail": 1.00,
    "trading": 1.00,
    "education": 0.90,
    "financial_services": 0.85,
    "technology": 0.85,
    "professional_services": 0.90,
    "government": 0.95,
    "nonprofit": 0.95,
}

DEFAULT_INDUSTRY_MULTIPLIER = 1.0


def _normalize_industry(industry: str) -> str:
    return industry.strip().lower().replace(" ", "_").replace("-", "_")


def industry_risk(industry: str) -> float:
    if not INDUSTRY_RATING_ENABLED or not industry:
        return DEFAULT_INDUSTRY_MULTIPLIER
    return INDUSTRY_RISK_MULTIPLIERS.get(_normalize_industry(industry), DEFAULT_INDUSTRY_MULTIPLIER)
