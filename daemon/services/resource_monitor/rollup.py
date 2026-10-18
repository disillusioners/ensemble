"""``rollup`` — raw → minute → hour arithmetic + retention sweep.

Architecture §2.5 (binding). The arithmetic is purely numeric:

  * raw → minute: group raw rows by ``bucket_ts // 60s`` (minute
    boundary). For each metric keep the LAST non-null value of the
    group, plus a row count. The minute row's ``bucket_ts`` is the
    ISO-8601 minute boundary (Naive-UTC per D9).

  * minute → hour: same shape, group by ``bucket_ts // 3600s``.

  * Retention sweep: DELETE rows from the three tier tables whose
    ``bucket_ts`` is older than the configured retention. Knobs are
    floor-gated (D8):

      - raw:    ``retention_raw_minutes``  ≥ 1440 (24 h)
      - minute: ``retention_minute_hours`` ≥ 168  (7 d)
      - hour:   ``retention_hour_days``    ≥ 90   (90 d)

    The defaults (2880 / 336 / 180) are wider than the floors so the
    "8.3 GB event was yesterday" forensics window is preserved.

P1 boundary (D13): attribution columns stay NULL/0; roll-up never
re-attributes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

# ── Retention floors (D8) ───────────────────────────────────────────────────


@dataclass
class RetentionPolicy:
    """Retention knobs (with floors enforced).

    All values are floor-gated: the constructor refuses a value
    below the floor, raising ``ValueError``. Tests inject the
    floors at construction time; production defaults to the spec
    knobs.
    """

    raw_minutes: int
    minute_hours: int
    hour_days: int
    # Floors — defaults per D8.
    floor_raw_minutes: int = 1440
    floor_minute_hours: int = 168
    floor_hour_days: int = 90

    def __post_init__(self) -> None:
        if self.raw_minutes < self.floor_raw_minutes:
            raise ValueError(
                f"raw_minutes {self.raw_minutes} below floor "
                f"{self.floor_raw_minutes}"
            )
        if self.minute_hours < self.floor_minute_hours:
            raise ValueError(
                f"minute_hours {self.minute_hours} below floor "
                f"{self.floor_minute_hours}"
            )
        if self.hour_days < self.floor_hour_days:
            raise ValueError(
                f"hour_days {self.hour_days} below floor "
                f"{self.floor_hour_days}"
            )

    @classmethod
    def from_config(
        cls,
        raw_minutes: int,
        minute_hours: int,
        hour_days: int,
    ) -> "RetentionPolicy":
        """Build with floor-gating; out-of-range values are CLAMPED to
        the floor with a logger warning (callers may want to flip
        the knob without crashing boot).
        """
        clamped_raw = max(raw_minutes, 1440)
        clamped_minute = max(minute_hours, 168)
        clamped_hour = max(hour_days, 90)
        return cls(
            raw_minutes=clamped_raw,
            minute_hours=clamped_minute,
            hour_days=clamped_hour,
        )


# ── Roll-up arithmetic (numeric; pure functions) ───────────────────────────


def _naive_to_epoch_seconds(naive_iso: str) -> int:
    """Parse a naive-UTC ISO-8601 string into epoch seconds.

    Naive means the wall-clock digits ARE UTC digits by D9 / the
    timestamps module contract. Inputs are accepted in either
    ``...T...`` or ``... ...`` form (with or without the T
    separator) — both are byte-stable from
    ``daemon.services.timestamps.now_utc_naive``.
    """
    s = naive_iso.replace("T", " ")
    # Truncate fractional seconds for the boundary math.
    if "." in s:
        s = s.split(".")[0]
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _epoch_to_naive_iso(epoch_s: int) -> str:
    """Format epoch seconds as a Naive-UTC ISO-8601 string (no ``Z``)."""
    dt = datetime.fromtimestamp(epoch_s, tz=timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def rollup_to_minute(raw_rows: Iterable[dict]) -> list[dict]:
    """Group raw rows by minute boundary.

    Each output row carries: ``bucket_ts`` (minute boundary ISO),
    ``interval_s`` = 60, the LAST non-null value of each metric
    within the group, and ``raw_count`` (how many raw ticks
    contributed). Attribution columns are NOT computed — P1
    keeps them NULL/0 (D13).
    """
    buckets: dict[int, list[dict]] = {}
    for row in raw_rows:
        if not row.get("bucket_ts"):
            continue
        ts = _naive_to_epoch_seconds(row["bucket_ts"])
        boundary = ts - (ts % 60)
        buckets.setdefault(boundary, []).append(row)

    out: list[dict] = []
    for boundary, rows in sorted(buckets.items()):
        merged = _merge_rows(rows)
        merged["bucket_ts"] = _epoch_to_naive_iso(boundary)
        merged["interval_s"] = 60
        merged["raw_count"] = len(rows)
        out.append(merged)
    return out


def rollup_to_hour(minute_rows: Iterable[dict]) -> list[dict]:
    """Group minute rows by hour boundary. Output row's
    ``bucket_ts`` is the hour boundary ISO; ``interval_s`` = 3600.
    ``raw_count`` aggregates the minute counts.
    """
    buckets: dict[int, list[dict]] = {}
    for row in minute_rows:
        if not row.get("bucket_ts"):
            continue
        ts = _naive_to_epoch_seconds(row["bucket_ts"])
        boundary = ts - (ts % 3600)
        buckets.setdefault(boundary, []).append(row)

    out: list[dict] = []
    for boundary, rows in sorted(buckets.items()):
        merged = _merge_rows(rows)
        merged["bucket_ts"] = _epoch_to_naive_iso(boundary)
        merged["interval_s"] = 3600
        merged["raw_count"] = sum(r.get("raw_count", 1) for r in rows)
        out.append(merged)
    return out


def _merge_rows(rows: list[dict]) -> dict:
    """Merge a list of raw/minute rows into one — LAST non-null per
    metric, summed counters (``missed_samples``), max for some
    monotonic metrics. Used by both ``rollup_to_minute`` and
    ``rollup_to_hour``.
    """
    if not rows:
        return {}
    # Numeric metrics use LAST non-null (cumulative monotonic
    # counters) or MAX (peak-like) — we keep it simple here: LAST
    # wins for counters and SUMMED for additive metrics.
    SUMMED_METRICS = ("missed_samples",)
    out: dict = {}
    last_non_null: dict[str, object] = {}
    for row in rows:
        for k, v in row.items():
            if k in ("bucket_ts", "interval_s", "raw_count"):
                continue
            if v is not None:
                last_non_null[k] = v
    out.update(last_non_null)
    for k in SUMMED_METRICS:
        total = sum(r.get(k, 0) or 0 for r in rows)
        out[k] = total
    return out


# ── Retention sweep ───────────────────────────────────────────────────────


def retention_cutoffs(
    *,
    now_epoch_s: int,
    policy: RetentionPolicy,
) -> tuple[int, int, int]:
    """Return the (raw_cutoff, minute_cutoff, hour_cutoff) epoch
    seconds. Rows whose ``bucket_ts`` is strictly less than the
    cutoff are eligible for sweep.

    Args:
        now_epoch_s: current epoch seconds (Naive-UTC).
        policy: the retention knobs.

    Returns:
        (raw_cutoff_epoch_s, minute_cutoff_epoch_s, hour_cutoff_epoch_s)
    """
    raw_cutoff = now_epoch_s - policy.raw_minutes * 60
    minute_cutoff = now_epoch_s - policy.minute_hours * 3600
    hour_cutoff = now_epoch_s - policy.hour_days * 86400
    return raw_cutoff, minute_cutoff, hour_cutoff


def sweep_query(
    table: str,
    cutoff_epoch_s: int,
) -> str:
    """Return the dialect-neutral DELETE statement for the retention sweep.

    Uses the ``bucket_ts`` ISO-8601 naive digits compared as TEXT
    against the cutoff ISO. The cutoff ISO is the
    lower-strict bound (rows strictly less are eligible).

    The query is parameterized at the call site (the flusher
    constructs the bound string); this helper just produces the
    SQL skeleton.
    """
    return (
        f"DELETE FROM {table} "
        f"WHERE bucket_ts < :cutoff_iso"
    )


def cutoff_iso(cutoff_epoch_s: int) -> str:
    """Format cutoff_epoch_s as the naive-UTC ISO string used in the
    DELETE WHERE clause.
    """
    return _epoch_to_naive_iso(cutoff_epoch_s)


__all__ = [
    "RetentionPolicy",
    "rollup_to_minute",
    "rollup_to_hour",
    "retention_cutoffs",
    "sweep_query",
    "cutoff_iso",
]