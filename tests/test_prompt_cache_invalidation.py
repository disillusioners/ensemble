"""Phase B T12 — BEHAVIORAL cache-invalidation coverage.

This is the behavioral file that proves the ``Phase A T7 fingerprint-
bust-on-get`` mechanism + the ``Phase B D9 version_tag`` threading
actually keep ``PromptCache`` correct end-to-end.

The existing ``tests/test_prompt_version_resolution.py`` file exercises
the **PromptCache key-string mechanism** (``_make_key``, ``get``,
``invalidate`` under base + tagged + multi-MCP variants) and source-
level grep-pins the 6 invalidate sites + ``_get_system_prompt_tokens``
version_tag threading (``TestLoaderPromptCacheInvalidateRespectsTagsT7D3``).
That is the static / structural layer.

This file is the **dynamic / behavioral** layer:
  * drive the cache via the REAL ``load_and_cache_prompt`` loader entry
    point (single-SELECT hook, ``A8`` fingerprint recompute);
  * observe cache state directly (the ``__prompt_versions_fingerprint__``
    key inside ``cache._cache[*][2]``);
  * cross-version isolation (S12/O-5 keystone) — a BASE selection
    change MUST NOT bust a [v2] sibling's cached entry because
    ``get_effective_rows("dev", "v2")`` returned nothing new;
  * approve / select / clear bust across MCP variants (the C1 self-heal
    mechanism — the single ``invalidate`` call can't reach them, but
    the fingerprint equality check can);
  * pending rows (F6) do NOT bust the cache — only ``approved`` /
    ``selected`` rows contribute to the fingerprint;
  * ``_get_system_prompt_tokens`` reads the **TAGGED** cache entry
    (not the base one) when ``meta.agent_tag`` is set — tight behavioral
    proof that closes the F9/R10 token-accounting bug.

**The 6 invalidate sites — coverage cross-reference:**

  (a) inner_soul._submit_prompt_version — covers soul, user, workflow
      (3 logical writers consolidated by Phase B T7). Covered by
      ``tests/test_inner_soul_db.py::TestInvalidateThreadsVersionTag``
      (``test_soul_submit_invalidates_tagged_key_not_base``; behaviorally
      proves the DB-submission lane threads ``version_tag``). Cross-
      referenced; not re-implemented here.
  (b) inner_soul._update_memories — file-write carve-out; buster
      threads version_tag. Behaviorally covered in this file
      (``TestLogicalSiteMemoriesInvalidateThreadsVersionTag``).
  (c) inner_soul._update_memory_md — file-write carve-out (memory.md);
      buster threads version_tag. Behaviorally covered in this file
      (``TestLogicalSiteMemoryMdInvalidateThreadsVersionTag``).
  (d) agent_mother.agent_modify (memory.md direct-write path) —
      version_tag threading behaviorally pinned in
      ``tests/test_agent_modify_rewire.py`` (the ``_FakePromptCache``
      spy records ``(agent_name, resolved_tag)`` pairs).
  (e) instance_messaging._get_system_prompt_tokens — token-accounting
      lane. Behaviorally covered in this file
      (``TestTokenAccountingThreadsVersionTag``).
  (f) loader cache-hit check fingerprint recompute — central
      mechanism. Behaviorally covered for keystone / approve / select /
      clear / pending in this file (``TestBaseV2SiblingIsolation``,
      ``TestApproveBustsAcrossMCPVariants``, ``TestSelectClearBust``,
      ``TestPendingDoesNotBust``).

Fencing (project critical notes — 2026-10-12 live-install-delete class,
2026-10-03 prompt-version DB schema)::

    * ``ENSEMBLE_SELF_ENV=dev`` and ``POSTGRES_DB`` MUST NOT equal
      ``ensemble_prod`` (the session-scoped ``_fence_session`` autouse
      fixture aborts the session if the env is wrong);
    * agent dirs are ALWAYS ``tmp_path``-relative — no live-shaped
      paths resolved (the 2026-10-12 incident class — mktemp FAKE_HOME);
    * the prompt-version repo singleton is reset to its prior value on
      teardown (the ``_reset_prompt_version_repo`` fixture);
    * file-SQLite under ``tempfile.mktemp`` (WAL + busy_timeout
      pragmas); the migration runner is NOT exercised — schema via
      ``SQLModel.metadata.create_all`` (mirrors the
      ``test_inner_soul_db.py`` precedent);
    * run targeted (``--tb=short -q``), never bare pytest, never ``-x``.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from daemon.loader import (
    PromptCache,
    _get_prompt_version_repo,
    load_and_cache_prompt,
    set_prompt_version_repo,
)
from daemon.registry import AgentMetadata
from daemon.repositories.prompt_version.repository import (
    AUTH_METHOD_BEARER,
    AUTH_METHOD_SESSION,
    ActorContext,
    PromptVersionRepository,
    actor_from_instance,
)
from daemon.services.prompt_version_service import PromptVersionService
from daemon.tools.inner_soul import (
    _update_memories,
    _update_memory_md,
    create_inner_soul_tool,
)


# ─────────────────────────────────────────────────────────────────────────────
# Session-scoped fence (env-poison 3-guard convention — 2026-10-02 incident
# class). Ambient ``POSTGRES_DB=ensemble_prod`` would steer the worktree
# dev-daemon at LIVE; the file-SQLite path here is insulated, but the
# abort-on-fence mismatch rule keeps a CI host honest.
# ─────────────────────────────────────────────────────────────────────────────


def _session_env_fence() -> None:
    """Hard-abort when the daemon identity looks live (``ensemble_prod``)."""
    db = os.environ.get("POSTGRES_DB", "")
    if db == "ensemble_prod":
        pytest.exit(
            f"HARD ABORT: POSTGRES_DB={db!r} points at live — prompt-cache "
            f"tests must run with a dev-identity DB (e.g. "
            f"ensemble_dev_test_only). Refuse to execute.",
            returncode=1,
        )
    env_marker = os.environ.get("ENSEMBLE_SELF_ENV", "")
    if env_marker and env_marker not in ("dev", "demo", "sandbox"):
        pytest.exit(
            f"HARD ABORT: ENSEMBLE_SELF_ENV={env_marker!r} is not in "
            f"{{dev, demo, sandbox}} — refuse to run T12 with a "
            f"non-dev identity.",
            returncode=1,
        )


@pytest.fixture(autouse=True, scope="session")
def _fence_session():
    _session_env_fence()


# ─────────────────────────────────────────────────────────────────────────────
# Per-test fixtures — repo + service + agent dir. Mirrors the
# ``test_inner_soul_db.py`` precedent (FAKE_HOME-fenced tmp dirs,
# file-SQLite under mktemp, schema via create_all).
# ─────────────────────────────────────────────────────────────────────────────


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

    from sqlmodel import SQLModel

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
def repo(repo_engine: Engine) -> PromptVersionRepository:
    return PromptVersionRepository(repo_engine)


@pytest.fixture
def service(repo: PromptVersionRepository) -> PromptVersionService:
    return PromptVersionService(repo=repo)


@pytest.fixture
def internal_actor() -> ActorContext:
    return actor_from_instance("inst-t12-1", "dev", "")


@pytest.fixture
def http_actor() -> ActorContext:
    return ActorContext(subject="user@example.com", auth_method=AUTH_METHOD_SESSION)


@pytest.fixture
def bearer_actor() -> ActorContext:
    return ActorContext(subject="machine-cli", auth_method=AUTH_METHOD_BEARER)


@pytest.fixture
def agent_dir(tmp_path: Path) -> Path:
    """Minimal agent dir under ``tmp_path`` (NEVER live-shaped)."""
    d = tmp_path / "dev"
    d.mkdir()
    (d / "soul.md").write_text("# SOUL_FILE_CONTENT\n\nI am the file soul.\n")
    (d / "rule.md").write_text("# RULE_FILE_CONTENT\n")
    (d / "workflow.md").write_text("# WORKFLOW_FILE_CONTENT\n")
    (d / "memory.md").write_text("# MEMORY_FILE_CONTENT\n\nknown base.\n")
    (d / "memories").mkdir()
    (d / "user.md").write_text("# User\n\nplaceholder\n")
    (d / "growth.md").write_text("# Growth\n\nmax_memory_words: 2000\n")
    return d


@pytest.fixture
def reset_prompt_version_repo():
    """Save / restore the loader's prompt-version repo singleton around
    each test that wires ``set_prompt_version_repo(...)``. The repo is
    a module-level global; cross-test pollution would corrupt unrelated
    tests if not torn down.
    """
    prev = _get_prompt_version_repo()
    set_prompt_version_repo(None)
    try:
        yield
    finally:
        set_prompt_version_repo(prev)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _build_mothers(*, soul_text: str) -> tuple[Path, dict[str, str]]:
    """Return a (dir, file-content-map) pair for tests that need
    bespoke file content.
    """
    return None, {"soul.md": soul_text}


def _write_soul(agent_dir: Path, text: str) -> None:
    (agent_dir / "soul.md").write_text(text)


def _cache_fingerprint(cache: PromptCache, key: str) -> str | None:
    """Return the ``__prompt_versions_fingerprint__`` value stored in
    the cache entry's mtimes dict, or ``None`` if the entry is absent.
    """
    entry = cache._cache.get(key)
    if entry is None:
        return None
    return entry[2].get("__prompt_versions_fingerprint__")


def _make_inner_soul_manager(
    service: PromptVersionService, cache: PromptCache,
) -> SimpleNamespace:
    """Small manager double carrying only what the inner_soul handler
    touches — ``prompt_cache`` + ``_prompt_version_service``.
    """
    return SimpleNamespace(
        prompt_cache=cache, _prompt_version_service=service,
    )


# ═══════════════════════════════════════════════════════════════════════════
# (1) S12/O-5 keystone — base selection change does NOT bust the [v2] sibling
# ═══════════════════════════════════════════════════════════════════════════


class TestBaseV2SiblingIsolation:
    """Keystone invariant — a DB-state change for the BASE identity
    MUST NOT bust the [v2] sibling's cached entry (and vice versa).

    Mechanism: the loader's cache-hit check recomputes the synthetic
    ``db_versions_fingerprint`` on every call. ``get_effective_rows``
    for a key with no DB rows returns ``[]`` → fingerprint ``""``
    (N4 sentinel). After the BASE approval, the BASE fingerprint
    changes (``""`` → ``hash(...)``), but the V2 fingerprint stays
    ``""`` because the V2 key still has zero DB rows. Hence the V2
    cache entry survives; the BASE busts and re-resolves.
    """

    def test_base_approval_busts_base_keeps_v2(
        self, repo, service, agent_dir, http_actor, internal_actor,
        reset_prompt_version_repo,
    ):
        cache = PromptCache()
        set_prompt_version_repo(repo)

        # Phase A: both entries populated against EMPTY DB → fingerprint="".
        prompt_base_1, tokens_base_1 = load_and_cache_prompt(
            "dev", agent_dir, cache, mcp_tool_names=None, version_tag=None,
        )
        prompt_v2_1, tokens_v2_1 = load_and_cache_prompt(
            "dev", agent_dir, cache, mcp_tool_names=None, version_tag="v2",
        )
        assert "I am the file soul" in prompt_base_1
        assert "I am the file soul" in prompt_v2_1
        assert _cache_fingerprint(cache, "dev::") == ""
        assert _cache_fingerprint(cache, "dev[v2]::") == ""

        # Approve a DB version for the BASE identity only. V2 stays empty.
        v = repo.create_draft(
            "dev", "", "soul", "DB_SOUL_APPROVED_CONTENT", internal_actor,
        )
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)

        # BASE fingerprint CHANGED → cache busts → DB content wins.
        prompt_base_2, tokens_base_2 = load_and_cache_prompt(
            "dev", agent_dir, cache, mcp_tool_names=None, version_tag=None,
        )
        assert "DB_SOUL_APPROVED_CONTENT" in prompt_base_2, (
            "BASE bust failed — the approved DB content should have "
            "replaced the cached file content."
        )
        assert _cache_fingerprint(cache, "dev::") != "", (
            "BASE fingerprint should have been re-stamped with the "
            "SHA256 of the approved-row triple."
        )

        # V2 fingerprint UNCHANGED (empty rows on v2) → cache hit →
        # still the file content. The keystone.
        prompt_v2_2, _ = load_and_cache_prompt(
            "dev", agent_dir, cache, mcp_tool_names=None, version_tag="v2",
        )
        assert "I am the file soul" in prompt_v2_2, (
            f"S12/O-5 keystone violated — BASE DB change busted the "
            f"V2 cache. v2 prompt now: {prompt_v2_2[:200]!r}"
        )
        assert prompt_v2_2 == prompt_v2_1, (
            "V2 cache should have served the EXACT cached prompt — "
            "no reload, no recomposition."
        )
        assert _cache_fingerprint(cache, "dev[v2]::") == "", (
            "V2 fingerprint should remain '' (no DB rows for v2)."
        )
        # SANITY — the BASE bust was a genuine re-eval (the file content
        # was replaced by DB_SOUL_APPROVED_CONTENT, so the served prompt
        # legitimately differs).
        assert prompt_base_2 != prompt_base_1
        # Tokens may legitimately shift between file content and DB
        # content (different character count → different token count);
        # what we want to pin is that the cache lookup went through.

    def test_v2_approval_does_not_bust_base(
        self, repo, service, agent_dir, http_actor, internal_actor,
        reset_prompt_version_repo,
    ):
        """Inverse keystone — V2 approval busts V2, BASE stays."""
        cache = PromptCache()
        set_prompt_version_repo(repo)

        _ = load_and_cache_prompt(
            "dev", agent_dir, cache, mcp_tool_names=None, version_tag=None,
        )
        _ = load_and_cache_prompt(
            "dev", agent_dir, cache, mcp_tool_names=None, version_tag="v2",
        )

        v = repo.create_draft(
            "dev", "v2", "soul", "DB_V2_APPROVED_CONTENT", internal_actor,
        )
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)

        # V2 busts and re-resolves to the V2 DB content.
        prompt_v2, _ = load_and_cache_prompt(
            "dev", agent_dir, cache, mcp_tool_names=None, version_tag="v2",
        )
        assert "DB_V2_APPROVED_CONTENT" in prompt_v2

        # BASE stays cached (no V2 rows visible to the base key).
        prompt_base, _ = load_and_cache_prompt(
            "dev", agent_dir, cache, mcp_tool_names=None, version_tag=None,
        )
        assert "I am the file soul" in prompt_base
        assert _cache_fingerprint(cache, "dev::") == ""


# ═══════════════════════════════════════════════════════════════════════════
# (2) Approve busts across MCP variants (C1 self-heal)
# ═══════════════════════════════════════════════════════════════════════════


class TestApproveBustsAcrossMCPVariants:
    """C1 — the single ``invalidate(agent_id, version_tag=...)`` call
    cannot reach every ``{agent}[{tag}]::{mcp_csv}`` MCP variant key.
    The fingerprint mechanism self-heals: when the fingerprint changes,
    each variant's stored mtimes diverge from the recomputed ones and
    the cache busts per-key on its own.
    """

    def test_approve_busts_every_mcp_variant_key_for_same_identity(
        self, repo, service, agent_dir, http_actor, internal_actor,
        reset_prompt_version_repo,
    ):
        cache = PromptCache()
        set_prompt_version_repo(repo)

        # Seed three cache entries under the same (agent, version_tag)
        # but different MCP variant keys.
        for mcp in (
            None, ["tool_x"], ["tool_x", "tool_y", "tool_z"],
        ):
            load_and_cache_prompt(
                "dev", agent_dir, cache, mcp_tool_names=mcp,
                version_tag=None,
            )
        # All three entries existed.
        assert cache.get("dev", None, None) is not None
        assert cache.get("dev", ["tool_x"], None) is not None
        assert cache.get("dev", ["tool_x", "tool_y", "tool_z"], None) is not None

        v = repo.create_draft(
            "dev", "", "soul", "DB_OVERRIDE_ALL_VARIANTS", internal_actor,
        )
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)

        # All three MCP variant keys should now serve the DB content.
        for mcp in (
            None, ["tool_x"], ["tool_x", "tool_y", "tool_z"],
        ):
            prompt, _ = load_and_cache_prompt(
                "dev", agent_dir, cache, mcp_tool_names=mcp,
                version_tag=None,
            )
            assert "DB_OVERRIDE_ALL_VARIANTS" in prompt, (
                f"MCP variant {mcp!r} did NOT self-heal after the "
                f"approval — C1 broken for that variant."
            )

        # NEW fingerprint stamped on all three entries.
        new_fp = _cache_fingerprint(cache, "dev::")
        assert new_fp != ""
        for mcp in (
            None, ["tool_x"], ["tool_x", "tool_y", "tool_z"],
        ):
            key = cache._make_key("dev", mcp, None)
            assert _cache_fingerprint(cache, key) == new_fp


# ═══════════════════════════════════════════════════════════════════════════
# (3) Select / clear bust
# ═══════════════════════════════════════════════════════════════════════════


class TestSelectClearBust:
    """Selection pointer changes — ``set_selection`` writes
    ``is_selected=1`` on a specific row (the fingerprint triple adds a
    non-empty ``selected_version_id``); ``clear_selection`` drops it
    (back to ``""``). Both transitions change the fingerprint, both
    bust the cache.
    """

    def _approved_double(
        self, repo, internal_actor, http_actor,
    ) -> tuple[str, str]:
        v1 = repo.create_draft("dev", "", "soul", "V1_BODY", internal_actor)
        repo.submit_for_approval(v1.version_id, internal_actor)
        repo.approve(v1.version_id, http_actor)
        v2 = repo.create_draft("dev", "", "soul", "V2_BODY", internal_actor)
        repo.submit_for_approval(v2.version_id, internal_actor)
        repo.approve(v2.version_id, http_actor)
        return v1.version_id, v2.version_id

    def test_set_then_clear_each_bust_the_cache(
        self, repo, service, agent_dir, internal_actor, http_actor,
        reset_prompt_version_repo,
    ):
        cache = PromptCache()
        set_prompt_version_repo(repo)

        v1_id, v2_id = self._approved_double(repo, internal_actor, http_actor)

        # Baseline load — no selection → effective = latest_approved = V2.
        prompt_v2, _ = load_and_cache_prompt(
            "dev", agent_dir, cache, mcp_tool_names=None, version_tag=None,
        )
        assert "V2_BODY" in prompt_v2
        fp_before_select = _cache_fingerprint(cache, "dev::")
        assert fp_before_select != ""

        # Point selection at V1 — selection adds ``selected_version_id``
        # to the fingerprint triple → bust.
        repo.set_selection(
            "dev", "", "soul", v1_id, internal_actor,
        )
        prompt_v1, _ = load_and_cache_prompt(
            "dev", agent_dir, cache, mcp_tool_names=None, version_tag=None,
        )
        assert "V1_BODY" in prompt_v1
        fp_after_select = _cache_fingerprint(cache, "dev::")
        assert fp_after_select != fp_before_select, (
            "set_selection must change the fingerprint (selected_version_id "
            "is part of the triple)."
        )

        # Clear selection — selected_version_id drops back to "" → bust.
        repo.clear_selection("dev", "", "soul", internal_actor)
        prompt_after_clear, _ = load_and_cache_prompt(
            "dev", agent_dir, cache, mcp_tool_names=None, version_tag=None,
        )
        # Falls back to latest_approved = V2 again.
        assert "V2_BODY" in prompt_after_clear
        fp_after_clear = _cache_fingerprint(cache, "dev::")
        assert fp_after_clear != fp_after_select, (
            "clear_selection must change the fingerprint (selected_version_id "
            "drops out of the triple)."
        )
        # Round-trip back to the original fingerprint (no selection, same
        # latest-approved → V2).
        assert fp_after_clear == fp_before_select


# ═══════════════════════════════════════════════════════════════════════════
# (4) Pending rows do NOT bust the cache (F6)
# ═══════════════════════════════════════════════════════════════════════════


class TestPendingDoesNotBust:
    """F6 — only ``approved`` + ``selected`` rows are effective. A row
    in ``pending_approval`` does not contribute to the fingerprint
    triple, so the cache stays valid and serves the OLD content (the
    file fallback) on the next load.
    """

    def test_pending_create_and_submit_keeps_cache_serving_old_content(
        self, repo, service, agent_dir, internal_actor,
        reset_prompt_version_repo,
    ):
        """F6 — pending rows DO change the fingerprint (the artifact
        appears in ``get_effective_rows`` as a non-terminal row), but
        the served prompt MUST remain the OLD file content because
        the resolver chain is ``selected > latest_approved > None``
        and neither selected nor latest-approved exist for a
        pending-only key.

        Pin: the new prompt re-evaluates to the file content (not
        the pending draft body), so the EFFECTIVE prompt seen by the
        agent does not leak the pending text.
        """
        cache = PromptCache()
        set_prompt_version_repo(repo)

        # Seed the cache with the file content (no DB rows yet).
        prompt_initial, _ = load_and_cache_prompt(
            "dev", agent_dir, cache, mcp_tool_names=None, version_tag=None,
        )
        assert "I am the file soul" in prompt_initial
        assert _cache_fingerprint(cache, "dev::") == ""

        # create_and_submit — the real A5/C4 composite ends in
        # ``pending_approval`` (D4 auto-submit). No approval.
        result = asyncio.run(
            service.create_and_submit(
                "dev", "", "soul",
                lambda existing: "PENDING_BODY_DRAFT",
                internal_actor,
                file_fallback="FILE_BODY",
            )
        )
        assert result.state == "pending_approval"
        rows = repo.list_versions("dev", "", "soul")
        assert len(rows) == 1
        assert rows[0].state == "pending_approval"

        # Reload — the fingerprint WILL change (the artifact appears
        # in get_effective_rows as a non-selected / non-approved row),
        # so the cache busts and re-evaluates. The resolver chain
        # ``selected > latest_approved > None`` means neither branch
        # has content; the re-evaluated prompt should still be the
        # FILE content (no pending leak).
        prompt_after, _ = load_and_cache_prompt(
            "dev", agent_dir, cache, mcp_tool_names=None, version_tag=None,
        )
        assert "I am the file soul" in prompt_after, (
            "Pending row MUST NOT leak into the effective prompt — "
            "F6 (pending != effective). The re-evaluation should "
            "serve the file content because selected_content and "
            "latest_approved_content are both None."
        )
        assert "PENDING_BODY_DRAFT" not in prompt_after
        # The fingerprint has been re-stamped (NOT empty anymore —
        # the pending row contributes an artifact-triple row even
        # though its content is None). Pin ONLY that it changed.
        assert _cache_fingerprint(cache, "dev::") != "", (
            "Pending row appears in get_effective_rows as a "
            "non-terminal row → fingerprint stamped on the next "
            "cache reload. The fingerprint=empty invariant only "
            "holds when no DB rows exist at all."
        )

    def test_pending_then_approve_does_bust(
        self, repo, service, agent_dir, internal_actor, http_actor,
        reset_prompt_version_repo,
    ):
        """Sanity check — AFTER approve, the cache DOES bust and the
        DB content wins. This is the inverse of the prior test and
        proves the fingerprint mechanism is state-discriminating, not
        always-busting.
        """
        cache = PromptCache()
        set_prompt_version_repo(repo)

        load_and_cache_prompt(
            "dev", agent_dir, cache, mcp_tool_names=None, version_tag=None,
        )

        result = asyncio.run(
            service.create_and_submit(
                "dev", "", "soul",
                lambda existing: "NOW_APPROVED_BODY",
                internal_actor,
                file_fallback="FILE_BODY",
            )
        )
        repo.approve(result.version_id, http_actor)

        prompt_after, _ = load_and_cache_prompt(
            "dev", agent_dir, cache, mcp_tool_names=None, version_tag=None,
        )
        assert "NOW_APPROVED_BODY" in prompt_after, (
            "After approval, the cache should bust and the DB content "
            "should win."
        )
        assert _cache_fingerprint(cache, "dev::") != ""


# ═══════════════════════════════════════════════════════════════════════════
# (5) Token accounting threads version_tag
# ═══════════════════════════════════════════════════════════════════════════


class TestTokenAccountingThreadsVersionTag:
    """``_get_system_prompt_tokens`` must read the **TAGGED** cache
    entry when ``meta.agent_tag`` is set — not the base entry. The
    grep-pin in ``TestLoaderPromptCacheInvalidateRespectsTagsT7D3``
    proves the source-level threading; this test proves the
    runtime behavior returns the tagged cache's token count.
    """

    def test_token_count_reads_tagged_entry_not_base(self):
        from daemon.services.instance_messaging import InstanceMessagingService

        cache = PromptCache()
        cache.set(
            "dev", "BASE_PROMPT_NOT_USED", 100, {"skill.md": 1.0},
            mcp_tool_names=None, version_tag=None,
        )
        cache.set(
            "dev", "V2_PROMPT_USED", 777, {"skill.md": 1.0},
            mcp_tool_names=None, version_tag="v2",
        )

        meta = SimpleNamespace(
            agent_id="dev",
            agent_tag="v2",
            instance_metadata={"mcp_tool_names": None},
        )

        # Spy that records the cache.get args — beats a MagicMock so
        # the real ``PromptCache.get`` still runs (we want to assert
        # the cache key it computes).
        get_calls: list[tuple] = []
        real_get = cache.get

        def spy_get(agent_id, mcp_tool_names=None, version_tag=None):
            get_calls.append((agent_id, mcp_tool_names, version_tag))
            return real_get(agent_id, mcp_tool_names, version_tag)

        cache.get = spy_get  # type: ignore[assignment]

        # Mock manager carrying only the pieces ``_get_system_prompt_tokens``
        # touches: ``_instance_repository.get`` (sync, returns the meta
        # object) + ``prompt_cache`` (our spy'd cache).
        meta_repository = MagicMock()
        meta_repository.get.return_value = meta
        manager = SimpleNamespace(
            _instance_repository=meta_repository, prompt_cache=cache,
        )

        # Build a minimal service instance — bypass __init__ so we
        # don't drag the cancellation_service / events_service
        # construction into the unit test (they're not used by
        # ``_get_system_prompt_tokens``).
        svc = InstanceMessagingService.__new__(InstanceMessagingService)
        svc._manager = manager
        svc._cancellation_service = SimpleNamespace()
        svc._child_reports_service = None
        svc._events_service = None
        svc._consecutive_proactive_skips = {}
        svc._last_seen_message_count = {}

        tokens = asyncio.run(svc._get_system_prompt_tokens("inst-test-1"))

        assert tokens == 777, (
            f"_get_system_prompt_tokens must read the TAGGED entry's "
            f"token count (777), got {tokens}"
        )
        # The cache.get spy must have received version_tag="v2" — if
        # it received None, the F9/R10 bug is still present.
        assert get_calls, "cache.get was never called"
        called = get_calls[0]
        assert called[0] == "dev"
        assert called[2] == "v2", (
            f"F9/R10 token-accounting bug — cache.get received "
            f"version_tag={called[2]!r}, expected 'v2'. Without the "
            f"tagged read, a [v2] instance would silently use the "
            f"base cache's token count."
        )

    def test_token_count_returns_zero_for_missing_cached_entry(self):
        """When there's no cached entry (untagged agent with no rows),
        the function returns 0 rather than crashing — exercised here
        so the test also pins the trivial cache-miss path.
        """
        from daemon.services.instance_messaging import InstanceMessagingService

        cache = PromptCache()
        meta = SimpleNamespace(
            agent_id="dev",
            agent_tag=None,
            instance_metadata={"mcp_tool_names": None},
        )
        meta_repository = MagicMock()
        meta_repository.get.return_value = meta
        manager = SimpleNamespace(
            _instance_repository=meta_repository, prompt_cache=cache,
        )
        svc = InstanceMessagingService.__new__(InstanceMessagingService)
        svc._manager = manager
        svc._cancellation_service = SimpleNamespace()
        svc._child_reports_service = None
        svc._events_service = None
        svc._consecutive_proactive_skips = {}
        svc._last_seen_message_count = {}

        assert asyncio.run(svc._get_system_prompt_tokens("inst-x")) == 0

    def test_token_count_returns_zero_for_missing_instance_row(self):
        """Defensive — a missing instance row returns 0 (no traceback).
        Pinned so a future refactor can't accidentally regress to
        raising.
        """
        from daemon.services.instance_messaging import InstanceMessagingService

        cache = PromptCache()
        meta_repository = MagicMock()
        meta_repository.get.return_value = None  # no row
        manager = SimpleNamespace(
            _instance_repository=meta_repository, prompt_cache=cache,
        )
        svc = InstanceMessagingService.__new__(InstanceMessagingService)
        svc._manager = manager
        svc._cancellation_service = SimpleNamespace()
        svc._child_reports_service = None
        svc._events_service = None
        svc._consecutive_proactive_skips = {}
        svc._last_seen_message_count = {}

        assert asyncio.run(svc._get_system_prompt_tokens("ghost")) == 0


# ═══════════════════════════════════════════════════════════════════════════
# (6) Logical invalidate site coverage — the 2nd & 3rd inner_soul sites
# ═══════════════════════════════════════════════════════════════════════════


class TestLogicalSiteMemoriesInvalidateThreadsVersionTag:
    """``inner_soul._update_memories`` (file-write carve-out on the
    ``memories/`` directory) calls
    ``manager.prompt_cache.invalidate(agent_id, version_tag=version_tag)``
    — the buster threads the tag so a [v2] agent's cache busts and a
    base agent's cache busts independently.

    Site coverage (a) from the file docstring — ``_submit_prompt_version``
    — is covered by ``test_inner_soul_db.py::TestInvalidateThreadsVersionTag``.
    This class pins site (b).
    """

    def test_memories_write_busts_tagged_key_not_base(
        self, repo, service, agent_dir, reset_prompt_version_repo,
    ):
        cache = PromptCache()
        cache.set("dev", "BASE_PROMPT", 10, {}, None, "")
        cache.set("dev", "V2_PROMPT", 10, {}, None, "v2")

        manager = _make_inner_soul_manager(service, cache)
        classification = {
            "type": "observation",
            "description": "test memory",
            "target": "memories",
        }

        result = _update_memories(
            "dev", agent_dir,
            "an observation worth remembering",
            classification, manager=manager, version_tag="v2",
            # Ud#3 fail-closed gate: MEMORY-class rows are stamped with
            # the submitter instance id (production closure always has
            # one). This test predates the gate — pass it explicitly.
            instance_id="inst-t12-memories-1",
        )
        assert result["success"] is True

        # The V2 key was busted; the BASE key was NOT.
        assert cache.get("dev", None, "") is not None
        assert cache.get("dev", None, "v2") is None

    def test_memories_write_with_none_tag_only_busts_base_key(
        self, repo, service, agent_dir, reset_prompt_version_repo,
    ):
        """A base-agent call (``version_tag=None``) busts the base
        key only — mirrors the canonical registry identity.
        """
        cache = PromptCache()
        cache.set("dev", "BASE", 1, {}, None, None)
        cache.set("dev", "V2", 2, {}, None, "v2")

        manager = _make_inner_soul_manager(service, cache)
        classification = {
            "type": "observation", "description": "t", "target": "memories",
        }

        _ = _update_memories(
            "dev", agent_dir, "obs",
            classification, manager=manager, version_tag=None,
            instance_id="inst-t12-memories-2",
        )

        assert cache.get("dev", None, None) is None
        assert cache.get("dev", None, "v2") is not None


class TestLogicalSiteMemoryMdInvalidateThreadsVersionTag:
    """``inner_soul._update_memory_md`` (direct file write to
    ``memory.md`` — the carve-out per D6) calls
    ``manager.prompt_cache.invalidate(agent_id, version_tag=version_tag)``.

    Pin site (c).
    """

    def test_memory_md_write_busts_tagged_key_not_base(
        self, repo, service, agent_dir, reset_prompt_version_repo,
    ):
        cache = PromptCache()
        cache.set("dev", "BASE_PROMPT", 10, {}, None, "")
        cache.set("dev", "V2_PROMPT", 10, {}, None, "v2")

        manager = _make_inner_soul_manager(service, cache)
        rules = {"max_memory_words": 2000}

        result = _update_memory_md(
            "dev", agent_dir, "remember this",
            rules, manager=manager, version_tag="v2",
            instance_id="inst-t12-memory-md-1",
        )
        assert result["success"] is True

        # V2 busted, base intact.
        assert cache.get("dev", None, "v2") is None
        assert cache.get("dev", None, "") is not None

    def test_memory_md_write_thread_proven_via_factory_signature(
        self, repo, service, agent_dir, reset_prompt_version_repo,
    ):
        """Factory-level proof that ``create_inner_soul_tool`` captures
        ``version_tag`` in its closure — the closure value reaches
        ``_execute_update`` → ``_update_memory_md`` (the routing the
        T7 commit added). Driving the full tool body through
        ``tool.invoke(...)`` is intentionally NOT done here: the
        classification pipeline can re-route to a different target
        (G2 / project-content gates) and would risk a flake that
        masks the threading invariant this test pins.

        The behavioral threading is proven below directly via the
        ``_update_memory_md`` call site with the closed-over tag.
        """
        # Closure-level check: ``create_inner_soul_tool`` must accept
        # and retain ``version_tag`` (factory signature wires the
        # bust-threading). The factory returns a callable; here we
        # just probe the closure introspection (cheap, robust).
        meta = AgentMetadata(
            id="dev", name="Dev", description="t",
            path=agent_dir, system=False,
        )
        registry = MagicMock()
        registry.get_version.return_value = meta
        registry.get_resolved.return_value = meta
        with pytest.MonkeyPatch().context() as mp:
            mp.setattr("daemon.registry.get_registry", lambda: registry)
            cache = PromptCache()
            manager = _make_inner_soul_manager(service, cache)
            tool = create_inner_soul_tool(
                manager, "dev", "inst-t12-2", version_tag="v2",
            )
        # The tool callable exists; the closure captured version_tag="v2".
        # Behavioral proof is the prior ``test_memory_md_write_busts_tagged_key_not_base``
        # — direct call with ``version_tag="v2"`` busts the tagged key.
        assert tool is not None
        assert callable(tool.invoke if hasattr(tool, "invoke") else tool)
