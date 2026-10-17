import random
import re
import unittest
import urllib.error

from lestinxor import (
    SYNTH_SYSTEM,
    AdaptiveSampler,
    ConvergencePolicy,
    FnScorer,
    Gate,
    Op,
    Orchestrator,
    OrchestratorConfig,
    RetryingLLM,
    Router,
    Score,
    ScriptedLLM,
    Stage,
    Strategy,
    Trace,
    Verdict,
    diversity,
    parse_score,
)
from lestinxor.clock import ManualClock

PASS = Stage("execute", "Produce it.", lambda ctx, s, o, it, llm: Gate(Verdict.PASS))


def inner(system: str, prompt: str) -> str:
    if system == SYNTH_SYSTEM:
        return "synthesized fused answer"
    if m := re.search(r"MUTATION \((\w+)\)", prompt):
        return f"polished {m.group(1)} solution"
    if "CROSSOVER" in prompt:
        return "polished crossover hybrid"
    m = re.search(r"APPROACH DIRECTIVE \((\w+)\)", prompt)
    return f"draft {m.group(1) if m else 'x'} idea"


def polished_wins(task: str, cand: str) -> float:
    return 1.0 if "polished" in cand else 0.5


def make(scorer, llm=None, stages=(PASS,), **cfg):
    policy = cfg.pop("policy", ConvergencePolicy(max_generations=10))
    return Orchestrator(
        llm or ScriptedLLM(inner),
        OrchestratorConfig(inner_stages=stages, policy=policy, **cfg),
        scorer=FnScorer(scorer),
        trace=Trace(clock=ManualClock()),
    )


class OuterLoopTests(unittest.TestCase):
    def test_diverge_mutate_converge_on_target(self):
        r = make(polished_wins).run("task")
        self.assertTrue(r.ok)
        self.assertTrue(r.stop_reason.startswith("converged: target"))
        self.assertEqual(r.generations, 1)
        self.assertIn("polished", r.answer)
        self.assertEqual(len({n.strategy for n in r.graph.generation(0)}), 4)  # diverged

    def test_synthesis_kept_only_if_not_worse(self):
        r = make(polished_wins).run("task")
        synth = [n for n in r.graph if n.op is Op.SYNTHESIZE]
        self.assertEqual(len(synth), 1)
        self.assertNotEqual(r.best.id, synth[0].id)  # 0.5 < 1.0: rejected
        r2 = make(lambda t, c: 1.0 if "synthesized" in c else 0.5, policy=ConvergencePolicy(max_generations=1)).run(
            "task"
        )
        self.assertIs(r2.best.op, Op.SYNTHESIZE)

    def test_plateau(self):
        r = make(lambda t, c: 0.5, synthesize=False, policy=ConvergencePolicy(patience=2, max_generations=10)).run(
            "task"
        )
        self.assertTrue(r.stop_reason.startswith("converged: plateau"), r.stop_reason)
        self.assertEqual(r.generations, 2)

    def test_diversity_collapse(self):
        r = make(lambda t, c: 0.5, llm=ScriptedLLM(lambda s, p: "same answer")).run("task")
        self.assertTrue(r.stop_reason.startswith("converged: diversity"), r.stop_reason)
        self.assertEqual(r.generations, 0)

    def test_max_generations(self):
        r = make(lambda t, c: 0.5, synthesize=False, policy=ConvergencePolicy(max_generations=1, patience=5)).run(
            "task"
        )
        self.assertTrue(r.stop_reason.startswith("budget"), r.stop_reason)

    def test_lineage_and_mermaid(self):
        r = make(polished_wins).run("task")
        child = next(n for n in r.graph if n.op is Op.MUTATE)
        self.assertEqual(r.graph.lineage(child.id), list(child.parents))
        self.assertIn(f"{child.parents[0]} --> {child.id}", r.graph.to_mermaid())

    def test_failed_branches_are_contained(self):
        stuck = Stage(
            "execute",
            "x",
            lambda ctx, s, o, it, llm: Gate(Verdict.RETRY, "no") if "contrarian" in ctx.task else Gate(Verdict.PASS),
        )

        def flaky(system, prompt):
            if "adversarial" in prompt:
                raise ConnectionError("down")
            return inner(system, prompt)

        o = make(polished_wins, llm=ScriptedLLM(flaky), stages=(stuck,), population=6)
        r = o.run("task")
        self.assertTrue(r.ok)
        failed = {n.strategy: n for n in r.graph.generation(0) if not n.ok}
        self.assertEqual(set(failed), {"contrarian", "adversarial"})
        self.assertIn("ConnectionError", failed["adversarial"].reason)
        self.assertLess(r.weights["strategies"]["contrarian"], r.weights["strategies"]["minimalist"])

    def test_deterministic_and_parallel_equivalent(self):
        def run(workers):
            o = make(
                lambda t, c: 0.3 + (len(c) % 7) / 10,
                workers=workers,
                crossover_rate=0.5,
                policy=ConvergencePolicy(max_generations=4, patience=4),
            )
            r = o.run("task")
            return r.graph.digest(), o.trace.digest()

        self.assertEqual(run(1), run(1))
        self.assertEqual(run(1), run(4))

    def test_router_roles(self):
        work = ScriptedLLM(inner)
        synth = ScriptedLLM(lambda s, p: "synth model answer")
        o = Orchestrator(
            Router(work, roles={"synth": synth}),
            OrchestratorConfig(inner_stages=(PASS,), policy=ConvergencePolicy(max_generations=1)),
            scorer=FnScorer(lambda t, c: 0.5),
        )
        o.run("task")
        self.assertEqual(len(synth.calls), 1)
        with self.assertRaises(ValueError):
            Router(work, roles={"bogus": work})

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            OrchestratorConfig(elite=0)
        with self.assertRaises(ValueError):
            OrchestratorConfig(immigrants=9, branch_factor=2)


