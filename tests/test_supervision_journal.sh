#!/bin/bash
# ============================================================================
# tests/test_supervision_journal.sh — journal-stamp completeness class
# (supervision-detection commission P5, mission matrix item 11)
# ============================================================================
# Leader's note (the v0.16.3 unjournaled-promote gap): pins that the
# supervision=/unit=/outcome= stamps land on the txn for EVERY promote
# path — success, refusal (78), halt (B4) — no path skips stamping.
#
#   J1 SUCCESS — a REAL full promote to commit on a fixture ladder (real
#      scripts, real launcher, stub daemon serving /livez+/readyz with the
#      manifest binary_version). The OPEN txn is snapshotted at stop-time
#      via a wrapper stop-ensemble.sh (the fixture repo's copy) — the
#      snapshot must carry all three stamps; the committed journal shows
#      current=vB + in_flight null + a commit history event.
#   J2 REFUSAL-78 — ENSEMBLE_SUPERVISION=unit with no resolvable name:
#      preflight refuses PRE-TXN (exit 78); the journal carries the
#      refusal event (reason token) and NO in_flight txn ever opens —
#      nothing is left unstamped.
#   J3 HALT-B4 — explicit unit + a systemctl stub whose START fails: the
#      promote stamps the txn, stops through the UNIT path (machine-line
#      env handoff observed by the wrapper stop), flips, then the hand-
#      back fails → halt event + B4 leave-txn-open + exit 1, NO nohup
#      fallback. The OPEN flipped txn must still carry the stamps.
#   J4 journal_mark_supervision refuses (journal untouched) with NO txn /
#      a malformed txn — never writes into a non-object in_flight.
#
# Fixture strategy: the test_release_journal.sh throwaway-repo precedent
# (real script copies, git-tagged, stage.sh --skip-build with stub
# binaries) + the REAL repo launcher.sh (so the post-flip nohup restart
# actually boots the stub daemon and the gates pass). Ports are
# 18079-family ephemeral; no real install, DB, or daemon is touched.
#
# Run:
#   bash tests/test_supervision_journal.sh
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"
STUB_DAEMON="$REPO_ROOT/tests/fixtures/supervision_stub_daemon.py"

