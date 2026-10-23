"""Memory in three layers, all plain files a person can read:

- working: rebuilt every step from trace events (never persisted, so it can always be rebuilt);
- episodic: the trace itself;
- notes: long-term, notes.jsonl, append-only; a wrong note is superseded, never edited.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .trace import lines, now

RECENT, FULL, SHORT = 8, 3, 300  # steps in context; the last FULL keep their whole observation view
NOTES_SHOWN, TITLES_SHOWN = 8, 30


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w{3,}", text.lower()))


def _clip(value, n: int = SHORT):
    if isinstance(value, str) and len(value) > n:
        return value[:n] + " [...]"
    return {k: _clip(v, n) for k, v in value.items()} if isinstance(value, dict) else value


class Notes:
    def __init__(self, path):
        self.path, self.items, self.count = Path(path), {}, 0
        for record in map(json.loads, lines(self.path) if self.path.exists() else []):
            self.count += 1
            if record["op"] == "add":
                self.items[record["id"]] = record
            else:
                self.items.pop(record["id"], None)

    def _append(self, record: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.count += 1
        return record

    def add(self, note: dict, run_id: str, step: int) -> dict:
        record = {"op": "add", "id": f"n{self.count + 1}", "kind": note["kind"], "title": note["title"],
                  "text": note["text"], "confidence": note["confidence"], "tags": note.get("tags", []),
                  "run_id": run_id, "step": step, "ts": now()}
        self.items[record["id"]] = record
        return self._append(record)

    def supersede(self, note_id: str, run_id: str, step: int) -> dict | None:
        if self.items.pop(note_id, None) is None:
            return None
        return self._append({"op": "supersede", "id": note_id, "run_id": run_id, "step": step, "ts": now()})

    def select(self, query: str, recall: list[str]) -> tuple[list[dict], list[str]]:
        """Full text for recalled, most recent and keyword-matching notes; titles only for the rest."""
        notes, words = list(self.items.values()), _words(query)
        score = {n["id"]: len(words & _words(" ".join([n["title"], n["text"], *n["tags"]]))) for n in notes}
        matched = sorted((n for n in notes if score[n["id"]]), key=lambda n: -score[n["id"]])
        chosen = {n["id"] for n in [self.items[i] for i in recall if i in self.items]
                  + notes[-NOTES_SHOWN // 2:] + matched[:NOTES_SHOWN // 2]}
        shown = [{k: n[k] for k in ("id", "kind", "title", "text", "confidence")} for n in notes if n["id"] in chosen]
        titles = [f"{n['id']}: {n['title']}" for n in notes if n["id"] not in chosen][-TITLES_SHOWN:]
        return shown, titles


class Memory:
    def __init__(self, notes_path):
        self.notes, self.rows, self.recall = Notes(notes_path), {}, []

    def on_event(self, e: dict):
        """Trace subscriber. Feeding it an old trace rebuilds working memory exactly (resume)."""
        kind, step = e["type"], e["step"]
        if kind == "plan":
            p = e["plan"]
            self.rows[step] = {"step": step, "goal": e["goal"], "intent": p["intent"], "tool": p["tool"],
                               "args": p["args"]}
            self.recall = p.get("recall", [])
        elif kind == "observe" and step in self.rows:
            self.rows[step].update(ok=e["observation"]["ok"], result=e["observation"]["view"])
        elif kind == "reflect" and e["level"] == "light" and step in self.rows:
            self.rows[step]["lesson"] = e["reflection"]["lesson"]

    def working(self, query: str) -> dict:
        rows = [self.rows[k] for k in sorted(self.rows)][-RECENT:]
        rows = [r if i >= len(rows) - FULL else _clip(r) for i, r in enumerate(rows)]
        notes, titles = self.notes.select(query, self.recall)
        return {"recent_steps": rows, "notes": notes, "other_note_titles": titles}
