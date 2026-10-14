"""inner_soul DB-submission tests (agent-prompt-immutability Phase B, T13).

Covers the T7 rewrite of the ``soul`` / ``workflow`` / ``user``
targets: every submission creates a ``pending_approval`` row via the
real ``PromptVersionRepository`` + ``PromptVersionService`` (A5/C4
composite), the on-disk ``.md`` file stays byte-identical, audit rows
carry the D10 instance identity, pending rows supersede
(archive-on-resubmit), the 📋 response grammar is pinned, and the
memory carve-outs (``memory.md`` / ``memories/``) still write files.

Also pins the SC7/C6 no-DB-rows golden across three representative
agents: with zero DB rows the loader prompt is byte-identical with the
repo wired vs unwired, and a ``pending_approval`` submission for one
agent does not change ANY agent's effective prompt (pending rows are
not effective — only approved/selected rows resolve).

Fencing (project critical notes):

* run with ``ENSEMBLE_SELF_ENV=dev`` and ``POSTGRES_DB`` NOT
  ``ensemble_prod`` (the fixture reads no ambient ``POSTGRES_*`` —
  file-SQLite only);
* agent dirs are ``tmp_path``-relative (NEVER live-shaped —
  2026-10-12 critical note); the loader-hook fence resets the
  module-level repo singleton on exit;
* run this file targeted (``--tb=short -q``), never bare pytest.
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
    get_prompt_version_hook_failures,
    load_and_cache_prompt,
    set_prompt_version_repo,
)
from daemon.registry import AgentMetadata
from daemon.repositories.prompt_version.models import (
    PromptAuditEvent,
    PromptVersion,
)
from daemon.repositories.prompt_version.repository import (
    ActorContext,
    PromptVersionRepository,
    actor_from_instance,
)
from daemon.services.prompt_version_service import PromptVersionService
from daemon.tools.inner_soul import (
    _update_memories,
    _update_memory_md,
    _update_soul,
    _update_user,
    _update_workflow,
    create_inner_soul_tool,
)


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def repo_engine() -> Iterator[Engine]:
    """Fresh file-SQLite engine (WAL) with the prompt-version schema.

    Mirrors the ``_sqlite_repo_engine`` fixture from
    ``tests/test_prompt_version_resolution.py`` (the Phase A canonical):
    file DB under the tempdir, WAL + busy_timeout pragmas,
    ``SQLModel.metadata.create_all`` for schema (the SQLite migration
    runner is not exercised here — that is the Phase A file's job).
    """
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
def service(repo) -> PromptVersionService:
    return PromptVersionService(repo=repo)


class _ManagerWithService:
    """Minimal manager stand-in: real prompt cache + real service.

    ``inner_soul`` reads ``manager._prompt_version_service`` and
    ``manager.prompt_cache`` — nothing else.
    """

    def __init__(self, service: PromptVersionService) -> None:
        self.prompt_cache = PromptCache()
        self._prompt_version_service = service


@pytest.fixture
def manager(service) -> _ManagerWithService:
    return _ManagerWithService(service)


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
    (agent_dir / "memories").mkdir()
    (agent_dir / "growth.md").write_text(
        "# Growth\n\nmax_memory_words: 2000\nmax_soul_chars: 2000\n"
    )
    return agent_dir


@pytest.fixture
def mock_registry(temp_agent):
    """Registry mock resolving ``dbagent`` to the tmp agent dir."""
    agent_metadata = AgentMetadata(
        id="dbagent",
        name="DB Agent",
        description="Test agent",
        path=temp_agent,
        system=False,
    )
    mock_reg = MagicMock()
    mock_reg.get_version.return_value = agent_metadata
    mock_reg.get_resolved.return_value = agent_metadata
    return mock_reg


@pytest.fixture
def registry_patched(mock_registry):
    with patch("daemon.registry.get_registry", return_value=mock_registry):
        yield mock_registry


def _submit(actor_instance: str = "inst-test-1") -> ActorContext:
    return actor_from_instance(actor_instance, "dbagent", "")


def _rows_for(repo: PromptVersionRepository, artifact: str) -> list[PromptVersion]:
    return repo.list_versions("dbagent", "", artifact)


def _audit_events(engine: Engine, artifact: str) -> list[PromptAuditEvent]:
    with Session(engine) as session:
        stmt = (
            select(PromptAuditEvent)
            .where(
                PromptAuditEvent.agent_id == "dbagent",
                PromptAuditEvent.version_tag == "",
                PromptAuditEvent.artifact == artifact,
            )
            .order_by(PromptAuditEvent.created_at)
        )
        return list(session.exec(stmt))


# ═══════════════════════════════════════════════════════════════════════════
# DB-row creation + file-untouched invariants (C1)
# ═══════════════════════════════════════════════════════════════════════════


class TestSoulSubmissionCreatesPendingRow:
    """C1 — agent soul change → pending_approval row, file untouched."""

    def test_soul_submit_creates_pending_row_file_untouched(
        self, registry_patched, manager, temp_agent, repo, repo_engine
    ):
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        soul_file = temp_agent / "soul.md"
        before_mtime = soul_file.stat().st_mtime_ns
        before_content = soul_file.read_text()

        result = tool.invoke({"request": "My name is DBTester", "target": "soul"})

        assert "📋 soul:" in result
        assert "pending approval" in result

        rows = _rows_for(repo, "soul")
        assert len(rows) == 1
        row = rows[0]
        assert row.state == "pending_approval"
        assert row.version == 1
        assert row.artifact == "soul"
        assert row.agent_id == "dbagent"
        assert row.version_tag == ""
        assert "**My name is DBTester**" in row.content
        assert row.submitter_instance_id == "inst-test-1"
        assert row.submitter_agent_id == "dbagent"

        # File untouched: content AND mtime (no write happened at all).
        assert soul_file.read_text() == before_content
        assert soul_file.stat().st_mtime_ns == before_mtime

    def test_history_dir_not_created(self, registry_patched, manager, temp_agent, repo):
        """D10 — the history/*_soul_change.md audit file is retired."""
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        tool.invoke({"request": "My name is DBTester", "target": "soul"})
        assert not (temp_agent / "history").exists()

    def test_audit_rows_stamped_with_instance_identity(
        self, registry_patched, manager, temp_agent, repo, repo_engine
    ):
        """D10 — CREATED + SUBMITTED audit events carry the internal lane identity."""
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        tool.invoke({"request": "My name is DBTester", "target": "soul"})

        events = _audit_events(repo_engine, "soul")
        types = sorted(e.event_type for e in events)
        assert types == ["CREATED", "SUBMITTED"]
        for e in events:
            assert e.actor_subject == "inst-test-1"
            assert e.actor_auth_method == "internal"

    def test_workflow_submit_learned_append_composes_on_file_fallback(
        self, registry_patched, manager, temp_agent, repo
    ):
        """C4 — workflow mutator composes on the effective chain (file fallback)."""
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        workflow_file = temp_agent / "workflow.md"
        before = workflow_file.read_text()

        result = tool.invoke({
            "request": "Always re-read the brief",
            "target": "workflow",
        })

        assert "📋 workflow:" in result
        rows = _rows_for(repo, "workflow")
        assert len(rows) == 1
        content = rows[0].content
        # Composed on the FILE content (no DB rows yet → file_fallback base)
        assert content.startswith("# Workflow\n\n1. Process\n")
        # Learned section ensured + bullet appended
        assert "**Learned:**" in content
        assert f"- Always re-read the brief" in content
        # File untouched
        assert workflow_file.read_text() == before

    def test_user_submit_placeholder_strip(
        self, registry_patched, manager, temp_agent, repo
    ):
        """Ud#3 SUPERSEDES D5 — user.md is MEMORY-class now: born-effective,
        no approval round-trip, file untouched.
        """
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        user_file = temp_agent / "user.md"
        before = user_file.read_text()

        result = tool.invoke({
            "request": "User prefers dark mode",
            "target": "user",
        })

        # MEMORY grammar: "✓ <target>: persisted (memory version N)"
        # — the DUAL-PATH born-effective marker.
        assert "✓ user:" in result
        assert "persisted (memory version 1)" in result
        rows = _rows_for(repo, "user")
        assert len(rows) == 1
        # MEMORY rows land in state='approved' (transition-free).
        assert rows[0].state == "approved"
        assert rows[0].artifact_class == "MEMORY"
        content = rows[0].content
        assert "(To be filled" not in content
        assert "- User prefers dark mode" in content
        # File untouched — the F6 fallback stays as the cold-boot
        # source; the DB row is the live state.
        assert user_file.read_text() == before


# ═══════════════════════════════════════════════════════════════════════════
# Pending supersession + composition chain (A5 / G-4f / C4)
# ═══════════════════════════════════════════════════════════════════════════


class TestPendingSupersession:
    """Consecutive un-approved calls chain on the prior pending row."""

    def test_second_submit_chains_on_prior_pending_and_archives_it(
        self, registry_patched, manager, temp_agent, repo, repo_engine
    ):
        """A5/C4: the new row's VERSION chains on max(version) over
        draft+pending rows; the prior pending row is archived (≤ 1
        active pending per key). Content composes on the EFFECTIVE
        chain (selected > latest_approved > file) — a pending row is
        NOT effective, so the second draft composes on the file
        fallback, not on the first pending draft.
        """
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")

        tool.invoke({"request": "My name is DBTester", "target": "soul"})
        tool.invoke({"request": "I value terse answers", "target": "soul"})

        rows = _rows_for(repo, "soul")
        assert len(rows) == 2
        by_version = {r.version: r for r in rows}
        v1, v2 = by_version[1], by_version[2]

        # v1 archived, v2 pending — ≤ 1 active pending per key.
        assert v1.state == "archived"
        assert v2.state == "pending_approval"

        # Version chaining: v2 = max(version over draft+pending) + 1.
        assert v2.version == 2

        # Content composed on the EFFECTIVE chain (file fallback — no
        # approved rows yet), with the target-specific mutation.
        assert "**My name is DBTester**" not in v2.content
        assert v2.content.startswith("# Who I Am\n\nI am a test agent.\n")
        assert "- I value terse answers" in v2.content

        # Audit: ARCHIVED event for v1 superseded by v2.
        events = _audit_events(repo_engine, "soul")
        archived = [e for e in events if e.event_type == "ARCHIVED"]
        assert len(archived) == 1
        assert archived[0].version_id == v1.version_id
        assert archived[0].payload.get("superseded_by") == v2.version_id

    def test_approved_content_becomes_composition_base(
        self, registry_patched, manager, temp_agent, repo, service
    ):
        """After approval, the next draft composes on the APPROVED content."""
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")

        result1 = tool.invoke({"request": "My name is DBTester", "target": "soul"})
        assert "📋 soul:" in result1

        # Human approves v1 (session lane actor).
        row = _rows_for(repo, "soul")[0]
        human = ActorContext(subject="user@example.com", auth_method="session")
        import asyncio

        approved = asyncio.run(service.approve(row.version_id, human))
        assert approved.state == "approved"

        # Next submission composes on the APPROVED content — which
        # contains the v1 marker — not on the raw template file.
        tool.invoke({"request": "I value terse answers", "target": "soul"})
        rows = _rows_for(repo, "soul")
        v2 = [r for r in rows if r.version == 2][0]
        assert "**My name is DBTester**" in v2.content
        assert "- I value terse answers" in v2.content


class TestSoulCapRejected:
    """The max_soul_chars cap refuses the draft — no row, no file write."""

    def test_cap_rejected_no_row_no_file_write(
        self, registry_patched, manager, temp_agent, repo
    ):
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        soul_file = temp_agent / "soul.md"
        before = soul_file.read_text()

        # The tool loads max_soul_chars from growth.md (fixture: 2000,
        # file is tiny); to exercise the cap directly, call the handler
        # with a rules dict capping at the current file length.
        result = _update_soul(
            "dbagent",
            temp_agent,
            "one more line",
            {"max_soul_chars": len(before)},
            manager,
            instance_id="inst-test-1",
        )

        assert result["success"] is False
        assert "Cannot add more" in result["error"]
        assert _rows_for(repo, "soul") == []
        assert soul_file.read_text() == before


# ═══════════════════════════════════════════════════════════════════════════
# Fail-closed posture
# ═══════════════════════════════════════════════════════════════════════════


class TestFailClosed:
    """No service / no instance identity → refusal, zero rows, file intact."""

    def test_service_missing_fails_closed(self, registry_patched, temp_agent, repo):
        class _NoServiceManager:
            prompt_cache = PromptCache()
            _prompt_version_service = None

        tool = create_inner_soul_tool(_NoServiceManager(), "dbagent", "inst-test-1")
        soul_file = temp_agent / "soul.md"
        before = soul_file.read_text()

        result = tool.invoke({"request": "My name is X", "target": "soul"})

        assert "⚠ soul:" in result
        assert "NOT applied" in result
        assert _rows_for(repo, "soul") == []
        assert soul_file.read_text() == before

    def test_missing_instance_identity_fails_closed(
        self, registry_patched, manager, temp_agent, repo
    ):
        """D10 — no instance id → no actor → refusal (no anonymous drafts)."""
        result = _update_soul(
            "dbagent", temp_agent, "My name is X", {}, manager,
            instance_id=None,
        )
        assert result["success"] is False
        assert "instance identity unavailable" in result["error"]
        assert _rows_for(repo, "soul") == []


# ═══════════════════════════════════════════════════════════════════════════
# Response grammar (R7) + classification pins (C11)
# ═══════════════════════════════════════════════════════════════════════════


class TestResponseGrammarPinned:
    """Exact 📋 strings — single path, compound path, per-target lines."""

    SINGLE_SOUL_LINE = (
        "  📋 soul: submitted as prompt-version v1 (pending approval); "
        "takes effect once approved"
    )

    def test_single_path_grammar_exact(self, registry_patched, manager):
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        result = tool.invoke({"request": "My name is DBTester", "target": "soul"})
        assert self.SINGLE_SOUL_LINE in result
        # Envelope preserved verbatim.
        assert result.startswith('✓ Processed: "My name is DBTester"')
        assert "  Classification: identity (Core identity and self-definition)" in result

    def test_compound_path_grammar_exact(self, registry_patched, manager):
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        result = tool.invoke({
            "request": "My name is DBTester AND Be more concise"
        })
        # Compound per-part lines carry the same grammar, 4-space indent.
        assert (
            "    📋 soul: submitted as prompt-version v1 (pending approval); "
            "takes effect once approved" in result
        )
        assert "Compound request split into 2 parts" in result

    def test_soul_target_never_redirects_with_rag_on(
        self, registry_patched, manager, repo
    ):
        """Never-redirect pin: explicit soul target with RAG enabled still
        submits a draft (the DB path), never the 📋 RAG-redirect."""
        with patch("daemon.tools.inner_soul.is_rag_enabled", return_value=True):
            tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
            result = tool.invoke({
                "request": "My name is DBTester",
                "target": "soul",
            })
        assert "Redirected to Knowledge System" not in result
        assert "📋 soul:" in result
        assert len(_rows_for(repo, "soul")) == 1

    def test_memories_still_write_files_during_db_regime(
        self, registry_patched, manager, temp_agent, repo, service
    ):
        """Ud#3 SUPERSEDES D7 — memories target is MEMORY-class: per-slug
        DB row, file fallback stays as cold-boot F6 source (NOT written
        by the writer path). The soul line keeps the CORE grammar;
        the memories line carries the MEMORY grammar.
        """
        # Wire the manager._prompt_version_service on the
        # _ManagerWithService (the test fixture's minimal stand-in).
        manager._prompt_version_service = service
        tool = create_inner_soul_tool(manager, "dbagent", "inst-test-1")
        with patch("daemon.tools.inner_soul.is_rag_enabled", return_value=False):
            result = tool.invoke({
                "request": "My name is DBTester AND I noticed a pattern in my own pacing",
            })
        # CORE path: soul line keeps the pending-approval grammar.
        assert "📋 soul:" in result
        # MEMORY path: memories line carries the new grammar.
        assert "✓ memories:" in result
        assert "persisted (memory version 1)" in result
        # No files written by the writer path (file fallback
        # only — Ud#3).
        memories = list((temp_agent / "memories").glob("*.md"))
        assert len(memories) == 0
        # The DB row IS the live state.
        rows = _rows_for(repo, "memory")  # memory.md key
        assert len(rows) == 0  # memory target not invoked here
        # But the per-slug memories key IS the row.
        from daemon.repositories.prompt_version.repository import (
            class_for_artifact,
        )
        # The memories/ row lives under key "memories/<slug>".
        eff_rows = repo.get_effective_rows("dbagent", "")
        slug_keys = [
            r.artifact for r in eff_rows
            if class_for_artifact(r.artifact) == "MEMORY"
        ]
        assert any(k.startswith("memories/") for k in slug_keys), (
            f"Expected a memories/<slug> row, got: {slug_keys}"
        )


class TestMemoryCarveOutsUnchanged:
    """Ud#3 SUPERSEDES D6/D7 — memory.md + memories/ are now MEMORY-class.

    The file fallback stays as the cold-boot F6 source; the writer
    substrate is ``create_memory_version`` (transition-free,
    born-effective). These tests now prove the new regime:

    * memory.md writes land as MEMORY-class DB rows; the file is
      UNTOUCHED (a cold-boot fallback only).
    * memories/ writes land as MEMORY-class per-slug DB rows; the
      file under memories/ is UNTOUCHED.
    """

    def test_memory_md_persists_as_db_row(self, temp_agent, repo, service):
        result = _update_memory_md(
            "dbagent", temp_agent, "A persona insight",
            {"max_memory_words": 2000},
            manager=_ManagerWithService(service),
            instance_id="inst-test-1",
        )
        assert result["success"] is True
        assert result["target"] == "memory"
        # MEMORY row materialized.
        rows = _rows_for(repo, "memory")
        assert len(rows) == 1
        assert rows[0].state == "approved"
        assert rows[0].artifact_class == "MEMORY"
        assert "A persona insight" in rows[0].content
        # File UNTOUCHED — the file is the cold-boot F6 source,
        # not the live state.
        assert "A persona insight" not in (temp_agent / "memory.md").read_text()
        # No CORE rows for memory (it's a different key).
        assert repo.list_versions("dbagent", "", "soul") == []

    def test_memories_persists_as_per_slug_db_row(
        self, temp_agent, repo, service,
    ):
        classification = {"type": "event", "description": "test event"}
        result = _update_memories(
            "dbagent", temp_agent, "An observation", classification,
            manager=_ManagerWithService(service),
            instance_id="inst-test-1",
        )
        assert result["success"] is True
        assert result["target"] == "memories"
        # No file written by the writer path.
        files = list((temp_agent / "memories").glob("*.md"))
        assert len(files) == 0
        # Per-slug MEMORY row materialized.
        from daemon.repositories.prompt_version.repository import (
            class_for_artifact,
        )
        eff_rows = repo.get_effective_rows("dbagent", "")
        slug_rows = [
            r for r in eff_rows
            if class_for_artifact(r.artifact) == "MEMORY"
            and r.artifact.startswith("memories/")
        ]
        assert len(slug_rows) == 1
        # EffectiveRow carries latest_content (the per-class
        # resolution helper), not raw .content — read it via
        # the same helper the loader uses.
        latest = slug_rows[0].latest_content or ""
        assert "An observation" in latest


# ═══════════════════════════════════════════════════════════════════════════
# D9 — cache invalidation threads version_tag
# ═══════════════════════════════════════════════════════════════════════════


class TestInvalidateThreadsVersionTag:
    def test_soul_submit_invalidates_tagged_key_not_base(
        self, temp_agent, service, repo,
    ):
        cache = PromptCache()
        cache.set("dbagent", "BASE-PROMPT", 10, {}, None, "")
        cache.set("dbagent", "V2-PROMPT", 10, {}, None, "v2")

        class _Mgr:
            prompt_cache = cache
            _prompt_version_service = service

        registry = MagicMock()
        v2_meta = AgentMetadata(
            id="dbagent", name="DB Agent", description="d",
            path=temp_agent, system=False,
        )
        registry.get_version.return_value = v2_meta
        registry.get_resolved.return_value = v2_meta

        with patch("daemon.registry.get_registry", return_value=registry):
            tool = create_inner_soul_tool(_Mgr(), "dbagent", "inst-test-1", version_tag="v2")
            result = tool.invoke({"request": "My name is DBTester", "target": "soul"})

        assert "📋 soul:" in result
        # The v2 key was busted; the base key was NOT (S12/O-5 non-bust).
        assert cache.get("dbagent", None, "v2") is None
        assert cache.get("dbagent", None, "") is not None

        # The DB row is keyed under the TAGGED identity.
        rows = repo.list_versions("dbagent", "v2", "soul")
        assert len(rows) == 1
        assert rows[0].version_tag == "v2"


# ═══════════════════════════════════════════════════════════════════════════
# SC7 / C6 — no-DB-rows golden across three representative agents
# ═══════════════════════════════════════════════════════════════════════════


def _make_agent_dir(tmp_path: Path, name: str, with_skill: bool = False) -> Path:
    agent_dir = tmp_path / name
    agent_dir.mkdir()
    (agent_dir / "soul.md").write_text(f"# Who I Am\n\n{name} core identity.\n")
    (agent_dir / "rule.md").write_text(f"# Rules\n\n- {name} rule one\n")
    (agent_dir / "workflow.md").write_text(f"# Workflow\n\n1. {name} step\n")
    (agent_dir / "memory.md").write_text(f"# Memory\n\n{name} insights.\n")
    (agent_dir / "memories").mkdir()
    if with_skill:
        (agent_dir / "skill.md").write_text(f"# Skill\n\n{name} legacy skill.\n")
    return agent_dir


class TestNoDBRowsGoldenThreeAgents:
    """SC7/C6 golden — 3 representative agents, zero DB rows.

    The loader prompt must be byte-identical with the prompt-version
    repo wired (hook active) vs unwired (pre-B file-only path). A
    ``pending_approval`` submission for one agent changes NO agent's
    effective prompt (pending rows are not effective).
    """

    AGENTS = ("coder-like", "developer-like", "watcher-like")

    @pytest.fixture
    def golden_agents(self, tmp_path):
        return {
            "coder-like": _make_agent_dir(tmp_path, "coder-like", with_skill=True),
            "developer-like": _make_agent_dir(tmp_path, "developer-like"),
            "watcher-like": _make_agent_dir(tmp_path, "watcher-like"),
        }

    def test_prompts_byte_identical_and_pending_submit_changes_nothing(
        self, golden_agents, repo, service, repo_engine, tmp_path
    ):
        caches_a = {name: PromptCache() for name in self.AGENTS}
        caches_b = {name: PromptCache() for name in self.AGENTS}

        # Path A — hook ACTIVE with the real (empty) repo.
        prev_repo = _get_prompt_version_repo()
        set_prompt_version_repo(repo)
        try:
            prompts_a = {
                name: load_and_cache_prompt(name, agent_dir, caches_a[name], version_tag="")
                for name, agent_dir in golden_agents.items()
            }

            # Phase B write path: submit a soul draft for ONE agent via
            # the real tool. The row lands in pending_approval.
            agent_dir = golden_agents["coder-like"]
            registry = MagicMock()
            meta = AgentMetadata(
                id="coder-like", name="Coder Like", description="d",
                path=agent_dir, system=False,
            )
            registry.get_version.return_value = meta
            registry.get_resolved.return_value = meta

            class _Mgr:
                prompt_cache = caches_a["coder-like"]
                _prompt_version_service = service

            with patch("daemon.registry.get_registry", return_value=registry):
                tool = create_inner_soul_tool(_Mgr(), "coder-like", "inst-golden-1")
                result = tool.invoke({"request": "My name is Golden", "target": "soul"})
            assert "📋 soul:" in result
            assert get_prompt_version_hook_failures() == 0

            # Reload ALL three agents (cache-busted via fingerprint change
            # for coder-like; the other two never had rows).
            prompts_a2 = {
                name: load_and_cache_prompt(name, agent_dir, caches_a[name], version_tag="")
                for name, agent_dir in golden_agents.items()
            }

            # Path B — repo unwired (pre-B file-only behavior).
            set_prompt_version_repo(None)
            prompts_b = {
                name: load_and_cache_prompt(name, agent_dir, caches_b[name], version_tag="")
                for name, agent_dir in golden_agents.items()
            }
        finally:
            set_prompt_version_repo(prev_repo)

        # 1) Zero-DB-rows: hook is a strict no-op — byte-identical.
        for name in self.AGENTS:
            assert prompts_a[name] == prompts_b[name], (
                f"SC7 violated for {name}: hook changed the no-DB-rows prompt"
            )

        # 2) A PENDING submission is not effective — no agent's prompt
        #    changed, including the submitting agent (C1: template keeps
        #    serving until a human approves).
        for name in self.AGENTS:
            assert prompts_a2[name] == prompts_b[name], (
                f"C1 violated for {name}: pending row changed the effective prompt"
            )
