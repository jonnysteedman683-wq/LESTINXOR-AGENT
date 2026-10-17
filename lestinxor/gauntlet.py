"""The Gauntlet runner: a state machine over gated stages."""
from __future__ import annotations

from typing import Sequence

from .contracts import Attempt, Context, Result, Verdict
from .governor import Governor
from .help import HelpChannel, NoHelp
from .llm import LLM
from .stages import DEFAULT_STAGES, Stage
from .trace import Trace


class Gauntlet:
    def __init__(self, llm: LLM, stages: Sequence[Stage] = DEFAULT_STAGES,
                 governor: Governor | None = None, help: HelpChannel | None = None,
                 trace: Trace | None = None) -> None:
        names = [s.name for s in stages]
        if len(set(names)) != len(names):
            raise ValueError(f"stage names must be unique: {names}")
        if not stages:
            raise ValueError("at least one stage is required")
        self.llm = llm
        self.stages = tuple(stages)
        self.names = names
        self.governor = governor or Governor()
        self.help = help or NoHelp()
        self.trace = trace or Trace()

    def run(self, task: str) -> Result:
        ctx = Context(task)
        iters = {n: 0 for n in self.names}
        i = 0
        self.trace.emit("start", task=task, stages=self.names)

        while i < len(self.stages):
            stage = self.stages[i]
            exhausted = self.governor.tick()
            if exhausted:
                return self._finish(ctx, "aborted", exhausted)

            iters[stage.name] += 1
            it = iters[stage.name]
            output = stage.run(ctx, self.llm)
            gate = stage.evaluate(ctx, output, it, self.llm)
            gate = self.governor.review(gate, stage.name, it, self.names[:i])

            ctx.history.append(Attempt(stage.name, it, output, gate))
            self.trace.emit("attempt", stage=stage.name, iteration=it, verdict=gate.verdict.value,
                            reason=gate.reason, revert_to=gate.revert_to, output=output)

            if gate.verdict is Verdict.PASS:
                ctx.artifacts[stage.name] = output
                i += 1
            elif gate.verdict is Verdict.RETRY:
                ctx.add_feedback(stage.name, gate.reason)
            elif gate.verdict is Verdict.REVERT:
                j = self.names.index(gate.revert_to)
                for name in self.names[j:i + 1]:
                    ctx.artifacts.pop(name, None)
                    iters[name] = 0
                ctx.add_feedback(gate.revert_to, f"Reverted from '{stage.name}': {gate.reason}")
                i = j
            elif gate.verdict is Verdict.HELP:
                question = f"[{stage.name}] {gate.reason}"
                answer = self.help.ask(question, ctx)
                self.trace.emit("help", stage=stage.name, question=question, answer=answer)
                if not answer:
                    return self._finish(ctx, "aborted", f"no help available: {gate.reason}")
                ctx.add_feedback(stage.name, f"Operator guidance: {answer}")
                iters[stage.name] = 0
            else:
                return self._finish(ctx, "aborted", gate.reason)

        return self._finish(ctx, "completed")

    def _finish(self, ctx: Context, status: str, reason: str = "") -> Result:
        self.trace.emit("finish", status=status, reason=reason, steps=self.governor.steps)
        return Result(status, ctx, reason)
