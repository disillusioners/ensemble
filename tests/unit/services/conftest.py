"""Pytest configuration for services tests."""

import asyncio
import sys

# Python 3.14 removed asyncio.get_event_loop_time, so we need to add it back
# for compatibility with code that uses it
if not hasattr(asyncio, "get_event_loop_time"):
    def _get_event_loop_time():
        """Return the current event loop's internal time in seconds."""
        loop = asyncio.get_running_loop()
        return loop.time()
    asyncio.get_event_loop_time = _get_event_loop_time
