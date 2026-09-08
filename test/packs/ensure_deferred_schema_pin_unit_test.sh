#!/usr/bin/env bash
# Test Pack: ensure_deferred_schema_pin_unit_test — schema-pin + sentinel
# regression tests for the content-NOT-NULL-safe deferred marker fix
# (commit ef1432ca, branch feature/fix-report-injection-content-notnull).
#
# Fix under test (daemon/repositories/report_injection/repository.py):
#   * ``_DEFERRED_MARKER_CONTENT_SENTINEL = ""`` — empty-string sentinel
#     used by the DEFERRED-marker INSERT. Truthful "no content yet, will
#     be filled at reconciliation time by
#     ``_create_subshape_a_artifacts``" — satisfies legacy prod
#     ``content NOT NULL`` schema AND Phase-1 nullable schema.
#   * Schema-pin tests document + pin the drift
#     (prod: content NOT NULL; model + create_all: content NULLABLE).
#
# Incident (2026-09-08, parent b7ead8a4 children aae1539c / 8629bc77 /
# 50b7c9a9): the pre-fix ensure_deferred INSERT wrote ``content=None``,
# tripping prod's legacy NOT NULL on ``report_injections.content`` every
# sweep pass; the row stayed DEFERRED forever. Phase 1 commit
# ``eeb4b286`` (2026-08-20) flipped the model to ``nullable=True`` but
# never added a PG ``ALTER COLUMN content DROP NOT NULL`` — schema/model
# drift. The sentinel pins the contract so the drift can never silently
# return.
#
# Files covered:
#   1. tests/unit/test_ensure_deferred_schema_pin.py — 7 tests
#      (collect-verified at ef1432ca: model declares nullable,
#      marker uses "" sentinel, sentinel round-trips through create_all
#      schema, create_all makes content nullable, legacy NOT NULL schema
#      accepts the marker, marker works post MigrationRunner, create_all
#      vs migration chain difference documented).
#
# NOTE ON PATHS: the dispatch paraphrased this file as
# tests/unit/repositories/test_ensure_deferred_schema_pin.py — that path
# does NOT exist. The ACTUAL file at commit ef1432ca is:
#   tests/unit/test_ensure_deferred_schema_pin.py
# (verified via `git show ef1432ca --name-only`). The repositories/
# subdirectory contains other ensure_deferred tests; the schema-pin
# file sits one level up next to its sibling
# test_ensure_deferred_integrity_error_classification.py.
#
# Branch-under-test: feature/fix-report-injection-content-notnull
# (fix commit ef1432ca, base 50080e81).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 120s` on the pytest process
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: ensure_deferred_schema_pin_unit_test ==="
echo "HEAD: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "(content NOT NULL drift sentinel pin: model nullable + sentinel '' + legacy NOT NULL accept + migration chain)"

# Pre-flight venv discipline check (uv-venv-aware): the daemon module
# imported via the worktree's .venv MUST resolve to a real path inside
# this worktree root. This validates the EDITABLE-INSTALL target — the
# actual incident class (running tests against the main checkout while
# claiming to test a worktree). Note: uv-managed venvs symlink
# .venv/bin/python to the shared uv interpreter dir BY DESIGN, so a
# python-binary realpath-equality check is NOT a valid gate here; the
# authoritative signal is the importable daemon module's real path.
WORKTREE_ROOT="$(pwd -P)"
DAEMON_FILE="$(.venv/bin/python -c 'import daemon,sys; print(daemon.__file__)' 2>/dev/null)" || {
  echo "[FATAL] .venv/bin/python failed to import daemon (exit $?)."
  echo "         The worktree venv is broken — re-run \`uv sync\` and retry."
  echo "RESULT: FAIL"
  exit 1
}
RESOLVED_DAEMON="$(realpath "$DAEMON_FILE" 2>/dev/null || python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$DAEMON_FILE")"
case "$RESOLVED_DAEMON" in
  "$WORKTREE_ROOT"/*) ;;
  *)
    echo "[FATAL] daemon.__file__ resolves to '$RESOLVED_DAEMON' which is NOT inside the worktree root '$WORKTREE_ROOT'."
    echo "         Editable install points at a different checkout — testing the wrong code."
    echo "RESULT: FAIL"
    exit 1
    ;;
esac
echo "(venv OK: daemon.__file__ resolves into worktree)"

cd "$PROJECT_DIR"

# Layer 2 (script-internal): 120s hard cap on the pytest process.
# 7 schema-pin tests are pure file-backed SQLite fixtures; <10s typical.
# set +e around the timed command so the RESULT: FAIL branch below is
# reachable under `set -e` (the timed command's exit code is captured,
# not fatal, until the RESULT block re-raises it).
set +e
timeout 120s .venv/bin/pytest \
  tests/unit/test_ensure_deferred_schema_pin.py \
  --tb=short -q 2>&1
EXIT_CODE=$?
set -e

if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi