"""Capture-lane observability — the D signal surface (phase1-plan §8).

This module is the SINGLE home of the capture-lane observability signals
that workstream D defines, and the shared anomaly mechanism that the
re-fix burst guard (workstream E1) reuses — "one mechanism, two callers"
(decisions.md DS-17/DS-20).

WHAT IS OBSERVABLE TODAY vs WHAT PHASE 2 TURNS ON
----------------------------------------------------
The commission asks for this marking explicitly (phase1-plan §8 D3
ruling 3). It is reproduced here because this module is where an
operator looks first:

======================================  =====================  =====================
Signal                                  Observable today        Turns on in Phase 2
======================================  =====================  =====================
Capture ADMISSION counters              YES — the counter      the counter's *non-zero*
                                        increments even when   admission rate
                                        the switch is OFF (the
                                        refusal path is where
                                        the counter lives)
EXECUTION-refusal rows                  YES —                  the population size
                                        ``terminal_reason=
                                        'lane_disabled'`` is
                                        written regardless
Deviation detection + auto-anomaly      YES — the detector     the anomaly *rate*
                                        reads tool-call
                                        records, not the lane
                                        state
Re-fix rate                             YES — counts           the burst signature
                                        admissions (zero while
                                        the lane is OFF)
Keeper transcript retention             NO — the 168 h TTL     Phase 2 must extend or
                                        destroys it           harvest retention
                                                                BEFORE the first flip
======================================  =====================  =====================

D1 — THE REFUSAL SEMANTICS (do not conflate, decisions.md DS-19)
------------------------------------------------------------------
* A refused **ADMISSION** mints NO ROW AT ALL (the producer returns
  before any enqueue). A row-counting monitor therefore CANNOT see it —
  it needs a COUNTER.
* A refused **EXECUTION** of an already-queued row DOES write a
  terminal row (``terminal_reason='lane_disabled'``).

⇒ the two are counted under TWO DISTINCT names and MUST never be
merged into one "refusals" number: "3 refusals" could mean 3 attempts
that never produced a row, or 3 rows killed mid-flight — completely
different operational stories.

D4 — RETENTION: THE STRUCTURED-LOG SINK (decisions.md DS-17)
-------------------------------------------------------------
Phase 1 ships a SCHEMA-FREE structured-log sink: one JSON record per
capture run, appended to a daily file under the daemon's data dir.
Retention is bounded by :data:`SINK_RETENTION_DAYS` (90 days — the
plan asks for ">= 30 days, >= 90 preferred"), enforced by a cheap
age-prune of the sink files. No new table, therefore no ``.sql``
migration and no ``_ensure_postgres_columns`` statement (that ensure
list is ONE transaction — a failure in any entry aborts boot).

The durable-TABLE variant is a LATER release (it needs a retention
sweep + a query surface) and is explicitly OUT of Phase 1 scope. The
read side needs NO restart: the observer reads the sink file directly.

CREDENTIAL HYGIENE (decisions.md DS-18 / the F7 lesson)
--------------------------------------------------------
The new observability fields carry a **SHAPE**, never the raw prompt
text: a length bucket + presence flags + a sha256 prefix. The capture
path already carries ``task_message`` (the P0 gate's subject) — D adds
a shape, not a second copy of sensitive content.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────

#: The agent whose lane is being observed (the skill-keeper).
SKILL_KEEPER_AGENT_ID = "skill-keeper"

#: Synthetic instance id for DAEMON-AUTHORED anomaly rows. The anomaly
#: service requires a non-empty instance id; a daemon-side report has no
#: instance of its own, so it names its own surface.
DAEMON_REPORTER_INSTANCE_ID = "daemon:skill-capture-observability"

#: Anomaly categories (closed vocabulary — ``ANOMALY_CATEGORIES`` at
#: ``daemon/services/metrics_query/wiring/vocab.py``). A keeper that
#: attempts an action outside its sanctioned surface is a TOOL failure;
#: an admission refused by a rate/cap control is a QUEUE-scheduling
#: event. ``other`` is the reserved fold target.
CATEGORY_LANE_DEVIATION = "tool-failure"
CATEGORY_BURST_CAP = "queue-scheduling"
CATEGORY_FALLBACK = "other"

#: Directory (under the daemon data dir) holding the sink files.
SINK_DIR_NAME = "skill-capture-observability"

#: Retention window for the sink files. The plan asks for ">= 30 days,
#: >= 90 days preferred" — 90 is the default here.
SINK_RETENTION_DAYS = 90

#: Prompt-length buckets (the "shape" of a prompt — never its content).
PROMPT_BUCKET_SHORT = 200
PROMPT_BUCKET_MEDIUM = 2000


# ── Prompt shape (never the prompt) ───────────────────────────────────────


def prompt_length_bucket(length: int) -> str:
    """Bucket a prompt length: empty / short / medium / long."""
    if length <= 0:
        return "empty"
    if length < PROMPT_BUCKET_SHORT:
        return "short"
    if length < PROMPT_BUCKET_MEDIUM:
        return "medium"
    return "long"


def prompt_shape(task_message: Any, agent_id: Any = None) -> dict[str, Any]:
    """Return the SHAPE of a prompt — NEVER the prompt itself (DS-18).

    Args:
        task_message: The raw prompt text (used ONLY to derive the
            shape — it is never copied into the result).
        agent_id: The originating agent id, when known.

    Returns:
        A dict with the length bucket, the length, whether an agent id
        was present, and a sha256 PREFIX of the prompt (the F7
        precedent: "name + value sha256 prefix, never the value").
    """
    text = task_message if isinstance(task_message, str) else ""
    encoded = text.encode("utf-8", errors="replace")
    return {
        "length": len(text),
        "length_bucket": prompt_length_bucket(len(text)),
        "has_agent_id": bool(isinstance(agent_id, str) and agent_id.strip()),
        "sha256_16": hashlib.sha256(encoded).hexdigest()[:16] if text else None,
    }


# ── Counters (two DISTINCT refusal signals — DS-19) ───────────────────────


@dataclass
class CaptureLaneCounters:
    """In-process counters for the capture/evolution lane.

    DS-19: the ADMISSION and EXECUTION refusal counters are separate
    fields with separate names — they MUST never be merged.
    """

    #: Admission refusals, keyed by the refusal reason
    #: (``capture_lane_off``, ``evolution_lane_off``, ``fix_cooldown``,
    #: ``fix_burst_cap``).
    admission_refusals: dict[str, int] = field(default_factory=dict)
    #: Execution refusals, keyed by the terminal reason
    #: (``lane_disabled``).
    execution_refusals: dict[str, int] = field(default_factory=dict)
    #: Admissions that produced a job row.
    admissions: int = 0
    #: Keeper deviations detected (daemon-side).
    deviations: int = 0
    #: Re-fix (FIX) admissions observed — the E1 re-fix-rate gauge.
    fix_admissions: int = 0

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot of every counter."""
        return {
            "admission_refusals": dict(self.admission_refusals),
            "execution_refusals": dict(self.execution_refusals),
            "admissions": self.admissions,
            "deviations": self.deviations,
            "fix_admissions": self.fix_admissions,
        }


