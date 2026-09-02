"""What an account's numbers are actually saying, as a list of alerts.

Every screen in the portal reports figures and leaves the reading to the
person in front of it. That works while the reader knows the book: 51.4%
of incurred still outstanding is alarming, and 18% is ordinary, and
nothing on the screen says which is which. The reader who does not
already know that sees two numbers of equal weight.

So the reading is written down here, once, as rules with named
thresholds. A rule carries four things and the screens render all four:

    value       what this account did
    threshold   the line it crossed
    rule        the comparison, in text, so it can be argued with
    action      what to do about it before pricing

The last one is the point. "Loss ratio 248.6%" is a fact; "this is not a
rate-increase conversation, restructure or decline" is the underwriting
decision that fact implies, and the difference between them is the whole
value of the alert.

These are READINGS, not gates. Nothing here blocks a working or changes
a premium - the one alert that reflects a genuine block (an account
loading that was never entered) reports a block imposed elsewhere, by
app.api.case_loading, rather than imposing its own. Keeping the alerts
advisory is what lets the thresholds be tuned without any risk of
changing a price.
"""
from typing import Dict, List, Optional

from app.scoring.rules.experience_pricing import HOUSE_TARGET_LOSS_RATIO

#: Ordered worst-first. The UI colours by severity and the list sorts by
#: it, so an account with six readings still leads with the one that
#: decides whether to quote at all.
SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_WATCH = "watch"

SEVERITY_ORDER = {SEVERITY_CRITICAL: 0, SEVERITY_HIGH: 1, SEVERITY_WATCH: 2}

#: Above this the account is not mispriced, it is unpriceable at any
#: increase a client would sign: 160% of earned premium needs the ask to
#: roughly double before the loading is even covered, and an account that
#: would accept that would have accepted less. Deliberately well clear of
#: HOUSE_TARGET_LOSS_RATIO so the two tiers say different things - above
#: target means price it harder, above this means change the risk.
CRITICAL_LOSS_RATIO = 1.60

#: Outstanding is the reserve the TPA is holding, not a settled cost, so
#: a high share means the incurred figure is still going to move - and
#: every loss ratio, trended loss ratio and required premium on the
#: screen moves with it. The book's own median sits near 18%.
OUTSTANDING_SHARE_HIGH = 0.35

#: One member carrying this much of a book is a fact about that member,
#: not about the group, and trending it prices next year's premium for an
#: event that may well not repeat. Above the critical line the "group"
#: loss ratio is really one person's medical history.
TOP_CLAIMANT_SHARE_HIGH = 0.20
TOP_CLAIMANT_SHARE_CRITICAL = 0.35

#: Under half a policy year the loss ratio is real but not yet credible -
#: a single inpatient admission in month two sets the ratio for the whole
#: term. Reported so the figure is labelled indicative wherever it leaves
#: the building, not so it is ignored.
CREDIBILITY_FLOOR_DAYS = 180


def _pct(value: Optional[float]) -> str:
    return f"{value * 100:.1f}%" if value is not None else "n/a"


def _amount(value: Optional[float]) -> str:
    return f"{value:,.0f}" if value is not None else "n/a"


def _alert(
    code: str,
    severity: str,
    title: str,
    message: str,
    rule: str,
    action: str,
    value: Optional[float] = None,
    threshold: Optional[float] = None,
) -> dict:
    return {
        "code": code,
        "severity": severity,
        "title": title,
        "message": message,
        "rule": rule,
        "action": action,
        "value": value,
        "threshold": threshold,
    }


def _loss_ratio_alert(row: dict, target_loss_ratio: float) -> Optional[dict]:
    loss_ratio = row.get("gross_loss_ratio")
    if loss_ratio is None:
        return None
    if loss_ratio > CRITICAL_LOSS_RATIO:
        return _alert(
            "loss_ratio_critical",
            SEVERITY_CRITICAL,
            "Loss ratio above tolerance",
            f"Earned loss ratio {_pct(loss_ratio)} against a maximum target of "
            f"{_pct(target_loss_ratio)}.",
            f"gross_loss_ratio > {CRITICAL_LOSS_RATIO:.2f}",
            "This is not a rate-increase conversation. Restructure the benefits, "
            "re-underwrite, or decline - the increase that would cover this "
            "experience is not one the account will sign.",
            value=loss_ratio,
            threshold=CRITICAL_LOSS_RATIO,
        )
    if loss_ratio > target_loss_ratio:
        return _alert(
            "loss_ratio_above_target",
            SEVERITY_HIGH,
            "Loss ratio above target",
            f"Earned loss ratio {_pct(loss_ratio)} against a maximum target of "
            f"{_pct(target_loss_ratio)}.",
            f"gross_loss_ratio > {target_loss_ratio:.2f}",
            "The renewal needs the full computed increase. Check the Method 1 "
            "ladder before discounting it.",
            value=loss_ratio,
            threshold=target_loss_ratio,
        )
    return None


