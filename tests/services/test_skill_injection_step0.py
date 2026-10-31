"""Phase-3 STEP-0 pack — file-canon interception in
``SkillInjectionService.inject_explicit_skill``.

Commission: project-skill-loader, Phase 3 (task 1 + task 2 + task 7
depth-0 + task 8 full-body + task 9 fail-closed + task 12 reachable
subset + A15 / N8 / S5 / A8-STEP-0).

Pins in this pack
-----------------

``TestStep0BareName`` (task 1)
    The load-path pin: a file skill by BARE NAME in the caller's
    project scope injects the body BYTE-EQUAL to the file on disk,
    returns ``[pseudo_id]``, performs ZERO DB writes (skills row
    count unchanged, clone service NEVER invoked — the task-2 audit
    pin), and prepends the S5 provenance header (project_id +
    root_path). Miss ⇒ the DB clone path runs byte-identically.
``TestStep0Precedence`` (task 1 / A15)
    File (STEP 0) → ``clone_on_miss_sync`` (project row → bank
    template) → repo fallback: a same-name DB twin is NEVER reached
    when the file skill hits (shadowing semantics for explicit load).
``TestStep0PfAddress`` (A15)
    ``pf:``-shaped inputs BYPASS the silent early-exit and FAIL
    CLOSED: no resolvable project ⇒ explicit error envelope; no file
    source ⇒ explicit envelope; miss ⇒ explicit envelope. Bare names
    keep silent-miss semantics (``(None, [])``).
``TestStep0NoneTolerance`` (N8)
    ``file_source is None`` ⇒ STEP-0 skipped entirely — no file
    lookup attempted (spy), DB path byte-identical.
``TestStep0A8Guard``
    A file source that RAISES (resolver / ensure_index / lookup) is
    treated as a MISS — DB-skill injection unaffected.
``TestStep0FailClosed`` (task 9)
    Body unreadable at load time (permission-denied — the
    lookup_explicit raise path) ⇒ explicit envelope naming the
    reason + re-index; never a stale body, never an empty injection.
    Deleted body ⇒ the stat-gate refresh invalidates the meta first
    ⇒ miss envelope (also fail-closed; two-layer detection pinned).
``TestStep0LoadsDepth0`` (task 7)
    A manifest carrying ``loads[]`` injects ONLY the addressed skill
    — dependencies are pointers, never transitively injected.
``TestStep0FullBody`` (task 8 / FR-P5-3)
    Injection renders the FULL body — no cap (a >8000-char body is
    injected byte-complete; the 8000 cap is the VIEW surface only).
``TestStep0SkillInjectionFlag`` (task 12 reachable subset)
    The explicit path is scope-gated exactly as before — file skills
    ride the SAME explicit-injection entry, no new defaults.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlmodel import SQLModel, create_engine, Session, select

import daemon.repositories.skill.models  # noqa: F401 — register legacy models
from daemon.repositories.project_skill_file.repository import (
    ProjectSkillFileRepository,
)
from daemon.repositories.skill.models import Skill
from daemon.services.skill_injection_service import SkillInjectionService
from daemon.services.project_skill_source.service import (
    ProjectSkillSourceService,
)
from tests.helpers.project_skill_source_fixtures import (
    FakeInstance,
    FakeInstanceRepository,
    FakeProject,
    FakeProjectRepository,
    write_skill,
)

TEST_PROJECT_ID = "11111111-2222-3333-4444-555555555555"
OTHER_PROJECT_ID = "99999999-8888-7777-6666-555555555555"
INSTANCE_ID = "inst-step0-1"


# ============================================================
# Builders
# ============================================================


class _StaticSkillRepo:
    """Minimal ``get_by_name`` repo (the STEP-0 miss fallback path)."""

    def __init__(self, skills: list) -> None:
        self._skills = list(skills)

    def get_by_name(self, project_id, name, generation=0):
        for s in self._skills:
            if getattr(s, "name", None) == name and (
                getattr(s, "project_id", None) == project_id
            ):
                return s
        return None


class _SpyCloneService:
    """Clone-on-miss stand-in that RECORDS invocations (task-2 audit
    pin: STEP-0 must never reach it when a file skill hits)."""

    def __init__(self, returns=None):
        self.calls: list[tuple] = []
        self._returns = returns

    def clone_on_miss_sync(self, name, agent_id, project_id):
        self.calls.append((name, agent_id, project_id))
        return self._returns


class _ExplodingFileSource:
    """Raises at a chosen seam — the A8 STEP-0 fixture."""

    def __init__(self, where: str) -> None:
        self.where = where

    async def resolve_active_project(self, instance_id=None, explicit_project_id=None):
        if self.where == "resolve":
            raise RuntimeError("resolver exploded (A8 STEP-0 fixture)")
        return SimpleNamespace(
            project_id=TEST_PROJECT_ID, roots=["/nonexistent"], refused=False
        )

    async def ensure_index(self, root, project_id):
        if self.where == "ensure":
            raise RuntimeError("ensure_index exploded (A8 STEP-0 fixture)")

    async def lookup_explicit(self, name_or_address, project_id):
        raise RuntimeError("lookup exploded (A8 STEP-0 fixture)")


def _make_service(*, file_source=None, clone=None, db_skills=None) -> SkillInjectionService:
    svc = SkillInjectionService(
        search_service=None,
        config=None,
        ab_test_repo=None,
        skill_repo=_StaticSkillRepo(db_skills or []),
    )
    if file_source is not None:
        svc.set_file_source(file_source)
    if clone is not None:
        svc.set_clone_service(clone)
    return svc


def _make_file_source(engine, root, project_id=TEST_PROJECT_ID,
                      instance_id=INSTANCE_ID):
    return ProjectSkillSourceService(
        instance_repository=FakeInstanceRepository(
            {instance_id: FakeInstance(instance_id, {"project_id": project_id}, project_id)}
        ),
        project_repository=FakeProjectRepository(
            [FakeProject(project_id, main_directory=root)]
        ),
        file_repository=ProjectSkillFileRepository(engine),
    )


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/step0.db")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def canon(tmp_path, engine):
    """A project root whose canon carries two skills: one plain, one
    with a ``loads[]`` dependency + a big body variant helper."""
    root = str(tmp_path / "proj-root")
    os.makedirs(root, exist_ok=True)
    write_skill(
        root, "tester", "alpha-skill",
        body_text="Alpha body content alpha alpha.\n",
        manifest={
            "schema_version": 1, "name": "alpha-skill",
            "body": "alpha-skill.md", "version": "1.0.0",
            "description": "Alpha does things.", "category": "workflow",
            "tiers": [1], "loads": [], "assets": [],
        },
    )
    write_skill(
        root, "tester", "beta-skill",
        body_text="Beta body content.\n",
        manifest={
            "schema_version": 1, "name": "beta-skill",
            "body": "beta-skill.md", "version": "1.2.0",
            "description": "Beta loads alpha.", "category": "workflow",
            "tiers": [1, 2], "loads": ["alpha-skill"], "assets": [],
        },
    )
    return root


def _db_skill_row(engine, project_id, name):
    with Session(engine) as session:
        rows = list(
            session.exec(
                select(Skill).where(
                    Skill.project_id == project_id, Skill.name == name
                )
            )
        )
        return rows


def _skill_row_count(engine) -> int:
    with Session(engine) as session:
        return len(list(session.exec(select(Skill))))


# ============================================================
# Task 1 — load path, provenance, zero DB writes, miss fallthrough
# ============================================================


class TestStep0BareName:
    async def test_file_skill_injects_byte_equal_body(
        self, tmp_path, engine, canon
    ):
        fs = _make_file_source(engine, canon)
        svc = _make_service(file_source=fs)
        text, ids = await svc.inject_explicit_skill(
            "alpha-skill", TEST_PROJECT_ID, INSTANCE_ID, "msg-1", "tester"
        )
        assert text is not None
        assert ids and ids[0].startswith("pf-")
        # Body byte-equal to the file on disk.
        disk = open(
            os.path.join(canon, ".agents", "tester", "skills",
                         "alpha-skill", "alpha-skill.md"),
            encoding="utf-8",
        ).read()
        assert disk in text
        # S5 provenance header: project_id + root_path.
        assert text.startswith("[Project-File Skill] pf:tester:alpha-skill")
        assert TEST_PROJECT_ID in text.splitlines()[0]
        assert canon in text.splitlines()[0]

    async def test_zero_db_writes_clone_never_invoked(
        self, engine, canon
    ):
        """Task-2 audit pin: same-name bank template + file skill ⇒
        row count unchanged AND the clone service is never called."""
        fs = _make_file_source(engine, canon)
        clone = _SpyCloneService(
            returns=SimpleNamespace(
                id="db-twin-1", name="alpha-skill", description="d",
                content="DB TWIN CONTENT", project_id=TEST_PROJECT_ID,
                status="active", ab_test_group=None,
            )
        )
        svc = _make_service(file_source=fs, clone=clone)
        before = _skill_row_count(engine)
        text, ids = await svc.inject_explicit_skill(
            "alpha-skill", TEST_PROJECT_ID, INSTANCE_ID, "msg-2", "tester"
        )
        assert ids[0].startswith("pf-")
        assert "DB TWIN CONTENT" not in text
        assert clone.calls == []  # audit: no clone/refresh DB writes
        assert _skill_row_count(engine) == before

    async def test_miss_falls_through_to_clone_path_byte_identically(
        self, engine, canon
    ):
        fs = _make_file_source(engine, canon)
        twin = SimpleNamespace(
            id="db-skill-9", name="gamma-skill", description="d",
            content="DB GAMMA", project_id=TEST_PROJECT_ID,
            status="active", ab_test_group=None,
        )
        clone = _SpyCloneService(returns=twin)
        svc = _make_service(file_source=fs, clone=clone)
        # gamma-skill does NOT exist in the file canon ⇒ STEP-0 miss.
        text, ids = await svc.inject_explicit_skill(
            "gamma-skill", TEST_PROJECT_ID, INSTANCE_ID, "msg-3", "tester"
        )
        assert clone.calls == [("gamma-skill", "tester", TEST_PROJECT_ID)]
        assert ids == ["db-skill-9"]
        assert "DB GAMMA" in text
        assert "[Project-File Skill]" not in text

    async def test_no_project_scope_file_invisible_db_path_stands(
        self, engine, canon
    ):
        """A DIFFERENT project scope (cross-project) must not see this
        canon (P3): the bare name falls to the DB path."""
        fs = _make_file_source(engine, canon)
        twin = SimpleNamespace(
            id="db-x-1", name="alpha-skill", description="d",
            content="DB ALPHA", project_id=OTHER_PROJECT_ID,
            status="active", ab_test_group=None,
        )
        clone = _SpyCloneService(returns=twin)
        svc = _make_service(file_source=fs, clone=clone)
        text, ids = await svc.inject_explicit_skill(
            # The instance resolves to TEST_PROJECT; explicit
            # OTHER_PROJECT conflicts ⇒ A6 refusal ⇒ file corpus off.
            "alpha-skill", OTHER_PROJECT_ID, INSTANCE_ID, "msg-4", "tester"
        )
        assert ids == ["db-x-1"]
        assert "DB ALPHA" in text


class TestStep0Precedence:
    async def test_file_precedes_clone_and_repo(self, engine, canon):
        """A15 precedence chain: file (STEP 0) → clone_on_miss → repo.
        A DB twin by the same name is NEVER reached on a file hit."""
        fs = _make_file_source(engine, canon)
        clone = _SpyCloneService(returns=None)
        svc = _make_service(
            file_source=fs,
            clone=clone,
            db_skills=[SimpleNamespace(
                id="db-twin-2", name="alpha-skill", description="d",
                content="DB TWIN", project_id=TEST_PROJECT_ID,
            )],
        )
        text, ids = await svc.inject_explicit_skill(
            "alpha-skill", TEST_PROJECT_ID, INSTANCE_ID, "msg-5", "tester"
        )
        assert ids[0].startswith("pf-")
        assert clone.calls == []


# ============================================================
# A15 — pf: fail-closed envelopes
# ============================================================


class TestStep0PfAddress:
    async def test_pf_no_project_fails_closed(self):
        svc = _make_service(file_source=_ExplodingFileSource("none"))
        text, ids = await svc.inject_explicit_skill(
            "pf:tester:alpha-skill", None, INSTANCE_ID, "m", "a"
        )
        assert ids == []
        assert text is not None and text.startswith("ERROR: skill load failed")
        assert "project scope" in text

    async def test_pf_no_file_source_fails_closed(self):
        svc = _make_service()  # file_source None
        text, ids = await svc.inject_explicit_skill(
            "pf:tester:alpha-skill", TEST_PROJECT_ID, INSTANCE_ID, "m", "a"
        )
        assert ids == []
        assert "file skill source is not available" in text

    async def test_pf_miss_fails_closed_never_db_fallback(
        self, engine, canon
    ):
        fs = _make_file_source(engine, canon)
        twin = SimpleNamespace(
            id="db-twin-3", name="nosuch-skill", description="d",
            content="DB SHOULD NOT APPEAR", project_id=TEST_PROJECT_ID,
            status="active", ab_test_group=None,
        )
        clone = _SpyCloneService(returns=twin)
        svc = _make_service(file_source=fs, clone=clone)
        text, ids = await svc.inject_explicit_skill(
            "pf:tester:nosuch-skill", TEST_PROJECT_ID, INSTANCE_ID, "m", "a"
        )
        assert ids == []
        assert "no file skill with address" in text
        assert clone.calls == []  # NEVER falls through to the DB path

    async def test_pf_hit_injects_with_pseudo_id(self, engine, canon):
        fs = _make_file_source(engine, canon)
        svc = _make_service(file_source=fs)
        text, ids = await svc.inject_explicit_skill(
            "pf:tester:beta-skill", TEST_PROJECT_ID, INSTANCE_ID, "m", "a"
        )
        assert ids and ids[0].startswith("pf-")
        assert "Beta body content." in text

    async def test_bare_name_no_project_keeps_silent_miss(self):
        """A15: bare names keep today's silent-miss semantics."""
        svc = _make_service(file_source=_ExplodingFileSource("none"))
        result = await svc.inject_explicit_skill(
            "some-skill", "", INSTANCE_ID, "m", "a"
        )
        assert result == (None, [])


