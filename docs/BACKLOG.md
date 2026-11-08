# Lestinxor backlog

Agents take the first unchecked item that isn't marked 🔒 (see `AGENTS.md`).
How this repo links to Jonny's other systems (Sebastian, cross-testing, fix requests, Grok, Claude): `docs/SYSTEMS.md`. It is context only; `AGENTS.md` wins on any conflict.

## Ready

- [x] **L1: Cover the sandbox's refusal paths.** `lestinxor/sandbox.py` lines 75, 81, 134-136, 163-164 and 178-181 are untested (coverage report). These are the safety boundary's edge branches. Tests only: don't change sandbox behaviour.
  *Done when:* `sandbox.py` is at 100% line coverage and every new test asserts the refusal or error, not just that the code ran.
- [x] **L2: Cover convergence and help edge cases.** `convergence.py` lines 20, 22, 28 and `help.py` lines 24-26 are untested.
  *Done when:* both files are at 100% line coverage, with assertions on the returned values.
- [x] **L3: `python -m lestinxor` smoke test.** `lestinxor/__main__.py` has 0% coverage.
  *Done when:* a test runs `python -m lestinxor --help` in a subprocess and asserts exit code 0 and that the usage text names both `orchestrate` and `gauntlet`.

- [x] **L6: Owner signatures.** Owner-role entries (approvals, halts) are checked by name only, so anyone who can write rows can post as the owner. Add stdlib hash-based signatures and enforce them in `Ledger` when an owner key is configured. *Done when* unsigned/forged/reused owner entries are refused on post and replay, with tests.

- [x] **L10: Malformed ledger rows are refused cleanly.** Found by fuzzing (LinX hardening 7): replaying a row with a missing or wrongly typed field, a non-object line or broken JSON crashed with KeyError/TypeError/AttributeError instead of ProtocolError. *Done when* every damaged history raises ProtocolError and anything accepted is a true prefix (seeded fuzz tests, signed and unsigned).

## 🔒 Owner decision

- [x] **L5: Vendor the shared Gauntlet core.** Byte-identical copy of Sebastian's stdlib-only `gauntlet_core.py` (CORE_VERSION 1.0.0, SHA-256 pinned in both repos). Not wired into the inner loop yet; that is the next step and unblocks L4.

- [ ] 🔒 **L4: Command-checked TEST stage.** Sebastian's Gauntlet runs PREDICT-ed check commands in VERIFY. Lestinxor's `test` stage is judged by an LLM. Running checks through `sandbox.py` would close that gap, but it changes the inner loop's contract and touches the safety boundary.

## Proposed

(Agents add up to 3 items here when Ready is empty.)
- [x] **L7: Fix test dependency issue in `test_owner_sig.py`.** The test file `tests/test_owner_sig.py` currently attempts to import `FakeRest` and `URL` from `tests.test_unisync_supabase`. The codebase test coverage command `python -m pytest --cov=lestinxor` fails due to `ModuleNotFoundError: No module named 'tests'`.
  *Done when:* The import path in `test_owner_sig.py` is updated to correctly resolve the test dependency, and running `python -m pytest --cov=lestinxor` passes without failure.

- [x] **L8: Improve test coverage for `lestinxor/cli.py`.** The current coverage for `cli.py` has several missing lines. Add tests to cover argument parsing edge cases and command routing logic to increase coverage.
  *Done when:* Test file `tests/test_cli.py` is created or updated, covering the missing paths in `lestinxor/cli.py`, and the total coverage increases, maintaining the overall >85% requirement.

- [ ] **L9: Improve test coverage for `lestinxor/llm.py`.** `llm.py` has lower coverage than other core components. Add tests to cover LLM retry mechanisms and specific edge cases of `BaseException` handling.
  *Done when:* Test file `tests/test_llm.py` has tests added to cover the retry logic and initialization errors in `llm.py`.
