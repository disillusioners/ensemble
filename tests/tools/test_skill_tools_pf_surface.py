"""Phase-3 tool-surface pack — ``pf:`` handling in ``daemon/tools/skill_tools.py``.

Commission: project-skill-loader, Phase 3 (tasks 3, 4, 5, 6 + the
ACTIVATION + A9 degraded-mode guards / None-tolerance).

Pins in this pack
-----------------

``TestActivation`` (the leader's named deliverable)
    ``skill_search`` threads ``current_instance_id`` into
    ``search(instance_id=...)`` — the seam that turns the Phase-2
    fusion from dark to agent-lane-LIVE. Pinned BOTH directions: the
    kwarg is passed with the closure id, and dropping it (mutation
    P3-M3) reddens the pin.
``TestA9FileOnly`` (task 10 guards, M10 shape in isolation)
    DB search service ABSENT + file source available ⇒ file-only
    BM25 answers with the pinned semantics (standard shape, source
    tags, ``degraded: "file_only"``, no stage 2/3, ``top_k=limit``);
    None-tolerance (no file source ⇒ the soft-fail message stands).
``TestSkillViewPf`` (tasks 3 / 7 / 8 / 9)
    ``skill_view`` on a ``pf:`` address: header metadata (name /
    version / category / tiers / namespace / address / body_path /
    digest), body fresh from disk, 8000-char cap + read_file hint,
    ``## Loads`` with pf: addresses + descriptions, ``## Assets``,
    depth-0 note; fail-closed on unreadable body; A13 no-hint
    resolution; miss envelope.
``TestSkillListFiles`` (task 4)
    File skills listed alongside DB rows with ``source`` tags,
    namespace-grouped; invalid entries surfaced WITH reasons (Q7);
    no mutable DB rows for file skills.
``TestSkillFixRefusal`` (task 5)
    ``pf:`` ⇒ git-PR guidance, dispatcher NEVER invoked.
    FR-P7-4 clause 2: ``skill_create`` has no ``source`` param
    (signature introspection — the vacuity) and refuses a
    ``pf:``-shaped name (the invariant's behavioral half).
``TestSkillFeedbackPf`` (task 6, D2 companion branch)
    ``pf:``/``pf-`` ids record METRICS-ONLY into
    ``project_skill_usage_records``; no lineage rows; no evolution
    dispatch; metrics service never invoked (works with it ABSENT);
    range validation mirrored.
``TestNoneTolerance`` (task 10 / approver P4)
    With NO manager file-skill attributes at all (the pre-Phase-4
    production shape) every tool soft-fails exactly as before —
    none of the new branches raises.
"""

from __future__ import annotations

import inspect
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import SQLModel, create_engine

import daemon.repositories.skill.models  # noqa: F401 — legacy models registered
from daemon.repositories.project_skill_file.models import (
    ProjectSkillUsageRecord,
)
from daemon.repositories.project_skill_file.repository import (
    ProjectSkillFileRepository,
    ProjectSkillUsageRecordRepository,
)
from daemon.services.project_skill_source.service import (
    ProjectSkillSourceService,
)
from daemon.tools.skill_tools import create_skill_tools
from tests.helpers.project_skill_source_fixtures import (
    FakeInstance,
    FakeInstanceRepository,
    FakeProject,
    FakeProjectRepository,
    write_skill,
)

PROJECT_ID = "11111111-2222-3333-4444-555555555555"
INSTANCE_ID = "inst-tools-1"


# ============================================================
# Builders
# ============================================================


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/tools.db")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def canon(tmp_path):
    root = str(tmp_path / "proj-root")
    os.makedirs(root, exist_ok=True)
    write_skill(
        root, "tester", "alpha-skill",
        body_text="Alpha body alpha alpha.\n",
        manifest={
            "schema_version": 1, "name": "alpha-skill",
            "body": "alpha-skill.md", "version": "1.0.0",
            "description": "Alpha does things.", "category": "workflow",
            "tiers": [1], "loads": [], "assets": [],
        },
    )
    write_skill(
        root, "tester", "beta-skill",
        body_text="Beta body.\n",
        manifest={
            "schema_version": 1, "name": "beta-skill",
            "body": "beta-skill.md", "version": "2.0.0",
            "description": "Beta loads alpha.", "category": "gate-runner",
            "tiers": [1], "loads": ["alpha-skill"], "assets": [],
        },
    )
    write_skill(
        root, "shared", "gamma-skill",
        body_text="Gamma body gamma.\n",
        manifest={
            "schema_version": 1, "name": "gamma-skill",
            "body": "gamma-skill.md", "version": "1.0.0",
            "description": "Gamma shared.", "category": "workflow",
            "tiers": [2], "loads": [], "assets": [],
        },
    )
    return root


