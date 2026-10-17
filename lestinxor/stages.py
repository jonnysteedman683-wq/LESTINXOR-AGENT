"""Stages and gates. A stage produces output; its gate decides the verdict."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from .contracts import Context, Gate, Verdict
from .llm import LLM

GateFn = Callable[[Context, "Stage", str, int, LLM], Gate]

SYSTEM = (
    "You are Lestinxor, an agent that works through a gated sequence of stages. "
    "Do only the current stage. Build on accepted earlier stages. "
    "Address every piece of feedback from previous attempts explicitly."
)

JUDGE_SYSTEM = (
    "You are the Lestinxor gatekeeper. Be strict and specific. "
    "Reply with a single JSON object and nothing else."
)


@dataclass(frozen=True)
class Stage:
    name: str
    instruction: str
    gate: GateFn | None = None  # None -> LLM judge
    criteria: str = ""

    def prompt(self, ctx: Context) -> str:
        parts = [f"TASK:\n{ctx.task}"]
        for name, output in ctx.artifacts.items():
            parts.append(f"[{name.upper()} — accepted]\n{output}")
        notes = ctx.feedback.get(self.name)
        if notes:
            parts.append("FEEDBACK ON PREVIOUS ATTEMPTS (address all):\n"
                         + "\n".join(f"- {n}" for n in notes))
        parts.append(f"CURRENT STAGE: {self.name}\n{self.instruction}")
        return "\n\n".join(parts)

    def run(self, ctx: Context, llm: LLM) -> str:
        return llm.complete(SYSTEM, self.prompt(ctx))

    def evaluate(self, ctx: Context, output: str, iteration: int, llm: LLM) -> Gate:
        return (self.gate or llm_judge)(ctx, self, output, iteration, llm)


def llm_judge(ctx: Context, stage: Stage, output: str, iteration: int, llm: LLM) -> Gate:
    earlier = list(ctx.artifacts)
    criteria = stage.criteria or "Complete, correct, and directly usable by the next stage."
    prompt = (
        f"TASK:\n{ctx.task}\n\n"
        f"STAGE: {stage.name}\nINSTRUCTION: {stage.instruction}\nCRITERIA: {criteria}\n\n"
        f"OUTPUT (attempt {iteration}):\n{output}\n\n"
        'Return {"verdict": "pass"|"retry"|"revert"|"help", "reason": "...", "revert_to": <stage|null>}.\n'
        f"- revert only if the flaw originates upstream; revert_to must be one of {earlier}.\n"
        "- help only if progress needs information or a decision you cannot supply.\n"
        "- retry reasons must say exactly what to fix."
    )
    return parse_gate(llm.complete(JUDGE_SYSTEM, prompt))


def parse_gate(raw: str) -> Gate:
    start, end = raw.find("{"), raw.rfind("}")
    data = None
    if start != -1 and end > start:
        try:
            data = json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict):
        return Gate(Verdict.RETRY, "gate: judge reply was not valid JSON")
    try:
        verdict = Verdict(str(data.get("verdict", "")).lower())
    except ValueError:
        return Gate(Verdict.RETRY, f"gate: unknown verdict {data.get('verdict')!r}")
    if verdict is Verdict.ABORT:  # only the Governor may abort
        verdict = Verdict.HELP
    return Gate(verdict, str(data.get("reason", "")), data.get("revert_to") or None)


DEFAULT_STAGES: tuple[Stage, ...] = (
    Stage("understand",
          "Restate the goal. List constraints, testable success criteria, and open unknowns.",
          criteria="Success criteria are concrete and testable; unknowns are flagged, not guessed."),
    Stage("brainstorm",
          "Generate 3-5 genuinely distinct approaches. One line of rationale each.",
          criteria="Approaches differ in mechanism, not just wording."),
    Stage("predict",
          "For each approach, predict the outcome, failure modes, cost, and likelihood of success.",
          criteria="Every approach has specific failure modes, not generic risks."),
    Stage("compare",
          "Score approaches against the success criteria using the predictions. Choose one and justify it.",
          criteria="Choice follows from the scores; trade-offs are explicit."),
    Stage("plan",
          "Write ordered steps for the chosen approach. Give each step a verification check.",
          criteria="Every step is actionable and has a check."),
    Stage("execute",
          "Carry out the plan and produce the full deliverable.",
          criteria="Deliverable is complete and follows the plan."),
    Stage("test",
          "Check the deliverable against every success criterion and plan check. Report pass/fail per item.",
          criteria="Pass only if every criterion passes. On failure, revert to the stage where the fault originates."),
    Stage("reflect",
          "State lessons learned, residual risks, and what to do differently next time.",
          criteria="Specific and actionable."),
)
