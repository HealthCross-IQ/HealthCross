"""Reading the book: rows out of the database, as plain dicts.

Every query against the uploaded membership, claims and mapping tables
lives here, so a caller that needs the book's members asks for the
book's members rather than writing its own query with its own idea of
which columns matter. Two views of "a member" that disagree about the
premium column is how the Renewal scorecard came to report NOMADA at
75.6% while the Loss Ratio screen had it at 83.6%.

Nothing here knows about a Case, a quote or a renewal, and nothing here
computes anything - see analysis.py for what the book MEANS.
"""
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import db_models as models
from app.scoring.rules.portfolio_analysis import (
    normalize_subgroup_key,
    resolve_group_product,
    resolve_master_client,
)


# --- what the book is measured to ----------------------------------------

def stored_as_of(db: Session) -> Optional[date]:
    """The extract's own production date, if one was recorded on upload."""
    snapshot = db.query(models.PortfolioDataSnapshot).first()
    return snapshot.data_as_of_date if snapshot else None


def set_stored_as_of(db: Session, as_of_date: date) -> None:
    snapshot = db.query(models.PortfolioDataSnapshot).first()
    if snapshot:
        snapshot.data_as_of_date = as_of_date
    else:
        db.add(models.PortfolioDataSnapshot(data_as_of_date=as_of_date))
    db.commit()


def covered_to(db: Session) -> Optional[date]:
    """The last day the uploaded claims actually cover.

    Used when nobody has told us a report date. The alternative was
    today, and today is always wrong in the same direction: it earns
    premium through weeks the claims file does not reach, so the ratio
    reads better the longer the extract sits. NOMADA measured 83.6% to
    15 August and 79.3% to today, on identical data.
    """
    return db.query(func.max(models.PortfolioClaimEntry.date_of_treatment)).scalar()


def measurement_date(db: Session, supplied: Optional[date] = None) -> Optional[date]:
    """The day the book's numbers are measured to, in the order that
    keeps them honest: what the caller asked for, then the extract's own
    production date if one was recorded on upload, then None - which
    lets the rules fall back to the last day the claims data covers.

    Never today. Earning premium to today against claims that stop at
    the extract date credits an account with premium for weeks nobody
    has reported a claim in yet, and it flatters every ratio by more the
    longer the extract sits.
    """
    return supplied or stored_as_of(db)


def has_members(db: Session) -> bool:
    """Whether anything has been uploaded at all.

    A hand-built case on an empty book is a normal state, not an error,
    and a caller with its own fallback needs to ask without catching a
    400 to find out.
    """
    return db.query(models.PortfolioMember.id).first() is not None


# --- the roster ----------------------------------------------------------

def members(db: Session) -> List[dict]:
    return [
        {
            "beneficiary_id": m.beneficiary_id,
            "contract": m.contract,
            "master_contract": m.master_contract,
            "master_client_name": m.master_client_name,
            "product_name": m.product_name,
            "category": m.category,
            "network_type_raw": m.network_type_raw,
            "date_of_birth": m.date_of_birth,
            "age": m.age,
            "gender": m.gender,
            "marital_status": m.marital_status,
            "relation": m.relation,
            "nationality": m.nationality,
            "nationality_zone": m.nationality_zone,
            "residence_emirate": m.residence_emirate,
            "region": m.region,
            "actual_gross_premium": m.actual_gross_premium,
            # The Membership export carries BOTH a booked GrossPremium and
            # an ActualGrossPremium; only the latter has ever fed the
            # analysis. Carried through so a caller can report on either -
            # see account_loss_ratio_rows' premium_basis.
            "gross_premium": m.gross_premium,
            "policy_start_date": m.policy_start_date,
            "policy_end_date": m.policy_end_date,
            "member_start_date": m.member_start_date,
            "member_end_date": m.member_end_date,
        }
        for m in db.query(models.PortfolioMember).all()
    ]


# --- the claims ----------------------------------------------------------

