"""Maintenance-run id minting — AM-8.

Single source of truth for ``run_id`` values used by the Section 1
manual Maintenance Console. Format:

    ckpt-YYYYMMDD_HHMMSSffffff-hex8

* ``YYYYMMDD_HHMMSSffffff`` — UTC, microsecond precision (matches the
  ``migration_id`` precedent at ``daemon/routers/migration.py:112-114``
  and extends it with the microsecond suffix).
* ``hex8`` — 8-char ``secrets.token_hex(4)`` suffix (Focus Area 1
  defense-in-depth for ``/runs/{id}`` enumeration hardening).

Documented superset [R-18]: same date-time core as ``migration_YYYYMMDD_HHMMSS``
plus microsecond precision plus an 8-hex random suffix — lineage stated
here in the module docstring (close-out checklist item).

The function is intentionally tiny and pure: no I/O, no env reads, no
clock injection — every test of the id shape can call it directly.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

__all__ = ["new_maintenance_run_id"]


def new_maintenance_run_id() -> str:
    """Mint a new ``run_id`` for the Section 1 Maintenance Console.

    Returns:
        A string of the form ``ckpt-YYYYMMDD_HHMMSSffffff-<hex8>``,
        colon-free (URL-clean, no encoding needed for path params),
        lexicographically sortable (TEXT ISO prefix sorts
        chronologically — the repo's audit-table ordering invariant).

    Notes:
        * Collision retry on the hex8 suffix is the caller's
          responsibility (the ``MaintenanceRunsRepository.insert``
          implementation retries once on PK collision — see T4.2 in
          ``.agents/shared/planning/maintenance-console/phase1-backend.md``).
        * This function does NOT verify uniqueness — it mints and returns.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S%f")
    return f"ckpt-{ts}-{secrets.token_hex(4)}"