"""Boot-time seeder + chain-break guard for the Central Maintenance
System's weekly trigger chain (Phase 1).

The chain is ONE ``source_configs`` row (``recurrence="once"``,
``agent="maintenancer"``) whose ``run_at`` is rewritten at each fire by
the adapter-side re-arm seam (``daemon/sources/adapters/scheduler.py``).
This module owns the OTHER half: the row's creation and its survival
across reboots / source-GC.

Modeled on the tool-usage digest seeder
(``daemon/repositories/source/seed.py:135-198``) — but this seeder is
NOT a plain get-or-create.  It is an **archive-aware** guard:

* the row is looked up by id and then by name;
* an existing row is a NO-OP in ANY state (operator intent is preserved)
  with three deliberate exceptions (the plan's Task 1.4 clauses):
  - a **cancelled** row is NEVER touched (BR-3);
  - a **paused** row (the operator's pause flag — ``enabled=False``)
    is NEVER touched (R-P1-5);
  - a **stopped** row that still carries a ``chain`` block is
    **RE-ENABLED** at boot (plan Task 1.4 iii-b) — it is the chain's
    self-heal after a crash between the fire and the re-arm;
* a **missing** row consults ``source_configs_archive`` FIRST (BR-3):
  - archived ``cancelled`` ⇒ NEVER revived;
  - archived ``stopped`` ⇒ revived IFF the archived config snapshot
    carries a ``chain`` block (one concept, one marker);
  - no archive row ⇒ created;
* a **past** ``run_at`` is re-minted ONLY under the BR-1 predicate — a
  lateness cap must be configured AND the lateness must exceed it.
  Otherwise the past ``run_at`` is LEFT ALONE so the adapter's
  past-due auto-fire delivers the late run (R-P1-8: this guard runs
  BEFORE any adapter exists, so an unconditional re-mint would silently
  skip the missed week).

The archived-config snapshot is the SOLE reconstruction input for a
revived row (the archive carries ``config`` as a JSON string).

BR-15 — reportless-week detector (surface-ONLY)
------------------------------------------------
After the guard pass the boot hook asks: did the most recent chain fire
produce a ``run-*.md`` under the artifact root?  A miss is surfaced
LOUDLY (a daemon-side ERROR log line plus a board item) — the detector
NEVER authors a stub, NEVER self-heals and NEVER re-fires.  The
"never fired yet" case is not a miss: with no execution record there is
nothing to detect.

Artifact root
-------------
``.agents/maintenancer/RESULTS/`` under the LIVE-INSTALL daemon project
root — resolved from the daemon package's own location (the house
convention: ``Path(sys.executable).parent`` when frozen, else the
directory that holds ``daemon/``), NEVER from ``Path.cwd()`` and NEVER
from a worktree path.  The seeder creates that directory idempotently
at first boot.

No-idle attestation
-------------------
This module carries NO idle/busyness precondition of any kind: the
weekly trigger fires regardless of system busyness (R2 / E-2).
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from daemon.repositories.factory import (
    create_project_repository,
    create_source_repository,
)
from daemon.repositories.source.models import (
    SourceConfig,
    SourceConfigArchive,
    SourceStatus,
)

logger = logging.getLogger(__name__)

SEED_NAME = "central-maintenance-weekly"
SEED_SOURCE_TYPE = "scheduler"
SEED_SOURCE_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, SEED_NAME))

#: The chain marker written into the row config.  Its presence is the
#: ONE marker shared by the re-arm seam (adapter side), the boot guard
#: and the archive-aware revive — "one concept, one marker" (BR-3).
CHAIN_MODE = "weekly-random"

#: The artifact root RELATIVE to the live-install project root.
RESULTS_RELATIVE_DIR = Path(".agents") / "maintenancer" / "RESULTS"

#: The board item written when a reportless week is detected.  The
#: board is the daemon-side operator surface (the "board = operational
#: surface" convention); the synthetic author id marks the write as
#: daemon-originated (no agent instance is behind it).
BOARD_TITLE = "maintenance: reportless week detected"
BOARD_TAGS = ("maintenance", "central-maintenance", "reportless-week")
BOARD_AUTHOR = "daemon:central-maintenance"

#: The FUNCTIONAL weekly dispatch message (plan Task 1.1 — NOT a
#: placeholder).  The trigger token travels in the message BODY: the
#: orchestrator parses ``trigger:`` and passes ``trigger_type`` to the
#: writer child (the transport spec is owned by Phase 4).
WEEKLY_RUN_MESSAGE = """Weekly central maintenance run.

