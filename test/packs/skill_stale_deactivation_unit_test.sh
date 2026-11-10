#!/usr/bin/env bash
# Test Pack: skill_stale_deactivation_unit_test — E4 (skill-system-revival Phase 1)
#
# WHAT IT PACKS
#   The E4 DEACTIVATION MECHANISM (plan §9 E4): the dual-column writer, the
#   A/B-strand refusal, the reversible receipt, the process-once suppression,
#   the pair-resolver, the operator script and the E4.3 injection-side
#   defence.
#
# THE LEVER (why two columns matter)
#   `skills.is_active` (bool) + `skills.status` (varchar) CAN DESYNC — the
#   Phase-0 census: (True,'active')=538 · (True,'ab_testing')=52 ·
#   (False,'inactive')=39 · (False,'ab_testing')=1.  The last group is a
#   half-dead row.  ANY deactivation MUST set BOTH columns; the house writer
#   (`SkillRepository.deactivate`) does exactly that.
#
# FILE SET
#   tests/unit/test_skill_stale_deactivation.py   (the E4 pins)
#
# MUTATION MATRIX — every leg proven RED→GREEN with a sha-verified restore:
#   M-E4a — make the reconciliation a NO-OP (no rows are read) ⇒ the
#           refusal-receipt discriminator pin REDs (the receipt no longer
#           shows R1 REFUSED-BY-GUARD).
#   M-E4b — make it DELETE instead of deactivate ⇒ the no-hard-delete pin
#           REDs (the row is gone).
#   M-E4c — make the reversal write a CANONICAL pair instead of the recorded
#           BEFORE pair ⇒ the reversal pin REDs (the prior `ab_testing` pair
#           is not restored).
#   M-E4d — remove the E4.3 injection filter ⇒ the "stale cannot be
#           injected" pins RED (an R1-shaped row is served).
#   M-E4e — make `--apply` available without a dry-run ⇒ the dry-run-default
#           pin REDs (the default run writes a row + a receipt).
#   M-E4f — drop a row from the receipt ⇒ the receipt-completeness pin REDs.
#   M-E4g — make the SECOND run change state (the rule re-matches an
#           already-deactivated row AND the suppression is dropped) ⇒ the
#           idempotence pin REDs.
#   M-E4i — drop the suppression alone ⇒ the "apply → reactivate → the next
#           boot does NOT re-deactivate" pin REDs (the reversal is undone).
#   M-E4j — make the refusal list APPEND again ⇒ the receipt-hygiene pin REDs
#           (the receipt grows without bound across boots).
#   M-E4h — make the writer write ONLY `is_active` ⇒ the single-column pin
#           REDs (the dual-column contract is broken).
#   M-E4k — flip the resolver's default to ON ⇒ the "inert by default"
#           pin REDs (the deactivator is constructed and the receipt is written).
#   M-H1a — the manifest docstring names a CLI flag the operator script does
#           NOT register ⇒ the "the documented interface matches the argparse
#           surface" pin REDs.
#   M-H1b — a ``--allow-declared-artifact`` CLI escape flag is added to the
#           operator script ⇒ the "no CLI escape flag" pin REDs.
#
# FENCES: per-variable env scrub, worktree-local interpreter, dual-layer
# timeout, sha-verified PRISTINE restore, VACUOUS-GREEN guard, no PG, no
# live DB (every pin runs on a scratch SQLite under pytest's tmp_path).
# EXIT-CODE CONTRACT: 0 PASS / 1 FAIL / 124 TIMEOUT.
set -uo pipefail

PACK_START=${SECONDS:-0}
BUDGET_S="${SKILL_STALE_DEACTIVATION_BUDGET_S:-900}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="$REPO_ROOT/.venv/bin/python"
LOG_DIR="${SKILL_STALE_DEACTIVATION_LOG_DIR:-/tmp/skill-stale-deactivation-gate}"
mkdir -p "$LOG_DIR"

# NOTE: the scrub is PER-VARIABLE on purpose — `env -u POSTGRES_*` is a
# silent NO-OP (bash does not glob env names), so the ambient
# POSTGRES_DB=ensemble_prod would survive a "fenced" run.
SCRUB=(env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
       -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT
       -u ENSEMBLE_SELF_ENV -u ENSEMBLE_CONFIG -u ENSEMBLE_DATA_DIR)

PIN="tests/unit/test_skill_stale_deactivation.py"
E4_PY="daemon/services/skill_deactivation.py"
INJ_PY="daemon/services/skill_injection_service.py"
SCRIPT_PY="scripts/audit/skill_stale_deactivate.py"
MANIFEST_PY="daemon/services/skill_deactivation_manifest.py"

echo "=== Test Pack: skill_stale_deactivation_unit_test (E4) ==="
echo "REPO_ROOT=$REPO_ROOT"

if [ ! -x "$PY" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — the worktree-local .venv/bin/python is absent)"
  exit 1
fi
for f in "$PIN" "$E4_PY" "$INJ_PY" "$SCRIPT_PY" "$MANIFEST_PY"; do
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
  "${SCRUB[@]}" "$PY" -m pytest "$PIN" -q --tb=line \
    -p no:randomly --override-ini="addopts=" > "$1" 2>&1
}

# ── phase 1: the pins ──────────────────────────────────────────────────────
echo "--- phase 1: the E4 pins ---"
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

if [ "${SKILL_STALE_DEACTIVATION_MUTATIONS:-1}" = "0" ]; then
  echo "RESULT: PASS (pin arms only — mutation matrix skipped by request)"
  exit 0
fi

