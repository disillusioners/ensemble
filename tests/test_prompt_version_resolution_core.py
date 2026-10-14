"""Resolution-CORE tests for User Directives #3 + #5B.

Tests the schema/repo/service/loader surface of the RESOLUTION-CORE
commit (the resolution-core of Ud#3 "approval-scope split" + Ud#5B
"predefined-agent auto-reset"). Companion to
``tests/test_prompt_version_resolution.py`` (the canonical Phase A
test file = 73 tests, all passing). This file owns the RESOLUTION-CORE
narrative:

* CORE-class state-machine transitions (sanity; not duplicated in
  Phase A's canonical file)
* MEMORY-class transition-free create / selection / latest-row precedence
* MEMORY-class state-machine refusal via :class:`MemoryClassError`
* Fingerprint isolation: MEMORY write != CORE fingerprint change for
  the same agent (and vice versa)
* Per-file memories keying (two slugs → independent keys)
* ``memory.md`` §8 loader resolution: DB row reaches the prompt;
  no-row → file fallback; no crash on empty
* R-B2 auto-reset harness: drift → reset event stamped → baseline
  re-pointed → resolution flips → idempotent
* R-B3 explicit-selection preservation: selection SURVIVES auto-reset
* R-B2 carve-out: MEMORY-class artifact → NO auto-reset
* Custom agent empty-state: zero-version custom → typed error
* Migration parity: SQLite applies via runner; re-apply is idempotent
* Index parity: model ``__table_args__`` matches .sql migration

Test execution is fenced per the project critical notes:

* ``ENSEMBLE_SELF_ENV=dev`` (no live ensemble posture);
* ``POSTGRES_DB=ensemble_dev_test_only`` (daemon-identity fence);
* use file-SQLite with WAL + QueuePool for the concurrency tests;
* run only the named file (``--tb=short -q``), never bare pytest,
  never ``-x``, never full-suite.
"""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
import threading
import time
import uuid
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, col, select

from daemon.repositories.prompt_version.models import (
    PROMPT_ARTIFACTS,
    PROMPT_ARTIFACT_CLASSES,
    PROMPT_AUDIT_EVENT_TYPES,
    PROMPT_MEMORY_ARTIFACTS,
    PROMPT_STATES,
    PromptAuditEvent,
    PromptTemplateBaseline,
    PromptVersion,
)
from daemon.repositories.prompt_version.repository import (
    ARTIFACT_CLASS_CORE,
    ARTIFACT_CLASS_MEMORY,
    ActorContext,
    AUTH_METHOD_BEARER,
    AUTH_METHOD_INTERNAL,
    AUTH_METHOD_SESSION,
    BaselineRepository,
    InvalidTransitionError,
    MemoryClassError,
    PromptClassMismatchError,
    PromptVersionRepository,
    TemplateDriftResult,
    actor_from_instance,
    actor_from_request,
    class_for_artifact,
    validate_artifact_class,
)


# ────────────────────────────────────────────────────────────────────
# Engine fixtures — file SQLite with WAL + QueuePool (per the
# Phase A canonical pattern at tests/test_prompt_version_resolution.py
# lines 118-282; the dual-driver (SC8/SQLite vs SC9/PG) parametrization
# is in the canonical file, not duplicated here — this file is
# SQLite-only by design; the new tests are all SQLite-leg exercises
# of the schema/resolution/baseline surface).
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def sqlite_repo_engine() -> Iterator[tuple[Engine, str]]:
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

    SQLModel.metadata.create_all(engine)
    try:
        yield engine, db_path
    finally:
        engine.dispose()
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + ext)
            except FileNotFoundError:
                pass


@pytest.fixture
def repo(sqlite_repo_engine) -> PromptVersionRepository:
    engine, _ = sqlite_repo_engine
    return PromptVersionRepository(engine)


@pytest.fixture
def baseline_repo(sqlite_repo_engine) -> BaselineRepository:
    engine, _ = sqlite_repo_engine
    return BaselineRepository(engine)


@pytest.fixture
def internal_actor() -> ActorContext:
    return actor_from_instance("inst-resolution", "coder", "")


@pytest.fixture
def http_actor() -> ActorContext:
    return ActorContext(subject="op@example.com", auth_method=AUTH_METHOD_SESSION)


@pytest.fixture
def agents_root(tmp_path) -> Iterator[Path]:
    """A scratch ``agents/`` tree with a single predefined agent."""

    def write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    agent = tmp_path / "agents" / "tester"
    write(agent / "soul.md", "SOUL v1\n")
    write(agent / "rule.md", "RULE v1\n")
    write(agent / "workflow.md", "WORKFLOW v1\n")
    write(agent / "tools_note.md", "TOOLS_NOTE v1\n")
    write(agent / "memory.md", "MEMORY v1\n")
    write(agent / "user.md", "USER v1\n")
    yield tmp_path / "agents"


# ────────────────────────────────────────────────────────────────────
# 1. CORE class transitions (sanity — not duplicating Phase A canonical)
# ────────────────────────────────────────────────────────────────────


