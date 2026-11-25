#!/usr/bin/env bash
# =============================================================================
# Test Pack: journal_defer_kind_nonterminal_unit_test
#
# Lane: test-pins on `latest` (the promote-handback landing tree) — the
#       A1 hygiene rename (`halt` -> `gate_defer`) must NOT make the new
#       journal kind TERMINAL in the daemon.
#
# WHAT IT PINS
#   The standing POK contract `journal-defer-kind-must-be-nonterminal`:
#     "A journal event kind in `_TERMINAL_EVENTS` makes `reconcile_pending_op`
#      close the pending promote op and release the arm. Transient deferral
#      kinds must stay outside it; `sweep_deferred` is the in-repo precedent."
#
#   Leg 1 — the pins: `tests/unit/tools/test_journal_defer_kind_nonterminal_pins.py`
#     (3 nodes): the CLASSIFICATION (`gate_defer` absent from
#     `_TERMINAL_EVENTS`, with the non-vacuity controls), the BEHAVIOUR (a
#     `gate_defer` row does not close the pending promote op and leaves the
#     journal byte-identical) and the DISCRIMINATING CONTROL (the same
#     fixture with a `commit` row DOES close — the behaviour leg cannot pass
#     vacuously).
#   Leg 2 — the MUTATION PROOF (non-vacuity, the other direction): the
#     production module is copied into a scratch dir, `"gate_defer"` is
#     inserted into the `_TERMINAL_EVENTS` tuple IN THE COPY, and the pins
#     are re-run against the copy: they MUST go RED.  The production module
#     is NEVER mutated.
#   Leg 3 — the byte-identity proof: sha256 of the production module before
#     and after the pack is IDENTICAL.
#
# CONTRACT (house shape)
#   - Dual-layer timeout: inner `timeout 280`; the caller wraps with 300.
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob in `env -u` is a SILENT
#     NO-OP — bash does not glob env-var names).
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY (never
#     `env -u` on it: stripping the ambient guard makes the boot sweep
#     SIGTERM/SIGKILL live processes).
#   - VACUOUS-VERDICT GUARD: leg 1's pytest summary and the mutation
#     proof's RED verdict must both appear; a missing summary line is a FAIL.
#   - The scratch copy lives under `mktemp -d` and is removed on exit; the
#     live install is NEVER touched.
#   - Output: header, leg bodies, `RESULT: PASS|FAIL|TIMEOUT`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
# =============================================================================
set -u

PACK_NAME="journal_defer_kind_nonterminal_unit_test"

echo "=== Test Pack: ${PACK_NAME} ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL (cannot cd to ${PROJECT_DIR})"; exit 1; }

PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3 || true)"
fi
if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
    echo "FATAL: no python3 interpreter found (no .venv/bin/python, none on PATH)" >&2
    echo "RESULT: FAIL"
    exit 1
fi

PINS="tests/unit/tools/test_journal_defer_kind_nonterminal_pins.py"
SUBJECT="daemon/tools/upgrade_journal.py"

for f in "$PINS" "$SUBJECT"; do
    if [ ! -f "$f" ]; then
        echo "FATAL: required file missing: $f" >&2
        echo "RESULT: FAIL"
        exit 1
    fi
done

SCRATCH="$(mktemp -d /tmp/journal-defer-pin.XXXXXX)"
cleanup() { rm -rf "$SCRATCH"; }
trap cleanup EXIT

SUBJECT_SHA_BEFORE="$(sha256sum "$SUBJECT" | cut -d' ' -f1)"

# ── the per-variable env scrub (never a POSTGRES_* glob) ──────────────────
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u OPENAI_API_KEY
)

# ── LEG 1: the pins against the PRODUCTION module ─────────────────────────
echo
echo "--- leg 1: the pins — gate_defer stays NON-terminal ---"
LEG1_LOG="$SCRATCH/leg1.log"
timeout 280 env "${SCRUB[@]}" \
    ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
    ENSEMBLE_SELF_ENV=dev \
    PYTHONDONTWRITEBYTECODE=1 \
    "$PYTHON_BIN" -m pytest "$PINS" -q -p no:randomly --timeout=120 >"$LEG1_LOG" 2>&1
