"""Unit pins for the nest-ops shakedown defect patch — READ-SIDE core (Phase 1).

Seven defects are pinned here: **F12** (status filter dropped at the
lifecycle→repo seam), **F4** (pagination flags + full-age-range scan),
**F3** (the never-dispatched husk detector), **F2** (census + totals
reconciliation), **F8** (read-path purity), **F5** (dlq vs dead-job
measures) and **F1** (the ``instance_fleet_summary`` alias).

Every pin names its PLANNED MUTATION in the class docstring — the
mutation cycles are executed by the tester in Phase 4, not here. One
behaviour neutered per run.

SAFETY FENCE: every repository pin runs on a ``tempfile`` SQLite database
(``mktemp``-style, created per test) — no HOME-derived or live-shaped path
is ever resolved, and nothing here touches ``ensemble_prod`` or the live
daemon on ``:9797``.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.tools import instance_read as ir


# ────────────────────────────────────────────────────────────────────────
# F12 — the ``status`` filter must reach the repository
# ────────────────────────────────────────────────────────────────────────


class _RecordingRepo:
    """A repository double that RECORDS the kwargs it was called with."""

    def __init__(self):
        self.list_calls: list[dict] = []

    def list(self, **kwargs):
        self.list_calls.append(dict(kwargs))
        return [], 0, False

    def list_child_ids_permanent(self, instance_id):
        return []


class _RepoOwnerManager:
    """The minimal manager surface the lifecycle service touches."""

    def __init__(self, repo):
        self._instance_repository = repo


class TestF12StatusForwardSeam:
    """F12 — ``status`` is forwarded at the lifecycle→repository seam.

    PLANNED MUTATION: delete the ``status=status`` kwarg from
    ``InstanceLifecycleService.list_instances``'s call into
    ``instance_repository.list(...)`` — the forwarding assertion goes
    RED (the recorded kwargs lose ``status``).

    This is the FIRST-EVER pin on the lifecycle→repo seam: before the
    fix, the kwarg was silently dropped, so a filtered
    ``instance_list(status=...)`` returned an arbitrary first page while
    ``total_matching`` counted only the matching population.
    """

    def _service(self, repo):
        manager = _RepoOwnerManager(repo)
        service = InstanceLifecycleService(manager, cancellation_service=None)
        return service

    def test_status_reaches_the_repository(self):
        repo = _RecordingRepo()
        service = self._service(repo)

        service.list_instances(status="running", limit=5, offset=0)

        assert len(repo.list_calls) == 1
        assert repo.list_calls[0]["status"] == "running"

    def test_status_defaults_to_none(self):
        """No filter ⇒ ``status=None`` reaches the repo (the repo treats
        ``None`` as "no status filter")."""
        repo = _RecordingRepo()
        service = self._service(repo)

        service.list_instances(limit=5, offset=0)

        assert repo.list_calls[0]["status"] is None

    def test_omitted_status_is_present_as_a_key(self):
        """The kwarg is always PRESENT on the repo call (present-and-None
        beats absent-and-guessed)."""
        repo = _RecordingRepo()
        service = self._service(repo)

        service.list_instances(limit=5, offset=0)

        assert "status" in repo.list_calls[0]

    def test_neighbour_kwargs_are_not_disturbed(self):
        """The forward is ADDITIVE — the established neighbours travel
        through unchanged."""
        repo = _RecordingRepo()
        service = self._service(repo)

        service.list_instances(
            limit=25,
            offset=10,
            project_id="proj-1",
            exclude_kb=False,
            include_descendants=True,
            search="refactor",
            order="activity",
            source="chat",
            status="paused",
        )

        forwarded = repo.list_calls[0]
        assert forwarded["limit"] == 25
        assert forwarded["offset"] == 10
        assert forwarded["project_id"] == "proj-1"
        assert forwarded["exclude_kb"] is False
        assert forwarded["include_descendants"] is True
        assert forwarded["search"] == "refactor"
        assert forwarded["order"] == "activity"
        assert forwarded["source"] == "chat"
        assert forwarded["status"] == "paused"


# ────────────────────────────────────────────────────────────────────────
# the fake fleet — the tool-level fixture
# ────────────────────────────────────────────────────────────────────────


@dataclass
class FakeRow:
    """One instance row, in the DICT shape ``manager.list_instances``
    hands to the read tools."""

    instance_id: str
    agent_id: str = "worker"
    parent_id: str | None = None
    status: str = "idle"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_activity_at: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "agent_id": self.agent_id,
            "agent_name": self.agent_id,
            "parent_id": self.parent_id,
            "status": self.status,
            "title": None,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.created_at,
            "last_activity_at": self.last_activity_at,
            "paused_at": None,
        }


class FakeFleetManager:
    """A manager double over an in-memory fleet.

    ``list_instances`` honours ``limit``/``offset``/``status`` and reports
    the TOTAL over the same WHERE — the same contract the real lifecycle
    service offers (post-F12). The ORDER mirrors the repository's flat
    path: ``created_at DESC``, then ``instance_id ASC``.
    """

    def __init__(self, rows: list[FakeRow] | None = None):
        self.rows: list[FakeRow] = list(rows or [])
        self.engine = None
        self._question_manager = None
        self._instance_repository = None
        self._task_repo = None
        self.list_calls: list[dict] = []
        #: Test-only knob: force the reported TOTAL (the COUNT) to a value
        #: other than the number of matching rows. It models the count/page
        #: race the real repository has — the COUNT statement runs BEFORE
        #: the SELECT in the same session, so a row deleted in between
        #: yields a total that counts a row the page does not contain.
        self.total_override: int | None = None

    def add(self, row: FakeRow) -> FakeRow:
        self.rows.append(row)
        return row

    def list_instances(
        self,
        limit: int = 10,
        offset: int = 0,
        status: str | None = None,
        **_kw,
    ):
        self.list_calls.append(
            {"limit": limit, "offset": offset, "status": status}
        )
        matching = [r for r in self.rows if status is None or r.status == status]
        matching.sort(key=lambda r: r.instance_id)
        matching.sort(key=lambda r: r.created_at, reverse=True)
        total = len(matching) if self.total_override is None else self.total_override
        page = matching[offset : offset + limit]
        out: list[dict] = []
        for row in page:
            info = row.to_dict()
            info["children"] = [
                c.instance_id for c in self.rows if c.parent_id == row.instance_id
            ]
            out.append(info)
        return out, total, False


def _read_tools(manager, *, agent: str = "maintenancer"):
    """The ``instance_read`` tool set for one fake manager."""
    return ir.create_instance_read_tools(
        manager, current_instance_id="caller-iid", agent_id=agent
    )


def _by_name(tools, name):
    return next(t for t in tools if t.name == name)


async def _call(tools, name, **kwargs):
    """Invoke one tool and JSON-decode its payload."""
    out = await _by_name(tools, name).ainvoke(kwargs)
    return json.loads(out)


# ────────────────────────────────────────────────────────────────────────
# F4 — the pagination flags
# ────────────────────────────────────────────────────────────────────────


class TestF4aHonestTruncated:
    """F4a — ``instance_list.truncated`` follows the exact formula.

    PLANNED MUTATION: restore the repository's constant ``False`` (copy
    the repo flag verbatim again) — the under-page and over-page
    assertions go RED (``truncated`` stays ``false`` while more rows
    exist).

    The contract: ``truncated == (total_matching > offset + count)``,
    re-derivable from the payload alone (``filters_applied.offset``).
    """

    def _fleet(self, n: int, status: str = "idle"):
        manager = FakeFleetManager()
        for i in range(n):
            manager.add(FakeRow(f"i-{i:03d}", status=status))
        return manager

    async def test_under_page_reports_truncated(self):
        manager = self._fleet(10)
        payload = await _call(_read_tools(manager), "instance_list", limit=5)

        assert payload["count"] == 5
        assert payload["total_matching"] == 10
        assert payload["truncated"] is True

    async def test_exact_page_reports_not_truncated(self):
        manager = self._fleet(5)
        payload = await _call(_read_tools(manager), "instance_list", limit=5)

        assert payload["count"] == 5
        assert payload["total_matching"] == 5
        assert payload["truncated"] is False

    async def test_over_page_reports_not_truncated(self):
        manager = self._fleet(10)
        payload = await _call(
            _read_tools(manager), "instance_list", limit=5, offset=5
        )

        assert payload["count"] == 5
        assert payload["total_matching"] == 10
        assert payload["truncated"] is False

    async def test_exhausted_empty_page_reports_not_truncated(self):
        """``len(rows) == 0`` and ``total <= offset`` ⇒ ``False`` —
        nothing matches past the caller's offset."""
        manager = self._fleet(10)
        payload = await _call(
            _read_tools(manager), "instance_list", limit=5, offset=10
        )

        assert payload["count"] == 0
        assert payload["total_matching"] == 10
        assert payload["truncated"] is False

    async def test_empty_page_before_the_end_reports_truncated(self):
        """``len(rows) == 0`` but ``total > offset`` ⇒ ``True``.

        Reachable through the repository's count/page race: the COUNT
        statement runs before the SELECT in the same session, so a row
        deleted in between leaves the total counting a row the page no
        longer contains. The flag must not silently read ``false``.
        """
        manager = self._fleet(10)
        manager.total_override = 10  # the COUNT saw 10 …
        manager.rows = manager.rows[:4]  # … then 6 rows were deleted
        payload = await _call(
            _read_tools(manager), "instance_list", limit=5, offset=6
        )

        assert payload["count"] == 0
        assert payload["total_matching"] == 10
        assert payload["truncated"] is True

    async def test_flag_is_rederivable_from_the_payload_alone(self):
        """The payload is self-consistent: a consumer can recompute the
        flag from ``total_matching``, ``filters_applied.offset`` and
        ``count``."""
        manager = self._fleet(10)
        payload = await _call(
            _read_tools(manager), "instance_list", limit=4, offset=2
        )

        offset = payload["filters_applied"]["offset"]
        assert payload["truncated"] == (
            payload["total_matching"] > offset + payload["count"]
        )

    async def test_filtered_call_truncated_is_exact_too(self):
        """F12 + F4a: with a status filter the COUNT and the rows describe
        the SAME population, so the formula stays exact."""
        manager = FakeFleetManager()
        for i in range(6):
            manager.add(FakeRow(f"idle-{i}", status="idle"))
        for i in range(4):
            manager.add(FakeRow(f"run-{i}", status="running"))

        payload = await _call(
            _read_tools(manager), "instance_list", status="running", limit=2
        )

        assert payload["total_matching"] == 4
        assert payload["count"] == 2
        assert payload["truncated"] is True
        assert {row["status"] for row in payload["rows"]} == {"running"}
