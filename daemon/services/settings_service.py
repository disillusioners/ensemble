"""DB-backed daemon runtime settings — managed-key registry, the ONE
shared tier resolver, override CRUD, and the boot apply hook.

(db-backed-settings commission, Phase 1; plan set:
``.agents/shared/planning/db-backed-settings/`` — BINDING.)

Resolution ladder (architecture-recommendation.md Ruling on blind
spot (b) + decisions.md ruling (c) — one code path, no second
resolution surface may exist)::

    DB override  >  os.environ[env_var]  >  yaml literal  >  registry default

Critical implementation note — the env tier is computed EXPLICITLY
from ``os.environ`` and parsed/validated DIRECTLY, NEVER via
pydantic's env tier: ``load_config`` passes the yaml sections as
pydantic init kwargs (``Config(**config_dict)``), and init kwargs
beat env vars in pydantic-settings — so while a yaml literal exists,
``LIMITS_LLM_CONCURRENCY`` is silently dead today. The boot apply
below is what makes the env tier (and the DB tier) effective for
managed keys.

MANAGED-KEY REGISTRY CAVEATS (binding registry docstring, decisions.md
ruling (c)):

* The **yaml tier is a BOOT-TIME snapshot** — there is no config.yaml
  hot-reload path today. The snapshot's ``yaml_value`` is the RAW
  file literal (when literally present in the file, ABSENT
  otherwise), sourced from the ``daemon/config.py`` raw-limits module
  accessor (``raw_yaml_limits()`` — module-global captured inside
  ``load_config`` from the processed limits-section dict BEFORE the
  pydantic merge; precedent ``_install_service_tool_enabled``). It is
  NEVER derived from the pydantic-merged Config field, which cannot
  distinguish a yaml literal from a field default.
* **Single-process invariant** — uvicorn runs with the default
  ``workers=1`` (``daemon/__main__.py:421-430``). If ``workers>1`` is
  ever set: the semaphore becomes per-process, boot apply runs
  per-process, and a PUT in worker A is invisible to worker B until
  both restart. Any future multi-process v2 needs a cross-process
  coordination lane — out of scope.
* **``sensitive=True` tripwire** — any future managed key carrying
  secret material in its value or tier values MUST set
  ``sensitive=True`` (GET/PUT responses omit the tier detail fields
  for sensitive keys; the PUT audit-log line in
  ``daemon/routers/settings.py`` logs ``value='<redacted>'`` instead
  of the raw value for sensitive keys; omission is a review blocker
  if missing).

v1.1 (settings-worker-slots) registry content: THREE keys —
``limits.llm_concurrency`` plus ``limits.worker_pool_size`` and
``limits.chat_worker_pool_size``. Adding a key = one registry entry +
nothing else (the ladder, CRUD, API and FE render generically off the
registry). The add-a-key rule stands: registry default mirrors the
pydantic Field default (constants -> Field -> registry, ONE truth).
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import Engine
from sqlmodel import Session, select

from daemon import constants
from daemon.models.app_settings import AppSetting

logger = logging.getLogger(__name__)


# =============================================================================
# Errors (typed rejections — the router maps these onto HTTP statuses)
# =============================================================================


class UnknownManagedKeyError(KeyError):
    """Raised when a key is not in the managed-key registry.

    The registry is the gate: unregistered keys are refused on EVERY
    read/write path (ruling (d) — no generic KV surface).
    """


class SettingsValidationError(ValueError):
    """Raised when a value fails parse or range validation for a
    registered key (write-time belt-and-braces rejection; the boot
    read uses the same validator but fails OPEN, per Ruling 3)."""


# =============================================================================
# Managed-key registry — the single source of managed-key truth
# =============================================================================


@dataclass(frozen=True)
class ManagedKeySpec:
    """Declarative spec for one managed setting key.

    ``value_type`` + ``ge`` together ARE the validator — they mirror
    the backing pydantic Field constraint EXACTLY (Ruling 1: no
    second, drifting truth of validation; no extra ``le`` in v1).
    """

    key: str
    config_path: str
    env_var: str
    value_type: str  # "int" | "float" | "bool" | "str"
    default: Any
    ge: int | None = None
    requires_restart: bool = True
    sensitive: bool = False
    description: str = ""

    def constraint_desc(self) -> str:
        """Human-readable constraint for log lines / 422 details
        (Ruling 3 log wording: ``failed validation (ge=1)``)."""
        return f"ge={self.ge}" if self.ge is not None else "none"


#: The managed-key registry. v1.1 (settings-worker-slots): THREE entries —
#: ``limits.llm_concurrency`` plus the two worker-pool sizes. Adding keys
#: later is a registry entry + nothing else (the ladder, CRUD, API and FE
#: render generically off the registry). The registry default MUST mirror
#: the backing pydantic Field default — chain: ``daemon.constants`` →
#: ``LimitsConfig`` Field → this registry default (ONE truth).
MANAGED_KEYS: dict[str, ManagedKeySpec] = {
    "limits.llm_concurrency": ManagedKeySpec(
        key="limits.llm_concurrency",
        config_path="limits.llm_concurrency",
        env_var="LIMITS_LLM_CONCURRENCY",
        value_type="int",
        # Mirrors LimitsConfig.llm_concurrency Field(default=10, ge=1)
        # (daemon/config.py) — the default tier of the ladder. The two
        # must move together; the registry is the resolver's truth.
        default=10,
        ge=1,
        requires_restart=True,
        sensitive=False,
        description=(
            "Maximum concurrent LLM calls across all instances "
            "(daemon-wide semaphore). Real parallelism is also capped "
            "by the worker thread pools — managed keys "
            "``limits.worker_pool_size`` + ``limits.chat_worker_pool_size`` "
            "on this page (effective concurrent graphs = "
            "min(llm_concurrency, worker_pool_size + chat_worker_pool_size), "
            "all boot-applied). Effective precedence: DB override > env var "
            "LIMITS_LLM_CONCURRENCY > config.yaml literal > default; "
            "changes require a daemon restart."
        ),
    ),
    # ── settings-worker-slots (D1) — the two worker-pool sizes ──────────
    # Default-pool worker threads. Mirrors LimitsConfig.worker_pool_size
    # Field(default=WORKER_POOL_SIZE, ge=1). Boot-applied by the SAME
    # generic machinery as llm_concurrency: PoolOrchestrator.setup reads
    # ``manager.config.limits.worker_pool_size`` once at boot.
    "limits.worker_pool_size": ManagedKeySpec(
        key="limits.worker_pool_size",
        config_path="limits.worker_pool_size",
        env_var="LIMITS_WORKER_POOL_SIZE",
        value_type="int",
        default=constants.WORKER_POOL_SIZE,
        ge=1,
        requires_restart=True,
        sensitive=False,
        description=(
            "Default-pool worker threads. Running value is boot-applied; "
            "changes apply at next daemon restart. Real LLM parallelism = "
            "min(limits.llm_concurrency, this pool + the other pool's "
            "boot-applied size). Precedence: DB override > env var "
            "LIMITS_WORKER_POOL_SIZE > config.yaml literal > default."
        ),
    ),
    # Chat-lane worker threads. Mirrors LimitsConfig.chat_worker_pool_size
    # Field(default=CHAT_WORKER_POOL_SIZE, ge=1). Read once at boot by
    # PoolOrchestrator.setup for the chat lane.
    "limits.chat_worker_pool_size": ManagedKeySpec(
        key="limits.chat_worker_pool_size",
        config_path="limits.chat_worker_pool_size",
        env_var="LIMITS_CHAT_WORKER_POOL_SIZE",
        value_type="int",
        default=constants.CHAT_WORKER_POOL_SIZE,
        ge=1,
        requires_restart=True,
        sensitive=False,
        description=(
            "Chat-lane worker threads. Running value is boot-applied; "
            "changes apply at next daemon restart. Real LLM parallelism = "
            "min(limits.llm_concurrency, this pool + the other pool's "
            "boot-applied size). Precedence: DB override > env var "
            "LIMITS_CHAT_WORKER_POOL_SIZE > config.yaml literal > default."
        ),
    ),
}


# =============================================================================
# Value parse + validate — the ONE validation truth
# =============================================================================


class _ParseError(ValueError):
    """Internal: raw value could not be parsed into value_type."""


def _coerce_value(value_type: str, raw: Any) -> Any:
    """Parse a raw tier value into the registry-declared type.

    Strict by design: bool never parses as int (JSON ``true`` must not
    smuggle past as 1), non-integral floats never parse as int,
    underscore digit separators are rejected (``"1_0"`` is not the
    canonical spelling of 10), non-finite floats are rejected, and
    strings must be cleanly int-parsable (``int("7.5")`` raises).
    """
    if value_type == "int":
        if isinstance(raw, bool):
            raise _ParseError(f"bool is not int: {raw!r}")
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str):
            text = raw.strip()
            if "_" in text:
                raise _ParseError(f"underscore separator not allowed in int: {raw!r}")
            return int(text)
        raise _ParseError(f"cannot parse {type(raw).__name__} as int: {raw!r}")
    if value_type == "float":
        if isinstance(raw, bool):
            raise _ParseError(f"bool is not float: {raw!r}")
        if isinstance(raw, (int, float)):
            parsed = float(raw)
        elif isinstance(raw, str):
            parsed = float(raw.strip())
        else:
            raise _ParseError(f"cannot parse {type(raw).__name__} as float: {raw!r}")
        if not math.isfinite(parsed):
            raise _ParseError(f"non-finite float not allowed: {raw!r}")
        return parsed
    if value_type == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
            return raw.strip().lower() == "true"
        if isinstance(raw, int) and raw in (0, 1):
            return bool(raw)
        raise _ParseError(f"cannot parse {raw!r} as bool")
    if value_type == "str":
        if isinstance(raw, str):
            return raw
        raise _ParseError(f"cannot parse {type(raw).__name__} as str: {raw!r}")
    raise _ParseError(f"unknown value_type {value_type!r}")


def parse_and_validate(spec: ManagedKeySpec, raw: Any) -> tuple[bool, Any, str | None, str | None]:
    """Parse + range-validate one raw tier value.

    Returns ``(ok, value, failure_class, detail)`` where
    ``failure_class`` is ``"parse"`` or ``"validation"`` (Ruling 3's
    two classes — the log wording keys off exactly these strings) and
    is ``None`` on success. Used by the resolver (tier skipping), the
    boot read (row skipping + ERROR logs), and the write path (422
    rejection) — one validation truth, three call sites.
    """
    try:
        value = _coerce_value(spec.value_type, raw)
    except (ValueError, TypeError) as exc:
        return False, None, "parse", str(exc)
    if spec.ge is not None and value < spec.ge:
        return (
            False,
            value,
            "validation",
            f"value {value!r} fails constraint {spec.constraint_desc()}",
        )
    return True, value, None, None


# =============================================================================
# Resolution snapshot (module-global, blind spot (b))
# =============================================================================


_resolution_snapshot: dict[str, dict[str, Any]] | None = None


def set_settings_resolution_snapshot(snapshot: Mapping[str, Mapping[str, Any]]) -> None:
    """Store the pristine pre-override tier snapshot (module-global).

    Called once from the api lifespan, right after the runtime
    ``SettingsService`` wiring (decisions.md ruling (b) RESOLVED). The
    snapshot is the SSOT input for the yaml tier of every later
    ``resolve`` — the live Config field is NEVER re-read for tier
    reporting (boot apply mutates it in place; reading it back would
    mis-report the DB value as the yaml literal).
    """
    global _resolution_snapshot
    _resolution_snapshot = {k: dict(v) for k, v in snapshot.items()} if snapshot else {}


def get_settings_resolution_snapshot() -> dict[str, dict[str, Any]] | None:
    """Return the stored snapshot, or ``None`` when never set
    (the router-layer getter turns ``None`` into a 503)."""
    return _resolution_snapshot


# =============================================================================
# THE ONE shared resolver — used by BOTH boot-apply and GET
# =============================================================================


def resolve(
    key: str,
    snapshot: Mapping[str, Mapping[str, Any]] | None,
    db_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one managed key over the 4-tier ladder — the SSOT.

    Ladder (highest wins; invalid tiers are SKIPPED and do not win)::

        live DB row  >  os.environ[env_var]  >  snapshot yaml  >  registry default

    * ``snapshot`` — the PRISTINE pre-override tier snapshot (from
      :func:`apply_db_settings_overrides`); its ``yaml_value`` is the
      RAW file literal (absent when not literally in the file).
    * ``db_overrides`` — the live DB tier input, supplied by the
      caller's engine context: the boot read passes its freshly-read
      throwaway-engine rows; GET passes
      ``SettingsService.get_overrides()`` from the manager's shared
      engine (Ruling 2's two-engine reality — the LADDER is the one
      shared path, the row read is necessarily context-supplied).
      ``None`` skips the DB tier (no caller context).
    * The env tier is read from ``os.environ`` DIRECTLY and parsed +
      validated here — NEVER via pydantic's env tier (the
      shadowing-bug fix, decisions.md ruling (c)).

    Returns ``{key, value, source, default, env_value, yaml_value,
    db_override}`` with ``source`` in ``{"db","env","yaml","default"}``.
    Tier detail fields carry each tier's PARSED, VALIDATED value (or
    ``None`` when the tier is absent or its raw value failed
    parse/validation — an invalid tier never wins and never renders).
    """
    spec = MANAGED_KEYS.get(key)
    if spec is None:
        raise UnknownManagedKeyError(key)

    snap: Mapping[str, Any] = (snapshot or {}).get(key, {}) or {}
    result: dict[str, Any] = {
        "key": key,
        "value": spec.default,
        "source": "default",
        "default": snap.get("default", spec.default),
        "env_value": None,
        "yaml_value": None,
        "db_override": None,
    }

    # Tier 3 — yaml literal (RAW literal from the pristine snapshot).
    raw_yaml = snap.get("yaml_value")
    if raw_yaml is not None:
        ok, value, failure_class, detail = parse_and_validate(spec, raw_yaml)
        if ok:
            result["yaml_value"] = value
            result["value"] = value
            result["source"] = "yaml"
        else:
            logger.warning(
                "[Settings] %s: invalid yaml tier value skipped — "
                "failed %s: %s (lower tier active)",
                key,
                failure_class,
                detail,
            )

    # Tier 2 — os.environ, read DIRECTLY (never pydantic's env tier).
    raw_env = os.environ.get(spec.env_var)
    if raw_env is not None:
        ok, value, failure_class, detail = parse_and_validate(spec, raw_env)
        if ok:
            result["env_value"] = value
            result["value"] = value
            result["source"] = "env"
        else:
            logger.warning(
                "[Settings] %s: invalid env tier value skipped (env_var=%s) — "
                "failed %s: %s (lower tier active)",
                key,
                spec.env_var,
                failure_class,
                detail,
            )

    # Tier 1 — live DB row (caller-supplied context).
    if db_overrides is not None:
        raw_db = db_overrides.get(key)
        if raw_db is not None:
            ok, value, _cls, _detail = parse_and_validate(spec, raw_db)
            if ok:
                result["db_override"] = value
                result["value"] = value
                result["source"] = "db"

    return result


