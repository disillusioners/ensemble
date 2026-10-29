#!/usr/bin/env bash
# dbpool_gate_ladder.sh — THE canonical 17-row gate ladder (db-pool-idle-txn).
#
# Lane: fix/db-pool-idle-txn (job 634c38f2). Authority:
# final-implementation-spec.md §6.2a (THE canonical ladder — one ID per
# scope, no range notation); phase3-plan.md §2.1/T3.5; test-strategy.md
# §2.2/§2.3 (the runner forms and the fence this ladder invokes).
#
# EVERY executed row carries the FULL Class-1 fence (DR-16) — all five
# parts, no exceptions:
#   1. cd /home/nea/ensemble-src-wt-dbpool-idletxn   (LOAD-BEARING: from
#      any other cwd the certified interpreter resolves `daemon` to the
#      SHARED CHECKOUT)
#   2. per-variable scrub of SIX variables (POSTGRES_DB, POSTGRES_USER,
#      POSTGRES_PASSWORD, POSTGRES_HOST, POSTGRES_PORT, DAEMON_PORT) —
#      `env -u POSTGRES_*` is a SILENT NO-OP (bash does not glob env
#      names) and is FORBIDDEN
#   3. HOME="$(mktemp -d)" — never a HOME-derived live install path
#   4. the certified interpreter /home/nea/ensemble-src/.venv/bin/python
#      (D-29: NOT uv run — off-PATH; NOT /usr/bin/python3 — no pytest)
#   5. the collected-count assertion FIRST (D-26/DR-23) — the expected
#      count is read from the INDEPENDENT artifact expected-collected.md
#      at run time; a TBD cell is FATAL (Δ1 — integer-validated before
#      comparison). A gate run before task R-1 lands therefore CANNOT
#      pass. That is by design.
#
# PG/integration rows additionally assert ZERO SKIPS (DR-17 —
# tests/postgres/conftest.py skips the whole module when PG is
# unreachable, so a skipped-everything gate is indistinguishable from a
# pass).
#
# G1.5 / G3.5 (whole-tree base-vs-HEAD byte-comm) are TESTER-OWNED and
# are documented here but NOT executed — they require the base worktree
# and are the tester's instrument.
#
# Usage:
#   tests/packs/dbpool_gate_ladder.sh            # fence-check every row
#   tests/packs/dbpool_gate_ladder.sh --run      # fence + RUN every row
#   tests/packs/dbpool_gate_ladder.sh --run G3.1 # fence + run ONE row
#
# Environment for PG rows: PG_TEST_HOST/PG_TEST_PORT/PG_TEST_DB/
# PG_TEST_USER/PG_TEST_PASSWORD must point at a scratch cluster + the
# harness DB (test-strategy.md §1.0). NEVER ensemble_prod.
set -uo pipefail

# T-M3: every temp HOME is registered and removed by the EXIT trap —
# a 17-row --run previously leaked 18+ /tmp/tmp.* dirs per invocation.
# _new_home sets the NEW_HOME GLOBAL (not an echo-helper: a
# `HOME="$(_new_home)"` substitution runs in a subshell, the array
# append would be lost, and the dir would leak anyway).
_TMP_HOMES=()
NEW_HOME=""
_new_home() {
  NEW_HOME=$(mktemp -d)
  _TMP_HOMES+=("$NEW_HOME")
}
trap '_rc=$?; for d in "${_TMP_HOMES[@]:-}"; do [ -n "$d" ] && rm -rf "$d"; done; exit $_rc' EXIT

WORKTREE=/home/nea/ensemble-src-wt-stage-v02713
PY=/home/nea/ensemble-src/.venv/bin/python
PLAN_DIR="$WORKTREE/.agents/shared/planning/db-pool-idle-txn"
EXPECTED_FILE="$PLAN_DIR/expected-collected.md"
FENCE=/tmp/sv4-ladder/dbpool_collected_fence.sh

MODE="${1:-check}"
ROW_FILTER=""
if [ "$MODE" = "--run" ]; then
  ROW_FILTER="${2:-}"
