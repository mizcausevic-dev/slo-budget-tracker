"""Unit tests for SLORegistry."""

from __future__ import annotations

import pytest

from slo_budget_tracker.models import SLODefinition
from slo_budget_tracker.registry import SLORegistry


class TestRegistry:
    def test_define_and_get(self) -> None:
        r = SLORegistry()
        r.define(SLODefinition(name="availability", target=0.999))
        t = r.get("availability")
        assert t.name == "availability"
        assert "availability" in r

    def test_duplicate_define_raises(self) -> None:
        r = SLORegistry()
        r.define(SLODefinition(name="a", target=0.99))
        with pytest.raises(ValueError, match="already registered"):
            r.define(SLODefinition(name="a", target=0.99))

    def test_get_unknown_raises_keyerror(self) -> None:
        r = SLORegistry()
        with pytest.raises(KeyError):
            r.get("missing")

    def test_iteration_and_names(self) -> None:
        r = SLORegistry()
        r.define(SLODefinition(name="a", target=0.99))
        r.define(SLODefinition(name="b", target=0.999))
        assert set(r.names()) == {"a", "b"}
        assert len(r) == 2
        seen = {t.name for t in r}
        assert seen == {"a", "b"}

    def test_snapshot_all(self) -> None:
        r = SLORegistry()
        a = r.define(SLODefinition(name="a", target=0.99))
        b = r.define(SLODefinition(name="b", target=0.999))
        a.record_success(at=10.0)
        b.record_failure(at=10.0)
        snaps = r.snapshot_all(at=20.0)
        by_name = {s.name: s for s in snaps}
        assert by_name["a"].success_ratio == 1.0
        assert by_name["b"].success_ratio == 0.0

    def test_check_burn_rates_aggregates(self) -> None:
        r = SLORegistry()
        a = r.define(SLODefinition(name="a", target=0.99, burn_rate_windows=(60,)))
        for i in range(10):
            a.record_failure(at=950.0 + i)
        alerts = r.check_burn_rates(at=1000.0)
        assert len(alerts) == 1
        assert alerts[0].slo_name == "a"
