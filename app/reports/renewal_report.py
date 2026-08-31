"""One document for a renewal account, for the team to read and print.

The Renewal Bench printed as seven pages of cards laid out for a screen,
and an underwriter had to assemble the argument themselves from figures
scattered across them. This is the argument: what the account cost, what
it therefore needs, and what the claims are made of - on one page, in the
order the conversation actually goes.

Every figure comes off the book (the Portfolio Loss Ratio rows), so the
document cannot disagree with the screen it was read from. Where a number
is measured on a different basis to the one beside it - and on a renewal
two of them always are - the basis is named next to it rather than left
for the reader to infer.

Shares the stylesheet and helpers with underwriting_report.py, so the two
documents look like they came from the same firm.
"""
from datetime import date
from typing import List, Optional

from app.reports.underwriting_report import (
    _HEAD,
    STYLESHEET,
    _footer,
    _logo_img,
    _note,
    _page,
    aed,
    area_chart,
    bar_rows,
    esc,
    long_date,
    pct,
)


#: What this document adds to the shared stylesheet. Kept here rather
#: than pushed into underwriting_report's sheet so the two documents stay
#: independently editable - a change made for the renewal page cannot
#: silently reflow the new business one.
EXTRA_CSS = """
.callout{background:var(--sky-25);border-left:3px solid var(--sky);padding:13px 16px;margin:0 0 26px;font-size:12.5px;line-height:1.6}
.callout strong{color:var(--navy)}
table.t{width:100%;border-collapse:collapse;font-size:11.5px;margin:0}
table.t th{font-family:var(--mono);font-size:8.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:600;text-align:left;padding:7px 16px 7px 0;border-bottom:1px solid var(--rule);white-space:nowrap}
table.t th:last-child,table.t td:last-child{padding-right:0}
table.t th.num,table.t td.num{text-align:right}
table.t td{padding:8px 16px 8px 0;border-bottom:1px solid var(--rule);vertical-align:top}
table.t tr:last-child td{border-bottom:0}
table.t td.num{font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap;color:var(--navy)}
table.t td.note{color:var(--muted);font-size:10.5px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:26px}
.mixrow{display:flex;align-items:center;gap:9px;margin-bottom:4px;font-size:11px}
.mixlab{width:64px;color:var(--muted);flex:none}
.mixbar{flex:1;background:var(--sky-25);height:9px;position:relative}
.mixbar i{position:absolute;inset:0 auto 0 0;background:var(--sky);display:block}
.chip{display:inline-block;font-family:var(--mono);font-size:8.5px;text-transform:uppercase;letter-spacing:.06em;background:var(--sky-25);color:var(--navy);padding:2px 6px;margin-right:5px;border-radius:2px}
.chip.warn{background:var(--warn-wash);color:var(--warn)}
.mixval{width:30px;text-align:right;font-family:var(--mono);flex:none;color:var(--navy)}
@media print{.toolbar{display:none}body{background:#fff;padding:0}.doc{box-shadow:none;border:0}.page-tag{display:none}section{break-inside:avoid}}
"""


def _kpi(label: str, value: str, foot: str = "", tone: str = "") -> str:
    return (f'<div class="kpi"><span class="l">{label}</span>'
            f'<span class="v {tone}">{value}</span>'
            f'{f"<span class=f>{foot}</span>" if foot else ""}</div>')


def _ratio_tone(ratio: Optional[float]) -> str:
    if ratio is None:
        return ""
    if ratio >= 1.0:
        return "bad"
    return "warn" if ratio >= 0.85 else "good"


def _rows(pairs: List[tuple]) -> str:
    """A plain two-column table. Used wherever a chart would decorate
    rather than inform - three premiums do not need a bar chart.
    """
    body = "".join(
        f'<tr><td>{label}</td><td class="num">{value}</td>'
        f'<td class="note">{note}</td></tr>'
        for label, value, note in pairs
    )
    return f'<table class="t"><tbody>{body}</tbody></table>'


def _masthead(payload: dict) -> str:
    case = payload["case"]
    return (f'<div class="masthead"><div class="logo">{_logo_img(34)}</div>'
            f'<div class="meta">Renewal Review<br>{esc(case.get("company_name"))}</div></div>')


