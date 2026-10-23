"""Regression tests for edge cases: odd characters, stops in mid-step, reused run directories, and more."""
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import (DONE, HELLO, NOTHING, ONE_GOAL, ROOT, events, hello_charter, hello_files, hello_script, light,
                      make_kernel, plan)
from minloop import trace
from minloop.__main__ import main, make_llm, parser, read_workspace
from minloop.kernel import ConfigError, Kernel
from minloop.llm import MockLLM, OpenAICompat
from minloop.llm.base import parse_json
from minloop.memory import Notes

RECORDED = ROOT / "examples" / "recorded" / "hello" / "trace.jsonl"


def test_line_separator_characters_do_not_break_the_trace_or_notes(tmp_path):
    files = {**hello_files(), "notes/odd.md": "pasted text with\x85separators"}
    s = hello_script()
    s["deep"][0]["notes"][0]["text"] += "  (pasted)"
    kernel = Kernel(hello_charter(), MockLLM(s), tmp_path / "r1", files=files)
    assert kernel.run()["reason"] == "done"
    assert trace.read(tmp_path / "r1" / "trace.jsonl")[-1]["type"] == "stop"
    assert " " in Notes(tmp_path / "r1" / "notes.jsonl").items["n1"]["text"]
    assert Kernel.resume(tmp_path / "r1", MockLLM({"goal": [DONE]})).run()["reason"] == "done"


def test_a_stop_in_mid_step_still_ends_the_step(tmp_path):
    s = hello_script()

    def ctrl_c(messages):
        raise KeyboardInterrupt
    s["reflect"].insert(1, ctrl_c)  # Ctrl-C during step 2's reflection, after its tool already ran
    assert make_kernel(tmp_path, s).run()["reason"] == "interrupt"
    run = tmp_path / "r1"
    state = json.loads((run / "state.json").read_text())
    assert state["step"] == 2 and events(run, "budget_tick")[-1]["step"] == 2
    rest = hello_script()
    rest = {"plan": rest["plan"][2:], "reflect": rest["reflect"][2:], "deep": rest["deep"], "goal": rest["goal"][1:]}
    assert Kernel.resume(run, MockLLM(rest)).run()["reason"] == "done"
    assert [e["step"] for e in events(run, "plan")] == list(range(1, 8))  # step 2 was not run twice


def test_token_budget_stop_after_the_tool_ran_keeps_state_in_step(tmp_path):
    result = make_kernel(tmp_path, charter=hello_charter(budget={"max_tokens": 1500})).run()
    run = tmp_path / "r1"
    assert result["reason"] == "budget"
    planned = {e["step"] for e in events(run, "plan")}
    assert json.loads((run / "state.json").read_text())["step"] == max(planned)


def test_an_existing_run_directory_is_never_reused_silently(tmp_path):
    make_kernel(tmp_path).run()
    with pytest.raises(ConfigError, match="not empty"):
        make_kernel(tmp_path, charter=hello_charter(mission="a different mission"))
    with pytest.raises(ConfigError, match="nothing to resume"):
        Kernel.resume(tmp_path / "missing", MockLLM({}))
    # --rerun into the directory of the recording itself is refused, and the recording is untouched
    original = (tmp_path / "r1" / "trace.jsonl").read_bytes()
    assert main(["replay", str(tmp_path / "r1" / "trace.jsonl"), "--rerun", "--root", str(tmp_path), "--quiet"]) == 2
    assert (tmp_path / "r1" / "trace.jsonl").read_bytes() == original


def test_an_uncounted_cost_budget_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="max_cost needs token prices"):
        make_kernel(tmp_path, charter=hello_charter(budget={"max_cost": 0.01}), forever=True)


def test_goal_rounds_without_goals_are_capped(tmp_path):
    charter = hello_charter(budget={"max_steps": 2}, stop={"no_progress_steps": 0})
    s = {"goal": [{"goals": []}, "not json", {"goals": []}] * 50}
    result = make_kernel(tmp_path, s, charter=charter).run()
    assert result["reason"] == "no_progress" and "3 rounds" in result["detail"]


