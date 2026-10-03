"""OpenDesign built-in MCP server definition.

Registers the ``opendesign`` stdio MCP server (``open-design-mcp``,
npm package ``open-design-mcp`` v0.16.1, ``mcpName:
io.github.nano-step/open-design-mcp``) so the §7 bootstrap flow of the
designer-agent mission can provision it through
``POST /api/v1/mcp-servers/configure-builtin``.

The MCP server speaks stdio (JSON-RPC over stdin/stdout) and exposes
10 ``od_*`` tools against an OpenDesign daemon. It requires:

- ``OD_DAEMON_URL`` — the OD daemon HTTP endpoint (default
  ``http://127.0.0.1:7456`` per the OD QUICKSTART). Not a secret.
- ``OD_API_TOKEN`` — optional bearer token for non-loopback installs.
  When a token IS provisioned it MUST ride the KMS-Lite seam: the
  stored config carries ONLY a ``__KMS_REF__<handle>__`` marker
  (written by ``kms_attach``); plaintext is substituted in-RAM at MCP
  spawn time by ``daemon.services.kms_resolver``. The schema accepts
  plaintext too (back-compat for non-secret values), but the
  ``install-opendesign`` skill never writes plaintext secrets.

Known limitation on this host (P2-WP4 §6, flagged leftover): the OD
DAEMON itself is NOT installed here (no Docker; official Linux install
path is Docker-only). The MCP server binary starts and lists its 10
``od_*`` tools with a valid ``OD_DAEMON_URL`` — but every actual
design operation fails until the daemon is brought up. This class
registers anyway so the install/resume plumbing is executable and the
daemon can be attached later without code change (WP12 e2e will run
against the ops-lane ``open-design-mcp`` v0.16.1 + OD daemon).

Command resolution (see :func:`_resolve_od_command`): ``McpStdioConfig``
has NO working-dir field, so PATH-relative resolution is unreliable for
the daemon's process env — we prefer, in order: the ``OD_MCP_COMMAND``
env override, the verified absolute install path
(``~/services/opendesign/node_modules/.bin/open-design-mcp``), then
the bare command name.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from daemon.mcp.builtin_servers.base import BuiltinServerDefinition

logger = logging.getLogger(__name__)

#: Verified absolute path of the ``open-design-mcp`` entry binary on
#: this host (ops-lane install root, P2-WP4 §5.1). ``McpStdioConfig``
#: has no working-dir field, so an absolute path is the only reliable
#: form when PATH is not inherited by the daemon process.
OD_MCP_COMMAND_ABSOLUTE = str(
    Path.home() / "services" / "opendesign" / "node_modules" / ".bin" / "open-design-mcp"
)

#: Bare command name — used when the absolute path does not exist and
#: no ``OD_MCP_COMMAND`` override is set (relies on daemon PATH).
OD_MCP_COMMAND_BARE = "open-design-mcp"

#: OD daemon default endpoint (OD QUICKSTART loopback default).
OD_DAEMON_URL_DEFAULT = "http://127.0.0.1:7456"

#: Optional env override pinning the MCP server command. Follows the
#: project's env-override conventions (explicit value wins).
OD_MCP_COMMAND_ENV = "OD_MCP_COMMAND"


def _resolve_od_command() -> str:
    """Resolve the ``open-design-mcp`` command for the base config.

    Precedence: ``OD_MCP_COMMAND`` env override → verified absolute
    install path (when it exists on disk) → bare command name.

    The existence probe runs once at definition construction time
    (module import + registry registration). If the ops-lane install
    root moves, set ``OD_MCP_COMMAND`` rather than editing this module.
    """
    override = os.environ.get(OD_MCP_COMMAND_ENV, "").strip()
    if override:
        return override
    try:
        if Path(OD_MCP_COMMAND_ABSOLUTE).exists():
            return OD_MCP_COMMAND_ABSOLUTE
    except OSError:
        # Unreadable filesystem — fall through to the bare command.
        pass
    return OD_MCP_COMMAND_BARE


class OpenDesignMCP(BuiltinServerDefinition):
    """Built-in MCP server definition for OpenDesign (design canvas).

    Class name is load-bearing: ``capabilities.yaml`` declares
    ``builtin_mcp_class: OpenDesignMCP`` (arch §7.3 registry shape),
    and the strict production registry load resolves that name against
    this class. First user of the §7 bootstrap flow: the
    ``install-opendesign`` skill provisions this server via
    ``configure-builtin``, minting any secret env through KMS-Lite so
    the stored config carries ONLY ``__KMS_REF__`` markers.
    """

    @property
    def name(self) -> str:
        return "opendesign"

    @property
    def display_name(self) -> str:
        return "OpenDesign"

    @property
    def description(self) -> str:
        return (
            "OpenDesign design-canvas tools (10 od_* tools over stdio). "
            "Requires the OD daemon at OD_DAEMON_URL (default "
            "http://127.0.0.1:7456). NOTE: the MCP server starts and "
            "lists tools even when the OD daemon is absent — actual "
            "design operations fail until the daemon is installed "
            "(flagged host leftover; no Docker on this box)."
        )

    @property
    def schema_version(self) -> str:
        """Schema version pinned to the MCP server npm semver (0.16.1).

        Bump when the open-design-mcp package upgrades AND its config
        surface changes — drives schema-drift detection and the
        configure-builtin strictness flip (arch §7.4).
        """
        return "0.16.1"

    @property
    def tool_call_timeout(self) -> int:
        """Per-server tool-call timeout override: 600s (vs 120s global).

        ``od_generate_design`` routinely runs 130–170s against the OD
        daemon — past the pool-wide ``McpPoolConfig.tool_call_timeout``
        default of 120s — so opendesign is the one builtin that opts
        into a longer budget (ODSP saga closure, 2026-10-03). Every
        other builtin inherits the base class ``None`` and keeps the
        global default; the dev-boot ``MCP_POOL_TOOL_CALL_TIMEOUT=600``
        line stays as belt-and-suspenders only.
        """
        return 600

    def get_base_config(self) -> dict[str, Any]:
        """Return base configuration for open-design-mcp (stdio).

        ``args`` is empty — the entry script IS the binary (P2-WP4 §5.1,
        ``bin`` field of the package). The command is resolved once via
        :func:`_resolve_od_command`.
        """
        return {
            "transport": "stdio",
            "command": _resolve_od_command(),
            "args": [],
        }

    def get_config_schema(self) -> list[dict[str, Any]]:
        """Return the configuration schema for the OpenDesign server.

        Two env keys per P2-WP4 §5.1/§5.2:

        - ``od_daemon_url`` → ``OD_DAEMON_URL`` (required by the MCP
          server, but the DEFAULT satisfies it — hence schema-level
          ``required: False`` with the default carrying the value).
        - ``od_api_token`` → ``OD_API_TOKEN`` (optional; loopback
          installs leave it empty). When set with a secret, callers
          MUST pass a ``__KMS_REF__<handle>__`` marker value — never
          plaintext (KMS-Lite seam, arch §7.5).

        Five optional BYOK / timeout keys enabling ``od_generate_design``
        on third-party LLM providers (OD design-workflow integration):

        - ``byok_base_url`` → ``BYOK_BASE_URL`` (optional; provider
          endpoint, e.g. ``https://api.openai.com/v1``).
        - ``byok_api_key`` → ``BYOK_API_KEY`` (optional; secret-bearing,
          mirrors ``od_api_token`` — pass a ``__KMS_REF__<handle>__``
          marker minted via ``kms_request`` + bound via ``kms_attach``,
          NEVER plaintext).
        - ``byok_model`` → ``BYOK_MODEL`` (optional; model identifier
          the OD server should target for generation).
        - ``byok_provider`` → ``BYOK_PROVIDER`` (optional; provider
          hint the OD server uses to dispatch BYOK requests).
        - ``od_generate_timeout_ms`` → ``OD_GENERATE_TIMEOUT_MS``
          (optional; integer millisecond timeout for design
          generation calls — ``type: number`` for int coercion
          round-trip via ``parse_config``).
        """
        return [
            {
                "key": "od_daemon_url",
                "label": "OD Daemon URL",
                "type": "text",
                "section": "env",
                "description": (
                    "OpenDesign daemon HTTP endpoint "
                    f"(default: {OD_DAEMON_URL_DEFAULT})"
                ),
                "default": OD_DAEMON_URL_DEFAULT,
                "required": False,
            },
            {
                "key": "od_api_token",
                "label": "OD API Token",
                "type": "text",
                "section": "env",
                "description": (
                    "Optional bearer token for non-loopback installs. "
                    "Secret-bearing: pass a __KMS_REF__<handle>__ marker "
                    "minted via kms_request + bound via kms_attach — "
                    "NEVER plaintext."
                ),
                "default": None,
                "required": False,
            },
            {
                "key": "byok_base_url",
                "label": "BYOK Base URL",
                "type": "text",
                "section": "env",
                "description": (
                    "Optional base URL for the BYOK LLM provider the "
                    "OD daemon should call for od_generate_design "
                    "(e.g. https://api.openai.com/v1)."
                ),
                "default": None,
                "required": False,
            },
            {
                "key": "byok_api_key",
                "label": "BYOK API Key",
                "type": "text",
                "section": "env",
                "description": (
                    "Optional secret-bearing API key for the BYOK "
                    "provider. Pass a __KMS_REF__<handle>__ marker "
                    "minted via kms_request + bound via kms_attach — "
                    "NEVER plaintext."
                ),
                "default": None,
                "required": False,
            },
            {
                "key": "byok_model",
                "label": "BYOK Model",
                "type": "text",
                "section": "env",
                "description": (
                    "Optional model identifier the OD daemon should "
                    "target for od_generate_design (e.g. "
                    "gpt-4o, claude-sonnet-4-5)."
                ),
                "default": None,
                "required": False,
            },
            {
                "key": "byok_provider",
                "label": "BYOK Provider",
                "type": "text",
                "section": "env",
                "description": (
                    "Optional provider hint the OD daemon uses to "
                    "dispatch BYOK requests (e.g. openai, anthropic)."
                ),
                "default": None,
                "required": False,
            },
            {
                "key": "od_generate_timeout_ms",
                "label": "OD Generate Timeout (ms)",
                "type": "number",
                "section": "env",
                "description": (
                    "Optional per-call timeout in milliseconds for "
                    "od_generate_design. Integer-valued; coerced "
                    "round-trip via parse_config."
                ),
                "default": None,
                "required": False,
            },
        ]


#: Convention alias — sibling builtins use the ``*ServerDefinition``
#: suffix; the registry contract name is ``OpenDesignMCP`` (yaml §7.3).
OpenDesignServerDefinition = OpenDesignMCP
