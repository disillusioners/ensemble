#!/usr/bin/env bash
# test/packs/upgrade_alerting_unit_test.sh
#
# Pack: upgrade_alerting_unit_test
# Scope: Self-Restart/Self-Upgrade P2.3 B3 (T8a) — deterministic SSE alert
#   emission on terminal-class upgrade journal events
#   (daemon/tools/upgrade_journal.py sink registry + emission hooks):
#     - per-event correctness: halt → upgrade_cap_halt, refusal →
#       upgrade_promote_refusal (reason token parsed per D-FA2.2),
#       rollback / sweep_rollback / quarantine → upgrade_auto_rollback —
#       exactly ONE alert per history event, payload journal-derived
#       (kind, source_event, reason, detail, version=current,
#       counters=rollback_window_count, cooldown_until, quarantined list,
#       run_id from in_flight, ts == the history entry ts);
#     - journal-history integration: the alert fires ON the real
#       journal_history_append — history contains the corresponding
#       entry in the same fixture;
#     - no-emission negatives (R3.4 terminal-class only): staged,
#       commit, restart, sweep, nonce_consumed (real
#       consume_pending_action), arm-class field writes
#       (journal_update_field in_flight + write_pending_op), idempotent
#       re-stage, adopt-without-rollback (real reconcile_pending_op
#       expiry-closure → sweep event) — ZERO alerts;
#     - once-per-event discipline: a second identical refusal appends
#       its own event → its own alert; no duplicates within one event;
#     - never-raises proof: a RAISING sink does not fail the journal
#       write (history still appended, exactly one WARNING log line);
#       a raising sink is never even called for ordinary events;
#     - sink absent: with no registration (register_alert_sink(None)),
#       journal writes work unchanged, zero errors, zero alerts;
#     - boot-registration seam semantics: last-wins replacement,
#       register returns the PREVIOUS sink, None resets to no-op;
#     - broadcaster bridge (broadcaster_alert_sink): loop-thread append
#       AND worker-thread append both deliver via
#       NotificationBroadcaster.emit (event_type=kind, data=payload);
#       closed-loop call drops the alert without raising;
#     - zero-live-port-literal self-check on the new/changed files
#       (fragment-built pattern per drill_ledger pack; prose says
#       "live port").
#   The recording fake sink registers via the REAL register_alert_sink
#   API — no monkeypatching past the seam. Fixtures: HOME-isolated
#   mktemp install dirs with real journal_init/ensure_extensions state
#   (shapes per tests/test_release_journal.sh / interlock-pack journal
#   fixtures). No daemon, no DB, no network, zero live contact.
# Internal watchdog (Layer 2): 120s — unit-type limit per test-pack skill.
# Layer 1 (outer) is the dispatcher's `timeout 300` wrap.
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Self-contained battery (no separate tests/ file): the wrapper re-invokes
# itself with --battery under the Layer-2 watchdog.
# Ref: agents-ensemble test-pack skill (dual-layer timeout, explicit RESULT).

set -u

# ─── wrapper (Layer 2) ───────────────────────────────────────────────────────
if [ "${1:-}" != "--battery" ]; then
    cd "$(dirname "$0")/../.." || {
        echo "FAIL: cannot cd to repo root"
        echo "RESULT: FAIL"
        exit 1
    }

    PACK_NAME="upgrade_alerting_unit_test"
    echo "=== Test Pack: ${PACK_NAME} ==="
    echo "Repo:    $(pwd)"
    echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo

    OUT="$(mktemp)"
    set -o pipefail
    timeout 120s bash "$PWD/test/packs/upgrade_alerting_unit_test.sh" --battery 2>&1 | tee "$OUT"
    RC=$?

    SUMMARY="$(grep -E '== summary: [0-9]+ passed' "$OUT" | tail -1)"
    rm -f "$OUT"
    if [ -n "$SUMMARY" ]; then echo "SUMMARY: $SUMMARY"; fi

    echo
    if [ "$RC" -eq 124 ]; then
        echo "RESULT: TIMEOUT"
        exit 124
    elif [ "$RC" -eq 0 ]; then
        echo "RESULT: PASS"
        exit 0
    else
        echo "RESULT: FAIL (exit=${RC})"
        exit 1
    fi
