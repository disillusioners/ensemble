"""DEFECT-1 (P2) — ``Result:`` block on task-kind completed events.

Live evidence (2026-09-24 E2E, real Ari on real dev daemon at 5f4e35b0):
6/6 reproducible — task-kind ``completed ✓`` events render with NO
``Result:`` block although the row carries the content. Tester's
isolation cites ``daemon/services/work_notifier.py:295-304``
(effective_result resolution) and ``daemon/services/job_feedback_observer.py:2078``
(outbox threading).

This file pins:

1. ``test_parse_task_result_summary_extracts_content_key`` — the
   resolver helper must surface CLEAN TEXT from ``task.result``
   when the JSON body carries a ``content`` key (the v0.13.9
   ``complete_task`` producer stamp shape:
   ``{"success": true, "message_id": "x", "content": "DONE"}``).
   Pre-fix the helper dumped the WHOLE dict into the watcher
   body — the ``Result:`` line carried JSON instead of the agent's
   text. Post-fix the text is the assistant's last message.

2. ``test_notify_work_watchers_completed_body_renders_result_text`` —
   end-to-end: a TASK-side row whose ``task.result`` carries the
   v0.13.9 JSON envelope renders a ``Result:\\n<clean text>`` line.
   No JSON dump in the body.

3. ``test_notify_work_watchers_settled_kind_no_result_block`` —
   GUARD: M3 mission-class design — message-kind ``settled ✓``
   receipts have a by-design NO-Result-block shape (the
   ``settled`` glyph is the only payload the parser needs).
   The ``else`` branch in ``_STATUS_DISPLAY_MAP`` must NOT
   thread a ``Result:`` line for settled receipts. The fix
   must not regress this M3 contract.

4. ``test_notify_work_watchers_failed_carries_error_block`` —
   GUARD: failed jobs still put error content in the ``Error:``
   slot (not in ``Result:``). This is the inverse contract of
   the F2 fix at commit 4b8e5e1c.

5. ``test_notify_work_watchers_keyword_only_slot_discipline`` —
   F2 pin: ``notify_watchers`` / ``notify_work_watchers``
   callers must use keyword args (``result_summary=`` /
   ``error=``). A regression to POSITIONAL passing would land
   ``DONE`` text into the ``Error:`` slot — the very bug the
   F2 fix closed. The test inspects the produced message
   body, not the call kwargs, so it catches a regression
   that drops the F2 discipline at any layer (param-passing,
   notifier, or formatter).

Recipe: real ``JobWatcherRepository`` + ``TaskRepository`` +
patched ``WorkResolverService`` per ``test_work_notifier_n1_pin.py``
so the actual SQL atomicity of the CAS path is exercised.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.job_queue.watcher_repository import JobWatcherRepository
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import TaskRepository
from daemon.services.work_notifier import notify_work_watchers
from daemon.services.work_resolver import (
    WorkRecord,
    WorkResolverService,
    _parse_task_result_summary,
)


# ── Fixtures + helpers ────────────────────────────────────────────────────


@pytest.fixture
def defect1_engine(tmp_path):
    db_path = tmp_path / "defect1.db"
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


def _seed_instance(engine, *, instance_id: str, agent_id: str = "worker") -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(engine) as s:
        s.add(Instance(
            instance_id=instance_id, agent_id=agent_id,
            agent_dir="/tmp/w", agent_name=agent_id,
            project_id="p1", status="completed",
            created_at=now_iso, updated_at=now_iso,
            paused_at=None, parent_id=None,
        ))
        s.commit()


def _seed_task(
    engine, *, work_id: str, instance_id: str,
    status: str = TaskStatus.COMPLETED.value,
    result: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as s:
        s.add(Task(
            work_id=work_id, task_type="process_report",
            instance_id=instance_id, status=status,
            created_at=now, completed_at=now,
            is_deferred=False, result=result,
        ))
        s.commit()


def _add_watch(engine, *, work_id: str, instance_id: str, watch_events) -> None:
    with Session(engine) as s:
        s.add(JobWatcher(
            job_id=work_id, instance_id=instance_id,
            watch_events=watch_events,
        ))
        s.commit()


def _patch_resolver_task(engine, resolver, *, wid: str, result_summary: str):
    """Synthesize a TASK-side WorkRecord — kind="report" so per_kind_status_for
    surfaces ``completed`` for task-kind (vs ``settled`` for message-kind).
    """
    record = WorkRecord(
        work_id=wid, kind="report", status="completed",
        instance_id="inst-test", project_id="p1",
        agent_id="worker", result_summary=result_summary,
        error=None, created_at=datetime.now(timezone.utc),
        job_type=None, mission_liveness=None,
    )
    original = resolver.resolve_work
    resolver.resolve_work = MagicMock(return_value=record)
    return original


def _patch_resolver_mirror(engine, resolver, *, wid: str):
    """Synthesize a message-kind JobItem WorkRecord — kind="job",
    job_type="message". The mirror row's per_kind_status_for returns
    ``settled`` (M3 mission-class design contract)."""
    record = WorkRecord(
        work_id=wid, kind="job", status="settled",
        instance_id="inst-test", project_id="p1",
        agent_id="worker", result_summary=None,
        error=None, created_at=datetime.now(timezone.utc),
        job_type="message", mission_liveness=None,
    )
    original = resolver.resolve_work
    resolver.resolve_work = MagicMock(return_value=record)
    return original


class _NoOpJobRepo:
    def get(self, _job_id):
        return None

    def __getattr__(self, name):
        return lambda *_a, **_kw: None


@pytest.fixture
def defect1_components(defect1_engine):
    watcher_repo = JobWatcherRepository(defect1_engine)
    task_repo = TaskRepository(defect1_engine)
    instance_repo = SQLModelInstanceRepository(defect1_engine)
    resolver = WorkResolverService(task_repo, _NoOpJobRepo(), instance_repo)
    instance_manager = MagicMock()
    instance_manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-defect1")
    )
    return {
        "engine": defect1_engine,
        "watcher_repo": watcher_repo,
        "task_repo": task_repo,
        "resolver": resolver,
        "instance_manager": instance_manager,
    }


def _make_task(*, work_id: str, instance_id: str, result: str | None) -> Task:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return Task(
        work_id=work_id, task_type="process_report",
        instance_id=instance_id, status=TaskStatus.COMPLETED.value,
        created_at=now, completed_at=now,
        is_deferred=False, result=result,
    )


# ── DEFECT-1 pins ─────────────────────────────────────────────────────────


class TestParseTaskResultSummary:
    """The resolver helper surfaces ``task.result`` to the work_resolver
    WorkRecord. When the JSON envelope carries a ``content`` key (the
    v0.13.9 producer stamp — ``complete_task`` wrote
    ``{"success": True, "message_id": ..., "content": "DONE"}``),
    the helper must surface the ``content`` text — NOT the whole JSON
    dump. The watcher body's ``Result:`` line then carries clean text.
    """

    def test_extracts_content_key_when_envelope_carries_it(self):
        """v0.13.9 envelope shape ``{success, message_id, content}``
        → ``_parse_task_result_summary`` returns ``content`` verbatim
        (no json.dumps, no whole-envelope dump)."""
        envelope = json.dumps({
            "success": True,
            "message_id": "msg-1",
            "content": "DONE",
        })
        task = _make_task(
            work_id="wid-test-1", instance_id="inst-test-1",
            result=envelope,
        )
        result = _parse_task_result_summary(task)
        assert result == "DONE", (
            f"DEFECT-1 fix: task.result JSON envelope carries a "
            f"``content`` key — the helper must surface the clean "
            f"text (the agent's last assistant message), not the "
            f"whole-envelope json.dumps. Got {result!r}."
        )

    def test_extracts_content_key_for_non_string_content(self):
        """If ``content`` is a non-string type, json.dumps the inner
        value (preserves the principle that result_summary is a
        JSON string when not a string)."""
        envelope = json.dumps({"content": {"nested": "value"}})
        task = _make_task(
            work_id="wid-test-1b", instance_id="inst-test-1b",
            result=envelope,
        )
        result = _parse_task_result_summary(task)
        assert result == '{"nested": "value"}'

    def test_content_none_falls_back_to_envelope_dump(self):
        """Edge pin: envelope carries ``content: None``. The helper's
        guard is ``content is not None`` — a None content does NOT
        take the clean-text branch; it falls back to the whole-dict
        ``json.dumps`` (reachable in production for content-less
        completions)."""
        payload = {
            "success": True,
            "message_id": "msg-2",
            "content": None,
        }
        envelope = json.dumps(payload)
        task = _make_task(
            work_id="wid-test-1c", instance_id="inst-test-1c",
            result=envelope,
        )
        result = _parse_task_result_summary(task)
        assert result == json.dumps(payload), (
            f"DEFECT-1 edge: envelope with ``content: None`` must fall "
            f"back to the whole-dict json.dumps (guard is ``content "
            f"is not None``), NOT surface None or clean text. "
            f"Got {result!r}."
        )


class TestNotifyWorkWatchersResultBlock:
    """End-to-end: a TASK-side row with v0.13.9 envelope content
    produces a ``[JOB_EVENT]`` body with a clean ``Result:\\n<text>``
    line — no JSON dump. The settlement/Error/F2 discipline are
    pinned alongside.
    """

    @pytest.mark.asyncio
    async def test_completed_task_kind_renders_clean_result_text(
        self, defect1_components,
    ):
        """TASK-side ``completed`` body must render
        ``Result:\\nDONE`` (clean text), not
        ``Result:\\n{\"success\": true, ...}`` (whole envelope).

        Real resolver path (NO patch): the Task row carries the
        v0.13.9 envelope ``{success, message_id, content: "DONE"}``.
        The resolver's ``resolve_work`` reads the Task through
        ``_parse_task_result_summary`` — which MUST extract the
        ``content`` key (post-fix) so the body surfaces ``DONE``
        clean text. Pre-fix the helper returned the whole JSON
        dump; the test fails on ````Result:\\nDONE`` absent.``
        """
        engine = defect1_components["engine"]
        watcher_repo = defect1_components["watcher_repo"]
        resolver = defect1_components["resolver"]
        instance_manager = defect1_components["instance_manager"]

        wid = f"wid-{uuid4().hex[:8]}"
        _seed_instance(engine, instance_id="inst-test")
        _seed_instance(engine, instance_id="watcher-1")
        envelope = json.dumps({
            "success": True,
            "message_id": "msg-1",
            "content": "DONE",
        })
        _seed_task(engine, work_id=wid, instance_id="inst-test", result=envelope)
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["completed"])

        # Real resolver path — no patch. The seeded Task row carries
        # the envelope; ``resolve_work`` (via ``_parse_task_result_summary``)
        # must surface clean text.
        notified = await notify_work_watchers(
            wid, "completed", instance_manager=instance_manager,
            work_resolver=resolver, watcher_repo=watcher_repo,
            # Caller does NOT supply result_summary — exercise the
            # resolver's fallback through the helper.
        )

        assert notified == 1
        call = instance_manager.enqueue_message.await_args
        msg = call.kwargs["message"]
        assert "[JOB_EVENT]" in msg
        assert "completed ✓" in msg
        # The Result: line carries the clean text.
        assert "Result:\nDONE" in msg, (
            "DEFECT-1: task-kind completed body must carry a "
            "``Result:\\n<clean text>`` line. Pre-fix the resolver "
            "surfaced the whole JSON envelope so the body had "
            "either no Result: line or one whose value was a JSON "
            "blob — neither of which surfaces the agent's last "
            "assistant message."
        )
        # And NO JSON envelope dump in the body.
        assert '"success": true' not in msg, (
            "DEFECT-1 trace cleanup: the message body must NOT "
            "carry the whole JSON envelope (it surfaces the clean "
            "text via the resolver's ``content``-key extraction)."
        )

    @pytest.mark.asyncio
    async def test_settled_message_kind_no_result_block(
        self, defect1_components,
    ):
        """GUARD: M3 mission-class design — message-kind ``settled ✓``
        receipts have a by-design NO-Result-block shape (the
        ``settled`` glyph is sufficient; no extra body). The fix
        must not regress this contract — the message body must
        carry the header + Agent line and NOTHING ELSE."""
        engine = defect1_components["engine"]
        watcher_repo = defect1_components["watcher_repo"]
        resolver = defect1_components["resolver"]
        instance_manager = defect1_components["instance_manager"]

        wid = f"wid-{uuid4().hex[:8]}"
        _seed_instance(engine, instance_id="inst-test")
        _seed_instance(engine, instance_id="watcher-1")
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["settled"])

        original = _patch_resolver_mirror(engine, resolver, wid=wid)
        try:
            notified = await notify_work_watchers(
                wid, "settled", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
            )
        finally:
            resolver.resolve_work = original

        assert notified == 1
        call = instance_manager.enqueue_message.await_args
        msg = call.kwargs["message"]
        assert "[JOB_EVENT]" in msg
        assert "settled ✓" in msg
        # GUARD: NO Result: block for settled.
        assert "Result:" not in msg, (
            "M3 mission-class GUARD: message-kind ``settled ✓`` "
            "receipts must NOT carry a ``Result:`` block — the "
            "settled glyph is the body. Pre-fix regression test."
        )
        # And no Error: line either (no error keyword passed).
        assert "Error:" not in msg

    @pytest.mark.asyncio
    async def test_failed_completed_body_carries_error_block_not_result(
        self, defect1_components,
    ):
        """Inverse contract: a failed terminal body must carry the
        error content in the ``Error:`` slot, NOT in ``Result:``.
        This is the F2 discipline at 4b8e5e1c pin: failed →
        ``error=``, everything else → ``result_summary=``. A
        positional-pass regression would slip error text into
        ``Result:``."""
        engine = defect1_components["engine"]
        watcher_repo = defect1_components["watcher_repo"]
        resolver = defect1_components["resolver"]
        instance_manager = defect1_components["instance_manager"]

        wid = f"wid-{uuid4().hex[:8]}"
        _seed_instance(engine, instance_id="inst-test")
        _seed_instance(engine, instance_id="watcher-1")
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["failed"])

        original = _patch_resolver_task(
            engine, resolver, wid=wid, result_summary=None,
        )
        try:
            notified = await notify_work_watchers(
                wid, "failed", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
                error="max retries exceeded",
            )
        finally:
            resolver.resolve_work = original

        assert notified == 1
        call = instance_manager.enqueue_message.await_args
        msg = call.kwargs["message"]
        assert "[JOB_EVENT]" in msg
        assert "failed ✗" in msg
        # Error text in Error: slot.
        assert "Error: max retries exceeded" in msg
        # NOT slipped into Result: slot — F2 contract.
        assert "Result:" not in msg, (
            "F2 GUARD: a failed terminal body's error text must "
            "NOT appear under ``Result:``. A regression to "
            "positional passing would land ``error`` in "
            "notify_watchers' third positional (``error=``), but "
            "only via the call site — the message body itself "
            "should faithfully reflect the slot mapping."
        )

    @pytest.mark.asyncio
    async def test_completed_caller_kwarg_overrides_with_no_json(
        self, defect1_components,
    ):
        """F2 pin: when the caller passes ``result_summary=`` (the
        observer's pre-fetched assistant text), the message body's
        ``Result:`` line carries that text — NOT the resolver's
        fallback (which might be a JSON dump). This is the
        primary path the observer takes; pin that the kwarg path
        produces CLEAN text even if the resolver returns the
        envelope dump (which would be the previous fallback)."""
        engine = defect1_components["engine"]
        watcher_repo = defect1_components["watcher_repo"]
        resolver = defect1_components["resolver"]
        instance_manager = defect1_components["instance_manager"]

        wid = f"wid-{uuid4().hex[:8]}"
        _seed_instance(engine, instance_id="inst-test")
        _seed_instance(engine, instance_id="watcher-1")
        _add_watch(engine, work_id=wid, instance_id="watcher-1",
                   watch_events=["completed"])

        # Resolver fallback returns the whole JSON envelope (pre-fix
        # behaviour); the caller's kwarg MUST override.
        envelope = json.dumps({
            "success": True, "message_id": "msg-1", "content": "DONE",
        })
        original = _patch_resolver_task(
            engine, resolver, wid=wid, result_summary=envelope,
        )
        try:
            notified = await notify_work_watchers(
                wid, "completed", instance_manager=instance_manager,
                work_resolver=resolver, watcher_repo=watcher_repo,
                result_summary="CLEAN ASSISTANT TEXT FROM KWARG",
            )
        finally:
            resolver.resolve_work = original

        assert notified == 1
        call = instance_manager.enqueue_message.await_args
        msg = call.kwargs["message"]
        # Kwarg overrides resolver fallback.
        assert "Result:\nCLEAN ASSISTANT TEXT FROM KWARG" in msg
        assert envelope not in msg, (
            "F2 kwarg override GUARD: the caller's ``result_summary`` "
            "kwarg MUST win over the resolver's fallback — even "
            "when the fallback is the whole JSON envelope."
        )
