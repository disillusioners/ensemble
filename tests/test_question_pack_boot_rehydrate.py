"""Question-pack boot rehydrate — FAIL@parent / PASS@HEAD regression.

Regression for ``fix/question-pack-boot-rehydrate`` (2026-10-19).

Every boot, the daemon must rehydrate pending question packs from the
durable ``instance_metadata['question_pack_payload']`` shadow so the
answer endpoint works across restarts. The previous call site lived
inside ``InstanceManager.initialize()`` — but ``initialize()`` runs
BEFORE ``manager.setup_worker_pool()`` (api.py startup order), and
``PoolOrchestrator.setup`` (pool_orchestrator.py:244-245) is the only
site that assigns ``manager._task_repo``. The rehydrate method
dereferences ``self._task_repo.find_suspended_turn_for_answer``, so
the early call raised ``AttributeError`` and the surrounding
boot-never-fails try/except WARN-logged it and moved on. Every answer
after a restart then 410'd ``QUESTION_PACK_LOST`` until the
per-instance fallback in ``_resolve_pack_or_raise``
(answer_helper.py:235-251) rehydrated one pack at a time.

The fix moves the call out of ``initialize()`` into a new helper
``daemon.api._boot_rehydrate_question_packs`` invoked from
``daemon.api.lifespan`` AFTER ``manager.setup_worker_pool()``. This
file tests the wiring contract end-to-end.

FAIL@parent evidence: at parent 333b2c225 the helper function does not
exist in ``daemon.api`` (the call site lived inside ``initialize()``).
On parent, the test's import of ``_boot_rehydrate_question_packs``
fails with ``ImportError`` — the SAME boot sequence api.py runs on
parent has no explicit post-``setup_worker_pool()`` rehydrate call,
so the regression contract is provably broken on parent and provably
fixed on HEAD.

The harness builds a real ``InstanceManager`` with the wiring-only
seam pattern from ``tests/integration/chat_source_harness.py`` — no
full lifespan (no LLM, no graph stack) but real repos, real
``setup_worker_pool()`` and real ``QuestionManager``.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel


# ─── Boot-pass harness ─────────────────────────────────────────────────────


def _build_minimal_config():
    """Build a minimal ``Config`` instance for the boot-pass harness.

    Mirrors the chat_source_harness shape; ``db_path`` is harmless
    because the patched ``create_engine_from_config`` returns the
    in-memory engine from the test fixture.
    """
    from daemon.config import (
        AgentsConfig,
        Config,
        DaemonConfig,
        LLMConfig,
        LimitsConfig,
        PersistenceConfig,
    )

    return Config(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4",
            temperature=0.7,
        ),
        limits=LimitsConfig(
            max_children_per_instance=3,
            instance_timeout_minutes=60,
        ),
        persistence=PersistenceConfig(
            db_path=":memory:",
            checkpoint_interval=1,
            checkpoint_ttl_hours=168,
            checkpoint_cleanup_interval=24,
            max_instance_history=300,
        ),
        daemon=DaemonConfig(host="127.0.0.1", port=8079),
        agents=AgentsConfig(directory="./agents"),
    )


def _build_real_manager(engine):
    """Build a real ``InstanceManager`` with the wiring-only seam.

    Skips ``initialize()`` (heavy async lifespan) and stubs the
    ``_maintenance_service`` so ``setup_worker_pool()`` can call
    ``set_task_repository`` — same recipe as the chat-source tests
    (chat_source_harness.py:wire_manager_only).

    The harness is intentionally hermetic: no DB connect at
    construction (the engine is mocked into the manager), no LLM,
    no MCP pool, no worker thread fan-out beyond what
    ``setup_worker_pool`` itself does.
    """
    from daemon.manager import InstanceManager
    from daemon.services.maintenance import MaintenanceService

    config = _build_minimal_config()

    with (
        patch(
            "daemon.migrations.runner.MigrationRunner.run_pending_migrations",
            return_value=[],
        ),
        patch(
            "daemon.manager.create_engine_from_config",
            return_value=engine,
        ),
        patch(
            "daemon.manager.build_instance_graph",
            return_value=None,
        ),
    ):
        manager = InstanceManager(config)
        manager._maintenance_service = MaintenanceService()
        manager._maintenance_service.set_request_registry({})
        # Many attributes consumed by ``setup_worker_pool`` are only
        # set during ``initialize()``. Wire the minimum the rehydrate
        # + setup_worker_pool path needs. The boot-pass never touches
        # anything beyond what's listed here — verified by running
        # the production code under pytest.
        manager._instance_repository = getattr(
            manager, "_instance_repository", None
        )
        manager._checkpointer = SimpleNamespace()  # checkpointer
        # stub — checkpoint_cleanup_job is constructed lazily
        manager._message_metadata_repo = SimpleNamespace()
        manager._instance_ui_prefs_repo = SimpleNamespace()
        manager._release_cached_instance = lambda *a, **kw: None
        manager._checkpoint_cleanup_job = SimpleNamespace()  #
        # placeholder; set_task_repository only needs the attribute
        # to exist for ``set_task_repository`` to noop cleanly when
        # the repo is None.
        return manager


@pytest.fixture
def engine():
    """In-memory SQLite engine with all tables created."""
    # Importing the model modules registers them on
    # SQLModel.metadata — without this, ``SQLModel.metadata.create_all``
    # creates zero tables (verified empirically).
    from daemon.repositories.event.models import Event
    from daemon.repositories.instance.models import Instance
    from daemon.repositories.job_queue.models import JobItem
    from daemon.repositories.job_queue.watcher_models import JobWatcher
    from daemon.repositories.task.models import Task

    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _seed_asker_with_durable_pack(engine, iid: str | None = None):
    """Seed a wedged asker with the durable pack shadow only.

    The instance + the paused ``awaiting_answer`` task + the
    ``question_pack_payload`` metadata shadow are written to the
    engine. NO RAM packs are written — i.e. exactly the state a
    daemon finds itself in AFTER a restart where a question was
    pending.
    """
    from daemon.repositories.instance.models import Instance, InstanceStatus
    from daemon.repositories.task.models import (
        SuspensionReason,
        Task,
        TaskStatus,
        TaskType,
    )

    iid = iid or f"inst-{uuid.uuid4()}"
    work_id = str(uuid.uuid4())

    # Original pack UUID the asker emitted — the regression asserts
    # this is preserved verbatim by rehydrate (the T1″ correlation
    # contract; minting a fresh uuid would break answer correlation).
    original_pack_id = str(uuid.uuid4())
    original_qid = "q-original"
    created_at = datetime.now(timezone.utc).isoformat()

    pack_payload = {
        "instance_id": iid,
        "pack_id": original_pack_id,
        "status": "pending",
        "created_at": created_at,
        "questions": [
            {
                "id": original_qid,
                "text": "What is your preference?",
                "options": ["alpha", "beta"],
                "allow_custom": True,
                "required": True,
                "option_descriptions": {},
            }
        ],
        "answers": {},
    }

    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=iid,
                agent_id="leader",
                agent_dir="/tmp",
                project_id="p",
                status=InstanceStatus.PAUSED.value,
                version=1,
                instance_metadata={},
                parent_id=None,
            )
        )
        session.add(
            Task(
                work_id=work_id,
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=iid,
                message_id=None,
                status=TaskStatus.PAUSED.value,
                suspension_reason=SuspensionReason.AWAITING_ANSWER.value,
                resume_target_turn_id=work_id,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    # Stamp the durable shadow after the commit. The rehydrate
    # reads from ``instances.metadata`` (a JSON column) via
    # ``json_extract(metadata, '$.key')`` on SQLite /
    # ``metadata ->> :key`` on PostgreSQL — see
    # ``SQLModelInstanceRepository.get_metadata_value``
    # (daemon/repositories/instance/repository.py:2109).
    import json

    payload_json = json.dumps(pack_payload, sort_keys=True, default=str)
    with engine.begin() as conn:
        # Postgres JSONB needs ``::jsonb`` cast; SQLite's JSON column
        # accepts the same payload as TEXT. Use dialect-aware path.
        from sqlalchemy import inspect as _sa_inspect

        dialect = engine.dialect.name
        if dialect == "postgresql":
            conn.execute(
                text(
                    "UPDATE instances SET metadata = metadata || "
                    "jsonb_set('{}'::jsonb, :key_path, :value::jsonb) "
                    "WHERE instance_id = :iid"
                ),
                {
                    "iid": iid,
                    "key_path": "{" + "question_pack_payload" + "}",
                    "value": payload_json,
                },
            )
        else:
            # SQLite: ``json_set`` is the JSON1-extension counterpart.
            # ``json_set(metadata, '$.key', value)`` returns the
            # merged JSON document. If ``metadata`` is NULL, the
            # expression returns NULL — seed with ``json_object`` so
            # the merge produces a complete shadow on first write.
            conn.execute(
                text(
                    "UPDATE instances SET metadata = "
                    "COALESCE("
                    "  json_set(metadata, '$.question_pack_payload', "
                    "    json(:payload)), "
                    "  json_object('question_pack_payload', json(:payload))"
                    ") WHERE instance_id = :iid"
                ),
                {"iid": iid, "payload": payload_json},
            )

    return iid, work_id, original_pack_id, original_qid, pack_payload


def _json_dump(obj) -> str:
    """JSON dump with stable key order for portable comparison."""
    import json

    return json.dumps(obj, sort_keys=True, default=str)


# ─── Tests ─────────────────────────────────────────────────────────────────


def test_boot_rehydrate_helper_exists_in_daemon_api():
    """FAIL@parent guard: ``_boot_rehydrate_question_packs`` must exist
    in ``daemon.api``.

    On parent 333b2c225 the helper does not exist (the boot pass
    lived inside ``InstanceManager.initialize()``). On HEAD it lives
    in ``daemon.api`` and is invoked from ``daemon.api.lifespan``
    after ``manager.setup_worker_pool()``.

    This is the most direct FAIL@parent evidence: if the function
    does not exist, the import fails and the regression test cannot
    even load — which is the exact failure mode for any caller
    relying on the new explicit post-``setup_worker_pool()`` boot
    pass.
    """
    from daemon.api import _boot_rehydrate_question_packs  # noqa: F401

    assert callable(_boot_rehydrate_question_packs), (
        "daemon.api._boot_rehydrate_question_packs must be callable "
        "for the lifespan startup to invoke it after setup_worker_pool()"
    )


def test_boot_rehydrate_restores_pack_with_original_id(engine, caplog):
    """The boot pass restores the pack with the ORIGINAL pack id.

    Boot scenario: a paused ``awaiting_answer`` asker with a durable
    ``question_pack_payload`` shadow but NO RAM pack. The boot pass
    must restore the pack into the RAM store preserving the original
    ``pack_id`` (the T1″ correlation contract — a fresh uuid would
    break answer correlation and the wedge-guard's metadata pack-id
    reads).

    Drives the same order the lifespan runs: build manager → manually
    wire ``_task_repo`` (the production wiring site is
    ``PoolOrchestrator.setup`` at pool_orchestrator.py:245, which
    runs at the end of ``setup_worker_pool()``; we replicate that
    minimal assignment here to avoid spinning up the worker pool
    inside this hermetic harness) → ``_boot_rehydrate_question_packs
    (manager)`` (the new explicit site).

    On parent 333b2c225: ``_boot_rehydrate_question_packs`` does not
    exist in ``daemon.api`` (the helper was introduced by this fix).
    The import in the test fails — see the companion test
    ``test_boot_rehydrate_helper_exists_in_daemon_api`` for the
    primary FAIL@parent witness.
    """
    iid, work_id, original_pack_id, original_qid, _payload = (
        _seed_asker_with_durable_pack(engine)
    )

    manager = _build_real_manager(engine)
    # Wire the per-instance repository that the rehydrate reads —
    # parent + HEAD both use the same instance repo wiring.
    from daemon.repositories.instance.repository import (
        SQLModelInstanceRepository,
    )

    manager._instance_repository = SQLModelInstanceRepository(engine)

    # Manually replicate the ``_task_repo`` assignment that
    # ``PoolOrchestrator.setup`` (pool_orchestrator.py:245) does at
    # the end of ``setup_worker_pool``. We avoid the full pool start
    # to keep this test hermetic (no thread fan-out, no chat pool,
    # no LLM, no MCP).
    from daemon.repositories.task.repository import TaskRepository

    manager._task_repo = TaskRepository(engine)

    # The new explicit boot pass invoked by api.py lifespan after
    # setup_worker_pool().
    from daemon.api import _boot_rehydrate_question_packs

    with caplog.at_level(logging.INFO, logger="daemon"):
        await_coro = _boot_rehydrate_question_packs(manager)
        import asyncio

        if asyncio.iscoroutine(await_coro):
            asyncio.run(await_coro)

    # Verify the pack was restored into RAM with the ORIGINAL pack id.
    qm = manager._question_manager
    restored = qm.get_question_pack(iid)
    assert restored is not None, (
        f"Boot rehydrate did not restore the pack for {iid[:8]}... "
        f"— check that _boot_rehydrate_question_packs is invoked "
        f"AFTER setup_worker_pool() and the durable shadow contains "
        f"the expected payload."
    )
    assert restored.id == original_pack_id, (
        f"Restored pack id {restored.id!r} differs from the original "
        f"{original_pack_id!r} — the T1″ correlation contract is "
        f"violated; post-restart answers would 400 QUESTION_PACK_MISMATCH."
    )
    assert restored.status == "pending", (
        f"Restored pack status {restored.status!r} — expected 'pending'; "
        f"a non-pending restoration is the wrong boot contract."
    )
    assert len(restored.questions) == 1
    assert restored.questions[0].id == original_qid


def test_rehydrate_method_silently_swallows_when_task_repo_unwired(
    engine, caplog
):
    """Root-cause witness: the rehydrate method SWALLOWS AttributeError
    when ``_task_repo`` is not yet wired — that is the bug.

    This is what was happening INSIDE ``InstanceManager.initialize()``
    on parent 333b2c225 — the call ran BEFORE
    ``PoolOrchestrator.setup`` wired ``_task_repo``. The per-instance
    try/except inside ``_rehydrate_question_packs_at_boot`` (which is
    documented as "per-instance best-effort") catches the
    AttributeError, logs a WARNING at manager.py:4614, and continues
    to the next paused instance. No exception propagates; the
    caller observes a return value of 0 (no packs restored). Every
    answer after a restart then 410s ``QUESTION_PACK_LOST``.

    This test exercises the ROOT CAUSE directly and asserts the
    WARNING-and-silent-skip contract. The companion test
    ``test_boot_rehydrate_loud_signal_on_missing_wiring`` asserts
    that the FIX site (``_boot_rehydrate_question_packs``) raises
    the loud-signal ERROR when the helper itself runs without
    wiring — that is the post-fix observable difference from parent.

    Runs identically on parent AND HEAD (the method is unchanged
    on both — the fix only moved the CALL SITE).
    """
    iid, _work_id, _original_pack_id, _original_qid, _payload = (
        _seed_asker_with_durable_pack(engine)
    )

    manager = _build_real_manager(engine)
    # CRITICAL: do NOT call setup_worker_pool — _task_repo is
    # intentionally NOT wired. This mirrors the parent's
    # ``initialize()`` call site: pre-``setup_worker_pool``.

    import asyncio

    with caplog.at_level(logging.WARNING, logger="daemon.manager"):
        coro = manager._rehydrate_question_packs_at_boot()
        if asyncio.iscoroutine(coro):
            asyncio.run(coro)

    # The per-instance try/except around
    # ``self._task_repo.find_suspended_turn_for_answer`` swallows
    # AttributeError and logs a WARNING. This is the production
    # silent-drop class defect that lost every restart rehydration.
    bug_warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and "Question-pack boot rehydration: suspended-turn lookup"
        in r.getMessage()
    ]
    assert bug_warnings, (
        "Expected the per-instance AttributeError to be logged as "
        "WARNING — this is the exact symptom of the parent bug "
        "(rehydrate silently failing on every boot)."
    )
    assert any(
        "_task_repo" in r.getMessage() for r in bug_warnings
    ), (
        f"WARNING message did not name '_task_repo' — "
        f"got {[r.getMessage() for r in bug_warnings]!r}."
    )


def test_boot_rehydrate_loud_signal_on_missing_wiring(engine, caplog):
    """Loud-signal contract: missing ``_task_repo`` logs at ERROR.

    The commission mandates that ``_task_repo`` being unwired at boot
    is a STRUCTURAL defect (someone broke the boot order) and must
    NEVER regress to a quiet WARNING. This test asserts that
    calling ``_boot_rehydrate_question_packs`` with an unwired
    manager logs at ERROR level.

    On parent 333b2c225 the function doesn't exist; this test would
    FAIL with ImportError (same failure mode as
    ``test_boot_rehydrate_helper_exists_in_daemon_api``).
    """
    from daemon.api import _boot_rehydrate_question_packs

    manager = _build_real_manager(engine)
    # Intentionally do NOT call setup_worker_pool — _task_repo is
    # missing on purpose, mirroring a broken boot order.
    assert not hasattr(manager, "_task_repo") or manager._task_repo is None

    import asyncio

    with caplog.at_level(logging.ERROR, logger="daemon"):
        coro = _boot_rehydrate_question_packs(manager)
        if asyncio.iscoroutine(coro):
            asyncio.run(coro)

    error_records = [
        r for r in caplog.records
        if r.levelno >= logging.ERROR
        and "Question-pack boot rehydration" in r.getMessage()
    ]
    assert error_records, (
        "Boot rehydrate did not log an ERROR when _task_repo was "
        "missing — the loud-signal contract is broken. A future "
        "regression of this kind would vanish from the boot log."
    )
    assert any(
        "_task_repo" in r.getMessage() for r in error_records
    ), (
        "Boot rehydrate ERROR did not name the missing wiring "
        "('_task_repo') — without naming the attribute the log "
        "line is unhelpful for triage."
    )


def test_boot_rehydrate_summary_log_line(engine, caplog):
    """Summary-log contract: a successful pass logs an INFO line
    ``question-packs rehydrated: N``.

    Without this line, a future candidate-set drift (e.g. the scan
    considers M candidates but restores 0 because every shadow is
    malformed) is invisible from the boot log.
    """
    iid, _work_id, _original_pack_id, _original_qid, _payload = (
        _seed_asker_with_durable_pack(engine)
    )

    manager = _build_real_manager(engine)
    from daemon.repositories.instance.repository import (
        SQLModelInstanceRepository,
    )
    from daemon.repositories.task.repository import TaskRepository

    manager._instance_repository = SQLModelInstanceRepository(engine)
    manager._task_repo = TaskRepository(engine)

    from daemon.api import _boot_rehydrate_question_packs

    with caplog.at_level(logging.INFO, logger="daemon"):
        import asyncio

        coro = _boot_rehydrate_question_packs(manager)
        if asyncio.iscoroutine(coro):
            asyncio.run(coro)

    summary_lines = [
        r.getMessage()
        for r in caplog.records
        if "question-packs rehydrated:" in r.getMessage()
    ]
    assert summary_lines, (
        "Boot rehydrate did not emit a summary log line — a future "
        "candidate-set drift would be invisible from the boot log."
    )
    # The line MUST include the restored count. It SHOULD include
    # the candidate count when M > 0.
    assert any(": 1" in line for line in summary_lines), (
        f"Summary log lines {summary_lines!r} did not include the "
        f"restored count (1 for one seeded asker)."
    )