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
    if not industry:
        return DEFAULT_INDUSTRY_MULTIPLIER
    return INDUSTRY_RISK_MULTIPLIERS.get(_normalize_industry(industry), DEFAULT_INDUSTRY_MULTIPLIER)
