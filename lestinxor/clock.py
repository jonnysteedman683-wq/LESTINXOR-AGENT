"""Injectable time source. Nothing else in Lestinxor reads the wall clock."""
from __future__ import annotations

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.time()


class ManualClock:
    """Deterministic clock for tests and DST: returns start, start+step, ..."""

    def __init__(self, start: float = 0.0, step: float = 1.0) -> None:
        self._t = start
        self._step = step

    def now(self) -> float:
        t = self._t
        self._t += self._step
        return t