fi

# ─── battery ─────────────────────────────────────────────────────────────────
cd "$(dirname "$0")/../.." || { echo "FAIL: cannot cd to repo root"; exit 1; }

REPO_ROOT="$(pwd)"
JOURNAL_MODULE="$REPO_ROOT/daemon/tools/upgrade_journal.py"
API_FILE="$REPO_ROOT/daemon/api.py"
PACK_SELF="$REPO_ROOT/test/packs/upgrade_alerting_unit_test.sh"
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3

PASS=0
FAIL=0
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); }

_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
}

assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass; else _fail "$name" "$expected" "$actual"; fi
}

assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass ;;
        *) _fail "$name" "contains '$needle'" "$haystack" ;;
    esac
}

assert_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _fail "$name" "absent '$needle'" "$haystack" ;;
        *) _pass ;;
    esac
}

section() { printf '\n== %s ==\n' "$1"; }

# ─── fixture root (HOME-isolated; per-scenario install dirs made in Python) ──
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/upgrade-alerting.XXXXXX")"
FIXTURE="$(cd "$FIXTURE" && pwd)"
FAKE_HOME="$FIXTURE/home"
mkdir -p "$FAKE_HOME"
cleanup() { rm -rf "$FIXTURE"; }
trap cleanup EXIT

# ═══ T1–T6: python driver over the REAL emission path ══════════════════════
# One process, N numbered scenarios; each registers its own sink through
# the real register_alert_sink API (last-wins replaceable — the replace
# itself is exercised by every scenario boundary).
section "T1-T6 driver (real register_alert_sink + journal_history_append)"

cat > "$FIXTURE/driver.py" <<'PYEOF'
"""P2.3 B3 (T8a) upgrade_alerting pack driver — real emission path."""
import asyncio
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, os.environ["REPO_ROOT"])
from daemon.tools import upgrade_journal as uj  # noqa: E402

FIXTURE = Path(os.environ["ALERT_FIXTURE"])
FAILURES = []


def report(sid, ok, note=""):
    # "<code> <description>" → canonical "SCENARIO <code>: PASS|FAIL — ..." line
    code = sid.split(" ", 1)[0]
    status = "PASS" if ok else "FAIL"
    suffix = f" | {note}" if note else ""
    print(f"SCENARIO {code}: {status} — {sid}{suffix}", flush=True)
    if not ok:
        FAILURES.append(sid)


def fresh_install(seed=None):
    """mktemp install dir + REAL journal_init/ensure_extensions, then
    optional field seed (shapes per tests/test_release_journal.sh)."""
    d = Path(tempfile.mkdtemp(dir=str(FIXTURE)))
    uj.journal_init(d)
    uj.ensure_extensions(d)
    if seed:
        data = uj.journal_read(d)
        data.update(seed)
        uj.journal_write(d, data)
    return d


class RecordingSink:
    def __init__(self):
        self.calls = []

    def __call__(self, payload):
        self.calls.append(dict(payload))


class RaisingSink:
    def __init__(self):
        self.calls = 0

    def __call__(self, payload):
        self.calls += 1
        raise RuntimeError("sink boom")


class LogCapture:
    def __init__(self):
        self.records = []
        self.handler = logging.Handler()
        self.handler.emit = lambda rec: self.records.append(rec)

    def attach(self):
        uj.logger.addHandler(self.handler)
        uj.logger.setLevel(logging.WARNING)

    def detach(self):
        uj.logger.removeHandler(self.handler)


CAP_HALT_DETAIL = (
    "rollback cap 3/24h reached (count=3) — halt-for-human; promotes "
    "refused until the window resets"
)
REFUSAL_DETAIL = "promote refused — reason=cooldown-active: rollback cooldown armed"
ROLLBACK_DETAIL = "auto-rollback v2.1.0 → v2.0.0 (gate fail: readyz-timeout; re-gate green)"
QUARANTINE_DETAIL = "v2.1.0 quarantined after gate failure (skipped by future promotes)"
SEEDED_IN_FLIGHT = {
    "kind": "promote",
    "target": "v2.1.0",
    "started_at": "2026-08-23T12:00:00Z",
    "flipped": True,
    "owner_pid": 4242,
    "run_id": "r-20260823-120000-abcd",
}

