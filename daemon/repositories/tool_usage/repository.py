"""SYNC repository for the ``tool_usage_daily`` ledger (Phase 1).

Per the house dual-driver pattern (``message_metadata`` / memoir / room)
the engine is the shared sync ``sqlalchemy.Engine`` from
``daemon/repositories/factory.py``; callers bridge to async via
``asyncio.to_thread``. There is NO ``async def`` in this module.

The three primitives:

* :meth:`ToolUsageRepository.upsert_batch` — ONE multi-row
  ``INSERT ... ON CONFLICT (...) DO UPDATE SET call_count = call_count +
  excluded.call_count, updated_at = excluded.updated_at``. Dialect-aware
  (PG / SQLite). Duplicate PKs WITHIN one batch are coalesced before the
  VALUES build — A-6 (🔴 must-fix): PG raises ``cardinality_violation``
  on two rows with the same PK in one multi-row VALUES while SQLite
  silently accumulates. The flusher's counter map already guarantees one
  row per PK by construction; this coalescing is the belt-and-braces
  second guarantee the dual-dialect test pins.
* :meth:`ToolUsageRepository.query_summary` — per-tool aggregate over the
  24h / N-day / total windows plus a top-50 per-agent breakdown.
* :meth:`ToolUsageRepository.query_daily` — zero-filled one-point-per-day
  series for one tool.

NAMES ONLY: no argument payload ever enters this table.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .models import ToolUsageDaily

if TYPE_CHECKING:
    from sqlalchemy import Engine

logger = logging.getLogger(__name__)

#: The nested-agents abuse bound (D21 pin 3): at most 50 agents per tool.
MAX_AGENTS_PER_TOOL = 50

_PK = ("day", "project_id", "tool_name", "agent_id")


@dataclass(frozen=True)
class ToolUsageDelta:
    """One coalesced counter delta handed to ``upsert_batch``."""

    day: str
    project_id: str
    tool_name: str
    agent_id: str
    call_count: int
    updated_at: str


class ToolUsageRepository:
    """SYNC repository for ``tool_usage_daily``."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ------------------------------------------------------------------
    # write path
    # ------------------------------------------------------------------
    def upsert_batch(self, deltas: list[ToolUsageDelta]) -> int:
        """Batch additive upsert. Returns the number of rows touched.

        Empty input short-circuits to 0 without issuing SQL. Duplicate
        PKs in the input are coalesced (call_counts summed, latest
        ``updated_at`` kept) so the multi-row VALUES carries at most one
        row per PK — the A-6 PG-safety precondition.
        """
        if not deltas:
            return 0

        coalesced: dict[tuple[str, str, str, str], ToolUsageDelta] = {}
        for delta in deltas:
            key = (delta.day, delta.project_id, delta.tool_name, delta.agent_id)
            prev = coalesced.get(key)
            if prev is None:
                coalesced[key] = delta
            else:
                coalesced[key] = ToolUsageDelta(
                    day=delta.day,
                    project_id=delta.project_id,
                    tool_name=delta.tool_name,
                    agent_id=delta.agent_id,
                    call_count=prev.call_count + delta.call_count,
                    updated_at=max(prev.updated_at, delta.updated_at),
                )

        rows = [
            {
                "day": d.day,
                "project_id": d.project_id,
                "tool_name": d.tool_name,
                "agent_id": d.agent_id,
                "call_count": d.call_count,
                "updated_at": d.updated_at,
            }
            for d in coalesced.values()
        ]

        with self._engine.begin() as conn:  # SYNC transaction
            tbl = ToolUsageDaily.__table__
            if conn.dialect.name == "postgresql":
                stmt = pg_insert(tbl).values(rows)
            else:
                stmt = sqlite_insert(tbl).values(rows)
            # Frozen upsert contract (D16): additive call_count, flush
            # time updated_at via EXCLUDED — never now() (PG-only, wrong
            # clock).
            stmt = stmt.on_conflict_do_update(
                index_elements=list(_PK),
                set_={
                    "call_count": tbl.c.call_count + stmt.excluded.call_count,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
            result = conn.execute(stmt)
            return result.rowcount or 0

    # ------------------------------------------------------------------
    # read path (door)
    # ------------------------------------------------------------------
    @staticmethod
    def _today() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    @staticmethod
    def _days_ago(today_iso: str, n_days: int) -> str:
        d = date.fromisoformat(today_iso) - timedelta(days=n_days - 1)
        return d.isoformat()

    def has_tool(self, tool_name: str) -> bool:
        """True if the ledger has EVER held a row for ``tool_name``.

        The door uses this to distinguish an unknown tool (404) from a
        known tool with an empty window (zero-filled 200). Project-agnostic
        by design — "known" is a property of the tool, not of a filter.
        """
        with self._engine.connect() as conn:
            row = conn.execute(
                select(ToolUsageDaily.__table__.c.tool_name)
                .where(ToolUsageDaily.__table__.c.tool_name == tool_name)
                .limit(1)
            ).first()
        return row is not None

    def query_summary(
        self,
        project_id: str | None,
        days: int,
        *,
        today: str | None = None,
    ) -> list[dict[str, Any]]:
        """Per-tool aggregate rows for the summary door.

        ``project_id is None`` ⇒ all projects INCLUDING the ``__none__``
        bucket (total truth). An explicit value filters to that project.
        ``today`` is injectable for deterministic tests; production uses
        the current UTC date.
        """
        today_iso = today or self._today()
        day_start = self._days_ago(today_iso, days)
        tbl = ToolUsageDaily.__table__

        where = [tbl.c.day >= day_start]
        if project_id is not None:
            where.append(tbl.c.project_id == project_id)

        # One scan: fetch the rows in the medium window with per-agent
        # detail; derive 24h / N-day / total from the same rows in memory
        # (the table is tiny — see architecture §4 growth estimate).
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(
                    tbl.c.tool_name,
                    tbl.c.agent_id,
                    tbl.c.day,
                    tbl.c.call_count,
                ).where(*where)
            ).all()
            # Lifetime totals are a SEPARATE aggregate over ALL days —
            # the medium-window scan above must not leak into calls_total.
            # The project filter still applies (it is a filter on the whole
            # response, not just the windowed columns).
            total_where = []
            if project_id is not None:
                total_where.append(tbl.c.project_id == project_id)
            total_stmt = select(tbl.c.tool_name, tbl.c.call_count)
            if total_where:
                total_stmt = total_stmt.where(*total_where)
            total_rows = conn.execute(total_stmt).all()

        totals_all: dict[str, int] = {}
        for tool_name, call_count in total_rows:
            totals_all[tool_name] = totals_all.get(tool_name, 0) + call_count

        by_tool: dict[str, dict[str, Any]] = {}
        for tool_name, agent_id, day, call_count in rows:
            entry = by_tool.setdefault(
                tool_name,
                {"calls_24h": 0, "calls_nd": 0, "agents": {}},
            )
            if day == today_iso:
                entry["calls_24h"] += call_count
            entry["calls_nd"] += call_count
            agents = entry["agents"]
            agents[agent_id] = agents.get(agent_id, 0) + call_count

        out: list[dict[str, Any]] = []
        for tool_name, entry in by_tool.items():
            top_agents = sorted(
                entry["agents"].items(), key=lambda kv: (-kv[1], kv[0])
            )[:MAX_AGENTS_PER_TOOL]
            out.append(
                {
                    "tool_name": tool_name,
                    "calls_24h": entry["calls_24h"],
                    "calls_7d": entry["calls_nd"],
                    "calls_total": totals_all.get(tool_name, 0),
                    "agents": [
                        {"agent_id": aid, "calls_7d": cnt} for aid, cnt in top_agents
                    ],
                }
            )
        return out

    def query_daily(
        self,
        project_id: str | None,
        tool_name: str,
        days: int,
        *,
        today: str | None = None,
    ) -> list[dict[str, Any]]:
        """Zero-filled ``[{day, calls}, ...]`` for one tool, oldest first."""
        today_iso = today or self._today()
        day_start = self._days_ago(today_iso, days)
        tbl = ToolUsageDaily.__table__

        where = [
            tbl.c.tool_name == tool_name,
            tbl.c.day >= day_start,
            tbl.c.day <= today_iso,
        ]
        if project_id is not None:
            where.append(tbl.c.project_id == project_id)

        with self._engine.connect() as conn:
            rows = conn.execute(
                select(tbl.c.day, tbl.c.call_count).where(*where)
            ).all()
        totals: dict[str, int] = {}
        for day, call_count in rows:
            totals[day] = totals.get(day, 0) + call_count

        d = date.fromisoformat(day_start)
        end = date.fromisoformat(today_iso)
        series: list[dict[str, Any]] = []
        while d <= end:
            iso = d.isoformat()
            series.append({"day": iso, "calls": totals.get(iso, 0)})
            d += timedelta(days=1)
        return series


__all__ = ["ToolUsageRepository", "ToolUsageDelta", "MAX_AGENTS_PER_TOOL"]
