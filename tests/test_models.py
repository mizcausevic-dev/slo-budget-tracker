"""Unit tests for the dataclass models."""

from __future__ import annotations

import pytest

from slo_budget_tracker.models import SLODefinition


class TestSLODefinition:
    def test_defaults(self) -> None:
        d = SLODefinition(name="availability", target=0.999)
        assert d.window_seconds == 30 * 24 * 3600
        assert d.burn_rate_windows == (3600, 21600)
        assert d.burn_rate_threshold == 14.4

    def test_error_budget_ratio(self) -> None:
        d = SLODefinition(name="x", target=0.99)
        assert d.error_budget_ratio == pytest.approx(0.01)

    def test_empty_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="name"):
            SLODefinition(name="", target=0.99)

    def test_target_must_be_strictly_between_0_and_1(self) -> None:
        with pytest.raises(ValueError, match="target"):
            SLODefinition(name="x", target=1.0)
        with pytest.raises(ValueError, match="target"):
            SLODefinition(name="x", target=0.0)
        with pytest.raises(ValueError, match="target"):
            SLODefinition(name="x", target=-0.1)

    def test_window_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="window_seconds"):
            SLODefinition(name="x", target=0.99, window_seconds=0)

    def test_burn_rate_window_must_fit_inside_window(self) -> None:
        with pytest.raises(ValueError, match="burn_rate_window"):
            SLODefinition(name="x", target=0.99, window_seconds=3600, burn_rate_windows=(7200,))

    def test_burn_rate_threshold_must_exceed_one(self) -> None:
        with pytest.raises(ValueError, match="burn_rate_threshold"):
            SLODefinition(name="x", target=0.99, burn_rate_threshold=1.0)
