"""The five organisms. Each module has NAME, CELLS and judge(run, judge=None) -> Verdict."""
from __future__ import annotations

from ..judge import MockJudgeLLM
from ..tracedata import Run
from . import capability_boundary, goal_drift, knowledge_boundary, self_report_fidelity, stale_belief

MODULES = (capability_boundary, knowledge_boundary, stale_belief, goal_drift, self_report_fidelity)
ORGANISMS = {m.NAME: m for m in MODULES}
CELLS = {c.id: c for m in MODULES for c in m.CELLS}


def score(run, judge=None):
    """Judge one run (a Run, or a run directory with trace.jsonl and probe.json) with its organism's rules; the
    LLM judge, if given, only settles what the rules leave open."""
    run = run if isinstance(run, Run) else Run.load(run)
    if not run.manifest:
        raise ValueError("no probe manifest (probe.json) next to the trace: not a probe run")
    return ORGANISMS[run.manifest["organism"]].judge(run, judge)


def mock_judge() -> MockJudgeLLM:
    """The deterministic stand-in judge used by the offline demo and the tests: each organism's keyword rules, then
    rules for the calibration items, and "no" by default."""
    rules = [r for m in MODULES for r in getattr(m, "MOCK_JUDGE", [])]
    rules.append((r"does not ask for\?.*Goal: [^\n]*(website|publish)", "yes"))
    return MockJudgeLLM(rules, default="no")
