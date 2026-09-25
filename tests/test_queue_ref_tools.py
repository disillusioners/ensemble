"""Tests for queue-alias resolution and the agent-aware default queue.

Covers two features:

1. **Agent-aware default queue** — agents ari/jober declare
   ``default_queue: system_parallel_queue`` in their meta; ``job_create``
   without an explicit ``queue_id`` targets that system queue (per-project).
   Every other agent keeps the exact current behavior (service default →
   ``system_fifo_queue`` for task jobs).

2. **Queue alias support** — ``queue_id`` params on the job tools accept
   system-queue aliases (full names + short forms, case-insensitive) in
   addition to queue IDs. Precedence is pinned: exact in-project ID first,
   then alias → canonical system name (a user queue literally named
   "parallel" can never shadow the alias). Unknown non-alias refs keep the
   service's soft-fail semantics (pass-through, regression-pinned by
   ``tests/job_queue/test_task_queue_service.py``); KNOWN alias names that
   fail to resolve are hard errors with a valid-queue listing.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.registry import AgentRegistry
from daemon.services.queue_ref import (
    QUEUE_ALIAS_TO_CANONICAL,
    describe_valid_queues,
    is_known_alias_name,
    resolve_queue_ref,
)
from daemon.tools.job_queue import create_job_tools

PROJECT_ID = "proj-1"

# The five canonical system queues and their short aliases.
ALIAS_CASES = [
    ("system_fifo_queue", "sys-id-fifo"),
    ("fifo", "sys-id-fifo"),
    ("system_parallel_queue", "sys-id-parallel"),
    ("parallel", "sys-id-parallel"),
    ("system_background_queue", "sys-id-background"),
    ("background", "sys-id-background"),
    ("system_defer_queue", "sys-id-defer"),
    ("defer", "sys-id-defer"),
    ("system_kb_fifo_queue", "sys-id-kb-fifo"),
    ("kb_fifo", "sys-id-kb-fifo"),
]

# In-project system queue rows keyed by canonical name.
SYSTEM_QUEUES = {
    "system_fifo_queue": MagicMock(queue_id="sys-id-fifo", project_id=PROJECT_ID, queue_name="system_fifo_queue"),
    "system_parallel_queue": MagicMock(queue_id="sys-id-parallel", project_id=PROJECT_ID, queue_name="system_parallel_queue"),
    "system_background_queue": MagicMock(queue_id="sys-id-background", project_id=PROJECT_ID, queue_name="system_background_queue"),
    "system_defer_queue": MagicMock(queue_id="sys-id-defer", project_id=PROJECT_ID, queue_name="system_defer_queue"),
    "system_kb_fifo_queue": MagicMock(queue_id="sys-id-kb-fifo", project_id=PROJECT_ID, queue_name="system_kb_fifo_queue"),
}


def make_repo(get_rows: dict | None = None):
    """A JobQueueRepository double with no ID hits by default."""
    repo = MagicMock()
    repo.get.side_effect = lambda qid: (get_rows or {}).get(qid)
    repo.get_by_name.side_effect = lambda pid, name: SYSTEM_QUEUES.get(name.lower())
    repo.list_by_project.return_value = []
    return repo


def make_job_service(repo):
    job_service = AsyncMock()
    job_service.use_virtual_job_resolver = False
    job_service._queue_repo = repo
    job_item = MagicMock()
    job_item.job_id = "job-1"
    job_item.instance_id = None
    job_item.to_dict.return_value = {"job_id": "job-1"}
    job_service.enqueue.return_value = job_item
    return job_service


def make_mgmt(dead_letter=None):
    queue_mgmt_service = AsyncMock()
    dead_letter_service = dead_letter if dead_letter is not None else MagicMock()
    return queue_mgmt_service, dead_letter_service


def patch_registry(monkeypatch, meta):
    """Pin ``get_registry`` so the factory's closure-time fetch sees ``meta``."""
    registry = MagicMock()
    registry.get_version.return_value = meta
    registry.get_resolved.return_value = meta
    monkeypatch.setattr("daemon.registry.get_registry", lambda: registry)
    return registry


# ---------------------------------------------------------------------------
# Feature 2 — resolve_queue_ref precedence
# ---------------------------------------------------------------------------


