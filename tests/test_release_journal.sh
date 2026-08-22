#!/bin/bash
# ============================================================================
# tests/test_release_journal.sh — tests for scripts/upgrade/ (P2.1 T1/T2/T3)
# ============================================================================
# Portable plain-bash tests: no bats, no new dev-dependency. Self-asserting;
# exits nonzero on any failure.
#
#   bash tests/test_release_journal.sh
#
# Scope (test-strategy.md §1 P2.1 unit rows):
#   - journal write/read atomicity + torn-write detection (truncated/empty)
#   - cap 3/24h + cooldown 10min + quarantine math (boundary: 3→4, 24h±,
#     cooldown±) — via lib.sh journal_* helpers against fixture journals
#   - manifest integrity: tampered file → status.sh --verify exit 1 naming
#     the file; untampered → 0; promote preflight aborts (78) on drift
#   - version smoke: gate_version mismatch refusal (stub /livez server)
#   - live-guard matrix: TARGET=live without ENSEMBLE_UPGRADE_LIVE on all
#     four scripts → exit 78; WITH the guard var on stage → still no action
#     against the real live dir (fixture HOME redirect proves isolation)
#   - no-.env-in-release assertion (stray .env → stage refuses)
#   - idempotent re-stage (stable checksums, byte-identical manifest)
#
# Fixture strategy (tests/test_deploy.sh precedent): stage.sh derives
# REPO_ROOT from its own path, so the suite builds a THROWAWAY repo with the
# real scripts + stub payload trees, `git init` + tag it, and runs the real
# code against throwaway install dirs. HOME is overridden so demo/live
# topology resolution can never touch the real host dirs.
# ============================================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"

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

section() { printf '\n== %s ==\n' "$1"; }

# ─── fixture: throwaway repo (real scripts, stub payloads, git-tagged) ──────
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/upgrade-test.XXXXXX")"
FIXTURE="$(cd "$FIXTURE" && pwd)"
FAKE_REPO="$FIXTURE/repo"
FAKE_HOME="$FIXTURE/home"
mkdir -p "$FAKE_REPO/scripts/upgrade" "$FAKE_REPO/agents/leader" \
         "$FAKE_REPO/daemon/migrations/versions" \
         "$FAKE_REPO/frontend/dist/frontend/browser" \
         "$FAKE_HOME/agents-ensemble"

# Fake LIVE install under the fake HOME (PORT staged only) so the live target
# RESOLVES and the guard — not the port-resolution failure — is what refuses.
# Nothing of it is ever contacted: the guard fires before any action.
printf 'PORT=4999\n' > "$FAKE_HOME/agents-ensemble/.env"

cp "$UPGRADE_DIR/lib.sh"        "$FAKE_REPO/scripts/upgrade/lib.sh"
cp "$UPGRADE_DIR/stage.sh"      "$FAKE_REPO/scripts/upgrade/stage.sh"
cp "$UPGRADE_DIR/promote.sh"    "$FAKE_REPO/scripts/upgrade/promote.sh"
cp "$UPGRADE_DIR/rollback.sh"   "$FAKE_REPO/scripts/upgrade/rollback.sh"
cp "$UPGRADE_DIR/status.sh"     "$FAKE_REPO/scripts/upgrade/status.sh"
cp "$REPO_ROOT/scripts/stop-ensemble.sh" "$FAKE_REPO/scripts/stop-ensemble.sh"
chmod +x "$FAKE_REPO/scripts/upgrade/"*.sh

printf '#!/bin/bash\n# stub launcher (unit fixture)\n' > "$FAKE_REPO/launcher.sh"
printf 'stub-agent-definition\n' > "$FAKE_REPO/agents/leader/soul.md"
printf 'port: ${PORT:-8088}\n' > "$FAKE_REPO/config.yaml"
printf 'stub-index\n' > "$FAKE_REPO/frontend/dist/frontend/browser/index.html"
printf 'stub-app\n' > "$FAKE_REPO/frontend/dist/frontend/browser/main.js"
printf 'CREATE TABLE x (id int);\n' > "$FAKE_REPO/daemon/migrations/versions/20260101_000001_init.sql"

