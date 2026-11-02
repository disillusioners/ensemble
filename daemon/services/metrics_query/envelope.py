"""The envelope builders + the pagination/cursor helpers (plan task 2.9).

The envelope is the SAME vocabulary on all three routes:

``schema_version`` (the envelope-shape axis) · ``vocabulary_version``
(the label-vocabulary axis) · ``generated_at_utc`` (aware UTC, the
house producer) · ``restart_epoch`` (the boot id — a restart is
visible, never a silent gap) · ``activation`` (the posture block).

The cursor (AM-6) is OPAQUE and pins the evaluation instant + the
position.  An invalid or expired cursor is an EXPECTED condition:
``InvalidCursor`` maps to ``400 invalid_cursor`` — never a 500, and
never a silent truncation.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from typing import Any, Iterable, Sequence

from daemon.checkpoint_metrics import VOCABULARY_VERSION
from daemon.services.metrics_query.constants import (
    DEFAULT_MAX_POINTS,
    MAX_FAMILIES,
    QUERY_SCHEMA_VERSION,
    RESPONSE_BYTE_CEILING,
    STALE_INTERVAL_MULTIPLIER,
    WINDOW_SECONDS,
)
from daemon.services.metrics_query.protocol import MetricsQueryProvider
from daemon.services.timestamps import now_utc_iso


class InvalidCursor(ValueError):
    """An invalid or expired cursor (→ 400 invalid_cursor)."""


def restart_epoch() -> str:
    """The current boot id — the envelope's ``restart_epoch`` (AM-14).

    ``bash_spawn_reaper.CURRENT_BOOT_ID`` is the boot id: it changes on
    every restart, so a gap in the trend is visible instead of silent.
    (``boot_epoch.get_boot_epoch()`` is NOT used — that is the DB-clock
    domain and it is None-able.)
    """
    from daemon.services.bash_spawn_reaper import CURRENT_BOOT_ID

    return CURRENT_BOOT_ID


def activation_block(provider: MetricsQueryProvider) -> dict[str, Any]:
    """The ``activation`` block — the posture of the three surfaces."""
    return {
        "listener_enabled": bool(getattr(provider, "listener_enabled", False)),
        "census_enabled": bool(getattr(provider, "census_enabled", False)),
        "query_api": "auth-gated-always-on",
    }


def envelope_header(provider: MetricsQueryProvider) -> dict[str, Any]:
    """The shared envelope header (all three routes)."""
    return {
        "schema_version": QUERY_SCHEMA_VERSION,
        "vocabulary_version": VOCABULARY_VERSION,
        "generated_at_utc": now_utc_iso(),
        "restart_epoch": restart_epoch(),
        "activation": activation_block(provider),
    }


def build_families_envelope(
    provider: MetricsQueryProvider,
    *,
    limit: int = MAX_FAMILIES,
    offset: int = 0,
) -> dict[str, Any]:
    """The ``/families`` envelope.

    ``offset`` resumes a PAGE (AM-6): the cursor pins the position, so a
    bounded response is never SILENTLY truncated — the caller resumes
    where the previous page stopped.  ``truncated`` means "more families
    remain AFTER this page" (the cursor's continuation condition).
    """
    families = list(provider.families())
    total = len(families)
    start = max(0, int(offset))
    page = families[start : start + limit]
    return {
        **envelope_header(provider),
        "families": page,
        "truncated": start + len(page) < total,
    }


def build_snapshot_envelope(
    provider: MetricsQueryProvider,
    *,
    family: str | None = None,
    name_prefix: str | None = None,
) -> dict[str, Any]:
    """The ``/snapshot`` envelope."""
    series = provider.snapshot(family=family, name_prefix=name_prefix)
    return {**envelope_header(provider), "series": series}


def _provider_float(provider: MetricsQueryProvider, attribute: str, default: float) -> float:
    """Read a numeric fact off a provider (property OR method)."""
    raw = getattr(provider, attribute, default)
    if callable(raw):
        try:
            raw = raw()
        except Exception:  # pragma: no cover — a broken provider never 500s the door
            return default
    try:
        return float(raw)
    except (TypeError, ValueError):  # pragma: no cover
        return default


def effective_interval_s(provider: MetricsQueryProvider) -> float:
    """The OBSERVED effective spacing ``max(sampler, publisher)`` (AM-15)."""
    return _provider_float(provider, "effective_interval_s", 15.0)


def build_series_envelope(
    provider: MetricsQueryProvider,
    *,
    names: Sequence[str] | None = None,
    series: Sequence[str] | None = None,
    window_s: int | None = None,
    max_points: int | None = None,
    now: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    """The ``/series`` envelope (the trend view).

    The window anchors at the server's ``generated_at_utc``; a cursor
    pins a different evaluation instant.  ``stale`` is true when the
    newest sample is older than 3x the effective interval — a wedged
    sampler surfaces as STALENESS, never a silently frozen line.

    ``offset`` resumes a PAGE (AM-6) over the returned series list.
    """
    window = int(window_s) if window_s else WINDOW_SECONDS
    window = max(1, min(window, WINDOW_SECONDS))
    instant = int(now) if now is not None else int(time.time())
    limit = max_points if max_points is not None else DEFAULT_MAX_POINTS
    payload = provider.history(
        names=names, series=series, window_s=window, max_points=limit, now=instant
    )
    start = max(0, int(offset))
    page = payload[start:]
    interval = effective_interval_s(provider)
    age = _provider_float(provider, "last_sample_age_s", 0.0)
    return {
        **envelope_header(provider),
        "window": {
            "from_utc": _iso(instant - window),
            "to_utc": _iso(instant),
            "interval_s": interval,
            "interval_s_source": "observed",
        },
        "last_sample_age_s": age,
        "stale": age > STALE_INTERVAL_MULTIPLIER * interval,
        "series": page,
    }


def _iso(instant: int) -> str:
    """An aware-UTC ISO-8601 string for an epoch second."""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(int(instant), tz=timezone.utc).isoformat()


# ── the cursor (AM-6) ──────────────────────────────────────────────────────


def encode_cursor(payload: dict[str, Any]) -> str:
    """Encode an OPAQUE cursor (the evaluation instant + the position)."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Decode a cursor; raises :class:`InvalidCursor` when it is invalid."""
    if not isinstance(cursor, str) or not cursor.strip():
        raise InvalidCursor("empty cursor")
    padded = cursor.strip()
    padding = "=" * (-len(padded) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded + padding)
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursor(f"undecodable cursor: {exc}") from exc
    if not isinstance(payload, dict):
        raise InvalidCursor("cursor payload is not an object")
    return payload


def cursor_is_expired(
    cursor: dict[str, Any], *, oldest_timestamp: int | None, now: int
) -> bool:
    """Whether a cursor points before the buffer's oldest sample."""
    instant = cursor.get("t")
    if not isinstance(instant, int):
        raise InvalidCursor("cursor carries no evaluation instant")
    if oldest_timestamp is not None and instant < oldest_timestamp:
        return True
    return False


def bound_body(
    payload: dict[str, Any], *, byte_ceiling: int = RESPONSE_BYTE_CEILING
) -> tuple[dict[str, Any], bool]:
    """Bound a response body to the byte ceiling.

    Returns ``(payload, truncated)``.  The body is bounded, never
    SILENTLY truncated: the caller carries the cursor that resumes it.
    """
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= byte_ceiling:
        return payload, False
    return payload, True


def family_limit() -> int:
    """The families-per-response ceiling."""
    return MAX_FAMILIES


__all__ = [
    "InvalidCursor",
    "activation_block",
    "bound_body",
    "build_families_envelope",
    "build_series_envelope",
    "build_snapshot_envelope",
    "cursor_is_expired",
    "decode_cursor",
    "effective_interval_s",
    "encode_cursor",
    "envelope_header",
    "family_limit",
    "restart_epoch",
]
