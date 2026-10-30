#!/usr/bin/env bash
# tests/packs/preflight_real_soak_test.sh — SC5's REAL soak leg
# (promote-preflight-hang phase 2, fold A8/D2-11).
#
# The negative control upgraded by the architect: a REAL 300 s
# conservative-mode soak — the REAL gate_soak ran with REAL sleeps and
# REAL probes against a REAL local HTTP responder — must trip NOTHING
# (0 breach rows, 0 kills) with the preflight budgets in force in the same
# shell. A simulated soak-length phase was explicitly rejected.
#
# This is the LONG pack (~5-6 min): run it BACKGROUNDED and poll:
#   nohup bash tests/packs/preflight_real_soak_test.sh > /tmp/soak.log 2>&1 &
#
# It also pins the structural exclusion (the watchdog is armed around the
# preflight window only; gate_soak is outside), so a future re-arming
# regression reds BOTH this pack and Pack D's D6 structural leg.
#
# Usage: bash tests/packs/preflight_real_soak_test.sh
# Exit codes: 0=PASS, 1=FAIL, 2=FATAL.

set -u
PF_PACK_NAME="preflight-real-soak"
. "$(cd "$(dirname "$0")/.." && pwd)/helpers/preflight_pack_common.sh"

pf_fence_init
trap pf_fence_cleanup EXIT

J="$INSTALL_DIR/releases/state.json"
printf '%s' '{"current":null,"previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}' > "$J"

SOAK_PORT=8466
BIN_VERSION="9.9.9"

# ── the REAL responder: a local HTTP server serving /livez + /readyz ───────
cat > "$PF_SB/responder.py" <<'PY'
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1])
VER = sys.argv[2]

class H(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/livez":
            body = {"status": "alive", "version": VER,
                    "uptime_seconds": 1.0}
            self.send_response(200)
        elif self.path == "/readyz":
            body = {"status": "ready",
                    "components": {"database": True, "queue_freshness": True,
                                   "services": True, "checkpoint_saver": True},
                    "detail": {"reasons": [], "inflight_turns": 0}}
            self.send_response(200)
        else:
            body = {"error": "not found"}
            self.send_response(404)
        raw = json.dumps(body).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):  # silence
        pass

HTTPServer(("127.0.0.1", PORT), H).serve_forever()
PY

/usr/bin/python3 "$PF_SB/responder.py" "$SOAK_PORT" "$BIN_VERSION" &
RESP_PID=$!
trap 'kill -KILL "$RESP_PID" 2>/dev/null; pf_fence_cleanup' EXIT
READY=0
for _ in $(seq 1 50); do
    _x="$(curl -s -m 2 "http://127.0.0.1:$SOAK_PORT/livez" 2>/dev/null)" || _x=""
    if printf '%s' "$_x" | grep -q '"alive"'; then READY=1; break; fi
    sleep 0.1
done
[ "$READY" = "1" ] && pf_ok "responder up on :$SOAK_PORT (real HTTP /livez + /readyz)" \
                   || pf_bad "responder did not start"

# ── the armed harness: budgets in force, lock held, REAL gate_soak ─────────
cat > "$PF_SB/soak.sh" <<'EOS'
#!/usr/bin/env bash
set -u
. "$REPO_ROOT/scripts/upgrade/lib.sh"
LOG_TAG=pf-soak
INSTALL_DIR="$SOAK_INSTALL"; export INSTALL_DIR
UP_TARGET=sandbox
PORT="$SOAK_PORT"; export PORT
# The budgets the watchdog would use if it were (wrongly) armed here:
PREFLIGHT_STEP_BUDGET_S=120
PREFLIGHT_PHASE_BUDGET_S=300
_trap_install_signal_handlers "promote:preflight"
lock_acquire || { echo "LOCK-FAIL"; exit 78; }
lock_heartbeat
echo "T0 $(date -u +%H:%M:%S) soak start"
ENSEMBLE_SOAK_CONSERVATIVE=1 gate_soak 300 "$SOAK_VER" ""
soak_rc=$?
echo "T1 $(date -u +%H:%M:%S) soak rc=$soak_rc"
lock_release
exit "$soak_rc"
EOS
chmod +x "$PF_SB/soak.sh"

echo "== REAL 300 s conservative soak (started $(date -u +%Y-%m-%dT%H:%M:%SZ)) =="
REPO_ROOT="$PF_REPO_ROOT" SOAK_INSTALL="$INSTALL_DIR" SOAK_PORT="$SOAK_PORT" \
    SOAK_VER="$BIN_VERSION" bash "$PF_SB/soak.sh" > "$PF_SB/soak.out" 2> "$PF_SB/soak.err" &
SOAK_SHELL=$!
# poll across the run (the dispatch's background/poll discipline)
while kill -0 "$SOAK_SHELL" 2>/dev/null; do
    sleep 30
    _pc="$(grep -c 'soak' "$PF_SB/soak.out" "$PF_SB/soak.err" 2>/dev/null | awk -F: '{s+=$2} END {print s+0}')"
    echo "  · $(date -u +%H:%M:%S) soak in flight (${_pc:-0} narrated soak lines)"
done
wait "$SOAK_SHELL"; SOAK_RC=$?

grep -E "^T[01] " "$PF_SB/soak.out" | sed 's/^/  · /'
[ "$SOAK_RC" = "0" ] && pf_ok "REAL conservative soak (300 s) completed rc=0" \
                     || pf_bad "soak rc=$SOAK_RC (want 0) [err: $(tail -2 "$PF_SB/soak.err" | tr '\n' ' ')]"

HALT_N="$(grep -o '"event":"halt"' "$J" 2>/dev/null | wc -l | tr -d ' ')"
BREACH_N="$(grep -o 'deadline-breach-' "$J" 2>/dev/null | wc -l | tr -d ' ')"
[ "$HALT_N" = "0" ] && pf_ok "0 halt rows during the real soak (0 kills)" \
                    || pf_bad "$HALT_N halt rows during the real soak"
[ "$BREACH_N" = "0" ] && pf_ok "0 deadline-breach rows (the watchdog never armed on the soak)" \
                      || pf_bad "$BREACH_N breach rows"
[ ! -d "$INSTALL_DIR/releases/rollback.lock.d" ] && pf_ok "lock released after the soak" \
                                                 || pf_bad "lock still held after the soak"
PROBES="$(grep -c 'soak' "$PF_SB/soak.err" 2>/dev/null || true)"
[ "${PROBES:-0}" -ge 2 ] && pf_note "soak narrated ${PROBES} lines (real loop, not a sim)" || true

pf_summary
