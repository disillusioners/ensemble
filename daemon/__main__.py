import signal
import sys
import warnings

# Suppress langchain Pydantic V1 compatibility warning on Python 3.14+
# This is safe: langchain still works, just uses deprecated Pydantic V1 shim
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible with Python 3.14",
    category=UserWarning,
)

import uvicorn
from pathlib import Path

from .config import load_config


def main():
    """Main entry point."""
    # Load config to get host/port
    config = load_config()
    
    # Note: uvicorn handles SIGTERM and SIGINT automatically.
    # The FastAPI lifespan shutdown (via @asynccontextmanager) will be
    # triggered when uvicorn shuts down, which calls manager.shutdown()
    # for graceful cleanup.
    
    # Run server (access_log=False: custom SelectiveAccessLogMiddleware handles selective logging)
    uvicorn.run(
        "daemon.api:app",
        host=config.daemon.host,
        port=config.daemon.port,
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
