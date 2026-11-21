"""Engine observability read model (Engine Arc Phase 6 — T6.1/T6.2).

PURE FUNCTIONS over INJECTED sources: the router injects the rows, the
engine snapshot, the convergence mapping, the resolved switch states and
the bounded runs tail. Observation-only contract: **no engine-state
mutation; the catalog fold drains its own metrics ring** (``event_view``
composes the P5 registry's ``snapshot()``, whose CONSUMING READ contract
folds the hot ring into per-kind totals and drains it — P5 rider-5; the
engine-tick-side periodic flush is P4's half). This module exists so a
future ``/metrics`` UI tab or the ``maintenancer`` tool has a STABLE,
FLAT, SORTED shape to bind to (Emperor addendum, 2026-11-21: "the
register function should include like service tag name, category, module
information (use for metric, management,..debug easier) … Later we may
even have tool for maintenancer agent to use or even a table chart on
metrics tabs").

Views
-----
* :func:`list_view` — the paged, filtered job inventory (default limit
  100, hard cap 500; opaque cursor).
* :func:`detail_view` — one job + its schedule/health + the resolved
  kill-switch state (D6.7) + a BOUNDED runs tail (≤ 20, D6.10).
* :func:`event_view` — the P5 kind catalog + the per-kind firing/consumer
  facts, composed through the P5 registry's ``snapshot()``.

T6.3 — THE METRICS-FAMILY CEREMONY: **DEFERRED** (the plan's default
outcome (b), recorded here so the absence is a DECISION with a pointer,
never a silent gap):

    The engine's inventory ships on its own read surface
    (``/api/engine/*``), which needs NO census change. A chart-primary
    ``ensemble_engine_*`` family — letting a future ``/metrics`` tab
    render an engine chart from the registry rather than from
    ``/api/engine`` — is DEFERRED. Reasons: (a) the addendum's request is
    satisfied by a labelled, attributed inventory (service_tag /
    category / module_info) without a family; (b) the metrics census is
    CLOSED with a zero-headroom band convention, so adding a family is a
    deliberate act by whoever wants the chart, not a side effect of
    building the substrate; (c) the engine is dormant — a chart of
    nothing would be dead surface.

    UNFENCING PATH (if a later phase wants the chart): ONE change
    carrying (1) a ``CATALOG_FAMILY_VERDICTS`` row, (2) a
    ``REGISTERED_FAMILIES`` member, (3) a ``FAMILY_SERIES_BANDS``
    ceiling, (4) an ``ALLOWED_DAEMON_DIFF``/``PRE_EXISTING_DAEMON_
    OFFENDERS`` record (this module lives outside ``WIRING_ROOT``), and
    (5) the census-count pin edits — mutation-verified, with the
    zero-headroom band rule (``F23``'s 67 = 67 precedent) held exactly.

Convergence (T6.2 / D6.6): the ``estate`` block unions ``job_metrics()``'s
``{last_success_ts, consecutive_skips}`` (P2's ``project_merged``) with
the engine snapshot — each field carrying its ``source``. ``job_metrics()``
itself is UNTOUCHED (fence F-2: its published block set is pinned).

Fences honored here: F-1 (zero metrics registered), F-2 (no
``job_metrics()`` edit), F-3/F-11 (no ``action_by``/``acted_at`` dims; no
``resource_pid_map`` + ``action`` pairing), D6.9/D6.10 (bounds), D6.8 (no
mutating view).
"""

from __future__ import annotations

import base64
import json
from typing import Any, Iterable, Mapping, Sequence

# The two SANCTIONED intra-package imports (dispatch ownership row):
# P2's convergence mapping + P3's pure kill-switch resolver. Both are
# pure/injected — nothing here touches the engine runtime.
from daemon.services.engine.killswitch import EngineSwitchResolver
from daemon.services.engine.metrics import project_merged

#: The default + ceiling page sizes (D6.9 — house paging convention).
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

#: The bounded runs tail on a detail read (D6.10).
RUNS_TAIL_LIMIT = 20

#: The opaque-cursor version (bumped only on a shape change).
CURSOR_VERSION = 1

# ── frozen key sets (AC1: asserted exactly by the unit pack) ─────────────

SCHEDULE_KEYS: frozenset[str] = frozenset(
    {
        "kind",
        "interval_seconds",
        "jitter_seconds",
        "first_tick",
        "offset_seconds",
        "event_kind",
    }
)

HEALTH_KEYS: frozenset[str] = frozenset(
    {
        "last_run_at",
        "last_outcome",
        "last_skip_reason",
        "consecutive_skips",
        "dead",
        "running",
        "skips_by_reason",
        "next_due",
        "would_fire",
    }
)

