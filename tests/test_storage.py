"""Unit tests for the in-memory observation store."""

from __future__ import annotations

import pytest

from slo_budget_tracker.models import Observation
from slo_budget_tracker.storage import InMemoryStore


class TestInMemoryStore:
    def test_records_and_returns_observations(self) -> None:
        s = InMemoryStore(max_age_seconds=1000)
        s.record(Observation(timestamp=100.0, success=True))
        s.record(Observation(timestamp=200.0, success=False))
        obs = s.window(now=300.0, seconds=1000)
        assert len(obs) == 2
        assert obs[0].timestamp == 100.0
        assert obs[1].success is False

    def test_record_trims_old_observations(self) -> None:
        s = InMemoryStore(max_age_seconds=100)
        s.record(Observation(timestamp=0.0, success=True))
        s.record(Observation(timestamp=50.0, success=True))
        s.record(Observation(timestamp=200.0, success=True))  # trims first two
        assert len(s) == 1

    def test_window_filters_by_cutoff(self) -> None:
        s = InMemoryStore(max_age_seconds=10_000)
        for t in (100.0, 200.0, 300.0, 400.0):
            s.record(Observation(timestamp=t, success=True))
        recent = s.window(now=400.0, seconds=150)  # cutoff = 250.0
        assert [o.timestamp for o in recent] == [300.0, 400.0]

    def test_trim(self) -> None:
        s = InMemoryStore(max_age_seconds=10_000)
        for t in (10.0, 20.0, 30.0):
            s.record(Observation(timestamp=t, success=True))
        s.trim(before=25.0)
        assert len(s) == 1

    def test_max_age_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            InMemoryStore(max_age_seconds=0)

    def test_empty_window(self) -> None:
        s = InMemoryStore(max_age_seconds=100)
        assert s.window(now=0.0, seconds=10) == []
        assert len(s) == 0