def _make_manager(tmp_path, engine, canon, *, with_db_services=True):
    file_repo = ProjectSkillFileRepository(engine)
    usage_repo = ProjectSkillUsageRecordRepository(engine)
    fs = ProjectSkillSourceService(
        instance_repository=FakeInstanceRepository(
            {INSTANCE_ID: FakeInstance(
                INSTANCE_ID, {"project_id": PROJECT_ID}, PROJECT_ID
            )}
        ),
        project_repository=FakeProjectRepository(
            [FakeProject(PROJECT_ID, main_directory=canon)]
        ),
        file_repository=file_repo,
    )
    manager = MagicMock()
    if with_db_services:
        manager._skill_search_service = MagicMock()
        manager._skill_store_service = MagicMock()
        manager._skill_metrics_service = MagicMock()
        manager._skill_job_dispatcher = MagicMock()
    else:
        manager._skill_search_service = None
        manager._skill_store_service = None
        manager._skill_metrics_service = None
        manager._skill_job_dispatcher = MagicMock()
    manager._project_skill_source = fs
    manager._project_skill_file_repository = file_repo
    manager._project_skill_usage_repository = usage_repo
    manager._instance_repository = FakeInstanceRepository(
        {INSTANCE_ID: FakeInstance(
            INSTANCE_ID, {"project_id": PROJECT_ID}, PROJECT_ID
        )}
    )
    return manager, fs, usage_repo


def _tools(manager):
    return {t.name: t for t in create_skill_tools(manager, INSTANCE_ID)}


# ============================================================
# ACTIVATION — instance_id threading at the skill_search tool
# ============================================================


class TestActivation:
    async def test_search_threads_instance_id(self, tmp_path, engine, canon):
        """The ACTIVATION: the closure's ``current_instance_id`` is
        forwarded — without it the file corpus is invisible on every
        tool call (fusion dark)."""
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        tools = _tools(manager)
        await tools["skill_search"].ainvoke({"query": "alpha"})
        kwargs = manager._skill_search_service.search.call_args.kwargs
        assert kwargs.get("instance_id") == INSTANCE_ID

    async def test_search_passes_project_and_limit_as_before(
        self, tmp_path, engine, canon
    ):
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        tools = _tools(manager)
        await tools["skill_search"].ainvoke({"query": "x", "limit": 3})
        kwargs = manager._skill_search_service.search.call_args.kwargs
        assert kwargs.get("max_results") == 3
        assert kwargs.get("project_id") == PROJECT_ID
        assert kwargs.get("agent_id") is None


# ============================================================
# A9 — file-only degraded search (M10 shape in isolation)
# ============================================================


