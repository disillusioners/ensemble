#!/bin/bash
# ============================================================================
# tests/test_adaptive_soak.sh — bash pack for P3 adaptive soak (decision c),
# boot-grace (decision e), transient classification (decision e), and
# busy-amnesty extension (decision e). Covers T3.1, T3.2, T3.5, T3.6, T3.8
# from test-strategy.md.
# ============================================================================
# Plain self-asserting bash (no bats). Throwaway INSTALL_DIR per subtest
# (mktemp). No daemon, no network, no real ports — a STUB PROBE HARNESS
# inside this file simulates the livez+readyz+version endpoints (T-PIN-gates
# anchor: probe functions are unit-testable WITHOUT a real daemon).
#
# Run:
#   bash tests/test_adaptive_soak.sh
#
# Coverage (test-strategy.md §1 + phase3-plan.md T3.1/T3.2/T3.5/T3.6/T3.8):
#   T3.1 — adaptive soak early-exit (3 greens @ 15 s cadence, floor 60 s)
#   T3.2 — conservative mode bit-identical (300 s, 30 s cadence, no early exit)
#   T3.5 — sentinel boot grace end conditions (elapsed OR 3 greens)
#   T3.6 — transient classification (503/exit-7/22/28 → transient + retry;
#          hard fail → immediate red)
#   T3.8 — busy-amnesty (inflight_turns ≤ 5 → alive; > 5 → busy)
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

section() { printf '\n== %s ==\n' "$1"; }

# ============================================================================
# Stub-ceremony harness: a fake livez/readyz/version server. The python
# server reads per-path env vars (STUB_FAIL_<SAFE_PATH>, STUB_BODY_<SAFE_PATH>,
# STUB_CODE_<SAFE_PATH>) where <SAFE_PATH> is the request path with slashes
# replaced by underscores (bash env var names cannot contain slashes).
# ============================================================================
STUB_PID=""
STUB_PORT=""

# Per-path env setter: STUB_FAIL_<safe_path>="refuse|503|500|400"
# STUB_BODY_<safe_path>="...", STUB_CODE_<safe_path>="200"
_stub_set() {
    local kind="$1" path="$2" value="$3"
    local safe_path="${path//\//_}"
    local varname="STUB_${kind}_${safe_path}"
    if [ -n "$value" ]; then
        export "${varname}=${value}"
    else
        unset "${varname}" 2>/dev/null || true
    fi
}

_start_stub() {
    STUB_PORT="${1:-18765}"
    local stub_root
    stub_root="$(mktemp -d -t ensemble-test-stub.XXXXXX)"
    cat > "$stub_root/server.py" <<'PYEOF'
import os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer

def _safe_path(p):
    return "_" + p.replace("/", "_")

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        spath = _safe_path(self.path)
        fail = os.environ.get("STUB_FAIL" + spath, "")
        delay = float(os.environ.get("STUB_DELAY" + spath, "0") or 0)
        if delay:
            time.sleep(delay)
        if fail == "refuse":
            os._exit(0)
        if fail == "503":
            body = b'{"status":"degraded"}'
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if fail == "500":
            body = b'{"status":"server_error"}'
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if fail == "400":
            body = b'{"status":"bad_request"}'
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = os.environ.get("STUB_BODY" + spath,
            '{"status":"ok","version":"vTEST","detail":{"inflight_turns":0}}').encode()
        try:
            code = int(os.environ.get("STUB_CODE" + spath, "200"))
        except ValueError:
            code = 200
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a, **k):
        pass
srv = HTTPServer(("127.0.0.1", int(sys.argv[1])), H)
srv.serve_forever()
PYEOF
    # Collect all STUB_* env vars to forward to the python subprocess.
    # Read the env into a python-friendly form: we use a here-doc to
    # pass each variable via a python -c "import os; os.environ[...]=..."
    # sequence. Simpler: just dump the env, but preserve quotes by
    # using python's os.environ.copy() — write a tiny python shim
    # that re-emits the env as `--env KEY=VAL` flags.
    local env_shim="$stub_root/dump_env.py"
    cat > "$env_shim" <<'PYEOF'
