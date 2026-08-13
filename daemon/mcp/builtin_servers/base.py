"""Abstract base class for built-in MCP server definitions."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


class BuiltinServerDefinition(ABC):
    """Abstract base class for built-in MCP server definitions."""

    # Optional Python package required for this built-in to function.
    # ``None`` (default) means the built-in has no optional Python
    # dependency — it relies only on external CLI binaries (``uvx``,
    # ``npx``) that we cannot introspect without spawning subprocesses.
    # Subclasses override this with the importable package name (e.g.
    # ``"my-mcp-server"``) when they require an optional Python package
    # that may not be installed. ``is_available()`` consults this
    # attribute; the bootstrap and warmup pool layers rely on
    # ``is_available()`` to skip unavailable built-ins cleanly
    # (no DB record, no connection attempt).
    required_package: ClassVar[str | None] = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this built-in server."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for display."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what this server does."""
        ...

    @property
    @abstractmethod
    def schema_version(self) -> str:
        """Version of the config schema. Changes trigger schema drift detection."""
        ...

    @property
    def tool_call_timeout(self) -> int | None:
        """Per-server tool call timeout in seconds, or None for the default.

        Subclasses override to request a longer timeout for tools that may
        run long-running operations (e.g. agent execution). Returns None
        by default — callers fall back to the pool-wide
        ``McpPoolConfig.tool_call_timeout``.
        """
        return None

    @property
    def tool_name_prefix(self) -> str | None:
        """Override the tool name prefix for this server's tools.

        When ``None`` (default), tools use the standard
        ``mcp_{server_name}_`` prefix (e.g. ``mcp_context7_get_docs``).
        When set (e.g. ``"plane"``), tools use ``{prefix}_`` instead —
        e.g. ``plane_list_issues`` instead of ``mcp_plane_list_issues``.

        This is for essential built-in servers whose tools should feel
        native to specific agents rather than appearing as add-on MCP
        tools. The MCP dispatch is unaffected: the lazy coroutine always
        closes over the original MCP tool name; only the EXPOSED name
        changes.
        """
        return None

    @property
    def resilience_config(self) -> "ResilienceConfig | None":
        """Per-server resilience configuration. ``None`` = no resilience.

        Subclasses override to opt into retry, caching, circuit breaking,
        and graceful degradation. Default ``None`` preserves current
        behavior for servers that haven't opted in (context7, webfetch,
        any future default-resilience servers).

        The returned ``ResilienceConfig`` is consumed by
        ``daemon.mcp.tool_adapter._lazy_coroutine`` via
        ``ResilienceManager`` — when this returns ``None``, the lazy
        coroutine falls back to the no-resilience path (the existing
        behavior, unchanged). When it returns a config, the manager
        creates a circuit breaker + (optional) cache for the server and
        every tool call runs through the full resilience flow.

        Currently the only override is ``PlaneServerDefinition``. See
        ``daemon/mcp/resilience.py`` for the config schema.
        """
        return None

    @classmethod
    def is_available(cls) -> bool:
        """Check if this builtin's external dependencies are installed.

        Subclasses override ``required_package`` (or override this method
        directly) when they require an optional Python package that may
        not be installed (e.g. ``my-mcp-server`` for a third-party
        built-in). When this returns ``False``, the bootstrap and warmup
        pool layers skip the server entirely — no DB record, no
        connection attempt — rather than failing later with an opaque
        subprocess error.

        Returns:
            ``True`` if the built-in can be used safely. Default ``True``
            covers built-ins whose only dependencies are external CLI
            binaries (``uvx``, ``npx``) and are therefore always available
            when the daemon can spawn subprocesses.
        """
        if cls.required_package is None:
            return True
        import importlib.util
        try:
            return importlib.util.find_spec(cls.required_package) is not None
        except (ImportError, ValueError):
            # ImportError covers ModuleNotFoundError (missing parent
            # package). ValueError catches invalid spec formats from
            # find_spec on relative imports / weird sys.path setups.
            return False

    def get_base_config(self) -> dict[str, Any]:
        """
        Return base configuration including transport, command, and fixed args.

        Subclasses override to provide server-specific base config like:
        {
            "transport": "stdio",
            "command": "uvx",
            "args": ["mcp-server-fetch"]
        }
        """
        return {}

    @abstractmethod
    def get_config_schema(self) -> list[dict[str, Any]]:
        """Return the config schema as a list of ConfigSchemaField dicts."""
        ...

    def build_config(self, user_values: dict[str, Any]) -> dict[str, Any]:
        """
        Generic config builder. Iterates schema fields and generates args/env.

        Algorithm:
        1. Get base config from get_base_config()
        2. Get schema from get_config_schema()
        3. For each field, resolve value: user_values[key] if present, else field['default']
        4. Skip if resolved value is None or empty string ("")
        5. If section == "args":
           - arg_format == "key_value": append "--key-name" and str(value) to args list
             (key should be converted: underscores to hyphens, e.g., "api_key" → "--api-key")
            - arg_format == "flag": append "--flag-name" if True / omit entirely if False
             (same key conversion)
        6. If section == "env": set env dict key = field key uppercased, value = str(value)
        7. Merge base config with generated args/env, appending to base args
        8. Return merged config

        If any base args/env should always be present, subclasses can override get_base_config().
        """
        base_config = self.get_base_config()
        schema = self.get_config_schema()
        args: list[str] = []
        env: dict[str, str] = {}

        # Copy base env vars
        base_env = base_config.get("env", {})
        if isinstance(base_env, dict):
            env.update(base_env)

        for field in schema:
            key = field["key"]
            # Resolve value: user value takes priority over default
            if key in user_values:
                value = user_values[key]
            elif field.get("default") is not None:
                value = field["default"]
            else:
                continue  # skip fields with no value

            # Skip None and empty string
            if value is None or (isinstance(value, str) and value == ""):
                continue

            section = field.get("section", "args")
            arg_format = field.get("arg_format", "key_value")

            # Convert key for CLI: underscores to hyphens
            cli_key = key.replace("_", "-")

            if section == "env":
                env[key.upper()] = str(value)
            elif section == "args":
                if arg_format == "flag":
                    if value:
                        args.append(f"--{cli_key}")
                    # When False, omit entirely — absence IS the False state
                else:  # key_value
                    args.append(f"--{cli_key}")
                    args.append(str(value))

        # Merge with base config
        result = {**base_config}
        # Append schema args to base args
        base_args = base_config.get("args", [])
        if isinstance(base_args, list):
            result["args"] = base_args + args
        else:
            result["args"] = args
        if env:
            result["env"] = env

        return result

    def parse_config(self, stored_config: dict[str, Any]) -> dict[str, Any]:
        """
        Reverse-map stored MCP config back to user values for form pre-fill.

        Algorithm:
        1. Get base config from get_base_config()
        2. Get schema from get_config_schema()
        3. Get stored args list and env dict from stored_config
        4. Skip base args from stored config (only parse user args)
        5. For each schema field:
           - If section == "env": look up key.upper() in env dict, coerce to field type
           - If section == "args" and arg_format == "flag": check if "--key-name" exists in args → True/False
           - If section == "args" and arg_format == "key_value": find "--key-name" in args, take next element as value, coerce type
        6. Type coercion: "number" → int/float, "boolean" → bool, "text"/"select" → str
        7. Return dict of {key: coerced_value} for all fields found in stored config
        """
        base_config = self.get_base_config()
        base_args = base_config.get("args", [])
        base_args_count = len(base_args) if isinstance(base_args, list) else 0

        schema = self.get_config_schema()
        stored_args = stored_config.get("args", [])
        stored_env = stored_config.get("env", {})
        result: dict[str, Any] = {}

        # Skip base args, only parse user args
        user_args = stored_args[base_args_count:] if base_args_count > 0 else stored_args

        for field in schema:
            key = field["key"]
            field_type = field.get("type", "text")
            section = field.get("section", "args")
            arg_format = field.get("arg_format", "key_value")
            cli_key = key.replace("_", "-")

            if section == "env":
                env_key = key.upper()
                if env_key in stored_env:
                    raw_value = stored_env[env_key]
                    result[key] = self._coerce_value(raw_value, field_type)
            elif section == "args":
                if arg_format == "flag":
                    # Presence of flag means True, absence means False (let defaults apply)
                    if f"--{cli_key}" in user_args:
                        result[key] = True
                else:  # key_value
                    key_str = f"--{cli_key}"
                    try:
                        idx = user_args.index(key_str)
                        if idx + 1 < len(user_args):
                            next_val = user_args[idx + 1]
                            if next_val.startswith("--"):
                                # Next token is a flag, not our value — skip
                                pass
                            else:
                                result[key] = self._coerce_value(next_val, field_type)
                    except ValueError:
                        pass  # key not found in args

        return result

    @staticmethod
    def _coerce_value(value: Any, field_type: str) -> Any:
        """Coerce a value to the expected type based on schema field type."""
        if field_type == "number":
            try:
                float_val = float(value)
                if float_val.is_integer():
                    return int(float_val)
                return float_val
            except (ValueError, TypeError):
                return value
        elif field_type == "boolean":
            if isinstance(value, bool):
                return value
            return str(value).lower() in ("true", "1", "yes")
        else:  # text or select
            return str(value)
