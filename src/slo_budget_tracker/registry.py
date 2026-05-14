"""
Multi-SLO registry.

Most services have more than one SLO (availability, latency, freshness, …).
The registry holds a named collection of SLOTrackers and provides batch
operations: snapshot_all() for /slo endpoints, check_burn_rates() for the
alerter, items() for the Prometheus exporter.
"""

from __future__ import annotations

from collections.abc import Iterator
from threading import Lock

from .models import BurnRateAlert, SLODefinition, SLOSnapshot
from .tracker import SLOTracker


class SLORegistry:
    """Named collection of SLOTrackers. Safe to share across threads."""

    __slots__ = ("_lock", "_trackers")

    def __init__(self) -> None:
        self._trackers: dict[str, SLOTracker] = {}
        self._lock = Lock()

    def define(self, definition: SLODefinition) -> SLOTracker:
        """Register a new SLO. Raises ValueError if the name is already in use."""
        tracker = SLOTracker(definition)
        with self._lock:
            if definition.name in self._trackers:
                raise ValueError(f"SLO {definition.name!r} is already registered")
            self._trackers[definition.name] = tracker
        return tracker

    def add(self, tracker: SLOTracker) -> None:
        """Register an existing tracker (useful when the caller wants a custom store)."""
        with self._lock:
            if tracker.name in self._trackers:
                raise ValueError(f"SLO {tracker.name!r} is already registered")
            self._trackers[tracker.name] = tracker

    def get(self, name: str) -> SLOTracker:
        with self._lock:
            try:
                return self._trackers[name]
            except KeyError as err:
                raise KeyError(f"No SLO registered under {name!r}") from err

    def __contains__(self, name: object) -> bool:
        with self._lock:
            return name in self._trackers

    def __len__(self) -> int:
        with self._lock:
            return len(self._trackers)

    def __iter__(self) -> Iterator[SLOTracker]:
        with self._lock:
            return iter(list(self._trackers.values()))

    def names(self) -> list[str]:
        with self._lock:
            return list(self._trackers.keys())

    def snapshot_all(self, *, at: float | None = None) -> list[SLOSnapshot]:
        return [t.snapshot(at=at) for t in self]

    def check_burn_rates(self, *, at: float | None = None) -> list[BurnRateAlert]:
        alerts: list[BurnRateAlert] = []
        for tracker in self:
            alerts.extend(tracker.check_burn_rate(at=at))
        return alerts