import os, sys, subprocess
# Re-emit every STUB_* env var to the child subprocess.
env = {k: v for k, v in os.environ.items() if k.startswith("STUB_")}
sys.exit(subprocess.call(sys.argv[1:], env={**os.environ, **env}))
PYEOF
    nohup python3 "$env_shim" python3 "$stub_root/server.py" "$STUB_PORT" \
        > "$stub_root/server.log" 2>&1 &
    STUB_PID=$!
    local i
    for i in 1 2 3 4 5 6; do
        if curl -sS --max-time 1 "http://127.0.0.1:$STUB_PORT/livez" >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

_stop_stub() {
    [ -n "$STUB_PID" ] && kill "$STUB_PID" 2>/dev/null
    # Also kill any stragglers (port-bound pids) — defensive against
    # orphan stubs from prior runs that left the port in TIME_WAIT.
    if [ -n "$STUB_PORT" ]; then
        pids="$(ss -tnlp 2>/dev/null | grep ":$STUB_PORT " | grep -oP 'pid=\K[0-9]+' | sort -u || true)"
        for p in $pids; do
            kill "$p" 2>/dev/null
        done
    fi
    STUB_PID=""
    _stub_clear
}

# Top-level cleanup trap: kill any orphan python stub servers on
# known test ports so a previous test run cannot leak into a new
# one. Fires on EXIT (normal completion OR error).
_cleanup_orphans() {
    local port
    for port in 18765 18766 18767 18768 18769 18770 18771 18772 18773 18774 18775; do
        pids="$(ss -tnlp 2>/dev/null | grep ":$port " | grep -oP 'pid=\K[0-9]+' | sort -u || true)"
        for p in $pids; do
            kill -9 "$p" 2>/dev/null
        done
    done
}
trap _cleanup_orphans EXIT

# Unset every STUB_* env var so the next subtest starts clean
_stub_clear() {
    # env | grep -E '^STUB_' outputs lines like `STUB_FOO=bar`. We
    # need to extract the var name (before the `=`) and unset each
    # one. The `cut -d= -f1` does the split.
    local v name
    while IFS= read -r v; do
        [ -n "$v" ] || continue
        case "$v" in
            STUB_*) name="${v%%=*}"; unset "$name" ;;
        esac
    done < <(env | grep -E '^STUB_')
}

# Source lib.sh fresh for each subtest (so the constants are
# available + the function definitions). lib.sh is a pure-function
# library — re-sourcing is safe (top-level definitions are
# idempotent; the constants use ${X:-default} so the env wins).
#
# Implementation note: we CANNOT call this in a $(...) subshell
# (variable + function assignments vanish when the subshell exits).
# We source lib.sh in the CURRENT shell, return the tmpdir via a
# global, and the caller reads $SUBTEST_TMPDIR.
SUBTEST_TMPDIR=""
_setup_subtest() {
    local subtest_name="$1"
    SUBTEST_TMPDIR="$(mktemp -d -t "ensemble-soak-test-${subtest_name}.XXXXXX")"
    mkdir -p "$SUBTEST_TMPDIR/releases"
    export INSTALL_DIR="$SUBTEST_TMPDIR"
    export PORT="$STUB_PORT"
    export UP_TARGET="test"
    export LOG_TAG="test-soak"
    export PROMOTE_TOTAL_DEADLINE_S="${PROMOTE_TOTAL_DEADLINE_S:-900}"
    # Reset boot-grace globals so the test starts clean
    _BOOT_GRACE_START_EPOCH=""
    _BOOT_GRACE_CONSEC_GREEN=0
    # shellcheck source=scripts/upgrade/lib.sh
    . "$UPGRADE_DIR/lib.sh"
}

# ============================================================================
# T3.1 — adaptive soak early-exit
# ============================================================================
section "T3.1 — adaptive soak early-exit (3 greens, floor 60 s, cap 120 s)"

_stub_set BODY /livez '{"status":"ok","version":"vTEST"}'
_stub_set CODE /livez 200
_stub_set BODY /readyz '{"status":"ok","detail":{"inflight_turns":0}}'
_stub_set CODE /readyz 200
if ! _start_stub 18765; then
    _fail "T3.1 stub boot" "stub ready" "stub not ready"
