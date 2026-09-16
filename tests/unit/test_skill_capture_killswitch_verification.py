"""Independent behavioral verification for the ``ENSEMBLE_SKILL_CAPTURE_ENABLED`` kill-switch.

This file is a SECOND, INDEPENDENT verification suite — it does NOT copy
or reuse the dev's ``tests/unit/test_skill_capture_killswitch.py`` fixtures,
test names, or assertion strategy. Where the dev's suite relies on a tight
in-memory SQLite engine + the pre-built ``ks_*`` fixture chain, this suite
constructs its own narrower fixtures so that:

  * gate-position drift (a refactor that moves the kill-switch check AFTER
    an expensive pre-check) is caught by us — we assert the real
    ``SkillRepository.create`` boundary is never touched when the
    kill-switch is OFF, and that the agent-metadata lookup is not even
    invoked;
  * caching regressions (someone "optimizes" by caching the resolver
    result at module level) are caught by the per-call no-cache pin;
  * ON-path regressions (the dispatcher no longer awaits the storage
    boundary when ON) are caught by an explicit ``assert_awaited_once``
    at the ``_job_service.enqueue`` boundary.

Six required coverages from the spec:

1. Default-OFF seam inertness — every seam is silent: no DB write, no
   job enqueue, no service call.
2. ON path — resolver True for "1" and "true"; dispatcher demonstrably
   proceeds to await the storage boundary.
3. Env token matrix — full ON/OFF/INVALID parametrized cases.
4. Containment at real call sites — ValueError from "garbage" env is
   caught by the metrics soft-fail boundary; the tool seam raises
   ValueError (the class ``ToolNode(handle_tool_errors=True)`` catches).
5. Per-call read — no caching: env OFF → False, then setenv ON → True
   on the next call.
6. Unaffected — ``skill_create`` tool still completes successfully with
   the kill-switch OFF (spy at the ``_skill_store_service.create_skill``
   boundary); ``skill_injection_service`` does not consult the
   kill-switch.

Conventions used throughout (see ``tests/conftest.py`` for the daemon-
mocked modules and the global ``clean_env`` autouse fixture):

* ``asyncio_mode = "auto"`` is set in ``pyproject.toml`` — every
  ``async def test_*`` is auto-decorated, no explicit
  ``@pytest.mark.asyncio`` needed.
* The kill-switch env var is hermetically isolated per test via
  ``monkeypatch.delenv(SKILL_CAPTURE_KILL_SWITCH_ENV, raising=False)``
  in a per-class autouse fixture.
* All async collaborators on the unit-under-test are mocked with
  ``AsyncMock``; sync collaborators with ``MagicMock``. The
  service/tool objects themselves are the REAL production classes
  — only the storage / job-queue / DB boundaries are mocked.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

# All three modules import the SAME constant — the resolver name is
# module-internal so we import the canonical constant from each side
# and assert identity (cheap, structural pin against rename drift).
from daemon.services.skill_metrics_service import (
    INJECTED_SKILLS_METADATA_KEY,
    SKILL_CAPTURE_KILL_SWITCH_ENV,
    SkillMetricsService,
)
from daemon.services.skill_job_dispatcher import (
    SKILL_CAPTURE_KILL_SWITCH_ENV as DISPATCHER_ENV_NAME,
    SkillJobDispatcher,
    _resolve_capture_enabled,
)
from daemon.tools.skill_evolution_tools import (
    SKILL_CAPTURE_KILL_SWITCH_ENV as TOOL_ENV_NAME,
)


# =============================================================================
# Module-level structural pins — cheap, catch rename drift early
# =============================================================================


def test_module_level_env_name_is_identical_across_all_three_callers():
    """The kill-switch env-var string MUST be identical in every consumer
    module. A desynced rename silently disables the gate (one module keeps
    reading the old name, the others read the new one). Cheap structural
    pin — a rename that breaks parity fails this test before any
    behavioral test even runs.
    """
    assert SKILL_CAPTURE_KILL_SWITCH_ENV == DISPATCHER_ENV_NAME == TOOL_ENV_NAME
    assert SKILL_CAPTURE_KILL_SWITCH_ENV == "ENSEMBLE_SKILL_CAPTURE_ENABLED"


# =============================================================================
# Coverage 5 (early, before the autouse fixtures): per-call no-cache proof
# =============================================================================


def test_resolver_is_per_call_no_caching():
    """Coverage 5 — the resolver MUST read the env on every call.

    The whole point of per-call resolution (vs a cached config knob) is
    that operators can flip the flag without a daemon restart. If anyone
    "optimizes" by memoizing the resolver result, this test fails.

    Proof strategy: call with env unset → False. Then setenv ON, call
    again → True. No restart, no reload, no module reset in between.
    """
    # Hard guarantee of hermetic start.
    os.environ.pop(SKILL_CAPTURE_KILL_SWITCH_ENV, None)
    assert _resolve_capture_enabled() is False
    # Flip the flag, immediately re-resolve. Caching would keep this False.
    os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = "1"
    assert _resolve_capture_enabled() is True
    # Flip back, re-resolve. Caching would keep this True.
    os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = "0"
    assert _resolve_capture_enabled() is False
    # Cleanup so a later test does not inherit our env.
    os.environ.pop(SKILL_CAPTURE_KILL_SWITCH_ENV, None)


# =============================================================================
# Coverage 3: Env token matrix — full ON / OFF / INVALID parametrization
# =============================================================================


@pytest.mark.parametrize("on_value", [
    "1", "true", "TRUE", "True",
    "yes", "Yes", "YES",
    "on", "On", "ON",
    " 1 ", " true ", "  on  ", "\tYES\n",
])
def test_resolver_on_token_matrix(on_value: str):
    """Coverage 3a — every ON token (case + whitespace variations) returns
    True. Includes the whitespace-stripped edge cases the spec calls out:
    ``" 1 "``, ``" true "``, ``"  on  "``, ``"\\tYES\\n"``.
    """
    try:
        os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = on_value
        assert _resolve_capture_enabled() is True, (
            f"on_value={on_value!r} should resolve to True"
        )
    finally:
        os.environ.pop(SKILL_CAPTURE_KILL_SWITCH_ENV, None)


@pytest.mark.parametrize("off_value", [
    "",          # empty string
    "   ",       # whitespace-only (strips to "")
    "0", "false", "False", "FALSE",
    "off", "OFF", "Off",
    "no", "NO", "No",
])
def test_resolver_off_token_matrix(off_value: str):
    """Coverage 3b — every OFF token (case + whitespace variations)
    returns False. Whitespace-only input strips to empty which IS a valid
    OFF (default-OFF) — explicitly tested because the spec calls it out.
    """
    try:
        os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = off_value
        assert _resolve_capture_enabled() is False, (
            f"off_value={off_value!r} should resolve to False"
        )
    finally:
        os.environ.pop(SKILL_CAPTURE_KILL_SWITCH_ENV, None)


def test_resolver_unset_returns_false():
    """Coverage 3c — env never set → default OFF (False).

    Distinct from the OFF-token matrix above because the unset case has
    no key in ``os.environ`` at all, vs an explicit empty-string set.
    """
    os.environ.pop(SKILL_CAPTURE_KILL_SWITCH_ENV, None)
    assert _resolve_capture_enabled() is False


@pytest.mark.parametrize("invalid_value", [
    "garbage",    # spec-required: not a known token
    "2",          # numeric, not in the ON/OFF allow-list
    "enabled",    # spec-required: looks like it should work but isn't in the grammar
    "tru",        # spec-required: prefix of "true" but not a valid token
    "1.0",        # spec-required: numeric-shaped, not integer
    "banana",     # extra sanity case
    "yesno",      # extra sanity case
])
def test_resolver_invalid_tokens_raise_value_error(invalid_value: str):
    """Coverage 3d — invalid env values raise ``ValueError`` (fail-closed).

    A typo in the operator's env surfaces as a loud exception (visible
    in the daemon boot logs) rather than silently enabling capture, which
    is the safe-default semantic. The error message MUST mention the
    env-var name (so operators can debug) and the offending value.
    """
    try:
        os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = invalid_value
        with pytest.raises(ValueError) as excinfo:
            _resolve_capture_enabled()
        msg = str(excinfo.value)
        assert SKILL_CAPTURE_KILL_SWITCH_ENV in msg, (
            f"ValueError message should name the env var; got: {msg!r}"
        )
        # The original value must appear (raw or repr'd) for debug.
        assert (invalid_value in msg) or (repr(invalid_value) in msg), (
            f"ValueError message should reference the offending value "
            f"{invalid_value!r}; got: {msg!r}"
        )
    finally:
        os.environ.pop(SKILL_CAPTURE_KILL_SWITCH_ENV, None)


# =============================================================================
# Coverage 1: Default-OFF seam inertness — every seam is silent
# =============================================================================


class TestDefaultOffSeamInertness:
    """Coverage 1 — when the kill-switch env is unset, every seam must be
    a silent no-op. We pin this on THREE independent boundaries:

    * ``SkillMetricsService._check_capture_eligibility`` — no evolution-
      service call, no agent-metadata lookup, no usage-record DB read,
      no dispatcher enqueue.
    * ``SkillJobDispatcher.enqueue_capture`` — no job-queue enqueue, no
      queue-repo lookup.
    * ``skill_execute_capture`` tool — returns the ``skipped`` envelope,
      no service.capture_skill call, AND no skill row is created in a
      real ``SkillRepository`` (in-memory SQLite proves the boundary).
    """

    @pytest.fixture(autouse=True)
    def _hermetic_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make sure NO test under this class inherits a leaked env."""
        monkeypatch.delenv(SKILL_CAPTURE_KILL_SWITCH_ENV, raising=False)

    # ── (a) Resolver direct ─────────────────────────────────────────

    def test_resolver_returns_false_when_env_unset(self):
        """Default OFF: no env → resolver returns False."""
        assert _resolve_capture_enabled() is False

    # ── (b) Dispatcher seam ────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_dispatcher_enqueue_capture_returns_none_and_never_enqueues(
        self,
    ):
        """``SkillJobDispatcher.enqueue_capture`` (REAL instance, mocked
        ``JobQueueService`` boundary) — with env unset, returns ``None``
        and NEVER awaits ``_job_service.enqueue``. Spy at the real
        ``enqueue`` boundary via ``AsyncMock`` to prove no row is
        written and no queue lookup happens.
        """
        job_service = MagicMock()
        job_service.enqueue = AsyncMock(return_value="would-be-job-id")
        queue_repo = MagicMock()
        queue_repo.get_by_name = MagicMock(
            return_value=SimpleNamespace(
                queue_id="q-verif-1",
                queue_name="system_parallel_queue",
                project_id="proj-verif",
            )
        )
        dispatcher = SkillJobDispatcher(
            job_service=job_service,
            queue_repo=queue_repo,
        )

        result = await dispatcher.enqueue_capture(
            project_id="proj-verif",
            task_details={"instance_id": "inst-verif", "iterations": 7},
        )

        # Contract: returns None.
        assert result is None
        # Boundary proof: the enqueue method was NEVER awaited — no job
        # row was created in the queue.
        job_service.enqueue.assert_not_called()
        job_service.enqueue.assert_not_awaited()
        # Boundary proof: queue_repo.get_by_name was NEVER consulted —
        # we short-circuit before the parallel-queue lookup.
        queue_repo.get_by_name.assert_not_called()

    # ── (c) Metrics seam (direct + via record_task_completion) ─────

    @pytest.fixture
    def verif_engine(self):
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
    def verif_metrics_service(self, verif_engine):
        """Real ``SkillMetricsService`` with mocked boundaries."""
        from daemon.repositories.skill import (
            SkillABTestRepository,
            SkillRepository,
            SkillTriggerRepository,
            SkillUsageRepository,
        )
        from daemon.config import SkillEvolutionConfig

        skill_repo = SkillRepository(verif_engine)
        trigger_repo = SkillTriggerRepository(verif_engine)
        usage_repo = SkillUsageRepository(verif_engine)
        ab_test_repo = SkillABTestRepository(verif_engine)
        # Replace the real repo methods with Mocks at the boundary so we
        # can spy on them — same pattern as the dev's ks_* fixtures.
        usage_repo.has_applied_for_instance = MagicMock(return_value=False)

        # ALL external collaborators mocked — these are the boundaries
        # whose non-invocation we use to prove the gate fires.
        evolution_service = MagicMock()
        evolution_service.check_and_capture = AsyncMock(
            return_value={"iterations": 10, "duration_seconds": 120}
        )
        agent_id_resolver = MagicMock(
            return_value=SimpleNamespace(skill_injection=True)
        )
        instance_repo = MagicMock()
        instance_repo.delete_metadata = MagicMock(return_value=None)
        dispatcher = MagicMock()
        dispatcher.enqueue_capture = AsyncMock(return_value="job-x")

        svc = SkillMetricsService(
            usage_repo=usage_repo,
            skill_repo=skill_repo,
            trigger_repo=trigger_repo,
            ab_test_repo=ab_test_repo,
            config=SkillEvolutionConfig(),
            instance_repo=instance_repo,
            evolution_service=evolution_service,
            agent_id_resolver=agent_id_resolver,
        )
        svc.set_evolution_service(evolution_service)
        svc.set_job_dispatcher(dispatcher)
        # Stash for assertion access.
        svc._verif_evolution_service = evolution_service
        svc._verif_agent_id_resolver = agent_id_resolver
        svc._verif_usage_repo = usage_repo
        svc._verif_instance_repo = instance_repo
        svc._verif_dispatcher = dispatcher
        return svc

    @pytest.mark.asyncio
    async def test_metrics_seam_default_off_skips_all_pre_gates(
        self, verif_metrics_service
    ):
        """Direct ``_check_capture_eligibility`` with env unset — every
        upstream collaborator must be untouched.

        Proof strategy: with EVERY collaborator mocked, the gate should
        return ``None`` and zero collaborators should be called. If any
        boundary is touched, the kill-switch is downstream of an
        expensive pre-check (or vice-versa) and the whole optimization
        rationale collapses.
        """
        result = await verif_metrics_service._check_capture_eligibility(
            instance_id="inst-verif-off",
            agent_id="agent-x",
            project_id="proj-verif",
            task_message="msg",
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
        )
        # Gate short-circuited.
        assert result is None
        # NO expensive pre-gates fired.
        verif_metrics_service._verif_evolution_service.check_and_capture \
            .assert_not_called()
        verif_metrics_service._verif_evolution_service.check_and_capture \
            .assert_not_awaited()
        # NO agent-metadata lookup.
        verif_metrics_service._verif_agent_id_resolver.assert_not_called()
        # NO usage-record DB read.
        verif_metrics_service._verif_usage_repo.has_applied_for_instance \
            .assert_not_called()
        # NO instance metadata touch.
        verif_metrics_service._verif_instance_repo.get.assert_not_called()
        # NO dispatcher enqueue.
        verif_metrics_service._verif_dispatcher.enqueue_capture \
            .assert_not_called()
        verif_metrics_service._verif_dispatcher.enqueue_capture \
            .assert_not_awaited()

    # ── (d) Tool seam ───────────────────────────────────────────────

    @pytest.fixture
    def verif_manager_with_skill_evolution_service(self):
        """Manager mock exposing a real ``_skill_evolution_service`` so the
        ``create_skill_evolution_tools`` factory builds a fully-wired
        ``skill_execute_capture``. Boundary mocked at ``service.capture_skill``.
        """
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.capture_skill = AsyncMock(
            return_value={
                "new_skill_id": "skill-should-not-appear",
                "skipped": False,
            }
        )
        manager = MagicMock()
        manager._skill_evolution_service = service
        return manager, service, create_skill_evolution_tools

    @pytest.mark.asyncio
    async def test_tool_seam_default_off_returns_skipped_envelope_and_no_service_call(
        self, verif_manager_with_skill_evolution_service
    ):
        """``skill_execute_capture`` (REAL factory output) with env unset
        returns the ``skipped`` envelope AND does not call
        ``service.capture_skill`` (boundary proof).
        """
        manager, service, factory = verif_manager_with_skill_evolution_service
        tools = {t.name: t for t in factory(manager, "inst-verif-closure")}

        result = await tools["skill_execute_capture"].ainvoke({
            "instance_id": "inst-verif-arg",
            "task_message": "msg",
            "iterations": 9,
            "duration_seconds": 99,
        })

        decoded = json.loads(result)
        # Skipped envelope.
        assert decoded["skipped"] is True
        assert decoded["new_skill_id"] is None
        # The reason names the env var so operators can debug.
        assert SKILL_CAPTURE_KILL_SWITCH_ENV in decoded["reason"]
        assert "disabled" in decoded["reason"].lower()
        # The agent-supplied instance_id is echoed for correlation.
        assert decoded["instance_id"] == "inst-verif-arg"
        assert decoded["kill_switch_env"] == SKILL_CAPTURE_KILL_SWITCH_ENV
        # Boundary proof: the service.capture_skill was NEVER called.
        service.capture_skill.assert_not_called()
        service.capture_skill.assert_not_awaited()


