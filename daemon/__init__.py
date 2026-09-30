"""Persistent Multi-Session Agent Daemon"""

__version__ = "0.16.6"

# ---------------------------------------------------------------------------
# P3-WP10 — KMS logging redaction filter
# ---------------------------------------------------------------------------
# Install the KMS redaction filter at logger-configuration root so every
# handler reachable from the root config carries the filter. The eager
# install handles handlers present at import time; the addHandler patch
# installed below keeps coverage live as handlers are attached later
# (notably by ``daemon/api.py`` which configures the root logger with
# two handlers — stderr + rotating file — after this module runs).
#
# The import is intentionally lazy (``noqa: E402``) so we don't pull
# the redaction filter module in until the package is fully
# initialised. Both functions below are idempotent and process-singleton
# safe — calling them here at module-import time means downstream code
# can rely on the filter being in place without having to install it
# themselves.
from daemon.util.log_redaction_filter import (  # noqa: E402
    install_kms_redaction_filter,
    patch_addhandler_to_install_filter,
)

install_kms_redaction_filter()
patch_addhandler_to_install_filter()
