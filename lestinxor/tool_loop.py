"""Provider-agnostic tool-use loop over plain text — works with any `complete(system, prompt)` backend."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .llm import LLM
from .tools import Toolbox, ToolResult

TOOL_PROTOCOL = """\
TOOLS
You can act through tools. To call one, include exactly one fenced block in your reply:
```tool
{"name": "<tool name>", "args": {...}}
```
You will receive the result and may call again. Verify by running things rather than asserting.
When finished, reply with your final answer and no tool block.
AVAILABLE TOOLS (JSON schema):"""

_BLOCK = re.compile(r"```tool[ \t]*\n?(.*?)```", re.S)


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]
    result: ToolResult


def parse_tool_call(reply: str) -> tuple[str, dict[str, Any]] | ToolResult | None:
    """(name, args) for a valid call, an error ToolResult for a malformed one, None when there is no call."""
    m = _BLOCK.search(reply)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        return ToolResult(False, error=f"malformed tool call JSON: {exc.msg}")
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        return ToolResult(False, error='tool call must be {"name": <string>, "args": <object>}')
    args = data.get("args", {})
    if not isinstance(args, dict):
        return ToolResult(False, error="tool call args must be an object")
    return data["name"], args


def run_with_tools(
    llm: LLM,
    system: str,
    prompt: str,
    toolbox: Toolbox,
    allowed: Iterable[str] | None = None,
    max_calls: int = 8,
    on_call: Callable[[ToolCall], None] | None = None,
) -> tuple[str, list[ToolCall]]:
    allow = None if allowed is None else tuple(allowed)
    specs = toolbox.specs(allow)
    if not specs:
        return llm.complete(system, prompt), []
    sys_prompt = f"{system}\n\n{TOOL_PROTOCOL}\n{json.dumps(specs, indent=1, sort_keys=True)}"
    transcript = prompt
    calls: list[ToolCall] = []
    for _ in range(max_calls):
        reply = llm.complete(sys_prompt, transcript)
        parsed = parse_tool_call(reply)
        if parsed is None:
            return reply.strip(), calls
        if isinstance(parsed, ToolResult):
            call = ToolCall("<invalid>", {}, parsed)
        else:
            call = ToolCall(parsed[0], parsed[1], toolbox.call(parsed[0], parsed[1], allow))
        calls.append(call)
        if on_call:
            on_call(call)
        transcript += f"\n\n[YOU]\n{reply.strip()}\n\n[TOOL RESULT: {call.name}]\n{call.result.render()}"
    final = llm.complete(
        sys_prompt, transcript + "\n\n[SYSTEM]\nTool budget exhausted. Give your final answer now without tools."
    )
    return _BLOCK.sub("", final).strip(), calls


def format_tool_log(calls: Iterable[ToolCall], tail: int = 400) -> str:
    """Compact evidence appended to a stage's output, so gates and later stages see what actually ran."""
    lines = ["[TOOL LOG]"]
    for c in calls:
        status = "ok" if c.result.ok else f"failed: {c.result.error}"
        args = json.dumps(c.args, sort_keys=True)
        lines.append(f"- {c.name}({args[:200] + '…' if len(args) > 200 else args}) → {status}")
        out = c.result.output.strip()
        if out:
            lines.append("  " + (out if len(out) <= tail else "…" + out[-tail:]).replace("\n", "\n  "))
    return "\n".join(lines)