def _identity(payload: dict, today: date) -> str:
    case, book = payload["case"], payload["book"]
    bits = [b for b in (case.get("broker_name"), case.get("product"),
                        f'{book.get("member_count")} lives') if b]
    return (
        f'<div class="pad" style="padding-top:22px">'
        f'<div class="eyebrow">Renewal review &middot; internal</div>'
        f'<h1>{esc(case.get("company_name"))}</h1>'
        f'<p class="desc">{" &middot; ".join(esc(b) for b in bits)}. '
        f'Experience to {esc(book.get("as_of"))}, {book.get("days")} days on risk. '
        f'Prepared {long_date(today)}.</p></div>'
    )


def _headline(payload: dict) -> str:
    book, rating = payload["book"], payload["rating"]
    inc = rating.get("renewal_increase_pct")
    return (
        '<div class="kpis">'
        + _kpi("Gross earned loss ratio", pct(book.get("gross_loss_ratio")),
               f'{aed(book.get("incurred_claims"))} &divide; {aed(book.get("earned_premium"))} earned',
               _ratio_tone(book.get("gross_loss_ratio")))
        + _kpi("Net earned loss ratio", pct(book.get("net_loss_ratio")),
               f'net of the {pct(book.get("loading_pct"))} book expense allowance',
               _ratio_tone(book.get("net_loss_ratio")))
        + _kpi("Required premium", aed(rating.get("required_premium")),
               f'{pct(rating.get("required_share_of_expiring"))} of the annualised expiring premium')
        + _kpi("Renewal increase", ("+" if (inc or 0) >= 0 else "") + f'{inc}%' if inc is not None else "&mdash;",
               f'vs {aed(rating.get("renewal_base_premium"))} annualised expiring',
               "bad" if (inc or 0) >= 15 else ("warn" if (inc or 0) > 0 else "good"))
        + "</div>"
    )


def _the_ask(payload: dict) -> str:
    """The sentence the meeting is about, written out rather than left to
    be assembled from four tiles.
    """
    book, rating = payload["book"], payload["rating"]
    inc, base = rating.get("renewal_increase_pct"), rating.get("renewal_base_premium")
    if inc is None or base is None:
        return ""
    gross = book.get("gross_loss_ratio")
    direction = "an increase of" if inc >= 0 else "a reduction of"
    verdict = (
        "The account is paying less than it costs." if (gross or 0) >= 1.0
        else "The account is inside its gross target but not its net one."
        if (book.get("net_loss_ratio") or 0) >= 1.0
        else "The account is running to plan."
    )
    ladder = payload.get("ladder") or {}
    return (
        f'<div class="callout"><strong>{verdict}</strong> The account runs at {pct(gross)}; '
        f'carried forward with {pct(ladder.get("inflation_pts"))} of claims inflation that is '
        f'<strong>{pct(ladder.get("trended_loss_ratio"))}</strong>, and grossed up for a '
        f'{pct(ladder.get("loading_pct"))} loading it needs '
        f'{pct(ladder.get("required_share_of_expiring"))} of the expiring premium. '
        f'On {aed(base)} annualised that is <strong>{aed(rating.get("required_premium"))}</strong> '
        f'&mdash; {direction} <strong>{abs(inc)}%</strong>.</div>'
    )


def _premiums(payload: dict) -> str:
    """The three premiums, named. They differ on every renewal with
    mid-term movement, and an increase quoted off the wrong one is wrong
    silently - which is the failure this section exists to prevent.
    """
    book, rating = payload["book"], payload["rating"]
    expiring = rating.get("expiring_premium") or {}
    case_premium = rating.get("case_current_annual_premium")
    rows = [
        ("Earned premium", aed(book.get("earned_premium")),
         f'what {book.get("days")} days on risk actually earned &mdash; the loss ratio divides by this'),
        ("Annual gross (pro-rata)", aed(book.get("gross_premium")),
         "includes part-year premium for members added and deleted mid-term"),
        ("Annualised expiring", aed(rating.get("renewal_base_premium")),
         esc(expiring.get("source") or "a full year at current rates") +
         f' &mdash; the renewal is quoted against this'),
    ]
    if case_premium:
        rows.append(("On the case record", aed(case_premium),
                     "typed on the case" + (" &mdash; <strong>disagrees with the book</strong>"
                                            if rating.get("premium_disagrees_with_book") else "")))
    return (
        '<section><h2 class="sec">Premium</h2>'
        '<p class="desc">A renewal covers a whole year for the members who stay, so it is priced '
        'off the annualised expiring premium. The loss ratio is a different question and divides '
        'by what was earned.</p>'
        + _rows(rows) + "</section>"
    )


