"""Phase-3 T7 pack (a) — digest unit + determinism + file-write paths.

Covers plan ``phase3-plan.md`` §4 T7 (``digest unit + determinism (T1) —
extend to the T3 FILE-WRITE path (byte-identical modulo the generated-at
header), not just the rollup; weight join/fallback (T2) ... windows D-1 /
[D-7,D-1]``) and the phase-5 G5 gate.

THE FIVE BINDING DETERMINISM ASSERTIONS (reviewer-adjudicated, ruled
grouping semantics — project sections ``project_id`` ASC, within-section
``(-score, tool_name)``, agent lines ``agent_id`` ASC):

  (i)   two runs on one fixture + date are byte-identical modulo the
        generated-at header — BOTH dialects (dev-fenced PG + tmp SQLite);
  (ii)  within a project section: score DESC, then ``tool_name`` ASC —
        driven by a >=3-tool tie at the 230 fallback weight;
  (iii) project section headers in ``project_id`` ASC order;
  (iv)  agent lines in ``agent_id`` ASC order;
  (v)   the score basis is pinned to ``calls_7d`` (not ``calls_24h``).

WINDOW PINS (D-7, frozen): "last-24h" = complete previous UTC day (D-1);
"cumulative-week" = [D-7, D-1] complete UTC days. The fixture therefore
carries a row on the D-7 lower BOUNDARY (must be counted) and a row one
day older (D-8 — must be dropped).

REVIEWER RULINGS OBSERVED, NOT RE-PINNED AWAY:
* 9(a) — the committed ``tool-weights.json`` artifact is the sanctioned
  empty ``{}``. The 230 fallback IS the sanctioned state, so the weight
  legs exercise the FALLBACK by construction (an artifact-absent tool
  name) instead of pinning the file's current contents (which T2 may
  legitimately fill later — a content pin would rot).
* 9(c) — the truthy-set asymmetry between the seeder and the collection
  key is DELIBERATE and is not touched here.

DIALECT FENCE (dev only, zero live contact): the PostgreSQL leg reads the
worktree ``.env`` file's ``POSTGRES_*`` fence, REFUSES any target whose
database name is not exactly ``ensemble_dev``, refuses a non-loopback
host, and runs inside a THROWAWAY schema (``t7digest_<uuid8>``) that is
dropped with CASCADE at teardown — the shared dev database is otherwise
untouched. Ambient ``POSTGRES_*`` / ``DATABASE_URL`` are never consulted,
so an offsite shell cannot redirect the engine. An unreachable dev PG
skips that leg cleanly; the SQLite leg always runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session

from daemon.repositories.tool_usage.models import ToolUsageDaily
from daemon.services import tool_usage_digest as tud
from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
)
from tests.helpers.tool_usage_t7_pg_fence import pg_throwaway_schema_engine

#: The run date (D). 08:00 ICT == 01:00 UTC of the same UTC date.
RUN_DATE = "2026-11-21"
#: D-1 — the complete previous UTC day ("last-24h").
DAY_D1 = "2026-11-20"
#: D-7 — the FIRST day of the cumulative-week window (lower boundary).
DAY_D7 = "2026-11-14"
#: D-8 — one day older than the window: must NOT be counted.
DAY_D8 = "2026-11-13"

PROJECT_MAIN = "p-main"
PROJECT_AA = "aa-proj"
PROJECT_ZZ = "zz-proj"


# ── fixture builders ───────────────────────────────────────────────────────


def _row(
    day: str,
    project_id: str,
    tool_name: str,
    agent_id: str,
    call_count: int,
) -> ToolUsageDaily:
    return ToolUsageDaily(
        day=day,
        project_id=project_id,
        tool_name=tool_name,
        agent_id=agent_id,
        call_count=call_count,
        updated_at=f"{day}T00:00:00+00:00",
    )


def _determinism_fixture_rows() -> list[ToolUsageDaily]:
    """The ruled tie fixture (>=3 tools at the 230 fallback weight).

    ``p-main`` carries, in expected score order:

    ==============  ===========  =========  =======  =====
    tool            weight       calls_7d   score    note
    ==============  ===========  =========  =======  =====
    week_only       230 (fallb)  5          1150     D-7 boundary row
    winner_tool     230 (fallb)  3          690      D-1
    tie_a           230 (fallb)  2          460      two agents
    tie_b           230 (fallb)  2          460      1 agent
    tie_c           230 (fallb)  2          460      1 agent
    ==============  ===========  =========  =======  =====

    ``old_tool`` sits at D-8 and must be dropped by the window filter;
    it doubles as the window-upper-bound control for the 7-day sum.
    """
    return [
        # -- the scored tools (project p-main, all at the 230 fallback) --
        _row(DAY_D7, PROJECT_MAIN, "week_only", "c-agent", 5),
        _row(DAY_D1, PROJECT_MAIN, "winner_tool", "c-agent", 3),
        _row(DAY_D1, PROJECT_MAIN, "tie_a", "z-agent", 1),
        _row(DAY_D1, PROJECT_MAIN, "tie_a", "a-agent", 1),
        _row(DAY_D1, PROJECT_MAIN, "tie_b", "m-agent", 2),
        _row(DAY_D1, PROJECT_MAIN, "tie_c", "b-agent", 2),
        # -- the window control: one day older than D-7 → excluded --
        _row(DAY_D8, PROJECT_MAIN, "old_tool", "c-agent", 7),
        # -- project section ordering (aa < p-main < zz) --
        _row(DAY_D1, PROJECT_AA, "bash", "unknown", 1),
        _row(DAY_D1, PROJECT_ZZ, "image", "unknown", 1),
    ]


def _seed(engine: Engine, rows: list[ToolUsageDaily]) -> None:
    with Session(engine) as session:
        session.add_all(rows)
        session.commit()


def _sqlite_engine(tmp_path: Path, name: str = "digest.db") -> Engine:
    """File-backed SQLite (never ``:memory:`` — several Sessions must
    see one database)."""
    engine = create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    SQLModel.metadata.create_all(engine, tables=[ToolUsageDaily.__table__])
    return engine


@pytest.fixture(params=["sqlite", "postgres"])
def digest_engine(request, tmp_path):
    """The dual-dialect engine: tmp SQLite + dev-fenced PG."""
    if request.param == "sqlite":
        engine = _sqlite_engine(tmp_path)
        try:
            yield engine
        finally:
            engine.dispose()
        return
    with pg_throwaway_schema_engine([ToolUsageDaily.__table__]) as engine:
        yield engine


# ── helpers on the rendered markdown ───────────────────────────────────────


def _strip_generated_at(markdown: str) -> str:
    """Drop the ONE wall-clock line — everything else must be identical."""
    out = []
    for line in markdown.splitlines():
        if line.startswith("Generated at:"):
            out.append("Generated at: <normalized>")
        else:
            out.append(line)
    return "\n".join(out)


def _section_tools(markdown: str, project_id: str | None = None) -> list[str]:
    """The ``| Tool |`` rows of one project table, in rendered order.

    ``project_id=None`` selects the FIRST project section.
    """
    tools: list[str] = []
    in_section = project_id is None
    in_table = False
    for line in markdown.splitlines():
        if line.startswith("## Project: "):
            if in_section and project_id is not None:
                break
            in_section = line[len("## Project: ") :] == project_id
            continue
        if not in_section:
            continue
        if line == "| Tool | Weight | Calls 24h | Calls 7d | Score | Allow-listed |":
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            cells = [c.strip() for c in line.strip("|").split("|")]
            if set(cells[0]) <= {"-"}:
                continue
            tools.append(cells[0])
    return tools


def _project_headers(markdown: str) -> list[str]:
    return [
        line[len("## Project: ") :]
        for line in markdown.splitlines()
        if line.startswith("## Project: ")
    ]


def _table_row(markdown: str, tool_name: str) -> list[str]:
    """The rendered table cells for ``tool_name`` (first match)."""
    for line in markdown.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if cells and cells[0] == tool_name:
            return cells
    raise AssertionError(f"{tool_name!r} not rendered as a table row")


def _agent_lines(markdown: str, tool_name: str) -> list[str]:
    """The per-agent bullet lines under ``#### <tool_name>``."""
    lines: list[str] = []
    capture = False
    for line in markdown.splitlines():
        if line == f"#### {tool_name}":
            capture = True
            continue
        if capture:
            if line.startswith("- "):
                lines.append(line)
                continue
            break
    return lines


