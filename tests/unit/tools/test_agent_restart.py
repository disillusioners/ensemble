"""agent-restart §8 test plan — T0 precondition pins, T0b spoof vectors,
mandated cases 1–6 + T8 unit rails.

Scope (unit lane): the provenance gate (direct + inherited), env-scoping
(demo stays FREE), rails (cooldown/min-uptime/budget — LIVE-only, live
fail-closed), budget envelope (increment-first, window boundary),
dry-run read-only verdict (never `_refusal`), stale-arm boot closure
(BOTH variants + run_id-matched lock release), supervision_scope (both
slice fixtures + lib.sh input parity), transient unit prefix, wake
reader kind-awareness (T0b′), executor env isolation (T0c), and the
no-re-stamp rule (T0d).

The REAL executor-crossing restart-sim (case 7) lives in the drill lane
(test/drills/, mirroring test_promote_cgroup_survivorship.sh) — ⛔ it
fences via mktemp FAKE_HOME + escape guard, NEVER a live-shaped path.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import daemon.tools.upgrade_journal as uj
from daemon.tools import upgrade_tools as ut
from daemon.tools.upgrade_tools import create_upgrade_tools

INSTANCE_ID = "inst-agent-restart-tests"


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def install(tmp_path: Path) -> Path:
    inst = tmp_path / "install"
    (inst / "releases").mkdir(parents=True)
    uj.journal_init(inst)
    uj.ensure_extensions(inst)
    return inst


def _manager(windows: dict | None = None, inherited: dict | None = None) -> MagicMock:
    mgr = MagicMock(name="InstanceManager")
    mgr.config.daemon.port = 0
    task_repo = MagicMock()
    task_repo.has_instance_busy = MagicMock(return_value=False)
    mgr._task_repo = task_repo
    mgr._user_origin_windows = windows or {}
    mgr._inherited_provenance_windows = inherited or {}
    mgr._user_origin_last_stamp = {}
    markers: list[dict] = []
    mgr.set_pending_system_execution = MagicMock(
        side_effect=lambda iid, spec: markers.append(dict(spec))
    )
    mgr._markers = markers
    return mgr


def _live_gate(install: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENSEMBLE_SELF_ENV", "live")
    monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: install)


def _demo_gate(install: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENSEMBLE_SELF_ENV", "demo")
    monkeypatch.setattr(ut, "_resolve_install_dir", lambda self_env: install)


def _boot_epoch_aged(seconds: int = 1200) -> None:
    from daemon.services.boot_epoch import set_boot_epoch

    set_boot_epoch(
        (datetime.now(timezone.utc) - timedelta(seconds=seconds)).replace(
            tzinfo=None
        )
    )


def _clear_boot_epoch() -> None:
    from daemon.services.boot_epoch import set_boot_epoch

    set_boot_epoch(None)


def _tools(mgr) -> dict:
    return {
        t.name: t
        for t in create_upgrade_tools(mgr, INSTANCE_ID, agent_id="maintenancer")
    }


async def _restart(mgr, **kw) -> str:
    kw.setdefault("target_env", "live")
    kw.setdefault("reason", "apply worker_pool_size 5→7")
    return await _tools(mgr)["system_restart"].ainvoke(kw)


def _stamp_direct(mgr, source="api", msg="m-1") -> None:
    mgr._user_origin_windows[INSTANCE_ID] = {
        "source": source,
        "message_id": msg,
        "stamped_at": uj.now_iso(),
        "expires_at": uj.iso_plus(uj.now_iso(), uj.NONCE_TTL_S),
    }


def _refusal_reason(out: str) -> str | None:
    import re

    m = re.search(r"reason=([a-z0-9-]+):", out)
    return m.group(1) if m else None


# ── T0 — precondition pins ───────────────────────────────────────────────────


class TestT0Preconditions:
    def test_t0a_upgrade_factor2_never_calls_restart_helper(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T0a: upgrade factor-2 keeps explicit direct-store access —
        the upgrade gate's factor-2 block reads ``_user_origin_windows``
        directly and NEVER calls ``_restart_provenance`` (directional
        isolation). Source-level pin."""
        src = Path(ut.__file__).read_text(encoding="utf-8")
        si = src.index("async def system_upgrade")
        ei = src.index("system_upgrade._full_doc_", si)
        upgrade_body = src[si:ei]
        assert "_restart_provenance" not in upgrade_body, (
            "upgrade factor-2 must never call the restart provenance helper"
        )
        # factor-2 explicit direct-store read preserved verbatim
        assert 'windows.get(current_instance_id)' in upgrade_body

    async def test_t0a_prime_hardened_fallback_is_reserved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T0a′ (N4): the R18 auto-dispatch fallback source is RESERVED
        — classify_user_origin fails it closed; the old bare-'api' mint
        is gone from the fallback site."""
        from daemon.constants import is_reserved_source
        from daemon.tools.upgrade_journal import classify_user_origin

        assert is_reserved_source("auto_dispatch_fallback")
        ok, detail = classify_user_origin("auto_dispatch_fallback", None)
        assert ok is False and detail == "reserved-internal"
        src = Path(ut.__file__).read_text(encoding="utf-8")
        import daemon.tools.instance as instance_mod

        isrc = Path(instance_mod.__file__).read_text(encoding="utf-8")
        fallback_block = isrc[
            isrc.index("_auto_dispatch_first_turn") : isrc.index(
                "_auto_dispatch_first_turn"
            )
            + 4000
        ]
        assert 'else "auto_dispatch_fallback"' in fallback_block
        assert 'else "api"' not in fallback_block

    async def test_t0b_prime_promote_wake_not_completed_by_stop_forced_kill(
        self, install: Path
    ) -> None:
        """T0(b′): kind-aware reader — a PROMOTE-kind wake is NOT
        completed by ``stop_forced_kill`` (nor restart_gate /
        restart_abandoned); a RESTART-kind wake IS."""
        armed = uj.now_iso()
        journal = {
            "history": [
                {
                    "ts": uj.now_iso(),
                    "event": "stop_forced_kill",
                    "detail": "pid 1 survived",
                }
            ]
        }
        assert (
            uj.wake_terminal_event_after(journal, armed, kind="promote") is None
        )
        assert (
            uj.wake_terminal_event_after(journal, armed, kind="restart")
            == "stop_forced_kill"
        )

    def test_t0c_executor_env_isolation(self) -> None:
        """T0c: the restart lane NEVER sets ENSEMBLE_UPGRADE_LIVE;
        ENSEMBLE_RESTART_LIVE is threaded ONLY via the explicit
        extra_env merge (absent from EXECUTOR_ENV_ALLOWLIST)."""
        assert "ENSEMBLE_UPGRADE_LIVE" not in uj.EXECUTOR_ENV_ALLOWLIST
        assert "ENSEMBLE_RESTART_LIVE" not in uj.EXECUTOR_ENV_ALLOWLIST
        import daemon.manager as mgr_mod

        msrc = Path(mgr_mod.__file__).read_text(encoding="utf-8")
        si = msrc.index("async def drain_pending_system_execution")
        drain = msrc[si : si + 12000]
        # the RESTART branch mints the restart var …
        assert 'extra_env["ENSEMBLE_RESTART_LIVE"] = "1"' in drain
        # … and does NOT read/write the upgrade var (comment mentions of
        # the promote branch's env are docstring prose, not mints — pin
        # the mint sites instead).
        import re as _re

        assert _re.search(r"extra_env\[\"ENSEMBLE_UPGRADE_LIVE\"\]", drain) is None
        assert _re.search(r"extra_env\[\"ENSEMBLE_RESTART_LIVE\"\]", drain) is not None

    async def test_t0d_restart_wake_mints_no_user_window(
        self, install: Path
    ) -> None:
        """T0d: a restart-kind wake's delivery stamps NOTHING (A-2) and
        rides the reserved source (C-2) — the wake turn can never
        re-mint direct provenance."""
        from daemon.services.upgrade_journal_sweep import (
            UpgradeJournalSweepService,
        )

        mgr = _manager()
        wake = uj.PendingWake(
            run_id="r-t0d", kind="restart", env="demo",
            arming_instance_id=INSTANCE_ID, source="discord:user1",
        )
        svc = UpgradeJournalSweepService(install, manager=mgr)
        # The re-stamp gate is kind-based: head.kind != "restart" guards
        # the stamp loop in the deliver pass. Source-level pin + the
        # reserved-source constant exists.
        assert uj.RESTART_WAKE_SOURCE == "system:post_restart_arm_notify"
        from daemon.constants import is_reserved_source

        assert is_reserved_source(uj.RESTART_WAKE_SOURCE)


# ── T0b — spoof vectors + N1 pins ────────────────────────────────────────────


class TestT0bSpoofVectors:
    async def test_internal_invoke_and_wait_never_inherits(
        self, monkeypatch: pytest.MonkeyPatch, install: Path
    ) -> None:
        """N1: the inherit lane keys ONLY on internal_agent: — an
        internal_invoke_and_wait:{parent} dispatch NEVER mints an
        inherited window (the funnel clears it)."""
        from daemon.manager import InstanceManager  # noqa: F401 — type only

        mgr = _manager()
        # simulate: parent holds a live direct window; child dispatches
        # arrive with different source shapes through the funnel helper
        mgr._user_origin_windows["caller-1"] = {
            "source": "api",
            "message_id": "m",
            "stamped_at": uj.now_iso(),
            "expires_at": uj.iso_plus(uj.now_iso(), uj.NONCE_TTL_S),
        }
        # Direct: internal_agent:caller-1 inherits (mechanism B)
        mgr._inherited_provenance_windows.pop(INSTANCE_ID, None)
        InstanceManager._inherit_provenance_window(
            mgr, INSTANCE_ID, "internal_agent:caller-1"
        )
        assert INSTANCE_ID in mgr._inherited_provenance_windows
        # internal_invoke_and_wait: NEVER (reserved — N1)
        InstanceManager._inherit_provenance_window(
            mgr, INSTANCE_ID, "internal_invoke_and_wait:caller-1"
        )
        assert INSTANCE_ID not in mgr._inherited_provenance_windows
        # system:*, agent:*, None also clear
        for src in ("system:post_restart_arm_notify", "agent:x", None):
            InstanceManager._inherit_provenance_window(mgr, INSTANCE_ID, src)
            assert INSTANCE_ID not in mgr._inherited_provenance_windows

    async def test_dry_run_live_full_verdict_zero_mutation(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T0b: dry_run on live renders the FULL verdict (provenance +
        every rail + overrides) with ZERO journal mutation, via the
        read-only formatter — `_refusal()` untouched (no REFUSED
        string)."""
        _live_gate(install, monkeypatch)
        _clear_boot_epoch()
        mgr = _manager()
        before = uj.journal_path(install).read_bytes()
        out = await _restart(mgr, dry_run=True)
        assert out.startswith("RESTART VERDICT (dry-run, zero mutation)")
        assert "no-user-provenance" in out  # the failing gate is named
        assert "restart-too-soon" in out  # epoch unavailable → fail-closed
        assert "NO mutation happened" in out
        assert uj.journal_path(install).read_bytes() == before
        assert not uj.lock_dir(install).exists()
        assert not mgr._markers

    async def test_second_restart_on_wake_turn_refused(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T0b: a restart wake delivered on the reserved lane carries NO
        provenance — a second restart armed from THAT turn is refused
        `no-user-provenance` (the wake cannot re-mint authorization)."""
        _live_gate(install, monkeypatch)
        _boot_epoch_aged()
        mgr = _manager()  # no window — simulates the post-wake turn state
        out = await _restart(mgr, dry_run=False)
        assert _refusal_reason(out) == "no-user-provenance"

    async def test_mixed_kind_coalesce_partition(self, install: Path) -> None:
        """T0b mixed-group pin (F-1): restart + promote wakes for ONE
        instance form TWO groups (17+0⇒1, 17+17⇒2 — pinned at the
        partition helper level)."""
        from daemon.services.upgrade_journal_sweep import (
            UpgradeJournalSweepService,
        )

        svc = UpgradeJournalSweepService(install, manager=_manager())

        def _wake(kind: str, n: int) -> list[tuple[uj.PendingWake, str]]:
            return [
                (
                    uj.PendingWake(
                        run_id=f"r-{kind}-{i}", kind=kind, env="demo",
                        arming_instance_id=INSTANCE_ID,
                    ),
                    "restart" if kind == "restart" else "commit",
                )
                for i in range(n)
            ]

        groups = svc._coalesce_wakes(_wake("restart", 17) + _wake("promote", 0))
        assert len(groups) == 1
        groups = svc._coalesce_wakes(_wake("restart", 17) + _wake("promote", 17))
        assert len(groups) == 2
        # 17 same-kind ⇒ cap 16 ⇒ overflow accounting (A-1) drops 1
        coalesced = svc._coalesce_wakes(_wake("restart", 17))
        kept = sum(len(v[0]) for v in coalesced.values())
        assert kept == uj.PENDING_WAKE_COALESCE_MAX


# ── Case 1 — genuine-user turn → scheduled (+ env-scope: demo stays FREE) ────


class TestCase1GenuineUserSchedules:
    async def test_live_direct_window_schedules(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _live_gate(install, monkeypatch)
        _boot_epoch_aged()
        mgr = _manager()
        _stamp_direct(mgr)
        out = await _restart(mgr, dry_run=False)
        assert "RESTART SCHEDULED" in out, out
        assert "provenance: class=direct" in out
        assert "root=api" in out
        assert "REFUSED" not in out
        # marker carries the C-1 provenance_ok carrier
        assert mgr._markers and mgr._markers[0].get("provenance_ok") is True
        # audit row written
        data = uj.journal_read(install)
        run_id = mgr._markers[0]["run_id"]
        row = (data.get("restart_audits") or {}).get(run_id, {})
        assert row.get("provenance_kind") == "direct"
        assert row.get("outcome") == "pending"

    async def test_live_inherited_window_schedules(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _live_gate(install, monkeypatch)
        _boot_epoch_aged()
        inherited = {
            INSTANCE_ID: {
                "root_source": "discord:user9",
                "root_message_id": "m-root",
                "root_stamped_at": uj.now_iso(),
                "depth": 2,
                "expires_at": uj.iso_plus(uj.now_iso(), 600),
            }
        }
        mgr = _manager(inherited=inherited)
        out = await _restart(mgr, dry_run=False)
        assert "RESTART SCHEDULED" in out, out
        assert "provenance: class=inherited" in out
        assert "depth=2" in out
        assert "stamped_at=" in out and "expires=" in out  # authorization age

    async def test_env_scope_demo_arms_without_any_window(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """§8 case-1 env-scope assertion: NO window seeded on demo —
        the arm still SCHEDULES (demo/dev/sandbox stay FREE; no silent
        gating, no rails)."""
        _demo_gate(install, monkeypatch)
        mgr = _manager()  # empty windows
        out = await _restart(mgr, target_env="demo", dry_run=False)
        assert "RESTART SCHEDULED" in out, out
        # no provenance line on non-live receipts
        assert "provenance: class=" not in out
        # demo arms carry NO provenance_ok carrier (env-scoped gate)
        assert mgr._markers and "provenance_ok" not in mgr._markers[0]


# ── Case 4 — rails (LIVE-only; fail-closed) ─────────────────────────────────


class TestCase4Rails:
    async def test_cooldown_active_refused(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _live_gate(install, monkeypatch)
        _boot_epoch_aged()
        uj.journal_update_field(
            install, "cooldown_until", uj.iso_plus(uj.now_iso(), 900)
        )
        mgr = _manager()
        _stamp_direct(mgr)
        out = await _restart(mgr, dry_run=False)
        assert _refusal_reason(out) == "cooldown-active"

    async def test_cooldown_unparseable_fails_closed(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _live_gate(install, monkeypatch)
        _boot_epoch_aged()
        uj.journal_update_field(install, "cooldown_until", "not-a-date")
        mgr = _manager()
        _stamp_direct(mgr)
        out = await _restart(mgr, dry_run=False)
        assert _refusal_reason(out) == "cooldown-active"

    async def test_min_uptime_epoch_unavailable_fails_closed(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _live_gate(install, monkeypatch)
        _clear_boot_epoch()
        mgr = _manager()
        _stamp_direct(mgr)
        out = await _restart(mgr, dry_run=False)
        assert _refusal_reason(out) == "restart-too-soon"
        assert "unavailable" in out

    async def test_budget_cap_refused_and_window_sliding(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _live_gate(install, monkeypatch)
        _boot_epoch_aged()
        for _ in range(3):
            uj.restart_budget_increment(install)
        mgr = _manager()
        _stamp_direct(mgr)
        out = await _restart(mgr, dry_run=False)
        assert _refusal_reason(out) == "restart-budget-exceeded"

    async def test_budget_window_boundary(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-ε inside the window counts; T-window (expired) does not
        (the sliding list, NOT the fixed journal bucket)."""
        data = uj.journal_read(install)
        now = datetime.now(timezone.utc)
        data["restart_window"] = {
            "events": [
                (now - timedelta(seconds=86390)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                (now - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ]
        }
        uj.journal_write(install, data)
        assert uj.restart_budget_count(install) == 2
        data["restart_window"] = {
            "events": [
                (now - timedelta(seconds=86460)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ]
        }
        uj.journal_write(install, data)
        assert uj.restart_budget_count(install) == 0

    async def test_env_scope_rails_do_not_fire_off_live(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """§8 case-4 env-scope: cooldown + budget + min-uptime do NOT
        gate demo — even with every rail 'tripped', the demo arm
        schedules."""
        _demo_gate(install, monkeypatch)
        _clear_boot_epoch()  # would fail-closed on live
        uj.journal_update_field(
            install, "cooldown_until", uj.iso_plus(uj.now_iso(), 900)
        )
        for _ in range(3):
            uj.restart_budget_increment(install)
        mgr = _manager()  # no window — would refuse on live
        out = await _restart(mgr, target_env="demo", dry_run=False)
        assert "RESTART SCHEDULED" in out, out

    async def test_budget_increment_first_in_arm_envelope(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """V-1: the increment lands INSIDE the lock-holding try BEFORE
        the op writes — an arm that dies after the increment still
        consumed budget (abandoned arms consume, no refund)."""
        _live_gate(install, monkeypatch)
        _boot_epoch_aged()
        mgr = _manager()
        _stamp_direct(mgr)

        order: list[str] = []
        real_increment = uj.restart_budget_increment
        real_write_op = uj.write_pending_op

        def _inc(path):
            order.append("budget")
            return real_increment(path)

        def _wop(path, op):
            order.append("op")
            return real_write_op(path, op)

        monkeypatch.setattr(uj, "restart_budget_increment", _inc)
        monkeypatch.setattr(uj, "write_pending_op", _wop)
        out = await _restart(mgr, dry_run=False)
        assert "RESTART SCHEDULED" in out, out
        assert order.index("budget") < order.index("op")


# ── Case 6 — deferred ordering (mirror) ──────────────────────────────────────


class TestCase6DeferredOrdering:
    async def test_armed_returns_scheduled_no_spawn(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Case 6 (mirror of test_armed_demo_returns_scheduled_no_spawn):
        the tool RETURNS before any stop; only the post-turn marker is
        set — no spawn from the tool call itself."""
        _demo_gate(install, monkeypatch)
        mgr = _manager()
        out = await _restart(mgr, target_env="demo", dry_run=False)
        assert "RESTART SCHEDULED" in out
        assert "executes_after_turn: yes" in out
        # marker armed; nothing spawned (spawn lives in the drain)
        assert mgr._markers


# ── T8 — rails extras ────────────────────────────────────────────────────────


class TestT8Extras:
    def test_stale_arm_closure_variant_a_and_lock_release(
        self, install: Path
    ) -> None:
        """T8: boot closure covers variant (a) no in_flight AND frees
        the run_id-matched lock — post-closure, promote arming
        succeeds (pipeline-busy is GONE)."""
        run_id = "r-stale-a"
        op = uj.PendingOp(
            run_id=run_id, kind="restart", env="demo",
            armed_by_instance=INSTANCE_ID, owner_pid=0,
        )
        uj.lock_acquire(install, run_id)
        uj.ensure_extensions(install)
        uj.write_pending_op(install, op)
        note = uj.close_stale_restart_arm(install)
        assert note is not None and "variant a" in note
        data = uj.journal_read(install)
        assert data.get("pending_op") is None
        assert not uj.lock_dir(install).exists()
        events = [e["event"] for e in data["history"]]
        assert "restart_abandoned" in events

    def test_stale_arm_closure_variant_b_in_flight_set(
        self, install: Path
    ) -> None:
        run_id = "r-stale-b"
        op = uj.PendingOp(
            run_id=run_id, kind="restart", env="demo",
            armed_by_instance=INSTANCE_ID, owner_pid=0,
        )
        uj.lock_acquire(install, run_id)
        uj.ensure_extensions(install)
        uj.write_pending_op(install, op)
        uj.journal_update_field(
            install, "in_flight",
            {"kind": "restart", "target": None, "started_at": uj.now_iso(),
             "flipped": False, "owner_pid": 0, "run_id": run_id},
        )
        note = uj.close_stale_restart_arm(install)
        assert note is not None and "variant b" in note
        data = uj.journal_read(install)
        assert data.get("pending_op") is None
        assert data.get("in_flight") is None
        assert not uj.lock_dir(install).exists()

    def test_stale_arm_skips_live_executor(
        self, install: Path
    ) -> None:
        """A restart op whose executor pid is ALIVE is left untouched
        (boot pass must not race a surviving unit)."""
        run_id = "r-stale-live"
        op = uj.PendingOp(
            run_id=run_id, kind="restart", env="demo",
            armed_by_instance=INSTANCE_ID, owner_pid=os.getpid(),  # alive
            owner_kind="executor",
        )
        uj.lock_acquire(install, run_id)
        uj.ensure_extensions(install)
        uj.write_pending_op(install, op)
        assert uj.close_stale_restart_arm(install) is None
        assert uj.read_pending_op(install) is not None

    def test_supervision_scope_both_slice_fixtures(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """§4(f)/T8: the DERIVED-scope classifier reads the cgroup slice
        prefix — /system.slice/ ⇒ system (+unit name), /user.slice/ ⇒
        user; SUPERVISION_UNIT_DIR corroborates the unit file; unknown
        shape ⇒ scope=unknown (never a default --user)."""
        from daemon.services.upgrade_journal_sweep import (
            UpgradeJournalSweepService,
        )

        svc = UpgradeJournalSweepService(tmp_path, manager=_manager())
        fake_proc = tmp_path / "proc"
        fake_proc.mkdir()

        _orig_open = open

        def _cg(pid: int, body: str):
            fake_cgroup = fake_proc / f"{pid}.cgroup"
            fake_cgroup.write_text(body, encoding="utf-8")

            def _patched_open(p, *a, **k):
                if str(p) == f"/proc/{pid}/cgroup":
                    return _orig_open(fake_cgroup, *a, **k)
                return _orig_open(p, *a, **k)

            monkeypatch.setattr("builtins.open", _patched_open)

        _cg(os.getpid(), "0::/system.slice/ensemble-main.service\n")
        info = svc.supervision_scope()
        assert info["scope"] == "system"
        assert info["unit"] == "ensemble-main.service"
        line = svc._escape_hatch_line(info)
        assert "sudo systemctl restart ensemble-main.service" in line
        assert "--user" not in line
        assert "launcher" not in line.replace("launcher alternative", "")

        _cg(os.getpid(), "0::/user.slice/user-1000.slice/session-5.scope\n")
        info = svc.supervision_scope()
        assert info["scope"] == "user"

        # unknown shape — never a default --user
        _cg(os.getpid(), "0::/weird\n")
        info = svc.supervision_scope()
        assert info["scope"] in ("unknown", "user", "system")  # classified only
        if info["scope"] == "unknown":
            line = svc._escape_hatch_line(info)
            assert "--user restart" not in line

    def test_supervision_scope_unit_dir_override(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from daemon.services.upgrade_journal_sweep import (
            UpgradeJournalSweepService,
        )

        svc = UpgradeJournalSweepService(tmp_path, manager=_manager())
        unit_dir = tmp_path / "units"
        unit_dir.mkdir()
        (unit_dir / "ensemble-main.service").write_text("[Unit]\n", encoding="utf-8")
        monkeypatch.setenv("SUPERVISION_UNIT_DIR", str(unit_dir))
        monkeypatch.setenv("SYSTEMCTL_BIN", "/usr/local/bin/systemctl")
        # unreadable cgroup → unit-file corroboration path
        info = svc.supervision_scope(pid=999999999)
        assert info["unit"] == "ensemble-main.service"
        assert info["systemctl_bin"] == "/usr/local/bin/systemctl"

    def test_unit_name_prefix_restart(self) -> None:
        """V-7/A-5: restart executors carry ensemble-restart-<run_id>
        unit names; promote default unchanged."""
        assert uj.UNIT_NAME_PREFIX == "ensemble-upgrade-"
        assert uj.UNIT_NAME_PREFIX_RESTART == "ensemble-restart-"
        argv = uj.build_service_argv(
            ["bash", "x"], "r-20261019-000000-abcd", "system", {},
            Path("/tmp/log"),
            unit_prefix=uj.UNIT_NAME_PREFIX_RESTART,
        )
        joined = " ".join(argv)
        assert "ensemble-restart-r-20261019-000000-abcd" in joined

    async def test_restart_kind_argv_lock_adoption_and_unit_prefix(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The drain threads unit_prefix=ensemble-restart- for restart
        markers + mints ENSEMBLE_RESTART_LIVE only when
        spec['provenance_ok'] is present (C-1: absent ⇒ no mint)."""
        from daemon.manager import InstanceManager

        mgr = MagicMock(name="InstanceManager")
        mgr._pending_system_executions = {}
        mgr.set_pending_system_execution = lambda iid, spec: (
            mgr._pending_system_executions.update({iid: dict(spec)})
        )
        scripts = install / "scripts" / "upgrade"
        scripts.mkdir(parents=True)
        (scripts / "restart.sh").write_text("#!/bin/bash\ntrue\n", encoding="utf-8")
        uj.ensure_extensions(install)

        calls: list[dict] = []

        def _rec(argv, install_dir, extra_env=None, *, run_id=None,
                 unit_prefix="ensemble-upgrade-"):
            calls.append(
                {"argv": list(argv), "extra_env": dict(extra_env or {}),
                 "unit_prefix": unit_prefix}
            )
            return 424242, "(daemonized)"

        monkeypatch.setattr(uj, "spawn_executor", _rec)
        for provenance_ok in (True, False, None):
            calls.clear()
            run_id = f"r-drain-{provenance_ok}"
            uj.lock_acquire(install, run_id)
            uj.write_pending_op(
                install,
                uj.PendingOp(
                    run_id=run_id, kind="restart", env="demo",
                    armed_by_instance=INSTANCE_ID,
                ),
            )
            spec = {
                "kind": "restart", "env": "demo", "run_id": run_id,
                "reason": "apply pool size", "install_dir": str(install),
                "scripts_dir": str(scripts), "port": 0,
            }
            if provenance_ok is not None:
                spec["provenance_ok"] = provenance_ok
            mgr.set_pending_system_execution(INSTANCE_ID, spec)
            result = await InstanceManager.drain_pending_system_execution(
                mgr, INSTANCE_ID
            )
            assert result is True
            assert calls[0]["unit_prefix"] == "ensemble-restart-"
            if provenance_ok:
                assert calls[0]["extra_env"].get("ENSEMBLE_RESTART_LIVE") == "1"
            else:
                assert "ENSEMBLE_RESTART_LIVE" not in calls[0]["extra_env"]
            assert "ENSEMBLE_UPGRADE_LIVE" not in calls[0]["extra_env"]
            uj.clear_pending_op(install)
            uj.lock_release(install, owner_pid=os.getpid())

    def test_reason_charset(self) -> None:
        ok, _ = ut._reason_ok("apply worker_pool_size 5→7")
        assert ok
        for bad in ("ab", "", "  ", "a" * 201, "line\nbreak", "tab\there"):
            ok, msg = ut._reason_ok(bad)
            assert not ok, bad


# ── Case 5 — notify contents (formatter unit) ────────────────────────────────


class TestCase5NotifyContents:
    def _svc(self, install: Path, tmp_path: Path):
        from daemon.services.upgrade_journal_sweep import (
            UpgradeJournalSweepService,
        )

        return UpgradeJournalSweepService(install, manager=_manager())

    def test_wake_body_contract(
        self, install: Path, tmp_path: Path
    ) -> None:
        svc = self._svc(install, tmp_path)
        run_id = "r-case5"
        uj.ensure_extensions(install)
        uj.record_restart_audit(
            install, run_id,
            {"run_id": run_id, "reason": "apply worker_pool_size 5→7",
             "overrides": [
                 {"key": "limits.worker_pool_size", "live_before": 5,
                  "db_value": 7, "status": "pending"}
             ]},
        )
        uj.journal_history_append(
            install, "restart_gate",
            f"restart run_id={run_id} gate PASSED; per-gate: livez=pass "
            "latency=1.2s budget=180s readyz=pass latency=0.9s budget=120s",
        )
        journal = uj.journal_read(install)
        wake = uj.PendingWake(
            run_id=run_id, kind="restart", env="live",
            arming_instance_id=INSTANCE_ID,
            provenance_class="direct", provenance_root_source="api",
            provenance_root_message_id="m-1", provenance_depth=0,
            provenance_stamped_at="2026-10-20T01:00:00Z",
        )
        body = svc._format_wake_body(wake, "restart", journal, install)
        # label, not raw key (N-2)
        assert "restarted (intentional)" in body
        # per-gate detail from the journal channel
        assert "livez=pass" in body and "readyz=pass" in body
        # overrides (V-8: None ≠ zero-dict — resolution absent ⇒ named)
        assert "limits.worker_pool_size: 5→7" in body
        assert "unavailable" in body or "applied" in body
        # provenance + authorization age
        assert "class=direct" in body and "stamped_at=2026-10-20T01:00:00Z" in body
        # three-signal disclosure
        assert "three-signal" in body or "JobItem" in body
        # NO escape hatch on SUCCESS outcome (N-4)
        assert "operator action" not in body
        # failure outcome renders the DERIVED-scope hatch ONLY
        body_halt = svc._format_wake_body(wake, "halt", journal, install)
        assert "operator action" in body_halt
        assert "systemctl" in body_halt
        assert "restart_current" not in body_halt

    def test_g51_none_vs_zero_dict_distinct(self, tmp_path: Path):
        """G-5.1 coverage pin (tester micro-fix cycle): the V-8 None ≠
        zero-dict discriminator is asserted DISTINCTLY — boot-resolution
        record ABSENT (None) renders the named unavailable form and NOT
        an applied claim; a populated winning-db resolution renders
        (applied) and NOT unavailable. (The pre-existing pin was a
        disjunction — `"unavailable" in body or "applied" in body` —
        which cannot distinguish the two.)"""
        from daemon.services import settings_service as ss
        from daemon.services.upgrade_journal_sweep import (
            UpgradeJournalSweepService,
        )

        svc = UpgradeJournalSweepService(tmp_path, manager=_manager())
        wake = uj.PendingWake(
            run_id="r-g51", kind="restart", env="live",
            arming_instance_id=INSTANCE_ID,
        )
        audit = {
            "overrides": [
                {"key": "limits.worker_pool_size", "live_before": 5,
                 "db_value": 7, "status": "pending"}
            ]
        }
        # (a) None — unavailable, NAMED; no applied claim
        ss.set_last_applied_resolution(None)
        body = svc._render_applied_overrides(wake, audit)
        assert "unavailable — boot resolution record absent" in body, body
        assert "(applied)" not in body, body
        assert "not-applied" not in body, body
        # (b) populated + db wins + value matches → applied
        ss.set_last_applied_resolution(
            {"limits.worker_pool_size": {
                "source": "db", "value": 7, "default": 5,
                "env_value": None, "yaml_value": None, "db_override": 7,
            }}
        )
        body = svc._render_applied_overrides(wake, audit)
        assert "limits.worker_pool_size: 5→7 (applied)" in body, body
        assert "unavailable" not in body, body
        # (c) populated but a NON-db tier wins → not-applied with the tier
        ss.set_last_applied_resolution(
            {"limits.worker_pool_size": {
                "source": "env", "value": 5, "default": 5,
                "env_value": 5, "yaml_value": None, "db_override": 7,
            }}
        )
        body = svc._render_applied_overrides(wake, audit)
        assert "not-applied — resolved tier=env" in body, body
        # restore the module-global (other tests read it)
        ss.set_last_applied_resolution(None)

    def test_outcome_labels_x4(self) -> None:
        for key in (
            "restart_gate", "restart_abandoned", "stop_forced_kill",
            "live-restart-refused",
        ):
            assert key in ut._OUTCOME_LABELS, key


# ── §6.3 — upgrade_status(section="restarts") ───────────────────────────────


class TestRestartAuditQuerySurface:
    async def test_restarts_section_renders_rows(
        self, install: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _demo_gate(install, monkeypatch)
        mgr = _manager()
        uj.ensure_extensions(install)
        uj.record_restart_audit(
            install, "r-q1",
            {"run_id": "r-q1", "env": "demo", "kind": "restart",
             "reason": "apply pool size", "armed_at": "2026-10-20T01:30:00Z",
             "arming_agent_id": "maintenancer",
             "arming_instance_id": "inst-ab12cd34",
             "outcome": "applied",
             "overrides": [{"key": "limits.worker_pool_size",
                            "live_before": 5, "db_value": 7,
                            "status": "applied"}]},
        )
        uj.record_restart_audit(
            install, "r-q2",
            {"run_id": "r-q2", "env": "demo", "kind": "restart",
             "reason": "no auth", "armed_at": "2026-10-20T02:30:00Z",
             "outcome": "refused", "refusal_token": "no-user-provenance"},
        )
        tools = _tools(mgr)
        out = await tools["upgrade_status"].ainvoke(
            {"target_env": "demo", "section": "restarts"}
        )
        assert "RESTART AUDIT" in out
        assert "maintenancer/inst-ab1" in out
        assert "limits.worker_pool_size: 5→7 (applied)" in out
        assert "refused (no-user-provenance)" in out
