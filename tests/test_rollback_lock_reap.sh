#!/bin/bash
# ============================================================================
# tests/test_rollback_lock_reap.sh — fix #6 (incident 2026-11-14 §5.5):
# age-bounded prune of rollback.lock.d.stale.<pid> siblings at acquire time.
# ============================================================================
# Why a NEW pack: the prune rule spans BOTH shell twins (scripts/upgrade/
# lib.sh lock_acquire + launcher.sh _js_lock_acquire boot sweep) as one
# contract; the existing homes are either heavyweight e2e suites
# (test_release_journal.sh — promote ceremonies) or launcher-cell-scoped
# (test_launcher.sh). A focused pack keeps the family's test surface tight
# and runnable, and pins BOTH twins against the SAME boundary matrix:
#
#   R1 over the bound  → reaped (mtime age strictly > bound)
#   R2 under the bound → survives (a freshly-broken sibling is forensics)
#   R3 non-<digits> sibling names → NEVER touched (operator evidence)
#   R4 the LIVE lock (no suffix) is structurally out of the prune's reach
#   R5 acquire still succeeds with the prune riding it (protocol intact)
#   R6 garbage knob env → default bound (never a 0-reap of fresh siblings)
#   R7 lib.sh twin: the reap is journaled (lock-stale-prune history row)
#
# Hermetic: fixture INSTALL_DIRs are throwaway mktemp dirs; the twins are
# sourced in subshells (launcher.sh has a source guard — the sanctioned
# test_launcher.sh pattern). No real install, no launchctl, no daemon.
#
# Run:
#   bash tests/test_rollback_lock_reap.sh
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIBSH="$REPO_ROOT/scripts/upgrade/lib.sh"
LAUNCHER="$REPO_ROOT/launcher.sh"
PY="$(command -v python3)"
BOUND="${ROLLBACK_LOCK_STALE_PRUNE_S:-604800}"   # the documented default

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
    if [ "$expected" = "$actual" ]; then _pass "$name"; else _fail "$name" "$expected" "$actual"; fi
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

NOW="$(date +%s)"

# set_mtime <epoch> <path> — platform-branched touch (BSD vs GNU date).
set_mtime() {
    local epoch="$1" path="$2" stamp
    # 12-digit stamp (CCYYMMDDhhmm — BSD touch rejects 14 bare digits;
    # seconds need the .SS dot form). Minute truncation is absorbed by
    # the ±300s boundary margins.
    case "$(uname -s)" in
        Darwin|*BSD*) stamp="$(date -r "$epoch" +%Y%m%d%H%M)" ;;
        Linux|GNU*)   stamp="$(date -d "@$epoch" +%Y%m%d%H%M)" ;;
        *) stamp="$(date -r "$epoch" +%Y%m%d%H%M 2>/dev/null || date -d "@$epoch" +%Y%m%d%H%M)" ;;
    esac
    touch -t "$stamp" "$path"
}

# fixture <name> — an install dir with releases/ + pre-seeded siblings.
#   over   : age bound+300   → MUST be reaped (R1)
#   under  : age bound-300   → MUST survive (R2)
#   fresh  : age 60          → MUST survive (R2 fresh-forensics shape)
#   odd    : rollback.lock.d.stale.notapid, ancient → MUST survive (R3)
#   nosuf  : rollback.lock.d.stale, ancient → MUST survive (R3)
#   junk   : rollback.lock.d.stale.7004junk, ancient → MUST survive (R3,
#            MINOR ④ twins-drift pin: digit-FIRST + trailing junk — the
#            pre-fix shell matcher [0-9]* PRUNED it while the python twin
#            (fullmatch \d+) SURVIVED it; all three now require
#            digits-to-EOL)
make_fixture() {
    local root
    root="$(mktemp -d -t reap-XXXXXX)"
    mkdir -p "$root/releases"
    : > "$root/releases/rollback.lock.d.stale.7001";   set_mtime "$(( NOW - BOUND - 300 ))" "$root/releases/rollback.lock.d.stale.7001"
    : > "$root/releases/rollback.lock.d.stale.7002";   set_mtime "$(( NOW - BOUND + 300 ))" "$root/releases/rollback.lock.d.stale.7002"
    : > "$root/releases/rollback.lock.d.stale.7003";   set_mtime "$(( NOW - 60 ))"          "$root/releases/rollback.lock.d.stale.7003"
    : > "$root/releases/rollback.lock.d.stale.notapid"; set_mtime "$(( NOW - BOUND - 300 ))" "$root/releases/rollback.lock.d.stale.notapid"
    : > "$root/releases/rollback.lock.d.stale.7004junk"; set_mtime "$(( NOW - BOUND - 300 ))" "$root/releases/rollback.lock.d.stale.7004junk"
    : > "$root/releases/rollback.lock.d.stale";         set_mtime "$(( NOW - BOUND - 300 ))" "$root/releases/rollback.lock.d.stale"
    printf '%s' "$root"
}

