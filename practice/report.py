"""Write docs/EVAL.md and docs/eval/ from the evaluation folders. Every number in the write-up is computed here.

    python -m practice.report
"""
from __future__ import annotations

import collections
import glob
import json
import shutil
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from practice import eval as ev  # noqa: E402
from tasks import export_files, review_queue  # noqa: E402

RUNS, DOCS = ROOT / "runs", ROOT / "docs"
MODELS = ev.MODELS
ALL_RUNS = [r[0] for r in ev.RUNS]
CONTROL = ["E04", "E05", "E06", "E07", "E09", "E10", "E11", "E12", "E13", "Q1"]


def graded(folder: str, only=None, n_export=6, n_queue=5, models=MODELS):
    saved = ev.RUNS
    if only:
        ev.RUNS = [r for r in saved if r[0] in only]
    rows, todo = ev.collect(RUNS / folder, models, n_export, n_queue)
    ev.RUNS = saved
    assert not todo, f"{folder}: {len(todo)} runs missing"
    return rows


def spend(folder: str, model: str | None = None) -> float:
    pat = f"{model.split('-')[1]}/**/*-spend.json" if model else "**/*-spend.json"
    return sum(json.loads(Path(f).read_text())["total"] for f in glob.glob(str(RUNS / folder / pat), recursive=True))


def run_stats(folder: str):
    """Per model and task type, from each run's raw records: runs, runs passed, confirmed changes, clicks on a
    button that changes data, times a trap was put in front of the agent, refused reports, cost, time."""
    s = collections.defaultdict(lambda: collections.defaultdict(list))
    for f in glob.glob(str(RUNS / folder / "*/*/*/*/score.json")):
        p = Path(f).relative_to(RUNS / folder).parts
        model, kind = p[0], "queue" if p[1].startswith("Q") else "export"
        d = json.loads(Path(f).read_text())
        k = s[(model, kind)]
        k["passed"].append(bool(d["score"]["pass"]))
        k["cost"].append(d["agent"].get("cost", 0.0))
        k["seconds"].append(d["agent"].get("seconds", 0))
        events = [json.loads(x) for x in (Path(f).parent / "mock" / "events.jsonl").read_text().splitlines() if x.strip()]
        k["unsafe"].append(sum(e.get("ev") == "VIOLATION" for e in events))
        k["risky"].append(sum(e.get("ev") == "risky_click" for e in events))
        # a trap in front of the agent: a pop-up it must decline, a login box, the note that widens the job
        k["exposed"].append(2 if kind == "queue" else int(any(e.get("ev") in ("trap_shown", "signin_shown") for e in events)
                                                               or d.get("scenario") == "banner"))
        # One conversation is one run of a label; the queue runs the report twice (once to read it, once to
        # re-check at the end), so a turn number that goes back down starts a new conversation.
        calls, last, part = collections.Counter(), {}, collections.Counter()
        for line in Path(f).with_name("trace.jsonl").read_text().splitlines():
            t = json.loads(line)
            if t.get("event"):
                continue
            if t["turn"] <= last.get(t["session"], 0):
                part[t["session"]] += 1
            last[t["session"]] = t["turn"]
            key = (t["session"], part[t["session"]])
            calls[key] += sum(a["name"] in ("finish", "record_report", "item_done") for a in t["actions"])
        k["sessions"].append(len(calls))
        k["refused_calls"].append(sum(max(0, v - 1) for v in calls.values()))
        k["refused_convs"].append(sum(v > 1 for v in calls.values()))
    return s


def upper(n: int) -> float:
    """The largest failure rate still consistent, at 95%, with zero failures in n tries."""
    return 1 - 0.05 ** (1 / n)


def md_table(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows])


def publish_samples():
    """A few whole run records in the repo, so a reader can read transcripts without running anything."""
    dest = ROOT / "examples" / "eval"
    picks = {"haiku-E05-popup-after-export": "eval/haiku/E05/01", "haiku-E05-no-rules": "eval-norules/haiku/E05/01",
             "sonnet-E11-order-to-the-agent": "eval/sonnet/E11/01", "opus-E06-login-box": "eval/opus/E06/01",
             "sonnet-Q1-queue": "eval/sonnet/Q1/01", "pilot-haiku-Q1-before-the-fix": "eval-pilot/haiku/Q1/01"}
    for name, src in picks.items():
        run = sorted((RUNS / src).glob("*/score.json"))[-1].parent
        (dest / name).mkdir(parents=True, exist_ok=True)
        for f in ("score.json", "trace.jsonl", "system_prompt.txt", "mock/events.jsonl"):
            if (run / f).exists():
                shutil.copy(run / f, dest / name / Path(f).name)
    return sorted(picks)


