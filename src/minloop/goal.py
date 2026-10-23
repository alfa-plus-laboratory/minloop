"""Goals: self-generated, each with checkable completion criteria.

One active goal on top of a stack (sub-goals push onto it; single agent, no sub-agents) and a FIFO
queue of pending goals. Statuses: pending, active, blocked (waiting on a sub-goal), done, abandoned.
"""
from __future__ import annotations

ITEM = {"type": "object", "required": ["text", "criteria"], "properties": {
    "text": {"type": "string", "minLength": 1},
    "criteria": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}}}}
SCHEMA = {"type": "object", "required": ["goals"], "properties": {
    "goals": {"type": "array", "items": ITEM},
    "mission_complete": {"type": "boolean"}, "reason": {"type": "string"}}}


class Goals:
    def __init__(self, emit, state: dict | None = None):
        state = state or {"items": {}, "stack": [], "queue": []}
        self.items, self.stack, self.queue, self.emit = state["items"], state["stack"], state["queue"], emit

    def state(self) -> dict:
        return {"items": self.items, "stack": self.stack, "queue": self.queue}

    def current(self) -> dict | None:
        return self.items[self.stack[-1]] if self.stack else None

    def add(self, item: dict, source: str, step: int, push: bool = False) -> dict:
        """source: charter / self / reflect. push=True makes it a sub-goal of the current goal."""
        parent = self.current() if push else None
        g = {"id": f"g{len(self.items) + 1}", "text": item["text"], "criteria": item["criteria"],
             "source": source, "status": "pending", "parent": parent and parent["id"], "step": step}
        self.items[g["id"]] = g
        self.emit("goal_set", step, goal=dict(g))
        if push:
            self._set(parent, "blocked", step, "block")
            self.stack.append(g["id"])
            self._set(g, "active", step, "activate")
        else:
            self.queue.append(g["id"])
        return g

    def next(self, step: int):
        """Activate the first pending goal when nothing is active."""
        if not self.stack and self.queue:
            self.stack.append(self.queue.pop(0))
            self._set(self.current(), "active", step, "activate")

    def revise(self, item: dict, step: int):
        g = self.current()
        g["text"], g["criteria"] = item["text"], item["criteria"]
        self._set(g, "active", step, "revise")

    def close(self, status: str, step: int, result: str):
        """status: done / abandoned. The parent (if any) becomes active again, else the next pending goal."""
        g = self.items[self.stack.pop()]
        g["result"] = result
        self._set(g, status, step, status)
        if self.stack:
            self._set(self.current(), "active", step, "unblock")
        self.next(step)

    def view(self) -> dict:
        closed = [g for g in self.items.values() if g["status"] in ("done", "abandoned")]
        return {"current": self.current(), "parents": [self.items[i]["text"] for i in self.stack[:-1]],
                "pending": [self.items[i]["text"] for i in self.queue],
                "closed": [{k: g[k] for k in ("id", "text", "status", "result")} for g in closed[-8:]]}

    def _set(self, g: dict, status: str, step: int, action: str):
        g["status"] = status
        self.emit("goal_update", step, goal=dict(g), action=action)
