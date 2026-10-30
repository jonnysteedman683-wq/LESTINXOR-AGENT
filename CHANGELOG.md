# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `docs/SYSTEMS.md` describes how Jonny's agent systems link: who does what (owner, Jules, Claude, Grok), Sebastian vs Lestinxor rules, and the cross-test, fix-request, evolve and Gauntlet processes. The same file is in the Sebastian repo. Linked from `docs/BACKLOG.md` and the README.
- **UniSync joint protocol** (`lestinxor/unisync.py`, `docs/UNISYNC.md`, `unisync/schema.sql`): one append-only, hash-chained ledger every agent posts tasks, claims, results, memory writes, approvals and escalations to. Enforces role authority (only the owner decides approvals), the task lifecycle (single claimant, approval-gated tasks can't be claimed until approved), scoped last-write-wins shared memory, and tamper-evident JSONL replay. Includes a Supabase schema with append-only and no-fork guarantees.

## [0.3.0] — 2026-10-17

### Added
- **Tool execution.** `Tool`, `Param`, `ToolResult` and `Toolbox`:
  - Schemas are inferred from type hints.
  - Every call is validated, permission-scoped (`fs.read`, `fs.write`, `exec`) and budgeted.
  - Output is capped, and tool errors never raise into the loop.
- **Sandbox** (`sandbox.py`):
  - A `Workspace` jail that refuses `..`, absolute-path and symlink escapes.
  - File tools: `read_file`, `write_file`, `list_files`.
  - Opt-in exec tools: `run_python`, and `run_command` restricted to an allowlist with no shell.
  - Subprocesses get a scrubbed environment (no API keys), CPU and address-space rlimits, a timeout, and a process-group kill on timeout.
- **Tool loop** (`tool_loop.py`): a provider-agnostic text protocol that works with any backend. A `[TOOL LOG]` of each stage's calls is appended to its output, so gates and later stages see what actually ran.
- **Gates** (`gates.py`): `command_gate` makes a stage pass or fail on a real command. `chain` runs cheap deterministic checks before the LLM judge. `with_gate` swaps a stage's gate.
- **Per-thought workspaces** (`workspace_factory`): each branch gets its own workspace, and a child starts as a copy of its parent's, so mutations evolve real artifacts.
- **CLI flags**: `--workspace`, `--allow-exec`, `--allow-cmd`, `--check`, `--tool-timeout`.

### Changed
- Default `plan`, `execute` and `test` stages declare tool allowlists. These take effect only when a toolbox is supplied.
- The LLM judge is told to trust the `[TOOL LOG]` over claims in the prose.
- Text synthesis is skipped when tools are on, because the best thought's workspace is the deliverable.

### Fixed
- Sandboxed Python runs with `-B`. `-E` ignores `PYTHONDONTWRITEBYTECODE`, so a same-size rewrite could be served from stale bytecode.

## [0.2.0] — 2026-10-17

### Added
- **Outer loop (`Orchestrator`)**: an evolutionary thought search over inner-loop runs. It diverges into strategy-seeded branches, then mutates, cross-breeds and injects immigrants. Survivors are elite-selected, and the run converges on one of four signals: target score, plateau, diversity collapse or generation budget. A final synthesis is kept only if it scores no worse than the best branch.
- **Thought graph**: full lineage of every thought, with JSON and Mermaid export and a content digest.
- **Adaptive sampler**: a multiplicative-weights bandit that learns which strategies and mutation operators pay off.
- **Role-based `Router`**: different models for the `inner`, `gate`, `score` and `synth` roles.
- **Parallel branches** (`workers`): results are identical to sequential runs, verified by graph and trace digests.
- **Backends**: `OpenAICompatLLM` for any `/chat/completions` endpoint, and `RetryingLLM` with deterministic exponential backoff on transient errors.
- **Scoring**: `LLMScorer` and `FnScorer`.
- **CLI**: `lestinxor orchestrate` and `lestinxor gauntlet`, with `--judge-model`, `--graph` and `--mermaid`.
- **Tooling**: CI on Python 3.10–3.13 (ruff, strict mypy, pytest with an 85% coverage gate), `py.typed`, `CHANGELOG.md`.

### Changed
- `Gauntlet` accepts a separate `judge` LLM for gates.
- `Trace.digest()` excludes timestamps by default, so parallel and sequential runs compare equal.
- `Result.deliverable` falls back to the last accepted artifact.

## [0.1.0] — 2026-10-17

### Added
- Inner loop (`Gauntlet`): gated stages with retry, revert and help verdicts, under a `Governor` that is the sole abort authority.
