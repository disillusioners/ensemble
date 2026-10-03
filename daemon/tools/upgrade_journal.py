"""Journal write-side + lock protocol + executor spawn (P2.2 Dispatch B, T4/T5).

Python twin of ``scripts/upgrade/lib.sh``'s journal and lock discipline. The
PROTOCOL — not shared code — is the contract (D-FA5.1): every function here
mirrors its lib.sh counterpart exactly:

* ``journal_read``            — torn-safe read (empty/unparseable → torn).
* ``journal_write``           — ATOMIC: temp file in the same dir + fsync +
                                ``os.replace`` (≡ lib.sh temp + ``mv -f``).
                                A ``kill -9`` mid-write leaves the temp file
                                and the journal intact.
* ``journal_init``            — idempotent empty-journal creation (P2.1
                                schema only; Dispatch-B extensions are added
                                lazily by :func:`ensure_extensions`).
* ``journal_update_field``    — set one top-level field (dict-level
                                read-modify-write of the WHOLE document —
                                see the ADR-034 note below).
* ``journal_history_append``  — newest-last history append.
* ``lock_acquire/release/heartbeat`` — the ``rollback.lock.d`` mkdir-lock:
                                mkdir IS the acquire; ``owner``/``run_id``/
                                ``heartbeat`` files; stale >300s + dead owner
                                → ``mv`` to ``rollback.lock.stale.<pid>`` →
                                re-acquire; owner-dead breaks a fresh
                                heartbeat dir too (lib.sh mirror, both
                                branches). Ownership-guarded heartbeat and
                                release.
* :class:`PendingOp`          — the D-FA1.1 journaled contract record.
* nonce mint/store/verify     — D-FA3.3 ``pending_actions`` keyed by run_id.

ADR-034 BINDING (splice escape discipline — do NOT tighten):
    lib.sh ``journal_update`` splices TEXTUALLY at the LAST occurrence of a
    field name and deliberately tolerates a field name occurring ≥2 times in
    the document (a divergence only synthesizable by hand-edit — e.g. a
    history ``detail`` string containing the word ``in_flight``). This
    module round-trips the document STRUCTURALLY (``json.loads`` → dict →
    mutate → ``json.dumps``): unknown extra fields are carried through, and
    duplicated keys normalize LAST-WINS under ``json.loads`` — the same
    field lib.sh's last-occurrence splice targets — so the tolerance is
    preserved by construction, and NO occurrence-counting assertion exists
    anywhere here. A single-occurrence assert is P2.3 hardening territory
    (ADR-034: ≥2 is the deliberate safety margin against false-positive
    torn writes) — it must NOT be added.

Extensions this module may add to ``releases/state.json`` (all additive,
all written with the same atomic discipline — lib.sh ``journal_update`` on
the P2.1 fields keeps working because its own fields are never removed):

* ``pending_op``      — null | D-FA1.1 record (ONE op at a time).
* ``pending_restart`` — null | "<run_id>" (phase2-plan D2 restart marker).
* ``pending_actions`` — {} | {"<run_id>": {nonce record}} (D-FA3.3).

Terminal-class alert emission (P2.3 B3/T8a): a module-level sink registry
(:func:`register_alert_sink` — default no-op, last-wins; the daemon boots
the real SSE sink via :func:`broadcaster_alert_sink`) + emission hooks in
:func:`journal_history_append` for the 3 alert kinds (cap-halt / promote
refusal / auto-rollback). Best-effort, never-raises, terminal-class only —
see the ALERT EVENT SET note at the emission section for the full
contract (incl. why launcher burst-abort is deliberately NOT an SSE kind).

Executor spawn (D-FA1.3 / D4):
    :func:`spawn_executor` runs the payload via ``subprocess.Popen(...,
    start_new_session=True)`` (≡ double-fork + ``setsid`` on macOS and
    under PyInstaller — assumption #2 of the pre-freeze checklist, verified
    by the Dispatch-B sandbox drill). The child is deliberately NOT
    registered in ``BashProcessRegistry`` (or anywhere else): the tool
    harness's SIGTERM teardown must never reach it. stdio →
    ``<install_dir>/data/upgrade.log``; env is ALLOWLISTED (R-SR09 — no
    ``.env`` passthrough, no API keys).

L8 (tidier, P2.3 final batch): this module is deliberately large — the
module split (journal / lock / nonce / alert) is fenced to the post-P2.3
refactor pass (decisions.md "P2.3 Gate Rulings & Fences" item 2); do not
grow it further without pulling that fence.

Size rationale: ~2017 lines is the protocol twin of scripts/upgrade/lib.sh
supervision section + the journal/lock/nonce/alert/spawn tool surface —
the single journal protocol home. Splitting the protocol twin from the
tool surface would force every contract change to touch two modules.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys  # r-f82e cycle 0 detector bug fix: bare `sys.platform` reference
            # in _scope_detect_real raised NameError on every call, silently
            # swallowed by the broad `except Exception:` in the detector —
            # the detector ALWAYS returned (False, "") regardless of host.
            # cycle 1 fixback: this import is REQUIRED for Item 1(a) — the
            # env= kwarg forwarded to subprocess.run is meaningless if the
            # probe never runs. (Pre-existing; cycle 0 reviewer caught this
            # only as a minor, but it blocked pin 6a outright.)
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, NamedTuple

from daemon.constants import is_reserved_source

logger = logging.getLogger(__name__)

# ── Constants (mirror scripts/upgrade/lib.sh — single source is lib.sh; a
# drift here is a protocol violation, not a config knob) ────────────────────
ROLLBACK_CAP_24H = 3            # lib.sh ROLLBACK_CAP_24H
LOCK_HEARTBEAT_REFRESH_S = 30   # lib.sh LOCK_HEARTBEAT_S
LOCK_STALE_S = 300              # lib.sh LOCK_STALE_S
NONCE_TTL_S = 60 * 60           # §4.3 nonce TTL (60min; widened from 15min per user decision 2026-09-30, ADR-036)
PENDING_OP_EXPIRE_RESTART_S = 30 * 60    # D-FA1.1 restart +30min
PENDING_OP_EXPIRE_PROMOTE_S = 10 * 60    # D-FA1.1 promote +10min outer window
RECONCILE_GRACE_S = 10 * 60     # grace past expires_at before a dead op is
                                # cleared as crashed-pre-open

JOURNAL_EMPTY: dict[str, Any] = {
    "current": None,
    "previous": None,
    "in_flight": None,
    "rollback_window_count": {"24h": 0, "window_start": None},
    "cooldown_until": None,
    "quarantined": [],
    "history": [],
}

# Nonce: CONFIRM- + 8 base32 chars (§4.3). The grouped rendering
# (CONFIRM-XXXX-XXXX, §2.1 example) is accepted as an equivalent echo —
# comparisons normalize internal dashes.
_NONCE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
NONCE_RE = re.compile(r"^CONFIRM-[A-Z2-7]{8}$")


def now_iso() -> str:
    """UTC ISO-8601 timestamp — journal fields are ISO (lib.sh ``_now_iso``)."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(ts: Any) -> datetime | None:
    """Parse ``YYYY-MM-DDTHH:MM:SSZ``; ``None`` on garbage (fail-closed)."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def iso_plus(ts: str, delta_s: int) -> str:
    """journal ISO + seconds → ISO (TTL/expiry arithmetic)."""
    base = parse_iso_utc(ts)
    if base is None:
        return now_iso()
    return (base + timedelta(seconds=delta_s)).strftime("%Y-%m-%dT%H:%M:%SZ")


def mint_run_id() -> str:
    """D-FA1.1 run id: ``r-<utcstamp>-<4hex>`` (cross-death join key)."""
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"r-{stamp}-{secrets.token_hex(2)}"


def mint_nonce() -> str:
    """§4.3 nonce: ``CONFIRM-`` + 8 base32 chars (secrets — unguessable)."""
    body = "".join(secrets.choice(_NONCE_ALPHABET) for _ in range(8))
    return f"CONFIRM-{body}"


def nonce_grouped(nonce: str) -> str:
    """The §2.1 grouped rendering ``CONFIRM-XXXX-XXXX`` (display parity)."""
    if NONCE_RE.match(nonce):
        return f"CONFIRM-{nonce[8:12]}-{nonce[12:16]}"
    return nonce


def nonce_normalize(value: str | None) -> str:
    """Normalize a nonce echo: uppercase, strip internal dashes/whitespace."""
    if not isinstance(value, str) or not value:
        return ""
    return re.sub(r"[-\s]", "", value.strip()).upper()


def nonce_equals(stored: str, echoed: str | None) -> bool:
    a = nonce_normalize(stored)
    b = nonce_normalize(echoed)
    return bool(a) and bool(b) and a == b


def nonce_in_content(stored_nonce: str, content: str | None) -> bool:
    """Does the triggering message content carry the nonce? Accepts the
    canonical ``CONFIRM-XXXXXXXX`` and the grouped ``CONFIRM-XXXX-XXXX``
    renderings (§2.1 example), dash- and whitespace-insensitively on both
    sides (a user's echo may drop or regroup the dashes)."""
    if not content or not stored_nonce:
        return False
    canonical = nonce_normalize(stored_nonce)
    grouped = nonce_grouped(stored_nonce)
    flat = re.sub(r"[-\s]+", "", content).upper()
    if canonical and canonical in flat:
        return True
    grouped_norm = nonce_normalize(grouped)
    if grouped_norm and grouped_norm in flat:
        return True
    return grouped.upper() in content.upper()


# ── Journal (torn-safe read / atomic write — lib.sh D4 discipline) ──────────


class JournalTorn(Exception):
    """``releases/state.json`` is empty/unparseable — mutations must refuse."""


def journal_path(install_dir: Path) -> Path:
    return install_dir / "releases" / "state.json"


def journal_read(install_dir: Path) -> dict[str, Any]:
    """Torn-safe read. Raises :class:`JournalTorn` when the journal is
    empty/unparseable (lib.sh ``journal_read`` exit-1 analogue — callers
    treat torn as halt-for-human, never trust, never write over it)."""
    jp = journal_path(install_dir)
    try:
        raw = jp.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise JournalTorn(f"journal absent at {jp}")
    except OSError as exc:
        raise JournalTorn(f"journal unreadable at {jp}: {exc}")
    if not raw.strip():
        raise JournalTorn(f"journal at {jp} is EMPTY (torn write?)")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise JournalTorn(f"journal at {jp} is unparseable (torn write?)")
    if not isinstance(data, dict):
        raise JournalTorn(f"journal at {jp} is not a JSON object")
    return data


def journal_init(install_dir: Path) -> None:
    """Create the empty P2.1 journal if absent (idempotent — lib.sh twin)."""
    jp = journal_path(install_dir)
    if jp.is_file():
        return
    jp.parent.mkdir(parents=True, exist_ok=True)
    journal_write(install_dir, dict(JOURNAL_EMPTY))


def journal_write(install_dir: Path, data: dict[str, Any]) -> None:
    """ATOMIC whole-document write: temp file in the same dir + fsync +
    ``os.replace`` (rename(2) semantics ≡ lib.sh temp + ``mv -f``). A crash
    mid-write leaves the temp and the previous journal intact.

    ADR-034: the document round-trips STRUCTURALLY — every existing field
    (including unknown/hand-edited extras) is carried through; duplicated
    keys normalize last-wins under ``json.loads``, the same field lib.sh's
    last-occurrence splice targets. No schema filtering, no occurrence
    assertions.
    """
    jp = journal_path(install_dir)
    jp.parent.mkdir(parents=True, exist_ok=True)
    # F-1 (P2.3 review cycle 2, MINOR — defense-in-depth): the tmp filename
    # is pid-based, so two threads in the SAME process collide. The seam is
    # currently unreachable (single-threaded callers today) but B6.5's sink
    # + future threaded callers make it live. Per-writer uniqueness via
    # threading.get_ident() + uuid.uuid4().hex[:8] — combined with the
    # existing pid + ms timestamp, a collision now requires four concurrent
    # writers all hitting the same millisecond with the same uuid prefix,
    # which is astronomically unlikely without the attacker controlling
    # both the clock and /dev/urandom. P2.3 caller discipline still
    # serializes journal writes externally; this is the inner ring.
    tmp = jp.with_name(
        f"{jp.name}.tmp.{os.getpid()}.{threading.get_ident()}."
        f"{uuid.uuid4().hex[:8]}.{int(time.time() * 1000)}"
    )
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, jp)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise OSError(f"journal write FAILED at {jp}: {exc}") from exc


def journal_update_field(install_dir: Path, field_name: str, value: Any) -> dict[str, Any]:
    """Read-modify-write ONE top-level field (atomic). Raises JournalTorn if
    the current journal is torn (mutations refuse — never write over a torn
    journal), KeyError when the field does not exist (mirror of lib.sh
    ``journal_update``'s "schema drift?" refusal — new fields go through
    :func:`ensure_extensions`)."""
    data = journal_read(install_dir)
    if field_name not in data:
        raise KeyError(
            f"journal_update_field: '{field_name}' not found (schema drift?) — "
            "new fields must be introduced via ensure_extensions"
        )
    data[field_name] = value
    journal_write(install_dir, data)
    return data


def journal_history_append(install_dir: Path, event: str, detail: str) -> None:
    """Append to ``history`` (newest last) — lib.sh ``journal_history_append``.

    P2.3 B3/T8a: a TERMINAL-CLASS event (see ``ALERT_KIND_BY_EVENT``)
    additionally emits a best-effort SSE alert AFTER the durable write —
    never-raises, sink absent = silent no-op (see the emission section
    below)."""
    data = journal_read(install_dir)
    history = data.get("history")
    if not isinstance(history, list):
        history = []
    ts = now_iso()
    history.append({"ts": ts, "event": event, "detail": detail})
    data["history"] = history
    journal_write(install_dir, data)
    _emit_terminal_class_alert(data, event, detail, ts)


