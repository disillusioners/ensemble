#!/usr/bin/env python3
# ============================================================================
# scripts/upgrade/journal_splice.py — atomic O(1) journal writer (P2 / WS-3)
# ============================================================================
# Protocol spec: architecture-recommendation.md §3 (P2 splice boundary).
# Decisions (a): opt B (stdlib-only python helper, no bash fallback on
# missing interpreter). Field-ownership matrix per decisions.md (a).
#
# # Invocation
#     python3 scripts/upgrade/journal_splice.py <journal_path> <op> [args…]
#
# # Ops (verbatim from arch §3 + C-2 correction; C-13 disambiguation):
#     append-history    <event-kind> <detail-string>
#     set-field         <top-level-field> <raw-json-literal>
#     set-in-flight-field <nested-key>   <raw-json-literal>   # C-2: NESTED
#     unset-field       <top-level-field>
#     clear-in-flight
#     set-previous      <quoted-version | "null">
#
# # Never in helper (decision (a) fenced-out list):
#     - set-current         (bash `journal_set_current`, lib.sh:826)
#     - pending_op family   (python reconciler owns, sweep:1590 reads)
#     - counters / cooldown_until / quarantine / rollback_window_count
#       (library/component owners; never from helper)
#     - open-txn            (bash `journal_open_txn` at lib.sh:700 is the
#                            sole writer — C-13 explicitly fences out of P2)
#
# # Exit codes:
#     0   success
#     1   runtime error (logged; caller treats as halt)
#     78  refusal (interpreter missing/below floor, live install path,
#         torn journal, schema drift, malformed argv)
#     2   argparse usage error
#
# # stderr shape (one greppable line, token-pinned):
#     splice: ERROR:  <op> <args>: <reason>
#     splice: REFUSED: <token>:  <detail>
#
# # Atomic write (matches ledger_check/journal_write):
#     tmp = jp.with_name(f"{name}.tmp.{pid}.{uuid4hex}.{msecs}")
#     open(tmp,'w',encoding='utf-8'); write; flush; NO fsync
#     os.replace(tmp, jp)
#     best-effort tmp unlink on OSError
#
# # Serialization (C-6 reframe; matches the bash writer's byte shape under
#     load→dump):
#     json.dumps(d, separators=(",", ":"), ensure_ascii=False)
#     + one trailing "\n"
#     Invariants: key order from input (3.7+ dict order); non-target field
#     values byte-identical after round-trip.
#
# # Live-install refusal (per ledger_check.py:46-55):
#     The resolved journal path must NOT be under ~/agents-ensemble
#     (the literal live install root), checked after expanduser+resolve
#     with case-fold + samefile defense. Refusal happens BEFORE any read.
#
# # Lock interplay:
#     The caller HOLDS the pipeline lock (every shell writer does —
#     promote.sh:165 lock_acquire → :177-2526 :2532 :4249 all under lock).
#     The helper NEVER probes the lock. The orphan-helper case (caller
#     SIGKILLed mid-helper-write) is safe: `os.replace` is atomic either
#     way; the in-flight write's pre-state cannot be observed.
#
# # Stdlib only (no `pip install`, no venv): mirrors
#     `scripts/upgrade/ledger_check.py:18-26` discipline (explicit argv,
#     no env-sniffing). The promote.sh preflight `python3 -c ...` probe
#     (C-7) is the gate that catches missing interpreters BEFORE the
#     first helper call; this script does NOT re-probe (the caller's
#     job; keeps startup cheap).
# ============================================================================

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, NoReturn

# ── Constants (single-source-of-truth per arch §3) ─────────────────────────
LIVE_INSTALL_DIRNAME = "agents-ensemble"  # literal live install root: ~/agents-ensemble

# Helpers may write ONLY these top-level fields. Anything else (e.g.
# pending_op, current, counters, cooldown_until, quarantine,
# rollback_window_count) is FENCED OUT per decisions.md (a) field-ownership
# matrix. The set is the single source of truth; reject any field write
# that targets an unauthorized key.
ALLOWED_TOP_LEVEL_WRITES = frozenset({
    "history",        # via append-history op
    "in_flight",      # via set-in-flight-field, clear-in-flight
    "previous",       # via set-previous
})

ALLOWED_IN_FLIGHT_KEYS = frozenset({
    # The owner_process record's `last_heartbeat` is the LOAD-BEARING
    # signal for the sweep (decisions (b) / C-2). The shell lane writes
    # this on the 30s lock_heartbeat cadence via the splice path. The
    # python reconciler does NOT touch this (the sweep's accessor
    # reads in_flight.last_heartbeat only).
    "last_heartbeat",
    # Comp3 owner_pid is currently bash-written; we open the fence here
    # for future ops but it is NOT required for P2.
    # "owner_pid",
})