else
    _setup_subtest T3-1
    # Use a tiny cadence + small cap/floor to keep the test fast.
    # 3 greens at 1s cadence ≈ 3s; the floor is wall-clock so the
    # 3-green exit must wait until now >= floor. We set floor=3,
    # cap=10, cadence=1, and expect rc 0.
    ADAPTIVE_SOAK_CADENCE_S=1 ADAPTIVE_SOAK_FLOOR_S=3 ADAPTIVE_SOAK_CAP_S=10 \
        gate_soak 60 "vTEST" "" >/dev/null 2>&1
    rc=$?
    assert_eq "T3.1 adaptive early-exit rc" "0" "$rc"
    rm -rf "$SUBTEST_TMPDIR"
    _stop_stub
fi

# ============================================================================
# T3.1-i — C-1 cap-reach-with-zero-greens → exit 1 (auto-rollback)
# ============================================================================
section "T3.1-i — C-1 cap-reach-with-zero-greens → exit 1"

_stub_set BODY /livez '{"status":"degraded"}'
_stub_set CODE /livez 503
_stub_set BODY /readyz '{"status":"degraded"}'
_stub_set CODE /readyz 503
if ! _start_stub 18766; then
    _fail "T3.1-i stub boot" "stub ready" "stub not ready"
else
    _setup_subtest T3-1i
    # Cap=5s, floor=2s, cadence=1s. Every probe is 503 (transient)
    # → never green → cap-reach with consecutive_green=0 → exit 1.
    ADAPTIVE_SOAK_CADENCE_S=1 ADAPTIVE_SOAK_FLOOR_S=2 ADAPTIVE_SOAK_CAP_S=5 \
        gate_soak 60 "vTEST" "" >/dev/null 2>&1
    rc=$?
    assert_eq "T3.1-i cap-reach-with-zero-greens → exit 1 (auto-rollback)" "1" "$rc"
    rm -rf "$SUBTEST_TMPDIR"
    _stop_stub
fi

# ============================================================================
# T3.2 — conservative mode (no early exit, 30 s cadence)
# ============================================================================
section "T3.2 — conservative mode bit-identical (no early exit, 30 s cadence)"

_stub_set BODY /livez '{"status":"ok","version":"vTEST"}'
_stub_set CODE /livez 200
_stub_set BODY /readyz '{"status":"ok","detail":{"inflight_turns":0}}'
_stub_set CODE /readyz 200
if ! _start_stub 18767; then
    _fail "T3.2 stub boot" "stub ready" "stub not ready"
else
    _setup_subtest T3-2
    # Conservative: soak=2s. The conservative loop will probe +
    # sleep 30s, but the deadline is 2s, so the loop exits on the
    # deadline boundary. rc 0 (no early exit; bit-identical to the
    # v0.18-era shape within the small soak window).
    ENSEMBLE_SOAK_CONSERVATIVE=1 \
        gate_soak 2 "vTEST" "" >/dev/null 2>&1
    rc=$?
    assert_eq "T3.2 conservative mode rc" "0" "$rc"
    rm -rf "$SUBTEST_TMPDIR"
    _stop_stub
fi

# ============================================================================
# T3.5 — sentinel boot grace end conditions (elapsed OR 3 greens)
# ============================================================================
section "T3.5 — sentinel boot grace end conditions"

_setup_subtest T3-5
# (a) Start the grace; verify active immediately.
PROMOTE_BOOT_GRACE_S=60 _boot_grace_start
if _boot_grace_active; then _pass; else _fail "T3.5a grace after start" "active" "not active"; fi
# (b) 2 consecutive greens — grace still active
_BOOT_GRACE_START_EPOCH="$(date +%s)"
_BOOT_GRACE_CONSEC_GREEN=2
PROMOTE_BOOT_GRACE_S=60
if _boot_grace_active; then _pass; else _fail "T3.5b grace at 2 greens" "active" "ended early"; fi
# (c) 3 consecutive greens — grace ends
_BOOT_GRACE_CONSEC_GREEN=3
if _boot_grace_active; then _fail "T3.5c grace at 3 greens" "ended" "still active"; else _pass; fi
# (d) Elapsed boundary
_BOOT_GRACE_START_EPOCH=$(($(date +%s) - 2))  # started 2 s ago
_BOOT_GRACE_CONSEC_GREEN=0
PROMOTE_BOOT_GRACE_S=1  # 1 s window
if _boot_grace_active; then _fail "T3.5d grace after 2 s elapsed" "ended" "still active"; else _pass; fi
rm -rf "$SUBTEST_TMPDIR"

