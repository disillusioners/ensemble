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


class _FakePendingUpdate:
    """Minimal stand-in for a BlueprintPendingUpdate row."""

    def __init__(
        self,
        id: str,
        project_id: str,
        source_type: str = "experience",
        source_payload: dict | None = None,
    ) -> None:
        self.id = id
        self.project_id = project_id
        self.source_type = source_type
        self.source_payload = source_payload or {}


class _FakePendingRepo:
    """In-memory stand-in for BlueprintPendingRepository.

    Implements the methods the new agent-facing tools touch: enqueue,
    claim_batch, acknowledge_batch, get_pending_count.
    """

    def __init__(self) -> None:
        self._records: list[_FakePendingUpdate] = []
        self._status: dict[str, str] = {}  # id -> status
        self._run_token: dict[str, str] = {}  # id -> run_token
        self._counter = 0

    def enqueue(
        self,
        project_id: str,
        source_type: str,
        source_payload: dict,
    ) -> _FakePendingUpdate:
        self._counter += 1
        rec = _FakePendingUpdate(
            id=f"pending-{self._counter}",
            project_id=project_id,
            source_type=source_type,
            source_payload=source_payload,
        )
        self._records.append(rec)
        self._status[rec.id] = "available"
        return rec

    def claim_batch(
        self,
        project_id: str,
        batch_size: int = 50,
        run_token: str = "",
    ) -> list[_FakePendingUpdate]:
        claimed: list[_FakePendingUpdate] = []
        for rec in self._records:
            if len(claimed) >= batch_size:
                break
            if rec.project_id != project_id:
                continue
            if self._status.get(rec.id) in ("available", "retryable"):
                self._status[rec.id] = "claimed"
                self._run_token[rec.id] = run_token
                claimed.append(rec)
        return claimed

    def acknowledge_batch(
        self,
        run_token: str,
        record_ids: list[str] | None = None,
    ) -> int:
        count = 0
        for rec in self._records:
            if self._status.get(rec.id) != "claimed":
                continue
            if self._run_token.get(rec.id) != run_token:
                continue
            if record_ids is not None and rec.id not in record_ids:
                continue
            self._status[rec.id] = "applied"
            count += 1
        return count

    def get_pending_count(self, project_id: str) -> int:
        return sum(
            1
            for rec in self._records
            if rec.project_id == project_id
            and self._status.get(rec.id) in ("available", "retryable")
        )


def _make_manager(
    repo: _FakeRepo | None = None,
    matcher: Any = None,
    instance_project_id: str = "proj-A",
    pending_repo: _FakePendingRepo | None = None,
    coordinator: Any = None,
) -> MagicMock:
    """Build a MagicMock manager with the blueprint attributes the tools touch."""
    m = MagicMock()
    repo = repo or _FakeRepo()
    m._blueprint_repo = repo
    m._blueprint_matcher = matcher
    # ``get_blueprint_write_service`` returns a _FakeWriteService bound to
    # the same repo so create/update go through the service→repo path.
    m.get_blueprint_write_service = lambda pid: _FakeWriteService(repo, pid)
    # ``_blueprint_pending_repo`` backs the claim/ack/count tools. ``None``
    # by default so the "not available" guard path is exercised.
    m._blueprint_pending_repo = pending_repo
    m._blueprint_trigger_coordinator = coordinator
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
    """Create the 10 blueprint tools from a manager."""
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


# ─── Tool count sanity (return list grew from 9 → 10) ───────────────────────


class TestBlueprintToolCount:
    """The factory must return exactly 10 tools with lease release."""

    def test_factory_returns_ten_tools(self) -> None:
        manager = _make_manager()
        tools = _build_tools(manager)
        assert len(tools) == 10


# ─── blueprint_release_lease ─────────────────────────────────────────────────


class TestBlueprintReleaseLease:
    """``blueprint_release_lease`` safely releases coordinator leases."""

    def test_release_lease_tool_success(self) -> None:
        coordinator = MagicMock()
        coordinator.release = AsyncMock(return_value=True)
        manager = _make_manager(coordinator=coordinator)
        release_lease = _build_tools(manager)[9]

        result = _run(release_lease.ainvoke({"run_token": "run-token"}))

        assert result == "Lease released successfully."
        coordinator.release.assert_awaited_once_with("proj-A", "run-token")

    def test_release_lease_tool_no_coordinator(self) -> None:
        manager = _make_manager(coordinator=None)
        release_lease = _build_tools(manager)[9]

        result = _run(release_lease.ainvoke({"run_token": "run-token"}))

        assert result == (
            "No coordinator available (lease system not configured)."
        )


# ─── blueprint_claim_pending ────────────────────────────────────────────────


class TestBlueprintClaimPending:
    """``blueprint_claim_pending`` claims records and returns the run_token."""

    def test_claim_pending_returns_records(self) -> None:
        """Enqueue 3 records, claim → returns run_token + 3 records."""
        pending = _FakePendingRepo()
        pending.enqueue("proj-A", "experience", {"text": "e1"})
        pending.enqueue("proj-A", "history", {"entry_type": "feature"})
        pending.enqueue("proj-A", "manual", {"note": "m1"})

        manager = _make_manager(pending_repo=pending)
        tools = _build_tools(manager)
        claim = tools[6]

        result = _run(claim.ainvoke({"batch_size": 10}))
        assert "Claimed 3 pending update(s)" in result
        assert "Run token:" in result
        # The run_token must be prominently present so the blueprinter
        # can pass it to blueprint_acknowledge_pending afterward.
        assert "pending-1" in result
        assert "pending-3" in result

    def test_claim_pending_empty(self) -> None:
        """No records → 'No pending updates'."""
        pending = _FakePendingRepo()
        manager = _make_manager(pending_repo=pending)
        tools = _build_tools(manager)
        claim = tools[6]

        result = _run(claim.ainvoke({}))
        assert "No pending updates" in result

    def test_claim_pending_unauthorized(self) -> None:
        """Non-blueprinter agent → error message."""
        pending = _FakePendingRepo()
        pending.enqueue("proj-A", "experience", {"text": "e1"})

        manager = _make_manager(pending_repo=pending)
        tools = _build_tools(manager, agent_id="developer")
        claim = tools[6]

        result = _run(claim.ainvoke({}))
        assert "Only the blueprinter agent" in result


