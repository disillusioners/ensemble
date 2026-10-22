"""Wedge-capture boot-smoke harness (T36 / D42 / D43 / D44 / A15).

WHAT THIS MODULE IS
-------------------
The reusable machinery behind ``tests/integration/test_wedge_capture_boot_smoke.py``:
the **3-leg injection matrix**, the per-leg environment sets, the
isolation/fence helpers, and the systemd-run transient-unit launcher.

WHY THE LEGS ARE INJECTIONS, NOT A REAL WEDGE
---------------------------------------------
The instrument exists to catch a wedge nobody can schedule. The smoke
therefore SYNTHESISES the three shapes:

===== ==================================== ====================================
leg   injection                            what must fire / must NOT fire
===== ==================================== ====================================
(i)   a dedicated BURNER thread spins      ``sustained_cpu`` fires;
      (the GIL is released every switch    ``sustained_loop_lag`` must NOT
      interval, so the loop still runs)    fire (the loop is serviced)
(ii)  the LOOP thread ``time.sleep(3)``    ``sustained_loop_lag`` fires;
                                           ``sustained_cpu`` AND
                                           ``thread_concentration`` must NOT
                                           (a sleep releases the GIL) —
                                           **this negative pair IS the H1/H2
                                           discriminator**
(iii) the LOOP thread spins with NO await  ``sustained_loop_lag`` AND
      (:func:`spin_on_loop`)               ``thread_concentration`` fire,
                                           attributed to
                                           ``loop_thread_native_id``, and the
                                           capture COMPLETES on that live
                                           wedge; the spin frame is VISIBLE
                                           in ``stacks.txt`` (F-13)
===== ==================================== ====================================

A sleep-based block releases the GIL and therefore cannot prove (iii) —
that is why the third leg exists.

SAFETY FENCE
------------
Every leg runs against a ``tmp_path`` capture root and an EPHEMERAL port,
in an isolated ``systemd-run`` transient unit when the daemon lane runs.
Nothing here touches the live daemon, the live install dir, or
``ensemble_prod`` — the fence helpers below ASSERT that before and after.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

#: The opt-in gate — the smoke is NEVER part of a default suite run.
SMOKE_ENV_FLAG = "ENSEMBLE_WEDGE_SMOKE"
#: The second gate for the systemd/daemon lane (needs a transient unit).
SMOKE_UNIT_ENV_FLAG = "ENSEMBLE_WEDGE_SMOKE_UNIT"

#: The live unit the smoke must NEVER touch.
LIVE_UNIT = "ensemble-main.service"

#: The canonical reasons (mirrors ``daemon.services.wedge_capture.triggers``).
REASON_CPU = "sustained_cpu"
REASON_LAG = "sustained_loop_lag"
REASON_CONCENTRATION = "thread_concentration"


def smoke_enabled() -> bool:
    return os.environ.get(SMOKE_ENV_FLAG) == "1"


def unit_lane_enabled() -> bool:
    return os.environ.get(SMOKE_UNIT_ENV_FLAG) == "1"


def nproc() -> int:
    """The host's usable CPU count (recorded in the evidence)."""
    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def multi_core() -> bool:
    """F-14: the negative pairs of legs (ii)/(iii) need >= 2 cores.

    On a single-core host "the loop burned the CPU" cannot be separated
    from "the helper thread burned the CPU", so those legs are SKIPPED,
    never failed.
    """
    return nproc() >= 2


