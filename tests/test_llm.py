import unittest
import urllib.error

from lestinxor.llm import AnthropicLLM, OpenAICompatLLM, RetryingLLM, ScriptedLLM, is_retryable


class DummyLLM:
    def __init__(self, excs):
        self.excs = excs
        self.calls = 0

    def complete(self, system: str, prompt: str) -> str:
        self.calls += 1
        if self.excs:
            raise self.excs.pop(0)
        return "success"


class TestLLM(unittest.TestCase):
    def test_anthropic_no_model(self):
        with self.assertRaisesRegex(ValueError, "model is required"):
            AnthropicLLM(model="", api_key="test")

    def test_anthropic_no_api_key(self):
        with self.assertRaisesRegex(ValueError, "ANTHROPIC_API_KEY is not set"):
            AnthropicLLM(model="test", api_key="")

    def test_openai_no_model(self):
        with self.assertRaisesRegex(ValueError, "model is required"):
            OpenAICompatLLM(model="", base_url="http://test")

    def test_openai_no_base_url(self):
        with self.assertRaisesRegex(ValueError, "base_url is required"):
            OpenAICompatLLM(model="test", base_url="")

    def test_retrying_llm_zero_attempts(self):
        with self.assertRaisesRegex(ValueError, "attempts must be >= 1"):
            RetryingLLM(inner=ScriptedLLM(["a"]), attempts=0)

    def test_retrying_llm_success(self):
        llm = RetryingLLM(inner=ScriptedLLM(["a"]))
        self.assertEqual(llm.complete("s", "p"), "a")

    def test_retrying_llm_retry_and_success(self):
        exc = urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None)
        dummy = DummyLLM([exc, exc])
        llm = RetryingLLM(inner=dummy, attempts=4, base_delay=0.01, sleep=lambda t: None)
        self.assertEqual(llm.complete("s", "p"), "success")
        self.assertEqual(dummy.calls, 3)

    def test_retrying_llm_retry_exhausted(self):
        exc = urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None)
        dummy = DummyLLM([exc, exc, exc])
        llm = RetryingLLM(inner=dummy, attempts=2, base_delay=0.01, sleep=lambda t: None)
        with self.assertRaises(urllib.error.HTTPError):
            llm.complete("s", "p")
        self.assertEqual(dummy.calls, 2)

    def test_retrying_llm_non_retryable(self):
        exc = ValueError("bad")
        dummy = DummyLLM([exc])
        llm = RetryingLLM(inner=dummy, attempts=4, base_delay=0.01, sleep=lambda t: None)
        with self.assertRaises(ValueError):
            llm.complete("s", "p")
        self.assertEqual(dummy.calls, 1)

    def test_scripted_llm_queue_exhausted(self):
        llm = ScriptedLLM([])
        with self.assertRaisesRegex(RuntimeError, "ScriptedLLM exhausted"):
            llm.complete("s", "p")

    def test_is_retryable_url_error(self):
        self.assertTrue(is_retryable(urllib.error.URLError("reason")))
        self.assertTrue(is_retryable(TimeoutError()))
        self.assertTrue(is_retryable(ConnectionError()))

    def test_is_retryable_http_error(self):
        self.assertTrue(is_retryable(urllib.error.HTTPError("url", 429, "msg", {}, None)))
        self.assertTrue(is_retryable(urllib.error.HTTPError("url", 500, "msg", {}, None)))
        self.assertTrue(is_retryable(urllib.error.HTTPError("url", 503, "msg", {}, None)))
        self.assertFalse(is_retryable(urllib.error.HTTPError("url", 400, "msg", {}, None)))
        self.assertFalse(is_retryable(urllib.error.HTTPError("url", 404, "msg", {}, None)))

    def test_unreachable(self):
        llm = RetryingLLM(inner=ScriptedLLM(["a"]), attempts=1)
        llm.attempts = 0  # force loop skip
        with self.assertRaisesRegex(RuntimeError, "unreachable"):
            llm.complete("s", "p")


if __name__ == "__main__":
    unittest.main()
