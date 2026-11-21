"""Engine substrate repositories (Engine Arc Phase 1 — T1.4).

The repository is the ONLY writer of ``engine_jobs`` and ``engine_runs``
(plan: ``.agents/shared/planning/engine-arc-phase1/phase1-plan.md`` T1.4).
The closed surface is deliberate — mirrors the memoir closed-repository
discipline (``daemon/repositories/memoir``):

* :meth:`EngineRepository.upsert_job` — registration upsert by id.
* :meth:`EngineRepository.get_job` — one row by id.
* :meth:`EngineRepository.list_jobs` — filter by category / service_tag.
* :meth:`EngineRepository.append_run` — append-only run row.
* :meth:`EngineRepository.stamp_job_outcome` — the terminal-outcome stamp.

Every method is SYNC and blocks its thread. Async callers MUST bridge with
``await asyncio.to_thread(...)`` (the estate idiom; research-anchors.md
section 4). The registration API in
``daemon/services/engine/registry.py`` does exactly that (D1.11).

There is NO general update path: nothing outside this module may mutate an
``engine_jobs`` row, and nothing may mutate an ``engine_runs`` row at all
(append-only audit).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import update
from sqlmodel import Session, select

from daemon.repositories.engine.models import EngineJob, EngineRun
from daemon.services.timestamps import now_utc_iso

if TYPE_CHECKING:  # pragma: no cover — typing only
    from sqlalchemy.engine import Engine

__all__ = ["EngineRepository"]


class EngineRepository:
    """Closed-surface sync repository over ``engine_jobs`` / ``engine_runs``.

    Args:
        engine: The caller's SQLAlchemy engine (``manager._engine`` in the
            daemon, a throwaway SQLite engine in tests). This repository
            NEVER builds an engine and NEVER reads ambient ``POSTGRES_*``
            variables (the POK / memoir layer discipline).
    """

    def __init__(self, engine: "Engine") -> None:
        self.engine = engine

    # ── writes ─────────────────────────────────────────────────────────

    def upsert_job(self, job: EngineJob) -> EngineJob:
        """Insert a new registration row or update the existing one BY ID.

        Semantics (plan T1.8 / AC5):

        * No row for ``job.id`` → INSERT with ``version=1`` and
          ``registered_at == updated_at`` (the caller supplies the values;
          this method preserves whatever version the caller set, so the
          registry can decide the bump).
        * Existing row → UPDATE, preserving ``registered_at`` from the DB
          row (the first-registration instant never moves), stamping
          ``updated_at``.

        The caller (``daemon/services/engine/registry.py``) owns the
        version arithmetic; this method is the persistence half only. It
        never reads or writes any table other than the two engine tables.
        """
        with Session(self.engine) as session:
            existing = session.get(EngineJob, job.id)
            if existing is None:
                session.add(job)
                session.commit()
                session.refresh(job)
                return job

            existing.version = job.version
            existing.service_tag = job.service_tag
            existing.service_name = job.service_name
            existing.category = job.category
            existing.module_info = job.module_info
            existing.description = job.description
            existing.job_metadata = job.job_metadata
            existing.trigger_kind = job.trigger_kind
            existing.interval_seconds = job.interval_seconds
            existing.jitter_seconds = job.jitter_seconds
            existing.first_tick_kind = job.first_tick_kind
            existing.first_tick_offset_seconds = job.first_tick_offset_seconds
            existing.event_kind = job.event_kind
            existing.event_filter = job.event_filter
            existing.max_runtime_s = job.max_runtime_s
            existing.timeout_policy = job.timeout_policy
            existing.retry_max = job.retry_max
            existing.retry_backoff_base_s = job.retry_backoff_base_s
            existing.retry_backoff_cap_s = job.retry_backoff_cap_s
            existing.priority = job.priority
            existing.concurrency_class = job.concurrency_class
            existing.kill_switch_key = job.kill_switch_key
            existing.enabled = job.enabled
            existing.updated_at = job.updated_at
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

    def append_run(self, run: EngineRun) -> EngineRun:
        """Append ONE run row. Append-only: this never updates an existing row."""
        with Session(self.engine) as session:
            session.add(run)
            session.commit()
            session.refresh(run)
            return run

    def stamp_job_outcome(
        self,
        job_id: str,
        *,
        last_outcome: str,
        last_skip_reason: str | None = None,
        consecutive_skips: int | None = None,
        last_run_at: str | None = None,
    ) -> EngineJob | None:
        """Stamp the terminal-outcome columns on one job row.

        This is the ONLY path that mutates the last-outcome surface (the
        ``last_outcome`` / ``last_skip_reason`` / ``consecutive_skips`` /
        ``last_run_at`` columns). It never touches registration fields.

        Args:
            job_id: The ``engine_jobs.id`` to stamp.
            last_outcome: ``SUCCEEDED`` | ``SKIPPED_*`` | ``FAILED_BACKOFF``
                | ``TIMED_OUT``.
            last_skip_reason: Reason token for a skip outcome.
            consecutive_skips: The new counter value; ``None`` leaves the
                column untouched (a successful outcome typically passes 0).
            last_run_at: Aware-UTC ISO stamp; defaults to now.

        Returns:
            The refreshed row, or ``None`` when ``job_id`` does not exist.
        """
        stamp = last_run_at or now_utc_iso()
        values: dict[str, Any] = {
            "last_outcome": last_outcome,
            "last_skip_reason": last_skip_reason,
            "last_run_at": stamp,
            "updated_at": stamp,
        }
        if consecutive_skips is not None:
            values["consecutive_skips"] = consecutive_skips

        with Session(self.engine) as session:
            stmt = (
                update(EngineJob)
                .where(EngineJob.id == job_id)
                .values(**values)
            )
            result = session.exec(stmt)
            session.commit()
            if int(result.rowcount or 0) == 0:
                return None
            return session.get(EngineJob, job_id)

    # ── reads ──────────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> EngineJob | None:
        """Return one job row by id, or ``None``."""
        with Session(self.engine) as session:
            return session.get(EngineJob, job_id)

    def list_jobs(
        self,
        category: str | None = None,
        service_tag: str | None = None,
    ) -> list[EngineJob]:
        """List job rows, optionally filtered by category and/or service_tag."""
        with Session(self.engine) as session:
            stmt = select(EngineJob)
            if category is not None:
                stmt = stmt.where(EngineJob.category == category)
            if service_tag is not None:
                stmt = stmt.where(EngineJob.service_tag == service_tag)
            stmt = stmt.order_by(EngineJob.priority, EngineJob.id)
            return list(session.exec(stmt).all())
