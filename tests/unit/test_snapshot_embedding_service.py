"""Unit tests for the SnapshotEmbeddingService (PR5).

Covers:

* Trigger-query generation prompt grounding (Rev 5 §3.4):
  ``task_summary + 1-2 digest excerpts`` (no per-node digests).
* Query parsing — fenced JSON, bare JSON, prose fallback,
  ``<think>``-strip.
* Embedding pipeline (``embed_text``) — exercised via mocks.
* :meth:`update_snapshot_embeddings` end-to-end — generates +
  embeds + persists via the repository ``add_embedding`` seam.

The chat-completion + embedding API calls are intercepted with
monkeypatched module helpers so no real LLM is invoked.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.services.snapshot_embedding_service import (
    SnapshotEmbeddingService,
    _MAX_TRIGGER_QUERIES,
    _MIN_TRIGGER_QUERIES,
    _clamp_queries,
    _extract_digest_excerpts,
    _parse_trigger_queries,
)


class FakeSnapshot:
    """Stand-in for :class:`Snapshot` with the attributes the
    embedding service reads."""

    def __init__(
        self,
        *,
        snapshot_id: str = "snap-1",
        title: str = "upgrade-after-v0.13.9",
        task_summary: str = "investigated upgrade pipeline",
        digest: dict[str, Any] | None = None,
    ) -> None:
        self.id = snapshot_id
        self.title = title
        self.task_summary = task_summary
        self.digest = digest or {}


class FakeConfig:
    """Stub config — ``embedding_model`` + chat overrides."""

    def __init__(
        self,
        *,
        embedding_model: str = "text-embedding-3-small",
        chat_model: str | None = "gpt-4o-mini",
        chat_base_url: str | None = None,
        chat_api_key: str | None = "test-key",
        embedding_base_url: str | None = None,
        embedding_api_key: str | None = "test-key",
    ) -> None:
        self.embedding_model = embedding_model
        self.chat_model = chat_model
        self.chat_base_url = chat_base_url
        self.chat_api_key = chat_api_key
        self.embedding_base_url = embedding_base_url
        self.embedding_api_key = embedding_api_key


class FakeRepo:
    """In-memory snapshot repo — records ``add_embedding`` calls."""

    def __init__(self) -> None:
        self.embeddings: list[tuple[str, str, list[float]]] = []

    def add_embedding(
        self,
        snapshot_id: str,
        trigger_query: str,
        embedding: list[float],
    ) -> Any:
        self.embeddings.append((snapshot_id, trigger_query, list(embedding)))
        return MagicMock()


# ============================================================================
# Module-level helpers
# ============================================================================


class TestExtractDigestExcerpts:
    def test_task_summary_first_excerpt(self):
        digest = {"task_summary_text": "primary signal"}
        out = _extract_digest_excerpts(digest, max_excerpts=2)
        assert "primary signal" in out

    def test_two_excerpts_with_decisions(self):
        digest = {
            "task_summary_text": "primary",
            "decisions": ["use SQLite", "drop PG mirror"],
        }
        out = _extract_digest_excerpts(digest, max_excerpts=2)
        assert "primary" in out
        assert "use SQLite" in out

    def test_max_excerpts_respected(self):
        digest = {
            "task_summary_text": "ts",
            "decisions": ["d"],
            "gotchas": ["g"],
            "worked_vs_wasted": ["ww"],
        }
        one = _extract_digest_excerpts(digest, max_excerpts=1)
        assert "ts" in one
        # The second excerpt is NOT included.
        assert "d" not in one

    def test_empty_digest_returns_empty(self):
        assert _extract_digest_excerpts(None) == ""
        assert _extract_digest_excerpts({}) == ""

    def test_per_excerpt_char_cap(self):
        """Each excerpt is capped at 300 chars."""
        long_text = "x" * 1000
        digest = {"task_summary_text": long_text}
        out = _extract_digest_excerpts(digest, max_excerpts=1)
        # Only the first 300 chars of the text should appear.
        assert out == "x" * 300


class TestParseTriggerQueries:
    def test_fenced_json(self):
        text = (
            "```json\n"
            '["how to upgrade", "version pump workflow", '
            '"v0.13.9 migration"]\n'
            "```"
        )
        parsed = _parse_trigger_queries(text)
        assert parsed == [
            "how to upgrade",
            "version pump workflow",
            "v0.13.9 migration",
        ]

    def test_bare_json(self):
        text = 'noise ["how to upgrade", "version pump workflow", "v0.13.9 migration"] more noise'
        parsed = _parse_trigger_queries(text)
        assert parsed == [
            "how to upgrade",
            "version pump workflow",
            "v0.13.9 migration",
        ]

    def test_prose_numbered_list(self):
        text = (
            "1. how to upgrade the daemon\n"
            "2. version pump workflow\n"
            "3. migration checklist"
        )
        parsed = _parse_trigger_queries(text)
        assert parsed == [
            "how to upgrade the daemon",
            "version pump workflow",
            "migration checklist",
        ]

    def test_prose_bulleted_list(self):
        text = "- upgrade daemon\n- version pump\n- migration plan"
        parsed = _parse_trigger_queries(text)
        assert parsed == ["upgrade daemon", "version pump", "migration plan"]

    def test_strips_think_blocks(self):
        text = (
            "<think>private chain-of-thought</think>\n"
            "```json\n"
            '["how to upgrade", "version pump", "migration plan"]\n'
            "```"
        )
        parsed = _parse_trigger_queries(text)
        assert parsed == [
            "how to upgrade",
            "version pump",
            "migration plan",
        ]

    def test_empty_returns_empty(self):
        assert _parse_trigger_queries("") == []
        assert _parse_trigger_queries(None) == []  # type: ignore[arg-type]

    def test_garbage_returns_empty(self):
        assert _parse_trigger_queries("not parseable at all") == []


class TestClampQueries:
    def test_below_min_kept_as_is(self):
        """Below the 3-min band, return what we have (better than
        nothing — caller treats empty as no cache)."""
        assert _clamp_queries([]) == []
        # Single 1-char queries: clamp returns them as-is (the cleaning
        # step is upstream in _clean_queries — here we're at the
        # already-cleaned band).
        assert _clamp_queries(["one query"]) == ["one query"]
        assert _clamp_queries(["alpha", "beta"]) == ["alpha", "beta"]

    def test_above_max_truncated(self):
        queries = [f"trigger query number {i:02d}" for i in range(_MAX_TRIGGER_QUERIES + 5)]
        clamped = _clamp_queries(queries)
        assert len(clamped) == _MAX_TRIGGER_QUERIES
        assert clamped == queries[:_MAX_TRIGGER_QUERIES]

    def test_within_band_unchanged(self):
        queries = ["alpha", "beta gamma", "delta epsilon zeta"]
        assert _clamp_queries(queries) == queries


# ============================================================================
# SnapshotEmbeddingService — chat-completion path
# ============================================================================


def _make_service(
    *,
    chat_response: str | None = None,
    chat_raises: Exception | None = None,
    embed_vec: list[float] | None = None,
    patch_helpers: bool = True,
) -> tuple[SnapshotEmbeddingService, FakeRepo]:
    """Build a SnapshotEmbeddingService with optional patched OpenAI helpers.

    Returns the service + the fake repo (for inspection).

    ``patch_helpers=True`` (default) installs a default fake chat +
    fake embed so the service never hits the network. Tests that
    install their OWN monkeypatch (with the per-test ``lambda``
    stub) can pass ``patch_helpers=False`` and then patch AFTER
    ``_make_service`` runs — otherwise the default patch below
    would overwrite their stub.
    """
    repo = FakeRepo()
    config = FakeConfig()
    llm_config = {"model": "test", "api_key": "k", "base_url": "http://x"}
    service = SnapshotEmbeddingService(config, repo, llm_config)  # type: ignore[arg-type]

    if not patch_helpers:
        return service, repo

    # Patch the module-level _do_chat_call and _do_embed_call so the
    # service never hits the network.
    import daemon.services.snapshot_embedding_service as ses_mod

    def fake_chat(*args, **kwargs):
        if chat_raises is not None:
            raise chat_raises
        # Return an object shaped like the OpenAI response.
        msg = MagicMock()
        msg.content = chat_response or ""
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        return resp

    def fake_embed(*args, **kwargs):
        vec = embed_vec if embed_vec is not None else [0.1, 0.2, 0.3]
        item = MagicMock()
        item.embedding = vec
        resp = MagicMock()
        resp.data = [item]
        return resp

    # Override the module-level helpers (snapshot_embedding_service uses
    # module globals when calling _do_chat_call / _do_embed_call).
    ses_mod._do_chat_call = fake_chat  # type: ignore[assignment]
    ses_mod._do_embed_call = fake_embed  # type: ignore[assignment]
    return service, repo


class TestGenerateTriggerQueries:
    def test_grounded_in_task_summary_and_excerpts(self, monkeypatch):
        """Rev 5 §3.4: trigger queries are grounded in
        task_summary + 1-2 digest excerpts."""
        # Capture the user prompt.
        captured: dict[str, Any] = {}

        def capture_chat(*args, **kwargs):
            captured["prompt"] = (
                args[4] if len(args) > 4 else kwargs.get("user_prompt")
            )
            msg = MagicMock()
            msg.content = (
                '["how to upgrade", "version pump workflow", '
                '"v0.13.9 migration checklist"]'
            )
            choice = MagicMock()
            choice.message = msg
            resp = MagicMock()
            resp.choices = [choice]
            return resp

        import daemon.services.snapshot_embedding_service as ses_mod

        # ``patch_helpers=False`` so the per-test lambda is NOT
        # clobbered by the default chat stub installed by
        # ``_make_service``.
        monkeypatch.setattr(ses_mod, "_do_chat_call", capture_chat)
        service, _ = _make_service(patch_helpers=False)
        snap = FakeSnapshot(
            title="upgrade-pipeline",
            task_summary="investigated upgrade pipeline",
            digest={
                "task_summary_text": "v0.13.9 verified",
                "decisions": ["use SQLite"],
            },
        )
        queries = asyncio.run(service.generate_trigger_queries(snap))
        assert queries == [
            "how to upgrade",
            "version pump workflow",
            "v0.13.9 migration checklist",
        ]
        prompt = captured["prompt"]
        # The prompt must include the task_summary and at least one
        # digest excerpt — grounding is mandatory.
        assert "investigated upgrade pipeline" in prompt
        assert "v0.13.9 verified" in prompt
        # And the title for grounding.
        assert "upgrade-pipeline" in prompt

    def test_query_count_clamped_to_3_to_10(self, monkeypatch):
        """3-10 trigger queries per snapshot."""
        import daemon.services.snapshot_embedding_service as ses_mod

        monkeypatch.setattr(
            ses_mod,
            "_do_chat_call",
            lambda *a, **k: MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(
                            content=json.dumps(
                                [
                                    f"trigger query number {i}"
                                    for i in range(15)
                                ]
                            )
                        )
                    )
                ]
            ),
        )
        service, _ = _make_service(patch_helpers=False)
        queries = asyncio.run(
            service.generate_trigger_queries(FakeSnapshot())
        )
        assert _MIN_TRIGGER_QUERIES <= len(queries) <= _MAX_TRIGGER_QUERIES

    def test_chat_failure_returns_empty_list(self, monkeypatch):
        """LLM down → empty list; caller treats empty as
        'skip embedding refresh for this snapshot'."""
        import daemon.services.snapshot_embedding_service as ses_mod

        def boom(*args, **kwargs):
            raise RuntimeError("simulated LLM outage")

        monkeypatch.setattr(ses_mod, "_do_chat_call", boom)
        service, _ = _make_service(patch_helpers=False)
        service, _ = _make_service()
        queries = asyncio.run(
            service.generate_trigger_queries(FakeSnapshot())
        )
        assert queries == []


# ============================================================================
# SnapshotEmbeddingService — embed_text + update_snapshot_embeddings
# ============================================================================


class TestUpdateSnapshotEmbeddings:
    def test_full_pipeline_persists_rows(self, monkeypatch):
        """Generate + embed + persist the trigger queries."""
        import daemon.services.snapshot_embedding_service as ses_mod

        monkeypatch.setattr(
            ses_mod,
            "_do_chat_call",
            lambda *a, **k: MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(
                            content=json.dumps(
                                [
                                    "upgrade pipeline steps",
                                    "version pump workflow",
                                    "v0.13.9 migration checklist",
                                ]
                            )
                        )
                    )
                ]
            ),
        )
        monkeypatch.setattr(
            ses_mod,
            "_do_embed_call",
            lambda *a, **k: MagicMock(
                data=[MagicMock(embedding=[0.1, 0.2, 0.3])]
            ),
        )
        service, repo = _make_service(patch_helpers=False)
        snap = FakeSnapshot(
            snapshot_id="snap-X",
            title="upgrade-after-v0.13.9",
            task_summary="investigated upgrade pipeline",
            digest={
                "task_summary_text": "v0.13.9 verified",
                "decisions": ["use SQLite"],
            },
        )
        written = asyncio.run(service.update_snapshot_embeddings(snap))
        # All 3 queries wrote (one embedding row per query).
        assert written == 3
        assert len(repo.embeddings) == 3
        # Each row stamped with the snapshot id.
        for sid, q, vec in repo.embeddings:
            assert sid == "snap-X"
            assert 3 <= len(q) <= 200
            assert vec == [0.1, 0.2, 0.3]

    def test_partial_embed_failure_still_writes_remaining(self, monkeypatch):
        """One query's embed fails → the other two write; pipeline
        returns the count of successful rows (best-effort)."""
        import daemon.services.snapshot_embedding_service as ses_mod

        monkeypatch.setattr(
            ses_mod,
            "_do_chat_call",
            lambda *a, **k: MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(
                            content=json.dumps(
                                [
                                    "query one",
                                    "query two",
                                    "query three",
                                    "query four",
                                    "query five",
                                ]
                            )
                        )
                    )
                ]
            ),
        )
        call_count = {"n": 0}

        def flaky_embed(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("transient embed error")
            return MagicMock(
                data=[MagicMock(embedding=[0.5, 0.5])]
            )

        monkeypatch.setattr(ses_mod, "_do_embed_call", flaky_embed)
        service, repo = _make_service(patch_helpers=False)
        written = asyncio.run(
            service.update_snapshot_embeddings(FakeSnapshot())
        )
        # 4 of 5 succeeded.
        assert written == 4
        assert len(repo.embeddings) == 4

    def test_no_queries_returns_zero(self, monkeypatch):
        """Empty LLM response → zero rows written (no cache)."""
        import daemon.services.snapshot_embedding_service as ses_mod

        monkeypatch.setattr(
            ses_mod,
            "_do_chat_call",
            lambda *a, **k: MagicMock(
                choices=[MagicMock(message=MagicMock(content=""))]
            ),
        )
        service, repo = _make_service(patch_helpers=False)
        written = asyncio.run(
            service.update_snapshot_embeddings(FakeSnapshot())
        )
        assert written == 0
        assert repo.embeddings == []

    def test_no_id_returns_zero(self, monkeypatch):
        """Snapshot with no id → zero rows (defensive guard)."""
        import daemon.services.snapshot_embedding_service as ses_mod

        monkeypatch.setattr(
            ses_mod,
            "_do_chat_call",
            lambda *a, **k: MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(
                            content=json.dumps(
                                [
                                    "how to upgrade",
                                    "version pump",
                                    "migration plan",
                                ]
                            )
                        )
                    )
                ]
            ),
        )
        service, repo = _make_service(patch_helpers=False)
        snap = FakeSnapshot()
        snap.id = None  # type: ignore[assignment]
        written = asyncio.run(service.update_snapshot_embeddings(snap))
        assert written == 0
        assert repo.embeddings == []


# ============================================================================
# Embedding text — failure modes
# ============================================================================


class TestEmbedText:
    def test_empty_text_raises(self):
        service, _ = _make_service()
        with pytest.raises(ValueError):
            asyncio.run(service.embed_text(""))
        with pytest.raises(ValueError):
            asyncio.run(service.embed_text("   "))

    def test_empty_data_raises(self, monkeypatch):
        """Empty API response → RuntimeError (caller handles)."""
        import daemon.services.snapshot_embedding_service as ses_mod

        # ``patch_helpers=False`` so the per-test lambda below is
        # NOT clobbered by the default embed stub installed by
        # ``_make_service``.
        service, _ = _make_service(patch_helpers=False)
        monkeypatch.setattr(
            ses_mod,
            "_do_embed_call",
            lambda *a, **k: MagicMock(data=[]),
        )
        with pytest.raises(RuntimeError):
            asyncio.run(service.embed_text("hello"))

    def test_successful_embed(self, monkeypatch):
        import daemon.services.snapshot_embedding_service as ses_mod

        service, _ = _make_service(patch_helpers=False)
        monkeypatch.setattr(
            ses_mod,
            "_do_embed_call",
            lambda *a, **k: MagicMock(
                data=[MagicMock(embedding=[0.1, 0.2, 0.3])]
            ),
        )
        vec = asyncio.run(service.embed_text("hello"))
        assert vec == [0.1, 0.2, 0.3]