def free_port() -> int:
    """An ephemeral loopback port (never 9797 / 7979)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    if port in (9797, 7979):
        return free_port()
    return port


# ── the per-leg environment (D43: per leg, NEVER one global tiny set) ────


@dataclass(frozen=True)
class LegEnv:
    """One leg's ``ENSEMBLE_WEDGE_CAPTURE_*`` knobs.

    Per-leg values are REQUIRED (RK-22): a single global tiny threshold
    set makes the daemon's own boot work fire spuriously and turns the
    cooldown proof indeterminate.
    """

    name: str
    knobs: dict[str, str]
    expect_fire: tuple[str, ...]
    expect_silent: tuple[str, ...] = ()

    def as_env(self) -> dict[str, str]:
        return dict(self.knobs)


#: leg (i) — a dedicated CPU burner. The lag threshold is deliberately far
#: above a healthy tick so the loop-serviced negative is meaningful.
LEG_CPU = LegEnv(
    name="leg-i-cpu-burn",
    knobs={
        "ENSEMBLE_WEDGE_CAPTURE_CPU_PCT": "50",
        "ENSEMBLE_WEDGE_CAPTURE_CPU_SUSTAIN_S": "0.5",
        "ENSEMBLE_WEDGE_CAPTURE_LAG_S": "30",
        "ENSEMBLE_WEDGE_CAPTURE_LAG_SUSTAIN_S": "0",
        "ENSEMBLE_WEDGE_CAPTURE_THREAD_CONCENTRATION_PCT": "99",
        "ENSEMBLE_WEDGE_CAPTURE_SAMPLE_INTERVAL_S": "0.25",
        "ENSEMBLE_WEDGE_CAPTURE_COOLDOWN_S": "300",
        "ENSEMBLE_WEDGE_CAPTURE_DEADLINE_S": "20",
    },
    expect_fire=(REASON_CPU,),
    expect_silent=(REASON_LAG,),
)

#: leg (ii) — the loop-thread SLEEP block (the H2 shape: no CPU peg).
LEG_LAG = LegEnv(
    name="leg-ii-loop-sleep",
    knobs={
        "ENSEMBLE_WEDGE_CAPTURE_CPU_PCT": "999",
        "ENSEMBLE_WEDGE_CAPTURE_CPU_SUSTAIN_S": "0",
        "ENSEMBLE_WEDGE_CAPTURE_LAG_S": "2.0",
        "ENSEMBLE_WEDGE_CAPTURE_LAG_SUSTAIN_S": "0",
        "ENSEMBLE_WEDGE_CAPTURE_THREAD_CONCENTRATION_PCT": "999",
        "ENSEMBLE_WEDGE_CAPTURE_SAMPLE_INTERVAL_S": "0.25",
        "ENSEMBLE_WEDGE_CAPTURE_COOLDOWN_S": "300",
        "ENSEMBLE_WEDGE_CAPTURE_DEADLINE_S": "20",
    },
    expect_fire=(REASON_LAG,),
    expect_silent=(REASON_CPU, REASON_CONCENTRATION),
)

#: leg (iii) — the loop-thread CPU SPIN (maximum GIL contention, live wedge).
LEG_SPIN = LegEnv(
    name="leg-iii-loop-spin",
    knobs={
        # The PROCESS-CPU trigger is deliberately out of reach for this
        # leg: it would fire ~0.5 s in, and the watchdog would then be
        # busy capturing while the spin expired — leg (iii) asserts LAG +
        # CONCENTRATION, so the CPU trigger must not preempt them.
        "ENSEMBLE_WEDGE_CAPTURE_CPU_PCT": "200",
        "ENSEMBLE_WEDGE_CAPTURE_CPU_SUSTAIN_S": "0.5",
        "ENSEMBLE_WEDGE_CAPTURE_LAG_S": "2.0",
        "ENSEMBLE_WEDGE_CAPTURE_LAG_SUSTAIN_S": "0",
        "ENSEMBLE_WEDGE_CAPTURE_THREAD_CONCENTRATION_PCT": "50",
        "ENSEMBLE_WEDGE_CAPTURE_SAMPLE_INTERVAL_S": "0.25",
        "ENSEMBLE_WEDGE_CAPTURE_COOLDOWN_S": "300",
        "ENSEMBLE_WEDGE_CAPTURE_DEADLINE_S": "20",
    },
    expect_fire=(REASON_LAG, REASON_CONCENTRATION),
    expect_silent=(),
)

ALL_LEGS: tuple[LegEnv, ...] = (LEG_CPU, LEG_LAG, LEG_SPIN)


def apply_leg_env(leg: LegEnv, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Return the process env with the leg's knobs applied.

    The instrument's OWN knobs only — the AutoContinue lever is never
    touched (BC-1).
    """
    env = dict(os.environ if environ is None else environ)
    env.update(leg.as_env())
    return env


