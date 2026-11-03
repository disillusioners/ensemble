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
from typing import Any, Sequence

from daemon.checkpoint_metrics import VOCABULARY_VERSION
from daemon.services.metrics_query.constants import (
    DEFAULT_MAX_POINTS,
    MAX_FAMILIES,
    MAX_SERIES,
    MAX_SNAPSHOT_SERIES,
    MAX_TOTAL_POINTS,
    QUERY_SCHEMA_VERSION,
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
    """The ``activation`` block — the posture of the three surfaces.

    ``registry_read_ok`` is the DEGRADED flag (H5): it is ``False`` only
    when the provider's registry read raised.  A failed read answers
    ``200`` with an EMPTY body — byte-identical to a legitimately empty
    registry — so without this flag a client cannot tell the two apart.
    It is ADDITIVE and DEFAULT-SAFE: a provider that does not carry the
    fact (a duck-typed fake, any pre-H5 provider) reads as ``True``.
    """
    return {
        "listener_enabled": bool(getattr(provider, "listener_enabled", False)),
        "census_enabled": bool(getattr(provider, "census_enabled", False)),
        "query_api": "auth-gated-always-on",
        "registry_read_ok": bool(getattr(provider, "registry_read_ok", True)),
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
    offset: int = 0,
    limit: int = MAX_SNAPSHOT_SERIES,
) -> dict[str, Any]:
    """The ``/snapshot`` envelope.

    The route is BOUNDED like the other two (W-4): ``offset`` resumes a
    PAGE and ``limit`` caps its size, so a snapshot response can never
    outgrow the byte ceiling silently — the caller gets a
    ``truncated`` flag and a continuation cursor.
    """
    series = provider.snapshot(family=family, name_prefix=name_prefix)
    total = len(series)
    start = max(0, int(offset))
    page = series[start : start + max(1, int(limit))]
    return {
        **envelope_header(provider),
        "series": page,
        "truncated": start + len(page) < total,
    }


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


#: The PUBLIC name of the seam's numeric-fact reader (the agent-tool door
#: reads the provider's numeric facts through THIS — never through the
#: private ``_provider_float`` and never through ``getattr``).  The
#: internal name is kept so every existing call site is untouched.
read_numeric_fact = _provider_float


def build_series_envelope(
    provider: MetricsQueryProvider,
    *,
    names: Sequence[str] | None = None,
    series: Sequence[str] | None = None,
    window_s: int | None = None,
    max_points: int | None = None,
    now: int | None = None,
    offset: int = 0,
    max_series: int = MAX_SERIES,
    max_total_points: int = MAX_TOTAL_POINTS,
) -> dict[str, Any]:
    """The ``/series`` envelope (the trend view).

    The window anchors at the server's ``generated_at_utc``; a cursor
    pins a different evaluation instant.  ``stale`` is true when the
    newest sample is older than 3x the effective interval — a wedged
    sampler surfaces as STALENESS, never a silently frozen line.

    ``offset`` resumes a PAGE (AM-6) over the returned series list.

    THE EMITTED BOUNDS (W-2) — THE AM-12 CLAIM MADE REAL
    ---------------------------------------------------
    The AM-12 bound ("≤ 240 points × ≤ 50 selectors ⇒ ≤ 12 000 points
    ≈ 3.6 ms of residual on-loop encode") was a CONSTANT IDENTITY, not
    an enforcement: the selector ceiling bounds the REQUESTED
    selectors, and a request with NO selector matched EVERY published
    series — each carrying up to ``max_points`` points.  So the page is
    bounded here, during the OFF-LOOP build, on two axes:

    * the SERIES ceiling (:data:`MAX_SERIES`) — how many series the
      page carries;
    * the TOTAL-POINT ceiling (:data:`MAX_TOTAL_POINTS`) — how many
      points the page carries ACROSS every series (this is the number
      the on-loop encode pays for).

    A bound that drops something sets ``truncated`` — the route mints
    the continuation cursor, so a bounded page is never a SILENT
    truncation.
    """
    window = int(window_s) if window_s else WINDOW_SECONDS
    window = max(1, min(window, WINDOW_SECONDS))
    instant = int(now) if now is not None else int(time.time())
    limit = max_points if max_points is not None else DEFAULT_MAX_POINTS
    start = max(0, int(offset))
    ceiling = max(1, int(max_series))
    # The WORK bound: the provider collects only what this page needs
    # plus ONE probe entry, which tells us whether more series remain
    # (the continuation condition) without materialising the rest.
    payload = provider.history(
        names=names,
        series=series,
        window_s=window,
        max_points=limit,
        now=instant,
        max_series=start + ceiling + 1,
    )
    more_available = len(payload) > start + ceiling
    page = payload[start : start + ceiling]
    page, points_dropped = _bound_page_points(page, max_total_points)
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
        "truncated": bool(more_available or points_dropped),
    }


def _bound_page_points(
    page: list[dict[str, Any]], max_total_points: int
) -> tuple[list[dict[str, Any]], bool]:
    """Bound a page's TOTAL point count (the AM-12 claim, enforced).

    Returns ``(kept, dropped)``.  A single series is never dropped for
    being too large on its own — the per-series point count is already
    bounded by ``max_points`` (240) — so the budget is spent series by
    series and the remainder is dropped.
    """
    budget = max(1, int(max_total_points))
    kept: list[dict[str, Any]] = []
    total = 0
    for entry in page:
        points = entry.get("points") or []
        if kept and total + len(points) > budget:
            return kept, True
        kept.append(entry)
        total += len(points)
    return kept, False


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


__all__ = [
    "InvalidCursor",
    "activation_block",
    "build_families_envelope",
    "build_series_envelope",
    "build_snapshot_envelope",
    "cursor_is_expired",
    "decode_cursor",
    "effective_interval_s",
    "encode_cursor",
    "envelope_header",
    "restart_epoch",
]