class TestA9FileOnly:
    async def test_file_only_search_pinned_semantics(
        self, tmp_path, engine, canon
    ):
        manager, fs, _ = _make_manager(
            tmp_path, engine, canon, with_db_services=False
        )
        tools = _tools(manager)
        result = json.loads(
            await tools["skill_search"].ainvoke({"query": "alpha", "limit": 1})
        )
        # Standard shape + degraded marker.
        assert set(result.keys()) >= {"injected", "low_match", "degraded"}
        assert result["degraded"] == "file_only"
        # Stage-1 BM25 only — no LLM/embedding machinery reachable
        # (the DB service is None; the marker proves the code path).
        assert len(result["injected"]) == 1  # top_k == limit
        entry = result["injected"][0]
        assert entry["source"] == "project-file"
        assert entry["skill"]["name"] == "alpha-skill"
        assert entry["skill"]["source"] == "project-file"
        assert entry["skill"]["address"] == "pf:tester:alpha-skill"
        # low_match carries the overflow entries with source tags.
        assert all(
            e.get("source") == "project-file" for e in result["low_match"]
        )

    async def test_file_only_search_no_scope_empty_shape(
        self, tmp_path, engine, canon
    ):
        """No resolvable scope ⇒ honest empty file-only answer, no
        raise (None-tolerance / P3 invisibility)."""
        manager, fs, _ = _make_manager(
            tmp_path, engine, canon, with_db_services=False
        )
        # Unbind the instance: no project scope resolvable.
        manager._instance_repository = FakeInstanceRepository({})
        manager._project_skill_source._instance_repository = (
            FakeInstanceRepository({})
        )
        tools = _tools(manager)
        result = json.loads(
            await tools["skill_search"].ainvoke({"query": "alpha"})
        )
        assert result == {
            "injected": [], "low_match": [], "degraded": "file_only"
        }

    async def test_no_services_no_file_source_soft_fail(self):
        """Both absent ⇒ the pre-Phase-3 soft-fail message stands."""
        manager = MagicMock(spec=[])
        tools = {t.name: t for t in create_skill_tools(manager, INSTANCE_ID)}
        result = await tools["skill_search"].ainvoke({"query": "x"})
        assert "not yet available" in result


# ============================================================
# skill_view on pf: addresses
# ============================================================


class TestSkillViewPf:
    async def test_view_header_body_loads_assets(self, tmp_path, engine, canon):
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        tools = _tools(manager)
        r = await tools["skill_view"].ainvoke(
            {"skill_id": "pf:tester:beta-skill"}
        )
        # Header metadata (task 3).
        assert r.splitlines()[0] == "# beta-skill"
        assert "- address: pf:tester:beta-skill" in r
        assert "- namespace: tester" in r
        assert "- version: 2.0.0" in r
        assert "- category: gate-runner" in r
        assert "- tiers: [1]" in r
        assert "- body_path:" in r
        assert "- digest:" in r
        assert "- source: project-file" in r
        assert "- project_id:" in r and PROJECT_ID in r
        assert "- root_path:" in r
        # Body fresh from disk (task 3 / FR-P1-3).
        assert "Beta body." in r
        # ## Loads with pf: address + description (task 7 / Q6 v1).
        assert "## Loads" in r
        assert "pf:tester:alpha-skill" in r
        assert "Alpha does things." in r
        assert "depth-0" in r
        # ## Assets section renders even when empty-listed skills
        # have none — beta has no assets; gamma view pins the shape.
        r_gamma = await tools["skill_view"].ainvoke(
            {"skill_id": "pf:shared:gamma-skill"}
        )
        assert "Gamma body gamma." in r_gamma
        assert "## Loads" not in r_gamma

    async def test_view_8000_cap_and_read_file_hint(self, tmp_path, engine):
        root = str(tmp_path / "big")
        os.makedirs(root, exist_ok=True)
        write_skill(
            root, "tester", "big-skill", body_text="BIGBODY " * 1500,
            manifest={
                "schema_version": 1, "name": "big-skill",
                "body": "big-skill.md", "version": "1.0.0",
                "description": "big", "category": "workflow",
                "tiers": [1], "loads": [], "assets": [],
            },
        )
        manager = MagicMock()
        manager._skill_search_service = None
        manager._skill_store_service = None
        from daemon.services.project_skill_source.service import (
            ProjectSkillSourceService as _S,
        )
        eng = create_engine(f"sqlite:///{tmp_path}/big.db")
        SQLModel.metadata.create_all(eng)
        fs = _S(
            instance_repository=FakeInstanceRepository(
                {INSTANCE_ID: FakeInstance(
                    INSTANCE_ID, {"project_id": PROJECT_ID}, PROJECT_ID
                )}
            ),
            project_repository=FakeProjectRepository(
                [FakeProject(PROJECT_ID, main_directory=root)]
            ),
            file_repository=ProjectSkillFileRepository(eng),
        )
        manager._project_skill_source = fs
        manager._project_skill_file_repository = ProjectSkillFileRepository(eng)
        manager._instance_repository = FakeInstanceRepository(
            {INSTANCE_ID: FakeInstance(
                INSTANCE_ID, {"project_id": PROJECT_ID}, PROJECT_ID
            )}
        )
        tools = _tools(manager)
        r = await tools["skill_view"].ainvoke(
            {"skill_id": "pf:tester:big-skill"}
        )
        assert "(truncated at 8000 chars)" in r
        assert "read_file" in r
        assert "- body_path:" in r
        # The raw body on disk stays full (parity: injection renders
        # full — pinned in the STEP-0 pack).

    async def test_view_fail_closed_unreadable_body(self, tmp_path, engine, canon):
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        tools = _tools(manager)
        # Index first.
        ok = await tools["skill_view"].ainvoke(
            {"skill_id": "pf:tester:alpha-skill"}
        )
        assert "# alpha-skill" in ok
        body = os.path.join(
            canon, ".agents", "tester", "skills", "alpha-skill",
            "alpha-skill.md",
        )
        os.chmod(body, 0o000)
        try:
            r = await tools["skill_view"].ainvoke(
                {"skill_id": "pf:tester:alpha-skill"}
            )
        finally:
            os.chmod(body, 0o644)
        assert r.startswith("ERROR: skill_view: body unreadable:")
        assert "re-index scheduled" in r

    async def test_view_no_scope_fails_closed(self, tmp_path, engine, canon):
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        manager._project_skill_source._instance_repository = (
            FakeInstanceRepository({})
        )
        tools = _tools(manager)
        r = await tools["skill_view"].ainvoke(
            {"skill_id": "pf:tester:alpha-skill"}
        )
        assert "no project scope resolved" in r

    async def test_view_miss_envelope(self, tmp_path, engine, canon):
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        tools = _tools(manager)
        r = await tools["skill_view"].ainvoke(
            {"skill_id": "pf:tester:nosuch"}
        )
        assert "no file skill with address" in r

    async def test_view_no_file_source_soft_fail(self):
        manager = MagicMock(spec=[])
        tools = {t.name: t for t in create_skill_tools(manager, INSTANCE_ID)}
        r = await tools["skill_view"].ainvoke({"skill_id": "pf:x:y"})
        assert "not yet available" in r

    async def test_a13_resolver_no_caller_hint(self, tmp_path, engine, canon):
        """A13: pf: resolution = resolver(current_instance_id) — the
        resolver receives NO caller-supplied project hint. Spied via
        a wrapper around resolve_active_project."""
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        calls: list[dict] = []
        orig = fs.resolve_active_project

        async def spy(instance_id=None, explicit_project_id=None):
            calls.append(
                {"instance_id": instance_id,
                 "explicit": explicit_project_id}
            )
            return await orig(
                instance_id=instance_id,
                explicit_project_id=explicit_project_id,
            )

        fs.resolve_active_project = spy
        tools = _tools(manager)
        await tools["skill_view"].ainvoke({"skill_id": "pf:tester:alpha-skill"})
        assert calls, "resolver was never consulted"
        assert calls[0]["explicit"] is None  # A13: NO caller hint
        assert calls[0]["instance_id"] == INSTANCE_ID


