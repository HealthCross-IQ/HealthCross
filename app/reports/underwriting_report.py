"""The four-page underwriting document, rendered on the server.

It used to be assembled in the browser from six separate fetches. That
had two problems and they turned out to be the same problem. The fetches
took long enough that `window.open` no longer counted as a response to
the click, so the browser silently killed the tab - a print button that
did nothing, twice. And the assembly lived in a template string inside
one 4,000-line HTML file, where a mis-typed field name produced a blank
section rather than an error.

Rendering here fixes both. The button becomes `window.open(url)` with
nothing awaited, which no pop-up blocker can refuse, and the document is
built from the same payload the API already returns, so the screen and
the print cannot disagree about what this case is.

Everything is None-safe on purpose. A new enquiry legitimately has no
claims report, no incumbent plan, and no issued quote, and a report that
throws on a case that has not been fully set up yet is a report nobody
can use early - which is exactly when an underwriter wants it. A missing
figure prints an em-dash and its section says what is missing; nothing
here fills a gap with a plausible number.

The design follows the brand guidelines: Dark Blue #1C2947 and the Light
Blue the artwork actually uses (#4AB0E3 - the guidelines print #5CD9FF
but every swatch and logo in that same document renders the former).
"""
import base64
import html
from datetime import date
from pathlib import Path
from typing import List, Optional

_LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "hc-logo-horizontal.png"
_logo_cache: Optional[str] = None


def _logo_img(height: int = 38) -> str:
    """The lockup, inlined so the document survives being saved to disk
    or emailed as a single file.
    """
    global _logo_cache
    if _logo_cache is None:
        try:
            _logo_cache = base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
        except OSError:
            _logo_cache = ""
    if not _logo_cache:
        return '<span style="font-weight:800;color:#1c2947;letter-spacing:-.02em">Health Cross</span>'
    return (f'<img class="hc-logo" style="height:{height}px" '
            f'src="data:image/png;base64,{_logo_cache}" alt="Health Cross">')


# --- formatting ---------------------------------------------------------
#
# One rule throughout: a value that is not known prints as an em-dash.
# Rendering an unknown as 0 would be a factual claim the data does not
# support, and 0 is a number a reader will act on.

DASH = "&mdash;"


def esc(value) -> str:
    return html.escape(str(value)) if value is not None else ""


def aed(value, decimals: int = 0) -> str:
    if value is None:
        return DASH
    return f"{value:,.{decimals}f}"


def pct(value, decimals: int = 1) -> str:
    """A fraction as a percentage. 0.173 -> 17.3%."""
    if value is None:
        return DASH
    return f"{value * 100:.{decimals}f}%"


def signed_pct(value, decimals: int = 0) -> str:
    if value is None:
        return DASH
    return f"{value * 100:+.{decimals}f}%".replace("-", "&minus;")


def _band_letter(band: Optional[str]) -> str:
    return {"high": "r", "medium": "a", "low": "g"}.get(band or "", "n")


def _width(value: Optional[float], of: Optional[float]) -> str:
    """A bar width as a percentage of the largest figure in its group,
    clamped so a bar never runs off its track.
    """
    if not value or not of:
        return "0%"
    return f"{max(0.0, min(100.0, value / of * 100)):.1f}%"


def _note(text: str) -> str:
    """What stands in for a section whose data has not arrived yet."""
    return f'<p class="missing">{text}</p>'


# --- small chart primitives ---------------------------------------------


def bar_rows(rows: List[dict], label_width: int = 120, value_width: int = 96) -> str:
    """rows: [{label, value_text, width, fill, key, value_class}]"""
    out = []
    for r in rows:
        out.append(
            f'<div class="bar-row" style="grid-template-columns:{label_width}px 1fr {value_width}px">'
            f'<span class="lbl{" key" if r.get("key") else ""}">{r["label"]}</span>'
            f'<span class="bar-track"><span class="bar-fill {r.get("fill", "")}" '
            f'style="width:{r["width"]}"></span></span>'
            f'<span class="val {r.get("value_class", "")}">{r["value_text"]}</span></div>'
        )
    return "".join(out)


def donut(segments: List[dict], centre: str, caption: str) -> str:
    """segments: [{value, colour, label}]. An SVG rather than a canvas so
    it survives printing and a PDF export.
    """
    total = sum(s["value"] for s in segments) or 1
    radius, circumference = 54, 2 * 3.14159265 * 54
    offset, arcs = 0.0, []
    for s in segments:
        length = circumference * (s["value"] / total)
        arcs.append(
            f'<circle cx="75" cy="75" r="{radius}" stroke="{s["colour"]}" '
            f'stroke-dasharray="{length:.2f} {circumference - length:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}"/>'
        )
        offset += length
    return (
        '<svg width="150" height="150" viewBox="0 0 150 150" role="img" '
        f'aria-label="{esc(caption)}">'
        '<g transform="rotate(-90 75 75)" fill="none" stroke-width="22">' + "".join(arcs) + "</g>"
        f'<text x="75" y="70" text-anchor="middle" font-family="IBM Plex Mono" font-size="27" '
        f'font-weight="700" fill="#1c2947">{centre}</text>'
        f'<text x="75" y="88" text-anchor="middle" font-family="IBM Plex Mono" font-size="9" '
        f'fill="#8e94a3" letter-spacing="1.5">{esc(caption)}</text></svg>'
    )


def gauge(score: Optional[float], band: Optional[str], caption: str) -> str:
    """A 270-degree arc. None draws the empty track and says so, rather
    than drawing a zero - an unscored account is not a zero-scoring one.
    """
    colour = {"high": "#c8443f", "medium": "#c98a2b", "low": "#2c8f74"}.get(band or "", "#8e94a3")
    import math

    start, sweep = 135.0, 270.0
    def point(angle_deg: float) -> str:
        rad = math.radians(angle_deg)
        return f"{90 + 66 * math.cos(rad):.2f} {90 + 66 * math.sin(rad):.2f}"

    track = f'<path d="M {point(start)} A 66 66 0 1 1 {point(start + sweep)}" fill="none" stroke="#d2ebf8" stroke-width="15" stroke-linecap="round"/>'
    fill = ""
    if score is not None:
        end = start + sweep * max(0.0, min(100.0, score)) / 100.0
        large = 1 if (end - start) > 180 else 0
        fill = (f'<path d="M {point(start)} A 66 66 0 {large} 1 {point(end)}" fill="none" '
                f'stroke="{colour}" stroke-width="15" stroke-linecap="round"/>')
    number = f"{score:.0f}" if score is not None else DASH
    return (
        '<svg width="180" height="150" viewBox="0 0 180 150" role="img" '
        f'aria-label="Risk score {number} out of 100">{track}{fill}'
        f'<text class="num" x="90" y="92" text-anchor="middle">{number}</text>'
        f'<text class="den" x="90" y="110" text-anchor="middle">/100</text>'
        f'<text class="cap" x="90" y="146" text-anchor="middle" fill="{colour}">{esc(caption)}</text></svg>'
    )


def month_amount(point: dict) -> float:
    """The paid figure on one monthly row.

    The claims-report parsers emit `paid`; every other monthly series in
    the portal calls it `amount`. Reading only one of them meant the
    chart raised on the real reports and drew fine on a fixture, which
    is the wrong way round.
    """
    for key in ("paid", "amount", "value"):
        if point.get(key) is not None:
            return point[key]
    return 0.0


def month_label(point: dict) -> str:
    """Three letters, whatever shape the month arrived in - the parsers
    give "Jan", other series give "2026-01".
    """
    raw = str(point.get("month") or "")
    if "-" in raw:
        month_number = raw.split("-")[-1]
        names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                 "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        if month_number.isdigit() and 1 <= int(month_number) <= 12:
            return names[int(month_number) - 1]
    return raw[:3].title()


