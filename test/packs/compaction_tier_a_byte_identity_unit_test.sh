#!/usr/bin/env bash
# Test Pack: compaction_tier_a_byte_identity_unit_test
#
# Purpose: gate the TIER-A tiktoken de-duplication in the compaction
# engine (row2-2a). The lane removed the redundant WHOLE-LIST token
# counts from three counting paths in daemon/compaction.py:
#
#   * compact_state's count sites — the SELECTABLE pool was measured up
#     to FIVE times per compaction (same list, same value),
#   * _apply_ceiling_rule — O(S²) re-summing → O(S) incremental savings,
#   * emergency_truncate — O(H·N) + O(P·N) whole-list re-counts in its
#     two loops → O(N + H + P) running count,
#
# plus the usage-path pre-filter in daemon/services/instance_messaging.py
# (a cheap "did the history change?" comparison ahead of the count).
#
# INVARIANT (deep-dive §2.4): every emitted count and every gate/loop
# DECISION stays byte-identical to the pre-change implementation.
#
# Scope — EXACTLY these three files, nothing else, no discovery:
#   tests/unit/test_compaction.py
#     (extended with the equivalence sweep: class TestTierAByteIdentity
#      compares the NEW implementation against a VERBATIM copy of the
#      pre-change code — normal multi-message corpora + the pathological
#      single-character-class runs (letters / whitespace / symbols))
#   tests/unit/test_compaction_tier_a_callcount_pin.py
#     (the counting-work MUTATION PIN: instrument the estimator and
#      assert the whole-list re-encode does NOT come back inside any
#      loop of emergency_truncate / _apply_ceiling_rule)
#   tests/unit/test_usage_path_tier_a_dedup.py
#     (the usage path's cheap pre-filter — the emitted value is
#      unchanged, the re-count of an unchanged history is skipped)
#
# Contract:
#   - Dual-layer timeout: inner `timeout 240` around pytest; the caller
#     wraps with `timeout 300 bash <this pack>`.
#   - Serial: no -n passed AND xdist plugin disabled outright (-p no:xdist).
#   - Deterministic order: -p no:randomly.
#   - MANDATORY env scrub — PER-VARIABLE (a `POSTGRES_*` / `OPENAI_*`
#     glob is a silent no-op in bash). The scrub covers the CREDENTIAL
#     class — OPENAI_API_KEY — NOT every OPENAI_* name: OPENAI_BASE_URL,
#     OPENAI_MODEL and ENSEMBLE_AUTH_ISSUER are REQUIRED by the house
#     ``load_config()``.
#   - Repo addopts cleared (--override-ini="addopts=").
#   - THE VACUOUS-VERDICT GUARD: the pack FAILS LOUDLY when pytest
#     reports zero passed tests or no summary at all — "PASS" must mean
#     "the suite ran".
#   - Output: header line, pytest body, RESULT: PASS|FAIL|TIMEOUT.
#   - Exit: 0 (PASS) / 1 (FAIL) / 124 (TIMEOUT).
#
# Expected runtime: < 2 min (unit class; the equivalence sweep encodes
# the pathological corpora through tiktoken).
# =============================================================================
set -u

echo "=== Test Pack: compaction_tier_a_byte_identity_unit_test ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/../.."
cd "$PROJECT_DIR" || { echo "RESULT: FAIL"; exit 1; }

PYTEST_BIN="$PROJECT_DIR/.venv/bin/pytest"
if [ ! -x "$PYTEST_BIN" ]; then
  echo "FATAL: .venv/bin/pytest not found at $PYTEST_BIN" >&2
  echo "RESULT: FAIL"
  exit 1
fi

for f in tests/unit/test_compaction.py \
         tests/unit/test_compaction_tier_a_callcount_pin.py \
         tests/unit/test_usage_path_tier_a_dedup.py; do
  if [ ! -f "$PROJECT_DIR/$f" ]; then
    echo "FATAL: required test file missing: $f" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
done

# ── the per-variable scrub (the LIVE credentials + the LIVE PG coords) ──
# NEVER `env -u POSTGRES_*` — bash does not glob env names, so that is
# a silent no-op (the house rule).
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  # The LIVE credential — the F7 class (an env-capturing harness leaked
  # the live OPENAI_API_KEY into scratch files).
  -u OPENAI_API_KEY
  -u OPENAI_REASONING_ECHO_DISABLED_MODELS
  # NOT scrubbed, DELIBERATELY: OPENAI_BASE_URL / OPENAI_MODEL /
  # ENSEMBLE_AUTH_ISSUER are REQUIRED by the house ``load_config()``.
)

# ── the FENCE SELF-TEST (the discriminating pair) ───────────────────────
SENTINEL="SENTINEL-$$-must-not-reach-the-child"
PROBE='import os,sys; sys.exit(0 if os.environ.get("OPENAI_API_KEY") is None else 1)'

export OPENAI_API_KEY="$SENTINEL"

if env "${SCRUB[@]}" python3 -c "$PROBE"; then
  echo "fence self-test: SCRUBBED child sees no OPENAI_API_KEY  [OK]"
  SCRUB_LEG="OK"
else
  echo "fence self-test: SCRUBBED child still sees OPENAI_API_KEY  [FAIL]"
  SCRUB_LEG="FAIL"
fi

# THE CONTROL — the same child WITHOUT the scrub MUST see the sentinel.
if python3 -c "$PROBE"; then
  echo "fence self-test: CONTROL child (no scrub) does NOT see the sentinel  [FAIL]"
  SCRUB_LEG="FAIL"
else
  echo "fence self-test: CONTROL child (no scrub) sees the sentinel  [OK — the scrub leg is meaningful]"
fi

if [ "$SCRUB_LEG" != "OK" ]; then
  echo "RESULT: FAIL (the env fence is not proven)"
  exit 1
fi

# NOTE: set -e is deliberately NOT used — a non-zero pytest exit must
# fall through to the RESULT mapping below.
OUT="$(mktemp)"
timeout 240 env \
  "${SCRUB[@]}" \
  PYTEST_TIMEOUT=240 \
  "$PYTEST_BIN" \
    -p no:randomly \
    -p no:xdist \
    -v \
    --tb=short \
    --color=no \
    --override-ini="addopts=" \
    --override-ini="timeout=240" \
    tests/unit/test_compaction.py \
    tests/unit/test_compaction_tier_a_callcount_pin.py \
    tests/unit/test_usage_path_tier_a_dedup.py \
    > "$OUT" 2>&1
RC=$?

cat "$OUT"

SUMMARY="$(grep -E '^[0-9]+ passed|passed|failed|error' "$OUT" | tail -3)"
PASSED="$(grep -oE '[0-9]+ passed' "$OUT" | tail -1 | grep -oE '[0-9]+' || true)"

if [ "$RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  rm -f "$OUT"
  exit 124
elif [ "$RC" -ne 0 ]; then
  echo "RESULT: FAIL"
  echo "Exit: $RC"
  rm -f "$OUT"
  exit 1
fi

# ── the vacuous-verdict guard ──────────────────────────────────────────
if [ -z "$PASSED" ] || [ "$PASSED" -eq 0 ]; then
  echo "RESULT: FAIL (vacuous verdict — pytest reported no passing test)"
  echo "Summary: ${SUMMARY:-<none>}"
  rm -f "$OUT"
  exit 1
fi

echo "Summary: ${SUMMARY}"
echo "RESULT: PASS"
rm -f "$OUT"
exit 0
