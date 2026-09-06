"""SQLModel-based Instance Repository implementation."""

from __future__ import annotations

import json
import logging
import os
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, case, delete as sql_delete, func, literal, not_, or_, String, text
from sqlalchemy import cast as sa_cast
from sqlalchemy.engine import Engine
from sqlalchemy.sql.elements import TextClause
from sqlmodel import Session as SQLModelSession, select, col

from .models import Instance, InstanceHierarchy, InstanceStatus
from daemon.repositories.task.models import Task
from daemon.repositories.event.models import Event
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.job_queue.models import JobItem, JobLock
from daemon.repositories.job_queue.watcher_models import JobWatcher
from daemon.repositories.dependency_bus.models import DependencyWatcher
from daemon.repositories.source.models import InstanceMapping
from daemon.repositories.instance_ui_prefs.models import InstanceUiPrefs

logger = logging.getLogger(__name__)

# Keep in sync with frontend: frontend/src/app/services/instance.service.ts (KB_AGENT_IDS)
KB_AGENT_IDS = frozenset(["experiencer", "kb-importer", "kb-writer", "blueprinter"])

# Safety limit for tree traversal — prevents infinite loops from circular references
_MAX_TRAVERSAL_DEPTH = 256

# Safety cap on descendants loaded per page during root-based pagination.
# Prevents pathological trees (huge fan-out, accidental cycles) from blowing
# up response size / DB latency. Triggers a truncation warning when hit.
MAX_DESCENDANTS_PER_PAGE = 1000

# Kill-switch wrapper state (P1, AF1 governance; removal ticket FT-004).
# When ``ENSEMBLE_CASCADE_LINEAGE=permanent`` (default), cascades enumerate
# the permanent lineage via ``get_tree_ids_permanent`` — sees descendants
# regardless of churn (completed / errored / revived mid-cascade). When
# ``hierarchy``, cascades fall back to the legacy ``instance_hierarchy``
# working set — useful only as a deploy-window escape hatch if P1's
# permanent enumeration surfaces a regression in production. Unknown
# values fall back to ``permanent`` with a WARN. The resolved mode is
# cached on first access so the wrapper is hot-path safe; ``boot_log_emitted``
# is set once the manager logs the resolved mode at startup.
_CASCADE_LINEAGE_ENV = "ENSEMBLE_CASCADE_LINEAGE"
_CASCADE_LINEAGE_MODE: str | None = None
_CASCADE_LINEAGE_BOOT_LOG_EMITTED: bool = False


# Governor Recursion Guard (2026-08-30) — kill-switch state.
# Mirrors the ``ENSEMBLE_CASCADE_LINEAGE`` wrapper above: env const →
# resolved-once + cached → boot INFO log. The actual guard logic lives
# inside ``InstanceLifecycleService.spawn_instance`` (see items 1a.1–1a.5
# in `.agents/shared/planning/governor-recursion-guard/plan-overview.md`
# if present; otherwise see the rule block at the top of that method).
# Restart-required semantics (C4): flipping the env mid-flight does NOT
# take effect until the daemon restarts.
_GOVERNOR_RECURSION_GUARD_ENV = "LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED"
_GOVERNOR_RECURSION_GUARD_ENABLED: bool | None = None
_GOVERNOR_RECURSION_GUARD_BOOT_LOG_EMITTED: bool = False


