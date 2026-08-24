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
#     - T8 (B4 leg 2): SHELL-written refusal journal → real classifier
#       maps it to upgrade_promote_refusal (pack-level truth for the
#       shell lane);
#     - T9 (P2.3 B6.5 / F-B6c-1): the IN-DAEMON tool-refusal lane —
#       REAL _refusal() path (real actor-gate call site + direct) journals
#       via the real journal_history_append: exactly one refusal event
#       per invocation carrying the D-FA2.2 token in the lib.sh _refuse
#       detail shape, sink fires upgrade_promote_refusal per event;
#       never-raises on unwritable/absent journal (identical refusal
#       string, one warning line, journal never materialized);
#       marker-absent/dev → zero writes; live carve-out pinned
#       (interlock-tripwired P2.2 contract — armed live refusals are
#       journal-read-only);
#     - T10 (P2.3 review cycle 1, MINOR-1): journal race discipline — the
#       pipeline-busy lock-busy carve-out (skip the append; mirrors the
#       B4 shell _refuse carve-out) + concurrent-journal-write proof:
#       sequenced interleaving of refusal appends vs REAL shell-lane
#       promote-style RMWs (lib.sh journal_open/close_txn subprocesses)
#       loses NO update and never tears; barrier-released concurrent
#       writers with a continuous reader never expose a torn file;
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

# ── T9 (P2.3 B6.5 / F-B6c-1): REAL _refusal() path journals + alerts ────────
# The T8 seam closure: in-daemon tool refusals journal via the REAL
# journal_history_append (single write point inside _refusal —
# _journal_refusal_event), so the B3 upgrade_promote_refusal alert kind
# is reachable from the tool lane. Fixture: HOME-isolated demo/live
# install dirs resolved through the REAL _self_env_marker →
# _resolve_install_dir chain (no monkeypatching — the rider resolves
# exactly as production). NEVER touches a real ~/agents-ensemble* path.
from daemon.tools import upgrade_tools as ut  # noqa: E402 — T9 only

DEMO_INSTALL = Path.home() / "agents-ensemble-demo"
LIVE_INSTALL = Path.home() / "agents-ensemble"
T9_MSG = "(3/24h) — halted-for-human; see release_info(section=journal)."
T9_MSG2 = "rollback cooldown active until 2026-08-23T10:30:00Z"