trigger: scheduled

Execute the weekly maintenance run:
1. Dispatch the five maintenance lane children (one per lane, per the
   maintenancer orchestrator workflow).
2. Collect the lane results and dispatch the report writer child with
   them.
3. Parse the `trigger:` token from this message body and pass the parsed
   value as `trigger_type` in the writer child's dispatch envelope.
"""


class MaintenanceSeedError(RuntimeError):
    """The chain could not be armed (fail-closed, never silent)."""


@dataclass
class GuardOutcome:
    """The result of one boot-time guard pass.

    Attributes:
        action: What the guard did — one of ``created``, ``revived``,
            ``reenabled``, ``reminted``, ``noop_existing``,
            ``noop_cancelled``, ``noop_paused``, ``noop_archived_cancelled``,
            ``noop_knobs_invalid`` or ``failed``.
        source_id: The chain row's id (``None`` when no row exists).
        run_at: The row's ``run_at`` after the pass (``None`` if unknown).
        detail: A human-readable explanation for the log line.
        reportless_week: The BR-15 finding (``None`` when there is none).
    """

    action: str
    source_id: str | None = None
    run_at: str | None = None
    detail: str = ""
    reportless_week: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "source_id": self.source_id,
            "run_at": self.run_at,
            "detail": self.detail,
            "reportless_week": self.reportless_week,
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Paths — the LIVE-INSTALL daemon project root (never a worktree)
# ---------------------------------------------------------------------------


def resolve_live_install_root() -> Path:
    """Resolve the live-install daemon project root.

    The house convention (``daemon/registry.py``, ``daemon/loader.py``,
    ``daemon/manager.py``): a frozen (PyInstaller) install resolves to
    the executable's directory; a source install resolves to the
    directory that holds the ``daemon/`` package.  The result is the
    INSTALL's own root — it never depends on ``Path.cwd()``, so a daemon
    launched from a worktree still writes into its own install.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # daemon/repositories/source/maintenance_seed.py → install root.
    return Path(__file__).resolve().parents[3]


def maintenance_results_dir(root: Path | None = None) -> Path:
    """The artifact root: ``<install root>/.agents/maintenancer/RESULTS/``."""
    base = resolve_live_install_root() if root is None else Path(root)
    return base / RESULTS_RELATIVE_DIR