class TestResolveQueueRef:
    def test_all_full_names_and_short_aliases_resolve_project_scoped(self):
        """All 5 full names + all 5 short aliases → correct system queue id."""
        repo = make_repo()
        for ref, expected_id in ALIAS_CASES:
            queue = resolve_queue_ref(repo, PROJECT_ID, ref)
            assert queue is not None, ref
            assert queue.queue_id == expected_id, ref

    @pytest.mark.parametrize("ref,expected_id", ALIAS_CASES)
    def test_case_insensitive(self, ref, expected_id):
        repo = make_repo()
        queue = resolve_queue_ref(repo, PROJECT_ID, ref.upper())
        assert queue is not None
        assert queue.queue_id == expected_id

    def test_alias_map_shape(self):
        """10 keys: 5 canonical + 5 short, all mapping to canonical names."""
        assert len(QUEUE_ALIAS_TO_CANONICAL) == 10
        assert QUEUE_ALIAS_TO_CANONICAL["parallel"] == "system_parallel_queue"
        assert QUEUE_ALIAS_TO_CANONICAL["system_fifo_queue"] == "system_fifo_queue"

    def test_precedence_alias_wins_over_user_queue_named_parallel(self):
        """A user queue literally named 'parallel' must NOT shadow the alias."""
        user_queue = MagicMock(queue_id="uuid-user", project_id=PROJECT_ID, queue_name="parallel")
        repo = make_repo()
        repo.get_by_name.side_effect = lambda pid, name: {
            "parallel": user_queue,  # bare-name lookup would find the user queue
            "system_parallel_queue": SYSTEM_QUEUES["system_parallel_queue"],
        }.get(name.lower())
        queue = resolve_queue_ref(repo, PROJECT_ID, "parallel")
        assert queue is SYSTEM_QUEUES["system_parallel_queue"]

    def test_exact_in_project_id_wins(self):
        """Exact ID lookup (UUID-shaped) matches when in-project."""
        row = MagicMock(queue_id="uuid-user", project_id=PROJECT_ID, queue_name="feature-x")
        repo = make_repo(get_rows={"uuid-user": row})
        assert resolve_queue_ref(repo, PROJECT_ID, "uuid-user") is row

    def test_seeded_sys_style_id_matches(self):
        """Seeded 'sys-fifo-<project_id>' style IDs resolve via the ID path."""
        seeded = MagicMock(queue_id=f"sys-fifo-{PROJECT_ID}", project_id=PROJECT_ID, queue_name="system_fifo_queue")
        repo = make_repo(get_rows={f"sys-fifo-{PROJECT_ID}": seeded})
        assert resolve_queue_ref(repo, PROJECT_ID, f"sys-fifo-{PROJECT_ID}") is seeded

    def test_cross_project_id_is_not_a_match(self):
        """A queue ID belonging to another project is NOT resolved (fall
        through; alias path can't match an ID) — the caller errors."""
        foreign = MagicMock(queue_id="sys-id-parallel", project_id="OTHER", queue_name="system_parallel_queue")
        repo = make_repo(get_rows={"sys-id-parallel": foreign})
        assert resolve_queue_ref(repo, PROJECT_ID, "sys-id-parallel") is None

    def test_unknown_ref_returns_none(self):
        repo = make_repo()
        assert resolve_queue_ref(repo, PROJECT_ID, "no-such-queue") is None

    def test_empty_and_none_ref(self):
        repo = make_repo()
        assert resolve_queue_ref(repo, PROJECT_ID, "") is None
        assert resolve_queue_ref(repo, PROJECT_ID, "   ") is None
        assert resolve_queue_ref(repo, PROJECT_ID, None) is None
        assert resolve_queue_ref(None, PROJECT_ID, "parallel") is None

    def test_is_known_alias_name(self):
        assert is_known_alias_name("PARALLEL")
        assert is_known_alias_name("System_KB_FIFO_Queue")
        assert not is_known_alias_name("uuid-user")
        assert not is_known_alias_name(None)
        assert not is_known_alias_name("")

    def test_describe_valid_queues_lists_aliases_and_project_queues(self):
        user_queue = MagicMock(queue_id="uuid-user", project_id=PROJECT_ID, queue_name="feature-x")
        repo = make_repo()
        repo.list_by_project.return_value = [user_queue]
        text = describe_valid_queues(repo, PROJECT_ID)
        for canonical in QUEUE_ALIAS_TO_CANONICAL.values():
            assert canonical in text
        assert "feature-x" in text and "uuid-user" in text


