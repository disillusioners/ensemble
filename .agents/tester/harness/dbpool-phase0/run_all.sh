#!/usr/bin/env bash
# Phase-0 empirical baseline harness — runner.
#
# NOT SHIPPED. Measurement scaffolding for the db-pool idle-in-transaction
# lane. Every script targets the LANE-OWNED SCRATCH DATABASE only.
#
#   DBPOOL_SCRATCH_DB      default ensemble_dbpool_scratch_phase0
#   DBPOOL_SCRATCH_USER    default dbpool_phase0
#   DBPOOL_SCRATCH_PASSWORD default phase0_scratch_pw
#   DBPOOL_REPO            default /home/nea/ensemble-src-wt-dbpool-phase0
#
# FENCE: this runner NEVER reads POSTGRES_* from the ambient environment
# and NEVER touches ensemble_prod, the live daemon, or any live install
# path. The scratch DB is created/dropped by the operator (see the
# baseline report for the exact SQL).
#
# DUAL-LAYER TIMEOUT: the outer `timeout 280` (command level) and the
# per-script internal budgets (script level).
set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${DBPOOL_PYTHON:-/home/nea/ensemble-src/.venv/bin/python}"
OUT_DIR="${DBPOOL_OUT_DIR:-$HARNESS_DIR/raw}"
mkdir -p "$OUT_DIR"

cd "$HARNESS_DIR" || exit 1

overall=0
for script in t2_stat_activity.py t1_mr3_hold.py t3_pool_attribution.py \
              t4_fingerprints.py t5_fix_sites.py; do
  name="${script%.py}"
  echo "=== running ${script} ==="
  timeout 280 "$PY" "$script" > "$OUT_DIR/${name}.out" 2>&1
  rc=$?
  case "$rc" in
    0)  echo "    ${name}: OK (exit 0)" ;;
    124) echo "    ${name}: TIMEOUT (exit 124)" ;;
    *)  echo "    ${name}: FAIL (exit ${rc})"; overall=1 ;;
  esac
done

echo
echo "raw outputs in: ${OUT_DIR}"
exit "$overall"
