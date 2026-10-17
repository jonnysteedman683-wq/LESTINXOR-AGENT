"""Command line.

lestinxor orchestrate "task" --model <exact-model-string> [--judge-model ...] [--workers 4]
lestinxor gauntlet    "task" --model <exact-model-string>
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .convergence import ConvergencePolicy
from .gauntlet import Gauntlet
from .governor import Budget, Governor
from .help import ConsoleHelp, NoHelp
from .llm import LLM, AnthropicLLM, OpenAICompatLLM, RetryingLLM
from .orchestrator import Orchestrator, OrchestratorConfig
from .router import Router
from .trace import Trace


def _build_llm(model: str, a: argparse.Namespace) -> LLM:
    key = os.environ.get(a.api_key_env) if a.api_key_env else None
    base: LLM
    if a.provider == "anthropic":
        base = AnthropicLLM(model, api_key=key, max_tokens=a.max_tokens)
    else:
        if not a.base_url:
            raise SystemExit("--base-url is required for --provider openai")
        base = OpenAICompatLLM(model, a.base_url, api_key=key, max_tokens=a.max_tokens)
    return RetryingLLM(base)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lestinxor", description="Lestinxor — grand orchestrator for AI work.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("task")
    common.add_argument("--model", required=True, help="exact model string for the work")
    common.add_argument("--judge-model", help="exact model string for gates and scoring (default: --model)")
    common.add_argument("--provider", choices=("anthropic", "openai"), default="anthropic")
    common.add_argument("--base-url", help="endpoint for --provider openai")
    common.add_argument("--api-key-env", help="env var holding the API key")
    common.add_argument("--max-tokens", type=int, default=4096)
    common.add_argument("--trace", default="lestinxor-trace.jsonl", help="JSONL trace path")
    common.add_argument("-v", "--verbose", action="store_true")

    g = sub.add_parser("gauntlet", parents=[common], help="single inner-loop run")
    g.add_argument("--max-iterations", type=int, default=3)
    g.add_argument("--max-steps", type=int, default=40)
    g.add_argument("--unattended", action="store_true", help="never prompt; escalation aborts")

    o = sub.add_parser("orchestrate", parents=[common], help="outer-loop thought search over inner loops")
    o.add_argument("--population", type=int, default=4)
    o.add_argument("--branch-factor", type=int, default=4)
    o.add_argument("--elite", type=int, default=2)
    o.add_argument("--generations", type=int, default=6)
    o.add_argument("--target", type=float, default=0.95)
    o.add_argument("--workers", type=int, default=4)
    o.add_argument("--seed", type=int, default=0)
    o.add_argument("--no-synthesize", action="store_true")
    o.add_argument("--graph", type=Path, help="write the thought graph as JSON")
    o.add_argument("--mermaid", type=Path, help="write the thought graph as a Mermaid diagram")
    return p


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    if a.verbose:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(name)s · %(message)s")

    llm = _build_llm(a.model, a)
    judge = _build_llm(a.judge_model, a) if a.judge_model else llm
    trace = Trace(path=a.trace)

    if a.command == "gauntlet":
        result = Gauntlet(
            llm,
            governor=Governor(Budget(max_iterations=a.max_iterations, max_steps=a.max_steps)),
            help=NoHelp() if a.unattended else ConsoleHelp(),
            trace=trace,
            judge=judge,
        ).run(a.task)
        print(result.deliverable)
        print(f"\n[{result.status}] {result.reason}".rstrip(), file=sys.stderr)
        return 0 if result.ok else 1

    config = OrchestratorConfig(
        population=a.population,
        branch_factor=a.branch_factor,
        elite=a.elite,
        workers=a.workers,
        seed=a.seed,
        synthesize=not a.no_synthesize,
        policy=ConvergencePolicy(target_score=a.target, max_generations=a.generations),
    )
    router = Router(llm, roles={"gate": judge, "score": judge})
    out = Orchestrator(router, config, trace=trace).run(a.task)
    if a.graph:
        a.graph.write_text(out.graph.to_json(), encoding="utf-8")
    if a.mermaid:
        a.mermaid.write_text(out.graph.to_mermaid(), encoding="utf-8")
    print(out.answer)
    best = f"{out.best.id} score {out.best.score:.2f}" if out.best else "none"
    print(f"\n[{out.stop_reason}] gens={out.generations} thoughts={len(out.graph)} best={best}", file=sys.stderr)
    return 0 if out.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
