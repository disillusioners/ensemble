#!/usr/bin/env bash
# Test Pack: skill_dual_column_deactivation_unit_test — I4 / E4 repository writer
#
# THE PROPERTY.  `skills.is_active` and `skills.status` are TWO DESYNCABLE
# COLUMNS (a real production row carries `(False, 'ab_testing')`).  A
# single-column write leaves a half-dead row.  `SkillRepository.
# deactivate_with_receipt()` writes BOTH in ONE assignment, surfaces the PRIOR
# `(is_active, status)` pair for the caller's receipt (exact reversal), and
# REFUSES (typed) a row guarded by an OPEN `skill_ab_tests` row — the guard
# resolved by the repository itself, so an external caller passes only a
# skill id.
#
# FILE SET
#   tests/unit/test_skill_dual_column_deactivation.py   (the pin)
#
# MUTATION MATRIX (phase 2), planted on daemon/repositories/skill/repository.py
# (the A3 gate region is NOT touched by any leg — the two regions stay separate):
#   M-E4w-a — write only ONE column ⇒ the dual-column pins RED
#   M-E4w-b — drop the A/B guard ⇒ the refusal pins RED
#   M-E4w-c — stop surfacing the PRIOR pair (return the post-write pair) ⇒ the
#             reversibility pins RED
#
# FENCES: per-variable env scrub, worktree-local interpreter, dual-layer
# timeout, sha-verified PRISTINE restore, VACUOUS-GREEN guard.
# EXIT-CODE CONTRACT: 0 PASS / 1 FAIL / 124 TIMEOUT.
set -uo pipefail

PACK_START=${SECONDS:-0}
BUDGET_S="${SKILL_DUAL_COLUMN_BUDGET_S:-300}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="$REPO_ROOT/.venv/bin/python"
LOG_DIR="${SKILL_DUAL_COLUMN_LOG_DIR:-/tmp/skill-dual-column-gate}"
mkdir -p "$LOG_DIR"

SCRUB=(env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
       -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT
       -u ENSEMBLE_SELF_ENV -u ENSEMBLE_CONFIG -u ENSEMBLE_DATA_DIR)

PIN="tests/unit/test_skill_dual_column_deactivation.py"
REPO_PY="daemon/repositories/skill/repository.py"

echo "=== Test Pack: skill_dual_column_deactivation_unit_test ==="
echo "REPO_ROOT=$REPO_ROOT"

[ -x "$PY" ] || { echo "RESULT: FAIL (MISSING-INTERPRETER)"; exit 1; }
for f in "$PIN" "$REPO_PY"; do
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

if [ "${SKILL_DUAL_COLUMN_MUTATIONS:-1}" = "0" ]; then
  echo "RESULT: PASS (pin only — mutation matrix skipped by request)"
  exit 0
fi

echo "--- phase 2: the mutation matrix (plant → RED → sha-restore → GREEN) ---"
PRISTINE="$(mktemp -d /tmp/skill-dual-column-pristine-XXXXXX)"
CLEANED=0
restore_all() {
  [ "$CLEANED" = 1 ] && return 0
  CLEANED=1
  [ -f "$PRISTINE/content" ] && cp "$PRISTINE/content" "$REPO_PY"
  rm -rf "$PRISTINE"
}
trap restore_all EXIT
trap 'restore_all; exit 143' TERM
trap 'restore_all; exit 130' INT

cp "$REPO_PY" "$PRISTINE/content"
WANT="$(sha256sum "$REPO_PY" | cut -d' ' -f1)"
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
  local id="$1" old="$2" new="$3" log rc sha_before sha_after
  sha_before="$(sha256sum "$REPO_PY" | cut -d' ' -f1)"
  if [ "$sha_before" != "$WANT" ]; then
    echo "  ${id}: FAIL (leg did not start pristine)"; MUTATION_FAILURES=$((MUTATION_FAILURES + 1)); return
  fi
  if ! plant "$REPO_PY" "$old" "$new"; then
    echo "  ${id}: FAIL (plant error)"; MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    cp "$PRISTINE/content" "$REPO_PY"; return
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
  cp "$PRISTINE/content" "$REPO_PY"
  sha_after="$(sha256sum "$REPO_PY" | cut -d' ' -f1)"
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

# M-E4w-a — write only ONE column (the desync defect).
leg "M-E4w-a" \
  '            skill.is_active = False
            skill.status = "inactive"' \
  '            skill.is_active = False'

# M-E4w-b — drop the A/B guard entirely.
leg "M-E4w-b" \
  '            blocking = self._open_ab_test_blocking(session, skill)
            if blocking is not None:
                raise SkillDeactivationRefused(
                    skill_id=skill_id,
                    ab_test_group=blocking,
                )
' \
  ''

# M-E4w-c — stop surfacing the PRIOR pair (report the POST-write state).
leg "M-E4w-c" \
  '            prior = DeactivationResult(
                skill_id=skill_id,
                row=skill,
                prior_is_active=bool(skill.is_active),
                prior_status=str(skill.status or ""),
            )' \
  '            prior = DeactivationResult(
                skill_id=skill_id,
                row=skill,
                prior_is_active=False,
                prior_status="inactive",
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
echo "RESULT: PASS (3 mutation legs RED→GREEN, sha-verified restores, $(elapsed_s)s)"
exit 0
