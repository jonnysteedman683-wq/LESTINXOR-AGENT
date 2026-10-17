"""Lestinxor — grand orchestrator. Outer loop: evolutionary thought search. Inner loop: the Gauntlet."""

from .clock import ManualClock, SystemClock
from .contracts import Attempt, Context, Gate, Result, Verdict
from .convergence import ConvergencePolicy, ConvergenceTracker, diversity
from .gates import chain, command_gate, with_gate
from .gauntlet import Gauntlet
from .governor import Budget, Governor
from .help import ConsoleHelp, NoHelp, ScriptedHelp
from .llm import AnthropicLLM, OpenAICompatLLM, RetryingLLM, ScriptedLLM
from .operators import SYNTH_SYSTEM
from .orchestrator import OrchestrationResult, Orchestrator, OrchestratorConfig
from .router import Router
from .sandbox import DEFAULT_COMMANDS, Workspace, allow_command, sandbox_toolbox, workspace_factory
from .scoring import FnScorer, LLMScorer, Score, parse_score
from .stages import COMPACT_STAGES, DEFAULT_STAGES, JUDGE_SYSTEM, Stage, llm_judge, parse_gate
from .strategies import DEFAULT_STRATEGIES, MUTATIONS, AdaptiveSampler, Strategy
from .thought import Op, Thought, ThoughtGraph
from .tool_loop import ToolCall, parse_tool_call, run_with_tools
from .tools import Param, Tool, Toolbox, ToolResult
from .trace import Trace

__version__ = "0.3.0"
__all__ = [
    "COMPACT_STAGES",
    "DEFAULT_COMMANDS",
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
    "Param",
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
    "Tool",
    "ToolCall",
    "ToolResult",
    "Toolbox",
    "Trace",
    "Verdict",
    "Workspace",
    "allow_command",
    "chain",
    "command_gate",
    "diversity",
    "llm_judge",
    "parse_gate",
    "parse_score",
    "parse_tool_call",
    "run_with_tools",
    "sandbox_toolbox",
    "with_gate",
    "workspace_factory",
]
