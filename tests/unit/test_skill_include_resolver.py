"""Unit tests for :mod:`daemon.services.skill_include_resolver`.

Exercises every resolution path and guard:

* No-frontmatter / no-include passthrough (backwards compat).
* Innate-skill include source (primary resolution path).
* ``skill_bank`` include source (fallback when innate is missing).
* Cross-agent ``skill_bank`` fallback (``get_by_name_any_agent``).
* Missing include (graceful degradation + warning).
* Cycle detection (A → B → A).
* Depth cap (chain longer than ``max_depth`` is truncated).
* Nested includes (A → B → C, all resolved).
* Sibling includes (A → B and A → C, both resolved independently).
* Frontmatter stripped from rendered body when ``include:`` present.
* Frontmatter preserved when no ``include:`` (backwards compat).
* ``include:`` value coercion (str, list, null, invalid types).
* Non-string include entries dropped with a warning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.skill.skill_bank_repository import SkillBankRepository
from daemon.services.skill_include_resolver import resolve_includes


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def engine() -> Iterator[Engine]:
    """In-memory SQLite engine with the ``skill_bank`` table created."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def bank_repo(engine: Engine) -> SkillBankRepository:
    """``SkillBankRepository`` wired to the in-memory engine."""
    return SkillBankRepository(engine)


def _make_agents_dir(tmp_path: Path) -> Path:
    """Create an ``agents/`` directory with the ``_prompt_system/innate-skills/`` subtree.

    Returns the ``agents/`` root — callers add per-agent dirs and
    innate-skill entries underneath.
    """
    agents = tmp_path / "agents"
    (agents / "_prompt_system" / "innate-skills").mkdir(parents=True)
    return agents


def _write_innate_skill(agents_dir: Path, name: str, body: str) -> Path:
    """Write an innate skill at ``agents/_prompt_system/innate-skills/{name}/skill.md``."""
    skill_dir = agents_dir / "_prompt_system" / "innate-skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "skill.md"
    skill_md.write_text(body, encoding="utf-8")
    return skill_md


# ============================================================================
# Passthrough — no include directive
# ============================================================================


class TestPassthrough:
    """No ``include:`` directive → content returned unchanged."""

    def test_no_frontmatter_returns_verbatim(self, tmp_path: Path) -> None:
        """Body without any frontmatter passes through untouched."""
        agents_dir = _make_agents_dir(tmp_path)
        body = "# Plain Skill\n\nNo frontmatter at all.\n"
        assert resolve_includes(body, "a", agents_dir, None) == body

    def test_frontmatter_without_include_returns_verbatim(
        self, tmp_path: Path
    ) -> None:
        """Frontmatter that doesn't declare ``include:`` is preserved
        (backwards compat — existing skills may rely on their
        frontmatter being part of the stored body)."""
        agents_dir = _make_agents_dir(tmp_path)
        content = (
            "---\n"
            "version: 1.0.0\n"
            "category: workflow\n"
            "---\n"
            "# Plain Skill\n\nBody.\n"
        )
        assert resolve_includes(content, "a", agents_dir, None) == content

    def test_include_empty_list_returns_verbatim(
        self, tmp_path: Path
    ) -> None:
        """``include: []`` is a no-op (treated as no directive)."""
        agents_dir = _make_agents_dir(tmp_path)
        content = (
            "---\n"
            "version: 1.0.0\n"
            "include: []\n"
            "---\n"
            "# Skill\n\nBody.\n"
        )
        # Empty list → no includes → frontmatter preserved.
        assert resolve_includes(content, "a", agents_dir, None) == content

    def test_include_null_returns_verbatim(self, tmp_path: Path) -> None:
        """``include: null`` (or absent) is a no-op."""
        agents_dir = _make_agents_dir(tmp_path)
        content = (
            "---\n"
            "version: 1.0.0\n"
            "include: null\n"
            "---\n"
            "# Skill\n\nBody.\n"
        )
        assert resolve_includes(content, "a", agents_dir, None) == content