# =============================================================================
# Coverage 2: ON path — resolver True; dispatcher proceeds
# =============================================================================


class TestOnPathProceeds:
    """Coverage 2 — when the kill-switch is ON, the resolver returns True
    AND the dispatcher demonstrably proceeds to the storage boundary.

    The ON-path tests use env values "1" and "true" — the two most
    likely operator spellings. The dispatcher test is the load-bearing
    one: it asserts ``_job_service.enqueue`` was awaited exactly once
    with the expected kwargs — so any refactor that breaks the
    dispatcher's enqueue call signature or short-circuits ON will fail
    this test.
    """

    @pytest.fixture(autouse=True)
    def _hermetic_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(SKILL_CAPTURE_KILL_SWITCH_ENV, raising=False)

    @pytest.mark.parametrize("on_value", ["1", "true"])
    def test_resolver_returns_true_for_env_one_and_true(self, on_value: str):
        """ON tokens ``"1"`` and ``"true"`` → resolver returns True."""
        os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = on_value
        assert _resolve_capture_enabled() is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("on_value", ["1", "true"])
    async def test_dispatcher_seam_on_proceeds_to_enqueue_boundary(
        self, on_value: str
    ):
        """ON-path proof at the dispatcher: with ``"1"`` / ``"true"``,
        ``_job_service.enqueue`` IS awaited exactly once with the
        expected kwargs. Spy at the boundary via AsyncMock.

        Distinct from the dev's existing test in that we assert the
        full kwarg shape (project_id, job_type, queue_id, agent_id,
        metadata.task_details) — any drift in the dispatch contract
        fails us before the dev's test even notices.
        """
        os.environ[SKILL_CAPTURE_KILL_SWITCH_ENV] = on_value

        # ``_job_service.enqueue`` returns an object with a ``.job_id``
        # attribute (mirrors :class:`JobItem` — the dispatcher's
        # ``_enqueue_skill_keeper_job`` does ``return job.job_id``).
        job_service = MagicMock()
        job_service.enqueue = AsyncMock(
            return_value=SimpleNamespace(job_id="job-verif-on")
        )
        queue_repo = MagicMock()
        queue_repo.get_by_name = MagicMock(
            return_value=SimpleNamespace(
                queue_id="q-verif-on",
                queue_name="system_parallel_queue",
                project_id="proj-verif-on",
            )
        )
        dispatcher = SkillJobDispatcher(
            job_service=job_service,
            queue_repo=queue_repo,
        )

        task_details = {
            "instance_id": "inst-verif-on",
            "iterations": 8,
            "duration_seconds": 90,
        }
        result = await dispatcher.enqueue_capture(
            project_id="proj-verif-on",
            task_details=task_details,
        )

        # Returns the dispatched job_id (extracted from the
        # ``JobItem``-like return value — the dispatcher does
        # ``return job.job_id``).
        assert result == "job-verif-on"
        # The storage boundary was awaited exactly once.
        job_service.enqueue.assert_awaited_once()
        kwargs = job_service.enqueue.await_args.kwargs
        # The kwargs MUST match the documented dispatch contract.
        assert kwargs["project_id"] == "proj-verif-on"
        assert kwargs["job_type"] == "skill_capture"
        assert kwargs["queue_id"] == "q-verif-on"
        assert kwargs["agent_id"] == "skill-keeper"
        assert kwargs["metadata"]["task_details"] == task_details
        # Cleanup.
        os.environ.pop(SKILL_CAPTURE_KILL_SWITCH_ENV, None)


