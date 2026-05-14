# slo-budget-tracker

[![CI](https://github.com/mizcausevic-dev/slo-budget-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/mizcausevic-dev/slo-budget-tracker/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**SLO + error-budget tracker for Python services** — drop-in FastAPI middleware, Prometheus exporter, and a small standalone library you can wire into any ASGI app or background worker.

Built around the math in the [Google SRE Workbook](https://sre.google/workbook/alerting-on-slos/): one rolling window, multi-window burn-rate alerts (defaults to 1h + 6h at burn rate ≥ 14.4), and an explicit error-budget remaining gauge so dashboards stop lying about reliability.

---

## Why

Most "SLO dashboards" you find in the wild conflate _availability_ with _uptime_ and surface neither error budget nor burn rate. You can't tell, at a glance, whether the freshly deployed service is **burning the next 30 days of error budget in the next 30 minutes**. This library makes that visible by default.

Two things matter:

1. **Error budget remaining** — a `[1.0 → ≤0]` ratio on every dashboard.
2. **Burn rate** — `(1 − actual_success_ratio) / (1 − target)`, sampled at short windows so fast-burn incidents page before the budget is spent.

---

## Install

```bash
pip install slo-budget-tracker
# or, with the FastAPI extras:
pip install "slo-budget-tracker[fastapi]"
```

Python 3.11+. Single runtime dep: `prometheus-client`.

---

## Quick start — standalone library

```python
from slo_budget_tracker import SLODefinition, SLOTracker

tracker = SLOTracker(
    SLODefinition(
        name="availability",
        target=0.999,                # three nines
        window_seconds=30 * 24 * 3600,  # 30-day rolling window
        burn_rate_windows=(3600, 21600),  # alert on 1h and 6h
        burn_rate_threshold=14.4,         # SRE workbook fast-burn page
    )
)

# Hot path — O(1)
tracker.record_success()
tracker.record_failure()

snap = tracker.snapshot()
print(f"success ratio: {snap.success_ratio:.4f}")
print(f"budget left:   {snap.error_budget_remaining:.2%}")
print(f"burn rate:     {snap.burn_rate:.2f}")

if snap.is_budget_exhausted:
    print("Freeze deploys.")

for alert in tracker.check_burn_rate():
    print(f"FAST BURN over {alert.window_seconds}s: {alert.burn_rate:.1f}x budget")
```

---

## FastAPI middleware

`SLOMiddleware` auto-classifies every HTTP response — by default 5xx and unhandled exceptions are failures, everything else is a success. Override with your own classifier when 4xx (or specific routes) should burn budget.

```python
from fastapi import FastAPI
from fastapi.responses import Response
from slo_budget_tracker import (
    PrometheusExporter,
    SLODefinition,
    SLOMiddleware,
    SLORegistry,
)

registry = SLORegistry()
registry.define(SLODefinition(name="availability", target=0.999))
registry.define(SLODefinition(name="freshness",    target=0.99))

app = FastAPI()
app.add_middleware(SLOMiddleware, registry=registry, slo_name="availability")

exporter = PrometheusExporter(registry)


@app.get("/metrics")
async def metrics() -> Response:
    body, content_type = exporter.render()
    return Response(content=body, media_type=content_type)


@app.get("/slo")
async def slo_snapshot() -> dict[str, object]:
    return {"slos": [s.__dict__ for s in registry.snapshot_all()]}
```

Point your Prometheus scrape at `/metrics` and you get:

```
slo_target{slo="availability"} 0.999
slo_success_ratio{slo="availability"} 0.9991
slo_error_budget_remaining{slo="availability"} 0.42
slo_burn_rate{slo="availability",window_seconds="3600"} 2.1
slo_burn_rate{slo="availability",window_seconds="21600"} 0.8
slo_breached{slo="availability"} 0.0
```

---

## Custom classification

Default: anything `< 500` and no exception is a success. Want 4xx to burn budget? Pass `classify=`:

```python
app.add_middleware(
    SLOMiddleware,
    registry=registry,
    slo_name="availability",
    classify=lambda status, exc: exc is None and status < 400,
)
```

The classifier receives `(status_code, exception_or_None)` and returns `True` for success.

---

## API surface

| Object             | Purpose                                                            |
| ------------------ | ------------------------------------------------------------------ |
| `SLODefinition`    | Frozen dataclass: name, target, window, burn-rate windows + threshold. Validates at construction. |
| `SLOTracker`       | Records observations, computes snapshots and burn-rate alerts.     |
| `SLORegistry`      | Holds many named trackers; supports `snapshot_all()` and `check_burn_rates()`. |
| `SLOMiddleware`    | ASGI middleware that auto-records HTTP outcomes against a tracker. |
| `PrometheusExporter` | Renders the registry as Prometheus text format on demand.        |
| `Observation`      | `(timestamp, success)` event.                                      |
| `SLOSnapshot`      | Point-in-time view: ratios, failures, budget remaining, burn rate. |
| `BurnRateAlert`    | One short window has crossed the configured threshold.             |
| `BurnRateSample`   | One short-window measurement attached to a snapshot.               |

---

## Burn-rate math

```
error_budget   = (1 - target) * total_requests_in_window
budget_used    = failures_in_window
remaining_pct  = (error_budget - budget_used) / error_budget

burn_rate(short_window) = (1 - success_ratio(short_window)) / (1 - target)
```

A `burn_rate == 1.0` means the service is failing at exactly the rate the SLO allows. `burn_rate == 14.4` means the next 30-day budget is being eaten in ~2 days. The default threshold of `14.4` follows the [SRE Workbook fast-burn page](https://sre.google/workbook/alerting-on-slos/#5-multiwindow-multi-burn-rate-alerts).

---

## Storage backends

The default `InMemoryStore` keeps a thread-safe deque trimmed to the window. For services pushing `>` ~100 rps you'll want a sampling or bucketed backend — wire one in by passing `store=` to `SLOTracker`. The protocol is small:

```python
class ObservationStore(Protocol):
    def record(self, observation: Observation) -> None: ...
    def window(self, now: float, seconds: int) -> list[Observation]: ...
    def trim(self, before: float) -> None: ...
    def __len__(self) -> int: ...
```

A Redis sorted-set backend is on the roadmap (`ZADD`/`ZREMRANGEBYSCORE`); contributions welcome.

---

## Tests

```bash
pip install -e ".[dev]"
ruff check src tests && ruff format --check src tests
mypy src
pytest -v
```

The CI matrix runs Python 3.11 / 3.12 / 3.13.

---

## Related work in this ecosystem

This is part of the [**Platform Reliability Stack**](https://github.com/mizcausevic-dev) — small, focused libraries that compose into a production reliability story:

- **[procurement-decision-api](https://github.com/mizcausevic-dev/procurement-decision-api)** — drafts AI Procurement Decision Cards from vendor Suite documents.
- **reliability-toolkit-rs** — async rate-limit + circuit-breaker + retry + bulkhead in Rust _(coming next)_.
- More at [kineticgain.com](https://kineticgain.com/).

---

## License

MIT. See [LICENSE](LICENSE).
