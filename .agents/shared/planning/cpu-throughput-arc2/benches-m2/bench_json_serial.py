#!/usr/bin/env python
"""M2-C2: big-payload (de)serialization — stdlib json vs orjson.

orjson is NOT a direct project dep at this base but IS present transitively
(langgraph-checkpoint-postgres / langgraph-sdk / langsmith require it), so the
"delta the option would buy" is measured against the already-installed wheel.
Payload bounded well below 1 GB.
"""
import json
import sys
import time


def build_payload(n_items):
    base = {
        "instance_id": "b4e0220d-f9ff-4c08-8419-b707dd3ad773",
        "messages": [],
        "metadata": {"team": "worker", "nested": {"a": [1, 2, 3], "flag": True}},
    }
    items = []
    for i in range(n_items):
        items.append({
            "id": i,
            "role": "assistant" if i % 2 else "human",
            "content": ("The coordinator resumed the bounded turn and wrote the "
                        "checkpoint. " * 12) + str(i),
            "kwargs": {"idx": i, "tags": ["t1", "t2", "t3"], "score": i * 0.5},
            "ts": 1763691532.538228,
        })
    return {"instance_id": base["instance_id"], "metadata": base["metadata"], "items": items}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30000
    payload = build_payload(n)
    print(f"payload items={n}")

    t0 = time.perf_counter()
    s = json.dumps(payload)
    t_json_dumps = time.perf_counter() - t0
    t0 = time.perf_counter()
    back = json.loads(s)
    t_json_loads = time.perf_counter() - t0
    mb = len(s.encode()) / 1e6
    print(f"stdlib json : {mb:.1f} MB | dumps={t_json_dumps:.3f}s ({mb/t_json_dumps:.0f} MB/s) "
          f"loads={t_json_loads:.3f}s ({mb/t_json_loads:.0f} MB/s)")
    assert back["items"][n // 2]["id"] == n // 2

    try:
        import orjson
    except ImportError:
        print("orjson: NOT importable in this venv")
        return
    t0 = time.perf_counter()
    o = orjson.dumps(payload)
    t_orjson_dumps = time.perf_counter() - t0
    t0 = time.perf_counter()
    ob = orjson.loads(o)
    t_orjson_loads = time.perf_counter() - t0
    print(f"orjson      : {len(o)/1e6:.1f} MB | dumps={t_orjson_dumps:.3f}s "
          f"({mb/t_orjson_dumps:.0f} MB/s) loads={t_orjson_loads:.3f}s ({mb/t_orjson_loads:.0f} MB/s)")
    assert ob["items"][n // 2]["id"] == n // 2
    print(f"delta       : dumps x{t_json_dumps/t_orjson_dumps:.1f} faster, "
          f"loads x{t_json_loads/t_orjson_loads:.1f} faster")


if __name__ == "__main__":
    main()