@pytest.fixture()
def hermetic_artifacts(monkeypatch):
    """Neutralize the committed artifacts for math-only legs.

    ``tool_weights`` / ``allowlists`` are the two module-level readers the
    rollup consults, so patching THEM keeps the lock-hold math independent
    of whatever the committed JSON artifacts currently carry (the
    sanctioned ``{}`` weight artifact means the fallback is the live state
    anyway — 9(a)).

    ``allowlist_source`` is neutralized TOO, with a minimal VALID schema-2
    envelope in state ``"ok"`` — never a degraded state. A degraded state
    would turn EVERY verdict into ``"unknown"``, which is a verdict change
    and would make these legs vacuous. An empty ``agents`` map makes NO
    per-agent claim (§3.6.3 step 3), while the full 278-name vocabulary
    keeps the reader's decision path live: a known name is DECIDED (``"no"``
    against an empty flat map), an unknown name is genuinely undecidable.
    """
    monkeypatch.setattr(tud, "tool_weights", lambda: {})
    monkeypatch.setattr(tud, "allowlists", lambda: {})
    monkeypatch.setattr(
        tud,
        "allowlist_source",
        lambda: tud.AllowSource(
            state="ok",
            schema_version=2,
            agents={},
            categories={},
            runtime_only_categories=[],
            privileged_categories=[],
            dynamic_prefixes=[],
            innate_skill_categories={},
            known_tool_names=set(KNOWN_TOOL_NAMES) | set(DYNAMIC_TOOL_NAMES),
            unresolved={"allow": [], "deny": []},
            provenance={},
        ),
    )
    return None


