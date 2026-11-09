"""I5 — the REST PATCH desync guard (``PUT /api/skills/{skill_id}``).

THE DEFECT THIS PINS.  ``daemon/routers/skills.py`` translated the public
``is_active`` flag by POPPING it and writing ONLY ``status``::

    if "is_active" in fields:
        fields["status"] = "active" if fields.pop("is_active") else "inactive"

so the row's ``is_active`` COLUMN never moved.  Every ``PATCH
{"is_active": false}`` therefore produced ``(is_active=True,
status='inactive')`` — the MIRROR of the real production desync row
``(is_active=False, status='ab_testing')``.  ``skills.is_active`` and
``skills.status`` are two desyncable columns; the census buckets on one and
the readers on the other, so a single-column write makes the two disagree.

THE FIX (inlined, deliberately NOT the E4 writer).  The handler keeps the
public flag AND derives the paired status, so ``store.update_skill`` persists
BOTH in one repository call.  Reusing
``SkillRepository.deactivate_with_receipt`` was REJECTED: it refuses on an
open A/B test, which would add NEW refusals to an existing public REST path —
a semantic change, not a desync fix.

Mutation leg (pack ``skill_dual_column_deactivation_unit_test``):
``M-I5-a`` restore the old pop-and-translate shape (drop the ``is_active``
write) ⇒ the both-columns pins RED while the status-only assertion still
passes — i.e. the pin catches the DESYNC, not merely the translation.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock


# ── a minimal app carrying ONLY the skills router, with a recording store ──


class _Row:
    """A row double exposing the shape ``_skill_to_dict`` accepts."""

    def __init__(self, data: dict) -> None:
        self._data = dict(data)

    def to_dict(self) -> dict:
        return dict(self._data)


class _RecordingStore:
    """A store double recording the ``update_skill`` kwargs it was handed."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.rows: dict[str, dict] = {}

    async def update_skill(self, skill_id: str, **fields):
        self.calls.append(dict(fields))
        row = self.rows.get(skill_id)
        if row is None:
            return None
        row.update(fields)
        return _Row(row)

    async def get_skill(self, skill_id: str):
        row = self.rows.get(skill_id)
        return _Row(row) if row else None

    async def deactivate_skill(self, skill_id: str):
        row = self.rows.get(skill_id)
        if row is None:
            return None
        row.update({"is_active": False, "status": "inactive"})
        return _Row(row)


def _app_with(store) -> TestClient:
    from daemon.routers import skills as skills_module

    app = FastAPI()
    app.state.manager = MagicMock(is_write_paused=False)
    app.include_router(skills_module.router, prefix="/api")
    app.dependency_overrides[skills_module.get_store] = lambda: store
    return TestClient(app, raise_server_exceptions=False)


def _row_dict(**overrides):
    row = {
        "id": "skill-abc",
        "name": "s",
        "description": "d",
        "content": "c",
        "category": "workflow",
        "project_id": "p",
        "is_active": True,
        "status": "active",
        "lineage_origin": "imported",
        "generation": 0,
        "ab_test_group": None,
        "total_selections": 0,
        "total_applied": 0,
        "total_completions": 0,
        "total_fallbacks": 0,
        "consecutive_failures": 0,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "last_used_at": None,
        "auto_load": False,
        "source_skill_bank_id": None,
    }
    row.update(overrides)
    return row


@pytest.fixture()
def store():
    s = _RecordingStore()
    s.rows["skill-abc"] = _row_dict()
    return s


@pytest.fixture()
def client(store):
    return _app_with(store)


class TestBothColumnsAreForwarded:
    """M-I5-a — the pop-and-translate shape reds exactly here."""

    def test_deactivate_direction_forwards_both_columns(self, client, store) -> None:
        response = client.put("/api/skills/skill-abc", json={"is_active": False})
        assert response.status_code == 200, response.text
        kwargs = store.calls[-1]
        assert kwargs["is_active"] is False, (
            "the PATCH must MOVE the is_active column — popping it writes only "
            "status and manufactures the (True, 'inactive') mirror desync"
        )
        assert kwargs["status"] == "inactive"

    def test_activate_direction_forwards_both_columns(self, client, store) -> None:
        store.rows["skill-abc"] = _row_dict(is_active=False, status="inactive")
        response = client.put("/api/skills/skill-abc", json={"is_active": True})
        assert response.status_code == 200, response.text
        kwargs = store.calls[-1]
        assert kwargs["is_active"] is True
        assert kwargs["status"] == "active"

    def test_the_persisted_row_never_ends_up_desynced(self, client, store) -> None:
        """End-to-end against the recording store: the PAIR stays consistent."""
        client.put("/api/skills/skill-abc", json={"is_active": False})
        row = store.rows["skill-abc"]
        assert (row["is_active"], row["status"]) == (False, "inactive")

        client.put("/api/skills/skill-abc", json={"is_active": True})
        row = store.rows["skill-abc"]
        assert (row["is_active"], row["status"]) == (True, "active")

    def test_a_metadata_only_patch_touches_neither_column(
        self, client, store
    ) -> None:
        client.put("/api/skills/skill-abc", json={"description": "x"})
        kwargs = store.calls[-1]
        assert "is_active" not in kwargs
        assert "status" not in kwargs
        assert kwargs["description"] == "x"

    def test_is_active_with_a_metadata_field_carries_both_plus_the_metadata(
        self, client, store
    ) -> None:
        client.put(
            "/api/skills/skill-abc",
            json={"is_active": False, "description": "new", "category": "ops"},
        )
        kwargs = store.calls[-1]
        assert kwargs["is_active"] is False
        assert kwargs["status"] == "inactive"
        assert kwargs["description"] == "new"
        assert kwargs["category"] == "ops"


class TestTheRestContractIsUnchanged:
    """No new refusals, no new accepted fields, no new error paths."""

    def test_a_row_guarded_by_an_open_ab_test_is_still_updatable(
        self, client, store
    ) -> None:
        """The E4 writer REFUSES on an open A/B test; this ROUTE must not.

        This is the assertion that proves the fix did NOT reuse the E4
        writer: reusing it would turn an existing 200 into a refusal.
        """
        store.rows["skill-abc"] = _row_dict(ab_test_group="grp-open")
        response = client.put("/api/skills/skill-abc", json={"is_active": False})
        assert response.status_code == 200, response.text
        assert store.rows["skill-abc"]["status"] == "inactive"

    def test_a_missing_row_is_still_a_404(self, client) -> None:
        response = client.put("/api/skills/ghost", json={"is_active": False})
        assert response.status_code == 404

    def test_a_status_only_payload_moves_neither_column(self, client, store) -> None:
        """``status`` was never an accepted field; the fix must not add it.

        FastAPI ignores an undeclared body field, so the request succeeds and
        the handler forwards NOTHING — a caller can therefore never move
        ``status`` without ``is_active`` at this site.
        """
        response = client.put("/api/skills/skill-abc", json={"status": "inactive"})
        assert response.status_code == 200
        kwargs = store.calls[-1]
        assert "status" not in kwargs
        assert "is_active" not in kwargs

    def test_null_is_active_is_dropped_like_every_other_null(self, client, store) -> None:
        client.put("/api/skills/skill-abc", json={"is_active": None, "description": "x"})
        kwargs = store.calls[-1]
        assert "is_active" not in kwargs and "status" not in kwargs
