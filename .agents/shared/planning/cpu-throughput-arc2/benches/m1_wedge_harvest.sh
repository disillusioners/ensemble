#!/usr/bin/env bash
# M1 wedge-capture harvest — READ-ONLY against /home/nea/agents-ensemble/data/wedge-captures.
# Summarises every capture: version, CPU%, sustained_s, executing turns, top threads,
# pool gauges. Honors POK offloop-audit-01 caveat: version mix is disclosed, not hidden.
# Usage: m1_wedge_harvest.sh [CAPTURE_ROOT]
set -u
ROOT="${1:-/home/nea/agents-ensemble/data/wedge-captures}"
python3 - "$ROOT" <<'PY'
import json, glob, os, csv, sys, collections
root = sys.argv[1]
rows = []
for d in sorted(glob.glob(root + "/*/")):
    mf = d + "manifest.json"
    if not os.path.exists(mf):
        continue
    m = json.load(open(mf))
    top = []
    tf = d + "threads.csv"
    if os.path.exists(tf):
        r = list(csv.DictReader(open(tf)))
        r = [x for x in r if float(x.get("cpu_delta_s") or 0) > 0]
        r.sort(key=lambda x: float(x["cpu_delta_s"]), reverse=True)
        top = [f'{x["name"]}:{x["cpu_delta_s"]}s' for x in r[:3]]
    turns = None
    df = d + "db.json"
    if os.path.exists(df):
        turns = json.load(open(df)).get("executing_turns_count")
    obs = m.get("observed", {})
    rows.append((m.get("captured_at", "?"), m.get("release_version", "?"),
                 obs.get("process_cpu_pct"), obs.get("sustained_s"), turns, top,
                 m.get("trigger_reason")))
print(f"{'captured_at':18} {'ver':9} {'cpu%':>8} {'sust_s':>7} {'turns':>5}  top3 (name:cpu_s)")
for r in rows:
    print(f"{r[0]:18} {r[1]:9} {str(r[2]):>8} {str(r[3]):>7} {str(r[4]):>5}  {', '.join(r[5])}")
print("\nVERSION MIX:", dict(collections.Counter(r[1] for r in rows)))
print("TRIGGER MIX:", dict(collections.Counter(r[6] for r in rows)))
print("CAPTURE COUNT:", len(rows))
PY
