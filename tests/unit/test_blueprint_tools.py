"""Unit tests for Project Blueprint tool-layer guards.

Covers the review fixes applied to :mod:`daemon.tools.blueprint`:

* **C5** — ``blueprint_search`` None-guard: when the manager has no
  embedding service (``_blueprint_matcher is None``) the tool returns a
  clear "not available" message instead of raising ``AttributeError``.
* **W1** — ``blueprint_get`` cross-project ownership check: a blueprint
  fetched by ID that belongs to a different project is reported as
  "not found" (no information leak).
* **W2** — ``blueprint_update`` cross-project ownership check: an update
  for a blueprint belonging to another project is denied before the
  ``repo.update`` call is made.

The tools are produced by ``create_blueprint_tools(manager,
current_instance_id, agent_id)`` and returned as a list of LangChain
``@tool``-decorated async functions. We drive each async tool via
``asyncio.run`` (matching the convention in
``test_blueprint_injection.py``) rather than depending on
``pytest-asyncio``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.blueprint_write_service import (
    BlueprintNotFoundError,
    BlueprintWriteService,
)
from daemon.tools.blueprint import create_blueprint_tools


# ─── Test doubles ────────────────────────────────────────────────────────────


class _FakeBlueprint:
    """Minimal stand-in for a blueprint ORM row."""

    def __init__(
        self,
        id: str,
        project_id: str,
        name: str = "test",
        content: str = "c",
        kind: str = "area",
    ) -> None:
        self.id = id
        self.project_id = project_id
        self.name = name
        self.content = content
        self.kind = kind
        self.version = 1
        self.slug = "test"
        self.file_refs: list = []
        self.tags: list = []
        self.trigger_queries: list = []
        # ``score`` is populated by the matching engine on result rows;
        # the search tool formats it with ``:.3f``.
        self.score: float = 1.0


class _FakeRepo:
    """Minimal in-memory blueprint repo for tool tests."""

    def __init__(self) -> None:
        self._store: dict[str, _FakeBlueprint] = {}

    def get_by_id(self, bp_id: str) -> _FakeBlueprint | None:
        return self._store.get(bp_id)

    def update(self, bp_id: str, reason: str | None = None, **fields: Any) -> _FakeBlueprint | None:
        bp = self._store.get(bp_id)
        if bp is None:
            return None
        for k, v in fields.items():
            setattr(bp, k, v)
        return bp

    def create(self, **fields: Any) -> _FakeBlueprint:
        bp = _FakeBlueprint(
            id=fields.get("id", "bp-new"),
            project_id=fields["project_id"],
            name=fields.get("name", "new"),
            content=fields.get("content", ""),
            kind=fields.get("kind", "area"),
        )
        self._store[bp.id] = bp
        return bp


class _FakeWriteService:
    """Lightweight async stand-in for BlueprintWriteService.

    Delegates create/update/disable to the _FakeRepo so the tool tests
    exercise the real service→repo call path without an embedding API.
    """

    def __init__(self, repo: _FakeRepo, project_id: str) -> None:
        self._repo = repo
        self.project_id = project_id

    async def create_blueprint(self, **kwargs: Any) -> _FakeBlueprint:
        kwargs.setdefault("project_id", self.project_id)
        return self._repo.create(**kwargs)

    async def update_blueprint(self, bp_id: str, reason: str | None = None, **kwargs: Any) -> _FakeBlueprint:
        result = self._repo.update(bp_id, reason=reason, **kwargs)
        if result is None:
            raise BlueprintNotFoundError(bp_id)
        return result

    async def disable_blueprint(self, bp_id: str, reason: str | None = None) -> bool:
        bp = self._repo.get_by_id(bp_id)
        if bp is None:
            raise BlueprintNotFoundError(bp_id)
        return True


def _make_manager(
    repo: _FakeRepo | None = None,
    matcher: Any = None,
    instance_project_id: str = "proj-A",
) -> MagicMock:
    """Build a MagicMock manager with the blueprint attributes the tools touch."""
    m = MagicMock()
    repo = repo or _FakeRepo()
    m._blueprint_repo = repo
    m._blueprint_matcher = matcher
    # ``get_blueprint_write_service`` returns a _FakeWriteService bound to
    # the same repo so create/update go through the service→repo path.
    m.get_blueprint_write_service = lambda pid: _FakeWriteService(repo, pid)
    # ``_get_project_id()`` reads the instance repo; mock it to return an
    # instance carrying the requested project_id.
    inst = MagicMock()
    inst.project_id = instance_project_id
    m._instance_repository.get = MagicMock(return_value=inst)
    return m


def _run(coro: Any) -> Any:
    """Drive an awaitable via a fresh event loop (no pytest-asyncio dep)."""
    return asyncio.run(coro)


def _build_tools(manager: MagicMock, agent_id: str = "blueprinter") -> list:
    """Create the 5 blueprint tools from a manager."""
    return create_blueprint_tools(manager, "inst-test", agent_id)


# ─── C5: blueprint_search None-guard ─────────────────────────────────────────


class TestBlueprintSearchMatcherNoneGuard:
    """``blueprint_search`` must degrade gracefully when the matcher is None."""

    def test_blueprint_search_matcher_none(self) -> None:
        """No embedding service → 'not available' message, no exception."""
        manager = _make_manager(matcher=None)
        tools = _build_tools(manager)
        blueprint_search = tools[0]

        result = _run(blueprint_search.ainvoke({"query": "anything"}))
        assert isinstance(result, str)
        assert "not available" in result


# ─── C5: blueprint_search with a working matcher ─────────────────────────────


class TestBlueprintSearchMatcherPresent:
    """``blueprint_search`` returns matched blueprint names when matcher works."""

    def test_blueprint_search_matcher_present(self) -> None:
        bp = _FakeBlueprint(id="bp-1", project_id="proj-A", name="Core Arches")
        matcher = MagicMock()
        matcher.match = AsyncMock(return_value=[bp])
        manager = _make_manager(matcher=matcher)
        tools = _build_tools(manager)
        blueprint_search = tools[0]

        result = _run(blueprint_search.ainvoke({"query": "arches"}))
        assert isinstance(result, str)
        assert "Core Arches" in result


# ─── W1: blueprint_get ownership check ───────────────────────────────────────


class TestBlueprintGetOwnership:
    """``blueprint_get`` by ID must enforce project ownership."""

    def test_blueprint_get_cross_project_denied(self) -> None:
        """Blueprint owned by proj-B, caller in proj-A → 'not found'."""
        repo = _FakeRepo()
        bp = _FakeBlueprint(id="bp-x", project_id="proj-B", name="Secret")
        repo._store["bp-x"] = bp

        manager = _make_manager(repo=repo, instance_project_id="proj-A")
        tools = _build_tools(manager)
        blueprint_get = tools[1]

        result = _run(blueprint_get.ainvoke({"blueprint_id": "bp-x"}))
        assert result == "Blueprint not found."

    def test_blueprint_get_same_project_ok(self) -> None:
        """Blueprint owned by proj-A, caller in proj-A → content returned."""
        repo = _FakeRepo()
        bp = _FakeBlueprint(
            id="bp-ok", project_id="proj-A", name="My Blueprint", content="hello"
        )
        repo._store["bp-ok"] = bp

        manager = _make_manager(repo=repo, instance_project_id="proj-A")
        tools = _build_tools(manager)
        blueprint_get = tools[1]

        result = _run(blueprint_get.ainvoke({"blueprint_id": "bp-ok"}))
        assert "My Blueprint" in result


# ─── W2: blueprint_update ownership check ────────────────────────────────────


class TestBlueprintUpdateOwnership:
    """``blueprint_update`` must enforce project ownership before updating."""

    def test_blueprint_update_cross_project_denied(self) -> None:
        """Update for a blueprint owned by proj-B, caller in proj-A → denied."""
        repo = _FakeRepo()
        bp = _FakeBlueprint(id="bp-y", project_id="proj-B", name="Keep")
        repo._store["bp-y"] = bp

        manager = _make_manager(repo=repo, instance_project_id="proj-A")
        tools = _build_tools(manager, agent_id="blueprinter")
        blueprint_update = tools[4]

        result = _run(
            blueprint_update.ainvoke(
                {"blueprint_id": "bp-y", "content": "new content"}
            )
        )
        assert result == "Blueprint not found."
        # The blueprint content must be unchanged (update never ran).
        assert bp.content == "c"

    def test_blueprint_update_same_project_ok(self) -> None:
        """Update for a blueprint owned by proj-A, caller in proj-A → success."""
        repo = _FakeRepo()
        bp = _FakeBlueprint(id="bp-z", project_id="proj-A", name="Editable")
        repo._store["bp-z"] = bp

        manager = _make_manager(repo=repo, instance_project_id="proj-A")
        tools = _build_tools(manager, agent_id="blueprinter")
        blueprint_update = tools[4]

        result = _run(
            blueprint_update.ainvoke(
                {"blueprint_id": "bp-z", "content": "fresh content"}
            )
        )
        assert "updated successfully" in result.lower()
        # The update must have actually mutated the stored blueprint.
        assert bp.content == "fresh content"
