#!/usr/bin/env bash
# Test Pack: skill_counter_drift_unit_test — A4 (skill-system-revival Phase 1)
#
# SCOPE: the GAUGE REDEFINITION only (plan §18 row 11b).  The counter-bump
# fix (row 11) and the reconciliation-script legs (M-A4e/f/g) are outside
# this lane's fence and outside A4's dispatched scope — REPORTED, not
# implemented here.  Hence this pack pins the GAUGE legs only (M-A4c,
# M-A4d), exactly as the dispatch scoped it.
#
# THE GAUGE AND WHY IT WAS REDEFINED.  `skills.name` is INTENTIONALLY
# non-unique: every project gets its own `bank_clone` generation-0 row, so
# one name spans many `project_id`s BY DESIGN.  The retired gauge counted
# names-with-diverging-content over all skills — it measured a NON-DEFECT
# and could never reach 0 without destroying the per-project clone model.
# The honest gauge measures divergence WITHIN one identity key
# `(name, project_id)`: `count(DISTINCT content) > 1` per key.
#
# BASELINE (census-carried; the pack environment cannot re-measure
# production — Phase 1 promotes nothing and writes no live DB):
#   dev (ensemble_dev, 15 rows)   : 0
#   prod (ensemble_prod, 630 rows): 32, caused by 27 stranded A/B pairs
#   → the 0 target is BLOCKED-ON the A/B resolution decision (Ruling E)
#
# FILE SET
#   tests/unit/test_skill_counter_drift_gauge.py   (the gauge pins)
#
# MUTATION MATRIX (phase 2), planted on PRODUCTION source:
#   M-A4c — restore the retired GLOBAL-name gauge (group by name only) ⇒ RED
#   M-A4d — make the within-identity-key filter a no-op (drop the
#           project_id grouping term) ⇒ RED
#
# FENCES: per-variable env scrub, worktree-local interpreter, dual-layer
# timeout, sha-verified restore, VACUOUS-GREEN guard.
# EXIT-CODE CONTRACT: 0 PASS / 1 FAIL / 124 TIMEOUT.
set -uo pipefail

PACK_START=${SECONDS:-0}
BUDGET_S="${SKILL_COUNTER_DRIFT_BUDGET_S:-300}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="$REPO_ROOT/.venv/bin/python"
LOG_DIR="${SKILL_COUNTER_DRIFT_LOG_DIR:-/tmp/skill-counter-drift-gate}"
mkdir -p "$LOG_DIR"

SCRUB=(env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
       -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT
       -u ENSEMBLE_SELF_ENV -u ENSEMBLE_CONFIG -u ENSEMBLE_DATA_DIR)

PIN="tests/unit/test_skill_counter_drift_gauge.py"
REPO_PY="daemon/repositories/skill/repository.py"

echo "=== Test Pack: skill_counter_drift_unit_test (GAUGE PINS ONLY) ==="
echo "REPO_ROOT=$REPO_ROOT"

if [ ! -x "$PY" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — the worktree-local .venv/bin/python is absent)"
  exit 1
fi
if [ ! -f "$PIN" ]; then
  echo "RESULT: FAIL (MISSING-PIN — $PIN)"
  exit 1
fi

elapsed_s() { echo $(( ${SECONDS:-0} - PACK_START )); }
check_budget() {
  local elapsed
  elapsed="$(elapsed_s)"
  if [ "$elapsed" -gt "$BUDGET_S" ]; then
    echo "RESULT: TIMEOUT (internal timer: wall ${elapsed}s > budget ${BUDGET_S}s ${1:-})"
    exit 124
  fi
}

run_pin() {
  "${SCRUB[@]}" "$PY" -m pytest "$PIN" -q --tb=line -p no:randomly \
    --override-ini="addopts=" > "$1" 2>&1
}

# ── phase 1: the pin ───────────────────────────────────────────────────────
echo "--- phase 1: the gauge pins ---"
PHASE1_LOG="$LOG_DIR/phase1.log"
run_pin "$PHASE1_LOG"
PHASE1_RC=$?
tail -3 "$PHASE1_LOG" | sed 's/^/  /'
grep -E "^FAILED " "$PHASE1_LOG" | sed 's/^/  /' || true
if ! grep -qE "^[1-9][0-9]* passed" "$PHASE1_LOG"; then
  echo "RESULT: FAIL (VACUOUS — the summary does not carry 'N passed' with N >= 1)"
  exit 1
