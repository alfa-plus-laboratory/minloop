"""Acceptance 1: one full loop with the mock LLM."""
import json

from conftest import HELLO, ROOT, events, make_kernel
from minloop import trace
from minloop.__main__ import main


def test_mock_run_completes_one_full_loop(tmp_path):
    result = make_kernel(tmp_path).run()
    run = tmp_path / "r1"
    assert result["reason"] == "done" and result["steps"] == 7

    all_events = trace.read(run / "trace.jsonl")  # validates every event against the schema
    assert [e["seq"] for e in all_events] == list(range(len(all_events)))
    types = {e["type"] for e in all_events}
    for needed in ("run_start", "goal_set", "goal_update", "llm_call", "plan", "tool_call", "tool_result",
                   "observe", "reflect", "note_write", "budget_tick", "ask_human", "stop"):
        assert needed in types, needed
    start = all_events[0]
    assert start["type"] == "run_start" and start["schema_version"] == trace.SCHEMA_VERSION
    assert set(start["workspace_files"]) == {"notes/monday.md", "notes/tuesday.md", "notes/wednesday.md"}

    for e in events(run, "plan"):  # every step carries the structured self-report
        assert {"expected", "confidence", "unknowns"} <= set(e["plan"])
    kinds = [e["kind"] for e in events(run, "llm_call")]
    assert kinds.count("plan") == 7 and kinds.count("reflect") == 7 and kinds.count("goal") == 2
    assert [e["trigger"] for e in events(run, "reflect") if e["level"] == "deep"] == [
        "surprise 0.80", "periodic (every 5 steps)", "goal done"]

    assert "## Contradictions" in (run / "workspace" / "summary.md").read_text(encoding="utf-8")
    notes = [json.loads(line) for line in (run / "notes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [n["kind"] for n in notes] == ["world", "self", "skill"]
    state = json.loads((run / "state.json").read_text(encoding="utf-8"))
    assert state["last_stop"] == "done" and state["goals"]["items"]["g1"]["status"] == "done"


def test_cli_mock_run(tmp_path, capsys):
    code = main(["run", str(HELLO / "charter.json"), "--llm", "mock", "--mock-script",
                 str(HELLO / "mock_script.json"), "--root", str(tmp_path), "--run-id", "cli"])
    out = capsys.readouterr().out
    assert code == 0
    assert '"reason": "done"' in out and "[4] ask_human:" in out
    assert main(["inspect", str(tmp_path / "cli" / "trace.jsonl")]) == 0
    assert '"ask_human": 1' in capsys.readouterr().out


def test_bundled_recording_is_valid():
    events_ = trace.read(ROOT / "examples" / "recorded" / "hello" / "trace.jsonl")
    assert events_[0]["llm"]["adapter"] == "mock" and events_[-1]["reason"] == "done"