def area_chart(points: List[dict], width: int = 700, height: int = 140) -> str:
    """Monthly claims as a filled line. Reading a run of twelve figures
    off a table does not show a trend; this does.
    """
    values = [month_amount(p) for p in points]
    if len(values) < 2:
        return ""
    top = max(values) or 1
    pad_l, pad_b = 6, 20
    span = width - pad_l * 2
    step = span / (len(values) - 1)
    plot_h = height - pad_b - 6

    def xy(i, v):
        return f"{pad_l + i * step:.1f},{6 + plot_h - (v / top) * plot_h:.1f}"

    line = " ".join(xy(i, v) for i, v in enumerate(values))
    fill = f"{pad_l},{6 + plot_h:.1f} {line} {pad_l + span:.1f},{6 + plot_h:.1f}"
    mean = sum(values) / len(values)
    mean_y = 6 + plot_h - (mean / top) * plot_h
    labels = "".join(
        f'<text x="{pad_l + i * step:.1f}" y="{height - 6}" text-anchor="middle" '
        f'font-family="IBM Plex Mono" font-size="8" fill="#8e94a3">{esc(month_label(p))}</text>'
        for i, p in enumerate(points)
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img" '
        'aria-label="Monthly paid claims">'
        f'<polygon points="{fill}" fill="#d2ebf8"/>'
        f'<polyline points="{line}" fill="none" stroke="#4ab0e3" stroke-width="2"/>'
        f'<line x1="{pad_l}" y1="{mean_y:.1f}" x2="{pad_l + span:.1f}" y2="{mean_y:.1f}" '
        'stroke="#1c2947" stroke-width="1" stroke-dasharray="4 3"/>'
        # Parked in the corner rather than beside its own line: at the
        # right-hand edge it sat directly on the data wherever the last
        # month ran close to average, which is most of the time.
        f'<text x="{pad_l + span:.1f}" y="12" text-anchor="end" font-family="IBM Plex Mono" '
        f'font-size="9" fill="#1c2947">MONTHLY AVERAGE {aed(mean)}</text>'
        f'{labels}</svg>'
    )


def tag(text: str, letter: str) -> str:
    return f'<span class="tag {letter}">{esc(text)}</span>'


# --- the stylesheet -----------------------------------------------------

