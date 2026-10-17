"""``secrets`` tool category — Phase 1 of agent-secrets (plan f11965039).

Phase 1 ships six tools behind the new ``secrets`` privileged
category (#6 in ``PRIVILEGED_TOOL_CATEGORIES`` — quintet →
sextet per the Q1/Q2/Q3 decisions 2026-10-17). The category
is opt-in-only via the per-agent ``tools.allow`` gate; the
service itself is unconditional (the trust ruling — plan
§1.2 — has no human-in-loop, so a holding-pause path is the
REJECTED failure mode).

Tool surface (plan §5.2 table, authoritative):

* ``secret_generate_ssh_keypair(name: str)`` — mint a fresh
  ed25519 keypair, wrap the private side, store it. Returns
  ``{id, name, public_key, fingerprint, marker}``. Private
  key bits NEVER cross the tool boundary.
* ``secret_generate_api_token(name: str, length: int = 32)`` —
  mint a URL-safe token, wrap, store. Returns
  ``{id, name, token_preview, marker}`` (last-4-char
  preview only).
* ``secret_store(name: str, kind: str, value: str)`` —
  wrap-on-store, open to forward-compatible kinds. Returns
  ``{id, name, marker}``.
* ``secret_list()`` — list active rows. Returns
  ``[{id, name, kind, public_material}, ...]``. No marker,
  no wrapped_secret, no plaintext.
* ``secret_read_public(id: str)`` — read one row. Returns
  ``{id, name, kind, public_material}``. NEVER
  ``wrapped_secret`` (the v1 pubkey-only rule is FINAL —
  plan §5.2; commission explicitly deferred to the plan
  on this).
* ``secret_retire(id: str)`` — soft-retire. Returns
  ``{id, retired_at}``.

Tool names (this module's contract)
------------------------------------

Tool NAMES are flat snake_case (``secret_generate_ssh_keypair``,
``secret_generate_api_token``, ``secret_store``, ``secret_list``,
``secret_read_public``, ``secret_retire``) — the repo convention
established by ``kms_attach`` and ``ens_env_read``. The plan's
``secrets.<tool>`` docstring form is the documentation shape
ONLY; the runtime tool names are the flat form. The agent sees
the tools listed as e.g. ``secret_generate_ssh_keypair`` in its
tool surface.

Category registration
---------------------

This module is the canonical home for the ``secrets`` category.
The four registration steps (P2.2 §3.3 / §8 — mirror the
ens_env_tools pattern):

1. ``@register_tool_category("secrets")`` decorator on every
   tool below (above ``@tool``).
2. ``CATEGORY_MODULES["secrets"] = "daemon.tools.secrets_tools"``
   in ``daemon/tools/_tool_registry.py``.
3. The list-extend inside ``create_instance_tools()`` at
   ``daemon/tools/instance.py`` — see the ens-env comment
   block at :6080-6098.
4. ``PRIVILEGED_TOOL_CATEGORIES`` includes ``"secrets"`` as
   entry #6 (quintet → sextet; same-PR triple-pin updates
   per the A14 / D18 SAME-PR rule).

Plus the ``KNOWN_TOOL_NAMES`` entry for the frozen-binary
fallback (the ``ens_env_read`` precedent — the
``discover_all_tool_names`` + ``KNOWN_TOOL_NAMES`` merge is
the static fallback for binary boots where the source
scanner misses a registration).

Trust model
-----------

The service implements NO per-call access check. The only
access boundary is the per-agent ``tools.allow`` category
gate (the ``secrets`` privileged-category strip + the
day-one allow-list on worker / tester / leader / ari
meta.json). The tools assume any caller reaching the
surface is authorized.

Visibility to non-allow agents
------------------------------

Because ``secrets`` is in ``PRIVILEGED_TOOL_CATEGORIES``,
agents WITHOUT ``secrets`` in their ``tools.allow`` get
ZERO of these tools — both the empty-allow default-deny
strip (``_strip_privileged_category_tools`` at
``daemon/tools/instance.py:6169``) AND the
``resolve_tool_filter`` empty-allow branch
(``daemon/tools/instance.py:373``) catch the absence.
This is the binding structural enforcement for the trust
ruling (plan §5.1 + §13 binding design constraint #2).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Annotated, Any

from langchain_core.tools import tool
from pydantic import Field

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Category doc — surfaces in the help-tool list (pattern parallel
# to the ens_env_tools.CATEGORY_DOC constant).
# ─────────────────────────────────────────────────────────────────────


CATEGORY_NAME = "Agent Secrets"
CATEGORY_DOC = """\
Encrypted secret store — durable Fernet-encrypted registry of
SSH keypairs, API tokens, and arbitrary wrap-on-store values.

