"""OCR-based fallback for table-of-benefits PDFs that are scanned images -
no extractable text or table lines for the normal pdfplumber-based parser
(app/ingestion/benefits_pdf.py) to work with at all.

OCR is meaningfully less reliable than the text-based parser: real-world
scans produce misread digits (the same figure can OCR as "29,400" on one
pass and "29,440" on another), garbled table headers, and column content
that bleeds together. Rather than pretend precision this technique doesn't
have, this module:
  - extracts full per-page OCR text (reliable, always returned) for a human
    to search/verify against the source PDF,
  - does best-effort label-anchored value extraction for a curated set of
    fields, returning EVERY distinct value found near a label rather than
    guessing which plan tier/column it belongs to - OCR reading order
    across side-by-side table columns isn't trustworthy enough to assign
    confidently,
  - never silently presents a single number as ground truth when the
    surrounding text is ambiguous.

Always spot-check numbers this module produces against the source document.
"""
import os
import re
import shutil
from typing import Any, BinaryIO, Dict, List, Optional

import pdfplumber
import pytesseract

# pytesseract shells out to a `tesseract` command on PATH by default. The
# official Windows installer doesn't reliably add itself to PATH, and asking
# a non-technical user to edit their Windows PATH by hand is its own source
# of errors - so fall back to Tesseract's own default install locations (or
# an explicit TESSERACT_CMD env var override) before giving up.
_WINDOWS_DEFAULT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


