"""Unit tests for Fix C — read-model split (mission vs mirror).

Fix C closes the alarm-churn read-model split documented in
``.agents/shared/planning/job-task-retrospective/drift-history-and-constitution.md``
(I3, D3) and operationalized in
``.agents/shared/planning/job-task-retrospective/architecture-recommendation.md``
§4 step C. The split adds two additive fields to the read-model
surface (``WorkRecord`` + ``JobResponse`` + ``_ResolvedWork`` SSE
payload):

    * ``job_type`` — JobItem-side discriminator: ``"task"`` for
      mission (the JobItem IS the instance's lifecycle proxy) and
      ``"message"`` for mirror (the JobItem is a per-message receipt).
      ``None`` for Task-backed records (reports), where the
      concept does not apply.
    * ``mission_liveness`` — the canonical status of the linked
      ``Instance`` row, populated ONLY for mirror rows. For mission
      rows the row's own ``status`` IS the liveness signal (Phase 1,
      Job as Queue Proxy), so this field stays ``None``.

The exact incident reproduced by these tests:

    A mirror JobItem (``job_type='message'``) reaches ``done`` at T0
    (Fix B's inline idempotent transition). The linked instance
    keeps living past the receipt (parent with live children). The
    pre-Fix-C read model surfaced both rows as ``completed`` — the
    28c6421b false-"everything finished" read. Post-Fix-C the mirror
    row still says ``completed`` (the receipt IS true) but the new
    ``mission_liveness`` field surfaces ``processing`` (the instance
    is still alive), so the renderer can now distinguish the two.

These tests pin the read-model contract only. Write-side discipline
(census, fail-closed ``work_id``, validation) is the
``daemon.job_state.constitution`` gate's territory — see
``test_constitution_drift.py`` and ``docs/job-task-system.md §7``.

Harness notes
-------------

Mirrors the StaticPool in-memory SQLite recipe used by every other
file in ``tests/unit/services/test_work_resolver.py``. Real
repositories (no mocks) so the SQL-level reads are exercised; the
resolver is the only SUT.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel
from sqlmodel.main import SQLModelMetaclass

# Register every model on ``SQLModel.metadata`` BEFORE ``create_all`` —
# mirrors the harness in ``tests/unit/services/test_work_resolver.py``.
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.work_resolver import (
    WorkRecord,
    WorkResolverService,
)


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine() -> Engine:
    """Real in-memory SQLite engine (StaticPool)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def task_repo(engine: Engine) -> TaskRepository:
    return TaskRepository(engine)


@pytest.fixture
def job_repo(engine: Engine) -> JobRepository:
    return JobRepository(engine)


@pytest.fixture
def instance_repo(engine: Engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


@pytest.fixture
def resolver(
    task_repo: TaskRepository,
    job_repo: JobRepository,
    instance_repo: SQLModelInstanceRepository,
) -> WorkResolverService:
    return WorkResolverService(task_repo, job_repo, instance_repo)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str | None = None,
    agent_id: str = "developer",
    project_id: str | None = "test-project",
    status: str = "running",
    parent_id: str | None = None,
) -> str:
    """Insert an Instance row. Returns ``instance_id`` (auto-generated if None)."""
    iid = instance_id or f"inst-{uuid.uuid4().hex[:8]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            Instance(
                instance_id=iid,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                agent_name=agent_id,
                project_id=project_id,
                status=status,
                created_at=now_iso,
                updated_at=now_iso,
                paused_at=None,
                parent_id=parent_id,
            )
        )
        s.commit()
    return iid


def _seed_job(
    engine: Engine,
    *,
    job_id: str | None = None,
    agent_id: str = "developer",
    instance_id: str | None = None,
    admission_state: str = AdmissionState.QUEUED.value,
    terminal_reason: str | None = None,
    job_type: str = "task",
) -> str:
    """Insert a JobItem row. Returns ``job_id`` (auto-generated if None)."""
    jid = job_id or str(uuid.uuid4())
    created = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(
            JobItem(
                job_id=jid,
                agent_id=agent_id,
                agent_dir=f"/tmp/agents/{agent_id}",
                message="fix-c test message",
                source="api",
                project_id="test-project",
                priority=5,
                admission_state=admission_state,
                instance_id=instance_id,
                created_at=created,
                job_metadata={},
                terminal_reason=terminal_reason,
                job_type=job_type,
            )
        )
        s.commit()
    return jid


# ─── WorkRecord additive fields ─────────────────────────────────────────────


