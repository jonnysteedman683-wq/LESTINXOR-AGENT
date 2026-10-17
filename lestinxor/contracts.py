"""Core data contracts. Pure data — no I/O, no clocks, no randomness."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    PASS = "pass"      # accept output, advance to next stage
    RETRY = "retry"    # redo this stage with feedback
    REVERT = "revert"  # fault originates upstream; roll back to an earlier stage
    HELP = "help"      # escalate to the operator
    ABORT = "abort"    # stop the run (Governor-only)


@dataclass(frozen=True)
class Gate:
    verdict: Verdict
    reason: str = ""
    revert_to: str | None = None


@dataclass(frozen=True)
class Attempt:
    stage: str
    iteration: int
    output: str
    gate: Gate


@dataclass
class Context:
    """Working memory for one run."""
    task: str
    artifacts: dict[str, str] = field(default_factory=dict)       # accepted output per stage
    feedback: dict[str, list[str]] = field(default_factory=dict)  # notes fed into the next attempt
    history: list[Attempt] = field(default_factory=list)          # every attempt, in order

    def add_feedback(self, stage: str, note: str) -> None:
        self.feedback.setdefault(stage, []).append(note)

    def attempts(self, stage: str) -> list[Attempt]:
        return [a for a in self.history if a.stage == stage]


@dataclass(frozen=True)
class Result:
    status: str  # "completed" | "aborted"
    context: Context
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    @property
    def deliverable(self) -> str:
        return self.context.artifacts.get("execute", "")
