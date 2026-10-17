"""Workspace-jailed built-in tools. File access is confined to the workspace; execution is opt-in.

Security model — read before granting `exec`:
  * file tools resolve every path (symlinks included) and refuse anything outside the workspace;
  * subprocesses get a scrubbed environment (no API keys), a CPU and address-space rlimit (POSIX),
    a wall-clock timeout, their own process group (killed whole on timeout) and an allowlisted argv
    with no shell;
  * they still share the host's network and the OS permissions of the user running Lestinxor.
    `exec` lets the model run arbitrary code: grant it inside a container or VM, never on a host
    with credentials you care about.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .tools import Tool, Toolbox, ToolResult

GiB = 1024**3
IGNORED_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})

DEFAULT_COMMANDS: dict[str, list[str]] = {
    "python": [sys.executable, "-E", "-s", "-B"],
    "pytest": [sys.executable, "-E", "-s", "-B", "-m", "pytest", "-p", "no:cacheprovider"],
}

# -B matters: -E ignores PYTHONDONTWRITEBYTECODE, and a rewrite with the same size inside the same mtime second
# would otherwise be served from stale bytecode — the model's fix would appear not to work.
# Applies rlimits, then replaces itself with the target program (same pid, so timeouts kill the target).
_LIMITER = (
    "import os,resource,sys\n"
    "cpu,mem=int(sys.argv[1]),int(sys.argv[2])\n"
    "def lim(r,v):\n"
    "    s,h=resource.getrlimit(r)\n"
    "    v=v if h==resource.RLIM_INFINITY else min(v,h)\n"
    "    resource.setrlimit(r,(v,v))\n"
    "lim(resource.RLIMIT_CPU,cpu)\n"
    "if mem: lim(resource.RLIMIT_AS,mem)\n"
    "os.execv(sys.argv[3],sys.argv[3:])\n"
)


class Workspace:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, rel: str) -> Path:
        """Resolve a workspace-relative path, refusing escapes via '..', absolute paths or symlinks."""
        p = (self.root / rel).resolve()
        if p != self.root and self.root not in p.parents:
            raise PermissionError(f"path escapes workspace: {rel!r}")
        return p

    def fork(self, dst: str | Path) -> Workspace:
        shutil.copytree(self.root, dst, symlinks=True, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*IGNORED_DIRS))
        return Workspace(dst)

    def __str__(self) -> str:
        return str(self.root)


def fs_tools(ws: Workspace, max_file_bytes: int = 1_000_000, max_entries: int = 500) -> list[Tool]:
    def read_file(path: str) -> str:
        """Read a UTF-8 text file from the workspace."""
        p = ws.resolve(path)
        if p.stat().st_size > max_file_bytes:
            raise ValueError(f"file exceeds {max_file_bytes} bytes")
        return p.read_text(encoding="utf-8", errors="replace")

    def write_file(path: str, content: str) -> str:
        """Create or overwrite a UTF-8 text file in the workspace; parent directories are created."""
        if len(content.encode()) > max_file_bytes:
            raise ValueError(f"content exceeds {max_file_bytes} bytes")
        p = ws.resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {p.relative_to(ws.root)}"

    def list_files(path: str = ".") -> str:
        """List files under a workspace directory, recursively."""
        base = ws.resolve(path)
        files = sorted(
            str(f.relative_to(ws.root))
            for f in base.rglob("*")
            if f.is_file() and not IGNORED_DIRS.intersection(f.relative_to(ws.root).parts)
        )
        more = len(files) - max_entries
        return ("\n".join(files[:max_entries]) + (f"\n… {more} more" if more > 0 else "")) or "(empty)"

    return [
        Tool.from_function(read_file, scopes={"fs.read"}),
        Tool.from_function(write_file, scopes={"fs.write"}),
        Tool.from_function(list_files, scopes={"fs.read"}),
    ]


def safe_env(ws: Workspace) -> dict[str, str]:
    """A minimal environment: nothing inherited except PATH, so no secrets leak into tool processes."""
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(ws.root),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONUNBUFFERED": "1",
    }


def _fmt(rc: int | None, out: Any, err: Any) -> str:
    def text(x: Any) -> str:
        return x.decode("utf-8", "replace") if isinstance(x, bytes) else (x or "")

    parts = [f"exit {rc}" if rc is not None else "killed (timeout)"]
    if text(out).strip():
        parts.append("--- stdout ---\n" + text(out).rstrip())
    if text(err).strip():
        parts.append("--- stderr ---\n" + text(err).rstrip())
    return "\n".join(parts)


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass


def run_sandboxed(
    argv: Sequence[str],
    ws: Workspace,
    *,
    timeout: float = 30.0,
    cpu_seconds: int = 60,
    memory_bytes: int = 2 * GiB,
    stdin: str | None = None,
) -> ToolResult:
    full = list(argv)
    if os.name == "posix":
        full = [sys.executable, "-E", "-s", "-B", "-c", _LIMITER, str(cpu_seconds), str(memory_bytes), *argv]
    try:
        proc = subprocess.Popen(
            full,
            cwd=ws.root,
            env=safe_env(ws),
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        return ToolResult(False, error=f"{type(exc).__name__}: {exc}")
    with proc:
        try:
            out, err = proc.communicate(stdin, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            out, err = proc.communicate()
            return ToolResult(False, _fmt(None, out, err), f"timed out after {timeout:g}s")
    rc = proc.returncode
    return ToolResult(rc == 0, _fmt(rc, out, err), "" if rc == 0 else f"exit code {rc}")


def allow_command(name: str) -> list[str]:
    """Resolve an executable on PATH for use in a command allowlist."""
    path = shutil.which(name)
    if path is None:
        raise FileNotFoundError(f"command {name!r} not found on PATH")
    return [path]


def exec_tools(
    ws: Workspace,
    commands: Mapping[str, Sequence[str]] | None = None,
    *,
    timeout: float = 30.0,
    cpu_seconds: int = 60,
    memory_bytes: int = 2 * GiB,
) -> list[Tool]:
    cmds = {k: list(v) for k, v in (DEFAULT_COMMANDS if commands is None else commands).items()}
    limits: dict[str, Any] = {"timeout": timeout, "cpu_seconds": cpu_seconds, "memory_bytes": memory_bytes}

    def run_python(code: str) -> ToolResult:
        """Run a Python snippet with the workspace as working directory. Returns exit code, stdout and stderr."""
        return run_sandboxed([*cmds.get("python", DEFAULT_COMMANDS["python"]), "-c", code], ws, **limits)

    def run_command(argv: list[str]) -> ToolResult:
        if not argv or not all(isinstance(a, str) for a in argv):
            return ToolResult(False, error="argv must be a non-empty list of strings")
        prefix = cmds.get(argv[0])
        if prefix is None:
            return ToolResult(False, error=f"command {argv[0]!r} not allowed; allowed: {sorted(cmds)}")
        return run_sandboxed([*prefix, *argv[1:]], ws, **limits)

    allowed = ", ".join(sorted(cmds))
    return [
        Tool.from_function(run_python, scopes={"exec"}),
        Tool.from_function(
            run_command,
            scopes={"exec"},
            description=f"Run an allowlisted program in the workspace (no shell). argv[0] must be one of: {allowed}.",
        ),
    ]


def sandbox_toolbox(
    workspace: Workspace | str | Path,
    *,
    scopes: Iterable[str] = ("fs.read", "fs.write"),
    commands: Mapping[str, Sequence[str]] | None = None,
    timeout: float = 30.0,
    cpu_seconds: int = 60,
    memory_bytes: int = 2 * GiB,
    max_calls: int = 50,
    output_limit: int = 8000,
) -> Toolbox:
    """File tools plus exec tools. `exec` is NOT granted by default — add it to `scopes` explicitly."""
    ws = workspace if isinstance(workspace, Workspace) else Workspace(workspace)
    tools = [
        *fs_tools(ws),
        *exec_tools(ws, commands, timeout=timeout, cpu_seconds=cpu_seconds, memory_bytes=memory_bytes),
    ]
    return Toolbox(tools, granted=scopes, max_calls=max_calls, output_limit=output_limit, workspace=ws)


ToolboxFactory = Callable[[str, tuple[str, ...]], Toolbox]


def workspace_factory(root: str | Path, **toolbox_kwargs: Any) -> ToolboxFactory:
    """One workspace per thought under `root`. A child starts as a copy of its first parent's workspace,
    so mutations evolve real artifacts rather than re-describing them."""
    base = Path(root)

    def make(thought_id: str, parents: tuple[str, ...]) -> Toolbox:
        dst = base / thought_id
        parent_dir = base / parents[0] if parents else None
        ws = Workspace(parent_dir).fork(dst) if parent_dir and parent_dir.is_dir() else Workspace(dst)
        return sandbox_toolbox(ws, **toolbox_kwargs)

    return make