def ensure_maintenance_results_dir(root: Path | None = None) -> Path | None:
    """Idempotently create the artifact root (first-boot dir creation).

    Returns the directory path, or ``None`` when it could not be created
    (logged; never fatal — a missing artifact root must not abort boot).
    """
    target = maintenance_results_dir(root)
    try:
        target.mkdir(parents=True, exist_ok=True)
        return target
    except Exception as exc:  # noqa: BLE001 — a missing dir never aborts boot
        logger.warning(
            "[CentralMaintenance] could not create the artifact root %s: %s",
            target,
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Config construction
# ---------------------------------------------------------------------------


def _refresh_chain_keys(
    config: dict[str, Any],
    *,
    picked: Any,
    knobs: Any,
) -> dict[str, Any]:
    """Refresh ONLY the chain-owned keys of an existing config.

    Every other key (agent / message / recurrence / instance_mode /
    project_id / anything a future surface adds) is PRESERVED — the
    config column is full-replaced on write
    (``daemon/repositories/source/repository.py:178-201``), so a partial
    refresh that drops a key would drop it from the row.
    """
    updated = dict(config)
    updated["run_at"] = picked.run_at_iso
    updated["local_time"] = picked.local_time
    updated["timezone"] = knobs.timezone
    chain = dict(updated.get("chain") or {})
    chain.update(
        {
            "mode": CHAIN_MODE,
            "window_start": knobs.window_start,
            "window_end": knobs.window_end,
            "weekday": knobs.weekday,
            "timezone": knobs.timezone,
            "run_at_utc": picked.run_at_iso,
        }
    )
    updated["chain"] = chain
    return updated


def build_chain_config(
    *,
    project_id: str,
    picked: Any,
    knobs: Any,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the chain row's COMPLETE config.

    ``base`` carries an existing config forward (the archive snapshot on
    the revive path) — the resolved keys are then OVERWRITTEN with the
    freshly minted values, and every other key (agent / message /
    timezone / recurrence / instance_mode / project_id / chain) is
    PRESERVED.

    The ``chain`` block records the RESOLVED knobs for audit; the env
    remains authoritative at the next arm/re-arm.
    """
    config: dict[str, Any] = dict(base or {})
    config.update(
        {
            "agent": config.get("agent") or "maintenancer",
            "message": config.get("message") or WEEKLY_RUN_MESSAGE,
            "timezone": knobs.timezone,
            "recurrence": config.get("recurrence") or "once",
            "local_time": picked.local_time,
            "run_at": picked.run_at_iso,
            "instance_mode": config.get("instance_mode") or "new_instance",
            "project_id": config.get("project_id") or project_id,
        }
    )
    chain = dict(config.get("chain") or {})
    chain.update(
        {
            "mode": CHAIN_MODE,
            "window_start": knobs.window_start,
            "window_end": knobs.window_end,
            "weekday": knobs.weekday,
            "timezone": knobs.timezone,
            "run_at_utc": picked.run_at_iso,
        }
    )
    config["chain"] = chain
    return config


def _chain_block(config: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the ``chain`` block of a config, or ``None``."""
    if not isinstance(config, dict):
        return None
    chain = config.get("chain")
    if isinstance(chain, dict) and chain:
        return chain
    return None


def _parse_run_at(raw: Any) -> datetime | None:
    """Parse a stored ``run_at`` into an aware UTC datetime."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def _read_archived_row(
    engine: Engine,
    *,
    source_id: str,
    name: str,
) -> SourceConfigArchive | None:
    """Read the archive snapshot for the chain row (BR-3).

    Looked up by id FIRST, then by name — a row created outside the
    seeder (e.g. through the REST surface) carries a random id but the
    same label.
    """
    try:
        with Session(engine) as session:
            row = session.get(SourceConfigArchive, source_id)
            if row is not None:
                return row
            stmt = select(SourceConfigArchive).where(
                SourceConfigArchive.name == name
            )
            return session.exec(stmt).first()
    except Exception as exc:  # noqa: BLE001 — an unreadable archive is not fatal
        logger.warning(
            "[CentralMaintenance] archive lookup failed for %s: %s",
            source_id,
            exc,
        )
        return None


def _archived_config(archive: SourceConfigArchive | None) -> dict[str, Any]:
    """Decode the archive's config snapshot (a JSON string)."""
    if archive is None:
        return {}
    raw = archive.config
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning(
            "[CentralMaintenance] archived config snapshot is not valid JSON"
        )
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def _lateness_cap() -> int | None:
    """Read the one-shot lateness cap (BR-1).

    ``SchedulingConfig.one_shot_max_lateness_seconds``; ``None`` (the
    default) means UNLIMITED — the past-due one-shot then fires late
    instead of being skipped.
    """
    try:
        from daemon.config import SchedulingConfig

        return SchedulingConfig().one_shot_max_lateness_seconds
    except Exception as exc:  # noqa: BLE001 — bootstrap-safety
        logger.debug(
            "[CentralMaintenance] SchedulingConfig unavailable (%s); "
            "treating the lateness cap as None (unlimited)",
            exc,
        )
        return None


def _write_chain_config(
    source_repo: Any,
    *,
    source_id: str,
    config: dict[str, Any],
    enabled: bool = True,
    status: str | None = None,
) -> None:
    """Persist the chain row's COMPLETE config (full-replace semantics).

    ``update_source_config`` FULL-REPLACES the config column
    (``daemon/repositories/source/repository.py:178-201``) — the caller
    always passes the complete config.
    """
    source_repo.update_source_config(source_id, config=config, enabled=enabled)
    if status is not None:
        try:
            source_repo.update_source_status(source_id, status)
        except Exception as exc:  # noqa: BLE001 — status is best-effort
            logger.warning(
                "[CentralMaintenance] could not set the status of %s to %s: %s",
                source_id,
                status,
                exc,
            )


def _mint(
    knobs: Any,
    *,
    now: datetime,
    allow_today: bool,
    rng: Any,
) -> Any:
    """Mint a fire instant via the knobs picker (fail-closed on error)."""
    from daemon.services.maintenance_schedule_knobs import pick_next_fire_instant

    return pick_next_fire_instant(
        knobs,
        now=now,
        allow_today=allow_today,
        rng=rng,
    )


def _apply_br1_past_run_at(
    source_repo: Any,
    row: SourceConfig,
    *,
    now: datetime,
    cap: int | None,
    knobs: Any,
    rng: Any,
) -> tuple[dict[str, Any] | None, str]:
    """Apply the BR-1 predicate to a present chain row.

    Returns ``(new_config_or_None, action)``:

    * ``(None, "run_at_future")`` — nothing to do;
    * ``(None, "past_run_at_left_for_auto_fire")`` — the cap is unset
      (or the lateness is within the cap): the past ``run_at`` is LEFT
      ALONE so the adapter's past-due auto-fire delivers the late run;
    * ``(config, "reminted")`` — the cap is configured AND the lateness
      exceeds it: a fresh instant is minted and the COMPLETE config is
      written back.
    """
    config = dict(row.config or {})
    raw_run_at = config.get("run_at")
    run_at = _parse_run_at(raw_run_at)
    if run_at is None:
        # An unparseable/missing run_at would make the adapter raise at
        # construction time — the chain would be dead. Repair it.
        picked = _mint(knobs, now=now, allow_today=True, rng=rng)
        return _refresh_chain_keys(config, picked=picked, knobs=knobs), (
            "reminted_unparseable_run_at"
        )
    if run_at > now:
        return None, "run_at_future"
    lateness = (now - run_at).total_seconds()
    if cap is None:
        return None, "past_run_at_left_for_auto_fire"
    if lateness <= cap:
        return None, "past_run_at_within_cap"
    picked = _mint(knobs, now=now, allow_today=True, rng=rng)
    return _refresh_chain_keys(config, picked=picked, knobs=knobs), "reminted"


def seed_central_maintenance_schedule(
    engine: Engine,
    *,
    now: datetime | None = None,
    results_root: Path | None = None,
    rng: Any = None,
) -> GuardOutcome:
    """The boot-time chain guard (archive-aware get-or-recreate).

    Never raises: every failure is contained and reported through the
    returned :class:`GuardOutcome` (the caller logs it).

    Args:
        engine: The daemon's engine (the seeder builds its own
            repositories from it).
        now: The reference instant (defaults to "now", UTC).
        results_root: Override for the artifact root (tests only; the
            production path resolves the LIVE-INSTALL root).
        rng: Optional seeded ``random.Random`` (tests only).

    Returns:
        The :class:`GuardOutcome` describing what the pass did.
    """
    now = datetime.now(timezone.utc) if now is None else now
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # First-boot artifact-root creation (owned by this seeder).
    ensure_maintenance_results_dir(results_root)

    outcome = GuardOutcome(action="failed")
    try:
        project_repo = create_project_repository(engine=engine, create_tables=False)
        source_repo = create_source_repository(engine=engine, create_tables=False)
        project_id = project_repo.ensure_system_default_project()

        from daemon.services.maintenance_schedule_knobs import (
            MaintenanceKnobError,
            read_maintenance_knobs,
        )

        try:
            knobs = read_maintenance_knobs()
        except MaintenanceKnobError as exc:
            # Fail-closed: no safe fire instant ⇒ no arm.  The guard
            # retries at the next boot.
            logger.error(
                "[CentralMaintenance] knobs are invalid — refusing to arm "
                "the weekly chain: %s",
                exc,
            )
            outcome = GuardOutcome(
                action="noop_knobs_invalid",
                detail=str(exc),
            )
            outcome.reportless_week = _detect_and_surface_reportless_week(
                source_repo,
                source_id=SEED_SOURCE_ID,
                results_root=results_root,
                engine=engine,
            )
            return outcome

        # ── the row: id first, then name (seed.py:161-166) ──────────────
        row = source_repo.get_source_config(SEED_SOURCE_ID)
        if row is None:
            row = source_repo.get_source_config_by_name(SEED_NAME)

        if row is not None:
            outcome = _guard_present_row(
                source_repo,
                row,
                knobs=knobs,
                now=now,
                rng=rng,
            )
        else:
            outcome = _guard_missing_row(
                source_repo,
                engine,
                knobs=knobs,
                now=now,
                rng=rng,
                project_id=project_id,
            )

        outcome.reportless_week = _detect_and_surface_reportless_week(
            source_repo,
            source_id=outcome.source_id or SEED_SOURCE_ID,
            results_root=results_root,
            engine=engine,
        )
        return outcome
    except Exception as exc:  # noqa: BLE001 — the guard never aborts boot
        logger.warning("[CentralMaintenance] chain guard failed: %s", exc)
        return GuardOutcome(action="failed", detail=str(exc))


def _guard_present_row(
    source_repo: Any,
    row: SourceConfig,
    *,
    knobs: Any,
    now: datetime,
    rng: Any,
) -> GuardOutcome:
    """The live-row decision table (plan Task 1.4 (ii) / (iii) / (iii-b))."""
    config = dict(row.config or {})
    chain = _chain_block(config)

    # (ii-a) CANCELLED ⇒ OPERATOR INTENT ⇒ NEVER touched.
    if row.status == SourceStatus.CANCELLED.value:
        logger.info(
            "[CentralMaintenance] chain row %s is cancelled — respecting "
            "the operator (no revive, no re-mint)",
            row.source_id,
        )
        return GuardOutcome(
            action="noop_cancelled",
            source_id=row.source_id,
            run_at=config.get("run_at") if isinstance(config, dict) else None,
            detail="cancelled row — operator intent preserved",
        )

    # (ii-b) PAUSED ⇒ OPERATOR INTENT ⇒ NEVER touched.
    #
    # A paused schedule is ``enabled=False`` + ``status='stopped'``
    # (``daemon/services/scheduling_service.py:1122-1141``).  The
    # DB representation of a PAUSE and of the (pre-Phase-1) one-shot
    # completion disable is therefore IDENTICAL — the chain's own
    # disable no longer happens (the adapter's chain-aware skip), so
    # ``enabled=False`` is read as the operator's pause.
    if not row.enabled:
        logger.info(
            "[CentralMaintenance] chain row %s is paused (enabled=False) — "
            "respecting the operator (no revive, no re-mint)",
            row.source_id,
        )
        return GuardOutcome(
            action="noop_paused",
            source_id=row.source_id,
            run_at=config.get("run_at") if isinstance(config, dict) else None,
            detail="paused row — operator intent preserved",
        )

    # (iii-b) STOPPED but ENABLED ⇒ the chain broke ⇒ RE-ENABLE.
    #
    # A ``stopped`` row is skipped by the registry's boot filter
    # (``daemon/sources/registry.py:325-334``), so the status must be
    # promoted to RUNNING for the row to be picked up again.
    if row.status == SourceStatus.STOPPED.value:
        if chain is None:
            logger.info(
                "[CentralMaintenance] chain row %s is stopped and carries no "
                "chain block — leaving it alone",
                row.source_id,
            )
            return GuardOutcome(
                action="noop_existing",
                source_id=row.source_id,
                run_at=config.get("run_at"),
                detail="stopped row without a chain block",
            )
        logger.warning(
            "[CentralMaintenance] chain row %s was stopped — RE-ENABLING it "
            "(the chain-break self-heal)",
            row.source_id,
        )
        _write_chain_config(
            source_repo,
            source_id=row.source_id,
            config=config,
            enabled=True,
            status=SourceStatus.RUNNING.value,
        )
        outcome = GuardOutcome(
            action="reenabled",
            source_id=row.source_id,
            run_at=config.get("run_at"),
            detail="stopped chain row re-enabled at boot",
        )
        remint_config, remint_action = _apply_br1_past_run_at(
            source_repo,
            row,
            now=now,
            cap=_lateness_cap(),
            knobs=knobs,
            rng=rng,
        )
        if remint_config is not None:
            _write_chain_config(
                source_repo,
                source_id=row.source_id,
                config=remint_config,
                enabled=True,
                status=SourceStatus.RUNNING.value,
            )
            outcome.action = "reenabled_reminted"
            outcome.run_at = remint_config.get("run_at")
            outcome.detail = f"{outcome.detail}; {remint_action}"
        else:
            outcome.detail = f"{outcome.detail}; {remint_action}"
        return outcome

    # (iii) A live row: BR-1 governs the past-``run_at`` question only.
    remint_config, remint_action = _apply_br1_past_run_at(
        source_repo,
        row,
        now=now,
        cap=_lateness_cap(),
        knobs=knobs,
        rng=rng,
    )
    if remint_config is not None:
        _write_chain_config(
            source_repo,
            source_id=row.source_id,
            config=remint_config,
            enabled=True,
        )
        return GuardOutcome(
            action="reminted",
            source_id=row.source_id,
            run_at=remint_config.get("run_at"),
            detail=remint_action,
        )
    return GuardOutcome(
        action="noop_existing",
        source_id=row.source_id,
        run_at=config.get("run_at"),
        detail=remint_action,
    )


def _guard_missing_row(
    source_repo: Any,
    engine: Engine,
    *,
    knobs: Any,
    now: datetime,
    rng: Any,
    project_id: str,
) -> GuardOutcome:
    """The archive-aware recreate (BR-3).

    The archive is consulted BEFORE any create: an archived ``cancelled``
    chain is NEVER revived, an archived ``stopped`` chain is revived IFF
    its config snapshot carries a ``chain`` block, and a row with no
    archive snapshot is created fresh.
    """
    archive = _read_archived_row(engine, source_id=SEED_SOURCE_ID, name=SEED_NAME)
    if archive is not None:
        archive_status = (archive.status or "").strip().lower()
        archived_config = _archived_config(archive)
        archived_chain = _chain_block(archived_config)

        if archive_status == SourceStatus.CANCELLED.value:
            logger.warning(
                "[CentralMaintenance] the chain row was archived as CANCELLED "
                "— NOT reviving it (operator intent, BR-3)"
            )
            return GuardOutcome(
                action="noop_archived_cancelled",
                source_id=SEED_SOURCE_ID,
                detail="archived cancelled chain — never revived",
            )

        if archive_status == SourceStatus.STOPPED.value and archived_chain:
            # Restore the snapshot; the stale ``run_at`` is then handled
            # by the BR-1 predicate exactly like a present row (a cap
            # that is exceeded ⇒ re-mint; otherwise the adapter's
            # past-due auto-fire delivers the late run).
            source_id = archive.source_id or SEED_SOURCE_ID
            config = dict(archived_config)
            try:
                row = source_repo.create_source_config(
                    source_type=SEED_SOURCE_TYPE,
                    name=SEED_NAME,
                    config=config,
                    credentials=None,
                    enabled=True,
                    autostart=True,
                    source_id=source_id,
                )
            except Exception as exc:  # noqa: BLE001 — the create is best-effort
                logger.warning(
                    "[CentralMaintenance] could not revive the archived chain "
                    "row: %s",
                    exc,
                )
                return GuardOutcome(action="failed", detail=str(exc))
            try:
                source_repo.update_source_status(
                    row.source_id, SourceStatus.RUNNING.value
                )
            except Exception as exc:  # noqa: BLE001 — status is best-effort
                logger.warning(
                    "[CentralMaintenance] could not promote the revived chain "
                    "row %s to RUNNING: %s",
                    row.source_id,
                    exc,
                )
            logger.warning(
                "[CentralMaintenance] revived the archived chain row %s "
                "(archived status=stopped, chain block present)",
                row.source_id,
            )
            outcome = GuardOutcome(
                action="revived",
                source_id=row.source_id,
                run_at=config.get("run_at"),
                detail="revived from the archive (stopped + chain block)",
            )
            remint_config, remint_action = _apply_br1_past_run_at(
                source_repo,
                row,
                now=now,
                cap=_lateness_cap(),
                knobs=knobs,
                rng=rng,
            )
            if remint_config is not None:
                _write_chain_config(
                    source_repo,
                    source_id=row.source_id,
                    config=remint_config,
                    enabled=True,
                    status=SourceStatus.RUNNING.value,
                )
                outcome.action = "revived_reminted"
                outcome.run_at = remint_config.get("run_at")
                outcome.detail = f"{outcome.detail}; {remint_action}"
            else:
                outcome.detail = f"{outcome.detail}; {remint_action}"
            return outcome

    # No usable archive ⇒ CREATE.
    picked = _mint(knobs, now=now, allow_today=True, rng=rng)
    config = build_chain_config(
        project_id=project_id,
        picked=picked,
        knobs=knobs,
    )
    try:
        row = source_repo.create_source_config(
            source_type=SEED_SOURCE_TYPE,
            name=SEED_NAME,
            config=config,
            credentials=None,
            enabled=True,
            autostart=True,
            source_id=SEED_SOURCE_ID,
        )
    except Exception as exc:  # noqa: BLE001 — the create is best-effort
        logger.warning(
            "[CentralMaintenance] could not create the chain row: %s", exc
        )
        return GuardOutcome(action="failed", detail=str(exc))
    # Status compensation: create initializes STOPPED; ``start_all``
    # skips stopped/cancelled rows ⇒ promote to RUNNING.
    try:
        source_repo.update_source_status(row.source_id, SourceStatus.RUNNING.value)
    except Exception as exc:  # noqa: BLE001 — status is best-effort
        logger.warning(
            "[CentralMaintenance] could not promote the chain row %s to "
            "RUNNING: %s",
            row.source_id,
            exc,
        )
    logger.info(
        "[CentralMaintenance] seeded the weekly chain row %s (run_at=%s)",
        row.source_id,
        config.get("run_at"),
    )
    return GuardOutcome(
        action="created",
        source_id=row.source_id,
        run_at=config.get("run_at"),
        detail="chain row created",
    )


# ---------------------------------------------------------------------------
# BR-15 — the reportless-week detector (surface-ONLY)
# ---------------------------------------------------------------------------


def _latest_run_report(results_dir: Path) -> tuple[Path | None, datetime | None]:
    """The newest ``run-*.md`` under the artifact root, if any."""
    if not results_dir.exists() or not results_dir.is_dir():
        return None, None
    newest: Path | None = None
    newest_mtime: datetime | None = None
    try:
        candidates = sorted(results_dir.glob("run-*.md"))
    except Exception as exc:  # noqa: BLE001 — a read failure is not fatal
        logger.warning(
            "[CentralMaintenance] could not list the artifact root %s: %s",
            results_dir,
            exc,
        )
        return None, None
    for candidate in candidates:
        try:
            mtime = datetime.fromtimestamp(
                candidate.stat().st_mtime, tz=timezone.utc
            )
        except OSError:
            continue
        if newest_mtime is None or mtime > newest_mtime:
            newest = candidate
            newest_mtime = mtime
    return newest, newest_mtime


def detect_reportless_week(
    source_repo: Any,
    *,
    source_id: str,
    results_root: Path | None = None,
) -> dict[str, Any] | None:
    """Detect a chain fire that produced no ``run-*.md`` report.

    Surface-ONLY: the detector NEVER authors a stub, NEVER self-heals
    and NEVER re-fires.  It answers one question — "did the most recent
    chain fire leave a report behind?".

    The "never fired yet" case is NOT a miss: with no execution record
    there is nothing to detect, and ``None`` is returned.

    Returns:
        The finding dict, or ``None`` when there is nothing to report.
    """
    try:
        execution = source_repo.get_latest_execution(source_id)
    except Exception as exc:  # noqa: BLE001 — a read failure is not fatal
        logger.warning(
            "[CentralMaintenance] could not read the latest execution for "
            "%s: %s",
            source_id,
            exc,
        )
        return None
    if execution is None:
        # No execution record ⇒ the chain never fired ⇒ nothing to detect.
        return None

    triggered_at = _parse_run_at(getattr(execution, "triggered_at", None))
    results_dir = maintenance_results_dir(results_root)
    newest, newest_mtime = _latest_run_report(results_dir)
    if newest is None:
        return {
            "source_id": source_id,
            "execution_id": getattr(execution, "execution_id", None),
            "triggered_at": triggered_at.isoformat() if triggered_at else None,
            "results_dir": str(results_dir),
            "latest_report": None,
            "detail": "the most recent chain fire produced no run-*.md report",
        }
    if triggered_at is not None and newest_mtime is not None:
        if newest_mtime < triggered_at:
            return {
                "source_id": source_id,
                "execution_id": getattr(execution, "execution_id", None),
                "triggered_at": triggered_at.isoformat(),
                "results_dir": str(results_dir),
                "latest_report": str(newest),
                "detail": (
                    "the most recent chain fire produced no run-*.md report "
                    "(the newest report predates the fire)"
                ),
            }
    return None


def _detect_and_surface_reportless_week(
    source_repo: Any,
    *,
    source_id: str,
    results_root: Path | None,
    engine: Engine,
) -> dict[str, Any] | None:
    """Detect AND surface a reportless week (log + board notice)."""
    finding = detect_reportless_week(
        source_repo,
        source_id=source_id,
        results_root=results_root,
    )
    if finding is None:
        return None
    logger.error(
        "[CentralMaintenance] REPORTLESS WEEK — %s (source_id=%s, "
        "results_dir=%s). Surface-only: no stub is authored, nothing is "
        "self-healed, nothing is re-fired.",
        finding.get("detail"),
        finding.get("source_id"),
        finding.get("results_dir"),
    )
    try:
        from daemon.services.board_service import BoardService

        board = BoardService(engine=engine)
        # De-dup: ONE open board item per reportless-week episode.  The
        # daemon may boot many times while the chain is broken — the log
        # line is the loud per-boot surface, the board carries the
        # durable one.
        existing = board.list_items(status="open", tag="reportless-week", limit=5)
        if int(existing.get("count") or 0) > 0:
            logger.info(
                "[CentralMaintenance] a reportless-week board item is "
                "already open — not creating a duplicate"
            )
            return finding
        board.create_item(
            title=BOARD_TITLE,
            body_md=(
                "The most recent central-maintenance chain fire produced no "
                "`run-*.md` report.\n\n"
                f"- source_id: {finding.get('source_id')}\n"
                f"- execution_id: {finding.get('execution_id')}\n"
                f"- triggered_at: {finding.get('triggered_at')}\n"
                f"- artifact root: {finding.get('results_dir')}\n\n"
                "Surface-only: the daemon authored no stub and did not "
                "re-fire the chain."
            ),
            tags=list(BOARD_TAGS),
            created_by_instance=BOARD_AUTHOR,
        )
    except Exception as exc:  # noqa: BLE001 — the notice is best-effort
        logger.warning(
            "[CentralMaintenance] could not write the reportless-week board "
            "notice: %s",
            exc,
        )
    return finding


__all__ = [
    "BOARD_AUTHOR",
    "BOARD_TAGS",
    "BOARD_TITLE",
    "CHAIN_MODE",
    "GuardOutcome",
    "MaintenanceSeedError",
    "RESULTS_RELATIVE_DIR",
    "SEED_NAME",
    "SEED_SOURCE_ID",
    "SEED_SOURCE_TYPE",
    "WEEKLY_RUN_MESSAGE",
    "build_chain_config",
    "detect_reportless_week",
    "ensure_maintenance_results_dir",
    "maintenance_results_dir",
    "resolve_live_install_root",
    "seed_central_maintenance_schedule",
]