ESTATE_KEYS: frozenset[str] = frozenset({"last_success_ts", "consecutive_skips"})

JOB_ROW_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "service_tag",
        "service_name",
        "category",
        "module_info",
        "description",
        "enabled",
        "trigger_kind",
        "schedule",
        "health",
        "chart_hint",
        "metadata",
        "priority",
        "concurrency_class",
        "max_runtime_s",
        "timeout_policy",
        "retry_max",
        "kill_switch_key",
        "version",
        "estate",
    }
)

DETAIL_KEYS: frozenset[str] = JOB_ROW_KEYS | {
    "registered_at",
    "updated_at",
    "switches",
    "runs",
}

SWITCH_KEYS: frozenset[str] = frozenset(
    {"engine_enabled", "shadow_mode", "job_killed", "should_run", "skip_reason"}
)

RUN_ROW_KEYS: frozenset[str] = frozenset(
    {
        "started_at",
        "finished_at",
        "outcome",
        "skip_reason",
        "duration_seconds",
        "attempt",
    }
)

EVENT_VIEW_KEYS: frozenset[str] = frozenset({"kinds", "counters", "generated_at"})

LIST_VIEW_KEYS: frozenset[str] = frozenset({"jobs", "next_cursor", "returned", "limit"})


class InvalidCursor(ValueError):
    """A cursor that cannot be decoded / validated (→ HTTP 400)."""


# ── small helpers ───────────────────────────────────────────────────────


