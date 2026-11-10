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
#   The pack's OWN budget is a SECOND, independent layer:
#     layer 1 (outer)  — the caller's `timeout 300 bash <pack>`;
#     layer 2 (inner)  — `timeout ${INNER_TIMEOUT_S}` ON THE PYTEST
#                        INVOCATION ITSELF, so a hang inside the pin is
#                        killed by the PACK (rc=124 -> RESULT: TIMEOUT),
#                        NOT by the outer kill.  Without the inner timeout
#                        the outer kill fires first and the pack dies with
#                        a bare rc=124 and NO `RESULT:` line — the
#                        dual-layer inversion the house forbids.
#     layer 3 (script) — the internal wall-clock budget (BUDGET_S), checked
#                        after every phase.
#   MEASURED WALL (this checkout, 10 derived class members): 78s wall
#   (73.21s pytest-reported) for the pin file alone; 89s for the pack.
#   The class is DERIVED from the tree, so a lane that adds sink-override
#   managers makes it grow.  Internal budget = 240s (BELOW the house 300s
#   outer cap on purpose); inner pytest timeout = BUDGET_S - 20 = 220s.
#
# FENCES (house).
#   - Per-variable env scrub: the FULL ENSEMBLE_AUTH_* family (7 vars) and
#     the FULL POSTGRES_* family (5 vars).  NEVER a partial auth scrub — a
#     partial one leaves ambient AUTH_ENABLED/CLIENT_ID and the daemon.api
#     importers collection-ERROR (G6a 2026-11-09).  NEVER the
#     `env -u POSTGRES_*` glob — bash does not glob env names, so it is a
#     SILENT NO-OP.
#   - ENSEMBLE_SELF_ENV is SET to `dev`, never unset: absent auto-derives
#     from the frozen/install-dir heuristic, which can land on `live`.
#   - The HOUSE CANONICAL scrub wrapper `scripts/run_tests_scrubbed.sh` is
#     the LAST layer before pytest: it unsets the libpq/Plane families and
#     runs a FAMILY-WIDE leak gate that fails LOUD on any connection-var
#     spelling the enumeration missed.
#   - The FENCE is evaluated in the SCRUBBED CHILD (never in the parent —
#     the parent legitimately still carries the live coordinates), and the
#     pack PROVES THE FENCE ARMS with a negative self-test.
#   - IMPORT GUARD: proves `import daemon` resolves under THIS checkout.
#     A run that silently imported the shared checkout or the live install
#     would report a green result computed against FOREIGN code.
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
# Pack exit: 0 PASS / 1 FAIL / 124 TIMEOUT.  The RESULT line is EXACTLY
# `RESULT: PASS` | `RESULT: FAIL` | `RESULT: TIMEOUT`; every count/detail
# goes on the PRECEDING line.
set -euo pipefail

PACK_START=${SECONDS:-0}
BUDGET_S="${SKILL_SINK_CLASS_CONTAINMENT_BUDGET_S:-240}"
INNER_TIMEOUT_S="${SKILL_SINK_CLASS_CONTAINMENT_INNER_TIMEOUT_S:-$(( BUDGET_S - 20 ))}"
CENSUS_TIMEOUT_S="${SKILL_SINK_CLASS_CONTAINMENT_CENSUS_TIMEOUT_S:-120}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR"

PY="$PROJECT_DIR/.venv/bin/python"
PIN_FILE="tests/unit/test_skill_sink_class_containment_pins.py"
SCRUB_WRAPPER="$PROJECT_DIR/scripts/run_tests_scrubbed.sh"

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
  -u DATABASE_URL -u DATABASE_URL_POSTGRES -u POSTGRES_URL -u PORT
  -u ENSEMBLE_CONFIG -u ENSEMBLE_DATA_DIR -u DATA_DIR
  -u PGPASSWORD -u PGHOST -u PGPORT -u PGUSER -u PGDATABASE -u PGPASSFILE
  -u PGSSLMODE -u PGSERVICE -u PGSERVICEFILE
  -u PLANE_MCP_WORKSPACE_SLUG -u PLANE_BASE_URL
)

# The FENCE — evaluated in the SCRUBBED CHILD, never in the parent (the
# parent legitimately still carries the live coordinates, so a parent-side
# assertion would fire spuriously on every run).
FENCE_SNIPPET='[ "${POSTGRES_DB:-}" != "ensemble_prod" ] || { echo "FENCE BREACH: POSTGRES_DB=ensemble_prod reached the child" >&2; exit 78; }; [ "${POSTGRES_HOST:-}" != "10.44.0.2" ] || { echo "FENCE BREACH: POSTGRES_HOST=10.44.0.2 reached the child" >&2; exit 78; }; exec "$@"'

