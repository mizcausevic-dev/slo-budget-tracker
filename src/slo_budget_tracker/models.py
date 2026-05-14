"""
Dataclass models for SLO definitions, observations, snapshots, and burn-rate alerts.

Kept dataclass-only (no Pydantic dependency) so this library stays cheap to import
inside a hot request path.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SLODefinition:
    """
    Declarative description of one SLO.

    Attributes:
        name:                  Human-readable identifier (also used as Prometheus label).
        target:                Target success ratio in (0, 1). E.g. 0.999 means "three nines".
        window_seconds:        Rolling window the SLO is evaluated over. 30 days = 2_592_000.
        burn_rate_windows:     Shorter windows (in seconds) used for burn-rate alerting.
                               Defaults to (3600, 21600) — 1h and 6h.
        burn_rate_threshold:   Burn-rate value above which the SLO is considered "burning fast".
                               Default 14.4 follows the SRE workbook's 1h/2% alert.
        description:           Optional free-form note.
    """

    name: str
    target: float
    window_seconds: int = 30 * 24 * 3600  # 30 days
    burn_rate_windows: tuple[int, ...] = (3600, 21600)
    burn_rate_threshold: float = 14.4
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("SLODefinition.name must be non-empty")
        if not (0.0 < self.target < 1.0):
            raise ValueError(f"SLODefinition.target must be in (0, 1); got {self.target}")
        if self.window_seconds <= 0:
            raise ValueError("SLODefinition.window_seconds must be positive")
        for w in self.burn_rate_windows:
            if w <= 0 or w > self.window_seconds:
                raise ValueError(
                    f"burn_rate_window {w}s must be positive and <= window_seconds ({self.window_seconds}s)"
                )
        if self.burn_rate_threshold <= 1:
            raise ValueError("burn_rate_threshold must be > 1 (otherwise it's the trivial limit)")

    @property
    def error_budget_ratio(self) -> float:
        """1 - target. The fraction of requests we're allowed to fail."""
        return 1.0 - self.target


@dataclass(frozen=True, slots=True)
class Observation:
    """A single observed event: was it a success, and when did it happen."""

    timestamp: float
    success: bool


@dataclass(frozen=True, slots=True)
class BurnRateSample:
    """Burn rate measured over one specific short window."""

    window_seconds: int
    burn_rate: float
    success_ratio: float
    sample_count: int


@dataclass(frozen=True, slots=True)
class SLOSnapshot:
    """
    Point-in-time view of an SLO's health.

    All ratios are in [0, 1]. error_budget_remaining is a ratio of the *budget*
    (not of total requests): 1.0 means the budget is untouched, 0.0 means
    completely spent, negative means the SLO is in breach.
    """

    name: str
    target: float
    window_seconds: int
    total: int
    failures: int
    success_ratio: float
    error_budget_remaining: float
    burn_rate: float
    burn_rate_samples: tuple[BurnRateSample, ...] = field(default_factory=tuple)

    @property
    def is_breached(self) -> bool:
        """True when actual success ratio is below the SLO target."""
        return self.success_ratio < self.target

    @property
    def is_budget_exhausted(self) -> bool:
        """True when the rolling error budget has been fully spent.

        Uses a tiny epsilon so dust from float arithmetic (e.g. 8.88e-16 instead
        of exactly 0) doesn't falsely report the budget as still alive.
        """
        return self.error_budget_remaining <= 1e-9


@dataclass(frozen=True, slots=True)
class BurnRateAlert:
    """Returned by SLOTracker.check_burn_rate when a short window crosses threshold."""

    slo_name: str
    window_seconds: int
    burn_rate: float
    threshold: float
    success_ratio: float
    sample_count: int