# ── the injections ────────────────────────────────────────────────────────


class Burner:
    """A dedicated thread that burns CPU until stopped (leg i)."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="SmokeBurner", daemon=True)
        self.ticks = 0

    def _run(self) -> None:
        while not self._stop.is_set():
            self.ticks += 1

    def __enter__(self) -> "Burner":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        self._thread.join(timeout=5.0)


def sleep_on_loop(loop: Any, seconds: float = 3.0) -> None:
    """Block the EVENT LOOP thread for ``seconds`` (leg ii).

    A sleep RELEASES the GIL, so the process shows no CPU peg — this is
    the H2 shape.
    """
    loop.call_soon_threadsafe(time.sleep, seconds)


def spin_on_loop(loop: Any, seconds: float = 3.0) -> None:
    """Burn CPU ON the event-loop thread, with no await (leg iii).

    Unlike a sleep this HOLDS the GIL (released only at the switch
    interval), so the loop cannot service its callbacks while the frame
    below is running — the stack dump must show ``spin_on_loop``.
    """
    deadline = time.monotonic() + seconds

    # The DISTINCTIVE name is load-bearing (F-13): the leg proves the
    # capture's stack dump shows THIS injected frame — a name shared with
    # the scheduling helper would not identify the burner.
    def wedge_smoke_injected_spin() -> None:  # noqa: D401 - a frame name
        end = deadline
        counter = 0
        while time.monotonic() < end:
            counter += 1

    loop.call_soon_threadsafe(wedge_smoke_injected_spin)


# ── the capture observation helpers ──────────────────────────────────────


def capture_dirs(root: Path) -> list[Path]:
    """Every capture directory under ``root`` (manifest-bearing only)."""
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and (path / "manifest.json").exists()
    )


def wait_for_captures(
    root: Path,
    *,
    minimum: int = 1,
    timeout: float = 20.0,
) -> list[Path]:
    """Wait until ``minimum`` captures exist (or the timeout elapses)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = capture_dirs(root)
        if len(found) >= minimum:
            return found
        time.sleep(0.1)
    return capture_dirs(root)


def manifests(root: Path) -> list[dict[str, Any]]:
    """Every capture's manifest under ``root`` (oldest first).

    A manifest can still be mid-write the instant its directory becomes
    visible (the writer creates the directory first and finalises the
    manifest LAST), so a malformed read is retried briefly rather than
    silently dropped — a dropped manifest would turn a real capture into
    a phantom "nothing fired".
    """
    out: list[dict[str, Any]] = []
    for directory in capture_dirs(root):
        payload: dict[str, Any] | None = None
        for attempt in range(10):
            try:
                payload = json.loads(
                    (directory / "manifest.json").read_text(encoding="utf-8")
                )
                break
            except (OSError, ValueError, TypeError):
                time.sleep(0.05 * (attempt + 1))
        if isinstance(payload, dict):
            out.append(payload)
    return out


def reasons(root: Path) -> set[str]:
    """The set of ``trigger_reason`` values captured under ``root``."""
    return {str(item.get("trigger_reason")) for item in manifests(root)}


@dataclass
class LegObservation:
    """What one leg observed (the evidence the assertions ride on)."""

    leg: str
    updates: dict[str, str] = field(default_factory=dict)
    root: Path | None = None
    capture_count: int = 0
    fired: set[str] = field(default_factory=set)
    captured_at: list[str] = field(default_factory=list)
    injection_window: tuple[float, float] | None = None
    stacks_contains_spin_frame: bool = False
    loop_thread_native_id: int | None = None
    attributed_native_ids: set[int] = field(default_factory=set)
    missed_samples: int | None = None
    watchdog_alive: bool | None = None
    nproc: int = field(default_factory=nproc)

    def as_dict(self) -> dict[str, Any]:
        return {
            "leg": self.leg,
            "nproc": self.nproc,
            "capture_count": self.capture_count,
            "fired": sorted(self.fired),
            "captured_at": self.captured_at,
            "injection_window": self.injection_window,
            "stacks_contains_spin_frame": self.stacks_contains_spin_frame,
            "loop_thread_native_id": self.loop_thread_native_id,
            "attributed_native_ids": sorted(self.attributed_native_ids),
            "missed_samples": self.missed_samples,
            "watchdog_alive": self.watchdog_alive,
            "root": str(self.root) if self.root else None,
        }


