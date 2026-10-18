"""``settings`` tool category — Phase 2 of settings-worker-slots.

Phase 2 ships three tools behind the new ``settings`` privileged
category (#8 in ``PRIVILEGED_TOOL_CATEGORIES`` — septet → octet;
``secrets`` joined 2026-10-17 making the count 7 before this
entry). The category is opt-in-only via the per-agent
``tools.allow`` gate; the ari + leader meta.json allow entries
landed 2026-10-18 (P3).

Tool surface (phase2-plan.md task 1, authoritative):

* ``settings_get(key: str | None = None)`` — resolved view of all
  (or one) managed keys with per-key
  ``{key, value (boot-applied), source, default, env_value?,
  yaml_value?, db_override?, requires_restart, sensitive,
  divergent}`` where ``divergent = (value != resolved-tier value)``
  — the honest-restart core. Tier detail fields are ``null`` for
  sensitive keys (the router's security addendum parity).
* ``settings_set(key, value)`` — ``SettingsService.set_override``
  (the ONE audited write path). Response is the HONEST shape:
  ``{key, written, updated_by, requires_restart,
  applies_at: "next daemon restart", source, running_value_still,
  divergent, live: false, note}``. NEVER claims liveness.
* ``settings_clear(key)`` — ``SettingsService.clear_override``
  (idempotent — ``cleared: false`` when no row existed), same
  honest shape. Exists because set-to-default leaves a db-tier
  row; only clear restores env/yaml/default tier semantics.

Honest never-live contract (D5, binding, verbatim)
---------------------------------------------------

1. every result reports per-key value/source/requires_restart/
   divergent — never a bare value;
2. every write/clear states ``applies_at: "next daemon restart"``
   and ``live: false`` — NEVER claim liveness;
3. when ``divergent=true``, the response says the running value
   remains ``running_value_still`` while the ``source`` tier
   resolves to the written value — pending restart.

Every current managed key carries ``requires_restart=True``; the
``note`` field states the restart requirement explicitly.

Service resolution — envelope seam (council B3, option (b))
------------------------------------------------------------

``_resolve_service()`` reads the router-owned DI global
``daemon/routers/settings.py`` ``_settings_service`` DIRECTLY
(module attribute, read at call time) and raises its OWN
``RuntimeError`` when the service is unwired — mirroring the
``secrets_tools.py`` idiom. It deliberately does NOT call
``get_settings_service()``, which raises ``HTTPException(503)``
and would yield the WRONG envelope ``{"error": "HTTPException"}``
instead of the pinned ``{"error": "RuntimeError"}``.

Import direction (council F6, binding):

* the resolution SNAPSHOT is read via the SSOT module-global in
  ``daemon/services/settings_service.py`` (tools → SERVICES — the
  clean direction);
* the SERVICE global exists ONLY in the router module (the
  db-backed-settings commission deliberately placed DI there) —
  the tools→routers attribute read is an ACCEPTED COUPLING; a
  duplicate services-layer global would create a second DI truth
  for one service. ``SettingsService`` intentionally does NOT
  live on the manager; no manager field is added, and the tool
  never constructs ``SettingsService(engine=manager.engine)``
  (that would bypass the wired instance).

The entry shape mirrors the router's ``_build_daemon_entries``
semantics EXACTLY (value = boot-applied ``applied_value`` from
the pristine snapshot; source = live re-resolve via the ONE
shared ``resolve()``; divergence between the two is the honest
restart-required reporting) — zero parallel ladder logic: the
ladder is ``settings_service.resolve``, the SSOT.

Failure idiom (the secrets PB-F1 playbook)
-------------------------------------------

Two tiers, same boundary:

* typed rejections (``SettingsValidationError`` /
  ``UnknownManagedKeyError`` — authored, agent-actionable strings)
  surface as a plain ``"ERROR: <detail>"`` text result;
* everything else (unwired-service ``RuntimeError``, SQLAlchemy,
  OS errors) is a class-name-only JSON envelope — ``str(exc)``,
  ``traceback``, and ``repr(exc)`` NEVER cross the boundary
  (CWE-209; the PB-F1 leak class is proven real in this project —
  2026-10-03 occ-2 incident).

Category registration (the four touch-points + construction)
-------------------------------------------------------------

1. ``DYNAMIC_TOOL_NAMES`` += the three names (``_tool_registry.py``).
2. ``PRIVILEGED_TOOL_CATEGORIES`` += ``"settings"`` (entry #8) —
   privileged because the tools mutate daemon-global runtime state
   and read tier values; the empty-allow inherit universe must NOT
   auto-grant them. Structural enforcement is the standard pair:
   ``_strip_privileged_category_tools`` (``daemon/tools/instance.py``)
   and the ``resolve_tool_filter`` empty-allow branch.
3. ``CATEGORY_MODULES["settings"] = "daemon.tools.settings_tools"``.
4. ``KNOWN_TOOL_NAMES`` (frozen-binary fallback) += the three names
   — BOTH name-sets must carry them or
   ``test_frozen_tool_name_discovery`` fails.

Plus the construction extend in ``create_instance_tools`` —
decorator-only registration is SILENTLY INVISIBLE.

Audit (task 2, O2 parity): one INFO line per tool write/clear in
the SAME ``[Settings]`` shape as the router-side audit lines
(``daemon/routers/settings.py`` PUT/DELETE handlers), with the
``[tool]`` lane marker and agent/instance attribution — value is
rendered ``<redacted>`` when the key's spec is ``sensitive``.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from langchain_core.tools import tool
from pydantic import Field

from daemon.services.settings_service import (
    MANAGED_KEYS,
    SettingsValidationError,
    UnknownManagedKeyError,
    get_settings_resolution_snapshot as _svc_get_snapshot,
    resolve,
)
from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)


# The applies_at / live literals are the FROZEN honest-contract
# vocabulary (D5) — every write/clear response carries them
# byte-identical. Never reword at call sites.
APPLIES_AT = "next daemon restart"


# ─────────────────────────────────────────────────────────────────────
# Error envelope (the secrets PB-F1 playbook, settings-flavored)
# ─────────────────────────────────────────────────────────────────────


def _typed_error_text(exc: BaseException) -> str:
    """Extract the authored detail from a TYPED rejection.

    ``SettingsValidationError`` messages are authored
    (key + failure class + constraint) — safe and
    agent-actionable. ``UnknownManagedKeyError`` is a
    ``KeyError`` subclass whose ``str()`` renders as ``'key'``;
    unwrap ``args[0]`` so the rejection reads as the bare key.
    """
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return str(exc)


def _format_error_envelope(exc: BaseException) -> str:
    """Build the class-name-only JSON envelope for UNEXPECTED
    exceptions (the secrets idiom — ``secrets_tools.py``
    ``_format_error_envelope``).

    NEVER carries ``str(exc)`` / ``repr(exc)`` / traceback into
    the payload (CWE-209). Returns a JSON-serialized dict so the
    tool-boundary shape stays machine-readable.
    """
    try:
        logger.warning(
            "[Settings][tool] boundary caught unexpected exception: %s",
            type(exc).__name__,
        )
    except Exception:  # noqa: BLE001 — logging must never break the envelope
        pass
    return json.dumps({"error": type(exc).__name__})


# ─────────────────────────────────────────────────────────────────────
# Category doc — surfaces in the help-tool list (the
# secrets_tools.CATEGORY_DOC pattern).
# ─────────────────────────────────────────────────────────────────────


CATEGORY_NAME = "Daemon Settings"
CATEGORY_DOC = """\
DB-managed daemon runtime settings — read and override the
managed-key registry (``limits.llm_concurrency``,
``limits.worker_pool_size``, ``limits.chat_worker_pool_size``)
backed by the ``app_settings`` table.

