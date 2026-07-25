"""Tests for agent_id resolution after the coder/developer alias removal.

Context: ``AGENT_ID_ALIASES`` (which previously mapped ``coder`` →
``developer``) was removed because ``coder`` is now a standalone,
registered agent at ``agents/coder/``. These tests pin the post-removal
contract:

* ``_restore_instance()`` must load coder's metadata when a DB row has
  ``agent_id='coder'`` and complete the restore without raising (no alias
  hop is needed — ``coder`` resolves directly).
* ``job_queue_service.enqueue()`` must create a job with
  ``agent_id='coder'`` and ``agent_dir='/agents/coder'`` when the caller
  requests the standalone coder agent.
* The canonical ``developer`` agent_id must continue to resolve and
  enqueue without regression.

These are registry-resolution tests; they do not exercise any DB
migration.
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from daemon import constants
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.services.job_queue_service import JobQueueService


# ═════════════════════════════════════════════════════════════════════════════
# Coder Agent ID Coverage Tests
# ═════════════════════════════════════════════════════════════════════════════
# These tests verify that DB rows / enqueue requests carrying
# ``agent_id='coder'`` resolve correctly via the registry AFTER the alias
# removal.
#
# Registry now has NO alias mapping (``AGENT_ID_ALIASES = {}``), and ``coder``
# is a real, registered standalone agent at ``agents/coder/``. So:
#   resolve_pure_id("coder") → "coder"          (standalone agent, no alias hop)
#   resolve_pure_id("developer") → "developer"  (canonical agent)
#   get_resolved("coder") → coder AgentMetadata (path=/agents/coder)
#   get_resolved("developer") → developer AgentMetadata (path=/agents/developer)
#
# Coverage scope:
#   - ``_restore_instance()`` must load coder's metadata when DB row has
#     ``agent_id='coder'`` and complete the restore without raising. Today
#     ``coder`` is registered, so the lookup succeeds directly (no alias).
#   - ``job_queue_service.enqueue()`` must create a job with
#     ``agent_id='coder'`` and ``agent_dir=/agents/coder`` when the caller
#     requests the standalone coder agent.
#
# Historical context: before the alias removal these tests asserted that
# ``resolve_pure_id('coder')`` mapped to ``'developer'`` via
# ``AGENT_ID_ALIASES``. That mapping is gone now; the tests pin the
# post-removal contract instead.
# ═════════════════════════════════════════════════════════════════════════════


# Test system project ID — mirrors tests/job_queue/conftest.py so the
# alias-resolution enqueue tests can run from tests/unit/ without depending
# on that conftest's autouse fixture.
_TEST_SYSTEM_PROJECT_ID = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"


@pytest.fixture(autouse=True)
def _setup_system_default_project():
    """Set SYSTEM_DEFAULT_PROJECT_ID so normalize_project_id() works.

    job_queue_service.enqueue() calls normalize_project_id() internally
    (see daemon/services/job_queue_service.py:351) which raises
    RuntimeError if SYSTEM_DEFAULT_PROJECT_ID is None. The
    tests/job_queue/conftest.py fixture that handles this is NOT
    applied to tests/unit/, so we declare it locally.
    """
    original = constants.SYSTEM_DEFAULT_PROJECT_ID
    constants.SYSTEM_DEFAULT_PROJECT_ID = _TEST_SYSTEM_PROJECT_ID
    try:
        yield
    finally:
        constants.SYSTEM_DEFAULT_PROJECT_ID = original


class TestRestoreInstanceWithCoderAgentId:
    """Verify ``_restore_instance()`` handles ``agent_id='coder'`` correctly.

    After the alias removal, ``coder`` is a standalone registered agent at
    ``agents/coder/``. ``_restore_instance()`` looks up the agent via
    ``registry.get_resolved(meta.agent_id)``, which (with no aliases)
    resolves directly to coder's metadata. The restore must complete
    without raising ``ValueError('Agent not found: coder')``.
    """

    @staticmethod
    def _make_mock_manager() -> tuple[MagicMock, MagicMock]:
        """Build the mock manager + cancellation service used by restore tests.

        Centralizes the boilerplate so the test methods can focus on the
        alias resolution contract. Returns
        ``(mock_manager, mock_cancellation_service)`` since
        ``InstanceLifecycleService`` is constructed with both.
        """
        mock_manager = MagicMock()
        mock_cancellation_service = MagicMock()

        mock_manager._instance_repository = MagicMock()
        mock_manager._project_repository = MagicMock()
        mock_manager._engine = MagicMock()
        mock_manager._live_hub = MagicMock()
        mock_manager._checkpointer = None
        mock_manager._compactor = None
        mock_manager.instances = {}
        mock_manager.prompt_cache = MagicMock()
        mock_manager._mcp_service = None

        mock_config = MagicMock()
        mock_config.queue.llm_retry_transient_attempts = 3
        mock_config.queue.llm_retry_timeout_attempts = 2
        mock_config.llm.base_url = None
        mock_config.llm.api_key = "test-key"
        mock_config.llm.model = "gpt-4"
        mock_config.llm.model_vision = False
        mock_config.llm.temperature = 0.7
        mock_config.llm.request_timeout = 60
        mock_config.limits.graph_recursion_limit = 1000
        mock_manager.config = mock_config

        return mock_manager, mock_cancellation_service

    def test_restore_instance_with_coder_agent_id_does_not_raise(self):
        """``_restore_instance`` with ``agent_id='coder'`` loads coder's metadata.

        After the alias removal, ``coder`` is a registered standalone agent
        at ``agents/coder/``. ``_restore_instance()`` calls
        ``registry.get_resolved(meta.agent_id)`` which returns coder's
        metadata directly (no alias hop). The restore must succeed.

        Before the alias removal this test simulated a stale DB row that
        relied on ``coder`` → ``developer`` alias resolution to succeed.
        The new contract is simpler: ``coder`` resolves to coder, period.
        """
        # ── Mock manager ─────────────────────────────────────────────────────
        mock_manager, mock_cancellation_service = self._make_mock_manager()
        service = InstanceLifecycleService(mock_manager, mock_cancellation_service)

        # ── Mock Instance row with agent_id='coder' (the standalone agent) ──
        mock_meta = MagicMock()
        mock_meta.instance_id = "stale-instance-001"
        mock_meta.agent_id = "coder"           # ← standalone coder agent
        mock_meta.agent_dir = "/agents/coder"  # ← coder's on-disk path
        mock_meta.agent_tag = None             # ← base version (no tag) on restore
        mock_meta.parent_id = None
        mock_meta.instance_metadata = {"mcp_tool_names": []}

        # ── Patch registry and manager helpers ───────────────────────────────
        with (
            patch("daemon.services.instance_lifecycle.get_registry") as mock_get_registry,
            patch("daemon.services.instance_lifecycle.append_context_key") as mock_append_ctx,
            patch("daemon.manager.load_and_cache_prompt") as mock_load_prompt,
            patch("daemon.manager.build_instance_graph") as mock_build_graph,
            patch("daemon.manager.create_instance_tools") as mock_create_tools,
        ):
            # Configure the mock registry: ``_restore_instance`` now calls
            # ``get_version(agent_id, agent_tag)`` first and only falls back
            # to ``get_resolved`` when that returns None (base-version case).
            # We stub ``get_version`` → None so the test exercises the
            # ``get_resolved`` fallback, which returns coder's metadata
            # directly (no alias hop, no separate ``resolve_pure_id`` call).
            mock_registry = MagicMock()
            mock_registry.get_version.return_value = None

            # get_resolved('coder') returns coder's metadata directly.
            mock_coder_meta = MagicMock()
            mock_coder_meta.path = Path("/agents/coder")
            mock_coder_meta.llm_model = None
            mock_registry.get_resolved.side_effect = lambda aid: (
                mock_coder_meta if aid == "coder" else None
            )
            mock_get_registry.return_value = mock_registry

            mock_load_prompt.return_value = ("You are a coder.", 10)
            mock_create_tools.return_value = []
            mock_build_graph.return_value = MagicMock()
            mock_append_ctx.return_value = "You are a coder."

            # ── Execute ────────────────────────────────────────────────────
            # Must succeed because 'coder' resolves to a registered agent.
            result = service._restore_instance("stale-instance-001", mock_meta)

            # ── Verify the registry was consulted with 'coder' ───────────
            # With the alias map empty, ``_restore_instance`` looks up the
            # agent via ``registry.get_resolved`` and uses ``meta.agent_id``
            # directly. No ``resolve_pure_id`` alias hop is needed any more.
            mock_registry.get_resolved.assert_called_with("coder")
            # The graph must be built and stored in instances dict
            assert result is not None
            mock_build_graph.assert_called_once()
            mock_create_tools.assert_called_once()

    def test_restore_instance_with_developer_agent_id_still_works(self):
        """_restore_instance with canonical 'developer' agent_id still works.

        Sanity check: resolving an already-canonical ID should be a no-op.
        """
        mock_manager, mock_cancellation_service = self._make_mock_manager()
        service = InstanceLifecycleService(mock_manager, mock_cancellation_service)

        mock_meta = MagicMock()
        mock_meta.instance_id = "fresh-instance-002"
        mock_meta.agent_id = "developer"  # ← already canonical
        mock_meta.agent_dir = "/agents/developer"
        mock_meta.agent_tag = None         # ← base version (no tag) on restore
        mock_meta.parent_id = None
        mock_meta.instance_metadata = {"mcp_tool_names": []}

        with (
            patch("daemon.services.instance_lifecycle.get_registry") as mock_get_registry,
            patch("daemon.services.instance_lifecycle.append_context_key") as mock_append_ctx,
            patch("daemon.manager.load_and_cache_prompt") as mock_load_prompt,
            patch("daemon.manager.build_instance_graph") as mock_build_graph,
            patch("daemon.manager.create_instance_tools") as mock_create_tools,
        ):
            # ``_restore_instance`` calls ``get_version`` first and falls
            # back to ``get_resolved`` when it returns None. Stub the
            # base-version lookup to None so the fallback is exercised.
            mock_registry = MagicMock()
            mock_registry.get_version.return_value = None
            mock_developer_meta = MagicMock()
            mock_developer_meta.path = Path("/agents/developer")
            mock_developer_meta.llm_model = None
            mock_registry.get_resolved.side_effect = lambda aid: (
                mock_developer_meta if aid == "developer" else None
            )
            mock_get_registry.return_value = mock_registry

            mock_load_prompt.return_value = ("You are a developer.", 10)
            mock_create_tools.return_value = []
            mock_build_graph.return_value = MagicMock()
            mock_append_ctx.return_value = "You are a developer."

            result = service._restore_instance("fresh-instance-002", mock_meta)

            # With the alias map empty, ``_restore_instance`` looks up the
            # agent via ``registry.get_resolved`` and uses ``meta.agent_id``
            # directly. No ``resolve_pure_id`` alias hop is needed any more.
            mock_registry.get_resolved.assert_called_with("developer")
            assert result is not None


class TestJobQueueEnqueueWithCoderAgentId:
    """Verify ``job_queue_service.enqueue()`` handles ``agent_id='coder'``.

    After the alias removal, ``coder`` is a registered standalone agent.
    Both the idempotency path and the regular enqueue path must:
      * resolve ``"coder"`` via ``registry.get_resolved()`` to coder's
        metadata (no alias hop)
      * create the job with ``agent_id="coder"`` and
        ``agent_dir="/agents/coder"``
    """

    @pytest.fixture
    def mock_repository(self):
        """Minimal mock JobRepository for enqueue()."""
        repo = MagicMock()
        repo.find_by_idempotency_key = MagicMock(return_value=None)
        repo.create = MagicMock()

        def _create_or_get_side_effect(**kwargs):
            key = kwargs.get("idempotency_key")
            existing = repo.find_by_idempotency_key(key)
            if existing is not None:
                return existing, False
            new_job = repo.create(**kwargs)
            return new_job, True

        repo.create_or_get_by_idempotency_key = MagicMock(
            side_effect=_create_or_get_side_effect
        )
        return repo

    @pytest.fixture
    def mock_lock_manager(self):
        return MagicMock()

    @pytest.fixture
    def mock_queue_repo(self):
        repo = MagicMock()
        mock_queue = MagicMock()
        mock_queue.queue_id = "system-fifo-queue-id"
        mock_queue.project_id = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"
        mock_queue.queue_name = "system_fifo_queue"

        def get_by_name(project_id, queue_name):
            return mock_queue

        repo.get_by_name = MagicMock(side_effect=get_by_name)
        repo.get = MagicMock(return_value=None)
        return repo

    @pytest.fixture
    def service(self, mock_repository, mock_lock_manager, mock_queue_repo):
        return JobQueueService(
            repository=mock_repository,
            lock_manager=mock_lock_manager,
            queue_repo=mock_queue_repo,
        )

    def _make_mock_registry_coder_resolves_to_coder(self):
        """Registry mock returning canonical metadata for ``coder`` and ``developer``.

        After the alias removal, ``AGENT_ID_ALIASES`` is empty and ``coder``
        is a real agent at ``/agents/coder``. ``enqueue`` looks up the agent
        via ``registry.get_resolved(agent_id)`` (see
        ``daemon/services/job_queue_service.py:577,689``) — which with no
        aliases is functionally ``registry.get``. The mock therefore:
          * returns coder metadata (path=``/agents/coder``) for
            ``get_resolved("coder")``
          * returns developer metadata (path=``/agents/developer``) for
            ``get_resolved("developer")``
          * returns ``None`` for any other id.
        Mirrors the production ``registry.get_resolved`` semantics with no
        alias hops.
        """
        registry = MagicMock()

        mock_coder_meta = MagicMock()
        mock_coder_meta.path = Path("/agents/coder")
        mock_developer_meta = MagicMock()
        mock_developer_meta.path = Path("/agents/developer")

        def _get_resolved(aid: str):
            if aid == "coder":
                return mock_coder_meta
            if aid == "developer":
                return mock_developer_meta
            return None

        registry.get_resolved.side_effect = _get_resolved
        return registry

    @pytest.mark.asyncio
    async def test_enqueue_with_coder_agent_id_succeeds(
        self, service, mock_repository, mock_queue_repo
    ):
        """``enqueue(agent_id='coder')`` resolves to coder and creates a job.

        After the alias removal, ``coder`` is a registered standalone agent,
        so ``registry.get_resolved('coder')`` returns coder's metadata.
        ``enqueue`` uses this to derive ``agent_id`` and ``agent_dir`` for
        the new job. The job must be created with the coder identity
        (agent_id="coder", agent_dir="/agents/coder"), NOT the developer
        identity.
        """
        expected_job = MagicMock()
        expected_job.job_id = "new-job-from-coder"
        mock_repository.create.return_value = expected_job

        with patch(
            "daemon.services.job_queue_service.get_registry",
            return_value=self._make_mock_registry_coder_resolves_to_coder(),
        ):
            result = await service.enqueue(
                agent_id="coder",          # standalone coder agent
                message="test message",
                source="api",
            )

        assert result.job_id == "new-job-from-coder"
        mock_repository.create.assert_called_once()
        # The job must be created with the resolved coder identity,
        # NOT a developer alias.
        call_kwargs = mock_repository.create.call_args.kwargs
        assert call_kwargs["agent_id"] == "coder", (
            f"Expected agent_id='coder' in create(), got {call_kwargs['agent_id']!r}"
        )
        assert call_kwargs["agent_dir"] == "/agents/coder", (
            f"Expected agent_dir='/agents/coder' in create(), got {call_kwargs['agent_dir']!r}"
        )

    @pytest.mark.asyncio
    async def test_enqueue_with_coder_and_idempotency_key_succeeds(
        self, service, mock_repository, mock_queue_repo
    ):
        """``enqueue`` with ``idempotency_key`` and ``agent_id='coder'`` works.

        Exercises the idempotency code path (``daemon/services/job_queue_service.py``
        around line 577) which independently resolves the agent via
        ``registry.get_resolved``. With the alias removed, ``coder``
        resolves to the standalone coder agent and the new job is
        created with coder identity.
        """
        expected_job = MagicMock()
        expected_job.job_id = "idempotent-job-from-coder"
        mock_repository.create.return_value = expected_job

        with patch(
            "daemon.services.job_queue_service.get_registry",
            return_value=self._make_mock_registry_coder_resolves_to_coder(),
        ):
            result = await service.enqueue(
                agent_id="coder",              # standalone coder agent
                message="test message",
                source="api",
                idempotency_key="unique-key-001",  # ← triggers idempotency path
            )

        assert result.job_id == "idempotent-job-from-coder"
        mock_repository.create_or_get_by_idempotency_key.assert_called_once()
        call_kwargs = mock_repository.create_or_get_by_idempotency_key.call_args.kwargs
        assert call_kwargs["agent_id"] == "coder", (
            f"Expected agent_id='coder' in create_or_get_by_idempotency_key(), "
            f"got {call_kwargs['agent_id']!r}"
        )
        assert call_kwargs["agent_dir"] == "/agents/coder", (
            f"Expected agent_dir='/agents/coder' in create_or_get_by_idempotency_key(), "
            f"got {call_kwargs['agent_dir']!r}"
        )

    @pytest.mark.asyncio
    async def test_enqueue_with_developer_agent_id_still_works(
        self, service, mock_repository, mock_queue_repo
    ):
        """``enqueue(agent_id='developer')`` still works (sanity check).

        Canonical ``developer`` agent_id must not regress — it resolves
        to the developer agent and creates the job with developer
        identity (agent_id="developer", agent_dir="/agents/developer").
        """
        expected_job = MagicMock()
        expected_job.job_id = "new-job-from-developer"
        mock_repository.create.return_value = expected_job

        with patch(
            "daemon.services.job_queue_service.get_registry",
            return_value=self._make_mock_registry_coder_resolves_to_coder(),
        ):
            result = await service.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
            )

        assert result.job_id == "new-job-from-developer"
        call_kwargs = mock_repository.create.call_args.kwargs
        assert call_kwargs["agent_id"] == "developer"
        assert call_kwargs["agent_dir"] == "/agents/developer"