def _outstanding_alert(row: dict, book_median_outstanding_share: Optional[float]) -> Optional[dict]:
    incurred = row.get("incurred_claims")
    outstanding = row.get("outstanding")
    if not incurred or outstanding is None:
        return None
    share = outstanding / incurred
    if share <= OUTSTANDING_SHARE_HIGH:
        return None
    against = (
        f", against a book median of {_pct(book_median_outstanding_share)}"
        if book_median_outstanding_share is not None
        else ""
    )
    return _alert(
        "outstanding_exposure",
        SEVERITY_HIGH,
        "Outstanding exposure",
        f"{_amount(outstanding)} outstanding - {_pct(share)} of incurred{against}.",
        f"outstanding / incurred > {OUTSTANDING_SHARE_HIGH:.2f}",
        "The incurred figure will still move. Confirm the reserves with the TPA "
        "before the quote goes out, because every loss ratio on this screen moves "
        "with them.",
        value=share,
        threshold=OUTSTANDING_SHARE_HIGH,
    )


def _concentration_alert(
    top_claimant_share: Optional[float],
    top_claimant_amount: Optional[float],
) -> Optional[dict]:
    if top_claimant_share is None or top_claimant_share <= TOP_CLAIMANT_SHARE_HIGH:
        return None
    critical = top_claimant_share > TOP_CLAIMANT_SHARE_CRITICAL
    amount = f" ({_amount(top_claimant_amount)})" if top_claimant_amount is not None else ""
    return _alert(
        "claim_concentration",
        SEVERITY_CRITICAL if critical else SEVERITY_HIGH,
        "Claim concentration",
        f"One member is {_pct(top_claimant_share)} of incurred{amount}.",
        f"top claimant share > "
        f"{(TOP_CLAIMANT_SHARE_CRITICAL if critical else TOP_CLAIMANT_SHARE_HIGH):.2f}",
        "Decide whether it recurs before it is trended. If it does not, strip it "
        "in the adjustments step rather than pricing next year for it.",
        value=top_claimant_share,
        threshold=TOP_CLAIMANT_SHARE_CRITICAL if critical else TOP_CLAIMANT_SHARE_HIGH,
    )


def _credibility_alert(row: dict) -> Optional[dict]:
    days = row.get("days")
    if days is None or row.get("expired") or days >= CREDIBILITY_FLOOR_DAYS:
        return None
    return _alert(
        "experience_immature",
        SEVERITY_WATCH,
        "Experience immature",
        f"Only {days} of 365 days have run.",
        f"days < {CREDIBILITY_FLOOR_DAYS}",
        "Label the loss ratio indicative on anything that leaves the building. "
        "One admission this early sets the ratio for the whole term.",
        value=float(days),
        threshold=float(CREDIBILITY_FLOOR_DAYS),
    )


def _loading_alerts(loading_problems: Optional[List[dict]]) -> List[dict]:
    """A renewal whose fee split was never entered cannot be priced at all
    (app.api.case_loading), and until now the only place that said so was
    the pricing panel itself - so the account looked ordinary everywhere
    else. Surfaced here it reads as what it is: the first thing to fix."""
    if not loading_problems:
        return []
    return [
        _alert(
            "loading_not_entered",
            SEVERITY_CRITICAL,
            "Account loading not entered",
            problem.get("message") or "The renewal loading is not set on this case.",
            "renewal loading is never defaulted",
            "Enter the fee split on the case record. Enter 0 for a fee the account "
            "genuinely does not pay - zero is an answer, blank is not.",
        )
        for problem in loading_problems
    ]


def underwriting_alerts(
    row: Optional[dict],
    top_claimant_share: Optional[float] = None,
    top_claimant_amount: Optional[float] = None,
    book_median_outstanding_share: Optional[float] = None,
    loading_problems: Optional[List[dict]] = None,
    target_loss_ratio: float = HOUSE_TARGET_LOSS_RATIO,
) -> List[dict]:
    """The readings for one account, worst first.

    `row` is one row of portfolio_analysis.account_loss_ratio_rows - the
    same row the Loss Ratio screen and the renewal working already use,
    so an alert can never disagree with the figure it is drawn from. An
    account with no row (nothing uploaded for it yet) still returns any
    alerts that do not depend on one.
    """
    alerts: List[dict] = list(_loading_alerts(loading_problems))
    if row:
        for candidate in (
            _loss_ratio_alert(row, target_loss_ratio),
            _outstanding_alert(row, book_median_outstanding_share),
            _concentration_alert(top_claimant_share, top_claimant_amount),
            _credibility_alert(row),
        ):
            if candidate is not None:
                alerts.append(candidate)
    else:
        concentration = _concentration_alert(top_claimant_share, top_claimant_amount)
        if concentration is not None:
            alerts.append(concentration)

    alerts.sort(key=lambda a: SEVERITY_ORDER.get(a["severity"], len(SEVERITY_ORDER)))
    return alerts


def alert_counts(alerts: List[dict]) -> Dict[str, int]:
    """Per-severity tally for the nav badge, always with all three keys so
    a caller can render "0 critical" without checking for the key."""
    counts = {severity: 0 for severity in SEVERITY_ORDER}
    for alert in alerts:
        if alert["severity"] in counts:
            counts[alert["severity"]] += 1
    return counts
