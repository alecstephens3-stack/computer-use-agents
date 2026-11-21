"""One conversation with the model on the current screen, with the guards in code rather than in the prompt.

A task gives the model the computer toolset plus one report-back tool of its own (finish, record_report,
item_done). The session ends when that tool is accepted. On the way, the code enforces:

- The report back must stand alone, in a turn after the model has looked at the result of its last action.
  A report sent in the same turn as a click describes a screen the model has not seen yet.
- An optional check() reads the report and can refuse it with a reason (wrong account, a count that does
  not match the total). The model gets the reason as a tool error and has to fix it on screen.
- Look first: once a turn asks for a screenshot or zoom, the actions after it in that turn are not run.
- After a failed action, the rest of that turn's actions are not run.
- With an approve callback set, a person sees each turn's actions and can stop the run before they happen.
- The spending cap is checked before every call. The mouse-in-the-corner stop (or Ctrl+C) is re-raised
  so the task can write its record and stop the whole run, not just this conversation.

Every action goes into the run's trace, and every non-look action into the recording used for replay.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path

import anthropic
from PIL import Image

from harness.computer import WIDTH, png_bytes
from harness.ledger import Ledger, cost

TOOLSET = {"type": "computer_toolset_20260801"}


def img_block(img: Image.Image) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.standard_b64encode(png_bytes(img)).decode()}}


def ask_approval(calls) -> bool:
    """Terminal approval for supervised runs: Enter runs the turn's actions, n stops the run."""
    return not input(f"Run {', '.join(b.name for b in calls)}? [Enter = yes, n = stop] ").strip().lower().startswith("n")


def stopped_by_hand(e: BaseException) -> bool:
    """pyautogui's corner fail-safe or Ctrl+C: a person wants the run to stop."""
    return isinstance(e, KeyboardInterrupt) or type(e).__name__ == "FailSafeException"


class Run:
    """The things one run shares across its sessions: the screen, the client, the budget and the record."""

    def __init__(self, run_id: str, out: Path, comp, ledger: Ledger, model: str, effort: str, client=None):
        self.id, self.out, self.comp, self.ledger = run_id, Path(out), comp, ledger
        self.model, self.effort = model, effort
        (self.out / "shots").mkdir(parents=True, exist_ok=True)
        self.client = client or anthropic.Anthropic(max_retries=3)
        self._trace = open(self.out / "trace.jsonl", "a")
        self.n = 0
        self.recording: list[dict] = []
        self.last_shot = None
        self.approve = None  # optional callback(list of tool calls) -> bool, for supervised first runs

    @staticmethod
    def new_id(tag: str) -> str:
        return datetime.now().strftime("%m%d-%H%M%S") + "-" + tag

    def shot(self, label: str) -> Image.Image:
        img = self.comp.screenshot()
        self.n += 1
        self.last_shot = f"{self.n:04d}-{label}.png"
        img.save(self.out / "shots" / self.last_shot)
        return img

    def zoom(self, region) -> Image.Image:
        """A sharp close-up: crop the full-resolution screen, not the 1280-wide picture."""
        raw = self.comp.raw_screenshot()
        f = raw.width / WIDTH
        x0, y0, x1, y1 = [round(v * f) for v in region]
        crop = raw.crop((x0, y0, x1, y1))
        k = min(WIDTH / max(1, crop.width), 800 / max(1, crop.height), 2.0)
        return crop.resize((max(1, round(crop.width * k)), max(1, round(crop.height * k))), Image.LANCZOS) if k > 1 else crop

    def trace(self, rec: dict):
        self._trace.write(json.dumps(rec) + "\n")
        self._trace.flush()

    def close(self):
        self._trace.close()
        (self.out / "recording.json").write_text(json.dumps(self.recording, indent=1))