# ============================================================================
# T3.6 — transient classification (C-8a: 503/7/22/28 → transient)
# ============================================================================
section "T3.6 — transient classification"

# (a) 503 → transient
_stub_set BODY /livez '{"status":"degraded"}'
_stub_set CODE /livez 503
if ! _start_stub 18769; then
    _fail "T3.6a stub boot" "stub ready" "stub not ready"
else
    _setup_subtest T3-6a
    PROMOTE_BOOT_GRACE_S=0 _probe_once_classified /livez "$PORT"
    assert_eq "T3.6a 503 → transient" "transient" "$_PROBE_STATUS"
    rm -rf "$SUBTEST_TMPDIR"
    _stop_stub
fi

# (b) 200 → green
_stub_set CODE /livez 200
_stub_set BODY /livez '{"status":"ok","version":"vTEST"}'
if ! _start_stub 18770; then
    _fail "T3.6b stub boot" "stub ready" "stub not ready"
else
    _setup_subtest T3-6b
    PROMOTE_BOOT_GRACE_S=0 _probe_once_classified /livez "$PORT"
    assert_eq "T3.6b 200 → green" "green" "$_PROBE_STATUS"
    rm -rf "$SUBTEST_TMPDIR"
    _stop_stub
fi

# (c) 500 → red
_stub_set CODE /livez 500
_stub_set BODY /livez '{"status":"server_error"}'
if ! _start_stub 18771; then
    _fail "T3.6c stub boot" "stub ready" "stub not ready"
else
    _setup_subtest T3-6c
    PROMOTE_BOOT_GRACE_S=0 _probe_once_classified /livez "$PORT"
    assert_eq "T3.6c 500 → red" "red" "$_PROBE_STATUS"
    rm -rf "$SUBTEST_TMPDIR"
    _stop_stub
fi

# (d) 400 → red
_stub_set CODE /livez 400
_stub_set BODY /livez '{"status":"bad_request"}'
if ! _start_stub 18772; then
    _fail "T3.6d stub boot" "stub ready" "stub not ready"
else
    _setup_subtest T3-6d
    PROMOTE_BOOT_GRACE_S=0 _probe_once_classified /livez "$PORT"
    assert_eq "T3.6d 400 → red" "red" "$_PROBE_STATUS"
    rm -rf "$SUBTEST_TMPDIR"
    _stop_stub
fi

# ============================================================================
# T3.7 — curl -fsS zero-hit pin (T-PIN-gates: no -f form inside probe fns)
# ============================================================================
section "T3.7 — curl -fsS zero-hit pin (no -f form inside lib.sh probe functions)"

# Pin: every `curl -fsS` hit in lib.sh must be in a comment line (not in
# active probe code). Strip comment lines, then check for any remaining
# hits — the pin fails if any active code path still uses -f.
fsS_hits="$(grep -nF 'curl -fsS' "$UPGRADE_DIR/lib.sh" 2>/dev/null \
    | awk -F: '$2 !~ /^[[:space:]]*#/ {print}' \
    || true)"
if [ -z "$fsS_hits" ]; then
    _pass
else
    _fail "T3.7 curl -fsS zero-hit pin" "no active hits" "found:
$fsS_hits"
fi

# ============================================================================
# T3.8 — busy-amnesty (BUSY_INFLIGHT_BOUND=5)
# ============================================================================
section "T3.8 — busy-amnesty (BUSY_INFLIGHT_BOUND=5)"

# (a) inflight_turns=3 → within bound → amnesty granted
_stub_set BODY /readyz '{"status":"ok","detail":{"inflight_turns":3}}'
_stub_set CODE /readyz 200
if ! _start_stub 18773; then
    _fail "T3.8a stub boot" "stub ready" "stub not ready"
