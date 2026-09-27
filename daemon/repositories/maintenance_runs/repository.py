"""Repository for the ``maintenance_runs`` audit table (AM-15).

Sync methods, engine-injected ctor (snapshot-repo pattern; callers
bridge via ``asyncio.to_thread`` — the project's standard pattern for
sync SQLModel/SQLAlchemy repos called from async code).

Two public guarantees:

* **Conditional INSERT** — :meth:`insert` returns ``False`` on the
  partial unique-index conflict (the DB-side claim AM-5). Caller
  raises 409 ``run_in_flight`` with the in-flight row's
  ``run_id``/``started_at`` nested under ``details`` (C-2).
* **PK collision retry** — :meth:`insert` retries once on the rare
  PK collision (different threads of execution minting the same hex8
  suffix in the same microsecond); on the second collision the
  IntegrityError propagates so the caller can log + surface as a
  transient 5xx.

The boot-sweep CAS (AM-7 / T8) lives in
``daemon/services/maintenance_boot_sweep.py`` and uses
:meth:`cas_running_to_interrupted`. The boot sweep is unconditional
(no age gate) — see the boot-sweep module docstring.
"""

from __future__ import annotations

import logging
import secrets
from typing import Optional

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from daemon.repositories.infra.types import JSONBType
from daemon.services.timestamps import now_utc_iso
from .models import MaintenanceRun

logger = logging.getLogger(__name__)


def _regenerate_hex_suffix(run_id: str) -> str:
    """Return a new ``run_id`` with the hex8 suffix regenerated.

    PK collision retry helper: ``ckpt-<prefix>-<hex8>`` →
    ``ckpt-<prefix>-<new_hex8>``. The date-time core is preserved
    (chronological ordering survives the retry).
    """
    head, _sep, _suffix = run_id.rpartition("-")
    return f"{head}-{secrets.token_hex(4)}"


