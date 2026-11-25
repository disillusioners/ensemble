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

The D4 CURE — the FRESH-ROOT guard (a DISTINCT surfacing)
----------------------------------------------------------
An EMPTY artifact root is NOT a reportless week.  Two conditions share
the "no ``run-*.md``" shape and MUST NOT be conflated:

* **"no runs ever"** — no durable last-fire state (the chain never
  fired): there is nothing to detect; the root is simply fresh;
* **"root moved/lost"** — durable last-fire state EXISTS but the root
  holds NO ``run-*.md``: a run should have landed and the root is
  empty where it must not be.  That is a POSSIBLE moved/lost root
  (the D4 promote-time seam) and it is surfaced as its OWN finding
  kind — a loud log line plus ONE de-duplicated board item — NEVER as
  a routine reportless-week event.

Artifact root
-------------
``.agents/maintenancer/RESULTS/`` under the PROMOTE-STABLE
LIVE-INSTALL daemon project root — the INSTALL DIR (the ancestor that
owns ``releases/``) for a frozen install, else the directory that
holds ``daemon/``.  NEVER from ``Path.cwd()`` and NEVER from a path
under ``releases/<ver>/``: the root must not MOVE when a release is
promoted.  A promote ADDS a release dir and repoints ``current``
INSIDE the install dir, and the keep-3 release eviction only ever
removes a ``releases/<ver>/`` subtree — a release-scoped root would
therefore move on every promote (stranding the run history) and lose
it on every eviction.  The seeder creates that directory idempotently
at first boot.

No-idle attestation
-------------------
This module carries NO idle/busyness precondition of any kind: the
weekly trigger fires regardless of system busyness (R2 / E-2).

Size rationale (tidier fix pass 2026-11-22, 1352 lines at the tip): this
module owns
FOUR concerns, all of them about the ONE chain row and its artifact
root. (1) The PROMOTE-STABLE install-root resolution
(``resolve_live_install_root`` / ``maintenance_results_dir``) — a PURE
path helper with no row access. (2) The chain-config CONSTRUCTION
(``build_chain_config`` / ``refresh_chain_keys``) — the config the row
carries. (3) The boot-time chain-break GUARD (the BR-1 past-``run_at``
re-mint predicate + the BR-3 one-concept-one-marker decisions). (4) The
TWO surfacing detectors (BR-15 reportless week + the D4 fresh-root
detector) and their BOARD-NOTICE surface. The GUARD and the DETECTORS
mutate the SAME row and read the config the CONSTRUCTION writes, so they
stay textually adjacent to it; splitting them apart would fork the
config shape they share. The install-root resolution is the ONE
SEPARABLE concern — it is a pure function of the install layout and
belongs in its own path module (a pure MOVE, no behaviour change, and it
is the extraction that unblocks the rest). The board-notice writer is
separable from the detectors ONLY by moving the notice payload with
them; it stays for now. The remaining guard/detector split is DEFERRED
to a post-staging refactor.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from dataclasses import dataclass
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

#: The CANONICAL row's label (architecture v3 §4.3).
#:
#: The seeder no longer composes CHAIN-shaped rows for NEW schedules —
#: it writes the canonical recurring row (``recurrence=weekly`` + cron +
#: local_time + timezone) under this label.  The label is DISTINCT from
#: ``SEED_NAME`` (the chain row's label — the cancelled chain row keeps
#: it for history and stays greppable) and from any operator-created
#: row: a distinct label is what makes the dead-label reuse impossible
#: in the first place.
CANONICAL_SEED_NAME = "central-maintenance-weekly-canonical"
CANONICAL_SEED_SOURCE_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, CANONICAL_SEED_NAME))

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

#: The board item written when the artifact root is EMPTY where a run
#: should have landed (the D4 moved/lost-root notice).  Its OWN title
#: and its OWN tag: the two notices never share a de-dup key, so an
#: open reportless-week item can never mask a moved root (and vice
#: versa).
BOARD_TITLE_ROOT_EMPTY = (
    "maintenance: results root empty (possible moved/lost root)"
)
BOARD_TAGS_ROOT_EMPTY = ("maintenance", "central-maintenance", "results-root-empty")

