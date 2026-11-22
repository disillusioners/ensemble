#!/usr/bin/env bash
# =============================================================================
# Test Pack: engine_boot_slo_gate  (P4 T4.7)  — F2 gate ownership
# =============================================================================
# LEG 1 (inherited, 21 nodes — count re-verified 2026-11-22): method-wiring
#        unit pins; the ONLY change since base 8a1045c02 is F1's posture-line
#        expectation in test_wiring.py (ad0175229 — the static "USE_ENGINE all
#        unset" claim became the dynamic use_engine_flags= field).
# LEG 2 (F2): doc-equality constant pin — this pack's bound constants MUST match
#        boot-slo-measurement.md §9.7 "gate constant mirror" (RED on divergence).
# LEG 3 (F2): WARM-class fenced boot measurement — ONE boot; asserts
#        block_elapsed_ms_gated <= SLO1_WARM_BOUND_MS plus posture fields.
# LEG 4 (F2): COLD-class bound — constant + provenance ONLY. DISCLOSURE:
#        fresh-PG-cluster cold boots are a TESTER-LANE procedure, not
#        pack-runnable; this pack encodes/pins the COLD bound but cannot execute
#        a COLD measurement leg. LEG 4 is NOT a measurement.
#
# All bound values are PROPOSALS under LEADER RULE (doc §9.7), NOT ratified.
# Evidence / derivation brackets: .agents/shared/planning/engine-arc-phase1/
# boot-slo-measurement.md §9 (added 2026-11-22).
# =============================================================================
set -u

PACK_NAME="engine_boot_slo_gate"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL"; exit 1; }
PYTEST_BIN="$PROJECT_DIR/.venv/bin/pytest"
[ -x "$PYTEST_BIN" ] || { echo "FATAL: no pytest"; exit 1; }

# ---------------------------------------------------------------------------
# F2 BOUND CONSTANTS — SINGLE SOURCE OF TRUTH
# Provenance (all values PROPOSAL / LEADER RULES, 2026-11-22):
#   WARM 250 ms — unchanged SLO-1 dormant bound (phase4-plan.md D4.9/:40,
#     SLO-1/:121); class = dormant posture, registrations=12, warm long-lived
#     PG, un-contended host; observed WARM max 191.188 ms
#     (.f2gate-transcripts/warm-20261122T182246Z-460476.txt:268) => 1.31x
#     headroom (250/191.188 = 1.3074). The pack-executed max before it was
#     179.192 ms and 134.938 ms is the wave-1-only stat — the earlier
#     "134.938 ms => 1.85x headroom" bracket quoted the wave-1-only max and
#     overstated the headroom. The bound still holds (every observed WARM
#     datum is <= 250 ms).
#   COLD 360 ms — class = cold/first-boot OR contended host; observed cold max
#     307.878 ms (tester boot A) x 1.15 margin = 354.06 -> rounded 360;
#     flip-day bound = COLD class (only empirical flip datum = tester D 302.418).
#   Evidence pointer: boot-slo-measurement.md §9.7 (this tree).
# ---------------------------------------------------------------------------
SLO1_WARM_BOUND_MS=250
SLO1_COLD_BOUND_MS=360
DOC_REL=".agents/shared/planning/engine-arc-phase1/boot-slo-measurement.md"

LEG_FAIL=0
leg_fail() { echo "LEG-FAIL: $*"; LEG_FAIL=1; }

# ---------------------------------------------------------------------------
# LEG 1 — inherited 21-node method-wiring pytest (byte-preserved behaviour)
# ---------------------------------------------------------------------------
UNIT_FILES=(
  tests/unit/services/engine/test_wiring.py
  tests/unit/services/engine/test_boot.py
)
for f in "${UNIT_FILES[@]}"; do
  [ -f "$f" ] || { echo "FATAL: missing $f"; exit 1; }
done

SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR -u OPENAI_API_KEY
)
OUT="$(mktemp)"
timeout 280 env "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB=ensemble_dev \
  "$PYTEST_BIN" -p no:randomly -p no:xdist -v --tb=short --color=no \
  --override-ini="addopts=" "${UNIT_FILES[@]}" 2>&1 | tee "$OUT"
