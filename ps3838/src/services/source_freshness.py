"""Source timestamps are independent from the time an API response is generated."""
from __future__ import annotations
from typing import Any


def crown_freshness(payload: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
    payload = payload or {}
    source_error = error or payload.get("error") or None
    updated = payload.get("updated_at") or payload.get("dataUpdatedAt") or None
    return {
        "dataUpdatedAt": updated,
        "cached": bool(payload.get("cached") or (source_error and updated)),
        "status": str(payload.get("status") or ("unavailable" if source_error else "unknown")),
        "error": source_error,
        "diagnostics": dict(payload.get("diagnostics") or {}),
    }


def pinnacle_freshness(snapshot: dict[str, Any]) -> dict[str, Any]:
    source = snapshot.get("source") or {}
    updated = source.get("lastCapturedAt") or None
    status = str(snapshot.get("freshness") or "no-data")
    return {
        "dataUpdatedAt": updated,
        "cached": bool(updated and status != "live"),
        "status": status,
        "error": None,
        "diagnostics": {"lagSeconds": snapshot.get("lagSeconds")},
    }
