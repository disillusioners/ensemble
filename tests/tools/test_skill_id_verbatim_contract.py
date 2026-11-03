"""Pin pack — wi-76d89e11: the skill id-emission CONTRACT (verbatim-consumable ids).

Every agent-facing skill id the skill tools EMIT must be an id
``skill_view`` ACCEPTS VERBATIM — per source:

* **DB skill** → the FULL stored uuid4 (``Skill.id`` — ``skill_view``
  resolves DB ids by EXACT PK, no prefix fallback).
* **Project-file skill** → the ADDRESS ``pf:<ns>:<name>`` (a file skill
  has NO uuid; its ``pf-<sha16>`` pseudo-id is the INTERNAL key and is
  REFUSED by ``skill_view``).

The harness is the REAL production construction (mirrors the Phase-0
runtime repro, ``.agents/tester/RESULTS/2026-11-03-wi76d89e11-phase0-runtime-repro.md``):
real repositories (the same factories ``manager.py`` uses), real
services, the real tool factory (``create_skill_tools``) invoked
through ``tool.ainvoke({...})`` — the true agent path. Scratch SQLite
via ``SQLModel.metadata.create_all`` (house norm — NEVER the migration
runner; the fresh-SQLite runner boot is broken by ``20260714_000001``).

Fenced search construction (W5): the fused leg builds the REAL
``SkillSearchService`` with ``config=None`` — legal (``__init__``
stores the parameter plainly) and deterministic: ``search()`` skips
stage 3 (``_llm_select``) and takes ``_degraded_select`` instead, so
NO LLM is reachable from this pack. Two degraded seams are pinned BY
NAME: the tool-level ``_file_only_bm25_search`` (DB store absent) and
the service-level ``_degraded_select`` (store present, ``config=None``).

RED-at-BASE recipe (approver N9): at BASE ``c4161335b`` the DB legs,
the search-payload legs, the skill_create leg and the injection-body
leg are RED — run this pack at the base commit with

    bash test/packs/skill_id_verbatim_contract_unit_test.sh

and the anchor evidence lives in the RESULTS doc named above.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import SQLModel, create_engine

import daemon.repositories.skill.models  # noqa: F401 — skill models registered
from daemon.repositories.factory import (
    create_instance_repository,
    create_project_repository,
    create_skill_embedding_repository,
    create_skill_lineage_repository,
    create_skill_repository,
)
from daemon.repositories.project_skill_file.repository import (
    ProjectSkillEmbeddingRepository,
    ProjectSkillFileRepository,
    ProjectSkillUsageRecordRepository,
)
from daemon.services.project_skill_source.model import (
    SOURCE_PROJECT_FILE,
    ProjectFileSkill,
    mint_address,
)
from daemon.services.project_skill_source.service import ProjectSkillSourceService
from daemon.services.skill_injection_service import (
    SkillInjectionService,
    _is_project_file_skill as _is_project_file_skill_injection,
)
from daemon.services.skill_search_service import (
    SkillSearchService,
    _is_project_file_skill as _is_project_file_skill_search,
)
from daemon.services.skill_store_service import SkillStoreService
from daemon.tools.skill_tools import create_skill_tools
from tests.helpers.project_skill_source_fixtures import write_skill

INSTANCE_ID = "inst-skill-id-verbatim-1"
DB_SKILL_NAME = "verbatim-db-skill"
DB_SKILL_CATEGORY = "pin"
FILE_NS = "tester"
FILE_NAME = "alpha-skill"
FILE_CATEGORY = "workflow"
FILE_VERSION = "1.0.0"

#: The DB row shape (``— source: db`` is ADDITIVE — present only when a
#: file source is wired).
DB_ROW_RE = re.compile(
    r"^- \[(?P<token>[^\]]+)\] (?P<name>.+?) — (?P<cat>.+?) — "
    r"(?P<status>.+?) — source: db$"
)
#: The file row shape (byte-preserved by this lane).
PF_ROW_RE = re.compile(
    r"^- \[(?P<ns>[^\]]+)\] (?P<address>pf:[^\s]+) — (?P<cat>.+?) — "
    r"v(?P<ver>.+?) — source: project-file$"
)
#: The third skill_list section (invalid project-file rows).
INVALID_ROW_RE = re.compile(r"^- (?P<address>pf:[^\s]+) — invalid: (?P<reason>.+)$")
#: The skill_create success line.
CREATE_ID_RE = re.compile(r"created with id (?P<token>[^.\s]+)\.")


# ============================================================
# Harness
# ============================================================


@dataclass
class Harness:
    """The real-tool harness (real repos + real services + real tools)."""

    engine: Any
    canon: str
    project_id: str
    instance_id: str
    store: Any
    file_source: Any
    search: Any
    manager: Any
    tools: dict[str, Any]
    db_skill: Any
    db_skill_id: str
    skill_repo: Any
    usage_repo: Any
    embedding_repo: Any
    pf_file_repo: Any
    file_row: Any = None
    file_skill: Any = None
    file_pseudo_id: str = ""
    file_address: str = ""


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/pin.db")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def canon(tmp_path):
    """A minimal, hermetic project-file canon (one valid skill)."""
    root = str(tmp_path / "proj-root")
    os.makedirs(root, exist_ok=True)
    write_skill(
        root,
        FILE_NS,
        FILE_NAME,
        body_text="Alpha body about verbatim skill ids.\n",
        manifest={
            "schema_version": 1,
            "name": FILE_NAME,
            "body": f"{FILE_NAME}.md",
            "version": FILE_VERSION,
            "description": "Alpha file skill for the verbatim id contract.",
            "category": FILE_CATEGORY,
            "tiers": [1],
            "loads": [],
            "assets": [],
        },
    )
    return root


@pytest.fixture
def broken_canon(tmp_path):
    """A canon with ONE invalid skill (body missing) — the third list section."""
    root = str(tmp_path / "broken-root")
    os.makedirs(root, exist_ok=True)
    write_skill(
        root,
        FILE_NS,
        "broken-skill",
        body_text="never written\n",
        manifest={
            "schema_version": 1,
            "name": "broken-skill",
            "body": "MISSING.md",
            "version": "1.0.0",
            "description": "Broken on purpose.",
            "category": FILE_CATEGORY,
            "tiers": [1],
            "loads": [],
            "assets": [],
        },
        with_body=False,
    )
    return root


async def _build_harness(engine, canon: str, *, with_search: bool = True) -> Harness:
    """Build the REAL production construction over the scratch engine."""
    skill_repo = create_skill_repository(engine=engine, create_tables=False)
    lineage_repo = create_skill_lineage_repository(engine=engine, create_tables=False)
    embedding_repo = create_skill_embedding_repository(engine=engine, create_tables=False)
    project_repo = create_project_repository(engine=engine, create_tables=False)
    instance_repo = create_instance_repository(engine=engine, create_tables=False)
    pf_file_repo = ProjectSkillFileRepository(engine)
    pf_emb_repo = ProjectSkillEmbeddingRepository(engine)
    pf_usage_repo = ProjectSkillUsageRecordRepository(engine)

    store = SkillStoreService(
        skill_repo=skill_repo,
        lineage_repo=lineage_repo,
        embedding_service=None,
    )
    file_source = ProjectSkillSourceService(
        instance_repository=instance_repo,
        project_repository=project_repo,
        file_repository=pf_file_repo,
        embedding_repository=pf_emb_repo,
        embedding_service=None,
        llm_config={},
    )

    project = project_repo.create(
        name="skill-id-verbatim-pin",
        project_type="software",
        main_directory=canon,
        description="Scratch project for the skill id verbatim contract pin.",
    )
    project_id = project.project_id
    instance_repo.create(
        instance_id=INSTANCE_ID,
        agent_id="worker",
        agent_dir=os.path.join(canon, "agents", "worker"),
        metadata={"project_id": project_id},
        project_id=project_id,
    )

    # Seed 1 — the DB skill through the REAL creation path.
    db_skill = await store.create_skill(
        name=DB_SKILL_NAME,
        description="DB-backed skill for the verbatim id contract pin.",
        content="# Verbatim body\n\nThe stored id must be emitted in full.\n",
        project_id=project_id,
        category=DB_SKILL_CATEGORY,
    )

    # Seed 2 — the file skill through the REAL loader entrypoint.
    await file_source.ensure_index(canon, project_id)

    # The REAL fused search service. ``config=None`` ⇒ deterministic
    # stage 3 (``_degraded_select``) — no LLM reachable from this pin.
    search = SkillSearchService(
        skill_repo=skill_repo,
        embedding_repo=embedding_repo,
        embedding_service=None,
        llm_config={},
        config=None,
        skill_bank_repo=None,
        file_source=file_source,
    )

    manager = SimpleNamespace(
        _skill_store_service=store,
        _skill_search_service=search if with_search else None,
        _skill_metrics_service=None,
        _skill_job_dispatcher=None,
        _project_skill_source=file_source,
        _project_skill_file_repository=pf_file_repo,
        _project_skill_usage_repository=pf_usage_repo,
        _instance_repository=instance_repo,
    )
    tools = {t.name: t for t in create_skill_tools(manager, INSTANCE_ID)}

    rows = pf_file_repo.list_by_project(project_id)
    file_row = rows[0] if rows else None
    file_skill = await file_source.lookup_explicit(
        mint_address(FILE_NS, FILE_NAME), project_id
    )

    return Harness(
        engine=engine,
        canon=canon,
        project_id=project_id,
        instance_id=INSTANCE_ID,
        store=store,
        file_source=file_source,
        search=search,
        manager=manager,
        tools=tools,
        db_skill=db_skill,
        db_skill_id=db_skill.id,
        skill_repo=skill_repo,
        usage_repo=pf_usage_repo,
        embedding_repo=pf_emb_repo,
        pf_file_repo=pf_file_repo,
        file_row=file_row,
        file_skill=file_skill,
        file_pseudo_id=str(getattr(file_row, "id", "") or ""),
        file_address=mint_address(FILE_NS, FILE_NAME),
    )


@pytest.fixture
async def harness(engine, canon):
    return await _build_harness(engine, canon)


# ============================================================
# Helpers
# ============================================================


def _db_rows(listing: str) -> list[dict[str, str]]:
    return [m.groupdict() for m in (DB_ROW_RE.match(line) for line in listing.splitlines()) if m]


def _pf_rows(listing: str) -> list[dict[str, str]]:
    return [m.groupdict() for m in (PF_ROW_RE.match(line) for line in listing.splitlines()) if m]


def _refusal(out: str) -> bool:
    return out.startswith("ERROR") or "not yet available" in out


def _doc_name(out: str) -> str:
    """The ``# <name>`` header of a rendered skill document."""
    for line in out.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


