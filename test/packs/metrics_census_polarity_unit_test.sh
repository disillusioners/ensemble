#!/usr/bin/env bash
# Test Pack: metrics_census_polarity_unit_test — A2 (skill-system-revival Phase 1)
#
# THE DEFECT THIS PACK GUARDS (R-ii).  `CensusSampler.gated` is
# documented as "whether the census sampler's DB work is currently gated
# OFF", but it returned `self._gate()` — the ENABLED sense.  Two
# downstream negations in the provider netted out to: F6 reported
# `dark / census_gated` exactly while the census was running and
# publishing 15 live series.
#
# THE FIX.  `metrics_census.py`: `return not self._gate()`.  One line, at
# the FACT OWNER (not the consumer, not the label layer).  It adds NO
# check site — the property still calls the existing `_gate()` — so the
# behavioural check-site census over `daemon/` stays exactly 2.
#
# FILE SET
#   tests/unit/metrics/test_metrics_census_polarity.py   (the pin)
#
# MUTATION MATRIX (phase 2), each leg planted on PRODUCTION source:
#   M-A2a — revert the negation (`return self._gate()`) ⇒ F6 dark again
#   M-A2b — "fix" it at the CONSUMER (`provider.py` uses bool(gated)) ⇒
#           the resolution-layer pins red
#   M-A2c — swap the R-i/R-ii labels back in availability.py ⇒ the
#           label-agreement pin reds
#   (M-A2d — delete the R-tokens from the disclosure — is BLOCKED: the
#    disclosure doc is outside this lane's edit fence.  Reported, not
#    pinned; the existing five absence-pins already cover the shipped
#    strings.)
#
# FENCES (house): per-variable env scrub, worktree-local interpreter,
# dual-layer timeout, sha-verified restore, VACUOUS-GREEN guard.
# EXIT-CODE CONTRACT: 0 PASS / 1 FAIL / 124 TIMEOUT.
#
# Usage:
#   bash test/packs/metrics_census_polarity_unit_test.sh
#   METRICS_CENSUS_POLARITY_MUTATIONS=0 bash test/packs/...   # pin only
set -uo pipefail

PACK_START=${SECONDS:-0}
BUDGET_S="${METRICS_CENSUS_POLARITY_BUDGET_S:-300}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="$REPO_ROOT/.venv/bin/python"
LOG_DIR="${METRICS_CENSUS_POLARITY_LOG_DIR:-/tmp/metrics-census-polarity-gate}"
mkdir -p "$LOG_DIR"

SCRUB=(env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
       -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT
       -u ENSEMBLE_SELF_ENV -u ENSEMBLE_CONFIG -u ENSEMBLE_DATA_DIR)

PIN="tests/unit/metrics/test_metrics_census_polarity.py"
CENSUS="daemon/services/metrics_census.py"
AVAIL="daemon/services/metrics_query/availability.py"
PROVIDER="daemon/services/metrics_query/provider.py"

echo "=== Test Pack: metrics_census_polarity_unit_test ==="
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
echo "--- phase 1: the pin ---"
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

if [ "${METRICS_CENSUS_POLARITY_MUTATIONS:-1}" = "0" ]; then
  echo "RESULT: PASS (pin only — mutation matrix skipped by request)"
  exit 0
fi

# ── phase 2: the mutation matrix ───────────────────────────────────────────
echo "--- phase 2: the mutation matrix (plant → RED → sha-restore → GREEN) ---"

MUTABLE_FILES=("$CENSUS" "$AVAIL" "$PROVIDER")
PRISTINE="$(mktemp -d /tmp/metrics-census-polarity-pristine-XXXXXX)"
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

# Pristine capture (self-contained, sha-verified — see the A1 pack's note
# on why a dirty-tree REFUSAL is not used in this uncommitted lane).
for f in "${MUTABLE_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "RESULT: FAIL (phase 2 pre-check: mutable file missing: $f)"
    exit 1
  fi
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
    echo "  ${id}: FAIL (NON-DISCRIMINATING — the pin stayed GREEN with the defect planted)"
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

# M-A2a — revert the negation at the fact owner.
leg "M-A2a" "$CENSUS" \
  '        return not self._gate()' \
  '        return self._gate()'
# M-A2b — the CONSUMER-side "fix": provider trusts `gated` as ENABLED.
leg "M-A2b" "$PROVIDER" \
  '            return not bool(self._census.gated)' \
  '            return bool(self._census.gated)'
# M-A2c — swap the labels back (the polarity re-labelled R-i).
leg "M-A2c" "$AVAIL" \
  '        # The POLARITY of `census.gated` itself is R-ii — the defect in' \
  '        # The POLARITY of `census.gated` itself is R-i — the defect in'

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
echo "RESULT: PASS (3 mutation legs RED→GREEN, sha-verified restores, $(elapsed_s)s)"
exit 0
