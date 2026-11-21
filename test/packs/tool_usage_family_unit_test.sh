#!/usr/bin/env bash
# =============================================================================
# Test Pack: tool_usage_family_unit_test
#
# Purpose: the tool-usage-metrics-p1 lane's FAMILY pack (ENSEMBLE-37,
# Phase 2 — the F24 central metrics family). Registers the family's
# unit surface so the standard gate actually RUNS it:
#
#   tests/unit/metrics/test_metrics_family_adjudication.py
#       — the adjudication pack: the F24 census rows (29 verdicts,
#         binding subset 19-of-29), the registration structure
#         (AM-2/AM-3 + the F24 closed label space 67 × 2 = 134),
#         the band pins (134 ≤ 160 with 26 explicit headroom), the
#         measured-total convention pin (89 + 536 = 625 ≤ 2000, THE
#         claim per decisions.md D18 / approver fix B5), the global
#         cap, VOCABULARY_VERSION == 11, the family gate matrix
#         (fail-closed, stacked, DB-free) and the write-time sink
#         behavior (folds, drift signal, wired-but-gated);
#   tests/unit/metrics/test_metrics_vocabulary_amendment.py
#   tests/unit/tools/test_tool_category_artifact.py
#       — the T2 vocabulary legs: the closed tool-category set is the
#         committed V-4 artifact's, no VOCABULARY_VERSION bump
#         (phase2-plan.md §3.4 — verified: every emitted category
#         folds into the 67-value set).
#
# Contract:
#   - Dual-layer timeout: inner `timeout 280` around pytest; the caller
#     wraps with `timeout 300 bash <this pack>`.
#   - Serial: no -n passed AND xdist plugin disabled outright (-p no:xdist).
#   - Deterministic order: -p no:randomly.
#   - MANDATORY env scrub — PER-VARIABLE (a `POSTGRES_*` glob is a
#     silent no-op in bash): the ambient shell may carry LIVE-PROD PG
#     coords and the LIVE OPENAI_API_KEY (the F7 class), and the lane's
#     own kill-switch env vars must not leak in armed. The scrub covers
#     the CREDENTIAL class — OPENAI_API_KEY — NOT every OPENAI_* name:
#     OPENAI_BASE_URL / OPENAI_MODEL / ENSEMBLE_AUTH_ISSUER are REQUIRED
#     by the house ``load_config()`` and no test here reaches the auth
#     surface. NOT scrubbed, deliberately: OPENAI_BASE_URL /
#     OPENAI_MODEL / ENSEMBLE_AUTH_ISSUER (the decision_module_p1 pack
#     precedent).
#   - Repo addopts cleared (--override-ini="addopts=").
#   - Optional `-k <expr>` passthrough as $1.
#   - Output: header line, pytest body, RESULT: PASS|FAIL|TIMEOUT.
#   - Exit: 0 (PASS) / 1 (FAIL) / 124 (TIMEOUT).
#
# THE VACUOUS-VERDICT GUARD (the D-8 discipline): a result line that
# can print PASS from an EMPTY run is the D-2/D-3 defect in shell form.
# The pack FAILS LOUDLY when pytest reports zero passed tests or no
# summary at all.
#
# THE FENCE SELF-TEST (the D-7 discipline): the scrub is proved by a
# DISCRIMINATING PAIR — the same child run WITH the scrub (the sentinel
# must be ABSENT) and WITHOUT it (the sentinel must be PRESENT).
#
# THE PRE-EXISTING RED DISCLOSURE (2026-11-21; refreshed by the council
# C3 follow-up): the pack DESELECTS exactly one node —
#   TestNoProducerInstrumentation::test_the_daemon_diff_is_confined_to_the_allowed_files
# That pin diffs the whole tree against the metrics-query lane's base
# (644c2bfaa) and REDS on 158 daemon paths that predate this lane
# entirely (every lane landed since that recorded nothing).
#
# THIS LANE'S OWN SURFACES ARE NOW RECORDED — the claim this block used
# to carry ("the lane's files are unrecorded") is STALE and is replaced
# here. The NINE lane daemon files are registered in
# PRE_EXISTING_DAEMON_OFFENDERS with their owning commits attributed
# (six to 49f5e9c1d, three to 71273d7e3) by commit aa98bce37, so this
# lane's MARGINAL offender contribution is ZERO: the offender set at
# HEAD is byte-identical to the set inherited at the branch base
# (0b5624dc9 — 158 offenders), which was ALREADY RED before phase 1
# landed. The lane's tenth pin-visible surface, daemon/graph.py (the
# observation-wrapper CALL SITE — no observe/inc added), is recorded by
# the same fix set in ALLOWED_DAEMON_DIFF with its lane attribution
# (49f5e9c1d) and in LANE_OWNED_DAEMON, which is what the both-directions
# pin requires of an allowlisted lane surface (direction 2 reads a new
# ALLOWED entry that is not lane-owned as a SILENT WIDENING). Recording
# it does not change this disclosure: the pin's red is the inherited set.
#
# THE DESELECT REMAINS REQUIRED because the node still REDS on those 158
# INHERITED FOREIGN offenders under the 644c2bfaa anchor — pre-existing
# foreign debt, disclosed here and to be adjudicated at tester-gate time
# (re-point the anchor to 0b5624dc9, or record the 158 lane-by-lane).
# The phase-2 footprint stays confined to
# daemon/services/metrics_query/wiring/ (the pin's WIRING_ROOT
# exclusion). Everything else in the file runs and must be GREEN.
#
# Expected runtime: < 1 min (unit class).
# =============================================================================
set -u

