"""POK trust-tier unit pack (Phase 2 — trust-tier addendum T-6 gate).

D1 legs per the addendum's AC-T1..T7 table:

* **AC-T1** — CHECK ``source_tier ∈ agent|curated`` mirrored in the
  additive ``.sql`` AND the SQLModel table; default ``'agent'``; the
  Phase-1 row shape reads byte-unchanged after the ALTER (the
  throwaway-SQLite breaker proof, encoded); the
  ``_ensure_postgres_columns`` entry carries the same column + CHECK
  (source-presence pin); named ``source_tier`` NOT ``tier``.
* **AC-T2** — the three typed refusals: non-curator upsert-curated →
  ``pok_tier_requires_curator``; non-curator supersede-of-curated →
  ``pok_curated_requires_curator``; non-curator
  ``pok_curate(source_tier=…)`` refused (``pok_curator_required``).
  Plus the vocabulary refusal (``pok_source_tier_invalid``).
* **AC-T3** — the authority lattice (≥4 legs): leader CAN
  upsert/supersede/flip curated; PM same; developer CANNOT, both
  directions. Tier INHERITANCE on supersede (omitted → inherit the
  target's tier; explicit curator param overrides).
* **AC-T4** — per-view bounds with curated present: the named worst
  case (6 pinned-family + 2 curated = 8 rows → 47 + 40 + 8×333 + 60
  = 2,811 ≤ 2,900 — the standing derivation, pinned arithmetically);
  curated cap 2/view; audience-invisible curated = excluded-for-view;
  NO curated drag (a band-admitted member leaves its family to the
  ranked scan as one consumed-aware unit).
* **AC-T5** — universal read across tiers (developer ``pok_query``
  returns curated entries; tier never filters a read); ``[CURATED]``
  renders in the QUERY lineage surface only.
* **AC-T6** — seeding legs: curator seeds per slug in ONE call
  (``pok_upsert(source_tier='curated')`` — the ≤2-call capture); the
  240-ESCAPED budget boundary on the curated write path; ZERO
  guideline content in any ``.sql`` (the no-backfill grep leg).
* **AC-T7** — board-note content assertions: the note exists at
  ``.agents/shared/planning/project-operating-knowledge/
  board-note-wi-d2abe4f7.md`` and carries disposition / mechanism /
  sha / seeding-status / pointers. ari's actual board read-back is
  OUT of lane scope (deferred-to-ari; the note is the lane's handoff
  CONTENT — ari/leader dual-gate owns the board write).

AsyncMock census: the tier surface adds ZERO new ``await``s — every
touched callable (service upsert/curate, repository curate,
admit_view, the three tools) is sync; the census leg pins that shape.

The engine is a StaticPool in-memory SQLite (the Phase-1 pack
pattern); no ``InstanceManager`` is ever constructed.
"""

from __future__ import annotations

import inspect
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.engine import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, select

import daemon.constants as constants
import daemon.repositories.pok.models  # noqa: F401
from daemon.repositories.pok.models import (
    POK_DEFAULT_SOURCE_TIER,
    POK_SOURCE_TIERS,
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
)
from daemon.services.pok_injection import admit_view, render_pok_block
from daemon.services.pok_service import (
    POK_BLOCK_MAX_ENTRIES,
    POK_CURATED_CAP_PER_VIEW,
    POKError,
    POKService,
    is_curator,
)
from daemon.tools.pok import create_pok_tools

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_SQL = (
    REPO_ROOT
    / "daemon/migrations/versions/20261107_110003_add_pok_source_tier.sql"
)
PHASE1_SQL = (
    REPO_ROOT
    / "daemon/migrations/versions/20261107_110001_create_project_operating_knowledge.sql"
)
MANAGER_PY = REPO_ROOT / "daemon/manager.py"
BOARD_NOTE = (
    REPO_ROOT
    / ".agents/shared/planning/project-operating-knowledge/board-note-wi-d2abe4f7.md"
)

WRAPPER_CHARS = len("[SYSTEM CONTEXT: ") + len("Project Operating Knowledge") + len(
    "]\n\n"
)
assert WRAPPER_CHARS == 47  # the §4.5 wrapper arithmetic, pinned
NOW = datetime.now(timezone.utc)


def _make_engine() -> Any:
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture()
def svc_env():
    engine = _make_engine()
    SQLModel.metadata.create_all(
        engine,
        tables=[
            ProjectOperatingKnowledge.__table__,
            ProjectOperatingKnowledgeEmbedding.__table__,
        ],
    )
    yield SimpleNamespace(engine=engine, svc=POKService(engine))