# always start from the documented default (silent no-op)
uj.register_alert_sink(None)

# ── T1: per-event correctness (kind + journal-derived payload fields) ──────
sink = RecordingSink()
report("t0a register returns previous (None default)",
       uj.register_alert_sink(sink) is None)
uj.register_alert_sink(sink)

d = fresh_install(seed={
    "current": "v2.0.0",
    "rollback_window_count": {"24h": 3, "window_start": "2026-08-23T09:00:00Z"},
    "cooldown_until": "2026-08-23T10:30:00Z",
    "quarantined": ["v2.1.0", "v2.1.1"],
    "in_flight": None,
})
uj.journal_history_append(d, "halt", CAP_HALT_DETAIL)
p = sink.calls[0] if sink.calls else {}
report("t1a halt fires exactly once",
       len(sink.calls) == 1, f"calls={len(sink.calls)}")
report("t1a kind upgrade_cap_halt", p.get("kind") == "upgrade_cap_halt", str(p.get("kind")))
report("t1a source_event halt", p.get("source_event") == "halt", str(p.get("source_event")))
report("t1a version=current (journal-derived)", p.get("version") == "v2.0.0", str(p.get("version")))
report("t1a counters carried", p.get("counters") == {"24h": 3, "window_start": "2026-08-23T09:00:00Z"}, str(p.get("counters")))
report("t1a cooldown_until carried", p.get("cooldown_until") == "2026-08-23T10:30:00Z", str(p.get("cooldown_until")))
report("t1a quarantined list carried", p.get("quarantined") == ["v2.1.0", "v2.1.1"], str(p.get("quarantined")))
report("t1a run_id None when no open txn", p.get("run_id") is None, str(p.get("run_id")))
report("t1a detail verbatim", p.get("detail") == CAP_HALT_DETAIL, str(p.get("detail"))[:60])
report("t1a ts present", isinstance(p.get("ts"), str) and p["ts"], str(p.get("ts")))

sink = RecordingSink()
uj.register_alert_sink(sink)
d = fresh_install(seed={"current": "v2.0.0", "in_flight": SEEDED_IN_FLIGHT})
uj.journal_history_append(d, "refusal", REFUSAL_DETAIL)
p = sink.calls[0] if sink.calls else {}
report("t1b refusal fires exactly once", len(sink.calls) == 1, f"calls={len(sink.calls)}")
report("t1b kind upgrade_promote_refusal", p.get("kind") == "upgrade_promote_refusal", str(p.get("kind")))
report("t1b reason token parsed", p.get("reason") == "cooldown-active", str(p.get("reason")))
report("t1b run_id from open txn", p.get("run_id") == "r-20260823-120000-abcd", str(p.get("run_id")))
report("t1b source_event refusal", p.get("source_event") == "refusal", str(p.get("source_event")))

sink = RecordingSink()
uj.register_alert_sink(sink)
d = fresh_install(seed={"current": "v2.0.0", "in_flight": SEEDED_IN_FLIGHT})
uj.journal_history_append(d, "rollback", ROLLBACK_DETAIL)
p = sink.calls[0] if sink.calls else {}
report("t1c rollback → upgrade_auto_rollback once",
       len(sink.calls) == 1 and p.get("kind") == "upgrade_auto_rollback",
       f"calls={len(sink.calls)} kind={p.get('kind')}")
report("t1c no reason token in detail → None", p.get("reason") is None, str(p.get("reason")))

sink = RecordingSink()
uj.register_alert_sink(sink)
d = fresh_install()
uj.journal_history_append(d, "sweep_rollback",
                          "adopt: orphaned flipped promote txn rolled back (ADR-024)")