# ============================================================
# skill_list — file section + invalid entries
# ============================================================


class TestSkillListFiles:
    async def test_files_listed_with_source_tag_namespaced(
        self, tmp_path, engine, canon
    ):
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        manager._skill_store_service.list_skills = AsyncMock(
            return_value=([], 0)
        )
        tools = _tools(manager)
        r = await tools["skill_list"].ainvoke({})
        assert "pf:tester:alpha-skill" in r
        assert "pf:tester:beta-skill" in r
        assert "pf:shared:gamma-skill" in r
        assert "source: project-file" in r
        assert "[tester]" in r and "[shared]" in r
        assert "v1.0.0" in r and "v2.0.0" in r

    async def test_invalid_entries_surfaced_with_reasons(
        self, tmp_path, engine
    ):
        root = str(tmp_path / "invalid-root")
        os.makedirs(root, exist_ok=True)
        write_skill(root, "tester", "good-skill", body_text="g\n",
                    manifest={"schema_version": 1, "name": "good-skill",
                              "body": "good-skill.md", "version": "1.0.0",
                              "description": "g", "category": "workflow",
                              "tiers": [1], "loads": [], "assets": []})
        write_skill(root, "tester", "broken-skill",
                    manifest={"schema_version": 1, "name": "broken-skill",
                              "body": "MISSING.md", "version": "1.0.0",
                              "description": "b", "category": "workflow",
                              "tiers": [1], "loads": [], "assets": []},
                    with_body=False)
        manager, fs, _ = _make_manager(tmp_path, engine, root)
        manager._skill_store_service = None
        tools = _tools(manager)
        r = await tools["skill_list"].ainvoke({})
        assert "pf:tester:good-skill" in r
        assert "Invalid project-file skills" in r
        assert "pf:tester:broken-skill" in r
        assert "body_missing" in r

    async def test_dbrows_tag_only_when_file_source_wired(self, tmp_path, engine, canon):
        """W4 (Phase-4 code-review fix cycle) — the ``— source: db``
        suffix is ADDITIVE and must be emitted ONLY when a file source
        exists. Under the knob OFF the DB row line is byte-identical to
        the pre-lane output (A11's list half, previously unpinned).

        Mutation leg W4 restores the unconditional suffix → RED.
        """
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        manager._skill_store_service.list_skills = AsyncMock(
            return_value=(
                [{
                    "id": "abcdef01-2345-6789-abcd-ef0123456789",
                    "name": "code-review",
                    "category": "workflow",
                    "status": "active",
                }],
                1,
            )
        )
        legacy_line = "- [abcdef01-2345-6789-abcd-ef0123456789] code-review — workflow — active"

        # Knob ON: the tag is present (cross-source disambiguation).
        on_out = await _tools(manager)["skill_list"].ainvoke({})
        assert legacy_line + " — source: db" in on_out

        # Knob OFF: file_source is None ⇒ the pre-lane line, verbatim.
        manager._project_skill_source = None
        off_out = await _tools(manager)["skill_list"].ainvoke({})
        assert "source: db" not in off_out
        assert off_out.splitlines()[1] == legacy_line

    async def test_no_mutable_db_rows_for_files(self, tmp_path, engine, canon):
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        store = manager._skill_store_service
        store.list_skills = MagicMock(return_value=([], 0))
        tools = _tools(manager)
        await tools["skill_list"].ainvoke({})
        # Only list_skills invoked — no create/refresh surface exists
        # on the store mock; assert the file path wrote no skill rows
        # (project_skill_files is metadata-only by construction; the
        # skills table is never touched by the file section).
        from sqlmodel import Session, select
        from daemon.repositories.skill.models import Skill
        with Session(engine) as session:
            assert list(session.exec(select(Skill))) == []

    async def test_list_reads_metadata_only_never_bodies(
        self, tmp_path, engine, canon
    ):
        """A6 (tidier fold) — ``skill_list`` renders name / address /
        category / version, so the listing path must NOT pay a body read.

        ``corpus_for`` is replaced with a tripwire: the pre-fold list
        called it (reading every body just to discard it, inverting
        A16's metadata-only invariant). The list must keep working
        through ``metadata_for`` with the tripwire untouched.
        """
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        manager._skill_store_service.list_skills = AsyncMock(
            return_value=([], 0)
        )

        async def _tripwire(project_id, root):  # pragma: no cover - must not run
            raise AssertionError(
                "skill_list read skill BODIES (corpus_for) instead of "
                "metadata (A6 metadata-only list path)"
            )

        fs.corpus_for = _tripwire  # type: ignore[method-assign]
        r = await _tools(manager)["skill_list"].ainvoke({})
        assert "pf:tester:alpha-skill" in r
        assert "v1.0.0" in r
        assert "source: project-file" in r

    async def test_db_absent_file_source_answers_file_only(
        self, tmp_path, engine, canon
    ):
        manager, fs, _ = _make_manager(
            tmp_path, engine, canon, with_db_services=False
        )
        tools = _tools(manager)
        r = await tools["skill_list"].ainvoke({})
        assert "pf:tester:alpha-skill" in r
        assert "not yet available" not in r


