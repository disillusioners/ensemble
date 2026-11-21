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
# THE CONFINEMENT PIN NOW RUNS (tester gate, 2026-11-21 — the deselect
# is RETIRED). Two changes made the node green and discriminating:
#
#   1. LANE_BASE was RE-POINTED from the metrics-query lane's P4 base
#      (644c2bfaa) to THIS lane's TRUE base (0b5624dc9). The old anchor
#      predates every lane landed since, so `git diff 644c2bfaa` returned
#      308 changed daemon paths (295 pin-visible, 13 under WIRING_ROOT)
#      and the pin RED'd on 157 inherited FOREIGN pin-visible paths
#      (170 counting the WIRING_ROOT-excluded ones) that no run of this
#      lane could ever clear. Against the true base the
#      same query returns 21 daemon paths (17 pin-visible, 4 under
#      WIRING_ROOT) and the pin's offender count is ZERO.
#   2. PRE_EXISTING_DAEMON_OFFENDERS was REGENERATED MECHANICALLY from
#      git (scripts/gen_tool_usage_daemon_allowlist.py — the recorded
#      command is in that file's docstring and in the constant's own
#      comment). It no longer carries the 108 rows five other lanes
#      accumulated; it now carries exactly this lane's 12 residual daemon
#      paths (the ones neither allowlisted nor under WIRING_ROOT), each
#      annotated with the commit that last touched it in the span.
#
# DISCRIMINATION (the property the old anchor destroyed): a foreign
# daemon path added to the diff is a NEW offender and REDs the node —
# proven by the in-file mutation test
# (test_the_daemon_diff_pin_is_discriminating) plus a live tree-level
# check run at this gate.
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
  tests/unit/tools/test_tool_category_artifact.py \
  tests/unit/migrations/test_tool_usage_migration.py
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

if env "${SCRUB[@]}" "$PROJECT_DIR/.venv/bin/python" -c "$PROBE"; then
  echo "fence self-test: SCRUBBED child sees no OPENAI_API_KEY  [OK]"
  SCRUB_LEG="OK"
else
  echo "fence self-test: SCRUBBED child still sees OPENAI_API_KEY  [FAIL]"
  SCRUB_LEG="FAIL"
fi

# THE CONTROL — the same child WITHOUT the scrub MUST see the sentinel.
if "$PROJECT_DIR/.venv/bin/python" -c "$PROBE"; then
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
    tests/unit/metrics/test_metrics_family_adjudication.py \
    tests/unit/metrics/test_metrics_vocabulary_amendment.py \
    tests/unit/tools/test_tool_category_artifact.py \
    tests/unit/migrations/test_tool_usage_migration.py 2>&1 | tee "$OUT"
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