# ── tool-env (the Phase-1 tools-pack pattern, mirrored) ──────────────────


@pytest.fixture()
def tool_env():
    engine = _make_engine()
    SQLModel.metadata.create_all(
        engine,
        tables=[
            ProjectOperatingKnowledge.__table__,
            ProjectOperatingKnowledgeEmbedding.__table__,
        ],
    )

    class FakeProjectRepo:
        def get(self, pid):
            if pid == "default-x":
                return SimpleNamespace(name="__system_default__")
            return SimpleNamespace(name="proj-one")

    def make_repo(project_id, agent_id):
        class FakeRepo:
            def get(self, iid):
                return SimpleNamespace(project_id=project_id, agent_id=agent_id)

        return FakeRepo()

    saved = constants.SYSTEM_DEFAULT_PROJECT_ID
    yield SimpleNamespace(
        engine=engine,
        make_repo=make_repo,
        project_repo=FakeProjectRepo(),
        saved_default=saved,
    )
    constants.SYSTEM_DEFAULT_PROJECT_ID = saved


def _tools(tool_env, agent_id: str, project_id: str = "p1"):
    manager = SimpleNamespace(
        _engine=tool_env.engine,
        _instance_repository=tool_env.make_repo(project_id, agent_id),
        _project_repository=tool_env.project_repo,
    )
    tools = create_pok_tools(
        manager, current_instance_id="inst-1", agent_id=agent_id
    )
    return {t.name: t for t in tools}


def _row(
    slug: str,
    tier: str = "agent",
    audience: str = "both",
    pinned: bool = False,
    disputed: bool = False,
    body: str = "fact body",
    domain: str = "deploy",
) -> ProjectOperatingKnowledge:
    return ProjectOperatingKnowledge(
        id=f"id-{slug}",
        project_id="p1",
        slug=slug,
        domain=domain,
        audience=audience,
        audience_agents=None,
        body=body,
        status="active",
        disputed=disputed,
        content_sha256="h",
        created_by_agent="x",
        created_at="2026-11-01T00:00:00+00:00",
        updated_at="2026-11-01T00:00:00+00:00",
        pinned=pinned,
        source_tier=tier,
    )


# ═══════════════════════════════ AC-T1 ═══════════════════════════════════


