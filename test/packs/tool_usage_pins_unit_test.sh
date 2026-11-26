#!/usr/bin/env bash
# =============================================================================
# Test Pack: tool_usage_pins_unit_test
#
# Lane: feature/pack-coverage-registration (base `40301a5e3`) (re-verified at this base) — the
#       PACKS.md + pack-scripts coverage registration wave, FAMILY A2
#       "TOOL-USAGE" (10 files, one pack script + one PACKS.md block).
#
# WHAT IT PINS
#   tests/unit/repositories/test_tool_usage_repository.py          (15 nodes)
#   tests/unit/repositories/test_tool_usage_seed_lifecycle.py      (79 nodes)
#   tests/unit/repositories/test_tool_usage_seed_optout.py          (6 nodes)
#   tests/unit/routers/test_tool_usage_router.py                   (16 nodes)
#   tests/unit/services/test_tool_usage_digest_determinism.py      (41 nodes)
#   tests/unit/services/test_tool_usage_digest_root.py               (9 nodes)
#   tests/unit/services/test_tool_usage_observation.py             (21 nodes)
#   tests/unit/tools/test_tool_usage_digest_registration.py       (24 nodes)
#   tests/unit/services/test_tool_usage_digest_allow_semantics.py  (14 nodes)
#   tests/unit/services/test_tool_usage_digest_artifact_schema.py  (10 nodes)
#   Total: 235 nodes (mechanical `--collect-only` census re-stamped after the
#   per-section weight heading/disclosure + artifact-empty trigger + schema-1
#   reason pin fix:
#   235 collected, RC=0, 0 collection errors; per-file 15+79+6+16+41+9+21+24+14+10
#   = 235, the slice count equals the sum of the per-file counts).
#
#   The family is the tool-usage-metrics-p1 lane's (ENSEMBLE-37) unit
#   surface. From the files' own docstrings:
#     - the `tool_usage_daily` REPOSITORY (Phase 1): a NAMES-ONLY schema
#       (no args/argv/content/tool_call_id/instance/session column may be
#       added), upsert aggregation + PK-conflict semantics, the A-6
#       dual-dialect coalescing (two same-PK deltas in ONE batch => one
#       summed row), sentinel round-trips, empty-batch no-op, and the
#       door read shapes + top-50 agents cap + project filter;
#     - the BOOT-TIME SEEDER LIFECYCLE (T5/T6/T7 + the G5 gate): uuid5
#       seed idempotency (seed twice => ONE row) on BOTH dialects,
#       operator-intent survival (an existing row is a no-op in ANY
#       state), the adapter-config shape (`schedule='0 8 * * *'`, NOT
#       `when=`), the startable RUNNING status, GC-safety (an enabled
#       seeded row is not a GC candidate), T6 boot ordering (the seeder
#       call is BEFORE `start_all()` and can never abort boot), and the
#       managed-cache tier;
#     - the D-5 DB-TIER OPT-OUT LADDER (council C6): an EMPTY
#       managed-values cache + a DB row `false` => seeding SUPPRESSED
#       (the defect scenario), an absent row => default True, env
#       `false` wins over a DB `true` row, a malformed row =>
#       FAIL-CLOSED, and a missing `app_settings` table => fail-OPEN;
#     - the `/api/tool-usage/*` READ DOOR (Phase 1): the auth-replication
#       invariant (the door's `_require_auth_on` body byte-matches the
#       metrics door's), the 1 MB response-size guard, the top-50 agents
#       cap, the closed error vocabulary + 503 under auth-OFF, and the
#       days clamp [1,90] / 404 unknown_tool / `__none__` filter;
#     - the DIGEST UNIT + DETERMINISM (T7 a): byte-identical output
#       modulo the generated-at header on BOTH dialects, the five binding
#       ordering assertions (score DESC then tool_name ASC inside a
#       project section, project sections in `project_id` ASC order, agent
#       lines in `agent_id` ASC order, the score basis pinned to
#       `calls_7d`), and the frozen D-7 windows ("last-24h" = the
#       complete previous UTC day; "cumulative-week" = [D-7, D-1]);
#     - the DIGEST ROOT RESOLUTION (council C5, corrected): the digest
#       root resolves to the INSTALL ROOT that owns `releases/`, via the
#       house resolver `resolve_live_install_root()` (the frozen branch is
#       the READ anchor only); `ENSEMBLE_TOOL_USAGE_DIGEST_ROOT` keeps
#       precedence over both;
#     - the OBSERVATION TAP + OFF-LOOP FLUSHER (Phase 1): the NAMES-ONLY
#       sink signature (no argument payload), the 256-char tap choke
#       (overflow counter + throttled WARNING), the flusher's
#       drain-before-dispose ordering / tail-flush on kill-switch /
#       emergency-flush at bound / never-drop / respawn-on-death, the
#       MANAGED_KEYS registration shape, and the collection kill-switch
#       absent/present/unparseable semantics;
#     - the `tool_usage_digest` REGISTRATION + CALL SURFACE (T7 b): the
#       THREE-STEP registration seam pinned independently (the
#       `@register_tool_category("scheduling")` + `@tool` decorator pair
#       AND ITS ORDER, the `CATEGORY_MODULES["scheduling"]` module path as
#       a `daemon.tools.*` string, and the construction hunk's PLACEMENT
#       before `create_help_tool` / `scan_tools_for_full_docs` /
#       `_apply_tool_filter`), because a dropped step is SILENTLY
#       INVISIBLE; plus the T3 callable/status-string contract and the
#       frozen-fallback-universe membership regression pin.
#
# CONTRACT (house shape — mirrors test/packs/por_b2_wedge_repro_pins_unit_test.sh)
#   - Dual-layer timeout: inner `timeout 280` around pytest; the caller
#     wraps with `timeout 300`.
#   - `set -u` ONLY, never `set -e`: under `set -e` a bare `X=$?` after a
#     failing pytest NEVER executes, so `RESULT: FAIL` becomes
#     unreachable. The pytest
#     exit code is taken from `PIPESTATUS[0]`.
#   - Serial + deterministic: `-p no:xdist -p no:randomly`.
#   - The repo's OWN pytest config is kept as-is (the `-m 'not integration
#     and not postgres and not slow and not conformance'` marker filter
#     and `timeout = 30`): no `--override-ini`, so the pack runs exactly
#     what a plain `pytest <files>` from the worktree root would run, and
#     the census that produced the node count was measured under the
#     SAME config. (The slowest node is 2.70s against the repo's 30s
#     timeout — a 10x margin, so the repo's own bound is not relaxed.)
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob is a SILENT NO-OP in
#     bash — bash does not glob env-var names in `env -u`). The scrub
#     drops the ambient POSTGRES coords AND the live OPENAI_API_KEY;
#     the two PostgreSQL legs read the WORKTREE `.env` FILE through
#     `tests/helpers/tool_usage_t7_pg_fence.py`, which hard-refuses any
#     database that is not exactly `ensemble_dev` and any non-loopback
#     host, and runs inside a throwaway schema dropped with CASCADE at
#     teardown — so an ambient prod coordinate can never redirect the
#     engine, and the scrub cannot break the dev fence.
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY (never
#     `env -u` on it: stripping the ambient guard makes the boot sweep
#     SIGTERM/SIGKILL live processes).
#   - SLICING PROOF: the pack re-derives its own node count with
#     `--collect-only` and FAILS if it drifts from the declared 235 — a
#     pack that silently runs a different node set is inadmissible. The
#     SAME collect leg yields the per-file census for free (the node-id
#     prefix), so each of the 10 files is counted individually and every
#     per-file count is checked against its declared value; the slice
#     count must equal the sum of the per-file counts.
#   - VACUOUS-VERDICT GUARD: zero passed tests or no summary line = FAIL
#     (a pack that can PASS from an empty run is worthless).
#   - Outcome counts are read from the pytest SUMMARY LINE only. No
#     `-rA`: under `-rA` pytest captures ERROR-prefixed LOG RECORDS
#     emitted by the code under test, so `grep -c '^ERROR '` manufactures
#     a FALSE RED. The summary line's own tokens are authoritative.
#   - Output: header, slicing proof, per-file census, pytest body,
#     `RESULT: PASS|FAIL|TIMEOUT`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
#
# SELF-TEST SEAM (the negative control — NOT for normal use)
#   `PACK_UNIT_FILES` (space-separated) + `PACK_EXPECTED_NODES` override
#   the declared slice so the pack's OWN verdict path can be exercised on
#   a deliberately failing selection. With either variable set the pack
#   prints a loud SELFTEST banner and SKIPS the per-file census check
#   (the frozen family census is not pinned in that mode). The DEFAULT
#   invocation — `bash test/packs/tool_usage_pins_unit_test.sh` with no
#   environment override — is the pinned family run and is unaffected.
#
#   HARD GATE (fail-safe against a stray export): the overrides are honored
#   ONLY when `PACK_SELFTEST=1` is ALSO set. Without the sentinel an
#   override present in the environment is IGNORED (the pinned family slice
#   stays the gate) and reported with ONE loud WARNING line — the pack does
#   NOT fail on a stray export, because the pinned values ARE the fail-safe.
# =============================================================================
set -u

PACK_NAME="tool_usage_pins_unit_test"
EXPECTED_NODES=235

# ── the declared family slice + its frozen per-file census ────────────────
UNIT_FILES=(
  tests/unit/repositories/test_tool_usage_repository.py
  tests/unit/repositories/test_tool_usage_seed_lifecycle.py
  tests/unit/repositories/test_tool_usage_seed_optout.py
  tests/unit/routers/test_tool_usage_router.py
  tests/unit/services/test_tool_usage_digest_determinism.py
  tests/unit/services/test_tool_usage_digest_root.py
  tests/unit/services/test_tool_usage_observation.py
  tests/unit/tools/test_tool_usage_digest_registration.py
  tests/unit/services/test_tool_usage_digest_allow_semantics.py
  tests/unit/services/test_tool_usage_digest_artifact_schema.py
)
UNIT_EXPECTED=(
  15
  79
  6
  16
  41
  9
  21
  24
  14
  10
)

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

# ── the SELF-TEST seam (negative control only) ───────────────────────────
# HARD-GATED (fail-safe against a stray export): the overrides are honored
# ONLY when PACK_SELFTEST=1 is ALSO set. Without the sentinel an override
# present in the environment is IGNORED — the pinned family slice stays the
# gate — and reported with ONE loud WARNING line. The pack does NOT fail on
# a stray export: the pinned values ARE the fail-safe.
# With the sentinel, EACH override is honored INDEPENDENTLY:
#   PACK_UNIT_FILES     -> replaces the declared file list (the per-file
#                          census no longer applies to the replacement);
#   PACK_EXPECTED_NODES -> replaces the declared node expectation while the
#                          file list stays PINNED, so a wrong expectation
#                          is caught by the slicing proof.
SELFTEST=0
FILES_REPLACED=0
PINNED_UNIT_FILES="${UNIT_FILES[*]}"
if [ "${PACK_SELFTEST:-0}" = "1" ]; then
  if [ -n "${PACK_UNIT_FILES:-}" ]; then
    SELFTEST=1
    if [ "${PACK_UNIT_FILES}" != "${PINNED_UNIT_FILES}" ]; then
      # shellcheck disable=SC2206
      UNIT_FILES=(${PACK_UNIT_FILES})
      UNIT_EXPECTED=()
      FILES_REPLACED=1
    fi
  fi
  if [ -n "${PACK_EXPECTED_NODES:-}" ]; then
    SELFTEST=1
    EXPECTED_NODES="${PACK_EXPECTED_NODES}"
  fi
  if [ "$SELFTEST" -eq 1 ]; then
    echo "SELFTEST MODE: the declared family census is NOT pinned in this run."
  fi
elif [ -n "${PACK_UNIT_FILES+x}" ] || [ -n "${PACK_EXPECTED_NODES+x}" ]; then
  echo "WARNING: PACK_UNIT_FILES/PACK_EXPECTED_NODES set without PACK_SELFTEST=1 — overrides IGNORED, running the pinned family slice"
fi

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

# ── the SLICING PROOF + the per-file census (ONE collect leg) ────────────
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

# ── the per-file census, derived from the SAME collect leg ───────────────
# The node-id prefix gives each file's count for free (no second pytest
# invocation). Every file's count must equal its declared value, and the
# per-file counts must sum to the slice count.
echo
echo "--- per-file census (derived from the same collect leg) ---"
CENSUS_SUM=0
i=0
while [ "$i" -lt "${#UNIT_FILES[@]}" ]; do
  f="${UNIT_FILES[$i]}"
  n="$(awk -F'::' -v want="$f" '$1 == want { c++ } END { print c + 0 }' "$COLLECT_OUT")"
  if [ "$FILES_REPLACED" -eq 1 ]; then
    echo "  $f -> $n nodes (selftest mode: not pinned)"
  else
    exp="${UNIT_EXPECTED[$i]}"
    if [ "$n" -ne "$exp" ]; then
      echo "  CENSUS MISMATCH: $f -> $n nodes, expected $exp"
      CENSUS_SUM=-1
      break
    fi
    echo "  $f -> $n nodes (expected $exp) OK"
  fi
  CENSUS_SUM=$((CENSUS_SUM + n))
  i=$((i + 1))
done
if [ "$FILES_REPLACED" -eq 0 ]; then
  if [ "$CENSUS_SUM" -ne "$EXPECTED_NODES" ]; then
    echo "CENSUS: FAIL (per-file sum=$CENSUS_SUM != slice count=$EXPECTED_NODES)"
    rm -f "$COLLECT_OUT"
    echo "RESULT: FAIL (slicing proof)"
    exit 1
  fi
  echo "CENSUS: OK (10 files, per-file sum=$CENSUS_SUM == slice count=$EXPECTED_NODES)"
fi
rm -f "$COLLECT_OUT"

echo
echo "--- lane A2: tool-usage repository / seeder lifecycle / opt-out ladder / read door / digest determinism / digest root / observation tap / digest registration / digest allow-semantics / digest artifact-schema ---"
OUT="$(mktemp)"
timeout 280 env \
  "${SCRUB[@]}" \
  ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
  ENSEMBLE_SELF_ENV=dev \
  PYTHONDONTWRITEBYTECODE=1 \
  "$PYTHON_BIN" -m pytest \
    -q \
    -p no:randomly \
    -p no:xdist \
    --tb=short \
    -rfs \
    --color=no \
    "${UNIT_FILES[@]}" 2>&1 | tee "$OUT"
PYTEST_EXIT="${PIPESTATUS[0]}"

# ── the VACUOUS-VERDICT GUARD ───────────────────────────────────────────
# NOTE: under `-q` the summary line STARTS with the count ("200 passed,
# ..."), so the extraction below anchors on the TOKEN, not on a preceding
# non-digit. The counts come from the SUMMARY LINE only — never from a
# grep of `^ERROR ` (with `-rA` that greps pytest's captured LOG RECORDS
# and manufactures a false RED).
SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT" | tail -1)"
PASSED="$(printf '%s\n' "$SUMMARY" | grep -oE '[0-9]+ passed' | head -1 | grep -oE '^[0-9]+')"
FAILED="$(printf '%s\n' "$SUMMARY" | grep -oE '[0-9]+ failed' | head -1 | grep -oE '^[0-9]+')"
SKIPPED="$(printf '%s\n' "$SUMMARY" | grep -oE '[0-9]+ skipped' | head -1 | grep -oE '^[0-9]+')"
rm -f "$OUT"

if [ "$PYTEST_EXIT" -eq 124 ]; then
  echo
  echo "RESULT: TIMEOUT (pytest hit the inner 280s timeout)"
  exit 124
fi
if [ "$PYTEST_EXIT" -ne 0 ]; then
  echo
  echo "SUMMARY: ${SUMMARY:-<none>}"
  echo "RESULT: FAIL (pytest exit=$PYTEST_EXIT)"
  exit 1
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
echo "SUMMARY: ${SUMMARY}  (passed=${PASSED:-0} failed=${FAILED:-0} skipped=${SKIPPED:-0})"

echo "RESULT: PASS"
exit 0