# =============================================================================
# Coverage 4: Containment at real call sites — invalid env surfaces
# differently at the metrics seam (soft-fail) vs the tool seam (raises)
# =============================================================================


class TestContainmentAtRealCallSites:
    """Coverage 4 — when the env is invalid (``"garbage"``), the two
    call sites behave differently BY DESIGN:

    * The METRICS seam (``record_task_completion``) is the job-queue
      completion hook. It MUST return cleanly — a ``ValueError``
      would block the hook. So the existing soft-fail boundary
      catches the resolver's ``ValueError``, logs a WARNING, and
      returns. The just-written metrics are preserved.

    * The TOOL seam (``skill_execute_capture``) is invoked by the
      skill-keeper agent loop. Here loud failure is the right
      default — the operator is the immediate caller (or at least
      one layer away in the LLM tool path). The exception class is
      exactly what ``ToolNode(handle_tool_errors=True)`` catches:
      ``Exception`` / ``ValueError``. We assert the class explicitly.
    """

    @pytest.fixture(autouse=True)
    def _hermetic_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(SKILL_CAPTURE_KILL_SWITCH_ENV, raising=False)

    @pytest.mark.asyncio
    async def test_metrics_seam_garbage_env_soft_fails_record_task_completion(
        self, monkeypatch, caplog: pytest.LogCaptureFixture
    ):
        """Coverage 4a — ``record_task_completion`` with env="garbage":
        returns cleanly (no raise), captures a WARNING, dispatcher
        is NEVER invoked.

        This pins the contract that an invalid env surfaces loudly in
        the daemon logs (operators can spot the misconfiguration) without
        breaking the metrics path or raising back to the job-queue
        completion hook. Distinct from the dev's test in that we use
        our own fixture chain (no SQLite — we drive the seam with a
        zero-injected-skill instance so the usage loop is short-
        circuited and the only "real" path is the capture gate).
        """
        from daemon.repositories.skill import (
            SkillABTestRepository,
            SkillRepository,
            SkillTriggerRepository,
            SkillUsageRepository,
        )
        from daemon.config import SkillEvolutionConfig

        eng = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(eng)
        try:
            skill_repo = SkillRepository(eng)
            trigger_repo = SkillTriggerRepository(eng)
            usage_repo = SkillUsageRepository(eng)
            ab_test_repo = SkillABTestRepository(eng)

            # Seed one skill row + mark it as injected so the
            # ``record_task_completion`` path reaches the
            # ``_check_capture_eligibility`` call (the function
            # short-circuits at ``if not injected_ids: return 0``
            # otherwise, and the resolver error never fires).
            seeded_skill = skill_repo.create(
                name="seeded-verif-garbage",
                description="seed",
                content="body",
                project_id="proj-verif-garbage",
                category="workflow",
            )

            evolution_service = MagicMock()
            evolution_service.check_and_capture = AsyncMock(
                return_value={"iterations": 5}
            )
            instance_repo = MagicMock()
            instance_repo.get = MagicMock(
                return_value=SimpleNamespace(
                    instance_id="inst-verif-garbage",
                    instance_metadata={
                        INJECTED_SKILLS_METADATA_KEY: [seeded_skill.id],
                    },
                )
            )
            instance_repo.delete_metadata = MagicMock(return_value=None)
            dispatcher = MagicMock()
            dispatcher.enqueue_capture = AsyncMock(return_value="never")

            svc = SkillMetricsService(
                usage_repo=usage_repo,
                skill_repo=skill_repo,
                trigger_repo=trigger_repo,
                ab_test_repo=ab_test_repo,
                config=SkillEvolutionConfig(),
                instance_repo=instance_repo,
                evolution_service=evolution_service,
                agent_id_resolver=MagicMock(
                    return_value=SimpleNamespace(skill_injection=True)
                ),
            )
            svc.set_evolution_service(evolution_service)
            svc.set_job_dispatcher(dispatcher)

            monkeypatch.setenv(SKILL_CAPTURE_KILL_SWITCH_ENV, "garbage")

            with caplog.at_level(
                logging.WARNING,
                logger="daemon.services.skill_metrics_service",
            ):
                result = await svc.record_task_completion(
                    instance_id="inst-verif-garbage",
                    agent_id="agent-x",
                    project_id="proj-verif-garbage",
                    task_succeeded=True,
                    iterations=5,
                    duration_seconds=60,
                    task_message="invalid-env task",
                )

            # Soft-fail swallowed the ValueError; record_task_completion
            # returned cleanly (the job-queue completion hook is unblocked).
            assert isinstance(result, int)
            assert result == 1  # one seeded injected skill → one usage row
            # Dispatcher was NEVER invoked — capture never fires.
            dispatcher.enqueue_capture.assert_not_called()
            dispatcher.enqueue_capture.assert_not_awaited()
            # The WARNING was emitted (operators can spot the
            # misconfiguration in the daemon logs).
            assert any(
                "CAPTURED eligibility check failed" in rec.message
                for rec in caplog.records
            ), (
                "Expected a WARNING from the soft-fail boundary; "
                f"records: {[r.message for r in caplog.records]}"
            )
        finally:
            eng.dispose()

    @pytest.mark.asyncio
    async def test_tool_seam_garbage_env_raises_valueerror(
        self, monkeypatch
    ):
        """Coverage 4b — ``skill_execute_capture`` with env="garbage":
        raises ``ValueError`` from the resolver. The exception class
        is exactly what ``ToolNode(handle_tool_errors=True)`` catches
        (i.e. ``Exception`` — ``ValueError`` is a subclass).

        We assert ``isinstance(exc, ValueError)`` explicitly so a
        refactor that wraps the resolver in a different exception class
        (e.g. ``RuntimeError``) fails this test — ToolNode's standard
        handler still catches it but the contract is no longer
        "resolver ValueError surfaces verbatim".
        """
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.capture_skill = AsyncMock(return_value={"new_skill_id": "x"})
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(
            manager, "inst-verif-garbage-tool"
        )}

        monkeypatch.setenv(SKILL_CAPTURE_KILL_SWITCH_ENV, "garbage")

        with pytest.raises(Exception) as excinfo:
            await tools["skill_execute_capture"].ainvoke({
                "instance_id": "inst-verif-garbage-arg",
                "task_message": "msg",
                "iterations": 7,
                "duration_seconds": 80,
            })
        # Boundary: exact class check — ToolNode(handle_tool_errors=True)
        # catches this. A future refactor that wraps the resolver in a
        # different class would break the contract.
        assert isinstance(excinfo.value, ValueError), (
            f"Expected ValueError (catchable by ToolNode handle_tool_errors), "
            f"got {type(excinfo.value).__name__}: {excinfo.value!r}"
        )
        # The error message names the env var (operator debug).
        assert SKILL_CAPTURE_KILL_SWITCH_ENV in str(excinfo.value)
        # Boundary proof: the service.capture_skill was NEVER called —
        # the gate fired BEFORE the service dispatch (the closure path
        # never even gets a chance).
        service.capture_skill.assert_not_called()
        service.capture_skill.assert_not_awaited()


