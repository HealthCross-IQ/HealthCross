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
    _HEAD_PLAIN,
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

/* --- the three questions ----------------------------------------------
   The first page answers exactly three: is this account risky, why, and
   what should be done about it. Everything that EXPLAINS those answers -
   the ladder, the premium build-up, the claims detail - is a section
   further down, because a first page carrying its own workings is a
   first page nobody reads in ten seconds. */
.verdict{display:flex;align-items:flex-start;gap:15px;border:1px solid var(--rule);
  border-left:5px solid var(--alert);padding:14px 18px;margin:0 0 22px}
.verdict.warn{border-left-color:var(--warn)} .verdict.ok{border-left-color:var(--ok)}
.verdict .tag{font-family:var(--mono);font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.09em;color:var(--alert);white-space:nowrap;padding-top:2px}
.verdict.warn .tag{color:var(--warn)} .verdict.ok .tag{color:var(--ok)}
.verdict .say{font-size:12.5px;line-height:1.6;color:var(--ink);margin:0}
.verdict .say strong{color:var(--navy)}

/* Account information: the facts, as facts. No card, no chart. */
.facts{display:grid;grid-template-columns:auto 1fr auto 1fr;gap:0 14px;font-size:11.5px;
  border:1px solid var(--rule);padding:13px 16px;margin:0}
.facts dt{font-family:var(--mono);font-size:8.5px;text-transform:uppercase;letter-spacing:.07em;
  color:var(--muted);padding:5px 0;white-space:nowrap}
.facts dd{margin:0;padding:5px 0;color:var(--navy);font-weight:600}

/* Gross to net, side by side with the reason between them. A reader who
   is told 168.2% and 228.8% without being told what happened in between
   assumes one of them is wrong. */
.compare{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;
  border:1px solid var(--rule);margin:0}
.compare>.side{padding:15px 18px}
.compare .l{font-family:var(--mono);font-size:8px;text-transform:uppercase;letter-spacing:.08em;
  color:var(--muted);display:block;margin-bottom:6px}
.compare .n{font-size:25px;font-weight:700;font-variant-numeric:tabular-nums;letter-spacing:-.02em;
  line-height:1.05;display:block}
.compare .s{font-size:10.5px;color:var(--muted);margin-top:5px;line-height:1.5}
.compare .arrow{padding:0 4px;color:var(--navy-25);font-size:20px;line-height:1}
.compare .gross .n{color:var(--warn)} .compare .net .n{color:var(--alert)}
.compare .gross .n.ok,.compare .net .n.ok{color:var(--ok)}

/* Why it is risky: the readings, worst first, each with its action. */
.why{border:1px solid var(--rule);margin:0}
.why .r{display:grid;grid-template-columns:6px 1fr;gap:13px;padding:12px 16px 12px 0;
  border-bottom:1px solid var(--rule)}
.why .r:last-child{border-bottom:0}
.why .r>i{background:var(--alert);display:block}
.why .r.high>i{background:var(--warn)} .why .r.watch>i{background:var(--sky)}
.why h4{margin:0 0 3px;font-size:12px;color:var(--navy);font-weight:700}
.why p{margin:0;font-size:11px;line-height:1.55;color:var(--ink)}
.why p.act{color:var(--muted);margin-top:4px}
.why p.act b{color:var(--navy);font-weight:600}

/* The decision column. A pill, because "accept" three times in a column
   of text is a paragraph and this is meant to be scanned. */
.pill{display:inline-block;font-family:var(--mono);font-size:8.5px;font-weight:700;
  text-transform:uppercase;letter-spacing:.07em;padding:3px 8px;border-radius:2px;white-space:nowrap}
.pill.accept{background:var(--ok-wash);color:var(--ok)}
.pill.review{background:var(--warn-wash);color:var(--warn)}
.pill.reject{background:var(--alert-wash);color:var(--alert)}
table.t tr.lead td{background:var(--sky-25)}
table.t tr.lead td.num{font-weight:700}
/* The claims metrics have short labels and long explanations, and auto
   layout gave the explanation the width - so "Members who claimed"
   wrapped onto two lines beside a column of white space. */
.perf table.t td:first-child{width:168px}

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
.dlhint{display:none;background:var(--sky-25);border-bottom:1px solid var(--rule);
  padding:10px 22px;font-size:11.5px;color:var(--navy);font-family:var(--sans)}
