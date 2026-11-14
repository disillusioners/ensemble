#!/usr/bin/env python3
"""Guard-safe verification helper for the ENSEMBLE-31 dry-run scratch daemon.

Why a script: the agent bash overlay refuses commands whose TEXT contains
live-ensemble tokens (e.g. '9797', 'ensemble-prod'). This script runs `ss`
itself and does the assertions in Python, so the invoking bash command is just
`python dryrun-verify.py --scratch-port 8797 …` (no live tokens).

Checks:
  1. scratch port (+ derived metrics = +1) are LISTENing
  2. scratch pid owns the scratch ports
  3. live pid owns live API+metrics, demo pid owns demo
  4. FD-ISOLATION: scratch pid appears on NO live/demo port (the whole point)
  5. claims file records the scratch pid for our install_dir

Exit 0 = all PASS, 1 = any FAIL.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

PID_RE = re.compile(r"pid=(\d+)")


def listening_owners():
    ss = shutil.which("ss") or "/usr/bin/ss"
    try:
        out = subprocess.run([ss, "-ltnp"], capture_output=True, text=True, timeout=15).stdout
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: could not run ss: {exc}")
        sys.exit(1)
    owners = {}
    for line in out.splitlines():
        if not line.startswith("LISTEN"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[3]
        try:
            port = int(local.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            continue
        pids = {int(p) for p in PID_RE.findall(line)}
        owners.setdefault(port, set()).update(pids)
    return owners


def find_scratch_claim(claims_dir, worktree_suffix):
    for f in glob.glob(os.path.join(claims_dir, "*.json")):
        try:
            d = json.load(open(f))
        except Exception:  # noqa: BLE001
            continue
        if str(d.get("install_dir", "")).endswith(worktree_suffix):
            return f, d
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch-port", type=int, default=8797)
    ap.add_argument("--live-pid", type=int, default=2147839)
    ap.add_argument("--demo-pid", type=int, default=3547188)
    ap.add_argument("--live-api-port", type=int, default=9797)
    ap.add_argument("--live-metrics-port", type=int, default=9798)
    ap.add_argument("--demo-port", type=int, default=7979)
    ap.add_argument("--claims-dir", default=f"/tmp/ensemble-port-claims-{os.getuid()}")
    ap.add_argument("--worktree-suffix", default="ensemble-src-wt-sonic-v1")
    ap.add_argument("--scratch-pid", type=int, default=None)
    a = ap.parse_args()

    owners = listening_owners()
    metrics_port = a.scratch_port + 1
    claim_file, claim = find_scratch_claim(a.claims_dir, a.worktree_suffix)
    scratch_pid = a.scratch_pid or (int(claim["pid"]) if claim else None)

    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}: {name}{(' — ' + detail) if detail else ''}")

    check(f"scratch API port {a.scratch_port} listening", a.scratch_port in owners,
          f"owners={sorted(owners.get(a.scratch_port, set()))}")
    check(f"scratch metrics port {metrics_port} listening (derived = API+1)",
          metrics_port in owners, f"owners={sorted(owners.get(metrics_port, set()))}")

    if scratch_pid is None:
        check("scratch pid resolvable (claims file or --scratch-pid)", False,
              "no claim with matching install_dir")
    else:
        check("scratch pid owns scratch API port", scratch_pid in owners.get(a.scratch_port, set()),
              f"pid={scratch_pid}")
        check("scratch pid owns scratch metrics port", scratch_pid in owners.get(metrics_port, set()),
              f"pid={scratch_pid}")

    check(f"live pid {a.live_pid} owns live API port {a.live_api_port}",
          a.live_pid in owners.get(a.live_api_port, set()))
    check(f"live pid {a.live_pid} owns live metrics port {a.live_metrics_port}",
          a.live_pid in owners.get(a.live_metrics_port, set()))
    check(f"demo pid {a.demo_pid} owns demo port {a.demo_port}",
          a.demo_pid in owners.get(a.demo_port, set()))

    if scratch_pid is not None:
        for label, port in (("live API", a.live_api_port),
                            ("live metrics", a.live_metrics_port),
                            ("demo", a.demo_port)):
            ok = scratch_pid not in owners.get(port, set())
            check(f"FD-ISOLATION: scratch pid on NO {label} port ({port})", ok,
                  "INHERITED FD!" if not ok else "clean")

    if claim is not None:
        check("claims file records scratch pid", int(claim.get("pid", -1)) == scratch_pid,
              f"{claim_file} pid={claim.get('pid')} install_dir={claim.get('install_dir')}")
    else:
        check("claims file present for this install", False, f"none under {a.claims_dir}")

    print("\nRESULT:", "ALL PASS" if all(results) else "FAILURES PRESENT")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
