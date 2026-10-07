#!/bin/bash
# ============================================================================
# tests/test_boot_sweep_commit_and_continue.sh — pin tests for the 2.2c
# boot-sweep commit-and-continue path (upgrade-resilience 2026-10-07)
# ============================================================================
# The flipped=true branch in launcher.sh:_journal_sweep now consults a NEW
# outcome BEFORE the existing halt gates + sweep-rollback path:
#
#   boot_sweep_commit_and_continue — committed when ALL gates hold:
#     (a) journal history contains an `intent_flip` event for THIS target
#         (writer: promote.sh 2.2c emit, post-integrity/pre-mutation)
#     (b) txn flipped:true (real OR kill-window-healed)
#     (c) previous release's manifest rollback_safe is EXPLICITLY "false"
#         (operator-declared incompatibility; missing/unreadable manifest
#         stays conservative-halt)
#
# Mutations performed (atomic temp+mv each, D4):
#   - journal current → target (the committed release)
#   - journal in_flight → null (close the txn)
#   - history append: boot_sweep_commit_and_continue
# NOT done: sweep-rollback, quarantine, counter+cooldown arming.
#
# Test matrix:
#   C1 happy-path — ALL gates hold → commit-and-continue fires, journal
#       current=target, in_flight=null, boot_sweep_commit_and_continue event
#       in history, NO sweep_rollback, NO quarantine.
#   C2 no intent_flip — fail-closed: even with prev.rollback_safe=false,
#       the existing halt (not-rollback_safe) gate fires. NO commit.
#   C3 missing manifest — fail-closed: intent_flip present BUT manifest
#       missing → halt fires (conservative). NO commit.
#   C4 rollback_safe missing key — halt fires. NO commit.
#   C5 rollback_safe TRUE — sweep-rollback path taken (not commit path).
#   C7 cooldown uname fix (lib.sh:982 BSD/GNU dispatch) — bonus check.
#
# Run:
#   bash tests/test_boot_sweep_commit_and_continue.sh
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
        *"$needle"*) _pass "$name" ;;
        *) _fail "$name" "contains '$needle'" "$haystack" ;;
    esac
}
assert_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _fail "$name" "absent '$needle'" "$haystack" ;;
        *) _pass "$name" ;;
    esac
}
section() { printf '\n== %s ==\n' "$1"; }

# Helper: build a fixture install dir with a release + manifest + journal
make_install_fixture() {
    # args: <fix_dir> <target> <prev> <prev_rollback_safe>
    local fix="$1" target="$2" prev="$3" prev_safe="$4"
    rm -rf "$fix"; mkdir -p "$fix/releases/$target" "$fix/releases/$prev" "$fix/data"
    # release manifests — just enough for _js_manifest_field to read
    cat > "$fix/releases/$target/manifest.json" <<EOF
{"manifest_version":"1.0.0","binary_version":"$target","rollback_safe":true}
EOF
    if [ "$prev_safe" = "__MISSING_MANIFEST__" ]; then
        : # leave manifest.json absent
    elif [ "$prev_safe" = "__MISSING_KEY__" ]; then
        cat > "$fix/releases/$prev/manifest.json" <<EOF
{"manifest_version":"1.0.0","binary_version":"$prev"}
EOF
    else
        cat > "$fix/releases/$prev/manifest.json" <<EOF
{"manifest_version":"1.0.0","binary_version":"$prev","rollback_safe":$prev_safe}
EOF
    fi
}

# Helper: write a journal with the shape _journal_sweep expects
seed_journal() {
    # args: <fix_dir> <target> <prev> <flipped> <include_intent_flip>
    local fix="$1" target="$2" prev="$3" flipped="$4" include_intent="$5"
    local intent=""
    if [ "$include_intent" = "1" ]; then
        intent=',{"ts":"2026-10-07T10:00:00Z","event":"intent_flip","detail":"preflight: intentional promote target '"$target"' — integrity verified (CURRENT drift-check + TARGET manifest match); operator-initiated"}'
    fi
    cat > "$fix/releases/state.json" <<EOF
{"current":"$prev","previous":"$prev","in_flight":{"kind":"promote","target":"$target","started_at":"2026-10-07T09:00:00Z","flipped":$flipped,"owner_pid":99999,"last_heartbeat":1728000000},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[{"ts":"2026-10-07T09:00:00Z","event":"open_txn","detail":"test seeded"}$intent]}
EOF
}