# ============================================================
# skill_fix refusal + skill_create invariant (task 5)
# ============================================================


class TestSkillFixRefusal:
    async def test_pf_refused_dispatcher_never_invoked(
        self, tmp_path, engine, canon
    ):
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        dispatcher = manager._skill_job_dispatcher
        tools = _tools(manager)
        r = await tools["skill_fix"].ainvoke(
            {"skill_id": "pf:tester:alpha-skill",
             "issue_description": "stale path"}
        )
        assert "git PR" in r
        assert "P7" in r
        assert "`pf:`" in r or "pf:" in r
        dispatcher.dispatch_fix.assert_not_called()

    @pytest.mark.parametrize("malformed", ["pf:foo", "pf:"])
    async def test_malformed_pf_spellings_refused_on_tool_path(
        self, tmp_path, engine, canon, malformed
    ):
        """A4 (tidier fold) — refusal keys on the PREFIX, never on
        ``is_pf_address`` parseability.

        Pre-fold the tool used the STRICT ``pf:<ns>:<name>`` parser, so
        a malformed ``pf:foo`` / ``pf:`` fell through and reached
        ``dispatch_fix`` as if it were a DB id — a REAL P7 bypass
        (evolution machinery aimed at a file-skill-shaped id). The REST
        endpoint has always refused on the prefix; the tool now uses the
        identical predicate.
        """
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        dispatcher = manager._skill_job_dispatcher
        tools = _tools(manager)
        r = await tools["skill_fix"].ainvoke(
            {"skill_id": malformed, "issue_description": "x"}
        )
        assert "git PR" in r and "P7" in r
        dispatcher.dispatch_fix.assert_not_called()

    async def test_uuid_still_dispatches_as_before(
        self, tmp_path, engine, canon
    ):
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        dispatcher = manager._skill_job_dispatcher
        dispatcher.dispatch_fix = MagicMock(return_value="job-123")
        tools = _tools(manager)
        r = await tools["skill_fix"].ainvoke(
            {"skill_id": "12345678-1111-2222-3333-444444444444",
             "issue_description": "x"}
        )
        dispatcher.dispatch_fix.assert_called_once()
        assert "job-123" in r

    async def test_skill_create_has_no_source_param_vacuity(self):
        """FR-P7-4 clause 2: ``skill_create`` accepts no ``source``
        body (vacuously true today) — pinned via signature
        introspection so ADDING a source param without a refusal
        reddens this pin."""
        from daemon.tools.skill_tools import create_skill_tools as _cst
        manager = MagicMock()
        tools = {t.name: t for t in _cst(manager, INSTANCE_ID)}
        params = tools["skill_create"].args
        assert "source" not in params, (
            "skill_create gained a `source` parameter — the FR-P7-4 "
            "refusal for `source: project-file` MUST be added with it"
        )

    async def test_skill_create_refuses_pf_shaped_name(self):
        """The invariant's behavioral half: a file-skill-shaped body
        (``pf:`` name) is refused with the same git-PR guidance."""
        manager = MagicMock()
        manager._skill_store_service = MagicMock()
        tools = {t.name: t for t in create_skill_tools(manager, INSTANCE_ID)}
        r = await tools["skill_create"].ainvoke(
            {"name": "pf:tester:alpha-skill",
             "description": "d", "content": "c"}
        )
        assert "git PR" in r and "P7" in r
        manager._skill_store_service.create_skill.assert_not_called()


