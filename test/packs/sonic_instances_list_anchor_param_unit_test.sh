#!/usr/bin/env bash
# Test Pack: sonic_instances_list_anchor_param_unit_test
#
# The ROUTE-BOUNDARY pack for the `anchor` presence param on
# GET /api/instances (commission: instances-list-anchor; wave
# standard-path verification, 2026-11-24).
#
# WHAT THIS PACK EXERCISES:
#   tests/api/test_list_instances_anchor_param.py — 6 nodes:
#     (1) test_anchor_accepted            — the param is ACCEPTED (HTTP 200).
#     (2) test_anchor_forwarded_verbatim  — the anchor reaches the facade
#                                           VERBATIM (no coercion/mangling).
#     (3) test_anchor_absent_not_called   — NO anchor ⇒ the facade is NOT
#                                           called at all (back-compat: the
#                                           second offload never happens).
#     (4) test_anchor_empty_string_forwarded — an EMPTY anchor is a PRESENT
#                                           value ⇒ forwarded; the facade
#                                           degrades to an empty family.
#     (5) test_envelope_unchanged         — the response envelope keeps its
#                                           shape and `total` keeps
#                                           normal-page semantics (the anchor
#                                           family is NOT counted in `total`).
#     (6) test_anchor_merge_appends_row   — the family row is MERGED into
#                                           `instances` (deduped by
#                                           instance_id, appended after the
#                                           normal page rows).
#
# The pack is a re-runnable, FENCED wrapper around that file so the
# commission's two new packs are GATE-VERIFIABLE ("gate-verifiable =
# registered" — .agents/tester/PACKS.md).
#
# Fences (house):
#   - Per-variable env scrub. The `env -u POSTGRES_*` GLOB form is a
#     SILENT NO-OP (bash does not glob env names) and is NEVER used here.
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY (the
#     frozen-binary boot sweep KILLS live processes when the var is absent;
#     `-u` on it strips the ambient guard).
#   - ENSEMBLE_SELF_ENV=dev (the ambient default on this host is `live`).
#   - FAKE_HOME (mktemp) — no live-shaped path resolution; removed on exit.
#   - Interpreter: the checkout's own .venv; cwd is the checkout root so
#     `import daemon` resolves HERE (phase-0 import guard proves it).
#   - INTERNAL TIMER: the pack asserts its own wall-clock budget after
#     every phase; overrun prints RESULT: TIMEOUT and exits 124.
#   - Dual-layer timeout: the outer `timeout 300` plus the inner
#     command-level `timeout`.
#
# Node census: phase 1 collects the file MECHANICALLY (--collect-only)
# and FAILS if the count drifts from the registered 6 (a registry row that
# silently rots is worse than no registry row). A zero-node collection is
# a FAIL, never a PASS (anti-vacuity).
#
# Usage:
#   bash test/packs/sonic_instances_list_anchor_param_unit_test.sh
#
# Pack exit: 0 PASS / 1 FAIL / 124 TIMEOUT. The RESULT label always
# prints — a red leg never kills the pack silently.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

PACK_NAME="sonic_instances_list_anchor_param_unit_test"
TEST_FILE="tests/api/test_list_instances_anchor_param.py"
EXPECTED_NODES=6

echo "=== Test Pack: $PACK_NAME ==="

# --- internal wall-clock budget ------------------------------------------
PACK_START="$(date +%s)"
BUDGET_S="${SONIC_ANCHOR_PARAM_BUDGET_S:-300}"
elapsed_s() { echo $(( $(date +%s) - PACK_START )); }
check_budget() {
  local elapsed label="${1:-}"
  elapsed="$(elapsed_s)"
  if [ "$elapsed" -gt "$BUDGET_S" ]; then
    echo "RESULT: TIMEOUT (internal timer: wall ${elapsed}s > budget ${BUDGET_S}s ${label})"
    exit 124
  fi
}

# --- interpreter ----------------------------------------------------------
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  PY="$PROJECT_DIR/.venv/bin/python"
elif [ -x "${ENSEMBLE_MAIN_VENV:-/home/nea/ensemble-src/.venv/bin/python}" ]; then
  PY="${ENSEMBLE_MAIN_VENV:-/home/nea/ensemble-src/.venv/bin/python}"
else
  PY="$(command -v python3 || command -v python)"
fi
echo "[pack] interpreter: $PY"

# --- fences ---------------------------------------------------------------
FAKE_HOME="$(mktemp -d /tmp/sonic-anchor-param-pack-XXXXXX)"
PACK_CLEANED=0
cleanup() {
  [ "$PACK_CLEANED" = 1 ] && return 0
  PACK_CLEANED=1
  rm -rf "$FAKE_HOME"
}
trap cleanup EXIT
trap 'cleanup; exit 143' TERM
trap 'cleanup; exit 130' INT

# Per-variable scrub — NEVER the ``env -u POSTGRES_*`` glob (silent no-op).
# HOME is redirected to the FAKE_HOME so no live-shaped path is resolved.
SCRUB=(
  env
  -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_HOST -u POSTGRES_PORT
  -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u PORT -u DAEMON_PORT
  -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_DISCOVERY_URL
  -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_CLIENT_ID
  -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_ALLOWED_ORIGINS
  -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
  PYTHONPATH=
  HOME="$FAKE_HOME"
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0
  ENSEMBLE_SELF_ENV=dev
  PYTHONDONTWRITEBYTECODE=1
)

# --- phase 0: import guard ------------------------------------------------
# Prove `import daemon` resolves to THIS checkout. A run that silently
# imported the shared checkout would report a green result computed
# against FOREIGN code. realpath-exact comparison — NOT a `startswith`
# prefix test, which has a sibling-prefix trap.
EXPECTED_DAEMON="$(cd "$PROJECT_DIR" && pwd -P)/daemon/__init__.py"
IMPORTED_DAEMON="$("${SCRUB[@]}" "$PY" -c 'import daemon, os; print(os.path.realpath(daemon.__file__))' 2>/dev/null || true)"
if [ "$IMPORTED_DAEMON" != "$EXPECTED_DAEMON" ]; then
  echo "[pack] import guard: expected $EXPECTED_DAEMON"
  echo "[pack] import guard: got      ${IMPORTED_DAEMON:-<none>}"
  echo "RESULT: FAIL (phase 0: import guard)"
  exit 1
fi
echo "[pack] import guard OK: $IMPORTED_DAEMON"
check_budget "(phase 0)"

# --- phase 1: mechanical node census -------------------------------------
CENSUS_OUT="$FAKE_HOME/census.txt"
timeout 300 "${SCRUB[@]}" "$PY" -m pytest "$TEST_FILE" \
  --collect-only -q -p no:randomly --timeout=120 > "$CENSUS_OUT" 2>&1
CENSUS_RC=$?
if [ "$CENSUS_RC" -ne 0 ]; then
  echo "--- census output ---"
  tail -30 "$CENSUS_OUT"
  echo "RESULT: FAIL (phase 1: census rc=$CENSUS_RC — collection error)"
  exit 1
fi
COLLECTED="$(grep -E '^[0-9]+ tests? collected' "$CENSUS_OUT" | tail -1 | grep -oE '^[0-9]+' || true)"
if [ -z "$COLLECTED" ]; then
  COLLECTED="$(grep -cE "^${TEST_FILE}::" "$CENSUS_OUT" || true)"
fi
if [ "${COLLECTED:-0}" -eq 0 ]; then
  echo "--- census output ---"
  tail -30 "$CENSUS_OUT"
  echo "RESULT: FAIL (phase 1: census collected 0 nodes — vacuous pin)"
  exit 1
fi
if [ "$COLLECTED" -ne "$EXPECTED_NODES" ]; then
  echo "RESULT: FAIL (phase 1: census drift — collected $COLLECTED, registered $EXPECTED_NODES)"
  exit 1
fi
echo "[pack] census OK: $COLLECTED nodes (registered $EXPECTED_NODES)"
check_budget "(phase 1)"

# --- phase 2: the pack itself --------------------------------------------
RUN_OUT="$FAKE_HOME/run.txt"
timeout 300 "${SCRUB[@]}" "$PY" -m pytest "$TEST_FILE" \
  -q -p no:randomly --timeout=120 --tb=short -rfE > "$RUN_OUT" 2>&1
RUN_RC=$?
tail -40 "$RUN_OUT"
if [ "$RUN_RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT (phase 2: pytest rc=124)"
  exit 124
fi
if [ "$RUN_RC" -ne 0 ]; then
  echo "RESULT: FAIL (phase 2: pytest rc=$RUN_RC)"
  exit 1
fi
check_budget "(phase 2)"

echo "RESULT: PASS ($PACK_NAME — $COLLECTED/$COLLECTED nodes)"
exit 0