_COUNTERS = CaptureLaneCounters()


def counters() -> CaptureLaneCounters:
    """Return the process-wide capture-lane counters."""
    return _COUNTERS


def reset_counters() -> None:
    """Reset every counter (test helper — never called in production)."""
    _COUNTERS.admission_refusals.clear()
    _COUNTERS.execution_refusals.clear()
    _COUNTERS.admissions = 0
    _COUNTERS.deviations = 0
    _COUNTERS.fix_admissions = 0


def record_admission_refusal(reason: str, *, count: int = 1) -> None:
    """Count ONE refused ADMISSION (no row was minted — DS-19/D1)."""
    key = str(reason or "unknown")
    _COUNTERS.admission_refusals[key] = (
        _COUNTERS.admission_refusals.get(key, 0) + count
    )


def record_execution_refusal(reason: str, *, count: int = 1) -> None:
    """Count ONE refused EXECUTION (a terminal row WAS written)."""
    key = str(reason or "unknown")
    _COUNTERS.execution_refusals[key] = (
        _COUNTERS.execution_refusals.get(key, 0) + count
    )


def record_admission(*, is_fix: bool = False) -> None:
    """Count ONE admitted capture/evolution admission (a job row exists)."""
    _COUNTERS.admissions += 1
    if is_fix:
        _COUNTERS.fix_admissions += 1


def record_deviation() -> None:
    """Count ONE detected keeper deviation."""
    _COUNTERS.deviations += 1


