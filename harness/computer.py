"""The screen the agent sees and drives.

Two backends with one interface (screenshot, raw_screenshot, act, close):
- PracticeComputer: headless Chromium showing the practice app. Used by tests and practice runs.
- DesktopComputer: the real screen, through pyautogui. Pushing the mouse into a corner stops it.

The model always works in a picture 1280 pixels wide. Each backend scales its screenshots down
to that and scales the model's coordinates back up, so a 1440x810 or Retina screen behaves the
same as the practice app. Keys arrive in xdotool style ("ctrl+a", "Return", "Tab").
"""
from __future__ import annotations

import io
import platform
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image

WIDTH = 1280

_PW_KEYS = {
    "return": "Enter", "enter": "Enter", "kp_enter": "Enter", "tab": "Tab", "escape": "Escape",
    "esc": "Escape", "backspace": "Backspace", "delete": "Delete", "space": "Space",
    "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight",
    "page_up": "PageUp", "page_down": "PageDown", "prior": "PageUp", "next": "PageDown",
    "home": "Home", "end": "End", "ctrl": "Control", "control": "Control", "alt": "Alt",
    "shift": "Shift", "super": "Meta", "cmd": "Meta", "meta": "Meta",
}
_GUI_KEYS = {
    "return": "enter", "kp_enter": "enter", "escape": "esc", "page_up": "pageup",
    "page_down": "pagedown", "prior": "pageup", "next": "pagedown", "super": "command",
    "cmd": "command", "meta": "command", "control": "ctrl",
}


def pw_combo(text: str) -> str:
    """"ctrl+a" -> "Control+a", "f5" -> "F5", for Playwright's keyboard."""
    out = []
    for p in (x for x in text.replace(" ", "").split("+") if x):
        k = _PW_KEYS.get(p.lower(), p)
        if len(k) > 1 and k[0] in "fF" and k[1:].isdigit():
            k = "F" + k[1:]
        out.append(k)
    return "+".join(out)


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class _Scaled:
    scale: float = 1.0
    size: tuple[int, int]

    def raw_screenshot(self) -> Image.Image:  # pragma: no cover - backends override
        raise NotImplementedError

    def screenshot(self) -> Image.Image:
        raw = self.raw_screenshot()
        return raw if raw.size == self.size else raw.resize(self.size, Image.LANCZOS)

    def _real(self, xy):
        return [round(v * self.scale) for v in xy]


class PracticeComputer(_Scaled):
    """Headless Chromium on the practice app at any window size; the model sees it 1280 wide."""

    def __init__(self, url: str, w: int = 1280, h: int = 800):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        self._ctx = self._browser.new_context(viewport={"width": w, "height": h})
        self.page = self._ctx.new_page()
        self.page.goto(url)
        self.page.wait_for_function("window.__ready === true")
        self.scale = w / WIDTH
        self.size = (WIDTH, round(h / self.scale))

    def raw_screenshot(self) -> Image.Image:
        return Image.open(io.BytesIO(self.page.screenshot())).convert("RGB")

    def close(self):
        self._ctx.close()
        self._browser.close()
        self._pw.stop()

    def act(self, name: str, inp: dict) -> str | None:
        m, kb = self.page.mouse, self.page.keyboard
        xy = self._real(inp["coordinate"]) if inp.get("coordinate") else None
        if xy:
            m.move(*xy)
        mods = [_PW_KEYS.get(k.lower(), k) for k in (inp.get("text") or "").split("+") if k] \
            if (name.endswith("click") or name == "scroll") else []
        for k in mods:
            kb.down(k)
        try:
            if name == "left_click":
                if xy:
                    m.click(*xy)
                else:
                    m.down(); m.up()
            elif name == "right_click":
                m.down(button="right"); m.up(button="right")
            elif name == "middle_click":
                m.down(button="middle"); m.up(button="middle")
            elif name == "double_click":
                if xy:
                    m.dblclick(*xy)
            elif name == "triple_click":
                if xy:
                    m.click(*xy, click_count=3)
            elif name == "left_click_drag":
                m.move(*self._real(inp["start_coordinate"])); m.down(); m.move(*xy, steps=8); m.up()
            elif name == "mouse_move":
                pass
            elif name == "left_mouse_down":
                m.down()
            elif name == "left_mouse_up":
                m.up()
            elif name == "scroll":
                d, n = inp.get("scroll_direction", "down"), int(inp.get("scroll_amount", 3)) * 100
                m.wheel(n if d == "right" else -n if d == "left" else 0, n if d == "down" else -n if d == "up" else 0)
            elif name == "type":
                kb.type(inp["text"], delay=8)
            elif name == "key":
                for _ in range(int(inp.get("repeat", 1))):
                    kb.press(pw_combo(inp["text"]))
            elif name == "hold_key":
                k = pw_combo(inp["text"]); kb.down(k); time.sleep(min(float(inp.get("duration", 1)), 5)); kb.up(k)
            elif name == "wait":
                secs = min(float(inp.get("duration", 1)), 60)
                time.sleep(secs)
                return f"waited {secs:g} s"
            elif name == "cursor_position":
                return "unknown"
            else:
                raise ValueError(f"unsupported action {name}")
        finally:
            for k in reversed(mods):
                kb.up(k)
        self.page.wait_for_timeout(250)
        return None