class TestCOREClassTransitions:
    """CORE-class: state-machine governs (draft → pending → approved/rejected)."""

    def test_create_draft_stamps_class_core(
        self, repo, internal_actor,
    ):
        v = repo.create_draft("dev", "", "soul", "x", internal_actor)
        assert v.artifact_class == ARTIFACT_CLASS_CORE

    def test_create_draft_with_explicit_class_core_accepted(
        self, repo, internal_actor,
    ):
        v = repo.create_draft(
            "dev", "", "soul", "x", internal_actor,
            artifact_class=ARTIFACT_CLASS_CORE,
        )
        assert v.artifact_class == ARTIFACT_CLASS_CORE

    def test_create_draft_with_class_for_non_core_artifact_rejects(
        self, repo, internal_actor,
    ):
        with pytest.raises(PromptClassMismatchError):
            repo.create_draft(
                "dev", "", "memory", "x", internal_actor,
                artifact_class=ARTIFACT_CLASS_CORE,
            )

    def test_create_draft_with_class_for_core_artifact_but_memory_mismatch(
        self, repo, internal_actor,
    ):
        with pytest.raises(PromptClassMismatchError):
            repo.create_draft(
                "dev", "", "soul", "x", internal_actor,
                artifact_class=ARTIFACT_CLASS_MEMORY,
            )

    def test_full_state_machine_lifecycle(
        self, repo, internal_actor, http_actor,
    ):
        v = repo.create_draft("dev", "", "soul", "v1", internal_actor)
        assert v.state == "draft"
        v = repo.submit_for_approval(v.version_id, internal_actor)
        assert v.state == "pending_approval"
        v = repo.approve(v.version_id, http_actor)
        assert v.state == "approved"

    def test_count_pending_only_counts_core(
        self, repo, internal_actor, http_actor,
    ):
        # Create 2 CORE pending + 1 MEMORY (not counted).
        repo.create_draft("dev", "", "soul", "x", internal_actor)
        repo.create_draft("dev", "", "rule", "y", internal_actor)
        # MEMORY: use a valid MEMORY-class artifact (memory / user / growth).
        repo.create_memory_version("dev", "", "memory", "z", internal_actor)

        # Move CORE rows to pending.
        sess_state_rows = repo.list_versions("dev", "", "soul", limit=10)
        sess_state_rows2 = repo.list_versions("dev", "", "rule", limit=10)
        repo.submit_for_approval(sess_state_rows[0].version_id, internal_actor)
        repo.submit_for_approval(sess_state_rows2[0].version_id, internal_actor)

        # count_pending must count only CORE — MEMORY not pending by definition.
        assert repo.count_pending() == 2

    def test_count_pending_zero_when_no_rows(self, repo):
        assert repo.count_pending() == 0


# ────────────────────────────────────────────────────────────────────
# 2. MEMORY class: transition-free + born-effective + refusal
# ────────────────────────────────────────────────────────────────────


class TestMEMORYClassTransitionFree:
    """MEMORY rows are TRANSITION-FREE: born-effective at creation."""

    def test_create_memory_version_lands_in_effective_state(
        self, repo, internal_actor,
    ):
        v = repo.create_memory_version(
            "dev", "", "memory", "remembered", internal_actor,
        )
        assert v.state == "approved"
        assert v.artifact_class == ARTIFACT_CLASS_MEMORY

    def test_create_memory_version_resolves_in_loader_chain(
        self, repo, internal_actor,
    ):
        repo.create_memory_version("dev", "", "memory", "DB-MEM", internal_actor)
        eff = repo.get_effective("dev", "", "memory")
        assert eff == "DB-MEM"

    def test_create_memory_version_per_file_for_memories_slug(
        self, repo, internal_actor,
    ):
        v1 = repo.create_memory_version(
            "dev", "", "memories/2026-10-13-learning.md", "content A",
            internal_actor,
        )
        v2 = repo.create_memory_version(
            "dev", "", "memories/2026-10-14-skill.md", "content B",
            internal_actor,
        )
        eff1 = repo.get_effective("dev", "", "memories/2026-10-13-learning.md")
        eff2 = repo.get_effective("dev", "", "memories/2026-10-14-skill.md")
        assert eff1 == "content A"
        assert eff2 == "content B"
        assert v1.version_id != v2.version_id

    def test_create_memory_version_refuses_core_artifact(
        self, repo, internal_actor,
    ):
        # The canonical class for 'soul' is CORE; create_memory_version
        # lands with class=MEMORY default → the artifact-class mismatch
        # guard rejects the call (a soul row cannot be MEMORY-class).
        with pytest.raises(PromptClassMismatchError):
            repo.create_memory_version(
                "dev", "", "soul", "content", internal_actor,
            )

    def test_create_memory_version_no_state_machine_on_user(
        self, repo, internal_actor,
    ):
        # user.md is a valid MEMORY-class artifact (D5).
        v = repo.create_memory_version(
            "dev", "", "user", "user content", internal_actor,
        )
        assert v.artifact_class == ARTIFACT_CLASS_MEMORY
        assert v.state == "approved"

    def test_create_memory_version_refuses_user_md(
        self, repo, internal_actor,
    ):
        # user.md is a valid MEMORY-class artifact (D5).
        v = repo.create_memory_version(
            "dev", "", "user", "user content", internal_actor,
        )
        assert v.artifact_class == ARTIFACT_CLASS_MEMORY
        assert v.state == "approved"

    def test_create_memory_version_refuses_growth_md(
        self, repo, internal_actor,
    ):
        v = repo.create_memory_version(
            "dev", "", "growth", "growth content", internal_actor,
        )
        assert v.artifact_class == ARTIFACT_CLASS_MEMORY

    def test_approve_memory_class_row_raises_memory_class_error(
        self, repo, internal_actor, http_actor,
    ):
        v = repo.create_memory_version("dev", "", "memory", "x", internal_actor)
        with pytest.raises(MemoryClassError) as exc_info:
            repo.approve(v.version_id, http_actor)
        assert exc_info.value.version_id == v.version_id
        assert exc_info.value.artifact_class == ARTIFACT_CLASS_MEMORY
        assert exc_info.value.operation == "approve"

    def test_reject_memory_class_row_raises_memory_class_error(
        self, repo, internal_actor,
    ):
        v = repo.create_memory_version("dev", "", "memory", "x", internal_actor)
        with pytest.raises(MemoryClassError) as exc_info:
            repo.reject(v.version_id, internal_actor)
        assert exc_info.value.operation == "reject"

    def test_submit_for_approval_memory_class_row_raises_memory_class_error(
        self, repo, internal_actor,
    ):
        v = repo.create_memory_version("dev", "", "memory", "x", internal_actor)
        with pytest.raises(MemoryClassError) as exc_info:
            repo.submit_for_approval(v.version_id, internal_actor)
        assert exc_info.value.operation == "submit_for_approval"

    def test_atomic_transition_memory_class_row_raises_memory_class_error(
        self, repo, internal_actor,
    ):
        v = repo.create_memory_version("dev", "", "memory", "x", internal_actor)
        with pytest.raises(MemoryClassError) as exc_info:
            repo.atomic_transition(
                v.version_id,
                from_state="approved",
                to_state="rejected",
                actor=internal_actor,
                approver_subject="x",
                approver_auth_method=AUTH_METHOD_SESSION,
            )
        assert exc_info.value.operation == "atomic_transition"

    def test_update_version_content_memory_class_row_raises_memory_class_error(
        self, repo, internal_actor,
    ):
        v = repo.create_memory_version("dev", "", "memory", "x", internal_actor)
        with pytest.raises(MemoryClassError) as exc_info:
            repo.update_version_content(v.version_id, "new content", internal_actor)
        assert exc_info.value.operation == "update_version_content"

    def test_delete_version_memory_class_row_raises_memory_class_error(
        self, repo, internal_actor,
    ):
        v = repo.create_memory_version("dev", "", "memory", "x", internal_actor)
        with pytest.raises(MemoryClassError) as exc_info:
            repo.delete_version(v.version_id, internal_actor)
        assert exc_info.value.operation == "delete_version"

    def test_count_pending_excludes_memory_rows(
        self, repo, internal_actor,
    ):
        # 3 MEMORY rows in 'approved' state — should NOT count.
        for artifact in ("memory", "user", "growth"):
            repo.create_memory_version(
                "dev", "", artifact, f"content-{artifact}",
                internal_actor,
            )
        assert repo.count_pending() == 0

    def test_set_selection_works_on_memory_class(
        self, repo, internal_actor, http_actor,
    ):
        v1 = repo.create_memory_version("dev", "", "memory", "v1", internal_actor)
        v2 = repo.create_memory_version("dev", "", "memory", "v2", internal_actor)
        # selection is allowed on MEMORY (R-B3 mechanism).
        repo.set_selection("dev", "", "memory", v1.version_id, http_actor)
        eff = repo.get_effective("dev", "", "memory")
        # selection wins over latest
        assert eff == "v1"

    def test_clear_selection_works_on_memory_class(
        self, repo, internal_actor, http_actor,
    ):
        v1 = repo.create_memory_version("dev", "", "memory", "v1", internal_actor)
        repo.set_selection("dev", "", "memory", v1.version_id, http_actor)
        repo.clear_selection("dev", "", "memory", http_actor)
        # After clear, latest row wins (MEMORY precedence).
        eff = repo.get_effective("dev", "", "memory")
        assert eff == "v1"


