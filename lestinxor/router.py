"""Role-based model routing: different models for different jobs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .llm import LLM

ROLES = ("inner", "gate", "score", "synth")


@dataclass(frozen=True)
class Router:
    """`inner` runs stages, `gate` judges stages, `score` ranks thoughts, `synth` fuses survivors."""

    default: LLM
    roles: Mapping[str, LLM] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = set(self.roles) - set(ROLES)
        if unknown:
            raise ValueError(f"unknown roles {sorted(unknown)}; valid: {ROLES}")

    def get(self, role: str) -> LLM:
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}; valid: {ROLES}")
        return self.roles.get(role, self.default)
