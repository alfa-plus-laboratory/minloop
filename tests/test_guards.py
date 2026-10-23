"""Budgets, stop conditions, safety gates, resume, goals."""
import json

import pytest

from conftest import (DONE, NOTHING, ONE_GOAL, context_of, events, hello_charter, hello_script, light,
                      make_kernel, plan, request)
from minloop.kernel import ConfigError, Kernel
from minloop.llm import MockLLM


def script(plans, reflections=None, goals=(ONE_GOAL, DONE), deep=(NOTHING,) * 5):
    return {"goal": list(goals), "plan": list(plans), "reflect": reflections or [light()] * len(plans),
            "deep": list(deep)}


def test_refuses_to_start_without_budget(tmp_path):
    with pytest.raises(ConfigError, match="without a budget"):
        make_kernel(tmp_path, charter=hello_charter(budget={}))
    assert not (tmp_path / "r1").exists()


def test_forever_needs_token_or_cost_cap_and_no_progress_stop(tmp_path):
    with pytest.raises(ConfigError, match="max_tokens or max_cost"):
        make_kernel(tmp_path, charter=hello_charter(budget={"max_steps": 5}), forever=True)
    with pytest.raises(ConfigError, match="no_progress"):
        make_kernel(tmp_path, charter=hello_charter(budget={"max_tokens": 9000}, stop={"no_progress_steps": 0}),
                    forever=True)
    kernel = make_kernel(tmp_path, charter=hello_charter(budget={"max_steps": 1, "max_tokens": 10 ** 6}), forever=True)
    assert kernel.run()["steps"] == 7  # max_steps is ignored by --forever; the mission completes


@pytest.mark.parametrize("tool,flag", [("shell", "shell"), ("http_get", "network")])
def test_shell_and_network_are_off_unless_the_charter_allows_them(tmp_path, tool, flag):
    tools = ["fs_read", "finish", tool]
    with pytest.raises(ConfigError, match="off by default"):
        make_kernel(tmp_path, charter=hello_charter(tools=tools))
    kernel = make_kernel(tmp_path, charter=hello_charter(tools=tools, allow={flag: True}))
    assert len(kernel.warnings) == 1 and "ON" in kernel.warnings[0]
    kernel.run()
    assert any("ON" in e["message"] for e in events(tmp_path / "r1", "warning"))


def test_step_budget(tmp_path):
    result = make_kernel(tmp_path, charter=hello_charter(budget={"max_steps": 2})).run()
    assert (result["reason"], result["detail"], result["steps"]) == ("budget", "max_steps", 2)


def test_token_budget_is_a_hard_cap(tmp_path):
    result = make_kernel(tmp_path, charter=hello_charter(budget={"max_tokens": 3000})).run()
    assert (result["reason"], result["detail"]) == ("budget", "max_tokens")
    used = sum(e["usage"]["prompt_tokens"] + e["usage"]["completion_tokens"]
               for e in events(tmp_path / "r1", "llm_call"))
    assert used < 3000 + 2000  # at most one call past the line


def test_no_progress_stop(tmp_path):
    same = plan("fs_list", {"path": "notes"})
    result = make_kernel(tmp_path, script(plans=[same] * 10)).run()
    assert result["reason"] == "no_progress" and result["steps"] == 5  # 1 new result, then 4 repeats


def test_interrupt_then_resume(tmp_path):
    s = hello_script()

    def ctrl_c(messages):
        raise KeyboardInterrupt
    s["plan"].insert(2, ctrl_c)
    result = make_kernel(tmp_path, s).run()
    run = tmp_path / "r1"
    assert result["reason"] == "interrupt" and result["steps"] == 2
    state = json.loads((run / "state.json").read_text())
    assert state["step"] == 2 and state["last_stop"] == "interrupt"

    rest = hello_script()
    rest = {"plan": rest["plan"][2:], "reflect": rest["reflect"][2:], "deep": rest["deep"], "goal": rest["goal"][1:]}
    result = Kernel.resume(run, MockLLM(rest)).run()
    assert result["reason"] == "done" and result["session"] == 2 and result["steps"] == 7
    starts = events(run, "run_start")
    assert [e["session"] for e in starts] == [1, 2]
    # Working memory was rebuilt from the trace: the first plan of session 2 sees steps 1 and 2.
    first_plan = [e for e in events(run, "llm_call") if e["kind"] == "plan" and e["seq"] > starts[1]["seq"]][0]
    recent = context_of(run, first_plan)["recent_steps"]
    assert [r["step"] for r in recent] == [1, 2] and recent[1]["args"] == {"path": "notes/monday.md"}
    assert [e["step"] for e in events(run, "budget_tick")] == list(range(1, 8))
    assert [e["seq"] for e in events(run)] == list(range(len(events(run))))  # one sequence across sessions