.dlhint.show{display:block}
.dlhint button{font:inherit;font-size:11px;margin-left:10px;border:1px solid var(--rule);
  background:#fff;padding:3px 10px;border-radius:3px;cursor:pointer}
@media print{.dlhint{display:none!important}}

/* --- the summary page, redesigned -------------------------------------
   The answer first: verdict, ask, gross and net on one strip. Then why,
   in sentences; how the ask is built, as one row; the price options; and
   the claims shape and account block so the page stands alone. */
.sv-head{padding-top:20px;margin-bottom:14px}
.sv-head h1{font-size:21px;font-weight:800;letter-spacing:-.01em;margin:0 0 4px;color:var(--navy)}
.sv-head .sub{font-size:12.5px;color:var(--muted);line-height:1.55;max-width:none}
.sv-head .sub b{color:var(--navy)}
.sv-strip{display:grid;grid-template-columns:1.35fr 1fr 1fr 1fr;border:1px solid var(--rule);border-radius:10px;overflow:hidden;margin:0 0 18px}
.sv-strip>div{padding:13px 16px;border-right:1px solid var(--rule)}
.sv-strip>div:last-child{border-right:none}
.sv-strip .rec{background:var(--sky-25)}
.sv-k{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--navy-50);margin-bottom:6px}
.sv-v{font-size:22px;font-weight:800;letter-spacing:-.01em;color:var(--navy);line-height:1.1}
.sv-v.override{color:var(--warn)}
.sv-s{font-size:11.5px;color:var(--muted);margin-top:4px;line-height:1.45}
.sv-pill{display:inline-flex;padding:2px 9px;border-radius:999px;font-size:11.5px;font-weight:700;white-space:nowrap;line-height:1.5}
.sv-pill.ok{background:var(--ok-wash);color:var(--ok)} .sv-pill.warn{background:var(--warn-wash);color:var(--warn)}
.sv-pill.bad{background:var(--alert-wash);color:var(--alert)} .sv-pill.grey{background:var(--sky-25);color:var(--muted)}
.sv-lr{display:inline-flex;min-width:52px;justify-content:center;padding:2px 7px;border-radius:4px;font-weight:800;font-size:15px;line-height:1.3}
.sv-lr.ok{background:var(--ok-wash);color:var(--ok)} .sv-lr.warn{background:var(--warn-wash);color:var(--warn)} .sv-lr.bad{background:var(--alert-wash);color:var(--alert)}
.sv-lr.small{font-size:12px;font-weight:700;min-width:48px}
h2.sv{font-size:12px;font-weight:800;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin:20px 0 8px;font-family:var(--sans)}
.sv-why{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.sv-why li{display:flex;gap:10px;font-size:13px;line-height:1.5}
.sv-why li>i{width:8px;height:8px;border-radius:2px;flex:none;margin-top:7px;background:var(--navy-50);display:block}
.sv-why li.bad>i{background:var(--alert)} .sv-why li.warn>i{background:var(--warn)} .sv-why li.ok>i{background:var(--ok)}
.sv-why b{color:var(--navy)}
.sv-why .act{color:var(--muted)}
.sv-ladder{display:grid;grid-template-columns:repeat(5,1fr);border:1px solid var(--rule);border-radius:10px;overflow:hidden;margin-top:4px}
.sv-ladder>div{padding:11px 12px;border-right:1px solid var(--rule);position:relative}
.sv-ladder>div:last-child{border-right:none;background:var(--sky-25)}
.sv-ladder .n{font-size:18px;font-weight:800;color:var(--navy);line-height:1.1}
.sv-ladder .sv-k{margin-bottom:4px}
.sv-ladder .sv-s{margin-top:2px}
.sv-ladder>div+div{border-left:0}
table.sv{width:100%;border-collapse:collapse;font-size:12.5px}
table.sv th{text-align:left;font-family:var(--sans);font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);padding:8px 8px;background:var(--sky-25);border-bottom:1.5px solid var(--sky)}
table.sv th.num,table.sv td.num{text-align:right}
table.sv td{padding:8px 8px;border-bottom:1px solid var(--rule);vertical-align:middle}
table.sv tr.lead td{font-weight:800;background:rgba(92,217,255,.07)}
table.sv td .note{display:block;font-size:11px;color:var(--muted);font-weight:400}
.sv-two{display:grid;grid-template-columns:1.1fr 1fr;gap:22px;margin-top:4px}
.sv-facts{display:grid;grid-template-columns:repeat(3,1fr);gap:6px 16px;font-size:12px}
.sv-facts div{display:flex;justify-content:space-between;gap:8px;border-bottom:1px solid var(--rule);padding:5px 0}
.sv-facts span{color:var(--muted)} .sv-facts b{color:var(--navy);white-space:nowrap}
.sv-bars div{display:grid;grid-template-columns:86px 1fr 44px;gap:8px;align-items:center;font-size:12px;margin-bottom:5px}
.sv-bars i{display:block;height:10px;background:var(--sky-25);border-radius:2px;position:relative;overflow:hidden}
.sv-bars i b{position:absolute;inset:0 auto 0 0;background:var(--sky);display:block}
.sv-bars .val{text-align:right;font-weight:700;color:var(--navy)}
.sv-sign{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-top:22px;padding-top:14px;border-top:1px dashed var(--rule);font-size:12px;color:var(--muted)}
.sv-sign div{border-bottom:1px solid var(--rule);padding-bottom:22px}
.sv-foot{font-size:11px;color:var(--navy-50);margin-top:10px;line-height:1.55}
.vs{border-radius:10px}

