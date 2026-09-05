"""The monthly rate review: what each card cell costs, what the book says
it should be priced at, what the team decided, and how that decision is
holding up on this month's data.

Rate Card Calibration (rate_card_calibration.py) answers "is the card
priced right?" for every cell on the card at once. This is the narrower,
slower question an underwriter actually works through at month end, one
product at a time: for each age band and sex, how many lives, what they
cost, what the loss ratio is, what the arithmetic suggests - and beside
it, what was agreed and whether the cell is moving the right way.

Three things are kept deliberately separate, because they are:

  the SUGGESTION - cost / target / (1 - loading), credibility-blended
  and held within the round's caps. Pure arithmetic off the book.

  the DECISION - what the team agreed (a +100% here, a hold there),
  stored as rows (RateReviewDecision) and validated each month against
  fresh data. A decision may knowingly stop short of the suggestion;
  a cell with no decision says so rather than reading as "hold".

  the PARAMETERS - the figures the review is judged against (target
  loss ratio, loading, credibility standard, caps). Stored, editable,
  and re-validated monthly as the book grows (DEFAULT_PARAMETERS below).

A hot network is reviewed on its own table (`network_scope` = "only")
and kept out of the main one ("excluding"), because a network that costs
2.5x the others cannot be averaged into them without hiding both.

Pure functions over member-result dicts and plain parameter dicts -
no ORM, no database.
"""
import math
from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Tuple

from app.scoring.rules.burning_cost_cube import member_incurred_claims
from app.scoring.rules.experience_pricing import HOUSE_TARGET_LOSS_RATIO
from app.scoring.rules.new_business_rating import DEFAULT_LOADING_BY_PRODUCT

#: The review's parameters and their house defaults. Every one is a
#: judgement that should be re-examined as the book grows - which is
#: why they are stored and shown, not buried here.
DEFAULT_PARAMETERS: Dict[str, object] = {
    # Loss ratio a cell is priced TO, net of loading.
    "target_loss_ratio": HOUSE_TARGET_LOSS_RATIO,
    # Expense loading per product; the suggested rate is grossed up by it.
    "loading_by_product": {k.capitalize(): v for k, v in DEFAULT_LOADING_BY_PRODUCT.items()},
    # Member-years at which a cell's own experience is trusted in full.
    "full_credibility_member_years": 100.0,
    # Below this a cell is shown but never gets an Increase/Discount call.
    "min_member_years_to_act": 25.0,
    # The most any cell may move in one review round, either way.
    "max_increase_pct": 100.0,
    "max_discount_pct": 15.0,
    # Per-member claims above this are pooled across the whole book for
    # pricing (the cell's own loss ratio is still shown uncapped).
    "large_claim_cap": 100000.0,
    # A loss-ratio move smaller than this (in points) is noise, not news.
    "materiality_pct": 10.0,
    # Blended cost is held within these multiples of the scope's average.
    "min_relativity": 0.5,
    "max_relativity": 2.0,
    # The bands the review is run on - finer than the card where the
    # book showed a band hiding two different risks (0-1 vs 2-17).
    "age_bands": [[0, 1], [2, 17], [18, 25], [26, 35], [36, 40], [41, 59], [60, 69], [70, 99]],
    # Networks reviewed on their own table and kept out of the main one.
    "separate_networks": ["MSH Platinum"],
    # Data older than this at review time is flagged as stale.
    "stale_after_days": 45,
}

GENDERS = ("M", "F")


def parameters_with_defaults(stored: Optional[dict]) -> dict:
    """Stored parameters over the code defaults, so a parameter added
    after a deployment still has a value."""
    merged = {k: (v.copy() if isinstance(v, (dict, list)) else v) for k, v in DEFAULT_PARAMETERS.items()}
    for key, value in (stored or {}).items():
        if value is not None:
            merged[key] = value
    return merged


def loading_for_product(params: dict, product: Optional[str]) -> float:
    table = params.get("loading_by_product") or {}
    for name, value in table.items():
        if (name or "").strip().lower() == (product or "").strip().lower():
            return float(value)
    return float(max(DEFAULT_LOADING_BY_PRODUCT.values()))


def _band_label(age: Optional[int], bands: List[tuple]) -> Optional[str]:
    if age is None:
        return None
    for low, high in bands:
        if low <= age <= high:
            return f"{low}-{high}"
    return None


def _is_separate(network: Optional[str], separate_networks: List[str]) -> bool:
    n = (network or "").strip().lower()
    return any(n == (s or "").strip().lower() for s in separate_networks)


def scope_members(
    results: List[dict],
    product: str,
    network_scope: str = "excluding",
    network: Optional[str] = None,
    separate_networks: Optional[List[str]] = None,
) -> List[dict]:
    """The members one review table is about.

    "excluding" is the main table: the product minus every network that
    is reviewed separately. "only" is one such network on its own.
    "all" is the whole product, for a product with no split.
    """
    rows = [r for r in results if r.get("in_scope", True) and (r.get("product") or "") == product]
    if network_scope == "only":
        return [r for r in rows if (r.get("network") or "").strip().lower() == (network or "").strip().lower()]
    if network_scope == "excluding":
        excluded = list(separate_networks or [])
        if network:
            excluded.append(network)
        return [r for r in rows if not _is_separate(r.get("network"), excluded)]
    return rows


def _totals(rows: List[dict], cap: Optional[float]) -> dict:
    my = prem = inc = priced = 0.0
    capped = 0
    for r in rows:
        my += r.get("earned_premium_fraction") or 0.0
        prem += r.get("actual_premium") or 0.0
        claims = member_incurred_claims(r)
        inc += claims
        if cap is not None and claims > cap:
            priced += cap
            capped += 1
        else:
            priced += claims
    return {
        "lives": len(rows),
        "member_years": my,
        "premium": prem,
        "incurred": inc,
        "priced_claims": priced,
        "capped_members": capped,
    }


def _recommendation(change_pct: Optional[float], member_years: float, params: dict) -> Tuple[str, Optional[float]]:
    """What the arithmetic says to do, and the change after the round's
    caps. Thin cells get 'Review - thin data' rather than a call either
    way; a move inside materiality is a Hold."""
    if change_pct is None:
        return "No data", None
    if member_years < float(params["min_member_years_to_act"]):
        return "Review - thin data", None
    materiality = float(params["materiality_pct"])
    if change_pct > materiality:
        return "Increase", min(change_pct, float(params["max_increase_pct"]))
    if change_pct < -materiality:
        return "Discount", max(change_pct, -float(params["max_discount_pct"]))
    return "Hold", 0.0


