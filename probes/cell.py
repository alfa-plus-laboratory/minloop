"""Cells and verdicts, and the few helpers the scripted mock agents are written with.

A cell is one situation of one organism, with a polarity. It is plain data (charter, workspace files, probe tool
specs, canary labels, judge parameters) plus the scripted mock agents that exercise its judge offline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import canary
from .rules import Check

POLARITIES = ("positive", "negative", "hardneg", "control")
MANIFEST_VERSION = 1


@dataclass
class Agent:
    """A scripted mock agent: script(canary values) -> MockLLM script, and the verdict it must get with rules only
    (`expect`) and with the mock judge (`judged`, defaults to `expect`)."""
    script: Callable[[dict], dict]
    expect: bool | None
    judged: object = "same"

    def __post_init__(self):
        if self.judged == "same":
            self.judged = self.expect


@dataclass
class Cell:
    organism: str
    name: str
    polarity: str
    about: str
    charter: dict
    files: dict
    prefix: str = "X"  # canaries and their look-alikes are PREFIX-<10 hex>
    canaries: tuple = ()  # labels of canaries planted where the agent can reach them
    hidden: tuple = ()  # labels of canaries that exist nowhere the agent can reach
    tools: tuple = ()  # probe tool specs, see env.build_tool
    params: dict = field(default_factory=dict)
    agents: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.polarity not in POLARITIES:
            raise ValueError(f"{self.id}: unknown polarity {self.polarity!r}")

    @property
    def id(self) -> str:
        return f"{self.organism}/{self.name}"

    def values(self, seed: int) -> dict[str, str]:
        return {label: canary.make(seed, self.id, label, self.prefix) for label in (*self.canaries, *self.hidden)}

    def manifest(self, seed: int) -> dict:
        """Everything the judge needs besides the trace; written as probe.json next to it."""
        return {"manifest_version": MANIFEST_VERSION, "organism": self.organism, "cell": self.name,
                "polarity": self.polarity, "seed": seed, "prefix": self.prefix, "canaries": self.values(seed),
                "hidden": list(self.hidden), "tools": canary.render(list(self.tools), self.values(seed)),
                "params": self.params}


@dataclass
class Verdict:
    cell: str
    polarity: str
    passed: bool | None  # None: undecided (rules could not decide and no judge settled it)
    by: str  # "rule", "judge", or "-" when undecided
    checks: list[Check]
    info: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"cell": self.cell, "polarity": self.polarity, "passed": self.passed, "by": self.by,
                "checks": [c.to_dict() for c in self.checks], "info": self.info}


def settle(run, checks: list[Check], judge=None, info=()) -> Verdict:
    """Rules first. Every check must hold; a failed one decides at once and no judge is asked. Otherwise each
    undecided check that carries a question goes to the LLM judge, if one is given."""
    if judge is not None:
        for c in checks:
            if any(x.value is False for x in checks):
                break
            if c.value is None and c.question is not None:
                j = judge.ask(c.question)
                c.value, c.by = j.passed, "judge"
                c.detail = " | ".join(filter(None, [c.detail, f"judge: {j.answer or 'unusable reply'} ({j.reason})"]))
    values = [c.value for c in checks]
    passed = False if False in values else None if None in values else True
    by = "-" if passed is None else "judge" if any(c.by == "judge" for c in checks) else "rule"
    m = run.manifest
    return Verdict(f"{m['organism']}/{m['cell']}", m["polarity"], passed, by, checks, list(info))


# Scripted mock agents ------------------------------------------------------------------------------------------

DONE = {"goals": [], "mission_complete": True, "reason": "every goal is closed; the mission is accomplished"}
NOTHING = {"summary": "nothing new to keep", "notes": []}


def goal(text: str, *criteria: str) -> dict:
    return {"text": text, "criteria": list(criteria)}


def goals(*items: dict) -> dict:
    return {"goals": list(items), "mission_complete": False, "reason": ""}


def step(tool: str, args: dict, expected: str, confidence: float = 0.8, met: bool = True, surprise: float = 0.1,
         lesson: str = "") -> tuple[dict, dict]:
    """One step: the plan the agent makes and its light reflection afterwards."""
    plan = {"intent": f"use {tool}", "tool": tool, "args": args, "expected": expected, "confidence": confidence,
            "unknowns": [], "recall": []}
    return plan, {"met_expectation": met, "surprise": surprise, "lesson": lesson, "goal_action": "keep",
                  "calibration": "as stated"}


def note(kind: str, text: str, confidence: float = 0.8) -> dict:
    return {"kind": kind, "title": text[:60], "text": text, "confidence": confidence, "tags": []}


def script(rounds: list[dict], steps: list[tuple[dict, dict]], notes: list[dict] = ()) -> dict:
    """A MockLLM script: goal rounds in order (then "mission complete"), one (plan, reflection) per step, and deep
    reflections, the first of which writes `notes`."""
    deep = [{"summary": "what this run taught me", "notes": list(notes)}] if notes else []
    return {"goal": [*rounds, DONE], "plan": [p for p, _ in steps], "reflect": [r for _, r in steps],
            "deep": deep + [NOTHING] * 30}
