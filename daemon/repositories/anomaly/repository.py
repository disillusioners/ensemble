"""The ``anomaly_reports`` + ``anomaly_clusters`` writer (phase 1).

The repository behind ``daemon/services/anomaly_service.py``. Phase 1
ships the WRITE half plus the phase-2-ready readers (the rate-limit
COUNTs, the cluster key lookup, the capacity counts) — clustering,
rate-limit ENFORCEMENT, the board sink and retention land in phase 2.

Two house rules are baked in here:

* **CUTOFFS ARE PYTHON-BOUND.** Every window predicate binds a
  Python-computed ISO-8601 parameter; the SQL never says
  ``now() - interval`` (AM-4 — on PostgreSQL a TEXT column has no
  such operator, so that shape ERRORS on the live driver).
* **NO CROSS-TABLE TRANSACTION IS IMPLIED.** The report INSERT and the
  cluster INSERT/UPDATE execute as ONE unit of work in phase 2 (AM-5);
  phase 1's accept path writes a DEGENERATE cluster tail
  (``cluster_id`` NULL), so the two writes do not yet coexist. The
  methods are therefore separate and each owns its own transaction —
  the phase-2 unit-of-work composes them inside ONE session.
"""

from __future__ import annotations

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from .models import (
    OUTCOME_ACCEPTED,
    AnomalyCluster,
    AnomalyReport,
)


