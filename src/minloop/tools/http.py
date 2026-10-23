"""http_get. OFF by default: the charter must list it in "tools" and set "allow": {"network": true}."""
from __future__ import annotations

import urllib.request

from .base import ToolResult, tool

MAX_BYTES = 200_000


@tool("http_get", "Fetch a URL with HTTP GET and return the body as text (truncated to 200 kB).",
      {"properties": {"url": {"type": "string", "minLength": 1}}, "required": ["url"]},
      side_effect="external", timeout=30, requires="network")
def http_get(args, ctx):
    url = args["url"]
    if not url.startswith(("http://", "https://")):
        raise ValueError("only http:// and https:// URLs are supported")
    request = urllib.request.Request(url, headers={"User-Agent": "minloop"})
    with urllib.request.urlopen(request, timeout=25) as response:
        body = response.read(MAX_BYTES + 1)
        charset = response.headers.get_content_charset() or "utf-8"
        return ToolResult(True, body[:MAX_BYTES].decode(charset, errors="replace"),
                          {"status": response.status, "truncated": len(body) > MAX_BYTES})
