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

/* --- the document's own structure -------------------------------------
   Numbered sections with a rule above each, so a reader can navigate a
   long report by eye and cite a section back. The summary is section-less
   by design: one page has nothing to navigate. */
.sec{margin:0 0 30px}
.sec-head{display:flex;align-items:baseline;gap:11px;border-top:2px solid var(--navy);padding-top:9px;margin:0 0 4px}
.sec-no{font-family:var(--mono);font-size:10px;font-weight:700;color:var(--sky);letter-spacing:.08em}
.sec-head h2{font-size:15px;margin:0;letter-spacing:-.01em;color:var(--navy)}
.sec-sub{color:var(--muted);font-size:11px;margin:0 0 15px;max-width:62ch;line-height:1.55}

/* The ask, stated once and unmistakably. */
.ask{border:1px solid var(--rule);border-left:4px solid var(--sky);padding:17px 20px;margin:0 0 26px}
.ask .l{font-family:var(--mono);font-size:8.5px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);display:block;margin-bottom:6px}
.ask .v{font-size:27px;font-weight:700;color:var(--navy);letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.ask .v .u{font-size:14px;color:var(--muted);font-weight:500;margin-left:5px}
.ask p{margin:11px 0 0;font-size:12px;line-height:1.65;color:var(--ink)}
.ask.override{border-left-color:var(--warn)}
.ask.override .v{color:var(--warn)}

/* Quoted against computed - the two are never printed without the
   sentence that tells them apart. */
.vs{display:grid;grid-template-columns:1fr 1fr;gap:0;border:1px solid var(--rule);margin:0 0 12px}
.vs>div{padding:13px 17px}
.vs>div+div{border-left:1px solid var(--rule)}
.vs .l{font-family:var(--mono);font-size:8px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);display:block;margin-bottom:5px}
.vs .n{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.vs .s{font-size:10.5px;color:var(--muted);margin-top:3px}
.vs .quoted .n{color:var(--warn)}
.vs .computed .n{color:var(--navy)}

/* Client version: everything internal folds away in one click. */
[data-view="client"] .internal-only{display:none!important}

.toolbar{position:sticky;top:0;z-index:9;display:flex;align-items:center;gap:14px;
  background:var(--navy);color:#fff;padding:11px 22px;font-size:12.5px;
  font-family:var(--sans);box-shadow:0 1px 6px rgba(0,0,0,.18)}
.toolbar .title{font-weight:600;flex:1}
.toolbar button{font:inherit;font-size:11.5px;border:1px solid rgba(255,255,255,.35);
  background:transparent;color:#fff;padding:6px 13px;border-radius:3px;cursor:pointer}
.toolbar button:hover{background:rgba(255,255,255,.12)}
.toolbar button.on{background:#fff;color:var(--navy);border-color:#fff;font-weight:600}
.toolbar .seg{display:flex;border-radius:3px;overflow:hidden}
.toolbar .seg button{border-radius:0}
.toolbar .seg button+button{border-left:0}

@page{size:A4;margin:12mm 11mm 14mm}
@media print{
  .toolbar{display:none}
  body{background:#fff;padding:0;margin:0}
  /* The screen paper is 920px wide - wider than A4's printable area, so
     on paper it ran off the right edge and took the masthead and the KPI
     strip with it. On paper the page IS the container. */
  .paper,.doc{max-width:100%;width:100%;margin:0;box-shadow:none;border:0}
  .pad{padding:0}
  .page-tag{display:none}
  /* Tightened for paper: the same spacing that breathes on a screen puts
     a one-page summary onto two. */
  .kpis{margin-bottom:16px}
  .ask{padding:13px 15px;margin-bottom:16px}
  .ask .v{font-size:23px}
  .vs{margin-bottom:9px}
  .sec{margin-bottom:20px}
  h1{font-size:27px;margin-bottom:6px}
  /* A summary that runs to two pages is not a summary. The rows carry
     their own explanation, which is what a reader needs and also what
     makes them tall - so the type tightens rather than the notes going. */
  table.t td{padding:5px 13px 5px 0}
  table.t th{padding:5px 13px 5px 0}
  table.t td.note,table.t th{font-size:9.5px}
  table.t{font-size:11px}
  .desc,.sec-sub{margin-bottom:9px;line-height:1.45}
  h2{font-size:14px;margin-bottom:2px}
  .ask p{margin-top:8px;line-height:1.55}
  .kpi .v{font-size:19px}
  section,.sec,.ask,.vs,table.t,.kpis{break-inside:avoid}
  .sec-head,h1,h2{break-after:avoid}
}
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
        # The share footnote is the LADDER's, which is the experience's -
        # so beside an overridden premium it described a different number
        # to the one printed above it (166.1% next to a figure that was
        # 160%). Where the two differ the footnote says what it is.
        + _kpi("Required premium", aed(rating.get("required_premium")),
               (f'quoted &mdash; the experience asks '
                f'{pct(rating.get("required_share_of_expiring"))} of expiring'
                if rating.get("increase_source") == "override"
                else f'{pct(rating.get("required_share_of_expiring"))} of the annualised expiring premium'))
        + _kpi(("Quoted increase" if rating.get("increase_source") == "override"
                else "Renewal increase"),
               ("+" if (inc or 0) >= 0 else "") + f'{inc}%' if inc is not None else "&mdash;",
               (f'an override &mdash; experience asks {rating.get("computed_increase_pct")}%'
                if rating.get("increase_source") == "override"
                else f'vs {aed(rating.get("renewal_base_premium"))} annualised expiring'),
               "bad" if (inc or 0) >= 15 else ("warn" if (inc or 0) > 0 else "good"))
        + "</div>"
    )


def _section(no: str, title: str, sub: str, body: str, internal_only: bool = False) -> str:
    """A numbered section. Internal-only sections fold away entirely in
    the client view rather than being redacted in place - a blanked-out
    box tells a client there is something they are not being shown."""
    cls = "sec internal-only" if internal_only else "sec"
    return (
        f'<section class="{cls}">'
        f'<div class="sec-head"><span class="sec-no">{no}</span><h2>{title}</h2></div>'
        + (f'<p class="sec-sub">{sub}</p>' if sub else "")
        + body
        + "</section>"
    )


def _quoted_vs_computed(payload: dict) -> str:
    """What is being asked, beside what the experience asks for.

    The document used to print the overridden premium in its KPI strip
    and the computed one in its ladder, three inches apart, with nothing
    distinguishing them - two required premiums and two increases on one
    page. Whichever the reader happened to land on was the number they
    took away.
    """
    r = payload["rating"]
    if r.get("increase_source") != "override":
        return ""
    quoted, computed = r.get("renewal_increase_pct"), r.get("computed_increase_pct")
    qp, cp = r.get("required_premium"), r.get("computed_required_premium")
    if quoted is None or computed is None:
        return ""
    gap = (cp - qp) if (cp is not None and qp is not None) else None
    return (
        '<div class="vs">'
        f'<div class="quoted"><span class="l">Quoted &mdash; what we are asking</span>'
        f'<span class="n">{"+" if quoted >= 0 else ""}{quoted}%</span>'
        f'<div class="s">{aed(qp)} on the annualised expiring premium</div></div>'
        f'<div class="computed"><span class="l">Experience &mdash; what the account asks for</span>'
        f'<span class="n">{"+" if computed >= 0 else ""}{computed}%</span>'
        f'<div class="s">{aed(cp)} &mdash; the figure below builds to this</div></div>'
        + (f'<div style="grid-column:1/-1;border-top:1px solid var(--rule);padding:10px 17px;'
           f'font-size:11px;color:var(--muted);line-height:1.55">The quoted increase is an '
           f'underwriting decision, not a measurement: it holds the account '
           f'<strong style="color:var(--navy)">{aed(abs(gap))}</strong> '
           f'{"below" if gap > 0 else "above"} what its own experience asks for. Every figure '
           f'below is the experience&rsquo;s.</div>' if gap else "")
        + "</div>"
    )


def _the_ask(payload: dict) -> str:
    """The sentence the meeting is about, written out rather than left to
    be assembled from four tiles.
    """
    book, rating = payload["book"], payload["rating"]
    inc, base = rating.get("renewal_increase_pct"), rating.get("renewal_base_premium")
    if inc is None or base is None:
        # A page printed for a meeting with the headline silently missing
        # reads as an oversight. Say the price is withheld and why - the
        # experience below it is still worth the meeting.
        problems = rating.get("pricing_problems") or []
        if not problems:
            return ""
        return (
            '<div class="callout"><strong>Not priced.</strong> The account\'s experience is '
            'reported below, but the renewal price is withheld until this is resolved: '
            + " ".join(esc(p["message"]) for p in problems)
            + "</div>"
        )
    gross = book.get("gross_loss_ratio")
    direction = "an increase of" if inc >= 0 else "a reduction of"
    verdict = (
        "The account is paying less than it costs." if (gross or 0) >= 1.0
        else "The account is inside its gross target but not its net one."
        if (book.get("net_loss_ratio") or 0) >= 1.0
        else "The account is running to plan."
    )
    ladder = payload.get("ladder") or {}
    override = rating.get("increase_source") == "override"
    # The headline is what is being ASKED. Where that is an override, the
    # experience's own figure is the sentence after it, never the number
    # in the box - a reader takes away whatever is largest on the page.
    return (
        f'<div class="ask{" override" if override else ""}">'
        f'<span class="l">{"Quoted renewal increase" if override else "Renewal increase"}</span>'
        f'<span class="v">{"+" if inc >= 0 else "&minus;"}{abs(inc)}%'
        f'<span class="u">&middot; {aed(rating.get("required_premium"))} '
        f'on {aed(base)} annualised expiring</span></span>'
        f'<p><strong>{verdict}</strong> The account runs at {pct(gross)}; carried forward with '
        f'{pct(ladder.get("inflation_pts"))} of claims inflation that is '
        f'<strong>{pct(ladder.get("trended_loss_ratio"))}</strong>, and grossed up for a '
        f'{pct(ladder.get("loading_pct"))} loading it needs '
        f'{pct(ladder.get("required_share_of_expiring"))} of the expiring premium &mdash; '
        f'{aed(rating.get("computed_required_premium") or rating.get("required_premium"))}'
        + (f', {direction} {abs(rating.get("computed_increase_pct", inc))}%.'
           if override else f', {direction} {abs(inc)}%.')
        + '</p></div>'
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
         ' &mdash; the renewal is quoted against this'),
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


def _benefits(payload: dict) -> str:
    """What is actually being bought, per category.

    A renewal priced without its cover beside it is a number with no
    subject: the same loss ratio on a 275,000 limit and a 150,000 one are
    different accounts.
    """
    plans = payload.get("benefits") or []
    if not plans:
        return _note("No table of benefits uploaded for this case, so the cover behind these "
                     "figures is not shown.")
    fields = ["annual_limit", "network", "deductible", "op_copay", "pharmacy_limit",
              "dental_limit", "optical_limit", "maternity_limit", "pre_existing"]
    labels = {f: f.replace("_", " ").title() for f in fields}
    shown = [f for f in fields if any((p["summary"] or {}).get(f) for p in plans)]
    if not shown:
        return _note("The uploaded benefits carry no readable values yet.")
    head = "".join(f"<th>{esc(labels[f])}</th>" for f in shown)
    body = "".join(
        f'<tr><td><strong>{esc(p.get("category") or "&mdash;")}</strong>'
        f'{f"<br><span class=note>{esc(p.get(chr(112)+chr(108)+chr(97)+chr(110)+chr(95)+chr(110)+chr(97)+chr(109)+chr(101)))}</span>" if p.get("plan_name") else ""}</td>'
        + "".join(f'<td>{esc((p["summary"] or {}).get(f) or "&mdash;")}</td>' for f in shown)
        + "</tr>"
        for p in plans
    )
    return (f'<table class="t"><thead><tr><th>Category</th>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table>")


def _toolbar(title: str, download_url: str) -> str:
    """Read on screen, download when wanted - never "print".

    A browser print puts its own date, URL and page counter on the page,
    which is what made these documents look like screenshots of an
    internal tool rather than something a firm sends out.
    """
    return (
        f'<div class="toolbar">'
        f'<span class="title">{title}</span>'
        f'<div class="seg">'
        f'<button id="v-int" class="on" onclick="setView(\'internal\')">Internal</button>'
        f'<button id="v-cli" onclick="setView(\'client\')">Client</button>'
        f"</div>"
        f'<button onclick="window.print()">Download PDF</button>'
        f"</div>"
        "<script>function setView(v){document.documentElement.setAttribute('data-view',v);"
        "document.getElementById('v-int').className=(v==='internal'?'on':'');"
        "document.getElementById('v-cli').className=(v==='client'?'on':'');}</script>"
    )


def _summary_body(payload: dict, today: date) -> str:
    """The decision, and only the decision. One page."""
    return (
        _masthead(payload)
        + _identity(payload, today)
        + _headline(payload)
        + '<div class="pad">'
        + _quoted_vs_computed(payload)
        + _the_ask(payload)
        + _ladder(payload)
        + _premiums(payload)
        + "</div>"
    )


def render_renewal_summary(payload: dict, today: Optional[date] = None) -> str:
    """One page: the loss ratio, the ask, the ladder, the premiums.

    Everything a renewal decision needs and nothing that explains it -
    the explanation is the Renewal Report. `payload` is what
    GET /cases/{id}/renewal-report returns; both documents are built from
    the same one so they cannot disagree.
    """
    today = today or date.today()
    company = payload["case"].get("company_name") or "Renewal"
    body = (
        _summary_body(payload, today)
        + _footer("HealthCross &middot; Renewal Summary", esc(company))
    )
    return (
        _HEAD.format(title=esc(f"{company} - Renewal Summary"), css=STYLESHEET + EXTRA_CSS)
        + _toolbar(f"{esc(company)} &middot; Renewal Summary",
                   f'/cases/{payload["case"]["id"]}/renewal-summary.html')
        + _page("Renewal summary", body)
        + "</body></html>"
    )


def render_renewal_report(payload: dict, today: Optional[date] = None) -> str:
    """The comprehensive file: the summary, then the census, the benefits
    and the claims behind it, then the basis every figure was struck on.

    Section 1 is the Summary document verbatim - the same function - so
    the short document and the long one can never quote different
    numbers for the same account.
    """
    today = today or date.today()
    company = payload["case"].get("company_name") or "Renewal"
    body = (
        _summary_body(payload, today)
        + '<div class="pad">'
        + _section("02", "Census", "The population being renewed - who is on risk, and how it "
                                   "has moved since the expiring term.",
                   _census(payload) + _population(payload))
        + _section("03", "Benefits", "The cover behind the price. The same loss ratio on a "
                                     "different annual limit is a different account.",
                   _benefits(payload))
        + _section("04", "Claims", "What the money went on: the shape of the year, who carried "
                                   "it, and what for.",
                   _claims_buildup(payload) + _monthly(payload) + _encounters(payload)
                   + _claimants(payload) + _diagnoses(payload))
        + _section("05", "Basis", "What every figure above was measured on, so it can be "
                                  "checked rather than trusted.",
                   _basis(payload), internal_only=True)
        + "</div>"
        + _footer("HealthCross &middot; Renewal Report", esc(company))
    )
    return (
        _HEAD.format(title=esc(f"{company} - Renewal Report"), css=STYLESHEET + EXTRA_CSS)
        + _toolbar(f"{esc(company)} &middot; Renewal Report",
                   f'/cases/{payload["case"]["id"]}/renewal-report.html')
        + _page("Renewal report", body)
        + "</body></html>"
    )
