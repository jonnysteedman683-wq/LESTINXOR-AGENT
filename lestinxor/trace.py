"""Append-only event log. Deterministic given a deterministic clock."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .clock import Clock, SystemClock


class Trace:
    def __init__(self, clock: Clock | None = None, path: str | None = None) -> None:
        self.clock = clock or SystemClock()
        self.path = path
        self.events: list[dict[str, Any]] = []
        self._seq = 0

    def emit(self, kind: str, **data: Any) -> dict[str, Any]:
        event = {"seq": self._seq, "ts": self.clock.now(), "kind": kind, **data}
        self._seq += 1
        self.events.append(event)
        if self.path:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    def digest(self) -> str:
        """SHA-256 of the canonical event stream — equal digests mean identical runs."""
        blob = json.dumps(self.events, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()
