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

## API

- `POST /cases` - create a case (broker, company, industry, region)
- `POST /cases/{id}/census` - upload the census (xlsx/csv)
- `POST /cases/{id}/benefits` - upload the table of benefits (xlsx/csv)
- `POST /cases/{id}/claims` - upload claims history (xlsx/csv, optional)
- `POST /cases/{id}/plan-details` - upload the broker's "CLIENT & PLAN
  details" sheet to populate broker/industry/existing-insurer/target-premium
  metadata on the case
- `POST /cases/{id}/score` - compute and store a scorecard
- `GET /cases/{id}/scorecard` / `/scorecards` - latest / full history
- `POST /cases/{id}/outcome` - record what actually happened
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

- **Table of benefits ingestion is generic**, not tailored to this broker's
  specific benefit-matrix layout (network tiers like Platinum/Comprehensive,
  percentage-based copays/deductibles). It handles a simple one-row-per-plan
  structure well; parsing the exact matrix layout is a follow-up once we
  see how consistent that layout is across brokers.
- **Zone multiplier learning needs volume**: with only a handful of bound
  cases, `/admin/recalibrate` will correctly refuse to move the zone
  multipliers. The zones only start meaningfully diverging from 1.0 once
  enough outcomes (20+, ideally far more) have been recorded.
- **No authentication/authorization layer** - this is a scoring engine
  service, meant to sit behind the existing portal's auth, not to be
  internet-facing on its own.
