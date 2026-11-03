"""Do a recorded run's clicks again without the model, for $0, and stop the moment the screen differs.

The recording keeps, for each action, the screenshot the model was looking at when it chose it. Before
the first action taken from each screenshot, replay compares the live screen with that picture. If more
than 0.5% of pixels changed noticeably, the app is not in the state the model planned for (a pop-up, a
different row order, a window in another place), so replay stops there and the model takes over.

Counting changed pixels works better than averaging the whole difference: a pop-up covers a small part
of the screen, and its average difference can look like noise.
"""
from __future__ import annotations

import time
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

PIXEL_THRESHOLD = 40      # a channel difference above this counts as "changed"
CHANGED_LIMIT = 0.005     # 0.5% of the screen, about 5,000 pixels at 1280x800


def changed_share(a: Image.Image, b: Image.Image) -> float:
    if a.size != b.size:
        return 1.0
    gray = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).convert("L")
    mask = gray.point(lambda v: 255 if v > PIXEL_THRESHOLD else 0)
    return ImageStat.Stat(mask).mean[0] / 255


def replay(recording: list[dict], shots_dir: Path, comp, out: Path | None = None) -> dict:
    t0, prev, checks, stop = time.time(), None, [], None
    for i, step in enumerate(recording):
        if step["screen_before"] != prev:
            prev = step["screen_before"]
            if step["name"] == "wait":
                comp.act("wait", step["input"])
            now = comp.screenshot()
            ref = Image.open(Path(shots_dir) / step["screen_before"]).convert("RGB")
            share = changed_share(now, ref)
            checks.append(round(share * 100, 2))
            if share > CHANGED_LIMIT:
                stop = {"step": i, "action": step["name"], "changed_pct": round(share * 100, 2)}
                if out:
                    out.mkdir(parents=True, exist_ok=True)
                    now.save(out / f"drift-{i:03d}.png")
                break
            if step["name"] == "wait":
                continue
        comp.act(step["name"], step["input"])
    return {"steps": len(recording), "done": stop is None, "drift_stop": stop,
            "screen_checks_pct": checks, "seconds": round(time.time() - t0, 1), "cost": 0.0}
