"""Spawn-boundary environment scrub policy (rogue-containment component B, §4.1).

Single source of truth for the set of environment-variable names that the
daemon MUST strip from the environment it hands to spawned children
(bash-tool children and service-spawner children), plus the pure helpers
that apply the scrub.

The policy is deliberately a pure module: no seam changes, no I/O, no
process state. The two seam consumers (``daemon/tools/bash.py`` and
``daemon/tools/service_spawner.py``) import from here. ``upgrade_journal``
is intentionally NOT a consumer — its allowlist seam stays untouched
(load-bearing for promote legs).

Strip set (10 names):
  - bind family (4): PORT, DAEMON_PORT, HOST, LIMITS_METRICS_LISTENER_PORT
  - DB-poison family (5): POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB,
    POSTGRES_USER, POSTGRES_PASSWORD (exact names from
    ``ensemble_config.get_postgres_url``)
  - live-attestation flag (1): ENSEMBLE_UPGRADE_LIVE

House precedent: ``daemon/services/vscode_server_manager.py`` ``_CODESERVER_STRIP_ENV``
strips ``PORT`` for exactly this reason ("PORT causes EADDRINUSE").

Semantics:
  - ``scrub_spawn_env`` returns a COPY of ``base`` with every name in
    ``SPAWN_BOUNDARY_STRIP_ENV`` removed; the input is not mutated.
  - ``apply_spawn_boundary(os_env, overlay, deliberate)``:
      1. strip the inherit base (``scrub_spawn_env``)
      2. apply ``overlay`` (explicit setter channel — re-adds are sanctioned)
      3. apply ``deliberate`` (deliberate setter, wins last)
    ``overlay`` and ``deliberate`` are both optional; ``None``/empty is a
    no-op. Explicit overlay + deliberate setters are the sanctioned
    re-add channels (e.g. promote executor ``extra_env["PORT"]``).
"""

from __future__ import annotations

from typing import Mapping

# Single source of truth for the scrub policy (pin target).
# NOTE (iter-1 minor 9): POSTGRES_URL / DATABASE_URL_POSTGRES / PG* are
# deliberately NOT in the strip set. The strip set is scoped to the exact
# names read by ensemble_config.get_postgres_url (POSTGRES_HOST/PORT/DB/
# USER/PASSWORD). A broader PG*-prefix scrub is a follow-up candidate
# (would also need the executor seam's PG* allowlist reconciled); the
# count pin (10 names) reflects the scoped set. See upgrade_journal's
# EXECUTOR_ENV_PREFIXES = ("PG",) for the promote-leg channel.
SPAWN_BOUNDARY_STRIP_ENV: frozenset[str] = frozenset({
    # bind family (analysis §5.3)
    "PORT",
    "DAEMON_PORT",
    "HOST",
    "LIMITS_METRICS_LISTENER_PORT",
    # DB-poison family (commission root; exact names from
    # ensemble_config.get_postgres_url)
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    # live-attestation flag (adjudicated §4.3)
    "ENSEMBLE_UPGRADE_LIVE",
})

# Sanity: the strip set is exactly 10 names (bind 4 + DB 5 + attestation 1).
assert len(SPAWN_BOUNDARY_STRIP_ENV) == 10, len(SPAWN_BOUNDARY_STRIP_ENV)


def scrub_spawn_env(base: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of ``base`` with every name in
    ``SPAWN_BOUNDARY_STRIP_ENV`` removed. Does NOT mutate the input."""
    return {k: v for k, v in base.items() if k not in SPAWN_BOUNDARY_STRIP_ENV}


def apply_spawn_boundary(
    os_env: Mapping[str, str],
    overlay: Mapping[str, str] | None = None,
    deliberate: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Strip the inherit base, then re-add explicit overlay and deliberate
    setters (last wins). Explicit overlay + deliberate setters are the
    sanctioned re-add channels."""
    out = scrub_spawn_env(dict(os_env))
    if overlay:
        out.update(overlay)
    if deliberate:
        out.update(deliberate)
    return out
