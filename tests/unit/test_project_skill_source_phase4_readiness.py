"""Phase-4 readiness pack — ``detail.skill_sources`` advisory block.

Commission: project-skill-loader, Phase 4 (tasks 4/6/7).

Pins in this pack
-----------------

``TestAdvisoryShape``
    A11 — the block publishes ALWAYS (``enabled: true|false``); empty
    roots = honest zero, never an absent key; full key set; the
    ``enabled:false`` shape.
``TestStatusInvariance`` (NFR-SAFE-5 / readiness-no-flip)
    A broken canon root / embeddings pending / source disabled NEVER
    flip ``status`` / ``components`` / ``detail.reasons``; no new
    readiness component exists; no 500 (to_payload always succeeds).
``TestSoftFail`` (NFR-SAFE-4)
    Loader disabled + DB stack absent ⇒ no crash, no 500 — the
    snapshot builder returns the honest disabled block.
``TestApiHelper``
    ``_read_skill_sources_snapshot`` — knob read, roots census from
    ``snapshot_status()``, shadowed ids + S5 overflow names from the
    search service's fusion stats, ``db_stack_present`` from
    ``has_db_corpus``; robust against a manager missing every
    attribute (returns the disabled shape, never raises).
``TestDrillSurvival``
    The block survives the forced-degradation rebuild (the A1 field
    list) like the MCP map.
"""

from __future__ import annotations

import pytest

from daemon.services.readiness import (
    ReadinessComposite,
    apply_forced_degradation,
    refresh_readiness_composite,
)


HEALTHY_BLOCK = {
    "enabled": True,
    "db_stack_present": True,
    "roots": [
        {
            "root_path": "/repo",
            "project_id": "11111111-2222-3333-4444-555555555555",
            "namespaces": 2,
            "skills_indexed": 5,
            "invalid_skills": 0,
            "last_scan_ms": 3.2,
            "last_scan_at": "2026-10-31T00:00:00+00:00",
            "last_error": None,
        }
    ],
    "shadowed_db_skills": ["9e79ccad-1111", "fd1e01dc-2222"],
    "overflow_dropped": [],
    "embeddings_pending": 1,
}

BROKEN_ROOT_BLOCK = {
    "enabled": True,
    "db_stack_present": True,
    "roots": [
        {
            "root_path": "/repo/.agents",
            "project_id": "11111111-2222-3333-4444-555555555555",
            "namespaces": 0,
            "skills_indexed": 0,
            "invalid_skills": 0,
            "last_scan_ms": 0.0,
            "last_scan_at": "",
            "last_error": "PermissionError: [Errno 13] permission denied",
        }
    ],
    "shadowed_db_skills": [],
    "overflow_dropped": [],
    "embeddings_pending": 0,
}

DISABLED_BLOCK = {
    "enabled": False,
    "db_stack_present": True,
    "roots": [],
    "shadowed_db_skills": [],
    "overflow_dropped": [],
    "embeddings_pending": 0,
}


async def _composite(skill_sources=None):
    return await refresh_readiness_composite(
        db_probe=lambda: True,
        queue_probe=lambda: None,
        services_ok=True,
        queue_freshness_threshold_seconds=300.0,
        skill_sources=skill_sources,
    )


class TestAdvisoryShape:
    @pytest.mark.asyncio
    async def test_block_publishes_with_keys(self):
        payload = (await _composite(HEALTHY_BLOCK)).to_payload()
        block = payload["detail"]["skill_sources"]
        assert block["enabled"] is True
        assert block["db_stack_present"] is True
        assert block["roots"][0]["skills_indexed"] == 5
        assert block["shadowed_db_skills"] == HEALTHY_BLOCK[
            "shadowed_db_skills"
        ]
        assert block["embeddings_pending"] == 1

    @pytest.mark.asyncio
    async def test_disabled_shape_publishes_always(self):
        """A11 — observability of a DISABLED feature is the point."""
        payload = (await _composite(DISABLED_BLOCK)).to_payload()
        block = payload["detail"]["skill_sources"]
        assert block["enabled"] is False
        # Empty roots = honest zero, NEVER an absent key.
        assert block["roots"] == []
        assert "roots" in block
        assert block["embeddings_pending"] == 0

    @pytest.mark.asyncio
    async def test_none_param_key_absent(self):
        """Older-caller tolerance: ``None`` ⇒ key absent (consumers
        fail-closed), NOT an empty block masquerading as disabled."""
        payload = (await _composite(None)).to_payload()
        assert "skill_sources" not in payload["detail"]


