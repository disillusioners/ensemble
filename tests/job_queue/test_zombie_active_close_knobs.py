"""Pack F — kill-switch + knob wiring (wi-78967dba).

Pins (phase3-plan T3.6): OFF ⇒ COMPLETE no-op (NO candidate query
issued — asserted via a query-counter spy on the repository method);
config Fields exist with correct defaults + FAIL-FAST on
out-of-range (pydantic ge=1); api.py wiring reads the three knobs
and forwards them (static source pins in the
test_f1_killswitch_tz_matrix / strand-heal wiring style); the boot
INFO log line exists.

Mutation legs (matrix T3.6): (a) plant a candidate query under OFF
(the kill-switch gate dropped) ⇒ the no-query pin goes RED; (b)
remove the ``ge=1`` constraint ⇒ the FAIL-FAST pin goes RED.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.config as _cfg

from daemon.services.dependency_bus import set_dependency_bus

from tests.job_queue.zombie_close_helpers import (
    insert_zombie_fixture,
    make_bus_mock,
    make_recovery_service,
    read_job_state,
)

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 9

_SOURCE_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def kb_bus():
    from tests.job_queue.zombie_close_helpers import make_bus_mock
    from daemon.services.dependency_bus import set_dependency_bus

    bus = make_bus_mock(pending=[])
    set_dependency_bus(bus)
    try:
        yield bus
    finally:
        set_dependency_bus(None)


@pytest.fixture
def kb_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


class TestKillSwitchCompleteNoop:
    async def test_off_issues_no_candidate_query(self, kb_engine, kb_bus):
        """OFF ⇒ the repository candidate method is NEVER called
        (spy counter), no probe, no write — pre-fix behavior
        byte-restored."""
        ref = insert_zombie_fixture(
            kb_engine, verdict="completed", age_hours=3.0
        )
        service = make_recovery_service(kb_engine)
        calls = {"n": 0}
        orig = type(service._job_repository).find_zombie_active_candidates

        def _spy(self_repo, **kwargs):
            calls["n"] += 1
            return orig(self_repo, **kwargs)

        with patch.object(
            type(service._job_repository),
            "find_zombie_active_candidates",
            _spy,
        ):
            stats = await (
                service.reconcile_drift_states(
                    min_pending_age_seconds=0,
                    min_orphan_age_seconds=60,
                    zombie_close_enabled=False,
                )
            )
        assert calls["n"] == 0, (
            "kill-switch OFF must not issue the candidate query: "
            f"{calls['n']} calls"
        )
        st = read_job_state(kb_engine, ref["job_id"])
        assert st["admission_state"] == "active", st
        zc = stats.get("zombie_close") or {}
        assert zc.get("examined", 0) == 0, zc

    async def test_on_by_default_issues_query(self, kb_engine, kb_bus):
        """Default kwargs (the production posture) ⇒ the candidate
        query IS issued (the spy sees it) and the fixture closes."""
        ref = insert_zombie_fixture(
            kb_engine, verdict="completed", age_hours=3.0
        )
        service = make_recovery_service(kb_engine)
        calls = {"n": 0}
        orig = type(service._job_repository).find_zombie_active_candidates

        def _spy(self_repo, **kwargs):
            calls["n"] += 1
            return orig(self_repo, **kwargs)

        with patch.object(
            type(service._job_repository),
            "find_zombie_active_candidates",
            _spy,
        ):
            await (
                service.reconcile_drift_states(
                    min_pending_age_seconds=0,
                    min_orphan_age_seconds=60,
                )
            )
        assert calls["n"] == 1, calls
        st = read_job_state(kb_engine, ref["job_id"])
        assert st["admission_state"] == "done", st


class TestConfigFields:
    async def test_knob_defaults_true_7200_25(self):
        svc = _cfg.ServicesConfig()
        assert (
            svc.drift_reconcile_zombie_close_enabled is True
        )
        assert (
            svc.drift_reconcile_zombie_close_age_bound_seconds == 7200
        )
        assert svc.drift_reconcile_zombie_close_batch_cap == 25

    async def test_out_of_range_fails_fast(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _cfg.ServicesConfig(
                drift_reconcile_zombie_close_age_bound_seconds=0
            )
        with pytest.raises(ValidationError):
            _cfg.ServicesConfig(
                drift_reconcile_zombie_close_batch_cap=0
            )

    async def test_env_prefix_wiring(self, monkeypatch):
        monkeypatch.setenv(
            "SERVICES_DRIFT_RECONCILE_ZOMBIE_CLOSE_ENABLED", "false"
        )
        monkeypatch.setenv(
            "SERVICES_DRIFT_RECONCILE_ZOMBIE_CLOSE_AGE_BOUND_SECONDS",
            "3600",
        )
        svc = _cfg.ServicesConfig()
        assert svc.drift_reconcile_zombie_close_enabled is False
        assert (
            svc.drift_reconcile_zombie_close_age_bound_seconds == 3600
        )


class TestApiWiring:
    async def test_api_drift_block_reads_the_three_knobs(self):
        src = (
            _SOURCE_ROOT / "daemon" / "api.py"
        ).read_text(encoding="utf-8")
        for needle in (
            "config.services.drift_reconcile_zombie_close_enabled",
            "config.services."
            "drift_reconcile_zombie_close_age_bound_seconds",
            "config.services.drift_reconcile_zombie_close_batch_cap",
        ):
            assert needle in src, f"api.py must read {needle}"

    async def test_api_forwards_knobs_to_loop_and_reconciler(self):
        src = (
            _SOURCE_ROOT / "daemon" / "api.py"
        ).read_text(encoding="utf-8")
        assert (
            re.search(
                r"zombie_close_enabled=zombie_close_enabled",
                src,
            )
            is not None
        ), "api.py must forward zombie_close_enabled into the loop"
        assert (
            re.search(
                r"zombie_close_age_bound_seconds"
                r"\s*=\s*zombie_close_age_bound",
                src,
            )
            is not None
        )
        assert (
            re.search(
                r"zombie_close_batch_cap\s*=\s*zombie_close_batch_cap",
                src,
            )
            is not None
        )

    async def test_boot_log_line_present(self):
        src = (
            _SOURCE_ROOT / "daemon" / "api.py"
        ).read_text(encoding="utf-8")
        assert (
            "Pattern (h) zombie-active close boot" in src
        ), "the one-shot boot INFO log line must exist"

    async def test_signature_carries_the_kwargs(self):
        from daemon.services.job_recovery_service import (
            JobRecoveryService,
        )

        sig = inspect.signature(
            JobRecoveryService.reconcile_drift_states
        )
        assert "zombie_close_enabled" in sig.parameters
        assert sig.parameters["zombie_close_enabled"].default is True
        assert "zombie_close_age_bound_seconds" in sig.parameters
        assert (
            sig.parameters["zombie_close_age_bound_seconds"].default
            == 7200
        )
        assert "zombie_close_batch_cap" in sig.parameters
        assert sig.parameters["zombie_close_batch_cap"].default == 25