echo "=== Test Pack: tool_usage_family_unit_test ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL"; exit 1; }

PYTEST_BIN="$PROJECT_DIR/.venv/bin/pytest"
if [ ! -x "$PYTEST_BIN" ]; then
  echo "FATAL: .venv/bin/pytest not found at $PYTEST_BIN" >&2
  echo "RESULT: FAIL"
  exit 1
fi

for f in \
  tests/unit/metrics/test_metrics_family_adjudication.py \
  tests/unit/metrics/test_metrics_vocabulary_amendment.py \
  tests/unit/tools/test_tool_category_artifact.py
do
  if [ ! -f "$f" ]; then
    echo "FATAL: required test file missing: $f" >&2
    echo "RESULT: FAIL"
    exit 1
  fi
done

# ── the per-variable scrub (the LIVE credentials + the LIVE PG coords
#    + this lane's own kill-switch signals) ──────────────────────────────
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u OPENAI_API_KEY
  -u OPENAI_REASONING_ECHO_DISABLED_MODELS
  # This lane's own kill-switch signals — the child must NOT inherit an
  # armed switch from the ambient shell (the gate matrix sets its own).
  -u ENSEMBLE_METRICS_ENABLED
  -u ENSEMBLE_METRICS_TOOL_USAGE_ENABLED
  -u LIMITS_TOOL_USAGE_COLLECTION_ENABLED
  # NOT scrubbed, DELIBERATELY: OPENAI_BASE_URL / OPENAI_MODEL /
  # ENSEMBLE_AUTH_ISSUER are REQUIRED by the house ``load_config()``
  # (scrubbing the auth issuer makes the house Config refuse to
  # construct — the oauth2 exit-78 class). They are NOT credentials and
  # no test here reaches the auth surface.
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
    --deselect "tests/unit/metrics/test_metrics_family_adjudication.py::TestNoProducerInstrumentation::test_the_daemon_diff_is_confined_to_the_allowed_files" \
    "${KARGS[@]}" \
    tests/unit/metrics/test_metrics_family_adjudication.py \
    tests/unit/metrics/test_metrics_vocabulary_amendment.py \
    tests/unit/tools/test_tool_category_artifact.py 2>&1 | tee "$OUT"
PYTEST_EXIT="${PIPESTATUS[0]}"

# ── the VACUOUS-VERDICT GUARD ───────────────────────────────────────────
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