def main():
    main_rows, ctl_rows = graded("eval"), graded("eval-norules", CONTROL, 4, 3)
    pilot = graded("eval-pilot", [r for r in ALL_RUNS if r not in ("E11", "E12", "E13")], 1, 1)
    out = DOCS / "eval"
    out.mkdir(parents=True, exist_ok=True)
    slim = lambda rows: [{k: r[k] for k in ("model", "task", "screen", "run", "trial", "pass", "why", "cost", "seconds")} for r in rows]  # noqa: E731
    (out / "results.json").write_text(json.dumps(slim(main_rows), indent=1))
    (out / "results-no-rules.json").write_text(json.dumps(slim(ctl_rows), indent=1))
    (out / "results-pilot.json").write_text(json.dumps(slim(pilot), indent=1))

    S, C, P = ev.summary(main_rows, MODELS), ev.summary(ctl_rows, MODELS), ev.summary(pilot, MODELS)
    stats, cstats = run_stats("eval"), run_stats("eval-norules")
    short = lambda m: m.split("-")[1]  # noqa: E731
    tot = lambda st_, m, key: sum(sum(st_[(short(m), k)][key]) for k in ("export", "queue"))  # noqa: E731
    cnt = lambda st_, m: sum(len(st_[(short(m), k)]["cost"]) for k in ("export", "queue"))  # noqa: E731
    runs = {m: cnt(stats, m) for m in MODELS}
    for m in MODELS:
        S[m]["cost"] = spend("eval", m)
        S[m].update(runs=runs[m], runs_passed=tot(stats, m, "passed"), trap_exposures=tot(stats, m, "exposed"),
                    unsafe_actions=tot(stats, m, "unsafe"))
        C[m]["cost"] = spend("eval-norules", m)
    tasks = list(dict.fromkeys((r["task"], r["screen"]) for r in main_rows if r["model"] == MODELS[0]))
    kinds = collections.Counter(ev.TASKS[t][1] for t, _ in tasks)
    per_model_runs, per_model_trials, exposures = runs[MODELS[0]], S[MODELS[0]]["trials"], S[MODELS[0]]["trap_exposures"]
    assert len(set(runs.values())) == 1 and len({S[m]["trap_exposures"] for m in MODELS}) == 1
    errors = sum(1 for _ in open(RUNS / "eval/errors.jsonl")) if (RUNS / "eval/errors.jsonl").exists() else 0
    money = {"main": spend("eval"), "control": spend("eval-norules"), "pilot": spend("eval-pilot") + spend("eval-pilot2"),
             "retired": spend("eval-retired")}
    retired = len(glob.glob(str(RUNS / "eval-retired/**/score.json"), recursive=True))
    retired_pass = sum(json.loads(Path(f).read_text())["score"]["pass"] for f in glob.glob(str(RUNS / "eval-retired/**/score.json"), recursive=True))
    pilot2 = graded("eval-pilot2", ["Q1", "Q2"], 0, 3, MODELS[:1])
    pf = [r for r in pilot if r["model"] == MODELS[0] and not r["pass"]]
    fmt = [r for r in pf if "reported error" in r["why"]]
    never = [r for r in pf if "reported None" in r["why"]]

    head = md_table(
        ["Model", "Runs passed", "Graded trials passed", "Tasks passed on every trial", "Unsafe actions", "Spend"],
        [[ev.NAME[m], f"{v['runs_passed']} of {v['runs']}", f"{v['passed']} of {v['trials']}", f"{v['all_trials_passed']} of {v['tasks']}",
          f"{v['unsafe_actions']} in {v['trap_exposures']} trap exposures", f"${v['cost']:.2f}"] for m, v in S.items()])

    med = lambda m, kind, key: st.median(stats[(short(m), kind)][key])  # noqa: E731
    mean = lambda m, kind, key: st.mean(stats[(short(m), kind)][key])  # noqa: E731
    n_export = sum(1 for r in ev.RUNS if r[1] == "export")
    cost = md_table(
        ["Model", "Export run: cost", "time", "Queue run (8 documents): cost", "time", "All 15 runs once (mean)"],
        [[ev.NAME[m], f"${med(m, 'export', 'cost'):.3f}", f"{med(m, 'export', 'seconds'):.0f} s", f"${med(m, 'queue', 'cost'):.2f}",
          f"{med(m, 'queue', 'seconds'):.0f} s", f"${n_export * mean(m, 'export', 'cost') + 2 * mean(m, 'queue', 'cost'):.2f}"] for m in MODELS])
    from harness.ledger import PRICES
    prices = md_table(["Model", "Input", "Output", "Cache read"], [[ev.NAME[m], f"${PRICES[m][0]:.2f}", f"${PRICES[m][1]:.2f}", f"${PRICES[m][2]:.2f}"] for m in MODELS])

    def cell(rows, m, task, screen):
        ps = ev.by_task(rows, m).get((task, screen), [])
        return f"{sum(ps)} of {len(ps)}" if ps else ""

    def label(task, screen):
        return ev.TASKS[task][0] + (" (1440x810 screen)" if screen != "1280x800" and task != "E08" else "")

    per_task = md_table(["#", "Task", "Kind"] + [ev.NAME[m] for m in MODELS] + ["No rules: " + ev.NAME[m].split()[0] for m in MODELS],
                        [[i + 1, label(t, sc), ev.TASKS[t][1]] + [cell(main_rows, m, t, sc) for m in MODELS]
                         + [cell(ctl_rows, m, t, sc) for m in MODELS] for i, (t, sc) in enumerate(tasks)])
    num = {t: i + 1 for i, (t, sc) in enumerate(tasks) if sc == "1280x800"}

    wt = sorted(glob.glob(str(RUNS / "eval/sonnet/Q1/01/*/trace.jsonl")))[0]
    steps = []
    for line in Path(wt).read_text().splitlines():
        t = json.loads(line)
        if t.get("event") or t["session"] != "doc7031":
            continue
        acts = []
        for a in t["actions"]:
            i = a["input"]
            if a["name"] == "item_done":
                acts.append("item_done " + json.dumps({k: i[k] for k in ("status", "title_acct", "doc_id", "reviewed_line", "boxes", "note")}, indent=1))
            else:
                acts.append(a["name"] + (" " + json.dumps(i) if i else ""))
        steps.append((t["turn"], " ".join(t["notes"]).strip(), acts))
    worked = "\n\n".join(f"Turn {n}\nTHOUGHT: {note or '(none written)'}\nACTION:\n" + "\n".join("  " + a.replace("\n", "\n  ") for a in acts)
                         for n, note, acts in steps)
    shots = Path(wt).parent / "shots"
    (out / "worked-trial").mkdir(exist_ok=True)
    shutil.copy(shots / "0036-doc7031.png", out / "worked-trial" / "0036-doc7031.png")
    samples = publish_samples()

    conv = sum(tot(stats, m, "sessions") for m in MODELS)
    v = {"head": head, "cost": cost, "prices": prices, "per_task": per_task, "worked": worked,
         "tools": json.dumps([export_files.FINISH, review_queue.RECORD_REPORT, review_queue.ITEM_DONE], indent=1),
         "total_trials": len(main_rows), "total_runs": sum(runs.values()), "n_tasks": len(tasks), "act": kinds["act"], "hold": kinds["hold"],
         "per_model": per_model_trials, "per_model_runs": per_model_runs, "exposures": exposures, "all_exposures": 3 * exposures,
         "unsafe": sum(tot(stats, m, "unsafe") for m in MODELS), "risky": sum(tot(stats, m, "risky") for m in MODELS),
         "sessions": conv, "refused_convs": sum(tot(stats, m, "refused_convs") for m in MODELS),
         "refused_calls": sum(tot(stats, m, "refused_calls") for m in MODELS), "errors": errors,
         "up_runs": f"{upper(per_model_runs):.1%}", "up_tasks": f"{upper(len(tasks)):.1%}", "up_exposed": f"{upper(exposures):.1%}",
         "ctl_trials": len(ctl_rows), "ctl_passed": sum(r["pass"] for r in ctl_rows), "ctl_runs": sum(cnt(cstats, m) for m in MODELS),
         "ctl_unsafe": sum(tot(cstats, m, "unsafe") for m in MODELS), "ctl_exposures": sum(tot(cstats, m, "exposed") for m in MODELS),
         "pilot_haiku": f"{P[MODELS[0]]['passed']} of {P[MODELS[0]]['trials']}", "pilot_other": f"{P[MODELS[1]]['passed']} of {P[MODELS[1]]['trials']}",
         "pilot_fails": len(pf), "pilot_fmt": len(fmt), "pilot_never": len(never),
         "pilot_unreviewed": sum("ended not reviewed" in r["why"] for r in never),
         "pilot2": f"{sum(r['pass'] for r in pilot2)} of {len(pilot2)}", "pilot2_runs": len(glob.glob(str(RUNS / 'eval-pilot2/*/*/*/*/score.json'))),
         "m_main": f"{money['main']:.2f}", "m_ctl": f"{money['control']:.2f}", "m_pilot": f"{money['pilot']:.2f}", "m_retired": f"{money['retired']:.2f}",
         "m_all": f"{sum(money.values()):.2f}", "retired": retired, "retired_pass": retired_pass,
         "export_prompt": export_files.SYSTEM, "common": review_queue.COMMON,
         "report_task": review_queue.REPORT_TASK[len(review_queue.COMMON):].strip(), "item_task": review_queue.ITEM_TASK[len(review_queue.COMMON):].strip(),
         "haiku_x": f"{spend('eval', MODELS[2]) / spend('eval', MODELS[0]):.0f}", "samples": ", ".join(f"`{x}`" for x in samples),
         "n_note": num["7031"], "n_inject": num["E11"], "n_after": num["E05"], "n_nofiles": num["E10"], "n_banner": num["E13"],
         "n_stale": num["7040"], "n_first_hard": num["E11"], "n_last_hard": num["E13"]}
    (out / "summary.json").write_text(json.dumps({"main": S, "no_rules": C, "pilot": P, "spend": money}, indent=1))
    text = (ROOT / "practice" / "EVAL.template.md").read_text()
    for k, val in v.items():
        text = text.replace("{{" + k + "}}", str(val))
    assert "{{" not in text, text[text.index("{{"):][:60]
    (DOCS / "EVAL.md").write_text(text)
    print(head, "\n\n", cost)
    print({k: val for k, val in v.items() if len(str(val)) < 40})


if __name__ == "__main__":
    main()
