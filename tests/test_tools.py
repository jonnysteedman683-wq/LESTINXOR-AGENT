import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lestinxor import (
    ConvergencePolicy,
    FnScorer,
    Gate,
    Gauntlet,
    ManualClock,
    Op,
    Orchestrator,
    OrchestratorConfig,
    ScriptedLLM,
    Stage,
    Tool,
    Toolbox,
    ToolResult,
    Trace,
    Verdict,
    chain,
    command_gate,
    parse_tool_call,
    run_with_tools,
    sandbox_toolbox,
    with_gate,
    workspace_factory,
)
from lestinxor.contracts import Context

ALL = ("fs.read", "fs.write", "exec")


def call(name, **args):
    return f"```tool\n{json.dumps({'name': name, 'args': args})}\n```"


def tmp():
    d = tempfile.TemporaryDirectory()
    return d, Path(d.name)


class ToolSchemaTests(unittest.TestCase):
    def test_inferred_schema_and_validation(self):
        def add(a: int, b: int = 2, label: str = "") -> int:
            """Add two integers.

            Longer description ignored."""
            return a + b

        t = Tool.from_function(add, scopes={"math"})
        spec = t.spec()
        self.assertEqual(spec["description"], "Add two integers.")
        self.assertEqual(spec["parameters"]["required"], ["a"])
        self.assertEqual(spec["parameters"]["properties"]["b"]["type"], "integer")
        self.assertIsNone(t.validate({"a": 1}))
        self.assertIn("missing", t.validate({}))
        self.assertIn("unknown", t.validate({"a": 1, "z": 0}))
        self.assertIn("must be integer", t.validate({"a": "1"}))
        self.assertIn("must be integer", t.validate({"a": True}))  # bool is not an int here

    def test_unsupported_annotation_rejected(self):
        def bad(x: set) -> None: ...

        with self.assertRaises(TypeError):
            Tool.from_function(bad)

    def test_decorator_and_duplicates(self):
        box = Toolbox(granted=["math"])

        @box.tool(scopes={"math"})
        def double(x: int) -> int:
            return x * 2

        self.assertEqual(box.call("double", {"x": 4}), ToolResult(True, "8"))
        with self.assertRaises(ValueError):
            box.add(Tool.from_function(double))


