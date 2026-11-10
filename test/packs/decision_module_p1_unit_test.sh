#!/usr/bin/env bash
# =============================================================================
# Test Pack: decision_module_p1_unit_test
#
# Purpose: register the Decision Module P1 lane's unit suite so the
# standard gate actually RUNS it. Before this registration the lane's
# 202+ nodes were executed by no named pack — a house-process gap, not
# cosmetics (council round 4, fold-now item 1).
#
# Scope — EXACTLY this directory, nothing else, no discovery:
#   tests/unit/decision/            (the Decision Module P1 unit suite)
#
# Contract:
#   - Dual-layer timeout: inner `timeout 280` around pytest; the caller
#     wraps with `timeout 300 bash <this pack>`.
#   - Serial: no -n passed AND xdist plugin disabled outright (-p no:xdist).
#   - Deterministic order: -p no:randomly.
#   - MANDATORY env scrub — the ambient shell may carry LIVE-PROD PG
#     coords, the LIVE OPENAI_API_KEY (the F7 class) and the lane's own
#     DECISION_* signal. The scrub is PER-VARIABLE (a `POSTGRES_*` or
#     `OPENAI_*` glob is a silent no-op in bash) and it is PROVED by the
#     fence self-test below (D-7/D-8). The scrub covers the CREDENTIAL
#     class — OPENAI_API_KEY — NOT every OPENAI_* name: OPENAI_BASE_URL,
#     OPENAI_MODEL and ENSEMBLE_AUTH_ISSUER are REQUIRED by the house
#     ``load_config()`` and no lane test reaches the auth surface.
#   - Repo addopts cleared (--override-ini="addopts=") — house
#     convention; per-test timeout raised to 280s.
#   - Optional `-k <expr>` passthrough as $1.
#   - Output: header line, pytest body, RESULT: PASS|FAIL|TIMEOUT.
#   - Exit: 0 (PASS) / 1 (FAIL) / 124 (TIMEOUT).
#
# THE VACUOUS-VERDICT GUARD (D-8, council round 5): a result line that
# can print PASS from an EMPTY run is the D-2/D-3 defect in shell form.
# The pack FAILS LOUDLY when pytest reports zero passed tests or no
# summary at all — "PASS" must mean "the suite ran".
#
# THE FENCE SELF-TEST (D-7, council round 5): the scrub is proved by a
# DISCRIMINATING PAIR — the same child is run WITH the scrub (the
# sentinel must be ABSENT) and WITHOUT it (the sentinel must be
# PRESENT). A leg that passes identically with and without the scrub
# proves nothing; the control leg is what makes the scrub leg mean
# something.
#
# Expected runtime: < 1 min (unit class).
# =============================================================================
set -u

echo "=== Test Pack: decision_module_p1_unit_test ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL"; exit 1; }

PYTEST_BIN="$PROJECT_DIR/.venv/bin/pytest"
if [ ! -x "$PYTEST_BIN" ]; then
  echo "FATAL: .venv/bin/pytest not found at $PYTEST_BIN" >&2
  echo "RESULT: FAIL"
  exit 1
fi

if [ ! -d "tests/unit/decision" ]; then
  echo "FATAL: required test directory missing: tests/unit/decision" >&2
  echo "RESULT: FAIL"
  exit 1
fi

# ── the per-variable scrub (the LIVE credentials + the LIVE PG coords) ──
# NEVER `env -u POSTGRES_*` — bash does not glob env names, so that is
# a silent no-op (the house rule).
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  # The LIVE credential — the F7 class (the env-capturing harness leaked
  # the live OPENAI_API_KEY into scratch files). Scrubbed PER VARIABLE:
  # `env -u OPENAI_*` is a silent no-op in bash (no globbing).
  -u OPENAI_API_KEY
  -u OPENAI_REASONING_ECHO_DISABLED_MODELS
  # The lane's own explicit signal — the child must NOT inherit an
  # armed DECISION_* switch from the ambient shell.
  -u DECISION_API_KEY -u DECISION_BASE_URL -u DECISION_ENABLED
  -u DECISION_SEAM_SNAPSHOT_L3_SELECTION -u DECISION_SEAM_SNAPSHOT_L3_SELECTION_STAGE
  # NOT scrubbed, DELIBERATELY: OPENAI_BASE_URL / OPENAI_MODEL /
  # ENSEMBLE_AUTH_ISSUER are REQUIRED by the house ``load_config()``
  # (scrubbing the auth issuer makes the house Config refuse to
  # construct — the oauth2 exit-78 class). They are NOT credentials and
  # no lane test reaches the auth surface. The header claim states
  # exactly this.
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

# THE CONTROL — the same child WITHOUT the scrub MUST see the
# sentinel. If it does not, the scrub leg above proved nothing (the
# variable was never there) and the fence is UNPROVEN.
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

KARGS=()
if [ -n "${1:-}" ]; then
  echo "( -k filter active: $1 )"
  KARGS=(-k "$1")
fi

# NOTE: set -e is deliberately NOT used — a non-zero pytest exit must
# fall through to the RESULT mapping below.
OUT="$(mktemp)"
timeout 280 env \
  "${SCRUB[@]}" \
  PYTEST_TIMEOUT=280 \
  "$PYTEST_BIN" \
    -p no:randomly \
    -p no:xdist \
    -v \
    --tb=short \
    --color=no \
    --override-ini="addopts=" \
    --override-ini="timeout=280" \
    "${KARGS[@]}" \
    tests/unit/decision 2>&1 | tee "$OUT"
PYTEST_EXIT="${PIPESTATUS[0]}"

# ── the VACUOUS-VERDICT GUARD (D-8) ─────────────────────────────────────
SUMMARY="$(grep -E '[0-9]+ (passed|failed|error|skipped|deselected)' "$OUT" | tail -1)"
PASSED="$(sed -n 's/.*[^0-9]\([0-9]\+\) passed.*/\1/p' "$OUT" | tail -1)"
rm -f "$OUT"

if [ -z "$SUMMARY" ]; then
  echo ""
  echo "RESULT: FAIL (VACUOUS — pytest produced NO summary: the suite did not run)"
  exit 1
fi
if [ -z "$PASSED" ] || [ "$PASSED" -eq 0 ]; then
  echo ""
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
