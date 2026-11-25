#!/usr/bin/env python3
"""Step 1, COLLECT: get new items from every source and save them in the repo.

    python collect.py                 # every enabled source in config.json (what the CI job does)
    python collect.py --source ID     # one source
    python collect.py --dry-run       # fetch and print, write nothing
    python collect.py --demo          # made-up sample items, no network

For each source the collector:
  1. gets the items the source lists now,
  2. drops each item that is already in data/seen.json,
  3. drops each item that fails the source's `keywords` filter,
  4. drops each item older than `lookback_days`,
  5. saves the rest to data/items/<today>.json and records every item it saw in data/seen.json.

No model runs here. The same input gives the same output, every time.
Standard library only.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report import sources, store  # noqa: E402

DEMO_SOURCES = [
    {"id": "sample-a", "name": "Sample source A", "group": "Group one", "type": "sample", "count": 2},
    {"id": "sample-b", "name": "Sample source B", "group": "Group one", "type": "sample", "count": 1},
    {"id": "sample-c", "name": "Sample source C", "group": "Group two", "type": "sample", "count": 2},
]


def too_old(published, cutoff):
    if not published:
        return False  # an item with no date is kept
    try:
        return datetime.fromisoformat(published) < cutoff
    except ValueError:
        return False


def run_source(src, cfg, seen, cutoff, now):
    """Return (new items, counts) for one source. Updates `seen` in place."""
    candidates = sources.collect(src)
    cap = src.get("max_new", cfg.get("max_new_per_source", 10))
    counts = {"fetched": len(candidates), "new": 0, "seen": 0, "filtered": 0, "old": 0}
    kept = []
    for it in candidates:
        iid = store.item_id(src["id"], it)
        if iid in seen:
            counts["seen"] += 1
            continue
        if not sources.matches(it, src.get("keywords")):
            verdict = "filtered"
        elif too_old(it.get("published"), cutoff):
            verdict = "old"
        elif len(kept) >= cap:
            continue  # over today's cap: leave it unseen, the next run takes it
        else:
            verdict = "new"
        seen[iid] = {"source": src["id"], "title": it["title"], "first_seen": now, "verdict": verdict}
        counts[verdict] += 1
        if verdict == "new":
            kept.append({
                "id": iid,
                "source_id": src["id"],
                "source": src["name"],
                "group": src.get("group", "Sources"),
                "title": it["title"],
                "url": store.clean_url(it.get("url", "")),
                "published": it.get("published"),
                "summary": it.get("summary", ""),
                **({"details": it["details"]} if it.get("details") else {}),
                **({"extra": it["extra"]} if it.get("extra") else {}),
                "first_seen": now,
            })
    return kept, counts


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="run only this source id")
    ap.add_argument("--dry-run", action="store_true", help="fetch and print; write nothing")
    ap.add_argument("--demo", action="store_true", help="use made-up sample sources (no network)")
    args = ap.parse_args()

    cfg = store.load_config()
    configured = DEMO_SOURCES if args.demo else cfg.get("sources", [])
    todo = [s for s in configured if s.get("enabled", True) and (not args.source or s["id"] == args.source)]
    if not todo:
        print("No enabled sources. Add one to config.json, or try: python collect.py --demo", file=sys.stderr)
        return

    now = store.now_iso(cfg)
    day = store.today(cfg).isoformat()
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.get("lookback_days", 7))
    seen = store.load_seen()
    known = {s["id"] for s in configured}
    status = {k: v for k, v in store.load_json(store.STATUS, {}).items() if k in known}  # forget removed sources
    items = store.load_items_for_day(day)
    have = {it["id"] for it in items}
    fresh = []

    for src in todo:
        try:
            kept, counts = run_source(src, cfg, seen, cutoff, now)
            status[src["id"]] = {"name": src["name"], "ok": True, **counts, "error": None, "last_run": now}
            print(f"ok   {src['name']}: {counts['new']} new of {counts['fetched']}", file=sys.stderr)
        except Exception as e:  # one broken source must not stop the others
            kept = []
            error = sources.scrub(f"{type(e).__name__}: {e}")
            status[src["id"]] = {"name": src["name"], "ok": False, "fetched": 0, "new": 0, "error": error, "last_run": now}
            print(f"FAIL {src['name']}: {error}", file=sys.stderr)
        for it in kept:
            if it["id"] not in have:
                fresh.append(it)
                have.add(it["id"])

    if args.dry_run:
        print(json.dumps(fresh, indent=2, ensure_ascii=False))
        return

    if fresh:
        store.save_json(store.ITEMS / f"{day}.json", items + fresh)
    store.save_json(store.SEEN, seen)
    store.save_json(store.STATUS, status)
    failed = [s["name"] for s in status.values() if not s["ok"]]
    print(f"\n{len(fresh)} new item(s) for {day}. {len(failed)} source(s) failed{': ' + ', '.join(failed) if failed else ''}.", file=sys.stderr)


if __name__ == "__main__":
    main()
