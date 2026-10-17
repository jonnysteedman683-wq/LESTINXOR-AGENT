"""The thought graph: every branch, mutation, crossover and synthesis, with lineage."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class Op(str, Enum):
    SEED = "seed"  # diverge: fresh branch from a strategy
    MUTATE = "mutate"  # adapt: rewrite a survivor under a mutation operator
    CROSSOVER = "crossover"  # recombine: merge two survivors
    SYNTHESIZE = "synthesize"  # converge: fuse the final survivors


@dataclass
class Thought:
    id: str
    generation: int
    op: Op
    parents: tuple[str, ...]
    strategy: str
    brief: str
    candidate: str = ""
    score: float = 0.0
    critique: str = ""
    status: str = "pending"  # pending | ok | failed
    inner_steps: int = 0
    reason: str = ""
    workspace: str = ""  # artifact directory, when the run has tools

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["op"] = self.op.value
        d["parents"] = list(self.parents)
        return d


class ThoughtGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, Thought] = {}
        self._seq = 0

    def new(self, generation: int, op: Op, parents: tuple[str, ...], strategy: str, brief: str) -> Thought:
        for p in parents:
            if p not in self._nodes:
                raise KeyError(f"unknown parent {p!r}")
        node = Thought(f"t{self._seq:04d}", generation, op, parents, strategy, brief)
        self._seq += 1
        self._nodes[node.id] = node
        return node

    def __getitem__(self, tid: str) -> Thought:
        return self._nodes[tid]

    def __iter__(self) -> Iterator[Thought]:
        return iter(self._nodes.values())

    def __len__(self) -> int:
        return len(self._nodes)

    def generation(self, g: int) -> list[Thought]:
        return [n for n in self if n.generation == g]

    def children(self, tid: str) -> list[Thought]:
        return [n for n in self if tid in n.parents]

    def lineage(self, tid: str) -> list[str]:
        """All ancestors of a thought, oldest first."""
        seen: set[str] = set()
        stack = list(self._nodes[tid].parents)
        while stack:
            p = stack.pop()
            if p not in seen:
                seen.add(p)
                stack.extend(self._nodes[p].parents)
        return sorted(seen)

    @staticmethod
    def ranked(nodes: Iterable[Thought]) -> list[Thought]:
        return sorted((n for n in nodes if n.ok), key=lambda n: (-n.score, n.id))

    def best(self) -> Thought | None:
        ranked = self.ranked(self)
        return ranked[0] if ranked else None

    def to_dict(self) -> dict[str, Any]:
        return {"nodes": [n.to_dict() for n in self]}

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    def digest(self) -> str:
        return hashlib.sha256(self.to_json(indent=None).encode()).hexdigest()

    def to_mermaid(self) -> str:
        lines = ["graph TD"]
        for n in self:
            mark = "" if n.ok else " ✗"
            lines.append(f'  {n.id}["{n.id} · {n.op.value}:{n.strategy} · {n.score:.2f}{mark}"]')
            lines.extend(f"  {p} --> {n.id}" for p in n.parents)
        return "\n".join(lines)
