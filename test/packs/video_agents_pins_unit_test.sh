#!/usr/bin/env bash
# =============================================================================
# Test Pack: video_agents_pins_unit_test
#
# Lane: feature/pack-coverage-registration (base `ed8ab508c`) — the
#       PACKS.md + pack-scripts coverage registration wave, family A3
#       ("video agents").
#
# WHAT IT PINS
#   tests/unit/test_video_capturer_agent.py   (19 nodes)
#   tests/unit/test_video_editor_agent.py     (19 nodes)
#   tests/unit/test_video_producer_agent.py   (19 nodes)
#   tests/unit/test_video_verifier_agent.py   (19 nodes)
#   Total: 76 nodes (mechanical `--collect-only` census at this freeze:
#   76 collected, RC=0, 0 collection errors).
#
#   The four video-team agent census packs (video-team activation step 4).
#   Each file pins its own agent directory under agents/ by EXACT EQUALITY:
#     1. meta.json schema + required fields (mirror test_maintenancer_agent.py)
#        — id, name, semver version, innate_skills, skill_injection=false,
#        snapshot_enabled=true, no_force_explore=true.
#     2. tools.allow EXACT set AND ordering discipline — `pok` immediately
#        before `nest-report`, `nest-report` LAST, each grant appearing once.
#     3. team_members exact set (empty for capturer/editor/verifier;
#        video-producer owns [video-capturer, video-editor, video-verifier]).
#     4. Registry discovery via AgentRegistry against the real agents/ dir.
#     5. Prompt-file inventory — soul/rule/workflow/tools_note + skill-set.yaml
#        + skills-template/ contents.
#
#   All four files are pure file + registry parsing — no daemon/DB startup,
#   no LLM calls.
#
# CONTRACT (house shape — mirrors the engine-arc lane packs)
#   - Dual-layer timeout: inner `timeout 280`; caller wraps with 300.
#   - Serial + deterministic: `-p no:xdist -p no:randomly`.
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob is a SILENT NO-OP in bash
#     — bash does not glob env-var names in `env -u`).
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY (never
#     `env -u` on it: stripping the ambient guard makes the boot sweep
#     SIGTERM/SIGKILL live processes).
#   - Repo addopts KEPT (the `-m 'not integration and not postgres ...'`
#     marker filter) so a postgres-marked node can never touch a DB; the
#     census that produced the node count was measured under the SAME filter,
#     so the pack's node count and the census are comparable.
#   - SLICING PROOF: the pack re-derives its own node count with
#     `--collect-only` and FAILS if it drifts from the declared 76 — a
#     pack that silently runs a different node set is inadmissible.
#   - VACUOUS-VERDICT GUARD: zero passed tests or no summary line = FAIL
#     (a pack that can PASS from an empty run is worthless).
#   - Output: header, pytest body, `RESULT: PASS|FAIL|TIMEOUT`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
# =============================================================================
set -u

PACK_NAME="video_agents_pins_unit_test"
EXPECTED_NODES=76

echo "=== Test Pack: ${PACK_NAME} ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL (cannot cd to ${PROJECT_DIR})"; exit 1; }

PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "FATAL: .venv/bin/python not found at $PYTHON_BIN" >&2
  echo "RESULT: FAIL"
  exit 1
fi

UNIT_FILES=(
  tests/unit/test_video_capturer_agent.py
  tests/unit/test_video_editor_agent.py
  tests/unit/test_video_producer_agent.py
  tests/unit/test_video_verifier_agent.py
)

for f in "${UNIT_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "FATAL: required test file missing: $f" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
done

# ── the per-variable env scrub (never a POSTGRES_* glob) ─────────────────
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u OPENAI_API_KEY
  -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_CLIENT_ID
  -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_DISCOVERY_URL
  -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
  -u ENSEMBLE_BASH_TRACKING_ID
)

# ── the SLICING PROOF (node-count parity vs the declared census) ────────
echo
echo "--- slicing proof: --collect-only over the declared file set ---"
COLLECT_OUT="$(mktemp)"
timeout 120 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  "$PYTHON_BIN" -m pytest \
    -p no:randomly \
    -p no:xdist \
    --collect-only \
    -q \
    --color=no \
    "${UNIT_FILES[@]}" > "$COLLECT_OUT" 2>&1
COLLECT_EXIT="$?"
COLLECTED="$(grep -oE '^[0-9]+ tests? collected' "$COLLECT_OUT" | head -1 | grep -oE '^[0-9]+')"

