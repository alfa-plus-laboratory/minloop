"""Any OpenAI-compatible /chat/completions endpoint (llama.cpp, vLLM, Ollama, most cloud APIs), stdlib only."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .base import LLM, Reply


class OpenAICompat(LLM):
    name = "openai_compat"

    def __init__(self, base_url: str, model: str, api_key: str | None = None, temperature: float = 0.0,
                 max_tokens: int = 2048, seed: int | None = None, timeout: float = 300,
                 price_in: float = 0.0, price_out: float = 0.0, retries: int = 2):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.api_key, self.timeout, self.retries = model, api_key, timeout, retries
        self.params = {"temperature": temperature, "max_tokens": max_tokens}
        if seed is not None:
            self.params["seed"] = seed
        self.price_in, self.price_out = price_in, price_out  # currency units per million tokens
        self.priced = bool(price_in or price_out)

    def info(self) -> dict:
        return {"adapter": self.name, "model": self.model, **self.params,
                "price_per_mtok": [self.price_in, self.price_out]}

    def complete(self, messages, kind):
        body = json.dumps({"model": self.model, "messages": messages, **self.params}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        for attempt in range(self.retries + 1):
            try:
                request = urllib.request.Request(self.url, data=body, headers=headers)
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.load(response)
                break
            except urllib.error.HTTPError as e:  # retry only rate limits and server errors
                if attempt == self.retries or (e.code < 500 and e.code != 429):
                    raise RuntimeError(f"HTTP {e.code} from endpoint: {e.read()[:500]!r}") from None
            except (urllib.error.URLError, TimeoutError):
                if attempt == self.retries:
                    raise
            time.sleep(2 ** attempt)
        text = data["choices"][0]["message"].get("content") or ""
        usage = data.get("usage") or {}  # some servers omit it: estimate (chars / 4) so token budgets still bind
        tokens_in = usage.get("prompt_tokens") or len(body) // 4
        tokens_out = usage.get("completion_tokens") or len(text) // 4
        return Reply(text, tokens_in, tokens_out, (tokens_in * self.price_in + tokens_out * self.price_out) / 1e6)
