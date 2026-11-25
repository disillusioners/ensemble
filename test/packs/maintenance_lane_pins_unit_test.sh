#!/usr/bin/env bash
# =============================================================================
# Test Pack: maintenance_lane_pins_unit_test
#
# Lane: feature/pack-coverage-registration (base `ed8ab508c`) — PACKS.md
#       coverage wave B, family B1 (Central Maintenance System pins).
#
# WHAT IT PINS
#   tests/unit/test_maintenance_acceptance_map.py
#   tests/unit/test_maintenance_manual_trigger_provenance.py
#   tests/unit/test_maintenance_no_idle_trigger.py
#   Total: 95 nodes (mechanical `--collect-only` census at this freeze:
#   95 collected, RC=0, 0 collection errors; per-file 72 / 16 / 7).
#
#   Acceptance map (P5 T5.5) — every row of the phase-5 verification-pass
#     map gets ONE named, green test node; sibling-owned rows are CITED
#     (node-id existence + pass asserted) instead of duplicated.
#   Manual-trigger provenance (P5 T5.6) — the message-body trigger token
#     round-trip (`trigger: manual` parsed by the orchestrator, passed
#     through the writer dispatch envelope; the writer NEVER reads the
#     DB, R-P5-7) plus the secondary audit sub-check (row provenance +
#     per-fire audit).
#   No-idle-trigger (P5 T5.1, BR-6, MUTATION TARGET) — the R2
#     no-idle-starvation two-legged pin: a BUSY system still enqueues the
#     due maintenancer chain one-shot (behavioral), and the canonical
#     no-JOB_IDLE_POLICIES/IdlePolicy/_is_idle predicate holds over
#     exactly four daemon files via an AST scan (structural).
#
# CONTRACT (house shape — mirrors por_b2_wedge_repro_pins_unit_test)
#   - Dual-layer timeout: inner `timeout 280`; caller wraps with 300.
#   - Serial + deterministic: `-p no:xdist -p no:randomly`.
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob is a SILENT NO-OP in bash
#     — bash does not glob env-var names in `env -u`).
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY (never
#     `env -u` on it: stripping the ambient guard makes the boot sweep
#     SIGTERM/SIGKILL live processes).
#   - Repo addopts KEPT (the `-m 'not integration and not postgres ...'`
#     marker filter) — none of the three files carry an excluded marker,
#     and the census that produced 95 was measured under the SAME filter,
#     so the pack's node count and the census are comparable.
#   - SLICING PROOF: the pack re-derives its own node count with
#     `--collect-only` and FAILS if it drifts from the declared 95 — a
#     pack that silently runs a different node set is inadmissible.
#   - VACUOUS-VERDICT GUARD: zero passed tests or no summary line = FAIL
#     (a pack that can PASS from an empty run is worthless).
#   - Output: header, pytest body, `RESULT: PASS|FAIL|TIMEOUT`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
# =============================================================================
set -u

PACK_NAME="maintenance_lane_pins_unit_test"
EXPECTED_NODES=95

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
  tests/unit/test_maintenance_acceptance_map.py
  tests/unit/test_maintenance_manual_trigger_provenance.py
  tests/unit/test_maintenance_no_idle_trigger.py
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
  -u SYSTEM_ENCRYPTION_KEY -u PLANE_MCP_API_KEY -u EMBEDDING_API_KEY
  -u SKILL_EVOLUTION_EMBEDDING_API_KEY -u BLUEPRINT_EMBEDDING_API_KEY
  -u LIGHTRAG_API_KEY
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
echo "--- lane B1: acceptance map / manual-trigger provenance / no-idle-trigger ---"
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

# A timeout produces no pytest summary line — map it BEFORE the vacuous guard
# so the guard cannot swallow it and report FAIL/exit 1.
if [ "$PYTEST_EXIT" -eq 124 ]; then
  echo
  echo "RESULT: TIMEOUT"
  exit 124
fi

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