class TestWorkRecordSplitFields:
    """The WorkRecord view-model exposes ``job_type`` / ``mission_liveness``
    as additive fields on every record (``None`` for Task-backed records)."""

    def test_workrecord_default_has_split_fields_none(self) -> None:
        """A default WorkRecord (no overrides) carries the new fields as ``None``.

        Backward-compat: callers that construct a WorkRecord without
        specifying the new fields get the documented null semantics
        — ``None`` / ``None`` (Task-style record).
        """
        record = WorkRecord(
            work_id="w-default",
            kind="report",
            status="completed",
            instance_id=None,
            project_id=None,
            agent_id=None,
            result_summary=None,
            error=None,
            created_at=None,
        )
        assert record.job_type is None
        assert record.mission_liveness is None

    def test_workrecord_to_dict_emits_split_fields(self) -> None:
        """``to_dict()`` includes the two new keys for every record.

        Even Task-backed records carry the keys (with ``None``
        values) so the FE's split-rendering branch can rely on a
        stable wire shape.
        """
        record = WorkRecord(
            work_id="w-dict",
            kind="job",
            status="completed",
            instance_id="inst-1",
            project_id="p-1",
            agent_id="developer",
            result_summary=None,
            error=None,
            created_at=None,
            job_type="message",
            mission_liveness="processing",
        )
        d = record.to_dict()
        assert d["job_type"] == "message"
        assert d["mission_liveness"] == "processing"
        # Backward-compat: every pre-Fix-C key is still present.
        for key in (
            "work_id",
            "kind",
            "status",
            "instance_id",
            "project_id",
            "agent_id",
            "result_summary",
            "error",
            "created_at",
            "started_at",
            "completed_at",
            "message_id",
        ):
            assert key in d, f"pre-Fix-C key {key!r} dropped from to_dict()"


# ─── Mirror row contract — incident 28c6421b reproduced ────────────────────


class TestMissionMirrorSplit:
    """The split semantics: mission rows render their ``status`` as the
    liveness signal; mirror rows render ``status`` as the receipt and
    surface the linked instance's status via ``mission_liveness``.

    Incident 28c6421b is reproduced by
    ``test_mirror_done_with_live_mission_shows_mission_still_running``
    — the exact read that alarmed the user on 09-01.
    """

    def test_mirror_done_with_live_mission_shows_mission_still_running(
        self, engine, resolver
    ) -> None:
        """The 28c6421b read reproduced and closed.

        Setup: a message JobItem in ``admission_state='done'`` with
        ``terminal_reason='completed'`` (Fix B's inline idempotent
        transition at T0) beside an instance still running
        (``status='running'``). Pre-Fix-C both rows read as
        ``completed`` and the user read the pair as "everything
        finished".

        Post-Fix-C: the mirror row still says ``completed`` (the
        receipt IS true) but the new ``mission_liveness`` field
        surfaces ``processing`` — so the renderer can show the
        mirror as "message handled" without falsely claiming the
        mission is finished.
        """
        iid = _seed_instance(engine, instance_id="inst-live-parent", status="running")
        mirror_jid = _seed_job(
            engine,
            instance_id=iid,
            admission_state=AdmissionState.DONE.value,
            terminal_reason="completed",
            job_type="message",
        )

        record = resolver.resolve_work(mirror_jid)

        assert record is not None
        # The receipt — the mirror's own terminal truth — is unchanged.
        assert record.status == "completed"
        # The split: this is a mirror, not a mission.
        assert record.job_type == "message"
        # The mission is still running — the renderer can now tell.
        assert record.mission_liveness == "processing", (
            f"expected mission_liveness='processing' for mirror-on-live-mission, "
            f"got {record.mission_liveness!r}; the 28c6421b alarm cannot be "
            f"silenced without this distinction."
        )

    def test_mirror_done_with_terminal_mission_surfaces_terminal_liveness(
        self, engine, resolver
    ) -> None:
        """A mirror on a TERMINAL mission (instance.status='completed'):
        the receipt is ``completed`` AND the mission liveness is
        ``completed``. Both signals agree — the renderer can safely
        mark "everything finished".

        This is the contrasting positive case: the renderer can
        distinguish "everything finished" (both signals terminal) from
        "mirror done, mission still running" (split semantics).
        """
        iid = _seed_instance(engine, instance_id="inst-terminal-parent", status="completed")
        mirror_jid = _seed_job(
            engine,
            instance_id=iid,
            admission_state=AdmissionState.DONE.value,
            terminal_reason="completed",
            job_type="message",
        )

        record = resolver.resolve_work(mirror_jid)

        assert record is not None
        assert record.status == "completed"
        assert record.job_type == "message"
        assert record.mission_liveness == "completed"

    def test_mirror_dead_with_revived_instance_surfaces_liveness(
        self, engine, resolver
    ) -> None:
        """W4 hazard (DLQ-replay × instance-revive): a DEAD mirror
        whose linked instance has been revived to ``running``.

        The renderer MUST surface the mission liveness so the FE
        can show "mirror dead-lettered but mission is alive" — the
        orthogonal case to the W4 guard for mission rows (which
        keeps ``status`` at ``dead_letter`` regardless).
        """
        iid = _seed_instance(engine, instance_id="inst-revived", status="running")
        mirror_jid = _seed_job(
            engine,
            instance_id=iid,
            admission_state=AdmissionState.DEAD.value,
            terminal_reason=None,  # DEAD rows have no terminal_reason discriminator
            job_type="message",
        )

        record = resolver.resolve_work(mirror_jid)

        assert record is not None
        assert record.status == "dead_letter"
        assert record.job_type == "message"
        # The mirror IS dead-lettered; the instance it was attached to
        # was revived (DLQ-replay × instance-revive). Surface the
        # instance state so the FE can warn the operator.
        assert record.mission_liveness == "processing"

    def test_mirror_active_with_running_instance_surfaces_liveness(
        self, engine, resolver
    ) -> None:
        """Pre-terminal mirror (Fix B hasn't fired yet — instance
        still running). The status comes from the instance (Phase 1
        rule, unchanged) and the mirror gets mission_liveness from
        the same instance.
        """
        iid = _seed_instance(engine, instance_id="inst-active", status="running")
        mirror_jid = _seed_job(
            engine,
            instance_id=iid,
            admission_state=AdmissionState.ACTIVE.value,
            job_type="message",
        )

        record = resolver.resolve_work(mirror_jid)

        assert record is not None
        assert record.status == "processing"
        assert record.job_type == "message"
        assert record.mission_liveness == "processing"