STYLESHEET = """
:root{
  --ground:#f2f8fc; --paper:#ffffff; --rule:#dfe8f0;
  --navy:#1c2947; --navy-85:#3e4963; --navy-50:#8e94a3; --navy-25:#c6c9d1;
  --sky:#4ab0e3; --sky-85:#65bce7; --sky-50:#a4d7f1; --sky-25:#d2ebf8;
  --ink:#1c2a48; --muted:#6b7789;
  --alert:#c8443f; --alert-wash:#fbecea;
  --warn:#c98a2b; --warn-wash:#fbf3e6;
  --ok:#2c8f74; --ok-wash:#e9f4f1;
  --display:'Montserrat','Proxima Nova','Mulish',system-ui,sans-serif;
  --sans:'Proxima Nova','Mulish',system-ui,-apple-system,sans-serif;
  --mono:'IBM Plex Mono',ui-monospace,Menlo,monospace;
}
*{box-sizing:border-box}
body{background:var(--ground);color:var(--ink);font-family:var(--sans);font-size:13px;line-height:1.55;margin:0;padding:26px 14px 70px}
.paper{max-width:920px;margin:0 auto 26px}
.page-tag{font-family:var(--mono);font-size:9.5px;font-weight:500;letter-spacing:.11em;text-transform:uppercase;color:var(--navy-50);margin-bottom:7px}
.doc{background:var(--paper);border:1px solid var(--rule);box-shadow:0 1px 2px rgba(28,42,72,.05),0 22px 50px -34px rgba(28,42,72,.45);padding:0 0 34px;overflow:hidden}
.pad{padding:0 44px}

.masthead{background:var(--navy);color:#fff;padding:20px 44px;display:flex;justify-content:space-between;align-items:center;gap:20px;flex-wrap:wrap}
.logo{display:flex;align-items:center;gap:13px;background:#fff;padding:9px 14px;border-radius:2px}
.hc-logo{width:auto;display:block}
.masthead .meta{text-align:right;font-family:var(--mono);font-size:9.5px;color:var(--sky-50);line-height:1.8}

.eyebrow{font-family:var(--mono);font-size:9.5px;font-weight:500;letter-spacing:.11em;text-transform:uppercase;color:var(--sky);margin-bottom:7px}
h1{font-family:var(--display);font-size:29px;font-weight:800;letter-spacing:-.025em;margin:0 0 14px;color:var(--navy);line-height:1.08}

.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--rule);border-top:1px solid var(--rule);border-bottom:1px solid var(--rule);margin-bottom:26px}
.kpi{background:var(--paper);padding:12px 14px}
.kpi .l{display:block;font-family:var(--mono);font-size:8.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin-bottom:5px;line-height:1.35}
.kpi .v{display:block;font-family:var(--mono);font-size:18px;font-weight:600;color:var(--navy);letter-spacing:-.01em;line-height:1.15}
.kpi .v.bad{color:var(--alert)} .kpi .v.good{color:var(--ok)} .kpi .v.warn{color:var(--warn)}
.kpi .f{display:block;font-size:10px;color:var(--muted);margin-top:3px}

section{margin-bottom:26px}
h2.sec{font-family:var(--display);font-size:15px;font-weight:700;color:var(--navy);margin:0 0 3px;letter-spacing:-.01em}
.desc{font-size:10.5px;color:var(--muted);margin:0 0 13px;max-width:74ch;line-height:1.55}
h3{font-family:var(--display);font-size:12.5px;font-weight:700;color:var(--navy);margin:18px 0 8px;letter-spacing:-.005em}
.split{display:grid;grid-template-columns:1fr 1fr;gap:32px}
.missing{font-size:11.5px;color:var(--muted);background:var(--sky-25);border-left:3px solid var(--sky-50);padding:11px 14px;margin:0;line-height:1.6}

.bar-row{display:grid;grid-template-columns:120px 1fr 96px;align-items:center;gap:10px;margin-bottom:7px}
.bar-row .lbl{font-size:11px}
.bar-row .lbl.key{font-weight:700;color:var(--navy)}
.bar-track{background:var(--sky-25);height:19px;position:relative}
.bar-fill{height:100%;background:var(--sky);display:block}
.bar-fill.navy{background:var(--navy)} .bar-fill.bad{background:var(--alert)}
.bar-fill.ok{background:var(--ok)} .bar-fill.soft{background:var(--sky-50)}
.bar-row .val{font-family:var(--mono);font-size:11px;text-align:right;font-variant-numeric:tabular-nums}
.bar-row .val.bad{color:var(--alert);font-weight:600} .bar-row .val.good{color:var(--ok);font-weight:600}
.caption{font-family:var(--mono);font-size:9px;color:var(--muted);margin-top:8px;letter-spacing:.03em;line-height:1.55}

table.data{width:100%;border-collapse:collapse;font-size:11px}
table.data th{text-align:left;font-family:var(--mono);font-size:8.5px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:500;padding:0 8px 6px 0;border-bottom:2px solid var(--navy)}
table.data td{padding:7px 8px 7px 0;border-bottom:1px solid var(--rule);font-family:var(--mono);vertical-align:middle}
table.data td:first-child{font-family:var(--sans)}
table.data tr.emphatic td{border-top:2px solid var(--navy);border-bottom:none;font-weight:700;color:var(--navy);padding-top:9px}
.num{text-align:right} .scroll{overflow-x:auto}

.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;vertical-align:-1px;background:var(--navy-25)}
.dot.r{background:var(--alert)} .dot.a{background:var(--warn)} .dot.g{background:var(--ok)}
.mini{display:inline-block;width:58px;height:7px;background:var(--sky-25)}
.mini i{display:block;height:100%;background:var(--navy-25)}
.mini i.r{background:var(--alert)} .mini i.a{background:var(--warn)} .mini i.g{background:var(--ok)}

.tag{display:inline-block;font-family:var(--mono);font-size:8px;font-weight:500;text-transform:uppercase;letter-spacing:.06em;padding:2px 7px;white-space:nowrap}
.tag.r{background:var(--alert-wash);color:var(--alert)}
.tag.a{background:var(--warn-wash);color:var(--warn)}
.tag.g{background:var(--ok-wash);color:var(--ok)}
.tag.n{background:var(--sky-25);color:var(--navy-85)}

.bridge{display:grid;grid-template-columns:1fr 50px 1fr 50px 1fr;align-items:end;margin:6px 0 2px}
.bridge .col{text-align:center}
.bridge .bcap{font-family:var(--mono);font-size:8.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);margin-bottom:6px}
.bridge .bbar{margin:0 auto;width:72%;background:var(--sky-50);min-height:3px}
.bridge .bbar.tech{background:var(--navy)} .bridge .bbar.comm{background:var(--alert)}
.bridge .bval{font-family:var(--mono);font-size:12px;font-weight:700;color:var(--navy);margin-top:7px}
.bridge .bval.comm{color:var(--alert)}
.bridge .arrow{text-align:center;font-family:var(--mono);font-size:9px;color:var(--muted);padding-bottom:26px;line-height:1.45}
.bridge .arrow b{display:block;font-weight:700;color:var(--navy)}

.findings{border-left:3px solid var(--sky);padding-left:18px}
.findings ul{margin:0;padding:0}
.findings li{list-style:none;padding:9px 0;border-top:1px solid var(--rule);font-size:12px;line-height:1.6}
.findings li:first-child{border-top:none;padding-top:0}
.findings b{font-family:var(--display);font-weight:700;color:var(--navy);font-size:12.5px;display:block;margin-bottom:2px}
.findings em{font-family:var(--mono);font-style:normal;font-weight:600;color:var(--navy)}

.verdict-box{border:1px solid var(--navy)}
.verdict-box .row{display:grid;grid-template-columns:190px 1fr;border-bottom:1px solid var(--rule)}
.verdict-box .row:last-child{border-bottom:none}
.verdict-box .k{background:var(--sky-25);padding:9px 13px;font-family:var(--mono);font-size:9px;text-transform:uppercase;letter-spacing:.07em;color:var(--navy-85)}
.verdict-box .v{padding:9px 13px;font-size:12.5px;background:var(--paper)}
.verdict-box .v strong{font-family:var(--mono);font-weight:700;color:var(--navy)}

.doc-footer{border-top:1px solid var(--rule);padding:11px 44px 0;margin-top:26px;font-family:var(--mono);font-size:8.5px;color:var(--muted);display:flex;justify-content:space-between;gap:16px}

.hero{background:linear-gradient(155deg,#1c2947 0%,#243459 58%,#1c2947 100%);color:#fff;padding:30px 44px 34px;position:relative;overflow:hidden}
.hero::after{content:"";position:absolute;right:-90px;top:-90px;width:340px;height:340px;border-radius:50%;background:radial-gradient(circle,rgba(74,176,227,.30) 0%,rgba(74,176,227,0) 68%);pointer-events:none}
.hero .top{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;flex-wrap:wrap;margin-bottom:24px;position:relative}
.hero .logo{background:#fff;padding:8px 13px;border-radius:2px}
.hero .meta{text-align:right;font-family:var(--mono);font-size:9.5px;color:var(--sky-50);line-height:1.85;letter-spacing:.04em}
.hero .eyebrow{font-size:9.5px;font-weight:600;letter-spacing:.14em;color:var(--sky);margin-bottom:9px}
.hero h1{font-family:var(--display);font-size:38px;font-weight:800;letter-spacing:-.03em;line-height:1.02;color:#fff;margin:0 0 20px;max-width:16ch;position:relative}
.hero .verdict{display:inline-flex;align-items:center;gap:14px;padding:10px 18px;position:relative;background:rgba(74,176,227,.14);border:1px solid rgba(74,176,227,.45)}
.hero .verdict .word{font-family:var(--display);font-size:19px;font-weight:800;color:#8fd4f5;letter-spacing:.04em;line-height:1}
.hero .verdict .why{font-size:11.5px;color:#d8e3f0;max-width:46ch;line-height:1.5}
.hero .verdict.bad{background:rgba(200,68,63,.16);border-color:rgba(200,68,63,.5)}
.hero .verdict.bad .word{color:#ff8b84}
.hero .verdict.good{background:rgba(44,143,116,.18);border-color:rgba(44,143,116,.5)}
.hero .verdict.good .word{color:#7fe3c4}
.hero-figs{display:grid;grid-template-columns:repeat(4,1fr);gap:26px;margin-top:26px;position:relative}
.hero-fig .l{display:block;font-family:var(--mono);font-size:8.5px;text-transform:uppercase;letter-spacing:.1em;color:var(--sky-50);margin-bottom:7px}
.hero-fig .v{display:block;font-family:var(--mono);font-size:26px;font-weight:700;letter-spacing:-.025em;line-height:1;color:#fff}
.hero-fig .v.bad{color:#ff8b84} .hero-fig .v.good{color:#7fe3c4}
.hero-fig .f{display:block;font-size:10px;color:#9fb2c9;margin-top:5px}

.gapwrap{display:grid;grid-template-columns:1fr 220px;gap:34px;align-items:center}
.gapnum{font-family:var(--mono);font-size:44px;font-weight:800;color:var(--alert);letter-spacing:-.035em;line-height:1}
.gapnum.good{color:var(--ok)}
.gapnum small{display:block;font-family:var(--sans);font-size:11.5px;font-weight:600;color:var(--navy);letter-spacing:0;margin-top:8px;line-height:1.45}
.ladder{position:relative;padding:6px 0 26px}
.lrow{display:grid;grid-template-columns:104px 1fr 96px;align-items:center;gap:12px;margin-bottom:9px}
.lrow .n{font-size:11px;color:var(--navy-85)}
.lrow .n.key{font-weight:700;color:var(--navy)}
.ltrack{height:26px;background:var(--sky-25);position:relative}
.lfill{height:100%;display:block;background:var(--sky)}
.lfill.navy{background:var(--navy)} .lfill.bad{background:var(--alert)} .lfill.ok{background:var(--ok)}
.lrow .a{font-family:var(--mono);font-size:11.5px;text-align:right;font-variant-numeric:tabular-nums}
.lrow .a.bad{color:var(--alert);font-weight:700} .lrow .a.ok{color:var(--ok);font-weight:700}
.floorline{position:absolute;top:0;bottom:22px;border-left:2px dashed var(--alert)}
.floorline span{position:absolute;bottom:-20px;left:50%;transform:translateX(-50%);font-family:var(--mono);font-size:8.5px;color:var(--alert);white-space:nowrap;font-weight:600;letter-spacing:.05em}

.gauge{display:flex;align-items:center;gap:22px}
.gauge svg{flex:none}
.gauge .num{font-family:var(--mono);font-size:40px;font-weight:800;letter-spacing:-.035em;fill:var(--navy)}
.gauge .den{font-family:var(--mono);font-size:13px;fill:var(--navy-50)}
.gauge .cap{font-family:var(--mono);font-size:8.5px;letter-spacing:.11em;font-weight:600}
.legend{margin:0;padding:0;list-style:none;font-size:11.5px}
.legend li{display:flex;align-items:baseline;gap:9px;padding:5px 0;border-top:1px solid var(--rule)}
.legend li:first-child{border-top:none}
.legend .sw{width:10px;height:10px;flex:none;border-radius:2px}
.legend .nm{flex:1}
.legend .vl{font-family:var(--mono);font-variant-numeric:tabular-nums;font-weight:600;color:var(--navy)}

.toolbar{max-width:920px;margin:0 auto 18px;display:flex;justify-content:flex-end;gap:10px}
.toolbar button{font-family:var(--sans);font-size:12px;font-weight:600;color:#fff;background:var(--navy);border:none;padding:9px 20px;cursor:pointer;border-radius:2px}
.toolbar button:hover{background:#243459}
.toolbar button:focus-visible{outline:2px solid var(--sky);outline-offset:2px}

@media (max-width:700px){
  .hero-figs{grid-template-columns:repeat(2,1fr);gap:18px}.hero h1{font-size:29px}
  .gapwrap{grid-template-columns:1fr}.split{grid-template-columns:1fr;gap:20px}
  .kpis{grid-template-columns:repeat(2,1fr)}
  .pad,.masthead,.titleblock,.doc-footer{padding-left:20px;padding-right:20px}
  .bridge{grid-template-columns:1fr}.bridge .arrow{padding:8px 0}
}
@media print{
  body{background:#fff;padding:0}
  .doc{border:none;box-shadow:none;page-break-after:always}
  .page-tag,.toolbar{display:none}
}
"""


