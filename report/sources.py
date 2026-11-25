"""Source drivers. This is the "scripts fetch" part.

A driver turns one `sources` entry from config.json into a list of items. An item is a dict:

    {"title": str, "url": str, "published": ISO 8601 or None, "summary": str}

plus, when the source has them, "details" (longer text for the agent to read) and
"extra" (any other fields you want the agent to see).

Three drivers come in the box:

    rss     an RSS or Atom feed                      {"type": "rss", "url": "..."}
    json    a JSON API, or a JSON file in the repo   {"type": "json", "url": "..."} or {"type": "json", "path": "..."}
    sample  made-up items, no network                {"type": "sample"}  (the demo uses this)

To add your own, write a function and register it:

    @driver("mytype")
    def mytype(src):
        return [{"title": "...", "url": "...", "published": None, "summary": "..."}]

Secrets: write "${NAME}" in a `url` or a `headers` value and the driver reads NAME from the
environment. Keep the real value in your CI secrets, never in config.json.

Standard library only.
"""

import gzip
import html
import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from . import store

USER_AGENT = "Mozilla/5.0 (compatible; report-collector/1.0)"
SUMMARY_CHARS = 1500
DETAILS_CHARS = 4000

DRIVERS = {}
_SECRETS = set()


def driver(name):
    def register(fn):
        DRIVERS[name] = fn
        return fn
    return register


def collect(src):
    """Run the driver for one source and return its items."""
    if src.get("type") not in DRIVERS:
        raise ValueError(f"unknown source type {src.get('type')!r}; known types: {', '.join(sorted(DRIVERS))}")
    return DRIVERS[src["type"]](src)


def matches(item, keywords):
    """True when the item names one of the keywords (no keywords: keep everything).

    Keep this filter loose. The agent makes the final call on what is relevant.
    """
    if not keywords:
        return True
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    return any(k.lower() in text for k in keywords)


# ----------------------------------------------------------------------------- secrets

def expand(value):
    """Replace ${NAME} with the environment variable NAME."""
    def sub(m):
        name = m.group(1)
        if name not in os.environ:
            raise KeyError(f"environment variable {name} is not set (add it as a CI secret)")
        _SECRETS.add(os.environ[name])
        return os.environ[name]
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", sub, value or "")


def scrub(text):
    """Remove secret values from a message before it is printed or saved."""
    for secret in _SECRETS:
        if secret:
            text = text.replace(secret, "***")
    return text


# ----------------------------------------------------------------------------- http and text

def fetch(url, headers=None, retries=2, timeout=25):
    """GET a URL and return the body as text. Raises on failure."""
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                return raw.decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if isinstance(e, urllib.error.HTTPError) and e.code not in (429, 500, 502, 503, 504):
                raise  # a client error will not get better on a retry
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise last


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        else:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def strip_html(text, limit=SUMMARY_CHARS):
    """HTML or plain text -> one line of plain text, cut at a word boundary."""
    if not text:
        return ""
    parser = _Text()
    parser.feed(str(text))
    out = re.sub(r"\s+", " ", html.unescape("".join(parser.parts))).strip()
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return out


def parse_date(text):
    """Best-effort date parsing -> ISO 8601 in UTC, or None."""
    text = (text or "").strip()
    if not text:
        return None
    dt = None
    for parse in (parsedate_to_datetime, lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))):
        try:
            dt = parse(text)
            break
        except (TypeError, ValueError):
            continue
    if dt is None:
        m = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if not m:
            return None
        dt = datetime.strptime(m.group(0), "%Y-%m-%d")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def dig(obj, path):
    """Follow a dotted path ("data.results", "author.name", "0.title") through dicts and lists."""
    for part in [p for p in (path or "").split(".") if p]:
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return None
    return obj


def _headers(src):
    return {k: expand(v) for k, v in (src.get("headers") or {}).items()}


# ----------------------------------------------------------------------------- driver: rss

def _local(tag):
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(el, names):
    for c in el:
        if _local(c.tag) in names and (c.text or "").strip():
            return c.text.strip()
    return ""


def parse_feed(text):
    """Parse RSS 2.0, RSS 1.0 or Atom into items."""
    text = text.lstrip("﻿ \n\r\t")
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        # Remove control characters and bare ampersands, then try once more.
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        cleaned = re.sub(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&amp;", cleaned)
        root = ET.fromstring(cleaned)

    items = []
    for entry in (e for e in root.iter() if _local(e.tag) in ("item", "entry")):
        title = strip_html(_child_text(entry, ("title",)), 400)
        link = ""
        for c in entry:
            if _local(c.tag) != "link":
                continue
            href = (c.get("href") or c.text or "").strip()
            if href and (c.get("rel") or "alternate") == "alternate":
                link = href
                break
            link = link or href
        if not link:
            guid = _child_text(entry, ("guid", "id"))
            link = guid if guid.startswith("http") else ""
        published = None
        for name in ("pubdate", "published", "updated", "date"):
            published = parse_date(_child_text(entry, (name,)))
            if published:
                break
        texts = [t for t in (_child_text(entry, (n,)) for n in ("description", "summary", "content", "encoded")) if t]
        summary = strip_html(texts[0]) if texts else ""
        if not (title and link):
            continue
        item = {"title": title, "url": link, "published": published, "summary": summary}
        longest = strip_html(max(texts, key=len), DETAILS_CHARS) if texts else ""
        if len(longest) > len(summary) + 200:
            item["details"] = longest  # the feed carries more than the summary shows
        items.append(item)
    return items


@driver("rss")
def rss(src):
    return parse_feed(fetch(expand(src["url"]), headers=_headers(src)))


# ----------------------------------------------------------------------------- driver: json

@driver("json")
def json_source(src):
    """A JSON API (`url`) or a JSON file in the repo (`path`).

    items_path    dotted path to the list of records ("" when the top level is the list)
    fields        where each item field lives in a record, as dotted paths
    extra_fields  more record fields to pass to the agent, as {"name": "dotted.path"}
    """
    if src.get("path"):
        data = json.loads((store.HOME / src["path"]).read_text(encoding="utf-8"))
    else:
        data = json.loads(fetch(expand(src["url"]), headers={"Accept": "application/json", **_headers(src)}))
    rows = dig(data, src.get("items_path"))
    if not isinstance(rows, list):
        raise ValueError(f"items_path {src.get('items_path', '')!r} does not point at a list")
    fields = {"title": "title", "url": "url", "published": "published", "summary": "summary", **(src.get("fields") or {})}
    items = []
    for row in rows:
        title = dig(row, fields["title"])
        if not title:
            continue
        item = {
            "title": strip_html(title, 400),
            "url": str(dig(row, fields["url"]) or ""),
            "published": parse_date(str(dig(row, fields["published"]) or "")),
            "summary": strip_html(dig(row, fields["summary"])),
        }
        extra = {name: dig(row, path) for name, path in (src.get("extra_fields") or {}).items()}
        extra = {k: v for k, v in extra.items() if v not in (None, "")}
        if extra:
            item["extra"] = extra
        items.append(item)
    return items


# ----------------------------------------------------------------------------- driver: sample

@driver("sample")
def sample(src):
    """Made-up items, so the whole pipeline runs with no network and no setup."""
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return [
        {
            "title": f"Sample item {n} from {src['name']}",
            "url": f"https://example.com/{src['id']}/item-{n}",
            "published": (now - timedelta(hours=6 * n)).isoformat(timespec="seconds"),
            "summary": "This is placeholder text. A real source puts its own description of the item here.",
        }
        for n in range(1, int(src.get("count", 2)) + 1)
    ]
