"""Integration tests for SLOMiddleware with a real FastAPI app."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from slo_budget_tracker import SLODefinition, SLOMiddleware, SLORegistry


@pytest.fixture
def registry_and_app() -> tuple[SLORegistry, FastAPI]:
    registry = SLORegistry()
    registry.define(SLODefinition(name="availability", target=0.999))

    app = FastAPI()
    app.add_middleware(SLOMiddleware, registry=registry, slo_name="availability")

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/server-error")
    async def server_error() -> None:
        raise HTTPException(status_code=500, detail="boom")

    @app.get("/client-error")
    async def client_error() -> None:
        raise HTTPException(status_code=400, detail="nope")

    @app.get("/exception")
    async def exception() -> None:
        raise RuntimeError("oops")

    return registry, app


class TestSLOMiddleware:
    def test_records_success_for_2xx(self, registry_and_app: tuple[SLORegistry, FastAPI]) -> None:
        registry, app = registry_and_app
        with TestClient(app) as c:
            r = c.get("/ok")
            assert r.status_code == 200
        snap = registry.get("availability").snapshot()
        assert snap.total == 1
        assert snap.failures == 0

    def test_records_failure_for_5xx(self, registry_and_app: tuple[SLORegistry, FastAPI]) -> None:
        registry, app = registry_and_app
        with TestClient(app) as c:
            r = c.get("/server-error")
            assert r.status_code == 500
        snap = registry.get("availability").snapshot()
        assert snap.total == 1
        assert snap.failures == 1

    def test_4xx_counts_as_success_by_default(self, registry_and_app: tuple[SLORegistry, FastAPI]) -> None:
        registry, app = registry_and_app
        with TestClient(app) as c:
            r = c.get("/client-error")
            assert r.status_code == 400
        snap = registry.get("availability").snapshot()
        assert snap.total == 1
        assert snap.failures == 0

    def test_unhandled_exception_counts_as_failure(
        self, registry_and_app: tuple[SLORegistry, FastAPI]
    ) -> None:
        registry, app = registry_and_app
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.get("/exception")
            assert r.status_code == 500
        snap = registry.get("availability").snapshot()
        assert snap.total == 1
        assert snap.failures == 1

    def test_custom_classifier(self) -> None:
        registry = SLORegistry()
        registry.define(SLODefinition(name="strict", target=0.999))
        app = FastAPI()
        app.add_middleware(
            SLOMiddleware,
            registry=registry,
            slo_name="strict",
            classify=lambda status, exc: exc is None and status < 400,  # 4xx counts as failure too
        )

        @app.get("/client-error")
        async def client_error() -> None:
            raise HTTPException(status_code=400, detail="nope")

        with TestClient(app) as c:
            c.get("/client-error")
        snap = registry.get("strict").snapshot()
        assert snap.failures == 1

    def test_requires_tracker_or_registry(self) -> None:
        with pytest.raises(ValueError):
            SLOMiddleware(app=lambda *_: None)  # type: ignore[arg-type]

    def test_registry_requires_slo_name(self) -> None:
        registry = SLORegistry()
        registry.define(SLODefinition(name="a", target=0.99))
        with pytest.raises(ValueError, match="slo_name"):
            SLOMiddleware(app=lambda *_: None, registry=registry)  # type: ignore[arg-type]