class TestAcT1SchemaMirrorAndDefaults:
    def test_sql_migration_mirrors_the_check(self) -> None:
        """The additive .sql carries NOT NULL DEFAULT 'agent' + the
        CHECK with BOTH values (readiness constraint 1 — the .sql is
        the SQLite-side canonical DDL)."""
        text = MIGRATION_SQL.read_text(encoding="utf-8")
        up = text.split("-- UP", 1)[1].split("-- DOWN", 1)[0]
        assert "ADD COLUMN source_tier" in up
        assert "TEXT NOT NULL DEFAULT 'agent'" in up
        assert "CHECK (source_tier IN ('agent', 'curated'))" in up
        # the DOWN adjudication is present (house symmetry)
        down = text.split("-- DOWN", 1)[1]
        assert "DROP COLUMN source_tier" in down

    def test_sqlmodel_check_mirrors_named_source_tier(self) -> None:
        """The SQLModel side mirrors the CHECK, named
        ``ck_pok_source_tier``, and the column is named
        ``source_tier`` NOT ``tier`` (the admission vocabulary owns
        'tier' — addendum T-1)."""
        table = ProjectOperatingKnowledge.__table__
        assert "source_tier" in table.columns
        assert "tier" not in table.columns
        col = table.columns["source_tier"]
        assert col.nullable is False
        assert col.server_default.arg == "agent"
        checks = {
            c.name: str(c.sqltext)
            for c in table.constraints
            if c.__class__.__name__ == "CheckConstraint"
        }
        assert "ck_pok_source_tier" in checks, sorted(checks)
        assert "agent" in checks["ck_pok_source_tier"]
        assert "curated" in checks["ck_pok_source_tier"]

    def test_default_agent_on_insert(self, svc_env) -> None:
        """Omitted tier at capture → 'agent' (zero behavior change
        without opt-in — every Phase-1 call signature is unchanged)."""
        svc = svc_env.svc
        out = svc.upsert(
            project_id="p1", slug="plain-01-fact", body="a fact", agent_id="worker"
        )
        assert out.entry.source_tier == POK_DEFAULT_SOURCE_TIER == "agent"

    def test_vocabulary_tuple(self) -> None:
        assert POK_SOURCE_TIERS == ("agent", "curated")

    def test_tier_never_derived_from_attribution(self) -> None:
        """A-5: attribution ≠ authorization — no code path derives
        source_tier from created_by_agent. The ONLY source_tier
        assignment in the repository layer is the curator flip."""
        from daemon.repositories.pok import repository as repo_mod

        src = inspect.getsource(repo_mod)
        assignments = [
            line.strip()
            for line in src.splitlines()
            if ".source_tier" in line and "=" in line and "==" not in line
        ]
        assert assignments == ["row.source_tier = source_tier"], assignments

    def test_throwaway_sqlite_breaker_proof(self, tmp_path) -> None:
        """The fresh-SQLite breaker lesson, encoded: the EXACT Phase-2
        ALTER executes on a fresh SQLite holding the verbatim Phase-1
        table; the DEFAULT 'agent' satisfies the CHECK for
        pre-existing rows (backfill is byte-preserving); out-of-
        vocabulary values FIRE the CHECK; NULL bites."""
        db_path = tmp_path / "pok_breaker.sqlite3"
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.cursor()
            phase1_up = PHASE1_SQL.read_text(encoding="utf-8").split(
                "-- UP", 1
            )[1].split("-- DOWN", 1)[0]
            cur.executescript(phase1_up)
            # a Phase-1-shaped row (pre-tier columns only)
            cur.execute(
                "INSERT INTO project_operating_knowledge (id, project_id, slug,"
                " body, content_sha256, created_by_agent, created_at, updated_at)"
                " VALUES ('row-1','p1','legacy-01-row','phase-1 body','h',"
                "'leader','2026-11-07T00:00:00+00:00','2026-11-07T00:00:00+00:00')"
            )
            conn.commit()
            cur.execute(
                "ALTER TABLE project_operating_knowledge ADD COLUMN source_tier "
                "TEXT NOT NULL DEFAULT 'agent' "
                "CHECK (source_tier IN ('agent', 'curated'))"
            )
            conn.commit()
            # Phase-1 row reads byte-unchanged + tier 'agent'
            row = cur.execute(
                "SELECT id, project_id, slug, body, status, pinned, source_tier"
                " FROM project_operating_knowledge WHERE id='row-1'"
            ).fetchone()
            assert row == (
                "row-1",
                "p1",
                "legacy-01-row",
                "phase-1 body",
                "active",
                0,
                "agent",
            )
            # explicit curated passes; bogus FIRES the CHECK; NULL bites
            cur.execute(
                "INSERT INTO project_operating_knowledge (id, project_id, slug,"
                " body, content_sha256, created_by_agent, created_at, updated_at,"
                " source_tier) VALUES ('r2','p1','g-01-x','b','h2','leader','t','t',"
                "'curated')"
            )
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                cur.execute(
                    "INSERT INTO project_operating_knowledge (id, project_id,"
                    " slug, body, content_sha256, created_by_agent, created_at,"
                    " updated_at, source_tier) VALUES ('r3','p1','b-01-x','x',"
                    "'h3','worker','t','t','tier')"
                )
                conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                cur.execute(
                    "INSERT INTO project_operating_knowledge (id, project_id,"
                    " slug, body, content_sha256, created_by_agent, created_at,"
                    " updated_at, source_tier) VALUES ('r4','p1','n-01-x','x',"
                    "'h4','worker','t','t',NULL)"
                )
                conn.commit()
        finally:
            conn.close()

    def test_ensure_postgres_columns_carries_the_entry(self) -> None:
        """The live-evolution path (Phase 2 lands post-Phase-1-promote
        ⇒ live PG already has the table): the
        ``_ensure_postgres_columns`` statement list carries the
        idempotent ADD COLUMN IF NOT EXISTS with the same column +
        DEFAULT + CHECK (source-presence pin on manager.py; the
        statement is a parenthesized string concatenation, so the
        fragments are pinned individually)."""
        src = MANAGER_PY.read_text(encoding="utf-8")
        assert (
            "ALTER TABLE project_operating_knowledge ADD COLUMN IF NOT EXISTS "
            in src
        )
        assert '"source_tier TEXT NOT NULL DEFAULT \'agent\' "' in src
        assert '"CHECK (source_tier IN (\'agent\', \'curated\'))"' in src
        # the SQLite counterpart is named in the ensure comment block
        assert "20261107_110003_add_pok_source_tier" in src