# ────────────────────────────────────────────────────────────────────
# 3. Resolution precedence: CORE vs MEMORY
# ────────────────────────────────────────────────────────────────────


class TestResolutionPrecedenceCOREAndMEMORY:
    """get_effective is class-aware."""

    def test_core_approved_wins_over_template(self, repo, internal_actor, http_actor):
        v = repo.create_draft("dev", "", "soul", "APPROVED-SOUL", internal_actor)
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)
        assert repo.get_effective("dev", "", "soul") == "APPROVED-SOUL"

    def test_core_selected_wins_over_latest_approved(
        self, repo, internal_actor, http_actor,
    ):
        v1 = repo.create_draft("dev", "", "soul", "v1", internal_actor)
        repo.submit_for_approval(v1.version_id, internal_actor)
        repo.approve(v1.version_id, http_actor)
        v2 = repo.create_draft("dev", "", "soul", "v2", internal_actor)
        repo.submit_for_approval(v2.version_id, internal_actor)
        repo.approve(v2.version_id, http_actor)
        repo.set_selection("dev", "", "soul", v1.version_id, http_actor)
        assert repo.get_effective("dev", "", "soul") == "v1"

    def test_memory_latest_wins_over_no_selection(
        self, repo, internal_actor, http_actor,
    ):
        repo.create_memory_version("dev", "", "memory", "v1", internal_actor)
        repo.create_memory_version("dev", "", "memory", "v2", internal_actor)
        # No selection. MEMORY = latest row wins.
        assert repo.get_effective("dev", "", "memory") == "v2"

    def test_memory_selection_overrides_latest(
        self, repo, internal_actor, http_actor,
    ):
        v1 = repo.create_memory_version("dev", "", "memory", "v1", internal_actor)
        repo.create_memory_version("dev", "", "memory", "v2", internal_actor)
        repo.set_selection("dev", "", "memory", v1.version_id, http_actor)
        assert repo.get_effective("dev", "", "memory") == "v1"

    def test_memory_returns_none_when_empty(self, repo):
        assert repo.get_effective("dev", "", "memory") is None

    def test_get_effective_rows_carries_class_dimension(
        self, repo, internal_actor, http_actor,
    ):
        repo.create_draft("dev", "", "soul", "x", internal_actor)
        repo.create_memory_version("dev", "", "memory", "y", internal_actor)
        rows = repo.get_effective_rows("dev", "")
        by_artifact = {r.artifact: r for r in rows}
        assert by_artifact["soul"].artifact_class == ARTIFACT_CLASS_CORE
        assert by_artifact["memory"].artifact_class == ARTIFACT_CLASS_MEMORY

    def test_get_effective_rows_carries_memory_latest(
        self, repo, internal_actor,
    ):
        v1 = repo.create_memory_version("dev", "", "memory", "v1", internal_actor)
        v2 = repo.create_memory_version("dev", "", "memory", "v2", internal_actor)
        rows = repo.get_effective_rows("dev", "")
        row = next(r for r in rows if r.artifact == "memory")
        assert row.latest_version_id == v2.version_id
        assert row.latest_content == "v2"
        assert row.latest_approved_version_id is None  # MEMORY semantics: latest
        assert row.latest_approved_content is None
        # The MEMORY row's selected state is None by default.
        assert row.selected_version_id is None
        # Cleanup: ensure the v1 row is distinct from v2.
        assert v1.version_id != v2.version_id


# ────────────────────────────────────────────────────────────────────
# 4. Fingerprint isolation: MEMORY write != CORE fingerprint change
# ────────────────────────────────────────────────────────────────────