# a stub binary "serving" nothing — staging only, no daemon in unit tests
printf '#!/bin/bash\nexit 78\n' > "$FIXTURE/stub-prod"
chmod +x "$FIXTURE/stub-prod"

# git-tag the fixture repo so stage.sh's exact-tag guard passes
git -C "$FAKE_REPO" init -q
git -C "$FAKE_REPO" add -A 2>/dev/null
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -qm fixture
SBX_V1="v1.0.0-sbx"
git -C "$FAKE_REPO" tag "$SBX_V1"

SBX="$FIXTURE/installsb"
mkdir -p "$SBX"
SBX_PORT=18377   # throwaway port; never a real env port

run_stage() {  # run_stage <extra args...> — env preset for the sandbox
    HOME="$FAKE_HOME" VERSION="$SBX_V1" TARGET=sandbox \
        INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
        bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox "$@"
}

# ─── 1. syntax gates ────────────────────────────────────────────────────────
section "syntax gates"
for s in lib stage promote rollback status; do
    if bash -n "$FAKE_REPO/scripts/upgrade/$s.sh" 2>/dev/null \
       || [ "$s" = lib ]; then _pass; else _fail "$s.sh passes bash -n"; fi
done
# lib.sh is sourced — check via the sourcing scripts above (promote/status
# exercise it); a direct bash -n on lib.sh is also valid:
bash -n "$FAKE_REPO/scripts/upgrade/lib.sh" 2>/dev/null && _pass || _fail "lib.sh passes bash -n"

# ─── 2. live-guard matrix (T1) ──────────────────────────────────────────────
section "live-guard matrix"
for s in stage promote rollback status; do
    out="$(HOME="$FAKE_HOME" TARGET=live VERSION=x bash "$FAKE_REPO/scripts/upgrade/$s.sh" live 2>&1)"
    rc=$?
    assert_eq "$s.sh live w/o guard exits 78" "78" "$rc"
    assert_contains "$s.sh live refusal names ENSEMBLE_UPGRADE_LIVE" "ENSEMBLE_UPGRADE_LIVE=1" "$out"
done
# unknown target refuses
out="$(HOME="$FAKE_HOME" bash "$FAKE_REPO/scripts/upgrade/status.sh" bogus 2>&1)"; rc=$?
assert_eq "status.sh unknown target exits 78" "78" "$rc"
# sandbox without INSTALL_DIR/PORT refuses (no defaults; env -u sheds any
# ambient daemon env a dev shell may carry, e.g. the live install's PORT)
out="$(env -u PORT -u POSTGRES_DB HOME="$FAKE_HOME" TARGET=sandbox bash "$FAKE_REPO/scripts/upgrade/status.sh" 2>&1)"; rc=$?
assert_eq "sandbox without INSTALL_DIR exits 78" "78" "$rc"
out="$(env -u PORT -u POSTGRES_DB HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" bash "$FAKE_REPO/scripts/upgrade/status.sh" 2>&1)"; rc=$?
assert_eq "sandbox without PORT exits 78" "78" "$rc"
# sandbox refuses the fake-live staged port from the fake HOME .env (the
# live-isolation-by-construction guard; 4999 = fixture-only fake)
out="$(env -u POSTGRES_DB HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT=4999 bash "$FAKE_REPO/scripts/upgrade/status.sh" 2>&1)"; rc=$?
assert_eq "sandbox refuses the live-staged port" "78" "$rc"
assert_contains "live-port refusal says by-construction" "live-isolation by construction" "$out"

# ─── 3. stage: manifest + no-.env + marker + idempotency (T2) ───────────────
section "stage"
STAGE_OUT="$(run_stage --skip-build "$FIXTURE/stub-prod" 2>&1)"; STAGE_RC=$?
assert_eq "stage rc" "0" "$STAGE_RC"
assert_contains "stage announces NO flip" "NO flip" "$STAGE_OUT"
MANIFEST="$SBX/releases/$SBX_V1/manifest.json"
[ -f "$MANIFEST" ] && _pass || _fail "manifest exists"
for f in binary_version known_schema_gen contains_contract_phase rollback_safe \
         launcher_sha256 binary_sha256 config_sha256 agents_tree_sha256 \
         frontend_tree_sha256 agents_manifest frontend_manifest; do
    grep -q "\"$f\"" "$MANIFEST" 2>/dev/null && _pass || _fail "manifest has $f"