# Run the sweep in isolation. Sourcing launcher.sh inside a subshell with
# INSTALL_DIR pointing at our fixture gives us access to _journal_sweep
# + the full _js_* helper family without spawning a daemon.
run_sweep() {
    local fix="$1"
    INSTALL_DIR="$fix" PORT=19999 \
    SWEEP_STALE_S=0 HEARTBEAT_STALE_S=0 LOCK_HEARTBEAT_S=30 LOCK_STALE_S=300 \
    bash -c '
        . "'"$REPO_ROOT"'/launcher.sh" >/dev/null 2>&1
        # _journal_sweep is the private function we want to exercise; it
        # walks the journal under the D5 lock and applies mutations.
        _journal_sweep "$INSTALL_DIR"
        echo "sweep-rc=$?"
    ' 2>&1
}

# ===========================================================================
section "C1 — happy-path commit-and-continue (ALL gates hold)"

C1_FIX="$(mktemp -d -t c1.XXXXXX)"
make_install_fixture "$C1_FIX" "v9.9.9-test-target" "v9.9.9-test-prev" "false"
# current=prev (pre-flip baseline), in_flight.flipped=true (post-flip
# symlink evidence), intent_flip event for the target.
seed_journal "$C1_FIX" "v9.9.9-test-target" "v9.9.9-test-prev" "true" "1"
# Symlink must point at the target (the kill-window heal + flipped:true
# are the proof the atomic flip completed).
ln -sfn "releases/v9.9.9-test-target" "$C1_FIX/current"
# Seed .env to keep restart paths from refusing.
printf 'PORT=19999\n' > "$C1_FIX/.env"
printf '#!/bin/bash\nexit 0\n' > "$C1_FIX/launcher.sh"
chmod +x "$C1_FIX/launcher.sh"

C1_OUT="$(run_sweep "$C1_FIX")"
echo "$C1_OUT" | head -20 >&2
C1_RC=$(printf '%s' "$C1_OUT" | grep -oE 'sweep-rc=[0-9]+' | head -1 | sed 's/sweep-rc=//')
assert_eq "C1 sweep returns 0" "0" "$C1_RC"

# Journal state after sweep:
C1_JOURNAL="$(cat "$C1_FIX/releases/state.json")"
case "$C1_JOURNAL" in
    *'"current":"v9.9.9-test-target"'*|*"\"current\": \"v9.9.9-test-target\""*) _pass "C1 journal current → target (committed)" ;;
    *) _fail "C1 journal current → target (committed)" '"current":"v9.9.9-test-target"' "$(printf '%s' "$C1_JOURNAL" | grep -oE '"current":[^,}]*')" ;;
esac
case "$C1_JOURNAL" in
    *'"in_flight":null'*|*"\"in_flight\": null"*) _pass "C1 journal in_flight cleared" ;;
    *) _fail "C1 journal in_flight cleared" '"in_flight":null' "$(printf '%s' "$C1_JOURNAL" | grep -oE '"in_flight":[^,}]*')" ;;
esac
assert_contains "C1 history has boot_sweep_commit_and_continue event" '"event":"boot_sweep_commit_and_continue"' "$C1_JOURNAL"
assert_contains "C1 history detail names target/prev" "target=v9.9.9-test-target" "$C1_JOURNAL"
assert_contains "C1 history detail names prev" "prev=v9.9.9-test-prev" "$C1_JOURNAL"
assert_contains "C1 log line fires BOOT-SWEEP COMMIT-AND-CONTINUE" "BOOT-SWEEP COMMIT-AND-CONTINUE" "$C1_OUT"
assert_contains "C1 notify_once sweep-commit fired" "NOTIFY[sweep-commit]" "$C1_OUT"

