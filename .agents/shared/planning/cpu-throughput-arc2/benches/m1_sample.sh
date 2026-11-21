#!/usr/bin/env bash
# M1 live CPU sampler — READ-ONLY against live daemon.
# Reads /proc/stat (per-core jiffies), /proc/<PID>/task/*/stat+comm (thread
# CPU), ps snapshots (foreign-lane attribution) and public GET doors
# (/readyz, /api/health). NO writes to live, NO load injection, NO restart.
# Usage: m1_sample.sh <PID> <OUTDIR> <window_secs...>
#   e.g. m1_sample.sh 1208513 /tmp/m1_run 60 60 300
set -u
PID="${1:?need PID}"
OUTDIR="${2:?need OUTDIR}"
shift 2
WINDOWS=("$@")
mkdir -p "$OUTDIR"
echo "M1 sampler start $(date -u +%FT%TZ) pid=$PID windows=${WINDOWS[*]} outdir=$OUTDIR"

python3 - "$PID" "$OUTDIR" "${WINDOWS[@]}" <<'PY'
import os, sys, time, subprocess, datetime, json

pid = sys.argv[1]
outdir = sys.argv[2]
windows = [int(x) for x in sys.argv[3:]]

CLK = os.sysconf("SC_CLK_TCK")

def ts():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def read_cpu_stat():
    """Return dict cpuN -> (user,nice,sys,idle,iowait,irq,softirq,steal) and aggregate."""
    out = {}
    with open("/proc/stat") as f:
        for line in f:
            p = line.split()
            if not p: continue
            if p[0] == "cpu" or (p[0].startswith("cpu") and p[0][3:].isdigit()):
                vals = [int(x) for x in p[1:9]]  # user nice sys idle iowait irq softirq steal
                out[p[0]] = vals
    return out

def read_threads():
    """Return dict tid -> {comm, utime, stime, state}."""
    res = {}
    tdir = f"/proc/{pid}/task"
    for name in os.listdir(tdir):
        if not name.isdigit(): continue
        tid = name
        try:
            with open(f"{tdir}/{tid}/comm") as f:
                comm = f.read().strip()
            with open(f"{tdir}/{tid}/stat") as f:
                line = f.read()
            rest = line[line.rfind(")")+2:].split()  # after 'comm) '
            # rest[0]=state, ... rest[11]=utime(14), rest[12]=stime(15)
            state = rest[0]
            utime = int(rest[11]); stime = int(rest[12])
            res[tid] = {"comm": comm, "utime": utime, "stime": stime, "state": state}
        except (FileNotFoundError, ProcessLookupError, IndexError, ValueError):
            continue
    return res

def ps_snapshot(label):
    """Capture top CPU processes + daemon threads + readyz/health into files."""
    with open(f"{outdir}/ps_{label}.txt", "w") as f:
        f.write(f"# {ts()}\n")
        r = subprocess.run(["ps","-eo","pid,ppid,pcpu,pmem,nlwp,stat,comm,args","--sort=-pcpu"],
                           capture_output=True, text=True)
        f.write("\n".join(r.stdout.splitlines()[:35])+"\n")
    with open(f"{outdir}/pst_{label}.txt", "w") as f:
        f.write(f"# {ts()}\n")
        r = subprocess.run(["ps","-T","-p",pid,"-o","tid,stat,pcpu,comm"],
                           capture_output=True, text=True)
        f.write(r.stdout)
    for ep, fn in [("/readyz","readyz"),("/api/health","health")]:
        r = subprocess.run(["curl","-sS","--max-time","8",f"http://127.0.0.1:9797{ep}"],
                           capture_output=True, text=True)
        with open(f"{outdir}/{fn}_{label}.txt","w") as f:
            f.write(f"# {ts()} GET {ep}\n"+r.stdout+"\n")

def diff_cpu(a, b):
    """Return per-core dict busy_pct, total_jiffies, busy_jiffies."""
    out = {}
    for k in sorted(a, key=lambda x: (x!="cpu", int(x[3:]) if x[3:].isdigit() else -1)):
        if k not in b: continue
        av, bv = a[k], b[k]
        total = sum(bv[i]-av[i] for i in range(8))
        idle = (bv[3]-av[3])+(bv[4]-av[4])  # idle+iowait
        busy = total-idle
        pct = (busy/total*100) if total>0 else 0.0
        out[k] = {"total": total, "busy": busy, "idle": idle, "pct": round(pct,2)}
    return out

def diff_threads(a, b, wall):
    rows = []
    for tid in b:
        if tid not in a: continue
        d = (b[tid]["utime"]-a[tid]["utime"])+(b[tid]["stime"]-a[tid]["stime"])
        cpu_pct = d/CLK/wall*100 if wall>0 else 0.0
        rows.append((cpu_pct, d, tid, b[tid]["comm"], b[tid]["state"]))
    rows.sort(reverse=True)
    return rows

report = []
for i, w in enumerate(windows):
    label = f"w{i}"
    print(f"\n=== WINDOW {i} ({w}s) start {ts()} ===", flush=True)
    a_cpu = read_cpu_stat(); a_thr = read_threads()
    ps_snapshot(label+"_a")
    t0 = time.time()
    time.sleep(w)
    wall = time.time()-t0
    b_cpu = read_cpu_stat(); b_thr = read_threads()
    ps_snapshot(label+"_b")
    dcpu = diff_cpu(a_cpu, b_cpu)
    dthr = diff_threads(a_thr, b_thr, wall)
    block = {
        "window": i, "requested_s": w, "actual_wall_s": round(wall,2),
        "start_utc": ts(), "clk_tck": CLK,
        "per_core": dcpu,
        "threads_top": [
            {"cpu_pct": round(r[0],2), "jiffies": r[1], "tid": r[2], "comm": r[3], "state": r[4]}
            for r in dthr[:40]
        ],
        "thread_count": len(dthr),
        "total_thread_cpu_pct": round(sum(r[0] for r in dthr),2),
    }
    report.append(block)
    # console summary
    cores = [f"{k}={dcpu[k]['pct']}%" for k in dcpu if k!="cpu"]
    print("AGG cpu=%s%% busy_j=%d/%d" % (dcpu.get("cpu",{}).get("pct"),
          dcpu.get("cpu",{}).get("busy"), dcpu.get("cpu",{}).get("total")), flush=True)
    print("CORES "+" ".join(cores), flush=True)
    print("THREADS top:", flush=True)
    for r in dthr[:15]:
        print("  %6.2f%%  %5dj  tid=%s  %s [%s]" % (r[0], r[1], r[2], r[3], r[4]), flush=True)
    print("  (total threads=%d, sum cpu=%.1f%%)" % (len(dthr), sum(r[0] for r in dthr)), flush=True)

with open(f"{outdir}/m1_report.json","w") as f:
    json.dump(report, f, indent=2)
print(f"\nM1 sampler DONE {ts()} -> {outdir}/m1_report.json", flush=True)
PY
echo "M1 sampler exited $(date -u +%FT%TZ)"
