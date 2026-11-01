#!/bin/bash
# wi-1fbee2ec G9 driver — net-new-failure discipline, scoped 108-file corpus, BOTH sides.
# BASE fully first, then HEAD; identical chunks + flags. Every invocation wrapped timeout 300 (-k 15 kill backstop).
set -u
BASE_WT=/home/nea/ensemble-src-wt-obligation-base
HEAD_WT=/home/nea/ensemble-src-wt-obligation
LIST=/tmp/recon-g9-base.txt
CH=/tmp/wi1f-g9-chunks
RUNNER=/tmp/wi1f-g9-py.sh
mkdir -p "$CH"
rm -f "$CH"/chunk-*.txt /tmp/wi1f-g9-rc.tsv /tmp/wi1f-g9-base-chunk-*.log /tmp/wi1f-g9-head-chunk-*.log

# dedup preserving order, then chunk by 8
awk 'NF && !seen[$0]++' "$LIST" > /tmp/wi1f-g9-list-dedup.txt
split -l 8 -d -a 2 --additional-suffix=.txt /tmp/wi1f-g9-list-dedup.txt "$CH/chunk-"
echo "CHUNKS=$(ls "$CH"/chunk-*.txt | wc -l) FILES=$(wc -l < /tmp/wi1f-g9-list-dedup.txt) START=$(date -u '+%FT%TZ')"

run_side() {
  local side="$1" wt="$2" cf base log rc nfiles
  for cf in "$CH"/chunk-*.txt; do
    base=$(basename "$cf" .txt)
    log="/tmp/wi1f-g9-${side}-${base}.log"
    nfiles=$(wc -l < "$cf")
    echo "### ${side} ${base} START=$(date -u '+%FT%TZ') files=${nfiles}"
    mapfile -t files < "$cf"
    timeout -k 15 300 bash "$RUNNER" "$wt" "${files[@]}" > "$log" 2>&1
    rc=$?
    echo "### ${side} ${base} rc=${rc} END=$(date -u '+%FT%TZ')"
    tail -2 "$log"
    printf '%s\t%s\t%s\n' "$side" "$base" "$rc" >> /tmp/wi1f-g9-rc.tsv
  done
}

run_side base "$BASE_WT"
run_side head "$HEAD_WT"
echo "ALL DONE=$(date -u '+%FT%TZ')"
