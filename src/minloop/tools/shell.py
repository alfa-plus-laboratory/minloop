"""shell. OFF by default: the charter must list it in "tools" and set "allow": {"shell": true}.

The command runs with your user's permissions. The workspace is only its working directory,
not a sandbox: run inside a container or VM if you enable this.
"""
from __future__ import annotations

import subprocess

from .base import ToolResult, tool


@tool("shell", "Run a shell command with the workspace as working directory; returns stdout and stderr.",
      {"properties": {"command": {"type": "string", "minLength": 1}}, "required": ["command"]},
      side_effect="external", timeout=60, requires="shell")
def shell(args, ctx):
    done = subprocess.run(args["command"], shell=True, cwd=ctx.workspace, capture_output=True, text=True,
                          timeout=55)
    output = done.stdout + (f"\n[stderr]\n{done.stderr}" if done.stderr else "")
    error = None if done.returncode == 0 else f"exit_status: command exited with {done.returncode}"
    return ToolResult(done.returncode == 0, output, {"returncode": done.returncode}, error)