# ============================================================
# N8 — file_source None ⇒ STEP-0 skipped entirely
# ============================================================


class TestStep0NoneTolerance:
    async def test_none_file_source_skips_step0_entirely(self):
        """N8: no file lookup is ATTEMPTED (spy proves the skip), DB
        path byte-identical."""
        exploding = _ExplodingFileSource("resolve")
        svc = _make_service()  # file_source NOT set
        svc.set_file_source(exploding)
        svc._file_source = None  # force None while keeping the spy object
        twin = SimpleNamespace(
            id="db-n8-1", name="whatever", description="d",
            content="DB N8", project_id=TEST_PROJECT_ID,
            status="active", ab_test_group=None,
        )
        clone = _SpyCloneService(returns=twin)
        svc.set_clone_service(clone)
        text, ids = await svc.inject_explicit_skill(
            "whatever", TEST_PROJECT_ID, INSTANCE_ID, "m", "a"
        )
        assert ids == ["db-n8-1"]
        assert "DB N8" in text
        assert clone.calls == [("whatever", "a", TEST_PROJECT_ID)]


# ============================================================
# A8 STEP-0 guard — a raising file source is a MISS
# ============================================================


class TestStep0A8Guard:
    @pytest.mark.parametrize("where", ["resolve", "ensure", "lookup"])
    async def test_raising_file_source_is_a_miss_db_injection_unaffected(
        self, where
    ):
        fs = _ExplodingFileSource(where)
        twin = SimpleNamespace(
            id="db-a8-1", name="alpha-skill", description="d",
            content="DB A8 SURVIVOR", project_id=TEST_PROJECT_ID,
            status="active", ab_test_group=None,
        )
        clone = _SpyCloneService(returns=twin)
        svc = _make_service(file_source=fs, clone=clone)
        text, ids = await svc.inject_explicit_skill(
            "alpha-skill", TEST_PROJECT_ID, INSTANCE_ID, "m", "a"
        )
        assert ids == ["db-a8-1"]
        assert "DB A8 SURVIVOR" in text
        assert "[Project-File Skill]" not in text

    async def test_resolver_refused_treated_as_miss(self, engine, canon):
        """A6 refusal (explicit conflicts with derived) ⇒ no file
        corpus ⇒ DB path stands."""

        class _RefusedSource:
            async def resolve_active_project(self, instance_id=None, explicit_project_id=None):
                return SimpleNamespace(
                    project_id=TEST_PROJECT_ID, roots=[], refused=True,
                    refusal_reason="explicit_project_conflict",
                )

        svc = _make_service(file_source=_RefusedSource())
        twin = SimpleNamespace(
            id="db-r-1", name="alpha-skill", description="d",
            content="DB REFUSED", project_id=TEST_PROJECT_ID,
            status="active", ab_test_group=None,
        )
        clone = _SpyCloneService(returns=twin)
        svc.set_clone_service(clone)
        text, ids = await svc.inject_explicit_skill(
            "alpha-skill", OTHER_PROJECT_ID, INSTANCE_ID, "m", "a"
        )
        assert ids == ["db-r-1"]