PYTEST_EXIT="${PIPESTATUS[0]}"
SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT" | tail -1)"
PASSED="$(sed -n 's/.*[^0-9]\([0-9]\+\) passed.*/\1/p' "$OUT" | tail -1)"
rm -f "$OUT"
[ -z "$SUMMARY" ] && { echo "LEG unit: FAIL (vacuous)"; leg_fail "unit vacuous"; }
[ -z "$PASSED" ] || [ "$PASSED" -eq 0 ] && { echo "LEG unit: FAIL (zero passed)"; leg_fail "unit zero passed"; }
echo "SUMMARY: ${SUMMARY}"
case "$PYTEST_EXIT" in
  0) echo "LEG unit: PASS" ;;
  124) echo "LEG unit: TIMEOUT"; leg_fail "unit timeout" ;;
  *) echo "LEG unit: FAIL (exit=$PYTEST_EXIT)"; leg_fail "unit exit $PYTEST_EXIT" ;;
esac

# ---------------------------------------------------------------------------
# LEG 2 — doc-equality constant pin (pack constants == doc §9.7 mirror)
# ---------------------------------------------------------------------------
DOC="$PROJECT_DIR/$DOC_REL"
if [ ! -f "$DOC" ]; then
  echo "LEG doc-equality: FAIL (missing $DOC_REL)"
  leg_fail "doc missing"
else
  DOC_FAIL=0
  grep -qF "SLO1_WARM_BOUND_MS=${SLO1_WARM_BOUND_MS}" "$DOC" || DOC_FAIL=1
  grep -qF "SLO1_COLD_BOUND_MS=${SLO1_COLD_BOUND_MS}" "$DOC" || DOC_FAIL=1
  if [ "$DOC_FAIL" -ne 0 ]; then
    echo "LEG doc-equality: FAIL (doc §9.7 mirror != pack constants: WARM=${SLO1_WARM_BOUND_MS} COLD=${SLO1_COLD_BOUND_MS})"
    leg_fail "doc constant divergence"
  else
    echo "LEG doc-equality: PASS (WARM=${SLO1_WARM_BOUND_MS} COLD=${SLO1_COLD_BOUND_MS}; doc=$DOC_REL §9.7)"
  fi
fi

# ---------------------------------------------------------------------------
# LEG 3 — WARM-class fenced boot measurement (ONE boot)
# Cap: hard wall-clock deadline 100 s + at most one poll iteration with
#      curl -m 5 => < 120 s. Only the recorded PID is signalled; an EXIT trap
#      guarantees cleanup even on early exit.
# ---------------------------------------------------------------------------
BOOT_DIR="$PROJECT_DIR/.f2gate-transcripts"
mkdir -p "$BOOT_DIR"
BOOT_LOG="$BOOT_DIR/warm-$(date -u +%Y%m%dT%H%M%SZ)-$$.txt"

GATE_PORT="$(.venv/bin/python -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()' 2>/dev/null)"
if [ -z "$GATE_PORT" ] || [ "$GATE_PORT" = "8079" ] || [ "$GATE_PORT" = "9797" ]; then
  echo "LEG warm-boot: FAIL (bad gate port '$GATE_PORT')"
  leg_fail "warm bad port"
