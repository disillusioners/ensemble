#!/usr/bin/env bash
# Test Pack: skill_dual_column_deactivation_unit_test — the dual-column
# contract on the LIVE writer + the REST PATCH arm
#
# THE PROPERTY.  `skills.is_active` and `skills.status` are TWO DESYNCABLE
# COLUMNS (a real production row carries `(False, 'ab_testing')`).  A
# single-column write leaves a half-dead row.  The LIVE writer is
# `daemon/services/skill_deactivation.py::write_skill_pair` (the E4
# mechanism's sanctioned writer: ONE repository call, BOTH columns, and the
# PRIOR pair carried by the receipt so the write is exactly reversible).
#
# SCOPE CHANGE (tidier M1, 2026-11-10).  This pack used to carry a SECOND,
# ORPHANED dual-column writer — `SkillRepository.deactivate_with_receipt`
# plus its two value-object types (`DeactivationResult` /
# `SkillDeactivationRefused`) — with ZERO production callers.  It was
# DELETED (one writer only).  Its invariants were RE-POINTED:
#   * the pair-moves-as-a-pair + the A/B refusal + the prior-pair/reversal
#     invariants are covered by the E4 lane's own pins
#     (`tests/unit/test_skill_stale_deactivation.py`: the sanctioned writer
#     sets both columns / one repository call / no E4 write path writes one
#     column without the other / a real attempted flip is REFUSED / the
#     receipt enumerates the refusal with the blocking test id / the receipt
#     entry carries the prior pair / reactivate restores the exact prior
#     pair), mutation-proved by THAT lane's pack
#     (`skill_stale_deactivation_unit_test`, M-E4h single-column, M-E4c
#     canonical-pair reversal);
#   * the TWO invariants E4 does NOT cover (no THIRD column moves; a missing
#     row returns None) are pinned HERE, on the live writer, in
#     `tests/unit/test_skill_dual_column_live_writer.py`.
#
# FILE SET
#   tests/unit/test_skill_dual_column_live_writer.py   (the re-pointed pins)
#   tests/unit/routers/test_skill_patch_dual_column.py   (I5 — the REST PATCH arm)
#
# MUTATION MATRIX (phase 2):
#   M-E4w-d — drop the `status` write inside `SkillRepository.update`
#             ⇒ the pair no longer moves as a pair ⇒ the live-writer pin REDs
#   M-E4w-e — make a missing row return something other than None
#             ⇒ the missing-row pin REDs
#   M-I5-a  — restore the REST handler's pop-and-translate shape (drop the
#             `is_active` write at `daemon/routers/skills.py`) ⇒ the REST
#             both-columns pins RED (the status-only translation still passes,
#             so the pin catches the DESYNC, not the translation)
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

PIN="tests/unit/test_skill_dual_column_live_writer.py"
REST_PIN="tests/unit/routers/test_skill_patch_dual_column.py"
ROUTER_PY="daemon/routers/skills.py"
REPO_PY="daemon/repositories/skill/repository.py"

echo "=== Test Pack: skill_dual_column_deactivation_unit_test ==="
echo "REPO_ROOT=$REPO_ROOT"

[ -x "$PY" ] || { echo "RESULT: FAIL (MISSING-INTERPRETER)"; exit 1; }
for f in "$PIN" "$REST_PIN" "$REPO_PY" "$ROUTER_PY"; do
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
  timeout 240 "${SCRUB[@]}" "$PY" -m pytest "$PIN" "$REST_PIN" -q --tb=line \
    -p no:randomly --override-ini="addopts=" > "$1" 2>&1
}

run_writer_pin() {
  timeout 240 "${SCRUB[@]}" "$PY" -m pytest "$PIN" -q --tb=line \
    -p no:randomly --override-ini="addopts=" > "$1" 2>&1
}

run_rest_pin() {
  timeout 240 "${SCRUB[@]}" "$PY" -m pytest "$REST_PIN" -q --tb=line \
    -p no:randomly --override-ini="addopts=" > "$1" 2>&1
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
  [ -f "$PRISTINE/router-content" ] && cp "$PRISTINE/router-content" "$ROUTER_PY"
  rm -rf "$PRISTINE"
}
trap restore_all EXIT
trap 'restore_all; exit 143' TERM
trap 'restore_all; exit 130' INT

