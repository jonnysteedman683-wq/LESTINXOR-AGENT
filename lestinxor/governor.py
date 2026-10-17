"""The Governor: budget enforcement and verdict review. Sole authority to abort."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import Gate, Verdict


@dataclass(frozen=True)
class Budget:
    max_iterations: int = 3  # attempts per stage before escalating to help
    max_steps: int = 40  # total stage attempts per run
    max_reverts: int = 4  # rollbacks per run
    max_help: int = 2  # operator escalations per run


class Governor:
    def __init__(self, budget: Budget | None = None) -> None:
        self.budget = budget or Budget()
        self.steps = 0
        self.reverts = 0
        self.helps = 0

    def tick(self) -> str | None:
        """Count a step. Returns an abort reason once the step budget is spent."""
        self.steps += 1
        if self.steps > self.budget.max_steps:
            return f"governor: step budget exhausted ({self.budget.max_steps})"
        return None

    def review(self, gate: Gate, stage: str, iteration: int, earlier: list[str]) -> Gate:
        if gate.verdict is Verdict.REVERT:
            if gate.revert_to not in earlier:
                gate = Gate(Verdict.RETRY, f"governor: invalid revert target {gate.revert_to!r}; {gate.reason}")
            elif self.reverts >= self.budget.max_reverts:
                gate = Gate(Verdict.HELP, f"governor: revert budget exhausted; {gate.reason}")
            else:
                self.reverts += 1
                return gate

        if gate.verdict is Verdict.RETRY and iteration >= self.budget.max_iterations:
            gate = Gate(Verdict.HELP, f"governor: '{stage}' failed {iteration}x; last: {gate.reason}")

        if gate.verdict is Verdict.HELP:
            if self.helps >= self.budget.max_help:
                return Gate(Verdict.ABORT, f"governor: help budget exhausted; {gate.reason}")
            self.helps += 1

        return gate