p = sink.calls[0] if sink.calls else {}
report("t1d sweep_rollback → upgrade_auto_rollback",
       len(sink.calls) == 1 and p.get("kind") == "upgrade_auto_rollback"
       and p.get("source_event") == "sweep_rollback",
       f"calls={len(sink.calls)} kind={p.get('kind')} src={p.get('source_event')}")

sink = RecordingSink()
uj.register_alert_sink(sink)
d = fresh_install(seed={"quarantined": ["v2.1.0"]})
uj.journal_history_append(d, "quarantine", QUARANTINE_DETAIL)
p = sink.calls[0] if sink.calls else {}
report("t1e quarantine → upgrade_auto_rollback w/ complete list",
       len(sink.calls) == 1 and p.get("kind") == "upgrade_auto_rollback"
       and p.get("quarantined") == ["v2.1.0"],
       f"calls={len(sink.calls)} q={p.get('quarantined')}")

# ── T2: journal-history integration (alert fires ON the append) ─────────────
sink = RecordingSink()
uj.register_alert_sink(sink)
d = fresh_install()
uj.journal_history_append(d, "halt", CAP_HALT_DETAIL)
hist = uj.journal_read(d)["history"]
p = sink.calls[0] if sink.calls else {}
report("t2a history has the halt entry (same fixture)",
       len(hist) == 1 and hist[0]["event"] == "halt" and hist[0]["detail"] == CAP_HALT_DETAIL,
       f"hist={hist}")
report("t2a alert ts == history entry ts", bool(p) and p.get("ts") == hist[0]["ts"],
       f"payload.ts={p.get('ts')} hist.ts={hist[0]['ts']}")

# ── T3: no-emission negatives (ordinary writes emit NOTHING) ────────────────
sink = RecordingSink()
uj.register_alert_sink(sink)
d = fresh_install()
uj.journal_history_append(d, "staged", "v2.1.0 staged (manifest ok)")
uj.journal_history_append(d, "commit", "promote v2.0.0 committed (gate+soak green)")
uj.journal_history_append(d, "restart", "intentional restart run_id=r-1 complete")
uj.journal_history_append(d, "sweep", "stale pre-flip txn cleared")
uj.journal_history_append(d, "nonce_consumed", "nonce for run_id=r-1 consumed")
report("t3a staged/commit/restart/sweep/nonce_consumed → ZERO alerts",
       len(sink.calls) == 0, f"calls={len(sink.calls)}")

d = fresh_install()
uj.journal_history_append(d, "staged", "v2.1.0 staged (manifest ok)")
uj.journal_history_append(d, "staged", "v2.1.0 staged (idempotent re-stage)")
report("t3b idempotent re-stage → ZERO alerts", len(sink.calls) == 0, f"calls={len(sink.calls)}")

d = fresh_install()
action = uj.PendingAction(run_id="r-nonce", nonce="CONFIRM-ABCDEFGH", kind="upgrade",
                          env="demo", target="v2.1.0")
uj.store_pending_action(d, action)
uj.consume_pending_action(d, action, "msg-1")
hist = uj.journal_read(d)["history"]
report("t3c real consume_pending_action → nonce event, ZERO alerts",
       len(sink.calls) == 0 and any(h["event"] == "nonce_consumed" for h in hist),
       f"calls={len(sink.calls)} events={[h['event'] for h in hist]}")

d = fresh_install()
op = uj.PendingOp(run_id="r-expired", kind="promote", env="demo", target="v2.1.0",
                  armed_at="2026-08-23T00:00:00Z", expires_at="2026-08-23T00:10:00Z")
uj.write_pending_op(d, op)
note = uj.reconcile_pending_op(d)
hist = uj.journal_read(d)["history"]
report("t3d adopt-without-rollback (real reconcile expiry-closure) → sweep event, ZERO alerts",
       len(sink.calls) == 0 and note is not None
       and any(h["event"] == "sweep" for h in hist),
       f"calls={len(sink.calls)} note={note!r} events={[h['event'] for h in hist]}")

