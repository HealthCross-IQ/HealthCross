"""When two differently-worded benefit values say the same thing.

An incumbent's document and a HealthCross quote describe identical cover
in different words, and a comparison that reads them literally reports a
difference on nearly every line. "Covered" against "Covered up to Policy
Limit"; "0%" against "NIL"; "Covered" against "Fully Covered". None of
those is a change, and flagging all of them as "review" is worse than
useless: an underwriter who has to check twelve rows to find the two real
differences stops checking.

Three kinds of sameness, and they are not the same kind:

  Nil is nil. "NIL", "Nil", "0%", "None", "-" are one value written five
  ways, and the distinction between them carries nothing.

  Cover at the plan's own limit. "Covered", "Fully Covered", "Covered up
  to Policy Limit", "Annual Limit", "Paid in Full" all mean the benefit
  is not separately capped - it runs to whatever the plan's overall
  maximum is. A document that says "Covered" and a quote that says
  "Covered up to Policy Limit" have made the same statement.

  Network names between insurers. This one is a judgement, not a
  synonym: Cigna's COMPREHENSIVE network is as broad as MSH Platinum, and
  only an underwriter knows that. It is deliberately kept OUT of
  app/reference/network_type_mapping.py, which maps HealthCross's OWN
  book values ("comprehensive" -> "MSH Comprehensive") and feeds every
  burning-cost lookup in Portfolio Analysis. The two mappings disagree on
  purpose, because they answer different questions: one asks what network
  a HealthCross member is on, the other asks whether an incumbent's
  network is as broad as the one being proposed.

Equivalence here only ever downgrades a "review" to "same". It never
turns a real difference into a match, and never decides a direction.

Pure functions over strings - no ORM, no database.
"""
import re
from typing import Optional, Set

#: Written five ways, worth nothing in all five.
NIL_VALUES: Set[str] = {
    "nil", "none", "no", "n/a", "na", "-", "--", "0", "0%", "0.00%",
    "zero", "no deductible", "no co-pay", "no copay", "no coinsurance",
    "not applicable", "waived",
}

#: The benefit is not separately capped - it runs to the plan's own
#: overall maximum. A quote and a booklet routinely pick different words
#: for this and mean exactly the same cover.
PLAN_LIMIT_VALUES: Set[str] = {
    "covered", "fully covered", "covered in full", "paid in full",
    "covered up to policy limit", "covered up to the policy limit",
    "covered up to plan limit", "covered up to the plan limit",
    "up to policy limit", "up to plan limit", "up to the annual limit",
    "annual limit", "policy limit", "plan limit", "full cover",
    "covered up to annual limit", "covered up to the annual limit",
    "as per annual limit", "subject to annual limit",
}

#: An incumbent insurer's network name against the HealthCross network of
#: equivalent breadth. Underwriting judgement, confirmed case by case -
#: not a general synonym table, and not to be reused for anything that
#: prices off HealthCross's own NETWORKTYPE values.
INCUMBENT_NETWORK_EQUIVALENTS = {
    # Cigna Global Care's own top network. Confirmed by underwriting as
    # equivalent in breadth to MSH Platinum.
    "comprehensive": "msh platinum",
}