- `secret_generate_ssh_keypair` — mint a fresh ed25519 keypair;
  private side wrapped and stored, public side returned.
- `secret_generate_api_token` — mint a URL-safe token; last-4-char
  preview returned.
- `secret_store` — wrap-and-store an arbitrary value under a
  forward-compatible kind.
- `secret_list` — list active rows (no secret material in the
  response).
- `secret_read_public` — read one row's public material (SSH
  public key for keypair rows; null for token / generic).
- `secret_retire` — soft-retire a row.

Visibility: the ``secrets`` category is privileged (entry #6 in
``PRIVILEGED_TOOL_CATEGORIES``). The day-one allow-list is
``worker``, ``tester``, ``leader``, ``ari`` — all other agents
get ZERO of these tools through the standard
``_strip_privileged_category_tools`` default-deny seam.
"""


# ─────────────────────────────────────────────────────────────────────
# Service resolution — the per-instance manager is the wiring
# seam; the tools look the service up at call time so the
# service lifecycle (lazy-init / restart / multi-engine)
# stays in one place (the manager's SecretService field).
# ─────────────────────────────────────────────────────────────────────


def _resolve_service(manager: Any) -> Any:
    """Return the :class:`SecretService` instance from the
    manager.

    The manager owns the service (mirroring
    ``SkillStoreService`` / ``PromptVersionService``). The
    factory tolerates a ``None`` manager for the loader
    warm-list (the existing ``ens_env_tools`` pattern at
    :208-225) — the tool body still works as long as the
    service is reachable.

    Raises:
        RuntimeError: When the manager has no SecretService
            field AND no fallback (``_secret_service``) is
            reachable. The tool boundary catches this and
            returns a structured JSON envelope.
    """
    if manager is None:
        raise RuntimeError(
            "SecretService is not reachable: manager is None"
        )
    svc = getattr(manager, "_secret_service", None)
    if svc is None:
        raise RuntimeError(
            "SecretService is not wired onto the manager "
            "(manager._secret_service is None); the P1 wiring "
            "in daemon/api.py lifespan must run before this "
            "tool is called"
        )
    return svc


def _resolve_actor(manager: Any, current_instance_id: str) -> str:
    """Resolve the actor string for audit attribution.

    Cardinal #2 source discipline: the agent id (NOT the HTTP
    subject, NOT the parent instance id). The canonical
    resolution path is
    ``manager._instance_repository.get(current_instance_id).agent_id``
    (the same path the message-routing layer uses at
    ``daemon/services/instance_messaging.py:2345``). We accept
    a shortcut (``manager._resolve_agent_id_for_actor``) for
    the case where a higher-level wrapper pre-computes the
    agent id; we fall back to the instance id string when
    the lookup fails (degenerate embeddings, test-only call
    sites, manager is None). The audit row carries the
    actor verbatim — never the HTTP subject.

    Args:
        manager: The per-instance InstanceManager handle.
        current_instance_id: The calling instance id
            (audit attribution fallback when the agent id
            resolution fails).
    """
    if manager is None:
        return current_instance_id or "<unknown>"
    # Shortcut — the manager may pre-compute and cache the
    # agent id for the calling instance. Today no wrapper
    # exposes this; the shortcut exists for future
    # optimizations.
    resolver = getattr(manager, "_resolve_agent_id_for_actor", None)
    if callable(resolver):
        try:
            aid = resolver(current_instance_id)
            if aid:
                return aid
        except Exception:
            pass
    # Canonical path — the instance row's agent_id is the
    # Cardinal-#2 source for the in-process caller's
    # identity. The repository lookup is sync; tool bodies
    # run inside asyncio.to_thread, so this is safe.
    repo = getattr(manager, "_instance_repository", None)
    if repo is not None and current_instance_id:
        try:
            inst = repo.get(current_instance_id)
            if inst is not None and getattr(inst, "agent_id", None):
                return inst.agent_id
        except Exception:
            # Never let the audit attribution fail the tool
            # body — the fallback is the instance id, which
            # is still a Cardinal-#2-compliant provenance
            # signal (the instance was the in-process caller).
            pass
    return current_instance_id or "<unknown>"


# ─────────────────────────────────────────────────────────────────────
# Tool factory
# ─────────────────────────────────────────────────────────────────────


def create_secret_tools(
    manager: Any | None = None,
    current_instance_id: str = "",
) -> list:
    """Create the ``secrets`` category tools (P1 surface — 6 tools).

    Factory pattern matches the house convention (see
    ``create_system_tools``, ``create_snapshot_tools``,
    ``create_service_tools``, ``create_ens_env_tools``). The
    ``manager`` argument is accepted for parity with sibling
    factories; the tools dereference ``manager._secret_service``
    at call time so the service lifecycle stays in the manager's
    hands.

    Args:
        manager: Optional per-instance manager handle. Required
            at call time (the tool bodies dereference
            ``manager._secret_service``); a ``None`` stub is
            accepted for the loader warm-list (the existing
            ``ens_env_tools`` pattern at :208-225).
        current_instance_id: The current instance id (audit
            attribution fallback when the agent id resolver
            fails).

    Returns:
        A list containing the 6 P1 tools:
        ``[secret_generate_ssh_keypair,
          secret_generate_api_token, secret_store, secret_list,
          secret_read_public, secret_retire]``.
    """
    caller_instance_id: str = current_instance_id or ""

    # Capture once (defensive regression guard against future
    # closure code that may rebind the param name).
    caller_manager: Any = manager

    @register_tool_category("secrets")
    @tool
    def secret_generate_ssh_keypair(
        name: Annotated[
            str,
            Field(
                description=(
                    "Human-readable label for the keypair. Must "
                    "be UNIQUE within the system; a re-store on "
                    "the same name errors at the service boundary."
                )
            ),
        ],
    ) -> str:
        """Generate a new ed25519 SSH keypair, store the private side encrypted.