d = fresh_install()
uj.journal_update_field(d, "in_flight", dict(SEEDED_IN_FLIGHT))
op = uj.PendingOp(run_id="r-arm", kind="promote", env="demo", target="v2.1.0")
uj.write_pending_op(d, op)
report("t3e arm-class writes (in_flight field + pending_op) → ZERO alerts",
       len(sink.calls) == 0, f"calls={len(sink.calls)}")

sink = RecordingSink()
uj.register_alert_sink(sink)
d = fresh_install()
uj.journal_history_append(d, "refusal", REFUSAL_DETAIL)
uj.journal_history_append(d, "refusal", REFUSAL_DETAIL)
kinds = [c["kind"] for c in sink.calls]
report("t3f second identical refusal → its OWN alert (2 events = 2 alerts)",
       len(sink.calls) == 2 and kinds == ["upgrade_promote_refusal"] * 2,
       f"calls={len(sink.calls)} kinds={kinds}")

# ── T4: never-raises (journal PRIMARY, alert best-effort) ───────────────────
boom = RaisingSink()
cap = LogCapture()
cap.attach()
try:
    uj.register_alert_sink(boom)
    d = fresh_install()
    uj.journal_history_append(d, "halt", CAP_HALT_DETAIL)  # must NOT raise
    hist = uj.journal_read(d)["history"]
    warns = [r for r in cap.records
             if r.levelno == logging.WARNING and "alert sink FAILED" in r.getMessage()]
    report("t4a raising sink: journal write SURVIVES (halt entry present)",
           len(hist) == 1 and hist[0]["event"] == "halt", f"hist={[h['event'] for h in hist]}")
    report("t4a raising sink called exactly once (once per event)",
           boom.calls == 1, f"calls={boom.calls}")
    report("t4a exactly one WARNING log line",
           len(warns) == 1, f"warnings={len(warns)}")
finally:
    cap.detach()

boom = RaisingSink()
uj.register_alert_sink(boom)
d = fresh_install()
uj.journal_history_append(d, "staged", "v2.1.0 staged (manifest ok)")
report("t4b raising sink never even called for ordinary events",
       boom.calls == 0, f"calls={boom.calls}")

# ── T5: sink absent (no registration) = silent no-op, writes unchanged ──────
prev = uj.register_alert_sink(None)
report("t5a register(None) returns the live sink being replaced",
       prev is boom, f"prev_is_boom={prev is boom}")
d = fresh_install()
uj.journal_history_append(d, "halt", CAP_HALT_DETAIL)
uj.journal_history_append(d, "refusal", REFUSAL_DETAIL)
uj.journal_history_append(d, "rollback", ROLLBACK_DETAIL)
hist = [h["event"] for h in uj.journal_read(d)["history"]]
report("t5a sink absent: all 3 terminal writes land, zero errors",
       hist == ["halt", "refusal", "rollback"], f"hist={hist}")

# ── T6: seam semantics (last-wins) + broadcaster bridge ─────────────────────
s1, s2 = RecordingSink(), RecordingSink()
prev = uj.register_alert_sink(s1)
report("t6a last-wins: register(s1) prev is None (post-reset)", prev is None, f"prev={prev!r}")
prev = uj.register_alert_sink(s2)
report("t6a last-wins: register(s2) prev is s1", prev is s1, f"prev_is_s1={prev is s1}")
d = fresh_install()
uj.journal_history_append(d, "refusal", REFUSAL_DETAIL)
report("t6a replacement effective: s2 fired, s1 silent",
       len(s2.calls) == 1 and len(s1.calls) == 0,
       f"s1={len(s1.calls)} s2={len(s2.calls)}")


class FakeBroadcaster:
    def __init__(self):
        self.events = []

    async def emit(self, notification):
        self.events.append(notification)
        return len(self.events)


async def bridge_main():
    b = FakeBroadcaster()
    uj.register_alert_sink(uj.broadcaster_alert_sink(b))
    d1 = fresh_install()
    uj.journal_history_append(d1, "halt", CAP_HALT_DETAIL)  # loop thread
    d2 = fresh_install(seed={"in_flight": SEEDED_IN_FLIGHT})

    def worker():  # non-loop thread — the threadsafe hop path
        uj.journal_history_append(d2, "refusal", REFUSAL_DETAIL)

    th = threading.Thread(target=worker)
    th.start()
    th.join()
    await asyncio.sleep(0.1)
    return b, d2