# ══════════════════════════════════════════════════════════════════════════
# (i) determinism — two runs, identical fixture + date, both dialects
# ══════════════════════════════════════════════════════════════════════════


class TestDeterminismTwoRuns:
    def test_rollup_and_render_are_byte_identical_across_two_runs(
        self, digest_engine
    ):
        """(i) rollup path: two passes over one fixture == one output."""
        _seed(digest_engine, _determinism_fixture_rows())

        first = tud.render_markdown(tud.rollup(digest_engine, RUN_DATE), "FIXED")
        second = tud.render_markdown(tud.rollup(digest_engine, RUN_DATE), "FIXED")

        assert first == second

    def test_file_write_is_byte_identical_modulo_generated_at(
        self, digest_engine, monkeypatch, tmp_path
    ):
        """(i) FILE-WRITE path (T3): same-day re-run overwrites with the
        same bytes except the generated-at header."""
        root = tmp_path / "digest-root"
        monkeypatch.setenv(tud.DIGEST_ROOT_ENV, str(root))
        _seed(digest_engine, _determinism_fixture_rows())

        md1, path1 = tud.write_digest(digest_engine, RUN_DATE)
        written_1 = path1.read_text(encoding="utf-8")
        md2, path2 = tud.write_digest(digest_engine, RUN_DATE)
        written_2 = path2.read_text(encoding="utf-8")

        assert path1 == path2 == root / f"{RUN_DATE}.md"
        assert md1 == written_1 and md2 == written_2
        assert _strip_generated_at(written_1) == _strip_generated_at(written_2)
        # Exactly ONE file for the date — the re-run overwrote, not appended.
        assert sorted(p.name for p in root.iterdir()) == [f"{RUN_DATE}.md"]

    def test_the_generated_at_header_is_the_only_differing_line(
        self, digest_engine, monkeypatch, tmp_path
    ):
        """The header normalization is DISCRIMINATING: with two different
        header values the raw bytes differ, and only the header line moves
        (a vacuous ``==`` on two identical strings would prove nothing)."""
        root = tmp_path / "digest-root"
        monkeypatch.setenv(tud.DIGEST_ROOT_ENV, str(root))
        _seed(digest_engine, _determinism_fixture_rows())

        data = tud.rollup(digest_engine, RUN_DATE)
        early = tud.render_markdown(data, "2026-11-21T01:00:00+00:00")
        late = tud.render_markdown(data, "2026-11-21T09:30:00+00:00")

        assert early != late
        diff = [
            (a, b)
            for a, b in zip(early.splitlines(), late.splitlines())
            if a != b
        ]
        assert len(diff) == 1
        assert diff[0][0].startswith("Generated at:")
        assert diff[0][1].startswith("Generated at:")
        assert _strip_generated_at(early) == _strip_generated_at(late)


