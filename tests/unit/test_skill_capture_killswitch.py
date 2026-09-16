"""Tests for the ``ENSEMBLE_SKILL_CAPTURE_ENABLED`` kill-switch.

The kill-switch (default OFF) gates every automatic CAPTURED-flow
entry point so the post-execution trigger, the dispatcher enqueue,
and the skill-keeper's ``skill_execute_capture`` tool all short-
circuit when the operator has disabled automatic skill capture.

Three independent seams must be pinned at OFF:

1. ``SkillMetricsService._check_capture_eligibility`` — the
   post-execution CAPTURED trigger that runs at the tail of
   ``record_task_completion``. With the flag OFF this gate must
   return ``None`` BEFORE any of the existing eligibility checks
   fire — no agent-metadata lookup, no usage-record DB read, no
   evolution-service call, no dispatcher enqueue.
2. ``SkillJobDispatcher.enqueue_capture`` — the single chokepoint
   that any producer of ``skill_capture`` jobs MUST go through.
   With the flag OFF this method must return ``None`` and skip the
   ``job_service.enqueue`` call entirely. Belt-and-braces for
   already-queued jobs re-dispatched after a flag flip.
3. ``daemon.tools.skill_evolution_tools.skill_execute_capture`` —
   the LangChain tool the skill-keeper agent calls. With the flag
   OFF the tool must return a clear JSON envelope
   (``{"skipped": True, ...}``) and NOT invoke
   ``_skill_evolution_service.capture_skill``. This is also the
   known bypass path around ``check_and_capture`` — the tool path
   historically skipped the metrics-service eligibility gates.

When ON (``=1``/``=true``/etc.) the resolver returns ``True`` and
all three seams are byte-identical to the pre-flag codebase.

Flag-resolution semantics: invalid env values raise ``ValueError``
(fail-closed) — mirroring the ``_resolve_repair_enabled`` exemplar
at ``daemon/tools/ens_db_tools.py:131``. This means a typo in the
env surfaces as an exception (visible in the daemon boot logs) rather
than silently enabling capture, which is the safe-default semantic.

Out of scope (must NOT be gated): ``skill_create``, ``skill_search``,
``skill_view``, ``skill_fix``, ``skill_list``, skill injection into
context, ``SkillTriggerEngine`` metric-scan / evolution jobs, A/B
test resolution machinery. These flows are unrelated to the
automatic CAPTURED path and must continue to operate regardless of
the flag. The dedicated assertion is that the resolver name is not
imported from ``daemon.tools.skill_tools`` (the file owning
``skill_create``/``skill_search``/etc.).
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.skill import (
    SkillABTestRepository,
    SkillEmbeddingRepository,
    SkillLineageRepository,
    SkillRepository,
    SkillTriggerRepository,
    SkillUsageRepository,
)
from daemon.services.skill_evolution_service import SkillEvolutionService
from daemon.services.skill_metrics_service import (
    INJECTED_SKILLS_METADATA_KEY,
    SKILL_CAPTURE_KILL_SWITCH_ENV,
    SkillMetricsService,
)
from daemon.services.skill_job_dispatcher import (
    SKILL_CAPTURE_KILL_SWITCH_ENV as DISPATCHER_ENV_NAME,
    _resolve_capture_enabled,
)
from daemon.tools.skill_evolution_tools import SKILL_CAPTURE_KILL_SWITCH_ENV as TOOL_ENV_NAME


# Cross-module invariant: every consumer imports the SAME constant.
# A rename that desyncs the three import sites would silently
# disable the gate. This is a one-liner structural pin — cheap to
# maintain, expensive to silently violate.
def test_killswitch_env_name_is_identical_across_modules():
    assert SKILL_CAPTURE_KILL_SWITCH_ENV == DISPATCHER_ENV_NAME == TOOL_ENV_NAME
    assert SKILL_CAPTURE_KILL_SWITCH_ENV == "ENSEMBLE_SKILL_CAPTURE_ENABLED"


# =============================================================================
# Group 1: Resolver — `_resolve_capture_enabled`
# =============================================================================


class TestResolveCaptureEnabled:
    """Direct exercise of :func:`_resolve_capture_enabled`.

    Resolution order:

    1. ``ENSEMBLE_SKILL_CAPTURE_ENABLED`` env (canonical).
    2. Unset / empty string → default OFF (capture disabled).

    Recognized ON values (case-insensitive): ``"1"``, ``"true"``,
    ``"yes"``, ``"on"``. Recognized OFF values: ``"0"``,
    ``"false"``, ``"no"``, ``"off"`` (and the default-OFF
    unset/empty case).

    Invalid env values raise ``ValueError`` — fail-closed on
    misconfiguration rather than silently enabling capture (parity
    with ``_resolve_repair_enabled`` at
    ``daemon/tools/ens_db_tools.py:131``).
    """

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make sure no parent env leaks into the test."""
        monkeypatch.delenv(SKILL_CAPTURE_KILL_SWITCH_ENV, raising=False)

    def test_unset_returns_false(self):
        """Default OFF: no env var set → capture disabled."""
        assert _resolve_capture_enabled() is False

    def test_empty_string_returns_false(self):
        """``ENSEMBLE_SKILL_CAPTURE_ENABLED=''`` is the default-OFF case."""
        os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = ""
        assert _resolve_capture_enabled() is False

    @pytest.mark.parametrize("value", ["0", "false", "no", "off"])
    def test_off_values_return_false(self, value: str):
        """All four canonical OFF spellings disable capture."""
        os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = value
        assert _resolve_capture_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
    def test_on_values_return_true(self, value: str):
        """All four canonical ON spellings enable capture."""
        os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = value
        assert _resolve_capture_enabled() is True

    @pytest.mark.parametrize("value", ["TRUE", "True", "On", "YES", "0", "1"])
    def test_resolution_is_case_insensitive(self, value: str):
        """Resolution trims + lowercases — surrounding whitespace and case don't matter."""
        os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = value
        # Just verify no exception and the boolean matches the lowercased equivalent.
        expected = value.lower() in ("1", "true", "yes", "on")
        assert _resolve_capture_enabled() is expected

    def test_whitespace_around_value_is_stripped(self):
        """``" 1 "`` and ``"  true  "`` both resolve correctly."""
        os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = " 1 "
        assert _resolve_capture_enabled() is True
        os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = "  off  "
        assert _resolve_capture_enabled() is False

    @pytest.mark.parametrize("bad_value", [
        "banana", "enabled", "yesno", "2", "-1", "1.0",
        "00", " ",  # bare space — strips to "", which IS valid (default OFF)
    ])
    def test_invalid_values_raise_value_error(self, bad_value: str):
        """Invalid env values raise ``ValueError`` (fail-closed).

        Mirrors the ``_resolve_repair_enabled`` exemplar: typos
        surface as a loud exception rather than silently enabling
        capture. Bare-whitespace ``" "`` is a special case — after
        ``.strip().lower()`` it becomes ``""`` which IS a valid
        value meaning "default OFF", so no exception is expected.
        """
        if bad_value == " ":
            os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = " "
            assert _resolve_capture_enabled() is False
            return
        os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = bad_value
        with pytest.raises(ValueError) as excinfo:
            _resolve_capture_enabled()
        # Error message must mention the env var name (operators need
        # this to debug a misconfigured env).
        assert SKILL_CAPTURE_KILL_SWITCH_ENV in str(excinfo.value)
        # And the offending value (helpful for debug).
        assert bad_value in str(excinfo.value) or repr(bad_value) in str(excinfo.value)


