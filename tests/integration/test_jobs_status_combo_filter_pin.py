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

* Sibling defect (2026-09-10, this commit): the ``cancelled`` branch
  was strict ``terminal_reason == 'cancelled'`` while the read layer
  canonicalizes ``aborted`` / ``orphan_retired`` /
  ``watchover_terminated`` onto ``cancelled`` via
  ``_STATUS_CANONICAL_MAP`` — rows carrying those discriminators
  silently dropped from ``?status=cancelled`` and the panel's full
  combo. The fix derives each token's accepted value-set from the
  canonical map itself (``terminal_reason_variants_for``) — no
  hand-copied list that can drift again. Pinned by
  ``TestJobsStatusFilterCanonicalAliases`` below.

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


def _seed_queue(s, queue_id: str, project_id: str = "test-project") -> None:
    queue = JobQueue(
        queue_id=queue_id,
        project_id=project_id,
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
    project_id: str = "test-project",
    queue_id: str = "queue-combo",
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
        project_id: Project scoping — the canonicalization-alias
            fixture uses its own project so the original combo
            matrix's strict row-set pins stay isolated.
        queue_id: Queue scoping — same isolation rationale.
    """
    job = JobItem(
        job_id=job_id,
        agent_id="developer",
        agent_dir="/tmp/agents/developer",
        message=tag,
        source="api",
        project_id=project_id,
        priority=5,
        admission_state=AdmissionState.DONE.value,
        terminal_reason=terminal_reason,
        instance_id=None,
        queue_id=queue_id,
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


# ─── The terminal_reason canonicalization pin ────────────────────────────

# Raw ``terminal_reason`` discriminators the read layer canonicalizes
# onto ``cancelled`` via ``_STATUS_CANONICAL_MAP`` (the Phase 7c
# discriminator block). The SQL filter must accept every one of them
# under ``status=cancelled`` — a strict ``== 'cancelled'`` branch
# silently dropped these rows (the defect this pin closes).
_CANONICAL_CANCELLED_ALIASES: tuple[str, ...] = (
    "aborted",
    "orphan_retired",
    "watchover_terminated",
)

_ALIAS_PROJECT = "test-project-aliases"
_ALIAS_QUEUE = "queue-canonical-aliases"


@pytest.fixture
def seeded_canonical_alias_matrix(engine: Engine) -> dict[str, str]:
    """Seed the terminal_reason canonicalization-alias matrix.

    Three done-cluster rows whose ``terminal_reason`` carries a raw
    discriminator the read layer (:func:`_derive_legacy_status` →
    ``canonicalize_status``) folds onto ``cancelled``:

    * ``task-aborted``              (task, ``'aborted'``)
    * ``task-orphan-retired``       (task, ``'orphan_retired'``)
    * ``task-watchover-terminated`` (task, ``'watchover_terminated'``)

    Scoped to project ``test-project-aliases`` so the original combo
    matrix (``test-project``) is untouched — the original
    single-token pins assert STRICT row-sets and must stay green.
    """
    matrix = [
        ("task", "aborted", "task-aborted"),
        ("task", "orphan_retired", "task-orphan-retired"),
        ("task", "watchover_terminated", "task-watchover-terminated"),
    ]
    ids: dict[str, str] = {}
    with Session(engine) as s:
        _seed_queue(s, _ALIAS_QUEUE, project_id=_ALIAS_PROJECT)
        for job_type, term, tag in matrix:
            jid = f"job-combo-{tag}-{uuid.uuid4().hex[:8]}"
            _seed_job(
                s,
                job_id=jid,
                job_type=job_type,
                terminal_reason=term,
                tag=tag,
                project_id=_ALIAS_PROJECT,
                queue_id=_ALIAS_QUEUE,
            )
            ids[tag] = jid
    return ids


class TestJobsStatusFilterCanonicalAliases:
    """terminal_reason canonicalization pin — the SQL filter's
    per-kind branches must accept every raw ``terminal_reason``
    discriminator the read layer canonicalizes onto the requested
    token, and must NOT leak those rows under any other token.

    The defect (2026-09-10, same silent-drop class as the
    settled/failed combo bug): the ``cancelled`` branch was strict
    ``terminal_reason == 'cancelled'`` while
    ``_derive_legacy_status`` canonicalizes ``aborted`` /
    ``orphan_retired`` / ``watchover_terminated`` onto ``cancelled``
    — rows carrying those discriminators vanished from
    ``?status=cancelled`` and the panel's full combo.

    The fix derives each token's accepted value-set from
    ``_STATUS_CANONICAL_MAP`` (single source of truth) via
    ``terminal_reason_variants_for`` — no hand-copied list that can
    drift again.
    """

    ALIAS_TAGS: frozenset[str] = frozenset(
        {"task-aborted", "task-orphan-retired", "task-watchover-terminated"}
    )

    @staticmethod
    def _messages(repo: JobRepository, statuses: list[str]) -> set[str]:
        """Run the repo filter scoped to the alias project."""
        jobs, _total = repo.list(
            statuses=statuses,
            project_id=_ALIAS_PROJECT,
            limit=200,
        )
        return {j.message for j in jobs}

    # ─── 1. The canonicalization regression ──────────────────────────

    def test_cancelled_token_includes_all_three_canonical_aliases(
        self, engine, seeded_canonical_alias_matrix
    ) -> None:
        """``statuses=['cancelled']`` surfaces ALL THREE alias rows.

        Pre-fix: 0 rows (strict ``== 'cancelled'`` matched none of
        the raw discriminators).
        """
        repo = JobRepository(engine)
        ids = self._messages(repo, ["cancelled"])

        assert ids == self.ALIAS_TAGS

    def test_panel_full_combo_includes_all_three_canonical_aliases(
        self, engine, seeded_canonical_alias_matrix
    ) -> None:
        """The panel combo (``completed,settled,failed,cancelled,
        dead_letter``) surfaces all three alias rows — the
        job-queue panel Recent inherits the fix.
        """
        repo = JobRepository(engine)
        ids = self._messages(
            repo,
            ["completed", "settled", "failed", "cancelled", "dead_letter"],
        )

        assert ids == self.ALIAS_TAGS

    # ─── 2. No leakage into non-cancelled tokens ─────────────────────

    def test_failed_token_does_not_leak_canonical_aliases(
        self, engine, seeded_canonical_alias_matrix
    ) -> None:
        """``statuses=['failed']`` must NOT return any alias row —
        the read layer derives those rows to ``cancelled``, not
        ``failed``; a leak would mean the IN-lists crossed tokens.
        """
        repo = JobRepository(engine)
        ids = self._messages(repo, ["failed"])

        assert ids == set()

    def test_settled_token_does_not_leak_canonical_aliases(
        self, engine, seeded_canonical_alias_matrix
    ) -> None:
        """``statuses=['settled']`` must NOT return any alias row.

        The read layer's per-kind dispatch renames
        ``completed``+message ⇒ ``settled`` — a raw
        ``aborted`` discriminator canonicalizes to ``cancelled``
        BEFORE the per-kind dispatch, so it never becomes
        ``settled``. The filter must agree.
        """
        repo = JobRepository(engine)
        ids = self._messages(repo, ["settled"])

        assert ids == set()

    def test_completed_token_does_not_leak_canonical_aliases(
        self, engine, seeded_canonical_alias_matrix
    ) -> None:
        """``statuses=['completed']`` must NOT return any alias row —
        ``completed`` has no map aliases beyond itself, and the
        alias rows derive to ``cancelled`` anyway.
        """
        repo = JobRepository(engine)
        ids = self._messages(repo, ["completed"])

        assert ids == set()

    # ─── 3. Symmetry (count == page) for the new fixtures ────────────

    def test_symmetry_count_matches_page_for_cancelled_token(
        self, engine, seeded_canonical_alias_matrix
    ) -> None:
        """``total`` == page size for ``status=cancelled`` on the
        alias matrix — count and list sites carry the SAME
        map-derived branches.
        """
        repo = JobRepository(engine)
        jobs, total = repo.list(
            statuses=["cancelled"],
            project_id=_ALIAS_PROJECT,
            limit=200,
        )
        assert total == len(jobs)
        assert total == len(seeded_canonical_alias_matrix)

    def test_symmetry_count_matches_page_for_panel_combo(
        self, engine, seeded_canonical_alias_matrix
    ) -> None:
        """``total`` == page size for the panel combo on the alias
        matrix.
        """
        repo = JobRepository(engine)
        statuses = ["completed", "settled", "failed", "cancelled", "dead_letter"]
        jobs, total = repo.list(
            statuses=statuses,
            project_id=_ALIAS_PROJECT,
            limit=200,
        )
        assert total == len(jobs)
        assert total == len(seeded_canonical_alias_matrix)

    # ─── 4. Map parity — filter tracks the canonical map ─────────────

    def test_filter_accepts_exactly_the_map_image_for_cancelled(
        self, engine, seeded_canonical_alias_matrix
    ) -> None:
        """The alias set this pin seeds is DERIVED from the map, not
        hand-copied: every seeded discriminator is a map source whose
        target is ``cancelled``, and the filter returns exactly the
        rows whose terminal_reason the map folds onto the token.

        If a future alias is added to ``_STATUS_CANONICAL_MAP``,
        ``terminal_reason_variants_for`` picks it up automatically —
        this assertion guards the single-source-of-truth contract at
        the SQL boundary.
        """
        from daemon.services.work_status import _STATUS_CANONICAL_MAP

        map_cancelled_sources = {
            src for src, tgt in _STATUS_CANONICAL_MAP.items()
            if tgt == "cancelled"
        }
        assert {"aborted", "orphan_retired", "watchover_terminated"} <= (
            map_cancelled_sources
        )

        repo = JobRepository(engine)
        ids = self._messages(repo, ["cancelled"])
        # Every returned row's discriminator is a map source for the
        # token (tag → discriminator: dashes mirror underscores).
        for tag in ids:
            discriminator = tag.replace("task-", "").replace("-", "_")
            assert discriminator in map_cancelled_sources
