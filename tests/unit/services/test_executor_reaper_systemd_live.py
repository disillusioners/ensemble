"""Cycle-3 E3 + E5 — the FIRST default-on legs exercising the reaper's
unit probe + classifier against REAL systemd on this host (RR1's gap:
every prior reaper test is hermetic — probe_fn injection — and the
watchdog pack's privileged leg is PRESENCE-ONLY, `command -v systemd-run`).

E3 — a REAL transient unit through the REAL classifier:
mint a systemd-run transient on the system bus (non-root minting works
on this host; the payload self-exits at 60s worst case and the unit
carries ``--collect`` so it self-GCs — bounded by construction), probe
it LIVE through the REAL ``executor_unit_state_probe``, classify via
the REAL ``classify_executor_exit`` (default probe_fn — NO injection),
terminate the payload, and classify the terminal shape. The unit name
is scratch-sentinel (the bash lane-guard's scratch carve-out).

E5 / AC-OBS-3 — the verify-phase narrative reaches the payload log:
the mechanically-checkable property (a payload emitting the verify
budget line + a verify-phase diagnostic on UNBUFFERED STDERR — the
specified flush mechanism, ``printf >&2`` — leaves BOTH in the unit's
journal) proven against real journald, default-on with a loud skip.
The full halted-hand-back harness (privileged approver note 7) remains
the disclosed residual.

Both legs SKIP LOUDLY (never fail) on a host without the substrate:
systemd-run absent, transient minting refused (polkit posture), or
journald unreadable — a container/CI host gets a skip, not a red.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from daemon.tools import upgrade_journal as uj

_UNIT_PREFIX = "ens-e3-scratch-"


def _systemd_available() -> tuple[bool, str]:
    if shutil.which("systemd-run") is None:
        return False, "systemd-run absent"
    if shutil.which("systemctl") is None:
        return False, "systemctl absent"
    return True, ""


def _mint(unit: str, argv: list[str]) -> tuple[bool, str]:
    mint_argv = ["systemd-run", "--collect", "--unit", unit]
    if os.geteuid() != 0:
        # a system-bus transient runs as ROOT by default — the payload
        # would be unkillable by the test user (measured: EPERM) and
        # cleanup would depend on the payload's own exit. Run it as the
        # CALLING user so the leg can terminate it deterministically.
        mint_argv += ["--property", f"User={os.environ.get('USER', 'nea')}"]
    mint_argv += argv
    try:
        r = subprocess.run(
            mint_argv,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=15, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, f"mint failed: {type(exc).__name__}: {exc}"
    if r.returncode != 0:
        return False, "mint refused: " + r.stderr.decode()[:120].strip()
    return True, ""


def _probe_prop(unit: str, prop: str) -> str:
    r = subprocess.run(
        ["systemctl", "show", unit, "-p", prop, "--value"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=10, check=False,
    )
    return r.stdout.decode().strip()


def _cleanup(unit: str, main_pid: int | None) -> None:
    # bounded + idempotent: kill the payload (if any), best-effort
    # reset-failed (polkit may refuse — fine: --collect self-GCs).
    if main_pid and main_pid > 1:
        try:
            os.kill(main_pid, 15)
        except OSError:
            pass
    subprocess.run(
        ["systemctl", "reset-failed", unit],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=10, check=False,
    )


class TestReaperRealSystemdLive:
    def test_e3_real_transient_through_the_real_classifier(
        self, tmp_path: Path,
    ) -> None:
        ok, why = _systemd_available()
        if not ok:
            pytest.skip(f"live-systemd substrate absent: {why}")
        unit = f"{_UNIT_PREFIX}{os.getpid()}-{int(time.time())}"
        ok, why = _mint(unit, ["/bin/sleep", "60"])
        if not ok:
            pytest.skip(f"transient minting unavailable on this host: {why}")
        # MainPID needs a bounded retry — a freshly minted transient can
        # report MainPID=0 for a moment before exec starts (a zero read
        # would silently skip the kill and leave the payload running).
        main_pid = 0
        deadline = time.time() + 5
        while time.time() < deadline:
            main_pid = int(_probe_prop(unit, "MainPID") or 0)
            if main_pid > 1:
                break
            time.sleep(0.1)
        try:
            # LIVE shape: the real probe sees a loaded+active unit …
            props, err = uj.executor_unit_state_probe(unit, "system")
            assert props is not None, f"probe failed on a live unit: {err}"
            assert props["LoadState"] == "loaded"
            assert props["ActiveState"] == "active"
            # … and the real classifier (NO probe_fn injection) calls it
            # ALIVE — the client died, the ceremony has not.
            cls = uj.classify_executor_exit(tmp_path, "e3-live", unit, "system")
            assert cls.kind == "client_exit_alive", cls.note
            # TERMINAL shape: terminate the payload, wait for the state
            # to settle (bounded), classify again.
            if main_pid > 1:
                try:
                    os.kill(main_pid, 15)
                except OSError:
                    pass
            deadline = time.time() + 10
            while time.time() < deadline:
                active = _probe_prop(unit, "ActiveState")
                load = _probe_prop(unit, "LoadState")
                if load == "not-found" or active in ("inactive", "failed"):
                    break
                time.sleep(0.2)
            cls2 = uj.classify_executor_exit(
                tmp_path, "e3-dead", unit, "system")
            # a dead transient must NEVER classify alive — and must be a
            # DECIDED verdict (unit_terminal when the unit lingers
            # loaded, ambiguous when --collect already GC'd it — both are
            # real reaper outcomes; the journal-fallback leg).
            assert cls2.kind in ("unit_terminal", "ambiguous"), (
                cls2.kind, cls2.unit_detail, cls2.note
            )
            if cls2.kind == "unit_terminal":
                # the PAYLOAD's own signal/exit code rides the verdict
                assert cls2.exit_code is not None
        finally:
            _cleanup(unit, main_pid)

    def test_e5_obs3_verify_narrative_reaches_the_payload_log(
        self, tmp_path: Path,
    ) -> None:
        """AC-OBS-3's mechanically-checkable property, proven against
        real journald: a payload emitting the verify budget line AND a
        verify-phase diagnostic on unbuffered stderr (the specified
        flush mechanism) leaves BOTH in the unit's journal — the C-8
        truncation question is exactly whether the narrative REACHES the
        log, and this leg pins the channel end-to-end."""
        ok, why = _systemd_available()
        if not ok:
            pytest.skip(f"live-systemd substrate absent: {why}")
        if shutil.which("journalctl") is None:
            pytest.skip("journalctl absent")
        unit = (
            f"{_UNIT_PREFIX}obs3-{os.getpid()}-{int(time.time())}"
        )
        payload_script = (
            "printf 'verify budget: handback 90s (bounded wait)\\n' >&2; "
            "printf 'HALT: verify-phase diagnostic — gate refused "
            "(fixture emission)\\n' >&2; sleep 1"
        )
        ok, why = _mint(unit, ["/bin/sh", "-c", payload_script])
        if not ok:
            pytest.skip(f"transient minting unavailable: {why}")
        try:
            # wait for the payload to have run (bounded)
            deadline = time.time() + 15
            emitted = ""
            while time.time() < deadline:
                r = subprocess.run(
                    ["journalctl", "-u", unit, "-n", "20", "--no-pager"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    timeout=10, check=False,
                )
                emitted = r.stdout.decode(errors="replace")
                if "verify budget" in emitted and "HALT:" in emitted:
                    break
                time.sleep(0.3)
            assert "verify budget" in emitted, (
                "the verify budget line did NOT reach the payload log"
            )
            assert "HALT:" in emitted and "verify-phase" in emitted, (
                "the verify-phase diagnostic did NOT reach the payload log"
            )
        finally:
            _cleanup(unit, None)
