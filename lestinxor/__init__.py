"""Lestinxor — grand orchestrator. Outer loop: evolutionary thought search. Inner loop: the Gauntlet."""

from .clock import ManualClock, SystemClock
from .contracts import Attempt, Context, Gate, Result, Verdict
from .convergence import ConvergencePolicy, ConvergenceTracker, diversity
from .gauntlet import Gauntlet
from .governor import Budget, Governor
from .help import ConsoleHelp, NoHelp, ScriptedHelp
from .llm import AnthropicLLM, OpenAICompatLLM, RetryingLLM, ScriptedLLM
from .operators import SYNTH_SYSTEM
from .orchestrator import OrchestrationResult, Orchestrator, OrchestratorConfig
from .router import Router
from .scoring import FnScorer, LLMScorer, Score, parse_score
from .stages import COMPACT_STAGES, DEFAULT_STAGES, JUDGE_SYSTEM, Stage, llm_judge, parse_gate
from .strategies import DEFAULT_STRATEGIES, MUTATIONS, AdaptiveSampler, Strategy
from .thought import Op, Thought, ThoughtGraph
from .trace import Trace

__version__ = "0.2.0"
__all__ = [
    "COMPACT_STAGES",
    "DEFAULT_STAGES",
    "DEFAULT_STRATEGIES",
    "JUDGE_SYSTEM",
    "MUTATIONS",
    "SYNTH_SYSTEM",
    "AdaptiveSampler",
    "AnthropicLLM",
    "Attempt",
    "Budget",
    "ConsoleHelp",
    "Context",
    "ConvergencePolicy",
    "ConvergenceTracker",
    "FnScorer",
    "Gate",
    "Gauntlet",
    "Governor",
    "LLMScorer",
    "ManualClock",
    "NoHelp",
    "Op",
    "OpenAICompatLLM",
    "OrchestrationResult",
    "Orchestrator",
    "OrchestratorConfig",
    "Result",
    "RetryingLLM",
    "Router",
    "Score",
    "ScriptedHelp",
    "ScriptedLLM",
    "Stage",
    "Strategy",
    "SystemClock",
    "Thought",
    "ThoughtGraph",
    "Trace",
    "Verdict",
    "diversity",
    "llm_judge",
    "parse_gate",
    "parse_score",
]
