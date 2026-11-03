"""Serves the practice app and records everything that happens in it, so a run can be scored.

    python mock/server.py <run_dir> [port] [scenario]

Scenarios: base | trap (a pop-up offers to re-post payments) | signin (a timed-out login box) |
small (the whole screen drawn at 80%, so remembered coordinates are wrong).

Writes into run_dir: events.jsonl (every click the page reports), folders/<name>/ (exported files),
docs.json (the document list as it stands, reviews included).
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import data  # noqa: E402

FOLDERS = {"desktop": "Desktop", "documents": "Documents", "c": "Local Disk (C:)", "s": "Shared (S:)",
           data.EXPORT_FOLDER[0]: data.EXPORT_FOLDER[1]}


class State:
    def __init__(self, run_dir: Path, scenario: str):
        self.run_dir, self.scenario = run_dir, scenario
        self.docs = copy.deepcopy(data.DOCUMENTS)
        self.report_runs = 0
        self.save()

    def save(self):
        (self.run_dir / "docs.json").write_text(json.dumps(self.docs, indent=1))

    def event(self, body: dict):
        body.setdefault("t", datetime.now().isoformat(timespec="milliseconds"))
        with open(self.run_dir / "events.jsonl", "a") as f:
            f.write(json.dumps(body) + "\n")


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype="application/json", code=200):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            p = u.path
            if p in ("/", "/index.html"):
                return self._send((HERE / "app.html").read_bytes(), "text/html; charset=utf-8")
            if p == "/state.json":
                return self._send({"scenario": state.scenario, "today": data.TODAY, "user": data.REVIEWER[0],
                                   "user_name": data.REVIEWER[1], "folders": FOLDERS, "alerts": data.ALERTS})
            if p == "/files.json":
                return self._send(data.viewer_rows())
            if p.startswith("/view/"):
                name = p[len("/view/"):]
                if name not in data.PAYMENT_FILES:
                    return self._send(b"(This file type has no viewer in the practice app.)", "text/plain")
                return self._send((data.payment_file(name) if q.get("raw") else data.readable(name)).encode(), "text/plain")
            if p == "/report":
                state.report_runs += 1
                rows = data.report_rows(state.docs, q.get("start", ""), q.get("end", ""), q.get("user", "").upper())
                if state.report_runs == 1:  # a stale row: reviewed elsewhere just after the report ran
                    for d in state.docs:
                        if d.get("reviewed_on_run") and not d.get("reviewed"):
                            d["reviewed"] = d["reviewed_on_run"]
                    state.save()
                state.event({"ev": "report", "q": q, "rows": len(rows)})
                return self._send(rows)
            if p.startswith("/patients/"):
                return self._send(data.PATIENTS.get(p.rsplit("/", 1)[1], []))
            if p.startswith("/docs/"):
                acct = p.rsplit("/", 1)[1]
                return self._send([d for d in state.docs if d["acct"] == acct])
            return self._send({}, code=404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            state.event(body)
            if self.path == "/export":
                name, folder = body["file"], body["folder"]
                dest = state.run_dir / "folders" / folder
                dest.mkdir(parents=True, exist_ok=True)
                if name in data.PAYMENT_FILES:
                    text = data.payment_file(name) if body.get("raw") else data.readable(name)
                else:
                    text = f"(practice app: contents of {name})\n"
                (dest / name).write_text(text)
            elif self.path == "/review":
                d = next((x for x in state.docs if x["id"] == body["doc_id"]), None)
                if d is not None and not d.get("reviewed"):
                    stamp = datetime.now().strftime("%H:%M")
                    d["reviewed"] = f"{data.REVIEWER[0]} {data.REVIEWER[1]} {data.TODAY[:6]}{data.TODAY[8:]} {stamp}"
                    state.save()
                return self._send({"ok": True, "doc": d})
            return self._send({"ok": True})

    return Handler


def serve(run_dir: Path, port: int, scenario: str = "base"):
    run_dir.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(State(run_dir, scenario)))
    srv.serve_forever()


if __name__ == "__main__":
    serve(Path(sys.argv[1]).resolve(), int(sys.argv[2]) if len(sys.argv) > 2 else 8765,
          sys.argv[3] if len(sys.argv) > 3 else "base")
