from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lestinxor.cli import main


def test_version(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out, _ = capsys.readouterr()
    assert "lestinxor" in out


@pytest.fixture(autouse=True)
def mock_anthropic_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")


def test_gauntlet_success(capsys) -> None:
    with patch("lestinxor.cli.Gauntlet") as mock_g:
        instance = MagicMock()
        instance.run.return_value.ok = True
        instance.run.return_value.deliverable = "Deliverable output"
        instance.run.return_value.status = "SUCCESS"
        instance.run.return_value.reason = "All good"
        mock_g.return_value = instance

        res = main(["gauntlet", "my_task", "--model", "dummy"])
        assert res == 0

        out, err = capsys.readouterr()
        assert "Deliverable output" in out
        assert "[SUCCESS] All good" in err


def test_gauntlet_failure(capsys) -> None:
    with patch("lestinxor.cli.Gauntlet") as mock_g:
        instance = MagicMock()
        instance.run.return_value.ok = False
        instance.run.return_value.deliverable = "Failed output"
        instance.run.return_value.status = "ERROR"
        instance.run.return_value.reason = "Something went wrong"
        mock_g.return_value = instance

        res = main(["gauntlet", "my_task", "-v", "--model", "dummy", "--unattended"])
        assert res == 1

        out, err = capsys.readouterr()
        assert "Failed output" in out
        assert "[ERROR] Something went wrong" in err


def test_orchestrate_success(capsys, tmp_path: Path) -> None:
    graph_path = tmp_path / "graph.json"
    mermaid_path = tmp_path / "graph.mermaid"

    with patch("lestinxor.cli.Orchestrator") as mock_o:
        instance = MagicMock()
        instance.run.return_value.ok = True
        instance.run.return_value.answer = "The Answer"
        instance.run.return_value.best = MagicMock(id="t1", score=0.9, workspace="ws1")
        instance.run.return_value.stop_reason = "Done"
        instance.run.return_value.generations = 3
        instance.run.return_value.graph = MagicMock()
        instance.run.return_value.graph.to_json.return_value = "{}"
        instance.run.return_value.graph.to_mermaid.return_value = "graph TD"
        instance.run.return_value.graph.__len__.return_value = 5
        mock_o.return_value = instance

        res = main(
            [
                "orchestrate",
                "my_task",
                "--model",
                "dummy",
                "--judge-model",
                "judge",
                "--graph",
                str(graph_path),
                "--mermaid",
                str(mermaid_path),
            ]
        )
        assert res == 0

        out, err = capsys.readouterr()
        assert "The Answer" in out
        assert "[Done] gens=3 thoughts=5 best=t1 score 0.90 workspace=ws1" in err
        assert graph_path.exists()
        assert mermaid_path.exists()


def test_orchestrate_failure_no_best(capsys) -> None:
    with patch("lestinxor.cli.Orchestrator") as mock_o:
        instance = MagicMock()
        instance.run.return_value.ok = False
        instance.run.return_value.answer = "No answer"
        instance.run.return_value.best = None
        instance.run.return_value.stop_reason = "Failed"
        instance.run.return_value.generations = 1
        instance.run.return_value.graph = MagicMock()
        instance.run.return_value.graph.__len__.return_value = 1
        mock_o.return_value = instance

        res = main(["orchestrate", "my_task", "--model", "dummy"])
        assert res == 1

        out, err = capsys.readouterr()
        assert "No answer" in out
        assert "[Failed] gens=1 thoughts=1 best=none" in err


def test_build_llm_openai(monkeypatch):
    monkeypatch.setenv("DUMMY_KEY", "secret")
    with patch("lestinxor.cli.Gauntlet"):
        main(
            [
                "gauntlet",
                "task",
                "--model",
                "dummy",
                "--provider",
                "openai",
                "--base-url",
                "http://localhost",
                "--api-key-env",
                "DUMMY_KEY",
            ]
        )


def test_build_llm_openai_missing_base_url():
    with pytest.raises(SystemExit) as exc:
        main(["gauntlet", "task", "--model", "dummy", "--provider", "openai"])
    assert "base-url is required" in str(exc.value)


def test_tool_kwargs_check_requires_allow_exec():
    with pytest.raises(SystemExit) as exc:
        main(["gauntlet", "task", "--model", "dummy", "--check", "pytest"])
    assert "--check requires --allow-exec" in str(exc.value)


def test_tool_kwargs_allow_exec_requires_workspace():
    with pytest.raises(SystemExit) as exc:
        main(["gauntlet", "task", "--model", "dummy", "--allow-exec"])
    assert "--allow-exec/--allow-cmd require --workspace" in str(exc.value)


def test_tool_kwargs_allow_cmd_requires_workspace():
    with pytest.raises(SystemExit) as exc:
        main(["gauntlet", "task", "--model", "dummy", "--allow-cmd", "ls"])
    assert "--allow-exec/--allow-cmd require --workspace" in str(exc.value)


def test_workspace_and_check(tmp_path: Path):
    with patch("lestinxor.cli.Gauntlet"):
        main(
            [
                "gauntlet",
                "task",
                "--model",
                "dummy",
                "--workspace",
                str(tmp_path),
                "--allow-exec",
                "--allow-cmd",
                "ls",
                "--check",
                "pytest -q",
            ]
        )