_TRAILING_NOISE_RE = re.compile(
    r"\b(per (?:year of insurance|policy year|year|annum|person|member|visit)"
    r"(?: of insurance)?|per insured person|each|in total)\b",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[.,;:()\[\]/]+")


def normalize_text(value: Optional[str]) -> str:
    """Case, punctuation and period wording stripped - the parts of a
    benefit value that differ between documents without changing what
    they say.
    """
    text = str(value or "").strip().lower()
    text = _TRAILING_NOISE_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    return " ".join(text.split())


def _canonical(value: Optional[str]) -> Optional[str]:
    """One of "nil", "plan_limit", or None when the value is neither.

    None is the honest answer for anything with a number in it: an
    amount is not a synonym for anything, and pretending otherwise is
    how a real reduction gets reported as "same".
    """
    text = normalize_text(value)
    if not text:
        return None
    if text in NIL_VALUES:
        return "nil"
    if text in PLAN_LIMIT_VALUES:
        return "plan_limit"
    # "Covered Co-pay: Nil" and "Annual Limit Co-pay: NIL" are the same
    # statement with a nil qualifier appended - the qualifier adds
    # nothing, so strip it and try again rather than treating the two as
    # different from the bare form.
    without_copay = re.sub(r"\bco-?pay:?\s*(nil|none|0%?)\b", "", text).strip()
    if without_copay != text and without_copay in PLAN_LIMIT_VALUES:
        return "plan_limit"
    if without_copay != text and without_copay in NIL_VALUES:
        return "nil"
    return None


#: Fields that state what the MEMBER pays, not what the plan covers. In
#: one of these, a bare "NIL" says "the member contributes nothing" - the
#: same statement as "Annual Limit Co-pay: NIL", which adds only that the
#: benefit runs to the plan limit.
#:
#: The distinction is the whole point. In a LIMIT field, "NIL" and
#: "covered up to the plan limit" are opposites: one is no cover at all.
#: Collapsing them everywhere would report a dropped dental benefit as
#: unchanged, which is the worst mistake this comparison can make.
MEMBER_CONTRIBUTION_FIELDS = frozenset({
    "deductible",
    "coinsurance",
    "maternity_coinsurance",
    "pharmacy_limit_and_coinsurance",
})


def _states_no_member_contribution(value: Optional[str]) -> bool:
    """True for "NIL", and for "<covered> Co-pay: NIL" - both of which say
    the member pays nothing.
    """
    canonical = _canonical(value)
    if canonical == "nil":
        return True
    if canonical != "plan_limit":
        return False
    return bool(re.search(r"\bco-?pay:?\s*(nil|none|0%?)\b", normalize_text(value)))


def equivalent_values(
    existing: Optional[str],
    proposed: Optional[str],
    field: Optional[str] = None,
) -> bool:
    """True when the two say the same thing in different words.

    Both sides must resolve to the same canonical family. Two values that
    merely fail to resolve are NOT equivalent - "unrecognised" is not a
    family, and treating it as one would match every pair of free-text
    values the parsers could not read.
    """
    left, right = _canonical(existing), _canonical(proposed)
    if left is not None and left == right:
        return True
    if field in MEMBER_CONTRIBUTION_FIELDS:
        return _states_no_member_contribution(existing) and _states_no_member_contribution(proposed)
    return False


def equivalent_networks(existing: Optional[str], proposed: Optional[str]) -> bool:
    """True when an incumbent's network is the same breadth as the one
    being proposed - by name, or by the underwriting-confirmed
    equivalence table above.
    """
    left, right = normalize_text(existing), normalize_text(proposed)
    if not left or not right:
        return False
    if left == right:
        return True
    return INCUMBENT_NETWORK_EQUIVALENTS.get(left) == right


#: Area of cover is written as a territory, and the same territory is
#: written many ways ("Worldwide excluding USA", "Area II", "WW Exc USA").
#: Reduced to a key so the incumbent's wording and the rate card's Zone
#: can be compared at all.
_AREA_PATTERNS = (
    ("worldwide_excl_usa", (
        r"worldwide\s*(?:excluding|excl\.?|exc\.?|less|without)\s*(?:the\s*)?usa",
        r"\bww\s*(?:excluding|excl\.?|exc\.?)\s*usa",
        r"\barea\s*(?:ii|2)\b",
    )),
    ("worldwide", (r"^worldwide$", r"\barea\s*(?:i|1)\b", r"worldwide\s*(?:including|incl\.?)\s*usa")),
    ("gcc_and_isc", (r"\bgcc\b", r"gulf cooperation council", r"\barea\s*(?:iv|4)\b")),
    ("uae_only", (r"\buae\s*only\b", r"^uae$", r"in\s*country", r"\blocal\b")),
)


def area_of_cover_key(value: Optional[str]) -> Optional[str]:
    """The territory a free-text area-of-cover value describes, or None.

    Checked most specific first: "Worldwide excluding USA" contains the
    word "worldwide", so a naive check would collapse the two territories
    that differ by the single most expensive country in the world.
    """
    text = normalize_text(value)
    if not text:
        return None
    for key, patterns in _AREA_PATTERNS:
        if any(re.search(pattern, text) for pattern in patterns):
            return key
    return None


def equivalent_areas(existing: Optional[str], proposed: Optional[str]) -> bool:
    left, right = area_of_cover_key(existing), area_of_cover_key(proposed)
    return left is not None and left == right
