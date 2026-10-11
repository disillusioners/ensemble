"""Auth-bootstrap-consumption repository package — oauth2-auth P4 (T4.1).

The one-shot bootstrap mint sentinel store (F10): one row per consumed
``sha256(ENSEMBLE_AUTH_BOOTSTRAP_TOKEN)``. The model + migration landed
in P2 (inert-tables posture); this repository is the P4 consumer — the
CLI (:mod:`daemon.auth.cli`) checks for a sentinel BEFORE honoring the
env value and inserts the sentinel atomically with the first mint.
"""

from .repository import AuthBootstrapRepository, BootstrapConsumed

__all__ = ["AuthBootstrapRepository", "BootstrapConsumed"]