# ============================================================================
# Innate-skill include source
# ============================================================================


class TestInnateInclude:
    """``include:`` resolves from innate-skills dir first."""

    def test_innate_include_appended_after_separator(
        self, tmp_path: Path
    ) -> None:
        """Includer body + separator + innate body, in that order."""
        agents_dir = _make_agents_dir(tmp_path)
        _write_innate_skill(
            agents_dir,
            "test-pack",
            "# Test Pack\n\n5-min cap. Dual-layer timeout.\n",
        )
        includer = (
            "---\n"
            "version: 1.0.0\n"
            "include: [test-pack]\n"
            "---\n"
            "# Unit Test\n\nDiscover tests.\n"
        )

        rendered = resolve_includes(includer, "tester", agents_dir, None)

        # Frontmatter stripped (the directive has no runtime value).
        assert not rendered.startswith("---")
        # Includer body intact.
        assert "# Unit Test" in rendered
        assert "Discover tests." in rendered
        # Separator + header for the included skill.
        assert "## Included: test-pack" in rendered
        # Included body present.
        assert "# Test Pack" in rendered
        assert "5-min cap. Dual-layer timeout." in rendered
        # Ordering: includer body BEFORE the included block.
        assert rendered.index("# Unit Test") < rendered.index(
            "## Included: test-pack"
        )
        assert rendered.index("## Included: test-pack") < rendered.index(
            "# Test Pack"
        )

    def test_bare_string_include_coerced_to_list(
        self, tmp_path: Path
    ) -> None:
        """``include: test-pack`` (bare string) is equivalent to ``[test-pack]``."""
        agents_dir = _make_agents_dir(tmp_path)
        _write_innate_skill(
            agents_dir, "shared", "# Shared\n\nShared body.\n"
        )
        includer = (
            "---\n"
            "version: 1.0.0\n"
            "include: shared\n"  # bare string, NOT a list
            "---\n"
            "# Main\n\nMain body.\n"
        )

        rendered = resolve_includes(includer, "tester", agents_dir, None)

        assert "## Included: shared" in rendered
        assert "Shared body." in rendered

    def test_multiple_includes_each_get_own_separator(
        self, tmp_path: Path
    ) -> None:
        """Two includes produce two separate ``## Included:`` blocks."""
        agents_dir = _make_agents_dir(tmp_path)
        _write_innate_skill(agents_dir, "alpha", "# Alpha\n")
        _write_innate_skill(agents_dir, "beta", "# Beta\n")
        includer = (
            "---\n"
            "version: 1.0.0\n"
            "include: [alpha, beta]\n"
            "---\n"
            "# Main\n"
        )

        rendered = resolve_includes(includer, "tester", agents_dir, None)

        assert rendered.count("## Included:") == 2
        assert "## Included: alpha" in rendered
        assert "## Included: beta" in rendered
        # Order matches the include list.
        assert rendered.index("## Included: alpha") < rendered.index(
            "## Included: beta"
        )


# ============================================================================
# skill_bank include source
# ============================================================================


