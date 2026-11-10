#!/usr/bin/env bash
# Test Pack: skill_capture_killswitch_unit_test — ENSEMBLE_SKILL_CAPTURE_ENABLED
# kill-switch suite + ON-pin regression set + the A3 CAPTURE-ATTRIBUTION SEAM arm.
#
# Purpose: Validate the ENSEMBLE_SKILL_CAPTURE_ENABLED kill-switch lane and
# the skill-system-revival Phase 1 A3 seam (the capture-attribution
# predicate):
#   - tests/unit/test_skill_capture_killswitch.py — the dedicated kill-switch
#     suite (default-OFF short-circuit semantics across the capture gates).
#   - The 4 pre-existing ON-pin suites that carry autouse fixtures
#     (`_enable_skill_capture_killswitch` → setenv("ENSEMBLE_SKILL_CAPTURE_ENABLED", "1"))
#     pinning the flag ON so legacy capture-flow expectations keep holding.
#   - tests/unit/test_skill_capture_attribution_seam.py — A3 (skill-system-
#     revival Phase 1): the genuinely-evolved predicate
#     (`selected AND task_succeeded AND feedback_applied IS NOT NULL`), the
#     census boundary + timestamp, the population caveat, and the kept-but-
#     re-documented old predicate.
#
# File set:
#   tests/unit/test_skill_capture_killswitch.py             (kill-switch suite)
#   tests/integration/test_skill_capture.py                 (ON-pin: class-scoped autouse ×2)
#   tests/integration/test_skill_cross_phase_flow_c.py      (ON-pin: module-level autouse)
#   tests/services/test_skill_job_dispatcher.py             (ON-pin: class-scoped autouse ×2)
#   tests/tools/test_skill_evolution_tools.py               (ON-pin: module-level autouse)
#   tests/unit/test_skill_capture_attribution_seam.py       (A3 seam — Phase 1)
#   tests/unit/test_skill_gate4_attribution_consumer.py     (A3 GATE-4 CONSUMER — Phase 1)
#   tests/unit/test_skill_capture_gate_twin_parity.py       (A3 TWIN PARITY — M-A3d, Phase 1)
#   tests/unit/test_skill_capture_killswitch_verification.py (F-8 — the independent kill-switch
#     verification suite; its stubs were RETARGETED at the A3 predicate, which is what makes
#     its "the gate never consults the retired predicate" assertion LIVE instead of vacuous)
#
# NOTE on addopts: repo default addopts = `-m 'not integration and not postgres'`
# would silently deselect the two integration-marked ON-pin files. This pack
# overrides addopts (precedent: skill_captured_regression_unit_test, PACKS.md
# row: mixed unit+integration pack) so ALL 6 files execute. Verified 0
# postgres-marked tests in the set → no live PG required.
#
# INTERPRETER (2026-11-09, skill-system-revival A3): the pack previously
# invoked `uv run python`, which requires `uv` on PATH — absent in the
# worktree shells this project uses (POK worktree-02), so the pack could
# not execute at all.  It now uses the worktree-local `.venv/bin/python`
# (the house fence every newer pack already uses; never the main
# checkout's stale interpreter) with a `uv` fallback for the older
# environments.
#
# MUTATION MATRIX (phase 2 — the A3 seam legs, planted on PRODUCTION
# source; each must go RED, then the file is restored SHA-IDENTICALLY and
# the seam file must go GREEN again):
#   M-A3a — drop the `feedback_applied IS NOT NULL` conjunct ⇒ the
#           no-evidence window rows count as genuinely evolved ⇒ RED.
#           (If this did not move the observed value the pin would be
#           VACUOUS and would not ship; it moves it.)
#   M-A3b — widen the predicate to `selected == TRUE` only ⇒ a
#           selected-but-never-fed-back instance counts ⇒ RED.
#   M-A3c — drop the population caveat (the UPPER BOUND / early-return
#           sentence) from the predicate's docstring ⇒ RED.
#   M-A3d — leave one of the TWO gate call sites on the old predicate:
#           BLOCKED — both call sites live in files outside this lane's
#           edit fence (reported, not pinned).
#
# Expected test count: 206 (49 + 12 + 12 + 22 + 23 + 18 + 8 + 13 + 49)
# Timeout: internal 240s per pytest leg; caller applies outer `timeout 900`.
set -uo pipefail

