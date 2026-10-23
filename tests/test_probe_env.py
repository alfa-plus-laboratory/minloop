"""The probe harness: perturbations live in wrapped tools, the kernel is untouched, judging never imports minloop;
plus the command line and the report."""
import ast
import json
import sys
from pathlib import Path

import pytest

from conftest import ROOT
from minloop.tools import REGISTRY, ToolContext
from probes.__main__ import main
from probes.env import build_tool, installed
from probes.organisms import CELLS
from probes.report import summarize, table, wilson

PROBES = ROOT / "probes"
HARNESS = {"env.py", "__main__.py"}  # the only probe modules that may import minloop


def imports(path: Path) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), feature_version=(3, 10))):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


def test_judging_depends_on_the_trace_schema_only():
    files = sorted(PROBES.rglob("*.py"))
    assert len(files) >= 12
    for f in files:
        found = imports(f)
        assert found <= set(sys.stdlib_module_names) | {"minloop"}, f"{f.name}: {found}"
        if f.name not in HARNESS:
            assert "minloop" not in found, f"{f.relative_to(ROOT)} imports minloop"


def context(tmp_path):
    events = []
    return ToolContext(tmp_path, "r", lambda type_, **kw: events.append({"type": type_, **kw})), events


def test_perturbations(tmp_path):
    ctx, events = context(tmp_path)
    (tmp_path / "f.txt").write_text("v1", encoding="utf-8")
    read = build_tool({"name": "fs_read", "wrap": "fs_read", "perturb": [
        {"kind": "fail", "calls": [2], "error": "timeout: slow"},
        {"kind": "mutate_file", "call": 3, "path": "f.txt", "content": "v2"},
        {"kind": "rewrite", "call": 4, "old": "v2", "new": "v3"},
        {"kind": "remove", "after": 4}]})
    outputs = [read.fn({"path": "f.txt"}, ctx) for _ in range(5)]
    assert [(r.ok, r.output or r.error) for r in outputs] == [
        (True, "v1"), (False, "timeout: slow"), (True, "v2"), (True, "v3"),
        (False, "unknown_tool: 'fs_read' is no longer available")]
    assert [(e["kind"], e["call"]) for e in events] == [("fail", 2), ("mutate_file", 3), ("rewrite", 4), ("remove", 5)]
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "v2"  # rewrite changes the result, not the file
    assert read.params == REGISTRY["fs_read"].params and read.fn is not REGISTRY["fs_read"].fn

    page = build_tool({"name": "page_cache", "description": "d", "fixture": {"u": "text"}})
    assert page.fn({"url": "u"}, ctx).output == "text"
    assert page.fn({"url": "v"}, ctx).error == "not_found: nothing at 'v'"
    bad = build_tool({"name": "fs_read", "wrap": "fs_read",
                      "perturb": [{"kind": "mutate_file", "call": 1, "path": "../x", "content": ""}]})
    with pytest.raises(ValueError, match="inside the workspace"):
        bad.fn({"path": "f.txt"}, ctx)


def test_installed_restores_the_registry_even_on_error():
    before = dict(REGISTRY)
    probe = build_tool({"name": "fs_read", "wrap": "fs_read"})
    with pytest.raises(RuntimeError):
        with installed({"fs_read": probe, "page_cache": probe}):
            assert REGISTRY["fs_read"] is probe and "page_cache" in REGISTRY
            raise RuntimeError
    assert REGISTRY == before


def test_wilson_and_summary():
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(5, 10)
    assert (round(lo, 4), round(hi, 4)) == (0.2366, 0.7634)
    assert wilson(10, 10)[1] == 1.0 and wilson(0, 10)[0] == 0.0

    class V:
        def __init__(self, cell, polarity, passed):
            self.cell, self.polarity, self.passed = cell, polarity, passed
    rows = summarize([V("a/x", "positive", True), V("a/y", "positive", False), V("a/x", "positive", None),
                      V("b/x", "control", True)])
    assert [(r["organism"], r["passed"], r["decided"], r["undecided"]) for r in rows] == [("a", 1, 2, 1),
                                                                                          ("b", 1, 1, 0)]
    assert "positive" in table(rows)


def test_command_line(tmp_path, capsys):
    assert main(["list"]) == 0
    assert "capability-boundary/no-web" in capsys.readouterr().out
    assert main(["demo", "--root", str(tmp_path), "--judge", "mock", "--replay", "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "all scripted agents got the verdict they were written for" in out and "UNEXPECTED" not in out
    run_dir = tmp_path / "record" / "stale-belief.changed.quiet"
    assert main(["judge", str(run_dir), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["passed"] is None
    assert main(["judge", str(run_dir), "--judge", "mock"]) == 0
    assert "pass by judge" in capsys.readouterr().out
    assert main(["judge", str(ROOT / "examples" / "recorded" / "hello")]) == 2
    assert "not a probe run" in capsys.readouterr().err



def test_demo_reports_a_differing_replay_even_when_quiet(tmp_path, capsys, monkeypatch):
    calls = iter(range(10 ** 6))
    monkeypatch.setattr("probes.__main__.comparable", lambda run_dir: next(calls))  # never equal
    assert main(["demo", "--root", str(tmp_path), "--replay", "--quiet"]) == 1
    runs = sum(len(cell.agents) for cell in CELLS.values())
    out = capsys.readouterr().out
    assert out.count("replay: DIFFERS") == runs and f"{runs} problem(s)" in out
