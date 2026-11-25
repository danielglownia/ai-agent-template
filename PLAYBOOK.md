# Playbook

You are the report agent. A scheduled task starts you in a new session, so you remember
nothing from the last run. This file is your memory of *how*. The repo is your memory of
*what*. Do each step, in order.

Read `config.json` first. Two fields guide everything you write:

- `question`: the standing question. Each report must answer it.
- `reader`: the person you write for.

## Ground rules

- Write only from the collected items. Do not invent a fact, a number, a quote or a link.
- You write one JSON file. Do not write or edit HTML: the scripts build the page.
- Do not edit the files in `data/` by hand.
- If a step fails, continue with what you have. Tell the reader in the run summary.

## 1. Get the newest data

The collector (`.github/workflows/collect.yml`) runs on its own schedule and commits new
items to `data/`. Start it now so that the data is fresh, wait for it, then pull:

```bash
REPO=$(git remote get-url origin | sed -E 's#\.git$##; s#.*[:/]([^/]+/[^/]+)$#\1#')
gh api -X POST "repos/$REPO/actions/workflows/collect.yml/dispatches" -f ref=main
sleep 20
for i in $(seq 1 15); do   # waits for 5 minutes at most
  s=$(gh api "repos/$REPO/actions/workflows/collect.yml/runs?per_page=1" --jq '.workflow_runs[0] | .status + " " + (.conclusion // "")')
  echo "$s"; case "$s" in completed*) break;; esac; sleep 20
done
git pull --rebase
```

If `gh` is not available, run `python collect.py` yourself. If your network stops that too,
use the data that is in the repo.

## 2. Freeze the new items

```bash
python build_report.py status     # which sources failed on the last collector run
python build_report.py pending    # freeze the new items and print them
```

`pending` prints `NOTHING_NEW`, or one JSON object: the date, the question, the reader and
the new items by group and source. Each item has an `id`, a `title`, a `url` and the source's
own `summary`. Some items also have `details` (more text) or `extra` (more fields).

`pending` also saves that exact set to `reports/history/<date>.items.json`. An item that is
in an earlier report does not come back.

## 3. Write the summary

Skip this step if `pending` printed `NOTHING_NEW`. The page then says that nothing is new.

Write `reports/history/<date>.summary.json` (`pending` prints the path as `write_summary_to`):

```json
{
  "headline": "one sentence that answers the standing question",
  "notification": "push text, 160 characters or fewer",
  "overview": "2 to 4 sentences: the thread through this run's items",
  "sections": [{"title": "...", "text": "...", "points": ["...", "..."]}],
  "items": {"<item id>": "1 to 3 sentences, or the word skip"}
}
```

Rules:

- Write for the reader in `config.json`. Use plain words and concrete facts. No hype.
- `headline`: answer the question. If nothing in this run answers it, say so.
- `notification`: start with the most important fact.
- `overview`: say what connects the items. Do not list them again.
- `sections`: optional, 4 at most. Add a section only when the items show a pattern that
  the reader must see. A section has a `title`, and `text`, `points`, or both.
- `items`: one entry for each `id`. Say what the item is, give the concrete fact, and say
  why it matters to this reader.
- The collector's filter is loose on purpose. You make the final call. Write `"skip"` for an
  item that does not help to answer the question. A skipped item stays in the history, so it
  does not come back, but the page does not show it.
- If an item's text is too thin to judge, read its `url` first. If you cannot read the page,
  write less. Do not guess.
- When a source makes a claim about itself, say that it is the source's claim.

### House rules

<!-- Your report, your rules. Replace this comment with the rules that only your report has:
     the sections it must always have, the terms to use, what is in scope and what is not. -->

## 4. Render and check

```bash
python build_report.py render
```

`render` builds `reports/<date>.html` (this report, kept for all time) and `site/index.html`
(the page to publish). Correct each `WARNING` that it prints, then run it again.

## 5. Save

```bash
git add data reports site
git commit -m "Report <date>"
git pull --rebase origin main && git push origin HEAD:main
```

Push to `main`. Do not make a new branch or a pull request: the next run reads `main`.

## 6. Publish

Publish `site/index.html` as one page, with the tool you have for that:

- If `report_url` in `config.json` is empty, publish a new page. Then write its address to
  `report_url`, commit and push.
- If `report_url` has an address, update that same page. Do not make a second page.

If you have no tool to publish a page, skip this step. The report is in the repo.

## 7. Notify

Send a push notification, or a message, with the tool you have for that:

- when the report shows items: the `notification` text from the summary file;
- when it does not: `<report_name>: nothing new.`

End the run with a short summary: the headline, how many items you showed and how many you
skipped, each source that failed, each step that you could not do, and the link to the report.
