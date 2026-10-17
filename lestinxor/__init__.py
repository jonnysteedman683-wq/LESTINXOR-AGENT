"""Lestinxor — a Gauntlet-driven agent: gated, self-correcting stage loops under a Governor."""
from .clock import ManualClock, SystemClock
from .contracts import Attempt, Context, Gate, Result, Verdict
from .gauntlet import Gauntlet
from .governor import Budget, Governor
from .help import ConsoleHelp, NoHelp, ScriptedHelp
from .llm import AnthropicLLM, ScriptedLLM
from .stages import DEFAULT_STAGES, JUDGE_SYSTEM, Stage, llm_judge, parse_gate
from .trace import Trace

__version__ = "0.1.0"
__all__ = [
    "Attempt", "AnthropicLLM", "Budget", "ConsoleHelp", "Context", "DEFAULT_STAGES", "Gate",
    "Gauntlet", "Governor", "JUDGE_SYSTEM", "ManualClock", "NoHelp", "Result", "ScriptedHelp",
    "ScriptedLLM", "Stage", "SystemClock", "Trace", "Verdict", "llm_judge", "parse_gate",
]