def _buildup_bars(book: dict) -> str:
    """Paid, then paid + outstanding, then the whole incurred figure -
    each bar the running total, so the reader sees what each step adds
    rather than three unrelated lengths.
    """
    incurred = book.get("incurred_claims") or 0
    paid = book.get("paid") or 0
    reported = paid + (book.get("outstanding") or 0)
    steps = [("Paid", paid, ""), ("+ Outstanding", reported, ""),
             ("+ IBNR = incurred", incurred, "solid")]
    return bar_rows([
        {"label": label, "value_text": aed(value),
         "width": f"{(value / incurred * 100) if incurred else 0:.1f}%",
         "fill": fill, "key": fill == "solid",
         "value_class": "key" if fill == "solid" else ""}
        for label, value, fill in steps
    ], label_width=150)


def _ladder(payload: dict) -> str:
    """The renewal arithmetic, step by step.

    A ratio carried forward, not absolute claims: the claims were earned
    against the pro-rata premium, so annualising them and dividing by the
    larger expiring premium mixes two bases and silently improves the
    loss ratio.
    """
    L = payload.get("ladder") or {}
    if not L.get("required_premium"):
        return ""
    rows = [
        ("Loss ratio", pct(L.get("loss_ratio")), "incurred claims &divide; premium earned over the same days"),
        (f'+ Claims inflation {pct(L.get("inflation_pts"))}', pct(L.get("trended_loss_ratio")),
         "added in points, the house convention"),
        (f'&divide; (1 &minus; {pct(L.get("loading_pct"))} loading)',
         pct(L.get("experience_share_of_expiring")),
         "the loading is a share of the premium, so the part funding claims is premium &times; (1 &minus; loading)"),
    ]
    if L.get("floor_applied"):
        rows.append((
            f'House floor &mdash; {pct(L.get("minimum_increase_pct"))} minimum',
            pct(L.get("required_share_of_expiring")),
            f'this account&rsquo;s own experience asks '
            f'{"+" if L.get("experience_increase_pct", 0) >= 0 else ""}'
            f'{L.get("experience_increase_pct")}%, below the house minimum',
        ))
    rows += [
        ("Annualised expiring premium", aed(L.get("expiring_annual_premium")),
         "a full year at current rates for the renewing headcount"),
        ("<strong>Required premium</strong>",
         f'<strong>{aed(L.get("required_premium"))}</strong>',
         f'<strong>{("+" if L.get("renewal_increase_pct", 0) >= 0 else "")}'
         f'{L.get("renewal_increase_pct")}%</strong> on expiring'),
    ]
    return ('<section><h2 class="sec">The renewal ask</h2>'
            '<p class="desc">The account&rsquo;s own loss ratio carried forward, not its absolute '
            'claims. Claims are earned against the pro-rata premium; annualising them and dividing '
            'by the larger expiring premium would mix two bases and understate the ask.</p>'
            + _rows(rows) + "</section>")


def _claims_buildup(payload: dict) -> str:
    book = payload["book"]
    return (
        '<section><h2 class="sec">Claims</h2>'
        f'<p class="desc">Incurred to {esc(book.get("as_of"))}: paid and outstanding as reported, '
        f'plus a 30-day tail on the account&rsquo;s own paid run rate for claims incurred but not '
        f'yet reported. No month is excluded.</p>'
        + _buildup_bars(book)
        + '<table class="t" style="margin-top:12px"><tbody>'
        + f'<tr><td>Paid</td><td class="num">{aed(book.get("paid"))}</td>'
          f'<td class="note">settled and reported</td></tr>'
        + f'<tr><td>Outstanding</td><td class="num">{aed(book.get("outstanding"))}</td>'
          f'<td class="note">reported, not yet settled</td></tr>'
        + f'<tr><td>IBNR</td><td class="num">{aed(book.get("ibnr"))}</td>'
          f'<td class="note">paid &divide; {book.get("days")} days &times; 30</td></tr>'
        + f'<tr><td><strong>Incurred</strong></td>'
          f'<td class="num"><strong>{aed(book.get("incurred_claims"))}</strong></td>'
          f'<td class="note">the figure the renewal is priced from</td></tr>'
        + "</tbody></table></section>"
    )


