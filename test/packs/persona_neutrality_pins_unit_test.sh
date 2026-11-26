#!/usr/bin/env bash
# =============================================================================
# Test Pack: persona_neutrality_pins_unit_test
#
# Lane: feature/pack-coverage-registration (base `40301a5e3`) (re-verified at this base) — PACKS.md +
#       pack-scripts coverage registration wave.
#
# WHAT IT PINS
#   tests/unit/test_persona_neutrality.py  (4 nodes)
#
#   - test_the_shipped_trees_carry_no_persona_lore_titles — every hit DERIVED
#     at test time by `git grep -nwIi` over the SHIPPED trees must be covered
#     by its FILE + TOKEN allowlist entry (the hit set is never transcribed).
#   - test_the_guard_file_carries_no_literal_token — the guard itself carries
#     no literal lore token (it assembles them from fragments) and never
#     matches its own scan.
#   - test_the_scan_scope_excludes_the_historical_ruling_trail — `.agents/**`
#     is the ruling trail, never in the scanned surface and never in the
#     allowlist.
#   - test_the_allowlist_grants_tokens_per_file_not_per_corpus — every
#     allowlist key is a SINGLE file and every entry carries a WHY reason.
#
# TRUE VERDICT AT THIS FREEZE: FAIL (1 failed, 3 passed) — a KNOWN
# PRE-EXISTING RED: the guard reds on TRACKED files carrying the lore title
# at base `ed8ab508c`. This pack is registered as an honest known-red. It is
# NOT softened by selection tricks: the pinned slice is exactly the one file,
# and the census is re-derived by the pack itself on every run.
#
# CONTRACT (house shape — mirrors b4c_services_unit_core_pins_unit_test.sh)
#   - Dual-layer timeout: inner `timeout 280`; caller wraps with 300.
#   - Serial + deterministic: `-p no:xdist -p no:randomly`.
#   - NO `--ff`: pytest's failed-first ordering is driven by the SHARED
#     `.pytest_cache/lastfailed`, which would couple this pack to concurrent
#     sibling lanes running in the same worktree.
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
#     `--collect-only` and FAILS if it drifts from the declared 4 — a pack
#     that silently runs a different node set is inadmissible.
#   - TIMEOUT IS CHECKED BEFORE THE VACUOUS GUARD: a 124 exit is reported as
#     `RESULT: TIMEOUT` (exit 124), never misread as a vacuous failure.
#   - VACUOUS-VERDICT GUARD: zero passed tests or no summary line = FAIL
#     (a pack that can PASS from an empty run is worthless).
#   - Output: header, pytest body, `RESULT: PASS|FAIL|TIMEOUT`.
#   - Exit: 0 PASS / 1 FAIL / 124 TIMEOUT.
# =============================================================================
set -u

PACK_NAME="persona_neutrality_pins_unit_test"
EXPECTED_NODES=4

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
  tests/unit/test_persona_neutrality.py
)
if [ "${#UNIT_FILES[@]}" -eq 0 ]; then
  echo "FATAL: empty test file list" >&2
  echo "RESULT: FAIL"
  exit 1
fi

echo "declared file set (${#UNIT_FILES[@]} file(s)), expected nodes: ${EXPECTED_NODES}"

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
  -u OPENAI_API_KEY -u SYSTEM_ENCRYPTION_KEY -u PLANE_MCP_API_KEY
  -u EMBEDDING_API_KEY -u SKILL_EVOLUTION_EMBEDDING_API_KEY
  -u BLUEPRINT_EMBEDDING_API_KEY -u LIGHTRAG_API_KEY
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
echo "--- persona neutrality: the shipped-tree lore-title guard (pytest leg) ---"
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
    --color=no \
    "${UNIT_FILES[@]}" 2>&1 | tee "$OUT"
PYTEST_EXIT="${PIPESTATUS[0]}"

# ── the summary extraction ───────────────────────────────────────────────
# NOTE: under `-q` the summary line STARTS with the count ("3 passed, ..."),
# so the extraction anchors on the TOKEN, not on a preceding non-digit.
SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT" | tail -1)"
PASSED="$(printf '%s\n' "$SUMMARY" | grep -oE '[0-9]+ passed' | head -1 | grep -oE '^[0-9]+')"
rm -f "$OUT"

# ── the TIMEOUT check comes FIRST (a 124 is never a vacuous verdict) ────
if [ "$PYTEST_EXIT" -eq 124 ]; then
  echo
  echo "RESULT: TIMEOUT (pytest leg hit the inner 280s timeout)"
  exit 124
fi

# ── the VACUOUS-VERDICT GUARD ───────────────────────────────────────────
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
    echo
    echo "RESULT: PASS"
    exit 0
    ;;
  *)
    echo
    echo "RESULT: FAIL (pytest exit=$PYTEST_EXIT)"
    exit 1
    ;;
esac