b, d2 = asyncio.run(bridge_main())
ok_b = (len(b.events) == 2
        and b.events[0]["event_type"] == "upgrade_cap_halt"
        and b.events[0]["data"]["kind"] == "upgrade_cap_halt"
        and b.events[0]["timestamp"]
        and b.events[1]["event_type"] == "upgrade_promote_refusal"
        and b.events[1]["data"]["reason"] == "cooldown-active")
report("t6b bridge: loop-thread + worker-thread appends both delivered via emit",
       ok_b, f"events={[(e.get('event_type'), e.get('data', {}).get('kind')) for e in b.events]}")

holder = {}


async def build_sink_only():
    holder["sink"] = uj.broadcaster_alert_sink(FakeBroadcaster())


asyncio.run(build_sink_only())  # loop closed on return
try:
    holder["sink"]({"kind": "upgrade_cap_halt", "ts": "2026-08-23T16:00:00Z"})
    report("t6c closed-loop sink call drops the alert WITHOUT raising", True)
except Exception as exc:  # pragma: no cover — failure path
    report("t6c closed-loop sink call drops the alert WITHOUT raising", False, repr(exc))

# ── T8 (P2.3 B4 leg 2): SHELL-written refusal journal → real classifier ────
# The shell entry-refusal helper (lib.sh _refuse) journals `refusal` events
# from OUTSIDE the daemon process; this scenario proves the REAL Python
# side maps such a shell-written entry to the SSE kind upgrade_promote_refusal
# with the D-FA2.2 reason token — the dormant kind goes live the moment a
# shell refusal lands in the journal. The shell writes via the REAL lib.sh
# journal_history_append (no daemon, no network); Python reads it back with
# the REAL journal_read and replays the post-write observation hook on the
# actual entry (the daemon-outside writer cannot hop the in-process sink).
import subprocess  # noqa: E402 — T8 only, keeps the diff surgical

d = fresh_install()
shell_detail = (
    "promote refused: rollback cooldown active until 2026-08-23T10:30:00Z "
    "(ADR-005: 10-min anti-flapping) (reason=cooldown)"
)
proc = subprocess.run(
    ["bash", "-c",
     f'. "{os.environ["REPO_ROOT"]}/scripts/upgrade/lib.sh"\n'
     f'export INSTALL_DIR="{d}"\n'
     f'journal_init\n'
     f'journal_history_append refusal {shell_detail!r}\n'],
    capture_output=True, text=True,
)
data = uj.journal_read(d)
hist = data.get("history") or []
ok_hist = (proc.returncode == 0 and len(hist) == 1
           and hist[0].get("event") == "refusal"
           and hist[0].get("detail") == shell_detail)
report("t8a shell-written refusal entry reads back via real journal_read",
       ok_hist, f"rc={proc.returncode} stderr={proc.stderr.strip()[:120]} hist={hist}")

kind = uj.ALERT_KIND_BY_EVENT.get(hist[0]["event"]) if ok_hist else None
report("t8b real classifier maps shell refusal → upgrade_promote_refusal",
       kind == "upgrade_promote_refusal", str(kind))
report("t8c D-FA2.2 reason token parsed from the shell detail",
       ok_hist and uj._reason_token(hist[0]["detail"]) == "cooldown",
       str(uj._reason_token(hist[0]["detail"]) if ok_hist else None))

sink = RecordingSink()
uj.register_alert_sink(sink)
if ok_hist:
    uj._emit_terminal_class_alert(data, hist[0]["event"], hist[0]["detail"], hist[0]["ts"])
p = sink.calls[0] if sink.calls else {}
report("t8d shell refusal entry → real emission hook → SSE payload",
       p.get("kind") == "upgrade_promote_refusal"
       and p.get("source_event") == "refusal"
       and p.get("reason") == "cooldown"
       and p.get("detail") == shell_detail,
       str(p))