- `settings_get` — resolved view of all (or one) managed keys with
  per-key tier reporting (value/source/default/env/yaml/db +
  requires_restart + divergent).
- `settings_set` — write a DB override through the audited
  service path. HONEST contract: never live — every response
  states `applies_at: "next daemon restart"`, `live: false`, and
  the `running_value_still` divergence when the running value
  differs.
- `settings_clear` — delete a DB override (idempotent), falling
  back down the ladder (env > yaml > default) at the next boot.

Visibility: the ``settings`` category is privileged (entry #8 in
``PRIVILEGED_TOOL_CATEGORIES``; ``secrets`` joined 2026-10-17
before this entry) — the tools mutate daemon-global runtime
state. NEVER default-granted: an agent reaches them ONLY through
an explicit ``tools.allow`` entry (ari + leader allow entries
landed 2026-10-18, P3). All other agents get ZERO of these
tools through the standard ``_strip_privileged_category_tools``
default-deny seam.
"""


# ─────────────────────────────────────────────────────────────────────
# Service resolution — envelope seam per council B3 option (b):
# read the router-owned DI global DIRECTLY (module attribute at
# call time), raise OUR OWN RuntimeError when unwired. NOT the
# HTTPException(503) getter.
# ─────────────────────────────────────────────────────────────────────


def _resolve_service(manager: Any = None) -> Any:
    """Return the wired :class:`SettingsService`.

    The service is intentionally NOT a manager field — the
    db-backed-settings commission wired it as the router module's
    ``_settings_service`` DI global (api lifespan), so that module
    attribute IS the only DI truth. Read at CALL time (a
    module-level ``from ... import _settings_service`` would
    bind ``None`` forever); the ``manager`` argument is accepted
    for factory parity with the sibling tool modules and ignored.

    Raises:
        RuntimeError: When the lifespan hasn't wired the service
            yet. The tool boundary converts this into the pinned
            ``{"error": "RuntimeError"}`` JSON envelope. Deliberately
            NOT routed through ``get_settings_service()`` — that
            getter raises ``HTTPException(503)`` whose class name
            would leak router taxonomy into the tool contract.
    """
    # Local import: keeps the router module out of the tool-registry
    # import graph and guarantees the attribute read is fresh.
    from daemon.routers import settings as _settings_router

    svc = getattr(_settings_router, "_settings_service", None)
    if svc is None:
        raise RuntimeError(
            "SettingsService is not wired (daemon/routers/settings.py "
            "_settings_service is None); the api lifespan "
            "(set_settings_service) must run before settings tools "
            "are usable"
        )
    return svc


def _resolve_actor(manager: Any, current_instance_id: str) -> str:
    """Resolve the actor string for audit attribution.

    Cardinal #2 source discipline (mirrors the secrets
    ``_resolve_actor`` pattern — keep in sync with
    ``daemon/tools/secrets_tools.py::_resolve_actor``): the agent
    id (NOT the HTTP subject, NOT the parent instance id). The
    canonical resolution path is
    ``manager._instance_repository.get(current_instance_id)
    .agent_id``, the same provenance the message-routing layer
    uses. We FIRST probe a ``manager._resolve_agent_id_for_actor``
    shortcut (DORMANT BY DESIGN — no manager defines it today; the
    probe is retained for parity with the secrets implementation
    so a future pre-computed-identity wrapper is picked up for
    free). Falls back to the instance id string when the lookup
    fails (test-only call sites, manager is None); attribution
    never fails the tool body.
    """
    if manager is None:
        return current_instance_id or "<unknown>"
    # Dormant by design: the manager may pre-compute and cache the
    # agent id for the calling instance. No manager defines this
    # today (grep ``_resolve_agent_id_for_actor`` finds no
    # definition); the probe exists for parity with
    # ``daemon/tools/secrets_tools.py::_resolve_actor`` and for
    # future optimizations. Keep in sync with that function.
    resolver = getattr(manager, "_resolve_agent_id_for_actor", None)
    if callable(resolver):
        try:
            aid = resolver(current_instance_id)
            if aid:
                return aid
        except Exception:  # noqa: BLE001 — attribution is best-effort
            pass
    repo = getattr(manager, "_instance_repository", None)
    if repo is not None and current_instance_id:
        try:
            inst = repo.get(current_instance_id)
            if inst is not None and getattr(inst, "agent_id", None):
                return inst.agent_id
        except Exception:  # noqa: BLE001 — attribution is best-effort
            pass
    return current_instance_id or "<unknown>"


# ─────────────────────────────────────────────────────────────────────
# Entry shape — mirrors the router's _build_daemon_entries semantics
# EXACTLY, sourced from the services-module SSOT snapshot. The LADDER
# itself is settings_service.resolve — the ONE shared resolver; no
# parallel tier logic lives here.
# ─────────────────────────────────────────────────────────────────────


def _registry_gate_rejection(key: str) -> str:
    """The registry-gate rejection string for unregistered keys."""
    return (
        f"ERROR: unknown managed setting key: {key!r} — the "
        f"managed-key registry (MANAGED_KEYS in "
        f"daemon/services/settings_service.py) is the write AND "
        f"read gate; unregistered keys are refused on every path"
    )


def _entry_for_key(
    key: str,
    overrides: dict[str, Any],
    snapshot: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    """Build one honest per-key entry (router-parity semantics).

    ``value`` = the boot-applied ``applied_value`` carried by the
    pristine snapshot (the RUNNING truth); ``source`` = live
    re-resolve over (db > env > yaml > default). Divergence
    between the two is the honest restart-required reporting.
    Sensitive keys null the tier detail fields (the router's
    security addendum parity).
    """
    spec = MANAGED_KEYS[key]
    resolution = resolve(key, snapshot, db_overrides=overrides)
    applied = (snapshot or {}).get(key, {}).get("applied_value")
    if applied is None:
        # Defensive: a hand-built snapshot without applied_value
        # (test harness / pre-snapshot boot) falls back to the
        # live resolved value — same fallback the router uses.
        applied = resolution["value"]
    if spec.sensitive:
        env_value = yaml_value = db_override = None
    else:
        env_value = resolution["env_value"]
        yaml_value = resolution["yaml_value"]
        db_override = resolution["db_override"]
    return {
        "key": key,
        "value": applied,
        "source": resolution["source"],
        "default": resolution["default"],
        "env_value": env_value,
        "yaml_value": yaml_value,
        "db_override": db_override,
        "requires_restart": spec.requires_restart,
        "sensitive": spec.sensitive,
        "divergent": applied != resolution["value"],
    }


def _honest_write_response(
    key: str,
    spec: Any,
    actor: str,
    written: Any,
    overrides: dict[str, Any],
    snapshot: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    """Assemble the D5 honest write/clear response.

    Binding shape: ``applies_at`` + ``live: false`` ALWAYS;
    ``running_value_still`` = the boot-applied value still running;
    ``divergent`` per the post-write resolution; ``note`` states
    the restart requirement explicitly (every current key carries
    ``requires_restart=True``).
    """
    entry = _entry_for_key(key, overrides, snapshot)
    # All current ManagedKeySpecs are requires_restart=True; the
    # note is the restart-required shape for every key. Revisit if
    # a False key is ever added (it would need a non-restart note).
    note = (
        f"NOT live — requires_restart: the running daemon keeps "
        f"{entry['value']!r}; the change applies at the next "
        f"daemon restart"
    )
    return {
        "key": key,
        "written": written,
        "updated_by": actor,
        "requires_restart": spec.requires_restart,
        "source": entry["source"],
        "applies_at": APPLIES_AT,
        "running_value_still": entry["value"],
        "divergent": entry["divergent"],
        "live": False,
        "note": note,
    }


# ─────────────────────────────────────────────────────────────────────
# Tool factory
# ─────────────────────────────────────────────────────────────────────


def create_settings_tools(
    manager: Any | None = None,
    current_instance_id: str = "",
) -> list:
    """Create the ``settings`` category tools (P2 surface — 3 tools).

    Factory pattern matches the house convention (``create_secret_tools``,
    ``create_ens_env_tools``, ...). The tools dereference the
    router-owned ``_settings_service`` DI global at call time, so the
    service lifecycle stays with the api lifespan; the factory is safe
    with a None-stub manager for the loader warm-list.

    Args:
        manager: Optional per-instance manager handle (actor
            attribution only — the service is NOT a manager field).
        current_instance_id: The current instance id (audit
            attribution fallback).

    Returns:
        A list containing the 3 tools:
        ``[settings_get, settings_set, settings_clear]``.
    """
    caller_instance_id: str = current_instance_id or ""
    caller_manager: Any = manager

    @register_tool_category("settings")
    @tool
    def settings_get(
        key: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Optional managed key to read (e.g. "
                    "'limits.worker_pool_size'). Omit for ALL "
                    "registered keys. Unknown keys are refused by "
                    "the registry gate."
                ),
            ),
        ] = None,
    ) -> str:
        """Read managed daemon settings with per-key tier reporting.

