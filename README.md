# LESTINXOR-AGENT

Lestinxor is an agent built on the **Gauntlet**. Every task passes through a sequence of gated stages. A stage loops until its gate passes, rolls back upstream when it finds a fault there, and escalates to a human when it is stuck. A **Governor** enforces budgets and is the only component allowed to abort a run.

Pure Python, zero dependencies, deterministic under an injected clock.

## Flow

```
understand → brainstorm → predict → compare → plan → execute → test → reflect
     ↺ retry (with feedback)    ↶ revert to an earlier stage    ⇡ help (operator)
                     Governor: iteration / step / revert / help budgets
```

| Verdict  | Effect |
|----------|--------|
| `pass`   | Accept the output and advance. |
| `retry`  | Rerun the stage with the gate's reason fed back in. |
| `revert` | Drop artifacts from the target stage onward and resume there. |
| `help`   | Ask the operator; their guidance is fed back in. With no answer, the run aborts. |
| `abort`  | Governor only. Ends the run. |

Governor rules: a retry at the iteration cap becomes `help`. A revert to an invalid or non-earlier stage becomes `retry`. A revert past the revert cap becomes `help`. A help request past the help cap becomes `abort`. Exceeding the step cap aborts.

## Quickstart

```bash
pip install -e .
export ANTHROPIC_API_KEY=...
lestinxor "Design a rate limiter for a multi-tenant API" --model <exact-model-string>
python -m unittest discover -s tests
```

## Layout

```
lestinxor/
  contracts.py   Verdict, Gate, Attempt, Context, Result: pure data
  stages.py      Stage, DEFAULT_STAGES, LLM judge, gate parsing
  governor.py    Budget + Governor (verdict review, sole abort authority)
  gauntlet.py    the state machine
  help.py        NoHelp / ConsoleHelp / ScriptedHelp
  llm.py         LLM protocol, ScriptedLLM, zero-dep AnthropicLLM
  trace.py       append-only JSONL trace + digest for determinism checks
  clock.py       injectable clock (SystemClock / ManualClock)
  cli.py
```

## Extending

- **Custom stages:** pass any sequence of `Stage(name, instruction, gate=None, criteria="")`.
- **Rule gates:** `gate=lambda ctx, stage, output, iteration, llm: Gate(Verdict.PASS)` for deterministic checks such as running tests or validating a schema. Leave `gate=None` to use the LLM judge.
- **Other models:** implement `complete(system, prompt) -> str`.

## Design rules

- Determinism first: no wall-clock reads outside `clock.py`, so traces are reproducible and DST-ready.
- Only the Governor aborts. Gates can only request `help`.
- Model strings are always explicit. There are no stale defaults.
