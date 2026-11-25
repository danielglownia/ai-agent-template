"""File layout and the dedupe index. This is the "Git remembers" part.

    config.json                      the question, the reader, the sources
    data/items/YYYY-MM-DD.json       items the collector kept on that day
    data/seen.json                   every item ever seen, so nothing is reported twice
    data/sources_status.json         result of the last collector run, per source
    reports/history/D.items.json     the exact items frozen into report D
    reports/history/D.summary.json   what the agent wrote for report D
    reports/D.html                   report D as a standalone page
    site/index.html                  the published page: recent reports plus an archive

Standard library only.
"""

import hashlib
import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

CODE = Path(__file__).resolve().parent.parent

# Query parameters that track a click and do not change the page.
_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid", "ref"}


def set_home(path):
    """Point the data, reports and site folders at `path`.

    The default is the repo itself. The demo and the tests use a scratch folder instead,
    so they never touch your real data.
    """
    global HOME, DATA, ITEMS, SEEN, STATUS, REPORTS, HISTORY, SITE
    HOME = Path(path)
    DATA = HOME / "data"
    ITEMS = DATA / "items"
    SEEN = DATA / "seen.json"
    STATUS = DATA / "sources_status.json"
    REPORTS = HOME / "reports"
    HISTORY = REPORTS / "history"
    SITE = HOME / "site"


set_home(os.environ.get("REPORT_HOME") or CODE)


# ----------------------------------------------------------------------------- config and time

def load_config():
    """Read config.json from the home folder, or from the repo when the home folder has none."""
    path = HOME / "config.json"
    return json.loads((path if path.exists() else CODE / "config.json").read_text(encoding="utf-8"))


def tz(cfg):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(cfg.get("timezone") or "UTC")
    except Exception:  # no time zone database on this machine
        return timezone.utc


def today(cfg):
    return datetime.now(tz(cfg)).date()


def now_iso(cfg):
    return datetime.now(tz(cfg)).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------- ids

def clean_url(url):
    """The URL as stored and linked: tracking parameters and the fragment removed."""
    p = urllib.parse.urlsplit((url or "").strip())
    query = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if k.lower() not in _TRACKING]
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(query), ""))


def canonical_url(url):
    """One spelling per page, so the same item at two addresses counts once."""
    p = urllib.parse.urlsplit(clean_url(url))
    host = p.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+", "/", p.path or "/").rstrip("/") or "/"
    query = urllib.parse.urlencode(sorted(urllib.parse.parse_qsl(p.query, keep_blank_values=True)))
    return urllib.parse.urlunsplit(("https", host, path, query, ""))


def title_key(title):
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()[:160]


def item_id(source_id, item):
    """A stable id: from the URL when there is one, otherwise from the source and the title."""
    basis = canonical_url(item["url"]) if item.get("url") else f"{source_id}|{title_key(item.get('title'))}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------------- files

def load_json(path, default):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_seen():
    return load_json(SEEN, {})


def load_items_for_day(day):
    return load_json(ITEMS / f"{day}.json", [])


def all_collected_items():
    """Every collected item, oldest day first."""
    items = []
    for f in sorted(ITEMS.glob("????-??-??.json")):
        items.extend(load_json(f, []))
    return items


def history_days():
    """Dates (oldest first) of every report that has a frozen items file."""
    return [f.name[:10] for f in sorted(HISTORY.glob("????-??-??.items.json"))]


def reported_ids():
    """Ids of every item that is already frozen into a report."""
    ids = set()
    for day in history_days():
        ids.update(it["id"] for it in load_json(HISTORY / f"{day}.items.json", []))
    return ids