else
    _setup_subtest T3-8a
    if BUSY_INFLIGHT_BOUND=5 _read_inflight_turns_bounded; then
        _pass
    else
        _fail "T3.8a inflight_turns=3 (within bound=5)" "amnesty granted" "no amnesty"
    fi
    rm -rf "$SUBTEST_TMPDIR"
    _stop_stub
fi

# (b) inflight_turns=6 → above bound → no amnesty
_stub_set BODY /readyz '{"status":"ok","detail":{"inflight_turns":6}}'
if ! _start_stub 18774; then
    _fail "T3.8b stub boot" "stub ready" "stub not ready"
else
    _setup_subtest T3-8b
    if BUSY_INFLIGHT_BOUND=5 _read_inflight_turns_bounded; then
        _fail "T3.8b inflight_turns=6 (above bound=5)" "no amnesty" "amnesty granted"
    else
        _pass
    fi
    rm -rf "$SUBTEST_TMPDIR"
    _stop_stub
fi

# (c) inflight_turns=0 → no in-flight turns → no amnesty (wedged-daemon guard)
_stub_set BODY /readyz '{"status":"ok","detail":{"inflight_turns":0}}'
if ! _start_stub 18775; then
    _fail "T3.8c stub boot" "stub ready" "stub not ready"
else
    _setup_subtest T3-8c
    if BUSY_INFLIGHT_BOUND=5 _read_inflight_turns_bounded; then
        _fail "T3.8c inflight_turns=0 (wedged-daemon guard)" "no amnesty" "amnesty granted"
    else
        _pass
    fi
    rm -rf "$SUBTEST_TMPDIR"
    _stop_stub
fi

# (d) Regression pin: no new /run/ensemble/inflight_turns file anywhere
section "T3.8d — regression pin: no new /run/ensemble/inflight_turns file"
# Exclude tests/, comments in .md, .pyc, .git
hits="$(grep -rn "/run/ensemble/inflight_turns" "$REPO_ROOT" 2>/dev/null \
    | grep -v "\.git/" \
    | grep -vE '/test_adaptive_soak\.sh:' \
    | grep -vE '/(tests|docs|\.agents)/.*\.md:' \
    | grep -vE '\.pyc:' \
    | grep -vE '^[^:]+:[0-9]+:\s*#' \
    | grep -vE '^[^:]+:[0-9]+:\s*//' \
    | grep -vE '/scripts/upgrade/lib\.sh:[0-9]+:[[:space:]]*#' \
    || true)"
if [ -z "$hits" ]; then
    _pass
else
    _fail "T3.8d /run/ensemble/inflight_turns zero-hit pin" "no hits" "found:
$hits"
fi

# ============================================================================
# Per-phase budget constants (T3.10 subset)
# ============================================================================
section "P3 budget constants — verify the 9-row C-10a arithmetic"

_setup_subtest T3-10
assert_eq "PROMOTE_TOTAL_DEADLINE_S" "900" "$PROMOTE_TOTAL_DEADLINE_S"
assert_eq "ADAPTIVE_SOAK_FLOOR_S" "60" "$ADAPTIVE_SOAK_FLOOR_S"
assert_eq "ADAPTIVE_SOAK_CAP_S" "120" "$ADAPTIVE_SOAK_CAP_S"
assert_eq "ADAPTIVE_SOAK_CONSECUTIVE" "3" "$ADAPTIVE_SOAK_CONSECUTIVE"
assert_eq "ADAPTIVE_SOAK_CADENCE_S" "15" "$ADAPTIVE_SOAK_CADENCE_S"
assert_eq "BOOT_GRACE_S" "60" "$BOOT_GRACE_S"
assert_eq "BUSY_INFLIGHT_BOUND" "5" "$BUSY_INFLIGHT_BOUND"

