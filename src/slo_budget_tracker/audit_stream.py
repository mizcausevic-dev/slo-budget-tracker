"""
Optional audit-stream-py integration.

When the `AUDIT_STREAM_URL` env var is set, callers using
[`SLOTracker.check_burn_rate_with_audit`][..tracker.SLOTracker.check_burn_rate_with_audit]
fire governance events whenever a burn-rate alert transitions:

  slo_burn_started   first time a window crosses the burn-rate threshold
                     (or transitions from "not alerting" to "alerting" after
                     a previous recovery)
  slo_recovered      window was alerting on the previous call but isn't now

Same opt-in pattern as the other Python producers in the Kinetic Gain
suite (procurement-decision-api, aeo-validator-service,
policy-as-code-engine, data-contract-registry). Identical env-var
contract.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_TIMEOUT_S = 2.5


def is_enabled() -> bool:
    """True when AUDIT_STREAM_URL is set to a non-empty value."""
    return bool(os.environ.get("AUDIT_STREAM_URL", "").strip())


def base_url() -> str | None:
    """Stripped audit-stream base URL, or None when disabled."""
    raw = os.environ.get("AUDIT_STREAM_URL", "").strip()
    if not raw:
        return None
    return raw.rstrip("/")


def timeout_s() -> float:
    """Configured per-call timeout. Defaults to 2.5s."""
    raw = os.environ.get("AUDIT_STREAM_TIMEOUT_S", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_S
    try:
        return max(0.1, float(raw))
    except ValueError:
        return DEFAULT_TIMEOUT_S


async def emit(
    client: httpx.AsyncClient,
    *,
    kind: str,
    payload: dict[str, Any],
) -> None:
    """Fire one event. Silent no-op when AUDIT_STREAM_URL is unset."""
    url = base_url()
    if url is None:
        return

    body = {
        "kind": kind,
        "source": "slo-budget-tracker",
        "payload": payload,
    }
    try:
        response = await client.post(
            f"{url}/events",
            json=body,
            timeout=timeout_s(),
        )
        response.raise_for_status()
    except (httpx.HTTPError, OSError) as err:
        print(
            f"audit-stream emit failed (kind={kind}): {type(err).__name__}: {err}",
            flush=True,
        )
