# LESTINXOR-AGENT

[![CI](https://github.com/jonnysteedman683-wq/LESTINXOR-AGENT/actions/workflows/ci.yml/badge.svg)](https://github.com/jonnysteedman683-wq/LESTINXOR-AGENT/actions/workflows/ci.yml)

**Lestinxor is a grand orchestrator for AI work.** An **outer loop** searches a space of thoughts: it splits a task into divergent branches, mutates and adapts the strongest ones, and converges, repeating until the answer is good enough or the search stops improving. Every branch is executed by an **inner loop**, the Gauntlet: gated stages that retry, revert and escalate until each step passes.

Pure Python. Zero runtime dependencies. Strictly typed. Deterministic under a seed.

## Architecture

```
                          ┌──────────────────────── OUTER LOOP ────────────────────────┐
 task ─► DIVERGE ─► ┌─────────────┐ ─► SCORE ─► ADAPT ─► SELECT ─► converged? ──yes──► SYNTHESIZE ─► answer
          (seeds)   │ INNER LOOP  │   (fitness) (bandit)  (elite)      │ no
                    │ per thought │                                    ▼
                    │ plan        │◄──────── MUTATE · CROSSOVER · IMMIGRANTS (next generation)
                    │ execute     │
                    │ test        │   retry ↺   revert ↶   help ⇡     Governor: budgets, sole abort
                    └─────────────┘
```

| Phase | What happens | Code |
|---|---|---|
| **Split / diverge** | Seed branches from distinct strategies: first-principles, analogical, adversarial, minimalist, exhaustive, contrarian. | `strategies.py`, `operators.seed_brief` |
| **Execute** | Each thought runs the Gauntlet inner loop. Failed branches are contained and score 0. | `gauntlet.py` |
| **Score** | Fitness from 0 to 1, plus a critique that feeds the next mutation. | `scoring.py` |
| **Adapt** | A multiplicative-weights bandit shifts sampling toward the strategies and mutations that improved scores. | `AdaptiveSampler` |
| **Mutate** | Survivors are rewritten with `refine`, `simplify`, `harden` or `rethink`. Crossover merges two survivors. Immigrants keep exploring. | `operators.py` |
| **Converge** | Stops on target score, plateau, diversity collapse or generation budget. Synthesis is kept only if it scores no worse than the best branch. | `convergence.py` |
| **Trace** | Every thought, inner-loop attempt and adaptation is logged as JSONL. The thought graph has full lineage and exports to JSON or Mermaid. | `trace.py`, `thought.py` |

## Quickstart

```bash
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=...

# Outer + inner loop, a cheaper judge, 4 parallel branches, graph exported
lestinxor orchestrate "Design a rate limiter for a multi-tenant API" \
  --model <exact-model-string> --judge-model <exact-model-string> \
  --workers 4 --graph graph.json --mermaid graph.mmd -v

# Inner loop only
lestinxor gauntlet "Write a migration plan" --model <exact-model-string>

# Any OpenAI-compatible endpoint (vLLM, Ollama, OpenRouter, ...)
lestinxor orchestrate "..." --provider openai --base-url http://localhost:8000/v1 --model <name>
```

```python
from lestinxor import Orchestrator, OrchestratorConfig, ConvergencePolicy, Router, AnthropicLLM, RetryingLLM

work = RetryingLLM(AnthropicLLM("<exact-model-string>"))
judge = RetryingLLM(AnthropicLLM("<exact-model-string>"))
result = Orchestrator(
    Router(work, roles={"gate": judge, "score": judge}),
    OrchestratorConfig(population=6, workers=6, policy=ConvergencePolicy(target_score=0.9)),
).run("Design a rate limiter for a multi-tenant API")

print(result.answer, result.stop_reason)
print(result.graph.to_mermaid())
```

## Tools

Tools are opt-in and sandboxed. Without a toolbox, Lestinxor is a pure text agent.

```bash
# File tools only (read/write jailed to ./work)
lestinxor gauntlet "Write a CSV parser module" --model <m> --workspace ./work

# Plus execution, with the test stage gated on a real test run before the judge
lestinxor orchestrate "Implement an LRU cache with tests" --model <m> \
  --workspace ./runs --allow-exec --check "pytest -q" --workers 4
```

```python
from lestinxor import (
    Orchestrator,
    OrchestratorConfig,
    COMPACT_STAGES,
    chain,
    command_gate,
    llm_judge,
    with_gate,
    workspace_factory,
)

stages = with_gate(COMPACT_STAGES, "test", chain(command_gate(["pytest", "-q"]), llm_judge))
config = OrchestratorConfig(
    inner_stages=stages,
    toolbox_factory=workspace_factory("./runs", scopes=["fs.read", "fs.write", "exec"]),
)
result = Orchestrator(router, config).run("Implement an LRU cache with tests")
print(result.best.workspace)  # the winning thought's files
```

| Piece | What it guarantees |
|---|---|
| `Toolbox` | Every call is checked against granted scopes, the stage's allowlist, the arg schema and the call budget. Output is capped. Errors come back as results; nothing raises. |
| `Workspace` | File access is confined to its root. `..`, absolute paths and symlink escapes are refused. |
| `run_python` / `run_command` | Require the `exec` scope. Allowlisted argv only, no shell. Scrubbed environment (no API keys). CPU and memory rlimits. Timeout with process-group kill. |
| Tool loop | Works on any LLM backend. Each stage's output carries a `[TOOL LOG]`, so the judge sees evidence, not claims. |
| `command_gate` + `chain` | A real check such as `pytest` decides before the LLM judge is billed. A failure can retry, or revert upstream with the failure output as feedback. |
| `workspace_factory` | One workspace per thought. Children inherit their parent's files, so mutations evolve code rather than prose. |

> **Security:** `exec` runs model-written code. The sandbox scrubs secrets and limits resources, but it shares the host's network and your user's permissions. Grant `exec` only inside a container or VM.

## Model roles

| Role | Job |
|---|---|
| `inner` | Runs inner-loop stages. This is the bulk of the tokens. |
| `gate` | Judges each stage: pass, retry, revert or help. |
| `score` | Ranks thoughts and writes critiques. |
| `synth` | Fuses the final survivors into one answer. |

Model strings are always explicit. There are no defaults, so nothing goes stale silently.

## Guarantees

- **Determinism**: all randomness comes from one seeded `random.Random` on the main thread. Thought IDs are sequential. Parallel results are applied in ID order. Same seed and same model replies produce the same graph digest and trace digest, whether `workers=1` or `workers=N`.
- **Containment**: a branch that errors, exhausts its budget or fails its gates is marked `failed` with a reason. It never takes down the run.
- **Authority**: only the Governor aborts an inner loop, and only convergence or the budget ends the outer loop.
- **Resilience**: `RetryingLLM` retries transient failures (429, 5xx, timeouts, connection errors) with backoff. It never retries 4xx client errors.

## Development

```bash
ruff check . && ruff format --check .   # lint + format
mypy                                    # strict typing
python -m pytest --cov=lestinxor        # tests, 85% coverage gate
```

CI runs all three on Python 3.10–3.13. Agents follow `AGENTS.md` and paste this output into every PR.
How Lestinxor fits with Jonny's other agents (Sebastian, cross-testing, fix requests): [`docs/SYSTEMS.md`](docs/SYSTEMS.md).
The joint protocol all of them share (tasks, approvals, shared memory, hash-chained ledger): [`docs/UNISYNC.md`](docs/UNISYNC.md).

## Layout

```
lestinxor/
  orchestrator.py  outer loop: OrchestratorConfig, Orchestrator, OrchestrationResult
  thought.py       Thought, ThoughtGraph (lineage, JSON, Mermaid, digest)
  strategies.py    strategies, mutation operators, AdaptiveSampler
  operators.py     seed / mutate / crossover / synthesis briefs
  scoring.py       Score, LLMScorer, FnScorer
  convergence.py   ConvergencePolicy, diversity, ConvergenceTracker
  router.py        role-based model routing
  tools.py         Tool, Param, ToolResult, Toolbox
  sandbox.py       Workspace, file and exec tools, workspace_factory
  tool_loop.py     provider-agnostic tool-use loop, tool log
  gates.py         command_gate, chain, with_gate
  gauntlet.py      inner loop
  stages.py        Stage, DEFAULT_STAGES, COMPACT_STAGES, LLM judge
  governor.py      Budget, Governor
  contracts.py     Verdict, Gate, Attempt, Context, Result
  llm.py           LLM protocol, Anthropic, OpenAI-compatible, Retrying, Scripted
  trace.py         JSONL trace + digest
  clock.py         injectable clock
  help.py          operator help channels
  cli.py
```