echo "=== Test Pack: skill_sink_class_containment_unit_test ==="
echo "PROJECT_DIR=$PROJECT_DIR"

elapsed_s() { echo $(( ${SECONDS:-0} - PACK_START )); }
check_budget() {
  local elapsed
  elapsed="$(elapsed_s)"
  if [ "$elapsed" -gt "$BUDGET_S" ]; then
    echo "RESULT: TIMEOUT"
    echo "[pack] internal timer: wall ${elapsed}s > budget ${BUDGET_S}s ${1:-}"
    exit 124
  fi
}

# --- preflight ------------------------------------------------------------
if [ ! -x "$PY" ]; then
  echo "RESULT: FAIL"
  echo "[pack] MISSING-INTERPRETER — the worktree-local .venv/bin/python is absent"
  exit 1
fi
if [ ! -f "$PIN_FILE" ]; then
  echo "RESULT: FAIL"
  echo "[pack] MISSING-ARTIFACT — $PIN_FILE"
  exit 1
fi
if [ ! -x "$SCRUB_WRAPPER" ]; then
  echo "RESULT: FAIL"
  echo "[pack] MISSING-ARTIFACT — the house scrub wrapper is not executable: $SCRUB_WRAPPER"
  exit 1
fi
echo "[pack] interpreter: $PY"
echo "[pack] pin file: $PIN_FILE (sha256 $(sha256sum "$PIN_FILE" | cut -d' ' -f1 | cut -c1-16))"
echo "[pack] budgets: outer 300s (caller) / inner pytest ${INNER_TIMEOUT_S}s / script ${BUDGET_S}s"

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

# run_pytest <timeout_s> <log> <pytest-args...>
#   scrub -> fence (in the child) -> house canonical scrub wrapper -> pytest.
run_pytest() {
  local tmo="$1"; shift
  local log="$1"; shift
  timeout "$tmo" "${SCRUB[@]}" \
    ENSEMBLE_SELF_ENV=dev \
    HOME="$FAKE_HOME" \
    XDG_CACHE_HOME="$FAKE_HOME/cache" \
    PYTHONPATH="$PROJECT_DIR" \
    sh -c "$FENCE_SNIPPET" sh "$SCRUB_WRAPPER" "$@" > "$log" 2>&1
}

# --- phase 0: the import guard + the fence self-test ----------------------
echo "--- phase 0: the import guard + the fence self-test ---"
GUARD_LOG="$LOG_DIR/phase0.log"
: > "$GUARD_LOG"

GUARD_RC=0
timeout "${CENSUS_TIMEOUT_S}" "${SCRUB[@]}" \
  ENSEMBLE_SELF_ENV=dev \
  HOME="$FAKE_HOME" \
  XDG_CACHE_HOME="$FAKE_HOME/cache" \
  PYTHONPATH="$PROJECT_DIR" \
  "$PY" - "$PROJECT_DIR" >> "$GUARD_LOG" 2>&1 <<'GUARDEOF' || GUARD_RC=$?
import os
import sys

project_dir = os.path.realpath(sys.argv[1])
try:
    import daemon
except Exception as exc:  # pragma: no cover — the guard must speak up
    sys.stderr.write(f"IMPORT GUARD: FAIL — 'import daemon' raised: {exc!r}\n")
    sys.exit(1)

pkg_dir = os.path.realpath(os.path.dirname(daemon.__file__))
expected = os.path.join(project_dir, "daemon")
sys.stderr.write(f"daemon.__file__={daemon.__file__}\n")
if pkg_dir != expected:
    sys.stderr.write(
        "IMPORT GUARD: FAIL — 'import daemon' did NOT resolve under "
        f"{project_dir}\n"
        "  (the run would have tested the FOREIGN tree)\n"
    )
    sys.exit(1)
sys.stderr.write("IMPORT GUARD: PASS — import daemon resolved under "
                 f"{project_dir}\n")
GUARDEOF
sed 's/^/  /' "$GUARD_LOG"
if [ "$GUARD_RC" -ne 0 ]; then
  echo "RESULT: FAIL"
  echo "[pack] phase 0: the import guard"
  exit 1
fi

