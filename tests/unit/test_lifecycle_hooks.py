"""Unit tests for the lifecycle hook registry, dispatcher, and hook function.

Covers Phase 5 tasks 1–7 of the Instance Lifecycle Hooks plan:

* Registry + dispatcher behaviour (registration, ordering, filtering, error
  handling, cancellation propagation).
* C1 — configured hook-name filtering.
* C3 — slug derivation.
* C4 — same-second collision + slug-parser compatibility.
* Hook function ``_add_to_shared_context_md_files``.
* ``lifecycle_hooks`` config field on ``AgentMetadata``.

The ``_HOOK_REGISTRY`` in ``daemon.services.lifecycle_hooks`` is module-level
mutable state, so each test runs against a clean copy via the
:func:`_clear_hook_registry` autouse fixture.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.services.context_injection import (
    _extract_slug_from_filename as _inject_extract_slug,
    _score_context_files,
    MATCH_THRESHOLD,
)
from daemon.services.context_tools import (
    _extract_slug_from_filename as _tools_extract_slug,
    list_context_files,
    write_context_file,
)
from daemon.services.lifecycle_hooks import (
    LifecycleHookContext,
    _add_to_shared_context_md_files,
    _derive_report_slug,
    _HOOK_REGISTRY,
    dispatch_lifecycle_hooks,
    register_lifecycle_hook,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_hook_registry():
    """Snapshot and restore the module-level hook registry around each test.

    The registry is mutated in place by ``register_lifecycle_hook`` and
    :func:`dispatch_lifecycle_hooks` reads from it.  Without this fixture
    registrations from earlier tests would leak into later ones.

    The built-in ``add_to_shared_context_md_files`` hook is re-registered
    after the clear so integration tests that exercise the real hook
    function can find it.
    """
    saved = {k: dict(v) for k, v in _HOOK_REGISTRY.items()}
    _HOOK_REGISTRY.clear()
    # Re-register the built-in so real-hook tests have a working registry.
    register_lifecycle_hook(
        "on_complete",
        "add_to_shared_context_md_files",
        _add_to_shared_context_md_files,
    )
    try:
        yield
    finally:
        _HOOK_REGISTRY.clear()
        _HOOK_REGISTRY.update(saved)


def _make_ctx(
    instance_id: str = "abcdef12-3456-7890-abcd-ef1234567890",
    last_content: str = "report body",
    context_key: str | None = "ctx-1",
) -> LifecycleHookContext:
    """Construct a minimal :class:`LifecycleHookContext` for tests."""
    return LifecycleHookContext(
        instance_id=instance_id,
        agent_id="test-agent",
        parent_id="parent-001",
        last_content=last_content,
        outcome="regular_child_completed",
        context_key=context_key,
        manager=MagicMock(),
    )


# ─── Task 1: registry + dispatcher ──────────────────────────────────────────


class TestRegistryDispatcher:
    """Cover the basic registry + dispatcher contract."""

    @pytest.mark.asyncio
    async def test_register_lifecycle_hook_adds_to_registry(self):
        hook = AsyncMock()
        register_lifecycle_hook("on_complete", "h1", hook)

        assert "on_complete" in _HOOK_REGISTRY
        assert _HOOK_REGISTRY["on_complete"]["h1"] is hook

    @pytest.mark.asyncio
    async def test_dispatch_calls_hooks_in_hook_names_order(self):
        order: list[str] = []

        async def h1(ctx):
            order.append("h1")

        async def h2(ctx):
            order.append("h2")

        async def h3(ctx):
            order.append("h3")

        register_lifecycle_hook("on_complete", "h1", h1)
        register_lifecycle_hook("on_complete", "h2", h2)
        register_lifecycle_hook("on_complete", "h3", h3)

        await dispatch_lifecycle_hooks("on_complete", ["h1", "h2", "h3"], _make_ctx())
        assert order == ["h1", "h2", "h3"]

    @pytest.mark.asyncio
    async def test_dispatch_with_empty_hook_names_is_noop(self):
        hook = AsyncMock()
        register_lifecycle_hook("on_complete", "h1", hook)

        await dispatch_lifecycle_hooks("on_complete", [], _make_ctx())
        hook.assert_not_called()

    @pytest.mark.asyncio
    async def test_hook_exception_is_swallowed_and_logged(self, caplog):
        async def bad(ctx):
            raise RuntimeError("boom")

        good = AsyncMock()

        register_lifecycle_hook("on_complete", "bad", bad)
        register_lifecycle_hook("on_complete", "good", good)

        with caplog.at_level(logging.WARNING, logger="daemon.services.lifecycle_hooks"):
            await dispatch_lifecycle_hooks(
                "on_complete", ["bad", "good"], _make_ctx()
            )

        # Second hook still ran despite the first hook's failure.
        good.assert_awaited_once()

        # First hook failure was logged at WARNING.
        assert any(
            record.levelno == logging.WARNING and "bad" in record.message
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_re_register_same_hook_name_overwrites(self):
        first = AsyncMock()
        second = AsyncMock()
        register_lifecycle_hook("on_complete", "h1", first)
        register_lifecycle_hook("on_complete", "h1", second)

        await dispatch_lifecycle_hooks("on_complete", ["h1"], _make_ctx())
        first.assert_not_called()
        second.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_through_dispatcher(self):
        async def hanging(ctx):
            raise asyncio.CancelledError()

        register_lifecycle_hook("on_complete", "h1", hanging)

        with pytest.raises(asyncio.CancelledError):
            await dispatch_lifecycle_hooks("on_complete", ["h1"], _make_ctx())


# ─── Task 2: C1 — Configured hook-name filtering ─────────────────────────────


class TestHookNameFiltering:
    """W8 #1: dispatcher only runs the hooks named in ``hook_names``."""

    @pytest.mark.asyncio
    async def test_only_named_hook_runs(self):
        a = AsyncMock()
        b = AsyncMock()
        register_lifecycle_hook("on_complete", "hook_a", a)
        register_lifecycle_hook("on_complete", "hook_b", b)

        await dispatch_lifecycle_hooks("on_complete", ["hook_a"], _make_ctx())

        a.assert_awaited_once()
        b.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_in_explicit_order(self):
        order: list[str] = []

        async def a(ctx):
            order.append("a")

        async def b(ctx):
            order.append("b")

        register_lifecycle_hook("on_complete", "hook_a", a)
        register_lifecycle_hook("on_complete", "hook_b", b)

        await dispatch_lifecycle_hooks(
            "on_complete", ["hook_b", "hook_a"], _make_ctx()
        )
        assert order == ["b", "a"]

    @pytest.mark.asyncio
    async def test_unknown_hook_name_is_skipped(self, caplog):
        """An unknown hook name is silently skipped (not raised)."""
        known = AsyncMock()
        register_lifecycle_hook("on_complete", "known", known)

        with caplog.at_level(
            logging.DEBUG, logger="daemon.services.lifecycle_hooks"
        ):
            await dispatch_lifecycle_hooks(
                "on_complete", ["known", "does_not_exist"], _make_ctx()
            )
        known.assert_awaited_once()
        # The skip is logged at DEBUG level; verify via the logger name.
        assert any(
            record.name == "daemon.services.lifecycle_hooks"
            and "hook not registered" in record.message
            for record in caplog.records
        )