async def _view(harness: Harness, skill_id: str) -> str:
    return await harness.tools["skill_view"].ainvoke({"skill_id": skill_id})


# ============================================================
# D1 — the verbatim-contract legs (list → view, per source)
# ============================================================


class TestListVerbatimContract:
    async def test_db_bracket_token_is_the_stored_full_uuid(self, harness):
        """(ii)/(iii)/(iv) DB leg — the bracket token IS the stored uuid4
        and ``skill_view`` resolves it VERBATIM (identity asserted)."""
        listing = await harness.tools["skill_list"].ainvoke({})
        db_rows = _db_rows(listing)
        # Vacuity guard (approver N3): the every-id loop below must not
        # pass over an EMPTY token set.
        assert len(db_rows) >= 1, listing
        pf_rows = _pf_rows(listing)
        assert len(pf_rows) >= 1, listing

        for row in db_rows:
            token = row["token"]
            # (iv) value equality — STR-EQUAL to the stored uuid4 (not a
            # shape match).
            assert token == harness.db_skill_id
            # (ii) verbatim consumption + (iii) identity.
            out = await _view(harness, token)
            assert not _refusal(out), out
            assert _doc_name(out) == DB_SKILL_NAME, out

    async def test_file_row_address_is_mint_address_and_resolves(self, harness):
        """(ii)/(iii)/(iv) FILE leg — the file row carries the ADDRESS
        (``mint_address``), and ``skill_view`` resolves it VERBATIM."""
        listing = await harness.tools["skill_list"].ainvoke({})
        pf_rows = _pf_rows(listing)
        assert len(pf_rows) >= 1, listing

        for row in pf_rows:
            address = row["address"]
            assert address == harness.file_address
            out = await _view(harness, address)
            assert not _refusal(out), out
            assert _doc_name(out) == FILE_NAME, out

    async def test_list_row_shapes_are_preserved(self, harness):
        """R4 — only the bracket TOKEN grows; every separator, the
        ``— source: db`` additivity and the file-row shape stay."""
        listing = await harness.tools["skill_list"].ainvoke({})
        lines = listing.splitlines()

        db_line = next(line for line in lines if line.endswith("— source: db"))
        assert db_line == (
            f"- [{harness.db_skill_id}] {DB_SKILL_NAME} — "
            f"{DB_SKILL_CATEGORY} — active — source: db"
        )
        pf_line = next(line for line in lines if line.endswith("— source: project-file"))
        assert pf_line == (
            f"- [{FILE_NS}] {harness.file_address} — {FILE_CATEGORY} — "
            f"v{FILE_VERSION} — source: project-file"
        )
        # The DB section keeps its header + count shape.
        assert lines[0].startswith("Skills (")
        # The knob-OFF shape: no file source ⇒ no ``— source: db`` tag
        # and the file section disappears entirely.
        harness.manager._project_skill_source = None
        off_listing = await harness.tools["skill_list"].ainvoke({})
        assert "source: db" not in off_listing
        assert off_listing.splitlines()[1] == (
            f"- [{harness.db_skill_id}] {DB_SKILL_NAME} — "
            f"{DB_SKILL_CATEGORY} — active"
        )

    async def test_invalid_rows_section_is_parsed(self, engine, broken_canon):
        """Approver N4 — the THIRD skill_list section (invalid rows) is
        parsed too: it is a reporting surface, not an id surface."""
        h = await _build_harness(engine, broken_canon)
        listing = await h.tools["skill_list"].ainvoke({})
        assert "Invalid project-file skills" in listing
        invalid = [
            m.groupdict()
            for m in (
                INVALID_ROW_RE.match(line)
                for line in listing.splitlines()
            )
            if m
        ]
        assert len(invalid) >= 1, listing
        for row in invalid:
            assert row["address"].startswith("pf:")
            assert row["reason"]


