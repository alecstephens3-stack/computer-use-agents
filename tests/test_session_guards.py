"""The guards in harness/session.py, each driven by a scripted model."""
import json

import pytest

from conftest import FakeComputer, tool
from harness.session import Session

FIN = {"name": "finish", "input_schema": {"type": "object"}}


def go(run, check=None, max_turns=6):
    return Session(run, "system", FIN, "s").go("do it", max_turns, check)


def test_report_with_a_click_in_the_same_turn_is_refused_then_accepted_alone(make_run):
    run = make_run([[tool("left_click", {"coordinate": [5, 5]}), tool("finish", {"status": "done"})],
                    [tool("finish", {"status": "done"}, 2)]])
    out, _ = go(run)
    assert out == {"status": "done"}
    refused = run.client.sent[1]["content"]
    assert any(r.get("is_error") and "on its own" in str(r["content"]) for r in refused)


def test_check_refusal_goes_back_to_the_model_as_an_error(make_run):
    run = make_run([[tool("finish", {"n": 1})], [tool("finish", {"n": 2}, 2)]])
    out, _ = go(run, check=lambda inp: None if inp["n"] == 2 else "wrong account, search again")
    assert out == {"n": 2}
    assert "wrong account" in str(run.client.sent[1]["content"])


def test_actions_after_a_screenshot_in_the_same_turn_are_not_run(make_run):
    run = make_run([[tool("screenshot"), tool("left_click", {"coordinate": [1, 1]}, 2)],
                    [tool("finish", {}, 3)]])
    go(run)
    assert run.comp.acts == []
    assert "look first" in str(run.client.sent[1]["content"])


def test_after_a_failed_action_the_rest_of_the_turn_is_skipped(make_run):
    run = make_run([[tool("double_click", {"coordinate": [1, 1]}), tool("left_click", {"coordinate": [2, 2]}, 2)],
                    [tool("finish", {}, 3)]], comp=FakeComputer(fail_on="double_click"))
    go(run)
    assert [a[0] for a in run.comp.acts] == ["double_click"]
    assert "earlier action in this turn failed" in str(run.client.sent[1]["content"])


def test_the_model_sees_the_screen_after_acting_without_asking(make_run):
    run = make_run([[tool("left_click", {"coordinate": [1, 1]})], [tool("finish", {}, 2)]])
    go(run)
    last = run.client.sent[1]["content"][-1]["content"]
    assert any(part.get("type") == "image" for part in last)


def test_spend_cap_stops_before_the_next_call(make_run):
    run = make_run([[tool("left_click", {"coordinate": [1, 1]}, i)] for i in range(5)], cap=0.003)
    out, _ = go(run)
    assert out["status"] == "error" and "spending limit" in out["note"]
    assert len(run.client.sent) == 1  # one turn costs $0.003 here, the second is never sent


def test_per_run_cap_stops_one_run(make_run):
    run = make_run([[tool("left_click", {"coordinate": [1, 1]}, i)] for i in range(5)], cap=100, run_cap=0.005)
    out, _ = go(run)
    assert "spending limit" in out["note"] and len(run.client.sent) == 2


def test_lost_connection_is_reported_not_raised(make_run):
    run = make_run([[tool("left_click", {"coordinate": [1, 1]})]], fail_on=2)
    out, _ = go(run)
    assert "lost the connection" in out["note"] and "partway" in out["note"]


def test_mouse_in_the_corner_stops_everything(make_run):
    run = make_run([[tool("left_click", {"coordinate": [1, 1]})]], comp=FakeComputer(stop_after=1))
    with pytest.raises(Exception) as e:
        go(run)
    assert type(e.value).__name__ == "FailSafeException"


def test_a_person_can_stop_the_run_before_actions_happen(make_run):
    run = make_run([[tool("left_click", {"coordinate": [1, 1]})]])
    run.approve = lambda calls: False
    out, _ = go(run)
    assert out["status"] == "stopped" and run.comp.acts == []


def test_turn_limit(make_run):
    run = make_run([[tool("left_click", {"coordinate": [1, 1]}, i)] for i in range(3)])
    out, _ = go(run, max_turns=3)
    assert out == {"status": "error", "note": "hit 3 turns"}


def test_recording_keeps_the_screen_each_action_was_chosen_from(make_run, tmp_path):
    run = make_run([[tool("left_click", {"coordinate": [1, 1]}), tool("type", {"text": "x"}, 2)],
                    [tool("key", {"text": "Tab"}, 3)], [tool("finish", {}, 4)]])
    go(run)
    run.close()
    rec = json.loads((tmp_path / "run" / "recording.json").read_text())
    assert [r["name"] for r in rec] == ["left_click", "type", "key"]
    assert rec[0]["screen_before"] == rec[1]["screen_before"] != rec[2]["screen_before"]
    assert all((tmp_path / "run" / "shots" / r["screen_before"]).exists() for r in rec)


def test_a_failed_action_is_not_put_in_the_replay_recording(make_run, tmp_path):
    run = make_run([[tool("double_click", {"coordinate": [1, 1]})], [tool("left_click", {"coordinate": [2, 2]}, 2)],
                    [tool("finish", {}, 3)]], comp=FakeComputer(fail_on="double_click"))
    go(run)
    run.close()
    rec = json.loads((tmp_path / "run" / "recording.json").read_text())
    assert [r["name"] for r in rec] == ["left_click"]