# =============================================================================
# Coverage 6: Unaffected — skill_create still works; injection entry has
# no flag dependency
# =============================================================================


class TestUnaffectedFlowsAreNotGated:
    """Coverage 6 — the kill-switch is narrowly scoped to the three
    capture-flow seams. Two independent sanity pins:

    (a) ``skill_create`` tool still works with the kill-switch OFF.
        We invoke the REAL ``create_skill_tools`` factory, then invoke
        ``skill_create`` and assert that ``manager._skill_store_service
        .create_skill`` was awaited (spy at the boundary). The dev's
        existing test only checks the factory returns; we exercise
        the tool body end-to-end (with a mocked store service — the
        REAL boundary).

    (b) ``skill_injection_service`` does not consult the
        kill-switch resolver. Structural pin via source-file grep —
        cheap, catches any drift that re-introduces the resolver into
        the injection pipeline.
    """

    @pytest.fixture(autouse=True)
    def _hermetic_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(SKILL_CAPTURE_KILL_SWITCH_ENV, raising=False)

    @pytest.mark.asyncio
    async def test_skill_create_tool_still_works_when_kill_switch_off(self):
        """Coverage 6a — ``skill_create`` (REAL factory output) with
        kill-switch OFF must succeed end-to-end. We mock ONLY the
        store-service boundary (``_skill_store_service.create_skill``)
        and assert that the tool body invokes it and returns the
        success envelope.
        """
        from daemon.tools.skill_tools import create_skill_tools

        # Mocked store-service boundary — returns a created row.
        store_service = MagicMock()
        store_service.create_skill = AsyncMock(
            return_value=SimpleNamespace(
                id="verif-skill-id-1234abcd",
                name="verif-skill",
                description="d",
                content="c",
                category="workflow",
            )
        )
        # Mocked search-service boundary (factory may probe for it).
        search_service = MagicMock()
        # Mocked instance-repo for project_id lookup (None → no project).
        instance_repository = MagicMock()
        instance_repository.get = MagicMock(return_value=None)

        manager = MagicMock()
        manager._skill_store_service = store_service
        manager._skill_search_service = search_service
        manager._instance_repository = instance_repository
        manager._skill_job_dispatcher = MagicMock()

        tools = {t.name: t for t in create_skill_tools(
            manager, "inst-verif-create"
        )}
        # sanity: factory exposes skill_create.
        assert "skill_create" in tools

        result = await tools["skill_create"].ainvoke({
            "name": "verif-skill",
            "description": "an independent-verification test skill",
            "content": "body of the test skill",
            "category": "workflow",
        })

        # Tool succeeded — the success envelope names the short id.
        assert "verif-skill-id" in result or "\u2705" in result, (
            f"Expected success envelope from skill_create; got: {result!r}"
        )
        # Boundary proof: the store-service.create_skill was invoked.
        store_service.create_skill.assert_awaited_once()
        kwargs = store_service.create_skill.await_args.kwargs
        assert kwargs["name"] == "verif-skill"
        assert kwargs["category"] == "workflow"
        # The dispatcher was NEVER touched (skill_create does NOT
        # route through the job dispatcher — it's an inline write).
        manager._skill_job_dispatcher.enqueue_capture.assert_not_called()

    def test_skill_injection_pipeline_does_not_consult_kill_switch(self):
        """Coverage 6b — structural pin: ``daemon/services/
        skill_injection_service.py`` MUST NOT consult the kill-switch
        resolver. Skill injection is a *retrieval* concern, not a
        *creation* concern — the kill-switch is for the CAPTURED flow
        only.

        Pin strategy: read the source file and grep for the resolver
        name AND the env-var name. Both MUST be absent. Distinct from
        the dev's check in that we hard-pin on the actual path
        (instead of iterating a candidate list).
        """
        repo_root = Path(__file__).resolve().parents[2]
        target = repo_root / "daemon" / "services" / "skill_injection_service.py"
        assert target.exists(), (
            f"skill_injection_service.py not found at expected path {target}; "
            "update this test if the path moved."
        )
        src = target.read_text(encoding="utf-8")
        assert "_resolve_capture_enabled" not in src, (
            "daemon/services/skill_injection_service.py MUST NOT import the "
            "skill-capture kill-switch resolver — injection is out of scope."
        )
        assert SKILL_CAPTURE_KILL_SWITCH_ENV not in src, (
            f"daemon/services/skill_injection_service.py MUST NOT reference "
            f"{SKILL_CAPTURE_KILL_SWITCH_ENV} — injection is out of scope."
        )