@page{size:A4;margin:10mm 11mm 10mm}
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
  .masthead{padding:10px 44px}
  .sv-head{padding-top:10px;margin-bottom:6px}
  .sv-head h1{font-size:17px} .sv-head .sub{font-size:10.5px;line-height:1.45}
  .sv-strip{margin-bottom:8px} .sv-strip>div{padding:8px 10px}
  .sv-v{font-size:17px} .sv-lr{font-size:12.5px} .sv-s{font-size:10px;line-height:1.35}
  h2.sv{margin:9px 0 4px;font-size:10.5px}
  .sv-why{gap:4px} .sv-why li{font-size:11px;line-height:1.4}
  .sv-ladder>div{padding:7px 9px} .sv-ladder .n{font-size:14px}
  table.sv{font-size:10.5px} table.sv td{padding:4px 7px} table.sv th{padding:5px 7px;font-size:9.5px}
  table.sv td .note{font-size:9.5px}
  .sv-two{gap:14px;margin-top:0} .sv-facts{font-size:10.5px;gap:3px 12px} .sv-facts div{padding:3px 0}
  .sv-bars div{font-size:10.5px;margin-bottom:2px} .sv-bars i{height:8px}
  .sv-foot{font-size:9.5px;margin-top:5px;line-height:1.45}
  .sv-sign{margin-top:6px;padding-top:5px;font-size:10px} .sv-sign div{padding-bottom:8px}
  /* The masthead already names the document and the account; the running
     footer is one more line the single page cannot spare. */
  .doc-footer{display:none}
  .doc{padding-bottom:0}
  .sv-why li{font-size:10.5px}
  section,.sec,.ask,.vs,table.t,table.sv,.kpis,.sv-strip,.sv-ladder{break-inside:avoid}
  .sec-head,h1,h2{break-after:avoid}
}
"""


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
            f'<div class="meta"><b>{esc(payload.get("_doc_title") or "Renewal Summary")}</b><br>'
            f'{esc(case.get("company_name"))}</div></div>')


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


def _net_ratio_note(book: dict, rating: dict) -> str:
    """Which loading the net ratio is net OF, and whether anybody
    supplied it.

    Two different loadings can legitimately appear in one document: the
    net loss ratio is a BOOK figure and uses the book's own expense
    allowance, while the ask is priced on the account's entered fee
    split. A reader who sees 33.0% here and 21.5% in the ladder three
    inches below has no way to tell that from an error - so the note
    says which is which, and says out loud when the 33 is the house
    average standing in for a figure nobody has supplied.
    """
    loading = book.get("loading_pct")
    quoted = (rating.get("assumptions_used") or {}).get("loading_pct")
    if book.get("loading_is_default"):
        note = (f'net of the house-average {pct(loading)} expense allowance &mdash; '
                f'this account&rsquo;s own has not been supplied')
    else:
        note = f'net of this account&rsquo;s own {pct(loading)} expense allowance'
    if quoted is not None and loading is not None and abs(quoted - loading) > 0.0005:
        note += f'. The ask below is priced on the {pct(quoted)} entered on the case'
    return note


def _loading_short(book: dict) -> str:
    loading = book.get("loading_pct")
    if book.get("loading_is_default"):
        return f"after the house-average {pct(loading)} expense allowance (account's own not supplied)"
    return f"after this account's own {pct(loading)} expense allowance"


def _day(value) -> str:
    """An ISO date from the payload as '1 Oct 2025'; anything else as is."""
    try:
        d = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return esc(value) if value else "&mdash;"
    return f"{d.day} {d.strftime('%b %Y')}"


def _sentence(text: str) -> str:
    """A clause written to sit mid-sentence, promoted to its own sentence.

    str.capitalize would lowercase everything after the first letter and
    take "The ask below is priced on the 21.5%" down with it.
    """
    text = text.strip()
    if not text:
        return ""
    return text[0].upper() + text[1:] + ("" if text.endswith(".") else ".")


def _verdict(payload: dict) -> tuple:
    """The ten-second answer: is this account profitable or risky.

    Read off the alerts rather than recomputed here - the alert rules ARE
    the house's reading of a loss ratio, and a document that re-derived
    the verdict from the raw ratio would eventually disagree with the
    screen the reader opened it from.
    """
    alerts = payload.get("alerts") or []
    book, rating = payload["book"], payload["rating"]
    gross = book.get("gross_loss_ratio")
    critical = [a for a in alerts if a["severity"] == "critical"]
    high = [a for a in alerts if a["severity"] == "high"]

    if rating.get("pricing_blocked"):
        return ("Not priced", "warn",
                "The experience below is complete, but the renewal price is withheld until the "
                "inputs it depends on are entered.")
    inc = rating.get("renewal_increase_pct")
    computed = rating.get("computed_increase_pct")
    if inc is None:
        ask = ""
    elif rating.get("increase_source") == "override":
        # "It needs +12%" would be false where 12% is a decision and the
        # experience asks for 66%. The banner is the one line a reader is
        # certain to take away, so it says which of the two it is naming.
        ask = (f' We are quoting <strong>{"+" if inc >= 0 else "&minus;"}{abs(inc)}%</strong> '
               f'&mdash; {aed(rating.get("required_premium"))}; the experience asks '
               f'{"+" if (computed or 0) >= 0 else "&minus;"}{abs(computed)}% '
               f'({aed(rating.get("computed_required_premium"))}).')
    else:
        ask = (f' It needs <strong>{"+" if inc >= 0 else "&minus;"}{abs(inc)}%</strong> '
               f'&mdash; {aed(rating.get("required_premium"))} against '
               f'{aed(rating.get("renewal_base_premium"))} annualised expiring.')
    if critical:
        # Name the reading that made it critical. "High risk" beside a
        # gross ratio of 76% reads as a contradiction until the reader
        # knows the risk is one member carrying 58% of the claims.
        worst = ", ".join(a["title"].lower() for a in critical)
        return ("High risk", "",
                f'The account runs at <strong>{pct(gross)}</strong> of the premium it earned.{ask} '
                f'Critical: {esc(worst)} &mdash; see below.')
    if high:
        return ("Above target", "warn",
                f'The account runs at <strong>{pct(gross)}</strong> of the premium it earned.{ask}')
    return ("On plan", "ok",
            f'The account runs at <strong>{pct(gross)}</strong> of the premium it earned, inside '
            f'the house target.{ask}')


def _verdict_banner(payload: dict) -> str:
    word, tone, say = _verdict(payload)
    return (f'<div class="verdict {tone}"><span class="tag">{word}</span>'
            f'<p class="say">{say}</p></div>')


def _account_information(payload: dict, today: date) -> str:
    """The facts about what is being renewed, before any figure about it.

    An underwriter reading a renewal for the first time needs to know
    which account, which product, how many lives and over what period
    before a loss ratio means anything - and every one of those was
    scattered across a sentence in the old page.
    """
    case, book = payload["case"], payload["book"]

    def fact(value):
        # esc() the value, not the fallback - escaping "&mdash;" printed
        # the entity itself on the page.
        return esc(value) if value else "&mdash;"

    rows = [
        ("Client", fact(case.get("company_name"))),
        ("Product", fact(case.get("product"))),
        ("Broker", fact(case.get("broker_name"))),
        ("Members", fact(book.get("member_count"))),
        ("Policy start", fact(book.get("policy_start_date"))),
        ("Expiry", fact(case.get("term_end_date"))),
        ("Experience period", f'{book.get("days")} days, to {esc(book.get("as_of"))}'),
        ("Prepared", long_date(today)),
    ]
    return ('<dl class="facts">'
            + "".join(f"<dt>{label}</dt><dd>{value}</dd>" for label, value in rows)
            + "</dl>")


def _ratio_compare(payload: dict) -> str:
    """Gross beside net, with what happened in between written between them.

    These two figures are the ones a non-underwriter misreads: told the
    account is at 168% and also at 229%, the natural conclusion is that
    one of them is a mistake. Nothing changed except that the expenses
    came out of the premium first, and saying so is a sentence, not a
    footnote.
    """
    book, rating = payload["book"], payload["rating"]
    gross, net = book.get("gross_loss_ratio"), book.get("net_loss_ratio")
    if gross is None or net is None:
        return ""
    loading = book.get("loading_pct")
    return (
        '<div class="compare">'
        f'<div class="side gross"><span class="l">Gross loss ratio</span>'
        f'<span class="n{" ok" if (gross or 0) < 1 else ""}">{pct(gross)}</span>'
        f'<div class="s">Before expenses. Incurred claims of {aed(book.get("incurred_claims"))} '
        f'against the {aed(book.get("earned_premium"))} the account earned over '
        f'{book.get("days")} days.</div></div>'
        '<div class="arrow">&#9654;</div>'
        f'<div class="side net"><span class="l">Net loss ratio</span>'
        f'<span class="n{" ok" if (net or 0) < 1 else ""}">{pct(net)}</span>'
        f'<div class="s">After expenses. The same claims against the '
        f'{aed(book.get("net_premium"))} left once the {pct(loading)} expense and commission '
        f'allowance comes out. Nothing else changed.<br>'
        f'<span style="color:var(--navy-50)">{_sentence(_net_ratio_note(book, rating))}</span>'
        f'</div></div>'
        '</div>'
    )


def _why_risky(payload: dict) -> str:
    """The second question, answered as readings rather than as figures.

    "Loss ratio 168.2%" is a fact; "this is not a rate-increase
    conversation, restructure or decline" is the underwriting decision
    that fact implies. The alerts carry both, and the action is the half
    a page of numbers never says.
    """
    alerts = payload.get("alerts") or []
    if not alerts:
        return _note("No reading on this account crosses a house threshold: the loss ratio is "
                     "inside target, no single member dominates the claims, the outstanding "
                     "share is ordinary and the term is long enough to be credible.")
    rows = "".join(
        f'<div class="r {esc(a["severity"])}"><i></i><div>'
        f'<h4>{esc(a["title"])}</h4>'
        f'<p>{esc(a["message"])}</p>'
        f'<p class="act"><b>Do:</b> {esc(a["action"])}</p>'
        f'</div></div>'
        for a in alerts
    )
    return f'<div class="why">{rows}</div>'


_PILL_WORD = {"accept": "Accept", "review": "Review", "reject": "Reject"}


def _options_table(payload: dict) -> str:
    """The third question: what should the underwriter do.

    Every row is projected against the same trended claims, so the table
    can be read straight down the column. A commercial price measured
    against a different denominator to the technical one would leave a
    reader unable to say which option was better, which is the only thing
    the table is for.
    """
    pricing = payload.get("pricing") or {}
    options = [o for o in (pricing.get("options") or []) if o.get("premium") is not None]
    if not options:
        return ""
    target = pricing.get("target_loss_ratio")
    body = ""
    for o in options:
        decision = o.get("decision")
        pill = (f'<span class="pill {decision}">{_PILL_WORD.get(decision, "")}</span>'
                if decision else "&mdash;")
        change = o.get("change_pct")
        note = (f'<br><span class="note">{esc(o["note"])}</span>') if o.get("note") else ""
        move = (("+" if change >= 0 else "&minus;") + f"{abs(change)}%") if change else "&mdash;"
        body += (
            f'<tr{" class=lead" if o.get("key") == "technical" else ""}>'
            f'<td><strong>{esc(o["label"])}</strong>{note}</td>'
            f'<td class="num">{aed(o["premium"])}</td>'
            f'<td class="num">{move}</td>'
            f'<td class="num">{pct(o.get("projected_loss_ratio"))}</td>'
            f'<td class="num">{pill}</td></tr>'
        )
    minimum = pricing.get("minimum_acceptable_premium")
    combined = pricing.get("combined_ratio")
    floor = (
        f'the house minimum increase of {pct(pricing.get("minimum_increase_pct"), 0)} on the '
        f'expiring premium &mdash; this account&rsquo;s own claims would allow as little as '
        f'{aed(pricing.get("minimum_by_loss_ratio"))}, which the house does not write'
        if pricing.get("minimum_is_house_floor")
        else f'those same trended claims over {pct(target)}, derived rather than typed'
    )
    # The line is a COMBINED ratio, not a pure loss ratio, and saying so
    # is the difference between a figure a reader can check and one they
    # have to accept. A pure maximum is a different underwriting position
    # on every account, because it cannot see the expense load: 95% of
    # premium going to claims on a 21.5% load is a 116.5% combined ratio.
    line = (
        f'The line is a <strong>combined ratio</strong> of {pct(combined, 0)} &mdash; claims plus '
        f'expenses over premium &mdash; which on this account&rsquo;s {pct(pricing.get("loading_pct"))} '
        f'expense load leaves {pct(target)} for claims. '
        if (combined is not None and target is not None) else ""
    )
    return (
        '<table class="t"><thead><tr><th>Option</th><th class="num">Premium</th>'
        '<th class="num">vs expiring</th><th class="num">Projected loss ratio</th>'
        '<th class="num">Decision</th></tr></thead>'
        f'<tbody>{body}</tbody></table>'
        + _note(
            f'Projected loss ratio is this account&rsquo;s trended claims &mdash; '
            f'{aed(pricing.get("trended_claims"))}, what next year is expected to cost &mdash; '
            f'divided by the premium in that row. Not last year&rsquo;s claims: using those would '
            f'flatter every option by the whole of the inflation assumption. '
            f'A price is accepted at or above the minimum acceptable premium of '
            f'{aed(minimum)}, which is {floor}, and reviewed within '
            f'{pct(pricing.get("review_band_pct"), 0)} below it. {line}'
            f'The two reference rows carry no verdict: the expiring premium is what the other '
            f'rows are measured against, and the minimum acceptable IS the line.'
        )
    )


def _build_up(payload: dict) -> str:
    """The ladder as a waterfall whose bars reach its own final figure.

    Each step is the actual amount that step adds and the running total
    accumulates the ROUNDED steps, because a reader checks a waterfall by
    adding up the numbers in front of them. Where the house floor lifted
    the ask above what the experience built, that difference is a labelled
    step - the alternative is a total that does not equal its own parts,
    which invites exactly the check that then fails.
    """
    rows = (payload.get("pricing") or {}).get("build_up") or []
    if not rows:
        return ""
    top = max((r.get("running") or 0) for r in rows) or 1
    bars = bar_rows([
        {"label": esc(r["label"]),
         "value_text": aed(r.get("running")),
         "width": f'{(r.get("running") or 0) / top * 100:.1f}%',
         "fill": "navy" if r["amount"] is None else "",
         "key": r["amount"] is None,
         "value_class": "key" if r["amount"] is None else ""}
        for r in rows
    ], label_width=190)
    body = "".join(
        f'<tr><td>{esc(r["label"])}</td>'
        f'<td class="num">{aed(r["amount"]) if r["amount"] is not None else "&mdash;"}</td>'
        f'<td class="num">{aed(r.get("running"))}</td>'
        f'<td class="note">{esc(r.get("note") or "")}</td></tr>'
        for r in rows
    )
    return ('<section><h2 class="sec">The premium build-up</h2>'
            '<p class="desc">The same ladder in money rather than in ratios. Each bar is the '
            'running total, and the steps add up to the final one exactly &mdash; including the '
            'house minimum where it lifted the ask above what the experience built.</p>'
            + bars
            + '<table class="t" style="margin-top:13px"><thead><tr><th>Step</th>'
              '<th class="num">Adds</th><th class="num">Running</th><th>What it is</th>'
              '</tr></thead>'
            + f'<tbody>{body}</tbody></table></section>')


def _claims_performance(payload: dict) -> str:
    """What the year cost and the shape of it.

    Frequency counts claim LINES per member while the claimant ratio
    counts members with any claim at all - a member with five claims is
    five in one and one in the other, which is why both are here. An
    account can be expensive because a few people are very ill or because
    everybody goes to the doctor, and those are different renewals.
    """
    pricing = payload.get("pricing") or {}
    p = pricing.get("claims_performance") or {}
    if not p.get("claim_count"):
        return ""
    book = payload["book"]
    rows = [
        ("Total claim lines", aed(p.get("total_incurred")),
         f'the {p.get("claim_count"):,} lines on {esc(pricing.get("claims_source") or "file")} '
         f'for this term'),
        ("Number of claims", f'{p.get("claim_count"):,}', "individual claim lines, not claimants"),
        ("Members who claimed", f'{p.get("distinct_claimants"):,}',
         (f'{pct(p.get("claimant_ratio"))} of the census &mdash; the rest did not claim at all'
          if p.get("claimant_ratio") is not None else "distinct claimants")),
        ("Claim frequency",
         f'{p.get("claim_frequency")}' if p.get("claim_frequency") is not None else "&mdash;",
         "claims per member on the census. Counts a member with five claims five times"),
        ("Average claim cost", aed(p.get("average_claim_cost")), "per claim line"),
        ("Largest single claim", aed(p.get("largest_claim")), "one line, not one member"),
        ("Top ten claims", aed(p.get("top_ten_claims")),
         f'{pct(p.get("top_ten_share"))} of the total &mdash; ten lines out of '
         f'{p.get("claim_count"):,}'),
        ("Chronic conditions", aed(p.get("chronic_claims")),
         (f'{pct(p.get("chronic_share"))} of the total, from '
          f'{p.get("classified_claims"):,} of {p.get("claim_count"):,} claims that carry a usable '
          f'diagnosis. An <strong>estimate</strong>: read off each claim&rsquo;s diagnosis chapter, '
          f'and a chapter blends ongoing conditions with one-off events. No claims file marks a '
          f'claim as chronic'
          if p.get("classified_claims")
          else 'Not measured. No claim on this account carries a diagnosis code that resolves to '
               'an ICD-10 chapter, so there is nothing to classify &mdash; which is a fact about '
               'the claims export, not about the account')),
        ("High-cost members", f'{p.get("high_cost_members"):,}',
         f'members whose own claims reach {aed(p.get("high_cost_threshold"))} &mdash; '
         f'{aed(p.get("high_cost_incurred"))} between them. The same line large-loss analysis uses'),
    ]
    # The claim lines and the incurred figure are not the same number and
    # never will be: the incurred figure carries a reserve for claims that
    # have not been reported yet, and a reserve has no lines. Stating the
    # difference is the only thing that stops a reader treating one of the
    # two as an error.
    total, incurred = p.get("total_incurred") or 0, book.get("incurred_claims") or 0
    gap = round(incurred - total, 2)
    reconcile = _note(
        f'These lines total {aed(total)}. The renewal is priced from incurred claims of '
        f'{aed(incurred)} &mdash; a difference of {aed(abs(gap))}, which is the '
        f'{aed(book.get("ibnr"))} reserved for claims incurred but not yet reported. A reserve '
        f'has no claim lines, so it cannot appear in the table above.'
    ) if abs(gap) >= 1 else ""
    return ('<section><h2 class="sec">Claims performance</h2>'
            '<p class="desc">What the year cost and the shape of it. Frequency counts claim '
            'lines per member; the claimant ratio counts members with any claim at all. An '
            'account can be expensive because a few people are very ill or because everybody '
            'goes to the doctor, and those are different renewals.</p>'
            '<div class="perf">' + _rows(rows) + "</div>" + reconcile + "</section>")


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
        f'<button onclick="dl()">Download PDF</button>'
        f"</div>"
        # The browser adds its own date, URL and page counter unless that
        # one box is unticked. It is a setting the browser remembers, not
        # something the page can turn off - so the page says so once,
        # plainly, the first time rather than leaving it a mystery.
        '<div class="dlhint" id="dlhint">In the print dialog choose '
        '<strong>Destination: Save as PDF</strong> and untick '
        '<strong>Headers and footers</strong> &mdash; your browser remembers it after the first '
        'time. <button onclick="document.getElementById(\'dlhint\').remove()">Got it</button></div>'
        "<script>function setView(v){document.documentElement.setAttribute('data-view',v);"
        "document.getElementById('v-int').className=(v==='internal'?'on':'');"
        "document.getElementById('v-cli').className=(v==='client'?'on':'');}"
        "function dl(){var h=document.getElementById('dlhint');"
        "if(h){h.classList.add('show');setTimeout(function(){window.print();},60);}"
        "else{window.print();}}</script>"
    )


_TONE_CLASS = {"": "bad", "warn": "warn", "ok": "ok"}


def _lr_class(value) -> str:
    if value is None:
        return "grey"
    return "bad" if value > 1.0 else "warn" if value >= 0.7 else "ok"


_PLURALS = {"child": "children"}


def _plural(n, word: str) -> str:
    return f"{n:,} {word if n == 1 else _PLURALS.get(word, word + 's')}"


def _summary_head(payload: dict, today: date) -> str:
    case, book, census = payload["case"], payload["book"], payload.get("census") or {}
    rel = {r["label"].lower(): r["count"] for r in (census.get("relations") or [])}
    lives = book.get("member_count")
    mix = ", ".join(_plural(rel[k], k) for k in ("employee", "spouse", "child") if rel.get(k))
    bits = [f'<b>{esc(case.get("product"))}</b>' if case.get("product") else "",
            f'{lives} lives' + (f' ({mix})' if mix else "") if lives else "",
            f'broker {esc(case.get("broker_name"))}' if case.get("broker_name") else "",
            f'term {_day(book.get("policy_start_date"))} &ndash; {_day(case.get("term_end_date"))}'
            if (book.get("policy_start_date") and case.get("term_end_date")) else ""]
    base = payload["rating"].get("renewal_base_premium")
    line2 = (f'Renewal Review &middot; internal &nbsp;&middot;&nbsp; '
             f'experience to {_day(book.get("as_of"))}, {book.get("days")} days on risk'
             + (f' &nbsp;&middot;&nbsp; expiring premium {aed(base)}' if base else "")
             + f' &nbsp;&middot;&nbsp; prepared {long_date(today)}')
    return (f'<div class="pad sv-head"><h1>{esc(case.get("company_name"))}</h1>'
            f'<div class="sub">{" &nbsp;&middot;&nbsp; ".join(b for b in bits if b)}<br>{line2}</div></div>')


def _verdict_strip(payload: dict) -> str:
    """Is it risky? - the verdict, the ask, gross and net, on one strip."""
    book, rating = payload["book"], payload["rating"]
    word, tone, say = _verdict(payload)
    gross, net = book.get("gross_loss_ratio"), book.get("net_loss_ratio")
    inc = rating.get("renewal_increase_pct")
    override = rating.get("increase_source") == "override"
    if inc is None:
        ask_v, ask_s = "&mdash;", "not priced - see below"
    else:
        ask_v = f'{"+" if inc >= 0 else "&minus;"}{abs(inc)}%'
        ask_s = f'{aed(rating.get("required_premium"))} &middot; ' + (
            "an underwriting decision" if override else "the experience, unadjusted")
    verdict_why = (
        "Paying less than it costs." if (gross or 0) >= 1.0
        else "Inside the gross target, outside the net one: expenses take it over the line."
        if (net or 0) >= 1.0 else "Inside both targets."
    ) if gross is not None else ""
    # An overridden ask is a decision, not a measurement - the one line a
    # reader is certain to take away says which figure it is naming.
    if override or inc is None:
        verdict_why = say
    return (
        '<div class="sv-strip">'
        f'<div class="rec"><div class="sv-k">Verdict</div>'
        f'<div class="sv-v"><span class="sv-pill {_TONE_CLASS.get(tone, "bad")}">{word}</span></div>'
        f'<div class="sv-s">{verdict_why}</div></div>'
        f'<div><div class="sv-k">{"Quoted renewal increase" if override else "Renewal increase"}</div>'
        f'<div class="sv-v{" override" if override else ""}">{ask_v}</div><div class="sv-s">{ask_s}</div></div>'
        f'<div><div class="sv-k">Gross loss ratio</div>'
        f'<div class="sv-v"><span class="sv-lr {_lr_class(gross)}">{pct(gross)}</span></div>'
        f'<div class="sv-s">{aed(book.get("incurred_claims"))} claims on {aed(book.get("earned_premium"))} earned</div></div>'
        f'<div><div class="sv-k">Net loss ratio</div>'
        f'<div class="sv-v"><span class="sv-lr {_lr_class(net)}">{pct(net)}</span></div>'
        f'<div class="sv-s">{_loading_short(book)}</div></div>'
        '</div>'
    )


def _why_list(payload: dict) -> str:
    """Why? - in sentences. The ratio story, the population and the shape
    of the claims, the movement, and then every reading that crosses a
    house threshold with what to do about it."""
    book, rating = payload["book"], payload["rating"]
    census, pop = payload.get("census") or {}, payload.get("population") or {}
    gross, net = book.get("gross_loss_ratio"), book.get("net_loss_ratio")
    items = []
    if gross is not None and net is not None:
        items.append((
            _lr_class(net) if _lr_class(net) != "grey" else "",
            f'<b>The account is at {pct(gross)} gross and {pct(net)} net</b> &mdash; nothing changed '
            f'between the two except that expenses came out of the premium first. '
            f'{_sentence(_net_ratio_note(book, rating))}'))
    rel = {r["label"].lower(): r["count"] for r in (census.get("relations") or [])}
    emp = rel.get("employee") or 0
    deps = (rel.get("spouse") or 0) + (rel.get("child") or 0)
    enc = {e["encounter_type"].lower(): e for e in (payload.get("encounter_split") or [])}
    incurred = book.get("incurred_claims") or 0
    top = payload.get("top_claimants") or []
    top5 = sum((c.get("incurred") or 0) for c in top[:5])
    pop_bits = []
    if emp:
        pop_bits.append(f'<b>Dependants outnumber employees {deps / emp:.1f} to 1</b> &mdash; '
                        f'{_plural(rel.get("child") or 0, "child")} and '
                        f'{_plural(rel.get("spouse") or 0, "spouse")} on {_plural(emp, "employee")}'
                        if deps > emp else
                        f'<b>{_plural(emp, "employee")}, {deps} dependants</b>')
        if census.get("average_age") is not None:
            pop_bits[-1] += f'; average age {census["average_age"]:.0f}'
    shape = ""
    if enc.get("op") and enc["op"].get("pct_of_total") is not None:
        shape = f'Claims are broad ({enc["op"]["pct_of_total"]:.0f}% outpatient)' if enc["op"]["pct_of_total"] >= 60 else \
                f'Claims are {100 - enc["op"]["pct_of_total"]:.0f}% inpatient and maternity'
    if incurred and top5:
        share = top5 / incurred
        shape += (f'{", " if shape else ""}{"concentrated" if share >= 0.4 else "not concentrated"}: '
                  f'the top five members carry {pct(share, 0)} of the total')
    if pop_bits or shape:
        items.append(("warn" if (emp and deps > emp * 1.5) else "ok",
                      ". ".join(b for b in pop_bits + ([shape[0].upper() + shape[1:]] if shape else []) if b) + "."))
    if pop.get("deleted_member_count"):
        leaving = (pop.get("leaving") or {}).get("incurred")
        items.append(("", f'<b>{_plural(pop["deleted_member_count"], "member")} left mid-term</b>'
                          + (f' taking {aed(leaving)} of claims with them' if leaving else "")
                          + f'; {pop.get("active_member_count")} continue.'))
    alerts = payload.get("alerts") or []
    for a in alerts:
        items.append(("bad" if a["severity"] == "critical" else "warn" if a["severity"] == "high" else "",
                      f'<b>{esc(a["title"])}.</b> {esc(a["message"])} '
                      f'<span class="act"><b>Do:</b> {esc(a["action"])}</span>'))
    if not alerts:
        items.append(("ok", "No reading on this account crosses a house threshold: the loss ratio is inside "
                            "target, no single member dominates the claims, the outstanding share is ordinary "
                            "and the term is long enough to be credible."))
    return '<ul class="sv-why">' + "".join(f'<li class="{cls}"><i></i><span>{text}</span></li>' for cls, text in items) + "</ul>"


def _ladder_row(payload: dict) -> str:
    """How the ask is built - the ladder as one row a reader can walk."""
    ladder, rating = payload.get("ladder") or {}, payload["rating"]
    if not ladder or ladder.get("required_share_of_expiring") is None:
        return _the_ask(payload)  # the not-priced callout, with why
    fees = payload.get("case") or {}
    inc = ladder.get("renewal_increase_pct")
    floor = ladder.get("floor_applied")
    return (
        '<div class="sv-ladder">'
        f'<div><div class="sv-k">Loss ratio</div><div class="n">{pct(ladder.get("loss_ratio"))}</div><div class="sv-s">this term, gross</div></div>'
        f'<div><div class="sv-k">+ Inflation</div><div class="n">+{(ladder.get("inflation_pts") or 0) * 100:.1f} pts</div><div class="sv-s">medical trend carried forward</div></div>'
        f'<div><div class="sv-k">Trended</div><div class="n">{pct(ladder.get("trended_loss_ratio"))}</div><div class="sv-s">what next year costs</div></div>'
        f'<div><div class="sv-k">&divide; (1 &minus; loading)</div><div class="n">{pct(ladder.get("loading_pct"))}</div><div class="sv-s">the fee split entered on the case</div></div>'
        f'<div><div class="sv-k">Required</div><div class="n">{pct(ladder.get("required_share_of_expiring"))}</div>'
        f'<div class="sv-s">of expiring &rarr; <b>{"+" if (inc or 0) >= 0 else "&minus;"}{abs(inc or 0)}%</b>'
        + (f' &middot; house floor of {pct(ladder.get("minimum_increase_pct"), 0)} applied' if floor else "")
        + '</div></div></div>'
    )


def _price_table(payload: dict) -> str:
    """What should we do? - each price with the loss ratio it lands on and
    a decision. Options nobody has entered collapse to one line."""
    pricing = payload.get("pricing") or {}
    options = pricing.get("options") or []
    priced = [o for o in options if o.get("premium") is not None]
    if not priced:
        return ""
    empty = [o for o in options if o.get("premium") is None]
    rows = ""
    for o in priced:
        decision = o.get("decision")
        if decision:
            pill = f'<span class="sv-pill {"ok" if decision == "accept" else "warn" if decision == "review" else "bad"}">{_PILL_WORD.get(decision, "")}</span>'
        elif o.get("key") == "expiring":
            pill = '<span class="sv-pill grey">Reference</span>'
        elif o.get("key") == "minimum_acceptable":
            pill = '<span class="sv-pill grey">The line</span>'
        else:
            pill = "&mdash;"
        change = o.get("change_pct")
        move = (("+" if change >= 0 else "&minus;") + f"{abs(change)}%") if change else "&mdash;"
        plr = o.get("projected_loss_ratio")
        rows += (f'<tr{" class=lead" if o.get("key") == "technical" else ""}>'
                 f'<td>{esc(o["label"])}<span class="note">{esc(o.get("note") or "")}</span></td>'
                 f'<td class="num">{aed(o["premium"])}</td><td class="num">{move}</td>'
                 f'<td class="num"><span class="sv-lr small {_lr_class(plr)}">{pct(plr)}</span></td>'
                 f'<td>{pill}</td></tr>')
    if empty:
        rows += (f'<tr><td>{" / ".join(esc(o["label"].replace(" strategy", "")) for o in empty)}'
                 f'<span class="note">not yet entered</span></td><td class="num">&mdash;</td>'
                 f'<td class="num">&mdash;</td><td class="num">&mdash;</td><td>&mdash;</td></tr>')
    target, combined = pricing.get("target_loss_ratio"), pricing.get("combined_ratio")
    line = (f' The line is a {pct(combined, 0)} combined ratio &mdash; claims plus expenses &mdash; '
            f'leaving {pct(target)} for claims on this fee split.'
            if (combined is not None and target is not None) else "")
    return (
        '<table class="sv"><thead><tr><th>Option</th><th class="num">Premium</th>'
        '<th class="num">vs expiring</th><th class="num">Projected LR</th><th>Decision</th></tr></thead>'
        f'<tbody>{rows}</tbody></table>'
        f'<div class="sv-foot">Projected LR = trended claims of {aed(pricing.get("trended_claims"))} '
        f'&divide; the premium in that row. Accept at or above the minimum acceptable of '
        f'{aed(pricing.get("minimum_acceptable_premium"))}; review within '
        f'{pct(pricing.get("review_band_pct"), 0)} below it.{line}</div>'
    )


def _claims_shape(payload: dict) -> str:
    enc = payload.get("encounter_split") or []
    perf = (payload.get("pricing") or {}).get("claims_performance") or {}
    diags = [d["label"] for d in (payload.get("top_diagnoses") or [])[:3] if d.get("label")]
    top = payload.get("top_claimants") or []
    incurred = payload["book"].get("incurred_claims") or 0
    if not enc and not perf:
        return ""
    bars = "".join(
        f'<div><span>{esc((e.get("encounter_type") or "").replace("Op", "Outpatient").replace("Ip", "Inpatient"))}</span>'
        f'<i><b style="width:{min(100, e.get("pct_of_total") or 0):.0f}%"></b></i>'
        f'<span class="val">{(e.get("pct_of_total") or 0):.0f}%</span></div>'
        for e in enc
    )
    bits = []
    if perf.get("claim_count"):
        bits.append(f'{perf["claim_count"]:,} claim lines at {aed(perf.get("average_claim_cost"))} average; '
                    f'{pct(perf.get("claimant_ratio"), 0)} of members claimed')
    if diags:
        bits.append("Top diagnoses: " + ", ".join(esc(d.split(",")[0].lower()) for d in diags))
    if top and incurred:
        t = top[0]
        top5 = sum((c.get("incurred") or 0) for c in top[:5])
        bits.append(f'Top five claimants {aed(top5)} ({pct(top5 / incurred, 0)}), highest a '
                    f'{t.get("age")}-year-old {esc(t.get("relation") or "member")} at {aed(t.get("incurred"))}')
    return (f'<div><h2 class="sv">What the claims are made of</h2><div class="sv-bars">{bars}</div>'
            f'<div class="sv-foot">{". ".join(bits)}.</div></div>')


def _account_block(payload: dict) -> str:
    case, book, census, pop = payload["case"], payload["book"], payload.get("census") or {}, payload.get("population") or {}
    rel = {r["label"].lower(): r["count"] for r in (census.get("relations") or [])}
    gen = {g["label"].upper(): g["count"] for g in (census.get("genders") or [])}
    facts = [
        ("Product", esc(case.get("product")) if case.get("product") else "&mdash;"),
        ("Lives", f'{book.get("member_count") or "&mdash;"}'),
        ("Average age", f'{census["average_age"]:.0f}' if census.get("average_age") is not None else "&mdash;"),
        ("Employees", f'{rel.get("employee", 0)}'), ("Spouses", f'{rel.get("spouse", 0)}'), ("Children", f'{rel.get("child", 0)}'),
        ("Male / Female", f'{gen.get("M", 0)} / {gen.get("F", 0)}'),
        ("Left mid-term", f'{pop.get("deleted_member_count", 0)}'),
        ("Policy start", _day(book.get("policy_start_date"))),
    ]
    return ('<div><h2 class="sv">The account</h2><div class="sv-facts">'
            + "".join(f'<div><span>{k}</span><b>{v}</b></div>' for k, v in facts) + '</div></div>')


def _summary_body(payload: dict, today: date) -> str:
    """Three questions, in the order they get asked - is it risky, why,
    what should we do - on one page, the answer first.

    The verdict, the ask and both loss ratios sit on one strip at the
    top; the why is sentences rather than a card per alert; the ladder is
    one row; the price options carry a decision each; and the claims
    shape and the account block let the page stand alone in a meeting.
    Everything that EXPLAINS these - the build-up, the census, the claims
    detail - is a section of the report, not of this page.
    """
    book = payload["book"]
    return (
        _masthead(payload)
        + _summary_head(payload, today)
        + '<div class="pad">'
        + '<h2 class="sv">Is it risky?</h2>'
        + _verdict_strip(payload)
        + _quoted_vs_computed(payload)
        + '<h2 class="sv">Why?</h2>'
        + _why_list(payload)
        + '<h2 class="sv">How the ask is built</h2>'
        + _ladder_row(payload)
        + '<h2 class="sv">What should we do?</h2>'
        + _price_table(payload)
        + '<div class="sv-two">' + _claims_shape(payload) + _account_block(payload) + '</div>'
        + '<div class="sv-sign"><div>Underwriter</div><div>Decision &amp; premium issued</div><div>Date</div></div>'
        + f'<div class="sv-foot">HealthCross Underwriting Intelligence &middot; every figure from the Portfolio '
          f'Loss Ratio book as of {_day(book.get("as_of"))}; claims incl. IBNR.</div>'
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
    payload = {**payload, "_doc_title": "Renewal Summary"}
    body = (
        _summary_body(payload, today)
        + _footer("HealthCross &middot; Renewal Summary", esc(company))
    )
    return (
        _HEAD_PLAIN.format(title=esc(f"{company} - Renewal Summary"), css=STYLESHEET + EXTRA_CSS)
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
    payload = {**payload, "_doc_title": "Renewal Report"}
    body = (
        _summary_body(payload, today)
        + '<div class="pad">'
        + _section("02", "Pricing", "How the technical premium was arrived at. The summary states "
                                    "the ask; this is the arithmetic behind it, step by step, so "
                                    "it can be checked rather than trusted.",
                   _ladder(payload) + _build_up(payload) + _premiums(payload))
        + _section("03", "Census", "The population being renewed - who is on risk, and how it "
                                   "has moved since the expiring term.",
                   _census(payload) + _population(payload))
        + _section("04", "Benefits", "The cover behind the price. The same loss ratio on a "
                                     "different annual limit is a different account.",
                   _benefits(payload))
        + _section("05", "Claims", "What the money went on: the shape of the year, who carried "
                                   "it, and what for.",
                   _claims_performance(payload) + _claims_buildup(payload) + _monthly(payload)
                   + _encounters(payload) + _claimants(payload) + _diagnoses(payload))
        + _section("06", "Basis", "What every figure above was measured on, so it can be "
                                  "checked rather than trusted.",
                   _basis(payload), internal_only=True)
        + "</div>"
        + _footer("HealthCross &middot; Renewal Report", esc(company))
    )
    return (
        _HEAD_PLAIN.format(title=esc(f"{company} - Renewal Report"), css=STYLESHEET + EXTRA_CSS)
        + _toolbar(f"{esc(company)} &middot; Renewal Report",
                   f'/cases/{payload["case"]["id"]}/renewal-report.html')
        + _page("Renewal report", body)
        + "</body></html>"
    )