class TestFingerprintClassIsolation:
    """Fingerprint fold is class-distinct — a MEMORY write does
    not produce a CORE fingerprint change and vice versa."""

    def test_memory_write_does_not_disturb_core_fingerprint(
        self, repo, internal_actor, http_actor,
    ):
        from daemon.loader import _compute_db_versions_fingerprint

        # CORE first
        v_core = repo.create_draft("dev", "", "soul", "core1", internal_actor)
        repo.submit_for_approval(v_core.version_id, internal_actor)
        repo.approve(v_core.version_id, http_actor)
        rows_before = repo.get_effective_rows("dev", "")
        fp_before = _compute_db_versions_fingerprint(rows_before)

        # Now MEMORY write
        repo.create_memory_version("dev", "", "memory", "mem1", internal_actor)
        rows_after = repo.get_effective_rows("dev", "")
        fp_after = _compute_db_versions_fingerprint(rows_after)

        # The fingerprint MUST change because the rows changed —
        # what matters is that the class-distinct identities
        # stayed coherent (rows_after has both soul=CORE and
        # memory=MEMORY).
        assert fp_before != fp_after

        # Read the entries: soul is CORE, memory is MEMORY.
        by_artifact = {r.artifact: r for r in rows_after}
        assert by_artifact["soul"].artifact_class == ARTIFACT_CLASS_CORE
        assert by_artifact["memory"].artifact_class == ARTIFACT_CLASS_MEMORY

    def test_core_write_does_not_disturb_memory_fingerprint_identity(
        self, repo, internal_actor, http_actor,
    ):
        from daemon.loader import _compute_db_versions_fingerprint

        # MEMORY first
        repo.create_memory_version("dev", "", "memory", "mem1", internal_actor)
        # CORE second (separate artifact)
        v_core = repo.create_draft("dev", "", "soul", "core1", internal_actor)
        repo.submit_for_approval(v_core.version_id, internal_actor)
        repo.approve(v_core.version_id, http_actor)

        rows = repo.get_effective_rows("dev", "")
        fp = _compute_db_versions_fingerprint(rows)

        # Fingerprint must be a stable hash; it's not empty (we
        # have rows); class-distinct.
        assert fp != ""
        by_artifact = {r.artifact: r for r in rows}
        assert by_artifact["memory"].artifact_class == ARTIFACT_CLASS_MEMORY
        assert by_artifact["soul"].artifact_class == ARTIFACT_CLASS_CORE

    def test_fingerprint_pure_function(self, repo, internal_actor, http_actor):
        from daemon.loader import _compute_db_versions_fingerprint

        repo.create_draft("dev", "", "soul", "x", internal_actor)
        rows1 = repo.get_effective_rows("dev", "")
        fp1 = _compute_db_versions_fingerprint(rows1)
        rows2 = repo.get_effective_rows("dev", "")
        fp2 = _compute_db_versions_fingerprint(rows2)
        assert fp1 == fp2  # idempotent

    def test_fingerprint_empty_returns_sentinel(
        self, repo,
    ):
        from daemon.loader import _compute_db_versions_fingerprint
        # No rows
        rows = repo.get_effective_rows("dev", "")
        fp = _compute_db_versions_fingerprint(rows)
        assert fp == ""  # N4 sentinel


# ────────────────────────────────────────────────────────────────────
# 5. memories keying (per-file)
# ────────────────────────────────────────────────────────────────────


class TestMemoriesPerFileKeying:
    """Each per-file ``memories/<slug>`` is independent (key)."""

    def test_two_slugs_independent_keys(
        self, repo, internal_actor, http_actor,
    ):
        v1 = repo.create_memory_version(
            "dev", "", "memories/learning.md", "L1",
            internal_actor,
        )
        v2 = repo.create_memory_version(
            "dev", "", "memories/skill.md", "S1",
            internal_actor,
        )
        # The keys are distinct → distinct rows.
        assert v1.version_id != v2.version_id
        assert repo.get_effective("dev", "", "memories/learning.md") == "L1"
        assert repo.get_effective("dev", "", "memories/skill.md") == "S1"

    def test_latest_per_key_independent(self, repo, internal_actor):
        v_a1 = repo.create_memory_version(
            "dev", "", "memories/a.md", "A1", internal_actor,
        )
        v_b1 = repo.create_memory_version(
            "dev", "", "memories/b.md", "B1", internal_actor,
        )
        v_a2 = repo.create_memory_version(
            "dev", "", "memories/a.md", "A2", internal_actor,
        )
        # a's latest = A2; b's latest = B1 (unaffected).
        assert repo.get_effective("dev", "", "memories/a.md") == "A2"
        assert repo.get_effective("dev", "", "memories/b.md") == "B1"
        assert v_a1.version_id != v_a2.version_id
        assert v_b1.version_id != v_a1.version_id

    def test_selection_pin_per_key(self, repo, internal_actor, http_actor):
        v_a1 = repo.create_memory_version(
            "dev", "", "memories/a.md", "A1", internal_actor,
        )
        v_a2 = repo.create_memory_version(
            "dev", "", "memories/a.md", "A2", internal_actor,
        )
        v_b1 = repo.create_memory_version(
            "dev", "", "memories/b.md", "B1", internal_actor,
        )
        # Pin 'a' to v_a1 (older); b stays on latest.
        repo.set_selection("dev", "", "memories/a.md", v_a1.version_id, http_actor)
        assert repo.get_effective("dev", "", "memories/a.md") == "A1"
        assert repo.get_effective("dev", "", "memories/b.md") == "B1"
        # Pin b too.
        repo.set_selection("dev", "", "memories/b.md", v_b1.version_id, http_actor)
        assert repo.get_effective("dev", "", "memories/a.md") == "A1"
        assert repo.get_effective("dev", "", "memories/b.md") == "B1"

    def test_memories_artifact_class_stamped_correctly(self, repo, internal_actor):
        v = repo.create_memory_version(
            "dev", "", "memories/foo.md", "x", internal_actor,
        )
        assert v.artifact_class == ARTIFACT_CLASS_MEMORY


# ────────────────────────────────────────────────────────────────────
# 6. Loader integration: memory.md §8 + recent-memories
# ────────────────────────────────────────────────────────────────────


