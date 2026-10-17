"""Help channels: how Lestinxor asks the operator when it is stuck."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from .contracts import Context


class HelpChannel(Protocol):
    def ask(self, question: str, ctx: Context) -> str | None: ...


class NoHelp:
    """Unattended mode: escalation means abort."""

    def ask(self, question: str, ctx: Context) -> str | None:
        return None


class ConsoleHelp:
    def ask(self, question: str, ctx: Context) -> str | None:
        print(f"\n[LESTINXOR needs help] {question}")
        answer = input("guidance (blank to abort)> ").strip()
        return answer or None


class ScriptedHelp:
    def __init__(self, answers: Iterable[str]) -> None:
        self._answers = list(answers)
        self.questions: list[str] = []

    def ask(self, question: str, ctx: Context) -> str | None:
        self.questions.append(question)
        return self._answers.pop(0) if self._answers else None
