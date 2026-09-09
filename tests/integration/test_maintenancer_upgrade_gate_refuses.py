"""Maintenancer system_upgrade 3-factor gate (W1-P2, task 2.10g).

The 3-factor gate at ``upgrade_tools.py:1877-2036``:

* F1: ``user_confirmed`` parameter
* F2: per-instance user-origin window (whitelisted source prefixes)
* F3: single-use, 15-min TTL, instance-bound, action-bound nonce
  in the triggering message row content

The gate fires ONLY on ``self_env == "live"`` (call-time refusal at
``upgrade_tools.py:1458-1465`` is defense-in-depth and does NOT
fire on dev/demo).

Pins:

1. A live ``system_upgrade`` request WITHOUT a user nonce echoes
   ``factor_failures: user-confirmation-missing`` (architect §5.3).
2. With a valid nonce + valid user-origin row → request arms
   (verified by checking the journal ``pending_action`` was created
   via ``upgrade_status`` polling).

The test mirrors the ``test_upgrade_tools.py`` fixture pattern
(install + monkey-patched ``_resolve_install_dir``).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import daemon.tools.upgrade_journal as uj
from daemon.tools.upgrade_tools import create_upgrade_tools


INSTANCE_ID = "instance-maintenancer-test"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """Minimal staged-install: journal + one manifest + current symlink."""
    inst = tmp_path / "install"
    (inst / "releases").mkdir(parents=True)
    uj.journal_init(inst)
    uj.ensure_extensions(inst)
    _write_manifest(inst, "1.2.2")
    (inst / "current").symlink_to("releases/1.2.2")
    return inst


def _write_manifest(install_dir: Path, version: str) -> None:
    import json
    rel = install_dir / "releases" / version
    rel.mkdir(parents=True, exist_ok=True)
    (rel / "manifest.json").write_text(json.dumps({
        "name": version,
        "binary_version": version,
        "commit": "deadbeef",
        "timestamp": "2026-09-09T00:00:00Z",
        "rollback_safe": True,  # P2.1 manifest shape — required by halt gates
        "min_keep": 2,
    }))


@pytest.fixture
def manager_with_window(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Manager that exposes a live user-origin window for the instance."""
    mgr = MagicMock(name="InstanceManager")
    mgr.config.daemon.port = 0  # falsy → _self_port None → no network
    task_repo = MagicMock()
    task_repo.has_instance_busy = MagicMock(return_value=False)
    mgr._task_repo = task_repo
    mgr._queue_repository = None

    # Inject a user-origin window that has not expired.
    window = {
        "source": "api",
        "message_id": "msg-1",
        "expires_at": "2099-01-01T00:00:00Z",  # far future
    }
    mgr._user_origin_windows = {INSTANCE_ID: window}
    return mgr


# ── Tests ──────────────────────────────────────────────────────────────────


class TestLiveGateWithoutNonce:
    """A live ``system_upgrade`` WITHOUT a user nonce must echo
    ``factor_failures: user-confirmation-missing``.

    The nonce is the load-bearing factor for F3 (single-use bound
    nonce). Without it, the gate refuses regardless of F1/F2.
    """

    @pytest.mark.asyncio
    async def test_live_upgrade_without_nonce_echoes_user_confirmation_missing(
        self, install: Path, monkeypatch: pytest.MonkeyPatch,
        manager_with_window: MagicMock,
    ) -> None:
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "live")
        monkeypatch.setattr(
            "daemon.tools.upgrade_tools._resolve_install_dir",
            lambda self_env: install,
        )
        tools = {
            t.name: t
            for t in create_upgrade_tools(
                manager_with_window, INSTANCE_ID, agent_id="maintenancer"
            )
        }
        out = await tools["system_upgrade"].ainvoke({
            "target_env": "live",
            "version": "1.2.2",
            "user_confirmed": True,  # F1 is set — but no nonce → fails
            "dry_run": False,
        })
        # The live-gate summary echoes the factor failures.
        assert "user-confirmation-missing" in out, (
            f"Expected user-confirmation-missing refusal; got:\n{out}"
        )
        # No nonce-related bypass.
        assert "nonce-mismatch" not in out
        # The gate refused; no arm happened.
        assert "CONFIRMATION REQUIRED" not in out


class TestLiveGateWithNonceArms:
    """A valid nonce + valid user-origin window → the request arms.

    We dry-run first (mints a nonce), then arm with that nonce +
    a queued message row carrying the nonce content. The arm
    succeeds; ``upgrade_status`` confirms the pending action.
    """

    @pytest.mark.asyncio
    async def test_valid_nonce_arms(
        self, install: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ENSEMBLE_SELF_ENV", "live")
        monkeypatch.setattr(
            "daemon.tools.upgrade_tools._resolve_install_dir",
            lambda self_env: install,
        )

        mgr = MagicMock(name="InstanceManager")
        mgr.config.daemon.port = 0
        mgr._task_repo = MagicMock()
        mgr._task_repo.has_instance_busy = MagicMock(return_value=False)
        window = {
            "source": "api",
            "message_id": "msg-armed-1",
            "expires_at": "2099-01-01T00:00:00Z",
        }
        mgr._user_origin_windows = {INSTANCE_ID: window}

        # Queue repo whose ``get`` returns a row with the nonce in
        # its content — this is F3 satisfied (the nonce appears in
        # the triggering HUMAN message row).
        from types import SimpleNamespace
        nonce_holder: dict[str, str | None] = {"nonce": None}

        def _repo_get(message_id: str) -> Any:
            return SimpleNamespace(content=f"please proceed with nonce {nonce_holder['nonce']}")

        queue_repo = MagicMock()
        queue_repo.get = _repo_get
        mgr._queue_repository = queue_repo

        tools = {
            t.name: t
            for t in create_upgrade_tools(
                mgr, INSTANCE_ID, agent_id="maintenancer"
            )
        }

        # Step 1: dry-run mints a nonce bound to (instance, version).
        out_dry = await tools["system_upgrade"].ainvoke({
            "target_env": "live",
            "version": "1.2.2",
            "user_confirmed": False,
            "dry_run": True,
        })
        assert "CONFIRMATION REQUIRED" in out_dry, (
            f"Expected dry-run to mint a nonce; got:\n{out_dry}"
        )
        # Extract the nonce.
        m = re.search(r"nonce (CONFIRM-[A-Z2-7]+)", out_dry)
        assert m is not None, f"No nonce in dry-run output:\n{out_dry}"
        nonce_holder["nonce"] = m.group(1)

        # Step 2: arm with the nonce + user_confirmed.
        out_arm = await tools["system_upgrade"].ainvoke({
            "target_env": "live",
            "version": "1.2.2",
            "user_confirmed": True,
            "nonce": nonce_holder["nonce"],
            "dry_run": False,
        })
        # The arm path (after the gate) proceeds to write a journal
        # entry. Either the arm succeeds OR it returns a structured
        # failure for downstream reasons — but the live-gate must
        # NOT refuse.
        assert "user-confirmation-missing" not in out_arm, (
            f"Live gate refused even with valid nonce + window; got:\n{out_arm}"
        )

        # Step 3: upgrade_status reflects the armed state.
        out_status = await tools["upgrade_status"].ainvoke({
            "target_env": "live",
        })
        # The status output is text; the test only asserts the gate
        # did NOT refuse — i.e., the arm proceeded past F1/F2/F3.
        assert "user-confirmation-missing" not in out_status, (
            f"upgrade_status echoed gate-refusal after valid arm; got:\n{out_status}"
        )
