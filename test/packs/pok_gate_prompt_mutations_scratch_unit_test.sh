#!/usr/bin/env bash
# =============================================================================
# Test Pack: pok_gate_prompt_mutations_scratch_unit_test
#
# Lane: feature/pack-coverage-registration (base `8b646ae37`) (re-verified at this base) — PACKS.md
#       coverage registration, final item (2): the v0.31.54 residuals lane's
#       hermetic test file that landed UNREGISTERED
#       (`tests/unit/test_pok_gate_prompt_mutations_scratch.py`).
#
# WHAT IT PINS
#   tests/unit/test_pok_gate_prompt_mutations_scratch.py
#   Total: 5 nodes (mechanical `--collect-only` census at this freeze:
#   5 collected, RC=0, 0 collection errors).
#
#   The PER-RUN SCRATCH + FAIL-CLOSED RESTORE of the POK write-gate prompt
#   mutation harness (`scripts/pok_gate_prompt_mutations.py`):
#     - a TAMPERED or TRUNCATED backup is REFUSED (2 parametrized nodes:
#       tampered / truncated) — the target is never overwritten with it and
#       the guard reports the mismatch loudly;
#     - a GOOD backup IS installed (the guard is not vacuously refusing);
#     - two CONCURRENT runs against DIFFERENT throwaway checkouts (whose
#       target files start with DIFFERENT sha256 values) each end with their
#       OWN pre-run bytes and leave no per-run scratch directory behind;
#     - the per-run scratch directory is removed even when a leg FAILS.
#   The harness is launched with POK_GATE_MUTATIONS_DRY_RUN=1 — set BY THE
#   TEST on its children, never by this pack: only the pytest invocations
#   are stubbed; the backup / guard / restore / cleanup lifecycle is fully
#   exercised. No network, no database.
#
# CONTRACT (house shape — mirrors test/packs/memoir_unit_pins_unit_test.sh)
#   - Dual-layer timeout: inner `timeout 280`; caller wraps with 300.
#   - Serial + deterministic: `-p no:xdist -p no:randomly`.
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob is a SILENT NO-OP in bash
#     — bash does not glob env-var names in `env -u`).
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY (never
#     `env -u` on it: stripping the ambient guard makes the boot sweep
#     SIGTERM/SIGKILL live processes).
#   - ENSEMBLE_SELF_ENV normalised to `dev` by a plain `export` near the top
#     (the host's ambient value is `live`), with a self-verifying assert.
#   - PYTHONDONTWRITEBYTECODE=1 on every child.
#   - Repo addopts KEPT (the marker filter) — the file carries only a
#     `pytest.mark.parametrize`, so the filter deselects nothing and the
#     census and the pack's node count are comparable. NO
#     `--override-ini=addopts=` override was needed or used.
#   - SLICING PROOF: the pack re-derives its own node count with
#     `--collect-only` (counting `::` node-id lines, never `^tests/`) and
#     cross-checks it against pytest's own "N tests collected" line; FAILS
#     if it drifts from the declared 5 — a pack that silently runs a
#     different node set is inadmissible.
#   - VACUOUS-VERDICT GUARD: zero passed tests or no summary line = FAIL
#     (a pack that can PASS from an empty run is worthless).
#   - The TIMEOUT verdict (exit 124) is checked BEFORE the vacuous guard.
#   - `set -u` only (no `-e`): the pytest exit code is read from
#     `PIPESTATUS[0]` immediately after the `tee` pipeline — errexit-safe.
#   - Output: header, pytest body, `RESULT: PASS|FAIL|TIMEOUT`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
#
# NOTE (bash parsing — READ BEFORE EDITING): NO comment may sit INSIDE a
#   backslash-continued command. A `#` line inside `env \` TERMINATES the
#   command — the `timeout 280 env …` prefix then has NO command to run and
#   `env` PRINTS THE ENTIRE ENVIRONMENT (live credentials included) — and the
#   pytest invocation after the comment runs as a SEPARATE command with NO
#   scrub and NO inner timeout. Every NOTE in this file stays OUTSIDE the
#   continuations; `tests/shell_continuation_scan.py` pins it.
# =============================================================================
set -u

PACK_NAME="pok_gate_prompt_mutations_scratch_unit_test"
EXPECTED_NODES=5

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
  tests/unit/test_pok_gate_prompt_mutations_scratch.py
)

# The test copies these two inputs into its throwaway checkouts.
HARNESS_INPUTS=(
  scripts/pok_gate_prompt_mutations.py
  daemon/services/pok_gate.py
)

for f in "${UNIT_FILES[@]}" "${HARNESS_INPUTS[@]}"; do
  if [ ! -f "$f" ]; then
    echo "FATAL: required input missing: $f" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
done

