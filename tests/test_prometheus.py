"""Unit tests for the Prometheus exporter."""

from __future__ import annotations

from slo_budget_tracker import (
    PrometheusExporter,
    SLODefinition,
    SLORegistry,
    SLOTracker,
)


def _fixed_clock_tracker(definition: SLODefinition, now: float) -> SLOTracker:
    """Build a tracker whose snapshot() will use `now` as the current time."""
    return SLOTracker(definition, clock=lambda: now)


class TestPrometheusExporter:
    def test_renders_text_format(self) -> None:
        r = SLORegistry()
        t = _fixed_clock_tracker(
            SLODefinition(name="availability", target=0.999, burn_rate_windows=(60,)), now=100.0
        )
        r.add(t)
        for i in range(10):
            t.record_success(at=float(i))

        exporter = PrometheusExporter(r)
        body, content_type = exporter.render()
        text = body.decode("utf-8")
        assert "text/plain" in content_type

        # All metric families should be present, each labelled by the slo name.
        for metric in (
            "slo_target",
            "slo_window_seconds",
            "slo_success_ratio",
            "slo_failures_total",
            "slo_total",
            "slo_error_budget_remaining",
            "slo_burn_rate",
            "slo_breached",
        ):
            assert metric in text
        assert 'slo="availability"' in text

    def test_breached_flag_flips_when_below_target(self) -> None:
        r = SLORegistry()
        t = _fixed_clock_tracker(
            SLODefinition(name="strict", target=0.99, burn_rate_windows=(60,)), now=100.0
        )
        r.add(t)
        for i in range(50):
            t.record_success(at=float(i))
        for i in range(50, 100):
            t.record_failure(at=float(i))

        exporter = PrometheusExporter(r)
        text = exporter.render()[0].decode("utf-8")
        # success_ratio = 0.5 -> well below 0.99, breached = 1
        assert 'slo_breached{slo="strict"} 1.0' in text

    def test_multiple_slos_all_exposed(self) -> None:
        r = SLORegistry()
        r.define(SLODefinition(name="a", target=0.99))
        r.define(SLODefinition(name="b", target=0.999))
        text = PrometheusExporter(r).render()[0].decode("utf-8")
        assert 'slo="a"' in text
        assert 'slo="b"' in text

    def test_burn_rate_labelled_by_window_seconds(self) -> None:
        r = SLORegistry()
        r.define(SLODefinition(name="a", target=0.99, burn_rate_windows=(60, 300)))
        text = PrometheusExporter(r).render()[0].decode("utf-8")
        assert 'window_seconds="60"' in text
        assert 'window_seconds="300"' in text
