"""The three subjects a probe suite runs against. All three get the same cell: the same charter text, workspace,
tools (minloop's tools and probe tools, run through minloop's tool runner) and step budget. Only the scaffold around
the model differs:

- minloop    the released kernel: goals, plan with self-report, act, observe, reflection, notes.
- reference  a minimal greedy loop: one call per step returns {thought, tool, args}; the result is appended to the
             history; it stops at finish or give_up. No goals, no stated expectations, no reflection, no notes.
- bare       one prompt, one answer, no tools: the initial workspace files are put into the prompt in full, and the
             files, give_up and ask_human in the answer are carried out for it. The lower bound.

The two baselines write their runs in the trace schema, with the same llm_call events as the kernel, so the probes
judge them unchanged and a recording can be replayed exactly.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from string import Template

from minloop import __version__, act, observe, trace
from minloop.llm.base import encode, parse_json
from minloop.schema import errors
from minloop.tools import DEFAULT_TOOLS, REGISTRY, ToolContext, describe
from probes.env import prepare, run_cell, write_manifest

PROMPTS = Path(__file__).parent / "prompts"
RETRIES = 2  # invalid JSON replies retried with the errors quoted back, as the kernel does
CONTROL = {"finish", "give_up"}
NO_EXPECTATION = {"expected": "", "confidence": 0.0}  # baselines state no expectation: no rule-based surprise


class Minloop:
    name = "minloop"
    signals = frozenset({"files", "tools", "goals", "plan", "reflection", "notes"})

    def run(self, cell, llm, root, *, seed=0, run_id=None) -> Path:
        return run_cell(cell, llm, root, seed=seed, run_id=run_id)


class Session:
    """One baseline run: workspace, trace, LLM calls with retries, tool calls, stop event, probe manifest."""

    def __init__(self, subject, cell, llm, root, seed, run_id):
        self.charter, files, probe_tools = prepare(cell, seed)
        self.dir = Path(root) / (run_id or cell.id.replace("/", "."))
        if self.dir.exists() and any(self.dir.iterdir()):
            raise FileExistsError(f"run directory '{self.dir.name}' exists and is not empty")
        self.ws = self.dir / "workspace"
        for rel, text in files.items():
            (self.ws / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.ws / rel).write_text(text, encoding="utf-8", newline="\n")
        self.ws.mkdir(parents=True, exist_ok=True)
        self.files, self.llm, self.cell, self.seed, self.k, self.tokens = files, llm, cell, seed, 0, 0
        self.tools = toolset(self.charter, probe_tools)
        self.max_steps = self.charter.get("budget", {}).get("max_steps") or 20
        self.max_tokens = self.charter.get("budget", {}).get("max_tokens")
        self.prompt = (PROMPTS / f"{subject.name}.md").read_text(encoding="utf-8")
        self.trace = trace.Trace(self.dir / "trace.jsonl")
        self.trace.emit("run_start", None, run_id=self.dir.name, session=1, version=__version__,
                        schema_version=trace.SCHEMA_VERSION, charter=self.charter, llm=llm.info(),
                        budget={"max_steps": self.max_steps, **({"max_tokens": self.max_tokens} if self.max_tokens
                                                                else {})},
                        forever=False, prompts={subject.name: trace.sha256(self.prompt)},
                        workspace_files={k: self.trace.blob(v) for k, v in files.items()}, subject=subject.name)

    def system(self) -> str:
        return Template(self.prompt).safe_substitute(tools=describe(self.tools))

    def ask(self, kind: str, user: dict, schema: dict, check=None) -> dict | None:
        """One structured call, retried with the errors quoted back; every attempt is an llm_call event."""
        messages = [{"role": "system", "content": self.system()},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False, indent=1)}]
        for attempt in range(RETRIES + 1):
            if self.max_tokens and self.tokens >= self.max_tokens:
                raise Budget("max_tokens")
            started = time.monotonic()
            reply = self.llm.complete(messages, kind)
            self.tokens += reply.prompt_tokens + reply.completion_tokens
            request = encode(messages)
            self.trace.emit("llm_call", self.k, kind=kind, attempt=attempt, request=self.trace.blob(request),
                            request_sha256=trace.sha256(request), response=self.trace.blob(reply.text),
                            usage={"prompt_tokens": reply.prompt_tokens, "completion_tokens": reply.completion_tokens,
                                   "cost": reply.cost}, ms=int((time.monotonic() - started) * 1000))
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
        self.trace.emit("warning", self.k, message=f"{kind}: no valid reply after {RETRIES + 1} attempts: {problems}")
        return None

    def act(self, k: int, plan: dict, tools=None, view_limit=observe.VIEW_CHARS):
        """Run one tool call as step k and record it the way the kernel does (plan, tool_call, tool_result,
        observe); a baseline states no expectation, so the observation carries no rule-based surprise."""
        self.k = k
        self.trace.emit("plan", k, goal="mission", plan=plan)
        self.trace.emit("tool_call", k, tool=plan["tool"], args=plan["args"])
        started = time.monotonic()
        ctx = ToolContext(self.ws.resolve(), self.dir.name, lambda type_, **kw: self.trace.emit(type_, k, **kw))
        res = act.run(tools or self.tools, plan["tool"], plan["args"], ctx)
        self.trace.emit("tool_result", k, tool=plan["tool"], ok=res.ok, output=self.trace.blob(res.output),
                        error=res.error, data=res.data, cost=res.cost, ms=int((time.monotonic() - started) * 1000))
        obs = {**observe.view(res, NO_EXPECTATION, view_limit), "surprise_rule": None}
        self.trace.emit("observe", k, observation=obs)
        return res, obs

    def close(self, reason: str, detail: str = "") -> Path:
        self.trace.emit("stop", self.k, reason=reason, detail=detail)
        self.trace.close()
        write_manifest(self.cell, self.seed, self.dir)
        return self.dir


class Budget(Exception):
    pass


def toolset(charter: dict, probe_tools: dict) -> dict:
    """The charter's tools, probe tools first. Baselines never get network or shell access."""
    out = {}
    for name in charter.get("tools", DEFAULT_TOOLS):
        tool = probe_tools.get(name) or REGISTRY.get(name)
        if tool is None:
            raise ValueError(f"unknown tool '{name}'")
        if tool.requires:
            raise ValueError(f"tool '{name}' needs '{tool.requires}' access, which baselines never get")
        out[name] = tool
    return out


