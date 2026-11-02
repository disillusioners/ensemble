#!/usr/bin/env bash
# The whole-tree base-proven diff — SHARDED (the canon's §4/§5 form).
#
# The single-shot `-n auto` run aborts on this host: a worker exits
# while a node is in flight (reproduced at BASE too — a PRE-EXISTING
# host/load class, not a lane defect), and xdist turns that into an
# INTERNALERROR that loses the whole leg.  The canon's sharded form
# survives it: the shard's results are persisted, and a crashed shard
# is re-run WITHOUT the crashing node (which is re-run alone and
# classified individually).
#
# BASE LEG FIRST, then HEAD LEG. Never concurrently. Identical command shape.

set -uo pipefail

PY=/home/nea/ensemble-src/.venv/bin/python
BASE_WT=/home/nea/ensemble-src-wt-central-metrics-v1-base
HEAD_WT=/home/nea/ensemble-src-wt-central-metrics-v1-head
SWEEP_DIR=${SWEEP_DIR:-/tmp/p3-sweep-sharded-$(date -u +%Y%m%dT%H%M%SZ)}
SHARD_SIZE=${SHARD_SIZE:-2500}
XPROC=${XPROC:-12}
mkdir -p "$SWEEP_DIR"

FENCE=(env
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER
  -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL
  -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGPASSWORD -u PGSERVICE -u PGOPTIONS
  -u ENSEMBLE_SELF_ENV -u ENSEMBLE_UPGRADE_LIVE -u ENSEMBLE_UPGRADE_SCRIPTS_DIR
  -u ENSEMBLE_RESTART_UNIT
  OPENAI_BASE_URL=http://127.0.0.1:9/v1
  OPENAI_BASE_URL_BACKUP=http://127.0.0.1:9/v1
  OPENAI_REQUEST_TIMEOUT=5
  QUEUE_LLM_RETRY_TRANSIENT_ATTEMPTS=1
  QUEUE_LLM_RETRY_TIMEOUT_ATTEMPTS=1
  PYTHONDONTWRITEBYTECODE=1
)

log() { echo "$@" | tee -a "$SWEEP_DIR/sweep.log"; }

log "=== sweep dir: $SWEEP_DIR ==="
log "=== CPUs: $(nproc) · xdist: $XPROC · shard size: $SHARD_SIZE ==="

# ── the collection (the pre-exclusion registry baked in) ──────────────────
for leg in BASE HEAD; do
  wt=$BASE_WT; [ "$leg" = HEAD ] && wt=$HEAD_WT
  ( cd "$wt" && "${FENCE[@]}" timeout 600 "$PY" -m pytest --collect-only -q tests/ \
      --ignore=tests/packs \
      --ignore=tests/e2e/test_context_injection_hybrid.py \
      > "$SWEEP_DIR/$leg.collect.txt" 2>&1 )
  grep '::' "$SWEEP_DIR/$leg.collect.txt" | sort > "$SWEEP_DIR/$leg.nodes.txt"
  log "$leg collected: $(wc -l < "$SWEEP_DIR/$leg.nodes.txt") nodes"
done
comm -13 "$SWEEP_DIR/BASE.nodes.txt" "$SWEEP_DIR/HEAD.nodes.txt" > "$SWEEP_DIR/head_only.nodes.txt"
log "head-only nodes (the additive lane's own files): $(wc -l < "$SWEEP_DIR/head_only.nodes.txt")"
log "base-only nodes: $(comm -23 "$SWEEP_DIR/BASE.nodes.txt" "$SWEEP_DIR/HEAD.nodes.txt" | wc -l)"

# ── the shard plan (by FILE boundary) ─────────────────────────────────────
python3 - "$SWEEP_DIR" "$SHARD_SIZE" <<'PYEOF'
import sys
from pathlib import Path

sweep_dir = Path(sys.argv[1])
shard_size = int(sys.argv[2])
for leg in ("BASE", "HEAD"):
    nodes = (sweep_dir / f"{leg}.nodes.txt").read_text().splitlines()
    shards: list[list[str]] = []
    current: list[str] = []
    current_file = None
    for node in nodes:
        node_file = node.split("::", 1)[0]
        if current_file is not None and node_file != current_file and len(current) >= shard_size:
            shards.append(current)
            current = []
        current_file = node_file
        current.append(node)
        if len(current) >= shard_size:
            shards.append(current)
            current = []
    if current:
        shards.append(current)
    out_dir = sweep_dir / f"{leg}.shards"
    out_dir.mkdir(exist_ok=True)
    for index, shard in enumerate(shards):
        (out_dir / f"shard-{index:03d}.txt").write_text("\n".join(shard) + "\n")
    print(f"{leg}: {len(shards)} shards")
PYEOF