def test_charter_is_read_only_and_proposals_are_recorded(tmp_path):
    charter = hello_charter()
    s = script([plan("propose_charter_change", {"change": "allow shell", "reason": "faster"}),
                plan("finish", {"check": "done"})])
    kernel = make_kernel(tmp_path, s, charter=charter)
    charter["mission"] = "changed after start"
    kernel.run()
    assert events(tmp_path / "r1", "charter_proposal")[0]["change"] == "allow shell"
    assert events(tmp_path / "r1", "run_start")[0]["charter"]["mission"] != "changed after start"
    state = json.loads((tmp_path / "r1" / "state.json").read_text())
    assert state["charter"]["tools"] == hello_charter()["tools"]


def test_task_mode_uses_the_charter_goal(tmp_path):
    charter = hello_charter(task={"text": "list the notes", "criteria": ["the note names were observed"]})
    s = script([plan("fs_list", {"path": "notes"}), plan("finish", {"check": "names observed"})], goals=())
    result = make_kernel(tmp_path, s, charter=charter).run()
    assert result["reason"] == "done" and "task" in result["detail"]
    assert events(tmp_path / "r1", "goal_set")[0]["goal"]["source"] == "charter"
    assert not [e for e in events(tmp_path / "r1", "llm_call") if e["kind"] == "goal"]


def test_invalid_json_is_retried_with_the_errors(tmp_path):
    s = script([plan("finish", {"check": "ok"})])
    s["plan"].insert(0, "I think I will read a file.")
    s["plan"].insert(1, {"intent": "x", "tool": "finish", "args": {}, "expected": "y", "confidence": 2, "unknowns": []})
    make_kernel(tmp_path, s).run()
    calls = [e for e in events(tmp_path / "r1", "llm_call") if e["kind"] == "plan"]
    assert [c["attempt"] for c in calls] == [0, 1, 2]
    feedback = request(tmp_path / "r1", calls[2])[-1]
    assert feedback["role"] == "user" and "$.confidence: must be <= 1" in feedback["content"]
    assert len(events(tmp_path / "r1", "plan")) == 1


def test_give_up_abandons_and_triggers_deep_reflection(tmp_path):
    s = script([plan("give_up", {"reason": "no network tool to fetch the data"})])
    make_kernel(tmp_path, s).run()
    goal = [e for e in events(tmp_path / "r1", "goal_update")][-1]["goal"]
    assert goal["status"] == "abandoned" and goal["result"].startswith("no network")
    assert [e["trigger"] for e in events(tmp_path / "r1", "reflect") if e["level"] == "deep"] == ["goal abandoned"]


def test_spawned_subgoal_blocks_its_parent_until_done(tmp_path):
    sub = {"text": "list the notes first", "criteria": ["names observed"]}
    s = script([plan("fs_list", {"path": "notes"}), plan("finish", {"check": "listed"}),
                plan("finish", {"check": "parent done"})],
               reflections=[light("spawn", new_goal=sub), light(), light()])
    make_kernel(tmp_path, s).run()
    updates = [(e["goal"]["id"], e["action"]) for e in events(tmp_path / "r1", "goal_update")]
    assert updates == [("g1", "activate"), ("g1", "block"), ("g2", "activate"), ("g2", "done"),
                       ("g1", "unblock"), ("g1", "done")]
    assert events(tmp_path / "r1", "goal_set")[1]["goal"]["parent"] == "g1"


def test_failures_have_rule_based_surprise(tmp_path):
    s = script([plan("fs_read", {"path": "missing.md"}, expected="the file content", confidence=0.9),
                plan("fs_read", {"path": "../outside.md"}, expected="an error: outside the workspace"),
                plan("finish", {"check": "ok"})])
    make_kernel(tmp_path, s).run()
    obs = [e["observation"] for e in events(tmp_path / "r1", "observe")]
    assert (obs[0]["error_type"], obs[0]["surprise_rule"]) == ("not_found", 0.9)
    assert (obs[1]["error_type"], obs[1]["surprise_rule"]) == ("permission", None)  # failure was expected
    deep = [e["trigger"] for e in events(tmp_path / "r1", "reflect") if e["level"] == "deep"]
    assert deep[:2] == ["surprise 0.90", "2 tool failures in a row"]


def test_notes_persist_across_runs_and_can_be_superseded(tmp_path):
    shared = tmp_path / "shared-notes.jsonl"
    first = script([plan("finish", {"check": "ok"})],
                   deep=[{"summary": "s", "notes": [{"kind": "self", "title": "No network tool here",
                                                    "text": "I cannot fetch URLs.", "confidence": 0.9}]}])
    make_kernel(tmp_path, first, notes_path=shared).run()
    second = script([plan("finish", {"check": "ok"})], deep=[{"summary": "s", "notes": [], "supersede": ["n1"]}])
    make_kernel(tmp_path, second, run_id="r2", notes_path=shared).run()
    plan_call = [e for e in events(tmp_path / "r2", "llm_call") if e["kind"] == "plan"][0]
    assert [n["title"] for n in context_of(tmp_path / "r2", plan_call)["notes"]] == ["No network tool here"]
    records = [json.loads(line) for line in shared.read_text().splitlines()]
    assert [(r["op"], r["id"]) for r in records] == [("add", "n1"), ("supersede", "n1")]
