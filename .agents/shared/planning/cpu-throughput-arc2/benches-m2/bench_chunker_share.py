#!/usr/bin/env python
"""M2-C1b: WHERE does normal-shape estimate_tokens time actually go?

Splits the shipped 2C path (daemon/loader.py:1249-1252) into its two kernels:
  chunker  — pure-Python _iter_count_chunks back-scan (whitespace-boundary +
             >=16-identical-run logic)
  encoder  — native tiktoken encode() over the pre-cut chunks
"""
import sys
import time

sys.path.insert(0, "/home/nea/ensemble-src-wt-cpu-m2")
import tiktoken
from daemon.loader import _iter_count_chunks

enc = tiktoken.get_encoding("cl100k_base")

UNIT = ("The instance coordinator acknowledged the checkpoint and resumed "
        "the turn, dispatching a bounded worker wave with pathspec-limited "
        "commit discipline. https://example.invalid/path?q=1&id=2 logs at "
        "2026-11-21T03:38:52Z; status=ok. ")
TEXT = (UNIT * (512 * 1024 // len(UNIT) + 1))[:512 * 1024]
PATHO = ("a" * 512 * 1024)


def best_of(fn, n=3):
    ts = []
    ref = None
    for _ in range(n):
        t0 = time.perf_counter()
        ref = fn()
        ts.append(time.perf_counter() - t0)
    return min(ts), ref


for name, text in (("normal_prose", TEXT), ("patho_a_run", PATHO)):
    t_chunks, chunks = best_of(lambda: list(_iter_count_chunks(text, 4096)))
    t_encode, _ = best_of(lambda: sum(len(enc.encode(c)) for c in chunks))
    n = len(chunks)
    print(f"{name}: {len(text)} chars, {n} chunks @4096")
    print(f"  chunker (pure python) : {t_chunks*1000:8.1f} ms  ({t_chunks/(t_chunks+t_encode)*100:4.1f}% of shipped path)")
    print(f"  encoder (native c)    : {t_encode*1000:8.1f} ms  ({t_encode/(t_chunks+t_encode)*100:4.1f}%)")
    print(f"  shipped-path approx   : {(t_chunks+t_encode)*1000:8.1f} ms")
    if name == "normal_prose":
        t_whole, whole = best_of(lambda: len(enc.encode(text)))
        print(f"  exact single encode   : {t_whole*1000:8.1f} ms  exact tokens={whole}")
    else:
        # NOT running exact encode on 512 KB of 'a': that is the O(n^2) BPE
        # pathology 2C exists to avoid (observed >420 s hang in this lane).
        print("  exact single encode   : SKIPPED (O(n^2) pathology — 2C's reason for existing)")