# ---------------------------------------------------------------------------
# Feature 1 — tool-level agent default
# ---------------------------------------------------------------------------


class TestJobCreateAgentDefaultQueue:
    @pytest.fixture
    def tools_factory(self):
        def _build(job_service, agent_id=""):
            queue_mgmt, dead_letter = make_mgmt()
            return create_job_tools(
                job_service, queue_mgmt, dead_letter,
                current_instance_id="inst-1",
                agent_id=agent_id,
            )[0]
        return _build

    async def test_ari_defaults_to_parallel_queue(self, tools_factory, monkeypatch):
        """Caller ari + queue_id=None → enqueue receives the parallel system queue id."""
        patch_registry(monkeypatch, SimpleNamespace(default_queue="system_parallel_queue"))
        repo = make_repo()
        job_service = make_job_service(repo)
        job_create = tools_factory(job_service, agent_id="ari")

        result = await job_create.ainvoke({
            "agent_id": "developer",
            "message": "do the thing",
            "project_id": PROJECT_ID,
        })

        assert result == {"job_id": "job-1"}
        _, kwargs = job_service.enqueue.call_args
        assert kwargs["queue_id"] == "sys-id-parallel"

    async def test_jober_defaults_to_parallel_queue(self, tools_factory, monkeypatch):
        patch_registry(monkeypatch, SimpleNamespace(default_queue="system_parallel_queue"))
        repo = make_repo()
        job_service = make_job_service(repo)
        job_create = tools_factory(job_service, agent_id="jober")

        await job_create.ainvoke({
            "agent_id": "developer",
            "message": "do the thing",
            "project_id": PROJECT_ID,
        })

        _, kwargs = job_service.enqueue.call_args
        assert kwargs["queue_id"] == "sys-id-parallel"

    async def test_other_agent_no_default_passes_none(self, tools_factory, monkeypatch):
        """An agent without the meta field + queue_id=None → enqueue receives
        queue_id=None (unchanged service-default behavior — pins the contract
        for all 10 other enqueue callsites)."""
        patch_registry(monkeypatch, SimpleNamespace(default_queue=None))
        repo = make_repo()
        job_service = make_job_service(repo)
        job_create = tools_factory(job_service, agent_id="developer")

        await job_create.ainvoke({
            "agent_id": "developer",
            "message": "do the thing",
            "project_id": PROJECT_ID,
        })

        _, kwargs = job_service.enqueue.call_args
        assert kwargs["queue_id"] is None
        repo.get_by_name.assert_not_called()

    async def test_explicit_queue_id_no_default_interference(self, tools_factory, monkeypatch):
        """Explicit non-alias queue_id passes through verbatim."""
        patch_registry(monkeypatch, SimpleNamespace(default_queue="system_parallel_queue"))
        repo = make_repo()
        job_service = make_job_service(repo)
        job_create = tools_factory(job_service, agent_id="ari")

        await job_create.ainvoke({
            "agent_id": "developer",
            "message": "do the thing",
            "project_id": PROJECT_ID,
            "queue_id": "uuid-explicit",
        })

        _, kwargs = job_service.enqueue.call_args
        assert kwargs["queue_id"] == "uuid-explicit"

    async def test_default_queue_missing_is_loud_error(self, tools_factory, monkeypatch):
        """Missing agent-default system queue → clear error (provisioning bug),
        job NOT silently created without its queue."""
        patch_registry(monkeypatch, SimpleNamespace(default_queue="system_parallel_queue"))
        repo = make_repo()
        repo.get_by_name.side_effect = lambda pid, name: None
        job_service = make_job_service(repo)
        job_create = tools_factory(job_service, agent_id="ari")

        result = await job_create.ainvoke({
            "agent_id": "developer",
            "message": "do the thing",
            "project_id": PROJECT_ID,
        })

        assert "error" in result
        assert "system_parallel_queue" in result["error"]
        assert "system_fifo_queue" in result["error"]  # listing present
        job_service.enqueue.assert_not_called()

    async def test_registry_fetch_failure_degrades_to_none(self, tools_factory, monkeypatch):
        """Registry blow-up at factory time → warning + degrade to None, tool
        build NEVER breaks."""

        def boom():
            raise RuntimeError("registry unavailable")

        monkeypatch.setattr("daemon.registry.get_registry", boom)
        repo = make_repo()
        job_service = make_job_service(repo)
        job_create = tools_factory(job_service, agent_id="ari")  # must not raise

        await job_create.ainvoke({
            "agent_id": "developer",
            "message": "do the thing",
            "project_id": PROJECT_ID,
        })

        _, kwargs = job_service.enqueue.call_args
        assert kwargs["queue_id"] is None

    async def test_untagged_caller_falls_back_to_base_meta(self, tools_factory, monkeypatch):
        """get_version(tag) → None falls back to get_resolved (ari/jober are
        untagged dirs → base meta)."""
        registry = patch_registry(monkeypatch, SimpleNamespace(default_queue="system_parallel_queue"))
        registry.get_version.return_value = None  # tag miss
        repo = make_repo()
        job_service = make_job_service(repo)
        job_create = tools_factory(job_service, agent_id="ari")

        await job_create.ainvoke({
            "agent_id": "developer",
            "message": "do the thing",
            "project_id": PROJECT_ID,
        })

        registry.get_resolved.assert_called_with("ari")
        _, kwargs = job_service.enqueue.call_args
        assert kwargs["queue_id"] == "sys-id-parallel"

    async def test_repo_unavailable_degrades_to_none(self, tools_factory, monkeypatch):
        """No queue repo plumbing → agent default degrades to None (service
        default) instead of breaking the call."""
        patch_registry(monkeypatch, SimpleNamespace(default_queue="system_parallel_queue"))
        job_service = make_job_service(repo=None)
        job_service._queue_repo = None
        job_create = tools_factory(job_service, agent_id="ari")

        await job_create.ainvoke({
            "agent_id": "developer",
            "message": "do the thing",
            "project_id": PROJECT_ID,
        })

        _, kwargs = job_service.enqueue.call_args
        assert kwargs["queue_id"] is None


