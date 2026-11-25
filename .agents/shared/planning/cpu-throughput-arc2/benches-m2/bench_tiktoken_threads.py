#!/usr/bin/env python
"""M2-C1: does tiktoken release the GIL on OUR post-arc1 call shapes?

Shape = daemon.loader.estimate_tokens AS SHIPPED (row2-agent-turn-cpu 2C):
cl100k_base, encoder.encode(), threshold 16K chars, 4K chunks split at
whitespace boundaries with the shape-aware >=16-identical-run rule
(daemon/loader.py:1121-1254). Measured: serial wall vs 4-thread wall over
four INDEPENDENT texts (GIL released => speedup ~4x; held => ~1x or worse).
"""
import json
import sys
import threading
import time

sys.path.insert(0, "/home/nea/ensemble-src-wt-cpu-m2")
from daemon.loader import estimate_tokens  # the real shipped function

REPEATS = 3
UNIT_KB = 512  # per-text payload; 512 KB > 16 KB threshold => chunked path


def make_text(unit: str) -> str:
    text = (unit * (UNIT_KB * 1024 // len(unit) + 1))[:UNIT_KB * 1024]
    return text


NORMAL = make_text(
    "The instance coordinator acknowledged the checkpoint and resumed "
    "the turn, dispatching a bounded worker wave with pathspec-limited "
    "commit discipline. https://example.invalid/path?q=1&id=2 logs at "
    "2026-11-21T03:38:52Z; status=ok. ")
PATHOLOGICAL = make_text("a" * 512 * 1024)          # single-piece run (hard splits)
LF_RUNS = make_text("line\n" * 64 + "\n" * 300)      # >=16-identical-run shape-aware cuts
SMALL = NORMAL[:8192]                                # <= 16 KB threshold: exact path


def measure(texts):
    serials = []
    ref = None
    for _ in range(REPEATS):
        t0 = time.perf_counter()
        res = [estimate_tokens(t) for t in texts]
        dt = time.perf_counter() - t0
        serials.append(dt)
        if ref is None: ref = res

    totals = [0] * len(texts)
    def w(k):
        totals[k] = estimate_tokens(texts[k])
    threads = [threading.Thread(target=w, args=(k,)) for k in range(len(texts))]
    t0 = time.perf_counter()
    for t in threads: t.start()
    for t in threads: t.join()
    dt_threads = time.perf_counter() - t0
    assert totals == ref, f"thread totals {totals} != serial {ref}"
    return min(serials), dt_threads, ref


def main():
    out = {"tiktoken_estimated_by": "daemon.loader.estimate_tokens (cl100k_base)",
           "text_kb_each": UNIT_KB, "n_texts": 4}
    report = {}
    for name, texts in (
        ("normal", [NORMAL + f"#{i}" for i in range(4)]),
        ("pathological_a_run", [PATHOLOGICAL] * 4),
        ("lf_runs", [LF_RUNS + f"#{i}" for i in range(4)]),
        ("small_exact_path_8k", [SMALL + f"#{i}" for i in range(4)]),
    ):
        s, t, ref = measure(texts)
        report[name] = {
            "tokens_per_text": ref[0],
            "serial_best_s": round(s, 4),
            "threads4_s": round(t, 4),
            "speedup_4t": round(s / t, 3),
            "verdict": "GIL HELD" if s / t < 1.6 else "GIL RELEASED",
        }
    out["results"] = report
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
