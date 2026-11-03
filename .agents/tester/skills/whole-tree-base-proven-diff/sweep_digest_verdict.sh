#!/usr/bin/env bash
# The whole-tree FAILED-set comparator — THE HOUSE CANON (adopted 2026-11-02, R-v).
#
# PROVENANCE: adopted from the central-metrics-v1 lane's repaired comparator
# (`.agents/shared/planning/central-metrics-v1/evidence/p3-scratch/sweep_digest_verdict.sh`),
# which was produced by the P3 evidence-repair round after the original sweep tool
# recorded TWO vacuous "net-new = EMPTY" verdicts. The task-gate layer is the
# tester lane's file, so the repair is adopted here rather than only preserved
# inside a commission's evidence dir.
#
# ── THE THREE DEFECT CLASSES THIS FILE EXISTS TO PREVENT ────────────────────
#
#  (1) THE `;`-vs-`|` VERDICT BLOCK (silent-empty verdict).
#      The buggy original wrote:
#          log "-- base-only (FIXED at head) --"; comm -23 "$b" "$h" | sed 's/^/  /'
#      The `;` ENDS the `log` call, so `comm`'s output went to the caller's STDOUT
#      and NEVER into the sweep log. The verdict block in the preserved sweep.log
#      was EMPTY BY CONSTRUCTION — the run recorded NO net-new verdict at all,
#      yet a human reading the log saw a clean file.
#      FIX (D-a): the diff lists go to FILES FIRST, then are LOGGED.
#
#  (2) THE COLLATION MISMATCH (silently dropped entries).
#      `sort` and `comm` must use the SAME collation. A locale-dependent sort can
#      order entries differently from what `comm` expects, and `comm` silently
#      DROPS them from the diff — an under-count that looks like a smaller net-new.
#      FIX (D-b): `LC_ALL=C` on BOTH `sort` and `comm`. (This is now a HOUSE
#      GATE-SCRIPT CONVENTION — every gate script that sorts/compares id sets
#      pins LC_ALL=C.)
#
#  (3) THE VACUOUS EMPTY-STRING DIGEST (a "pass" over zero evidence).
#      `sha256("") = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
#      Two whole-tree attempts aborted before producing ANY FAILED ids, so the
#      digest was that constant and the "DIGEST-EQUAL … net-new = EMPTY" verdict
#      that followed was vacuous (nodes = 0). A comparison over an empty set is
#      not a pass.
#      FIX (D-e, the FAIL-LOUD GUARD below): an empty input set, an empty-string
#      digest, or a verdict block that emits no VERDICT line each terminate the
#      tool with a LOUD VACUOUS banner and exit code 2 — never a silent 0.
#
# ── FORM-TAG DISCIPLINE (D-c) ──────────────────────────────────────────────
#   GREP-FULL — every captured line (node ids AND the daemon LOG lines that
#               happen to start with "FAILED "/"ERROR "). This is what a
#               `grep -E '^(FAILED|ERROR)'` sweep produces.
#   BARE-ID   — the sorted node-id list, prefix stripped, NODE IDS ONLY.
#   A cross-form comparison FALSE-MISMATCHES (the P3 run saw 37 vs 36 on the
#   same evidence: the 37th was a daemon log line, not a test id). Every verdict
#   line therefore carries its FORM tag, and each form is compared ONLY against
#   itself.
#
# USAGE:
#   sweep_digest_verdict.sh <BASE.ids.txt> <HEAD.ids.txt> [out_dir]
#
# EXIT CODES:
#   0  net-new EMPTY in BOTH forms (and the verdict block was non-vacuous)
#   1  net-new NOT empty in at least one form
#   2  VACUOUS — no usable evidence (empty input, empty-string digest, or an
#      empty verdict block). NEVER treat this as a pass.
#
# ── DOWNSTREAM OBLIGATION (recorded here so the tool is not over-trusted) ───
#   A non-empty net-new set is an INPUT TO ADJUDICATION, not a regression verdict.
#   The central-metrics-v1 P3 round proved the sweep-level delta is a
#   parallel-shard COMPOSITION ARTEFACT: the SAME shard on the SAME tree yielded
#   DIFFERENT red sets across two runs (26 ids differed). So: any diff id is
#   re-run in ISOLATION 3× per side before it is classified (the recipe's §6
#   classification ladder). The tool measures; the ladder decides.

set -uo pipefail

BASE_LIST=${1:?usage: sweep_digest_verdict.sh BASE.ids.txt HEAD.ids.txt [out_dir]}
HEAD_LIST=${2:?usage: sweep_digest_verdict.sh BASE.ids.txt HEAD.ids.txt [out_dir]}
OUT_DIR=${3:-.}

EMPTY_SHA="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

mkdir -p "$OUT_DIR"
LOG="$OUT_DIR/sweep_digest_verdict.log"
: > "$LOG"

log() { echo "$@" | tee -a "$LOG"; }

vacuous() {
    # THE FAIL-LOUD GUARD (D-e). Never a silent zero.
    log ""
    log "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    log "!! VACUOUS VERDICT — this is NOT a pass and MUST NOT be reported as one."
    log "!! reason: $1"
    log "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    exit 2
}

for f in "$BASE_LIST" "$HEAD_LIST"; do
    [ -f "$f" ] || vacuous "input list does not exist: $f"
done

