#!/usr/bin/env bash
# Test Pack: skill_sink_class_containment_unit_test — sink-leak CLASS containment
#
# WHY THIS PACK EXISTS (the F-2 blind-spot class).
#   The class-wide containment pin
#     tests/unit/test_skill_sink_class_containment_pins.py
#   (commit ba01d9cbb) landed in NO registered pack.  An UNREGISTERED test
#   file is a surface no gate can see: the house runner enumerates
#   test/packs/*.sh, so a file that no pack EXECUTES is never run by any
#   gate — it is green-by-absence, not green-by-proof.  This pack closes
#   that gap: it makes the pin file an EXECUTED surface.
#
# WHAT THE PIN PROVES (3 legs, class-wide — NOT four per-file fixes):
#   N1 — STATIC CLASS CENSUS (derived): AST-scans every tests/**.py for the
#        poison signature `configure_sink(None)` (and the zero-arg
#        `configure_sink()`).  The ONLY sanctioned occurrence is the
#        deliberate P9 rogue-teardown SIMULATION in
#        tests/unit/test_skill_sink_retention_shared_path_pins.py; every
#        OTHER poisoner fails the node loudly.
#   N2 — MARKER ORACLE (dynamic, per class member): each derived class
#        member is driven in a SUBPROCESS with the sink NOT redirected and a
#        UNIQUE PER-RUN MARKER; the node asserts no file under the
#        checkout's real data/skill-capture-observability/ carries that
#        marker.  The oracle is the MARKER, never byte-identity, because
#        that dir is SHARED with every other lane working in this worktree.
#   N3 — SHARED-FILE SENTINEL: across the class run no shared sink file may
#        VANISH and none may SHRANK.  COUNTS ONLY — this pack makes no
#        byte-assertion against the shared sink dir, ever.
#
# NODE-COUNT CONTRACT (measured, not guessed).
#   MEASURED node count = 3 (N1, N2, N3), stable across THREE consecutive
#   `--collect-only -q` collects (3 / 3 / 3) at pin commit ba01d9cbb.
#   The pin file is UNDER ACTIVE CONCURRENT EXTENSION by another lane, so
#   the count is a MOVING TARGET by construction: a hard `== 3` would turn a
#   legitimate growth into a spurious RED.  This pack therefore enforces
#     (a) a LOWER BOUND  — collected nodes >= EXPECTED_NODE_FLOOR, and
#   (b) NODE PRESENCE   — each of the three legs' node IDs must appear in
#                         the collect output (a renamed/removed leg fails),
#   and reports the live count vs the measured baseline as a NOTICE when
#   the count MOVES.  The floor + presence form is the exact-count form
#   that survives growth.
#
# TIMEOUT CONTRACT (dual-layer, house).
#   The repo's pytest ini sets `timeout = 30` with `timeout_method =
#   "thread"`.  With `timeout_func_only` at its default (False) the timeout
#   also covers FIXTURE SETUP, and the pin's module-scoped `_class_run`
#   fixture is where the heavy class run happens — the ini value alone
#   would kill it (anomaly 224c24dc).  The pin therefore carries its own
#   `@pytest.mark.timeout(900)` on N2/N3.  THIS PACK MUST NOT OVERRIDE
#   THAT: it overrides `addopts` ONLY, never `timeout`, never
#   `timeout_method`, never `timeout_func_only`, and never passes
#   `--timeout=` (which would clobber the marker).  Do not "fix" a
#   timeout failure by disabling the marker.
#
# MEASURED WALL (this checkout, 10 derived class members): 78s wall
# (73.21s pytest-reported) for the full pin file.  The class is DERIVED from
# the tree, so a lane that adds sink-override managers makes it grow.
# Internal budget = 240s (BELOW the house 300s outer cap on purpose: an
# inner budget LARGER than the outer cap inverts the dual-layer timeout —
# the outer kill fires first and the pack dies with a bare rc=124 and NO
# `RESULT:` line).  Overrun prints RESULT: TIMEOUT and exits 124.
#
# FENCES (house).
#   - Per-variable env scrub: the FULL ENSEMBLE_AUTH_* family (7 vars) and
#     the FULL POSTGRES_* family (5 vars).  NEVER a partial auth scrub — a
#     partial one leaves ambient AUTH_ENABLED/CLIENT_ID and the daemon.api
#     importers collection-ERROR (G6a 2026-11-09).  NEVER the
#     `env -u POSTGRES_*` glob — bash does not glob env names, so it is a
#     SILENT NO-OP.
#   - FAKE_HOME (mktemp) — no live-shaped path resolution; removed on exit
#     behind an escape guard.
#   - Interpreter: the checkout's own .venv.  cwd is the checkout root, so
#     `import daemon` resolves HERE.
#   - The shared sink dir data/skill-capture-observability/ is touched by
#     the PIN only, and only through the marker oracle + file COUNTS.  This
#     pack never byte-asserts it.
#
# Usage:
#   bash test/packs/skill_sink_class_containment_unit_test.sh
#   SKILL_SINK_CLASS_CONTAINMENT_BUDGET_S=240 bash test/packs/skill_sink_class_containment_unit_test.sh
#     — override the internal wall-clock budget (seconds).
#
# Pack exit: 0 PASS / 1 FAIL / 124 TIMEOUT.  The RESULT label ALWAYS prints.
set -euo pipefail