def _coerce_priority(value: Any) -> int:
    """``priority`` as a plain int — a REAL 0 is a 0 (not a default).

    The ``or``-default idiom silently promotes a legitimate priority 0 to
    the default; the priority participates in the ordering contract, so
    it is coerced explicitly here.
    """
    if value is None:
        return 5
    try:
        return int(value)
    except (TypeError, ValueError):
        return 5


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read an attribute from an ORM row OR a dict (duck-typed sources)."""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def encode_cursor(payload: Mapping[str, Any]) -> str:
    """Encode an OPAQUE cursor (the only shape: ``{"v": 1, "o": offset}``)."""
    raw = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Decode + validate an opaque cursor. Raises :class:`InvalidCursor`."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except Exception as exc:  # noqa: BLE001 — any decode failure is a 400
        raise InvalidCursor("The cursor is not decodable") from exc
    if not isinstance(payload, dict):
        raise InvalidCursor("The cursor carries no state")
    if payload.get("v") != CURSOR_VERSION:
        raise InvalidCursor("The cursor version is not supported")
    offset = payload.get("o", 0)
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise InvalidCursor("The cursor carries an invalid position")
    return {"offset": offset}


def clamp_limit(value: Any, *, default: int = DEFAULT_LIMIT, cap: int = MAX_LIMIT) -> int:
    """Clamp ``limit`` INTO ``[1, cap]`` (D6.9: default 100, hard cap 500)."""
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, cap))


def chart_hint_for(trigger_kind: str | None) -> str | None:
    """``chart_hint`` derives ONLY from the trigger kind (AC1 — never a guess).

    An ``interval`` trigger is a cadence measured at a point ⇒ ``gauge``;
    an ``event`` trigger is a count of occurrences ⇒ ``counter``. Any
    other / missing kind yields ``None`` (no fabricated hint).
    """
    if trigger_kind == "interval":
        return "gauge"
    if trigger_kind == "event":
        return "counter"
    return None


def _schedule_block(row: Any) -> dict[str, Any]:
    trigger_kind = _get(row, "trigger_kind")
    if trigger_kind == "interval":
        first_tick = _get(row, "first_tick_kind")
        return {
            "kind": "interval",
            "interval_seconds": _get(row, "interval_seconds"),
            "jitter_seconds": _get(row, "jitter_seconds"),
            "first_tick": first_tick,
            "offset_seconds": _get(row, "first_tick_offset_seconds"),
            "event_kind": None,
        }
    if trigger_kind == "event":
        return {
            "kind": "event",
            "interval_seconds": None,
            "jitter_seconds": None,
            "first_tick": None,
            "offset_seconds": None,
            "event_kind": _get(row, "event_kind"),
        }
    return {
        "kind": str(trigger_kind) if trigger_kind else "unknown",
        "interval_seconds": None,
        "jitter_seconds": None,
        "first_tick": None,
        "offset_seconds": None,
        "event_kind": None,
    }


def _health_block(row: Any, snap_entry: Mapping[str, Any] | None) -> dict[str, Any]:
    """The health block — row columns as truth, RAM snapshot as enrichment.

    A job with ZERO runs renders honestly: ``last_run_at=None`` and the
    snapshot-derived fields take their typed defaults (never fabricated).
    """
    snap_entry = snap_entry or {}
    skips = snap_entry.get("skips_by_reason")
    return {
        "last_run_at": _get(row, "last_run_at"),
        "last_outcome": _get(row, "last_outcome"),
        "last_skip_reason": _get(row, "last_skip_reason"),
        "consecutive_skips": int(_get(row, "consecutive_skips", 0) or 0),
        "dead": bool(_get(row, "dead", 0) or snap_entry.get("dead", False)),
        "running": bool(snap_entry.get("running", False)),
        "skips_by_reason": dict(skips) if isinstance(skips, Mapping) else {},
        "next_due": snap_entry.get("next_due"),
        "would_fire": bool(snap_entry.get("would_fire", False)),
    }


def _estate_block(
    job_id: str,
    merged: Mapping[str, Any] | None,
    maintenance_job_ids: Iterable[str] | None,
) -> dict[str, Any]:
    """The convergence block (T6.2 / D6.6) — per-field ``source`` attribution.

    ``project_merged`` gives the merged values; the SOURCE of the two
    F-2 fields is "maintenance" when the job appears in
    ``job_metrics()``'s output (the maintenance tier owns those numbers),
    else "engine" (the engine snapshot filled them).
    """
    block = (merged or {}).get(job_id) or {}
    maint_ids = set(maintenance_job_ids or ())
    source = "maintenance" if job_id in maint_ids else "engine"
    return {
        "last_success_ts": {
            "value": block.get("last_success_ts"),
            "source": source,
        },
        "consecutive_skips": {
            "value": int(block.get("consecutive_skips", 0) or 0),
            "source": source,
        },
    }


def job_row(
    row: Any,
    *,
    snapshot: Mapping[str, Any] | None = None,
    merged: Mapping[str, Any] | None = None,
    maintenance_job_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Project ONE job row into the tab-ready flat shape (frozen key set)."""
    job_id = str(_get(row, "id"))
    snap_entry = (snapshot or {}).get(job_id)
    meta = _get(row, "job_metadata", None)
    if meta is None:
        meta = _get(row, "metadata", None)
    trigger_kind = _get(row, "trigger_kind")
    return {
        "id": job_id,
        "service_tag": _get(row, "service_tag"),
        "service_name": _get(row, "service_name"),
        "category": _get(row, "category"),
        "module_info": _get(row, "module_info"),
        "description": _get(row, "description"),
        "enabled": bool(_get(row, "enabled", 0)),
        "trigger_kind": trigger_kind,
        "schedule": _schedule_block(row),
        "health": _health_block(row, snap_entry),
        "chart_hint": chart_hint_for(trigger_kind),
        "metadata": dict(meta) if isinstance(meta, Mapping) else {},
        "priority": _coerce_priority(_get(row, "priority", None)),
        "concurrency_class": _get(row, "concurrency_class"),
        "max_runtime_s": _get(row, "max_runtime_s"),
        "timeout_policy": _get(row, "timeout_policy"),
        "retry_max": int(_get(row, "retry_max", 0) or 0),
        "kill_switch_key": _get(row, "kill_switch_key"),
        "version": int(_get(row, "version", 1) or 1),
        "estate": _estate_block(job_id, merged, maintenance_job_ids),
    }


def _matches_filters(
    row: Any,
    *,
    category: str | None,
    service_tag: str | None,
    enabled: bool | None,
) -> bool:
    if category is not None and _get(row, "category") != category:
        return False
    if service_tag is not None and _get(row, "service_tag") != service_tag:
        return False
    if enabled is not None and bool(_get(row, "enabled", 0)) is not enabled:
        return False
    return True


