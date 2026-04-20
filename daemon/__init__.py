"""Persistent Multi-Session Agent Daemon"""

import sys
from pathlib import Path

__version__ = "0.2.5"

# Determine the base path (use working directory for production)
# PyInstaller runs from INSTALL_DIR where frontend/dist is expected
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent
