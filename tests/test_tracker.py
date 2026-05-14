"""Unit tests for SLOTracker."""

from __future__ import annotations

import pytest

from slo_budget_tracker.models import SLODefinition
from slo_budget_tracker.tracker import SLOTracker


def _defn(**overrides: float | int | str | tuple[int, ...]) -> SLODefinition:
    kwargs: dict[str, float | int | str | tuple[int, ...]] = {
        "name": "availability",
        "target": 0.99,
        "window_seconds": 3600,
        "burn_rate_windows": (60, 300),
        "burn_rate_threshold": 14.4,
    }
    kwargs.update(overrides)
    return SLODefinition(**kwargs)  # type: ignore[arg-type]


class TestRecord:
    def test_record_success_and_failure(self) -> None:
        t = SLOTracker(_defn(), clock=lambda: 1000.0)
        t.record_success()
        t.record_failure()
        snap = t.snapshot()
        assert snap.total == 2
        assert snap.failures == 1
        assert snap.success_ratio == 0.5

    def test_record_with_explicit_timestamp(self) -> None:
        t = SLOTracker(_defn(), clock=lambda: 100.0)
        t.record_success(at=10.0)
        t.record_success(at=20.0)
        snap = t.snapshot(at=100.0)
        assert snap.total == 2


class TestSnapshot:
    def test_empty_snapshot(self) -> None:
        t = SLOTracker(_defn(), clock=lambda: 0.0)
        snap = t.snapshot()
        assert snap.total == 0
        assert snap.success_ratio == 1.0
        assert snap.error_budget_remaining == 1.0
        assert snap.burn_rate == 0.0
        assert snap.is_breached is False
        assert snap.is_budget_exhausted is False

    def test_all_success(self) -> None:
        t = SLOTracker(_defn())
        for i in range(100):
            t.record_success(at=float(i))
        snap = t.snapshot(at=200.0)
        assert snap.success_ratio == 1.0
        assert snap.error_budget_remaining == 1.0
        assert snap.is_breached is False

    def test_budget_partially_used(self) -> None:
        # target 0.99, 100 events, 1 failure -> ratio = 0.99 exactly
        # allowed errors = 0.01 * 100 = 1; used = 1; remaining = 0
        t = SLOTracker(_defn(target=0.99))
        for i in range(99):
            t.record_success(at=float(i))
        t.record_failure(at=99.0)
        snap = t.snapshot(at=100.0)
        assert snap.success_ratio == pytest.approx(0.99)
        assert snap.error_budget_remaining == pytest.approx(0.0)
        assert snap.is_breached is False  # ratio == target is OK
        assert snap.is_budget_exhausted is True  # but no headroom

    def test_budget_overspent_is_negative_and_breached(self) -> None:
        # target 0.99, 100 events, 5 failures -> ratio = 0.95
        # allowed = 1, used = 5, remaining = (1-5)/1 = -4
        t = SLOTracker(_defn(target=0.99))
        for i in range(95):
            t.record_success(at=float(i))
        for i in range(95, 100):
            t.record_failure(at=float(i))
        snap = t.snapshot(at=100.0)
        assert snap.success_ratio == pytest.approx(0.95)
        assert snap.error_budget_remaining == pytest.approx(-4.0)
        assert snap.is_breached is True
        assert snap.is_budget_exhausted is True

    def test_observations_outside_window_excluded(self) -> None:
        t = SLOTracker(_defn(window_seconds=100, burn_rate_windows=(10,)))
        t.record_failure(at=10.0)  # outside window when we snapshot at t=200
        t.record_success(at=150.0)
        snap = t.snapshot(at=200.0)
        assert snap.total == 1
        assert snap.failures == 0


class TestBurnRate:
    def test_burn_rate_at_steady_state(self) -> None:
        # target 0.99, error_budget = 0.01.
        # If actual ratio = 0.99 the burn rate is exactly 1.0.
        t = SLOTracker(_defn(target=0.99))
        for i in range(99):
            t.record_success(at=float(i))
        t.record_failure(at=99.0)
        snap = t.snapshot(at=100.0)
        assert snap.burn_rate == pytest.approx(1.0)

    def test_burn_rate_above_threshold_triggers_alert(self) -> None:
        # target 0.99, window 3600s, burn windows (60, 300)
        # Pile 50 failures into the last 60s -> ratio = 0, burn_rate = 100
        # which is well above the default threshold of 14.4.
        t = SLOTracker(_defn(target=0.99, burn_rate_threshold=14.4))
        for i in range(50):
            t.record_failure(at=950.0 + i)  # all inside the 60s window if we snap at 1000.0
        alerts = t.check_burn_rate(at=1000.0)
        # Both burn windows (60 and 300) contain only failures.
        assert len(alerts) == 2
        assert {a.window_seconds for a in alerts} == {60, 300}
        for a in alerts:
            assert a.burn_rate > 14.4

    def test_burn_rate_below_threshold_no_alert(self) -> None:
        t = SLOTracker(_defn(target=0.99))
        for i in range(1000):
            t.record_success(at=float(i))
        assert t.check_burn_rate(at=1001.0) == []

    def test_burn_rate_samples_populated(self) -> None:
        t = SLOTracker(_defn(target=0.99, burn_rate_windows=(60, 300)))
        for i in range(100):
            t.record_success(at=float(i))
        snap = t.snapshot(at=100.0)
        assert {s.window_seconds for s in snap.burn_rate_samples} == {60, 300}
        for s in snap.burn_rate_samples:
            assert s.burn_rate == 0.0
