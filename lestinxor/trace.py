"""Append-only event log. Content-deterministic; timestamps come from an injected clock."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterable
from typing import Any

from .clock import Clock, SystemClock


class Trace:
    def __init__(self, clock: Clock | None = None, path: str | None = None) -> None:
        self.clock: Clock = clock or SystemClock()
        self.path = path
        self.events: list[dict[str, Any]] = []
        self._seq = 0
        self._lock = threading.Lock()

    def emit(self, kind: str, **data: Any) -> dict[str, Any]:
        with self._lock:
            event = {"seq": self._seq, "ts": self.clock.now(), "kind": kind, **data}
            self._seq += 1
            self.events.append(event)
            if self.path:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event, sort_keys=True, default=str) + "\n")
            return event

    def digest(self, exclude: Iterable[str] = ("ts",)) -> str:
        """SHA-256 of the canonical event stream. Equal digests mean identical runs (timestamps excluded)."""
        skip = set(exclude)
        events = [{k: v for k, v in e.items() if k not in skip} for e in self.events]
        blob = json.dumps(events, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(blob).hexdigest()