# Fence self-test: prove the fence ARMS (a poisoned child must be refused).
FENCE_RC=0
FENCE_OUT="$(POSTGRES_DB=ensemble_prod sh -c "$FENCE_SNIPPET" sh true 2>&1)" || FENCE_RC=$?
if [ "$FENCE_RC" -eq 0 ] || ! printf '%s' "$FENCE_OUT" | grep -q "FENCE BREACH"; then
  echo "RESULT: FAIL"
  echo "[pack] phase 0: the fence did NOT arm (rc=${FENCE_RC}, out=[${FENCE_OUT}]) — refusing to run unprotected"
  exit 1
fi
echo "[pack] fence self-test: armed (rc=${FENCE_RC})"
echo "[timer] phase 0 done: $(elapsed_s)s elapsed / budget ${BUDGET_S}s"
check_budget "after phase 0"

# --- phase 1: the node census ---------------------------------------------
echo "--- phase 1: the node census (collect-only) ---"
COLLECT_LOG="$LOG_DIR/collect.log"
COLLECT_RC=0
run_pytest "$CENSUS_TIMEOUT_S" "$COLLECT_LOG" \
  "$PIN_FILE" --collect-only -q -p no:randomly --override-ini="addopts=" || COLLECT_RC=$?

if [ "$COLLECT_RC" -ne 0 ]; then
  tail -5 "$COLLECT_LOG" | sed 's/^/  /'
  echo "RESULT: FAIL"
  echo "[pack] phase 1: collect-only rc=${COLLECT_RC}"
  exit 1
fi

NODE_COUNT="$(grep -cE '^tests/.*::' "$COLLECT_LOG" || true)"
NODE_COUNT="${NODE_COUNT:-0}"
echo "[pack] collected nodes: $NODE_COUNT (measured baseline ${MEASURED_NODE_BASELINE}, floor ${EXPECTED_NODE_FLOOR})"

if [ "$NODE_COUNT" -lt "$EXPECTED_NODE_FLOOR" ]; then
  tail -8 "$COLLECT_LOG" | sed 's/^/  /'
  echo "RESULT: FAIL"
  echo "[pack] phase 1: collected ${NODE_COUNT} node(s) < the floor ${EXPECTED_NODE_FLOOR} — a leg is missing"
  exit 1
fi

if [ "$NODE_COUNT" -ne "$MEASURED_NODE_BASELINE" ]; then
  echo "[pack] NOTICE: the node count MOVED (${NODE_COUNT} != baseline ${MEASURED_NODE_BASELINE}) — the pin grew under concurrent extension; the floor + presence checks still hold"
fi

for nid in "${REQUIRED_NODE_IDS[@]}"; do
  if ! grep -qF "$nid" "$COLLECT_LOG"; then
    echo "RESULT: FAIL"
    echo "[pack] phase 1: required node id absent from the collect output: $nid"
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
run_pytest "$INNER_TIMEOUT_S" "$PHASE2_LOG" \
  "$PIN_FILE" -q --tb=short -p no:randomly --override-ini="addopts=" || EXIT_CODE=$?

tail -5 "$PHASE2_LOG" | sed 's/^/  /'
grep -E "^(FAILED|ERROR) " "$PHASE2_LOG" | sed 's/^/  /' || true
PASSED_COUNT="$(grep -oE '[0-9]+ passed' "$PHASE2_LOG" | tail -1 | cut -d' ' -f1 || true)"
PASSED_COUNT="${PASSED_COUNT:-0}"
echo "[pack] passed nodes: ${PASSED_COUNT} / collected ${NODE_COUNT}"
echo "[timer] phase 2 done: $(elapsed_s)s elapsed / budget ${BUDGET_S}s"

# --- verdict --------------------------------------------------------------
echo "[timer] total wall: $(elapsed_s)s / budget ${BUDGET_S}s"
if [ "$EXIT_CODE" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  echo "[pack] the pin run hit the pack's inner timeout (${INNER_TIMEOUT_S}s)"
  exit 124
fi
if [ "$EXIT_CODE" -ne 0 ]; then
  echo "RESULT: FAIL"
  echo "[pack] phase 2: the class-containment pin is RED — rc=${EXIT_CODE}"
  exit 1
fi
if ! grep -qE "^[1-9][0-9]* passed" "$PHASE2_LOG"; then
  echo "RESULT: FAIL"
  echo "[pack] VACUOUS — the summary carries no 'N passed' with N >= 1"
  exit 1
fi
echo "[pack] ${PASSED_COUNT} node(s) passed, ${NODE_COUNT} collected, $(elapsed_s)s wall"
echo "RESULT: PASS"
exit 0
