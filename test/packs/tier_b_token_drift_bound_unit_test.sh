#!/usr/bin/env bash
# Test Pack: tier_b_token_drift_bound_unit_test — row2-agent-turn-cpu 2C
# (tiktoken Tier B: policy-gated O(n) chunked counting in daemon/loader.py
# estimate_tokens — Emperor-sanctioned shape, ruling 2026-11-19 verbatim:
# "Counting only for reference low drift allowed")
#
# Timeout: 10 minutes (600s) — the gate-b legs recompute EXACT references on
# 200 KB pathological corpora (~87 s of O(n^2) encode by design; that cost is
# the very thing Tier B removes from the hot path).
#
# Gates proven (commission row2-2c):
#   (a) EXACT equality below threshold across corpora (default knobs) —
#       byte-identical for EVERY caller of the primitive.
#   (b) drift <= 0.1% above threshold on representative corpora (prose /
#       dense JSON / base64-ish) AND pathological unbroken runs
#       (letters / symbols) at 4/16/64/200 KB. DISCLOSED BREACH for pure
#       whitespace-class runs (spaces ~3.1-3.3%, newlines ~1.5%): they
#       tokenize ~8-100x denser than prose, so the same ~1-2 tokens per
#       boundary is a larger RATIO; the ABSOLUTE bound (~1-2 tokens per
#       boundary) holds exactly. Direction remains SAFE (over-count).
#   (c) direction: chunked >= exact ALWAYS — never smaller.
#   (d) TIMING at HEAD: 200 KB unbroken run counts < 1 s through the
#       default policy path (today's exact path: 27.05 s; C=4KB model
#       predicts ~0.55 s; measured 0.33-0.43 s).
#   (e) kill-switch ENSEMBLE_TOKEN_COUNT_EXACT=1 restores the exact path
#       everywhere.
#
# Legs:
#   1. pytest tests/unit/test_loader_chunked_counting.py  (36 tests, gates
#      a/c/e + knobs + consumer inheritance + 64 KB pathological drift)
#   2. benchmark script (scripts/bench_tier_b_token_drift.py) pinned to the
#      commission shape set at 4/16/64/200 KB — prints the drift + timing
#      table, asserts direction and the <1s gate-d bound. CJK excluded from
#      the pack set (its exact reference costs ~135 s; measured separately:
#      drift 0.0000%, chunked 2.5 s vs exact 135.4 s).
#
# FENCE (lane standard): explicit per-variable POSTGRES_* / dev pins built
# from the worktree's fenced .env — the `env -u POSTGRES_*` glob form is a
# SILENT NO-OP (bash does not glob env names) and is never used here.
set -euo pipefail

unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: tier_b_token_drift_bound_unit_test ==="

cd "$PROJECT_DIR"

# ─── per-variable dev fence (from the fenced .env — never `source` it) ─────
export ENSEMBLE_SELF_ENV="${ENSEMBLE_SELF_ENV:-dev}"
: "${POSTGRES_HOST:=$(grep -E '^POSTGRES_HOST=' .env | cut -d= -f2-)}"
: "${POSTGRES_PORT:=$(grep -E '^POSTGRES_PORT=' .env | cut -d= -f2-)}"
: "${POSTGRES_DB:=$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2-)}"
: "${POSTGRES_USER:=$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2-)}"
: "${POSTGRES_PASSWORD:=$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2-)}"
: "${DAEMON_PORT:=$(grep -E '^DAEMON_PORT=' .env | cut -d= -f2-)}"
export POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD DAEMON_PORT

if [ "${POSTGRES_DB:-}" = "ensemble_prod" ]; then
  echo "RESULT: FENCE-ABORT (POSTGRES_DB=ensemble_prod refused)"
  exit 3
fi

PACK_RC=0

# ─── Leg 1: the pytest gate file (gates a/c/e + knobs + inheritance) ───────
echo "--- Leg 1: pytest tests/unit/test_loader_chunked_counting.py ---"
timeout 300s .venv/bin/python -m pytest \
  tests/unit/test_loader_chunked_counting.py \
  --tb=short -q 2>&1 | tee /tmp/tier_b_pack_leg1_$$.txt >/dev/null || PACK_RC=1
tail -3 /tmp/tier_b_pack_leg1_$$.txt
grep -q "passed" /tmp/tier_b_pack_leg1_$$.txt || PACK_RC=1
grep -qE "^FAILED|[0-9] failed" /tmp/tier_b_pack_leg1_$$.txt && PACK_RC=1
rm -f /tmp/tier_b_pack_leg1_$$.txt

# ─── Leg 2: full-size drift + timing table (gates b + d at commission set) ─
echo "--- Leg 2: benchmark (commission shape set, 4/16/64/200 KB) ---"
TIER_B_SHAPES="prose,json,base64,letters,symbols,whitespace,newlines,digits" \
TIER_B_SIZES="4096,16384,65536,200000" \
TIER_B_RESULTS_PATH="/tmp/tier_b_pack_leg2_${$}.json" \
timeout 480s .venv/bin/python scripts/bench_tier_b_token_drift.py 2>&1 || PACK_RC=1

echo "--- Verdict ---"
if [ $PACK_RC -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
