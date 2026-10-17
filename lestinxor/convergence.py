"""When the outer loop stops: target reached, plateau, diversity collapse, or budget."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations


@dataclass(frozen=True)
class ConvergencePolicy:
    target_score: float = 0.95  # stop when the best thought reaches this
    patience: int = 2  # generations without min_delta improvement before plateau
    min_delta: float = 0.01
    min_diversity: float = 0.15  # mean pairwise Jaccard distance below this = population has converged
    max_generations: int = 6  # including generation 0

    def __post_init__(self) -> None:
        if not 0.0 <= self.target_score <= 1.0:
            raise ValueError("target_score must be in [0, 1]")
        if self.patience < 1 or self.max_generations < 1:
            raise ValueError("patience and max_generations must be >= 1")


def jaccard_distance(a: str, b: str) -> float:
    ta, tb = set(a.lower().split()), set(b.lower().split())
    if not ta and not tb:
        return 0.0
    return 1.0 - len(ta & tb) / len(ta | tb)


def diversity(texts: Sequence[str]) -> float:
    """Mean pairwise Jaccard distance; 1.0 when fewer than two texts (nothing to have converged)."""
    pairs = list(combinations(texts, 2))
    if not pairs:
        return 1.0
    return sum(jaccard_distance(a, b) for a, b in pairs) / len(pairs)


class ConvergenceTracker:
    def __init__(self, policy: ConvergencePolicy) -> None:
        self.policy = policy
        self.best = float("-inf")
        self.stale = 0

    def observe(self, generation: int, best_score: float, texts: Sequence[str]) -> str | None:
        p = self.policy
        if best_score >= p.target_score:
            return f"converged: target reached ({best_score:.2f} >= {p.target_score:.2f})"
        if best_score - self.best >= p.min_delta:
            self.best, self.stale = best_score, 0
        else:
            self.stale += 1
        if self.stale >= p.patience:
            return f"converged: plateau ({self.stale} generations without +{p.min_delta})"
        div = diversity(texts)
        if div < p.min_diversity:
            return f"converged: diversity collapse ({div:.2f} < {p.min_diversity:.2f})"
        if generation + 1 >= p.max_generations:
            return f"budget: max generations ({p.max_generations})"
        return None
