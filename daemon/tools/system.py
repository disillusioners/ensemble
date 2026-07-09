"""Read-only system information tools.

This module exposes three tools for inspecting ensemble runtime state:

* ``system_env`` — list curated environment variables (secrets masked).
* ``system_config`` — show the resolved ``Config`` (sections, secrets
  masked).
* ``system_health`` — return a small health snapshot (version, DB
  backend, RAG flag, Python / OS info, data dir, PID).

All three tools are **read-only** and safe to call from any agent. They
do not mutate config, the database, or the filesystem. The tools are
created inside a factory (``create_system_tools``) so the tool
functions close over the shared :class:`InstanceManager` and see live
``manager.config`` / ``manager.ensemble_config`` values.

Secret-masking policy
---------------------

By default the tools redact values whose key looks like a secret
(``api_key``, ``password``, ``token``, ``secret``, ``headers``) as
well as values in an explicit allow-list of known-secret environment
variable names. Config keys containing ``base`` are treated as public
URL-like endpoints: plain URLs stay visible, while embedded URL
passwords are masked surgically. To inspect a real value, pass
``nomask=True``. The full docstrings attached via ``_full_doc_``
explicitly warn the agent to use that escape hatch only when truly
needed.

URL/connection-string values are masked at the password component
(``postgresql://user:pass@host:5432/db`` → ``postgresql://user:[REDACTED]@host:5432/db``)
so the host/port/db remain visible for debugging while the credential
does not leak.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
import urllib.parse
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

from ..config import Config
from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "System"
CATEGORY_DOC = """\
Read-only diagnostics for ensemble runtime state.

- `system_env` — List curated environment variables (secrets masked).
- `system_config` — Show the resolved Config (sections, secrets masked).
- `system_health` — Quick health snapshot (version, DB backend, RAG, PID).

