"""Is the rate card charging what the book actually costs?

New Business prices off a rate card: a fixed grid of Product x Region x
Network x age band, with a price for males, females, and married females.
Nothing in that grid knows what those members have gone on to cost. The
book does - the burning cost cube (see burning_cost_cube) holds the
credibility-blended expected cost for exactly the same cells - but until
now the two only ever met AFTER a quote was produced, as a footnote
comparison on one case.

This puts them side by side cell by cell, before anyone quotes. For every
row of the card it asks: what do we charge here, what does this cell
actually cost, and what loss ratio does that imply? A cell whose implied
loss ratio is 140% is not a pricing opinion - it is a cell that has been
losing money on every case it has ever priced, and it will keep doing so
until the number changes.

Two things this deliberately does NOT do:

It does not rewrite the card. The output is a suggested price beside the
current one, because a rate card is a commercial document as well as an
actuarial one - a cell may be knowingly held below cost to win a segment,
and that is a decision, not an error to be corrected automatically.

It does not treat a thin cell as a finding. Where the cube fell back to a
broader cell for want of exposure, the comparison says so, because
"your Platinum 66+ female cell is underpriced" means nothing if it rests
on two member-years of experience.

Pure functions over rate-card and cube dicts - no ORM, no database.
"""
from typing import Dict, List, Optional

from app.scoring.rules.burning_cost_cube import UNMAPPED, build_cube_index

#: Loss ratio a cell is calibrated TO. The suggested price is whatever
#: makes the cell land here after its own expense loading. 0.85 leaves a
#: margin over break-even rather than pricing every cell to exactly fund
#: its own claims and nothing else.
DEFAULT_TARGET_LOSS_RATIO = 0.85

#: Cells whose implied loss ratio is beyond these bounds are called out.
#: The high side is the money-losing one; the low side matters too, since
#: a cell priced at half its cost is leaving business on the table and is
#: usually a sign the band is wrong rather than that HealthCross is
#: unusually good at that age.
UNDERPRICED_ABOVE = 1.0
OVERPRICED_BELOW = 0.5

#: Genders the card prices separately, and the cube key each maps to.
#: "Married female" is a card concept (a surcharge on the female price
#: for maternity exposure) rather than a cube dimension, so it looks up
#: the same female cell and is compared against the surcharged price.
CARD_GENDERS = (
    ("male", "M"),
    ("female", "F"),
    ("married_female", "F"),
)


def _cube_expected_cost(
    cube: dict,
    index: dict,
    product: Optional[str],
    network: Optional[str],
    age_band: str,
    gender: str,
) -> Optional[dict]:
    """The cube's expected cost for one card cell, walking from the most
    specific match back toward the book - the same fallback the pricing
    engine uses, so a calibration figure and a quoted price for the same
    member can never come from different rules.
    """
    dimensions = tuple(cube["dimensions"])
    key_by_dim = {"product": product, "network": network, "age_band": age_band, "gender": gender}
    full_key = tuple(
        (str(key_by_dim.get(dim)).strip() if key_by_dim.get(dim) else UNMAPPED)
        for dim in dimensions
    )
    for level in range(len(dimensions), 0, -1):
        cell = index.get((level, full_key[:level]))
        if cell and cell["expected_cost"] is not None:
            return {
                "expected_cost": cell["expected_cost"],
                "matched_level": level,
                "credibility": cell["credibility"],
                "exposure_member_years": cell["earned_member_years"],
                "fell_back": level < len(dimensions),
            }
    book = cube["book"]["burning_cost"]
    if book is None:
        return None
    return {
        "expected_cost": book,
        "matched_level": 0,
        "credibility": 0.0,
        "exposure_member_years": cube["book"]["earned_member_years"],
        "fell_back": True,
    }