Use tool_help("settings_get") for details."""
        try:
            svc = _resolve_service(caller_manager)
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            return _format_error_envelope(exc)
        try:
            overrides = svc.get_overrides()
            # F6: the SNAPSHOT comes from the services-module SSOT
            # global (None → default-tier semantics inside resolve()).
            snapshot = _svc_get_snapshot()
            if key is None:
                entries = [
                    _entry_for_key(k, overrides, snapshot)
                    for k in MANAGED_KEYS
                ]
                restart_required = any(
                    e["source"] == "db" for e in entries
                )
                return json.dumps(
                    {
                        "settings": entries,
                        "restart_required": restart_required,
                    }
                )
            if key not in MANAGED_KEYS:
                return _registry_gate_rejection(key)
            return json.dumps(_entry_for_key(key, overrides, snapshot))
        except (SettingsValidationError, UnknownManagedKeyError) as exc:
            return f"ERROR: {_typed_error_text(exc)}"
        except Exception as exc:  # noqa: BLE001
            return _format_error_envelope(exc)

    @register_tool_category("settings")
    @tool
    def settings_set(
        key: Annotated[
            str,
            Field(
                description=(
                    "The managed key to override (e.g. "
                    "'limits.worker_pool_size'). Unknown keys are "
                    "refused by the registry gate."
                )
            ),
        ],
        value: Annotated[
            Any,
            Field(
                description=(
                    "The value to write (parsed + range-validated "
                    "against the key's registry spec — e.g. int "
                    "with ge=1 for all current keys). HONEST "
                    "contract: the write is NEVER live — it applies "
                    "at the next daemon restart."
                )
            ),
        ],
    ) -> str:
        """Write a DB override for a managed key. NEVER live — applies at next daemon restart.

