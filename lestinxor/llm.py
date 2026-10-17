"""Model backends. Anything with `complete(system, prompt) -> str` works."""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Callable, Protocol, Sequence, Union


class LLM(Protocol):
    def complete(self, system: str, prompt: str) -> str: ...


Script = Union[Sequence[str], Callable[[str, str], str]]


class ScriptedLLM:
    """Deterministic test double: a queue of replies or a function(system, prompt) -> str."""

    def __init__(self, script: Script) -> None:
        self._fn = script if callable(script) else None
        self._queue = [] if callable(script) else list(script)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        if self._fn is not None:
            return self._fn(system, prompt)
        if not self._queue:
            raise RuntimeError("ScriptedLLM exhausted")
        return self._queue.pop(0)


class AnthropicLLM:
    """Zero-dependency Messages API client. `model` is required — no stale defaults."""

    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, api_key: str | None = None,
                 max_tokens: int = 4096, timeout: float = 120.0) -> None:
        if not model:
            raise ValueError("model is required — pass the exact model string")
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        self.max_tokens = max_tokens
        self.timeout = timeout

    def complete(self, system: str, prompt: str) -> str:
        body = json.dumps({
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(self.URL, data=body, method="POST", headers={
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        })
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.load(resp)
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
