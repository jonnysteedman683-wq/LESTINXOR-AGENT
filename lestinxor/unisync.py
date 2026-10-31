"""UniSync: the joint protocol every agent in Jonny's systems speaks.

One append-only, hash-chained ledger of envelopes. Agents post tasks, claims, results, memory writes,
approval requests and escalations; only the owner decides approvals. The ledger enforces sender
authority and the task lifecycle, so an agent cannot claim unapproved work or report a result for a
task it never claimed. Pure data and hashing: no network, no wall clock (time is injected), no
runtime dependencies. Same posts in the same order give the same chain digests.

The Supabase schema in ``unisync/schema.sql`` mirrors this ledger so agents in other repos can share it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .clock import Clock, SystemClock

PROTOCOL_VERSION = "unisync/1"
GENESIS = "0" * 64


class ProtocolError(ValueError):
    """A post that breaks the protocol. Nothing is appended when this is raised."""


class Role(str, Enum):
    OWNER = "owner"  # decides approvals; the only role that can
    ORCHESTRATOR = "orchestrator"  # plans and dispatches work
    WORKER = "worker"  # claims and completes tasks
    WATCHER = "watcher"  # observes, reports and escalates; never executes


class Kind(str, Enum):
    TASK_REQUEST = "task.request"
    TASK_CLAIM = "task.claim"
    TASK_RESULT = "task.result"
    TASK_FAILED = "task.failed"
    MEMORY_WRITE = "memory.write"
    APPROVAL_REQUEST = "approval.request"
    APPROVAL_DECISION = "approval.decision"
    ESCALATE = "escalate"
    HEARTBEAT = "heartbeat"


@dataclass(frozen=True)
class AgentCard:
    name: str
    role: Role
    repos: tuple[str, ...] = ()


DEFAULT_AGENTS: tuple[AgentCard, ...] = (
    AgentCard("jonny", Role.OWNER),
    AgentCard("lestinxor", Role.ORCHESTRATOR, ("jonnysteedman683-wq/LESTINXOR-AGENT",)),
    AgentCard("sebastian", Role.WORKER, ("jonnysteedman683-wq/Sebastian",)),
    AgentCard("jules", Role.WORKER),
    AgentCard("claude", Role.WORKER),
    AgentCard("hermes", Role.WORKER),
    AgentCard("o0", Role.ORCHESTRATOR, ("jonnysteedman683-wq/O0",)),
    AgentCard("grok", Role.WATCHER, ("jonnysteedman683-wq/Sebastian",)),
    AgentCard("coordinator-bot", Role.WATCHER),
)

_ALL = frozenset(Role)
AUTHORITY: Mapping[Kind, frozenset[Role]] = {
    Kind.TASK_REQUEST: frozenset({Role.OWNER, Role.ORCHESTRATOR}),
    Kind.TASK_CLAIM: frozenset({Role.ORCHESTRATOR, Role.WORKER}),
    Kind.TASK_RESULT: frozenset({Role.ORCHESTRATOR, Role.WORKER}),
    Kind.TASK_FAILED: frozenset({Role.ORCHESTRATOR, Role.WORKER}),
    Kind.MEMORY_WRITE: _ALL,
    Kind.APPROVAL_REQUEST: frozenset({Role.ORCHESTRATOR, Role.WORKER, Role.WATCHER}),
    Kind.APPROVAL_DECISION: frozenset({Role.OWNER}),
    Kind.ESCALATE: _ALL,
    Kind.HEARTBEAT: _ALL,
}

REQUIRED_BODY: Mapping[Kind, tuple[str, ...]] = {
    Kind.TASK_REQUEST: ("title",),
    Kind.TASK_CLAIM: (),
    Kind.TASK_RESULT: ("summary",),
    Kind.TASK_FAILED: ("reason",),
    Kind.MEMORY_WRITE: ("key", "value"),
    Kind.APPROVAL_REQUEST: ("action",),
    Kind.APPROVAL_DECISION: ("approved",),
    Kind.ESCALATE: ("reason",),
    Kind.HEARTBEAT: (),
}

_NEEDS_TASK = frozenset(
    {
        Kind.TASK_REQUEST,
        Kind.TASK_CLAIM,
        Kind.TASK_RESULT,
        Kind.TASK_FAILED,
        Kind.APPROVAL_REQUEST,
        Kind.APPROVAL_DECISION,
    }
)


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


@dataclass(frozen=True)
class Envelope:
    seq: int
    sender: str
    kind: Kind
    task_id: str
    body: dict[str, Any] = field(compare=False)
    prev: str
    digest: str
    ts: float = field(default=0.0, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "v": PROTOCOL_VERSION,
            "seq": self.seq,
            "sender": self.sender,
            "kind": self.kind.value,
            "task_id": self.task_id,
            "body": self.body,
            "prev": self.prev,
            "digest": self.digest,
            "ts": self.ts,
        }


def envelope_digest(seq: int, sender: str, kind: Kind, task_id: str, body: Mapping[str, Any], prev: str) -> str:
    """SHA-256 over the canonical envelope, timestamp excluded so replays are deterministic."""
    payload = {"v": PROTOCOL_VERSION, "seq": seq, "sender": sender, "kind": kind.value, "task_id": task_id}
    payload.update({"body": dict(body), "prev": prev})
    return hashlib.sha256(_canonical(payload)).hexdigest()


class Ledger:
    """Append-only, hash-chained log of envelopes with authority and lifecycle checks."""

    def __init__(self, agents: Iterable[AgentCard] = DEFAULT_AGENTS, clock: Clock | None = None) -> None:
        self.agents: dict[str, AgentCard] = {a.name: a for a in agents}
        self.clock: Clock = clock or SystemClock()
        self.entries: list[Envelope] = []

    @property
    def head(self) -> str:
        return self.entries[-1].digest if self.entries else GENESIS

    # ── posting ────────────────────────────────────────────────────────────

    def prepare(self, sender: str, kind: Kind | str, task_id: str = "", **body: Any) -> Envelope:
        """Check and build the next envelope at the current head without appending it."""
        kind = Kind(kind)
        self._check(sender, kind, task_id, body)
        seq = len(self.entries)
        prev = self.head
        digest = envelope_digest(seq, sender, kind, task_id, body, prev)
        return Envelope(seq, sender, kind, task_id, dict(body), prev, digest, self.clock.now())

    def post(self, sender: str, kind: Kind | str, task_id: str = "", **body: Any) -> Envelope:
        env = self.prepare(sender, kind, task_id, **body)
        self.entries.append(env)
        return env

    def _check(self, sender: str, kind: Kind, task_id: str, body: Mapping[str, Any]) -> None:
        card = self.agents.get(sender)
        if card is None:
            raise ProtocolError(f"unknown sender {sender!r}")
        if card.role not in AUTHORITY[kind]:
            raise ProtocolError(f"{sender} ({card.role.value}) may not post {kind.value}")
        missing = [k for k in REQUIRED_BODY[kind] if k not in body]
        if missing:
            raise ProtocolError(f"{kind.value} missing body field(s): {', '.join(missing)}")
        if kind in _NEEDS_TASK and not task_id:
            raise ProtocolError(f"{kind.value} needs a task_id")

        status = self.status(task_id) if task_id else ""
        if kind is Kind.TASK_REQUEST and status:
            raise ProtocolError(f"task {task_id} already exists")
        if kind is not Kind.TASK_REQUEST and kind in _NEEDS_TASK and not status:
            raise ProtocolError(f"task {task_id} does not exist")
        if kind is Kind.TASK_CLAIM and status not in ("open", "approved"):
            raise ProtocolError(f"task {task_id} is {status}, not claimable")
        if kind in (Kind.TASK_RESULT, Kind.TASK_FAILED):
            if status != "claimed":
                raise ProtocolError(f"task {task_id} is {status}, not claimed")
            if self.claimant(task_id) != sender:
                raise ProtocolError(f"task {task_id} is claimed by {self.claimant(task_id)}, not {sender}")
        if kind is Kind.APPROVAL_REQUEST and status not in ("open", "claimed"):
            raise ProtocolError(f"task {task_id} is {status}; approval can only be requested while open or claimed")
        if kind is Kind.APPROVAL_DECISION:
            if status != "awaiting_approval":
                raise ProtocolError(f"task {task_id} has no pending approval")
            if not isinstance(body["approved"], bool):
                raise ProtocolError("approval.decision 'approved' must be a bool")

    # ── reading ────────────────────────────────────────────────────────────

    def _for(self, task_id: str) -> list[Envelope]:
        return [e for e in self.entries if e.task_id == task_id]

    def status(self, task_id: str) -> str:
        """'' (no such task), open, awaiting_approval, approved, rejected, claimed, done or failed."""
        state = ""
        for e in self._for(task_id):
            if e.kind is Kind.TASK_REQUEST:
                state = "awaiting_approval" if e.body.get("requires_approval") else "open"
            elif e.kind is Kind.APPROVAL_REQUEST and state in ("open", "claimed"):
                state = "awaiting_approval"
            elif e.kind is Kind.APPROVAL_DECISION:
                state = "approved" if e.body["approved"] else "rejected"
            elif e.kind is Kind.TASK_CLAIM:
                state = "claimed"
            elif e.kind is Kind.TASK_RESULT:
                state = "done"
            elif e.kind is Kind.TASK_FAILED:
                state = "failed"
        return state

    def claimant(self, task_id: str) -> str | None:
        claims = [e.sender for e in self._for(task_id) if e.kind is Kind.TASK_CLAIM]
        return claims[-1] if claims else None

    def tasks(self) -> list[str]:
        return [e.task_id for e in self.entries if e.kind is Kind.TASK_REQUEST]

    def claimable(self) -> list[str]:
        return [t for t in self.tasks() if self.status(t) in ("open", "approved")]

    def pending_approvals(self) -> list[str]:
        return [t for t in self.tasks() if self.status(t) == "awaiting_approval"]

    def memory(self, scope: str = "shared") -> dict[str, Any]:
        """Last-write-wins view of memory.write entries in one scope (ledger order, not wall clock)."""
        view: dict[str, Any] = {}
        for e in self.entries:
            if e.kind is Kind.MEMORY_WRITE and e.body.get("scope", "shared") == scope:
                view[str(e.body["key"])] = e.body["value"]
        return view

    # ── integrity and persistence ──────────────────────────────────────────

    def verify(self) -> bool:
        prev = GENESIS
        for i, e in enumerate(self.entries):
            if e.seq != i or e.prev != prev:
                return False
            if e.digest != envelope_digest(e.seq, e.sender, e.kind, e.task_id, e.body, e.prev):
                return False
            prev = e.digest
        return True

    def to_jsonl(self) -> str:
        return "".join(json.dumps(e.to_dict(), sort_keys=True, default=str) + "\n" for e in self.entries)

    def append_record(self, raw: Mapping[str, Any]) -> Envelope:
        """Append an entry written elsewhere, re-checking every rule and the hash chain. Raises ProtocolError."""
        if raw.get("v") != PROTOCOL_VERSION:
            raise ProtocolError(f"unsupported protocol version {raw.get('v')!r}")
        kind = Kind(raw["kind"])
        self._check(raw["sender"], kind, raw["task_id"], raw["body"])
        expect = envelope_digest(len(self.entries), raw["sender"], kind, raw["task_id"], raw["body"], self.head)
        if raw["seq"] != len(self.entries) or raw["prev"] != self.head or raw["digest"] != expect:
            raise ProtocolError(f"hash chain broken at seq {raw['seq']}")
        env = Envelope(
            raw["seq"], raw["sender"], kind, raw["task_id"], dict(raw["body"]), raw["prev"], expect, raw["ts"]
        )
        self.entries.append(env)
        return env

    @classmethod
    def from_records(cls, records: Iterable[Mapping[str, Any]], agents: Iterable[AgentCard] = DEFAULT_AGENTS) -> Ledger:
        """Replay a ledger, re-checking every rule and the hash chain. Raises ProtocolError on tampering."""
        ledger = cls(agents)
        for raw in records:
            ledger.append_record(raw)
        return ledger

    @classmethod
    def from_jsonl(cls, text: str, agents: Iterable[AgentCard] = DEFAULT_AGENTS) -> Ledger:
        return cls.from_records((json.loads(line) for line in text.splitlines() if line.strip()), agents)
