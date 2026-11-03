"""A scripted stand-in for the model and the screen, so the guards can be tested with no API key and no browser."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mock"))


class Block:
    def __init__(self, **k):
        self.__dict__.update(k)

    def model_dump(self, exclude_none=True):
        return dict(self.__dict__)


def tool(name, inp=None, i=0):
    return Block(type="tool_use", name=name, input=inp or {}, id=f"t{name}{i}")


def text(t):
    return Block(type="text", text=t)


USAGE = types.SimpleNamespace(input_tokens=1000, output_tokens=100, cache_creation_input_tokens=0, cache_read_input_tokens=0)


class FakeClient:
    """Returns one scripted turn per call and keeps what it was sent."""

    def __init__(self, turns, stop_reason="tool_use", fail_on=None):
        self.turns, self.sent, self.stop_reason, self.fail_on = list(turns), [], stop_reason, fail_on

    @property
    def messages(self):
        return self

    def create(self, **kw):
        import anthropic
        self.sent.append(kw["messages"][-1])
        if self.fail_on is not None and len(self.sent) == self.fail_on:
            raise anthropic.APIConnectionError(request=types.SimpleNamespace(method="POST", url="x"))
        return types.SimpleNamespace(content=self.turns.pop(0), usage=USAGE, stop_reason=self.stop_reason)


class FailSafeException(Exception):
    """Same name as pyautogui's corner stop."""


class FakeComputer:
    size = (1280, 800)
    scale = 1.0

    def __init__(self, stop_after=None, fail_on=None):
        self.acts, self.stop_after, self.fail_on, self.closed = [], stop_after, fail_on, False

    def screenshot(self):
        return Image.new("RGB", self.size, (200, 200, 200))

    raw_screenshot = screenshot

    def act(self, name, inp):
        self.acts.append((name, inp))
        if self.stop_after and len(self.acts) >= self.stop_after:
            raise FailSafeException("mouse in corner")
        if self.fail_on and name == self.fail_on:
            raise RuntimeError("click landed nowhere")
        return None

    def close(self):
        self.closed = True


@pytest.fixture
def make_run(tmp_path):
    from harness.ledger import Ledger
    from harness.session import Run

    def make(turns, cap=10.0, run_cap=0.0, comp=None, **client_kw):
        comp = comp or FakeComputer()
        ledger = Ledger(tmp_path / "spend.json", cap, run_cap)
        return Run("t", tmp_path / "run", comp, ledger, "claude-sonnet-5-5", "low", FakeClient(turns, **client_kw))
    return make


def browser_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:  # noqa: BLE001
        return False


needs_browser = pytest.mark.skipif(not browser_available(), reason="Playwright Chromium is not installed")
