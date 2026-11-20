#!/usr/bin/env python3
"""Boot-warm load-test harness — 2B schema registry (culprit2-residual M5).

WHAT THIS MEASURES
------------------
``daemon/__main__.py`` starts a BACKGROUND thread named
``schema-registry-warm`` BEFORE the uvicorn listener is bound.  The warm
memoises the pydantic tool schemas process-wide, so the cost is paid once
at boot instead of on the first graph assembly (which runs on the event
loop).  Under the GIL the warm COMPETES with the uvicorn lifespan
startup for CPU, so the shipped code comment's own claim is:

    "It ADDS to boot wall-clock rather than overlapping it ...
     measured ~2.3 s on the dev box (this tree: 255 registry entries
     memoised), and ~16 s on the production box by the deep-dive's
     derivation (NOT re-measured here)."

That ~2.3 s claim was NEVER load-tested.  This harness MEASURES it.
It fixes nothing.

LEGS (each >= 3 boots, median + spread reported)
-------------------------------------------------
  A  warm ON   (default)                          — the shipped shape
  B  warm OFF  (ENSEMBLE_SCHEMA_REGISTRY_WARM=0)  — the counterfactual
  C  warm ON   + 2 synthetic busy-loop processes   — the contention shape

PER-BOOT MEASUREMENTS
---------------------
  * ``t_launch``          — wall clock at ``Popen``
  * ``t_banner``          — first "Starting Ensemble" log line
  * ``t_lifespan_start``  — uvicorn "Waiting for application startup."
  * ``t_lifespan_end``    — uvicorn "Application startup complete."
  * ``t_readyz``          — first ``GET /readyz`` returning 200
  * ``t_warm_start`` / ``t_warm_end`` — the warm thread's own window
  * time-to-readyz = ``t_readyz - t_launch``   (the VERDICT metric)

VERDICT RULE (fixed by the commission brief — not negotiable here)
-----------------------------------------------------------------
  regression  iff  median(time_to_readyz(A)) - median(time_to_readyz(B)) > 1.0 s

FENCES (all enforced by this harness)
-------------------------------------
  * NEVER contacts :9797 (live) or :8079 (dev) — the probe port is 18079.
  * ``ensemble_dev_scratch`` is the ONLY database (never ensemble_dev,
    never a prod shape).
  * ``ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`` is PINNED EXPLICITLY on
    every boot — the harness shell is a daemon child, and an unpinned
    frozen-boot sweep can SIGKILL live processes.
  * The daemon env is built from the WORKTREE ``.env`` plus explicit
    per-variable overrides; the ambient shell env is NOT inherited
    (only PATH/HOME/LANG/... are carried over), so an ambient
    ``POSTGRES_*`` can never leak in.
  * Every boot is torn down by SIGTERM to its RECORDED PID, then the
    port is proven free before the next boot starts.
  * No writes outside the scratch dir and the worktree's gitignored
    ``data/``; no promote; no Docker; no push; no stash.

MEASUREMENT INSTRUMENTATION (disclosed)
---------------------------------------
The daemon logs the warm's END ("[schema-registry] boot warm complete")
but never its START, and the log format is second-resolution
(``datefmt='%H:%M:%S'``).  To get a high-resolution warm window the
harness injects a ``sitecustomize.py`` on ``PYTHONPATH`` that timestamps
the ``schema-registry-warm`` thread's ``start()``/``run()`` boundaries
into a marker file.  NO DAEMON SOURCE IS MODIFIED — the instrumentation
lives entirely in the scratch dir and is inert unless
``BENCH_BOOTWARM_MARKER`` is set.  The per-boot wall-clock stamps of the
log markers carry +/- one poll interval (25 ms) of uncertainty.

Usage
-----
    .venv/bin/python scripts/bench_boot_warm_startup.py \
        --worktree . --out evidence.json --repeats 3

Nothing here is a test: the numbers are RECORDED, not asserted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── fences / defaults ──────────────────────────────────────────────────────
DEFAULT_PORT = 18079
DEFAULT_DB = "ensemble_dev_scratch"
FORBIDDEN_PORTS = (9797, 8079)
POLL_INTERVAL_S = 0.025
BOOT_DEADLINE_S = 420.0
TEARDOWN_WAIT_S = 90.0
VERDICT_THRESHOLD_S = 1.0

# Env keys carried over from the ambient shell.  Everything else is
# dropped: the daemon env is rebuilt from the worktree .env + explicit
# overrides so an ambient POSTGRES_* / ENSEMBLE_* can never leak in.
BASE_ENV_KEYS = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TZ",
    "TERM",
    "USER",
    "LOGNAME",
    "SHELL",
    "TMPDIR",
)

# Log markers (first-seen timestamps, +/- one poll interval).
# NOTE: the daemon's OWN "Starting Ensemble" banner (daemon/__main__.py:838)
# is emitted BEFORE ``install_root_logging()`` runs (it is installed at
# daemon.api import time), so that line never reaches a handler and is NOT
# observable in the log.  The observable banner is daemon.api's (create_app).
MARKER_BANNER = "Starting Ensemble"
MARKER_PREBIND_RE = re.compile(r"Bound \S+ on attempt \d+")
MARKER_LIFESPAN_START = "Waiting for application startup"
MARKER_LIFESPAN_END = "Application startup complete"
MARKER_SERVING = "Uvicorn running on"
MARKER_WARM = "[schema-registry]"

WARM_COMPLETE_RE = re.compile(r"boot warm complete:\s*(\d+)\s+schema")
WARM_DISABLED_RE = re.compile(r"boot warm disabled")


# ── helpers ────────────────────────────────────────────────────────────────
def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` env file without executing it."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def sha256_prefix(value: str, n: int = 12) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:n]


def port_is_free(port: int) -> bool:
    """True when nothing holds a LISTEN socket on ``port`` (bind probe)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def probe_readyz(port: int, timeout: float = 1.5) -> int | None:
    """GET /readyz — returns the HTTP status, or None when unreachable."""
    url = f"http://127.0.0.1:{port}/readyz"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return None