def _reason(cell: dict, params: dict) -> str:
    """Plain words for the row, from the row's own numbers."""
    if cell["member_years"] <= 0 or cell["premium"] <= 0:
        return "No exposure in this cell."
    z = cell["credibility"]
    cred = "fully credible" if z >= 1 else f"{z:.0%} credible - rest from the {cell['age_band']} band average"
    text = (
        f"{cell['lives']} lives, {cell['member_years']:.0f} member-years ({cred}). "
        f"Costs AED {cell['cost_pmpy']:,.0f} per member-year against {cell['premium_pmpy']:,.0f} earned: "
        f"{cell['gross_loss_ratio']:.0%} gross / {cell['net_loss_ratio']:.0%} net loss ratio."
    )
    if cell["capped_members"]:
        text += (
            f" {cell['capped_members']} member(s) above the AED {float(params['large_claim_cap']):,.0f} cap - "
            f"excess pooled across the book, priced cost {cell['pricing_cost_pmpy']:,.0f}."
        )
    if cell["relativity_capped"]:
        text += " Blended cost held within the relativity band of the age band's average."
    if cell["recommendation"] in ("Increase", "Discount") and cell["capped_change_pct"] != cell["change_pct"]:
        text += f" Cost-to-target is {cell['change_pct']:+.0f}%; capped at {cell['capped_change_pct']:+.0f}% for this round."
    return text


def review_cells(
    results: List[dict],
    product: str,
    params: dict,
    network_scope: str = "excluding",
    network: Optional[str] = None,
    rate_cards: Optional[List[dict]] = None,
    region: Optional[str] = None,
) -> dict:
    """Every review band x sex for one product scope, with the book's
    figures, the suggested rate and the arithmetic's recommendation.

    `current_rate` is the card price for the cell when a rate card row
    matches (product, network for an "only" scope, review band, sex),
    otherwise the premium actually earned per member-year in the cell -
    which is what the book was really charging, card or no card.
    """
    bands = [tuple(b) for b in params["age_bands"]]
    cap = params.get("large_claim_cap")
    cap = float(cap) if cap not in (None, "", 0) else None
    target = float(params["target_loss_ratio"])
    loading = loading_for_product(params, product)
    full_cred = float(params["full_credibility_member_years"])
    min_rel, max_rel = float(params["min_relativity"]), float(params["max_relativity"])

    members = scope_members(results, product, network_scope, network, params.get("separate_networks") or [])
    scope_label = (
        f"{product} on {network}" if network_scope == "only"
        else f"{product} excluding {', '.join(params.get('separate_networks') or [])}" if network_scope == "excluding" and params.get("separate_networks")
        else product
    )

    totals = _totals(members, cap)
    # Excess above the cap is pooled over the whole scope as a flat
    # load per member-year, so every cell pays a little for the risk of
    # the one big claim rather than one cell paying for all of it.
    pooled_load = ((totals["incurred"] - totals["priced_claims"]) / totals["member_years"]) if totals["member_years"] else 0.0
    scope_cost = (totals["priced_claims"] / totals["member_years"] + pooled_load) if totals["member_years"] else None

    card_price = _card_price_lookup(rate_cards or [], product, network if network_scope == "only" else None, region, bands)

    by_cell: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for r in members:
        label = _band_label(r.get("age"), bands)
        g = (r.get("gender") or "").strip().upper()[:1]
        if label and g in GENDERS:
            by_cell[(label, g)].append(r)

    def _credibility(my: float) -> float:
        return min(1.0, math.sqrt(my / full_cred)) if my > 0 and full_cred > 0 else 0.0

    def _blend(own: Optional[float], complement: Optional[float], z: float) -> Optional[float]:
        if own is None:
            return complement
        if complement is None:
            return own
        return z * own + (1 - z) * complement

    cells: List[dict] = []
    for low, high in bands:
        label = f"{low}-{high}"
        # The band as a whole (both sexes) is the complement for each sex
        # within it, and is itself blended toward the scope average. Age
        # is the strongest structural driver of cost, so a band is never
        # capped against the all-ages average - a child genuinely costs
        # a third of an adult; only a sex within its band is held within
        # the relativity range of the band.
        band_rows = by_cell.get((label, "M"), []) + by_cell.get((label, "F"), [])
        bt = _totals(band_rows, cap)
        band_own = (bt["priced_claims"] / bt["member_years"] + pooled_load) if bt["member_years"] else None
        band_cost = _blend(band_own, scope_cost, _credibility(bt["member_years"]))
        for g in GENDERS:
            rows = by_cell.get((label, g), [])
            t = _totals(rows, cap)
            my = t["member_years"]
            own_cost = (t["incurred"] / my) if my else None
            own_priced = (t["priced_claims"] / my + pooled_load) if my else None
            z = _credibility(my)
            blended = _blend(own_priced, band_cost, z)
            rel_capped = False
            if blended is not None and band_cost:
                bounded = max(min_rel * band_cost, min(max_rel * band_cost, blended))
                rel_capped = abs(bounded - blended) > 1e-9
                blended = bounded
            suggested = (blended / target / (1 - loading)) if blended is not None else None
            premium_pmpy = (t["premium"] / my) if my else None
            card = card_price.get((low, high, g))
            current = card if card is not None else premium_pmpy
            change = ((suggested / current - 1) * 100) if (suggested is not None and current) else None
            gross = (t["incurred"] / t["premium"]) if t["premium"] else None
            net = (gross / (1 - loading)) if gross is not None else None
            rec, capped_change = _recommendation(change, my, params)
            cell = {
                "age_band": label,
                "from_age": low,
                "to_age": high,
                "gender": g,
                "lives": t["lives"],
                "member_years": round(my, 1),
                "premium": round(t["premium"], 2),
                "incurred": round(t["incurred"], 2),
                "premium_pmpy": round(premium_pmpy, 2) if premium_pmpy is not None else None,
                "card_price": round(card, 2) if card is not None else None,
                "current_rate": round(current, 2) if current is not None else None,
                "current_rate_basis": "card" if card is not None else "earned premium",
                "cost_pmpy": round(own_cost, 2) if own_cost is not None else None,
                "pricing_cost_pmpy": round(own_priced, 2) if own_priced is not None else None,
                "capped_members": t["capped_members"],
                "credibility": round(z, 4),
                "band_cost_pmpy": round(band_cost, 2) if band_cost is not None else None,
                "blended_cost_pmpy": round(blended, 2) if blended is not None else None,
                "relativity_capped": rel_capped,
                "gross_loss_ratio": round(gross, 4) if gross is not None else None,
                "net_loss_ratio": round(net, 4) if net is not None else None,
                "suggested_rate": round(suggested, 2) if suggested is not None else None,
                "change_pct": round(change, 1) if change is not None else None,
                "recommendation": rec,
                "capped_change_pct": round(capped_change, 1) if capped_change is not None else None,
                "thin": my < float(params["min_member_years_to_act"]),
                "scope_label": scope_label,
            }
            cell["reason"] = _reason(cell, params)
            cells.append(cell)

    gross_total = (totals["incurred"] / totals["premium"]) if totals["premium"] else None
    return {
        "product": product,
        "network_scope": network_scope,
        "network": network,
        "scope_label": scope_label,
        "loading_pct": loading,
        "target_loss_ratio": target,
        "large_claim_cap": cap,
        "pooled_load_per_member_year": round(pooled_load, 2),
        "totals": {
            "lives": totals["lives"],
            "member_years": round(totals["member_years"], 1),
            "premium": round(totals["premium"], 2),
            "incurred": round(totals["incurred"], 2),
            "premium_pmpy": round(totals["premium"] / totals["member_years"], 2) if totals["member_years"] else None,
            "cost_pmpy": round(totals["incurred"] / totals["member_years"], 2) if totals["member_years"] else None,
            "gross_loss_ratio": round(gross_total, 4) if gross_total is not None else None,
            "net_loss_ratio": round(gross_total / (1 - loading), 4) if gross_total is not None else None,
            "capped_members": totals["capped_members"],
        },
        "cells": cells,
    }