# --- verdict wording ----------------------------------------------------
#
# The word an underwriter acts on. Kept in one place so the hero, the
# recommendation table and the footer cannot describe the same case
# differently.

_VERDICT_WORD = {
    "decline": ("DECLINE", "bad"),
    "refer": ("REFER", "bad"),
    "proceed": ("PROCEED", "good"),
    "incomplete": ("INCOMPLETE", ""),
}


def _verdict(decision: dict) -> tuple:
    word, tone = _VERDICT_WORD.get(decision.get("verdict") or "", ("REVIEW", ""))
    return word, tone, decision.get("headline") or ""


def _meta_block(payload: dict, subtitle: str, today: date) -> str:
    case = payload["case"]
    return (f'<div class="meta">{subtitle}<br>Case #{case["id"]} &middot; New Business<br>'
            f'{today.strftime("%-d %B %Y")} &middot; Internal</div>')


def _masthead(payload: dict, subtitle: str) -> str:
    return (f'<div class="masthead"><div class="logo">{_logo_img(34)}</div>'
            f'<div class="meta">{subtitle}<br>{esc(payload["case"]["company_name"])}</div></div>')


def _footer(left: str, right: str) -> str:
    return f'<div class="doc-footer"><span>{left}</span><span>{right}</span></div>'


def _page(tag: str, body: str) -> str:
    # The tag is our own markup, entities and all - escaping it here
    # printed "&middot;" on the page rather than a separator.
    return f'<div class="paper"><div class="page-tag">{tag}</div><div class="doc">{body}</div></div>'


# --- page 1: the dashboard ----------------------------------------------


def _page_dashboard(payload: dict, today: date) -> str:
    case, decision = payload["case"], payload["decision"]
    experience = payload["experience"]
    word, tone, why = _verdict(decision)

    quoted = decision.get("quoted")
    expected = decision.get("expected_claims")
    technical = decision.get("recommended_minimum")
    break_even = decision.get("break_even")
    loss_ratio = experience.get("implied_loss_ratio_at_quote")
    members = case.get("member_count") or 0

    per_member = f"AED {aed(quoted / members)} per member" if quoted and members else ""
    lr_class = "bad" if (loss_ratio or 0) > 1.0 else "good" if loss_ratio else ""

    hero = f"""
    <div class="hero">
      <div class="top"><div class="logo">{_logo_img(34)}</div>
        {_meta_block(payload, "Underwriting Intelligence", today)}</div>
      <div class="eyebrow">{esc(case.get("industry") or "Industry not recorded")} &nbsp;&middot;&nbsp;
        {members} lives &nbsp;&middot;&nbsp; Broker: {esc(case.get("broker_name") or "not recorded")}</div>
      <h1>{esc(case["company_name"])}</h1>
      <div class="verdict {tone}"><span class="word">{word}</span><span class="why">{esc(why)}</span></div>
      <div class="hero-figs">
        <div class="hero-fig"><span class="l">Quoted premium</span><span class="v">{aed(quoted)}</span>
          <span class="f">{per_member}</span></div>
        <div class="hero-fig"><span class="l">Expected claims</span><span class="v">{aed(expected)}</span>
          <span class="f">blended &amp; trended</span></div>
        <div class="hero-fig"><span class="l">Loss ratio</span><span class="v {lr_class}">{pct(loss_ratio)}</span>
          <span class="f">at the quoted price</span></div>
        <div class="hero-fig"><span class="l">Technical price</span><span class="v good">{aed(technical)}</span>
          <span class="f">to land at {pct(experience.get("target_loss_ratio"), 0)}</span></div>
      </div>
    </div>"""

    return _page("Page 1 of 4 &middot; Executive Underwriting Dashboard", hero + f"""
    <div class="pad" style="padding-top:30px">
      {_section_gap(quoted, expected, break_even, technical, decision)}
      <section><div class="split">
        <div>{_scorecard_gauge(payload["scorecard"])}</div>
        <div>{_population_donut(payload.get("census"), members)}</div>
      </div></section>
      {_section_risk_drivers(payload["scorecard"])}
    </div>
    """ + _footer("Health Cross &middot; Underwriting Intelligence",
                  "Internal &mdash; contains risk pricing. Not for release to broker or client."))


def _section_gap(quoted, expected, break_even, technical, decision) -> str:
    """Four figures on one scale. The point of drawing them together is
    that a premium, the claims it has to pay and the price that would
    have funded them are the same kind of quantity, and a reader
    comparing them across three separate tables does the arithmetic in
    their head - usually wrongly.
    """
    figures = [f for f in (quoted, expected, break_even, technical) if f]
    if not figures:
        return ('<section><h2 class="sec">The gap</h2>'
                + _note("No price and no claims estimate on file yet. Compute the quote, and "
                        "upload the incumbent's claims report, and this section fills itself in.")
                + "</section>")
    top = max(figures)
    rows = [
        ("Quoted", quoted, "bad", True),
        ("Expected claims", expected, "", False),
        ("Break-even", break_even, "navy", False),
        ("Technical price", technical, "ok", True),
    ]
    ladder = "".join(
        f'<div class="lrow"><span class="n{" key" if key else ""}">{label}</span>'
        f'<span class="ltrack"><span class="lfill {fill}" style="width:{_width(value, top)}"></span></span>'
        f'<span class="a {"bad" if fill == "bad" else "ok" if fill == "ok" else ""}">{aed(value)}</span></div>'
        for label, value, fill, key in rows
    )
    floor = ""
    if break_even and top:
        share = min(1.0, break_even / top)
        floor = (f'<div class="floorline" style="left:calc(104px + 12px + (100% - 104px - 96px - 24px) '
                 f'* {share:.3f})"><span>BREAK-EVEN</span></div>')

    gap = ""
    if quoted and break_even:
        margin = quoted / break_even - 1
        shortfall = break_even - quoted
        if margin < 0:
            gap = (f'<div class="gapnum">{signed_pct(margin)}<small>below break-even.<br>'
                   f'AED {aed(shortfall)} short &mdash; that is<br>the year-one loss.</small></div>')
        else:
            gap = (f'<div class="gapnum good">{signed_pct(margin)}<small>above break-even.<br>'
                   f'AED {aed(-shortfall)} of margin<br>before the account turns.</small></div>')

    loading = decision.get("loading_pct")
    return f"""<section>
      <h2 class="sec">The gap</h2>
      <p class="desc">Every figure against the same lives. The dashed line is break-even &mdash; the
        premium at which claims are exactly funded after the {pct(loading)} loading.</p>
      <div class="gapwrap"><div class="ladder">{ladder}{floor}</div><div>{gap}</div></div>
    </section>"""


def _scorecard_gauge(scorecard: dict) -> str:
    scored = [r for r in scorecard["rows"] if r["score"] is not None]
    band = scorecard.get("overall_band")
    caption = {"high": "HIGH RISK", "medium": "MEDIUM RISK", "low": "LOW RISK"}.get(band or "", "NOT SCORED")
    legend = "".join(
        f'<li><span class="sw" style="background:{_band_colour(r["band"])}"></span>'
        f'<span class="nm">{esc(r["label"])}</span><span class="vl">{r["score"]:.0f}</span></li>'
        for r in sorted(scored, key=lambda r: r["score"])[:5]
    ) or '<li><span class="nm">Nothing measurable on file yet</span></li>'
    unscored = ""
    if scorecard.get("weight_unscored"):
        unscored = (f'<li style="border-top:1px solid var(--rule);color:var(--muted);font-size:10.5px">'
                    f'{pct(scorecard["weight_unscored"], 0)} of the weighting could not be measured '
                    f'and is left out of the total</li>')
    return f"""
      <h2 class="sec">Risk score</h2>
      <p class="desc">Seven weighted factors, higher is safer.</p>
      <div class="gauge">{gauge(scorecard.get("overall_score"), band, caption)}
        <ul class="legend" style="flex:1">{legend}{unscored}</ul></div>"""


