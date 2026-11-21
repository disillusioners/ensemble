#!/usr/bin/env bash
# =============================================================================
# Test Pack: engine_substrate_unit_test
#
# Phase: engine-arc-phase1 P1 (Substrate) — T1.9.
# Owner-of-record: P1 authors this pack; P7 REGISTERS it in
# `.agents/tester/PACKS.md` (deliberately NOT done here).
#
# WHAT IT PINS
#   tests/unit/services/test_engine_jobspec_registration.py
#       — T1.7/T1.8/T1.10 + AC4/AC5/AC6/AC7/AC9: spec validation (one error
#         code per rule), the registration contract (version arithmetic,
#         no-op echo, changed-content bump, run-history preservation), the
#         self-describing metadata surface, the TriggerSpec data-only seam,
#         the repository's closed surface, the EngineConfig resolution
#         (defaults + env + yaml-precedence), the zero-execution fence and
#         the SERVICES_* continuity drift pin.
#   tests/unit/services/test_engine_config_deadknobs.py
#       — T1.12/T1.13 + AC10/AC11: the three retired knobs are gone from
#         their config classes and from the whole tree; the two misfiled
#         knobs kept their class AND env var (description-only change).
#   tests/unit/migrations/test_engine_substrate_migration.py
#       — T1.1/T1.2/T1.3 + AC1/AC2/AC3/AC8-delta: two migration files with
#         distinct prefixes, IF NOT EXISTS throughout, TEXT strings, DOWN
#         sections, no trailing-comment semicolon, throwaway-SQLite apply +
#         idempotency, model index lockstep (byte-exact, incl. the AMD-7
#         UNIQUE service_tag), manager registration ordering, no
#         _ensure_postgres_columns mirror.
#
# PLUS THE AC10 COLLECTION GATE
#   `pytest --collect-only` over a bounded, named scope. This is the REAL
#   AC10 gate: a deleted pydantic field explodes at COLLECTION time
#   (`TypeError: unexpected keyword`) and a GET-on-spec-mock explodes when
#   the module is imported, so a collect-only pass proves the atomic
#   cleanup did not leave a broken call site. It is NOT a full-suite run.
#
# CONTRACT
#   - Dual-layer timeout: inner `timeout 280`; caller wraps with 300.
#   - Serial + deterministic: `-p no:xdist -p no:randomly`.
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob is a silent no-op in
#     bash — see the house critical notes). Covers the LIVE credential
#     class and the LIVE PG coords; also pins
#     ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 EXPLICITLY (never `env -u`
#     on it: stripping the ambient guard makes the sweep kill live procs).
#   - Repo addopts cleared (`--override-ini="addopts="`).
#   - VACUOUS-VERDICT GUARD: zero passed tests or no summary line =
#     FAIL (a pack that can PASS from an empty run is worthless).
#   - Output: header, pytest body, `RESULT: PASS|FAIL|TIMEOUT`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
# =============================================================================
set -u

PACK_NAME="engine_substrate_unit_test"

echo "=== Test Pack: ${PACK_NAME} ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL (cannot cd to ${PROJECT_DIR})"; exit 1; }

PYTEST_BIN="$PROJECT_DIR/.venv/bin/pytest"
if [ ! -x "$PYTEST_BIN" ]; then
  echo "FATAL: .venv/bin/pytest not found at $PYTEST_BIN" >&2
  echo "RESULT: FAIL"
  exit 1
fi

UNIT_FILES=(
  tests/unit/services/test_engine_jobspec_registration.py
  tests/unit/services/test_engine_config_deadknobs.py
  tests/unit/migrations/test_engine_substrate_migration.py
)

for f in "${UNIT_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "FATAL: required test file missing: $f" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
done