# ─── Mission row contract — W4 guard + Phase 1 unchanged ──────────────────


class TestMissionRowContract:
    """For mission (task-type) JobItems, the row's ``status`` IS the
    liveness signal. ``mission_liveness`` stays ``None`` (Phase 1,
    Job as Queue Proxy). The W4 hazard (revived-instance-under-DEAD-job)
    is preserved.
    """

    def test_mission_active_status_from_instance(self, engine, resolver) -> None:
        """Active mission: status = canonical instance.status; no
        mission_liveness (mission rows don't get it — the field would
        be redundant with status).
        """
        iid = _seed_instance(engine, instance_id="inst-mission", status="running")
        mission_jid = _seed_job(
            engine,
            instance_id=iid,
            admission_state=AdmissionState.ACTIVE.value,
            job_type="task",
        )

        record = resolver.resolve_work(mission_jid)

        assert record is not None
        assert record.status == "processing"
        assert record.job_type == "task"
        assert record.mission_liveness is None, (
            "mission rows must NOT carry mission_liveness — the row's "
            "own status IS the liveness signal (Phase 1, Job as Queue "
            "Proxy); the field would be redundant."
        )

    def test_mission_done_status_from_terminal_reason(self, engine, resolver) -> None:
        """Done mission: status from terminal_reason; mission_liveness=None."""
        iid = _seed_instance(engine, instance_id="inst-done-mission", status="completed")
        mission_jid = _seed_job(
            engine,
            instance_id=iid,
            admission_state=AdmissionState.DONE.value,
            terminal_reason="failed",
            job_type="task",
        )

        record = resolver.resolve_work(mission_jid)

        assert record is not None
        assert record.status == "failed"
        assert record.job_type == "task"
        assert record.mission_liveness is None

    def test_mission_dead_status_overrides_instance_liveness(
        self, engine, resolver
    ) -> None:
        """W4 hazard: a DEAD mission row's status is hard-coded to
        ``dead_letter`` regardless of the linked instance's status
        (DLQ-replay × instance-revive can legally produce a revived
        instance under a DEAD job — the renderer must not let
        instance liveness override DEAD for mission rows).

        The split: mission_liveness stays ``None`` (mission rows
        don't get it), status stays ``dead_letter``. The instance
        is NOT consulted for the derived ``status`` field on this
        branch (the pre-Fix-C code path is preserved).
        """
        # Revived instance — would normally surface as ``processing``.
        iid = _seed_instance(engine, instance_id="inst-revived-mission", status="running")
        mission_jid = _seed_job(
            engine,
            instance_id=iid,
            admission_state=AdmissionState.DEAD.value,
            terminal_reason=None,
            job_type="task",
        )

        record = resolver.resolve_work(mission_jid)

        assert record is not None
        # W4 guard — the renderer must not surface "running" for a
        # DEAD mission, even when the instance has been revived.
        assert record.status == "dead_letter", (
            f"W4 hazard violated: DEAD mission surfaced status="
            f"{record.status!r} instead of 'dead_letter'. DLQ-replay "
            f"× instance-revive is the documented orthogonal case; "
            f"instance liveness must NEVER override DEAD for mission "
            f"rows."
        )
        assert record.job_type == "task"
        assert record.mission_liveness is None


