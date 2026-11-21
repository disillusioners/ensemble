#!/usr/bin/env python
"""M2-A1: Is the worktree venv interpreter a standard GIL build?

Evidence: sys._is_gil_enabled(), sysconfig Py_GIL_DISABLED, plus a measured
CPU-bound 1-thread vs 4-thread wall-clock speedup (GIL present => ~1.0x).
"""
import json
import sys
import sysconfig
import threading
import time

WORK_UNITS = 4          # 4 independent chunks
CHUNK_ITERS = 2_600_000  # tuned so each chunk ~= 0.35 s on this host
REPEATS = 3


def cpu_chunk(n: int) -> int:
    """Pure-Python CPU-bound kernel (no GIL-releasing C calls)."""
    acc = 0
    x = 0
    for i in range(n):
        x = (x * 31 + i) % 1_000_003
        acc += x
    return acc


def run_serial():
    t0 = time.perf_counter()
    results = [cpu_chunk(CHUNK_ITERS) for _ in range(WORK_UNITS)]
    return time.perf_counter() - t0, results


def run_threads():
    results = [0] * WORK_UNITS
    def w(i):
        results[i] = cpu_chunk(CHUNK_ITERS)
    threads = [threading.Thread(target=w, args=(i,)) for i in range(WORK_UNITS)]
    t0 = time.perf_counter()
    for t in threads: t.start()
    for t in threads: t.join()
    return time.perf_counter() - t0, results


def main():
    out = {
        "python": sys.version,
        "implementation": sys.implementation.name,
        "gil_disabled_config_var": sysconfig.get_config_var("Py_GIL_DISABLED"),
        "is_gil_enabled": sys._is_gil_enabled(),
        "abiflags": getattr(sys, "abiflags", "<no attr>"),
        "flags": str(sys.flags),
    }
    print(json.dumps(out, indent=2, default=str))

    serials, threaded = [], []
    ref = None
    for _ in range(REPEATS):
        ts, rs = run_serial()
        tt, rt = run_threads()
        assert rs == rt, "thread/serial results diverge"
        if ref is None: ref = rs
        serials.append(ts)
        threaded.append(tt)
    best_s, best_t = min(serials), min(threaded)
    print(f"serial  best={best_s:.3f}s all={[round(x,3) for x in serials]}")
    print(f"threads best={best_t:.3f}s all={[round(x,3) for x in threaded]}")
    print(f"SPEEDUP (best serial / best 4-thread) = {best_s / best_t:.3f}x  "
          f"({'GIL-bound as expected' if best_s / best_t < 1.6 else 'SCALES — GIL ABSENT?'})")


if __name__ == "__main__":
    main()
