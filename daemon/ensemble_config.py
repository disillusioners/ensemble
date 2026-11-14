"""Ensemble configuration — database selection and connection settings.

The `EnsembleConfig` model is loaded from (or auto-created at) `<data_dir>/ensemble.json`.
On first startup, the configuration is auto-detected from environment variables:
    - If POSTGRES_HOST AND POSTGRES_DB are both set → default to "postgres"
    - Otherwise → default to "sqlite"

Environment variables take precedence over file values at runtime, which supports
operations like credential rotation without rewriting the config file.
"""

import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class PostgresConfig(BaseModel):
    """PostgreSQL connection settings."""

    host: str = "localhost"
    port: int = 5432
    db: str = "ensemble"
    user: str = "ensemble"
    password: str = ""


class SqliteConfig(BaseModel):
    """SQLite database file paths."""

    instances_db: str = "./data/instances.db"
    checkpoints_db: str = "./data/checkpoints.db"


class EnsembleConfig(BaseModel):
    """Ensemble configuration loaded from ensemble.json."""

    database: str = "sqlite"  # "sqlite" or "postgres"
    postgres: PostgresConfig = PostgresConfig()
    sqlite: SqliteConfig = SqliteConfig()

    @property
    def is_postgres(self) -> bool:
        return self.database == "postgres"

    @property
    def is_sqlite(self) -> bool:
        return self.database == "sqlite"

    @classmethod
    def load_or_create(cls, data_dir: Path) -> "EnsembleConfig":
        """Load ensemble.json from data_dir, or create with auto-detected defaults.

        Auto-detection rule:
            - If ensemble.json exists → load it
            - If POSTGRES_HOST AND POSTGRES_DB are both set AND ensemble.json doesn't
              exist AND no SQLite DBs (instances.db/checkpoints.db) → default to "postgres"
            - If POSTGRES_HOST AND POSTGRES_DB are both set AND ensemble.json doesn't
              exist BUT SQLite DBs exist → default to "sqlite" (migration preparation)
            - Otherwise → default to "sqlite"

        The resulting configuration is always persisted to disk via `save()` so the
        first-startup decision is recorded and can be inspected later.
        """
        config_path = data_dir / "ensemble.json"

        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                config = cls(**data)
                logger.info(f"Loaded ensemble config: database={config.database}")
                return config
            except Exception as e:
                logger.warning(f"Failed to load ensemble.json: {e}. Using defaults.")
                return cls()

        # Auto-detect from environment variables
        if os.environ.get("POSTGRES_HOST") and os.environ.get("POSTGRES_DB"):
            has_sqlite_dbs = (data_dir / "instances.db").exists() or (data_dir / "checkpoints.db").exists()
            if has_sqlite_dbs:
                logger.info("PostgreSQL ENV vars detected but SQLite DBs exist, defaulting to sqlite for migration preparation")
                config = cls()
            else:
                logger.info("PostgreSQL ENV vars detected, defaulting to postgres")
                config = cls(database="postgres")
        else:
            logger.info("No PostgreSQL ENV vars detected, defaulting to sqlite")
            config = cls()

        # Save the auto-created config (atomic write)
        config.save(data_dir)
        return config

    def save(self, data_dir: Path) -> None:
        """Save config to ensemble.json with atomic write (write to temp, then os.replace)."""
        config_path = data_dir / "ensemble.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = config_path.with_suffix(".json.tmp")
        try:
            temp_path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
            os.replace(str(temp_path), str(config_path))
            logger.info(f"Saved ensemble config to {config_path}")
        except Exception as e:
            logger.error(f"Failed to save ensemble.json: {e}")
            if temp_path.exists():
                temp_path.unlink()
            raise

    def get_postgres_url(self) -> str:
        """Get PostgreSQL connection URL for async engine creation.

        Format: postgresql+asyncpg://user:password@host:port/db

        Environment variables override file values, supporting credential rotation
        without rewriting ensemble.json.
        """
        pg = self.postgres
        # Override with ENV vars if set (ENV takes precedence over file)
        host = os.environ.get("POSTGRES_HOST", pg.host)
        port = os.environ.get("POSTGRES_PORT", str(pg.port))
        db = os.environ.get("POSTGRES_DB", pg.db)
        user = os.environ.get("POSTGRES_USER", pg.user)
        password = os.environ.get("POSTGRES_PASSWORD", pg.password)

        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"

    @property
    def postgres_env_available(self) -> bool:
        """Check if PostgreSQL ENV vars are available."""
        return bool(os.environ.get("POSTGRES_HOST") and os.environ.get("POSTGRES_DB"))