# ============================================================
# skill_feedback metrics-only (task 6, D2 companion branch)
# ============================================================


class TestSkillFeedbackPf:
    async def test_pf_address_records_into_companion_table(
        self, tmp_path, engine, canon
    ):
        manager, fs, usage_repo = _make_manager(tmp_path, engine, canon)
        tools = _tools(manager)
        r = await tools["skill_feedback"].ainvoke(
            {"skill_id": "pf:tester:alpha-skill", "applied": True,
             "usefulness": 8, "note": "helpful",
             "improvement_note": "add example"}
        )
        assert "metrics-only" in r
        assert "P7" in r
        from sqlmodel import Session, select
        with Session(engine) as session:
            rows = list(session.exec(select(ProjectSkillUsageRecord)))
        assert len(rows) == 1
        row = rows[0]
        assert row.skill_id.startswith("pf-")
        assert row.project_id == PROJECT_ID
        assert row.instance_id == INSTANCE_ID
        assert row.feedback_applied is True
        assert "helpful" in (row.feedback_note or "")
        assert "add example" in (row.feedback_note or "")
        assert "usefulness: 8/10" in (row.feedback_note or "")

    async def test_update_path_latest_row_updated(
        self, tmp_path, engine, canon
    ):
        manager, fs, usage_repo = _make_manager(tmp_path, engine, canon)
        tools = _tools(manager)
        await tools["skill_feedback"].ainvoke(
            {"skill_id": "pf:tester:alpha-skill", "applied": True}
        )
        r = await tools["skill_feedback"].ainvoke(
            {"skill_id": "pf:tester:alpha-skill", "applied": False,
             "note": "second"}
        )
        from sqlmodel import Session, select
        with Session(engine) as session:
            rows = list(session.exec(select(ProjectSkillUsageRecord)))
        assert len(rows) == 1  # updated, not duplicated
        assert rows[0].feedback_applied is False
        assert "second" in rows[0].feedback_note

    async def test_no_lineage_rows_no_evolution_dispatch(
        self, tmp_path, engine, canon
    ):
        manager, fs, usage_repo = _make_manager(tmp_path, engine, canon)
        metrics = manager._skill_metrics_service
        tools = _tools(manager)
        await tools["skill_feedback"].ainvoke(
            {"skill_id": "pf:tester:alpha-skill", "applied": True}
        )
        # No lineage rows (skill lineage untouched — the companion
        # table is the ONLY write) and no metrics-service dispatch.
        metrics.record_feedback.assert_not_called()
        from sqlmodel import Session, select
        from daemon.repositories.skill.models import SkillLineage
        with Session(engine) as session:
            assert list(session.exec(select(SkillLineage))) == []

    async def test_works_with_metrics_service_absent(
        self, tmp_path, engine, canon
    ):
        """A9 degraded mode: the pf: path runs BEFORE the
        metrics-service gate — the DB evolution stack may be absent."""
        manager, fs, usage_repo = _make_manager(
            tmp_path, engine, canon, with_db_services=False
        )
        tools = _tools(manager)
        r = await tools["skill_feedback"].ainvoke(
            {"skill_id": "pf:tester:alpha-skill", "applied": True}
        )
        assert "metrics-only" in r
        from sqlmodel import Session, select
        with Session(engine) as session:
            rows = list(session.exec(select(ProjectSkillUsageRecord)))
        assert len(rows) == 1

    async def test_pseudo_id_form_accepted(self, tmp_path, engine, canon):
        """The ``pf-<hex16>`` pseudo-id form REMAINS accepted — legacy
        transcript compatibility (R2g/M6) — but only when it is a REAL
        indexed file skill of this project (W5)."""
        manager, fs, usage_repo = _make_manager(tmp_path, engine, canon)
        await fs.ensure_index(canon, PROJECT_ID)
        pseudo = manager._project_skill_file_repository.list_by_project(
            PROJECT_ID
        )[0].id
        assert pseudo.startswith("pf-")
        tools = _tools(manager)
        r = await tools["skill_feedback"].ainvoke(
            {"skill_id": pseudo, "applied": True}
        )
        assert "metrics-only" in r
        rows = usage_repo.list_by_skill(pseudo)
        assert len(rows) == 1

    async def test_pseudo_id_refused_under_knob_off(self, tmp_path, engine, canon):
        """W5 (Phase-4 code-review fix cycle): with no file source the
        bare pseudo-id branch must REFUSE — "nothing is written under
        OFF" has to be literally true. Mutation leg W5 removes the
        guard and a usage row is minted → RED."""
        manager, fs, usage_repo = _make_manager(tmp_path, engine, canon)
        await fs.ensure_index(canon, PROJECT_ID)
        pseudo = manager._project_skill_file_repository.list_by_project(
            PROJECT_ID
        )[0].id
        manager._project_skill_source = None  # the knob-OFF shape
        tools = _tools(manager)
        r = await tools["skill_feedback"].ainvoke(
            {"skill_id": pseudo, "applied": True}
        )
        assert "no project scope resolved" in r
        assert usage_repo.list_by_skill(pseudo) == []

    async def test_pseudo_id_refused_when_not_indexed(self, tmp_path, engine, canon):
        """W5: an arbitrary ``pf-…`` string never mints a row."""
        manager, fs, usage_repo = _make_manager(tmp_path, engine, canon)
        tools = _tools(manager)
        r = await tools["skill_feedback"].ainvoke(
            {"skill_id": "pf-deadbeefdeadbeef", "applied": True}
        )
        assert "no indexed file skill with pseudo id" in r
        assert usage_repo.list_by_skill("pf-deadbeefdeadbeef") == []

    async def test_range_validation_mirrored(self, tmp_path, engine, canon):
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        tools = _tools(manager)
        r = await tools["skill_feedback"].ainvoke(
            {"skill_id": "pf:tester:alpha-skill", "applied": True,
             "usefulness": 11}
        )
        assert "must be in range 1-10" in r

    async def test_no_usage_repo_soft_fail(
        self, tmp_path, engine, canon
    ):
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        manager._project_skill_usage_repository = None
        tools = _tools(manager)
        r = await tools["skill_feedback"].ainvoke(
            {"skill_id": "pf:tester:alpha-skill", "applied": True}
        )
        assert "not yet available" in r

    async def test_pf_miss_envelope(self, tmp_path, engine, canon):
        manager, fs, _ = _make_manager(tmp_path, engine, canon)
        tools = _tools(manager)
        r = await tools["skill_feedback"].ainvoke(
            {"skill_id": "pf:tester:nosuch", "applied": True}
        )
        assert "no file skill with address" in r