# Negative assertions (the new path must NOT do these):
assert_not_contains "C1 NO sweep_rollback event" '"event":"sweep_rollback"' "$C1_JOURNAL"
assert_not_contains "C1 NO halt event" '"event":"halt"' "$C1_JOURNAL"
# Quarantine: the commit-and-continue path MUST NOT add target to
# quarantined[]. The existing journal already has `"quarantined":[]`
# (empty list). The target's string should NOT appear inside the
# quarantined array post-sweep (target= strings in the history detail
# are FINE — only the quarantined[] array matters). Extract the
# quarantined array and check it does NOT contain the target.
C1_QUAR="$(printf '%s' "$C1_JOURNAL" | grep -oE '"quarantined":\[[^]]*\]')"
case "$C1_QUAR" in
    *"v9.9.9-test-target"*) _fail "C1 NO quarantine append (target absent from quarantined[])" "absent" "present: $C1_QUAR" ;;
    *) _pass "C1 NO quarantine append (target absent from quarantined[])" ;;
esac

rm -rf "$C1_FIX"

# ===========================================================================
section "C2 — no intent_flip event → fail-closed (halt gate fires)"

C2_FIX="$(mktemp -d -t c2.XXXXXX)"
make_install_fixture "$C2_FIX" "v9.9.9-test-target" "v9.9.9-test-prev" "false"
# Same as C1 but NO intent_flip event.
seed_journal "$C2_FIX" "v9.9.9-test-target" "v9.9.9-test-prev" "true" "0"
ln -sfn "releases/v9.9.9-test-target" "$C2_FIX/current"
printf 'PORT=19999\n' > "$C2_FIX/.env"
printf '#!/bin/bash\nexit 0\n' > "$C2_FIX/launcher.sh"
chmod +x "$C2_FIX/launcher.sh"

C2_OUT="$(run_sweep "$C2_FIX")"
echo "$C2_OUT" | head -20 >&2
C2_JOURNAL="$(cat "$C2_FIX/releases/state.json")"
# Without intent_flip evidence, the boot_sweep_commit_and_continue gates
# don't all hold. The conservative behavior: ONE of the existing halt
# gates fires (we have prev+manifest+flipped=true+rollback_safe=false,
# so the prev is valid and the rollback_safe:false IS detectable). The
# brief says "If evidence is insufficient: existing halt gates +
# rollback path UNTOUCHED — the new branch ONLY fires when ALL gates
# hold." The path taken depends on which existing check the code reaches
# first — without intent_flip, the commit-and-continue branch is
# SKIPPED and one of the halt gates fires (the not-rollback_safe gate
# when prev.rollback_safe=false is the next one in the chain).
# Most importantly: NO commit-and-continue event, NO sweep_rollback
# (because prev.rollback_safe=false halts before sweep-rollback).
assert_not_contains "C2 NO boot_sweep_commit_and_continue event (intent_flip missing)" '"event":"boot_sweep_commit_and_continue"' "$C2_JOURNAL"
assert_not_contains "C2 NO sweep_rollback (prev.rollback_safe=false halts first)" '"event":"sweep_rollback"' "$C2_JOURNAL"

rm -rf "$C2_FIX"

# ===========================================================================
section "C3 — manifest missing → fail-closed (intent_flip present but no prev manifest)"

C3_FIX="$(mktemp -d -t c3.XXXXXX)"
make_install_fixture "$C3_FIX" "v9.9.9-test-target" "v9.9.9-test-prev" "__MISSING_MANIFEST__"
# Intent_flip IS present.
seed_journal "$C3_FIX" "v9.9.9-test-target" "v9.9.9-test-prev" "true" "1"
ln -sfn "releases/v9.9.9-test-target" "$C3_FIX/current"
printf 'PORT=19999\n' > "$C3_FIX/.env"
printf '#!/bin/bash\nexit 0\n' > "$C3_FIX/launcher.sh"
chmod +x "$C3_FIX/launcher.sh"