All three tools are read-only. Secrets are masked by default; pass
``nomask=True`` to a tool to retrieve raw values when a task genuinely
needs them.
"""


# Curated env var prefixes/names that are relevant to ensemble.
_TRACKED_ENV_PREFIXES = [
    "ENSEMBLE_",
    "OPENAI_",
    "POSTGRES_",
    "RAG_IS_REQUIRED",
    "MCP_",
    "LIGHTRAG_",
    "OPENSPACE_",
    "QUEUE_DISCARD_ON_STARTUP",
]
_TRACKED_ENV_EXACT = [
    "DATABASE_URL_POSTGRES",
    "POSTGRES_URL",
    "SOURCE_CREDENTIAL_KEY",
    "RAG_IS_REQUIRED",
    "TEMP",
    "TMP",
]

# Explicit env var names that are ALWAYS masked (unless nomask=True),
# even if they don't match the suffix patterns above (e.g. LIGHT_RAG_*).
_SECRET_ENV_VARS: frozenset[str] = frozenset({
    "OPENSPACE_API_KEY",
    "OPENSPACE_LLM_API_KEY",
    "OPENSPACE_LLM_API_BASE",
    "OPENSPACE_LLM_EXTRA_HEADERS",
    "SOURCE_CREDENTIAL_KEY",
    "LIGHTRAG_API_KEY",
    "POSTGRES_URL",
    "DATABASE_URL_POSTGRES",
})

# Suffix patterns used by ``_is_secret_key`` to flag values as secrets
# when they appear in nested config dicts.
_SECRET_KEY_SUBSTRINGS: tuple[str, ...] = (
    "api_key",
    "password",
    "token",
    "secret",
    "headers",
    "base",
)
_NON_BASE_SECRET_KEY_SUBSTRINGS: tuple[str, ...] = tuple(
    sub for sub in _SECRET_KEY_SUBSTRINGS if sub != "base"
)

# Env var suffix patterns used by ``_mask_env_value`` to mask values
# whose key LOOKS like a secret but isn't in the explicit allow-list
# (e.g. ``OPENAI_API_KEY``, ``ANTHROPIC_AUTH_TOKEN``).
_SECRET_SUFFIXES: tuple[str, ...] = (
    "_API_KEY",
    "_TOKEN",
    "_PASSWORD",
    "_SECRET",
    "_HEADERS",
)


# ---------------------------------------------------------------------------
# Masking helpers
# ---------------------------------------------------------------------------


def _is_secret_key(key_name: str) -> bool:
    """Return True if ``key_name`` looks like a secret-bearing field name.

    Used for nested config masking. Match is case-insensitive substring
    against the curated patterns: ``api_key``, ``password``, ``token``,
    ``secret``, ``headers``, ``base``. The ``base`` substring covers
    ``*_api_base`` style URLs whose value is often a credential-bearing
    endpoint.

    Args:
        key_name: The dict key to inspect.

    Returns:
        True if the key matches any pattern, False otherwise.
    """
    if not isinstance(key_name, str):
        return False
    lowered = key_name.lower()
    return any(sub in lowered for sub in _SECRET_KEY_SUBSTRINGS)


def _mask_connection_string(value: str) -> str:
    """If ``value`` is a URL with an embedded password, mask the password.

    Parses ``value`` with :func:`urllib.parse.urlparse`. If the netloc
    contains a ``:``-separated password component, the password is
    replaced with ``[REDACTED]`` while the user, host, port, path, and
    query are preserved. Non-URL strings and URL strings without a
    password are returned unchanged.

    Args:
        value: The candidate string.

    Returns:
        The masked string (or the original input if no password was
        found).
    """
    if not isinstance(value, str) or not value:
        return value
    # Cheap pre-check: a URL with an embedded password must contain
    # ``://`` followed eventually by an ``@``. If neither is present,
    # skip the parser entirely.
    if "://" not in value or "@" not in value:
        return value
    try:
        parsed = urllib.parse.urlparse(value)
    except (ValueError, TypeError):
        return value
    if not parsed.netloc or "@" not in parsed.netloc:
        return value
    # netloc format: [user[:password]@]host[:port]
    # urlparse doesn't split userinfo, so do it manually.
    userinfo, _, hostport = parsed.netloc.rpartition("@")
    if ":" not in userinfo:
        # No password — return unchanged.
        return value
    username, _, _ = userinfo.partition(":")
    new_netloc = f"{username}:[REDACTED]@{hostport}"
    return urllib.parse.urlunparse(parsed._replace(netloc=new_netloc))


def _mask_secret(value: Any) -> Any:
    """Recursively mask secret-bearing values.

    Behaviour by type:

    * ``None`` or empty string → ``"[REDACTED]"`` placeholder is
      inappropriate for a missing value; leave ``None`` and ``""`` as
      they are.
    * ``str`` → if it looks like a URL with an embedded password, mask
      only the password component. Otherwise return the literal
      placeholder ``"[REDACTED]"``.
    * ``dict`` → return a NEW dict where every value is recursively
      masked. Keys are kept verbatim (the agent still needs to see the
      field names to understand the config).
    * ``list`` / ``tuple`` → return a new sequence where every item is
      recursively masked.
    * Anything else (int, float, bool, custom objects) → replaced with
      the literal placeholder.

    Args:
        value: The candidate value to mask.

    Returns:
        The masked representation.
    """
    if value is None or value == "":
        return value
    if isinstance(value, str):
        masked = _mask_connection_string(value)
        # If the string contained an embedded password, _mask_connection_string
        # returned a transformed string (different from the original). The
        # test below distinguishes the two cases so plain strings become
        # "[REDACTED]" while connection-string passwords get surgical
        # redaction.
        if masked is not value and "[REDACTED]" in masked:
            return masked
        return "[REDACTED]"
    if isinstance(value, dict):
        return {k: _mask_secret(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_secret(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_mask_secret(item) for item in value)
    # int / float / bool / objects → replace with placeholder.
    return "[REDACTED]"


def _mask_env_value(var_name: str, value: str, nomask: bool) -> str:
    """Apply masking rules to a single environment variable value.

    Masking escalation:

    1. ``nomask=True`` → return ``value`` verbatim (escape hatch).
    2. ``var_name`` is in :data:`_SECRET_ENV_VARS` → return
       ``"[REDACTED]"``.
    3. ``var_name`` ends with any suffix in :data:`_SECRET_SUFFIXES`
       (``_API_KEY``, ``_TOKEN``, ``_PASSWORD``, ``_SECRET``,
       ``_HEADERS``) → return ``"[REDACTED]"``.
    4. ``value`` parses as a URL with an embedded password → mask the
       password component only (e.g. for ``POSTGRES_URL`` variants not
       in the explicit list).
    5. Otherwise → return ``value`` unchanged.

    Args:
        var_name: The environment variable name (used for pattern
            matching).
        value: The raw environment variable value.
        nomask: If True, bypass all masking.

    Returns:
        The (possibly masked) value.
    """
    if nomask:
        return value
    if var_name in _SECRET_ENV_VARS:
        return "[REDACTED]"
    upper = var_name.upper()
    if any(upper.endswith(suffix) for suffix in _SECRET_SUFFIXES):
        return "[REDACTED]"
    masked = _mask_connection_string(value)
    if masked is not value and "[REDACTED]" in masked:
        return masked
    return value


def _mask_config(obj: Any) -> Any:
    """Recursively mask a config section according to ``_is_secret_key``.

    Walks the structure returned by ``Config.model_dump()``. Dict keys
    that satisfy :func:`_is_secret_key` have their values masked based
    on the matching pattern: ``base``-matching keys are treated as
    URL-like endpoints and get only embedded URL passwords redacted;
    all other secret-looking keys use :func:`_mask_secret` for blanket
    redaction. Other dict/list values are recursed into. Leaf strings
    are additionally passed through :func:`_mask_connection_string` so
    a top-level key like ``postgres_url`` whose name doesn't itself
    match the secret patterns still has its embedded password scrubbed
    — defence in depth against credential leakage.

    Args:
        obj: The config subtree to mask.

    Returns:
        A masked copy of ``obj``.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, str):
                lowered = k.lower()
                if any(sub in lowered for sub in _NON_BASE_SECRET_KEY_SUBSTRINGS):
                    out[k] = _mask_secret(v)
                    continue
                if "base" in lowered:
                    if isinstance(v, str):
                        out[k] = _mask_connection_string(v)
                    else:
                        out[k] = _mask_config(v)
                    continue
            out[k] = _mask_config(v)
        return out
    if isinstance(obj, list):
        return [_mask_config(item) for item in obj]
    if isinstance(obj, str):
        # Leaf-level connection-string scrubbing. Mirrors the
        # ``system_env`` policy: any string that parses as a URL with
        # an embedded password gets the password component replaced
        # with ``[REDACTED]``. Strings without a password (or that
        # aren't URLs at all) are returned unchanged.
        return _mask_connection_string(obj)
    return obj


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_system_tools(manager: "InstanceManager", current_instance_id: str) -> list:
    """Create the System tool category tools.

    All three tools are defined inside this factory closure so they
    capture ``manager`` directly. They never raise — I/O errors are
    caught and returned as JSON error strings so the agent sees a
    structured failure instead of an exception bubbling up.

    Args:
        manager: The :class:`InstanceManager` instance. Used to read
            ``manager.config`` and ``manager.ensemble_config``.
        current_instance_id: The current instance ID, included in
            ``nomask=True`` audit logs.

    Returns:
        A list of three tool functions:
        ``[system_env, system_config, system_health]``.
    """

    @register_tool_category("system")
    @tool
    async def system_env(prefix: str = "", nomask: bool = False) -> str:
        """List curated ensemble environment variables as JSON. Secrets are masked by default. Use tool_help("system_env") for details."""
        try:
            result: dict[str, str] = {}
            for key, value in os.environ.items():
                # Match either prefix or exact-name rules.
                if not (any(key.startswith(p) for p in _TRACKED_ENV_PREFIXES)
                        or key in _TRACKED_ENV_EXACT):
                    continue
                # Optional further filter by caller-supplied prefix
                # (case-insensitive).
                if prefix and not key.lower().startswith(prefix.lower()):
                    continue
                result[key] = _mask_env_value(key, value, nomask)
            if nomask:
                logger.warning("system_env NOMASK used by instance=%s", current_instance_id)
            return json.dumps(result, indent=2, sort_keys=True)
        except Exception as exc:
            logger.warning("system_env failed: %s", exc)
            return json.dumps({"error": f"system_env failed: {type(exc).__name__}"})

    system_env._full_doc_ = """List a curated set of environment variables as JSON.

The tool enumerates ``os.environ`` and includes a variable if its name
starts with one of the tracked prefixes (``ENSEMBLE_``, ``OPENAI_``,
``POSTGRES_``, ``RAG_IS_REQUIRED``, ``MCP_``, ``LIGHTRAG_``,
``OPENSPACE_``, ``QUEUE_DISCARD_ON_STARTUP``) OR equals one of the
tracked exact names (``DATABASE_URL_POSTGRES``, ``POSTGRES_URL``,
``SOURCE_CREDENTIAL_KEY``, ``RAG_IS_REQUIRED``, ``TEMP``, ``TMP``).

Args:
    prefix: Optional case-insensitive filter. When non-empty, only
        variables whose name starts with this substring are returned
        (applied AFTER the curated-prefix filter, so it further narrows
        rather than widens).
    nomask: When True, secret values are returned in the clear. When
        False (the default), values are masked according to the policy
        below.

Masking policy (applied when ``nomask=False``):

* The value is returned as ``"[REDACTED]"`` if the variable name is in
  the explicit secret allow-list (``OPENSPACE_API_KEY``,
  ``OPENSPACE_LLM_API_KEY``, ``OPENSPACE_LLM_API_BASE``,
  ``OPENSPACE_LLM_EXTRA_HEADERS``, ``SOURCE_CREDENTIAL_KEY``,
  ``LIGHTRAG_API_KEY``, ``POSTGRES_URL``,
  ``DATABASE_URL_POSTGRES``) OR if its name ends with any of
  ``_API_KEY``, ``_TOKEN``, ``_PASSWORD``, ``_SECRET``, ``_HEADERS``.
* Otherwise, if the value parses as a URL with an embedded password
  (``scheme://user:pass@host/...``), only the password component is
  replaced with ``[REDACTED]`` while the rest of the URL is preserved.
* Otherwise, the value is returned unchanged.

Returns:
    A JSON object mapping variable name → (possibly masked) value.
    Keys are sorted alphabetically for stable output. Returns an
    ``{"error": ...}`` object if the enumeration itself fails — never
    raises.

Secrets are masked by default. If your task needs the real secret value
to continue, recall the tool with ``nomask=True``.
"""

    @register_tool_category("system")
    @tool
    async def system_config(section: str = "", nomask: bool = False) -> str:
        """Show the resolved Config (sections, secrets masked) as JSON. Use tool_help("system_config") for details."""
        try:
            config = getattr(manager, "config", None)
            if config is None:
                return json.dumps(
                    {"error": "manager.config is not available (manager not initialized?)"},
                    indent=2,
                )
            # Derive valid sections dynamically from the Pydantic model
            # fields. Do NOT hardcode — new sections added to the
            # ``Config`` class will appear here automatically.
            try:
                valid_sections = list(Config.model_fields.keys())
            except Exception as exc:
                logger.warning("system_config: failed to enumerate config sections: %s", exc)
                return json.dumps(
                    {"error": f"failed to enumerate config sections: {type(exc).__name__}"},
                    indent=2,
                )

            full = config.model_dump()

            if section:
                if section not in valid_sections:
                    return json.dumps(
                        {
                            "error": (
                                f"Unknown section '{section}'. Valid sections: "
                                f"{sorted(valid_sections)}"
                            )
                        },
                        indent=2,
                    )
                payload: dict[str, Any] = {section: full.get(section)}
            else:
                payload = full

            if not nomask:
                payload = _mask_config(payload)
            else:
                logger.warning("system_config NOMASK used by instance=%s", current_instance_id)

            return json.dumps(payload, indent=2, default=str)
        except Exception as exc:
            logger.warning("system_config failed: %s", exc)
            return json.dumps({"error": f"system_config failed: {type(exc).__name__}"})

    system_config._full_doc_ = """Show the resolved ensemble ``Config`` as JSON.

The list of valid sections is derived dynamically from
``manager.config.model_fields.keys()`` — every section the ``Config``
Pydantic model declares is recognised without hardcoding. As of this
writing that set is ``{llm, daemon, limits, persistence, agents, queue,
compaction, services, job_system, mcp_pool}``, but new sections show
up here automatically when added.

Args:
    section: Optional section name to return. When empty, ALL sections
        are returned. When non-empty and not in the valid set, an
        ``{"error": ...}`` object is returned listing the valid names.
    nomask: When True, secret-bearing fields are returned in the
        clear. When False (the default), values whose key matches
        ``api_key``, ``password``, ``token``, ``secret``, or
        ``headers`` (case-insensitive substring) are replaced with
        ``"[REDACTED]"``. Values whose key matches ``base`` are treated
        as URL-like endpoints: plain URLs remain visible, and embedded
        URL passwords are masked surgically.

Returns:
    A JSON object with the requested section(s) of the resolved
    config. Returns ``{"error": ...}`` if the manager has no config or
    the introspection itself fails — never raises.

Secrets are masked by default. If your task needs the real secret value
to continue, recall the tool with ``nomask=True``.
"""

    @register_tool_category("system")
    @tool
    async def system_health() -> str:
        """Return a small health snapshot of the ensemble runtime. Use tool_help("system_health") for details."""
        try:
            ensemble_cfg = getattr(manager, "ensemble_config", None)
            if ensemble_cfg is not None:
                # Prefer the explicit backend selector — older
                # configurations may default to sqlite while still
                # being a postgres cluster, so the explicit field is
                # authoritative.
                database_type = getattr(ensemble_cfg, "database", "sqlite")
            else:
                database_type = "sqlite"

            # RAG toggle — pulled from the same flag the rest of the
            # codebase consults (knowledge_tools, rag_tools, inner_soul,
            # instance.py). Importing the symbol inline so the system
            # tool does not add a new top-level dependency edge.
            from daemon.rag.config import is_rag_enabled
            try:
                rag_enabled = is_rag_enabled()
            except Exception as exc:
                logger.debug("is_rag_enabled() raised in system_health: %s", exc)
                rag_enabled = False

            config = getattr(manager, "config", None)
            data_directory: str | None = None
            if config is not None:
                try:
                    # Prefer the manager.data_dir property — it already
                    # knows the real data directory anchor. Fall back
                    # to the persistence.db_path parent for the rare
                    # case where the manager was constructed without
                    # going through the lifespan.
                    data_dir_prop = getattr(manager, "data_dir", None)
                    if data_dir_prop is not None:
                        data_directory = str(data_dir_prop)
                    else:
                        db_path = getattr(config.persistence, "db_path", None)
                        if db_path:
                            data_directory = str(db_path).rsplit("/", 1)[0]
                except Exception as exc:
                    logger.debug("system_health: data_directory lookup failed: %s", exc)

            result = {
                "version": getattr(sys.modules.get("daemon"), "__version__", None)
                           or "0.0.0",
                "database_type": database_type,
                "rag_enabled": rag_enabled,
                "python_version": platform.python_version(),
                "platform": platform.system(),
                "platform_machine": platform.machine(),
                "data_directory": data_directory,
                "process_pid": os.getpid(),
            }
            return json.dumps(result, indent=2)
        except Exception as exc:
            logger.warning("system_health failed: %s", exc)
            return json.dumps({"error": f"system_health failed: {type(exc).__name__}"})

    system_health._full_doc_ = """Return a small health snapshot of the ensemble runtime.

The snapshot is intended for fast triage: it answers "which version
am I running, what database is in use, is RAG available, and where is
my data dir" without exposing any credential material.

The result includes:

* ``version`` — value of ``daemon.__version__``.
* ``database_type`` — ``"postgres"`` or ``"sqlite"`` (from
  ``manager.ensemble_config.database``; defaults to ``"sqlite"`` when
  the manager has no EnsembleConfig).
* ``rag_enabled`` — result of :func:`daemon.rag.config.is_rag_enabled`.
* ``python_version`` — ``platform.python_version()``.
* ``platform`` — ``platform.system()`` (e.g. ``"Darwin"``,
  ``"Linux"``).
* ``platform_machine`` — ``platform.machine()`` (e.g. ``"x86_64"``,
  ``"arm64"``).
* ``data_directory`` — the directory containing the SQLite DBs and
  ``ensemble.json``. Resolved via ``manager.data_dir`` when available,
  falling back to the parent of ``manager.config.persistence.db_path``.
* ``process_pid`` — ``os.getpid()``.

Returns:
    A JSON object with the eight fields above. Returns
    ``{"error": ...}`` if the snapshot itself fails — never raises.
"""

    return [system_env, system_config, system_health]