log "=== the whole-tree FAILED-set verdict (house canon) ==="
log "  BASE list: $BASE_LIST"
log "  HEAD list: $HEAD_LIST"
log "  out dir:   $OUT_DIR"
log "  date(UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)"
log "  collation: LC_ALL=C (pinned on BOTH sort and comm)"

# ── the two forms (LC_ALL=C for BOTH sort and comm) ───────────────────────
LC_ALL=C sort -u "$BASE_LIST" > "$OUT_DIR/base.grepfull.c"
LC_ALL=C sort -u "$HEAD_LIST" > "$OUT_DIR/head.grepfull.c"
grep '::' "$OUT_DIR/base.grepfull.c" > "$OUT_DIR/base.bareid.c" || true
grep '::' "$OUT_DIR/head.grepfull.c" > "$OUT_DIR/head.bareid.c" || true

base_grepfull_n=$(wc -l < "$OUT_DIR/base.grepfull.c")
head_grepfull_n=$(wc -l < "$OUT_DIR/head.grepfull.c")

log "  BASE raw unique lines: $base_grepfull_n   HEAD raw unique lines: $head_grepfull_n"

# GUARD 1 — an empty input set cannot produce a net-new verdict.
[ "$base_grepfull_n" -gt 0 ] || vacuous "BASE id list is EMPTY (0 unique lines)"
[ "$head_grepfull_n" -gt 0 ] || vacuous "HEAD id list is EMPTY (0 unique lines)"

rc=0
verdict_lines=0
for form in grepfull bareid; do
    b="$OUT_DIR/base.$form.c"
    h="$OUT_DIR/head.$form.c"
    LC_ALL=C comm -23 "$b" "$h" > "$OUT_DIR/$form.base-only.txt"
    LC_ALL=C comm -13 "$b" "$h" > "$OUT_DIR/$form.head-only.txt"
    LC_ALL=C comm -12 "$b" "$h" > "$OUT_DIR/$form.both.txt"

    base_n=$(wc -l < "$b")
    head_n=$(wc -l < "$h")
    base_only=$(wc -l < "$OUT_DIR/$form.base-only.txt")
    head_only=$(wc -l < "$OUT_DIR/$form.head-only.txt")
    both=$(wc -l < "$OUT_DIR/$form.both.txt")
    digest_base=$(sha256sum "$b" | cut -d' ' -f1)
    digest_head=$(sha256sum "$h" | cut -d' ' -f1)

    log ""
    log "--- FORM=$form ---"
    log "  BASE unique ids: $base_n   HEAD unique ids: $head_n"
    log "  base-only (FIXED at head): $base_only"
    log "  head-only (NET-NEW reds):  $head_only"
    log "  in BOTH sets:               $both"
    log "  DIGEST base: $digest_base"
    log "  DIGEST head: $digest_head"

    # GUARD 2 — the empty-string digest is the vacuous class, by name.
    # (A form may legitimately be empty on ONE side when that side truly had zero
    # reds — but BOTH sides empty under the same form means zero evidence.)
    if [ "$digest_base" = "$EMPTY_SHA" ] && [ "$digest_head" = "$EMPTY_SHA" ]; then
        vacuous "FORM=$form produced the EMPTY-STRING digest on BOTH sides (nodes=0) — sha256(\"\")"
    fi

    # ── THE FIXED VERDICT BLOCK (files first, then logged) ──────────────────
    log "  -- $form base-only (FIXED at head) --"
    if [ "$base_only" -gt 0 ]; then
        sed 's/^/    /' "$OUT_DIR/$form.base-only.txt" | tee -a "$LOG"
    else
        log "    (none)"
    fi
    log "  -- $form head-only (NET-NEW reds) --"
    if [ "$head_only" -gt 0 ]; then
        sed 's/^/    /' "$OUT_DIR/$form.head-only.txt" | tee -a "$LOG"
    else
        log "    (none)"
    fi

    verdict_lines=$((verdict_lines + 1))
    if [ "$head_only" -eq 0 ]; then
        log "  VERDICT [FORM=$form]: NET-NEW = EMPTY (the two legs' FAILED sets are equal)"
    else
        log "  VERDICT [FORM=$form]: NET-NEW = $head_only (NOT EMPTY — the sets differ)"
        rc=1
    fi
done

# GUARD 3 — the verdict block must actually exist in the log.
# This is the direct defence against defect class (1): a `;`-bug emits no VERDICT
# line, so a downstream reader sees a clean-looking log. Count them in the FILE.
logged_verdicts=$(grep -c 'VERDICT \[FORM=' "$LOG" || true)
if [ "$logged_verdicts" -ne "$verdict_lines" ] || [ "$verdict_lines" -eq 0 ]; then
    vacuous "verdict block EMPTY — expected $verdict_lines VERDICT line(s) in the log, found $logged_verdicts"
fi

log ""
log "=== FINAL VERDICT ==="
if [ "$rc" -eq 0 ]; then
    log "NET-NEW = EMPTY in BOTH forms (GREP-FULL and BARE-ID) — the whole-tree leg's net-new claim holds."
    log "  REMINDER: this claim is only as good as the shard determinism behind it. Adjudicate any"
    log "  diff id in ISOLATION 3x per side before classifying (see the recipe's section 6)."
else
    log "NET-NEW is NOT EMPTY in at least one form — the whole-tree leg's net-new claim does NOT hold as stated."
    log "The per-id adjudication (each id in ISOLATION, 3 runs per side) is the next step."
fi
log "  the verdict log: $LOG"
exit $rc