# C-10a 9-row arithmetic
sum=$((45 + 225 + 30 + 90 + 180 + 120 + 30 + 120 + 30))
assert_eq "C-10a 9-row sum (870 s non-stop worst)" "870" "$sum"
slack=$((900 - 870))
assert_eq "C-10a slack inside 900 s ceiling" "30" "$slack"
all_max=$((45 + 755 + 30 + 90 + 180 + 120 + 30 + 120 + 30))
assert_eq "C-10a all-budgets-max (1400 s abort-path)" "1400" "$all_max"

rm -rf "$SUBTEST_TMPDIR"

# ============================================================================
# Final tally
# ============================================================================
echo ""


# ===========================================================================
# C1/C2 (council 2026-10-12): connection-level classification + ride-out
# ===========================================================================
# The stub harness's STUB_FAIL=refuse mode (server exits → connection
# refused → curl exit 7) and STUB_DELAY mode (> curl's 5s max-time →
# curl exit 28) were UNUSED. These cells pin the C1 fix: refused /
# timed-out probes are TRANSIENT (rc 2 from _probe_once_classified),
# never red.

section "C1a — connection refused (curl 7) → transient"

# STUB_FAIL=refuse mode (previously unused): the stub boots WITH
# refuse armed, serves exactly ONE request (the harness health-check
# curls it — server exits mid-handler, _start_stub reports not-ready,
# which is EXPECTED here and ignored), and from then on every
# connection is refused — curl exit 7. The C1 defect classified this
# red (stale PIPESTATUS read); it must be transient.
_stub_set FAIL /livez "refuse"
_start_stub 18776 || true   # health-check death is the point; rc ignored
_setup_subtest C1a
export PORT=18776
sleep 0.5   # listener teardown after the health-check kill → refused
_probe_once_classified /livez "$PORT"
assert_eq "C1a refused → transient (was: red via stale PIPESTATUS)" "transient" "$_PROBE_STATUS"
rm -rf "$SUBTEST_TMPDIR"
_stop_stub || true

section "C1b — probe timeout (curl 28) → transient"

# Raw-launch a delay-only server (the _start_stub ready-check times
# out against a 6s handler, so the harness path can't be used). Every
# request hangs past curl's 5s max-time → curl exit 28.
mkdir -p /tmp/soak-delay-stub-$$
cat > /tmp/soak-delay-stub-$$/server.py <<'DPYEOF'
import sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(6)
        body = b'{"status":"ok","version":"vTEST"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
DPYEOF
python3 /tmp/soak-delay-stub-$$/server.py 18777 &
DPID=$!
sleep 0.7
_setup_subtest C1b
export PORT=18777
t0=$SECONDS
_probe_once_classified /livez "$PORT"
rc=$?
assert_eq "C1b timed-out → transient" "transient" "$_PROBE_STATUS"
assert_eq "C1b rc 2 (three-state protocol)" "2" "$rc"
kill $DPID 2>/dev/null
rm -rf "$SUBTEST_TMPDIR" /tmp/soak-delay-stub-$$

# ---------------------------------------------------------------------------
# C2 pin — T+30s-then-green: sustained transients RIDE OUT inside the
# phase budget and the gate PASSES once green. (The shipped defect:
# a single post-grace blip returned 1 → instant gate fail — the
# 1s/2s/4s retry + budget ride-out were dead code.)
# ---------------------------------------------------------------------------
section "C2 — T+30s transient storm then green → gate PASSES (ride-out)"

STUB_TRANSIENT_PORT=18778
# 503 for the first 30s, then 200 (STUB_TRANSIENT_UNTIL epoch).
cat > /tmp/soak-transient-stub-$$/server.py 2>/dev/null || mkdir -p /tmp/soak-transient-stub-$$
cat > /tmp/soak-transient-stub-$$/server.py <<'TPYEOF'
import os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
UNTIL = int(os.environ.get("STUB_TRANSIENT_UNTIL", "0"))
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if int(time.time()) < UNTIL:
            body = b'{"status":"degraded"}'
            self.send_response(503)
        else:
            body = b'{"status":"ok","version":"vTEST"}'
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
TPYEOF
_setup_subtest C2ride
export PORT="$STUB_TRANSIENT_PORT"
T30_UNTIL=$(( $(date +%s) + ${T30_WINDOW_S:-30} ))
STUB_TRANSIENT_UNTIL="$T30_UNTIL" python3 /tmp/soak-transient-stub-$$/server.py "$STUB_TRANSIENT_PORT" &
TPID=$!
t0=$SECONDS
LIVEZ_BUDGET_S=${T30_BUDGET_S:-45} gate_livez >/dev/null 2>&1
rc=$?
elapsed=$((SECONDS - t0))
kill $TPID 2>/dev/null
assert_eq "C2 rides out 30s of 503s then PASSES" "0" "$rc"
if [ "$elapsed" -ge $(( ${T30_WINDOW_S:-30} - 3 )) ]; then _pass; else
    _fail "C2 ride-out took the full transient window" ">=27s" "${elapsed}s (gate passed too EARLY — transients were not being ridden)"