def assert_db_resolution_sanctioned(data_dir) -> None:
    """DB-resolution guard: refuse an ambient-POSTGRES-driven first boot
    (tier 2) unless ``ENSEMBLE_DB_AMBIENT_ALLOW=1`` is set.

    Fail-closed on the tier-2 poison case AND on a guard-internal error
    (fix-round 2 / N5 — leader fail-polarity decision: an unverifiable DB
    sanction must not proceed; the guard logs a clear reason and exits 78).
    CLI exemption CLOSED (rogue-containment p2, component B2 §4.7): the
    old "documented exemption" at daemon/auth/cli.py resolve_engine was an
    active poisoner (its load_or_create AUTO-PERSISTS ensemble.json);
    resolve_engine now calls THIS guard BEFORE load_or_create (a refused
    CLI persists no artifact) and the prod-DSN gate
    (assert_prod_dsn_sanctioned, below) after load. Operator escape hatch
    on the live install: ENSEMBLE_SELF_ENV=live.

    Fires (sys.exit(78) + marker line) iff:
      - (data_dir / "ensemble.json") does NOT exist (tier 2 path), AND
      - POSTGRES_HOST and POSTGRES_DB are both set, AND
      - ENSEMBLE_DB_AMBIENT_ALLOW != "1".

    Tier 1 (json exists) => THIS ambient tier does not fire — but tier 1
    is NO LONGER categorically sanctioned (rogue-containment p2, B2 §4.2):
    a tier-1 boot whose RESOLVED DSN targets ensemble_prod is fenced
    post-load by assert_prod_dsn_sanctioned (below) unless the boot
    identity is live. Tier 3/4 (sqlite) => does not fire. Marker line
    names the tier + ambient var NAMES only (never values) + the fence
    var name (F7).

    EXIT-CODE NOTE (iter-1 D4): this function calls sys.exit(78). When
    invoked from daemon/__main__._boot_db_preflight (BEFORE uvicorn) the
    process exits 78 (the launcher's refuse track). When invoked from
    the api.py lifespan (a defense-in-depth backstop, unreachable when
    the preflight ran) the SystemExit propagates through uvicorn's
    lifespan handler as exit 3 (crash track). The __main__ preflight is
    the exit-78 owner on BOTH entry paths (python -m daemon and frozen
    run_app); the lifespan call is backstop only.
    """
    import sys as _sys

    try:
        dd = Path(data_dir)
        config_path = dd / "ensemble.json"
        if config_path.exists():
            return  # tier 1 — ambient tier does not fire; the prod-name fence is the post-load assert_prod_dsn_sanctioned
        host_set = bool(os.environ.get("POSTGRES_HOST"))
        db_set = bool(os.environ.get("POSTGRES_DB"))
        if not (host_set and db_set):
            return  # tier 3/4 — sqlite default, safe
        if os.environ.get("ENSEMBLE_DB_AMBIENT_ALLOW") == "1":
            return  # sanctioned
        ambient_names = []
        for name in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB",
                     "POSTGRES_USER", "POSTGRES_PASSWORD"):
            if os.environ.get(name):
                ambient_names.append(name)
        logger.error(
            "DB RESOLUTION REFUSED (rogue-containment): tier=ambient "
            "(ensemble.json absent) with ambient POSTGRES_* present "
            "(%s) and ENSEMBLE_DB_AMBIENT_ALLOW != 1. "
            "Refusing before any TCP connection. Set "
            "ENSEMBLE_DB_AMBIENT_ALLOW=1 for a sanctioned first boot "
            "(the persisted ensemble.json makes later boots tier 1).",
            ",".join(ambient_names),
        )
        _sys.exit(78)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        # FAIL-CLOSED (fix-round 2 / N5, leader decision): if the guard
        # cannot EVALUATE the sanction, the DB resolution is unverified and
        # must not proceed. Loud, explicit, and exit 78 (the existing
        # configuration-refusal contract — the launcher does not loop).
        logger.error(
            "DB RESOLUTION UNVERIFIABLE (rogue-containment guard error): "
            "%s: %s — refusing boot (fail-closed; exit 78).",
            type(exc).__name__,
            exc,
        )
        _sys.exit(78)


