#!/usr/bin/env bash
# Test Pack: metrics_capture_lane_read_unit_test — I3 / m4 (skill-system-revival Phase 1)
#
# THE SURFACE.  `GET /api/metrics/capture-lane` — a JSON read of
# `daemon.services.skill_capture_observability.counters_snapshot()`, mounted on
# the existing metrics router (any-of scope set; the exposition route's
# fail-closed auth-OFF refusal).
#
# WHY JSON AND NOT A FAMILY.  A new exposition family adds series to the
# Prometheus body and the COMMITTED baseline artifact
# (tests/unit/metrics/baselines/metrics_exposition_baseline.txt) is that body's
# reference — a family forces a re-baseline of a committed gate artifact.  The
# counters already have a public accessor, so the read site is JSON-only and
# EXPOSITION-NEUTRAL.  This pack pins that neutrality as a first-class
# property.
#
# FILE SET
#   tests/unit/metrics/test_metrics_capture_lane_read.py   (the pin)
#
# MUTATION MATRIX (phase 2), planted on OUR OWN file (daemon/routers/metrics.py):
#   M-m4a — delete the route registration ⇒ the route pins RED
#   M-m4b — register a capture-lane exposition family at import time ⇒ the
#           EXPOSITION-NEUTRAL pins RED (the registry count moves and the
#           rendered body grows) — proving the GUARD, not merely the route
#
# FENCES: per-variable env scrub, worktree-local interpreter, dual-layer
# timeout, sha-verified PRISTINE restore, VACUOUS-GREEN guard.
# EXIT-CODE CONTRACT: 0 PASS / 1 FAIL / 124 TIMEOUT.
set -uo pipefail

PACK_START=${SECONDS:-0}
BUDGET_S="${METRICS_CAPTURE_LANE_READ_BUDGET_S:-300}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="$REPO_ROOT/.venv/bin/python"
LOG_DIR="${METRICS_CAPTURE_LANE_READ_LOG_DIR:-/tmp/metrics-capture-lane-read-gate}"
mkdir -p "$LOG_DIR"

SCRUB=(env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
       -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT
       -u ENSEMBLE_SELF_ENV -u ENSEMBLE_CONFIG -u ENSEMBLE_DATA_DIR)

PIN="tests/unit/metrics/test_metrics_capture_lane_read.py"
ROUTER="daemon/routers/metrics.py"
ROUTER_DIR="daemon/routers"

echo "=== Test Pack: metrics_capture_lane_read_unit_test ==="
echo "REPO_ROOT=$REPO_ROOT"

[ -x "$PY" ] || { echo "RESULT: FAIL (MISSING-INTERPRETER)"; exit 1; }
for f in "$PIN" "$ROUTER"; do
  [ -f "$f" ] || { echo "RESULT: FAIL (MISSING-ARTIFACT — $f)"; exit 1; }
done

elapsed_s() { echo $(( ${SECONDS:-0} - PACK_START )); }
check_budget() {
  local elapsed; elapsed="$(elapsed_s)"
  if [ "$elapsed" -gt "$BUDGET_S" ]; then
    echo "RESULT: TIMEOUT (internal timer: wall ${elapsed}s > budget ${BUDGET_S}s ${1:-})"
    exit 124
  fi
}

run_pin() {
  timeout 240 "${SCRUB[@]}" "$PY" -m pytest "$PIN" -q --tb=line -p no:randomly \
    --override-ini="addopts=" > "$1" 2>&1
}

echo "--- phase 1: the pin ---"
PHASE1_LOG="$LOG_DIR/phase1.log"
run_pin "$PHASE1_LOG"; RC=$?
tail -3 "$PHASE1_LOG" | sed 's/^/  /'
grep -E "^FAILED " "$PHASE1_LOG" | sed 's/^/  /' || true
if ! grep -qE "^[1-9][0-9]* passed" "$PHASE1_LOG"; then
  echo "RESULT: FAIL (VACUOUS — the summary does not carry 'N passed' with N >= 1)"
  exit 1
fi
[ "$RC" -ne 0 ] && { echo "RESULT: FAIL (phase 1 pin rc=$RC)"; exit 1; }
check_budget "after phase 1"