class TestStatusInvariance:
    """NFR-SAFE-5 — the advisory NEVER flips readiness."""

    @pytest.mark.asyncio
    async def test_broken_root_does_not_flip(self):
        healthy = (await _composite(HEALTHY_BLOCK)).to_payload()
        broken = (await _composite(BROKEN_ROOT_BLOCK)).to_payload()
        assert healthy["status"] == broken["status"]
        assert healthy["components"] == broken["components"]
        assert healthy["detail"]["reasons"] == broken["detail"]["reasons"]
        # The break is VISIBLE in the advisory, not the verdict:
        assert broken["detail"]["skill_sources"]["roots"][0]["last_error"]

    @pytest.mark.asyncio
    async def test_disabled_does_not_flip(self):
        base = (await _composite(None)).to_payload()
        off = (await _composite(DISABLED_BLOCK)).to_payload()
        assert base["status"] == off["status"]
        assert base["components"] == off["components"]

    @pytest.mark.asyncio
    async def test_no_new_readiness_component(self):
        """No ``skill_sources`` component exists — the block is
        detail-only by construction; pin the component key set."""
        payload = (await _composite(HEALTHY_BLOCK)).to_payload()
        assert "skill_sources" not in payload["components"]
        assert "skill_sources" not in {
            r.split(":")[0] for r in payload["detail"]["reasons"]
        }

    @pytest.mark.asyncio
    async def test_no_500_broken_root_payload_builds(self):
        composite = await _composite(BROKEN_ROOT_BLOCK)
        payload = composite.to_payload()
        assert payload  # builds without raising


class TestSoftFail:
    """NFR-SAFE-4 — loader disabled + DB stack absent ⇒ no crash."""

    @pytest.mark.asyncio
    async def test_off_absent_composite_builds(self):
        block = dict(DISABLED_BLOCK, db_stack_present=False)
        payload = (await _composite(block)).to_payload()
        assert payload["status"] is not None
        assert payload["detail"]["skill_sources"]["db_stack_present"] is False


class TestApiHelper:
    def _manager(self, **kw):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        defaults = dict(
            config=SimpleNamespace(
                project_skill_source=SimpleNamespace(
                    enabled=True, extra_roots=[]
                )
            ),
            _project_skill_source_service=MagicMock(
                **{
                    "snapshot_status.return_value": {
                        "roots": [{"root_path": "/r", "skills_indexed": 2}],
                        "embeddings_pending": 3,
                    }
                }
            ),
            _skill_search_service=SimpleNamespace(
                has_db_corpus=True,
                last_file_corpus_stats={
                    "shadowed_db_ids": ["9e79ccad"],
                    "budget_dropped_names": ["overflowed-skill"],
                },
            ),
        )
        defaults.update(kw)
        return SimpleNamespace(**defaults)

    def test_snapshot_shape(self):
        from daemon.api import _read_skill_sources_snapshot

        block = _read_skill_sources_snapshot(self._manager())
        assert block["enabled"] is True
        assert block["db_stack_present"] is True
        assert block["roots"][0]["skills_indexed"] == 2
        assert block["shadowed_db_skills"] == ["9e79ccad"]
        # S5: overflow names carried separately from shadowing.
        assert block["overflow_dropped"] == ["overflowed-skill"]
        assert block["embeddings_pending"] == 3

    def test_snapshot_disabled_manager(self):
        from daemon.api import _read_skill_sources_snapshot

        mgr = self._manager()
        mgr.config.project_skill_source.enabled = False
        mgr._project_skill_source_service = None
        mgr._skill_search_service = None
        block = _read_skill_sources_snapshot(mgr)
        assert block == {
            "enabled": False,
            "db_stack_present": False,
            "roots": [],
            "shadowed_db_skills": [],
            "overflow_dropped": [],
            "embeddings_pending": 0,
        }

    def test_snapshot_bare_manager_never_raises(self):
        """Soft-fail: a manager missing EVERY attribute still gets the
        honest disabled block (no crash / no 500 path)."""
        from types import SimpleNamespace

        from daemon.api import _read_skill_sources_snapshot

        block = _read_skill_sources_snapshot(SimpleNamespace())
        assert block["enabled"] is False
        assert block["roots"] == []


