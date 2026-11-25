#!/usr/bin/env bash
# =============================================================================
# Test Pack: por_c_checkpoint_retention_unit_test
#
# Lane: fix/post-outage-recovery (base `a52b63dce`) — the 2026-11-23
#       post-outage wedge taxonomy, lane **c** (checkpoint retention).
#
# WHAT IT PINS
#   tests/unit/services/test_checkpoint_retention_lane_pins.py
#   tests/unit/services/test_checkpoint_blob_bytes_guard_pins.py
#   tests/unit/test_checkpoint_lane_docs_pins.py
#   Total: 49 nodes (mechanical `--collect-only` census at this freeze:
#   49 collected, RC=0, 0 collection errors).
#
#   Lane c owns the checkpoint-retention lane: the retention sweep's
#   predicates, its kill-switch, and the BYTES-level guard that measures
#   actual blob volume (not dead-tuple counts) before the operator is nudged.
#   The docs pins hold the runbook's promises to the code (the primary signal
#   is the size ratio; the daemon never shells out to kubectl; the lane is
#   operator-nudged, never automatic).
#
# CONTRACT (house shape — mirrors the engine-arc lane packs)
#   - Dual-layer timeout: inner `timeout 280`; caller wraps with 300.
#   - Serial + deterministic: `-p no:xdist -p no:randomly`.
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob is a SILENT NO-OP in bash
#     — bash does not glob env-var names in `env -u`).
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY (never
#     `env -u` on it: stripping the ambient guard makes the boot sweep
#     SIGTERM/SIGKILL live processes).
#   - Repo addopts KEPT (the `-m 'not integration and not postgres ...'`
#     marker filter) so a postgres-marked node can never touch a DB; the
#     census that produced the node count was measured under the SAME filter,
#     so the pack's node count and the census are comparable.
#   - SLICING PROOF: the pack re-derives its own node count with
#     `--collect-only` and FAILS if it drifts from the declared 49 — a
#     pack that silently runs a different node set is inadmissible.
#   - VACUOUS-VERDICT GUARD: zero passed tests or no summary line = FAIL
#     (a pack that can PASS from an empty run is worthless).
#   - Output: header, pytest body, `RESULT: PASS|FAIL|TIMEOUT`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
# =============================================================================
set -u

PACK_NAME="por_c_checkpoint_retention_unit_test"
EXPECTED_NODES=49

echo "=== Test Pack: ${PACK_NAME} ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL (cannot cd to ${PROJECT_DIR})"; exit 1; }

PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "FATAL: .venv/bin/python not found at $PYTHON_BIN" >&2
  echo "RESULT: FAIL"
  exit 1
fi

UNIT_FILES=(
  tests/unit/services/test_checkpoint_retention_lane_pins.py
  tests/unit/services/test_checkpoint_blob_bytes_guard_pins.py
  tests/unit/test_checkpoint_lane_docs_pins.py
)

for f in "${UNIT_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "FATAL: required test file missing: $f" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
done

# ── the per-variable env scrub (never a POSTGRES_* glob) ─────────────────
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u OPENAI_API_KEY
  -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_CLIENT_ID
  -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_DISCOVERY_URL
  -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
  -u ENSEMBLE_BASH_TRACKING_ID
)

# ── the SLICING PROOF (node-count parity vs the declared census) ────────
echo
echo "--- slicing proof: --collect-only over the declared file set ---"
COLLECT_OUT="$(mktemp)"
timeout 120 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  "$PYTHON_BIN" -m pytest \
    -p no:randomly \
    -p no:xdist \
    --collect-only \
    -q \
    --color=no \
    "${UNIT_FILES[@]}" > "$COLLECT_OUT" 2>&1
COLLECT_EXIT="$?"
COLLECTED="$(grep -oE '^[0-9]+ tests? collected' "$COLLECT_OUT" | head -1 | grep -oE '^[0-9]+')"

if [ "$COLLECT_EXIT" -ne 0 ]; then
  echo "SLICING PROOF: FAIL (collection exit=$COLLECT_EXIT)"
  tail -25 "$COLLECT_OUT"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (slicing proof)"
  exit 1
fi
if [ -z "$COLLECTED" ]; then
  echo "SLICING PROOF: FAIL (VACUOUS — no 'collected' summary line)"
  tail -25 "$COLLECT_OUT"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (slicing proof)"
  exit 1
fi
if [ "$COLLECTED" -ne "$EXPECTED_NODES" ]; then
  echo "SLICING PROOF: FAIL (node-count drift: expected $EXPECTED_NODES, collected $COLLECTED)"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (slicing proof)"
  exit 1
fi
echo "SLICING PROOF: OK ($COLLECTED nodes collected, expected $EXPECTED_NODES)"
rm -f "$COLLECT_OUT"

echo
echo "--- lane c: checkpoint retention lane + blob-bytes guard + lane docs ---"
OUT="$(mktemp)"
  # NOTE: `--ff` is the pytest>=9 spelling of `--rf` (--failed-first);
  #       pytest 9.0.2 REMOVED the `--rf` short form (unrecognized argument).
timeout 280 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTEST_TIMEOUT=280 \
  "$PYTHON_BIN" -m pytest \
    -q \
    -p no:randomly \
    -p no:xdist \
    --tb=short \
    --ff \
    --color=no \
    --override-ini="timeout=120" \
    "${UNIT_FILES[@]}" 2>&1 | tee "$OUT"
PYTEST_EXIT="${PIPESTATUS[0]}"

# ── the VACUOUS-VERDICT GUARD ───────────────────────────────────────────
# NOTE: under `-q` the summary line STARTS with the count ("31 passed, ..."),
# so the engine-arc house sed (`[^0-9]`-anchored) cannot extract it — the
# extraction below anchors on the TOKEN, not on a preceding non-digit.
SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT" | tail -1)"
PASSED="$(printf '%s\n' "$SUMMARY" | grep -oE '[0-9]+ passed' | head -1 | grep -oE '^[0-9]+')"
rm -f "$OUT"

if [ -z "$SUMMARY" ]; then
  echo
  echo "RESULT: FAIL (VACUOUS — pytest produced NO summary: the suite did not run)"
  exit 1
fi
if [ -z "$PASSED" ] || [ "$PASSED" -eq 0 ]; then
  echo
  echo "RESULT: FAIL (VACUOUS — zero tests passed: '${SUMMARY}')"
  exit 1
fi
echo "SUMMARY: ${SUMMARY}"

case "$PYTEST_EXIT" in
  0)
    echo "RESULT: PASS"
    exit 0
    ;;
  124)
    echo "RESULT: TIMEOUT"
    exit 124
    ;;
  *)
    echo "RESULT: FAIL (pytest exit=$PYTEST_EXIT)"
    exit 1
    ;;
esac
