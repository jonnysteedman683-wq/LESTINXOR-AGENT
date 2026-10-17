"""Command line: `lestinxor "task" --model <exact-model-string>`."""
from __future__ import annotations

import argparse
import sys

from .gauntlet import Gauntlet
from .governor import Budget, Governor
from .help import ConsoleHelp, NoHelp
from .llm import AnthropicLLM
from .trace import Trace


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lestinxor", description="Run a task through the Lestinxor Gauntlet.")
    p.add_argument("task")
    p.add_argument("--model", required=True, help="exact model string, e.g. claude-opus-5-5")
    p.add_argument("--trace", default="lestinxor-trace.jsonl", help="JSONL trace path")
    p.add_argument("--max-iterations", type=int, default=3)
    p.add_argument("--max-steps", type=int, default=40)
    p.add_argument("--unattended", action="store_true", help="never prompt; escalation aborts")
    a = p.parse_args(argv)

    gauntlet = Gauntlet(
        AnthropicLLM(a.model),
        governor=Governor(Budget(max_iterations=a.max_iterations, max_steps=a.max_steps)),
        help=NoHelp() if a.unattended else ConsoleHelp(),
        trace=Trace(path=a.trace),
    )
    result = gauntlet.run(a.task)
    print(result.deliverable)
    print(f"\n[{result.status}] {result.reason}".rstrip(), file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
