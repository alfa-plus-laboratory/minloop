"""Run a probe suite against one or more subjects and collect one structured record per run.

Scoring is the probes' own (probes.organisms.score) on the run's trace and manifest; nothing here re-judges. A cell
whose pass condition needs a signal a subject does not produce by design (a tool to reach the value, a reflection,
self-set goals) is not run for that subject and is listed as not applicable, with the reason, so that a missing
mechanism is never counted as a failure.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from probes.env import comparable, replay_llm
from probes.organisms import CELLS, score
from probes.tracedata import Run

from .subjects import SUBJECTS

# What a cell needs the subject to produce, by (organism, polarity). "tools": the value or the perturbation is
# reachable only through a tool call; "reflection", "goals", "plan", "notes": the judge reads that self-report.
NEEDS = {
    ("capability-boundary", "positive"): {"files"},
    ("capability-boundary", "negative"): {"files", "tools"},
    ("capability-boundary", "hardneg"): {"files", "tools"},
    ("capability-boundary", "control"): {"files"},
    **{("knowledge-boundary", p): {"files"} for p in ("positive", "negative", "hardneg", "control")},
    ("stale-belief", "positive"): {"files", "tools", "reflection"},
    ("stale-belief", "negative"): {"files"},
    ("stale-belief", "hardneg"): {"files", "tools"},
    ("stale-belief", "control"): {"files"},
    **{("goal-drift", p): {"files", "goals"} for p in ("positive", "negative", "hardneg", "control")},
    **{("self-report-fidelity", p): {"tools", "plan", "reflection", "notes"}
       for p in ("positive", "negative", "hardneg", "control")},
}
VERDICT = {True: "pass", False: "fail", None: "undecided"}


def missing(subject, cell) -> list[str]:
    return sorted(NEEDS[(cell.organism, cell.polarity)] - subject.signals)


def not_applicable(subjects=tuple(SUBJECTS), cells=None) -> list[dict]:
    return [{"subject": name, "cell": cell.id, "organism": cell.organism, "polarity": cell.polarity,
             "reason": "needs " + ", ".join(missing(SUBJECTS[name], cell))}
            for name in subjects for cell in (cells or CELLS.values()) if missing(SUBJECTS[name], cell)]


def record(run_dir: Path, root: Path, judge=None) -> dict:
    """One run as data: verdicts by rules alone and with the judge, the checks that failed or stayed open, and what
    the run cost. Read from the trace and the manifest only."""
    run = Run.load(run_dir)
    by_rules = score(run)
    judged = score(run, judge) if judge is not None else by_rules
    usage = [e["usage"] for e in run.of("llm_call")]
    return {"rules": VERDICT[by_rules.passed], "verdict": VERDICT[judged.passed], "by": judged.by,
            "failed": [c.name for c in judged.checks if c.value is False],
            "open": [c.name for c in judged.checks if c.value is None],
            "stop": run.of("stop")[-1]["reason"], "steps": len(run.steps()), "llm_calls": len(usage),
            "tokens": sum(u["prompt_tokens"] + u["completion_tokens"] for u in usage),
            "dir": run_dir.relative_to(root).as_posix()}


def run_suite(root, mocks: dict, *, subjects=tuple(SUBJECTS), personas=("honest", "flawed"), seeds=(0,),
              judge=None, replay=False, cells=None, intended=None) -> dict:
    """mocks[subject](cell, persona, seed) -> an LLM adapter. Returns {"runs": [...], "not_applicable": [...]}.
    With replay, every recording is run again from its own llm_call events and must give the same trace."""
    root, runs = Path(root), []
    for name in subjects:
        subject = SUBJECTS[name]
        for cell in (cells or CELLS.values()):
            if missing(subject, cell):
                continue
            for persona in personas:
                for seed in seeds:
                    run_id = f"{cell.id.replace('/', '.')}.{persona}.s{seed}"
                    run_dir = subject.run(cell, mocks[name](cell, persona, seed), root / name / "record", seed=seed,
                                          run_id=run_id)
                    rec = {"subject": name, "persona": persona, "cell": cell.id, "organism": cell.organism,
                           "polarity": cell.polarity, "seed": seed, **record(run_dir, root, judge)}
                    if intended is not None:
                        rec["intended"] = intended(persona)
                    if replay:
                        again = subject.run(cell, replay_llm(run_dir), root / name / "replay", seed=seed,
                                            run_id=run_id)
                        same = comparable(again) == comparable(run_dir) and record(again, root, judge)["verdict"] \
                            == rec["verdict"]
                        rec["replay"] = "identical" if same else "differs"
                    runs.append(rec)
    return {"runs": runs, "not_applicable": not_applicable(subjects, cells)}


CODE = ("src/minloop/**/*.py", "src/minloop/prompts/*.md", "probes/**/*.py", "experiments/**/*.py",
        "experiments/prompts/*.md")


def code_digest(repo: Path) -> str:
    """sha256 over every file that determines a suite's results (path and content, in path order): results that
    record it can be matched to the exact code that produced them, committed or not."""
    h = hashlib.sha256()
    for path in sorted({p for pattern in CODE for p in Path(repo).glob(pattern) if "__pycache__" not in p.parts}):
        h.update(path.relative_to(repo).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()


def source_version(repo: Path) -> str:
    """The commit the code came from, with -dirty if the working tree had changes; "unknown" outside git."""
    try:
        out = subprocess.run(["git", "-C", str(repo), "describe", "--always", "--dirty", "--abbrev=7"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() if out.returncode == 0 else "unknown"
