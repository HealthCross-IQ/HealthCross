# HealthCross Underwriting Intelligence

A risk-scoring service for the SME medical insurance quote portal. Brokers
attach a census, a table of benefits, and (sometimes) claims history for each
submission; this service ingests those files, computes a 0-100 risk
scorecard with a tier and suggested loading, and recalibrates itself over
time from recorded case outcomes.

## Why it's structured this way

A scorecard is a weighted blend of four components:

| Component | Driven by | Source |
|---|---|---|
| Demographic & group composition | census (age, gender, marital status, relation, nationality) | `app/scoring/rules/demographic.py` |
| Claims experience | claims history, credibility-weighted by group size | `app/scoring/rules/claims_experience.py` |
| Benefit richness | table of benefits (limits, deductible, network, riders) | `app/scoring/rules/benefit_richness.py` |
| Industry | declared industry | `app/scoring/rules/industry.py` |

`app/scoring/engine.py` combines them with a `ScoringWeightSet` (versioned,
one active at a time) into a 0-100 composite score, a risk tier (Preferred /
Standard / Substandard / Decline-Refer), and a suggested premium loading.
Every scorecard stores its full component breakdown in `details` for
underwriter transparency - nothing is a black box.

## The demographic rules

These encode explicit underwriting judgment, not statistical fits, since
they were specified directly:

- **Age bands**: 0-17, 18-40, 41-59, 60-69, 70-99, each with its own base
  risk multiplier.
- **Maternity risk**: a married female aged 18-40 gets a risk loading,
  whether she's the employee or a dependent spouse.
- **Female spouse loading**: a female spouse carries a higher loading than a
  male spouse.
- **Children are favorable, except infants**: a dependent child older than 1
  year gets a favorable discount; a child aged 1 or under (newborn/delivery
  exposure) gets a loading instead.
- **Male employees are individually favorable**, reinforcing the group-level
  effect below.
- **Group favorability**: a larger group, and a higher male ratio among
  employees, both discount the composite score (a bigger, male-skewed group
  is a "good account").

All of these constants live at the top of `demographic.py`, named and
documented, specifically so they're easy for an underwriter to tune without
touching the surrounding logic.

## Nationality zones - and why they're *learned*, not asserted

Nationalities are grouped into four zones (`app/reference/nationality_zones.py`):

- Zone 1 - Asia
- Zone 2 - Middle East (incl. North Africa, per broker convention)
- Zone 3 - Europe & Americas
- Zone 4 - Other (mainly Sub-Saharan Africa in the sample data, plus
  anything unmapped - nothing silently disappears)

Unlike the demographic rules above, nobody asserted which zone should carry
more risk or by how much. So each zone's multiplier starts neutral (1.0) on
the `ScoringWeightSet` and is the main thing the feedback loop is meant to
*learn* as real outcomes accumulate - see below.

## The feedback / learning loop

1. `POST /cases/{id}/outcome` records what actually happened to a scored
   case: was it bound, what was the final premium, and (once known) the
   actual loss ratio. A loss ratio at or below 85% is labeled "profitable".
2. `POST /admin/recalibrate` (`app/feedback/recalibration.py`) fits two
   logistic regressions against all recorded outcomes:
   - one over the four component risk scores, to re-weight
     demographic/claims/benefits/industry importance;
   - one over each case's nationality-zone mix, to nudge the zone
     multipliers toward whichever zones actually predict profitability.

   Both require at least 20 outcomes with both profitable and unprofitable
   examples, and cap how far a single run can move any parameter (15% for
   weights, 10% for zone multipliers) so one batch of outcomes can't
   whipsaw the scorecard. Every recalibration creates a new, versioned
   `ScoringWeightSet` - nothing is overwritten, so you can always see (or
   revert to) what the model looked like before.

## Standing analysis standards

These are the fixed defaults for reviewing a submission - every case is
analyzed on its own terms and not benchmarked against other cases.

**Table of benefits summary** (`app/scoring/rules/benefits_summary.py`) - any
plan, from any insurer, gets summarized in the same fixed 10-field layout:
Area of Cover, Annual Limit, Deductible, Pre-existing & Chronic Limit,
Maternity Limit, Dental, Optical, Coinsurance, Alternative/Complementary
Treatment, Pharmacy Limit & Coinsurance. A field the source document doesn't
specify is shown as "Not specified" rather than silently dropped.
`GET /cases/{id}/benefits-summary` renders this for every uploaded plan/tier.

**Quote vs. existing-plan comparison** - a case can hold both the existing/
incumbent table of benefits (uploaded via `/benefits`) and a new insurer's
quotation (uploaded via `/quote`) at the same time, tagged internally as
`role="existing"` / `role="quoted"` on the same `BenefitPlan` table so
neither upload deletes the other. This is a same-case comparison (the new
quote against what this specific client already has), not the cross-case
benchmarking the standing standards above deliberately avoid.
- `app/ingestion/quote_pdf.py` targets the QIC/HealthCROSS Global
  "Full Category Premium Calculation" quote layout: a category premium
  table (Category / Members / Plan / Network / Gross Premium) plus a
  series of tables where the benefit label is the table's own first
  column and the remaining columns are one per quoted category.