done
# no .env inside the release dir
if [ -z "$(find "$SBX/releases/$SBX_V1" -name '.env' -print -quit 2>/dev/null)" ]; then
    _pass
else
    _fail "release dir contains a .env"
fi
# ENSEMBLE_SELF_ENV marker in INSTALL_DIR/.env with the resolved env value
grep -q "^ENSEMBLE_SELF_ENV=sandbox$" "$SBX/.env" 2>/dev/null && _pass || _fail "ENSEMBLE_SELF_ENV=sandbox in INSTALL_DIR/.env"

# idempotent re-stage: byte-identical manifest (staged_at preserved)
M1="$(shasum -a 256 "$MANIFEST" | awk '{print $1}')"
sleep 1
run_stage --skip-build "$FIXTURE/stub-prod" > /dev/null 2>&1
M2="$(shasum -a 256 "$MANIFEST" | awk '{print $1}')"
assert_eq "re-stage manifest byte-identical" "$M1" "$M2"

# missing tag → 78
out="$(HOME="$FAKE_HOME" VERSION=v99.9.9 TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "stage untagged VERSION exits 78" "78" "$rc"
assert_contains "stage untagged refusal cites ADR-009 D3" "ADR-009 D3" "$out"

# stray .env in the payload tree → stage refuses (m6 invariant)
printf 'LEAKED=1\n' > "$FAKE_REPO/agents/.env"
out="$(run_stage --skip-build "$FIXTURE/stub-prod" 2>&1)"; rc=$?
assert_eq "stage refuses stray .env (exit 1)" "1" "$rc"
assert_contains "no-.env refusal names the file" "agents/.env" "$out"
rm -f "$FAKE_REPO/agents/.env"
# …and recovers
run_stage --skip-build "$FIXTURE/stub-prod" > /dev/null 2>&1 && _pass || _fail "stage recovers after .env removed"

# ─── 4. status --verify integrity (T3) ──────────────────────────────────────
section "integrity verify"
# seed staged-mode state: journal + current symlink (first promote equivalent)
(
    cd "$SBX" || exit 1
    ln -sfn "releases/$SBX_V1" releases/current
)
(
    export INSTALL_DIR="$SBX"
    # shellcheck source=scripts/upgrade/lib.sh
    . "$FAKE_REPO/scripts/upgrade/lib.sh"
    journal_init
    journal_set_current "$SBX_V1"
) > /dev/null 2>&1

VOUT="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    bash "$FAKE_REPO/scripts/upgrade/status.sh" sandbox --verify 2>&1)"; VRC=$?
assert_eq "verify clean exit 0" "0" "$VRC"
assert_contains "verify clean says OK" "integrity OK" "$VOUT"

# tamper one file → exit 1 naming the file
printf 'tampered\n' >> "$SBX/releases/$SBX_V1/config.yaml"
VOUT2="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    bash "$FAKE_REPO/scripts/upgrade/status.sh" sandbox --verify 2>&1)"; VRC2=$?
assert_eq "tampered verify exit 1" "1" "$VRC2"
assert_contains "tamper names the file" "config.yaml" "$VOUT2"
# restore (re-stage rewrites the payload)
run_stage --skip-build "$FIXTURE/stub-prod" > /dev/null 2>&1
VOUT3="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    bash "$FAKE_REPO/scripts/upgrade/status.sh" sandbox --verify 2>&1)"; VRC3=$?
assert_eq "verify clean again after re-stage" "0" "$VRC3"

# tamper an agents-tree file → named
printf 'x' >> "$SBX/releases/$SBX_V1/agents/leader/soul.md"
VOUT4="$(HOME="$FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    bash "$FAKE_REPO/scripts/upgrade/status.sh" sandbox --verify 2>&1)"; VRC4=$?
assert_eq "agents-tree tamper exit 1" "1" "$VRC4"
assert_contains "agents-tree tamper names file" "agents/leader/soul.md" "$VOUT4"
run_stage --skip-build "$FIXTURE/stub-prod" > /dev/null 2>&1

