#!/usr/bin/env python3
"""See the whole loop in one second. No network, no API key, no agent.

    python demo.py

The demo runs the three steps in a scratch folder (.demo/), so it does not touch your data:

  1. COLLECT  a script makes sample items        python collect.py --demo
  2. THINK    canned text stands in for the LLM  (a real agent writes this part)
  3. RENDER   a script builds the HTML report    python build_report.py render

Then open .demo/site/index.html in a browser.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOME = ROOT / ".demo"


def run(*args):
    env = {**os.environ, "REPORT_HOME": str(HOME)}
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env, check=True, capture_output=True, text=True).stdout


def stand_in_for_the_llm(pending):
    """Write the summary a real agent would write. The text says where each field goes."""
    ids = [it["id"] for sources in pending["groups"].values() for items in sources.values() for it in items]
    notes = {i: "The agent writes 1 to 3 sentences here: what the item is, and why it matters to the reader." for i in ids}
    notes[ids[-1]] = "skip"  # the agent judged this item off-topic, so the page does not show it
    return {
        "headline": "This is the headline. Here the agent answers your standing question in one sentence.",
        "notification": "This is the push text. The agent keeps it to 160 characters or fewer.",
        "overview": (
            "This is the overview. Here the agent gives the thread through the new items in 2 to 4 sentences. "
            f"In this demo a script wrote all the text. It also marked 1 of the {len(ids)} items as off-topic, so the page shows {len(ids) - 1}."
        ),
        "sections": [
            {
                "title": "Sections are optional",
                "text": "The agent adds a section when the items have a pattern that the reader must see.",
                "points": ["A section has a title.", "It can have text, points, or both."],
            },
            {
                "title": "You make the rules",
                "text": "PLAYBOOK.md tells the agent which sections to write and how to write them.",
            },
        ],
        "items": notes,
    }


def main():
    shutil.rmtree(HOME, ignore_errors=True)
    HOME.mkdir()

    run("collect.py", "--demo")
    print("1. COLLECT  sample items saved to .demo/data/")

    pending = json.loads(run("build_report.py", "pending"))
    Path(HOME / pending["write_summary_to"]).write_text(json.dumps(stand_in_for_the_llm(pending), indent=2) + "\n", encoding="utf-8")
    print(f"2. THINK    summary written for {pending['count']} items (canned text, no LLM)")

    run("build_report.py", "render")
    print("3. RENDER   report built\n")
    print(f"Open this file in a browser: {(HOME / 'site' / 'index.html').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
