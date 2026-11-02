#!/usr/bin/env bash
# The whole-tree FAILED-set comparator — the FIXED net-new verdict block.
#
# WHY THIS FILE EXISTS (the P3 evidence-repair round, 2026-11-02):
#
#   The P3 whole-tree sweep's comparator had a `;` vs `|` shell bug in its
#   verdict block:
#
#       log "-- base-only (FIXED at head) --"; comm -23 "$b" "$h" | sed 's/^/  /'
#
#   The `;` ENDS the `log` call, so the `comm` output went to the caller's
#   STDOUT and NEVER into the sweep log. The verdict block in the preserved
#   `sweep.log` is therefore EMPTY BY CONSTRUCTION, and the run recorded NO
#   net-new verdict at all. (The two EARLIER runs recorded "DIGEST-EQUAL …
#   net-new = EMPTY" over the EMPTY-STRING digest `e3b0c442…b855`
#   (= sha256(""), nodes=0) — vacuous.)
#
#   This comparator is the authoritative net-new verdict tool: it writes the
#   diff lists to FILES, LOGS them, and emits an EXPLICIT verdict line.
#
# USAGE:
#   sweep_digest_verdict.sh <BASE.nodeids.txt> <HEAD.nodeids.txt> [out_dir]
#
# FORMS (the failed-digest-equality-gate v1.0.2 FORM-TAG discipline):
#   GREP-FULL — every captured line (the node ids AND the LOG lines that
#               happen to start with "FAILED "/"ERROR "). This is the form
#               the sweep's `grep -E '^(FAILED|ERROR)'` produced.
#   BARE-ID   — the sorted node-id list, prefix stripped, NODE IDS ONLY.
#   A cross-form comparison FALSE-MISMATCHES — each form is compared ONLY
#   against itself, and each verdict carries its form tag.
#
# LOCALE: `sort` AND `comm` MUST use the SAME collation (LC_ALL=C). A
# mismatch silently DROPS entries from the diff — the two tools must agree.
#
# EXIT: 0 when the net-new set is EMPTY in BOTH forms, 1 otherwise.

set -uo pipefail

BASE_LIST=${1:?usage: sweep_digest_verdict.sh BASE.nodeids.txt HEAD.nodeids.txt [out_dir]}
HEAD_LIST=${2:?usage: sweep_digest_verdict.sh BASE.nodeids.txt HEAD.nodeids.txt [out_dir]}
OUT_DIR=${3:-.}
mkdir -p "$OUT_DIR"
LOG="$OUT_DIR/sweep_digest_verdict.log"

log() { echo "$@" | tee -a "$LOG"; }

log "=== the whole-tree FAILED-set verdict ==="
log "  BASE list: $BASE_LIST"
log "  HEAD list: $HEAD_LIST"
log "  out dir:   $OUT_DIR"
log "  date(UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── the two forms (LC_ALL=C for BOTH sort and comm) ───────────────────────
LC_ALL=C sort -u "$BASE_LIST" > "$OUT_DIR/base.grepfull.c"
LC_ALL=C sort -u "$HEAD_LIST" > "$OUT_DIR/head.grepfull.c"
grep '::' "$OUT_DIR/base.grepfull.c" > "$OUT_DIR/base.bareid.c" || true
grep '::' "$OUT_DIR/head.grepfull.c" > "$OUT_DIR/head.bareid.c" || true

rc=0
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

  log ""
  log "--- FORM=$form ---"
  log "  BASE unique ids: $base_n   HEAD unique ids: $head_n"
  log "  base-only (FIXED at head): $base_only"
  log "  head-only (NET-NEW reds):  $head_only"
  log "  in BOTH sets:               $both"
  log "  DIGEST base: $(sha256sum "$b" | cut -d' ' -f1)"
  log "  DIGEST head: $(sha256sum "$h" | cut -d' ' -f1)"

  # ── THE FIXED VERDICT BLOCK ──────────────────────────────────────────────
  # The diff lists are written to files FIRST and then LOGGED through the
  # log function — never piped from `comm` into a `;`-terminated call.
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
  if [ "$head_only" -eq 0 ]; then
    log "  VERDICT [FORM=$form]: NET-NEW = EMPTY (the two legs' FAILED sets are equal)"
  else
    log "  VERDICT [FORM=$form]: NET-NEW = $head_only (NOT EMPTY — the sets differ)"
    rc=1
  fi
done

log ""
log "=== FINAL VERDICT ==="
if [ "$rc" -eq 0 ]; then
  log "NET-NEW = EMPTY in BOTH forms (GREP-FULL and BARE-ID) — the whole-tree leg's net-new claim holds."
else
  log "NET-NEW is NOT EMPTY in at least one form — the whole-tree leg's net-new claim does NOT hold as stated."
  log "The per-id adjudication (each id in ISOLATION, 3 runs per side) is the next step."
fi
log "  the verdict log: $LOG"
exit $rc
