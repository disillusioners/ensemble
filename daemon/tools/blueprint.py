"""Project blueprint management tools.

Agent-facing tools for searching, reading, and (restricted) writing project
blueprints. Follows the exact factory pattern of :mod:`daemon.tools.knowledge_tools`:

* ``create_blueprint_tools(manager, current_instance_id, agent_id)`` factory
  returns a list of LangChain ``@tool``-decorated functions.
* ``_get_project_id()`` closure auto-injects the project from instance context.
* Read tools (search / get / list) are unrestricted; write tools (create /
  update) are gated by a runtime ``agent_id`` check — only the ``blueprinter``
  agent may pass.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from ._tool_registry import register_tool_category

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Blueprint"
CATEGORY_DOC = """Project blueprint management tools.

blueprint_search() searches project blueprints using the matching engine.
blueprint_get() retrieves a specific blueprint by ID or slug.
blueprint_list() lists all blueprints for the current project.
blueprint_create() creates a new blueprint (restricted to blueprinter agent).
blueprint_update() updates a blueprint (restricted to blueprinter agent).
"""


def create_blueprint_tools(
    manager: "InstanceManager",
    current_instance_id: str,
    agent_id: str = "",
) -> list:
    """Create blueprint management tools with injected manager reference.

    Args:
        manager: The InstanceManager instance to use for operations.
        current_instance_id: The ID of the current instance.
        agent_id: The ``agent_id`` of the calling instance. Captured in the
            closure so the write tools (create/update) can enforce the
            ``blueprinter``-only authorization check. Defaults to ``""``
            (unauthorized — read tools still work, write tools are blocked).

    Returns:
        List of tool functions:
        ``[blueprint_search, blueprint_get, blueprint_list, blueprint_create, blueprint_update]``.
    """

    def _get_project_id() -> str | None:
        """Auto-inject project_id from instance context."""
        try:
            instance_meta = manager._instance_repository.get(current_instance_id)
            if instance_meta and instance_meta.project_id:
                return instance_meta.project_id
        except Exception:
            pass
        return None

    def _is_writer_authorized(caller_agent_id: str) -> bool:
        """Only the 'blueprinter' agent can create/update blueprints."""
        return caller_agent_id == "blueprinter"

    # ------------------------------------------------------------------
    # 1. blueprint_search — the ONLY tool that uses the matcher
    # ------------------------------------------------------------------

    @register_tool_category("blueprint")
    @tool
    async def blueprint_search(
        query: str,
        project_id: str | None = None,
    ) -> str:
        """Search project blueprints. Uses the matching engine (BM25 + vector fusion).

        Returns matched blueprints ranked by relevance. The core blueprint is always included.

        Args:
            query: The search query text.
            project_id: Optional project ID. Auto-detected from context if not provided.

        Returns:
            Formatted string with matched blueprint names, kinds, and scores.
        """
        pid = project_id or _get_project_id()
        if not pid:
            return "Error: project_id not available. Ensure the agent instance has a project context set."

        matcher = manager._blueprint_matcher
        if matcher is None:
            return "Blueprint search is not available (no embedding service configured)"

        try:
            matched = await matcher.match(
                project_id=pid, query=query
            )
        except Exception as e:
            logger.warning("blueprint_search matcher failed: %s", e, exc_info=True)
            return f"Error: blueprint search failed: {e}"

        if not matched:
            return "No blueprints found."

        lines = [f"Found {len(matched)} blueprint(s):"]
        for bp in matched:
            lines.append(
                f"- {bp.name} (kind={bp.kind}, version={bp.version}, "
                f"score={bp.score:.3f})"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 2. blueprint_get — retrieve by ID or slug
    # ------------------------------------------------------------------

    @register_tool_category("blueprint")
    @tool
    async def blueprint_get(
        blueprint_id: str = None,
        slug: str = None,
        project_id: str = None,
    ) -> str:
        """Get a specific blueprint by ID or slug.

        Pass either blueprint_id or slug. If slug is provided, project_id is required
        (slug is unique only within a project).

        Args:
            blueprint_id: The blueprint's primary key.
            slug: The blueprint's slug (requires project_id).
            project_id: Optional project ID. Auto-detected from context if not provided.

        Returns:
            Formatted blueprint content, or an error / not-found message.
        """
        if blueprint_id is None and slug is None:
            return "Error: provide either blueprint_id or slug."

        repo = manager._blueprint_repo

        if blueprint_id is not None:
            try:
                bp = await asyncio.to_thread(repo.get_by_id, blueprint_id)
            except Exception as e:
                logger.warning("blueprint_get by id failed: %s", e, exc_info=True)
                return f"Error: failed to fetch blueprint: {e}"
            # Ownership check: blueprint must belong to the caller's project
            pid = project_id or _get_project_id()
            if bp is None or (pid is not None and bp.project_id != pid):
                return "Blueprint not found."
        else:
            # slug lookup — needs project_id
            pid = project_id or _get_project_id()
            if not pid:
                return (
                    "Error: project_id is required when looking up by slug. "
                    "Ensure the agent instance has a project context set."
                )
            try:
                bp = await asyncio.to_thread(repo.get_by_slug, pid, slug)
            except Exception as e:
                logger.warning("blueprint_get by slug failed: %s", e, exc_info=True)
                return f"Error: failed to fetch blueprint: {e}"

        if bp is None:
            return "Blueprint not found."

        lines = [
            f"Blueprint: {bp.name}",
            f"  ID: {bp.id}",
            f"  Kind: {bp.kind}",
            f"  Version: {bp.version}",
            f"  Slug: {bp.slug}",
        ]
        tags = getattr(bp, "tags", None) or []
        if tags:
            lines.append(f"  Tags: {tags}")
        file_refs = getattr(bp, "file_refs", None) or []
        if file_refs:
            lines.append(f"  File refs: {file_refs}")
        lines.append("")
        lines.append(bp.content or "")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 3. blueprint_list — list all blueprints for the project
    # ------------------------------------------------------------------

    @register_tool_category("blueprint")
    @tool
    async def blueprint_list(
        kind: str = None,
        project_id: str = None,
    ) -> str:
        """List all blueprints for the current project.

        Optional kind filter: 'core' or 'area'.

        Args:
            kind: Optional kind filter ('core' or 'area').
            project_id: Optional project ID. Auto-detected from context if not provided.

        Returns:
            Formatted list with name, slug, kind, version for each blueprint.
        """
        pid = project_id or _get_project_id()
        if not pid:
            return "Error: project_id not available. Ensure the agent instance has a project context set."

        repo = manager._blueprint_repo
        try:
            blueprints = await asyncio.to_thread(
                repo.list_by_project, pid, kind=kind
            )
        except Exception as e:
            logger.warning("blueprint_list failed: %s", e, exc_info=True)
            return f"Error: failed to list blueprints: {e}"

        if not blueprints:
            return "No blueprints found for this project."

        lines = [f"Found {len(blueprints)} blueprint(s):"]
        for bp in blueprints:
            lines.append(
                f"- {bp.name} (slug={bp.slug}, kind={bp.kind}, "
                f"version={bp.version})"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 4. blueprint_create — RESTRICTED to blueprinter agent
    # ------------------------------------------------------------------

    @register_tool_category("blueprint")
    @tool
    async def blueprint_create(
        slug: str,
        name: str,
        kind: str,
        content: str,
        project_id: str = None,
        tags: list = None,
        file_refs: list = None,
        trigger_queries: list = None,
        reason: str = None,
    ) -> str:
        """Create a new blueprint. RESTRICTED to the blueprinter agent.

        Only the 'blueprinter' agent can create blueprints. Routes
        through the canonical write service so trigger embeddings,
        rate-limiting, and revision capture all run.

        Args:
            slug: URL-safe unique slug within the project.
            name: Human-readable blueprint name.
            kind: Blueprint kind ('core' or 'area').
            content: The blueprint markdown content.
            project_id: Optional project ID. Auto-detected from context if not provided.
            tags: Optional list of tag dicts.
            file_refs: Optional list of file reference strings.
            trigger_queries: Optional list of trigger query strings to embed.
                None = no triggers stored.
            reason: Optional reason for the revision log.

        Returns:
            Success message with the new blueprint ID, or an authorization error.
        """
        if not _is_writer_authorized(agent_id):
            return "ERROR: Only the blueprinter agent can create blueprints."

        pid = project_id or _get_project_id()
        if not pid:
            return "Error: project_id not available. Ensure the agent instance has a project context set."

        service = manager.get_blueprint_write_service(pid)
        try:
            bp = await service.create_blueprint(
                slug=slug,
                name=name,
                kind=kind,
                content=content,
                tags=tags or [],
                file_refs=file_refs or [],
                trigger_queries=trigger_queries,
                reason=reason,
            )
        except Exception as e:
            logger.warning("blueprint_create failed: %s", e, exc_info=True)
            return f"Error: failed to create blueprint: {e}"

        return f"Blueprint created successfully. ID: {bp.id}"

    # ------------------------------------------------------------------
    # 5. blueprint_update — RESTRICTED to blueprinter agent
    # ------------------------------------------------------------------

    @register_tool_category("blueprint")
    @tool
    async def blueprint_update(
        blueprint_id: str,
        content: str = None,
        name: str = None,
        project_id: str = None,
        tags: list = None,
        file_refs: list = None,
        trigger_queries: list = None,
        reason: str = None,
    ) -> str:
        """Update a blueprint. RESTRICTED to the blueprinter agent.

        Only the 'blueprinter' agent can update blueprints. Routes
        through the canonical write service.

        Args:
            blueprint_id: The blueprint's primary key.
            content: Optional new content. Omitted fields are left unchanged.
            name: Optional new name.
            project_id: Optional project ID (unused by update but accepted for API symmetry).
            tags: Optional new tags list.
            file_refs: Optional new file references list.
            trigger_queries: Optional trigger queries. None = leave
                unchanged; [] = clear all triggers; [a,b] = replace.
            reason: Optional reason for the revision log.

        Returns:
            Success message, or an authorization / error message.
        """
        if not _is_writer_authorized(agent_id):
            return "ERROR: Only the blueprinter agent can update blueprints."

        # Build kwargs dict from non-None values
        kwargs: dict = {}
        if content is not None:
            kwargs["content"] = content
        if name is not None:
            kwargs["name"] = name
        if tags is not None:
            kwargs["tags"] = tags
        if file_refs is not None:
            kwargs["file_refs"] = file_refs
        # trigger_queries: pass through as-is (None OR [] OR list).
        if trigger_queries is not None:
            kwargs["trigger_queries"] = trigger_queries
        if reason is not None:
            kwargs["reason"] = reason

        if not kwargs:
            return "Error: no fields to update. Provide content, name, tags, file_refs, trigger_queries, or reason."

        repo = manager._blueprint_repo
        # Ownership check: verify the blueprint exists and belongs to
        # the caller's project before updating.
        pid = project_id or _get_project_id()
        try:
            existing = await asyncio.to_thread(repo.get_by_id, blueprint_id)
        except Exception as e:
            logger.warning("blueprint_update fetch failed: %s", e, exc_info=True)
            return f"Error: failed to verify blueprint: {e}"
        if existing is None or (pid is not None and existing.project_id != pid):
            return "Blueprint not found."

        service = manager.get_blueprint_write_service(pid)
        try:
            bp = await service.update_blueprint(blueprint_id, **kwargs)
        except Exception as e:
            logger.warning("blueprint_update failed: %s", e, exc_info=True)
            return f"Error: failed to update blueprint: {e}"

        if bp is None:
            return f"Error: blueprint not found (id={blueprint_id})."

        return f"Blueprint updated successfully. ID: {bp.id}"

    return [blueprint_search, blueprint_get, blueprint_list, blueprint_create, blueprint_update]
