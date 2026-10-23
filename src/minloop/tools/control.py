"""Control tools. finish / give_up close the current goal (the kernel reads data["control"]).
ask_human, propose_charter_change and idle only leave a record: in a fully autonomous run
nobody answers, and the charter stays read-only."""
from __future__ import annotations

from .base import ToolResult, tool

_TEXT = {"type": "string", "minLength": 1}


@tool("finish", "Declare the current goal done. `check` must go through each completion criterion and say "
      "what observation shows it is met.", {"properties": {"check": _TEXT}, "required": ["check"]})
def finish(args, ctx):
    return ToolResult(True, "Goal marked done.", {"control": "finish", "result": args["check"]})


@tool("give_up", "Declare the current goal cannot be achieved with the tools and information available. "
      "Say why.", {"properties": {"reason": _TEXT}, "required": ["reason"]})
def give_up(args, ctx):
    return ToolResult(True, "Goal abandoned.", {"control": "give_up", "result": args["reason"]})


@tool("ask_human", "Ask a human a question. This run is fully autonomous: nobody will answer, "
      "but the question is recorded.", {"properties": {"question": _TEXT}, "required": ["question"]})
def ask_human(args, ctx):
    ctx.trace("ask_human", question=args["question"])
    return ToolResult(True, "No human is available (fully autonomous run); the question was recorded. "
                      "Decide on your own, or give_up if you cannot proceed.", {"control": "ask_human"})


@tool("propose_charter_change", "Propose a change to the charter. The charter is read-only to you; "
      "the proposal is recorded for a human to review later.",
      {"properties": {"change": _TEXT, "reason": _TEXT}, "required": ["change", "reason"]})
def propose_charter_change(args, ctx):
    ctx.trace("charter_proposal", change=args["change"], reason=args["reason"])
    return ToolResult(True, "Proposal recorded. The charter is unchanged.", {"control": "propose"})


@tool("idle", "Record that there is nothing useful to do right now, and why.",
      {"properties": {"reason": _TEXT}, "required": ["reason"]})
def idle(args, ctx):
    ctx.trace("idle", reason=args["reason"])
    return ToolResult(True, "Idle recorded.", {"control": "idle"})