Use tool_help("settings_set") for details."""
        try:
            svc = _resolve_service(caller_manager)
        except Exception as exc:  # noqa: BLE001
            return _format_error_envelope(exc)
        try:
            spec = MANAGED_KEYS.get(key)
            if spec is None:
                return _registry_gate_rejection(key)
            actor = _resolve_actor(caller_manager, caller_instance_id)
            # The ONE audited write path — set_override validates
            # (parse + range) BEFORE writing and persists updated_by.
            svc.set_override(key, value, updated_by=actor)
            overrides = svc.get_overrides()
            snapshot = _svc_get_snapshot()
            parsed = overrides.get(key, value)
            # O2 audit parity — one INFO line per tool write, the
            # SAME [Settings] shape as the router-side PUT audit
            # (daemon/routers/settings.py), with the [tool] lane
            # marker. Sensitive keys log "<redacted>" (sensitive
            # tripwire); the "value=" term stays for grep-ability.
            logged_value = "<redacted>" if spec.sensitive else parsed
            logger.info(
                "[Settings][tool] db override set key=%s value=%r "
                "agent=%s instance=%s",
                key,
                logged_value,
                actor,
                caller_instance_id or "<unknown>",
            )
            return json.dumps(
                _honest_write_response(
                    key, spec, actor, parsed, overrides, snapshot
                )
            )
        except (SettingsValidationError, UnknownManagedKeyError) as exc:
            return f"ERROR: {_typed_error_text(exc)}"
        except Exception as exc:  # noqa: BLE001
            return _format_error_envelope(exc)

    @register_tool_category("settings")
    @tool
    def settings_clear(
        key: Annotated[
            str,
            Field(
                description=(
                    "The managed key whose DB override should be "
                    "deleted (idempotent — 'cleared': false when no "
                    "row existed). After clearing, the ladder falls "
                    "back to env > yaml > default at the next boot."
                )
            ),
        ],
    ) -> str:
        """Clear a managed key's DB override (idempotent). NEVER live — applies at next daemon restart.