# ═══════════════════════════════ AC-T2 ═══════════════════════════════════


class TestAcT2TypedRefusals:
    def test_non_curator_upsert_curated_refused_service(self, svc_env) -> None:
        with pytest.raises(POKError) as ei:
            svc_env.svc.upsert(
                project_id="p1",
                slug="guide-01-slug",
                body="a guideline",
                source_tier="curated",
                agent_id="developer",
            )
        assert ei.value.code == "pok_tier_requires_curator"
        # and nothing was written
        assert svc_env.svc._repo.get_by_slug("p1", "guide-01-slug") is None

    def test_non_curator_upsert_curated_refused_tool(self, tool_env) -> None:
        tools = _tools(tool_env, "developer")
        out = tools["pok_upsert"].invoke(
            {"slug": "guide-01-slug", "body": "a guideline", "source_tier": "curated"}
        )
        assert out["code"] == "pok_tier_requires_curator"
        assert "attribution ≠ authorization" in out["error"]

    def test_non_curator_supersede_of_curated_refused(self, svc_env) -> None:
        svc = svc_env.svc
        svc.upsert(
            project_id="p1",
            slug="guide-01-slug",
            body="curated content v1",
            source_tier="curated",
            agent_id="leader",
        )
        with pytest.raises(POKError) as ei:
            svc.upsert(
                project_id="p1",
                slug="guide-01-slug",
                body="curated content v2",
                agent_id="developer",
            )
        assert ei.value.code == "pok_curated_requires_curator"

    def test_non_curator_supersede_of_pinned_echo_unchanged(self, svc_env) -> None:
        """The pinned refusal stays byte-identical with Phase 1 (the
        widened guard ADDS the curated refusal; it does not replace
        the pinned echo)."""
        svc = svc_env.svc
        svc.upsert(
            project_id="p1", slug="pinned-01-x", body="v1", agent_id="leader"
        )
        svc.curate(
            project_id="p1", slug="pinned-01-x", agent_id="leader", pinned=True
        )
        with pytest.raises(POKError) as ei:
            svc.upsert(
                project_id="p1", slug="pinned-01-x", body="v2", agent_id="developer"
            )
        assert ei.value.code == "pok_pinned_requires_curator"

    def test_non_curator_curate_tier_flip_refused_service(self, svc_env) -> None:
        svc = svc_env.svc
        svc.upsert(
            project_id="p1", slug="plain-01-fact", body="a fact", agent_id="worker"
        )
        with pytest.raises(POKError) as ei:
            svc.curate(
                project_id="p1",
                slug="plain-01-fact",
                agent_id="developer",
                source_tier="curated",
            )
        assert ei.value.code == "pok_curator_required"

    def test_non_curator_curate_tier_flip_refused_tool(self, tool_env) -> None:
        tools = _tools(tool_env, "developer")
        tools["pok_upsert"].invoke({"slug": "plain-01-fact", "body": "a fact"})
        out = tools["pok_curate"].invoke(
            {"slug": "plain-01-fact", "source_tier": "curated"}
        )
        assert out["code"] == "pok_curator_required"

    def test_out_of_vocabulary_tier_refused_both_tools(self, svc_env) -> None:
        with pytest.raises(POKError) as ei:
            svc_env.svc.upsert(
                project_id="p1",
                slug="x-01-a",
                body="fact",
                source_tier="tier",
                agent_id="leader",
            )
        assert ei.value.code == "pok_source_tier_invalid"
        with pytest.raises(POKError) as ei2:
            svc_env.svc.curate(
                project_id="p1",
                slug="x-01-a",
                agent_id="leader",
                source_tier="diamond",
            )
        assert ei2.value.code == "pok_source_tier_invalid"


# ═══════════════════════════════ AC-T3 ═══════════════════════════════════


