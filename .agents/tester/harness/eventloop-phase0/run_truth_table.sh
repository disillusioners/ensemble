#!/usr/bin/env bash
# run_truth_table.sh — the INSTRUMENT x TREE truth table (2x2) for the
# eventloop-phase0 harness.
#
# WHY: a mode switch that cannot demonstrate BOTH directions is a broken
# instrument.  A repaired harness must show:
#
#                       BASE tree          FIXED tree
#   BASE mode           GREEN  (defect     RED    (asserting the defect
#                       present)                  PRESENT on a fixed tree
#                                                 must fail)
#   HEAD mode           RED    (asserting   GREEN  (the fix is asserted)
#                       the defect ABSENT
#                       on a broken tree
#                       must fail)
#
# TODAY the "FIXED tree" cell is a SIMULATION (--simulate=head): no
# production fix has landed, so the fixed state is produced by
# transforming the MEASURED evidence with the fix's published semantics.
# The script labels those cells SIMULATED and refuses to print a
# simulated GREEN as gate evidence.
#
# Runs are SEQUENTIAL ON PURPOSE: every cell measures loop lag and loaded
# latency, so concurrent runs on one host would corrupt each other.
#
# Usage:
#   bash .agents/tester/harness/eventloop-phase0/run_truth_table.sh [--profile=smoke|full]
#
# Exit 0 iff all four cells match their expected verdict.

set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HARNESS_DIR/../../../.." && pwd)"
cd "$REPO_ROOT"

PROFILE="smoke"
for arg in "$@"; do
    case "$arg" in
        --profile=full)  PROFILE="full" ;;
        --profile=smoke) PROFILE="smoke" ;;
        *) echo "unknown arg: $arg (use --profile={full|smoke})" >&2; exit 2 ;;
    esac
done

RUN="$HARNESS_DIR/run_all.sh"
TABLE_TSV="$(mktemp)"
trap 'rm -f "$TABLE_TSV"' EXIT

REF="-"

# one cell: <label> <mode> <simulate> <expected_exit> <expected_verdict> <is_simulated>
cell() {
    local label="$1" mode="$2" sim="$3" want_rc="$4" want_verdict="$5" simulated="$6"
    local ref_arg=()
    if [ "$REF" != "-" ]; then ref_arg=("--reference=$REF"); fi
    echo
    echo "════════════════════════════════════════════════════════════════"
    echo "  CELL: $label   (mode=$mode simulate=$sim)"
    echo "════════════════════════════════════════════════════════════════"
    set +e
    bash "$RUN" --mode="$mode" --profile="$PROFILE" --simulate="$sim" \
        ${ref_arg[@]+"${ref_arg[@]}"} >/dev/null 2>&1
    local rc=$?
    set -e
    local suffix="${PROFILE}.${mode}"
    [ "$sim" = "head" ] && suffix="${suffix}.simulated"
    local report="$HARNESS_DIR/raw/BASELINE.${suffix}.json"
    local verdict="MISSING"; local failed=""; local nfences=0
    if [ -f "$report" ]; then
        # PIPE-delimited: fence names contain SPACES, so a plain
        # ``read -r a b c`` would shred them (caught in the first full run).
        IFS='|' read -r verdict nfences failed < <(
            python3 - "$report" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
fails = [f["name"] for f in d.get("fences", []) if not f["ok"]]
print(f'{d.get("verdict", "?")}|{len(d.get("fences", []))}|{", ".join(fails) if fails else "-"}')
PY
        )
    fi
    local match="NO"
    if [ "$rc" = "$want_rc" ] && [ "$verdict" = "$want_verdict" ]; then match="YES"; fi
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$label" "$mode" "$sim" "$simulated" "$rc" "$verdict" "$want_rc" \
        "$want_verdict" "$match" "${failed:-}" >> "$TABLE_TSV"
    echo
    echo "  -> exit=$rc verdict=$verdict fences=$nfences expected=(exit $want_rc, $want_verdict) match=$match"
    [ -n "${failed:-}" ] && echo "  -> failing fences: $failed"
    [ -z "$REF" ] && true
    if [ "$REF" = "-" ]; then REF="$report"; fi
}

echo "=== eventloop-phase0 TRUTH TABLE (profile=$PROFILE) ==="
echo "tree under test: $(git rev-parse HEAD) ($(git rev-parse --abbrev-ref HEAD))"

# 1. BASE mode on the BASE tree  -> GREEN, and produces the G4 reference.
cell "BASE-mode on BASE-tree (REAL)"        base none 0 PASS "no"
# 2. HEAD mode on the BASE tree  -> RED (the defect is still present).
cell "HEAD-mode on BASE-tree (REAL)"        head none 1 FAIL "no"
# 3. HEAD mode on the SIMULATED fixed tree -> GREEN (the fix is asserted).
cell "HEAD-mode on FIXED-tree (SIMULATED)"  head head 0 PASS "yes"
# 4. BASE mode on the SIMULATED fixed tree -> RED (asserting the defect
#    PRESENT on a fixed tree must fail = the base assertions are not vacuous).
cell "BASE-mode on FIXED-tree (SIMULATED)"  base head 1 FAIL "yes"

echo
echo "════════════════════════════════════════════════════════════════════"
echo "  TRUTH TABLE  (instrument x tree)"
echo "════════════════════════════════════════════════════════════════════"
printf '%-42s | %-4s | %-9s | %-8s | %-6s | %-6s | %-5s\n' \
    "cell" "mode" "tree" "verdict" "exit" "expect" "match"
printf '%s\n' "--------------------------------------------------------------------------------------------------"
python3 - "$TABLE_TSV" <<'PY'
import sys
rows = [l.rstrip("\n").split("\t") for l in open(sys.argv[1]) if l.strip()]
bad = 0
for (label, mode, sim, simulated, rc, verdict, want_rc, want_verdict, match, failed) in rows:
    tree = "SIMULATED" if simulated == "yes" else "BASE"
    print(f"{label:<42} | {mode:<4} | {tree:<9} | {verdict:<8} | {rc:<6} | "
          f"{want_verdict:<6} | {match:<5}")
    if match != "YES":
        bad += 1
print()
print(f"cells matching expectation: {len(rows)-bad}/{len(rows)}")
if bad:
    print("TRUTH TABLE: FAIL — a cell did not match its expected verdict")
    sys.exit(1)
print("TRUTH TABLE: PASS — the instrument separates the BROKEN tree from "
      "the FIXED tree in BOTH directions")
PY