# Bounded collection scope for the AC10 gate — the files whose CONSTRUCTION
# of a config object would break on a half-finished dead-knob cleanup.
COLLECT_FILES=(
  tests/test_config.py
  tests/test_manager.py
  tests/test_manager_shutdown.py
  tests/test_progressive_dispatch.py
  tests/test_question_pack_boot_rehydrate.py
  tests/test_spawn_limit_edge_cases.py
  tests/tools/test_auto_kill_integration.py
  tests/unit/test_mcp_cold_load_race.py
  tests/unit/test_mission_record_review_fixes.py
  tests/unit/test_scheduler_deferred_delivery_pin.py
  tests/unit/test_scheduler_deferred_mechanism_probe.py
  tests/unit/test_builtin_mcp_servers.py
  tests/unit/test_context7_builtin.py
  tests/unit/test_webfetch_builtin.py
  tests/integration/chat_source_harness.py
  tests/integration/test_checkpoint_pool_knob_boot_resolution.py
  tests/integration/test_job_driven_enqueue_work_id_facade.py
  tests/integration/test_job_driven_enqueue_image_refs_facade.py
  tests/integration/test_messages_ref_path_e2e.py
  tests/integration/test_messages_ref_path_fallback.py
  tests/integration/test_spawn_pause_resume_dispatch_durable.py
  tests/integration/test_paused_auto_resume_facade_image_refs.py
  tests/integration/test_wc_wake_pure_hang.py
)

for f in "${COLLECT_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "FATAL: collection-scope file missing: $f" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
done

# ── the per-variable env scrub ──────────────────────────────────────────
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u OPENAI_API_KEY
  # The retired-knob env names, in case an ambient shell still exports them.
  -u SERVICES_WORKER_POLL_INTERVAL
  -u SERVICES_LEASE_HEARTBEAT_INTERVAL_SECONDS
  -u PERSISTENCE_CHECKPOINT_INTERVAL
)

# ── the AC10 COLLECTION GATE (this is the real dead-knob gate) ─────────
echo
echo "--- AC10 collection gate (--collect-only over the touched surface) ---"
COLLECT_OUT="$(mktemp)"
timeout 200 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  "$PYTEST_BIN" \
    -p no:randomly \
    -p no:xdist \
    --collect-only \
    -q \
    --color=no \
    --override-ini="addopts=" \
    "${COLLECT_FILES[@]}" 2>&1 | tee "$COLLECT_OUT"
COLLECT_EXIT="${PIPESTATUS[0]}"

if [ "$COLLECT_EXIT" -ne 0 ]; then
  echo "COLLECTION GATE: FAIL (exit=$COLLECT_EXIT) — a dead-knob call site survived"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (AC10 collection gate)"
  exit 1
fi
if grep -qE "(ERROR|errors during collection)" "$COLLECT_OUT"; then
  echo "COLLECTION GATE: FAIL (collection errors detected)"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (AC10 collection gate)"
  exit 1
fi
COLLECTED="$(grep -oE '[0-9]+ tests? collected' "$COLLECT_OUT" | head -1)"
if [ -z "$COLLECTED" ]; then
  echo "COLLECTION GATE: FAIL (VACUOUS — no 'collected' summary line)"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (AC10 collection gate)"
  exit 1
fi
echo "COLLECTION GATE: OK ($COLLECTED)"
rm -f "$COLLECT_OUT"

# ── the unit legs ───────────────────────────────────────────────────────
echo
echo "--- unit legs ---"
OUT="$(mktemp)"
timeout 280 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTEST_TIMEOUT=280 \
  "$PYTEST_BIN" \
    -p no:randomly \
    -p no:xdist \
    -v \
    --tb=short \
    --color=no \
    --override-ini="addopts=" \
    --override-ini="timeout=280" \
    "${UNIT_FILES[@]}" 2>&1 | tee "$OUT"
PYTEST_EXIT="${PIPESTATUS[0]}"

# ── the VACUOUS-VERDICT GUARD ───────────────────────────────────────────
SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT" | tail -1)"
PASSED="$(sed -n 's/.*[^0-9]\([0-9]\+\) passed.*/\1/p' "$OUT" | tail -1)"
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
