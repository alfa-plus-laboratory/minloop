"""The harness: builds a cell's environment and runs the released kernel in it, unchanged.

Every perturbation lives in the environment. Canaries are rendered into the workspace and into fixture tools; a probe
tool wraps a registered tool (or serves fixed pages, with no network) and can fail chosen calls, rewrite one result,
edit a workspace file just before a call, or stop working after N calls. Each perturbation that fires records a
`probe` event through the tool context, so the trace shows when and how the environment changed.

This module and the command line are the only parts of probes/ that import minloop, and they use its public surface
only: Kernel, the LLM adapters, the tool registry and the trace reader.
"""
from __future__ import annotations

import dataclasses
import json
from contextlib import contextmanager
from pathlib import Path

from minloop import trace
from minloop.kernel import Kernel
from minloop.llm import MockLLM, ReplayLLM
from minloop.tools import REGISTRY, Tool, ToolResult

from . import canary
from .tracedata import norm


def fixture(name: str, description: str, pages: dict[str, str], param: str = "url"):
    """A tool that answers from fixed pages, keyed by its one argument. Nothing leaves the machine."""
    def serve(args, ctx):
        if args[param] not in pages:
            return ToolResult(False, error=f"not_found: nothing at '{args[param]}'")
        return ToolResult(True, pages[args[param]])
    params = {"type": "object", "additionalProperties": False, "required": [param],
              "properties": {param: {"type": "string", "minLength": 1}}}
    return Tool(name, description, params, serve)


def build_tool(spec: dict) -> Tool:
    """A probe tool from a canary-rendered spec: {"name", "wrap": <registered tool name>} or
    {"name", "description", "fixture": {key: text}, "param"?}, plus an optional "perturb" list of
    {"kind": "fail", "calls": [n, ...], "error"} | {"kind": "remove", "after": n}
    | {"kind": "mutate_file", "call": n, "path", "content"} | {"kind": "rewrite", "call": n, "old", "new"}.
    Calls are counted per tool from 1."""
    name = spec["name"]
    base = REGISTRY[spec["wrap"]] if "wrap" in spec else fixture(name, spec["description"], spec["fixture"],
                                                                spec.get("param", "url"))
    perturb, count = spec.get("perturb", []), [0]

    def run(args, ctx):
        count[0] += 1
        n = count[0]

        def record(p, **detail):
            ctx.trace("probe", kind=p["kind"], tool=name, call=n, **detail)
        for p in perturb:
            if p["kind"] == "remove" and n > p["after"]:
                record(p)
                return ToolResult(False, error=f"unknown_tool: '{name}' is no longer available")
            if p["kind"] == "fail" and n in p["calls"]:
                record(p, error=p["error"])
                return ToolResult(False, error=p["error"])
            if p["kind"] == "mutate_file" and n == p["call"]:
                path = norm(p["path"])
                if path.startswith("..") or Path(path).is_absolute():
                    raise ValueError(f"probe path '{path}' must stay inside the workspace")
                (ctx.workspace / path).parent.mkdir(parents=True, exist_ok=True)
                (ctx.workspace / path).write_text(p["content"], encoding="utf-8", newline="\n")
                record(p, path=path, content=p["content"])
        result = base.fn(args, ctx)
        for p in perturb:
            if p["kind"] == "rewrite" and n == p["call"] and p["old"] in result.output:
                result = dataclasses.replace(result, output=result.output.replace(p["old"], p["new"]))
                record(p, old=p["old"], new=p["new"])
        return result
    return dataclasses.replace(base, name=name, fn=run)


@contextmanager
def installed(tools: dict[str, Tool]):
    """Put probe tools into the registry while a kernel is built (that is where it picks its tools), then restore
    the registry exactly."""
    saved = {name: REGISTRY.get(name) for name in tools}
    REGISTRY.update(tools)
    try:
        yield
    finally:
        for name, old in saved.items():
            if old is None:
                REGISTRY.pop(name, None)
            else:
                REGISTRY[name] = old


def prepare(cell, seed: int = 0) -> tuple[dict, dict, dict]:
    """A cell's environment for one seed: the charter and the workspace files with their canaries filled in, and
    the probe tools by name. Anything that runs a cell (the kernel here, other agents elsewhere) starts from this."""
    values = cell.values(seed)
    tools = {spec["name"]: build_tool(spec) for spec in canary.render(list(cell.tools), values)}
    return canary.render(cell.charter, values), canary.render(cell.files, values), tools


def write_manifest(cell, seed: int, run_dir) -> None:
    (Path(run_dir) / "probe.json").write_text(json.dumps(cell.manifest(seed), ensure_ascii=False, indent=1) + "\n",
                                              encoding="utf-8")


def run_cell(cell, llm, root, *, seed: int = 0, run_id: str | None = None, on_event=()) -> Path:
    """Run one cell with any LLM adapter; returns the run directory (trace.jsonl, probe.json, workspace/, ...)."""
    charter, files, tools = prepare(cell, seed)
    run_dir = Path(root) / (run_id or cell.id.replace("/", "."))
    with installed(tools):
        kernel = Kernel(charter, llm, run_dir, run_id=run_dir.name, files=files, on_event=list(on_event))
    kernel.run()
    write_manifest(cell, seed, run_dir)
    return run_dir


def mock_llm(cell, agent: str, seed: int = 0) -> MockLLM:
    """The scripted mock agent `agent` of the cell, as a MockLLM."""
    return MockLLM(cell.agents[agent].script(cell.values(seed)))


def replay_llm(run_dir) -> ReplayLLM:
    """Feeds back a recorded run's LLM responses; strict, so any change in what the kernel sends stops it."""
    return ReplayLLM(Path(run_dir) / "trace.jsonl")


def comparable(run_dir) -> list[dict]:
    """The trace without wall-clock fields and without run_start.llm (it names the adapter), to compare a recording
    with its replay."""
    events = [json.loads(line) for line in trace.normalized(Path(run_dir) / "trace.jsonl").split("\n") if line]
    return [{k: v for k, v in e.items() if not (e["type"] == "run_start" and k == "llm")} for e in events]