# ── A. lib.sh twin (the promote/rollback preflight acquire) ────────────────
section "A — lib.sh twin: lock_acquire rides the prune"
A_FIX="$(make_fixture)"
# lib.sh's _log writes to STDOUT when ENSEMBLE_UPGRADE_LOG_FILE is unset
# (the launcher twin's _log goes to stderr — different surface, same rule);
# merge both streams so the reap-line asserts hold against either twin.
A_OUT="$(cd /tmp && INSTALL_DIR="$A_FIX" bash -c '
    . "'"$LIBSH"'" >/dev/null 2>&1
    journal_init >/dev/null 2>&1 || true
    lock_acquire 0
    echo "acquire-rc=$?"
' 2>&1)"
assert_contains "A acquire succeeds with the prune riding it" "acquire-rc=0" "$A_OUT"
assert_eq "A R1 over-bound sibling reaped" "" "$(ls "$A_FIX/releases/rollback.lock.d.stale.7001" 2>/dev/null)"
assert_contains "A R1 reap is logged with the age + bound" "reaped stale-broken sibling rollback.lock.d.stale.7001 (age" "$A_OUT"
assert_contains "A R1 reap log cites the incident" "incident 2026-11-14" "$A_OUT"
[ -f "$A_FIX/releases/rollback.lock.d.stale.7002" ] && _pass "A R2 under-bound sibling survives" || _fail "A R2 under-bound sibling survives" "present" "reaped"
[ -f "$A_FIX/releases/rollback.lock.d.stale.7003" ] && _pass "A R2 fresh sibling survives (forensics window)" || _fail "A R2 fresh sibling survives (forensics window)" "present" "reaped"
[ -f "$A_FIX/releases/rollback.lock.d.stale.notapid" ] && _pass "A R3 non-pid sibling name NEVER touched" || _fail "A R3 non-pid sibling name NEVER touched" "present" "reaped"
[ -f "$A_FIX/releases/rollback.lock.d.stale.7004junk" ] && _pass "A R3 digit-first junk sibling survives (MINOR ④ twins-drift pin)" || _fail "A R3 digit-first junk sibling survives (MINOR ④ twins-drift pin)" "present" "reaped"
[ -f "$A_FIX/releases/rollback.lock.d.stale" ] && _pass "A R3 suffix-less sibling NEVER touched" || _fail "A R3 suffix-less sibling NEVER touched" "present" "reaped"
[ -d "$A_FIX/releases/rollback.lock.d" ] && _pass "A R4 the LIVE lock is untouched (we hold it)" || _fail "A R4 the LIVE lock is untouched (we hold it)" "present" "gone"
# protocol triple intact after the prune rode the acquire
for f in owner run_id heartbeat; do
    [ -s "$A_FIX/releases/rollback.lock.d/$f" ] && _pass "A R5 protocol file $f written" || _fail "A R5 protocol file $f written" "non-empty" "missing"
done
# R7 — the reap is journaled (history row; journal_init bootstrapped above)
A_J="$A_FIX/releases/state.json"
if grep -q "lock-stale-prune" "$A_J" 2>/dev/null; then
    assert_contains "A R7 reap journaled as a lock-stale-prune history row" "rollback.lock.d.stale.7001" "$(cat "$A_J")"
