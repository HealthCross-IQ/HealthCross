#!/usr/bin/env python3
"""One-time data update: force-applies the new scorecard weights and
nationality-zone multipliers to your LIVE active weight set.

Why this is needed: app/main.py's startup seeding only backfills a
weight set that's still at version 1 (i.e. never touched by a
recalibration or a manual edit). If your active weight set has already
been recalibrated at least once, its version is past 1, so simply
restarting the server after applying the code changes will NOT change
its actual stored numbers - this script sets them directly, regardless
of version.

Run from the HealthCross repo root, with the venv activated:
    python3 set_scoring_weights_v2.py

This only touches these specific fields - your active weight set's
recalibration history, version number, and any other tuned values are
left untouched.
"""
from app.database import SessionLocal
from app.models import db_models as models

NEW_VALUES = {
    "w_demographic": 0.35,
    "w_claims_experience": 0.35,
    "w_benefit_richness": 0.20,
    "w_industry": 0.10,
    "zone_1_asia_multiplier": 0.90,
    "zone_2_middle_east_multiplier": 1.15,
    "zone_3_europe_americas_multiplier": 0.95,
}


def main():
    db = SessionLocal()
    try:
        active = db.query(models.ScoringWeightSet).filter_by(is_active=True).first()
        if not active:
            print("No active weight set found - nothing to update. Start the server once first.")
            return

        print(f"Active weight set id={active.id}, version={active.version}")
        print("Before -> After:")
        for field, new_value in NEW_VALUES.items():
            old_value = getattr(active, field)
            print(f"  {field}: {old_value} -> {new_value}")
            setattr(active, field, new_value)

        db.commit()
        print("\nDone - active weight set updated.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