# ── The sink (D4 — structured log, schema-free, restart-free read side) ──


def resolve_data_dir(data_dir: str | Path | None = None) -> Path:
    """Resolve the daemon data dir (``ENSEMBLE_DATA_DIR`` > ``DATA_DIR`` > ``./data``).

    Mirrors the project-wide precedence chain used by
    ``daemon/api.py`` and ``daemon/services/install_audit.py``.
    """
    if data_dir is not None:
        return Path(data_dir)
    return Path(
        os.environ.get("ENSEMBLE_DATA_DIR")
        or os.environ.get("DATA_DIR")
        or "./data"
    )


def resolve_sink_dir(data_dir: str | Path | None = None) -> Path:
    """Resolve the sink directory (under the daemon data dir)."""
    return resolve_data_dir(data_dir) / SINK_DIR_NAME


def _prune_expired_sink_files(sink_dir: Path, *, retention_days: int) -> None:
    """Delete sink files older than the retention window (best effort)."""
    if retention_days <= 0:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    for path in sink_dir.glob("skill-capture-*.jsonl"):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:  # pragma: no cover — stat race
            continue
        if mtime < cutoff:
            try:
                path.unlink()
            except OSError as err:  # pragma: no cover — best effort
                logger.debug(
                    "skill-capture-observability: could not prune %s (%s)",
                    path, err,
                )


def append_run_record(
    record: dict[str, Any],
    *,
    data_dir: str | Path | None = None,
    retention_days: int = SINK_RETENTION_DAYS,
) -> Path | None:
    """Append ONE structured record to the sink. NEVER raises.

    Args:
        record: The record to append (JSON-serializable).
        data_dir: Override for the daemon data dir (tests).
        retention_days: Sink retention window.

    Returns:
        The path of the file the record was appended to, or ``None`` when
        the write failed (the caller MUST NOT fail the lane because of
        an observability write).
    """
    try:
        sink_dir = resolve_sink_dir(data_dir)
        sink_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = sink_dir / f"skill-capture-{day}.jsonl"
        payload = dict(record)
        payload.setdefault("ts", datetime.now(timezone.utc).isoformat())
        line = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        _prune_expired_sink_files(sink_dir, retention_days=retention_days)
        return path
    except Exception as err:  # noqa: BLE001 — observability NEVER breaks the lane
        logger.warning(
            "skill-capture-observability: sink append failed (%s: %s) — "
            "the capture lane is unaffected",
            type(err).__name__, err,
        )
        return None


# ── The anomaly hook (D3 ruling 1 — the DAEMON announces) ───────────────

#: A reporter is any callable with the ``AnomalyService.accept_report``
#: shape (keyword-only: agent_id, instance_id, category, description,
#: severity, suspected_cause) returning an awaitable.
AnomalyReporter = Callable[..., Awaitable[Any]]

_ANOMALY_REPORTER: AnomalyReporter | None = None


def set_anomaly_reporter(reporter: AnomalyReporter | None) -> None:
    """Register the daemon-side anomaly reporter (process-wide).

    Wiring is OPTIONAL: when no reporter is registered, the module
    falls back to a late-bound resolution through the job service (see
    :func:`resolve_anomaly_service`). Tests register a double.
    """
    global _ANOMALY_REPORTER
    _ANOMALY_REPORTER = reporter


def get_anomaly_reporter() -> AnomalyReporter | None:
    """Return the registered anomaly reporter (or ``None``)."""
    return _ANOMALY_REPORTER


def resolve_anomaly_service(job_service: Any = None) -> Any | None:
    """Late-bound resolution of the daemon's ``AnomalyService``.

    The anomaly service is mounted on the instance manager
    (``manager._anomaly_service`` with the public alias
    ``manager.anomaly_service``), and the job queue service holds the
    instance manager. Resolving it through the job service keeps the
    wiring inside this module (no manager-side change is required for
    the daemon to announce a deviation).

    Args:
        job_service: The ``JobQueueService`` (or anything exposing
            ``_instance_manager``).

    Returns:
        The anomaly service (anything with an ``accept_report``
        coroutine), or ``None`` when it cannot be resolved.
    """
    candidates: list[Any] = []
    if job_service is not None:
        manager = getattr(job_service, "_instance_manager", None)
        if manager is not None:
            candidates.append(manager)
    for holder in candidates:
        for attr in ("anomaly_service", "_anomaly_service"):
            service = getattr(holder, attr, None)
            if service is not None and callable(
                getattr(service, "accept_report", None)
            ):
                return service
    return None