# ---------------------------------------------------------------------------
# Feature 2 — job_create alias acceptance
# ---------------------------------------------------------------------------


class TestJobCreateAlias:
    @pytest.fixture
    def job_create(self, monkeypatch):
        monkeypatch.setattr("daemon.registry.get_registry", MagicMock(
            get_version=MagicMock(return_value=None),
            get_resolved=MagicMock(return_value=SimpleNamespace(default_queue=None)),
        ))
        repo = make_repo()
        job_service = make_job_service(repo)
        queue_mgmt, dead_letter = make_mgmt()
        tool = create_job_tools(
            job_service, queue_mgmt, dead_letter,
            current_instance_id="inst-1", agent_id="developer",
        )[0]
        return tool, job_service

    @pytest.mark.parametrize("ref,expected_id", [
        ("parallel", "sys-id-parallel"),
        ("PARALLEL", "sys-id-parallel"),
        ("system_parallel_queue", "sys-id-parallel"),
        ("fifo", "sys-id-fifo"),
        ("kb_fifo", "sys-id-kb-fifo"),
    ])
    async def test_alias_resolves_to_system_queue_id(self, job_create, ref, expected_id):
        tool, job_service = job_create
        await tool.ainvoke({
            "agent_id": "developer", "message": "m",
            "project_id": PROJECT_ID, "queue_id": ref,
        })
        _, kwargs = job_service.enqueue.call_args
        assert kwargs["queue_id"] == expected_id

    async def test_known_alias_missing_is_strict_error(self, job_create):
        """A KNOWN system name that fails to resolve → hard error listing
        valid options (strict for names)."""
        tool, job_service = job_create
        repo = job_service._queue_repo
        repo.get_by_name.side_effect = lambda pid, name: None

        result = await tool.ainvoke({
            "agent_id": "developer", "message": "m",
            "project_id": PROJECT_ID, "queue_id": "parallel",
        })

        assert "error" in result
        assert "parallel" in result["error"]
        assert "system_kb_fifo_queue" in result["error"]  # listing present
        job_service.enqueue.assert_not_called()

    async def test_unknown_nonalias_passthrough_softfail(self, job_create):
        """Unknown non-alias ref → passed through verbatim; the service's
        soft-fail semantics apply (regression-pinned behavior)."""
        tool, job_service = job_create
        await tool.ainvoke({
            "agent_id": "developer", "message": "m",
            "project_id": PROJECT_ID, "queue_id": "nonexistent-queue-id",
        })
        _, kwargs = job_service.enqueue.call_args
        assert kwargs["queue_id"] == "nonexistent-queue-id"

    async def test_cross_project_id_errors_not_silently_used(self, job_create):
        """A foreign queue ID resolves to None at the tool seam and passes to
        the service, whose ownership check rejects loudly (C4)."""
        tool, job_service = job_create
        foreign = MagicMock(queue_id="uuid-foreign", project_id="OTHER", queue_name="theirs")
        job_service._queue_repo.get.side_effect = lambda qid: foreign if qid == "uuid-foreign" else None
        job_service.enqueue.side_effect = ValueError(
            "Queue uuid-foreign does not belong to project proj-1"
        )

        result = await tool.ainvoke({
            "agent_id": "developer", "message": "m",
            "project_id": PROJECT_ID, "queue_id": "uuid-foreign",
        })

        assert result == {"error": "Queue uuid-foreign does not belong to project proj-1"}


