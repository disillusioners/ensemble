"""Boot-consistency reconciler — structural + switch + readyz pins.

Companion to ``test_boot_consistency_reconciler.py``. Covers kill-switch /
boot-epoch short-circuits, the structural invariants (source greps), the
migration-exists gate, never-wedge, readyz-leg mechanics, and api.py wiring.
"""

from __future__ import annotations

import inspect
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest

import daemon.services.boot_consistency_reconciler as lane
from daemon.services.boot_consistency_reconciler import (
    reconcile_boot_consistency,
    reset_boot_recovery_state,
)

REPO = Path(__file__).resolve().parents[3]
BOOT_EPOCH = datetime(2026, 11, 23, 18, 0, 0)


class _ZeroRepo:
    """Repo that records any call (kill-switch / epoch-None must make zero)."""

    def __init__(self) -> None:
        self.calls = 0

    def find_boot_consistency_candidates(self, *a, **k):
        self.calls += 1
        return []

    def __getattr__(self, name):
        def _count(*a, **k):
            self.calls += 1
            return None

        return _count


class _ZeroManager:
    def __init__(self) -> None:
        self._task_repo = _ZeroRepo()
        self.resume_calls = 0

    async def _has_checkpoint(self, *a, **k):
        return True

    async def _schedule_explicit_handle_resume(self, **k):
        self.resume_calls += 1
        return {"status": "resuming"}

    async def terminate_instance(self, *a, **k):
        return True


@contextmanager
def _env(name: str, value: str | None):
    old = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


@pytest.fixture(autouse=True)
def _reset_leg():
    reset_boot_recovery_state()
    yield
    reset_boot_recovery_state()


def _executable_source() -> str:
    """Module source with docstrings, triple-quoted strings and comments stripped."""
    src = inspect.getsource(lane)
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    src = re.sub(r"'''[\s\S]*?'''", "", src)
    lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 9. Kill-switch / boot-epoch short-circuits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_switch_off_zero_calls() -> None:
    mgr = _ZeroManager()
    with _env("ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART", "0"):
        out = await reconcile_boot_consistency(mgr, BOOT_EPOCH)
    assert out.skipped_kill_switch == 1
    assert out.mode == "off"
    assert mgr._task_repo.calls == 0
    assert mgr.resume_calls == 0


@pytest.mark.asyncio
async def test_no_boot_epoch_zero_calls() -> None:
    mgr = _ZeroManager()
    with _env("ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART", "1"):
        out = await reconcile_boot_consistency(mgr, None)
    assert out.skipped_no_boot_epoch == 1
    assert mgr._task_repo.calls == 0
    assert mgr.resume_calls == 0


# ---------------------------------------------------------------------------
# 10. Structural greps (assert on module source)
# ---------------------------------------------------------------------------


def test_structural_no_in_memory_candidate_sources() -> None:
    code = _executable_source()
    for forbidden in ("_graph_tasks", "_pending_injections", "_live_children"):
        assert forbidden not in code, f"lane must not use {forbidden} as candidate input"


def test_structural_no_enqueue_message() -> None:
    assert "enqueue_message" not in _executable_source()


def test_structural_no_datetime_now() -> None:
    code = _executable_source()
    assert "datetime.now" not in code
    assert "timezone.utc" not in code


def test_structural_no_resume_instance_cascade() -> None:
    assert "resume_instance_cascade" not in _executable_source()


def test_structural_single_resume_call_site() -> None:
    code = _executable_source()
    assert code.count("_schedule_explicit_handle_resume(") == 1


def test_structural_single_candidate_source() -> None:
    code = _executable_source()
    assert code.count("find_boot_consistency_candidates") == 1


# ---------------------------------------------------------------------------
# 11. Migration-exists pin
# ---------------------------------------------------------------------------


def test_migration_file_exists() -> None:
    migration = (
        REPO
        / "daemon"
        / "migrations"
        / "versions"
        / "20261123_190000_add_instance_boot_re_drive_at.sql"
    )
    assert migration.is_file()
    text = migration.read_text()
    assert "ALTER TABLE instances ADD COLUMN boot_re_drive_at TIMESTAMP" in text


def test_model_and_pg_hook_carry_column() -> None:
    model = (REPO / "daemon" / "repositories" / "instance" / "models.py").read_text()
    assert "boot_re_drive_at" in model
    manager = (REPO / "daemon" / "manager.py").read_text()
    assert "ALTER TABLE instances ADD COLUMN IF NOT EXISTS boot_re_drive_at TIMESTAMP" in manager


# ---------------------------------------------------------------------------
# 12. Never-wedge — raising repo counted, no raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_never_wedge_sweep_level_guard() -> None:
    class _BoomRepo:
        def find_boot_consistency_candidates(self, *a, **k):
            raise RuntimeError("boom")

    class _Mgr:
        _task_repo = _BoomRepo()

    # The lane's selection try/except swallows and returns a result.
    out = await reconcile_boot_consistency(_Mgr(), BOOT_EPOCH)
    assert out.errors >= 1
    assert lane.get_boot_recovery_state()["status"] == "degraded"


# ---------------------------------------------------------------------------
# readyz leg mechanics
# ---------------------------------------------------------------------------


def test_readyz_leg_healthy_payload_byte_identical() -> None:
    from daemon.services.readiness import compute_readiness_composite

    c = compute_readiness_composite(
        database_ok=True, queue_fresh_ok=True, services_ok=True,
        queue_max_age_seconds=None,
    )
    payload = c.to_payload()
    assert c.ready is True
    assert set(payload["components"]) == {
        "database", "queue_freshness", "services", "checkpoint_saver"
    }


def test_readyz_leg_degraded_503_with_reason() -> None:
    from daemon.services.readiness import compute_readiness_composite

    c = compute_readiness_composite(
        database_ok=True, queue_fresh_ok=True, services_ok=True,
        queue_max_age_seconds=None, boot_recovery_ok=False,
    )
    payload = c.to_payload()
    assert c.ready is False
    assert payload["status"] == "degraded"
    assert payload["components"]["boot_recovery"] is False
    assert any("boot_recovery" in r for r in payload["detail"]["reasons"])


def test_readyz_refresh_reads_lane_state() -> None:
    from daemon.services.readiness import _boot_recovery_leg_ok

    assert _boot_recovery_leg_ok() is True  # default healthy
    lane._publish_readyz_state(type("R", (), {"errors": 2})(), BOOT_EPOCH)
    assert _boot_recovery_leg_ok() is False


# ---------------------------------------------------------------------------
# api.py wiring
# ---------------------------------------------------------------------------


def test_api_wires_lane_after_orphan_sweep() -> None:
    src = (REPO / "daemon" / "api.py").read_text()
    assert "reconcile_boot_consistency" in src
    sweep_pos = src.index("sweep_orphaned_live_turns(")
    lane_pos = src.index("reconcile_boot_consistency(")
    # The import line names it first; the actual call must be after the sweep.
    call_pos = src.index("await reconcile_boot_consistency(")
    assert call_pos > sweep_pos
    assert lane_pos > 0