try:  # the closed vocabulary (imported once, module level)
    from daemon.services.metrics_query.wiring.vocab import ANOMALY_CATEGORIES
except Exception:  # pragma: no cover — import guard
    ANOMALY_CATEGORIES: tuple[str, ...] = (  # type: ignore[no-redef]
        "tool-failure",
        "performance",
        "resource-exhaustion",
        "loop-stall",
        "wedge-suspected",
        "data-corruption",
        "auth-permission",
        "external-dependency",
        "mcp-failure",
        "llm-provider",
        "queue-scheduling",
        "environment-config",
        "other",
    )


def _canonical_category(category: str) -> str:
    """Fold a category into the closed anomaly vocabulary.

    The vocabulary is closed (``ANOMALY_CATEGORIES`` at
    ``daemon/services/metrics_query/wiring/vocab.py``); ``other`` is the
    reserved fold target. An unknown category is folded — never
    invented.
    """
    if category in ANOMALY_CATEGORIES:
        return category
    return CATEGORY_FALLBACK


async def emit_lane_anomaly(
    *,
    category: str,
    description: str,
    severity: str = "major",
    suspected_cause: str | None = None,
    agent_id: str = SKILL_KEEPER_AGENT_ID,
    instance_id: str = DAEMON_REPORTER_INSTANCE_ID,
    job_service: Any = None,
    reporter: AnomalyReporter | None = None,
) -> bool:
    """Write ONE anomaly row through the daemon-side accept path.

    The Oct-22 keeper did NOT announce itself — that is the whole
    reason the deviation needed a forensic reconstruction. This is the
    DAEMON-SIDE trigger: the detector calls it automatically, so a
    misbehaving keeper is announced even when the agent stays silent.

    Args:
        category: Raw category (folded into the closed vocabulary).
        description: What happened (never the raw prompt).
        severity: The tier (``minor`` / ``major`` / ``critical``).
        suspected_cause: The detector's hypothesis.
        agent_id: The reporting agent TYPE id.
        instance_id: The reporting instance id (synthetic for daemon rows).
        job_service: Used for the late-bound anomaly-service resolution.
        reporter: Explicit reporter (overrides the registered one).

    Returns:
        ``True`` when an anomaly row was accepted, ``False`` otherwise.
        NEVER raises — a failed announcement must not break the lane.
    """
    canonical = _canonical_category(category)
    target = reporter or get_anomaly_reporter()
    if target is None:
        service = resolve_anomaly_service(job_service)
        if service is not None:
            target = getattr(service, "accept_report", None)
    if target is None:
        logger.warning(
            "skill-capture-observability: no anomaly reporter available — "
            "deviation NOT announced (category=%s): %s",
            canonical, description,
        )
        return False
    try:
        await target(
            agent_id=agent_id,
            instance_id=instance_id,
            category=canonical,
            description=description,
            severity=severity,
            suspected_cause=suspected_cause,
        )
        return True
    except Exception as err:  # noqa: BLE001 — the lane must not break
        logger.warning(
            "skill-capture-observability: anomaly write failed (%s: %s) — "
            "category=%s",
            type(err).__name__, err, canonical,
        )
        return False


# ── Run records (one structured record per capture run) ───────────────────


def build_run_record(
    *,
    stage: str,
    run_id: str | None = None,
    agent_id: str | None = None,
    prompt_source: str | None = None,
    task_message: Any = None,
    outcome: str = "",
    refusal_reason: str | None = None,
    deviation: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the structured record for ONE capture run.

    The record carries the run id → agent → prompt source → prompt
    SHAPE → outcome → refusal → deviation flag chain. The raw prompt
    text is NEVER included (DS-18).
    """
    record: dict[str, Any] = {
        "stage": stage,
        "run_id": run_id,
        "agent_id": agent_id,
        "prompt_source": prompt_source,
        "prompt_shape": prompt_shape(task_message, agent_id),
        "outcome": outcome,
        "refusal_reason": refusal_reason,
        "deviation": bool(deviation),
    }
    if extra:
        record.update(extra)
    return record