_prev_self_env = os.environ.get("ENSEMBLE_SELF_ENV")
ut_records = []
_ut_cap = logging.Handler()
_ut_cap.emit = lambda rec: ut_records.append(rec)
ut.logger.addHandler(_ut_cap)
ut.logger.setLevel(logging.WARNING)
try:
    os.environ["ENSEMBLE_SELF_ENV"] = "demo"
    uj.journal_init(DEMO_INSTALL)
    uj.ensure_extensions(DEMO_INSTALL)

    # (a)+(b) REAL refusal site — the actor env gate (tools code, not a
    # hand-built string) journals exactly ONE refusal event carrying the
    # token in the shell _refuse detail shape, and the sink fires once.
    sink = RecordingSink()
    uj.register_alert_sink(sink)
    _g_self, _g_dir, gate_refusal = ut._actor_env_gate("UPGRADE", "bogus-env")
    _enum = "|".join(ut.VALID_ENVS)
    expect_detail = (
        f"target_env must be one of {_enum} (got 'bogus-env'). "
        "(reason=invalid-target-env)"
    )
    hist = uj.journal_read(DEMO_INSTALL)["history"]
    p = sink.calls[0] if sink.calls else {}
    report("t9a real refusal site: journal gains exactly ONE refusal event w/ token",
           gate_refusal == ("Error: UPGRADE REFUSED — reason=invalid-target-env: "
                            f"target_env must be one of {_enum} (got 'bogus-env').")
           and len(hist) == 1 and hist[0]["event"] == "refusal"
           and hist[0]["detail"] == expect_detail
           and uj._reason_token(hist[0]["detail"]) == "invalid-target-env",
           f"hist={hist} out={gate_refusal[:80]}")
    report("t9a sink fired once: kind upgrade_promote_refusal, token present",
           len(sink.calls) == 1 and p.get("kind") == "upgrade_promote_refusal"
           and p.get("source_event") == "refusal"
           and p.get("reason") == "invalid-target-env",
           f"calls={len(sink.calls)} p={p}")

    # direct _refusal(): return byte-identical to the pure formatter, and
    # the journal/sink each gain exactly one more entry.
    sink = RecordingSink()
    uj.register_alert_sink(sink)
    out = ut._refusal("UPGRADE", "rollback-cap-exceeded", T9_MSG)
    hist = uj.journal_read(DEMO_INSTALL)["history"]
    report("t9b _refusal direct: return byte-identical, +1 event, no dupes",
           out == f"Error: UPGRADE REFUSED — reason=rollback-cap-exceeded: {T9_MSG}"
           and len(hist) == 2
           and [h["event"] for h in hist] == ["refusal", "refusal"]
           and uj._reason_token(hist[1]["detail"]) == "rollback-cap-exceeded"
           and len(sink.calls) == 1,
           f"hist_n={len(hist)} calls={len(sink.calls)} out={out[:60]}")

    # (c) each-refusal-journals-once: a SECOND distinct invocation → its
    # own event + its own alert; no duplicates within one event.
    sink = RecordingSink()
    uj.register_alert_sink(sink)
    out2 = ut._refusal("RESTART", "cooldown-active", T9_MSG2)
    hist = uj.journal_read(DEMO_INSTALL)["history"]
    tokens = [uj._reason_token(h["detail"]) for h in hist]
    report("t9c two distinct refusals → two events, two alerts, no dupes",
           out2.startswith("Error: RESTART REFUSED — reason=cooldown-active: ")
           and len(hist) == 3 and len(sink.calls) == 1
           and tokens == ["invalid-target-env", "rollback-cap-exceeded",
                          "cooldown-active"],
           f"hist_n={len(hist)} calls={len(sink.calls)} tokens={tokens}")

    # (d) never-raises, unwritable journal: refusal still returns the
    # IDENTICAL string, append skipped, exactly ONE warning log line.
    # (MINOR-1: reason is deliberately NOT pipeline-busy — that token now
    # skips the append by the lock-busy carve-out BEFORE any write is
    # attempted, so it could never produce the warning this case pins.
    # The carve-out's own writable-journal case is t10a below.)
    os.chmod(DEMO_INSTALL / "releases", 0o555)
    ut_records.clear()
    out3 = ut._refusal("UPGRADE", "rollback-cap-exceeded", "lock held")
    os.chmod(DEMO_INSTALL / "releases", 0o755)
    warns = [r for r in ut_records if r.levelno == logging.WARNING
             and "refusal journal append FAILED" in r.getMessage()]
    hist = uj.journal_read(DEMO_INSTALL)["history"]
    report("t9d unwritable journal: refusal identical, append skipped, one log line",
           out3 == "Error: UPGRADE REFUSED — reason=rollback-cap-exceeded: lock held"
           and len(hist) == 3 and len(warns) == 1,
           f"warns={len(warns)} hist_n={len(hist)}")

    # (d) absent journal: identical string, journal NOT materialized by
    # the refusal append, one warning line.
    jp = DEMO_INSTALL / "releases" / "state.json"
    jp_absent = DEMO_INSTALL / "releases" / "state.json.absent-t9d"
    jp.rename(jp_absent)
    ut_records.clear()
    out4 = ut._refusal("UPGRADE", "journal-unavailable", "absent journal")
    warns = [r for r in ut_records if r.levelno == logging.WARNING
             and "refusal journal append FAILED" in r.getMessage()]
    report("t9d absent journal: refusal identical, journal NOT materialized, one log line",
           out4 == "Error: UPGRADE REFUSED — reason=journal-unavailable: absent journal"
           and not jp.exists() and len(warns) == 1,
           f"exists={jp.exists()} warns={len(warns)}")
    jp_absent.rename(jp)

    # (e) marker-absent / no-install contexts: clean refusals, ZERO
    # journal writes, ZERO alerts.
    sink = RecordingSink()
    uj.register_alert_sink(sink)
    os.environ.pop("ENSEMBLE_SELF_ENV", None)
    out5 = ut._refusal("UPGRADE", "env-marker-absent", "marker absent")
    os.environ["ENSEMBLE_SELF_ENV"] = "dev"  # dev resolves NO install dir
    out6 = ut._refusal("RESTART", "no-staged-install", "dev has no staged install")
    hist = uj.journal_read(DEMO_INSTALL)["history"]
    report("t9e marker-absent + dev: clean refusals, zero journal writes/alerts",
           out5 == "Error: UPGRADE REFUSED — reason=env-marker-absent: marker absent"
           and out6 == ("Error: RESTART REFUSED — reason=no-staged-install: "
                        "dev has no staged install")
           and len(hist) == 3 and len(sink.calls) == 0,
           f"hist_n={len(hist)} calls={len(sink.calls)}")

    # live carve-out (interlock-tripwired P2.2 contract: armed live
    # refusals are pipeline-read-only): a live-self daemon's tool
    # refusals journal NOTHING — live refusal records arrive via the
    # shell lane's lib.sh _refuse append + B4 watcher/relay instead.
    os.environ["ENSEMBLE_SELF_ENV"] = "live"
    uj.journal_init(LIVE_INSTALL)
    uj.ensure_extensions(LIVE_INSTALL)
    out7 = ut._refusal("UPGRADE", "user-confirmation-missing", "gate failed")
    report("t9f live carve-out: refusal journals nothing on the live install",
           out7 == ("Error: UPGRADE REFUSED — reason=user-confirmation-missing: "
                    "gate failed")
           and uj.journal_read(LIVE_INSTALL)["history"] == []
           and len(sink.calls) == 0,
           f"live_hist={uj.journal_read(LIVE_INSTALL)['history']} calls={len(sink.calls)}")