# ============================================================
# D1-4 — the search-payload legs (BOTH render paths)
# ============================================================


class TestSearchPayloadContract:
    async def test_fused_path_file_entry_id_is_consumable(self, harness):
        """The FUSED render path — the nested ``["injected"][i]["skill"]``
        object carries the ADDRESS as its ``id`` (consumable) plus the
        additive ``pseudo_id`` (internal key continuity)."""
        out = await harness.tools["skill_search"].ainvoke(
            {"query": "verbatim skill ids alpha", "limit": 5}
        )
        result = json.loads(out)
        assert result.get("degraded") == "file_only"  # config=None ⇒ stage 3 skipped
        entries = [
            e for e in result["injected"]
            if e.get("skill", {}).get("source") == "project-file"
        ]
        assert len(entries) >= 1, out

        for entry in entries:
            skill = entry["skill"]
            # (iv) consumable: the nested id feeds skill_view VERBATIM.
            assert skill["id"] == harness.file_address
            view_out = await _view(harness, skill["id"])
            assert not _refusal(view_out), view_out
            assert _doc_name(view_out) == FILE_NAME, view_out
            # The additive pseudo_id — the sha16 internal key.
            assert skill["pseudo_id"] == harness.file_pseudo_id
            assert re.fullmatch(r"pf-[0-9a-f]{16}", skill["pseudo_id"])
            # Field-set equality (fused path ONLY): every dataclass field
            # is still emitted + the additive pseudo_id; ``version`` is a
            # PROPERTY, not a field.
            expected_fields = {f.name for f in dataclasses.fields(ProjectFileSkill)}
            assert set(skill.keys()) == expected_fields | {"pseudo_id"}
            assert "version" not in skill
            # Per-field VALUE comparison (approver N2 — the key set alone
            # is near-tautological): every value is source-identical to
            # the live object except ``id`` (the address).
            obj = harness.file_skill
            for f in expected_fields:
                if f == "id":
                    continue
                assert skill[f] == getattr(obj, f), f
            assert skill["id"] == obj.address

    async def test_file_only_path_file_entry_id_is_consumable(self, engine, canon):
        """The FILE-ONLY degraded render path (tool-level
        ``_file_only_bm25_search`` — the DB store absent)."""
        h = await _build_harness(engine, canon, with_search=False)
        out = await h.tools["skill_search"].ainvoke(
            {"query": "verbatim skill ids alpha", "limit": 5}
        )
        result = json.loads(out)
        assert result.get("degraded") == "file_only"
        entries = [e for e in result["injected"] if e.get("source") == "project-file"]
        assert len(entries) >= 1, out

        for entry in entries:
            skill = entry["skill"]
            assert skill["id"] == h.file_address
            view_out = await _view(h, skill["id"])
            assert not _refusal(view_out), view_out
            assert _doc_name(view_out) == FILE_NAME, view_out
            assert skill["pseudo_id"] == h.file_pseudo_id
            assert skill["address"] == h.file_address
            # The file-only payload has its OWN frozen shape (the
            # hand-built payload dict — NOT the dataclass field set).
            assert set(skill.keys()) == {
                "id", "pseudo_id", "name", "description", "content",
                "category", "version", "tiers", "namespace", "address",
                "project_id", "root_path", "body_path", "digest",
                "shadows", "source",
            }

    async def test_fused_path_db_entry_id_is_the_full_uuid(self, harness):
        """The DB entry of the fused path keeps the FULL uuid (the DB
        render path was never truncated)."""
        out = await harness.tools["skill_search"].ainvoke(
            {"query": "verbatim stored id", "limit": 5}
        )
        result = json.loads(out)
        db_entries = [
            e for e in result["injected"]
            if e.get("skill", {}).get("source") != "project-file"
        ]
        assert len(db_entries) >= 1, out
        for entry in db_entries:
            assert entry["skill"]["id"] == harness.db_skill_id


