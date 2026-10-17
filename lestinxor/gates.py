"""Composable gates: real checks first, judgement second."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from .contracts import Context, Gate, Verdict
from .llm import LLM
from .stages import GateFn, Stage


def command_gate(
    argv: Sequence[str],
    *,
    on_fail: Verdict = Verdict.RETRY,
    revert_to: str | None = None,
    tail: int = 1500,
) -> GateFn:
    """Pass iff `argv` exits 0 in the run's workspace (via the `run_command` tool and its allowlist)."""
    if on_fail not in (Verdict.RETRY, Verdict.REVERT, Verdict.HELP):
        raise ValueError("on_fail must be RETRY, REVERT or HELP")
    if on_fail is Verdict.REVERT and not revert_to:
        raise ValueError("on_fail=REVERT requires revert_to")
    cmd = " ".join(argv)

    def gate(ctx: Context, stage: Stage, output: str, iteration: int, llm: LLM) -> Gate:
        if ctx.tools is None:
            return Gate(Verdict.HELP, f"check `{cmd}` needs a toolbox")
        denied = ctx.tools.permitted("run_command")
        if denied:
            return Gate(Verdict.HELP, f"check `{cmd}` cannot run: {denied}")
        r = ctx.tools.call("run_command", {"argv": list(argv)})
        if r.ok:
            return Gate(Verdict.PASS, f"`{cmd}` passed")
        detail = r.output[-tail:] if r.output else ""
        return Gate(on_fail, f"`{cmd}` failed ({r.error}):\n{detail}".rstrip(), revert_to)

    return gate


def chain(*gates: GateFn) -> GateFn:
    """Run gates in order; the first non-PASS verdict wins. Put cheap deterministic checks first."""
    if not gates:
        raise ValueError("chain needs at least one gate")

    def gate(ctx: Context, stage: Stage, output: str, iteration: int, llm: LLM) -> Gate:
        result = Gate(Verdict.PASS)
        for g in gates:
            result = g(ctx, stage, output, iteration, llm)
            if result.verdict is not Verdict.PASS:
                return result
        return result

    return gate


def with_gate(stages: Sequence[Stage], name: str, gate: GateFn) -> tuple[Stage, ...]:
    """Copy of `stages` with stage `name` gated by `gate`."""
    if name not in {s.name for s in stages}:
        raise ValueError(f"no stage named {name!r}")
    return tuple(replace(s, gate=gate) if s.name == name else s for s in stages)