#: The finding kinds the boot guard surfaces.  The two are DISTINCT:
#: a reportless week is a fire that produced no report; an empty
#: results root is a root that may have MOVED (or been lost) — the
#: empty-root case is NEVER surfaced as a reportless week.
FINDING_KIND_REPORTLESS_WEEK = "reportless_week"
FINDING_KIND_RESULTS_ROOT_EMPTY = "results_root_empty"

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
        results_root_empty: The D4 fresh-root finding (``None`` when there
            is none) — a MOVED/LOST artifact root.  DISTINCT from
            ``reportless_week``: an empty root is never surfaced as a
            routine reportless week.
    """

    action: str
    source_id: str | None = None
    run_at: str | None = None
    detail: str = ""
    reportless_week: dict[str, Any] | None = None
    results_root_empty: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Paths — the PROMOTE-STABLE live-install daemon project root
# ---------------------------------------------------------------------------


def _install_dir_owning_releases(exe: Path) -> Path | None:
    """The nearest ancestor of ``exe`` that owns a ``releases/`` dir.

    The staged binary lives at ``<INSTALL_DIR>/releases/<ver>/ensemble-prod``
    (``scripts/upgrade/stage.sh:5-17``), so the INSTALL DIR is the
    ancestor that owns ``releases/``.  The walk (bounded to four
    ancestors) covers the resolved-release shape
    (``releases/<ver>/ensemble-prod``) AND the ``current``-symlink
    shape (``current/ensemble-prod``); the symlink is resolved FIRST,
    so the former is what the walk normally sees.

    Returns ``None`` when no ancestor looks like an install dir.
    """
    for candidate in list(exe.parents)[:4]:
        try:
            if (candidate / "releases").is_dir():
                return candidate
        except OSError:  # pragma: no cover — an unreadable ancestor
            continue
    return None


def _frozen_install_root() -> Path:
    """The PROMOTE-STABLE install root of a frozen (PyInstaller) install.

    The OLD basis (``Path(sys.executable).parent`` — the binary's own
    directory) landed the artifact root UNDER ``releases/<ver>/``: every
    promote MOVED the root, the keep-3 release eviction DESTROYED the
    run history, and the BR-15 detector false-positived a reportless
    week on every post-promote boot.

    The install dir is what a promote does NOT move: a promote adds a
    release dir and repoints ``current`` INSIDE the install dir, and
    the eviction only ever removes a ``releases/<ver>/`` subtree.
    """
    try:
        exe = Path(sys.executable).resolve()
    except OSError:  # pragma: no cover — an exotic resolve failure
        exe = Path(sys.executable)
    # The house convention (two parents up from the binary) — the exact
    # shape stage.sh produces.
    candidate = exe.parent.parent.parent
    try:
        if (candidate / "releases").is_dir():
            return candidate
    except OSError:  # pragma: no cover — an unreadable ancestor
        pass
    walk = _install_dir_owning_releases(exe)
    if walk is not None:
        return walk
    # Nothing verified an install dir — the house convention stands as
    # the last resort (it is the layout the launcher stages).
    return candidate


def resolve_live_install_root() -> Path:
    """Resolve the PROMOTE-STABLE live-install daemon project root.

    The basis, by install shape:

    * **frozen install** — the INSTALL DIR (the ancestor that owns
      ``releases/``): stable across promotes AND across the keep-3
      release eviction (the D4 cure);
    * **source install** (dev / worktree) — the checkout root (the
      directory that holds ``daemon/``): the daemon's OWN tree, so a
      dev run never writes into the production install.  A source tree
      that itself lives under ``releases/<ver>/`` is lifted to its
      install dir, so that shape can never re-introduce a
      release-scoped root.

    NEVER ``Path.cwd()`` (a daemon launched from anywhere writes into
    its own install) and NEVER a path under ``releases/<ver>/`` (a
    promote would move the root and strand the run history).
    """
    if getattr(sys, "frozen", False):
        return _frozen_install_root()
    # daemon/repositories/source/maintenance_seed.py → install root.
    checkout = Path(__file__).resolve().parents[3]
    if checkout.parent.name == "releases":
        # A source tree inside a release dir — lift to the install dir.
        install = checkout.parent.parent
        try:
            if (install / "releases").is_dir():
                return install
        except OSError:  # pragma: no cover — an unreadable ancestor
            pass
    return checkout


def maintenance_results_dir(root: Path | None = None) -> Path:
    """THE artifact-root resolver — ``<install root>/.agents/maintenancer/RESULTS/``.

    ONE helper, THREE surfaces (the D4 cure): the seeder (P1), the BE
    list route (``daemon/routers/maintenance_reports.py``) and the
    live-views root registration (``daemon/api.py``) all resolve through
    THIS function, so the basis logic exists exactly once and the three
    surfaces can never disagree.

    Args:
        root: An explicit base directory (tests only). ``None`` resolves
            the PROMOTE-STABLE live-install root.
    """
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


def refresh_chain_keys(
    config: dict[str, Any],
    *,
    picked: Any,
    knobs: Any,
) -> dict[str, Any]:
    """Refresh ONLY the chain-owned keys of an existing config.

    PUBLIC — the adapter-side chain re-arm seam
    (``daemon/sources/adapters/scheduler.py``) reuses THIS function
    instead of inlining its own refresh, so the chain marker
    (``CHAIN_MODE``) and the resolved-knob keys exist in exactly ONE
    place (BR-3 — one concept, one marker).

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