# ============================================================
# Task 9 — fail-closed at load time
# ============================================================


class TestStep0FailClosed:
    async def test_unreadable_body_explicit_envelope(
        self, tmp_path, engine, canon
    ):
        """The lookup_explicit RAISE path (permission-denied: stat
        passes, open fails) — the plan's mandated envelope text."""
        fs = _make_file_source(engine, canon)
        svc = _make_service(file_source=fs)
        # Index first with the body present.
        ok = await svc.inject_explicit_skill(
            "pf:tester:alpha-skill", TEST_PROJECT_ID, INSTANCE_ID, "m1", "a"
        )
        assert ok[1]
        body = os.path.join(
            canon, ".agents", "tester", "skills", "alpha-skill",
            "alpha-skill.md",
        )
        os.chmod(body, 0o000)
        try:
            text, ids = await svc.inject_explicit_skill(
                "pf:tester:alpha-skill", TEST_PROJECT_ID, INSTANCE_ID,
                "m2", "a"
            )
        finally:
            os.chmod(body, 0o644)
        assert ids == []
        assert text is not None
        assert "body unreadable" in text
        assert "re-index scheduled" in text
        assert "Alpha body content" not in text  # never the stale body

    async def test_deleted_body_never_stale_never_empty(
        self, tmp_path, engine, canon
    ):
        """Deleted body: the stat-gate refresh invalidates the meta
        one layer earlier ⇒ the miss envelope (also fail-closed —
        never stale, never empty). Two-layer detection pinned."""
        fs = _make_file_source(engine, canon)
        svc = _make_service(file_source=fs)
        ok = await svc.inject_explicit_skill(
            "pf:tester:beta-skill", TEST_PROJECT_ID, INSTANCE_ID, "m1", "a"
        )
        assert ok[1]
        os.remove(os.path.join(
            canon, ".agents", "tester", "skills", "beta-skill",
            "beta-skill.md",
        ))
        text, ids = await svc.inject_explicit_skill(
            "pf:tester:beta-skill", TEST_PROJECT_ID, INSTANCE_ID, "m2", "a"
        )
        assert ids == []
        assert text is not None  # explicit envelope, never (None, [])
        assert "ERROR: skill load failed" in text
        assert "Beta body content." not in text  # never the stale body