Use tool_help("settings_clear") for details."""
        try:
            svc = _resolve_service(caller_manager)
        except Exception as exc:  # noqa: BLE001
            return _format_error_envelope(exc)
        try:
            spec = MANAGED_KEYS.get(key)
            if spec is None:
                return _registry_gate_rejection(key)
            actor = _resolve_actor(caller_manager, caller_instance_id)
            cleared = svc.clear_override(key)
            overrides = svc.get_overrides()
            snapshot = _svc_get_snapshot()
            # O2 audit parity — the DELETE-side sibling of the set
            # line (same [Settings] family, [tool] lane marker).
            logger.info(
                "[Settings][tool] db override cleared key=%s "
                "agent=%s instance=%s",
                key,
                actor,
                caller_instance_id or "<unknown>",
            )
            response = _honest_write_response(
                key, spec, actor, None, overrides, snapshot
            )
            response["cleared"] = cleared
            return json.dumps(response)
        except (SettingsValidationError, UnknownManagedKeyError) as exc:
            return f"ERROR: {_typed_error_text(exc)}"
        except Exception as exc:  # noqa: BLE001
            return _format_error_envelope(exc)

    tools_list = [
        settings_get,
        settings_set,
        settings_clear,
    ]

    # Attach _full_doc_ (the ``_full_docs[tool_name]`` registration
    # path — see ``daemon/tools/_tool_registry.py``).
    settings_get._full_doc_ = _FULL_DOC_GET
    settings_set._full_doc_ = _FULL_DOC_SET
    settings_clear._full_doc_ = _FULL_DOC_CLEAR

    return tools_list


# ─────────────────────────────────────────────────────────────────────
# Per-tool full documentation (the help-tool + LangChain tool
# metadata source).
# ─────────────────────────────────────────────────────────────────────


_FULL_DOC_GET = """\
Read managed daemon settings with per-key tier reporting.

