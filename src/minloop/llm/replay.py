"""Deterministic replay: feed back the LLM responses recorded in a trace, in order, so the whole
loop (tools included) runs again. With from_step + then, recorded responses are used only before
step K and `then` answers the rest (counterfactual replay: another model or prompt from step K)."""
from __future__ import annotations

from pathlib import Path

from .. import trace
from .base import LLM, Reply, encode


class ReplayDivergence(RuntimeError):
    """The kernel asked for something other than what was recorded."""


class ReplayLLM(LLM):
    name = "replay"

    def __init__(self, trace_path, strict: bool = True, from_step: int | None = None, then: LLM | None = None):
        events = trace.read(trace_path)
        self.base = Path(trace_path).parent
        self.recorded = next(e["llm"] for e in events if e["type"] == "run_start")
        self.calls = [e for e in events if e["type"] == "llm_call"
                      and (from_step is None or (e["step"] or 0) < from_step)]
        self.strict, self.from_step, self.then, self.i = strict, from_step, then, 0
        self.priced = any(c["usage"].get("cost") for c in self.calls) or bool(then and then.priced)

    def info(self):
        out = {"adapter": self.name, "recorded": self.recorded}
        return out if self.then is None else {**out, "from_step": self.from_step, "then": self.then.info()}

    def complete(self, messages, kind):
        if self.i >= len(self.calls):
            if self.then is not None:
                return self.then.complete(messages, kind)
            raise ReplayDivergence(f"the recording has only {len(self.calls)} LLM calls")
        call = self.calls[self.i]
        self.i += 1
        if call["kind"] != kind:
            raise ReplayDivergence(f"call {self.i}: recorded kind '{call['kind']}', kernel asked for '{kind}'")
        if self.strict and call["request_sha256"] != trace.sha256(encode(messages)):
            raise ReplayDivergence(f"call {self.i} ({kind}, step {call['step']}): request differs from the recording")
        usage = call["usage"]
        return Reply(trace.text(call["response"], self.base), usage["prompt_tokens"],
                     usage["completion_tokens"], usage.get("cost", 0.0))
