"""Infrastructure asset tools for managing servers, clusters, and other infra.

This module is the **tool-layer** integration point for the Infrastructure
Asset Storage feature (Phase 1: repository, Phase 2: tools). It wraps the
:class:`SQLModelInfraRepository` into 9 LangChain tools registered under
the ``"infra"`` category.

Architecture boundaries (do not cross):

* **Repository boundary** — :func:`create_infra_tools` receives the shared
  ``SQLModelInfraRepository`` from the :class:`InstanceManager`
  (C3, D5). It does **not** instantiate its own repository. This keeps
  all instances sharing one repository bound to the same engine, which
  avoids lock contention.
* **Audit boundary** — every asset mutation passes ``current_instance_id``
  (captured from the factory closure) as the ``created_by`` /
  ``updated_by`` / ``deleted_by`` field. The audit trail is therefore
  always traceable to the agent instance that initiated the change.
* **Error sanitization** — every tool catches exceptions and returns an
  error string instead of raising (N9). Repository error messages that
  contain user-supplied values (e.g. the unique-constraint violation
  from ``update_asset``) are truncated to 200 characters so they
  cannot be used to overflow the agent context window.
* **JSONB rendering** — ``InfraAsset.attributes`` and ``InfraAsset.relationships``
  are rendered as truncated single-line summaries in list/search/table
  outputs. The full JSON is returned by ``infra_asset_get`` and
  ``infra_asset_update`` via ``json.dumps(to_dict(), default=str)``.

Tool functions created by this module:

* ``infra_asset_create`` — Create a new infra asset.
* ``infra_asset_get`` — Fetch one asset by id.
* ``infra_asset_list`` — List assets in a project (with filters).
* ``infra_asset_search`` — Search assets by name / type / attributes.
* ``infra_asset_update`` — Update an existing asset.
* ``infra_asset_delete`` — Delete an asset.
* ``infra_type_register`` — Register / upsert a type schema (global).
* ``infra_type_list`` — List all registered types (global).
* ``infra_history_get`` — Get the change history for an asset.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

from ._tool_registry import register_tool_category
from daemon.services.env_key_policy import (
    env_key_is_secret_shaped,
    is_ascii_env_key,
)
from daemon.services.kms_lite import (
    KMSUnavailableError,
    build_env_marker,
    build_marker,
    kms_fingerprint,
    kms_request,
    kms_resolve_handle,
)
from daemon.services.kms_resolver import is_marker

if TYPE_CHECKING:
    from daemon.manager import InstanceManager
    from daemon.repositories.infra.repository import SQLModelInfraRepository


logger = logging.getLogger(__name__)


CATEGORY_NAME = "Infrastructure"
CATEGORY_DOC = """\
Manage infrastructure assets (servers, clusters, datacenters, ...).

**Asset CRUD:**
- `infra_asset_create` — Create a new asset
- `infra_asset_get` — Fetch one asset by id
- `infra_asset_list` — List assets in a project (with filters)
- `infra_asset_search` — Search assets by name/type/attributes
- `infra_asset_update` — Update an existing asset
- `infra_asset_delete` — Delete an asset

**Type Registry (global):**
- `infra_type_register` — Register/update a type schema
- `infra_type_list` — List all registered types

**History:**
- `infra_history_get` — Get the change history for an asset
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(s: str | None, n: int = 60) -> str:
    """Truncate a string to at most n characters, appending '...' if clipped."""
    if s is None:
        return ""
    if len(s) <= n:
        return s
    return s[: n - 3] + "..."


def _format_asset_row(asset: Any) -> str:
    """Format a single InfraAsset as a markdown table row.

    Args:
        asset: An object with ``.id``, ``.type``, ``.name``,
            ``.parent_asset_id``, ``.attributes``, ``.updated_at``,
            ``.created_by`` attributes.

    Returns:
        A ``| col1 | col2 | ... |`` markdown table line.
    """
    # Render a compact single-line view of the JSONB columns so
    # the table stays readable. Full JSON is available via infra_asset_get.
    attrs_preview = _truncate(json.dumps(asset.attributes, default=str) if asset.attributes else "", 60)
    rels_preview = _truncate(json.dumps(asset.relationships, default=str) if asset.relationships else "", 60)
    cells = [
        _truncate(asset.id, 40),
        _truncate(asset.type, 20),
        _truncate(asset.name, 30),
        _truncate(asset.parent_asset_id, 40) if asset.parent_asset_id else "",
        attrs_preview,
        rels_preview,
        _truncate(asset.updated_at, 28),
        _truncate(asset.created_by, 30) if asset.created_by else "",
    ]
    return "| " + " | ".join(cells) + " |"


def _format_type_row(type_row: Any) -> str:
    """Format a single InfraAssetType as a markdown table row.

    Args:
        type_row: An object with ``.name``, ``.description``,
            ``.updated_at`` attributes.

    Returns:
        A ``| col1 | col2 | col3 |`` markdown table line.
    """
    cells = [
        _truncate(type_row.name, 30),
        _truncate(type_row.description, 60),
        _truncate(type_row.updated_at, 28),
    ]
    return "| " + " | ".join(cells) + " |"


