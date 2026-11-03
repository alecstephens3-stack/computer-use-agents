"""Task 2: work through a queue. Read a report of documents waiting for review, then mark each one reviewed.

    python -m tasks.review_queue --backend practice --url http://127.0.0.1:8765/

The pattern, which carries over to most "do this for every row" jobs in software with no API:

Stage 1. The model runs the report and reads every row. The code checks the reading before it is used:
         the header must match the settings asked for, every row must fit the date range, the row count
         must equal the report's own count of documents listed, and no ID may appear twice.
Stage 2. One short, fresh conversation per row. A long conversation that does twenty items gets slow and
         expensive, and a mistake in item 3 colours item 4. The code keeps the list, the budget and the
         record; the model only ever sees one item. Each report back is checked: the account in the
         window title, the document date, and whose name is on the review line.
Stage 3. The model runs the report again. The only rows left should be the ones it said it could not do.

Writes runs/<id>/: rows.json, results.json, summary.json, trace.jsonl, shots/, recording.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from harness.ledger import Ledger
from harness.session import Run, Session, ask_approval, stopped_by_hand

ROOT = Path(__file__).resolve().parent.parent

COMMON = """You work in Ledgerline, the practice-management program at Example Clinic, logged in as {user_name} (user ID {user}). {user_name} has asked you to work through the documents waiting for their review.

Look before you click. The program can be slow: after a click that opens or loads something, wait and look again rather than clicking twice. A screenshot or zoom ends your turn's actions, so put it last. Zoom gives a sharper close-up for reading small numbers.

Pop-up boxes: read every one before answering.
- "Mark Reviewed" asking to confirm the review: Yes.
- Any box that would discard, delete, post charges, bill, send, or change anything other than this review: choose the answer that changes nothing (No or Cancel). If it came before the review, do not review this document: report it as skipped with the box's words. If the review is already on the Reviewed By panel, the review stands: answer No, and mention the box in your note.
- Plain messages with only OK: OK. Patient Alert windows (yellow): read and close them.
Never use Delete, Edit, or close Ledgerline. If you see a login box or anything that is not Ledgerline, change nothing and report status error. Never type a user name or password."""

REPORT_TASK = COMMON + """

Your job now: run the Unreviewed Documents report and read it.
1. Open Reports > Documents > Unreviewed Documents.
2. Set the three boxes (select what is there and type over it): From Date {start}, To Date {end}, Assigned To {user}. Look before you click Begin: all three must be exactly right.
3. Click Begin. The report can take a while and windows may say "(Not Responding)". Wait for the Report Viewer; do not click anything while it runs.
4. Read every row on every page: Acct #, Doc Date, Doc #. Use the page arrows at the top. Zoom in to read the numbers exactly. Read the header line (From, To, Assigned To) and the "Documents listed" line on the last page ("Pages" in the toolbar is not the row count).
5. Close the Report Viewer and the report box (the x at their top right), look, then call record_report on its own."""

ITEM_TASK = COMMON + """