Use tool_help("secret_generate_ssh_keypair") for details."""
        try:
            svc = _resolve_service(caller_manager)
            actor = _resolve_actor(caller_manager, caller_instance_id)
            result = svc.generate_ssh_keypair(name=name, actor=actor)
            return json.dumps(result)
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            return json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            )

    @register_tool_category("secrets")
    @tool
    def secret_generate_api_token(
        name: Annotated[
            str,
            Field(
                description=(
                    "Human-readable label for the token. Must be "
                    "UNIQUE within the system."
                )
            ),
        ],
        length: Annotated[
            int,
            Field(
                default=32,
                description=(
                    "Token length in bytes (the URL-safe token "
                    "string will be longer in chars). Default 32 "
                    "(256 bits). Must be positive."
                ),
            ),
        ] = 32,
    ) -> str:
        """Generate a URL-safe API token, store it encrypted. Returns a 4-char preview.

Use tool_help("secret_generate_api_token") for details."""
        try:
            svc = _resolve_service(caller_manager)
            actor = _resolve_actor(caller_manager, caller_instance_id)
            result = svc.generate_api_token(
                name=name, length=length, actor=actor
            )
            return json.dumps(result)
        except Exception as exc:  # noqa: BLE001
            return json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            )

    @register_tool_category("secrets")
    @tool
    def secret_store(
        name: Annotated[
            str,
            Field(
                description=(
                    "Human-readable label. Must be UNIQUE within "
                    "the system."
                )
            ),
        ],
        kind: Annotated[
            str,
            Field(
                description=(
                    "Forward-compatible kind tag — 'ssh_keypair' "
                    "| 'api_token' | 'generic'. New kinds may be "
                    "added later without a migration."
                )
            ),
        ],
        value: Annotated[
            str,
            Field(
                description=(
                    "The plaintext value to wrap-and-store. Held "
                    "only in memory until the Fernet.encrypt call "
                    "returns; the tool response carries only the "
                    "marker (NOT the value, NOT a preview)."
                )
            ),
        ],
    ) -> str:
        """Wrap-and-store an arbitrary value under a forward-compatible kind.