class TestDrillSurvival:
    @pytest.mark.asyncio
    async def test_block_survives_forced_degradation(self):
        """The forced-degradation rebuild (the operator drill) must
        carry the skill-source census forward like the MCP map."""
        composite = await _composite(HEALTHY_BLOCK)
        rebuilt = apply_forced_degradation(
            composite, env_value="degraded"
        )
        payload = rebuilt.to_payload()
        assert payload["detail"]["skill_sources"]["enabled"] is True
        assert payload["status"] != composite.to_payload()["status"]


class TestApiSeamWiring:
    """A10 site 6 — the api.py refresh call threads the snapshot.

    Source-level pin (the house source-substring precedent): the
    orchestrator-level behavior is covered by TestAdvisoryShape; this
    guards the SEAM — the live /readyz tick must actually PASS the
    block (a dropped kwarg would make every behavioral test pass while
    the live payload loses the key — the F2 "true in unit, dead in
    production" family).
    """

    def test_refresh_call_threads_skill_sources(self):
        import inspect

        import daemon.api as api_module

        src = inspect.getsource(api_module)
        assert "skill_sources=_read_skill_sources_snapshot(manager)," in src


class TestDegradedEnabledShape:
    """A2 (tidier fold) — READINESS HONESTY on the builder-failure path.

    Pre-fold the exception path hardcoded ``enabled: False``: a builder
    crash told an operator the feature was OFF while the knob was ON.
    A ``dict``-key check cannot see that lie — these pins assert the
    TRUE knob value survives, that the failure is surfaced as a stable
    token, and that no raw exception text reaches the payload.
    """

    def _mgr(self, *, enabled: bool, failure: Exception):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        return SimpleNamespace(
            config=SimpleNamespace(
                project_skill_source=SimpleNamespace(enabled=enabled)
            ),
            _project_skill_source_service=MagicMock(
                **{"snapshot_status.side_effect": failure}
            ),
            _skill_search_service=None,
        )

    def test_builder_failure_reports_true_enabled_and_stable_token(self):
        import json

        from daemon.api import _read_skill_sources_snapshot

        mgr = self._mgr(
            enabled=True,
            failure=RuntimeError("provider exploded at /home/nea/secret.db"),
        )
        block = _read_skill_sources_snapshot(mgr)
        assert block["enabled"] is True, (
            "A2: a builder failure must not report the knob as disabled"
        )
        assert block["degraded"] is True
        assert block["degraded_reason"] == "RuntimeError"
        dumped = json.dumps(block)
        assert "exploded" not in dumped and "/home/nea/secret.db" not in dumped, (
            "A2: degraded_reason must be the exception CLASS name — never str(exc)"
        )

    def test_disabled_knob_failure_stays_disabled(self):
        from daemon.api import _read_skill_sources_snapshot

        mgr = self._mgr(enabled=False, failure=ValueError("nope"))
        block = _read_skill_sources_snapshot(mgr)
        assert block["enabled"] is False
        assert block["degraded"] is True
        assert block["degraded_reason"] == "ValueError"