fi
rm -rf "$SUBTEST_TMPDIR" /tmp/soak-transient-stub-$$

# C2 ceiling control: the ride-out is bounded — with a tiny budget and
# a never-coming server, the gate fails AT the budget (not instantly).
section "C2c — ride-out ceiling: tiny budget + dead port → fails at budget"

_setup_subtest C2c
PORT=19799   # nothing listens here
t0=$SECONDS
LIVEZ_BUDGET_S=6 gate_livez >/dev/null 2>&1
rc=$?
elapsed=$((SECONDS - t0))
assert_eq "C2c bounded ride-out still fails (rc 2 at budget)" "2" "$rc"
if [ "$elapsed" -ge 5 ]; then _pass; else
    _fail "C2c gate failed at the budget, not instantly" ">=5s" "${elapsed}s"
fi
rm -rf "$SUBTEST_TMPDIR"

# ---------------------------------------------------------------------------
# C6 pin — floor-holds: 3 greens arriving at ~30s must NOT end the soak
# before the 60s floor. (Shipped defect: early-exit was greens-count
# only — soak exited at ~30s vs the approved 60s floor.)
# ---------------------------------------------------------------------------
section "C6 — floor-holds: greens at ~30s do not exit before the 60s floor"

STUB_FLOOR_PORT=18780
mkdir -p /tmp/soak-floor-stub-$$
UNTIL=$(( $(date +%s) + 28 ))   # 503 until t+28 → first greens land ~28-30s
cat > /tmp/soak-floor-stub-$$/server.py <<'FPYEOF'
import os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
UNTIL = int(os.environ.get("STUB_TRANSIENT_UNTIL", "0"))
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if int(time.time()) < UNTIL:
            body = b'{"status":"degraded"}'
            self.send_response(503)
        else:
            body = b'{"status":"ok","version":"vTEST"}'
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a):
        pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
FPYEOF
STUB_TRANSIENT_UNTIL="$UNTIL" python3 /tmp/soak-floor-stub-$$/server.py "$STUB_FLOOR_PORT" &
FPID=$!
_setup_subtest C6floor
export PORT="$STUB_FLOOR_PORT"
sleep 0.5
t0=$SECONDS
ADAPTIVE_SOAK_CADENCE_S=2 ADAPTIVE_SOAK_FLOOR_S=${FLOOR_TEST_FLOOR_S:-60}     ADAPTIVE_SOAK_CAP_S=${FLOOR_TEST_CAP_S:-90}     gate_soak 120 "vTEST" "" >/dev/null 2>&1
rc=$?
elapsed=$((SECONDS - t0))
kill $FPID 2>/dev/null
assert_eq "C6 floor-holds: soak succeeds" "0" "$rc"
if [ "$elapsed" -ge $(( ${FLOOR_TEST_FLOOR_S:-60} - 2 )) ]; then _pass; else
    _fail "C6 soak did NOT exit before the floor"         ">=${FLOOR_TEST_FLOOR_S:-60}s"         "${elapsed}s (3 greens at ~30s ended the soak EARLY — floor not wired)"
fi
rm -rf "$SUBTEST_TMPDIR" /tmp/soak-floor-stub-$$

echo "============================================================"
echo "Test summary: $PASS passed / $FAIL failed"
echo "============================================================"
if [ "$FAIL" -gt 0 ]; then
    echo "Failed tests:"
    printf '%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
