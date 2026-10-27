"""Context7 built-in MCP server definition.

Provides the @upstash/context7-mcp package via npx for fetching
up-to-date library documentation.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from daemon.mcp.builtin_servers.base import BuiltinServerDefinition
from daemon.mcp.config import McpConfigValidationError


#: The daemon-side MIRROR of the package's hardcoded site root.
#: ``@upstash/context7-mcp`` v4.4.4 reads
#: ``const CONTEXT7_BASE_URL = "https://context7.com"`` as a HARDCODED
#: constant (``dist/lib/constants.js:7``) — it does NOT read the
#: environment variable. Only ``CONTEXT7_API_URL`` is an effective lever
#: (``dist/lib/constants.js:10``:
#: ``process.env.CONTEXT7_API_URL || `${CONTEXT7_BASE_URL}/api``).
#: The mirror exists so the daemon has a single, settable statement of
#: the site root — and so the C2 DNS pre-check has a derivation input.
CONTEXT7_DEFAULT_BASE_URL = "https://context7.com"


class Context7ServerDefinition(BuiltinServerDefinition):
    """Built-in MCP server definition for Context7."""

    @property
    def name(self) -> str:
        return "context7"

    @property
    def display_name(self) -> str:
        return "Context7"

    @property
    def description(self) -> str:
        return "Provides up-to-date library documentation for AI coding assistants. Fetches official docs, API references, and examples for libraries."

    @property
    def schema_version(self) -> str:
        # G5: the schema changed (two env fields were added) ⇒ the
        # version MUST be bumped. The consequence is disclosed: a live
        # row stamped "2" is refused by ``/configure-builtin`` (409)
        # until the next boot heals it, and the heal is
        # ENV-PRESERVING (the operator's env survives it).
        return "3"

    def get_base_config(self) -> dict[str, Any]:
        """Return base configuration for @upstash/context7-mcp."""
        return {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@upstash/context7-mcp"],
        }

    def get_config_schema(self) -> list[dict[str, Any]]:
        """Return the configuration schema for Context7 server.

        TWO env-section fields (G1) — they are NOT interchangeable:

        * ``CONTEXT7_BASE_URL`` — the **daemon-side MIRROR** of the site
          root. It is **INERT to the package**: ``@upstash/context7-mcp``
          v4.4.4 hardcodes ``CONTEXT7_BASE_URL`` as a constant and does
          not read it from the environment. Its ONLY effect is the C2
          DNS pre-check's derivation input (the host the daemon resolves
          when ``CONTEXT7_API_URL`` is unset).
        * ``CONTEXT7_API_URL`` — the **EFFECTIVE LEVER**: the only
          variable the package actually reads
          (``process.env.CONTEXT7_API_URL``). It deliberately has **NO
          default** — pinning the API path at template time is the thing
          that broke, and an unset value keeps the package's own
          derivation live.
        """
        return [
            {
                "key": "CONTEXT7_BASE_URL",
                "label": "Context7 Base URL (mirror)",
                "type": "text",
                "section": "env",
                "description": (
                    "Daemon-side mirror of the Context7 site root. INERT to the "
                    "@upstash/context7-mcp package (v4.4.4 hardcodes this value "
                    "as a constant and does not read it from the environment) — "
                    "only CONTEXT7_API_URL is an effective lever. This value is "
                    "used as the daemon's DNS pre-check derivation input (the "
                    "host the daemon resolves when CONTEXT7_API_URL is unset)."
                ),
                "default": CONTEXT7_DEFAULT_BASE_URL,
                "required": False,
            },
            {
                "key": "CONTEXT7_API_URL",
                "label": "Context7 API URL",
                "type": "text",
                "section": "env",
                "description": (
                    "The EFFECTIVE lever: the API base the @upstash/context7-mcp "
                    "package actually reads (process.env.CONTEXT7_API_URL). "
                    "Leave unset to keep the package's own derivation live "
                    "(its hardcoded base + /api). No default on purpose — a "
                    "pinned default would freeze the exact thing that broke."
                ),
                "default": None,
                "required": False,
            },
        ]

    def build_config(self, user_values: dict[str, Any]) -> dict[str, Any]:
        """Build the config, validating the URL-shaped values (G4).

        Rejects a non-``http(s)`` scheme and embedded userinfo.

        The raised error is ``McpConfigValidationError`` — a
        ``ValueError`` SUBCLASS (so the webfetch ``ValueError``
        precedent holds and a plain ``except ValueError`` still
        catches it) that the ``/configure-builtin`` route translates
        into a clean **422** instead of an opaque 500.
        """
        for key in ("CONTEXT7_BASE_URL", "CONTEXT7_API_URL"):
            value = user_values.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                raise McpConfigValidationError(f"{key} must be a string")
            text = value.strip()
            if not text:
                # An empty string is "unset" — ``build_config`` skips
                # empty values anyway; nothing to validate.
                continue
            _validate_base_url(key, text)
        return super().build_config(user_values)

    def get_precheck_url(self, env: dict[str, str] | None = None) -> str | None:
        """The URL whose HOST the C2 DNS pre-check resolves.

        The **effective post-heal** base the child will actually use:
        the row's ``CONTEXT7_API_URL`` wins (the effective lever); when
        it is unset the ``CONTEXT7_BASE_URL`` mirror is the derivation
        input. The package appends ``/api`` to its hardcoded root — that
        does not change the resolved HOST, so the DNS verdict is
        identical.

        ``env`` is the EFFECTIVE merged env (the row's env overlaid on
        the definition defaults — the precedence ``build_pooled_stdio_config``
        pins: the row wins on every key).
        """
        values = env or {}
        api = (values.get("CONTEXT7_API_URL") or "").strip()
        if api:
            return api
        mirror = (values.get("CONTEXT7_BASE_URL") or "").strip()
        return mirror or None


def _validate_base_url(key: str, value: str) -> None:
    """Reject a non-http(s) scheme and embedded userinfo (G4)."""
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise McpConfigValidationError(f"{key} is not a valid URL: {exc}") from exc
    if parts.scheme not in ("http", "https"):
        raise McpConfigValidationError(
            f"{key} must start with http:// or https:// "
            f"(got '{value}')"
        )
    if parts.username or parts.password or "@" in parts.netloc:
        raise McpConfigValidationError(
            f"{key} must not embed credentials (userinfo) in the URL"
        )
    if not parts.hostname:
        raise McpConfigValidationError(f"{key} must contain a host")