# ─── Degradation contract — instance lookup fails soft ─────────────────────


class TestDegradationContract:
    """The instance lookup MUST degrade gracefully: on failure,
    mission_liveness stays ``None`` (degradation-safe contract per
    the message_metadata repo access precedent).

    The renderer falls back to the receipt-only view in that case —
    the pre-Fix-C behaviour stands (the spec is explicit: "if
    instance lookup fails, current behavior stands (warn + fall
    back)").
    """

    def test_mirror_with_no_linked_instance_has_null_mission_liveness(
        self, engine, resolver
    ) -> None:
        """A queue-stage mirror (instance_id IS NULL): no linked
        instance to consult, so mission_liveness stays ``None``.

        This is the "no instance at all" case — different from the
        "lookup failed" case (below) but both degrade to the same
        null answer.
        """
        # Queue-stage mirror: instance_id set by enqueue, but the
        # resolver path still tolerates a missing row.
        mirror_jid = _seed_job(
            engine,
            instance_id=None,
            admission_state=AdmissionState.QUEUED.value,
            job_type="message",
        )

        record = resolver.resolve_work(mirror_jid)

        assert record is not None
        assert record.status == "pending"
        assert record.job_type == "message"
        assert record.mission_liveness is None

    def test_mirror_instance_lookup_sqlalchemy_error_degrades_to_null(
        self, engine, resolver
    ) -> None:
        """When the instance repo's ``get()`` raises a
        ``SQLAlchemyError`` for a mirror row, the resolver must
        log a warning and return a record with ``mission_liveness=None``
        rather than propagating the exception.

        Defensive contract: the read path must not blow up on a
        transient DB error. Same pattern as the existing
        ``_lookup_instance`` (already catches ``SQLAlchemyError``
        for the active-state lookup) — Fix C reuses the same guard.
        """
        iid = _seed_instance(engine, instance_id="inst-degraded", status="running")
        mirror_jid = _seed_job(
            engine,
            instance_id=iid,
            admission_state=AdmissionState.DONE.value,
            terminal_reason="completed",
            job_type="message",
        )

        # Force the lookup to fail. The resolver's _lookup_instance
        # already catches SQLAlchemyError for the active-state path;
        # for Fix C we exercise the same guard on the mirror path.
        with patch.object(
            SQLModelInstanceRepository,
            "get",
            side_effect=SQLAlchemyError("simulated DB outage"),
        ):
            # Must not raise — degradation-safe contract.
            record = resolver.resolve_work(mirror_jid)

        assert record is not None
        assert record.status == "completed"
        assert record.job_type == "message"
        # The lookup degraded; mission_liveness stays None and the
        # renderer falls back to the receipt-only view.
        assert record.mission_liveness is None


# ─── list_work consistency — batched mirror rows ────────────────────────────