class ComponentTests(unittest.TestCase):
    def test_sampler_adapts(self):
        s = AdaptiveSampler([Strategy("a", ""), Strategy("b", "")], random.Random(0))
        for _ in range(5):
            s.reward("a", 1.0)
            s.reward("b", -1.0)
        w = s.snapshot()
        self.assertGreater(w["a"], w["b"])
        self.assertGreaterEqual(w["b"], s.floor)
        picks = [x.name for x in s.sample(50, distinct=False)]
        self.assertGreater(picks.count("a"), picks.count("b"))

    def test_parse_score(self):
        self.assertEqual(parse_score('{"score": 1.7}').value, 1.0)
        self.assertEqual(parse_score('x {"score":"0.4","critique":"c"} y'), Score(0.4, "c"))
        self.assertEqual(parse_score("nope").value, 0.0)
        self.assertEqual(parse_score('{"score": NaN}').value, 0.0)
        self.assertEqual(parse_score('{"critique": "no score"}').value, 0.0)

    def test_diversity(self):
        self.assertEqual(diversity(["a b", "a b"]), 0.0)
        self.assertEqual(diversity(["only one"]), 1.0)
        self.assertGreater(diversity(["a b", "c d"]), 0.9)

    def test_retrying_llm(self):
        sleeps = []

        class Flaky:
            calls = 0

            def complete(self, system, prompt):
                Flaky.calls += 1
                if Flaky.calls < 3:
                    raise urllib.error.URLError("blip")
                return "ok"

        self.assertEqual(RetryingLLM(Flaky(), sleep=sleeps.append).complete("s", "p"), "ok")
        self.assertEqual(sleeps, [1.0, 2.0])

        class BadRequest:
            calls = 0

            def complete(self, system, prompt):
                BadRequest.calls += 1
                raise urllib.error.HTTPError("u", 400, "bad", None, None)

        with self.assertRaises(urllib.error.HTTPError):
            RetryingLLM(BadRequest(), sleep=sleeps.append).complete("s", "p")
        self.assertEqual(BadRequest.calls, 1)


if __name__ == "__main__":
    unittest.main()
