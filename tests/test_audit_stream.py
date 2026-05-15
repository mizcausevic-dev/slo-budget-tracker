"""Tests for the audit-stream-py integration."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from slo_budget_tracker import SLODefinition, SLOTracker, audit_stream


class TestConfig:
    def test_disabled_when_env_var_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUDIT_STREAM_URL", raising=False)
        assert audit_stream.is_enabled() is False
        assert audit_stream.base_url() is None

    def test_enabled_when_env_var_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", "http://localhost:8093")
        assert audit_stream.is_enabled() is True
        assert audit_stream.base_url() == "http://localhost:8093"

    def test_trailing_slash_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", "http://localhost:8093/")
        assert audit_stream.base_url() == "http://localhost:8093"

    def test_timeout_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUDIT_STREAM_TIMEOUT_S", raising=False)
        assert audit_stream.timeout_s() == audit_stream.DEFAULT_TIMEOUT_S

    def test_timeout_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_TIMEOUT_S", "5.0")
        assert audit_stream.timeout_s() == 5.0


class TestEmitHelper:
    @pytest.mark.asyncio
    async def test_emit_noop_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUDIT_STREAM_URL", raising=False)
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(201)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await audit_stream.emit(client, kind="slo_burn_started", payload={"x": 1})
        assert captured == []

    @pytest.mark.asyncio
    async def test_emit_posts_to_events_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", "http://audit.local/")
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "http://audit.local/events"
            captured.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(201)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await audit_stream.emit(client, kind="slo_burn_started", payload={"slo_name": "api"})
        assert captured[0]["kind"] == "slo_burn_started"
        assert captured[0]["source"] == "slo-budget-tracker"
        assert captured[0]["payload"]["slo_name"] == "api"


# ---------------------------------------------------------------------------
# Real producer-method tests against `SLOTracker.check_burn_rate_with_audit`
# ---------------------------------------------------------------------------


def _hot_tracker() -> SLOTracker:
    """Build a tracker pre-loaded with enough failures to trip burn-rate."""
    definition = SLODefinition(
        name="api-availability",
        target=0.999,
        window_seconds=3600,
        burn_rate_windows=(60,),
        burn_rate_threshold=2.0,  # easy to trip in tests
    )
    return SLOTracker(definition)


class TestCheckBurnRateWithAudit:
    @pytest.mark.asyncio
    async def test_emit_slo_burn_started_on_threshold_cross(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", "http://audit.local")
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(201)

        tracker = _hot_tracker()
        # Slam failures so burn rate >> threshold
        now = 1_000_000.0
        for _ in range(20):
            tracker.record_failure(at=now)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            alerts = await tracker.check_burn_rate_with_audit(client, at=now)

        assert len(alerts) == 1
        assert any(e["kind"] == "slo_burn_started" for e in captured)
        evt = next(e for e in captured if e["kind"] == "slo_burn_started")
        assert evt["source"] == "slo-budget-tracker"
        assert evt["payload"]["slo_name"] == "api-availability"
        assert evt["payload"]["window_seconds"] == 60

    @pytest.mark.asyncio
    async def test_already_alerting_does_not_re_emit_started(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", "http://audit.local")
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(201)

        tracker = _hot_tracker()
        now = 1_000_000.0
        for _ in range(20):
            tracker.record_failure(at=now)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await tracker.check_burn_rate_with_audit(client, at=now)
            await tracker.check_burn_rate_with_audit(client, at=now)

        # Only ONE slo_burn_started across two polls — second call sees the
        # window was already alerting and stays quiet.
        started = [e for e in captured if e["kind"] == "slo_burn_started"]
        assert len(started) == 1

    @pytest.mark.asyncio
    async def test_emit_slo_recovered_on_clearing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUDIT_STREAM_URL", "http://audit.local")
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(201)

        tracker = _hot_tracker()
        # T0: hot — record only failures, trip the alert
        t0 = 1_000_000.0
        for _ in range(20):
            tracker.record_failure(at=t0)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            # First poll: alert starts firing
            await tracker.check_burn_rate_with_audit(client, at=t0)
            # T1: well after the burn window — old failures fall out + tons of
            # successes land. Window now passes.
            t1 = t0 + 3600
            for _ in range(1000):
                tracker.record_success(at=t1)
            alerts = await tracker.check_burn_rate_with_audit(client, at=t1)

        assert alerts == []
        assert any(e["kind"] == "slo_recovered" for e in captured)
        recovered = next(e for e in captured if e["kind"] == "slo_recovered")
        assert recovered["payload"]["slo_name"] == "api-availability"
        assert recovered["payload"]["window_seconds"] == 60

    @pytest.mark.asyncio
    async def test_silent_when_audit_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AUDIT_STREAM_URL", raising=False)
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(201)

        tracker = _hot_tracker()
        now = 1_000_000.0
        for _ in range(20):
            tracker.record_failure(at=now)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            alerts = await tracker.check_burn_rate_with_audit(client, at=now)
        # Tracker still works; emit is a no-op.
        assert len(alerts) == 1
        assert captured == []