# ─── Task 3: C3 — Slug derivation ────────────────────────────────────────────


class TestSlugDerivation:
    """W8 #1 (C3): heading-based slug with non-boilerplate fallback."""

    def test_heading_only(self):
        slug = _derive_report_slug(
            "# Distributed Consensus Algorithms\n\nbody", "abcd1234"
        )
        assert slug == "distributed-consensus-algorithms"

    def test_no_heading_uses_first_substantive_line(self):
        slug = _derive_report_slug(
            "Migrating from SQLite to PostgreSQL\n\nbody", "abcd1234"
        )
        assert slug == "migrating-from-sqlite-to-postgresql"

    def test_boilerplate_only_falls_back_to_child_report_id(self):
        slug = _derive_report_slug(
            "✅ Task Complete\nSkill(s) Applied: foo\n---\n```\n```\n",
            "abcd1234-extra",
        )
        assert slug == "child-report-abcd1234"

    def test_skips_boilerplate_then_picks_real_heading(self):
        slug = _derive_report_slug(
            "✅ Task Complete\n"
            "Skill(s) Applied: foo\n"
            "---\n"
            "```\n"
            "# Real Heading\n"
            "```\n",
            "abcd1234",
        )
        assert slug == "real-heading"

    def test_slug_is_capped_at_80_chars(self):
        long = "a" * 200
        slug = _derive_report_slug(long, "abcd1234")
        assert len(slug) <= 80
        assert re.fullmatch(r"[a-z0-9-]+", slug)

    def test_written_file_scores_above_threshold_against_topic_query(self, tmp_path, monkeypatch):
        """Heuristic matcher should score the slug-derived filename above the
        0.10 threshold against a query that uses a topic keyword from the heading.
        """
        # Redirect tempdir to tmp_path for filesystem isolation.
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

        body = (
            "# Distributed Consensus Algorithms\n\n"
            "body about consensus algorithms"
        )
        path = write_context_file(
            context_key="ctx-x",
            content=body,
            slug=_derive_report_slug(body, "abcd1234"),
            suffix=".md",
            instance_id="abcd1234-deadbeef",
        )
        assert path.exists()
        context_dir = path.parent

        scored = _score_context_files("consensus", context_dir)
        assert scored, "expected at least one match"
        top_score, _ = scored[0]
        assert top_score >= MATCH_THRESHOLD, (
            f"top score {top_score} below threshold {MATCH_THRESHOLD}"
        )


