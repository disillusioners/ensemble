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
from .api import app, manager

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    print(f"\nReceived signal {signum}, shutting down...")
    sys.exit(0)

def main():
    """Main entry point."""
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Load config to get host/port
    config = load_config()
    
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
