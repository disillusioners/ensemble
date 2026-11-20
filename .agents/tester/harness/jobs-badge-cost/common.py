"""Common helpers for the jobs-badge-cost harness."""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path


def p50(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    if n % 2:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def p95(xs):
    if not xs:
        return None
    s = sorted(xs)
    k = max(0, int(len(s) * 0.95) - 1)
    return s[k]


def agg(xs):
    if not xs:
        return {"p50": None, "max": None, "min": None, "mean": None, "n": 0}
    return {
        "p50": p50(xs),
        "max": max(xs),
        "min": min(xs),
        "mean": statistics.fmean(xs),
        "n": len(xs),
    }


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, default=str)
