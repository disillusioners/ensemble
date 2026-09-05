"""Shared fixtures for ``send_message`` routing tests.

Three test files duplicate the same 17-patch factory-helper stack and
the same ``create_instance_tools`` invocation pattern (with per-file
deltas):

  * ``tests/unit/tools/test_instance_tools.py`` (~1680 lines, 18 classes)
  * ``tests/tools/test_send_message_status_guard.py`` (~416 lines)
  * ``tests/tools/test_send_message_task_repo_guard.py`` (~291 lines)

The 17-patch list and the tool-builder are byte-identical across all
three; the manager baseline is shared with per-file deltas (extra
``get_injection_count`` mock in the unit-tools file; ``_task_repo``
parameter and minimal ``get_instance_info`` shape in the
task-repo-guard file).

Importable from all three test trees via the ``tests.helpers`` package —
no ``sys.path`` manipulation needed (pytest adds the repo root to
``sys.path`` during test discovery, and ``tests/helpers/__init__.py``
declares the package; this matches the existing pattern used by
``tests.helpers.pause_report_orphan_scenarios`` and
``tests.helpers.fake_instance_repo``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


# Defaults shared by all three suites — the parent instance id and agent
# id passed to ``create_instance_tools`` when building the
# ``send_message`` tool closure.
_PARENT_INSTANCE_ID = "parent-instance"
_AGENT_ID = "developer"


def patch_heavy_helpers():
    """Return a stack of ``unittest.mock.patch`` context managers that
    disable the heavy ``create_instance_tools`` factory helpers (RAG,
    knowledge, MCP, project, job, mother, OpenCode, DB, infra, context)
    so only the instance-management tools (spawn / send / terminate /
    list / get) are built.

    NOTE: ``_check_team_membership`` is patched at the test-method level
    (the ``with patch(...)`` block wraps the coroutine) so the team
    membership gate stays open for the full call duration — anything
    patched here is torn down BEFORE ``send_message.coroutine`` runs.
    """
    return [
        patch("daemon.tools.instance.is_rag_enabled", return_value=False),
        patch("daemon.tools.instance.create_rag_tools", return_value=[]),
        patch("daemon.tools.instance.create_knowledge_tools", return_value=[]),
        patch("daemon.tools.instance.create_inner_soul_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_access_memory_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_project_tools", return_value=[]),
        patch("daemon.tools.instance.create_job_tools_if_available", return_value=[]),
        patch("daemon.tools.instance.create_help_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_critical_notes_tools", return_value=[]),
        patch("daemon.tools.instance.create_project_history_tools", return_value=[]),
        patch("daemon.tools.instance.create_opencode_tools", return_value=[]),
        patch("daemon.tools.instance.create_db_tools", return_value=[]),
        patch("daemon.tools.instance.create_infra_tools", return_value=[]),
        patch("daemon.tools.instance.create_context_tools", return_value=[]),
        patch("daemon.tools.instance._load_mcp_tools", return_value=[]),
        patch("daemon.tools.instance.scan_tools_for_full_docs"),
        patch("daemon.tools.instance._apply_tool_filter", side_effect=lambda tools, *a, **kw: tools),
    ]


def make_send_message_manager(*, status: str) -> MagicMock:
    """Build a mock manager wired for ``send_message`` with a given status.

    Sets the union of attributes used by all three consuming test files.
    Per-file deltas (``get_injection_count`` mock; ``_task_repo``
    parameter; minimal ``get_instance_info`` shape) live in the per-file
    wrapper.

    The returned manager exposes:
      * ``get_instance`` (async) — succeeds so ``_resolve_instance_id`` passes.
      * ``find_near_instance`` — returns no fuzzy matches.
      * ``get_instance_info`` — returns ``{"status": status, "agent_id": "developer"}``.
      * ``get_queue_stats`` (async) — empty counts (idle queue).
      * ``enqueue_message`` (async) — succeeds (the enqueue path).
      * ``enqueue_message_job`` (sync) — kept for straggling reads, NOT
        called by ``send_message`` (the JobItem-mirror path is reserved
        for external/public entry points).
      * ``set_injection`` (sync) — succeeds (the injection path; Phase 1
        RUNNING / WAITING_CHILDREN targets route through here).
      * ``get_agent_tool_revive_count`` / ``note_agent_tool_revive``
        (sync) — quick-win #7 revive-once guard, backed by a REAL
        per-manager dict (first agent-tool revive granted, second
        refused) with MagicMock call tracking.
      * Plus infra attributes the production code touches in the
        post-enqueue path: ``_instance_repository``, ``engine``,
        ``write_guard``, ``_live_hub``.
    """
    manager = MagicMock()

    async def _get_instance(instance_id):
        return MagicMock(instance_id=instance_id)

    manager.get_instance = _get_instance
    manager.find_near_instance = MagicMock(return_value=[])
    manager.get_instance_info = MagicMock(
        return_value={"status": status, "agent_id": _AGENT_ID}
    )
    manager.get_queue_stats = AsyncMock(
        return_value={"pending_count": 0, "processing_count": 0}
    )
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-abc-123")
    )
    manager.enqueue_message_job = MagicMock(
        return_value=MagicMock(message_id="msg-abc-123")
    )
    manager.set_injection = MagicMock(
        return_value={"content": "stub", "timestamp": "2026-08-26T00:00:00Z"}
    )
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    manager.engine = MagicMock()
    manager.write_guard = MagicMock()
    manager._live_hub = MagicMock()

    # Quick-win #7 (revive-once guard, scoped — feature/fix-revive-guard-scope
    # 2026-09-05): REAL in-memory counter wired behind the two manager
    # methods the agent-tool ``send_message`` terminal-revive branch
    # consults. Fresh dict per manager (fresh per test) mirrors the
    # production ``InstanceManager`` lifetime: only revives whose prior
    # status is ERROR / FAILED consume the budget — COMPLETED /
    # TERMINATED revives are granted without incrementing. The
    # ``MagicMock`` wrappers keep call tracking so tests can assert
    # increment/no-increment per path.
    revive_counts: dict[str, int] = {}

    def _note_revive(instance_id: str, prior_status: str | None = None) -> int:
        # SCOPE: only ERROR / FAILED prior statuses consume the budget;
        # COMPLETED / TERMINATED / None (defensive default) do not.
        # Mirrors ``InstanceManager.note_agent_tool_revive`` exactly.
        if prior_status is not None and prior_status not in ("error", "failed"):
            return revive_counts.get(instance_id, 0)
        revive_counts[instance_id] = revive_counts.get(instance_id, 0) + 1
        return revive_counts[instance_id]

    manager.get_agent_tool_revive_count = MagicMock(
        side_effect=lambda iid: revive_counts.get(iid, 0)
    )
    manager.note_agent_tool_revive = MagicMock(side_effect=_note_revive)
    return manager


def get_send_message_tool(manager: MagicMock):
    """Build the instance tools and return the ``send_message`` tool object.

    The tool object exposes a ``.coroutine`` attribute that is the actual
    async function decorated by ``@tool``. Invoking it directly bypasses
    Pydantic schema validation (callers already know their inputs are
    valid).

    The factory-helper patches are torn down BEFORE
    ``send_message.coroutine`` runs, so anything the production code
    reads at call-time must NOT be patched here — patch it at the
    test-method level via ``with patch(...)``.
    """
    from daemon.tools.instance import create_instance_tools

    patches = patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        tools = create_instance_tools(
            manager, _PARENT_INSTANCE_ID, _AGENT_ID
        )
    finally:
        for p in reversed(patches):
            p.stop()

    for t in tools:
        if getattr(t, "name", None) == "send_message":
            return t
    raise RuntimeError(
        "send_message tool not found in create_instance_tools output; "
        f"got {[getattr(t, 'name', None) for t in tools]}"
    )