else
  BOOT_PID_FILE="$(mktemp)"
  cleanup_warm() {
    [ -s "$BOOT_PID_FILE" ] || { rm -f "$BOOT_PID_FILE"; return 0; }
    BPID="$(cat "$BOOT_PID_FILE")"
    if kill -0 "$BPID" 2>/dev/null; then
      kill -TERM "$BPID" 2>/dev/null
      for _ in $(seq 1 10); do kill -0 "$BPID" 2>/dev/null || break; sleep 1; done
      if kill -0 "$BPID" 2>/dev/null; then
        echo "WARM_CLEANUP: TERM failed -> SIGKILL pid=$BPID" >> "$BOOT_LOG"
        kill -KILL "$BPID" 2>/dev/null; sleep 1
      fi
    fi
    echo "WARM_CLEANUP: final pid=$BPID alive=$([ kill -0 "$BPID" 2>/dev/null ] && echo YES || echo NO)" >> "$BOOT_LOG"
    rm -f "$BOOT_PID_FILE"
  }
  trap cleanup_warm EXIT

  {
    echo "=== F2 WARM-CLASS GATE BOOT ==="
    echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "worktree=$PROJECT_DIR"
    echo "git_head=$(git rev-parse HEAD)"
    echo "gate_port=$GATE_PORT"
    echo "bound_warm_ms=$SLO1_WARM_BOUND_MS (PROPOSAL/LEADER RULES; doc §9.7)"
    echo "--- effective fence (secrets not echoed) ---"
  } > "$BOOT_LOG"

  # fence verification — same fence the boot itself uses; source .env in subshell
  (
    set -a; . ./.env; set +a
    export PORT="$GATE_PORT" DAEMON_PORT="$GATE_PORT" \
      POSTGRES_DB=ensemble_dev POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 \
      ENSEMBLE_SELF_ENV=dev ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
      ENSEMBLE_DB_AMBIENT_ALLOW=1 \
      DATA_DIR=./data_dev ENSEMBLE_DATA_DIR=./data_dev PERSISTENCE_DB_PATH=./data_dev/instances.db
    {
      echo "PORT=$PORT DAEMON_PORT=$DAEMON_PORT"
      echo "POSTGRES_DB=$POSTGRES_DB POSTGRES_HOST=$POSTGRES_HOST POSTGRES_PORT=$POSTGRES_PORT POSTGRES_USER=<set-from-.env>"
      echo "ENSEMBLE_SELF_ENV=$ENSEMBLE_SELF_ENV"
      echo "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=$ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES"
      echo "ENSEMBLE_DB_AMBIENT_ALLOW=$ENSEMBLE_DB_AMBIENT_ALLOW"
      echo "DATA_DIR=$DATA_DIR ENSEMBLE_DATA_DIR=$ENSEMBLE_DATA_DIR PERSISTENCE_DB_PATH=$PERSISTENCE_DB_PATH"
      [ "$POSTGRES_DB" = ensemble_dev ] || { echo "FENCE_VIOLATION=DB_NOT_DEV"; exit 97; }
      [ "$PORT" != 8079 ] && [ "$PORT" != 9797 ] || { echo "FENCE_VIOLATION=PORT_FORBIDDEN"; exit 97; }
      [ "$ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES" = 0 ] || { echo "FENCE_VIOLATION=SWEEP_GUARD"; exit 97; }
      echo "FENCE_ASSERT=PASS"
    } >> "$BOOT_LOG"
  )
  FENCE_RC=$?
  if [ "$FENCE_RC" -ne 0 ]; then
    echo "LEG warm-boot: FAIL (fence rc=$FENCE_RC; transcript=$BOOT_LOG)"
    leg_fail "warm fence rc=$FENCE_RC"
  else
    # launch — entry is -m daemon (NEVER -m daemon.api); record $! at launch
    (
      set -a; . ./.env; set +a
      export PORT="$GATE_PORT" DAEMON_PORT="$GATE_PORT" \
        POSTGRES_DB=ensemble_dev POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 \
        ENSEMBLE_SELF_ENV=dev ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
        ENSEMBLE_DB_AMBIENT_ALLOW=1 \
        DATA_DIR=./data_dev ENSEMBLE_DATA_DIR=./data_dev PERSISTENCE_DB_PATH=./data_dev/instances.db
      exec .venv/bin/python -m daemon
    ) >> "$BOOT_LOG" 2>&1 &
    BOOT_PID=$!
    echo "$BOOT_PID" > "$BOOT_PID_FILE"
    START_MS=$(date +%s%3N)
    {
      echo "RECORDED_PID=$BOOT_PID"
      echo "RECORDED_PID_CMD=$(tr '\0' ' ' < /proc/$BOOT_PID/cmdline 2>/dev/null)"
      echo "LAUNCH_EPOCH_MS=$START_MS"
    } >> "$BOOT_LOG"

    # poll livez THEN readyz at 2 s cadence (NOT /healthz); hard deadline 100 s
    LIVEZ_AT=""; READYZ_AT=""; POSTURE=""; DEADLINE_S=$(( $(date +%s) + 100 ))
    for i in $(seq 1 50); do
      sleep 2
      NOW_MS=$(date +%s%3N)
      if [ -z "$LIVEZ_AT" ]; then
        if curl -sf -m 3 "http://127.0.0.1:${GATE_PORT}/livez" -o "$BOOT_DIR/.warm-livez-$$.json" 2>/dev/null; then
          LIVEZ_AT=$((NOW_MS - START_MS)); echo "PROBE livez GREEN at +${LIVEZ_AT}ms (t+${i}x2s)" >> "$BOOT_LOG"
        fi
      fi
      if [ -n "$LIVEZ_AT" ] && [ -z "$READYZ_AT" ]; then
        if curl -sf -m 5 "http://127.0.0.1:${GATE_PORT}/readyz" -o "$BOOT_DIR/.warm-readyz-$$.json" 2>/dev/null; then
          READYZ_AT=$((NOW_MS - START_MS)); echo "PROBE readyz GREEN at +${READYZ_AT}ms (t+${i}x2s)" >> "$BOOT_LOG"
        fi
      fi
      POSTURE="$(grep -h 'engine boot posture' "$BOOT_LOG" 2>/dev/null | head -1)"
      if [ -n "$READYZ_AT" ] && [ -n "$POSTURE" ]; then break; fi
      if ! kill -0 "$BOOT_PID" 2>/dev/null; then echo "PROBE daemon died before readyz/posture" >> "$BOOT_LOG"; break; fi
      if [ "$(date +%s)" -ge "$DEADLINE_S" ]; then echo "PROBE hard deadline reached" >> "$BOOT_LOG"; break; fi
    done

    GATED="$(printf '%s' "$POSTURE" | grep -oE 'block_elapsed_ms_gated=[0-9.]+' | head -1 | cut -d= -f2)"
    {
      echo "LIVEZ_GREEN_MS=${LIVEZ_AT:-NEVER}"
      echo "READYZ_GREEN_MS=${READYZ_AT:-NEVER}"
      echo "POSTURE_LINE=${POSTURE:-MISSING}"
      echo "GATED_MS=${GATED:-MISSING}"
      echo "WARM_BOUND_MS=$SLO1_WARM_BOUND_MS"
    } >> "$BOOT_LOG"

    PF=0
    for field in 'enabled=' 'shadow=' 'registrations=' 'use_engine_flags='; do
      printf '%s' "$POSTURE" | grep -qF "$field" || PF=1
    done
    if [ -z "$GATED" ]; then
      echo "LEG warm-boot: FAIL (no gated number; bound=$SLO1_WARM_BOUND_MS ms; transcript=$BOOT_LOG)"
      leg_fail "warm no gated number"
    elif [ "$PF" -ne 0 ]; then
      echo "LEG warm-boot: FAIL (posture fields missing; gated=$GATED bound=$SLO1_WARM_BOUND_MS; transcript=$BOOT_LOG)"
      leg_fail "warm posture fields"
    else
      OVER="$(awk -v g="$GATED" -v b="$SLO1_WARM_BOUND_MS" 'BEGIN{print (g>b)?1:0}')"
      if [ "$OVER" -ne 0 ]; then
        echo "LEG warm-boot: FAIL (gated=${GATED}ms > bound=${SLO1_WARM_BOUND_MS}ms; transcript=$BOOT_LOG)"
        leg_fail "warm gated $GATED > $SLO1_WARM_BOUND_MS"
      else
        echo "LEG warm-boot: PASS (gated=${GATED}ms <= bound=${SLO1_WARM_BOUND_MS}ms; posture fields present; livez=${LIVEZ_AT:-NEVER}ms readyz=${READYZ_AT:-NEVER}ms; transcript=$BOOT_LOG)"
      fi
    fi

    # terminate ONLY the recorded PID; confirm dead + port freed
    kill -TERM "$BOOT_PID" 2>/dev/null
    for _ in $(seq 1 10); do kill -0 "$BOOT_PID" 2>/dev/null || break; sleep 1; done
    if kill -0 "$BOOT_PID" 2>/dev/null; then
      echo "WARM_TERM: TERM failed -> SIGKILL pid=$BOOT_PID" >> "$BOOT_LOG"
      kill -KILL "$BOOT_PID" 2>/dev/null; sleep 1
    fi
    ALIVE=NO; kill -0 "$BOOT_PID" 2>/dev/null && ALIVE=YES
    LISTENERS="$(ss -ltn 2>/dev/null | grep -c ":${GATE_PORT} " || true)"
    echo "WARM_TERM: pid=$BOOT_PID alive=$ALIVE port=${GATE_PORT} listeners=${LISTENERS}" >> "$BOOT_LOG"
    [ "$ALIVE" = "NO" ] || leg_fail "warm pid still alive"
    [ "$LISTENERS" -eq 0 ] || leg_fail "warm port still listening"
    : > "$BOOT_PID_FILE"   # disarm trap (cleanup already done)
  fi
fi

# ---------------------------------------------------------------------------
# LEG 4 — COLD-class bound: constant + provenance ONLY (no executable leg)
# DISCLOSURE: fresh-PG-cluster boots are tester-lane, not pack-runnable.
# ---------------------------------------------------------------------------
echo "LEG cold-class: DISCLOSED (bound=${SLO1_COLD_BOUND_MS}ms; constant pinned by LEG 2; fresh-PG/contended boots are tester-lane — no pack measurement leg)"

# ---------------------------------------------------------------------------
# summary (RESULT: PASS shape preserved for tester-lane adoption)
# ---------------------------------------------------------------------------
if [ "$LEG_FAIL" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