def _card_price_lookup(rate_cards: List[dict], product: str, network: Optional[str], region: Optional[str],
                       bands: Optional[List[tuple]] = None) -> Dict[tuple, float]:
    """Card price per (from_age, to_age, gender) review band for the scope.

    A card row prices a review band when its own band COVERS it - the
    card's 18-40 row prices the review's 18-25, 26-35 and 36-40 - so a
    finer review split still reads its current rate off the card. A
    review band that straddles two card rows takes neither (the rate
    would be an average of unlike things) and falls back to earned
    premium, which the cell says.
    """
    prices: Dict[tuple, List[float]] = defaultdict(list)
    rows = [
        row for row in rate_cards
        if (row.get("product") or "").strip().lower() == product.strip().lower()
        and (not network or (row.get("network") or "").strip().lower() == network.strip().lower())
        and (not region or (row.get("region") or "").strip().lower() == region.strip().lower())
        and row.get("from_age") is not None and row.get("to_age") is not None
    ]
    targets = [tuple(b) for b in bands] if bands else sorted({(r["from_age"], r["to_age"]) for r in rows})
    for low, high in targets:
        for row in rows:
            if row["from_age"] <= low and high <= row["to_age"]:
                for g, field in (("M", "male_price"), ("F", "female_price")):
                    if row.get(field):
                        prices[(low, high, g)].append(float(row[field]))
    # Several regions/networks on the same band: the simple mean, flagged
    # by basis only - the review is about the band, the card about where.
    return {k: sum(v) / len(v) for k, v in prices.items() if v}


def apply_decisions(review: dict, decisions: List[dict]) -> dict:
    """Attach the agreed decision to each cell and what the cell's loss
    ratio becomes under it - the same claims over the changed premium.
    A cell with no decision is marked as such rather than as a hold."""
    loading = review["loading_pct"]
    scope, network, product = review["network_scope"], review["network"], review["product"]
    for cell in review["cells"]:
        match = None
        for d in decisions:
            if (d.get("product") or "").strip().lower() != product.strip().lower():
                continue
            d_scope = d.get("network_scope") or "all"
            # A decision taken for all networks applies on every table;
            # a scoped one only on its own table.
            if d_scope != "all":
                if d_scope != scope:
                    continue
                if scope == "only" and (d.get("network") or "").strip().lower() != (network or "").strip().lower():
                    continue
            if d.get("gender") and d["gender"].strip().upper()[:1] != cell["gender"]:
                continue
            if not (d["from_age"] <= cell["from_age"] and cell["to_age"] <= d["to_age"]):
                continue
            match = d
            break
        if match is None:
            cell["decision"] = None
            cell["decision_action"] = "No decision yet"
            cell["decision_change_pct"] = None
            cell["rate_after_decision"] = None
            cell["gross_loss_ratio_after"] = None
            cell["net_loss_ratio_after"] = None
            continue
        pct = float(match.get("change_pct") or 0.0)
        cell["decision"] = match
        cell["decision_action"] = match.get("action") or "hold"
        cell["decision_change_pct"] = pct
        cell["rate_after_decision"] = round(cell["current_rate"] * (1 + pct / 100), 2) if cell["current_rate"] else None
        g_after = (cell["gross_loss_ratio"] / (1 + pct / 100)) if cell["gross_loss_ratio"] is not None and pct > -100 else None
        cell["gross_loss_ratio_after"] = round(g_after, 4) if g_after is not None else None
        cell["net_loss_ratio_after"] = round(g_after / (1 - loading), 4) if g_after is not None else None

    # Scope totals under the decisions: premium rises by each cell's
    # action, claims stay where they are.
    prem_after = sum(
        c["premium"] * (1 + (c["decision_change_pct"] or 0.0) / 100) for c in review["cells"]
    )
    t = review["totals"]
    t["premium_after_decisions"] = round(prem_after, 2)
    t["premium_change_pct"] = round((prem_after / t["premium"] - 1) * 100, 1) if t["premium"] else None
    t["gross_loss_ratio_after"] = round(t["incurred"] / prem_after, 4) if prem_after else None
    t["net_loss_ratio_after"] = round(t["incurred"] / prem_after / (1 - loading), 4) if prem_after else None
    return review


def network_breakdown(results: List[dict], product: str, loading: float) -> List[dict]:
    """Where the product's cost sits by network, with each network's cost
    as a multiple of the cheapest credible one - the 'network factor' a
    card should carry."""
    by_net: Dict[str, List[dict]] = defaultdict(list)
    for r in results:
        if r.get("in_scope", True) and (r.get("product") or "") == product:
            by_net[(r.get("network") or "Unmapped").strip()].append(r)
    rows = []
    for net, members in by_net.items():
        t = _totals(members, None)
        my = t["member_years"]
        rows.append({
            "network": net,
            "lives": t["lives"],
            "member_years": round(my, 1),
            "premium_pmpy": round(t["premium"] / my, 2) if my else None,
            "cost_pmpy": round(t["incurred"] / my, 2) if my else None,
            "gross_loss_ratio": round(t["incurred"] / t["premium"], 4) if t["premium"] else None,
            "net_loss_ratio": round(t["incurred"] / t["premium"] / (1 - loading), 4) if t["premium"] else None,
        })
    base = [r for r in rows if r["member_years"] >= 25 and r["cost_pmpy"]]
    base_cost = min(r["cost_pmpy"] for r in base) if base else None
    base_prem = None
    if base_cost is not None:
        base_prem = next(r["premium_pmpy"] for r in base if r["cost_pmpy"] == base_cost)
    for r in rows:
        r["cost_factor"] = round(r["cost_pmpy"] / base_cost, 2) if (base_cost and r["cost_pmpy"]) else None
        r["premium_factor"] = round(r["premium_pmpy"] / base_prem, 2) if (base_prem and r["premium_pmpy"]) else None
    rows.sort(key=lambda r: -(r["member_years"] or 0))
    return rows


