"""End to end on the practice app in headless Chromium: the scorer, the replay, and screen scaling."""
import json

from conftest import needs_browser
from practice import oracle
from practice.run import main as practice_main

import data


def test_payment_files_are_valid_835s_and_deterministic():
    from practice.score import claims_in_835
    for name in data.PAYMENT_FILES:
        assert data.payment_file(name) == data.payment_file(name)
        assert claims_in_835(data.payment_file(name)) >= 3
    assert claims_in_835(data.readable("PMT_03_559104.835")) == 0


def test_answer_key_covers_every_trap():
    key = data.answer_key()
    assert set(key.values()) == {"reviewed", "skipped", "already_reviewed"}
    assert "7055" not in key and "6644" not in key  # another user's document, and one reviewed last month


@needs_browser
def test_scorer_passes_the_careful_script_and_fails_the_sloppy_one(tmp_path):
    assert oracle.run("export", "careful", tmp_path / "ec")["pass"]
    assert not oracle.run("export", "sloppy", tmp_path / "es")["pass"]
    assert oracle.run("queue", "careful", tmp_path / "qc")["pass"]
    sloppy = oracle.run("queue", "sloppy", tmp_path / "qs")
    assert not sloppy["pass"]
    assert {v["what"] for v in sloppy["violations"]} == {"posted_charges", "discarded_note"}
    assert sloppy["other_documents_changed"] == ["7055"]


@needs_browser
def test_queue_works_on_a_scaled_screen(tmp_path):
    assert oracle.run("queue", "careful", tmp_path / "q", screen=(1440, 810))["pass"]


@needs_browser
def test_replay_finishes_on_the_same_screen_and_stops_when_it_changes(tmp_path):
    rec = tmp_path / "rec"
    assert oracle.run("export", "careful", rec)["pass"]
    same = practice_main(["export", "--replay", str(rec), "--runs-dir", str(tmp_path / "r")])
    assert same["agent"]["done"] and same["score"]["pass"] and same["agent"]["cost"] == 0
    for scenario in ("trap", "small"):
        r = practice_main(["export", "--replay", str(rec), "--scenario", scenario, "--runs-dir", str(tmp_path / scenario)])
        assert r["agent"]["drift_stop"] is not None
        assert not r["score"]["violations"]  # it stopped before touching the pop-up
