"""Scripted adapter for tests and demos: one queue of canned replies per call kind."""
from __future__ import annotations

import json
from pathlib import Path

from .base import LLM, Reply


class MockLLM(LLM):
    """script = {"goal": [...], "plan": [...], "reflect": [...], "deep": [...]}.

    Items are strings, JSON-able objects, or callables(messages) returning either (tests use
    callables to raise, e.g. KeyboardInterrupt). Token counts are a deterministic chars/4 estimate.
    """
    name = "mock"

    def __init__(self, script: dict):
        self.queues = {kind: list(items) for kind, items in script.items()}

    @classmethod
    def from_file(cls, path) -> "MockLLM":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def complete(self, messages, kind):
        if not self.queues.get(kind):
            raise RuntimeError(f"mock script has no reply left for '{kind}'")
        item = self.queues[kind].pop(0)
        item = item(messages) if callable(item) else item
        text = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return Reply(text, sum(len(m["content"]) for m in messages) // 4, len(text) // 4)
