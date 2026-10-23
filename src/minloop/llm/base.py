"""LLM adapter interface. Adapters only move text; prompt design lives in prompts/."""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class Reply:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0


class LLM:
    name = "base"
    priced = False  # True when replies carry a real cost, so a max_cost budget can bind

    def info(self) -> dict:
        """Adapter, model name, decoding parameters and seed; recorded in run_start."""
        return {"adapter": self.name}

    def complete(self, messages: list[dict], kind: str) -> Reply:
        """kind is the caller: 'goal', 'plan', 'reflect' or 'deep'."""
        raise NotImplementedError


def encode(messages: list[dict]) -> str:
    """Canonical request text: what the trace stores and what replay compares."""
    return json.dumps(messages, ensure_ascii=False)


def parse_json(text: str):
    """First JSON object in a reply; tolerates surrounding prose, code fences and reasoning before </think>."""
    text = text.rsplit("</think>", 1)[-1]
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object found in the reply")
    return json.JSONDecoder(parse_constant=_not_json).raw_decode(text, start)[0]


def _not_json(name: str):
    raise ValueError(f"{name} is not valid JSON")