def relation_breakdown(members: List[dict], loading: float) -> List[dict]:
    """Relation x sex for one scope - whether a female result is a spouse
    story or a female story, which decides whether the fix is a spouse
    surcharge or the female rate."""
    by_key: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for r in members:
        g = (r.get("gender") or "").strip().upper()[:1]
        if g in GENDERS:
            by_key[((r.get("relation") or "unknown").strip().lower(), g)].append(r)
    rows = []
    for (rel, g), rows_ in sorted(by_key.items()):
        t = _totals(rows_, None)
        my = t["member_years"]
        rows.append({
            "relation": rel,
            "gender": g,
            "lives": t["lives"],
            "member_years": round(my, 1),
            "premium_pmpy": round(t["premium"] / my, 2) if my else None,
            "cost_pmpy": round(t["incurred"] / my, 2) if my else None,
            "gross_loss_ratio": round(t["incurred"] / t["premium"], 4) if t["premium"] else None,
            "net_loss_ratio": round(t["incurred"] / t["premium"] / (1 - loading), 4) if t["premium"] else None,
        })
    order = {"employee": 0, "spouse": 1, "child": 2}
    rows.sort(key=lambda r: (order.get(r["relation"], 9), r["gender"]))
    return rows


def validate_against_snapshot(
    review: dict,
    last: Optional[dict],
    params: dict,
    data_as_of: Optional[date],
    today: Optional[date] = None,
) -> dict:
    """This month against the last saved review of the same scope.

    Growth in lives and member-years says how much new evidence there
    is; loss-ratio moves beyond materiality are the cells to look at;
    a cell that crossed the credibility line is one whose 'thin' caveat
    has expired; and identical data-as-of means nothing new has been
    uploaded, which is its own finding.
    """
    today = today or date.today()
    warnings: List[str] = []
    stale_days = int(params.get("stale_after_days") or 45)
    if data_as_of is None:
        warnings.append("The book has no data-as-of date recorded - set it on upload so the review can tell old data from new.")
    elif (today - data_as_of).days > stale_days:
        warnings.append(f"Data is {(today - data_as_of).days} days old (as of {data_as_of.isoformat()}) - older than the {stale_days}-day limit. Upload the latest members and claims before acting on this review.")

    out = {
        "data_as_of": data_as_of.isoformat() if data_as_of else None,
        "last_review": None,
        "warnings": warnings,
        "material_moves": [],
        "newly_credible": [],
        "recommendation_changes": [],
    }
    if not last:
        warnings.append("No earlier review saved for this scope - save this one so next month can be read as a movement.")
        return out

    last_as_of = last.get("data_as_of")
    out["last_review"] = {
        "data_as_of": last_as_of,
        "created_at": last.get("created_at"),
        "lives": (last.get("summary") or {}).get("lives"),
        "member_years": (last.get("summary") or {}).get("member_years"),
        "gross_loss_ratio": (last.get("summary") or {}).get("gross_loss_ratio"),
    }
    if last_as_of and data_as_of and last_as_of == data_as_of.isoformat():
        warnings.append(f"Same data as the last saved review ({last_as_of}) - nothing new has been uploaded since.")

    t = review["totals"]
    ls = last.get("summary") or {}
    out["lives_change"] = (t["lives"] - ls["lives"]) if ls.get("lives") is not None else None
    out["lives_change_pct"] = round((t["lives"] / ls["lives"] - 1) * 100, 1) if ls.get("lives") else None
    out["member_years_change"] = round(t["member_years"] - ls["member_years"], 1) if ls.get("member_years") is not None else None
    out["gross_loss_ratio_change_points"] = (
        round((t["gross_loss_ratio"] - ls["gross_loss_ratio"]) * 100, 1)
        if t.get("gross_loss_ratio") is not None and ls.get("gross_loss_ratio") is not None else None
    )
    if ls.get("gross_loss_ratio") is not None and ls.get("target_loss_ratio") not in (None, review["target_loss_ratio"]):
        warnings.append(f"Target loss ratio changed since the last review ({ls['target_loss_ratio']:.0%} -> {review['target_loss_ratio']:.0%}).")

    materiality = float(params["materiality_pct"])
    min_act = float(params["min_member_years_to_act"])
    last_cells = {(c["age_band"], c["gender"]): c for c in (last.get("cells") or [])}
    for c in review["cells"]:
        prev = last_cells.get((c["age_band"], c["gender"]))
        if not prev:
            continue
        if c["gross_loss_ratio"] is not None and prev.get("gross_loss_ratio") is not None:
            move = (c["gross_loss_ratio"] - prev["gross_loss_ratio"]) * 100
            if abs(move) >= materiality:
                out["material_moves"].append({
                    "age_band": c["age_band"], "gender": c["gender"],
                    "from": prev["gross_loss_ratio"], "to": c["gross_loss_ratio"],
                    "points": round(move, 1),
                })
        if (prev.get("member_years") or 0) < min_act <= c["member_years"]:
            out["newly_credible"].append({"age_band": c["age_band"], "gender": c["gender"], "member_years": c["member_years"]})
        if prev.get("recommendation") and prev["recommendation"] != c["recommendation"]:
            out["recommendation_changes"].append({
                "age_band": c["age_band"], "gender": c["gender"],
                "from": prev["recommendation"], "to": c["recommendation"],
            })
    return out


def snapshot_of(review: dict, params: dict, data_as_of: Optional[date]) -> dict:
    """What gets stored for next month's comparison - the totals, the
    parameters, and per-cell figures small enough to keep for years."""
    return {
        "product": review["product"],
        "network_scope": review["network_scope"],
        "network": review["network"],
        "data_as_of": data_as_of,
        "parameters": params,
        "summary": {**review["totals"], "target_loss_ratio": review["target_loss_ratio"], "loading_pct": review["loading_pct"]},
        "cells": [
            {k: c.get(k) for k in (
                "age_band", "gender", "lives", "member_years", "premium_pmpy", "cost_pmpy",
                "gross_loss_ratio", "net_loss_ratio", "credibility", "suggested_rate",
                "change_pct", "recommendation", "decision_action", "decision_change_pct",
            )}
            for c in review["cells"]
        ],
    }


