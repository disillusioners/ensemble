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
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

from ._tool_registry import register_tool_category

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
                logger.warning(
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
            logger.warning(
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
            logger.warning(
                "infra_asset_create failed for project=%s type=%s name=%s: %s",
                project_id, type, name, exc,
            )
            return (
                f"ERROR: Failed to create infra asset "
                f"({type(exc).__name__})."
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
            logger.warning(
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
            logger.warning(
                "infra_asset_list failed: project=%s: %s", project_id, exc
            )
            return f"ERROR: Failed to list infra assets ({type(exc).__name__})."

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
            logger.warning(
                "infra_asset_search failed: project=%s query=%s: %s",
                project_id, query, exc,
            )
            return f"ERROR: Failed to search infra assets ({type(exc).__name__})."

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
            logger.warning(
                "infra_asset_update constraint error: asset_id=%s: %s",
                asset_id, exc,
            )
            return f"ERROR: Update violates unique (project_id, type, name) constraint: {msg}"
        except AttributeError as exc:
            # Repo raises AttributeError for unknown column names.
            logger.warning(
                "infra_asset_update invalid field: asset_id=%s: %s",
                asset_id, exc,
            )
            return f"ERROR: Invalid update field: {exc}"
        except Exception as exc:
            logger.warning(
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
            logger.warning(
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
            logger.warning(
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
            logger.warning("infra_type_list failed: %s", exc)
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
            logger.warning(
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