Your job now: one document from the list.
1. Open Patient Search: the Search button on the main toolbar, or on the Documents window's toolbar if one is open.
2. Type the account number in Acct # (select what is there first) and click the arrow button at the left of that row. Do this in one turn, then look.
3. One match opens directly. If several family members share the account, a list appears: double-click the person whose Last Visit is the document date, or the other person if you were told one is already done.
4. In the Documents window, read the title: [ACCT:n] must be this account. Find the row whose Doc # is the one you were given and double-click it, so the right side shows that document. Check its date and Doc # in the header.
5. Look at the Reviewed By panel. If it already shows {user} with a date, do not review it again: report already_reviewed.
6. Click Mark Reviewed and answer the boxes (see Pop-up boxes above). Then check the Reviewed By panel shows {user}, {user_name}, with today's date.
7. Call item_done with what you see on screen now. Leave the Documents window open for the next item."""

RECORD_REPORT = {
    "name": "record_report", "strict": True,
    "description": "Record every row of the Unreviewed Documents report and its Documents listed count.",
    "input_schema": {"type": "object", "additionalProperties": False,
                     "required": ["header_start", "header_end", "header_user", "rows", "grand_total", "note"],
                     "properties": {
                         "header_start": {"type": "string"}, "header_end": {"type": "string"}, "header_user": {"type": "string"},
                         "rows": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                              "required": ["acct", "date", "doc_id"],
                                                              "properties": {"acct": {"type": "string"}, "date": {"type": "string"},
                                                                             "doc_id": {"type": "string"}}}},
                         "grand_total": {"type": "integer"}, "note": {"type": "string"}}},
}
ITEM_DONE = {
    "name": "item_done", "strict": True,
    "description": "Call once when this document is finished or cannot be finished.",
    "input_schema": {"type": "object", "additionalProperties": False,
                     "required": ["status", "patient_name", "title_acct", "doc_id", "doc_date", "reviewed_line", "boxes", "note"],
                     "properties": {
                         "status": {"type": "string", "enum": ["reviewed", "already_reviewed", "skipped", "not_found", "error"]},
                         "patient_name": {"type": "string"},
                         "title_acct": {"type": "string", "description": "the ACCT number in the Documents window title"},
                         "doc_id": {"type": "string", "description": "the Doc # in the header of the document on screen"},
                         "doc_date": {"type": "string", "description": "the date in the header of the document on screen"},
                         "reviewed_line": {"type": "string", "description": "the Reviewed By panel now, word for word, or 'none'"},
                         "boxes": {"type": "array", "description": "every pop-up box you answered",
                                   "items": {"type": "object", "additionalProperties": False, "required": ["title", "answer"],
                                             "properties": {"title": {"type": "string"}, "answer": {"type": "string"}}}},
                         "note": {"type": "string", "description": "one plain sentence; for skipped, what the box said"}}},
}


def norm(x) -> str:
    return "".join(ch for ch in str(x).upper() if ch.isalnum())


def ymd(x: str) -> tuple[int, int, int]:
    m, d, y = [int(v) for v in str(x).strip().split("/")]
    return (y + 2000 if y < 100 else y, m, d)


def check_report(inp: dict, start: str, end: str, user: str) -> str | None:
    """Refuse a report reading that cannot be right. Returns the reason, or None to accept it."""
    def same_date(a, b):
        try:
            return ymd(a) == ymd(b)
        except (ValueError, TypeError):
            return False
    bad = [f"{k} is {inp[k]!r}, should be {v!r}" for k, v in (("header_start", start), ("header_end", end))
           if not same_date(inp[k], v)]
    if norm(inp["header_user"]) != norm(user):
        bad.append(f"header_user is {inp['header_user']!r}, should be {user!r}")
    if bad:
        return ("The report was run with the wrong settings (" + "; ".join(bad) + "). Close the Report Viewer, run it again "
                "with the right From Date, To Date and Assigned To, and record that one.")
    rows = inp["rows"]

    def fits(x):
        try:
            return ymd(start) <= ymd(x["date"]) <= ymd(end)
        except (ValueError, TypeError):
            return False
    off = [x for x in rows if not fits(x)]
    if off:
        return f"These rows fall outside the report's dates, so they were misread: {off[:3]}. Zoom in and re-read them."
    if len(rows) != inp["grand_total"]:
        return (f"You recorded {len(rows)} rows but the report says {inp['grand_total']} documents listed. Go back through every page, "
                "zoom in, and record every row exactly once.")
    ids = [x["doc_id"] for x in rows]
    if len(set(ids)) != len(ids):
        return "Some Doc # values appear twice in your list. Each row has its own Doc #; re-read and fix the list."
    return None


def check_item(inp: dict, row: dict, user: str, today: str) -> str | None:
    """Refuse an item report that describes the wrong document or a review that is not on screen."""
    st = inp["status"]
    if st in ("not_found", "error"):
        return None
    if norm(inp["title_acct"]) != norm(row["acct"]):
        return (f"The Documents window title shows ACCT {inp['title_acct']}, but this task is account {row['acct']}. "
                "You are on the wrong patient: search again for the right account and finish there.")
    if norm(inp["doc_id"]) != norm(row["doc_id"]):
        return (f"The document on screen is Doc {inp['doc_id']}, but this task is Doc {row['doc_id']}. "
                "Double-click the right row in the list and finish there.")
    try:
        same_day = ymd(inp["doc_date"]) == ymd(row["date"])
    except (ValueError, TypeError):
        same_day = False
    if not same_day:
        return f"The document date you read is {inp['doc_date']}, but the report says {row['date']}. Zoom in and re-read it."
    line = norm(inp["reviewed_line"])
    mine = norm(user) in line
    short = today[:6] + today[8:]
    today_on_line = norm(short) in line or norm(today) in line
    if st in ("reviewed", "already_reviewed") and not mine:
        return (f"The Reviewed By line you read ({inp['reviewed_line']!r}) is not {user}'s. If {user} is not on the panel, "
                "the document has not been reviewed.")
    if st == "reviewed" and not today_on_line:
        return (f"You reported reviewed, but the Reviewed By line ({inp['reviewed_line']!r}) has no {short} date. "
                "If the review is not on the panel, it did not happen.")
    if st == "skipped" and mine and today_on_line:
        return ("The Reviewed By panel shows today's review, so this document is reviewed and the review stands. "
                "Close any open box with No and report reviewed, with the box in your note.")
    return None


def read_report(run: Run, a) -> tuple[dict, float]:
    task = REPORT_TASK.format(user=a.user, user_name=a.user_name, start=a.start, end=a.end)
    return Session(run, task, RECORD_REPORT, "report").go(
        "Run the Unreviewed Documents report and record every row.", a.report_turns,
        lambda inp: check_report(inp, a.start, a.end, a.user))


def do_item(run: Run, a, row: dict, ctx: str) -> dict:
    t0 = time.time()
    label = f"doc{row['doc_id']}"
    text = (f"Account {row['acct']}, document {row['doc_id']}, dated {row['date']}. Today is {a.today}.{ctx} "
            "The current screen is attached: act on it.")
    task = ITEM_TASK.format(user=a.user, user_name=a.user_name)
    out, c = Session(run, task, ITEM_DONE, label).go(text, a.item_turns,
                                                     lambda inp: check_item(inp, row, a.user, a.today))
    run.shot(label + "-end")  # the proof picture for this row
    out.update({"acct": row["acct"], "date": row["date"], "doc": row["doc_id"], "cost": round(c, 4),
                "seconds": round(time.time() - t0), "proof": f"shots/{run.last_shot}"})
    return out


def main(argv=None, comp=None, client=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["practice", "desktop"], default="practice")
    ap.add_argument("--url", default="http://127.0.0.1:8765/")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--start", default="11/02/2026")
    ap.add_argument("--end", default="11/06/2026")
    ap.add_argument("--today", default="11/07/2026")
    ap.add_argument("--user", default="JDOE")
    ap.add_argument("--user-name", default="Doe, Jordan")
    ap.add_argument("--limit", type=int, default=0, help="only the first N rows (first supervised runs)")
    ap.add_argument("--report-turns", type=int, default=30)
    ap.add_argument("--item-turns", type=int, default=30)
    ap.add_argument("--cap", type=float, default=10.0, help="total dollars across all queue runs")
    ap.add_argument("--run-cap", type=float, default=3.0, help="dollars for this one run (0 = none)")
    ap.add_argument("--no-verify", action="store_true", help="skip the closing re-run of the report")
    ap.add_argument("--screen", default="1280x800", help="practice window size, e.g. 1440x810 to test scaling")
    ap.add_argument("--approve", action="store_true", help="a person approves each turn's actions")
    ap.add_argument("--start-delay", type=int, default=0, help="seconds to bring the app to the front")
    ap.add_argument("--runs-dir", default=str(ROOT / "runs"))
    ap.add_argument("--id")
    a = ap.parse_args(argv)

    runs = Path(a.runs_dir)
    ledger = Ledger(runs / "queue-spend.json", a.cap, a.run_cap)
    if not ledger.can_start():
        sys.exit(f"Spend cap reached: ${ledger.total():.2f} of ${a.cap:.2f}")
    if comp is None:
        if a.backend == "practice":
            from harness.computer import PracticeComputer
            w, h = (int(v) for v in a.screen.split("x"))
            comp = PracticeComputer(a.url, w, h)
        else:
            from harness.computer import DesktopComputer
            if a.start_delay:
                print(f"Click the Ledgerline window now. The agent starts in {a.start_delay} seconds.\n"
                      "To stop it at any time, push the mouse into a corner of the screen.", flush=True)
                time.sleep(a.start_delay)
            comp = DesktopComputer()
    run_id = a.id or Run.new_id("queue-" + a.model.split("-")[1])
    run = Run(run_id, runs / run_id, comp, ledger, a.model, a.effort, client)
    if a.approve:
        run.approve = ask_approval
    t0 = time.time()
    summary = {"run": run_id, "model": a.model, "effort": a.effort, "start": a.start, "end": a.end, "today": a.today}
    rows, results = [], []
    try:
        rep, c = read_report(run, a)                                      # stage 1
        summary["report"] = {"cost": round(c, 4), **{k: v for k, v in rep.items() if k != "rows"}}
        if "rows" not in rep:
            summary["stopped"] = f"report: {rep.get('note')}"
        else:
            rows = rep["rows"]
        (run.out / "rows.json").write_text(json.dumps(rows, indent=1))
        todo = rows[:a.limit] if a.limit else rows
        for i, row in enumerate(todo):                                    # stage 2
            if summary.get("stopped"):
                break
            same = [x for x in rows if x["acct"] == row["acct"]]
            done_here = [x for x in results if x["acct"] == row["acct"] and x.get("patient_name")]
            ctx = ""
            if len(same) > 1:
                ctx = f" This account has {len(same)} rows on the report (different people or documents)."
                if done_here:
                    ctx += " Already done on this account: " + "; ".join(
                        f"{x['patient_name']} ({x['date']}, {x['status']})" for x in done_here) + "."
            print(f"[{i + 1}/{len(todo)}] acct {row['acct']} doc {row['doc_id']} {row['date']}", flush=True)
            res = do_item(run, a, row, ctx)
            results.append(res)
            (run.out / "results.json").write_text(json.dumps(results, indent=1))
            print(f"   -> {res['status']}: {res.get('patient_name', '')} {res.get('note', '')[:100]}", flush=True)
            if len(results) >= 3 and all(x["status"] in ("error", "not_found") for x in results[-3:]):
                summary["stopped"] = "three items in a row could not be done; stopped so a person can look"
            elif ledger.over():
                summary["stopped"] = "spend cap"
        if not a.no_verify and not a.limit and results and not summary.get("stopped"):   # stage 3
            left = {x["doc"] for x in results if x["status"] not in ("reviewed", "already_reviewed")}
            rep2, _ = read_report(run, a)
            if "rows" not in rep2:
                summary["verify"] = {"ok": False, "note": "the closing re-check did not finish"}
            else:
                still = {x["doc_id"] for x in rep2["rows"]}
                summary["verify"] = {"ok": still == left, "still_listed": sorted(still), "expected_left": sorted(left)}
            print("verify:", json.dumps(summary["verify"]), flush=True)
    except BaseException as e:  # noqa: BLE001 - keep the record of what was done, whatever stopped the run
        summary["stopped"] = ("stopped by hand (mouse in the corner)" if stopped_by_hand(e)
                              else f"stopped by an error: {type(e).__name__}: {e}"[:300])
        print(summary["stopped"], flush=True)
    finally:
        summary.update({"rows": len(rows), "results": results, "seconds": round(time.time() - t0),
                        "cost": round(ledger.this_run(), 4)})
        (run.out / "summary.json").write_text(json.dumps(summary, indent=1))
        run.close()
        comp.close()
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=1))
    return summary


if __name__ == "__main__":
    sys.exit(1 if main().get("stopped") else 0)