# =============================================================================
# Row reading (shared by the boot read and the runtime service)
# =============================================================================


def _is_missing_table_error(exc: BaseException) -> bool:
    """Best-effort missing-table detection across both drivers.

    Load-bearing on EVERY first boot: the settings boot read runs
    BEFORE ``create_all`` (PG) / the SQLite migration runner, so a
    missing ``app_settings`` table is the NORMAL first-boot state and
    must read as "no overrides", not as an error.

    Detection order: driver-native FIRST (SQLSTATE ``42P01`` —
    undefined_table — via the DBAPI exception wrapped in ``exc.orig``;
    PostgreSQL-only), then an ANCHORED string-marker fallback for
    drivers without ``pgcode`` (SQLite). The markers name the relation
    explicitly — a bare "does not exist" (e.g. a missing COLUMN or
    enum-type error text) must NOT read as a missing table.
    """
    orig = getattr(exc, "orig", exc)
    if getattr(orig, "pgcode", None) == "42P01":
        return True
    message = str(orig).lower()
    return (
        'relation "app_settings" does not exist' in message
        or "no such table: app_settings" in message
    )


def _read_managed_rows(session: Session) -> dict[str, Any]:
    """Read + decode all ``app_settings`` rows for REGISTERED keys.

    Failure posture (Ruling 3 — fail-open, class-specific logging):

    * missing table → ``{}`` with a one-line INFO (first-boot normal).
    * unregistered row → WARNING + skip (cannot exist via the API;
      guards direct-DB tampering only).
    * ``value_type`` mismatch vs the registry → ERROR
      (``failed validation``) + skip.
    * undecodable ``value_json`` → ERROR (``failed parse``) + skip.
    * range failure → ERROR (``failed validation (ge=N)``) + skip.

    In every skip case the lower tier wins for that key (the resolver
    never sees the bad row).
    """
    try:
        rows = session.exec(select(AppSetting)).all()
    except Exception as exc:
        if _is_missing_table_error(exc):
            logger.info(
                "[Settings] app_settings table absent (first boot or "
                "pre-migration) — no db overrides; lower tiers active"
            )
            return {}
        raise

    overrides: dict[str, Any] = {}
    for row in rows:
        spec = MANAGED_KEYS.get(row.key)
        if spec is None:
            logger.warning(
                "[Settings] ignoring unregistered app_settings row key=%r — "
                "registry is the write gate; lower tiers stay active",
                row.key,
            )
            continue
        if row.value_type != spec.value_type:
            logger.error(
                "[Settings] app_settings row key=%r skipped: failed validation "
                "(value_type %r != registry %r) — lower tier wins",
                row.key,
                row.value_type,
                spec.value_type,
            )
            continue
        try:
            decoded = json.loads(row.value_json)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error(
                "[Settings] app_settings row key=%r skipped: failed parse: %s "
                "— lower tier wins",
                row.key,
                exc,
            )
            continue
        ok, value, failure_class, detail = parse_and_validate(spec, decoded)
        if not ok:
            if failure_class == "parse":
                # Ruling 3 wording: "failed parse: <exc>"
                logger.error(
                    "[Settings] app_settings row key=%r skipped: failed parse: %s "
                    "— lower tier wins",
                    row.key,
                    detail,
                )
            else:
                # Ruling 3 wording: "failed validation (ge=1)"
                logger.error(
                    "[Settings] app_settings row key=%r skipped: failed validation "
                    "(%s): %s — lower tier wins",
                    row.key,
                    spec.constraint_desc(),
                    detail,
                )
            continue
        overrides[row.key] = value
    return overrides