PACK_START=${SECONDS:-0}
BUDGET_S="${SKILL_CAPTURE_KILLSWITCH_BUDGET_S:-900}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: skill_capture_killswitch_unit_test ==="

cd "$PROJECT_DIR" || exit 1

# The DATA-DIR SANDBOX (E1/D lane's L7 finding, 2026-11-10).  The
# observability sink resolves `./data` CWD-RELATIVELY, so a test that
# drives a refusal seam without a sink destination writes into the
# CHECKOUT's gitignored `data/skill-capture-observability/` tree — invisible
# to git status, to the residue gate and to the staging gate.  Two of the
# files in THIS pack's set still have no per-test sink destination (they
# are not this lane's files), so the pack points the resolved data dir at a
# scratch dir OUTSIDE the checkout: the pack can no longer pollute the
# working tree, whoever owns the file.  (It is the SANCTIONED env knob —
# the first hop of the existing precedence chain — not a re-implementation.)
SANDBOX="$(mktemp -d /tmp/skill-capture-killswitch-data-XXXXXX)"
export ENSEMBLE_DATA_DIR="$SANDBOX"

if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  PYTEST=("$PROJECT_DIR/.venv/bin/python" -m pytest)
  PY="$PROJECT_DIR/.venv/bin/python"
elif command -v uv >/dev/null 2>&1; then
  PYTEST=(uv run python -m pytest)
  PY="uv run python"
else
  echo "RESULT: FAIL (MISSING-INTERPRETER — no worktree .venv/bin/python and no uv)"
  exit 1
fi

FILES=(
  tests/unit/test_skill_capture_killswitch.py
  tests/integration/test_skill_capture.py
  tests/integration/test_skill_cross_phase_flow_c.py
  tests/services/test_skill_job_dispatcher.py
  tests/tools/test_skill_evolution_tools.py
  tests/unit/test_skill_capture_attribution_seam.py
  tests/unit/test_skill_gate4_attribution_consumer.py
  tests/unit/test_skill_capture_gate_twin_parity.py
  tests/unit/test_skill_capture_killswitch_verification.py
)

SEAM="tests/unit/test_skill_capture_attribution_seam.py"
CONSUMER="tests/unit/test_skill_gate4_attribution_consumer.py"
PARITY="tests/unit/test_skill_capture_gate_twin_parity.py"
REPO_PY="daemon/repositories/skill/repository.py"
LOG_DIR="${SKILL_CAPTURE_KILLSWITCH_LOG_DIR:-/tmp/skill-capture-killswitch-gate}"
mkdir -p "$LOG_DIR"

elapsed_s() { echo $(( ${SECONDS:-0} - PACK_START )); }
check_budget() {
  local elapsed
  elapsed="$(elapsed_s)"
  if [ "$elapsed" -gt "$BUDGET_S" ]; then
    echo "RESULT: TIMEOUT (internal timer: wall ${elapsed}s > budget ${BUDGET_S}s ${1:-})"
    exit 124
  fi
}

# ── phase 1: the pack's node set ──────────────────────────────────────────
echo "--- phase 1: the kill-switch suite + ON-pin set + the A3 seam ---"
PHASE1_LOG="$LOG_DIR/phase1.log"
EXIT_CODE=0
timeout 240 "${PYTEST[@]}" "${FILES[@]}" -q --tb=short \
  --override-ini="addopts=" > "$PHASE1_LOG" 2>&1 || EXIT_CODE=$?
tail -3 "$PHASE1_LOG" | sed 's/^/  /'
grep -E "^FAILED " "$PHASE1_LOG" | sed 's/^/  /' || true
if [ "$EXIT_CODE" -eq 124 ]; then
  echo "RESULT: TIMEOUT (pytest leg)"
  exit 124
fi
if ! grep -qE "^[1-9][0-9]* passed" "$PHASE1_LOG"; then
  echo "RESULT: FAIL (VACUOUS — the summary does not carry 'N passed' with N >= 1)"
  exit 1