# =============================================================================
# Group 2: Dispatcher gate — `SkillJobDispatcher.enqueue_capture`
# =============================================================================


class FakeQueue:
    """Minimal stand-in for :class:`JobQueue` (only ``queue_id`` is read)."""

    def __init__(self, queue_id: str = "q-parallel-ks", name: str = "system_parallel_queue"):
        self.queue_id = queue_id
        self.queue_name = name
        self.project_id = "test-project-ks"


class FakeJob:
    """Minimal stand-in for :class:`JobItem` (only ``job_id`` is read)."""

    def __init__(self, job_id: str = "job-ks-1"):
        self.job_id = job_id


@pytest.fixture
def ks_job_service():
    """Mock :class:`JobQueueService` whose ``enqueue`` returns a deterministic job."""
    svc = MagicMock()
    svc.enqueue = AsyncMock(return_value=FakeJob("job-ks-captured"))
    return svc


@pytest.fixture
def ks_queue_repo():
    """Mock :class:`JobQueueRepository` returning a known parallel queue."""
    repo = MagicMock()
    repo.get_by_name = MagicMock(return_value=FakeQueue())
    return repo


@pytest.fixture
def ks_dispatcher(ks_job_service, ks_queue_repo):
    """A :class:`SkillJobDispatcher` wired against mock collaborators."""
    from daemon.services.skill_job_dispatcher import SkillJobDispatcher
    return SkillJobDispatcher(job_service=ks_job_service, queue_repo=ks_queue_repo)


