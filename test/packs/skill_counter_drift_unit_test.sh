#!/usr/bin/env bash
# Test Pack: skill_counter_drift_unit_test — A4 (skill-system-revival Phase 1)
#
# TWO ARMS, ONE JSON CONTRACT:
#   * GAUGE arm     — the within-identity-key divergence gauge (plan §18 row 11b)
#   * COUNTER arm   — the transition-based total_applied bump (row 11) and the
#                     audited, reversible, dry-run-first reconcile (plan §5 A4
#                     step (ii))
#
# THE GAUGE AND WHY IT WAS REDEFINED.  `skills.name` is INTENTIONALLY
# non-unique: every project gets its own `bank_clone` generation-0 row, so
# one name spans many `project_id`s BY DESIGN.  The retired gauge counted
# names-with-diverging-content over all skills — it measured a NON-DEFECT
# and could never reach 0 without destroying the per-project clone model.
# The honest gauge measures divergence WITHIN one identity key
# `(name, project_id)`: `count(DISTINCT content) > 1` per key.
#
# THE COUNTER DRIFT.  `record_feedback(applied=True)` used to bump
# `total_applied` on EVERY call, including repeat filings against a row that
# was already `applied=True`.  The ledger counts ROWS, the counter counted
# CALLS ⇒ +1 counter / +0 ledger per duplicate filing = the observed +480.
# The bump is now transition-based (`applied_bool is True and not
# prev_applied`); the accumulated gap is repaired by
# `scripts/audit/skill_counter_reconcile.py` (dry-run by default, `--apply`
# writes + receipt, `--revert` restores).
#
# BASELINE (census-carried; the pack environment cannot re-measure
# production — Phase 1 promotes nothing and writes no live DB):
#   dev (ensemble_dev, 15 rows)   : 0
#   prod (ensemble_prod, 630 rows): 32, caused by 27 stranded A/B pairs
#   → the 0 target is BLOCKED-ON the A/B resolution decision (Ruling E)
#
# FILE SET
#   tests/unit/test_skill_counter_drift_gauge.py                     (gauge arm)
#   tests/unit/test_skill_counter_transition_and_reconcile.py        (counter arm)
#
# MUTATION MATRIX (phase 2), planted on PRODUCTION source:
#   M-A4a — remove the transition guard (`applied_bool is True and not
#           prev_applied` -> `applied_bool is True`) ⇒ repeat filing bumps
#           again ⇒ the repeat-filing pin REDs
#   M-A4b — neuter the transition READ (`_prev_applied = False` on the
#           existing-row path) ⇒ same pin REDs (a distinct defect: the guard
#           is present but sees nothing)
#   M-A4c — restore the retired GLOBAL-name gauge (group by name only) ⇒ RED
#   M-A4d — make the within-identity-key filter a no-op (drop the
#           project_id grouping term) ⇒ RED
#   M-A4e — make the reconcile dry-run-only (refuse to write even under
#           --apply) ⇒ the apply/convergence pins RED
#   M-A4f — make the reconcile write an OFF-BY-ONE value (ledger + 1) ⇒ the
#           convergence pins RED
#   M-A4g — skip the >= 24 h leg (the evaluator always reports "converged")
#           ⇒ the close-readings pin REDs
#
# FENCES: per-variable env scrub, worktree-local interpreter, dual-layer
# timeout, sha-verified PRISTINE restore, VACUOUS-GREEN guard, no PG, no
# live DB (the reconcile pins use a scratch SQLite under pytest's tmp_path).
# EXIT-CODE CONTRACT: 0 PASS / 1 FAIL / 124 TIMEOUT.
set -uo pipefail

PACK_START=${SECONDS:-0}
BUDGET_S="${SKILL_COUNTER_DRIFT_BUDGET_S:-600}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="$REPO_ROOT/.venv/bin/python"
LOG_DIR="${SKILL_COUNTER_DRIFT_LOG_DIR:-/tmp/skill-counter-drift-gate}"
mkdir -p "$LOG_DIR"

SCRUB=(env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
       -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT
       -u ENSEMBLE_SELF_ENV -u ENSEMBLE_CONFIG -u ENSEMBLE_DATA_DIR)

GAUGE_PIN="tests/unit/test_skill_counter_drift_gauge.py"
COUNTER_PIN="tests/unit/test_skill_counter_transition_and_reconcile.py"
REPO_PY="daemon/repositories/skill/repository.py"
SVC_PY="daemon/services/skill_metrics_service.py"
SCRIPT_PY="scripts/audit/skill_counter_reconcile.py"

echo "=== Test Pack: skill_counter_drift_unit_test (GAUGE + COUNTER arms) ==="
echo "REPO_ROOT=$REPO_ROOT"

if [ ! -x "$PY" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — the worktree-local .venv/bin/python is absent)"
  exit 1
fi
for f in "$GAUGE_PIN" "$COUNTER_PIN" "$REPO_PY" "$SVC_PY" "$SCRIPT_PY"; do
  if [ ! -f "$f" ]; then
    echo "RESULT: FAIL (MISSING-ARTIFACT — $f)"
    exit 1
  fi
done

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
  "${SCRUB[@]}" "$PY" -m pytest "$GAUGE_PIN" "$COUNTER_PIN" -q --tb=line \
    -p no:randomly --override-ini="addopts=" > "$1" 2>&1
}

# ── phase 1: the pins ──────────────────────────────────────────────────────
echo "--- phase 1: the gauge + counter pins ---"
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
  echo "RESULT: FAIL (phase 1 pins rc=${PHASE1_RC})"
  exit 1
fi
check_budget "after phase 1"

