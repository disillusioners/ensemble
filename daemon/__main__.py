import logging
import signal
import sys
import warnings

logger = logging.getLogger(__name__)

# Suppress langchain Pydantic V1 compatibility warning on Python 3.14+
# This is safe: langchain still works, just uses deprecated Pydantic V1 shim
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible with Python 3.14",
    category=UserWarning,
)

import uvicorn
from pathlib import Path

from .config import load_config, warn_deprecated_reasoning_echo_env


def main():
    """Main entry point."""
    # Load config to get host/port
    config = load_config()

    # Apply LLM-specific class-level config that must be set before any
    # ThinkingChatOpenAI instance is created.
    from .graph import ThinkingChatOpenAI
    ThinkingChatOpenAI.reasoning_echo_disabled_models = list(
        config.llm.reasoning_echo_disabled_models or []
    )
    logger.info(
        f"[Config] reasoning_echo_disabled_models={ThinkingChatOpenAI.reasoning_echo_disabled_models} "
        f"(models matching these patterns will NOT echo reasoning_content; all others echo)"
    )

    # Warn-once if the removed allowlist env var is still set (no-op when
    # load_config already emitted it)
    warn_deprecated_reasoning_echo_env()

    # Log version for debugging
    from . import __version__
    logger.info(f"Starting Ensemble v{__version__}")

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