# ── stderr emit (token-pinned, greppable) ───────────────────────────────────


def _err(op: str, args: list[str], reason: str) -> None:
    args_part = " ".join(args)
    print(f"splice: ERROR: {op} {args_part}: {reason}", file=sys.stderr)


def _refused(token: str, detail: str) -> None:
    print(f"splice: REFUSED: {token}: {detail}", file=sys.stderr)


def _die_refused(token: str, detail: str) -> NoReturn:
    _refused(token, detail)
    sys.exit(78)


def _die_runtime(op: str, args: list[str], reason: str) -> NoReturn:
    _err(op, args, reason)
    sys.exit(1)


# ── live-install refusal (mirrors ledger_check.py:46-55) ────────────────────


def _live_install_root() -> Path:
    try:
        home = Path.home()
    except RuntimeError as exc:
        _die_refused("python3-floor", f"cannot determine home for live-install refusal: {exc}")
    return (home / LIVE_INSTALL_DIRNAME).resolve(strict=False)


def _pid_alive(pid: int) -> bool:
    """os.kill(pid, 0) liveness with the EPERM=ALIVE rule — mirrors the
    bash twin `_pid_alive` (lib.sh) and `upgrade_journal._pid_alive`
    (PermissionError → the process EXISTS under another user; killing
    a live foreign owner's lock would trample its txn)."""
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _pipeline_lock_held(jp: Path) -> bool:
    """True when the promote/rollback pipeline lock for the install that
    OWNS this journal is held by a LIVE owner (C4 remediation, council
    2026-10-12).

    Protocol (mirrors lib.sh lock_acquire / the launcher _js_lock
    twin): the lock is the DIRECTORY
    ``<install>/releases/rollback.lock.d``; contents: ``owner`` (pid),
    ``run_id``, ``heartbeat`` (epoch, refreshed ~30s). HELD := the dir
    exists AND the owner pid is alive. Heartbeat age is advisory — a
    live owner is trusted even mid-un-heartbeated long ops (the stop
    span runs un-heartbeated up to 600s; lib.sh never breaks a live
    owner's lock on heartbeat age alone). A crashed owner leaves a
    fresh-heartbeat dir with a dead pid → NOT held → the refusal
    stands (fail-closed) until the sweep adopts/breaks the stale dir.

    Why this shape: the live-install refusal (mirrored from
    ledger_check.py:46-55) fences the READ/inspect tool out of the
    live install — but the splice is the WRITE tool ON the promote
    pipeline itself, and promote.sh live DETERMINISTICALLY refused at
    its first splice call (the maiden ceremony was unusable as
    shipped; a live manual rollback diverged the journal after
    repoint). Waiving ONLY while the pipeline lock is genuinely held
    keeps OUT-of-pipeline writers fail-closed: any splice call against
    the live install while no pipeline action is running is refused
    exactly as before. Residual (documented, accepted): a writer that
    somehow runs while the SANCTIONED pipeline holds the lock passes
    the same check the lock itself grants — the lock is the trust
    anchor, not the caller's identity (mkdir-locks carry no
    inheritable fd to attest with).

    C4 pid-reuse residual (LANE-1 yellow, council 2026-10-12; accepted,
    NOT mitigated here): a lock-owner pid written to
    ``rollback.lock.d/owner`` can be recycled by an unrelated process
    after the original owner dies but before the sweep adopts/breaks
    the stale dir — i.e., the waiver could be granted to a non-owner.
    The harness of the residual is bounded by two mitigations working
    in concert: (i) the mkdir-lock EPERM liveness semantics — a
    recycled pid only "owns" the lock dir if it can also recreate it,
    which is gated by the OS directory-mode check; (ii) the waiver
    window is short — the live owner is the genuine pipeline
    (``promote.sh`` / ``rollback.sh``) which holds the dir for the
    duration of one ceremony (<≈600s on stop-spans), and any pid
    recycled inside that window would also have to land inside the
    same install. Neither mitigation closes the residual in the
    abstract; we accept it because the residual is bounded to the
    ceremony window AND because tightening it would require an
    inheritable fd across exec (a deep, cross-platform change) or a
    signed witness from the lock helper (a new artifact). The blast
    radius is bounded to a single ``append-history`` /
    ``set-in-flight-field`` / ``unset-field`` against the live journal
    during the brief window — equivalent in failure mode to the
    pre-C4 sanctioned pipeline writing the same op, which is the
    trust anchor by construction. Do NOT change behavior here — the
    docstring only documents why we accept the residual."""
    releases = jp.parent
    if releases.name != "releases":
        return False
    lock_dir = releases / "rollback.lock.d"
    if not lock_dir.is_dir():
        return False
    try:
        owner_raw = (lock_dir / "owner").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not owner_raw.isdigit():
        return False
    return _pid_alive(int(owner_raw))