elif [ -n "$MODE" ] && [ "$MODE" != "check" ]; then
  echo "usage: $0 [--run [GATE_ID]]" >&2
  exit 2
fi

FAILED=0
# T-H1: matched-row counter — an unmatched ROW_FILTER (typo'd/renamed/
# not-yet-added gate id) previously made every row early-return and the
# script printed LADDER: OK / exit 0 having executed ZERO gates.
MATCHED=0

# ── the expected count for one expected-collected.md row (Δ1: a TBD
#    cell is FATAL here — the ladder never invents a number) ─────────
expected_for_row() {
  local row="$1"
  local cell
  # T-L1: a MISSING artifact is not a missing/TBD ROW — stat first so
  # the FATAL names the actual defect.
  if [ ! -f "$EXPECTED_FILE" ]; then
    echo "FATAL: expected-collected.md is MISSING at $EXPECTED_FILE — cannot look up row ${row} (is the plan dir mounted?)" >&2
    return 1
  fi
  # T-L2: escape the row id's dots (G1.1's '.' matched any char in ERE).
  local row_ere="${row//./\\.}"
  cell=$(grep -E "^\| ${row_ere} \|" "$EXPECTED_FILE" | head -1 \
    | awk -F'|' '{gsub(/^ +| +$/, "", $6); print $6}')
  if ! [[ "$cell" =~ ^[0-9]+$ ]]; then
    echo "FATAL: expected-collected.md row ${row} is '${cell:-MISSING}' (TBD / R-1 not landed) — the gate cannot assert its count and therefore cannot pass (Δ1/DR-23)" >&2
    return 1
  fi
  echo "$cell"
}

# ── the FULL Class-1 fence prefix (all five parts, every command) ─────
fence_env() {
  echo "env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT HOME=\"\$(mktemp -d)\""
}