def list_view(
    rows: Sequence[Any],
    *,
    snapshot: Mapping[str, Any] | None = None,
    merged: Mapping[str, Any] | None = None,
    maintenance_job_ids: Iterable[str] | None = None,
    category: str | None = None,
    service_tag: str | None = None,
    enabled: bool | None = None,
    limit: int = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    """The paged, filtered job inventory (D6.4 row shape; D6.9 bounds).

    Deterministic ordering ``(priority, id)`` — the same order the
    repository uses — so a cursor walk is no-dup / no-omission against a
    stable source list.
    """
    limit = clamp_limit(limit)
    offset = decode_cursor(cursor)["offset"] if cursor else 0
    filtered = [
        r
        for r in rows
        if _matches_filters(
            r, category=category, service_tag=service_tag, enabled=enabled
        )
    ]
    filtered.sort(
        key=lambda r: (
            _coerce_priority(_get(r, "priority", None)),
            str(_get(r, "id")),
        )
    )
    page = filtered[offset : offset + limit]
    jobs = [
        job_row(
            r,
            snapshot=snapshot,
            merged=merged,
            maintenance_job_ids=maintenance_job_ids,
        )
        for r in page
    ]
    consumed = offset + len(page)
    next_cursor = (
        encode_cursor({"v": CURSOR_VERSION, "o": consumed})
        if consumed < len(filtered)
        else None
    )
    return {
        "jobs": jobs,
        "next_cursor": next_cursor,
        "returned": len(jobs),
        "limit": limit,
    }


def _run_row(run: Any) -> dict[str, Any]:
    return {
        "started_at": _get(run, "started_at"),
        "finished_at": _get(run, "finished_at"),
        "outcome": _get(run, "outcome"),
        "skip_reason": _get(run, "skip_reason"),
        "duration_seconds": _get(run, "duration_seconds"),
        "attempt": int(_get(run, "attempt", 0) or 0),
    }


def switches_block(resolver: Any, kill_switch_key: str | None) -> dict[str, Any]:
    """The resolved kill-switch state (D6.7) — through P3's pure resolver.

    ``resolver`` is an ``EngineSwitchResolver``; the resolver exposes
    ``engine_enabled()`` / ``shadow_mode()`` / ``job_killed(key)`` — no
    raw env values ever leave this function.
    """
    if resolver is None:
        return {
            "engine_enabled": None,
            "shadow_mode": None,
            "job_killed": None,
            "should_run": None,
            "skip_reason": None,
        }
    job_killed = bool(resolver.job_killed(kill_switch_key))
    engine_on = bool(resolver.engine_enabled())
    if job_killed or not engine_on:
        should_run, skip_reason = False, "KILL_SWITCHED"
    else:
        should_run, skip_reason = True, None
    return {
        "engine_enabled": engine_on,
        "shadow_mode": bool(resolver.shadow_mode()),
        "job_killed": job_killed,
        "should_run": should_run,
        "skip_reason": skip_reason,
    }


def detail_view(
    row: Any,
    *,
    snapshot: Mapping[str, Any] | None = None,
    merged: Mapping[str, Any] | None = None,
    maintenance_job_ids: Iterable[str] | None = None,
    runs: Sequence[Any] = (),
    resolver: Any = None,
) -> dict[str, Any]:
    """One job's full read (D6.4 + D6.7 + D6.10).

    ``runs`` is the INJECTED bounded tail — the caller fetches at most
    :data:`RUNS_TAIL_LIMIT` rows (newest first). This function never
    reads a DB.
    """
    base = job_row(
        row,
        snapshot=snapshot,
        merged=merged,
        maintenance_job_ids=maintenance_job_ids,
    )
    tail = list(runs)[:RUNS_TAIL_LIMIT]
    base["registered_at"] = _get(row, "registered_at")
    base["updated_at"] = _get(row, "updated_at")
    base["switches"] = switches_block(resolver, _get(row, "kill_switch_key"))
    base["runs"] = [_run_row(r) for r in tail]
    return base


def event_view(*, bus: Any = None, snapshot_fn: Any = None) -> dict[str, Any]:
    """The P5 kind catalog + per-kind firing/consumer facts (T6.1).

    Composes through the P5 registry's ``snapshot()`` — which, per its
    CONSUMING READ contract (P5 rider-5), folds the hot ring into the
    per-kind totals and drains it (cumulative totals, exactly-once
    folding). This read surface is the documented composition point for
    that fold (the engine-tick-side periodic flush is P4's half).
    """
    if snapshot_fn is None:
        from daemon.services.event_registry import snapshot as snapshot_fn  # local: keep import surface minimal
    snap = snapshot_fn(bus=bus)
    return {
        "kinds": snap.get("kinds", {}),
        "counters": snap.get("counters", {}),
        "generated_at": snap.get("generated_at"),
    }


__all__ = [
    "CURSOR_VERSION",
    "DEFAULT_LIMIT",
    "DETAIL_KEYS",
    "ESTATE_KEYS",
    "EVENT_VIEW_KEYS",
    "HEALTH_KEYS",
    "InvalidCursor",
    "JOB_ROW_KEYS",
    "LIST_VIEW_KEYS",
    "MAX_LIMIT",
    "RUNS_TAIL_LIMIT",
    "RUN_ROW_KEYS",
    "SCHEDULE_KEYS",
    "SWITCH_KEYS",
    "chart_hint_for",
    "clamp_limit",
    "decode_cursor",
    "detail_view",
    "encode_cursor",
    "event_view",
    "job_row",
    "list_view",
    "switches_block",
]