class ToolboxPolicyTests(unittest.TestCase):
    def setUp(self):
        def boom() -> str:
            raise RuntimeError("kaboom")

        def big() -> str:
            return "x" * 5000

        def secret() -> str:
            return "s"

        self.box = Toolbox(
            [Tool.from_function(boom), Tool.from_function(big), Tool.from_function(secret, scopes={"admin"})],
            max_calls=4,
            output_limit=200,
        )

    def test_scopes_and_stage_allowlist(self):
        self.assertIn("needs scope", self.box.call("secret", {}).error)
        self.assertIn("not allowed in this stage", self.box.call("big", {}, allowed=["boom"]).error)
        self.assertIn("unknown tool", self.box.call("nope", {}).error)
        self.assertEqual([s["name"] for s in self.box.specs()], ["big", "boom"])

    def test_errors_never_raise_and_output_is_capped(self):
        self.assertEqual(self.box.call("boom", {}).error, "RuntimeError: kaboom")
        r = self.box.call("big", {})
        self.assertTrue(r.ok)
        self.assertLess(len(r.output), 300)
        self.assertIn("truncated", r.output)
        self.assertIn("must be an object", self.box.call("big", ["x"]).error)

    def test_budget(self):
        for _ in range(4):
            self.box.call("big", {})
        self.assertIn("budget exhausted", self.box.call("big", {}).error)


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self._d, self.root = tmp()
        self.box = sandbox_toolbox(self.root / "ws", scopes=ALL, timeout=5)

    def tearDown(self):
        self._d.cleanup()

    def test_fs_roundtrip(self):
        self.assertTrue(self.box.call("write_file", {"path": "pkg/a.py", "content": "X = 1\n"}).ok)
        self.assertEqual(self.box.call("read_file", {"path": "pkg/a.py"}).output, "X = 1\n")
        self.assertEqual(self.box.call("list_files", {}).output, "pkg/a.py")

    def test_fs_tools_file_size_limits(self):
        from lestinxor.sandbox import Workspace, fs_tools

        ws = Workspace(self.root / "ws")
        tools = fs_tools(ws, max_file_bytes=10)
        write_file = next(t.fn for t in tools if t.name == "write_file")
        read_file = next(t.fn for t in tools if t.name == "read_file")
        with self.assertRaises(ValueError) as exc:
            write_file("big.txt", "12345678901")
        self.assertIn("content exceeds 10 bytes", str(exc.exception))

        write_file("ok.txt", "1234")
        with open(ws.resolve("ok.txt"), "a") as f:
            f.write("5678901")
        with self.assertRaises(ValueError) as exc2:
            read_file("ok.txt")
        self.assertIn("file exceeds 10 bytes", str(exc2.exception))

    def test_path_escapes_refused(self):
        (self.root / "outside.txt").write_text("secret")
        os.symlink(self.root / "outside.txt", self.root / "ws" / "link.txt")
        for path in ("../outside.txt", str(self.root / "outside.txt"), "link.txt"):
            r = self.box.call("read_file", {"path": path})
            self.assertFalse(r.ok, path)
            self.assertIn("escapes workspace", r.error)
        self.assertFalse(self.box.call("write_file", {"path": "../x", "content": "x"}).ok)

    def test_exec_is_opt_in(self):
        box = sandbox_toolbox(self.root / "ws2")
        self.assertIn("needs scope(s) ['exec']", box.call("run_python", {"code": "print(1)"}).error)

    def test_run_python_and_exit_codes(self):
        ok = self.box.call("run_python", {"code": "print('hi')"})
        self.assertTrue(ok.ok)
        self.assertIn("exit 0", ok.output)
        self.assertIn("hi", ok.output)
        bad = self.box.call("run_python", {"code": "raise SystemExit(3)"})
        self.assertFalse(bad.ok)
        self.assertEqual(bad.error, "exit code 3")

    def test_secrets_do_not_leak_into_tools(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-secret"}):
            r = self.box.call("run_python", {"code": "import os; print(repr(os.environ.get('ANTHROPIC_API_KEY')))"})
        self.assertIn("None", r.output)
        self.assertNotIn("sk-secret", r.output)

    def test_cwd_is_workspace_and_imports_work(self):
        self.box.call("write_file", {"path": "solution.py", "content": "def add(a, b):\n    return a + b\n"})
        r = self.box.call("run_command", {"argv": ["python", "-c", "import solution; print(solution.add(2, 3))"]})
        self.assertTrue(r.ok, r.output)
        self.assertIn("5", r.output)

    def test_same_size_rewrite_is_not_served_from_stale_bytecode(self):
        run = ["python", "-c", "import m; print(m.f())"]
        self.box.call("write_file", {"path": "m.py", "content": "def f():\n    return 1 - 1\n"})
        self.assertIn("\n0", self.box.call("run_command", {"argv": run}).output)
        self.box.call("write_file", {"path": "m.py", "content": "def f():\n    return 1 + 1\n"})
        self.assertIn("\n2", self.box.call("run_command", {"argv": run}).output)

    def test_command_allowlist(self):
        r = self.box.call("run_command", {"argv": ["rm", "-rf", "/"]})
        self.assertIn("not allowed", r.error)
        self.assertIn("non-empty", self.box.call("run_command", {"argv": []}).error)

    def test_timeout_kills(self):
        box = sandbox_toolbox(self.root / "ws3", scopes=ALL, timeout=0.5)
        r = box.call("run_python", {"code": "import time; time.sleep(30)"})
        self.assertFalse(r.ok)
        self.assertIn("timed out", r.error)

    def test_kill_tree_exceptions(self):
        import subprocess

        from lestinxor.sandbox import _kill_tree

        proc = mock.Mock(spec=subprocess.Popen)
        proc.pid = 12345
        proc.kill.side_effect = PermissionError("mock perm error")

        with mock.patch("os.name", "nt"):
            _kill_tree(proc)
            proc.kill.assert_called_once()

        with mock.patch("os.name", "posix"):
            with mock.patch("os.killpg", side_effect=ProcessLookupError("mock lookup error")) as killpg_mock:
                _kill_tree(proc)
                killpg_mock.assert_called_once()

    def test_run_sandboxed_oserror(self):
        from lestinxor.sandbox import Workspace, run_sandboxed

        ws = Workspace(self.root / "ws4")
        with mock.patch("subprocess.Popen", side_effect=OSError("mock os error")):
            r = run_sandboxed(["python", "-c", "print(1)"], ws)
            self.assertFalse(r.ok)
            self.assertIn("OSError: mock os error", r.error)

    def test_allow_command_not_found(self):
        from lestinxor.sandbox import allow_command

        with self.assertRaises(FileNotFoundError) as exc:
            allow_command("this-cmd-does-not-exist-12345")
        self.assertIn("not found on PATH", str(exc.exception))

        # Test finding python
        res = allow_command("python")
        self.assertTrue(len(res) == 1)
        self.assertTrue(res[0].endswith("python") or res[0].endswith("python.exe") or res[0].endswith("python3"))

    def test_workspace_factory_forks_parent(self):
        make = workspace_factory(self.root / "runs")
        parent = make("t0000", ())
        parent.call("write_file", {"path": "a.txt", "content": "parent"})
        child = make("t0001", ("t0000",))
        self.assertEqual(child.call("read_file", {"path": "a.txt"}).output, "parent")
        child.call("write_file", {"path": "a.txt", "content": "child"})
        self.assertEqual(parent.call("read_file", {"path": "a.txt"}).output, "parent")  # isolated


class ToolLoopTests(unittest.TestCase):
    def setUp(self):
        self._d, root = tmp()
        self.box = sandbox_toolbox(root, scopes=ALL, timeout=5)

    def tearDown(self):
        self._d.cleanup()

    def test_parse(self):
        self.assertIsNone(parse_tool_call("just an answer"))
        self.assertEqual(parse_tool_call(call("x", a=1)), ("x", {"a": 1}))
        self.assertIn("malformed", parse_tool_call("```tool\n{nope\n```").error)
        self.assertIn("must be", parse_tool_call('```tool\n{"args": {}}\n```').error)
        self.assertIn("object", parse_tool_call('```tool\n{"name": "x", "args": 3}\n```').error)

    def test_loop_executes_and_returns_final(self):
        llm = ScriptedLLM(
            [
                "I'll write it. " + call("write_file", path="f.py", content="print(40 + 2)"),
                call("run_command", argv=["python", "f.py"]),
                "```tool\n{broken\n```",
                "Done: prints 42.",
            ]
        )
        seen = []
        text, calls = run_with_tools(llm, "sys", "do it", self.box, on_call=seen.append)
        self.assertEqual(text, "Done: prints 42.")
        self.assertEqual([c.name for c in calls], ["write_file", "run_command", "<invalid>"])
        self.assertIn("42", calls[1].result.output)
        self.assertEqual(len(seen), 3)
        self.assertIn("[TOOL RESULT: run_command]", llm.calls[2][1])  # results fed back
        self.assertIn("run_python", llm.calls[0][0])  # specs in system prompt

    def test_budget_forces_final_answer(self):
        llm = ScriptedLLM(lambda s, p: "Final." if "budget exhausted" in p else call("list_files"))
        text, calls = run_with_tools(llm, "sys", "p", self.box, max_calls=2)
        self.assertEqual((text, len(calls)), ("Final.", 2))

    def test_no_permitted_tools_is_plain_completion(self):
        llm = ScriptedLLM(["plain"])
        self.assertEqual(run_with_tools(llm, "s", "p", self.box, allowed=[]), ("plain", []))


class GateTests(unittest.TestCase):
    def setUp(self):
        self._d, root = tmp()
        self.box = sandbox_toolbox(root, scopes=ALL, timeout=10)
        self.ctx = Context("t", tools=self.box)
        self.stage = Stage("test", "x")

    def tearDown(self):
        self._d.cleanup()

    def run_gate(self, g, ctx=None):
        return g(ctx or self.ctx, self.stage, "", 1, ScriptedLLM([]))

    def test_command_gate(self):
        self.assertIs(self.run_gate(command_gate(["python", "-c", "pass"])).verdict, Verdict.PASS)
        g = self.run_gate(command_gate(["python", "-c", "print('boom'); raise SystemExit(1)"]))
        self.assertIs(g.verdict, Verdict.RETRY)
        self.assertIn("boom", g.reason)
        g = self.run_gate(command_gate(["python", "-c", "1/0"], on_fail=Verdict.REVERT, revert_to="plan"))
        self.assertEqual((g.verdict, g.revert_to), (Verdict.REVERT, "plan"))

    def test_command_gate_without_exec_escalates(self):
        ctx = Context("t", tools=sandbox_toolbox(self.box.workspace.root / "sub"))
        self.assertIs(self.run_gate(command_gate(["python"]), ctx).verdict, Verdict.HELP)
        self.assertIs(self.run_gate(command_gate(["python"]), Context("t")).verdict, Verdict.HELP)
        with self.assertRaises(ValueError):
            command_gate(["x"], on_fail=Verdict.REVERT)
        with self.assertRaises(ValueError):
            command_gate(["x"], on_fail=Verdict.PASS)

    def test_chain_and_with_gate(self):
        hits = []

        def judge(*a):
            hits.append(1)
            return Gate(Verdict.PASS, "judge ok")

        failing = chain(command_gate(["python", "-c", "raise SystemExit(1)"]), judge)
        self.assertIs(self.run_gate(failing).verdict, Verdict.RETRY)
        self.assertEqual(hits, [])  # short-circuited: judge never billed
        self.assertEqual(self.run_gate(chain(command_gate(["python", "-c", "pass"]), judge)).reason, "judge ok")
        stages = with_gate((Stage("a", "x"), Stage("test", "y")), "test", judge)
        self.assertIs(stages[1].gate, judge)
        self.assertIsNone(stages[0].gate)
        with self.assertRaises(ValueError):
            with_gate(stages, "missing", judge)
        with self.assertRaises(ValueError):
            chain()


def coder(system: str, prompt: str) -> str:
    """A scripted 'model' that writes code with tools, then reports."""
    if "[TOOL RESULT" in prompt:
        return "Implemented solution.py; see tool log."
    if "CURRENT STAGE: execute" in prompt:
        bug = "a - b" if "buggy" in prompt and "Operator" not in prompt and "failed" not in prompt else "a + b"
        return call("write_file", path="solution.py", content=f"def add(a, b):\n    return {bug}\n")
    return "ok"


CHECK = ["python", "-c", "import solution; assert solution.add(2, 3) == 5, 'add is wrong'"]


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self._d, self.root = tmp()

    def tearDown(self):
        self._d.cleanup()

    def stages(self):
        execute = Stage("execute", "Write solution.py", lambda *a: Gate(Verdict.PASS), tools=("write_file",))
        return with_gate(
            (execute, Stage("test", "Verify")), "test", command_gate(CHECK, on_fail=Verdict.REVERT, revert_to="execute")
        )

    def test_gauntlet_real_check_reverts_until_code_is_right(self):
        box = sandbox_toolbox(self.root / "ws", scopes=ALL, timeout=10)
        trace = Trace(clock=ManualClock())
        r = Gauntlet(ScriptedLLM(coder), stages=self.stages(), tools=box, trace=trace).run("buggy add")
        self.assertTrue(r.ok, r.reason)
        verdicts = [(a.stage, a.gate.verdict) for a in r.context.history]
        self.assertEqual(verdicts[1], ("test", Verdict.REVERT))  # real test caught the bug
        self.assertIn("add is wrong", r.context.history[1].gate.reason)
        self.assertIn("[TOOL LOG]", r.context.artifacts["execute"])
        self.assertTrue(any(e["kind"] == "tool" for e in trace.events))
        self.assertIn("a + b", (self.root / "ws" / "solution.py").read_text())

    def test_orchestrator_gives_each_thought_an_inherited_workspace(self):
        def per_branch(system, prompt):
            if "[TOOL RESULT" in prompt:
                return "done"
            m = re.search(r"(?:APPROACH DIRECTIVE|MUTATION) \((\w+)\)", prompt)
            return call("write_file", path=f"{m.group(1)}.txt", content="x")

        o = Orchestrator(
            ScriptedLLM(per_branch),
            OrchestratorConfig(
                population=2,
                branch_factor=2,
                immigrants=0,
                crossover_rate=0.0,
                synthesize=False,
                workers=2,
                inner_stages=(Stage("execute", "x", lambda *a: Gate(Verdict.PASS), tools=("write_file",)),),
                toolbox_factory=workspace_factory(self.root / "runs"),
                policy=ConvergencePolicy(max_generations=2, patience=5),
            ),
            scorer=FnScorer(lambda t, c: 0.5),
        )
        r = o.run("task")
        child = next(n for n in r.graph if n.op is Op.MUTATE)
        parent = r.graph[child.parents[0]]
        child_files = {p.name for p in Path(child.workspace).iterdir()}
        parent_files = {p.name for p in Path(parent.workspace).iterdir()}
        self.assertTrue(parent_files < child_files)  # inherited + its own
        self.assertEqual(Path(child.workspace).name, child.id)


if __name__ == "__main__":
    unittest.main()
