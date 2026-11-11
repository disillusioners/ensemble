#!/usr/bin/env python3
"""RAM-guard measurement harness — T18 (the per-tick scan cost) and
T19 (the allocation rate R).

Run from the worktree root with the worktree interpreter::

    ./.venv/bin/python scripts/ram_guard_measurements.py

**The allocation is BOUNDED (T19).**  The allocator never touches the
host's full RAM: it allocates a CAPPED total (``--alloc-mib``, default
256 MiB) inside a BOUNDED observation window (``--window-seconds``,
default 2.0 s) and releases it before exiting.  This is a measurement,
never a load injection and never a repro attempt.

**The scan-population axis (T18).**  The shared ``/proc`` scan is
O(host pids) per tick.  The harness builds a fake procfs tree at each
requested population and reports the median + p95 per-tick cost.

Nothing here is a test: the numbers are RECORDED (test-plan §3.10), not
asserted.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from daemon.services.ram_guard.tree_reader import (  # noqa: E402
    read_tree_pss,
    read_tree_rss,
    scan_proc,
)

MIB = 1024 * 1024


def build_fake_procfs(root: Path, population: int) -> None:
    """A fake procfs tree with ``population`` live entries."""
    root.mkdir(parents=True, exist_ok=True)
    tail = (
        "S 1 1 1 0 -1 4194560 1 0 0 0 0 0 0 0 20 0 1 0 1000 0 256"
    )
    for pid in range(1, population + 1):
        pid_dir = root / str(pid)
        pid_dir.mkdir(exist_ok=True)
        (pid_dir / "stat").write_text(f"{pid} (proc) {tail}\n")
        (pid_dir / "status").write_text("Name:\tproc\nVmRSS:\t1024 kB\n")
        (pid_dir / "smaps_rollup").write_text("Rss:\t1024 kB\nPss:\t512 kB\n")


def _stats(samples_ms: list[float]) -> dict:
    ordered = sorted(samples_ms)
    return {
        "median_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 3),
        "min_ms": round(ordered[0], 3),
        "max_ms": round(ordered[-1], 3),
    }


def measure_scan(root: Path, iterations: int) -> dict:
    """The per-tick shared-scan cost at one population."""
    samples_ms: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        table = scan_proc(root)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if not table.scan_complete:
            raise SystemExit("the scan did not complete — the measurement is void")
        samples_ms.append(elapsed_ms)
    row = {"population": len(list(root.iterdir())), "iterations": iterations}
    row.update(_stats(samples_ms))
    return row


def measure_readers(root: Path, iterations: int) -> dict:
    """The per-member reader cost — the RSS trigger and the PSS confirm.

    CAVEAT: on a FAKE tree this measures the READER's overhead (the
    open + parse), NOT the kernel's ``smaps_rollup`` generation cost.
    The kernel-side per-member cost is the host-measured E6 model
    (``≈0.10 ms + 0.0115 ms/MiB × RSS``) — the fake UNDERSTATES it.
    """
    members = [int(p.name) for p in root.iterdir() if p.name.isdigit()]
    rss_samples: list[float] = []
    pss_samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        read_tree_rss(root, members)
        rss_samples.append((time.perf_counter() - started) * 1000.0)
        started = time.perf_counter()
        read_tree_pss(root, members)
        pss_samples.append((time.perf_counter() - started) * 1000.0)
    row = {"population": len(members), "members": len(members)}
    row["rss_reader"] = _stats(rss_samples)
    row["pss_reader"] = _stats(pss_samples)
    return row


def measure_allocation_rate(total_mib: int, window_seconds: float) -> dict:
    """The allocation rate R — a CAPPED, BOUNDED synthetic allocator.

    The allocator allocates in chunks, touching every page so the
    memory is really resident, and stops at the cap.  The observed rate
    is the allocated bytes divided by the elapsed time.
    """
    cap_bytes = total_mib * MIB
    chunk_bytes = 4 * MIB
    blocks: list[bytearray] = []
    started = time.perf_counter()
    allocated = 0
    while allocated < cap_bytes:
        block = bytearray(chunk_bytes)
        # Touch every page so the pages are really resident.
        for offset in range(0, chunk_bytes, 4096):
            block[offset] = 1
        blocks.append(block)
        allocated += chunk_bytes
        if time.perf_counter() - started > window_seconds:
            break
    elapsed = time.perf_counter() - started
    rate_bps = allocated / elapsed if elapsed > 0 else 0.0
    # Release before returning — the harness leaves no residue.
    del blocks
    return {
        "allocated_mib": round(allocated / MIB, 2),
        "elapsed_seconds": round(elapsed, 4),
        "rate_mib_per_second": round(rate_bps / MIB, 3),
        "cap_mib": total_mib,
        "window_seconds": window_seconds,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--populations",
        type=int,
        nargs="+",
        default=[250, 1000],
        help="the scan populations to measure (T18)",
    )
    parser.add_argument("--iterations", type=int, default=25)
    parser.add_argument("--alloc-mib", type=int, default=256, help="the allocation CAP (T19)")
    parser.add_argument("--window-seconds", type=float, default=2.0)
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args(argv)

    report: dict = {"scan": [], "readers": [], "allocation": None, "host_pids": None}

    try:
        report["host_pids"] = len([p for p in Path("/proc").iterdir() if p.name.isdigit()])
    except OSError:
        report["host_pids"] = None

    with tempfile.TemporaryDirectory(prefix="ram-guard-measure-") as tmp:
        for population in args.populations:
            root = Path(tmp) / f"proc-{population}"
            build_fake_procfs(root, population)
            report["scan"].append(measure_scan(root, args.iterations))
            report["readers"].append(measure_readers(root, max(3, args.iterations // 5)))

        report["allocation"] = measure_allocation_rate(args.alloc_mib, args.window_seconds)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print("== T18 — the per-tick shared-scan cost (scan-population axis) ==")
    for row in report["scan"]:
        print(
            f"  population={row['population']:>5}  median={row['median_ms']:>8.3f} ms"
            f"  p95={row['p95_ms']:>8.3f} ms  (n={row['iterations']})"
        )
    print(f"  host pid count: {report['host_pids']}")
    print()
    print("== T18b — the per-member reader cost (the fake tree understates it) ==")
    for row in report["readers"]:
        rss = row["rss_reader"]
        pss = row["pss_reader"]
        print(
            f"  members={row['members']:>5}  rss median={rss['median_ms']:>8.3f} ms"
            f"  pss median={pss['median_ms']:>8.3f} ms"
            f"  (per member: rss {rss['median_ms'] / row['members']:.4f} ms,"
            f" pss {pss['median_ms'] / row['members']:.4f} ms)"
        )
    print()
    print("== T19 — the allocation rate R (bounded allocation) ==")
    alloc = report["allocation"]
    print(
        f"  allocated {alloc['allocated_mib']} MiB in {alloc['elapsed_seconds']} s"
        f" ⇒ R = {alloc['rate_mib_per_second']} MiB/s"
    )
    print(f"  allocation bound: cap {alloc['cap_mib']} MiB, window {alloc['window_seconds']} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