# ══════════════════════════════════════════════════════════════════════════
# (ii) within-section order: score DESC, then tool_name ASC (the 230 tie)
# ══════════════════════════════════════════════════════════════════════════


class TestWithinSectionOrdering:
    def test_score_desc_then_tool_name_asc_inside_one_project(
        self, digest_engine, hermetic_artifacts
    ):
        """(ii) three tools SHARE the 230 fallback weight and an equal
        score; the secondary key ``tool_name`` ASC decides, so insertion
        order cannot leak into the output."""
        _seed(digest_engine, _determinism_fixture_rows())
        md = tud.render_markdown(tud.rollup(digest_engine, RUN_DATE), "FIXED")

        main_tools = _section_tools(md, PROJECT_MAIN)
        assert main_tools == [
            "week_only",   # 230 * 5 = 1150
            "winner_tool",  # 230 * 3 =  690
            "tie_a",       # 230 * 2 =  460  (tie → name ASC)
            "tie_b",       # 230 * 2 =  460
            "tie_c",       # 230 * 2 =  460
        ]
        # The three tied rows genuinely share one score — otherwise the
        # tie-breaking leg above would be vacuous.
        scores = {name: int(_table_row(md, name)[4]) for name in ("tie_a", "tie_b", "tie_c")}
        assert set(scores.values()) == {460}

    def test_a_tool_outside_the_week_window_is_not_rendered(
        self, digest_engine, hermetic_artifacts
    ):
        """Window upper bound: a row one day OLDER than D-7 is dropped."""
        _seed(digest_engine, _determinism_fixture_rows())
        md = tud.render_markdown(tud.rollup(digest_engine, RUN_DATE), "FIXED")

        assert "old_tool" not in _section_tools(md, PROJECT_MAIN)


# ══════════════════════════════════════════════════════════════════════════
# (iii) project sections in project_id ASC order
# ══════════════════════════════════════════════════════════════════════════


class TestProjectSectionOrdering:
    def test_project_headers_are_project_id_asc(
        self, digest_engine, hermetic_artifacts
    ):
        """(iii) sections render in ``project_id`` ASC — the fixture's
        insertion order is deliberately NOT sorted."""
        _seed(digest_engine, _determinism_fixture_rows())
        md = tud.render_markdown(tud.rollup(digest_engine, RUN_DATE), "FIXED")

        assert _project_headers(md) == [PROJECT_AA, PROJECT_MAIN, PROJECT_ZZ]
        assert _project_headers(md) == sorted(_project_headers(md))


# ══════════════════════════════════════════════════════════════════════════
# (iv) agent lines in agent_id ASC order
# ══════════════════════════════════════════════════════════════════════════