def leg_attribution(root: Path) -> tuple[int | None, set[int]]:
    """Read ``threads.csv`` of the newest capture: (top native id, all ids)."""
    directories = capture_dirs(root)
    if not directories:
        return None, set()
    csv_path = directories[-1] / "threads.csv"
    try:
        lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return None, set()
    if len(lines) < 2:
        return None, set()
    header = lines[0].split(",")
    try:
        native_index = header.index("native_id")
        share_index = header.index("share_pct")
    except ValueError:
        return None, set()
    attributed: set[int] = set()
    top_id: int | None = None
    top_share = -1.0
    for row in lines[1:]:
        parts = row.split(",")
        if len(parts) <= max(native_index, share_index):
            continue
        try:
            native_id = int(parts[native_index])
            share = float(parts[share_index])
        except (TypeError, ValueError):
            continue
        attributed.add(native_id)
        if share > top_share:
            top_share = share
            top_id = native_id
    return top_id, attributed


def stacks_show_frame(root: Path, frame_name: str) -> bool:
    """Does the newest capture's ``stacks.txt`` contain ``frame_name``?"""
    directories = capture_dirs(root)
    if not directories:
        return False
    try:
        text = (directories[-1] / "stacks.txt").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return frame_name in text


# ── the isolation / fence helpers (D42 / SC-14) ──────────────────────────


def cgroup_path(pid: int) -> str:
    """The ``/proc/<pid>/cgroup`` line — the isolation proof."""
    try:
        return Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def assert_cgroup_isolated(pid: int) -> None:
    """The transient unit must NOT live under the live daemon's cgroup."""
    membership = cgroup_path(pid)
    assert membership, f"/proc/{pid}/cgroup is unreadable — cannot prove isolation"
    assert LIVE_UNIT not in membership, (
        f"the smoke process is INSIDE {LIVE_UNIT} ({membership}) — refusing to "
        "run: the smoke must never share the live daemon's cgroup"
    )
    assert "ensemble-upgrade-" not in membership, membership


def transient_unit_argv(
    *,
    unit: str,
    workdir: Path,
    argv: list[str],
    env: dict[str, str],
) -> list[str]:
    """The ``systemd-run`` argv for an ISOLATED transient unit (D42).

    ``--wait --collect`` + explicit ``--setenv`` for every knob; the unit
    name is prefixed so a runaway can be identified (and never matches
    ``ensemble-main.service``).
    """
    assert not unit.startswith("ensemble-main"), unit
    command = [
        "systemd-run",
        f"--unit={unit}",
        "--wait",
        "--collect",
        "--pipe",
        f"--working-directory={workdir}",
        "--property=TimeoutStopSec=30",
    ]
    for key, value in env.items():
        command.append(f"--setenv={key}={value}")
    command.extend(argv)
    return command