C3_OUT="$(run_sweep "$C3_FIX")"
echo "$C3_OUT" | head -20 >&2
C3_JOURNAL="$(cat "$C3_FIX/releases/state.json")"
# The new commit path uses _js_manifest_field, which returns empty on
# unreadable manifest. The `grep -q '^false$'` check fails → branch is
# SKIPPED. Existing not-rollback_safe gate fires (prev.rollback_safe
# is empty, NOT false) — the journal gets a halt event.
assert_not_contains "C3 NO boot_sweep_commit_and_continue (missing manifest)" '"event":"boot_sweep_commit_and_continue"' "$C3_JOURNAL"
assert_contains "C3 halt event (manifest missing → conservative)" '"event":"halt"' "$C3_JOURNAL"

rm -rf "$C3_FIX"

# ===========================================================================
section "C4 — rollback_safe key missing → fail-closed (manifest present, key absent)"

C4_FIX="$(mktemp -d -t c4.XXXXXX)"
make_install_fixture "$C4_FIX" "v9.9.9-test-target" "v9.9.9-test-prev" "__MISSING_KEY__"
seed_journal "$C4_FIX" "v9.9.9-test-target" "v9.9.9-test-prev" "true" "1"
ln -sfn "releases/v9.9.9-test-target" "$C4_FIX/current"
printf 'PORT=19999\n' > "$C4_FIX/.env"
printf '#!/bin/bash\nexit 0\n' > "$C4_FIX/launcher.sh"
chmod +x "$C4_FIX/launcher.sh"

C4_OUT="$(run_sweep "$C4_FIX")"
echo "$C4_OUT" | head -20 >&2
C4_JOURNAL="$(cat "$C4_FIX/releases/state.json")"
# Same: rollback_safe key absent → _js_manifest_field returns empty →
# `grep -q '^false$'` fails → branch SKIPPED. Existing halt fires.
assert_not_contains "C4 NO boot_sweep_commit_and_continue (rollback_safe absent)" '"event":"boot_sweep_commit_and_continue"' "$C4_JOURNAL"
assert_contains "C4 halt event (rollback_safe absent → conservative)" '"event":"halt"' "$C4_JOURNAL"

rm -rf "$C4_FIX"

# ===========================================================================
section "C5 — rollback_safe TRUE → existing sweep-rollback path (commit branch skipped)"

C5_FIX="$(mktemp -d -t c5.XXXXXX)"
make_install_fixture "$C5_FIX" "v9.9.9-test-target" "v9.9.9-test-prev" "true"
seed_journal "$C5_FIX" "v9.9.9-test-target" "v9.9.9-test-prev" "true" "1"
ln -sfn "releases/v9.9.9-test-target" "$C5_FIX/current"
printf 'PORT=19999\n' > "$C5_FIX/.env"
printf '#!/bin/bash\nexit 0\n' > "$C5_FIX/launcher.sh"
chmod +x "$C5_FIX/launcher.sh"

C5_OUT="$(run_sweep "$C5_FIX")"
echo "$C5_OUT" | head -20 >&2
C5_JOURNAL="$(cat "$C5_FIX/releases/state.json")"
# rollback_safe:true → commit branch skipped (it's only for false) →
# the existing sweep-rollback path takes over: quarantine + repoint
# + sweep_rollback event + in_flight cleared.
assert_contains "C5 sweep_rollback event (existing path)" '"event":"sweep_rollback"' "$C5_JOURNAL"
assert_not_contains "C5 NO boot_sweep_commit_and_continue (rollback_safe=true)" '"event":"boot_sweep_commit_and_continue"' "$C5_JOURNAL"
case "$C5_JOURNAL" in
    *'"in_flight":null'*|*"\"in_flight\": null"*) _pass "C5 in_flight cleared" ;;
    *) _fail "C5 in_flight cleared" '"in_flight":null' "$(printf '%s' "$C5_JOURNAL" | grep -oE '"in_flight":[^,}]*')" ;;