- `app/scoring/rules/benefits_comparison.py` compares the two plans'
  standard 10-field summaries. A numeric direction (Improved/Reduced/Same,
  with a % change) is only given when both sides parse as a currency
  amount - USD converts to AED at the fixed 3.6725 peg. Anything that
  doesn't parse this way is flagged "Review" rather than guessed.
- `GET /cases/{id}/premium-by-category` - members, network, gross premium,
  and premium/member for each quoted category, plus a blended total.
- `GET /cases/{id}/benefits-comparison` - the existing vs. quoted plans,
  paired by position, compared field by field.
- Only the existing-role plan feeds the risk scorecard's benefit-richness
  component - a quoted plan uploaded for comparison never changes the
  score of the case as submitted.

**Claims projection - burning cost method** (`app/scoring/rules/claims_projection.py`) -
`project_annual_claims()` runs the agreed formula:
1. Average the first 6 *full* months of paid claims (excluding any partial
   stub month at policy inception).
2. Annualize (x12), then add a flat 10% IBNR load.
3. Divide by the average of the claims report's opening and closing member
   counts to get an annual burning cost per member.
4. Multiply by the *current* census member count for this submission.
5. Apply 7.5% inflation, then 90% credibility, then gross up for a 28%
   commission/OPEX loading via `/ (1 - 0.28)` (not a multiplicative add-on).

All five percentages are keyword defaults on `ClaimsProjectionAssumptions`,
overridable per call rather than hardcoded. `GET /cases/{id}/claims-projection`
runs this against the case's latest uploaded claims report and current
census count.

**Diagnosis exposure classification** (`app/reference/diagnosis_classification.py`) -
every top-N diagnosis grouping from a claims report gets tagged chronic /
non-chronic / mixed, plus a `high_exposure` flag for cancer, heart disease,
and kidney/genitourinary conditions regardless of current claim volume.
`flag_diagnosis_group()` also flags an in-patient-claim average above AED
30,000 as a probable large/shock claim, and below AED 1,000 as a likely
day-case-miscoded-as-in-patient data artifact.
`GET /cases/{id}/diagnosis-exposure` returns this, sorted by value.

## PDF ingestion

Two insurer document formats are parsed directly, no manual conversion to
Excel needed - both `POST /cases/{id}/benefits` and `POST /cases/{id}/claims`
auto-detect a `.pdf` upload and dispatch to these instead of the generic
spreadsheet parsers:

- **Claims report** (`app/ingestion/claims_report.py`) - two known insurer
  layouts, auto-detected by a distinguishing marker in the text, each with
  its own parser rather than one trying to handle both:
  - *Format 1*: the DHA (Dubai Health Authority) Mandated Format, a
    regulatory template with fixed row numbering. Parsed from plain
    extracted text.
  - *Format 2*: a different insurer's own "Health Insurance Claims Record"
    layout (different date format, different row numbers, a
    Male/Single-female/Married-female population split instead of
    Male/Female). This one is parsed from `find_tables()`'s bordered-table
    structure rather than plain text - this document's text reading order
    badly scrambles some multi-line-wrapped row labels (a label can appear
    split before *and* after its row's numbers), while the underlying PDF
    table lines give clean, correctly-ordered cells regardless.

  Both extract policy dates, opening/closing population, the
  diagnosis/provider breakdowns, and the monthly-paid trend (auto-flagging
  a policy-inception stub month as partial), stored as `ClaimsReport` -
  distinct from the per-claim `ClaimsRecord` rows a spreadsheet upload
  produces.
- **Table of benefits** (`app/ingestion/benefits_pdf.py`) - targets insurer
  guides shaped like Bupa Global/Sukoon's "Business Health Plan": a benefit
  label in the page's left margin next to a bordered table with one column
  per plan tier. Recovers each row's label by cropping the page to the
  left-margin region at that row's vertical position (via pdfplumber's
  `find_tables()` + row bounding boxes) - a positional technique, not
  fragile text-matching, so it holds up as long as the same
  label-column-next-to-bordered-table layout is used, regardless of tier
  names or insurer. Produces the standard 10-field summary directly, for
  every tier found, in one pass.
