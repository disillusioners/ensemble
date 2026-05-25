"""SQLModel-based Project Repository implementation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sql_delete, insert, func, or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select, col

from .models import Project, ProjectTagLink, ProjectShortnameLink, ProjectStatus, ProjectType, ProjectHistoryEntry, CriticalNoteModel, ProjectMetadataRecord
from daemon.constants import SYSTEM_DEFAULT_PROJECT_NAME
from daemon.repositories.instance.models import Instance, InstanceStatus, InstanceHierarchy
from daemon.repositories.job_queue.models import JobItem, JobStatus, JobLock, DeadLetterItem, JobQueue
from daemon.repositories.task.models import Task
from daemon.repositories.event.models import Event
from daemon.repositories.message_queue.models import MessageQueue


class SQLModelProjectRepository:
    """SQLModel-based Project repository with bulk operations for tags/shortnames."""
    
    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    # --------------------------------------------------------
    # INTERNAL HELPERS - Using bulk operations to avoid flush warning
    # --------------------------------------------------------

    def _load_tags(self, session: Session, project_id: str) -> list[str]:
        """Load tags from junction table."""
        links = session.exec(
            select(ProjectTagLink).where(ProjectTagLink.project_id == project_id)
        ).all()
        return [link.tag for link in links]

    def _load_shortnames(self, session: Session, project_id: str) -> list[str]:
        """Load shortnames from junction table."""
        links = session.exec(
            select(ProjectShortnameLink).where(ProjectShortnameLink.project_id == project_id)
        ).all()
        return [link.shortname for link in links]

    def _load_metadata(self, session: Session, project_id: str) -> dict[str, Any]:
        """Load metadata from project_metadata_records table."""
        records = self.list_metadata_records(session, project_id)
        return {r.meta_key: r.meta_value for r in records}

    def _enrich_project(self, session: Session, project: Project | None) -> Project | None:
        """Load tags/shortnames/metadata onto project."""
        if project is None:
            return None
        project.tags = self._load_tags(session, project.project_id)
        project.shortnames = self._load_shortnames(session, project.project_id)
        project.project_metadata = self._load_metadata(session, project.project_id)
        return project

    def _enrich_projects(self, session: Session, projects: list[Project]) -> list[Project]:
        """Load tags/shortnames/metadata for multiple projects."""
        for p in projects:
            p.tags = self._load_tags(session, p.project_id)
            p.shortnames = self._load_shortnames(session, p.project_id)
            p.project_metadata = self._load_metadata(session, p.project_id)
        return projects

    def _sync_tags_bulk(self, session: Session, project_id: str, tags: list[str]) -> None:
        """Sync tags using bulk operations - NO FLUSH WARNING."""
        session.exec(
            sql_delete(ProjectTagLink).where(ProjectTagLink.project_id == project_id)
        )
        if tags:
            session.execute(
                insert(ProjectTagLink),
                [{"project_id": project_id, "tag": tag} for tag in tags]
            )
        session.commit()

    def _sync_shortnames_bulk(self, session: Session, project_id: str, shortnames: list[str]) -> None:
        """Sync shortnames using bulk operations - NO FLUSH WARNING."""
        session.exec(
            sql_delete(ProjectShortnameLink).where(ProjectShortnameLink.project_id == project_id)
        )
        if shortnames:
            session.execute(
                insert(ProjectShortnameLink),
                [{"project_id": project_id, "shortname": s} for s in shortnames]
            )
        session.commit()

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    def create(
        self,
        name: str,
        project_type: str = "general",
        main_directory: str | None = None,
        related_directories: list[str] | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        shortnames: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        project_id: str | None = None,
        creator_instance_id: str | None = None,
        creator_agent_id: str | None = None,
    ) -> Project:
        """Create a new project."""
        if not ProjectType.is_valid(project_type):
            raise ValueError(f"Invalid project_type: {project_type}")
        
        with Session(self.engine) as session:
            # Check within the SAME session for atomic uniqueness check
            existing = session.exec(
                select(Project).where(Project.name == name)
            ).first()
            if existing:
                raise ValueError(f"Project with name '{name}' already exists")

            now = datetime.now(timezone.utc).isoformat()
            project_id = project_id or str(uuid.uuid4())
            tags = tags or []
            shortnames = shortnames or []

            project = Project(
                project_id=project_id,
                name=name,
                project_type=project_type,
                status=ProjectStatus.ACTIVE.value,
                main_directory=main_directory,
                related_directories=related_directories or [],
                description=description,
                project_metadata={},  # Stored in project_metadata_records table; enriched on read
                relationships={},
                creator_instance_id=creator_instance_id,
                creator_agent_id=creator_agent_id,
                created_at=now,
                updated_at=now,
            )

            session.add(project)
            session.commit()
            session.refresh(project)

            # Store initial metadata in dedicated table
            if metadata:
                for key, value in metadata.items():
                    self.set_metadata_record(session, project.project_id, key, value)
                session.commit()
                session.refresh(project)

            self._sync_tags_bulk(session, project.project_id, tags)
            self._sync_shortnames_bulk(session, project.project_id, shortnames)

            return self._enrich_project(session, project) or project.to_dict()

    # --------------------------------------------------------
    # READ
    # --------------------------------------------------------

    def get(self, project_id: str) -> Project | None:
        """Get a project by ID."""
        with Session(self.engine) as session:
            project = session.get(Project, project_id)
            return self._enrich_project(session, project)

    def get_by_name(self, name: str) -> Project | None:
        """Get a project by name."""
        with Session(self.engine) as session:
            project = session.exec(
                select(Project).where(Project.name == name)
            ).first()
            return self._enrich_project(session, project)

    def ensure_system_default_project(self) -> str:
        """Get or create the system default project (idempotent).

        Returns:
            The project_id of the system default project.
        """
        # Check if exists
        existing = self.get_by_name(SYSTEM_DEFAULT_PROJECT_NAME)
        if existing:
            return existing.project_id

        # Create with deterministic UUID for consistency across restarts
        project_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, SYSTEM_DEFAULT_PROJECT_NAME))
        now = datetime.now(timezone.utc).isoformat()

        with Session(self.engine) as session:
            project = Project(
                project_id=project_id,
                name=SYSTEM_DEFAULT_PROJECT_NAME,
                project_type="system",
                status=ProjectStatus.ACTIVE.value,
                description="System default project for jobs without an explicit project",
                project_metadata={},  # Stored in project_metadata_records table; enriched on read
                relationships={},
                created_at=now,
                updated_at=now,
            )
            session.add(project)
            session.commit()

            # Store metadata in dedicated table
            self.set_metadata_record(session, project_id, "is_system", True)
            session.commit()

        return project_id

    def get_by_shortname(self, shortname: str) -> Project | None:
        """Get a project by shortname."""
        with Session(self.engine) as session:
            stmt = (
                select(Project)
                .join(ProjectShortnameLink)
                .where(ProjectShortnameLink.shortname == shortname)
            )
            project = session.exec(stmt).first()
            return self._enrich_project(session, project)

    def get_by_instance(self, instance_id: str) -> list[Project]:
        """Get all projects linked to an instance."""
        with Session(self.engine) as session:
            stmt = select(Project).where(
                (Project.creator_instance_id == instance_id)
                | col(Project.relationships).contains(f'"instances"')
            )
            projects = list(session.exec(stmt))
            result = []
            for p in projects:
                if p.creator_instance_id == instance_id:
                    result.append(p)
                elif "instances" in p.relationships and instance_id in p.relationships.get("instances", []):
                    result.append(p)
            return self._enrich_projects(session, result)

    def get_by_directory(self, directory: str) -> list[Project]:
        """Get all projects that reference a directory."""
        with Session(self.engine) as session:
            stmt = select(Project).where(
                (Project.main_directory == directory)
                | col(Project.related_directories).contains(f'"{directory}"')
            )
            projects = list(session.exec(stmt))
            result = []
            for p in projects:
                if p.main_directory == directory or directory in p.related_directories:
                    result.append(p)
            return self._enrich_projects(session, result)

    # --------------------------------------------------------
    # LIST
    # --------------------------------------------------------

    def list_projects(
        self,
        status: str | None = None,
        project_type: str | None = None,
        tags: list[str] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Project]:
        """List projects with optional filters."""
        with Session(self.engine) as session:
            if tags:
                return self._list_with_tags(session, status, project_type, tags, limit, offset)

            stmt = select(Project)
            if status:
                stmt = stmt.where(Project.status == status)
            if project_type:
                stmt = stmt.where(Project.project_type == project_type)

            stmt = stmt.order_by(col(Project.updated_at).desc()).offset(offset).limit(limit)
            projects = list(session.exec(stmt))
            return self._enrich_projects(session, projects)

    def _list_with_tags(
        self,
        session: Session,
        status: str | None,
        project_type: str | None,
        tags: list[str],
        limit: int,
        offset: int,
    ) -> list[Project]:
        """List projects using junction table for tag filtering."""
        stmt = select(Project)
        for tag in tags:
            stmt = stmt.where(
                col(Project.project_id).in_(
                    select(ProjectTagLink.project_id).where(ProjectTagLink.tag == tag)
                )
            )
        if status:
            stmt = stmt.where(Project.status == status)
        if project_type:
            stmt = stmt.where(Project.project_type == project_type)

        stmt = stmt.order_by(col(Project.updated_at).desc()).offset(offset).limit(limit)
        projects = list(session.exec(stmt))
        return self._enrich_projects(session, projects)

    # --------------------------------------------------------
    # SEARCH
    # --------------------------------------------------------

    def search(self, query: str, limit: int = 20) -> list[Project]:
        """Search projects by name, description, or shortnames."""
        with Session(self.engine) as session:
            stmt = (
                select(Project)
                .join(ProjectShortnameLink, isouter=True)
                .where(
                    (col(Project.name).contains(query))
                    | (col(Project.description).contains(query))
                    | (col(ProjectShortnameLink.shortname).contains(query))
                )
                .distinct()
                .order_by(col(Project.updated_at).desc())
                .limit(limit)
            )
            projects = list(session.exec(stmt))
            return self._enrich_projects(session, projects)

    def match_by_keywords(self, keywords: list[str]) -> Project | None:
        """Find best matching project by keywords."""
        if not keywords:
            return None

        with Session(self.engine) as session:
            stmt = select(Project).where(Project.status == ProjectStatus.ACTIVE.value)
            projects = list(session.exec(stmt))
            projects = self._enrich_projects(session, projects)

            if not projects:
                return None

            best_project: Project | None = None
            best_score = 0

            for project in projects:
                score = 0
                identifiers = [project.name.lower()] + [s.lower() for s in project.shortnames]

                for keyword in keywords:
                    kw = keyword.lower()
                    for identifier in identifiers:
                        if kw == identifier:
                            score += 2
                        elif kw in identifier or identifier in kw:
                            score += 1

                if score > best_score:
                    best_score = score
                    best_project = project

            return best_project if best_score > 0 else None

    # --------------------------------------------------------
    # UPDATE
    # --------------------------------------------------------

    def update(self, project_id: str, **updates) -> Project | None:
        """Update a project's fields."""
        with Session(self.engine) as session:
            project = session.get(Project, project_id)
            if project is None:
                return None

            if 'status' in updates and not ProjectStatus.is_valid(updates['status']):
                raise ValueError(f"Invalid status: {updates['status']}")

            if 'name' in updates and updates['name'] != project.name:
                existing = self.get_by_name(updates['name'])
                if existing:
                    raise ValueError(f"Project with name '{updates['name']}' already exists")

            tags_update = updates.pop('tags', None)
            shortnames_update = updates.pop('shortnames', None)
            metadata_update = updates.pop('project_metadata', None)

            for key, value in updates.items():
                if hasattr(project, key):
                    setattr(project, key, value)
                    flag_modified(project, key)

            project.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            session.refresh(project)

            if tags_update is not None:
                self._sync_tags_bulk(session, project_id, tags_update)
            if shortnames_update is not None:
                self._sync_shortnames_bulk(session, project_id, shortnames_update)
            if metadata_update is not None:
                if metadata_update:
                    for k, v in metadata_update.items():
                        self.set_metadata_record(session, project_id, k, v)
                else:
                    # Empty dict = clear all metadata
                    session.exec(
                        sql_delete(ProjectMetadataRecord).where(ProjectMetadataRecord.project_id == project_id)
                    )
                session.commit()
                session.refresh(project)

            return self._enrich_project(session, project)

    def update_status(self, project_id: str, status: str) -> Project | None:
        """Update project status."""
        return self.update(project_id, status=status)

    # --------------------------------------------------------
    # TAGS
    # --------------------------------------------------------

    def set_tags(self, project_id: str, tags: list[str]) -> Project | None:
        """Replace all tags on a project."""
        return self.update(project_id, tags=tags)

    def add_tag(self, project_id: str, tag: str) -> Project | None:
        """Add a tag to a project."""
        with Session(self.engine) as session:
            project = session.get(Project, project_id)
            if project is None:
                return None

            current_tags = self._load_tags(session, project_id)
            if tag not in current_tags:
                current_tags.append(tag)
                self._sync_tags_bulk(session, project_id, current_tags)
                project.updated_at = datetime.now(timezone.utc).isoformat()
                session.commit()
                session.refresh(project)

            return self._enrich_project(session, project)

    def remove_tag(self, project_id: str, tag: str) -> Project | None:
        """Remove a tag from a project."""
        with Session(self.engine) as session:
            project = session.get(Project, project_id)
            if project is None:
                return None

            current_tags = self._load_tags(session, project_id)
            if tag in current_tags:
                current_tags.remove(tag)
                self._sync_tags_bulk(session, project_id, current_tags)
                project.updated_at = datetime.now(timezone.utc).isoformat()
                session.commit()
                session.refresh(project)

            return self._enrich_project(session, project)

    # --------------------------------------------------------
    # SHORTNAMES
    # --------------------------------------------------------

    def set_shortnames(self, project_id: str, shortnames: list[str]) -> Project | None:
        """Replace all shortnames on a project."""
        return self.update(project_id, shortnames=shortnames)

    def add_shortname(self, project_id: str, shortname: str) -> Project | None:
        """Add a shortname to a project."""
        with Session(self.engine) as session:
            project = session.get(Project, project_id)
            if project is None:
                return None

            current_shortnames = self._load_shortnames(session, project_id)
            if shortname not in current_shortnames:
                current_shortnames.append(shortname)
                self._sync_shortnames_bulk(session, project_id, current_shortnames)
                project.updated_at = datetime.now(timezone.utc).isoformat()
                session.commit()
                session.refresh(project)

            return self._enrich_project(session, project)

    def remove_shortname(self, project_id: str, shortname: str) -> Project | None:
        """Remove a shortname from a project."""
        with Session(self.engine) as session:
            project = session.get(Project, project_id)
            if project is None:
                return None

            current_shortnames = self._load_shortnames(session, project_id)
            if shortname in current_shortnames:
                current_shortnames.remove(shortname)
                self._sync_shortnames_bulk(session, project_id, current_shortnames)
                project.updated_at = datetime.now(timezone.utc).isoformat()
                session.commit()
                session.refresh(project)

            return self._enrich_project(session, project)

    # --------------------------------------------------------
    # DIRECTORIES
    # --------------------------------------------------------

    def add_related_directory(self, project_id: str, directory: str) -> Project | None:
        """Add a related directory to a project."""
        with Session(self.engine) as session:
            project = session.get(Project, project_id)
            if project is None:
                return None

            if directory not in project.related_directories:
                project.related_directories.append(directory)
                project.updated_at = datetime.now(timezone.utc).isoformat()
                flag_modified(project, "related_directories")
                session.commit()
                session.refresh(project)

            return self._enrich_project(session, project)

    def remove_related_directory(self, project_id: str, directory: str) -> Project | None:
        """Remove a related directory from a project."""
        with Session(self.engine) as session:
            project = session.get(Project, project_id)
            if project is None:
                return None

            if directory in project.related_directories:
                project.related_directories.remove(directory)
                project.updated_at = datetime.now(timezone.utc).isoformat()
                flag_modified(project, "related_directories")
                session.commit()
                session.refresh(project)

            return self._enrich_project(session, project)

    # --------------------------------------------------------
    # METADATA
    # --------------------------------------------------------

    def get_metadata_record(self, session: Session, project_id: str, key: str) -> ProjectMetadataRecord | None:
        """Get a single metadata record."""
        return session.exec(
            select(ProjectMetadataRecord).where(
                ProjectMetadataRecord.project_id == project_id,
                ProjectMetadataRecord.meta_key == key
            )
        ).first()

    def set_metadata_record(self, session: Session, project_id: str, key: str, value: Any) -> ProjectMetadataRecord:
        """Insert or update a metadata record (atomic upsert)."""
        if not key or not key.strip():
            raise ValueError("meta_key cannot be empty")

        now = datetime.now(timezone.utc).isoformat()
        stmt = sqlite_insert(ProjectMetadataRecord).values(
            project_id=project_id, meta_key=key, meta_value=value,
            created_at=now, updated_at=now
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=['project_id', 'meta_key'],
            set_={'meta_value': value, 'updated_at': now}
        )
        session.execute(stmt)
        session.flush()
        return self.get_metadata_record(session, project_id, key)

    def delete_metadata_record(self, session: Session, project_id: str, key: str) -> bool:
        """Delete a metadata record by (project_id, key). Returns True if deleted."""
        record = self.get_metadata_record(session, project_id, key)
        if record:
            session.delete(record)
            session.flush()
            return True
        return False

    def list_metadata_records(self, session: Session, project_id: str) -> list[ProjectMetadataRecord]:
        """List all metadata records for a project."""
        return session.exec(
            select(ProjectMetadataRecord).where(ProjectMetadataRecord.project_id == project_id)
        ).all()

    def set_metadata(self, project_id: str, key: str, value: Any) -> Project | None:
        """Set a metadata key-value pair."""
        with Session(self.engine) as session:
            project = session.get(Project, project_id)
            if project is None:
                return None

            self.set_metadata_record(session, project_id, key, value)
            project.updated_at = datetime.now(timezone.utc).isoformat()
            session.add(project)
            session.commit()
            session.refresh(project)

            return self._enrich_project(session, project)

    def delete_metadata(self, project_id: str, key: str) -> Project | None:
        """Delete a metadata key."""
        with Session(self.engine) as session:
            project = session.get(Project, project_id)
            if project is None:
                return None

            self.delete_metadata_record(session, project_id, key)
            project.updated_at = datetime.now(timezone.utc).isoformat()
            session.add(project)
            session.commit()
            session.refresh(project)

            return self._enrich_project(session, project)

    # --------------------------------------------------------
    # RELATIONSHIPS
    # --------------------------------------------------------

    def add_relationship(
        self, project_id: str, entity_type: str, entity_id: str
    ) -> Project | None:
        """Add a relationship to another entity."""
        with Session(self.engine) as session:
            project = session.get(Project, project_id)
            if project is None:
                return None

            if entity_type not in project.relationships:
                project.relationships[entity_type] = []

            if entity_id not in project.relationships[entity_type]:
                project.relationships[entity_type].append(entity_id)
                project.updated_at = datetime.now(timezone.utc).isoformat()
                flag_modified(project, "relationships")
                session.commit()
                session.refresh(project)

            return self._enrich_project(session, project)

    def remove_relationship(
        self, project_id: str, entity_type: str, entity_id: str
    ) -> Project | None:
        """Remove a relationship to another entity."""
        with Session(self.engine) as session:
            project = session.get(Project, project_id)
            if project is None:
                return None

            if entity_type in project.relationships:
                if entity_id in project.relationships[entity_type]:
                    project.relationships[entity_type].remove(entity_id)
                    project.updated_at = datetime.now(timezone.utc).isoformat()
                    flag_modified(project, "relationships")
                    session.commit()
                    session.refresh(project)

            return self._enrich_project(session, project)

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    def delete(
        self,
        project_id: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """Delete a project with full cascade cleanup.
        
        Args:
            project_id: The project ID to delete.
            force: If True, bypass active instance check. Default False.
            
        Returns:
            Dict with deletion summary including counts per entity type.
            
        Raises:
            ValueError: If project not found.
            RuntimeError: If active instances exist and force=False.
        """
        # Non-terminal statuses (instances that block deletion)
        # These are statuses that indicate the instance is still doing work
        non_terminal_statuses = {
            InstanceStatus.RUNNING.value,
            InstanceStatus.PAUSED.value,
            InstanceStatus.QUEUED.value,
            InstanceStatus.WAITING_CHILDREN.value,
        }
        
        with Session(self.engine) as session:
            project = session.get(Project, project_id)
            if project is None:
                raise ValueError(f"Project not found: {project_id}")
            
            project_name = project.name
            
            # BUG 3 FIX: Use direct COUNT query instead of limit=1
            # Check for active instances (non-terminal statuses)
            if not force:
                stmt = select(func.count()).select_from(Instance).where(
                    Instance.project_id == project_id,
                    Instance.status.in_(non_terminal_statuses)
                )
                active_count = session.exec(stmt).one()
                if active_count > 0:
                    raise RuntimeError(
                        f"Cannot delete project with active instances. "
                        f"Found {active_count} non-idle instances. "
                        f"Use force=True to bypass this check."
                    )
            
            # Check for running/processing jobs
            if not force:
                running_statuses = {JobStatus.PROCESSING.value}
                # Count jobs with processing status for this project
                stmt = select(func.count()).select_from(JobItem).where(
                    JobItem.project_id == project_id,
                    JobItem.status.in_(running_statuses)
                )
                running_count = session.exec(stmt).one()
                if running_count > 0:
                    raise RuntimeError(
                        f"Cannot delete project with running jobs. "
                        f"Found {running_count} running/processing jobs. "
                        f"Use force=True to bypass this check."
                    )
            
            # BUG 1 & 2 FIX: Perform ALL cascade deletions inline within this session
            # This ensures atomicity - either ALL changes commit or NONE commit
            
            # Import JobWatcher here to avoid circular imports
            from daemon.repositories.job_queue.watcher_models import JobWatcher
            
            # 1. Get all instance IDs for this project (needed for hierarchy and watchers)
            stmt = select(Instance.instance_id).where(Instance.project_id == project_id)
            instance_ids = list(session.exec(stmt).all())
            
            # 2. Delete job_watchers for jobs in this project (Bug 2: was missing)
            watchers_stmt = sql_delete(JobWatcher).where(
                JobWatcher.job_id.in_(
                    select(JobItem.job_id).where(JobItem.project_id == project_id)
                )
            )
            watchers_deleted = session.exec(watchers_stmt).rowcount
            
            # 3. Delete job_watchers for instances in this project
            if instance_ids:
                instance_watchers_stmt = sql_delete(JobWatcher).where(
                    JobWatcher.instance_id.in_(instance_ids)
                )
                instance_watchers_deleted = session.exec(instance_watchers_stmt).rowcount
                watchers_deleted += instance_watchers_deleted
            
            # 4. Delete job_locks
            locks_deleted = session.exec(
                sql_delete(JobLock).where(JobLock.project_id == project_id)
            ).rowcount
            
            # 5. Delete dead_letter_items
            dlq_deleted = session.exec(
                sql_delete(DeadLetterItem).where(DeadLetterItem.project_id == project_id)
            ).rowcount
            
            # 6. Delete job_queue_items
            jobs_deleted = session.exec(
                sql_delete(JobItem).where(JobItem.project_id == project_id)
            ).rowcount
            
            # 7. Delete job_queues
            queues_deleted = session.exec(
                sql_delete(JobQueue).where(JobQueue.project_id == project_id)
            ).rowcount
            
            # 8. Delete instance hierarchy links (BUG 5 FIX: use IN clause instead of N+1)
            if instance_ids:
                hierarchy_stmt = sql_delete(InstanceHierarchy).where(
                    (col(InstanceHierarchy.parent_id).in_(instance_ids)) |
                    (col(InstanceHierarchy.child_id).in_(instance_ids))
                )
                session.exec(hierarchy_stmt)
            
            # BUG 6 FIX: Delete orphaned tables that reference instance_id
            # These tables were not being cleaned during cascade deletion
            if instance_ids:
                # Delete tasks for this project's instances
                session.exec(sql_delete(Task).where(col(Task.instance_id).in_(instance_ids)))
                # Delete events for this project's instances
                session.exec(sql_delete(Event).where(col(Event.instance_id).in_(instance_ids)))
                # Delete message_queue entries for this project's instances
                session.exec(sql_delete(MessageQueue).where(col(MessageQueue.instance_id).in_(instance_ids)))
            
            # 9. Delete instances
            instances_deleted = session.exec(
                sql_delete(Instance).where(Instance.project_id == project_id)
            ).rowcount
            
            # 10. Delete junction tables
            session.exec(
                sql_delete(ProjectTagLink).where(ProjectTagLink.project_id == project_id)
            )
            session.exec(
                sql_delete(ProjectShortnameLink).where(ProjectShortnameLink.project_id == project_id)
            )
            session.exec(
                sql_delete(ProjectMetadataRecord).where(ProjectMetadataRecord.project_id == project_id)
            )
            session.exec(
                sql_delete(ProjectHistoryEntry).where(ProjectHistoryEntry.project_id == project_id)
            )
            session.exec(
                sql_delete(CriticalNoteModel).where(CriticalNoteModel.project_id == project_id)
            )
            
            # 11. Delete project record
            session.delete(project)
            
            # ONE commit for ALL changes (atomic transaction)
            session.commit()
            
            return {
                "deleted": True,
                "project_id": project_id,
                "name": project_name,
                "message": "Project deleted successfully",
                "counts": {
                    "job_watchers": watchers_deleted,
                    "job_locks": locks_deleted,
                    "dead_letter_items": dlq_deleted,
                    "job_queue_items": jobs_deleted,
                    "job_queues": queues_deleted,
                    "instances": instances_deleted,
                    "project": True,
                },
            }

    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

    def add_history_entry(
        self,
        project_id: str,
        entry_type: str,
        summary: str,
        details: str | None = None,
        source_agent: str | None = None,
        source_instance_id: str | None = None,
        entry_metadata: dict | None = None,
    ) -> dict:
        """Add a history entry to a project.

        Args:
            project_id: The project ID.
            entry_type: Type of history entry.
            summary: Brief summary (truncated to 300 chars).
            details: Optional detailed description (truncated to 5000 chars).
            source_agent: Agent that recorded this entry.
            source_instance_id: Instance that recorded this entry.
            entry_metadata: Optional metadata dictionary.

        Returns:
            The created history entry as a dict.
        """
        summary = summary[:300]
        details = details[:5000] if details else None

        with Session(self.engine) as session:
            entry = ProjectHistoryEntry(
                project_id=project_id,
                entry_type=entry_type,
                summary=summary,
                details=details,
                source_agent=source_agent,
                source_instance_id=source_instance_id,
                entry_metadata=entry_metadata,
            )
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry.to_dict()

    def get_history_entry(self, entry_id: str) -> dict | None:
        """Get a history entry by ID.

        Args:
            entry_id: The history entry ID.

        Returns:
            The history entry as a dict, or None if not found.
        """
        with Session(self.engine) as session:
            entry = session.get(ProjectHistoryEntry, entry_id)
            return entry.to_dict() if entry else None

    def delete_history_entry(self, entry_id: str, project_id: str | None = None) -> bool:
        """Delete a history entry.

        Args:
            entry_id: The history entry ID to delete.
            project_id: Optional project ID for ownership validation.

        Returns:
            True if deleted, False if not found or ownership mismatch.
        """
        with Session(self.engine) as session:
            entry = session.get(ProjectHistoryEntry, entry_id)
            if entry is None:
                return False

            if project_id is not None and entry.project_id != project_id:
                return False

            session.delete(entry)
            session.commit()
            return True

    def list_history_entries(
        self,
        project_id: str,
        entry_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """List history entries for a project.

        Args:
            project_id: The project ID.
            entry_type: Optional filter by entry type.
            limit: Maximum number of entries to return.
            offset: Number of entries to skip.

        Returns:
            Dict with entries, total count, limit, and offset.
        """
        with Session(self.engine) as session:
            stmt = select(ProjectHistoryEntry).where(
                ProjectHistoryEntry.project_id == project_id
            )
            if entry_type:
                stmt = stmt.where(ProjectHistoryEntry.entry_type == entry_type)

            # Get total count
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = session.exec(count_stmt).one()

            # Apply ordering and pagination
            stmt = stmt.order_by(ProjectHistoryEntry.created_at.desc()).offset(offset).limit(limit)
            entries = list(session.exec(stmt))

            return {
                "entries": [e.to_dict() for e in entries],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    def search_history_entries(
        self,
        project_id: str,
        query: str,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Search history entries for a project.

        Args:
            project_id: The project ID.
            query: Search query string.
            limit: Maximum number of entries to return.
            offset: Number of entries to skip.

        Returns:
            Dict with entries, total count, limit, offset, and query.
        """
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        search_term = f"%{escaped}%"

        with Session(self.engine) as session:
            # Build search condition for summary and details with NULL-safe handling
            stmt = select(ProjectHistoryEntry).where(
                ProjectHistoryEntry.project_id == project_id,
                or_(
                    ProjectHistoryEntry.summary.ilike(search_term),
                    func.coalesce(ProjectHistoryEntry.details, "").ilike(search_term),
                ),
            )

            # Get total count
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = session.exec(count_stmt).one()

            # Apply ordering and pagination
            stmt = stmt.order_by(ProjectHistoryEntry.created_at.desc()).offset(offset).limit(limit)
            entries = list(session.exec(stmt))

            return {
                "entries": [e.to_dict() for e in entries],
                "total": total,
                "limit": limit,
                "offset": offset,
                "query": query,
            }

    def get_recent_history(self, project_id: str, limit: int = 10) -> list[dict]:
        """Get recent history entries for a project.

        Args:
            project_id: The project ID.
            limit: Maximum number of entries to return.

        Returns:
            List of history entries as dicts.
        """
        with Session(self.engine) as session:
            stmt = (
                select(ProjectHistoryEntry)
                .where(ProjectHistoryEntry.project_id == project_id)
                .order_by(ProjectHistoryEntry.created_at.desc())
                .limit(limit)
            )
            entries = list(session.exec(stmt))
            return [e.to_dict() for e in entries]

    # --------------------------------------------------------
    # CRITICAL NOTES
    # --------------------------------------------------------

    def list_critical_notes(self, project_id: str) -> list[CriticalNoteModel]:
        """List all critical notes for a project.
        
        Args:
            project_id: The project ID.
            
        Returns:
            List of CriticalNoteModel instances.
        """
        with Session(self.engine) as session:
            notes = list(session.exec(
                select(CriticalNoteModel)
                .where(CriticalNoteModel.project_id == project_id)
                .order_by(CriticalNoteModel.created_at.desc())
            ))
            return notes

    def add_critical_note(
        self,
        project_id: str,
        source_agent: str,
        category: str,
        priority: str,
        summary: str,
        reference: str | None = None,
    ) -> CriticalNoteModel:
        """Add a new critical note to a project.
        
        Args:
            project_id: The project ID.
            source_agent: The agent adding the note.
            category: Note category.
            priority: Note priority.
            summary: Note summary text.
            reference: Optional reference URL/path.
            
        Returns:
            The created CriticalNoteModel instance.
        """
        now = datetime.now(timezone.utc).isoformat()
        with Session(self.engine) as session:
            note = CriticalNoteModel(
                project_id=project_id,
                source_agent=source_agent,
                category=category,
                priority=priority,
                summary=summary,
                reference=reference,
                created_at=now,
                updated_at=now,
            )
            session.add(note)
            session.commit()
            session.refresh(note)
            return note

    def get_critical_note(self, project_id: str, entry_id: str) -> CriticalNoteModel | None:
        """Get a specific critical note by ID.
        
        Args:
            project_id: The project ID.
            entry_id: The note entry ID.
            
        Returns:
            The CriticalNoteModel if found and belongs to project, None otherwise.
        """
        with Session(self.engine) as session:
            note = session.get(CriticalNoteModel, entry_id)
            if note is None or note.project_id != project_id:
                return None
            return note

    def remove_critical_note(self, project_id: str, entry_id: str) -> bool:
        """Remove a critical note by ID.
        
        Args:
            project_id: The project ID.
            entry_id: The note entry ID to remove.
            
        Returns:
            True if removed, False if not found.
        """
        with Session(self.engine) as session:
            note = session.get(CriticalNoteModel, entry_id)
            if note is None or note.project_id != project_id:
                return False
            session.delete(note)
            session.commit()
            return True

    # Allowed fields for update (security: prevent overwriting id/project_id)
    _ALLOWED_UPDATES = {"source_agent", "category", "priority", "summary", "reference"}

    def update_critical_note(
        self,
        project_id: str,
        entry_id: str,
        **updates,
    ) -> CriticalNoteModel | None:
        """Update a critical note's fields.
        
        Args:
            project_id: The project ID.
            entry_id: The note entry ID to update.
            **updates: Fields to update (source_agent, category, priority, summary, reference).
            
        Returns:
            The updated CriticalNoteModel if found, None otherwise.
        """
        with Session(self.engine) as session:
            note = session.get(CriticalNoteModel, entry_id)
            if note is None or note.project_id != project_id:
                return None
            
            now = datetime.now(timezone.utc).isoformat()
            for key, value in updates.items():
                if key in self._ALLOWED_UPDATES and value is not None:
                    setattr(note, key, value)
            note.updated_at = now
            
            session.commit()
            session.refresh(note)
            return note

    def count_critical_notes(self, project_id: str) -> int:
        """Count critical notes for a project.
        
        Args:
            project_id: The project ID.
            
        Returns:
            Number of critical notes.
        """
        with Session(self.engine) as session:
            count = session.exec(
                select(func.count())
                .select_from(CriticalNoteModel)
                .where(CriticalNoteModel.project_id == project_id)
            ).one()
            return count