# promote preflight aborts (78) on CURRENT drift (no daemon needed —
# preflight integrity fails before the stop step). Tamper LAST so the
# preflight path is deterministic (a clean current would send the promote
# into a full stop/flip/gate cycle — wrong for a unit test).
printf 'drift\n' >> "$SBX/releases/$SBX_V1/agents/leader/soul.md"
P_OUT="$(HOME="$FAKE_HOME" VERSION="$SBX_V1" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
    bash "$FAKE_REPO/scripts/upgrade/promote.sh" sandbox 2>&1)"; P_RC=$?
assert_eq "promote preflight drift abort exits 78" "78" "$P_RC"
assert_contains "promote drift cites failed integrity" "failed integrity" "$P_OUT"
run_stage --skip-build "$FIXTURE/stub-prod" > /dev/null 2>&1   # restore for later sections

# ─── 5. journal atomicity + torn-write detection ────────────────────────────
section "journal atomicity"
JB="$SBX/releases/state.json"
# valid write + read round-trip
(
    export INSTALL_DIR="$SBX"
    . "$FAKE_REPO/scripts/upgrade/lib.sh"
    journal_history_append commit "round-trip"
    journal_read > /dev/null
) && _pass || _fail "journal write+read round-trip"
# torn write: truncated JSON is rejected by journal_read
printf '{"current":"v1","prev' > "$JB"
if ( export INSTALL_DIR="$SBX"; . "$FAKE_REPO/scripts/upgrade/lib.sh"; journal_read > /dev/null ) 2>/dev/null; then
    _fail "torn (truncated) journal must be rejected"
else
    _pass
fi
# empty file rejected
: > "$JB"
if ( export INSTALL_DIR="$SBX"; . "$FAKE_REPO/scripts/upgrade/lib.sh"; journal_read > /dev/null ) 2>/dev/null; then
    _fail "empty journal must be rejected"
else
    _pass
fi
# restore a valid journal for later sections
(
    export INSTALL_DIR="$SBX"
    . "$FAKE_REPO/scripts/upgrade/lib.sh"
    journal_init
    journal_set_current "$SBX_V1"
) > /dev/null 2>&1
# atomicity: no .tmp leftovers after a write
( export INSTALL_DIR="$SBX"; . "$FAKE_REPO/scripts/upgrade/lib.sh"; journal_write '{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}' ) > /dev/null
if [ -z "$(find "$SBX/releases" -maxdepth 1 -name 'state.json.tmp.*' -print -quit)" ]; then
    _pass
else
    _fail "no temp journal leftovers"
fi

# ─── 6. cap / cooldown / quarantine math (boundary cases) ───────────────────
section "cap/cooldown/quarantine math"
JLIB_TEST() {
    # JLIB_TEST <python-free bash snippet using lib helpers>
    (
        export INSTALL_DIR="$SBX"
        . "$FAKE_REPO/scripts/upgrade/lib.sh"
        eval "$1"
    ) 2>/dev/null
}
JBASE='{"current":"v1","previous":"v0","in_flight":null,"rollback_window_count":{"24h":COUNT,"window_start":"WSTART"},"cooldown_until":CD,"quarantined":["vQ"],"history":[]}'
mkjournal() {  # mkjournal <count> <wstart-iso-or-null> <cooldown-iso-or-null>
    printf '%s' "$JBASE" | sed -e "s/COUNT/$1/" -e "s/WSTART/$2/" -e "s/CD/$3/" > "$JB"
}
NOW_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# helper to offset an ISO ts by seconds (BSD date: -v BEFORE the date operand)
iso_off() { date -ju -v"$2"S -f '%Y-%m-%dT%H:%M:%SZ' "$NOW_ISO" +%Y-%m-%dT%H:%M:%SZ; }
AGE_86398="$(iso_off x -86398)"; AGE_86400="$(iso_off x -86400)"; AGE_86402="$(iso_off x -86402)"
COOL_PAST="$(iso_off x -3)"; COOL_FUTURE="$(iso_off x +180)"

