"""Strategies (divergence), mutation operators (adaptation), and the adaptive sampler that learns which work."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Strategy:
    name: str
    directive: str


DEFAULT_STRATEGIES: tuple[Strategy, ...] = (
    Strategy(
        "first_principles", "Reason from first principles: ignore convention and derive the solution from fundamentals."
    ),
    Strategy("analogical", "Borrow a proven solution from an adjacent domain and adapt it."),
    Strategy("adversarial", "Assume everything that can fail will. Design primarily around failure modes."),
    Strategy("minimalist", "Find the simplest solution that fully meets the success criteria. Cut everything else."),
    Strategy("exhaustive", "Be comprehensive: cover edge cases, alternatives and long-tail requirements."),
    Strategy("contrarian", "Challenge the obvious approach and pursue the strongest non-obvious alternative."),
)

MUTATIONS: tuple[Strategy, ...] = (
    Strategy("refine", "Keep the approach. Fix every weakness named in the critique."),
    Strategy("simplify", "Keep what works, remove complexity, tighten the result."),
    Strategy("harden", "Make it robust: handle failure modes, edge cases and verification."),
    Strategy("rethink", "Keep the goal, discard the approach. Address the critique with a different mechanism."),
)


class AdaptiveSampler:
    """Multiplicative-weights bandit over named arms. Seeded and deterministic.

    Rewards in [-1, 1] scale an arm's weight by exp(learning_rate * reward); weights are renormalised
    to mean 1 and floored so no arm is ever fully extinguished (exploration never drops to zero).
    """

    def __init__(
        self, arms: Sequence[Strategy], rng: random.Random, learning_rate: float = 2.0, floor: float = 0.05
    ) -> None:
        names = [a.name for a in arms]
        if not arms or len(set(names)) != len(names):
            raise ValueError("arms must be non-empty with unique names")
        self.arms = {a.name: a for a in arms}
        self.weights = {a.name: 1.0 for a in arms}
        self.rng = rng
        self.learning_rate = learning_rate
        self.floor = floor

    def sample(self, k: int = 1, distinct: bool = True) -> list[Strategy]:
        out: list[Strategy] = []
        pool = dict(self.weights)
        for _ in range(k):
            if not pool:
                pool = dict(self.weights)
            names = sorted(pool)
            choice = self.rng.choices(names, weights=[pool[n] for n in names])[0]
            out.append(self.arms[choice])
            if distinct:
                del pool[choice]
        return out

    def reward(self, name: str, value: float) -> None:
        value = max(-1.0, min(1.0, value))
        self.weights[name] *= math.exp(self.learning_rate * value)
        mean = sum(self.weights.values()) / len(self.weights)
        self.weights = {n: max(self.floor, w / mean) for n, w in self.weights.items()}

    def snapshot(self) -> dict[str, float]:
        return {n: round(w, 6) for n, w in sorted(self.weights.items())}
