#!/usr/bin/env bash
# =============================================================================
# Test Pack: group_chat_room_a1b_router_service_tool_pins_unit_test
#
# Family A1 "group-chat room" pins — HALF A1b (the routers + services + tools half, 8 files)
#
# Lane: feature/pack-coverage-registration (base `40301a5e3`).
#
# A1 SPLIT (2026-11-25). The former single 14-file room pack measured
#   181.4 s standalone, 200.15 s under the cold verifier's conditions and
#   247.7 s (270 s wall) UNDER LOAD — up to 88 % of its inner 280 s timeout,
#   against a documented outer 300 s bound. A flaky gate is worse than no
#   gate, so the family is split in two by directory:
#     A1a = the 6 tests/unit/repositories/ room files
#           (group_chat_room_a1a_repo_pins_unit_test)
#     A1b = the 8 tests/unit/routers/ + tests/unit/services/
#           + tests/unit/tools/ room files
#           (group_chat_room_a1b_router_service_tool_pins_unit_test)
#
# WHAT IT PINS (per-file node counts = mechanical `--collect-only` census at
# this freeze, under the repo addopts: 126 collected, RC=0, 0 collection errors)
#   tests/unit/routers/test_room_http_api.py                             16
#   tests/unit/routers/test_rooms_list_api.py                            41
#   tests/unit/services/test_room_delivery_refactor_pins.py              10
#   tests/unit/services/test_room_gc_sweeper.py                          25
#   tests/unit/services/test_room_p6_wiring_pin.py                        6
#   tests/unit/services/test_room_post_cap.py                            12
#   tests/unit/services/test_spawn_room_join_broadcast.py                 7
#   tests/unit/tools/test_room_close.py                                   9
#   Total: 126 nodes.
#
#   Agent group-chat room P6 pins, routers + services + tools half: the room HTTP/SSE API,
#   the rooms-list API, the delivery refactor pins, the GC purge sweeper, the P6 wiring pin,
#   the §9 per-turn post budget (≤4, manager-level, turn-boundary reset), the spawn/join
#   broadcast, and the room_close verb (owner-only, typed refusals, read-only after close).
#
# CONTRACT (house shape — mirrors the engine-arc lane packs)
#   - Dual-layer timeout: inner `timeout 280`; caller wraps with 300.
#   - Serial + deterministic: `-p no:xdist -p no:randomly`.
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob is a SILENT NO-OP in
#     bash — bash does not glob env-var names in `env -u`).
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY (never
#     `env -u` on it: stripping the ambient guard makes the boot sweep
#     SIGTERM/SIGKILL live processes).
#   - Repo addopts KEPT (the `-m 'not integration and not postgres ...'`
#     marker filter) so a postgres-marked node can never touch a DB; the
#     census that produced the node count was measured under the SAME
#     filter, so the pack's node count and the census are comparable.
#   - SLICING PROOF: the pack re-derives its own node count with
#     `--collect-only` and FAILS if it drifts from the declared 126 — a
#     pack that silently runs a different node set is inadmissible.
#   - VACUOUS-VERDICT GUARD: zero passed tests or no summary line = FAIL
#     (a pack that can PASS from an empty run is worthless).
#   - Output: header, pytest body, `RESULT: PASS|FAIL|TIMEOUT`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
# =============================================================================
set -u

PACK_NAME="group_chat_room_a1b_router_service_tool_pins_unit_test"
EXPECTED_NODES=126

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
  tests/unit/routers/test_room_http_api.py
  tests/unit/routers/test_rooms_list_api.py
  tests/unit/services/test_room_delivery_refactor_pins.py
  tests/unit/services/test_room_gc_sweeper.py
  tests/unit/services/test_room_p6_wiring_pin.py
  tests/unit/services/test_room_post_cap.py
  tests/unit/services/test_spawn_room_join_broadcast.py
  tests/unit/tools/test_room_close.py
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
echo "--- family A1b: group-chat room pins — routers + services + tools half (8 files) ---"
OUT="$(mktemp)"
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
SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT" | tail -1)"
PASSED="$(printf '%s\n' "$SUMMARY" | grep -oE '[0-9]+ passed' | head -1 | grep -oE '^[0-9]+')"
rm -f "$OUT"

# NOTE (ordering): the TIMEOUT verdict is checked FIRST, before the
# vacuous-verdict guard. A killed pytest produces NO summary line at all,
# so the vacuous guard would fire and report `RESULT: FAIL` (exit 1) for a
# genuine timeout — and the pack contract's `RESULT: TIMEOUT` / exit 124
# would be unreachable. Timeout is a distinct verdict and must win.
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
