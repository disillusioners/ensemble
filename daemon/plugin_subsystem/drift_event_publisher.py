"""Drift-event publisher (REC §1.2 component 7 — slice ⑥).

Emits drift facts from the sync-runner into the tier-1 trigger
engine.  **No new bus** (CON §5: "single subject; no new bus") —
the payload is persisted to the ``drift_events`` table (the probe's
option (a): DB-backed, durable, replay-capable — one row per
emitted event; resolution deletes the row) and the engine's
``drift_event_observed`` condition reads the same table through
:class:`DriftEventRepository`.

**Payload shape (FROZEN — CON §5 verbatim, no envelope):**

``{plugin, class, divergence_id, files, delta, rationale,
pinning_test, observed_at, observed_tag}`` — built by
:func:`daemon.plugin_subsystem.sync_runner.build_drift_event_payload`
(the single source of truth for the shape; this module never
re-encodes it).

**Wiring model (library-safe):**

``sync_runner.emit_drift_event`` is called from library code that
has NO daemon runtime (operator CLI runs, tests).  The publisher is
therefore a module-level configurable sink:

- DEFAULT: :class:`LogOnlyDriftEventSink` — the slice-③ stub
  behavior preserved verbatim (structured log line, no DB).
- Daemon runtime: ``manager`` calls
  :func:`configure_drift_event_publisher` at boot with a
  :class:`DriftEventPublisher` bound to the real repository; from
  then on every emit persists (the daemon lane).

**Never-raises contract:** an emission failure must never turn a
completed sync into a crash — :func:`emit_drift_event_safe` swallows
+ logs every sink error (observer discipline, mirroring the Plane
sync service's never-raises contract).  The sync result the operator
already holds is unaffected by downstream emission problems.

**Resolution semantics (probe doc, option (a)):** the drift-events
table is the SYNCHRONIZED trigger-side view; the manifest's
divergence register stays the canonical representation.  Resolving a
divergence (re-apply or drop disposition landed) DELETES the row —
"unresolved" is simply "row exists" (probe doc: "resolution deletes
the row").
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Protocol

from sqlmodel import Field, SQLModel, Session, select  # SQLModel Session: .exec() select helper
from sqlalchemy import Column, String, Text, delete as sa_delete
from sqlalchemy.engine import Engine

from daemon.repositories.infra.types import JSONBType

logger = logging.getLogger(__name__)

__all__ = [
    "DriftEvent",
    "DriftEventRepository",
    "DriftEventPublisher",
    "LogOnlyDriftEventSink",
    "DriftEventSink",
    "configure_drift_event_publisher",
    "get_drift_event_publisher",
    "reset_drift_event_publisher",
    "emit_drift_event_safe",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ══════════════════════════════════════════════════════════════════════════════
# Model — the drift_events table
# ══════════════════════════════════════════════════════════════════════════════


class DriftEvent(SQLModel, table=True):
    """One emitted drift event (CON §5 verbatim payload, persisted).

    Row lifetime: created on emission; DELETED on resolution (the
    probe's option-(a) semantics — "unresolved" == "row exists").
    The canonical divergence representation remains the manifest's
    divergence register; this table is the trigger-side view.

    Column naming: the payload's ``class`` key maps to the
    ``target_class`` column (``class`` is an awkward SQL identifier);
    every other column carries the payload field name verbatim.
    """

    __tablename__ = "drift_events"

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        max_length=64,
    )
    plugin: str = Field(sa_column=Column(String, nullable=False), max_length=128)
    target_class: str = Field(
        sa_column=Column("target_class", String, nullable=False), max_length=64
    )
    divergence_id: int = Field(default=0, nullable=False)
    files: List[str] = Field(
        default_factory=list,
        sa_column=Column("files", JSONBType, nullable=False),
    )
    delta: str = Field(default="", sa_column=Column(String, nullable=False))
    rationale: str = Field(default="", sa_column=Column(Text, nullable=False))
    pinning_test: str = Field(default="", sa_column=Column(Text, nullable=False))
    observed_at: str = Field(sa_column=Column(String, nullable=False), max_length=64)
    observed_tag: str = Field(
        sa_column=Column(String, nullable=False), max_length=128
    )
    created_at: str = Field(default_factory=_now_iso)

    def to_payload(self) -> Dict[str, Any]:
        """Return the CON §5 verbatim payload view of this row."""
        return {
            "plugin": self.plugin,
            "class": self.target_class,
            "divergence_id": self.divergence_id,
            "files": list(self.files or []),
            "delta": self.delta,
            "rationale": self.rationale,
            "pinning_test": self.pinning_test,
            "observed_at": self.observed_at,
            "observed_tag": self.observed_tag,
        }


# ══════════════════════════════════════════════════════════════════════════════
# Repository — sync methods (callers bridge via asyncio.to_thread, the
# SkillTriggerRepository pattern)
# ══════════════════════════════════════════════════════════════════════════════


class DriftEventRepository:
    """Sync repository for the ``drift_events`` table.

    All methods are synchronous; async callers hop threads via
    ``asyncio.to_thread`` (the established repository pattern in
    this codebase).
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_from_payload(self, payload: Mapping[str, Any]) -> DriftEvent:
        """Persist one CON §5 verbatim payload as a new row.

        The payload keys are the frozen CON §5 set; ``class`` maps
        to the ``target_class`` column.  Unknown extra keys are
        ignored (additive payload evolution per CON §8 must not
        break the v1 writer).
        """
        row = DriftEvent(
            plugin=str(payload.get("plugin", "")),
            target_class=str(payload.get("class", "")),
            divergence_id=int(payload.get("divergence_id", 0) or 0),
            files=[str(f) for f in (payload.get("files") or [])],
            delta=str(payload.get("delta", "")),
            rationale=str(payload.get("rationale", "")),
            pinning_test=str(payload.get("pinning_test", "")),
            observed_at=str(payload.get("observed_at", "")),
            observed_tag=str(payload.get("observed_tag", "")),
        )
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
        return row

    def list_unresolved(self, plugin: Optional[str] = None) -> List[DriftEvent]:
        """List unresolved events (every row; resolution deletes).

        ``plugin`` filters to one plugin's events; ``None`` walks
        all plugins (the future cross-plugin sweep path, probe doc
        open question — the schema already accommodates it).
        Ordered by ``observed_at`` ascending so the oldest alarm is
        evaluated first (stable verdicts).
        """
        with Session(self._engine) as session:
            stmt = select(DriftEvent)
            if plugin is not None:
                stmt = stmt.where(DriftEvent.plugin == plugin)
            stmt = stmt.order_by(DriftEvent.observed_at.asc())
            return list(session.exec(stmt))

    def resolve(self, plugin: str, divergence_id: int) -> bool:
        """Resolve (DELETE) one event.  Returns True iff a row existed.

        Resolution is the re-apply-or-drop disposition landing
        (CON §2); the canonical register keeps the audit trail —
        the trigger-side row is deliberately deleted so "unresolved"
        stays a cheap existence check (probe doc option (a)).
        """
        with Session(self._engine) as session:
            stmt = sa_delete(DriftEvent).where(
                DriftEvent.plugin == plugin,
                DriftEvent.divergence_id == divergence_id,
            )
            result = session.exec(stmt)
            session.commit()
        return bool(result.rowcount)

    def latest_for_plugin(self, plugin: str) -> Optional[DriftEvent]:
        """Newest unresolved event for ``plugin`` (by observed_at)."""
        with Session(self._engine) as session:
            stmt = (
                select(DriftEvent)
                .where(DriftEvent.plugin == plugin)
                .order_by(DriftEvent.observed_at.desc())
            )
            return session.exec(stmt).first()


# ══════════════════════════════════════════════════════════════════════════════
# Sinks — the module-level publisher seam
# ══════════════════════════════════════════════════════════════════════════════


class DriftEventSink(Protocol):
    """Structural type for a drift-event sink (publisher seam)."""

    def publish(self, payload: Mapping[str, Any]) -> None:  # pragma: no cover
        ...


class LogOnlyDriftEventSink:
    """The default sink — the slice-③ stub behavior, preserved.

    Logs the payload shape at INFO (structured, greppable:
    ``drift_event_emitted``) and does nothing else.  Library callers
    (operator CLI, tests) get this unless a runtime explicitly
    configures the DB-backed publisher.
    """

    def publish(self, payload: Mapping[str, Any]) -> None:
        logger.info("drift_event_emitted (log-only sink): %s", dict(payload))


class DriftEventPublisher:
    """DB-backed sink: persist the payload, then log the emission."""

    def __init__(self, store: DriftEventRepository) -> None:
        self._store = store

    def publish(self, payload: Mapping[str, Any]) -> None:
        row = self._store.create_from_payload(payload)
        logger.info(
            "drift_event_persisted: plugin=%s class=%s divergence_id=%s "
            "observed_tag=%s row=%s",
            row.plugin,
            row.target_class,
            row.divergence_id,
            row.observed_tag,
            row.id,
        )


# Module-level sink (the wiring seam; daemon boot swaps it).
_sink: DriftEventSink = LogOnlyDriftEventSink()


def configure_drift_event_publisher(sink: DriftEventSink) -> None:
    """Install ``sink`` as the process-wide drift-event sink.

    Called ONCE by the daemon runtime at boot (manager wiring);
    test suites call :func:`reset_drift_event_publisher` in their
    teardown to avoid cross-test leakage.
    """
    global _sink
    _sink = sink


def get_drift_event_publisher() -> DriftEventSink:
    """Return the currently configured sink (never None)."""
    return _sink


def reset_drift_event_publisher() -> None:
    """Restore the log-only default (test isolation helper)."""
    global _sink
    _sink = LogOnlyDriftEventSink()


def emit_drift_event_safe(payload: Mapping[str, Any]) -> None:
    """Publish through the configured sink; NEVER raises.

    Observer discipline: a sink failure (DB down, schema drift) is
    logged and swallowed — the sync that produced the payload has
    already completed and its result is unaffected.
    """
    try:
        _sink.publish(payload)
    except Exception as exc:  # noqa: BLE001 - never-raises contract
        logger.warning(
            "drift_event_emission_failed (never-raises; sync result "
            "unaffected): %s — payload=%s",
            exc,
            dict(payload),
        )