class TestAcT3AuthorityLattice:
    def test_leader_can_upsert_curated_one_call(self, svc_env) -> None:
        """≤2-call capture preserved: ONE call seeds a curated
        guideline."""
        out = svc_env.svc.upsert(
            project_id="p1",
            slug="guide-01-slug",
            body="the guideline fact",
            source_tier="curated",
            agent_id="leader",
        )
        assert out.action == "created"
        assert out.entry.source_tier == "curated"

    def test_leader_supersede_curated_inherits_tier(self, svc_env) -> None:
        svc = svc_env.svc
        svc.upsert(
            project_id="p1",
            slug="guide-01-slug",
            body="v1",
            source_tier="curated",
            agent_id="leader",
        )
        out = svc.upsert(
            project_id="p1",
            slug="guide-01-slug",
            body="v2 refreshed wording",
            agent_id="leader",  # tier OMITTED — inheritance
        )
        assert out.action == "superseded"
        assert out.entry.source_tier == "curated", (
            "omitted tier INHERITS the target's tier — content refresh "
            "never demotes/promotes accidentally"
        )
        # the superseded prior keeps its curated tier (lineage truth)
        prior = svc._repo.get(out.superseded_id)
        assert prior.source_tier == "curated"

    def test_leader_can_flip_curated_to_agent(self, svc_env) -> None:
        svc = svc_env.svc
        svc.upsert(
            project_id="p1",
            slug="guide-01-slug",
            body="f",
            source_tier="curated",
            agent_id="leader",
        )
        row = svc.curate(
            project_id="p1",
            slug="guide-01-slug",
            agent_id="leader",
            source_tier="agent",
        )
        assert row.source_tier == "agent"

    def test_pm_can_upsert_curated_and_flip_up(self, svc_env) -> None:
        svc = svc_env.svc
        out = svc.upsert(
            project_id="p1",
            slug="guide-02-slug",
            body="pm guideline",
            source_tier="curated",
            agent_id="project-manager",
        )
        assert out.entry.source_tier == "curated"
        # the PM can also flip an existing agent row UP
        svc.upsert(
            project_id="p1", slug="plain-02-fact", body="f2", agent_id="worker"
        )
        flipped = svc.curate(
            project_id="p1",
            slug="plain-02-fact",
            agent_id="project-manager",
            source_tier="curated",
        )
        assert flipped.source_tier == "curated"

    def test_explicit_curator_param_overrides_inheritance(self, svc_env) -> None:
        """A curator may DEMOTE on supersede with an explicit param —
        inheritance only applies to the OMITTED tier."""
        svc = svc_env.svc
        svc.upsert(
            project_id="p1",
            slug="guide-01-slug",
            body="v1",
            source_tier="curated",
            agent_id="leader",
        )
        out = svc.upsert(
            project_id="p1",
            slug="guide-01-slug",
            body="v2",
            source_tier="agent",
            agent_id="leader",
        )
        assert out.action == "superseded"
        assert out.entry.source_tier == "agent"

    def test_developer_cannot_write_curated_either_direction(
        self, svc_env
    ) -> None:
        svc = svc_env.svc
        # direction 1: upsert-curated
        with pytest.raises(POKError) as ei:
            svc.upsert(
                project_id="p1",
                slug="guide-03-slug",
                body="f",
                source_tier="curated",
                agent_id="developer",
            )
        assert ei.value.code == "pok_tier_requires_curator"
        # direction 2: flip an existing row up
        svc.upsert(
            project_id="p1", slug="plain-03-fact", body="f", agent_id="developer"
        )
        with pytest.raises(POKError) as ei2:
            svc.curate(
                project_id="p1",
                slug="plain-03-fact",
                agent_id="developer",
                source_tier="curated",
            )
        assert ei2.value.code == "pok_curator_required"

    def test_developer_can_write_agent_tier_omitted(self, svc_env) -> None:
        """The non-curator DEFAULT path is untouched: omit → 'agent',
        write succeeds."""
        out = svc_env.svc.upsert(
            project_id="p1", slug="dev-01-fact", body="f", agent_id="developer"
        )
        assert out.entry.source_tier == "agent"

    def test_curator_predicate_is_leader_pm_only(self) -> None:
        assert is_curator("leader") and is_curator("project-manager")
        assert not is_curator("developer")
        assert not is_curator("worker")
        assert not is_curator("")
        assert not is_curator(None)


# ═══════════════════════════════ AC-T4 ═══════════════════════════════════


