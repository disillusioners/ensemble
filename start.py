#!/usr/bin/env python3
"""
Start script for Ensemble Daemon.
Works cross-platform (Windows, macOS, Linux).
"""

import warnings

# Suppress langchain Pydantic V1 compatibility warning on Python 3.14+
# This is safe: langchain still works, just uses deprecated Pydantic V1 shim
warnings.filterwarnings(
    "ignore",
    message="Core Pydantic V1 functionality isn't compatible with Python 3.14",
    category=UserWarning,
)

import os
import sys
import subprocess
from pathlib import Path


def load_env_file():
    """Load environment variables from .env file if it exists."""
    env_file = Path(".env")
    if env_file.exists():
        print("Loading environment from .env...")
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip().strip('"').strip("'")


def check_dependencies():
    """Check if required dependencies are installed."""
    try:
        import fastapi
        import uvicorn
    except ImportError as e:
        print(f"Error: Missing dependency - {e}")
        print("\nPlease install dependencies:")
        print("  pip install -e .")
        sys.exit(1)


def main():
    print("Starting Ensemble Daemon...")

    # Load .env file
    load_env_file()

    # Check for required environment variables
    if not os.environ.get("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY is not set")
        print("Please set it in .env file or as environment variable")
        sys.exit(1)

    # Set defaults
    os.environ.setdefault("OPENAI_BASE_URL", "https://api.openai.com/v1")
    os.environ.setdefault("OPENAI_MODEL", "gpt-4")

    # Create data directory
    Path("data").mkdir(exist_ok=True)

    # Get configuration
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8079"))
    reload_mode = "--reload" in sys.argv

    print(f"\nConfiguration:")
    print(f"  Host: {host}")
    print(f"  Port: {port}")
    print(f"  Model: {os.environ['OPENAI_MODEL']}")
    print(f"  API URL: {os.environ['OPENAI_BASE_URL']}")
    print(f"\nStarting server...")
    print(f"  API Docs:   http://localhost:{port}/docs")
    print(f"  Health:     http://localhost:{port}/health")
    print(f"  UI:         http://localhost:{port}/ui\n")

    # Start uvicorn
    args = ["uvicorn", "daemon.api:app", "--host", host, "--port", str(port)]
    if reload_mode:
        args.append("--reload")

    subprocess.run(args)


if __name__ == "__main__":
    main()