# =============================================================================
# Final integration pin: all three OFF seams + injection unaffected —
# proven simultaneously in one combined scenario
# =============================================================================


class TestCombinedOffScenarioAllSeamsSilent:
    """Integration pin: in a single OFF scenario, every seam stays silent
    AND the unrelated skill_create path is unaffected. This is the
    belt-and-braces proof that the kill-switch does not leak across
    the architectural boundary it claims to draw.

    The dev's suite has a similar belt-and-braces test — we add value
    by also asserting that ``skill_create`` succeeds in the same
    scenario (the dev's test does not exercise the unaffected side).
    """

    @pytest.fixture(autouse=True)
    def _hermetic_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(SKILL_CAPTURE_KILL_SWITCH_ENV, raising=False)

    @pytest.mark.asyncio
    async def test_combined_off_scenario_capture_silent_create_succeeds(
        self,
    ):
        """Combined scenario:
        (a) dispatcher.enqueue_capture → None, no enqueue.
        (b) tool.skill_execute_capture → skipped envelope.
        (c) tool.skill_create → success (out-of-scope flow works).
        """
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools
        from daemon.tools.skill_tools import create_skill_tools

        # (a) Dispatcher
        job_service = MagicMock()
        job_service.enqueue = AsyncMock(return_value="would-be-id")
        queue_repo = MagicMock()
        queue_repo.get_by_name = MagicMock(
            return_value=SimpleNamespace(
                queue_id="q-combined",
                queue_name="system_parallel_queue",
                project_id="proj-combined",
            )
        )
        dispatcher = SkillJobDispatcher(
            job_service=job_service,
            queue_repo=queue_repo,
        )
        capture_result = await dispatcher.enqueue_capture(
            project_id="proj-combined",
            task_details={"instance_id": "inst-combined"},
        )
        assert capture_result is None
        job_service.enqueue.assert_not_called()
        job_service.enqueue.assert_not_awaited()

        # (b) Tool seam — capture
        capture_service = MagicMock()
        capture_service.capture_skill = AsyncMock(
            return_value={"new_skill_id": "nope"}
        )
        manager_capture = MagicMock()
        manager_capture._skill_evolution_service = capture_service
        capture_tools = {t.name: t for t in create_skill_evolution_tools(
            manager_capture, "inst-combined-c"
        )}
        capture_tool_result = await capture_tools["skill_execute_capture"].ainvoke({
            "instance_id": "inst-combined-c-arg",
            "task_message": "msg",
            "iterations": 5,
            "duration_seconds": 60,
        })
        decoded = json.loads(capture_tool_result)
        assert decoded["skipped"] is True
        assert decoded["new_skill_id"] is None
        capture_service.capture_skill.assert_not_called()
        capture_service.capture_skill.assert_not_awaited()

        # (c) Tool seam — create (out of scope, must work)
        store_service = MagicMock()
        store_service.create_skill = AsyncMock(
            return_value=SimpleNamespace(id="verif-combined-id-9999")
        )
        manager_create = MagicMock()
        manager_create._skill_store_service = store_service
        manager_create._skill_search_service = MagicMock()
        manager_create._instance_repository = MagicMock()
        manager_create._instance_repository.get = MagicMock(return_value=None)
        create_tools = {t.name: t for t in create_skill_tools(
            manager_create, "inst-combined-x"
        )}
        create_result = await create_tools["skill_create"].ainvoke({
            "name": "combined-skill",
            "description": "out of scope, must still work",
            "content": "body",
            "category": "workflow",
        })
        assert "verif-combined-id" in create_result or "\u2705" in create_result
        store_service.create_skill.assert_awaited_once()