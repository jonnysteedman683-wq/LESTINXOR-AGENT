"""Offline tests for HTTP backends, trace persistence and the CLI (urlopen is patched)."""

import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from lestinxor import AnthropicLLM, OpenAICompatLLM, ScriptedLLM, Trace
from lestinxor.cli import main
from lestinxor.scoring import SCORE_SYSTEM
from lestinxor.stages import JUDGE_SYSTEM


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(reply_for):
    """reply_for(url, body) -> dict; records requests on the returned mock."""

    def _open(req, timeout=None):
        body = json.loads(req.data)
        _open.requests.append((req.full_url, dict(req.header_items()), body))
        return _Resp(json.dumps(reply_for(req.full_url, body)).encode())

    _open.requests = []
    return _open


def anthropic_reply(text):
    return {"content": [{"type": "text", "text": text}]}


def smart_model(url, body):
    """Plays every role: judge passes, scorer scores, stages produce text."""
    system = body.get("system") or body["messages"][0]["content"]
    if system == JUDGE_SYSTEM:
        text = '{"verdict": "pass", "reason": "ok"}'
    elif system == SCORE_SYSTEM:
        text = '{"score": 0.97, "critique": "solid"}'
    else:
        m = re.search(r"APPROACH DIRECTIVE \((\w+)\)", json.dumps(body))
        text = f"answer via {m.group(1) if m else 'synthesis'}"
    if "chat/completions" in url:
        return {"choices": [{"message": {"content": text}}]}
    return anthropic_reply(text)


class BackendTests(unittest.TestCase):
    def test_anthropic_request_shape(self):
        op = fake_urlopen(lambda u, b: anthropic_reply("hi"))
        with mock.patch("urllib.request.urlopen", op):
            out = AnthropicLLM("model-x", api_key="k").complete("sys", "prompt")
        self.assertEqual(out, "hi")
        url, headers, body = op.requests[0]
        self.assertEqual(url, AnthropicLLM.URL)
        self.assertEqual(headers["X-api-key"], "k")
        self.assertEqual(body["model"], "model-x")
        self.assertEqual(body["system"], "sys")

    def test_openai_compat_request_shape(self):
        op = fake_urlopen(lambda u, b: {"choices": [{"message": {"content": "yo"}}]})
        with mock.patch("urllib.request.urlopen", op):
            out = OpenAICompatLLM("m", "http://local:8000/v1/", api_key="k").complete("s", "p")
        self.assertEqual(out, "yo")
        url, headers, body = op.requests[0]
        self.assertEqual(url, "http://local:8000/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer k")
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])

    def test_openai_compat_without_key_sends_no_auth(self):
        op = fake_urlopen(lambda u, b: {"choices": [{"message": {"content": None}}]})
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("urllib.request.urlopen", op):
            self.assertEqual(OpenAICompatLLM("m", "http://local").complete("s", "p"), "")
        self.assertNotIn("Authorization", op.requests[0][1])

    def test_backends_require_model_and_config(self):
        with self.assertRaises(ValueError):
            AnthropicLLM("", api_key="k")
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            AnthropicLLM("m")
        with self.assertRaises(ValueError):
            OpenAICompatLLM("m", "")

    def test_scripted_queue(self):
        llm = ScriptedLLM(["a"])
        self.assertEqual(llm.complete("s", "p"), "a")
        with self.assertRaises(RuntimeError):
            llm.complete("s", "p")


class TraceFileTests(unittest.TestCase):
    def test_jsonl_persisted(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "t.jsonl"
            t = Trace(path=str(path))
            t.emit("a", x=1)
            t.emit("b")
            lines = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual([e["kind"] for e in lines], ["a", "b"])


class CLITests(unittest.TestCase):
    def run_cli(self, *args, provider_url=None):
        op = fake_urlopen(smart_model)
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("urllib.request.urlopen", op),
            mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}),
            redirect_stdout(out),
            redirect_stderr(err),
        ):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue(), op.requests

    def test_orchestrate_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            graph, mmd, trace = Path(d) / "g.json", Path(d) / "g.mmd", Path(d) / "t.jsonl"
            code, out, err, reqs = self.run_cli(
                "orchestrate",
                "solve it",
                "--model",
                "work-m",
                "--judge-model",
                "judge-m",
                "--workers",
                "1",
                "--graph",
                str(graph),
                "--mermaid",
                str(mmd),
                "--trace",
                str(trace),
            )
            self.assertEqual(code, 0, err)
            self.assertIn("answer via", out)
            self.assertIn("converged: target", err)
            self.assertTrue(json.loads(graph.read_text())["nodes"])
            self.assertTrue(mmd.read_text().startswith("graph TD"))
            self.assertTrue(trace.read_text())
        models = {body["model"] for _, _, body in reqs}
        self.assertEqual(models, {"work-m", "judge-m"})

    def test_gauntlet_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            code, _out, err, _ = self.run_cli(
                "gauntlet", "solve it", "--model", "m", "--unattended", "--trace", str(Path(d) / "t.jsonl")
            )
        self.assertEqual(code, 0, err)
        self.assertIn("[completed]", err)

    def test_openai_provider_requires_base_url(self):
        with self.assertRaises(SystemExit):
            self.run_cli("gauntlet", "t", "--model", "m", "--provider", "openai")

    def test_openai_provider(self):
        with tempfile.TemporaryDirectory() as d:
            code, _, err, reqs = self.run_cli(
                "gauntlet",
                "t",
                "--model",
                "m",
                "--provider",
                "openai",
                "--base-url",
                "http://local/v1",
                "--unattended",
                "--trace",
                str(Path(d) / "t.jsonl"),
            )
        self.assertEqual(code, 0, err)
        self.assertTrue(all(url.endswith("/chat/completions") for url, _, _ in reqs))

    def test_tool_flags(self):
        with tempfile.TemporaryDirectory() as d:
            ws, t = Path(d) / "ws", str(Path(d) / "t.jsonl")
            code, _, err, _ = self.run_cli(
                "gauntlet",
                "t",
                "--model",
                "m",
                "--unattended",
                "--trace",
                t,
                "--workspace",
                str(ws),
                "--allow-exec",
                "--check",
                "python -c pass",
            )
            self.assertEqual(code, 0, err)
            self.assertTrue(ws.is_dir())
            code, _, err, _ = self.run_cli(
                "orchestrate",
                "t",
                "--model",
                "m",
                "--workers",
                "1",
                "--trace",
                t,
                "--workspace",
                str(Path(d) / "runs"),
                "--allow-exec",
                "--check",
                "python -c pass",
            )
            self.assertEqual(code, 0, err)
            self.assertIn("workspace=", err)
            for bad in (["--check", "x"], ["--allow-exec"]):
                with self.assertRaises(SystemExit):
                    self.run_cli("gauntlet", "t", "--model", "m", "--trace", t, *bad)


if __name__ == "__main__":
    unittest.main()
