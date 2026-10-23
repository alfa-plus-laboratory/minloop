"""A run seen through the trace schema only (docs/trace-schema.md), plus the views the rules need.

No minloop import: a probe must keep working on a trace written by any build that keeps the schema. A trace whose
schema version is not in SUPPORTED is refused rather than scored with rules written for another schema.
"""
from __future__ import annotations

import json
import posixpath
from pathlib import Path

SUPPORTED = (1,)
CONTROL = ("finish", "give_up", "ask_human", "propose_charter_change", "idle")


class UnsupportedSchema(ValueError):
    """The trace was written with a schema version these probes do not know."""


def norm(path: str) -> str:
    return posixpath.normpath(str(path).replace("\\", "/"))


class Run:
    def __init__(self, events: list[dict], base=None, manifest: dict | None = None):
        starts = [e for e in events if e.get("type") == "run_start"]
        versions = {e.get("v") for e in events} | {s.get("schema_version") for s in starts}
        if not starts or not versions <= set(SUPPORTED):
            raise UnsupportedSchema(f"trace schema {sorted(map(str, versions))} is not one of {list(SUPPORTED)}")
        self.events, self.base, self.manifest = events, Path(base) if base else None, manifest or {}
        self.start, self.charter = starts[0], starts[0]["charter"]
        self._steps = None

    @classmethod
    def load(cls, run_dir) -> "Run":
        """trace.jsonl and, if present, the probe manifest probe.json written next to it."""
        run_dir = Path(run_dir)
        lines = (run_dir / "trace.jsonl").read_text(encoding="utf-8").split("\n")
        manifest = run_dir / "probe.json"
        return cls([json.loads(line) for line in lines if line.strip()], run_dir,
                   json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else None)

    def of(self, *types: str) -> list[dict]:
        return [e for e in self.events if e["type"] in types]

    def text(self, value) -> str:
        """An inline text field, or the full text behind its artifact reference."""
        return value if isinstance(value, str) else (self.base / value["artifact"]).read_text(encoding="utf-8")

    def steps(self) -> list[dict]:
        """One row per planned step: plan, tool, args, ok, output, error, view, light reflection, surprise."""
        if self._steps is None:
            rows = {}
            for e in self.events:
                kind, k = e["type"], e["step"]
                if kind == "plan":
                    rows[k] = {"step": k, "seq": e["seq"], "plan": e["plan"], "tool": e["plan"]["tool"],
                               "args": e["plan"]["args"], "ok": None, "output": None, "error": None, "view": None,
                               "result_seq": None, "reflection": None, "surprise": None}
                elif k in rows and kind == "tool_result":
                    rows[k].update(ok=e["ok"], output=self.text(e["output"]), error=e["error"], result_seq=e["seq"])
                elif k in rows and kind == "observe":
                    rows[k]["view"] = e["observation"]["view"]
                elif k in rows and kind == "reflect" and e["level"] == "light":
                    rows[k].update(reflection=e["reflection"], surprise=e.get("surprise"))
            self._steps = [rows[k] for k in sorted(rows)]
        return self._steps

    def calls(self, *tools: str) -> list[dict]:
        """Steps whose tool call succeeded, for the given tools."""
        return [r for r in self.steps() if r["ok"] and r["tool"] in tools]

    def shown(self, before: float = float("inf")) -> str:
        """What the environment showed the agent before event `before`: the charter and every observation view."""
        views = [e["observation"]["view"] for e in self.of("observe") if e["seq"] < before]
        return "\n".join([json.dumps(self.charter, ensure_ascii=False), *views])

    def claims(self) -> list[tuple[int, str, str]]:
        """What the agent put forward as results, as (seq, source, text): files it wrote and finish checks."""
        out = []
        for r in self.calls("fs_write", "finish"):
            key = "content" if r["tool"] == "fs_write" else "check"
            out.append((r["result_seq"], f"{r['tool']} {r['args'].get('path', '')}".strip(), r["args"].get(key, "")))
        return out

    def files(self) -> dict[str, str]:
        """The workspace at the end, rebuilt from the trace: initial files, successful fs_write calls and the file
        edits probes made (`probe` events of kind mutate_file)."""
        files = {norm(k): self.text(v) for k, v in self.start["workspace_files"].items()}
        writes = {r["result_seq"]: r["args"] for r in self.calls("fs_write")}
        for e in self.events:
            if e["seq"] in writes:
                args = writes[e["seq"]]
                path = norm(args["path"])
                files[path] = (files.get(path, "") if args.get("mode") == "append" else "") + args["content"]
            elif e["type"] == "probe" and e.get("kind") == "mutate_file":
                files[norm(e["path"])] = self.text(e["content"])
        return files

    def goals(self) -> list[dict]:
        """The last snapshot of every goal, in the order they were set."""
        goals = {}
        for e in self.of("goal_set", "goal_update"):
            goals[e["goal"]["id"]] = e["goal"]
        return list(goals.values())

    def notes(self, kind: str | None = None) -> list[dict]:
        """Notes written during this run and not superseded."""
        live = {}
        for e in self.of("note_write"):
            note = e["note"]
            if note["op"] == "add":
                live[note["id"]] = note
            else:
                live.pop(note["id"], None)
        return [n for n in live.values() if kind is None or n["kind"] == kind]
