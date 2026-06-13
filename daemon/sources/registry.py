"""Source registry for managing message source adapters."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from .base import (
    IncomingMessage,
    MessageSourceAdapter,
    SourceConfig,
    SourceStatus,
)
from .mapper import InstanceMapper

if TYPE_CHECKING:
    from daemon.services.job_queue_service import JobQueueService

logger = logging.getLogger(__name__)

# Module-level executor for async-safe callbacks (P2 Issue #7)
_executor = ThreadPoolExecutor(max_workers=4)


class SourceRegistry:
    """Registry for managing message source adapters.
    
    Provides lifecycle management for all registered adapters including:
    - Registration/unregistration
    - Start/stop operations
    - Supervised execution with exponential backoff
    - Database persistence integration
    """
    
    ADAPTER_START_TIMEOUT = 60.0  # seconds to wait for adapter.start()
    AUTOSTART_DELAY_SECONDS = 60.0  # delay before auto-starting sources on service boot
    
    def __init__(self, source_repo, manager, job_queue_service: "JobQueueService" | None = None, instance_repo=None):
        """Initialize the source registry.
        
        Args:
            source_repo: SQLModelSourceRepository for database operations.
            manager: InstanceManager reference for handling messages.
            job_queue_service: Optional JobQueueService for scheduler queue routing.
            instance_repo: Optional InstanceRepository for scheduler instance mode.
        """
        self._source_repo = source_repo
        self._manager = manager
        self._job_queue_service = job_queue_service
        self._instance_repo = instance_repo
        self._adapters: dict[str, MessageSourceAdapter] = {}
        self._supervisor_tasks: dict[str, asyncio.Task] = {}
        self._running: dict[str, bool] = {}  # Track running state for each adapter
        self._autostart_tasks: dict[str, asyncio.Task] = {}  # Pending delayed autostart tasks
        self._stopping: bool = False
    
    def register(self, adapter: MessageSourceAdapter) -> None:
        """Register an adapter with the registry.
        
        Args:
            adapter: The MessageSourceAdapter to register.
            
        Raises:
            ValueError: If an adapter with the same source_id is already registered.
        """
        source_id = adapter.source_id
        if source_id in self._adapters:
            raise ValueError(f"Adapter already registered: {source_id}")
        
        self._adapters[source_id] = adapter
        self._running[source_id] = False
        logger.info(f"Registered adapter: source_id={source_id}, type={adapter.source_type}")
    
    def unregister(self, source_id: str) -> bool:
        """Unregister an adapter from the registry.
        
        Args:
            source_id: The source_id of the adapter to unregister.
            
        Returns:
            True if adapter was unregistered, False if not found.
        """
        if source_id not in self._adapters:
            logger.warning(f"Adapter not found for unregistration: {source_id}")
            return False
        
        adapter = self._adapters[source_id]
        
        # Stop the adapter if running (P1 Issue #5)
        if self._running.get(source_id, False):
            try:
                # Create task to stop adapter without blocking
                asyncio.create_task(self._stop_adapter_async(adapter, source_id))
            except Exception as e:
                logger.warning(f"Error initiating adapter stop during unregister: {e}")
        
        # Cancel supervisor task if running
        if source_id in self._supervisor_tasks:
            task = self._supervisor_tasks.pop(source_id)
            if not task.done():
                task.cancel()
                logger.debug(f"Cancelled supervisor task for: {source_id}")
        
        # Cancel any pending delayed autostart task
        autostart_task = self._autostart_tasks.pop(source_id, None)
        if autostart_task and not autostart_task.done():
            autostart_task.cancel()
            logger.debug(f"Cancelled pending autostart task for: {source_id}")
        
        del self._adapters[source_id]
        self._running.pop(source_id, None)
        logger.info(f"Unregistered adapter: source_id={source_id}")
        return True
    
    async def _stop_adapter_async(self, adapter: MessageSourceAdapter, source_id: str) -> None:
        """Helper to stop adapter asynchronously."""
        try:
            await adapter.stop()
            logger.debug(f"Stopped adapter during unregister: {source_id}")
        except Exception as e:
            logger.warning(f"Error stopping adapter {source_id} during unregister: {e}")
    
    def get(self, source_id: str) -> MessageSourceAdapter | None:
        """Get an adapter by source_id.
        
        Args:
            source_id: The source_id to look up.
            
        Returns:
            The MessageSourceAdapter if found, None otherwise.
        """
        return self._adapters.get(source_id)
    
    def list_adapters(self) -> list[dict]:
        """List all registered adapters with their status information.
        
        Returns:
            List of adapter info dictionaries containing source_id, type, status, etc.
        """
        result = []
        for source_id, adapter in self._adapters.items():
            result.append({
                "source_id": source_id,
                "source_type": adapter.source_type,
                "name": adapter.config.name,
                "enabled": adapter.config.enabled,
                "status": adapter.status.value,
                "error": adapter.error,
            })
        return result
    
    async def start_all(self) -> None:
        """Load all enabled adapters from database and start them.
        
        Loads source configurations from the database, creates adapters
        if not registered, and schedules auto-start for all enabled,
        autostart sources. Auto-started sources are delayed by
        AUTOSTART_DELAY_SECONDS to let the service settle on boot.
        """
        logger.info("Loading and scheduling auto-start for enabled adapters...")
        self._stopping = False
        
        # Load configs from database
        configs = await asyncio.to_thread(self._source_repo.list_source_configs)
        
        scheduled_count = 0
        for config in configs:
            if not config.enabled:
                logger.debug(f"Skipping disabled source: {config.source_id}")
                continue
            
            # Only auto-start sources with the autostart flag enabled
            if not getattr(config, "autostart", True):
                logger.debug(f"Skipping non-autostart source: {config.source_id}")
                continue
            
            # Skip sources that were explicitly stopped (manual stop persists
            # across reboots regardless of the autostart flag)
            if config.status == SourceStatus.STOPPED.value:
                logger.debug(f"Skipping stopped source: {config.source_id}")
                continue
            
            source_id = config.source_id
            
            # Check if adapter is already registered
            adapter = self.get(source_id)
            if adapter is None:
                # Create adapter from config
                logger.info(f"Creating adapter for: {source_id}")
                try:
                    adapter = await self._create_adapter_from_config(config)
                    if adapter:
                        self.register(adapter)
                        logger.info(f"Registered adapter: {source_id}")
                    else:
                        logger.warning(f"Could not create adapter for: {source_id}")
                        continue
                except Exception as e:
                    logger.error(f"Failed to create adapter {source_id}: {e}")
                    continue
            
            # Schedule a delayed auto-start (1 minute after service boot)
            self._schedule_autostart(source_id)
            scheduled_count += 1
        
        logger.info(
            f"Scheduled auto-start for {scheduled_count} adapters "
            f"(delayed {self.AUTOSTART_DELAY_SECONDS}s)"
        )

    def _schedule_autostart(self, source_id: str) -> None:
        """Schedule a delayed auto-start for a source.
        
        Args:
            source_id: The source_id to auto-start after the delay.
        """
        # Cancel any previously pending autostart for this source
        existing = self._autostart_tasks.pop(source_id, None)
        if existing and not existing.done():
            existing.cancel()
        
        task = asyncio.create_task(self._delayed_start(source_id))
        self._autostart_tasks[source_id] = task
        logger.info(
            f"Scheduled auto-start for {source_id} in {self.AUTOSTART_DELAY_SECONDS}s"
        )

    async def _delayed_start(self, source_id: str) -> None:
        """Auto-start a source after the configured delay.
        
        Guarded against shutdown: if the registry is stopping or the
        source was already started/removed, this is a no-op.
        """
        try:
            await asyncio.sleep(self.AUTOSTART_DELAY_SECONDS)
        except asyncio.CancelledError:
            self._autostart_tasks.pop(source_id, None)
            raise
        
        self._autostart_tasks.pop(source_id, None)
        
        if self._stopping:
            logger.info(f"Skipping auto-start during shutdown: {source_id}")
            return
        
        adapter = self.get(source_id)
        if adapter is None:
            logger.warning(f"Skipping auto-start, adapter no longer registered: {source_id}")
            return
        
        if self._running.get(source_id, False):
            logger.debug(f"Skipping auto-start, already running: {source_id}")
            return
        
        try:
            await self.start_adapter(source_id)
        except Exception as e:
            logger.error(f"Failed to auto-start adapter {source_id}: {e}")
    
    async def _create_adapter_from_config(self, config):
        """Create an adapter instance from a config.
        
        Args:
            config: Source configuration (SourceConfig model or dict) from database.
            
        Returns:
            MessageSourceAdapter instance or None if unsupported type.
        """
        from .base import SourceConfig
        from .credentials import CredentialManager
        
        # Convert SQLModel object to dict if needed
        if hasattr(config, 'source_type'):  # It's a SQLModel object
            config_dict = {
                "source_id": config.source_id,
                "source_type": config.source_type,
                "name": config.name,
                "config": config.config,
                "credentials": config.credentials,
                "enabled": config.enabled,
            }
        else:
            config_dict = config
        
        source_type = config_dict["source_type"]
        source_id = config_dict["source_id"]
        
        # Decrypt credentials if needed
        credentials = config_dict.get("credentials", {})
        if credentials and isinstance(credentials, str):
            cred_manager = CredentialManager()
            credentials = cred_manager.decrypt(credentials)
        
        # Validate credentials is a dict (not a string from malformed JSON)
        if credentials is not None and not isinstance(credentials, dict):
            logger.error(
                f"Invalid credentials format for {source_id}: expected dict, got {type(credentials).__name__}. "
                f"Credentials should be stored as JSON object, e.g., {{\"bot_token\": \"...\"}}"
            )
            raise ValueError(
                f"Invalid credentials format: expected dict, got {type(credentials).__name__}. "
                f"Store credentials as JSON object."
            )
        
        # Validate config is a dict (not a string from malformed JSON)
        config_data = config_dict.get("config", {})
        if config_data is not None and not isinstance(config_data, dict):
            logger.error(
                f"Invalid config format for {source_id}: expected dict, got {type(config_data).__name__}"
            )
            raise ValueError(
                f"Invalid config format: expected dict, got {type(config_data).__name__}"
            )
        
        config = SourceConfig(
            source_id=source_id,
            source_type=source_type,
            name=config_dict["name"],
            config=config_data,
            credentials=credentials,
            enabled=config_dict.get("enabled", True),
        )

        logger.info(f"Creating adapter for {source_id}: config={config.config}")
        
        # Create callback wrapper that includes source_id
        async def on_message(msg):
            try:
                priority = int(msg.metadata.get("priority", 1))
            except (ValueError, TypeError):
                priority = 1
            await self._handle_message(source_id, msg, priority=priority)
        
        # Create the appropriate adapter
        if source_type == "telegram":
            from .adapters.telegram import TelegramAdapter
            adapter = TelegramAdapter(config, on_message)
            logger.info(f"TelegramAdapter created: default_agent={adapter._default_agent}")
            return adapter
        elif source_type == "scheduler":
            from .adapters.scheduler import SchedulerAdapter
            
            # Thread-safe wrapper for execution callback (P2 Issue #7)
            def _safe_sync_callback(
                repo,
                execution_id: str,
                schedule_id: str,
                status: str,
                instance_id: str | None = None,
                error_message: str | None = None,
            ):
                """Thread-safe wrapper for execution callback."""
                try:
                    if status == "triggered":
                        repo.record_execution_start(
                            schedule_id=schedule_id,
                            instance_id=instance_id,
                            execution_id=execution_id,
                        )
                    elif status in ("completed", "failed", "skipped", "queued"):
                        repo.record_execution_complete(
                            execution_id=execution_id,
                            status=status,
                            error_message=error_message,
                        )
                except Exception as e:
                    logger.error(f"Failed to record execution status: {e}")
            
            def execution_callback(
                execution_id: str,
                schedule_id: str,
                status: str,
                instance_id: str | None = None,
                error_message: str | None = None,
            ):
                """Sync callback - run in thread pool to avoid blocking."""
                loop = asyncio.get_running_loop()
                loop.run_in_executor(
                    _executor,
                    _safe_sync_callback,
                    self._source_repo,
                    execution_id, schedule_id, status, instance_id, error_message
                )
            
            def on_complete_callback(source_id: str, completed: bool):
                """Disable scheduler after one-time execution completes.
                
                This callback is invoked by SchedulerAdapter when a one-time
                schedule finishes execution. We disable the source so it
                won't run again.
                """
                try:
                    self._source_repo.update_source_status(source_id, "stopped")
                    self._source_repo.update_source_config(source_id, enabled=False)
                    logger.info(f"Disabled one-time scheduler after completion: {source_id}")
                except Exception as e:
                    logger.error(f"Failed to disable scheduler {source_id}: {e}")
            
            # Pass JobQueueService, SourceRepository, and InstanceRepository for queue routing and instance mode (Tasks 5.4 & 6)
            adapter = SchedulerAdapter(
                config,
                on_message,
                execution_callback,
                on_complete_callback=on_complete_callback,
                job_queue_service=self._job_queue_service,
                source_repo=self._source_repo,
                instance_repo=self._instance_repo,
            )
            logger.info(f"SchedulerAdapter created: type={adapter._schedule_type}, agent={adapter._agent}")
            return adapter
        elif source_type == "slack":
            from .adapters.slack import SlackAdapter
            adapter = SlackAdapter(config, on_message, manager=self._manager)
            # Inject source_repo for DB lookup during send()
            adapter._source_repo = self._source_repo
            logger.info(f"SlackAdapter created: default_agent={adapter._default_agent}")
            return adapter
        else:
            logger.warning(f"Unsupported source type: {source_type}")
            return None
    
    async def stop_all(self) -> None:
        """Stop all running adapters gracefully.
        
        Cancels all pending autostart tasks, supervisor tasks, and stops
        all adapters.
        """
        logger.info("Stopping all adapters...")
        self._stopping = True
        
        # Cancel any pending delayed autostart tasks
        autostart_ids = list(self._autostart_tasks.keys())
        for source_id in autostart_ids:
            task = self._autostart_tasks.pop(source_id, None)
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if autostart_ids:
            logger.info(f"Cancelled {len(autostart_ids)} pending autostart tasks")
        
        # Copy keys since we may modify during iteration
        source_ids = list(self._supervisor_tasks.keys())
        
        for source_id in source_ids:
            await self.stop_adapter(source_id, persist_status=False)
        
        logger.info("All adapters stopped")
    
    async def start_adapter(self, source_id: str) -> bool:
        """Start a specific adapter in supervisor mode.
        
        Args:
            source_id: The source_id of the adapter to start.
            
        Returns:
            True if started successfully, False if adapter not found.
        """
        adapter = self.get(source_id)
        if adapter is None:
            logger.error(f"Adapter not found: {source_id}")
            return False
        
        # Check if already running
        if self._running.get(source_id, False):
            logger.warning(f"Adapter already running: {source_id}")
            return True
        
        # Update status
        adapter._status = SourceStatus.STARTING
        await asyncio.to_thread(self._source_repo.update_source_status, source_id, SourceStatus.STARTING.value)
        
        # Start supervisor task
        self._running[source_id] = True
        task = asyncio.create_task(self._run_adapter_safe(adapter))
        self._supervisor_tasks[source_id] = task
        
        logger.info(f"Started adapter: {source_id}")
        return True
    
    async def stop_adapter(self, source_id: str, persist_status: bool = True) -> bool:
        """Stop a specific adapter gracefully.

        Args:
            source_id: The source_id of the adapter to stop.
            persist_status: When False, do not write status='stopped' to the DB.
                Used by stop_all during shutdown so that the next boot can still
                auto-start sources that were running before shutdown.

        Returns:
            True if stopped successfully, False if adapter not found.
        """
        adapter = self.get(source_id)
        if adapter is None:
            logger.error(f"Adapter not found: {source_id}")
            return False
        
        # Mark as not running to signal supervisor to stop
        self._running[source_id] = False
        
        # Cancel any pending delayed autostart so a manual stop within the
        # boot autostart window does not resurrect the source later
        autostart_task = self._autostart_tasks.pop(source_id, None)
        if autostart_task and not autostart_task.done():
            autostart_task.cancel()
            try:
                await autostart_task
            except asyncio.CancelledError:
                pass
        
        # Cancel supervisor task if exists
        task = self._supervisor_tasks.pop(source_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Stop the adapter
        try:
            await adapter.stop()
        except Exception as e:
            logger.error(f"Error stopping adapter {source_id}: {e}")
        
        # Update status (skipped during bulk shutdown so running sources stay
        # runnable across restarts; only explicit user stops persist 'stopped')
        adapter._status = SourceStatus.STOPPED
        if persist_status:
            await asyncio.to_thread(self._source_repo.update_source_status, source_id, SourceStatus.STOPPED.value)
        
        logger.info(f"Stopped adapter: {source_id}")
        return True
    
    async def reload_adapter(self, source_id: str) -> bool:
        """Reload configuration and restart an adapter.
        
        Args:
            source_id: The source_id of the adapter to reload.
            
        Returns:
            True if reloaded successfully, False if adapter not found or reload failed.
        """
        adapter = self.get(source_id)
        if adapter is None:
            logger.error(f"Adapter not found: {source_id}")
            return False
        
        # Reload config from database
        config_dict = await asyncio.to_thread(self._source_repo.get_source_config, source_id)
        if config_dict is None:
            logger.error(f"Config not found in database: {source_id}")
            return False
        
        # Stop the adapter first
        await self.stop_adapter(source_id)
        
        # Create new config
        new_config = SourceConfig(
            source_id=config_dict["source_id"],
            source_type=config_dict["source_type"],
            name=config_dict["name"],
            config=config_dict.get("config", {}),
            credentials=config_dict.get("credentials", {}),
            enabled=config_dict.get("enabled", True),
        )
        
        # Update adapter config
        adapter.config = new_config
        
        # Start again
        await self.start_adapter(source_id)
        
        logger.info(f"Reloaded adapter: {source_id}")
        return True
    
    async def _run_adapter_safe(self, adapter: MessageSourceAdapter) -> None:
        """Supervisor loop for an adapter with exponential backoff.
        
        Handles adapter lifecycle with automatic restart on crash,
        exponential backoff for repeated failures, and graceful shutdown.
        
        Args:
            adapter: The adapter to supervise.
        """
        source_id = adapter.source_id
        
        # Backoff parameters
        backoff: float = 2.0  # initial backoff in seconds
        max_backoff: float = 300.0  # max backoff in seconds
        multiplier: float = 2.5
        last_success_time: float = 0.0
        success_threshold: float = 60.0  # reset backoff after 60s of successful run
        
        while self._running.get(source_id, False):
            try:
                # Start the adapter
                logger.info(f"Starting adapter: {source_id}")
                adapter._status = SourceStatus.STARTING
                await asyncio.to_thread(self._source_repo.update_source_status, source_id, SourceStatus.STARTING.value)
                
                # Add timeout to start() to detect hung adapters
                try:
                    await asyncio.wait_for(adapter.start(), timeout=self.ADAPTER_START_TIMEOUT)
                except asyncio.TimeoutError:
                    raise TimeoutError(f"Adapter start() timed out after {self.ADAPTER_START_TIMEOUT}s")
                
                # Adapter started successfully
                adapter._status = SourceStatus.RUNNING
                adapter._error = None
                await asyncio.to_thread(self._source_repo.update_source_status, source_id, SourceStatus.RUNNING.value)
                logger.info(f"Adapter running: {source_id}")
                
                # Record success and reset backoff
                last_success_time = time.monotonic()
                backoff = 2.0
                
                # Keep the adapter running - wait until stopped or error
                while self._running.get(source_id, False):
                    try:
                        # Periodic health check
                        if not await adapter.health_check():
                            logger.warning(f"Health check failed for: {source_id}")
                            raise Exception("Health check failed")
                        
                        await asyncio.sleep(5)  # Check every 5 seconds
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.error(f"Error in adapter run loop {source_id}: {e}")
                        break
                
            except asyncio.CancelledError:
                # Graceful shutdown requested
                logger.info(f"Supervisor cancelled for adapter: {source_id}")
                break
                
            except Exception as e:
                logger.error(f"Adapter crashed {source_id}: {e}")
                adapter._status = SourceStatus.ERROR
                adapter._error = str(e)
                await asyncio.to_thread(self._source_repo.update_source_status, source_id, SourceStatus.ERROR.value, str(e))
                
                # Stop the adapter if still running
                try:
                    await adapter.stop()
                except Exception as stop_err:
                    logger.error(f"Error stopping crashed adapter {source_id}: {stop_err}")
                
                # Check if we should restart
                if not self._running.get(source_id, False):
                    break
                
                # Calculate backoff with jitter
                jitter = random.uniform(0.5, 1.5)
                sleep_time = min(backoff * jitter, max_backoff)
                
                logger.warning(
                    f"Restarting adapter {source_id} in {sleep_time:.1f}s "
                    f"(backoff={backoff:.1f}s)"
                )
                
                # Check if we should reset backoff based on successful runtime
                if last_success_time > 0:
                    elapsed = time.monotonic() - last_success_time
                    if elapsed >= success_threshold:
                        logger.info(
                            f"Resetting backoff for {source_id} after {elapsed:.1f}s "
                            f"successful run"
                        )
                        backoff = 2.0
                
                await asyncio.sleep(sleep_time)
                
                # Exponential backoff
                backoff = min(backoff * multiplier, max_backoff)
        
        # Clean up on exit (P2 Issue #8)
        self._running.pop(source_id, None)  # Clean up running state
        self._supervisor_tasks.pop(source_id, None)
        logger.info(f"Supervisor exiting for adapter: {source_id}")
    
    async def _handle_message(self, source_id: str, msg: IncomingMessage, priority: int = 1) -> None:
        """Handle incoming message from an adapter.
        
        This is the callback that adapters call when they receive messages.
        Forwards the message to the InstanceManager for processing.
        
        Args:
            source_id: The source_id the message came from.
            msg: The incoming message.
        """
        logger.debug(
            f"_handle_message called: source_id={source_id}, "
            f"user={msg.external_user_id}, content={msg.content[:50] if msg.content else None}..."
        )
        try:
            # Check for duplicates using the message_id from metadata
            # Try multiple locations for message_id (telegram stores in nested dict)
            external_msg_id = msg.metadata.get("message_id")
            if not external_msg_id and msg.metadata.get("telegram"):
                external_msg_id = msg.metadata.get("telegram", {}).get("message_id")
            
            logger.debug(f"Checking for duplicate: external_msg_id={external_msg_id}")
            
            if external_msg_id:
                is_dup = await asyncio.to_thread(self._source_repo.check_and_mark_processed, source_id, external_msg_id)
                if is_dup:
                    logger.info(
                        f"Skipping duplicate message: source_id={source_id}, "
                        f"external_id={external_msg_id}"
                    )
                    return
            
            # Get or create instance via InstanceMapper
            mapper = InstanceMapper(self._source_repo, self._manager)
            
            # Determine agent_dir from metadata or use default
            agent_dir = msg.metadata.get("agent_dir") if msg.metadata else None
            if not agent_dir:
                # Check if agent name is specified in metadata (e.g., from Telegram default_agent)
                agent_name = msg.metadata.get("agent") if msg.metadata else None
                if agent_name:
                    # Construct agent_dir from base directory + agent name
                    base_dir = self._manager.config.agents.directory
                    agent_dir = f"{base_dir}/{agent_name}"
                    logger.info(f"📍 Using agent from metadata: agent={agent_name}, agent_dir={agent_dir}")
                else:
                    # Fall back to a sane default agent name ("leader") rather than the
                    # bare base directory, which would fail to resolve to a registered
                    # agent and produce confusing "Agent not found: ./agents" errors.
                    base_dir = self._manager.config.agents.directory
                    default_agent_name = "leader"
                    agent_dir = f"{base_dir}/{default_agent_name}"
                    logger.warning(
                        f"⚠️ No agent specified in metadata, using default agent: {agent_dir}"
                    )
            else:
                logger.debug(f"Using explicit agent_dir: {agent_dir}")
            
            # Check for force_new_instance flag (e.g., /new command)
            force_new = msg.metadata.get("force_new_instance", False) if msg.metadata else False
            command = msg.metadata.get("command") if msg.metadata else None
            
            if force_new:
                # Delete existing mapping if exists
                existing_mapping = await asyncio.to_thread(self._source_repo.get_instance_mapping, source_id, msg.external_user_id)
                if existing_mapping:
                    mapping_id = existing_mapping.mapping_id
                    old_instance_id = existing_mapping.agent_instance_id
                    await asyncio.to_thread(self._source_repo.delete_instance_mapping, mapping_id)
                    logger.info(
                        f"🗑️ Deleted existing mapping for /new: "
                        f"user={msg.external_user_id}, old_instance={old_instance_id}"
                    )
                    # Also terminate the old instance
                    try:
                        await self._manager.terminate_instance(old_instance_id)
                        logger.debug(f"Terminated old instance: {old_instance_id}")
                    except Exception as e:
                        logger.warning(f"Could not terminate old instance {old_instance_id}: {e}")
            
            logger.debug(f"Getting or creating instance: agent_dir={agent_dir}")
            
            # Get source_type from adapter
            adapter = self.get(source_id)
            source_type = adapter.source_type if adapter else None
            
            # Build extra_mapping_metadata for Slack (channel_id, thread_ts from metadata)
            extra_mapping_metadata = None
            if source_type == "slack" and msg.metadata:
                slack_meta = msg.metadata.get("slack", {})
                if slack_meta:
                    extra_mapping_metadata = {
                        "slack_channel_id": slack_meta.get("channel_id"),
                        "slack_thread_ts": slack_meta.get("thread_ts"),
                        "slack_workspace_id": slack_meta.get("workspace_id"),
                        "slack_channel_type": slack_meta.get("channel_type"),
                    }
                else:
                    logger.warning(
                        f"Slack message missing metadata for source={source_id}, "
                        f"user={msg.external_user_id}. Routing may fail."
                    )
            
            # Get or create the instance
            instance_id = await mapper.get_or_create_instance(
                source_id=source_id,
                external_user_id=msg.external_user_id,
                agent_id=agent_dir,
                force_new=force_new,
                extra_mapping_metadata=extra_mapping_metadata,
            )
            
            logger.debug(f"Got instance_id={instance_id}")
            
            # Handle special commands that don't need agent processing
            if command == "/new":
                # Send confirmation message directly
                adapter = self.get(source_id)
                if adapter:
                    from .base import OutgoingMessage
                    confirmation = OutgoingMessage(
                        external_user_id=msg.external_user_id,
                        content="✨ Started new conversation! Your chat history has been reset.",
                        source_id=source_id,
                    )
                    await adapter.send(confirmation)
                    logger.info(f"✅ Sent /new confirmation to user {msg.external_user_id}")
                return  # Don't queue to agent
            
            # Format source as "{source_id}:{external_user_id}"
            source = f"{source_id}:{msg.external_user_id}"
            
            # Queue the message for processing with correct parameters
            await self._manager.enqueue_message(
                instance_id=instance_id,
                message=msg.content,
                source=source,
                priority=priority,
                images=msg.images,
                metadata=msg.metadata,
            )
            logger.info(
                f"✅ Queued message: source_id={source_id}, "
                f"user={msg.external_user_id}, instance={instance_id}, "
                f"content={msg.content[:50] if msg.content else None}..."
            )
            
            # Start typing indicator for Telegram sources
            # Use reply_chat_id for typing (in groups, show typing in the group)
            adapter = self.get(source_id)
            if adapter and hasattr(adapter, 'start_typing'):
                reply_chat_id = msg.metadata.get("reply_chat_id", msg.external_user_id) if msg.metadata else msg.external_user_id
                await adapter.start_typing(reply_chat_id)  # type: ignore
                logger.debug(f"Started typing indicator for chat {reply_chat_id}")
            
        except Exception as e:
            logger.error(f"❌ Error in _handle_message: {e}", exc_info=True)
