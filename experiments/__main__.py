"""Command line for the experiment harness. Offline: the only models it can drive are the mock personas.

    PYTHONPATH=src python -m experiments run [--root DIR] [--seeds 0 1 2] [--judge mock] [--replay]
                                             [--json experiments/results-mock.json] [--md experiments/RESULTS-mock.md]
    PYTHONPATH=src python -m experiments check experiments/results-mock.json    # re-run, compare every record
    PYTHONPATH=src python -m experiments rescore experiments/results-mock.json --root DIR   # re-judge, no runs
    PYTHONPATH=src python -m experiments render experiments/results-mock.json --md experiments/RESULTS-mock.md
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from probes.judge import LLMJudge, calibrate
from probes.organisms import CELLS, mock_judge

from .harness import code_digest, record, run_suite, source_version
from .personas import MOCKS, PERSONAS
from .report import comparable, dumps, render
from .subjects import SUBJECTS

REPO = Path(__file__).resolve().parent.parent


def make_judge(settings: dict):
    if settings["judge"] != "mock (calibrated)":
        return None
    judge = LLMJudge(mock_judge())
    calibrate(judge)  # refuse a judge that scores known items the wrong way, on either path
    return judge


def suite(settings: dict, root) -> dict:
    judge = make_judge(settings)
    cells = [c for c in CELLS.values() if not settings["cells"] or c.id in settings["cells"]
             or c.organism in settings["cells"]]
    return run_suite(root, MOCKS, subjects=settings["subjects"], personas=settings["personas"],
                     seeds=settings["seeds"], judge=judge, replay=settings["replay"], cells=cells,
                     intended=lambda persona: "pass" if persona == "honest" else "fail")


def write(results: dict, json_path, md_path):
    text = dumps(results)
    if json_path:
        Path(json_path).write_text(text, encoding="utf-8")
    if md_path:
        Path(md_path).write_text(render(results, Path(json_path).name if json_path else "results.json", text),
                                 encoding="utf-8")


def command(a) -> str:
    """The run command as it can be repeated from the repository root, without machine-specific paths."""
    parts = ["PYTHONPATH=src python -m experiments run", "--seeds", *map(str, a.seeds), "--judge", a.judge]
    parts += ["--replay"] if a.replay else []
    parts += ["--subjects", *a.subjects] if a.subjects != list(SUBJECTS) else []
    parts += ["--personas", *a.personas] if a.personas != list(PERSONAS) else []
    parts += ["--cells", *a.cells] if a.cells else []
    for flag, path in (("--json", a.json), ("--md", a.md)):
        if path:
            parts += [flag, f"experiments/{Path(path).name}" if Path(path).is_absolute() else path]
    return " ".join(parts)


def cmd_run(a) -> int:
    unknown = [c for c in a.cells if c not in CELLS and c not in {x.organism for x in CELLS.values()}]
    if unknown:
        raise SystemExit(f"unknown cell or organism: {', '.join(unknown)}")
    settings = {"subjects": a.subjects, "personas": a.personas, "seeds": a.seeds, "cells": a.cells,
                "judge": "mock (calibrated)" if a.judge == "mock" else "none (rules only)", "replay": a.replay}
    source, digest = source_version(REPO), code_digest(REPO)  # before anything is written
    root = Path(a.root or f"runs/experiments-{time.strftime('%Y%m%d-%H%M%S')}")
    out = suite(settings, root)
    results = {"label": "mock data, not a model experiment", "kind": "mock", "date": time.strftime("%Y-%m-%d"),
               "source": source, "code_sha256": digest, "command": command(a), "settings": settings, **out}
    write(results, a.json, a.md)
    runs = out["runs"]
    healthy = all(r["verdict"] == r["intended"] and r["stop"] == "done" and r.get("replay", "identical") == "identical"
                  for r in runs)
    print(f"{len(runs)} runs, {len(out['not_applicable'])} not applicable; "
          f"{'every run got its intended verdict' if healthy else 'SOME RUNS DID NOT'}; runs in {root}")
    return 0 if healthy else 1


def cmd_check(a) -> int:
    recorded = json.loads(Path(a.results).read_text(encoding="utf-8"))
    Path(a.root).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="check-", dir=a.root) as tmp:
        fresh = suite(recorded["settings"], tmp)
    if comparable(fresh) == comparable(recorded):
        print(f"{a.results}: all {len(fresh['runs'])} run records reproduced exactly")
        return 0
    old = {(r["subject"], r["cell"], r["persona"], r["seed"]): r for r in recorded["runs"]}
    for r in fresh["runs"]:
        key = (r["subject"], r["cell"], r["persona"], r["seed"])
        if old.get(key) != r:
            print(f"differs: {key}\n  recorded: {old.get(key)}\n  now:      {r}")
    if fresh["not_applicable"] != recorded["not_applicable"]:
        print("the not-applicable list differs")
    return 1


def cmd_rescore(a) -> int:
    """Score the recorded run directories again, without running anything: what verifies a real experiment, whose
    model calls cannot be repeated exactly but whose traces can be re-judged."""
    recorded, root = json.loads(Path(a.results).read_text(encoding="utf-8")), Path(a.root)
    judge, differs = make_judge(recorded["settings"]), 0
    for r in recorded["runs"]:
        now = record(root / r["dir"], root, judge)
        changed = {k: (r[k], v) for k, v in now.items() if r.get(k) != v}
        if changed:
            differs += 1
            print(f"differs: {r['dir']}: {changed}")
    total = len(recorded["runs"])
    print(f"{a.results}: {total - differs} of {total} run records re-scored identically")
    return 1 if differs else 0


def cmd_render(a) -> int:
    text = Path(a.results).read_text(encoding="utf-8")
    Path(a.md).write_text(render(json.loads(text), Path(a.results).name, text), encoding="utf-8")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="experiments", description="probe suite against minloop and two baselines "
                                                                "(offline, mock personas only)")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the suite with the mock personas")
    r.add_argument("--root", help="where the runs go (default runs/experiments-<time>)")
    r.add_argument("--subjects", nargs="+", choices=list(SUBJECTS), default=list(SUBJECTS))
    r.add_argument("--personas", nargs="+", choices=PERSONAS, default=list(PERSONAS))
    r.add_argument("--seeds", nargs="+", type=int, default=[0])
    r.add_argument("--cells", nargs="+", default=[], help="cell ids or organism names (default: all)")
    r.add_argument("--judge", choices=["none", "mock"], default="mock")
    r.add_argument("--replay", action="store_true", help="re-run every recording and compare")
    r.add_argument("--json", help="write the results JSON here")
    r.add_argument("--md", help="write the Markdown report here")
    c = sub.add_parser("check", help="re-run a results file's suite and compare every run record")
    c.add_argument("results")
    c.add_argument("--root", default="runs", help="parent directory for the temporary runs (default runs/)")
    s = sub.add_parser("rescore", help="re-judge the recorded run directories of a results file (no new runs)")
    s.add_argument("results")
    s.add_argument("--root", required=True, help="the --root the suite was run with")
    d = sub.add_parser("render", help="render the Markdown report of a results file")
    d.add_argument("results")
    d.add_argument("--md", required=True)
    a = p.parse_args(argv)
    return {"run": cmd_run, "check": cmd_check, "rescore": cmd_rescore, "render": cmd_render}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