def _band_colour(band: Optional[str]) -> str:
    return {"high": "#c8443f", "medium": "#c98a2b", "low": "#2c8f74"}.get(band or "", "#c6c9d1")


def _population_donut(census: Optional[dict], members: int) -> str:
    if not census or not census.get("relation_counts"):
        return ('<h2 class="sec">Who is on the scheme</h2>'
                + _note("No census on file. Upload one and the population mix appears here."))
    counts = census["relation_counts"]
    palette = ["#1c2947", "#4ab0e3", "#a4d7f1", "#d2ebf8", "#8e94a3"]
    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    segments = [{"value": n, "colour": palette[i % len(palette)]} for i, (_, n) in enumerate(ordered)]
    total = sum(counts.values()) or 1
    legend = "".join(
        f'<li><span class="sw" style="background:{palette[i % len(palette)]}"></span>'
        f'<span class="nm">{esc(name)}</span><span class="vl">{n} &middot; {n / total * 100:.0f}%</span></li>'
        for i, (name, n) in enumerate(ordered)
    )
    employees = counts.get("Employee") or 0
    ratio = ""
    if employees:
        spouses = counts.get("Spouse") or 0
        children = counts.get("Child") or 0
        ratio = (f'<li style="border-top:1px solid var(--rule);color:var(--muted);font-size:10.5px">'
                 f'{spouses / employees:.2f} spouses and {children / employees:.2f} children per employee</li>')
    return f"""
      <h2 class="sec">Who is on the scheme</h2>
      <p class="desc">{members} lives, by relation.</p>
      <div class="gauge">{donut(segments, str(total), "LIVES")}
        <ul class="legend" style="flex:1">{legend}{ratio}</ul></div>"""


#: How a factor's score reads as a word. An underwriter scanning the page
#: should not have to remember which end of 0-100 is bad.
def _impact(score: Optional[float]) -> str:
    if score is None:
        return "Not measured"
    if score < 25:
        return "Severe"
    if score < 40:
        return "High"
    if score < 70:
        return "Moderate"
    return "Low"


def _section_risk_drivers(scorecard: dict) -> str:
    scored = [r for r in scorecard["rows"] if r["score"] is not None]
    if not scored:
        return ('<section><h2 class="sec">Top risk drivers</h2>'
                + _note("Nothing on this case is measurable yet.") + "</section>")
    worst = sorted(scored, key=lambda r: r["score"])[:5]
    rows = "".join(
        f'<tr><td><span class="dot {_band_letter(r["band"])}"></span></td>'
        f'<td><strong>{esc(r["label"])}</strong></td><td>{esc(r["measure"])}</td>'
        f'<td class="num">{_impact(r["score"])}</td></tr>'
        for r in worst
    )
    return f"""<section>
      <h2 class="sec">Top risk drivers</h2>
      <p class="desc">The lowest-scoring factors, worst first &mdash; each with the figure it was
        measured from, so it can be argued with on its evidence.</p>
      <div class="scroll"><table class="data">
        <tr><th style="width:4%">&nbsp;</th><th style="width:22%">Driver</th><th>What it says</th>
            <th class="num" style="width:12%">Impact</th></tr>{rows}
      </table></div>
    </section>"""


# --- page 2: census & risk profile --------------------------------------


def _page_census(payload: dict) -> str:
    census = payload.get("census")
    body = _masthead(payload, "Census &amp; Risk Profile")
    if not census or not census.get("total_members"):
        body += ('<div class="pad" style="padding-top:26px"><section><h2 class="sec">Population</h2>'
                 + _note("No census has been uploaded for this case. Everything on this page is "
                         "derived from it.") + "</section></div>")
        return _page("Page 2 of 4 &middot; Census &amp; Risk Profile",
                     body + _footer("Census not on file", "Internal"))

    body += f"""<div class="pad" style="padding-top:26px">
      <section>
        <h2 class="sec">Population</h2>
        <div class="split">
          <div><h3 style="margin-top:0">Relation mix</h3>{_mix_bars(census["relation_counts"], census["total_members"])}</div>
          <div><h3 style="margin-top:0">Gender</h3>{_gender_bars(census)}</div>
        </div>
        <h3>Age distribution</h3>
        {_age_bars(census)}
      </section>
      {_section_maternity(census)}
      {_section_demographic_indicators(census)}
    </div>"""
    return _page("Page 2 of 4 &middot; Census &amp; Risk Profile",
                 body + _footer("Census as uploaded for this case", "Internal"))


def _mix_bars(counts: dict, total: int) -> str:
    if not counts:
        return _note("No relation recorded on the census.")
    top = max(counts.values()) or 1
    ordered = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return bar_rows([
        {"label": esc(name), "width": _width(n, top),
         "fill": "navy" if i == 0 else "",
         "value_text": f"{n} &middot; {n / total * 100:.0f}%" if total else str(n)}
        for i, (name, n) in enumerate(ordered)
    ], label_width=74, value_width=72)


def _gender_bars(census: dict) -> str:
    counts = {k: v for k, v in (census.get("gender_counts") or {}).items() if v}
    if not counts:
        return _note("No gender recorded on the census.")
    total = census["total_members"]
    top = max(counts.values()) or 1
    names = {"M": "Male", "F": "Female", "Other": "Not stated"}
    rows = bar_rows([
        {"label": names.get(k, esc(k)), "width": _width(v, top),
         "fill": "navy" if i == 0 else "",
         "value_text": f"{v} &middot; {v / total * 100:.0f}%" if total else str(v)}
        for i, (k, v) in enumerate(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))
    ], label_width=74, value_width=72)
    caption = ""
    if census.get("employee_count"):
        male = census.get("male_employees") or 0
        caption = (f'<div class="caption">Employees run {male} male to '
                   f'{census["employee_count"] - male} female</div>')
    return rows + caption


#: The band from which claims cost climbs steeply enough to be worth
#: calling out. Bands are named by their own lower bound, so this is
#: compared against that rather than against an age nobody banded on.
_OLDER_BAND_FROM = 41


def _band_floor(label: str) -> Optional[int]:
    head = (label or "").split("-")[0].strip()
    return int(head) if head.isdigit() else None


def _age_bars(census: dict) -> str:
    # Bands nobody is in are not information. The census summary emits
    # every band the rate card knows about, so on a young scheme half the
    # chart would otherwise be empty tracks reading zero.
    bands = {name: n for name, n in (census.get("age_band_counts") or {}).items() if n}
    if not bands:
        return _note("No ages recorded on the census.")
    top = max(bands.values()) or 1
    items = list(bands.items())
    half = (len(items) + 1) // 2

    def column(subset):
        return bar_rows([
            # Escape first, then insert the dash - escaping afterwards
            # printed the entity itself on the page.
            {"label": esc(name).replace("-", "&ndash;"), "width": _width(n, top),
             "fill": "navy" if (_band_floor(name) or 0) >= _OLDER_BAND_FROM else "",
             "value_text": str(n)}
            for name, n in subset
        ], label_width=68, value_width=30)

    total = census["total_members"] or 1
    older_bands = [name for name in bands if (_band_floor(name) or 0) >= _OLDER_BAND_FROM]
    older = sum(bands[name] for name in older_bands)
    share = older / total
    caption = (f'<div class="caption">{older} of {total} sit in the '
               f'{esc(", ".join(older_bands)).replace("-", "&ndash;") or "older"} band(s) &mdash; '
               f'where chronic disease starts to show '
               f'{tag("Watch", "a") if share > 0.4 else tag("Contained", "g")}</div>')
    return (f'<div class="split"><div>{column(items[:half])}</div>'
            f'<div>{column(items[half:])}</div></div>{caption}')