class DesktopComputer(_Scaled):
    """The real screen. On macOS the app running the agent needs Screen Recording and
    Accessibility permission. Push the mouse into a screen corner to stop the run."""

    def __init__(self, settle: float = 0.6):
        import pyautogui

        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.15
        self.gui, self.settle = pyautogui, settle
        lw, lh = pyautogui.size()
        self.scale = lw / WIDTH
        self.size = (WIDTH, round(lh / self.scale))

    def raw_screenshot(self) -> Image.Image:
        if platform.system() == "Darwin":  # pyautogui's own capture is slow on Retina screens
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "s.png"
                subprocess.run(["screencapture", "-x", "-m", "-t", "png", str(p)], check=True)
                img = Image.open(p).convert("RGB")
                img.load()
            return img
        return self.gui.screenshot().convert("RGB")

    def close(self):
        pass

    def act(self, name: str, inp: dict) -> str | None:
        g = self.gui
        if inp.get("coordinate"):
            g.moveTo(*self._real(inp["coordinate"]))
        mods = [_GUI_KEYS.get(m.lower(), m.lower()) for m in (inp.get("text") or "").split("+") if m] \
            if (name.endswith("click") or name == "scroll") else []
        for k in mods:
            g.keyDown(k)
        try:
            if name == "left_click":
                g.click()
            elif name == "right_click":
                g.click(button="right")
            elif name == "middle_click":
                g.click(button="middle")
            elif name == "double_click":
                g.doubleClick()
            elif name == "triple_click":
                g.tripleClick()
            elif name == "left_click_drag":
                g.moveTo(*self._real(inp["start_coordinate"])); g.dragTo(*self._real(inp["coordinate"]), duration=0.4)
            elif name == "mouse_move":
                pass
            elif name == "left_mouse_down":
                g.mouseDown()
            elif name == "left_mouse_up":
                g.mouseUp()
            elif name == "scroll":
                d, n = inp.get("scroll_direction", "down"), int(inp.get("scroll_amount", 3))
                (g.hscroll if d in ("left", "right") else g.scroll)(n if d in ("up", "right") else -n)
            elif name == "type":
                g.write(inp["text"], interval=0.02)
            elif name == "key":
                keys = [_GUI_KEYS.get(k.lower(), k.lower()) for k in inp["text"].replace(" ", "").split("+")]
                for _ in range(int(inp.get("repeat", 1))):
                    g.hotkey(*keys)
            elif name == "hold_key":
                k = _GUI_KEYS.get(inp["text"].lower(), inp["text"].lower()); g.keyDown(k)
                time.sleep(min(float(inp.get("duration", 1)), 5)); g.keyUp(k)
            elif name == "wait":
                secs = min(float(inp.get("duration", 1)), 60)
                time.sleep(secs)
                return f"waited {secs:g} s"
            elif name == "cursor_position":
                x, y = g.position()
                return f"X={round(x / self.scale)}, Y={round(y / self.scale)}"
            else:
                raise ValueError(f"unsupported action {name}")
        finally:
            for k in reversed(mods):
                g.keyUp(k)
        time.sleep(self.settle)  # remote desktop sessions repaint slower than a local page
        return None
