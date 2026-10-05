"""Pydantic response models for the Snapshots read-only HTTP surface.

Sibling module — mirrors the ``daemon/routers/skill_schemas.py`` split
(the catch-all ``daemon/routers/schemas.py`` is 2136+ lines; new domain
schemas land in their own sibling to keep that file from growing).

* :class:`SnapshotListResponse` — list envelope ``{items, total}``
  (D-1 binding — the envelope key is ``items``, NOT ``snapshots``;
  per ``sequencing.md`` §1 D-1).
* :class:`SnapshotResponse` — detail endpoint body. The full
  ``to_dict()`` shape minus the (always-present, possibly empty)
  ``digest`` key. ``?include=digest`` flips the digest from ``{}`` to
  the full JSONB payload.
* :class:`SnapshotUsageMetricsResponse` — re-export of the
  catch-all-schemas model (pass 4 amendment, blocker #4) so the new
  router has a single import surface for the three response models it
  uses (``from .snapshot_schemas import (SnapshotListResponse,
  SnapshotResponse, SnapshotUsageMetricsResponse)``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Re-export — the canonical home is `daemon/routers/schemas.py` (line
# 1781); the new router imports it through this sibling for one-stop
# model wiring.
from daemon.routers.schemas import SnapshotUsageMetricsResponse  # noqa: F401


class SnapshotResponse(BaseModel):
    """Detail-endpoint body for ``GET /api/snapshots/{id}``.

    The full ``to_dict()`` of a :class:`Snapshot` row, with one nuance:
    ``digest`` is **present-but-empty** (``{}``) by default; the
    detail handler flips it to the full JSONB dict when the request
    carries ``?include=digest`` (D7 — list payloads stay lean).
    """

    id: str
    project_id: str
    created_by_agent_id: str
    target_instance_id: str
    title: str
    task_summary: str = ""
    domain_tags: list[str] = Field(default_factory=list)
    status: str
    supersedes_snapshot_id: str | None = None
    repo_path: str | None = None
    vcs_type: str | None = None
    git_sha: str | None = None
    git_branch: str | None = None
    git_dirty: bool = False
    runtime_version: str
    effective_model: str | None = None
    digest: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class SnapshotListItem(SnapshotResponse):
    """List-item body for ``GET /api/snapshots``.

    Same shape as :class:`SnapshotResponse` — the list handler strips
    ``digest`` (D7) and ``task_summary`` (sequencing §1 addendum A-2)
    AFTER construction so the wire payload stays lean. We extend
    ``SnapshotResponse`` (rather than re-declaring every field) so the
    two shapes share a single source of truth; the strip lives in
    ``daemon/routers/snapshots.py::_to_list_item``.
    """


class SnapshotListResponse(BaseModel):
    """List envelope for ``GET /api/snapshots``.

    Mirrors the project convention at
    ``daemon/routers/blueprints.py:122-126``
    (``BlueprintListResponse``) and at ``daemon/routers/missions.py``
    (``{missions, total, ...}``). Envelope key is ``items`` (D-1
    binding — supersedes the pre-canon design-spec text mentioning
    ``snapshots``; sequencing §1 D-1).
    """

    items: list[SnapshotListItem]
    total: int
