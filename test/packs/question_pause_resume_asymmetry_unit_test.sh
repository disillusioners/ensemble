#!/usr/bin/env bash
# Test Pack: question_pause_resume_asymmetry_unit_test
# Timeout: 5 minutes (300s)
#
# Feature: fix/question-pause-resume-asymmetry (side-effect-paused
# instances stranded after a sibling's ``ask_questions``).
#
# What this pack exercises:
#   - tests/unit/test_question_pause_resume_asymmetry.py
#       (a) ask → the cascade parks a side-effect child → the answer
#           releases it AND re-arms its turn (the wake);
#       (b) restart between ask and answer → the recorded set survives
#           and the boot backstop sweep recovers a stranded instance;
#       (c) the age bound (older → swept, younger → untouched);
#       (d) a genuinely question-paused asker is NOT swept + the D1
#           tree-scoped predicate pin;
#       plus the late-pause race pin and the already-delivered
#           (CAS loser) branch pin.
#
# The suite is hermetic: file-backed SQLite under pytest's tmp_path, no
# network, no live daemon, no HOME-derived install paths.
#
# Usage:
#   ./test/packs/question_pause_resume_asymmetry_unit_test.sh
#   PYTEST_BIN=/path/to/pytest ./test/packs/question_pause_resume_asymmetry_unit_test.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: question_pause_resume_asymmetry_unit_test ==="
echo "Repo:    ${PROJECT_DIR}"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

cd "$PROJECT_DIR" || { echo "RESULT: FAIL"; exit 1; }

# The project venv is the sanctioned runner. A worktree without its own
# .venv may point PYTEST_BIN at an equivalent interpreter.
if [ -z "${PYTEST_BIN:-}" ]; then
  if [ -x "$PROJECT_DIR/.venv/bin/pytest" ]; then
    PYTEST_BIN="$PROJECT_DIR/.venv/bin/pytest"
  elif command -v pytest >/dev/null 2>&1; then
    PYTEST_BIN="$(command -v pytest)"
  else
    echo "FATAL: no pytest runner found (set PYTEST_BIN)" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
fi
echo "Pytest:  ${PYTEST_BIN}"
echo

FILES=(
  "tests/unit/test_question_pause_resume_asymmetry.py"
)

for f in "${FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "FATAL: required test file missing: $f" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
done

# Layer-2 timeout: 280s wraps pytest (innate test-pack invariant).
# --timeout=60 (rider): the C1 concurrency pin guards a DEADLOCK
# stratum — the pre-fix failure mode is a HANG (a threading.Lock held
# across an await blocks the event loop), not an assertion failure.
# Without a hard per-test bound a regression deadlock would hang the
# pack instead of failing it: the timeout turns the deadlock into a
# KILLED RUN (a reported failure) rather than an indefinite hang.
timeout 280 "$PYTEST_BIN" \
  -p no:randomly \
  -p no:xdist \
  -v \
  --tb=short \
  --color=no \
  --override-ini="addopts=" \
  --timeout=60 \
  "${FILES[@]}"
PYTEST_EXIT=$?

echo
if [ $PYTEST_EXIT -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $PYTEST_EXIT -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL (pytest exit ${PYTEST_EXIT})"
  exit 1
fi