PACK_START=${SECONDS:-0}
BUDGET_S="${SKILL_SINK_CLASS_CONTAINMENT_BUDGET_S:-240}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

PY="$PROJECT_DIR/.venv/bin/python"
PIN_FILE="tests/unit/test_skill_sink_class_containment_pins.py"

#: The LOWER BOUND of collected nodes.  The three legs N1/N2/N3 are the
#: pin's contract; a pin that loses one is not the pin.
EXPECTED_NODE_FLOOR=3

#: The MEASURED baseline (3 collects, all 3).  Growth is a NOTICE, not a
#: failure — the pin is under concurrent extension.
MEASURED_NODE_BASELINE=3

#: The node IDs that must be PRESENT in the collect output.
REQUIRED_NODE_IDS=(
  "test_n1_no_test_file_may_restore_the_sink_override_to_none"
  "test_n2_no_class_member_can_write_outside_its_scratch_dir"
  "test_n3_the_class_run_neither_destroyed_nor_shrank_a_shared_sink_file"
)

LOG_DIR="${SKILL_SINK_CLASS_CONTAINMENT_LOG_DIR:-/tmp/skill-sink-class-containment-gate}"
mkdir -p "$LOG_DIR"

# Per-variable scrub — NEVER the ``env -u POSTGRES_*`` glob, NEVER a partial
# ENSEMBLE_AUTH_* scrub.
SCRUB=(
  env
  -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_PASSWORD
  -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_CLIENT_ID
  -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_DISCOVERY_URL
  -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
  -u DATABASE_URL -u PORT -u ENSEMBLE_SELF_ENV -u ENSEMBLE_CONFIG
  -u ENSEMBLE_DATA_DIR -u DATA_DIR
  -u PGPASSWORD -u PGHOST -u PGPORT -u PGUSER -u PGDATABASE
)

echo "=== Test Pack: skill_sink_class_containment_unit_test ==="
echo "PROJECT_DIR=$PROJECT_DIR"

elapsed_s() { echo $(( ${SECONDS:-0} - PACK_START )); }
check_budget() {
  local elapsed
  elapsed="$(elapsed_s)"
  if [ "$elapsed" -gt "$BUDGET_S" ]; then
    echo "RESULT: TIMEOUT (internal timer: wall ${elapsed}s > SKILL_SINK_CLASS_CONTAINMENT_BUDGET_S=${BUDGET_S}s ${1:-})"
    exit 124
  fi
}

# --- preflight ------------------------------------------------------------
if [ ! -x "$PY" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — the worktree-local .venv/bin/python is absent)"
  exit 1
fi
if [ ! -f "$PIN_FILE" ]; then
  echo "RESULT: FAIL (MISSING-ARTIFACT — $PIN_FILE)"
  exit 1
fi
echo "[pack] interpreter: $PY"
echo "[pack] pin file: $PIN_FILE (sha256 $(sha256sum "$PIN_FILE" | cut -d' ' -f1 | cut -c1-16))"

# --- fences: FAKE_HOME (no live-shaped path resolution) --------------------
FAKE_HOME="$(mktemp -d /tmp/skill-sink-class-containment-home-XXXXXX)"
PACK_CLEANED=0
cleanup() {
  [ "$PACK_CLEANED" = 1 ] && return 0
  PACK_CLEANED=1
  # Escape guard — only ever remove a mktemp path under /tmp with our prefix.
  case "$FAKE_HOME" in
    /tmp/skill-sink-class-containment-home-*) rm -rf "$FAKE_HOME" ;;
    *) echo "[pack] WARN (cleanup): refusing to remove a non-mktemp path: $FAKE_HOME" >&2 ;;
  esac
  return 0
}
trap cleanup EXIT
trap 'cleanup; exit 143' TERM
trap 'cleanup; exit 130' INT