if [ "$COLLECT_EXIT" -ne 0 ]; then
  echo "SLICING PROOF: FAIL (collection exit=$COLLECT_EXIT)"
  tail -25 "$COLLECT_OUT"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (slicing proof)"
  exit 1
fi
if [ -z "$COLLECTED" ]; then
  echo "SLICING PROOF: FAIL (VACUOUS — no 'collected' summary line)"
  tail -25 "$COLLECT_OUT"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (slicing proof)"
  exit 1
fi
if [ "$COLLECTED" -ne "$EXPECTED_NODES" ]; then
  echo "SLICING PROOF: FAIL (node-count drift: expected $EXPECTED_NODES, collected $COLLECTED)"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (slicing proof)"
  exit 1
fi
echo "SLICING PROOF: OK ($COLLECTED nodes collected, expected $EXPECTED_NODES)"
rm -f "$COLLECT_OUT"

echo
echo "--- lane A3: video agents census (capturer / editor / producer / verifier) ---"
OUT="$(mktemp)"
timeout 280 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTEST_TIMEOUT=280 \
  "$PYTHON_BIN" -m pytest \
    -q \
    -p no:randomly \
    -p no:xdist \
    --tb=short \
    --color=no \
    --override-ini="timeout=120" \
    "${UNIT_FILES[@]}" 2>&1 | tee "$OUT"
# NOTE: `set -u` (no `set -e`) — under `set -e` a bare `X=$?` after a
#       failing pytest would NEVER execute, making `RESULT: FAIL`
#       unreachable. PIPESTATUS[0] is pytest's own rc.
PYTEST_EXIT="${PIPESTATUS[0]}"

# ── the VACUOUS-VERDICT GUARD ───────────────────────────────────────────
# NOTE: under `-q` the summary line STARTS with the count ("76 passed, ..."),
# so the engine-arc house sed (`[^0-9]`-anchored) cannot extract it — the
# extraction below anchors on the TOKEN, not on a preceding non-digit.
# NOTE: outcome counts come from the SUMMARY LINE tokens only. A bare
#       `grep -c '^ERROR '` manufactures a FALSE RED: with `-rA` pytest
#       captures ERROR-prefixed LOG RECORDS emitted by the code under test
#       (a real pytest error shows an ERRORS section header).
SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|errors|skipped|deselected|xfailed|xpassed)' "$OUT" | tail -1)"
PASSED="$(printf '%s\n' "$SUMMARY" | grep -oE '[0-9]+ passed' | head -1 | grep -oE '^[0-9]+')"
FAILED="$(printf '%s\n' "$SUMMARY" | grep -oE '[0-9]+ failed' | head -1 | grep -oE '^[0-9]+')"
SKIPPED="$(printf '%s\n' "$SUMMARY" | grep -oE '[0-9]+ skipped' | head -1 | grep -oE '^[0-9]+')"
XFAILED="$(printf '%s\n' "$SUMMARY" | grep -oE '[0-9]+ xfailed' | head -1 | grep -oE '^[0-9]+')"
rm -f "$OUT"

# NOTE (ordering): the TIMEOUT verdict is checked FIRST, before the
# vacuous-verdict guard. A killed pytest produces NO summary line at all,
# so the vacuous guard would fire and report `RESULT: FAIL` (exit 1) for a
# genuine timeout — and the pack contract's `RESULT: TIMEOUT` / exit 124
# would be unreachable. Timeout is a distinct verdict and must win.
if [ "$PYTEST_EXIT" -eq 124 ]; then
  echo
  echo "RESULT: TIMEOUT"
  exit 124
fi

if [ -z "$SUMMARY" ]; then
  echo
  echo "RESULT: FAIL (VACUOUS — pytest produced NO summary: the suite did not run)"
  exit 1
fi
if [ -z "$PASSED" ] || [ "$PASSED" -eq 0 ]; then
  echo
  echo "RESULT: FAIL (VACUOUS — zero tests passed: '${SUMMARY}')"
  exit 1
fi
echo "SUMMARY: ${SUMMARY}"
echo "COUNTS: passed=${PASSED:-0} failed=${FAILED:-0} skipped=${SKIPPED:-0} xfailed=${XFAILED:-0}"

case "$PYTEST_EXIT" in
  0)
    echo "RESULT: PASS"
    exit 0
    ;;
  124)
    echo "RESULT: TIMEOUT"
    exit 124
    ;;
  *)
    echo "RESULT: FAIL (pytest exit=$PYTEST_EXIT)"
    exit 1
    ;;
esac
