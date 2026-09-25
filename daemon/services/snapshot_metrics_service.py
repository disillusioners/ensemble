"""Snapshot monitoring counters (R16 — Wave 3).

Light usage counters, MONITORING ONLY. The R16 contract:

* **Capture counts** — incremented on every ``snapshot_create``
  invocation regardless of R9 verdict (REUSE + NEW + SUPERSEDE +
  CREATE-FRESH all count). The key is the caller agent id (``scope
  = "capture:agent:<agent_id>"``) so the FE surfaces a per-agent
  breakdown.
* **Spawn-warm counts** — incremented on the ``spawn_hot_instance``
  WARM path ONLY. Cold / no-hit / expired / verify-failed spawns
  are NOT counted (R16 rider j). The key is the consumed snapshot
  id (``scope = "spawn:snapshot:<snapshot_id>"``).

**Guarantees**:

* **Fail-soft increments** — wraps every increment in try/except and
  logs on failure; spawn/create paths MUST NEVER raise on counter
  failure.
* **No locks held across awaits** — each ``inc_*`` opens its own
  short-lived SQLAlchemy Session, runs an ``upsert`` (or a guarded
  ``UPDATE ... THEN INSERT`` pair), and commits. Two concurrent
  increments are safe at the column level (PostgreSQL is atomic;
  SQLite serializes the writer).
* **Cheap** — the read path is a single SELECT keyed by the
  (``scope``, ``key``) UNIQUE index. No joins, no aggregation in
  SQL.
* **No ranking** — ranking modules (snapshot_search + snapshot_embedding)
  MUST NEVER import this module. Pinned by the
  ``MonitoringOnlyPinTest`` in ``tests/unit/tools/test_snapshot_v3_pin.py``.

DB guardrail: the storage lives in the brand-new
``snapshot_usage_counters`` table (``daemon/repositories/snapshot/models.py``).
``snapshots`` and ``snapshot_embeddings`` are INTACT.

The service constructor takes the shared SQLAlchemy ``engine`` so
the same SQLite memory handle (test scope) or PG DSN (production)
is used. The R16 surface reads the aggregated counters from
``daemon/routers/settings.py ``/snapshot-usage-metrics``.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from daemon.repositories.snapshot.models import (
    CAPTURE_COUNTER_PREFIX,
    SPAWN_COUNTER_PREFIX,
    SnapshotUsageCounter,
)

logger = logging.getLogger(__name__)


class SnapshotMetricsService:
    """R16 counter service: increment + surface, fail-soft throughout.

    All increments run as small upserts against the
    ``snapshot_usage_counters`` table. The service holds no state
    aside from the ``engine`` handle — there is no in-memory cache
    to invalidate, so a hot-restart or asyncio migration cannot
    desync counters from the DB.
    """

    def __init__(self, engine: Engine) -> None:
        """Store the engine handle.

        Args:
            engine: Shared SQLAlchemy engine (the same one the
                snapshot repository, project repository, and other
                SQLModel-backed tables use). Test scope = in-memory
                SQLite; production = the engine ``daemon/manager.py``
                constructs.
        """
        self._engine = engine

    # ── incrementers ──────────────────────────────────────────────────

    def inc_capture(self, agent_id: str) -> None:
        """Increment the capture counter for one agent id.

        Called on every ``snapshot_create`` invocation regardless of
        R9 verdict (REUSE + NEW + SUPERSEDE + CREATE-FRESH all
        count). Failure is logged at WARNING and the call is a
        no-op — the spawn/create path MUST NOT propagate counter
        failures (R16 rider j: never raise, never fail the tool).

        Args:
            agent_id: The caller agent id (e.g. ``"coder"``). An
                empty string is normalized to ``"unknown"`` so the
                counter row is never keyed by an empty natural key.
        """
        scope, key = self._capture_scope_key(agent_id)
        self._increment_upsert(scope, key)

    def inc_spawn(self, snapshot_id: str) -> None:
        """Increment the spawn-warm counter for one snapshot id.

        Called on the ``spawn_hot_instance`` WARM path ONLY. Cold /
        no-hit / expired / verify-failed spawns DO NOT increment
        (R16 rider j).

        Args:
            snapshot_id: The consumed snapshot id (the
                ``snapshot_id`` from the R14 result contract when
                ``started == "warm"``). An empty / ``None`` value is
                a programmer error here (the warm path is the only
                caller, and it always has an id); we still
                no-op-and-log instead of raising.
        """
        if not snapshot_id:
            logger.warning(
                "[Snapshot] inc_spawn called with empty snapshot_id "
                "(R16 warm-path call site is the only legitimate "
                "caller; this is a programming error)."
            )
            return
        scope, key = self._spawn_scope_key(snapshot_id)
        self._increment_upsert(scope, key)

    # ── surface (read) ────────────────────────────────────────────────

    async def surface(self) -> dict[str, Any]:
        """Read the aggregated counters (read-only, no mutations).

        Returns:
            ``{"capture_counts": {agent_id: {"created": int}, ...},
            "spawn_counts_per_snapshot": [{"snapshot_id": str,
            "count": int}, ...]}``.

            Only counters that have at least one observable
            increment surface (zero rows are omitted — a fresh
            deployment yields an empty surface, not zeros).
        """
        def _read() -> dict[str, Any]:
            with Session(self._engine) as session:
                rows = list(
                    session.exec(select(SnapshotUsageCounter)).all()
                )
            captures: dict[str, dict[str, int]] = {}
            spawns: list[dict[str, Any]] = []
            for row in rows:
                if row.scope == CAPTURE_COUNTER_PREFIX + row.key:
                    captures.setdefault(row.key, {})["created"] = row.value
                elif row.scope == SPAWN_COUNTER_PREFIX + row.key:
                    spawns.append(
                        {"snapshot_id": row.key, "count": row.value}
                    )
            return {
                "capture_counts": captures,
                "spawn_counts_per_snapshot": spawns,
            }

        import asyncio

        return await asyncio.to_thread(_read)

    # ── internals ─────────────────────────────────────────────────────

    @staticmethod
    def _capture_scope_key(agent_id: str) -> tuple[str, str]:
        cleaned = str(agent_id or "").strip() or "unknown"
        return f"{CAPTURE_COUNTER_PREFIX}{cleaned}", cleaned

    @staticmethod
    def _spawn_scope_key(snapshot_id: str) -> tuple[str, str]:
        cleaned = str(snapshot_id or "").strip()
        return f"{SPAWN_COUNTER_PREFIX}{cleaned}", cleaned

    def _increment_upsert(self, scope: str, key: str) -> None:
        """Upsert one counter row by (``scope``, ``key``) — fail-soft.

        The flow: read-modify-write under a single short Session.
        If the row exists, ``value`` increments by 1 and
        ``updated_at`` refreshes; if it doesn't, a fresh row lands
        at ``value=1``. The Session commits at the end; the
        ``engine`` dialect-aware insert handles dialect divergence.

        R16 rider j — failure is logged and swallowed. The agent
        tool calls MUST NOT raise.
        """
        try:
            with Session(self._engine) as session:
                row = session.exec(
                    select(SnapshotUsageCounter).where(
                        SnapshotUsageCounter.scope == scope,
                        SnapshotUsageCounter.key == key,
                    )
                ).one_or_none()
                if row is None:
                    row = SnapshotUsageCounter(
                        scope=scope,
                        key=key,
                        value=1,
                    )
                    session.add(row)
                else:
                    row.value = int(row.value or 0) + 1
                    row.updated_at = self._now_iso()
                    session.add(row)
                session.commit()
        except Exception as exc:  # pragma: no cover — defensive belt
            logger.warning(
                f"[Snapshot] R16 counter upsert failed for "
                f"scope={scope!r} key={key!r}: "
                f"{type(exc).__name__}: {exc}"
            )

    @staticmethod
    def _now_iso() -> str:
        """ISO-8601 UTC timestamp for counter rows."""
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()
