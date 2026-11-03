#!/usr/bin/env bash
# Test Pack: metrics_read_mutation_legs
# The metrics-agent-tools Phase 5 gate — Tier 0 (the mutation matrix).
#
# The matrix plants every mutation INDEPENDENTLY on the production
# source, runs the intended pack, requires RED, restores the file
# BYTE-IDENTICALLY and requires GREEN again.
#
# The pack is a WRAPPER: the harness owns the restore discipline (the
# in-memory byte copies, the pre-run manifest, the recovery journal, the
# dirty-tree refusal and the NO-`git checkout --` rule).
#
# EXIT-CODE CONTRACT (the house shape):
#   0 = PASS · 1 = FAIL · 124 = TIMEOUT
#
# GREEN-AT-ZERO IS A FAILURE: the harness's summary must carry
# "RESULT: PASS" — a matrix that produced no leg output is not a pass.
#
# FENCES: the worktree-local `.venv/bin/python`, the per-variable env
# scrub, and a DIRTY-TREE REFUSAL (the matrix attributes a dirty tree to
# itself, so it refuses to start on one).
set -uo pipefail

PACK_START=${SECONDS:-0}
PACK_BUDGET_S=1800

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="$REPO_ROOT/.venv/bin/python"
HARNESS="$REPO_ROOT/test/packs/metrics_read_mutation_matrix_test.py"
LOG_DIR="${METRICS_READ_LOG_DIR:-/tmp/metrics-read-gate}"
mkdir -p "$LOG_DIR"

SCRUB=(env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
       -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT)

echo "=== Test Pack: metrics_read_mutation_legs ==="
echo "REPO_ROOT=$REPO_ROOT"

if [ ! -x "$PY" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — the worktree-local .venv/bin/python is absent)"
  exit 1
fi
if [ ! -f "$HARNESS" ]; then
  echo "RESULT: FAIL (MISSING-HARNESS — ${HARNESS})"
  exit 1
fi

LOG="$LOG_DIR/mutation_matrix.log"
set +e
timeout 1800 "${SCRUB[@]}" "$PY" "$HARNESS" > "$LOG" 2>&1
rc=$?
set -e

echo "mutation_matrix_EXIT=${rc}"
tail -5 "$LOG" | sed 's/^/  /'

if [ "$rc" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
fi
if ! grep -q "RESULT: PASS" "$LOG"; then
  echo "RESULT: FAIL (the matrix did not report PASS)"
  exit 1
fi
if ! grep -qE "OK \(RED via " "$LOG"; then
  echo "RESULT: FAIL (VACUOUS — no leg reported a RED)"
  exit 1
fi
# The restore discipline is a FIRST-CLASS assertion: the post-run
# manifest must confirm every touched file is byte-identical.
if ! grep -q "post-run manifest: every touched file is byte-identical" "$LOG"; then
  echo "RESULT: FAIL (the post-run manifest did not confirm the byte-identical restore)"
  exit 1
fi

elapsed=$(( ${SECONDS:-0} - PACK_START ))
echo "RESULT: PASS (${elapsed}s)"
exit 0
