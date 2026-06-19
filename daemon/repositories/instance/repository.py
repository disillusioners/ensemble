"""SQLModel-based Instance Repository implementation."""

from __future__ import annotations

import json
import logging
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete as sql_delete, func, not_, text
from sqlalchemy.engine import Engine
from sqlmodel import Session as SQLModelSession, select, col

from .models import Instance, InstanceHierarchy, InstanceStatus
from daemon.repositories.task.models import Task
from daemon.repositories.event.models import Event
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.job_queue.watcher_models import JobWatcher

logger = logging.getLogger(__name__)

# Keep in sync with frontend: frontend/src/app/services/instance.service.ts (KB_AGENT_IDS)
KB_AGENT_IDS = frozenset(["experiencer", "kb-importer"])

# Safety limit for tree traversal — prevents infinite loops from circular references
_MAX_TRAVERSAL_DEPTH = 256

# Safety cap on descendants loaded per page during root-based pagination.
# Prevents pathological trees (huge fan-out, accidental cycles) from blowing
# up response size / DB latency. Triggers a truncation warning when hit.
MAX_DESCENDANTS_PER_PAGE = 500


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
        """Load child instance IDs from hierarchy table."""
        links = db_session.exec(
            select(InstanceHierarchy).where(InstanceHierarchy.parent_id == instance_id)
        ).all()
        return [link.child_id for link in links]

    def _enrich_instance(self, db_session: SQLModelSession, instance: Instance | None) -> Instance | None:
        """Load children onto instance."""
        if instance is None:
            return None
        with db_session.no_autoflush:
            instance.children = self._load_children(db_session, instance.instance_id)
        return instance

    def _enrich_instances(self, db_session: SQLModelSession, instances: list[Instance]) -> list[Instance]:
        """Load children for multiple instances.

        .. note::
            ``inst.children`` is loaded from the ``instance_hierarchy`` working
            set (entries are removed on child completion). The BFS traversal in
            :meth:`list` (include_descendants=True), by contrast, uses
            ``instances.parent_id`` — the permanent record that survives
            completion. This asymmetry is **intentional**:

            * ``children[]`` drives working-set operations (cascade
              pause/resume) where completed children are no longer relevant.
            * The frontend rebuilds the visible tree from the flat result list
              using ``parent_id`` relationships, so completed descendants are
              still surfaced even when they're absent from ``children[]``.
        """
        with db_session.no_autoflush:
            for inst in instances:
                inst.children = self._load_children(db_session, inst.instance_id)
        return instances

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
            agent_id: Agent ID (e.g., 'coder').
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
            agent_id: The agent identifier (e.g., 'coder', 'leader').
            
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

        Args:
            status: Optional status filter. For ``include_descendants=True``,
                applied to root selection only; descendants are returned
                regardless of status. For flat pagination, applied to all rows.
            project_id: Optional project ID filter (applied to both roots and
                descendants when ``include_descendants=True``).
            limit: Maximum number of root instances to return.
            offset: Number of root instances to skip.
            exclude_kb: Exclude KB-related instances (experiencer, kb-importer)
                when True (default: True).
            include_descendants: When False (default), return a flat paginated
                list of all matching instances. When True, paginate by root and
                BFS-load all descendants of each root in the current page.

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

                total = db_session.exec(count_stmt).one()

                stmt = stmt.order_by(col(Instance.created_at).desc()).offset(offset).limit(limit)
                instances = list(db_session.exec(stmt))
                return self._enrich_instances(db_session, instances), total

        # ────────────────────────────────────────────────────────────────
        # Root-based pagination + BFS descendant loading (API path).
        # ────────────────────────────────────────────────────────────────
        with SQLModelSession(self.engine) as db_session:
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

            root_stmt = (
                root_stmt.order_by(col(Instance.created_at).desc())
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

                # Only project_id is applied mid-traversal (defense-in-depth).
                # exclude_kb and status are handled outside the loop.
                child_stmt = select(Instance).where(
                    col(Instance.parent_id).in_(current_level_ids)
                )
                if project_id is not None:
                    child_stmt = child_stmt.where(Instance.project_id == project_id)

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

    def get_all_with_waiting_for(self) -> list[Instance]:
        """Return all instances with a non-zero ``waiting_for`` counter.

        Phase 4: this is a REBUILD query for ``rebuild_from_db()`` —
        it reconstructs the CorrelationManager's in-memory pending set
        on daemon startup. ``waiting_for`` is the rebuild cache per
        ADR-011, not a control-flow signal. Do NOT replace with a CM
        call; the CM is empty at this point in the lifecycle.
        """
        with SQLModelSession(self.engine) as db_session:
            stmt = select(Instance).where(Instance.waiting_for > 0)
            return db_session.exec(stmt).all()

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    def update(self, instance_id: str, **updates) -> Instance | None:
        """Update an instance's fields."""
        with SQLModelSession(self.engine) as db_session:
            instance = db_session.get(Instance, instance_id)
            if instance is None:
                return None

            if 'status' in updates and not InstanceStatus.is_valid(updates['status']):
                raise ValueError(f"Invalid status: {updates['status']}")

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

    def update_waiting_for(self, instance_id: str, waiting_for: int) -> Instance | None:
        """Update instance waiting_for counter.

        Args:
            instance_id: The instance ID to update.
            waiting_for: New waiting_for value.

        Returns:
            Updated Instance or None if not found.
        """
        return self.update(instance_id, waiting_for=waiting_for)

    def update_title(self, instance_id: str, title: str) -> Instance | None:
        """Update instance title in instance_metadata.

        Delegates to :meth:`set_metadata` so the title write uses the same
        dialect-aware atomic SQL path (single-statement UPDATE with
        ``jsonb_set`` on PostgreSQL / ``json_set`` on SQLite). This avoids
        the read-modify-write race where a concurrent ``set_metadata`` on
        a different key would be silently overwritten.
        """
        return self.set_metadata(instance_id, "title", title)

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

        Order: JobWatcher → Task → Event → MessageQueue → InstanceHierarchy (parent) → InstanceHierarchy (child).
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
            
            # Delete all instances
            db_session.exec(sql_delete(Instance))
            
            db_session.commit()

            return total

    def delete_by_project(self, project_id: str) -> int:
        """Delete all instances for a project.

        Args:
            project_id: Project identifier.

        Returns:
            Number of instances deleted.
        """
        with SQLModelSession(self.engine) as db_session:
            # Get all instance IDs for this project first to run per-instance cascade
            stmt = select(Instance.instance_id).where(Instance.project_id == project_id)
            instance_ids = list(db_session.exec(stmt).all())

            # Cascade deps for each instance (handles JobWatcher, Task, Event,
            # MessageQueue, and InstanceHierarchy parent/child rows).
            for instance_id in instance_ids:
                self._cascade_instance_deps(db_session, instance_id)

            # Delete all instances for this project
            stmt = sql_delete(Instance).where(Instance.project_id == project_id)
            result = db_session.exec(stmt)
            db_session.commit()
            return result.rowcount
