import unittest

from lestinxor import (
    DEFAULT_STAGES, JUDGE_SYSTEM, Budget, Gate, Gauntlet, Governor, ManualClock, NoHelp,
    ScriptedHelp, ScriptedLLM, Stage, Trace, Verdict, parse_gate,
)


def gate_always(verdict=Verdict.PASS, reason="", revert_to=None):
    return lambda ctx, stage, out, it, llm: Gate(verdict, reason, revert_to)


def make(stages, help=None, budget=None, llm=None):
    return Gauntlet(
        llm or ScriptedLLM(lambda s, p: "ok"),
        stages=stages,
        governor=Governor(budget or Budget()),
        help=help or NoHelp(),
        trace=Trace(clock=ManualClock()),
    )


class GauntletTests(unittest.TestCase):
    def test_linear_pass(self):
        r = make([Stage(n, "do", gate_always()) for n in "abc"]).run("t")
        self.assertTrue(r.ok)
        self.assertEqual(list(r.context.artifacts), ["a", "b", "c"])

    def test_retry_then_pass(self):
        gate = lambda ctx, s, o, it, llm: Gate(Verdict.PASS) if it >= 2 else Gate(Verdict.RETRY, "fix x")
        r = make([Stage("a", "do", gate)]).run("t")
        self.assertTrue(r.ok)
        self.assertEqual(len(r.context.history), 2)
        self.assertEqual(r.context.feedback["a"], ["fix x"])

    def test_feedback_reaches_next_prompt(self):
        llm = ScriptedLLM(lambda s, p: "ok")
        gate = lambda ctx, s, o, it, l: Gate(Verdict.PASS) if it >= 2 else Gate(Verdict.RETRY, "add tests")
        make([Stage("a", "do", gate)], llm=llm).run("t")
        self.assertIn("add tests", llm.calls[1][1])

    def test_retry_exhaustion_escalates_to_help(self):
        def gate(ctx, s, o, it, llm):
            if any("Operator guidance" in n for n in ctx.feedback.get(s.name, [])):
                return Gate(Verdict.PASS)
            return Gate(Verdict.RETRY, "stuck")
        help = ScriptedHelp(["use y"])
        r = make([Stage("a", "do", gate)], help=help, budget=Budget(max_iterations=2)).run("t")
        self.assertTrue(r.ok)
        self.assertEqual(len(r.context.history), 3)
        self.assertEqual(len(help.questions), 1)

    def test_no_help_aborts(self):
        r = make([Stage("a", "do", gate_always(Verdict.RETRY, "bad"))], budget=Budget(max_iterations=1)).run("t")
        self.assertFalse(r.ok)
        self.assertIn("no help available", r.reason)

    def test_revert_rolls_back_and_reruns(self):
        def test_gate(ctx, s, o, it, llm):
            if not ctx.attempts("test"):
                return Gate(Verdict.REVERT, "bad plan", "plan")
            return Gate(Verdict.PASS)
        stages = [Stage("plan", "p", gate_always()), Stage("execute", "e", gate_always()),
                  Stage("test", "t", test_gate)]
        r = make(stages).run("t")
        self.assertTrue(r.ok)
        self.assertEqual(len(r.context.attempts("plan")), 2)
        self.assertEqual(len(r.context.attempts("execute")), 2)
        self.assertIn("Reverted from 'test'", r.context.feedback["plan"][0])

    def test_invalid_revert_becomes_retry(self):
        def gate(ctx, s, o, it, llm):
            return Gate(Verdict.REVERT, "x", "nope") if it == 1 else Gate(Verdict.PASS)
        r = make([Stage("a", "do", gate)]).run("t")
        self.assertTrue(r.ok)
        self.assertIs(r.context.history[0].gate.verdict, Verdict.RETRY)

    def test_revert_budget_then_abort(self):
        stages = [Stage("a", "do", gate_always()), Stage("b", "do", gate_always(Verdict.REVERT, "x", "a"))]
        r = make(stages, budget=Budget(max_reverts=1, max_help=0)).run("t")
        self.assertFalse(r.ok)
        self.assertIn("help budget exhausted", r.reason)

    def test_step_budget(self):
        r = make([Stage("a", "do", gate_always(Verdict.RETRY, "x"))],
                 budget=Budget(max_iterations=100, max_steps=5)).run("t")
        self.assertFalse(r.ok)
        self.assertIn("step budget", r.reason)
        self.assertEqual(len(r.context.history), 5)

    def test_determinism(self):
        def run():
            g = make([Stage(n, "do", gate_always()) for n in "abc"])
            g.run("t")
            return g.trace.digest()
        self.assertEqual(run(), run())

    def test_duplicate_stage_names_rejected(self):
        with self.assertRaises(ValueError):
            make([Stage("a", "x"), Stage("a", "y")])


class JudgeTests(unittest.TestCase):
    def test_parse_gate(self):
        self.assertIs(parse_gate('noise {"verdict":"PASS","reason":"fine"} tail').verdict, Verdict.PASS)
        self.assertIs(parse_gate("garbage").verdict, Verdict.RETRY)
        self.assertIs(parse_gate('{"verdict":"maybe"}').verdict, Verdict.RETRY)
        self.assertIs(parse_gate('{"verdict":"abort"}').verdict, Verdict.HELP)
        g = parse_gate('{"verdict":"revert","reason":"r","revert_to":"plan"}')
        self.assertEqual((g.verdict, g.revert_to), (Verdict.REVERT, "plan"))

    def test_default_stages_with_llm_judge(self):
        llm = ScriptedLLM(lambda s, p: '{"verdict":"pass","reason":"ok"}' if s == JUDGE_SYSTEM else "work")
        r = make(DEFAULT_STAGES, llm=llm).run("build a thing")
        self.assertTrue(r.ok)
        self.assertEqual(list(r.context.artifacts), [s.name for s in DEFAULT_STAGES])
        self.assertEqual(r.deliverable, "work")


if __name__ == "__main__":
    unittest.main()
