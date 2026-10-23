"""Reflect, two levels. Light: every step, structured fields only. Deep: on triggers, and the only
place that writes long-term notes."""
from __future__ import annotations

from . import goal

LIGHT = {"type": "object", "required": ["met_expectation", "surprise", "lesson", "goal_action", "calibration"],
         "properties": {
             "met_expectation": {"type": "boolean"},
             "surprise": {"type": "number", "minimum": 0, "maximum": 1},
             "lesson": {"type": "string"},
             "goal_action": {"enum": ["keep", "revise", "spawn"]},  # done / abandon go through finish / give_up
             "new_goal": goal.ITEM,
             "calibration": {"type": "string"}}}
NOTE = {"type": "object", "required": ["kind", "title", "text", "confidence"], "properties": {
    "kind": {"enum": ["world", "skill", "self"]},
    "title": {"type": "string", "minLength": 1}, "text": {"type": "string", "minLength": 1},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "tags": {"type": "array", "items": {"type": "string"}}}}
DEEP = {"type": "object", "required": ["summary", "notes"], "properties": {
    "summary": {"type": "string"}, "notes": {"type": "array", "items": NOTE},
    "supersede": {"type": "array", "items": {"type": "string"}}}}
DEFAULTS = {"surprise": 0.7, "failures": 2, "every": 5}  # charter "reflect" overrides these


def check_light(r: dict) -> list[str]:
    if r["goal_action"] in ("revise", "spawn") and "new_goal" not in r:
        return [f"goal_action '{r['goal_action']}' needs new_goal"]
    return []


def light(ask, context: dict) -> dict | None:
    return ask("reflect", context, LIGHT, check_light)


def deep(ask, context: dict) -> dict | None:
    return ask("deep", context, DEEP)


def trigger(surprise, failures: int, closed: str | None, step: int, cfg: dict) -> str | None:
    """Why a deep reflection is due now, or None."""
    if closed:
        return f"goal {closed}"
    if surprise is not None and surprise >= cfg["surprise"]:
        return f"surprise {surprise:.2f}"
    if failures >= cfg["failures"]:
        return f"{failures} tool failures in a row"
    if cfg["every"] and step % cfg["every"] == 0:
        return f"periodic (every {cfg['every']} steps)"
    return None
