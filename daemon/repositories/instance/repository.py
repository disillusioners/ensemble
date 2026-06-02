"""SQLModel-based Instance Repository implementation."""

from __future__ import annotations

import json
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete as sql_delete, func, not_
from sqlalchemy.engine import Engine
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session as SQLModelSession, select, col

from .models import Instance, InstanceHierarchy, InstanceStatus
from daemon.repositories.task.models import Task
from daemon.repositories.event.models import Event
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.job_queue.watcher_models import JobWatcher

# Keep in sync with frontend: frontend/src/app/services/instance.service.ts (KB_AGENT_IDS)
KB_AGENT_IDS = frozenset(["experiencer", "kb-importer"])

# Safety limit for tree traversal — prevents infinite loops from circular references
_MAX_TRAVERSAL_DEPTH = 256


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
        """Load children for multiple instances."""
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
    ) -> tuple[list[Instance], int]:
        """List instances with optional status filter and pagination.
        
        Args:
            status: Optional status filter.
            project_id: Optional project ID filter.
            limit: Maximum number of instances to return.
            offset: Number of instances to skip.
            exclude_kb: Exclude KB-related instances (experiencer, kb-importer) when True (default: True).
            
        Returns:
            Tuple of (list of instances, total count).
        """
        with SQLModelSession(self.engine) as db_session:
            # Get total count using database-level counting
            count_stmt = select(func.count()).select_from(Instance)
            if status:
                count_stmt = count_stmt.where(Instance.status == status)
            if project_id is not None:
                count_stmt = count_stmt.where(Instance.project_id == project_id)
            if exclude_kb:
                count_stmt = count_stmt.where(Instance.agent_id.not_in(KB_AGENT_IDS))
            total = db_session.exec(count_stmt).one()

            # Get paginated instances
            stmt = select(Instance)
            if status:
                stmt = stmt.where(Instance.status == status)
            if project_id is not None:
                stmt = stmt.where(Instance.project_id == project_id)
            if exclude_kb:
                stmt = stmt.where(Instance.agent_id.not_in(KB_AGENT_IDS))
            
            stmt = stmt.order_by(col(Instance.created_at).desc()).offset(offset).limit(limit)
            instances = list(db_session.exec(stmt))
            
            return self._enrich_instances(db_session, instances), total

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
        """Update instance title in instance_metadata."""
        with SQLModelSession(self.engine) as db_session:
            instance = db_session.get(Instance, instance_id)
            if instance is None:
                return None

            instance.instance_metadata["title"] = title
            flag_modified(instance, "instance_metadata")
            instance.updated_at = datetime.now(timezone.utc).isoformat()
            db_session.commit()
            db_session.refresh(instance)

            return self._enrich_instance(db_session, instance)

    def set_metadata(self, instance_id: str, key: str, value: Any) -> Instance | None:
        """Set an instance_metadata key-value pair."""
        with SQLModelSession(self.engine) as db_session:
            instance = db_session.get(Instance, instance_id)
            if instance is None:
                return None

            instance.instance_metadata[key] = value
            flag_modified(instance, "instance_metadata")
            instance.updated_at = datetime.now(timezone.utc).isoformat()
            db_session.commit()
            db_session.refresh(instance)

            return self._enrich_instance(db_session, instance)

    def delete_metadata(self, instance_id: str, key: str) -> Instance | None:
        """Delete an instance_metadata key."""
        with SQLModelSession(self.engine) as db_session:
            instance = db_session.get(Instance, instance_id)
            if instance is None:
                return None

            instance.instance_metadata.pop(key, None)
            flag_modified(instance, "instance_metadata")
            instance.updated_at = datetime.now(timezone.utc).isoformat()
            db_session.commit()
            db_session.refresh(instance)

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