def claims(db: Session) -> List[dict]:
    """The four fields the member-level analysis joins on. Deliberately
    not every column - see large_claim_lines for the wide read."""
    return [
        {
            "patient_id": patient_id,
            "date_of_treatment": date_of_treatment,
            "final_amount": final_amount,
            "claim_status": claim_status,
        }
        for patient_id, date_of_treatment, final_amount, claim_status in db.query(
            models.PortfolioClaimEntry.patient_id,
            models.PortfolioClaimEntry.date_of_treatment,
            models.PortfolioClaimEntry.final_amount,
            models.PortfolioClaimEntry.claim_status,
        ).all()
    ]


def large_claim_lines(db: Session) -> List[dict]:
    """Every uploaded claim line's own group_name/client_name/provider_name
    are already denormalized onto PortfolioClaimEntry itself (a book-wide
    export carries its own group identity per row - see
    app/ingestion/portfolio_claims.py), but that denormalized client_name
    is the raw SUBGROUP name on the claims export, not the master policy -
    the same subgroup fragmentation the analysis resolves away via
    resolve_master_client for every other view. `client_name` here is
    overridden with the resolved master client name (falling back to the
    claim's own raw client_name only for a patient_id with no matching
    PortfolioMember row), so large-claims/high-cost-member analysis rolls
    up by master client just like everything else, instead of splintering
    one group across its own subgroups.
    """
    master_by_beneficiary = master_client_by_beneficiary(db)
    product_by_ben = product_by_beneficiary(db)

    rows = db.query(
        models.PortfolioClaimEntry.patient_id,
        models.PortfolioClaimEntry.group_name,
        models.PortfolioClaimEntry.client_name,
        models.PortfolioClaimEntry.provider_name,
        models.PortfolioClaimEntry.diagnosis_description,
        # The CODE as well as the description. Everything that classifies a
        # claim as chronic reads the code's ICD-10 chapter
        # (app.reference.icd10_chapters), and a line that arrives without
        # one is not classified as anything - so claims performance
        # reported chronic spend of nil on accounts whose claims file
        # carries the codes. A description alone cannot stand in: it is
        # free text an operator typed.
        models.PortfolioClaimEntry.diagnosis_code,
        models.PortfolioClaimEntry.date_of_treatment,
        # When the claim was actually received, not just treated - the
        # pair a real completion-factor IBNR needs (see
        # app.scoring.rules.claims_completion). NULL on anything ingested
        # before that field existed, which callers must not read as "not
        # yet received" - see that module's own handling of the gap.
        models.PortfolioClaimEntry.date_reception,
        models.PortfolioClaimEntry.final_amount,
        # A stable per-line identifier, so a UI can let someone toggle one
        # specific claim line in or out of a calculation (a large claim
        # flagged for exclusion) and mean the same line on the next call -
        # patient_id + amount + date is not unique enough on its own.
        models.PortfolioClaimEntry.claim_id,
        # The pair top_members_by_total_claims' member_status reads - same
        # "member_end_date >= policy_end_date is Active" convention
        # claims_ledger_analysis.top_patients_by_final_amount already uses
        # for a single case's own ledger, applied here book-wide.
        models.PortfolioClaimEntry.policy_end_date,
        models.PortfolioClaimEntry.member_end_date,
    ).all()
    return [
        {
            "patient_id": patient_id,
            "group_name": group_name,
            "client_name": master_by_beneficiary.get(patient_id) or client_name,
            "product": product_by_ben.get(patient_id),
            "provider_name": provider_name,
            "diagnosis_description": diagnosis_description,
            "diagnosis_code": diagnosis_code,
            "date_of_treatment": date_of_treatment,
            "date_reception": date_reception,
            "final_amount": final_amount,
            "claim_id": claim_id,
            "policy_end_date": policy_end_date,
            "member_end_date": member_end_date,
        }
        for patient_id, group_name, client_name, provider_name, diagnosis_description,
        diagnosis_code, date_of_treatment, date_reception, final_amount, claim_id,
        policy_end_date, member_end_date in rows
    ]


# --- how names resolve ---------------------------------------------------

def subgroup_master_by_name(db: Session) -> Dict[str, str]:
    """Subgroup name -> the master policy it rolls up to. One account
    spread across seven booked entities is one account to an underwriter."""
    return {
        normalize_subgroup_key(sm.subgroup_name): sm.master_name
        for sm in db.query(models.SubgroupMasterMapping).all()
    }