def unit_exists(unit: str) -> bool:
    """Does the transient unit still exist? (the F-14 close-out check)"""
    if shutil.which("systemctl") is None:
        return False
    completed = subprocess.run(
        ["systemctl", "show", unit, "--property=LoadState", "--value"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() not in ("", "not-found")


def live_daemon_fence() -> dict[str, str]:
    """A snapshot of the LIVE daemon's identity — must be unchanged."""
    snapshot = {"unit": LIVE_UNIT}
    if shutil.which("systemctl") is None:
        return snapshot
    for prop, key in (
        ("MainPID", "main_pid"),
        ("ActiveEnterTimestamp", "active_enter"),
        ("NRestarts", "n_restarts"),
    ):
        completed = subprocess.run(
            ["systemctl", "show", LIVE_UNIT, f"--property={prop}", "--value"],
            capture_output=True,
            text=True,
            check=False,
        )
        snapshot[key] = completed.stdout.strip()
    return snapshot


def livez_version(port: int = 9797, *, timeout: float = 2.0) -> str:
    """The live daemon's ``/livez`` version ("" when unreachable)."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/livez", timeout=timeout
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return str(payload.get("version", ""))
    except (urllib.error.URLError, OSError, ValueError):
        return ""


def assert_live_fence_unchanged(before: dict[str, str], after: dict[str, str]) -> None:
    """SC-14: the live daemon's identity is IDENTICAL before and after."""
    for key in ("main_pid", "active_enter", "n_restarts"):
        if key in before and key in after:
            assert before[key] == after[key], (
                f"the live daemon's {key} changed ({before[key]!r} -> "
                f"{after[key]!r}) — the smoke touched the live unit"
            )


def scratch_env(*, tmp_path: Path, port: int, pg_env: Iterable[tuple[str, str]]) -> dict[str, str]:
    """The isolated child environment (scratch DB + scratch dirs).

    Never reuses a live path: ``HOME``/``DATA_DIR`` point INTO
    ``tmp_path`` (the mktemp-FAKE_HOME discipline), and the PG credentials
    come from ``PG_TEST_*`` only.
    """
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": str(fake_home),
        "ENSEMBLE_DATA_DIR": str(tmp_path / "data"),
        "DAEMON_PORT": str(port),
        "ENSEMBLE_SELF_ENV": "sandbox",
    }
    env.update({key: value for key, value in pg_env})
    return env


def run_injected_leg(
    *,
    root: Path,
    leg: LegEnv,
    inject: Callable[[Any], None],
    hold_s: float = 3.0,
    settle_s: float = 20.0,
) -> LegObservation:
    """Drive ONE leg against a REAL loop + the REAL watchdog.

    The caller supplies ``inject`` (a callable taking the loop); this
    function normalises the observation the leg's assertions read.

    NOTE: this helper does NOT set the process env — the caller applies
    the leg's knobs (``apply_leg_env``) before constructing the config,
    so the watchdog's "read once at start" contract is honoured.
    """
    import asyncio

    from daemon.services.wedge_capture.config import WedgeCaptureConfig
    from daemon.services.wedge_capture.watchdog import WedgeCaptureWatchdog

    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever, name="SmokeLoop", daemon=True)
    loop_thread.start()
    config = WedgeCaptureConfig.from_env(data_dir=root)
    watchdog = WedgeCaptureWatchdog(
        config=config,
        app_state=None,
        loop=loop,
        boot_epoch=None,
        release_version="smoke",
    )
    observation = LegObservation(leg=leg.name, root=config.capture_dir)
    watchdog.start()
    try:
        # let the probe establish its first sample before injecting
        time.sleep(1.0)
        window_start = time.monotonic()
        inject(loop)
        # HOLD the leg open for its whole injection window: the triggers
        # are independent and the cooldown is PER REASON, so a leg that
        # asserts two reasons (leg iii) must stay wedged long enough for
        # BOTH to land — reading the moment the first capture appears
        # would race the second one.
        time.sleep(max(0.0, hold_s))
        found = wait_for_captures(config.capture_dir, minimum=1, timeout=settle_s)
        # ... and settle once more so a capture in flight is not read
        # mid-write.
        time.sleep(1.0)
        found = capture_dirs(config.capture_dir)
        window_end = time.monotonic()
        observation.capture_count = len(found)
        observation.injection_window = (window_start, window_end)
        observation.fired = reasons(config.capture_dir)
        observation.captured_at = [
            str(item.get("captured_at")) for item in manifests(config.capture_dir)
        ]
        observation.loop_thread_native_id = watchdog._lag_probe.loop_thread_native_id
        top_id, attributed = leg_attribution(config.capture_dir)
        observation.attributed_native_ids = attributed
        observation.stacks_contains_spin_frame = stacks_show_frame(
            config.capture_dir, "wedge_smoke_injected_spin"
        )
        status = watchdog.status()
        observation.missed_samples = status.get("missed_samples")
        observation.watchdog_alive = status.get("running")
    finally:
        watchdog.stop()
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=5.0)
        if not loop.is_closed():
            loop.close()
    return observation
