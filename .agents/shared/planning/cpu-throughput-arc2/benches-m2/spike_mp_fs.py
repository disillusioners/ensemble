#!/usr/bin/env python
"""M2-B spike variant WITH freeze_support at entry (identical otherwise).

Diff vs spike_mp_nofs.py: the FIRST statement of the __main__ guard is
multiprocessing.freeze_support() — the canonical PyInstaller fix.
"""
import json
import os
import sys
import time

MARK = os.environ.get("M2_SPIKE_MARKER", "/tmp/m2-spike-markers.jsonl")


def note(kind, extra=None):
    line = json.dumps({
        "t": round(time.time(), 4), "pid": os.getpid(), "kind": kind,
        "frozen": getattr(sys, "frozen", False),
        "meipass": getattr(sys, "_MEIPASS", None),
        "argv": sys.argv[1:], "exe": sys.executable, "extra": extra,
    }) + "\n"
    fd = os.open(MARK, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def count_mains():
    try:
        with open(MARK) as f:
            return sum(1 for l in f if json.loads(l).get("kind") == "main")
    except FileNotFoundError:
        return 0


def work(x):
    note("worker-task")
    return x * x


def main():
    note("main")
    if count_mains() > 4:
        print("REENTRY-LOOP-DETECTED (main entered >4 times) -> os._exit(3)")
        sys.stdout.flush()
        os._exit(3)
    ctx_name = os.environ.get("M2_SPIKE_CTX", "spawn")
    import multiprocessing as mp
    ctx = mp.get_context(ctx_name)
    t0 = time.perf_counter()
    with ctx.Pool(2) as pool:
        res = pool.map(work, range(8))
    t_pool = time.perf_counter() - t0
    payload = "x" * 65536
    t0 = time.perf_counter()
    with ctx.Pool(1) as pool:
        rt = pool.map(len, [payload] * 50)
    t_roundtrip = time.perf_counter() - t0
    from concurrent.futures import ProcessPoolExecutor
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=2, mp_context=ctx) as ex:
        res2 = list(ex.map(work, range(8, 16)))
    t_ppe = time.perf_counter() - t0
    note("done", {"pool_s": round(t_pool, 3), "ppe_s": round(t_ppe, 3),
                  "pickle50_s": round(t_roundtrip, 3), "ctx": ctx_name})
    print(f"SPIKE-OK ctx={ctx_name} pool={t_pool:.3f}s ppe={t_ppe:.3f}s "
          f"pickle64KBx50={t_roundtrip:.3f}s results={len(res) + len(res2)}")
    sys.stdout.flush()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()  # THE VARIANT DELTA — first entry statement
    main()