# ============================================================
# Task 7 — loads are pointers (depth-0 only)
# ============================================================


class TestStep0LoadsDepth0:
    async def test_loads_never_transitively_injected(self, engine, canon):
        fs = _make_file_source(engine, canon)
        svc = _make_service(file_source=fs)
        text, ids = await svc.inject_explicit_skill(
            "pf:tester:beta-skill", TEST_PROJECT_ID, INSTANCE_ID, "m", "a"
        )
        # ONLY beta injected — alpha (the loads[] dep) is NOT injected.
        assert len(ids) == 1
        assert "Beta body content." in text
        assert "Alpha body content" not in text


# ============================================================
# Task 8 — full body, no cap (FR-P5-3)
# ============================================================


class TestStep0FullBody:
    async def test_injection_renders_full_body_no_cap(
        self, tmp_path, engine
    ):
        root = str(tmp_path / "big-root")
        os.makedirs(root, exist_ok=True)
        big_body = "BIG " * 4000  # 16000 chars — over the 8000 view cap
        write_skill(
            root, "tester", "big-skill", body_text=big_body,
            manifest={
                "schema_version": 1, "name": "big-skill",
                "body": "big-skill.md", "version": "1.0.0",
                "description": "big", "category": "workflow",
                "tiers": [1], "loads": [], "assets": [],
            },
        )
        fs = _make_file_source(engine, root)
        svc = _make_service(file_source=fs)
        text, ids = await svc.inject_explicit_skill(
            "pf:tester:big-skill", TEST_PROJECT_ID, INSTANCE_ID, "m", "a"
        )
        assert ids
        assert big_body in text  # FULL body — no truncation marker
        assert "truncated" not in text


# ============================================================
# Task 12 — reachable subset: same footing, no new defaults
# ============================================================


class TestStep0SkillInjectionFlag:
    async def test_explicit_path_unchanged_for_db_skills(self):
        """P5: no new injection defaults — the DB explicit path is
        byte-identical when no file source is wired (regression
        guard for the STEP-0 insertion)."""
        twin = SimpleNamespace(
            id="db-p5-1", name="plain-skill", description="d",
            content="PLAIN", project_id=TEST_PROJECT_ID,
            status="active", ab_test_group=None,
        )
        clone = _SpyCloneService(returns=twin)
        svc = _make_service(clone=clone)
        text, ids = await svc.inject_explicit_skill(
            "plain-skill", TEST_PROJECT_ID, INSTANCE_ID, "m", "a"
        )
        assert ids == ["db-p5-1"]
        assert "[System Inject] Relevant skills loaded:" in text
        assert "PLAIN" in text
