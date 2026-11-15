import io
import urllib.error

import pytest

from lestinxor.llm import OpenAICompatLLM, RetryingLLM, ScriptedLLM, is_retryable


def test_openaicompatllm_empty_model():
    """Test that OpenAICompatLLM raises ValueError when model is empty."""
    with pytest.raises(ValueError, match="model is required — pass the exact model string"):
        OpenAICompatLLM(model="", base_url="http://localhost")


def test_retryingllm_invalid_attempts():
    """Test that RetryingLLM raises ValueError when attempts < 1."""
    inner = ScriptedLLM([])
    with pytest.raises(ValueError, match="attempts must be >= 1"):
        RetryingLLM(inner, attempts=0)


def test_retryingllm_unreachable():
    """Test that RetryingLLM.complete hits the unreachable RuntimeError."""
    inner = ScriptedLLM([])
    llm = RetryingLLM(inner, attempts=1)
    # Force the loop condition to fail immediately by setting attempts to 0 after initialization
    llm.attempts = 0
    with pytest.raises(RuntimeError, match="unreachable"):
        llm.complete("system", "prompt")


def test_retryingllm_success_after_retries():
    """Test that RetryingLLM retries on retryable exceptions and returns successfully."""
    sleeps = []

    class FailingLLM:
        def __init__(self):
            self.calls = 0

        def complete(self, system, prompt):
            self.calls += 1
            if self.calls < 3:
                raise TimeoutError("timeout")
            return "success"

    llm = RetryingLLM(FailingLLM(), attempts=3, base_delay=1.0, sleep=sleeps.append)
    result = llm.complete("sys", "prompt")
    assert result == "success"
    assert len(sleeps) == 2
    assert sleeps == [1.0, 2.0]


def test_retryingllm_exhausts_attempts():
    """Test that RetryingLLM raises the last exception after exhausting attempts."""
    sleeps = []

    class FailingLLM:
        def complete(self, system, prompt):
            raise ConnectionError("conn error")

    llm = RetryingLLM(FailingLLM(), attempts=2, base_delay=1.0, sleep=sleeps.append)
    with pytest.raises(ConnectionError, match="conn error"):
        llm.complete("sys", "prompt")
    assert len(sleeps) == 1


def test_retryingllm_does_not_catch_baseexception():
    """Test that RetryingLLM lets BaseException (like KeyboardInterrupt) propagate immediately."""

    class FailingLLM:
        def complete(self, system, prompt):
            raise KeyboardInterrupt()

    llm = RetryingLLM(FailingLLM(), attempts=3)
    with pytest.raises(KeyboardInterrupt):
        llm.complete("sys", "prompt")


def test_is_retryable():
    """Test the is_retryable function with various exceptions."""

    # Retryable HTTP errors
    err_500 = urllib.error.HTTPError("url", 500, "Internal Server Error", {}, io.BytesIO())
    assert is_retryable(err_500) is True

    err_429 = urllib.error.HTTPError("url", 429, "Too Many Requests", {}, io.BytesIO())
    assert is_retryable(err_429) is True

    # Non-retryable HTTP error
    err_404 = urllib.error.HTTPError("url", 404, "Not Found", {}, io.BytesIO())
    assert is_retryable(err_404) is False

    # Other retryable errors
    assert is_retryable(TimeoutError()) is True
    assert is_retryable(ConnectionError()) is True
    assert is_retryable(urllib.error.URLError("reason")) is True

    # BaseException handling edge cases
    assert is_retryable(KeyboardInterrupt()) is False
    assert is_retryable(ValueError()) is False