class TestBankInclude:
    """``include:`` falls back to ``skill_bank`` when innate is missing."""

    def test_bank_include_when_innate_missing(
        self, tmp_path: Path, bank_repo: SkillBankRepository
    ) -> None:
        """No innate skill by that name → fall back to skill_bank."""
        agents_dir = _make_agents_dir(tmp_path)
        # Seed the bank with a shared skill owned by the same agent.
        bank_repo.create(
            name="shared-evolvable",
            content="# Shared Evolvable\n\nFrom the bank.\n",
            agent_id="tester",
        )
        includer = (
            "---\n"
            "version: 1.0.0\n"
            "include: [shared-evolvable]\n"
            "---\n"
            "# Main\n"
        )

        rendered = resolve_includes(
            includer, "tester", agents_dir, bank_repo
        )

        assert "## Included: shared-evolvable" in rendered
        assert "From the bank." in rendered

    def test_bank_cross_agent_fallback(
        self, tmp_path: Path, bank_repo: SkillBankRepository
    ) -> None:
        """Include name owned by a DIFFERENT agent resolves via the
        cross-agent fallback (``get_by_name_any_agent``)."""
        agents_dir = _make_agents_dir(tmp_path)
        bank_repo.create(
            name="tester-only-shared",
            content="# Tester Shared\n\nOwned by tester.\n",
            agent_id="tester",
        )
        includer = (
            "---\n"
            "version: 1.0.0\n"
            "include: [tester-only-shared]\n"
            "---\n"
            "# Main\n"
        )

        # Resolver called with agent_id='worker' — no exact match
        # in the bank, but the cross-agent fallback should find it.
        rendered = resolve_includes(
            includer, "worker", agents_dir, bank_repo
        )

        assert "## Included: tester-only-shared" in rendered
        assert "Owned by tester." in rendered

    def test_innate_wins_over_bank(
        self, tmp_path: Path, bank_repo: SkillBankRepository
    ) -> None:
        """When both innate and bank have an entry, innate wins.

        Innate skills are framework-wide invariants and should never
        be silently shadowed by an evolvable variant with the same
        name.
        """
        agents_dir = _make_agents_dir(tmp_path)
        _write_innate_skill(
            agents_dir, "conflict", "# Innate Conflict\n\nInnate body.\n"
        )
        bank_repo.create(
            name="conflict",
            content="# Bank Conflict\n\nBank body.\n",
            agent_id="tester",
        )
        includer = (
            "---\n"
            "version: 1.0.0\n"
            "include: [conflict]\n"
            "---\n"
            "# Main\n"
        )

        rendered = resolve_includes(
            includer, "tester", agents_dir, bank_repo
        )

        assert "Innate body." in rendered
        assert "Bank body." not in rendered


# ============================================================================
# Missing / invalid includes
# ============================================================================


