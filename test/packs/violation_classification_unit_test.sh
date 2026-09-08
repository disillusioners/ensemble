#!/usr/bin/env bash
# Test Pack: violation_classification_unit_test — IntegrityError
# classification regression tests for the deterministic-NotNullViolation
# fix (commit ef1432ca, branch feature/fix-report-injection-content-notnull).
#
# Fix under test (daemon/repositories/report_injection/repository.py):
#   * ``_is_obligation_triple_unique_violation(exc)`` — dialect-aware
#     discriminator (PG constraint NAME ``uq_report_injections_oblig_triple``;
#     SQLite column-set match on
#     ``(parent_instance_id, child_instance_id, child_message_id)``).
#     Mirrors ``daemon/services/child_reports.py::_is_obligation_triple_integrity_error``.
#   * ``ensure_deferred`` IntegrityError handler now routes ONLY the
#     obligation-triple unique violation through the phantom-conflict
#     convergence path; EVERY other IntegrityError (NOT NULL /
#     FOREIGN KEY / CHECK / a different UNIQUE) is deterministic and
#     re-raises IMMEDIATELY without a second INSERT (the b7ead8a4 bug
#     class: the pre-fix code routed deterministic NotNullViolation
#     through the phantom-conflict retry, which produced a misleading
#     log AND a second INSERT that raised the SAME error, stranding
#     the marker DEFERRED forever).
#
# Incident (2026-09-08): leader b7ead8a4 children aae1539c / 8629bc77
# / 50b7c9a9 stayed DEFERRED every sweep forever — every 300s the
# pre-fix code hit ``NotNullViolation`` on ``content``, logged
# "phantom conflict", re-inserted, hit ``NotNullViolation`` again,
# logged "persistent conflict". Three children × every sweep = a
# permanently broken recovery lane.
#
# Files covered:
#   1. tests/unit/test_ensure_deferred_integrity_error_classification.py — 11 tests
#      (collect-verified at ef1432ca: NOT NULL raises immediately,
#      FOREIGN KEY raises immediately, CHECK raises immediately,
#      non-triple UNIQUE raises immediately, obligation-triple unique
#      triggers insert-on-missing, persistent zero-rows raises,
#      discriminator accepts PG format constraint name, discriminator
#      accepts SQLite format column set, discriminator rejects NOT
#      NULL, discriminator rejects PK collision, discriminator rejects
#      partial column overlap).
#
# Branch-under-test: feature/fix-report-injection-content-notnull
# (fix commit ef1432ca, base 50080e81).
#
# NOTE: the 11 classification tests live in a SEPARATE file
# (tests/unit/test_ensure_deferred_integrity_error_classification.py),
# NOT inside tests/unit/test_ensure_deferred_schema_pin.py — hence this
# separate pack. If a future commit collapses them, retire this pack.
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

echo "=== Test Pack: violation_classification_unit_test ==="
echo "HEAD: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "(IntegrityError classification: obligation-triple unique → converge; everything else → immediate re-raise)"

# Pre-flight venv discipline check: the pytest binary MUST resolve into
# the worktree's .venv (a prior incident class ran the main checkout's
# venv against a worktree — silently testing the wrong code).
EXPECTED_VENV_PREFIX="$(pwd -P)/.venv/bin/python"
RESOLVED_PYTHON="$(realpath .venv/bin/python 2>/dev/null || python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' .venv/bin/python)"
if [[ "$RESOLVED_PYTHON" != "$EXPECTED_VENV_PREFIX" ]]; then
  echo "[FATAL] .venv/bin/python resolves to '$RESOLVED_PYTHON' but the worktree root is '$EXPECTED_VENV_PREFIX'."
  echo "         The worktree has no proper venv — re-run \`uv sync\` and retry."
  echo "RESULT: FAIL"
  exit 1
fi
DAEMON_FILE="$($RESOLVED_PYTHON -c 'import daemon; print(daemon.__file__)' 2>&1)"
EXPECTED_DAEMON_PREFIX="$(pwd -P)/daemon/__init__.py"
RESOLVED_DAEMON="$(realpath "$DAEMON_FILE" 2>/dev/null || python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$DAEMON_FILE")"
if [[ "$RESOLVED_DAEMON" != "$EXPECTED_DAEMON_PREFIX" ]]; then
  echo "[FATAL] daemon.__file__ resolves to '$RESOLVED_DAEMON' but the worktree root is '$EXPECTED_DAEMON_PREFIX'."
  echo "         Editable install points at a different checkout — testing the wrong code."
  echo "RESULT: FAIL"
  exit 1
fi
echo "(venv OK: daemon.__file__ resolves into worktree)"

cd "$PROJECT_DIR"

# Layer 2 (script-internal): 120s hard cap on the pytest process.
# 11 classification tests run on file-backed SQLite with mocked
# IntegrityError instances; <15s typical.
# set +e around the timed command so the RESULT: FAIL branch below is
# reachable under `set -e`.
set +e
timeout 120s .venv/bin/pytest \
  tests/unit/test_ensure_deferred_integrity_error_classification.py \
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