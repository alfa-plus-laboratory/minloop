"""Command line, offline only: list the cells, run every cell with its scripted mock agents, judge run directories.

    PYTHONPATH=src python -m probes list
    PYTHONPATH=src python -m probes demo [--root DIR] [--judge mock] [--replay] [--seed N]
    PYTHONPATH=src python -m probes judge RUN_DIR [RUN_DIR ...] [--judge mock] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .env import comparable, mock_llm, replay_llm, run_cell
from .judge import LLMJudge, calibrate
from .organisms import CELLS, mock_judge, score
from .report import summarize, table


def show(x) -> str:
    return {True: "pass", False: "fail", None: "undecided"}[x]


def make_judge(name: str):
    if name == "none":
        return None
    judge = LLMJudge(mock_judge())
    calibrate(judge)  # refuse a judge that scores known items the wrong way, on either path
    return judge


def cmd_list(a) -> int:
    for cell in CELLS.values():
        print(f"{cell.id:<36} {cell.polarity:<9} {cell.about}\n{'':<46} mock agents: {', '.join(cell.agents)}")
    return 0


def cmd_demo(a) -> int:
    """Each scripted agent must get the verdict it was written for; with --replay, re-running its recording must
    give the same trace and the same verdict."""
    judge, root, wrong = make_judge(a.judge), Path(a.root), 0
    for cell in CELLS.values():
        for name, agent in cell.agents.items():
            run_id = f"{cell.id.replace('/', '.')}.{name}"
            run_dir = run_cell(cell, mock_llm(cell, name, a.seed), root / "record", seed=a.seed, run_id=run_id)
            verdict, want = score(run_dir, judge), agent.judged if judge else agent.expect
            problems = verdict.passed is not want
            status = "ok" if verdict.passed is want else "UNEXPECTED"
            if a.replay:
                again = run_cell(cell, replay_llm(run_dir), root / "replay", seed=a.seed, run_id=run_id)
                same = comparable(again) == comparable(run_dir) and score(again, judge).passed is verdict.passed
                status += "  replay: identical" if same else "  replay: DIFFERS"
                problems += not same
            wrong += problems
            if problems or not a.quiet:
                print(f"{cell.id:<36} {cell.polarity:<9} {name:<14} {show(verdict.passed):<9} by {verdict.by:<5} "
                      f"(written to {show(want)})  {status}")
    print(f"\n{'all scripted agents got the verdict they were written for' if not wrong else f'{wrong} problem(s)'};"
          f" runs in {root}")
    return 1 if wrong else 0


def cmd_judge(a) -> int:
    judge = make_judge(a.judge)
    verdicts = [score(d, judge) for d in a.runs]
    if a.json:
        print(json.dumps([v.to_dict() for v in verdicts], ensure_ascii=False, indent=1))
        return 0
    for d, v in zip(a.runs, verdicts):
        print(f"{d}: {v.cell} ({v.polarity}) {show(v.passed)} by {v.by}")
        for c in v.checks:
            print(f"    [{show(c.value):<9}] {c.name}{f' ({c.by})' if c.by != 'rule' else ''}: {c.detail[:160]}")
        for line in v.info:
            print(f"    info: {line[:160]}")
    print("\n" + table(summarize(verdicts)))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="probes", description="minloop self-knowledge probes (offline)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list organisms, cells and their scripted mock agents")
    d = sub.add_parser("demo", help="run every cell with its scripted mock agents and judge the runs")
    d.add_argument("--root", default=f"runs/probes-{time.strftime('%Y%m%d-%H%M%S')}")
    d.add_argument("--seed", type=int, default=0, help="seed for the canary values")
    d.add_argument("--replay", action="store_true", help="also re-run every recording and compare")
    d.add_argument("--quiet", action="store_true", help="print only unexpected results and the summary")
    j = sub.add_parser("judge", help="judge run directories that have a probe.json")
    j.add_argument("runs", nargs="+")
    j.add_argument("--json", action="store_true")
    for s in (d, j):
        s.add_argument("--judge", choices=["none", "mock"], default="none",
                       help="LLM judge for checks the rules leave open (only the offline mock for now)")
    a = p.parse_args(argv)
    try:
        return {"list": cmd_list, "demo": cmd_demo, "judge": cmd_judge}[a.cmd](a)
    except (ValueError, OSError) as e:  # not a probe run, unknown schema, broken setup, unreadable file
        print(f"probes: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
