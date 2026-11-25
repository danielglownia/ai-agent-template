"""Step 3, RENDER: turn frozen items plus the agent's summary into HTML. This is the "scripts build" part.

The agent never writes HTML. It writes reports/history/<date>.summary.json, and this module
puts that text into report/template.html. The layout is the same on every run.

A report page has, in this order:
  1. the verdict: the standing question, the headline and the overview,
  2. the agent's sections (summary["sections"]),
  3. the items, one card per source, each item with the agent's note,
  4. the sources that were checked.
"""

import html
from datetime import date, datetime

from . import store

TEMPLATE = (store.CODE / "report" / "template.html").read_text(encoding="utf-8")
NOTIFICATION_MAX = 160


def esc(text):
    return html.escape(str(text or ""), quote=True)


def plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def fmt_date_long(d):
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def fmt_date_short(iso, tzinfo):
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return (iso or "")[:10]
    if (dt.hour, dt.minute, dt.second) != (0, 0, 0):
        dt = dt.astimezone(tzinfo)  # a date with no time stays on its own day
    return f"{dt:%b} {dt.day}, {dt.year}"


# ----------------------------------------------------------------------------- data

def load_day(day):
    return {
        "date": day,
        "items": store.load_json(store.HISTORY / f"{day}.items.json", []),
        "summary": store.load_json(store.HISTORY / f"{day}.summary.json", None),
    }


def all_days():
    return [load_day(d) for d in store.history_days()]


def note_for(item, summary):
    note = ((summary or {}).get("items") or {}).get(item["id"])
    return note.strip() if isinstance(note, str) else ""


def is_skipped(item, summary):
    """The agent wrote "skip" for an item it judged off-topic: it stays in history but is not shown."""
    return note_for(item, summary).lower() == "skip"


def shown_items(rep):
    return [it for it in rep["items"] if not is_skipped(it, rep["summary"])]


def problems(rep):
    """Things the agent must fix in a summary file before the report is published."""
    summary, items = rep["summary"], rep["items"]
    if summary is None:
        return ["no summary file yet; the page shows each source's own description"] if items else []
    out = []
    ids = {it["id"] for it in items}
    notes = summary.get("items") or {}
    for field in ("headline", "notification", "overview"):
        if items and not str(summary.get(field) or "").strip():
            out.append(f'"{field}" is empty')
    if len(str(summary.get("notification") or "")) > NOTIFICATION_MAX:
        out.append(f'"notification" is longer than {NOTIFICATION_MAX} characters')
    missing = sorted(ids - set(notes))
    if missing:
        out.append(f"{plural(len(missing), 'item')} with no entry in \"items\": {', '.join(missing)}")
    unknown = sorted(set(notes) - ids)
    if unknown:
        out.append(f"\"items\" names {plural(len(unknown), 'id')} that this report does not hold: {', '.join(unknown)}")
    return out


# ----------------------------------------------------------------------------- html parts

def render_item(item, summary, tzinfo):
    title = esc(item["title"])
    link = ""
    if item.get("url"):
        title = f'<a href="{esc(item["url"])}" target="_blank" rel="noopener">{title}</a>'
        link = f'<a class="go" href="{esc(item["url"])}" target="_blank" rel="noopener">Read at source →</a>'
    meta = f'<div class="m num">{esc(fmt_date_short(item["published"], tzinfo))}</div>' if item.get("published") else ""
    note = note_for(item, summary)
    if note:
        body = f'<p class="s">{esc(note)}</p>'
    elif item.get("summary"):
        body = f'<p class="s raw">{esc(item["summary"][:600])}</p>'  # not judged yet: the source's own words
    else:
        body = ""
    return f'<li class="item"><div class="t">{title}</div>{meta}{body}{link}</li>'


def card_grid(cards):
    """Cards in two columns on a wide screen. An odd last card spans both columns."""
    if not cards:
        return ""
    last = len(cards) - 1 if len(cards) % 2 else -1
    out = [f'<article class="card{" wide" if i == last else ""}">{inner}</article>' for i, inner in enumerate(cards)]
    return f'<div class="cards">{"".join(out)}</div>'


def render_sections(summary):
    cards = []
    for sec in (summary or {}).get("sections") or []:
        if not sec.get("title"):
            continue
        text = f"<p>{esc(sec['text'])}</p>" if sec.get("text") else ""
        points = "".join(f"<li>{esc(p)}</li>" for p in sec.get("points") or [])
        points = f'<ul class="points">{points}</ul>' if points else ""
        cards.append(f"<h3>{esc(sec['title'])}</h3>{text}{points}")
    return card_grid(cards)


def render_groups(items, summary, cfg):
    """One block per group (in the order the sources appear in config.json), one card per source."""
    order = []
    for src in cfg.get("sources", []):
        if src.get("group", "Sources") not in order:
            order.append(src.get("group", "Sources"))
    by_group = {}
    for it in items:
        by_group.setdefault(it["group"], {}).setdefault(it["source"], []).append(it)
    tzinfo = store.tz(cfg)
    out = []
    for group in sorted(by_group, key=lambda g: (order.index(g) if g in order else len(order), g)):
        cards = []
        for source in sorted(by_group[group]):
            its = sorted(by_group[group][source], key=lambda i: (i.get("published") or "", i["title"]), reverse=True)
            rows = "".join(render_item(it, summary, tzinfo) for it in its)
            cards.append(
                f'<div class="card-head"><h3>{esc(source)}</h3><span class="n">{plural(len(its), "item")}</span></div>'
                f'<ul class="items">{rows}</ul>'
            )
        out.append(f'<div class="group"><div class="gname">{esc(group)}</div>{card_grid(cards)}</div>')
    return "".join(out)