def _section_maternity(census: dict) -> str:
    """Maternity is the one exposure a census can size on its own, and
    the book says a spouse and an employee of the same age behave nothing
    alike - so they are counted separately rather than pooled into one
    "females of childbearing age" number that hides the difference.
    """
    at_risk = census.get("maternity_risk_count")
    if at_risk is None:
        return ""
    share = census.get("maternity_risk_pct")
    letter = "r" if (share or 0) > 0.25 else "a" if (share or 0) > 0.10 else "g"
    return f"""<section>
      <h2 class="sec">Maternity exposure</h2>
      <p class="desc">Females of maternity age, and how they sit on the scheme. A spouse and an
        employee of the same age do not deliver at the same rate, which is why they are counted apart.</p>
      <div class="scroll"><table class="data">
        <tr><th>Cohort</th><th class="num">Lives</th><th class="num">Share of scheme</th><th>Reading</th></tr>
        <tr><td>Maternity-age females</td><td class="num">{at_risk}</td>
            <td class="num">{pct(share)}</td><td>{tag(_maternity_word(share), letter)}</td></tr>
        <tr><td>Female spouses</td><td class="num">{census.get("female_spouse_count", DASH)}</td>
            <td class="num">{DASH}</td><td>Spouses deliver at several times the employee rate</td></tr>
        <tr><td>Married females</td><td class="num">{census.get("married_female_count", DASH)}</td>
            <td class="num">{pct(census.get("married_female_pct"))}</td><td>The rate card's own surcharge population</td></tr>
        <tr><td>Infants already on the scheme</td><td class="num">{census.get("infant_count", DASH)}</td>
            <td class="num">{DASH}</td><td>A newborn costs several times any later childhood year</td></tr>
      </table></div>
    </section>"""


def _maternity_word(share: Optional[float]) -> str:
    if share is None:
        return "Not measured"
    return "Heavy" if share > 0.25 else "Watch" if share > 0.10 else "Light"


def _section_demographic_indicators(census: dict) -> str:
    employees = census.get("employee_count") or 0
    relations = census.get("relation_counts") or {}
    spouses, children = relations.get("Spouse") or 0, relations.get("Child") or 0
    zones = census.get("nationality_zone_counts") or {}
    total = census["total_members"]
    top_zone = max(zones.items(), key=lambda kv: kv[1]) if zones else None

    rows = [
        ("a" if (census.get("avg_age") or 0) >= 40 else "g", "Average age, scheme",
         f'{census.get("avg_age"):.1f}' if census.get("avg_age") is not None else DASH,
         "Claims cost rises steeply with age from the late thirties"),
        ("g" if children / employees < 0.5 else "a" if employees else "n", "Children per employee",
         f"{children / employees:.2f}" if employees else DASH,
         "A child costs roughly half an employee"),
        ("g" if spouses / employees < 0.3 else "a" if employees else "n", "Spouses per employee",
         f"{spouses / employees:.2f}" if employees else DASH,
         "Adult dependants carry adult claims costs"),
        ("a" if (census.get("maternity_risk_pct") or 0) > 0.10 else "g", "Maternity-age females",
         f'{census.get("maternity_risk_count", DASH)}',
         f'{pct(census.get("maternity_risk_pct"))} of the scheme'),
    ]
    if top_zone:
        # The zone arrives as its storage key ("zone_2_middle_east"),
        # which is not a thing to print on a document someone reads.
        rows.append(("n", "Largest nationality zone",
                     esc(str(top_zone[0]).replace("_", " ").title()),
                     f"{top_zone[1] / total * 100:.0f}% of the scheme"))
    body = "".join(
        f'<tr><td><span class="dot {letter}"></span></td><td>{label}</td>'
        f'<td class="num">{value}</td><td>{note}</td></tr>'
        for letter, label, value, note in rows
    )
    return f"""<section>
      <h2 class="sec">Demographic risk indicators</h2>
      <div class="scroll"><table class="data">
        <tr><th style="width:4%">&nbsp;</th><th style="width:26%">Indicator</th>
            <th class="num" style="width:14%">This group</th><th>Reading</th></tr>{body}
      </table></div>
    </section>"""


# --- page 3: pricing & claims -------------------------------------------


def _page_pricing(payload: dict) -> str:
    body = _masthead(payload, "Pricing &amp; Claims") + f"""<div class="pad" style="padding-top:26px">
      {_section_bridge(payload)}
      {_section_premium_against_claims(payload)}
      {_section_claims_build(payload)}
      {_section_monthly_claims(payload)}
      {_section_sensitivity(payload)}
    </div>"""
    report = payload.get("claims_report") or {}
    source = ("Source: incumbent claims report "
              f'{esc(report.get("report_period_start") or "")} to {esc(report.get("report_period_end") or "")}'
              ) if report else "No incumbent claims report on file"
    return _page("Page 3 of 4 &middot; Pricing &amp; Claims", body + _footer(source, "Internal"))


def _section_bridge(payload: dict) -> str:
    bridge = payload["pricing_bridge"]
    card, technical, commercial = bridge["card_price"], bridge["technical_price"], bridge["commercial_price"]
    if not any((card, technical, commercial)):
        return ('<section><h2 class="sec">Pricing bridge</h2>'
                + _note("No price has been computed for this case yet.") + "</section>")
    top = max(v for v in (card, technical, commercial) if v)

    def col(caption, value, cls=""):
        height = max(3, round((value / top) * 100)) if value else 3
        return (f'<div class="col"><div class="bcap">{caption}</div>'
                f'<div class="bbar {cls}" style="height:{height}px"></div>'
                f'<div class="bval {cls if cls == "comm" else ""}">{aed(value)}</div></div>')

    def arrow(change, label):
        return f'<div class="arrow">&rarr;<b>{signed_pct(change)}</b>{label}</div>'

    return f"""<section>
      <h2 class="sec">Pricing bridge</h2>
      <p class="desc">The rate card prices on demographics alone. The technical price adds what this
        group's own claims actually say. The commercial price is what went out of the door.</p>
      <div class="bridge">
        {col("Rate card", card)}
        {arrow(bridge["card_to_technical_pct"], "own claims")}
        {col("Technical price", technical, "tech")}
        {arrow(bridge["technical_to_commercial_pct"], "discount")}
        {col("Commercial price", commercial, "comm")}
      </div>
      <div class="caption">Only the technical price reads the claims report. Where the card and the
        commercial price agree closely, it is because neither of them does.</div>
    </section>"""


def _section_premium_against_claims(payload: dict) -> str:
    decision = payload["decision"]
    quoted, expected = decision.get("quoted"), decision.get("expected_claims")
    loading = decision.get("loading_pct") or 0.0
    if not quoted or not expected:
        return ""
    funds = quoted * (1 - loading)
    top = max(quoted, expected)
    shortfall = expected - funds
    rows = bar_rows([
        {"label": "Quoted premium", "key": True, "width": _width(quoted, top),
         "fill": "navy", "value_text": aed(quoted)},
        {"label": "&mdash; funds claims", "width": _width(funds, top),
         "fill": "bad" if shortfall > 0 else "ok", "value_text": aed(funds),
         "value_class": "bad" if shortfall > 0 else "good"},
        {"label": "Expected claims", "key": True, "width": _width(expected, top),
         "fill": "soft", "value_text": aed(expected)},
    ])
    word = "Shortfall" if shortfall > 0 else "Surplus"
    return f"""<section>
      <h2 class="sec">Premium against claims</h2>
      <p class="desc">Only the part of a premium left after the {pct(loading)} loading can pay a claim.</p>
      {rows}
      <div class="caption">{word} AED {aed(abs(shortfall))} &middot; loss ratio
        {pct(payload["experience"].get("implied_loss_ratio_at_quote"))}</div>
    </section>"""


