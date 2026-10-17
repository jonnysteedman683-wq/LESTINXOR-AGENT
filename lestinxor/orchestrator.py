"""The outer loop: evolutionary thought search over inner-loop Gauntlet runs.

diverge ─► inner loop (per thought) ─► score ─► adapt ─► select ─► converged? ─► synthesize
   ▲                                                                   │ no
   └──────────── mutate · crossover · immigrants ◄─────────────────────┘
"""

from __future__ import annotations

import logging
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

from .clock import ManualClock
from .contracts import Result
from .convergence import ConvergencePolicy, ConvergenceTracker
from .gauntlet import Gauntlet
from .governor import Budget, Governor
from .help import NoHelp
from .llm import LLM
from .operators import SYNTH_SYSTEM, crossover_brief, mutate_brief, seed_brief, synthesis_prompt
from .router import Router
from .sandbox import ToolboxFactory
from .scoring import LLMScorer, Score, Scorer
from .stages import COMPACT_STAGES, Stage
from .strategies import DEFAULT_STRATEGIES, MUTATIONS, AdaptiveSampler, Strategy
from .thought import Op, Thought, ThoughtGraph
from .trace import Trace

log = logging.getLogger("lestinxor.orchestrator")

INNER_BUDGET = Budget(max_iterations=2, max_steps=12, max_reverts=2, max_help=0)