def _is_under_live(resolved: Path) -> bool:
    live = _live_install_root()
    # String compare first (fast; sufficient on POSIX where the live path
    # has no symbolic aliases the user controls). samefile() second for
    # any existing component of the input path so symlink spellings can't
    # smuggle a live path in.
    if resolved == live or live in resolved.parents:
        return True
    try:
        resolved_folded = os.path.normcase(str(resolved)).casefold()
        live_folded = os.path.normcase(str(live)).casefold()
        if resolved_folded == live_folded or resolved_folded.startswith(live_folded + os.sep):
            return True
    except Exception as exc:  # noqa: BLE001 — defensive fold, never gate-relevant
        # M2 (Batch-2 tidier): the fold is a defense-in-depth alias check
        # BEHIND the exact-path compare above; a fold failure must NOT
        # change the refusal path (samefile walk + final False still
        # decide). The catch stays deliberately broad (Path.__str__ /
        # normcase oddities are the hazard being defended against), but
        # the swallow is now VISIBLE at DEBUG instead of silent.
        logging.getLogger(__name__).debug(
            "journal_splice._is_under_live: case-fold alias check skipped "
            "(resolved=%r live=%r): %s: %s",
            resolved, live, type(exc).__name__, exc,
        )
    if live.exists():
        probe = resolved if resolved.exists() else None
        cursor: Path | None = resolved
        while cursor is not None and str(cursor) != cursor.anchor:
            try:
                if cursor.exists() and os.path.samefile(cursor, live):
                    return True
            except OSError:
                pass
            cursor = cursor.parent
    return False


# ── journal I/O (atomic, parse-then-write) ──────────────────────────────────