fi
if [ "$PHASE1_RC" -ne 0 ]; then
  echo "RESULT: FAIL (phase 1 pin rc=${PHASE1_RC})"
  exit 1
fi
check_budget "after phase 1"

if [ "${SKILL_COUNTER_DRIFT_MUTATIONS:-1}" = "0" ]; then
  echo "RESULT: PASS (pin only — mutation matrix skipped by request)"
  exit 0
fi

# ── phase 2: the mutation matrix ───────────────────────────────────────────
echo "--- phase 2: the mutation matrix (plant → RED → sha-restore → GREEN) ---"
PRISTINE="$(mktemp -d /tmp/skill-counter-drift-pristine-XXXXXX)"
CLEANED=0
restore_all() {
  [ "$CLEANED" = 1 ] && return 0
  CLEANED=1
  if [ -f "$PRISTINE/content" ]; then
    cp "$PRISTINE/content" "$REPO_PY"
  fi
  rm -rf "$PRISTINE"
}
trap restore_all EXIT
trap 'restore_all; exit 143' TERM
trap 'restore_all; exit 130' INT

cp "$REPO_PY" "$PRISTINE/content"
WANT="$(sha256sum "$REPO_PY" | cut -d' ' -f1)"
echo "$WANT" > "$PRISTINE/sha"
echo "[pack] pristine: $REPO_PY sha=$WANT"

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
  local id="$1" old="$2" new="$3"
  local log rc sha
  sha="$(sha256sum "$REPO_PY" | cut -d' ' -f1)"
  if [ "$sha" != "$WANT" ]; then
    echo "  ${id}: FAIL (leg did not start pristine)"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    return
  fi
  if ! plant "$REPO_PY" "$old" "$new"; then
    echo "  ${id}: FAIL (plant error)"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    cp "$PRISTINE/content" "$REPO_PY"
    return
  fi
  log="$LOG_DIR/${id}-red.log"
  run_pin "$log"; rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  ${id}: FAIL (NON-DISCRIMINATING — the gauge stayed GREEN with the defect planted)"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
  else
    echo "  ${id}: RED as intended (rc=${rc})"
    grep -E "^FAILED " "$log" | head -3 | sed 's/^/    /' || true
  fi
  cp "$PRISTINE/content" "$REPO_PY"
  sha="$(sha256sum "$REPO_PY" | cut -d' ' -f1)"
  if [ "$sha" != "$WANT" ]; then
    echo "  ${id}: FAIL (restore NOT sha-identical: ${sha} != ${WANT})"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    return
  fi
  log="$LOG_DIR/${id}-green.log"
  run_pin "$log"; rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "  ${id}: FAIL (post-restore leg did not return to GREEN, rc=${rc})"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
  else
    echo "  ${id}: GREEN after sha-verified restore"
  fi
  check_budget "after ${id}"
}

# M-A4d — drop the project_id grouping term (global-name gauge again).
leg "M-A4d" \
  '            .group_by(Skill.name, Skill.project_id)' \
  '            .group_by(Skill.name)'
# M-A4c — restore the retired GLOBAL-name gauge shape (divergence across
# projects counted, plus the group key reduced to the bare name).
leg "M-A4c" \
  '            select(
                Skill.name,
                Skill.project_id,
                distinct_contents.label("distinct_contents"),
                rows_in_key.label("rows_in_key"),
            )' \
  '            select(
                Skill.name,
                distinct_contents.label("distinct_contents"),
                rows_in_key.label("rows_in_key"),
            )'

sha="$(sha256sum "$REPO_PY" | cut -d' ' -f1)"
if [ "$sha" != "$WANT" ]; then
  echo "RESULT: FAIL (post-run manifest: $REPO_PY is NOT byte-identical)"
  exit 1
fi
echo "[pack] post-run manifest: every touched file is byte-identical"

if [ "$MUTATION_FAILURES" -ne 0 ]; then
  echo "RESULT: FAIL (${MUTATION_FAILURES} mutation leg(s) failed)"
  exit 1
fi
echo "RESULT: PASS (2 gauge mutation legs RED→GREEN, sha-verified restores, $(elapsed_s)s)"
exit 0