class TestMissingInclude:
    """Graceful degradation when an include can't be resolved."""

    def test_missing_include_warns_and_skips(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No innate, no bank entry → log warning + render body
        without the include (skill still gets seeded)."""
        agents_dir = _make_agents_dir(tmp_path)
        includer = (
            "---\n"
            "version: 1.0.0\n"
            "include: [nonexistent]\n"
            "---\n"
            "# Main\n\nMain body.\n"
        )

        with caplog.at_level("WARNING"):
            rendered = resolve_includes(includer, "tester", agents_dir, None)

        # Body rendered without the include.
        assert "# Main" in rendered
        assert "Main body." in rendered
        assert "## Included:" not in rendered
        # Frontmatter still stripped (the directive was present).
        assert not rendered.startswith("---")
        # Warning logged.
        assert any(
            "nonexistent" in rec.getMessage() and "not found" in rec.getMessage()
            for rec in caplog.records
        )

    def test_partial_missing_keeps_resolved_includes(
        self, tmp_path: Path
    ) -> None:
        """One missing include doesn't poison the others — resolved
        ones still land in the rendered body."""
        agents_dir = _make_agents_dir(tmp_path)
        _write_innate_skill(agents_dir, "real", "# Real\n")
        includer = (
            "---\n"
            "version: 1.0.0\n"
            "include: [real, ghost]\n"
            "---\n"
            "# Main\n"
        )

        rendered = resolve_includes(includer, "tester", agents_dir, None)

        assert "## Included: real" in rendered
        assert "# Real" in rendered
        # ghost was missing — no block for it.
        assert "## Included: ghost" not in rendered

    def test_non_string_include_entry_dropped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Non-string entries in ``include:`` are dropped with a warning."""
        agents_dir = _make_agents_dir(tmp_path)
        _write_innate_skill(agents_dir, "real", "# Real\n")
        includer = (
            "---\n"
            "version: 1.0.0\n"
            "include: [real, 123, true]\n"  # 123 and true are invalid
            "---\n"
            "# Main\n"
        )

        with caplog.at_level("WARNING"):
            rendered = resolve_includes(includer, "tester", agents_dir, None)

        # Only 'real' is resolved; 123 and true are dropped.
        assert "## Included: real" in rendered
        assert rendered.count("## Included:") == 1
        assert any(
            "non-string" in rec.getMessage() for rec in caplog.records
        )


# ============================================================================
# Cycle detection
# ============================================================================


class TestCycleDetection:
    """Cycles in the include graph are broken, not infinitely recursed."""

    def test_direct_self_cycle_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A → A (skill includes itself) — the self-reference is skipped."""
        agents_dir = _make_agents_dir(tmp_path)
        # Simulate the cycle by writing the includer body to an
        # innate skill of the same name.
        _write_innate_skill(
            agents_dir,
            "loopy",
            "---\ninclude: [loopy]\n---\n# Loopy\n\nSelf-reference.\n",
        )
        includer = (
            "---\n"
            "version: 1.0.0\n"
            "include: [loopy]\n"
            "---\n"
            "# Main\n"
        )

        with caplog.at_level("WARNING"):
            rendered = resolve_includes(includer, "tester", agents_dir, None)

        # The include resolves once (depth 1) — the inner
        # self-reference is detected as a cycle and skipped.
        assert "## Included: loopy" in rendered
        assert "Self-reference." in rendered
        # Exactly ONE include block, not infinite recursion.
        assert rendered.count("## Included: loopy") == 1
        assert any(
            "cycle" in rec.getMessage().lower() for rec in caplog.records
        )

    def test_three_node_cycle_broken(
        self, tmp_path: Path
    ) -> None:
        """A → B → C → A — the back-edge to A is skipped."""
        agents_dir = _make_agents_dir(tmp_path)
        _write_innate_skill(
            agents_dir, "b-node",
            "---\ninclude: [c-node]\n---\n# B\n",
        )
        _write_innate_skill(
            agents_dir, "c-node",
            "---\ninclude: [a-node]\n---\n# C\n",
        )
        # 'a-node' innate skill — simulates the cycle closure.
        _write_innate_skill(
            agents_dir, "a-node",
            "---\ninclude: [b-node]\n---\n# A\n",
        )
        # Includer references 'b-node' (which transitively cycles
        # back to 'a-node' → 'b-node' → ...).
        includer = (
            "---\n"
            "include: [a-node]\n"
            "---\n"
            "# Main\n"
        )

        rendered = resolve_includes(includer, "tester", agents_dir, None)

        # 'a-node' resolved once.
        assert "## Included: a-node" in rendered
        # 'b-node' resolved (a-node → b-node).
        assert "## Included: b-node" in rendered
        # 'c-node' resolved (b-node → c-node).
        assert "## Included: c-node" in rendered
        # Cycle broken: a-node is NOT resolved a second time
        # (c-node → a-node would re-enter, but a-node is already
        # in visited).
        assert rendered.count("## Included: a-node") == 1
        assert rendered.count("## Included: b-node") == 1
        assert rendered.count("## Included: c-node") == 1


# ============================================================================
# Depth cap
# ============================================================================


class TestDepthCap:
    """The ``max_depth`` parameter bounds include chain length."""

    def test_default_depth_cap_is_three(self, tmp_path: Path) -> None:
        """Default ``max_depth=3`` resolves 3 levels then stops."""
        agents_dir = _make_agents_dir(tmp_path)
        # Build a 5-deep chain: includer → d1 → d2 → d3 → d4 → d5.
        for level in range(1, 6):
            next_level = level + 1
            body = (
                f"---\ninclude: [d{next_level}]\n---\n# D{level}\n"
                if level < 5
                else f"# D{level}\n"
            )
            _write_innate_skill(agents_dir, f"d{level}", body)
        includer = (
            "---\n"
            "include: [d1]\n"
            "---\n"
            "# Main\n"
        )

        rendered = resolve_includes(includer, "tester", agents_dir, None)

        # Default cap=3: Main(0) → d1(1) → d2(2) → d3(3).
        # d3's include of d4 would be depth 4 → skipped.
        assert "## Included: d1" in rendered
        assert "## Included: d2" in rendered
        assert "## Included: d3" in rendered
        # d4 and d5 are beyond the cap.
        assert "## Included: d4" not in rendered
        assert "## Included: d5" not in rendered

    def test_explicit_max_depth_one(self, tmp_path: Path) -> None:
        """``max_depth=1`` resolves direct includes only — no nesting."""
        agents_dir = _make_agents_dir(tmp_path)
        _write_innate_skill(
            agents_dir, "outer",
            "---\ninclude: [inner]\n---\n# Outer\n",
        )
        _write_innate_skill(agents_dir, "inner", "# Inner\n")
        includer = (
            "---\n"
            "include: [outer]\n"
            "---\n"
            "# Main\n"
        )

        rendered = resolve_includes(
            includer, "tester", agents_dir, None, max_depth=1
        )

        # outer is resolved (depth 1).
        assert "## Included: outer" in rendered
        # outer's include of 'inner' would be depth 2 → skipped.
        assert "## Included: inner" not in rendered


# ============================================================================
# Sibling includes
# ============================================================================


class TestSiblingIncludes:
    """Two includes at the same level both resolve — visited is popped
    after each recursion so siblings don't false-trigger cycles."""

    def test_two_siblings_both_including_same_third(
        self, tmp_path: Path
    ) -> None:
        """A → B, A → C, B → D, C → D — D resolves twice (once under
        B, once under C). The visited-set is per-branch, not global."""
        agents_dir = _make_agents_dir(tmp_path)
        _write_innate_skill(
            agents_dir, "b-sib",
            "---\ninclude: [d-shared]\n---\n# B\n",
        )
        _write_innate_skill(
            agents_dir, "c-sib",
            "---\ninclude: [d-shared]\n---\n# C\n",
        )
        _write_innate_skill(agents_dir, "d-shared", "# D Shared\n")
        includer = (
            "---\n"
            "include: [b-sib, c-sib]\n"
            "---\n"
            "# Main\n"
        )

        rendered = resolve_includes(includer, "tester", agents_dir, None)

        # b-sib and c-sib both resolved.
        assert "## Included: b-sib" in rendered
        assert "## Included: c-sib" in rendered
        # d-shared resolved TWICE — once under b-sib, once under c-sib.
        # The visited-set is per-branch (popped after each recursion)
        # so the second resolution under c-sib is NOT treated as a cycle.
        assert rendered.count("## Included: d-shared") == 2


# ============================================================================
# Nested includes (multi-level happy path)
# ============================================================================


class TestNestedIncludes:
    """Multi-level include chains resolve all the way down (within depth cap)."""

    def test_three_level_chain_all_resolved(
        self, tmp_path: Path
    ) -> None:
        """A → B → C — all three bodies land in the rendered output."""
        agents_dir = _make_agents_dir(tmp_path)
        _write_innate_skill(
            agents_dir, "level-b",
            "---\ninclude: [level-c]\n---\n# Level B\n\nB body.\n",
        )
        _write_innate_skill(agents_dir, "level-c", "# Level C\n\nC body.\n")
        includer = (
            "---\n"
            "version: 1.0.0\n"
            "include: [level-b]\n"
            "---\n"
            "# Level A\n\nA body.\n"
        )

        rendered = resolve_includes(includer, "tester", agents_dir, None)

        # All three bodies present.
        assert "A body." in rendered
        assert "B body." in rendered
        assert "C body." in rendered
        # Both include separators present.
        assert "## Included: level-b" in rendered
        assert "## Included: level-c" in rendered
        # Ordering: A → level-b → level-c.
        assert rendered.index("A body.") < rendered.index(
            "## Included: level-b"
        )
        assert rendered.index("## Included: level-b") < rendered.index(
            "B body."
        )
        assert rendered.index("B body.") < rendered.index(
            "## Included: level-c"
        )