def _format_history_row(history: Any) -> str:
    """Format a single InfraAssetHistory row as a markdown table row.

    Args:
        history: An object with ``.timestamp``, ``.change_type``,
            ``.changed_by``, ``.changed_fields``, ``.old_values``,
            ``.new_values`` attributes.

    Returns:
        A ``| col1 | col2 | col3 | col4 |`` markdown table line.
    """
    changed_by = _truncate(history.changed_by, 30) if history.changed_by else ""
    changed_fields = (
        ", ".join(history.changed_fields) if history.changed_fields else ""
    )
    old_preview = _truncate(json.dumps(history.old_values, default=str) if history.old_values else "", 40)
    new_preview = _truncate(json.dumps(history.new_values, default=str) if history.new_values else "", 40)
    cells = [
        _truncate(history.timestamp, 28),
        _truncate(history.change_type, 16),
        changed_by,
        changed_fields,
        old_preview,
        new_preview,
    ]
    return "| " + " | ".join(cells) + " |"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_infra_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    repository: "SQLModelInfraRepository",
) -> list:
    """Create infrastructure tools with injected shared repository.

    The factory receives the shared ``SQLModelInfraRepository`` from the
    :class:`InstanceManager` (C3, D5). It does **not** instantiate its own
    repository — that lives at the manager level and is shared across all
    instances. This keeps all instances sharing one repository bound to the
    same engine, which avoids lock contention.

    The factory captures ``current_instance_id`` from the enclosing scope
    and passes it as the audit field (``created_by`` / ``updated_by`` /
    ``deleted_by`` / ``changed_by``) on every write, so all asset mutations
    are automatically traceable back to the agent instance that made them.

    Args:
        manager: The :class:`InstanceManager` instance. Accepted for
            parity with other factories but unused by these tools
            (the infra repository is process-level).
        current_instance_id: The current instance ID. Recorded on
            every asset row and history row as the audit
            ``created_by`` / ``updated_by`` / ``deleted_by`` /
            ``changed_by`` value.
        repository: Shared :class:`SQLModelInfraRepository` from
            ``manager.infra_repository``.

    Returns:
        A list of 9 tool functions:
        ``[infra_asset_create, infra_asset_get, infra_asset_list,
        infra_asset_search, infra_asset_update, infra_asset_delete,
        infra_type_register, infra_type_list, infra_history_get]``.
    """
    # Note: ``manager`` is accepted for parity with create_db_tools but
    # is not used in the tool bodies — the infra repository is a
    # process-level singleton, not an instance-specific resource.

    # -------------------------------------------------------------------------
    # infra_asset_create
    # -------------------------------------------------------------------------
    @register_tool_category("infra")
    @tool
    async def infra_asset_create(
        project_id: str,
        type: str,
        name: str,
        attributes: dict[str, Any] | None = None,
        parent_asset_id: str | None = None,
        relationships: dict[str, list[str]] | None = None,
    ) -> str:
        """Create a new infrastructure asset. Use tool_help("infra_asset_create") for details."""
        try:
            asset = repository.create_asset(
                project_id=project_id,
                type=type,
                name=name,
                attributes=attributes,
                parent_asset_id=parent_asset_id,
                relationships=relationships,
                created_by=current_instance_id,
            )
            return (
                f"Created infra asset: id={asset.id}, "
                f"type={asset.type}, name={asset.name}, project={project_id}.\n\n"
                f"{json.dumps(asset.to_dict(), indent=2, default=str)}"
            )
        except ValueError as exc:
            # Repo raises ``ValueError`` for constraint violations.
            # C4 fix: the message now differentiates UNIQUE (duplicate
            # (project_id, type, name)) from FK (invalid project_id or
            # parent_asset_id). The tool layer mirrors the same
            # distinction so the agent can react appropriately.
            msg = str(exc)
            if "already exists" in msg:
                # UNIQUE constraint violation.
                logger.warning(
                    "infra_asset_create duplicate: "
                    "project=%s type=%s name=%s", project_id, type, name
                )
                return (
                    f"ERROR: An infra asset with "
                    f"(project_id={project_id!r}, type={type!r}, "
                    f"name={name!r}) already exists."
                )
            if "Invalid reference" in msg:
                # FOREIGN KEY constraint violation.
                logger.exception(
                    "infra_asset_create FK violation: "
                    "project=%s type=%s name=%s parent=%s err=%s",
                    project_id, type, name, parent_asset_id, exc,
                )
                return (
                    f"ERROR: Invalid reference — "
                    f"project_id={project_id!r} or "
                    f"parent_asset_id={parent_asset_id!r} does not exist."
                )
            # Other / unknown constraint violation (CHECK, NOT NULL, ...).
            # Truncate to 200 chars to prevent context-window overflow.
            truncated = msg[:200]
            logger.exception(
                "infra_asset_create constraint error: "
                "project=%s type=%s name=%s: %s",
                project_id, type, name, exc,
            )
            return f"ERROR: {truncated}"
        except Exception as exc:
            # N9: log the full exception for operators; return only the
            # class name to the agent. ``str(exc)`` is unsafe because the
            # infra repository error text may echo user-supplied values
            # in a way that could be used for context-window overflow.
            logger.exception(
                "infra_asset_create failed for project=%s type=%s name=%s: %s",
                project_id, type, name, exc,
            )
            # C1 fix: use ``exc.__class__.__name__`` instead of
            # ``type(exc).__name__``. The ``type`` parameter above
            # shadows the Python builtin, so ``type(exc)`` would
            # raise ``TypeError: 'str' object is not callable``
            # before producing the class name. ``__class__`` is
            # safe and idiomatic.
            return (
                f"ERROR: Failed to create infra asset "
                f"({exc.__class__.__name__})."
            )

    infra_asset_create._full_doc_ = """Create a new infrastructure asset.

    The asset is persisted with the supplied ``type``, ``name``, and optional
    ``attributes`` / ``parent_asset_id`` / ``relationships``. The
    ``current_instance_id`` of the agent making the call is recorded as the
    ``created_by`` audit field on both the asset row and the history row
    written by the repository.

    Args:
        project_id: The owning project ID. Must already exist (enforced
            by the FK to the ``projects`` table).
        type: Asset type identifier (e.g. ``"server"``, ``"k8s_cluster"``,
            ``"datacenter"``). Not validated against the type registry
            here — use ``infra_type_register`` to manage the registry.
        name: Human-readable name, unique within ``(project_id, type)``.
        attributes: Optional type-specific structured data (dict). Defaults
            to an empty dict.
        parent_asset_id: Optional parent asset ID for parent/child hierarchies.
        relationships: Optional dict of ``{entity_type: [id, ...]}`` for
            cross-entity links. Defaults to an empty dict.

    Returns:
        Confirmation string with asset metadata and the full JSON
        representation of the created asset. Raises ``ERROR: ...`` on
        duplicate name or repository failure.
    """

    # -------------------------------------------------------------------------
    # infra_asset_get
    # -------------------------------------------------------------------------
    @register_tool_category("infra")
    @tool
    async def infra_asset_get(project_id: str, asset_id: str) -> str:
        """Fetch a single infrastructure asset by its ID. Use tool_help("infra_asset_get") for details."""
        try:
            asset = repository.get_asset(asset_id, project_id=project_id)
            if asset is None:
                return (
                    f"ERROR: No infra asset found with id={asset_id!r} "
                    f"in project {project_id!r}."
                )
            return json.dumps(asset.to_dict(), indent=2, default=str)
        except Exception as exc:
            logger.exception(
                "infra_asset_get failed: asset_id=%s project=%s: %s",
                asset_id, project_id, exc,
            )
            return f"ERROR: Failed to get infra asset ({type(exc).__name__})."

    infra_asset_get._full_doc_ = """Fetch a single infrastructure asset by its UUID4 ID.

    The asset is verified to belong to ``project_id`` before being returned.
    If the ID exists but belongs to a different project, the call returns
    ``ERROR: No infra asset found`` rather than the asset.

    Args:
        project_id: The project the asset must belong to.
        asset_id: The asset's UUID4 primary key.

    Returns:
        The full JSON representation of the asset, or ``ERROR: ...`` if
        not found or on repository failure.
    """

    # -------------------------------------------------------------------------
    # infra_asset_list
    # -------------------------------------------------------------------------
    @register_tool_category("infra")
    @tool
    async def infra_asset_list(
        project_id: str,
        type: str | None = None,
        parent_asset_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """List infrastructure assets in a project with optional filters. Use tool_help("infra_asset_list") for details."""
        try:
            assets = repository.list_assets(
                project_id=project_id,
                type=type,
                parent_asset_id=parent_asset_id,
                limit=limit,
                offset=offset,
            )
            if not assets:
                return (
                    f"No infra assets found in project {project_id!r} "
                    f"(type={type!r}, parent_asset_id={parent_asset_id!r})."
                )

            header = (
                "| id | type | name | parent_asset_id | "
                "attributes | relationships | updated_at | created_by |"
            )
            divider = "|---|---|---|---|---|---|---|---|"
            lines = [
                f"Infra assets in project {project_id!r} ({len(assets)} rows):",
                "",
                header,
                divider,
            ]
            for asset in assets:
                lines.append(_format_asset_row(asset))
            return "\n".join(lines)
        except Exception as exc:
            logger.exception(
                "infra_asset_list failed: project=%s: %s", project_id, exc
            )
            # C1 fix: use ``exc.__class__.__name__`` (the ``type``
            # parameter above shadows the builtin ``type``).
            return f"ERROR: Failed to list infra assets ({exc.__class__.__name__})."

    infra_asset_list._full_doc_ = """List infrastructure assets in a project with optional filters.

    Always filters on ``project_id`` for project isolation. The
    ``parent_asset_id=None`` (default) returns only top-level / unparented
    assets. To see all assets regardless of hierarchy, use
    ``infra_asset_search`` without a ``parent_asset_id``.

    Args:
        project_id: The project to list assets for.
        type: Optional exact-match filter on the ``type`` column.
        parent_asset_id: Optional parent filter. ``None`` (default)
            returns only unparented (root) assets; a string ID returns
            only direct children of that parent.
        limit: Maximum number of rows to return (default 50).
        offset: Number of rows to skip for pagination (default 0).

    Returns:
        A markdown table with id, type, name, parent, attributes preview,
        relationships preview, updated_at, and created_by. Returns
        ``ERROR: ...`` on repository failure.
    """

    # -------------------------------------------------------------------------
    # infra_asset_search
    # -------------------------------------------------------------------------
    @register_tool_category("infra")
    @tool
    async def infra_asset_search(
        project_id: str,
        query: str,
        type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> str:
        """Search infrastructure assets by name substring and optional type. Use tool_help("infra_asset_search") for details."""
        try:
            query_dict: dict[str, Any] = {"name": query}
            if type is not None:
                query_dict["type"] = type
            assets = repository.search_assets(
                project_id=project_id,
                query=query_dict,
                limit=limit,
                offset=offset,
            )
            if not assets:
                return (
                    f"No infra assets match query={query!r} "
                    f"(type={type!r}) in project {project_id!r}."
                )

            header = (
                "| id | type | name | parent_asset_id | "
                "attributes | relationships | updated_at | created_by |"
            )
            divider = "|---|---|---|---|---|---|---|---|"
            lines = [
                f"Infra assets matching query={query!r} "
                f"in project {project_id!r} ({len(assets)} rows):",
                "",
                header,
                divider,
            ]
            for asset in assets:
                lines.append(_format_asset_row(asset))
            return "\n".join(lines)
        except Exception as exc:
            logger.exception(
                "infra_asset_search failed: project=%s query=%s: %s",
                project_id, query, exc,
            )
            # C1 fix: use ``exc.__class__.__name__`` (the ``type``
            # parameter above shadows the builtin ``type``).
            return f"ERROR: Failed to search infra assets ({exc.__class__.__name__})."

    infra_asset_search._full_doc_ = """Search infrastructure assets by name substring and optional type.

    Performs a case-insensitive ``LIKE '%query%'`` on the ``name`` column.
    When ``type`` is provided, additionally filters by exact type match.

    Args:
        project_id: The project to search within.
        query: Substring to match against the ``name`` column (case-insensitive).
        type: Optional exact-match filter on the ``type`` column.
        limit: Maximum number of rows to return (default 50).
        offset: Number of rows to skip for pagination (default 0).

    Returns:
        A markdown table with id, type, name, parent, attributes preview,
        relationships preview, updated_at, and created_by. Returns
        ``ERROR: ...`` on repository failure.
    """

    # -------------------------------------------------------------------------
    # infra_asset_update
    # -------------------------------------------------------------------------
    @register_tool_category("infra")
    @tool
    async def infra_asset_update(
        project_id: str,
        asset_id: str,
        attributes: dict[str, Any] | None = None,
        name: str | None = None,
        parent_asset_id: str | None = None,
        relationships: dict[str, list[str]] | None = None,
    ) -> str:
        """Update an existing infrastructure asset. Use tool_help("infra_asset_update") for details."""
        try:
            # Build updates dict from only the fields that were explicitly
            # provided (not None). This lets the caller clear a field by
            # passing an explicit empty dict / string if needed.
            updates: dict[str, Any] = {}
            if attributes is not None:
                updates["attributes"] = attributes
            if name is not None:
                updates["name"] = name
            if parent_asset_id is not None:
                updates["parent_asset_id"] = parent_asset_id
            if relationships is not None:
                updates["relationships"] = relationships

            if not updates:
                return (
                    "ERROR: No update fields provided. Provide at least one of: "
                    "attributes, name, parent_asset_id, relationships."
                )

            # C2 fix: pass ``project_id`` so the repository enforces
            # project isolation — a mismatched project_id yields
            # ``None`` (same as "asset not found") rather than
            # mutating a cross-project row.
            asset = repository.update_asset(
                asset_id,
                project_id=project_id,
                updated_by=current_instance_id,
                **updates,
            )
            if asset is None:
                return (
                    f"ERROR: No infra asset found with id={asset_id!r} "
                    f"in project {project_id!r}."
                )
            return (
                f"Updated infra asset: id={asset.id}.\n\n"
                f"{json.dumps(asset.to_dict(), indent=2, default=str)}"
            )
        except ValueError as exc:
            # Repo raises ValueError for the unique-constraint violation
            # (update would create a duplicate (project_id, type, name)).
            # Truncate to 200 chars to prevent context-window overflow.
            msg = str(exc)[:200]
            logger.exception(
                "infra_asset_update constraint error: asset_id=%s: %s",
                asset_id, exc,
            )
            return f"ERROR: Update violates unique (project_id, type, name) constraint: {msg}"
        except AttributeError as exc:
            # Repo raises AttributeError for unknown column names.
            logger.exception(
                "infra_asset_update invalid field: asset_id=%s: %s",
                asset_id, exc,
            )
            return f"ERROR: Invalid update field: {exc}"
        except Exception as exc:
            logger.exception(
                "infra_asset_update failed: asset_id=%s: %s",
                asset_id, exc,
            )
            return f"ERROR: Failed to update infra asset ({type(exc).__name__})."

    infra_asset_update._full_doc_ = """Update fields on an existing infrastructure asset.

    Auto-records an ``updated`` history row with the pre-update snapshot,
    the list of changed field names, and old/new values for each changed
    field. The ``current_instance_id`` is recorded as ``updated_by`` on the
    row and as ``changed_by`` on the history entry.

    Project isolation (C2): the asset is verified to belong to
    ``project_id`` before the update is applied — a mismatched
    project_id yields ``ERROR: No infra asset found ...`` rather
    than mutating a cross-project row.

    Args:
        project_id: The project the asset belongs to. The asset must
            belong to this project or the call returns a not-found
            error.
        asset_id: The asset's UUID4 primary key.
        attributes: Replacement value for the ``attributes`` JSONB column.
            Pass an explicit dict to replace; omit to leave unchanged.
        name: Replacement value for the ``name`` column.
        parent_asset_id: Replacement value for the ``parent_asset_id`` column.
        relationships: Replacement value for the ``relationships`` JSONB column.

    Returns:
        Confirmation string with the full JSON representation of the
        updated asset. Returns ``ERROR: ...`` on not-found, constraint
        violation, invalid field, or repository failure.
    """

    # -------------------------------------------------------------------------
    # infra_asset_delete
    # -------------------------------------------------------------------------
    @register_tool_category("infra")
    @tool
    async def infra_asset_delete(project_id: str, asset_id: str) -> str:
        """Delete an infrastructure asset and record its history. Use tool_help("infra_asset_delete") for details."""
        try:
            # C2 fix: pass ``project_id`` so the repository enforces
            # project isolation — a mismatched project_id yields
            # ``False`` (same as "asset not found") rather than
            # deleting a cross-project row.
            deleted = repository.delete_asset(
                asset_id,
                project_id=project_id,
                deleted_by=current_instance_id,
            )
            if not deleted:
                return (
                    f"No infra asset with id={asset_id!r} in project "
                    f"{project_id!r} to delete."
                )
            return (
                f"Deleted infra asset: id={asset_id} (project={project_id})."
            )
        except Exception as exc:
            logger.exception(
                "infra_asset_delete failed: asset_id=%s: %s",
                asset_id, exc,
            )
            return (
                f"ERROR: Failed to delete infra asset "
                f"({type(exc).__name__})."
            )

    infra_asset_delete._full_doc_ = """Delete an infrastructure asset and record its history.

    The repository writes a ``deleted`` history row with the full pre-delete
    snapshot before removing the row, so the audit trail is preserved even
    after the asset is gone. The ``current_instance_id`` is recorded as
    ``deleted_by`` on the history entry.

    Project isolation (C2): the asset is verified to belong to
    ``project_id`` before the delete is applied — a mismatched
    project_id yields the "no asset ... to delete" message rather
    than deleting a cross-project row.

    Args:
        project_id: The project the asset belongs to. The asset must
            belong to this project or the call returns the
            "no asset ... to delete" message.
        asset_id: The asset's UUID4 primary key.

    Returns:
        Confirmation string on success, or ``ERROR: ...`` on not-found
        or repository failure.
    """

    # -------------------------------------------------------------------------
    # infra_type_register
    # -------------------------------------------------------------------------
    @register_tool_category("infra")
    @tool
    async def infra_type_register(
        name: str,
        schema_def: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> str:
        """Register or update an infrastructure asset type schema (global). Use tool_help("infra_type_register") for details."""
        try:
            # The repository's parameter is still called ``schema_json``
            # (the locked design name on the ``infra_asset_types``
            # table), so we map the tool-level ``schema_def`` →
            # ``schema_json`` at the call site. ``schema_def`` is the
            # tool-layer name (C3 fix) to avoid shadowing Pydantic's
            # ``BaseModel.schema_json`` method, which used to emit a
            # ``UserWarning`` on every import and would become fatal
            # under ``pytest -W error``.
            type_row = repository.register_type(
                name=name,
                schema_json=schema_def,
                description=description,
            )
            return (
                f"Registered infra type: name={type_row.name}.\n\n"
                f"{json.dumps(type_row.to_dict(), indent=2, default=str)}"
            )
        except Exception as exc:
            logger.exception(
                "infra_type_register failed: name=%s: %s", name, exc
            )
            return (
                f"ERROR: Failed to register infra type "
                f"({type(exc).__name__})."
            )

    infra_type_register._full_doc_ = """Register or update an infrastructure asset type schema.

    Atomic upsert: if a type with the same ``name`` already exists,
    ``description`` and ``schema_def`` are overwritten and ``updated_at``
    is bumped; otherwise a new row is created. This is a **global**
    operation — there is no ``project_id``; type definitions are shared
    across all projects.

    Args:
        name: Type identifier (also the value used in
            :attr:`InfraAsset.type`). Must be non-empty.
        schema_def: Optional JSON-Schema-shaped document stored verbatim.
            Defaults to an empty dict. (Renamed from ``schema_json``
            in C3 to avoid shadowing Pydantic's ``BaseModel.schema_json``
            method.)
        description: Optional human-readable description. Defaults to empty
            string.

    Returns:
        Confirmation string with the full JSON representation of the
        registered type. Returns ``ERROR: ...`` on repository failure.
    """

    # -------------------------------------------------------------------------
    # infra_type_list
    # -------------------------------------------------------------------------
    @register_tool_category("infra")
    @tool
    async def infra_type_list() -> str:
        """List all registered infrastructure asset types (global). Use tool_help("infra_type_list") for details."""
        try:
            types = repository.list_types()
            if not types:
                return (
                    "No infra asset types registered. "
                    "Use infra_type_register to add one."
                )

            header = "| name | description | updated_at |"
            divider = "|---|---|---|"
            lines = [
                f"Registered infra asset types ({len(types)}):",
                "",
                header,
                divider,
            ]
            for t in types:
                lines.append(_format_type_row(t))
            return "\n".join(lines)
        except Exception as exc:
            logger.exception("infra_type_list failed: %s", exc)
            return (
                f"ERROR: Failed to list infra types "
                f"({type(exc).__name__})."
            )

    infra_type_list._full_doc_ = """List every registered infrastructure asset type.

    This is a **global** operation — there is no ``project_id``; type
    definitions are shared across all projects. Results are ordered by
    ``name`` ascending.

    Returns:
        A markdown table with name, description, and updated_at. Returns
        ``ERROR: ...`` on repository failure.
    """

    # -------------------------------------------------------------------------
    # infra_history_get
    # -------------------------------------------------------------------------
    @register_tool_category("infra")
    @tool
    async def infra_history_get(
        project_id: str,
        asset_id: str,
        limit: int = 20,
    ) -> str:
        """Get the change history for an infrastructure asset. Use tool_help("infra_history_get") for details."""
        try:
            # C2 fix: pass ``project_id`` so the repository enforces
            # project isolation — a mismatched project_id yields
            # ``[]`` (the "No history found" branch) rather than
            # returning history for a cross-project asset.
            history_rows = repository.get_history(
                asset_id, project_id=project_id, limit=limit
            )
            if not history_rows:
                return (
                    f"No history found for asset id={asset_id!r} "
                    f"in project {project_id!r}."
                )

            header = (
                "| timestamp | change_type | changed_by | "
                "changed_fields | old_values | new_values |"
            )
            divider = "|---|---|---|---|---|---|"
            lines = [
                f"Change history for asset id={asset_id!r} "
                f"({len(history_rows)} entries):",
                "",
                header,
                divider,
            ]
            for row in history_rows:
                lines.append(_format_history_row(row))
            return "\n".join(lines)
        except Exception as exc:
            logger.exception(
                "infra_history_get failed: asset_id=%s: %s",
                asset_id, exc,
            )
            return (
                f"ERROR: Failed to get infra asset history "
                f"({type(exc).__name__})."
            )

    infra_history_get._full_doc_ = """Get the change history for an infrastructure asset.

    Returns history rows ordered by ``timestamp`` descending (newest first).
    Works for both live assets and assets that have been deleted — the
    repository matches by ``asset_id`` OR by the snapshot's stored ``id``
    field, so the ``deleted`` history entry (which nullifies ``asset_id``)
    is still retrievable.

    Project isolation (C2): only history rows whose ``project_id``
    matches are returned — a mismatched project_id yields the
    "No history found" message rather than leaking audit rows
    belonging to a different project.

    Args:
        project_id: The project the asset belongs to. Only history
            rows for this project are returned.
        asset_id: The asset's UUID4 primary key (current or historical).
        limit: Maximum number of history entries to return (default 20).

    Returns:
        A markdown table with timestamp, change_type, changed_by,
        changed_fields, old_values, and new_values. Returns ``ERROR: ...``
        on repository failure.
    """

    # -------------------------------------------------------------------------
    # Return
    # -------------------------------------------------------------------------
    return [
        infra_asset_create,
        infra_asset_get,
        infra_asset_list,
        infra_asset_search,
        infra_asset_update,
        infra_asset_delete,
        infra_type_register,
        infra_type_list,
        infra_history_get,
    ]


# ---------------------------------------------------------------------------
# KMS-Lite tools (P3-WP7 + P3-WP6 — designer-agent mission)
#
# Two tool surfaces exposed under the ``infra`` category per arch §7.5:
#   * ``kms_request`` — mint a new handle; returns {handle, fingerprint}
#   * ``kms_attach`` — bind an existing handle to an MCP server's env key
#     (writes the ``__KMS_REF__<handle>__`` marker into that row's
#     ``config.env[<env_key>]``; appends a handle-binding entry to
#     ``instance_metadata.bound_handles``). Plaintext NEVER appears in
#     any return value, log line, or DB column.
#   * ``kms_lookup_handle`` — read-side: confirm a recorded handle is
#     still known (fingerprint match). NEVER reveals plaintext.
#
# Why a separate factory (vs. folding into ``create_infra_tools``):
#   * Different concerns: infra assets are typed records; KMS handles
#     are secret-bearing pointers. Keeping the factories disjoint makes
#     the audit trail clearer (KMS tools are the only writers of the
#     ``instance_metadata.bound_handles`` substrate).
#   * The factory still registers under ``infra`` so agent ``tools.allow``
#     does not need a new category.
#
# Day-1 contract (§7.5 — ratified):
#   * No policy layer, no allowlist, no TTL sweeps, no rotate/revoke.
#   * Handles-not-secrets: plaintext NEVER leaves the encrypted store
#     except at the spawn-time resolver (``kms_resolver.py``).
# ---------------------------------------------------------------------------


def create_kms_tools(
    manager: "InstanceManager",
    current_instance_id: str,
) -> list:
    """Create KMS-Lite mint / attach / lookup tools.

    Args:
        manager: The :class:`InstanceManager` instance. Used to access
            ``manager._mcp_server_repository`` for the ``kms_attach``
            write path. Accepted for parity with other factories.
        current_instance_id: The current instance ID. Recorded as the
            ``actor`` on every mint call and threaded into the
            ``bound_handles`` audit entry on attach.

    Returns:
        A list of 3 tool functions:
        ``[kms_request, kms_attach, kms_lookup_handle]``.

    Failure surfaces:

    * ``kms_request`` returns ``ERROR: KMS_UNAVAILABLE: ...`` when
      :data:`SYSTEM_ENCRYPTION_KEY` is absent or invalid. The
      fail-closed ``KMSUnavailableError`` is the day-1 contract (P3-WP9
      closes arch §8 R2). It is the escalation envelope's signal to
      report ``installed_but_unconfigured`` /
      ``detection_evidence=kms_key_absent`` (sibling WP wires the
      envelope; this tool only emits the typed error).
    * ``kms_attach`` returns ``ERROR: HANDLE_NOT_FOUND: ...`` if the
      caller passes a handle the store does not recognise. This is a
      day-1 contract failure (re-mint is the recovery path; revocation
      is §7.5a deferred).
    * All three tools catch generic exceptions and return
      ``ERROR: <exc-class-name>`` so a misbehaving KMS-Lite cannot
      crash the agent turn (N9 contract — same as the rest of the
      ``infra`` category).
    """

    logger = logging.getLogger(__name__)

    # -------------------------------------------------------------------------
    # kms_request
    # -------------------------------------------------------------------------
    @register_tool_category("infra")
    @tool
    def kms_request(
        service: str,
        reason: str,
    ) -> str:
        """Mint a new KMS handle for a service. Use tool_help("kms_request") for details.

        Returns ``{"handle": "KMS_HANDLE_<uuid>", "fingerprint":
        "<sha256[:16]>"}`` as JSON. The plaintext secret is held ONLY in
        the encrypted store; the caller never receives it. Fail-closed:
        raises :class:`KMSUnavailableError` when ``SYSTEM_ENCRYPTION_KEY``
        is absent or invalid (P3-WP9 invariant).
        """
        try:
            record = kms_request(
                service=service,
                reason=reason,
                actor=current_instance_id,
            )
            return json.dumps(record)
        except KMSUnavailableError as exc:
            # Fail-closed (P3-WP9). Surface as a typed tool error so
            # the escalation envelope (sibling WP) can convert to
            # ``installed_but_unconfigured`` /
            # ``detection_evidence=kms_key_absent``. We deliberately do
            # NOT log the exception's message: it would only echo
            # env-var state and is already operator-side info.
            logger.warning(
                "kms_request refused: kms_unavailable actor=%s",
                current_instance_id,
            )
            return f"ERROR: KMS_UNAVAILABLE: {exc}"
        except Exception as exc:  # noqa: BLE001 — N9 contract
            logger.exception(
                "kms_request unexpected error: actor=%s", current_instance_id
            )
            return f"ERROR: {type(exc).__name__}"

    # -------------------------------------------------------------------------
    # kms_attach
    # -------------------------------------------------------------------------
    @register_tool_category("infra")
    @tool
    def kms_attach(
        server_id: str,
        env_key: str,
        handle: str | None = None,
        env_source: str | None = None,
    ) -> str:
        """Bind a KMS handle OR an env-ref to an MCP server's env key. Use tool_help("kms_attach") for details.

        Two mutually-exclusive modes share this entry point. Exactly one
        of ``handle`` / ``env_source`` MUST be supplied — passing both
        or neither returns ``ERROR: INVALID_ARGUMENT``.

        LANE-1 (handle mode — minted KMS handle):

            Reads the ``mcp_servers`` row fresh from the DB, then
            writes:

            * ``config.env[<env_key>]`` ← ``__KMS_REF__<handle>__`` marker
            * ``instance_metadata.bound_handles`` ← ``{handle, env_key,
              fingerprint, actor}``

        LANE-2 (env-ref mode — daemon-process env var):

            Validates that the named ``env_source`` var EXISTS in the
            daemon's ``os.environ`` (presence check only — value is never
            read or returned), then writes:

            * ``config.env[<env_key>]`` ← ``__KMS_ENV__<env_source>__`` marker
            * ``instance_metadata.env_refs`` ← ``{env_key, env_source,
              actor}``

            This is the bridge for the OpenDesign BYOK chain: the
            daemon reuses the existing ``OPENAI_API_KEY`` from its own
            ``.env`` (no new key material, per user directive
            2026-10-02) by writing a pointer into the MCP server's
            ``config.env``. The resolver at
            ``daemon/services/kms_resolver.py`` substitutes the marker
            to the plaintext value at spawn time — plaintext is in-RAM
            ONLY and never stored.

        The read-then-write pattern is load-bearing for both modes: any
        code path that re-serialises ``mcp_servers.config`` MUST re-read
        the row to avoid overwriting a stored marker with cached
        plaintext (R1). Plaintext NEVER appears in any write — only
        markers (``__KMS_REF__`` or ``__KMS_ENV__``).

        This tool is the ONLY way a secret-shaped ``env_key`` gets
        written going forward in the new flow (LANE-2 env-ref mode).
        ``mcp_set_env`` stays UNCHANGED and keeps rejecting
        secret-shaped keys — its regression is pinned by the existing
        ``tests/unit/tools/test_mcp_set_env_tools.py`` pack.

        LANE-2 env-ref markers are restart-DURABLE (resolved fresh
        from the daemon env every spawn — no in-memory drain).
        LANE-1 minted handles are NOT (the day-1 KMS-Lite store is
        in-memory and drains on daemon restart; the install-opendesign
        skill's restart-drain self-heal covers that lane).
        """
        try:
            # ── Mode arbitration (exactly one of handle/env_source) ──
            if handle is not None and env_source is not None:
                return (
                    "ERROR: INVALID_ARGUMENT: pass exactly one of "
                    "'handle' (LANE-1, minted KMS handle) or "
                    "'env_source' (LANE-2, daemon-process env var "
                    "name) — not both."
                )
            if handle is None and env_source is None:
                return (
                    "ERROR: INVALID_ARGUMENT: must pass exactly one of "
                    "'handle' (LANE-1) or 'env_source' (LANE-2). "
                    "handle=<KMS_HANDLE_…> → __KMS_REF__<HANDLE>__ "
                    "marker; env_source=<VARNAME> → __KMS_ENV__<VARNAME>__ "
                    "marker."
                )
            use_env_ref_mode = env_source is not None

            # ── R1: resolve + read the row FRESH at call time ──────
            repo = manager._mcp_server_repository
            server = repo.get_mcp_server(server_id)
            if server is None:
                return f"ERROR: SERVER_NOT_FOUND: server_id={server_id!r}"

            existing_config = dict(server.config or {})
            env_block = dict(existing_config.get("env") or {})

            if use_env_ref_mode:
                # ── LANE-2: env-ref mode ────────────────────────────
                # build_env_marker validates the var name shape; the
                # ``os.environ`` presence check is the spawn-time
                # resolution guarantee (resolver fail-closed on
                # missing). We do an EAGER presence check too so the
                # caller learns the misconfiguration at attach time,
                # not at next-spawn — but never echo the value.
                try:
                    marker = build_env_marker(env_source)
                except ValueError as exc:
                    return (
                        f"ERROR: INVALID_ARGUMENT: env_source={env_source!r} "
                        f"is not a valid env-var name ({exc})"
                    )
                if env_source not in os.environ:
                    # Name only — never a value (the value never enters
                    # this tool's frame; the var was simply not present
                    # in the daemon's process env).
                    return (
                        f"ERROR: ENV_VAR_NOT_FOUND: env_source={env_source!r} "
                        "is not set in the daemon's environment. "
                        "Set the env var on the daemon process (or in "
                        "the daemon's .env) and retry."
                    )

                # Idempotent collapse on (env_source, env_key) — same
                # tuple collapses; the audit substrate keeps a fresh
                # actor + is appended at the end.
                existing_meta = dict(server.instance_metadata or {})
                env_refs = list(existing_meta.get("env_refs") or [])
                env_refs = [
                    b for b in env_refs
                    if not (
                        b.get("env_source") == env_source
                        and b.get("env_key") == env_key
                    )
                ]
                env_refs.append(
                    {
                        "env_source": env_source,
                        "env_key": env_key,
                        "actor": current_instance_id,
                    }
                )
                existing_meta["env_refs"] = env_refs

                env_block[env_key] = marker
                existing_config["env"] = env_block

                updated = repo.update_mcp_server(
                    server_id,
                    config=existing_config,
                    instance_metadata=existing_meta,
                )
                if updated is None:
                    return f"ERROR: SERVER_NOT_FOUND: server_id={server_id!r} vanished mid-update; retry."

                # ── Warmup-pool config refresh (mcp warmup-pool config
                # starvation fix, 2026-10-02) — see mcp_set_env for the
                # full rationale. Read the row fresh so the overlay
                # carries the just-written marker plus every other
                # pre-existing env key. kms_attach writes a marker (NOT
                # plaintext) — the resolver at spawn time still expands
                # it to the plaintext value the subprocess needs.
                # ``getattr(row, "name", "")`` keeps test stubs (which
                # sometimes omit ``.name``) from blowing up; production
                # rows always have it (McpServer.name is a non-null str).
                fresh_row = repo.get_mcp_server(server_id) if hasattr(repo, "get_mcp_server") else None
                fresh_env = ((fresh_row.config or {}).get("env") if fresh_row is not None else None) or {}
                server_name_for_pool = getattr(fresh_row, "name", "") if fresh_row is not None else ""
                if server_name_for_pool:
                    _refresh_pool_env(manager, server_name_for_pool, fresh_env)

                # Result echoes marker + var NAME only — never a value.
                return json.dumps(
                    {
                        "server_id": server_id,
                        "env_key": env_key,
                        "env_source": env_source,
                        "marker": marker,
                        "note": (
                            "LANE-2 env-ref attached. Resolved fresh "
                            "from the daemon's os.environ at spawn time. "
                            "Restart-durable (NOT drained by daemon "
                            "restart — the var name is the binding, the "
                            "value lives in the daemon env)."
                        ),
                    }
                )

            # ── LANE-1: handle mode (unchanged from v0.x) ──────────
            # Confirm the handle is known. We do NOT reveal plaintext
            # at this layer; the attach is a marker-write only.
            fp = kms_fingerprint(handle)  # type: ignore[arg-type]
            if fp is None:
                return (
                    f"ERROR: HANDLE_NOT_FOUND: handle={handle!r} "
                    "is not known to the KMS-Lite store. Re-mint via "
                    "kms_request."
                )

            existing_meta = dict(server.instance_metadata or {})
            bindings = list(existing_meta.get("bound_handles") or [])
            # Idempotent: same (handle, env_key) tuple collapses.
            bindings = [
                b for b in bindings
                if not (b.get("handle") == handle and b.get("env_key") == env_key)
            ]
            bindings.append(
                {
                    "handle": handle,
                    "env_key": env_key,
                    "fingerprint": fp,
                    "actor": current_instance_id,
                }
            )
            existing_meta["bound_handles"] = bindings

            env_block[env_key] = build_marker(handle)  # type: ignore[arg-type]
            existing_config["env"] = env_block

            updated = repo.update_mcp_server(
                server_id,
                config=existing_config,
                instance_metadata=existing_meta,
            )
            if updated is None:
                return f"ERROR: SERVER_NOT_FOUND: server_id={server_id!r}"

            # ── Warmup-pool config refresh (mcp warmup-pool config
            # starvation fix, 2026-10-02) — LANE-1 mirrors LANE-2
            # above. Read fresh so the overlay carries the just-written
            # handle marker alongside every other pre-existing env key.
            # ``getattr(row, "name", "")`` keeps test stubs (which
            # sometimes omit ``.name``) from blowing up; production
            # rows always have it (McpServer.name is a non-null str).
            fresh_row = repo.get_mcp_server(server_id) if hasattr(repo, "get_mcp_server") else None
            fresh_env = ((fresh_row.config or {}).get("env") if fresh_row is not None else None) or {}
            server_name_for_pool = getattr(fresh_row, "name", "") if fresh_row is not None else ""
            if server_name_for_pool:
                _refresh_pool_env(manager, server_name_for_pool, fresh_env)

            return json.dumps(
                {
                    "server_id": server_id,
                    "handle": handle,
                    "env_key": env_key,
                    "fingerprint": fp,
                    "marker": build_marker(handle),  # type: ignore[arg-type]
                }
            )
        except Exception as exc:  # noqa: BLE001 — N9 contract
            # Includes KMSUnavailableError if the store was initialised
            # with a bad key between the lookup and the attach (very
            # narrow window). Surface as a typed error.
            logger.exception(
                "kms_attach unexpected error: actor=%s server_id=%s",
                current_instance_id, server_id,
            )
            if isinstance(exc, KMSUnavailableError):
                return f"ERROR: KMS_UNAVAILABLE: {exc}"
            return f"ERROR: {type(exc).__name__}"

    # -------------------------------------------------------------------------
    # kms_lookup_handle
    # -------------------------------------------------------------------------
    @register_tool_category("infra")
    @tool
    def kms_lookup_handle(handle: str) -> str:
        """Look up the recorded fingerprint for a KMS handle. Use tool_help("kms_lookup_handle") for details.

        Returns ``{"handle": "...", "fingerprint": "<sha256[:16]>"}``
        when known, or ``ERROR: HANDLE_NOT_FOUND`` when the store does
        not recognise the handle. NEVER returns plaintext.
        """
        try:
            fp = kms_fingerprint(handle)
            if fp is None:
                return f"ERROR: HANDLE_NOT_FOUND: handle={handle!r}"
            return json.dumps({"handle": handle, "fingerprint": fp})
        except Exception as exc:  # noqa: BLE001 — N9 contract
            logger.exception(
                "kms_lookup_handle unexpected error: actor=%s handle=%s",
                current_instance_id, handle,
            )
            return f"ERROR: {type(exc).__name__}"

    # -------------------------------------------------------------------------
    # Return
    # -------------------------------------------------------------------------

    kms_attach._full_doc_ = """Bind a secret-shaped env key on an MCP server — two mutually-exclusive modes share the entry point.

This tool is the SINGLE legitimate secret-shaped env-write surface for
the agent lane (the ``mcp_set_env`` companion tool rejects
secret-shaped names; this one carries the env-ref path that bypasses
that gate in a controlled way).

Args:
    server_id: The MCP server ID (UUID4). Resolved fresh from the DB
        at call time — name resolution is ``mcp_set_env``'s lane, not
        this tool's.
    env_key: The ``config.env[<key>]`` slot to populate.
    handle: LANE-1 mode — the ``KMS_HANDLE_<uuid>`` to bind. The
        marker ``__KMS_REF__<HANDLE>__`` is written into
        ``config.env[env_key]``; the binding is recorded in
        ``instance_metadata.bound_handles``. Use after ``kms_request``.
        **Restart-fragile** — KMS-Lite is in-memory and drains on
        daemon restart.
    env_source: LANE-2 mode — the name of a daemon-process env var
        (e.g. ``OPENAI_API_KEY``). The marker
        ``__KMS_ENV__<VARNAME>__`` is written into
        ``config.env[env_key]``; the binding is recorded in
        ``instance_metadata.env_refs``. **Restart-durable** — the
        resolver reads ``os.environ[<VARNAME>]`` fresh on every spawn,
        so the binding survives daemon restart as long as the env var
        is still set.

Mode arbitration:
    Exactly one of ``handle`` / ``env_source`` MUST be passed. Both
    or neither → ``ERROR: INVALID_ARGUMENT``.

Returns (success, LANE-1)::

    {
        "server_id": "<uuid>",
        "handle": "KMS_HANDLE_<uuid>",
        "env_key": "...",
        "fingerprint": "<sha256[:16]>",
        "marker": "__KMS_REF__KMS_HANDLE_<uuid>__"
    }

Returns (success, LANE-2)::

    {
        "server_id": "<uuid>",
        "env_key": "...",
        "env_source": "OPENAI_API_KEY",
        "marker": "__KMS_ENV__OPENAI_API_KEY__",
        "note": "LANE-2 env-ref attached. …"
    }

Values NEVER appear in any return (LANE-1 marker carries the handle,
not the plaintext; LANE-2 marker carries the var name, not the
value). Tool results land in LangGraph checkpoints (PB-F1 family).

Reads the row FRESH at call time (R1 invariant — ``config`` is
co-owned by configure-builtin, mcp_set_env, and this tool; a cached
row would clobber markers with stale plaintext).

Rejections:
    * ``ERROR: INVALID_ARGUMENT`` — both or neither of
      ``handle``/``env_source`` passed, OR ``env_source`` is not a
      valid env-var name (the name validator at
      ``kms_lite.build_env_marker``).
    * ``ERROR: ENV_VAR_NOT_FOUND`` — LANE-2 mode and ``env_source``
      names a var absent from ``os.environ``. The var name is echoed;
      the value (if it had been present) is never read, never
      returned, never logged. Eager check so the caller learns the
      misconfiguration at attach time, not at next-spawn.
    * ``ERROR: HANDLE_NOT_FOUND`` — LANE-1 mode and the handle is
      unknown to the KMS-Lite store (typically a post-restart probe
      where the day-1 in-memory store drained). Re-mint via
      ``kms_request``.
    * ``ERROR: SERVER_NOT_FOUND`` — server_id does not resolve.
    * ``ERROR: KMS_UNAVAILABLE`` — KMS key absent / invalid; only
      surfaced on LANE-1 path. LANE-2 does NOT need the KMS store to
      be initialised.
    * Other failures → ``ERROR: <exc-class-name>`` (N9 contract —
      never raises; never leaks values).

Co-ownership contract (load-bearing):

    ``mcp_servers.config`` is co-owned by ``configure-builtin`` (HTTP
    lane), ``mcp_set_env`` (non-secret agent lane), and this tool. All
    three MUST re-read fresh at call time and merge — a stale cached
    row would silently replace stored markers with plaintext from an
    older snapshot. The marker-clobber trap is documented at the
    ``mcp_set_env`` long doc — agent-lane writers must re-apply env
    (and re-attach handles / re-bind env-refs) after a reconfigure.
"""

    return [
        kms_request,
        kms_attach,
        kms_lookup_handle,
    ]


# ---------------------------------------------------------------------------
# mcp_set_env — MCP config.env writer for NON-SECRET env values
# (Stage 1 of the self-provisioning chain, feature/od-self-provisioning,
# 2026-10-02)
#
# Complement of ``kms_attach``: that tool owns the SECRET-shaped lane
# (writes ``__KMS_REF__<handle>__`` markers into ``config.env`` via the
# KMS-Lite store); this tool owns the NON-SECRET lane (``BYOK_BASE_URL``,
# ``BYOK_MODEL``, log levels, URLs, model names). The split is the
# leader policy decision: this tool MUST NOT accept plaintext for
# secret-shaped key names — it rejects them with an error that points
# the caller at ``kms_request`` / ``kms_attach``.
#
# Why a separate factory (vs. folding into ``create_kms_tools``):
#   * Same rationale as the infra-asset / KMS split above — different
#     concerns (non-secret env writes vs. secret-bearing handle
#     pointers), disjoint audit trails.
#   * Still registered under ``infra`` so ``tools.allow`` opt-in is
#     unchanged (the worker lane already carries ``infra``).
#
# CO-OWNERSHIP / R1 (load-bearing): ``mcp_servers.config`` is co-owned
# by ``configure-builtin`` (HTTP lane), ``kms_attach`` (marker lane) and
# this tool. Any writer MUST re-read the row fresh at call time and
# merge — never re-serialise a cached row object. A stale cached row
# would silently replace stored ``__KMS_REF__`` markers with plaintext
# from an older snapshot (the R1 invariant ``kms_attach`` documents).
#
# Known trap (documented, NOT fixed here): a later ``configure-builtin``
# run regenerates ``config`` from the builtin schema payload and can
# drop env keys written by this tool (and any KMS markers). Idempotency
# rails + install audit are the HTTP lane's protection; agent-lane
# writers must re-apply after a reconfigure. Callers see this caution
# in the tool's long doc.
# ---------------------------------------------------------------------------


# Secret-shape classification lives in ONE place now:
# ``daemon/services/env_key_policy.py`` (W2, reviewer council
# 2026-10-02) — the same conservative word list the routers
# ``redact_secrets`` helper applies on the read side. This module
# imports :func:`env_key_is_secret_shaped` / :func:`is_ascii_env_key`
# from there; ``BASE`` / ``HEADERS`` remain deliberately write-legal
# (a base URL is a non-secret value this tool is ALLOWED to write —
# see the policy module docstring for the write-vs-redact split).


def _invalidate_mcp_schema_cache(manager: Any, server_name: str) -> None:
    """Invalidate the MCP service's schema cache for ``server_name``.

    Tool-layer analog of ``daemon/routers/mcp_servers.py::
    _invalidate_mcp_schema_cache`` (same getattr guard, same no-op
    semantics): config mutations force schema re-discovery on the next
    instance preload. No-op when the manager has no MCP service
    attached (legacy test fixtures that mock the manager bare).
    """
    mcp_service = getattr(manager, "_mcp_service", None)
    if mcp_service is not None and hasattr(mcp_service, "invalidate_schema_cache"):
        mcp_service.invalidate_schema_cache(server_name)


def _refresh_pool_env(manager: Any, server_name: str, new_env: dict[str, str]) -> None:
    """Refresh the warmup-pool's stored env for ``server_name``.

    Paired half of the MCP warmup-pool config starvation fix
    (2026-10-02, defect pinned against
    ``InstanceManager._init_warmup_pool`` at ``daemon/manager.py``).
    The pre-fix code path invalidated ONLY the schema cache after an
    env write; the pool's ``_configs[server_name]`` snapshot was
    never refreshed, so every ``acquire()`` + ``_replenish`` cycle
    spawned a fresh subprocess from the BUILTIN defaults rather than
    the live row env. Live symptom: ``od_generate_design`` returned
    "BYOK not configured" while the row held all 4 BYOK values.

    This helper walks ``manager._mcp_service._warmup_pool`` (the same
    singleton the manager wired at boot) and calls
    :meth:`McpWarmupPool.update_server_env` to overlay ``new_env`` on
    the stored config. Behavior on the various edge cases matches
    ``_invalidate_mcp_schema_cache``:

      * No ``_mcp_service`` on the manager (legacy test fixtures that
        mock the manager bare) → no-op, no raise.
      * No warmup pool attached (manager init never reached
        ``_init_warmup_pool``) → no-op.
      * Server not in the pool (e.g. a non-builtin user-created
        server whose ``register_server`` was never called) → the pool
        method returns ``False``; we don't surface that as an error
        because non-pooled servers fall through to the cold-start
        path in ``McpService._McpSessionProviderImpl`` which reads
        the live row anyway (the row IS the source of truth there).
      * The pool method is the one that does the safe ``McpStdioConfig``
        replacement — see its docstring for the "no healthy
        connections killed" guarantee.

    Args:
        manager: The :class:`InstanceManager` instance (typed ``Any``
            to match the tool-factory call style + legacy mocks).
        server_name: The MCP server name (NOT the row id — the pool
            keys by definition name, which is the same key the row's
            ``name`` column carries).
        new_env: The full env dict to overlay on the pool's stored
            config. The pool's helper merges over any prior env so
            partial writes (single key) do not erase sibling keys.
    """
    mcp_service = getattr(manager, "_mcp_service", None)
    if mcp_service is None:
        return
    pool = getattr(mcp_service, "_warmup_pool", None)
    if pool is None:
        return
    # Lazy import — the warmup_pool module is loaded eagerly via the
    # manager's import block, but a defensive import keeps this helper
    # safe to call from any tool-factory context (including unit
    # tests that mock the manager without the full module graph).
    update = getattr(pool, "update_server_env", None)
    if not callable(update):
        return
    try:
        update(server_name, new_env)
    except Exception:
        # Pool refresh failure must NOT cascade into a tool result.
        # The DB row is the source of truth — cold-start paths and
        # the next ``_create_pooled_connection`` will still see the
        # right env IF the operator restarts the daemon (the boot
        # overlay in ``_init_warmup_pool`` runs then). Logged at
        # WARNING so the operator can see why the pool didn't
        # refresh.
        logger.warning(
            "[pool-refresh] update_server_env failed for %s; "
            "pool will see fresh env only on next daemon restart",
            server_name,
        )


def create_mcp_env_tools(
    manager: "InstanceManager",
    current_instance_id: str,
) -> list:
    """Create the MCP ``config.env`` write tool for NON-SECRET values.

    Args:
        manager: The :class:`InstanceManager` instance. Provides
            ``manager._mcp_server_repository`` (the write path — same
            repository ``kms_attach`` uses, so both lanes co-own the
            row through the same engine) and optionally
            ``manager._mcp_service`` (schema-cache invalidation).
        current_instance_id: The current instance ID — audit-log
            attribution only.

    Returns:
        A list containing exactly one tool: [``mcp_set_env``].
    """
    logger = logging.getLogger(__name__)
    caller_instance_id: str = current_instance_id or ""

    @register_tool_category("infra")
    @tool
    def mcp_set_env(
        server: str,
        env: dict[str, str],
    ) -> str:
        """Set non-secret env vars on an MCP server's config. Use tool_help("mcp_set_env") for details.

        Merges ``env`` (a dict of env-var name to value) into the named
        MCP server's ``config.env``, preserving every existing key —
        including ``__KMS_REF__``/``__KMS_ENV__`` markers written by
        ``kms_attach``. Rejects secret-shaped key names (the shared
        word list at ``daemon/services/env_key_policy.py``: KEY /
        TOKEN / SECRET / PASSWORD / CREDENTIAL / PRIVATE / PWD /
        AUTH), non-ASCII key names, and marker-shaped VALUES (markers
        are ``kms_attach``-only) — bind secrets via ``kms_request`` +
        ``kms_attach`` instead. Open sessions keep the old env until
        they reconnect.
        """
        try:
            # ── Input validation (before any DB touch) ──────────────
            if not isinstance(server, str) or not server.strip():
                return "ERROR: INVALID_ARGUMENT: 'server' must be a non-empty server name (or server id)."
            if not isinstance(env, dict) or not env:
                return (
                    "ERROR: INVALID_ARGUMENT: 'env' must be a non-empty dict of "
                    "{env_var_name: value} — e.g. {\"BYOK_BASE_URL\": \"https://...\"}."
                )

            bad_types = sorted(
                k for k, v in env.items()
                if not isinstance(k, str) or not isinstance(v, str)
            )
            if bad_types:
                return (
                    "ERROR: INVALID_ARGUMENT: 'env' entries must be string→string. "
                    f"Rejected key(s): {', '.join(bad_types[:10])}."
                )

            non_ascii_keys = sorted(k for k in env if not is_ascii_env_key(k))
            if non_ascii_keys:
                return (
                    "ERROR: INVALID_ENV_KEY: env key(s) "
                    f"{', '.join(non_ascii_keys[:10])} must be ASCII "
                    "identifiers ([A-Za-z_][A-Za-z0-9_]*). Non-ASCII or "
                    "homoglyph key names cannot be classified by the "
                    "secret-shape gate or redacted reliably — use plain "
                    "POSIX-style variable names."
                )

            secret_keys = sorted(k for k in env if env_key_is_secret_shaped(k))
            if secret_keys:
                return (
                    "ERROR: SECRET_SHAPED_KEY: env key(s) "
                    f"{', '.join(secret_keys)} look secret-shaped (contain "
                    "KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL/PRIVATE/PWD/AUTH — "
                    "the shared policy at daemon/services/env_key_policy.py). "
                    "NEVER pass plaintext credentials through mcp_set_env. "
                    "Mint a handle via kms_request(service=..., reason=...), "
                    "then bind it via kms_attach(server_id=<id>, "
                    "handle=<handle>, env_key=<key>); for an env-ref bind "
                    "use kms_attach(server_id=<id>, env_key=<key>, "
                    "env_source=<VAR>). mcp_set_env's success result "
                    "includes the server_id for exactly this chained flow."
                )

            marker_valued = sorted(
                k for k, v in env.items()
                if isinstance(v, str) and is_marker(v)
            )
            if marker_valued:
                return (
                    "ERROR: MARKER_VALUE_FORBIDDEN: env value(s) for "
                    f"key(s) {', '.join(marker_valued[:10])} are KMS "
                    "markers (__KMS_REF__<handle>__ / __KMS_ENV__<var>__). "
                    "Markers are written ONLY by kms_attach — mcp_set_env "
                    "writes non-secret plaintext config. Passing a marker "
                    "here would bypass the attach lane's binding/audit "
                    "rails (the spawn-time resolver's fail-closed presence "
                    "check remains the backstop)."
                )

            # ── R1: resolve + read the row FRESH at call time ───────
            # Agents know the server NAME; kms_attach historically
            # takes the server_id. Resolve by name first, fall back to
            # id so a caller that only has the id (e.g. from a previous
            # mcp_set_env result) still lands.
            repo = manager._mcp_server_repository
            resolved = repo.get_mcp_server_by_name(server) if hasattr(
                repo, "get_mcp_server_by_name"
            ) else None
            if resolved is None:
                resolved = repo.get_mcp_server(server)
            if resolved is None:
                return (
                    f"ERROR: SERVER_NOT_FOUND: no MCP server matches "
                    f"name-or-id={server!r}. Check the server name (e.g. "
                    "'opendesign') and retry."
                )

            server_id = resolved.id
            server_name = resolved.name

            existing_config = dict(resolved.config or {})
            env_block = dict(existing_config.get("env") or {})
            env_block.update(env)
            existing_config["env"] = env_block

            # instance_metadata is KMS-owned (bound_handles) — this
            # tool deliberately does NOT touch it; update_mcp_server
            # leaves absent fields unmodified.
            updated = repo.update_mcp_server(server_id, config=existing_config)
            if updated is None:
                return f"ERROR: SERVER_NOT_FOUND: server_id={server_id!r} vanished mid-update; retry."

            # ── Schema cache invalidation (routers analog) ──────────
            _invalidate_mcp_schema_cache(manager, server_name)

            # ── Warmup-pool config refresh (mcp warmup-pool config
            # starvation fix, 2026-10-02) — the pool's ``_configs``
            # snapshot must reflect the new env so the next
            # ``acquire()`` + ``_replenish`` cycle spawns a stdio
            # subprocess with the new values. Without this, a pooled
            # opendesign connection keeps the env it was spawned
            # with (definition defaults + pre-write row state) and
            # every new connection also does — the BYOK keys never
            # reach the subprocess. Read the row FRESH here (NOT from
            # the stale ``updated`` object whose ``config`` is a
            # Python dict we already mutated) so the overlay uses the
            # exact stored env.
            fresh_row = repo.get_mcp_server(server_id) if hasattr(repo, "get_mcp_server") else None
            fresh_env = ((fresh_row.config or {}).get("env") if fresh_row is not None else None) or {}
            _refresh_pool_env(manager, server_name, fresh_env)

            # Audit: key NAMES + shape only — NEVER values.
            # M2 (reviewer fold-in): the preserved-marker count covers
            # BOTH marker shapes (LANE-1 ``__KMS_REF__`` AND LANE-2
            # ``__KMS_ENV__``) via the resolver's ``is_marker``.
            logger.info(
                "[mcp_set_env] actor=%s server=%s keys=%d marker_keys_preserved=%d",
                caller_instance_id,
                server_name,
                len(env),
                sum(
                    1 for v in env_block.values()
                    if isinstance(v, str) and is_marker(v)
                ),
            )

            return json.dumps(
                {
                    "ok": True,
                    "server_id": server_id,
                    "server_name": server_name,
                    "env_keys_set": sorted(env.keys()),
                    "note": (
                        "Merged into config.env; existing keys and "
                        "__KMS_REF__ markers preserved. Open sessions keep "
                        "the OLD env until they reconnect — call "
                        f"close_server('{server_name}') (or spawn a new "
                        "instance) to pick up the new values. Caution: a "
                        "later configure-builtin run regenerates config and "
                        "may drop these keys — re-apply after reconfiguring."
                    ),
                },
                indent=2,
            )
        except Exception as exc:  # noqa: BLE001 — N9 contract (infra family)
            logger.exception(
                "mcp_set_env unexpected error: actor=%s server=%s",
                caller_instance_id,
                server if isinstance(server, str) else "<non-string>",
            )
            return f"ERROR: {type(exc).__name__}"

    mcp_set_env._full_doc_ = """Merge non-secret env vars into an MCP server's stored ``config.env``.

The NON-SECRET half of the env-write surface (``kms_attach`` is the
secret half). Purpose-built for the OpenDesign self-provisioning
chain: after ``ens_env_read`` captures the daemon's LLM connection
values, this tool writes ``BYOK_BASE_URL`` and ``BYOK_MODEL`` into the
``opendesign`` server's ``config.env``. ``BYOK_API_KEY`` must NOT go
through this tool — see the secret-shaped rejection below.

Args:
    server: The MCP server NAME (e.g. ``"opendesign"``). As a
        convenience the server ID is also accepted — name is resolved
        first, id is the fallback (``kms_attach`` only takes the id;
        this tool's success result echoes ``server_id`` so a chained
        ``kms_attach`` call needs no second lookup).
    env: A dict of ``{env_var_name: value}`` to MERGE into
        ``config.env``. Multiple keys in one call are applied in a
        single read-fresh→merge→write cycle (one round trip, one
        window). All keys and values must be strings.

Returns (success):
    JSON object::

        {
          "ok": true,
          "server_id": "<uuid>",          // for the chained kms_attach flow
          "server_name": "opendesign",
          "env_keys_set": ["BYOK_BASE_URL", "BYOK_MODEL"],
          "note": "…reconnect semantics + reconfigure caution…"
        }

    Values are NEVER echoed back — the caller just supplied them, and
    tool results land in LangGraph checkpoints (PB-F1 family).
    ``BYOK_BASE_URL`` additionally reads back ``[REDACTED]`` through
    the HTTP API (presentation-layer redaction) — that is EXPECTED,
    not a write failure.

Semantics:
    * MERGE, not replace: every existing ``config.env`` key survives,
      including ``__KMS_REF__<handle>__`` markers written by
      ``kms_attach``. The row is re-read FRESH at call time (R1
      invariant — ``config`` is co-owned by configure-builtin,
      kms_attach, and this tool; a cached row would clobber markers
      with stale plaintext).
    * ``instance_metadata`` (``bound_handles``, install snapshots) is
      KMS/HTTP-lane-owned and deliberately untouched.
    * Schema cache for the server is invalidated — new instances
      preload the updated config on their next MCP session creation.
    * OPEN SESSIONS: a running MCP session captured its env at spawn
      time (``resolve_env`` runs once per session creation). New env
      values apply only after ``close_server(server_name)`` or a fresh
      instance spawn. The KMS marker lane resolves at the same seam.

Rejections:
    * Secret-shaped KEY NAMES — any key whose name contains one of
      the shared policy words ``KEY``, ``TOKEN``, ``SECRET``,
      ``PASSWORD``, ``CREDENTIAL``, ``PRIVATE``, ``PWD``, or ``AUTH``
      (case-insensitive; ``daemon/services/env_key_policy.py`` — W2
      single-sourced with the read-side ``redact_secrets``) is
      REFUSED with ``ERROR: SECRET_SHAPED_KEY`` pointing at
      ``kms_request`` → ``kms_attach``. This is the leader policy:
      plaintext credentials must never transit this tool. (``BASE``
      is read-side redaction only — ``BYOK_BASE_URL`` is writable
      here.)
    * Non-ASCII env KEY NAMES — keys must be ASCII identifiers
      (``[A-Za-z_][A-Za-z0-9_]*``); anything else is REFUSED with
      ``ERROR: INVALID_ENV_KEY``. Homoglyph/unicode names could dodge
      substring classification while still landing in ``config.env``
      (W2).
    * Marker-shaped VALUES — a value matching a full KMS marker
      (``__KMS_REF__<handle>__`` / ``__KMS_ENV__<var>__``) is REFUSED
      with ``ERROR: MARKER_VALUE_FORBIDDEN``. Markers are written
      ONLY by ``kms_attach``; a marker routed through this tool would
      bypass the attach lane's binding/audit rails (M1 — the
      resolver's fail-closed presence check remains the backstop).
    * Unknown server → ``ERROR: SERVER_NOT_FOUND`` (name-or-id echoed).
    * Empty/ill-typed ``env`` → ``ERROR: INVALID_ARGUMENT``.

Caution (documented trap, by design): a later ``configure-builtin``
run for the same builtin regenerates ``config`` from its schema
payload and can DROP env keys written by this tool — including KMS
markers. The HTTP lane's idempotency rails + install audit are
load-bearing and are NOT bypassed here; agent-lane writers must
re-apply env (and re-attach handles) after any reconfigure.

Errors never raise (N9 contract): every failure returns an
``ERROR: ...`` string. Unexpected exceptions surface the exception
CLASS NAME only — values and messages never ride into the result.
"""

    return [mcp_set_env]