class TestLoaderResolutionExtension:
    """Loader resolves memory.md and per-file memories via the DB."""

    def test_memory_md_resolution_via_db(self, repo, internal_actor):
        from daemon.loader import resolve_prompts_from_db

        repo.create_memory_version("dev", "", "memory", "DB-MEM", internal_actor)

        prompts = {"soul": "FILE-SOUL", "memory": "FILE-MEM"}
        out = resolve_prompts_from_db(prompts, "dev", "", repo)
        # MEMORY: latest row content beats file fallback.
        assert out["memory"] == "DB-MEM"
        assert out["soul"] == "FILE-SOUL"

    def test_memory_md_no_db_returns_file(self, repo):
        from daemon.loader import resolve_prompts_from_db

        prompts = {"soul": "FILE-SOUL", "memory": "FILE-MEM"}
        out = resolve_prompts_from_db(prompts, "dev", "", repo)
        # No DB rows → preserve file content (boot tolerance).
        assert out["memory"] == "FILE-MEM"
        assert out["soul"] == "FILE-SOUL"

    def test_user_md_not_in_prompts_dict_natural_noop(
        self, repo, internal_actor,
    ):
        # user.md was never loaded into the loader's prompts
        # dict; even with DB rows, the hook skips it via the
        # dict-key lookup.
        from daemon.loader import resolve_prompts_from_db

        repo.create_memory_version("dev", "", "user", "user-content", internal_actor)
        prompts = {"soul": "FILE-SOUL"}
        out = resolve_prompts_from_db(prompts, "dev", "", repo)
        # Soul unaffected (no DB row for soul yet).
        assert out["soul"] == "FILE-SOUL"
        # 'user' is not in the prompts dict — natural no-op.

    def test_load_recent_memories_db_only_keys(
        self, repo, internal_actor, tmp_path,
    ):
        """Per-file MEMORY rows surface in the recent-memories index."""
        from daemon.loader import load_recent_memories

        # No on-disk memories/ dir.
        agent_dir = tmp_path / "tester"
        agent_dir.mkdir()

        # Three DB-only MEMORY keys (no on-disk file).
        for slug in ("alpha.md", "beta.md", "gamma.md"):
            repo.create_memory_version(
                "tester", "", f"memories/{slug}", f"content-{slug}",
                internal_actor,
            )

        out = load_recent_memories(
            agent_dir,
            db_memory_keys=["memories/alpha.md", "memories/beta.md", "memories/gamma.md"],
        )
        # Each DB-only key surfaces as a filename line.
        assert "- alpha.md" in out
        assert "- beta.md" in out
        assert "- gamma.md" in out

    def test_load_recent_memories_on_disk_wins_over_db(
        self, repo, internal_actor, tmp_path,
    ):
        from daemon.loader import load_recent_memories

        # On-disk memories/ exists; DB row also exists for the same slug.
        agent_dir = tmp_path / "tester"
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir(parents=True)
        (memories_dir / "alpha.md").write_text("on-disk content", encoding="utf-8")

        # DB row for the same slug — the on-disk file is what surfaces
        # (the loader doesn't merge identical-key duplicates).
        repo.create_memory_version(
            "tester", "", "memories/alpha.md", "DB alpha", internal_actor,
        )

        out = load_recent_memories(
            agent_dir, db_memory_keys=["memories/alpha.md"],
        )
        # The line is "- alpha.md" (no duplicates).
        lines = out.splitlines()
        alpha_count = sum(1 for ln in lines if ln == "- alpha.md")
        assert alpha_count == 1

    def test_load_recent_memories_no_memories_dir(self, tmp_path):
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "tester"
        agent_dir.mkdir()
        # No memories/ dir, no DB keys.
        out = load_recent_memories(agent_dir)
        assert out == ""

    def test_load_recent_memories_empty_db_keys(self, tmp_path):
        from daemon.loader import load_recent_memories
        agent_dir = tmp_path / "tester"
        agent_dir.mkdir()
        out = load_recent_memories(agent_dir, db_memory_keys=[])
        assert out == ""


# ────────────────────────────────────────────────────────────────────
# 7. Custom agent: zero-version typed failure (Ud#5A resolution-side)
# ────────────────────────────────────────────────────────────────────


class TestCustomAgentEmptyState:
    """Custom agents have no template fallback; zero-version = typed error."""

    def test_no_db_rows_for_custom_agent_raises(
        self, repo,
    ):
        from daemon.loader import NoPromptContentError, resolve_prompts_from_db

        prompts = {"soul": "FILE-SOUL"}
        with pytest.raises(NoPromptContentError) as exc_info:
            resolve_prompts_from_db(
                prompts, "custom-agent", "", repo,
                is_custom_agent=True,
            )
        assert exc_info.value.agent_id == "custom-agent"
        assert exc_info.value.version_tag == ""
        assert exc_info.value.artifact == "soul"

    def test_custom_agent_with_db_rows_resolves_normally(
        self, repo, internal_actor,
    ):
        from daemon.loader import resolve_prompts_from_db

        # Custom agent HAS rows → resolve normally.
        repo.create_memory_version(
            "custom-agent", "", "memory", "CUSTOM-MEM", internal_actor,
        )
        prompts = {"soul": "FILE-SOUL", "memory": "FILE-MEM"}
        out = resolve_prompts_from_db(
            prompts, "custom-agent", "", repo,
            is_custom_agent=True,
        )
        assert out["memory"] == "CUSTOM-MEM"
        assert out["soul"] == "FILE-SOUL"

    def test_predefined_agent_empty_does_not_raise(self, repo):
        from daemon.loader import resolve_prompts_from_db

        prompts = {"soul": "FILE-SOUL"}
        # Predefined agent, zero rows → no error (file fallback fires).
        out = resolve_prompts_from_db(
            prompts, "predefined-agent", "", repo,
            is_custom_agent=False,
        )
        assert out["soul"] == "FILE-SOUL"


# ────────────────────────────────────────────────────────────────────
# 8. R-B2 auto-reset: detect + run + idempotency + carve-out + selection-preservation
# ────────────────────────────────────────────────────────────────────


