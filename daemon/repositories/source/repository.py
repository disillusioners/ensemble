"""SQLModel-based Source Repository implementation."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import delete as sql_delete, insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select, col

from .models import SourceConfig, InstanceMapping, ProcessedMessage, ScheduleExecution, SourceStatus


logger = logging.getLogger(__name__)


class SQLModelSourceRepository:
    """SQLModel-based Source repository for source configs, instance mappings, and message deduplication."""
    
    def __init__(self, engine: Engine):
        """Initialize repository with a database engine."""
        self.engine = engine

    # ==================== Source Config Operations ====================

    def create_source_config(
        self,
        source_type: str,
        name: str,
        config: dict[str, Any],
        credentials: Optional[str] = None,
        enabled: bool = True,
        source_id: Optional[str] = None,
    ) -> SourceConfig:
        """Create a new source configuration."""
        with Session(self.engine) as session:
            now = datetime.utcnow().isoformat()
            source_id = source_id or str(uuid.uuid4())
            
            source_config = SourceConfig(
                source_id=source_id,
                source_type=source_type,
                name=name,
                config=config,
                credentials=credentials,
                enabled=enabled,
                status=SourceStatus.STOPPED.value,
                error_message=None,
                created_at=now,
                updated_at=now,
            )
            
            session.add(source_config)
            session.commit()
            session.refresh(source_config)
            
            logger.info(f"Created source config: source_id={source_id}, name={name}")
            return source_config

    def update_source_config(
        self,
        source_id: str,
        source_type: Optional[str] = None,
        name: Optional[str] = None,
        config: Optional[dict[str, Any]] = None,
        credentials: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> SourceConfig | None:
        """Update a source configuration."""
        with Session(self.engine) as session:
            source_config = session.get(SourceConfig, source_id)
            if source_config is None:
                return None
            
            if source_type is not None:
                source_config.source_type = source_type
            if name is not None:
                source_config.name = name
            if config is not None:
                source_config.config = config
            if credentials is not None:
                source_config.credentials = credentials
            if enabled is not None:
                source_config.enabled = enabled
            
            source_config.updated_at = datetime.utcnow().isoformat()
            session.commit()
            session.refresh(source_config)
            
            logger.info(f"Updated source config: source_id={source_id}")
            return source_config

    def increment_scheduler_run_counter(self, source_id: str) -> int | None:
        """Atomically increment and return the scheduler run counter for a source.
        
        The counter is stored in the source's config field (_run_counter) so it persists
        even if sessions crash. Initializes to 0 if not present.
        
        Args:
            source_id: The source ID to increment the counter for.
            
        Returns:
            The new counter value, or None if the source was not found.
        """
        with Session(self.engine) as session:
            source_config = session.get(SourceConfig, source_id)
            if source_config is None:
                logger.warning(f"Source not found for run counter increment: source_id={source_id}")
                return None
            
            # Get current counter from config, initialize to 0 if not present
            current_counter = source_config.config.get("_run_counter", 0)
            new_counter = current_counter + 1
            
            # Update the config with new counter value
            source_config.config["_run_counter"] = new_counter
            source_config.updated_at = datetime.utcnow().isoformat()
            
            session.commit()
            
            logger.debug(f"Incremented run counter: source_id={source_id}, new_value={new_counter}")
            return new_counter

    def get_source_config(self, source_id: str) -> SourceConfig | None:
        """Get a source configuration by source_id."""
        with Session(self.engine) as session:
            return session.get(SourceConfig, source_id)

    def get_source_config_by_name(self, name: str) -> SourceConfig | None:
        """Get a source configuration by name."""
        with Session(self.engine) as session:
            stmt = select(SourceConfig).where(SourceConfig.name == name)
            return session.exec(stmt).first()

    def list_source_configs(
        self,
        enabled: Optional[bool] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SourceConfig]:
        """List source configurations with optional filters."""
        with Session(self.engine) as session:
            stmt = select(SourceConfig)
            
            if enabled is not None:
                stmt = stmt.where(SourceConfig.enabled == enabled)
            if status is not None:
                stmt = stmt.where(SourceConfig.status == status)
            
            stmt = stmt.order_by(col(SourceConfig.created_at).desc()).offset(offset).limit(limit)
            return list(session.exec(stmt))

    def update_source_status(
        self,
        source_id: str,
        status: str,
        error_message: Optional[str] = None,
    ) -> SourceConfig | None:
        """Update the status of a source configuration."""
        with Session(self.engine) as session:
            source_config = session.get(SourceConfig, source_id)
            if source_config is None:
                logger.warning(f"Source not found for status update: source_id={source_id}")
                return None
            
            if not SourceStatus.is_valid(status):
                raise ValueError(f"Invalid status: {status}")
            
            source_config.status = status
            source_config.error_message = error_message
            source_config.updated_at = datetime.utcnow().isoformat()
            
            session.commit()
            session.refresh(source_config)
            
            logger.info(f"Updated source status: source_id={source_id}, status={status}")
            return source_config

    def delete_source_config(self, source_id: str) -> dict[str, Any]:
        """Delete a source configuration and all associated mappings."""
        with Session(self.engine) as session:
            source_config = session.get(SourceConfig, source_id)
            if source_config is None:
                logger.warning(f"Source config not found for deletion: source_id={source_id}")
                return {"deleted": False, "source_id": source_id, "error": "Not found"}
            
            # Delete all mappings for this source
            session.exec(
                sql_delete(InstanceMapping).where(InstanceMapping.source_id == source_id)
            )
            
            # Delete processed messages for this source
            session.exec(
                sql_delete(ProcessedMessage).where(ProcessedMessage.source_id == source_id)
            )
            
            # Delete schedule executions for this source
            session.exec(
                sql_delete(ScheduleExecution).where(ScheduleExecution.schedule_id == source_id)
            )
            
            # Delete source config
            session.delete(source_config)
            session.commit()
            
            logger.info(f"Deleted source config and associated data: source_id={source_id}")
            return {
                "deleted": True,
                "source_id": source_id,
                "name": source_config.name
            }

    # ==================== Instance Mapping Operations ====================

    def create_instance_mapping(
        self,
        source_id: str,
        external_user_id: str,
        agent_instance_id: str,
        agent_id: str,
        agent_dir: str,
        metadata: Optional[dict[str, Any]] = None,
        mapping_id: Optional[str] = None,
    ) -> InstanceMapping:
        """Create or update an instance mapping."""
        with Session(self.engine) as session:
            now = datetime.utcnow().isoformat()
            mapping_id = mapping_id or str(uuid.uuid4())
            
            # Check if mapping exists (upsert logic)
            existing = session.exec(
                select(InstanceMapping).where(
                    InstanceMapping.source_id == source_id,
                    InstanceMapping.external_user_id == external_user_id
                )
            ).first()
            
            if existing:
                # Update existing mapping
                existing.agent_instance_id = agent_instance_id
                existing.agent_id = agent_id
                existing.agent_dir = agent_dir
                existing.mapping_metadata = metadata or {}
                existing.last_message_at = now
                session.commit()
                session.refresh(existing)
                logger.info(
                    f"Updated instance mapping: mapping_id={existing.mapping_id}, "
                    f"source_id={source_id}, external_user_id={external_user_id}"
                )
                return existing
            
            # Create new mapping
            mapping = InstanceMapping(
                mapping_id=mapping_id,
                source_id=source_id,
                external_user_id=external_user_id,
                agent_instance_id=agent_instance_id,
                agent_id=agent_id,
                agent_dir=agent_dir,
                mapping_metadata=metadata or {},
                last_message_at=now,
                created_at=now,
            )
            
            session.add(mapping)
            session.commit()
            session.refresh(mapping)
            
            logger.info(
                f"Created instance mapping: mapping_id={mapping_id}, "
                f"source_id={source_id}, external_user_id={external_user_id}"
            )
            return mapping

    def get_instance_mapping(
        self,
        source_id: str,
        external_user_id: str,
    ) -> InstanceMapping | None:
        """Get an instance mapping by source_id and external_user_id."""
        with Session(self.engine) as session:
            stmt = select(InstanceMapping).where(
                InstanceMapping.source_id == source_id,
                InstanceMapping.external_user_id == external_user_id
            )
            return session.exec(stmt).first()

    def get_instance_mapping_by_instance(
        self,
        agent_instance_id: str,
    ) -> InstanceMapping | None:
        """Get an instance mapping by agent_instance_id."""
        with Session(self.engine) as session:
            stmt = select(InstanceMapping).where(
                InstanceMapping.agent_instance_id == agent_instance_id
            )
            return session.exec(stmt).first()

    def update_mapping_last_message(
        self,
        source_id: str,
        external_user_id: str,
    ) -> bool:
        """Update the last_message_at timestamp for an instance mapping."""
        with Session(self.engine) as session:
            mapping = session.exec(
                select(InstanceMapping).where(
                    InstanceMapping.source_id == source_id,
                    InstanceMapping.external_user_id == external_user_id
                )
            ).first()
            if mapping is None:
                return False
            
            mapping.last_message_at = datetime.utcnow().isoformat()
            session.commit()
            
            logger.debug(
                f"Updated last_message_at: source_id={source_id}, external_user_id={external_user_id}"
            )
            return True

    def delete_instance_mapping(self, mapping_id: str) -> dict[str, Any]:
        """Delete an instance mapping."""
        with Session(self.engine) as session:
            mapping = session.get(InstanceMapping, mapping_id)
            if mapping is None:
                logger.warning(f"Instance mapping not found for deletion: mapping_id={mapping_id}")
                return {"deleted": False, "mapping_id": mapping_id, "error": "Not found"}
            
            session.delete(mapping)
            session.commit()
            
            logger.info(f"Deleted instance mapping: mapping_id={mapping_id}")
            return {"deleted": True, "mapping_id": mapping_id}

    def list_instance_mappings(
        self,
        source_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[InstanceMapping]:
        """List all instance mappings for a source."""
        with Session(self.engine) as session:
            stmt = (
                select(InstanceMapping)
                .where(InstanceMapping.source_id == source_id)
                .order_by(col(InstanceMapping.last_message_at).desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.exec(stmt))

    def cleanup_inactive_mappings(
        self,
        max_age_days: int = 30,
    ) -> int:
        """Clean up inactive instance mappings older than max_age_days."""
        with Session(self.engine) as session:
            cutoff_time = datetime.utcnow() - timedelta(days=max_age_days)
            cutoff_str = cutoff_time.isoformat()
            
            # Find inactive mappings
            stmt = select(InstanceMapping).where(
                ((InstanceMapping.last_message_at == None) & (InstanceMapping.created_at < cutoff_str))
                | ((InstanceMapping.last_message_at != None) & (InstanceMapping.last_message_at < cutoff_str))
            )
            inactive_mappings = list(session.exec(stmt))
            
            # Delete them
            for mapping in inactive_mappings:
                session.delete(mapping)
            
            session.commit()
            
            deleted_count = len(inactive_mappings)
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} inactive instance mappings older than {max_age_days}d")
            
            return deleted_count

    # ==================== Deduplication Operations ====================

    def check_and_mark_processed(
        self,
        source_id: str,
        external_message_id: str,
    ) -> bool:
        """Check if a message has already been processed and mark it as processed.
        
        Uses atomic check-and-insert with UNIQUE constraint.
        
        Note: This is an atomic operation - if INSERT succeeds, the message
        is marked as processed. If the caller fails to handle the message,
        it will be permanently dropped. Callers must handle their own errors
        before calling this if different behavior is needed.
        
        Returns:
            True if message was already processed (duplicate).
            False if this is a new message (and now marked as processed).
        """
        with Session(self.engine) as session:
            now = datetime.utcnow().isoformat()
            
            processed = ProcessedMessage(
                source_id=source_id,
                external_message_id=external_message_id,
                processed_at=now,
            )
            
            try:
                session.add(processed)
                session.commit()
                return False  # New message, not a duplicate
            except IntegrityError:
                # Unique constraint violation - message already exists
                session.rollback()
                logger.debug(
                    f"Duplicate message detected: source_id={source_id}, "
                    f"external_message_id={external_message_id}"
                )
                return True  # Duplicate

    def cleanup_old_processed_messages(
        self,
        max_age_hours: int = 24,
    ) -> int:
        """Clean up processed messages older than max_age_hours."""
        with Session(self.engine) as session:
            cutoff_time = datetime.utcnow() - timedelta(hours=max_age_hours)
            cutoff_str = cutoff_time.isoformat()
            
            # Find messages to delete first
            count_stmt = select(ProcessedMessage).where(
                ProcessedMessage.processed_at < cutoff_str
            )
            messages_to_delete = list(session.exec(count_stmt))
            deleted_count = len(messages_to_delete)
            
            # Delete them
            stmt = sql_delete(ProcessedMessage).where(
                ProcessedMessage.processed_at < cutoff_str
            )
            session.exec(stmt)
            session.commit()
            
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} processed messages older than {max_age_hours}h")
            
            return deleted_count

    # ==================== Schedule Execution Operations ====================

    def record_execution_start(
        self,
        schedule_id: str,
        session_id: Optional[str] = None,
        execution_id: Optional[str] = None,
    ) -> ScheduleExecution:
        """Record a new execution with status 'triggered'.
        
        Args:
            schedule_id: The schedule that triggered this execution
            session_id: Optional session ID associated with the execution
            execution_id: Optional execution ID (generated if not provided)
        """
        with Session(self.engine) as session:
            execution = ScheduleExecution(
                schedule_id=schedule_id,
                session_id=session_id,
                status="triggered",
            )
            
            # Use provided execution_id if available
            if execution_id:
                execution.execution_id = execution_id
            
            session.add(execution)
            session.commit()
            session.refresh(execution)
            
            logger.info(f"Recorded execution start: execution_id={execution.execution_id}, schedule_id={schedule_id}")
            return execution

    def record_execution_complete(
        self,
        execution_id: str,
        status: str = "completed",
        error_message: Optional[str] = None,
    ) -> Optional[ScheduleExecution]:
        """Update execution status to completed or failed."""
        with Session(self.engine) as session:
            execution = session.get(ScheduleExecution, execution_id)
            if execution is None:
                logger.warning(f"Execution not found for update: execution_id={execution_id}")
                return None
            
            execution.status = status
            execution.error_message = error_message
            execution.completed_at = datetime.utcnow().isoformat()
            
            session.commit()
            session.refresh(execution)
            
            logger.info(f"Recorded execution completion: execution_id={execution_id}, status={status}")
            return execution

    def list_schedule_executions(
        self,
        schedule_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScheduleExecution]:
        """List executions for a schedule."""
        with Session(self.engine) as session:
            stmt = (
                select(ScheduleExecution)
                .where(ScheduleExecution.schedule_id == schedule_id)
                .order_by(col(ScheduleExecution.triggered_at).desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.exec(stmt))

    def get_latest_execution(self, schedule_id: str) -> Optional[ScheduleExecution]:
        """Get the most recent execution for a schedule."""
        with Session(self.engine) as session:
            stmt = (
                select(ScheduleExecution)
                .where(ScheduleExecution.schedule_id == schedule_id)
                .order_by(col(ScheduleExecution.triggered_at).desc())
                .limit(1)
            )
            return session.exec(stmt).first()

    def get_running_executions(self, schedule_id: str) -> list[ScheduleExecution]:
        """Get currently running executions (status='triggered' but not completed)."""
        with Session(self.engine) as session:
            stmt = (
                select(ScheduleExecution)
                .where(
                    ScheduleExecution.schedule_id == schedule_id,
                    ScheduleExecution.status == "triggered",
                )
                .order_by(col(ScheduleExecution.triggered_at).desc())
            )
            return list(session.exec(stmt))