def _worst_case_rows() -> list[ProjectOperatingKnowledge]:
    """The named worst case (addendum T-2): 2 pinned families of 3
    (6 rows) + 2 curated = 8 rows, every body at the 240-escaped
    ceiling, max-size slug/domain/audience overhead."""
    body = "x" * 240  # escape-neutral at exactly the budget
    rows: list[ProjectOperatingKnowledge] = []
    for fam in ("pinfam", "pinfam2"):
        for n in (1, 2, 3):
            rows.append(
                _row(
                    f"{fam}-{n:02d}-member-with-a-maximal-slug-tail-aaaaaa",
                    audience="implementation",
                    domain="environment",
                    pinned=(n == 1),
                    body=body,
                )
            )
    rows.append(
        _row(
            "guide-01-a-curated-entry-with-a-maximal-slug-tail-bbbbbb",
            tier="curated",
            audience="implementation",
            domain="environment",
            body=body,
        )
    )
    rows.append(
        _row(
            "guide-02-b-curated-entry-with-a-maximal-slug-tail-cccccc",
            tier="curated",
            audience="implementation",
            domain="environment",
            body=body,
        )
    )
    return rows


class TestAcT4PerViewBoundsWithCurated:
    def test_named_worst_case_8_rows_within_2900(self) -> None:
        """6 pinned-family + 2 curated = 8 rows admitted; the FULL
        render incl. the 47c wrapper is ≤ 2,900; the STANDING
        derivation is pinned arithmetically: 47 + 40 + 8×333 + 60 =
        2,811 ≤ 2,900 (no re-derivation — the bound is a function of
        admitted count × entry ceiling, not of which band supplied
        the entries)."""
        assert POK_BLOCK_MAX_ENTRIES == 8
        result = admit_view(_worst_case_rows(), agent_id="developer", now=NOW)
        assert len(result.entries) == 8
        tiers = [e.tier for e in result.entries]
        assert tiers.count("curated") == 2
        content = render_pok_block(result)
        full = f"[SYSTEM CONTEXT: Project Operating Knowledge]\n\n{content}"
        assert len(full) <= 2_900, len(full)
        # the standing §4.5:110 derivation, pinned (identical numbers)
        assert 47 + 40 + 8 * 333 + 60 == 2_811
        assert 2_811 <= 2_900

    def test_curated_cap_is_two_per_view(self) -> None:
        """4 curated rows, all fitting → exactly 2 admitted INTO the
        band; the remainder flows through the ranked scan (tier
        'ranked') — never dropped."""
        rows = [
            _row(f"guide-{n:02d}-fact", tier="curated") for n in (1, 2, 3, 4)
        ]
        rows.append(_row("misc-01-agent-fact"))
        assert POK_CURATED_CAP_PER_VIEW == 2
        result = admit_view(rows, agent_id="developer", now=NOW)
        bands = [(e.row.slug, e.tier) for e in result.entries]
        curated = [s for s, t in bands if t == "curated"]
        assert curated == ["guide-01-fact", "guide-02-fact"]  # slug order
        assert ("guide-03-fact", "ranked") in bands
        assert ("guide-04-fact", "ranked") in bands

    def test_audience_invisible_curated_excluded_for_view(self) -> None:
        """An audience-invisible curated row is an excluded row FOR
        THAT VIEW (F3 extension) — it neither renders nor drags, and
        other views still see it (no cross-view leakage)."""
        rows = [
            _row("guide-01-ops-only", tier="curated", audience="operation"),
            _row("impl-01-dev-fact", audience="implementation"),
        ]
        dev_view = admit_view(rows, agent_id="developer", now=NOW)
        assert [e.row.slug for e in dev_view.entries] == ["impl-01-dev-fact"]
        assert dev_view.visible_total == 1
        leader_view = admit_view(rows, agent_id="leader", now=NOW)
        assert {e.row.slug for e in leader_view.entries} == {
            "guide-01-ops-only",
            "impl-01-dev-fact",
        }

    def test_no_curated_drag_family_stays_ranked(self) -> None:
        """A band-admitted curated member does NOT pull its family:
        it renders solo at the curated position; the family's other
        members render as ONE ranked unit (consumed-aware expansion,
        no double admission)."""
        rows = [
            _row("fam-01-member", tier="curated"),
            _row("fam-02-member"),
            _row("fam-03-member"),
        ]
        result = admit_view(rows, agent_id="developer", now=NOW)
        bands = [(e.row.slug, e.tier) for e in result.entries]
        assert bands == [
            ("fam-01-member", "curated"),
            ("fam-02-member", "ranked"),
            ("fam-03-member", "ranked"),
        ]
        assert result.omitted == 0

    def test_pinned_drag_consumes_curated_member_once(self) -> None:
        """A pinned family carrying a curated member admits it ONCE,
        at the pinned position (the drag consumes it; the band skips
        consumed rows)."""
        rows = [
            _row("fam-01-member", tier="curated", pinned=True),
            _row("fam-02-member"),
        ]
        result = admit_view(rows, agent_id="developer", now=NOW)
        bands = [(e.row.slug, e.tier) for e in result.entries]
        assert bands == [
            ("fam-01-member", "pinned"),
            ("fam-02-member", "pinned"),
        ]
        assert result.omitted == 0