def ensure_extensions(install_dir: Path) -> dict[str, Any]:
    """Add the Dispatch-B additive fields when absent (idempotent).

    Only ADDS — never removes, never rewrites an existing value. Existing
    P2.1 fields are untouched, so every lib.sh ``journal_update`` keeps
    working against the extended document.

    Post-Restart Arm-Notify (ADR-039): also ensures the
    ``pending_wakes`` key (default ``{}``) is present. The wake record
    rides this same helper so every consumer (Phase 2 sweep, future
    forensic tools) sees a structurally stable top-level shape.
    """
    data = journal_read(install_dir)
    changed = False
    if "pending_op" not in data:
        data["pending_op"] = None
        changed = True
    if "pending_restart" not in data:
        data["pending_restart"] = None
        changed = True
    if "pending_actions" not in data:
        data["pending_actions"] = {}
        changed = True
    if "pending_wakes" not in data:
        data["pending_wakes"] = {}
        changed = True
    if changed:
        journal_write(install_dir, data)
    return data


# ── Terminal-class alert emission (P2.3 B3 / T8a — D4 deterministic SSE) ────
#
# Alert EVENT SET (caller-ruled 2026-08-23 — exactly 3 SSE kinds):
#
#   ``upgrade_cap_halt``          journal ``halt`` history entry — the
#                                 halt-for-human record (rollback-cap
#                                 reached, gate-fail-with-no-rollback,
#                                 adopt halts …; the ledger checker's
#                                 violation class).
#   ``upgrade_promote_refusal``   journal ``refusal`` history entry —
#                                 entry-side refusals (cap/cooldown/lock/
#                                 quarantine/gate-refuse), reason token
#                                 parsed from the detail via the D-FA2.2
#                                 ``reason=<token>`` convention.
#   ``upgrade_auto_rollback``     journal ``rollback`` / ``sweep_rollback``
#                                 / ``quarantine`` history entries —
#                                 auto-rollback executed + its quarantine
#                                 stamp. promote.sh appends the rollback
#                                 entry BEFORE stamping quarantine, so
#                                 the quarantine-entry alert is the one
#                                 whose payload carries the COMPLETE
#                                 quarantine list.
#
# Deliberately NOT an SSE kind: launcher burst-abort. It is a
# daemon-down class — structurally undeliverable by SSE (the daemon is
# the thing that died). Covered instead by the B4 watchdog-watcher
# extension (ADR-025(b): file-watch of ``.launcher-state`` abort markers
# + journal halt/sweep events, readable without the daemon) + Ari's
# next-recovery relay from the journal.
#
# Emission contract:
#   * the hook fires in :func:`journal_history_append` AFTER the durable
#     write — the journal is PRIMARY, the alert best-effort;
#   * never-raises: the sink call is wrapped in ``except Exception``
#     (NEVER BaseException — the CancelledError project gotcha) with a
#     single log line; a failing/absent sink never breaks or delays a
#     journal write; sink absent (unit tests, scripts context) = silent
#     no-op;
#   * terminal-class ONLY (R3.4 anti-spam): ordinary entries (staged,
#     commit, restart, sweep, nonce_consumed, arm-class field writes,
#     idempotent re-stage, sweep-clear of non-rollback txns, adoption
#     without rollback) emit NOTHING; ONE alert per history EVENT (a
#     second refusal appends its own event → its own alert; never
#     duplicates within one event);
#   * payloads are derived FROM THE JOURNAL (promotion-ladder §3:
#     evidence-cited, not vibes) — counters, cooldown, quarantine list
#     and the in-flight run_id/txn handle are read from the
#     just-written document, and ``source_event`` names the journal
#     history event type;
#   * the SHELL-side journal writers (promote.sh / rollback.sh /
#     restart.sh via lib.sh) run daemonized OUTSIDE the daemon process —
#     their appends cannot hop this in-process sink; those surfaces are
#     covered by the same B4 watcher + relay channels. In-daemon
#     appends (this module and future Python writers) emit here.
#
# ADR-034: nothing below touches the splice's >=2-occurrence/escape
# discipline — emission is purely post-write observation.

ALERT_KIND_BY_EVENT: dict[str, str] = {
    "halt": "upgrade_cap_halt",
    "refusal": "upgrade_promote_refusal",
    "rollback": "upgrade_auto_rollback",
    "sweep_rollback": "upgrade_auto_rollback",
    "quarantine": "upgrade_auto_rollback",
}

# The registered sink (sync callable, receives the alert payload dict).
# Default: None = silent no-op. Plain module attribute (GIL-atomic
# last-wins assignment; the registry is deliberately single-slot).
_alert_sink: Callable[[dict[str, Any]], None] | None = None


def register_alert_sink(
    sink: Callable[[dict[str, Any]], None] | None,
) -> Callable[[dict[str, Any]], None] | None:
    """Register the terminal-class alert sink — LAST-WINS.

    The daemon registers the real sink once at boot (api lifespan, via
    :func:`broadcaster_alert_sink`); tests re-register freely and reset
    to the silent no-op default with ``register_alert_sink(None)``.
    Returns the PREVIOUS sink so callers can restore it. Idempotent:
    re-registering the same callable is a harmless self-replacement."""
    global _alert_sink
    previous = _alert_sink
    _alert_sink = sink
    return previous


def broadcaster_alert_sink(
    broadcaster: Any,
) -> Callable[[dict[str, Any]], None]:
    """Build the REAL daemon sink: publish alerts via the
    NotificationBroadcaster SSE service (daemon/services/
    notification_broadcaster.py — the module singleton wired in the api
    lifespan; registration lands there, ONE line at boot).

    MUST be built ON the running event loop (boot); the returned sync
    callable is then safe from ANY thread — loop thread (tool calls) and
    executor threads alike hop through ``loop.call_soon_threadsafe`` →
    ``asyncio.ensure_future(broadcaster.emit(...))``. A closed/shutdown
    loop drops the alert with one log line (best-effort — the journal
    write is already durable). Never raises into the journal caller."""
    loop = asyncio.get_running_loop()

    def sink(payload: dict[str, Any]) -> None:
        notification = {
            "event_type": str(payload.get("kind", "upgrade_alert")),
            "data": payload,
            "timestamp": payload.get("ts"),
        }

        def _dispatch() -> None:
            task = asyncio.ensure_future(broadcaster.emit(notification))
            task.add_done_callback(_log_emit_result)

        try:
            loop.call_soon_threadsafe(_dispatch)
        except RuntimeError:
            logger.warning(
                "upgrade_journal: alert loop closed — alert dropped "
                "(best-effort; journal already written): %s",
                payload.get("kind"),
            )

    return sink


def _log_emit_result(task: asyncio.Future[Any]) -> None:
    """Done-callback for the dispatched ``broadcaster.emit`` — a failed
    emit is ONE warning line (best-effort alert, journal unaffected)."""
    # M1 (tidier, P2.3 final batch): a CANCELLED task raises CancelledError
    # from task.exception() — inside a done-callback that escapes as loop
    # noise. Same family as the 2026-07-12 wait_for_result gotcha: check
    # cancelled FIRST (single warning), never consume the exception.
    if task.cancelled():
        logger.warning(
            "upgrade_journal: SSE alert emit CANCELLED (alert dropped; "
            "journal already written)"
        )
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(
            "upgrade_journal: SSE alert emit FAILED (alert dropped; "
            "journal already written): %s",
            exc,
        )


_REASON_TOKEN_RE = re.compile(r"\breason=([a-z0-9][a-z0-9-]*)")


def _reason_token(detail: str) -> str | None:
    """Parse the D-FA2.2 ``reason=<token>`` from a history detail, if any."""
    match = _REASON_TOKEN_RE.search(detail or "")
    return match.group(1) if match else None


def _emit_terminal_class_alert(
    data: dict[str, Any], event: str, detail: str, ts: str
) -> None:
    """Best-effort alert for a terminal-class history append.

    Called from :func:`journal_history_append` AFTER the durable write
    with the just-written document in hand (payload fields are read from
    it — journal-derived evidence). NEVER raises: a failing sink is
    logged with a single line and dropped; the journal write — already
    complete — is unaffected. Sink absent or ordinary (non-terminal)
    event: silent no-op."""
    sink = _alert_sink
    if sink is None:
        return
    kind = ALERT_KIND_BY_EVENT.get(event)
    if kind is None:
        return  # ordinary write — terminal-class only (R3.4)
    in_flight = data.get("in_flight")
    quarantined = data.get("quarantined")
    # M6 (tidier): the run_id conditional hoisted out of the payload dict —
    # a named local reads as what it is (None unless in_flight is an object).
    run_id = (
        in_flight.get("run_id") if isinstance(in_flight, dict) else None
    )
    payload = {
        "kind": kind,
        "source_event": event,
        "reason": _reason_token(detail),
        "detail": detail,
        "version": data.get("current"),
        "counters": data.get("rollback_window_count"),
        "cooldown_until": data.get("cooldown_until"),
        "quarantined": quarantined if isinstance(quarantined, list) else [],
        "run_id": run_id,
        "ts": ts,
    }
    try:
        sink(payload)
    except Exception as exc:  # NEVER BaseException (CancelledError gotcha)
        logger.warning(
            "upgrade_journal: alert sink FAILED (alert dropped; journal "
            "write unaffected): %s",
            exc,
        )


# ── rollback.lock.d — mkdir lock, the D-FA5.1 protocol ──────────────────────


def lock_dir(install_dir: Path) -> Path:
    return install_dir / "releases" / "rollback.lock.d"