class MaintenanceRunsRepository:
    """Sync CRUD + boot-sweep CAS for the ``maintenance_runs`` table."""

    def __init__(self, engine: Engine) -> None:
        """Initialize the repository.

        Args:
            engine: Shared SQLAlchemy engine (created once at the
                manager level and passed to every repository, same
                pattern as snapshot_repo / skill_repo).
        """
        self.engine = engine

    # ── Writes ────────────────────────────────────────────────────────

    def insert(self, run: MaintenanceRun) -> bool:
        """Insert a new ``running`` row. Returns ``False`` on conflict.

        Conflict path (AM-5): a row with the same ``section`` and
        ``status='running'`` already exists → the partial unique
        index rejects this INSERT. The caller (MaintenanceApiService)
        treats ``False`` as the 409 ``run_in_flight`` signal — no row
        is written for the refused caller.

        PK collision retry (AM-8): ``IntegrityError`` on the PK (two
        callers minting the same ``run_id`` in the same microsecond)
        → regenerate the hex8 suffix via ``_regenerate_hex_suffix``
        and retry once. On a second collision the IntegrityError
        propagates (caller logs + surfaces as a transient 5xx).

        Constraint discrimination: both violations surface as
        ``IntegrityError``; the constraint is identified by NAME, not
        by fragile message substrings —

        * PG partial-index violation:
          ``duplicate key value violates unique constraint
          "uq_maintenance_runs_running_section"``
        * PG PK violation: ``... "maintenance_runs_pkey"``
        * SQLite PK: ``UNIQUE constraint failed: maintenance_runs.run_id``
        * SQLite partial index: ``UNIQUE constraint failed:
          maintenance_runs.section``
        """
        for _attempt in range(2):
            try:
                with Session(self.engine, expire_on_commit=False) as session:
                    session.add(run)
                    session.commit()
                return True
            except IntegrityError as exc:
                msg = str(exc).lower()
                is_pk = (
                    "maintenance_runs_pkey" in msg  # PG
                    or "maintenance_runs.run_id" in msg  # SQLite
                )
                is_partial = (
                    "uq_maintenance_runs_running_section" in msg  # PG
                    or "maintenance_runs.section" in msg  # SQLite
                )
                if is_partial and not is_pk:
                    # AM-5 — the refused caller's signal. NO row
                    # written for the refused caller (the conflicting
                    # row belongs to the in-flight run).
                    return False
                if is_pk:
                    # PK collision retry — regenerate the hex8 suffix
                    # and try once more.
                    new_id = _regenerate_hex_suffix(run.run_id)
                    logger.warning(
                        f"maintenance_runs PK collision: regenerating "
                        f"{run.run_id} -> {new_id}"
                    )
                    run.run_id = new_id
                    continue
                # Other IntegrityError (NOT NULL, etc.) — propagate.
                raise
        # Second PK collision after retry — propagate.
        raise IntegrityError(
            "maintenance_runs PK collision retry exhausted",
            params=None,
            orig=Exception("retry exhausted"),
        )

    def mark_terminal(
        self,
        run_id: str,
        status: str,
        completed_at: str,
        summary_json: Optional[dict] = None,
        error_json: Optional[dict] = None,
    ) -> None:
        """Write the terminal ``status`` + ``completed_at`` + audit fields.

        ``status`` ∈ ``{'succeeded', 'failed', 'interrupted'}`` —
        NOT ``'running'`` (the running row's lifecycle starts with
        :meth:`insert`; no transition back to ``running`` ever
        happens — the boot sweep on a dead-mans-``running`` row goes
        straight to ``interrupted`` without a write here).

        ``summary_json`` (succeeded) / ``error_json`` (failed /
        interrupted) — both optional; at least one is set by the
        caller based on the terminal status.
        """
        with Session(self.engine, expire_on_commit=False) as session:
            run = session.get(MaintenanceRun, run_id)
            if run is None:
                # [tidier fix pass] DEBUG, not WARNING: a missing row at
                # terminal-mark time is a benign race (manual runbook
                # heal / DB surgery removed it) and the boot sweep
                # already owns the orphan-heal WARNING lane.
                logger.debug(
                    f"maintenance_runs.mark_terminal: run_id={run_id} "
                    "not found (already removed?) — no-op"
                )
                return
            run.status = status
            run.completed_at = completed_at
            if summary_json is not None:
                run.summary_json = summary_json
            if error_json is not None:
                run.error_json = error_json
            session.add(run)
            session.commit()

    def cas_running_to_interrupted(self, section: str) -> int:
        """Boot-sweep CAS — flip every ``running`` row to ``interrupted``.

        AM-7 / T8 — unconditional rowcount-guarded CAS at lifespan
        start. Sets ``error_json = {'code': 'run_interrupted',
        'message': '...'}`` and ``completed_at = now_utc_iso()``.

        Returns the rowcount (the rowcount guard — the caller logs
        the summary line).

        Single-statement UPDATE (not load-then-write): the rowcount
        is exact under PG's MVCC + SQLite's per-connection write
        lock, and a concurrent sweeper cannot interleave. The JSON
        payload binds through :class:`JSONBType` (the sanctioned
        portable decorator) so both dialects serialize it correctly.
        """
        now_iso = now_utc_iso()
        stmt = text(
            """
            UPDATE maintenance_runs
            SET status = 'interrupted',
                completed_at = :completed_at,
                error_json = :payload
            WHERE section = :section AND status = 'running'
            """
        ).bindparams(
            bindparam("payload", type_=JSONBType()),
            bindparam("completed_at"),
            bindparam("section"),
        )
        with Session(self.engine, expire_on_commit=False) as session:
            result = session.execute(
                stmt,
                {
                    "section": section,
                    "completed_at": now_iso,
                    "payload": {
                        "code": "run_interrupted",
                        "message": "Run interrupted by daemon restart",
                    },
                },
            )
            session.commit()
            return result.rowcount or 0

    # ── Reads ─────────────────────────────────────────────────────────

    def get(self, run_id: str) -> Optional[MaintenanceRun]:
        """Fetch a single row by ``run_id``. ``None`` when unknown."""
        with Session(self.engine, expire_on_commit=False) as session:
            return session.get(MaintenanceRun, run_id)

    def get_running(self, section: str) -> Optional[MaintenanceRun]:
        """The at-most-one ``running`` row for ``section`` (AM-9).

        The partial unique index guarantees uniqueness; the
        ``ORDER BY started_at DESC LIMIT 1`` is belt-and-braces (in
        case the index is rebuilt into a non-unique variant).
        """
        with Session(self.engine, expire_on_commit=False) as session:
            stmt = (
                select(MaintenanceRun)
                .where(MaintenanceRun.section == section)
                .where(MaintenanceRun.status == "running")
                .order_by(MaintenanceRun.started_at.desc())
                .limit(1)
            )
            return session.exec(stmt).first()

    def latest_completed_for_section(
        self,
        section: str,
        kinds: tuple[str, ...] = ("auto", "manual_execute"),
    ) -> Optional[MaintenanceRun]:
        """Latest terminal (succeeded|failed) row of a kind in ``kinds``.

        AM-9 — ``last_run`` = latest ``succeeded|failed`` of ``kind ∈
        {auto, manual_execute}``; ``manual_dry_run`` NEVER surfaces.
        Dry-run history is queryable via :meth:`get` by ID.

        TEXT ISO ``completed_at`` sorts lexicographically;
        chronological ordering is preserved.
        """
        with Session(self.engine, expire_on_commit=False) as session:
            stmt = (
                select(MaintenanceRun)
                .where(MaintenanceRun.section == section)
                .where(MaintenanceRun.kind.in_(list(kinds)))
                .where(MaintenanceRun.status.in_(["succeeded", "failed"]))
                .order_by(MaintenanceRun.completed_at.desc())
                .limit(1)
            )
            return session.exec(stmt).first()

    def get_dry_run(self, run_id: str) -> Optional[MaintenanceRun]:
        """Fetch a manual dry-run row by ID (or ``None`` if missing).

        Execute validates the referenced dry-run via this lookup —
        a missing row (or a row of any OTHER kind) returns ``None``
        and the service emits 404 ``not_found`` with ``details.run_id``
        (T4.2: the lookup is ``kind='manual_dry_run'``-scoped — an
        execute referencing an auto or manual_execute row is NOT a
        valid dry-run reference).
        """
        with Session(self.engine, expire_on_commit=False) as session:
            stmt = select(MaintenanceRun).where(
                MaintenanceRun.run_id == run_id,
                MaintenanceRun.kind == "manual_dry_run",
            )
            return session.exec(stmt).first()

    def list_all(self) -> list[MaintenanceRun]:
        """Return all rows (testing / debugging only).

        NOT a public API surface — the operator-facing list view lives
        in the FE (Section 2 / maintenance section). Tests use this
        helper to seed + verify the table state.
        """
        with Session(self.engine, expire_on_commit=False) as session:
            stmt = select(MaintenanceRun).order_by(MaintenanceRun.started_at)
            return list(session.exec(stmt).all())


__all__ = ["MaintenanceRunsRepository"]
