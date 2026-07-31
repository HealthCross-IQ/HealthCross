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
  is a "good account"). This is a continuous discount, capped at 10% and
  reached at 500 employees (`GROUP_SIZE_SCALE`).
- **Small-group loading**: below 50 employees (`SMALL_GROUP_THRESHOLD`), a
  distinct loading is layered on *top* of (not instead of) the discount
  above - a small group's claims pool is far less predictable than a large
  one, so a 5-employee group should be priced up, not just miss out on the
  large-group discount. The loading phases linearly from 15%
  (`SMALL_GROUP_LOADING_CAP`) at the smallest sizes down to 0% at 50
  employees; at 50+ employees only the group-favorability discount above
  applies.

All of these constants live at the top of `demographic.py`, named and
documented, specifically so they're easy for an underwriter to tune without
touching the surrounding logic.

## Nationality zones - and why they're *learned*, not asserted

Nationalities are grouped into three zones (`app/reference/nationality_zones.py`):

- Zone 1 - Asia
- Zone 2 - Middle East (incl. North Africa, Sub-Saharan Africa, and
  anything else unmapped, per broker convention - nothing silently
  disappears, and there's no 4th zone to catch it separately)
- Zone 3 - Europe & Americas

`ScoringWeightSet` still carries a legacy `zone_4_other_multiplier` column
from when there was a 4th zone, kept only so old recorded weight-set
history stays readable - `classify_zone()` never produces it for new data,
it's excluded from `ALL_ZONES`, and `/admin/recalibrate` carries it forward
unchanged rather than actively recalibrating it.

Unlike the demographic rules above, nobody asserted which zone should carry
more risk or by how much. So each zone's multiplier starts neutral (1.0) on
the `ScoringWeightSet` and is the main thing the feedback loop is meant to
*learn* as real outcomes accumulate - see below.

### Zone interaction effects (also learned, not asserted)

Real-world underwriting intuition ("married Arab women on a Platinum network
are higher risk"; "Europeans/Americans have babies later, in their 30s,
vs. mid-20s in the Middle East") is exactly the kind of pattern this system
is built to *confirm from outcomes* rather than bake in as a fixed rule -
the same philosophy as the zone multipliers above, extended to two
interaction effects:

- **Zone x maternity** (`zone_*_maternity_multiplier` on `ScoringWeightSet`):
  an extra multiplier applied only to members who already trigger the flat
  `MATERNITY_LOADING` (married female, 18-40), scaled by their nationality
  zone. Lets the model learn if e.g. Zone 2 (Middle East) maternity exposure
  carries materially different risk than Zone 3 (Europe/Americas) maternity
  exposure, rather than assuming they're equal.
- **Zone x network tier** (`zone_*_network_multiplier` on
  `ScoringWeightSet`): an extra multiplier applied to every member of a
  zone, scaled by how rich/expensive the case's benefit plan network is.
  `app/reference/network_tiers.py` maps a free-text network name (e.g.
  "MSH Platinum", "Comprehensive+", "Essential") to a 0-1 richness score by
  keyword match - deliberately separate from `benefit_richness.py`'s own
  `NETWORK_MULTIPLIER`, which only recognizes the 3 canonical
  in_country/regional/worldwide values set by the generic spreadsheet
  parser, not real insurer marketing tier names. `compute_scorecard()`
  blends this per-plan score, weighted by member count, into a single
  case-level `network_tier_score` and applies it as
  `1 + (zone_network_multiplier - 1) * network_tier_score` - so on a cheap
  network the effect is muted toward neutral, and on a Platinum-tier
  network it applies in full.

Both sets of multipliers start neutral (1.0) and are recalibrated the same
way as the plain zone multipliers - see below.

## The feedback / learning loop

1. `POST /cases/{id}/outcome` records what actually happened to a scored
   case: was it bound, what was the final premium, and (once known) the
   actual loss ratio. A loss ratio at or below 85% is labeled "profitable".
2. `POST /admin/recalibrate` (`app/feedback/recalibration.py`) fits four
   logistic regressions against all recorded outcomes:
   - one over the four component risk scores, to re-weight
     demographic/claims/benefits/industry importance;
   - one over each case's nationality-zone mix, to nudge the zone
     multipliers toward whichever zones actually predict profitability;
   - one over each case's zone-maternity mix (fraction of members who are
     both maternity-risk and in each zone), to nudge the zone-maternity
     interaction multipliers;
   - one over each case's zone-network mix (each zone's fraction of members
     times the case's `network_tier_score`), to nudge the zone-network
     interaction multipliers.

   All four require at least 20 outcomes with both profitable and
   unprofitable examples, and cap how far a single run can move any
   parameter (15% for weights, 10% for zone-family multipliers) so one
   batch of outcomes can't whipsaw the scorecard. Every recalibration
   creates a new, versioned `ScoringWeightSet` - nothing is overwritten, so
   you can always see (or revert to) what the model looked like before.

## Standing analysis standards

These are the fixed defaults for reviewing a submission - every case is
analyzed on its own terms and not benchmarked against other cases.

**Table of benefits summary** (`app/scoring/rules/benefits_summary.py`) - any
plan, from any insurer, gets summarized in the same fixed 11-field layout:
Area of Cover, Annual Limit, Deductible, Pre-existing & Chronic Limit,
Maternity Limit, Dental, Optical, Coinsurance, Alternative/Complementary
Treatment, Pharmacy Limit & Coinsurance, Health Screening/Wellness Package.
A field the source document doesn't specify is shown as "Not specified"
rather than silently dropped. `GET /cases/{id}/benefits-summary` renders
this for every uploaded plan/tier.

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
  standard 11-field summaries, plus network. A numeric direction
  (Improved/Reduced/Same, with a % change) is only given when both sides
  parse as a currency amount - USD converts to AED at the fixed 3.6725
  peg. Anything that doesn't parse this way (including network, which is
  never a currency amount) is flagged "Review" rather than guessed. In the
  UI, the quoted side of this comparison is labeled "HealthCross Quote"
  rather than the generic "Quoted".
