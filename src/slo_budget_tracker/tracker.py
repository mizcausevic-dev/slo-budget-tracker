"""
Core SLO tracker.

One SLOTracker holds one SLODefinition + an ObservationStore. Hot methods:

    record_success() / record_failure() / record(success: bool)
        O(1) average; trims old observations as it goes.

    snapshot() -> SLOSnapshot
        O(n) over the rolling window. Computes success ratio, error budget
        remaining (as a fraction of allowed errors), burn rate at each
        configured short window.

    check_burn_rate() -> list[BurnRateAlert]
        Returns alerts for any short window whose burn rate >= threshold.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from . import audit_stream
from .models import (
    BurnRateAlert,
    BurnRateSample,
    Observation,
    SLODefinition,
    SLOSnapshot,
)
from .storage import InMemoryStore, ObservationStore

if TYPE_CHECKING:
    import httpx


class SLOTracker:
    """An SLO + its observation history. Thread-safe via the underlying store."""

    __slots__ = ("_clock", "_definition", "_previous_alerted_windows", "_store")

    def __init__(
        self,
        definition: SLODefinition,
        *,
        store: ObservationStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._definition = definition
        self._store: ObservationStore = store or InMemoryStore(max_age_seconds=definition.window_seconds)
        self._clock = clock
        # Tracks which short windows were alerting on the previous
        # `check_burn_rate_with_audit` call. Used to detect started vs
        # recovered transitions so we only fire one event per state change.
        self._previous_alerted_windows: set[int] = set()

    @property
    def definition(self) -> SLODefinition:
        return self._definition

    @property
    def name(self) -> str:
        return self._definition.name

    # ---- record ---------------------------------------------------------

    def record(self, success: bool, *, at: float | None = None) -> None:
        ts = at if at is not None else self._clock()
        self._store.record(Observation(timestamp=ts, success=success))

    def record_success(self, *, at: float | None = None) -> None:
        self.record(True, at=at)

    def record_failure(self, *, at: float | None = None) -> None:
        self.record(False, at=at)

    # ---- read -----------------------------------------------------------

    def snapshot(self, *, at: float | None = None) -> SLOSnapshot:
        now = at if at is not None else self._clock()
        observations = self._store.window(now, self._definition.window_seconds)
        total = len(observations)
        failures = sum(1 for o in observations if not o.success)
        success_ratio = (total - failures) / total if total else 1.0

        # Error budget remaining: ratio of *unused* error budget.
        # allowed_errors = (1 - target) * total
        # used           = failures
        # remaining_pct  = (allowed_errors - used) / allowed_errors
        if total == 0:
            error_budget_remaining = 1.0
        else:
            allowed = self._definition.error_budget_ratio * total
            if allowed == 0:
                # target == 1.0 would have been rejected; safety branch.
                error_budget_remaining = 1.0 if failures == 0 else float("-inf")
            else:
                error_budget_remaining = (allowed - failures) / allowed

        burn_rate = self._burn_rate(success_ratio)
        burn_rate_samples = tuple(self._sample_burn(now, w) for w in self._definition.burn_rate_windows)

        return SLOSnapshot(
            name=self._definition.name,
            target=self._definition.target,
            window_seconds=self._definition.window_seconds,
            total=total,
            failures=failures,
            success_ratio=success_ratio,
            error_budget_remaining=error_budget_remaining,
            burn_rate=burn_rate,
            burn_rate_samples=burn_rate_samples,
        )

    def check_burn_rate(self, *, at: float | None = None) -> list[BurnRateAlert]:
        """Returns alerts for short windows whose burn rate is >= threshold."""
        now = at if at is not None else self._clock()
        alerts: list[BurnRateAlert] = []
        for window in self._definition.burn_rate_windows:
            sample = self._sample_burn(now, window)
            if sample.sample_count == 0:
                continue
            if sample.burn_rate >= self._definition.burn_rate_threshold:
                alerts.append(
                    BurnRateAlert(
                        slo_name=self._definition.name,
                        window_seconds=window,
                        burn_rate=sample.burn_rate,
                        threshold=self._definition.burn_rate_threshold,
                        success_ratio=sample.success_ratio,
                        sample_count=sample.sample_count,
                    )
                )
        return alerts

    async def check_burn_rate_with_audit(
        self,
        http_client: httpx.AsyncClient,
        *,
        at: float | None = None,
    ) -> list[BurnRateAlert]:
        """Check burn rates **and** emit transition events to the audit-stream spine.

        This is a thin wrapper over :meth:`check_burn_rate` that tracks
        which windows were alerting on the previous call and emits one
        event per transition:

        - ``slo_burn_started`` — window newly crossed the threshold
        - ``slo_recovered``    — window cleared since the previous call

        Stateless polling (calling :meth:`check_burn_rate` directly)
        ignores transitions and just returns the current alerts. Use
        this method when you want governance events to land in
        ``audit-stream-py`` automatically.

        The emit is best-effort: a failed POST is logged and swallowed,
        never raised. The tracker's transition bookkeeping advances
        regardless of emit outcome so consumers don't get stuck
        re-firing the same event.
        """
        alerts = self.check_burn_rate(at=at)
        current_alerted = {a.window_seconds for a in alerts}
        newly_started = current_alerted - self._previous_alerted_windows
        recovered = self._previous_alerted_windows - current_alerted

        # Advance state BEFORE emit so a slow audit-stream doesn't make
        # us re-fire the same transition on the next call.
        self._previous_alerted_windows = current_alerted

        for alert in alerts:
            if alert.window_seconds in newly_started:
                await audit_stream.emit(
                    http_client,
                    kind="slo_burn_started",
                    payload={
                        "slo_name": alert.slo_name,
                        "window_seconds": alert.window_seconds,
                        "burn_rate": alert.burn_rate,
                        "threshold": alert.threshold,
                        "success_ratio": alert.success_ratio,
                        "sample_count": alert.sample_count,
                    },
                )

        for window in recovered:
            await audit_stream.emit(
                http_client,
                kind="slo_recovered",
                payload={
                    "slo_name": self._definition.name,
                    "window_seconds": window,
                    "threshold": self._definition.burn_rate_threshold,
                },
            )

        return alerts

    # ---- internals ------------------------------------------------------

    def _burn_rate(self, success_ratio: float) -> float:
        """
        Burn rate = (1 - actual_success_ratio) / (1 - target).

        burn_rate == 1.0 means errors are arriving at exactly the rate the SLO
        allows. > 1 means we'll exhaust the budget sooner than the window.
        """
        budget = self._definition.error_budget_ratio
        if budget == 0:
            return 0.0
        return (1.0 - success_ratio) / budget

    def _sample_burn(self, now: float, window_seconds: int) -> BurnRateSample:
        observations = self._store.window(now, window_seconds)
        total = len(observations)
        if total == 0:
            return BurnRateSample(
                window_seconds=window_seconds, burn_rate=0.0, success_ratio=1.0, sample_count=0
            )
        failures = sum(1 for o in observations if not o.success)
        success_ratio = (total - failures) / total
        return BurnRateSample(
            window_seconds=window_seconds,
            burn_rate=self._burn_rate(success_ratio),
            success_ratio=success_ratio,
            sample_count=total,
        )
