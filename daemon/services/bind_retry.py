"""Daemon-side bounded bind-retry for the main listener port.

WHY THIS EXISTS (2026-11-11 incident)
--------------------------------------
The live daemon restart-looped ~35x on 2026-11-11 10:30:53Z→11:55:28Z.
Every boot's uvicorn bind of 0.0.0.0:9797 raised ``[Errno 98] address
already in use`` because the PREVIOUS incarnation (an orphaned
grandchild of it) still held the LISTEN socket. uvicorn's own handler
for that OSError is ``logger.error(exc)`` → ``lifespan.shutdown()`` →
``sys.exit(1)`` (uvicorn/server.py:179-182) — a CRASH exit, which the
launcher respawns, so the loop was self-sustaining. It ended only when
systemd's cgroup cleanup SIGKILLed the stale holder.

A listener bind that fails with EADDRINUSE is TRANSIENT RESOURCE
CONTENTION, not a configuration error: the holder is a live process
that is on its way out. Waiting (bounded) is therefore the correct
response, and this module owns that wait.

THE BUDGET IS NOT SIZED TO COVER A WIND-DOWN (corrected 2026-11-11)
-------------------------------------------------------------------
The original brief sized the ceiling to "comfortably exceed the
port-hold envelope" (uvicorn drain <=60s, launcher CHILD_STOP_WAIT_S
=70, unit TimeoutStopSec=90, measured healthy shutdown 108s). That
premise was FALSIFIED by measurement:

* uvicorn closes the 9797 LISTENING socket as the FIRST statement of
  ``Server.shutdown()`` — before the <=60s drain and before the
  lifespan shutdown. The daemon's own teardown NEVER holds 9797.
* the launcher is strictly sequential (``"$bin" & wait "$CHILD_PID"``);
  across 63 measured cycles the previous child was already DEAD
  36.26s (min) / 68.24s (median) / 87.59s (mean) / 191.25s (max)
  BEFORE every bind attempt.

So there is NO legitimate "a wind-down is holding the port" window to
wait out. The budget's real job is to absorb a TRANSIENT holder — a
dying duplicate/rogue daemon (its SIGTERM releases the listener within
~1s, because uvicorn closes the listener FIRST; a systemd cgroup
SIGKILL reap releases it immediately) — WITHOUT converting a
crash-loop into a hang-loop. The default (30s) is ~30x the plausible
release latency of a dying holder and 2x the 16s backoff cap, and it
is env-tunable (DAEMON_BIND_RETRY_MAX_WAIT_SECONDS) so an operator
facing a slow-to-die holder can widen it; 0 restores the pre-fix
fail-fast behaviour.

THE BLAST-RADIUS WIN IS SEPARATE FROM THE WAIT
----------------------------------------------
Because the bind happens BEFORE the lifespan startup, a daemon that
cannot get the port performs ZERO lifespan startup steps — no
migrations, no MCP warm-up, no pool setup — instead of the full
startup plus the full ~11-step teardown that the old shape paid on
EVERY failed cycle. The wait's outcome is secondary; not thrashing
the live database 63 times is the material reduction.

CANONICAL ARITHMETIC (single source of truth)
----------------------------------------------
The burst-abort coupling (``6 crashes / 600 s ⇒ 310 + 5U ≤ 600 ⇒
U ≤ 58 s``, the measured frozen-path B ≈ 18.3 s breakdown, and the
ceiling) is documented ONCE, above ``BIND_RETRY_MAX_WAIT_CEILING_S``
in this module. ``daemon/config.py``'s field description and the test
pins reference it; they do not re-derive it.

ADR-STYLE NOTE — WHY THE PREFLIGHT AND THE BIND DIFFER
-------------------------------------------------------
``daemon/__main__.py`` deliberately does NOT retry the boot preflights
(ADR-011: "No in-process retry loop (plan m5) — one check, one
verdict"). The preflights decide CONFIG verdicts: exit 75 (PostgreSQL
unreachable) and exit 78 (auth refusal / auth misconfig). Those are
deterministic — a wrong password or an unreachable host does not heal
by waiting, so an in-process retry would only delay the supervisor's
verdict and burn wall-clock on a boot that cannot succeed.

The bind is the opposite failure class: the port is held by a process
that is on its way out, so waiting is exactly right. The two lanes are
deliberately DIFFERENT and must not be "harmonised" — a future reader
who sees one retry and one non-retry should NOT "fix" one to match the
other.

What this module does NOT do
-----------------------------
* It does not touch the exit-75 / exit-78 preflight verdicts — those
  are decided BEFORE the bind and stay decided before it.
* It does not change the exit code. Exhaustion exits **1** — the same
  code, from the same logical site, that uvicorn's failed-bind path
  used. A NEW exit code would land in the launcher's ``*→crash``
  track and would need a paired ``classify_exit`` change in
  launcher.sh (dragging the manifest ``launcher_sha256`` scalar with
  it) — out of scope for this lane.
* It does not change the 9798 MetricsListener behaviour. That
  listener's non-fatal degrade is by design (see
  ``daemon/services/metrics_listener.py``).
"""