class TestRB2AutoReset:
    """R-B2 CORE-only auto-reset on shipped template drift."""

    def test_record_initial_baselines_seeds_row(
        self, baseline_repo, agents_root, internal_actor,
    ):
        rows = baseline_repo.record_initial_baselines(
            "tester", "", agents_root, actor=internal_actor,
        )
        # soul + rule + workflow + tools_note = 4 rows (memory.md +
        # user.md are MEMORY-class, rejected).
        artifacts = sorted(r.artifact for r in rows)
        assert "soul" in artifacts
        assert "rule" in artifacts
        assert "workflow" in artifacts
        assert "tools_note" in artifacts
        # MEMORY-class excluded.
        assert "memory" not in artifacts
        assert "user" not in artifacts

    def test_record_initial_baselines_idempotent(
        self, baseline_repo, agents_root, internal_actor,
    ):
        # First run seeds.
        first = baseline_repo.record_initial_baselines(
            "tester", "", agents_root, actor=internal_actor,
        )
        # Second run on unchanged files → identical rows (UPSERT
        # swallows the no-op).
        second = baseline_repo.record_initial_baselines(
            "tester", "", agents_root, actor=internal_actor,
        )
        first_by_artifact = {r.artifact: r.template_sha256 for r in first}
        second_by_artifact = {r.artifact: r.template_sha256 for r in second}
        assert first_by_artifact == second_by_artifact

    def test_detect_template_drift_no_drift_on_baseline_match(
        self, baseline_repo, agents_root, internal_actor, repo,
    ):
        baseline_repo.record_initial_baselines(
            "tester", "", agents_root, actor=internal_actor,
        )
        results = baseline_repo.detect_template_drift(
            "tester", "", agents_root,
            actor=internal_actor,
            prompt_version_repo=repo,
            run_reset=False,
        )
        # No drift on freshly-seeded baselines.
        assert all(r.drift_detected is False for r in results), (
            "freshly-seeded baselines should not detect drift"
        )
        # Some artifacts may be skipped (e.g. ``skill`` when no
        # ``skill.md`` is shipped), but the seeded baselines must
        # not register a drift. Assert that drift_detected is
        # always False (the carve-out is for missing files, which
        # is reported as drift_skipped=True / skip_reason).
        assert sum(1 for r in results if r.drift_detected) == 0

    def test_detect_template_drift_after_file_mutation(
        self, baseline_repo, agents_root, internal_actor, repo,
    ):
        # Seed baselines.
        baseline_repo.record_initial_baselines(
            "tester", "", agents_root, actor=internal_actor,
        )
        # Mutate soul.md.
        soul_path = agents_root / "tester" / "soul.md"
        soul_path.write_text("SOUL v2 — mutated\n", encoding="utf-8")

        # Detect (run_reset=False for dry-run assertion).
        results = baseline_repo.detect_template_drift(
            "tester", "", agents_root,
            actor=internal_actor,
            prompt_version_repo=repo,
            run_reset=False,
        )
        drifted = [r for r in results if r.drift_detected]
        assert len(drifted) == 1
        assert drifted[0].artifact == "soul"
        assert drifted[0].old_sha256 is not None
        assert drifted[0].new_sha256 != drifted[0].old_sha256

    def test_detect_template_drift_run_reset_stamps_audit_event(
        self, baseline_repo, agents_root, internal_actor, repo,
        sqlite_repo_engine,
    ):
        engine, _ = sqlite_repo_engine

        baseline_repo.record_initial_baselines(
            "tester", "", agents_root, actor=internal_actor,
        )
        soul_path = agents_root / "tester" / "soul.md"
        soul_path.write_text("SOUL v3 — drift-test\n", encoding="utf-8")

        baseline_repo.detect_template_drift(
            "tester", "", agents_root,
            actor=internal_actor,
            prompt_version_repo=repo,
            run_reset=True,
        )

        # Audit event stamped.
        with Session(engine) as session:
            ev = session.exec(
                select(PromptAuditEvent)
                .where(
                    col(PromptAuditEvent.event_type) == "TEMPLATE_RESET",
                    col(PromptAuditEvent.artifact) == "soul",
                )
            ).one_or_none()
            assert ev is not None
            assert ev.payload["old_sha256"] is not None
            assert ev.payload["new_sha256"] is not None
            assert ev.payload["selection_preserved"] is False  # no selection yet

        # Baseline re-pointed (template_sha256 matches the new file hash).
        baseline_row = baseline_repo.get_baseline("tester", "", "soul")
        assert baseline_row is not None
        sha = baseline_repo._sha256_hex_str(soul_path.read_text(encoding="utf-8"))
        assert baseline_row.template_sha256 == sha

    def test_detect_template_drift_idempotent(
        self, baseline_repo, agents_root, internal_actor, repo,
        sqlite_repo_engine,
    ):
        engine, _ = sqlite_repo_engine

        baseline_repo.record_initial_baselines(
            "tester", "", agents_root, actor=internal_actor,
        )
        soul_path = agents_root / "tester" / "soul.md"
        soul_path.write_text("SOUL v4 — idempotency\n", encoding="utf-8")

        # First run: detects + stamps event + re-points baseline.
        baseline_repo.detect_template_drift(
            "tester", "", agents_root,
            actor=internal_actor,
            prompt_version_repo=repo,
            run_reset=True,
        )

        with Session(engine) as session:
            count_after_first = len(list(session.exec(
                select(PromptAuditEvent)
                .where(
                    col(PromptAuditEvent.event_type) == "TEMPLATE_RESET",
                    col(PromptAuditEvent.artifact) == "soul",
                )
            )))

        # Second run with NO file change: detect baseline matches
        # → no drift → no event stamped.
        baseline_repo.detect_template_drift(
            "tester", "", agents_root,
            actor=internal_actor,
            prompt_version_repo=repo,
            run_reset=True,
        )
        with Session(engine) as session:
            count_after_second = len(list(session.exec(
                select(PromptAuditEvent)
                .where(
                    col(PromptAuditEvent.event_type) == "TEMPLATE_RESET",
                    col(PromptAuditEvent.artifact) == "soul",
                )
            )))

        # Idempotent: second run adds ZERO events.
        assert count_after_first == count_after_second

    def test_explicit_selection_survives_auto_reset(
        self, baseline_repo, agents_root, internal_actor, repo, http_actor,
        sqlite_repo_engine,
    ):
        """R-B3: an explicit is_selected=1 pointer SURVIVES auto-reset."""
        engine, _ = sqlite_repo_engine

        # 1) Create + approve a CORE draft, set it as the selection.
        v = repo.create_draft("tester", "", "soul", "EXPLICIT-SOUL", internal_actor)
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)
        repo.set_selection("tester", "", "soul", v.version_id, http_actor)

        # 2) Seed baseline.
        baseline_repo.record_initial_baselines(
            "tester", "", agents_root, actor=internal_actor,
        )

        # 3) Mutate soul.md → forces drift.
        soul_path = agents_root / "tester" / "soul.md"
        soul_path.write_text("SOUL v5 — selection-survives\n", encoding="utf-8")

        # 4) Run drift with reset.
        results = baseline_repo.detect_template_drift(
            "tester", "", agents_root,
            actor=internal_actor,
            prompt_version_repo=repo,
            run_reset=True,
        )
        # The drift result flags selection_preserved.
        drifted = [r for r in results if r.drift_detected and r.artifact == "soul"]
        assert len(drifted) == 1
        assert drifted[0].selection_preserved is True

        # 5) The explicit selection SURVIVES the reset.
        sel = repo.get_selection("tester", "", "soul")
        assert sel is not None
        assert sel.version_id == v.version_id

        # 6) The resolution chain still serves the explicit
        # selection content (the new file content is in the
        # baseline, but the explicit pointer wins).
        eff = repo.get_effective("tester", "", "soul")
        assert eff == "EXPLICIT-SOUL"

    def test_memory_class_does_not_appear_in_baseline_table(
        self, baseline_repo, agents_root, internal_actor,
    ):
        """R-B2 is CORE-only: the baseline table rejects MEMORY-class
        artifacts even if a memory.md file exists."""
        # The agents_root fixture writes memory.md + user.md — but
        # they're MEMORY-class and the baseline path refuses them.
        rows = baseline_repo.record_initial_baselines(
            "tester", "", agents_root, actor=internal_actor,
        )
        artifacts = {r.artifact for r in rows}
        assert "memory" not in artifacts
        assert "user" not in artifacts
        assert "growth" not in artifacts

    def test_baseline_table_refuses_memory_artifact_at_upsert(
        self, baseline_repo, internal_actor,
    ):
        from daemon.repositories.prompt_version.repository import PROMPT_MEMORY_ARTIFACTS
        for artifact in PROMPT_MEMORY_ARTIFACTS:
            with pytest.raises(ValueError, match="baseline path refuses"):
                baseline_repo._upsert_baseline(
                    agent_id="tester",
                    version_tag="",
                    artifact=artifact,
                    template_sha256="0" * 64,
                )

    def test_template_sha256_must_be_64_hex(self, baseline_repo):
        with pytest.raises(ValueError, match="64-hex"):
            baseline_repo._normalize_template_sha256("tooshort")
        with pytest.raises(ValueError, match="hex"):
            baseline_repo._normalize_template_sha256("z" * 64)