def loadavg() -> dict:
    try:
        raw = Path("/proc/loadavg").read_text(encoding="utf-8").split()
        return {"1m": float(raw[0]), "5m": float(raw[1]), "15m": float(raw[2])}
    except Exception:
        return {}


def cpu_count() -> int:
    return os.cpu_count() or 1


def pick_idle_cpu() -> int | None:
    """Pick the least-busy CPU (1 s sample of /proc/stat).

    Used for the PINNED supplementary legs: the contention only bites when
    the daemon and the busy loops share ONE cpu, and we do not want to
    park that on a cpu the live daemon is already using.
    """
    try:
        def sample() -> dict[int, int]:
            out: dict[int, int] = {}
            for line in Path("/proc/stat").read_text(encoding="utf-8").splitlines():
                if not line.startswith("cpu"):
                    continue
                parts = line.split()
                head = parts[0]
                if not head[3:].isdigit():
                    continue  # the "cpu" aggregate line
                times = [int(v) for v in parts[1:]]
                idle = times[3] + (times[4] if len(times) > 4 else 0)
                out[int(head[3:])] = sum(times) - idle
            return out

        first = sample()
        time.sleep(1.0)
        second = sample()
        deltas = []
        for cpu, total in second.items():
            d = total - first.get(cpu, 0)
            if d > 0:
                deltas.append((d, cpu))
        if not deltas:
            return None
        deltas.sort()
        return deltas[0][1]
    except Exception:
        return None


# ── instrumentation (scratch-side only; daemon source untouched) ───────────
SITECUSTOMIZE_SRC = '''\
"""Harness-side instrumentation for the boot-warm measurement.

Injected on PYTHONPATH by scripts/bench_boot_warm_startup.py.  Inert
unless BENCH_BOOTWARM_MARKER points at a writable file.  It timestamps
the "schema-registry-warm" thread boundary because the daemon logs the
warm's END but never its START, and its log format is second-resolution.
"""

import json
import os
import threading
import time

_MARKER = os.environ.get("BENCH_BOOTWARM_MARKER")


def _write(payload):
    if not _MARKER:
        return
    try:
        with open(_MARKER, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\\n")
            fh.flush()
    except Exception:
        pass


if _MARKER:
    _orig_run = threading.Thread.run

    def _run(self):
        if getattr(self, "name", "") == "schema-registry-warm":
            t0 = time.time()
            try:
                return _orig_run(self)
            finally:
                _write({"event": "warm_end", "t": time.time(), "t_run_start": t0})
        return _orig_run(self)

    threading.Thread.run = _run

    _orig_start = threading.Thread.start

    def _start(self, *args, **kwargs):
        if getattr(self, "name", "") == "schema-registry-warm":
            _write({"event": "warm_start_call", "t": time.time()})
        return _orig_start(self, *args, **kwargs)

    threading.Thread.start = _start
'''


# ── the boot runner ────────────────────────────────────────────────────────
class BootResult(dict):
    """A single boot's measurements (plain dict for JSON serialisation)."""


def build_daemon_env(
    *,
    worktree: Path,
    env_file: Path,
    port: int,
    db: str,
    log_dir: Path,
    marker_path: Path,
    instr_dir: Path,
    warm_enabled: bool,
    data_dir: Path,
) -> dict[str, str]:
    """Build the daemon's env: worktree .env + explicit per-var overrides."""
    env = {k: os.environ[k] for k in BASE_ENV_KEYS if k in os.environ}
    env.update(parse_env_file(env_file))  # == `set -a; . ./.env; set +a`
    env.update(
        {
            # ── FENCE: the only database this harness may touch ──
            "POSTGRES_DB": db,
            # ── FENCE: our own port; never :9797 (live) / :8079 (dev) ──
            "DAEMON_PORT": str(port),
            "PORT": str(port),
            "HOST": "127.0.0.1",
            # ── FENCE: identity marker ──
            "ENSEMBLE_SELF_ENV": "dev",
            # ── MANDATORY PIN: the sweep can SIGKILL live processes ──
            "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES": "0",
            # ── SANCTIONED FIRST BOOT: the daemon's own DB-resolution guard
            #    (daemon/ensemble_config.py:138) refuses a tier-2 (ambient
            #    POSTGRES_*) first boot with exit 78 unless this flag is
            #    set.  The guard's own message names this escape hatch;
            #    the persisted ensemble.json makes every later boot tier 1.
            "ENSEMBLE_DB_AMBIENT_ALLOW": "1",
            # ── FENCE: the data dir lives in the scratch, never the
            #    worktree and never the live install ──
            "ENSEMBLE_DATA_DIR": str(data_dir),
            # ── harness plumbing ──
            "PYTHONUNBUFFERED": "1",
            "DAEMON_LOG_DIR": str(log_dir),
            "BENCH_BOOTWARM_MARKER": str(marker_path),
            "PYTHONPATH": str(instr_dir),
        }
    )
    if not warm_enabled:
        env["ENSEMBLE_SCHEMA_REGISTRY_WARM"] = "0"
    else:
        env.pop("ENSEMBLE_SCHEMA_REGISTRY_WARM", None)
    return env