def rate_card_calibration(
    rate_cards: List[dict],
    cube: dict,
    loading_pct: float,
    target_loss_ratio: float = DEFAULT_TARGET_LOSS_RATIO,
    min_exposure_member_years: float = 5.0,
) -> dict:
    """Every rate card cell against what the book says it costs.

    `implied_loss_ratio` is the cell's expected claims over the part of
    its price that is actually available to pay claims - price x (1 -
    loading), not the price itself. Comparing claims against the gross
    price would flatter every cell by the whole expense load and make a
    card that is losing money look like it is running at 70%.
    """
    if loading_pct >= 1:
        raise ValueError("loading_pct must be less than 1.")
    if target_loss_ratio <= 0:
        raise ValueError("target_loss_ratio must be positive.")

    index = build_cube_index(cube)
    cells: List[dict] = []

    for row in rate_cards:
        from_age, to_age = row.get("from_age"), row.get("to_age")
        if from_age is None or to_age is None:
            continue
        age_band = f"{from_age}-{to_age}"

        for card_gender, cube_gender in CARD_GENDERS:
            price = row.get(f"{card_gender}_price")
            if card_gender == "married_female":
                surcharge = row.get("married_female_surcharge")
                if not surcharge:
                    continue
                price = (row.get("female_price") or 0.0) + surcharge
            if not price:
                continue

            match = _cube_expected_cost(
                cube, index, row.get("product"), row.get("network"), age_band, cube_gender
            )
            if match is None:
                continue

            available_for_claims = price * (1 - loading_pct)
            implied_lr = (
                match["expected_cost"] / available_for_claims if available_for_claims else None
            )
            suggested = match["expected_cost"] / target_loss_ratio / (1 - loading_pct)

            cells.append({
                "product": row.get("product"),
                "region": row.get("region"),
                "network": row.get("network"),
                "tpa": row.get("tpa"),
                "age_band": age_band,
                "from_age": from_age,
                "gender": card_gender,
                "card_price": round(price, 2),
                "expected_cost": match["expected_cost"],
                "available_for_claims": round(available_for_claims, 2),
                "implied_loss_ratio": round(implied_lr, 4) if implied_lr is not None else None,
                "suggested_price": round(suggested, 2),
                "price_change_pct": round((suggested / price - 1) * 100, 1) if price else None,
                "exposure_member_years": match["exposure_member_years"],
                "credibility": match["credibility"],
                "fell_back": match["fell_back"],
                # Thin cells are reported but never presented as findings -
                # a mispricing claim resting on two member-years is noise.
                "thin": match["exposure_member_years"] < min_exposure_member_years,
                "verdict": (
                    "underpriced" if implied_lr is not None and implied_lr > UNDERPRICED_ABOVE
                    else "overpriced" if implied_lr is not None and implied_lr < OVERPRICED_BELOW
                    else "in range"
                ),
            })

    cells.sort(key=lambda c: -(c["implied_loss_ratio"] or 0.0))
    solid = [c for c in cells if not c["thin"]]
    underpriced = [c for c in solid if c["verdict"] == "underpriced"]
    overpriced = [c for c in solid if c["verdict"] == "overpriced"]

    return {
        "loading_pct": loading_pct,
        "target_loss_ratio": target_loss_ratio,
        "cell_count": len(cells),
        "measurable_cell_count": len(solid),
        "thin_cell_count": len(cells) - len(solid),
        "underpriced_count": len(underpriced),
        "overpriced_count": len(overpriced),
        "worst_implied_loss_ratio": solid[0]["implied_loss_ratio"] if solid else None,
        "cells": cells,
    }


def calibration_summary_by_product(calibration: dict) -> List[dict]:
    """Roll the cells up per product, so "is Silver priced right?" has an
    answer without reading forty rows. Averages are weighted by nothing -
    each cell counts once - because this is a count of how much of the
    grid is mispriced, not a premium-weighted view of the book: a cell
    nobody has sold yet is still a cell that will lose money when they do.
    """
    by_product: Dict[str, dict] = {}
    for cell in calibration["cells"]:
        if cell["thin"]:
            continue
        entry = by_product.setdefault(cell["product"] or "Unmapped", {
            "product": cell["product"] or "Unmapped",
            "cells": 0, "underpriced": 0, "overpriced": 0, "lr_sum": 0.0,
        })
        entry["cells"] += 1
        entry["lr_sum"] += cell["implied_loss_ratio"] or 0.0
        if cell["verdict"] == "underpriced":
            entry["underpriced"] += 1
        elif cell["verdict"] == "overpriced":
            entry["overpriced"] += 1

    rows = []
    for entry in by_product.values():
        rows.append({
            "product": entry["product"],
            "measurable_cells": entry["cells"],
            "underpriced_cells": entry["underpriced"],
            "overpriced_cells": entry["overpriced"],
            "average_implied_loss_ratio": round(entry["lr_sum"] / entry["cells"], 4) if entry["cells"] else None,
            "underpriced_share": round(entry["underpriced"] / entry["cells"], 4) if entry["cells"] else None,
        })
    rows.sort(key=lambda r: -(r["average_implied_loss_ratio"] or 0.0))
    return rows