else
    _fail "A R7 reap journaled as a lock-stale-prune history row" "lock-stale-prune row in state.json" "$(head -c 200 "$A_J" 2>/dev/null)"
fi
rm -rf "$A_FIX"

# R6 (lib twin) — garbage knob env falls back to the DEFAULT bound (a
# garbage bound reading as 0 would reap the UNDER-default sibling below).
section "B — lib.sh twin: garbage knob env → documented default (never 0-reap)"
B_FIX="$(make_fixture)"
B_OUT="$(cd /tmp && INSTALL_DIR="$B_FIX" ROLLBACK_LOCK_STALE_PRUNE_S=garbage bash -c '
    . "'"$LIBSH"'" >/dev/null 2>&1
    lock_acquire 0
    echo "acquire-rc=$?"
' 2>/dev/null)"
assert_contains "B acquire succeeds under a garbage knob" "acquire-rc=0" "$B_OUT"
[ -f "$B_FIX/releases/rollback.lock.d.stale.7002" ] && _pass "B garbage knob → default bound (under-default sibling survives)" || _fail "B garbage knob → default bound (under-default sibling survives)" "present (default bound applied)" "reaped (bound read as 0)"
assert_eq "B R1 over-bound still reaped under the default fallback" "" "$(ls "$B_FIX/releases/rollback.lock.d.stale.7001" 2>/dev/null)"
rm -rf "$B_FIX"

# ── C. launcher twin (the BOOT sweep acquire — self-contained) ─────────────
section "C — launcher twin: _js_lock_acquire rides the prune"
C_FIX="$(make_fixture)"
C_LOG="$C_FIX/acquire.log"
C_OUT="$(cd /tmp && bash -c '
    . "'"$LAUNCHER"'" >/dev/null 2>&1
    _js_lock_acquire "'"$C_FIX"'" 0
    echo "acquire-rc=$?"
' 2>"$C_LOG")"
assert_contains "C launcher acquire succeeds with the prune riding it" "acquire-rc=0" "$C_OUT"
assert_eq "C R1 over-bound sibling reaped" "" "$(ls "$C_FIX/releases/rollback.lock.d.stale.7001" 2>/dev/null)"
assert_contains "C R1 reap is logged via the launcher _log surface" "reaped stale-broken pipeline-lock sibling rollback.lock.d.stale.7001" "$(cat "$C_LOG")"
[ -f "$C_FIX/releases/rollback.lock.d.stale.7002" ] && _pass "C R2 under-bound sibling survives" || _fail "C R2 under-bound sibling survives" "present" "reaped"
[ -f "$C_FIX/releases/rollback.lock.d.stale.7003" ] && _pass "C R2 fresh sibling survives" || _fail "C R2 fresh sibling survives" "present" "reaped"
[ -f "$C_FIX/releases/rollback.lock.d.stale.notapid" ] && _pass "C R3 non-pid sibling name NEVER touched" || _fail "C R3 non-pid sibling name NEVER touched" "present" "reaped"
[ -f "$C_FIX/releases/rollback.lock.d.stale.7004junk" ] && _pass "C R3 digit-first junk sibling survives (MINOR ④ twins-drift pin)" || _fail "C R3 digit-first junk sibling survives (MINOR ④ twins-drift pin)" "present" "reaped"
[ -f "$C_FIX/releases/rollback.lock.d.stale" ] && _pass "C R3 suffix-less sibling NEVER touched" || _fail "C R3 suffix-less sibling NEVER touched" "present" "reaped"
[ -d "$C_FIX/releases/rollback.lock.d" ] && _pass "C R4 the LIVE lock is untouched (we hold it)" || _fail "C R4 the LIVE lock is untouched (we hold it)" "present" "gone"
rm -rf "$C_FIX"