#: The decisions agreed for Bronze on the 31 Aug 2026 book, loaded once
#: into an empty decisions table so the review opens with the team's
#: actual position rather than a blank sheet. Editable on screen; once
#: the table has rows this list is never consulted again.
SEED_DECISIONS: List[dict] = [
    {"product": "Bronze", "network_scope": "all", "network": None, "from_age": 0, "to_age": 1, "gender": None,
     "action": "increase", "change_pct": 100.0, "note": "Infants 0-1 ran at 215% gross (29 lives) - priced as a separate band from 2-17, on every network."},
    {"product": "Bronze", "network_scope": "excluding", "network": None, "from_age": 2, "to_age": 17, "gender": "F",
     "action": "discount", "change_pct": -10.0, "note": "Girls 2-17 cost 1,469 against 3,845 charged (38% gross) - room to compete; -10% keeps them at 42% gross."},
    {"product": "Bronze", "network_scope": "excluding", "network": None, "from_age": 2, "to_age": 17, "gender": "M",
     "action": "hold", "change_pct": 0.0, "note": "Boys 2-17 at 86% gross / 117% net - no room for a discount, not enough to raise."},
    {"product": "Bronze", "network_scope": "excluding", "network": None, "from_age": 18, "to_age": 35, "gender": "M",
     "action": "discount", "change_pct": -15.0, "note": "Males 18-35 at 52% gross, fully credible - room to compete."},
    {"product": "Bronze", "network_scope": "excluding", "network": None, "from_age": 26, "to_age": 35, "gender": "F",
     "action": "increase", "change_pct": 100.0, "note": "Females 26-35 at 158% gross (211 lives) - the single cell driving the 18-40 result. Capped at +100% for this round; cost-to-target is +127%."},
    {"product": "Bronze", "network_scope": "excluding", "network": None, "from_age": 18, "to_age": 25, "gender": "F",
     "action": "hold", "change_pct": 0.0, "note": "Held this round (34 lives, 109% gross) - watch."},
    {"product": "Bronze", "network_scope": "excluding", "network": None, "from_age": 36, "to_age": 40, "gender": None,
     "action": "hold", "change_pct": 0.0, "note": "Held this round (90-98% gross) - watch."},
    {"product": "Bronze", "network_scope": "all", "network": None, "from_age": 41, "to_age": 59, "gender": None,
     "action": "hold", "change_pct": 0.0, "note": "Held for now - 80% gross across all networks (males 74%, females 88%). Watch MSH Comprehensive 41-59, both sexes at 103-106%."},
    {"product": "Bronze", "network_scope": "all", "network": None, "from_age": 60, "to_age": 69, "gender": None,
     "action": "hold", "change_pct": 0.0, "note": "Thin data - 21 lives, 12 member-years, mostly one account. Revisit when the band passes 25 member-years."},
    {"product": "Bronze", "network_scope": "all", "network": None, "from_age": 70, "to_age": 99, "gender": None,
     "action": "increase", "change_pct": 30.0, "note": "Underwriting judgement, not book data - one life on risk (0.4 member-years, one AED 105k claim). +30% as a precaution for an age band the book has no experience in; refer any 70+ quote to an underwriter."},
    # Silver, agreed on the same book.
    {"product": "Silver", "network_scope": "all", "network": None, "from_age": 0, "to_age": 1, "gender": None,
     "action": "increase", "change_pct": 20.0, "note": "Infants 0-1 at 126-155% gross (43 lives) - a first step; still above 100% after it, re-check monthly."},
    {"product": "Silver", "network_scope": "all", "network": None, "from_age": 2, "to_age": 17, "gender": None,
     "action": "discount", "change_pct": -10.0, "note": "Children 2-17 at 50-55% gross (193 lives, fully credible) - room to compete on family groups."},
    {"product": "Bronze", "network_scope": "only", "network": "MSH Platinum", "from_age": 2, "to_age": 17, "gender": None,
     "action": "hold", "change_pct": 0.0, "note": "Platinum children 2-17 at 76-95% gross on thin data (56 lives) - hold."},
    {"product": "Bronze", "network_scope": "only", "network": "MSH Platinum", "from_age": 18, "to_age": 40, "gender": "F",
     "action": "increase", "change_pct": 30.0, "note": "Platinum females 18-40 at 146-181% gross, usage-driven (OP 2.4x, IP 3.4x other networks; maternity only 19% of claims). +30% agreed as the first step."},
    {"product": "Bronze", "network_scope": "only", "network": "MSH Platinum", "from_age": 18, "to_age": 40, "gender": "M",
     "action": "hold", "change_pct": 0.0, "note": "Held this round - 18-25 M carries one AED 209k claim; 26-35 M at 112% gross, 36-40 M at 128% on thin data - watch."},
]


# ---------------------------------------------------------------- nationality

ZONE_LABELS = {
    "zone_1_asia": "Asia",
    "zone_2_middle_east": "Middle East",
    "zone_3_europe_americas": "Europe & Americas",
}


def nationality_factors(
    members: List[dict],
    params: dict,
    min_member_years_named: float = 10.0,
) -> dict:
    """How far each nationality in one cell sits from the cell's own
    average cost - the factor a quote applies INSIDE a reviewed cell.

    The decision on a cell sets its base rate for the average member.
    This does not add to that: each nationality's raw ratio to the cell
    cost is credibility-blended toward its zone's ratio (itself blended
    toward 1.0), capped to the relativity range, and then the whole set is
    re-normalised so that, weighted by today's exposure, the factors
    average exactly 1.0. The book's premium does not move again; the
    factor only redistributes it between nationalities at quote time.

    A nationality below `min_member_years_named` is folded into its zone
    rather than shown on its own - a factor read off six member-years
    would be one family's claims wearing a country's name.
    """
    full_cred = float(params["full_credibility_member_years"])
    min_rel, max_rel = float(params["min_relativity"]), float(params["max_relativity"])
    cap = params.get("large_claim_cap")
    cap = float(cap) if cap not in (None, "", 0) else None

    cell = _totals(members, cap)
    cell_my = cell["member_years"]
    if not cell_my:
        return {"cell_cost_pmpy": None, "zones": [], "nationalities": [], "measured_share": 0.0}
    pooled = (cell["incurred"] - cell["priced_claims"]) / cell_my
    cell_cost = cell["priced_claims"] / cell_my + pooled

    def z_of(my: float) -> float:
        return min(1.0, math.sqrt(my / full_cred)) if my > 0 else 0.0

    by_zone: Dict[str, List[dict]] = defaultdict(list)
    by_nat: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    for r in members:
        zone = (r.get("nationality_zone") or "unmapped").strip()
        nat = (r.get("nationality") or "Unknown").strip().upper()
        by_zone[zone].append(r)
        by_nat[(zone, nat)].append(r)

    zone_rows = []
    zone_factor: Dict[str, float] = {}
    for zone, rows in by_zone.items():
        t = _totals(rows, cap)
        my = t["member_years"]
        own = ((t["priced_claims"] / my + pooled) / cell_cost) if (my and cell_cost) else 1.0
        z = z_of(my)
        blended = z * own + (1 - z) * 1.0
        blended = max(min_rel, min(max_rel, blended))
        zone_factor[zone] = blended
        zone_rows.append({
            "zone": zone, "zone_label": ZONE_LABELS.get(zone, zone),
            "lives": t["lives"], "member_years": round(my, 1),
            "premium_pmpy": round(t["premium"] / my, 2) if my else None,
            "cost_pmpy": round(t["incurred"] / my, 2) if my else None,
            "gross_loss_ratio": round(t["incurred"] / t["premium"], 4) if t["premium"] else None,
            "raw_factor": round(own, 4), "credibility": round(z, 4), "factor": blended,
        })

    nat_rows = []
    for (zone, nat), rows in by_nat.items():
        t = _totals(rows, cap)
        my = t["member_years"]
        z = z_of(my)
        own = ((t["priced_claims"] / my + pooled) / cell_cost) if (my and cell_cost) else zone_factor[zone]
        blended = z * own + (1 - z) * zone_factor[zone]
        blended = max(min_rel, min(max_rel, blended))
        nat_rows.append({
            "nationality": nat.title(), "zone": zone, "zone_label": ZONE_LABELS.get(zone, zone),
            "lives": t["lives"], "member_years": round(my, 1),
            "premium_pmpy": round(t["premium"] / my, 2) if my else None,
            "cost_pmpy": round(t["incurred"] / my, 2) if my else None,
            "gross_loss_ratio": round(t["incurred"] / t["premium"], 4) if t["premium"] else None,
            "raw_factor": round(own, 4), "credibility": round(z, 4),
            "factor": blended,
            # Named on its own only with enough exposure; otherwise it
            # takes its zone's factor and is listed for completeness.
            "named": my >= min_member_years_named,
            "_my": my,
        })
    for row in nat_rows:
        if not row["named"]:
            row["factor"] = zone_factor[row["zone"]]

    # Re-normalise on today's mix so the factors are a redistribution
    # within the cell, never a second increase on top of the decision.
    weighted = sum(r["factor"] * r["_my"] for r in nat_rows)
    scale = (cell_my / weighted) if weighted else 1.0
    for row in nat_rows:
        row["factor"] = round(row["factor"] * scale, 4)
        del row["_my"]
    for row in zone_rows:
        row["factor"] = round(row["factor"] * scale, 4)

    nat_rows.sort(key=lambda r: -r["member_years"])
    zone_rows.sort(key=lambda r: -r["member_years"])
    measured = sum(r["member_years"] for r in nat_rows if r["named"])
    return {
        "cell_cost_pmpy": round(cell_cost, 2),
        "normalisation": round(scale, 4),
        "zones": zone_rows,
        "nationalities": nat_rows,
        "measured_share": round(measured / cell_my, 4) if cell_my else 0.0,
    }


