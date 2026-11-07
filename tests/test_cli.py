import os
import runpy
import sys
import unittest
from unittest import mock

from lestinxor import cli


class AdditionalCLITests(unittest.TestCase):
    @mock.patch("lestinxor.cli.logging.basicConfig")
    @mock.patch("lestinxor.cli.Orchestrator")
    @mock.patch("lestinxor.cli.Router")
    @mock.patch("lestinxor.cli.Trace")
    def test_verbose_flag(self, mock_trace, mock_router, mock_orchestrator, mock_basic_config):
        mock_orch_instance = mock_orchestrator.return_value
        mock_graph = mock.Mock(__len__=lambda self: 1)
        mock_run_ret = mock.Mock(ok=True, best=None, stop_reason="done", generations=1, graph=mock_graph)
        mock_orch_instance.run.return_value = mock_run_ret

        args = ["orchestrate", "task", "--model", "m", "--verbose", "--workers", "1"]
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
            cli.main(args)

        mock_basic_config.assert_called_once()
        self.assertEqual(mock_basic_config.call_args[1]["level"], cli.logging.INFO)

    @mock.patch("lestinxor.cli.Orchestrator")
    @mock.patch("lestinxor.cli.Router")
    @mock.patch("lestinxor.cli.Trace")
    def test_allow_cmd_flag(self, mock_trace, mock_router, mock_orchestrator):
        mock_orch_instance = mock_orchestrator.return_value
        mock_graph = mock.Mock(__len__=lambda self: 1)
        mock_run_ret = mock.Mock(ok=True, best=None, stop_reason="done", generations=1, graph=mock_graph)
        mock_orch_instance.run.return_value = mock_run_ret

        with (
            mock.patch("lestinxor.cli.workspace_factory") as mock_factory,
            mock.patch("lestinxor.cli.allow_command") as mock_allow_cmd,
        ):
            mock_allow_cmd.return_value = lambda *a, **kw: None
            args = [
                "orchestrate",
                "task",
                "--model",
                "m",
                "--workers",
                "1",
                "--workspace",
                ".",
                "--allow-cmd",
                "ls",
                "--allow-cmd",
                "cat",
            ]
            with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "k"}):
                cli.main(args)

            mock_factory.assert_called_once()
            commands_arg = mock_factory.call_args[1]["commands"]
            self.assertIn("ls", commands_arg)
            self.assertIn("cat", commands_arg)

    def test_main_block(self):
        original_argv = sys.argv
        sys.argv = ["lestinxor", "--help"]
        try:
            with self.assertRaises(SystemExit) as cm:
                runpy.run_module("lestinxor.cli", run_name="__main__")
            self.assertEqual(cm.exception.code, 0)
        finally:
            sys.argv = original_argv


if __name__ == "__main__":
    unittest.main()