- `GET /cases/{id}/premium-by-category` - members, network, gross premium,
  and premium/member for each quoted category, plus a blended total.
- `GET /cases/{id}/benefits-comparison` - the existing vs. quoted plans,
  paired by position (1st existing vs 1st quoted category, etc.), compared
  field by field. If one side has fewer plans than the other - most
  commonly a scanned/OCR'd existing plan, which only ever produces ONE
  combined entry regardless of how many categories the source document
  actually has - the shorter side's last plan is reused for the extra
  categories rather than comparing against nothing, and
  `existing_plan_reused` flags this so it's never silently misread as a
  distinct Category 2/B entry.
- Only the existing-role plan feeds the risk scorecard's benefit-richness
  component - a quoted plan uploaded for comparison never changes the
  score of the case as submitted.
- **One file per category** - some insurers ship each category's table of
  benefits as its own separate document rather than one combined file.
  `POST /cases/{id}/benefits?mode=append` keeps other categories' plans
  intact and only replaces a plan sharing the uploaded file's category
  letter, instead of the default `mode=replace` behavior (wiping every
  existing-role plan before adding the new file's). Upload each category's
  file in turn with `mode=append` to build up the full set. A plan with no
  detected category (e.g. the OCR/text-scan fallbacks) can't be matched
  this way and is just added alongside whatever's already there.

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

## New business vs. existing business (renewals)

`Case.business_type` ("new" or "existing") distinguishes the two situations
a submission can be in, since a renewal has real data a fresh quote doesn't:

- **New business** uses the claims-projection burning-cost method above,
  built to rescale a claims REPORT's population-level experience (opening/
  closing member counts from a DHA-style report) onto whatever the current
  census's member count is - appropriate when the group being quoted isn't
  the same population the report was drawn from.
- **Existing business** is a renewal for the *same* group: no rescaling is
  needed, because the claims ledger and current premium both belong to the
  exact group being priced. `Case.current_annual_premium` holds the
  expiring/current premium (distinct from `target_premium`, a broker's
  target for a new quote), set via `PATCH /cases/{id}`.