def _pid_alive(pid: Any) -> bool:
    try:
        p = int(pid)
    except (TypeError, ValueError):
        return False  # missing/garbage owner pid = unverifiable
    if p <= 0:
        return False
    try:
        os.kill(p, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM → ALIVE — the process exists but belongs to another user;
        # never break a lock whose owner may be live; degrades to
        # pipeline-busy instead. lib.sh's _pid_alive now matches this
        # semantics (kill -0 EPERM → alive, P2.3 B3.5) — the former
        # deliberate divergence (EPERM → dead on the shell side) is
        # closed.
        return True  # exists but owned by another user


def _lock_read_file(lock: Path, name: str) -> str:
    try:
        return (lock / name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def lock_acquire(
    install_dir: Path,
    run_id: str,
    owner_pid: int | None = None,
    wait_s: float = 0.0,
) -> tuple[bool, str | None]:
    """Acquire the pipeline lock (mkdir IS the acquire). Returns
    ``(acquired, busy_run_id)`` — ``busy_run_id`` names the holder on a busy
    refusal (structured pipeline-busy, not a crash). Mirrors lib.sh
    ``lock_acquire`` including BOTH stale-break branches:

    * heartbeat older than LOCK_STALE_S **and** owner dead/unverifiable
      → ``mv`` the dir to ``rollback.lock.stale.<pid>`` → re-acquire (a
      LIVE owner's lock is NEVER broken on heartbeat age alone — the stop
      span is legitimately un-heartbeated up to 600s);
    * owner pid dead even with a fresh heartbeat (crash left a fresh dir)
      → break too.
    """
    owner_pid = os.getpid() if owner_pid is None else int(owner_pid)
    lock = lock_dir(install_dir)
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        try:
            lock.mkdir()
        except FileExistsError:
            pass
        else:
            (lock / "owner").write_text(f"{owner_pid}\n", encoding="utf-8")
            (lock / "run_id").write_text(f"{run_id}\n", encoding="utf-8")
            (lock / "heartbeat").write_text(f"{int(time.time())}\n", encoding="utf-8")
            return True, None

        hb = _lock_read_file(lock, "heartbeat")
        owner = _lock_read_file(lock, "owner")
        held_run = _lock_read_file(lock, "run_id") or None
        if hb.isdigit():
            age = int(time.time()) - int(hb)
            if age > LOCK_STALE_S and not _pid_alive(owner):
                stale = lock.with_name(f"{lock.name}.stale.{os.getpid()}")
                try:
                    shutil.move(str(lock), str(stale))
                    logger.info(
                        "upgrade_journal: pipeline lock stale (heartbeat %ss old, "
                        "owner pid %s dead/unverifiable, run %s) — breaking",
                        age, owner or "?", held_run or "?",
                    )
                    continue
                except OSError:
                    pass
        if owner and not _pid_alive(owner):
            stale = lock.with_name(f"{lock.name}.stale.{os.getpid()}")
            try:
                shutil.move(str(lock), str(stale))
                logger.info(
                    "upgrade_journal: pipeline lock owner pid %s is dead — breaking lock",
                    owner,
                )
                continue
            except OSError:
                pass
        if time.monotonic() >= deadline:
            return False, held_run
        time.sleep(1.0)


def lock_heartbeat(install_dir: Path, owner_pid: int | None = None) -> bool:
    """Refresh the heartbeat. OWNERSHIP-GUARDED (lib.sh B2b): a non-owner
    write would keep another process's lock alive — refuse instead."""
    owner_pid = os.getpid() if owner_pid is None else int(owner_pid)
    lock = lock_dir(install_dir)
    if not lock.is_dir():
        return False
    if _lock_read_file(lock, "owner") != str(owner_pid):
        return False
    try:
        (lock / "heartbeat").write_text(f"{int(time.time())}\n", encoding="utf-8")
        return True
    except OSError:
        return False


def lock_release(install_dir: Path, owner_pid: int | None = None) -> bool:
    """Remove the lock dir — only if we still own it (lib.sh ``lock_release``)."""
    owner_pid = os.getpid() if owner_pid is None else int(owner_pid)
    lock = lock_dir(install_dir)
    if not lock.is_dir():
        return True
    if _lock_read_file(lock, "owner") != str(owner_pid):
        logger.warning(
            "upgrade_journal: lock_release: lock owned by pid %s, not us — leaving it",
            _lock_read_file(lock, "owner") or "?",
        )
        return False
    shutil.rmtree(lock, ignore_errors=True)
    return True


def lock_run_id(install_dir: Path | None) -> str | None:
    """The active lock's run_id, if held (read-only).

    None-hardened (P2.2 review cycle-3, MAJOR-1): dev/unresolved envs have
    no staged install dir — ``None`` means "no lock to read", mirroring the
    None-hardened read helpers in upgrade_tools.py. Hardening here also
    covers the journal-side actor call sites, not just upgrade_status."""
    if install_dir is None:
        return None
    return _lock_read_file(lock_dir(install_dir), "run_id") or None


# ── pending_op (D-FA1.1 — the journaled contract) ───────────────────────────


@dataclass
class PendingOp:
    """The D-FA1.1 record — written BEFORE the tool returns, survives death."""

    run_id: str
    kind: str                    # "restart" | "promote"
    env: str
    target: str | None = None    # null for restart
    mode: str | None = None      # "graceful-now" (restart only)
    reason: str = ""
    armed_at: str = field(default_factory=now_iso)
    armed_by_instance: str = ""
    owner_pid: int = 0
    owner_kind: str = "tool-arm"  # tool-arm | executor
    owner_heartbeat_at: str | None = None
    trigger: str = "post-turn-callback"  # post-turn-callback | manual
    nonce: str | None = None
    nonce_consumed: bool = False
    confirmed_by_human: bool = False
    confirmed_source: str | None = None
    flipped: bool = False
    expires_at: str = field(default_factory=now_iso)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Any) -> PendingOp | None:
        if not isinstance(data, dict) or not data.get("run_id"):
            return None
        kwargs = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        try:
            return cls(**kwargs)
        except TypeError:
            return None


def write_pending_op(install_dir: Path, op: PendingOp) -> None:
    data = ensure_extensions(install_dir)
    data["pending_op"] = op.to_json()
    if op.kind == "restart":
        data["pending_restart"] = op.run_id
    journal_write(install_dir, data)


def read_pending_op(install_dir: Path) -> PendingOp | None:
    try:
        data = journal_read(install_dir)
    except JournalTorn:
        return None
    return PendingOp.from_json(data.get("pending_op"))


def clear_pending_op(install_dir: Path, *, clear_restart_marker: bool = True) -> None:
    data = ensure_extensions(install_dir)
    data["pending_op"] = None
    if clear_restart_marker:
        data["pending_restart"] = None
    journal_write(install_dir, data)


# ── pending_wakes (Post-Restart Arm-Notify — ADR-039) ─────────────────────────
#
# The wake record is the durable carrier of the post-restart arm-notify
# deliverable: at arm time we capture the routing context (source, message_id,
# agent_id, etc.) which is in-memory-only on the manager and wiped at boot;
# after a daemon restart the boot sweep reads pending_wakes + a terminal-class
# journal event and delivers a self-describing wake to the arming instance
# (Phase 2, ADR-040 + ADR-041 + ADR-042).
#
# Persistence home: a new top-level key ``pending_wakes`` on
# ``releases/state.json`` keyed by ``run_id``. Lives on the same atomic
# surface as ``pending_op`` and ``pending_restart`` — written by the SAME
# ``journal_write`` envelope UNDER the caller-acquired journal lock
# (``journal_write`` itself acquires no lock; serialization is the arm
# site's ``journal_lock_acquire`` at ``upgrade_tools.py:2167``/``:2719``).
# Crash-safety: arm + wake ride one ``journal_write`` so a crash between
# leaves EITHER the pre-arm state OR the post-arm state — never a half.
#
# Lifecycle state machine (ADR-039):
#     pending → delivering → delivered | abandoned
# * pending  : newly armed; the sweep gates on the wake-owned terminal reader
#              (Phase 2) and holds the record pending a terminal event.
# * delivering : CAS-set by ``mark_wake_delivering`` (lock-holders succeed,
#              losers return ``None``); the sweep is in flight.
# * delivered : unconditional; record is STRUCTURALLY REMOVED from the dict
#              (the structural removal is the idempotency key — invariant 7;
#              invariant 5 — no second surface to forget).
# * abandoned : unconditional; record is removed AND a ``wake_abandoned``
#              history event is journaled for forensics (D-FA5.2 +
#              ADR-042 grace-abandonment).
#
# ``from_json`` filter discipline (``upgrade_journal.py:738``): known
# fields preserved, unknown fields dropped silently. Missing required
# fields raise ``ValueError`` — same discipline as the other records.
#
# Live-outright-refusal inheritance (D-FA5.5 / ADR-044): the arm-side
# write is INSIDE the try block that follows the live-outright-refusal
# return (``upgrade_tools.py:2051-2058``). A live arm returns BEFORE
# any journal write; the wake write is unreachable on live.

# Lifecycle state constants (Phase 1 T1)
_WAKE_STATUS_PENDING = "pending"
_WAKE_STATUS_DELIVERING = "delivering"
_WAKE_STATUS_DELIVERED = "delivered"
_WAKE_STATUS_ABANDONED = "abandoned"

# Wake abandonment grace (PENDING_WAKE_GRACE_S, Phase 2 T14, ADR-042).
# Default-on, env-overridable; the sweep marks the wake ``abandoned`` with
# ``reason=kill_switch_off`` if the kill-switch is OFF when the sweep runs
# (one-time pass), or after ``expires_at + grace`` if the pipeline never
# journaled a terminal event.
PENDING_WAKE_GRACE_S: int = 600

# Coalesce cap (D-FA5.2 / ADR-043): the sweep coalesces by ``arming_instance_id``
# and caps each group at 16 — the dropper journals a ``wake_coalesce_overflow``
# history event and the user can query the rest interactively. 16 wakes ×
# ~80 chars per entry ≈ 1.3KB body, well under the 64KB MessageQueue cap.
PENDING_WAKE_COALESCE_MAX: int = 16


@dataclass
class PendingWake:
    """The Post-Restart Arm-Notify durable record (ADR-039).

    Captured at arm time from the manager's in-memory user-origin window
    (RAM-only; wiped at boot — see ADR-041). The wake sweep (Phase 2)
    re-stamps the window from this record for the wake turn.
    """

    run_id: str
    kind: str                    # "restart" | "promote"
    env: str
    arming_instance_id: str = ""
    arming_agent_id: str | None = None
    source: str = ""             # "" sentinel = no user-origin at arm time
    message_id: str | None = None
    message_metadata: dict[str, Any] = field(default_factory=dict)
    target_version: str | None = None
    mode: str | None = None      # restart-only
    armed_at: str = field(default_factory=now_iso)
    expires_at: str = field(default_factory=now_iso)
    abandon_after: str = ""
    status: str = _WAKE_STATUS_PENDING
    delivered_at: str | None = None
    delivered_message_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Any) -> PendingWake | None:
        if not isinstance(data, dict) or not data.get("run_id"):
            return None
        kwargs = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        # ``abandon_after`` defaults to ``expires_at + PENDING_WAKE_GRACE_S``;
        # callers may override. If a record predates the field (legacy
        # defensive case) we backfill from expires_at + grace at use time.
        if not kwargs.get("abandon_after"):
            exp = kwargs.get("expires_at")
            if exp:
                kwargs["abandon_after"] = iso_plus(exp, PENDING_WAKE_GRACE_S)
        try:
            return cls(**kwargs)
        except (TypeError, ValueError):
            return None


# Kill-switch name (shared Phase 1 + Phase 2). The arm-side and sweep-side
# both gate on the same env; default ON.
ARM_NOTIFY_KILL_SWITCH_ENV = "ENSEMBLE_POST_RESTART_ARM_NOTIFY"


def _arm_notify_enabled() -> bool:
    """Operator kill-switch: ``ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`` disables.

    Default ON — the deliverable is zero-user-action; the kill-switch exists
    for operator opt-out, not default-off (ADR-044). Read per call (NOT
    cached) so an operator flip takes effect on the next arm / sweep.
    """
    return os.environ.get(ARM_NOTIFY_KILL_SWITCH_ENV, "1") != "0"


def arm_pending_wake(install_dir: Path, wake: PendingWake) -> None:
    """Write the ``pending_wakes`` record under the journal's existing
    atomic envelope (Phase 1 T2, ADR-039).

    Must be called INSIDE the caller-acquired journal lock at the arm
    sites (``upgrade_tools.py:2287``/``:2848``); ``journal_write``
    itself has no internal lock (architecture delta #3 — atomicity is
    BY the caller's lock, not the journal's). Per-arm semantics: a
    re-arm of the same ``run_id`` OVERWRITES — same record, same
    identity (the wake's identity is the run_id).

    The kill-switch check is the first line; when OFF the function is a
    no-op (no exception, no write — ADR-044 + D-FA6.2).
    """
    if not _arm_notify_enabled():
        return
    data = ensure_extensions(install_dir)
    pending = data.get("pending_wakes")
    if not isinstance(pending, dict):
        pending = {}
    # Backfill abandon_after if the caller left it empty (sentinel).
    payload = wake.to_json()
    if not payload.get("abandon_after") and payload.get("expires_at"):
        payload["abandon_after"] = iso_plus(
            payload["expires_at"], PENDING_WAKE_GRACE_S
        )
    pending[wake.run_id] = payload
    data["pending_wakes"] = pending
    journal_write(install_dir, data)


def mark_wake_pending(
    install_dir: Path, run_id: str
) -> PendingWake | None:
    """Reverse the ``delivering → pending`` transition (F1 review round).

    Sets ``status = "pending"`` on the record (does NOT touch the dict
    membership). Used by the sweep when ``enqueue_message`` either
    raises a transient exception or returns a result without a
    ``message_id`` — the wake MUST be made re-claimable on the next
    tick (otherwise it strands in ``delivering`` forever, since
    ``mark_wake_delivering``'s CAS gate at F1 review requires
    ``status == "pending"``).

    Atomicity-safety note (F1 review precondition): this helper is
    safe ONLY because ``enqueue_message`` (sweep's primary path) is
    atomic — verified at
    ``daemon/services/instance_messaging._prepare_enqueued_message``
    via the ``WriteGuardSession + session.commit()`` envelope: a
    raised exception rolls the entire transaction back (no
    MessageQueue row, no Task row written), and a returned
    ``message_id`` corresponds to a committed row. The rollback can
    therefore never cause a double-deliver — the wake either
    enqueued (and the next tick will see an empty dict via
    ``mark_wake_delivered``'s structural removal) or it didn't (and
    the rollback puts the record back in ``pending`` for the next
    tick to retry).

    Acquires the journal pipeline lock with ``wait_s=30.0`` (F4
    hardening — same envelope as the other ``mark_wake_*`` helpers).
    Returns the new ``PendingWake`` on success, ``None`` on
    lock-not-acquired or absent/already-pending record.
    """
    acquired, _busy = lock_acquire(install_dir, run_id, wait_s=30.0)
    if not acquired:
        logger.warning(
            "upgrade_journal: mark_wake_pending lock NOT acquired "
            "run_id=%s — skip (record stays delivering; next tick retries)",
            run_id,
        )
        return None
    try:
        data = ensure_extensions(install_dir)
        pending = data.get("pending_wakes")
        if not isinstance(pending, dict) or run_id not in pending:
            return None
        raw = pending[run_id]
        if not isinstance(raw, dict):
            return None
        if raw.get("status") == _WAKE_STATUS_PENDING:
            # Already pending — no-op (idempotent).
            return PendingWake.from_json(raw)
        if raw.get("status") != _WAKE_STATUS_DELIVERING:
            # Not in delivering state (abandoned, delivered, or other)
            # — leave it alone (the record is on a terminal path).
            return None
        raw["status"] = _WAKE_STATUS_PENDING
        pending[run_id] = raw
        data["pending_wakes"] = pending
        journal_write(install_dir, data)
        return PendingWake.from_json(raw)
    finally:
        lock_release(install_dir)


def mark_wake_delivering(
    install_dir: Path, run_id: str
) -> PendingWake | None:
    """CAS-style transition ``pending → delivering`` (Phase 1 T3, ADR-039,
    F3 hardened).

    Acquires the journal's per-env pipeline lock with ``wait_s=30.0``
    (review round F3): the CAS MUST wait for any concurrent shell
    writer to release, since shell writers (restart.sh:262 + promote.sh
    + rollback.sh) hold the lock across their ``journal_history_append``
    terminal-event writes and the sweep is the natural follow-up
    reader. The pre-F3 ``wait_s=0.0`` short-circuited the wait, leaving
    the CAS-loser path exposed to shell-writer clobber (F4's race). On
    successful acquire, sets ``status = "delivering"`` and persists via
    ``journal_write``. Returns the new ``PendingWake``. On
    lock-not-acquired-after-30s, returns ``None`` and logs a WARNING —
    the caller (Phase 2 T7) skips this tick (the next tick retries).

    The lock is the existing ``journal_lock_acquire`` (same primitive
    ``pending_op`` uses); on contention the sweep blocks until the
    holder releases (mirroring the other journal-protocol CAS sites).
    """
    acquired, busy = lock_acquire(install_dir, run_id, wait_s=30.0)
    if not acquired:
        logger.warning(
            "upgrade_journal: mark_wake_delivering lock NOT acquired "
            "run_id=%s busy=%s — skip",
            run_id, busy,
        )
        return None
    try:
        data = ensure_extensions(install_dir)
        pending = data.get("pending_wakes")
        if not isinstance(pending, dict) or run_id not in pending:
            return None
        raw = pending[run_id]
        if not isinstance(raw, dict):
            return None
        if raw.get("status") != _WAKE_STATUS_PENDING:
            return None  # not in pending state — CAS-loser or already done
        raw["status"] = _WAKE_STATUS_DELIVERING
        pending[run_id] = raw
        data["pending_wakes"] = pending
        journal_write(install_dir, data)
        return PendingWake.from_json(raw)
    finally:
        lock_release(install_dir)