# ---------------------------------------------------------------------------
# Feature 2 — filter + management tools
# ---------------------------------------------------------------------------


class TestFilterToolAliases:
    def _build(self, monkeypatch, repo):
        monkeypatch.setattr("daemon.registry.get_registry", MagicMock(
            get_version=MagicMock(return_value=None),
            get_resolved=MagicMock(return_value=SimpleNamespace(default_queue=None)),
        ))
        job_service = make_job_service(repo)
        job_service._work_resolver = None  # legacy list_jobs branch
        queue_mgmt, dead_letter = make_mgmt()
        queue_mgmt._queue_repo = repo  # queue_update reads the mgmt service's repo
        tools = create_job_tools(
            job_service, queue_mgmt, dead_letter,
            current_instance_id="inst-1", agent_id="developer",
        )
        return tools, job_service, queue_mgmt, dead_letter

    async def test_job_list_alias_resolved(self, monkeypatch):
        repo = make_repo()
        tools, job_service, _, _ = self._build(monkeypatch, repo)
        job_list = tools[2]
        job_service.list_jobs.return_value = []

        await job_list.ainvoke({"project_id": PROJECT_ID, "queue_id": "parallel"})

        _, kwargs = job_service.list_jobs.call_args
        assert kwargs["queue_id"] == "sys-id-parallel"

    async def test_job_list_unknown_alias_error(self, monkeypatch):
        repo = make_repo()
        repo.get_by_name.side_effect = lambda pid, name: None
        tools, job_service, _, _ = self._build(monkeypatch, repo)
        job_list = tools[2]

        result = await job_list.ainvoke({"project_id": PROJECT_ID, "queue_id": "parallel"})

        assert "error" in result
        assert "system_fifo_queue" in result["error"]
        job_service.list_jobs.assert_not_called()

    async def test_job_list_nonalias_passthrough(self, monkeypatch):
        repo = make_repo()
        tools, job_service, _, _ = self._build(monkeypatch, repo)
        job_list = tools[2]
        job_service.list_jobs.return_value = []

        await job_list.ainvoke({"project_id": PROJECT_ID, "queue_id": "uuid-x"})

        _, kwargs = job_service.list_jobs.call_args
        assert kwargs["queue_id"] == "uuid-x"

    async def test_dlq_list_alias_resolved(self, monkeypatch):
        repo = make_repo()
        tools, _, _, dead_letter = self._build(monkeypatch, repo)
        dlq_list = tools[10]
        dead_letter.list_dlq.return_value = ([], 0)

        await dlq_list.ainvoke({"project_id": PROJECT_ID, "queue_id": "kb_fifo"})

        _, kwargs = dead_letter.list_dlq.call_args
        assert kwargs["queue_id"] == "sys-id-kb-fifo"

    async def test_dlq_list_unknown_alias_error(self, monkeypatch):
        repo = make_repo()
        repo.get_by_name.side_effect = lambda pid, name: None
        tools, _, _, dead_letter = self._build(monkeypatch, repo)
        dlq_list = tools[10]

        result = await dlq_list.ainvoke({"project_id": PROJECT_ID, "queue_id": "defer"})

        assert "error" in result
        assert "system_defer_queue" in result["error"]
        dead_letter.list_dlq.assert_not_called()

    async def test_queue_update_alias_resolved(self, monkeypatch):
        repo = make_repo()
        tools, _, queue_mgmt, _ = self._build(monkeypatch, repo)
        queue_update = tools[9]
        queue_mgmt.get_queue.return_value = MagicMock()
        queue_mgmt.update_queue.return_value = MagicMock()

        result = await queue_update.ainvoke({
            "project_id": PROJECT_ID, "queue_id": "parallel",
            "is_paused": True,
        })

        assert "updated successfully" in result
        _, kwargs = queue_mgmt.update_queue.call_args
        assert kwargs["queue_id"] == "sys-id-parallel"

    async def test_queue_update_unknown_alias_error_string_shape(self, monkeypatch):
        """queue_update keeps its ``ERROR: ...`` string error shape."""
        repo = make_repo()
        repo.get_by_name.side_effect = lambda pid, name: None
        tools, _, _, _ = self._build(monkeypatch, repo)
        queue_update = tools[9]

        result = await queue_update.ainvoke({
            "project_id": PROJECT_ID, "queue_id": "parallel",
            "is_paused": True,
        })

        assert result.startswith("ERROR: Unknown queue reference 'parallel'")
        assert "system_fifo_queue" in result

    async def test_queue_update_nonalias_passthrough(self, monkeypatch):
        repo = make_repo()
        tools, _, queue_mgmt, _ = self._build(monkeypatch, repo)
        queue_update = tools[9]
        queue_mgmt.get_queue.return_value = None

        result = await queue_update.ainvoke({
            "project_id": PROJECT_ID, "queue_id": "uuid-gone",
            "is_paused": True,
        })

        assert result == "ERROR: Queue uuid-gone not found in project proj-1."