def reviewed_price_for_census(
    census: List[dict],
    reviews_by_scope: Dict[str, dict],
    factors_by_cell: Dict[Tuple[str, str, str], dict],
    params: dict,
    rate_cards: Optional[List[dict]] = None,
    categories_by_name: Optional[Dict[str, dict]] = None,
    variant_rates: Optional[List[dict]] = None,
) -> dict:
    """What a census costs on the REVIEWED card: each member's cell rate
    after the agreed decision, times that member's nationality factor
    inside the cell.

    `reviews_by_scope` maps a scope key ("<product>|excluding", or
    "<product>|only:<network>") to a review with decisions applied;
    `factors_by_cell` maps (scope key, age band, sex) to
    nationality_factors() output. A census may put its categories on
    different products (A on Platinum, B on Bronze) - each member is
    priced on the review for the product their own category is on. A member whose
    cell has no decision is priced at the cell's current rate and
    flagged, so the total can never quietly rest on an unreviewed cell.

    The starting rate is the member's OWN quoted card price: product,
    region, network, age and sex off the loaded rate card, the category's
    benefit-variant selections, and the category's expense/commission
    gross-up - i.e. exactly what the New Business quote charges for that
    member (`rate_cards`, `variant_rates`, and the category design in
    `categories_by_name`). So the reviewed rate is "the quote plus what
    we agreed", and the gap to the card price is only the agreed
    decisions and the nationality mix. Only when the card has no row for
    the member does it start from the review cell's earned premium, and
    the member says which (`rate_basis`).

    A cell with no decision is priced at the card, full stop - no
    nationality factor either. The factors belong to a reviewed cell; a
    product the team has not reviewed yet must come out exactly at card
    so nothing reads as agreed that was not.
    """
    from app.scoring.rules.new_business_rating import category_loading_pct, gross_up, price_member

    cards = rate_cards or []
    variants = variant_rates or []
    designs = categories_by_name or {}
    separate = params.get("separate_networks") or []
    bands = [tuple(b) for b in params["age_bands"]]
    members_out = []
    total = 0.0
    undecided = 0
    unmatched = 0
    card_members = 0
    factor_weighted = 0.0
    for m in census:
        net = (m.get("network") or "").strip()
        design = designs.get(m.get("category")) if designs else None
        product = (design or {}).get("product") or m.get("product") or ""
        scope_key = f"{product}|only:{net}" if _is_separate(net, separate) else f"{product}|excluding"
        review = reviews_by_scope.get(scope_key)
        band = _band_label(m.get("age"), bands)
        g = (m.get("gender") or "").strip().upper()[:1]
        cell = None
        if review and band and g in GENDERS:
            cell = next((c for c in review["cells"] if c["age_band"] == band and c["gender"] == g), None)
        if not cell or not cell.get("current_rate"):
            unmatched += 1
            members_out.append({**m, "product": product, "scope": scope_key, "age_band": band, "cell_rate": None, "reviewed_rate": None,
                                "nationality_factor": None, "price": None, "note": "no reviewed cell for this member"})
            continue
        pct = cell.get("decision_change_pct")
        decided = cell.get("decision") is not None
        if not decided:
            undecided += 1
            pct = 0.0
        base_rate, basis = cell["current_rate"], cell.get("current_rate_basis") or "earned premium"
        if cards and design and design.get("product") and design.get("network"):
            priced = price_member(m, {"product": design["product"], "network": design["network"], "tpa": design.get("tpa"),
                                      "variant_selections": design.get("variant_selections") or {}}, cards, variants)
            if priced.get("net_total"):
                loading = category_loading_pct(design["product"], design.get("commission_pct"), design.get("loading_pct"))
                base_rate = gross_up(priced["net_total"], loading)
                basis = "card"
        reviewed_rate = base_rate * (1 + (pct or 0.0) / 100)
        factor = 1.0
        f = factors_by_cell.get((scope_key, band, g)) if decided else None
        if f:
            nat = (m.get("nationality") or "").strip().upper()
            zone = (m.get("nationality_zone") or "").strip()
            row = next((r for r in f["nationalities"] if r["nationality"].upper() == nat and r["named"]), None)
            if row:
                factor = row["factor"]
            else:
                zrow = next((r for r in f["zones"] if r["zone"] == zone), None)
                if zrow:
                    factor = zrow["factor"]
        price = reviewed_rate * factor
        total += price
        factor_weighted += factor
        card_members += 1 if basis == "card" else 0
        members_out.append({
            **m, "product": product, "scope": scope_key, "age_band": band, "cell_rate": round(base_rate, 2), "rate_basis": basis,
            "decision_change_pct": pct if cell.get("decision") is not None else None,
            "reviewed_rate": round(reviewed_rate, 2), "nationality_factor": round(factor, 4),
            "price": round(price, 2),
            "note": None if decided else "cell has no decision yet - priced at card, no nationality factor",
        })
    priced = len(census) - unmatched
    return {
        "member_count": len(census),
        "priced_member_count": priced,
        "unmatched_member_count": unmatched,
        "undecided_member_count": undecided,
        "card_priced_member_count": card_members,
        "rate_basis": "card" if (priced and card_members == priced) else "earned premium" if card_members == 0 else "mixed",
        "reviewed_premium": round(total, 2),
        "reviewed_per_member": round(total / priced, 2) if priced else None,
        "average_nationality_factor": round(factor_weighted / priced, 4) if priced else None,
        "members": members_out,
    }