if [ "${METRICS_CAPTURE_LANE_READ_MUTATIONS:-1}" = "0" ]; then
  echo "RESULT: PASS (pin only — mutation matrix skipped by request)"
  exit 0
fi

echo "--- phase 2: the mutation matrix (plant → RED → sha-restore → GREEN) ---"
PRISTINE="$(mktemp -d /tmp/metrics-capture-lane-read-pristine-XXXXXX)"
CLEANED=0
restore_all() {
  [ "$CLEANED" = 1 ] && return 0
  CLEANED=1
  [ -f "$PRISTINE/content" ] && cp "$PRISTINE/content" "$ROUTER"
  rm -rf "$PRISTINE"
}
trap restore_all EXIT
trap 'restore_all; exit 143' TERM
trap 'restore_all; exit 130' INT

cp "$ROUTER" "$PRISTINE/content"
WANT="$(sha256sum "$ROUTER" | cut -d' ' -f1)"
echo "[pack] pristine: $ROUTER sha=$WANT"

plant() {
  "$PY" - "$1" "$2" "$3" <<'PYEOF'
import pathlib
import sys

path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path)
s = p.read_text()
n = s.count(old)
if n != 1:
    sys.stderr.write(f"PLANT-ERROR: {path}: expected 1 occurrence, found {n}\n")
    sys.exit(3)
p.write_text(s.replace(old, new))
PYEOF
}

MUTATION_FAILURES=0
leg() {
  local id="$1" old="$2" new="$3" log rc sha_before sha_after
  sha_before="$(sha256sum "$ROUTER" | cut -d' ' -f1)"
  if [ "$sha_before" != "$WANT" ]; then
    echo "  ${id}: FAIL (leg did not start pristine)"; MUTATION_FAILURES=$((MUTATION_FAILURES + 1)); return
  fi
  if ! plant "$ROUTER" "$old" "$new"; then
    echo "  ${id}: FAIL (plant error)"; MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    cp "$PRISTINE/content" "$ROUTER"; return
  fi
  log="$LOG_DIR/${id}-red.log"
  run_pin "$log"; rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  ${id}: FAIL (NON-DISCRIMINATING — the pin stayed GREEN with the defect planted)"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
  else
    echo "  ${id}: RED as intended (rc=${rc})"
    grep -E "^FAILED " "$log" | head -3 | sed 's/^/    /' || true
  fi
  cp "$PRISTINE/content" "$ROUTER"
  sha_after="$(sha256sum "$ROUTER" | cut -d' ' -f1)"
  if [ "$sha_after" != "$WANT" ]; then
    echo "  ${id}: FAIL (restore NOT sha-identical)"; MUTATION_FAILURES=$((MUTATION_FAILURES + 1)); return
  fi
  log="$LOG_DIR/${id}-green.log"
  run_pin "$log"; rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "  ${id}: FAIL (post-restore leg not GREEN, rc=${rc})"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
  else
    echo "  ${id}: GREEN after sha-verified restore"
  fi
  check_budget "after ${id}"
}

# M-m4a — remove the route/endpoint entirely (decorator + handler).
leg "M-m4a" \
  '@router.get(
    "/capture-lane",' \
  '@router.get(
    "/capture-lane-REMOVED-BY-MUTATION",'

# M-m4b — register a capture-lane exposition family at import time.
leg "M-m4b" \
  '__all__ = ["METRICS_MEDIA_TYPE", "router"]' \
  'from daemon import checkpoint_metrics as _cm_mutation
_cm_mutation.register_gauge(
    "ensemble_capture_lane_admissions_total",
    help="MUTATION: a capture-lane exposition family (must be REFUSED)",
)

__all__ = ["METRICS_MEDIA_TYPE", "router"]'

sha="$(sha256sum "$ROUTER" | cut -d' ' -f1)"
if [ "$sha" != "$WANT" ]; then
  echo "RESULT: FAIL (post-run manifest: $ROUTER is NOT byte-identical)"
  exit 1
fi
echo "[pack] post-run manifest: every touched file is byte-identical"

if [ "$MUTATION_FAILURES" -ne 0 ]; then
  echo "RESULT: FAIL (${MUTATION_FAILURES} mutation leg(s) failed)"
  exit 1
fi
echo "RESULT: PASS (2 mutation legs RED→GREEN, sha-verified restores, $(elapsed_s)s)"
exit 0