@dataclass(frozen=True)
class OrchestratorConfig:
    population: int = 4  # seeds in generation 0 (divergence width)
    branch_factor: int = 4  # new thoughts per later generation
    elite: int = 2  # survivors carried forward
    immigrants: int = 1  # fresh seeds per generation (keeps exploring)
    crossover_rate: float = 0.25
    synthesize: bool = True  # fuse final survivors (text runs only); kept only if it scores at least as well
    workers: int = 1  # parallel branches; results are order-independent
    seed: int = 0
    strategies: tuple[Strategy, ...] = DEFAULT_STRATEGIES
    mutations: tuple[Strategy, ...] = MUTATIONS
    inner_stages: tuple[Stage, ...] = COMPACT_STAGES
    inner_budget: Budget = INNER_BUDGET
    policy: ConvergencePolicy = field(default_factory=ConvergencePolicy)
    toolbox_factory: ToolboxFactory | None = None  # per-thought tools, e.g. sandbox.workspace_factory(root)

    def __post_init__(self) -> None:
        for name in ("population", "branch_factor", "elite", "workers"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        if not 0 <= self.immigrants <= self.branch_factor:
            raise ValueError("immigrants must be in [0, branch_factor]")
        if not 0.0 <= self.crossover_rate <= 1.0:
            raise ValueError("crossover_rate must be in [0, 1]")
        if not (self.strategies and self.mutations and self.inner_stages):
            raise ValueError("strategies, mutations and inner_stages must be non-empty")

    def summary(self) -> dict[str, Any]:
        """Run-defining settings (excludes `workers`, which never changes results)."""
        return {
            "population": self.population,
            "branch_factor": self.branch_factor,
            "elite": self.elite,
            "immigrants": self.immigrants,
            "crossover_rate": self.crossover_rate,
            "synthesize": self.synthesize,
            "seed": self.seed,
            "strategies": [s.name for s in self.strategies],
            "mutations": [m.name for m in self.mutations],
            "inner_stages": [s.name for s in self.inner_stages],
            "inner_budget": asdict(self.inner_budget),
            "tools": self.toolbox_factory is not None,
            "policy": asdict(self.policy),
        }


@dataclass(frozen=True)
class _Outcome:
    result: Result | None
    score: Score | None
    events: list[dict[str, Any]]
    error: str | None = None
    workspace: str = ""


@dataclass
class OrchestrationResult:
    best: Thought | None
    graph: ThoughtGraph
    stop_reason: str
    generations: int
    weights: dict[str, dict[str, float]]

    @property
    def ok(self) -> bool:
        return self.best is not None and self.best.ok

    @property
    def answer(self) -> str:
        return self.best.candidate if self.best else ""


class Orchestrator:
    def __init__(
        self,
        router: Router | LLM,
        config: OrchestratorConfig | None = None,
        scorer: Scorer | None = None,
        trace: Trace | None = None,
    ) -> None:
        self.router = router if isinstance(router, Router) else Router(router)
        self.config = config or OrchestratorConfig()
        self.scorer = scorer or LLMScorer(self.router.get("score"))
        self.trace = trace or Trace()
        self.rng = random.Random(self.config.seed)
        self.strategies = AdaptiveSampler(self.config.strategies, self.rng)
        self.mutations = AdaptiveSampler(self.config.mutations, self.rng)

    # ── public ────────────────────────────────────────────────────────────
    def run(self, task: str) -> OrchestrationResult:
        cfg = self.config
        graph = ThoughtGraph()
        tracker = ConvergenceTracker(cfg.policy)
        self.trace.emit("orchestrate.start", task=task, config=cfg.summary())

        gen = 0
        batch = [graph.new(0, Op.SEED, (), s.name, seed_brief(task, s)) for s in self.strategies.sample(cfg.population)]
        survivors = self._step(task, graph, gen, batch, [])

        while True:
            reason = self._check(gen, survivors, batch, tracker)
            if reason:
                break
            gen += 1
            batch = self._spawn(task, graph, gen, survivors)
            survivors = self._step(task, graph, gen, batch, survivors)

        best = survivors[0] if survivors else None
        # With tools the deliverable is the workspace; a text-only fusion would detach the answer from its artifacts.
        if best is not None and cfg.synthesize and cfg.toolbox_factory is None and len(survivors) >= 2:
            best = self._synthesize(task, graph, gen + 1, survivors, best)

        weights = {"strategies": self.strategies.snapshot(), "mutations": self.mutations.snapshot()}
        self.trace.emit(
            "orchestrate.finish",
            reason=reason,
            generations=gen,
            thoughts=len(graph),
            best=best.id if best else None,
            score=best.score if best else None,
        )
        log.info("finished: %s · best=%s", reason, f"{best.id} {best.score:.2f}" if best else "none")
        return OrchestrationResult(best, graph, reason, gen, weights)

    # ── outer loop steps ──────────────────────────────────────────────────
    def _step(
        self, task: str, graph: ThoughtGraph, gen: int, batch: list[Thought], survivors: list[Thought]
    ) -> list[Thought]:
        self._evaluate(task, batch)
        self._adapt(graph, batch)
        nxt = self._select(survivors + batch)
        log.info("gen %d: %d thoughts, best %s", gen, len(batch), f"{nxt[0].id} {nxt[0].score:.2f}" if nxt else "none")
        self.trace.emit("generation", generation=gen, survivors=[n.id for n in nxt], scores=[n.score for n in nxt])
        return nxt

    def _check(
        self, gen: int, survivors: list[Thought], batch: list[Thought], tracker: ConvergenceTracker
    ) -> str | None:
        if not survivors:
            return "aborted: no viable thoughts"
        texts = [n.candidate for n in batch if n.ok]
        return tracker.observe(gen, survivors[0].score, texts)

    def _spawn(self, task: str, graph: ThoughtGraph, gen: int, survivors: list[Thought]) -> list[Thought]:
        cfg = self.config
        out: list[Thought] = []
        for k in range(cfg.branch_factor - cfg.immigrants):
            if len(survivors) >= 2 and self.rng.random() < cfg.crossover_rate:
                a = survivors[0]
                b = survivors[1 + self.rng.randrange(len(survivors) - 1)]
                out.append(graph.new(gen, Op.CROSSOVER, (a.id, b.id), "crossover", crossover_brief(task, a, b)))
            else:
                parent = survivors[k % len(survivors)]
                m = self.mutations.sample(1)[0]
                out.append(graph.new(gen, Op.MUTATE, (parent.id,), m.name, mutate_brief(task, parent, m)))
        for s in self.strategies.sample(cfg.immigrants):
            out.append(graph.new(gen, Op.SEED, (), s.name, seed_brief(task, s)))
        return out

    def _evaluate(self, task: str, batch: list[Thought]) -> None:
        if self.config.workers > 1 and len(batch) > 1:
            with ThreadPoolExecutor(max_workers=self.config.workers) as pool:
                outcomes = list(pool.map(lambda n: self._work(task, n), batch))
        else:
            outcomes = [self._work(task, n) for n in batch]

        for node, out in zip(batch, outcomes, strict=True):  # apply on the main thread, in id order
            for ev in out.events:
                rest = {k: v for k, v in ev.items() if k not in ("seq", "ts", "kind")}
                self.trace.emit("inner", thought=node.id, inner_kind=ev["kind"], inner_seq=ev["seq"], **rest)
            node.workspace = out.workspace
            if out.result is not None:
                node.inner_steps = len(out.result.context.history)
            if out.error:
                node.status, node.reason = "failed", out.error
            elif out.result is None or not out.result.ok or out.score is None:
                node.status, node.reason = "failed", out.result.reason if out.result else "no result"
            else:
                node.candidate = out.result.deliverable
                node.score, node.critique = out.score.value, out.score.critique
                node.status = "ok"
            self.trace.emit("thought", **{k: v for k, v in node.to_dict().items() if k != "brief"})

    def _work(self, task: str, node: Thought) -> _Outcome:
        """Inner loop + scoring for one thought. Runs on worker threads; touches no shared state."""
        trace = Trace(clock=ManualClock())
        workspace = ""
        try:
            factory = self.config.toolbox_factory
            tools = factory(node.id, node.parents) if factory else None
            if tools is not None and tools.workspace is not None:
                workspace = str(tools.workspace)
            result = Gauntlet(
                self.router.get("inner"),
                stages=self.config.inner_stages,
                governor=Governor(self.config.inner_budget),
                help=NoHelp(),
                trace=trace,
                judge=self.router.get("gate"),
                tools=tools,
            ).run(node.brief)
            score = self.scorer.score(task, result.deliverable) if result.ok else None
            return _Outcome(result, score, trace.events, workspace=workspace)
        except Exception as exc:  # a failed branch must never take down the run
            return _Outcome(None, None, trace.events, f"{type(exc).__name__}: {exc}", workspace)

    def _adapt(self, graph: ThoughtGraph, batch: list[Thought]) -> None:
        for node in batch:
            if node.op is Op.SEED:
                self.strategies.reward(node.strategy, node.score - 0.5)
            elif node.op is Op.MUTATE:
                self.mutations.reward(node.strategy, node.score - graph[node.parents[0]].score)
        self.trace.emit("adapt", strategies=self.strategies.snapshot(), mutations=self.mutations.snapshot())

    def _select(self, nodes: list[Thought]) -> list[Thought]:
        out: list[Thought] = []
        seen: set[str] = set()
        for n in ThoughtGraph.ranked(nodes):
            if n.candidate not in seen:  # exact clones never take two elite slots
                seen.add(n.candidate)
                out.append(n)
            if len(out) == self.config.elite:
                break
        return out

    def _synthesize(self, task: str, graph: ThoughtGraph, gen: int, survivors: list[Thought], best: Thought) -> Thought:
        node = graph.new(
            gen,
            Op.SYNTHESIZE,
            tuple(s.id for s in survivors),
            "synthesize",
            f"synthesis of {', '.join(s.id for s in survivors)}",
        )
        try:
            node.candidate = self.router.get("synth").complete(SYNTH_SYSTEM, synthesis_prompt(task, survivors))
            score = self.scorer.score(task, node.candidate)
            node.score, node.critique, node.status = score.value, score.critique, "ok"
        except Exception as exc:
            node.status, node.reason = "failed", f"{type(exc).__name__}: {exc}"
        self.trace.emit("thought", **{k: v for k, v in node.to_dict().items() if k != "brief"})
        return node if node.ok and node.score >= best.score else best
