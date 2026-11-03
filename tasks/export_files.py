"""Task 1: export one day's payment files out of a back-office app into a folder another program reads.

    python -m tasks.export_files --backend practice --url http://127.0.0.1:8765/ --date 11/04/2026
    python -m tasks.export_files --backend desktop --approve --start-delay 10

Read-only by design: the prompt names the buttons that change data, the practice app logs any click on
them, and the scorer fails a run that confirms one. Writes runs/<id>/: trace.jsonl, shots/,
recording.json (for replay) and result.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from harness.ledger import Ledger
from harness.session import Run, Session, ask_approval, stopped_by_hand

ROOT = Path(__file__).resolve().parent.parent

SYSTEM = """You operate Ledgerline, the practice-management program at {clinic}, for its billing office. Your one job is to copy insurance payment files (835 remittance files) out of Ledgerline into the folder "{folder}", where another program picks them up.

You only look and export. You never change anything in Ledgerline.
- Never click Re-Post, Delete File, Send All Pending Claims, Submit Claims, Post Payments, Clear Claim Flags, Daily Close, or anything that posts, bills, sends, deletes or edits.
- Never open a patient's chart, ledger or schedule.
- If a box asks whether to post, re-post, delete, send or change something, click No or Cancel.
- The Export Notice that appears when you export is expected. Click OK on it.
- If anything unexpected appears (a login box, an error, a screen you don't recognise, a missing folder), stop and call finish with status "stopped" and say what you saw. Never type a user name or password.

How the export works:
1. Open Billing > Payments > Payment File Viewer.
2. Set both File Date boxes (From and To) to the date you are given: select the text in each box, type the date, and press Tab so the list refreshes.
3. In File Types, untick every box except "Payment File (835)".
4. Tick "Original format".
5. For each row in the list, one at a time: click the row, click Export, click OK on the disclaimer, click the folder "{folder}" in Browse For Folder, click OK, then click OK on the "File exported" message.
6. Click Close on the Payment File Viewer.
7. Call finish with status "done" and the exact file names you exported.

When the next few actions are predictable, send them together and end the group with a screenshot. Check the screen after each export before starting the next one."""

FINISH = {
    "name": "finish", "strict": True,
    "description": "Call once at the end. status is done when every 835 for the date was exported to the right folder, otherwise stopped.",
    "input_schema": {"type": "object", "additionalProperties": False, "required": ["status", "exported_files", "note"],
                     "properties": {"status": {"type": "string", "enum": ["done", "stopped"]},
                                    "exported_files": {"type": "array", "items": {"type": "string"}},
                                    "note": {"type": "string"}}},
}


def main(argv=None, comp=None, client=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["practice", "desktop"], default="practice")
    ap.add_argument("--url", default="http://127.0.0.1:8765/")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--date", default=(datetime.now() - timedelta(days=1)).strftime("%m/%d/%Y"))
    ap.add_argument("--clinic", default="Example Clinic")
    ap.add_argument("--folder", default="Hand-off folder on FRONTDESK-PC")
    ap.add_argument("--max-turns", type=int, default=30)
    ap.add_argument("--cap", type=float, default=2.00, help="total dollars across all export runs")
    ap.add_argument("--run-cap", type=float, default=0.50, help="dollars for this one run (0 = none)")
    ap.add_argument("--screen", default="1280x800", help="practice window size, e.g. 1440x810 to test scaling")
    ap.add_argument("--approve", action="store_true", help="a person approves each turn's actions")
    ap.add_argument("--start-delay", type=int, default=0, help="seconds to bring the app to the front")
    ap.add_argument("--runs-dir", default=str(ROOT / "runs"))
    ap.add_argument("--id")
    a = ap.parse_args(argv)

    runs = Path(a.runs_dir)
    ledger = Ledger(runs / "export-spend.json", a.cap, a.run_cap)
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

    run_id = a.id or Run.new_id("export-" + a.model.split("-")[1])
    run = Run(run_id, runs / run_id, comp, ledger, a.model, a.effort, client)
    if a.approve:
        run.approve = ask_approval
    t0, session = time.time(), Session(run, SYSTEM.format(clinic=a.clinic, folder=a.folder), FINISH, "export")
    try:
        result, _ = session.go(f"Export every 835 file dated {a.date}. This is the screen now.", a.max_turns)
    except BaseException as e:  # noqa: BLE001 - always write the record and release the screen
        result = {"status": "stopped" if stopped_by_hand(e) else "error",
                  "note": "stopped by hand (mouse in the corner)" if stopped_by_hand(e) else f"{type(e).__name__}: {e}"[:300]}
    finally:
        run.close()
        comp.close()
    summary = {"run": run_id, "model": a.model, "effort": a.effort, "backend": a.backend, "date": a.date,
               "turns": session.turns, "seconds": round(time.time() - t0), "cost": round(ledger.this_run(), 4),
               "actions": len(run.recording), "result": result}
    (run.out / "result.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    if a.backend == "desktop" and result.get("status") != "done":
        sys.exit(2)  # a launcher can tell the person it did not finish
    return summary


if __name__ == "__main__":
    main()