def mark_wake_delivered(
    install_dir: Path, run_id: str, message_id: str
) -> None:
    """Unconditional ``delivering → delivered`` write + structural removal
    (Phase 1 T4, ADR-039, F4 hardened).

    Sets ``status = "delivered"``, ``delivered_at = now_iso()``,
    ``delivered_message_id = message_id`` then REMOVES the record from
    the ``pending_wakes`` dict. The structural removal is the
    idempotency key (invariant 7 — invariant 5 — no second surface to
    forget). The dict shrinks on every ``journal_write`` that follows.

    F4 lock hygiene (review round): the read-modify-write envelope
    acquires the journal pipeline lock with ``wait_s=30.0`` (same as
    the CAS at ``mark_wake_delivering``) so the sweep-side write
    cannot clobber a concurrent shell writer's terminal-event append
    (restart.sh:262 holds the lock across ``journal_history_append
    restart ...``; promote.sh/rollback.sh do the same). The pre-F4
    lock-free RMW was a real race at boot when a shell-writer had
    just journaled the terminal event that the wake needs to read on
    the next tick. UNCONDITIONAL: even if the record is absent we
    no-op silently — the structural removal makes this the common
    case (idempotency by construction). We do not log a WARNING —
    absence is normal.
    """
    acquired, _busy = lock_acquire(install_dir, run_id, wait_s=30.0)
    if not acquired:
        logger.warning(
            "upgrade_journal: mark_wake_delivered lock NOT acquired "
            "run_id=%s — skip (record stays pending; next tick retries)",
            run_id,
        )
        return
    try:
        data = ensure_extensions(install_dir)
        pending = data.get("pending_wakes")
        if not isinstance(pending, dict):
            pending = {}
        if run_id in pending:
            pending.pop(run_id, None)
            data["pending_wakes"] = pending
            journal_write(install_dir, data)
        # Note: ``delivered_at`` and ``delivered_message_id`` are recorded
        # only on the row that *was* present at arm time; the wake record
        # lives in MessageQueue (audit on the message itself), not here.
    finally:
        lock_release(install_dir)


def mark_wake_abandoned(
    install_dir: Path, run_id: str, reason: str
) -> None:
    """Terminal-cleanup transition + ``wake_abandoned`` history event
    (Phase 1 T5, ADR-039 + ADR-042 grace-abandonment + ADR-044
    abandon-on-switch-off, F4 hardened).

    Sets ``status = "abandoned"``, removes from dict, then calls
    ``journal_history_append(install_dir, "wake_abandoned", ...)`` for
    forensics (the operator can grep the journal for the abandonment
    reason). Uses the same ``journal_history_append`` helper as the
    existing pipeline events.

    F4 lock hygiene (review round): the dict-pop write acquires the
    journal pipeline lock with ``wait_s=30.0`` so the abandonment
    cannot clobber a concurrent shell writer's terminal-event append
    (restart.sh:262 etc.). The history-event append also runs under
    the same lock for the same reason — the wake's history entry
    must NOT race the shell's terminal-event append. The
    history-append is best-effort (a torn journal surfaces the
    failure as a log line, never raises); the dict-pop write's
    lock-acquire-refusal path also returns silently (the dict may
    have been concurrently removed by a prior pass; idempotency by
    construction).

    UNCONDITIONAL on absence — the structural removal makes this the
    common case (idempotency by construction).
    """
    acquired, _busy = lock_acquire(install_dir, run_id, wait_s=30.0)
    if not acquired:
        logger.warning(
            "upgrade_journal: mark_wake_abandoned lock NOT acquired "
            "run_id=%s — skip dict-pop (history-append also deferred; "
            "next tick retries)",
            run_id,
        )
        return
    try:
        data = ensure_extensions(install_dir)
        pending = data.get("pending_wakes")
        if isinstance(pending, dict) and run_id in pending:
            pending.pop(run_id, None)
            data["pending_wakes"] = pending
            journal_write(install_dir, data)
    finally:
        lock_release(install_dir)
    journal_history_append(
        install_dir,
        "wake_abandoned",
        f"run_id={run_id} reason={reason}",
    )


def list_pending_wakes(install_dir: Path) -> list[PendingWake]:
    """Defensive reader for the ``pending_wakes`` dict (Phase 1 T6, ADR-039).

    Returns ``[]`` on ``JournalTorn``. On any ``pending_wakes`` value
    that is not a dict (``null``, ``[]``, ``"<str>"``, ``123``) returns
    ``[]`` — the empty-list sentinel. Mirrors ``PendingOp.from_json``
    garbage tolerance (R-20). Each value validated via
    ``PendingWake.from_json`` (which itself drops unknown fields and
    returns ``None`` on missing required); malformed records are logged
    + skipped so a single bad row does NOT block the others.
    """
    try:
        data = journal_read(install_dir)
    except JournalTorn:
        return []
    pending = data.get("pending_wakes")
    if not isinstance(pending, dict):
        return []
    out: list[PendingWake] = []
    for run_id, raw in pending.items():
        if not isinstance(raw, dict):
            logger.warning(
                "upgrade_journal: pending_wakes[%r] not a dict (got %s); skipping",
                run_id, type(raw).__name__,
            )
            continue
        record = PendingWake.from_json(raw)
        if record is None:
            logger.warning(
                "upgrade_journal: pending_wakes[%r] from_json returned None "
                "(missing run_id or schema mismatch); skipping",
                run_id,
            )
            continue
        out.append(record)
    return out


def latest_matching_event(
    journal: dict[str, Any],
    run_id: str,
    armed_at: str,
    events: tuple[str, ...],
) -> str | None:
    """Parameterized history walker (Phase 1 T7, ADR-042).

    Reads the journal dict's ``history`` (FLAT ``{"ts": <iso>, "event":
    <name>, "detail": <prose>}`` records — NO ``run_id`` field at
    ``upgrade_journal.py:326`` and ``lib.sh:663,666``). Walks the
    history; returns the event name of the LATEST entry whose
    ``event`` field is a member of the passed ``events`` tuple AND
    whose ``ts`` is ``>= armed_at``.

    ``JournalTorn`` or malformed history → ``None`` (best-effort; the
    sweep's caller logs and continues).

    The ``run_id`` parameter is accepted (the walker's signature) but
    NOT used for the event-class match on the promote lane — promote
    terminal events carry NO ``run_id`` at all. Phase 2 wraps this
    walker with the wake-owned reader (mirroring
    ``_terminal_event_after`` ``upgrade_journal.py:986``) and applies
    the RESTART-lane detail-substring tie-break on the CALLER side
    (r5 fold N2).

    NOTE: returns the FIRST match whose ``ts >= armed_at`` (oldest
    matching entry); the original ``_terminal_event_after`` returns
    the NEWEST. We deliberately return the LATEST match here so the
    caller can rely on a stable ordering. Re-validating:
    ``_terminal_event_after`` walks history left-to-right and returns
    the LAST matching entry (newest by position). The wake sweep
    wants the FIRST event-class match in the ``armed_at`` window
    (newest by ts); in practice the journal appends are monotonic
    (newest last), so position-order == ts-order. We mirror
    ``_terminal_event_after``'s "return the latest" semantics — see
    the test assertion in T4.1.
    """
    history = journal.get("history")
    if not isinstance(history, list):
        return None
    armed = parse_iso_utc(armed_at)
    if armed is None:
        return None
    found: str | None = None
    for entry in history:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("event", "")) in events:
            ts = parse_iso_utc(entry.get("ts"))
            if ts is not None and ts >= armed:
                found = str(entry["event"])
    return found


# ── Verified-arm predicate + passthrough extras (v0.15.3 P1 Item 1) ─────────
#
# The 3-factor-verified live arm (user ratification 2026-09-26: the arm
# ceremony IS the F2-equivalent attestation for the TOOL LANE; ADR-017's
# tool-lane posture is superseded for VERIFIED ARMS ONLY) carries its
# attestation to promote.sh as the ``--f2-verified-closed`` argv flag plus
# the ``ENSEMBLE_UPGRADE_LIVE=1`` / ``F2_VERIFIED_NOTE`` env extras.
#
# EVERY verified-side expansion MUST route through the helpers below
# (name-frozen, truth-table-pinned) — no call site rebuilds the predicate
# inline. The F2 fence is intact for every UNVERIFIED path:
# EXECUTOR_ENV_ALLOWLIST is NOT widened; the extras ride the pre-existing
# ``executor_env`` explicit-extra merge (:1083-1084), which is per-call-site
# and never ambient — an unverified arm's child env still strips
# ENSEMBLE_UPGRADE_LIVE (poison tests pin that side).


def is_verified_arm(op: PendingOp | None) -> bool:
    """5-conjunct verified-arm predicate (M-11): the pending_op records a
    live promote whose 3-factor ceremony completed (nonce burned + human
    confirmed via a registered source).

    ``op.env == "live"`` is the safe-by-construction conjunct — the
    passthrough NEVER fires on demo/dev/sandbox even with nonce + source
    present (the truth-table test pins each falsifying row)."""
    return (
        op is not None
        and op.kind == "promote"
        and op.nonce_consumed
        and op.confirmed_by_human
        and bool(op.confirmed_source)
        and op.env == "live"  # M-11: safe-by-construction — env must be live
    )


def _verified_arm_extras(op: PendingOp | None) -> tuple[list[str], dict[str, str]]:
    """Shared verified-arm expansion — ``(argv_extension, extra_env_extension)``.

    Returns ``([], {})`` for any unverified op: call sites gate on
    :func:`is_verified_arm` and skip extension entirely (the unverified
    path stays byte-identical; the helper returning empty is the belt to
    that suspenders). The note is ``<confirmed_source>:<run_id>`` — the
    operator's audit trail (a registration id + the arm id; secret-free by
    construction — research §6 "no secrets… safe to embed")."""
    if op is None or not is_verified_arm(op):
        return [], {}
    return (
        ["--f2-verified-closed"],
        {
            "ENSEMBLE_UPGRADE_LIVE": "1",
            "F2_VERIFIED_NOTE": f"{op.confirmed_source}:{op.run_id}",
        },
    )


# ── Nonce store (D-FA3.3 — pending_actions keyed by run_id) ─────────────────


@dataclass
class PendingAction:
    """A live-confirmation nonce record (D-FA3.3). Disk-persisted in the
    journal (survives MessageQueue wipe + daemon death — R-SR10)."""

    run_id: str
    nonce: str
    kind: str                    # "upgrade"
    env: str
    target: str | None
    issued_at: str = field(default_factory=now_iso)
    ttl_expires_at: str = field(default_factory=lambda: iso_plus(now_iso(), NONCE_TTL_S))
    issued_to_instance: str = ""
    consumed_at: str | None = None
    consumed_by_message_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Any) -> PendingAction | None:
        if not isinstance(data, dict) or not data.get("nonce"):
            return None
        kwargs = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        try:
            return cls(**kwargs)
        except TypeError:
            return None


def _gc_pending_actions(
    data: dict[str, Any], keep_run_id: str | None = None
) -> dict[str, Any]:
    """Opportunistic GC of ``pending_actions`` (review nit #9): drop entries
    that are consumed or past TTL; keep everything unconsumed+unexpired
    (mintable). ``keep_run_id`` exempts one entry — consume_pending_action
    passes the just-consumed record so a nonce replay still gets the
    accurate ``nonce-already-used`` refusal (it is pruned on the NEXT
    pending_actions write instead).

    Pure in-memory dict pruning (``parse_iso_utc`` swallows garbage, all
    shapes are isinstance-guarded) — it CANNOT raise, so it can never fail
    the parent journal write. An unparseable/absent ttl_expires_at is KEPT:
    GC only deletes what it can prove is dead (an expired TTL is provable;
    an unparseable one is not — deleting is the destructive direction).
    """
    actions = data.get("pending_actions")
    if not isinstance(actions, dict) or not actions:
        return data
    now = datetime.now(tz=timezone.utc)
    kept: dict[str, Any] = {}
    for run_id, entry in actions.items():
        if run_id == keep_run_id or not isinstance(entry, dict):
            kept[run_id] = entry  # exempt / unknown shape — do not judge it
            continue
        if entry.get("consumed_at"):
            continue  # consumed → single-use spent; audit lives in history
        ttl = parse_iso_utc(entry.get("ttl_expires_at"))
        if ttl is not None and now > ttl:
            continue  # past TTL → un-mintable
        kept[run_id] = entry
    data["pending_actions"] = kept
    return data


def gc_pending_actions(install_dir: Path, keep_run_id: str | None = None) -> int:
    """Public GC entry (v0.15.3 P1 Item 4) — the scheduled-sweep face of
    :func:`_gc_pending_actions`.

    Read-modify-write: prunes consumed / past-TTL entries exactly like the
    write-triggered pruner, and returns the pruned count (0 → nothing
    pruned and NO journal write — the sweep ticks every ~90s and must not
    churn the journal: READ-FIRST discipline, same as reconcile).
    ``keep_run_id`` is the operator/in-progress exemption (the background
    sweep passes ``None`` — TTL + consumed-state logic IS the safety).
    Raises ``JournalTorn`` on a torn journal (callers in the sweep wrap
    everything; the never-raises contract lives there, not here)."""
    data = journal_read(install_dir)
    actions = data.get("pending_actions")
    if not isinstance(actions, dict) or not actions:
        return 0
    before = len(actions)
    _gc_pending_actions(data, keep_run_id=keep_run_id)
    kept = data.get("pending_actions")
    pruned = before - (len(kept) if isinstance(kept, dict) else 0)
    if pruned > 0:
        journal_write(install_dir, data)
    return pruned