# ─── Task 4: C4 — Same-second collision ──────────────────────────────────────


class TestFilenameCollision:
    """W8 #2: identical slug + timestamp + different instance_id must produce
    distinct filenames (the new ``_abcd1234`` suffix discriminates)."""

    def test_same_slug_same_second_different_instance_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

        # Two writes with the same body, but different instance_ids.
        body = "# Same Heading\n\nbody"
        slug = _derive_report_slug(body, "id1")

        p1 = write_context_file(
            context_key="ctx",
            content=body,
            slug=slug,
            suffix=".md",
            instance_id="aaaa1111-zzz",
        )
        p2 = write_context_file(
            context_key="ctx",
            content=body,
            slug=slug,
            suffix=".md",
            instance_id="bbbb2222-zzz",
        )

        # Different filenames due to instance_id[:8] suffix.
        assert p1.name != p2.name
        assert p1.exists()
        assert p2.exists()
        # The suffix appears in each filename.
        assert "_aaaa1111" in p1.name
        assert "_bbbb2222" in p2.name


# ─── Task 5: C4 — Slug-parser compatibility ──────────────────────────────────


class TestSlugParserCompat:
    """W8 #3: both the ``context_tools`` and ``context_injection`` slug parsers
    must accept the OLD and NEW filename formats."""

    def test_context_tools_new_format(self):
        slug = _tools_extract_slug("my-slug_20260808_123410_abcd1234.md")
        assert slug == "my-slug"

    def test_context_tools_new_format_with_sub_slug(self):
        slug = _tools_extract_slug(
            "child-report-abc12345_20260808_123410_deadbeef.md"
        )
        assert slug == "child-report-abc12345"

    def test_context_tools_old_format(self):
        slug = _tools_extract_slug("my-slug_20260808_123410.md")
        assert slug == "my-slug"

    def test_context_injection_new_format(self):
        slug = _inject_extract_slug("my-slug_20260808_123410_abcd1234.md")
        assert slug == "my-slug"

    def test_context_injection_old_format(self):
        slug = _inject_extract_slug("my-slug_20260808_123410.md")
        assert slug == "my-slug"

    def test_heuristic_scorer_accepts_both_formats(self, tmp_path):
        """A file written under either naming scheme must still score above
        threshold when its topic keyword appears in the query."""
        old = tmp_path / "consensus-algo_20260808_123410.md"
        old.write_text("# Consensus\n\nbody", encoding="utf-8")
        new = tmp_path / "consensus-algo_20260808_123410_0123abcd.md"
        new.write_text("# Consensus\n\nbody", encoding="utf-8")

        scored = _score_context_files("consensus", tmp_path)
        names = {p.name for _, p in scored}
        assert old.name in names
        assert new.name in names
        # All scores above threshold.
        assert all(s >= MATCH_THRESHOLD for s, _ in scored)


# ─── Task 6: hook function `_add_to_shared_context_md_files` ────────────────