# R6 (launcher twin) — garbage knob env → the inline default fallback.
section "D — launcher twin: garbage knob env → documented default"
D_FIX="$(make_fixture)"
D_OUT="$(cd /tmp && ROLLBACK_LOCK_STALE_PRUNE_S=not-a-number bash -c '
    . "'"$LAUNCHER"'" >/dev/null 2>&1
    echo "knob=$ROLLBACK_LOCK_STALE_PRUNE_S"
    _js_lock_acquire "'"$D_FIX"'" 0
    echo "acquire-rc=$?"
' 2>/dev/null)"
assert_contains "D launcher knob falls back to the default on garbage env" "knob=$BOUND" "$D_OUT"
assert_contains "D acquire succeeds" "acquire-rc=0" "$D_OUT"
[ -f "$D_FIX/releases/rollback.lock.d.stale.7002" ] && _pass "D garbage knob → default bound (under-default sibling survives)" || _fail "D garbage knob → default bound (under-default sibling survives)" "present" "reaped"
rm -rf "$D_FIX"

# ── E. python twin (daemon/tools/upgrade_journal.py) — the cross-language
# mirror pin (MINOR ④): the SAME fixture through
# upgrade_journal._prune_stale_lock_siblings; every survival/reap verdict
# must agree with the shell twins cell-for-cell (the drift this pack
# exists to catch was exactly a shell-vs-python matcher divergence).
section "E — python twin: _prune_stale_lock_siblings (the cross-language mirror)"
E_FIX="$(make_fixture)"
E_OUT="$(REPO_ROOT="$REPO_ROOT" INSTALL_DIR="$E_FIX" "$PY" - <<'EPY'
import os
from pathlib import Path
import importlib.machinery, importlib.util, types, sys

REPO = Path(os.environ["REPO_ROOT"])
m = types.ModuleType("daemon"); m.__path__ = ["daemon"]
c = types.ModuleType("daemon.constants"); c.is_reserved_source = lambda x: False
sys.modules["daemon"] = m; sys.modules["daemon.constants"] = c
loader = importlib.machinery.SourceFileLoader(
    "uj_reap", str(REPO / "daemon" / "tools" / "upgrade_journal.py"))
spec = importlib.util.spec_from_loader("uj_reap", loader)
uj = importlib.util.module_from_spec(spec); sys.modules["uj_reap"] = uj
loader.exec_module(uj)

inst = Path(os.environ["INSTALL_DIR"])
reaped = uj._prune_stale_lock_siblings(inst)
rel = inst / "releases"
def exists(name):
    return "present" if (rel / name).exists() else "REAPED"
print(f"reaped-count={reaped}")
for name in ("rollback.lock.d.stale.7001", "rollback.lock.d.stale.7002",
             "rollback.lock.d.stale.7003", "rollback.lock.d.stale.notapid",
             "rollback.lock.d.stale", "rollback.lock.d.stale.7004junk"):
    print(f"{name}={exists(name)}")
EPY
)"
assert_contains "E python twin reaped exactly the over-bound sibling (count=1)" "reaped-count=1" "$E_OUT"
assert_contains "E R1 over-bound sibling REAPED" "rollback.lock.d.stale.7001=REAPED" "$E_OUT"
assert_contains "E R2 under-bound sibling survives" "rollback.lock.d.stale.7002=present" "$E_OUT"
assert_contains "E R2 fresh sibling survives" "rollback.lock.d.stale.7003=present" "$E_OUT"
assert_contains "E R3 non-pid sibling survives" "rollback.lock.d.stale.notapid=present" "$E_OUT"
assert_contains "E R3 suffix-less sibling survives" "rollback.lock.d.stale=present" "$E_OUT"
assert_contains "E R3 digit-first junk sibling survives (the MINOR ④ drift cell)" "rollback.lock.d.stale.7004junk=present" "$E_OUT"
rm -rf "$E_FIX"

# ── summary ────────────────────────────────────────────────────────────────
section "summary"
printf 'PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED TESTS:%s\n' "$FAILED_TESTS"
    exit 1
fi
printf '\n=== ALL TESTS PASSED ===\n'