def guarded(body):
    """Run a baseline body inside a session and always end with a stop event: done, budget or error."""
    def run(self, cell, llm, root, *, seed=0, run_id=None) -> Path:
        s = Session(self, cell, llm, root, seed, run_id)
        try:
            reason, detail = body(self, s)
        except Budget as e:
            reason, detail = "budget", str(e)
        except Exception as e:  # recorded, like the kernel does
            reason, detail = "error", f"{type(e).__name__}: {e}"
        return s.close(reason, detail)
    return run


ACTION = {"type": "object", "required": ["tool", "args"], "properties": {
    "thought": {"type": "string"}, "tool": {"type": "string", "minLength": 1}, "args": {"type": "object"}}}


class Reference:
    name = "reference"
    signals = frozenset({"files", "tools"})

    @guarded
    def run(self, s: Session):
        history = []
        for k in range(1, s.max_steps + 1):
            s.k = k
            a = s.ask("act", {"mission": s.charter["mission"], "constraints": s.charter.get("constraints", []),
                              "history": history, "steps_left": s.max_steps - k + 1}, ACTION)
            if a is None:
                return "error", "no valid action"
            res, obs = s.act(k, {"intent": a.get("thought", ""), "tool": a["tool"], "args": a["args"]})
            history.append({"step": k, "tool": a["tool"], "args": a["args"], "ok": obs["ok"], "result": obs["view"]})
            if res.ok and (res.data or {}).get("control") in CONTROL:
                return "done", f"{a['tool']}: {res.data['result']}"
        return "budget", "max_steps"


ANSWER = {"type": "object", "required": ["files"], "properties": {
    "files": {"type": "object"}, "give_up": {"type": ["string", "null"]}, "ask_human": {"type": ["string", "null"]},
    "summary": {"type": ["string", "null"]}}}


def check_answer(a: dict) -> list[str]:
    return [f"files['{p}'] must be a string" for p, text in a["files"].items() if not isinstance(text, str)]


class Bare:
    name = "bare"
    signals = frozenset({"files"})

    @guarded
    def run(self, s: Session):
        # The harness reads every initial file for the model and puts it into the prompt in full; recording each
        # read as an observation keeps "what the model was shown" in the trace, where the probes look for it.
        reader = {"fs_read": REGISTRY["fs_read"]}  # the plain tool: these reads must not trigger perturbations
        documents = {}
        for k, path in enumerate(sorted(s.files), 1):
            plan = {"intent": "harness: put this file into the prompt", "tool": "fs_read",
                    "args": {"path": path, "limit": len(s.files[path]) + 1}}
            _, obs = s.act(k, plan, reader, view_limit=len(s.files[path]) + 1000)
            documents[path] = obs["view"]
        s.k = k = len(documents) + 1
        a = s.ask("answer", {"mission": s.charter["mission"], "constraints": s.charter.get("constraints", []),
                             "documents": documents}, ANSWER, check_answer)
        if a is None:
            return "error", "no valid answer"
        actions = [("fs_write", {"path": p, "content": text}) for p, text in a["files"].items()]
        if a.get("ask_human"):
            actions.append(("ask_human", {"question": a["ask_human"]}))
        actions.append(("give_up", {"reason": a["give_up"]}) if a.get("give_up")
                       else ("finish", {"check": a.get("summary") or "(no summary given)"}))
        for i, (tool, args) in enumerate(actions):
            s.act(k + i, {"intent": "harness: carry out the answer", "tool": tool, "args": args})
        return "done", actions[-1][0]


SUBJECTS = {s.name: s for s in (Minloop(), Reference(), Bare())}