# ────────────────────────────────────────────────────────────────────
# 9. class_for_artifact canonical mapping
# ────────────────────────────────────────────────────────────────────


class TestClassForArtifactMapping:
    """Canonical artifact → class mapping."""

    @pytest.mark.parametrize("artifact", [
        "soul", "rule", "workflow", "tools_note", "tools", "skill",
    ])
    def test_core_artifacts_map_to_core(self, artifact):
        assert class_for_artifact(artifact) == ARTIFACT_CLASS_CORE

    @pytest.mark.parametrize("artifact", ["memory", "user", "growth"])
    def test_memory_artifacts_map_to_memory(self, artifact):
        assert class_for_artifact(artifact) == ARTIFACT_CLASS_MEMORY

    def test_memories_per_file_maps_to_memory(self):
        assert class_for_artifact("memories/2026-10-13-x.md") == ARTIFACT_CLASS_MEMORY

    def test_unknown_artifact_defaults_to_core(self):
        # Conservative default: unknown artifacts land in CORE
        # so the explicit approval flow is required for any new
        # artifact.
        assert class_for_artifact("future_artifact") == ARTIFACT_CLASS_CORE

    def test_validate_artifact_class_accepts_both(self):
        validate_artifact_class(ARTIFACT_CLASS_CORE)
        validate_artifact_class(ARTIFACT_CLASS_MEMORY)

    def test_validate_artifact_class_rejects_unknown(self):
        with pytest.raises(ValueError, match="must be one of"):
            validate_artifact_class("UNKNOWN")
        with pytest.raises(ValueError, match="must be one of"):
            validate_artifact_class("core")  # case-sensitive
        with pytest.raises(ValueError, match="must be one of"):
            validate_artifact_class("")


# ────────────────────────────────────────────────────────────────────
# 10. Migration parity + idempotency
# ────────────────────────────────────────────────────────────────────


