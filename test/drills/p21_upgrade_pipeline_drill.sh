#!/bin/bash
# ============================================================================
# test/drills/p21_upgrade_pipeline_drill.sh — P2.1 sandbox acceptance drills
# ============================================================================
# End-to-end acceptance drills for scripts/upgrade/ (phase1-plan T1-T8
# acceptance rows), entirely inside throwaway sandbox install dirs with stub
# "ensemble-prod" test doubles (test/drills/p21_stub_daemon.py) — NO real
# PyInstaller build, NO DB, NO live contact (drill ports 184xx; the live
# port is never a literal in scripts/upgrade/ and sandboxes refuse it).
#
# Phases:
#   0  guards: TARGET=live w/o guard → 78; sandbox no-PORT → 78; status OK
#   A  clean promote ×2 (T4): commit, previous updated, version verify, lock
#      free; T9 make-vs-direct byte identity
#   B  unsafe-previous halt (T5): rollback_safe=false PREVIOUS → halt, NO
#      repoint (the manifest gate checks the journal's previous, not current)
#   C  auto-rollback (T5): wrongver stub → rollback + quarantine + cooldown
#      + count; quarantined re-promote refused
#   D  cap 3/24h (T5): manual rollbacks count → 3rd arms halt → promotes 78
#   E  quarantine --force (T6): refused without --force; warned + re-gate
#      verdict with it
#   F  retention (T8, sandbox 2): stage 5 + promote through → exactly 3
#   G  mid-flip SIGKILL (T4, sandbox 3): journal in_flight + flipped:true
#   H  cross-writer sweep (T7 × review i2, sandbox 4): SIGKILL a REAL
#      promote after flipped:true (journal 100% lib.sh-written), age the
#      txn past the 600s sweep gate, then start the REAL launcher.sh
#      against the sandbox → its sweep rollbacks the REAL orphan:
#      repoint + sweep_rollback + counter + cooldown + quarantine, and
#      boots the rolled-back release
#
# Drill knobs (sandbox-only conveniences; production defaults unchanged):
#   ENSEMBLE_PROMOTE_SOAK_S=4  LIVEZ_BUDGET_S=6  READYZ_BUDGET_S=6
# Cooldown timing is unit-proven (release_journal pack); phases clear the
# stamp to sequence drills without 10-min waits (each clearing is logged).
#
# Usage:  bash test/drills/p21_upgrade_pipeline_drill.sh [run-dir]
# Output: transcript + per-phase PASS/FAIL; exit 0 iff all phases pass.
# ============================================================================

set -u
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UP="$REPO_ROOT/scripts/upgrade"
STUB_TPL="$REPO_ROOT/test/drills/p21_stub_daemon.py"

RUN_DIR="${1:-/tmp/p21-drills/$(date -u +%Y%m%dT%H%M%S)}"
mkdir -p "$RUN_DIR"
TRANS="$RUN_DIR/transcript.txt"

