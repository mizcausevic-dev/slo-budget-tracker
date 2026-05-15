"""
slo-budget-tracker — SLO + error-budget tracking for Python services.

Pieces:
    SLODefinition      — describe a target (e.g. 99.9% over 30 days)
    SLOTracker         — record observations, compute success ratio / budget / burn rate
    SLORegistry        — hold many named SLOs
    SLOMiddleware      — ASGI middleware that auto-records HTTP outcomes
    PrometheusExporter — expose tracker state as Prometheus gauges

The math follows the Google SRE workbook:
    error_budget = (1 - target) * window
    burn_rate    = (1 - actual_success_ratio) / (1 - target)
"""

from __future__ import annotations

from .middleware import SLOMiddleware
from .models import BurnRateAlert, Observation, SLODefinition, SLOSnapshot
from .prometheus import PrometheusExporter
from .registry import SLORegistry
from .tracker import SLOTracker

__version__ = "0.1.1"

__all__ = [
    "BurnRateAlert",
    "Observation",
    "PrometheusExporter",
    "SLODefinition",
    "SLOMiddleware",
    "SLORegistry",
    "SLOSnapshot",
    "SLOTracker",
    "__version__",
]