def build_canonical_config(
    *,
    project_id: str,
    knobs: Any,
) -> dict[str, Any]:
    """Build the CANONICAL recurring row's config (architecture v3 §4.3).

    The canonical shape is ONE user-facing mechanism: ``recurrence=weekly``
    + a raw cron expression + the local time + the timezone.  There is NO
    ``chain`` block — the chain marker is demoted to an internal
    compatibility representation, readable for legacy rows but never
    composed for new ones — and NO ``run_at`` (a recurring row re-arms
    itself through its cron expression).

    The fire instant is the window START: the FIXED slot.  Jitter is
    refused until a recurring collision is MEASURED (architecture v3
    §2.2, Branch A) — a random pick inside the window would be
    unearned complexity.
    """
    local_time = (getattr(knobs, "window_start", "") or "").strip()
    parts = local_time.split(":")
    if len(parts) != 2:
        raise ValueError(
            f"maintenance knobs: window_start={local_time!r} is malformed "
            "— expected HH:MM"
        )
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ValueError(
            f"maintenance knobs: window_start={local_time!r} is malformed "
            "— expected HH:MM"
        ) from exc
    if not (0 <= hour <= 23) or not (0 <= minute <= 59):
        raise ValueError(
            f"maintenance knobs: window_start={local_time!r} is out of range "
            "— expected 00:00..23:59"
        )
    weekday = int(getattr(knobs, "weekday", 0))
    return {
        "agent": "maintenancer",
        "message": WEEKLY_RUN_MESSAGE,
        "timezone": knobs.timezone,
        "recurrence": "weekly",
        "local_time": local_time,
        "schedule": f"{minute} {hour} * * {weekday}",
        "instance_mode": "new_instance",
        "project_id": project_id,
    }


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