import errno
import logging
import math
import socket
import sys
from time import monotonic, sleep

logger = logging.getLogger(__name__)

# ── Retry policy (module-owned) ───────────────────────────────────────────────
# Module-level constants, NOT in daemon/constants.py: that module is SEALED
# (content-sha256 pins in tests/test_chart_image_delivery_e2e.py's
# SEALED_SHA_BASELINES + the source-gc post-merge history pin in
# tests/unit/repositories/test_source_gc_repository.py), so it cannot host
# new constants without a leader-gated re-pin ceremony. This placement also
# matches the house retry pattern — daemon/services/vscode_server_manager.py
# keeps its restart policy in-module ("Module-level constants ... matches the
# existing module-level style").
#
# The USER-TUNABLE ceiling lives in DaemonConfig
# (``bind_retry_max_wait_seconds`` / env DAEMON_BIND_RETRY_MAX_WAIT_SECONDS);
# the value below is its default and the fallback when no ceiling is passed.
#
# BUDGET RATIONALE (corrected 2026-11-11 — the earlier "cover the wind-down
# envelope" premise was FALSIFIED by measurement):
#   * uvicorn closes the 9797 LISTENING socket as the FIRST statement of
#     ``Server.shutdown()`` — BEFORE the <=60s drain and BEFORE the lifespan
#     shutdown. The daemon's own teardown NEVER holds 9797.
#   * the launcher is STRICTLY SEQUENTIAL (``"$bin" & wait "$CHILD_PID"``);
#     across 63 measured cycles the previous child was already DEAD 36.26s
#     (min) / 68.24s (median) / 87.59s (mean) / 191.25s (max) BEFORE every
#     bind attempt.
#   => there is NO legitimate "a wind-down is holding the port" window to
#      wait out. The budget's real job is to absorb a TRANSIENT holder (a
#      dying duplicate/rogue daemon: SIGTERM releases the listener within
#      ~1s because uvicorn closes it FIRST; a systemd cgroup SIGKILL reap
#      releases it immediately) WITHOUT turning a crash-loop into a
#      hang-loop.
#   => 30s is ~30x the plausible release latency of a dying holder and 2x
#      the 16s backoff cap, while staying bounded so a genuinely rogue
#      holder still produces a clean exit-1 signal.
#
# COUPLING TO THE LAUNCHER'S BURST ABORT (the ceiling below is NOT a taste
# choice — it keeps the supervisor's only safety net REACHABLE):
#   launcher.sh:57-59 -> BUDGET_MAX_CRASHES=5 (abort at the 6th crash),
#   BUDGET_WINDOW_S=600 (sliding window), BUDGET_UPTIME_RESET_S=600.
#   launcher.sh:3624-3646 ``budget_tick()``: exit 75 is EXEMPT; a child
#   that lived >=600s RESETS the counter; a window older than 600s RESETS
#   it; the 6th crash in the window ABORTS (launcher.sh:3848-3851 → HALT,
#   exit 1, staying down).
#   The 5 waits between 6 crashes are 10+20+40+80+160 = 310s, and the
#   window spans the 5 UPTIMES of crashes #2..#6 => the abort is reachable
#   only while 310 + 5U <= 600, i.e. U <= 58s (U = the failed child's
#   uptime). In the 2026-11-11 incident U was 87-95s (boot-to-bind ~29s +
#   post-fatal teardown ~64s) => 310 + 5U > 600 => the counter reset 13
#   times => the unbounded 2h38m churn.
#   With the pre-bind, a blocked child exits after the BUDGET (no lifespan
#   startup, no teardown) => U ~= boot-to-prebind + budget. THE CEILING IS
#   SET EQUAL TO THE DEFAULT (30.0) so the invariant holds UNCONDITIONALLY:
#   on a FROZEN install the measured boot-to-prebind B is ~18.3s worst-case
#   (import + load_config ~3.2s + the boot sweep's TERM grace window 5.0s,
#   daemon/services/boot_subprocess_sweep.py:128 DEFAULT_TERM_GRACE_SECONDS,
#   once any candidate exists + the DB preflight <=10s, BOOT_DB_TIMEOUT_S),
#   so U ~= 30 + 18.3 = 48.3s <= 58s — reachable with margin. A ceiling of
#   45s would give B_max = 58 - 45 = 13s < 18.3s => U ~= 63.3s => the abort
#   becomes UNREACHABLE and the supervisor's safety net goes dark again.
#   Raising the ceiling is therefore forbidden by the clamp, not by policy.
BIND_RETRY_MAX_WAIT_S: float = 30.0  # DEFAULT effective budget (seconds) — see the coupling note
BIND_RETRY_MAX_WAIT_CEILING_S: float = 30.0  # HARD ceiling; equal to the default on purpose — see the coupling note
BIND_RETRY_INITIAL_BACKOFF_S: float = 1.0  # First wait between bind attempts
BIND_RETRY_MAX_BACKOFF_S: float = 16.0  # Backoff cap (ladder: 1,2,4,8,16,16...)
BIND_RETRY_MAX_ATTEMPTS: int = 20  # Hard attempt cap (backstop for a frozen clock)

