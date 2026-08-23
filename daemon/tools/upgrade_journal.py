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

Executor spawn (D-FA1.3 / D4):
    :func:`spawn_executor` runs the payload via ``subprocess.Popen(...,
    start_new_session=True)`` (≡ double-fork + ``setsid`` on macOS and
    under PyInstaller — assumption #2 of the pre-freeze checklist, verified
    by the Dispatch-B sandbox drill). The child is deliberately NOT
    registered in ``BashProcessRegistry`` (or anywhere else): the tool
    harness's SIGTERM teardown must never reach it. stdio →
    ``<install_dir>/data/upgrade.log``; env is ALLOWLISTED (R-SR09 — no
    ``.env`` passthrough, no API keys).
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
    tmp = jp.with_name(f"{jp.name}.tmp.{os.getpid()}.{int(time.time() * 1000)}")
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
    """Append to ``history`` (newest last) — lib.sh ``journal_history_append``."""
    data = journal_read(install_dir)
    history = data.get("history")
    if not isinstance(history, list):
        history = []
    history.append({"ts": now_iso(), "event": event, "detail": detail})
    data["history"] = history
    journal_write(install_dir, data)


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
        # Deliberate conservative divergence from lib.sh's shell `kill -0`
        # (EPERM → dead there): treat EPERM as ALIVE — never break a lock
        # whose owner may be live; degrades to pipeline-busy instead.
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


def lock_run_id(install_dir: Path) -> str | None:
    """The active lock's run_id, if held (read-only)."""
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
    def from_json(cls, data: Any) -> "PendingOp | None":
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
    def from_json(cls, data: Any) -> "PendingAction | None":
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
        ensure_extensions(install_dir)
        clear_pending_op(install_dir, clear_restart_marker=False)
        journal_history_append(
            install_dir,
            "sweep",
            f"pending_op run_id={op.run_id} closed by reconcile: terminal event "
            f"'{event}' at {entry.get('ts', '?')} ({str(entry.get('detail', ''))[:120]})",
        )
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
        ensure_extensions(install_dir)
        clear_pending_op(install_dir, clear_restart_marker=False)
        journal_history_append(
            install_dir,
            "sweep",
            f"pending_op run_id={op.run_id} cleared by reconcile: no in_flight, no "
            f"terminal event, past expires_at {op.expires_at}+grace (executor died pre-open?)",
        )
        return f"pending_op run_id={op.run_id} cleared (expired, executor died pre-open)"
    return None


# ── Daemonized executor spawn (D-FA1.3 / D4 / T5) ───────────────────────────

# R-SR09 env allowlist — the executor inherits the MINIMUM a pipeline
# script needs. NEVER the daemon's full environment (no .env passthrough,
# no API keys). PG* covers the sandbox drill harness's throwaway PG vars.
EXECUTOR_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH", "HOME", "INSTALL_DIR", "PORT", "POSTGRES_DB", "TMPDIR",
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


def spawn_executor(
    argv: list[str], install_dir: Path, extra_env: dict[str, str] | None = None
) -> int:
    """Daemonize the executor payload (``start_new_session=True`` ≡
    double-fork + setsid). Returns the child pid.

    Deliberately NOT registered in ``BashProcessRegistry`` or any other
    teardown registry (D4/T5 static-assertion target): the child must
    survive BOTH tool-harness teardown and daemon death. stdio →
    ``data/upgrade.log`` (append). The child re-points its cwd at the
    install dir so relative pipeline output lands in the right place.
    """
    log = executor_log_path(install_dir)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as log_fh:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(install_dir),
            env=executor_env(extra_env),
            start_new_session=True,  # ≡ setsid: detaches the process group
            close_fds=True,
        )
    return proc.pid


# ── USER_ORIGIN_SOURCES (assumption #1 closure — D-FA3.1) ───────────────────
#
# Enumeration of the ACTUAL user-origin source strings observed at the
# dispatch funnel (manager._process_message_with_tracking receives
# ``message_source``; formats verified in the dispatch paths):
#
#   * ``"api"``                    — routers/messages.py:391 stamps exactly
#                                    "api" on the HTTP chat path (the web
#                                    UI — a genuine human typing).
#   * ``"<source_id>:<user_id>"``  — daemon/sources/registry.py:869 builds
#                                    this for every external-channel message
#                                    (``f"{source_id}:{external_user_id}"``;
#                                    instance_messaging.py:1781 docstring
#                                    example: "telegram:user:1"). source_id
#                                    is DB-configured per adapter
#                                    (daemon/models/source.py SourceCreate,
#                                    pattern ^[a-zA-Z0-9_-]+$ — free-form);
#                                    the deployed convention names sources
#                                    after their type ("telegram:user").
#                                    The whitelist therefore matches the six
#                                    human-channel SourceType values as
#                                    PREFIXES (daemon/models/source.py:17):
#                                    telegram / webhook / whatsapp / discord
#                                    / slack. A source whose source_id does
#                                    NOT start with its type name fails
#                                    CLOSED (no marker) — the safe direction;
#                                    rename the source or use the web UI.
#
# Deliberately NOT whitelisted (machine/internal origins — the marker must
# never fire for them):
#   * ``"scheduler"``              — daemon/sources/adapters/scheduler.py:765
#                                    (a scheduled job is not a human).
#   * ``"cascade_resume"``, ``"internal_invoke_and_wait:*"``, ``"agent:*"``,
#     ``"internal_report:*"``, ``"internal_error_report:*"``,
#     ``"internal_agent:*"``, every other ``internal_*`` prefix — internal
#     lanes (the pre-existing else-branch HUMAN-mis-typing defect at
#     instance_messaging.py:1310-1319 is DEFERRED, not fixed here; this
#     whitelist is its mitigation and gates AT THE TOOL).
_USER_ORIGIN_EXACT: frozenset[str] = frozenset({"api"})
_USER_ORIGIN_PREFIXES: tuple[str, ...] = (
    "telegram:", "webhook:", "whatsapp:", "discord:", "slack:",
)

USER_ORIGIN_SOURCES: frozenset[str] = frozenset(
    _USER_ORIGIN_EXACT | set(_USER_ORIGIN_PREFIXES)
)


def is_user_origin_source(source: str | None) -> bool:
    """Whitelist test for a message source string (exact or channel prefix)."""
    if not isinstance(source, str) or not source:
        return False
    if source in _USER_ORIGIN_EXACT:
        return True
    return source.startswith(_USER_ORIGIN_PREFIXES)