esac

rm -rf "$C5_FIX"

# ===========================================================================
section "C6 — promote.sh intent_flip emit (writer-side contract)"

# Verify the writer-side contract in promote.sh: the intent_flip event
# is written with the EXACT shape the consumer (_js_history_has_intent_flip)
# expects. Grep the source files.
PROMOTE_SH="$REPO_ROOT/scripts/upgrade/promote.sh"
LAUNCHER_SH="$REPO_ROOT/launcher.sh"
if grep -q 'journal_history_append intent_flip' "$PROMOTE_SH"; then
    _pass "C6 promote.sh writes intent_flip event"
else
    _fail "C6 promote.sh writes intent_flip event" "present" "missing"
fi
if grep -q 'preflight: intentional promote target \$VERSION' "$PROMOTE_SH"; then
    _pass "C6 promote.sh emit prefix is byte-identical (consumer contract)"
else
    _fail "C6 promote.sh emit prefix" "byte-identical" "different"
fi
if grep -q '_js_history_has_intent_flip' "$LAUNCHER_SH"; then
    _pass "C6 launcher.sh has _js_history_has_intent_flip consumer"
else
    _fail "C6 launcher.sh has _js_history_has_intent_flip consumer" "present" "missing"
fi

# ===========================================================================
section "C7 — Cooldown uname dispatch fix (lib.sh:982 — opportunistic)"

LIB_SH="$REPO_ROOT/scripts/upgrade/lib.sh"
if grep -q 'Darwin|\*BSD\*|\*bsd\*)' "$LIB_SH" \
   && grep -q 'Linux|GNU\*|\*GNU\*)' "$LIB_SH"; then
    _pass "C7 BSD+GNU dispatch arms present in lib.sh"
else
    _fail "C7 BSD+GNU dispatch arms present" "both arms" "missing"
fi
assert_contains "C7 Linux branch uses GNU date -d" 'date -u -d "+${COOLDOWN_S} seconds"' "$(cat "$LIB_SH")"
assert_contains "C7 BSD branch keeps -ju -v form" 'date -ju -v+${COOLDOWN_S}S' "$(cat "$LIB_SH")"

# Live verification on this GNU host: dispatch arms must produce a
# future timestamp (cooldown_until) when the cooldown is armed. On
# pre-fix, the BSD-only ``date -ju -v+...`` form failed silently and
# ``|| until=$(_now_iso)`` disarmed the cooldown. On post-fix, the Linux
# arm fires and produces a future timestamp.
COOLDOWN_EPOCH="$(bash -c '
    _now_epoch() { date +%s; }
    _now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }
    COOLDOWN_S=600
    case "$(uname -s)" in
        Darwin|*BSD*|*bsd*)
            until="$(date -ju -v+${COOLDOWN_S}S -f "%Y-%m-%dT%H:%M:%SZ" "$(_now_iso)" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" \
                || until="$(_now_iso)" ;;
        Linux|GNU*|*GNU*)
            until="$(date -u -d "+${COOLDOWN_S} seconds" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" \
                || until="$(_now_iso)" ;;
        *) until="$(_now_iso)" ;;
    esac
    date -d "$until" +%s 2>/dev/null
')"
NOW_E="$(date +%s)"
if [ -n "$COOLDOWN_EPOCH" ] && [ "$COOLDOWN_EPOCH" -gt "$NOW_E" ]; then
    _pass "C7 cooldown produces a future timestamp on GNU (delta=$((COOLDOWN_EPOCH - NOW_E))s, expect ~600s)"
else
    _fail "C7 cooldown produces a future timestamp on GNU" ">now ($NOW_E)" "got '$COOLDOWN_EPOCH'"
fi

# ===========================================================================
section "Final — summary"

printf '\n== summary ==\n'
printf 'PASS=%d FAIL=%d\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED TESTS:\n%s\n' "$FAILED_TESTS"
    exit 1
fi
printf '=== ALL TESTS PASSED ===\n'
exit 0