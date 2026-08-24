"""Field-by-field comparison between an existing (incumbent) plan and a
quoted (proposed) plan's standard 10-field benefit summary.

Values arrive as free text from whichever insurer's parser produced them
(e.g. "AED 13,800" vs "USD 3,000"), so a numeric direction is only given
when a currency + amount can be confidently extracted from BOTH sides -
USD is converted to AED at the fixed peg (3.6725) so magnitudes are
comparable. Anything that can't be parsed this way falls back to a plain
text-equality check and is flagged "review" rather than a guessed
direction, since silently misjudging a benefit change is worse than asking
a human to look.
"""
import re
from typing import Any, Dict, Optional

from app.scoring.rules.benefits_summary import STANDARD_FIELDS

AED_PER_USD = 3.6725

# "US$7,500,000" is how a Cigna-style booklet writes it, and it is the
# form OCR returns - matching only "AED" and "USD" left every limit read
# off one of those documents uncomparable, so fields whose numbers were
# plainly there still fell through to "review".
_AMOUNT_RE = re.compile(r"(AED|USD|US\$|\$)\s*([\d,]+(?:\.\d+)?)", re.IGNORECASE)
_USD_MARKERS = ("USD", "US$", "$")


def extract_amount_aed(text: Optional[str]) -> Optional[float]:
    """The first currency amount in a free-text benefit value, in AED.

    None when there is no amount to read - "Covered up to Policy Limit",
    "Not specified in source document". That is a real answer, and it is
    not zero.
    """
    if not text:
        return None
    match = _AMOUNT_RE.search(text)
    if not match:
        return None
    currency, amount_text = match.group(1).upper(), match.group(2).replace(",", "")
    try:
        amount = float(amount_text)
    except ValueError:
        return None
    return amount * AED_PER_USD if currency in _USD_MARKERS else amount


#: The private name the rest of this module already calls.
_extract_amount_aed = extract_amount_aed


def compare_benefit_value(existing_text: Optional[str], quoted_text: Optional[str]) -> Dict[str, Any]:
    existing_amount = _extract_amount_aed(existing_text)
    quoted_amount = _extract_amount_aed(quoted_text)

    if existing_amount is not None and quoted_amount is not None:
        pct_change = round((quoted_amount - existing_amount) / existing_amount * 100, 1) if existing_amount else None
        if quoted_amount > existing_amount:
            direction = "improved"
        elif quoted_amount < existing_amount:
            direction = "reduced"
        else:
            direction = "same"
        return {
            "existing": existing_text,
            "quoted": quoted_text,
            "existing_amount_aed": round(existing_amount, 2),
            "quoted_amount_aed": round(quoted_amount, 2),
            "pct_change": pct_change,
            "direction": direction,
        }

    existing_norm = (existing_text or "").strip().lower()
    quoted_norm = (quoted_text or "").strip().lower()
    direction = "same" if existing_norm and quoted_norm and existing_norm == quoted_norm else "review"
    return {
        "existing": existing_text,
        "quoted": quoted_text,
        "existing_amount_aed": None,
        "quoted_amount_aed": None,
        "pct_change": None,
        "direction": direction,
    }


def compare_benefit_summaries(existing_summary: Dict[str, str], quoted_summary: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    return {
        field: compare_benefit_value(existing_summary.get(field), quoted_summary.get(field))
        for field in STANDARD_FIELDS
    }
