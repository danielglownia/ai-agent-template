"""Offline tests for the three steps: collect, freeze, render. No network, no model.

    python -m unittest discover tests
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_report  # noqa: E402
import collect  # noqa: E402
from report import render, sources, store  # noqa: E402

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>First &amp; best</title><link>https://example.com/a?utm_source=x</link>
<pubDate>Mon, 05 Jan 2026 10:00:00 GMT</pubDate><description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</description></item>
<item><title>No link</title></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>An entry</title><link rel="alternate" href="https://example.com/b"/>
<updated>2026-01-06T08:00:00Z</updated><summary>Short text</summary></entry></feed>"""

CFG = {"report_name": "Test report", "question": "What is new?", "timezone": "UTC", "sources": []}


def fresh(n, source="s1"):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {"title": f"Item {n}", "url": f"https://example.com/{source}/{n}", "published": now, "summary": f"About topic {n}"}


class Scratch(unittest.TestCase):
    """Each test gets its own empty home folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        store.set_home(self.tmp.name)
        (store.HOME / "config.json").write_text(json.dumps(CFG))

    def tearDown(self):
        store.set_home(ROOT)
        self.tmp.cleanup()


class SourceTests(Scratch):
    def test_rss(self):
        items = sources.parse_feed(RSS)
        self.assertEqual(len(items), 1)  # the entry with no link is dropped
        self.assertEqual(items[0]["title"], "First & best")
        self.assertEqual(items[0]["summary"], "Hello world")
        self.assertEqual(items[0]["published"], "2026-01-05T10:00:00+00:00")

    def test_atom(self):
        items = sources.parse_feed(ATOM)
        self.assertEqual((items[0]["title"], items[0]["url"]), ("An entry", "https://example.com/b"))

    def test_json_file(self):
        (store.HOME / "in.json").write_text(json.dumps({"data": {"rows": [
            {"name": "Row one", "link": "https://example.com/1", "when": "2026-01-05", "meta": {"score": 7}},
            {"link": "https://example.com/no-title"},
        ]}}))
        src = {"type": "json", "path": "in.json", "items_path": "data.rows",
               "fields": {"title": "name", "url": "link", "published": "when"}, "extra_fields": {"score": "meta.score"}}
        items = sources.collect(src)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["extra"], {"score": 7})
        self.assertTrue(items[0]["published"].startswith("2026-01-05"))

    def test_secrets_come_from_the_environment_and_stay_out_of_messages(self):
        os.environ["TEST_REPORT_KEY"] = "s3cret-value"
        try:
            self.assertEqual(sources.expand("Bearer ${TEST_REPORT_KEY}"), "Bearer s3cret-value")
            self.assertEqual(sources.scrub("failed for s3cret-value"), "failed for ***")
            with self.assertRaises(KeyError):
                sources.expand("${TEST_REPORT_KEY_THAT_IS_NOT_SET}")
        finally:
            del os.environ["TEST_REPORT_KEY"]

    def test_unknown_type(self):
        with self.assertRaises(ValueError):
            sources.collect({"type": "nope"})

    def test_feed_and_api_over_http(self):
        """A local web server stands in for the internet: one feed, and one API that wants a key."""
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/feed.xml":
                    body = RSS.encode()
                elif self.headers.get("Authorization") == "Bearer s3cret-value":
                    body = json.dumps({"results": [{"title": "From the API", "url": "https://example.com/api/1"}]}).encode()
                else:
                    self.send_error(401)
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with mock.patch.dict(os.environ, {"no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1", "TEST_REPORT_KEY": "s3cret-value"}):
                feed = sources.collect({"type": "rss", "url": f"{base}/feed.xml"})
                api = sources.collect({"type": "json", "url": f"{base}/api", "items_path": "results",
                                       "headers": {"Authorization": "Bearer ${TEST_REPORT_KEY}"}})
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual([i["title"] for i in feed + api], ["First & best", "From the API"])


class CollectTests(Scratch):
    def run_source(self, rows, seen, **src):
        sources.DRIVERS["fake"] = lambda _src: rows
        src = {"id": "s1", "name": "Source one", "group": "G", "type": "fake", **src}
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        return collect.run_source(src, {"max_new_per_source": 10}, seen, cutoff, "2026-01-05T00:00:00+00:00")

    def test_an_item_is_collected_once(self):
        seen = {}
        kept, counts = self.run_source([fresh(1), fresh(2)], seen)
        self.assertEqual((len(kept), counts["new"]), (2, 2))
        kept, counts = self.run_source([fresh(1), fresh(2), fresh(3)], seen)
        self.assertEqual(([k["title"] for k in kept], counts["seen"]), (["Item 3"], 2))

    def test_filter_and_age(self):
        old = {**fresh(9), "published": "2001-01-01T00:00:00+00:00"}
        kept, counts = self.run_source([fresh(1), fresh(2), old], {}, keywords=["topic 2", "topic 9"])
        self.assertEqual([k["title"] for k in kept], ["Item 2"])
        self.assertEqual((counts["filtered"], counts["old"]), (1, 1))

    def test_cap_leaves_the_rest_for_the_next_run(self):
        seen = {}
        kept, _ = self.run_source([fresh(n) for n in range(5)], seen, max_new=2)
        self.assertEqual(len(kept), 2)
        kept, _ = self.run_source([fresh(n) for n in range(5)], seen, max_new=2)
        self.assertEqual([k["title"] for k in kept], ["Item 2", "Item 3"])


def pending(day):
    """Run `build_report.py pending` without its printed output."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        build_report.cmd_pending(day)