Args:
  key: Optional managed key (e.g. ``'limits.worker_pool_size'``).
       Omit to read ALL registered keys (returns
       ``{"settings": [...], "restart_required": <bool>}``).

Returns:
  JSON object per key: {
    "key":              "limits.worker_pool_size",
    "value":            <boot-applied running truth>,
    "source":           "db" | "env" | "yaml" | "default",
    "default":          <registry default>,
    "env_value":        <parsed env tier or null>,
    "yaml_value":       <raw yaml literal or null>,
    "db_override":      <db tier or null>,
    "requires_restart": true,
    "sensitive":        false,
    "divergent":        <value != resolved-tier value>
  }

``divergent=true`` means a DB override is pending: the running
value (``value``) still reflects the boot-applied state while the
``source`` tier resolves to the override — the change lands at
the next daemon restart. Sensitive keys report null tier details.

Visibility: the ``settings`` category is privileged (#8 in
``PRIVILEGED_TOOL_CATEGORIES``; ``secrets`` joined 2026-10-17
before this entry). NEVER default-granted — an explicit
``tools.allow`` entry is the only access path (ari + leader
allow entries landed 2026-10-18, P3).
"""

_FULL_DOC_SET = """\
Write a DB override for a managed key through the ONE audited
service path (``SettingsService.set_override`` — validates parse
+ range BEFORE writing).

Args:
  key:   The managed key (registry gate — unknown keys are
         refused).
  value: The value (validated against the key's registry spec —
         e.g. int with ge=1 for all current keys).

Returns (the HONEST contract — binding):
  JSON object: {
    "key":                 "<key>",
    "written":             <parsed value persisted>,
    "updated_by":          "<calling agent>",
    "requires_restart":    true,
    "source":              "db",
    "applies_at":          "next daemon restart",
    "running_value_still": <boot-applied value STILL running>,
    "divergent":           true,
    "live":                false,
    "note":                "<restart requirement, explicit>"
  }

The write is NEVER live (v1 semantics — no live re-arm of pools,
semaphores, or caps). The running daemon keeps its boot-applied
value until restart; ``running_value_still`` names it. Invalid
values are refused as ``"ERROR: <detail>"`` (registry validator).

Audit: one INFO line per write —
``[Settings][tool] db override set key=... value=... agent=...
instance=...`` (``value`` rendered ``<redacted>`` for sensitive
keys) — mirroring the router-side PUT audit line in
``daemon/routers/settings.py``.

Visibility: see ``settings_get``.
"""

_FULL_DOC_CLEAR = """\
Delete a managed key's DB override (idempotent — reports
``"cleared": false`` when no row existed). Use this to UNDO a
previous ``settings_set``: setting a key back to its default
leaves a db-tier row; only clear restores env/yaml/default tier
semantics at the next boot.

Args:
  key: The managed key whose override should be deleted.

Returns (the HONEST contract — binding):
  JSON object: {
    "key":                 "<key>",
    "written":             null,
    "cleared":             <true when a row was removed>,
    "updated_by":          "<calling agent>",
    "requires_restart":    true,
    "source":              <post-clear resolved tier>,
    "applies_at":          "next daemon restart",
    "running_value_still": <boot-applied value STILL running>,
    "divergent":           <bool>,
    "live":                false,
    "note":                "<restart requirement, explicit>"
  }

The clear is NEVER live — the ladder (db > env > yaml > default)
re-evaluates at the next daemon restart.

Audit: one INFO line per clear —
``[Settings][tool] db override cleared key=... agent=...
instance=...`` — mirroring the router-side DELETE audit line in
``daemon/routers/settings.py``.

Visibility: see ``settings_get``.
"""
