"""SQLModel-based Instance Repository implementation."""

from __future__ import annotations

import json
import logging
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, case, delete as sql_delete, func, literal, not_, or_, String, text
from sqlalchemy import cast as sa_cast
from sqlalchemy.engine import Engine
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
KB_AGENT_IDS = frozenset(["experiencer", "kb-importer", "kb-writer"])

# Safety limit for tree traversal — prevents infinite loops from circular references
_MAX_TRAVERSAL_DEPTH = 256

# Safety cap on descendants loaded per page during root-based pagination.
# Prevents pathological trees (huge fan-out, accidental cycles) from blowing
# up response size / DB latency. Triggers a truncation warning when hit.
MAX_DESCENDANTS_PER_PAGE = 1000


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
        
        Args:
            root_id: Root instance ID to start traversal.
            
        Returns:
            List of instance IDs including root_id and all descendants.
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