def _section_claims_build(payload: dict) -> str:
    """How the expected-claims figure was arrived at, line by line.

    A single number labelled "expected claims" is the one figure on the
    page most worth disagreeing with, and an underwriter cannot disagree
    with it unless they can see the steps.
    """
    experience = payload["experience"]
    detail = experience.get("experience")
    if not detail:
        return ('<section><h2 class="sec">How the claims figure was built</h2>'
                + _note("No incumbent claims report is on file, so the expected claims here are the "
                        "book's own estimate for members like these rather than this group's own "
                        "experience. Uploading the incumbent's report is the single biggest "
                        "improvement available to this case.") + "</section>")
    own = detail["own_experience"]
    blend = detail["blend"]
    rows = [
        ("Paid, per the report", own.get("paid"), f'{own.get("report_days")} days of experience'),
        ("Reported, not yet paid", own.get("reported_not_paid"), "already incurred, still in the pipeline"),
        ("Incurred, not yet reported", own.get("incurred_not_reported"), "the insurer's own IBNR"),
        ("Total incurred", own.get("incurred_claims"),
         f'over {own.get("member_years")} member-years', True),
        ("Their rate per member-year", own.get("claims_per_member_year"), "this group's own cost"),
        ("Our book's rate for these members", blend.get("book_rate"),
         "what members like these cost across the book"),
        ("Blended rate", blend.get("blended_rate"),
         f'credibility {pct(blend.get("credibility"))} on their own claims'),
        (f'&times; {detail.get("census_size")} lives &times; {1 + (detail.get("trend_pct") or 0):.2f} trend',
         detail.get("expected_claims"), "expected claims for the policy year", True),
    ]
    def build_row(row) -> str:
        emphatic = ' class="emphatic"' if len(row) > 3 else ""
        return (f'<tr{emphatic}><td>{row[0]}</td><td class="num">{aed(row[1])}</td>'
                f'<td>{esc(row[2])}</td></tr>')

    body = "".join(build_row(r) for r in rows)
    caveats = "".join(f"<li>{esc(c)}</li>" for c in (detail.get("caveats") or []))
    caveat_block = (f'<div class="findings" style="margin-top:14px"><ul>{caveats}</ul></div>'
                    if caveats else "")
    return f"""<section>
      <h2 class="sec">How the claims figure was built</h2>
      <p class="desc">{esc(blend.get("basis") or "")}</p>
      <div class="scroll"><table class="data">
        <tr><th>Step</th><th class="num" style="width:18%">AED</th><th style="width:40%">Note</th></tr>{body}
      </table></div>{caveat_block}
    </section>"""


def _section_monthly_claims(payload: dict) -> str:
    report = payload.get("claims_report") or {}
    monthly = report.get("monthly_paid") or []
    diagnoses = report.get("diagnosis_breakdown") or []
    if not monthly and not diagnoses:
        return ""
    # Full width rather than half. Squeezed into one column of a split
    # the month labels collided and the average line ran through its own
    # caption - a chart nobody can read is worse than the table it
    # replaced.
    chart = area_chart(monthly, width=1000, height=170) if len(monthly) > 1 else ""
    chart_block = (f'<h3 style="margin-top:0">Paid claims by month</h3>{chart}'
                   '<div class="caption">A rising line inside the experience period is a trend the '
                   'annualised total hides.</div>') if chart else ""
    diag_block = ""
    if diagnoses:
        top = max(d.get("value") or 0 for d in diagnoses) or 1
        diag_block = "<h3>Where the money went</h3>" + bar_rows([
            {"label": esc(d.get("label")), "width": _width(d.get("value"), top),
             "value_text": aed(d.get("value"))}
            for d in diagnoses[:8]
        ], label_width=170, value_width=80)
        share = payload.get("chronic_share_of_claims")
        if share is not None:
            diag_block += (f'<div class="caption">{pct(share)} of paid claims are chronic or metabolic. '
                           'Unlike an accident, a chronic condition transfers with the member and '
                           'claims from month one.</div>')
    return f"""<section>
      <h2 class="sec">Claims analysis</h2>
      {chart_block}
      {diag_block}
    </section>"""


def _section_sensitivity(payload: dict) -> str:
    rows = payload.get("sensitivity") or []
    if not rows:
        return ""
    absorbed = payload.get("stress_absorbed") or {}
    has_quote = any(r["loss_ratios"].get("quoted") is not None for r in rows)

    def verdict_tag(ratio):
        if ratio is None:
            return ""
        if ratio <= 0.9:
            return tag("On target", "g")
        if ratio <= 1.0:
            return tag("At break-even", "a")
        return tag("Loss", "r")

    def build_row(row) -> str:
        stress = row["stress_pct"]
        label = "As expected" if stress == 0 else f"+{stress * 100:.0f}%"
        ratios = row["loss_ratios"]
        # Each verdict sits beside the price it judges. One tag at the end
        # of the row read as a verdict on the last column it followed,
        # which put "Loss" next to a technical price landing on 85%.
        quoted_cells = (f'<td class="num">{pct(ratios.get("quoted"), 0)}</td>'
                        f'<td style="width:11%">{verdict_tag(ratios.get("quoted"))}</td>'
                        ) if has_quote else ""
        return (f'<tr><td>{label}</td><td class="num">{aed(row["expected_claims"])}</td>'
                f'{quoted_cells}<td class="num">{pct(ratios.get("technical"), 0)}</td>'
                f'<td style="width:11%">{verdict_tag(ratios.get("technical"))}</td></tr>')

    body = "".join(build_row(r) for r in rows)
    quoted_head = '<th class="num">LR at the quoted price</th><th>&nbsp;</th>' if has_quote else ""
    cushion = absorbed.get("technical")
    quoted_cushion = absorbed.get("quoted")
    lead = "The technical price absorbs "
    lead += (f"<strong>{signed_pct(cushion)}</strong> of claims inflation before it reaches break-even"
             if cushion is not None else "an unknown amount of claims inflation")
    if quoted_cushion is not None:
        lead += ("; the quoted price is already past break-even" if quoted_cushion < 0
                 else f"; the quoted price absorbs <strong>{signed_pct(quoted_cushion)}</strong>")
    return f"""<section>
      <h2 class="sec">Sensitivity</h2>
      <p class="desc">What happens if claims run above expectation. {lead}. A price that breaks even at
        +5% is not the same offer as one that holds to +20%, and nothing on a quote sheet says which
        it is.</p>
      <div class="scroll"><table class="data">
        <tr><th>Claims stress</th><th class="num">Expected claims</th>{quoted_head}
            <th class="num">LR at the technical price</th><th>&nbsp;</th></tr>{body}
      </table></div>
    </section>"""


# --- page 4: scorecard, benefits & decision -----------------------------


def _page_decision(payload: dict, today: date) -> str:
    body = _masthead(payload, "Scorecard &amp; Recommendation") + f"""<div class="pad" style="padding-top:26px">
      {_section_scorecard_table(payload["scorecard"])}
      {_section_benefits(payload.get("benefits"))}
      {_section_recommendation(payload, today)}
    </div>"""
    return _page("Page 4 of 4 &middot; Scorecard, Benefits &amp; Decision",
                 body + _footer(f'Prepared by Health Cross Underwriting Intelligence &middot; '
                                f'{today.strftime("%-d %B %Y")}', "Internal &mdash; not for release"))


def _section_scorecard_table(scorecard: dict) -> str:
    def row(label, weight, score, band, measure, emphatic=False):
        letter = _band_letter(band)
        bar = (f'<span class="dot {letter}"></span><span class="mini">'
               f'<i class="{letter}" style="width:{score:.0f}%"></i></span>') if score is not None else \
              '<span class="dot"></span><span class="mini"></span>'
        cls = ' class="emphatic"' if emphatic else ""
        return (f'<tr{cls}><td>{esc(label)}</td><td class="num">{pct(weight, 0)}</td>'
                f'<td class="num">{f"{score:.0f}" if score is not None else DASH}</td>'
                f'<td style="width:14%">{bar}</td><td>{esc(measure)}</td></tr>')

    body = "".join(
        row(r["label"], r["weight"], r["score"], r["band"], r["measure"])
        for r in scorecard["rows"]
    )
    overall = scorecard.get("overall_score")
    verdict = {"high": "High risk", "medium": "Medium risk", "low": "Low risk"}.get(
        scorecard.get("overall_band") or "", "Not enough measured to score")
    scored_note = ""
    if scorecard.get("weight_unscored"):
        scored_note = (f' &mdash; out of the {pct(scorecard["weight_scored"], 0)} of the weighting that '
                       f'could be measured')
    body += row("Overall", scorecard.get("weight_scored"), overall, scorecard.get("overall_band"),
                verdict + scored_note, emphatic=True)
    return f"""<section>
      <h2 class="sec">Risk scorecard</h2>
      <p class="desc">Higher is safer, on every line &mdash; one direction throughout, so a high number
        never means a good thing on one row and a bad thing on the next. A factor with no evidence
        behind it is left unscored rather than guessed at the middle, and its weight is shared out
        across the rest.</p>
      <div class="scroll"><table class="data">
        <tr><th style="width:20%">Factor</th><th class="num">Weight</th><th class="num">Score</th>
            <th>&nbsp;</th><th>Measured from</th></tr>{body}
      </table></div>
    </section>"""


