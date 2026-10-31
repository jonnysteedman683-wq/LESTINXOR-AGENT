# Lestinxor backlog

Agents take the first unchecked item that isn't marked 🔒 (see `AGENTS.md`).
How this repo links to Jonny's other systems (Sebastian, cross-testing, fix requests, Grok, Claude): `docs/SYSTEMS.md`. It is context only; `AGENTS.md` wins on any conflict.

## Ready

- [x] **L1: Cover the sandbox's refusal paths.** `lestinxor/sandbox.py` lines 75, 81, 134-136, 163-164 and 178-181 are untested (coverage report). These are the safety boundary's edge branches. Tests only: don't change sandbox behaviour.
  *Done when:* `sandbox.py` is at 100% line coverage and every new test asserts the refusal or error, not just that the code ran.
- [ ] **L2: Cover convergence and help edge cases.** `convergence.py` lines 20, 22, 28 and `help.py` lines 24-26 are untested.
  *Done when:* both files are at 100% line coverage, with assertions on the returned values.
- [ ] **L3: `python -m lestinxor` smoke test.** `lestinxor/__main__.py` has 0% coverage.
  *Done when:* a test runs `python -m lestinxor --help` in a subprocess and asserts exit code 0 and that the usage text names both `orchestrate` and `gauntlet`.

## 🔒 Owner decision

- [x] **L5: Vendor the shared Gauntlet core.** Byte-identical copy of Sebastian's stdlib-only `gauntlet_core.py` (CORE_VERSION 1.0.0, SHA-256 pinned in both repos). Not wired into the inner loop yet; that is the next step and unblocks L4.

- [ ] 🔒 **L4: Command-checked TEST stage.** Sebastian's Gauntlet runs PREDICT-ed check commands in VERIFY. Lestinxor's `test` stage is judged by an LLM. Running checks through `sandbox.py` would close that gap, but it changes the inner loop's contract and touches the safety boundary.

## Proposed

(Agents add up to 3 items here when Ready is empty.)
