#!/usr/bin/env python
"""M2-C1c: isolate WHICH part of tiktoken holds the GIL.

tiktoken.Encoding.encode() = Python-side regex pre-tokenizer (_pat_str)
+ native BPE core (_core_bpe). Measure both separately, serial vs 4 threads,
on the normal-prose shape. Also 4-thread hashlib and re scans for contrast.
"""
import hashlib
import json
import re
import threading
import time

import tiktoken

enc = tiktoken.get_encoding("cl100k_base")
UNIT = ("The instance coordinator acknowledged the checkpoint and resumed "
        "the turn, dispatching a bounded worker wave with pathspec-limited "
        "commit discipline. https://example.invalid/path?q=1&id=2 logs at "
        "2026-11-21T03:38:52Z; status=ok. ")
TEXT = (UNIT * (512 * 1024 // len(UNIT) + 1))[:512 * 1024]
TEXTS = [TEXT + f"#{i}" for i in range(4)]
NTHREADS = 4


def parallel(fn, items):
    out = [None] * len(items)
    def w(k): out[k] = fn(items[k])
    ts = [threading.Thread(target=w, args=(k,)) for k in range(len(items))]
    t0 = time.perf_counter()
    for t in ts: t.start()
    for t in ts: t.join()
    return time.perf_counter() - t0, out


def report(name, fn, items, ref_check=True):
    t0 = time.perf_counter()
    serial = [fn(x) for x in items]
    ts = time.perf_counter() - t0
    tp, par = parallel(fn, items)
    if ref_check and isinstance(serial[0], int):
        assert serial == par, f"{name}: parallel != serial"
    return {"serial_s": round(ts, 4), "threads4_s": round(tp, 4),
            "speedup": round(ts / tp, 3)}


out = {}
out["encode_full"] = report("encode_full", lambda t: len(enc.encode(t)), TEXTS)
out["bpe_core_only"] = report("bpe_core_only",
                              lambda t: len(enc._core_bpe.encode_ordinary(t)), TEXTS)
pat = getattr(enc, "_pat_str", None)
if pat is not None:
    import regex  # tiktoken's own pre-tokenizer engine
    pat_re = regex.compile(pat)
    out["regex_pretokenizer_only"] = report("regex_pretokenizer_only",
                                            lambda t: len(pat_re.findall(t)), TEXTS)
else:
    out["regex_pretokenizer_only"] = "enc._pat_str not present in this tiktoken build"

# hashlib: 4 x 128 MB sha256 (GIL released for >2KB buffers)
CHUNK = bytes(128 * 1024 * 1024)
def sha(x):
    h = hashlib.sha256()
    for _ in range(8): h.update(CHUNK[:16 * 1024 * 1024])
    return h.hexdigest()[:8]
out["hashlib_sha256_128MBx4"] = report("hash", lambda _: sha(0), [0] * 4)

# re: 4 x 20 MB uuid scan (GIL held)
unit = ("instance b4e0220d-f9ff-4c08-8419-b707dd3ad773 resumed at "
        "2026-11-21T03:38:52Z; contact ops@example.com ")
RTXT = (unit * (20 * 1024 * 1024 // len(unit) + 1))[:20 * 1024 * 1024]
P = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
out["re_uuid_scan_20MBx4"] = report("re", lambda t: len(P.findall(t)), [RTXT] * 4)

print(json.dumps(out, indent=2))

# Same split on the pathological a-run AT THE SHIPPED 4K CHUNK GRID.
# Feeding a raw 512 KB single piece to the native core is the O(n^2) BPE
# pathology 2C exists to avoid (observed >240 s hang) — never bench that.
A_GROUPS = [["a" * 4096 for _ in range(128)] for _ in range(4)]


def group_report(name, fn):
    t0 = time.perf_counter()
    serial = [fn(g) for g in A_GROUPS]
    ts = time.perf_counter() - t0
    tp, par = parallel(fn, A_GROUPS)
    assert serial == par
    return {"serial_s": round(ts, 4), "threads4_s": round(tp, 4),
            "speedup": round(ts / tp, 3)}


out_a = {
    "encode_full_a_run_4k_chunks": group_report(
        "enc_a", lambda g: sum(len(enc.encode(c)) for c in g)),
    "bpe_core_only_a_run_4k_chunks": group_report(
        "bpe_a", lambda g: sum(len(enc._core_bpe.encode_ordinary(c)) for c in g)),
}
if pat is not None:
    out_a["regex_pretokenizer_only_a_run_4k_chunks"] = group_report(
        "pat_a", lambda g: sum(len(pat_re.findall(c)) for c in g))
print(json.dumps(out_a, indent=2), flush=True)