if [ "${SKILL_COUNTER_DRIFT_MUTATIONS:-1}" = "0" ]; then
  echo "RESULT: PASS (pin arms only — mutation matrix skipped by request)"
  exit 0
fi

# ── phase 2: the mutation matrix ───────────────────────────────────────────
echo "--- phase 2: the mutation matrix (plant → RED → sha-restore → GREEN) ---"
MUTABLE_FILES=("$REPO_PY" "$SVC_PY" "$SCRIPT_PY")
PRISTINE="$(mktemp -d /tmp/skill-counter-drift-pristine-XXXXXX)"
CLEANED=0
restore_all() {
  [ "$CLEANED" = 1 ] && return 0
  CLEANED=1
  local f key
  for f in "${MUTABLE_FILES[@]}"; do
    key="$(printf '%s' "$f" | sha256sum | cut -d' ' -f1 | cut -c1-16)"
    [ -f "$PRISTINE/$key.content" ] || continue
    cp "$PRISTINE/$key.content" "$f"
  done
  rm -rf "$PRISTINE"
}
trap restore_all EXIT
trap 'restore_all; exit 143' TERM
trap 'restore_all; exit 130' INT

for f in "${MUTABLE_FILES[@]}"; do
  key="$(printf '%s' "$f" | sha256sum | cut -d' ' -f1 | cut -c1-16)"
  cp "$f" "$PRISTINE/$key.content"
  sha256sum "$f" | cut -d' ' -f1 > "$PRISTINE/$key.sha"
  echo "[pack] pristine: $f sha=$(cat "$PRISTINE/$key.sha")"
done

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
  local id="$1" path="$2" old="$3" new="$4"
  local key want sha_before sha_after log rc
  key="$(printf '%s' "$path" | sha256sum | cut -d' ' -f1 | cut -c1-16)"
  want="$(cat "$PRISTINE/$key.sha")"
  sha_before="$(sha256sum "$path" | cut -d' ' -f1)"
  if [ "$sha_before" != "$want" ]; then
    echo "  ${id}: FAIL (leg did not start pristine)"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    restore_all; CLEANED=0; trap restore_all EXIT
    return
  fi
  if ! plant "$path" "$old" "$new"; then
    echo "  ${id}: FAIL (plant error)"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    cp "$PRISTINE/$key.content" "$path"
    return
  fi
  log="$LOG_DIR/${id}-red.log"
  run_pin "$log"; rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  ${id}: FAIL (NON-DISCRIMINATING — the pins stayed GREEN with the defect planted)"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
  else
    echo "  ${id}: RED as intended (rc=${rc})"
    grep -E "^FAILED " "$log" | head -3 | sed 's/^/    /' || true
  fi
  cp "$PRISTINE/$key.content" "$path"
  sha_after="$(sha256sum "$path" | cut -d' ' -f1)"
  if [ "$sha_after" != "$want" ]; then
    echo "  ${id}: FAIL (restore NOT sha-identical: ${sha_after} != ${want})"
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

# ── counter arm ────────────────────────────────────────────────────────────
# M-A4a — remove the transition guard (the call-counting leak).
leg "M-A4a" "$SVC_PY" \
  '        if applied_bool is True and not prev_applied:' \
  '        if applied_bool is True:'
# M-A4b — keep the guard but neuter the transition read.
leg "M-A4b" "$SVC_PY" \
  '            _prev_applied = bool(getattr(record, "applied", False))' \
  '            _prev_applied = False'
# ── gauge arm ──────────────────────────────────────────────────────────────
# M-A4d — drop the project_id grouping term (global-name gauge again).
leg "M-A4d" "$REPO_PY" \
  '            .group_by(Skill.name, Skill.project_id)' \
  '            .group_by(Skill.name)'
# M-A4c — restore the retired GLOBAL-name gauge shape.
leg "M-A4c" "$REPO_PY" \
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
# ── reconcile arm ──────────────────────────────────────────────────────────
# M-A4e — the reconcile refuses to write even under --apply (dry-run only).
leg "M-A4e" "$SCRIPT_PY" \
  '    if not apply:' \
  '    if True:  # MUTATION: dry-run only'
# M-A4f — the reconcile writes an OFF-BY-ONE value.
leg "M-A4f" "$SCRIPT_PY" \
  '        {"after": int(row["after"]), "skill_id": row["skill_id"]},' \
  '        {"after": int(row["after"]) + 1, "skill_id": row["skill_id"]},'
# M-A4g — skip the >= 24 h leg (always claim convergence).
leg "M-A4g" "$SCRIPT_PY" \
  '    if elapsed < CONVERGENCE_MIN_ELAPSED:' \
  '    if False:  # MUTATION: the elapsed gate is skipped'

# Final manifest check.
for f in "${MUTABLE_FILES[@]}"; do
  key="$(printf '%s' "$f" | sha256sum | cut -d' ' -f1 | cut -c1-16)"
  want="$(cat "$PRISTINE/$key.sha")"
  got="$(sha256sum "$f" | cut -d' ' -f1)"
  if [ "$got" != "$want" ]; then
    echo "RESULT: FAIL (post-run manifest: $f is NOT byte-identical)"
    exit 1
  fi
done
echo "[pack] post-run manifest: every touched file is byte-identical"

if [ "$MUTATION_FAILURES" -ne 0 ]; then
  echo "RESULT: FAIL (${MUTATION_FAILURES} mutation leg(s) failed)"
  exit 1
fi
echo "RESULT: PASS (7 mutation legs RED→GREEN, sha-verified restores, $(elapsed_s)s)"
exit 0
