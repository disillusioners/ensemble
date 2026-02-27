"""Source registry for managing message source adapters."""

import asyncio
import logging
import random
import time
from typing import Optional

from .base import (
    IncomingMessage,
    MessageSourceAdapter,
    SourceConfig,
    SourceStatus,
)
from . import persistence
from .mapper import SessionMapper

logger = logging.getLogger(__name__)


class SourceRegistry:
    """Registry for managing message source adapters.
    
    Provides lifecycle management for all registered adapters including:
    - Registration/unregistration
    - Start/stop operations
    - Supervised execution with exponential backoff
    - Database persistence integration
    """
    
    ADAPTER_START_TIMEOUT = 60.0  # seconds to wait for adapter.start()
    
    def __init__(self, conn, manager):
        """Initialize the source registry.
        
        Args:
            conn: sqlite3.Connection for database operations.
            manager: SessionManager reference for handling messages.
        """
        self._conn = conn
        self._manager = manager
        self._adapters: dict[str, MessageSourceAdapter] = {}
        self._supervisor_tasks: dict[str, asyncio.Task] = {}
        self._running: dict[str, bool] = {}  # Track running state for each adapter
    
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
        
        # Cancel supervisor task if running
        if source_id in self._supervisor_tasks:
            task = self._supervisor_tasks.pop(source_id)
            if not task.done():
                task.cancel()
                logger.debug(f"Cancelled supervisor task for: {source_id}")
        
        del self._adapters[source_id]
        self._running.pop(source_id, None)
        logger.info(f"Unregistered adapter: source_id={source_id}")
        return True
    
    def get(self, source_id: str) -> Optional[MessageSourceAdapter]:
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
        if not registered, and starts all enabled adapters.
        """
        logger.info("Loading and starting all enabled adapters...")
        
        # Load configs from database
        configs = persistence.list_source_configs(self._conn)
        
        started_count = 0
        for config_dict in configs:
            if not config_dict.get("enabled", True):
                logger.debug(f"Skipping disabled source: {config_dict['source_id']}")
                continue
            
            source_id = config_dict["source_id"]
            
            # Check if adapter is already registered
            adapter = self.get(source_id)
            if adapter is None:
                # Create adapter from config
                logger.info(f"Creating adapter for: {source_id}")
                try:
                    adapter = await self._create_adapter_from_config(config_dict)
                    if adapter:
                        self.register(adapter)
                        logger.info(f"Registered adapter: {source_id}")
                    else:
                        logger.warning(f"Could not create adapter for: {source_id}")
                        continue
                except Exception as e:
                    logger.error(f"Failed to create adapter {source_id}: {e}")
                    continue
            
            # Start the adapter
            try:
                await self.start_adapter(source_id)
                started_count += 1
            except Exception as e:
                logger.error(f"Failed to start adapter {source_id}: {e}")
        
        logger.info(f"Started {started_count} adapters from database")
    
    async def _create_adapter_from_config(self, config_dict: dict):
        """Create an adapter instance from a config dictionary.
        
        Args:
            config_dict: Source configuration from database.
            
        Returns:
            MessageSourceAdapter instance or None if unsupported type.
        """
        from .base import SourceConfig
        from .credentials import CredentialManager
        
        source_type = config_dict["source_type"]
        source_id = config_dict["source_id"]
        
        # Decrypt credentials if needed
        credentials = config_dict.get("credentials", {})
        if credentials and isinstance(credentials, str):
            cred_manager = CredentialManager()
            credentials = cred_manager.decrypt(credentials)
        
        config = SourceConfig(
            source_id=source_id,
            source_type=source_type,
            name=config_dict["name"],
            config=config_dict.get("config", {}),
            credentials=credentials,
            enabled=config_dict.get("enabled", True),
        )
        
        # Create callback wrapper that includes source_id
        async def on_message(msg):
            await self._handle_message(source_id, msg)
        
        # Create the appropriate adapter
        if source_type == "telegram":
            from .adapters.telegram import TelegramAdapter
            return TelegramAdapter(config, on_message)
        else:
            logger.warning(f"Unsupported source type: {source_type}")
            return None
    
    async def stop_all(self) -> None:
        """Stop all running adapters gracefully.
        
        Cancels all supervisor tasks and stops all adapters.
        """
        logger.info("Stopping all adapters...")
        
        # Copy keys since we may modify during iteration
        source_ids = list(self._supervisor_tasks.keys())
        
        for source_id in source_ids:
            await self.stop_adapter(source_id)
        
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
        persistence.update_source_status(self._conn, source_id, SourceStatus.STARTING.value)
        
        # Start supervisor task
        self._running[source_id] = True
        task = asyncio.create_task(self._run_adapter_safe(adapter))
        self._supervisor_tasks[source_id] = task
        
        logger.info(f"Started adapter: {source_id}")
        return True
    
    async def stop_adapter(self, source_id: str) -> bool:
        """Stop a specific adapter gracefully.
        
        Args:
            source_id: The source_id of the adapter to stop.
            
        Returns:
            True if stopped successfully, False if adapter not found.
        """
        adapter = self.get(source_id)
        if adapter is None:
            logger.error(f"Adapter not found: {source_id}")
            return False
        
        # Mark as not running to signal supervisor to stop
        self._running[source_id] = False
        
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
        
        # Update status
        adapter._status = SourceStatus.STOPPED
        persistence.update_source_status(self._conn, source_id, SourceStatus.STOPPED.value)
        
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
        config_dict = persistence.get_source_config(self._conn, source_id)
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
                persistence.update_source_status(self._conn, source_id, SourceStatus.STARTING.value)
                
                # Add timeout to start() to detect hung adapters
                try:
                    await asyncio.wait_for(adapter.start(), timeout=self.ADAPTER_START_TIMEOUT)
                except asyncio.TimeoutError:
                    raise TimeoutError(f"Adapter start() timed out after {self.ADAPTER_START_TIMEOUT}s")
                
                # Adapter started successfully
                adapter._status = SourceStatus.RUNNING
                adapter._error = None
                persistence.update_source_status(self._conn, source_id, SourceStatus.RUNNING.value)
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
                persistence.update_source_status(
                    self._conn, source_id, SourceStatus.ERROR.value, str(e)
                )
                
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
        
        # Clean up on exit
        logger.info(f"Supervisor exiting for adapter: {source_id}")
    
    async def _handle_message(self, source_id: str, msg: IncomingMessage) -> None:
        """Handle incoming message from an adapter.
        
        This is the callback that adapters call when they receive messages.
        Forwards the message to the SessionManager for processing.
        
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
                is_dup = persistence.is_duplicate_message(
                    self._conn, source_id, external_msg_id
                )
                if is_dup:
                    logger.info(
                        f"Skipping duplicate message: source_id={source_id}, "
                        f"external_id={external_msg_id}"
                    )
                    return
            
            # Get or create session via SessionMapper
            mapper = SessionMapper(self._conn, self._manager)
            
            # Determine agent_dir from metadata or use default
            agent_dir = msg.metadata.get("agent_dir") if msg.metadata else None
            if not agent_dir:
                # Use default from config
                agent_dir = self._manager.config.agents.directory
                logger.debug(f"Using default agent_dir from config: {agent_dir}")
            
            logger.debug(f"Getting or creating session: agent_dir={agent_dir}")
            
            # Get or create the session
            session_id = await mapper.get_or_create_session(
                source_id=source_id,
                external_user_id=msg.external_user_id,
                agent_dir=agent_dir
            )
            
            logger.debug(f"Got session_id={session_id}")
            
            # Format source as "{source_id}:{external_user_id}"
            source = f"{source_id}:{msg.external_user_id}"
            
            # Queue the message for processing with correct parameters
            self._manager.queue.enqueue(
                session_id=session_id,
                content=msg.content,
                source=source,
                priority=1,
                metadata=msg.metadata
            )
            logger.info(
                f"✅ Queued message: source_id={source_id}, "
                f"user={msg.external_user_id}, session={session_id}, "
                f"content={msg.content[:50] if msg.content else None}..."
            )
            
            # Start typing indicator for Telegram sources
            adapter = self.get(source_id)
            if adapter and hasattr(adapter, 'start_typing'):
                await adapter.start_typing(msg.external_user_id)
                logger.debug(f"Started typing indicator for user {msg.external_user_id}")
            
            # Trigger queue processing (safe to call even if already processing)
            asyncio.create_task(self._manager._process_queue(session_id))
            logger.debug(f"Triggered queue processing for session {session_id}")
            
        except Exception as e:
            logger.error(f"❌ Error in _handle_message: {e}", exc_info=True)