def group_product_by_name(db: Session) -> Dict[str, str]:
    return {gp.group_name: gp.product for gp in db.query(models.GroupProductMapping).all()}


def master_client_by_beneficiary(db: Session) -> Dict[str, str]:
    """beneficiary_id -> resolved master client name, for attributing a
    raw claim line (which only carries a patient_id, not a master client)
    to the same master client every other view uses - see
    resolve_master_client. Shared by every claims-only view (Large
    Claims, Utilization of Benefits) that needs to roll up or filter by
    master client without a full membership/rate-card join.
    """
    by_subgroup = subgroup_master_by_name(db)
    return {
        m.beneficiary_id: resolve_master_client(
            {"contract": m.contract, "master_contract": m.master_contract,
             "master_client_name": m.master_client_name},
            by_subgroup,
        )
        for m in db.query(
            models.PortfolioMember.beneficiary_id, models.PortfolioMember.contract,
            models.PortfolioMember.master_contract, models.PortfolioMember.master_client_name,
        ).all()
        if m.beneficiary_id
    }


def product_by_beneficiary(db: Session) -> Dict[str, str]:
    """beneficiary_id -> resolved Product (Platinum/Gold/Silver/Bronze/
    Group - see resolve_group_product), for attributing a raw claim line
    to the same Product every other view uses. Shared by the claims-only
    views (Large Claims, Utilization of Benefits) that need to filter by
    Product without a full membership/rate-card join.
    """
    by_group = group_product_by_name(db)
    return {
        m.beneficiary_id: resolve_group_product(
            {"contract": m.contract, "master_contract": m.master_contract, "product_name": m.product_name},
            by_group,
        )
        for m in db.query(
            models.PortfolioMember.beneficiary_id, models.PortfolioMember.contract,
            models.PortfolioMember.master_contract, models.PortfolioMember.product_name,
        ).all()
        if m.beneficiary_id
    }


def network_region_by_beneficiary(db: Session) -> Dict[str, dict]:
    """beneficiary_id -> {"network", "region"}, resolved the same way
    analyze_portfolio_member does (map_network_type off the member's own
    raw network type; region is already a stored, ingestion-resolved
    column) - for scoping a claims-only view (Utilization, provider cost
    comparisons) to one network/region without a full membership/rate-
    card join.
    """
    from app.reference.network_type_mapping import map_network_type

    return {
        m.beneficiary_id: {"network": map_network_type(m.network_type_raw), "region": m.region}
        for m in db.query(
            models.PortfolioMember.beneficiary_id, models.PortfolioMember.network_type_raw,
            models.PortfolioMember.region,
        ).all()
        if m.beneficiary_id
    }


def opex_records_by_client(db: Session) -> Dict[str, List[dict]]:
    """Each master client's own expense loading over time, for the NET
    loss ratio. An account with a recorded opex is struck against its
    own, not the book's average."""
    from collections import defaultdict

    records: Dict[str, List[dict]] = defaultdict(list)
    for cm in db.query(models.ClientMasterInfo).all():
        if cm.opex_pct is not None:
            records[cm.master_client_name].append(
                {"start_date": cm.start_date, "end_date": cm.end_date, "opex_pct": cm.opex_pct}
            )
    return records


# --- the rate card the book is priced against ----------------------------

def rate_cards(db: Session) -> List[dict]:
    return [
        {
            "product": r.product,
            "region": r.region,
            "network": r.network,
            "tpa": r.tpa,
            "from_age": r.from_age,
            "to_age": r.to_age,
            "male_price": r.male_price,
            "female_price": r.female_price,
            "married_female_surcharge": r.married_female_surcharge,
        }
        for r in db.query(models.RateCard).all()
    ]


def variant_rates(db: Session) -> List[dict]:
    return [
        {
            "variant_name": r.variant_name,
            "option_value": r.option_value,
            "direction": r.direction,
            "impact_type": r.impact_type,
            "impact_value": r.impact_value,
            "region": r.region,
            "tpa": r.tpa,
            "network": r.network,
        }
        for r in db.query(models.BenefitVariantRate).all()
    ]