class TestAgentLineOrdering:
    def test_agent_lines_are_agent_id_asc(self, digest_engine, hermetic_artifacts):
        """(iv) the per-agent breakdown lists ``agent_id`` ASC."""
        _seed(digest_engine, _determinism_fixture_rows())
        md = tud.render_markdown(tud.rollup(digest_engine, RUN_DATE), "FIXED")

        lines = _agent_lines(md, "tie_a")
        assert [line.split(":")[0][2:] for line in lines] == ["a-agent", "z-agent"]

    def test_multi_agent_breakdown_is_sorted_not_insertion_ordered(
        self, digest_engine, hermetic_artifacts
    ):
        """The same pin with three agents (insertion order z, a, m)."""
        rows = [
            _row(DAY_D1, PROJECT_MAIN, "multi", "z-agent", 1),
            _row(DAY_D1, PROJECT_MAIN, "multi", "a-agent", 1),
            _row(DAY_D1, PROJECT_MAIN, "multi", "m-agent", 1),
        ]
        _seed(digest_engine, rows)
        md = tud.render_markdown(tud.rollup(digest_engine, RUN_DATE), "FIXED")

        lines = _agent_lines(md, "multi")
        assert [line.split(":")[0][2:] for line in lines] == [
            "a-agent",
            "m-agent",
            "z-agent",
        ]


# ══════════════════════════════════════════════════════════════════════════
# (v) the score basis is calls_7d
# ══════════════════════════════════════════════════════════════════════════


class TestScoreBasis:
    def test_score_is_weight_times_calls_7d_not_calls_24h(
        self, digest_engine, hermetic_artifacts
    ):
        """(v) ``week_only`` has 0 calls in the 24h window and 5 in the
        week: a 24h-based score would render 0 and sort LAST."""
        _seed(digest_engine, _determinism_fixture_rows())
        md = tud.render_markdown(tud.rollup(digest_engine, RUN_DATE), "FIXED")

        cells = _table_row(md, "week_only")
        weight, calls_24h, calls_7d, score = (
            int(cells[1]),
            int(cells[2]),
            int(cells[3]),
            int(cells[4]),
        )
        assert (weight, calls_24h, calls_7d, score) == (230, 0, 5, 1150)
        assert score == weight * calls_7d
        assert score != weight * calls_24h

    def test_calls_24h_counts_only_the_d_minus_1_day(self, digest_engine, hermetic_artifacts):
        """The 24h column is the COMPLETE previous UTC day (D-1); the D-7
        boundary row contributes to the week column only."""
        _seed(digest_engine, _determinism_fixture_rows())
        md = tud.render_markdown(tud.rollup(digest_engine, RUN_DATE), "FIXED")

        assert int(_table_row(md, "week_only")[2]) == 0   # 24h
        assert int(_table_row(md, "week_only")[3]) == 5   # 7d (D-7 boundary)
        assert int(_table_row(md, "tie_b")[2]) == 2       # D-1
        assert int(_table_row(md, "tie_b")[3]) == 2       # D-1


# ══════════════════════════════════════════════════════════════════════════
# weight join + the sanctioned 230 fallback (T2 / 9(a))
# ══════════════════════════════════════════════════════════════════════════


