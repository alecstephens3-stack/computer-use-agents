"""What a run costs, and the cap that stops it.

Every API call's dollars go into one JSON ledger per task. A run will not start once the
ledger total is within 5 cents of the cap, and stops between turns when it reaches it.
A per-run cap stops one runaway run without touching the total.
"""
from __future__ import annotations

import json
from pathlib import Path

# $ per million tokens: input, output, cache read. Cache writes (5 minute) cost 1.25x input.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.40),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
}


def cost(model: str, usage) -> float:
    pin, pout, pread = PRICES[model]
    write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (usage.input_tokens * pin + write * pin * 1.25 + read * pread + usage.output_tokens * pout) / 1e6


class Ledger:
    def __init__(self, path: Path, cap: float, run_cap: float = 0.0):
        self.path, self.cap, self.run_cap = Path(path), cap, run_cap
        self.at_start = self.total()

    def total(self) -> float:
        return json.loads(self.path.read_text())["total"] if self.path.exists() else 0.0

    def this_run(self) -> float:
        return self.total() - self.at_start

    def add(self, run_id: str, dollars: float):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(self.path.read_text()) if self.path.exists() else {"total": 0.0, "runs": {}}
        data["total"] = round(data["total"] + dollars, 6)
        data["runs"][run_id] = round(data["runs"].get(run_id, 0) + dollars, 6)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(self.path)

    def can_start(self) -> bool:
        return self.total() < self.cap - 0.05

    def over(self) -> bool:
        return self.total() >= self.cap or bool(self.run_cap and self.this_run() >= self.run_cap)
