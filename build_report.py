#!/usr/bin/env python3
"""Steps 2 and 3: freeze the new items for the agent, then RENDER the report.

    python build_report.py pending [--date YYYY-MM-DD]
        Freeze every collected item that no earlier report holds into
        reports/history/<date>.items.json, and print those items for the agent to read.
        Prints NOTHING_NEW when the day is empty. Safe to run again: late arrivals are added.

    python build_report.py render
        Build reports/<date>.html for every saved day, and site/index.html (the published page).
        Prints a warning for each problem in a summary file.

    python build_report.py status
        Show which sources worked on the last collector run.

Between `pending` and `render`, the agent writes reports/history/<date>.summary.json:

    {
      "headline": "one sentence that answers the standing question",
      "notification": "push text, 160 characters or fewer",
      "overview": "2 to 4 sentences: the thread through this run's items",
      "sections": [{"title": "...", "text": "...", "points": ["...", "..."]}],
      "items": {"<item id>": "1 to 3 sentences, or the word skip"}
    }

No model runs here. Standard library only.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from report import render, store  # noqa: E402

AGENT_FIELDS = ("id", "title", "url", "published", "summary", "details", "extra")


def rel(path):
    return Path(path).relative_to(store.HOME).as_posix()


def cmd_pending(date_arg):
    cfg = store.load_config()
    day = date.fromisoformat(date_arg).isoformat() if date_arg else store.today(cfg).isoformat()
    out_file = store.HISTORY / f"{day}.items.json"
    frozen = store.load_json(out_file, [])
    taken = store.reported_ids() | {it["id"] for it in frozen}
    for it in store.all_collected_items():
        if it["id"] not in taken and it.get("first_seen", "")[:10] <= day:
            frozen.append(it)
            taken.add(it["id"])
    frozen.sort(key=lambda i: (i["group"], i["source"], i.get("published") or "", i["title"]))
    store.save_json(out_file, frozen)

    if not frozen:
        print("NOTHING_NEW")
        print(f"Recorded an empty day: {rel(out_file)}", file=sys.stderr)
        return
    groups = {}
    for it in frozen:
        groups.setdefault(it["group"], {}).setdefault(it["source"], []).append({k: it[k] for k in AGENT_FIELDS if it.get(k)})
    summary_file = store.HISTORY / f"{day}.summary.json"
    print(json.dumps({
        "date": day,
        "question": cfg.get("question", ""),
        "reader": cfg.get("reader", ""),
        "count": len(frozen),
        "write_summary_to": rel(summary_file),
        "groups": groups,
    }, indent=2, ensure_ascii=False))
    print(f"\n{len(frozen)} item(s) frozen into {rel(out_file)}. Now write {rel(summary_file)}, then run: python build_report.py render", file=sys.stderr)


def cmd_render():
    cfg = store.load_config()
    days = render.all_days()
    if not days:
        sys.exit("No saved days yet. Run: python build_report.py pending")
    status = store.load_json(store.STATUS, {})
    store.REPORTS.mkdir(parents=True, exist_ok=True)
    store.SITE.mkdir(parents=True, exist_ok=True)
    for rep in days:
        page = render.render_report_page(rep, cfg, status if rep is days[-1] else None)
        (store.REPORTS / f"{rep['date']}.html").write_text(page, encoding="utf-8")
    (store.SITE / "index.html").write_text(render.render_index(days, cfg, status), encoding="utf-8")
    print(f"Rendered {len(days)} report(s): {rel(store.REPORTS)}/<date>.html and {rel(store.SITE)}/index.html")
    for rep in days:
        for problem in render.problems(rep):
            print(f"WARNING {rep['date']}: {problem}", file=sys.stderr)


def cmd_status():
    status = store.load_json(store.STATUS, {})
    if not status:
        print("No collector run recorded yet.")
        return
    for st in sorted(status.values(), key=lambda s: s["name"].lower()):
        detail = f"fetched={st.get('fetched', 0)} new={st.get('new', 0)}" if st["ok"] else st.get("error")
        print(f"{'ok  ' if st['ok'] else 'FAIL'} {st['name']:32s} {detail}  ({st.get('last_run', '')[:16]})")
    failed = [s["name"] for s in status.values() if not s["ok"]]
    print(f"\n{len(status) - len(failed)} ok, {len(failed)} failed{': ' + ', '.join(failed) if failed else ''}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pending").add_argument("--date", help="report date (YYYY-MM-DD); default: today in the config time zone")
    sub.add_parser("render")
    sub.add_parser("status")
    args = ap.parse_args()
    if args.cmd == "pending":
        cmd_pending(args.date)
    elif args.cmd == "render":
        cmd_render()
    else:
        cmd_status()


if __name__ == "__main__":
    main()