def _resolve_governor_recursion_guard_enabled() -> bool:
    """Resolve and cache the governor recursion guard kill-switch.

    Returns:
        ``True`` when the guard is enabled (default — matches the locked
        design decision "guard default ON"), ``False`` when disabled via
        ``LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED=0``. The whitelist of
        truthy values is exactly ``("1", "true", "yes", "on", "")`` — the
        empty-string match mirrors the default-resolve path (``raw =
        os.environ.get(..., "1")`` always returns a string, so an unset
        env var passes the truthy check). Unknown values fall back to
        enabled with a WARN (one-shot, cached on first access).
    """
    global _GOVERNOR_RECURSION_GUARD_ENABLED
    if _GOVERNOR_RECURSION_GUARD_ENABLED is not None:
        return _GOVERNOR_RECURSION_GUARD_ENABLED
    raw = os.environ.get(_GOVERNOR_RECURSION_GUARD_ENV, "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        _GOVERNOR_RECURSION_GUARD_ENABLED = False
    elif raw in ("1", "true", "yes", "on", ""):
        _GOVERNOR_RECURSION_GUARD_ENABLED = True
    else:
        logger.warning(
            "%s=%r is not a recognized truthy/falsy value; "
            "falling back to enabled (default). Valid falsy: 0/false/no/off. "
            "Valid truthy: 1/true/yes/on.",
            _GOVERNOR_RECURSION_GUARD_ENV,
            raw,
        )
        _GOVERNOR_RECURSION_GUARD_ENABLED = True
    return _GOVERNOR_RECURSION_GUARD_ENABLED


def emit_governor_recursion_guard_boot_log() -> None:
    """Emit the one-time boot-time INFO log naming the resolved guard state.

    Called by ``InstanceManager.__init__`` after the instance repository
    is wired (mirrors ``emit_cascade_lineage_boot_log``). Restart-required
    semantics — same as the cascade-lineage wrapper. The actual guard
    logic is gated on ``_resolve_governor_recursion_guard_enabled()`` at
    every call site, so flipping the env mid-flight has no effect.
    """
    global _GOVERNOR_RECURSION_GUARD_BOOT_LOG_EMITTED
    if _GOVERNOR_RECURSION_GUARD_BOOT_LOG_EMITTED:
        return
    _GOVERNOR_RECURSION_GUARD_BOOT_LOG_EMITTED = True
    enabled = _resolve_governor_recursion_guard_enabled()
    logger.info(
        "Governor recursion guard resolved: %s (env %s=%s); "
        "refuses governor spawn when parent chain already contains "
        "≥ K governors (K from LIMITS_MAX_GOVERNOR_ANCESTORS, default 1). "
        "Restart required to flip.",
        "enabled" if enabled else "DISABLED",
        _GOVERNOR_RECURSION_GUARD_ENV,
        os.environ.get(_GOVERNOR_RECURSION_GUARD_ENV, "<unset>"),
    )


def _resolve_cascade_lineage_mode() -> str:
    """Resolve and cache the cascade-lineage mode from the kill-switch env.

    Returns:
        ``"permanent"`` (default) or ``"hierarchy"``. Unknown values fall
        back to ``"permanent"`` with a one-shot WARN (admin-tool-only
        escalation path; see FT-004 removal ticket).
    """
    global _CASCADE_LINEAGE_MODE
    if _CASCADE_LINEAGE_MODE is not None:
        return _CASCADE_LINEAGE_MODE
    raw = os.environ.get(_CASCADE_LINEAGE_ENV, "permanent").strip().lower()
    if raw in ("permanent", "hierarchy"):
        _CASCADE_LINEAGE_MODE = raw
    else:
        logger.warning(
            "%s=%r is not a recognized cascade-lineage mode; "
            "falling back to 'permanent'. Valid values: 'permanent', "
            "'hierarchy'. See FT-004 for the kill-switch removal ticket.",
            _CASCADE_LINEAGE_ENV,
            raw,
        )
        _CASCADE_LINEAGE_MODE = "permanent"
    return _CASCADE_LINEAGE_MODE


def emit_cascade_lineage_boot_log() -> None:
    """Emit the one-time boot-time INFO log naming the resolved mode.

    Called by ``InstanceManager.__init__`` after the instance repository
    is wired. Restart-required semantics (C4) — the kill-switch env is
    read on first ``get_cascade_tree_ids`` call, not on every call; if
    an operator flips the env mid-flight they need to restart for the
    flip to take effect. FT-004 documents the removal criterion (~+30
    days post-soak + V1/O1 verification outcomes).
    """
    global _CASCADE_LINEAGE_BOOT_LOG_EMITTED
    if _CASCADE_LINEAGE_BOOT_LOG_EMITTED:
        return
    _CASCADE_LINEAGE_BOOT_LOG_EMITTED = True
    mode = _resolve_cascade_lineage_mode()
    logger.info(
        "Cascade lineage mode resolved: %s (env %s=%s); "
        "permanent enumeration uses instances.parent_id, hierarchy "
        "fallback uses instance_hierarchy. Restart required to flip.",
        mode,
        _CASCADE_LINEAGE_ENV,
        os.environ.get(_CASCADE_LINEAGE_ENV, "<unset>"),
    )


def get_agent_name(agent_dir: str) -> str:
    """Derive agent name from agent directory path.

    Args:
        agent_dir: Path to the agent directory.

    Returns:
        Agent name in Title Case (e.g., "Coder", "Designer").
    """
    return Path(agent_dir).name.title()


class SQLModelInstanceRepository:
    """SQLModel-based Instance repository with hierarchy support."""
    
    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    # --------------------------------------------------------
    # INTERNAL HELPERS
    # --------------------------------------------------------

    def _load_children(self, db_session: SQLModelSession, instance_id: str) -> list[str]:
        """Load child instance IDs from the ``instance_hierarchy`` working set."""
        links = db_session.exec(
            select(InstanceHierarchy).where(InstanceHierarchy.parent_id == instance_id)
        ).all()
        return [link.child_id for link in links]

    def list_child_ids(self, instance_id: str) -> list[str]:
        """Return the child instance IDs of ``instance_id`` from the
        ``instance_hierarchy`` working set.

        Callers that need the permanent-record child list (including
        completed descendants) should use :meth:`get_children`, which
        walks ``instances.parent_id``.
        """
        with SQLModelSession(self.engine) as db_session:
            return self._load_children(db_session, instance_id)

    def list_child_ids_permanent(self, instance_id: str) -> list[str]:
        """Return the child instance IDs of ``instance_id`` from the
        permanent ``instances.parent_id`` record.

        Unlike :meth:`list_child_ids` (which reads the
        ``instance_hierarchy`` working set whose rows are deleted when a
        child completes), this walks ``instances.parent_id`` and
        therefore includes completed / terminated children. Use this for
        any display / nesting concern (the instance list UI, the
        ``children`` field of ``get_instance_info``) where dropping a
        child once it completes would orphan it from its parent's tree.

        ID-only variant of :meth:`get_children` (avoids hydrating full
        ``Instance`` rows when the caller only needs ids).
        """
        with SQLModelSession(self.engine) as db_session:
            rows = db_session.exec(
                select(Instance.instance_id).where(
                    Instance.parent_id == instance_id
                )
            ).all()
            return list(rows)

    def _enrich_instance(self, db_session: SQLModelSession, instance: Instance | None) -> Instance | None:
        """Hook for subclasses/tests to enrich a freshly-read instance. Default returns unchanged."""
        return instance

    def _enrich_instances(
        self, db_session: SQLModelSession, instances: list[Instance]
    ) -> list[Instance]:
        """Hook for subclasses/tests to enrich freshly-read instances. Default returns unchanged."""
        return instances

    def _build_search_condition(self, db_session: SQLModelSession, search: str | None):
        """Build a dialect-aware ``or_`` predicate for substring search.

        Matches the (escaped) ``search`` term against:
        * ``instance_metadata.title`` (JSONB on PostgreSQL, JSON on SQLite)
        * ``instance_metadata.initiative_message`` (JSONB on PostgreSQL, JSON on SQLite)
        * ``agent_name`` (column)
        * ``agent_id`` (column)

        Returns ``None`` when ``search`` is falsy (no filter to apply).

        The ``%`` and ``_`` wildcards in user input are escaped to literals
        via a backslash so a search like ``50%`` doesn't accidentally match
        arbitrary text. ``escape="\\"`` is the LIKE escape character and is
        required on both SQLite and PostgreSQL for the backslash to be
        interpreted correctly.
        """
        if not search:
            return None

        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        search_term = f"%{escaped}%"

        # Dialect-aware title expression: PG casts the JSONB-extracted text
        # value to VARCHAR; SQLite uses json_extract and casts to TEXT.
        # The cast lets ILIKE behave like a string compare (rather than JSON
        # comparator semantics) on both backends.
        is_postgres = db_session.bind.dialect.name == "postgresql"
        if is_postgres:
            title_expr = sa_cast(Instance.instance_metadata["title"], String)
            initiative_expr = sa_cast(Instance.instance_metadata["initiative_message"], String)
        else:
            title_expr = sa_cast(func.json_extract(Instance.instance_metadata, "$.title"), String)
            initiative_expr = sa_cast(func.json_extract(Instance.instance_metadata, "$.initiative_message"), String)

        return or_(
            title_expr.ilike(search_term, escape="\\"),
            initiative_expr.ilike(search_term, escape="\\"),
            Instance.agent_name.ilike(search_term, escape="\\"),
            Instance.agent_id.ilike(search_term, escape="\\"),
        )

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def create(
        self,
        instance_id: str,
        agent_id: str,
        agent_dir: str,
        parent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "idle",
        project_id: str | None = None,
    ) -> Instance:
        """Create a new instance.
        
        Args:
            instance_id: Unique instance identifier.
            agent_id: Agent ID (e.g., 'developer').
            agent_dir: Path to the agent directory.
            parent_id: Optional parent instance ID for hierarchical instances.
            metadata: Optional metadata dictionary.
            status: Instance status (default: "idle").
            project_id: Optional project ID for project context.
            
        Returns:
            Created Instance object.
        """
        with SQLModelSession(self.engine) as db_session:
            agent_name = get_agent_name(agent_dir)
            now = datetime.now(timezone.utc).isoformat()
            
            instance = Instance(
                instance_id=instance_id,
                agent_id=agent_id,
                agent_dir=agent_dir,
                agent_name=agent_name,
                parent_id=parent_id,
                status=status,
                instance_metadata=metadata or {},
                created_at=now,
                updated_at=now,
                project_id=project_id,
            )

            db_session.add(instance)
            
            # Add to hierarchy if parent_id is provided
            if parent_id is not None:
                hierarchy_link = InstanceHierarchy(
                    parent_id=parent_id,
                    child_id=instance_id,
                    created_at=now,
                )
                db_session.add(hierarchy_link)
            
            db_session.commit()
            db_session.refresh(instance)

            return self._enrich_instance(db_session, instance)

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get(self, instance_id: str) -> Instance | None:
        """Get an instance by ID."""
        with SQLModelSession(self.engine) as db_session:
            instance = db_session.get(Instance, instance_id)
            return self._enrich_instance(db_session, instance)

    def get_by_agent_id(self, agent_id: str) -> list[Instance]:
        """Get all instances for a given agent ID.
        
        Args:
            agent_id: The agent identifier (e.g., 'developer', 'leader').
            
        Returns:
            List of Instance objects for the specified agent.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(Instance).where(Instance.agent_id == agent_id)
            instances = list(db_session.exec(stmt))
            return self._enrich_instances(db_session, instances)

    def get_by_agent_dir(self, agent_dir: str) -> list[Instance]:
        """Get all instances for a given agent directory.
        
        .. deprecated::
            Use :meth:`get_by_agent_id` instead. agent_id is the canonical
            identifier; agent_dir is derived and may change.
        """
        warnings.warn(
            "get_by_agent_dir() is deprecated, use get_by_agent_id() instead",
            DeprecationWarning,
            stacklevel=2,
        )
        with SQLModelSession(self.engine) as db_session:
            stmt = select(Instance).where(Instance.agent_dir == agent_dir)
            instances = list(db_session.exec(stmt))
            return self._enrich_instances(db_session, instances)

    def get_children(self, instance_id: str) -> list[Instance]:
        """Get all child instances of an instance."""
        with SQLModelSession(self.engine) as db_session:
            stmt = select(Instance).where(Instance.parent_id == instance_id)
            instances = list(db_session.exec(stmt))
            return self._enrich_instances(db_session, instances)

    def get_parent(self, instance_id: str) -> Instance | None:
        """Get the parent instance of an instance."""
        with SQLModelSession(self.engine) as db_session:
            instance = db_session.get(Instance, instance_id)
            if instance is None or instance.parent_id is None:
                return None
        return self.get(instance.parent_id)

    def count_children(self, parent_id: str) -> int:
        """Count direct children of an instance from the hierarchy table.
        
        Args:
            parent_id: The parent instance ID.
            
        Returns:
            Number of direct children.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(func.count()).select_from(InstanceHierarchy).where(
                InstanceHierarchy.parent_id == parent_id
            )
            return db_session.exec(stmt).one()

    # --------------------------------------------------------
    # TREE TRAVERSAL
    # --------------------------------------------------------

    def get_tree_root_id(self, instance_id: str) -> str | None:
        """Get the root instance ID by traversing up the parent chain.
        
        Args:
            instance_id: Starting instance ID.
            
        Returns:
            Root instance ID, or None if instance not found.
        """
        with SQLModelSession(self.engine) as db_session:
            current_id = instance_id
            for _ in range(_MAX_TRAVERSAL_DEPTH):
                instance = db_session.get(Instance, current_id)
                if instance is None:
                    return None
                if instance.parent_id is None:
                    return current_id
                current_id = instance.parent_id
            return None

    def get_tree_ids(self, root_id: str) -> list[str]:
        """Get all instance IDs in the tree starting from root_id (BFS).

        Walks the transient ``instance_hierarchy`` working set. NOTE: this
        enumeration is **transient by construction** — hierarchy rows are
        deleted on child completion (``child_reports.py``, ``error_reporting.py``)
        and on ``_terminate_instance_db_sync`` (``instance_lifecycle.py:3331``),
        and they are NEVER re-inserted on revive (``instance_messaging.py:1510-1530``
        transitions terminal→RUNNING without writing to ``instance_hierarchy``).
        As a result, this helper silently misses descendants that completed,
        errored, or were revived during churn — which is the cause of B1 (pause
        does not cascade DOWN) and B4 (terminate-root misses live children).

        For cascades that must see the **complete permanent lineage** regardless
        of churn, use :meth:`get_tree_ids_permanent` (or, in production, the
        kill-switch wrapper :meth:`get_cascade_tree_ids`). This helper is
        retained during staged deprecation (leader D5; see plan
        ``phase1-plan.md`` AF1 resolution + removal ticket FT-004); the
        "active working set" framing in earlier docstrings was incorrect.

        Args:
            root_id: Root instance ID to start traversal.

        Returns:
            List of instance IDs including root_id and all descendants still
            represented in ``instance_hierarchy``. Empty when the root is not
            in the DB.
        """
        with SQLModelSession(self.engine) as db_session:
            # Check root exists
            root = db_session.get(Instance, root_id)
            if root is None:
                return []

            result = [root_id]
            queue = [root_id]
            for _ in range(_MAX_TRAVERSAL_DEPTH):
                if not queue:
                    break
                current_id = queue.pop(0)
                child_ids = list(db_session.exec(
                    select(InstanceHierarchy.child_id).where(InstanceHierarchy.parent_id == current_id)
                ))
                for child_id in child_ids:
                    if child_id not in result:
                        result.append(child_id)
                        queue.append(child_id)
            return result

    def get_tree_ids_permanent(self, root_id: str) -> list[str]:
        """Complete permanent-lineage tree enumeration (root + ALL descendants).

        Walks ``instances.parent_id`` (permanent — survives completion, error,
        terminate, revive) rather than the ``instance_hierarchy`` working set
        (rows deleted at child_reports.py:922 / error_reporting.py:233 /
        child_reports.py:2872 / instance_lifecycle.py:3331, plus the 5th
        site ``_terminate_instance_db_sync`` at
        ``instance_lifecycle.py:3324-3333``). Use for ANY cascade that must
        see the whole tree regardless of churn.

        NO status filter by design — callers classify AFTER enumeration
        (pause skips PAUSED/TERMINAL at instance_lifecycle.py:2094-2102;
        terminate restructures to enumerate-first per T3).

        Traversal cap: ``_MAX_TRAVERSAL_DEPTH = 256`` (``repository.py:33``).
        Trees at or beyond the cap are **WARN-logged at depth 256** (one-time
        ``logger.warning`` emitted when the ``for...else`` clause fires with
        a non-empty ``frontier``); the visited set itself is the truthful
        result. Callers that suspect deeper trees must raise the cap
        (rare; admin tool only). Cycle self-parenting is guarded by the
        depth cap + visited set.
        """
        with SQLModelSession(self.engine) as db_session:
            # Root must exist in the permanent record.
            root = db_session.get(Instance, root_id)
            if root is None:
                return []

            visited: list[str] = [root_id]
            seen: set[str] = {root_id}
            frontier: list[str] = [root_id]
            for _ in range(_MAX_TRAVERSAL_DEPTH):
                if not frontier:
                    break
                next_frontier: list[str] = []
                for current_id in frontier:
                    rows = db_session.exec(
                        select(Instance.instance_id).where(
                            Instance.parent_id == current_id
                        )
                    ).all()
                    for child_id in rows:
                        if child_id not in seen:
                            seen.add(child_id)
                            visited.append(child_id)
                            next_frontier.append(child_id)
                frontier = next_frontier
            else:
                # `for...else` fires only when the loop ran to its cap
                # without breaking. If `frontier` is still non-empty,
                # the tree was truncated — log a one-time warning so the
                # operator notices (admin-tool-only escalation path;
                # production trees are well under 256).
                if frontier:
                    logger.warning(
                        "get_tree_ids_permanent(%r): traversal depth cap "
                        "_MAX_TRAVERSAL_DEPTH=%d reached with %d unvisited "
                        "frontier nodes; tree truncated. Admin tool only — "
                        "raise the cap if your tree legitimately exceeds it.",
                        root_id,
                        _MAX_TRAVERSAL_DEPTH,
                        len(frontier),
                    )
            return visited

    def get_max_last_activity_in_instances(
        self, instance_ids: list[str]
    ) -> datetime | None:
        """MAX(last_activity_at) across a SET of instances (tree aggregate).

        f1-misfire batch (incident 2026-08-31, JobItem 69a34b35):
        the Pattern-f1 subtree-alive guard's leg 2. The per-row
        signal provably fails — ``last_activity_at`` FREEZES on a
        waiting_children parent while a descendant streams mid-LLM —
        so the guard must aggregate MAX over the whole permanent
        lineage (root + descendants via ``get_tree_ids_permanent``).

        NULL ``last_activity_at`` rows are ignored by the MAX
        aggregate. Returns ``None`` when no member has ever been
        active (or the set is empty) — callers treat None as "no
        activity signal", NOT as "recently active".

        Args:
            instance_ids: Instance IDs to aggregate over (the lineage
                tree).

        Returns:
            The most recent ``last_activity_at`` in the set, or None.
        """
        if not instance_ids:
            return None
        with SQLModelSession(self.engine) as db_session:
            stmt = select(
                func.max(Instance.last_activity_at)
            ).where(Instance.instance_id.in_(instance_ids))
            return db_session.exec(stmt).one()

    def get_cascade_tree_ids(self, root_id: str) -> list[str]:
        """Deploy-window escape hatch — pick the cascade-lineage source.

        All cascade call sites (pause, terminate, hard-delete snapshot,
        resume, maintenance pin-protection) call THIS method instead of
        ``get_tree_ids`` directly so the operator can flip between
        permanent lineage and the legacy ``instance_hierarchy`` working
        set without a code revert.

        Mode is read from the ``ENSEMBLE_CASCADE_LINEAGE`` env var
        (default ``permanent``); see :func:`_resolve_cascade_lineage_mode`
        for the full resolution rules and :func:`emit_cascade_lineage_boot_log`
        for the one-time boot-time INFO log. **Restart-required semantics**
        (C4): the env is cached on first call, so a mid-flight flip does
        NOT take effect until the daemon restarts. Flipping after churn
        re-exposes the B1/B4 defects (pause does not cascade DOWN to
        churned descendants; terminate-root misses live children).

        Removal criterion: ~+30 days post-soak + V1/O1 verification
        outcomes (ticket **FT-004**, filed in ``decisions.md:46``).

        Args:
            root_id: Root instance ID to start traversal.

        Returns:
            List of instance IDs — see :meth:`get_tree_ids_permanent`
            (``permanent`` mode) or :meth:`get_tree_ids` (``hierarchy`` mode).
        """
        mode = _resolve_cascade_lineage_mode()
        if mode == "hierarchy":
            return self.get_tree_ids(root_id)
        return self.get_tree_ids_permanent(root_id)

    def get_ancestor_ids(self, instance_id: str) -> list[str]:
        """Get all ancestor instance IDs (parent, grandparent, ..., up to root).
        
        Args:
            instance_id: Starting instance ID.
            
        Returns:
            List of ancestor IDs from parent to root (inclusive). Empty list if no parent.
        """
        with SQLModelSession(self.engine) as db_session:
            ancestors = []
            current_id = instance_id
            for _ in range(_MAX_TRAVERSAL_DEPTH):
                instance = db_session.get(Instance, current_id)
                if instance is None or instance.parent_id is None:
                    break
                ancestors.append(instance.parent_id)
                current_id = instance.parent_id
            return ancestors

    def get_agent_ids_for(self, instance_ids: list[str]) -> dict[str, str | None]:
        """Resolve ``agent_id`` for each instance id in one query.

        Governor Recursion Guard (2026-08-30): used by the lifecycle-layer
        chain walk to count how many ancestors (including the prospective
        parent itself) carry ``agent_id == "governor"`` — without N round
        trips. Missing ids map to ``None``; the caller treats ``None`` as
        "not a governor" because we cannot confirm it. Empty input returns
        an empty dict (no DB access).

        Args:
            instance_ids: Instance ids to look up. Order is not preserved;
                the caller already has the chain list separately.

        Returns:
            Dict ``{instance_id: agent_id_or_None}`` — every input id is
            present in the output. Missing rows map to ``None`` so the
            caller can sum without KeyError risk.
        """
        if not instance_ids:
            return {}
        with SQLModelSession(self.engine) as db_session:
            stmt = select(Instance.instance_id, Instance.agent_id).where(
                Instance.instance_id.in_(set(instance_ids))
            )
            rows = db_session.exec(stmt).all()
            found = {iid: aid for iid, aid in rows}
        # Backfill missing ids with None — caller treats None as
        # "not a governor".
        return {iid: found.get(iid) for iid in instance_ids}

    # --------------------------------------------------------
    # LIST
    # --------------------------------------------------------

    def list(
        self,
        status: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        exclude_kb: bool = True,
        include_descendants: bool = False,
        search: str | None = None,
    ) -> tuple[list[Instance], int]:
        """List instances with optional root-based pagination and full tree loading.

        Two modes are supported:

        1. **Flat pagination (default)**: ``include_descendants=False``. Returns a
           simple paginated list of all instances matching the filters, ordered
           by ``created_at DESC``. Use this for callers that expect a flat
           list (manager cache cleanup, fuzzy match, project deletion,
           maintenance, agent tools).

        2. **Root-based pagination with BFS descendant loading**: ``include_descendants=True``.
           Only root instances (``parent_id`` IS NULL or empty) are counted and
           paginated. ALL descendants of each root in the current page are
           loaded via iterative BFS and included in the flat result list.

        Descendant loading uses ``instances.parent_id`` (the permanent record), NOT
        ``instance_hierarchy`` (which is a working set that deletes entries when
        children complete). Traversal is iterative BFS — one query per tree depth
        level, so total queries = number of depth levels (not number of instances).
        A ``seen_ids`` set guards against duplicates in case of circular
        ``parent_id`` references.

        Filtering semantics for ``include_descendants=True``:

        * ``status`` is applied to the **root query only**. Once a root is
          selected for the current page, ALL of its descendants are loaded —
          regardless of status. This keeps descendant sets complete.
        * ``project_id`` is applied to both the root query and the BFS child
          queries (defense-in-depth; descendants should inherit project from
          their root, but the BFS filter prevents leakage in case of corrupt
          ``parent_id`` references).
        * ``exclude_kb`` is applied to the **root query** for pagination and
          then **post-filtered** in Python on the assembled descendant list.
          The BFS itself does NOT exclude KB agents mid-traversal — that would
          orphan non-KB grandchildren whose KB parent's ID never enters
          ``next_level_ids``. KB agents are still traversed *through* (so their
          non-KB children are reachable) and then stripped from the final
          result.
        * ``search`` is applied to the root count, root query, and the BFS
          child query (defense-in-depth, mirroring ``project_id``). Like
          ``%`` / ``_`` wildcards in user input are escaped to literals.

        Args:
            status: Optional status filter. For ``include_descendants=True``,
                applied to root selection only; descendants are returned
                regardless of status. For flat pagination, applied to all rows.
            project_id: Optional project ID filter (applied to both roots and
                descendants when ``include_descendants=True``).
            limit: Maximum number of root instances to return.
            offset: Number of root instances to skip.
            exclude_kb: Exclude KB-related instances (experiencer, kb-importer, kb-writer)
                when True (default: True).
            include_descendants: When False (default), return a flat paginated
                list of all matching instances. When True, paginate by root and
                BFS-load all descendants of each root in the current page.
            search: Optional case-insensitive substring filter. Matches the
                escaped term against ``instance_metadata.title``,
                ``instance_metadata.initiative_message``, ``agent_name``,
                and ``agent_id``. ``%`` and ``_`` in the search term are treated
                as literals.

        Returns:
            Tuple of (flat list of instances, total count). In
            ``include_descendants=False`` mode, the total reflects all matching
            instances. In ``include_descendants=True`` mode, the total reflects
            only root instances (matching the pagination).
        """
        if not include_descendants:
            # ────────────────────────────────────────────────────────────────
            # Flat pagination path: original behavior for all non-API callers.
            # ────────────────────────────────────────────────────────────────
            with SQLModelSession(self.engine) as db_session:
                count_stmt = select(func.count()).select_from(Instance)
                stmt = select(Instance)
                if status:
                    count_stmt = count_stmt.where(Instance.status == status)
                    stmt = stmt.where(Instance.status == status)
                if project_id is not None:
                    count_stmt = count_stmt.where(Instance.project_id == project_id)
                    stmt = stmt.where(Instance.project_id == project_id)
                if exclude_kb:
                    count_stmt = count_stmt.where(Instance.agent_id.not_in(KB_AGENT_IDS))
                    stmt = stmt.where(Instance.agent_id.not_in(KB_AGENT_IDS))
                search_cond = self._build_search_condition(db_session, search)
                if search_cond is not None:
                    count_stmt = count_stmt.where(search_cond)
                    stmt = stmt.where(search_cond)

                total = db_session.exec(count_stmt).one()

                # NOTE: only pinned=True floats to the top. An explicit pinned=False and a
                # never-pinned (NULL) row are treated equivalently as "unpinned" (both map to
                # tier 0 via the CASE), then tiebreak on created_at DESC then instance_id.
                stmt = (
                    stmt.outerjoin(
                        InstanceUiPrefs,
                        col(Instance.instance_id) == col(InstanceUiPrefs.instance_id),
                    )
                    .order_by(
                        case(
                            (col(InstanceUiPrefs.pinned).is_(True), literal(1)),
                            else_=literal(0),
                        ).desc(),
                        col(InstanceUiPrefs.pinned_at).desc().nulls_last(),
                        col(Instance.created_at).desc(),
                        col(Instance.instance_id).asc(),  # stable final tiebreaker
                    )
                    .offset(offset)
                    .limit(limit)
                )
                instances = list(db_session.exec(stmt))
                return self._enrich_instances(db_session, instances), total

        # ────────────────────────────────────────────────────────────────
        # Root-based pagination + BFS descendant loading (API path).
        # ────────────────────────────────────────────────────────────────
        with SQLModelSession(self.engine) as db_session:
            # Build the search condition once and reuse across root count,
            # root query, and BFS child queries.
            search_cond = self._build_search_condition(db_session, search)

            # 1. Count root instances only (parent_id IS NULL OR empty).
            count_stmt = select(func.count()).select_from(Instance).where(
                (Instance.parent_id.is_(None)) | (Instance.parent_id == "")
            )
            if status:
                count_stmt = count_stmt.where(Instance.status == status)
            if project_id is not None:
                count_stmt = count_stmt.where(Instance.project_id == project_id)
            if exclude_kb:
                count_stmt = count_stmt.where(Instance.agent_id.not_in(KB_AGENT_IDS))
            if search_cond is not None:
                count_stmt = count_stmt.where(search_cond)
            total = db_session.exec(count_stmt).one()

            # 2. Paginate root instances (ORDER BY created_at DESC).
            root_stmt = select(Instance).where(
                (Instance.parent_id.is_(None)) | (Instance.parent_id == "")
            )
            if status:
                root_stmt = root_stmt.where(Instance.status == status)
            if project_id is not None:
                root_stmt = root_stmt.where(Instance.project_id == project_id)
            if exclude_kb:
                root_stmt = root_stmt.where(Instance.agent_id.not_in(KB_AGENT_IDS))
            if search_cond is not None:
                root_stmt = root_stmt.where(search_cond)

            # NOTE: only pinned=True floats to the top. An explicit pinned=False and a
            # never-pinned (NULL) row are treated equivalently as "unpinned" (both map to
            # tier 0 via the CASE), then tiebreak on created_at DESC then instance_id.
            root_stmt = (
                root_stmt.outerjoin(
                    InstanceUiPrefs,
                    col(Instance.instance_id) == col(InstanceUiPrefs.instance_id),
                )
                .order_by(
                    case(
                        (col(InstanceUiPrefs.pinned).is_(True), literal(1)),
                        else_=literal(0),
                    ).desc(),
                    col(InstanceUiPrefs.pinned_at).desc().nulls_last(),
                    col(Instance.created_at).desc(),
                    col(Instance.instance_id).asc(),  # stable final tiebreaker
                )
                .offset(offset)
                .limit(limit)
            )
            roots = list(db_session.exec(root_stmt))

            if not roots:
                return [], total

            # 3. Iterative BFS to load ALL descendants of the paginated roots.
            #    Uses instances.parent_id (permanent record), not the working-set
            #    instance_hierarchy table.
            #    Each depth level is a single query: WHERE parent_id IN (...).
            #    ``seen_ids`` guards against duplicates from circular parent_id refs.
            #
            #    IMPORTANT: We do NOT apply exclude_kb or status to the BFS
            #    child query. Applying exclude_kb mid-traversal would orphan
            #    non-KB grandchildren (a KB parent's ID would never enter
            #    next_level_ids, so its children would never be queried). We
            #    still traverse through KB agents to find their non-KB
            #    descendants, then strip KB agents in a post-filter below.
            #    Similarly, the status filter only governs WHICH roots are
            #    paginated; once a root is in the page, all of its descendants
            #    are loaded regardless of status.
            all_instances: list[Instance] = list(roots)
            seen_ids: set[str] = {r.instance_id for r in roots}
            current_level_ids: list[str] = [r.instance_id for r in roots]
            hit_depth_limit = False

            for _ in range(_MAX_TRAVERSAL_DEPTH):
                if not current_level_ids:
                    break

                # Only project_id and search are applied mid-traversal (defense-in-depth).
                # exclude_kb and status are handled outside the loop.
                child_stmt = select(Instance).where(
                    col(Instance.parent_id).in_(current_level_ids)
                )
                if project_id is not None:
                    child_stmt = child_stmt.where(Instance.project_id == project_id)
                if search_cond is not None:
                    child_stmt = child_stmt.where(search_cond)

                children = list(db_session.exec(child_stmt))
                if not children:
                    break

                # Dedup: skip children whose IDs were already encountered at an
                # earlier level (defends against circular parent_id references).
                new_children: list[Instance] = []
                next_level_ids: list[str] = []
                for child in children:
                    if child.instance_id in seen_ids:
                        continue
                    seen_ids.add(child.instance_id)
                    new_children.append(child)
                    next_level_ids.append(child.instance_id)

                all_instances.extend(new_children)

                if not new_children:
                    # All children were duplicates — no new IDs to traverse.
                    break

                # Safety cap: pathological trees (huge fan-out, accidental
                # cycles, very deep hierarchies) must not blow up response
                # size or DB latency. Truncate with a warning.
                if len(all_instances) >= MAX_DESCENDANTS_PER_PAGE:
                    logger.warning(
                        "Descendant limit (%d) reached for roots at offset %d; "
                        "truncating tree load",
                        MAX_DESCENDANTS_PER_PAGE,
                        offset,
                    )
                    break

                current_level_ids = next_level_ids
            else:
                # Loop exhausted ``_MAX_TRAVERSAL_DEPTH`` iterations without
                # ``current_level_ids`` becoming empty. Possible data corruption
                # (very deep tree or circular refs not caught by ``seen_ids``).
                hit_depth_limit = True

            if hit_depth_limit and current_level_ids:
                logger.warning(
                    "BFS descendant loading hit depth limit (%d) with %d unprocessed IDs; "
                    "possible data corruption or excessively deep tree.",
                    _MAX_TRAVERSAL_DEPTH,
                    len(current_level_ids),
                )

            # Post-filter: strip KB agents from the assembled descendant list.
            # Roots were already KB-filtered by the root query above; this
            # only affects descendants. We do NOT post-filter by status —
            # all descendants of the selected roots are returned regardless
            # of their status (see docstring).
            if exclude_kb:
                all_instances = [
                    inst for inst in all_instances if inst.agent_id not in KB_AGENT_IDS
                ]

            return self._enrich_instances(db_session, all_instances), total

    def list_by_parent(self, parent_id: str) -> list[Instance]:
        """List all child instances of a parent."""
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                select(Instance)
                .join(InstanceHierarchy, InstanceHierarchy.child_id == Instance.instance_id)
                .where(InstanceHierarchy.parent_id == parent_id)
            )
            instances = list(db_session.exec(stmt))
            return self._enrich_instances(db_session, instances)

    def list_parents_with_active_children(self) -> list[str]:
        """Return all ``parent_id`` values that currently have at least one
        active child in the ``instance_hierarchy`` working set.

        A parent with at least one row in this table has at least one
        outstanding child whose completion report is still expected
        (entries are added on spawn and deleted when children complete).
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(InstanceHierarchy.parent_id).distinct()
            return list(db_session.exec(stmt))

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    def update(self, instance_id: str, **updates) -> Instance | None:
        """Update an instance's fields.

        Defense-in-depth guard: callers must NOT pass ``status=`` here.
        Status changes are routed through :meth:`transition_status_if`
        so the SQL-level ``WHERE status IN (:allowed_from)`` predicate
        prevents concurrent clobbering of terminal statuses. Bypassing
        that path by writing ``status`` directly here would reintroduce
        the very race the atomic-transition fix was designed to
        eliminate.

        ``instance_metadata`` is also rejected because writes to the
        JSON column via plain ORM setattr are a read-modify-write at
        the column level (the ORM replaces the whole JSON blob) and
        clobber concurrent writers. Use :meth:`set_metadata` /
        :meth:`delete_metadata` (dialect-aware JSONB / json_set
        UPDATE) instead.

        Args:
            instance_id: Instance identifier.
            **updates: Fields to update. ``status`` and
                ``instance_metadata`` are rejected — use
                :meth:`transition_status_if` for status changes and
                :meth:`set_metadata` / :meth:`delete_metadata` for
                metadata edits.

        Returns:
            Updated Instance if found, None otherwise.

        Raises:
            ValueError: If ``status`` or ``instance_metadata`` is
                supplied via ``updates``.
        """
        if "status" in updates:
            raise ValueError(
                "Use transition_status_if for status changes "
                "(see InstanceRepository.transition_status_if)"
            )
        if "instance_metadata" in updates:
            raise ValueError(
                "Use set_metadata / delete_metadata for metadata edits "
                "(see InstanceRepository.set_metadata / delete_metadata)"
            )

        with SQLModelSession(self.engine) as db_session:
            instance = db_session.get(Instance, instance_id)
            if instance is None:
                return None

            for key, value in updates.items():
                if hasattr(instance, key):
                    setattr(instance, key, value)

            instance.updated_at = datetime.now(timezone.utc).isoformat()
            db_session.commit()
            db_session.refresh(instance)

            return self._enrich_instance(db_session, instance)

    def update_status(self, instance_id: str, status: str) -> Instance | None:
        """Update instance status."""
        return self.update(instance_id, status=status)

    def transition_status_if(
        self,
        instance_id: str,
        new_status: str,
        allowed_from: tuple[str, ...],
    ) -> Instance | None:
        """Atomically transition an instance's status only if the current
        status is in ``allowed_from``. Returns the updated instance, or
        ``None`` if the row was not found OR the precondition was not
        satisfied (no clobber of a concurrent terminal-state write).

        Use this for status transitions that must not overwrite a
        concurrent error/pause/terminate from another path.
        """
        from sqlmodel import update as sqlmodel_update
        with SQLModelSession(self.engine) as db_session:
            stmt = (
                sqlmodel_update(Instance)
                .where(Instance.instance_id == instance_id)
                .where(col(Instance.status).in_(list(allowed_from)))
                .values(
                    status=new_status,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            result = db_session.exec(stmt)
            db_session.commit()
            if result.rowcount == 0:
                # Either instance not found, or current status is not in
                # allowed_from (e.g. a concurrent writer set it to ERROR
                # or PAUSED between the caller's read and this update).
                return None
            instance = db_session.get(Instance, instance_id)
            if instance is None:
                return None
            return self._enrich_instance(db_session, instance)

    def update_title(self, instance_id: str, title: str) -> Instance | None:
        """Update instance title in instance_metadata.

        Delegates to :meth:`set_metadata` so the title write uses the same
        dialect-aware atomic SQL path (single-statement UPDATE with
        ``jsonb_set`` on PostgreSQL / ``json_set`` on SQLite). This avoids
        the read-modify-write race where a concurrent ``set_metadata`` on
        a different key would be silently overwritten.
        """
        return self.set_metadata(instance_id, "title", title)

    # --------------------------------------------------------
    # LEADER COMPLETION ATTESTATION — Phase 3 ledger methods
    # --------------------------------------------------------
    #
    # The four ledger methods that back the gate node's
    # ``denied_count_getter`` plus the four reset triggers per leader
    # rulings 1+2:
    #
    #   * (1) attested allow (canonical ``Decision.ALLOWED`` under enforce)
    #   * (2) ``terminal_after_bound`` finalization (clears BOTH columns)
    #   * (3) revive-from-COMPLETED via a NEW top-level user/mission
    #         message (clears BOTH columns — wired in
    #         ``daemon/services/instance_messaging.py:_prepare_enqueued_
    #         message``, the same-transaction status=RUNNING site)
    #   * (4) instance creation (column default 0; no method needed —
    #         the Instance row's default value is the trigger)
    #
    # ``allowed_legitimate_pending_wakeup`` (R2 un-attested allow) MUST
    # NOT reset the counter — that non-reset IS the loop protection.
    #
    # O4 (Pause-mid-gate double-increment): ``increment_attestation_
    # denied_count`` is keyed by ``(instance_id, denial_epoch)`` — the
    # epoch is derived deterministically from checkpoint-stable state
    # material by the caller (the gate node) so a checkpoint re-run of
    # the node reproduces the SAME key. A pause-mid-gate resume that
    # replays the same deny MUST NOT double-increment.
    #
    # All four methods fail-OPEN at the call site (the gate node wraps
    # them in ``except Exception`` and degrades deny → allow + emits
    # ``leader_completion_gate_db_error`` per C3/AC-6.6). The methods
    # themselves raise on DB failure so the caller's ``except Exception``
    # sees the original exception class (SQLAlchemy OperationalError,
    # etc.) for the structured error log.
    #
    # The reset is a single UPDATE that clears BOTH columns in one
    # statement (leader ruling 2: ``completion_gate_escalated`` shares
    # the counter's per-mission lifecycle). Atomic at the row level —
    # no torn write under concurrent deny paths.

    def increment_attestation_denied_count(
        self,
        instance_id: str,
        denial_epoch: str,
    ) -> int:
        """O4 idempotent per-denial-epoch increment (Phase 3 task 3.3).

        Atomically increments ``attestation_denied_count`` for the
        instance, keyed by ``(instance_id, denial_epoch)`` — replaying
        the SAME deny (checkpoint re-run / pause-mid-gate resume) MUST
        NOT double-increment.

        Single-transaction guarantee (review must-fix 2): the ENTIRE
        operation — row lock, O4 dedup decision, epochs-array append,
        counter increment, commit — runs in ONE session/transaction.
        The predecessor ran TWO (a ``set_metadata`` self-commit followed
        by a second UPDATE), so a crash between them persisted the
        epochs array WITHOUT the counter bump — a permanently lost deny.

        Concurrency: ``Session.get(..., with_for_update=True)`` takes a
        row lock on PostgreSQL from the read through the commit, so a
        concurrent reset/increment/set_metadata UPDATE on the same row
        serializes BEHIND this commit (the read-modify-write lost-update
        window against a concurrent reset is closed). On SQLite the
        clause is a no-op (dialect renders no ``FOR UPDATE``) and the
        database's single-writer lock provides the same serialization:
        a competing writer either waits or fails LOUD (``OperationalError``
        → the gate's C3 fail-open) instead of silently interleaving.
        No PG-only syntax exists on the SQLite path — the array is
        written through ORM column assignment, which serializes
        dialect-independently (the ``jsonb_set``/``json_set`` raw-SQL
        handling in :meth:`set_metadata` is NOT needed here precisely
        because the row lock makes the read-modify-write safe).

        Args:
            instance_id: Leader instance under evaluation.
            denial_epoch: Caller-supplied key identifying THIS deny
                (the gate node derives it deterministically from
                checkpoint-stable state material). Replays of the same
                key are no-ops.

        Returns:
            The post-increment counter value (the unchanged current
            value on an O4 replay). ``-1`` if the instance is missing
            (caller treats as DB error → fail-open). The ``-1``
            literal is the family's int-sentinel (see the
            missing-row sentinel convention note on
            :meth:`_update_attestation_columns`).

        Raises:
            Exception: any DB-level error (SQLAlchemy OperationalError
                etc.) propagates — the gate node wraps in
                ``except Exception`` per C3/AC-6.6 and degrades
                deny → allow + emits ``leader_completion_gate_db_error``.
        """
        with SQLModelSession(self.engine) as db_session:
            instance = db_session.get(
                Instance, instance_id, with_for_update=True
            )
            if instance is None:
                return -1
            # O4 idempotency — the dedup decision happens INSIDE the
            # locked transaction: read the seen-epochs JSON array from
            # ``instance_metadata``. A pre-existing epoch = no-op
            # (returns the current counter unchanged). First-seen epoch
            # = append + increment, both committed together.
            current_count = int(instance.attestation_denied_count or 0)
            existing = instance.instance_metadata.get(
                "attestation:denial_epochs"
            ) if instance.instance_metadata else None
            if existing is None:
                seen_epochs: list[str] = []
            elif isinstance(existing, list):
                seen_epochs = [str(e) for e in existing]
            else:
                # Corrupt legacy payload — reset (defensive; treat as
                # empty list to keep O4 semantics bounded). LOUD: a
                # corrupt array silently disabling dedup is observable.
                logger.warning(
                    "attestation_denied_count: corrupt "
                    "'attestation:denial_epochs' metadata (type=%s) for "
                    "instance=%s; resetting seen-epochs to [] — O4 dedup "
                    "will re-count until the next reset",
                    type(existing).__name__,
                    instance_id,
                )
                seen_epochs = []
            if denial_epoch in seen_epochs:
                # O4 replay — same deny, no double-increment.
                return current_count
            seen_epochs.append(denial_epoch)
            # ORM write of BOTH columns in the SAME transaction. The
            # metadata dict must be REASSIGNED (not mutated in place):
            # ``instance_metadata`` is a plain ``JSONBType`` column
            # without mutable tracking, so in-place key writes would not
            # be detected as a change.
            new_metadata = dict(instance.instance_metadata or {})
            new_metadata["attestation:denial_epochs"] = seen_epochs
            instance.instance_metadata = new_metadata
            instance.attestation_denied_count = current_count + 1
            instance.updated_at = datetime.now(timezone.utc).isoformat()
            db_session.commit()
            return current_count + 1

    def _update_attestation_columns(
        self, instance_id: str, **values: Any
    ) -> bool:
        """Shared UPDATE skeleton for the attestation ledger writes.

        One session, one ``UPDATE``: load the row, bail with the
        family's missing-row sentinel when absent, apply the caller's
        column values, commit.

        Missing-row sentinel convention (family-wide, deliberate — do
        NOT unify to a single literal): BOOL-returning writes return
        ``False`` when the row is missing; the INT-returning
        :meth:`increment_attestation_denied_count` returns ``-1``
        (``0`` is a legitimate post-O4-replay count, so the increment
        needs an out-of-band sentinel — the gate's ``< 0`` guard
        depends on it); the read accessor
        :meth:`get_attestation_denied_count` returns ``0`` by design
        (a fresh instance folds to an empty counter). The gate node
        consumes these via identity checks (``is False`` / ``is
        None``) and ``< 0`` — changing any literal would flip
        caller-visible behavior.

        Returns:
            ``True`` when the row was found and the UPDATE committed;
            ``False`` when the instance is missing.

        Raises:
            Exception: any DB-level error propagates — see fail-open
                wrapper note on
                :meth:`increment_attestation_denied_count`.
        """
        from sqlmodel import update as sqlmodel_update

        with SQLModelSession(self.engine) as db_session:
            instance = db_session.get(Instance, instance_id)
            if instance is None:
                return False
            stmt = (
                sqlmodel_update(Instance)
                .where(Instance.instance_id == instance_id)
                .values(**values)
            )
            db_session.exec(stmt)
            db_session.commit()
            return True

    def reset_attestation_denied_count(self, instance_id: str) -> bool:
        """Ruling-2 single reset op — clears BOTH columns (Phase 3 task 3.3).

        Sets ``attestation_denied_count = 0`` AND
        ``completion_gate_escalated = False`` in a single UPDATE. This
        is the canonical reset for:

        * (1) attested allow — wired in the gate node on
          ``Decision.ALLOWED`` under enforce;
        * (2) ``terminal_after_bound`` finalization — wired in the gate
          node on ``Decision.TERMINAL_AFTER_BOUND`` (the same op also
          sets the escalation flag via :meth:`set_completion_gate_
          escalated` BEFORE this reset lands — see the atomic variant
          below);
        * (3) revive-from-COMPLETED via a NEW top-level user/mission
          message — wired in
          ``daemon/services/instance_messaging.py:_prepare_enqueued_
          message`` at the same-transaction status=RUNNING site
          (only fires on ``is_terminal_revival`` AND ``msg_type ==
          HUMAN.value`` AND ``priority == 1`` — internal reports and
          agent-to-agent revives are NOT new episodes).

        Returns:
            ``True`` when the row was found and reset; ``False`` when
            the instance is missing (caller treats as DB error →
            fail-open).

        Raises:
            Exception: any DB-level error propagates — see fail-open
                wrapper note on :meth:`increment_attestation_denied_count`.
        """
        return self._update_attestation_columns(
            instance_id,
            attestation_denied_count=0,
            completion_gate_escalated=False,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def reset_attestation_ledger_with_escalation(
        self,
        instance_id: str,
    ) -> bool:
        """Atomic (2) terminal_after_bound finalization: set flag + reset.

        Same single transaction semantics as the ruling-2 reset — sets
        ``completion_gate_escalated = True`` AND
        ``attestation_denied_count = 0`` in one UPDATE so a concurrent
        read sees a consistent (flag=True, count=0) state. The
        gate node calls THIS for the ``terminal_after_bound`` decision
        path; the plain :meth:`reset_attestation_denied_count` is for
        the attested-allow and trigger-3 paths (where the flag is
        already False).

        Returns:
            ``True`` when the row was found and the atomic write
            landed; ``False`` when the instance is missing.

        Raises:
            Exception: any DB-level error propagates — see fail-open
                wrapper note on :meth:`increment_attestation_denied_count`.
        """
        return self._update_attestation_columns(
            instance_id,
            attestation_denied_count=0,
            completion_gate_escalated=True,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def set_completion_gate_escalated(self, instance_id: str) -> bool:
        """Terminal-after-bound flag setter (no counter change).

        Caller: ``completion_gate_escalated`` is a postmortem marker
        that persists for the rest of the instance's life unless reset.
        Per leader ruling 2, the SAME reset op clears BOTH columns;
        callers that want both columns flipped atomically should use
        :meth:`reset_attestation_ledger_with_escalation` (terminal_after
        _bound path) instead.

        Returns:
            ``True`` when the row was found and the flag set;
            ``False`` when the instance is missing.

        Raises:
            Exception: any DB-level error propagates — see fail-open
                wrapper note on :meth:`increment_attestation_denied_count`.
        """
        return self._update_attestation_columns(
            instance_id,
            completion_gate_escalated=True,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def get_attestation_denied_count(self, instance_id: str) -> int:
        """Read the current deny counter for the gate node's
        ``denied_count_getter`` (Phase 3 task 3.3).

        Returns:
            Current ``attestation_denied_count`` value; ``0`` when the
            row is missing — BY DESIGN, not an error (a fresh instance
            simply has no counter yet: the read folds the missing row
            to ``0`` exactly like ``int(... or 0)`` folds a NULL
            column). Callers fail-open only on RAISED exceptions (via
            ``except Exception`` at the gate's count getter), never on
            a missing row.

        Raises:
            Exception: any DB-level error propagates — see fail-open
                wrapper note on :meth:`increment_attestation_denied_count`.
        """
        from sqlmodel import select as sqlmodel_select

        with SQLModelSession(self.engine) as db_session:
            row = db_session.exec(
                sqlmodel_select(Instance.attestation_denied_count).where(
                    Instance.instance_id == instance_id
                )
            ).first()
            if row is None:
                return 0
            return int(row[0] if isinstance(row, tuple) else row)

    # Short protocol aliases consumed by the in-graph gate.  The public
    # repository methods above are intentionally descriptive; these aliases
    # keep the graph's small AttestationLedger protocol honest when it is
    # given the real SQLModelInstanceRepository.
    def increment(self, instance_id: str, denial_epoch: str) -> int:
        return self.increment_attestation_denied_count(instance_id, denial_epoch)

    def reset(self, instance_id: str) -> bool:
        return self.reset_attestation_denied_count(instance_id)

    def set_escalated_and_reset(self, instance_id: str) -> bool:
        return self.reset_attestation_ledger_with_escalation(instance_id)

    # --------------------------------------------------------
    # ZOMBIE-INSTANCE SCAN — System Cleanup Bucket 5
    # --------------------------------------------------------

    # Terminal ``InstanceStatus`` values that mark an instance as already
    # dead (``completed``, ``error``, ``terminated``, ``failed``). The
    # value set is duplicated here as a frozen tuple of strings (rather
    # than re-derived from :class:`InstanceStatus`) so the SQL template
    # stays a plain ``NOT IN (,)`` predicate with bound parameters,
    # matching the :meth:`count_bad_state_tasks` pattern.
    #
    # W1: This same terminal set is also reused in the third
    # ``NOT EXISTS`` clause of :meth:`_build_zombie_scan_sql` to
    # exclude parents that still have non-terminal children — a
    # ``WAITING_CHILDREN`` instance may have children still executing,
    # and terminating the parent would orphan them.
    _TERMINAL_STATUSES_FOR_ZOMBIE_SCAN: tuple[str, ...] = (
        "completed",
        "error",
        "terminated",
        "failed",
    )

    # Statuses that mark a Task as "still in flight" for the purpose of
    # the zombie-instance scan. ``pending``/``running``/``paused`` mirror
    # the bad-state reconciliation set; a Task in any of these is enough
    # to consider the instance alive.
    _LIVE_TASK_STATUSES_FOR_ZOMBIE_SCAN: tuple[str, ...] = (
        "pending",
        "running",
        "paused",
    )

    # Admission states on ``job_queue_items`` that mean the JobItem is
    # still doing real work (``queued`` = PENDING, ``active`` = PROCESSING).
    # Rows in ``done``/``dead`` (or soft-deleted) are excluded because
    # they are not live work.
    _LIVE_JOBITEM_STATES_FOR_ZOMBIE_SCAN: tuple[str, ...] = (
        "queued",
        "active",
    )

    def _build_zombie_scan_sql(self, count_only: bool) -> Any:
        """Return the raw-SQL ``text()`` statement for the zombie scan.

        A "zombie instance" is one whose ``instances.status`` is NOT in
        the terminal set AND has no live JobItem AND no live Task AND
        has no non-terminal child instance.

        **WS4 mission lens (2026-09-06, ``fix/defer-self-witness-and-
        cleanup``):** the JobItem anti-join no longer lets an instance's
        OWN queued defer-lane rows shield it (the "self-shield"). The
        live ops unstick (incident 2026-09-06, instance 6bc61f42 / job
        47161b1e) proved the gap: a stalled holder's only remaining
        busy rows were its own queued defer-lane message mirrors, so
        the pre-WS4 scan shielded the holder from the reaper forever —
        the mirrors hold the defer gate, the gate holds the mirrors,
        nothing ever runs. The witness clause now counts a JobItem row
        against the instance ONLY when it is ``active`` (any lane) or
        ``queued`` on a NON-defer lane (or on an unknown/missing queue
        — a ``LEFT JOIN`` miss yields ``queue_type IS NULL`` and the
        row still shields: fail-CLOSED, matching the gate's error
        posture). Rationale for exempting queued defer TASK rows too:
        the reaper runs AFTER cleanup bucket 1
        (``batch_cancel_queued``), which has already drained every
        queued non-mirror job, so at reaper time the surviving queued
        defer-lane rows are necessarily ``job_type='message'`` mirrors
        — and for the standalone preflight the exemption predicts the
        post-bucket-1 state, which is what a preflight must predict.

        Each anti-join is expressed as a ``NOT EXISTS (SELECT 1 ... WHERE
        jqi.instance_id = i.instance_id ...)`` correlated subquery — NOT
        as ``NOT IN (SELECT DISTINCT jqi.instance_id ...)``. C1 (2026-08-12):
        ``job_queue_items.instance_id`` is nullable, and SQL three-valued
        logic makes ``NOT IN (..., NULL)`` evaluate to UNKNOWN for every
        row (the NULL poisoning bug). A single ``job_queue_items``
        row with ``instance_id IS NULL`` was silently producing an empty
        scan, so Bucket 5 matched nothing in production. ``NOT EXISTS``
        is NULL-safe because the correlation is an equality predicate
        (``jqi.instance_id = i.instance_id``) on the outer row —
        NULL = NULL is UNKNOWN but the outer row is excluded only by
        the EXISTS-correlated match, not by NULL membership.

        The third ``NOT EXISTS`` (W1, 2026-08-12) excludes parents
        whose children are still non-terminal — terminating a parent
        with live children would orphan them. The instance model is
        self-referential via ``instances.parent_id``; the scan walks
        the child rows for each candidate parent.

        See :meth:`find_orphan_active_jobs` in
        ``daemon/repositories/job_queue/repository.py`` for the
        reference pattern on ``NOT EXISTS`` use in this codebase.

        Args:
            count_only: When True, return a ``COUNT(DISTINCT)`` statement;
                otherwise return a row-level ``SELECT i.instance_id``
                statement.

        Returns:
            A :class:`sqlalchemy.sql.expression.TextClause` ready to be
            executed with the standard bound-parameter dict.
        """
        if count_only:
            select_clause = "SELECT COUNT(DISTINCT i.instance_id)"
        else:
            select_clause = "SELECT i.instance_id"
        # NOTE: terminal / live sets are baked into the SQL string as
        # literal lists (not bound parameters) because SQLAlchemy's
        # ``expanding`` parameter style is dialect-fragile on SQLite
        # when used inside ``NOT IN (...)``. The lists are short,
        # closed, and defined as class-level tuples above, so baking is
        # safe and avoids the expanding-param issue. The same terminal
        # CSV is reused by the W1 anti-join — that subquery compares
        # ``child.status`` against it.
        terminal_csv = ", ".join(
            f"'{s}'" for s in self._TERMINAL_STATUSES_FOR_ZOMBIE_SCAN
        )
        live_task_csv = ", ".join(
            f"'{s}'" for s in self._LIVE_TASK_STATUSES_FOR_ZOMBIE_SCAN
        )
        live_jobitem_csv = ", ".join(
            f"'{s}'" for s in self._LIVE_JOBITEM_STATES_FOR_ZOMBIE_SCAN
        )
        return text(f"""
            {select_clause} FROM instances i
            WHERE i.status NOT IN ({terminal_csv})
              AND NOT EXISTS (
                SELECT 1 FROM job_queue_items jqi
                LEFT JOIN job_queues jq ON jq.queue_id = jqi.queue_id
                WHERE jqi.instance_id = i.instance_id
                  AND jqi.admission_state IN ({live_jobitem_csv})
                  AND jqi.deleted_at IS NULL
                  -- WS4 mission lens: the instance's OWN queued
                  -- defer-lane rows do NOT witness against it (the
                  -- self-shield exemption). ``active`` rows of any
                  -- lane and queued rows on non-defer / unknown
                  -- lanes still witness (fail-CLOSED on the LEFT
                  -- JOIN miss via the explicit ``IS NULL`` arm).
                  AND (
                    jqi.admission_state = 'active'
                    OR jq.queue_type IS NULL
                    OR jq.queue_type != 'defer'
                  )
              )
              AND NOT EXISTS (
                SELECT 1 FROM task t
                WHERE t.instance_id = i.instance_id
                  AND t.status IN ({live_task_csv})
              )
              AND NOT EXISTS (
                SELECT 1 FROM instances child
                WHERE child.parent_id = i.instance_id
                  AND child.status NOT IN ({terminal_csv})
              )
        """)

    def find_zombie_instances(self) -> list[str]:
        """Return ``instance_id`` strings for non-terminal instances with no live work.

        A "zombie instance" is one whose ``instances.status`` is NOT in
        the terminal set (``completed``, ``error``, ``terminated``,
        ``failed``) AND has:

        * no *witnessing* ``job_queue_items`` rows — WS4 mission lens:
          an ``active`` row on ANY lane, or a ``queued`` row on a
          non-defer (or unknown) lane, witnesses; the instance's OWN
          ``queued`` defer-lane rows do NOT (the self-shield
          exemption — see :meth:`_build_zombie_scan_sql`), and
        * no pending/running/paused ``task`` rows.

        Used by the System Cleanup endpoint's Bucket 5 (instance-level
        reaper) to terminate the leftover non-terminal instance rows
        that no longer have any live work driving them. Without this
        bucket, an instance whose last Task was cancelled and whose last
        JobItem was finalised can stay in ``running``/``paused``/``idle``
        forever, blocking the operator from a clean reset and
        inflating dashboard counters.

        Self-contained SYNC method using raw-SQL ``text()`` — runs
        inside the calling thread's event-loop wrapper
        (``asyncio.to_thread`` on the consumer side). The SQL uses three
        NULL-safe ``NOT EXISTS`` correlated subqueries (anti-joins
        against ``job_queue_items``, ``task``, and child ``instances``)
        so it works on both PostgreSQL and SQLite without dialect-
        specific syntax. See :meth:`_build_zombie_scan_sql` for the
        NULL-poisoning rationale and the W1 parent-child guard.

        Returns:
            List of ``instance_id`` strings that should be terminated.
        """
        stmt = self._build_zombie_scan_sql(count_only=False)
        with self.engine.begin() as conn:
            rows = conn.execute(stmt).fetchall()
        return [row[0] for row in rows if row and row[0] is not None]

    def count_zombie_instances(self) -> int:
        """System-wide count of zombie instances (see :meth:`find_zombie_instances`).

        Same predicate as :meth:`find_zombie_instances` but returns
        ``COUNT(DISTINCT i.instance_id)`` so the preflight endpoint can
        surface a single number for the frontend badge/tooltip without
        paying the cost of hydrating every matching row.

        Self-contained SYNC method using raw-SQL ``text()`` — the
        preflight wraps it in ``asyncio.to_thread`` to keep the request
        non-blocking.

        Returns:
            Count of non-terminal instances with no live work.
        """
        stmt = self._build_zombie_scan_sql(count_only=True)
        with self.engine.begin() as conn:
            row = conn.execute(stmt).fetchone()
        return int(row[0]) if row else 0

    def find_non_terminal_instance_ids(self) -> list[str]:
        """Return ids of ALL non-terminal instances (read-only scan).

        WS4 (2026-09-06) companion to :meth:`find_zombie_instances`
        for the cleanup preflight's live-vs-reap split: the preflight
        derives ``live_instance_ids`` ("will remain") as the
        non-terminal set minus the reap-eligible set, using the SAME
        terminal CSV constant (``_TERMINAL_STATUSES_FOR_ZOMBIE_SCAN``)
        so the two scans cannot disagree on what "terminal" means.

        Self-contained SYNC method using raw-SQL ``text()`` — the
        preflight wraps it in ``asyncio.to_thread``.

        Returns:
            List of ``instance_id`` strings whose status is NOT in the
            terminal set. Unbounded (the operator preflight owns
            bounding).
        """
        terminal_csv = ", ".join(
            f"'{s}'" for s in self._TERMINAL_STATUSES_FOR_ZOMBIE_SCAN
        )
        stmt = text(
            f"SELECT i.instance_id FROM instances i "
            f"WHERE i.status NOT IN ({terminal_csv}) "
            f"ORDER BY i.instance_id"
        )
        with self.engine.begin() as conn:
            rows = conn.execute(stmt).fetchall()
        return [row[0] for row in rows if row and row[0] is not None]

    def has_live_work(self, instance_id: str) -> bool:
        """Return True iff ``instance_id`` has ANY live work driving it.

        WS4 Round-2 W2 (2026-09-06, ``fix/defer-self-witness-and-cleanup``)
        — the single-instance companion to the zombie-scan family. The
        WS4 holder-action guard (:meth:`JobQueueService.force_complete_defer_holder`)
        previously probed ONLY the job side via
        ``JobRepository.has_active_non_deferred_work`` — that misses two
        live-work shapes the zombie scan already detects:

        * a Task in ``pending``/``running``/``paused`` (no JobItem at
          all — direct Task, common for forked helpers / reaper sweep);
        * a non-terminal child instance (a ``waiting_children`` parent
          whose subtree is still executing).

        Without those arms the holder-probe returned False (probe clean)
        for any non-trivial instance, and a force-complete would orphan
        live tasks / live children.

        **Reuse the existing scan arms (derive-don't-reimplement).** The
        predicate composes from the SAME three literal-lists the zombie
        scan bakes into :meth:`_build_zombie_scan_sql`
        (``_TERMINAL_STATUSES_FOR_ZOMBIE_SCAN``,
        ``_LIVE_TASK_STATUSES_FOR_ZOMBIE_SCAN``,
        ``_LIVE_JOBITEM_STATES_FOR_ZOMBIE_SCAN``) so the per-instance
        check CANNOT drift from the bulk-scan definition of "live".
        The JobItem anti-join keeps the WS4 mission-lens self-shield:
        the holder's OWN queued defer-lane rows do NOT witness against
        it (``admission_state='active'`` OR queue is non-defer / unknown).
        The child arm matches the third ``NOT EXISTS`` of the zombie
        scan, same terminal CSV.

        Returns:
            True iff ANY of:

            * EXISTS a JobItem on the instance in ``queued``/``active``,
              AND not a settled-mirror exception
              (WS4 mission lens: ``admission_state='active'`` OR
              ``queue_type`` non-defer / unknown);
            * EXISTS a Task on the instance with status in
              ``_LIVE_TASK_STATUSES_FOR_ZOMBIE_SCAN``;
            * EXISTS a child instance with ``parent_id`` set to this
              instance whose status is not in the terminal CSV.

            False iff none of the above holds — i.e. the instance has
            no live work and is the safe-to-terminate set the bulk
            zombie scan would also match.

        Raises:
            SQLAlchemyError: propagated — caller fails-closed by
                treating any error as ``True`` (busy / refuse). Mirrors
                the gate's own fail-CLOSED posture.
        """
        terminal_csv = ", ".join(
            f"'{s}'" for s in self._TERMINAL_STATUSES_FOR_ZOMBIE_SCAN
        )
        live_task_csv = ", ".join(
            f"'{s}'" for s in self._LIVE_TASK_STATUSES_FOR_ZOMBIE_SCAN
        )
        live_jobitem_csv = ", ".join(
            f"'{s}'" for s in self._LIVE_JOBITEM_STATES_FOR_ZOMBIE_SCAN
        )
        # Parameterized EXISTS over the three anti-join arms; ``iid``
        # is bound so SQLAlchemy handles driver-quote differences
        # (SQLite vs PG). The arms are byte-shared with
        # :meth:`_build_zombie_scan_sql` apart from the outer WHERE
        # binding to a single instance.
        stmt = text(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM job_queue_items jqi
                LEFT JOIN job_queues jq ON jq.queue_id = jqi.queue_id
                WHERE jqi.instance_id = :instance_id
                  AND jqi.admission_state IN ({live_jobitem_csv})
                  AND jqi.deleted_at IS NULL
                  AND (
                    jqi.admission_state = 'active'
                    OR jq.queue_type IS NULL
                    OR jq.queue_type != 'defer'
                  )
            )
            OR EXISTS (
                SELECT 1 FROM task t
                WHERE t.instance_id = :instance_id
                  AND t.status IN ({live_task_csv})
            )
            OR EXISTS (
                SELECT 1 FROM instances child
                WHERE child.parent_id = :instance_id
                  AND child.status NOT IN ({terminal_csv})
            )
            """
        )
        with self.engine.begin() as conn:
            row = conn.execute(stmt, {"instance_id": instance_id}).fetchone()
        return bool(row[0]) if row else False

    def has_real_active_or_queued_work(self, instance_id: str) -> bool:
        """Return True iff ``instance_id`` has a NON-MIRROR ``active``
        / ``queued`` JobItem (Bucket-2-cancellable work).

        Unblock-round ITEM 11 (2026-09-06, ``fix/defer-self-witness-and-cleanup``)
        — the truth-survivor filter for the cleanup preflight. The
        round-2 ``live_ids`` shape (``non-terminal ∖ zombie``) listed
        an instance that has a non-mirror ``active`` / ``queued``
        JobItem (NOT a zombie per the WS4 mission lens — ``active``
        rows of any lane witness, ``queued`` rows on non-defer /
        unknown lanes witness) as a "will remain" candidate. But
        Bucket 2's per-row ``cancel_job`` cascade terminates the
        instance for every active JobItem (excluding mirrors), and
        Bucket 1's queued batch UPDATE cancels every queued JobItem
        (excluding mirrors). So the preflight over-promised survival.

        This helper is the truth-survivor guard: the preflight's
        ``live_instance_ids`` MUST exclude instances for which this
        probe returns True. The probe SQL is the minimum required to
        detect Bucket-2 / Bucket-1 cancellable work:

        * ``admission_state IN ('queued','active')`` — the live
          JobItem predicate (the SAME constant the zombie scan uses).
        * ``job_type != 'message'`` — mirror protection: cleanup does
          NOT cancel ``job_type='message'`` rows in buckets 1/2, so
          they are NOT Bucket-1/2-cancellable.
        * ``deleted_at IS NULL`` — soft-deleted rows are terminal on
          paper and excluded everywhere.

        Returns:
            ``True`` iff at least one qualifying row exists. SQL is
            a single ``SELECT EXISTS`` so the wire cost is one
            round-trip per probed instance.

        Raises:
            SQLAlchemyError: propagated — the preflight wraps the
                probe in ``except Exception`` and treats an error as
                ``has_real_active_or_queued_work == False`` (NOT a
                survivor — conservative, fail-CLOSED).
        """
        live_jobitem_csv = ", ".join(
            f"'{s}'" for s in self._LIVE_JOBITEM_STATES_FOR_ZOMBIE_SCAN
        )
        # Mirror exclusion is hard-coded: ``cleanup_helper`` only
        # flags ``job_type='message'`` rows (the canonical
        # constitution-era mirror protection).
        stmt = text(
            f"""
            SELECT EXISTS (
                SELECT 1 FROM job_queue_items jqi
                WHERE jqi.instance_id = :instance_id
                  AND jqi.admission_state IN ({live_jobitem_csv})
                  AND jqi.job_type != 'message'
                  AND jqi.deleted_at IS NULL
            )
            """
        )
        with self.engine.begin() as conn:
            row = conn.execute(stmt, {"instance_id": instance_id}).fetchone()
        return bool(row[0]) if row else False

    def get_metadata_value(self, instance_id: str, key: str) -> Any | None:
        """Read ONE top-level metadata key without hydrating the row.

        Targeted single-key accessor for hot paths (e.g. the usage-limit
        episode anchor, read on every quota sighting) where a full
        ``get()`` — entire instances row plus the whole metadata JSON
        document — is pure overhead. Dialect-aware:

        * PostgreSQL: ``metadata ->> :key`` (text extraction).
        * SQLite:     ``json_extract(metadata, :path)``.

        Returns ``None`` when the row is missing, the column is NULL,
        or the key is absent — callers that need to distinguish must
        use ``get()``.

        Type note: extracted TEXT is re-parsed to restore JSON types
        (numbers, containers). Boolean fidelity is NOT guaranteed on
        SQLite (``json_extract`` returns JSON ``true`` as integer 1) —
        use ``get()`` when ``is True``/``is False`` identity matters.
        """
        with SQLModelSession(self.engine) as db_session:
            dialect = (
                db_session.bind.dialect.name
                if db_session.bind is not None
                else "sqlite"
            )
            if dialect == "postgresql":
                stmt = text(
                    "SELECT metadata ->> :key FROM instances "
                    "WHERE instance_id = :instance_id"
                )
                params: dict[str, Any] = {"key": key, "instance_id": instance_id}
            else:
                stmt = text(
                    "SELECT json_extract(metadata, :path) FROM instances "
                    "WHERE instance_id = :instance_id"
                )
                params = {"path": f"$.{key}", "instance_id": instance_id}
            row = db_session.execute(stmt, params).fetchone()
            if row is None:
                return None
            value = row[0]
            if value is None:
                return None
            # The extraction returns TEXT; restore the JSON type when the
            # stored value was not itself a string (numbers, booleans,
            # JSON containers). A cheap re-parse keeps the return contract
            # identical to ``instance_metadata.get(key)``.
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return value

    def delete_metadata_if_present(self, instance_id: str, key: str) -> bool:
        """Conditionally delete one metadata key — no write when absent.

        Variant of :meth:`delete_metadata` for hot-path clears (e.g. the
        usage-limit anchor on every successful task): the WHERE clause
        carries a key-presence predicate (PostgreSQL ``metadata ? :key``,
        SQLite ``json_type(metadata, :path) IS NOT NULL``) so an absent
        key matches ZERO rows — no UPDATE, no ``updated_at`` bump, no
        rewrite of the JSON document, no post-commit refresh SELECT.

        Returns:
            ``True`` when a row was matched and the key deleted;
            ``False`` when the instance or the key is absent (no-op).
        """
        with SQLModelSession(self.engine) as db_session:
            dialect = (
                db_session.bind.dialect.name
                if db_session.bind is not None
                else "sqlite"
            )
            now = datetime.now(timezone.utc).isoformat()

            if dialect == "postgresql":
                update_sql = text(
                    """
                    UPDATE instances
                    SET metadata = metadata - :key,
                        updated_at = :now
                    WHERE instance_id = :instance_id
                      AND metadata ? :key
                    """
                )
                params = {
                    "key": key,
                    "now": now,
                    "instance_id": instance_id,
                }
            else:
                update_sql = text(
                    """
                    UPDATE instances
                    SET metadata = json_remove(metadata, :path),
                        updated_at = :now
                    WHERE instance_id = :instance_id
                      AND json_type(metadata, :path) IS NOT NULL
                    """
                )
                params = {
                    "path": f"$.{key}",
                    "now": now,
                    "instance_id": instance_id,
                }

            result = db_session.execute(update_sql, params)
            db_session.commit()
            return bool(result.rowcount)

    def find_instances_with_metadata_key(
        self, key: str, value: Any
    ) -> list[Instance]:
        """Return instances whose top-level metadata key equals ``value``.

        Uses the native JSON operator on each supported database rather than
        loading every instance and filtering in Python:

        * PostgreSQL compares ``metadata -> key`` with a bound JSONB value.
        * SQLite compares two ``json_extract`` results so booleans, strings,
          numbers, nulls, arrays, and objects retain JSON semantics.

        Args:
            key: Top-level JSON metadata key.
            value: JSON-serialisable value to match exactly.

        Returns:
            Enriched matching instance rows.
        """
        with SQLModelSession(self.engine) as db_session:
            dialect = (
                db_session.bind.dialect.name
                if db_session.bind is not None
                else "sqlite"
            )
            encoded_value = json.dumps(value)
            stmt = select(Instance)
            if dialect == "postgresql":
                stmt = stmt.where(
                    text(
                        "(metadata -> CAST(:metadata_key AS TEXT)) = "
                        "CAST(:metadata_value AS jsonb)"
                    )
                ).params(
                    metadata_key=key,
                    metadata_value=encoded_value,
                )
            else:
                if value is None:
                    stmt = stmt.where(
                        text("json_type(metadata, :metadata_path) = 'null'")
                    ).params(metadata_path=f"$.{key}")
                else:
                    # Extract the expected value from a one-key JSON wrapper
                    # so SQLite applies the same scalar/object conversion to
                    # both sides of the equality comparison.
                    stmt = stmt.where(
                        text(
                            "json_extract(metadata, :metadata_path) = "
                            "json_extract(:metadata_wrapper, '$.value')"
                        )
                    ).params(
                        metadata_path=f"$.{key}",
                        metadata_wrapper=json.dumps({"value": value}),
                    )
            instances = list(db_session.exec(stmt).all())
            return self._enrich_instances(db_session, instances)

    def set_metadata(self, instance_id: str, key: str, value: Any) -> Instance | None:
        """Atomically set an instance_metadata key-value pair.

        Uses a dialect-aware single-statement UPDATE so concurrent calls
        targeting different keys compose correctly instead of silently
        overwriting each other:

        * PostgreSQL: ``jsonb_set(COALESCE(metadata, '{}'::jsonb), ...)``
        * SQLite:     ``json_set(COALESCE(metadata, '{}'), ...)``

        ``COALESCE`` keeps the call safe when the column is NULL.

        Args:
            instance_id: The instance ID whose metadata to update.
            key: Top-level JSON key to set.
            value: JSON-serialisable value to store.

        Returns:
            The refreshed enriched ``Instance``, or ``None`` if the
            instance does not exist.
        """
        with SQLModelSession(self.engine) as db_session:
            dialect = (
                db_session.bind.dialect.name
                if db_session.bind is not None
                else "sqlite"
            )
            json_value = json.dumps(value)
            now = datetime.now(timezone.utc).isoformat()

            if dialect == "postgresql":
                update_sql = text(
                    """
                    UPDATE instances
                    SET metadata = jsonb_set(
                        COALESCE(metadata, '{}'::jsonb),
                        :path,
                        CAST(:value AS jsonb),
                        true
                    ),
                    updated_at = :now
                    WHERE instance_id = :instance_id
                    """
                )
                path_value = f"{{{key}}}"
            else:
                update_sql = text(
                    """
                    UPDATE instances
                    SET metadata = json_set(
                        COALESCE(metadata, '{}'),
                        :path,
                        json(:value)
                    ),
                    updated_at = :now
                    WHERE instance_id = :instance_id
                    """
                )
                path_value = f"$.{key}"

            db_session.execute(
                update_sql,
                {
                    "path": path_value,
                    "value": json_value,
                    "now": now,
                    "instance_id": instance_id,
                },
            )
            db_session.commit()

            instance = db_session.get(Instance, instance_id)
            if instance is None:
                return None
            return self._enrich_instance(db_session, instance)

    def set_metadata_many(
        self, instance_id: str, updates: dict[str, Any]
    ) -> Instance | None:
        """Atomically set multiple ``instance_metadata`` JSONB keys in a single UPDATE.

        Phase 3 / Watchover (2026-08-05, T3.3b). Writes N keys in ONE
        SQL statement so a partial crash or concurrent read cannot expose
        torn state (e.g. ``watchover_enabled=true`` but
        ``watchover_context`` still empty). The single-key :meth:`set_metadata`
        is composable for callers that touch one key at a time, but the
        watchover activation path sets 3-4 keys together and MUST use this
        atomic helper.

        Implementation reuses the dialect-aware ``jsonb_set`` (PostgreSQL) /
        ``json_set`` (SQLite) pattern from :meth:`set_metadata` and nests
        N calls — one chain per key. ``COALESCE(metadata, '{}')`` keeps
        the call safe when the column is NULL.

        Args:
            instance_id: The instance ID whose metadata to update.
            updates: Mapping of top-level JSON key to JSON-serialisable
                value. Each value is ``json.dumps``'d before binding.
                Must be non-empty.

        Returns:
            The refreshed enriched ``Instance``, or ``None`` if the
            instance does not exist.

        Raises:
            ValueError: If ``updates`` is empty (caller bug — silently
                no-op'ing an empty write would be ambiguous).
        """
        if not updates:
            raise ValueError("set_metadata_many requires at least one key")

        with SQLModelSession(self.engine) as db_session:
            dialect = (
                db_session.bind.dialect.name
                if db_session.bind is not None
                else "sqlite"
            )
            now = datetime.now(timezone.utc).isoformat()

            # Pre-encode every value once so dialect branches can bind them.
            encoded: dict[str, str] = {k: json.dumps(v) for k, v in updates.items()}

            if dialect == "postgresql":
                # Build a nested jsonb_set chain — one layer per key.
                # The deepest layer wraps COALESCE(metadata, '{}'::jsonb)
                # and each shallower layer wraps the previous result.
                # Final SQL: jsonb_set(jsonb_set(jsonb_set(COALESCE(...),
                # path0, val0, true), path1, val1, true), path2, val2, true)
                params: dict[str, Any] = {"now": now, "instance_id": instance_id}
                nested_expr = "COALESCE(metadata, '{}'::jsonb)"
                for i, key in enumerate(updates.keys()):
                    nested_expr = (
                        f"jsonb_set({nested_expr}, :path{i}, "
                        f"CAST(:value{i} AS jsonb), true)"
                    )
                    params[f"path{i}"] = f"{{{key}}}"
                    params[f"value{i}"] = encoded[key]

                update_sql = text(
                    f"""
                    UPDATE instances
                    SET metadata = {nested_expr},
                    updated_at = :now
                    WHERE instance_id = :instance_id
                    """
                )
            else:
                # SQLite — nested json_set() chain. The JSON1 ``json_set``
                # function takes (target, path, value) and returns the
                # patched document; nesting composes multiple writes.
                nested_expr = "COALESCE(metadata, '{}')"
                params = {"now": now, "instance_id": instance_id}
                for i, key in enumerate(updates.keys()):
                    nested_expr = (
                        f"json_set({nested_expr}, :path{i}, json(:value{i}))"
                    )
                    params[f"path{i}"] = f"$.{key}"
                    params[f"value{i}"] = encoded[key]

                update_sql = text(
                    f"""
                    UPDATE instances
                    SET metadata = {nested_expr},
                    updated_at = :now
                    WHERE instance_id = :instance_id
                    """
                )

            db_session.execute(update_sql, params)
            db_session.commit()

            instance = db_session.get(Instance, instance_id)
            if instance is None:
                return None
            return self._enrich_instance(db_session, instance)

    def delete_metadata(self, instance_id: str, key: str) -> Instance | None:
        """Atomically delete an instance_metadata key.

        Uses a dialect-aware single-statement UPDATE so the deletion
        cannot lose concurrent writes to other keys via a stale read:

        * PostgreSQL: ``metadata - :key`` (jsonb delete-by-key)
        * SQLite:     ``json_remove(metadata, :path)``

        Args:
            instance_id: The instance ID whose metadata to mutate.
            key: Top-level JSON key to delete.

        Returns:
            The refreshed enriched ``Instance``, or ``None`` if the
            instance does not exist.
        """
        with SQLModelSession(self.engine) as db_session:
            dialect = (
                db_session.bind.dialect.name
                if db_session.bind is not None
                else "sqlite"
            )
            now = datetime.now(timezone.utc).isoformat()

            if dialect == "postgresql":
                update_sql = text(
                    """
                    UPDATE instances
                    SET metadata = metadata - :key,
                    updated_at = :now
                    WHERE instance_id = :instance_id
                    """
                )
                params = {
                    "key": key,
                    "now": now,
                    "instance_id": instance_id,
                }
            else:
                update_sql = text(
                    """
                    UPDATE instances
                    SET metadata = json_remove(metadata, :path),
                    updated_at = :now
                    WHERE instance_id = :instance_id
                    """
                )
                params = {
                    "path": f"$.{key}",
                    "now": now,
                    "instance_id": instance_id,
                }

            db_session.execute(update_sql, params)
            db_session.commit()

            instance = db_session.get(Instance, instance_id)
            if instance is None:
                return None
            return self._enrich_instance(db_session, instance)

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    @staticmethod
    def _cascade_instance_deps(db_session: SQLModelSession, instance_id: str) -> None:
        """Delete all dependent records for an instance (except the instance itself).

        Order: JobWatcher → Task → Event → MessageQueue → InstanceUiPrefs →
        InstanceHierarchy (parent) → InstanceHierarchy (child).
        JobWatcher must come first since it has a real FK to instances.instance_id.
        Does NOT commit — the caller handles the commit.
        """
        # JobWatcher (FK to instances.instance_id)
        db_session.exec(
            sql_delete(JobWatcher).where(JobWatcher.instance_id == instance_id)
        )
        # Task
        db_session.exec(
            sql_delete(Task).where(Task.instance_id == instance_id)
        )
        # Event
        db_session.exec(
            sql_delete(Event).where(Event.instance_id == instance_id)
        )
        # MessageQueue
        db_session.exec(
            sql_delete(MessageQueue).where(MessageQueue.instance_id == instance_id)
        )
        # InstanceUiPrefs
        db_session.exec(
            sql_delete(InstanceUiPrefs).where(InstanceUiPrefs.instance_id == instance_id)
        )
        # InstanceHierarchy where instance is parent
        db_session.exec(
            sql_delete(InstanceHierarchy).where(InstanceHierarchy.parent_id == instance_id)
        )
        # InstanceHierarchy where instance is child
        db_session.exec(
            sql_delete(InstanceHierarchy).where(InstanceHierarchy.child_id == instance_id)
        )

    def delete(self, instance_id: str) -> dict[str, Any]:
        """Delete an instance and its hierarchy references."""
        with SQLModelSession(self.engine) as db_session:
            instance = db_session.get(Instance, instance_id)
            if instance is None:
                return {"deleted": False, "instance_id": instance_id, "error": "Not found"}

            self._cascade_instance_deps(db_session, instance_id)
            db_session.delete(instance)
            db_session.commit()

            return {
                "deleted": True,
                "instance_id": instance_id,
                "agent_dir": instance.agent_dir,
            }

    def hard_delete_tree(self, tree_ids: list[str]) -> dict[str, Any]:
        """Hard-delete DB records for a tree of instances in FK-safe order.

        Destructive: removes ``instance`` rows AND every dependent row
        across the standard cascade (``tasks``, ``events``, ``message_queue``,
        ``instance_hierarchy``) PLUS the job-queue sub-tree (``job_queue_items``,
        ``job_watchers``, ``job_locks``) AND the source / dependency-bus
        linkage rows (``dependency_watchers``, ``instance_mappings``).
        Checkpoints (``checkpoints.db``) are NOT touched by this method —
        the caller is responsible for sweeping ``adelete_thread`` for each
        ``tree_ids`` member via :class:`CheckpointerAdapter` (see
        :meth:`InstanceLifecycleService.hard_delete_instance`).

        Dependency order (must match to avoid FK violations):

        1. ``job_locks`` — via subquery on ``job_queue_items.job_id``
           (matches the plan documented in
           ``instance-deletion-architecture-in-agents-ensemble``
           on the Knowledge Base). The lock table has no formal FK to
           ``job_queue_items.job_id`` — we delete by job_id rather than
           by ``JobLock.instance_id`` because the ``instance_id`` field
           on locks is informational (denormalised at acquire time) and
           the job-id subquery is the authoritative source.
        2. ``job_queue_items`` — must precede ``job_watchers``/``tasks``/
           ``instances`` for symmetry even though no formal FK chain
           exists; also drops the source rows the lock subquery keys on.
        3. ``job_watchers`` — has a REAL ``foreign_key="instances.instance_id"``
           constraint, so MUST be cleaned before the matching ``instance``
           rows are deleted. Without this, PostgreSQL raises IntegrityError
           and SQLite silently leaves the instance row but loses the watcher.
        4. ``tasks`` — referenced by instance_id (no FK).
        5. ``events`` — referenced by instance_id (no FK).
        6. ``message_queue`` — referenced by instance_id (no FK).
        7. ``dependency_watchers`` — has a logical FK
           (``target_instance_id`` → instances.instance_id) but no formal
           DB-level FK constraint declared on the model. MUST be cleaned
           before the instance rows go; otherwise the watchers become
           orphans pointing at non-existent instance IDs (and the bus's
           cancellation scan picks them up on next stop).
        8. ``instance_mappings`` — has a logical FK
           (``agent_instance_id`` → instances.instance_id) but no formal
           DB-level FK constraint declared on the model. MUST be cleaned
           before the instance rows go; otherwise the mappings become
           orphans and any source routing that re-resolves them on the
           cancelled instance path will silently fail.
        9. ``instance_hierarchy`` — must precede ``instances`` because the
           junction rows reference instance_id on either side. Wipe both
           parent and child sides in a single statement so the cascade is
           symmetric (a tree root has no parent link; a tree leaf has no
           child link).
        9b. ``instance_ui_prefs`` — keyed by ``instance_id``. Logical FK
           via ``instance_id`` (no DB-level FK declared; the model lives
           in a separate repository package to keep the agent-tool's
           ``Instance`` model insulated from UI-only fields). MUST be
           cleaned before the matching ``instances`` rows go; otherwise
           the prefs rows become orphans pointing at non-existent
           instance IDs (no formal DB FK means SQLite/PG silently keep
           them, but they then re-surface in the next
           ``GET /instances`` as orphan rows the merge step would
           silently swallow).
        10. ``instances`` — last; every dependent row above must be gone.

        All ten DELETEs run inside a single ``SQLModelSession`` so a
        mid-cascade error rolls back the entire cascade — matches the
        ``WriteGuardSession`` pattern used elsewhere in the codebase
        (H10 transaction-boundary fix).

        Concurrency: on PostgreSQL, we acquire ``SELECT ... FOR UPDATE``
        on the affected ``Instance`` rows BEFORE the cascade — same
        defence-in-depth pattern as :meth:`delete_by_project` (M7 fix).
        On SQLite, the implicit per-session transaction serializes
        writes (SQLite is single-writer).

        Idempotency: re-running with the same ``tree_ids`` is a no-op
        once the matching rows are gone — the WHEREs simply match zero
        rows and ``rowcount`` is 0.

        Args:
            tree_ids: List of instance IDs to hard-delete. Caller MUST
                resolve the tree (root + all descendants) BEFORE calling
                because the existing ``terminate_instance`` already
                removes some related rows — see
                :func:`append_context_key` and the H10 plan for how
                the in-memory cascade composes with this hard-delete
                step.

        Returns:
            Dict with the deletion summary::

                {
                    "deleted": True|False,
                    "tree_ids": [...],
                    "counts": {
                        "job_locks": int,
                        "job_queue_items": int,
                        "job_watchers": int,
                        "tasks": int,
                        "events": int,
                        "message_queue": int,
                        "dependency_watchers": int,
                        "instance_mappings": int,
                        "instance_hierarchy": int,
                        "instance_ui_prefs": int,
                        "instances": int,
                    },
                }

            ``deleted`` is ``True`` iff at least one ``instances`` row
            was removed; ``False`` means the ``tree_ids`` set matched
            no rows (caller likely passed an already-deleted or empty
            list).
        """
        if not tree_ids:
            return {
                "deleted": False,
                "tree_ids": [],
                "counts": {
                    "job_locks": 0,
                    "job_queue_items": 0,
                    "job_watchers": 0,
                    "tasks": 0,
                    "events": 0,
                    "message_queue": 0,
                    "dependency_watchers": 0,
                    "instance_mappings": 0,
                    "instance_hierarchy": 0,
                    "instance_ui_prefs": 0,
                    "instances": 0,
                },
            }

        # Freeze the snapshot as a list — a calling thread that mutates
        # the original between read and write here must not silently
        # widen the cascade set.
        ids: list[str] = list(tree_ids)

        with SQLModelSession(self.engine) as db_session:
            # Detect dialect once so we can take PG row-locks. Mirrors
            # ``delete_by_project`` (M7 fix).
            is_pg = (
                db_session.bind is not None
                and db_session.bind.dialect.name == "postgresql"
            )

            # Pre-lock the affected instance rows. On PG this is a real
            # ``SELECT ... FOR UPDATE``; on SQLite we fall back to a
            # plain SELECT — SQLite's implicit per-session transaction
            # serialises writers (single-writer DB engine).
            lock_stmt = select(Instance.instance_id).where(
                col(Instance.instance_id).in_(ids)
            )
            if is_pg:
                lock_stmt = lock_stmt.with_for_update()
            db_session.exec(lock_stmt).all()

            # 1. job_locks — via job_id subquery. Uses bindparam(..., expanding=True)
            # so the same SQL works for both SQLite (positional ``?``) and
            # PostgreSQL (named ``:tree_ids``) — each dialect expands the
            # ``IN`` correctly.
            locks_stmt = (
                text(
                    "DELETE FROM job_locks "
                    "WHERE job_id IN ("
                    "  SELECT job_id FROM job_queue_items "
                    "  WHERE instance_id IN :tree_ids"
                    ")"
                )
                .bindparams(bindparam("tree_ids", expanding=True))
            )
            locks_count = db_session.execute(
                locks_stmt, {"tree_ids": ids}
            ).rowcount or 0

            # 2. job_queue_items. Uses the ORM ``sql_delete`` for symmetry with
            # the existing ``_cascade_instance_deps`` helper.
            jobs_count = db_session.exec(
                sql_delete(JobItem).where(col(JobItem.instance_id).in_(ids))
            ).rowcount or 0

            # 3. job_watchers — REAL FK to ``instances.instance_id``.
            # Must be cleaned BEFORE the corresponding ``instances`` row
            # is deleted or PostgreSQL raises IntegrityError.
            watchers_count = db_session.exec(
                sql_delete(JobWatcher).where(col(JobWatcher.instance_id).in_(ids))
            ).rowcount or 0

            # 4. tasks.
            tasks_count = db_session.exec(
                sql_delete(Task).where(col(Task.instance_id).in_(ids))
            ).rowcount or 0

            # 5. events.
            events_count = db_session.exec(
                sql_delete(Event).where(col(Event.instance_id).in_(ids))
            ).rowcount or 0

            # 6. message_queue.
            msgq_count = db_session.exec(
                sql_delete(MessageQueue).where(col(MessageQueue.instance_id).in_(ids))
            ).rowcount or 0

            # 7. dependency_watchers — logical FK via target_instance_id
            # (no DB-level constraint declared on the model). Must be cleaned
            # before the matching ``instances`` rows go; otherwise watchers
            # become orphans pointing at non-existent instance IDs and the
            # bus's cancellation scan picks them up on the next stop.
            # Uses the same ORM ``sql_delete`` pattern as the other dependent
            # table deletes — SQLAlchemy expands the ``IN`` to ``?`` on
            # SQLite and ``:ids`` on PostgreSQL automatically, so no
            # bindparam boilerplate is required.
            dep_watchers_count = db_session.exec(
                sql_delete(DependencyWatcher).where(
                    col(DependencyWatcher.target_instance_id).in_(ids)
                )
            ).rowcount or 0

            # 8. instance_mappings — logical FK via agent_instance_id (no
            # DB-level constraint declared on the model). Must be cleaned
            # before the matching ``instances`` rows go; otherwise the
            # mappings become orphans and any source routing that
            # re-resolves them on the cancelled instance path silently
            # fails. Same ORM ``sql_delete`` pattern as #7.
            instance_mappings_count = db_session.exec(
                sql_delete(InstanceMapping).where(
                    col(InstanceMapping.agent_instance_id).in_(ids)
                )
            ).rowcount or 0

            # 9. instance_hierarchy — wipe parent and child sides
            # together so the cascade is symmetric regardless of root
            # vs leaf position. Composed as a single statement with
            # ``OR`` so the planner can collapse it into one scan.
            hierarchy_count = db_session.exec(
                sql_delete(InstanceHierarchy).where(
                    col(InstanceHierarchy.parent_id).in_(ids)
                    | col(InstanceHierarchy.child_id).in_(ids)
                )
            ).rowcount or 0

            # 9b. instance_ui_prefs — keyed by ``instance_id``.
            # Logical FK via ``instance_id`` (no DB-level FK declared
            # on the model because the ``instance_ui_prefs`` package
            # is separate to keep the agent-tool's ``Instance`` model
            # insulated from UI-only fields). MUST be cleaned before
            # the matching ``instances`` rows go; otherwise the prefs
            # rows become orphans pointing at non-existent instance
            # IDs and silently re-surface in the next ``GET`` /
            # ``list_instances`` as rows the merge step swallows.
            ui_prefs_count = db_session.exec(
                sql_delete(InstanceUiPrefs).where(
                    col(InstanceUiPrefs.instance_id).in_(ids)
                )
            ).rowcount or 0

            # 10. instances — last. Single bulk DELETE so the planner
            # can issue one statement instead of N round-trips.
            instances_result = db_session.exec(
                sql_delete(Instance).where(col(Instance.instance_id).in_(ids))
            )
            instances_count = instances_result.rowcount or 0

            db_session.commit()

            logger.info(
                "hard_delete_tree: removed instances=%d, hierarchy=%d, "
                "watchers=%d, jobs=%d, locks=%d, tasks=%d, events=%d, "
                "msgq=%d, dep_watchers=%d, instance_mappings=%d, "
                "ui_prefs=%d (tree_size=%d)",
                instances_count,
                hierarchy_count,
                watchers_count,
                jobs_count,
                locks_count,
                tasks_count,
                events_count,
                msgq_count,
                dep_watchers_count,
                instance_mappings_count,
                ui_prefs_count,
                len(ids),
            )

            return {
                "deleted": instances_count > 0,
                "tree_ids": ids,
                "counts": {
                    "job_locks": int(locks_count),
                    "job_queue_items": int(jobs_count),
                    "job_watchers": int(watchers_count),
                    "tasks": int(tasks_count),
                    "events": int(events_count),
                    "message_queue": int(msgq_count),
                    "dependency_watchers": int(dep_watchers_count),
                    "instance_mappings": int(instance_mappings_count),
                    "instance_hierarchy": int(hierarchy_count),
                    "instance_ui_prefs": int(ui_prefs_count),
                    "instances": int(instances_count),
                },
            }

    def delete_all(self) -> int:
        """Delete all instances from the database.
        
        Returns:
            Number of instances deleted.
        """
        with SQLModelSession(self.engine) as db_session:
            # Count before deletion
            total = len(list(db_session.exec(select(Instance))))

            # Delete all hierarchy links
            db_session.exec(sql_delete(InstanceHierarchy))

            # Delete all instance_ui_prefs (FK → instances.instance_id).
            # Without this, delete_all() leaves orphan prefs rows.
            db_session.exec(sql_delete(InstanceUiPrefs))

            # Delete all instances
            db_session.exec(sql_delete(Instance))
            
            db_session.commit()

            return total

    def delete_by_project(self, project_id: str) -> int:
        """Delete all instances for a project.

        M7 fix: the previous implementation read instance IDs, ran
        per-instance cascades, then bulk-deleted the rows — all without
        any row-level lock. Concurrent ``insert_instance`` /
        ``update_instance`` calls for the same project could either:
          (a) insert a new row after the ID snapshot but before the
              DELETE, leaving an orphaned instance behind (because the
              DELETE WHERE project_id = :p would actually pick that row
              up — see below — so this is the LOWER risk), or
          (b) much worse: an in-flight ``terminate_instance`` call
              could cascade-update dependent rows (status, hierarchy)
              concurrently with our cascade delete, producing a
              partial delete (e.g. JobWatcher gone but Instance row
              stuck because the terminate path locked the Instance row
              in a different transaction).

        We close that window by acquiring ``SELECT ... FOR UPDATE`` on
        the affected rows BEFORE the cascade. PostgreSQL honours
        ``FOR UPDATE`` and serializes us against any concurrent writer
        touching the same rows. SQLite (the dev/test path) does not
        support ``FOR UPDATE`` but its implicit per-session transaction
        already serializes writes against this session, which is
        sufficient because SQLite is single-writer at the database
        level — concurrent writers wait for the holding transaction
        to commit/rollback.

        Args:
            project_id: Project identifier.

        Returns:
            Number of instances deleted.
        """
        with SQLModelSession(self.engine) as db_session:
            # Detect dialect once and pick the right lock mode.
            is_pg = (
                db_session.bind is not None
                and db_session.bind.dialect.name == "postgresql"
            )

            # Get all instance IDs for this project, locking the rows
            # for the duration of this transaction. On PostgreSQL this
            # is ``SELECT ... FOR UPDATE``; on SQLite we fall back to a
            # plain SELECT (the implicit per-session transaction is the
            # lock).
            stmt = select(Instance.instance_id).where(
                Instance.project_id == project_id
            )
            if is_pg:
                stmt = stmt.with_for_update()
            instance_ids = list(db_session.exec(stmt).all())

            # Cascade deps for each instance (handles JobWatcher, Task, Event,
            # MessageQueue, and InstanceHierarchy parent/child rows).
            for instance_id in instance_ids:
                self._cascade_instance_deps(db_session, instance_id)

            # Delete all instances for this project. The PostgreSQL
            # ``FOR UPDATE`` from the snapshot above is still held here,
            # so any concurrent writer that tried to read these rows
            # would have been blocked at the SELECT.
            stmt = sql_delete(Instance).where(Instance.project_id == project_id)
            result = db_session.exec(stmt)
            db_session.commit()
            return result.rowcount

    def backfill_system_default_project_id(self, project_id: str) -> int:
        """Idempotently assign ``project_id`` to instances missing one.

        Background: ``InstanceLifecycleService.spawn_instance`` used to
        skip ``normalize_project_id`` when the caller passed
        ``project_id=None`` (root instances, direct messages, source
        mappings). Those rows were stored with a NULL / empty
        ``project_id`` instead of the system default UUID, which made
        them invisible to project-scoped gates such as the defer-queue
        idle check (``TaskRepository.has_active_non_deferred_work``).
        A paused non-deferred instance on the system default project
        then failed to hold back ``system_defer_queue`` and defer jobs
        started prematurely (bug reproduced 2026-07-07). The spawn path
        is now fixed; this method repairs the legacy rows on every
        startup so the fix takes effect immediately on existing data.

        Args:
            project_id: The system default project ID to stamp onto
                rows whose ``project_id`` is NULL or empty. The caller
                (``ensure_system_default_project`` startup hook)
                guarantees this row exists.

        Returns:
            Number of instance rows updated (0 on a clean / already
            backfilled database).
        """
        with SQLModelSession(self.engine) as db_session:
            result = db_session.exec(
                text(
                    "UPDATE instances SET project_id = :project_id "
                    "WHERE project_id IS NULL OR project_id = ''"
                ),
                params={"project_id": project_id},
            )
            db_session.commit()
            return result.rowcount

    # --------------------------------------------------------
    # WAITING_CHILDREN WATCHDOG HELPERS (issue #8)
    # --------------------------------------------------------
    # The watchdog (daemon/services/waiting_children_watchdog.py)
    # needs two SQL-side primitives — both use dialect-aware age
    # computation to avoid the psycopg session-local-time skew (the
    # ``last_activity_at`` column is timezone-naive; reading it back
    # via a +07 PG session would shift the value by 7h).

    # Hang = child non-terminal AND age(last_activity_at) > threshold.
    # The terminal set mirrors ``_TERMINAL_STATUSES_FOR_ZOMBIE_SCAN``:
    # ``completed``, ``error``, ``terminated``, ``failed``.
    #
    # Design note — ``paused`` is NOT in the terminal set above, BUT
    # we still exclude paused children via the application-side
    # filter in ``list_hung_children_for_parent`` (see comment
    # there). A paused child is NOT "hung" by intent — a user or
    # system operator has paused it explicitly and is the canonical
    # decision-maker. Nudging the parent to "spawn a replacement"
    # would silently create a duplicate child while the paused
    # one waits for the user's resume. So the helper applies an
    # additional ``status != 'paused'`` filter on top of the
    # ``NOT IN (terminal_set)`` predicate.
    #
    # ``waiting_children`` is excluded for the same reason one level
    # down: a child parked in WAITING_CHILDREN is itself blocked on
    # ITS children's completion reports — it is not wedged, it is
    # waiting by design, and parking does NOT refresh
    # ``last_activity_at``. Counting such a child as hung would nudge
    # the grandparent to revive/replace a subtree that is still
    # legitimately working — the exact duplicate-work hazard the
    # paused exclusion exists to avoid, applied recursively.
    _WAITING_CHILDREN_HUNG_TERMINAL_SET: tuple[str, ...] = (
        "completed",
        "error",
        "terminated",
        "failed",
    )

    def list_waiting_children_parents(self) -> list[str]:
        """Return ``instance_id``s for instances currently in ``WAITING_CHILDREN``.

        Cheap status-only enumeration — no instance hydration needed; the
        watchdog only walks children of these IDs, and a per-parent
        hydration would be wasted work for parents with no live children.
        """
        with SQLModelSession(self.engine) as db_session:
            rows = db_session.exec(
                select(Instance.instance_id).where(
                    Instance.status == InstanceStatus.WAITING_CHILDREN.value
                )
            ).all()
            return list(rows)

    def parents_with_non_terminal_children(
        self, parent_ids: list[str],
    ) -> set[str]:
        """Return the subset of ``parent_ids`` that have at least one
        non-terminal child instance.

        Batched single-query equivalent of the wedge-fix third condition
        (``zero non-terminal children``) in
        ``WaitingChildrenWatchdog``'s wedge pass — the inline predicate
        is the inverse of the third ``NOT EXISTS`` clause at
        ``_build_zombie_scan_sql``:1083-1087 (W1 anti-join). Same
        terminal set (``completed`` / ``error`` / ``terminated`` /
        ``failed``) so a child in any other status is considered
        ``still in flight``.

        Used by ``WaitingChildrenWatchdog`` to enforce the wedge
        predicate's third gate in one query for the entire WC-parent
        set instead of one query per parent. Within the
        ``≤1-extra-query-per-WC-parent-per-tick`` budget, the batched
        shape is strictly cheaper: O(1) queries per tick regardless
        of WC-parent count.

        Empty ``parent_ids`` short-circuits to ``set()`` to avoid an
        empty ``IN ()`` SQL clause (SQLite + PostgreSQL both reject
        it).

        Args:
            parent_ids: Parent ``instance_id`` candidates — typically
                the result of :meth:`list_waiting_children_parents`.

        Returns:
            The subset of ``parent_ids`` for whom at least one row in
            ``instances`` has ``parent_id == p`` AND a non-terminal
            ``status``. Parents with zero children, or whose children
            are all terminal, are NOT in the returned set.
        """
        if not parent_ids:
            return set()
        # Baked terminal CSV — matches the zombie-scan convention at
        # ``_build_zombie_scan_sql``:1060-1062 (literal list, not
        # bound parameters — SQLAlchemy's ``expanding`` style is
        # dialect-fragile inside ``NOT IN`` on SQLite).
        terminal_csv = ", ".join(
            f"'{s}'" for s in self._TERMINAL_STATUSES_FOR_ZOMBIE_SCAN
        )
        # Expanding bindparam for ``parent_ids`` (safe here — used
        # inside a top-level ``IN``, NOT inside ``NOT IN``). DISTINCT
        # collapses parents with multiple non-terminal children to a
        # single row so the caller can do O(1) ``in`` checks. The
        # expanding values are bound at statement-build time so the
        # call site uses the SQLModel 1-arg ``Session.exec(stmt)``
        # convention (same shape as every other repo method, e.g.
        # :meth:`list_waiting_children_parents`).
        stmt = text(
            f"""
            SELECT DISTINCT child.parent_id
            FROM instances child
            WHERE child.parent_id IN :parent_ids
              AND child.status NOT IN ({terminal_csv})
            """
        ).bindparams(
            bindparam(
                "parent_ids",
                value=list(parent_ids),
                expanding=True,
            )
        )
        with SQLModelSession(self.engine) as db_session:
            rows = db_session.exec(stmt).all()
        # ``Session.exec(text(...)).all()`` returns a list of
        # ``sqlalchemy.engine.row.Row`` objects — tuple-LIKE (supports
        # integer indexing) but not ``isinstance(..., tuple)``. Always
        # pull the first column; ``Row[0]`` gives the scalar.
        result: set[str] = set()
        for row in rows:
            value = row[0]
            if value is not None:
                result.add(value)
        return result

    @staticmethod
    def _build_hung_children_sql(dialect_name: str) -> TextClause:
        """Build the hung-children SQL for ``dialect_name``.

        Extracted from :meth:`list_hung_children_for_parent` so the
        PostgreSQL branch can be compile-checked / render-verified in
        unit tests without a PG server (the suite's engines are
        SQLite-only while PG is the production primary). Mirrors the
        readiness.py dialect-constant pattern
        (``_QUEUE_MAX_AGE_SQL_POSTGRES`` / ``_QUEUE_MAX_AGE_SQL_SQLITE``
        at ``daemon/services/readiness.py:100-107``).

        Args:
            dialect_name: ``self.engine.dialect.name`` — only the
                ``"postgresql"`` / everything-else (SQLite) split
                matters here.

        Returns:
            A ``text()`` clause with the ``:parent_id`` /
            ``:threshold_seconds`` binds. Callers own execution.
        """
        if dialect_name == "postgresql":
            age_expr = "EXTRACT(EPOCH FROM (now() - last_activity_at))"
        else:
            age_expr = (
                "(julianday('now') - julianday(last_activity_at)) * 86400"
            )
        terminal_csv = ", ".join(
            f"'{s}'" for s in SQLModelInstanceRepository._WAITING_CHILDREN_HUNG_TERMINAL_SET
        )
        return text(f"""
            SELECT instance_id AS child_id,
                   {age_expr} AS age_seconds
              FROM instances
             WHERE parent_id = :parent_id
               AND status NOT IN ({terminal_csv})
               AND status != 'paused'
               AND status != 'waiting_children'
               AND last_activity_at IS NOT NULL
               AND {age_expr} > :threshold_seconds
             ORDER BY age_seconds DESC
        """)

    def list_hung_children_for_parent(
        self,
        parent_id: str,
        threshold_seconds: int,
    ) -> list[tuple[str, float]]:
        """Return ``[(child_id, age_seconds), ...]`` for hung children of ``parent_id``.

        A child is "hung" iff ALL of the following hold:

        * ``child.parent_id == parent_id`` (walks the PERMANENT
          ``instances.parent_id`` record, not the transient
          ``instance_hierarchy`` working set — a child that completed
          and was swept from the hierarchy is still in the permanent
          record, and a hung child by definition has not completed).
        * ``child.status`` is NOT in the terminal set
          (``completed`` / ``error`` / ``terminated`` / ``failed``).
          ``paused`` is DELIBERATELY excluded — a paused child is the
          canonical "not hung" case and the parent must NOT be nagged.
          ``waiting_children`` is excluded the same way — a child
          parked on ITS children is waiting by design (parking does
          not refresh ``last_activity_at``), and counting it hung
          would push the grandparent into duplicating a still-working
          subtree.
        * ``EXTRACT(EPOCH FROM (now() - last_activity_at))`` (PG) or
          ``(julianday('now') - julianday(last_activity_at)) * 86400``
          (SQLite) is strictly greater than ``threshold_seconds``.
          Age is computed SQL-side (per house pattern in
          ``daemon/repositories/task/repository.py``) to avoid the
          7h psycopg session-local skew that bites naive
          ``datetime.now(tz.utc) - row.last_activity_at`` reads on PG
          deployments running in a non-UTC session.

        ``last_activity_at IS NULL`` rows are skipped by the SQL
        predicate (they evaluate NULL, never True, under ``>``).
        Such rows indicate an instance that was never heartbeated
        after creation — extremely rare in production but possible
        during the warm-up window. The watchdog will catch them on a
        later tick after ``last_activity_at`` is populated.

        Args:
            parent_id: The parent instance ID (must already be known
                to be in ``WAITING_CHILDREN``; the caller is
                responsible for that filter — keeps this helper single-
                purpose and cheap).
            threshold_seconds: Strictly-greater-than threshold. A
                child whose age equals the threshold exactly is NOT
                considered hung; only strictly older ages count.

        Returns:
            List of ``(child_id, age_seconds)`` tuples. Empty when no
            children are hung. Ordered by age DESC (oldest first) so
            the watchdog can pick the worst offenders first.
        """
        if threshold_seconds < 0:
            raise ValueError(
                f"threshold_seconds must be >= 0; got {threshold_seconds!r}"
            )

        # Dialect switch on the age expression — same pattern as
        # ``daemon/services/readiness.py`` at lines 100-107
        # (``_QUEUE_MAX_AGE_SQL_POSTGRES`` / ``_QUEUE_MAX_AGE_SQL_SQLITE``).
        # SQLite ships with ``julianday``; PG accepts
        # ``EXTRACT(EPOCH FROM ...)``. The local ``_build_zombie_scan_sql``
        # builds a NOT-EXISTS join skeleton, NOT an age expression,
        # so it is NOT the right precedent here. The SQL text lives
        # in :meth:`_build_hung_children_sql` so the PG branch is
        # dialect-parity-testable without a PG server (the unit suite
        # only runs SQLite while PG is the production primary).
        #
        # ``status != 'paused'`` filter: ``paused`` is technically
        # non-terminal (not in the ``_WAITING_CHILDREN_HUNG_TERMINAL_SET``
        # above), but a paused child is NOT "hung" — it is awaiting a
        # user/system decision and the parent must NOT be nagged. The
        # brief's literal "non-terminal" definition would include
        # ``paused``; we exclude it as a deliberate design choice
        # (documented at the class constant above and in the
        # watchdog's module docstring). An exclusion of ``paused``
        # also means an episode ENDS cleanly when a child is paused
        # — the cooldown set is cleared and a future non-paused
        # episode can re-notify.
        #
        # ``status != 'waiting_children'`` filter: the same hazard one
        # level down. A child parked in WAITING_CHILDREN is blocked on
        # its own children — not wedged — and parking does NOT refresh
        # ``last_activity_at``, so the age predicate would misread it
        # as hung. Nudging this parent to revive/replace that child
        # would duplicate a subtree that is still working. Documented
        # at the class constant above (mirrors the paused-exclusion
        # pattern).
        sql = self._build_hung_children_sql(self.engine.dialect.name)
        with SQLModelSession(self.engine) as db_session:
            rows = db_session.exec(
                sql,
                params={
                    "parent_id": parent_id,
                    "threshold_seconds": float(threshold_seconds),
                },
            ).all()
        return [(row.child_id, float(row.age_seconds)) for row in rows]

    def list_terminal_instance_ids(self, instance_ids: list[str]) -> set[str]:
        """Return the subset of ``instance_ids`` whose status is terminal.

        Terminal set mirrors ``_WAITING_CHILDREN_HUNG_TERMINAL_SET``
        (``completed`` / ``error`` / ``terminated`` / ``failed``).
        Used by the WAITING_CHILDREN watchdog's per-tick cooldown
        purge: a (parent, child) pair whose child reached a terminal
        status via ANY path must have its notified-episode cooldown
        cleared — not only via the watchdog's own scan of that parent
        (a scan that errored, or a parent that left WAITING_CHILDREN,
        would otherwise strand the pair in the cooldown set forever).

        Args:
            instance_ids: Candidate ids. Duplicates are fine; the
                result is a set. Empty list short-circuits to an
                empty set (no SQL emitted).

        Returns:
            Set of the input ids that are currently terminal. Ids
            that do not exist in the table are simply absent from
            the result (a missing row cannot be concluded terminal —
            callers keep their cooldown entries for those).
        """
        if not instance_ids:
            return set()
        with SQLModelSession(self.engine) as db_session:
            rows = db_session.exec(
                select(Instance.instance_id).where(
                    Instance.instance_id.in_(instance_ids),
                    Instance.status.in_(
                        list(self._WAITING_CHILDREN_HUNG_TERMINAL_SET)
                    ),
                )
            ).all()
            return set(rows)
