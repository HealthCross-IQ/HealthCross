"""Classifies a claim line's MEDICAL_ACT (the specific treatment performed)
into the benefit groupings underwriting actually prices on.

Needed because MEDICAL_CATEGORY is too coarse in one important place: its
"PARAMEDICAL" bucket holds both true physiotherapy and every alternative
therapy on the book, and Portfolio Analysis had been labelling the whole
category "Physiotherapy" even though roughly a third of its value is not
physiotherapy at all.

Two quirks of the real export drive the matching rules below:

  * Alternative therapies are spelled as the source spells them, not as a
    dictionary would. The book's single largest one is "Ayuverdic" - the
    correct spelling (ayurvedic) does not appear anywhere, so a pattern
    written from the correct spelling silently misses it entirely.
  * The same treatment appears in English and French on different rows
    ("Chiropractor"/"Chiropracteur", "Physical Therapist"/
    "Kinesitherapeute"), so both have to be recognised or the split lands
    on the wrong side depending on which language a row happened to use.
"""
import re
import unicodedata
from typing import Optional

#: Matched against a normalized (accent-stripped, lowercased) MEDICAL_ACT.
#: Deliberately substring-based rather than exact: the export carries
#: "Homeopath" and "Homeopathic Treatment" as separate values for the same
#: therapy, and more variants should fold in without a code change.
_ALTERNATIVE_PATTERNS = (
    # "ayuverd" first: that is the book's own spelling. "ayurved" is kept
    # so a corrected export still matches.
    r"ayuverd", r"ayurved",
    r"homeopath", r"homoeopath",
    r"acupunct",
    r"osteopath",
    r"chiroprac",      # covers Chiropractor and Chiropracteur alike
    r"naturopath",
    r"unani", r"siddha", r"reiki",
    r"traditional chinese",
)

#: True physiotherapy and allied rehabilitation - the rest of PARAMEDICAL
#: once alternative therapy is taken out.
_PHYSIOTHERAPY_PATTERNS = (
    r"physical therapist", r"physiotherap",
    r"kinesitherapeute", r"kine",
    r"occupational therapist",
    r"speech therapist", r"orthophon",
    r"podolog", r"podiatr",
)

_ALTERNATIVE_RE = re.compile("|".join(_ALTERNATIVE_PATTERNS))
_PHYSIOTHERAPY_RE = re.compile("|".join(_PHYSIOTHERAPY_PATTERNS))


def _normalize(medical_act: Optional[str]) -> str:
    """Lowercased and accent-stripped, so "Kinésithérapeute" matches the
    plain-ASCII pattern above rather than slipping through unclassified."""
    if not medical_act:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(medical_act))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip().lower()


def is_alternative_treatment(medical_act: Optional[str]) -> bool:
    """Ayurvedic, homeopathy, acupuncture, osteopathy, chiropractic and
    the like - benefits typically written with their own sub-limit, and
    worth pricing separately from the physiotherapy they share a category
    with."""
    return bool(_ALTERNATIVE_RE.search(_normalize(medical_act)))


def is_physiotherapy(medical_act: Optional[str]) -> bool:
    """True physiotherapy and allied rehab (physio, occupational, speech,
    podiatry) - PARAMEDICAL minus the alternative therapies."""
    return bool(_PHYSIOTHERAPY_RE.search(_normalize(medical_act)))


def classify_paramedical(medical_act: Optional[str]) -> str:
    """Splits a PARAMEDICAL claim line into the three things that category
    actually contains: "Alternative Treatment", "Physiotherapy", or
    "Other Paramedical" (nursing, and anything not recognised - left as
    its own bucket rather than folded into physiotherapy, so an
    unrecognised treatment shows up as unclassified instead of quietly
    inflating a real benefit's cost)."""
    if is_alternative_treatment(medical_act):
        return "Alternative Treatment"
    if is_physiotherapy(medical_act):
        return "Physiotherapy"
    return "Other Paramedical"
