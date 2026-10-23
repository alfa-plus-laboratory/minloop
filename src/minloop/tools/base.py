"""Tool interface: a plain function plus metadata, registered by name."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    data: dict | None = None
    error: str | None = None  # "kind: message"; observe normalizes the kind
    cost: float = 0.0


@dataclass
class ToolContext:
    """Everything a tool gets: its sandbox directory, the run id and a trace handle. No kernel internals."""
    workspace: Path
    run_id: str
    trace: Callable[..., dict]  # trace(event_type, **payload)


@dataclass
class Tool:
    name: str
    description: str
    params: dict  # JSON Schema for the arguments
    fn: Callable[[dict, ToolContext], ToolResult]
    side_effect: str = "none"  # none / local / external
    timeout: float = 10.0  # seconds
    requires: str | None = None  # charter "allow" flag that must be true, e.g. "network" or "shell"


REGISTRY: dict[str, Tool] = {}
DEFAULT_TOOLS = ["fs_list", "fs_read", "fs_write", "finish", "give_up", "ask_human", "propose_charter_change"]


def tool(name: str, description: str, params: dict, **meta):
    """Decorator: register fn(args, ctx) -> ToolResult as a tool."""
    def register(fn):
        REGISTRY[name] = Tool(name, description, {"type": "object", "additionalProperties": False, **params},
                              fn, **meta)
        return fn
    return register


def describe(tools: dict[str, Tool]) -> str:
    """The tool list as the model sees it."""
    lines = []
    for t in tools.values():
        required = t.params.get("required", [])
        sig = ", ".join(f"{k}{'' if k in required else '?'}: {v.get('type', '|'.join(map(str, v.get('enum', []))))}"
                        for k, v in t.params.get("properties", {}).items())
        lines.append(f"- {t.name}({sig}): {t.description}")
    return "\n".join(lines)
