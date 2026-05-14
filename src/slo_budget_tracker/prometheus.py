"""
Prometheus exporter — turn an SLORegistry into gauges scrapable at /metrics.

Emitted series (one of each per SLO, labelled by slo name):

    slo_target{slo="..."}                    target ratio (e.g. 0.999)
    slo_window_seconds{slo="..."}            window the SLO is evaluated over
    slo_success_ratio{slo="..."}             actual ratio over the window
    slo_failures_total{slo="..."}            failure count in the window
    slo_total{slo="..."}                     observation count in the window
    slo_error_budget_remaining{slo="..."}    fraction of budget left (1.0 = untouched)
    slo_burn_rate{slo="...", window="..."}   burn rate for window-window seconds
    slo_breached{slo="..."}                  1 if success_ratio < target else 0

We use a fresh CollectorRegistry (not the global one) so the exporter is safe
to instantiate multiple times in tests and inside the same process.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Gauge, generate_latest

from .registry import SLORegistry


class PrometheusExporter:
    """Render the current state of an SLORegistry as Prometheus text format."""

    __slots__ = (
        "_breached",
        "_burn_rate",
        "_error_budget_remaining",
        "_failures_total",
        "_registry",
        "_slo_registry",
        "_success_ratio",
        "_target",
        "_total",
        "_window_seconds",
    )

    def __init__(self, slo_registry: SLORegistry) -> None:
        self._slo_registry = slo_registry
        self._registry = CollectorRegistry(auto_describe=True)

        labels = ("slo",)
        self._target = Gauge("slo_target", "SLO target ratio.", labels, registry=self._registry)
        self._window_seconds = Gauge(
            "slo_window_seconds", "SLO rolling window in seconds.", labels, registry=self._registry
        )
        self._success_ratio = Gauge(
            "slo_success_ratio", "Actual success ratio over the window.", labels, registry=self._registry
        )
        self._failures_total = Gauge(
            "slo_failures_total", "Failure count over the window.", labels, registry=self._registry
        )
        self._total = Gauge(
            "slo_total", "Observation count over the window.", labels, registry=self._registry
        )
        self._error_budget_remaining = Gauge(
            "slo_error_budget_remaining",
            "Fraction of error budget remaining. 1.0 = untouched, <=0 = exhausted.",
            labels,
            registry=self._registry,
        )
        self._burn_rate = Gauge(
            "slo_burn_rate",
            "Burn rate over a short window. >1 means burning faster than budget allows.",
            ("slo", "window_seconds"),
            registry=self._registry,
        )
        self._breached = Gauge(
            "slo_breached",
            "1 if the SLO target is currently violated, else 0.",
            labels,
            registry=self._registry,
        )

    def _refresh(self) -> None:
        for snap in self._slo_registry.snapshot_all():
            self._target.labels(snap.name).set(snap.target)
            self._window_seconds.labels(snap.name).set(snap.window_seconds)
            self._success_ratio.labels(snap.name).set(snap.success_ratio)
            self._failures_total.labels(snap.name).set(snap.failures)
            self._total.labels(snap.name).set(snap.total)
            self._error_budget_remaining.labels(snap.name).set(snap.error_budget_remaining)
            self._breached.labels(snap.name).set(1 if snap.is_breached else 0)
            for sample in snap.burn_rate_samples:
                self._burn_rate.labels(snap.name, str(sample.window_seconds)).set(sample.burn_rate)

    def render(self) -> tuple[bytes, str]:
        """Return (body, content_type) — drop straight into a FastAPI Response."""
        self._refresh()
        return generate_latest(self._registry), CONTENT_TYPE_LATEST

    @property
    def collector_registry(self) -> CollectorRegistry:
        """Expose the underlying prometheus_client registry for advanced use."""
        return self._registry
