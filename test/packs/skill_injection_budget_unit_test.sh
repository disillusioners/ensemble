#!/usr/bin/env bash
# Test Pack: skill_injection_budget_unit_test — F (skill-system-revival Phase 1)
#
# WORKSTREAM F — THE INJECTION GATE.  Two arms:
#
#   * BUDGET arm   — the injection budget proved against a LIVE-SHAPED
#                     corpus (real skill names, real descriptions, real
#                     multi-paragraph bodies — NOT synthetic one-word skills).
#   * DEDUPE arm   — the identity dedupe at the injection boundary: at
#                     most ONE variant per (name, project_id) reaches an
#                     agent, chosen by a stated deterministic rule.
#   * ROSTER arm   — the roster manifest + the conditional staged payload.
#
# THE BUDGET.  `max_inject_skills` defaults to 2 (daemon/config.py —
# SkillEvolutionConfig.max_inject_skills) and the search returns
# {"injected": [...], "low_match": [...]}.  The arm proves the injected
# count never exceeds the cap AND that the cap BINDS, that low_match is
# present and bounded, and that the rendered [System Inject] block is
# HONEST (a skill that was not injected is never rendered as injected).
#
# THE DEDUPE (Ruling F).  Primary key = LINEAGE GENERATION (highest wins);
# tie-break = the skill id, lexicographically ascending.  NOT timestamps —
# two families carry a stale `updated_at` relative to their challenger's
# `created_at`, so a timestamp rule would pick WRONG.
#
# THE ROSTER.  The manifest (.agents/shared/planning/skill-system-revival/
# roster.md) is the authoritative per-axis list; the pin RE-DERIVES the
# census from agents/*/meta.json and diffs it (M-F4).  The staged payload
# is ARMED ONLY WHEN THE CAPTURE LANE IS ON (M-F5) — Phase 1 flips no
# agent and no meta.json is edited.
#
# FILE SET
#   tests/services/test_skill_injection_budget.py      (budget arm)
#   tests/services/test_skill_identity_dedupe.py        (dedupe arm)
#   tests/services/test_skill_roster_manifest.py        (roster arm)
#
# MUTATION MATRIX (phase 2), planted on PRODUCTION source:
#   M-F1 — raise the configured cap (max_inject_skills 2 -> 3) => the
#           budget pin REDs
#   M-F2 — drop the low_match bound (the next 3 -> the next 99) => the
#           low_match bound pin REDs
#   M-F4 — change the roster manifest without re-deriving it => the
#           manifest-diff pin REDs
#   M-F5 — make the staged payload apply unconditionally (drop the
#           capture-lane gate) => the conditional-payload pin REDs
#   M-F6 — remove the identity dedupe => both variants of one identity
#           are injected => the dedupe pin REDs
#
# M-F3 (remove E4.3's injection filter => an R1-shaped row is injected)
# is E4's leg on E4's file (their M-E4d).  This pack carries the
# CONSUMER-side assertion (an inactive row is never served); it SKIPS
# with a loud reason while E4.3's filter is absent from the tree.
#
# FENCES: per-variable env scrub (never `env -u POSTGRES_*` — bash does
# not glob env names), worktree-local interpreter, dual-layer timeout,
# sha-verified PRISTINE restore, VACUOUS-GREEN guard, no PG, no live DB,
# no network, no meta.json write.
#
# MUTATION TARGETS must be QUIET (no uncommitted foreign work) — the pack
# refuses to plant on a dirty tracked file rather than clobbering another
# lane's edit.
#
# EXIT-CODE CONTRACT: 0 PASS / 1 FAIL / 124 TIMEOUT.
set -uo pipefail

PACK_START=${SECONDS:-0}
BUDGET_S="${SKILL_INJECTION_BUDGET_BUDGET_S:-900}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="$REPO_ROOT/.venv/bin/python"
LOG_DIR="${SKILL_INJECTION_BUDGET_LOG_DIR:-/tmp/skill-injection-budget-gate}"
mkdir -p "$LOG_DIR"

SCRUB=(env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
       -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT
       -u ENSEMBLE_SELF_ENV -u ENSEMBLE_CONFIG -u ENSEMBLE_DATA_DIR
       -u SKILL_EVOLUTION_MAX_INJECT_SKILLS)

BUDGET_PIN="tests/services/test_skill_injection_budget.py"
DEDUPE_PIN="tests/services/test_skill_identity_dedupe.py"
ROSTER_PIN="tests/services/test_skill_roster_manifest.py"
PIN_FILES=("$BUDGET_PIN" "$DEDUPE_PIN" "$ROSTER_PIN")

