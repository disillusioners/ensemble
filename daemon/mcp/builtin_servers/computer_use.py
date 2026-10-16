"""Computer Use MCP server definition.

Built-in MCP server that wraps the pinned
``@zavora-ai/computer-use-mcp@7.6.1`` stdio package — a cross-platform
desktop control surface (macOS / Windows / Linux) backed by a Rust
NAPI native module. Tools are exposed with the native ``cu_`` prefix
(rather than ``mcp_computer_use_``) so the agent surface feels
intentional.

Version-pin rationale (project memos 2026-10-16): the v7.x line has
documented wire-format churn across patch versions; an unpinned
``npx -y @zavora-ai/computer-use-mcp`` invocation would auto-pull the
latest 7.x at every fresh cache, silently shifting the tool surface,
the schema, and the per-platform support flags. The pin is the only
honest answer in this environment — a bump requires re-verifying the
tool catalog + re-running the SAME-PR test pin.

Profile selection (default: ``core``): the upstream package exposes a
nested profile system (core ⊂ desktop ⊂ ax ⊂ scripting ⊂
windows-admin ⊂ full; default ``desktop``). We pin ``core`` explicitly:

  * includes all required input primitives (``click``, ``type``,
    ``key``, ``mouse_move``, ``scroll``, plus the v7 aliases
    ``left_click``/``right_click``/``double_click``/etc.),
  * includes ``screenshot``, ``zoom``, ``read_window_text`` (OCR) and
    ``doctor`` (onboarding diagnostics),
  * excludes the destructive scripting / windows-admin tiers
    (``run_script``, ``process_kill``, ``filesystem``, …) — those are
    first-release OUT-OF-SCOPE and escalate to ``desktop``/``full``
    later if needed.

The profile is set via the ``COMPUTER_USE_PROFILE`` env var passed to
the subprocess; ``get_base_config`` injects it into ``config.env`` so
the bootstrap-time copy and any runtime override agree.

Availability / kill-switches
----------------------------
``is_available()`` is a 3-gate AND:

  1. ``COMPUTER_USE_MCP_ENABLED`` — primary kill-switch, default ON.
     Falsy values ``0`` / ``false`` / ``no`` / ``off`` (case-insensitive,
     whitespace-stripped) disable. Mirrors the ``PLANE_MCP_ENABLED``
     semantics so operators use one vocabulary.
  2. ``MCP_DISABLE_BUILT_IN_COMPUTER_USE`` — framework-level generic
     disable (per ``daemon.mcp.builtin_servers.is_builtin_disabled``).
  3. ``npx`` resolvable — env var ``CU_NPX_PATH`` first, then
     ``shutil.which("npx")`` on PATH. When neither resolves, no
     subprocess can ever spawn and registration would silently
     degrade — we refuse up-front instead.

When any gate fails, ``is_available()`` returns ``False`` and the
bootstrap / warmup layers skip the server entirely (no DB record, no
connection attempt). The kill-switch path emits ONE precise log
line per process (mirrors plane.py:292-314) so operators see the
distinguisher between "operator opted out" and "binary missing".

Security & daemon-escape surface
--------------------------------
Computer-use is a FULL local-control surface when the OS grants are
present (Accessibility + Screen Recording on macOS). With those
grants, the ``cu_*`` mutating tools can drive the user's
focused/input-receiving apps in ways that ``bash`` / ``proc`` on
ensemble's process model alone cannot. Therefore:

  * Prefix is ``cu`` — and ``cu`` is added to
    ``PRIVILEGED_TOOL_CATEGORIES`` so empty-allow agents
    (``watcher`` and the other universe-(a) agents) NEVER
    auto-inherit it. An explicit ``tools.allow: ["cu"]`` entry on
    the agent's ``meta.json`` is the only access path (see
    ``agents/ari/meta.json`` + ``agents/leader/meta.json``).
  * ``read_only_tools=False`` (writes are intentional) — CR-3
    (read-only-strip bypass via ``mcp_full_access``) does not apply.
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any

from daemon.mcp.builtin_servers import is_builtin_disabled
from daemon.mcp.builtin_servers.base import BuiltinServerDefinition

logger = logging.getLogger(__name__)


# Pin contract — see module docstring for churn context.
CU_NPM_PACKAGE: str = "@zavora-ai/computer-use-mcp@7.6.1"
CU_NPX_ARGS: tuple[str, ...] = ("-y", CU_NPM_PACKAGE)
CU_DEFAULT_PROFILE: str = "core"
CU_TOOL_NAME_PREFIX: str = "cu"
CU_TOOL_CALL_TIMEOUT_SECONDS: int = 120
CU_SCHEMA_VERSION: str = "1"


def _mcp_kill_switch_enabled() -> bool:
    """Return False when ``COMPUTER_USE_MCP_ENABLED`` opts out.

    Falsy values ``0`` / ``false`` / ``no`` / ``off`` (case-insensitive,
    whitespace-stripped) disable. Default (unset/empty) is ON. Mirrors
    ``PLANE_MCP_ENABLED`` semantics so operators use one vocabulary
    across both kill-switches.
    """
    raw = os.environ.get("COMPUTER_USE_MCP_ENABLED", "").strip().lower()
    if not raw:
        return True
    return raw not in ("0", "false", "no", "off")


def _resolve_cu_npx() -> str | None:
    """Return the absolute path to the ``npx`` binary, or ``None``.

    Resolution order:

      1. ``CU_NPX_PATH`` env var (explicit absolute path, e.g. when
         running under a sandboxed launchd agent that has a fixed
         PATH and no nvm shim in it) — validated via ``shutil.which``
         so a typo'd or non-executable path falls through cleanly
         rather than masquerading as a successful resolution.
      2. ``shutil.which("npx")`` (PATH lookup, including nvm / volta
         shims).

    Returns ``None`` when neither resolves — ``is_available()``
    refuses the server in that case so we never spawn a subprocess
    that will fail with an opaque error. ``shutil.which`` does the
    existence + executable checks so we don't even let a
    non-existent ``CU_NPX_PATH`` slip through.
    """
    explicit = os.environ.get("CU_NPX_PATH", "").strip()
    if explicit:
        # shutil.which on an absolute path returns it as-is when it
        # exists AND is executable; returns None otherwise. This
        # catches the typo / non-executable case that a naive
        # ``os.environ.get`` resolution would miss.
        resolved = shutil.which(explicit)
        if resolved is not None:
            return resolved
    return shutil.which("npx")


# Module-level one-shot flags for the lazy boot-anchor logs.
# Mirrors plane.py:88-89 — the bootstrap layer only consults
# ``is_available()`` once per builtin at startup, so the log must
# be emitted AT MOST once to avoid per-check spam.
_kill_switch_log_emitted: bool = False
_unresolvable_npx_log_emitted: bool = False


def _log_kill_switch_once() -> None:
    global _kill_switch_log_emitted
    if _kill_switch_log_emitted:
        return
    _kill_switch_log_emitted = True
    logger.info(
        "Computer Use MCP server disabled by kill-switch "
        "(COMPUTER_USE_MCP_ENABLED is falsy) — builtin skipped at registration"
    )


def _log_unresolvable_npx_once() -> None:
    global _unresolvable_npx_log_emitted
    if _unresolvable_npx_log_emitted:
        return
    _unresolvable_npx_log_emitted = True
    logger.info(
        "Computer Use MCP server unavailable — npx not found on PATH and "
        "CU_NPX_PATH is unset; set CU_NPX_PATH to an absolute npx path or "
        "install Node.js >= 20 so a PATH lookup resolves"
    )


class ComputerUseServerDefinition(BuiltinServerDefinition):
    """Built-in MCP server definition for Computer Use (desktop control)."""

    @property
    def name(self) -> str:
        return "computer-use"

    @property
    def display_name(self) -> str:
        return "Computer Use"

    @property
    def description(self) -> str:
        return (
            "Cross-platform desktop control via mouse, keyboard, "
            "screenshots, OCR, and accessibility (AX) tree. Tools are "
            "exposed with the native 'cu_' prefix; macOS / Windows / "
            "Linux supported, Pinned to "
            "@zavora-ai/computer-use-mcp@7.6.1 (core profile)."
        )

    @property
    def schema_version(self) -> str:
        return CU_SCHEMA_VERSION

    @property
    def tool_name_prefix(self) -> str | None:
        # Override the default ``mcp_{server}_`` with ``cu_`` —
        # essential built-in whose tools should feel native to ari
        # and leader.
        return CU_TOOL_NAME_PREFIX

    @property
    def read_only_tools(self) -> bool:
        # Writes are intentional — this is a desktop-control surface.
        # CR-3 (read-only-strip bypass via mcp_full_access) does NOT
        # apply because every mutating tool is in scope.
        return False

    @property
    def tool_call_timeout(self) -> int | None:
        # First npx download + OCR/screenshot round-trips are slow.
        return CU_TOOL_CALL_TIMEOUT_SECONDS

    @classmethod
    def is_available(cls) -> bool:
        """3-gate AND: kill-switch + generic disable + npx.

        The bootstrap / warmup layers skip the server entirely on a
        ``False`` return (no DB record, no connection attempt). The
        ``preload_mcp_tools`` loop in ``daemon/services/mcp_service.py``
        ALSO consults this method for each active builtin — a post-boot
        kill-switch flip with an active row from a prior ON boot is
        filtered at preload time before any cold-spawn attempt (cu-mcp
        commission S1, 2026-10-16). The two lazy one-shot logs are
        emitted ONCE per process so an operator scanning the boot
        anchor sees the precise reason (kill-switch opt-out vs.
        missing binary vs. generic MCP disable) instead of the
        generic "skipped" message.
        """
        if not _mcp_kill_switch_enabled():
            _log_kill_switch_once()
            return False
        if is_builtin_disabled("computer-use"):
            return False
        if _resolve_cu_npx() is None:
            _log_unresolvable_npx_once()
            return False
        return True

    def get_base_config(self) -> dict[str, Any]:
        """Return base configuration for the Computer Use stdio MCP.

        The pin lives in this method, NOT in an env var, so a
        registry/config drift cannot silently pull a different
        version. The ``COMPUTER_USE_PROFILE`` env var is injected
        into the subprocess env so the upstream server boots with
        the right profile on every invocation (no surprise profile
        drift if the host shell happened to export a different
        value).
        """
        npx_path = _resolve_cu_npx()
        if npx_path is None:
            # is_available() should have already filtered this; we
            # still defend here because get_base_config can be
            # called from test contexts that bypass availability.
            raise RuntimeError(
                "npx not resolvable; set CU_NPX_PATH or install Node.js >= 20"
            )
        return {
            "transport": "stdio",
            "command": npx_path,
            "args": list(CU_NPX_ARGS),
            "env": {
                "COMPUTER_USE_PROFILE": CU_DEFAULT_PROFILE,
            },
        }

    def get_config_schema(self) -> list[dict[str, Any]]:
        """Computer Use is configured entirely via package pin + env —
        no user-settable fields surfaced in the admin UI (mirrors
        the context7 pattern).
        """
        return []


# ---------------------------------------------------------------------------
# Test-only helpers. NOT part of the runtime API — exposed for unit tests
# that need to reset module-level one-shot flags between cases (mirrors
# ``daemon.mcp.builtin_servers.plane._kill_switch_log_emitted``).
# ---------------------------------------------------------------------------


def _reset_one_shot_log_flags_for_tests() -> None:
    """Reset the module-level kill-switch / npx-missing log flags.

    Test isolation: each test case exercises the lazy log path under
    a fresh ``monkeypatch.setattr`` of the relevant env vars. Without
    resetting these flags, the second-and-later cases would silently
    skip the log because the first case already flipped the flag.
    """
    global _kill_switch_log_emitted, _unresolvable_npx_log_emitted
    _kill_switch_log_emitted = False
    _unresolvable_npx_log_emitted = False
