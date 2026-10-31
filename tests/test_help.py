from unittest.mock import patch

from lestinxor.contracts import Context
from lestinxor.help import ConsoleHelp, NoHelp, ScriptedHelp


def test_console_help_ask():
    ctx = Context(task="test")
    help_channel = ConsoleHelp()

    with patch("builtins.input", return_value="some answer"):
        with patch("builtins.print") as mock_print:
            answer = help_channel.ask("some question", ctx)
            assert answer == "some answer"
            mock_print.assert_called_with("\n[LESTINXOR needs help] some question")

    with patch("builtins.input", return_value="   "):
        with patch("builtins.print") as mock_print:
            answer = help_channel.ask("some question", ctx)
            assert answer is None


def test_no_help_ask():
    ctx = Context(task="test")
    help_channel = NoHelp()
    assert help_channel.ask("some question", ctx) is None


def test_scripted_help_ask():
    ctx = Context(task="test")
    help_channel = ScriptedHelp(["answer 1", "answer 2"])

    assert help_channel.ask("q1", ctx) == "answer 1"
    assert help_channel.ask("q2", ctx) == "answer 2"
    assert help_channel.ask("q3", ctx) is None

    assert help_channel.questions == ["q1", "q2", "q3"]