# ═══════════════════════════════ AC-T5 ═══════════════════════════════════


class TestAcT5UniversalReadAndLineageMarker:
    def test_developer_query_returns_curated_entries(self, svc_env) -> None:
        """Universal read across tiers (addendum T-4): the tier NEVER
        filters a read — a developer reads curated + agent rows."""
        svc = svc_env.svc
        svc.upsert(
            project_id="p1",
            slug="guide-01-slug",
            body="the guideline",
            source_tier="curated",
            agent_id="leader",
        )
        svc.upsert(
            project_id="p1", slug="plain-01-fact", body="a fact", agent_id="worker"
        )
        out = svc.query(project_id="p1", agent_id="developer")
        slugs = {e.slug for e in out.entries}
        assert slugs == {"guide-01-slug", "plain-01-fact"}
        tiers = {e.slug: e.source_tier for e in out.entries}
        assert tiers["guide-01-slug"] == "curated"
        assert tiers["plain-01-fact"] == "agent"

    def test_tool_query_carries_source_tier_and_lineage_marker(
        self, tool_env
    ) -> None:
        tools = _tools(tool_env, "leader")
        tools["pok_upsert"].invoke(
            {
                "slug": "guide-01-slug",
                "body": "the guideline v1",
                "source_tier": "curated",
            }
        )
        # curator supersedes with content v2 (tier omitted → inherits)
        tools["pok_upsert"].invoke(
            {"slug": "guide-01-slug", "body": "the guideline v2"}
        )
        dev_tools = _tools(tool_env, "developer")
        out = dev_tools["pok_query"].invoke({})
        entries = {e["slug"]: e for e in out["entries"]}
        entry = entries["guide-01-slug"]
        assert entry["source_tier"] == "curated"
        lineage = entry["lineage"]
        assert len(lineage) == 1
        prior = lineage[0]
        assert prior["source_tier"] == "curated"
        assert prior["marker"] == "[CURATED]"
        assert prior["body"] == "the guideline v1"

    def test_agent_tier_lineage_rows_carry_no_marker(self, tool_env) -> None:
        tools = _tools(tool_env, "developer")
        tools["pok_upsert"].invoke({"slug": "plain-01-fact", "body": "v1"})
        tools["pok_upsert"].invoke({"slug": "plain-01-fact", "body": "v2"})
        out = tools["pok_query"].invoke({})
        lineage = out["entries"][0]["lineage"]
        assert lineage and all("marker" not in r for r in lineage)


# ═══════════════════════════════ AC-T6 ═══════════════════════════════════


