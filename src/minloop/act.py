"""Act: whitelist check, argument validation, timeout, call. No LLM, no decisions."""
from __future__ import annotations

import threading

from .schema import errors
from .tools.base import ToolResult


def run(tools: dict, name: str, args: dict, ctx) -> ToolResult:
    tool = tools.get(name)
    if tool is None:
        return ToolResult(False, error=f"unknown_tool: '{name}' is not available; tools: {', '.join(tools)}")
    problems = errors(args, tool.params)
    if problems:
        return ToolResult(False, error="invalid_args: " + "; ".join(problems))
    box = {}

    def call():
        try:
            box["result"] = tool.fn(args, ctx)
        except Exception as e:  # keep absolute paths out of the trace
            box["result"] = ToolResult(False, error=f"{type(e).__name__}: {e}".replace(str(ctx.workspace), "."))

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(tool.timeout)
    if worker.is_alive():  # the thread cannot be killed; it is abandoned and the step moves on
        return ToolResult(False, error=f"timeout: '{name}' took longer than {tool.timeout}s")
    return box["result"]
