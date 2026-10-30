"""SupabaseLedger: the UniSync ledger backed by the shared ``unisync_events`` table (``unisync/schema.sql``).

Stdlib only (``urllib``, PostgREST). Every row read is replayed through ``Ledger.append_record``, so a
tampered or rule-breaking row stops the sync with ``ProtocolError`` instead of being trusted. Writes
use optimistic concurrency: an envelope is built at the local head and inserted; if another agent got
there first, the unique ``seq``/``prev`` constraint returns 409, the ledger re-syncs, the post is
re-checked against the new state and retried.

Credentials come from the agent's own environment, never from a repo:
``UNISYNC_SUPABASE_URL`` (``https://<ref>.supabase.co``) and ``UNISYNC_SUPABASE_KEY`` (service-role key).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from typing import Any

from .clock import Clock
from .unisync import DEFAULT_AGENTS, AgentCard, Envelope, Kind, Ledger, ProtocolError

TABLE = "unisync_events"
PAGE = 1000


class SupabaseLedger:
    def __init__(
        self,
        url: str | None = None,
        key: str | None = None,
        agents: Iterable[AgentCard] = DEFAULT_AGENTS,
        clock: Clock | None = None,
        timeout: float = 30.0,
        retries: int = 5,
    ) -> None:
        url = url or os.environ.get("UNISYNC_SUPABASE_URL", "")
        key = key or os.environ.get("UNISYNC_SUPABASE_KEY", "")
        if not url or not key:
            raise ValueError("set UNISYNC_SUPABASE_URL and UNISYNC_SUPABASE_KEY (or pass url= and key=)")
        self.endpoint = url.rstrip("/") + "/rest/v1/" + TABLE
        self.headers = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        self.ledger = Ledger(agents, clock)
        self.timeout = timeout
        self.retries = retries

    def _request(self, method: str, url: str, payload: Any = None, extra: dict[str, str] | None = None) -> Any:
        data = None if payload is None else json.dumps(payload, default=str).encode()
        req = urllib.request.Request(url, data=data, method=method, headers={**self.headers, **(extra or {})})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read()
        return json.loads(raw) if raw else None

    def sync(self) -> int:
        """Pull and verify every row past the local head. Returns how many were appended."""
        added = 0
        while True:
            query = urllib.parse.urlencode(
                {
                    "select": "v,seq,sender,kind,task_id,body,prev,digest,ts",
                    "seq": f"gte.{len(self.ledger.entries)}",
                    "order": "seq.asc",
                    "limit": str(PAGE),
                }
            )
            rows = self._request("GET", f"{self.endpoint}?{query}") or []
            for row in rows:
                self.ledger.append_record(row)
            added += len(rows)
            if len(rows) < PAGE:
                return added

    def post(self, sender: str, kind: Kind | str, task_id: str = "", **body: Any) -> Envelope:
        """Insert one envelope at the shared head, re-syncing and re-checking on a concurrent write."""
        self.sync()
        for _ in range(self.retries):
            env = self.ledger.prepare(sender, kind, task_id, **body)
            try:
                self._request("POST", self.endpoint, env.to_dict(), {"Prefer": "return=minimal"})
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                self.sync()  # someone else wrote first; the post may now be invalid, prepare() re-checks
                continue
            self.ledger.entries.append(env)
            return env
        raise ProtocolError(f"gave up after {self.retries} concurrent-write conflicts")

    # Read-through helpers so callers can treat this like a Ledger.
    def status(self, task_id: str) -> str:
        return self.ledger.status(task_id)

    def claimable(self) -> list[str]:
        return self.ledger.claimable()

    def pending_approvals(self) -> list[str]:
        return self.ledger.pending_approvals()

    def memory(self, scope: str = "shared") -> dict[str, Any]:
        return self.ledger.memory(scope)