PASS=0; FAIL=0; FAILED=""
ok()   { PASS=$((PASS+1)); printf 'PASS: %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); FAILED="$FAILED\n  ✗ $1"; printf 'FAIL: %s\n' "$1"; }
chk()  { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (want=$2 got=$3)"; fi }
chk_contains() { # <name> <needle> <file> — FIXED-STRING match (needles are literals)
    if grep -qF "$2" "$3" 2>/dev/null; then ok "$1"; else bad "$1 (missing '$2' in $3)"; fi
}

# Drill knobs + env sanitation (shed the host's ambient daemon env — the
# live install's PORT/POSTGRES_DB must never leak into a sandbox).
dk() { # dk [env-prefixes...] -- <cmd...>
    env -u PORT -u POSTGRES_DB -u INSTALL_DIR \
        ENSEMBLE_PROMOTE_SOAK_S=4 LIVEZ_BUDGET_S=6 READYZ_BUDGET_S=6 "$@"
}

tag_with() { # <tag> — tag HEAD for the stage guard (retry on index.lock
# races with the parallel coder on this branch; deleted right after use)
    local t="$1" n=0
    git -C "$REPO_ROOT" tag -d "$t" > /dev/null 2>&1
    while [ $n -lt 5 ]; do
        if git -C "$REPO_ROOT" tag "$t" > /dev/null 2>&1; then return 0; fi
        sleep 5; n=$((n+1))
    done
    printf 'DRILL: cannot tag %s (lock contention)\n' "$t"; return 1
}
untag() { git -C "$REPO_ROOT" tag -d "$1" > /dev/null 2>&1 || true; }

make_stub() { # <out> <version> <mode>
    sed -e "s/0\.0\.0-stub/$2/" -e "s/STUB_MODE = \"serve\"/STUB_MODE = \"$3\"/" \
        "$STUB_TPL" > "$1"
    chmod +x "$1"
}

stage_version() { # <sbx> <port> <ver> <mode> <rollback_safe 0|1>
    local sbx="$1" port="$2" ver="$3" mode="$4" rbs="$5"
    local stub="$RUN_DIR/stub-$ver"
    make_stub "$stub" "$ver" "$mode"
    # the stage guard requires tag == VERSION exactly (ADR-009 D3), so the
    # drill tags HEAD with the version token itself (deleted right after;
    # drill-only tokens vXx never collide with real release tags)
    tag_with "$ver" || return 1
    dk ENSEMBLE_ROLLBACK_SAFE="$rbs" ENSEMBLE_BINARY_VERSION="$ver" \
       VERSION="$ver" TARGET=sandbox INSTALL_DIR="$sbx" PORT="$port" \
       bash "$UP/stage.sh" sandbox --skip-build "$stub" > "$RUN_DIR/stage-$ver.log" 2>&1
    local rc=$?
    untag "$ver"
    return $rc
}

promote() { # <sbx> <port> <ver> <logfile>
    dk VERSION="$3" TARGET=sandbox INSTALL_DIR="$1" PORT="$2" \
       bash "$UP/promote.sh" sandbox > "$4" 2>&1
}
rollback_run() { # <sbx> <port> <logfile> [extra-args...]
    local sbx="$1" port="$2" log="$3"; shift 3
    dk TARGET=sandbox INSTALL_DIR="$sbx" PORT="$port" \
       bash "$UP/rollback.sh" sandbox "$@" > "$log" 2>&1
}
jsnap() { # jsnap <sbx> <name> — dump journal to a file
    ( export INSTALL_DIR="$1"; . "$UP/lib.sh"; journal_read ) 2>/dev/null > "$RUN_DIR/j-$2.json"
}
jget() { # jget <sbx> <snippet>
    ( export INSTALL_DIR="$1"; . "$UP/lib.sh"; eval "$2" ) 2>/dev/null
}
livez_version() { # <port>
    curl -fsS --max-time 3 "http://localhost:$1/livez" 2>/dev/null \
        | sed 's/.*"version" *: *"\([^"]*\)".*/\1/'
}

# drill_scoped_cleanup — kill ONLY processes whose cmdline references this
# drill namespace (p21-drills/...). Never port-based, never path-ambiguous:
# demo/live install paths cannot match this prefix.
drill_cleanup() {
    local d
    for d in "$RUN_DIR"/sbx1 "$RUN_DIR"/sbx2 "$RUN_DIR"/sbx3 "$RUN_DIR"/sbx4; do
        [ -d "$d" ] && bash "$REPO_ROOT/scripts/stop-ensemble.sh" "$d" > /dev/null 2>&1
    done
    # belt-and-braces: anything still referencing the run dir namespace
    pkill -f "p21-drills/.*/current/ensemble-prod" 2>/dev/null
    pkill -f "p21-drills/.*/launcher.sh" 2>/dev/null
    return 0
}

main() {
trap 'drill_cleanup' EXIT
exec > >(tee "$TRANS") 2>&1
printf 'P2.1 sandbox acceptance drills — %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'run dir: %s\n' "$RUN_DIR"

# leftovers from earlier drill runs (killed tool timeouts etc.) would hold
# the drill ports and poison every gate — sweep the whole p21-drills
# namespace first (scoped by cmdline path; demo/live can never match)
pkill -f "p21-drills/.*/current/ensemble-prod" 2>/dev/null
pkill -f "p21-drills/.*/launcher.sh" 2>/dev/null
sleep 1

# live-isolation baseline (Batch C: print→assert conversion). Every drill
# phase runs inside throwaway sandboxes; the live install's listener pids
# (resolved from the live .env — never a literal port) must be IDENTICAL at
# the end of the drill run. Empty-vs-empty (no live install staged on this
# host) passes trivially — there is nothing to protect.
live_pids_snapshot() {
    lsof -ti:"$(sed -n 's/^[[:space:]]*PORT=//p' "$HOME/agents-ensemble/.env" 2>/dev/null | head -1)" 2>/dev/null | sort | tr '\n' ' '
}
LIVE_PIDS_BASE="$(live_pids_snapshot)"
printf 'live pid checkpoint (baseline): %s\n' "${LIVE_PIDS_BASE:-<none>}"

# ═══ Phase 0: guards ══════════════════════════════════════════════════════
printf '\n━━━ Phase 0: guards ━━━\n'
dk TARGET=live bash "$UP/status.sh" live > "$RUN_DIR/g-live.log" 2>&1
chk "live without guard exits 78" 78 $?
SBX1="$RUN_DIR/sbx1"; SBX1_PORT=18401; mkdir -p "$SBX1"
dk TARGET=sandbox INSTALL_DIR="$SBX1" bash "$UP/status.sh" > "$RUN_DIR/g-noport.log" 2>&1
chk "sandbox without PORT exits 78" 78 $?
dk TARGET=sandbox INSTALL_DIR="$SBX1" PORT=$SBX1_PORT bash "$UP/status.sh" > "$RUN_DIR/g-ok.log" 2>&1
chk "sandbox status exits 0" 0 $?
chk_contains "sandbox status prints resolved env" "resolved env: target=sandbox" "$RUN_DIR/g-ok.log"

# ═══ Phase A: clean promotes (T4) + T9 byte identity ══════════════════════
printf '\n━━━ Phase A: clean promote ×2 (T4) + T9 ━━━\n'
stage_version "$SBX1" $SBX1_PORT vA1 serve 1 && ok "stage vA1" || bad "stage vA1"
stage_version "$SBX1" $SBX1_PORT vA2 serve 1 && ok "stage vA2" || bad "stage vA2"
promote "$SBX1" $SBX1_PORT vA1 "$RUN_DIR/promote-vA1.log"
chk "promote vA1 exits 0" 0 $?
jsnap "$SBX1" afterA1
chk_contains "journal commit recorded" '"commit"' "$RUN_DIR/j-afterA1.json"
chk "livez version == vA1 manifest binary_version" "vA1" "$(livez_version $SBX1_PORT)"
promote "$SBX1" $SBX1_PORT vA2 "$RUN_DIR/promote-vA2.log"
chk "promote vA2 exits 0" 0 $?
jsnap "$SBX1" afterA2
chk_contains "current is vA2" '"current":"vA2"' "$RUN_DIR/j-afterA2.json"
chk_contains "previous updated to vA1" '"previous":"vA1"' "$RUN_DIR/j-afterA2.json"
chk "livez version == vA2 manifest" "vA2" "$(livez_version $SBX1_PORT)"
if [ -d "$SBX1/releases/rollback.lock.d" ]; then bad "lock released after commit"; else ok "lock released after commit"; fi
# T9: make wrapper output byte-identical to direct invocation
dk TARGET=sandbox INSTALL_DIR="$SBX1" PORT=$SBX1_PORT bash "$UP/status.sh" sandbox > "$RUN_DIR/t9-direct.txt" 2>&1
dk TARGET=sandbox INSTALL_DIR="$SBX1" PORT=$SBX1_PORT make --no-print-directory -C "$REPO_ROOT" upgrade-status TARGET=sandbox INSTALL_DIR="$SBX1" PORT=$SBX1_PORT > "$RUN_DIR/t9-make.txt" 2>&1
if cmp -s "$RUN_DIR/t9-direct.txt" "$RUN_DIR/t9-make.txt"; then ok "T9: make upgrade-status byte-identical"; else bad "T9 byte identity (see t9-direct/t9-make)"; fi

# ═══ Phase B: unsafe-PREVIOUS halt (T5) ═══════════════════════════════════
printf '\n━━━ Phase B: rollback_safe=false PREVIOUS → halt-for-human, NO repoint (T5) ━━━\n'
# make the JOURNAL PREVIOUS (vA1) unsafe by re-staging it rollback_safe=0
stage_version "$SBX1" $SBX1_PORT vA1 serve 0 && ok "re-stage vA1 as rollback_safe=false (previous)" || bad "re-stage vA1 unsafe"
stage_version "$SBX1" $SBX1_PORT vB1 exit78 1 && ok "stage vB1 (exit78 stub)" || bad "stage vB1"
promote "$SBX1" $SBX1_PORT vB1 "$RUN_DIR/promote-vB1.log"
chk "promote vB1 halts with 78" 78 $?
chk_contains "halt: previous not rollback_safe" "NOT rollback_safe" "$RUN_DIR/promote-vB1.log"
chk "NO repoint — current still flipped to vB1" "releases/vB1" "$(readlink "$SBX1/current")"
jsnap "$SBX1" afterB1
chk_contains "journal halt event" '"halt"' "$RUN_DIR/j-afterB1.json"
# recovery: vA1 re-staged SAFE; manual rollback to vA1; then re-promote vA2
# to re-establish a good (current,previous) pairing
stage_version "$SBX1" $SBX1_PORT vA1 serve 1 && ok "re-stage vA1 safe (recovery)" || bad "re-stage vA1 safe"
rollback_run "$SBX1" $SBX1_PORT "$RUN_DIR/rollback-rec1.log"
chk "manual recovery rollback to vA1 exits 0" 0 $?
chk "recovered livez serves vA1" "vA1" "$(livez_version $SBX1_PORT)"
promote "$SBX1" $SBX1_PORT vA2 "$RUN_DIR/promote-vA2b.log"
chk "re-promote vA2 exits 0" 0 $?

# ═══ Phase C: auto-rollback on version-verify failure (T5) ════════════════
printf '\n━━━ Phase C: gate failure → auto-rollback + quarantine + cooldown (T5) ━━━\n'
stage_version "$SBX1" $SBX1_PORT vC1 wrongver 1 && ok "stage vC1 (wrongver stub)" || bad "stage vC1"
promote "$SBX1" $SBX1_PORT vC1 "$RUN_DIR/promote-vC1.log"
chk "promote vC1 exits 1 (rolled back)" 1 $?
chk_contains "version verify mismatch logged" "version verify MISMATCH" "$RUN_DIR/promote-vC1.log"
chk_contains "rollback complete" "ROLLBACK COMPLETE" "$RUN_DIR/promote-vC1.log"
jsnap "$SBX1" afterC1
chk_contains "journal rollback event" '"event":"rollback"' "$RUN_DIR/j-afterC1.json"
chk_contains "journal quarantine array holds vC1" '"quarantined":["vC1"' "$RUN_DIR/j-afterC1.json"
chk_contains "cooldown armed" '"cooldown_until":"2' "$RUN_DIR/j-afterC1.json"
chk "rollback restored vA1 serving" "vA1" "$(livez_version $SBX1_PORT)"
# clear the cooldown stamp so the re-promote refusal we observe is the
# QUARANTINE one (entry checks would otherwise refuse on cooldown first)
jget "$SBX1" 'journal_update cooldown_until "null"' > /dev/null
printf 'DRILL: cleared cooldown stamp (fixture manipulation — isolating the quarantine refusal)\n'
promote "$SBX1" $SBX1_PORT vC1 "$RUN_DIR/promote-vC1-retry.log"
chk "re-promote of quarantined vC1 refused (78)" 78 $?
chk_contains "quarantine refusal message" "QUARANTINED" "$RUN_DIR/promote-vC1-retry.log"

# ═══ Phase D: cap 3/24h (T5) ══════════════════════════════════════════════
printf '\n━━━ Phase D: cap 3/24h → halt-for-human (T5) ━━━\n'
# Phase C armed cooldown + count=1. Cooldown timing is unit-proven; clear
# the stamp to sequence drills (logged fixture manipulation).
jget "$SBX1" 'journal_update cooldown_until "null"' > /dev/null
printf 'DRILL: cleared cooldown stamp (fixture manipulation for sequencing)\n'
# rollbacks so far: Phase B recovery (manual, counts — T6) = 1,
# Phase C auto-rollback = 2. The NEXT rollback is the 3rd → arms halt.
chk "count after Phase C rollback = 2 (B-recovery + C-auto)" 2 "$(jget "$SBX1" journal_rollback_count_24h)"
stage_version "$SBX1" $SBX1_PORT vD1 serve 1 && ok "stage vD1" || bad "stage vD1"
promote "$SBX1" $SBX1_PORT vD1 "$RUN_DIR/promote-vD1.log"
chk "promote vD1 exits 0" 0 $?
rollback_run "$SBX1" $SBX1_PORT "$RUN_DIR/rollback-d1.log"
chk "manual rollback #3 exits 0 (arms halt)" 0 $?
jsnap "$SBX1" after-cap
chk_contains "journal halt at cap" '"halt"' "$RUN_DIR/j-after-cap.json"
chk "count = 3 (halt armed)" 3 "$(jget "$SBX1" journal_rollback_count_24h)"
promote "$SBX1" $SBX1_PORT vD1 "$RUN_DIR/promote-capped.log"
chk "promote under cap-halt refused (78)" 78 $?
chk_contains "halt message" "HALT-FOR-HUMAN" "$RUN_DIR/promote-capped.log"

# ═══ Phase E: quarantined rollback needs --force (T6) ═════════════════════
printf '\n━━━ Phase E: quarantined target requires --force (T6) ━━━\n'
rollback_run "$SBX1" $SBX1_PORT "$RUN_DIR/rollback-vC1-noforce.log" --to vC1
chk "rollback onto quarantined refused (78)" 78 $?
chk_contains "quarantine warning without --force" "QUARANTINED" "$RUN_DIR/rollback-vC1-noforce.log"
rollback_run "$SBX1" $SBX1_PORT "$RUN_DIR/rollback-vC1-force.log" --to vC1 --force
chk "rollback --force reaches re-gate (halt 78 on wrongver stub)" 78 $?
chk_contains "--force warning printed" "FORCING rollback onto QUARANTINED" "$RUN_DIR/rollback-vC1-force.log"

# ═══ Phase F: retention (T8) — fresh sandbox ══════════════════════════════
printf '\n━━━ Phase F: retention — 5 versions → exactly 3 (T8) ━━━\n'
SBX2="$RUN_DIR/sbx2"; SBX2_PORT=18402; mkdir -p "$SBX2"
# interleaved stage→promote (real upgrade cadence): staging all five UP
# FRONT would let retention evict the not-yet-promoted oldest right after
# the first commit — correct behavior, wrong drill shape
for v in vF1 vF2 vF3 vF4 vF5; do
    stage_version "$SBX2" $SBX2_PORT "$v" serve 1 && ok "stage $v" || bad "stage $v"
    promote "$SBX2" $SBX2_PORT "$v" "$RUN_DIR/promote-$v.log"
    chk "promote $v exits 0" 0 $?
done
N_RELS="$(find "$SBX2/releases" -maxdepth 1 -type d -not -name releases -not -name '.*' | wc -l | tr -d ' ')"
chk "exactly 3 releases remain" 3 "$N_RELS"
chk "current vF5 survives" "yes" "$([ -d "$SBX2/releases/vF5" ] && echo yes || echo no)"
chk "previous vF4 survives (pinned)" "yes" "$([ -d "$SBX2/releases/vF4" ] && echo yes || echo no)"
chk "oldest vF1 evicted" "yes" "$([ ! -d "$SBX2/releases/vF1" ] && echo yes || echo no)"
chk "vF2 evicted" "yes" "$([ ! -d "$SBX2/releases/vF2" ] && echo yes || echo no)"
chk "livez serves vF5" "vF5" "$(livez_version $SBX2_PORT)"
printf 'retention survivors: %s\n' "$(ls "$SBX2/releases" | tr '\n' ' ')"

# ═══ Phase G: mid-flip SIGKILL (T4) — fresh sandbox ═══════════════════════
printf '\n━━━ Phase G: SIGKILL promote mid-flip → in_flight + flipped:true (T4) ━━━\n'
SBX3="$RUN_DIR/sbx3"; SBX3_PORT=18403; mkdir -p "$SBX3"
stage_version "$SBX3" $SBX3_PORT vG1 serve 1 && ok "stage vG1" || bad "stage vG1"
stage_version "$SBX3" $SBX3_PORT vG2 serve 1 && ok "stage vG2" || bad "stage vG2"
promote "$SBX3" $SBX3_PORT vG1 "$RUN_DIR/promote-vG1.log"
chk "promote vG1 exits 0" 0 $?
# launch via DIRECT env (env execs bash: $! IS the promote pid — the dk()
# wrapper runs in a subshell and kill -9 would only kill the wrapper,
# letting the promote survive and race the drill)
env -u PORT -u POSTGRES_DB -u INSTALL_DIR \
    ENSEMBLE_PROMOTE_SOAK_S=4 LIVEZ_BUDGET_S=6 READYZ_BUDGET_S=6 \
    VERSION=vG2 TARGET=sandbox INSTALL_DIR="$SBX3" PORT=$SBX3_PORT \
    bash "$UP/promote.sh" sandbox > "$RUN_DIR/promote-vG2-killed.log" 2>&1 &
PRO_PID=$!
KILLED=0
i=0
while [ $i -lt 150 ]; do
    FLIP="$(jget "$SBX3" '_json_field "$(_json_sub "$(journal_read)" in_flight)" flipped')"
    if [ "$FLIP" = "true" ]; then
        kill -9 $PRO_PID 2>/dev/null
        KILLED=1
        break
    fi
    sleep 0.2
    i=$((i+1))
done
if [ "$KILLED" = "1" ]; then ok "promote SIGKILLed right after flipped:true"; else bad "never observed flipped:true"; fi
wait $PRO_PID 2>/dev/null
jsnap "$SBX3" after-kill
chk_contains "journal has in_flight promote txn" '"kind":"promote"' "$RUN_DIR/j-after-kill.json"
chk_contains "txn target vG2" '"target":"vG2"' "$RUN_DIR/j-after-kill.json"
chk_contains "flipped stays true" '"flipped":true' "$RUN_DIR/j-after-kill.json"
chk "current moved to vG2 (flip happened)" "releases/vG2" "$(readlink "$SBX3/current")"
if [ -d "$SBX3/releases/rollback.lock.d" ]; then ok "lock left held by dead owner (T7 sweep input; stale-breakable)"; else bad "lock unexpectedly absent after kill"; fi
# drill cleanup (T7 owns the real sweep): resolve the orphan manually and
# re-promote vG2 cleanly so the sandbox ends green
bash "$REPO_ROOT/scripts/stop-ensemble.sh" "$SBX3" $SBX3_PORT > /dev/null 2>&1
rm -rf "$SBX3/releases/rollback.lock.d"
jget "$SBX3" 'journal_close_txn; journal_set_current vG2; journal_set_previous vG1' > /dev/null
promote "$SBX3" $SBX3_PORT vG2 "$RUN_DIR/promote-vG2-recovery.log"
chk "post-orphan re-promote vG2 exits 0 (drill recovery)" 0 $?

# ═══ Phase H: cross-writer sweep (T7 × review i2) ═════════════════════════
# The wave-1 gap this closes: nothing ever proved the LAUNCHER's sweep
# against a journal written by the REAL lib.sh writers (promote's
# open_txn/mark_flipped). Here a real promote is SIGKILLed right after
# flipped:true; the on-disk journal is 100% pipeline-written (NOT
# drill-seeded). The launcher.sh started next must rollback that REAL
# orphan: repoint + sweep_rollback + counter + cooldown + quarantine.
printf '\n━━━ Phase H: cross-writer — REAL promote kill → REAL launcher sweep rollback ━━━\n'
SBX4="$RUN_DIR/sbx4"; SBX4_PORT=18404; mkdir -p "$SBX4"
# THREE versions: vH0 → vH1 promoted cleanly first, so the journal carries
# a REAL previous (vH0) for the orphaned vH2 flip to roll back to (a
# first-ever promote would leave previous=null and the sweep would
# correctly halt instead of rolling back)
stage_version "$SBX4" $SBX4_PORT vH0 serve 1 && ok "stage vH0" || bad "stage vH0"
stage_version "$SBX4" $SBX4_PORT vH1 serve 1 && ok "stage vH1" || bad "stage vH1"
stage_version "$SBX4" $SBX4_PORT vH2 serve 1 && ok "stage vH2" || bad "stage vH2"
promote "$SBX4" $SBX4_PORT vH0 "$RUN_DIR/promote-vH0.log"
chk "promote vH0 exits 0" 0 $?
promote "$SBX4" $SBX4_PORT vH1 "$RUN_DIR/promote-vH1.log"
chk "promote vH1 exits 0" 0 $?
chk "journal carries a real previous (vH0)" "vH0" "$(jget "$SBX4" '_json_field "$(journal_read)" previous')"
# launch via DIRECT env (env execs bash: $! IS the promote pid — the dk()
# wrapper runs in a subshell and kill -9 would only kill the wrapper)
env -u PORT -u POSTGRES_DB -u INSTALL_DIR \
    ENSEMBLE_PROMOTE_SOAK_S=4 LIVEZ_BUDGET_S=6 READYZ_BUDGET_S=6 \
    VERSION=vH2 TARGET=sandbox INSTALL_DIR="$SBX4" PORT=$SBX4_PORT \
    bash "$UP/promote.sh" sandbox > "$RUN_DIR/promote-vH2-killed.log" 2>&1 &
PRO_PID=$!
KILLED=0
i=0
while [ $i -lt 150 ]; do
    FLIP="$(jget "$SBX4" '_json_field "$(_json_sub "$(journal_read)" in_flight)" flipped')"
    if [ "$FLIP" = "true" ]; then
        kill -9 $PRO_PID 2>/dev/null
        KILLED=1
        break
    fi
    sleep 0.2
    i=$((i+1))
done
if [ "$KILLED" = "1" ]; then ok "promote vH2 SIGKILLed right after flipped:true (REAL lib.sh journal)"; else bad "never observed flipped:true (vH2)"; fi
wait $PRO_PID 2>/dev/null
# the kill races restart_via_launcher: if the orphaned promote got its
# nohup'd launcher + stub out before dying, that stub holds the port —
# stop it so the drill's own launcher start below is deterministic
# (identical sweep input either way: journal + symlink + lock)
bash "$REPO_ROOT/scripts/stop-ensemble.sh" "$SBX4" $SBX4_PORT > /dev/null 2>&1
chk "pre-sweep: current orphaned on vH2" "releases/vH2" "$(readlink "$SBX4/current")"
jsnap "$SBX4" pre-sweep
chk_contains "pre-sweep: real promote txn on disk" '"kind":"promote"' "$RUN_DIR/j-pre-sweep.json"
chk_contains "pre-sweep: flipped:true on disk" '"flipped":true' "$RUN_DIR/j-pre-sweep.json"
if [ -d "$SBX4/releases/rollback.lock.d" ]; then ok "pre-sweep: lock held by dead promote (sweep must dead-owner break)"; else bad "lock unexpectedly absent pre-sweep"; fi
# age the REAL txn past the 600s sweep gate (knobs stay script constants
# per T7 — the only way to cross the gate in drill time is rewriting
# started_at via journal_update itself; logged fixture manipulation, same
# class as the cooldown clears above)
jget "$SBX4" 'ts="$(date -ju -v-700S +%Y-%m-%dT%H:%M:%SZ)"; inf="$(_json_sub "$(journal_read)" in_flight)"; k="$(_json_field "$inf" kind)"; t="$(_json_field "$inf" target)"; o="$(_json_field "$inf" owner_pid)"; journal_update in_flight "{\"kind\":\"$k\",\"target\":\"$t\",\"started_at\":\"$ts\",\"flipped\":true,\"owner_pid\":$o}"' > /dev/null
printf 'DRILL: aged the REAL txn started_at to -700s (fixture manipulation to cross the 600s sweep gate in drill time)\n'
# start the REAL launcher (promote's launcher_swap staged it at the sandbox
# root in the stopped window; resolve_install_dir picks the sandbox up from
# its own location): sweep runs BEFORE binary resolution (T7)
( cd "$SBX4" && nohup bash ./launcher.sh >> "$SBX4/data/launcher.log" 2>&1 & )
sleep 2
chk "cross-writer: sweep repointed current to vH0 (previous)" "releases/vH0" "$(readlink "$SBX4/current")"
jsnap "$SBX4" post-sweep
chk_contains "cross-writer: sweep_rollback event in the REAL journal" '"event":"sweep_rollback"' "$RUN_DIR/j-post-sweep.json"
# semantic reads (the launcher's splice writes "key": value with a space —
# lib.sh writes "key":value; never assert raw spacing across writers)
chk "cross-writer: txn cleared" "null" "$(jget "$SBX4" '_json_field "$(journal_read)" in_flight')"
chk "cross-writer: journal current = vH0" "vH0" "$(jget "$SBX4" '_json_field "$(journal_read)" current')"
chk_contains "cross-writer: vH2 quarantined" '"vH2"' "$RUN_DIR/j-post-sweep.json"
chk "cross-writer: rollback counter incremented to 1" 1 "$(jget "$SBX4" journal_rollback_count_24h)"
CD_H="$(jget "$SBX4" '_json_field "$(journal_read)" cooldown_until')"
case "$CD_H" in null|"") bad "cross-writer: cooldown armed by the sweep (ADR-024)" ;; *) ok "cross-writer: cooldown armed by the sweep (ADR-024)" ;; esac
i=0; V=""
while [ $i -lt 20 ]; do
    V="$(livez_version $SBX4_PORT)"
    [ -n "$V" ] && break
    sleep 0.5; i=$((i+1))
done
chk "cross-writer: launcher booted the rolled-back vH0" "vH0" "$V"
if [ -d "$SBX4/releases/rollback.lock.d" ]; then bad "cross-writer: sweep released the lock"; else ok "cross-writer: sweep released the lock"; fi

# ═══ static: no network fetch, no literal live port ═══════════════════════
printf '\n━━━ static: no git-pull / no literal live port in scripts/upgrade/ ━━━\n'
if grep -rn -e 'git pull' -e 'git fetch' -e 'git clone' "$UP" > "$RUN_DIR/static-fetch.txt" 2>&1; then
    bad "network-fetch pattern found (see static-fetch.txt)"
else
    ok "no git pull/fetch/clone anywhere in scripts/upgrade/"
fi
if grep -rn '9797' "$UP" > /dev/null 2>&1; then bad "literal live port in scripts/upgrade/"; else ok "no literal live port"; fi

# live-isolation ASSERTION (Batch C: print→assert conversion): fail the
# drill if the live install's listener pid set changed at any point during
# the phases. Sorted sets compare order-insensitively; empty==empty (no
# live install on this host) is a pass.
LIVE_PIDS_END="$(live_pids_snapshot)"
printf 'live pid checkpoint (end of drills): %s\n' "${LIVE_PIDS_END:-<none>}"
if [ "$LIVE_PIDS_BASE" = "$LIVE_PIDS_END" ]; then
    ok "live-isolation: live pid set unchanged across all drills (baseline == end)"
else
    bad "live-isolation: live pid set CHANGED across drills (baseline='${LIVE_PIDS_BASE:-<none>}' end='${LIVE_PIDS_END:-<none>}')"
fi

printf '\n═══ DRILLS COMPLETE: %d passed, %d failed ═══\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || printf 'failed:%b\n' "$FAILED"
printf 'transcript: %s\n' "$TRANS"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
}

main "$@"
