"""Tools: typed, permission-scoped, budgeted — and they never raise into the agent loop."""

from __future__ import annotations

import inspect
import json
import typing
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .sandbox import Workspace

_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}
_PY_TO_JSON: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    description: str = ""
    required: bool = True

    def __post_init__(self) -> None:
        if self.type not in _TYPES:
            raise ValueError(f"unsupported param type {self.type!r}; valid: {sorted(_TYPES)}")


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str = ""
    error: str = ""

    def render(self) -> str:
        if self.ok:
            return self.output
        return f"ERROR: {self.error}" + (f"\n{self.output}" if self.output else "")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: tuple[Param, ...]
    fn: Callable[..., Any]
    scopes: frozenset[str] = frozenset()

    @classmethod
    def from_function(
        cls,
        fn: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        scopes: Iterable[str] = (),
    ) -> Tool:
        """Build a tool from a typed function: annotations become the schema, defaults make params optional."""
        hints = typing.get_type_hints(fn)
        params: list[Param] = []
        for p in inspect.signature(fn).parameters.values():
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                raise TypeError(f"{fn.__name__}: *args/**kwargs are not supported in tools")
            ann = hints.get(p.name, str)
            jtype = _PY_TO_JSON.get(typing.get_origin(ann) or ann)
            if jtype is None:
                raise TypeError(f"{fn.__name__}: unsupported annotation for {p.name!r}: {ann!r}")
            params.append(Param(p.name, jtype, required=p.default is inspect.Parameter.empty))
        doc = (inspect.getdoc(fn) or "").split("\n\n")[0].strip()
        return cls(name or fn.__name__, description or doc, tuple(params), fn, frozenset(scopes))

    def spec(self) -> dict[str, Any]:
        props = {
            p.name: {"type": p.type, **({"description": p.description} if p.description else {})} for p in self.params
        }
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": [p.name for p in self.params if p.required],
            },
        }

    def validate(self, args: Mapping[str, Any]) -> str | None:
        known = {p.name for p in self.params}
        unknown = sorted(set(args) - known)
        if unknown:
            return f"unknown argument(s): {unknown}"
        for p in self.params:
            if p.name not in args:
                if p.required:
                    return f"missing required argument: {p.name!r}"
                continue
            v = args[p.name]
            is_bool_as_number = isinstance(v, bool) and p.type in ("integer", "number")
            if not isinstance(v, _TYPES[p.type]) or is_bool_as_number:
                return f"argument {p.name!r} must be {p.type}, got {type(v).__name__}"
        return None


class Toolbox:
    """A set of tools plus the policy around them: granted scopes, a call budget and output caps."""

    def __init__(
        self,
        tools: Iterable[Tool] = (),
        *,
        granted: Iterable[str] = (),
        max_calls: int = 50,
        output_limit: int = 8000,
        workspace: Workspace | None = None,
    ) -> None:
        if max_calls < 1 or output_limit < 100:
            raise ValueError("max_calls must be >= 1 and output_limit >= 100")
        self._tools: dict[str, Tool] = {}
        self.granted = frozenset(granted)
        self.max_calls = max_calls
        self.output_limit = output_limit
        self.workspace = workspace
        self.calls = 0
        for t in tools:
            self.add(t)

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name!r}")
        self._tools[tool.name] = tool

    def tool(
        self, *, name: str | None = None, scopes: Iterable[str] = ()
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator: register a typed function as a tool."""

        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.add(Tool.from_function(fn, name=name, scopes=scopes))
            return fn

        return register

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return sorted(self._tools)

    def permitted(self, name: str, allowed: Iterable[str] | None = None) -> str | None:
        """None if the call is permitted, else the reason it is not."""
        tool = self._tools.get(name)
        if tool is None:
            return f"unknown tool {name!r}"
        if allowed is not None and name not in set(allowed):
            return f"tool {name!r} is not allowed in this stage"
        missing = tool.scopes - self.granted
        if missing:
            return f"tool {name!r} denied: needs scope(s) {sorted(missing)}"
        return None

    def specs(self, allowed: Iterable[str] | None = None) -> list[dict[str, Any]]:
        allow = None if allowed is None else set(allowed)
        return [t.spec() for n, t in sorted(self._tools.items()) if self.permitted(n, allow) is None]

    def call(self, name: str, args: Any, allowed: Iterable[str] | None = None) -> ToolResult:
        denied = self.permitted(name, allowed)
        if denied:
            return ToolResult(False, error=denied)
        if self.calls >= self.max_calls:
            return ToolResult(False, error=f"tool budget exhausted ({self.max_calls} calls)")
        self.calls += 1
        if not isinstance(args, Mapping):
            return ToolResult(False, error="args must be an object")
        tool = self._tools[name]
        problem = tool.validate(args)
        if problem:
            return ToolResult(False, error=problem)
        try:
            out = tool.fn(**args)
        except Exception as exc:  # tools never raise into the loop
            return ToolResult(False, error=f"{type(exc).__name__}: {exc}")
        if isinstance(out, ToolResult):
            result = out
        else:
            result = ToolResult(True, out if isinstance(out, str) else json.dumps(out, default=str, sort_keys=True))
        return self._truncate(result)

    def _truncate(self, r: ToolResult) -> ToolResult:
        n = len(r.output)
        if n <= self.output_limit:
            return r
        half = self.output_limit // 2
        clipped = f"{r.output[:half]}\n…[{n - 2 * half} chars truncated]…\n{r.output[-half:]}"
        return ToolResult(r.ok, clipped, r.error)