Use tool_help("secret_store") for details."""
        try:
            svc = _resolve_service(caller_manager)
            actor = _resolve_actor(caller_manager, caller_instance_id)
            result = svc.store(
                name=name, kind=kind, value=value, actor=actor
            )
            return json.dumps(result)
        except Exception as exc:  # noqa: BLE001
            return json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            )

    @register_tool_category("secrets")
    @tool
    def secret_list() -> str:
        """List active secrets. No secret material in the response.

Use tool_help("secret_list") for details."""
        try:
            svc = _resolve_service(caller_manager)
            actor = _resolve_actor(caller_manager, caller_instance_id)
            result = svc.list(actor=actor)
            return json.dumps(result)
        except Exception as exc:  # noqa: BLE001
            return json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            )

    @register_tool_category("secrets")
    @tool
    def secret_read_public(
        id: Annotated[
            str,
            Field(
                description=(
                    "The UUID4 id of the secret (returned by the "
                    "create / generate_* tool responses)."
                )
            ),
        ],
    ) -> str:
        """Read one secret's public material. NEVER returns wrapped_secret.

Use tool_help("secret_read_public") for details."""
        try:
            svc = _resolve_service(caller_manager)
            actor = _resolve_actor(caller_manager, caller_instance_id)
            result = svc.read_public(id=id, actor=actor)
            return json.dumps(result)
        except Exception as exc:  # noqa: BLE001
            return json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            )

    @register_tool_category("secrets")
    @tool
    def secret_retire(
        id: Annotated[
            str,
            Field(
                description=(
                    "The UUID4 id of the secret to soft-retire."
                )
            ),
        ],
    ) -> str:
        """Soft-retire a secret. The row stays queryable for audit.

Use tool_help("secret_retire") for details."""
        try:
            svc = _resolve_service(caller_manager)
            actor = _resolve_actor(caller_manager, caller_instance_id)
            result = svc.retire(id=id, actor=actor)
            return json.dumps(result)
        except Exception as exc:  # noqa: BLE001
            return json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            )

    tools_list = [
        secret_generate_ssh_keypair,
        secret_generate_api_token,
        secret_store,
        secret_list,
        secret_read_public,
        secret_retire,
    ]

    # Attach _full_doc_ to each tool (the
    # ``_full_docs[tool_name]`` registration path uses the
    # ``_full_doc_`` attribute — see
    # ``daemon/tools/_tool_registry.py:252-262``). The doc
    # strings carry the public-facing summary; the per-tool
    # ``_full_doc_`` carries the plan-anchored long-form
    # documentation.
    secret_generate_ssh_keypair._full_doc_ = _FULL_DOC_GENERATE_SSH
    secret_generate_api_token._full_doc_ = _FULL_DOC_GENERATE_API
    secret_store._full_doc_ = _FULL_DOC_STORE
    secret_list._full_doc_ = _FULL_DOC_LIST
    secret_read_public._full_doc_ = _FULL_DOC_READ_PUBLIC
    secret_retire._full_doc_ = _FULL_DOC_RETIRE

    return tools_list


# ─────────────────────────────────────────────────────────────────────
# Per-tool full documentation (the help-tool + LangChain tool
# metadata source). Kept as module-level constants so the
# factory closure is one-liner-shaped.
# ─────────────────────────────────────────────────────────────────────


_FULL_DOC_GENERATE_SSH = """\
Generate a fresh ed25519 SSH keypair, store the private side
encrypted (Fernet via SYSTEM_ENCRYPTION_KEY), and return the
public-side material to the caller.

Args:
  name: Human-readable label (UNIQUE within the system).

Returns:
  JSON object: {
    "id":           "<UUID4>",
    "name":         "<label>",
    "public_key":   "ssh-ed25519 AAAA... <comment>",
    "fingerprint":  "SHA256:xxxx",
    "marker":       "__ENS_SECRET__<id>__"
  }

The PRIVATE key bits NEVER cross the tool boundary —
``public_key`` is the OpenSSH ``authorized_keys`` form (the
same shape ``ssh-keygen -y`` prints); ``fingerprint`` is the
SHA256 fingerprint (the same shape ``ssh-keygen -lf`` prints);
``marker`` is the P1 mint-form reference (FROZEN 2026-10-17,
Q3 ruling).

