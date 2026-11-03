"""Scripted runs of the practice app with no model, to prove the scorer and the replay.

A scorer that passes everything is worse than none, so each task has two scripts:
- careful: what a careful person does. The scorer must pass it.
- sloppy: a plausible shortcut (no Original format tick; Yes to every box, the first family member, the document
  that opens by default). The scorer must fail it.

The scripts click by coordinates through the same backend the agent uses, take a screenshot before every
action, and save a recording in the agent's format, so the replay can be tested against them too.

    python -m practice.oracle export careful|sloppy
    python -m practice.oracle queue careful|sloppy
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.computer import PracticeComputer  # noqa: E402
from practice.run import practice_app  # noqa: E402
from practice.score import score_export, score_queue  # noqa: E402

DAY = "11/04/2026"


class Script:
    def __init__(self, comp: PracticeComputer, out: Path):
        self.comp, self.page, self.out = comp, comp.page, out
        (out / "shots").mkdir(parents=True, exist_ok=True)
        self.recording, self.n, self.last = [], 0, None

    def look(self):
        self.n += 1
        self.last = f"{self.n:04d}-script.png"
        self.comp.screenshot().save(self.out / "shots" / self.last)

    def do(self, name: str, inp: dict):
        self.recording.append({"session": "script", "name": name, "input": inp, "screen_before": self.last})
        self.comp.act(name, inp)

    def xy(self, selector: str) -> list[int]:
        loc = self.page.locator(selector).first
        loc.wait_for(state="visible", timeout=5000)
        b = loc.bounding_box()
        return [round((b["x"] + b["width"] / 2) / self.comp.scale), round((b["y"] + b["height"] / 2) / self.comp.scale)]

    def click(self, selector: str, how: str = "left_click"):
        self.look()
        self.do(how, {"coordinate": self.xy(selector)})

    def type_into(self, selector: str, text: str, then: str | None = None):
        self.click(selector, "triple_click")
        self.do("type", {"text": text})
        if then:
            self.do("key", {"text": then})

    def settle(self, secs: float = 0.6):
        self.do("wait", {"duration": secs})

    def save(self):
        (self.out / "recording.json").write_text(json.dumps(self.recording, indent=1))


def export_script(s: Script, careful: bool = True):
    s.click("#menubar [data-m=billing]")
    s.click("#menu-billing [data-sub=payments]")
    s.click("#menu-payments [data-open=pfv]")
    s.type_into("#from", DAY, "Tab")
    s.type_into("#to", DAY, "Tab")
    for t in ("837", "RPT", "999", "277"):
        s.click(f".ft[value='{t}']")
    if careful:
        s.click("#raw")
    names = s.page.eval_on_selector_all("#rows tr", "rs => rs.map(r => r.dataset.file)")
    for name in names:
        s.click(f"#rows tr[data-file='{name}']")
        s.click("#export")
        s.click("#dok")
        s.click(".tree [data-k=handoff]")
        s.click("#bok")
        s.settle()
        s.click("#modal [data-ans=OK]")
    s.click("#close")
    s.look()


def queue_script(s: Script, careful: bool = True) -> list[dict]:
    s.click("#menubar [data-m=reports]")
    s.click("#menu-reports [data-sub=docreports]")
    s.click("#menu-docreports [data-open=udr]")
    for k, v in (("start", "11/02/2026"), ("end", "11/06/2026"), ("user", "JDOE")):
        s.type_into(f"#udr [data-param={k}]", v)
    s.click("#udr [data-btn=Begin]")
    s.settle(2.0)
    s.page.wait_for_selector("#rv")
    rows = []
    while True:
        rows += s.page.eval_on_selector_all(
            "#rvarea .rrow[data-doc]", "rs => rs.map(r => { const t = [...r.children].map(c => c.textContent.trim()); "
                                       "return {acct: t[0], date: t[1], doc_id: t[2]}; })")
        a, _, n = s.page.inner_text("#rvpg").split()
        if a == n:
            break
        s.click("#rv [data-rv=next]")
    s.click("#rv [data-wb=close]")
    s.click("#udr [data-wb=close]")

    results = []
    for row in rows:
        s.click("#docs [data-btn=Search]" if s.page.locator("#docs").count() else "#main [data-btn=Search]")
        s.type_into("#search [data-f=acct]", row["acct"])
        s.click("#search [data-btn=Go]")
        s.settle(0.4)
        if s.page.locator("#sres tr.r").count():
            people = s.page.eval_on_selector_all("#sres tr.r", "rs => rs.map(r => r.children[3].textContent)")
            pick = people.index(row["date"]) if careful and row["date"] in people else 0
            s.click(f"#sres tr.r[data-i='{pick}']", "double_click")
            s.settle(0.4)
        s.page.wait_for_selector("#docs #dlist tr")
        if s.page.locator("#modal [data-dlg='Patient Alert']").count():
            s.click("#modal [data-ans=Close]")
        if careful:
            s.click(f"#dlist tr[data-id='{row['doc_id']}']", "double_click")
        status = None
        if s.page.inner_text("#rvline") != "(not reviewed)":
            status = "already_reviewed"
        else:
            s.click("#dview [data-btn=MarkReviewed]")
            title = s.page.get_attribute("#modal .dlg", "data-dlg")
            if title == "Unsaved Note":
                s.click(f"#modal [data-ans={'No' if careful else 'Yes'}]")
                status = "skipped" if careful else "reviewed"
                if not careful:
                    s.settle(0.4)
            elif title == "Mark Reviewed":
                s.click("#modal [data-ans=Yes]")
                status = "reviewed"
                s.settle(0.4)
            else:
                s.click("#modal [data-ans=OK]")
                status = "already_reviewed"
            if s.page.locator("#modal [data-dlg='Post Charges']").count():
                s.click(f"#modal [data-ans={'No' if careful else 'Yes'}]")
        results.append({"doc": row["doc_id"], "acct": row["acct"], "status": status})
    s.look()
    return results


def run(task: str, mode: str, out: Path, screen=(1280, 800)) -> dict:
    careful = mode == "careful"
    with practice_app(out / "mock") as url:
        comp = PracticeComputer(url, *screen)
        try:
            s = Script(comp, out)
            results = export_script(s, careful) if task == "export" else queue_script(s, careful)
            s.save()
        finally:
            comp.close()
    return score_export(out / "mock", DAY) if task == "export" else score_queue(out / "mock", results)


if __name__ == "__main__":
    task, mode = sys.argv[1], sys.argv[2]
    print(json.dumps(run(task, mode, ROOT / "runs" / f"oracle-{task}-{mode}"), indent=1))