**Claims ledger** (`app/ingestion/claims_ledger.py`, `POST /cases/{id}/claims-ledger`) -
a raw per-claim-line export (e.g. the "ServicePlan" format: PATIENT_ID,
CLAIM_ID, DATE_OF_TREATMENT, DIAGNOSIS_CODE, "Final Amount in AED", etc.),
stored as `ClaimsLedgerEntry` rows - distinct from both the generic
`ClaimsRecord` spreadsheet and the pre-aggregated `ClaimsReport`. Both
"Paid Claims" and "Outstanding Claims" status rows count toward totals,
since outstanding claims are already-incurred cost, not a speculative
estimate.

`app/scoring/rules/claims_ledger_analysis.py` computes:
- Top 10 patients, top 10 diagnoses, and top 10 medical providers by final
  claims amount (`GET /cases/{id}/claims-ledger-analysis`). Each top patient
  also gets a `member_status` of "Active" or "Deleted": a real ledger
  carries two distinct pairs of dates - the scheme's own fixed
  `policy_start_date`/`policy_end_date` (the same on every row) and each
  individual member's own `member_start_date`/`member_end_date` (which
  falls short of the scheme's if they joined late or left early). A
  patient's own `member_end_date` (the latest across their claim lines)
  is compared directly against the scheme's `policy_end_date` - matching
  or later means still active, earlier means they left the scheme before
  its term ended. "Unknown" when either date is missing so there's
  nothing to compare. Diagnoses are classified chronic/non-chronic via
  their ICD-10 chapter
  (`app/reference/icd10_chapters.py` maps a raw code like "J454" to the
  same broad chapter labels `diagnosis_classification.py` already uses,
  extended to cover chapters a DHA report's own pre-aggregated groupings
  never surfaced - mental/behavioural, skin, blood, pregnancy, injury,
  infectious, and administrative/Z-code chapters). Providers need a
  `provider_name` column in the source ledger (several aliases recognized,
  e.g. "Provider Name", "Hospital/Clinic") - omitted entirely if the ledger
  doesn't carry one, rather than guessing.
- A month-wise claims trend (by treatment date), averaging only the FULL
  months - the first month is dropped if the policy didn't start on the
  1st, and the last month present is always dropped as a trailing partial
  (a ledger export is "as of" some date mid-month, not a guaranteed-complete
  month).
- An `expected_annual_premium`: the average full month annualized (x12),
  trended for inflation, then grossed up for the commission/OPEX loading
  via `/ (1 - loading)` - the same convention as the burning-cost method,
  just without its member-count rescaling step. `inflation_pct`/
  `loading_pct` are overridable per call (defaults 7.5%/28%).
- A **category-wise burning cost breakdown** (`category_burning_cost()`):
  the same average/annualize/trend/load formula as above, computed
  separately per `medical_category` value (whatever categorization the
  source ledger uses), over the identical full-months window already
  established for the overall figure - so every category is measured over
  the same time period rather than each deciding its own edge exclusions.
  When a quote has been uploaded for this case (`POST /cases/{id}/quote`),
  each category row is matched against that quote's own category letter
  (or an exact plan-name match) to attach the quoted plan's product name,
  network, and gross premium, plus a `projected_loss_ratio` (this
  category's projected annual claims over its quoted premium) - a direct
  "if we accept this quote, does our own claims experience support it"
  check, distinct from the renewal rating's loss ratio (which compares
  against the *current*, not quoted, premium). A category with no matching
  quoted plan still shows its burning cost, just without the comparison
  columns - `quote_available_for_comparison` on the response says whether
  a quote exists for this case at all. Each category row also reports
  `claim_count` (distinct claim IDs), `avg_claims_per_member` (burning cost
  ÷ members), and `pct_of_total_claims` (this category's share of the
  total burning cost across all categories). Members are counted uniquely
  by patient ID AND prorated by each member's own `policy_start_date`/
  `policy_end_date` against the analysis period - a member covered for
  only 6 of the analysis window's 12 months counts as 0.5 members, not 1,
  since a mid-term joiner or leaver shouldn't be weighted the same as a
  full-term member in the per-member average.