CONFIG_PY="daemon/config.py"
SEARCH_PY="daemon/services/skill_search_service.py"
ROSTER_PY="daemon/services/skill_roster.py"
DEDUPE_PY="daemon/services/skill_identity_dedupe.py"
ROSTER_MD=".agents/shared/planning/skill-system-revival/roster.md"
MUTABLE_FILES=("$CONFIG_PY" "$SEARCH_PY" "$ROSTER_PY" "$DEDUPE_PY" "$ROSTER_MD")

echo "=== Test Pack: skill_injection_budget_unit_test (BUDGET + DEDUPE + ROSTER arms) ==="
echo "REPO_ROOT=$REPO_ROOT"

if [ ! -x "$PY" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — the worktree-local .venv/bin/python is absent)"
  exit 1
fi
for f in "${PIN_FILES[@]}" "${MUTABLE_FILES[@]}"; do
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
  "${SCRUB[@]}" "$PY" -m pytest "${PIN_FILES[@]}" -q --tb=line \
    -p no:randomly --override-ini="addopts=" > "$1" 2>&1
}

# ── phase 1: the pins ──────────────────────────────────────────────────────
echo "--- phase 1: the budget + dedupe + roster pins ---"
PHASE1_LOG="$LOG_DIR/phase1.log"
run_pin "$PHASE1_LOG"
PHASE1_RC=$?
tail -3 "$PHASE1_LOG" | sed 's/^/  /'
grep -E "^FAILED " "$PHASE1_LOG" | sed 's/^/  /' || true
if grep -qE "^SKIPPED" "$PHASE1_LOG"; then
  echo "  [note] the run carries skips:"
  grep -E "^SKIPPED" "$PHASE1_LOG" | sed 's/^/    /' | head -5
fi
if ! grep -qE "^[1-9][0-9]* passed" "$PHASE1_LOG"; then
  echo "RESULT: FAIL (VACUOUS — the summary does not carry 'N passed' with N >= 1)"
  exit 1
fi
if [ "$PHASE1_RC" -ne 0 ]; then
  echo "RESULT: FAIL (phase 1 pins rc=${PHASE1_RC})"
  exit 1
fi
check_budget "after phase 1"

if [ "${SKILL_INJECTION_BUDGET_MUTATIONS:-1}" = "0" ]; then
  echo "RESULT: PASS (pin arms only — mutation matrix skipped by request)"
  exit 0
fi

# ── the mutation targets must be quiet ─────────────────────────────────────
# Planting on a file another lane has UNCOMMITTED work in would restore
# stale bytes over their edit.  Refuse loudly instead of clobbering.
DIRTY_TARGETS=""
for f in "${MUTABLE_FILES[@]}"; do
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    if ! git diff --quiet -- "$f"; then
      DIRTY_TARGETS="${DIRTY_TARGETS} ${f}"
    fi
  fi
done
if [ -n "$DIRTY_TARGETS" ]; then
  echo "RESULT: FAIL (MUTATION TARGET DIRTY — another lane has uncommitted work in:${DIRTY_TARGETS})"
  echo "        re-run the pack when those files are quiet; the pack refuses to plant on them"
  exit 1
fi

# ── phase 2: the mutation matrix ───────────────────────────────────────────
echo "--- phase 2: the mutation matrix (plant -> RED -> sha-restore -> GREEN) ---"
PRISTINE="$(mktemp -d /tmp/skill-injection-budget-pristine-XXXXXX)"
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

# M-F1 — raise the configured cap: the budget pin must notice.
leg "M-F1" "$CONFIG_PY" \
  '    max_inject_skills: int = Field(default=2)' \
  '    max_inject_skills: int = Field(default=3)'

# M-F2 — drop the low_match bound: the bound pin must notice.
leg "M-F2" "$SEARCH_PY" \
  '                max_results : max_results + 3' \
  '                max_results : max_results + 99'

# M-F4 — change the roster manifest without re-deriving it.
leg "M-F4" "$ROSTER_MD" \
  '| `worker` | agent | yes | true | true | true | origin/latest census |' \
  '| `worker` | agent | yes | false | false | false | origin/latest census |'

# M-F5 — make the staged payload apply unconditionally.
leg "M-F5" "$ROSTER_PY" \
  '    if not capture_lane_on:' \
  '    if False:  # MUTATION: the capture-lane gate is removed'

# M-F6 — remove the identity dedupe.
leg "M-F6" "$DEDUPE_PY" \
  '    deduped = dedupe_injected_entries(list(injected))' \
  '    deduped = list(injected)  # MUTATION: the identity dedupe is removed'

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
echo "RESULT: PASS (5 mutation legs RED->GREEN, sha-verified restores, $(elapsed_s)s)"
exit 0
