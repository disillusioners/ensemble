#!/usr/bin/env bash
# FM4 WHOLE-TREE LEG — merged tree (6947cc2b8), method-identical to the
# base leg (/tmp/tdbp-wtbase/driver.sh) and rehead attempt-2 (driver2.sh):
#   * house --ignore files applied at census; corpus = census ids
#   * ONE pytest process at a time (strictly sequential)
#   * chunk = evidence only with a -q session summary; killed -> split in
#     two halves, re-run each ONCE; still dead -> UNRESOLVED (disclosed)
#   * fence: per-var env -u POSTGRES_x5 + DAEMON_PORT, ENSEMBLE_SELF_ENV=dev,
#     HOME=$(mktemp -d), certified interpreter, --timeout=120, timeout 1500
# Tests only: NO edits, NO commits, NO source changes.
set -uo pipefail

WT=/home/nea/ensemble-src-wt-tdbp-fmwt
PY=/home/nea/ensemble-src/.venv/bin/python
OUT=/tmp/tdbp-fm4
STATUS="$OUT/driver.status"
UNRESOLVED="$OUT/unresolved.txt"
: > "$STATUS"
: > "$UNRESOLVED"

cd "$WT" || { echo "FATAL: cannot cd worktree" | tee -a "$STATUS"; exit 1; }

run_list() {
  # $1 = ids file, $2 = log basename. Writes $2.full.log, $2.log (tail-40), $2.rc
  local list="$1" base="$2" rc
  mapfile -t IDS < "$list"
  local n=${#IDS[@]}
  [ "$n" -eq 0 ] && { echo "0" > "$OUT/$base.rc"; : > "$OUT/$base.full.log"; return 0; }
  timeout 1500 env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    ENSEMBLE_SELF_ENV=dev HOME="$(mktemp -d)" \
    "$PY" -m pytest "${IDS[@]}" -q --tb=no -rfE --timeout=120 \
    > "$OUT/$base.full.log" 2>&1
  rc=$?
  tail -40 "$OUT/$base.full.log" > "$OUT/$base.log"
  echo "$rc" > "$OUT/$base.rc"
}

summary_of() {
  tail -200 "$1" 2>/dev/null | grep -E "in [0-9]+(\.[0-9]+)?s( ?\(.+\))?$" | tail -1
}

CHUNKS=("$OUT"/chunk_ids_[0-9][0-9][0-9])
N=${#CHUNKS[@]}
START_ALL=$(date +%s)
echo "driver start $(date -u +%FT%TZ) chunks=$N params=--timeout=120 ENSEMBLE_SELF_ENV=dev house-ignores-at-census" >> "$STATUS"

i=0
for c in "${CHUNKS[@]}"; do
  i=$((i+1))
  name=$(basename "$c" | sed 's/chunk_ids_/chunk_/')
  t0=$(date +%s)
  run_list "$c" "$name"
  rc=$(cat "$OUT/$name.rc")
  t1=$(date +%s)
  S=$(summary_of "$OUT/$name.full.log")
  if [ -n "$S" ]; then
    echo "OK   $name rc=$rc $((t1-t0))s ids=$(wc -l < "$c") [$S] [$i/$N] $(date -u +%T)" >> "$STATUS"
    continue
  fi
  echo "DEAD $name rc=$rc $((t1-t0))s ids=$(wc -l < "$c") — split re-run [$i/$N] $(date -u +%T)" >> "$STATUS"
  total=$(wc -l < "$c")
  if [ "$total" -le 1 ]; then
    echo "UNRESOLVED $name (singleton, no summary) ids=$total" | tee -a "$STATUS" >> "$UNRESOLVED"
    continue
  fi
  h1=$(( (total + 1) / 2 ))
  split -l "$h1" -d -a 1 "$c" "$OUT/ids_${name}_"
  for sub in "$OUT/ids_${name}_"*; do
    [[ -s "$sub" ]] || continue
    sbase=$(basename "$sub" | sed 's/^ids_chunk_/rchunk_/')
    t0=$(date +%s)
    run_list "$sub" "$sbase"
    rc=$(cat "$OUT/$sbase.rc")
    t1=$(date +%s)
    S=$(summary_of "$OUT/$sbase.full.log")
    if [ -n "$S" ]; then
      echo "OK-SUB $sbase rc=$rc $((t1-t0))s ids=$(wc -l < "$sub") [$S] [$i/$N]" >> "$STATUS"
    else
      echo "UNRESOLVED $sbase rc=$rc $((t1-t0))s ids=$(wc -l < "$sub")" | tee -a "$STATUS" >> "$UNRESOLVED"
    fi
  done
done

END_ALL=$(date +%s)
echo "driver done $(date -u +%FT%TZ) total_wall=$((END_ALL-START_ALL))s" >> "$STATUS"