def store_pending_action(install_dir: Path, action: PendingAction) -> None:
    data = ensure_extensions(install_dir)
    actions = data.get("pending_actions")
    if not isinstance(actions, dict):
        actions = {}
    actions[action.run_id] = action.to_json()
    data["pending_actions"] = actions
    _gc_pending_actions(data)  # opportunistic — cannot fail this write
    journal_write(install_dir, data)


def find_pending_action_by_nonce(install_dir: Path, nonce: str | None) -> PendingAction | None:
    """Locate a pending action by normalized nonce echo. Prefers an
    UNCONSUMED match; falls back to the consumed one (the caller then
    refuses ``nonce-already-used`` — single-use enforcement)."""
    needle = nonce_normalize(nonce)
    if not needle:
        return None
    try:
        data = journal_read(install_dir)
    except JournalTorn:
        return None
    actions = data.get("pending_actions")
    if not isinstance(actions, dict):
        return None
    matches = [
        PendingAction.from_json(v)
        for v in actions.values()
        if isinstance(v, dict) and nonce_normalize(str(v.get("nonce", ""))) == needle
    ]
    unconsumed = [m for m in matches if m and m.consumed_at is None]
    return unconsumed[0] if unconsumed else (matches[0] if matches else None)


def consume_pending_action(
    install_dir: Path, action: PendingAction, message_id: str | None
) -> None:
    """Mark consumed (single-use) + journal the audit event (D-FA3.3 — the
    audit trail survives the MessageQueue wipe)."""
    data = ensure_extensions(install_dir)
    actions = data.get("pending_actions")
    if isinstance(actions, dict) and action.run_id in actions:
        actions[action.run_id]["consumed_at"] = now_iso()
        actions[action.run_id]["consumed_by_message_id"] = message_id
        data["pending_actions"] = actions
        # GC with the just-consumed entry exempt (kept for nonce-already-
        # used on replay; pruned by a later pending_actions write).
        _gc_pending_actions(data, keep_run_id=action.run_id)
        journal_write(install_dir, data)
    journal_history_append(
        install_dir,
        "nonce_consumed",
        f"nonce for run_id={action.run_id} consumed by message "
        f"{message_id or '?'} (kind={action.kind} env={action.env} target={action.target})",
    )


# ── Lazy pending_op reconciliation (crash-tolerant closure) ─────────────────
#
# promote.sh does not know about pending_op — the tool-armed promote record
# is closed lazily when terminal evidence exists (the adopt-at-preflight
# pattern, promote.sh's own ``adopt_stale_txn``). kind=restart pending-ops
# are NEVER cleared here while their in_flight txn is open — the daemon
# boot sweep owns restart-kind convergence (D-FA4.3; P2.3 wires the boot
# sweep).

_TERMINAL_EVENTS = ("commit", "rollback", "halt", "sweep_rollback", "sweep", "quarantine")


# ── Wake terminal event-set + wake-owned reader (Phase 2 T13, ADR-042) ─────────
#
# The PROMOTE-only reconcile (``reconcile_pending_op``) depends on the
# strict 6-member ``_TERMINAL_EVENTS`` (a restart event MUST NEVER close a
# promote pending_op — the intent-driven pipeline says so). The wake
# sweep's terminal reader, by contrast, MUST fire for intentional
# restarts (``restart.sh:262`` journals ``"restart"``); without
# ``"restart"`` in the predicate the dominant wake case would never fire.
#
# Architecture delta #1: SIBLING constant — NOT a mutation of the shared
# 6-member set. The reconcile's semantics at ``:1016`` depend on the
# 6-member tuple; mutating it would silently change PROMOTE reconcile
# behavior. Phase 3 T4.8 (mutation guard) pins BOTH directions:
# ``"restart" in WAKE_TERMINAL_EVENTS`` AND ``"restart" not in
# _TERMINAL_EVENTS`` with the 6-member set intact.
#
# Ride-along #2 (approve): single home — the constant is lifted into
# ``upgrade_journal.py`` (the journal-protocol home). The old
# ``upgrade_tools.py`` alias ``_TERMINAL_OUTCOME_EVENTS = _TERMINAL_EVENTS
# + ("restart",)`` is a DIFFERENT surface (status-view terminal
# vocabulary, P1 Item 5 — NOT the wake sweep's). Both surfaces derive
# from ``_TERMINAL_EVENTS`` by tuple concat; the wake constant is the
# authoritative one for the wake sweep.
WAKE_TERMINAL_EVENTS: tuple[str, ...] = _TERMINAL_EVENTS + ("restart",)


def wake_terminal_event_after(
    journal: dict[str, Any], armed_at: str
) -> str | None:
    """Wake-owned terminal reader (Phase 2 T13, ADR-042 addendum r4 fold
    C1/r5 fold N2). Mirrors ``_terminal_event_after`` exactly: TS-scope
    (``entry.ts >= armed_at``) + event-class membership over
    ``WAKE_TERMINAL_EVENTS``, NOTHING MORE — the reader is lane-agnostic
    and has NO ``run_id`` knowledge.

    Returns the LATEST matching event name (or ``None``). The journal's
    history entries are FLAT ``{"ts": <iso>, "event": <name>, "detail":
    <prose>}`` records (``upgrade_journal.py:326`` and ``lib.sh:663,666``)
    — NO ``run_id`` field on history entries; promote-lane terminal
    events carry NO ``run_id`` at all (``promote.sh:366``;
    ``rollback.sh:203,209,211``); only the RESTART lane embeds a
    ``run_id=<id>`` substring inside the ``detail`` PROSE
    (``restart.sh:252,262``), used as an OPTIONAL tie-breaker on the
    RESTART lane only (applied caller-side in
    ``UpgradeJournalSweepService._resolve_wake_targets``, NOT here).

    The RESTART-lane tie-break NEVER blocks base event-class matching
    (r5 fold N3): a restart terminal whose detail prose mismatches (or
    omits) the run_id still fires the wake — the tie-break only
    disambiguates when MULTIPLE same-class candidates exist in scope.

    ``JournalTorn`` or malformed history → ``None`` (best-effort; the
    sweep's caller logs and continues).
    """
    return latest_matching_event(
        journal,
        run_id="<wake-reader-lane-agnostic>",  # accepted, NOT used
        armed_at=armed_at,
        events=WAKE_TERMINAL_EVENTS,
    )


def _terminal_event_after(journal: dict[str, Any], armed_at: str) -> tuple[str, dict[str, Any]] | None:
    armed = parse_iso_utc(armed_at)
    history = journal.get("history")
    if not isinstance(history, list) or armed is None:
        return None
    found: tuple[str, dict[str, Any]] | None = None
    for entry in history:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("event", "")) in _TERMINAL_EVENTS:
            ts = parse_iso_utc(entry.get("ts"))
            if ts is not None and ts >= armed:
                found = (str(entry["event"]), entry)
    return found  # newest terminal event at/after armed_at


def reconcile_pending_op(install_dir: Path) -> str | None:
    """Close a tool-armed promote pending_op when its evidence is terminal.
    Returns a one-line note when a closure happened (callers surface it),
    ``None`` otherwise. Never raises; a torn journal leaves everything
    untouched. Restart-kind ops are never touched (see note above).

    READ-FIRST discipline: no field is added and no byte is written unless
    a closure actually happens — dry-run preflights call this and must
    leave the journal byte-identical when nothing is pending."""
    try:
        data = journal_read(install_dir)
    except (JournalTorn, OSError):
        return None
    op = PendingOp.from_json(data.get("pending_op"))
    if op is None or op.kind == "restart":
        return None
    in_flight = data.get("in_flight")
    if isinstance(in_flight, dict):
        return None  # promote.sh's txn is live — the op tracks a real run
    terminal = _terminal_event_after(data, op.armed_at)
    if terminal is not None:
        event, entry = terminal
        # "Never raises" contract: the 3-call closure below is best-effort
        # (NIT-F, P2.3 B3.5: was "3-write" — ensure_extensions writes only
        # when a field is missing, so the steady-state closure performs 2
        # writes, not 3; a failed write is logged and left as-is — the
        # pending_op survives for the boot sweep / operator; this is a
        # janitor, never a gate).
        try:
            ensure_extensions(install_dir)
            clear_pending_op(install_dir, clear_restart_marker=False)
            journal_history_append(
                install_dir,
                "sweep",
                f"pending_op run_id={op.run_id} closed by reconcile: terminal event "
                f"'{event}' at {entry.get('ts', '?')} ({str(entry.get('detail', ''))[:120]})",
            )
        except (JournalTorn, OSError) as exc:
            logger.warning(
                "upgrade_journal: reconcile terminal-closure write FAILED "
                "(pending_op left as-is): %s",
                exc,
            )
            return None
        return (
            f"pending_op run_id={op.run_id} closed — terminal event "
            f"'{event}' at {entry.get('ts', '?')}"
        )
    # No txn, no terminal event: if well past expiry the executor died
    # pre-open (before promote.sh opened its txn) — close as expired.
    expires = parse_iso_utc(op.expires_at)
    if expires is not None and datetime.now(tz=timezone.utc) > expires + timedelta(
        seconds=RECONCILE_GRACE_S
    ):
        try:  # same never-raises contract as the terminal closure above
            ensure_extensions(install_dir)
            clear_pending_op(install_dir, clear_restart_marker=False)
            journal_history_append(
                install_dir,
                "sweep",
                f"pending_op run_id={op.run_id} cleared by reconcile: no in_flight, no "
                f"terminal event, past expires_at {op.expires_at}+grace (executor died pre-open?)",
            )
        except (JournalTorn, OSError) as exc:
            logger.warning(
                "upgrade_journal: reconcile expiry-closure write FAILED "
                "(pending_op left as-is): %s",
                exc,
            )
            return None
        return f"pending_op run_id={op.run_id} cleared (expired, executor died pre-open)"
    return None


# ── Daemonized executor spawn (D-FA1.3 / D4 / T5) ───────────────────────────

# R-SR09 env allowlist — the executor inherits the MINIMUM a pipeline
# script needs. NEVER the daemon's full environment (no .env passthrough,
# no API keys). PG* covers the sandbox drill harness's throwaway PG vars.
#
# Bus-discovery vars (r-f82e fix cycle 1): on session-env Linux hosts
# (dev/demo login-session daemons), ``systemd-run --user`` needs
# ``XDG_RUNTIME_DIR`` + ``DBUS_SESSION_BUS_ADDRESS`` to locate the user
# bus. They are non-secrets (only bus socket addresses, never keys) and
# the F2 fence is preserved — ``ENSEMBLE_UPGRADE_LIVE`` and other
# privileged/secret vars are NOT in this allowlist and stay stripped.
# Pin: tests/unit/tools/test_promote_cgroup_survivorship_python.py
# 6b + 6c (cycle-1 fixback).
EXECUTOR_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH", "HOME", "INSTALL_DIR", "PORT", "POSTGRES_DB", "TMPDIR",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "DBUS_SYSTEM_BUS_ADDRESS",
)
EXECUTOR_ENV_PREFIXES: tuple[str, ...] = ("PG",)


def executor_env(extra: dict[str, str] | None = None) -> dict[str]:
    env: dict[str, str] = {}
    for key in EXECUTOR_ENV_ALLOWLIST:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    for key, val in os.environ.items():
        if any(key.startswith(p) for p in EXECUTOR_ENV_PREFIXES):
            env[key] = val
    for key, val in (extra or {}).items():
        env[key] = str(val)
    return env


def executor_log_path(install_dir: Path) -> Path:
    return install_dir / "data" / "upgrade.log"


# ── systemd transient scope (r-20260928-005506-f82e; component 1 of 6) ────────
#
# INCIDENT (2026-09-28, ensemble-vm LIVE): the promote executor was spawned
# with ``start_new_session=True`` (≡ setsid). That creates a new SESSION
# — it does NOT leave the systemd unit's cgroup. When ``ensemble-live.service``
# deactivated under KillMode=control-group, the cgroup teardown SIGTERMed
# every process in the unit — including the executor — BEFORE the symlink
# flip could run, leaving an orphaned ``in_flight`` txn the reaper had
# stopped observing 2 seconds earlier.
#
# FIX: on Linux+systemd hosts, wrap the executor in a transient SCOPE unit
# (``systemd-run --scope --unit=ensemble-upgrade-<run_id> …``). The scope is
# its OWN cgroup, outside ``ensemble-live.service`` — the unit teardown
# cannot reach it, the executor survives to flip + close the txn.
#
# BYTE-IDENTICALITY (macOS / no-systemd / Linux-no-systemd): today's
# ``start_new_session=True`` path is preserved verbatim. Detection is
# test-injectable via ``_scope_detect_fn`` so the three branches are
# pinned deterministically (Linux+systemd, Linux-no-systemd, non-Linux).
#
# UNIT NAME: ``ensemble-upgrade-<run_id>`` — the polkit rule on
# ensemble-vm (§3.4 of the incident doc) name-restricts scope creation
# to ``^ensemble-[0-9A-Za-z@._-]+\.scope$`` via the detail-carrying
# path; systemd 255's StartTransientUnit path carries no detail, so
# the rule's detail-less branch (granted) covers us. The
# ``ensemble-upgrade-`` prefix is conservative and matches the rule's
# regex; future tightening to ``^ensemble-upgrade-`` is straightforward.
#
# USER vs SYSTEM bus: the daemon runs as the service user (no root).
# A non-root process cannot create scope units on the system bus; we
# therefore use the USER bus via ``systemd-run --user --scope`` when
# available, falling back to the system bus when --user is unavailable
# (daemons running as root in containers, etc.). Detection mirrors
# the systemd-run helper's own logic: try ``--user`` first and watch
# for a clear "not available" stderr; if it fails for any reason,
# fall back to ``--scope`` (system bus).
#
# R-SR09 ENV ALLOWLIST PRESERVED: the scope wrapper's child inherits the
# SAME allowlist as today's Popen — no .env passthrough, no API keys.
# The wrapper argv carries the SAME inner argv; the env dict flows
# through unchanged.


