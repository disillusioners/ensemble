#!/usr/bin/env bash
# =============================================================================
# Test Pack: shell_continuation_comment_lint_test
#
# Lane: fix/promote-handback-h3-continuation (base `8d306f2e3`) — the C-1
#       parse-class fix in scripts/upgrade/lib.sh.
#
# WHAT IT PINS
#   1. `tests/shell_continuation_scan.py` — the TREE-WIDE lint for the bash
#      "continuation followed by a comment" parse class: a line that ends with
#      a backslash followed by a comment-only line silently TRUNCATES the
#      continued command's argument list (bash removes a backslash-newline
#      during TOKENIZATION, before comment recognition).  The construct stays
#      syntactically legal, so `bash -n` returns 0 and every diff-based gate
#      walks past it.
#   2. `tests/test_handback_start_timeout_journal_row_test.sh` — the RUNTIME
#      leg: the hand-back start-timeout journal row must land with its full
#      detail, and the executor must not abort with an unbound-variable
#      error.  The leg carries its own non-vacuity proof: the same
#      assertions are run against a reconstructed PRE-FIX shape and MUST
#      fail there.
#
# CONTRACT (house shape)
#   - Dual-layer timeout: inner `timeout 280`; the caller wraps with 300.
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob in `env -u` is a SILENT
#     NO-OP — bash does not glob env-var names).
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY (never
#     `env -u` on it: stripping the ambient guard makes the boot sweep
#     SIGTERM/SIGKILL live processes).
#   - The runtime leg NEVER touches the live install, the `current` symlink,
#     state.json or the production journal: it uses a `mktemp -d` FAKE
#     install with an escape guard.
#   - VACUOUS-VERDICT GUARD: the lint's `RESULT: PASS` line and the runtime
#     leg's `PASS=<n> FAIL=0` summary must both appear; a missing summary
#     line is a FAIL.
#   - Output: header, leg bodies, `RESULT: PASS|FAIL|TIMEOUT`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
# =============================================================================
set -u

PACK_NAME="shell_continuation_comment_lint_test"

echo "=== Test Pack: ${PACK_NAME} ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL (cannot cd to ${PROJECT_DIR})"; exit 1; }

PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    # Fall back to the PATH interpreter (the precedent set by
    # tests/packs/upgrade_refusal_vocabulary_census_test.sh — a worktree
    # without a local .venv is normal).
    PYTHON_BIN="$(command -v python3 || true)"
fi
if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
    echo "FATAL: no python3 interpreter found (no .venv/bin/python, none on PATH)" >&2
    echo "RESULT: FAIL"
    exit 1
fi

SCANNER="tests/shell_continuation_scan.py"
RUNTIME_LEG="tests/test_handback_start_timeout_journal_row_test.sh"

for f in "$SCANNER" "$RUNTIME_LEG"; do
    if [ ! -f "$f" ]; then
        echo "FATAL: required file missing: $f" >&2
        echo "RESULT: FAIL"
        exit 1
    fi
done

# ── the per-variable env scrub (never a POSTGRES_* glob) ──────────────────
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u OPENAI_API_KEY
)

# ── LEG 1: the tree-wide parse-class lint ─────────────────────────────────
echo
echo "--- leg 1: tree-wide lint — continuation followed by a comment ---"
LINT_LOG="/tmp/shell_cont_lint_$$.txt"
timeout 280 "$PYTHON_BIN" "$SCANNER" "$PROJECT_DIR" >"$LINT_LOG" 2>&1
LINT_RC=$?
cat "$LINT_LOG"
# VACUOUS-VERDICT GUARD — a lint that printed no verdict line proves nothing.
LINT_VERDICT="$(grep -E '^RESULT: ' "$LINT_LOG" | tail -1 || true)"
rm -f "$LINT_LOG"

# ── LEG 2: the runtime leg (the row must land; the executor must not die) ─
echo
echo "--- leg 2: runtime leg — hand-back start-timeout journal row ---"
RUNTIME_LOG="/tmp/shell_cont_runtime_$$.txt"
timeout 280 env "${SCRUB[@]}" \
    ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
    ENSEMBLE_SELF_ENV=dev \
    PYTHONDONTWRITEBYTECODE=1 \
    bash "$RUNTIME_LEG" >"$RUNTIME_LOG" 2>&1
RUNTIME_RC=$?
cat "$RUNTIME_LOG"
# VACUOUS-VERDICT GUARD — the runtime leg must print a summary and FAIL=0.
RUNTIME_SUMMARY="$(grep -E '^PASS=[0-9]+ FAIL=[0-9]+' "$RUNTIME_LOG" | tail -1 || true)"
rm -f "$RUNTIME_LOG"

echo
echo "== summary =="
echo "lint rc=${LINT_RC} (${LINT_VERDICT:-<no verdict line>})  runtime rc=${RUNTIME_RC}  runtime summary: ${RUNTIME_SUMMARY:-<none>}"

if [ "$LINT_RC" -eq 124 ] || [ "$RUNTIME_RC" -eq 124 ]; then
    echo "RESULT: TIMEOUT"
    exit 124
fi
if [ "$LINT_RC" -ne 0 ]; then
    echo "RESULT: FAIL (lint leg)"
    exit 1
fi
if [ -z "$LINT_VERDICT" ]; then
    echo "RESULT: FAIL (lint leg printed no verdict line — vacuous verdict)"
    exit 1
fi
if [ "$RUNTIME_RC" -ne 0 ]; then
    echo "RESULT: FAIL (runtime leg)"
    exit 1
fi
if [ -z "$RUNTIME_SUMMARY" ]; then
    echo "RESULT: FAIL (runtime leg printed no summary — vacuous verdict)"
    exit 1
fi
case "$RUNTIME_SUMMARY" in
    *"FAIL=0"*) : ;;
    *)
        echo "RESULT: FAIL (runtime leg did not report FAIL=0)"
        exit 1
        ;;
esac

echo "RESULT: PASS"
exit 0