class TestShutdownDrainWiring:
    """A5 (tidier fold) — the drain must be wired, not merely available.

    Low-4 (pre-gate follow-up): behavioral sentinel REPLACING the
    source-inspection pin (``"aclose()" in inspect.getsource(...)``),
    which caught REMOVAL of the drain call but not SHORT-CIRCUITING
    it — ``if False: await svc.aclose()`` keeps the literal in source
    while never awaiting it, and the old pin stayed GREEN. The
    sentinel drives the REAL ``InstanceManager.shutdown`` and asserts
    the service's ``aclose`` was actually AWAITED.
    """

    @pytest.mark.asyncio
    async def test_manager_shutdown_awaits_the_skill_source_aclose(
        self, monkeypatch
    ):
        import asyncio
        import importlib
        from unittest.mock import AsyncMock, MagicMock

        from daemon.manager import InstanceManager

        # Hermetic: the two module-level cleanup singletons shutdown
        # calls right after the drain are swapped for stubs. NOTE:
        # ``import daemon.tools.bash as m`` binds the TOOL object the
        # package re-exports (name shadow), so resolve the real module
        # via importlib — the same module shutdown's function-local
        # ``from daemon.tools.bash import …`` reads.
        proc_mod = importlib.import_module("daemon.tools.proc_tools")
        bash_mod = importlib.import_module("daemon.tools.bash")
        proc_stub = MagicMock()
        proc_stub.cleanup_all = AsyncMock(return_value=0)
        bash_stub = MagicMock()
        bash_stub.cleanup_all = AsyncMock(return_value=0)
        monkeypatch.setattr(
            proc_mod, "get_background_process_manager", lambda: proc_stub
        )
        monkeypatch.setattr(
            bash_mod, "get_bash_process_registry", lambda: bash_stub
        )

        # Bare instance (the house ``__new__`` pattern) + the minimal
        # attribute set the shutdown path touches BEFORE and AT the
        # drain; every later step is individually try/except-wrapped
        # in shutdown itself, so stubs only need to exist, not work.
        mgr = InstanceManager.__new__(InstanceManager)
        mgr._cancellation_service = MagicMock(is_shutting_down=False)
        mgr._warmup_task = None
        mgr._background_tasks = []
        mgr._event_bus = MagicMock()
        mgr._maintenance_service = None
        mgr._mcp_service = MagicMock()
        sentinel = AsyncMock()
        mgr._project_skill_source_service = sentinel

        await asyncio.wait_for(mgr.shutdown(grace_period=0.1), timeout=30)

        assert sentinel.aclose.await_count == 1, (
            "manager.shutdown must AWAIT the project-skill source's "
            "aclose() (A5 wiring) — a short-circuited drain (call "
            "present but never awaited) fails here, which the old "
            "source-inspection pin could not catch"
        )

    @pytest.mark.asyncio
    async def test_shutdown_is_idempotent_and_drain_absent_is_noop(
        self, monkeypatch
    ):
        """Belt: with NO service wired the same shutdown path completes
        without raising (the getattr-None guard), and a second
        shutdown() call is skipped by the _shutting_down flag."""
        import asyncio
        import importlib
        from unittest.mock import AsyncMock, MagicMock

        from daemon.manager import InstanceManager

        proc_mod = importlib.import_module("daemon.tools.proc_tools")
        bash_mod = importlib.import_module("daemon.tools.bash")
        proc_stub = MagicMock()
        proc_stub.cleanup_all = AsyncMock(return_value=0)
        bash_stub = MagicMock()
        bash_stub.cleanup_all = AsyncMock(return_value=0)
        monkeypatch.setattr(
            proc_mod, "get_background_process_manager", lambda: proc_stub
        )
        monkeypatch.setattr(
            bash_mod, "get_bash_process_registry", lambda: bash_stub
        )

        mgr = InstanceManager.__new__(InstanceManager)
        mgr._cancellation_service = MagicMock(is_shutting_down=False)
        mgr._warmup_task = None
        mgr._background_tasks = []
        mgr._event_bus = MagicMock()
        mgr._maintenance_service = None
        mgr._mcp_service = MagicMock()
        # NOTE: no _project_skill_source_service attribute at all.
        mgr2_cancel = MagicMock(is_shutting_down=True)

        await asyncio.wait_for(mgr.shutdown(grace_period=0.1), timeout=30)

        # Second call: is_shutting_down now True -> early return.
        mgr._cancellation_service = mgr2_cancel
        await asyncio.wait_for(mgr.shutdown(grace_period=0.1), timeout=30)
        assert not mgr2_cancel.method_calls  # returned before any step