# Exit code for "the listener could not be bound". IDENTICAL to
# uvicorn's own crash exit on a failed bind (uvicorn/server.py:179-182:
# ``logger.error(exc)`` → ``await self.lifespan.shutdown()`` →
# ``sys.exit(1)``). Same code, same logical site — see the module
# docstring for why a new code is NOT introduced here.
BIND_FAILURE_EXIT_CODE = 1


def resolve_max_wait_s(value: object) -> float:
    """Resolve the EFFECTIVE bind-retry budget from a requested value.

    Every departure from the requested value is ANNOUNCED (the operator
    must never be surprised by the effective budget):

    * ``None`` (nothing requested) → the module default.
    * a real number → clamped to ``[0, BIND_RETRY_MAX_WAIT_CEILING_S]``.
      A request ABOVE the ceiling is CLAMPED with a WARNING naming the
      requested vs the effective value and the reason: the ceiling keeps
      the launcher's burst abort reachable (6 crashes in the 600s window
      need ``310 + 5U <= 600``, i.e. the failed child's uptime
      ``U <= 58s``; ``U ~= boot-to-prebind + budget``). A silently
      accepted larger budget would push U back over 58s and switch the
      supervisor's safety net off again.
    * ``0`` → fail-fast (one attempt, no wait) — the pre-fix behaviour.
    * a NEGATIVE or MALFORMED value (non-numeric, NaN, infinite, or not a
      number at all) → fail CLOSED to fail-fast (0) with a WARNING. A
      budget we cannot understand must never silently become a long wait.
    """
    if value is None:
        return BIND_RETRY_MAX_WAIT_S
    # Only real scalars are accepted. A MOCK attribute (tests that build
    # ``MagicMock(spec=DaemonConfig)``) is NOT a number — and
    # ``float(MagicMock())`` silently yields 1.0, which would shrink the
    # budget to one second. Reject anything that is not a real scalar.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        logger.warning(
            "Bind retry: non-numeric budget %r — failing fast (0s, the "
            "pre-fix behaviour)",
            value,
        )
        return 0.0
    try:
        requested = float(value)
    except (TypeError, ValueError):
        logger.warning(
            "Bind retry: malformed budget %r — failing fast (0s, the "
            "pre-fix behaviour)",
            value,
        )
        return 0.0
    if not math.isfinite(requested):
        logger.warning(
            "Bind retry: non-finite budget %r — failing fast (0s, the "
            "pre-fix behaviour)",
            value,
        )
        return 0.0
    if requested < 0:
        logger.warning(
            "Bind retry: negative budget %.1fs — failing fast (0s, the "
            "pre-fix behaviour)",
            requested,
        )
        return 0.0
    if requested > BIND_RETRY_MAX_WAIT_CEILING_S:
        logger.warning(
            "Bind retry: BUDGET_CEILING_CLAMP — requested budget %.1fs "
            "EXCEEDS the %.1fs ceiling (clamped to %.1fs). The ceiling "
            "keeps the launcher's burst abort reachable: 6 crashes in the "
            "600s window need 310 + 5U <= 600 (the failed child's uptime "
            "U must stay <= 58s, and U ~= boot-to-prebind + budget).",
            requested,
            BIND_RETRY_MAX_WAIT_CEILING_S,
            BIND_RETRY_MAX_WAIT_CEILING_S,
        )
        return BIND_RETRY_MAX_WAIT_CEILING_S
    return requested


