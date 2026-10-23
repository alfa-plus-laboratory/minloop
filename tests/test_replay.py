"""Acceptance 3: deterministic replay; plus divergence detection and counterfactual replay."""
import shutil

from conftest import DONE, NOTHING, ROOT, events, hello_charter, hello_files, light, make_kernel, plan
from minloop import trace
from minloop.__main__ import main
from minloop.kernel import PROMPTS, Kernel
from minloop.llm import MockLLM, ReplayLLM

RECORDED = ROOT / "examples" / "recorded" / "hello" / "trace.jsonl"


def rerun(trace_path, root, *extra):
    assert main(["replay", str(trace_path), "--rerun", "--root", str(root), "--quiet", *extra]) == 0


def test_two_replays_match_byte_for_byte(tmp_path):
    make_kernel(tmp_path / "orig", run_id="x").run()
    original = tmp_path / "orig" / "x" / "trace.jsonl"
    rerun(original, tmp_path / "a")
    rerun(original, tmp_path / "a2")
    first, second = (trace.normalized(tmp_path / d / "x" / "trace.jsonl") for d in ("a", "a2"))
    assert first == second
    # Against the recording itself only run_start differs (it names the replay adapter).
    assert first.splitlines()[1:] == trace.normalized(original).splitlines()[1:]
    artifacts = sorted(p.name for p in (tmp_path / "a" / "x" / "artifacts").iterdir())
    assert artifacts == sorted(p.name for p in (tmp_path / "orig" / "x" / "artifacts").iterdir())
    assert (tmp_path / "a" / "x" / "workspace" / "summary.md").read_bytes() == \
        (tmp_path / "orig" / "x" / "workspace" / "summary.md").read_bytes()


def test_bundled_recording_replays_exactly(tmp_path):
    rerun(RECORDED, tmp_path)
    assert trace.normalized(tmp_path / "hello" / "trace.jsonl").splitlines()[1:] == \
        trace.normalized(RECORDED).splitlines()[1:]


def test_changed_prompt_is_detected(tmp_path):
    prompts = tmp_path / "prompts"
    shutil.copytree(PROMPTS, prompts)
    (prompts / "plan.md").write_text((prompts / "plan.md").read_text() + "\nBe brief.\n")
    assert main(["replay", str(RECORDED), "--rerun", "--root", str(tmp_path / "r"), "--quiet",
                 "--prompts", str(prompts)]) == 1
    stop = events(tmp_path / "r" / "hello", "stop")[0]
    assert stop["reason"] == "error" and "ReplayDivergence" in stop["detail"]


def test_counterfactual_replay_from_step(tmp_path):
    then = MockLLM({"plan": [plan("give_up", {"reason": "pretend the notes are unreadable"})],
                    "reflect": [light()], "deep": [NOTHING], "goal": [DONE]})
    llm = ReplayLLM(RECORDED, strict=False, from_step=3, then=then)
    result = Kernel(hello_charter(), llm, tmp_path / "cf", files=hello_files()).run()
    assert result["reason"] == "done" and result["steps"] == 3
    tools = [e["tool"] for e in events(tmp_path / "cf", "tool_call")]
    assert tools == ["fs_list", "fs_read", "give_up"]  # steps 1-2 recorded, step 3 from the new model
    assert events(tmp_path / "cf", "run_start")[0]["llm"]["from_step"] == 3