# ── the per-variable env scrub (never a POSTGRES_* glob) ─────────────────
# NOTE (scrub completeness): the list carries the six credential-bearing
#   vars the house template omits but this deployment's ambient env carries
#   (SYSTEM_ENCRYPTION_KEY, PLANE_MCP_API_KEY, EMBEDDING_API_KEY,
#   SKILL_EVOLUTION_EMBEDDING_API_KEY, BLUEPRINT_EMBEDDING_API_KEY,
#   LIGHTRAG_API_KEY), so this pack's fence is in force, not merely declared.
#   The test spawns the harness with os.environ.copy(), so the scrub
#   propagates to the grandchildren.
#
# NOTE (deliberately NOT scrubbed): PLANE_BASE_URL / PLANE_MCP_URL /
#   EMBEDDING_BASE_URL / OPENAI_BASE_URL — endpoints, not credentials;
#   tests/conftest.py branches on PLANE_BASE_URL, so scrubbing it would
#   change behavior rather than fence it.
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u OPENAI_API_KEY
  -u SYSTEM_ENCRYPTION_KEY
  -u PLANE_MCP_API_KEY
  -u EMBEDDING_API_KEY
  -u SKILL_EVOLUTION_EMBEDDING_API_KEY
  -u BLUEPRINT_EMBEDDING_API_KEY
  -u LIGHTRAG_API_KEY
  -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_CLIENT_ID
  -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_DISCOVERY_URL
  -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
  -u ENSEMBLE_BASH_TRACKING_ID
  -u ENSEMBLE_SELF_ENV
)

# NORMALISE, never refuse: the host's ambient ENSEMBLE_SELF_ENV is `live` (measured
# on this host). A pack that REFUSED on the parent's ambient value would refuse to
# start for every tester — every PACKS.md Invocation cell runs
# `cd $WT && timeout 300 bash test/packs/<script>.sh` with NO prefix. So the pack
# normalises to `dev` (the value its own legs require) and the assert below can only
# fire if a future edit drops THIS export — which is exactly the drift it exists to
# catch.
export ENSEMBLE_SELF_ENV=dev

# FENCE (self-verifying): the legs below force ENSEMBLE_SELF_ENV=dev, so the child can
# never inherit the host's ambient value (measured `live` on this host). This assert makes
# that guarantee LOUD — if a future edit drops the normalising export, the pack refuses
# rather than silently running against the wrong environment.
if [ "${ENSEMBLE_SELF_ENV:-}" != "dev" ]; then
  echo "FENCE FAILURE: ENSEMBLE_SELF_ENV='${ENSEMBLE_SELF_ENV:-<unset>}' — this pack requires 'dev'." >&2
  echo "RESULT: FAIL (env fence)"
  exit 1
fi

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
# Count NODE-ID lines (`grep '::'`, never `grep '^tests/'` — a node id is
# defined by its `::`, and `grep -c` prints 0 AND exits 1 on no match, so the
# `|| true` keeps the count a plain integer).
COLLECTED="$(grep -c '::' "$COLLECT_OUT" || true)"
COLLECT_SUMMARY="$(grep -oE '^[0-9]+ tests? collected' "$COLLECT_OUT" | head -1 | grep -oE '^[0-9]+')"

if [ "$COLLECT_EXIT" -ne 0 ]; then
  echo "SLICING PROOF: FAIL (collection exit=$COLLECT_EXIT)"
  tail -25 "$COLLECT_OUT"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (slicing proof)"
  exit 1
fi
if [ -z "$COLLECTED" ] || [ "$COLLECTED" -eq 0 ]; then
  echo "SLICING PROOF: FAIL (VACUOUS — zero '::' node-id lines in the census)"
  tail -25 "$COLLECT_OUT"
  rm -f "$COLLECT_OUT"
  echo "RESULT: FAIL (slicing proof)"
  exit 1
fi
if [ -n "$COLLECT_SUMMARY" ] && [ "$COLLECT_SUMMARY" -ne "$COLLECTED" ]; then
  echo "SLICING PROOF: FAIL (census disagreement: $COLLECTED node-id lines vs pytest's '$COLLECT_SUMMARY collected')"
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
echo "--- per-run scratch + fail-closed restore of scripts/pok_gate_prompt_mutations.py ---"
# NOTE: `--ff` is the pytest>=9 spelling of `--rf` (--failed-first);
#       pytest 9.0.2 REMOVED the `--rf` short form (unrecognized argument).
#
# NOTE: this comment block sits OUTSIDE the `env \` continuation below on
#   purpose — see the header NOTE. Nothing may be inserted between the
#   `timeout 280 env \` line and the pytest invocation.
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
    --ff \
    --color=no \
    --override-ini="timeout=120" \
    "${UNIT_FILES[@]}" 2>&1 | tee "$OUT"
PYTEST_EXIT="${PIPESTATUS[0]}"

# ── the VACUOUS-VERDICT GUARD ───────────────────────────────────────────
# NOTE: under `-q` the summary line STARTS with the count ("5 passed, ..."),
# so the engine-arc house sed (`[^0-9]`-anchored) cannot extract it — the
# extraction below anchors on the TOKEN, not on a preceding non-digit.
SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT" | tail -1)"
PASSED="$(printf '%s\n' "$SUMMARY" | grep -oE '[0-9]+ passed' | head -1 | grep -oE '^[0-9]+')"
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