def _listener_family(host: str) -> int:
    """Mirror uvicorn's family selection (``Config.bind_socket``).

    uvicorn 0.41.0, uvicorn/config.py:525-531::

        family = socket.AF_INET
        addr_format = "%s://%s:%d"
        if self.host and ":" in self.host:
            family = socket.AF_INET6
    """
    if host and ":" in host:
        return socket.AF_INET6
    return socket.AF_INET


def _new_listener_socket(host: str) -> socket.socket:
    """Create the listener socket with uvicorn's OWN socket options.

    Mirrors ``uvicorn.config.Config.bind_socket()`` exactly (uvicorn
    0.41.0, uvicorn/config.py:533-536)::

        sock = socket.socket(family=family)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    SO_REUSEADDR does NOT allow two LIVE LISTENERS on one port — it only
    relaxes the TIME_WAIT reuse rule. It is set here because uvicorn
    sets it, NOT because it would mask a conflict; the fact that the
    2026-11-11 bind still failed with EADDRINUSE while this option was
    in force is itself the proof that the holder was a LIVE process.
    (Measured caveat: SO_REUSEADDR DOES permit a second ``bind()`` while
    this socket is bound-but-not-listening — so a second process can
    bind the port, but only one of the two can ``listen()``; the loser
    fails at ``create_server`` → exit 1 → crash track → retry. The
    LISTEN-state contention — the actual incident shape — is handled
    correctly.)

    NO ``listen()`` HERE — deliberately. asyncio's
    ``loop.create_server(..., sock=sock)`` performs the ``listen()``
    itself (uvicorn/server.py:144 hands the socket to
    ``loop.create_server``). Binding WITHOUT listening keeps the socket
    in the same OBSERVABLE state as an unbound port: a connect attempt
    is answered with ECONNREFUSED (verified: SO_ACCEPTCONN == 0 on the
    bound socket; a connect raised ConnectionRefusedError errno 111).
    Calling ``listen()`` early would instead QUEUE the connection — a
    probe during the lifespan startup would hang instead of being
    refused, which would change what the promoter's livez/readyz gate
    observes.
    """
    sock = socket.socket(family=_listener_family(host))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    return sock


