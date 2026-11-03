"""The code-side checks the tasks apply to what the model reports, and how a task ends when stopped."""
import json

import pytest

from conftest import FakeClient, FakeComputer, tool
from tasks import export_files, review_queue
from tasks.review_queue import check_item, check_report

ROW = {"acct": "40211", "date": "11/03/2026", "doc_id": "7019"}
GOOD = {"status": "reviewed", "patient_name": "Alvarez, Luis", "title_acct": "40211", "doc_id": "7019",
        "doc_date": "11/03/2026", "reviewed_line": "JDOE Doe, Jordan 11/07/26 09:50", "boxes": [], "note": ""}


def rep(rows, total=None, **k):
    return {"header_start": "11/02/2026", "header_end": "11/06/2026", "header_user": "JDOE", "rows": rows,
            "grand_total": len(rows) if total is None else total, "note": "", **k}


R1 = {"acct": "1", "date": "11/03/2026", "doc_id": "5"}
R2 = {"acct": "2", "date": "11/04/2026", "doc_id": "6"}


@pytest.mark.parametrize("inp,needle", [
    (rep([R1, R2]), None),
    (rep([R1, R2], header_user="KLEE"), "wrong settings"),
    (rep([R1, R2], total=3), "says 3 documents listed"),
    (rep([R1, R2], header_start="11/2/2026"), None),
    (rep([R1, R2], header_start="11/03/2026"), "wrong settings"),
    (rep([R1, dict(R2, date="11/14/2026")]), "misread"),
    (rep([R1, dict(R2, date="11-04-2026")]), "misread"),
    (rep([R1, dict(R2, doc_id="5")]), "appear twice"),
])
def test_report_checks(inp, needle):
    got = check_report(inp, "11/02/2026", "11/06/2026", "JDOE")
    assert (got is None) if needle is None else (needle in got)


@pytest.mark.parametrize("change,needle", [
    ({}, None),
    ({"title_acct": "40388"}, "wrong patient"),
    ({"doc_id": "7012"}, "Double-click the right row"),
    ({"doc_date": "11/02/2026"}, "re-read"),
    ({"reviewed_line": "KLEE Lee, Kim 11/07/26 09:50"}, "is not JDOE's"),
    ({"reviewed_line": "JDOE Doe, Jordan 10/22/26 08:15"}, "has no 11/07/26 date"),
    ({"status": "already_reviewed", "reviewed_line": "JDOE Doe, Jordan 10/22/26 08:15"}, None),
    ({"status": "skipped", "reviewed_line": "(not reviewed)"}, None),
    ({"status": "skipped"}, "review stands"),
    ({"status": "not_found", "title_acct": ""}, None),
])
def test_item_checks(change, needle):
    got = check_item({**GOOD, **change}, ROW, "JDOE", "11/07/2026")
    assert (got is None) if needle is None else (needle in got)


def test_export_stopped_by_hand_writes_the_record_and_tells_the_launcher(tmp_path):
    client = FakeClient([[tool("left_click", {"coordinate": [1, 1]})]] * 3)
    with pytest.raises(SystemExit) as e:
        export_files.main(["--backend", "desktop", "--id", "x", "--runs-dir", str(tmp_path)],
                          comp=FakeComputer(stop_after=1), client=client)
    assert e.value.code == 2
    res = json.loads((tmp_path / "x" / "result.json").read_text())
    assert res["result"]["status"] == "stopped" and "by hand" in res["result"]["note"]
    assert (tmp_path / "x" / "recording.json").exists()


def test_export_will_not_start_over_the_cap(tmp_path):
    (tmp_path / "export-spend.json").write_text(json.dumps({"total": 1.99, "runs": {}}))
    with pytest.raises(SystemExit) as e:
        export_files.main(["--cap", "2", "--runs-dir", str(tmp_path)], comp=FakeComputer(), client=FakeClient([]))
    assert "Spend cap reached" in str(e.value.code)


def test_queue_stops_after_three_failures_in_a_row(tmp_path):
    rows = [{"acct": str(i), "date": "11/03/2026", "doc_id": str(100 + i)} for i in range(5)]
    report = {"header_start": "11/02/2026", "header_end": "11/06/2026", "header_user": "JDOE",
              "rows": rows, "grand_total": 5, "note": ""}
    nf = {"status": "not_found", "patient_name": "", "title_acct": "", "doc_id": "", "doc_date": "",
          "reviewed_line": "none", "boxes": [], "note": "no match"}
    client = FakeClient([[tool("record_report", report)]] + [[tool("item_done", nf, i)] for i in range(5)])
    s = review_queue.main(["--id", "q", "--runs-dir", str(tmp_path)], comp=FakeComputer(), client=client)
    assert "three items in a row" in s["stopped"] and len(s["results"]) == 3
    assert json.loads((tmp_path / "q" / "summary.json").read_text())["rows"] == 5
