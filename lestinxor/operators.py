"""Outer-loop operators: turn graph state into briefs for the inner loop."""

from __future__ import annotations

from collections.abc import Sequence

from .strategies import Strategy
from .thought import Thought

SYNTH_SYSTEM = (
    "You are the Lestinxor synthesizer. Fuse multiple candidate solutions into one final answer "
    "that is strictly better than each. Output only the final answer."
)


def _block(label: str, node: Thought) -> str:
    return (
        f"{label} ({node.id}, score {node.score:.2f}):\n{node.candidate}\n\n"
        f"CRITIQUE OF {label}:\n{node.critique or 'none'}"
    )


def seed_brief(task: str, strategy: Strategy) -> str:
    return f"{task}\n\nAPPROACH DIRECTIVE ({strategy.name}): {strategy.directive}"


def mutate_brief(task: str, parent: Thought, mutation: Strategy) -> str:
    return (
        f"{task}\n\nYou are improving an existing candidate.\n"
        f"MUTATION ({mutation.name}): {mutation.directive}\n\n{_block('CANDIDATE', parent)}"
    )


def crossover_brief(task: str, a: Thought, b: Thought) -> str:
    return (
        f"{task}\n\nCROSSOVER: Combine the strongest elements of both candidates into one superior "
        f"solution. Resolve conflicts explicitly and fix both critiques.\n\n"
        f"{_block('CANDIDATE A', a)}\n\n{_block('CANDIDATE B', b)}"
    )


def synthesis_prompt(task: str, nodes: Sequence[Thought]) -> str:
    blocks = "\n\n".join(_block(f"CANDIDATE {i + 1}", n) for i, n in enumerate(nodes))
    return (
        f"TASK:\n{task}\n\n{blocks}\n\n"
        "Produce one final answer that merges the strongest elements, fixes every critique, "
        "and resolves conflicts between candidates."
    )
