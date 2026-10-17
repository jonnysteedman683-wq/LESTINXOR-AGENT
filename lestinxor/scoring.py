"""Fitness: how the outer loop ranks candidates."""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, Union

from .llm import LLM


@dataclass(frozen=True)
class Score:
    value: float
    critique: str = ""


class Scorer(Protocol):
    def score(self, task: str, candidate: str) -> Score: ...


SCORE_SYSTEM = (
    "You are the Lestinxor evaluator. Score strictly and consistently against the task. "
    "Reply with a single JSON object and nothing else."
)

DEFAULT_RUBRIC = "Correctness, completeness against the task's success criteria, robustness, and clarity."


class LLMScorer:
    def __init__(self, llm: LLM, rubric: str = DEFAULT_RUBRIC) -> None:
        self.llm = llm
        self.rubric = rubric

    def score(self, task: str, candidate: str) -> Score:
        prompt = (
            f"TASK:\n{task}\n\nRUBRIC: {self.rubric}\n\nCANDIDATE:\n{candidate}\n\n"
            'Return {"score": <0.0-1.0>, "critique": "<specific weaknesses and how to fix each>"}. '
            "1.0 means nothing left to improve."
        )
        return parse_score(self.llm.complete(SCORE_SYSTEM, prompt))


class FnScorer:
    """Deterministic scorer from a function returning a float or a Score (tests, rule-based fitness)."""

    def __init__(self, fn: Callable[[str, str], Union[float, Score]]) -> None:
        self.fn = fn

    def score(self, task: str, candidate: str) -> Score:
        out = self.fn(task, candidate)
        return out if isinstance(out, Score) else Score(_clamp(float(out)))


def _clamp(v: float) -> float:
    return 0.0 if math.isnan(v) else max(0.0, min(1.0, v))


def parse_score(raw: str) -> Score:
    start, end = raw.find("{"), raw.rfind("}")
    try:
        data = json.loads(raw[start : end + 1]) if start != -1 and end > start else None
        value = float(data["score"]) if isinstance(data, dict) else None
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        value = None
    if value is None or not isinstance(data, dict):
        return Score(0.0, "scorer: unparseable reply")
    return Score(_clamp(value), str(data.get("critique", "")))