# ── interpreter certification (first step of every gate) ─────────────
certify() {
  # T-M2: printing the resolved daemon.__file__ without asserting it
  # certified nothing — an import that resolves to the SHARED checkout
  # (any cwd other than the worktree) still printed a plausible path
  # and exited 0. Assert the prefix (the cd is load-bearing, DR-16).
  local resolved rc
  _new_home
  resolved=$(cd "$WORKTREE" && env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
     -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT HOME="$NEW_HOME" \
     "$PY" -c "import daemon,pytest; print(daemon.__file__, pytest.__version__)")
  rc=$?
  echo "$resolved"
  if [ "$rc" -ne 0 ]; then
    echo "FATAL: interpreter certification failed (rc=$rc)" >&2
    return 1
  fi
  case "$resolved" in
    "$WORKTREE"/*) return 0 ;;
    *)
      echo "FATAL: daemon resolved OUTSIDE the worktree — first line must start with $WORKTREE/ (got: ${resolved%%$'\n'*})" >&2
      return 1
      ;;
  esac
}

# ── one ladder row ─────────────────────────────────────────────────────
# args: GATE_ID | rows-in-expected-collected (comma-separated) | marker
#       (""=default) | then the pack paths.
run_row() {
  local gate_id="$1"; shift
  local ec_rows="$1"; shift
  local marker="$1"; shift
  # W-3: the LABEL may carry a marker-split annotation; the ID column
  # ($gate_id) stays CANONICAL (contract 6.2a) — the label never feeds
  # the id checks.
  local label="${ROW_LABEL:-$gate_id}"
  ROW_LABEL=""

  if [ -n "$ROW_FILTER" ] && [ "$ROW_FILTER" != "$gate_id" ]; then
    return 0
  fi
  MATCHED=$((MATCHED + 1))

  echo "── $label ──────────────────────────────────────────────"

  # The count assertion: sum this row's expected-collected.md cells.
  local expected=0 row cell
  local -a rows
  IFS=',' read -ra rows <<< "$ec_rows"
  for row in "${rows[@]}"; do
    cell=$(expected_for_row "$row") || { FAILED=1; return 0; }
    expected=$((expected + cell))
  done

  # The fence (collect-only, integer-validated, FATAL on TBD).
  if ! "$FENCE" "$expected" "$marker" "$@"; then
    FAILED=1
    return 0
  fi

  if [ "$MODE" != "--run" ]; then
    echo "$label: FENCE-OK (collect-only mode; --run executes)"
    return 0
  fi

  # The run — FULL fence, marker override for PG/integration rows,
  # zero-skips asserted on the marked classes.
  local marker_args=()
  [ -n "$marker" ] && marker_args=(--override-ini="addopts=" -m "$marker")
  local summary run_rc
  # W-5 (code-review council): capture the pipeline's exit status —
  # under this script's `set -o pipefail` the command substitution's
  # status carries PYTEST's exit code through the tail, so an
  # ERRORS-ONLY outcome (no FAILED line in the tail) can no longer
  # print RUN-PASS. The grep below is the belt: it additionally
  # recognises an errors-only summary line.
  _new_home
  summary=$(cd "$WORKTREE" && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$NEW_HOME" \
    "$PY" -m pytest "$@" "${marker_args[@]}" -q --tb=short 2>&1 | tail -3)
  run_rc=$?
  echo "$summary"
  if [ "$run_rc" -ne 0 ]; then
    echo "$label: RUN-FAILED (pytest exit=$run_rc)" >&2
    FAILED=1
    return 0
  fi
  if echo "$summary" | grep -qE "^[0-9]+ failed|FAILED|[0-9]+ error"; then
    echo "$label: RUN-FAILED" >&2
    FAILED=1
    return 0
  fi
  if [ -n "$marker" ]; then
    if echo "$summary" | grep -qE "[1-9][0-9]* skipped"; then
      echo "$label: FATAL — a PG/integration gate that SKIPPED is a FAILING gate (DR-17)" >&2
      FAILED=1
      return 0
    fi
  fi
  echo "$label: PASS (count=$expected asserted from expected-collected.md rows $ec_rows)"
}

echo "# db-pool gate ladder — interpreter certification"
certify || exit 1

# ── G0 — Phase-0 precondition (artifact check, not a pytest row) ──────
if { [ -z "$ROW_FILTER" ] || [ "$ROW_FILTER" = "G0" ]; }; then
  MATCHED=$((MATCHED + 1))
  echo "── G0 ──────────────────────────────────────────────"
  ok=1
  # BASELINE.md exists, is reviewed, and both provisional literals are
  # RESOLVED (not merely recorded — phase3-plan §2.1 G0 row). The
  # resolutions live where the fold put them: the D-47 record in
  # decisions.md + the shipped constant's DISABLED block; the D-48
  # erratum in BASELINE.md itself + the D-48 record.
  [ -f "$PLAN_DIR/BASELINE.md" ] || { echo "G0: FATAL — BASELINE.md missing" >&2; ok=0; }
  grep -q "^MANAGER_ENGINE_IDLE_IN_TXN_TIMEOUT_MS" "$WORKTREE/daemon/constants.py" \
    || { echo "G0: FATAL — the idle-in-txn literal is not resolved (constant absent)" >&2; ok=0; }
  grep -q "DEFINED BUT NOT APPLIED" "$WORKTREE/daemon/constants.py" \
    || { echo "G0: FATAL — the idle-in-txn literal is not recorded DISABLED (D-47)" >&2; ok=0; }
  grep -q "DISABLED" "$PLAN_DIR/decisions.md" \
    || { echo "G0: FATAL — the D-47 DISABLED record is absent from decisions.md" >&2; ok=0; }
  grep -qiE "withdr" "$PLAN_DIR/BASELINE.md" \
    || { echo "G0: FATAL — the residence-metric erratum is not recorded in BASELINE.md" >&2; ok=0; }
  grep -q "xact_age_s" "$PLAN_DIR/BASELINE.md" \
    || { echo "G0: FATAL — the measured metric (xact_age_s) is not recorded" >&2; ok=0; }
  [ "$ok" = 1 ] && echo "G0: PASS (BASELINE.md present; both literals resolved — idle-in-txn DISABLED per D-47; the state_change−xact_start metric withdrawn, xact_age_s recorded per D-48)"
  [ "$ok" = 1 ] || FAILED=1
fi

U=tests/unit; T=tests/unit/tools
run_row G1.1 6  "" \
  "$T/test_dbpool_txn_hygiene.py"
run_row G1.2 10 "" \
  "$T/test_dbpool_mission_mint.py"
run_row G1.3 11 "" \
  "$T/test_dbpool_wedge_capture_static.py"
run_row G1.4 7,8,9,16,18 "" \
  "$T/test_dbpool_probe_bound.py" \
  "$T/test_dbpool_probe_failure_class.py" \
  "$U/tools/test_readyz_grace_detail.py" \
  "$T/test_dbpool_fix_site_static.py" \
  "$T/test_dbpool_consistency_pin.py"
run_row G2.1 1,2,5 "" \
  "$U/repositories/test_pg_engine_utc_session.py" \
  "$T/test_dbpool_pool_attribution.py" \
  "$T/test_dbpool_honesty_blocks.py"
run_row G2.2 3 "" \
  "$T/test_dbpool_repair_engine_isolation.py"
run_row G2.3 4 "" \
  "$T/test_dbpool_p2_immutability.py"
run_row G2.4 1 "" \
  "$U/repositories/test_pg_engine_utc_session.py"
ROW_LABEL="G2.5 [postgres]"
run_row G2.5 20,21 postgres \
  tests/postgres/test_dbpool_mission_mint_pg.py \
  tests/postgres/test_dbpool_probe_starvation_pg.py
ROW_LABEL="G2.5 [integration — marker split of the canonical row]"
run_row G2.5 23,26 integration \
  tests/integration/test_dbpool_probe_bound_pg.py \
  tests/integration/test_dbpool_deferred_report_reconcile_pg.py
run_row G2.6 22 postgres \
  tests/postgres/test_dbpool_wedge_capture_pg.py
run_row G3.1 25 integration \
  tests/integration/test_dbpool_churn_census.py
ROW_LABEL="G3.2 [integration]"
run_row G3.2 24 integration \
  tests/integration/test_db_pool_idle_in_transaction_guard.py
ROW_LABEL="G3.2 [unit static twins — marker split of the canonical row]"
run_row G3.2 15,14 "" \
  "$T/test_dbpool_guard_pack_selfcheck.py" \
  "$T/test_dbpool_residence_static.py"
run_row G3.3 12,13,14 "" \
  "$T/test_dbpool_probe_retention_static.py" \
  "$T/test_dbpool_occupancy_static.py" \
  "$T/test_dbpool_residence_static.py"
run_row G3.4 17 "" \
  "$T/test_dbpool_census_families.py"

# ── G1.5 / G3.5 — whole-tree base-vs-HEAD: TESTER-OWNED ───────────────
if [ -z "$ROW_FILTER" ]; then
  echo "── G1.5 / G3.5 ─────────────────────────────────────"
  echo "TESTER-OWNED (not executed here): whole-tree default-collected corpus, base vs HEAD, with base_collected/head_collected reconciled against the sum of the declared EXPECTED_COLLECTED_* (row 35 of expected-collected.md); collected > 0 on BOTH sides; mismatch = FATAL. The byte-comm instrument is the tester lane's."
fi

if [ -n "$ROW_FILTER" ] && [ "$MATCHED" -eq 0 ]; then
  echo "LADDER: FATAL — no gate row matched '${ROW_FILTER}' (typo'd/renamed/not-yet-added id?); ZERO gates were executed" >&2
  exit 1
fi
if [ "$FAILED" = 1 ]; then
  echo "LADDER: FAILED" >&2
  exit 1
fi
echo "LADDER: OK (mode=${MODE}, rows matched=${MATCHED})"
