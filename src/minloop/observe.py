"""Observe: what the model gets to see of a tool result, a normalized error type, and rule-based surprise.
The full result always goes to the trace; only this view is truncated."""
from __future__ import annotations

import re

VIEW_CHARS = 3500
KINDS = {"FileNotFoundError": "not_found", "NotADirectoryError": "not_found", "IsADirectoryError": "invalid_args",
         "PermissionError": "permission", "TimeoutError": "timeout", "ValueError": "invalid_args"}
KNOWN = {"unknown_tool", "invalid_args", "timeout", "not_found", "permission"}
EXPECTS_FAILURE = re.compile(r"error|fail|not found|denied|missing|does not exist|refus", re.I)


def view(result, plan: dict, limit: int = VIEW_CHARS) -> dict:
    text = result.output if result.ok else "\n".join(filter(None, [result.error, result.output]))
    shown = text if len(text) <= limit else text[:limit] + f"\n[... {len(text) - limit} more characters not shown]"
    kind = None
    if not result.ok:
        head = (result.error or "").split(":", 1)[0].strip()
        kind = KINDS.get(head, head if head in KNOWN else "tool_error")
    # Rule first: a failure the plan did not anticipate is as surprising as the plan was confident.
    # Anything the rule cannot judge (None) is left to the light reflection.
    surprise = plan["confidence"] if not result.ok and not EXPECTS_FAILURE.search(plan["expected"]) else None
    return {"ok": result.ok, "view": shown, "chars": len(text), "error_type": kind, "surprise_rule": surprise}
