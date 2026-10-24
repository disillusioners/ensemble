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
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task
from daemon.services.instance_lifecycle import InstanceLifecycleService
from daemon.tools import instance_read as ir
from tests.helpers.nestops_pg_fixtures import by_name, call
from daemon.tools._tool_registry import (
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    discover_source_only_tool_names,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


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
        payload = await call(_read_tools(manager), "instance_list", limit=5)

        assert payload["count"] == 5
        assert payload["total_matching"] == 10
        assert payload["truncated"] is True

    async def test_exact_page_reports_not_truncated(self):
        manager = self._fleet(5)
        payload = await call(_read_tools(manager), "instance_list", limit=5)

        assert payload["count"] == 5
        assert payload["total_matching"] == 5
        assert payload["truncated"] is False

    async def test_over_page_reports_not_truncated(self):
        manager = self._fleet(10)
        payload = await call(
            _read_tools(manager), "instance_list", limit=5, offset=5
        )

        assert payload["count"] == 5
        assert payload["total_matching"] == 10
        assert payload["truncated"] is False

    async def test_exhausted_empty_page_reports_not_truncated(self):
        """``len(rows) == 0`` and ``total <= offset`` ⇒ ``False`` —
        nothing matches past the caller's offset."""
        manager = self._fleet(10)
        payload = await call(
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
        payload = await call(
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
        payload = await call(
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

        payload = await call(
            _read_tools(manager), "instance_list", status="running", limit=2
        )

        assert payload["total_matching"] == 4
        assert payload["count"] == 2
        assert payload["truncated"] is True
        assert {row["status"] for row in payload["rows"]} == {"running"}


class SyntheticFleetManager(FakeFleetManager):
    """A fleet of ``total`` rows, generated ON DEMAND per window.

    The fleet is never materialized (that is the point of the F4b
    streaming contract) — a window's rows are built when the window is
    read. The rows are ordered ``created_at DESC`` (index 0 = newest),
    exactly like the repository's flat path.
    """

    def __init__(self, total: int, *, base: datetime | None = None):
        super().__init__([])
        self.total = total
        self.base = base or datetime.now(timezone.utc)
        #: Every row id the manager actually SERVED to the scanner.
        self.rows_served: list[str] = []
        #: Rows injected at the FRONT of the order (the concurrent-insert
        #: drift fixture).
        self.front_rows: list[FakeRow] = []

    def _row(self, index: int) -> FakeRow:
        stamp = (self.base - timedelta(seconds=index)).isoformat()
        return FakeRow(f"i-{index:06d}", created_at=stamp)

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
        effective_total = len(self.front_rows) + self.total
        out: list[dict] = []
        for position in range(offset, min(offset + limit, effective_total)):
            if position < len(self.front_rows):
                row = self.front_rows[position]
            else:
                row = self._row(position - len(self.front_rows))
            info = row.to_dict()
            info["children"] = []
            out.append(info)
            self.rows_served.append(row.instance_id)
        return out, effective_total, False


class _OverlappingWindowManager(SyntheticFleetManager):
    """A synthetic fleet whose BOUNDARY row is a detector match.

    The concurrent-insert drift shape serves the last row of window 1
    AGAIN at the head of window 2. This manager makes that boundary
    row an ORPHAN (a live row whose parent is gone), so the
    duplication is observable in the payload's counts.
    """

    def _row(self, index: int) -> FakeRow:
        row = super()._row(index)
        if index == self.total - 1:
            row.parent_id = "gone-parent"
        return row


class TestF4bFullAgeRangeScan:
    """F4b — the health scan covers the WHOLE fleet, in offset windows.

    PLANNED MUTATION (one behaviour per run):
      * restore the SINGLE-window scan (``limit=FLEET_SCAN_LIMIT``,
        ``offset=0``) — the "old rows covered" pin goes RED (the tail is
        never served) and the stop reason stops being ``end_of_set``;
      * break the walk on ``len(read) >= total`` (the drift bug) — the
        concurrent-write drift pin goes RED (the tail is skipped);
      * re-assign ``total`` on every window — the drift pin goes RED;
      * remove the window cap — the "cap fires" pin goes RED;
      * remove the time budget — the "budget fires" pin goes RED.
    """

    async def _health(self, manager):
        payload = await call(_read_tools(manager), "instance_health")
        return payload

    async def test_fleet_larger_than_the_window_is_covered(self):
        """More rows than one window ⇒ the OLD rows are read too."""
        total = ir.FLEET_SCAN_LIMIT + 1
        manager = SyntheticFleetManager(total)

        payload = await self._health(manager)

        assert payload["scan_truncated"] is False
        assert payload["scan_stop_reason"] == "end_of_set"
        # the OLDEST row (the last one served) was read
        assert len(manager.rows_served) == total
        assert f"i-{total - 1:06d}" in manager.rows_served

    async def test_the_walk_asks_for_a_second_window(self):
        """The walk is a LOOP: a second window is requested once the first
        is full."""
        manager = SyntheticFleetManager(ir.FLEET_SCAN_LIMIT + 1)

        await self._health(manager)

        offsets = [call["offset"] for call in manager.list_calls]
        assert offsets[0] == 0
        assert ir.FLEET_SCAN_LIMIT in offsets

    async def test_small_fleet_stops_on_the_empty_window(self):
        """A fleet smaller than one window ends at the empty window."""
        manager = SyntheticFleetManager(3)

        payload = await self._health(manager)

        assert payload["scan_truncated"] is False
        assert payload["scan_stop_reason"] == "end_of_set"
        assert len(manager.list_calls) == 2  # the window + the empty one

    async def test_window_cap_fires_loudly(self):
        """A fleet past the window cap ⇒ ``scan_truncated`` is TRUE (never
        silently false) and the stop reason says ``window_cap``."""
        total = ir.FLEET_SCAN_LIMIT * ir.FLEET_SCAN_MAX_WINDOWS + 5
        manager = SyntheticFleetManager(total)

        payload = await self._health(manager)

        assert payload["scan_truncated"] is True
        assert payload["scan_stop_reason"] == "window_cap"
        assert len(manager.list_calls) == ir.FLEET_SCAN_MAX_WINDOWS

    async def test_time_budget_fires_loudly(self, monkeypatch):
        """The wall-clock budget is checked BEFORE each window; on
        exhaustion the walk breaks with ``scan_truncated=True`` and
        ``scan_stop_reason="time_budget"``."""
        monkeypatch.setattr(ir, "FLEET_SCAN_TIME_BUDGET_SECONDS", 0.05)

        class SlowManager(SyntheticFleetManager):
            def list_instances(self, limit=10, offset=0, status=None, **kw):
                time.sleep(0.08)
                return super().list_instances(limit, offset, status, **kw)

        manager = SlowManager(ir.FLEET_SCAN_LIMIT * 10)

        payload = await self._health(manager)

        assert payload["scan_truncated"] is True
        assert payload["scan_stop_reason"] == "time_budget"

    async def test_concurrent_insert_at_the_front_cannot_skip_the_tail(self):
        """The drift bug: a NEW row landing at the FRONT of the
        ``created_at DESC`` order shifts every later row down. A walk
        that stops on ``len(read) >= total`` skips the TAIL — the exact
        rows the husks live in — and reports a clean scan.

        The walk must read the tail anyway.
        """
        total = ir.FLEET_SCAN_LIMIT
        manager = SyntheticFleetManager(total)
        newest = FakeRow(
            "i-front",
            created_at=(
                manager.base + timedelta(seconds=1)
            ).isoformat(),
        )

        original_list = manager.list_instances

        def list_with_front_insert(limit=10, offset=0, status=None, **kw):
            if offset > 0 and not manager.front_rows:
                # The concurrent insert lands between the two windows.
                manager.front_rows.append(newest)
            return original_list(limit, offset, status, **kw)

        manager.list_instances = list_with_front_insert  # type: ignore[method-assign]

        payload = await self._health(manager)

        # The tail row (the oldest) was served to the scanner.
        assert f"i-{total - 1:06d}" in manager.rows_served
        # And the walk did not mistake the first full window for the end.
        assert payload["scan_stop_reason"] == "end_of_set"
        assert payload["scan_truncated"] is False

    async def test_a_row_served_in_two_windows_is_counted_once(self):
        """The drift shape can serve the SAME row twice.

        A concurrent INSERT at the front of the ``created_at DESC``
        order shifts every later row down, so the LAST row of window 1
        is served AGAIN at the head of window 2. The id-keyed maps
        dedupe on their own; the LIST-backed detectors do NOT — before
        the seen-set fix that row was appended TWICE and every count
        in the payload was inflated by exactly the drift the F4b walk
        exists to survive.

        The overlapping row here is an ORPHAN (a live row whose parent
        is gone), so the duplication is observable in the payload.
        """
        total = ir.FLEET_SCAN_LIMIT
        manager = _OverlappingWindowManager(total)
        newest = FakeRow(
            "i-front",
            created_at=(manager.base + timedelta(seconds=1)).isoformat(),
        )

        original_list = manager.list_instances

        def list_with_front_insert(limit=10, offset=0, status=None, **kw):
            if offset > 0 and not manager.front_rows:
                manager.front_rows.append(newest)
            return original_list(limit, offset, status, **kw)

        manager.list_instances = list_with_front_insert  # type: ignore[method-assign]

        payload = await self._health(manager)

        # the overlapping row really WAS served twice ...
        assert manager.rows_served.count("i-000999") == 2
        # ... but it is COUNTED once
        orphans = next(
            d for d in payload["detectors"] if d["detector"] == "orphans"
        )
        assert [row["instance_id"] for row in orphans["instances"]] == ["i-000999"]
        assert orphans["count"] == 1
        assert payload["total_flagged"] == sum(
            d["count"] for d in payload["detectors"]
        )

    async def test_a_husk_candidate_served_in_two_windows_is_counted_once(
        self, fleet_db
    ):
        """The same duplication, on the HUSK accumulator.

        The husk candidates are a LIST too, and the batched evidence
        legs resolve the candidate SET — a duplicated candidate was
        flagged TWICE, so the husk count was inflated by the drift.
        """
        _insert_instances(
            fleet_db, [_instance("husk-dup", created_at=_aged())]
        )
        manager = RepoBackedManager(fleet_db)
        calls: list[int] = []
        original_list = manager.list_instances

        def overlapping_list(limit=10, offset=0, status=None, **kw):
            calls.append(offset)
            if len(calls) == 1:
                return original_list(limit=limit, offset=offset, status=status, **kw)
            if len(calls) == 2:
                # the SAME row is served again — the drift shape
                rows, total, truncated = original_list(
                    limit=limit, offset=0, status=status, **kw
                )
                return rows, total, truncated
            return [], 0, False

        manager.list_instances = overlapping_list  # type: ignore[method-assign]

        payload = await self._health(manager)

        assert len(calls) >= 2, "the fixture must serve the row twice"
        husk = next(
            d
            for d in payload["detectors"]
            if d["detector"] == "never-dispatched-husk"
        )
        assert [row["instance_id"] for row in husk["instances"]] == ["husk-dup"]
        assert husk["count"] == 1

    async def test_total_is_captured_from_the_first_window_only(self):
        """``total`` is never reassigned: the COUNT the walk trusts is the
        one the FIRST window reported."""
        manager = SyntheticFleetManager(ir.FLEET_SCAN_LIMIT + 1)
        seen_totals: list[int] = []
        original_list = manager.list_instances

        def recording_list(limit=10, offset=0, status=None, **kw):
            rows, total, truncated = original_list(limit, offset, status, **kw)
            seen_totals.append(total)
            return rows, total, truncated

        manager.list_instances = recording_list  # type: ignore[method-assign]

        await self._health(manager)

        assert seen_totals[0] == ir.FLEET_SCAN_LIMIT + 1
        assert len(seen_totals) >= 2


# ────────────────────────────────────────────────────────────────────────
# F3 — the never-dispatched husk detector
# ────────────────────────────────────────────────────────────────────────

HUSK_AGE = timedelta(days=12)


class FakeTaskRepo:
    """A task-repo double: the ids that own a task row."""

    def __init__(self, tasks: dict[str, list] | None = None):
        self._tasks = tasks or {}

    def get_by_instance(self, instance_id):
        return self._tasks.get(instance_id, [])


class FakeQuestionManager:
    """A question-manager double: the ids that own a pending pack."""

    def __init__(self, packs: dict[str, object] | None = None):
        self._packs = packs or {}

    def get_question_pack(self, instance_id):
        return self._packs.get(instance_id)


class SpyRepo(SQLModelInstanceRepository):
    """A repository that RECORDS which evidence probes were called.

    The husk detector must call ``filter_without_any_evidence_ids``
    ONLY — never ``filter_never_dispatched_ids`` (D-1/D-19: that
    method's ``idle ∧ version==1`` conjuncts are a STATE signature, not
    an evidence one).
    """

    def __init__(self, engine):
        super().__init__(engine)
        self.never_dispatched_calls: list[list[str]] = []
        self.without_evidence_calls: list[list[str]] = []

    def filter_never_dispatched_ids(self, instance_ids):
        self.never_dispatched_calls.append(list(instance_ids))
        return super().filter_never_dispatched_ids(instance_ids)

    def filter_without_any_evidence_ids(self, instance_ids):
        self.without_evidence_calls.append(list(instance_ids))
        return super().filter_without_any_evidence_ids(instance_ids)


class RepoBackedManager:
    """The manager surface the read tools touch, over a REAL repository.

    ``list_instances`` mirrors the lifecycle service's dict conversion +
    the per-row ``children`` attach — so the pins run against the real
    SQL, the real ``to_dict()`` shapes and the real evidence probes, on
    a scratch SQLite file.
    """

    def __init__(self, engine, *, tasks=None, packs=None, near_matches=None):
        self.engine = engine
        self._instance_repository = SpyRepo(engine)
        self._task_repo = FakeTaskRepo(tasks)
        self._question_manager = FakeQuestionManager(packs)
        self.list_calls: list[dict] = []
        #: F8 spies — the read path must NEVER touch either seam.
        self.get_instance_calls: list[str] = []
        self.mcp_preload_calls: list[str] = []
        self._near_matches = list(near_matches or [])

    async def get_instance(self, instance_id):
        """The HYDRATION seam (F8) — recorded, never used by a read."""
        self.get_instance_calls.append(instance_id)
        return object()  # sentinel: a real call would compile a graph

    async def ensure_mcp_preloaded(self, instance_id):
        """The MCP-discovery seam (F8) — recorded, never used by a read."""
        self.mcp_preload_calls.append(instance_id)
        return []

    def find_near_instance(self, instance_id, max_distance=7):
        """The fuzzy matcher — repo-only in production; a stub here."""
        return list(self._near_matches)

    def list_instances(
        self,
        limit: int = 10,
        offset: int = 0,
        status: str | None = None,
        **kw,
    ):
        self.list_calls.append(
            {"limit": limit, "offset": offset, "status": status}
        )
        instances, total, truncated = self._instance_repository.list(
            limit=limit,
            offset=offset,
            status=status,
            exclude_kb=kw.get("exclude_kb", True),
        )
        result: list[dict] = []
        for inst in instances:
            info = inst.to_dict()
            info["children"] = self._instance_repository.list_child_ids_permanent(
                inst.instance_id
            )
            result.append(info)
        return result, total, truncated


@pytest.fixture()
def fleet_db():
    """A scratch SQLite engine with the instance + evidence tables.

    tempfile-based — never a HOME-derived or live-shaped path.
    """
    tmpdir = tempfile.mkdtemp(prefix="shakedown-fleet-")
    engine = create_engine(f"sqlite:///{tmpdir}/fleet.db")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _insert_instances(engine, rows: list[Instance]) -> list[str]:
    """Insert instance rows; returns their ids (read BEFORE the commit
    expires the objects)."""
    ids = [row.instance_id for row in rows]
    with Session(engine) as session:
        for row in rows:
            session.add(row)
        session.commit()
    return ids


def _instance(
    instance_id: str,
    *,
    agent_id: str = "worker",
    status: str = "idle",
    version: int = 1,
    created_at: datetime | None = None,
    last_activity_at: datetime | None = None,
    metadata: dict | None = None,
    parent_id: str | None = None,
) -> Instance:
    return Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=f"/tmp/agents/{agent_id}",
        status=status,
        version=version,
        created_at=(created_at or datetime.now(timezone.utc)).isoformat(),
        updated_at=(created_at or datetime.now(timezone.utc)).isoformat(),
        last_activity_at=last_activity_at,
        instance_metadata=metadata or {},
        parent_id=parent_id,
    )


def _aged(**kw) -> datetime:
    return datetime.now(timezone.utc) - HUSK_AGE


class TestF3HuskDetector:
    """F3 — detector #7 ``never-dispatched-husk``.

    PLANNED MUTATION (one behaviour per run):
      * delete the detector's AGE leg — the FRESH-spawn fixture fires
        (it should not) → RED;
      * drop the JobItem leg (E3) — the "husk-with-a-job" fixture fires
        → RED;
      * neuter the ``last_activity_at IS NULL`` leg — the "husk that
        ran a turn" fixture fires → RED;
      * drop the CHILDREN leg (E4) — the "husk with a child" fixture
        fires → RED;
      * drop the question-pack leg (E5) — the "husk with a pending
        pack" fixture fires → RED;
      * drop the pause-marker leg (E6) — the "deliberately parked husk"
        fixture fires → RED;
      * point the detector at ``filter_never_dispatched_ids`` — the
        non-idle and version>1 evidence-free fixtures stop firing → RED;
      * make the detector flag a TERMINAL row — the terminal fixture
        fires → RED.
    """

    async def _health(self, manager):
        return await call(_read_tools(manager), "instance_health")

    def _husk(self, payload):
        for detector in payload["detectors"]:
            if detector["detector"] == "never-dispatched-husk":
                return detector
        raise AssertionError("never-dispatched-husk detector is missing")

    def _flagged_ids(self, payload) -> set[str]:
        return {
            row["instance_id"] for row in self._husk(payload)["instances"]
        }

    async def test_spawned_never_dispatched_row_is_flagged(self, fleet_db):
        """The husk class itself: spawned, never dispatched, aged."""
        _insert_instances(
            fleet_db, [_instance("husk-1", created_at=_aged())]
        )
        manager = RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert self._flagged_ids(payload) == {"husk-1"}
        row = self._husk(payload)["instances"][0]
        assert row["idle_seconds"] is not None
        assert row["idle_seconds"] > ir.DEFAULT_HUSK_MIN_AGE_SECONDS
        assert "last_activity_at" in row["detail"]
        assert "never dispatched" in row["detail"]

    async def test_fresh_spawn_is_not_flagged(self, fleet_db):
        """The legitimate spawn→dispatch ritual: never flagged."""
        _insert_instances(fleet_db, [_instance("fresh-1")])
        manager = RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "fresh-1" not in self._flagged_ids(payload)

    async def test_row_that_ran_a_turn_is_not_flagged(self, fleet_db):
        """A non-NULL ``last_activity_at`` excludes the row — leg 2."""
        _insert_instances(
            fleet_db,
            [
                _instance(
                    "worked-1",
                    created_at=_aged(),
                    last_activity_at=datetime.now(timezone.utc)
                    - timedelta(hours=1),
                )
            ],
        )
        manager = RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "worked-1" not in self._flagged_ids(payload)

    async def test_terminal_row_is_not_flagged(self, fleet_db):
        """A TERMINAL row is not a husk, however old and empty."""
        _insert_instances(
            fleet_db,
            [_instance("done-1", status="completed", created_at=_aged())],
        )
        manager = RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "done-1" not in self._flagged_ids(payload)

    async def test_row_with_a_task_row_is_not_flagged(self, fleet_db):
        """E1 — a task row is evidence of a dispatch."""
        _insert_instances(
            fleet_db, [_instance("tasked-1", created_at=_aged())]
        )
        with Session(fleet_db) as session:
            session.add(Task(instance_id="tasked-1", task_type="PROCESS_MESSAGE"))
            session.commit()
        manager = RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "tasked-1" not in self._flagged_ids(payload)

    async def test_row_with_a_message_row_is_not_flagged(self, fleet_db):
        """E2 — a message row is evidence of a dispatch."""
        _insert_instances(
            fleet_db, [_instance("messaged-1", created_at=_aged())]
        )
        with Session(fleet_db) as session:
            session.add(
                MessageQueue(
                    message_id="m-1",
                    instance_id="messaged-1",
                    content="hello",
                    role="user",
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            session.commit()
        manager = RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "messaged-1" not in self._flagged_ids(payload)

    async def test_row_with_a_job_item_is_not_flagged(self, fleet_db):
        """E3 — a ``job_queue_items`` row is evidence of a dispatch, in
        ANY admission state (soft-deleted included)."""
        _insert_instances(
            fleet_db, [_instance("jobbed-1", created_at=_aged())]
        )
        with Session(fleet_db) as session:
            session.add(
                JobItem(
                    agent_id="worker",
                    agent_dir="/tmp/agents/worker",
                    message="dispatch",
                    instance_id="jobbed-1",
                    job_type="task",
                    admission_state="dead",
                    deleted_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            session.commit()
        manager = RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "jobbed-1" not in self._flagged_ids(payload)

    async def test_row_with_a_child_is_not_flagged(self, fleet_db):
        """E4 — a husk that spawned children is not evidence-free. The
        children come from the PERMANENT parent_id record."""
        _insert_instances(
            fleet_db,
            [
                _instance("parent-1", created_at=_aged()),
                _instance(
                    "child-1",
                    created_at=_aged(),
                    parent_id="parent-1",
                ),
            ],
        )
        manager = RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        flagged = self._flagged_ids(payload)
        assert "parent-1" not in flagged

    async def test_row_with_a_pending_pack_is_not_flagged(self, fleet_db):
        """E5 — a pending question pack is evidence of a live turn."""
        _insert_instances(
            fleet_db, [_instance("asking-1", created_at=_aged())]
        )
        manager = RepoBackedManager(
            fleet_db, packs={"asking-1": object()}
        )

        payload = await self._health(manager)

        assert "asking-1" not in self._flagged_ids(payload)

    async def test_row_with_a_pause_marker_is_not_flagged(self, fleet_db):
        """E6 — the pause marker covers BOTH the question pause and the
        deliberate park tombstone."""
        _insert_instances(
            fleet_db,
            [
                _instance(
                    "parked-1",
                    created_at=_aged(),
                    metadata={
                        "question_paused": {
                            "deliberate": True,
                            "cleared_at": datetime.now(timezone.utc)
                            .isoformat(),
                        }
                    },
                )
            ],
        )
        manager = RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "parked-1" not in self._flagged_ids(payload)

    async def test_non_idle_evidence_free_row_is_still_flagged(self, fleet_db):
        """THE D-1 AMENDMENT: a ``running`` evidence-free row IS a husk.

        ``filter_never_dispatched_ids`` (``idle ∧ version==1``) would
        silently miss it — which is exactly why the detector calls the
        evidence-only method instead.
        """
        _insert_instances(
            fleet_db,
            [_instance("stuck-running-1", status="running", created_at=_aged())],
        )
        manager = RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "stuck-running-1" in self._flagged_ids(payload)

    async def test_version_gt_one_evidence_free_row_is_still_flagged(
        self, fleet_db
    ):
        """THE D-1 AMENDMENT, version leg: ``version > 1`` does not
        manufacture evidence."""
        _insert_instances(
            fleet_db,
            [_instance("bumped-1", version=4, created_at=_aged())],
        )
        manager = RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert "bumped-1" in self._flagged_ids(payload)

    async def test_detector_never_calls_the_state_signature(self, fleet_db):
        """D-1/D-19: the husk detector calls the EVIDENCE-ONLY method and
        NEVER ``filter_never_dispatched_ids``."""
        _insert_instances(
            fleet_db, [_instance("husk-spy-1", created_at=_aged())]
        )
        manager = RepoBackedManager(fleet_db)

        await self._health(manager)

        assert manager._instance_repository.never_dispatched_calls == []
        assert manager._instance_repository.without_evidence_calls

    async def test_disagreement_pin_states_evidence_conjunct_containment(
        self, fleet_db
    ):
        """The pin NAMES the canonical twins and scopes the containment
        to the evidence conjuncts (D-1 amendment) — never the old
        "husk ⊆ canonical ghost set" phrasing, which is false once a
        paused evidence-free row can be flagged."""
        _insert_instances(
            fleet_db, [_instance("husk-pin-1", created_at=_aged())]
        )
        manager = RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        pin = self._husk(payload)["disagreement_pin"]
        assert "filter_never_dispatched_ids" in pin
        assert "_ghost_child_filter" in pin
        assert "never_dispatched_evidence_expr" in pin
        assert "EVIDENCE-CONJUNCT CONTAINMENT ONLY" in pin
        assert "NOT a subset" in pin


class TestF3EvidenceContainment:
    """The containment pin (D-1/D-19): ``filter_never_dispatched_ids``
    ⊆ ``filter_without_any_evidence_ids``.

    PLANNED MUTATION: make ``filter_without_any_evidence_ids`` carry a
    status conjunct (any status) — the containment goes RED (the
    evidence-free ``running`` row falls out of the superset).
    """

    def test_evidence_free_is_a_superset_of_the_canonical_ghost_set(
        self, fleet_db
    ):
        rows = [
            _instance("c-ghost", created_at=_aged()),  # idle/v1, no evidence
            _instance("c-running", status="running", created_at=_aged()),
            _instance("c-bumped", version=3, created_at=_aged()),
            _instance("c-tasked", created_at=_aged()),
            _instance("c-messaged", created_at=_aged()),
            _instance("c-done", status="completed", created_at=_aged()),
        ]
        ids = _insert_instances(fleet_db, rows)
        with Session(fleet_db) as session:
            session.add(Task(instance_id="c-tasked", task_type="PROCESS_MESSAGE"))
            session.add(
                MessageQueue(
                    message_id="m-2",
                    instance_id="c-messaged",
                    content="x",
                    role="user",
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
            )
            session.commit()

        repo = SQLModelInstanceRepository(fleet_db)

        canonical = repo.filter_never_dispatched_ids(ids)
        evidence_free = repo.filter_without_any_evidence_ids(ids)

        assert canonical == {"c-ghost"}
        # Evidence-only is STATUS-BLIND by design: the completed row has
        # no evidence either, so it is in the superset (the caller's
        # row-local non-terminal leg is what excludes it from the husk
        # set — the probe itself does not).
        assert evidence_free == {"c-ghost", "c-running", "c-bumped", "c-done"}
        assert canonical.issubset(evidence_free)

    def test_empty_input_touches_nothing(self, fleet_db):
        repo = SQLModelInstanceRepository(fleet_db)
        assert repo.filter_without_any_evidence_ids([]) == set()


class TestF3xF4Coupling:
    """F3 × F4 — the husk in the OLDEST rows must still be flagged.

    The two known production husks sit in the oldest 730 of 1730 rows —
    OUTSIDE the old 1000-row window. F3 without F4 is correct-but-blind.

    PLANNED MUTATION: restore the single-window scan — the husk falls
    out of the window and this pin goes RED.
    """

    def test_oldest_row_husk_is_flagged_across_windows(self, fleet_db):
        total = ir.FLEET_SCAN_LIMIT + 1
        now = datetime.now(timezone.utc)
        rows = [
            _instance(
                f"filler-{i:06d}",
                created_at=now - timedelta(seconds=i),
                last_activity_at=now - timedelta(seconds=i),
            )
            for i in range(total)
        ]
        # The OLDEST row (the last one in ``created_at DESC`` order) is
        # the husk.
        rows[-1] = _instance("oldest-husk", created_at=now - timedelta(days=30))
        _insert_instances(fleet_db, rows)

        manager = RepoBackedManager(fleet_db)

        payload = asyncio.run(call(_read_tools(manager), "instance_health"))

        flagged = {
            row["instance_id"]
            for detector in payload["detectors"]
            if detector["detector"] == "never-dispatched-husk"
            for row in detector["instances"]
        }
        assert "oldest-husk" in flagged
        # and the scan covered the whole fleet
        assert payload["scan_truncated"] is False
        assert payload["scan_stop_reason"] == "end_of_set"


# ────────────────────────────────────────────────────────────────────────
# F2 — the fleet census
# ────────────────────────────────────────────────────────────────────────


class TestF2Census:
    """F2 — the census counts the WHOLE fleet and reconciles.

    PLANNED MUTATION (one behaviour per run):
      * revert to the windowed Python loop — the coverage pin goes RED
        (the sum drops to the window size, so it no longer equals
        ``total_rows`` / ``total_matching``);
      * drop the KB exclusion from the new repository method — the
        KB-parity pin goes RED (the census total diverges from
        ``instance_list.total_matching``);
      * derive ``counts_by_liveness`` by adding an ``idle`` key — the
        no-idle-in-liveness pin goes RED;
      * copy the repository's constant ``False`` into
        ``totals.scan_truncated`` — no observable change (the value is
        already the literal ``False``); this pin is the SHAPE pin.
    """

    def _fleet(self, engine):
        now = datetime.now(timezone.utc)
        rows = [
            _instance("census-idle-1", status="idle", created_at=now),
            _instance("census-idle-2", status="idle", created_at=now),
            _instance("census-run-1", status="running", created_at=now),
            _instance("census-done-1", status="completed", created_at=now),
            _instance(
                "census-kb-1",
                agent_id="kb-writer",
                status="idle",
                created_at=now,
            ),
        ]
        return _insert_instances(engine, rows)

    async def test_totals_reconcile_with_instance_list(self, fleet_db):
        ids = self._fleet(fleet_db)
        manager = RepoBackedManager(fleet_db)
        tools = _read_tools(manager)

        capacity = await call(tools, "fleet_capacity_summary")
        listing = await call(tools, "instance_list", limit=200, offset=0)

        totals = capacity["totals"]
        assert (
            sum(capacity["counts_by_status"].values())
            == totals["counted_rows"]
        )
        assert totals["counted_rows"] == totals["total_rows"]
        # The census and the census page describe the SAME population
        # (unfiltered): the KB row is excluded from BOTH.
        assert totals["total_rows"] == listing["total_matching"] == 4
        assert totals["reconciled"] is True

    async def test_idle_is_a_status_bucket(self, fleet_db):
        """`idle` appears in ``counts_by_status`` (a STATUS bucket) and
        DELIBERATELY NOT in ``counts_by_liveness`` — the canonical map
        sends idle → "processing"."""
        self._fleet(fleet_db)
        manager = RepoBackedManager(fleet_db)

        payload = await call(_read_tools(manager), "fleet_capacity_summary")

        assert payload["counts_by_status"].get("idle") == 2
        assert "idle" not in payload["counts_by_liveness"]
        # idle → "processing" (the canonical map) and running →
        # "processing" too, so the liveness bucket carries BOTH.
        assert payload["counts_by_liveness"].get("processing") == 3

    async def test_both_marginals_sum_to_counted_rows(self, fleet_db):
        """The free third cross-check (arch §2.3)."""
        self._fleet(fleet_db)
        manager = RepoBackedManager(fleet_db)

        payload = await call(_read_tools(manager), "fleet_capacity_summary")

        counted = payload["totals"]["counted_rows"]
        assert sum(payload["counts_by_status"].values()) == counted
        assert sum(payload["counts_by_agent"].values()) == counted

    async def test_scan_truncated_is_the_literal_false(self, fleet_db):
        self._fleet(fleet_db)
        manager = RepoBackedManager(fleet_db)

        payload = await call(_read_tools(manager), "fleet_capacity_summary")

        assert payload["totals"]["scan_truncated"] is False
        assert payload["scan_truncated"] is False

    async def test_note_carries_both_caveats(self, fleet_db):
        """The note states the filtered-page caveat AND the
        two-statement race disclosure."""
        self._fleet(fleet_db)
        manager = RepoBackedManager(fleet_db)

        payload = await call(_read_tools(manager), "fleet_capacity_summary")

        note = payload["totals"]["note"]
        assert "status filter" in note
        assert "total_matching" in note
        assert "between the two statements" in note

    def test_kb_parity_between_census_and_list(self, fleet_db):
        """With a KB-agent row present, the census total and
        ``instance_list.total_matching`` BOTH exclude it and agree."""
        self._fleet(fleet_db)
        manager = RepoBackedManager(fleet_db)

        census = manager._instance_repository.count_fleet_by_status_and_agent(
            exclude_kb=True
        )
        _rows, total_matching, _trunc = manager._instance_repository.list(
            limit=1000, offset=0, exclude_kb=True
        )

        assert census.total == total_matching == 4

    def test_census_runs_both_statements_on_one_session(self, fleet_db):
        """ONE connection checkout for the COUNT and the GROUP BY
        (arch §2.1)."""
        from sqlalchemy import event

        self._fleet(fleet_db)
        manager = RepoBackedManager(fleet_db)

        checkouts: list[int] = []

        def _on_checkout(dbapi_conn, record_proxy, counter):
            checkouts.append(counter)

        event.listen(fleet_db, "checkout", _on_checkout)
        try:
            manager._instance_repository.count_fleet_by_status_and_agent(
                exclude_kb=True
            )
        finally:
            event.remove(fleet_db, "checkout", _on_checkout)

        assert len(checkouts) == 1

    async def test_a_null_census_group_key_is_bucketed_as_unknown(self):
        """A NULL group key must reach the consumer AS None.

        The repository used to coerce the group keys with ``str()``,
        which turned a NULL into the LITERAL string "None" — a TRUTHY
        key, so the consumer's ``or "unknown"`` bucket never fired and
        the payload carried an agent named "None".
        """
        from daemon.repositories.instance.repository import FleetCounts

        class _NullKeyRepo:
            def count_fleet_by_status_and_agent(self, *, exclude_kb: bool = True):
                return FleetCounts(groups=[("idle", None, 1)], total=1)

        class _Manager:
            engine = None
            _instance_repository = _NullKeyRepo()
            _worker_pool = None

        payload = await asyncio.to_thread(
            ir._build_capacity_summary, _Manager()
        )

        assert "None" not in payload["counts_by_agent"], (
            "a NULL census group key must not surface as the literal "
            f"'None' — got {payload['counts_by_agent']!r}"
        )
        assert payload["counts_by_agent"]["unknown"] == 1

    def test_the_census_does_not_coerce_a_null_group_key_to_a_string(self):
        """The repository hands a NULL group key over AS None.

        The pre-fix comprehension was ``(str(status), str(agent_id),
        ...)`` — ``str(None)`` is the literal "None", which defeats
        the consumer's ``or "unknown"`` bucket.
        """
        source = (
            REPO_ROOT
            / "daemon"
            / "repositories"
            / "instance"
            / "repository.py"
        ).read_text(encoding="utf-8")
        block = source[
            source.index("def count_fleet_by_status_and_agent(") : source.index(
                "def list_by_parent("
            )
        ]
        assert "(str(status), str(agent_id)," not in block, (
            "the census must NOT coerce a NULL group key with str() — "
            "str(None) is the literal 'None'"
        )

    def test_census_is_fleet_size_independent(self, fleet_db):
        """A fleet far larger than one census window still counts every
        row — the defect that created F2 was a census that silently
        degraded past 1000 rows."""
        now = datetime.now(timezone.utc)
        rows = [
            _instance(
                f"bulk-{i:05d}",
                created_at=now - timedelta(seconds=i),
                last_activity_at=now - timedelta(seconds=i),
                status="running" if i % 2 else "completed",
            )
            for i in range(1200)
        ]
        ids = _insert_instances(fleet_db, rows)
        manager = RepoBackedManager(fleet_db)

        census = manager._instance_repository.count_fleet_by_status_and_agent(
            exclude_kb=True
        )

        assert census.total == len(ids)
        assert sum(c for _s, _a, c in census.groups) == len(ids)


# ────────────────────────────────────────────────────────────────────────
# F8 — read-path purity
# ────────────────────────────────────────────────────────────────────────

FULL_GET_PAYLOAD_KEYS = (
    "instance_id",
    "agent_id",
    "agent_name",
    "agent_tag",
    "status",
    "liveness",
    "instance_metadata",
    "project_id",
    "parent_id",
    "children",
    "children_count",
    "ancestors",
    "descendants",
    "created_at",
    "updated_at",
    "last_activity_at",
    "age_seconds",
    "idle_seconds",
    "stale",
    "paused_at",
    "pause_marker",
    "pending_question_pack",
    "current_task",
    "last_messages",
    "spawned_by",
    "mission_ref",
)

#: The four refusal texts, BYTE-FOR-BYTE from
#: ``daemon/tools/instance.py::_resolve_instance_id`` — the read-scoped
#: resolver must reproduce them exactly (the operator-facing text does
#: not change).
NOT_FOUND_NO_MATCH = (
    "ERROR: instance 'nope-1' not found and no similar instance found. "
    "Please check the instance ID or spawn a new instance for your task."
)
NOT_FOUND_ONE_MATCH = (
    "ERROR: instance 'nope-1' not found. "
    "Did you mean 'real-1'? Please retry with the corrected instance_id."
)
NOT_FOUND_MULTI_MATCH = (
    "ERROR: instance 'nope-1' not found. Multiple similar instances found: "
    "'real-1', 'real-2'. Please retry with the correct instance_id."
)
EMPTY_ID = "ERROR: instance_id cannot be empty"


class TestF8ReadPurity:
    """F8 — ``instance_get`` is genuinely side-effect-free.

    PLANNED MUTATION: point the resolver back at
    ``manager.get_instance`` (the hydration seam) — the spy pin goes RED
    (the seam fires), and with a fixture row whose ``agent_tag`` is
    stale the ROW-IDENTITY pin goes RED too (the re-elevation write
    lands).
    """

    async def _get(self, manager, instance_id="real-1"):
        return await call(_read_tools(manager), "instance_get", id=instance_id)

    def _manager(self, fleet_db, **kw):
        now = datetime.now(timezone.utc)
        _insert_instances(
            fleet_db,
            [
                _instance(
                    "real-1",
                    created_at=now,
                    last_activity_at=now,
                    metadata={"title": "t", "question_paused": None},
                )
            ],
        )
        # Put a second row in so a fuzzy hit has something to hit.
        _insert_instances(
            fleet_db, [_instance("real-2", created_at=now, last_activity_at=now)]
        )
        manager = RepoBackedManager(fleet_db, **kw)
        return manager

    async def test_cold_read_triggers_no_discovery_and_no_hydration(
        self, fleet_db
    ):
        manager = self._manager(fleet_db)

        payload = await self._get(manager)

        assert manager.mcp_preload_calls == []
        assert manager.get_instance_calls == []
        assert payload["instance_id"] == "real-1"

    async def test_cold_read_leaves_the_row_byte_identical(self, fleet_db):
        """No write: the row (including ``agent_tag``, ``agent_dir`` and
        ``metadata``) is identical before and after the read."""
        manager = self._manager(fleet_db)
        repo = manager._instance_repository

        before = repo.get("real-1").to_dict()
        await self._get(manager)
        after = repo.get("real-1").to_dict()

        assert before == after

    async def test_full_payload_still_returned(self, fleet_db):
        """Purity must not cost functionality."""
        manager = self._manager(fleet_db)

        payload = await self._get(manager)

        for key in FULL_GET_PAYLOAD_KEYS:
            assert key in payload, f"payload lost {key!r}"
        assert payload["current_task"] is None
        assert payload["last_messages"] == []
        assert payload["children"] == []

    async def test_fuzzy_miss_refuses_with_the_suggestion(self, fleet_db):
        """The fuzzy leg still works — and behaves EXACTLY like the write
        path: it refuses with the corrected id rather than
        auto-resolving, so a short form is a two-step call, unchanged by
        F8."""
        manager = self._manager(fleet_db, near_matches=["real-1"])
        tools = _read_tools(manager)

        out = await by_name(tools, "instance_get").ainvoke({"id": "real-"})

        assert out == (
            "Error: INSTANCE_GET REFUSED — reason=instance-not-found: "
            "ERROR: instance 'real-' not found. "
            "Did you mean 'real-1'? Please retry with the corrected instance_id."
        )
        # the fuzzy matcher itself WAS consulted (the repo-only one)
        assert manager.get_instance_calls == []
        assert manager.mcp_preload_calls == []

    @pytest.mark.parametrize(
        ("near_matches", "expected"),
        [
            ([], NOT_FOUND_NO_MATCH),
            (["real-1"], NOT_FOUND_ONE_MATCH),
            (["real-1", "real-2"], NOT_FOUND_MULTI_MATCH),
        ],
    )
    async def test_refusal_texts_are_byte_identical(
        self, fleet_db, near_matches, expected
    ):
        manager = self._manager(fleet_db, near_matches=near_matches)
        tools = _read_tools(manager)

        out = await by_name(tools, "instance_get").ainvoke({"id": "nope-1"})

        assert out == (
            f"Error: INSTANCE_GET REFUSED — reason=instance-not-found: {expected}"
        )

    async def test_empty_id_guard_is_byte_identical(self, fleet_db):
        manager = self._manager(fleet_db)
        tools = _read_tools(manager)

        out = await by_name(tools, "instance_get").ainvoke({"id": ""})

        assert out == (
            f"Error: INSTANCE_GET REFUSED — reason=instance-not-found: {EMPTY_ID}"
        )


class TestF8DocSurface:
    """F8 — the doc surface states CURRENT semantics (the D-V2 lesson).

    PLANNED MUTATION: revert any docstring — the corresponding text pin
    goes RED.
    """

    def test_instance_get_doc_states_the_cold_read_contract(self):
        tools = ir.create_instance_read_tools(
            FakeFleetManager(), current_instance_id="m", agent_id="maintenancer"
        )
        doc = by_name(tools, "instance_get")._full_doc_ or ""

        assert "never triggers MCP schema discovery" in doc
        assert "a cold instance stays cold" in doc
        # the residual disclosure (arch §5 correction 1)
        assert "agent_tag" in doc and "agent_dir" in doc
        assert "no longer warm the instance cache" in doc
        assert "now becomes a successful plain read" in doc
        assert "Side effects:\n    NONE." in doc

    def test_health_doc_states_seven_detectors(self):
        tools = ir.create_instance_read_tools(
            FakeFleetManager(), current_instance_id="m", agent_id="maintenancer"
        )
        doc = by_name(tools, "instance_health")._full_doc_ or ""

        assert "seven read-only detector classes" in doc
        assert "never-dispatched-husk" in doc
        assert "DEFAULT_HUSK_MIN_AGE_SECONDS" in doc
        assert "`idle_seconds` is the AGE since `created_at`" in doc

    def test_health_doc_states_the_live_fleet_paging_disclosure(self):
        tools = ir.create_instance_read_tools(
            FakeFleetManager(), current_instance_id="m", agent_id="maintenancer"
        )
        doc = by_name(tools, "instance_health")._full_doc_ or ""

        assert "OFFSET WINDOW LOOP" in doc
        assert "scan_stop_reason" in doc
        assert "time_budget" in doc
        assert "window_cap" in doc
        assert "end_of_set" in doc

    def test_list_doc_states_the_honest_truncated_contract(self):
        tools = ir.create_instance_read_tools(
            FakeFleetManager(), current_instance_id="m", agent_id="maintenancer"
        )
        doc = by_name(tools, "instance_list")._full_doc_ or ""

        assert "total_matching > filters_applied.offset + count" in doc

    def test_list_doc_states_the_f12_truth(self):
        tools = ir.create_instance_read_tools(
            FakeFleetManager(), current_instance_id="m", agent_id="maintenancer"
        )
        doc = by_name(tools, "instance_list")._full_doc_ or ""

        # wrapped-safe: the sentence breaks across lines in the source
        assert "applied in" in doc and "SQL" in doc
        assert "CORRECT" in doc
        assert "F12" in doc

    def test_category_doc_says_seven_detectors(self):
        assert "seven detector classes" in ir.CATEGORY_DOC

    def test_module_doc_says_five_tools(self):
        assert "5 tools" in (ir.__doc__ or "")


# ────────────────────────────────────────────────────────────────────────
# F5 — the two dead-job measures
# ────────────────────────────────────────────────────────────────────────


class TestF5Measures:
    """F5 — `dead_jobs_total` + `dlq_note`, `dlq_depth` untouched.

    PLANNED MUTATION: redefine `dlq_depth` to the admission-state count
    (the "unify" the design rejected) — the value pin goes RED whenever
    the snapshot table and the canonical state disagree, and the
    "different tables" doc assertion goes RED with it. Dropping the
    `dlq_note` key or the doc paragraph goes RED on its own pin.
    """

    def _capacity(self, manager):
        return _read_tools(manager)

    async def test_both_keys_present_and_consistent(self, fleet_db):
        now = datetime.now(timezone.utc)
        _insert_instances(fleet_db, [_instance("m-1", created_at=now)])
        with Session(fleet_db) as session:
            for i in range(3):
                session.add(
                    JobItem(
                        agent_id="worker",
                        agent_dir="/tmp/agents/worker",
                        message=f"job-{i}",
                        instance_id="m-1",
                        job_type="task",
                        admission_state="dead",
                    )
                )
            session.commit()

        manager = RepoBackedManager(fleet_db)

        payload = await call(
            self._capacity(manager), "fleet_capacity_summary"
        )

        assert "dlq_depth" in payload
        assert "dead_jobs_total" in payload
        assert "dlq_note" in payload
        assert payload["dead_jobs_total"] == 3
        assert payload["dead_jobs_total"] == sum(
            int(row["dead"]) for row in payload["queue_depth"]
        )

    async def test_dlq_depth_keeps_its_own_meaning(self, fleet_db):
        """`dlq_depth` still counts the SNAPSHOT table — here it is 0
        while three canonical dead jobs exist, and that is NOT an error
        (the drift-recovery path finalizes to `dead` with no snapshot).
        """
        now = datetime.now(timezone.utc)
        _insert_instances(fleet_db, [_instance("m-2", created_at=now)])
        with Session(fleet_db) as session:
            session.add(
                JobItem(
                    agent_id="worker",
                    agent_dir="/tmp/agents/worker",
                    message="no-snapshot",
                    instance_id="m-2",
                    job_type="task",
                    admission_state="dead",
                )
            )
            session.commit()

        manager = RepoBackedManager(fleet_db)

        payload = await call(
            self._capacity(manager), "fleet_capacity_summary"
        )

        assert payload["dlq_depth"] == 0
        assert payload["dead_jobs_total"] >= 1
        assert payload["dlq_depth"] <= payload["dead_jobs_total"]

    def test_doc_distinguishes_the_two_tables(self):
        tools = ir.create_instance_read_tools(
            FakeFleetManager(), current_instance_id="m", agent_id="maintenancer"
        )
        doc = by_name(tools, "fleet_capacity_summary")._full_doc_ or ""

        assert "dead_letter_items" in doc
        assert "dead_jobs_total" in doc
        assert "dlq_depth <= dead_jobs_total" in doc
        assert "**not** an error" in doc
        # the category doc carries the same distinction
        assert "dead_letter_items" in ir.CATEGORY_DOC
        assert "dead_jobs_total" in ir.CATEGORY_DOC

    async def test_note_rides_the_payload(self, fleet_db):
        manager = RepoBackedManager(fleet_db)

        payload = await call(
            self._capacity(manager), "fleet_capacity_summary"
        )

        note = payload["dlq_note"]
        assert "dead_letter_items" in note
        assert "job_queue_items" in note
        assert "different tables" in note


# ────────────────────────────────────────────────────────────────────────
# F1 — the instance_fleet_summary alias
# ────────────────────────────────────────────────────────────────────────


class TestF1Alias:
    """F1 — BOTH names resolve, ONE builder, byte-identical payloads.

    PLANNED MUTATION (one behaviour per run):
      * delete the alias closure — the alias-resolution pin goes RED;
      * remove ONE registry-list entry — the frozen-name drift pin
        (tests/unit/tools/test_frozen_tool_name_discovery.py) goes RED;
      * drop the alias ``_full_doc_`` — the tool_help-body pin goes RED;
      * give the alias its OWN builder — the byte-identical pin goes RED
        (the two payloads drift on ``generated_at`` ordering at best).
    """

    def _tools(self, manager=None):
        return ir.create_instance_read_tools(
            manager or FakeFleetManager(),
            current_instance_id="m",
            agent_id="maintenancer",
        )

    def test_both_names_are_in_the_factory(self):
        names = {t.name for t in self._tools()}
        assert names == {
            "instance_list",
            "instance_get",
            "instance_health",
            "fleet_capacity_summary",
            "instance_fleet_summary",
        }

    def test_the_alias_is_a_literal_closure_not_generated(self):
        """AST discovery must find it — a code-generated alias would NOT
        be discovered (the hard constraint that forecloses
        "alias via helper")."""
        discovered = discover_source_only_tool_names()
        assert "instance_fleet_summary" in discovered

    def test_both_names_in_both_registry_lists(self):
        assert "instance_fleet_summary" in set(DYNAMIC_TOOL_NAMES)
        assert "instance_fleet_summary" in set(KNOWN_TOOL_NAMES)

    async def test_payloads_are_byte_identical(self, fleet_db):
        now = datetime.now(timezone.utc)
        _insert_instances(
            fleet_db,
            [
                _instance("alias-1", status="idle", created_at=now),
                _instance("alias-2", status="running", created_at=now),
            ],
        )
        manager = RepoBackedManager(fleet_db)
        tools = self._tools(manager)

        live = json.loads(
            await by_name(tools, "fleet_capacity_summary").ainvoke({})
        )
        alias = json.loads(
            await by_name(tools, "instance_fleet_summary").ainvoke({})
        )

        # ONE builder ⇒ identical payloads. `generated_at` is the only
        # key that legitimately differs (two calls, two timestamps).
        live.pop("generated_at")
        alias.pop("generated_at")
        assert alias == live

    async def test_both_names_have_non_empty_help_bodies(self):
        """Full docs are keyed by the tool NAME — an alias without its
        own ``_full_doc_`` would render an empty ``tool_help`` body."""
        for name in ("fleet_capacity_summary", "instance_fleet_summary"):
            doc = by_name(self._tools(), name)._full_doc_ or ""
            assert doc.strip(), f"{name} has an empty _full_doc_"

    def test_the_alias_doc_says_it_is_an_alias(self):
        doc = by_name(self._tools(), "instance_fleet_summary")._full_doc_ or ""
        assert "Alias of" in doc
        assert "fleet_capacity_summary" in doc

    def test_the_live_doc_mentions_the_alias(self):
        doc = by_name(self._tools(), "fleet_capacity_summary")._full_doc_ or ""
        assert "instance_fleet_summary" in doc

    def test_the_category_doc_lists_the_alias(self):
        assert "instance_fleet_summary" in ir.CATEGORY_DOC

    async def test_the_alias_gates_authority_too(self):
        """The body gate is not cosmetic: a literal tool name in an allow
        list bypasses the category gate, so every body checks the same
        shared helper."""
        tools = ir.create_instance_read_tools(
            FakeFleetManager(), current_instance_id="m", agent_id="leader"
        )
        out = await by_name(tools, "instance_fleet_summary").ainvoke({})
        assert "not permitted" in out

    def test_the_planning_rows_are_annotated_not_rewritten(self):
        """The stale pre-rename rows carry the pointer to the rename
        verdict (annotation, not rewrite — they are the historical Q1
        record)."""
        rec = REPO_ROOT / ".agents" / "shared" / "planning" / "nest-ops" / (
            "architecture-recommendation.md"
        )
        if not rec.exists():
            pytest.skip("the nest-ops planning record is not in this tree")
        text = rec.read_text(encoding="utf-8")
        assert text.count("2026-10-24 ANNOTATION") == 2
        assert "PRE-RENAME SPELLING" in text
        # the rename verdict rows themselves are untouched
        assert "Rename `instance_fleet_summary` → `fleet_capacity_summary`" in text


# ────────────────────────────────────────────────────────────────────────
# The tidier fold — the parent-status cache discipline + the husk
# evidence-leg disclosure (two ADDITIVE observability fixes).
# ────────────────────────────────────────────────────────────────────────


class _FlakyParentRepo:
    """A repository double whose ``get`` FAILS ONCE for one parent.

    The failure models a transient database error on the parent-status
    probe. Before the cache-discipline fix the failure was memoized as
    "the parent is gone" — and a failed probe is indistinguishable from
    a missing parent to the detectors, so EVERY LATER child of that
    parent was flagged as a false orphan / dead-parent row.
    """

    def __init__(self, parent_row, *, fail_first: bool = True):
        self.parent_row = parent_row
        self.fail_first = fail_first
        self.calls: list[str] = []

    def get(self, iid):
        self.calls.append(iid)
        if self.fail_first and self.calls.count(iid) == 1:
            raise RuntimeError("transient parent-status probe failure")
        if iid == self.parent_row.instance_id:
            return self.parent_row
        return None


class _UnlistedParentFleetManager:
    """A manager double over two children of ONE parent.

    The parent row is deliberately NOT in the fleet listing — it is
    only reachable through the repository. That is what forces the
    parent-status PROBE (the ``status_by_id`` fast path never applies).
    """

    def __init__(self, children: list[FakeRow], repo):
        self.rows = list(children)
        self._instance_repository = repo
        self._task_repo = FakeTaskRepo()
        self._question_manager = FakeQuestionManager()
        self.engine = None

    def list_instances(self, limit=10, offset=0, status=None, **kw):
        matching = [r for r in self.rows if status is None or r.status == status]
        matching.sort(key=lambda r: r.instance_id)
        total = len(matching)
        page = matching[offset : offset + limit]
        out = []
        for row in page:
            info = row.to_dict()
            info["children"] = []
            out.append(info)
        return out, total, False


class TestParentStatusCache:
    """The parent-status cache holds RESOLVED FACTS only.

    PLANNED MUTATION (one behaviour per run):
      * cache the probe-failure leg again → the no-poisoning pin RED
        (the second child is flagged as a false orphan);
      * stop caching a genuinely-missing parent → the cost pin RED
        (the probe runs once per child).
    """

    def _child(self, iid: str) -> FakeRow:
        return FakeRow(
            iid,
            agent_id="worker",
            parent_id="parent-1",
            status="waiting_children",
        )

    def _parent(self) -> FakeRow:
        return FakeRow("parent-1", agent_id="worker", status="running")

    def test_a_probe_failure_is_not_cached(self):
        """The direct contract: a raised probe leaves the cache EMPTY."""
        repo = _FlakyParentRepo(self._parent())
        cache: dict[str, str | None] = {}

        status = ir._parent_status_of(repo, {}, "parent-1", cache)

        assert status is None
        assert "parent-1" not in cache, (
            "a FAILED probe must not be memoized — the parent's liveness "
            "is unknown, and a cached 'gone' poisons every later child"
        )

    def test_a_failed_probe_is_retried_on_the_next_child(self):
        """The next child RE-PROBES and gets the truth."""
        repo = _FlakyParentRepo(self._parent())
        cache: dict[str, str | None] = {}

        first = ir._parent_status_of(repo, {}, "parent-1", cache)
        second = ir._parent_status_of(repo, {}, "parent-1", cache)

        assert first is None
        assert second == "running"
        assert repo.calls == ["parent-1", "parent-1"]

    def test_a_genuinely_missing_parent_IS_cached(self):
        """A MISSING parent row is a stable fact — it stays cached."""
        calls: list[str] = []

        class _MissingRepo:
            def get(self, iid):
                calls.append(iid)
                return None

        cache: dict[str, str | None] = {}
        first = ir._parent_status_of(_MissingRepo(), {}, "parent-1", cache)
        second = ir._parent_status_of(_MissingRepo(), {}, "parent-1", cache)

        assert first is None and second is None
        assert cache["parent-1"] is None
        assert calls == ["parent-1"], (
            "a resolved 'the parent row is gone' fact is cached — the "
            "probe must not run again for the same parent"
        )

    async def test_a_transient_failure_does_not_poison_the_next_child(self):
        """END-TO-END: only the child that HIT the failure is flagged.

        Pre-fix, the second child read the poisoned cache entry and was
        flagged as a dead-parent row even though its parent was alive.
        """
        repo = _FlakyParentRepo(self._parent())
        manager = _UnlistedParentFleetManager(
            [self._child("child-1"), self._child("child-2")], repo
        )
        payload = await call(_read_tools(manager), "instance_health")
        detector = next(
            d
            for d in payload["detectors"]
            if d["detector"] == "waiting-on-a-dead-parent"
        )
        flagged = {row["instance_id"] for row in detector["instances"]}

        assert flagged == {"child-1"}, (
            "only the child that hit the transient failure may be "
            f"flagged; got {sorted(flagged)}"
        )
        assert repo.calls == ["parent-1", "parent-1"], (
            "the second child must RE-PROBE, not read a poisoned entry"
        )


class TestF8OffLoopReads:
    """The two blocking reads run OFF the event loop (RIDE-ALONG 1).

    ``repo.get`` is a database round-trip and ``find_near_instance``
    lists the fleet — both are SYNC. Called straight from the async
    tool body they BLOCK the event loop for the duration of the query.
    The module's own convention (``asyncio.to_thread`` — cf. the
    ``repo.get`` calls further down the same module and
    ``daemon/graph.py``) applies to both legs of the resolver.

    The double records the THREAD each call ran on; the pin asserts
    neither ran on the event-loop thread.

    PLANNED MUTATION: call either leg directly (no ``to_thread``) → the
    matching pin RED.
    """

    async def test_the_exact_read_runs_off_the_event_loop(self, fleet_db):
        _insert_instances(fleet_db, [_instance("off-loop-1")])
        manager = RepoBackedManager(fleet_db)
        threads: list[int] = []
        original_get = manager._instance_repository.get

        def recording_get(iid):
            threads.append(threading.get_ident())
            return original_get(iid)

        manager._instance_repository.get = recording_get  # type: ignore[method-assign]

        out = await by_name(_read_tools(manager), "instance_get").ainvoke(
            {"id": "off-loop-1"}
        )

        assert "off-loop-1" in out
        assert threads, "the exact read must have happened"
        assert threads[0] != threading.get_ident(), (
            "the repository read must run OFF the event loop "
            "(asyncio.to_thread) — a sync round-trip on the loop blocks "
            "every other task in the daemon"
        )

    async def test_the_fuzzy_leg_runs_off_the_event_loop(self, fleet_db):
        """The exact read MISSES, so the fuzzy leg is the one that runs."""
        manager = RepoBackedManager(fleet_db, near_matches=["near-1"])
        threads: list[int] = []
        original = manager.find_near_instance

        def recording(instance_id, max_distance=7):
            threads.append(threading.get_ident())
            return original(instance_id, max_distance=max_distance)

        manager.find_near_instance = recording  # type: ignore[method-assign]

        out = await by_name(_read_tools(manager), "instance_get").ainvoke(
            {"id": "ghost-1"}
        )

        assert "near-1" in out, "the fuzzy leg must have run"
        assert threads, "the fuzzy match must have happened"
        assert threads[0] != threading.get_ident(), (
            "the fuzzy match must run OFF the event loop "
            "(asyncio.to_thread)"
        )


class TestHuskEvidenceLegsDegradation:
    """A DEGRADED evidence-leg scan is visible in the payload.

    The pre-fix shape swallowed a probe failure into an empty husk
    list, so the operator read a clean ``count: 0`` on a scan that
    never actually looked — indistinguishable from "there are no
    husks".

    PLANNED MUTATION (one behaviour per run):
      * drop the ``evidence_legs`` field from the payload → RED;
      * report "ok" on a failed probe → RED;
      * report "degraded" on a HEALTHY scan → RED.
    """

    async def _health(self, manager):
        return await call(_read_tools(manager), "instance_health")

    def _husk_row(self, payload):
        for detector in payload["detectors"]:
            if detector["detector"] == "never-dispatched-husk":
                return detector
        raise AssertionError("never-dispatched-husk detector is missing")

    async def test_a_healthy_scan_reports_ok(self, fleet_db):
        _insert_instances(
            fleet_db, [_instance("husk-1", created_at=_aged())]
        )
        manager = RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert payload["evidence_legs"] == "ok"
        assert self._husk_row(payload)["evidence_legs"] == "ok"

    async def test_a_failed_evidence_probe_reports_degraded(self, fleet_db):
        """The E1/E2 evidence probe fails → the scan says so."""
        _insert_instances(
            fleet_db, [_instance("husk-1", created_at=_aged())]
        )
        manager = RepoBackedManager(fleet_db)

        def _boom(_ids):
            raise RuntimeError("evidence probe unavailable")

        manager._instance_repository.filter_without_any_evidence_ids = _boom

        payload = await self._health(manager)

        assert payload["evidence_legs"] == "degraded"
        row = self._husk_row(payload)
        assert row["evidence_legs"] == "degraded"
        assert row["count"] == 0

    async def test_a_failed_job_item_probe_reports_degraded(self, fleet_db):
        """The E3 JobItem probe cannot run → the scan says so."""
        _insert_instances(
            fleet_db, [_instance("husk-1", created_at=_aged())]
        )
        manager = RepoBackedManager(fleet_db)

        def _no_engine(_manager, _ids):
            return None

        original = ir._job_item_counts_by_instance
        ir._job_item_counts_by_instance = _no_engine
        try:
            payload = await self._health(manager)
        finally:
            ir._job_item_counts_by_instance = original

        assert payload["evidence_legs"] == "degraded"
        assert self._husk_row(payload)["evidence_legs"] == "degraded"

    async def test_no_candidates_is_not_degraded(self, fleet_db):
        """No candidates at all — "0 husks" is TRUE, not degraded."""
        _insert_instances(fleet_db, [_instance("fresh-1")])
        manager = RepoBackedManager(fleet_db)

        payload = await self._health(manager)

        assert payload["evidence_legs"] == "ok"
