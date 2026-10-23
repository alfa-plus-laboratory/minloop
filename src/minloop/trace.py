"""Append-only JSONL trace: the source of truth for a run.

Each event is one JSON line: an envelope {v, seq, ts, type, step} plus a type-specific payload.
Text longer than INLINE_LIMIT goes to artifacts/<sha256>.txt and the event keeps a reference,
so nothing is ever truncated. See docs/trace-schema.md.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import schema

SCHEMA_VERSION = 1
VOLATILE = frozenset({"ts", "ms"})  # wall-clock time and durations: the only nondeterministic fields
INLINE_LIMIT = 4000

_TEXT = {"type": ["string", "object"]}  # inline text or an artifact reference
_OBJ, _STR, _INT, _BOOL = ({"type": t} for t in ("object", "string", "integer", "boolean"))


def _payload(**props) -> dict:
    return {"type": "object", "required": list(props), "properties": props}


EVENTS = {
    "run_start": _payload(run_id=_STR, session=_INT, version=_STR, schema_version=_INT, charter=_OBJ,
                          llm=_OBJ, budget=_OBJ, forever=_BOOL, prompts=_OBJ, workspace_files=_OBJ),
    "goal_set": _payload(goal=_OBJ),
    "goal_update": _payload(goal=_OBJ, action=_STR),
    "llm_call": _payload(kind=_STR, attempt=_INT, request=_TEXT, request_sha256=_STR, response=_TEXT,
                         usage=_OBJ, ms=_INT),
    "plan": _payload(goal=_STR, plan=_OBJ),
    "tool_call": _payload(tool=_STR, args=_OBJ),
    "tool_result": _payload(tool=_STR, ok=_BOOL, output=_TEXT, error={"type": ["string", "null"]}, ms=_INT),
    "observe": _payload(observation=_OBJ),
    "reflect": _payload(level={"enum": ["light", "deep"]}, reflection=_OBJ),
    "note_write": _payload(note=_OBJ),
    "budget_tick": _payload(used=_OBJ, no_progress=_INT),
    "ask_human": _payload(question=_STR),
    "charter_proposal": _payload(change=_STR, reason=_STR),
    "idle": _payload(reason=_STR),
    "warning": _payload(message=_STR),
    "stop": _payload(reason={"enum": ["done", "budget", "no_progress", "interrupt", "error"]}, detail=_STR),
}
ENVELOPE = _payload(v=_INT, seq=_INT, ts=_STR, type=_STR, step={"type": ["integer", "null"]})


def validate(event) -> list[str]:
    """Envelope check for every event; payload check for known types (unknown types are allowed)."""
    problems = schema.errors(event, ENVELOPE)
    return problems or schema.errors(event, EVENTS.get(event["type"], {}))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Trace:
    """Writer. emit() validates, appends one line, flushes, then notifies subscribers (the event hook)."""

    def __init__(self, path, subscribers=()):
        self.path = Path(path)
        self.dir = self.path.parent
        self.seq = len(lines(self.path)) if self.path.exists() else 0
        self.file = open(self.path, "a", encoding="utf-8", newline="\n")
        self.subscribers = list(subscribers)

    def emit(self, type_: str, step: int | None = None, **payload) -> dict:
        event = {"v": SCHEMA_VERSION, "seq": self.seq, "ts": now(), "type": type_, "step": step, **payload}
        problems = validate(event)
        if problems:
            raise ValueError(f"invalid '{type_}' event: {problems}")
        self.file.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        self.file.flush()
        self.seq += 1
        for fn in self.subscribers:
            fn(event)
        return event

    def blob(self, text: str):
        """Keep short text inline; store long text under artifacts/ and return a reference to it."""
        if len(text) <= INLINE_LIMIT:
            return text
        digest = sha256(text)
        target = self.dir / "artifacts" / f"{digest}.txt"
        if not target.exists():
            target.parent.mkdir(exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
        return {"artifact": f"artifacts/{digest}.txt", "sha256": digest, "chars": len(text)}

    def close(self):
        self.file.close()


def lines(path) -> list[str]:
    """Non-empty lines split on "\\n" only: str.splitlines() would also break on U+2028 inside JSON strings."""
    return [line for line in Path(path).read_text(encoding="utf-8").split("\n") if line.strip()]


def read(path, check: bool = True) -> list[dict]:
    events = [json.loads(line) for line in lines(path)]
    for event in events if check else ():
        problems = validate(event)
        if problems:
            raise ValueError(f"{Path(path).name} seq {event.get('seq')}: {problems}")
    return events


def text(value, base) -> str:
    """Inline string, or the full text behind an artifact reference."""
    return value if isinstance(value, str) else (Path(base) / value["artifact"]).read_text(encoding="utf-8")


def normalized(path) -> str:
    """The trace with volatile fields removed; deterministic replays must match it byte for byte."""
    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in x.items() if k not in VOLATILE}
        return [strip(v) for v in x] if isinstance(x, list) else x
    return "".join(json.dumps(strip(e), ensure_ascii=False) + "\n" for e in read(path, check=False))