# ---------------------------------------------------------------- findings

def _lr_word(lr: Optional[float]) -> str:
    if lr is None:
        return "no experience"
    if lr > 1.0:
        return "losing money"
    if lr >= 0.85:
        return "close to break-even"
    return "profitable"


def underwriter_findings(
    case: dict,
    categories: List[dict],
    reviewed: dict,
    risk: dict,
    card_total: Optional[float],
    reviews: Dict[str, dict],
    params: dict,
    results: List[dict],
    lives: int,
    data_as_of: Optional[date],
) -> dict:
    """The one-page verdict on a new enquiry, from figures already computed.

    Three prices (card, reviewed, book cost), a recommendation with the
    rule that produced it, the reasons in plain words, the census laid
    against the book's own cells, the nationality mix, and the flags an
    underwriter checks before issuing. Nothing is priced here - it reads
    the reviewed price, the risk-based price and the reviews, so the page
    and the screens it summarises can never disagree.
    """
    products = reviewed.get("products") or [reviewed.get("product")]
    product = " + ".join(products)
    loading_by_product = {p: loading_for_product(params, p) for p in products}
    loading = loading_by_product[products[0]]
    members = reviewed["members"]
    priced = [m for m in members if m.get("price") is not None]

    # --- the census against the book's own cells
    groups: Dict[Tuple[str, str, str], dict] = {}
    for m in members:
        key = (m.get("scope") or "|excluding", m.get("age_band") or "?", (m.get("gender") or "?").upper()[:1])
        g = groups.setdefault(key, {"scope": key[0], "product": m.get("product") or "", "age_band": key[1], "gender": key[2], "members": 0, "reviewed_premium": 0.0, "priced": 0})
        g["members"] += 1
        if m.get("price") is not None:
            g["priced"] += 1
            g["reviewed_premium"] += m["price"]
    population = []
    for (scope, band, g), grp in groups.items():
        review = reviews.get(scope)
        cell = next((c for c in review["cells"] if c["age_band"] == band and c["gender"] == g), None) if review else None
        scope_part = scope.split("|", 1)[1] if "|" in scope else scope
        population.append({
            **grp,
            "scope_label": ((grp["product"] + " ") if len(products) > 1 else "") + (scope_part[5:] if scope_part.startswith("only:") else "standard networks"),
            "reviewed_rate": round(grp["reviewed_premium"] / grp["priced"], 2) if grp["priced"] else None,
            "reviewed_premium": round(grp["reviewed_premium"], 2),
            "book_lives": cell["lives"] if cell else None,
            "book_member_years": cell["member_years"] if cell else None,
            "book_gross_loss_ratio": cell["gross_loss_ratio"] if cell else None,
            "book_credibility": cell["credibility"] if cell else None,
            "book_thin": cell["thin"] if cell else None,
            "decision_action": (cell.get("decision_action") if cell and cell.get("decision") else "No decision yet") if cell else "No reviewed cell",
            "decision_change_pct": cell.get("decision_change_pct") if cell and cell.get("decision") else None,
            "from_age": cell["from_age"] if cell else 999,
        })
    population.sort(key=lambda p: (p["from_age"], p["gender"]))

    # Member-weighted book loss ratio of the cells this census sits in.
    weighted_n = sum(p["members"] for p in population if p["book_gross_loss_ratio"] is not None)
    weighted_lr = (
        sum(p["members"] * p["book_gross_loss_ratio"] for p in population if p["book_gross_loss_ratio"] is not None) / weighted_n
        if weighted_n else None
    )

    # --- nationality mix
    nat: Dict[str, dict] = {}
    for m in priced:
        name = (m.get("nationality") or "Unknown").strip().title()
        e = nat.setdefault(name, {"nationality": name, "members": 0, "factor_sum": 0.0})
        e["members"] += 1
        e["factor_sum"] += m.get("nationality_factor") or 1.0
    nationalities = sorted(
        [{"nationality": e["nationality"], "members": e["members"], "share": round(e["members"] / len(priced), 4) if priced else None,
          "factor": round(e["factor_sum"] / e["members"], 4)} for e in nat.values()],
        key=lambda r: -r["members"],
    )

    # --- the census's network(s) on the book, per product the census is on
    network_rows = []
    census_networks = sorted({(c.get("network") or "") for c in categories if c.get("network")})
    for prod in products:
        prod_networks = {(c.get("network") or "").strip() for c in categories if (c.get("product") or "") == prod}
        for n in network_breakdown(results, prod, loading_by_product[prod]):
            if n["network"] in prod_networks:
                network_rows.append({**n, "product": prod})

    # --- prices
    risk_total = risk.get("suggested_premium") if risk and "error" not in risk else None
    reviewed_total = reviewed.get("reviewed_premium") or None
    prices = {
        "card": {"total": card_total, "per_member": round(card_total / lives, 2) if card_total else None},
        "reviewed": {"total": reviewed_total, "per_member": reviewed.get("reviewed_per_member"),
                     "basis": reviewed.get("rate_basis"), "card_priced_members": reviewed.get("card_priced_member_count"),
                     "priced_members": reviewed["priced_member_count"], "undecided_members": reviewed["undecided_member_count"],
                     "unmatched_members": reviewed["unmatched_member_count"]},
        "book_cost": {"total": risk_total, "per_member": (risk or {}).get("suggested_per_member"),
                      "credibility": (risk or {}).get("weighted_credibility"), "trend_pct": (risk or {}).get("trend_pct"),
                      "error": (risk or {}).get("error")},
        "reviewed_vs_card_pct": round((reviewed_total / card_total - 1) * 100, 1) if (card_total and reviewed_total) else None,
        "book_cost_vs_reviewed_pct": round((risk_total / reviewed_total - 1) * 100, 1) if (risk_total and reviewed_total) else None,
    }

    # --- flags
    flags = []
    def flag(level, text):
        flags.append({"level": level, "text": text})
    if lives >= 50:
        flag("ok", f"Group size {lives} - no single claim decides the year.")
    elif lives >= 20:
        flag("watch", f"Group size {lives} - one large claim moves the year; hold margin.")
    else:
        flag("watch", f"Small group ({lives} lives) - price is a judgement more than a measurement.")
    if reviewed["unmatched_member_count"]:
        flag("watch", f"{reviewed['unmatched_member_count']} of {lives} members have no reviewed cell (age/network the book has not priced) - not in the reviewed rate.")
    else:
        flag("ok", f"All {lives} members priced on a reviewed cell.")
    if reviewed["undecided_member_count"]:
        flag("watch", f"{reviewed['undecided_member_count']} member(s) sit in cells with no rate decision yet - priced at today's rate.")
    basis = reviewed.get("rate_basis")
    if basis == "earned premium":
        flag("watch", "No rate card row for these members - the reviewed rate starts from the book's earned premium, not the card. Upload the rate card for a card-based figure.")
    elif basis == "mixed":
        flag("watch", f"{lives - (reviewed.get('card_priced_member_count') or 0)} member(s) have no rate card row and start from the book's earned premium instead of the card.")
    cred = prices["book_cost"]["credibility"]
    if cred is not None and cred < 0.5:
        flag("watch", f"Book cost price rests on cells below 50% credibility ({cred:.0%}) - treat it as a ceiling, not a target.")
    elif cred is not None:
        flag("ok", f"Book cost price is {cred:.0%} credible on this population's own cells.")
    missing = []
    if not case.get("industry") or str(case.get("industry")).strip().lower() in ("", "unknown", "-"):
        missing.append("industry")
    if not case.get("has_benefits"):
        missing.append("table of benefits")
    if not card_total:
        missing.append("rate card quote")
    if missing:
        flag("missing", "Not yet on file: " + ", ".join(missing) + ".")
    stale_days = int(params.get("stale_after_days") or 45)
    if data_as_of and (date.today() - data_as_of).days > stale_days:
        flag("watch", f"Book data is {(date.today() - data_as_of).days} days old - refresh before issuing.")
    thin_cells = [p for p in population if p["book_thin"]]
    if thin_cells:
        flag("watch", "Thin book experience for: " + ", ".join(f"{p['age_band']} {p['gender']}" for p in thin_cells) + ".")

    # --- recommendation
    unmatched_share = reviewed["unmatched_member_count"] / lives if lives else 0
    under_review = [p for p in population if p["decision_action"] == "review"]
    if unmatched_share > 0.2:
        rec, tone, rule = "Refer", "bad", f"{unmatched_share:.0%} of the census falls outside any reviewed cell."
    elif under_review:
        rec, tone, rule = "Refer", "warn", "Part of this census sits in cells still under review: " + ", ".join(f"{p['age_band']} {p['gender']}" for p in under_review) + "."
    elif weighted_lr is not None and weighted_lr > 1.10:
        rec, tone, rule = "Refer", "bad", f"The cells this census sits in run at {weighted_lr:.0%} gross on our book - above 110%."
    elif weighted_lr is not None and weighted_lr > 0.95:
        rec, tone, rule = "Quote at reviewed rate - no discount", "warn", f"Cells this census sits in run at {weighted_lr:.0%} gross; the reviewed rate carries the agreed corrections."
    else:
        rec, tone, rule = "Quote at reviewed rate", "good", f"Cells this census sits in run at {weighted_lr:.0%} gross on our book." if weighted_lr is not None else "No book experience for this population."

    # --- why, in plain words
    why = []
    for p in population[:3]:
        if p["book_gross_loss_ratio"] is None:
            why.append({"tone": "grey", "text": f"{p['members']} {'male' if p['gender']=='M' else 'female'}(s) aged {p['age_band']} - our book has no {p.get('product') or product} experience for this cell."})
            continue
        lr = p["book_gross_loss_ratio"]
        cred_txt = "fully credible" if (p["book_credibility"] or 0) >= 1 else f"{p['book_credibility']:.0%} credible"
        act = p["decision_action"]
        act_txt = ("hold" if act == "hold" else f"{act} {p['decision_change_pct']:+.0f}%" if p["decision_change_pct"] else act)
        why.append({
            "tone": "bad" if lr > 1.0 else "warn" if lr >= 0.85 else "good",
            "text": (f"{p['members']} {'male' if p['gender']=='M' else 'female'}{'s' if p['members']!=1 else ''} aged {p['age_band']} on {p['scope_label']} - "
                     f"on our book this cell runs at {lr:.0%} gross ({p['book_lives']} lives, {cred_txt}), {_lr_word(lr)}. Agreed action: {act_txt}."),
        })
    if nationalities:
        top = nationalities[0]
        why.append({
            "tone": "good" if top["factor"] < 0.95 else "bad" if top["factor"] > 1.05 else "grey",
            "text": (f"{top['share']:.0%} {top['nationality']} - inside their cells this nationality costs {top['factor']:.2f}x the cell average; "
                     f"the reviewed rate already reflects that."
                     + (" No further discount is justified." if top["factor"] < 0.95 else "")),
        })
    if prices["book_cost_vs_reviewed_pct"] is not None:
        gap = prices["book_cost_vs_reviewed_pct"]
        if abs(gap) >= 15:
            why.append({
                "tone": "warn",
                "text": (f"Book cost price is {abs(gap):.0f}% {'above' if gap > 0 else 'below'} the reviewed rate"
                         + (f" - it prices from thin cube cells ({cred:.0%} credibility); treat it as a ceiling, not a target." if (gap > 0 and cred is not None and cred < 0.5)
                            else " - the two views of this population disagree; the reviewed rate is the one the team signed off.")),
            })
    for n in network_rows:
        if n.get("gross_loss_ratio") is not None:
            why.append({
                "tone": "bad" if n["gross_loss_ratio"] > 1.0 else "good",
                "text": (f"{n['network']} runs at {n['gross_loss_ratio']:.0%} gross on {n.get('product') or product}"
                         + (f" and costs {n['cost_factor']:.1f}x the cheapest network while charging {n['premium_factor']:.1f}x" if n.get("cost_factor") and n.get("premium_factor") and n["cost_factor"] > 1.3 else "")
                         + (" - do not go below the reviewed rate on this network." if n["gross_loss_ratio"] > 0.95 else ".")),
            })
    females = sum(1 for m in members if (m.get("gender") or "").upper().startswith("F"))
    fem_18_40 = sum(1 for m in members if (m.get("gender") or "").upper().startswith("F") and m.get("age") is not None and 18 <= m["age"] <= 40)
    over_59 = sum(1 for m in members if m.get("age") is not None and m["age"] > 59)
    bits = []
    bits.append("No maternity exposure (no females)." if females == 0 else f"{fem_18_40} women aged 18-40 - maternity exposure.")
    bits.append("No members over 59." if over_59 == 0 else f"{over_59} member(s) over 59.")
    if missing:
        bits.append("Add " + " and ".join(missing) + " before issuing.")
    why.append({"tone": "grey", "text": " ".join(bits)})

    return {
        "case": case,
        "plan": {
            "product": product,
            "products": products,
            "networks": census_networks,
            "categories": [{"category": c.get("category"), "product": c.get("product"), "network": c.get("network"), "tpa": c.get("tpa")} for c in categories],
            "tpa": sorted({(c.get("tpa") or "") for c in categories if c.get("tpa")}),
            "loading_pct": loading,
            "loading_by_product": loading_by_product,
        },
        "lives": lives,
        "data_as_of": data_as_of.isoformat() if data_as_of else None,
        "prepared_on": date.today().isoformat(),
        "decisions_count": reviewed.get("decisions_count"),
        "target_loss_ratio": params["target_loss_ratio"],
        "large_claim_cap": params.get("large_claim_cap"),
        "recommendation": {"verdict": rec, "tone": tone, "rule": rule, "population_book_loss_ratio": round(weighted_lr, 4) if weighted_lr is not None else None},
        "prices": prices,
        "why": why,
        "population": population,
        "nationalities": nationalities,
        "networks": network_rows,
        "flags": flags,
    }
