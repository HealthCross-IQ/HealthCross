"""Computes HC's commission fee on a policy/endorsement premium - the %
HealthCross earns from Qatar Insurance Co. (QIC) for medical insurance
business sold through the platform.

Rate banding is channel x tier:
  - Broker-introduced business: 6.5% (Bronze/Silver) / 5% (Gold/Platinum)
  - Direct business: 11.5% (Bronze/Silver) / 10% (Gold/Platinum)
  - Group/case-to-case business isn't banded at all - it's a negotiated
    rate entered manually per row (see `manual_fee_pct`).

Mirrors app/scoring/engine.py's shape: one small pure function, a plain
list of active rates (sourced from the FeeRateCard table), and an
orchestrating `compute_hc_fee()` that returns both the headline numbers
and a `details` sub-dict recording exactly how the rate was chosen - fee
math should be as auditable as the risk scorecard is.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional

VAT_PCT = 0.05

# Tiers banded into the two rate-card bands the fee table distinguishes.
# A Product value naming exactly one tier bands cleanly; a mixed value
# (e.g. "Gold/Bronze", a real value seen on tracker rows for a group whose
# members span more than one tier) can't be banded automatically - see
# band_for_product.
_BRONZE_SILVER_TIERS = {"bronze", "silver"}
_GOLD_PLATINUM_TIERS = {"gold", "platinum"}


@dataclass
class FeeRate:
    channel: str  # "broker" / "direct"
    tier_band: str  # "bronze_silver" / "gold_platinum"
    fee_pct: float


def band_for_product(product: Optional[str]) -> Optional[str]:
    """Bands a Product/tier label onto "bronze_silver" or "gold_platinum".

    Returns None when the label doesn't resolve to a single band - either
    it names no recognized tier, or it names tiers from BOTH bands at once
    (e.g. "Gold/Bronze") - in either case the caller must supply a manual
    rate rather than have one inferred. Public (not underscore-prefixed)
    because app.finance.tracker_analysis also uses this to check whether a
    tracker row's recorded fee % matches its rate-card band.
    """
    if not product:
        return None
    tokens = {t.strip().lower() for t in product.replace("/", " ").split() if t.strip()}
    in_bronze_silver = bool(tokens & _BRONZE_SILVER_TIERS)
    in_gold_platinum = bool(tokens & _GOLD_PLATINUM_TIERS)
    if in_bronze_silver and not in_gold_platinum:
        return "bronze_silver"
    if in_gold_platinum and not in_bronze_silver:
        return "gold_platinum"
    return None


def compute_hc_fee(
    channel: str,
    product: Optional[str],
    premium_excl_vat: float,
    rate_cards: List[FeeRate],
    manual_fee_pct: Optional[float] = None,
    vat_pct: float = VAT_PCT,
) -> Dict:
    """Computes HC's fee on one policy/endorsement premium.

    `channel` is "broker", "direct", or "group". Group business, and any
    "broker"/"direct" row whose `product` doesn't band cleanly (see
    band_for_product), REQUIRES `manual_fee_pct` - there's no rate-card
    fallback to guess from, matching the source tracker's "manual calc"
    convention rather than inventing a rate.
    """
    tier_band = band_for_product(product)
    is_manual = manual_fee_pct is not None or channel == "group" or tier_band is None

    if is_manual:
        if manual_fee_pct is None:
            raise ValueError(
                f"No fee rate available for channel={channel!r} product={product!r} - "
                "a Group/mixed-tier row needs an explicit manual_fee_pct."
            )
        fee_pct = manual_fee_pct
        rate_source = "manual"
    else:
        match = next((r for r in rate_cards if r.channel == channel and r.tier_band == tier_band), None)
        if match is None:
            raise ValueError(f"No active FeeRateCard row for channel={channel!r} tier_band={tier_band!r}")
        fee_pct = match.fee_pct
        rate_source = "rate_card"

    hc_fees = round(premium_excl_vat * fee_pct, 2)
    vat_amount = round(hc_fees * vat_pct, 2)
    total_value = round(hc_fees + vat_amount, 2)

    return {
        "hc_fee_pct": fee_pct,
        "hc_fees": hc_fees,
        "vat_pct": vat_pct,
        "vat_amount": vat_amount,
        "total_value": total_value,
        "is_manual_fee": is_manual,
        "details": {
            "channel": channel,
            "product": product,
            "tier_band": tier_band,
            "rate_source": rate_source,
        },
    }
