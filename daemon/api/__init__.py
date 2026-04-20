"""
Ensemble Daemon API package.

This module provides the FastAPI application and route handlers for the
multi-agent AI daemon with LangGraph.

Backward Compatibility:
    This package replaces the previous daemon/api.py module.
    All public symbols are re-exported for compatibility:

        from daemon.api import app           # FastAPI application instance
        from daemon.api import send_message  # Route handler function
        from daemon.api import validate_agent_id  # Helper function
"""

import warnings

# Suppress langchain Pydantic V1 compatibility warning on Python 3.14+
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible with Python 3.14",
    category=UserWarning,
)

# Configure logging for daemon modules
# This ensures our logs are visible when running via uvicorn
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)

# Suppress uvicorn INFO-level access logs (our SelectiveAccessLogMiddleware handles selective logging)
uvicorn_access = logging.getLogger("uvicorn.access")
uvicorn_access.setLevel(logging.WARNING)

# Determine the base path (use working directory for production)
# PyInstaller runs from INSTALL_DIR where frontend/dist is expected
import sys
from pathlib import Path
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent.parent

# Import submodules after defining BASE_DIR to avoid import order issues
from . import app
from . import routes
from . import middleware

# Re-export public API for backward compatibility
from .app import app
from .routes import (
    send_message,
    validate_agent_id,
    validate_instance_mode,
    _reject_scheduler_lifecycle,
    api_router,
)

# Re-export middleware
from .middleware import SelectiveAccessLogMiddleware

# Re-export globals for test mocking
from .routes import manager, start_time

# Public API
__all__ = [
    # Main application
    "app",
    # Globals (for testing/mocking)
    "manager",
    "start_time",
    "BASE_DIR",
    # Route handlers (backward compatibility)
    "send_message",
    "validate_agent_id",
    "validate_instance_mode",
    "_reject_scheduler_lifecycle",
    # Router
    "api_router",
    # Middleware
    "SelectiveAccessLogMiddleware",
]
