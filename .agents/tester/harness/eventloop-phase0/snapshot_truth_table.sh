#!/usr/bin/env bash
# snapshot_truth_table.sh — commit the 2x2 truth-table EVIDENCE.
#
# WHY THIS EXISTS (council round-2 Blocking 3): the BASE-red artifacts were
# workspace-local, so Phase 5.1 acceptance could not be met as evidenced.
# This copies the four cell reports + the table log into raw/truth-table/
# (NOT gitignored — deliberately committed) and writes a manifest recording
# the instrument commit, the tree-sha, and each cell's verdict, so the
# non-vacuity proof is reviewable from the repository alone.
#
# Usage: snapshot_truth_table.sh <truth-table-log> [profile]
set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HARNESS_DIR/../../../.." && pwd)"
cd "$REPO_ROOT"

LOG="${1:?usage: snapshot_truth_table.sh <truth-table-log> [profile]}"
PROFILE="${2:-full}"
OUT="$HARNESS_DIR/raw/truth-table"
mkdir -p "$OUT"

HEAD_SHA="$(git rev-parse HEAD)"
TREE_SHA="$(git rev-parse HEAD:.agents/tester/harness/eventloop-phase0)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

CELLS=("base:none" "head:none" "head:head" "base:head")
for cell in "${CELLS[@]}"; do
    mode="${cell%%:*}"; sim="${cell##*:}"
    suffix="${PROFILE}.${mode}"
    [ "$sim" = "head" ] && suffix="${suffix}.simulated"
    src="$HARNESS_DIR/raw/BASELINE.${suffix}.json"
    dst="$OUT/${STAMP}-BASELINE.${suffix}.json"
    [ -f "$src" ] || { echo "MISSING cell report: $src" >&2; exit 1; }
    cp "$src" "$dst"
    echo "copied $dst"
done

cp "$LOG" "$OUT/${STAMP}-truth-table.log"

{
    echo "# Truth-table evidence manifest"
    echo
    echo "- instrument_commit: $HEAD_SHA"
    echo "- harness_tree_sha: $TREE_SHA"
    echo "- profile: $PROFILE"
    echo "- captured_utc: $STAMP"
    echo "- cells:"
    for cell in "${CELLS[@]}"; do
        mode="${cell%%:*}"; sim="${cell##*:}"
        suffix="${PROFILE}.${mode}"
        [ "$sim" = "head" ] && suffix="${suffix}.simulated"
        f="$OUT/${STAMP}-BASELINE.${suffix}.json"
        python3 - "$f" "$mode" "$sim" <<'PY'
import json, sys
p, mode, sim = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(p))
fails = [x["name"] for x in d.get("fences", []) if not x["ok"]]
print(f"  - {{file: {p.split('/')[-1]}, mode: {mode}, simulate: {sim}, "
      f"repo_head: {d.get('repo_head')}, verdict: {d.get('verdict')}, "
      f"fences: {len(d.get('fences', []))}, "
      f"failing: {len(fails)}}}")
PY
    done
    echo
    echo "The four cell reports are the COMPLETE raw output of each run"
    echo "(mode-suffixed originals stay gitignored under raw/)."
} > "$OUT/${STAMP}-MANIFEST.md"

echo
cat "$OUT/${STAMP}-MANIFEST.md"