def spawn_contention(n: int, pin_cpu: int | None) -> list[subprocess.Popen]:
    """Spawn ``n`` CPU-bound busy-loop processes (synthetic contention)."""
    procs = []
    for _ in range(n):
        cmd = [sys.executable, "-c", "while True:\n    pass\n"]
        if pin_cpu is not None:
            cmd = ["taskset", "-c", str(pin_cpu)] + cmd
        procs.append(
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        )
    return procs


def stop_processes(procs: list[subprocess.Popen]) -> None:
    for proc in procs:
        if proc.poll() is not None:
            continue
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
    deadline = time.time() + 10
    for proc in procs:
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.05)
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


def run_boot(
    *,
    leg: str,
    index: int,
    worktree: Path,
    env_file: Path,
    port: int,
    db: str,
    scratch: Path,
    warm_enabled: bool,
    contention: int = 0,
    pin_cpu: int | None = None,
    daemon_pin_cpu: int | None = None,
) -> BootResult:
    """Boot the real daemon once and measure the warm/lifespan windows."""
    if port in FORBIDDEN_PORTS:
        raise SystemExit(f"refusing to use a fenced port: {port}")

    tag = f"{leg}_{index}"
    log_dir = scratch / "logs" / tag
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "daemon.out"
    marker_path = scratch / "markers" / f"{tag}.jsonl"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    if marker_path.exists():
        marker_path.unlink()

    instr_dir = scratch / "instr"
    instr_dir.mkdir(parents=True, exist_ok=True)
    (instr_dir / "sitecustomize.py").write_text(SITECUSTOMIZE_SRC, encoding="utf-8")

    # One data dir per HARNESS RUN (shared by every boot): the first boot
    # persists ensemble.json there (tier 1 for every later boot) and the
    # worktree stays pristine.
    data_dir = scratch / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    env = build_daemon_env(
        worktree=worktree,
        env_file=env_file,
        port=port,
        db=db,
        log_dir=log_dir,
        marker_path=marker_path,
        instr_dir=instr_dir,
        warm_enabled=warm_enabled,
        data_dir=data_dir,
    )

    contention_procs: list[subprocess.Popen] = []
    result = BootResult(
        leg=leg,
        index=index,
        warm_enabled=warm_enabled,
        contention_processes=contention,
        pin_cpu=daemon_pin_cpu,
        started_at=time.time(),
        started_at_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        loadavg_at_start=loadavg(),
        cpu_count=cpu_count(),
    )

    if contention:
        contention_procs = spawn_contention(contention, pin_cpu)
        result["contention_pids"] = [p.pid for p in contention_procs]
        # Let the busy loops actually start burning CPU.
        time.sleep(0.5)

    cmd = [str(worktree / ".venv" / "bin" / "python"), "-m", "daemon"]
    if daemon_pin_cpu is not None:
        cmd = ["taskset", "-c", str(daemon_pin_cpu)] + cmd

    log_fh = open(log_path, "wb")
    t_launch = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=str(worktree),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    result["pid"] = proc.pid
    result["t_launch"] = t_launch
    result["cmd"] = " ".join(cmd)

    markers: dict[str, float] = {}
    banners: list[float] = []
    buffer = ""
    t_readyz = None
    readyz_first_seen_non200 = None
    deadline = t_launch + BOOT_DEADLINE_S
    exited_early = None

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as reader:
            while time.time() < deadline:
                chunk = reader.read()
                if chunk:
                    buffer += chunk
                    while "\n" in buffer:
                        line, _, buffer = buffer.partition("\n")
                        now = time.time()
                        if MARKER_BANNER in line and "Starting Ensemble" in line:
                            banners.append(now)
                            markers.setdefault("app_banner", now)
                        if MARKER_PREBIND_RE.search(line):
                            markers.setdefault("prebind", now)
                        if MARKER_LIFESPAN_START in line:
                            markers.setdefault("lifespan_start", now)
                        elif MARKER_LIFESPAN_END in line:
                            markers.setdefault("lifespan_end", now)
                        elif MARKER_SERVING in line:
                            markers.setdefault("serving", now)
                        elif MARKER_WARM in line:
                            markers.setdefault("warm_log_first", now)
                            m = WARM_COMPLETE_RE.search(line)
                            if m:
                                markers.setdefault("warm_log_complete", now)
                                result["warm_schemas_memoised"] = int(m.group(1))
                            if WARM_DISABLED_RE.search(line):
                                markers.setdefault("warm_log_disabled", now)
                if t_readyz is None:
                    status = probe_readyz(port)
                    if status == 200:
                        t_readyz = time.time()
                    elif status is not None and readyz_first_seen_non200 is None:
                        readyz_first_seen_non200 = time.time()
                        result["readyz_first_non200_status"] = status
                else:
                    break
                if proc.poll() is not None:
                    exited_early = proc.returncode
                    # Drain whatever the dead process wrote.
                    try:
                        tail = Path(log_path).read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        tail = ""
                    buffer += tail
                    break
                time.sleep(POLL_INTERVAL_S)
    finally:
        result["exited_early"] = exited_early

    # ── teardown: SIGTERM to the RECORDED pid ──────────────────────────
    teardown_started = time.time()
    forced_kill = False
    if proc.poll() is None:
        try:
            os.kill(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=TEARDOWN_WAIT_S)
        except subprocess.TimeoutExpired:
            forced_kill = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
    result["exit_code"] = proc.returncode
    result["teardown_seconds"] = time.time() - teardown_started
    result["forced_kill"] = forced_kill
    stop_processes(contention_procs)

    # ── port released? ──────────────────────────────────────────────────
    deadline_free = time.time() + 20
    while time.time() < deadline_free and not port_is_free(port):
        time.sleep(0.1)
    result["port_free_after_teardown"] = port_is_free(port)
    log_fh.close()

    # ── warm window (marker file: high resolution) ─────────────────────
    warm_start = None
    warm_end = None
    if marker_path.exists():
        for raw in marker_path.read_text(encoding="utf-8").splitlines():
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            if payload.get("event") == "warm_start_call":
                warm_start = payload.get("t")
            elif payload.get("event") == "warm_end":
                warm_end = payload.get("t")
    result["t_warm_start"] = warm_start
    result["t_warm_end"] = warm_end
    result["warm_duration_s"] = (
        round(warm_end - warm_start, 4) if (warm_start and warm_end) else None
    )

    # ── markers → durations ─────────────────────────────────────────────
    result["markers"] = {k: round(v, 4) for k, v in markers.items()}
    result["banner_count"] = len(banners)
    result["t_readyz"] = t_readyz
    if t_readyz is not None:
        result["time_to_readyz_s"] = round(t_readyz - t_launch, 4)
        result["app_banner_to_readyz_s"] = (
            round(t_readyz - markers["app_banner"], 4) if "app_banner" in markers else None
        )
        result["prebind_to_readyz_s"] = (
            round(t_readyz - markers["prebind"], 4) if "prebind" in markers else None
        )
        if "lifespan_start" in markers:
            result["lifespan_start_to_readyz_s"] = round(
                t_readyz - markers["lifespan_start"], 4
            )
            result["launch_to_lifespan_start_s"] = round(
                markers["lifespan_start"] - t_launch, 4
            )
        if "lifespan_end" in markers:
            result["lifespan_duration_s"] = round(
                markers["lifespan_end"] - markers.get("lifespan_start", markers["lifespan_end"]),
                4,
            )
            result["lifespan_end_to_readyz_s"] = round(
                t_readyz - markers["lifespan_end"], 4
            )
    else:
        result["time_to_readyz_s"] = None

    # ── warm overlap with the lifespan startup window ───────────────────
    if warm_start and warm_end and "lifespan_start" in markers and "lifespan_end" in markers:
        ls, le = markers["lifespan_start"], markers["lifespan_end"]
        overlap = max(0.0, min(warm_end, le) - max(warm_start, ls))
        result["warm_overlap_lifespan_s"] = round(overlap, 4)
        result["warm_windows_lifespan"] = True
        result["warm_finished_before_lifespan_end"] = warm_end <= le
        result["warm_finished_before_lifespan_start"] = warm_end <= ls
    elif warm_start and warm_end:
        result["warm_overlap_lifespan_s"] = None
        result["warm_windows_lifespan"] = False

    # ── warm overlap with the PRE-lifespan phase (launch → lifespan) ────
    if warm_start and warm_end and "lifespan_start" in markers:
        ls = markers["lifespan_start"]
        result["warm_overlap_pre_lifespan_s"] = round(
            max(0.0, min(warm_end, ls) - max(warm_start, t_launch)), 4
        )
        result["warm_start_after_launch_s"] = round(warm_start - t_launch, 4)
        result["warm_end_after_launch_s"] = round(warm_end - t_launch, 4)
    if warm_start and "prebind" in markers:
        result["warm_start_minus_prebind_s"] = round(warm_start - markers["prebind"], 4)

    # ── log tail (crash evidence) ───────────────────────────────────────
    try:
        tail_lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
        # Keep the evidence blob small and readable: the tail is CRASH
        # evidence, not a log archive.  Crashed boots keep 30 lines, healthy
        # boots keep 5; every line is truncated to 300 chars.
        keep = 30 if exited_early is not None else 5
        result["log_tail"] = [ln[:300] for ln in tail_lines[-keep:]]
        result["log_path"] = str(log_path)
        result["log_line_count"] = len(tail_lines)
    except Exception as exc:
        result["log_tail_error"] = str(exc)

    result["loadavg_at_end"] = loadavg()
    result["finished_at_iso"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return result


# ── statistics ─────────────────────────────────────────────────────────────
def summarise(values: list[float]) -> dict:
    clean = [v for v in values if v is not None]
    if not clean:
        return {"n": 0, "values": []}
    ordered = sorted(clean)
    out = {
        "n": len(ordered),
        "values": [round(v, 4) for v in ordered],
        "median": round(statistics.median(ordered), 4),
        "min": round(min(ordered), 4),
        "max": round(max(ordered), 4),
        "mean": round(statistics.fmean(ordered), 4),
        "stdev": round(statistics.pstdev(ordered), 4) if len(ordered) > 1 else 0.0,
        "spread_s": round(max(ordered) - min(ordered), 4),
    }
    if len(ordered) > 1:
        out["median_abs_dev_s"] = round(
            statistics.median([abs(v - out["median"]) for v in ordered]), 4
        )
    return out


def summarise_leg(boots: list[dict]) -> dict:
    return {
        "n_boots": len(boots),
        "time_to_readyz_s": summarise([b.get("time_to_readyz_s") for b in boots]),
        "app_banner_to_readyz_s": summarise([b.get("app_banner_to_readyz_s") for b in boots]),
        "prebind_to_readyz_s": summarise([b.get("prebind_to_readyz_s") for b in boots]),
        "launch_to_lifespan_start_s": summarise([b.get("launch_to_lifespan_start_s") for b in boots]),
        "lifespan_duration_s": summarise([b.get("lifespan_duration_s") for b in boots]),
        "warm_duration_s": summarise([b.get("warm_duration_s") for b in boots]),
        "warm_overlap_lifespan_s": summarise([b.get("warm_overlap_lifespan_s") for b in boots]),
        "warm_overlap_pre_lifespan_s": summarise([b.get("warm_overlap_pre_lifespan_s") for b in boots]),
        "warm_start_after_launch_s": summarise([b.get("warm_start_after_launch_s") for b in boots]),
        "warm_end_after_launch_s": summarise([b.get("warm_end_after_launch_s") for b in boots]),
        "warm_schemas_memoised": [b.get("warm_schemas_memoised") for b in boots],
        "all_boots_ready": all(b.get("time_to_readyz_s") is not None for b in boots),
        "forced_kills": sum(1 for b in boots if b.get("forced_kill")),
        "crashed_boots": sum(1 for b in boots if b.get("exited_early") is not None),
    }


# ── live-LLM leg ───────────────────────────────────────────────────────────
MOCK_SERVER_SRC = '''\
"""Local mock OpenAI-compatible endpoint (fallback for the live-LLM leg).

Records the request body to RECORD_PATH and answers with a chat completion
that carries one tool call, so the harness can prove that the bound tool
payload serialises and the request path executes end to end.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

RECORD_PATH = os.environ["MOCK_RECORD_PATH"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            payload = {"_raw": body.decode("utf-8", "replace")}
        with open(RECORD_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"path": self.path, "body": payload}) + "\\n")
        tool_names = []
        for entry in payload.get("tools") or []:
            fn = (entry or {}).get("function") or {}
            if fn.get("name"):
                tool_names.append(fn["name"])
        message = {
            "role": "assistant",
            "content": "calling the probe tool",
            "tool_calls": [
                {
                    "id": "call_probe_1",
                    "type": "function",
                    "function": {
                        "name": tool_names[0] if tool_names else "echo_probe",
                        "arguments": json.dumps({"text": "ping"}),
                    },
                }
            ],
        }
        out = {
            "id": "chatcmpl-mock-1",
            "object": "chat.completion",
            "created": 0,
            "model": payload.get("model", "mock"),
            "choices": [
                {"index": 0, "message": message, "finish_reason": "tool_calls"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(out).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    port = int(os.environ["MOCK_PORT"])
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


def run_live_llm_leg(
    *,
    worktree: Path,
    main_env_path: Path,
    scratch: Path,
    timeout_s: float = 90.0,
) -> dict:
    """Exercise build_instance_llms + openai_tool_payloads against a live LLM.

    The OPENAI_* triple is read from the MAIN checkout's .env at runtime.
    The API key is NEVER echoed — only its sha256 prefix is recorded.
    """
    out: dict = {
        "attempted": True,
        "source_env_file": str(main_env_path),
        "timeout_s": timeout_s,
    }
    main_env = parse_env_file(main_env_path)
    base_url = (main_env.get("OPENAI_BASE_URL") or "").strip()
    api_key = (main_env.get("OPENAI_API_KEY") or "").strip()
    model = (main_env.get("OPENAI_MODEL") or "").strip()
    out["base_url"] = base_url
    out["api_key_sha256_prefix"] = sha256_prefix(api_key) if api_key else None
    out["api_key_present"] = bool(api_key)
    out["model"] = model
    if not base_url or not api_key or not model:
        out["verdict"] = "skipped_missing_openai_env"
        return out

    sys.path.insert(0, str(worktree))
    try:
        from langchain_core.messages import HumanMessage
        from langchain_core.tools import tool

        from daemon.graph import build_instance_llms
        from daemon.tools._schema_registry import openai_tool_payloads, registry_stats

        @tool
        def echo_probe(text: str) -> str:
            """Echo a short text back to the caller (probe tool)."""
            return text

        @tool
        def ping_probe() -> str:
            """Return the literal string pong (probe tool)."""
            return "pong"

        tools = [echo_probe, ping_probe]
        payloads = openai_tool_payloads(tools)
        out["tool_payload_names"] = [
            (p.get("function") or {}).get("name") for p in payloads
        ]
        out["tool_payload_bytes"] = len(json.dumps(payloads))
        out["registry_stats_after_payloads"] = registry_stats()

        llm_config = {
            "base_url": base_url,
            "base_url_backup": None,
            "api_key": api_key,
            "model": model,
            "model_vision": None,
            "temperature": 0.0,
            "request_timeout": timeout_s,
            "buffer_response_header": True,
            "max_tokens": 64,
        }
        llm_config_with_headers = {
            **llm_config,
            "default_headers": {
                "x-proxy-app": "ensemble",
                "x-proxy-interleaved-thinking": "True",
                "X-LLMProxy-Buffer-Response": "true",
            },
        }
        llm_with_tools, _ = build_instance_llms(
            llm_config_with_headers=llm_config_with_headers,
            model_standard=model,
            model_vision=None,
            tools=tools,
            retry_config=None,
        )
        t0 = time.time()
        response = llm_with_tools.invoke(
            [
                HumanMessage(
                    content="Call the echo_probe tool with text='ping'. Do not answer in prose."
                )
            ]
        )
        latency = time.time() - t0
        out["latency_s"] = round(latency, 4)
        out["response_type"] = type(response).__name__
        out["response_content"] = str(getattr(response, "content", ""))[:400]
        tool_calls = getattr(response, "tool_calls", None) or []
        out["tool_calls"] = [
            {
                "name": tc.get("name"),
                "args": tc.get("args"),
                "id": tc.get("id"),
            }
            for tc in tool_calls
        ]
        out["has_tool_calls"] = bool(tool_calls)
        out["usage_metadata"] = getattr(response, "usage_metadata", None)
        out["verdict"] = (
            "pass" if (tool_calls or out["response_content"]) else "unparsable_response"
        )
        out["passed"] = True
    except Exception as exc:
        out["verdict"] = "live_provider_error"
        out["passed"] = False
        out["error_type"] = type(exc).__name__
        out["error"] = str(exc)[:600]
    return out


def run_mock_llm_leg(
    *,
    worktree: Path,
    scratch: Path,
    port: int = 18191,
    timeout_s: float = 60.0,
) -> dict:
    """Fallback: a local mock OpenAI endpoint proving the payload serialises."""
    out: dict = {"attempted": True, "mode": "mock", "mock_port": port}
    record_path = scratch / "mock_record.jsonl"
    if record_path.exists():
        record_path.unlink()
    mock_dir = scratch / "mock"
    mock_dir.mkdir(parents=True, exist_ok=True)
    server_py = mock_dir / "mock_openai_server.py"
    server_py.write_text(MOCK_SERVER_SRC, encoding="utf-8")

    env = {k: os.environ[k] for k in BASE_ENV_KEYS if k in os.environ}
    env.update(
        {
            "MOCK_RECORD_PATH": str(record_path),
            "MOCK_PORT": str(port),
            "PYTHONUNBUFFERED": "1",
        }
    )
    proc = subprocess.Popen(
        [str(worktree / ".venv" / "bin" / "python"), str(server_py)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        # wait for the listener
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.1)
        sys.path.insert(0, str(worktree))
        from langchain_core.messages import HumanMessage
        from langchain_core.tools import tool

        from daemon.graph import build_instance_llms
        from daemon.tools._schema_registry import openai_tool_payloads

        @tool
        def echo_probe(text: str) -> str:
            """Echo a short text back to the caller (probe tool)."""
            return text

        tools = [echo_probe]
        payloads = openai_tool_payloads(tools)
        llm_config = {
            "base_url": f"http://127.0.0.1:{port}/v1",
            "base_url_backup": None,
            "api_key": "mock-key-not-a-secret",
            "model": "mock-model",
            "model_vision": None,
            "temperature": 0.0,
            "request_timeout": timeout_s,
            "buffer_response_header": True,
            "max_tokens": 64,
        }
        llm_config_with_headers = {
            **llm_config,
            "default_headers": {"x-proxy-app": "ensemble"},
        }
        llm_with_tools, _ = build_instance_llms(
            llm_config_with_headers=llm_config_with_headers,
            model_standard="mock-model",
            model_vision=None,
            tools=tools,
            retry_config=None,
        )
        t0 = time.time()
        response = llm_with_tools.invoke(
            [HumanMessage(content="Call the echo_probe tool with text='ping'.")]
        )
        out["latency_s"] = round(time.time() - t0, 4)
        out["response_type"] = type(response).__name__
        out["tool_calls"] = [
            {"name": tc.get("name"), "args": tc.get("args")}
            for tc in (getattr(response, "tool_calls", None) or [])
        ]
        recorded = []
        if record_path.exists():
            for raw in record_path.read_text(encoding="utf-8").splitlines():
                try:
                    recorded.append(json.loads(raw))
                except Exception:
                    pass
        out["recorded_request_count"] = len(recorded)
        if recorded:
            body = recorded[0].get("body") or {}
            sent_tools = body.get("tools") or []
            out["wire_tool_names"] = [
                (t.get("function") or {}).get("name") for t in sent_tools
            ]
            out["wire_has_tools"] = bool(sent_tools)
            out["wire_model"] = body.get("model")
            out["wire_payload_bytes"] = len(json.dumps(body))
            out["verdict"] = "pass_mock"
            out["passed"] = True
        else:
            out["verdict"] = "mock_no_request_recorded"
            out["passed"] = False
        out["disclosure"] = (
            "REAL-PROVIDER ACCEPTANCE REMAINS UNPROVEN — the live endpoint was "
            "unreachable, so only the payload serialisation + request path were "
            "exercised against a local mock."
        )
    except Exception as exc:
        out["verdict"] = "mock_error"
        out["passed"] = False
        out["error_type"] = type(exc).__name__
        out["error"] = str(exc)[:600]
    finally:
        stop_processes([proc])
    return out


def build_summary_table(evidence: dict) -> list[dict]:
    """A compact, human-readable view of the per-leg numbers."""
    rows: list[dict] = []
    for leg, data in evidence.get("legs", {}).items():
        summary = data.get("summary", {})
        ttr = summary.get("time_to_readyz_s", {})
        warm = summary.get("warm_duration_s", {})
        overlap = summary.get("warm_overlap_lifespan_s", {})
        pre = summary.get("warm_overlap_pre_lifespan_s", {})
        life = summary.get("lifespan_duration_s", {})
        rows.append(
            {
                "leg": leg,
                "label": data.get("label"),
                "warm_enabled": data.get("warm_enabled"),
                "contention_processes": data.get("contention_processes"),
                "pinned_to_cpu": data.get("pinned_to_cpu"),
                "n": ttr.get("n"),
                "time_to_readyz_median_s": ttr.get("median"),
                "time_to_readyz_min_s": ttr.get("min"),
                "time_to_readyz_max_s": ttr.get("max"),
                "time_to_readyz_spread_s": ttr.get("spread_s"),
                "time_to_readyz_median_abs_dev_s": ttr.get("median_abs_dev"),
                "values_s": ttr.get("values"),
                "warm_duration_median_s": warm.get("median"),
                "warm_duration_values_s": warm.get("values"),
                "warm_overlap_lifespan_median_s": overlap.get("median"),
                "warm_overlap_pre_lifespan_median_s": pre.get("median"),
                "lifespan_duration_median_s": life.get("median"),
                "schemas_memoised": summary.get("warm_schemas_memoised"),
                "crashed_boots": summary.get("crashed_boots"),
                "forced_kills": summary.get("forced_kills"),
            }
        )
    return rows


# ── main ───────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--worktree",
        default=str(REPO_ROOT),
        help="worktree root (the daemon is booted from here)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="evidence JSON path (default: stdout summary only)",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--legs", default="A,B,C")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--scratch", default=None)
    parser.add_argument("--env-file", default=None, help="daemon .env (default: <worktree>/.env)")
    parser.add_argument(
        "--main-env",
        default="/home/nea/ensemble-src/.env",
        help="env file the live-LLM leg reads OPENAI_* from",
    )
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--skip-warmup", action="store_true")
    parser.add_argument(
        "--summarize-only",
        default=None,
        help="recompute the summary table of an existing evidence file and rewrite it",
    )
    parser.add_argument(
        "--warmup-repeats", type=int, default=1, help="discarded boots before measuring"
    )
    args = parser.parse_args()

    if args.summarize_only:
        target = Path(args.summarize_only).resolve()
        evidence = json.loads(target.read_text(encoding="utf-8"))
        evidence["summary_table"] = build_summary_table(evidence)
        target.write_text(
            json.dumps(evidence, indent=2, sort_keys=False, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"summary table refreshed: {target}", flush=True)
        return 0

    worktree = Path(args.worktree).resolve()
    env_file = Path(args.env_file).resolve() if args.env_file else worktree / ".env"
    python = worktree / ".venv" / "bin" / "python"
    if not python.exists():
        raise SystemExit(f"no worktree venv at {python} — run `uv sync` first")
    if args.port in FORBIDDEN_PORTS:
        raise SystemExit(f"refusing to use a fenced port: {args.port}")

    scratch = Path(args.scratch).resolve() if args.scratch else Path(
        tempfile.mkdtemp(prefix="bootwarm-")
    )
    scratch.mkdir(parents=True, exist_ok=True)

    legs_spec = {
        "A": {"warm_enabled": True, "contention": 0, "pin": False, "label": "warm ON (shipped shape)"},
        "B": {"warm_enabled": False, "contention": 0, "pin": False, "label": "warm OFF (ENSEMBLE_SCHEMA_REGISTRY_WARM=0)"},
        "C": {"warm_enabled": True, "contention": 2, "pin": False, "label": "warm ON + 2 busy-loop processes"},
        # Supplementary PINNED legs: the contention only bites when the
        # daemon and the busy loops share ONE cpu.  On a 16-core box the
        # unpinned leg C may be a no-op — the pinned pair answers the
        # "does the warm cost more under contention?" question for real.
        "Ap": {"warm_enabled": True, "contention": 0, "pin": True, "label": "warm ON, daemon pinned to ONE cpu"},
        "Bp": {"warm_enabled": False, "contention": 0, "pin": True, "label": "warm OFF, daemon pinned to ONE cpu"},
        "Cp": {"warm_enabled": True, "contention": 2, "pin": True, "label": "warm ON + 2 busy loops, all pinned to ONE cpu"},
    }
    requested_raw = [leg.strip() for leg in args.legs.split(",") if leg.strip()]
    leg_alias = {name.upper(): name for name in legs_spec}
    requested = []
    for leg in requested_raw:
        canonical = leg_alias.get(leg.upper())
        if canonical is None:
            print(f"skipping unknown leg {leg!r}", flush=True)
            continue
        requested.append(canonical)
    needs_pin = any(legs_spec.get(leg, {}).get("pin") for leg in requested)
    pin_cpu = pick_idle_cpu() if needs_pin else None

    evidence: dict = {
        "schema": "bootwarm-loadtest/v1",
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "worktree": str(worktree),
        "scratch": str(scratch),
        "daemon_entrypoint": f"{python} -m daemon",
        "env_file": str(env_file),
        "fences": {
            "port": args.port,
            "database": args.db,
            "ens_self_env": "dev",
            "boot_sweep_stale_subprocesses": "0",
            "db_ambient_allow": "1",
            "data_dir": str(scratch / "data"),
            "forbidden_ports_not_contacted": list(FORBIDDEN_PORTS),
            "ambient_env_inherited": list(BASE_ENV_KEYS),
            "note_db_ambient_allow": (
                "ENSEMBLE_DB_AMBIENT_ALLOW=1 is the daemon's OWN sanctioned escape "
                "hatch for the tier-2 (ambient POSTGRES_*) first boot "
                "(daemon/ensemble_config.py:138); without it the daemon exits 78 "
                "BEFORE any TCP connection."
            ),
        },
        "host": {"cpu_count": cpu_count(), "loadavg_at_start": loadavg()},
        "verdict_rule": (
            "regression iff median(time_to_readyz(A)) - median(time_to_readyz(B)) > 1.0 s"
        ),
        "legs": {},
        "boots": {},
        "warmup_boots": [],
        "crashes": [],
        "notes": [],
    }

    # ── warm-up boots (discarded) — pay the first-boot migration cost ──
    if not args.skip_warmup and args.warmup_repeats > 0:
        for i in range(args.warmup_repeats):
            print(f"[warmup {i+1}/{args.warmup_repeats}] booting (discarded)...", flush=True)
            res = run_boot(
                leg="warmup",
                index=i,
                worktree=worktree,
                env_file=env_file,
                port=args.port,
                db=args.db,
                scratch=scratch,
                warm_enabled=True,
                contention=0,
            )
            evidence["warmup_boots"].append(
                {
                    "leg": "warmup",
                    "index": i,
                    "time_to_readyz_s": res.get("time_to_readyz_s"),
                    "exit_code": res.get("exit_code"),
                    "exited_early": res.get("exited_early"),
                    "forced_kill": res.get("forced_kill"),
                    "log_tail": res.get("log_tail", [])[-10:],
                }
            )
            print(
                f"[warmup {i+1}] readyz={res.get('time_to_readyz_s')}s exit={res.get('exit_code')}",
                flush=True,
            )

    # ── measured legs ────────────────────────────────────────────────────
    for leg in requested:
        spec = legs_spec[leg]
        boots: list[dict] = []
        for i in range(args.repeats):
            print(f"[leg {leg} boot {i+1}/{args.repeats}] starting...", flush=True)
            res = run_boot(
                leg=leg,
                index=i,
                worktree=worktree,
                env_file=env_file,
                port=args.port,
                db=args.db,
                scratch=scratch,
                warm_enabled=spec["warm_enabled"],
                contention=spec["contention"],
                pin_cpu=pin_cpu if spec["pin"] else None,
                daemon_pin_cpu=pin_cpu if spec["pin"] else None,
            )
            boots.append(res)
            evidence["boots"][f"{leg}_{i}"] = res
            if res.get("exited_early") is not None:
                evidence["crashes"].append(
                    {
                        "leg": leg,
                        "index": i,
                        "exit_code": res.get("exited_early"),
                        "log_tail": res.get("log_tail", [])[-30:],
                    }
                )
            print(
                f"[leg {leg} boot {i+1}] time_to_readyz={res.get('time_to_readyz_s')}s "
                f"warm={res.get('warm_duration_s')}s exit={res.get('exit_code')}",
                flush=True,
            )
        evidence["legs"][leg] = {
            "label": spec["label"],
            "warm_enabled": spec["warm_enabled"],
            "contention_processes": spec["contention"],
            "pinned_to_cpu": pin_cpu if spec["pin"] else None,
            "summary": summarise_leg(boots),
            "boot_indices": list(range(len(boots))),
        }

    # ── verdict ──────────────────────────────────────────────────────────
    def _median(leg: str) -> float | None:
        return (
            evidence["legs"]
            .get(leg, {})
            .get("summary", {})
            .get("time_to_readyz_s", {})
            .get("median")
        )

    med_a, med_b, med_c = _median("A"), _median("B"), _median("C")
    med_ap, med_bp, med_cp = _median("Ap"), _median("Bp"), _median("Cp")
    verdict: dict = {"threshold_s": VERDICT_THRESHOLD_S, "pin_cpu": pin_cpu}
    if med_a is not None and med_b is not None:
        delta = round(med_a - med_b, 4)
        verdict.update(
            {
                "median_a_s": med_a,
                "median_b_s": med_b,
                "delta_a_minus_b_s": delta,
                "regression": bool(delta > VERDICT_THRESHOLD_S),
                "rule": "regression iff (median_A - median_B) > 1.0 s",
            }
        )
    if med_c is not None:
        verdict["median_c_s"] = med_c
        if med_a is not None:
            verdict["delta_c_minus_a_s"] = round(med_c - med_a, 4)
    if med_ap is not None and med_bp is not None:
        delta = round(med_ap - med_bp, 4)
        verdict["pinned"] = {
            "median_Ap_s": med_ap,
            "median_Bp_s": med_bp,
            "delta_Ap_minus_Bp_s": delta,
            "regression": bool(delta > VERDICT_THRESHOLD_S),
            "note": (
                "SUPPLEMENTARY (not the briefed verdict): the same rule applied to "
                "the PINNED pair, where the daemon and the busy loops share one cpu "
                "so the contention actually bites."
            ),
        }
    if med_cp is not None:
        verdict.setdefault("pinned", {})["median_Cp_s"] = med_cp
        if med_ap is not None:
            verdict["pinned"]["delta_Cp_minus_Ap_s"] = round(med_cp - med_ap, 4)
    evidence["verdict"] = verdict

    # ── live-LLM leg ─────────────────────────────────────────────────────
    if not args.skip_llm:
        print("[llm] live-provider leg...", flush=True)
        live = run_live_llm_leg(
            worktree=worktree,
            main_env_path=Path(args.main_env).resolve(),
            scratch=scratch,
        )
        if not live.get("passed"):
            print("[llm] live leg failed — running the mock fallback...", flush=True)
            live["mock_fallback"] = run_mock_llm_leg(worktree=worktree, scratch=scratch)
            live["disclosure"] = (
                "REAL-PROVIDER ACCEPTANCE REMAINS UNPROVEN — the live endpoint did "
                "not serve a parsable response; the mock leg proves the bound tool "
                "payload serialises and the request path executes."
            )
        evidence["llm_leg"] = live

    # ── write evidence ───────────────────────────────────────────────────
    evidence["summary_table"] = build_summary_table(evidence)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=False, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"evidence written: {out_path}", flush=True)
    else:
        print(json.dumps(evidence, indent=2, default=str)[:4000], flush=True)
    print(json.dumps(evidence.get("verdict", {}), indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