class TestAcT6SeedingLegs:
    def test_curator_seeds_per_slug_one_call_each(self, svc_env) -> None:
        """Tool-lane seeding (addendum T-5): a CURATOR seeds per slug
        with ``source_tier='curated'`` — one call per slug (the
        ≤2-call capture; optional second pok_curate to pin)."""
        svc = svc_env.svc
        seeded = []
        for slug, body in (
            ("guide-01-merge-gate", "Merging to master triggers the image CI build."),
            ("guide-02-verify-digest", "Verify the image tag digest after the build."),
        ):
            out = svc.upsert(
                project_id="p1",
                slug=slug,
                body=body,
                source_tier="curated",
                agent_id="leader",
            )
            assert out.action == "created" and out.entry.source_tier == "curated"
            seeded.append(slug)
        rows = svc._repo.list_entries("p1", statuses=("active",))
        assert {r.slug for r in rows} == set(seeded)
        assert all(r.source_tier == "curated" for r in rows)

    def test_240_escaped_budget_boundary_on_curated_write(self, svc_env) -> None:
        """The curated write path obeys the SAME escape-aware budget:
        exactly 240 escaped chars passes; 241 refuses
        (pok_body_oversize)."""
        svc = svc_env.svc
        boundary = "x" * 240  # escape-neutral: '&' '<' '>' absent
        out = svc.upsert(
            project_id="p1",
            slug="guide-01-boundary",
            body=boundary,
            source_tier="curated",
            agent_id="leader",
        )
        assert out.action == "created"
        over = "y" * 241
        with pytest.raises(POKError) as ei:
            svc.upsert(
                project_id="p1",
                slug="guide-02-boundary",
                body=over,
                source_tier="curated",
                agent_id="leader",
            )
        assert ei.value.code == "pok_body_oversize"
        # escape INFLATION counts (W5): '&' escapes to \u0026 (6×) —
        # 40 '&' chars measure exactly 240 post-escape: passes; 41
        # inflate to 246: refuses.
        amp = "&" * 40
        out2 = svc.upsert(
            project_id="p1",
            slug="guide-03-boundary",
            body=amp,
            source_tier="curated",
            agent_id="leader",
        )
        assert out2.action == "created"
        with pytest.raises(POKError):
            svc.upsert(
                project_id="p1",
                slug="guide-04-boundary",
                body="&" * 41,
                source_tier="curated",
                agent_id="leader",
            )

    def test_zero_guideline_content_in_any_sql(self) -> None:
        """The no-backfill grep leg (addendum T-5): seeding is the
        TOOL lane — NO .sql in the versions directory INSERTs into
        project_operating_knowledge, and no seeded guideline body
        text appears in any migration file."""
        versions = REPO_ROOT / "daemon/migrations/versions"
        sql_files = sorted(versions.glob("*.sql"))
        assert sql_files, "the versions directory must exist"
        guideline_bodies = (
            "Merging to master triggers the image CI build.",
            "Verify the image tag digest after the build.",
            "The artifact is a container image.",
            "Deploy order: daoduc backup first.",
        )
        for path in sql_files:
            text = path.read_text(encoding="utf-8")
            assert "insert into project_operating_knowledge" not in text.lower(), (
                f"{path.name}: SQL backfill is forbidden (tool lane only)"
            )
            for body in guideline_bodies:
                assert body.lower() not in text.lower(), (
                    f"{path.name}: guideline content in a migration file"
                )
        # the Phase-2 file specifically carries DDL only
        up = MIGRATION_SQL.read_text(encoding="utf-8").split("-- UP", 1)[
            1
        ].split("-- DOWN", 1)[0]
        assert "INSERT" not in up.upper()


# ═══════════════════════════════ AC-T7 ═══════════════════════════════════


class TestAcT7BoardNoteContent:
    @pytest.fixture(autouse=True)
    def _note(self):
        assert BOARD_NOTE.exists(), (
            f"the board-note content file is missing: {BOARD_NOTE}"
        )
        raw = BOARD_NOTE.read_text(encoding="utf-8")
        # prose wraps — match on whitespace-normalized text
        self.text = " ".join(raw.split())

    def test_disposition(self) -> None:
        assert "D3-folded-into-POK" in self.text

    def test_mechanism_one_liner(self) -> None:
        """Both phrasings merged coherently: the addendum §T-5 line AND
        the leader's one-liner."""
        assert "source_tier" in self.text
        assert "agent-captured vs dev-curated" in self.text
        assert "cap 2 per view" in self.text
        assert "curator-only" in self.text
        assert "dev-curated immutable reference" in self.text

    def test_finalization_sha_present(self) -> None:
        assert re.search(r"\b[0-9a-f]{40}\b", self.text), (
            "the note must carry the final tier-implementation commit sha"
        )

    def test_seeding_status(self) -> None:
        assert "mechanism live; content authoring future" in self.text
        assert "8ffd3082-a1bb-4273-a5ea-e9ac87f5ceca" in self.text
        assert "NO SQL backfill" in self.text

    def test_pointers_and_close_intent(self) -> None:
        assert "trust-tier-addendum.md" in self.text
        assert "annotate-and-close" in self.text
        assert "pointer, never a duplicate" in self.text


# ═══════════════════════════════ census ══════════════════════════════════


class TestTierSurfaceAsyncCensus:
    def test_no_new_awaits_on_the_tier_surface(self) -> None:
        """AsyncMock census: the tier surface adds ZERO new awaits —
        every touched callable is sync (the service/repository/
        injection/tool functions)."""
        from daemon.repositories.pok.repository import POKRepository

        for fn in (
            POKService.upsert,
            POKService.curate,
            POKService.query,
            POKRepository.curate,
            admit_view,
            render_pok_block,
            create_pok_tools,
        ):
            assert not inspect.iscoroutinefunction(fn), fn