# ============================================================
# None-tolerance — the pre-Phase-4 production shape
# ============================================================


class TestNoneTolerance:
    async def test_no_file_attrs_at_all_soft_fail_everywhere(self):
        """Pre-Phase-4 production: manager carries NONE of the four
        file-skill attributes. Every tool must behave exactly as
        before — no raise, no new branches tripping."""
        manager = MagicMock(spec=[])
        tools = {t.name: t for t in create_skill_tools(manager, INSTANCE_ID)}
        r_search = await tools["skill_search"].ainvoke({"query": "x"})
        r_view = await tools["skill_view"].ainvoke({"skill_id": "pf:a:b"})
        r_list = await tools["skill_list"].ainvoke({})
        r_feedback = await tools["skill_feedback"].ainvoke(
            {"skill_id": "pf:a:b", "applied": True}
        )
        for r in (r_search, r_view, r_list, r_feedback):
            assert "not yet available" in r


# ============================================================
# A7 — project-scope precedence (tidier fold + LEADER RULING)
# ============================================================


class TestScopePrecedenceA7:
    """A7: the SERVICE's precedence WINS — ``instance_metadata``
    ``["project_id"]`` first, then the ``instances.project_id`` row.

    The ruling is a *single-precedence* rule, not a preference: the
    file-skill resolver (``resolve_active_project``, D-Q1) derives the
    project in that order, and an ``explicit`` hint may only CONFIRM it
    (A6). The tools used to read the ROW ONLY, so on a drifted instance
    the DB half of a call saw one project while the resolver derived
    another — and the resolver refused the file corpus as an "explicit
    project conflict" the caller never supplied.
    """

    DRIFT_PID = "99999999-8888-7777-6666-555555555555"

    async def test_metadata_project_wins_over_row(
        self, tmp_path, engine, canon
    ):
        manager, _, _ = _make_manager(tmp_path, engine, canon)
        manager._instance_repository = FakeInstanceRepository(
            {
                INSTANCE_ID: FakeInstance(
                    INSTANCE_ID, {"project_id": self.DRIFT_PID}, PROJECT_ID
                )
            }
        )
        await _tools(manager)["skill_search"].ainvoke({"query": "alpha"})
        kwargs = manager._skill_search_service.search.call_args.kwargs
        assert kwargs.get("project_id") == self.DRIFT_PID, (
            "A7: metadata project must WIN over the drifted row column "
            "(the service's precedence)"
        )

    async def test_row_used_when_metadata_absent(self, tmp_path, engine, canon):
        manager, _, _ = _make_manager(tmp_path, engine, canon)
        manager._instance_repository = FakeInstanceRepository(
            {INSTANCE_ID: FakeInstance(INSTANCE_ID, {}, PROJECT_ID)}
        )
        await _tools(manager)["skill_search"].ainvoke({"query": "alpha"})
        kwargs = manager._skill_search_service.search.call_args.kwargs
        assert kwargs.get("project_id") == PROJECT_ID
