#!/usr/bin/env bash
# tests/packs/preflight_stage_lock_test.sh — Pack F (promote-preflight-hang
# phase 4: the stage.sh lock-dir leak / reclaim audit).
#
# Scope (SC8 + AC1-AC3):
#   F1  reproduce     — a SIGKILLed lock holder leaves the lock dir behind
#                       (the D4(a) orphan class: the release is trap-only).
#   F2  reclaim audit — the next acquisition reclaims it and journals ONE
#                       `lock-stale-break` row with ALL FIVE fields (dead
#                       owner pid, run_id, heartbeat age, reason,
#                       reclaiming run_id) — both break paths; a LIVE
#                       owner's lock is NEVER broken.
#   F3  stage success — an explicit release on the success path (structural
#                       pin at stage.sh's exit + a functional double-release
#                       no-op leg).
#   F4  fence interop — the reclaim row lands even on a LIVE-SHAPED path
#                       (the row is written AFTER acquisition, when the new
#                       owner holds the lock — the splice fence's exact
#                       requirement).
#
# FENCE: tests/helpers/preflight_pack_common.sh. No self-exec/mutations.
#
# Usage: bash tests/packs/preflight_stage_lock_test.sh
# Exit codes: 0=PASS, 1=FAIL, 2=FATAL.

set -u
PF_PACK_NAME="preflight-stage-lock"
. "$(cd "$(dirname "$0")/.." && pwd)/helpers/preflight_pack_common.sh"

pf_fence_init
trap pf_fence_cleanup EXIT

J="$INSTALL_DIR/releases/state.json"
mkjournal() {
    /usr/bin/python3 - "${1:-$J}" <<'PY'
import sys
open(sys.argv[1], "wb").write(b'{"current":null,"previous":null,"in_flight":null,'
    b'"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,'
    b'"quarantined":[],"history":[]}')
PY
}
count_rows() { grep -o "\"event\":\"$2\"" "$1" 2>/dev/null | wc -l | tr -d ' '; }
LOCKDIR="$INSTALL_DIR/releases/rollback.lock.d"

printf '== F1: reproduce the orphan (SIGKILLed holder) ==\n'
mkjournal
cat > "$PF_SB/holder.sh" <<'EOS'
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
LOG_TAG=pf-hold
INSTALL_DIR="$H_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
_trap_install_signal_handlers "promote:preflight"
lock_acquire || exit 78
echo "HELD pid=$$ run=$lock_run_id"
sleep 60
EOS
REPO_ROOT="$PF_REPO_ROOT" H_INSTALL="$INSTALL_DIR" bash "$PF_SB/holder.sh" > "$PF_SB/hold.out" 2>/dev/null &
HOLDER=$!
for _ in $(seq 1 50); do [ -d "$LOCKDIR" ] && break; sleep 0.1; done
[ -d "$LOCKDIR" ] && pf_ok "lock acquired by the holder" || pf_bad "F1 holder never acquired"
kill -KILL "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null
[ -d "$LOCKDIR" ] && pf_ok "SIGKILL leaves the lock dir behind (trap bypassed — the D4(a) orphan reproduces)" \
                  || pf_bad "F1 the lock dir vanished on SIGKILL (unexpected)"

printf '\n== F2: reclaim + the lock-stale-break audit row ==\n'
OLD_OWNER="$(cat "$LOCKDIR/owner" 2>/dev/null)"
OLD_RUN="$(cat "$LOCKDIR/run_id" 2>/dev/null)"
cat > "$PF_SB/reclaim.sh" <<'EOS'
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
LOG_TAG=pf-rec
INSTALL_DIR="$H_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
lock_acquire || { echo "BUSY"; exit 1; }
echo "GOT run=$lock_run_id"
lock_release
EOS
REPO_ROOT="$PF_REPO_ROOT" H_INSTALL="$INSTALL_DIR" bash "$PF_SB/reclaim.sh" > "$PF_SB/rec.out" 2> "$PF_SB/rec.err"
REC_RC=$?
[ "$REC_RC" = "0" ] && pf_ok "the dead-owner lock was RECLAIMED (rc=0)" || pf_bad "F2 reclaim failed: $(cat "$PF_SB/rec.err")"
SBR="$(count_rows "$J" lock-stale-break)"
[ "$SBR" = "1" ] && pf_ok "exactly ONE lock-stale-break row" || pf_bad "F2 rows=$SBR want 1"
DET="$(/usr/bin/python3 -c '
import json,sys
j=json.load(open(sys.argv[1]))
h=[r for r in j["history"] if r.get("event")=="lock-stale-break"]
print(h[-1]["detail"] if h else "")' "$J")"
case "$DET" in
    "reclaimed owner pid=$OLD_OWNER run_id=$OLD_RUN heartbeat_age="*"reason=dead-owner reclaiming_run_id=run-"*)
        pf_ok "row carries ALL five fields (owner=$OLD_OWNER run=$OLD_RUN age reason reclaiming)" ;;
    *) pf_bad "F2 row shape wrong: $DET" ;;
esac
[ ! -d "$LOCKDIR" ] && pf_ok "the reclaiming run released cleanly (no residue)" || pf_bad "F2 lock residue"