cp "$REPO_PY" "$PRISTINE/content"
WANT="$(sha256sum "$REPO_PY" | cut -d' ' -f1)"
cp "$ROUTER_PY" "$PRISTINE/router-content"
ROUTER_WANT="$(sha256sum "$ROUTER_PY" | cut -d' ' -f1)"
echo "[pack] pristine: $REPO_PY sha=$WANT"
echo "[pack] pristine: $ROUTER_PY sha=$ROUTER_WANT"

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
# ── M-E4w-d / M-E4w-e — the LIVE writer's re-pointed invariants ────────────
# Both plants land in `SkillRepository.update` — the primitive the live
# writer calls — and are the ONLY production surface these two pins
# observe.  The pair-moves-as-a-pair and the missing-row contracts are the
# two invariants the E4 lane's own pin file does NOT cover.
writer_leg() {
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
  run_writer_pin "$log"; rc=$?
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
  run_writer_pin "$log"; rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "  ${id}: FAIL (post-restore leg not GREEN, rc=${rc})"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
  else
    echo "  ${id}: GREEN after sha-verified restore"
  fi
  check_budget "after ${id}"
}

# M-E4w-d — the `status` half of the pair never moves (the desync defect,
# one layer below the writer: the writer asks for BOTH columns, the
# primitive drops one).
writer_leg "M-E4w-d" \
  '                setattr(skill, key, value)' \
  '                if key == "status":
                    continue  # MUTATION: the pair'"'"'s status column never moves
                setattr(skill, key, value)'

# M-E4w-e — a missing row returns something other than None.
writer_leg "M-E4w-e" \
  '                logger.warning(
                    f"Skill not found for update: id={skill_id}"
                )
                return None' \
  '                logger.warning(
                    f"Skill not found for update: id={skill_id}"
                )
                return None or _now_iso()  # MUTATION: a missing row is not None'

# ── M-I5-a — the REST PATCH desync leg ────────────────────────────────────
# Restore the handler's ORIGINAL pop-and-translate shape: it writes only
# `status`, so the row's `is_active` column never moves and the PATCH
# manufactures the MIRROR desync (True, 'inactive'). The status-only
# assertion still passes — the pin catches the DESYNC, not the translation.
rest_leg() {
  local id="$1" old="$2" new="$3" log rc sha_before sha_after
  sha_before="$(sha256sum "$ROUTER_PY" | cut -d' ' -f1)"
  if [ "$sha_before" != "$ROUTER_WANT" ]; then
    echo "  ${id}: FAIL (leg did not start pristine)"; MUTATION_FAILURES=$((MUTATION_FAILURES + 1)); return
  fi
  if ! plant "$ROUTER_PY" "$old" "$new"; then
    echo "  ${id}: FAIL (plant error)"; MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    cp "$PRISTINE/router-content" "$ROUTER_PY"; return
  fi
  log="$LOG_DIR/${id}-red.log"
  run_rest_pin "$log"; rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  ${id}: FAIL (NON-DISCRIMINATING — the REST pin stayed GREEN with the desync restored)"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
  else
    echo "  ${id}: RED as intended (rc=${rc})"
    grep -E "^FAILED " "$log" | head -3 | sed 's/^/    /' || true
  fi
  cp "$PRISTINE/router-content" "$ROUTER_PY"
  sha_after="$(sha256sum "$ROUTER_PY" | cut -d' ' -f1)"
  if [ "$sha_after" != "$ROUTER_WANT" ]; then
    echo "  ${id}: FAIL (restore NOT sha-identical)"; MUTATION_FAILURES=$((MUTATION_FAILURES + 1)); return
  fi
  log="$LOG_DIR/${id}-green.log"
  run_rest_pin "$log"; rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "  ${id}: FAIL (post-restore leg not GREEN, rc=${rc})"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
  else
    echo "  ${id}: GREEN after sha-verified restore"
  fi
  check_budget "after ${id}"
}

rest_leg "M-I5-a" \
  '        fields["is_active"] = is_active_value
        fields["status"] = "active" if is_active_value else "inactive"' \
  '        fields.pop("is_active", None)
        fields["status"] = "active" if is_active_value else "inactive"'

sha="$(sha256sum "$REPO_PY" | cut -d' ' -f1)"
if [ "$sha" != "$WANT" ]; then
  echo "RESULT: FAIL (post-run manifest: $REPO_PY is NOT byte-identical)"
  exit 1
fi
sha="$(sha256sum "$ROUTER_PY" | cut -d' ' -f1)"
if [ "$sha" != "$ROUTER_WANT" ]; then
  echo "RESULT: FAIL (post-run manifest: $ROUTER_PY is NOT byte-identical)"
  exit 1
fi
echo "[pack] post-run manifest: every touched file is byte-identical"

if [ "$MUTATION_FAILURES" -ne 0 ]; then
  echo "RESULT: FAIL (${MUTATION_FAILURES} mutation leg(s) failed)"
  exit 1
fi
echo "RESULT: PASS (3 mutation legs RED→GREEN, sha-verified restores, $(elapsed_s)s)"
exit 0