# --- phase 1: the node census ---------------------------------------------
echo "--- phase 1: the node census (collect-only) ---"
COLLECT_LOG="$LOG_DIR/collect.log"
COLLECT_RC=0
"${SCRUB[@]}" \
  HOME="$FAKE_HOME" \
  XDG_CACHE_HOME="$FAKE_HOME/cache" \
  PYTHONPATH="$PROJECT_DIR" \
  "$PY" -m pytest "$PIN_FILE" --collect-only -q \
  -p no:randomly --override-ini="addopts=" > "$COLLECT_LOG" 2>&1 || COLLECT_RC=$?

if [ "$COLLECT_RC" -ne 0 ]; then
  tail -5 "$COLLECT_LOG" | sed 's/^/  /'
  echo "RESULT: FAIL (phase 1: collect-only rc=${COLLECT_RC})"
  exit 1
fi

NODE_COUNT="$(grep -cE '^tests/.*::' "$COLLECT_LOG" || true)"
NODE_COUNT="${NODE_COUNT:-0}"
echo "[pack] collected nodes: $NODE_COUNT (measured baseline ${MEASURED_NODE_BASELINE}, floor ${EXPECTED_NODE_FLOOR})"

if [ "$NODE_COUNT" -lt "$EXPECTED_NODE_FLOOR" ]; then
  tail -8 "$COLLECT_LOG" | sed 's/^/  /'
  echo "RESULT: FAIL (phase 1: collected ${NODE_COUNT} node(s) < the floor ${EXPECTED_NODE_FLOOR} — a leg is missing)"
  exit 1
fi

if [ "$NODE_COUNT" -ne "$MEASURED_NODE_BASELINE" ]; then
  echo "[pack] NOTICE: the node count MOVED (${NODE_COUNT} != baseline ${MEASURED_NODE_BASELINE}) — the pin grew under concurrent extension; the floor + presence checks still hold"
fi

for nid in "${REQUIRED_NODE_IDS[@]}"; do
  if ! grep -qF "$nid" "$COLLECT_LOG"; then
    echo "RESULT: FAIL (phase 1: required node id absent from the collect output: $nid)"
    exit 1
  fi
done
echo "[pack] node presence: all ${#REQUIRED_NODE_IDS[@]} required leg(s) present"
echo "[timer] phase 1 done: $(elapsed_s)s elapsed / budget ${BUDGET_S}s"
check_budget "after phase 1"

# --- phase 2: the pins ----------------------------------------------------
echo "--- phase 2: the class-containment pins ---"
PHASE2_LOG="$LOG_DIR/phase2.log"
EXIT_CODE=0
"${SCRUB[@]}" \
  HOME="$FAKE_HOME" \
  XDG_CACHE_HOME="$FAKE_HOME/cache" \
  PYTHONPATH="$PROJECT_DIR" \
  "$PY" -m pytest "$PIN_FILE" -q --tb=short \
  -p no:randomly --override-ini="addopts=" > "$PHASE2_LOG" 2>&1 || EXIT_CODE=$?

tail -5 "$PHASE2_LOG" | sed 's/^/  /'
grep -E "^(FAILED|ERROR) " "$PHASE2_LOG" | sed 's/^/  /' || true
PASSED_COUNT="$(grep -oE '[0-9]+ passed' "$PHASE2_LOG" | tail -1 | cut -d' ' -f1 || true)"
PASSED_COUNT="${PASSED_COUNT:-0}"
echo "[pack] passed nodes: ${PASSED_COUNT} / collected ${NODE_COUNT}"
echo "[timer] phase 2 done: $(elapsed_s)s elapsed / budget ${BUDGET_S}s"
check_budget "after phase 2"

# --- verdict --------------------------------------------------------------
echo "[timer] total wall: $(elapsed_s)s / budget ${BUDGET_S}s"
if [ "$EXIT_CODE" -eq 124 ]; then
  echo "RESULT: TIMEOUT (the pin run hit the outer 300s kill)"
  exit 124
fi
if [ "$EXIT_CODE" -ne 0 ]; then
  echo "RESULT: FAIL (phase 2: the class-containment pin is RED — rc=${EXIT_CODE})"
  exit 1
fi
if ! grep -qE "^[1-9][0-9]* passed" "$PHASE2_LOG"; then
  echo "RESULT: FAIL (VACUOUS — the summary carries no 'N passed' with N >= 1)"
  exit 1
fi
echo "RESULT: PASS (${PASSED_COUNT} node(s) passed, ${NODE_COUNT} collected, $(elapsed_s)s)"
exit 0
