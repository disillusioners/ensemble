#!/usr/bin/env bash
# Test Pack: skill_evolution_unit_test — Skill evolution unit tests
# EXTENDED 2026-11-09 (skill-system-revival Phase 1, workstream G).
#
# Legs:
#   1. the pre-existing unit suites (unchanged);
#   2. the G flakiness pins (M-G1..M-G5);
#   3. the E1 re-fix burst-guard pins (M-E1a..M-E1f);
#   4. the G ACCEPTANCE — >= 3 consecutive FULL-FILE-SET green runs of
#      the A/B-resolution file, classified with the pinned classifier
#      (`grep '^FAILED'` only) and asserted through
#      `assert_full_file_set_acceptance` (phase1-plan §11, DS-37: an
#      isolated green passes at 15% BY LUCK — the acceptance context is
#      the full file set, where the flake fires at 38%).
#
# Timeout: 2 minutes (120s) per pytest invocation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: skill_evolution_unit_test ==="

cd "$PROJECT_DIR"

# NOTE: tests/unit/test_auto_load_skills.py was deleted in eeef8845
# (legacy context injection removal). Removed from pack.
#
# RESULT-echo discipline: with `set -e` a pytest failure would abort the
# script BEFORE the RESULT line, so every pytest call is guarded with
# `|| EXIT_CODE=$?` (the assignment is then in a list-context and
# `set -e` does not fire on it).
EXIT_CODE=0

# ── Leg 1 — the pre-existing unit suites ──────────────────────────────
timeout 120s .venv/bin/pytest \
  tests/unit/test_skill_seeding.py \
  tests/unit/test_skill_clone_service.py \
  --tb=short -q 2>&1 || EXIT_CODE=$?

# ── Leg 2 — the G flakiness pins (M-G1..M-G5) ─────────────────────────
if [ "$EXIT_CODE" -eq 0 ]; then
  timeout 120s .venv/bin/pytest \
    tests/services/test_skill_flakiness_fixture_pins.py \
    --tb=short -q 2>&1 || EXIT_CODE=$?
fi

# ── Leg 3 — the E1 re-fix burst-guard pins (M-E1a..M-E1f) ─────────────
if [ "$EXIT_CODE" -eq 0 ]; then
  timeout 120s .venv/bin/pytest \
    tests/unit/test_skill_fix_burst_guard.py \
    --tb=short -q 2>&1 || EXIT_CODE=$?
fi

# ── Leg 3b — the FIX WAVE 1 pins (M1 config wiring · M2 route · M3
#            dialect) + the G equivalence pin leg ──────────────────────
#   * tests/unit/test_skill_revival_fix_wave1_pins.py — the M1
#     (config knob reaches the dispatcher), M2 (a designed refusal is
#     not an HTTP 500) and M3 (the PostgreSQL branch of the E1 dialect
#     split) pins;
#   * the M-m5 experiment-equivalence pin lives in the G pin file
#     (leg 2) — it needs the services fixture, so it cannot move.
if [ "$EXIT_CODE" -eq 0 ]; then
  timeout 120s .venv/bin/pytest \
    tests/unit/test_skill_revival_fix_wave1_pins.py \
    --tb=short -q 2>&1 || EXIT_CODE=$?
fi

# ── Leg 4 — the G acceptance (>= 3 consecutive FULL-FILE-SET greens) ──
if [ "$EXIT_CODE" -eq 0 ]; then
  G_RUNS=3
  G_TMP="$(mktemp -d)"
  trap 'rm -rf "$G_TMP"' EXIT
  G_LEG_OK=1
  for i in $(seq 1 "$G_RUNS"); do
    timeout 120s .venv/bin/pytest \
      tests/services/test_skill_evolution_service.py \
      --tb=short -q > "$G_TMP/run${i}.log" 2>&1 || true
    # Pinned classifier: `grep '^FAILED'` ONLY (never '^(FAILED|ERROR)' —
    # the duplicate traceback line would inflate the red count).
    grep '^FAILED' "$G_TMP/run${i}.log" | sort > "$G_TMP/run${i}.failed" || true
    echo "G acceptance run ${i}/${G_RUNS}: $(wc -l < "$G_TMP/run${i}.failed") red(s)"
  done
  .venv/bin/python - "$G_TMP" "$G_RUNS" <<'PY' || EXIT_CODE=$?
import sys
from pathlib import Path

from tests.services.test_skill_evolution_service import (
    G_ACCEPTANCE_CONTEXT,
    GAcceptanceRun,
    assert_full_file_set_acceptance,
)

tmp = Path(sys.argv[1])
runs = int(sys.argv[2])
parsed = []
for index in range(1, runs + 1):
    failed = tuple(
        line.split("::", 1)[1].strip()
        for line in (tmp / f"run{index}.failed").read_text().splitlines()
        if line.startswith("FAILED ")
    )
    parsed.append(GAcceptanceRun(G_ACCEPTANCE_CONTEXT, failed))
assert_full_file_set_acceptance(parsed)
print(f"G acceptance OK: {runs} consecutive full-file-set runs, 0 reds")
PY
fi

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