# stale-heartbeat path: dead pid + an ANCIENT heartbeat
mkjournal
mkdir -p "$LOCKDIR"
printf '%s\n' "999999" > "$LOCKDIR/owner"          # a certainly-dead pid
printf '%s\n' "run-dead-1" > "$LOCKDIR/run_id"
printf '%s\n' "$(( $(date +%s) - 1000 ))" > "$LOCKDIR/heartbeat"
REPO_ROOT="$PF_REPO_ROOT" H_INSTALL="$INSTALL_DIR" bash "$PF_SB/reclaim.sh" > /dev/null 2>&1
DET2="$(/usr/bin/python3 -c '
import json,sys
j=json.load(open(sys.argv[1]))
h=[r for r in j["history"] if r.get("event")=="lock-stale-break"]
print(h[-1]["detail"] if h else "")' "$J")"
case "$DET2" in
    *"heartbeat_age=1000s"*"reason=stale-heartbeat"*) pf_ok "stale-heartbeat path journals its own reason + age" ;;
    *) pf_bad "F2 stale-heartbeat row wrong: $DET2" ;;
esac

# a LIVE owner is NEVER broken
mkjournal
( sleep 15 ) & LIVEOWN=$!
mkdir -p "$LOCKDIR"
printf '%s\n' "$LIVEOWN" > "$LOCKDIR/owner"
printf '%s\n' "run-live-1" > "$LOCKDIR/run_id"
printf '%s\n' "$(( $(date +%s) - 1000 ))" > "$LOCKDIR/heartbeat"
REPO_ROOT="$PF_REPO_ROOT" H_INSTALL="$INSTALL_DIR" bash -c '
. "$REPO_ROOT/scripts/upgrade/lib.sh"
LOG_TAG=pf-live
INSTALL_DIR="$H_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
lock_acquire 2 || { echo BUSY; exit 7; }
' > "$PF_SB/live.out" 2>/dev/null; LIVE_RC=$?
[ "$LIVE_RC" = "7" ] && [ -d "$LOCKDIR" ] && pf_ok "a LIVE owner's lock (stale heartbeat) is NEVER broken (pipeline-busy)" \
                                       || pf_bad "F2 live-owner lock was broken or rc=$LIVE_RC"
kill -KILL "$LIVEOWN" 2>/dev/null; wait "$LIVEOWN" 2>/dev/null
rm -rf "$LOCKDIR"

printf '\n== F3: stage.sh success-path release (structural + functional) ==\n'
SREL="$(grep -n '^lock_release$' "$PF_REPO_ROOT/scripts/upgrade/stage.sh" | tail -1 | cut -d: -f1)"
SEXIT="$(grep -n '^exit 0$' "$PF_REPO_ROOT/scripts/upgrade/stage.sh" | tail -1 | cut -d: -f1)"
[ -n "$SREL" ] && [ -n "$SEXIT" ] && [ "$SREL" -lt "$SEXIT" ] \
    && pf_ok "stage.sh releases explicitly BEFORE its final exit 0 (lines $SREL < $SEXIT)" \
    || pf_bad "F3 no explicit release before the success exit (rel=$SREL exit=$SEXIT)"
grep -q '_run_log_retention' "$PF_REPO_ROOT/scripts/upgrade/stage.sh" \
    && pf_ok "stage seat runs per-run log retention under the lock" || pf_bad "F3 stage retention seat missing"
# functional: acquire -> explicit release -> the EXIT trap's second release is a no-op
mkjournal
cat > "$PF_SB/succ.sh" <<'EOS'
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
LOG_TAG=pf-succ
INSTALL_DIR="$H_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
_trap_install_signal_handlers "stage:lock"
lock_acquire || exit 78
lock_heartbeat
lock_release
exit 0
EOS
REPO_ROOT="$PF_REPO_ROOT" H_INSTALL="$INSTALL_DIR" bash "$PF_SB/succ.sh" > /dev/null 2> "$PF_SB/succ.err"
SUCC_RC=$?
[ "$SUCC_RC" = "0" ] && [ ! -d "$LOCKDIR" ] \
    && pf_ok "success path leaves NO lock dir; the EXIT-trap second release is a silent no-op" \
    || pf_bad "F3 rc=$SUCC_RC lock=$([ -d "$LOCKDIR" ] && echo present || echo absent)"
[ -s "$PF_SB/succ.err" ] && pf_bad "F3 double-release warned: $(cat "$PF_SB/succ.err")" \
                         || pf_ok "double release is silent (idempotent)"

printf '\n== F4: live-shaped path — the reclaim row still lands (fence interop) ==\n'
LS="$PF_FAKE_HOME/agents-ensemble"
mkdir -p "$LS/releases"
LJ="$LS/releases/state.json"
mkjournal "$LJ"
LLOCK="$LS/releases/rollback.lock.d"
mkdir -p "$LLOCK"
printf '%s\n' "999998" > "$LLOCK/owner"
printf '%s\n' "run-dead-2" > "$LLOCK/run_id"
printf '%s\n' "$(( $(date +%s) - 1000 ))" > "$LLOCK/heartbeat"
REPO_ROOT="$PF_REPO_ROOT" H_INSTALL="$LS" bash "$PF_SB/reclaim.sh" > /dev/null 2>&1
LSR="$(count_rows "$LJ" lock-stale-break)"
[ "$LSR" = "1" ] && pf_ok "live-shaped: the reclaim row LANDED (written after acquisition, fence satisfied)" \
                 || pf_bad "F4 live-shaped reclaim row missing (rows=$LSR)"

pf_summary