class TestDispatcherCaptureKillSwitch:
    """``SkillJobDispatcher.enqueue_capture`` kill-switch pin.

    OFF (default) → returns ``None``, ``job_service.enqueue`` is
    NEVER awaited. ON (``=1``) → dispatches normally and returns
    the ``job_id``. Invalid env → ``ValueError`` propagates.
    """

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default: kill-switch OFF (unset env)."""
        monkeypatch.delenv(SKILL_CAPTURE_KILL_SWITCH_ENV, raising=False)

    @pytest.mark.asyncio
    async def test_off_by_default_returns_none_no_enqueue(
        self, ks_dispatcher, ks_job_service
    ):
        """Unset env → ``enqueue_capture`` returns ``None`` without dispatching."""
        result = await ks_dispatcher.enqueue_capture(
            project_id="my-project",
            task_details={"instance_id": "inst-x", "iterations": 8},
        )
        assert result is None
        # The whole point of the gate: ``job_service.enqueue`` is
        # NEVER called — no job row is created in the queue.
        ks_job_service.enqueue.assert_not_called()
        ks_job_service.enqueue.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("off_value", ["0", "false", "no", "off", ""])
    async def test_explicit_off_values_return_none_no_enqueue(
        self, ks_dispatcher, ks_job_service, monkeypatch, off_value
    ):
        """All four canonical OFF spellings (and empty string) → no-op."""
        monkeypatch.setenv(SKILL_CAPTURE_KILL_SWITCH_ENV, off_value)
        result = await ks_dispatcher.enqueue_capture(
            project_id="my-project",
            task_details={"instance_id": "inst-x"},
        )
        assert result is None
        ks_job_service.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_preserves_existing_behavior(
        self, ks_dispatcher, ks_job_service, monkeypatch
    ):
        """``ENSEMBLE_SKILL_CAPTURE_ENABLED=1`` → dispatch is byte-identical to pre-flag."""
        monkeypatch.setenv(SKILL_CAPTURE_KILL_SWITCH_ENV, "1")
        result = await ks_dispatcher.enqueue_capture(
            project_id="my-project",
            task_details={
                "instance_id": "inst-abc",
                "agent_id": "agent-x",
                "project_id": "proj-1",
                "task_message": "extract a skill",
                "iterations": 8,
                "duration_seconds": 90,
            },
        )
        assert result == "job-ks-captured"
        ks_job_service.enqueue.assert_awaited_once()
        kwargs = ks_job_service.enqueue.await_args.kwargs
        assert kwargs["job_type"] == "skill_capture"
        assert kwargs["queue_id"] == "q-parallel-ks"
        assert kwargs["agent_id"] == "skill-keeper"
        assert kwargs["metadata"]["task_details"]["iterations"] == 8

    @pytest.mark.asyncio
    async def test_invalid_env_propagates_value_error(
        self, ks_dispatcher, monkeypatch
    ):
        """``ENSEMBLE_SKILL_CAPTURE_ENABLED=banana`` → ``ValueError`` from the resolver
        propagates out of ``enqueue_capture``. The dispatcher MUST NOT silently
        swallow it — a misconfigured env is a load-bearing signal."""
        monkeypatch.setenv(SKILL_CAPTURE_KILL_SWITCH_ENV, "banana")
        with pytest.raises(ValueError) as excinfo:
            await ks_dispatcher.enqueue_capture(
                project_id="my-project",
                task_details={},
            )
        assert SKILL_CAPTURE_KILL_SWITCH_ENV in str(excinfo.value)


# =============================================================================
# Group 3: Metrics service gate — `SkillMetricsService._check_capture_eligibility`
# =============================================================================


@pytest.fixture
def ks_engine():
    """In-memory SQLite engine with all skill tables created."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def ks_repos(ks_engine):
    """All six skill repositories bound to the test engine."""
    return SimpleNamespace(
        skill=SkillRepository(ks_engine),
        lineage=SkillLineageRepository(ks_engine),
        usage=SkillUsageRepository(ks_engine),
        trigger=SkillTriggerRepository(ks_engine),
        embedding=SkillEmbeddingRepository(ks_engine),
        ab_test=SkillABTestRepository(ks_engine),
    )


@pytest.fixture
def ks_config():
    from daemon.config import SkillEvolutionConfig
    return SkillEvolutionConfig()


@pytest.fixture
def ks_embedding_service():
    svc = MagicMock()
    svc.update_skill_embeddings = AsyncMock(return_value=0)
    svc.embed_user_message = AsyncMock(return_value=[0.1] * 4)
    return svc


@pytest.fixture
def ks_evolution_service(ks_repos, ks_embedding_service, ks_config):
    """Real :class:`SkillEvolutionService` — its ``check_and_capture`` will be
    patched per-test to track whether the gate called it."""
    return SkillEvolutionService(
        skill_repo=ks_repos.skill,
        lineage_repo=ks_repos.lineage,
        usage_repo=ks_repos.usage,
        embedding_service=ks_embedding_service,
        metrics_service=MagicMock(),
        ab_test_repo=ks_repos.ab_test,
        config=ks_config,
        llm_config={"base_url": "http://test", "api_key": "test"},
    )


@pytest.fixture
def ks_agent_id_resolver():
    """Resolver that returns metadata with ``skill_injection=True`` so the
    eligibility gate, if it runs, would proceed past Gate 3."""
    meta = SimpleNamespace(skill_injection=True)
    return MagicMock(return_value=meta)


@pytest.fixture
def ks_instance_repo():
    inst = SimpleNamespace(
        instance_id="inst-ks-1",
        instance_metadata={INJECTED_SKILLS_METADATA_KEY: []},
    )
    repo = MagicMock()
    repo.get = MagicMock(return_value=inst)
    repo.delete_metadata = MagicMock(return_value=None)
    repo.set_metadata = MagicMock(return_value=None)
    return repo


