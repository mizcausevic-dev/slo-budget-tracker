"""
Observation storage backends.

The default backend is an in-memory rolling deque keyed by wall-clock timestamp.
The Protocol is exposed so callers can plug in Redis / SQL / whatever without
patching the tracker itself.

We trim opportunistically on every write rather than running a background sweeper
— for typical request volumes the deque stays small (a 30-day window at 100 rps =
~260 M entries, which is too much to keep in process; users at that scale should
switch to a sampling backend or aggregate into time buckets).
"""

from __future__ import annotations

from collections import deque
from threading import Lock
from typing import Protocol

from .models import Observation


class ObservationStore(Protocol):
    """Pluggable backend for storing observations within a rolling window."""

    def record(self, observation: Observation) -> None: ...

    def window(self, now: float, seconds: int) -> list[Observation]:
        """Return observations whose timestamp is within `seconds` of `now`."""
        ...

    def trim(self, before: float) -> None:
        """Drop observations older than `before` (seconds since epoch)."""
        ...

    def __len__(self) -> int: ...


class InMemoryStore:
    """Thread-safe in-memory deque. O(1) record, O(n) trim and window scan."""

    __slots__ = ("_data", "_lock", "_max_age_seconds")

    def __init__(self, max_age_seconds: int) -> None:
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self._max_age_seconds = max_age_seconds
        self._data: deque[Observation] = deque()
        self._lock = Lock()

    def record(self, observation: Observation) -> None:
        with self._lock:
            self._data.append(observation)
            cutoff = observation.timestamp - self._max_age_seconds
            while self._data and self._data[0].timestamp < cutoff:
                self._data.popleft()

    def window(self, now: float, seconds: int) -> list[Observation]:
        cutoff = now - seconds
        with self._lock:
            # deque is append-ordered by timestamp because record() only appends
            # the latest event. Scan from the right for the cheap case where
            # `seconds` is short (typical for burn-rate windows).
            return [obs for obs in self._data if obs.timestamp >= cutoff]

    def trim(self, before: float) -> None:
        with self._lock:
            while self._data and self._data[0].timestamp < before:
                self._data.popleft()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
