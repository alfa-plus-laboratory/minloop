import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from minloop import trace  # noqa: E402
from minloop.__main__ import read_workspace  # noqa: E402
from minloop.kernel import Kernel  # noqa: E402
from minloop.llm import MockLLM  # noqa: E402

HELLO = ROOT / "examples" / "hello"


def pytest_configure(config):
    # pytest creates --basetemp (runs/pytest, see pyproject.toml) without its parents; runs/ is git-ignored, so a
    # fresh clone does not have it
    if config.option.basetemp:
        Path(config.option.basetemp).resolve().parent.mkdir(parents=True, exist_ok=True)


def hello_charter(**changes) -> dict:
    charter = json.loads((HELLO / "charter.json").read_text(encoding="utf-8"))
    charter.update(changes)
    return charter


def hello_script() -> dict:
    return copy.deepcopy(json.loads((HELLO / "mock_script.json").read_text(encoding="utf-8")))


def hello_files() -> dict:
    return read_workspace(HELLO / "charter.json", hello_charter())


def make_kernel(tmp_path, script=None, charter=None, run_id="r1", **options) -> Kernel:
    return Kernel(charter or hello_charter(), MockLLM(script or hello_script()), tmp_path / run_id,
                  run_id=run_id, files=hello_files(), **options)


def events(run_dir, type_=None) -> list:
    lines = trace.lines(Path(run_dir) / "trace.jsonl")
    out = [json.loads(line) for line in lines]
    return [e for e in out if type_ is None or e["type"] == type_]


def request(run_dir, event) -> list:
    """The messages of an llm_call event, resolving an artifact reference if needed."""
    return json.loads(trace.text(event["request"], run_dir))


def context_of(run_dir, event) -> dict:
    """The working-memory context (user message) the kernel sent in an llm_call."""
    return json.loads(request(run_dir, event)[1]["content"])


def plan(tool, args, expected="it works", confidence=0.8):
    return {"intent": f"call {tool}", "tool": tool, "args": args, "expected": expected,
            "confidence": confidence, "unknowns": []}


def light(action="keep", **extra):
    return {"met_expectation": True, "surprise": 0.1, "lesson": "", "goal_action": action,
            "calibration": "ok", **extra}


ONE_GOAL = {"goals": [{"text": "do the thing", "criteria": ["the thing is done"]}]}
DONE = {"goals": [], "mission_complete": True, "reason": "all done"}
NOTHING = {"summary": "nothing new", "notes": []}