class TestAddToSharedContextHook:
    """End-to-end behavior of the first hook function."""

    @pytest.mark.asyncio
    async def test_writes_file_under_resolve_context_dir(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        ctx = _make_ctx(
            instance_id="abcd1234-9999-aaaa-bbbb-cccccccccccc",
            last_content="# Heading\n\nbody text",
            context_key="ctx-1",
        )

        await _add_to_shared_context_md_files(ctx)

        expected_dir = tmp_path / "ensemble" / "context" / "ctx-1"
        files = list(expected_dir.glob("*.md"))
        assert files, "hook should have written a .md file"
        assert all(f.exists() for f in files)

    @pytest.mark.asyncio
    async def test_file_includes_metadata_header_and_last_content(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        ctx = _make_ctx(
            instance_id="abcd1234",
            last_content="# Heading\n\nreal body",
            context_key="ctx-1",
        )

        await _add_to_shared_context_md_files(ctx)

        files = list((tmp_path / "ensemble" / "context" / "ctx-1").glob("*.md"))
        body = files[0].read_text(encoding="utf-8")
        assert "# Child Report: test-agent" in body
        assert "abcd1234" in body  # instance id
        assert "real body" in body  # last_content body

    @pytest.mark.asyncio
    async def test_filename_includes_instance_id_prefix(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        ctx = _make_ctx(
            instance_id="deadbeef-1234",
            last_content="# Heading\n\nbody",
            context_key="ctx-1",
        )

        await _add_to_shared_context_md_files(ctx)

        files = list((tmp_path / "ensemble" / "context" / "ctx-1").glob("*.md"))
        assert any("_deadbeef" in f.name for f in files)

    @pytest.mark.asyncio
    async def test_disk_write_error_is_swallowed(self, caplog):
        ctx = _make_ctx(context_key="ctx-1")

        with patch(
            "daemon.services.lifecycle_hooks.write_context_file",
            side_effect=OSError("disk full"),
        ):
            with caplog.at_level(
                logging.WARNING, logger="daemon.services.lifecycle_hooks"
            ):
                # Must NOT raise.
                await _add_to_shared_context_md_files(ctx)

        assert any(
            record.levelno == logging.WARNING
            and "add_to_shared_context_md_files" in record.message
            for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_empty_content_uses_fallback_slug(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
        ctx = _make_ctx(
            instance_id="abcdef12-zzz",
            last_content="   \n   \n   ",
            context_key="ctx-1",
        )

        await _add_to_shared_context_md_files(ctx)

        files = list_context_files("ctx-1")
        # list_context_files filters to the actual tempdir, which we redirected.
        # If isolation patches didn't apply, the result might be empty here.
        # We just need to confirm a file was written under our isolated dir.
        # (Fallback slug = "child-report-abcdef12")
        isolated_files = list(
            (tmp_path / "ensemble" / "context" / "ctx-1").glob("*.md")
        )
        assert any("child-report-abcdef12" in f.name for f in isolated_files)

    @pytest.mark.asyncio
    async def test_none_context_key_is_noop(self, caplog):
        ctx = _make_ctx(context_key=None)
        with caplog.at_level(
            logging.DEBUG, logger="daemon.services.lifecycle_hooks"
        ):
            await _add_to_shared_context_md_files(ctx)

        # No file written anywhere.
        # (We can't strictly verify "nowhere" — but the DEBUG log is the
        # contract.)
        assert any(
            "context_key is None" in r.message for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        ctx = _make_ctx(context_key="ctx-1")
        with patch(
            "daemon.services.lifecycle_hooks.write_context_file",
            side_effect=asyncio.CancelledError(),
        ):
            with pytest.raises(asyncio.CancelledError):
                await _add_to_shared_context_md_files(ctx)


# ─── Task 7: lifecycle_hooks config field on AgentMetadata ──────────────────


class TestLifecycleHooksConfigField:
    """Phase 3 schema additions: ``lifecycle_hooks`` field on ``AgentMetadata``."""

    def test_default_is_empty_dict(self):
        from pathlib import Path

        from daemon.registry import AgentMetadata

        m = AgentMetadata(id="a", name="A", path=Path("/tmp"))
        assert m.lifecycle_hooks == {}

    def test_configured_value_parses(self):
        from pathlib import Path

        from daemon.registry import AgentMetadata

        m = AgentMetadata(
            id="a",
            name="A",
            path=Path("/tmp"),
            lifecycle_hooks={"on_complete": ["x"]},
        )
        assert m.lifecycle_hooks == {"on_complete": ["x"]}

    def test_empty_list_value_parses_as_no_hooks(self):
        from pathlib import Path

        from daemon.registry import AgentMetadata

        m = AgentMetadata(
            id="a",
            name="A",
            path=Path("/tmp"),
            lifecycle_hooks={"on_complete": []},
        )
        # The configured empty list should remain as-is (no special
        # normalization) — the dispatcher treats empty lists as a no-op.
        assert m.lifecycle_hooks == {"on_complete": []}

    def test_wanderer_meta_json_loads_with_expected_lifecycle_hooks(self):
        """Smoke test: the project's actual ``agents/wanderer/meta.json``
        parses and contains the expected lifecycle hook wiring."""
        import json
        from pathlib import Path

        meta_path = Path(__file__).resolve().parents[2] / "agents" / "wanderer" / "meta.json"
        assert meta_path.exists(), f"missing wanderer meta.json: {meta_path}"
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        assert data.get("lifecycle_hooks") == {
            "on_complete": ["add_to_shared_context_md_files"]
        }