# ============================================================
# D1-4 — skill_create + the injection body
# ============================================================


class TestSkillCreateContract:
    async def test_create_line_id_is_the_stored_uuid_and_resolves(self, harness):
        """The skill_create success line carries the FULL id — and it
        resolves VERBATIM in ``skill_view`` (identity asserted)."""
        out = await harness.tools["skill_create"].ainvoke(
            {
                "name": "verbatim-created-skill",
                "description": "Created through the real tool path.",
                "content": "# Created\n\nThe id in the success line must resolve.\n",
            }
        )
        assert "created with id" in out, out
        match = CREATE_ID_RE.search(out)
        assert match, out
        token = match.group("token")
        # (iv) the token IS the stored uuid — STR-EQUAL, not a prefix
        # and not a length heuristic.
        created_row = harness.skill_repo.get_by_name(
            harness.project_id, "verbatim-created-skill"
        )
        assert created_row is not None
        assert token == created_row.id
        view_out = await _view(harness, token)
        assert not _refusal(view_out), view_out
        assert _doc_name(view_out) == "verbatim-created-skill", view_out


class TestInjectionBodyContract:
    async def test_file_injection_body_renders_the_address(self, harness):
        """The injection body's id segment carries the ADDRESS for file
        skills (the pseudo-id is not routable by any id tool)."""
        service = SkillInjectionService(
            search_service=None,
            config=None,
            ab_test_repo=None,
            skill_repo=None,
        )
        rendered = service._render_file_skill(harness.file_skill)
        assert f"(id: {harness.file_address}, match score:" in rendered, rendered
        assert "(id: pf-" not in rendered, rendered

    async def test_db_injection_body_renders_the_full_uuid(self, harness):
        """The DB injection path is byte-unchanged — it renders the full
        uuid (never a prefix)."""
        service = SkillInjectionService(
            search_service=None,
            config=None,
            ab_test_repo=None,
            skill_repo=None,
        )
        rendered = service._format_injection(
            {"injected": [{"skill": harness.db_skill, "score": 1.0}], "low_match": []}
        )
        assert f"(id: {harness.db_skill_id}, match score:" in rendered, rendered
        assert "(id: pf-" not in rendered, rendered


