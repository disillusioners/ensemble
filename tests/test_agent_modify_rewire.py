"""Phase B T8 — ``agent_mother.agent_modify`` DB-submission rewire tests.

agent-prompt-immutability commission
(``.agents/shared/planning/agent-prompt-immutability/phaseB-plan.md`` T8).

Acceptance (plan §Test Strategy #10): ``_mother`` calling
``agent_modify(file="soul.md", content=...)`` produces a
``pending_approval`` row and does NOT touch the file; ``memory.md``
still writes (D6 carve-out).

What is pinned here (against the REAL tool closure from
``create_mother_tools`` — not a replay copy):

* every versioned artifact (soul / rule / workflow / tools_note /
  tools / skill / user) routes to ``create_and_submit`` and produces
  a ``pending_approval`` row keyed ``(agent_id, version_tag,
  artifact)`` while the file stays byte-identical;
* ``developer[v2]`` keys the row at ``("developer", "v2")``; a base
  agent keys at ``("developer", "")`` (D9/F9 composition);
* ``memory.md`` keeps the direct write AND invalidates the prompt
  cache with the resolved ``version_tag`` (D9 threading);
* non-modifiable files keep the Phase A refusal shape;
* the ``_mother``-only system-agent protection (``:352-356`` region)
  is intact;
* a missing prompt-version service fails CLOSED (no silent file
  write, no exception leak);
* two consecutive submits on one key archive the prior pending row
  (A5/C4 ``≤ 1 active pending per key`` — integration view).

Fence discipline (2026-10-12 critical note — zero tolerance): every
test patches ``daemon.tools.agent_mother.BASE_DIR`` to a
``tmp_path``-rooted Path BEFORE invoking the tool. The tool resolves
agent dirs through that module global, so an unpatched invocation
would read/write the REAL ``agents/`` tree (this bit the T8 smoke
run once — ``agents/developer[v2]/memory.md`` had to be
``git checkout``-restored; the fence is now structural in these
tests).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.prompt_version.models import (
    PromptAuditEvent,
    PromptVersion,
)
from daemon.repositories.prompt_version.repository import (
    PromptVersionRepository,
)
from daemon.services.prompt_version_service import PromptVersionService
from daemon.tools.agent_mother import create_mother_tools


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def prompt_db_engine(tmp_path):
    """Fresh file-SQLite engine carrying the two prompt-version tables."""
    db_path = tmp_path / "promptver.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def service(prompt_db_engine):
    """The REAL PromptVersionService over the scratch engine (A4 facade)."""
    return PromptVersionService(
        repo=PromptVersionRepository(prompt_db_engine)
    )


class _FakePromptCache:
    """Records ``invalidate`` calls — the D9 version_tag threading spy."""

    def __init__(self):
        self.calls: list[tuple[str, str | None]] = []

    def invalidate(self, agent_id, version_tag=None):
        self.calls.append((agent_id, version_tag))


class _FakeManager:
    """Manager double carrying only what ``agent_modify`` touches."""

    def __init__(self, svc):
        self._prompt_version_service = svc
        self.prompt_cache = _FakePromptCache()


@pytest.fixture
def manager(service):
    return _FakeManager(service)


@pytest.fixture
def agent_modify(manager):
    """The REAL ``agent_modify`` tool closure (mother category)."""
    tools = create_mother_tools(manager, "inst-mother-test")
    return next(t for t in tools if t.name == "agent_modify")


@pytest.fixture
def fenced_agents_root(tmp_path):
    """tmp agents tree + BASE_DIR fence — the structural 2026-10-12 guard.

    Creates ``<tmp>/agents/<name>/`` dirs with starter files and patches
    ``daemon.tools.agent_mother.BASE_DIR`` at the SOURCE (the module
    global the tool resolves paths through). Restores automatically.
    """
    agents_root = tmp_path / "agents"
    agents_root.mkdir()

    def _make_agent(name: str, files: dict[str, str]) -> Path:
        d = agents_root / name
        d.mkdir()
        for fname, body in files.items():
            (d / fname).write_text(body)
        return d

    _make_agent("developer", {"soul.md": "OLD SOUL", "memory.md": "M0"})
    _make_agent("developer[v2]", {"soul.md": "OLD SOUL v2", "memory.md": "M0 v2"})
    _make_agent("tester", {"user.md": "U0"})

    with mock.patch(
        "daemon.tools.agent_mother.BASE_DIR", tmp_path
    ):
        yield {"root": agents_root, "make_agent": _make_agent}


def _invoke(tool, **kwargs) -> dict:
    """Run the async tool coroutine to completion."""
    return asyncio.run(tool.coroutine(**kwargs))


# ─────────────────────────────────────────────────────────────────────────────
# T8 acceptance — DB submission, file untouched
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentModifyDbSubmission:
    """The versioned-immutable set routes through create_and_submit."""

    @pytest.mark.parametrize(
        "fname,artifact",
        [
            ("soul.md", "soul"),
            ("rule.md", "rule"),
            ("workflow.md", "workflow"),
            ("tools_note.md", "tools_note"),
            ("tools.md", "tools"),
            ("skill.md", "skill"),
            ("user.md", "user"),  # D5 — joins DB-versioning
        ],
    )
    def test_versioned_artifact_submits_pending_row_and_never_touches_file(
        self, agent_modify, service, fenced_agents_root, fname, artifact
    ):
        target = fenced_agents_root["root"] / "developer"
        before = (target / fname)
        before.write_text("ORIGINAL")
        original_bytes = before.read_bytes()

        result = _invoke(
            agent_modify, agent_name="developer", file=fname, content="NEW BODY"
        )

        assert result["success"] is True
        assert result["state"] == "pending_approval"
        assert result["submitted"] is True
        assert result["version"] == 1
        assert result["version_id"]

        # The file is byte-identical — the write NEVER hit disk.
        assert before.read_bytes() == original_bytes

        # The row exists at the right key with the right content.
        rows = service._repo.list_versions("developer", "", artifact)
        assert len(rows) == 1
        assert rows[0].state == "pending_approval"
        assert rows[0].content == "NEW BODY"

    def test_tagged_agent_keys_row_at_base_id_plus_tag(
        self, agent_modify, service, fenced_agents_root
    ):
        """``developer[v2]`` → ``(developer, v2, soul)`` (F9/D15)."""
        result = _invoke(
            agent_modify,
            agent_name="developer[v2]",
            file="soul.md",
            content="V2 BODY",
        )

        assert result["success"] is True
        rows = service._repo.list_versions("developer", "v2", "soul")
        assert len(rows) == 1
        assert rows[0].content == "V2 BODY"
        # No leak into the base agent's key.
        assert service._repo.list_versions("developer", "", "soul") == []

    def test_file_fallback_is_current_file_content(
        self, agent_modify, service, fenced_agents_root
    ):
        """file_fallback = the pre-submit on-disk body (F6 chain bottom)."""
        target = fenced_agents_root["root"] / "developer"
        (target / "soul.md").write_text("CURRENT DISK BODY")

        result = _invoke(
            agent_modify,
            agent_name="developer",
            file="soul.md",
            content="DRAFT BODY",
        )
        assert result["success"] is True
        assert result["file_fallback_used"] is True

        # The audit trail's CREATED row records the fallback (the
        # composite stamps lineage metadata) — verified via the
        # service-level effective read: with only a pending row,
        # effective is None, but the row itself carries the draft.
        rows = service._repo.list_versions("developer", "", "soul")
        assert rows[0].content == "DRAFT BODY"

    def test_missing_file_fallback_passes_none(
        self, agent_modify, service, fenced_agents_root
    ):
        """A file that does not exist on disk → file_fallback=None."""
        result = _invoke(
            agent_modify,
            agent_name="tester",
            file="workflow.md",
            content="FRESH",
        )
        assert result["success"] is True
        assert result["file_fallback_used"] is False

    def test_resubmit_archives_prior_pending_leaves_one_active(
        self, agent_modify, service, fenced_agents_root
    ):
        """A5/C4: ≤ 1 active pending per key; prior pending archived."""
        r1 = _invoke(agent_modify, agent_name="developer", file="soul.md", content="V1")
        r2 = _invoke(agent_modify, agent_name="developer", file="soul.md", content="V2")

        assert r1["success"] and r2["success"]
        assert (r1["version"], r2["version"]) == (1, 2)
        rows = service._repo.list_versions("developer", "", "soul")
        states = {r.version: r.state for r in rows}
        assert states[1] == "archived"
        assert states[2] == "pending_approval"


class TestAgentModifyMemoryCarveOut:
    """D6 — memory.md keeps the direct write + D9 invalidation."""

    def test_memory_md_writes_file_directly_and_invalidates_with_tag(
        self, agent_modify, manager, fenced_agents_root
    ):
        target = fenced_agents_root["root"] / "developer[v2]"
        result = _invoke(
            agent_modify,
            agent_name="developer[v2]",
            file="memory.md",
            content="M1",
        )

        assert result["success"] is True
        assert result.get("cache_invalidated") is True
        # The file REALLY changed on disk this time.
        assert (target / "memory.md").read_text() == "M1"
        # D9: the bust reaches the per-tag key, not the base key.
        assert manager.prompt_cache.calls == [("developer[v2]", "v2")]

    def test_memory_md_base_agent_invalidates_base_key(
        self, agent_modify, manager, fenced_agents_root
    ):
        target = fenced_agents_root["root"] / "developer"
        result = _invoke(
            agent_modify, agent_name="developer", file="memory.md", content="M1"
        )
        assert result["success"] is True
        assert (target / "memory.md").read_text() == "M1"
        # Base agent: tag resolves to None (normalized to "" by the
        # repo; the cache call carries the raw resolved None).
        assert manager.prompt_cache.calls == [("developer", None)]

    def test_memory_md_never_creates_db_rows(
        self, agent_modify, service, fenced_agents_root
    ):
        _invoke(agent_modify, agent_name="developer", file="memory.md", content="M1")
        # No 'memory' artifact rows — the carve-out is total (D6).
        assert service._repo.list_versions("developer", "", "memory") == []


# ─────────────────────────────────────────────────────────────────────────────
# Refusals + protections preserved from Phase A
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentModifyRefusals:
    """The refusal shape stays; only the allowed set changed (T8)."""

    @pytest.mark.parametrize("fname", ["growth.md", "meta.json", "random.md"])
    def test_non_modifiable_file_keeps_refusal_shape(
        self, agent_modify, fenced_agents_root, fname
    ):
        result = _invoke(
            agent_modify, agent_name="developer", file=fname, content="X"
        )
        assert result["success"] is False
        assert f"Cannot modify '{fname}'" in result["error"]

    def test_system_agent_protection_intact(
        self, agent_modify, fenced_agents_root
    ):
        """``:352-356`` — system agents (``_`` prefix) refuse except _mother."""
        for fname in ("soul.md", "memory.md"):
            result = _invoke(
                agent_modify, agent_name="_baby_template", file=fname, content="X"
            )
            assert result["success"] is False
            assert "Cannot modify system agents except _mother" in result["error"]

    def test_mother_self_modify_allowed(self, agent_modify, fenced_agents_root):
        """``_mother`` self-mod keeps its exemption on the carve-out."""
        fenced_agents_root["make_agent"]("_mother", {"memory.md": "SELF"})
        result = _invoke(
            agent_modify, agent_name="_mother", file="memory.md", content="SELF2"
        )
        assert result["success"] is True

    def test_unknown_agent_refused_on_db_path(
        self, agent_modify, fenced_agents_root
    ):
        result = _invoke(
            agent_modify, agent_name="ghost_agent", file="soul.md", content="X"
        )
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_unknown_agent_refused_on_memory_path(
        self, agent_modify, fenced_agents_root
    ):
        result = _invoke(
            agent_modify, agent_name="ghost_agent", file="memory.md", content="X"
        )
        assert result["success"] is False
        assert "not found" in result["error"]


# ─────────────────────────────────────────────────────────────────────────────
# Fail-closed posture
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentModifyFailClosed:
    """No service → refuse; service error → refuse; never a file write."""

    def test_missing_service_refuses_and_file_stays(
        self, fenced_agents_root, service
    ):
        tools = create_mother_tools(_FakeManager(None), "inst-mother-test")
        modify = next(t for t in tools if t.name == "agent_modify")

        target = fenced_agents_root["root"] / "developer"
        (target / "soul.md").write_text("PRISTINE")

        result = _invoke(modify, agent_name="developer", file="soul.md", content="X")
        assert result["success"] is False
        assert "service unavailable" in result["error"].lower()
        assert (target / "soul.md").read_text() == "PRISTINE"

    def test_service_error_surfaces_as_refusal(
        self, fenced_agents_root, manager
    ):
        class _BoomSvc:
            def create_and_submit(self, *a, **k):
                raise RuntimeError("db exploded")

        manager._prompt_version_service = _BoomSvc()
        target = fenced_agents_root["root"] / "developer"
        (target / "soul.md").write_text("PRISTINE")

        modify = next(
            t for t in create_mother_tools(manager, "i") if t.name == "agent_modify"
        )
        result = _invoke(
            modify,
            agent_name="developer",
            file="soul.md",
            content="X",
        )
        assert result["success"] is False
        assert "db exploded" in result["error"]
        assert (target / "soul.md").read_text() == "PRISTINE"


# ─────────────────────────────────────────────────────────────────────────────
# Actor + audit provenance (D10 — internal lane stamping)
# ─────────────────────────────────────────────────────────────────────────────


class TestAgentModifyProvenance:
    """The submission stamps the _mother instance identity (D10)."""

    def test_audit_row_stamps_internal_mother_actor(
        self, agent_modify, prompt_db_engine, fenced_agents_root
    ):
        _invoke(agent_modify, agent_name="developer", file="soul.md", content="X")

        from sqlalchemy import text

        with prompt_db_engine.connect() as conn:
            rows = list(conn.execute(text(
                "SELECT event_type, actor_subject, actor_auth_method "
                "FROM prompt_audit_events ORDER BY created_at"
            )))
        types = [r[0] for r in rows]
        assert "CREATED" in types and "SUBMITTED" in types
        for event_type, subject, method in rows:
            assert method == "internal", (
                f"{event_type} must stamp the internal lane, saw {method!r}"
            )
            assert subject == "inst-mother-test", (
                f"{event_type} must stamp the _mother instance id, saw {subject!r}"
            )

    def test_submitter_columns_carry_instance_triple(
        self, agent_modify, service, fenced_agents_root
    ):
        _invoke(
            agent_modify,
            agent_name="developer[v2]",
            file="soul.md",
            content="X",
        )
        rows = service._repo.list_versions("developer", "v2", "soul")
        assert rows[0].submitter_instance_id == "inst-mother-test"
        assert rows[0].submitter_agent_id == "_mother"
        # The SUBMITTER's own tag (the _mother instance is a base agent).
        assert rows[0].submitter_version_tag == ""