class ReportTests(Scratch):
    def item(self, n, day):
        it = fresh(n)
        return {"id": f"id{n}", "source_id": "s1", "source": "Source one", "group": "G", **it, "first_seen": f"{day}T06:00:00+00:00"}

    def test_pending_does_not_repeat_an_item(self):
        store.save_json(store.ITEMS / "2026-01-05.json", [self.item(1, "2026-01-05")])
        pending("2026-01-05")
        store.save_json(store.ITEMS / "2026-01-06.json", [self.item(2, "2026-01-06")])
        pending("2026-01-06")
        pending("2026-01-06")  # safe to run again
        frozen = [[it["id"] for it in store.load_json(store.HISTORY / f"{d}.items.json", [])] for d in store.history_days()]
        self.assertEqual(frozen, [["id1"], ["id2"]])

    def test_render_uses_the_summary_and_hides_skipped_items(self):
        items = [self.item(1, "2026-01-05"), self.item(2, "2026-01-05")]
        store.save_json(store.HISTORY / "2026-01-05.items.json", items)
        store.save_json(store.HISTORY / "2026-01-05.summary.json", {
            "headline": "A <b>bold</b> answer", "notification": "Short", "overview": "Why it matters.",
            "sections": [{"title": "Pattern", "points": ["one", "two"]}],
            "items": {"id1": "The note for item one.", "id2": "skip"},
        })
        rep = render.all_days()[0]
        page = render.render_index([rep], CFG)
        self.assertIn("A &lt;b&gt;bold&lt;/b&gt; answer", page)  # the agent's text is escaped, never trusted as HTML
        self.assertIn("The note for item one.", page)
        self.assertIn("Pattern", page)
        self.assertNotIn("Item 2", page)
        self.assertEqual(render.problems(rep), [])

    def test_problems_are_reported(self):
        store.save_json(store.HISTORY / "2026-01-05.items.json", [self.item(1, "2026-01-05")])
        store.save_json(store.HISTORY / "2026-01-05.summary.json", {
            "headline": "Answer", "notification": "x" * 161, "overview": "Text", "items": {"other": "note"},
        })
        text = " | ".join(render.problems(render.all_days()[0]))
        for expected in ("longer than 160", "no entry", "does not hold"):
            self.assertIn(expected, text)

    def test_an_empty_day_still_renders(self):
        pending("2026-01-05")
        self.assertIn("Nothing new", render.render_index(render.all_days(), CFG))


class DemoTest(unittest.TestCase):
    def test_demo_runs_end_to_end(self):
        subprocess.run([sys.executable, "demo.py"], cwd=ROOT, check=True, capture_output=True)
        page = (ROOT / ".demo" / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn("This is the headline.", page)
        self.assertEqual(page.count('class="item"'), 4)  # 5 sample items, 1 skipped


if __name__ == "__main__":
    unittest.main()
