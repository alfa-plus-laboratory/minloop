"""The kernel: runs goal -> plan -> act -> observe -> reflect until done, out of budget, or stopped.

It owns the budget and stop guards, checkpoint/resume (state.json) and graceful Ctrl-C, and talks to
the LLM through one JSON-text protocol (ask). It contains no domain logic.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from string import Template

from . import __version__, act, observe, plan, reflect, trace
from .goal import ITEM, SCHEMA as GOAL_SCHEMA, Goals
from .llm.base import encode, parse_json
from .memory import Memory
from .schema import errors
from .tools import DEFAULT_TOOLS, REGISTRY, ToolContext, describe

PROMPTS = Path(__file__).parent / "prompts"
PROMPT_FILES = {"goal": "goal.md", "plan": "plan.md", "reflect": "reflect.md", "deep": "deep_reflect.md"}
BUDGET_KEYS = ("max_steps", "max_tokens", "max_seconds", "max_cost")
STOP_DEFAULTS = {"no_progress_steps": 5}
JSON_RETRIES = 2
GOAL_ROUNDS = 3  # consecutive goal rounds without a usable goal before giving up
BANNER = {"network": "NETWORK ACCESS IS ON ({}): the agent can send requests to any host.",
          "shell": "SHELL IS ON ({}): the agent can run any command with your user's permissions."}


class ConfigError(ValueError):
    """Refusal to start: the charter or the options are incomplete or unsafe."""


class Stop(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail)
        self.reason, self.detail = reason, detail


def gate(charter: dict, budget: dict, forever: bool, stop: dict, priced: bool):
    """Startup checks (T1, T2, T8). Returns the run's tools and the warnings to show loudly."""
    if not str(charter.get("mission", "")).strip():
        raise ConfigError("the charter needs a mission")
    if "task" in charter and errors(charter["task"], ITEM):
        raise ConfigError(f"charter task: {errors(charter['task'], ITEM)}")
    if not any(budget.get(k) for k in BUDGET_KEYS):
        raise ConfigError("refusing to start without a budget: set at least one of " + ", ".join(BUDGET_KEYS))
    if budget.get("max_cost") and not priced:
        raise ConfigError("max_cost needs token prices (--price-in/--price-out), otherwise cost is never counted")
    if forever and not (budget.get("max_tokens") or budget.get("max_cost")):
        raise ConfigError("--forever needs a max_tokens or max_cost budget")
    if forever and not stop.get("no_progress_steps"):
        raise ConfigError("--forever needs stop.no_progress_steps > 0")
    tools, warnings, allow = {}, [], charter.get("allow", {})
    for name in charter.get("tools", DEFAULT_TOOLS):
        if name not in REGISTRY:
            raise ConfigError(f"unknown tool '{name}'")
        t = tools[name] = REGISTRY[name]
        if t.requires and allow.get(t.requires) is not True:
            raise ConfigError(f"tool '{name}' is off by default; enable it with "
                              f"\"allow\": {{\"{t.requires}\": true}} in the charter")
        if t.requires:
            warnings.append(BANNER[t.requires].format(name) + " The workspace is not a sandbox; use a container.")
    return tools, warnings


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class Kernel:
    def __init__(self, charter: dict | None, llm, run_dir, *, resume=False, run_id=None, budget=None, forever=False,
                 files=None, notes_path=None, prompts_dir=None, on_event=()):
        self.dir = Path(run_dir)
        if resume:
            if not (self.dir / "state.json").exists():
                raise ConfigError(f"nothing to resume in '{self.dir.name}': no state.json")
            self.state = json.loads((self.dir / "state.json").read_text(encoding="utf-8"))
            charter = self.state["charter"]  # the charter of record; a new one cannot be swapped in
        elif self.dir.exists() and any(self.dir.iterdir()):
            raise ConfigError(f"run directory '{self.dir.name}' exists and is not empty (use --resume to continue it)")
        self.charter = json.loads(json.dumps(charter))  # private copy: the agent can only propose changes
        merged = {**self.charter.get("budget", {}), **(budget or {})}
        self.budget = {k: merged[k] for k in BUDGET_KEYS if merged.get(k)}
        self.stop_cfg = {**STOP_DEFAULTS, **self.charter.get("stop", {})}
        self.reflect_cfg = {**reflect.DEFAULTS, **self.charter.get("reflect", {})}
        self.tools, self.warnings = gate(self.charter, self.budget, forever, self.stop_cfg, getattr(llm, "priced", False))
        self.llm, self.forever, self.files = llm, forever, {}
        folder = Path(prompts_dir) if prompts_dir else PROMPTS
        self.prompts = {kind: (folder / name).read_text(encoding="utf-8") for kind, name in PROMPT_FILES.items()}
        self.tool_text = describe(self.tools)
        if not resume:
            self.state = {"run_id": run_id or self.dir.name, "session": 0, "step": 0, "charter": self.charter,
                          "goals": None, "seen": [], "no_progress": 0, "failures": 0, "notes": "notes.jsonl"}
            self.files = dict(files or {})  # initial workspace content, recorded in run_start
            for rel, content in self.files.items():
                if Path(rel).is_absolute() or ".." in Path(rel).parts:
                    raise ConfigError(f"workspace file '{rel}' must stay inside the workspace")
                (self.dir / "workspace" / rel).parent.mkdir(parents=True, exist_ok=True)
                (self.dir / "workspace" / rel).write_text(content, encoding="utf-8", newline="\n")
        if notes_path:
            self.state["notes"] = str(Path(notes_path).resolve())
        self.ws = (self.dir / "workspace").resolve()
        self.ws.mkdir(parents=True, exist_ok=True)
        self.memory = Memory(self.dir / self.state["notes"])
        path = self.dir / "trace.jsonl"
        for event in trace.read(path) if path.exists() else []:
            self.memory.on_event(event)  # working memory is rebuilt from the trace, never loaded
        self.trace = trace.Trace(path, [self.memory.on_event, *on_event])
        self.goals = Goals(self.emit, self.state["goals"])
        self.used, self.started, self.k, self.goal_misses = {"steps": 0, "tokens": 0, "cost": 0.0}, time.monotonic(), 0, 0

    @classmethod
    def resume(cls, run_dir, llm, **options) -> "Kernel":
        return cls(None, llm, run_dir, resume=True, **options)

    def emit(self, type_: str, step, **payload) -> dict:
        return self.trace.emit(type_, step, **payload)

    def run(self) -> dict:
        s = self.state
        s["session"] += 1
        s["no_progress"] = s["failures"] = 0  # a new session (maybe a new model) gets a fresh chance
        self.started, self.k = time.monotonic(), s["step"]
        self.emit("run_start", None, run_id=s["run_id"], session=s["session"], version=__version__,
                  schema_version=trace.SCHEMA_VERSION, charter=self.charter, llm=self.llm.info(),
                  budget=self.budget, forever=self.forever,
                  prompts={k: trace.sha256(v) for k, v in self.prompts.items()},
                  workspace_files={k: self.trace.blob(v) for k, v in self.files.items()})
        for warning in self.warnings:
            self.emit("warning", None, message=warning)
        if s["session"] == 1 and "task" in self.charter:  # task mode: the human gives the first goal
            self.goals.add(self.charter["task"], "charter", 0)
            self.goals.next(0)
        try:
            while True:
                self.guard()
                if self.goals.current() is None:
                    self.new_goals()
                else:
                    self.step()
        except Stop as e:
            reason, detail = e.reason, e.detail
        except KeyboardInterrupt:
            reason, detail = "interrupt", "KeyboardInterrupt"
        except Exception as e:  # recorded, then the run ends cleanly and can be resumed
            reason, detail = "error", f"{type(e).__name__}: {e}"
        self.emit("stop", s["step"], reason=reason, detail=detail)
        s["last_stop"] = reason
        self.checkpoint()
        self.trace.close()
        return {"run_id": s["run_id"], "session": s["session"], "steps": s["step"], "reason": reason,
                "detail": detail}

    def guard(self):
        """Hard limits, checked before every step and every LLM call."""
        limits = [("max_tokens", self.used["tokens"]), ("max_cost", self.used["cost"])]
        if not self.forever:
            limits += [("max_steps", self.used["steps"]), ("max_seconds", time.monotonic() - self.started)]
        for key, used in limits:
            if self.budget.get(key) and used >= self.budget[key]:
                raise Stop("budget", key)
        n = self.stop_cfg["no_progress_steps"]
        if n and self.state["no_progress"] >= n:
            raise Stop("no_progress", f"{n} steps in a row without progress")

    def new_goals(self):
        s = self.state
        self.k = s["step"]
        if "task" in self.charter:
            raise Stop("done", f"task mode: the charter task is {self.goals.items['g1']['status']}")
        out = self.ask("goal", self.context(), GOAL_SCHEMA)
        if out and out["goals"]:
            for item in out["goals"]:
                self.goals.add(item, "self", s["step"])
            self.goals.next(s["step"])
            self.goal_misses = 0
            self.checkpoint()
        elif out and out.get("mission_complete"):
            raise Stop("done", out.get("reason") or "mission complete")
        else:
            s["no_progress"], self.goal_misses = s["no_progress"] + 1, self.goal_misses + 1
            if self.goal_misses >= GOAL_ROUNDS:
                raise Stop("no_progress", f"no usable goals after {GOAL_ROUNDS} rounds")

    def step(self):
        s = self.state
        self.k = k = s["step"] + 1
        g = self.goals.current()
        p = plan.make(self.ask, self.context())
        if p is None:
            return self.end_step(k, progress=False)
        progress = False
        try:
            progress = self.act_and_reflect(k, g, p)
        finally:  # a stop in mid-step (budget, Ctrl-C, error) still closes the step: resume never redoes it
            self.end_step(k, progress)

    def act_and_reflect(self, k: int, g: dict, p: dict) -> bool:
        s = self.state
        self.emit("plan", k, goal=g["id"], plan=p)
        self.emit("tool_call", k, tool=p["tool"], args=p["args"])
        started = time.monotonic()
        ctx = ToolContext(self.ws, s["run_id"], lambda type_, **kw: self.emit(type_, k, **kw))
        res = act.run(self.tools, p["tool"], p["args"], ctx)
        self.used["cost"] += res.cost
        self.emit("tool_result", k, tool=p["tool"], ok=res.ok, output=self.trace.blob(res.output), error=res.error,
                  data=res.data, cost=res.cost, ms=_ms(started))
        obs = observe.view(res, p)
        self.emit("observe", k, observation=obs)
        control = (res.data or {}).get("control") if res.ok else None
        closed = {"finish": "done", "give_up": "abandoned"}.get(control)
        if closed:
            self.goals.close(closed, k, res.data["result"])
        r = reflect.light(self.ask, {"goal": g, "plan": p, "observation": obs, "goal_closed": closed})
        surprise = obs["surprise_rule"] if obs["surprise_rule"] is not None else (r or {}).get("surprise")
        if r:
            self.emit("reflect", k, level="light", reflection=r, surprise=surprise)
            if not closed and r["goal_action"] == "revise":
                self.goals.revise(r["new_goal"], k)
            elif not closed and r["goal_action"] == "spawn":
                self.goals.add(r["new_goal"], "reflect", k, push=True)
        # Progress is judged by rule, not self-report: a goal closed, or a successful result never seen before.
        signature = trace.sha256(json.dumps([p["tool"], p["args"], res.ok, res.output, res.error], sort_keys=True))
        fresh = signature not in s["seen"]
        if fresh:
            s["seen"].append(signature)
        s["failures"] = 0 if res.ok else s["failures"] + 1
        why = reflect.trigger(surprise, s["failures"], closed, k, self.reflect_cfg)
        if why:
            self.deep(k, why)
        return bool(closed) or (res.ok and not control and fresh)

    def deep(self, k: int, why: str):
        d = reflect.deep(self.ask, self.context(trigger=why))
        if d is None:
            return
        self.emit("reflect", k, level="deep", reflection=d, trigger=why)
        for note in d["notes"]:
            self.emit("note_write", k, note=self.memory.notes.add(note, self.state["run_id"], k))
        for note_id in d.get("supersede", []):
            record = self.memory.notes.supersede(note_id, self.state["run_id"], k)
            if record:
                self.emit("note_write", k, note=record)

    def end_step(self, k: int, progress: bool):
        s = self.state
        s["step"], s["no_progress"] = k, 0 if progress else s["no_progress"] + 1
        self.used["steps"] += 1
        self.emit("budget_tick", k, used={**self.used, "ms": _ms(self.started)}, no_progress=s["no_progress"])
        self.checkpoint()

    def ask(self, kind: str, context: dict, schema: dict, check=None):
        """One structured call: JSON text out, parsed and validated locally, retried with the errors."""
        system = Template(self.prompts[kind]).safe_substitute(tools=self.tool_text)
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False, indent=1)}]
        for attempt in range(JSON_RETRIES + 1):
            self.guard()
            started = time.monotonic()
            reply = self.llm.complete(messages, kind)
            self.used["tokens"] += reply.prompt_tokens + reply.completion_tokens
            self.used["cost"] += reply.cost
            request = encode(messages)
            self.emit("llm_call", self.k, kind=kind, attempt=attempt, request=self.trace.blob(request),
                      request_sha256=trace.sha256(request), response=self.trace.blob(reply.text),
                      usage={"prompt_tokens": reply.prompt_tokens, "completion_tokens": reply.completion_tokens,
                             "cost": reply.cost}, ms=_ms(started))
            try:
                value = parse_json(reply.text)
                problems = errors(value, schema) or (check(value) if check else [])
            except ValueError as e:
                problems = [str(e)]
            if not problems:
                return value
            messages = messages + [{"role": "assistant", "content": reply.text},
                                   {"role": "user", "content": "That reply was not valid: " + "; ".join(problems)
                                    + ". Reply again with exactly one JSON object and nothing else."}]
        self.emit("warning", self.k, message=f"{kind}: no valid reply after {JSON_RETRIES + 1} attempts: {problems}")
        return None

    def context(self, **extra) -> dict:
        """Working memory for one call: charter, goals, recent steps, selected notes, budget left."""
        g = self.goals.current()
        return {"mission": self.charter["mission"], "constraints": self.charter.get("constraints", []),
                "goals": self.goals.view(), **self.memory.working(g["text"] if g else self.charter["mission"]),
                "budget_left": self.left(), **extra}

    def left(self) -> dict:
        names = ("tokens", "cost") if self.forever else ("steps", "tokens", "cost")
        return {n: round(self.budget[f"max_{n}"] - self.used[n], 6) for n in names if self.budget.get(f"max_{n}")}

    def checkpoint(self):
        self.state["goals"] = self.goals.state()
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.dir / "state.json")