# ============================================================
# Safety invariants (R3 hard constraint + R2g routing)
# ============================================================


class TestSafetyInvariants:
    async def test_file_skill_id_attribute_stays_the_pseudo_id(self, harness):
        """R3 (M5) — the ``ProjectFileSkill.id`` ATTRIBUTE stays the
        internal sha16 key. The id the AGENT sees may be the address;
        the id the SYSTEM keys on may not change."""
        assert harness.file_skill is not None
        assert harness.file_skill.id == harness.file_pseudo_id
        assert re.fullmatch(r"pf-[0-9a-f]{16}", harness.file_skill.id)
        # The indexed ROW carries the same key (internal continuity).
        assert harness.file_row.id == harness.file_skill.id

    async def test_embeddings_stay_keyed_by_the_pseudo_id(self, harness):
        """M5 — the embedding rows are keyed by the sha16 pseudo-id, and
        the SKILL OBJECT's own id must still REACH them: the rerank
        looks the rows up by the object's id, so an unsafe ``id``
        remap (the attribute carrying the address) would make every row
        unreachable — a SILENT degradation to a neutral score, not an
        error.

        The seed lands on the INDEXED row's key (the stable internal
        key). The reachability assertion goes through ``skill.id`` (the
        object), NOT through the row — asserting via the row would be
        inert under the mutation (approver N1).
        """
        skill = harness.file_skill
        assert skill is not None
        seeded = harness.embedding_repo.replace_for_skill(
            harness.file_pseudo_id, [("verbatim trigger query", [0.1, 0.2, 0.3])]
        )
        assert seeded[0] >= 1  # (inserted, removed)
        # The rows ARE reachable at the key the skill object carries.
        assert len(harness.file_source.file_embeddings_for(skill.id)) >= 1
        # They exist at the internal key ...
        assert len(
            harness.file_source.file_embeddings_for(harness.file_pseudo_id)
        ) >= 1
        # ... and the ADDRESS is not a valid embedding key.
        assert harness.file_source.file_embeddings_for(skill.address) == []

    async def test_feedback_accepts_both_forms_and_keys_by_pseudo_id(self, harness):
        """R2g (M6) — the ``pf:`` address AND the ``pf-<hex16>``
        pseudo-id both record metrics, keyed by the RESOLVED pseudo-id
        (legacy-transcript compatibility is load-bearing)."""
        out = await harness.tools["skill_feedback"].ainvoke(
            {"skill_id": harness.file_address, "applied": True}
        )
        assert "metrics-only" in out, out
        after_address = harness.usage_repo.list_by_skill(harness.file_pseudo_id)
        assert len(after_address) == 1
        first_row_id = after_address[0].id

        # The pseudo-id form lands on the SAME row (the usage write is an
        # upsert keyed by (skill_id, instance_id)) — proof that BOTH
        # input forms resolve to the same internal key.
        out = await harness.tools["skill_feedback"].ainvoke(
            {
                "skill_id": harness.file_pseudo_id,
                "applied": True,
                "note": "second form",
            }
        )
        assert "metrics-only" in out, out
        after_pseudo = harness.usage_repo.list_by_skill(harness.file_pseudo_id)
        assert len(after_pseudo) == 1
        assert after_pseudo[0].id == first_row_id
        assert "second form" in (after_pseudo[0].feedback_note or "")

    async def test_feedback_refusals_write_nothing(self, harness):
        """The W5 fail-closed refusals still refuse — and write NOTHING."""
        # Knob OFF (no file source) ⇒ no scope ⇒ refuse.
        harness.manager._project_skill_source = None
        out = await harness.tools["skill_feedback"].ainvoke(
            {"skill_id": harness.file_address, "applied": True}
        )
        assert "no project scope resolved" in out, out
        assert harness.usage_repo.list_by_skill(harness.file_pseudo_id) == []

        # Knob ON, but the pseudo-id is NOT an indexed file skill.
        harness.manager._project_skill_source = harness.file_source
        out = await harness.tools["skill_feedback"].ainvoke(
            {"skill_id": "pf-deadbeefdeadbeef", "applied": True}
        )
        assert "no indexed file skill with pseudo id" in out, out
        assert harness.usage_repo.list_by_skill("pf-deadbeefdeadbeef") == []

    async def test_injection_attribution_stays_the_pseudo_id(self, harness):
        """Approver N8 — the attribution path (``track_injection``) keeps
        the pseudo-id even though the rendered body now shows the
        address: the agent-facing id and the internal key are two
        different things on purpose."""
        service = SkillInjectionService(
            search_service=None,
            config=None,
            ab_test_repo=None,
            skill_repo=None,
        )
        rendered = service._render_file_skill(harness.file_skill)
        assert f"(id: {harness.file_address}," in rendered
        service.track_injection(
            harness.instance_id, "msg-1", [harness.file_skill.id]
        )
        assert service.get_injected_skill_ids(harness.instance_id, "msg-1") == [
            harness.file_pseudo_id
        ]


