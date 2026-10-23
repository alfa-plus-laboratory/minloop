"""Workspace file tools. Every path is confined to the run's workspace directory.

This is a path check, not a security boundary: it stops honest mistakes, not a determined process.
"""
from __future__ import annotations

import os
from pathlib import Path

from .base import ToolResult, tool

READ_CHARS = 3000
MAX_WRITE = 200_000
_PATH = {"type": "string", "minLength": 1}


def _resolve(ctx, rel: str) -> Path:
    root = os.path.realpath(ctx.workspace)
    full = os.path.realpath(os.path.join(root, rel))
    if os.path.commonpath([root, full]) != root:
        raise PermissionError(f"'{rel}' is outside the workspace")
    return Path(full)


@tool("fs_list", "List files and directories under a workspace path (default: the workspace root).",
      {"properties": {"path": _PATH}})
def fs_list(args, ctx):
    rel = args.get("path", ".")
    folder = _resolve(ctx, rel)
    if not folder.is_dir():
        raise NotADirectoryError(f"'{rel}' is not a directory")
    entries = [f"{p.name}/" if p.is_dir() else f"{p.name}  ({p.stat().st_size} bytes)"
               for p in sorted(folder.iterdir())]
    return ToolResult(True, "\n".join(entries) or "(empty)", {"entries": len(entries)})


@tool("fs_read", f"Read a text file. Returns at most `limit` characters (default {READ_CHARS}) from `offset`; "
      "read large files in chunks.",
      {"properties": {"path": _PATH, "offset": {"type": "integer", "minimum": 0},
                      "limit": {"type": "integer", "minimum": 1}}, "required": ["path"]})
def fs_read(args, ctx):
    file = _resolve(ctx, args["path"])
    if not file.is_file():
        raise FileNotFoundError(f"'{args['path']}' does not exist or is not a file")
    text = file.read_text(encoding="utf-8", errors="replace")
    start, limit = args.get("offset", 0), args.get("limit", READ_CHARS)
    chunk = text[start:start + limit]
    if start or len(text) > start + limit:
        chunk += f"\n[chars {start}-{start + len(chunk)} of {len(text)}]"
    return ToolResult(True, chunk, {"chars": len(text)})


@tool("fs_write", "Write a text file in the workspace (mode 'overwrite' by default, or 'append'). "
      "Creates parent directories.",
      {"properties": {"path": _PATH, "content": {"type": "string"}, "mode": {"enum": ["overwrite", "append"]}},
       "required": ["path", "content"]}, side_effect="local")
def fs_write(args, ctx):
    if len(args["content"]) > MAX_WRITE:
        raise ValueError(f"content is longer than {MAX_WRITE} characters")
    file = _resolve(ctx, args["path"])
    file.parent.mkdir(parents=True, exist_ok=True)
    with open(file, "a" if args.get("mode") == "append" else "w", encoding="utf-8", newline="\n") as f:
        f.write(args["content"])
    return ToolResult(True, f"wrote {len(args['content'])} characters to {args['path']}")