#: How a comparison row reads at a glance. The direction the engine
#: produces is about the benefit moving up or down; what an underwriter
#: needs to know is whether that costs us money.
_DIRECTION_TAG = {
    "improved": ("Richer", "r"),
    "reduced": ("Leaner", "g"),
    "same": ("Match", "n"),
    "review": ("Check", "a"),
}


def _section_benefits(benefits: Optional[dict]) -> str:
    categories = (benefits or {}).get("categories") or []
    if not categories:
        return ('<section><h2 class="sec">Benefits against the incumbent</h2>'
                + _note("No incumbent table of benefits has been uploaded, so there is nothing to "
                        "compare the proposal against &mdash; a gap worth closing before binding.")
                + "</section>")
    blocks = []
    for category in categories:
        rows = category.get("rows") or []
        richer = sum(1 for r in rows if r.get("direction") == "improved")
        body = "".join(
            f'<tr><td><strong>{esc(r.get("label"))}</strong></td>'
            f'<td>{esc(r.get("existing")) or DASH}</td><td>{esc(r.get("proposed")) or DASH}</td>'
            f'<td style="width:12%">{tag(*_DIRECTION_TAG.get(r.get("direction") or "review", ("Check", "a")))}</td></tr>'
            for r in rows
        )
        heading = f'Category {esc(category.get("category"))}'
        if category.get("product") or category.get("network"):
            heading += (f' &middot; {esc(category.get("product") or "")} '
                        f'{esc(category.get("network") or "")}'.rstrip())
        caption = (f'<div class="caption">{richer} line(s) richer than the incumbent\'s. Buying up '
                   f'against the plan these claims were incurred on means the benefit will be used '
                   f'harder than the experience implies.</div>') if richer else ""
        blocks.append(f'<h3>{heading}</h3><div class="scroll"><table class="data">'
                      f'<tr><th style="width:22%">Benefit</th>'
                      f'<th>Incumbent{" &mdash; " + esc(category["existing_plan_name"]) if category.get("existing_plan_name") else ""}</th>'
                      f'<th>Health Cross proposal</th><th>&nbsp;</th></tr>{body}</table></div>{caption}')
    return f"""<section>
      <h2 class="sec">Benefits against the incumbent</h2>
      <p class="desc">Line by line, with the direction of each change. <strong>Richer</strong> is the
        one to look at: it is the only direction that costs money.</p>
      {"".join(blocks)}
    </section>"""


def _section_recommendation(payload: dict, today: date) -> str:
    decision = payload["decision"]
    scorecard = payload["scorecard"]
    word, _, why = _verdict(decision)
    quoted = decision.get("quoted")
    minimum = decision.get("recommended_minimum")
    break_even = decision.get("break_even")
    floor = decision.get("discount_authority_floor")

    stance = "Not priced yet"
    if quoted and break_even:
        margin = quoted / break_even - 1
        stance = ("<strong>Aggressive</strong> &mdash; " + signed_pct(margin) + " against break-even"
                  if margin < 0 else "<strong>Adequate</strong> &mdash; " + signed_pct(margin) +
                  " against break-even")

    authority = "No discount available &mdash; the quote is already below the technical price"
    if quoted and floor and quoted > floor:
        authority = (f"Down to <strong>AED {aed(floor)}</strong> without referral; below that, "
                     f"UW Manager")
    elif floor:
        authority = (f"<strong>0%</strong> &mdash; any price below <strong>AED {aed(minimum)}</strong> "
                     f"is a referral")

    conditions = _conditions(payload)
    rows = [
        ("Decision", f"<strong>{word}</strong> &mdash; {esc(why)}"),
        ("Risk rating", (f'<strong>{esc((scorecard.get("overall_band") or "not scored").title())}</strong> '
                         f'&middot; {aed(scorecard.get("overall_score"))}/100')),
        ("Pricing stance", stance),
        ("Recommended minimum", (f'<strong>AED {aed(break_even)}</strong> (break-even) &middot; target '
                                 f'<strong>AED {aed(minimum)}</strong> at '
                                 f'{pct(payload["experience"].get("target_loss_ratio"), 0)} loss ratio')),
        ("Discount authority", authority),
        ("Referral required", ("<strong>Yes</strong>" if decision.get("referral_required")
                               else "<strong>No</strong> &mdash; within standard authority")),
        ("Key conditions", conditions),
    ]
    body = "".join(f'<div class="row"><div class="k">{k}</div><div class="v">{v}</div></div>'
                   for k, v in rows)
    return f"""<section>
      <h2 class="sec">Final underwriting recommendation</h2>
      <div class="verdict-box">{body}</div>
      {_section_if_price_cannot_move(payload)}
    </section>"""


def _conditions(payload: dict) -> str:
    """Conditions earned by what the data actually shows, not a standard
    list. A condition nobody checked against this case is noise, and
    noise is what gets a real condition skipped.
    """
    items = []
    scorecard = {r["key"]: r for r in payload["scorecard"]["rows"]}

    design = scorecard.get("benefit_design") or {}
    if "pharmacy uncapped" in (design.get("measure") or ""):
        items.append("Cap the pharmacy benefit &mdash; it is the single cheapest control available here.")
    if "no outpatient deductible" in (design.get("measure") or ""):
        items.append("Add an outpatient deductible; frequency is what a deductible reaches.")

    chronic = scorecard.get("chronic_pre_existing") or {}
    if "covered from day one" in (chronic.get("measure") or ""):
        items.append("Consider a waiting period on pre-existing and chronic conditions &mdash; "
                     f'{pct(payload.get("chronic_share_of_claims"))} of paid claims are chronic.')

    if not (payload.get("claims_report")):
        items.append("Obtain the incumbent's claims report before binding &mdash; without it this is "
                     "priced on demographics alone.")
    if not ((payload.get("benefits") or {}).get("categories")):
        items.append("Obtain the incumbent's table of benefits, so the proposal can be compared "
                     "against what these claims were incurred on.")
    items.append("Confirm the take-up basis &mdash; a voluntary scheme is self-selected and claims "
                 "above a compulsory one.")
    return "<br>".join(f"{i}. {text}" for i, text in enumerate(items, start=1))


def _section_if_price_cannot_move(payload: dict) -> str:
    decision = payload["decision"]
    quoted, expected = decision.get("quoted"), decision.get("expected_claims")
    loading = decision.get("loading_pct") or 0.0
    if not quoted or not expected:
        return ""
    loss = expected - quoted * (1 - loading)
    if loss <= 0:
        return ""
    return f"""<div style="margin-top:22px"><h3>If the price cannot move</h3>
      <div class="findings"><ul>
        <li><b>Declining is the honest answer</b>
          At <em>AED {aed(quoted)}</em> this account is expected to lose <em>AED {aed(loss)}</em> in
          year one. On a book working to get under 100%, adding an account at
          {pct(payload["experience"].get("implied_loss_ratio_at_quote"))} moves it the wrong way.</li>
        <li><b>Or change the risk rather than the price</b>
          The conditions above each reduce expected claims. None is free, but they are levers the
          broker can be offered instead of a discount.</li>
      </ul></div></div>"""


# --- the whole document -------------------------------------------------

_HEAD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Mulish:wght@400;500;600;700;800&family=Montserrat:wght@600;700;800&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap">
<style>{css}</style></head><body>
<div class="toolbar"><button type="button" onclick="window.print()">Print / Save as PDF</button></div>
"""


def render_underwriting_report(payload: dict, today: Optional[date] = None) -> str:
    """The full internal document. `payload` is exactly what
    GET /cases/{id}/underwriting-report returns.
    """
    today = today or date.today()
    company = payload["case"]["company_name"]
    return (
        _HEAD.format(title=html.escape(f"{company} - Underwriting Dashboard"), css=STYLESHEET)
        + _page_dashboard(payload, today)
        + _page_census(payload)
        + _page_pricing(payload)
        + _page_decision(payload, today)
        + "</body></html>"
    )