# ============================================================
# Project-file discriminator PARITY (council W2 fold, wi-76d89e11)
# ============================================================
#
# ``skill_search_service._is_project_file_skill`` and
# ``skill_injection_service._is_project_file_skill`` are DUPLICATED
# helpers — the duplication is deliberate (importing the canonical one
# would pull ``openai`` into the injection service's import graph —
# approver note N6) but it was UNPINNED: grep over tests/ found ZERO
# hits (a prose comment only). A one-sided divergence would have
# silently re-introduced this lane's exact defect on one path — no
# error, no test failure.
#
# The ``source`` LITERAL is now single-sourced in
# ``project_skill_source.model.SOURCE_PROJECT_FILE`` (W2 fold (i)).
# Constant equality alone is INSUFFICIENT: both helpers could diverge
# in PREDICATE ORDER (e.g. the address test dropped, or the source
# fallback dropped) while the constant stays equal. This battery
# therefore asserts BOTH the expected verdict AND the cross-module
# equality, over a shape set that covers every branch of the
# two-step predicate.


class TestProjectFileDiscriminatorParity:
    """The two duplicated helpers must agree — and agree on the RIGHT
    answer — over every discriminating shape."""

    # (shape-name, duck-typed skill, expected verdict)
    SHAPES = [
        # (a) DB-shaped: no address, no source tag.
        ("db_row_no_address_no_source", SimpleNamespace(), False),
        # (a') DB-shaped with an explicit non-file source tag.
        ("db_row_source_db", SimpleNamespace(source="db"), False),
        # (b) file-shaped via the pf: address, NO source tag.
        ("file_address_only", SimpleNamespace(address="pf:tester:alpha"), True),
        # (b') file-shaped via a pf: address AND the source tag.
        ("file_address_and_source", SimpleNamespace(
            address="pf:tester:alpha", source=SOURCE_PROJECT_FILE
        ), True),
        # (c) file-shaped via the source tag only, NO address.
        ("file_source_tag_only", SimpleNamespace(source=SOURCE_PROJECT_FILE), True),
        # (d) a NON-pf: address — the prefix test must NOT fire.
        ("non_pf_address", SimpleNamespace(address="not-a-pf-address"), False),
        # (d') a non-pf: address WITH the source tag — the fallback fires.
        ("non_pf_address_with_source", SimpleNamespace(
            address="not-a-pf-address", source=SOURCE_PROJECT_FILE
        ), True),
        # (e) the bare prefix without the colon — NOT a pf: address.
        ("prefix_without_colon", SimpleNamespace(address="pf"), False),
        # (f) a non-STRING address — the isinstance() guard must hold.
        ("non_string_address", SimpleNamespace(address=12345), False),
        # (g) a None address with the source tag — the fallback fires.
        ("none_address_with_source", SimpleNamespace(
            address=None, source=SOURCE_PROJECT_FILE
        ), True),
        # (h) a wrong-case source tag — NOT the file source.
        ("wrong_case_source", SimpleNamespace(source="Project-File"), False),
        # (i) a near-miss source tag.
        ("near_miss_source", SimpleNamespace(source="project_files"), False),
    ]

    def test_helpers_agree_on_every_shape(self):
        """Every shape: both helpers return the SAME verdict, and that
        verdict is the EXPECTED one (a shared-wrong answer fails)."""
        for name, skill, expected in self.SHAPES:
            search_verdict = _is_project_file_skill_search(skill)
            injection_verdict = _is_project_file_skill_injection(skill)
            assert search_verdict == injection_verdict, (
                f"shape {name!r}: the two helpers DISAGREE — "
                f"search={search_verdict!r} injection={injection_verdict!r}"
            )
            assert search_verdict is expected, (
                f"shape {name!r}: expected {expected!r}, got {search_verdict!r}"
            )

    def test_search_helper_alone_agrees_on_every_shape(self):
        """The search-side helper is the CANONICAL semantics (A-7): the
        ``pf:`` address test FIRST, then the duck-typed source tag
        fallback. Pinned independently so the parity leg above cannot
        be satisfied by both drifting together."""
        for name, skill, expected in self.SHAPES:
            assert _is_project_file_skill_search(skill) is expected, (
                f"shape {name!r}: the search helper returned "
                f"{_is_project_file_skill_search(skill)!r}, expected {expected!r}"
            )

    def test_injection_helper_alone_agrees_on_every_shape(self):
        """The injection-side helper mirrors the canonical semantics."""
        for name, skill, expected in self.SHAPES:
            assert _is_project_file_skill_injection(skill) is expected, (
                f"shape {name!r}: the injection helper returned "
                f"{_is_project_file_skill_injection(skill)!r}, expected {expected!r}"
            )

    def test_source_literal_is_single_sourced(self):
        """W2 fold (i) — the ``project-file`` literal is defined ONCE, in
        ``project_skill_source.model``, and BOTH consumers reference
        that single definition (no local re-spelling that could drift
        one-sided)."""
        from daemon.services import skill_injection_service, skill_search_service
        from daemon.services.project_skill_source import model as pf_model

        canonical = pf_model.SOURCE_PROJECT_FILE
        assert canonical == "project-file"
        # Both consumers resolve to the SAME object, not equal copies.
        assert skill_search_service.SOURCE_PROJECT_FILE is canonical
        assert skill_injection_service.SOURCE_PROJECT_FILE is canonical