**Renewal rating** (`app/scoring/rules/renewal_rating.py`,
`GET /cases/{id}/renewal-rating`) - the actual renewal-increase
calculation: actual loss ratio (the ledger's annualized incurred claims
over `current_annual_premium`), trended for inflation, then grossed up for
the loading, same gross-up convention throughout. Requires both a claims
ledger upload and `current_annual_premium` set on the case.

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
  names or insurer. Produces the standard 11-field summary directly, for
  every tier found, in one pass.
  - **Fallback 1 - the QIC/HealthCROSS Global "Plan - CAT X" layout**
    (`app/ingestion/quote_pdf.py`'s `parse_benefit_tables_only()`) - the
    same insurer family's EXISTING/incumbent benefits documents reuse the
    exact table layout built for its quotes (see below): the benefit label
    is the table's own first column, tier headers contain a "CAT <letter>"
    token instead of a known tier-name alias, and there's no premium table
    to enumerate categories from first, so this self-discovers them
    directly from the tier headers. Field-label wording varies even within
    this one insurer family across documents (e.g. "Maternity Inpatient
    Limit" vs "Maternity Inpatient- Limit", "Annual Maximum Optical Cover"
    vs "Annual Optical Cover") - each standard field's anchor is a list of
    known wordings, tried in order, not a single fixed string. Stored as
    `source_format="pdf-cat-style"`.
  - **Fallback 2 - the "labeled 3-column row" layout, one category per FILE**
    (`app/ingestion/labeled_row_benefits_pdf.py`'s
    `parse_labeled_row_benefits_pdf()`) - targets a third real document
    family (seen on MaxHealth/MaxMed's "MAXMED Neuron &lt;TIER&gt; GROUP"
    documents) where the whole file describes only ONE category, laid out
    as a bordered Benefit label / Value / Clarification table under
    full-width section-banner rows ("INPATIENT BENEFIT", "DENTAL", ...)
    that carry no data of their own. `find_tables()`'s own rows are really
    just the VALUE column's ruling - the label/description text often wraps
    across more than one such row with no ruling of its own (e.g. a real
    "Chiropractic, Ayurveda, Homeopathy, / Osteopathy & Acupuncture" label
    split across two rows, with its "AED 3,000" limit only appearing on the
    second) - so every word on the page is bucketed into a column/row cell
    by its own vertical midpoint rather than trusting pdfplumber's row
    grouping (which either drops or duplicates wrapped text right at a row
    boundary), and a row with no value is folded forward into whichever
    later row supplies one. The category letter isn't stated in the
    document body at all here (only a member-count band is, under a
    same-named but unrelated "CATEGORY" heading) - it's parsed from the
    filename instead (e.g. `..._Category_A_1.pdf`), which is also exactly
    why `mode=append` (see below) matters most for this format: each
    category is a genuinely separate file with no shared parent document.
    Returns `None` (not just an empty result) when a file isn't this layout
    at all, so `/benefits` falls through cleanly to the next fallback.
    Stored as `source_format="pdf-labeled-row"`.
  - **Fallback 3 - a plain text scan** - a real (non-scanned) PDF whose
    tiers are laid out with whitespace alignment rather than actual table
    lines (seen on a real Sukoon "renewal" TOB with 4 categories) leaves
    `find_tables()` with nothing usable under any table-based parser above.
    `parse_benefits_pdf_text_fallback()` reuses the OCR module's
    label-anchored nearby-value scan (`build_ocr_benefit_summary`) against
    this PDF's real extracted text instead of an OCR'd image - the same
    "report every candidate value with a verify note" behavior applies,
    since a flat text stream can't reveal which of several per-category
    values belongs to which category once the table structure is lost.
    `/benefits` tries the bordered-table parser, then the CAT-style parser,
    then the labeled-row parser, and only falls back here if all three
    find nothing, storing the result as `source_format="pdf-text-fallback"`.
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
  Each standard field's nearby-value scan only recognized a currency amount
  or a bare Covered/Not Covered flag - a field whose real value is
  descriptive free text (e.g. area of cover's "Worldwide Exc (USA)") found
  nothing at all and always showed "Not specified", and a co-insurance-style
  value combining a rate with a capped amount (e.g. "20% up to a maximum of
  AED 50/-") only ever surfaced the bare AED figure, dropping the rate. Both
  are fixed: a free-text fallback captures the nearby value verbatim
  (collapsing an exact immediate repeat, since two plan columns sharing the
  same value OCRs as that phrase twice back-to-back), and a percentage-plus-
  money pattern is tried before the plain money pattern so the rate isn't
  discarded. `deductible` also didn't have its own anchor at all - it was
  silently absent from every OCR'd document regardless of wording, and
  reusing the label wording as the `coinsurance` anchor's own duplicate
  entry now populates it too.

## Web UI

Opening the server's root URL (`http://127.0.0.1:8000/`) serves a small
self-contained single-page UI (`app/static/index.html` - no build step, no
JS framework, no external requests): create a case (choosing new vs.
existing business), upload census/benefits/claims/quote/claims-ledger
files, compute the scorecard, and record an outcome, all by clicking
around instead of using `/docs`. It's a thin client over the same API
below.

The case workspace is organized into four tabs (Census / Benefits / Claims /
Scorecard) below an always-visible case-details and file-upload area, rather
than one long stacked page - so reviewing a case's claims trend doesn't
require scrolling past the benefits comparison table first. Several sections
render a small hand-rolled inline SVG chart alongside their detail tables
(age distribution, nationality-zone mix, monthly claims trend, burning cost
by category, the scorecard's composite-score meter and component-multiplier
bars) - no charting library, consistent with the page's zero-dependency,
single-file design. Every chart mark carries a hover tooltip (a shared
`#chart-tooltip` element bound to any `[data-tooltip]` node), and file
uploads accept drag-and-drop onto their row in addition to the file picker.

## API

- `GET /cases` - list all cases
- `POST /cases` - create a case (broker, company, industry, region, business_type, current_annual_premium)
- `PATCH /cases/{id}` - update business_type/current_annual_premium after creation
- `POST /cases/{id}/census` - upload the census (xlsx/csv)
- `POST /cases/{id}/benefits` - upload the existing/incumbent table of benefits (xlsx/csv/pdf); `?mode=append` adds one category's file without replacing the others (default `mode=replace`)
- `POST /cases/{id}/quote` - upload a new insurer's quotation for comparison (pdf)
- `POST /cases/{id}/claims` - upload claims history (xlsx/csv, optional)
- `POST /cases/{id}/claims-ledger` - upload a raw per-claim-line claims ledger for an existing-business renewal (xlsx/csv)
- `POST /cases/{id}/plan-details` - upload the broker's "CLIENT & PLAN
  details" sheet to populate broker/industry/existing-insurer/target-premium
  metadata on the case
- `POST /cases/{id}/score` - compute and store a scorecard
- `GET /cases/{id}/scorecard` / `/scorecards` - latest / full history
- `POST /cases/{id}/outcome` - record what actually happened
- `GET /cases/{id}/census-summary` - demographic breakdown of the uploaded census (age bands, gender, marital status, relation, nationality-zone mix, married-female/maternity-risk/infant counts) as counts and percentages, with a Male/Female split on top of the age-band/relation/marital-status counts so a gender-skewed data gap (e.g. marital status only recorded for one gender on the source census) is visible rather than blended away, plus the top 5 nationalities within each zone
- `GET /cases/{id}/benefits-summary` - every uploaded existing/incumbent plan/tier in the standard 11-field format
- `GET /cases/{id}/premium-by-category` - the uploaded quote's per-category members, network, gross premium, and premium/member, plus a blended total
- `GET /cases/{id}/benefits-comparison` - existing plan(s) vs. quoted plan(s), compared field by field
- `GET /cases/{id}/claims-report` - the latest parsed claims report
- `GET /cases/{id}/claims-projection` - the burning-cost annual claims projection (new business)
- `GET /cases/{id}/diagnosis-exposure` - chronic/high-exposure-flagged diagnosis breakdown (new business, from a claims report)
- `GET /cases/{id}/claims-ledger-analysis` - top patients/diagnoses/providers, monthly trend, expected annual premium, and category-wise burning cost (compared against a quoted premium if a quote is available) from an uploaded claims ledger (existing business)
- `GET /cases/{id}/renewal-rating` - the renewal-increase calculation from actual loss ratio (existing business)
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