def render_status(status):
    if not status:
        return ""
    rows, bad = [], 0
    for st in sorted(status.values(), key=lambda s: s["name"].lower()):
        if st.get("ok"):
            rows.append(f'<li><span>{esc(st["name"])}</span><span class="num">{st.get("fetched", 0)} seen · {st.get("new", 0)} new</span></li>')
        else:
            bad += 1
            rows.append(f'<li class="bad"><span>{esc(st["name"])}</span><span>failed</span></li>')
    failed = f", <strong>{bad} failed</strong>" if bad else ""
    return f'<details class="sources"><summary>Sources checked: {len(status) - bad} ok{failed}</summary><ul>{"".join(rows)}</ul></details>'


def render_day(rep, cfg, status=None, hidden=False):
    summary = rep["summary"] or {}
    items = shown_items(rep)
    n_sources = len({it["source"] for it in items})
    count = f"{plural(len(items), 'new item')} from {plural(n_sources, 'source')}" if items else "nothing new"
    question = f'<p class="question">{esc(cfg["question"])}</p>' if cfg.get("question") else ""
    if items:
        headline = summary.get("headline") or f"{plural(len(items), 'new item')} from {plural(n_sources, 'source')}."
        overview = f'<p class="overview">{esc(summary["overview"])}</p>' if summary.get("overview") else ""
        notice = "" if rep["summary"] else '<p class="notice">The agent has not written this report yet. The text under each item is the source\'s own description.</p>'
        verdict = f'<div class="verdict">{question}<div class="headline">{esc(headline)}</div>{overview}{notice}</div>'
    else:
        headline = summary.get("headline") or "Nothing new this time."
        overview = summary.get("overview") or "No new item passed the filters. Items from earlier reports are not shown again."
        verdict = f'<div class="verdict empty">{question}<div class="headline">{esc(headline)}</div><p class="overview">{esc(overview)}</p></div>'
    return (
        f'<section class="day" id="day-{rep["date"]}"{" hidden" if hidden else ""}>'
        f'<div class="daymeta"><div class="date">{esc(fmt_date_long(date.fromisoformat(rep["date"])))}</div><div class="count">{esc(count)}</div></div>'
        f"{verdict}{render_sections(summary)}{render_groups(items, summary, cfg)}{render_status(status)}</section>"
    )


# ----------------------------------------------------------------------------- pages

def page(title, body, script=""):
    return TEMPLATE.replace("<!--TITLE-->", esc(title)).replace("<!--BODY-->", body).replace("<!--SCRIPT-->", script)


def header_html(cfg, picker=""):
    sub = f'<div class="sub">{esc(cfg["tagline"])}</div>' if cfg.get("tagline") else ""
    return f'<header><div><div class="eyebrow">Automated report</div><h1>{esc(cfg.get("report_name", "Report"))}</h1>{sub}</div>{picker}</header>'


def footer_html(cfg):
    stamp = store.now_iso(cfg)[:16].replace("T", " ")
    return f"<footer>Generated {esc(stamp)}. Code collected the items and built this page. An LLM wrote the notes, from the collected items only.</footer>"


def render_report_page(rep, cfg, status=None):
    """One report as a standalone page (reports/<date>.html)."""
    return page(f"{cfg.get('report_name', 'Report')} {rep['date']}", header_html(cfg) + render_day(rep, cfg, status) + footer_html(cfg))


PICKER_SCRIPT = """<script>(function(){
var sel=document.getElementById('day');if(!sel)return;
function show(v){var m=document.getElementById('day-'+v);if(!m)return false;
document.querySelectorAll('.day').forEach(function(s){s.hidden=(s!==m);});sel.value=v;
document.querySelectorAll('.archive a').forEach(function(a){a.classList.toggle('cur',a.getAttribute('data-day')===v);});
return true;}
sel.addEventListener('change',function(){if(show(sel.value))window.scrollTo(0,0);});
document.querySelectorAll('.archive a').forEach(function(a){a.addEventListener('click',function(e){
e.preventDefault();if(show(a.getAttribute('data-day')))window.scrollTo(0,0);});});
})();</script>"""


def render_index(days, cfg, status=None):
    """The published page (site/index.html): the newest reports behind a date picker, plus an archive."""
    keep = cfg.get("index_reports", 30)
    recent = list(reversed(days[-keep:]))  # newest first
    options = "".join(
        f'<option value="{d["date"]}">{esc(fmt_date_long(date.fromisoformat(d["date"])))}{"" if shown_items(d) else " (nothing new)"}</option>'
        for d in recent
    )
    picker = f'<div class="picker"><label for="day">Report</label><select id="day">{options}</select></div>'
    body = header_html(cfg, picker)
    for i, d in enumerate(recent):
        body += render_day(d, cfg, status if i == 0 else None, hidden=(i != 0))
    embedded = {d["date"] for d in recent}
    cur = ' class="cur"'
    links = "".join(
        f'<li><a href="#day-{d["date"]}" data-day="{d["date"]}"{cur if i == 0 else ""}>{d["date"]}</a></li>'
        if d["date"] in embedded else
        f'<li><span class="gone" title="Older than the newest {keep} reports. Open reports/{d["date"]}.html in the repo.">{d["date"]}</span></li>'
        for i, d in enumerate(reversed(days))
    )
    body += f'<div class="archive"><div class="eyebrow">Archive · {plural(len(days), "report")}</div><ul>{links}</ul></div>'
    return page(cfg.get("report_name", "Report"), body + footer_html(cfg), PICKER_SCRIPT)