run_shard() {
  # $1 = leg, $2 = worktree, $3 = shard file, $4 = output file
  local leg="$1" wt="$2" shard="$3" out="$4"
  # mapfile + a quoted array: an unquoted $(cat f) word-splits AND the
  # argument list overflows on a 2500-node shard (rc=126).
  ( cd "$wt" && "${FENCE[@]}" timeout 1200 bash -c '
      mapfile -t NODEIDS < "$1"
      shift
      exec "$@" "${NODEIDS[@]}"
    ' _ "$shard" "$PY" -m pytest \
      -q --tb=line -rEf -n "$XPROC" --timeout=120 -p no:cacheprovider \
      > "$out" 2>&1 )
  return $?
}

run_leg() {
  local leg="$1" wt="$2"
  local shard_dir="$SWEEP_DIR/$leg.shards"
  local progress="$SWEEP_DIR/$leg.progress.tsv"
  local failed="$SWEEP_DIR/$leg.failed_ids.txt"
  local singles="$SWEEP_DIR/$leg.singles.tsv"
  : > "$progress"; : > "$failed"; : > "$singles"
  local index=0
  for shard in "$shard_dir"/shard-*.txt; do
    local name; name=$(basename "$shard" .txt)
    local out="$SWEEP_DIR/$leg.$name.out"
    local started; started=$(date +%s)
    run_shard "$leg" "$wt" "$shard" "$out"
    local rc=$?
    local dur=$(( $(date +%s) - started ))
    if [ "$rc" = 3 ]; then
      # The worker died mid-node: recover the canon's way — isolate the
      # crashing node, re-run it ALONE, and re-run the shard without it.
      local crash_node
      crash_node=$(grep -oE "assert not '[^']+'" "$out" | head -1 | sed "s/assert not '//; s/'$//")
      log "  $leg/$name CRASHED (rc=3) at ${crash_node:-<unknown>} — recovering"
      if [ -n "${crash_node:-}" ]; then
        local single_out="$SWEEP_DIR/$leg.$name.single.out"
        ( cd "$wt" && "${FENCE[@]}" timeout 300 "$PY" -m pytest \
            "$crash_node" -q --tb=line -p no:randomly > "$single_out" 2>&1 )  # single node: fine
        local src=$?
        echo "$crash_node|$src" >> "$singles"
        grep -E '^(FAILED|ERROR)' "$single_out" | sed -E 's/^(FAILED|ERROR) //' >> "$failed"
        log "    the isolated node: rc=$src ($(tail -1 "$single_out"))"
        # re-run the shard WITHOUT the crashing node
        grep -vFx "$crash_node" "$shard" > "$shard.without"
        run_shard "$leg" "$wt" "$shard.without" "$out"
        rc=$?
      fi
    fi
    grep -E '^(FAILED|ERROR)' "$out" | sed -E 's/^(FAILED|ERROR) //' >> "$failed"
    local p f e
    p=$(grep -oE '[0-9]+ passed' "$out" | tail -1 | grep -oE '[0-9]+' || echo 0)
    f=$(grep -oE '[0-9]+ failed' "$out" | tail -1 | grep -oE '[0-9]+' || echo 0)
    e=$(grep -oE '[0-9]+ error' "$out" | tail -1 | grep -oE '[0-9]+' || echo 0)
    echo "$name|$(wc -l < "$shard")|$p|$f|$e|$dur|$rc" >> "$progress"
    log "  $leg/$name nodes=$(wc -l < "$shard") P=$p F=$f E=$e dur=${dur}s rc=$rc"
  done
  sort -u "$failed" > "$SWEEP_DIR/$leg.nodeids.txt"
  log "$leg FAILED/ERROR ids: $(wc -l < "$SWEEP_DIR/$leg.nodeids.txt")"
  log "$leg digest (BARE-ID): $(sha256sum "$SWEEP_DIR/$leg.nodeids.txt" | cut -d' ' -f1)"
}

log ""
log "=== sweep leg: BASE ==="
run_leg BASE "$BASE_WT"
log ""
log "=== sweep leg: HEAD ==="
run_leg HEAD "$HEAD_WT"

log ""
log "=== SWEEP DIGEST DIFF ==="
b="$SWEEP_DIR/BASE.nodeids.txt"; h="$SWEEP_DIR/HEAD.nodeids.txt"
if cmp -s "$b" "$h"; then
  log "DIGEST-EQUAL: the FAILED sets are byte-identical (net-new = EMPTY)"
else
  log "-- base-only (FIXED at head) --"; comm -23 "$b" "$h" | sed 's/^/  /'
  log "-- head-only (NET-NEW reds) --"; comm -13 "$b" "$h" | sed 's/^/  /'
fi
log ""
log "=== sweep dir: $SWEEP_DIR ==="
