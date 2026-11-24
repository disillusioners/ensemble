"""The POK repository — engine-passed CRUD over the two POK tables.

Layer discipline (the memoir precedent): this module NEVER builds an
engine and NEVER reads an ambient ``POSTGRES_*`` variable — the
repository is a thin wrapper around the CALLER'S engine
(``manager._engine``). Tests use a throwaway in-memory SQLite engine;
no ``InstanceManager`` is ever constructed.

What lives here (mechanics only, no policy):
* row CRUD (insert / get / list) and the SUPERSIDE TRANSACTION
  (flip prior active → superseded + insert successor, one commit —
  ``uq_pok_active_slug`` makes dual-active structurally impossible,
  so the txn is the only safe ordering);
* the curator flip (pinned / disputed / archived / audience /
  audience_agents / source_tier — the tier flip is Phase 2, trust-tier
  addendum T-3) with the forward-only archived guard;
* access/verify counters (query-path bookkeeping);
* the WRITE-GATE LEDGER surface (append-only + read-only-cheap
  queries — the decision substrate for the POK write gate, pok-write-gate
  Phase 1 T1.3/T1.7; there is deliberately no update/delete method);
* the STANDING-DEFECT RESIDUAL rows (pok-write-gate Phase 5 T5.6) — the
  ledger rows that record the disposition of the subsystem's two
  standing defects (the same append-only surface, one row per defect);
* the embeddings-companion CRUD (upsert / map / delete).

Policy (fence, budgets, caps, audience admission, refusal taxonomy)
lives in the service layer — ``daemon/services/pok_service.py`` and
``daemon/services/pok_injection.py``. NO FOREIGN KEY anywhere (§4.1):
``project_id`` / ``superseded_by_id`` are plain TEXT; the supersede
txn maintains the lineage wire.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from sqlmodel import Session, select

from daemon.repositories.pok.models import (
    POKWriteGateLedger,
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
    pok_family_key,
)

__all__ = [
    "POKRepository",
    "POK_STANDING_DEFECTS",
    "POK_STANDING_DEFECT_GATE_PREFIX",
    "UNSET",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Unset:
    """Sentinel for 'leave audience_agents unchanged' in curate()."""

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "UNSET"


UNSET = _Unset()


#: The STANDING DEFECTS of the POK write-gate subsystem and their
#: DISPOSITIONS (pok-write-gate Phase 5, T5.6).
#:
#: Each member is ``(defect, disposition, disclosure)``:
#:
#: * ``record_verification`` — ``wired-via-gate-path``. The metric has
#:   NO writer today (zero callers ⇒ ``verify_count`` structurally 0
#:   on every row). Addendum-3 item 7 REVERSED the earlier
#:   retire-with-disclosure ruling: the gate path (Wave 2 Phase 7/8)
#:   becomes the FIRST REAL WRITER — its verdicts, merges, refusals and
#:   admissions stamp the metric. NOT retired.
#: * ``vector-leg`` — ``retired-with-disclosure``. The embeddings
#:   companion is dead: the embeddings table holds 0 rows (2026-11-23
#:   census), the mint hook ``ensure_embedding`` has zero callers, and
#:   the query seam never passes a ``query_embedding``. Documented, not
#:   wired (the operator-decided follow-up P2 may still revive it).
#:
#: The vocabulary lives HERE (not in ``models.py``) because these are
#: DISPOSITIONS of the subsystem, not a store-wide column vocabulary —
#: the ledger's own vocabularies (``POK_LEDGER_PHASES`` /
#: ``POK_LEDGER_VERDICTS`` / ``POK_ARCHIVE_BASES``) stay single-sourced
#: in ``models.py`` and are NOT forked.
POK_STANDING_DEFECTS: tuple[tuple[str, str, str], ...] = (
    (
        "record_verification",
        "wired-via-gate-path",
        "verify_count has no writer today (zero callers); the gate path "
        "(Wave 2 Phase 7/8) becomes the first real writer — verdicts, "
        "merges, refusals and admissions stamp the metric. Not retired "
        "(addendum-3 item 7 reversed the retire-with-disclosure ruling).",
    ),
    (
        "vector-leg",
        "retired-with-disclosure",
        "The embeddings companion is dead: 0 embedding rows (2026-11-23 "
        "census), the mint hook ensure_embedding has zero callers, and the "
        "query seam never passes a query_embedding. Documented, not wired "
        "(follow-up P2 may still identify a caller).",
    ),
)

#: The ``gate_id`` prefix of a standing-defect residual row. The
#: residual rows are NOT gate events, but they live in the same
#: APPEND-ONLY ledger — the audit substrate — and the prefix is what
#: tells a reader (and :meth:`POKRepository.standing_defect_rows`) that
#: a row is a subsystem DISPOSITION rather than an admission decision.
POK_STANDING_DEFECT_GATE_PREFIX = "standing-defect:"

#: Slug→row resolution precedence (mechanics, not policy): the row that
#: OWNS a slug. The ACTIVE row owns it; an ARCHIVED row owns it when no
#: active row exists (terminal, forward-only); a SUPERSEDED row is an
#: ANCESTOR and never owns its slug while a descendant exists.
#:
#: The precedence is applied in PYTHON over the full slug match set —
#: NOT by an ORDER BY the database is free to ignore — so the owner is
#: PLAN-INDEPENDENT (PostgreSQL and SQLite choose different indexes for
#: the same WHERE clause; see the ``resolve_slug_row`` docstring).
#:
#: Caveat: the rank above is the *owner* scope. A superseded row IS
#: still returned as a last resort when no active or archived row
#: remains — a dead ancestor, refused by the service layer (see
#: ``resolve_slug_row`` scope="owner").
_SLUG_OWNER_STATUS_RANK: dict[str, int] = {
    "active": 0,
    "archived": 1,
    "superseded": 2,
}


class POKRepository:
    """CRUD + the supersede transaction over the caller's engine."""

    def __init__(self, engine: "Engine") -> None:
        self._engine = engine

    # ── reads ────────────────────────────────────────────────────────────

    def get(self, entry_id: str) -> ProjectOperatingKnowledge | None:
        with Session(self._engine) as session:
            return session.get(ProjectOperatingKnowledge, entry_id)

    def get_by_slug(
        self, project_id: str, slug: str, *, status: str | None = "active"
    ) -> ProjectOperatingKnowledge | None:
        """The first row matching (project, slug[, status]).

        ⚠️ THE UNFILTERED FORM (``status=None``) IS **NOT** A SLUG
        RESOLVER: with a lineage present (a superseded ancestor + its
        active successor share one slug) the row returned is whichever
        one the DATABASE PLAN yields first — PostgreSQL returns the
        ROOT ancestor, SQLite returns the active row. MUTATION PATHS
        MUST USE :meth:`resolve_slug_row`; this method is an
        inspection helper.
        """
        stmt = select(ProjectOperatingKnowledge).where(
            ProjectOperatingKnowledge.project_id == project_id,
            ProjectOperatingKnowledge.slug == slug,
        )
        if status is not None:
            stmt = stmt.where(ProjectOperatingKnowledge.status == status)
        with Session(self._engine) as session:
            return session.exec(stmt).first()

    def resolve_slug_row(
        self,
        project_id: str,
        slug: str,
        *,
        scope: str = "owner",
    ) -> ProjectOperatingKnowledge | None:
        """Resolve a slug to ITS row — DETERMINISTIC, plan-independent.

        One slug is carried by a WHOLE LINEAGE: every supersede
        inserts a successor with the SAME slug, so a slug can map to
        several rows (the superseded ancestors + the active head). A
        single-row fetch over that set picks whichever row the
        database plan yields first — PostgreSQL's plan for the
        unfiltered WHERE returned the ROOT ancestor, so the curation
        path refused every successor-generation row with
        ``pok_not_found`` (measured: PG ``Index Scan using
        idx_pok_project_id`` → heap order → root first). The unordered
        lookup MAY return either row — it is index-choice/plan-dependent
        (e.g. idx_pok_project_id vs idx_pok_project_status) — on ANY
        engine, including SQLite, which is why the SQLite unit pack
        never showed the bug.

        The owner is therefore resolved HERE, in Python, over the full
        match set:

        * ``scope="owner"`` (default) → the row that OWNS the slug:
          the active row; else the most recent archived row; else the
          most recent superseded row (a dead ancestor — the service
          layer refuses to curate it).
        * ``scope="active"`` → the ACTIVE row only. The write path's
          prior: an archived row is TERMINAL (archived is
          forward-only) and a superseded row is an ancestor — neither
          is supersede-able, so neither may be picked there.

        Ties inside one status are broken by the most recent row
        (``created_at`` then ``id``) — the terminal row of that status
        owns the slug.

        Multi-archived ambiguity: when a slug's only rows are ARCHIVED
        (archive → upsert → archive), the MOST-RECENT archived row owns
        the slug; older archived rows are unreachable by slug (terminal,
        never injected — no correctness exposure).
        """
        if scope not in ("owner", "active"):
            raise ValueError(f"unknown slug-resolution scope {scope!r}")
        stmt = select(ProjectOperatingKnowledge).where(
            ProjectOperatingKnowledge.project_id == project_id,
            ProjectOperatingKnowledge.slug == slug,
        )
        if scope == "active":
            stmt = stmt.where(ProjectOperatingKnowledge.status == "active")
        with Session(self._engine) as session:
            rows = list(session.exec(stmt).all())
        if not rows:
            return None
        if scope == "active":
            # Structurally ONE active row per (project, slug) — the
            # uq_pok_active_slug partial unique index. The max() is a
            # deterministic tie-break should the index ever be absent.
            return max(rows, key=lambda r: (r.created_at or "", r.id or ""))
        best_rank = min(
            _SLUG_OWNER_STATUS_RANK.get(r.status, len(_SLUG_OWNER_STATUS_RANK))
            for r in rows
        )
        same = [
            r
            for r in rows
            if _SLUG_OWNER_STATUS_RANK.get(r.status, len(_SLUG_OWNER_STATUS_RANK))
            == best_rank
        ]
        # (created_at, id) DESC invariant: all writers stamp created_at
        # via a uniform single-writer format (_utc_now_iso()); a uuid4 id
        # breaks an exact timestamp tie, and a future seeder writing "Z"
        # vs "+00:00" would order differently.
        return max(same, key=lambda r: (r.created_at or "", r.id or ""))

    def list_entries(
        self,
        project_id: str,
        *,
        statuses: Sequence[str] = ("active",),
        include_superseded_of: bool = False,
    ) -> list[ProjectOperatingKnowledge]:
        """All entries of the project with the given statuses, slug order.

        UNIVERSAL READ (ER-1.4): callers NEVER pass an audience filter
        here — retrieval is audience-blind by invariant; the audience
        dimension gates ACTIVE injection only.
        """
        stmt = select(ProjectOperatingKnowledge).where(
            ProjectOperatingKnowledge.project_id == project_id,
            ProjectOperatingKnowledge.status.in_(list(statuses)),
        )
        with Session(self._engine) as session:
            rows = list(session.exec(stmt).all())
        rows.sort(key=lambda r: r.slug)
        return rows

    def count_active(self, project_id: str) -> int:
        stmt = (
            select(ProjectOperatingKnowledge)
            .where(
                ProjectOperatingKnowledge.project_id == project_id,
                ProjectOperatingKnowledge.status == "active",
            )
        )
        with Session(self._engine) as session:
            return len(list(session.exec(stmt).all()))

    def count_active_in_family(self, project_id: str, family: str) -> int:
        """Active rows whose FAMILY KEY EQUALS ``family`` (§4.5).

        STRICT MEMBERSHIP (was a PREFIX SUPERSET — the doctrine is OVERTURNED by
        commission pok-family-count-strict, 2026-11-24): every active row's
        canonical family key is derived with ``pok_family_key`` and compared
        for EQUALITY.

        Consequences (MEASURED on the 2026-11-23 census, 499 active rows): a
        LONGER family never inflates a SHORTER one (``skill-e6-*`` no longer
        counts toward ``skill``); an UNNUMBERED slug (``verify-something-
        unnumbered``) has NO family and never counts; a family's count is the
        number of rows it actually OWNS. The bound (POK_FAMILY_MAX) is
        unchanged — only the COUNT's meaning is corrected.
        """
        stmt = select(ProjectOperatingKnowledge).where(
            ProjectOperatingKnowledge.project_id == project_id,
            ProjectOperatingKnowledge.status == "active",
        )
        with Session(self._engine) as session:
            rows = list(session.exec(stmt).all())
        return sum(1 for r in rows if pok_family_key(r.slug) == family)

    def lineage(self, entry_id: str) -> list[ProjectOperatingKnowledge]:
        """The supersede chain for one slug lineage, oldest first.

        Walks ``superseded_by_id`` forward from the oldest row that
        reaches ``entry_id`` (a row whose superseded_by_id == entry_id
        is its successor). Plain-TEXT wire (no FK) walked by hand.
        """
        with Session(self._engine) as session:
            anchor = session.get(ProjectOperatingKnowledge, entry_id)
            if anchor is None:
                return []
            rows = list(
                session.exec(
                    select(ProjectOperatingKnowledge).where(
                        ProjectOperatingKnowledge.project_id == anchor.project_id,
                        ProjectOperatingKnowledge.slug == anchor.slug,
                    )
                ).all()
            )
        rows.sort(key=lambda r: (r.created_at, r.id))
        return rows

    # ── writes ───────────────────────────────────────────────────────────

    def insert(self, entry: ProjectOperatingKnowledge) -> ProjectOperatingKnowledge:
        with Session(self._engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def supersede_and_insert(
        self,
        prior: ProjectOperatingKnowledge,
        successor: ProjectOperatingKnowledge,
    ) -> "tuple[ProjectOperatingKnowledge, str]":
        """THE supersede transaction (§4.2): flip prior → superseded
        (with ``superseded_by_id`` = successor id) and insert the
        successor, ONE commit.

        Ordering is load-bearing: the prior row must leave the
        ``active`` set before the successor enters it, or
        ``uq_pok_active_slug`` fires. One commit = no window.
        ``pinned`` does NOT carry over (the successor is unpinned;
        re-pinning is an explicit curator act via pok_curate).

        COUNTER CARRY-OVER (pok-write-gate Phase 2, T2.7 — design-
        amendments-v2 §13.4 defect #2): the successor INHERITS the
        prior row's counters (``verify_count`` + ``access_count``) and
        the prior ``last_verified`` / ``last_accessed_at`` stamps
        instead of defaulting to 0. Rationale: every merge is a
        supersede, and a supersede that silently resets the counters
        breaks the J trusted-metric chain (gate-stamped verify_count
        IS the trusted metric) — the chain would restart at every
        merge, making the metric self-defending against exactly the
        rows the librarian most needs to judge.

        The counters ADD to whatever the successor already carries
        (``prior + successor``) rather than overwriting it: the
        successor is normally a fresh row with 0, so the sum is the
        inherited value; a caller that pre-set a counter keeps it.
        ``verify_count`` is the metric the librarian trusts
        (design-amendments-v2 §13.2 fork J); ``access_count`` rides
        along as LEGACY corroboration only.
        """
        prior_id = prior.id
        with Session(self._engine) as session:
            # The PERSISTED prior row is the authority for the
            # carry-over and for the status flip. Reading it here (not
            # trusting the caller's in-memory snapshot) matters twice
            # over: a snapshot taken before a ``record_verification``
            # bump would carry STALE counters, and merging that stale
            # snapshot back over the row would ZERO the lineage this
            # method exists to preserve.
            db_prior = session.get(ProjectOperatingKnowledge, prior_id)
            if db_prior is not None:
                # Counter carry-over — the successor inherits the
                # lineage's counters (T2.7). `last_verified` is carried
                # with them: the verify stamp IS the provenance of the
                # count.
                successor.verify_count = (db_prior.verify_count or 0) + (
                    successor.verify_count or 0
                )
                successor.access_count = (db_prior.access_count or 0) + (
                    successor.access_count or 0
                )
                if successor.last_verified is None:
                    successor.last_verified = db_prior.last_verified
                if successor.last_accessed_at is None:
                    successor.last_accessed_at = db_prior.last_accessed_at
                db_prior.status = "superseded"
                db_prior.superseded_by_id = successor.id
                db_prior.pinned = False
                db_prior.updated_at = _utc_now_iso()
                session.add(db_prior)
            else:
                # The prior was never persisted — fall back to the
                # caller's snapshot (the historical behaviour).
                session.add(prior)
            # Keep the caller's object observably consistent with the
            # row (the historical contract of this method).
            prior.status = "superseded"
            prior.superseded_by_id = successor.id
            prior.pinned = False
            prior.updated_at = _utc_now_iso()
            session.add(successor)
            session.commit()
            session.refresh(successor)
        return successor, prior_id

    def curate(
        self,
        entry_id: str,
        *,
        pinned: bool | None = None,
        disputed: bool | None = None,
        archived: bool | None = None,
        audience: str | None = None,
        audience_agents: Any = UNSET,
        source_tier: Any = UNSET,
    ) -> ProjectOperatingKnowledge | None:
        """Apply curator flips (pok_curate).

        ``archived=True`` is FORWARD-ONLY (status moves to archived
        and never back — the un-archive refusal lives at the tool
        layer, the flip here just never writes a non-archived status).
        ``audience_agents`` uses the module ``UNSET`` sentinel to
        distinguish "leave unchanged" (default) from "clear back to
        NULL" (pass an empty list). A non-empty list is JSON-encoded
        at this boundary.
        ``source_tier`` (Phase 2 — trust-tier addendum T-3) uses the
        same UNSET sentinel; unlike ``audience_agents`` there is no
        clear-to-NULL form — the only writable values are the
        vocabulary members ('agent' | 'curated'), validated upstream.
        """
        with Session(self._engine) as session:
            row = session.get(ProjectOperatingKnowledge, entry_id)
            if row is None:
                return None
            if pinned is not None:
                row.pinned = bool(pinned)
            if disputed is not None:
                row.disputed = bool(disputed)
            if archived is True:
                row.status = "archived"
                row.pinned = False
            if audience is not None:
                row.audience = audience
            if audience_agents is not UNSET:
                row.audience_agents = (
                    json.dumps(list(audience_agents)) if audience_agents else None
                )
            if source_tier is not UNSET:
                row.source_tier = source_tier
            row.updated_at = _utc_now_iso()
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def bump_access(self, entry_ids: Iterable[str], *, now: str | None = None) -> int:
        """Query-path bookkeeping: access_count += 1, last_accessed_at."""
        ids = [i for i in entry_ids if i]
        if not ids:
            return 0
        stamp = now or _utc_now_iso()
        with Session(self._engine) as session:
            rows = list(
                session.exec(
                    select(ProjectOperatingKnowledge).where(
                        ProjectOperatingKnowledge.id.in_(ids)
                    )
                ).all()
            )
            for row in rows:
                row.access_count = (row.access_count or 0) + 1
                row.last_accessed_at = stamp
                session.add(row)
            session.commit()
            return len(rows)

    # ── write-gate ledger (pok-write-gate Phase 1, T1.3 + T1.7) ────────
    #
    # APPEND-ONLY. There is deliberately NO update and NO delete
    # method on this surface — the ledger is the audit substrate and a
    # mutable audit is not an audit. The only mutator is
    # :meth:`append_ledger`; everything else is a read.

    def append_ledger(
        self, entry: POKWriteGateLedger
    ) -> POKWriteGateLedger:
        """APPEND one ledger row (the only mutator on this surface).

        Read-only-cheap by construction: one INSERT, one commit, no
        read-modify-write cycle and no counter bump on any other row.
        The caller owns the CONTENT of the row (the gate owns the
        verdict row; the tool owns the execution row — the authority
        split, design-amendments-v2 §8).
        """
        with Session(self._engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def query_ledger(
        self,
        project_id: str,
        *,
        gate_id: str | None = None,
        phase: str | None = None,
        entry_id: str | None = None,
        source: str | None = None,
        limit: int | None = 100,
    ) -> list[POKWriteGateLedger]:
        """The ledger READ surface (T1.7) — read-only-cheap queries.

        Ordered oldest-first by ``(created_at, id)`` so a gate chain
        reads in the order it happened (the ASK chain's three rows are
        separated in time by the answer round-trip, so the ordering is
        total in practice; ``id`` breaks any residual tie).

        Every filter is OPTIONAL and composable — the common shapes are
        "the whole chain for one gate", "every retrieval for one row",
        "every retrieval from one source" and "every phase for one
        project".
        """
        stmt = select(POKWriteGateLedger).where(
            POKWriteGateLedger.project_id == project_id
        )
        if gate_id is not None:
            stmt = stmt.where(POKWriteGateLedger.gate_id == gate_id)
        if phase is not None:
            stmt = stmt.where(POKWriteGateLedger.phase == phase)
        if entry_id is not None:
            stmt = stmt.where(POKWriteGateLedger.entry_id == entry_id)
        if source is not None:
            stmt = stmt.where(POKWriteGateLedger.source == source)
        stmt = stmt.order_by(POKWriteGateLedger.created_at, POKWriteGateLedger.id)
        if limit is not None and limit >= 0:
            stmt = stmt.limit(limit)
        with Session(self._engine) as session:
            return list(session.exec(stmt).all())

    def ledger_gate_chain(
        self, project_id: str, gate_id: str
    ) -> list[POKWriteGateLedger]:
        """The FULL chain for one gate id, oldest-first.

        This is the shape the authority split produces (design-
        amendments-v2 §13.2 fork G): TWO rows for a plain
        ACCEPT/MERGE/REJECT chain (the gate's verdict row then the
        tool's execution row), THREE for an ASK chain
        (``ASK_MINTED`` → ``ASK_ANSWERED`` → ``ASK_EXECUTED``).
        """
        return self.query_ledger(project_id, gate_id=gate_id, limit=None)

    def count_ledger_phase(
        self, project_id: str, phase: str
    ) -> int:
        """How many ledger rows of ONE phase a project has.

        Counts a phase IN AGGREGATE: this method filters on
        ``project_id`` + ``phase`` only and takes NO ``source``
        argument, so it cannot tell query-driven retrievals from
        injection-driven ones. To disambiguate, use
        :meth:`query_ledger` with ``source=...`` (the ``source``
        column is what tells them apart).

        The librarian's metric read (Phase 10): the RETRIEVAL counts
        here SUPERSEDE ``access_count`` — they disambiguate
        query-vs-injection, which ``access_count`` alone cannot
        (design-amendments-v2 §13.2 fork J, amendment 1).

        ── RETRIEVAL EVENTS: DESIGNED, NOT WIRED (Wave 1) ─────────────
        No production caller records ``source="query"`` in Wave 1:
        the ``pok_query`` read path does not append a ledger
        RETRIEVAL row yet. The column lands up front so the read
        path can populate it in Wave 2 without a migration. Until
        then, any retrieval rows that exist carry
        ``source="injection"`` only.

        NOTE for Phase 10: an ADMISSION-phase count INCLUDES the
        standing-defect residual rows (they are ADMISSION-phase rows
        with a NULL verdict). Filter them out with
        :meth:`standing_defect_rows`, or read the verdict-bearing rows
        only — a residual disposition is not an admission decision.
        """
        stmt = select(POKWriteGateLedger).where(
            POKWriteGateLedger.project_id == project_id,
            POKWriteGateLedger.phase == phase,
        )
        with Session(self._engine) as session:
            return len(list(session.exec(stmt).all()))

    # ── standing-defect residual rows (pok-write-gate Phase 5, T5.6) ────

    def standing_defect_rows(
        self, project_id: str
    ) -> list[POKWriteGateLedger]:
        """The standing-defect RESIDUAL rows for one project (read).

        A residual row records the DISPOSITION of a standing defect of
        the write-gate subsystem (see :data:`POK_STANDING_DEFECTS`) —
        not an admission decision about a knowledge row. They are
        ordinary APPEND-ONLY ledger rows, so the ledger alone can
        answer "what was decided about the two standing defects?"
        without a cross-store join.
        """
        return [
            row
            for row in self.query_ledger(project_id, limit=None)
            if (row.gate_id or "").startswith(POK_STANDING_DEFECT_GATE_PREFIX)
        ]

    def record_standing_defects(
        self, project_id: str
    ) -> list[POKWriteGateLedger]:
        """APPEND the standing-defect RESIDUAL rows for one project.

        The minimal way to record the two standing-defect dispositions
        (pok-write-gate Phase 5, T5.6): ``record_verification`` is
        reserved for the gate path (Wave 2 Phase 7/8 wires the caller)
        and the vector leg is retired with disclosure.

        ENCODING (the landed ledger shape — NO schema fork):
          * ``phase = "ADMISSION"`` — the closed phase vocabulary's
            decision-record step. The residual rows are dispositions,
            not retrievals and not executions.
          * ``verdict = NULL`` — a disposition is NOT a gate outcome;
            the four verdicts (ACCEPT / MERGE / ASK / REJECT) are
            reserved for the gate's admission decisions.
          * ``gate_id = "standing-defect:<defect>"`` — the chain key
            that marks the row as a disposition.
          * ``reason`` — the disposition claim; ``judge_explanation``
            — the disclosure (why, with the citation).
          * ``basis = NULL`` — nothing was ARCHIVED (a disposition
            archives nothing); ``executed = False`` — no knowledge row
            was written.

        IDEMPOTENT PER DEFECT: a defect that already has a residual row
        for this project is skipped (the ledger is append-only, so a
        blind re-call would duplicate the audit trail). The rows that
        were appended (the NEW ones) are returned.
        """
        existing = {
            (row.gate_id or "").removeprefix(POK_STANDING_DEFECT_GATE_PREFIX)
            for row in self.standing_defect_rows(project_id)
        }
        appended: list[POKWriteGateLedger] = []
        for defect, disposition, disclosure in POK_STANDING_DEFECTS:
            if defect in existing:
                continue
            appended.append(
                self.append_ledger(
                    POKWriteGateLedger(
                        project_id=project_id,
                        gate_id=f"{POK_STANDING_DEFECT_GATE_PREFIX}{defect}",
                        phase="ADMISSION",
                        verdict=None,
                        reason=disposition,
                        judge_explanation=disclosure,
                        basis=None,
                        executed=False,
                    )
                )
            )
        return appended

    def record_verification(self, entry_id: str, *, now: str | None = None) -> bool:
        """verify_count += 1 + last_verified stamp (pok_verify phase-2
        path; the 30-day decay reads ``last_verified``).

        ── THE GATE-PATH CALLER IS DESIGNED, NOT WIRED ─────────────────
        (pok-write-gate Phase 5, T5.1 — the W1 half).

        This method still has ZERO callers: the write gate's judge
        service lands in WAVE 2 (Phase 7/8), and THAT service is the
        FIRST REAL WRITER. Its verdicts (ACCEPT / MERGE / ASK / REJECT),
        its merges, its refusals and its admissions stamp the verify
        metric HERE, so the librarian's next pass reads the stamps its
        own activity produced — the metrics are dead code until the
        gate exists, and the gate is what makes them live
        (design-amendments-v2 §6, §9 "close the loop").

        Until Wave 2 lands, ``verify_count`` is structurally 0 on every
        row. That ZERO IS NOT EVIDENCE — it is the absence of a
        writer, never a quality signal (see the IGNORED list below).

        WAVE 2 OWNS THE ACTUAL CALLS. This note owns the DESIGN: the
        seam, the stamp set, and what a stamp may and may not be used
        for — so the librarian's metric contract is settled before the
        caller exists, and the librarian is not re-litigated when it
        arrives. No call is wired here.

        ── THE TRUSTED METRIC SET (design-amendments-v2 §9 / §13.2 J) ──

        TRUST:
          * ``verify_count`` — but ONLY once it is GATE-STAMPED. The
            pre-stamp value (structurally 0) is IGNORED.
          * the CURATOR TIERS (``pinned`` / ``disputed`` /
            ``source_tier`` / ``audience``) — cold signals, read only.
          * the FAMILY (post the strict per-key count fix) — a family
            at the cap is a STRUCTURAL signal, never a per-row one.
          * VERDICT-DERIVED COUNTS — the librarian's own ledger output
            (ACCEPT / MERGE / ASK / REJECT counts per project).
          * TIMESTAMPS (``created_at`` / ``updated_at`` /
            ``last_verified``) — as PROVENANCE. They corroborate a
            finding; they NEVER trigger one (a NOW-FALSE row usually
            co-occurs with an ancient ``last_verified``, but staleness
            alone never fires).

        IGNORE:
          * ``access_count`` ALONE — retrieval-driven, not use-driven.
            The ledger's RETRIEVAL counts SUPERSEDE it once they
            exist, because they disambiguate query-vs-injection via
            the ``source`` column; ``access_count`` survives as legacy
            corroboration only.
          * AGE — never a consolidation basis, never a worthlessness
            signal.
          * the VECTOR / EMBEDDINGS leg — dead, retired with
            disclosure (below).
          * ``verify_count`` BEFORE gate stamping — structurally 0.
          * tier=0 as a signal — every tier is inert today.

        PER-ROW vs PER-STORE: per-ROW metrics (a verify stamp on one
        row, one row's curator tier) drive ROW decisions; per-STORE
        aggregates (refusal rate, verdict mix) are PASS TELEMETRY and
        never decide a row.

        ── THE VECTOR LEG — RETIRED WITH DISCLOSURE (Phase 5, T5.2) ──

        The ranking companion (``upsert_embedding`` / ``embeddings_map``
        / ``ensure_embedding``) is NOT wired and is NOT a metric:

          * the embeddings table is EMPTY (measured at the
            2026-11-23 census — the store has 0 embedding rows);
          * the mint hook ``ensure_embedding`` has ZERO callers
            (WIRED-BUT-DORMANT — the dormancy is marked at its
            definition in ``pok_service.py``);
          * the query seam NEVER passes a ``query_embedding`` — the
            only caller is the ``pok_query`` tool, which does not
            supply one; and the injection path never populates
            ``embeddings_map`` from the repository.

        So the selector degrades to BM25-only by design (AC14). This is
        DISCLOSED, not silently dropped: a caller may still identify
        one (the operator-decided follow-up P2). Until then the vector
        leg contributes NO signal — and, per the IGNORED list above, a
        future one may not be read as a freshness or a worth measure
        either.
        """
        stamp = now or _utc_now_iso()
        with Session(self._engine) as session:
            row = session.get(ProjectOperatingKnowledge, entry_id)
            if row is None:
                return False
            row.verify_count = (row.verify_count or 0) + 1
            row.last_verified = stamp
            row.updated_at = stamp
            session.add(row)
            session.commit()
            return True

    def stamp_last_verified(self, entry_id: str, *, now: str | None = None) -> bool:
        """Frozen A-8 (W3): stamp ``last_verified`` (no count bump) —
        the no-op echo path. Restarts the staleness clock so ``[stale Nd]``
        decay is reachable naturally (a row goes stale only if it is
        never re-echoed for POK_VERIFY_WINDOW_DAYS)."""
        stamp = now or _utc_now_iso()
        with Session(self._engine) as session:
            row = session.get(ProjectOperatingKnowledge, entry_id)
            if row is None:
                return False
            row.last_verified = stamp
            row.updated_at = stamp
            session.add(row)
            session.commit()
            return True

    def oldest_least_verified_active(
        self, project_id: str, *, exclude_ids: Sequence[str] = ()
    ) -> ProjectOperatingKnowledge | None:
        """The refusal-naming candidate (§4.2 admission): oldest active
        row, least verify_count — the archive-cure target the
        active-cap refusal names. Never the pinned rows (curator-owned).
        """
        stmt = select(ProjectOperatingKnowledge).where(
            ProjectOperatingKnowledge.project_id == project_id,
            ProjectOperatingKnowledge.status == "active",
            ProjectOperatingKnowledge.pinned == False,  # noqa: E712
        )
        if exclude_ids:
            stmt = stmt.where(
                ProjectOperatingKnowledge.id.not_in(list(exclude_ids))
            )
        with Session(self._engine) as session:
            rows = list(session.exec(stmt).all())
        if not rows:
            return None
        rows.sort(key=lambda r: (r.verify_count or 0, r.created_at, r.id))
        return rows[0]

    # ── embeddings companion (ranking-only) ─────────────────────────────

    def upsert_embedding(self, entry_id: str, vector: list[float]) -> None:
        with Session(self._engine) as session:
            existing = session.exec(
                select(ProjectOperatingKnowledgeEmbedding).where(
                    ProjectOperatingKnowledgeEmbedding.entry_id == entry_id
                )
            ).first()
            if existing is not None:
                existing.embedding = list(vector)
                existing.created_at = _utc_now_iso()
                session.add(existing)
            else:
                session.add(
                    ProjectOperatingKnowledgeEmbedding(
                        entry_id=entry_id, embedding=list(vector)
                    )
                )
            session.commit()

    def embeddings_map(self, project_id: str) -> dict[str, list[float]]:
        """``{entry_id: vector}`` for the project's entries (the
        selector's ``embeddings_map`` shape). Missing rows = BM25-only
        degradation for that entry (AC14 lag flag)."""
        stmt = select(ProjectOperatingKnowledgeEmbedding).join(
            ProjectOperatingKnowledge,
            ProjectOperatingKnowledge.id
            == ProjectOperatingKnowledgeEmbedding.entry_id,
        ).where(ProjectOperatingKnowledge.project_id == project_id)
        with Session(self._engine) as session:
            rows = list(session.exec(stmt).all())
        return {r.entry_id: list(r.embedding) for r in rows}

    def delete_embeddings_for(self, entry_ids: Iterable[str]) -> int:
        ids = [i for i in entry_ids if i]
        if not ids:
            return 0
        with Session(self._engine) as session:
            rows = list(
                session.exec(
                    select(ProjectOperatingKnowledgeEmbedding).where(
                        ProjectOperatingKnowledgeEmbedding.entry_id.in_(ids)
                    )
                ).all()
            )
            for row in rows:
                session.delete(row)
            session.commit()
            return len(rows)