# ── phase 2: the mutation matrix ───────────────────────────────────────────
echo "--- phase 2: the mutation matrix (plant → RED → sha-restore → GREEN) ---"
MUTABLE_FILES=("$E4_PY" "$INJ_PY" "$SCRIPT_PY" "$MANIFEST_PY")
PRISTINE="$(mktemp -d /tmp/skill-stale-deactivation-pristine-XXXXXX)"
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
# A plant may carry SEVERAL edits in one file, separated by the @@E4@@
# sentinel (each edit must match exactly once at the time it is applied).
olds = old.split("@@E4@@")
news = new.split("@@E4@@")
if len(olds) != len(news):
    sys.stderr.write(f"PLANT-ERROR: {path}: sentinel mismatch\n")
    sys.exit(3)
for one_old, one_new in zip(olds, news):
    n = s.count(one_old)
    if n != 1:
        sys.stderr.write(
            f"PLANT-ERROR: {path}: expected 1 occurrence, found {n}\n"
        )
        sys.exit(3)
    s = s.replace(one_old, one_new)
p.write_text(s)
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

# M-E4a — the reconciliation becomes a NO-OP (nothing is read).
leg "M-E4a" "$E4_PY" \
  '        rows = _all_skills(self._skill_repo)' \
  '        rows = []  # MUTATION: the reconciliation is a no-op'

# M-E4b — it DELETES instead of deactivating.
leg "M-E4b" "$E4_PY" \
  '                deactivate_skill(self._skill_repo, entry.skill_id)' \
  '                self._skill_repo.delete(entry.skill_id)  # MUTATION'

# M-E4c — the reversal writes a CANONICAL pair, not the recorded BEFORE pair.
leg "M-E4c" "$E4_PY" \
  '                restore_skill_pair(self._skill_repo, entry.skill_id, entry.before)' \
  '                write_skill_pair(self._skill_repo, entry.skill_id, is_active=True, status="active")  # MUTATION'

# M-E4d — the E4.3 injection filter is removed.
leg "M-E4d" "$INJ_PY" \
  '        injected, low_match = await self._filter_stale_candidates(
            injected, low_match, project_id
        )' \
  '        injected, low_match = injected, low_match  # MUTATION: the filter is removed'

# M-E4e — `--apply` is available without a dry-run.
leg "M-E4e" "$SCRIPT_PY" \
  '    report = deactivator.reconcile(dry_run=not args.apply)' \
  '    report = deactivator.reconcile(dry_run=False)  # MUTATION'

# M-E4f — a row is dropped from the receipt.
leg "M-E4f" "$E4_PY" \
  '        receipt.entries.extend(touched)' \
  '        receipt.entries.extend(touched[:-1])  # MUTATION: a row is dropped'

# M-E4g — the second run CHANGES STATE: the rule re-matches an
# already-deactivated row AND the suppression is dropped ⇒ the idempotence
# pin REDs (the second run touches the row again).
leg "M-E4g" "$E4_PY" \
  '            if not bool(getattr(row, "is_active", False)):
                continue
@@E4@@
            if (skill_id, RULE_R3) in suppression:' \
  '            pass  # MUTATION: the rule also matches an already-deactivated row
@@E4@@
            if False:  # MUTATION: the suppression is dropped'

# M-E4i — the suppression alone is dropped ⇒ the "apply → reactivate → the
# next boot does NOT re-deactivate" pin REDs (the reversal is undone).
leg "M-E4i" "$E4_PY" \
  '            if (skill_id, RULE_R3) in suppression:' \
  '            if False:  # MUTATION: the suppression is dropped'

# M-E4j — the refusal list is APPENDED again ⇒ the receipt grows without
# bound across boots ⇒ the receipt-hygiene pin REDs.
leg "M-E4j" "$E4_PY" \
  '        receipt.refusals = list(refusals)' \
  '        receipt.refusals.extend(refusals)  # MUTATION: the receipt grows'

# M-E4h — the writer writes ONLY `is_active` (the dual-column contract breaks).
leg "M-E4h" "$E4_PY" \
  '    updated = skill_repo.update(
        skill_id,
        is_active=bool(is_active),
        status=str(status),
    )' \
  '    updated = skill_repo.update(
        skill_id,
        is_active=bool(is_active),
    )  # MUTATION: a single-column write'

# M-E4k — flip the resolver's default to ON ⇒ the "inert by default" pin REDs.
leg "M-E4k" "$E4_PY" \
  '    return _resolve_switch(SKILL_STALE_DEACTIVATION_KILL_SWITCH_ENV, False)' \
  '    return _resolve_switch(SKILL_STALE_DEACTIVATION_KILL_SWITCH_ENV, True)  # MUTATION: default ON'

# M-H1a — the manifest docstring names a CLI flag the script does NOT
# register (the H1 defect: a docstring that lies about the interface).
leg "M-H1a" "$MANIFEST_PY" \
  '  touches a declared artifact. (Adding one would contradict the pinned
' \
  '  touches a declared artifact. (Adding one would contradict the pinned
  MUTATION: the operator passes the explicit
  ``--allow-declared-artifact`` override.)
'

# M-H1b — a CLI escape flag is added to the operator script (the escape the
# LEADER RULING forbids: REWORD, DO NOT ADD THE FLAG).
leg "M-H1b" "$SCRIPT_PY" \
  '    parser.add_argument(
        "--apply",
' \
  '    parser.add_argument(
        "--allow-declared-artifact",
        action="store_true",
        help="MUTATION: a CLI escape flag the leader ruling forbids.",
    )
    parser.add_argument(
        "--apply",
'

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
echo "RESULT: PASS (13 mutation legs RED→GREEN, sha-verified restores, $(elapsed_s)s)"
exit 0
