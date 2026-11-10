#!/usr/bin/env bash
# Test Pack: metrics_capture_active_wiring_unit_test — A1 (skill-system-revival Phase 1)
#
# THE DEFECT THIS PACK GUARDS.  RegistryMetricsQueryProvider carries a
# `capture_active` fact whose own contract is "whether the wedge capture
# is active", but daemon/api.py never passed it — the field defaulted to
# False forever and every wedge-capture-idiom family (F1/F3) reported
# `dark / capture_off` even while publishing live series.  That is R-i.
#
# THE FIX.  The fact is the WEDGE-CAPTURE PROCESS FACT
# (`app.state.wedge_capture_watchdog is not None`), resolved PER READ
# (late-binding: the watchdog is created ~665 lines AFTER the provider).
#
# FILE SET
#   tests/unit/metrics/test_metrics_capture_active_wiring.py  (the pin)
#
# MUTATION MATRIX (phase 2) — each leg plants its defect on PRODUCTION
# source, the pack must go RED, the file is restored SHA-IDENTICALLY
# (from an in-memory pristine capture taken before the first plant), and
# the leg must go GREEN again:
#   M-A1a — drop the `capture_active=` kwarg at the api.py wiring site
#   M-A1b — hard-code False where the resolver is stored
#   M-A1c — make the closure read a switch instead of the watchdog
#   M-A1d — make the resolver a boot snapshot (evaluate once)
#
# FENCES (house): per-variable env scrub (the `env -u POSTGRES_*` glob
# is a SILENT NO-OP), worktree-local `.venv/bin/python`, dual-layer
# timeout, dirty pre-check refusal, sha-verified restore.
#
# EXIT-CODE CONTRACT: 0 PASS / 1 FAIL / 124 TIMEOUT.
# GREEN-AT-ZERO IS A FAILURE (the VACUOUS-GREEN guard).
#
# Usage:
#   bash test/packs/metrics_capture_active_wiring_unit_test.sh
#   METRICS_CAPTURE_ACTIVE_MUTATIONS=0 bash test/packs/...   # pin only
#   METRICS_CAPTURE_ACTIVE_BUDGET_S=300 bash test/packs/...  # budget
set -uo pipefail

PACK_START=${SECONDS:-0}
BUDGET_S="${METRICS_CAPTURE_ACTIVE_BUDGET_S:-300}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="$REPO_ROOT/.venv/bin/python"
LOG_DIR="${METRICS_CAPTURE_ACTIVE_LOG_DIR:-/tmp/metrics-capture-active-gate}"
mkdir -p "$LOG_DIR"

SCRUB=(env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
       -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT
       -u ENSEMBLE_SELF_ENV -u ENSEMBLE_CONFIG -u ENSEMBLE_DATA_DIR)

PIN="tests/unit/metrics/test_metrics_capture_active_wiring.py"
PROVIDER="daemon/services/metrics_query/provider.py"
API="daemon/api.py"

echo "=== Test Pack: metrics_capture_active_wiring_unit_test ==="
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

if [ "${METRICS_CAPTURE_ACTIVE_MUTATIONS:-1}" = "0" ]; then
  echo "RESULT: PASS (pin only — mutation matrix skipped by request)"
  exit 0
fi

# ── phase 2: the mutation matrix ───────────────────────────────────────────
echo "--- phase 2: the mutation matrix (plant → RED → sha-restore → GREEN) ---"

MUTABLE_FILES=("$API" "$PROVIDER")
PRISTINE="$(mktemp -d /tmp/metrics-capture-active-pristine-XXXXXX)"
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

# Pristine capture.  NOTE (differs from the house dirty-tree REFUSAL): this
# lane's own fix is UNCOMMITTED by design (no commit point is owned by the
# implementer here), so a clean-tree pre-check would make the pack
# unrunnable.  The restore discipline is instead SELF-CONTAINED and
# sha-verified: the pristine bytes + sha256 are captured from the working
# tree BEFORE the first plant, every leg restores to PRISTINE (never to
# leg-start), the EXIT/TERM/INT trap restores on any exit path, and the
# final manifest asserts every mutable file is byte-identical.
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

# M-A1a — drop the kwarg at the wiring site.
leg "M-A1a" "$API" \
  '            capture_active=lambda: (
                getattr(app.state, "wedge_capture_watchdog", None) is not None
            ),
' \
  ''
# M-A1b — hard-code False where the resolver is stored.
leg "M-A1b" "$PROVIDER" \
  '        self._capture_active_resolver: Callable[[], bool] = (
            capture_active
            if callable(capture_active)
            else (lambda: bool(capture_active))
        )' \
  '        self._capture_active_resolver: Callable[[], bool] = (lambda: False)'
# M-A1c — the closure reads a SWITCH instead of the watchdog.
leg "M-A1c" "$API" \
  '                getattr(app.state, "wedge_capture_watchdog", None) is not None' \
  '                bool(__import__("os").environ.get("ENSEMBLE_WEDGE_CAPTURE_ENABLED"))'
# M-A1d — the resolver becomes a BOOT SNAPSHOT (evaluated once).
leg "M-A1d" "$PROVIDER" \
  '            return bool(self._capture_active_resolver())' \
  '            _snap = getattr(self, "_capture_active_boot", None)
            if _snap is None:
                _snap = bool(self._capture_active_resolver())
                self._capture_active_boot = _snap
            return _snap'

# Final manifest check: every mutable file byte-identical to pristine.
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
echo "RESULT: PASS (4 mutation legs RED→GREEN, sha-verified restores, $(elapsed_s)s)"
exit 0