class TestListWorkSplitFields:
    """``list_work`` must surface the new fields on every row, and
    the batched fetch must remain ONE query per page (no N+1 — the
    perf hard requirement from the Fix C spec).
    """

    def test_list_work_emits_split_fields_on_every_job_row(
        self, engine, resolver
    ) -> None:
        """A page of JobItems (mixed kinds) — every record carries
        the two new fields, defaulted to ``None`` for mission rows.
        """
        iid_live = _seed_instance(engine, instance_id="inst-list-live", status="running")
        iid_done = _seed_instance(engine, instance_id="inst-list-done", status="completed")
        _seed_job(
            engine,
            instance_id=iid_live,
            admission_state=AdmissionState.ACTIVE.value,
            job_type="task",
        )
        _seed_job(
            engine,
            instance_id=iid_done,
            admission_state=AdmissionState.DONE.value,
            terminal_reason="completed",
            job_type="message",
        )

        records = resolver.list_work(kind="job")

        assert len(records) == 2
        for record in records:
            assert hasattr(record, "job_type")
            assert hasattr(record, "mission_liveness")
            assert record.job_type in {"task", "message"}

        # The split is observable on every record — find both rows
        # by their job_type discriminator.
        mirror_records = [r for r in records if r.job_type == "message"]
        mission_records = [r for r in records if r.job_type == "task"]
        assert len(mirror_records) == 1
        assert len(mission_records) == 1
        # Mirror gets the linked instance's canonical status.
        assert mirror_records[0].mission_liveness == "completed"
        # Mission does not get mission_liveness (Phase 1 contract).
        assert mission_records[0].mission_liveness is None

    def test_list_work_emits_null_split_fields_for_report_rows(
        self, engine, resolver
    ) -> None:
        """Task/report rows (``kind='report'``) carry ``job_type=None``
        and ``mission_liveness=None`` — the concept does not apply
        to Task-backed records.

        This pins the wire contract for FE consumers that branch on
        ``kind`` to pick the right rendering shape.
        """
        iid = _seed_instance(engine, instance_id="inst-report", status="running")
        with Session(engine) as s:
            wid = f"task-{uuid.uuid4().hex[:8]}"
            s.add(
                Task(
                    work_id=wid,
                    instance_id=iid,
                    task_type="process_report",
                    status=TaskStatus.COMPLETED.value,
                    created_at=datetime.now(timezone.utc),
                )
            )
            s.commit()

        records = resolver.list_work(kind="report")
        assert len(records) == 1
        report = records[0]
        assert report.kind == "report"
        assert report.job_type is None
        assert report.mission_liveness is None

    def test_list_work_no_n_plus_one_for_mission_liveness(
        self, engine, resolver
    ) -> None:
        """Perf hard requirement: the batched list path issues a
        bounded number of ``SELECT`` queries against the
        ``instances`` table for the entire page, regardless of how
        many rows need a ``mission_liveness`` consult. A regression
        to per-row lookups would scale linearly with the page size
        (the pre-Fix-B 50-row page → 50 ``SELECT … FROM instances``
        queries that degraded to 42s/206MB).

        The expected bounded shape with ``root_only=True`` (the
        default for ``list_work``):

        * ONE ``SELECT … FROM job_queue_items`` (the page-defining
          query, on the ``job_repo`` engine — not counted here).
        * ONE ``SELECT … FROM instances WHERE instance_id IN (…)``
          for ``_batch_instances`` (the per-page Instance fetch the
          batched path uses for both ``status`` AND
          ``mission_liveness``).
        * ONE ``SELECT … FROM instances WHERE instance_id IN (…) AND
          parent_id IS NOT NULL`` for ``_batch_child_instance_ids``
          (the ``root_only`` guard — also batched).

        3 queries: ``_query_jobs`` + ``_batch_instances`` +
        ``_batch_child_instance_ids``; per-row mirror lookups
        would breach the bound. We assert ``<= 3`` to leave
        headroom for an additional batched query if a future
        feature adds one, while still failing on a per-row
        regression (which would be 6+ for N=5).
        """
        # Seed 5 mirror rows on 5 distinct instances.
        for i in range(5):
            iid = _seed_instance(
                engine,
                instance_id=f"inst-perf-{i}",
                status="running",
            )
            _seed_job(
                engine,
                instance_id=iid,
                admission_state=AdmissionState.DONE.value,
                terminal_reason="completed",
                job_type="message",
            )

        # Wrap session.exec on the instance engine with a counter.
        call_count = 0
        original_exec = Session.exec

        def counting_exec(self, *args, **kwargs):  # noqa: ANN001 — test mock
            nonlocal call_count
            # Only count queries against the instance-repo engine.
            if self.bind is engine:
                call_count += 1
            return original_exec(self, *args, **kwargs)

        with patch.object(Session, "exec", counting_exec):
            records = resolver.list_work(kind="job")

        # 5 mirrors, each carrying mission_liveness from a distinct
        # instance. The batched path is bounded — N+1 would be 6+.        assert len(records) == 5
        for record in records:
            assert record.job_type == "message"
            assert record.mission_liveness == "processing"
        assert call_count <= 3, (
            f"N+1 regression: list_work(5 mirror rows) issued "
            f"{call_count} SQL queries on the instance engine. "
            f"Batched path must be bounded (job_repo SELECT + at most "
            f"3 instance-repo queries — _batch_instances, "
            f"_batch_child_instance_ids, and any future batched "
            f"helper). A per-row regression would push this to 6+; "
            f"fix is to extend the batched fetch in "
            f"_job_to_record (and the inferred-instance lookup in "
            f"the mirror branch) to fold into _batch_instances."
        )

    def test_list_work_batch_instance_lookup_failure_degrades_to_null(
        self, engine, resolver, caplog
    ) -> None:
        """W-1 batch-degradation contract: a transient
        ``SQLAlchemyError`` on the instance engine during the
        batched ``_batch_instances`` fetch must fail soft — the
        whole ``list_work`` call returns the page with every
        mirror row's ``mission_liveness`` set to ``None`` and the
        existing receipt-only ``status`` answers preserved; a
        single warning is logged (NOT one per row); no exception
        propagates.

        Pre-W-1, the batch path was unprotected — a transient
        DB outage on the instance engine would 500 the entire
        ``list_jobs`` / ``list_work`` call. The single-row
        ``_lookup_instance`` guard was already in place; the
        batch path is now symmetric with that guard (narrow
        catch, warn once per batch, return empty map so the
        caller falls back to the receipt-only view).
        """
        # Seed a page of 3 mirror rows on 3 distinct instances.
        # Mix of live and terminal so the "before" test could
        # have surfaced non-null mission_liveness — proving
        # the degradation flips them all to None.
        seed_specs = [
            ("inst-bd-running", "running"),
            ("inst-bd-queued", "queued"),
            ("inst-bd-completed", "completed"),
        ]
        for iid, status in seed_specs:
            _seed_instance(engine, instance_id=iid, status=status)
            _seed_job(
                engine,
                instance_id=iid,
                admission_state=AdmissionState.DONE.value,
                terminal_reason="completed",
                job_type="message",
            )

        # Force the batched fetch to fail — patch ``Session``
        # so the ``session.exec(stmt)`` call inside
        # ``_batch_instances`` raises the same exception class the
        # single-row guard catches. This exercises the
        # degradation wrap IN PLACE inside ``_batch_instances``
        # (patching the whole method would bypass the wrap, so
        # we patch the inner SQL execution instead).
        #
        # Filter: only raise for the SPECIFIC ``_batch_instances``
        # SELECT (full-Instance fetch). We differentiate by
        # the SELECT's ``expr`` — ``_batch_instances`` projects
        # the full ``Instance`` class while
        # ``_batch_child_instance_ids`` projects the scalar
        # ``Instance.instance_id`` column (an InstrumentedAttribute,
        # not a SQLModelMetaclass). Leave the scalar SELECT
        # alone so the ``root_only`` guard still works and the
        # page can be filtered to its non-child rows.
        #
        # ALSO patch ``SQLModelInstanceRepository.get`` to
        # simulate the per-row lookup (``_lookup_instance``)
        # also failing — otherwise the per-row fallback inside
        # ``_job_to_record`` would silently resolve
        # ``mission_liveness`` via ``_instance_repo.get``,
        # defeating the degradation contract we're verifying.

        original_exec = Session.exec

        def raising_exec(self, stmt, *args, **kwargs):  # noqa: ANN001 — test mock
            try:
                col_desc = stmt.column_descriptions[0]
                entity = col_desc["entity"]
                expr = col_desc["expr"]
            except (AttributeError, IndexError, KeyError):
                return original_exec(self, stmt, *args, **kwargs)
            # Only fail the full-Instance SELECT (class-shaped
            # ``expr``); leave the scalar
            # ``select(Instance.instance_id)`` SELECT alone.
            if entity is Instance and isinstance(expr, SQLModelMetaclass):
                raise SQLAlchemyError("simulated batch DB outage")
            return original_exec(self, stmt, *args, **kwargs)

        with caplog.at_level("WARNING"):
            with patch.object(Session, "exec", raising_exec), patch.object(
                SQLModelInstanceRepository,
                "get",
                side_effect=SQLAlchemyError("simulated per-row DB outage"),
            ):
                # Must not raise — degradation-safe contract.
                records = resolver.list_work(kind="job")

        # Every seeded row still surfaces (the JobItem SELECT
        # is unaffected by the instance-engine failure).
        assert len(records) == 3, (
            f"list_work should return all JobItems even when the "
            f"instance batch fails; got {len(records)} records"
        )

        # Every mirror row's mission_liveness degrades to None
        # (the fallback the renderer already understands).
        for record in records:
            assert record.job_type == "message"
            assert record.mission_liveness is None, (
                f"W-1 degradation violated: mirror row kept "
                f"mission_liveness={record.mission_liveness!r} "
                f"after instance batch failed; must degrade to "
                f"None so the renderer falls back to receipt-only."
            )

        # Existing ``status`` answers are unchanged — the
        # receipt IS true regardless of the instance-engine
        # outage. ``done`` mirrors with ``terminal_reason='completed'``
        # canonicalize to "completed".
        statuses = sorted(r.status for r in records)
        assert statuses == ["completed", "completed", "completed"], (
            f"W-1 degradation violated: receipt statuses changed "
            f"to {statuses!r}; the JobItem mirror must remain "
            f"authoritative for the receipt field."
        )

        # ONE warning logged for the batch (not per-row).
        warnings = [
            r for r in caplog.records
            if "batched instance lookup failed" in r.message
        ]
        assert len(warnings) == 1, (
            f"W-1 must log exactly ONE warning per batch (not "
            f"per row); got {len(warnings)} warnings. Per-row "
            f"warnings would flood the logs on a 50-row page "
            f"during a transient outage."
        )


