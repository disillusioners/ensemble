"""Shared fixtures for the project-skill-source test packs.

The helper-trio convention (``tests/helpers/<feature>_fixtures.py``):
canon-tree builders, fake repositories, and a fake embedding service —
everything the unit + integration packs share. NO real daemon, NO live
paths (tmp_path only), NO network.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest

from daemon.repositories.project_skill_file.models import (  # noqa: F401
    ProjectSkillEmbedding,
    ProjectSkillFile,
    ProjectSkillUsageRecord,
)
from daemon.repositories.project_skill_file.repository import (
    ProjectSkillEmbeddingRepository,
    ProjectSkillFileRepository,
    ProjectSkillUsageRecordRepository,
)
from daemon.services.project_skill_source.service import ProjectSkillSourceService

TEST_PROJECT_ID = "11111111-2222-3333-4444-555555555555"
OTHER_PROJECT_ID = "99999999-8888-7777-6666-555555555555"

VALID_MANIFEST = {
    "schema_version": 1,
    "version": "1.0.0",
    "description": "A demo skill body description",
    "category": "demo",
    "scope": "project",
    "tiers": [1],
    "assets": [],
    "loads": [],
}
# NOTE: ``name``/``body`` are deliberately NOT in the template — the
# ``write_skill`` helper fills them from the target dir name / actual
# body filename, so per-test intentional-bad values always win.


def write_skill(
    root,
    namespace: str,
    name: str,
    body_text: str = "Demo body content.\n",
    manifest: Optional[dict[str, Any]] = None,
    manifest_text: Optional[str] = None,
    with_body: bool = True,
    body_name: Optional[str] = None,
) -> str:
    """Write one canon skill dir under ``<root>/.agents/<ns>/skills/<name>/``.

    Returns the skill dir path. ``manifest_text`` overrides the serialized
    manifest entirely (for invalid-JSON fixtures); ``with_body=False``
    omits the body file (body_missing fixture).
    """
    skill_dir = os.path.join(root, ".agents", namespace, "skills", name)
    os.makedirs(skill_dir, exist_ok=True)
    body = body_name or (f"{name}.md")
    if manifest_text is None:
        manifest_dict = dict(manifest if manifest is not None else VALID_MANIFEST)
        # Fill ONLY what the caller left unset — intentional bad values
        # (name mismatch, absolute body, …) must survive.
        manifest_dict.setdefault("name", name)
        manifest_dict.setdefault("body", body)
        manifest_text = json.dumps(manifest_dict, indent=2)
    with open(os.path.join(skill_dir, "skill.json"), "w", encoding="utf-8") as fh:
        fh.write(manifest_text)
    if with_body:
        with open(os.path.join(skill_dir, body), "w", encoding="utf-8") as fh:
            fh.write(body_text)
    return skill_dir


def write_legacy_bump_version(root) -> str:
    """The FR-VAL-1 negative fixture: the legacy ``.agents/project-skills/
    bump-version`` shape — different schema, no ``skills/`` segment, no
    ``schema_version``. Fails BOTH dual-leg exclusion legs."""
    legacy_dir = os.path.join(root, ".agents", "project-skills", "bump-version")
    os.makedirs(legacy_dir, exist_ok=True)
    legacy_manifest = {
        "id": "legacy-bump-version",
        "triggers": ["bump", "version"],
        "default_bump": "patch",
        "supported_project_types": ["software"],
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    with open(os.path.join(legacy_dir, "skill.json"), "w", encoding="utf-8") as fh:
        json.dump(legacy_manifest, fh)
    with open(os.path.join(legacy_dir, "bump-version.md"), "w", encoding="utf-8") as fh:
        fh.write("Legacy bump-version instructions.\n")
    return legacy_dir


@dataclass
class FakeInstance:
    instance_id: str
    instance_metadata: dict[str, Any] = field(default_factory=dict)
    project_id: Optional[str] = None


class FakeInstanceRepository:
    def __init__(self, instances: dict[str, FakeInstance] | None = None) -> None:
        self.instances = instances or {}

    def get(self, instance_id: str) -> Optional[FakeInstance]:
        return self.instances.get(instance_id)


@dataclass
class FakeProject:
    project_id: str
    main_directory: Optional[str] = None
    related_directories: list[str] = field(default_factory=list)


class FakeProjectRepository:
    """``get`` + ``get_by_directory`` semantics mirroring the real repo's
    binding predicate (main_directory == entry OR related contains entry)."""

    def __init__(self, projects: list[FakeProject] | None = None) -> None:
        self.projects = projects or []

    def get(self, project_id: str) -> Optional[FakeProject]:
        for project in self.projects:
            if project.project_id == project_id:
                return project
        return None

    def get_by_directory(self, directory: str) -> list[FakeProject]:
        return [
            project
            for project in self.projects
            if project.main_directory == directory
            or directory in (project.related_directories or [])
        ]


class FakeEmbeddingService:
    """Records generation calls; returns deterministic vectors.

    ``fail_mode``: None (succeed) | "generate" (generate raises) |
    "empty" (generate returns []) | "embed" (embed_text raises).
    """

    def __init__(self, queries: int = 3, fail_mode: Optional[str] = None) -> None:
        self.queries = queries
        self.fail_mode = fail_mode
        self.generate_calls: list[str] = []
        self.embed_calls: list[str] = []
        self.generation_bodies: list[str] = []

    async def generate_trigger_queries(self, skill: Any) -> list[str]:
        self.generate_calls.append(getattr(skill, "name", "?"))
        self.generation_bodies.append(getattr(skill, "content", "") or "")
        if self.fail_mode == "generate":
            raise RuntimeError("provider unreachable")
        if self.fail_mode == "empty":
            return []
        return [f"trigger query {i} for {getattr(skill, 'name', '?')}" for i in range(self.queries)]

    async def embed_text(self, message: str) -> list[float]:
        self.embed_calls.append(message)
        if self.fail_mode == "embed":
            raise RuntimeError("embedding endpoint down")
        return [float(len(message)), 1.0, 2.0]


@pytest.fixture
def canon_root(tmp_path):
    """A tmp root with a three-skill canon: two in ``tester`` (one with a
    same-namespace loads + supersedes), one in ``shared`` (P6 namespace
    coverage)."""
    root = str(tmp_path / "proj-root")
    os.makedirs(root, exist_ok=True)
    write_skill(root, "tester", "alpha-skill", body_text="Alpha body.\n")
    write_skill(
        root,
        "tester",
        "beta-skill",
        body_text="Beta body.\n",
        manifest={
            **VALID_MANIFEST,
            "name": "beta-skill",
            "loads": ["alpha-skill"],
            "supersedes": {"store": "dynamic-skill", "skill_id": "12345678-1111-2222-3333-444444444444"},
        },
    )
    write_skill(root, "shared", "gamma-skill", body_text="Gamma body.\n")
    return root


@pytest.fixture
def sqlite_engine(tmp_path):
    from sqlmodel import SQLModel, create_engine

    # Register the lane models (and the legacy skill models for the
    # zero-FK-child-write assertions) BEFORE create_all.
    import daemon.repositories.skill.models  # noqa: F401

    engine = create_engine(f"sqlite:///{tmp_path}/pss.db")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def make_service(
    sqlite_engine=None,
    instance_repo=None,
    project_repo=None,
    embedding_service=None,
    extra_roots=None,
    refresh_debounce_s: float = 0.05,
) -> ProjectSkillSourceService:
    file_repo = (
        ProjectSkillFileRepository(sqlite_engine) if sqlite_engine is not None else None
    )
    embed_repo = (
        ProjectSkillEmbeddingRepository(sqlite_engine)
        if sqlite_engine is not None
        else None
    )
    return ProjectSkillSourceService(
        instance_repository=instance_repo,
        project_repository=project_repo,
        file_repository=file_repo,
        embedding_repository=embed_repo,
        embedding_service=embedding_service,
        llm_config={"model": "coding"},
        extra_roots=extra_roots,
        refresh_debounce_s=refresh_debounce_s,
    )