PASS=0
FAIL=0
SKIP=0
SKIPPED=""
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); }
_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
}
_skip() {
    SKIP=$((SKIP + 1))
    SKIPPED="$SKIPPED
  ⏭ $1"
    printf 'SKIP: %s\n' "$1" >&2
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

# cleanup: always remove the fixture (and any launcher the promotes left
# running) — the suite must leave the host clean.
SUPJOUR_FIXTURE=""
cleanup_supjour() {
    if [ -n "${SUPJOUR_KEEP:-}" ]; then
        echo "SUPJOUR_KEEP set — fixture left at $SUPJOUR_FIXTURE" >&2
        return 0
    fi
    if [ -n "$SUPJOUR_FIXTURE" ] && [ -d "$SUPJOUR_FIXTURE" ]; then
        pkill -TERM -f "supervision_stub_daemon.py --port 18081" 2>/dev/null || true
        pkill -TERM -f "$SUPJOUR_FIXTURE/sbx/launcher.sh" 2>/dev/null || true
        sleep 1
        pkill -KILL -f "supervision_stub_daemon.py --port 18081" 2>/dev/null || true
        rm -rf "$SUPJOUR_FIXTURE"
    fi
}
trap cleanup_supjour EXIT

# json_get <file> <python-expr over d> — robust JSON field extraction
json_get() {
    python3 - "$1" "$2" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    d = json.load(f)
print(eval(sys.argv[2], {"d": d}))  # noqa: S307 — fixture-local, test-controlled
PY
}

HOST_HAS_SYSTEMD=0
[ "$(uname -s)" = "Linux" ] && [ -d /run/systemd/system ] && HOST_HAS_SYSTEMD=1

# ── the throwaway fixture (test_release_journal.sh precedent) ───────────────
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/supjour.XXXXXX")"
FIXTURE="$(cd "$FIXTURE" && pwd)"
SUPJOUR_FIXTURE="$FIXTURE"
FAKE_REPO="$FIXTURE/repo"
FAKE_HOME="$FIXTURE/home"
SBX="$FIXTURE/sbx"
SBX_PORT=18081
PROBE="$FIXTURE/probe"; mkdir -p "$PROBE"
# pre-flight sweep: kill any LEFTOVER stub daemon from an earlier aborted
# run still holding the fixture port (its stale /livez version poisons the
# gates — real debug lesson 2026-09-29). Only OUR OWN stub pattern is
# matched — never a port sweep, never a foreign process.
if pgrep -f "supervision_stub_daemon.py --port $SBX_PORT" > /dev/null 2>&1; then
    pkill -TERM -f "supervision_stub_daemon.py --port $SBX_PORT" 2>/dev/null || true
    sleep 1
    pkill -KILL -f "supervision_stub_daemon.py --port $SBX_PORT" 2>/dev/null || true
fi
mkdir -p "$FAKE_REPO/scripts/upgrade" "$FAKE_REPO/agents/leader" \
         "$FAKE_REPO/daemon/migrations/versions" \
         "$FAKE_REPO/frontend/dist/frontend/browser" \
         "$FAKE_HOME/agents-ensemble"

cp "$UPGRADE_DIR/lib.sh"        "$FAKE_REPO/scripts/upgrade/lib.sh"
cp "$UPGRADE_DIR/promote.sh"    "$FAKE_REPO/scripts/upgrade/promote.sh"
cp "$REPO_ROOT/scripts/stop-ensemble.sh" "$FAKE_REPO/scripts/stop-ensemble.real"
chmod +x "$FAKE_REPO/scripts/upgrade/"*.sh

# WRAPPER stop-ensemble.sh — snapshot probe: the journal state + the env
# handoff AT STOP TIME (the only window where the open stamped txn is
# observable on the success path), then exec the real stop behavior.
cat > "$FAKE_REPO/scripts/stop-ensemble.sh" <<WRAPPER
#!/bin/bash
n="\$(cat "$PROBE/stop.n" 2>/dev/null || echo 0)"; n=\$((n + 1)); printf '%s' "\$n" > "$PROBE/stop.n"
cp "\${1:-}/releases/state.json" "$PROBE/stop\${n}-state.json" 2>/dev/null || true
env | grep -E '^(ENSEMBLE_SUPERVISION_RESULT|SUPERVISION_)' > "$PROBE/stop\${n}-env.txt" 2>/dev/null || true
exec bash "$FAKE_REPO/scripts/stop-ensemble.real" "\$@"
WRAPPER
chmod +x "$FAKE_REPO/scripts/stop-ensemble.sh"

# the REAL launcher (the post-flip nohup restart must boot the stub daemon)
cp "$REPO_ROOT/launcher.sh" "$FAKE_REPO/launcher.sh"
chmod +x "$FAKE_REPO/launcher.sh"
printf 'stub-agent-definition\n' > "$FAKE_REPO/agents/leader/soul.md"
printf 'port: ${PORT:-8088}\n' > "$FAKE_REPO/config.yaml"
printf 'stub-index\n' > "$FAKE_REPO/frontend/dist/frontend/browser/index.html"
printf 'stub-app\n' > "$FAKE_REPO/frontend/dist/frontend/browser/main.js"
printf 'CREATE TABLE x (id int);\n' > "$FAKE_REPO/daemon/migrations/versions/20260101_000001_init.sql"

# stub daemon binaries per release (version literal baked per binary)
make_stub_bin() { # <path> <version>
    {
        printf '#!/bin/bash\n'
        printf 'exec python3 "%s" --port "${PORT:-%s}" --version "%s"\n' \
            "$STUB_DAEMON" "$SBX_PORT" "$2"
    } > "$1"
    chmod +x "$1"
}
BIN_A="$FIXTURE/stub-prod-a"; make_stub_bin "$BIN_A" "1.0.0-ja"
BIN_B="$FIXTURE/stub-prod-b"; make_stub_bin "$BIN_B" "1.0.0-jb"

git -C "$FAKE_REPO" init -q
git -C "$FAKE_REPO" add -A 2>/dev/null
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -qm fixture

VA="v1.0.0-ja"
VB="v1.0.0-jb"

run_stage() {  # run_stage <version> <binary> — own commit + tag first
    # (stage.sh's exact-tag guard: `git describe --exact-match HEAD` returns
    # only ONE of two tags on the same commit — the precedent gives each
    # version its own commit so vA never shadows vB.)
    (
        unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT SYSTEMCTL_BIN 2>/dev/null || true
        printf 'fixture-%s\n' "$1" > "$FAKE_REPO/FIXTURE_$1"
        git -C "$FAKE_REPO" add -A 2>/dev/null
        git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t \
            commit -qm "fixture-$1" >/dev/null 2>&1
        git -C "$FAKE_REPO" tag "$1" 2>/dev/null
        HOME="$FAKE_HOME" VERSION="$1" TARGET=sandbox \
        INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
        ENSEMBLE_BINARY_VERSION="${1#v}" \
        bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox --skip-build "$2"
    )
}

# stage both releases + seed current=VA (first-promote-equivalent)
cp "$UPGRADE_DIR/stage.sh" "$FAKE_REPO/scripts/upgrade/stage.sh"
chmod +x "$FAKE_REPO/scripts/upgrade/stage.sh"
mkdir -p "$SBX"
run_stage "$VA" "$BIN_A" > "$PROBE/stage-a.log" 2>&1 || {
    echo "FATAL: stage VA failed:"; tail -5 "$PROBE/stage-a.log"
}
run_stage "$VB" "$BIN_B" > "$PROBE/stage-b.log" 2>&1 || {
    echo "FATAL: stage VB failed:"; tail -5 "$PROBE/stage-b.log"
}
ln -sfn "releases/$VA" "$SBX/current"
printf 'PORT=%s\n' "$SBX_PORT" > "$SBX/.env"
(
    export INSTALL_DIR="$SBX"
    . "$FAKE_REPO/scripts/upgrade/lib.sh" >/dev/null 2>&1
    journal_init
    journal_set_current "$VA"
) > /dev/null 2>&1

# ===========================================================================
section "J1 — SUCCESS: full promote to commit carries the stamps"

J1_LOG="$PROBE/j1-promote.log"
(
    unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT SYSTEMCTL_BIN 2>/dev/null || true
    HOME="$FAKE_HOME" VERSION="$VB" TARGET=sandbox \
    INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    LIVEZ_BUDGET_S=8 READYZ_BUDGET_S=8 ENSEMBLE_PROMOTE_SOAK_S=0 \
    bash "$FAKE_REPO/scripts/upgrade/promote.sh" sandbox
) > "$J1_LOG" 2>&1
J1_RC=$?
J1_OUT="$(cat "$J1_LOG")"
assert_eq "J1 promote exits 0 (committed)" "0" "$J1_RC"

# the stop-time snapshot of the OPEN txn must carry all three stamps
SNAP="$PROBE/stop1-state.json"
if [ -f "$SNAP" ]; then
    assert_eq "J1 snapshot: supervision stamp" "SCRIPT_NOHUP" "$(json_get "$SNAP" 'd["in_flight"].get("supervision")')"
    assert_eq "J1 snapshot: unit stamp (null on the script arm)" "None" "$(json_get "$SNAP" 'd["in_flight"].get("unit")')"
    assert_eq "J1 snapshot: outcome stamp (auto×script = conforming)" "conforming" "$(json_get "$SNAP" 'd["in_flight"].get("outcome")')"
else
    _fail "J1 stop-time snapshot exists" "file" "missing"
fi
# script arm: NO machine-line env handed to the stop child
if [ -f "$PROBE/stop1-env.txt" ]; then
    assert_eq "J1 script arm: no env handoff to the stop child" "" "$(cat "$PROBE/stop1-env.txt")"
else
    _fail "J1 stop env probe exists" "file" "missing"
fi

# committed journal: current=VB, txn closed, commit history event
FINAL="$SBX/releases/state.json"
assert_eq "J1 committed current=VB" "$VB" "$(json_get "$FINAL" 'd["current"]')"
assert_eq "J1 txn closed (in_flight null)" "None" "$(json_get "$FINAL" 'd["in_flight"]')"
assert_contains "J1 commit history event" '"event":"commit"' "$(cat "$FINAL")"
assert_contains "J1 promote output carries the RESULT machine line" "ENSEMBLE_SUPERVISION_RESULT=SCRIPT_NOHUP" "$J1_OUT"
assert_contains "J1 promote output carries the OUTCOME machine line" "ENSEMBLE_SUPERVISION_OUTCOME=conforming" "$J1_OUT"

# cleanup: stop the nohup'd launcher + stub daemon the promote left
# running (targeted fixture-anchored TERMs — the promote's own stop path
# is already exercised inside the run)
pkill -TERM -f "$SBX/launcher.sh" 2>/dev/null || true
pkill -TERM -f "supervision_stub_daemon.py --port $SBX_PORT" 2>/dev/null || true
sleep 1
pgrep -f "$SBX/launcher.sh" > /dev/null 2>&1 \
    && pkill -KILL -f "$SBX/launcher.sh" 2>/dev/null || true
pgrep -f "supervision_stub_daemon.py --port $SBX_PORT" > /dev/null 2>&1 \
    && pkill -KILL -f "supervision_stub_daemon.py --port $SBX_PORT" 2>/dev/null || true

# ===========================================================================
section "J2 — REFUSAL-78: pre-txn refusal, nothing unstamped left behind"

J2_LOG="$PROBE/j2-promote.log"
(
    unset ENSEMBLE_RESTART_UNIT SYSTEMCTL_BIN 2>/dev/null || true
    HOME="$FAKE_HOME" VERSION="$VA" TARGET=sandbox \
    INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    ENSEMBLE_SUPERVISION=unit \
    bash "$FAKE_REPO/scripts/upgrade/promote.sh" sandbox
) > "$J2_LOG" 2>&1
J2_RC=$?
J2_OUT="$(cat "$J2_LOG")"
assert_eq "J2 explicit-unit unresolved: exit 78" "78" "$J2_RC"
assert_contains "J2 refusal message (never silent-degrade)" "never silent-degrade" "$J2_OUT"
FINAL2="$SBX/releases/state.json"
assert_eq "J2 NO txn opened (nothing unstamped)" "None" "$(json_get "$FINAL2" 'd["in_flight"]')"
assert_contains "J2 refusal journaled with the token" "reason=supervision-unit-unresolved" "$(cat "$FINAL2")"
assert_eq "J2 journal still parses as JSON" "ok" "$(json_get "$FINAL2" '"ok"')"
# current untouched at LKG
assert_eq "J2 current untouched" "$VB" "$(json_get "$FINAL2" 'd["current"]')"

# ===========================================================================
section "J3 — HALT-B4: hand-back failure leaves the OPEN stamped txn"

if [ "$HOST_HAS_SYSTEMD" = "1" ]; then
    # systemctl stub: stop ok + already-stopped polls, hand-back START fails
    SCJ="$FIXTURE/systemctl-stub"; mkdir -p "$(dirname "$SCJ")"
    cat > "$SCJ" <<'STUB'
#!/bin/bash
echo "$*" >> "${SCJ_LOG:-/dev/null}"
sub="$1"; shift
case "$sub" in
    is-active) echo inactive; exit 0 ;;
    show) prop=""; while [ $# -gt 0 ]; do case "$1" in -p) prop="$2"; shift 2;; --value) shift;; *) shift;; esac; done
             case "$prop" in MainPID) echo 0;; ControlGroup) echo "";; Restart) echo no;; *) echo "";; esac; exit 0 ;;
    stop) exit 0 ;;
    start) echo "start is denied for this unit (stub)" >&2; exit 1 ;;
    *) exit 0 ;;
