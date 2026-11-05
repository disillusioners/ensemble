"""The ``anomaly_reports`` + ``anomaly_clusters`` writer (phases 1-2).

The repository behind ``daemon/services/anomaly_service.py``. Phase 1
shipped the WRITE half plus the phase-2-ready readers; phase 2 (C3)
adds the CLUSTERING unit-of-work, the shared shed helper and the
retention/trims:

* **AM-5a — ONE TRANSACTION.** :meth:`assign_report_unit` executes the
  report INSERT + the cluster INSERT/UPDATE + the report→cluster
  binding in ONE repository transaction (the caller wraps the whole
  call in ONE ``db_offload`` offload). ``accepted`` MEANS clustered:
  a row that cannot be assigned is written ``unclustered`` directly
  (the AM-5b terminal state), never ``accepted`` + NULL.
* **D2 — the rollup is RELATIVE.** The cluster UPDATE is
  ``report_count = report_count + 1 ... RETURNING report_count`` —
  never absolute-from-a-stale-read (a concurrent per-tree shed under a
  DIFFERENT stripe slot would be clobbered by the absolute form under
  READ COMMITTED).
* **E4 — an EMPTY RETURNING is an assignment failure.** The cluster
  was zero-count-DELETED by a concurrent shed between the SELECT and
  the UPDATE; the report row is terminalized ``unclustered`` — NEVER a
  ``cluster_id`` pointing at a deleted cluster.
* **E5 — ONE shed helper, two callers.** :func:`shed_members` is THE
  decrement-and-delete implementation (per-cluster RELATIVE decrement
  per AM-6 + the R-b zero-count cluster DELETE in the same
  unit-of-work); the LIVE per-tree shed-then-accept and BOTH
  maintenance trims call it. One implementation, no drift.
* **AM-4 — CUTOFFS ARE PYTHON-BOUND.** Every window predicate binds a
  Python-computed ISO-8601 parameter; the SQL never says
  ``now() - interval`` (on PostgreSQL a TEXT column has no such
  operator, so that shape ERRORS on the live driver).
* **AM-6 — retention DECREMENTS.** Every batched DELETE of member
  rows runs through :func:`shed_members`, so
  ``report_count == COUNT(members WHERE outcome='accepted')`` holds
  ABSOLUTELY.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, case, delete, exists, func, select, update
from sqlmodel import Session, select as sm_select

from daemon.models.board import BoardItem

from .models import (
    OUTCOME_ACCEPTED,
    OUTCOME_UNCLUSTERED,
    SEVERITY_CRITICAL,
    SEVERITY_MAJOR,
    AnomalyCluster,
    AnomalyReport,
)

#: The severity rank (R5 — the incremental max-rank rollup).
SEVERITY_RANK: dict[str, int] = {"minor": 1, "major": 2, "critical": 3}


def _rank(value: str) -> int:
    return SEVERITY_RANK.get(value, 1)


def max_severity_rollup(current: str, incoming: str) -> str:
    """The incremental max-rank rollup (R5)."""
    return incoming if _rank(incoming) > _rank(current) else current


def cluster_key_hash(tree_root_instance_id: str, category: str) -> str:
    """SHA-256 of ``"{tree_root}|{canonical category}"`` (R5)."""
    import hashlib

    return hashlib.sha256(
        f"{tree_root_instance_id}|{category}".encode("utf-8")
    ).hexdigest()


def _new_uuid() -> str:
    import uuid

    return str(uuid.uuid4())


def _select_candidate(
    session: Session,
    *,
    tree_root_instance_id: str,
    category: str,
    since: str,
) -> Any:
    """The R5 write-time cluster lookup — ONE composite-index seek.

    ``WHERE tree_root=? AND category=? AND last_report_at >= cutoff
    ORDER BY last_report_at DESC LIMIT 1`` — served by
    ``idx_anomaly_clusters_key_window``. A module-level function (not a
    method) so the unit-of-work and the standalone reader share the
    exact same statement.
    """
    stmt = (
        select(
            AnomalyCluster.id,
            AnomalyCluster.first_report_at,
            AnomalyCluster.max_severity,
        )
        .where(AnomalyCluster.tree_root_instance_id == tree_root_instance_id)
        .where(AnomalyCluster.category == category)
        .where(AnomalyCluster.last_report_at >= since)
        .order_by(AnomalyCluster.last_report_at.desc())
        .limit(1)
    )
    return session.execute(stmt).first()


def shed_members(session: Session, report_ids: list[str]) -> int:
    """E5 — THE shared decrement-and-delete helper.

    Deletes the given member rows and, IN THE SAME unit-of-work:

    * per-cluster RELATIVE decrement — ``report_count = report_count
      - :n`` grouped over the batch's deleted ids (AM-6; composes with
      concurrent relative INCREMENTS under READ COMMITTED — D2), and
    * the R-b zero-count cluster DELETE — a cluster whose
      ``report_count`` reaches 0 and has no surviving members is
      DELETED (``DELETE ... WHERE id=:c AND report_count <= 0 AND NOT
      EXISTS (members)``).

    One implementation, TWO callers (the live per-tree
    shed-then-accept at insert, and both maintenance trims) — no
    drift. Rows with a NULL ``cluster_id`` (refusal rows) are deleted
    with NO decrement (they are not members).
    """
    ids = [str(item) for item in report_ids if item]
    if not ids:
        return 0
    groups = session.execute(
        select(AnomalyReport.cluster_id, func.count())
        .where(AnomalyReport.id.in_(ids))
        .where(AnomalyReport.cluster_id.is_not(None))
        .group_by(AnomalyReport.cluster_id)
    ).all()
    session.execute(delete(AnomalyReport).where(AnomalyReport.id.in_(ids)))
    for cluster_id, count in groups:
        session.execute(
            update(AnomalyCluster)
            .where(AnomalyCluster.id == cluster_id)
            .values(report_count=AnomalyCluster.report_count - int(count))
        )
        session.execute(
            delete(AnomalyCluster).where(
                AnomalyCluster.id == cluster_id,
                AnomalyCluster.report_count <= 0,
                ~exists(
                    select(1).where(
                        AnomalyReport.cluster_id == cluster_id
                    )
                ),
            )
        )
    return len(ids)


@dataclass
class ClusterAssignment:
    """What :meth:`assign_report_unit` produced.

    ``assigned`` is False ONLY on the E4 empty-RETURNING path — the
    report row was persisted ``unclustered`` (the AM-5b terminal
    state), never ``accepted`` + NULL. Every field is captured INSIDE
    the unit's session (commit expires the ORM instance and a
    post-close read raises ``DetachedInstanceError`` — the notify_audit
    lesson).
    """

    assigned: bool
    report_id: str
    cluster_id: str | None
    report_count: int
    first_report_at: str


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

        Phase 1's standalone write (kept for direct/test use). The
        LIVE accept path uses :meth:`assign_report_unit` — the
        RELATIVE, RETURNING-based form (D2); this absolute-form helper
        must NOT be used by the accept path.
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
        """Stamp the board row the critical gate created (R3/AM-13)."""
        with Session(self._engine) as session:
            cluster = session.get(AnomalyCluster, cluster_id)
            if cluster is None:
                return
            cluster.board_item_id = board_item_id
            session.add(cluster)
            session.commit()

    def get_cluster_board_item_id(self, cluster_id: str) -> str | None:
        """The cluster's board row id, or ``None`` (the create-or-append read)."""
        with Session(self._engine) as session:
            row = session.execute(
                select(AnomalyCluster.board_item_id).where(
                    AnomalyCluster.id == cluster_id
                )
            ).first()
            if row is None:
                return None
            return row[0]

    # ── the AM-5a ONE-TRANSACTION unit-of-work (phase 2) ──────────────

    def assign_report_unit(
        self,
        report: AnomalyReport,
        *,
        tree_root_instance_id: str,
        category: str,
        severity: str,
        window_cutoff: str,
        now: str,
        shed_report_ids: list[str] | None = None,
    ) -> ClusterAssignment:
        """Report INSERT + cluster INSERT/UPDATE + binding in ONE transaction.

        The order (per phase2-plan.md §"2+3" + the C3 shed):

        1. the optional LIVE per-tree shed (C3(ii) shed-then-accept —
           the oldest-first batch goes through the SHARED
           :func:`shed_members` helper INSIDE this same transaction);
        2. the R5 candidate seek (the composite-index SELECT);
        3. found → the D2 RELATIVE rollup UPDATE with ``RETURNING
           report_count``; an EMPTY RETURNING (E4 — the cluster was
           zero-count-DELETED by a concurrent shed between the SELECT
           and the UPDATE) terminalizes THIS report ``unclustered``;
        4. not found → a NEW cluster row (a legitimate re-incident —
           the key is intentionally non-unique over time, R5);
        5. the report INSERT carrying its FINAL ``cluster_id`` /
           ``outcome`` — ``accepted`` MEANS clustered (AM-5b).

        The caller (the service) wraps this whole call in ONE
        ``db_offload`` offload, inside the AM-14 same-key stripe.
        """
        key_hash = cluster_key_hash(tree_root_instance_id, category)
        with Session(self._engine) as session:
            # 1. the LIVE per-tree shed (never refuses — C3(ii)).
            if shed_report_ids:
                shed_members(session, shed_report_ids)
            # 2. the candidate seek.
            candidate = _select_candidate(
                session,
                tree_root_instance_id=tree_root_instance_id,
                category=category,
                since=window_cutoff,
            )
            if candidate is not None:
                cluster_id = str(candidate[0])
                first_report_at = str(candidate[1])
                new_max = max_severity_rollup(str(candidate[2]), severity)
                # 3. the D2 RELATIVE rollup + RETURNING.
                post_count = session.execute(
                    update(AnomalyCluster)
                    .where(AnomalyCluster.id == cluster_id)
                    .values(
                        report_count=AnomalyCluster.report_count + 1,
                        last_report_at=now,
                        max_severity=new_max,
                    )
                    .returning(AnomalyCluster.report_count)
                ).scalars().first()
                if post_count is None:
                    # E4 — the cluster died between the SELECT and this
                    # UPDATE. The report row is TERMINALIZED
                    # ``unclustered`` (the AM-5b live-failure mechanism):
                    # NEVER a cluster_id pointing at a deleted cluster.
                    report.cluster_id = None
                    report.outcome = OUTCOME_UNCLUSTERED
                    session.add(report)
                    session.commit()
                    return ClusterAssignment(
                        assigned=False,
                        report_id=report.id,
                        cluster_id=None,
                        report_count=1,
                        first_report_at=report.created_at,
                    )
                count = int(post_count)
            else:
                # 4. a NEW cluster (the re-incident INSERT — R5's own
                #    algorithm; the key is intentionally non-unique).
                cluster_id = _new_uuid()
                first_report_at = now
                count = 1
                session.add(
                    AnomalyCluster(
                        id=cluster_id,
                        cluster_key_hash=key_hash,
                        tree_root_instance_id=tree_root_instance_id,
                        category=category,
                        first_report_at=now,
                        last_report_at=now,
                        report_count=1,
                        max_severity=severity,
                        board_item_id=None,
                        created_at=now,
                    )
                )
            # 5. the report INSERT carrying its FINAL identity.
            report.cluster_id = cluster_id
            report.outcome = OUTCOME_ACCEPTED
            session.add(report)
            report_id = report.id
            session.commit()
            return ClusterAssignment(
                assigned=True,
                report_id=report_id,
                cluster_id=cluster_id,
                report_count=count,
                first_report_at=first_report_at,
            )

    # ── phase-2-ready readers ─────────────────────────────────────────

    def count_reports(
        self,
        *,
        agent_id: str | None = None,
        tree_root_instance_id: str | None = None,
        outcome: str | None = OUTCOME_ACCEPTED,
        severity: str | None = None,
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
                outcome (the TOTAL-row capacity count — the F-1
                single TOTAL-row bound).
            severity: Restrict to one severity tier (the critical
                counting leg).
            since: An ISO-8601 lower bound (Python-bound — never a
                SQL ``now() - interval``).

        Returns:
            The matching row count (0 when nothing matches).
        """
        with Session(self._engine) as session:
            stmt = sm_select(func.count()).select_from(AnomalyReport)
            if agent_id is not None:
                stmt = stmt.where(AnomalyReport.agent_id == agent_id)
            if tree_root_instance_id is not None:
                stmt = stmt.where(
                    AnomalyReport.tree_root_instance_id == tree_root_instance_id
                )
            if outcome is not None:
                stmt = stmt.where(AnomalyReport.outcome == outcome)
            if severity is not None:
                stmt = stmt.where(AnomalyReport.severity == severity)
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
        ``idx_anomaly_clusters_key_window``. The unit-of-work uses the
        same predicate internally (``_select_candidate``); this reader
        returns the ORM row (the phase-3 read tools' shape).

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
                sm_select(AnomalyCluster)
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

    def select_oldest_report_ids(
        self,
        *,
        tree_root_instance_id: str | None = None,
        limit: int,
    ) -> list[str]:
        """The OLDEST-FIRST id batch (the trims' selection).

        ``ORDER BY created_at ASC, id ASC LIMIT :n`` — the deterministic
        oldest-first order every shed path uses (the live per-tree
        shed-then-accept, the global capacity trim, the per-tree trim).
        """
        with Session(self._engine) as session:
            stmt = (
                select(AnomalyReport.id)
                .order_by(AnomalyReport.created_at.asc())
                .order_by(AnomalyReport.id.asc())
                .limit(max(1, int(limit)))
            )
            if tree_root_instance_id is not None:
                stmt = stmt.where(
                    AnomalyReport.tree_root_instance_id == tree_root_instance_id
                )
            rows = session.execute(stmt).all()
            return [str(row[0]) for row in rows]

    # ── the AM-12e deterministic top description ──────────────────────

    def top_description_for_cluster(
        self, cluster_id: str, *, max_chars: int = 1500
    ) -> str | None:
        """The board body's ``top_description`` (AM-12e).

        The member with the HIGHEST severity, tie-break ``created_at
        ASC, id ASC`` — one indexed seek over the cluster's members.
        """
        with Session(self._engine) as session:
            rank = case(
                (AnomalyReport.severity == SEVERITY_CRITICAL, 3),
                (AnomalyReport.severity == SEVERITY_MAJOR, 2),
                else_=1,
            )
            row = session.execute(
                select(AnomalyReport.description)
                .where(AnomalyReport.cluster_id == cluster_id)
                .where(AnomalyReport.outcome == OUTCOME_ACCEPTED)
                .order_by(rank.desc(), AnomalyReport.created_at.asc())
                .order_by(AnomalyReport.id.asc())
                .limit(1)
            ).first()
            if row is None:
                return None
            return str(row[0])[:max(1, int(max_chars))]

    # ── the AM-13 board ceilings (join anomaly_clusters → board_items) ─

    def count_recent_board_items(
        self,
        *,
        tree_root_instance_id: str | None = None,
        since: str,
    ) -> int:
        """COUNT anomaly-linked board items created at/after ``since``.

        Every anomaly board item is linked from its cluster
        (``anomaly_clusters.board_item_id`` — including the AM-13
        suppression fallback, which REPOINTED the cluster at the
        target item), so the join is exact for both the per-tree and
        the global ceiling. The board's ``created_at`` is the same
        FIXED-WIDTH shape as the anomaly stamps, so the lexicographic
        compare against the Python-bound cutoff is chronological.
        """
        with Session(self._engine) as session:
            stmt = (
                select(func.count(func.distinct(BoardItem.id)))
                .select_from(BoardItem)
                .join(
                    AnomalyCluster,
                    AnomalyCluster.board_item_id == BoardItem.id,
                )
                .where(BoardItem.created_at >= since)
            )
            if tree_root_instance_id is not None:
                stmt = stmt.where(
                    AnomalyCluster.tree_root_instance_id == tree_root_instance_id
                )
            return int(session.execute(stmt).scalars().one())

    def most_recent_board_item_for_tree(
        self, tree_root_instance_id: str | None = None
    ) -> str | None:
        """The most recent anomaly board item id (newest-first).

        The AM-13 suppression target — the item a ceiling-blocked
        critical APPENDS to instead of creating a new one. With a tree
        root: the TREE's most recent anomaly item; without: the most
        recent anomaly item anywhere (the GLOBAL-ceiling fallback — a
        global flood must not mint fresh items just because each tree
        is on its first).
        """
        with Session(self._engine) as session:
            stmt = (
                select(BoardItem.id)
                .join(
                    AnomalyCluster,
                    AnomalyCluster.board_item_id == BoardItem.id,
                )
                .order_by(BoardItem.created_at.desc())
                .order_by(BoardItem.id.desc())
                .limit(1)
            )
            if tree_root_instance_id is not None:
                stmt = stmt.where(
                    AnomalyCluster.tree_root_instance_id == tree_root_instance_id
                )
            row = session.execute(stmt).first()
            if row is None:
                return None
            return str(row[0])

    def resolved_clusters_with_open_board_items(
        self,
        resolve_cutoff: str,
        *,
        limit: int = 200,
    ) -> list[tuple[str, str]]:
        """AM-7 — ``(cluster_id, board_item_id)`` pairs whose cluster has
        RESOLVED (``last_report_at < cutoff``) while the board item is
        not yet ``done``.

        A BOARD-row write only — the cluster row itself is never
        touched (R5 forbids cluster status).
        """
        with Session(self._engine) as session:
            rows = session.execute(
                select(AnomalyCluster.id, AnomalyCluster.board_item_id)
                .where(AnomalyCluster.board_item_id.is_not(None))
                .where(AnomalyCluster.last_report_at < resolve_cutoff)
                .join(BoardItem, BoardItem.id == AnomalyCluster.board_item_id)
                .where(BoardItem.status != "done")
                .limit(max(1, int(limit)))
            ).all()
            return [(str(row[0]), str(row[1])) for row in rows]

    # ── retention + trims (R6 / AM-6 / AM-9 — phase 2) ────────────────

    def sweep_reports_older_than(self, cutoff: str, *, limit: int) -> int:
        """DELETE one bounded batch of reports older than ``cutoff``.

        The batched retention DELETE — the ids are selected oldest-first,
        deleted AND shed (the per-cluster decrement + the zero-count
        cluster delete) in ONE transaction (AM-6 + E5). The LIMIT keeps
        the delete bounded; the caller loops.
        """
        with Session(self._engine) as session:
            rows = session.execute(
                select(AnomalyReport.id)
                .where(AnomalyReport.created_at < cutoff)
                .order_by(AnomalyReport.created_at.asc())
                .order_by(AnomalyReport.id.asc())
                .limit(max(1, int(limit)))
            ).all()
            ids = [str(row[0]) for row in rows]
            if not ids:
                return 0
            shed_members(session, ids)
            session.commit()
            return len(ids)

    def trim_to_global_capacity(
        self,
        *,
        max_rows: int,
        batch: int,
        per_run_cap: int,
    ) -> int:
        """AM-9 — the GLOBAL oldest-first capacity trim.

        Deletes TOTAL rows (every outcome — the F-1 single TOTAL-row
        bound) oldest-first until the table is back at ``max_rows``;
        bounded by ``per_run_cap`` per call so a single tick stays
        cheap. Every batch goes through the shared :func:`shed_members`.
        """
        deleted_total = 0
        while deleted_total < per_run_cap:
            total = self.count_reports(outcome=None)
            if total <= int(max_rows):
                break
            excess = total - int(max_rows)
            take = min(int(batch), excess, per_run_cap - deleted_total)
            if take <= 0:
                break
            ids = self.select_oldest_report_ids(limit=take)
            if not ids:
                break
            with Session(self._engine) as session:
                shed_members(session, ids)
                session.commit()
            deleted_total += len(ids)
        return deleted_total

    def tree_roots_above(self, max_rows_per_tree: int) -> list[str]:
        """The tree roots whose TOTAL row count exceeds the per-tree cap."""
        with Session(self._engine) as session:
            rows = session.execute(
                select(AnomalyReport.tree_root_instance_id)
                .where(AnomalyReport.tree_root_instance_id.is_not(None))
                .group_by(AnomalyReport.tree_root_instance_id)
                .having(func.count() > int(max_rows_per_tree))
            ).all()
            return [str(row[0]) for row in rows]

    def trim_tree_to_capacity(
        self,
        tree_root_instance_id: str,
        *,
        max_rows_per_tree: int,
        batch: int,
    ) -> int:
        """C3(ii) — the per-tree oldest-first trim (the defense leg).

        Sheds the tree's oldest rows until it is back at the cap. The
        per-tree cap NEVER refuses — the channel stays live; the
        durable critical record is the BOARD item, which the trim does
        not remove.
        """
        deleted_total = 0
        while True:
            total = self.count_reports(
                tree_root_instance_id=tree_root_instance_id, outcome=None
            )
            if total <= int(max_rows_per_tree):
                break
            excess = total - int(max_rows_per_tree)
            take = min(int(batch), excess)
            if take <= 0:
                break
            ids = self.select_oldest_report_ids(
                tree_root_instance_id=tree_root_instance_id, limit=take
            )
            if not ids:
                break
            with Session(self._engine) as session:
                shed_members(session, ids)
                session.commit()
            deleted_total += len(ids)
        return deleted_total

    def trim_trees_to_capacity(
        self,
        *,
        max_rows_per_tree: int,
        batch: int,
    ) -> int:
        """Every over-cap tree gets ONE ``trim_tree_to_capacity`` pass."""
        deleted_total = 0
        for root in self.tree_roots_above(max_rows_per_tree):
            deleted_total += self.trim_tree_to_capacity(
                root,
                max_rows_per_tree=max_rows_per_tree,
                batch=batch,
            )
        return deleted_total

    def terminalize_orphan_accepted(
        self,
        cutoff: str,
        *,
        limit: int,
    ) -> int:
        """AM-5b — TERMINALIZE orphan ``accepted``+NULL rows to ``unclustered``.

        A row that stands as ``outcome='accepted' AND cluster_id IS
        NULL`` is an ORPHAN (a crash window between the INSERT and the
        binding, or a legacy live failure). The sweep terminalizes it
        to the NEW outcome token ``unclustered`` — NEVER reassigns it
        (post-hoc reassignment is retroactive identity instability,
        exactly what R5 rejects). Age-gated by the caller's cutoff and
        bounded by ``limit``.
        """
        with Session(self._engine) as session:
            rows = session.execute(
                select(AnomalyReport.id)
                .where(AnomalyReport.outcome == OUTCOME_ACCEPTED)
                .where(AnomalyReport.cluster_id.is_(None))
                .where(AnomalyReport.created_at < cutoff)
                .limit(max(1, int(limit)))
            ).all()
            ids = [str(row[0]) for row in rows]
            if not ids:
                return 0
            session.execute(
                update(AnomalyReport)
                .where(AnomalyReport.id.in_(ids))
                .values(outcome=OUTCOME_UNCLUSTERED)
            )
            session.commit()
            return len(ids)

    def delete_clusters_older_than(self, cutoff: str, *, limit: int) -> int:
        """DELETE up to ``limit`` clusters whose last report is older
        than ``cutoff`` (swept only AFTER their member reports)."""
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

    def count_accepted_members(self, cluster_id: str) -> int:
        """COUNT the cluster's ``accepted`` members — the AM-6 invariant read."""
        with Session(self._engine) as session:
            return int(
                session.exec(
                    sm_select(func.count())
                    .select_from(AnomalyReport)
                    .where(AnomalyReport.cluster_id == cluster_id)
                    .where(AnomalyReport.outcome == OUTCOME_ACCEPTED)
                ).one()
            )

    # ── the phase-3 READ queries (the read half's persistence) ────────
    #
    # The SERVICE owns the projection (ONE projector, two transports —
    # the nest-report-read tools and the /api/nest-reports route); this
    # class owns the bounded, paginated persistence reads.  Every
    # cutoff is PYTHON-BOUND (AM-4); every page is LIMIT/OFFSET-bounded
    # with an explicit ``more_available`` probe row (LIMIT n+1).

    def count_cluster_groups(
        self,
        *,
        window_cutoff: str | None = None,
        severity: str | None = None,
        category: str | None = None,
    ) -> int:
        """COUNT the ``(tree_root, category)`` GROUPS the filters match.

        The bound block's ``matched_count`` (the AM-6c grouped read).
        """
        with Session(self._engine) as session:
            base = select(
                AnomalyCluster.tree_root_instance_id,
                AnomalyCluster.category,
            )
            if window_cutoff is not None:
                base = base.where(AnomalyCluster.last_report_at >= window_cutoff)
            if severity is not None:
                base = base.where(AnomalyCluster.max_severity == severity)
            if category is not None:
                base = base.where(AnomalyCluster.category == category)
            grouped = base.group_by(
                AnomalyCluster.tree_root_instance_id,
                AnomalyCluster.category,
            ).subquery()
            return int(
                session.execute(
                    select(func.count()).select_from(grouped)
                ).scalar_one()
            )

    def page_cluster_groups(
        self,
        *,
        window_cutoff: str | None = None,
        severity: str | None = None,
        category: str | None = None,
        limit: int,
        offset: int = 0,
    ) -> list[tuple[str, str, int, int, str, str]]:
        """ONE PAGE of the AM-6c GROUPS, freshest-first.

        Returns ``(tree_root_instance_id, category, occurrence_count,
        report_count_sum, newest_last_report_at, peak_severity)`` rows.
        The GROUP key is ``(tree_root_instance_id, category)``; the
        peak severity is ``MAX(max_severity)`` — the closed 3-tier
        set's lexicographic order IS the rank order
        (``critical > major > minor``), so the SQL MAX is the
        peak-of-peaks (AM-7).  Callers pass ``limit + 1`` and read the
        surplus row as the ``more_available`` probe.
        """
        with Session(self._engine) as session:
            stmt = select(
                AnomalyCluster.tree_root_instance_id,
                AnomalyCluster.category,
                func.count().label("occurrence_count"),
                func.coalesce(func.sum(AnomalyCluster.report_count), 0).label(
                    "report_count"
                ),
                func.max(AnomalyCluster.last_report_at).label("last_report_at"),
                func.max(AnomalyCluster.max_severity).label("peak_severity"),
            ).group_by(
                AnomalyCluster.tree_root_instance_id,
                AnomalyCluster.category,
            )
            if window_cutoff is not None:
                stmt = stmt.where(AnomalyCluster.last_report_at >= window_cutoff)
            if severity is not None:
                stmt = stmt.where(AnomalyCluster.max_severity == severity)
            if category is not None:
                stmt = stmt.where(AnomalyCluster.category == category)
            stmt = stmt.order_by(
                func.max(AnomalyCluster.last_report_at).desc(),
                AnomalyCluster.tree_root_instance_id,
                AnomalyCluster.category,
            ).limit(max(1, int(limit))).offset(max(0, int(offset)))
            return [
                (
                    str(row[0]),
                    str(row[1]),
                    int(row[2]),
                    int(row[3]),
                    str(row[4]),
                    str(row[5]),
                )
                for row in session.execute(stmt).all()
            ]

    def cluster_ids_for_group(
        self,
        *,
        tree_root_instance_id: str,
        category: str,
        limit: int,
    ) -> list[tuple[str, str | None]]:
        """The group's member cluster ids + board row ids, freshest-first.

        Bounded by ``limit`` (the plan's "member ``cluster_ids`` bounded
        by the same page limit").  Served by
        ``idx_anomaly_clusters_key_window``.
        """
        with Session(self._engine) as session:
            stmt = (
                select(
                    AnomalyCluster.id,
                    AnomalyCluster.board_item_id,
                )
                .where(AnomalyCluster.tree_root_instance_id == tree_root_instance_id)
                .where(AnomalyCluster.category == category)
                .order_by(AnomalyCluster.last_report_at.desc())
                .limit(max(1, int(limit)))
            )
            return [
                (str(row[0]), (str(row[1]) if row[1] is not None else None))
                for row in session.execute(stmt).all()
            ]

    def get_cluster(self, cluster_id: str) -> dict[str, Any] | None:
        """One cluster's scalar fields by id, or ``None``.

        PLAIN DATA, not an ORM row: the row is captured INSIDE the
        session (a post-close attribute read raises
        ``DetachedInstanceError`` — the ``insert_report`` lesson) and the
        caller crosses an offload boundary, where an ORM instance
        would be detached anyway.
        """
        with Session(self._engine) as session:
            row = session.execute(
                select(
                    AnomalyCluster.id,
                    AnomalyCluster.category,
                    AnomalyCluster.max_severity,
                    AnomalyCluster.report_count,
                    AnomalyCluster.tree_root_instance_id,
                    AnomalyCluster.first_report_at,
                    AnomalyCluster.last_report_at,
                    AnomalyCluster.board_item_id,
                ).where(AnomalyCluster.id == cluster_id)
            ).first()
            if row is None:
                return None
            return {
                "id": str(row[0]),
                "category": str(row[1]),
                "max_severity": str(row[2]),
                "report_count": int(row[3]),
                "tree_root_instance_id": str(row[4]),
                "first_report_at": str(row[5]),
                "last_report_at": str(row[6]),
                "board_item_id": (str(row[7]) if row[7] is not None else None),
            }

    def list_cluster_reports(
        self,
        cluster_id: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """The cluster's member reports, FRESHEST-FIRST, bounded.

        PLAIN DATA (the same reason as :meth:`get_cluster`).  Callers
        pass ``limit + 1`` and read the surplus row as the
        ``more_available`` probe.  Only ``accepted`` rows carry a
        ``cluster_id`` (AM-5), so the cluster filter is the honest
        member set — no outcome filter is needed.
        """
        with Session(self._engine) as session:
            stmt = (
                select(
                    AnomalyReport.id,
                    AnomalyReport.agent_id,
                    AnomalyReport.instance_id,
                    AnomalyReport.created_at,
                    AnomalyReport.category,
                    AnomalyReport.category_raw,
                    AnomalyReport.severity,
                    AnomalyReport.description,
                    AnomalyReport.outcome,
                    AnomalyReport.excerpt,
                )
                .where(AnomalyReport.cluster_id == cluster_id)
                .order_by(
                    AnomalyReport.created_at.desc(),
                    AnomalyReport.id,
                )
                .limit(max(1, int(limit)))
            )
            return [
                {
                    "report_id": str(row[0]),
                    "agent_id": str(row[1]),
                    "instance_id": str(row[2]),
                    "created_at": str(row[3]),
                    "category": str(row[4]),
                    "category_raw": str(row[5]),
                    "severity": str(row[6]),
                    "description": str(row[7]),
                    "outcome": str(row[8]),
                    "excerpt": (str(row[9]) if row[9] is not None else None),
                }
                for row in session.execute(stmt).all()
            ]


__all__ = [
    "AnomalyRepository",
    "ClusterAssignment",
    "SEVERITY_RANK",
    "cluster_key_hash",
    "max_severity_rollup",
    "shed_members",
]
