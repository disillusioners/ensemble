"""Periodic cleanup for source-related database tables."""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class SourceCleanup:
    """Periodic cleanup for source-related tables.
    
    Runs cleanup jobs at regular intervals to:
    - Remove old processed messages (deduplication table)
    - Remove inactive session mappings
    """
    
    def __init__(self, source_repo, interval_hours: int = 6):
        self._source_repo = source_repo
        self._interval = interval_hours * 3600
        self._running = False
        self._task: Optional[asyncio.Task] = None
    
    def start(self) -> None:
        """Start the cleanup loop."""
        self._running = True
        self._task = asyncio.create_task(self._cleanup_loop())
        logger.info(f"SourceCleanup started (interval: {self._interval}s)")
    
    async def stop(self) -> None:
        """Stop the cleanup loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("SourceCleanup stopped")
    
    async def _cleanup_loop(self) -> None:
        """Main cleanup loop."""
        # Initial short delay before first cleanup (don't run immediately on startup)
        await asyncio.sleep(60)  # 1 minute after startup
        
        while self._running:
            try:
                stats = await self._run_cleanup()
                if any(v > 0 for v in stats.values()):
                    logger.info(f"Cleanup completed: {stats}")
            except Exception as e:
                logger.error(f"Cleanup job failed: {e}", exc_info=True)
            
            await asyncio.sleep(self._interval)
    
    async def _run_cleanup(self) -> dict:
        """Run all cleanup tasks. Returns stats."""
        stats = {}
        
        # 1. Cleanup old processed messages (24h TTL)
        stats["processed_messages_deleted"] = self._source_repo.cleanup_old_processed_messages(
            max_age_hours=24
        )
        
        # 2. Cleanup inactive session mappings (30 day TTL)
        stats["inactive_mappings_deleted"] = self._source_repo.cleanup_inactive_mappings(
            max_age_days=30
        )
        
        return stats
    
    async def run_once(self) -> dict:
        """Run cleanup once (for manual trigger)."""
        return await self._run_cleanup()