class Session:
    def __init__(self, run: Run, system: str, report_tool: dict, label: str):
        self.r, self.system, self.tool, self.label = run, system, report_tool, label
        self.turns = 0

    def go(self, text: str, max_turns: int, check=None) -> tuple[dict, float]:
        r, name = self.r, self.tool["name"]
        first = r.shot(self.label + "-start")
        messages = [{"role": "user", "content": [{"type": "text", "text": text}, img_block(first)]}]
        system = [{"type": "text", "text": self.system}]
        tools = [TOOLSET, self.tool]
        spent_here = 0.0
        for turn in range(1, max_turns + 1):
            self.turns = turn
            if r.ledger.over():
                where = "before this task; nothing was touched" if turn == 1 else "partway through; check the screen by hand"
                return {"status": "error", "note": f"spending limit reached {where}"}, spent_here
            try:
                resp = r.client.messages.create(model=r.model, max_tokens=8000, system=system, tools=tools,
                                                messages=messages, output_config={"effort": r.effort},
                                                cache_control={"type": "ephemeral"})
            except anthropic.APIError as e:
                where = "before this task; nothing was touched" if turn == 1 else "partway through; check the screen by hand"
                return {"status": "error", "note": f"lost the connection to the model {where} "
                                                   f"({type(e).__name__}: {getattr(e, 'message', e)})"}, spent_here
            c = cost(r.model, resp.usage)
            spent_here += c
            r.ledger.add(r.id, c)
            messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in resp.content]})
            calls = [b for b in resp.content if b.type == "tool_use"]
            notes = [b.text for b in resp.content if b.type == "text" and b.text.strip()]
            u = resp.usage
            r.trace({"session": self.label, "turn": turn, "served_by": getattr(resp, "model", None), "stop": resp.stop_reason, "in": u.input_tokens,
                     "cache_write": getattr(u, "cache_creation_input_tokens", 0),
                     "cache_read": getattr(u, "cache_read_input_tokens", 0), "out": u.output_tokens,
                     "cost": round(c, 5), "notes": notes, "actions": [{"name": b.name, "input": b.input} for b in calls]})
            print(f"  {self.label} t{turn:02d} ${r.ledger.this_run():.3f} " + ", ".join(
                f"{b.name}{tuple(b.input.get('coordinate', ())) or ''}"
                f"{' ' + repr(b.input.get('text'))[:30] if b.input.get('text') else ''}" for b in calls), flush=True)
            if resp.stop_reason == "refusal":
                return {"status": "error", "note": "the model declined to continue"}, spent_here
            if not calls:
                messages.append({"role": "user", "content": [{"type": "text", "text": f"Keep going, and call {name} when you are finished."}]})
                continue
            if r.approve and not r.approve(calls):
                return {"status": "stopped", "note": "a person stopped the run before these actions"}, spent_here

            results, final, failed, looked = [], None, False, False
            for b in calls:
                if b.name == name:
                    problem = (f"Not recorded: call {name} on its own, in a turn after you have looked at the result "
                               "of your last action.") if len(calls) > 1 else (check(b.input) if check else None)
                    if problem:
                        r.trace({"event": "report_refused", "session": self.label, "turn": turn, "why": problem})
                        results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True, "content": problem})
                    else:
                        final = dict(b.input)
                        results.append({"type": "tool_result", "tool_use_id": b.id, "content": "Recorded."})
                    continue
                base = {"type": "tool_result", "tool_use_id": b.id, "toolset_name": "computer"}
                if failed:
                    results.append({**base, "is_error": True, "content": "Not executed: an earlier action in this turn failed."})
                    continue
                if looked:
                    results.append({**base, "is_error": True,
                                    "content": "Not executed: you asked to look first. Act after you have seen the picture."})
                    continue
                try:
                    if b.name in ("screenshot", "zoom"):
                        img = r.shot(self.label)
                        if b.name == "zoom":
                            img = r.zoom(b.input["region"])
                        results.append({**base, "content": [img_block(img)]})
                        looked = True
                    else:
                        txt = r.comp.act(b.name, b.input)
                        # only actions that ran go into the replay recording
                        r.recording.append({"session": self.label, "name": b.name, "input": b.input,
                                            "screen_before": r.last_shot})
                        results.append({**base, "content": [{"type": "text", "text": txt or "OK"}]})
                except Exception as e:  # noqa: BLE001
                    if stopped_by_hand(e):
                        raise
                    failed = True
                    results.append({**base, "is_error": True, "content": f"Error: {e}"})
            if final is not None:
                return final, spent_here
            acted = [x for x in results if "toolset_name" in x]
            if acted and not looked:  # show the model what its actions did
                img = r.shot(self.label)
                last = acted[-1]
                if isinstance(last["content"], str):
                    last["content"] = [{"type": "text", "text": last["content"]}]
                last["content"].append(img_block(img))
            messages.append({"role": "user", "content": results})
        return {"status": "error", "note": f"hit {max_turns} turns"}, spent_here