esac
STUB
    chmod +x "$SCJ"
    : > "$PROBE/scj.log"

    # re-seed: current back at VA with a clean journal (fresh ladder)
    ln -sfn "releases/$VA" "$SBX/current"
    printf 'PORT=%s\n' "$SBX_PORT" > "$SBX/.env"
    (
        export INSTALL_DIR="$SBX"
        . "$FAKE_REPO/scripts/upgrade/lib.sh" >/dev/null 2>&1
        journal_init
        journal_set_current "$VA"
    ) > /dev/null 2>&1
    : > "$PROBE/stop.n"

    J3_LOG="$PROBE/j3-promote.log"
    (
        ENSEMBLE_SUPERVISION=unit \
        HOME="$FAKE_HOME" VERSION="$VB" TARGET=sandbox \
        INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
        ENSEMBLE_RESTART_UNIT=ensemble-j3.service \
        SYSTEMCTL_BIN="$SCJ" SCJ_LOG="$PROBE/scj.log" \
        LIVEZ_BUDGET_S=6 READYZ_BUDGET_S=6 ENSEMBLE_PROMOTE_SOAK_S=0 \
        bash "$FAKE_REPO/scripts/upgrade/promote.sh" sandbox
    ) > "$J3_LOG" 2>&1
    J3_RC=$?
    J3_OUT="$(cat "$J3_LOG")"
    assert_eq "J3 hand-back failure: exit 1 (halt, never false-success)" "1" "$J3_RC"

    # B4: txn OPEN, flipped, STAMPED
    FINAL3="$SBX/releases/state.json"
    assert_contains "J3 halt event cites the hand-back failure" '"event":"halt"' "$(cat "$FINAL3")"
    assert_contains "J3 halt detail names unit hand-back" "unit hand-back" "$(cat "$FINAL3")"
    assert_eq "J3 txn left OPEN (B4)" "dict" "$(json_get "$FINAL3" 'type(d["in_flight"]).__name__')"
    assert_eq "J3 txn flipped=true" "True" "$(json_get "$FINAL3" 'd["in_flight"].get("flipped")')"
    assert_eq "J3 open txn: supervision stamp" "UNIT_MANAGED" "$(json_get "$FINAL3" 'd["in_flight"].get("supervision")')"
    assert_eq "J3 open txn: unit stamp" "ensemble-j3.service" "$(json_get "$FINAL3" 'd["in_flight"].get("unit")')"
    assert_eq "J3 open txn: outcome stamp" "conforming" "$(json_get "$FINAL3" 'd["in_flight"].get("outcome")')"
    # the halt event payload names declared/verified/unit (additive fields)
    assert_contains "J3 halt payload carries declared×verified×unit" "declared=unit verified=UNIT_MANAGED unit=ensemble-j3.service" "$(cat "$FINAL3")"
    # machine lines surfaced on the promote output
    assert_contains "J3 RESULT machine line" "ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-j3.service" "$J3_OUT"
    assert_contains "J3 OUTCOME machine line" "ENSEMBLE_SUPERVISION_OUTCOME=conforming" "$J3_OUT"
    assert_contains "J3 NO nohup fallback" "NO nohup fallback" "$J3_OUT"
    # the flip DID happen at the symlink level while the journal stays at
    # the pre-promote current — the B4 divergence the boot sweep reconciles
    # (journal halt lane for hand-back failure never repoints current)
    assert_eq "J3 journal current stays at LKG (halt lane never repoints)" "$VA" "$(json_get "$FINAL3" 'd["current"]')"
    assert_eq "J3 symlink flipped to VB (sweep-owned recovery)" "releases/$VB" "$(readlink "$SBX/current")"
    # stop-time: the machine-line env handoff reached the stop child
    if [ -f "$PROBE/stop1-env.txt" ]; then
        assert_contains "J3 stop child received the machine-line env" "ENSEMBLE_SUPERVISION_RESULT=UNIT_MANAGED:ensemble-j3.service" "$(cat "$PROBE/stop1-env.txt")"
    else
        _fail "J3 stop env probe exists" "file" "missing"
    fi
    # stop routed through the UNIT path (stub observed the stop)
    assert_contains "J3 stop dispatched via systemctl stub" "stop ensemble-j3.service" "$(cat "$PROBE/scj.log")"
    assert_contains "J3 hand-back start attempted via stub" "start ensemble-j3.service" "$(cat "$PROBE/scj.log")"
    assert_not_contains "J3 7b trap: success-path lines clean of the literal" "systemctl start" "$(printf '%s' "$J3_OUT" | grep -v 'falling back\|failed\|FAILED\|did not come up' || true)"
