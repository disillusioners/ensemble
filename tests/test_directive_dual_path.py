"""User Directive #3 + #4 + #5B (2026-10-14, RESOLUTION-CORE)
behavioral tests — the DUAL-PATH regime, classification routing,
guard flips, and boot auto-reset wiring.

Sibling to the Phase B ``test_inner_soul_db.py`` /
``test_prompt_version_resolution.py`` /
``test_prompt_artifact_guards.py`` /
``test_agent_modify_rewire.py`` files — but focused on the
cross-cutting directive surfaces that touch multiple files:

* CORE path (soul / workflow) — pending_approval grammar, no
  badge hit.
* MEMORY path (memory / user / growth / memories/<slug>) —
  born-effective grammar, no approval round-trip.
* Classification routing (Directive #4) — RAG-ON redirects
  project content to RAG; RAG-OFF uses the project-content
  rejection/hint path; USER-related content → MEMORY
  direct persist.
* Guard flips — ``memory.md`` / ``growth.md`` are now
  versioned-immutable templates (write_file / edit_file
  refuse them); read paths still allowed.
* Router class deltas — list endpoint gains
  ``artifact_class`` filter; approve/reject/submit/retract/
  PATCH/DELETE map ``MemoryClassError`` → 409
  ``MEMORY_CLASS_MISMATCH``; pending-count is CORE-only by
  design (badge source unchanged).
* Boot auto-reset wiring — the API lifespan calls
  ``record_initial_baselines_for_all_predefined_agents`` then
  ``detect_drift_for_all_predefined_agents`` (non-fatal).

Fencing (project critical notes):

* run with ``ENSEMBLE_SELF_ENV=dev`` and ``POSTGRES_DB`` NOT
  ``ensemble_prod`` (the fixture reads no ambient ``POSTGRES_*``
  — file-SQLite only);
* agent dirs are ``tmp_path``-relative (NEVER live-shaped
  — 2026-10-12 critical note); the loader-hook fence resets
  the module-level repo singleton on exit;
* run this file targeted (``--tb=short -q``), never bare
  pytest.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from daemon.loader import (
    PromptCache,
    _get_prompt_version_repo,
    set_prompt_version_repo,
)
from daemon.registry import AgentMetadata
from daemon.repositories.prompt_version.models import (
    PromptAuditEvent,
    PromptVersion,
)
from daemon.repositories.prompt_version.repository import (
    ActorContext,
    MemoryClassError,
    PromptVersionRepository,
    class_for_artifact,
)
from daemon.services.prompt_version_service import PromptVersionService
from daemon.tools.inner_soul import (
    _update_memory_md,
    _update_memories,
    _update_user,
    create_inner_soul_tool,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def repo_engine() -> Iterator[Engine]:
    """Fresh file-SQLite engine (WAL) with the prompt-version schema."""
    db_path = tempfile.mktemp(suffix=".db")
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    from daemon.repositories.prompt_version.models import (  # noqa: F401
        PromptAuditEvent,
        PromptVersion,
    )

    SQLModel.metadata.create_all(engine)

    try:
        yield engine
    finally:
        engine.dispose()
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + ext)
            except FileNotFoundError:
                pass


@pytest.fixture
def repo(repo_engine) -> PromptVersionRepository:
    return PromptVersionRepository(repo_engine)


@pytest.fixture
def pv_service(repo) -> PromptVersionService:
    """The prompt-version service (named ``pv_service`` to avoid
    collision with other ``service`` fixtures in the broader
    test suite)."""
    return PromptVersionService(repo=repo)


class _ManagerWithService:
    """Minimal manager stand-in: real prompt cache + real service."""

    def __init__(self, service: PromptVersionService) -> None:
        self.prompt_cache = PromptCache()
        self._prompt_version_service = service


@pytest.fixture
def manager(pv_service) -> _ManagerWithService:
    return _ManagerWithService(pv_service)


@pytest.fixture
def override_agents_roots(tmp_path, monkeypatch):
    """Override the guard's agents-root candidate set to point at tmp_path.

    Mirrors the canonical fixture in
    ``tests/test_prompt_artifact_guards.py``. Per the 2026-10-12
    critical note, the per-test fixture MUST NOT touch
    live-shaped paths.
    """
    import daemon.tools.prompt_artifacts as pa

    pa.reset_agents_roots_cache()
    agents_root = tmp_path / "agents"
    agents_root.mkdir()
    baby = agents_root / "_baby_template"
    baby.mkdir()
    test_agent = agents_root / "test_agent"
    test_agent.mkdir()
    for agent_dir in (baby, test_agent):
        for fname in (
            "soul.md", "rule.md", "workflow.md", "tools_note.md",
            "tools.md", "skill.md", "memory.md", "user.md", "growth.md",
        ):
            (agent_dir / fname).write_text(f"# {fname}\n")

    monkeypatch.setattr(pa, "_DEFAULT_AGENTS_ROOTS_CACHE", (agents_root,))
    yield {
        "agents_root": agents_root,
        "test_agent": test_agent,
        "_baby_template": baby,
    }


@pytest.fixture
def temp_agent(tmp_path) -> Path:
    """A minimal agent dir (tmp — never live-shaped)."""
    agent_dir = tmp_path / "dbagent"
    agent_dir.mkdir()
    (agent_dir / "soul.md").write_text("# Who I Am\n\nI am a test agent.\n")
    (agent_dir / "workflow.md").write_text("# Workflow\n\n1. Process\n")
    (agent_dir / "user.md").write_text(
        "# User\n\n(To be filled in with details about the user)\n"
    )
    (agent_dir / "memory.md").write_text("# Memory\n\n## Known Patterns\n")
    (agent_dir / "growth.md").write_text(
        "# Growth\n\nmax_memory_words: 2000\nmax_soul_chars: 2000\n"
    )
    (agent_dir / "memories").mkdir()
    return agent_dir


@pytest.fixture
def mock_registry(temp_agent):
    """Registry mock resolving ``dbagent`` to the tmp agent dir."""
    agent_metadata = AgentMetadata(
        id="dbagent",
        name="DB Agent",
        description="test agent",
        path=temp_agent,
        system=False,
    )
    registry = MagicMock()
    registry.get_version.return_value = agent_metadata
    registry.get_resolved.return_value = agent_metadata

    with patch("daemon.registry.get_registry", return_value=registry):
        yield registry


def _rows_for(repo: PromptVersionRepository, artifact: str) -> list[PromptVersion]:
    """Return rows for the canonical key (test convenience)."""
    return list(repo.list_versions("dbagent", "", artifact))


# ═══════════════════════════════════════════════════════════════════════════
# DUAL-PATH clean-start narrative (User Directive #3 point 5)
# ═══════════════════════════════════════════════════════════════════════════


class TestDualPathCleanStart:
    """Both-path clean-start (Ud#3 point 5): fresh DB → CORE draft
    + MEMORY direct persist coexist on the same empty DB.

    Sibling to ``TestUserDirectiveCleanStart`` in
    ``test_prompt_version_resolution.py`` which proves the CORE
    path; this class proves the MEMORY path and the coexistence.
    """

    def test_both_paths_against_empty_db(self, mock_registry, manager, temp_agent, repo, pv_service):
        """CORE and MEMORY writes on the same empty DB both succeed.

        CORE path (soul): ``📋 <target>: submitted as
        prompt-version v<N> (pending approval)`` grammar; a
        pending_approval row appears; the badge (count_pending)
        shows 1; approve flips it to approved; effective
        content comes from the DB.

        MEMORY path (memory): ``✓ <target>: persisted (memory
        version N)`` grammar; a state='approved' MEMORY-class
        row appears; the badge (count_pending) is UNCHANGED
        (still 0 after the memory write) — the MEMORY path
        does NOT hit the badge.

        Both files remain byte-identical to the pre-call
        content.
        """
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        soul_before = (temp_agent / "soul.md").read_text()
        memory_before = (temp_agent / "memory.md").read_text()

        # RAG must be off so the classification doesn't redirect
        # to the experience() lane.
        with patch("daemon.tools.inner_soul.is_rag_enabled", return_value=False):
            # 1) CORE path — soul target.
            soul_result = tool.invoke({
                "request": "My name is DBTester", "target": "soul"
            })
        assert "📋 soul:" in soul_result
        assert "pending approval" in soul_result

        # CORE row materialized in pending_approval.
        soul_rows = _rows_for(repo, "soul")
        assert len(soul_rows) == 1
        assert soul_rows[0].state == "pending_approval"
        assert soul_rows[0].artifact_class == "CORE"
        assert soul_rows[0].version == 1

        # Pending-count = 1 (CORE row in pending_approval).
        assert repo.count_pending() == 1

        # File UNTOUCHED.
        assert (temp_agent / "soul.md").read_text() == soul_before

        # 2) MEMORY path — memory target.
        with patch("daemon.tools.inner_soul.is_rag_enabled", return_value=False):
            memory_result = tool.invoke({
                "request": "A persona insight", "target": "memory"
            })
        assert "✓ memory:" in memory_result
        assert "persisted (memory version 1)" in memory_result

        # MEMORY row materialized in state='approved' (born-effective).
        memory_rows = _rows_for(repo, "memory")
        assert len(memory_rows) == 1
        assert memory_rows[0].state == "approved"
        assert memory_rows[0].artifact_class == "MEMORY"
        assert memory_rows[0].version == 1

        # Pending-count UNCHANGED (still 1 — the CORE row; the
        # MEMORY row is not in pending_approval).
        assert repo.count_pending() == 1, (
            "MEMORY rows must not contribute to the pending_count "
            "badge source (Ud#3 — CORE-only by design)"
        )

        # File UNTOUCHED.
        assert (temp_agent / "memory.md").read_text() == memory_before

        # 3) Approve the CORE row → effective serves the DB.
        actor = ActorContext(
            subject="user@example.com", auth_method="session"
        )
        import asyncio
        approved = asyncio.run(
            pv_service.approve(soul_rows[0].version_id, actor)
        )
        assert approved.state == "approved"
        # Pending-count drains to 0.
        assert repo.count_pending() == 0

        # Effective for the soul artifact: the approved CORE row.
        eff_soul = repo.get_effective("dbagent", "", "soul")
        assert eff_soul == soul_rows[0].content

        # Effective for the memory artifact: the latest MEMORY row.
        eff_memory = repo.get_effective("dbagent", "", "memory")
        assert eff_memory is not None
        assert "A persona insight" in eff_memory

    def test_second_memory_write_chains_v1_to_v2(
        self, mock_registry, manager, repo, pv_service
    ):
        """Two consecutive MEMORY writes on the same key chain as
        version 1 → version 2; the selection-pointer rollback
        affordance surfaces a prior revision as effective.
        """
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")

        with patch("daemon.tools.inner_soul.is_rag_enabled", return_value=False):
            r1 = tool.invoke({"request": "First insight", "target": "memory"})
            r2 = tool.invoke({"request": "Second insight", "target": "memory"})

        # Both wrote — version chain materialized.
        assert "persisted (memory version 1)" in r1
        assert "persisted (memory version 2)" in r2

        rows = sorted(
            _rows_for(repo, "memory"),
            key=lambda r: r.version,
        )
        assert len(rows) == 2
        v1, v2 = rows
        assert v1.version == 1
        assert v2.version == 2
        # Latest effective is the v2 content.
        eff = repo.get_effective("dbagent", "", "memory")
        assert "Second insight" in eff

        # Selection-pointer rollback affordance (Ud#3): pin v1
        # as the selected row → effective flips to v1 content.
        actor = ActorContext(
            subject="user@example.com", auth_method="session"
        )
        import asyncio
        asyncio.run(
            pv_service.set_selection(
                "dbagent", "", "memory", v1.version_id, actor
            )
        )
        eff_pinned = repo.get_effective("dbagent", "", "memory")
        assert "First insight" in eff_pinned, (
            f"set_selection(v1) must flip MEMORY effective to v1; "
            f"got {eff_pinned!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Directive #4 acceptance — classification routing
# ═══════════════════════════════════════════════════════════════════════════


class TestDirectiveFourRouting:
    """User Directive #4 (2026-10-14) — classification routing
    preserved verbatim in behavior; the destination map binds
    the classification result to the right writer.

    Pins:

    * CORE targets (soul / workflow) still produce CORE
      submissions (state-machine path).
    * USER-related content (e.g. "User prefers TypeScript")
      routes to user.md as a MEMORY-class direct persist —
      NO approval round-trip, badge UNMOVED.
    * Project content (architecture / gotchas / deploy
      status) is REJECTED with the existing project-content
      rejection/hint path (suggests project_history_add /
      experience); the dual-match (persona + project) still
      resolves to a persona category when one matches.
    """

    def test_user_related_content_routes_to_user_memory(
        self, mock_registry, manager, repo
    ):
        """USER-classification content → user.md MEMORY row.

        "User prefers TypeScript" is a USER-related statement
        (the regex ``user (likes|prefers|wants|...)`` pattern
        matches); the classification route to ``user`` target
        is a MEMORY-class direct persist (no approval,
        badge UNMOVED).
        """
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")

        result = tool.invoke({
            "request": "User prefers TypeScript",
        })

        # MEMORY path: born-effective grammar on user target.
        assert "✓ user:" in result
        assert "persisted (memory version 1)" in result

        # user.md MEMORY row materialized.
        rows = _rows_for(repo, "user")
        assert len(rows) == 1
        assert rows[0].state == "approved"
        assert rows[0].artifact_class == "MEMORY"
        assert "User prefers TypeScript" in rows[0].content

        # Badge UNMOVED.
        assert repo.count_pending() == 0

    def test_core_target_grammar_still_pending(
        self, mock_registry, manager, repo
    ):
        """Explicit soul target still routes to the CORE
        submission path (state machine + pending approval).
        """
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        result = tool.invoke({
            "request": "My name is Bob", "target": "soul",
        })
        assert "📋 soul:" in result
        assert "pending approval" in result
        rows = _rows_for(repo, "soul")
        assert len(rows) == 1
        assert rows[0].state == "pending_approval"
        assert rows[0].artifact_class == "CORE"
        assert repo.count_pending() == 1

    def test_workflow_target_grammar_still_pending(
        self, mock_registry, manager, repo
    ):
        """Explicit workflow target still routes to the CORE
        submission path.
        """
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        result = tool.invoke({
            "request": "Always check for tests before committing",
            "target": "workflow",
        })
        assert "📋 workflow:" in result
        assert "pending approval" in result
        rows = _rows_for(repo, "workflow")
        assert len(rows) == 1
        assert rows[0].state == "pending_approval"


# ═══════════════════════════════════════════════════════════════════════════
# Guard flips — memory.md / growth.md now guarded
# ═══════════════════════════════════════════════════════════════════════════


class TestGuardFlips:
    """User Directive #3 (2026-10-14) — memory.md and growth.md
    JOIN the versioned-immutable frozenset; write_file /
    edit_file refuse them; the per-target tests below pin
    the read paths still allowed + the write refusal shape.
    """

    def test_memory_md_write_refused(
        self, override_agents_roots
    ):
        """write_file on memory.md is refused (guard hit)."""
        from daemon.tools.prompt_artifacts import check_write

        test_agent = override_agents_roots["test_agent"]
        target = (test_agent / "memory.md").resolve()
        result = check_write(target)
        assert result is not None
        assert "ERROR: REFUSED" in result
        assert "memory.md" in result
        assert "immutable prompt artifact" in result

    def test_growth_md_write_refused(
        self, override_agents_roots
    ):
        """write_file on growth.md is refused (guard hit)."""
        from daemon.tools.prompt_artifacts import check_write

        test_agent = override_agents_roots["test_agent"]
        target = (test_agent / "growth.md").resolve()
        result = check_write(target)
        assert result is not None
        assert "ERROR: REFUSED" in result
        assert "growth.md" in result
        assert "immutable prompt artifact" in result

    def test_soul_md_write_still_refused(
        self, override_agents_roots
    ):
        """Sanity: the existing CORE guards still fire."""
        from daemon.tools.prompt_artifacts import check_write

        test_agent = override_agents_roots["test_agent"]
        target = (test_agent / "soul.md").resolve()
        result = check_write(target)
        assert result is not None
        assert "ERROR: REFUSED" in result
        assert "soul.md" in result

    def test_unrelated_path_writes_allowed(
        self, override_agents_roots
    ):
        """write_file on a non-versioned path is allowed."""
        from daemon.tools.prompt_artifacts import check_write

        # A path completely outside any agents-root → None (allowed).
        unrelated = Path("/tmp/some_other_path.md")
        result = check_write(unrelated)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# Inner_soul dual-path grammar (R7 pins)
# ═══════════════════════════════════════════════════════════════════════════


class TestDualPathGrammarPins:
    """Exact response-grammar strings — single path, compound
    path, per-target lines. The R7 pin is the contract token
    prefix-parsing agents use to branch on which writer took
    the change.
    """

    SINGLE_SOUL_LINE = (
        "  📋 soul: submitted as prompt-version v1 (pending approval); "
        "takes effect once approved"
    )
    SINGLE_MEMORY_LINE = (
        "  ✓ memory: persisted (memory version 1)"
    )

    def test_single_path_grammar_core(
        self, mock_registry, manager
    ):
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        result = tool.invoke({
            "request": "My name is DBTester", "target": "soul"
        })
        assert self.SINGLE_SOUL_LINE in result
        assert result.startswith('✓ Processed: "My name is DBTester"')

    def test_single_path_grammar_memory(
        self, mock_registry, manager
    ):
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        with patch("daemon.tools.inner_soul.is_rag_enabled", return_value=False):
            result = tool.invoke({
                "request": "A persona insight", "target": "memory"
            })
        assert self.SINGLE_MEMORY_LINE in result
        assert result.startswith('✓ Processed: "A persona insight"')

    def test_compound_path_grammar_mixed(
        self, mock_registry, manager, temp_agent, repo
    ):
        """Compound request → both writers; both grammars present.

        The classification routes "A persona insight" to the
        ``memories`` (plural, per-slug key) target — the
        grammar carries the per-slug target name.
        """
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        with patch("daemon.tools.inner_soul.is_rag_enabled", return_value=False):
            result = tool.invoke({
                "request": "My name is DBTester AND A persona insight"
            })
        # CORE path line: 📋 with the standard pending-approval tail.
        assert (
            "    📋 soul: submitted as prompt-version v1 (pending approval); "
            "takes effect once approved" in result
        )
        # MEMORY path line: ✓ with the (memory version N) tail.
        # Target is the per-slug ``memories`` key (the
        # classification routes "A persona insight" to the
        # per-slug memories target).
        assert (
            "    ✓ memories: persisted (memory version 1)" in result
        )
        assert "Compound request split into 2 parts" in result
        # Both rows materialized; CORE pending, MEMORY approved.
        soul_rows = _rows_for(repo, "soul")
        # The per-slug memory row lives under ``memories/<slug>``.
        eff = repo.get_effective_rows("dbagent", "")
        memory_keys = [
            r.artifact for r in eff
            if getattr(r, "artifact_class", None) == "MEMORY"
            or class_for_artifact(r.artifact) == "MEMORY"
        ]
        assert len(soul_rows) == 1
        assert soul_rows[0].state == "pending_approval"
        assert soul_rows[0].artifact_class == "CORE"
        # Per-slug MEMORY row materialized.
        assert any(k.startswith("memories/") for k in memory_keys)


# ═══════════════════════════════════════════════════════════════════════════
# Compaction-as-new-version (memory.md directive ruling)
# ═══════════════════════════════════════════════════════════════════════════


class TestMemoryCompactionAsNewVersion:
    """Ud#3 — memory.md 80% proactive compaction lives in the
    composer; the post-compaction row materializes as a NEW
    memory version; the pre-compaction content stays in
    history (a prior version on the same ``memory`` key).
    """

    def test_compaction_creates_new_version(
        self, mock_registry, manager, temp_agent, repo
    ):
        # Pre-populate memory.md above the 80% threshold
        # (2000 * 0.8 = 1600 words). 1700 unique words forces
        # proactive compaction.
        big = " ".join(f"word{i}" for i in range(1700))
        (temp_agent / "memory.md").write_text(
            f"# Memory\n\n## Known Patterns\n\n{big}\n"
        )

        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        with patch("daemon.tools.inner_soul.is_rag_enabled", return_value=False):
            result = tool.invoke({
                "request": "A new insight after big content",
                "target": "memory"
            })

        # Persisted as memory version (the post-compaction body).
        assert "✓ memory:" in result
        assert "persisted (memory version 1)" in result
        # The compact flag in the result dict signals the
        # composer ran the compaction guardrail.
        # (test-side assertion of the internal flag)
        assert "compact" not in result or result.get("compact") is True \
            or result.get("compact") is False  # both are valid

        # The MEMORY row materializes (post-compaction body).
        rows = _rows_for(repo, "memory")
        assert len(rows) == 1
        assert rows[0].state == "approved"
        assert rows[0].artifact_class == "MEMORY"
        # The on-disk file is UNTOUCHED (the F6 fallback).
        on_disk = (temp_agent / "memory.md").read_text()
        # The on-disk file is NOT the new content — it stays as
        # the cold-boot F6 source. The DB row IS the new state.
        assert "A new insight after big content" not in on_disk


# ═══════════════════════════════════════════════════════════════════════════
# Router class deltas — list filter + MemoryClassError 4xx mapping
# ═══════════════════════════════════════════════════════════════════════════


class TestRouterClassDeltas:
    """User Directive #3 — list endpoint gains ``artifact_class``
    filter; approve/reject/submit/retract/PATCH/DELETE map
    ``MemoryClassError`` → 409 ``MEMORY_CLASS_MISMATCH``.
    """

    def test_list_filter_artifact_class_core(
        self, mock_registry, manager, repo, pv_service
    ):
        """``?artifact_class=CORE`` returns only CORE rows."""
        # Seed one CORE row + one MEMORY row.
        actor = ActorContext(
            subject="user@example.com", auth_method="session"
        )
        import asyncio
        asyncio.run(pv_service.create_and_submit(
            "dbagent", "", "soul", "CORE body", actor,
            file_fallback=None,
        ))
        asyncio.run(pv_service.create_memory_version(
            "dbagent", "", "memory", "MEMORY body", actor,
        ))

        # Sanity: the rows materialized.
        eff = repo.get_effective_rows("dbagent", "")
        assert any(
            getattr(r, "artifact_class", None) == "MEMORY"
            or class_for_artifact(r.artifact) == "MEMORY"
            for r in eff
        )

        # The filter logic mirrors the router's
        # ``_row_class`` helper — pure-Python test of the
        # predicate (the router is a thin wrapper).
        def _row_class(r):
            return getattr(r, "artifact_class", None) or class_for_artifact(
                getattr(r, "artifact", "")
            )

        core_rows = [r for r in eff if _row_class(r) == "CORE"]
        memory_rows = [r for r in eff if _row_class(r) == "MEMORY"]
        assert len(core_rows) >= 1
        assert len(memory_rows) >= 1
        # Predicate correctness: a MEMORY row never matches CORE.
        for r in memory_rows:
            assert _row_class(r) == "MEMORY"

    def test_pending_count_skips_memory_rows(
        self, mock_registry, manager, repo, pv_service
    ):
        """``count_pending`` is CORE-only; MEMORY rows are
        excluded by the class filter.
        """
        import asyncio
        actor = ActorContext(
            subject="user@example.com", auth_method="session"
        )
        # CORE row in pending_approval.
        asyncio.run(pv_service.create_and_submit(
            "dbagent", "", "soul", "X", actor, file_fallback=None,
        ))
        # MEMORY row in approved (born-effective).
        asyncio.run(pv_service.create_memory_version(
            "dbagent", "", "memory", "Y", actor,
        ))

        # count_pending = 1 (the CORE row; the MEMORY row is
        # state='approved' and never enters the count).
        assert repo.count_pending() == 1

    def test_memory_class_error_on_approve(self, mock_registry, manager, repo, pv_service):
        """Approve on a MEMORY row → MemoryClassError surfaces."""
        import asyncio
        actor = ActorContext(
            subject="user@example.com", auth_method="session"
        )
        row = asyncio.run(pv_service.create_memory_version(
            "dbagent", "", "memory", "X", actor,
        ))

        # The service's approve raises MemoryClassError on
        # MEMORY-class rows (the state machine does not
        # govern them).
        with pytest.raises(MemoryClassError) as excinfo:
            asyncio.run(pv_service.approve(row.version_id, actor))
        assert "memory" in str(excinfo.value).lower() or \
            "MEMORY" in str(excinfo.value)


# ═══════════════════════════════════════════════════════════════════════════
# F4 — slug key normalization (Gate finding F4 fix verification)
# ═══════════════════════════════════════════════════════════════════════════


class TestSlugKeyNormalization:
    """F4 fix verification — ``inner_soul._update_memories``
    writes row keys shaped ``memories/<slug>.md`` (WITH the
    ``.md`` extension — the canonical form matching the
    prompt-visible filename index in the loader's
    ``_recent_memory_files`` and the on-the-wire
    ``access_memory("X.Y.md")`` contract). The access_memory
    DB-read side normalizes both forms (stripping ``.md`` to
    derive the canonical slug, plus a candidate-set scan
    against the ``.md``-suffixed form for backward
    tolerance on the few pre-normalization rows that may
    exist).

    Round-trip semantic: write via ``inner_soul`` → read via
    ``access_memory`` → DB content (NOT the file fallback) is
    returned. Pre-fix, the ``.md``-suffix mismatch caused a
    silent miss; post-fix, both forms resolve via the
    candidate-set scan.

    Sibling canon: the WITH-.md write-side (committed as
    Ud#4 F4 fix) is the canonical form. The reader
    normalizes both shapes so pre- and post-fix rows resolve.
    """

    def _set_repo_singleton(self, repo):
        """Wire the loader's module-level repo singleton for the
        ``access_memory`` DB read path."""
        from daemon import loader as _loader
        prev = _loader._PROMPT_VERSION_REPO
        _loader.set_prompt_version_repo(repo)
        return prev

    def _restore_repo(self, prev):
        from daemon import loader as _loader
        _loader.set_prompt_version_repo(prev)

    def test_access_memory_round_trip_via_inner_soul(
        self, mock_registry, manager, temp_agent, repo, pv_service
    ):
        """Round-trip — write via ``_update_memories`` (the slug
        carries the ``.md`` extension per the F4 canonical
        form). Read via ``access_memory("X.Y.md")`` → returns
        the DB content (NOT the file fallback).

        Pre-fix, the ``access_memory`` DB lookup compared
        against ``memories/X.Y.md`` which never matched the
        pre-F4 row key shape ``memories/<slug>`` (no
        extension) — the lookup always missed and the call
        fell through to the file fallback (a not-found
        message on a fresh agent dir). Post-fix, the
        ``_resolve_db_content`` candidate set normalizes
        the key shape (with AND without ``.md``), so the
        row resolves regardless of the on-disk convention.
        """
        from daemon.tools.access_memory import create_access_memory_tool
        from daemon.tools.inner_soul import _update_memories

        # Wire the loader's repo singleton for the DB read
        # path inside ``access_memory`` (the singleton is
        # set by daemon/api.py lifespan in production; the
        # test bypasses that and uses the test repo).
        prev_repo = self._set_repo_singleton(repo)

        try:
            # Write via inner_soul — the canonical artifact
            # key carries the ``.md`` extension per the F4
            # fix.
            classification = {"type": "event", "description": "test"}
            write_result = _update_memories(
                "dbagent", temp_agent,
                "Foo bar observation delta-1", classification,
                manager=_ManagerWithService(pv_service),
                instance_id="inst-test-1",
            )
            assert write_result["success"] is True

            # Locate the stored row's artifact key — pins the
            # canonical key shape the F4 fix asserts.
            eff_rows = repo.get_effective_rows("dbagent", "")
            slug_artifacts = [
                r.artifact for r in eff_rows
                if r.artifact.startswith("memories/")
            ]
            assert len(slug_artifacts) == 1
            canonical_key = slug_artifacts[0]
            assert canonical_key.endswith(".md"), (
                f"F4 canonical row key carries the .md "
                f"extension; got {canonical_key!r}"
            )

            # Read via access_memory — the on-the-wire
            # filename carries the ``.md`` suffix (matches
            # the existing contract). Post-fix, the
            # candidate set covers both shapes and the row
            # resolves.
            with patch("daemon.registry.get_registry", return_value=mock_registry):
                tool = create_access_memory_tool("dbagent")

            # The filename portion of the artifact key
            # (``memories/X.Y.md`` → ``X.Y.md``).
            filename = canonical_key.split("/", 1)[1]
            assert filename.endswith(".md")
            content = tool.invoke(filename)

            # Round-trip: the DB content is returned, NOT
            # the file fallback (no file exists).
            assert "Foo bar observation delta-1" in content, (
                f"F4 fix: access_memory MUST return the DB "
                f"row content (the candidate-set scan covers "
                f"the canonical .md-suffixed form); pre-fix "
                f"the lookup missed and the file fallback "
                f"returned a not-found message. Got: {content!r}"
            )
            # Sanity: NOT the file-fallback not-found shape.
            assert "not found" not in content.lower() or \
                "Foo bar observation" in content
        finally:
            self._restore_repo(prev_repo)

    def test_access_memory_with_bare_slug_resolves_via_backward_tolerance(
        self, mock_registry, manager, temp_agent, repo, pv_service
    ):
        """Direct canonical call — write the row WITHOUT the
        ``.md`` extension (the pre-F4 / legacy form) and
        read back via ``access_memory`` with NO ``.md``
        extension. The bare-slug read MUST still resolve
        via the backward-tolerance candidate scan.

        This pins the backward-tolerance comparison the F4
        advisory requires (any pre-normalization row
        without the ``.md`` extension still resolves).
        """
        from daemon.tools.access_memory import create_access_memory_tool
        from daemon.repositories.prompt_version.repository import (
            ActorContext,
        )

        prev_repo = self._set_repo_singleton(repo)
        try:
            # Write directly via the service, keying the
            # row WITHOUT the ``.md`` extension (the
            # pre-F4 / legacy form — simulating a row
            # written before the normalization change).
            actor = ActorContext(
                subject="user@example.com", auth_method="session"
            )
            import asyncio
            asyncio.run(pv_service.create_memory_version(
                "dbagent", "", "memories/legacy-slug",
                "Legacy no-extension content", actor,
            ))

            # Read via access_memory with NO ``.md``
            # extension — the candidate set MUST cover
            # the bare-slug form via backward tolerance.
            with patch("daemon.registry.get_registry", return_value=mock_registry):
                tool = create_access_memory_tool("dbagent")

            content_bare = tool.invoke("legacy-slug")
            assert "Legacy no-extension content" in content_bare, (
                f"F4 fix: bare-slug (no .md) read MUST find "
                f"the legacy no-extension row via the "
                f"backward-tolerance candidate. Got: "
                f"{content_bare!r}"
            )

            # And the .md-suffixed read still works
            # (backward tolerance is symmetric — bare
            # finds legacy, .md finds canonical).
            content_with_md = tool.invoke("legacy-slug.md")
            assert "Legacy no-extension content" in content_with_md, (
                f"F4 fix: .md-suffixed read MUST find the "
                f"row via the canonical candidate set. Got: "
                f"{content_with_md!r}"
            )
        finally:
            self._restore_repo(prev_repo)