def _monthly(payload: dict) -> str:
    points = payload.get("monthly") or []
    if len(points) < 2:
        return ""
    chart = area_chart(points)
    if not chart:
        return ""
    amounts = [p.get("paid") or p.get("amount") or 0 for p in points]
    peak = max(amounts)
    peak_month = points[amounts.index(peak)]
    average = sum(amounts) / len(amounts)
    spike = (f' The worst month is {esc(peak_month.get("month"))} at {aed(peak)}, '
             f'{peak / average:.1f}&times; the average &mdash; worth knowing whether that is one '
             f'event or a run rate.') if average and peak > average * 1.8 else ""
    return (
        '<section><h2 class="sec">Monthly claims</h2>'
        f'<p class="desc">Paid claims by month of treatment.{spike}</p>{chart}</section>'
    )


def _top_table(title: str, desc: str, rows: List[dict], columns: List[tuple]) -> str:
    if not rows:
        return ""
    head = "".join(f'<th{" class=num" if num else ""}>{esc(label)}</th>' for label, _, num in columns)
    body = ""
    for row in rows:
        cells = "".join(
            f'<td{" class=num" if num else ""}>{fmt(row)}</td>' for _, fmt, num in columns
        )
        body += f"<tr>{cells}</tr>"
    return (f'<section><h2 class="sec">{esc(title)}</h2><p class="desc">{desc}</p>'
            f'<table class="t"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></section>')


def _claimants(payload: dict) -> str:
    rows = (payload.get("top_claimants") or [])[:8]
    total = payload["book"].get("incurred_claims") or 0
    return _top_table(
        "Who the cost is",
        "Worst first. A renewal is a different conversation depending on whether the top of this "
        "list is renewing, is leaving, or can be written with a condition excluded.",
        rows,
        [("Member", lambda r: esc(r.get("beneficiary_id")), False),
         ("Relation", lambda r: esc(r.get("relation") or ""), False),
         ("Age", lambda r: esc(r.get("age") if r.get("age") is not None else ""), True),
         ("Incurred", lambda r: aed(r.get("incurred")), True),
         ("Share", lambda r: pct((r.get("incurred") or 0) / total) if total else "&mdash;", True),
         ("Top diagnosis", lambda r: esc(r.get("top_diagnosis") or ""), False)],
    )


def _diagnoses(payload: dict) -> str:
    rows = (payload.get("top_diagnoses") or [])[:8]
    return _top_table(
        "What they were treated for",
        "By incurred amount. A chronic condition renews with the member; a one-off event does not.",
        rows,
        [("Diagnosis", lambda r: esc(r.get("label") or ""), False),
         ("", lambda r: ('<span class="chip">chronic</span>' if r.get("chronic") else "")
             + ('<span class="chip warn">high exposure</span>' if r.get("high_exposure") else ""), False),
         ("Claims", lambda r: esc(r.get("claim_count") or ""), True),
         ("Amount", lambda r: aed(r.get("amount")), True)],
    )


def _census(payload: dict) -> str:
    """The population being renewed. Age and dependant mix move next
    year's cost independently of this year's claims, so they belong
    beside the claims rather than in another tab.
    """
    c = payload.get("census") or {}
    if not c.get("member_count"):
        return ""
    total = c["member_count"]

    def mix(rows):
        return "".join(
            f'<div class="mixrow"><span class="mixlab">{esc(r["label"])}</span>'
            f'<span class="mixbar"><i style="width:{(r["count"] / total * 100):.1f}%"></i></span>'
            f'<span class="mixval">{r["count"]}</span></div>'
            for r in rows if r.get("count")
        )

    ratio = c.get("dependant_ratio")
    if ratio is None:
        ratio_note = ""
    elif ratio == 0:
        ratio_note = " Employees only, no dependants on the roster."
    else:
        ratio_note = (
            f' {ratio} dependants per employee'
            + (" &mdash; a dependant-heavy population costs more than its headcount suggests."
               if ratio >= 1 else ".")
        )
    return (
        '<section><h2 class="sec">Census</h2>'
        f'<p class="desc">{total} lives, average age '
        f'{esc(c.get("average_age") if c.get("average_age") is not None else "&mdash;")}.'
        f'{ratio_note}</p>'
        '<div class="two">'
        f'<div><h3>Age</h3>{mix(c.get("age_bands") or [])}</div>'
        f'<div><h3>Relation</h3>{mix(c.get("relations") or [])}</div>'
        "</div></section>"
    )


