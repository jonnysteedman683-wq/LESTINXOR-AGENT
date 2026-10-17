"""Model backends. Anything with `complete(system, prompt) -> str` is an LLM."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any, Protocol, Union


class LLM(Protocol):
    def complete(self, system: str, prompt: str) -> str: ...


Script = Union[Sequence[str], Callable[[str, str], str]]


class ScriptedLLM:
    """Deterministic test double: a queue of replies or a function(system, prompt) -> str."""

    def __init__(self, script: Script) -> None:
        self._fn: Callable[[str, str], str] | None = script if callable(script) else None
        self._queue: list[str] = [] if callable(script) else list(script)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        if self._fn is not None:
            return self._fn(system, prompt)
        if not self._queue:
            raise RuntimeError("ScriptedLLM exhausted")
        return self._queue.pop(0)


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float) -> Any:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"content-type": "application/json", **headers},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class AnthropicLLM:
    """Zero-dependency Messages API client. `model` is required — no stale defaults."""

    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str, api_key: str | None = None, max_tokens: int = 4096, timeout: float = 120.0) -> None:
        if not model:
            raise ValueError("model is required — pass the exact model string")
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set")
        self.max_tokens = max_tokens
        self.timeout = timeout

    def complete(self, system: str, prompt: str) -> str:
        data = _post_json(
            self.URL,
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            },
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            self.timeout,
        )
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


class OpenAICompatLLM:
    """Any OpenAI-compatible /chat/completions endpoint (vLLM, Ollama, OpenRouter, Gemini-compat, ...)."""

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> None:
        if not model:
            raise ValueError("model is required — pass the exact model string")
        if not base_url:
            raise ValueError("base_url is required")
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key or os.environ.get(api_key_env, "")
        self.max_tokens = max_tokens
        self.timeout = timeout

    def complete(self, system: str, prompt: str) -> str:
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        data = _post_json(
            self.url,
            {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            },
            headers,
            self.timeout,
        )
        return str(data["choices"][0]["message"].get("content") or "")


RETRYABLE_HTTP = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRYABLE_HTTP
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError))


class RetryingLLM:
    """Wraps any LLM with deterministic exponential backoff on transient failures."""

    def __init__(
        self,
        inner: LLM,
        attempts: int = 4,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        retryable: Callable[[BaseException], bool] = is_retryable,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be >= 1")
        self.inner = inner
        self.attempts = attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.sleep = sleep
        self.retryable = retryable

    def complete(self, system: str, prompt: str) -> str:
        for n in range(1, self.attempts + 1):
            try:
                return self.inner.complete(system, prompt)
            except Exception as exc:
                if n >= self.attempts or not self.retryable(exc):
                    raise
                self.sleep(min(self.max_delay, self.base_delay * 2 ** (n - 1)))
        raise RuntimeError("unreachable")
