# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

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