print(f"DRIVER {'FAIL' if FAILURES else 'PASS'} ({len(FAILURES)} failed)", flush=True)
sys.exit(1 if FAILURES else 0)
PYEOF

BATT_OUT="$(HOME="$FAKE_HOME" REPO_ROOT="$REPO_ROOT" ALERT_FIXTURE="$FIXTURE" "$PY" "$FIXTURE/driver.py" 2>"$FIXTURE/driver-stderr.txt")"
DRIVER_RC=$?
DRIVER_ERR="$(cat "$FIXTURE/driver-stderr.txt")"

# granular per-scenario assertions (each code = one PASS-presence + one
# FAIL-absence pack assertion over the driver's SCENARIO lines)
for SCEN in \
    t0a \
    t1a t1b t1c t1d t1e \
    t2a \
    t3a t3b t3c t3d t3e t3f \
    t4a t4b \
    t5a \
    t6a t6b t6c \
    t8a t8b t8c t8d \
; do
    assert_contains "scenario $SCEN pass line" "SCENARIO ${SCEN}: PASS" "$BATT_OUT"
    assert_not_contains "scenario $SCEN no fail line" "SCENARIO ${SCEN}: FAIL" "$BATT_OUT"
done
assert_eq "driver exit 0" "0" "$DRIVER_RC"
assert_contains "driver summary line" "DRIVER PASS (0 failed)" "$BATT_OUT"

# ═══ T7: zero live-port literals (fragment-built pattern, drill_ledger ═════
# convention — this pack never contains the contiguous digits either; prose
# says "live port"). File-level on the journal module + this pack (both are
# clean by construction); diff-added-lines check on api.py (only OUR new
# lines are in scope — pre-existing lines are not this batch's to police).
section "T7 zero-live-port-literal self-check"
LIVE_PORT_LITERAL="$(printf '%s%s' 9 797)"
if grep -n -- "$LIVE_PORT_LITERAL" "$JOURNAL_MODULE" >/dev/null 2>&1; then
    _fail "t7a journal module free of live-port literals" "absent" "$(grep -n -- "$LIVE_PORT_LITERAL" "$JOURNAL_MODULE")"
else
    _pass
fi
if grep -n -- "$LIVE_PORT_LITERAL" "$PACK_SELF" >/dev/null 2>&1; then
    _fail "t7b pack free of live-port literals" "absent" "$(grep -n -- "$LIVE_PORT_LITERAL" "$PACK_SELF")"
else
    _pass
fi
API_ADDED="$(git -C "$REPO_ROOT" diff -U0 -- daemon/api.py | grep '^+' || true)"
if [ -n "$API_ADDED" ]; then
    # working-tree diff exists → police the added lines
    if printf '%s' "$API_ADDED" | grep -- "$LIVE_PORT_LITERAL" >/dev/null 2>&1; then
        _fail "t7c api.py added lines free of live-port literals" "absent" "literal present"
    else
        _pass
    fi
else
    # committed state (post-merge run): whole-file check (api.py carries no
    # live-port literal today, so this stays green)
    if grep -n -- "$LIVE_PORT_LITERAL" "$API_FILE" >/dev/null 2>&1; then
        _fail "t7c api.py free of live-port literals" "absent" "$(grep -n -- "$LIVE_PORT_LITERAL" "$API_FILE")"
    else
        _pass
    fi
fi
# same rule for the journal module's own diff-added lines (the file-level
# t7a covers the committed shape; this covers the working-tree delta)
JOURNAL_ADDED="$(git -C "$REPO_ROOT" diff -U0 -- daemon/tools/upgrade_journal.py | grep '^+' || true)"
if [ -n "$JOURNAL_ADDED" ] && printf '%s' "$JOURNAL_ADDED" | grep -- "$LIVE_PORT_LITERAL" >/dev/null 2>&1; then
    _fail "t7d journal module added lines free of live-port literals" "absent" "literal present"
else
    _pass
fi

# ─── summary ────────────────────────────────────────────────────────────────
printf '\n== summary: %d passed, %d failed ==\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'failed:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