def _effective_source_id(source_repo: Any) -> str:
    """The source id of the row that ACTUALLY fires (canonical row first).

    The canonical recurring row (architecture v3 §4.3) is the row the
    seeder writes for new schedules; the chain row is the legacy
    identity.  The reportless-week / empty-root detectors must follow the
    row that fires, so the canonical identity wins when it exists.
    """
    for candidate in (CANONICAL_SEED_SOURCE_ID, SEED_SOURCE_ID):
        try:
            if source_repo.get_source_config(candidate) is not None:
                return candidate
        except Exception as exc:  # noqa: BLE001 — the probe is best-effort
            logger.warning(
                "[CentralMaintenance] could not probe %s: %s", candidate, exc
            )
    return SEED_SOURCE_ID


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
        return refresh_chain_keys(config, picked=picked, knobs=knobs), (
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
    return refresh_chain_keys(config, picked=picked, knobs=knobs), "reminted"


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
                source_id=_effective_source_id(source_repo),
                results_root=results_root,
                engine=engine,
            )
            outcome.results_root_empty = _detect_and_surface_results_root_empty(
                source_repo,
                source_id=_effective_source_id(source_repo),
                results_root=results_root,
                engine=engine,
            )
            return outcome

        # ── the row: id first, then name (seed.py:161-166) ──────────────
        #
        # The CANONICAL identity is looked up too (architecture v3 §4.3):
        # the seeder writes the canonical recurring row under a distinct
        # label, and the guard MUST find it on the next boot — otherwise
        # every boot would try to create it again.
        row = source_repo.get_source_config(SEED_SOURCE_ID)
        if row is None:
            row = source_repo.get_source_config_by_name(SEED_NAME)
        if row is None:
            row = source_repo.get_source_config(CANONICAL_SEED_SOURCE_ID)
        if row is None:
            row = source_repo.get_source_config_by_name(CANONICAL_SEED_NAME)

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
        outcome.results_root_empty = _detect_and_surface_results_root_empty(
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
            run_at=config.get("run_at"),
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
            run_at=config.get("run_at"),
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
    #
    # The BR-1 repair is CHAIN-SPECIFIC (it re-mints a stale one-shot's
    # ``run_at`` and refreshes the chain keys).  A CANONICAL recurring row
    # (or any row without a chain block) has no ``run_at`` to re-mint —
    # running the repair on it would GRAFT a chain block onto a canonical
    # row, undoing the canonical shape (architecture v3 §4.3).  Such a
    # row is left exactly as it is.
    if chain is None:
        return GuardOutcome(
            action="noop_existing",
            source_id=row.source_id,
            run_at=config.get("run_at"),
            detail="live row without a chain block — no BR-1 repair",
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
    #
    # The CANONICAL recurring row (architecture v3 §4.3): the seeder no
    # longer composes a chain-shaped row for a NEW schedule.  The row is
    # a plain ``recurrence=weekly`` + cron + local_time + timezone row
    # under a DISTINCT label — no ``chain`` block, no ``run_at``, no
    # random pick (the fixed window-start slot; jitter is refused until
    # a recurring collision is MEASURED — architecture v3 §2.2).
    #
    # The CANONICAL identity carries its own archive rule: an archived
    # CANCELLED canonical row is never revived either (operator intent).
    canonical_archive = _read_archived_row(
        engine,
        source_id=CANONICAL_SEED_SOURCE_ID,
        name=CANONICAL_SEED_NAME,
    )
    if canonical_archive is not None:
        canonical_status = (canonical_archive.status or "").strip().lower()
        if canonical_status == SourceStatus.CANCELLED.value:
            logger.warning(
                "[CentralMaintenance] the canonical row was archived as "
                "CANCELLED — NOT reviving it (operator intent)"
            )
            return GuardOutcome(
                action="noop_archived_cancelled",
                source_id=CANONICAL_SEED_SOURCE_ID,
                detail="archived cancelled canonical row — never revived",
            )
    try:
        config = build_canonical_config(project_id=project_id, knobs=knobs)
    except Exception as exc:  # noqa: BLE001 — a bad knob shape never aborts boot
        logger.warning(
            "[CentralMaintenance] could not build the canonical row config: %s",
            exc,
        )
        return GuardOutcome(action="failed", detail=str(exc))
    try:
        row = source_repo.create_source_config(
            source_type=SEED_SOURCE_TYPE,
            name=CANONICAL_SEED_NAME,
            config=config,
            credentials=None,
            enabled=True,
            autostart=True,
            source_id=CANONICAL_SEED_SOURCE_ID,
        )
    except Exception as exc:  # noqa: BLE001 — the create is best-effort
        logger.warning(
            "[CentralMaintenance] could not create the canonical row: %s", exc
        )
        return GuardOutcome(action="failed", detail=str(exc))
    # Status compensation: create initializes STOPPED; ``start_all``
    # skips stopped/cancelled rows ⇒ promote to RUNNING.
    try:
        source_repo.update_source_status(row.source_id, SourceStatus.RUNNING.value)
    except Exception as exc:  # noqa: BLE001 — status is best-effort
        logger.warning(
            "[CentralMaintenance] could not promote the canonical row %s to "
            "RUNNING: %s",
            row.source_id,
            exc,
        )
    logger.info(
        "[CentralMaintenance] seeded the canonical weekly row %s "
        "(recurrence=weekly, cron=%s)",
        row.source_id,
        config.get("schedule"),
    )
    return GuardOutcome(
        action="created",
        source_id=row.source_id,
        run_at=None,
        detail="canonical recurring row created (no chain block)",
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

    The EMPTY-root case is NOT this detector's business (the D4 cure):
    an empty root is the moved/lost-root shape and belongs to
    :func:`detect_results_root_empty` — it is NEVER surfaced here as a
    routine reportless week.

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
        # The root holds NO report at all — that is the empty-root shape
        # (the root may have MOVED with a promote), not a reportless
        # week.  :func:`detect_results_root_empty` owns it; surfacing
        # it here would re-create the D4 false-positive.
        return None
    if triggered_at is not None and newest_mtime is not None:
        if newest_mtime < triggered_at:
            return {
                "kind": FINDING_KIND_REPORTLESS_WEEK,
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


def detect_results_root_empty(
    source_repo: Any,
    *,
    source_id: str,
    results_root: Path | None = None,
) -> dict[str, Any] | None:
    """Detect a MOVED/LOST artifact root (the D4 promote-time seam).

    This detector separates the two conditions that share the "no
    ``run-*.md``" shape:

    * **"no runs ever"** — no durable last-fire state (the chain never
      fired): there is nothing to detect and the root is simply fresh
      ⇒ ``None``;
    * **"root moved/lost"** — durable last-fire state EXISTS but the
      root holds NO ``run-*.md``: a run should have landed and the
      root is empty where it must not be.  The artifact root may have
      MOVED with a promote (the D4 seam) or been lost.

    Surface-ONLY: the detector NEVER authors a stub, NEVER recreates
    the root's content, NEVER self-heals and NEVER re-fires.  The
    finding is a DISTINCT kind — it is NEVER surfaced as a routine
    reportless-week event (that case belongs to
    :func:`detect_reportless_week`).

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
        # No durable last-fire state ⇒ the chain never fired ⇒ the empty
        # root is the FRESH-INSTALL state, not a moved/lost root.
        return None

    results_dir = maintenance_results_dir(results_root)
    newest, _newest_mtime = _latest_run_report(results_dir)
    if newest is not None:
        # The root holds reports ⇒ it is not empty ⇒ nothing to detect.
        return None

    triggered_at = _parse_run_at(getattr(execution, "triggered_at", None))
    return {
        "kind": FINDING_KIND_RESULTS_ROOT_EMPTY,
        "source_id": source_id,
        "execution_id": getattr(execution, "execution_id", None),
        "triggered_at": triggered_at.isoformat() if triggered_at else None,
        "results_dir": str(results_dir),
        "latest_report": None,
        "detail": (
            "the chain fired but the artifact root holds no run-*.md — the "
            "root may have MOVED (a promote) or been lost"
        ),
    }


def _surface_board_notice(
    engine: Engine,
    *,
    title: str,
    body_md: str,
    tags: list[str],
    dedup_tag: str,
) -> bool:
    """Write ONE board notice, de-duplicated against the OPEN items.

    The board is the daemon-side operator surface; the OPEN-item check
    is the DURABLE de-dup: the daemon may boot many times while the
    condition holds, so the log line is the loud per-boot surface and
    the board carries the durable one-shot record (ONE item per
    episode).

    Args:
        engine: The daemon's engine.
        title: The board item title.
        body_md: The board item body.
        tags: The board item tags.
        dedup_tag: The tag the de-dup counts on — the OPEN items
            carrying it suppress a duplicate write.

    Returns:
        True when an item was created, False when the write was
        de-duplicated or failed (the notice is best-effort — never
        fatal).
    """
    try:
        from daemon.services.board_service import BoardService

        board = BoardService(engine=engine)
        existing = board.list_items(status="open", tag=dedup_tag, limit=5)
        if int(existing.get("count") or 0) > 0:
            logger.info(
                "[CentralMaintenance] a %s board item is already open — not "
                "creating a duplicate",
                dedup_tag,
            )
            return False
        board.create_item(
            title=title,
            body_md=body_md,
            tags=list(tags),
            created_by_instance=BOARD_AUTHOR,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — the notice is best-effort
        logger.warning(
            "[CentralMaintenance] could not write the %s board notice: %s",
            dedup_tag,
            exc,
        )
        return False


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
    # De-dup: ONE open board item per reportless-week episode.  The
    # daemon may boot many times while the chain is broken — the log
    # line is the loud per-boot surface, the board carries the durable
    # one.
    _surface_board_notice(
        engine,
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
        dedup_tag="reportless-week",
    )
    return finding


def _detect_and_surface_results_root_empty(
    source_repo: Any,
    *,
    source_id: str,
    results_root: Path | None,
    engine: Engine,
) -> dict[str, Any] | None:
    """Detect AND surface a MOVED/LOST artifact root (log + board notice).

    The D4 fresh-root guard: durable last-fire state EXISTS but the
    artifact root holds no ``run-*.md``.  The surfacing is DISTINCT
    from the reportless-week notice — its own title, its own tag, its
    own de-dup key — so the two episodes can never mask each other,
    and it is NEVER reported as a routine reportless week.
    """
    finding = detect_results_root_empty(
        source_repo,
        source_id=source_id,
        results_root=results_root,
    )
    if finding is None:
        return None
    logger.error(
        "[CentralMaintenance] RESULTS ROOT EMPTY — %s (source_id=%s, "
        "results_dir=%s). The artifact root may have MOVED (a promote) or "
        "been lost. Surface-only: no stub is authored, nothing is "
        "self-healed, nothing is re-fired.",
        finding.get("detail"),
        finding.get("source_id"),
        finding.get("results_dir"),
    )
    # De-dup: ONE open board item per empty-root episode — the durable
    # state that keeps this a once-per-episode notice across boots.
    _surface_board_notice(
        engine,
        title=BOARD_TITLE_ROOT_EMPTY,
        body_md=(
            "The central-maintenance chain fired, but the artifact root "
            "holds no `run-*.md` — the root may have MOVED (a promote) or "
            "been lost.\n\n"
            f"- source_id: {finding.get('source_id')}\n"
            f"- execution_id: {finding.get('execution_id')}\n"
            f"- triggered_at: {finding.get('triggered_at')}\n"
            f"- artifact root: {finding.get('results_dir')}\n\n"
            "Surface-only: the daemon authored no stub, did not "
            "self-heal the root and did not re-fire the chain."
        ),
        tags=list(BOARD_TAGS_ROOT_EMPTY),
        dedup_tag="results-root-empty",
    )
    return finding


__all__ = [
    "BOARD_AUTHOR",
    "BOARD_TAGS",
    "BOARD_TITLE",
    "BOARD_TAGS_ROOT_EMPTY",
    "BOARD_TITLE_ROOT_EMPTY",
    "CHAIN_MODE",
    "FINDING_KIND_REPORTLESS_WEEK",
    "FINDING_KIND_RESULTS_ROOT_EMPTY",
    "GuardOutcome",
    "MaintenanceSeedError",
    "RESULTS_RELATIVE_DIR",
    "SEED_NAME",
    "SEED_SOURCE_ID",
    "SEED_SOURCE_TYPE",
    "WEEKLY_RUN_MESSAGE",
    "build_chain_config",
    "detect_reportless_week",
    "detect_results_root_empty",
    "ensure_maintenance_results_dir",
    "maintenance_results_dir",
    "refresh_chain_keys",
    "resolve_live_install_root",
    "seed_central_maintenance_schedule",
]
