"""Jobs status combo filter — regression pin.

Pins the fix for the combo-drop bug at the SQL boundary (repository
layer):

* Live-measured (disposable PG, 2026-09-08): ``GET /api/jobs?status=
  settled,failed`` returned **2 rows**; ``?status=failed`` alone
  returned **3 rows**. Adding ``settled`` to the combo silently DROPPED
  failed rows. The job-queue panel Recent (``completed,settled,
  failed,cancelled,dead_letter``) inherits the same drop — failed
  receipts vanished from the panel.

* Sibling defect (tester-observed): the M3 per-kind predicate's
  ``settled`` branch was strict (no ``terminal_reason IS NULL`` hedge)
  while the read API's :func:`_derive_legacy_status` derives
  ``settled`` for a message row with ``terminal_reason IS NULL``
  (per-kind dispatch on the lossy ``done → completed`` fallthrough).
  The filter and the read API disagreed.

The pre-fix ``JobRepository.list`` built per-kind branches only for
the ``completed`` and ``settled`` tokens; ``failed`` and ``cancelled``
had no per-kind branch, so a non-empty branch list AND-combined with
the admission-state IN-clause and silently narrowed every multi-token
filter to its ``completed``-or-``settled`` subset.

The fix: every done-cluster token (``completed`` / ``settled`` /
``failed`` / ``cancelled``) gets its own per-kind branch and the four
are OR-combined so a row matches when its DERIVED status is ANY of
the requested tokens. ``settled`` also carries the same NULL hedge as
``completed``.

Test shape (mirrors ``test_n3_per_kind_filter_pin.py``):

* File-backed SQLite at ``tmp_path`` (NullPool + FK on + WAL +
  busy_timeout=10000) — BLUEPRINT §3 recipe.
* Seed five rows covering the full per-kind × terminal-reason matrix.
* Drive ``JobRepository.list(statuses=...)`` with every combo the
  task description calls out; assert the row-set matches the
  derived-status contract.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.job_queue.models import (
    AdmissionState,
    JobItem,
    JobQueue,
)
from daemon.repositories.job_queue.repository import JobRepository


# ─── Fixtures (file-backed SQLite per BLUEPRINT recipe) ──────────────────


@pytest.fixture
def engine(tmp_path) -> Engine:
    """File-backed SQLite at ``tmp_path`` (NullPool + FK on + WAL).

    Blueprint §3 recipe — ``NullPool`` + file-backed SQLite at
    ``tmp_path`` + ``PRAGMA journal_mode=WAL`` +
    ``PRAGMA busy_timeout=10000`` + foreign-keys ON is the
    FORBIDDEN-PATTERN antidote for the QUARANTINE.md StaticPool +
    WriteGuardSession dependency_bus row.
    """
    db_path = tmp_path / "combo_pin.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


def _seed_queue(s, queue_id: str) -> None:
    queue = JobQueue(
        queue_id=queue_id,
        project_id="test-project",
        queue_name=queue_id,
        queue_name_lower=queue_id,
        queue_type="fifo",
        concurrency_limit=1,
        is_system=False,
        is_paused=False,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    s.add(queue)
    s.commit()


def _seed_job(
    s,
    *,
    job_id: str,
    job_type: str,
    terminal_reason: str | None,
    tag: str,
) -> str:
    """Seed a JobItem with the per-kind × terminal-reason params.

    Args:
        job_id: Stable UUID4 PK for the JobItem.
        job_type: ``"task"`` or ``"message"``.
        terminal_reason: ``"completed"`` / ``"failed"`` /
            ``"cancelled"`` / ``None`` (the NULL-hedge case).
        tag: Short human-readable tag stored in ``message`` so the
            tests can identify the row in the result set without
            needing to inspect the JobItem shape.
    """
    job = JobItem(
        job_id=job_id,
        agent_id="developer",
        agent_dir="/tmp/agents/developer",
        message=tag,
        source="api",
        project_id="test-project",
        priority=5,
        admission_state=AdmissionState.DONE.value,
        terminal_reason=terminal_reason,
        instance_id=None,
        queue_id="queue-combo",
        created_at=datetime.now(timezone.utc).isoformat(),
        job_metadata={},
        job_type=job_type,
    )
    s.add(job)
    s.commit()
    return job_id


@pytest.fixture
def seeded_combo_matrix(engine: Engine) -> dict[str, str]:
    """Seed the per-kind × terminal-reason matrix used by the
    combo-filter pin.

    Returns a mapping ``{tag: job_id}`` so the tests can identify
    each seeded row by its short tag. The matrix:

    * ``task-completed``      (task,    'completed')
    * ``message-completed``   (message, 'completed')
    * ``task-failed``         (task,    'failed')
    * ``message-failed``      (message, 'failed')
    * ``task-cancelled``      (task,    'cancelled')
    * ``message-cancelled``   (message, 'cancelled')
    * ``message-settled-null``(message, ``None`` — the NULL-hedge
      case the read API derives to ``settled`` via
      ``_derive_legacy_status``)
    """
    matrix = [
        ("task", "completed", "task-completed"),
        ("message", "completed", "message-completed"),
        ("task", "failed", "task-failed"),
        ("message", "failed", "message-failed"),
        ("task", "cancelled", "task-cancelled"),
        ("message", "cancelled", "message-cancelled"),
        ("message", None, "message-settled-null"),
    ]
    ids: dict[str, str] = {}
    with Session(engine) as s:
        _seed_queue(s, "queue-combo")
        for job_type, term, tag in matrix:
            jid = f"job-combo-{tag}-{uuid.uuid4().hex[:8]}"
            _seed_job(
                s,
                job_id=jid,
                job_type=job_type,
                terminal_reason=term,
                tag=tag,
            )
            ids[tag] = jid
    return ids


# ─── The combo-filter pin ────────────────────────────────────────────────


class TestJobsStatusComboFilter:
    """Combo-filter regression pin — per-kind × terminal-reason
    matrix MUST surface every row whose DERIVED status is in the
    requested set, regardless of combo composition.
    """

    @staticmethod
    def _messages(repo: JobRepository, statuses: list[str]) -> set[str]:
        """Run the repo filter and return the set of ``message``
        tags (the per-row short identifier we seeded).
        """
        jobs, _total = repo.list(
            statuses=statuses,
            project_id="test-project",
            limit=200,
        )
        return {j.message for j in jobs}

    # ─── 1. The combo-drop regression ────────────────────────────────

    def test_combo_settled_failed_returns_full_union(
        self, engine, seeded_combo_matrix
    ) -> None:
        """``statuses=['settled','failed']`` returns every row whose
        DERIVED status is ``settled`` OR ``failed``.

        Pre-fix: 1 row (only ``message-completed``). The two failed
        rows were silently DROPPED because the
        ``per_kind_branches`` list AND-combined with the
        admission-state IN-clause and narrowed to
        ``message-completed``-only.
        """
        repo = JobRepository(engine)
        ids = self._messages(repo, ["settled", "failed"])

        # settled-eligible: message-completed, message-settled-null
        assert "message-completed" in ids
        assert "message-settled-null" in ids
        # failed-eligible: task-failed, message-failed
        assert "task-failed" in ids
        assert "message-failed" in ids
        # task-completed and the cancelled rows must NOT appear
        # (caller didn't ask for ``completed`` or ``cancelled``)
        assert "task-completed" not in ids
        assert "task-cancelled" not in ids
        assert "message-cancelled" not in ids

    def test_combo_failed_settled_order_independence(
        self, engine, seeded_combo_matrix
    ) -> None:
        """``statuses=['failed','settled']`` (order-reversed) returns
        the same union as ``['settled','failed']``.
        """
        repo = JobRepository(engine)
        ids = self._messages(repo, ["failed", "settled"])

        assert ids == self._messages(repo, ["settled", "failed"])
        assert {
            "message-completed",
            "message-settled-null",
            "task-failed",
            "message-failed",
        } <= ids

    # ─── 2. The terminal_reason-NULL sibling ─────────────────────────

    def test_settled_includes_terminal_reason_null_message_rows(
        self, engine, seeded_combo_matrix
    ) -> None:
        """``statuses=['settled']`` returns BOTH ``message-completed``
        AND ``message-settled-null``.

        The pre-fix ``settled`` branch was strict
        (``terminal_reason == 'completed'``) which silently dropped
        the ``NULL`` row. The read API's ``_derive_legacy_status``
        derives ``NULL`` + ``job_type='message'`` to ``settled`` (the
        per-kind dispatch on the lossy ``done → completed``
        fallthrough) so the filter must agree.
        """
        repo = JobRepository(engine)
        ids = self._messages(repo, ["settled"])

        assert "message-completed" in ids
        assert "message-settled-null" in ids
        # task rows must NOT appear — settled is mirror-only
        assert "task-completed" not in ids
        assert "task-failed" not in ids
        assert "message-failed" not in ids

    # ─── 3. Single-token sanity ──────────────────────────────────────

    def test_single_token_failed_returns_only_failed_rows(
        self, engine, seeded_combo_matrix
    ) -> None:
        """``statuses=['failed']`` returns only the two failed rows
        (task + message), nothing else.
        """
        repo = JobRepository(engine)
        ids = self._messages(repo, ["failed"])

        assert ids == {"task-failed", "message-failed"}

    def test_single_token_completed_returns_only_task_completed(
        self, engine, seeded_combo_matrix
    ) -> None:
        """``statuses=['completed']`` returns task-completed rows
        only (the NULL hedge covers pre-7c rows; we don't seed any
        here so only ``task-completed`` appears).
        """
        repo = JobRepository(engine)
        ids = self._messages(repo, ["completed"])

        assert ids == {"task-completed"}

    def test_single_token_cancelled_returns_only_cancelled_rows(
        self, engine, seeded_combo_matrix
    ) -> None:
        """``statuses=['cancelled']`` returns BOTH cancelled rows
        (task + message), nothing else — the per-kind branch must
        not narrow by job_type for the cancelled token.
        """
        repo = JobRepository(engine)
        ids = self._messages(repo, ["cancelled"])

        assert ids == {"task-cancelled", "message-cancelled"}

    # ─── 4. The panel's full combo ───────────────────────────────────

    def test_panel_full_combo_returns_every_done_row(
        self, engine, seeded_combo_matrix
    ) -> None:
        """The job-queue panel's full combo
        ``['completed','settled','failed','cancelled','dead_letter']``
        returns every done row in the matrix (the dead_letter branch
        is purely an admission-state IN-clause — we don't seed dead
        rows here).
        """
        repo = JobRepository(engine)
        ids = self._messages(
            repo,
            ["completed", "settled", "failed", "cancelled", "dead_letter"],
        )

        assert ids == {
            "task-completed",
            "message-completed",
            "task-failed",
            "message-failed",
            "task-cancelled",
            "message-cancelled",
            "message-settled-null",
        }

    def test_panel_combo_count_matches_returned_rows(
        self, engine, seeded_combo_matrix
    ) -> None:
        """The panel combo's ``total`` count matches the number of
        returned rows — count and list queries must stay in lockstep
        after the fix (the bug was symmetric across both sites).
        """
        repo = JobRepository(engine)
        statuses = ["completed", "settled", "failed", "cancelled", "dead_letter"]
        jobs, total = repo.list(
            statuses=statuses,
            project_id="test-project",
            limit=200,
        )
        assert total == len(jobs)
        assert total == len(seeded_combo_matrix)