def resolved_postgres_db_name(config) -> str:
    """The POST-resolution PostgreSQL DB NAME (env-wins), normalized.

    Mirrors ``EnsembleConfig.get_postgres_url``'s env-wins expression
    EXACTLY — ``os.environ.get("POSTGRES_DB", config.postgres.db)`` — so
    the prod-DSN fence sees the SAME name the connection will use: an
    ambient ``POSTGRES_DB`` override of a tier-1 file boot is INSIDE the
    predicate's view (component B2 §4.2/§4.5, the env-wins poison
    closure). The identical expression also lives in
    ``daemon/repositories/factory.create_postgres_engine`` (its env-wins
    line), so ONE resolved-NAME helper covers both DSN construction
    sites; the rendered URL is NEVER parsed (passwords containing
    ``?``/``@`` break URL parsing — the documented reason the factory
    uses ``URL.create``).

    Normalization follows the house idiom (``_auto_derive_env``'s
    ``db_hint``): ``.strip().lower()`` before comparison — the predicate
    is a name-safety check, not a SQL identifier match.
    """
    return os.environ.get("POSTGRES_DB", config.postgres.db).strip().lower()


def assert_prod_dsn_sanctioned(ensemble_config) -> None:
    """Tier-1 prod-DSN fence (rogue-containment p2, component B2 — the
    occ#9 closer): refuse a boot whose RESOLVED DSN targets
    ``ensemble_prod`` — the live production database — unless the boot's
    identity is the sanctioned live environment. Pre-TCP: call it BEFORE
    any engine construction / socket activity. ``daemon/__main__.
    _boot_db_preflight`` is the exit-78 owner on BOTH entry paths
    (python -m daemon and frozen ``run_app``); the ``daemon/api.py``
    lifespan call is a defense-in-depth backstop whose SystemExit(78)
    maps to exit 3 (crash track) through uvicorn's LifespanOn;
    ``daemon/auth/cli.resolve_engine`` exits 78 straight out of its
    argparse ``main``.

    Predicate (exact, normalized):
    ``resolved_postgres_db_name(config) == "ensemble_prod"`` — the
    POST-resolution name (the same env-wins rule ``get_postgres_url``
    applies), so an ambient ``POSTGRES_DB=ensemble_prod`` override of a
    tier-1 file boot is INSIDE the predicate's view (B2 §4.5).
    ``ensemble_prod`` is the ONLY refused name: ``ensemble_dev`` is the
    sanctioned dev DB and ``ensemble_prod_test``/scratch names are
    harness territory (B2 §4.4 — exact-match design, never a prefix or
    the harness ``FORBIDDEN_DB_NAMES`` set).

    Identity carve-out via ``_self_env_marker()`` — imported from
    ``daemon.tools.upgrade_tools`` FUNCTION-LOCALLY (call-time lookup;
    keeps the very-early preflight import cycle-free; precedent
    ``daemon/services/port_ownership.resolve_self_install_dir``):

      - ``"live"``  → SANCTIONED — proceed, one INFO line naming the
        resolution source (explicit / derived).
      - ``"dev"`` / ``"demo"`` / ``"sandbox"`` → REFUSE — exit 78 +
        marker. Positive proof of a NON-live boot targeting prod: the
        wi-2454ede3 rogue derived ``sandbox`` (frozen staged binary +
        ``releases/`` + non-canonical dir) — refused by construction.
      - ``None``    → DEGRADE — loud WARNING carrying the
        ``_self_env_source`` vocabulary (``explicit`` / ``opted-out`` /
        ``auto`` / ``auto-unresolved`` / ``ignored-garbage``), then
        proceed. RATIFIED V2 (leader, 2026-11-14): the live boot
        carries NO explicit ``ENSEMBLE_SELF_ENV`` and its fence-pass
        depends ENTIRELY on auto-derive, so refuse-on-None would
        convert any derive-regression into a live-down at promote
        (BF-2). The deliberate identity-blurring rogue in the
        None+prod class is narrowed by the tier-2 pre-guard (above)
        + component A.
      - resolver RAISES → FAIL-CLOSED — exit 78 + ``UNVERIFIABLE``
        marker (an evaluation failure is NOT a resolution of None;
        same polarity as the shipped tier-2 guard).

    ``ENSEMBLE_DB_AMBIENT_ALLOW`` is NEVER consulted here (independent
    gating, B2 §4.6): the var grants tier-2 (ambient-first-boot)
    sanction only and is permanently removed from the prod-name
    decision path — a rogue inheriting ``ALLOW=1`` (it is NOT in
    ``SPAWN_BOUNDARY_STRIP_ENV``) that resolves a prod DSN passes the
    tier-2 guard and is refused by THIS gate.

    Residuals (declared, not closed): a same-host rogue whose ambient
    prod env + ``~/agents-ensemble/.env`` cross-check derives ``live``
    passes the carve-out — identity is HOST-scoped, not process-scoped;
    the containment boundary remains uid + DB credential (the
    port-ownership H1-analog, B2 §10-R1). A same-host None-shaped
    rogue with a copied prod json passes the degrade (B2 §10-R2).
    Ambient ``POSTGRES_HOST`` re-pointing at an impostor host with a
    non-prod DB name is a different threat class, out of scope (B2
    §10-R7). Out-of-hook residual: ``daemon/services/migration_worker``
    builds a fresh post-boot config — boot already fenced; that is
    post-boot migration work, not a boot-time resolution path.

    Marker / F7 discipline: ambient var VALUES are never logged; the DB
    name and the derived self-env label ARE named because they ARE the
    refusal reason (neither is a credential). This refusal fires BEFORE
    ``install_root_logging()`` ⇒ the marker lands via root-logger
    lastResort → stderr → journald (the stage-verification S-legs
    assert on stderr/journal text for the B2 family).

    Operator escape hatch (degraded identity on the live install):
    stage ``ENSEMBLE_SELF_ENV=live`` explicitly — the explicit marker
    wins.
    """
    import sys as _sys

    try:
        if not ensemble_config.is_postgres:
            return  # sqlite backend — nothing to fence
        if resolved_postgres_db_name(ensemble_config) == "ensemble_prod":
            from daemon.tools.upgrade_tools import (
                _self_env_marker,
                _self_env_source,
            )

            marker = _self_env_marker()
            try:
                source = _self_env_source()
            except Exception:  # noqa: BLE001 - label read is NON-decisive (B2 §4.3): never fail-close a decided verdict on it
                source = "unknown"
            resolution = "explicit" if source == "explicit" else "derived"
            if marker == "live":
                logger.info(
                    "DB RESOLUTION SANCTIONED (rogue-containment): resolved "
                    "DSN targets ensemble_prod and boot identity is live "
                    "(resolution=%s) — proceeding.",
                    resolution,
                )
                return
            if marker in ("dev", "demo", "sandbox"):
                logger.error(
                    "DB RESOLUTION REFUSED (rogue-containment): "
                    "tier=resolved-dsn-targets-prod db=ensemble_prod "
                    "self_env=%s resolution=%s — refusing boot; set "
                    "ENSEMBLE_SELF_ENV=live only on the live install.",
                    marker,
                    resolution,
                )
                _sys.exit(78)
            # marker is None — unresolved identity (incl. explicit
            # opt-out): DEGRADE, loud, then proceed (ratified V2).
            logger.warning(
                "DB RESOLUTION DEGRADED (rogue-containment): resolved DSN "
                "targets ensemble_prod but boot identity is UNRESOLVED "
                "(self_env_source=%s) — proceeding WITHOUT positive live "
                "proof; set ENSEMBLE_SELF_ENV=live only on the live "
                "install.",
                source,
            )
            return
        # resolved name is NOT the prod name — nothing to fence
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        # FAIL-CLOSED (mirror of the tier-2 guard): if the guard cannot
        # EVALUATE the sanction, the DB resolution is unverified and must
        # not proceed. Loud, explicit, and exit 78 (the existing
        # configuration-refusal contract — the launcher does not loop).
        logger.error(
            "DB RESOLUTION UNVERIFIABLE (rogue-containment prod-dsn guard "
            "error): %s: %s — refusing boot (fail-closed; exit 78).",
            type(exc).__name__,
            exc,
        )
        _sys.exit(78)