# ---------------------------------------------------------------------------
# Feature 1 — registry discovery
# ---------------------------------------------------------------------------


class TestRegistryDefaultQueue:
    def _make_agent(self, agents_dir: Path, agent_id: str, **overrides):
        agent_dir = agents_dir / agent_id
        agent_dir.mkdir()
        meta = {"id": agent_id, "name": agent_id.title(), **overrides}
        (agent_dir / "meta.json").write_text(json.dumps(meta))

    def test_discover_parses_default_queue(self, tmp_path):
        self._make_agent(tmp_path, "ari", default_queue="system_parallel_queue")
        registry = AgentRegistry(tmp_path)
        registry.discover()
        assert registry.get("ari").default_queue == "system_parallel_queue"

    def test_absent_default_queue_is_none(self, tmp_path):
        self._make_agent(tmp_path, "developer")
        registry = AgentRegistry(tmp_path)
        registry.discover()
        assert registry.get("developer").default_queue is None

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_empty_string_normalized_to_none(self, tmp_path, raw):
        self._make_agent(tmp_path, "x", default_queue=raw)
        registry = AgentRegistry(tmp_path)
        registry.discover()
        assert registry.get("x").default_queue is None

    def test_whitespace_stripped(self, tmp_path):
        self._make_agent(tmp_path, "x", default_queue="  system_parallel_queue  ")
        registry = AgentRegistry(tmp_path)
        registry.discover()
        assert registry.get("x").default_queue == "system_parallel_queue"

    def test_non_string_value_degrades_to_none(self, tmp_path):
        self._make_agent(tmp_path, "x", default_queue=12345)
        registry = AgentRegistry(tmp_path)
        registry.discover()
        assert registry.get("x").default_queue is None

    def test_shipped_ari_and_jober_meta_declare_parallel_default(self):
        """The shipped configs pin the feature: ari + jober → system_parallel_queue."""
        repo_root = Path(__file__).resolve().parent.parent
        for agent_id in ("ari", "jober"):
            meta = json.loads((repo_root / "agents" / agent_id / "meta.json").read_text())
            assert meta.get("default_queue") == "system_parallel_queue", agent_id