class TestWeightJoinAndFallback:
    def test_a_mapped_weight_is_joined_and_absent_tools_fall_back(
        self, digest_engine, monkeypatch
    ):
        """T2 join: a tool present in the weight artifact uses ITS weight;
        a tool absent uses the documented 230 fallback."""
        monkeypatch.setattr(tud, "tool_weights", lambda: {"w_heavy": 50})
        monkeypatch.setattr(tud, "allowlists", lambda: {})
        _seed(
            digest_engine,
            [
                _row(DAY_D1, PROJECT_MAIN, "w_heavy", "a", 10),  # 50 * 10  = 500
                _row(DAY_D1, PROJECT_MAIN, "w_absent", "a", 3),  # 230 * 3  = 690
            ],
        )
        md = tud.render_markdown(tud.rollup(digest_engine, RUN_DATE), "FIXED")

        assert int(_table_row(md, "w_heavy")[1]) == 50
        assert int(_table_row(md, "w_absent")[1]) == tud.FALLBACK_WEIGHT == 230
        # 690 > 500 ⇒ the fallback-weighted tool ranks first.
        assert _section_tools(md, PROJECT_MAIN) == ["w_absent", "w_heavy"]

    def test_weight_lookup_falls_back_for_an_absent_name(self, hermetic_artifacts):
        """The fallback constant is the sanctioned state (9(a)): an
        artifact-absent name resolves to 230, deterministically."""
        assert tud._weight_for("t7_definitely_not_in_the_artifact") == 230
        assert tud.FALLBACK_WEIGHT == 230

    def test_weight_lookup_accepts_a_mapped_name(self, monkeypatch):
        monkeypatch.setattr(tud, "tool_weights", lambda: {"mapped": 512})
        assert tud._weight_for("mapped") == 512
        assert tud._weight_for("unmapped") == tud.FALLBACK_WEIGHT

    def test_an_empty_artifact_reads_as_an_empty_mapping(self, monkeypatch):
        """The sanctioned ``{}`` artifact decodes to ``{}`` (never raises),
        so every tool lands on the 230 fallback."""
        monkeypatch.setattr(tud, "_load_json_artifact", lambda _name: {})
        assert tud.tool_weights() == {}
        assert tud._weight_for("anything") == 230

    def test_a_non_dict_artifact_reads_as_an_empty_mapping(self, monkeypatch):
        """Defensive decode: a list/string artifact is not a weight map."""
        monkeypatch.setattr(tud, "_load_json_artifact", lambda _name: ["nope"])
        assert tud.tool_weights() == {}


# ══════════════════════════════════════════════════════════════════════════
# rollup content shape (S2) — zero/near-zero + allow-list markers
# ══════════════════════════════════════════════════════════════════════════


class TestRollupContentShape:
    def test_zero_near_zero_lists_allow_listed_tools_at_or_below_the_threshold(
        self, digest_engine, monkeypatch
    ):
        """Zero AND near-zero share one section: the predicate is
        ``calls_7d <= NEAR_ZERO_CALLS_7D`` (3), so a called-but-barely
        tool qualifies while a genuinely used one does not."""
        monkeypatch.setattr(tud, "tool_weights", lambda: {})
        monkeypatch.setattr(
            tud,
            "allowlists",
            lambda: {"ari": ["tool_usage_digest", "barely", "well_used"]},
        )
        _seed(
            digest_engine,
            [
                _row(DAY_D1, PROJECT_MAIN, "barely", "ari", 2),
                _row(DAY_D1, PROJECT_MAIN, "well_used", "ari", 5),
            ],
        )

        data = tud.rollup(digest_engine, RUN_DATE)
        zero = {entry["tool_name"]: entry for entry in data["zero_near_zero"]}

        assert tud.NEAR_ZERO_CALLS_7D == 3
        # Allow-listed but never called → the strongest layer-2 signal.
        assert "tool_usage_digest" in zero
        assert zero["tool_usage_digest"]["calls_7d"] == 0
        assert zero["tool_usage_digest"]["allowed_agents"] == ["ari"]
        # 2 calls is still <= 3 → near-zero.
        assert zero["barely"]["calls_7d"] == 2
        # 5 calls clears the threshold → not a candidacy row.
        assert "well_used" not in zero

    def test_allow_list_marker_renders_per_agent(
        self, digest_engine, monkeypatch
    ):
        monkeypatch.setattr(tud, "tool_weights", lambda: {})
        monkeypatch.setattr(tud, "allowlists", lambda: {"ari": ["tie_a"]})
        _seed(digest_engine, [_row(DAY_D1, PROJECT_MAIN, "tie_a", "ari", 2)])

        data = tud.rollup(digest_engine, RUN_DATE)
        tool = data["projects"][0]["tools"][0]
        assert tool["allow_listed_any"] is True
        assert tool["allowed_agents"] == ["ari"]
        assert tool["agents"][0]["allow_listed"] is True

        md = tud.render_markdown(data, "FIXED")
        assert "(allow-listed)" in _agent_lines(md, "tie_a")[0]

    def test_window_metadata_is_frozen_D_minus_1_and_D_minus_7_to_D_minus_1(
        self, digest_engine, hermetic_artifacts
    ):
        _seed(digest_engine, [])
        data = tud.rollup(digest_engine, RUN_DATE)

        assert data["run_date"] == RUN_DATE
        assert data["last24_day"] == DAY_D1
        assert data["week_days"] == [
            "2026-11-14",  # D-7
            "2026-11-15",
            "2026-11-16",
            "2026-11-17",
            "2026-11-18",
            "2026-11-19",
            "2026-11-20",  # D-1
        ]

    def test_digest_path_is_root_slash_date_md(self, tmp_path, monkeypatch):
        monkeypatch.setenv(tud.DIGEST_ROOT_ENV, str(tmp_path / "root"))
        path = tud.digest_path(RUN_DATE)
        assert path == tmp_path / "root" / f"{RUN_DATE}.md"
        assert path.parent.is_dir()