def test_relative_notes_path_is_relative_to_the_caller(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = {"goal": [ONE_GOAL, DONE], "plan": [plan("finish", {"check": "ok"})], "reflect": [light()],
         "deep": [{"summary": "s", "notes": [{"kind": "self", "title": "t", "text": "x", "confidence": 1}]}]}
    make_kernel(tmp_path / "runs", s, notes_path="shared/notes.jsonl").run()
    assert len(Notes(tmp_path / "shared" / "notes.jsonl").items) == 1


def test_resume_after_no_progress_gets_a_fresh_chance(tmp_path):
    same = plan("fs_list", {"path": "notes"})
    s = {"goal": [ONE_GOAL, DONE], "plan": [same] * 5, "reflect": [light()] * 5, "deep": [NOTHING] * 5}
    assert make_kernel(tmp_path, s).run()["reason"] == "no_progress"
    rest = {"plan": [plan("finish", {"check": "ok"})], "reflect": [light()], "deep": [NOTHING], "goal": [DONE]}
    assert Kernel.resume(tmp_path / "r1", MockLLM(rest)).run()["reason"] == "done"


def test_parse_json_ignores_reasoning_before_a_lone_closing_think_tag():
    assert parse_json('draft {"tool": "fs_write"}</think>{"tool": "fs_read"}') == {"tool": "fs_read"}
    with pytest.raises(ValueError, match="NaN is not valid JSON"):
        parse_json('{"confidence": NaN}')


def test_trace_refuses_non_finite_numbers(tmp_path):
    t = trace.Trace(tmp_path / "trace.jsonl")
    with pytest.raises(ValueError):
        t.emit("warning", 0, message="x", value=math.nan)


def test_subscribers_see_goal_snapshots(tmp_path):
    seen = []
    make_kernel(tmp_path, on_event=[lambda e: seen.append(e)]).run()
    assert [(e["type"], e["goal"]["status"]) for e in seen if e["type"].startswith("goal")][:3] == [
        ("goal_set", "pending"), ("goal_update", "active"), ("goal_update", "done")]


def test_from_step_zero_hands_over_to_the_endpoint():
    a = parser().parse_args(["replay", str(RECORDED), "--rerun", "--from-step", "0", "--model", "m"])
    a.llm, a.replay_trace = "replay", str(RECORDED)
    llm = make_llm(a)
    assert isinstance(llm.then, OpenAICompat) and llm.calls == []


def test_read_workspace_keeps_bytes_and_refuses_binary(tmp_path):
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "crlf.txt").write_bytes(b"a\r\nb")
    charter_path = tmp_path / "charter.json"
    assert read_workspace(charter_path, {"workspace": "ws"}) == {"crlf.txt": "a\r\nb"}
    (tmp_path / "ws" / "blob.bin").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ConfigError, match="not UTF-8"):
        read_workspace(charter_path, {"workspace": "ws"})


def test_cli_reports_bad_input_without_a_traceback(tmp_path, capsys):
    assert main(["run", str(tmp_path / "missing.json"), "--llm", "mock", "--mock-script",
                 str(HELLO / "mock_script.json")]) == 2
    assert main(["run", "--resume", str(tmp_path / "nothing"), "--llm", "mock", "--mock-script",
                 str(HELLO / "mock_script.json")]) == 2
    err = capsys.readouterr().err
    assert "cannot read charter" in err and "nothing to resume" in err


def test_missing_usage_is_estimated_so_token_budgets_still_bind():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps({"choices": [{"message": {"content": "x" * 400}}]}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        reply = OpenAICompat(f"http://127.0.0.1:{server.server_port}/v1", "m").complete(
            [{"role": "user", "content": "y" * 4000}], "plan")
    finally:
        server.shutdown()
    assert reply.prompt_tokens >= 1000 and reply.completion_tokens == 100
