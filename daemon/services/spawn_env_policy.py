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

Strip set (12 exact names + one prefix family, fix-round 2 / N4):
  - bind family (4): PORT, DAEMON_PORT, HOST, LIMITS_METRICS_LISTENER_PORT
  - DB-poison family (7): POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB,
    POSTGRES_USER, POSTGRES_PASSWORD (exact names from
    ``ensemble_config.get_postgres_url``), DATABASE_URL, ENSEMBLE_CONFIG
  - live-attestation flag (1): ENSEMBLE_UPGRADE_LIVE
  - ``PG`` PREFIX family (libpq knobs: PGHOST, PGPORT, PGDATABASE,
    PGUSER, PGPASSWORD, PGPASSFILE, PGSSLMODE, PGSERVICE, ...). The
    test-fence family ``PG_TEST_*`` is EXEMPT: those values are scratch
    coordinates the sanctioned test lanes (conventions) require, never a
    live credential. Ordinary ``POSTGRES_*`` names are matched exactly.

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
# NOTE (fix-round 2 / N4): the exact set now ALSO covers DATABASE_URL and
# ENSEMBLE_CONFIG (a scratch config path re-points port/DB resolution), and
# a ``PG`` PREFIX family covers libpq knobs. The promote-executor seam
# (upgrade_journal.executor_env) is deliberately NOT a consumer — promote
# legs legitimately forward PG*/POSTGRES_DB under explicit control.
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
    # scratch-config + DSN re-point channels (fix-round 2 / N4)
    "ENSEMBLE_CONFIG",
    "DATABASE_URL",
})

#: Prefix families stripped from the inherit base.
SPAWN_BOUNDARY_STRIP_PREFIXES: tuple[str, ...] = ("PG",)

#: Prefix exemptions — values that are scratch test coordinates, not
#: credentials. Keeping these makes sanctioned pytest/PG lanes work through
#: the scrub without re-opening the live-poison channel.
SPAWN_BOUNDARY_STRIP_PREFIX_EXEMPTIONS: tuple[str, ...] = ("PG_TEST_",)

# Sanity: the exact set is exactly 12 names (bind 4 + DB 7 + attestation 1).
assert len(SPAWN_BOUNDARY_STRIP_ENV) == 12, len(SPAWN_BOUNDARY_STRIP_ENV)


def is_stripped_name(name: str) -> bool:
    """True when ``name`` is removed by the spawn-boundary policy."""
    if name in SPAWN_BOUNDARY_STRIP_ENV:
        return True
    if not name.startswith(SPAWN_BOUNDARY_STRIP_PREFIXES):
        return False
    return not name.startswith(SPAWN_BOUNDARY_STRIP_PREFIX_EXEMPTIONS)


def scrub_spawn_env(base: Mapping[str, str]) -> dict[str, str]:
    """Return a copy of ``base`` with every stripped name removed.
    Does NOT mutate the input."""
    return {k: v for k, v in base.items() if not is_stripped_name(k)}


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