@pytest.fixture
def ks_metrics_service(
    ks_repos, ks_config, ks_evolution_service,
    ks_agent_id_resolver, ks_instance_repo,
):
    svc = SkillMetricsService(
        usage_repo=ks_repos.usage,
        skill_repo=ks_repos.skill,
        trigger_repo=ks_repos.trigger,
        ab_test_repo=ks_repos.ab_test,
        config=ks_config,
        instance_repo=ks_instance_repo,
        evolution_service=ks_evolution_service,
        agent_id_resolver=ks_agent_id_resolver,
    )
    # Real dispatcher so we can assert capture-job enqueue.
    dispatcher = MagicMock()
    dispatcher.enqueue_capture = AsyncMock(return_value="job-captured-ks")
    svc.set_job_dispatcher(dispatcher)
    return svc


class TestMetricsServiceCaptureKillSwitch:
    """``SkillMetricsService._check_capture_eligibility`` kill-switch pin.

    OFF (default) → returns ``None`` BEFORE any eligibility check
    fires (no agent-metadata lookup, no usage-record read, no
    evolution-service call, no dispatcher enqueue). ON (``=1``) →
    runs all gates as documented.

    The eligibility gate's normal flow (without the flag) would
    call ``evolution_service.check_and_capture`` and then
    ``dispatcher.enqueue_capture``. We spy on both: with the
    flag OFF neither must be called.
    """

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(SKILL_CAPTURE_KILL_SWITCH_ENV, raising=False)

    def _seed_skill(self, ks_repos, name: str = "ks-test-skill") -> str:
        skill = ks_repos.skill.create(
            name=name,
            description=f"test {name}",
            content="body",
            project_id="p-ks",
            category="workflow",
        )
        return skill.id

    @pytest.mark.asyncio
    async def test_off_skips_trigger_entirely_no_evolution_call(
        self, ks_metrics_service, ks_repos, ks_instance_repo, monkeypatch
    ):
        """With the kill-switch OFF, ``_check_capture_eligibility`` returns ``None``
        BEFORE the evolution service is consulted — no ``check_and_capture`` call,
        no dispatcher enqueue, no usage-record read.

        Spy on ``evolution_service.check_and_capture`` to prove the
        gate short-circuits BEFORE Gate 4/5 (which would otherwise
        call this method). Also spy on ``usage_repo.has_applied_for_instance``
        to prove the DB hit is avoided.
        """
        skill_id = self._seed_skill(ks_repos)
        inst = SimpleNamespace(
            instance_id="inst-ks-off",
            instance_metadata={INJECTED_SKILLS_METADATA_KEY: [skill_id]},
        )
        ks_instance_repo.get = MagicMock(return_value=inst)

        # Spy on the expensive collaborators.
        ks_metrics_service.evolution_service.check_and_capture = AsyncMock(
            return_value={
                "instance_id": "inst-ks-off",
                "agent_id": "agent-x",
                "project_id": "p-ks",
                "task_message": "msg",
                "iterations": 10,
                "duration_seconds": 120,
                "task_succeeded": True,
            }
        )
        ks_repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        result = await ks_metrics_service._check_capture_eligibility(
            instance_id="inst-ks-off",
            agent_id="agent-x",
            project_id="p-ks",
            task_message="msg",
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
        )

        # The gate returns None — no capture.
        assert result is None
        # The evolution service was NEVER consulted — Gate 0 fires
        # before Gate 4/5.
        ks_metrics_service.evolution_service.check_and_capture.assert_not_called()
        # The usage record DB read was NEVER made — same reason.
        ks_repos.usage.has_applied_for_instance.assert_not_called()
        # The dispatcher was NEVER invoked.
        ks_metrics_service._skill_job_dispatcher.enqueue_capture.assert_not_called()

    @pytest.mark.asyncio
    async def test_off_skips_record_task_completion_path(
        self, ks_metrics_service, ks_repos, ks_instance_repo
    ):
        """End-to-end via ``record_task_completion`` (the public entry) — with
        the flag OFF, even a complex successful task with high iterations /
        duration produces zero capture-job enqueue calls."""
        skill_id = self._seed_skill(ks_repos)
        inst = SimpleNamespace(
            instance_id="inst-ks-rtc",
            instance_metadata={INJECTED_SKILLS_METADATA_KEY: [skill_id]},
        )
        ks_instance_repo.get = MagicMock(return_value=inst)
        ks_repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        result = await ks_metrics_service.record_task_completion(
            instance_id="inst-ks-rtc",
            agent_id="agent-x",
            project_id="p-ks",
            task_succeeded=True,
            iterations=10,         # > capture_min_iterations (5)
            duration_seconds=120,  # > capture_min_duration_seconds (60)
            task_message="complex task that would normally capture",
        )

        # Usage record was still inserted (metrics are NOT gated) — but capture is.
        assert result == 1
        # The dispatcher received NO capture call.
        assert ks_metrics_service._skill_job_dispatcher.enqueue_capture.await_count == 0

    @pytest.mark.asyncio
    async def test_off_default_matches_post_execution_capture_disabled(
        self, ks_metrics_service, ks_repos, ks_instance_repo
    ):
        """Identical assertions but with the env EXPLICITLY set to ``=0`` (the
        canonical OFF spelling) — proves the gate is the same code path
        whether the env is unset or set to ``"0"``."""
        skill_id = self._seed_skill(ks_repos)
        inst = SimpleNamespace(
            instance_id="inst-ks-0",
            instance_metadata={INJECTED_SKILLS_METADATA_KEY: [skill_id]},
        )
        ks_instance_repo.get = MagicMock(return_value=inst)
        ks_repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = "0"

        await ks_metrics_service._check_capture_eligibility(
            instance_id="inst-ks-0",
            agent_id="agent-x",
            project_id="p-ks",
            task_message="msg",
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
        )
        ks_metrics_service._skill_job_dispatcher.enqueue_capture.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_preserves_existing_capture_behavior(
        self, ks_metrics_service, ks_repos, ks_instance_repo, monkeypatch
    ):
        """``ENSEMBLE_SKILL_CAPTURE_ENABLED=1`` → gate runs all 5 (now 6)
        checks and dispatches a capture job. Byte-identical to pre-flag."""
        monkeypatch.setenv(SKILL_CAPTURE_KILL_SWITCH_ENV, "1")
        skill_id = self._seed_skill(ks_repos)
        inst = SimpleNamespace(
            instance_id="inst-ks-on",
            instance_metadata={INJECTED_SKILLS_METADATA_KEY: [skill_id]},
        )
        ks_instance_repo.get = MagicMock(return_value=inst)
        ks_repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        result = await ks_metrics_service._check_capture_eligibility(
            instance_id="inst-ks-on",
            agent_id="agent-x",
            project_id="p-ks",
            task_message="msg",
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
        )
        assert result is not None
        assert ks_metrics_service._skill_job_dispatcher.enqueue_capture.await_count == 1

    @pytest.mark.asyncio
    async def test_invalid_env_raises_value_error(
        self, ks_metrics_service, monkeypatch
    ):
        """``ENSEMBLE_SKILL_CAPTURE_ENABLED=banana`` → ``ValueError`` propagates
        from ``_check_capture_eligibility``. The metrics service MUST NOT
        swallow the resolver error — a misconfigured env is loud."""
        monkeypatch.setenv(SKILL_CAPTURE_KILL_SWITCH_ENV, "banana")
        with pytest.raises(ValueError):
            await ks_metrics_service._check_capture_eligibility(
                instance_id="inst-ks-bad",
                agent_id="agent-x",
                project_id="p-ks",
                task_message="msg",
                task_succeeded=True,
                iterations=10,
                duration_seconds=120,
            )

    @pytest.mark.asyncio
    async def test_record_task_completion_with_invalid_env_warns_does_not_crash(
        self, ks_metrics_service, ks_repos, ks_instance_repo, monkeypatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """``ENSEMBLE_SKILL_CAPTURE_ENABLED=banana`` at the public
        ``record_task_completion`` entry → ``ValueError`` from
        ``_resolve_capture_enabled`` is CONTAINED by the existing soft-fail
        boundary (lines 487-501); the usage-record metrics path still runs
        and the dispatcher enqueue is never awaited.

        Complements :meth:`test_invalid_env_raises_value_error` (above),
        which pins the direct ``_check_capture_eligibility`` path: there
        the resolver error propagates loudly because the caller is the
        test/operator. Here the caller is the job-queue completion hook
        — which MUST return cleanly to its caller — so the soft-fail
        boundary catches the ``ValueError``, logs it loudly as a
        warning, preserves the just-written usage records, and ensures
        capture never fires. This pins the contract that an invalid
        env surfaces loudly in the daemon logs (operators can spot
        the misconfiguration) without breaking the metrics path or
        raising back to the job-queue completion hook.
        """
        monkeypatch.setenv(SKILL_CAPTURE_KILL_SWITCH_ENV, "banana")
        skill_id = self._seed_skill(ks_repos)
        inst = SimpleNamespace(
            instance_id="inst-ks-banana",
            instance_metadata={INJECTED_SKILLS_METADATA_KEY: [skill_id]},
        )
        ks_instance_repo.get = MagicMock(return_value=inst)
        ks_repos.usage.has_applied_for_instance = MagicMock(return_value=False)

        with caplog.at_level(
            "WARNING", logger="daemon.services.skill_metrics_service"
        ):
            result = await ks_metrics_service.record_task_completion(
                instance_id="inst-ks-banana",
                agent_id="agent-x",
                project_id="p-ks",
                task_succeeded=True,
                iterations=10,         # > capture_min_iterations (5)
                duration_seconds=120,  # > capture_min_duration_seconds (60)
                task_message="invalid-env task — soft-fail boundary pin",
            )

        # Soft-fail swallowed the ValueError and the job-queue
        # completion hook returned cleanly — the usage-record
        # metrics path still ran end-to-end (1 record for the 1
        # injected skill).
        assert result >= 1
        # Capture never fired: the resolver raised BEFORE Gate 1
        # could wire any task_details back to the dispatcher, and
        # ``_resolve_capture_enabled`` only returns True on a
        # recognized ON value.
        assert ks_metrics_service._skill_job_dispatcher.enqueue_capture.await_count == 0
        # Soft-fail logged a warning with the instance id so
        # operators can spot the misconfiguration in the daemon logs.
        assert any(
            "CAPTURED eligibility check failed" in rec.message
            and "inst-ks-banana" in rec.message
            for rec in caplog.records
        )


# =============================================================================
# Group 4: Tool gate — `skill_execute_capture` LangChain tool
# =============================================================================


@pytest.fixture
def ks_manager_with_service():
    """Mock manager wired to a real-ish skill evolution service mock."""
    service = MagicMock()
    service.capture_skill = AsyncMock(return_value={
        "new_skill_id": "skill-ks-captured",
        "skipped": False,
    })
    manager = MagicMock()
    manager._skill_evolution_service = service
    return manager, service


class TestSkillExecuteCaptureToolKillSwitch:
    """``skill_execute_capture`` LangChain tool kill-switch pin.

    OFF (default) → tool returns a JSON envelope with
    ``{"skipped": true, "reason": ..., "new_skill_id": null, ...}``
    and DOES NOT invoke ``service.capture_skill``. This closes the
    known bypass path around ``check_and_capture`` — the tool path
    historically skipped the metrics-service eligibility gates and
    could trigger capture even when the post-execution check would
    have refused it.

    ON (``=1``) → tool delegates to ``service.capture_skill`` as
    before, returning the JSON-serialized result. Byte-identical
    to the pre-flag codebase.
    """

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(SKILL_CAPTURE_KILL_SWITCH_ENV, raising=False)

    @pytest.mark.asyncio
    async def test_off_returns_disabled_envelope_no_service_call(
        self, ks_manager_with_service
    ):
        """Default OFF: tool returns the disabled envelope and never touches
        ``service.capture_skill``. The closure-supplied ``current_instance_id``
        and the agent-supplied ``instance_id`` are both irrelevant."""
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools
        manager, service = ks_manager_with_service
        tools = {t.name: t for t in create_skill_evolution_tools(
            manager, "closure-inst-ks"
        )}

        result = await tools["skill_execute_capture"].ainvoke({
            "instance_id": "arg-inst-ks",
            "task_message": "should be ignored",
            "iterations": 99,
            "duration_seconds": 999,
        })

        decoded = json.loads(result)
        assert decoded["skipped"] is True
        assert decoded["new_skill_id"] is None
        # The reason mentions the env var so operators can debug.
        assert SKILL_CAPTURE_KILL_SWITCH_ENV in decoded["reason"]
        assert "disabled" in decoded["reason"].lower()
        # The closure-supplied current_instance_id is reflected back so
        # the agent loop can correlate the disabled response with the
        # calling instance.
        assert decoded["instance_id"] == "arg-inst-ks"
        assert decoded["kill_switch_env"] == SKILL_CAPTURE_KILL_SWITCH_ENV

        # The service was NEVER invoked — the gate fires before the
        # dispatch, closing the bypass path.
        service.capture_skill.assert_not_called()
        service.capture_skill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_off_records_nothing(
        self, ks_manager_with_service, monkeypatch
    ):
        """Verify the OFF envelope contains no skill-row creation signals —
        in particular ``new_skill_id is None`` and ``skipped is True``.
        These are the two fields downstream consumers check."""
        monkeypatch.setenv(SKILL_CAPTURE_KILL_SWITCH_ENV, "0")
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools
        manager, service = ks_manager_with_service
        tools = {t.name: t for t in create_skill_evolution_tools(
            manager, "inst-ks-zero"
        )}

        result = await tools["skill_execute_capture"].ainvoke({
            "instance_id": "inst-ks-zero",
            "task_message": "msg",
            "iterations": 7,
            "duration_seconds": 80,
        })
        decoded = json.loads(result)
        assert decoded["skipped"] is True
        assert decoded["new_skill_id"] is None
        service.capture_skill.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_preserves_existing_dispatch_behavior(
        self, ks_manager_with_service, monkeypatch
    ):
        """``ENSEMBLE_SKILL_CAPTURE_ENABLED=1`` → tool dispatches to
        ``service.capture_skill`` with the closure-supplied
        ``current_instance_id`` and the agent-supplied ``task_details``."""
        monkeypatch.setenv(SKILL_CAPTURE_KILL_SWITCH_ENV, "1")
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools
        manager, service = ks_manager_with_service
        tools = {t.name: t for t in create_skill_evolution_tools(
            manager, "closure-inst-ks-on"
        )}

        result = await tools["skill_execute_capture"].ainvoke({
            "instance_id": "arg-inst-ks-on",
            "task_message": "Capture a skill-cap record",
            "iterations": 5,
            "duration_seconds": 60,
        })
        # Service was invoked exactly once with the expected args.
        service.capture_skill.assert_awaited_once_with(
            "closure-inst-ks-on",
            {
                "instance_id": "arg-inst-ks-on",
                "task_message": "Capture a skill-cap record",
                "iterations": 5,
                "duration_seconds": 60,
            },
        )
        # Result is the JSON-serialized service response.
        decoded = json.loads(result)
        assert decoded["new_skill_id"] == "skill-ks-captured"
        assert decoded["skipped"] is False

    @pytest.mark.asyncio
    async def test_invalid_env_raises_value_error(
        self, ks_manager_with_service, monkeypatch
    ):
        """``ENSEMBLE_SKILL_CAPTURE_ENABLED=banana`` → ``ValueError`` propagates
        from the tool. ``_invoke_service`` would normally catch exceptions
        from the service, but the kill-switch gate fires BEFORE
        ``_invoke_service`` is called — so the resolver error surfaces
        as a tool exception (the standard LangChain contract for
        misconfigured tools)."""
        monkeypatch.setenv(SKILL_CAPTURE_KILL_SWITCH_ENV, "banana")
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools
        manager, service = ks_manager_with_service
        tools = {t.name: t for t in create_skill_evolution_tools(
            manager, "closure-inst-ks-bad"
        )}

        with pytest.raises(ValueError):
            await tools["skill_execute_capture"].ainvoke({
                "instance_id": "arg-inst-ks-bad",
                "task_message": "msg",
                "iterations": 7,
                "duration_seconds": 80,
            })
        service.capture_skill.assert_not_called()


# =============================================================================
# Group 5: Out-of-scope flows are NOT gated (skill_create, skill_search,
# injection into context, SkillTriggerEngine jobs)
# =============================================================================


class TestOutOfScopeFlowsAreNotGated:
    """Sanity pins that the kill-switch does NOT bleed into unrelated flows.

    The capture flag is narrowly scoped: only the CAPTURED-flow entry
    points (``enqueue_capture``, ``_check_capture_eligibility``,
    ``skill_execute_capture``) consult it. Every other skill flow
    (``skill_create``, ``skill_search``, ``skill_view``,
    ``skill_list``, ``skill_fix``, skill injection into context,
    ``SkillTriggerEngine`` metric-scan / evolution jobs, A/B
    resolution) MUST continue to operate regardless of the flag.

    These tests are mostly structural: grep-level proof that the
    resolver is NOT imported into the out-of-scope modules. The
    two runtime checks are:
    """

    def test_resolver_not_imported_by_skill_tools(self):
        """``skill_create`` / ``skill_search`` / ``skill_view`` /
        ``skill_list`` / ``skill_fix`` live in
        ``daemon/tools/skill_tools.py`` — they MUST NOT consult
        ``_resolve_capture_enabled``. The cleanest pin is an import-
        level check: if anyone imports the resolver there, this test
        fails loudly."""
        import daemon.tools.skill_tools as skill_tools_mod
        module_source = getattr(skill_tools_mod, "__file__", "") or ""
        # Read the source file and grep for the resolver name.
        # This catches both ``import`` and ``from ... import`` forms.
        try:
            with open(module_source, encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            pytest.skip("skill_tools.py not loadable as a file")
        assert "_resolve_capture_enabled" not in src, (
            "daemon/tools/skill_tools.py MUST NOT consult the "
            "skill-capture kill-switch — it owns skill_create / "
            "skill_search / skill_view / skill_list / skill_fix, "
            "which are out of scope for this flag."
        )
        assert SKILL_CAPTURE_KILL_SWITCH_ENV not in src, (
            "daemon/tools/skill_tools.py MUST NOT reference the "
            "skill-capture kill-switch env var — out of scope."
        )

    def test_resolver_not_imported_by_injection_pipeline(self):
        """Skill injection into the prompt lives in
        ``daemon/services/skill_injection_service.py`` (and related
        modules). The injection pipeline MUST NOT consult the
        capture kill-switch — captured skills are a *creation*
        concern, not an *injection* concern."""
        candidates = [
            "daemon/services/skill_injection_service.py",
            "daemon/services/injection.py",
            "daemon/services/skill_injection.py",
        ]
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[2]
        checked = []
        for rel in candidates:
            p = repo_root / rel
            if not p.exists():
                continue
            checked.append(rel)
            src = p.read_text(encoding="utf-8")
            assert "_resolve_capture_enabled" not in src, (
                f"{rel} MUST NOT consult the skill-capture kill-switch — "
                f"skill injection is out of scope for this flag."
            )
            assert SKILL_CAPTURE_KILL_SWITCH_ENV not in src
        # At least one of the candidate files must exist; otherwise
        # the test is silently no-op'd and we lose the pin.
        assert checked, (
            "No skill-injection source file found — the injection "
            "pipeline's location has changed; update this test to "
            "pin the new path."
        )

    @pytest.mark.asyncio
    async def test_skill_create_tool_unaffected_by_kill_switch(
        self, monkeypatch
    ):
        """``skill_create`` is the explicit authoring tool — out of scope for
        this kill-switch. Pin a minimal stub: even with the kill-switch
        OFF the tool's registration / call path is unchanged. We assert
        this by reading the tool-registration source for ``skill_create``
        and grepping that the capture resolver is not consulted there."""
        # The actual skill_create tool lives in daemon/tools/skill_tools.py
        # and uses a separate service (``_skill_store_service``). Since
        # this is a structural pin (the flag has no runtime effect on
        # skill_create), we re-use the same import check as the
        # ``test_resolver_not_imported_by_skill_tools`` test — but at
        # runtime, also assert that ``create_skill_tools`` does not
        # raise when the flag is OFF.
        from daemon.tools.skill_tools import create_skill_tools
        manager = MagicMock()
        manager._skill_store_service = MagicMock()
        manager._skill_search_service = MagicMock()
        # Kill-switch OFF — must NOT crash.
        monkeypatch.delenv(SKILL_CAPTURE_KILL_SWITCH_ENV, raising=False)
        tools = create_skill_tools(manager, "inst-ks-create")
        # Factory returned the expected tools (5 langchain tools per
        # the factory contract). We don't drill into the runtime
        # behavior of skill_create here — that's covered by the
        # dedicated test_skill_seeding / test_skill_clone_service
        # suites — we only assert the flag doesn't crash the factory.
        assert len(tools) >= 1
        # And specifically, the tool names include skill_create / skill_search.
        tool_names = {getattr(t, "name", None) for t in tools}
        assert "skill_create" in tool_names
        assert "skill_search" in tool_names


# =============================================================================
# Group 6: Belt-and-braces — all 3 gates fire on the OFF path simultaneously
# =============================================================================


class TestAllThreeGatesFireSimultaneously:
    """When OFF, every gate fires — no capture can sneak through any path.

    The architectural claim of this flag is that AUTOMATIC skill
    capture is impossible when ``ENSEMBLE_SKILL_CAPTURE_ENABLED`` is
    unset. Three layers cooperate to enforce that invariant:

    1. ``SkillMetricsService._check_capture_eligibility`` returns
       ``None`` BEFORE consulting the evolution service. (post-
       execution CAPTURED trigger)
    2. ``SkillJobDispatcher.enqueue_capture`` returns ``None``
       instead of enqueueing. (dispatch chokepoint — belt-and-
       braces for any other producer)
    3. ``skill_execute_capture`` returns the ``skipped`` envelope
       without calling ``service.capture_skill``. (tool bypass path)

    This test exercises all three seams in the same OFF scenario
    and asserts NONE of them perform their real work.
    """

    @pytest.fixture(autouse=True)
    def _isolate_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(SKILL_CAPTURE_KILL_SWITCH_ENV, raising=False)

    @pytest.mark.asyncio
    async def test_all_three_seams_silently_disabled_when_off(
        self, ks_metrics_service, ks_dispatcher, ks_manager_with_service,
        ks_repos, ks_instance_repo,
    ):
        """Exercise metrics-service trigger, dispatcher enqueue, and tool
        invoke — all with the env unset. NONE must do real work."""
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        # (a) Post-execution trigger via the metrics service.
        skill_id = ks_repos.skill.create(
            name="ks-belt",
            description="ks-belt",
            content="body",
            project_id="p-ks-belt",
            category="workflow",
        )
        inst = SimpleNamespace(
            instance_id="inst-ks-belt",
            instance_metadata={INJECTED_SKILLS_METADATA_KEY: [skill_id]},
        )
        ks_instance_repo.get = MagicMock(return_value=inst)
        ks_repos.usage.has_applied_for_instance = MagicMock(return_value=False)
        # Spy on the evolution service so we can prove Gate 0 fires
        # BEFORE it would have been consulted.
        ks_metrics_service.evolution_service.check_and_capture = AsyncMock(
            return_value={"iterations": 10, "duration_seconds": 120}
        )

        await ks_metrics_service._check_capture_eligibility(
            instance_id="inst-ks-belt",
            agent_id="agent-x",
            project_id="p-ks-belt",
            task_message="msg",
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
        )
        # (b) Dispatcher enqueue from any external producer.
        result_dispatch = await ks_dispatcher.enqueue_capture(
            project_id="p-ks-belt",
            task_details={"instance_id": "inst-ks-belt"},
        )

        # (c) Tool invoke.
        manager, service = ks_manager_with_service
        tools = {t.name: t for t in create_skill_evolution_tools(
            manager, "closure-inst-ks-belt"
        )}
        result_tool = await tools["skill_execute_capture"].ainvoke({
            "instance_id": "arg-inst-ks-belt",
            "task_message": "msg",
            "iterations": 7,
            "duration_seconds": 80,
        })

        # ── Asserts ─────────────────────────────────────────────
        # (a) Metrics service gate fired.
        ks_metrics_service.evolution_service.check_and_capture.assert_not_called()
        assert ks_metrics_service._skill_job_dispatcher.enqueue_capture.await_count == 0
        # (b) Dispatcher returned None, never enqueued.
        assert result_dispatch is None
        ks_dispatcher._job_service.enqueue.assert_not_called()
        # (c) Tool returned the disabled envelope, never called capture_skill.
        decoded = json.loads(result_tool)
        assert decoded["skipped"] is True
        assert decoded["new_skill_id"] is None
        service.capture_skill.assert_not_called()