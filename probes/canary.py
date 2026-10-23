"""Canary strings: random tokens planted in the environment as the true values of facts, so that a judge can tell
mechanically whether a value the agent reports came from an observation or was made up.

A canary is PREFIX-<10 hex digits>, derived from (seed, cell, label): the same seed gives the same environment, so a
run can be replayed exactly. Use a fresh seed for every real run so that no canary can be known in advance.
Texts mark where a canary goes with {canary:label}.
"""
from __future__ import annotations

import hashlib
import re

SLOT = re.compile(r"\{canary:([a-z0-9_]+)\}")


def make(seed: int, scope: str, label: str, prefix: str) -> str:
    return f"{prefix}-{hashlib.sha256(f'{seed}/{scope}/{label}'.encode()).hexdigest()[:10]}"


def pattern(prefix: str) -> re.Pattern:
    """Matches every answer-shaped token of a cell: planted canaries and made-up look-alikes."""
    return re.compile(rf"\b{re.escape(prefix)}-[0-9a-f]{{10}}\b")


def render(value, values: dict[str, str]):
    """Fill {canary:label} slots in a string, or in every string inside a dict or list."""
    if isinstance(value, str):
        def fill(m):
            if m.group(1) not in values:
                raise ValueError(f"unknown canary '{m.group(1)}'")
            return values[m.group(1)]
        return SLOT.sub(fill, value)
    if isinstance(value, dict):
        return {k: render(v, values) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [render(v, values) for v in value]
    return value
