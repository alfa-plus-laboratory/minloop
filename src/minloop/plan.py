"""Plan: one rolling step at a time. The PlanStep carries the self-report that P3 measures:
what the agent expects to observe, how confident it is, and what it knows it does not know."""
from __future__ import annotations

SCHEMA = {"type": "object", "required": ["intent", "tool", "args", "expected", "confidence", "unknowns"],
          "properties": {
              "intent": {"type": "string", "minLength": 1},
              "tool": {"type": "string", "minLength": 1},
              "args": {"type": "object"},
              "expected": {"type": "string", "minLength": 1},
              "confidence": {"type": "number", "minimum": 0, "maximum": 1},
              "unknowns": {"type": "array", "items": {"type": "string"}},
              "recall": {"type": "array", "items": {"type": "string"}}}}  # note ids to show in full next step


def make(ask, context: dict) -> dict | None:
    return ask("plan", context, SCHEMA)