def bind_socket_with_retry(
    host: str,
    port: int,
    *,
    max_wait_s: float | None = None,
    initial_backoff_s: float = BIND_RETRY_INITIAL_BACKOFF_S,
    max_backoff_s: float = BIND_RETRY_MAX_BACKOFF_S,
    max_attempts: int = BIND_RETRY_MAX_ATTEMPTS,
) -> socket.socket:
    """Bind ``host:port``, waiting out transient EADDRINUSE contention.

    Returns the BOUND socket — the caller owns it (and must hand it to
    uvicorn, or close it). The socket is bound but NOT yet listening:
    uvicorn's ``loop.create_server(..., sock=sock)`` performs the
    ``listen()``. The hold closes the window in which the port can be
    SERVED by another process (only one process can ``listen()``; a
    second ``bind()`` is still possible under SO_REUSEADDR and loses at
    ``create_server`` — see ``_new_listener_socket``).

    On EADDRINUSE the call waits with an exponential backoff
    (1→2→4→8→16s, capped) until the port frees or the budget is
    exhausted. On any OTHER OSError it logs and exits with
    ``BIND_FAILURE_EXIT_CODE`` — the same verdict uvicorn gave.

    The budget is the EFFECTIVE one: ``resolve_max_wait_s`` clamps a
    request to ``BIND_RETRY_MAX_WAIT_CEILING_S`` (30s — the coupling that
    keeps the launcher's burst abort reachable) and fails a malformed or
    negative request closed to fail-fast. The wait ladder can never sleep
    past the remaining budget (``wait = min(backoff, remaining)``), and
    the attempt cap can only end the loop EARLIER, so the total wait is
    bounded by the effective budget.

    On budget exhaustion it exits with ``BIND_FAILURE_EXIT_CODE`` (1) —
    see the module docstring for why the code is unchanged.

    Logging contract: ONE warning line per attempt (errno, host, port,
    attempt number, elapsed wait) plus a final ERROR naming the total
    budget. The 2026-11-11 incident was a forensic black hole precisely
    because the failure was unobservable — a recurrence must be
    diagnosable from the log alone.
    """
    budget = resolve_max_wait_s(
        BIND_RETRY_MAX_WAIT_S if max_wait_s is None else max_wait_s
    )
    started = monotonic()
    deadline = started + budget
    backoff = initial_backoff_s
    attempt = 0

    while True:
        attempt += 1
        sock = _new_listener_socket(host)
        try:
            sock.bind((host, port))
        except OSError as exc:
            sock.close()
            if exc.errno != errno.EADDRINUSE:
                # Not contention — the same verdict uvicorn gave
                # (logger.error(exc) → sys.exit(1)). It is also NOT a
                # transient: retrying an EACCES/EADDRNOTAVAIL cannot fix
                # it, so this fails fast on the FIRST attempt.
                logger.error(exc)
                logger.error(
                    "Bind failed on %s:%s (errno %s) after %d attempt(s) — "
                    "not EADDRINUSE (not transient); exiting %d",
                    host,
                    port,
                    exc.errno,
                    attempt,
                    BIND_FAILURE_EXIT_CODE,
                )
                sys.exit(BIND_FAILURE_EXIT_CODE)

            now = monotonic()
            elapsed = now - started
            remaining = deadline - now
            if remaining <= 0 or attempt >= max_attempts:
                logger.error(
                    "Bind retry budget exhausted on %s:%s after %d attempt(s) "
                    "over %.1fs (total budget %.1fs, errno %d) — exiting %d",
                    host,
                    port,
                    attempt,
                    elapsed,
                    budget,
                    exc.errno,
                    BIND_FAILURE_EXIT_CODE,
                )
                sys.exit(BIND_FAILURE_EXIT_CODE)

            wait = min(backoff, remaining)
            logger.warning(
                "Bind retry %d/%d on %s:%s — errno %d (EADDRINUSE): %s; "
                "retrying in %.1fs (elapsed %.1fs of %.1fs budget)",
                attempt,
                max_attempts,
                host,
                port,
                exc.errno,
                exc,
                wait,
                elapsed,
                budget,
            )
            sleep(wait)
            backoff = min(backoff * 2, max_backoff_s)
            continue

        # uvicorn's bind_socket() marks the socket inheritable
        # (uvicorn/config.py:546) — mirrored for fidelity.
        sock.set_inheritable(True)
        elapsed = monotonic() - started
        if attempt > 1:
            # The wait actually absorbed a contention — worth a WARNING
            # (it is the only trace that another process held the
            # listener). The plain-success case is INFO-level.
            logger.warning(
                "Listener bind on %s:%s succeeded on attempt %d after %.1fs "
                "— the port was held by another process and the wait "
                "absorbed it",
                host,
                sock.getsockname()[1],
                attempt,
                elapsed,
            )
        logger.info(
            "Bound %s:%s on attempt %d after %.1fs",
            host,
            sock.getsockname()[1],
            attempt,
            elapsed,
        )
        return sock
