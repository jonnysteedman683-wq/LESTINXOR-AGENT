import runpy
import subprocess
import sys

import pytest


def test_main_help() -> None:
    """Run `python -m lestinxor --help` in a subprocess to ensure it works."""
    result = subprocess.run(
        [sys.executable, "-m", "lestinxor", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "orchestrate" in result.stdout
    assert "gauntlet" in result.stdout


def test_main_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Import __main__ to cover it in the same process."""
    monkeypatch.setattr(sys, "argv", ["lestinxor", "--help"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("lestinxor.__main__", run_name="__main__")
    assert exc.value.code == 0