# ─── Schema + response surface ──────────────────────────────────────────────


class TestJobResponseSurface:
    """The split-semantics additive-field contract (Fix C spec),
    pinned at the two wire-shape surfaces that the FE actually
    consumes:

    * ``JobResponse`` (router schema) — produced by
      ``_job_to_response`` in ``routers/jobs_crud.py`` and
      reused verbatim by ``routers/jobs_management.py``. Tests
      pin the additive field presence + ``None`` defaults so
      older FE clients and fixtures keep working.
    * ``_ResolvedWork`` (SSE payload in
      ``routers/jobs_streaming.py``) — the live-stream wire
      shape. Tests pin both ``to_payload`` (connected /
      status_update) and ``to_completed_payload`` (terminal
      event) emit the split-semantics keys.

    The jobs_management delegation is router-level wiring
    covered by its own suite; the underlying
    ``WorkRecord`` primary surface is pinned by the
    ``TestWorkRecordSplitFields`` / ``TestMissionMirrorSplit``
    / ``TestListWorkSplitFields`` block above. These tests
    cover ONLY the two additive-field wire shapes — the
    schema (JobResponse) and the SSE payload
    (_ResolvedWork) — that FE consumers branch on.
    """

    def test_job_response_schema_has_split_fields(self) -> None:
        """``JobResponse`` carries ``job_type`` / ``mission_liveness``
        as additive fields with ``None`` defaults — backward
        compatible (existing test fixtures that construct
        ``JobResponse`` without these fields continue to work).
        """
        from daemon.routers.schemas import JobResponse

        # Bare construction without the new fields must succeed
        # (additive contract — ``None`` defaults).
        response = JobResponse(
            job_id="j-1",
            status="completed",
            priority=5,
            agent_id="developer",
            agent_dir="/tmp/agent",
            created_at="2025-09-02T00:00:00+00:00",
        )
        assert response.job_type is None
        assert response.mission_liveness is None

        # And explicit values round-trip.
        response2 = JobResponse(
            job_id="j-2",
            status="completed",
            priority=5,
            agent_id="developer",
            agent_dir="/tmp/agent",
            created_at="2025-09-02T00:00:00+00:00",
            job_type="message",
            mission_liveness="processing",
        )
        assert response2.job_type == "message"
        assert response2.mission_liveness == "processing"

    def test_resolved_work_sse_payload_carries_split_fields(self) -> None:
        """``_ResolvedWork.to_payload()`` and ``to_completed_payload()``
        emit the two new keys so the FE work-view's SSE consumer can
        render the split. ``None`` for Task-backed records.
        """
        from daemon.routers.jobs_streaming import _ResolvedWork

        # Mirror on a live mission (the 28c6421b shape).
        resolved = _ResolvedWork(
            work_id="w-sse",
            status="completed",
            instance_id="inst-sse",
            queue_id=None,
            result_summary=None,
            error_message=None,
            job_type="message",
            mission_liveness="processing",
        )

        # Connected / status_update payload.
        payload = resolved.to_payload(work_id="w-sse")
        assert payload["job_type"] == "message"
        assert payload["mission_liveness"] == "processing"

        # Completed payload (terminal event) — the FE most needs
        # the split here because the renderer is deciding whether
        # to show "all done" or "mirror done, mission still running".
        completed = resolved.to_completed_payload(work_id="w-sse")
        assert completed["job_type"] == "message"
        assert completed["mission_liveness"] == "processing"

    def test_resolved_work_sse_payload_carries_none_for_task_rows(self) -> None:
        """Task/report rows (no job_type, no mission_liveness) pass
        through as ``None`` so older FE clients that branch on
        the existence of the keys (rather than the values) get a
        stable wire shape.
        """
        from daemon.routers.jobs_streaming import _ResolvedWork

        resolved = _ResolvedWork(
            work_id="w-task",
            status="completed",
            instance_id="inst-task",
            queue_id=None,
            result_summary=None,
            error_message=None,
            job_type=None,
            mission_liveness=None,
        )

        payload = resolved.to_payload(work_id="w-task")
        assert "job_type" in payload
        assert "mission_liveness" in payload
        assert payload["job_type"] is None
        assert payload["mission_liveness"] is None

    def test_job_response_serializer_key_parity_with_model_fields(self) -> None:
        """Serializer-key parity tripwire (B5): the set of keys emitted
        by ``JobResponse._serialize`` MUST equal ``set(JobResponse.model_fields)``
        when the kill-switch is ON, and MUST equal ``set(JobResponse.model_fields)``
        minus the three mission_* keys when the kill-switch is OFF.

        The custom ``@model_serializer`` emits exactly the model fields
        (in ON mode) or the model fields minus the mission_* triple
        (in OFF mode) — no drift is allowed. If a new field lands on
        ``JobResponse`` but the serializer forgets it, this test fails
        (the next maintainer knows to extend the serializer). If the
        serializer leaks an extra key the model doesn't declare, this
        test fails the same way (anti-leakage tripwire).
        """
        from daemon.routers.schemas import JobResponse

        # Default: kill-switch OFF (the M1 default). The serializer must
        # omit the three mission_* keys so the OFF wire format stays
        # byte-identical to pre-M1.
        response = JobResponse(
            job_id="j-parity",
            status="completed",
            priority=5,
            agent_id="developer",
            agent_dir="/tmp/agent",
            created_at="2025-09-02T00:00:00+00:00",
            admission_state="done",
            terminal_reason="completed",
            job_type="message",
            mission_liveness="processing",
            mission_id="inst-x",
            mission_epoch=1,
            mission_terminal_reason=None,
        )

        serialized = response.model_dump()

        mission_keys = {"mission_id", "mission_epoch", "mission_terminal_reason"}
        expected_off = set(JobResponse.model_fields) - mission_keys
        actual_off = set(serialized)
        assert actual_off == expected_off, (
            f"OFF-state serializer key drift: only_in_serialized="
            f"{sorted(actual_off - expected_off)} "
            f"only_in_model_fields={sorted(expected_off - actual_off)}"
        )

        # Confirm the three mission_* keys are absent (the OFF contract).
        assert mission_keys.isdisjoint(set(serialized)), (
            f"OFF state must NOT include {sorted(mission_keys)} "
            f"(byte-identical to pre-M1); got {sorted(set(serialized) & mission_keys)}"
        )

    def test_job_response_on_state_emits_mission_keys(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ON-state emission test (B5): with the kill-switch ON,
        ``JobResponse.model_dump`` includes the three mission_* keys.

        Pins the additive-on-ON contract for the JobResponse wire
        surface (the ``_serialize`` collapse is a refactor; the
        ON-state emission contract must NOT change).
        """
        import daemon.services.mission_resolver as mr_mod
        from daemon.routers.schemas import JobResponse

        monkeypatch.setenv("ENSEMBLE_MISSION_PROJECTION_ENABLED", "1")
        mr_mod._reset_mission_projection_for_tests()

        response = JobResponse(
            job_id="j-on",
            status="completed",
            priority=5,
            agent_id="developer",
            agent_dir="/tmp/agent",
            created_at="2025-09-02T00:00:00+00:00",
            admission_state="done",
            mission_id="inst-on",
            mission_epoch=1,
            mission_terminal_reason="completed",
        )

        serialized = response.model_dump()

        assert serialized["mission_id"] == "inst-on"
        assert serialized["mission_epoch"] == 1
        assert serialized["mission_terminal_reason"] == "completed"

        # Restore OFF for downstream tests.
        mr_mod._reset_mission_projection_for_tests()