def _configure_tesseract_cmd() -> None:
    if shutil.which("tesseract"):
        return
    env_override = os.environ.get("TESSERACT_CMD")
    for candidate in ([env_override] if env_override else []) + _WINDOWS_DEFAULT_PATHS:
        if candidate and os.path.isfile(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            return


_configure_tesseract_cmd()

# label -> regex (or (regex, window) when the real value sits further away
# than the default 200-char window - e.g. a long parenthetical clarification
# wedged between a Dubai Insurance/iSON Secure-style label and its own
# value) to search for in the flattened OCR text of the whole document.
# A note on the (?=\s+(?:Not\s+)?Covered) lookaheads below. Cigna Global
# Care-style booklets set the benefit table in three columns - label,
# value, clarification - and Tesseract reads such a page across the row,
# not down the column. A label that wraps onto a second line therefore
# arrives split around its own value:
#
#   "19. Prescribed Medicines, Covered We pay for prescribed medications,
#    drugs and Drugs and Dressings Co-pay: Nil dressings by a Medical..."
#
# Matching the full "Prescribed Medicines, Drugs and Dressings" never
# succeeds on that text. Anchoring on the first line of the label and
# requiring the value column to follow it does, and is stricter than
# matching the bare word alone - "Out-patient" appears on nearly every
# page, "Out-patient" immediately followed by "Covered" does not.
_FIELD_LABEL_PATTERNS = {
    # Not a benefit so much as the frame around one: the same cover reads
    # very differently on a restricted network than a comprehensive one,
    # so it belongs next to the limits rather than buried in the case
    # setup. The lookahead keeps this off the prose uses of the word -
    # "In Network", "available within the network" - by requiring an
    # all-caps network name ("COMPREHENSIVE", "RESTRICTED") after it.
    # (?-i:) matters here: everything else in this table is matched case
    # -insensitively, and "eligible provider network" appears a dozen
    # times in the network explainer prose. Only the capitalised label
    # followed by a capitalised network name is the row we want.
    "network": r"Network Type|Network\s*/\s*Provider Tier|(?-i:Provider Network(?=\s+[A-Z])|Network(?=\s+[A-Z]{4,}))",
    # "Plan Annual Maximum" wraps around its own value in the three-column
    # layout ("Plan Annual US$7,500,000 ... Maximum per year of
    # insurance"), so the second alternative anchors on the first line of
    # the label plus the currency that follows it.
    "annual_limit": r"Indemnity Limit|Plan Annual [Ll]imit|Plan Annual\s+Maximum|Plan Annual(?=\s+(?:US\$|USD|AED|\$))",
    # (?!age) keeps this off "area of coverage", which the exclusions
    # section uses in running prose a dozen pages later.
    "area_of_cover": r"Basic Territory for Elective\s*&\s*Emergency treatment|Area of Cover(?!age)",
    # Negative lookbehind excludes the maternity-specific "Outpatient
    # Ante/Post Natal Consultation Deductible / Coinsurance" row, which
    # shares this same wording for a different (and usually differently
    # valued) benefit elsewhere in the document.
    "deductible": (
        r"(?<!Natal )Consultation Deductible\s*/?\s*Coinsurance"
        r"|Out-?patient Co-?insurance\s*/\s*Deductible"
        r"|Out-?patient(?=\s+(?:Not\s+)?Covered)"
    ),
    "pre_existing_chronic_limit": r"Pre-existing conditions|Pre-existing(?=\s+(?:Not\s+)?Covered)",
    # Dubai Insurance/iSON Secure's own label ("Maternity In-patient
    # Services and Complications") is followed by a long parenthetical
    # pre-approval clarification before its own "Covered up to USD ..."
    # value - much further away than this module's default 200-char
    # window reaches, so this one needs the wider window explicitly.
    "maternity_limit": (
        r"Normal Delivery|Maternity In-?patient Services"
        r"|Maternity Benefits\s+(?:Benefit Limit\s+)?Clarifications",
        600,
    ),
    # "Dental Benefit" on its own also hits the contents page and every
    # passing mention in the exclusions; requiring the table header that
    # opens the dental section keeps it on the row that carries a limit.
    "dental": r"Dental Benefits\s+(?:Benefit Limit\s+)?Clarifications|Dental Benefit Limit|Basic Dental",
    "optical": r"Optical Benefit|Vision Benefits\s+(?:Benefit Limit\s+)?Clarifications",
    "coinsurance": (
        r"Outpatient Co-Insurance|(?<!Natal )Consultation Deductible\s*/?\s*Coinsurance"
        # The member-reimbursement claims co-insurance: the percentage the
        # member carries for treatment taken outside the network or on a
        # reimbursement basis. Its label wraps across three lines, so the
        # only reliable anchor is its own first word plus the percentage.
        r"|Member(?:\s+reimbursement)?(?=\s+\d{1,3}%)"
    ),
    # Same label-cluster-then-value-cluster layout as maternity_limit above
    # (Dubai Insurance/iSON Secure's own template lists several benefit
    # labels together, then their values in the same order, sometimes with
    # another whole label cluster wedged in between) - needs the same
    # wider window to actually reach its own value rather than a fallback
    # grab of the next unrelated label's own text.
    "alternative_or_complementary_treatment": (
        r"Alternative Medicine Co-Insurance|Enhanced Alternative Medicine|Alternative and Complementary"
        r"|Complementary and(?=\s+(?:Not\s+)?Covered)",
        600,
    ),
    "pharmacy_limit_and_coinsurance": (
        r"Prescribed Pharmaceuticals|Prescribed Medicines,?(?=\s+(?:Not\s+)?Covered)"
    ),
    "health_screening_wellness": (
        r"Health Check\s*/?\s*Wellness Package|Wellness Package|Health Screening"
        r"|Adult Wellness(?=\s+(?:Not\s+)?Covered)"
        r"|Wellbeing Benefits\s+(?:Benefit Limit\s+)?Clarifications"
    ),
}

# The original pattern only matched amounts with at least one comma group
# (e.g. "5,520,000/-"), silently missing smaller AED amounts with no comma
# at all (e.g. "AED 50/-") - the first alternative below covers those too,
# as long as "AED" is present to distinguish a real amount from a stray
# small number; the second alternative keeps the original comma-grouped
# match for amounts that appear with no "AED" prefix at all.
_CURRENCY = r"(?:AED|USD|US\$|\$)"
_MONEY_RE = re.compile(
    _CURRENCY + r"\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?(?:/-)?|[\d]{1,3}(?:,\d{3})+(?:/-)?"
)
# A co-insurance/deductible value is often "20% up to a maximum of AED
# 50/-" rather than a bare amount - tried first so the rate isn't dropped
# in favor of just the capped AED figure.
_PCT_MONEY_RE = re.compile(r"\d{1,3}%[^%\d]{0,40}?(?:" + _MONEY_RE.pattern + r")")
# Case-sensitive on purpose. A value column writes "Covered" and "Not
# Covered"; the clarification paragraph beside it writes "Elective
# Caesarean Delivery is not covered" and "unless a Dental Plan has also
# been selected". Matching case-insensitively lets a mid-sentence "not
# covered" outrank the benefit's own limit a few words later, and turns a
# US$10,000 maternity benefit into a flat "Not Covered".
_COVERED_RE = re.compile(r"\bNot Covered\b|\bCovered\b|\bPaid in Full\b")
# "Covered up to AED 29,440" is one value, not the word "Covered" followed
# by an unrelated amount. Matched as a unit so the limit isn't lost to the
# flag that introduces it - which is what happens when the earliest
# candidate wins and "Covered" is always the earliest.
#: The trailing alternative picks up the second currency in "USD 137/
#: AED 500" - the same limit stated twice, and dropping either half makes
#: the value look like a different number to whoever reads it next.
_COVERED_UP_TO_RE = re.compile(
    r"\bCovered\s+(?:up to|to a maximum of)\s+(?:" + _MONEY_RE.pattern + r")"
    r"(?:\s*/\s*(?:" + _MONEY_RE.pattern + r"))?"
)
# A bare percentage is a real value ("Member reimbursement 0%") but a weak
# one - a percentage also turns up inside clarification prose - so it is
# only consulted once nothing stronger is in the window.
_PCT_RE = re.compile(r"\d{1,3}%")
# An all-caps run is how network tiers are set ("COMPREHENSIVE"). Anchored
# to the start of the window so it only ever reads the value column, not a
# heading further into the clarification text.
_ALLCAPS_RE = re.compile(r"^(?:[A-Z][A-Z&/+.'-]{2,}\s*){1,3}")
_WORD_RE = re.compile(r"\S+")

# What turns a bare number into a benefit limit. "US$200" and "US$200 per
# year of insurance" are different statements, and dropping the second
# half leaves the reader unable to tell a per-visit cap from an annual
# one; "Co-pay: Nil" is likewise part of the value, not a footnote.
_PER_PERIOD_RE = re.compile(
    r"per (?:year of insurance|policy year|year|annum|person|member|visit|treatment|lifetime)"
    r"(?: of insurance)?",
    re.IGNORECASE,
)
_COPAY_RE = re.compile(
    r"Co-?pay:?\s*(?:Nil|None|\d{1,3}%|" + _CURRENCY + r"\s?[\d,]+)"
    r"|\d{1,3}%\s*Co-?Pay",
    re.IGNORECASE,
)
#: How far past a value to keep looking for the qualifier that belongs to
#: it. Wide enough to cross the line break a wrapped value column puts
#: between "US$200" and "per year of insurance", short enough not to
#: annex the next row's own wording.
_QUALIFIER_REACH = 120


#: Below this share of readable characters, an "extractable" page is not
#: worth reading. A page of Private Use Area codepoints extracts as
#: thousands of characters and contains no words - see is_scanned_pdf.
_MIN_READABLE_SHARE = 0.35


def _readable_share(text: str) -> float:
    """How much of this text is characters a parser can actually match on.

    Private Use Area codepoints (U+E000-U+F8FF) are the giveaway: a PDF
    built from a subsetted font with no ToUnicode map extracts its glyphs
    as PUA, which is text by every mechanical test and unreadable by any
    useful one.
    """
    stripped = "".join(text.split())
    if not stripped:
        return 0.0
    readable = sum(
        1 for ch in stripped
        if ch.isprintable() and ord(ch) < 0x2000 and not (0xE000 <= ord(ch) <= 0xF8FF)
    )
    return readable / len(stripped)


def is_scanned_pdf(file: BinaryIO) -> bool:
    """True if the PDF has no text worth reading - so the caller should
    OCR it rather than parse its text layer.

    The question is deliberately "is the text usable?", not "is there
    text?". Those differ on a whole class of real documents, and the
    difference is silent: a table of benefits built from a subsetted font
    with no ToUnicode map extracts thousands of Private Use Area
    codepoints per page. Every mechanical test says it has text, so OCR
    never runs, the parser matches nothing against gibberish, and the
    result is a plan whose every field reads "not specified in source
    document" - which looks like a document that omitted them rather than
    one that was never read.

    A page mixing a little PUA with real text (an icon font, a bullet
    glyph) stays on the text path; only a page that is mostly unreadable
    is treated as needing OCR.
    """
    file.seek(0)
    try:
        with pdfplumber.open(file) as pdf:
            pages = pdf.pages[:5]
            has_readable_text = any(
                _readable_share(page.extract_text() or "") >= _MIN_READABLE_SHARE
                and (page.extract_text() or "").strip()
                for page in pages
            )
    finally:
        file.seek(0)
    return not has_readable_text


def _render_pages_with_pdfium(file: BinaryIO, resolution: int) -> Optional[List]:
    """Page images via pdfium, or None if it isn't available.

    Preferred over pdfplumber's own rasteriser because the two disagree on
    documents built from subsetted fonts: pdfium reproduces the page as it
    appears on screen, while pdfplumber's renderer produces a mangled
    image that OCR then faithfully transcribes as nonsense. The failure
    looks like bad OCR - "ccecceecseeeeee" where the page plainly reads
    "Chronic Conditions" - so it gets blamed on Tesseract rather than on
    the picture Tesseract was handed.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None

    file.seek(0)
    try:
        document = pdfium.PdfDocument(file.read())
        return [page.render(scale=resolution / 72).to_pil() for page in document]
    except Exception:
        return None
    finally:
        file.seek(0)


def ocr_pdf_pages(file: BinaryIO, resolution: int = 300) -> List[str]:
    """Returns OCR'd text for each page, in page order.

    OCR is only ever as good as the image it is given, and rendering is
    where this quietly goes wrong on real documents - see
    _render_pages_with_pdfium. pdfplumber's rasteriser stays as the
    fallback so an environment without pdfium still works, just less well.
    """
    images = _render_pages_with_pdfium(file, resolution)
    if images is not None:
        return [pytesseract.image_to_string(image) for image in images]

    file.seek(0)
    pages_text = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            image = page.to_image(resolution=resolution).original
            pages_text.append(pytesseract.image_to_string(image))
    return pages_text


#: Tick boxes, rule lines and section markers have no letters to read, so
#: Tesseract invents some: a checked box next to "Area I" comes back as
#: "sd" or "aooome". They are recognisable without knowing the document -
#: a run of one letter three times over, or a lower-case word with no
#: vowel in it - and they only ever appear in the free-text fallback,
#: where they crowd out the words that carry the actual answer.
_REPEATED_LETTER_RE = re.compile(r"(.)\1{2,}")
_VOWEL_RE = re.compile(r"[aeiouy]")


def _is_noise_token(token: str) -> bool:
    word = token.strip(".,;:()[]")
    if not word or not any(ch.islower() for ch in word):
        return False  # all-caps and digits are read as written - "III", "USA", "0%"
    if _REPEATED_LETTER_RE.search(word):
        return True
    return word.isalpha() and not _VOWEL_RE.search(word.lower())


def _nearby_text_value(window_text: str, max_words: int = 8) -> Optional[str]:
    """Fallback for a nearby value that's neither a currency amount nor a
    Covered/Not Covered flag - e.g. area of cover's "Worldwide Exc (USA)".
    Two side-by-side plan columns sharing the same value (common when a
    benefit doesn't differ between tiers) OCRs as that phrase repeated
    twice back-to-back, so this collapses an exact immediate repeat down
    to a single copy rather than reporting the duplicate.
    """
    words = [w for w in _WORD_RE.findall(window_text) if not _is_noise_token(w)][:max_words]
    if not words:
        return None
    for split in range(1, len(words)):
        first_half = " ".join(words[:split])
        second_half = " ".join(words[split : split * 2])
        if second_half and first_half == second_half:
            return first_half
    return " ".join(words)


#: A contents page names every benefit in the document and carries a value
#: for none of them, so it is where label-anchored extraction goes wrong
#: first: "Area of Cover" matches its own contents entry before it ever
#: reaches page 6, and the "value" that comes back is the dot leader and
#: the page number. Tesseract renders those leaders as dots on some lines
#: and as runs of stray letters on others, so this looks for the dotted
#: ones and drops the whole page on the strength of them.
_DOT_LEADER_RE = re.compile(r"\.{4,}")
_CONTENTS_HEADING_RE = re.compile(r"^\s*(?:TABLE OF )?CONTENTS\s*$|^\s*INDEX\s*$", re.IGNORECASE)
_MIN_DOT_LEADER_LINES = 3


def _is_contents_page(page_text: str) -> bool:
    lines = page_text.splitlines()
    if sum(1 for line in lines if _DOT_LEADER_RE.search(line)) >= _MIN_DOT_LEADER_LINES:
        return True
    return any(_CONTENTS_HEADING_RE.match(line) for line in lines[:3])


def _with_qualifiers(value: str, rest: str) -> str:
    """value plus the period and co-pay wording that qualifies it."""
    reach = rest[:_QUALIFIER_REACH]
    parts = [value]
    period = _PER_PERIOD_RE.search(reach)
    if period:
        parts.append(period.group(0))
    copay = _COPAY_RE.search(reach)
    if copay:
        parts.append(copay.group(0))
    return " ".join(parts)


class _ShiftedMatch:
    """A match found in a sliced window, reported against the whole one.

    The all-caps value regex is anchored, so it has to run against the
    window with its leading space removed; ranking it against the other
    candidates then needs its offsets back in the original coordinates.
    """

    def __init__(self, match: "re.Match", offset: int) -> None:
        self._match = match
        self._offset = offset

    def start(self) -> int:
        return self._match.start() + self._offset

    def end(self) -> int:
        return self._match.end() + self._offset

    def group(self, index: int = 0) -> str:
        return self._match.group(index)


def _value_from_window(window_text: str) -> Optional[str]:
    """The one value this label carries, out of the text that follows it.

    Deliberately one value, not every number in the window: past the value
    column sits a clarification paragraph, and it is full of numbers that
    belong to other things - exclusion thresholds, note references, the
    limits of adjacent benefits. Taking the earliest candidate keeps to
    the value column; taking the longest of the ones that start together
    prefers "20% up to a maximum of AED 50/-" over the bare "20%".
    """
    candidates = [
        match
        for match in (
            regex.search(window_text)
            for regex in (_COVERED_UP_TO_RE, _PCT_MONEY_RE, _MONEY_RE, _COVERED_RE, _PCT_RE)
        )
        if match
    ]
    # An all-caps run only counts where the value column is - immediately
    # after the label - so it ranks by that position like everything else
    # rather than jumping the queue or waiting behind it. "Network
    # COMPREHENSIVE ... Member 0%" has to read COMPREHENSIVE, and
    # "Member 0% ... Pre-existing Covered" has to read 0%; both fall out
    # of ranking on where the value sits, not on which kind it is.
    leading = len(window_text) - len(window_text.lstrip())
    all_caps = _ALLCAPS_RE.match(window_text[leading:])
    if all_caps and len(all_caps.group(0).strip()) >= 3:
        candidates.append(_ShiftedMatch(all_caps, leading))

    if candidates:
        best = min(candidates, key=lambda m: (m.start(), -len(m.group(0).strip())))
        return _with_qualifiers(best.group(0).strip(), window_text[best.end() :])

    areas = _area_options(window_text)
    if areas:
        return areas

    return _nearby_text_value(window_text)


#: Area of cover is the one standard field a booklet routinely states as
#: a tick against a list rather than as words. The tick is a box glyph
#: with no text in it, so no amount of OCR tuning will say which area was
#: chosen - the honest answer is the list of areas the document offers,
#: plus why the choice isn't in it. Roman numerals are allowed to OCR as
#: pipes and lower-case Ls, which is what they usually come back as.
_AREA_OPTION_RE = re.compile(r"Area\s+(?:[IVXlt|]{1,4})\s*:?\s*((?:(?!Area\b)[A-Za-z ()]){3,40})")
_MIN_AREA_OPTIONS = 2
_AREA_TICKBOX_NOTE = "selected area is a tick box - read it off the source PDF"


def _area_options(window_text: str) -> Optional[str]:
    options = [
        " ".join(w for w in m.group(0).split() if not _is_noise_token(w))
        for m in _AREA_OPTION_RE.finditer(window_text)
    ]
    seen: List[str] = []
    for option in options:
        if option not in seen:
            seen.append(option)
    if len(seen) < _MIN_AREA_OPTIONS:
        return None
    return " | ".join(seen) + f" ({_AREA_TICKBOX_NOTE})"


def _nearby_values(flat_text: str, label_pattern: str, window: int = 200) -> List[str]:
    values: List[str] = []
    for match in re.finditer(label_pattern, flat_text, re.IGNORECASE):
        value = _value_from_window(flat_text[match.end() : match.end() + window])
        if value:
            values.append(value)

    seen = set()
    unique = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return _collapse_partial_readings(unique)


def _collapse_partial_readings(values: List[str]) -> List[str]:
    """Drop a value that is only a shorter reading of another one.

    The same benefit row is often matched twice - once where the label
    sits beside its full value ("Covered Co-pay: Nil") and once where a
    wrapped line put the qualifier out of reach ("Covered"). Reporting
    both would flag the field as contradictory and send the underwriter
    to the source PDF over a disagreement that isn't one.
    """
    return [
        value
        for value in values
        if not any(other != value and other.startswith(value) for other in values)
    ]


def build_ocr_benefit_summary(pages_text: List[str]) -> Dict[str, str]:
    """Best-effort standard-field summary from OCR'd text.

    A field with exactly one distinct value nearby is reported as-is. A
    field with multiple distinct values is reported as all of them,
    joined, with an explicit note that column order couldn't be trusted -
    the caller should treat that as "needs manual verification", not as a
    confident multi-tier breakdown.
    """
    readable = [page for page in pages_text if not _is_contents_page(page)]
    flat = " ".join(" ".join(page.split()) for page in readable or pages_text)
    summary: Dict[str, str] = {}
    for field, spec in _FIELD_LABEL_PATTERNS.items():
        pattern, window = spec if isinstance(spec, tuple) else (spec, 200)
        values = _nearby_values(flat, pattern, window=window)
        if not values:
            continue
        if len(values) == 1:
            summary[field] = values[0]
        else:
            summary[field] = (
                " / ".join(values) + " (multiple values found near this label - verify against the source PDF)"
            )
    return summary


def parse_benefits_pdf_ocr(file: BinaryIO, filename: str) -> Dict[str, Any]:
    pages_text = ocr_pdf_pages(file)
    return {
        "summary": build_ocr_benefit_summary(pages_text),
        "raw_ocr_text": "\n\n".join(f"--- page {i + 1} ---\n{text}" for i, text in enumerate(pages_text)),
    }
