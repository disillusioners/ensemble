#!/usr/bin/env bash
# Test Pack: metrics_capture_lane_off_seam_unit_test — I2 / C2 (skill-system-revival Phase 1)
#
# THE GAP THIS PACK GUARDS.  `SkillMetricsService._check_capture_eligibility`
# Gate 0 returns None when ENSEMBLE_SKILL_CAPTURE_ENABLED is off — BEFORE the
# dispatcher's own gate is reached.  The dispatcher counts its refusals and the
# TOOLS lane calls `record_capture_lane_disabled_refusal` at its own seam, so
# `admission_refusals['capture_lane_off']` was PARTIAL: it moved for the tools
# lane and NEVER for the production metrics-lane path.  The fix wires the
# metrics-lane seam.
#
# FILE SET
#   tests/unit/test_skill_metrics_capture_lane_off_seam.py   (the pin)
#
# MUTATION MATRIX (phase 2) — planted on OUR OWN file only
# (daemon/services/skill_metrics_service.py; never in another lane's file):
#   M-C2a — delete the recorder call at Gate 0 ⇒ the counting pins RED
#   M-C2b — move the `return None` ABOVE the recorder call (the pre-fix
#           order) ⇒ the ORDERING pin REDs while the call still exists
#   (M-C2c — drop the counters increment inside the observability helper —
#    is a plant in ANOTHER LANE'S file, so it is proven in-test by
#    monkeypatch instead: see TestTheSeamIsDefensive / the real-counter pin.)
#
# FENCES: per-variable env scrub, worktree-local interpreter, dual-layer
# timeout, sha-verified PRISTINE restore, VACUOUS-GREEN guard; NO env-switch
# writes (the pin simulates the switch via monkeypatch only).
# EXIT-CODE CONTRACT: 0 PASS / 1 FAIL / 124 TIMEOUT.
set -uo pipefail

PACK_START=${SECONDS:-0}
BUDGET_S="${METRICS_CAPTURE_LANE_OFF_BUDGET_S:-300}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="$REPO_ROOT/.venv/bin/python"
LOG_DIR="${METRICS_CAPTURE_LANE_OFF_LOG_DIR:-/tmp/metrics-capture-lane-off-gate}"
mkdir -p "$LOG_DIR"

SCRUB=(env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
       -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT
       -u ENSEMBLE_SELF_ENV -u ENSEMBLE_CONFIG -u ENSEMBLE_DATA_DIR
       -u ENSEMBLE_SKILL_CAPTURE_ENABLED -u ENSEMBLE_SKILL_EVOLUTION_ENABLED)

PIN="tests/unit/test_skill_metrics_capture_lane_off_seam.py"
SVC="daemon/services/skill_metrics_service.py"

echo "=== Test Pack: metrics_capture_lane_off_seam_unit_test ==="
echo "REPO_ROOT=$REPO_ROOT"

if [ ! -x "$PY" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — the worktree-local .venv/bin/python is absent)"
  exit 1
fi
for f in "$PIN" "$SVC"; do
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

# ── phase 1: the pin ───────────────────────────────────────────────────────
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

if [ "${METRICS_CAPTURE_LANE_OFF_MUTATIONS:-1}" = "0" ]; then
  echo "RESULT: PASS (pin only — mutation matrix skipped by request)"
  exit 0
fi

# ── phase 2: the mutation matrix ───────────────────────────────────────────
echo "--- phase 2: the mutation matrix (plant → RED → sha-restore → GREEN) ---"
PRISTINE="$(mktemp -d /tmp/metrics-capture-lane-off-pristine-XXXXXX)"
CLEANED=0
restore_all() {
  [ "$CLEANED" = 1 ] && return 0
  CLEANED=1
  [ -f "$PRISTINE/content" ] && cp "$PRISTINE/content" "$SVC"
  rm -rf "$PRISTINE"
}
trap restore_all EXIT
trap 'restore_all; exit 143' TERM
trap 'restore_all; exit 130' INT

cp "$SVC" "$PRISTINE/content"
WANT="$(sha256sum "$SVC" | cut -d' ' -f1)"
echo "[pack] pristine: $SVC sha=$WANT"

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
  local id="$1" old="$2" new="$3" log rc
  local sha_before sha_after
  sha_before="$(sha256sum "$SVC" | cut -d' ' -f1)"
  if [ "$sha_before" != "$WANT" ]; then
    echo "  ${id}: FAIL (leg did not start pristine)"; MUTATION_FAILURES=$((MUTATION_FAILURES + 1)); return
  fi
  if ! plant "$SVC" "$old" "$new"; then
    echo "  ${id}: FAIL (plant error)"; MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    cp "$PRISTINE/content" "$SVC"; return
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
  cp "$PRISTINE/content" "$SVC"
  sha_after="$(sha256sum "$SVC" | cut -d' ' -f1)"
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

# M-C2a — DELETE the recorder call entirely.
leg "M-C2a" \
  '            _record_capture_lane_off_refusal(
                agent_id=agent_id,
                task_message=task_message,
                prompt_source="skill_metrics_service",
            )
' \
  ''

# M-C2b — MOVE the early return ABOVE the recorder call (the pre-fix order:
# the call still exists, so a textual "is the call present" check stays green).
leg "M-C2b" \
  '            _record_capture_lane_off_refusal(
                agent_id=agent_id,
                task_message=task_message,
                prompt_source="skill_metrics_service",
            )
            return None' \
  '            return None
            _record_capture_lane_off_refusal(
                agent_id=agent_id,
                task_message=task_message,
                prompt_source="skill_metrics_service",
            )'

sha="$(sha256sum "$SVC" | cut -d' ' -f1)"
if [ "$sha" != "$WANT" ]; then
  echo "RESULT: FAIL (post-run manifest: $SVC is NOT byte-identical)"
  exit 1
fi
echo "[pack] post-run manifest: every touched file is byte-identical"

if [ "$MUTATION_FAILURES" -ne 0 ]; then
  echo "RESULT: FAIL (${MUTATION_FAILURES} mutation leg(s) failed)"
  exit 1
fi
echo "RESULT: PASS (2 mutation legs RED→GREEN, sha-verified restores, $(elapsed_s)s)"
exit 0
