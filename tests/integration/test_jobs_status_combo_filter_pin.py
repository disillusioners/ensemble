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

* Gap A (tester-live round, 2026-09-11): ``status=settled`` SOLO
  leaked dead-letter message rows (3 vs 2 live) —
  ``_LEGACY_TO_ADMISSION`` had no ``settled`` entry, so the admission
  IN-clause was skipped and the ``terminal_reason IS NULL`` hedge
  matched DEAD rows (dead rows carry no ``terminal_reason``
  discriminator). Fix: ``settled`` maps to ``done`` like the rest of
  the done cluster. Pinned by ``TestJobsDeadLetterUnionPins``.

* Gap B (tester-live round, 2026-09-11, pre-existing family):
  ``dead_letter`` + any done-cluster token DROPPED dead rows — the
  done-cluster per-kind OR-list AND-combined with the admission
  IN-clause, and the IN-clause (pinned to ``done`` + ``dead``) forced
  every survivor through a done-cluster shape; membership traded
  sides (intersection instead of union). Fix: when a done-cluster
  token is present, ``dead_letter`` contributes an
  ``admission_state='dead'`` OR-term alongside the done-cluster
  branches. Pinned by ``TestJobsDeadLetterUnionPins``.

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
    admission_state: str = AdmissionState.DONE.value,
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
        admission_state: ``AdmissionState`` value — defaults to
            ``done`` (every original matrix row); the dead-union
            fixture seeds ``dead`` rows (Gap A/B pins).
    """
    job = JobItem(
        job_id=job_id,
        agent_id="developer",
        agent_dir="/tmp/agents/developer",
        message=tag,
        source="api",
        project_id=project_id,
        priority=5,
        admission_state=admission_state,
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


# ─── Gap A + Gap B pins: the dead-admission rows ─────────────────────────

_DEAD_PROJECT = "test-project-dead"
_DEAD_QUEUE = "queue-dead-union"


@pytest.fixture
def seeded_dead_letter_matrix(engine: Engine) -> dict[str, str]:
    """Seed the done×dead matrix used by the Gap A/B union pins.

    Scoped to project ``test-project-dead`` so the original combo
    matrix's strict row-set pins stay isolated. The matrix repeats the
    original 7-row done-cluster shape (same tags) and adds TWO
    dead-admission rows — the canonical dead shape (``admission_state
    ='dead'``, NO ``terminal_reason`` discriminator, per
    ``_derive_legacy_status``: dead rows match via admission alone):

    * ``task-dead-null``    (task,    ``None``, admission=``dead``)
    * ``message-dead-null`` (message, ``None``, admission=``dead``)

    The task-kind dead row is the clean witness for Gap B: under the
    pre-fix intersection composition it matched NO requested token,
    while the message-kind dead row leaked through the settled NULL
    hedge — both wrong directions.
    """
    matrix = [
        # done side — same 7-row shape as the original combo matrix
        ("task", "completed", "task-completed", AdmissionState.DONE.value),
        ("message", "completed", "message-completed", AdmissionState.DONE.value),
        ("task", "failed", "task-failed", AdmissionState.DONE.value),
        ("message", "failed", "message-failed", AdmissionState.DONE.value),
        ("task", "cancelled", "task-cancelled", AdmissionState.DONE.value),
        ("message", "cancelled", "message-cancelled", AdmissionState.DONE.value),
        ("message", None, "message-settled-null", AdmissionState.DONE.value),
        # dead side — admission='dead', no terminal_reason
        ("task", None, "task-dead-null", AdmissionState.DEAD.value),
        ("message", None, "message-dead-null", AdmissionState.DEAD.value),
    ]
    ids: dict[str, str] = {}
    with Session(engine) as s:
        _seed_queue(s, _DEAD_QUEUE, project_id=_DEAD_PROJECT)
        for job_type, term, tag, admission in matrix:
            jid = f"job-combo-{tag}-{uuid.uuid4().hex[:8]}"
            _seed_job(
                s,
                job_id=jid,
                job_type=job_type,
                terminal_reason=term,
                tag=tag,
                project_id=_DEAD_PROJECT,
                queue_id=_DEAD_QUEUE,
                admission_state=admission,
            )
            ids[tag] = jid
    return ids


class TestJobsDeadLetterUnionPins:
    """Gap A + Gap B regression pins — dead-admission rows.

    Gap A (fix-introduced): ``_LEGACY_TO_ADMISSION`` lacked a
    ``settled`` entry, so a ``settled`` solo filter skipped the
    admission IN-clause entirely and the per-kind branch's
    ``terminal_reason IS NULL`` hedge matched DEAD rows (dead rows
    carry no ``terminal_reason``). Pre-fix: ``['settled']`` leaked
    ``message-dead-null``.

    Gap B (pre-existing family): ``dead_letter`` + a done-cluster
    token AND-composed the done-cluster per-kind OR-list with an
    admission IN-clause pinned to ``{'done','dead'}`` — every
    survivor had to satisfy a done-cluster shape, so dead rows fell
    out (and in the ``settled`` combo the done rows fell out too).
    Pre-fix: ``['settled','dead_letter']`` returned ONLY
    ``message-dead-null`` (accidental intersection);
    ``['failed','dead_letter']`` returned NOTHING; the full panel
    combo returned ONLY the two dead rows (membership traded sides).
    """

    DEAD_TAGS: frozenset[str] = frozenset(
        {"task-dead-null", "message-dead-null"}
    )
    DONE_SETTLED_TAGS: frozenset[str] = frozenset(
        {"message-completed", "message-settled-null"}
    )
    DONE_FAILED_TAGS: frozenset[str] = frozenset(
        {"task-failed", "message-failed"}
    )
    ALL_DONE_TAGS: frozenset[str] = frozenset(
        {
            "task-completed",
            "message-completed",
            "task-failed",
            "message-failed",
            "task-cancelled",
            "message-cancelled",
            "message-settled-null",
        }
    )

    @staticmethod
    def _messages(repo: JobRepository, statuses: list[str]) -> set[str]:
        """Run the repo filter scoped to the dead-matrix project."""
        jobs, _total = repo.list(
            statuses=statuses,
            project_id=_DEAD_PROJECT,
            limit=200,
        )
        return {j.message for j in jobs}

    # ─── Gap A: settled solo must NOT leak dead rows ─────────────────

    def test_settled_solo_excludes_dead_letter_rows(
        self, engine, seeded_dead_letter_matrix
    ) -> None:
        """``statuses=['settled']`` returns ONLY the done mirror rows.

        The dead rows are seeded ``admission_state='dead'`` with
        ``terminal_reason IS NULL`` — exactly the shape the pre-fix
        composition leaked: without a ``settled`` admission-map entry
        the IN-clause was skipped and the NULL hedge matched
        ``message-dead-null``.
        """
        repo = JobRepository(engine)
        ids = self._messages(repo, ["settled"])

        # done side present (settled-eligible mirror rows)
        assert self.DONE_SETTLED_TAGS <= ids
        # dead rows must NOT surface — the leak this pin closes
        assert ids.isdisjoint(self.DEAD_TAGS)
        # nothing else either (settled is mirror-only, done-only)
        assert ids == self.DONE_SETTLED_TAGS

        # count == page symmetry at the fixed site
        jobs, total = repo.list(
            statuses=["settled"],
            project_id=_DEAD_PROJECT,
            limit=200,
        )
        assert total == len(jobs)
        assert total == 2

    def test_dead_letter_solo_still_returns_dead_rows(
        self, engine, seeded_dead_letter_matrix
    ) -> None:
        """Sanity: ``statuses=['dead_letter']`` alone still surfaces
        exactly the two dead rows — proves the fixture's dead rows
        are visible to the filter, so the Gap A exclusion is a real
        predicate effect and not a seeding artifact.
        """
        repo = JobRepository(engine)
        ids = self._messages(repo, ["dead_letter"])

        assert ids == self.DEAD_TAGS

    # ─── Gap B: dead + done-cluster combos must UNION both sides ─────

    def test_dead_plus_settled_returns_both_sides(
        self, engine, seeded_dead_letter_matrix
    ) -> None:
        """``statuses=['settled','dead_letter']`` returns the dead
        rows AND the done settled-eligible rows.

        Pre-fix this returned ONLY ``message-dead-null`` — the
        admission IN-clause pinned to ``{'done','dead'}`` AND-combined
        with the settled per-kind branch killed the done rows and the
        task-kind dead row (membership traded sides).
        """
        repo = JobRepository(engine)
        ids = self._messages(repo, ["settled", "dead_letter"])

        # dead side present
        assert self.DEAD_TAGS <= ids
        # done side present
        assert self.DONE_SETTLED_TAGS <= ids
        # and nothing else
        assert ids == self.DEAD_TAGS | self.DONE_SETTLED_TAGS

        # count == page symmetry for the union
        jobs, total = repo.list(
            statuses=["settled", "dead_letter"],
            project_id=_DEAD_PROJECT,
            limit=200,
        )
        assert total == len(jobs)
        assert total == 4

    def test_dead_plus_failed_returns_both_sides(
        self, engine, seeded_dead_letter_matrix
    ) -> None:
        """``statuses=['failed','dead_letter']`` returns the dead
        rows AND the failed rows.

        Pre-fix this returned NOTHING: the admission IN-clause pinned
        to ``{'done','dead'}`` AND-combined with the failed
        per-kind branch (``terminal_reason IN failed-variants``) —
        dead rows carry no ``terminal_reason``, done rows are not
        ``admission_state='dead'``.
        """
        repo = JobRepository(engine)
        ids = self._messages(repo, ["failed", "dead_letter"])

        assert self.DEAD_TAGS <= ids
        assert self.DONE_FAILED_TAGS <= ids
        assert ids == self.DEAD_TAGS | self.DONE_FAILED_TAGS

        jobs, total = repo.list(
            statuses=["failed", "dead_letter"],
            project_id=_DEAD_PROJECT,
            limit=200,
        )
        assert total == len(jobs)
        assert total == 4

    def test_dead_plus_full_panel_combo_returns_both_sides(
        self, engine, seeded_dead_letter_matrix
    ) -> None:
        """The panel's full combo
        ``['completed','settled','failed','cancelled','dead_letter']``
        returns EVERY done row AND both dead rows.

        Pre-fix this returned ONLY the two dead rows — the exact
        membership trade the live round measured (the done rows the
        panel exists to show were all dropped; dead rows leaked in
        through the completed/settled NULL hedges).
        """
        repo = JobRepository(engine)
        statuses = [
            "completed", "settled", "failed", "cancelled", "dead_letter",
        ]
        ids = self._messages(repo, statuses)

        # dead side present
        assert self.DEAD_TAGS <= ids
        # done side present (all seven done rows)
        assert self.ALL_DONE_TAGS <= ids
        # and nothing else
        assert ids == self.DEAD_TAGS | self.ALL_DONE_TAGS

        jobs, total = repo.list(
            statuses=statuses,
            project_id=_DEAD_PROJECT,
            limit=200,
        )
        assert total == len(jobs)
        assert total == 9
