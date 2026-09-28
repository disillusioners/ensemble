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
            # at upgrade_journal.py:1180 raised NameError on every call,
            # silently swallowed by the broad `except Exception:` at :1247 —
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
from typing import Any, Callable

from daemon.constants import is_reserved_source

logger = logging.getLogger(__name__)

# ── Constants (mirror scripts/upgrade/lib.sh — single source is lib.sh; a
# drift here is a protocol violation, not a config knob) ────────────────────
ROLLBACK_CAP_24H = 3            # lib.sh ROLLBACK_CAP_24H
LOCK_HEARTBEAT_REFRESH_S = 30   # lib.sh LOCK_HEARTBEAT_S
LOCK_STALE_S = 300              # lib.sh LOCK_STALE_S
NONCE_TTL_S = 15 * 60           # §4.3 nonce TTL (15 min)
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
        probe_unit = f"ensemble-upgrade-detect-{os.getpid()}-{os.getpid()}"
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


_scope_detect_fn: Callable[[], tuple[bool, str]] = _scope_detect_real


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
) -> int:
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

    Returns the child pid. Deliberately NOT registered in
    ``BashProcessRegistry`` or any other teardown registry (D4/T5
    static-assertion target): the child must survive BOTH tool-harness
    teardown AND daemon death. stdio → ``data/upgrade.log`` (append).
    The child re-points its cwd at the install dir so relative
    pipeline output lands in the right place. Env: same allowlist
    semantics (R-SR09) — no .env passthrough, no API keys. The
    scope-wrapper inherits the SAME env dict.

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
        if run_id:
            pass  # use directly
        else:
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
        return proc.pid

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
    return proc.pid


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