# ─── blueprint_acknowledge_pending ──────────────────────────────────────────


class TestBlueprintAcknowledgePending:
    """``blueprint_acknowledge_pending`` acks a run_token → count."""

    def test_acknowledge_pending_success(self) -> None:
        """Claim a batch, acknowledge the token → count returned."""
        pending = _FakePendingRepo()
        pending.enqueue("proj-A", "experience", {"text": "e1"})
        pending.enqueue("proj-A", "experience", {"text": "e2"})

        manager = _make_manager(pending_repo=pending)
        tools = _build_tools(manager)
        claim = tools[6]
        ack = tools[7]

        claimed = _run(claim.ainvoke({"batch_size": 10}))
        # Extract the run_token from the first line of the claim output.
        first_line = claimed.splitlines()[0]
        token = first_line.split("Run token: ")[1].strip()

        result = _run(ack.ainvoke({"run_token": token}))
        assert "Acknowledged 2 pending update(s)" in result
        assert token in result

    def test_acknowledge_pending_unauthorized(self) -> None:
        """Non-blueprinter → error message."""
        pending = _FakePendingRepo()
        manager = _make_manager(pending_repo=pending)
        tools = _build_tools(manager, agent_id="developer")
        ack = tools[7]

        result = _run(ack.ainvoke({"run_token": "some-token"}))
        assert "Only the blueprinter agent" in result


# ─── blueprint_get_pending_count ────────────────────────────────────────────


class TestBlueprintGetPendingCount:
    """``blueprint_get_pending_count`` returns the active count (all agents)."""

    def test_get_pending_count(self) -> None:
        """Enqueue records → count matches unprocessed."""
        pending = _FakePendingRepo()
        pending.enqueue("proj-A", "experience", {"text": "e1"})
        pending.enqueue("proj-A", "experience", {"text": "e2"})
        pending.enqueue("proj-B", "experience", {"text": "other-project"})

        manager = _make_manager(pending_repo=pending)
        tools = _build_tools(manager)
        count_tool = tools[8]

        result = _run(count_tool.ainvoke({}))
        # proj-A has 2; proj-B's record is filtered out by project_id.
        assert result == "2"

    def test_get_pending_count_no_repo(self) -> None:
        """No pending repo configured → '0' (graceful)."""
        manager = _make_manager(pending_repo=None)
        tools = _build_tools(manager)
        count_tool = tools[8]

        result = _run(count_tool.ainvoke({}))
        assert result == "0"


# ─── blueprint_disable ──────────────────────────────────────────────────────


class TestBlueprintDisable:
    """``blueprint_disable`` soft-deletes a blueprint (blueprinter-only)."""

    def test_disable_success(self) -> None:
        """Existing blueprint owned by proj-A → disabled."""
        repo = _FakeRepo()
        bp = _FakeBlueprint(id="bp-d", project_id="proj-A", name="Stale")
        repo._store["bp-d"] = bp

        manager = _make_manager(repo=repo, instance_project_id="proj-A")
        tools = _build_tools(manager, agent_id="blueprinter")
        disable = tools[5]

        result = _run(disable.ainvoke({"blueprint_id": "bp-d"}))
        assert "disabled successfully" in result.lower()

    def test_disable_unauthorized(self) -> None:
        """Non-blueprinter → error message."""
        repo = _FakeRepo()
        bp = _FakeBlueprint(id="bp-d2", project_id="proj-A", name="Stale")
        repo._store["bp-d2"] = bp

        manager = _make_manager(repo=repo, instance_project_id="proj-A")
        tools = _build_tools(manager, agent_id="developer")
        disable = tools[5]

        result = _run(disable.ainvoke({"blueprint_id": "bp-d2"}))
        assert "Only the blueprinter agent" in result

    def test_disable_not_found(self) -> None:
        """Blueprint owned by proj-B, caller in proj-A → 'not found'."""
        repo = _FakeRepo()
        bp = _FakeBlueprint(id="bp-d3", project_id="proj-B", name="Other")
        repo._store["bp-d3"] = bp

        manager = _make_manager(repo=repo, instance_project_id="proj-A")
        tools = _build_tools(manager, agent_id="blueprinter")
        disable = tools[5]

        result = _run(disable.ainvoke({"blueprint_id": "bp-d3"}))
        assert result == "Blueprint not found."


# ─── blueprint_update status param ──────────────────────────────────────────


class TestBlueprintUpdateStatus:
    """``blueprint_update`` forwards the ``status`` kwarg to the service."""

    def test_update_with_status(self) -> None:
        """Update with status='draft' → success, status forwarded."""
        repo = _FakeRepo()
        bp = _FakeBlueprint(id="bp-s", project_id="proj-A", name="StageMe")
        repo._store["bp-s"] = bp

        manager = _make_manager(repo=repo, instance_project_id="proj-A")
        tools = _build_tools(manager, agent_id="blueprinter")
        blueprint_update = tools[4]

        result = _run(
            blueprint_update.ainvoke(
                {"blueprint_id": "bp-s", "status": "draft"}
            )
        )
        assert "updated successfully" in result.lower()
        # The status must have been forwarded to the blueprint row.
        assert getattr(bp, "status", None) == "draft"
