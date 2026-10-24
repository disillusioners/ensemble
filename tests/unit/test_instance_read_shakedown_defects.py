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
