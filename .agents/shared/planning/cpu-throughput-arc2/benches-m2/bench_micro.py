#!/usr/bin/env python
"""M2-C3: micro benches of the remaining hot CPU kernels (post-2A/2B/2C).

pydantic model_validate over a large list; hashlib sha256 throughput;
regex scan over large text. Numbers are single-process wall clock.
"""
import hashlib
import json
import re
import time

from pydantic import BaseModel, RootModel


class Item(BaseModel):
    model_config = {"extra": "forbid"}
    id: int
    role: str
    content: str
    tags: list[str]
    score: float
    ts: float


class ItemList(RootModel[list[Item]]):
    pass


def bench_pydantic(n=100_000):
    dicts = [{"id": i, "role": "assistant" if i % 2 else "human",
              "content": "checkpoint resumed bounded turn " * 4,
              "tags": ["t1", "t2"], "score": i * 0.5, "ts": 1763691532.5}
             for i in range(n)]
    t0 = time.perf_counter()
    out = ItemList.model_validate(dicts)
    dt = time.perf_counter() - t0
    n_models = len(out.root)
    return {"n": n, "validated_models": n_models, "secs": round(dt, 3),
            "models_per_s": int(n / dt)}


def bench_hashlib(mb=512, chunk_mb=1):
    chunk = bytes(chunk_mb * 1024 * 1024)
    h = hashlib.sha256()
    iters = mb // chunk_mb
    t0 = time.perf_counter()
    for _ in range(iters):
        h.update(chunk)
    dt = time.perf_counter() - t0
    return {"mb": mb, "secs": round(dt, 3), "mb_per_s": int(mb / dt),
            "digest_prefix": h.hexdigest()[:16]}


def bench_regex(mb=20):
    unit = ("instance b4e0220d-f9ff-4c08-8419-b707dd3ad773 resumed at "
            "2026-11-21T03:38:52Z; contact ops@example.com or https://x.invalid/p?q=1 ")
    text = (unit * (mb * 1024 * 1024 // len(unit) + 1))[:mb * 1024 * 1024]
    pat_uuid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    t0 = time.perf_counter()
    hits = pat_uuid.findall(text)
    dt_uuid = time.perf_counter() - t0
    pat_iso = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
    t0 = time.perf_counter()
    hits2 = pat_iso.findall(text)
    dt_iso = time.perf_counter() - t0
    return {"mb": mb, "uuid_hits": len(hits), "uuid_s": round(dt_uuid, 3),
            "uuid_mb_per_s": int(mb / dt_uuid),
            "iso_hits": len(hits2), "iso_s": round(dt_iso, 3),
            "iso_mb_per_s": int(mb / dt_iso)}


if __name__ == "__main__":
    print(json.dumps({
        "pydantic_list_validate": bench_pydantic(),
        "hashlib_sha256": bench_hashlib(),
        "regex_scan": bench_regex(),
    }, indent=2))