# ══════════════════════════════════════════════════════════════════════════
# G5 pass criterion — the rollup body reads no clock / random / live registry
# ══════════════════════════════════════════════════════════════════════════


class TestRollupBodyHasNoNondeterministicSource:
    """The determinism invariant (§3.4) is structural, not incidental: the
    rollup body must contain no wall-clock, no RNG and no live-registry
    read. A source-level pin is what makes the byte-identical assertions
    above non-accidental — a future edit that consults ``datetime.now()``
    inside the rollup reds HERE even if the fixture happens to be stable."""

    def _rollup_source(self) -> str:
        import inspect

        return inspect.getsource(tud.rollup)

    def test_no_clock_read_inside_the_rollup(self):
        source = self._rollup_source()
        assert "datetime.now" not in source
        assert "time.time" not in source
        assert ".now(" not in source

    def test_no_rng_inside_the_rollup(self):
        source = self._rollup_source()
        assert "random" not in source
        assert "uuid" not in source

    def test_no_live_env_or_registry_read_inside_the_rollup(self):
        """Determinism requires the artifact readers be the ONLY external
        inputs — an ``os.environ`` read or a live ``agents/`` scan would
        let ambient state leak into the digest."""
        source = self._rollup_source()
        assert "os.environ" not in source
        assert "getenv" not in source
        assert "agents/" not in source
        assert "tool_weights()" in source and "allowlists()" in source

    def test_the_defaulted_header_is_the_only_wall_clock_surface(self):
        """``render_markdown`` takes the stamp INJECTABLE and defaults it
        to ``datetime.now`` — that isolation is what the determinism gate
        relies on."""
        import inspect

        source = inspect.getsource(tud.render_markdown)
        assert "generated_at" in source
        assert "datetime.now" in source


# ══════════════════════════════════════════════════════════════════════════
# R-EXPANSION — the reader consumes registry keys, never display names
# ══════════════════════════════════════════════════════════════════════════


def test_the_digest_never_consumes_the_display_name_category_function():
    """Node 38 (§3.9.2): a SOURCE pin plus a DATA pin.

    The digest must never call the display-name category helper or import
    the display-name constant: the artifact's category keys are REGISTRY
    keys, and a display name ("File Operations", "Shell", ...) is not a
    category the runtime can grant.
    """
    source = Path(tud.__file__).read_text(encoding="utf-8")
    assert "get_tool_categories" not in source
    assert "CATEGORY_NAME" not in source

    artifact = (
        Path(tud.__file__).resolve().parents[2]
        / ".agents"
        / "shared"
        / "planning"
        / "tool-usage-metrics-p1"
        / "allowlists.json"
    )
    categories = json.loads(artifact.read_text(encoding="utf-8"))["categories"]
    assert categories, "vacuity guard: the artifact must carry a category map"
    assert set(categories) <= set(CATEGORY_MODULES)
    for display_name in ("File Operations", "Nest Report", "Shell", "MCP"):
        assert display_name not in categories
