"""PIN — the schedule NAMESPACE is scoped to LIVE rows (schedule listing
truth, Phase 1 — architecture v3 §4.2).

The rules this file pins:

* create-time label uniqueness is scoped to LIVE rows — a CANCELLED row
  keeps its name for history but no longer OWNS it, so a new schedule
  may reuse the label;
* a LIVE row's label still blocks a create;
* label resolution PREFERS the LIVE row (a dead+live pair sharing a
  label resolves to the live one) and REFUSES an ambiguity between two
  live rows (the caller must use ``source_id``);
* the terminal-row semantics are preserved — with NO live row carrying
  the label, the historical first-match lookup runs (cancelling an
  already-cancelled schedule still resolves to that row).

MUTATION-VERIFIED (skill ``mutation-verified-regression-pins``): the
"uniqueness reverted to match-any-row" mutation and the "live-preference
removed" mutation each turn this pin RED; each restore turns it GREEN.

SAFETY FENCE: pure unit tests — in-memory doubles only.  No DB, no
daemon boot, no live contact.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services import scheduling_service as svc


def _row(
    source_id: str,
    *,
    name: str,
    status: str = "running",
    source_type: str = "scheduler",
) -> SimpleNamespace:
    return SimpleNamespace(
        source_id=source_id,
        source_type=source_type,
        name=name,
        config={"agent": "ari", "message": "hi", "timezone": "UTC"},
        credentials=None,
        enabled=status != "cancelled",
        autostart=True,
        status=status,
        error_message=None,
        created_at="2026-11-01T00:00:00+00:00",
        updated_at="2026-11-01T00:00:00+00:00",
    )


class _FakeRepo:
    """In-memory source repository double with PAGE-AWARE listing."""

    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}

    def add(self, row: SimpleNamespace) -> SimpleNamespace:
        self.rows[row.source_id] = row
        return row

    def get_source_config(self, source_id):
        return self.rows.get(source_id)

    def get_source_config_by_name(self, name):
        for row in self.rows.values():
            if row.name == name:
                return row
        return None

    def list_source_configs(self, enabled=None, status=None, limit=100, offset=0):
        rows = list(self.rows.values())
        if status is not None:
            rows = [r for r in rows if r.status == status]
        if enabled is not None:
            rows = [r for r in rows if r.enabled == enabled]
        return rows[offset : offset + limit]

    def create_source_config(self, source_type, name, config, credentials=None,
                             enabled=True, autostart=True, source_id=None):
        source_id = source_id or name
        row = _row(source_id, name=name, status="stopped")
        row.config = config
        row.enabled = enabled
        return self.add(row)

    def update_source_config(self, *args, **kwargs):
        return None

    def update_source_status(self, *args, **kwargs):
        return None

    def cancel_source_config(self, source_id):
        row = self.rows.get(source_id)
        if row is not None:
            row.status = "cancelled"
            row.enabled = False
        return row

    def get_latest_execution(self, schedule_id):
        return None


@pytest.fixture
def namespace_env(monkeypatch):
    """A configured service + a patched agent registry (create-time
    fail-fast lookups the agent up in the GLOBAL registry)."""
    repo = _FakeRepo()
    registry = MagicMock()
    registry.get = MagicMock(return_value=None)
    registry.unregister = MagicMock(return_value=True)
    registry._create_adapter_from_config = AsyncMock(return_value=MagicMock())
    registry.register = MagicMock()
    registry.start_adapter = AsyncMock(return_value=True)
    svc.configure(source_repo=repo, source_registry=registry)
    monkeypatch.setattr(svc, "_source_locks", {})

    import daemon.registry as registry_module

    agent_registry = MagicMock()
    agent_registry.exists = MagicMock(return_value=True)
    monkeypatch.setattr(registry_module, "get_registry", lambda: agent_registry)
    yield repo
    svc._deps.source_repo = None
    svc._deps.source_registry = None
    svc._deps.project_repository = None
    svc._source_locks.clear()


class TestNamespaceLiveRows:
    @pytest.mark.asyncio
    async def test_create_with_a_cancelled_rows_label_succeeds(self, namespace_env):
        """A CANCELLED row keeps its name for history but no longer owns
        it — the create SUCCEEDS."""
        repo = namespace_env
        repo.add(_row("dead-1", name="reused-label", status="cancelled"))
        result = await svc.create_schedule(
            {
                "label": "reused-label",
                "agent": "ari",
                "message": "hi",
                "when": "2030-01-15T09:00:00",
                "timezone": "UTC",
                "recurrence": "once",
            },
            caller_instance_id="test",
            caller_agent_id="tester",
        )
        assert result.label == "reused-label"
        # The cancelled row is untouched — history is preserved.
        assert repo.rows["dead-1"].status == "cancelled"
        assert repo.rows["dead-1"].name == "reused-label"

    @pytest.mark.asyncio
    async def test_create_with_a_live_rows_label_fails(self, namespace_env):
        """A LIVE row still owns its label — the create FAILS."""
        repo = namespace_env
        repo.add(_row("live-1", name="taken-label", status="running"))
        with pytest.raises(ValueError, match="label already exists"):
            await svc.create_schedule(
                {
                    "label": "taken-label",
                    "agent": "ari",
                    "message": "hi",
                    "when": "2030-01-15T09:00:00",
                    "timezone": "UTC",
                    "recurrence": "once",
                },
                caller_instance_id="test",
                caller_agent_id="tester",
            )

    def test_label_resolution_prefers_the_live_row(self, namespace_env):
        """A dead+live pair sharing a label resolves to the LIVE row."""
        repo = namespace_env
        repo.add(_row("dead-1", name="shared", status="cancelled"))
        repo.add(_row("live-1", name="shared", status="running"))
        row = svc.resolve_label_row(repo, "shared")
        assert row is not None
        assert row.source_id == "live-1"

    def test_label_resolution_refuses_two_live_rows(self, namespace_env):
        """Two LIVE rows sharing a label ⇒ the resolution REFUSES (a
        silent first-match pick would be a guess)."""
        repo = namespace_env
        repo.add(_row("live-1", name="shared", status="running"))
        repo.add(_row("live-2", name="shared", status="running"))
        with pytest.raises(ValueError, match="ambiguous"):
            svc.resolve_label_row(repo, "shared")

    def test_label_resolution_keeps_the_terminal_row_semantics(self, namespace_env):
        """With NO live row carrying the label the historical first-match
        lookup runs — cancelling an already-cancelled schedule still
        resolves to that row."""
        repo = namespace_env
        repo.add(_row("dead-1", name="gone", status="cancelled"))
        row = svc.resolve_label_row(repo, "gone")
        assert row is not None
        assert row.source_id == "dead-1"
        assert row.status == "cancelled"
