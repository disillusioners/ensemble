"""OpenSpace built-in MCP server definition.

Provides the openspace.mcp_server module via STDIO with optional HTTP transport
fallback. When ENS_OPENSPACE_REMOTE_URL is set, connects to a remote OpenSpace
MCP endpoint via streamable-http. Otherwise runs the local python module as a
subprocess.

OpenSpace is a self-evolving skill engine that exposes ``execute_task``,
``search_skills``, and ``skill_evolution`` tools.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, ClassVar
from urllib.parse import urlparse

from daemon.mcp.builtin_servers.base import BuiltinServerDefinition
from daemon.mcp.config import McpConfigValidationError

logger = logging.getLogger(__name__)


# Env var checked at config-build time (NOT import time) to allow runtime override
# of the OpenSpace transport. When set to a non-empty value, the server switches
# from local STDIO to remote streamable-http against the given URL.
_OPENSPACE_REMOTE_URL_ENV = "ENS_OPENSPACE_REMOTE_URL"


# Env vars explicitly injected from ``os.environ`` into the OpenSpace subprocess
# via ``build_config()``. Required because MCP stdio_client's
# ``get_default_environment()`` only forwards 6 POSIX vars (HOME, LOGNAME, PATH,
# SHELL, TERM, USER) — the SDK cannot inject arbitrary env at spawn time, so
# ``TaskScopedStdioClient`` needs each value present in the config's ``env``
# dict. ``OPENSPACE_LLM_CONFIG`` is intentionally excluded (deferred for
# separate handling).
_INJECTABLE_VARS = (
    "OPENSPACE_LLM_API_KEY",
    "OPENSPACE_API_KEY",
    "OPENSPACE_LLM_API_BASE",
    "OPENSPACE_LLM_EXTRA_HEADERS",
)


class OpenSpaceServerDefinition(BuiltinServerDefinition):
    """Built-in MCP server definition for OpenSpace."""

    # OpenSpace requires the optional ``openspace-ai`` dependency. When
    # the package is missing, the base class's ``is_available()`` returns
    # ``False`` and the bootstrap / warmup pool layers skip OpenSpace
    # entirely (no DB record, no connection attempt) rather than failing
    # later with a ``ModuleNotFoundError`` in the subprocess.
    required_package: ClassVar[str] = "openspace-ai"

    @property
    def name(self) -> str:
        return "openspace"

    @property
    def display_name(self) -> str:
        return "OpenSpace"

    @property
    def description(self) -> str:
        return (
            "OpenSpace self-evolving skill engine. Provides execute_task for "
            "running embedded OpenSpace agents, search_skills for finding "
            "reusable skills, and skill_evolution for evolving the skill set."
        )

    @property
    def schema_version(self) -> str:
        return "1"

    @property
    def tool_call_timeout(self) -> int | None:
        # OpenSpace execute_task can take up to 15 minutes for long-running
        # agent tasks. Other tools inherit the default.
        return 900

    def get_base_config(self) -> dict[str, Any]:
        """Return base configuration for openspace.mcp_server.

        Defaults to STDIO transport launching the module wrapped in
        ``daemon.mcp.safe_stdout`` so that any stray ``print()`` calls
        inside the OpenSpace server cannot corrupt the JSON-RPC stream.
        The effective command becomes
        ``python3 -m daemon.mcp.safe_stdout openspace.mcp_server``.

        ``build_config()`` may override this to streamable-http when
        ``ENS_OPENSPACE_REMOTE_URL`` is set.
        """
        return {
            "transport": "stdio",
            "command": sys.executable,  # daemon's Python, has `daemon` installed
            # Wrap the target module with daemon.mcp.safe_stdout: text
            # writes (print(), etc.) inside the OpenSpace server code
            # are redirected to stderr so the stdio transport's JSON-RPC
            # frames on sys.stdout.buffer stay intact. Opt-in per
            # server — webfetch/context7 use external CLIs (uvx/npx)
            # and are deliberately NOT wrapped.
            "args": ["-m", "daemon.mcp.safe_stdout", "openspace.mcp_server"],
        }

    def get_config_schema(self) -> list[dict[str, Any]]:
        """Return the configuration schema for OpenSpace server.

        All three fields live in the ``env`` section so the base class
        uppercases the key into the env dict (e.g. ``openspace_model``
        → ``OPENSPACE_MODEL``).

        Defaults are intentionally empty strings (falsy) — the base
        ``build_config()`` skips them when user_values is empty, letting
        OpenSpace fall back to its own internal defaults instead of us
        hardcoding model names here.
        """
        return [
            {
                "key": "openspace_model",
                "label": "LLM Model",
                "type": "text",
                "section": "env",
                "arg_format": "key_value",
                "description": (
                    "LLM model identifier for OpenSpace agents (e.g. "
                    "'gpt-4o', 'claude-3-5-sonnet'). Empty = OpenSpace default."
                ),
                "default": "",
                "required": False,
            },
            {
                "key": "openspace_max_iterations",
                "label": "Max Iterations",
                "type": "number",
                "section": "env",
                "arg_format": "key_value",
                "description": (
                    "Maximum iterations per OpenSpace execute_task call. "
                    "Empty = OpenSpace default."
                ),
                "default": "",
                "required": False,
            },
            {
                "key": "openspace_backend_scope",
                "label": "Backend Scope",
                "type": "text",
                "section": "env",
                "arg_format": "key_value",
                "description": (
                    "Comma-separated backend scope filter (e.g. "
                    "'cloud,local'). Empty = all backends."
                ),
                "default": "",
                "required": False,
            },
        ]

    def parse_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Reverse-map a stored OpenSpace config back to form values.

        Delegates to ``super().parse_config()`` unchanged. The override
        exists solely to document a deliberate behavior: injected env
        vars from ``_INJECTABLE_VARS`` (credentials, base URL, headers)
        and the transport pin (``OPENSPACE_MCP_TRANSPORT=stdio``) are
        written to ``env`` by ``build_config()`` but are NOT schema
        fields, so they are NOT recovered by ``parse_config()`` on
        round-trip. This is intentional: those values come from the
        runtime environment, not from user form input.
        """
        return super().parse_config(config)

    def build_config(self, user_values: dict[str, Any]) -> dict[str, Any]:
        """Build config with dual-transport support.

        Behavior:
        1. Check ``ENS_OPENSPACE_REMOTE_URL``. If non-empty after strip,
           validate the URL scheme is ``http://`` or ``https://`` (else
           raise ``McpConfigValidationError``) and that it does not embed
           userinfo credentials (``user:pass@host``), then return a remote
           HTTP config (no STDIO subprocess). If credentials env vars are
           set in this mode, log a per-var warning that they are ignored —
           they should be configured on the remote OpenSpace instance
           instead.
        2. Otherwise call ``super().build_config(user_values)`` for STDIO,
           then layer on OpenSpace-specific environment:
           - ``OPENSPACE_MCP_TRANSPORT=stdio``: prevents OpenSpace's TTY
             auto-detection from picking SSE in subprocess context.
           - Env var injection (see ``_INJECTABLE_VARS``): the MCP SDK's
             ``stdio_client`` calls ``get_default_environment()`` which
             only forwards 6 POSIX vars (HOME, LOGNAME, PATH, SHELL,
             TERM, USER) — NOT full ``os.environ``. We must explicitly
             inject credentials, base URL, and extra headers.
           - ``OPENSPACE_LLM_API_BASE`` is validated for embedded
             userinfo (``user:pass@host``) before injection.

        Args:
            user_values: User-provided config values from the schema fields.

        Returns:
            Merged config dict ready for ``McpStdioConfig`` or HTTP transport.

        Raises:
            McpConfigValidationError: If ``ENS_OPENSPACE_REMOTE_URL`` is set
                but does not use the ``http://`` or ``https://`` scheme, or
                if it embeds userinfo credentials (``user:pass@host``).
                ``McpConfigValidationError`` is a ``ValueError`` subclass so
                callers that catch ``ValueError`` still work, but the router
                translates it into a 422 response instead of a 500.
        """
        remote_url = os.environ.get(_OPENSPACE_REMOTE_URL_ENV, "").strip()
        if remote_url:
            # Reject non-HTTP schemes explicitly (e.g. ftp://, file://, ws://)
            # — they are not valid transport URLs for streamable-http.
            if not (remote_url.startswith("http://") or remote_url.startswith("https://")):
                raise McpConfigValidationError(
                    "ENS_OPENSPACE_REMOTE_URL must use http:// or https:// scheme"
                )

            # Reject embedded credentials in the URL. The remote URL is
            # stored in the DB and surfaces in API responses (after
            # redact_secrets, which strips env values but does not see
            # URLs), so any userinfo here would be exposed to API
            # clients. Configure auth on the remote OpenSpace instance
            # instead (e.g. via its own Authorization headers).
            parsed_remote = urlparse(remote_url)
            if "@" in parsed_remote.netloc:
                raise McpConfigValidationError(
                    "ENS_OPENSPACE_REMOTE_URL must not contain userinfo "
                    "credentials (user:pass@host)"
                )

            # Warn if OpenSpace env vars are set in HTTP mode — they are
            # intentionally ignored because the local subprocess isn't
            # started in this mode. Configure them on the remote OpenSpace
            # instance instead.
            for env_var in _INJECTABLE_VARS:
                if os.environ.get(env_var, "").strip():
                    logger.warning(
                        "OpenSpace: %s is set but ignored in HTTP mode "
                        "(ENS_OPENSPACE_REMOTE_URL); configure it on the "
                        "remote OpenSpace instance instead",
                        env_var,
                    )

            logger.info(
                "OpenSpace: ENS_OPENSPACE_REMOTE_URL is set, using streamable-http transport"
            )
            return {
                "transport": "streamable-http",
                "url": remote_url,
                "headers": {},
            }

        # STDIO path: delegate to base for schema-driven config, then
        # inject OpenSpace-specific env vars.
        config = super().build_config(user_values)

        # Ensure env dict exists
        config.setdefault("env", {})
        if not isinstance(config["env"], dict):
            config["env"] = {}

        # Pin OpenSpace to STDIO transport (subprocess TTY auto-detect
        # would otherwise pick SSE).
        config["env"]["OPENSPACE_MCP_TRANSPORT"] = "stdio"

        # Validate OPENSPACE_LLM_API_BASE before injection — reject embedded
        # userinfo credentials (user:pass@host) so they never reach the
        # subprocess env. Same rationale as the ENS_OPENSPACE_REMOTE_URL
        # check above: db-stored config and API responses surface this
        # value, so credentials must not be embedded in the URL.
        llm_api_base = os.environ.get("OPENSPACE_LLM_API_BASE", "").strip()
        if llm_api_base:
            parsed = urlparse(llm_api_base)
            if "@" in parsed.netloc:
                raise McpConfigValidationError(
                    "OPENSPACE_LLM_API_BASE must not contain userinfo "
                    "credentials (user:pass@host)"
                )

        # Inject OpenSpace env vars from os.environ. The MCP stdio_client
        # uses get_default_environment() which only forwards 6 POSIX vars
        # — without explicit injection, the OpenSpace subprocess loses
        # these settings even when the daemon process has them set.
        for env_var in _INJECTABLE_VARS:
            value = os.environ.get(env_var, "").strip()
            if value:
                config["env"][env_var] = value

        return config
