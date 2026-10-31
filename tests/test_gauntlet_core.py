"""The vendored Gauntlet core stays byte-identical to Sebastian's and behaves the same."""

from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path

import pytest

import lestinxor.gauntlet_core as core
from lestinxor.gauntlet_core import GauntletError, Stage, next_stage, parse_prediction, parse_verdict

CORE = Path(core.__file__)
# Must equal the pin in Sebastian's tests/test_gauntlet_core.py. Update both together.
PINNED = {"1.0.0": "5d0e9c298f5d9a00c8fcd24d4724c07132d35fe13d5d5c56ee95d571c8e8415a"}


class Policy:
    max_loops = 3
    replan_after = 2
    checks_required = True


def test_vendored_copy_matches_sebastian_pin() -> None:
    digest = hashlib.sha256(CORE.read_bytes()).hexdigest()
    assert PINNED.get(core.CORE_VERSION) == digest, (core.CORE_VERSION, digest)


def test_core_imports_only_the_standard_library() -> None:
    names: set[str] = set()
    for node in ast.walk(ast.parse(CORE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            names.add((node.module or "").split(".")[0])
    stdlib = getattr(sys, "stdlib_module_names", None)
    if stdlib is None:  # pragma: no cover - Python < 3.10
        pytest.skip("needs sys.stdlib_module_names")
    assert names <= set(stdlib) | {"__future__"}, names


def test_stage_formats_as_value() -> None:
    assert f"{Stage.VERIFY} failed" == "verify failed"


@pytest.mark.parametrize(
    ("stage", "passed", "loops", "expect"),
    [
        (Stage.PLAN, True, 0, (Stage.PREDICT, 0)),
        (Stage.ACT, False, 0, (Stage.HELP, 0)),
        (Stage.VERIFY, False, 0, (Stage.ACT, 1)),
        (Stage.CRITIQUE, False, 1, (Stage.PLAN, 2)),
        (Stage.VERIFY, False, 2, (Stage.HELP, 3)),
    ],
)
def test_next_stage(stage: Stage, passed: bool, loops: int, expect: tuple[Stage, int]) -> None:
    t = next_stage(stage, passed, loops, Policy())
    assert (t.stage, t.loops) == expect


def test_next_stage_rejects_terminal() -> None:
    with pytest.raises(GauntletError):
        next_stage(Stage.DONE, True, 0, Policy())


def test_parse_prediction_strict() -> None:
    p = parse_prediction("<checks>\nfile: src/a.py\nrun: pytest -q\n</checks>")
    assert p.files == ("src/a.py",) and p.commands == ("pytest -q",)
    for bad in (
        "no block",
        "<checks>\nfile: ../etc\n</checks>",
        "<checks>\nrun: pytest | tee x\n</checks>",
        "<checks>\nfile: a.py\n</checks>",  # code change without a check
    ):
        with pytest.raises(GauntletError):
            parse_prediction(bad)


def test_parse_verdict_strict() -> None:
    assert parse_verdict("VERDICT: PASS").passed
    v = parse_verdict("VERDICT: FAIL\n- breaks the API")
    assert not v.passed and v.findings == ("breaks the API",)
    assert parse_verdict("looks fine to me").malformed
    assert not parse_verdict("VERDICT: PASS\n- but one nit").passed
