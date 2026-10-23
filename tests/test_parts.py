"""Schema validator, trace format, JSON parsing, tools and the OpenAI-compatible adapter."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from minloop import act, observe, trace
from minloop.memory import Notes
from minloop.llm import OpenAICompat
from minloop.llm.base import parse_json
from minloop.schema import errors
from minloop.tools import REGISTRY, ToolContext, ToolResult, describe
from minloop.tools.base import Tool


def test_schema_subset():
    s = {"type": "object", "required": ["a"], "additionalProperties": False,
         "properties": {"a": {"type": "integer", "minimum": 1}, "b": {"type": "array", "minItems": 1,
                                                                     "items": {"enum": ["x", "y"]}}}}
    assert errors({"a": 2, "b": ["x"]}, s) == []
    assert errors({"a": True}, s) == ["$.a: expected integer, got bool"]
    assert errors({"b": ["z"], "c": 1}, s) == ["$: missing required key 'a'", "$.b[0]: must be one of ['x', 'y']",
                                               "$: unexpected key 'c'"]
    assert errors({"a": 0, "b": []}, s) == ["$.a: must be >= 1", "$.b: needs at least 1 item(s)"]
    assert errors("", {"type": "string", "minLength": 1}) == ["$: must not be empty"]


def test_trace_validates_and_moves_long_text_to_artifacts(tmp_path):
    t = trace.Trace(tmp_path / "trace.jsonl")
    with pytest.raises(ValueError, match="missing required key 'goal'"):
        t.emit("goal_set", 0)
    long = "x" * (trace.INLINE_LIMIT + 1)
    ref = t.blob(long)
    assert ref["artifact"] == f"artifacts/{trace.sha256(long)}.txt" and trace.text(ref, tmp_path) == long
    t.emit("warning", 1, message="hello", ms=12)
    t.close()
    (e,) = trace.read(tmp_path / "trace.jsonl")
    assert e["seq"] == 0 and e["v"] == trace.SCHEMA_VERSION
    assert json.loads(trace.normalized(tmp_path / "trace.jsonl")) == {"v": 1, "seq": 0, "type": "warning",
                                                                       "step": 1, "message": "hello"}


def test_parse_json_tolerates_prose_fences_and_think_blocks():
    assert parse_json('<think>{"no": 1}</think>Sure!\n```json\n{"a": {"b": 1}}\n``` trailing {') == {"a": {"b": 1}}
    with pytest.raises(ValueError):
        parse_json("no json here")


def ctx(tmp_path):
    return ToolContext(tmp_path, "run", lambda *a, **k: None)


def test_fs_tools_stay_inside_the_workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "secret.txt").write_text("secret")
    os.symlink(tmp_path / "secret.txt", ws / "link.txt")
    tools = {n: REGISTRY[n] for n in ("fs_read", "fs_write", "fs_list")}
    for path in ("../secret.txt", str(tmp_path / "secret.txt"), "link.txt"):
        result = act.run(tools, "fs_read", {"path": path}, ctx(ws))
        assert not result.ok and result.error.startswith("PermissionError")
        assert str(ws) not in result.error
    assert act.run(tools, "fs_write", {"path": "a/b.txt", "content": "0123456789"}, ctx(ws)).ok
    chunk = act.run(tools, "fs_read", {"path": "a/b.txt", "offset": 2, "limit": 3}, ctx(ws))
    assert chunk.output == "234\n[chars 2-5 of 10]"
    assert act.run(tools, "fs_list", {}, ctx(ws)).output.splitlines()[0] == "a/"


def test_act_whitelist_arguments_and_timeout(tmp_path):
    slow = Tool("slow", "sleeps", {"type": "object"}, lambda a, c: time.sleep(5) or ToolResult(True), timeout=0.1)
    tools = {"fs_read": REGISTRY["fs_read"], "slow": slow}
    assert act.run(tools, "shell", {"command": "ls"}, ctx(tmp_path)).error.startswith("unknown_tool")
    assert act.run(tools, "fs_read", {"file": "x"}, ctx(tmp_path)).error.startswith("invalid_args")
    assert act.run(tools, "slow", {}, ctx(tmp_path)).error.startswith("timeout")
    kinds = [observe.view(act.run(tools, name, args, ctx(tmp_path)), {"expected": "x", "confidence": 0.5})["error_type"]
             for name, args in (("shell", {}), ("fs_read", {}), ("fs_read", {"path": "nope"}))]
    assert kinds == ["unknown_tool", "invalid_args", "not_found"]


def test_describe_shows_signatures():
    text = describe({"fs_read": REGISTRY["fs_read"]})
    assert text.startswith("- fs_read(path: string, offset?: integer, limit?: integer):")


def test_openai_compatible_adapter_against_a_stub_server():
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            seen["auth"] = self.headers.get("Authorization")
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            body = json.dumps({"choices": [{"message": {"content": '{"ok": true}'}}],
                               "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        llm = OpenAICompat(f"http://127.0.0.1:{server.server_port}/v1", "some-model", api_key="k", seed=7,
                           price_in=2.0, price_out=4.0)
        reply = llm.complete([{"role": "user", "content": "hi"}], "plan")
    finally:
        server.shutdown()
    assert seen["path"] == "/v1/chat/completions" and seen["auth"] == "Bearer k"
    assert seen["body"] == {"model": "some-model", "messages": [{"role": "user", "content": "hi"}],
                            "temperature": 0.0, "max_tokens": 2048, "seed": 7}
    assert (reply.text, reply.prompt_tokens, reply.completion_tokens) == ('{"ok": true}', 1000, 500)
    assert reply.cost == pytest.approx(0.004)
    assert llm.info()["model"] == "some-model"


def test_note_selection_recent_keyword_recall_and_titles(tmp_path):
    notes = Notes(tmp_path / "notes.jsonl")
    for i in range(12):
        topic = "projector" if i == 1 else f"topic{i}"
        notes.add({"kind": "world", "title": f"{topic} title", "text": f"about {topic}", "confidence": 0.5}, "r", i)
    shown, titles = notes.select("find a projector for the demo", recall=["n3"])
    assert [n["id"] for n in shown] == ["n2", "n3", "n9", "n10", "n11", "n12"]  # match, recall, 4 most recent
    assert titles[0] == "n1: topic0 title" and len(titles) == 6
    notes.supersede("n12", "r", 99)
    reloaded = Notes(tmp_path / "notes.jsonl")
    assert "n12" not in reloaded.items and reloaded.count == 13