Visibility: the ``secrets`` category is privileged (#6 in
``PRIVILEGED_TOOL_CATEGORIES``). The day-one allow-list is
``worker``, ``tester``, ``leader``, ``ari`` — agents without
``secrets`` in their ``tools.allow`` get ZERO of these tools.

Crypto: ``cryptography>=42.0.0`` (already at
``pyproject.toml:22``). paramiko NOT used. The private side
is the OpenSSH PEM (no password) form, Fernet-wrapped before
storage; the raw 32-byte seed is held only in memory until
the wrap call returns, then dropped on the floor.
"""

_FULL_DOC_GENERATE_API = """\
Generate a URL-safe API token, store it encrypted, and return
a 4-character preview to the caller.

Args:
  name:   Human-readable label (UNIQUE within the system).
  length: Token length in bytes (the URL-safe string is
          longer in chars). Default 32 (256 bits). Must be
          positive.

Returns:
  JSON object: {
    "id":            "<UUID4>",
    "name":          "<label>",
    "token_preview": "<last-4-chars>",
    "marker":        "__ENS_SECRET__<id>__"
  }

The FULL token NEVER crosses the tool boundary — only the
last 4 characters of the URL-safe form are returned in
``token_preview``. The full token is held only in memory
until the Fernet.encrypt call returns, then dropped on the
floor. To use the token later, the agent authors the
``marker`` into a tool parameter / env overlay; the P2
marker-resolver will swap the marker for the decrypted
value at the consumption seam.

Visibility: see ``secret_generate_ssh_keypair``.
"""

_FULL_DOC_STORE = """\
Wrap-and-store an arbitrary value under a forward-compatible
kind.

Args:
  name:  Human-readable label (UNIQUE within the system).
  kind:  Forward-compatible kind tag — ``'ssh_keypair'`` |
         ``'api_token'`` | ``'generic'``. New kinds may be
         added later without a migration.
  value: The plaintext value to wrap-and-store. Held only
         in memory until the Fernet.encrypt call returns;
         the tool response carries only the marker (NOT
         the value, NOT a preview).

Returns:
  JSON object: {
    "id":     "<UUID4>",
    "name":   "<label>",
    "marker": "__ENS_SECRET__<id>__"
  }

The plaintext value NEVER crosses the tool boundary. To
use the stored value later, the agent authors the marker
into a tool parameter / env overlay; the P2 marker-
resolver will swap the marker for the decrypted value
at the consumption seam.

Visibility: see ``secret_generate_ssh_keypair``.
"""

_FULL_DOC_LIST = """\
List active secrets (no secret material in the response).

Args:
  (none)

Returns:
  JSON array of objects: [
    {
      "id":              "<UUID4>",
      "name":            "<label>",
      "kind":            "<kind>",
      "public_material": "<public-side or null>"
    },
    ...
  ]

The list view NEVER includes ``marker``, ``wrapped_secret``,
or any plaintext. The list is a metadata scan only.

Visibility: see ``secret_generate_ssh_keypair``.
"""

_FULL_DOC_READ_PUBLIC = """\
Read one secret's public material.

Args:
  id: The UUID4 id of the secret (returned by the create /
      generate_* tool responses).

Returns:
  JSON object: {
    "id":              "<UUID4>",
    "name":            "<label>",
    "kind":            "<kind>",
    "public_material": "<public-side or null>"
  }

The pubkey-only rule (plan §5.2, FINAL): this tool NEVER
returns ``wrapped_secret``, NEVER returns the decrypted
plaintext, NEVER returns private key bits. There is NO
plaintext-value-returning read tool in v1 — the only
way to use a stored value is via the marker reference,
which the P2 marker-resolver will swap at the consumption
seam (bash ``env`` overlay, MCP spawn, tool params).

Visibility: see ``secret_generate_ssh_keypair``.
"""

_FULL_DOC_RETIRE = """\
Soft-retire a secret. The row stays queryable for audit;
the active-registry partial index skips retired rows.

Args:
  id: The UUID4 id of the secret to soft-retire.

Returns:
  JSON object: {
    "id":          "<UUID4>",
    "retired_at":  "<ISO-8601 stamp>"
  }

The retire action is irreversible through the tool surface
(plan §5.2). The audit row is stamped in the same
transaction as the UPDATE.

Visibility: see ``secret_generate_ssh_keypair``.
"""