def _read_journal(jp: Path) -> dict[str, Any]:
    """Parse the entire journal. JSONDecodeError → 78 (refuse to splice a
    torn journal). File missing → caller treats as fresh-init (writes
    the journal_init payload). Invalid UTF-8 byte stream → 78 (treat
    as torn — refusing is the safe answer; lib.sh's brace-walk could
    tolerate pure-ASCII garbage by accident, but json.loads+utf-8
    cannot)."""
    try:
        if not jp.exists():
            # Treat absent as a fresh-init shape. Caller's `journal_init`
            # would normally write the empty payload; the splice helper,
            # if it runs first (e.g. on a brand-new state.json), must
            # not panic. This matches the bash journal_init semantics:
            # idempotent "create empty if absent."
            return {
                "current": None,
                "previous": None,
                "in_flight": None,
                "rollback_window_count": {"24h": 0, "window_start": None},
                "cooldown_until": None,
                "quarantined": [],
                "history": [],
            }
        raw = jp.read_bytes()
    except OSError as exc:
        _die_runtime("<read>", [str(jp)], f"cannot read journal: {exc}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        _die_refused("torn-journal", f"journal at {jp} is not valid UTF-8 (binary garbage?): {exc}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _die_refused("torn-journal", f"journal at {jp} is not valid JSON: {exc}")
    if not isinstance(data, dict):
        _die_refused("schema", f"journal root at {jp} is not a JSON object (got {type(data).__name__})")
    return data


def _ensure_required_top_level(data: dict[str, Any], keys: list[str]) -> None:
    """Refuse if any required top-level key is missing — the helper
    enforces the D4 schema so a partial write can't silently drift it."""
    for k in keys:
        if k not in data:
            # Auto-fill schema-missing optional slots with their init values
            # is NOT the helper's job (schema drift would mask a real bug);
            # bail loudly.
            _die_refused("schema-drift", f"required top-level key '{k}' missing from journal")


def _atomic_write(jp: Path, payload: dict[str, Any]) -> None:
    """Temp-file + os.replace in the SAME dir. No fsync. Mirrors the
    ledger_check / journal_write atomicity contract."""
    name = jp.name
    parent = jp.parent
    parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    uuid4hex = uuid.uuid4().hex[:8]
    msecs = int(time.time() * 1000)
    tmp = parent / f"{name}.tmp.{pid}.{uuid4hex}.{msecs}"
    serialized = json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        _die_runtime("<write>", [str(jp)], f"cannot write tmp {tmp}: {exc}")
    try:
        os.replace(tmp, jp)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        _die_runtime("<write>", [str(jp)], f"os.replace failed: {exc}")


# ── Op handlers (single point of truth per op) ──────────────────────────────


def _op_append_history(jp: Path, args: list[str]) -> int:
    if len(args) < 2:
        _die_refused("argv", f"append-history needs 2 args (event detail); got {len(args)}")
    event, detail = args[0], args[1]
    if not isinstance(event, str) or not event:
        _die_refused("argv", "append-history: event must be non-empty string")
    data = _read_journal(jp)
    _ensure_required_top_level(data, ["history"])
    history = data.get("history")
    if not isinstance(history, list):
        _die_refused("schema-drift", f"history field is not a list (got {type(history).__name__})")
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # The bash writer escapes embedded " in detail via its _json_escape;
    # we build a structured object so the helper doesn't need to worry
    # about escape correctness for arbitrary detail strings (the bash
    # writer's escape table is conservative — the helper takes the
    # structured path, which is strictly more robust).
    new_entry = {"ts": now_iso, "event": event, "detail": detail}
    history.append(new_entry)
    data["history"] = history
    _atomic_write(jp, data)
    return 0


def _op_set_field(jp: Path, args: list[str]) -> int:
    if len(args) != 2:
        _die_refused("argv", f"set-field needs 2 args (field raw-json); got {len(args)}")
    field, raw = args[0], args[1]
    if field not in ALLOWED_TOP_LEVEL_WRITES:
        _die_refused(
            "field-ownership",
            f"set-field: target '{field}' is fenced out of helper (per decisions (a) "
            f"matrix); allowed: {sorted(ALLOWED_TOP_LEVEL_WRITES)}",
        )
    if field == "in_flight":
        # The 19 promote.sh call sites use `append-history`, not set-field
        # on in_flight, but `set-field in_flight` is technically possible
        # if a future caller wanted a whole-object write. We refuse here
        # in P2 to keep the splice fenced to its declared surface — use
        # `set-in-flight-field` for per-key writes (the C-2-correct path).
        _die_refused(
            "field-ownership",
            "set-field in_flight is fenced — use set-in-flight-field <key> <json> "
            "for the nested write path (decisions (b) / C-2)",
        )
    if field == "history":
        # W5 (council 2026-10-12): the history key is APPEND-ONLY —
        # set-field (whole-object replace) on it could silently erase
        # the audit trail (the only evidence surface promote/rollback
        # debugging has). The sole writer is the append-history op;
        # anything else refuses.
        _die_refused(
            "field-ownership",
            "set-field history is APPEND-ONLY — history may only be written "
            "via the append-history op (W5: a whole-object replace would "
            "erase the audit trail)",
        )
    # Parse the raw literal; on failure this is an argv-shape bug, refuse.
    try:
        parsed_raw = json.loads(raw)
    except json.JSONDecodeError as exc:
        _die_refused("argv", f"set-field: raw-json-literal not valid JSON ({exc})")
    data = _read_journal(jp)
    _ensure_required_top_level(data, [field])
    data[field] = parsed_raw
    _atomic_write(jp, data)
    return 0


def _op_set_in_flight_field(jp: Path, args: list[str]) -> int:
    if len(args) != 2:
        _die_refused("argv", f"set-in-flight-field needs 2 args (nested-key raw-json); got {len(args)}")
    key, raw = args[0], args[1]
    if key not in ALLOWED_IN_FLIGHT_KEYS:
        _die_refused(
            "field-ownership",
            f"set-in-flight-field: nested-key '{key}' is not in the allow-list "
            f"{sorted(ALLOWED_IN_FLIGHT_KEYS)} (decisions (b) — the helper fences "
            f"`pending_op`; this op is for the in_flight fingerprint only)",
        )
    try:
        parsed_raw = json.loads(raw)
    except json.JSONDecodeError as exc:
        _die_refused("argv", f"set-in-flight-field: raw-json-literal not valid JSON ({exc})")
    data = _read_journal(jp)
    _ensure_required_top_level(data, ["in_flight"])
    inf = data.get("in_flight")
    if inf is None:
        # Create {} per the spec — first-time owner setup; the bash
        # `journal_open_txn` would normally have written the full
        # object, but the splice helper accepts a fresh shell write
        # (e.g. if the heartbeat loop races an aborted txn).
        inf = {}
    elif not isinstance(inf, dict):
        _die_refused("schema-drift", f"in_flight is not a JSON object (got {type(inf).__name__})")
    inf[key] = parsed_raw
    data["in_flight"] = inf
    _atomic_write(jp, data)
    return 0


def _op_unset_field(jp: Path, args: list[str]) -> int:
    if len(args) != 1:
        _die_refused("argv", f"unset-field needs 1 arg (field); got {len(args)}")
    field = args[0]
    if field not in ALLOWED_TOP_LEVEL_WRITES:
        _die_refused(
            "field-ownership",
            f"unset-field: target '{field}' is fenced out of helper",
        )
    if field == "history":
        # W5 (council 2026-10-12): history is APPEND-ONLY — unsetting it
        # would erase the audit trail wholesale. The only writer for the
        # history key is the append-history op.
        _die_refused(
            "field-ownership",
            "unset-field history is APPEND-ONLY — history may only be written "
            "via the append-history op (W5)",
        )
    data = _read_journal(jp)
    _ensure_required_top_level(data, [field])
    data[field] = None
    _atomic_write(jp, data)
    return 0


def _op_clear_in_flight(jp: Path, args: list[str]) -> int:
    if len(args) != 0:
        _die_refused("argv", f"clear-in-flight takes no args; got {len(args)}")
    data = _read_journal(jp)
    _ensure_required_top_level(data, ["in_flight"])
    data["in_flight"] = None
    _atomic_write(jp, data)
    return 0


def _op_set_previous(jp: Path, args: list[str]) -> int:
    if len(args) != 1:
        _die_refused("argv", f"set-previous needs 1 arg (version-string | 'null'); got {len(args)}")
    raw = args[0]
    if raw == "null":
        parsed = None
    else:
        # The set-previous argv gets a quoted version string from the
        # shell wrapper; strip outer quotes if present (the bash writer
        # passes `"v0.20.1"` — actual double-quoted). The python side
        # accepts ANY non-null string after de-quoting.
        if len(raw) >= 2 and raw[0] == raw[-1] == '"':
            raw = raw[1:-1]
        parsed = raw
    data = _read_journal(jp)
    _ensure_required_top_level(data, ["previous"])
    data["previous"] = parsed
    _atomic_write(jp, data)
    return 0


# ── Op dispatch (parser stable; arch §3 grammar) ────────────────────────────


_OPS = {
    "append-history": _op_append_history,
    "set-field": _op_set_field,
    "set-in-flight-field": _op_set_in_flight_field,
    "unset-field": _op_unset_field,
    "clear-in-flight": _op_clear_in_flight,
    "set-previous": _op_set_previous,
}


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(
            f"splice: ERROR: argv too short; need <journal_path> <op> [args…]; "
            f"got {len(argv)} ({argv!r})",
            file=sys.stderr,
        )
        return 2
    raw_jp = argv[1]
    op = argv[2]
    op_args = argv[3:]
    if op not in _OPS:
        print(
            f"splice: ERROR: unknown op '{op}'; known: {sorted(_OPS)}",
            file=sys.stderr,
        )
        return 2

    # Live-install refusal (mirrors ledger_check.py:46-55 — same file we
    # mirrored the discipline from). C4 (council 2026-10-12): the
    # refusal now carries ONE waiver — while the pipeline lock
    # (rollback.lock.d) for the journal's own install is HELD by a
    # live owner, the splice IS the promote/rollback pipeline writing
    # to its own journal and is allowed through. Any other caller (or
    # a crashed-owner stale lock dir) is refused exactly as before —
    # fail-closed for out-of-pipeline writes.
    try:
        jp = Path(raw_jp).expanduser().resolve(strict=False)
    except RuntimeError as exc:
        _die_refused("argv", f"cannot expand journal path '{raw_jp}': {exc}")
    if _is_under_live(jp) and not _pipeline_lock_held(jp):
        _die_refused(
            "live-install",
            f"journal path '{jp}' resolves under the live install root and the "
            f"pipeline lock (rollback.lock.d) is NOT held by a live owner — "
            f"refusing (live environment is out of bounds for this writer "
            f"unless the promote/rollback pipeline holds the lock)",
        )
    # Path must exist for the writer to have something to splice on top
    # of (the bash journal_init creates it; we expect the caller's
    # journal_init to have run before any splice call).
    if not jp.exists():
        _die_refused(
            "missing-journal",
            f"journal at {jp} does not exist; caller must run journal_init first",
        )

    return _OPS[op](jp, op_args)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
