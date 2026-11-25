"""In-process engine projection (Engine Arc P2 — T2.6, T2.9).

``snapshot()`` returns per-job the closed key set from D2.14. This is NOT a
metrics family — no ``ensemble_engine_*`` family is registered here (F-1).
P6 owns the family ceremony.

``project_merged`` is the pure convergence mapping (T2.9): it unions
``job_metrics()``'s two fields with the engine snapshot WITHOUT editing
``job_metrics()`` (F-2).
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Optional

from daemon.services.engine.state import effective_interval_seconds, parse_iso


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


class EngineMetrics:
    def __init__(self, engine: Any) -> None:
        self._engine = engine

    @property
    def _cache(self) -> dict:
        return getattr(self._engine, "_cache", {}) or {}

    @property
    def _executors(self) -> dict:
        return getattr(self._engine, "_executors", {}) or {}

    def snapshot(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        now = _now()
        for job_id, cache in self._cache.items():
            spec = cache.get("spec")
            last_run_at = cache.get("last_run_at")
            interval = effective_interval_seconds(cache, spec) if spec is not None else 1.0
            # next_due
            last_dt = parse_iso(last_run_at) if last_run_at else None
            if last_dt is None:
                next_due = now
            else:
                next_due = last_dt + _dt.timedelta(seconds=interval)
            # would_fire: last computed due-ness (default True if never computed)
            would_fire = bool(cache.get("would_fire", False))
            # dead (D2.15 + CS-9 carve-out)
            dead = self._compute_dead(job_id, cache, spec, now, interval)
            out[job_id] = {
                "next_due": next_due.isoformat(),
                "last_duration_seconds": cache.get("last_duration_seconds"),
                "last_outcome": cache.get("last_outcome"),
                "skips_by_reason": dict(cache.get("skips_by_reason", {}) or {}),
                "consecutive_skips": int(cache.get("consecutive_skips", 0) or 0),
                "dead": bool(dead),
                "running": bool(cache.get("running", False)),
                "would_fire": would_fire,
            }
        return out

    def _compute_dead(self, job_id, cache, spec, now, interval):
        # Persisted dead (D3.4) takes precedence: if the row says dead, it's dead.
        if bool((cache or {}).get("persisted_dead", False)):
            return True
        is_observer = job_id not in self._executors
        if is_observer:
            # CS-9/L-8 carve-out: dead <=> ZERO SKIPPED_SHADOW rows within
            # 2x effective_interval. Count only timestamped shadow evidence
            # inside the window — a single stale row must not keep an
            # observer alive forever. RAM-only (no per-tick DB reads).
            window = 2.0 * interval
            evidence = cache.get("shadow_evidence_ts") or []
            fresh = 0
            for ts in evidence:
                stamped = parse_iso(ts) if ts else None
                if stamped is None:
                    continue
                if (now - stamped).total_seconds() <= window:
                    fresh += 1
            return fresh == 0
        last_run_at = cache.get("last_run_at")
        if not last_run_at:
            return True
        last_dt = parse_iso(last_run_at)
        if last_dt is None:
            return True
        elapsed = (now - last_dt).total_seconds()
        return elapsed >= (2.0 * interval)


def project_merged(maintenance_service: Any, engine_service: Any) -> dict[str, Any]:
    """Pure convergence mapping (T2.9).

    Per job id, union of ``job_metrics()``'s ``{last_success_ts,
    consecutive_skips}`` and the engine snapshot. No writes, no state.
    ``job_metrics()`` is never modified (F-2).
    """
    maint: dict[str, Any] = {}
    if maintenance_service is not None:
        try:
            maint = maintenance_service.job_metrics() or {}
        except Exception:  # noqa: BLE001
            maint = {}
    eng_snap: dict[str, Any] = {}
    if engine_service is not None:
        try:
            metrics = getattr(engine_service, "metrics", None)
            if metrics is not None and hasattr(metrics, "snapshot"):
                eng_snap = metrics.snapshot() or {}
        except Exception:  # noqa: BLE001
            eng_snap = {}
    result: dict[str, Any] = {}
    for job_id in set(maint) | set(eng_snap):
        m_block = maint.get(job_id, {}) or {}
        e_block = eng_snap.get(job_id, {}) or {}
        merged = dict(e_block)
        if "last_success_ts" in m_block:
            merged["last_success_ts"] = m_block.get("last_success_ts")
        else:
            merged.setdefault("last_success_ts", None)
        if "consecutive_skips" in m_block:
            merged["consecutive_skips"] = m_block.get("consecutive_skips")
        else:
            merged.setdefault("consecutive_skips", e_block.get("consecutive_skips", 0))
        result[job_id] = merged
    return result


__all__ = ["EngineMetrics", "project_merged"]