# Sentinel object the spawner uses to thread the run_id (and ONLY the
# run_id) into the scope-unit name. Re-exported from upgrade_tools for
# the test pin; production code passes the run_id via the existing
# reaper-enqueue seam, NOT through argv mutation.
SCOPE_UNIT_PREFIX = "ensemble-upgrade-"

# Test seam: ``_scope_detect_fn`` returns ``(use_scope, bus_kind)``
# where ``bus_kind`` is ``"user"`` or ``"system"``. Default = the real
# detector. Tests inject a stub returning deterministic values for
# the three branches (Linux+systemd / Linux-no-systemd / non-Linux).
# r-f82e fix cycle 1: the seam now accepts the spawn-time env dict
# (executor_env shape) so detection sees exactly what the wrapper
# inherits — pin 6a (Python) asserts the env dict is forwarded.
def _scope_detect_real(env: dict[str, str] | None = None) -> tuple[bool, str]:
    """Real detector — Linux+systemd → (True, "user"|"system") else
    (False, ""). Conservative: returns ``(False, "")`` whenever ANY
    detection step fails (no /run/systemd/system, no systemd-run on
    PATH, uname != Linux, etc.) so a misconfigured host falls back
    to the legacy ``start_new_session=True`` path byte-identically.

    The ``env`` arg, when supplied, is the EXACT env the scope wrapper
    inherits from the caller (executor_env(extra_env)). Detection uses
    THIS env, NOT the daemon's full ambient — on session-env Linux
    hosts (r-f82e fix cycle 1), the bus-discovery vars
    (XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS) are ambient in the
    daemon process but the allowlist strips them from the wrapper, so
    probing with ambient would falsely report "user bus available"
    while the wrapper would lack the bus address and the payload
    would never reach systemd-run. ``env=None`` preserves the
    pre-cycle-1 probe (inherit ambient) for the existing pin 5 call
    site that exercises "never raises" on a hostile PATH.
    """
    try:
        if sys.platform != "linux":
            return (False, "")
        # systemd's "I'm PID 1" mark — universal across distros.
        if not Path("/run/systemd/system").exists():
            return (False, "")
        # systemd-run must be on PATH (NOT just present elsewhere).
        # shutil.which honors PATH and respects current env.
        if shutil.which("systemd-run") is None:
            return (False, "")
        # We MUST know whether to use --user or --scope. The clean
        # test: try ``systemd-run --user --scope --unit=… /bin/true``
        # with stderr captured; an "not available" message means the
        # user instance isn't running (the common case for service
        # users). Anything else (success) → use --user. If --user
        # fails for any OTHER reason we conservatively fall back to
        # the system bus (--scope alone) — the unit-name match still
        # passes the polkit rule's detail-less branch.
        #
        # r-f82e fix cycle 1: probe is run with ``env=env`` (the
        # wrapper's env), NOT the daemon's full ambient. Without this
        # gate, the probe inherits ambient XDG_RUNTIME_DIR +
        # DBUS_SESSION_BUS_ADDRESS, decides "user bus OK", and the
        # wrapper then lacks the bus address at spawn time — the
        # payload never runs. Pin: tests/unit/tools/test_
        # promote_cgroup_survivorship_python.py 6a.
        probe_unit = f"ensemble-upgrade-detect-{os.getpid()}-{int(time.time())}"
        try:
            r = subprocess.run(
                ["systemd-run", "--user", "--scope", f"--unit={probe_unit}",
                 "/bin/true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=5.0,
                env=env,
            )
            if r.returncode == 0:
                return (True, "user")
        except (OSError, subprocess.TimeoutExpired):
            pass
        # Fall back to system bus: --scope alone (no --user). Still
        # needs systemd-run AND the polkit grant for nea (live).
        try:
            r = subprocess.run(
                ["systemd-run", "--scope", f"--unit={probe_unit}",
                 "/bin/true"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=5.0,
                env=env,
            )
            if r.returncode == 0:
                return (True, "system")
        except (OSError, subprocess.TimeoutExpired):
            pass
        return (False, "")
    except Exception as exc:  # noqa: BLE001 — detection must never raise
        # r-f82e fix cycle 2 (review-cycle-2 fixback): one WARNING so a silent
        # detector regression (e.g. a NameError from a typo'd import that the
        # bare-except swallow hides for days) leaves a forensic breadcrumb.
        # The 3-commit silent NameError is the dispositive cost-of-silence
        # evidence; spawn rarity makes noise negligible. NO behavior change —
        # detection still falls back to legacy path byte-identically.
        logger.warning(
            "upgrade_journal: _scope_detect_real swallowed exception — "
            "falling back to legacy path: %s: %s",
            type(exc).__name__, exc,
        )
        return (False, "")


_scope_detect_fn: Callable[[dict[str, str] | None], tuple[bool, str]] = _scope_detect_real


def build_scope_argv(
    inner_argv: list[str], run_id: str, bus_kind: str
) -> list[str]:
    """Build the systemd-run argv wrapping ``inner_argv`` in a transient
    scope. The SCOPE UNIT NAME carries the run_id for observability
    (visible via ``systemctl list-units ensemble-upgrade-*`` and in
    the journal). Returns ``["systemd-run", "--user"|"--scope",
    "--unit=ensemble-upgrade-<run_id>", "--", <inner_argv...>]``.

    Caller's responsibility: this helper does NO detection — pass the
    result of ``_scope_detect_fn()`` directly. Pure function for test
    injection; the detector itself has a test seam above.
    """
    unit = f"{SCOPE_UNIT_PREFIX}{run_id}"
    # systemd-run args: --user/--scope first (mutually exclusive),
    # --unit=NAME next, then -- (separator), then the inner argv.
    head: list[str] = ["systemd-run"]
    if bus_kind == "user":
        head.append("--user")
    # else "system" → just --scope (no --user)
    head.append("--scope")
    head.append(f"--unit={unit}")
    head.append("--")
    return head + list(inner_argv)


def spawn_executor(
    argv: list[str],
    install_dir: Path,
    extra_env: dict[str, str] | None = None,
    *,
    run_id: str | None = None,
) -> tuple[int, str]:
    """Daemonize the executor payload. THREE BRANCHES (r-f82e fix):

    1. Linux + systemd + polkit grant: wrap in a transient scope unit
       (``systemd-run --user/--scope --unit=ensemble-upgrade-<run_id>``).
       The scope is its OWN cgroup — unit teardown cannot reach it
       (the live promote survives daemon shutdown, flips the symlink,
       closes the txn). Detected via ``_scope_detect_fn`` (test-injectable).
    2. Linux + no systemd / systemd-run denied: fall back to today's
       ``start_new_session=True`` path BYTE-IDENTICALLY. The setsid
       child shares the unit's cgroup — known-bad under KillMode=
       control-group, but the only available option on a non-systemd
       host.
    3. Non-Linux (macOS, BSD): the legacy ``start_new_session=True``
       path BYTE-IDENTICALLY. setsid works correctly on launchd —
       the survivorship model was designed on macOS launchd semantics.

    Returns ``(child_pid, mode_note)``. ``mode_note`` is derived from the
    SAME internal detection result (``scope=ensemble-upgrade-<run_id>``
    for the SCOPE branch, ``(daemonized, start_new_session)`` for the
    legacy branch) so the caller never has to re-detect. Deliberately
    NOT registered in ``BashProcessRegistry`` or any other teardown
    registry (D4/T5 static-assertion target): the child must survive
    BOTH tool-harness teardown AND daemon death. stdio →
    ``data/upgrade.log`` (append). The child re-points its cwd at the
    install dir so relative pipeline output lands in the right place.
    Env: same allowlist semantics (R-SR09) — no .env passthrough, no
    API keys. The scope-wrapper inherits the SAME env dict.

    ``run_id`` (r-f82e fix cycle 1, kw-only): when supplied, the
    SCOPE UNIT NAME carries it directly (``ensemble-upgrade-<run_id>``),
    giving journal↔unit correlation and a stable per-promote unit
    identity even when argv lacks ``--run-id`` (the promote argv today
    carries ``--version`` only). When ``None``, falls back to argv
    extraction (legacy behavior) and finally to a pid+epoch sentinel.
    Manager's drain path threads ``run_id`` explicitly so the unit
    name is reliable; tests and any future call site may omit it.
    """
    log = executor_log_path(install_dir)
    log.parent.mkdir(parents=True, exist_ok=True)

    # r-f82e fix cycle 1: build the env dict FIRST — detection must see
    # exactly the env the wrapper inherits (executor_env shape).
    # Without this gate the probe inherits ambient bus-discovery vars
    # and decides "user bus OK"; the wrapper then lacks them and the
    # payload never runs. Pin: 6a (Python).
    env = executor_env(extra_env)
    use_scope, bus_kind = _scope_detect_fn(env)

    if use_scope:
        # r-f82e fix cycle 1: prefer the explicit run_id kwarg (set by
        # the manager drain seam — promotion callers always have it),
        # then argv extraction (restart argv carries --run-id), then
        # the pid+epoch sentinel as a last-resort unique unit name.
        # The scope unit MUST be unique per promote — systemd refuses
        # the second transient creation against an existing name.
        if not run_id:
            run_id = ""
            for i, tok in enumerate(argv[:-1]):
                if tok == "--run-id" and i + 1 < len(argv):
                    run_id = argv[i + 1]
                    break
            if not run_id:
                run_id = f"spawn-{os.getpid()}-{int(time.time())}"
        wrapped_argv = build_scope_argv(argv, run_id, bus_kind)
        with log.open("ab") as log_fh:
            proc = subprocess.Popen(
                wrapped_argv,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=str(install_dir),
                env=env,
                # start_new_session MUST be False: systemd-run is the
                # new session leader; setsid on the daemon side
                # would put the systemd-run child in OUR cgroup,
                # defeating the scope escape.
                start_new_session=False,
                close_fds=True,
            )
        return proc.pid, f"scope=ensemble-upgrade-{run_id}"

    # Legacy path: BYTE-IDENTICAL to pre-r-f82e behavior. Any host
    # where the scope detector returns (False, "") lands here.
    with log.open("ab") as log_fh:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(install_dir),
            env=env,
            start_new_session=True,  # ≡ setsid: detaches the process group
            close_fds=True,
        )
    return proc.pid, "(daemonized, start_new_session)"


# ── Supervision classification (ownership-mode commission P1, 2026-09-29) ────
#
# DETECTION + CLASSIFIER ONLY — zero behavior change to existing paths.
# Consumers (stop-path P2, hand-back P3, unit adoption P4) land later; this
# section exposes the seam and the boot-time advisory only.
#
# §0 MANDATORY (architect 2026-09-29, leader-ratified): the cgroup BASENAME
# of the owning pid's cgroup leaf (/proc/<pid>/cgroup) is the PRIMARY
# signal. INVOCATION_ID is CORROBORATION/DIAGNOSTIC ONLY — it is set for
# transient scopes too (systemd-run --scope mints one for every promote
# executor) AND ``executor_env`` strips it from the allowlist, so an
# INVOCATION_ID-first gate would misclassify today's live survivor as
# SCRIPT. On disagreement: trust the cgroup, WARN-once.
#
#   leaf ends ``.service``                 → UNIT_MANAGED (yields unit name)
#   leaf matches ``ensemble-upgrade-*.scope`` → SCOPE_SURVIVOR
#   ``session-*`` / user-slice / init scope → SCRIPT_NOHUP
#
# TWINS CONTRACT: the classification ladder is implemented TWICE — this
# Python twin (daemon-side, boot advisory) and ``supervision_classify`` in
# scripts/upgrade/lib.sh (pipeline-side, preflight + stop site). The ~6-line
# cgroup-leaf parse below is DELIBERATELY duplicated across the twins
# (cross-language seam — twins-pinned); the P5 twins-agree drift-guard test
# enforces agreement. Do not "DRY" them into one side.
#
# This twin classifies the DAEMON'S OWN pid (it runs inside the daemon at
# boot); the shell twin discovers the install's owning pid via the anchored
# tiers (scripts/stop-ensemble.sh) and reads ITS cgroup. Both observe the
# same daemon, so the states agree wherever the daemon actually lives.
#
# ZERO-CHANGE GUARDS: NEVER raises (every failure path degrades to a
# classified state); NO disk writes (the boot advisory journal event is
# appended by the sweep-service startup hook, not the detector); non-Linux
# hosts take the script branch with ZERO /proc and /run reads (byte-safe on
# BSD/macOS arms).
#
# ── Named deployment topologies + declared×verified outcome map (A1) ────────
#
# TWO named topologies (Amendment #1, 2026-09-29):
#
#   SCRIPT MODE  — the operator runs the script; the launcher lineage
#                  self-respawns (crash backoff, ADR-011). Today's
#                  live/prod shape: direct start, no OS supervisor.
#   SERVICE MODE — an OS service owns monitoring/restart. systemd is THIS
#                  commission's substrate (ENSEMBLE_SUPERVISION=unit +
#                  ENSEMBLE_RESTART_UNIT); service×macOS = launchd =
#                  documented FUTURE scope.
#
# Deployment type is a FIRST-CLASS dimension alongside OS detection: the
# DECLARED mode (``ENSEMBLE_SUPERVISION``: unit|script|auto) and the
# classifier's VERIFIED state (UNIT_MANAGED / SCOPE_SURVIVOR /
# SCRIPT_NOHUP / DUAL_FIGHT) form one explicit matrix with NAMED outcome
# classes — ``conforming`` / ``degraded`` / ``fault`` (see
# ``supervision_outcome`` below). A1 is BEHAVIOR-PRESERVING: every
# conforming/degraded/fault PATH predates this mapping and is unchanged
# (exit-78 preflights, WARN-degradeds, DUAL_FIGHT halts); A1 adds only
# naming, documentation and the mapping surface.
#
#   declared        verified                 outcome     (existing behavior named)
#   ─────────────── ──────────────────────── ──────────  ──────────────────────
#   script          SCRIPT_NOHUP             conforming  SCRIPT MODE healthy
#   script          UNIT_MANAGED             degraded    decl-mismatch; latent two-masters (UNREACHABLE: explicit script never verifies the cgroup)
#   script          SCOPE_SURVIVOR           degraded    WARN-once + self-heals at next promote with a unit configured
#   unit            UNIT_MANAGED (name set)  conforming  SERVICE MODE healthy
#   unit            UNIT_MANAGED (no name)   fault       exit-78 at preflight — never silent-degrade
#   unit            anything else            fault       never silent-degrade (DUAL_FIGHT arm halts loud)
#   auto            SCRIPT_NOHUP             conforming  auto defers to verification
#   auto            UNIT_MANAGED             conforming  auto defers to verification (P4 adoption signal)
#   auto            SCOPE_SURVIVOR           degraded    WARN-once + self-heals at next promote with a unit configured
#   any             DUAL_FIGHT               fault       halt-loud (two masters must never meet a flip)
#   unknown         anything                 fault       fail-closed
#
# RESOLVED-MODE EQUIVALENCE: the detector resolves ``auto`` INTO
# ``unit``|``script`` before returning (``mode`` = the declaration
# POST-ladder), and the resolved-mode cells agree with the declared cells
# above row-for-row — runtime callers therefore pass ``detection.mode``
# and the twins agree cell-for-cell either way.
#
# OS×deployment matrix — canonical home: ``docs/runbooks/systemd-adoption.md``
# (lands in P4; the forward reference is INTENTIONAL). Rows: script×macOS
# = byte-identical no-systemd arm; script×ubuntu-no-systemd = same arm;
# script×ubuntu-systemd-present-not-adopted = TODAY's live topology;
# service×ubuntu = systemd substrate; service×macOS = FUTURE scope (launchd).
#
# Surfaced outcome name (additive only): the boot-advisory detail string
# gains ``outcome=<name>`` (upgrade_journal_sweep.py); the machine line
# ``ENSEMBLE_SUPERVISION_RESULT=<state>[:<unit>]`` grammar is FROZEN (P2
# consumes it) and is never extended here.

# Resolved supervision states (the machine-line vocabulary shared with the
# shell twin — twins-pinned).
SUPERVISION_STATE_SCRIPT = "SCRIPT_NOHUP"
SUPERVISION_STATE_UNIT = "UNIT_MANAGED"
SUPERVISION_STATE_SCOPE = "SCOPE_SURVIVOR"
# §6 DUAL_FIGHT is a supervision_dualfight_check VERDICT, not a classify
# state — but it is a first-class VERIFIED input to the outcome map below
# (twins-pinned vocabulary, shell twin literal "DUAL_FIGHT").
SUPERVISION_STATE_DUALFIGHT = "DUAL_FIGHT"

# Opt-out vocabulary — matches ``daemon.config._PROACTIVE_FALSE_BOOLS`` /
# upgrade_tools ``_OPT_OUT_FALSES`` (same permissive bool parser the repo
# uses for kill-switch env knobs; lower-cased + trimmed at the compare site).
# Module-level (frozen constant) — mirrors the cross-module opt-out
# vocabulary placement convention.
_SUPERVISION_OPT_OUT_FALSES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class SupervisionDetection:
    """One supervision classification result.

    ``state``  — SCRIPT_NOHUP | UNIT_MANAGED | SCOPE_SURVIVOR
    ``unit``   — unit name when UNIT_MANAGED ("" when none resolved)
    ``mode``   — resolved mode AFTER the ladder: "unit" | "script"
    ``note``   — WARN-once diagnostic ("" when the ladder ran clean)
    """

    state: str
    unit: str = ""
    mode: str = "script"
    note: str = ""


def _supervision_read_cgroup_leaf(pid: int) -> str | None:
    """Basename of the owning pid's cgroup LEAF, or None on any failure.

    TWINS-PINNED parse (~6 lines, duplicated in lib.sh ``supervision_classify``
    — cross-language seam; see the section comment). cgroup v2 emits one
    ``0::/path`` line; hybrid v1 emits several — the LAST line's last path
    segment is the leaf on both.
    """
    try:
        raw = Path(f"/proc/{int(pid)}/cgroup").read_text(encoding="utf-8")
        line = raw.strip().splitlines()[-1]
        path = line.split(":", 2)[-1]
        leaf = path.rstrip("/").rsplit("/", 1)[-1]
        return leaf or None
    except (OSError, ValueError, IndexError):
        return None


def _supervision_classify_leaf(leaf: str) -> tuple[str, str]:
    """Leaf basename → (state, unit). §0 table; conservative default script."""
    if leaf.endswith(".service"):
        return SUPERVISION_STATE_UNIT, leaf
    if leaf.startswith("ensemble-upgrade-") and leaf.endswith(".scope"):
        return SUPERVISION_STATE_SCOPE, ""
    if leaf.startswith("session-") or leaf == "init.scope":
        return SUPERVISION_STATE_SCRIPT, ""
    if leaf.endswith(".slice") and ("user" in leaf or "machine" in leaf):
        # user-1000.slice / user.slice / machine.slice hierarchies — login
        # sessions and containers, not a service unit.
        return SUPERVISION_STATE_SCRIPT, ""
    # Unknown leaf shape (raw system.slice child, cgroupns oddity) —
    # fail toward script, no unit name.
    return SUPERVISION_STATE_SCRIPT, ""


def _supervision_detect_real(
    env: dict[str, str] | None = None,
) -> SupervisionDetection:
    """Real detector — NEVER raises; every failure path degrades to a
    classified state (fail-toward-script, mirroring ENSEMBLE_SELF_ENV's
    resolution shape at upgrade_tools:199-242).

    Resolution ladder (P1 §2):
      explicit ``ENSEMBLE_SUPERVISION=unit|script``  → wins
      ``0|false|no|off``                            → silent script opt-out
      any other garbage value                       → WARN-once → script
      ``auto`` / unset                              → auto-derive chain:
        non-Linux            → script  (ZERO /proc + /run reads)
        /run/systemd/system absent → script (ZERO /proc reads)
        own-pid cgroup leaf decisive → UNIT_MANAGED / SCOPE_SURVIVOR / script
        unreadable cgroup   → script + WARN-once
      INVOCATION_ID = corroboration ONLY: on disagreement with the cgroup
      state, trust the cgroup + carry a WARN-once note (§0 — it is set for
      transient scopes too, and executor_env strips it).

    Unit-name resolution (P1 §3, only when a unit name is needed):
      env ENSEMBLE_RESTART_UNIT > cgroup-derived (UNIT_MANAGED only).
      The python side NEVER refuses on an unresolved explicit-unit — the
      exit-78 refusal belongs to the shell preflight (scripts/upgrade);
      here the unresolved case is carried as (UNIT_MANAGED, unit="",
      note="unit-unresolved") for the advisory record.

    TWINS DIVERGENCE — no .env rung (M1, review cycle 1; pinned +
    documented, deliberately NOT implemented): the shell twin has a
    THIRD name rung (INSTALL_DIR/.env read directly, pipeline-side);
    this twin resolves env > cgroup ONLY. Rationale: the daemon is
    launcher-started and launcher.sh ``load_env_file`` EXPORTS every
    .env key into the daemon process env, so a .env-sourced
    ENSEMBLE_RESTART_UNIT already reaches the env rung transitively on
    every supported start; a daemon-side .env read would require the
    install-dir ladder (live/demo topology + frozen-binary sandbox)
    re-implemented here — a cyclic import away in upgrade_tools and an
    ambient read of the REAL live install from dev-context tests. A
    boot advisory on a non-launcher start may therefore note
    ``unit-unresolved`` where the shell preflight would resolve via
    .env — advisory-only, never a gate; the exit-78 semantics live in
    the shell preflight either way.
    """
    try:
        e = os.environ if env is None else env
        raw = (e.get("ENSEMBLE_SUPERVISION") or "").strip()

        # ── explicit / opt-out / garbage parsing (ladder top) ─────────────
        if raw:
            low = raw.lower()
            if low in _SUPERVISION_OPT_OUT_FALSES:
                return SupervisionDetection(SUPERVISION_STATE_SCRIPT)
            if low == "script":
                return SupervisionDetection(SUPERVISION_STATE_SCRIPT, mode="script")
            if low == "unit":
                # explicit unit: resolve the NAME (env > cgroup); the
                # daemon cannot refuse (boot advisory) — carry the note.
                unit = (e.get("ENSEMBLE_RESTART_UNIT") or "").strip()
                if not unit and sys.platform == "linux":
                    leaf = _supervision_read_cgroup_leaf(os.getpid())
                    if leaf and leaf.endswith(".service"):
                        unit = leaf
                if unit:
                    return SupervisionDetection(
                        SUPERVISION_STATE_UNIT, unit=unit, mode="unit"
                    )
                return SupervisionDetection(
                    SUPERVISION_STATE_UNIT,
                    unit="",
                    mode="unit",
                    note=(
                        "unit mode explicit but no unit name resolvable "
                        "(env ENSEMBLE_RESTART_UNIT unset; cgroup not "
                        "unit-managed) — shell preflight owns the exit-78 "
                        "refusal"
                    ),
                )
            if low != "auto":
                return SupervisionDetection(
                    SUPERVISION_STATE_SCRIPT,
                    note=f"garbage ENSEMBLE_SUPERVISION='{raw}' — WARN-once, failing toward script",
                )

        # ── auto-derive chain ─────────────────────────────────────────────
        if sys.platform != "linux":
            return SupervisionDetection(SUPERVISION_STATE_SCRIPT)
        if not Path("/run/systemd/system").exists():
            return SupervisionDetection(SUPERVISION_STATE_SCRIPT)
        leaf = _supervision_read_cgroup_leaf(os.getpid())
        if not leaf:
            return SupervisionDetection(
                SUPERVISION_STATE_SCRIPT,
                note="cgroup unreadable for own pid — WARN-once, failing toward script",
            )
        state, unit = _supervision_classify_leaf(leaf)

        # ── INVOCATION_ID corroboration (diagnostic ONLY — §0) ───────────
        inv = (e.get("INVOCATION_ID") or "").strip()
        note = ""
        if inv and state != SUPERVISION_STATE_UNIT:
            note = (
                f"INVOCATION_ID present but cgroup leaf '{leaf}' classifies "
                f"{state} — trusting cgroup (§0: transient scopes mint "
                "INVOCATION_ID too)"
            )
        elif not inv and state == SUPERVISION_STATE_UNIT:
            note = (
                f"UNIT_MANAGED (leaf '{leaf}') without INVOCATION_ID in "
                "env — trusting cgroup (§0 corroboration only)"
            )
        mode = "unit" if state == SUPERVISION_STATE_UNIT else "script"
        return SupervisionDetection(state, unit=unit, mode=mode, note=note)
    except Exception as exc:  # noqa: BLE001 — detection must never raise
        logger.warning(
            "upgrade_journal: _supervision_detect_real swallowed exception — "
            "degrading to script: %s: %s",
            type(exc).__name__, exc,
        )
        return SupervisionDetection(SUPERVISION_STATE_SCRIPT)


# Test seam (mirrors ``_scope_detect_fn``): tests inject a stub returning a
# deterministic SupervisionDetection; production calls the real detector.
_supervision_detect_fn: Callable[
    [dict[str, str] | None], SupervisionDetection
] = _supervision_detect_real

# Compute-once-per-process memo. The ladder is pure observation (no disk
# writes) and the daemon's supervision state cannot change within a
# process lifetime (a unit↔nohup switch IS a process switch), so one
# classification per boot is correct. Tests bypass the memo by calling
# ``_supervision_detect_real`` / the seam directly (same pattern the scope
# pins use); ``_supervision_reset_memo()`` clears it for test hygiene.
_SUPERVISION_MEMO: SupervisionDetection | None = None


def supervision_detect(env: dict[str, str] | None = None) -> SupervisionDetection:
    """Memoized classification for in-daemon consumers (boot advisory).
    First call wins regardless of ``env`` — ``env`` is a seam/testing
    override; production callers pass nothing and read os.environ."""
    global _SUPERVISION_MEMO
    if _SUPERVISION_MEMO is None:
        _SUPERVISION_MEMO = _supervision_detect_fn(env)
    return _SUPERVISION_MEMO


def _supervision_reset_memo() -> None:
    """Test hygiene: clear the compute-once memo."""
    global _SUPERVISION_MEMO
    _SUPERVISION_MEMO = None


# ── Declared×verified outcome mapping (Amendment #1 delta A1, 2026-09-29) ────
#
# NAMED outcome classes for the declared-mode × verified-state matrix
# (section comment above holds the full table + named topologies). Pure
# function: no I/O, never raises, returns a fail-closed ``fault`` for any
# unknown input. TWINS-PINNED: ``supervision_map_outcome`` in
# scripts/upgrade/lib.sh implements the IDENTICAL cell-for-cell table
# (outcome names AND reason strings byte-identical) — the P5
# twins-agree drift-guard test pins this. Do not "DRY" them into one side.
#
# Behavior-preserving by construction: this function NAMES existing
# outcomes; it does not gate, refuse, warn, or journal anything. The
# runtime paths (exit-78 preflight refusal, WARN-once degradeds,
# DUAL_FIGHT halt) predate A1 and are untouched.

# Outcome vocabulary (twins-pinned with the shell twin's literals).
SUPERVISION_OUTCOME_CONFORMING = "conforming"
SUPERVISION_OUTCOME_DEGRADED = "degraded"
SUPERVISION_OUTCOME_FAULT = "fault"


class SupervisionOutcome(NamedTuple):
    """One declared×verified mapping verdict.

    ``outcome`` — conforming | degraded | fault
    ``reason``  — short human-readable cell reason (twins-pinned
                  byte-identical with the shell twin)
    """

    outcome: str
    reason: str


def supervision_outcome(
    declared: str, verified: str, unit: str = ""
) -> SupervisionOutcome:
    """Map (declared mode, verified state[, unit name]) → named outcome.

    ``declared`` — "unit" | "script" | "auto" (the ENSEMBLE_SUPERVISION
                   vocabulary; the detector's resolved ``mode`` may also be
                   passed — see RESOLVED-MODE EQUIVALENCE, section comment)
    ``verified`` — SCRIPT_NOHUP | UNIT_MANAGED | SCOPE_SURVIVOR |
                   DUAL_FIGHT (the classifier / dualfight vocabulary)
    ``unit``     — resolved unit name; ONLY consulted for
                   declared=unit × verified=UNIT_MANAGED, where an EMPTY
                   name is the reachable exit-78 arm (explicit unit,
                   nothing resolvable) and names the cell ``fault``.

    Cell-for-cell table (mirrors the section comment; the twins-pinned
    shell twin ``supervision_map_outcome`` agrees on every cell):

      script × SCRIPT_NOHUP  → conforming  (SCRIPT MODE healthy)
      script × UNIT_MANAGED  → degraded    (decl-mismatch; UNREACHABLE today)
      script × SCOPE_SURVIVOR→ degraded    (WARN + self-heal at next promote)
      unit   × UNIT_MANAGED  → conforming with a name, fault without
                               (exit-78 arm; never silent-degrade)
      unit   × else          → fault       (never silent-degrade)
      auto   × SCRIPT_NOHUP  → conforming  (verification deferred, confirmed)
      auto   × UNIT_MANAGED  → conforming  (P4 adoption signal)
      auto   × SCOPE_SURVIVOR→ degraded    (WARN + self-heal at next promote)
      any    × DUAL_FIGHT    → fault       (halt-loud)
      unknown × anything     → fault       (fail-closed)
    """
    dec = (declared or "").strip().lower()
    ver = (verified or "").strip()

    if ver == SUPERVISION_STATE_DUALFIGHT:
        return SupervisionOutcome(
            SUPERVISION_OUTCOME_FAULT,
            "two masters live (unit active/armed while owned pids or "
            "port-holder sit outside it) — halt-loud",
        )
    if dec == "script":
        if ver == SUPERVISION_STATE_SCRIPT:
            return SupervisionOutcome(
                SUPERVISION_OUTCOME_CONFORMING,
                "script topology declared and verified (SCRIPT MODE — "
                "launcher lineage self-respawns)",
            )
        if ver == SUPERVISION_STATE_UNIT:
            return SupervisionOutcome(
                SUPERVISION_OUTCOME_DEGRADED,
                "declaration mismatch: script declared but unit-managed "
                "reality — latent two-masters hazard (unreachable today: "
                "explicit script never verifies the cgroup)",
            )
        if ver == SUPERVISION_STATE_SCOPE:
            return SupervisionOutcome(
                SUPERVISION_OUTCOME_DEGRADED,
                "scope survivor under script declaration: WARN-once + "
                "self-heals at next promote with a unit configured",
            )
    elif dec == "unit":
        if ver == SUPERVISION_STATE_UNIT:
            if unit:
                return SupervisionOutcome(
                    SUPERVISION_OUTCOME_CONFORMING,
                    "service topology declared and verified, unit name "
                    "resolved (SERVICE MODE)",
                )
            return SupervisionOutcome(
                SUPERVISION_OUTCOME_FAULT,
                "unit declared but no unit name resolvable — preflight "
                "refuses (exit 78), never silent-degrade",
            )
        if ver == SUPERVISION_STATE_SCRIPT:
            return SupervisionOutcome(
                SUPERVISION_OUTCOME_FAULT,
                "declaration mismatch: unit declared but script reality "
                "— never silent-degrade (unreachable today: explicit "
                "unit never verifies the cgroup)",
            )
        if ver == SUPERVISION_STATE_SCOPE:
            return SupervisionOutcome(
                SUPERVISION_OUTCOME_FAULT,
                "declaration mismatch: unit declared but scope-survivor "
                "reality — never silent-degrade (unreachable today)",
            )
    elif dec == "auto":
        if ver == SUPERVISION_STATE_SCRIPT:
            return SupervisionOutcome(
                SUPERVISION_OUTCOME_CONFORMING,
                "auto defers to verification: script topology confirmed "
                "(today's nohup direct/live-prod shape)",
            )
        if ver == SUPERVISION_STATE_UNIT:
            return SupervisionOutcome(
                SUPERVISION_OUTCOME_CONFORMING,
                "auto defers to verification: unit topology confirmed "
                "(P4 adoption signal)",
            )
        if ver == SUPERVISION_STATE_SCOPE:
            return SupervisionOutcome(
                SUPERVISION_OUTCOME_DEGRADED,
                "scope survivor under auto declaration: WARN-once + "
                "self-heals at next promote with a unit configured",
            )
    # Unknown declared mode or verified state — fail-closed.
    return SupervisionOutcome(
        SUPERVISION_OUTCOME_FAULT,
        "unknown declared mode or verified state — fail-closed",
    )


# ── User-origin classification (assumption #1 closure — D-FA3.1; verdict §4) ──
# HISTORY: this was a STATIC whitelist (exact "api" + the
# telegram:/webhook:/whatsapp:/discord:/slack: id prefixes). The live-gate
# investigation (dead-lettered job f9309685 →
# .agents/shared/planning/deploy-ownership-fix/gate-bug-final-verdict.md,
# 2026-09-24) proved it DEAD for chat: sources registry.py:948 mints
# "<source_id>:<uid>[:<cid>]" where source_id is the OPERATOR-CHOSEN
# registration id (e.g. "my-discord-bot" — live deployment), so no chat
# source could ever match a platform prefix, every genuine chat nonce
# ceremony was structurally refused at the window factor, and the ONLY
# origin that could arm ("api") was precisely the F2-forgeable one.
#
# Classification is now REGISTRY-BACKED (verdict §4):
#
#   * exact FULL-STRING "api"      — routers/messages.py stamps the literal
#                                    "api" on the HTTP chat path. Full-string
#                                    equality only: a source REGISTERED under
#                                    id "api" mints "api:<uid>", which does
#                                    NOT take this path (it must clear the
#                                    registry like any other source).
#                                    F2 PRECISION (security review round 1):
#                                    vs the old whitelist, the STRING SET
#                                    that can arm via the unauth-loopback
#                                    body.source grows from {"api"} to
#                                    {"api"} ∪ {registered-chat-id
#                                    patterns} — but the CAPABILITY delta
#                                    is ZERO: an attacker able to forge
#                                    body.source could already send the
#                                    exact string "api", so chat-pattern
#                                    strings grant no new power. F2 forging
#                                    itself remains the separately-fenced
#                                    pre-existing exposure (executor env
#                                    allowlist + --f2-verified-closed
#                                    promote gate), not addressed here.
#   * registered chat source       — first segment of the source string
#                                    (source.split(":", 1)[0]; source_id is
#                                    colon-free per models/source.py:36)
#                                    resolves in the sources registry AND its
#                                    daemon-controlled source_type is a chat
#                                    type. source_type is write-once at
#                                    registration (SourceUpdate has no
#                                    source_type field; never
#                                    message-supplied) — the anti-forgery
#                                    invariant. Classification therefore
#                                    reads REGISTRY METADATA ONLY.
#   * everything else FAILS CLOSED — unregistered ids, deregistered sources,
#                                    registry unavailable/raising, and the
#                                    reserved internal lanes
#                                    (internal_agent:*, agent:*,
#                                    cascade_resume, scheduler, …) all clear
#                                    the window at the stamp site
#                                    (manager.stamp_user_origin_window).
#                                    Belt-and-suspenders: reserved ids are
#                                    rejected via constants.is_reserved_source
#                                    (single-home check) BEFORE the registry
#                                    lookup, so even a mis-registered id like
#                                    "agent" can never arm.
#                                    Cross-reference (restored — the old
#                                    static-whitelist comment named this):
#                                    the pre-existing else-branch HUMAN
#                                    mis-typing defect at
#                                    instance_messaging.py:1310-1319 is
#                                    DEFERRED, not fixed here; this
#                                    classification is its mitigation and
#                                    gates AT THE TOOL — the mitigation
#                                    still holds (reserved lanes clear the
#                                    window regardless of registry type).
#
# webhook is EXCLUDED from the chat-type set: no WebhookAdapter exists
# (_create_adapter_from_config has no webhook branch — verdict §1); add it
# only when an adapter lands.
#
# whatsapp is the mirror-image case: it IS in the accept set
# (USER_ORIGIN_CHAT_SOURCE_TYPES above) but NO whatsapp adapter exists in
# _create_adapter_from_config (daemon/sources/registry.py:445-537 has no
# whatsapp branch), so the registry lookup for any "whatsapp:*" id always
# misses → classification fails CLOSED ("unregistered"). Dead-but-harmless:
# membership is kept deliberately for FORWARD-COMPAT — a future whatsapp
# adapter arms the gate the moment it registers, with no gate-side edit.
#
# SINGLE SOURCE OF TRUTH: classify_user_origin() is the ONLY classification.
# The stamp site (manager.stamp_user_origin_window), the release_info
# user-origin drift probe (upgrade_tools.py), and the gate refusal token all
# consult it (or user_origin_sources_display()) — no divergent copies.
#
# NOT the same concept as constants.CHAT_SOURCE_PREFIXES ("telegram:",
# "slack:", "discord:" — the jobs_crud/task-repo chat-source WORKER-LANE
# routing set, no whatsapp): this gate set is matched against registry
# source_type VALUES (not id prefixes) and carries whatsapp per the verdict.
# Do NOT dedup the two — different members, different match semantics.
_USER_ORIGIN_EXACT: frozenset[str] = frozenset({"api"})

# The registry source_type values that count as a human chat channel for the
# live-upgrade gate (SourceType members, compared via .value).
USER_ORIGIN_CHAT_SOURCE_TYPES: frozenset[str] = frozenset(
    {"telegram", "slack", "discord", "whatsapp"}
)


def user_origin_sources_display() -> str:
    """Accurate one-line description of what arms the live gate — used in
    refusal reasons and docs. MUST track classify_user_origin; kept adjacent
    so drift is visible in review (the old prefix frozenset lied about what
    arms the gate — it described a dialect no chat source speaks)."""
    types = ", ".join(sorted(USER_ORIGIN_CHAT_SOURCE_TYPES))
    return (
        'exact source "api" (HTTP chat path) + any REGISTERED source whose '
        f"daemon-controlled source_type is one of: {types} "
        "(registry-backed; webhook has no adapter and never arms)"
    )


def classify_user_origin(
    source: str | None,
    registry_get: Callable[[str], Any] | None,
) -> tuple[bool, str]:
    """THE single source of truth for user-origin classification (verdict §4).

    ``registry_get`` is the sources registry lookup (``registry.get``);
    pass ``None`` when no registry exists — then only exact "api" can arm.

    Returns ``(is_user_origin, detail)``. ``detail`` is a short diagnostic
    token (no secrets) safe to embed in gate refusal reasons. Never raises:
    every failure mode fails CLOSED.
    """
    if not isinstance(source, str) or not source:
        return False, "no-source"
    if source in _USER_ORIGIN_EXACT:
        return True, "exact:api"
    # Reserved internal lanes NEVER arm — checked BEFORE the registry so a
    # mis-registered reserved id ("agent", "scheduler", …) cannot arm via
    # registry metadata either. is_reserved_source is the single-home check.
    if is_reserved_source(source):
        return False, "reserved-internal"
    segment = source.split(":", 1)[0]
    if not segment:
        return False, "empty-segment"
    if registry_get is None:
        return False, "registry-unavailable"
    try:
        adapter = registry_get(segment)
    except Exception as exc:  # noqa: BLE001 — fail-closed on any registry fault
        return False, f"registry-error:{type(exc).__name__}"
    if adapter is None:
        return False, "unregistered"
    st = getattr(adapter, "source_type", None)
    st_value = getattr(st, "value", st)  # SourceType enum → plain str
    if not isinstance(st_value, str) or st_value not in USER_ORIGIN_CHAT_SOURCE_TYPES:
        # Detail-token rendering is deliberately BOUNDED (security review
        # round 1, MINOR-1). The common case is a non-chat STRING — render
        # it repr-style, capped at 40 chars (keeps 'scheduler' / 'webhook'
        # refusal tokens distinguishable). Any NON-string value renders as
        # its TYPE NAME only: a bare {st_value!r} would leak enum class
        # names ("SourceType.discord") and, for default-repr pathological
        # objects, memory addresses into gate refusal reasons. The length
        # cap also bounds adversarially long values. Classification is
        # unaffected: this branch has already failed CLOSED.
        if isinstance(st_value, str):
            rendered = repr(st_value[:40])
        else:
            rendered = type(st_value).__name__[:40]
        return False, f"source-type-not-chat:{rendered}"
    return True, f"registered-chat:{st_value}"


def is_user_origin_source(
    source: str | None,
    registry_get: Callable[[str], Any] | None = None,
) -> bool:
    """Boolean convenience wrapper over :func:`classify_user_origin`.

    The stamp site (manager) passes its registry's ``get``; registry-less
    callers get exact-"api"-only semantics (fail-closed)."""
    ok, _ = classify_user_origin(source, registry_get)
    return ok