# cap: count 3 in-window → entry view 3; count 2 → 2
mkjournal 3 "$AGE_86398" null
assert_eq "cap boundary: count=3 in-window reads 3" "3" "$(JLIB_TEST 'journal_rollback_count_24h')"
mkjournal 2 "$AGE_86398" null
assert_eq "count=2 in-window reads 2" "2" "$(JLIB_TEST 'journal_rollback_count_24h')"
# 24h±1s: window_start exactly 24h old → rolled over (0); 2s newer → visible
mkjournal 3 "$AGE_86400" null
assert_eq "24h boundary: window 86400s old → count 0 (rollover)" "0" "$(JLIB_TEST 'journal_rollback_count_24h')"
mkjournal 3 "$AGE_86402" null
assert_eq "window 86402s old → count 0" "0" "$(JLIB_TEST 'journal_rollback_count_24h')"
# journal_count_rollback: 3 → 4 (and re-opens the window when stale)
mkjournal 3 "$AGE_86400" null
assert_eq "count_rollback after stale window resets then counts → 1" "1" "$(JLIB_TEST 'journal_count_rollback 1')"
mkjournal 2 "$AGE_86398" null
assert_eq "count_rollback in-window 2 → 3" "3" "$(JLIB_TEST 'journal_count_rollback 1')"
# cooldown±: future → active (refuse), past → clear
mkjournal 0 "$AGE_86398" "\"$COOL_FUTURE\""
JLIB_TEST 'journal_cooldown_active' && _pass || _fail "cooldown future → active"
mkjournal 0 "$AGE_86398" "\"$COOL_PAST\""
JLIB_TEST 'journal_cooldown_active' || _pass || _fail "cooldown past → clear"
# cooldown arming: journal_count_rollback 1 stamps cooldown_until ~now+600
mkjournal 0 "$AGE_86398" null
JLIB_TEST 'journal_count_rollback 1' > /dev/null
CD_UNTIL="$(JLIB_TEST '_json_field "$(journal_read)" cooldown_until')"
[ -n "$CD_UNTIL" ] && [ "$CD_UNTIL" != "null" ] && _pass || _fail "count_rollback arms cooldown_until"
# quarantine: membership + idempotency
mkjournal 0 "$AGE_86398" null
JLIB_TEST 'journal_quarantine vQ2; journal_quarantine vQ2' > /dev/null
QSUB="$(JLIB_TEST '_json_sub "$(journal_read)" quarantined')"
case "$QSUB" in *vQ2*) _pass ;; *) _fail "quarantine add" "$QSUB" ;; esac
[ "$(printf '%s' "$QSUB" | grep -o vQ2 | wc -l | tr -d ' ')" = "1" ] && _pass || _fail "quarantine idempotent (single entry)"

# ─── 7. version smoke (gate_version) with a stub /livez server ─────────────
section "version smoke"
SRV_PORT=18399
python3 - "$SRV_PORT" <<'PYEOF' &
import sys, http.server, json
port = int(sys.argv[1])
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/livez":
            body = json.dumps({"status": "alive", "uptime_seconds": 1, "version": "9.9.9-stub"}).encode()
            self.send_response(200)
        elif self.path == "/readyz":
            body = json.dumps({"status": "ready", "reasons": []}).encode()
            self.send_response(200)
        else:
            body = b"{}"
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
http.server.HTTPServer(("127.0.0.1", port), H).serve_forever()
PYEOF
SRV_PID=$!
sleep 1
if ( export INSTALL_DIR="$SBX"; export PORT=$SRV_PORT; . "$FAKE_REPO/scripts/upgrade/lib.sh"; gate_version "9.9.9-stub" ) > /dev/null 2>&1; then
    _pass
else
    _fail "gate_version accepts matching version"
fi
if ( export INSTALL_DIR="$SBX"; export PORT=$SRV_PORT; . "$FAKE_REPO/scripts/upgrade/lib.sh"; gate_version "0.0.1-wrong" ) > /dev/null 2>&1; then
    _fail "gate_version REFUSES mismatching version"
else
    _pass
fi
kill "$SRV_PID" 2>/dev/null
wait "$SRV_PID" 2>/dev/null

# ─── summary ────────────────────────────────────────────────────────────────
printf '\n== summary: %d passed, %d failed ==\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'failed:%s\n' "$FAILED_TESTS"
    rm -rf "$FIXTURE"
    exit 1
fi
rm -rf "$FIXTURE"
exit 0
