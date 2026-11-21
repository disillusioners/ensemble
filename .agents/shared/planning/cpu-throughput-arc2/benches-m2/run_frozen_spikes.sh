#!/usr/bin/env bash
# M2-B runner: fenced execution of the frozen multiprocessing spikes.
# Fences: ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY (the
# boot-sweep guard); DAEMON_PORT=8079-class; POSTGRES_* scrubbed PER-VARIABLE
# (`env -u POSTGRES_*` is a SILENT NO-OP — never use it); scratch-only marker
# dir; each run wrapped in timeout 45. Output goes to a FILE (not a pipe):
# boot-loop children inherit the write end of a stdout pipe and keep `tail`
# waiting on EOF forever (observed on the first runner draft). Leaked spike
# children are killed after every run by exact spike-build path match — the
# live binary (/home/nea/agents-ensemble/current/ensemble-prod) never matches.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
DIST="$HERE/spike-build/dist"
PYBIN="/home/nea/ensemble-src-wt-cpu-m2/.venv/bin/python"
SCRATCH="$(mktemp -d /tmp/m2-spike.XXXXXX)"
export ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0
export DAEMON_PORT=8079
export ENSEMBLE_SELF_ENV=dev
echo "scratch=$SCRATCH fences: SWEEP=$ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES PORT=$DAEMON_PORT"

summarize() {
  "$PYBIN" - "$SCRATCH/markers.jsonl" <<'PYEOF'
import json, sys
try:
    rows = [json.loads(l) for l in open(sys.argv[1])]
except FileNotFoundError:
    print("  markers: <none>")
    sys.exit(0)
mains = [r for r in rows if r["kind"] == "main"]
print(f"  markers: total={len(rows)} main_entries={len(mains)} worker_tasks={sum(1 for r in rows if r['kind']=='worker-task')}")
for r in mains[:6]:
    print(f"    main pid={r['pid']} frozen={r['frozen']} argv={r['argv']} meipass={'yes' if r['meipass'] else 'no'}")
if len(mains) > 6:
    print(f"    ... +{len(mains)-6} more main entries")
PYEOF
}

for BIN in spike_mp_nofs spike_mp_fs; do
  for CTX in spawn fork; do
    rm -f "$SCRATCH/markers.jsonl"
    echo "================ $BIN ctx=$CTX ================"
    timeout 45 env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
      -u POSTGRES_HOST -u POSTGRES_PORT \
      M2_SPIKE_CTX="$CTX" M2_SPIKE_MARKER="$SCRATCH/markers.jsonl" \
      "$DIST/$BIN" > "$SCRATCH/out_${BIN}_${CTX}.txt" 2>&1
    echo "  rc=$? (124 = timeout/hang)"
    echo "  stdout/stderr (last 6 lines):"
    tail -6 "$SCRATCH/out_${BIN}_${CTX}.txt" | sed 's/^/    /'
    pkill -9 -f "spike_mp_[nof]s" 2>/dev/null || true
    sleep 1
    summarize
  done
done
echo "ALL-SPIKE-RUNS-COMPLETE scratch=$SCRATCH"
