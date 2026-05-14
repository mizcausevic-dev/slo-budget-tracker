"""
ASGI middleware that records HTTP outcomes against an SLOTracker (or SLORegistry).

Default classification:
    success: status < 500 and no exception
    failure: status >= 500 or the inner app raised

The classification function is overridable so callers can mark 4xx as failures
on their public APIs, or carve out specific routes.

Usage:

    from fastapi import FastAPI
    from slo_budget_tracker import SLODefinition, SLORegistry, SLOMiddleware

    registry = SLORegistry()
    registry.define(SLODefinition(name="availability", target=0.999))

    app = FastAPI()
    app.add_middleware(SLOMiddleware, registry=registry, slo_name="availability")
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from .registry import SLORegistry
from .tracker import SLOTracker


class _Recorder(Protocol):
    @property
    def name(self) -> str: ...

    def record(self, success: bool, *, at: float | None = None) -> None: ...


ClassifyFn = Callable[[int, BaseException | None], bool]
"""Return True for success, False for failure. Receives (status_code, exception)."""


def default_classifier(status_code: int, exception: BaseException | None) -> bool:
    if exception is not None:
        return False
    return status_code < 500


class SLOMiddleware:
    """ASGI middleware that records each HTTP request against an SLO."""

    def __init__(
        self,
        app: Callable[..., Awaitable[None]],
        *,
        tracker: SLOTracker | None = None,
        registry: SLORegistry | None = None,
        slo_name: str | None = None,
        classify: ClassifyFn = default_classifier,
    ) -> None:
        if tracker is None and registry is None:
            raise ValueError("SLOMiddleware needs either tracker= or registry=")
        if registry is not None and slo_name is None:
            raise ValueError("registry= also requires slo_name= so the middleware knows which SLO to record")

        self._app = app
        self._classify = classify
        if tracker is not None:
            self._recorder: _Recorder = tracker
        else:
            assert registry is not None  # narrowed by the guard above
            assert slo_name is not None  # narrowed by the guard above
            self._recorder = registry.get(slo_name)

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        status_holder: dict[str, int] = {"status": 500}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message.get("status", 500))
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        except BaseException as err:
            self._recorder.record(self._classify(status_holder["status"], err))
            raise
        else:
            self._recorder.record(self._classify(status_holder["status"], None))
