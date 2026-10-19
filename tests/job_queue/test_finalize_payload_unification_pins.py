"""D2 — payload-seam unification pins (2026-10-18,
fix/finalize-subtree-error-payload — RCA job 6465391d).

Incident: the terminal ``[JOB_EVENT]`` rendered the FROZEN task-16551
first-turn text under ``Result:`` (the watcher-notify WorkRecord
resolver fallback — the JobItem-linked FIRST task's ``Task.result``)
while the event-publish lane carried the real report via
``_get_last_assistant_message_raw`` (event #30570, 08:21:59Z).
v0.13.9 stale-result family, different seam.

Two fix lanes pinned here:

  * **Observer outbox threading (producer side).** The
    ``_finalize_job`` post-commit outbox now threads
    ``db_result.result_summary`` (the SAME authoritative final
    payload the JOB_COMPLETED event row publish carries) on EVERY
    per-kind receipt — the failed lane passes ``error=`` AND
    ``result_summary=``; non-failed receipts (settled/completed)
    pass the payload, not the error string.
    Differential: pre-fix the failed receipt call carried NO
    ``result_summary`` kwarg (the resolver fallback fired with the
    frozen text) and the settled-on-errored-mission receipt carried
    the ERROR STRING in the ``Result:`` slot (the cosmetic skew).

  * **Resolver-fallback unification (notify side).** For terminal
    receipts of JOB-kind work whose caller did NOT thread the
    payload, ``notify_work_watchers`` now prefers the instance's
    last assistant message (same seam as the event-publish lane,
    ``skip_repair=True``) over the frozen first-task
    ``Task.result``; TASK-kind records keep the resolver value
    (each task's own result is that work unit's correct payload);
    the seam is skipped for non-terminal receipts and when the
    caller threaded the payload; a seam failure fails OPEN to the
    resolver value.

Tier-1 pins drive the REAL ``_process_event`` → ``_finalize_job`` →
post-commit outbox flow (only ``_finalize_job_db_sync`` is mocked),
reusing the N8 hot-path harness. Tier-2 pins drive the REAL
``notify_work_watchers`` with a real ``JobWatcherRepository`` +
patched ``WorkResolverService`` per the defect1-pin recipe.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.task.models import Task
from daemon.repositories.task.repository import TaskRepository
from daemon.services.job_feedback_observer import _FinalizeJobResult
from daemon.services.work_notifier import notify_work_watchers
from daemon.services.work_resolver import WorkRecord, WorkResolverService

from tests.job_queue.test_n8_hot_path_pin import (
    _build_observer_for_hot_path,
)

AUTHORITATIVE = "REAL FINAL REPORT (event #30570, 08:21:59Z)"
FROZEN = "FROZEN FIRST-TURN TEXT (task 16551)"


# ── Tier-2 fixture machinery (defect1-pin recipe) ────────────────────────


@pytest.fixture
def d2_engine(tmp_path):
    db_path = tmp_path / "d2-payload.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(eng, "connect")
    def _enable_fk(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng


class _NoOpJobRepo:
    def get(self, _job_id):
        return None

    def __getattr__(self, name):
        return lambda *_a, **_kw: None


@pytest.fixture
def d2_components(d2_engine):
    watcher_repo = JobWatcherRepository(d2_engine)
    task_repo = TaskRepository(d2_engine)
    instance_repo = SQLModelInstanceRepository(d2_engine)
    resolver = WorkResolverService(task_repo, _NoOpJobRepo(), instance_repo)
    instance_manager = MagicMock()
    instance_manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-d2")
    )
    instance_manager._instance_repository = instance_repo
    return {
        "engine": d2_engine,
        "watcher_repo": watcher_repo,
        "resolver": resolver,
        "instance_manager": instance_manager,
    }


def _seed_instance(engine, *, instance_id: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(Instance(
            instance_id=instance_id, agent_id="worker",
            agent_dir="/tmp/w", agent_name="worker",
            project_id="p1", status="completed",
            created_at=now_iso, updated_at=now_iso,
            paused_at=None, parent_id=None,
        ))
        s.commit()


def _add_watch(engine, *, work_id: str, instance_id: str, watch_events) -> None:
    with Session(engine) as s:
        s.add(JobWatcher(
            job_id=work_id, instance_id=instance_id,
            watch_events=watch_events,
        ))
        s.commit()


def _patch_resolver(resolver, record: WorkRecord):
    original = resolver.resolve_work
    resolver.resolve_work = MagicMock(return_value=record)
    return original


def _job_record(*, wid: str, status: str, result_summary: str | None):
    return WorkRecord(
        work_id=wid, kind="job", status=status,
        instance_id="inst-d2", project_id="p1",
        agent_id="worker", result_summary=result_summary,
        error=None, created_at=datetime.now(timezone.utc),
        job_type="message", mission_liveness=None,
    )


def _task_record(*, wid: str, status: str, result_summary: str | None):
    return WorkRecord(
        work_id=wid, kind="report", status=status,
        instance_id="inst-d2", project_id="p1",
        agent_id="worker", result_summary=result_summary,
        error=None, created_at=datetime.now(timezone.utc),
        job_type=None, mission_liveness=None,
    )


def _delivered_bodies(instance_manager) -> list[str]:
    return [
        call.kwargs["message"]
        for call in instance_manager.enqueue_message.await_args_list
    ]


# ── Tier-2 pins: resolver-fallback unification in notify_work_watchers ───


class TestResolverFallbackUnification:
    """Terminal JOB-kind receipts whose caller did not thread the
    payload must render the authoritative final payload, not the
    frozen first-task ``Task.result``."""

    @pytest.mark.asyncio
    async def test_unthreaded_failed_job_kind_prefers_authoritative_payload(
        self, d2_components,
    ):
        engine = d2_components["engine"]
        _seed_instance(engine, instance_id="inst-d2")
        _seed_instance(engine, instance_id="watcher-1")
        wid = f"wid-{uuid.uuid4().hex[:8]}"
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["failed"])

        im = d2_components["instance_manager"]
        im._get_last_assistant_message_raw = AsyncMock(return_value=AUTHORITATIVE)
        original = _patch_resolver(
            d2_components["resolver"],
            _job_record(wid=wid, status="failed", result_summary=FROZEN),
        )
        try:
            notified = await notify_work_watchers(
                wid, "failed",
                error="child agent error",
                instance_manager=im,
                work_resolver=d2_components["resolver"],
                watcher_repo=d2_components["watcher_repo"],
            )
        finally:
            d2_components["resolver"].resolve_work = original

        assert notified == 1
        (body,) = _delivered_bodies(im)
        assert "Error: child agent error" in body
        assert f"Result:\n{AUTHORITATIVE}" in body, (
            "D2: the failed receipt's ``Result:`` slot must carry the "
            "authoritative final payload (the instance's last "
            "assistant message — same seam as the event-publish "
            "lane), not the resolver's frozen first-task text."
        )
        assert FROZEN not in body, (
            "D2: the frozen first-turn text (task-16551 shape) must "
            "NOT appear on the delivered envelope."
        )
        im._get_last_assistant_message_raw.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unthreaded_settled_job_kind_prefers_authoritative_payload(
        self, d2_components,
    ):
        engine = d2_components["engine"]
        _seed_instance(engine, instance_id="inst-d2")
        _seed_instance(engine, instance_id="watcher-1")
        wid = f"wid-{uuid.uuid4().hex[:8]}"
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["settled"])

        im = d2_components["instance_manager"]
        im._get_last_assistant_message_raw = AsyncMock(return_value=AUTHORITATIVE)
        original = _patch_resolver(
            d2_components["resolver"],
            _job_record(wid=wid, status="settled", result_summary=FROZEN),
        )
        try:
            notified = await notify_work_watchers(
                wid, "settled",
                instance_manager=im,
                work_resolver=d2_components["resolver"],
                watcher_repo=d2_components["watcher_repo"],
            )
        finally:
            d2_components["resolver"].resolve_work = original

        assert notified == 1
        (body,) = _delivered_bodies(im)
        assert "settled ✓" in body
        assert f"Result:\n{AUTHORITATIVE}" in body
        assert FROZEN not in body

    @pytest.mark.asyncio
    async def test_task_kind_keeps_resolver_value(
        self, d2_components,
    ):
        """TASK-kind records are NOT the frozen class — each task's own
        ``result`` is that work unit's correct payload; the seam must
        not fire."""
        engine = d2_components["engine"]
        _seed_instance(engine, instance_id="inst-d2")
        _seed_instance(engine, instance_id="watcher-1")
        wid = f"wid-{uuid.uuid4().hex[:8]}"
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["completed"])

        im = d2_components["instance_manager"]
        im._get_last_assistant_message_raw = AsyncMock(return_value=AUTHORITATIVE)
        original = _patch_resolver(
            d2_components["resolver"],
            _task_record(wid=wid, status="completed", result_summary="TASK OWN RESULT"),
        )
        try:
            notified = await notify_work_watchers(
                wid, "completed",
                instance_manager=im,
                work_resolver=d2_components["resolver"],
                watcher_repo=d2_components["watcher_repo"],
            )
        finally:
            d2_components["resolver"].resolve_work = original

        assert notified == 1
        (body,) = _delivered_bodies(im)
        assert "Result:\nTASK OWN RESULT" in body
        im._get_last_assistant_message_raw.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_threaded_payload_is_authoritative_seam_not_called(
        self, d2_components,
    ):
        """A caller-threaded ``result_summary`` wins; the fallback seam
        is skipped entirely (F-1 producer-side precedence kept)."""
        engine = d2_components["engine"]
        _seed_instance(engine, instance_id="inst-d2")
        _seed_instance(engine, instance_id="watcher-1")
        wid = f"wid-{uuid.uuid4().hex[:8]}"
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["completed"])

        im = d2_components["instance_manager"]
        im._get_last_assistant_message_raw = AsyncMock(return_value=AUTHORITATIVE)
        original = _patch_resolver(
            d2_components["resolver"],
            _job_record(wid=wid, status="completed", result_summary=FROZEN),
        )
        try:
            notified = await notify_work_watchers(
                wid, "completed",
                instance_manager=im,
                work_resolver=d2_components["resolver"],
                watcher_repo=d2_components["watcher_repo"],
                result_summary="PRODUCER IN-MEMORY CONTENT",
            )
        finally:
            d2_components["resolver"].resolve_work = original

        assert notified == 1
        (body,) = _delivered_bodies(im)
        assert "Result:\nPRODUCER IN-MEMORY CONTENT" in body
        im._get_last_assistant_message_raw.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_seam_failure_fails_open_to_resolver_value(
        self, d2_components,
    ):
        """A broken/raising seam must not lose the receipt — the
        resolver value renders (documented fail-open)."""
        engine = d2_components["engine"]
        _seed_instance(engine, instance_id="inst-d2")
        _seed_instance(engine, instance_id="watcher-1")
        wid = f"wid-{uuid.uuid4().hex[:8]}"
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["failed"])

        im = d2_components["instance_manager"]

        async def _raise(_iid, *, skip_repair=False, agent_id=None):
            raise RuntimeError("checkpoint unavailable")

        im._get_last_assistant_message_raw = AsyncMock(side_effect=_raise)
        original = _patch_resolver(
            d2_components["resolver"],
            _job_record(wid=wid, status="failed", result_summary=FROZEN),
        )
        try:
            notified = await notify_work_watchers(
                wid, "failed",
                error="boom",
                instance_manager=im,
                work_resolver=d2_components["resolver"],
                watcher_repo=d2_components["watcher_repo"],
            )
        finally:
            d2_components["resolver"].resolve_work = original

        assert notified == 1
        (body,) = _delivered_bodies(im)
        assert "Error: boom" in body
        assert f"Result:\n{FROZEN}" in body, (
            "D2 fail-open contract: when the authoritative seam raises, "
            "the resolver value renders rather than dropping the "
            "Result block or aborting the notify."
        )

    @pytest.mark.asyncio
    async def test_non_terminal_receipt_skips_seam(
        self, d2_components,
    ):
        engine = d2_components["engine"]
        _seed_instance(engine, instance_id="inst-d2")
        _seed_instance(engine, instance_id="watcher-1")
        wid = f"wid-{uuid.uuid4().hex[:8]}"
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["in_progress"])

        im = d2_components["instance_manager"]
        im._get_last_assistant_message_raw = AsyncMock(return_value=AUTHORITATIVE)
        original = _patch_resolver(
            d2_components["resolver"],
            _job_record(wid=wid, status="processing", result_summary=FROZEN),
        )
        try:
            notified = await notify_work_watchers(
                wid, "in_progress",
                instance_manager=im,
                work_resolver=d2_components["resolver"],
                watcher_repo=d2_components["watcher_repo"],
                progress="working",
            )
        finally:
            d2_components["resolver"].resolve_work = original

        assert notified == 1
        (body,) = _delivered_bodies(im)
        assert "Progress:" in body
        assert "Result:" not in body
        im._get_last_assistant_message_raw.assert_not_awaited()


# ── Tier-1 pins: the observer finalize outbox threads the payload ────────


class TestObserverOutboxPayloadThreading:
    """The ``_finalize_job`` post-commit outbox must thread
    ``db_result.result_summary`` (the authoritative final payload —
    same value the JOB_COMPLETED event row publish carries) on every
    per-kind receipt. Drives the REAL handler via the N8 hot-path
    harness (only ``_finalize_job_db_sync`` is mocked)."""

    def _build(self, *, instance_id: str, mirror_wid: str, task_wid: str):
        observer, mock_jqs, resolver = _build_observer_for_hot_path(
            instance_id=instance_id,
            mirror_work_id=mirror_wid,
            task_work_ids=[task_wid],
            job_id_for_instance=mirror_wid,
        )
        # Re-stub the sync DB write with a 6-arg-capable fake — the
        # N8 harness's own ``_fake_sync`` predates the
        # ``already_finalized_job_id`` parameter and raises
        # TypeError on the current call shape (pre-existing base
        # failure in test_n8_hot_path_pin.py, NOT this fix).
        def _fake_sync(
            job_id,
            instance_id,
            terminal_status,
            result_summary,
            error_message,
            _already_finalized=None,
        ):
            return _FinalizeJobResult(
                skip=False,
                terminal_status=terminal_status,
                job_id=job_id,
                instance_id=instance_id,
                parent_id=None,
                agent_id="worker",
                result_summary=result_summary,
                error_message=error_message,
                locks_released=1,
                instance_was_terminal=False,
            )

        observer._finalize_job_db_sync = MagicMock(side_effect=_fake_sync)
        # Surface the task work_id in the outbox's candidate set:
        # the harness's ``_task_repo.get_by_instance`` returns []
        # (N8's task pin drove the resolver directly for this
        # reason); the outbox only reads ``work_id`` off each row.
        from types import SimpleNamespace

        observer._instance_manager._task_repo.get_by_instance = MagicMock(
            return_value=[SimpleNamespace(work_id=task_wid)]
        )
        # Re-point the resolver's resolve_work at D2 records: a
        # mirror row (settled) and a task row whose terminal status
        # is ``failed`` so per_kind_status_for surfaces BOTH tokens.
        records = {
            mirror_wid: _job_record(
                wid=mirror_wid, status="settled", result_summary=FROZEN
            ),
            task_wid: _task_record(
                wid=task_wid, status="failed", result_summary=None
            ),
        }
        original = resolver.resolve_work
        resolver.resolve_work = MagicMock(
            side_effect=lambda wid: records.get(wid)
        )
        return observer, mock_jqs, original, resolver

    @pytest.mark.asyncio
    async def test_error_finalize_threads_payload_on_both_receipts(self):
        instance_id = f"inst-d2-{uuid.uuid4().hex[:8]}"
        mirror_wid = f"job-d2-mirror-{uuid.uuid4().hex[:8]}"
        task_wid = f"task-d2-{uuid.uuid4().hex[:8]}"

        observer, mock_jqs, original, resolver = self._build(
            instance_id=instance_id, mirror_wid=mirror_wid, task_wid=task_wid,
        )
        try:
            event = {
                "event_type": "instance_lifecycle",
                "data": {
                    "instance_id": instance_id,
                    "status": "error",
                    "error": None,
                },
            }
            await observer._process_event(event)
        finally:
            resolver.resolve_work = original

        calls = mock_jqs.notify_watchers.await_args_list
        assert calls, "post-commit outbox must fire notify_watchers"

        task_call = next(c for c in calls if c.args[0] == task_wid)
        assert task_call.args[1] == "failed"
        assert task_call.kwargs.get("error") == "Unknown error"
        assert task_call.kwargs.get("result_summary") == (
            "hot-path pin assistant message"
        ), (
            "D2: the failed receipt must thread the authoritative "
            "final payload alongside the error string. Pre-fix the "
            "failed lane passed ``error=`` ONLY — the notify-side "
            "resolver fallback then rendered the frozen first-turn "
            "text under ``Result:`` (the task-16551 incident)."
        )

        mirror_call = next(c for c in calls if c.args[0] == mirror_wid)
        assert mirror_call.args[1] == "settled"
        assert "error" not in mirror_call.kwargs or (
            mirror_call.kwargs.get("error") is None
        )
        assert mirror_call.kwargs.get("result_summary") == (
            "hot-path pin assistant message"
        ), (
            "D2: the settled receipt on an ERRORED mission must carry "
            "the authoritative payload — NOT the error string in the "
            "``Result:`` slot (the failed-lane Result:/Error: "
            "cosmetic skew)."
        )

    @pytest.mark.asyncio
    async def test_completed_finalize_threads_payload(self):
        instance_id = f"inst-d2-{uuid.uuid4().hex[:8]}"
        mirror_wid = f"job-d2-mirror-{uuid.uuid4().hex[:8]}"
        task_wid = f"task-d2-{uuid.uuid4().hex[:8]}"

        observer, mock_jqs, original, resolver = self._build(
            instance_id=instance_id, mirror_wid=mirror_wid, task_wid=task_wid,
        )
        try:
            event = {
                "event_type": "instance_lifecycle",
                "data": {
                    "instance_id": instance_id,
                    "status": "completed",
                    "error": None,
                },
            }
            await observer._process_event(event)
        finally:
            resolver.resolve_work = original

        calls = mock_jqs.notify_watchers.await_args_list
        assert calls
        mirror_call = next(c for c in calls if c.args[0] == mirror_wid)
        assert mirror_call.args[1] == "settled"
        assert mirror_call.kwargs.get("result_summary") == (
            "hot-path pin assistant message"
        )