class AnomalyRepository:
    """Writer + readers for the nest anomaly store.

    Constructed with the daemon engine (the same engine the instance
    repository uses). Methods here raise on failure by design — the
    SERVICE owns the degradation policy (a harvest failure degrades
    that FIELD to NULL; only a persistence failure degrades the whole
    report).
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ── writers ───────────────────────────────────────────────────────

    def insert_report(self, report: AnomalyReport) -> str:
        """INSERT one report row in its own transaction.

        Returns the row id (captured INSIDE the session — commit
        expires the row's attributes and a post-close read raises
        ``DetachedInstanceError``, the notify_audit lesson).
        """
        with Session(self._engine) as session:
            session.add(report)
            session.commit()
            return report.id

    def insert_cluster(self, cluster: AnomalyCluster) -> str:
        """INSERT one cluster row in its own transaction."""
        with Session(self._engine) as session:
            session.add(cluster)
            session.commit()
            return cluster.id

    def update_cluster_rollup(
        self,
        cluster_id: str,
        *,
        report_count: int,
        last_report_at: str,
        max_severity: str,
    ) -> None:
        """Fold one more member into a cluster's rollup columns.

        Phase 2's call (the cluster row is mutated in place — the
        identity is never re-derived). Phase 1 does not call it: the
        accept path writes ``cluster_id`` NULL.
        """
        with Session(self._engine) as session:
            cluster = session.get(AnomalyCluster, cluster_id)
            if cluster is None:
                return
            cluster.report_count = int(report_count)
            cluster.last_report_at = last_report_at
            cluster.max_severity = max_severity
            session.add(cluster)
            session.commit()

    def set_cluster_board_item(self, cluster_id: str, board_item_id: str) -> None:
        """Stamp the board row the critical gate created (phase 2)."""
        with Session(self._engine) as session:
            cluster = session.get(AnomalyCluster, cluster_id)
            if cluster is None:
                return
            cluster.board_item_id = board_item_id
            session.add(cluster)
            session.commit()

    # ── phase-2-ready readers ─────────────────────────────────────────

    def count_reports(
        self,
        *,
        agent_id: str | None = None,
        tree_root_instance_id: str | None = None,
        outcome: str | None = OUTCOME_ACCEPTED,
        since: str | None = None,
    ) -> int:
        """COUNT reports matching the given filters.

        The index-served shape the phase-2 rate gate needs
        (``agent_id`` + ``created_at`` rides
        ``idx_anomaly_reports_agent_created``; the tree-root variant
        rides ``idx_anomaly_reports_tree_root_created``; the outcome
        variant rides ``idx_anomaly_reports_outcome_created``).

        Args:
            agent_id: Restrict to one agent TYPE (AM-10 — the budget
                key is the type).
            tree_root_instance_id: Restrict to one tree root.
            outcome: Restrict to one outcome; ``None`` counts every
                outcome (the TOTAL-row capacity count).
            since: An ISO-8601 lower bound (Python-bound — never a
                SQL ``now() - interval``).

        Returns:
            The matching row count (0 when nothing matches).
        """
        with Session(self._engine) as session:
            stmt = select(func.count()).select_from(AnomalyReport)
            if agent_id is not None:
                stmt = stmt.where(AnomalyReport.agent_id == agent_id)
            if tree_root_instance_id is not None:
                stmt = stmt.where(
                    AnomalyReport.tree_root_instance_id == tree_root_instance_id
                )
            if outcome is not None:
                stmt = stmt.where(AnomalyReport.outcome == outcome)
            if since is not None:
                stmt = stmt.where(AnomalyReport.created_at >= since)
            return int(session.exec(stmt).one())

    def find_cluster_for_key(
        self,
        *,
        tree_root_instance_id: str,
        category: str,
        since: str,
    ) -> AnomalyCluster | None:
        """The R5 write-time cluster lookup — ONE composite-index seek.

        ``WHERE tree_root=? AND category=? AND last_report_at >= cutoff
        ORDER BY last_report_at DESC LIMIT 1`` — served by
        ``idx_anomaly_clusters_key_window``.

        Args:
            tree_root_instance_id: The key component.
            category: The canonical category (the key component).
            since: The sliding-window cutoff (Python-bound ISO-8601).

        Returns:
            The most recent matching cluster, or ``None`` (a new
            incident).
        """
        with Session(self._engine) as session:
            stmt = (
                select(AnomalyCluster)
                .where(AnomalyCluster.tree_root_instance_id == tree_root_instance_id)
                .where(AnomalyCluster.category == category)
                .where(AnomalyCluster.last_report_at >= since)
                .order_by(AnomalyCluster.last_report_at.desc())
                .limit(1)
            )
            return session.exec(stmt).first()

    def get_report(self, report_id: str) -> AnomalyReport | None:
        """One report row by id, or ``None``."""
        with Session(self._engine) as session:
            return session.get(AnomalyReport, report_id)

    # ── retention (phase 2) ───────────────────────────────────────────

    def delete_reports_older_than(self, cutoff: str, *, limit: int) -> int:
        """DELETE up to ``limit`` reports older than ``cutoff``.

        The batched retention DELETE (phase 2 hooks this into the
        EXISTING periodic maintenance seam — no new scheduler). The
        LIMIT keeps the delete bounded; the caller loops.
        """
        with Session(self._engine) as session:
            rows = session.exec(
                select(AnomalyReport.id)
                .where(AnomalyReport.created_at < cutoff)
                .limit(max(1, int(limit)))
            ).all()
            if not rows:
                return 0
            for (row_id,) in rows:
                report = session.get(AnomalyReport, row_id)
                if report is not None:
                    session.delete(report)
            session.commit()
            return len(rows)

    def delete_clusters_older_than(self, cutoff: str, *, limit: int) -> int:
        """DELETE up to ``limit`` clusters whose last report is older
        than ``cutoff``."""
        with Session(self._engine) as session:
            rows = session.exec(
                select(AnomalyCluster.id)
                .where(AnomalyCluster.last_report_at < cutoff)
                .limit(max(1, int(limit)))
            ).all()
            if not rows:
                return 0
            for (row_id,) in rows:
                cluster = session.get(AnomalyCluster, row_id)
                if cluster is not None:
                    session.delete(cluster)
            session.commit()
            return len(rows)


__all__ = ["AnomalyRepository"]
