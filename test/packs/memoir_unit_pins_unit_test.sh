#!/usr/bin/env bash
# =============================================================================
# Test Pack: memoir_unit_pins_unit_test
#
# Lane: feature/pack-coverage-registration (base `40301a5e3`) (re-verified at this base) — PACKS.md
#       coverage wave B, family B3 (memoir pins).
#
# WHAT IT PINS
#   tests/unit/test_memoir_briefing_queue_counts.py
#   tests/unit/test_memoir_proposal_decision.py
#   Total: 67 nodes (mechanical `--collect-only` census at this freeze:
#   67 collected, RC=0, 0 collection errors; per-file 31 / 36).
#
#   Briefing approval-queue COUNT lines (m1) — the two lines the briefing
#     renders for the pending persona/proposal approval queues: the CHEAP
#     count (a `SELECT COUNT(*)` with the SAME predicate `list_pending`
#     selects with, asserted on the EMITTED SQL), ALWAYS-PRESENT-BUT-
#     SILENT-WHEN-ZERO (at zero counts the rendered body is byte-identical
#     to the body rendered with no count parameters), the ZONE each line
#     belongs to and the SURFACE it names, the proposals-zone MEASUREMENT
#     (zone overhead, measured first inside the <=1,000-char bound), the
#     untouched pinned LADDER (fit dynamic -> trim dynamic -> trim
#     proposals -> withhold; 24,000-char ceiling still binds), and the build
#     path (ONE guarded COUNT per queue; a failed count degrades to "no
#     line", never an aborted build; `list_pending` is never called so the
#     count cannot materialize the lineage).
#
#   Keepsake-proposal DECISION axis (Memoir Store v2) — the review store:
#     the SCHEMA (three nullable decision columns on `memoir_proposal_ledger`
#     plus a model-bound CHECK on the two-token decision vocabulary; the
#     DELIVERY vocabulary stays a separate closed four-token set), the
#     EXCLUSION (a DECIDED row leaves the OPEN set for BOTH the briefing's
#     zone render AND the notify target, and the exclusion lives in ONE
#     place — the statement builder — so the two paths cannot drift), the
#     CAS (the notify claim keeps `notification_status == 'pending'` and
#     ADDS `decision_status IS NULL`, so a proposal decided between the
#     selection and the claim is never announced), and the SERVICE surface
#     (`count_pending_reviews` — a cheap COUNT, never a row materialization
#     — and `record_review`, returning a plain dict and refusing with a
#     typed `.code`).
#
# CONTRACT (house shape — mirrors por_b2_wedge_repro_pins_unit_test)
#   - Dual-layer timeout: inner `timeout 280`; caller wraps with 300.
#   - Serial + deterministic: `-p no:xdist -p no:randomly`.
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob is a SILENT NO-OP in bash
#     — bash does not glob env-var names in `env -u`).
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY (never
#     `env -u` on it: stripping the ambient guard makes the boot sweep
#     SIGTERM/SIGKILL live processes).
#   - Repo addopts KEPT (the `-m 'not integration and not postgres ...'`
#     marker filter) — NEITHER file carries an excluded marker (no
#     `pytest.mark.*` at all), so the filter deselects nothing and the
#     census that produced 67 was measured under the SAME filter; the pack's
#     node count and the census are comparable. NO `--override-ini=addopts=`
#     override was needed or used.
#   - SLICING PROOF: the pack re-derives its own node count with
#     `--collect-only` and FAILS if it drifts from the declared 67 — a pack
#     that silently runs a different node set is inadmissible.
#   - VACUOUS-VERDICT GUARD: zero passed tests or no summary line = FAIL
#     (a pack that can PASS from an empty run is worthless).
#   - Output: header, pytest body, `RESULT: PASS|FAIL|TIMEOUT`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
#
# RED-ABILITY (negative control, executed at this freeze): a scratch copy of
#   this script pointed at a deliberately failing scratch test under a
#   `mktemp -d` OUTSIDE the repo reports `RESULT: FAIL` and exits 1 — the
#   pack can go red, so a green run is a gate and not a tautology.
# =============================================================================
set -u

PACK_NAME="memoir_unit_pins_unit_test"
EXPECTED_NODES=67

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
  tests/unit/test_memoir_briefing_queue_counts.py
  tests/unit/test_memoir_proposal_decision.py
)

for f in "${UNIT_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    echo "FATAL: required test file missing: $f" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
done

# ── the per-variable env scrub (never a POSTGRES_* glob) ─────────────────
# NOTE (scrub completeness): the house template's list omits several
#   credential-bearing vars that ARE present in this deployment's ambient
#   env. A negative-control probe (scratch file OUTSIDE the repo) proved
#   that the template list leaves SYSTEM_ENCRYPTION_KEY, PLANE_MCP_API_KEY,
#   EMBEDDING_API_KEY, SKILL_EVOLUTION_EMBEDDING_API_KEY,
#   BLUEPRINT_EMBEDDING_API_KEY and LIGHTRAG_API_KEY visible to the test
#   process. The vars below are added so this pack's fence is actually in
#   force, not merely declared.
#
# NOTE (deliberately NOT scrubbed): PLANE_BASE_URL / PLANE_MCP_URL /
#   EMBEDDING_BASE_URL / OPENAI_BASE_URL — they are endpoints, not
#   credentials, and tests/conftest.py branches on PLANE_BASE_URL; scrubbing
#   them would change behavior rather than fence it.
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
echo "--- lane B3: memoir briefing queue-count lines / keepsake-proposal decision axis ---"
# NOTE: `--ff` is the pytest>=9 spelling of `--rf` (--failed-first);
#       pytest 9.0.2 REMOVED the `--rf` short form (unrecognized argument).
#
# NOTE (bash parsing — READ BEFORE EDITING): this comment MUST stay OUTSIDE
#   the `env \` continuation below. A `#` comment placed INSIDE a
#   backslash-continued command TERMINATES that command in bash: the
#   `timeout 280 env ...` prefix becomes a command with NO command to run
#   (so `env` PRINTS THE ENTIRE ENVIRONMENT — including live secrets — and
#   exits 0), and the pytest invocation that follows the comment becomes a
#   SEPARATE command that runs with NO env scrub and NO inner timeout.
#   That silently defeats BOTH the per-variable scrub and the inner
#   `timeout 280` (only the caller's outer `timeout 300` would bound it).
#   The house template `test/packs/por_b2_wedge_repro_pins_unit_test.sh`
#   carries this shape; see the lane report.
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
# NOTE: under `-q` the summary line STARTS with the count ("31 passed, ..."),
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