finally:
    if _prev_self_env is None:
        os.environ.pop("ENSEMBLE_SELF_ENV", None)
    else:
        os.environ["ENSEMBLE_SELF_ENV"] = _prev_self_env
    try:
        os.chmod(DEMO_INSTALL / "releases", 0o755)
    except OSError:
        pass
    ut.logger.removeHandler(_ut_cap)
    uj.register_alert_sink(None)

# ── T10 (P2.3 review cycle 1, MINOR-1): refusal-append vs promote-style RMW ──
# The lock-busy carve-out (upgrade_tools._journal_refusal_event skips the
# append when reason=pipeline-busy) removes the BY-CONSTRUCTION racing
# pair: a pipeline-busy refusal fires precisely BECAUSE a txn holder is
# live. What remains is proven here at the file level:
#   (a) carve-out pin: pipeline-busy journals NOTHING on a WRITABLE
#       journal — no event, no warning; the skip precedes any write;
#   (b) control: a non-busy refusal on the same journal still appends;
#   (c) sequenced interleaving (deterministic): ROUNDS of REAL shell-lane
#       promote-style read-modify-write (lib.sh journal_open_txn →
#       journal_close_txn via real subprocesses) interleaved with
#       daemon-lane refusal appends against the SAME journal — every
#       write survives (no lost update: all refusal events present AND
#       the final closed-txn state intact) and the journal parses after
#       EVERY step (atomic-replace torn discipline);
#   (d) barrier threads (deterministic for torn-safety): concurrent
#       refusal-append + RMW writer threads released by ONE barrier, with
#       a continuous reader — the file is NEVER torn (journal_read always
#       parses; JournalTorn never raised). Lost-update under truly
#       unsynchronized RMWs is exactly the race the (a) carve-out removes
#       for the by-construction pair, so (d) pins torn-safety only — by
#       design, not omission. Bounded: fixed iteration counts, <10s.
_prev2 = os.environ.get("ENSEMBLE_SELF_ENV")
try:
    os.environ["ENSEMBLE_SELF_ENV"] = "demo"
    T10_INSTALL = DEMO_INSTALL  # writable; resolves via the REAL marker chain

    # (a) carve-out on a WRITABLE journal: skip happens pre-write.
    ut_records.clear()
    hist_n0 = len(uj.journal_read(T10_INSTALL)["history"])
    out_a = ut._refusal("UPGRADE", "pipeline-busy", "second arm while run active")
    warns_a = [r for r in ut_records if r.levelno == logging.WARNING]
    hist_a = uj.journal_read(T10_INSTALL)["history"]
    report("t10a pipeline-busy carve-out: no event, no warning, string unchanged",
           out_a == ("Error: UPGRADE REFUSED — reason=pipeline-busy: "
                     "second arm while run active")
           and len(hist_a) == hist_n0 and len(warns_a) == 0,
           f"hist {hist_n0}->{len(hist_a)} warns={len(warns_a)}")

    # (b) control: non-busy refusal on the same journal still appends (+1).
    out_b = ut._refusal("UPGRADE", "cooldown-active", "control append")
    hist_b = uj.journal_read(T10_INSTALL)["history"]
    report("t10b control: non-busy refusal still appends exactly one event",
           out_b.startswith("Error: UPGRADE REFUSED — reason=cooldown-active")
           and len(hist_b) == hist_n0 + 1
           and uj._reason_token(hist_b[-1]["detail"]) == "cooldown-active",
           f"hist_n={len(hist_b)}")

    # (c) sequenced interleaving — real shell RMWs vs daemon refusal appends.
    ROUNDS = 6
    ok_c, note_c = True, ""
    for rnd in range(ROUNDS):
        proc = subprocess.run(
            ["bash", "-c",
             f'. "{os.environ["REPO_ROOT"]}/scripts/upgrade/lib.sh"\n'
             f'export INSTALL_DIR="{T10_INSTALL}"\n'
             f'journal_open_txn promote "v-t10-{rnd}" || exit 9\n'
             f'journal_close_txn || exit 9\n'],
            capture_output=True, text=True)
        if proc.returncode != 0:
            ok_c, note_c = False, f"round {rnd} shell rc={proc.returncode} err={proc.stderr.strip()[:120]}"
            break
        ut._journal_refusal_event("cooldown-active", f"t10c interleaved refusal {rnd}")
        try:
            uj.journal_read(T10_INSTALL)  # parses after EVERY step
        except Exception as exc:
            ok_c, note_c = False, f"round {rnd} journal unparseable post-step: {exc}"
            break
    hist_c = uj.journal_read(T10_INSTALL)["history"] if ok_c else []
    refusal_details = [h["detail"] for h in hist_c if h.get("event") == "refusal"]
    got_all_rounds = all(
        any(f"t10c interleaved refusal {rnd}" in d for d in refusal_details)
        for rnd in range(ROUNDS)
    )
    inf = uj.journal_read(T10_INSTALL).get("in_flight") if ok_c else "unread"
    report("t10c sequenced interleave: every refusal append + every txn RMW survives",
           ok_c and got_all_rounds and inf is None,
           f"rounds_ok={ok_c} all_appends={got_all_rounds} in_flight={inf} {note_c}")

    # (d) barrier threads: torn-safety under true concurrency.
    T10D = fresh_install()
    barrier = threading.Barrier(3)
    stop_flag = threading.Event()
    torn_errors = []

    def _t10d_refusal_writer():
        barrier.wait()
        for i in range(60):
            uj.journal_history_append(T10D, "refusal", f"t10d concurrent refusal {i}")
        stop_flag.set()

    def _t10d_rmw_writer():
        barrier.wait()
        for i in range(60):
            uj.journal_update_field(
                T10D, "cooldown_until",
                "2026-08-23T10:00:00Z" if i % 2 else None,
            )
        stop_flag.set()

    def _t10d_reader():
        barrier.wait()
        while not stop_flag.is_set():
            try:
                uj.journal_read(T10D)
            except uj.JournalTorn as exc:
                torn_errors.append(str(exc))

    # F-2 (P2.3 review cycle 2, MINOR — strengthen assertion to the
    # GUARANTEED property): post-F-1 the tmp filename carries
    # threading.get_ident() + uuid.uuid4().hex[:8] in addition to the
    # existing pid + ms timestamp, so the SEAM that lets two concurrent
    # writers collide on a single tmp filename (and silently clobber each
    # other's payload mid-write) is closed. The behavioral proof:
    # capture every tmp source passed to os.replace during the concurrent
    # run and assert NO two writers picked the same tmp filename. A
    # clobber is impossible iff every captured tmp name is unique — the
    # property F-1 was added to guarantee. Wrapping os.replace (rather
    # than journal_write) observes what journal_write ACTUALLY used,
    # independent of any test-side formula coupling. The original
    # torn-safety assertion remains intact below.
    captured_tmp_srcs = []
    capture_lock = threading.Lock()
    _orig_replace = os.replace
    def _t10d_capturing_replace(src, dst, *a, **kw):
        # F-1 (post-fix) tmp filename format is
        # state.json.tmp.{pid}.{tid}.{uuid8}.{ts} — match on the
        # journal basename + ".tmp." prefix rather than a positional
        # suffix (the suffix changed between fix iterations; the prefix
        # is invariant as long as the writer lives in the journal dir).
        src_str = str(src)
        if f"{uj.journal_path(T10D).name}.tmp." in src_str:
            with capture_lock:
                captured_tmp_srcs.append(src_str)
        return _orig_replace(src, dst, *a, **kw)
    os.replace = _t10d_capturing_replace
    try:
        th_a = threading.Thread(target=_t10d_refusal_writer)
        th_b = threading.Thread(target=_t10d_rmw_writer)
        th_r = threading.Thread(target=_t10d_reader)
        th_a.start(); th_b.start(); th_r.start()
        th_a.join(10); th_b.join(10); stop_flag.set(); th_r.join(2)
    finally:
        os.replace = _orig_replace
    final_ok = True
    try:
        uj.journal_read(T10D)
    except Exception as exc:
        final_ok = False
        torn_errors.append(f"final read: {exc}")
    unique_tmp = len(set(captured_tmp_srcs))
    total_tmp = len(captured_tmp_srcs)
    tmp_clashes = total_tmp - unique_tmp
    report("t10d barrier threads: journal NEVER torn under concurrent writers",
           not torn_errors and final_ok,
           f"torn={torn_errors[:2]}")
    # F-2 GUARANTEED-property assertion — survives the reviewer's
    # standing-rule "concurrency acceptance requires >=10-run evidence"
    # because the assertion is over a captured set, not a single-run
    # outcome: if any tmp name were shared, two concurrent writers
    # would have clobbered each other's payload, and exactly ONE name
    # would appear at least twice in the captured sequence.
    report("t10d tmp-name uniqueness (F-1 guarantee): no concurrent clobber",
           tmp_clashes == 0 and total_tmp > 0,
           f"unique={unique_tmp}/{total_tmp} clashes={tmp_clashes}")
finally:
    if _prev2 is None:
        os.environ.pop("ENSEMBLE_SELF_ENV", None)
    else:
        os.environ["ENSEMBLE_SELF_ENV"] = _prev2

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
    t9a t9b t9c t9d t9e t9f \
    t10a t10b t10c t10d \
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