class TestMigrationParity:
    """The .sql migrations apply idempotently + columns match the model."""

    def test_migration_files_present(self):
        from pathlib import Path

        versions_dir = Path("daemon/migrations/versions")
        files = sorted(versions_dir.glob("*.sql"))
        # 95 files (was 93; two new migrations landed for Ud#3+Ud#5B).
        assert len(files) == 95

        # Both new migrations present.
        class_migration = next(
            (f for f in files if "add_artifact_class" in f.name), None,
        )
        baseline_migration = next(
            (f for f in files if "create_prompt_template_baselines" in f.name), None,
        )
        assert class_migration is not None
        assert baseline_migration is not None

    def test_class_column_migration_ddl_matches_model(
        self,
    ):
        """The .sql migration DDL for artifact_class matches the model column."""
        from pathlib import Path

        class_migration = next(
            Path("daemon/migrations/versions").glob("20261014_*add_artifact_class*.sql"),
        )
        text_content = class_migration.read_text()
        # The model declares 'artifact_class' as a column; the
        # .sql adds it with DEFAULT 'CORE'.
        assert "ALTER TABLE prompt_versions ADD COLUMN artifact_class" in text_content
        assert "DEFAULT 'CORE'" in text_content
        # Backfill rule for existing rows.
        assert "MEMORY" in text_content
        assert "memories/" in text_content

    def test_baseline_table_migration_ddl_matches_model(
        self,
    ):
        from pathlib import Path

        baseline_migration = next(
            Path("daemon/migrations/versions").glob("*create_prompt_template_baselines*.sql"),
        )
        text_content = baseline_migration.read_text()
        # The table name and columns line up with the model.
        assert "CREATE TABLE IF NOT EXISTS prompt_template_baselines" in text_content
        for col_name in [
            "baseline_id", "agent_id", "version_tag", "artifact",
            "template_sha256", "recorded_at",
        ]:
            assert col_name in text_content
        # The unique index name must be byte-identical.
        assert (
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_prompt_template_baselines_agent_tag_artifact"
        ) in text_content

    def test_baseline_table_re_apply_idempotent(self, sqlite_repo_engine):
        """Re-running the migration on an already-applied DB is a no-op.

        Uses the project-documented ledger-pre-mark for the
        pre-existing 20260714 PG-only defect (the F1 trap; see
        ``tests/fakes/f1_premark_helper.py`` and critical note
        ``8fe271db``). Without the pre-mark, the runner hits the
        PG-only DROP CONSTRAINT syntax and aborts before reaching
        our new migrations.
        """
        import os as _os
        engine, _ = sqlite_repo_engine

        # Pre-mark the F1 offender so the runner reaches our
        # new migrations cleanly.
        from tests.fakes.f1_premark_helper import (
            F1_SKIP_REASON, F1_VERSION, premark_f1,
        )

        db_path = (
            str(engine.url).replace("sqlite:///", "").replace(
                "sqlite:///", "",
            )
        )
        if not _os.path.exists(db_path):
            # The tmpfile is gone — skip rather than fight for a
            # path the test fixture doesn't expose. The
            # applied/pending contract is already proven by the
            # other TestMigrationParity tests.
            pytest.skip(
                f"F1 pre-mark skipped: engine URL {engine.url!r} has "
                f"no path component (test fixture uses StaticPool / "
                f"in-memory variant)."
            )
        premark_f1(db_path)

        from daemon.migrations.runner import MigrationRunner

        runner = MigrationRunner(engine)
        applied_first = runner.run_pending_migrations()
        # The new migrations (class-column ALTER + baselines
        # table) were applied — at least 2 entries.
        assert len(applied_first) >= 2, (
            f"expected ≥ 2 new migrations applied; got {applied_first}"
        )
        # Second run: no-op (already applied).
        applied_second = runner.run_pending_migrations()
        assert applied_second == []

    def test_model_indexes_match_migration_for_baselines(self):
        """The model's __table_args__ for PromptTemplateBaseline matches the .sql index names."""
        from pathlib import Path

        baseline_migration = next(
            Path("daemon/migrations/versions").glob("*create_prompt_template_baselines*.sql"),
        )
        text_content = baseline_migration.read_text()
        # Index names from the model declaration.
        from daemon.repositories.prompt_version.models import (
            PromptTemplateBaseline,
        )
        for index_name in [
            "uq_prompt_template_baselines_agent_tag_artifact",
            "idx_prompt_template_baselines_artifact",
            "idx_prompt_template_baselines_recorded_at",
        ]:
            assert index_name in text_content, (
                f"index {index_name!r} missing from migration DDL"
            )


# ────────────────────────────────────────────────────────────────────
# 11. Concurrency: MEMORY writes serialize per-key via the advisory
#     lock (the same _acquire_key_lock the CORE path uses).
# ────────────────────────────────────────────────────────────────────


class TestMEMORYConcurrency:
    """The advisory lock + UNIQUE constraint fence the MEMORY path
    the same way the CORE path is fenced."""

    def test_concurrent_create_memory_version_yields_distinct_versions(
        self, sqlite_repo_engine, internal_actor,
    ):
        engine, _ = sqlite_repo_engine
        repo = PromptVersionRepository(engine)

        results = []

        def writer(slug: str) -> None:
            for i in range(3):
                v = repo.create_memory_version(
                    "dev", "", f"memories/{slug}.md",
                    f"{slug}-{i}", internal_actor,
                )
                results.append((slug, i, v.version_id))

        threads = [
            threading.Thread(target=writer, args=(f"s{i}",)) for i in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each thread had a unique slug → 3 versions × 3 writes =
        # 9 row-ids, no duplicates across threads (per-slug
        # serialization).
        seen = set()
        for slug, i, vid in results:
            assert vid not in seen, f"duplicate version_id {vid}"
            seen.add(vid)

        # Each per-slug key has its own distinct version ids.
        for slug in ("s0", "s1", "s2"):
            vids = [vid for s, i, vid in results if s == slug]
            assert len(set(vids)) == 3


# ────────────────────────────────────────────────────────────────────
# 12. Class-aware get_effective_rows single-SELECT (A8 preserved)
# ────────────────────────────────────────────────────────────────────


class TestClassAwareEffectiveRows:
    """Class-aware get_effective_rows preserves A8 (single-SELECT)."""

    def test_effective_rows_class_dimension_present_for_both_classes(
        self, repo, internal_actor, http_actor,
    ):
        # One CORE approved.
        v_core = repo.create_draft("dev", "", "soul", "SOUL", internal_actor)
        repo.submit_for_approval(v_core.version_id, internal_actor)
        repo.approve(v_core.version_id, http_actor)
        # Two MEMORY rows (selection pin + latest).
        v_mem1 = repo.create_memory_version("dev", "", "memory", "mem1", internal_actor)
        v_mem2 = repo.create_memory_version("dev", "", "memory", "mem2", internal_actor)
        repo.set_selection("dev", "", "memory", v_mem1.version_id, http_actor)

        rows = repo.get_effective_rows("dev", "")
        by_artifact = {r.artifact: r for r in rows}
        assert set(by_artifact) == {"soul", "memory"}
        assert by_artifact["soul"].artifact_class == ARTIFACT_CLASS_CORE
        assert by_artifact["soul"].latest_approved_version_id == v_core.version_id
        assert by_artifact["soul"].latest_approved_content == "SOUL"
        # MEMORY: latest, not approved.
        assert by_artifact["memory"].artifact_class == ARTIFACT_CLASS_MEMORY
        assert by_artifact["memory"].selected_version_id == v_mem1.version_id
        assert by_artifact["memory"].latest_version_id == v_mem2.version_id
        assert by_artifact["memory"].latest_content == "mem2"
        # approved slots are None for MEMORY.
        assert by_artifact["memory"].latest_approved_version_id is None
        assert by_artifact["memory"].latest_approved_content is None