# =============================================================================
# SettingsService — runtime CRUD on the manager's shared engine
# =============================================================================


def _now_iso() -> str:
    """ISO-8601 UTC timestamp (application-stamped; the secrets
    ``_now_iso`` convention of this table lineage)."""
    return datetime.now(timezone.utc).isoformat()


class SettingsService:
    """Registry-gated CRUD over ``app_settings`` (runtime lane).

    Engine-bound to the manager's SINGLE shared engine (Ruling 2 —
    wired ``SettingsService(engine=manager.engine)`` in the api
    lifespan; the throwaway bootstrap engine exists ONLY inside
    :func:`apply_db_settings_overrides`). All key-argued methods
    refuse unregistered keys with :class:`UnknownManagedKeyError`.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_overrides(self) -> dict[str, Any]:
        """Return ``{key: decoded value}`` for every VALID registered
        row (the resolver's DB-tier input). Fail-open per-row posture
        mirrors the boot read."""
        with Session(self._engine) as session:
            return _read_managed_rows(session)

    def set_override(self, key: str, value: Any, updated_by: str | None = None) -> None:
        """Upsert the DB override row for a registered key.

        Validates (parse + range) BEFORE writing — write-time
        belt-and-braces rejection (Ruling 3); invalid values raise
        :class:`SettingsValidationError` (the router maps to 422).
        ``updated_by`` is the actor subject, ``None`` when auth is off
        (never the empty string — the empty string is normalized to
        ``None`` here).
        """
        spec = MANAGED_KEYS.get(key)
        if spec is None:
            raise UnknownManagedKeyError(key)
        ok, parsed, failure_class, detail = parse_and_validate(spec, value)
        if not ok:
            raise SettingsValidationError(
                f"{key}: {failure_class} failure ({detail}); "
                f"value must be {spec.value_type} with constraint {spec.constraint_desc()}"
            )
        row = AppSetting(
            key=spec.key,
            value_json=json.dumps(parsed),
            value_type=spec.value_type,
            updated_by=updated_by or None,
            updated_at=_now_iso(),
        )
        with Session(self._engine) as session:
            session.merge(row)
            session.commit()

    def clear_override(self, key: str) -> bool:
        """Delete the override row for a registered key (idempotent —
        ``False`` when no row existed). Returns whether a row was
        removed."""
        spec = MANAGED_KEYS.get(key)
        if spec is None:
            raise UnknownManagedKeyError(key)
        with Session(self._engine) as session:
            row = session.get(AppSetting, spec.key)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True


# =============================================================================
# Boot apply — lifespan hook between load_config() and InstanceManager
# =============================================================================


def _build_bootstrap_engine(ensemble_config: Any, config: Any) -> Engine:
    """Build the THROWAWAY engine for the boot read (Ruling 2).

    Mirrors the manager's own engine selection exactly
    (``daemon/manager.py`` — PG via ``create_postgres_engine`` on
    ``ensemble_config.is_postgres``; SQLite via
    ``DatabaseConfig.sqlite(db_path=config.persistence.db_path)``).
    The manager's shared engine does not exist yet at the call site.
    Serves the BOOT READ ONLY and is disposed by the caller.
    """
    from daemon.repositories.factory import (
        DatabaseConfig,
        create_engine_from_config,
        create_postgres_engine,
    )

    if ensemble_config is not None and ensemble_config.is_postgres:
        return create_postgres_engine(ensemble_config)
    db_path = getattr(getattr(config, "persistence", None), "db_path", None) or "data.db"
    return create_engine_from_config(DatabaseConfig.sqlite(db_path=str(db_path)))


def _raw_yaml_leaf(raw_yaml_limits: Any, spec: ManagedKeySpec) -> Any:
    """Return the RAW yaml literal for a managed key, or ``None``.

    ``raw_yaml_limits`` is the SECTION dict captured by the
    ``daemon/config.py`` accessor (the section named by the FIRST
    component of ``config_path`` — ``limits`` today); the remaining
    components walk to the leaf. ABSENT means "not literally present
    in config.yaml" — the pydantic-merged field is NEVER consulted
    (it cannot distinguish a yaml literal from the field default;
    deriving from it would mis-attribute ``source="yaml"`` on a
    default-only install and fail the default-tier test).
    """
    if not isinstance(raw_yaml_limits, dict):
        return None
    node: Any = raw_yaml_limits
    for part in spec.config_path.split(".")[1:]:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _get_config_value(config: Any, config_path: str) -> Any:
    obj = config
    for part in config_path.split("."):
        obj = getattr(obj, part)
    return obj


def _set_config_value(config: Any, config_path: str, value: Any) -> None:
    parts = config_path.split(".")
    obj = config
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


def apply_db_settings_overrides(
    config: Any,
    ensemble_config: Any,
    raw_yaml_limits: Any = None,
) -> tuple[Any, dict[str, dict[str, Any]]]:
    """Apply DB-backed overrides onto the live Config at boot.

    Called ONCE from the api lifespan, between ``load_config()`` and
    ``InstanceManager(...)`` — the manager then constructs the
    daemon-wide LLM semaphore from the patched
    ``config.limits.llm_concurrency`` with ZERO downstream changes
    (restart-required semantics by construction).

    Steps (decisions.md ruling (b)):

    1. Capture the PRISTINE pre-override tier snapshot
       ``{key: {default, env_value, yaml_value}}`` — default from the
       registry, env straight from ``os.environ`` at this moment
       (raw string, evidence-grade), yaml from the RAW file literal
       via ``raw_yaml_limits`` (ABSENT when not literally present).
    2. Read all rows through a THROWAWAY engine (boot read ONLY;
       disposed in ``finally`` on BOTH success and failure). Missing
       table → no overrides (fail-open — load-bearing on every first
       boot, both backends: this read runs BEFORE ``create_all`` and
       the SQLite migration runner).
    3. Resolve each managed key through the ONE shared
       :func:`resolve` and mutate the Config field IN PLACE with the
       WINNING value — this is also what makes the env tier effective
       despite the yaml literal (the shadowing fix). ``Config`` is a
       plain mutable BaseSettings (no ``frozen``, no
       ``validate_assignment``) so direct assignment works;
       re-validation happened in the resolver.
    4. Capture ``applied_value`` (post-apply, just before return)
       completing the per-key snapshot
       ``{default, env_value, yaml_value, applied_value}``.

    FAIL-OPEN EVERYWHERE (Ruling 3): any read error (dead DB, corrupt
    row, parse/range failure) logs ERROR with the failure class and
    continues boot on the lower tiers — worst case is "the feature is
    inert", never "the daemon does not start".

    Returns ``(config, snapshot)`` — the same Config object, mutated
    in place, plus the completed snapshot.
    """
    snapshot: dict[str, dict[str, Any]] = {}

    # Step 1 — pristine tiers, BEFORE any mutation (blind spot (b)).
    for key, spec in MANAGED_KEYS.items():
        snapshot[key] = {
            "default": spec.default,
            "env_value": os.environ.get(spec.env_var),
            "yaml_value": _raw_yaml_leaf(raw_yaml_limits, spec),
        }

    # Step 2 — boot read via the throwaway engine, disposed on BOTH
    # success and failure (adopted note 8b).
    db_overrides: dict[str, Any] = {}
    engine: Engine | None = None
    try:
        engine = _build_bootstrap_engine(ensemble_config, config)
        with Session(engine) as session:
            db_overrides = _read_managed_rows(session)
    except Exception as exc:
        db_overrides = {}
        logger.error(
            "[Settings] boot read failed (%s): %s — fail-open, lower tiers "
            "active; [Settings] applied 0 db override(s)",
            type(exc).__name__,
            exc,
        )
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                logger.warning(
                    "[Settings] bootstrap engine dispose failed", exc_info=True
                )

    # Step 3+4 — resolve, mutate in place, capture applied_value.
    applied_count = 0
    for key, spec in MANAGED_KEYS.items():
        resolution = resolve(key, snapshot, db_overrides=db_overrides)
        if resolution["source"] == "db":
            applied_count += 1
        current = _get_config_value(config, spec.config_path)
        if resolution["value"] != current:
            # Sensitive-key tripwire: BOTH %r args redacted (same
            # posture as the router PUT audit line) — a future
            # secret-bearing key must never leak through this line.
            logged_current = "<redacted>" if spec.sensitive else current
            logged_value = "<redacted>" if spec.sensitive else resolution["value"]
            logger.info(
                "[Settings] %s: %s tier wins — config field %r -> %r "
                "(applies on this boot; restart-required semantics)",
                key,
                resolution["source"],
                logged_current,
                logged_value,
            )
            _set_config_value(config, spec.config_path, resolution["value"])
        snapshot[key]["applied_value"] = _get_config_value(config, spec.config_path)

    # ONE summary INFO per invocation on ALL exit paths (council
    # follow-up) — every apply_db_settings_overrides exit is uniformly
    # grep-able. On a read-failure path applied_count is 0 by
    # construction (db_overrides is reset to {} in the except above),
    # so N always reflects reality.
    logger.info("[Settings] applied %d db override(s)", applied_count)

    return config, snapshot