- **Scanned (image-only) table of benefits** (`app/ingestion/benefits_ocr.py`) -
  when a PDF has no extractable text at all (a raster scan, not a real PDF
  table), `/benefits` automatically falls back to OCR (pdfplumber renders
  each page, `pytesseract` reads it - needs the `tesseract-ocr` system
  package installed, not just the pip package). OCR is meaningfully less
  reliable than the text-based parsers: real scans misread digits (the same
  figure can come out as "29,400" on one pass and "29,440" on another) and
  garble table structure, so this module never silently picks one answer
  when a label has multiple nearby candidate values - it reports all of
  them with an explicit "verify against the source PDF" note, and keeps the
  full per-page OCR text (`raw_ocr_text`) for manual lookup. Expect it to
  take significantly longer than a text-based PDF (tens of seconds for a
  10+ page scan). On Windows, the official installer doesn't reliably add
  itself to PATH, so `app/ingestion/benefits_ocr.py` also checks Tesseract's
  own default install directories automatically before giving up; an
  atypical install location can be pointed to with a `TESSERACT_CMD` env var.

## Web UI

Opening the server's root URL (`http://127.0.0.1:8000/`) serves a small
self-contained single-page UI (`app/static/index.html` - no build step, no
JS framework, no external requests): create a case, upload census/benefits/
claims/quote files, compute the scorecard, and record an outcome, all by
clicking around instead of using `/docs`. It's a thin client over the same
API below.

## API

- `GET /cases` - list all cases
- `POST /cases` - create a case (broker, company, industry, region)
- `POST /cases/{id}/census` - upload the census (xlsx/csv)
- `POST /cases/{id}/benefits` - upload the existing/incumbent table of benefits (xlsx/csv/pdf)
- `POST /cases/{id}/quote` - upload a new insurer's quotation for comparison (pdf)
- `POST /cases/{id}/claims` - upload claims history (xlsx/csv, optional)
- `POST /cases/{id}/plan-details` - upload the broker's "CLIENT & PLAN
  details" sheet to populate broker/industry/existing-insurer/target-premium
  metadata on the case
- `POST /cases/{id}/score` - compute and store a scorecard
- `GET /cases/{id}/scorecard` / `/scorecards` - latest / full history
- `POST /cases/{id}/outcome` - record what actually happened
- `GET /cases/{id}/census-summary` - demographic breakdown of the uploaded census (age bands, gender, marital status, relation, nationality-zone mix, married-female/maternity-risk/infant counts) as counts and percentages, with a Male/Female split on top of the age-band/relation/marital-status counts so a gender-skewed data gap (e.g. marital status only recorded for one gender on the source census) is visible rather than blended away
- `GET /cases/{id}/benefits-summary` - every uploaded existing/incumbent plan/tier in the standard 10-field format
- `GET /cases/{id}/premium-by-category` - the uploaded quote's per-category members, network, gross premium, and premium/member, plus a blended total
- `GET /cases/{id}/benefits-comparison` - existing plan(s) vs. quoted plan(s), compared field by field
- `GET /cases/{id}/claims-report` - the latest parsed claims report
- `GET /cases/{id}/claims-projection` - the burning-cost annual claims projection
- `GET /cases/{id}/diagnosis-exposure` - chronic/high-exposure-flagged diagnosis breakdown
- `GET /admin/weights` - full version history of scoring weight sets
- `POST /admin/recalibrate` - trigger recalibration from recorded outcomes

## Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Interactive docs at `http://127.0.0.1:8000/docs`. Data persists to a local
SQLite file (`underwriting.db`) by default; set `DATABASE_URL` to point
elsewhere.

On startup, `app/db_migrate.py` automatically adds any columns a model has
gained since your database file was created (SQLAlchemy's `create_all()`
only creates brand-new tables, it never alters an existing one - without
this, updating the app while keeping your old `underwriting.db` would fail
with "no such column" errors instead of just working). Existing case data
is preserved; this isn't a substitute for a real migration tool on a
multi-user/production database, but it's enough for a single-file SQLite
dev database.

Scanned (image-only) table-of-benefits PDFs need the `tesseract-ocr` system
package too - `pip install` alone won't provide it:
```bash
sudo apt-get install tesseract-ocr   # Debian/Ubuntu
brew install tesseract                # macOS
```
Everything else works without it; only the OCR fallback needs it.

## Tests

```bash
pytest
```

Covers nationality-zone classification, census/benefits/claims/plan-details
parsing (including the broker's real "Member List" / "CLIENT & PLAN
details" template layout), every demographic rule above in isolation, the
combined scoring engine, the recalibration logistic regressions, and a full
create-case → upload → score → record-outcome → recalibrate flow through
the API.

## Known limitations / next steps

- **PDF parsers target two specific, real document families** (the DHA
  Mandated claims report, and Bupa Global/Sukoon-style tier-table benefit
  guides) - both validated against real documents, but a differently laid
  out insurer PDF may need its own anchor/label tuning rather than working
  automatically. The generic spreadsheet parsers remain the fallback for
  anything else.
- **Zone multiplier learning needs volume**: with only a handful of bound
  cases, `/admin/recalibrate` will correctly refuse to move the zone
  multipliers. The zones only start meaningfully diverging from 1.0 once
  enough outcomes (20+, ideally far more) have been recorded.
- **No authentication/authorization layer** - this is a scoring engine
  service, meant to sit behind the existing portal's auth, not to be
  internet-facing on its own.