fi
if [ "$EXIT_CODE" -ne 0 ]; then
  echo "RESULT: FAIL (phase 1 rc=${EXIT_CODE})"
  exit 1
fi
check_budget "after phase 1"

if [ "${SKILL_CAPTURE_KILLSWITCH_MUTATIONS:-1}" = "0" ]; then
  echo "RESULT: PASS (pin set only — A3 mutation matrix skipped by request)"
  exit 0
fi

# ── phase 2: the A3 mutation matrix ───────────────────────────────────────
echo "--- phase 2: the A3 seam mutation matrix (plant → RED → sha-restore → GREEN) ---"
PRISTINE="$(mktemp -d /tmp/skill-capture-killswitch-pristine-XXXXXX)"
CLEANED=0
restore_all() {
  [ "$CLEANED" = 1 ] && return 0
  CLEANED=1
  for meta in "$PRISTINE"/*.meta; do
    [ -e "$meta" ] || continue
    cp "$PRISTINE/$(basename "$meta" .meta).content" "$(cat "$meta")"
  done
  rm -rf "$PRISTINE"
}
trap restore_all EXIT
trap 'restore_all; exit 143' TERM
trap 'restore_all; exit 130' INT

key_of() { printf '%s' "$1" | sha256sum | cut -d' ' -f1 | cut -c1-16; }
if [ ! -f "$REPO_PY" ]; then
  echo "RESULT: FAIL (phase 2 pre-check: mutable file missing: $REPO_PY)"
  exit 1
fi
K="$(key_of "$REPO_PY")"
cp "$REPO_PY" "$PRISTINE/$K.content"
printf '%s' "$REPO_PY" > "$PRISTINE/$K.meta"
sha256sum "$REPO_PY" | cut -d' ' -f1 > "$PRISTINE/$K.sha"
echo "[pack] pristine: $REPO_PY sha=$(cat "$PRISTINE/$K.sha")"
WANT="$(cat "$PRISTINE/$K.sha")"

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
    cp "$PRISTINE/$K.content" "$REPO_PY"
    return
  fi
  log="$LOG_DIR/${id}-red.log"
  timeout 240 "${PYTEST[@]}" "$SEAM" -q --tb=line -p no:randomly \
    --override-ini="addopts=" > "$log" 2>&1
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  ${id}: FAIL (NON-DISCRIMINATING — the seam stayed GREEN with the defect planted)"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
  else
    echo "  ${id}: RED as intended (rc=${rc})"
    grep -E "^FAILED " "$log" | head -3 | sed 's/^/    /' || true
  fi
  cp "$PRISTINE/$K.content" "$REPO_PY"
  sha="$(sha256sum "$REPO_PY" | cut -d' ' -f1)"
  if [ "$sha" != "$WANT" ]; then
    echo "  ${id}: FAIL (restore NOT sha-identical: ${sha} != ${WANT})"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    return
  fi
  log="$LOG_DIR/${id}-green.log"
  timeout 240 "${PYTEST[@]}" "$SEAM" -q --tb=line -p no:randomly \
    --override-ini="addopts=" > "$log" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "  ${id}: FAIL (post-restore leg did not return to GREEN, rc=${rc})"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
  else
    echo "  ${id}: GREEN after sha-verified restore"
  fi
  check_budget "after ${id}"
}

# M-A3a — drop the feedback-evidence conjunct.
leg "M-A3a" \
  '                .where(SkillUsageRecord.feedback_applied.is_not(None))
' \
  ''
# M-A3b — widen to `selected == TRUE` only (drop the success + evidence conjuncts).
leg "M-A3b" \
  '                .where(SkillUsageRecord.task_succeeded == True)  # noqa: E712
                .where(SkillUsageRecord.feedback_applied.is_not(None))
' \
  ''
# M-A3c — drop the population caveat sentence from the docstring.
leg "M-A3c" \
  '        NOTE (population caveat, load-bearing for any count derived
        from this predicate): the population this predicate can ever
        see is *"succeeded instances that had >=1 selected/injected
        usage row"* — ``SkillMetricsService.record_task_completion``
        early-returns before the capture gate when no skill was
        injected.  A row count is therefore an UPPER BOUND on capture
        candidates, never a capture forecast.
' \
  ''

# ── M-A3d-consumer — the GATE-4 CONSUMPTION leg ───────────────────────────
# The three legs above plant defects in the PREDICATE. This leg plants the
# defect the leader's extended fence exists for: Gate 4 falls back to the
# FEEDBACK-ONLY predicate (a repoint that was never wired, or a revert). The
# predicate pins stay green under it — only the CONSUMER pins see it, which is
# exactly why the consumer pin has to exist separately.
SVC_PY="daemon/services/skill_metrics_service.py"
consumer_leg() {
  local id="$1" path="$2" old="$3" new="$4" pinfile="$5"
  local key want sha_before sha_after log rc
  key="$(key_of "$path")"
  if [ ! -f "$PRISTINE/$key.content" ]; then
    cp "$path" "$PRISTINE/$key.content"
    printf '%s' "$path" > "$PRISTINE/$key.meta"
    sha256sum "$path" | cut -d' ' -f1 > "$PRISTINE/$key.sha"
    echo "[pack] pristine: $path sha=$(cat "$PRISTINE/$key.sha")"
  fi
  want="$(cat "$PRISTINE/$key.sha")"
  sha_before="$(sha256sum "$path" | cut -d' ' -f1)"
  if [ "$sha_before" != "$want" ]; then
    echo "  ${id}: FAIL (leg did not start pristine)"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    return
  fi
  if ! plant "$path" "$old" "$new"; then
    echo "  ${id}: FAIL (plant error)"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
    cp "$PRISTINE/$key.content" "$path"
    return
  fi
  log="$LOG_DIR/${id}-red.log"
  timeout 240 "${PYTEST[@]}" "$pinfile" -q --tb=line -p no:randomly \
    --override-ini="addopts=" > "$log" 2>&1
  rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "  ${id}: FAIL (NON-DISCRIMINATING — the consumer pin stayed GREEN with the repoint planted)"
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
  timeout 240 "${PYTEST[@]}" "$pinfile" -q --tb=line -p no:randomly \
    --override-ini="addopts=" > "$log" 2>&1
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "  ${id}: FAIL (post-restore leg did not return to GREEN, rc=${rc})"
    MUTATION_FAILURES=$((MUTATION_FAILURES + 1))
  else
    echo "  ${id}: GREEN after sha-verified restore"
  fi
  check_budget "after ${id}"
}

consumer_leg "M-A3d-consumer" "$SVC_PY" \
  '                self.usage_repo.has_genuinely_evolved_for_instance,' \
  '                self.usage_repo.has_applied_for_instance,' \
  "$CONSUMER"

# ── M-A3d-parity — the TWIN-PARITY leg ────────────────────────────────────
# Plant the defect on OUR OWN seam only: repointing `skill_metrics_service.py`
# back to the feedback-only predicate must red the parity pin. The plant is
# deliberately NOT placed in `skill_evolution_service.py` — a transient plant
# in a file another lane may commit into can revert their commit on restore
# (the house CONCURRENT-COMMIT hazard).
consumer_leg "M-A3d-parity" "$SVC_PY" \
  '                self.usage_repo.has_genuinely_evolved_for_instance,' \
  '                self.usage_repo.has_applied_for_instance,' \
  "$PARITY"

for meta in "$PRISTINE"/*.meta; do
  [ -e "$meta" ] || continue
  path="$(cat "$meta")"
  key="$(basename "$meta" .meta)"
  want="$(cat "$PRISTINE/$key.sha")"
  got="$(sha256sum "$path" | cut -d' ' -f1)"
  if [ "$got" != "$want" ]; then
    echo "RESULT: FAIL (post-run manifest: $path is NOT byte-identical)"
    exit 1
  fi
done
echo "[pack] post-run manifest: every touched file is byte-identical"

if [ "$MUTATION_FAILURES" -ne 0 ]; then
  echo "RESULT: FAIL (${MUTATION_FAILURES} mutation leg(s) failed)"
  exit 1
fi
echo "RESULT: PASS (5 A3 mutation legs RED→GREEN, sha-verified restores, $(elapsed_s)s)"
exit 0