else
    _skip "J3 halt-B4 unit-path pin — FENCE: no /run/systemd/system on this host"
fi

# ===========================================================================
section "J4 — journal_mark_supervision refuses on no-txn / malformed txn"

J4_FIX="$FIXTURE/j4"; mkdir -p "$J4_FIX/releases"
printf '{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}' \
    > "$J4_FIX/releases/state.json"
J4A_OUT="$(
    (
        export INSTALL_DIR="$J4_FIX"
        . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
        SUPERVISION_MODE=script; SUPERVISION_STATE=SCRIPT_NOHUP; SUPERVISION_UNIT=""
        journal_mark_supervision
        echo "ms-rc=$?"
    ) 2>&1
)"
assert_contains "J4a no-txn: refuses with WARN" "no in_flight txn" "$J4A_OUT"
assert_contains "J4a no-txn: rc 1 (journal untouched)" "ms-rc=1" "$J4A_OUT"
assert_eq "J4a journal byte-identical (untouched)" "None" "$(json_get "$J4_FIX/releases/state.json" 'd["in_flight"]')"

printf '{"current":null,"previous":null,"in_flight":[1,2],"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}' \
    > "$J4_FIX/releases/state.json"
J4B_OUT="$(
    (
        export INSTALL_DIR="$J4_FIX"
        . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1
        SUPERVISION_MODE=script; SUPERVISION_STATE=SCRIPT_NOHUP; SUPERVISION_UNIT=""
        journal_mark_supervision
        echo "ms-rc=$?"
    ) 2>&1
)"
assert_contains "J4b malformed (array) txn: refuses" "not a JSON object" "$J4B_OUT"
assert_contains "J4b malformed: rc 1" "ms-rc=1" "$J4B_OUT"

# ===========================================================================
section "summary"
printf 'PASS=%s FAIL=%s SKIP=%s\n' "$PASS" "$FAIL" "$SKIP"
if [ "$SKIP" -gt 0 ]; then
    printf 'SKIPPED (named fences):%s\n' "$SKIPPED"
fi
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED TESTS:%s\n' "$FAILED_TESTS"
    exit 1
fi
printf '\n=== ALL TESTS PASSED ===\n'
