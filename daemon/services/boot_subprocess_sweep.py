"""Boot-time stale-subprocess sweep (resource-cost-control O8a, 2026-10-18).

At daemon startup, detect and reap leftover children of PRIOR daemon
incarnations — the test-farm leak class demonstrated by the 2026-10-17
OOM night (3 kernel OOM deaths, daemon killed 23:19:14Z; the journal
carries the smoking gun: ``ensemble-main.service: Unit process 3393684
(MainThread) remains running after unit stopped`` — a child that
survived even systemd's own cgroup-wide SIGKILL sweep).

WHY A BOOT SWEEP (the gap it fills). A clean ``systemctl`` stop/restart
kills the whole unit cgroup (KillMode=mixed: TERM to the launcher, then
SIGKILL to remaining cgroup members after TimeoutStopSec=90). The kernel
OOM killer does NOT run that stop phase — it kills only the selected
(main, fattest) process. Two survivor windows follow:

* the OOM event itself, where systemd's reactive cgroup SIGKILL sweep
  can still leave survivors (observed: pid 3393684, 2026-10-17);
* every daemon start that is NOT a systemd stop/start pair — the
  launcher's in-life relaunch loop after a child crash (exit 137), dev
  and manual ``launcher.sh`` starts. There, no cgroup kill happens at
  all and orphans persist until the NEXT systemd-level restart — which
  is how a context7-mcp chain was observed running 4.2 days across
  incarnations (2026-10-17 recon).

The sweep runs inside the daemon at boot — on EVERY start path, systemd
or not — and closes exactly that window, bounded and logged.

PREDICATE (every clause required; fail-closed by construction). A
process is a stale candidate iff ALL hold:

1. member of THIS daemon's cgroup — exact cgroup-path equality with the
   daemon's own ``/proc/<pid>/cgroup`` entry (cgroup v2 unified path on
   current hosts; v1 ``name=systemd`` path compared best-effort). This
   clause structurally excludes pid 1, systemd, every other unit
   (``ensemble-demo.service``, getty, journald, ...) and user sessions —
   verified against the 2026-10-18 live census where the demo daemon and
   its MCP chain live in a sibling cgroup and are never eligible.
2. ``starttime`` (stat field 22, RAW TICKS — no HZ arithmetic needed for
   the comparison) strictly OLDER than this process's own starttime:
   the prior-incarnation oracle. Everything the current incarnation
   spawned is younger by construction.
3. NOT this process, NOT an ancestor of it, NOT a descendant of it. The
   ancestry clause is load-bearing: the launcher and the PyInstaller
   bootloader start seconds EARLIER than the daemon process inside the
   same cgroup (census: launcher −6s, bootloader −1s) and would
   otherwise be reaped into a systemd restart loop.
4. not a kernel thread (ppid 2 or empty cmdline) and not a zombie
   (state Z) — nothing to reap there.

PID-RECYCLE GUARD (the P2 guard: starttime field-22 + pgid, re-verified
IMMEDIATELY before every signal, plus a cgroup re-check). A pid that
exited and was recycled between enumeration and signaling mismatches on
at least one of the three identities and is SKIPPED — never signaled.
The guard re-runs before the TERM, and again before the KILL escalation
(a recycle can land inside the grace window).

ESCALATION. SIGTERM → bounded grace window → SIGKILL for survivors.
Processes still present after SIGKILL are logged and LEFT ALONE (a
D-state process holds pending signals in the kernel; spinning on them
at boot buys nothing). Every action is logged with pid, cmdline, age,
signal and result; a summary line closes the pass.

KNOBS (boot-time env, O1 convention; deliberately NOT app_settings —
this runs before config load and is boot-lane hygiene, not a managed
runtime key):

* ``ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES`` — kill-switch with lane-
  conditional default. The var **set** to ``0/false/no/off``
  (case-insensitive) disables in BOTH lanes (explicit operator
  override); the var **set** to anything else (including empty/
  whitespace — the operator exported it) enables in BOTH lanes; the
  var **absent** falls back to the lane default — prod lane
  (``is_prod_lane() == True``, the promoted frozen binary) is ON,
  dev/test/manual lane (``is_prod_lane() == False``, source-mode
  ``python -m daemon`` / ``uvicorn daemon.api:app`` / ``pytest`` /
  ``launcher.sh`` source runs) is OFF. The leader ruling of
  "ships default-ON at promote" is honored by leaving
  ``DEFAULT_ENABLED = True`` unchanged; the lane detector is what
  gates whether the default applies. OFF is a COMPLETE no-op: no
  /proc reads, no signals, no log noise beyond one INFO line.
* ``ENSEMBLE_BOOT_SWEEP_TERM_GRACE_SECONDS`` — TERM→KILL grace window;
  default 5.0, clamped to [0.5, 30.0]; parse failure falls back to the
  default.

FAIL-OPEN everywhere: any internal error is logged and a partial report
returned — boot hygiene must never block boot. Beyond the qualified
candidates, the module only ever READS /proc; it never writes anywhere
and never signals anything the predicate did not qualify.

LANE-CONDITIONAL DEFAULT (remediation r2, 2026-10-18). The default-ON
rule (leader ruling, see KNOBS) is honored only in the PROD lane — the
promoted frozen PyInstaller binary running under systemd. In dev/test/
manual lanes (source-mode ``python -m daemon``, ``launcher.sh`` source
runs, every ``pytest`` invocation), the sweep defaults to OFF. The
discriminator and the rationale are in :func:`is_prod_lane`; the
override rule (explicit ``ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES``
wins in BOTH directions in ALL lanes) is in :func:`kill_switch_from_env`.
The dev/test/manual default-OFF matters because (a) the dev/manual
``launcher.sh`` cgroup is the login-session scope, so the candidate
set over-broadens to same-session older non-lineage siblings (tmux
daemons, tunnels, htop) — collateral-kill breadth; (b) test lanes
unrelated to this module never patch the kill-switch env and would
otherwise scan real /proc on every ``main()`` call. The pin test
``test_default_is_on_pinned`` proves both sides; ``test_env_override_
wins_in_dev_test`` pins the override.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Knobs ──────────────────────────────────────────────────────────────────
ENV_KILL_SWITCH = "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"
ENV_TERM_GRACE_SECONDS = "ENSEMBLE_BOOT_SWEEP_TERM_GRACE_SECONDS"

# Default-ON is the leader ruling (2026-10-18): guarded daemon boot code,
# bounded + logged + revertible. The DEFAULT itself stays True; the
# lane-conditional application is in :func:`kill_switch_from_env` via
# :func:`is_prod_lane` — Pinned by tests (default-ON pin).
DEFAULT_ENABLED = True
DEFAULT_TERM_GRACE_SECONDS = 5.0
MIN_TERM_GRACE_SECONDS = 0.5
MAX_TERM_GRACE_SECONDS = 30.0

_DISABLE_VALUES = frozenset({"0", "false", "no", "off"})

# ── Enumeration bounds ─────────────────────────────────────────────────────
# pid-chain / tree walks are bounded so a hostile /proc shape can neither
# spin us nor exhaust memory; both are far above any real lineage depth.
_MAX_LINEAGE_DEPTH = 64


def is_prod_lane() -> bool:
    """True when running in the promoted frozen-binary PROD lane.

    Discriminator: ``getattr(sys, "frozen", False)`` — the canonical
    PyInstaller freeze attribute, set by the bootloader before any
    user code runs and available at module-import time (this function
    must be callable BEFORE config load — it gates the default for the
    sweep, which runs in ``daemon/__main__.main()`` BEFORE
    ``load_config()``).

    Why this discriminator, not ``ENSEMBLE_SELF_ENV``:

    * ``ENSEMBLE_SELF_ENV`` can land on ``"live"`` for a dev checkout
      when ``POSTGRES_DB=ensemble_prod`` is ambient on the box — the
      auto-derive branch (D-FA2.3) does that on purpose so the live
      gate sees ``live`` regardless of how the daemon was reached;
      that is the OPPOSITE of what the sweep needs. Auto-derive is an
      authoritative-detect for the lifecycle invariant; for THIS sweep
      it would be false-positive-ON.
    * systemd ``INVOCATION_ID`` (env) is only set when the daemon runs
      under ``systemd-run`` / ``systemd.service``; the dev-box and
      launcher.sh source runs do not have it, so it correctly
      classifies them as non-prod — but it is also absent in unrelated
      contexts (cron, manual ``python -m daemon`` from a non-systemd
      shell), making it brittle on the negative side and unbinding
      sys.frozen as the source of truth.
    * ``getattr(sys, "frozen", False)`` is the canonical idiom this
      codebase already uses at five sites to gate prod-only behavior:
      ``daemon/loader.py:445``, ``daemon/manager.py:3542``,
      ``daemon/registry.py:1486``, ``run_app.py:9``, and
      ``daemon/tools/upgrade_tools.py:475,668``. Source-mode runs
      (``python -m daemon``, ``uvicorn daemon.api:app``, ``pytest``,
      ``launcher.sh`` source runs) resolve False; the promoted
      PyInstaller binary resolves True. Reusing the established idiom
      is the cheapest and most review-stable choice.

    Tests pin both sides of the discriminator through the explicit
    ``is_prod=`` kwarg on :func:`kill_switch_from_env` (the function
    defers to this helper when ``is_prod`` is not supplied).
    """
    return bool(getattr(sys, "frozen", False))


def kill_switch_from_env(
    environ: dict | None = None,
    *,
    is_prod: bool | None = None,
) -> bool:
    """Resolve the kill-switch from ``ENV_KILL_SWITCH`` + lane detector.

    Semantics (remediation r2, 2026-10-18; see KNOBS section in the
    module docstring for the operator-facing summary):

    * ``ENV_KILL_SWITCH`` **set** (any value, including empty or
      whitespace-only — the operator went to the trouble of exporting
      the var) → **explicit operator override**: a stripped-lower
      value in ``_DISABLE_VALUES`` (``"0"``/``"false"``/``"no"``/
      ``"off"``) returns OFF; **every other value (incl. empty after
      strip) returns ON**. The override applies in BOTH lanes in
      BOTH directions (B-side / override pin: ``test_env_override_
      wins_in_dev_test``).
    * ``ENV_KILL_SWITCH`` **absent** (no key in the env, or ``None``
      from ``os.environ.get``) → lane-conditional default: **ON** in
      the prod lane (``is_prod_lane()`` truthy — the promoted frozen
      PyInstaller binary under systemd), **OFF** in dev/test/manual
      (``is_prod_lane()`` falsy — source-mode ``python -m daemon``,
      launcher.sh source runs, every ``pytest`` invocation).
      ``is_prod`` defaults to :func:`is_prod_lane` when None; tests
      can pin either lane via the explicit kwarg without depending
      on how pytest was launched.

    OFF is a complete no-op (the caller returns immediately, no /proc
    reads, no signals).
    """
    env = os.environ if environ is None else environ
    val = env.get(ENV_KILL_SWITCH)
    if val is not None:
        # Explicit env override — independent of lane.
        raw = val.strip().lower()
        if not raw:
            return True  # operator set the var to empty/whitespace: ON
        return raw not in _DISABLE_VALUES
    # Env key absent — lane-conditional default.
    if is_prod is None:
        is_prod = is_prod_lane()
    return DEFAULT_ENABLED if is_prod else False


def term_grace_seconds_from_env(environ: dict | None = None) -> float:
    """Resolve the TERM→KILL grace window from ``ENV_TERM_GRACE_SECONDS``.

    Parse failure or out-of-range values fall back to / clamp onto the
    bounded default — the grace window is a latency bound on boot, never
    an operator DoS on it.
    """
    env = os.environ if environ is None else environ
    raw = (env.get(ENV_TERM_GRACE_SECONDS) or "").strip()
    if not raw:
        return DEFAULT_TERM_GRACE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TERM_GRACE_SECONDS
    return max(MIN_TERM_GRACE_SECONDS, min(MAX_TERM_GRACE_SECONDS, value))


# ── /proc reading (READ-ONLY) ──────────────────────────────────────────────

def _read_bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _read_proc_stat(proc_root: str, pid: int) -> dict | None:
    """Parse ``/proc/<pid>/stat`` into the fields the sweep needs.

    Layout: ``pid (comm) state ppid pgrp session ... starttime``. comm may
    contain spaces/parens — split on the LAST ``)``; after it, field 3 is
    index 0 and field 22 (starttime) is index 19. Returns None when the
    entry vanished mid-read or does not parse (treated as "gone" — the
    caller skips, never guesses).
    """
    raw = _read_bytes(f"{proc_root}/{pid}/stat")
    if not raw:
        return None
    try:
        text = raw.decode("utf-8", "replace")
        close = text.rindex(")")
        rest = text[close + 1 :].split()
        return {
            "state": rest[0],
            "ppid": int(rest[1]),
            "pgrp": int(rest[2]),
            "session": int(rest[3]),
            "starttime": int(rest[19]),  # field 22, raw clock ticks
            "comm": text[text.index("(") + 1 : close],
        }
    except (ValueError, IndexError):
        return None


def _read_proc_cmdline(proc_root: str, pid: int) -> str:
    raw = _read_bytes(f"{proc_root}/{pid}/cmdline")
    if not raw:
        return ""
    return raw.decode("utf-8", "replace").replace("\x00", " ").strip()


def _read_proc_cgroup(proc_root: str, pid: int) -> str | None:
    """Identity path from ``/proc/<pid>/cgroup`` (last entry's path).

    cgroup v2 renders one ``0::/path`` line; v1's trailing ``name=systemd``
    (or v2) line still yields the path as the third ``:``-field. Exact
    string equality against the daemon's own value is the membership test.
    """
    raw = _read_bytes(f"{proc_root}/{pid}/cgroup")
    if not raw:
        return None
    lines = [ln for ln in raw.decode("utf-8", "replace").splitlines() if ln.strip()]
    if not lines:
        return None
    parts = lines[-1].split(":", 2)
    if len(parts) < 3:
        return None
    return parts[2]


def _boot_epoch_seconds(proc_root: str) -> float | None:
    """``btime`` from ``/proc/stat`` — wall-clock base for age reporting."""
    raw = _read_bytes(f"{proc_root}/stat")
    if not raw:
        return None
    for line in raw.decode("utf-8", "replace").splitlines():
        if line.startswith("btime"):
            try:
                return float(line.split()[1])
            except (ValueError, IndexError):
                return None
    return None


# ── Report shapes ──────────────────────────────────────────────────────────

@dataclass
class SweepCandidate:
    pid: int
    starttime: int
    pgrp: int
    cgroup: str
    cmdline: str
    age_seconds: float | None


@dataclass
class SweepAction:
    pid: int
    phase: str          # "term" | "kill"
    signal_name: str    # "SIGTERM" | "SIGKILL"
    result: str         # "signaled" | "vanished" | "guard_skip" | "error:<detail>"
    detail: str = ""


@dataclass
class SweepReport:
    enabled: bool = True
    scanned: int = 0
    candidates: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    errors: list = field(default_factory=list)


# ── The sweep ──────────────────────────────────────────────────────────────

def sweep_stale_subprocesses(
    *,
    proc_root: str = "/proc",
    daemon_pid: int | None = None,
    grace_seconds: float | None = None,
    kill_switch: bool | None = None,
    signal_fn=None,
    sleep_fn=None,
) -> SweepReport:
    """Detect and reap children orphaned by prior daemon incarnations.

    See the module docstring for the predicate, the PID-recycle guard and
    the knobs. All arguments are injection seams for tests: ``proc_root``
    points at a synthetic /proc-style tree, ``signal_fn``/``sleep_fn``
    replace :func:`os.kill`/:func:`time.sleep`, and ``kill_switch`` /
    ``grace_seconds`` override the env-resolved knobs (explicit None =
    resolve from env with default-ON semantics).

    Never raises: internal failures are logged and surfaced in the report
    (``errors``) — boot hygiene fails open.
    """
    report = SweepReport()
    log = logger

    # Kill-switch: explicit argument wins; otherwise env with default ON.
    enabled = kill_switch_from_env() if kill_switch is None else kill_switch
    report.enabled = enabled
    if not enabled:
        # Complete no-op: no /proc reads, no signals. The one INFO line is
        # the revertibility breadcrumb for operators.
        log.info(
            "Boot stale-subprocess sweep: disabled via %s — no-op",
            ENV_KILL_SWITCH,
        )
        return report

    _signal = signal_fn if signal_fn is not None else os.kill
    _sleep = sleep_fn if sleep_fn is not None else time.sleep
    grace = (
        term_grace_seconds_from_env() if grace_seconds is None else float(grace_seconds)
    )
    grace = max(MIN_TERM_GRACE_SECONDS, min(MAX_TERM_GRACE_SECONDS, grace))

    try:
        me = daemon_pid if daemon_pid is not None else os.getpid()
        my_stat = _read_proc_stat(proc_root, me)
        if my_stat is None:
            report.errors.append(f"own stat unreadable at {proc_root}/{me} — aborting")
            log.warning("Boot stale-subprocess sweep: own stat unreadable — aborting")
            return report
        my_cgroup = _read_proc_cgroup(proc_root, me)
        if not my_cgroup:
            report.errors.append("own cgroup unreadable — aborting (fail-closed)")
            log.warning(
                "Boot stale-subprocess sweep: own cgroup unreadable — aborting"
            )
            return report
        my_start = my_stat["starttime"]

        btime = _boot_epoch_seconds(proc_root)
        hz = os.sysconf("SC_CLK_TCK") or 100

        # Ancestry (launcher + PyInstaller bootloader start EARLIER inside
        # the same cgroup — must never be targets) and descendants (own
        # live tree) — both excluded structurally.
        stats: dict[int, dict] = {}
        children: dict[int, list[int]] = {}
        for name in os.listdir(proc_root):
            if not name.isdigit():
                continue
            pid = int(name)
            st = _read_proc_stat(proc_root, pid)
            if st is None:
                continue  # vanished mid-walk — skip, never guess
            stats[pid] = st
            children.setdefault(st["ppid"], []).append(pid)

        ancestors: set[int] = set()
        cursor = stats.get(me, {}).get("ppid")
        depth = 0
        while cursor and cursor > 1 and depth < _MAX_LINEAGE_DEPTH:
            ancestors.add(cursor)
            nxt = stats.get(cursor, {}).get("ppid")
            if nxt == cursor:  # cycle guard
                break
            cursor = nxt
            depth += 1

        descendants: set[int] = set()
        stack = list(children.get(me, []))
        depth = 0
        while stack and depth < _MAX_LINEAGE_DEPTH * 8:
            pid = stack.pop()
            if pid in descendants:
                continue
            descendants.add(pid)
            stack.extend(children.get(pid, []))
            depth += 1

        # Candidate selection — every clause required (module docstring).
        report.scanned = len(stats)
        for pid, st in sorted(stats.items()):
            if pid <= 1 or pid == me:
                continue
            if pid in ancestors or pid in descendants:
                continue
            if st["ppid"] == 2:  # kthreadd child = kernel thread
                continue
            cmdline = _read_proc_cmdline(proc_root, pid)
            if not cmdline:
                continue  # kernel threads / nothing addressable
            if st["state"] == "Z":
                continue  # zombie: already dead, nothing to reap
            cgroup = _read_proc_cgroup(proc_root, pid)
            if cgroup != my_cgroup:
                continue  # not a member of THIS unit's cgroup
            if st["starttime"] >= my_start:
                continue  # not older than this incarnation
            age = None
            if btime is not None:
                age = max(0.0, time.time() - (btime + st["starttime"] / hz))
            report.candidates.append(
                SweepCandidate(
                    pid=pid,
                    starttime=st["starttime"],
                    pgrp=st["pgrp"],
                    cgroup=cgroup,
                    cmdline=cmdline[:200],
                    age_seconds=age,
                )
            )

        if not report.candidates:
            log.info(
                "Boot stale-subprocess sweep: no candidates "
                "(scanned=%d, cgroup=%s)", report.scanned, my_cgroup,
            )
            return report

        def _identity_ok(cand: SweepCandidate) -> str | None:
            """PID-recycle guard: re-verify (starttime, pgrp, cgroup).

            Returns None when the identity still matches the enumeration
            snapshot, else a short skip reason. Runs IMMEDIATELY before
            every signal — a recycled pid must never be reaped.
            """
            st = _read_proc_stat(proc_root, cand.pid)
            if st is None:
                return "vanished"
            if st["starttime"] != cand.starttime:
                return (
                    f"starttime_mismatch(enum={cand.starttime},"
                    f"now={st['starttime']})"
                )
            if st["pgrp"] != cand.pgrp:
                return f"pgrp_mismatch(enum={cand.pgrp},now={st['pgrp']})"
            cgroup = _read_proc_cgroup(proc_root, cand.pid)
            if cgroup != cand.cgroup:
                return "cgroup_mismatch"
            return None

        age_fmt = (
            lambda c: "unknown" if c.age_seconds is None else f"{c.age_seconds:.0f}s"
        )

        # Phase 1 — SIGTERM (graceful) under the recycle guard.
        for cand in report.candidates:
            reason = _identity_ok(cand)
            if reason:
                report.actions.append(
                    SweepAction(cand.pid, "term", "SIGTERM", "guard_skip", reason)
                )
                log.warning(
                    "Boot sweep: SKIP pid=%d (%s guard: %s)",
                    cand.pid, reason.split("(")[0], reason,
                )
                continue
            try:
                _signal(cand.pid, signal.SIGTERM)
                report.actions.append(
                    SweepAction(cand.pid, "term", "SIGTERM", "signaled")
                )
                log.warning(
                    "Boot sweep: SIGTERM stale child pid=%d age=%s "
                    "cmdline='%s' cgroup=%s (prior-incarnation orphan)",
                    cand.pid, age_fmt(cand), cand.cmdline, cand.cgroup,
                )
            except ProcessLookupError:
                report.actions.append(
                    SweepAction(cand.pid, "term", "SIGTERM", "vanished")
                )
            except PermissionError as exc:
                report.actions.append(
                    SweepAction(cand.pid, "term", "SIGTERM", f"error:{exc}")
                )
                log.warning(
                    "Boot sweep: SIGTERM pid=%d failed (permission): %s",
                    cand.pid, exc,
                )

        # Bounded grace window, then Phase 2 — SIGKILL for survivors
        # (guard re-applied: a recycle can land inside the window).
        _sleep(grace)
        for cand in report.candidates:
            termed = any(
                a.pid == cand.pid and a.phase == "term" and a.result == "signaled"
                for a in report.actions
            )
            if not termed:
                continue
            reason = _identity_ok(cand)
            if reason:
                report.actions.append(
                    SweepAction(cand.pid, "kill", "SIGKILL", "guard_skip", reason)
                )
                log.warning(
                    "Boot sweep: KILL phase skip pid=%d (guard: %s)",
                    cand.pid, reason,
                )
                continue
            try:
                _signal(cand.pid, signal.SIGKILL)
                report.actions.append(
                    SweepAction(cand.pid, "kill", "SIGKILL", "signaled")
                )
                log.warning(
                    "Boot sweep: SIGKILL stale child pid=%d "
                    "(survived %s grace) cmdline='%s'",
                    cand.pid, grace, cand.cmdline,
                )
            except ProcessLookupError:
                report.actions.append(
                    SweepAction(cand.pid, "kill", "SIGKILL", "vanished")
                )
            except PermissionError as exc:
                report.actions.append(
                    SweepAction(cand.pid, "kill", "SIGKILL", f"error:{exc}")
                )
                log.warning(
                    "Boot sweep: SIGKILL pid=%d failed (permission): %s",
                    cand.pid, exc,
                )

        # Honest tail: anything still present after SIGKILL is reported,
        # never spun on (D-state processes hold signals in-kernel).
        for cand in report.candidates:
            if _read_proc_stat(proc_root, cand.pid) is not None:
                log.warning(
                    "Boot sweep: pid=%d still present after SIGKILL "
                    "(uninterruptible or re-parented late) — leaving it; "
                    "next systemd stop/restart reaps the cgroup",
                    cand.pid,
                )

        n_term = sum(
            1 for a in report.actions if a.phase == "term" and a.result == "signaled"
        )
        n_kill = sum(
            1 for a in report.actions if a.phase == "kill" and a.result == "signaled"
        )
        n_skip = sum(1 for a in report.actions if a.result == "guard_skip")
        log.warning(
            "Boot stale-subprocess sweep complete: scanned=%d candidates=%d "
            "term=%d kill=%d guard_skips=%d grace=%.1fs",
            report.scanned, len(report.candidates), n_term, n_kill, n_skip, grace,
        )
        return report
    except Exception as exc:  # fail-open — hygiene must never block boot
        report.errors.append(f"{type(exc).__name__}: {exc}")
        log.warning(
            "Boot stale-subprocess sweep: aborted (fail-open): %s: %s",
            type(exc).__name__, exc,
        )
        return report
