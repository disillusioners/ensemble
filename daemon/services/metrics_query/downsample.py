"""Window slicing and the bucket-max downsample (AM-5).

The downsample is **bucket-max**: the window is split into at most
``max_points`` buckets and each bucket contributes its MAXIMUM sample.
It is NAMED (never every-kth) because every-kth sampling hides a
spike that a maximum would show — a trend line that quietly drops the
peak is a wrong trend.

The window is anchored at the server's ``generated_at_utc`` (the
caller may pin a different instant with a cursor).
"""

from __future__ import annotations

import math
from typing import Sequence

Points = Sequence[tuple[int, float]]


def slice_window(
    points: Points, *, now: int, window_s: int
) -> list[tuple[int, float]]:
    """The samples inside ``[now - window_s, now]``, oldest first."""
    if window_s <= 0:
        return []
    cutoff = now - int(window_s)
    return [(ts, value) for ts, value in points if cutoff <= ts <= now]


def bucket_max(
    points: Points, max_points: int | None
) -> list[tuple[int, float]]:
    """Downsample to at most ``max_points`` samples (bucket-max).

    A window that already fits is returned unchanged.  Otherwise the
    window is split into ``max_points`` buckets and each bucket
    contributes its maximum sample (the observed maximum, with the
    timestamp of THAT sample).
    """
    if max_points is None or max_points <= 0 or len(points) <= max_points:
        return list(points)
    bucket_size = max(1, math.ceil(len(points) / max_points))
    out: list[tuple[int, float]] = []
    for start in range(0, len(points), bucket_size):
        chunk = points[start : start + bucket_size]
        if not chunk:
            continue
        out.append(max(chunk, key=lambda sample: sample[1]))
    return out


__all__ = ["bucket_max", "slice_window"]
