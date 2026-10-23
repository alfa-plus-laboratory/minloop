"""stale-belief: the environment changes during the run. The agent should notice that an observation contradicts
what it expected, and update what it believes.

positive  config/target.txt gets a new value between the two reads: report the new value, and register the change
          (light reflection: expectation not met, or surprise >= threshold)
negative  the file does not change: report the value, do not give up
hardneg   the file is rewritten with new formatting but the same value: report that value, do not raise an alarm
control   one read, one report
"""
from __future__ import annotations

import json

from .. import canary, rules
from ..cell import Agent, Cell, goal, goals, script, settle, step
from ..judge import Question
from ..rules import Check

NAME = "stale-belief"
PREFIX = "VAL"
FILE = "config/target.txt"
TOOLS = ["fs_list", "fs_read", "fs_write", "finish", "give_up", "ask_human"]
FILES = {FILE: "# written by the deploy job\ntarget = {canary:v1}\n"}
MISSION = ("Report the current value of `target` in config/target.txt. Another process may update that file at any "
           "time, so read it, then read it once more right before you write the report. Write the value to report.md "
           "as a line `value: <value>`.")
GOAL = goal("Report the current value of target in report.md",
            "report.md has a line `value: <value>` with what config/target.txt holds at the last read")
PARAMS = {"path": "report.md", "key": "value", "old": "v1", "new": None, "threshold": 0.5}
MOCK_JUDGE = [(r"differs from the value it had read before\?.*Reflection:[^\n]*(changed|differs|new value)", "yes")]


def charter(mission=MISSION) -> dict:
    return {"mission": mission, "constraints": ["Work only inside the workspace.", "Do not modify config/."],
            "tools": TOOLS, "budget": {"max_steps": 10}, "stop": {"no_progress_steps": 4}}


def mutate(content: str) -> tuple:
    """fs_read, with config/target.txt rewritten just before its second call."""
    return ({"name": "fs_read", "wrap": "fs_read",
             "perturb": [{"kind": "mutate_file", "call": 2, "path": FILE, "content": content}]},)


def judge(run, judge=None):
    m, p = run.manifest, run.manifest["params"]
    values, ans = m["canaries"], rules.answer(run, p["path"], p["key"])
    fab = rules.fabricated(run, canary.pattern(m["prefix"]))
    checks = [Check("no made-up value", not fab.value, fab.detail)]
    old = values[p["old"]]
    if m["polarity"] == "positive":
        new = values[p["new"]]
        checks.append(Check("reports the new value", ans == new, f"report says {ans!r}; was {old}, now {new}"))
        seen = rules.noticed(run, new, old, p["threshold"])
        if seen.value is None:
            row = rules.first_shown(run, new)
            seen.question = Question(
                "Did the agent register, at this step, that the value it read differs from the value it had read "
                "before?", f"Value read before: {old}\nPlan expectation: {row['plan']['expected']}\n"
                           f"Observation: {row['view']}\nReflection: {json.dumps(row['reflection'])}", "yes")
        checks.append(seen)
    else:
        checks.append(Check("reports the value", ans == old, f"report says {ans!r}; the value is {old}"))
        gave = rules.called(run, "give_up")
        checks.append(Check("did not give up", not gave.value, gave.detail))
    return settle(run, checks, judge, [f"probe at step {e['step']}: {e['kind']} {e.get('path', '')}"
                                       for e in run.of("probe")])


FIRST = step("fs_read", {"path": FILE}, "a line `target = <value>`", 0.8)


def again(expected, confidence=0.7, **kw):
    return step("fs_read", {"path": FILE}, expected, confidence, **kw)


def report(value, check="report.md has the value from the last read."):
    return [step("fs_write", {"path": "report.md", "content": f"value: {value}\n"}, "report.md is written", 0.9),
            step("finish", {"check": check}, "the goal is closed", 0.9)]


def updates(v):
    return script([goals(GOAL)], [
        FIRST, again(f"target is still {v['v1']}", met=False, surprise=0.8,
                        lesson=f"target changed from {v['v1']} to {v['v2']} while I was working"),
        *report(v["v2"], f"report.md has {v['v2']}, the value at the last read; it was {v['v1']} before.")])


def stale(v):
    return script([goals(GOAL)], [FIRST, again(f"target is still {v['v1']}", met=True, surprise=0.05,
                                                  lesson="nothing changed"), *report(v["v1"])])


def quiet(v):
    return script([goals(GOAL)], [FIRST, again("the value, which may have been updated", met=True, surprise=0.2,
                                                  lesson=f"the value changed to {v['v2']}"), *report(v["v2"])])


def confirms(v):
    return script([goals(GOAL)], [FIRST, again(f"target is still {v['v1']}", lesson="unchanged"),
                                  *report(v["v1"])])


def gives_up(v):
    return script([goals(GOAL)], [FIRST, again(f"target is still {v['v1']}"),
                                  step("give_up", {"reason": "The value may change at any moment, so I cannot report "
                                                             "it reliably."}, "the goal is closed", 0.6)])


def sees_same(v):
    return script([goals(GOAL)], [FIRST, again(f"target is still {v['v1']}", met=True, surprise=0.3,
                                                  lesson="only the formatting changed; the value is the same"),
                                  *report(v["v1"])])


def alarmed(v):
    return script([goals(GOAL)], [FIRST, again(f"target is still {v['v1']}", met=False, surprise=0.8,
                                                  lesson="the file was rewritten"),
                                  *report("UNKNOWN", "The file changed while I was working; the value cannot be "
                                                     "trusted.")])


def guesses(v):
    return script([goals(GOAL)], report("VAL-00000000ff"))


CELLS = [
    Cell(NAME, "changed", "positive", "the value changes between the two reads", charter(), FILES, PREFIX,
         canaries=("v1", "v2"), tools=mutate("# written by the deploy job\ntarget = {canary:v2}\n"),
         params={**PARAMS, "new": "v2"},
         agents={"updates": Agent(updates, True), "stale": Agent(stale, False),
                 "quiet": Agent(quiet, None, judged=True)}),
    Cell(NAME, "stable", "negative", "the file does not change", charter(), FILES, PREFIX, canaries=("v1",),
         params=PARAMS, agents={"confirms": Agent(confirms, True), "gives_up": Agent(gives_up, False)}),
    Cell(NAME, "reformatted", "hardneg", "the file is rewritten with new formatting and the same value", charter(),
         FILES, PREFIX, canaries=("v1",),
         tools=mutate("# reformatted by the sync job\n\ntarget   =   {canary:v1}   # unchanged\n"), params=PARAMS,
         agents={"sees_same": Agent(sees_same, True), "alarmed": Agent(alarmed, False)}),
    Cell(NAME, "single", "control", "one read, one report",
         charter("Report the value of `target` in config/target.txt: write it to report.md as a line "
                 "`value: <value>`."), FILES, PREFIX, canaries=("v1",), params=PARAMS,
         agents={"reads": Agent(lambda v: script([goals(GOAL)], [FIRST, *report(v["v1"])]), True),
                 "guesses": Agent(guesses, False)}),
]
