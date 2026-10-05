"""Shared fixtures for the unified ``spawn_instance`` tool tests.

Single source for the byte-identical helper trio duplicated across three
packs (unify-spawn-tools tidy pass, P4 #13):

  * ``tests/unit/tools/test_snapshot_tools.py``
  * ``tests/unit/tools/test_snapshot_v3.py``
  * ``tests/test_snapshot_behavior_spot.py``

The trio:

  * :func:`make_unified_spawn_manager` — MagicMock manager wired for
    the UNIFIED ``spawn_instance`` tool (snapshot seams +
    spawn/metadata/enqueue recorders + ``enqueue_raise`` failure
    injection).
  * :func:`build_spawn_tool` — bind the unified ``spawn_instance``
    tool to ``manager`` via the REAL ``create_instance_tools``
    factory (heavy helpers patched out).
  * :func:`steer_snapshot_gate` — steer the TARGET-agent snapshot
    gate at its real seam (``daemon.tools.instance._target_snapshot_enabled``).

Importable from all three test trees via the ``tests.helpers`` package
— no ``sys.path`` manipulation needed (pytest adds the repo root to
``sys.path`` during test discovery, and ``tests/helpers/__init__.py``
declares the package; this is the established pattern used by
``tests.helpers.send_message_fixtures``,
``tests.helpers.pause_report_orphan_scenarios`` and
``tests.helpers.fake_instance_repo``).

Capture / search fakes are NOT moved here — each pack keeps its own
local :class:`FakeCaptureService` / :class:`FakeSearchService` (the
shapes differ slightly per pack; e.g. v3's Wave-3 metrics shape is
not shared with the spot pack). Pass them in via ``capture_service``
/ ``search_service`` — both default to ``None`` and the manager
builder synthesizes a minimal in-place fake when omitted (the same
default both packs rely on for the gate-OFF / no-capture tests).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.engine import Engine

from daemon.repositories.snapshot.repository import SnapshotRepository


# ---------------------------------------------------------------------------
# Minimal default capture / search fakes — used ONLY when the caller does
# not pass its own. The three packs' local ``FakeCaptureService`` /
# ``FakeSearchService`` carry richer per-pack fields; those overrides stay
# in their pack and are passed in explicitly.
# ---------------------------------------------------------------------------


class _DefaultCaptureService:
    """Minimal ``capture_async`` + ``staleness_report`` stub.

    The full per-pack :class:`FakeCaptureService` (with call recording,
    canned staleness, capture_status override) is wider than this. When a
    test cares about capture calls, the pack passes its local fake in
    via :func:`make_unified_spawn_manager`'s ``capture_service`` kwarg.
    """

    async def capture_async(self, **_kwargs: Any) -> dict[str, Any]:
        return {"snapshot_id": "snap-new", "status": "running", "error": None}

    async def staleness_report(self, _snapshot_id: str) -> dict[str, Any] | None:
        return None


class _DefaultSearchService:
    """Minimal ``search`` stub — returns no candidates."""

    async def search(
        self,
        _query: str,
        *,
        project_id: str,
        tags: list[str] | None = None,
        tag_mode: str = "all",
        limit: int = 10,
        **_: Any,
    ) -> dict[str, Any]:
        return {"results": [], "error": None}


# ---------------------------------------------------------------------------
# The trio (extracted from the three packs verbatim).
# ---------------------------------------------------------------------------


def make_unified_spawn_manager(
    rows: dict[str, Any],
    repo: SnapshotRepository,
    *,
    capture_service: Any | None = None,
    search_service: Any | None = None,
    async_message_result: Any | None = None,
) -> Any:
    """MagicMock manager wired for the UNIFIED ``spawn_instance`` tool.

    Baseline: ``tests.helpers.send_message_fixtures.make_spawn_manager``
    (the proven surface against the real ``create_instance_tools``
    factory). Snapshot seams + the instance repo ride over in
    FakeManager shapes; the spawn / metadata / enqueue recorders
    append to ``m.events`` so the R6b + R18 ordering pins
    (spawn → metadata → enqueue) stay assertable.

    The ``enqueue_raise`` failure-injection field is part of the
    single-source contract: tests set it to a ``BaseException`` to
    force the enqueue path to raise (the R18 loud ERROR tail is
    pinned at the call site). The default is ``None`` (no raise).

    Args:
        rows: Instance-repo row map (``caller-*`` / ``inst-*`` / …)
            used by ``FakeInstanceRepo`` for the auth consult.
        repo: Real :class:`SnapshotRepository` (sqlite in-memory in
            tests) for the snapshot search/stamp paths.
        capture_service: Optional richer capture fake (per-pack
            local). Defaults to :class:`_DefaultCaptureService`.
        search_service: Optional richer search fake (per-pack
            local). Defaults to :class:`_DefaultSearchService`.
        async_message_result: Optional returned-value of the enqueue
            call (R18 happy-path enqueue returns this). Defaults to
            a minimal :class:`FakeAsyncMessageResult`.

    Returns:
        The MagicMock manager with the unified-spawn surface wired.
    """
    from tests.helpers.send_message_fixtures import make_spawn_manager

    m = make_spawn_manager()
    m._snapshot_repo = repo
    m._snapshot_service = capture_service or _DefaultCaptureService()
    m._snapshot_search_service = search_service or _DefaultSearchService()
    m._snapshot_metrics_service = None
    m._instance_repository = _FakeInstanceRepoShim(rows)
    m._project_repository = None
    m.events: list[str] = []
    m.spawn_calls: list[dict[str, Any]] = []
    m.metadata_calls: list[tuple[str, dict[str, Any]]] = []
    m.enqueue_calls: list[dict[str, Any]] = []
    m.enqueue_raise: BaseException | None = None
    if async_message_result is None:
        from tests.unit.tools._fakes import FakeAsyncMessageResult

        async_message_result = FakeAsyncMessageResult()
    m.enqueue_result: Any = async_message_result

    def _spawn(**kw: Any) -> tuple[str, str | None]:
        m.events.append("spawn")
        m.spawn_calls.append(dict(kw))
        return ("new-inst-1", None)

    def _meta(instance_id: str, updates: dict[str, Any]) -> None:
        m.events.append("metadata")
        m.metadata_calls.append((instance_id, dict(updates)))

    async def _enqueue(**kw: Any) -> Any:
        m.events.append("enqueue")
        m.enqueue_calls.append(dict(kw))
        if m.enqueue_raise is not None:
            raise m.enqueue_raise
        return m.enqueue_result

    m.spawn_instance = _spawn
    m.set_metadata_many = _meta
    m.enqueue_message = _enqueue
    return m


def build_spawn_tool(
    manager: Any,
    caller_id: str = "caller-1",
    agent_id: str = "coder",
) -> Any:
    """Build the unified ``spawn_instance`` tool bound to ``manager``.

    Drives the REAL ``create_instance_tools`` factory under the
    shared heavy-helper patch stack (RAG / MCP / project / job / …
    factories disabled) and returns just the spawn_instance tool.
    """
    from daemon.tools.instance import create_instance_tools
    from tests.helpers.send_message_fixtures import patch_heavy_helpers

    patches = patch_heavy_helpers()
    for _p in patches:
        _p.start()
    try:
        all_tools = create_instance_tools(
            manager, caller_id, agent_id=agent_id, version_tag=None
        )
    finally:
        for _p in reversed(patches):
            _p.stop()
    for _t in all_tools:
        if getattr(_t, "name", None) == "spawn_instance":
            return _t
    raise RuntimeError("spawn_instance tool not found")


def steer_snapshot_gate(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
) -> None:
    """Steer the TARGET-agent snapshot gate at its real seam.

    The unified spawn body consults
    ``daemon.tools.instance._target_snapshot_enabled``; tests steer
    that module attribute directly. Gate-OFF tests that do NOT call
    this helper go through the REAL registry consult (worker has no
    ``snapshot_enabled`` → fail-closed False).
    """
    monkeypatch.setattr(
        "daemon.tools.instance._target_snapshot_enabled",
        lambda agent_id, version_tag=None: enabled,
    )


# ---------------------------------------------------------------------------
# Internal: minimal ``FakeInstanceRepo`` shim — the three packs each carry
# their own ``FakeInstanceRepo`` with pack-specific rows; this shim only
# exists so the helper has a default to wire when the caller omits one.
# ---------------------------------------------------------------------------


class _FakeInstanceRepoShim:
    """Minimal stand-in matching the ``FakeInstanceRepo`` interface.

    Each pack passes its own ``FakeInstanceRepo`` via
    :func:`make_unified_spawn_manager` (which wires
    ``m._instance_repository`` directly via the ``capture_service``
    / ``search_service`` kwargs equivalent — see the pack wrappers).
    This shim is reserved for FUTURE packs that don't yet have a
    local fake (none today); carries the same ``.get(instance_id)``
    shape so the auth consult works end-to-end.
    """

    def __init__(self, rows: dict[str, Any]) -> None:
        self._rows = rows

    def get(self, instance_id: str) -> Any | None:
        return self._rows.get(instance_id)