def _encounters(payload: dict) -> str:
    """Where the money goes: inpatient, outpatient, maternity. An
    account whose cost is inpatient is a different renewal from one whose
    cost is routine outpatient volume.
    """
    rows = payload.get("encounter_split") or []
    rows = [r for r in rows if (r.get("total_claimed") or r.get("value") or r.get("amount"))]
    if not rows:
        return ""
    def amount(r):
        return r.get("total_claimed") or r.get("value") or r.get("amount") or 0
    total = sum(amount(r) for r in rows) or 1
    body = "".join(
        f'<tr><td>{esc(r.get("encounter_type") or r.get("label") or "")}</td>'
        f'<td class="num">{esc(r.get("claim_count") or r.get("count") or "")}</td>'
        f'<td class="num">{aed(amount(r))}</td>'
        f'<td class="num">{pct(amount(r) / total)}</td></tr>'
        for r in sorted(rows, key=amount, reverse=True)
    )
    return ('<section><h2 class="sec">Where the cost sits</h2>'
            '<p class="desc">By encounter type. An account driven by inpatient events is a '
            'different renewal from one driven by routine outpatient volume.</p>'
            '<table class="t"><thead><tr><th>Type</th><th class="num">Claims</th>'
            '<th class="num">Amount</th><th class="num">Share</th></tr></thead>'
            f'<tbody>{body}</tbody></table></section>')


def _population(payload: dict) -> str:
    pop = payload.get("population") or {}
    if not pop:
        return ""
    leavers = pop.get("leaving") or {}
    rows = [
        ("Active at the cut date", esc(pop.get("active_member_count")),
         "the population being renewed"),
        ("Off risk during the term", esc(pop.get("deleted_member_count")),
         "premium earned for part of a year, claims not"),
    ]
    if leavers.get("incurred"):
        rows.append(("Claims of members off risk", aed(leavers.get("incurred")),
                     "not carried into the renewal &mdash; excluded from the figures above"))
    return ('<section><h2 class="sec">Population</h2>'
            '<p class="desc">A renewal prices the members who will be there, not the ones who '
            'were.</p>' + _rows(rows) + "</section>")


def _basis(payload: dict) -> str:
    book, rating = payload["book"], payload["rating"]
    a = rating.get("assumptions_used") or {}
    return _note(
        f'Read from the Portfolio Loss Ratio book as of {esc(book.get("as_of"))} '
        f'({esc(book.get("as_of_source") or "recorded extract date")}). '
        f'Trend {pct(a.get("inflation_pct"))}. Two different loadings appear above and they are '
        f'different things: the net loss ratio is struck after the book&rsquo;s own expense '
        f'allowance of {pct(book.get("loading_pct"))}, while the required premium is grossed up by '
        f'this case&rsquo;s commission, TPA, HealthCross and carrier fees totalling '
        f'{pct(a.get("loading_pct"))}. '
        f'Required premium = incurred claims annualised over {book.get("days")} days, trended, '
        f'divided by (1 &minus; loading). Every figure on this page comes from the same function '
        f'that produces the Loss Ratio screen, so the two cannot disagree.'
    )


def render_renewal_report(payload: dict, today: Optional[date] = None) -> str:
    """One page. `payload` is what GET /cases/{id}/renewal-report returns."""
    today = today or date.today()
    company = payload["case"].get("company_name") or "Renewal"
    body = (
        _masthead(payload)
        + _identity(payload, today)
        + _headline(payload)
        + '<div class="pad">'
        + _the_ask(payload)
        + _ladder(payload)
        + _premiums(payload)
        + _claims_buildup(payload)
        + _monthly(payload)
        + _encounters(payload)
        + _claimants(payload)
        + _diagnoses(payload)
        + _census(payload)
        + _population(payload)
        + _basis(payload)
        + "</div>"
        + _footer("HealthCross &middot; Renewal Review", esc(company))
    )
    return (
        _HEAD.format(title=esc(f"{company} - Renewal Review"), css=STYLESHEET + EXTRA_CSS)
        + _page("Renewal review", body)
        + "</body></html>"
    )