LEG1_RC=$?
cat "$LEG1_LOG"
LEG1_SUMMARY="$(grep -E '^[0-9]+ passed' "$LEG1_LOG" | tail -1 || true)"

# ── LEG 2: the MUTATION PROOF — the pins MUST go RED ─────────────────────
echo
echo "--- leg 2: mutation proof — gate_defer added to a SCRATCH COPY ---"
MUTATED="$SCRATCH/upgrade_journal.py"
cp "$SUBJECT" "$MUTATED"
"$PYTHON_BIN" - "$MUTATED" <<'PYEOF'
import sys

path = sys.argv[1]
src = open(path, encoding="utf-8").read()
needle = '    "serving_green_commit",\n)'
if needle not in src:
    sys.stderr.write("MUTATION ANCHOR NOT FOUND — the tuple shape moved\n")
    raise SystemExit(3)
src = src.replace(needle, '    "serving_green_commit",\n    "gate_defer",\n)')
open(path, "w", encoding="utf-8").write(src)
print("mutated scratch copy written: gate_defer added to _TERMINAL_EVENTS")
PYEOF
MUTATE_RC=$?

LEG2_LOG="$SCRATCH/leg2.log"
if [ "$MUTATE_RC" -eq 0 ]; then
    timeout 280 env "${SCRUB[@]}" \
        ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
        ENSEMBLE_SELF_ENV=dev \
        PYTHONDONTWRITEBYTECODE=1 \
        UPGRADE_JOURNAL_MODULE_PATH="$MUTATED" \
        "$PYTHON_BIN" -m pytest "$PINS" -q -p no:randomly --timeout=120 >"$LEG2_LOG" 2>&1
    LEG2_RC=$?
    cat "$LEG2_LOG"
else
    LEG2_RC=99
    echo "MUTATION SETUP FAILED (rc=$MUTATE_RC)" | tee "$LEG2_LOG"
fi
LEG2_SUMMARY="$(grep -E '^[0-9]+ failed' "$LEG2_LOG" | tail -1 || true)"

# ── LEG 3: the production module is byte-identical ───────────────────────
echo
echo "--- leg 3: byte-identity — the production module is untouched ---"
SUBJECT_SHA_AFTER="$(sha256sum "$SUBJECT" | cut -d' ' -f1)"
echo "sha256 before: ${SUBJECT_SHA_BEFORE}"
echo "sha256 after : ${SUBJECT_SHA_AFTER}"
if [ "$SUBJECT_SHA_BEFORE" = "$SUBJECT_SHA_AFTER" ]; then
    SHA_VERDICT="identical"
else
    SHA_VERDICT="CHANGED"
fi

echo
echo "== summary =="
echo "leg1 rc=${LEG1_RC} (${LEG1_SUMMARY:-<no summary line>})  leg2 rc=${LEG2_RC} (${LEG2_SUMMARY:-<no summary line>})  subject sha256: ${SHA_VERDICT}"

PASS=0
FAIL=0
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); }
_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  x $1"
    printf 'FAIL: %s\n' "$1" >&2
}

if [ "$LEG1_RC" -eq 124 ] || [ "$LEG2_RC" -eq 124 ]; then
    echo "RESULT: TIMEOUT"
    exit 124
fi

if [ "$LEG1_RC" -eq 0 ] && [ -n "$LEG1_SUMMARY" ]; then
    _pass
else
    _fail "leg 1 — the pins pass against the production module (rc=${LEG1_RC})"
fi

if [ "$LEG2_RC" -ne 0 ] && [ -n "$LEG2_SUMMARY" ]; then
    _pass
else
    _fail "leg 2 — the pins go RED against the mutated copy (rc=${LEG2_RC})"
fi

if [ "$SHA_VERDICT" = "identical" ]; then
    _pass
else
    _fail "leg 3 — the production module is byte-identical after the pack"
fi

echo "PASS=${PASS} FAIL=${FAIL}"
if [ "$FAIL" -ne 0 ]; then
    echo "RESULT: FAIL"
    exit 1
fi

echo "RESULT: PASS"
exit 0
