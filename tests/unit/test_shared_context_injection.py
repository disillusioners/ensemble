"""Unit tests for ``append_shared_context_metadata``.

The injection function lives in
``daemon/services/instance_lifecycle.py`` and is wired into the
post-processing chain at both spawn and restore call sites. These
tests use ``unittest.mock.MagicMock`` for both repositories — the
function only needs ``instance_repository.get_tree_root_id(parent_id)``
and ``shared_context_metadata_repo.get_all_as_dict(context_key)`` from
each.

Failure paths return the prompt unchanged so a transient repo error
never breaks instance execution; the tests assert that contract.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from daemon.services.instance_lifecycle import append_shared_context_metadata


# ─── Helpers ───────────────────────────────────────────────────────────────────


def _make_repo(kvs: dict | None = None, raises: Exception | None = None):
    """Build a mock ``SharedContextMetadataRepository`` with the given KV snapshot.

    ``raises`` (if provided) is raised by ``get_all_as_dict`` so the
    error-handling test path stays isolated from the happy path.
    """
    repo = MagicMock()
    if raises is not None:
        repo.get_all_as_dict.side_effect = raises
    else:
        repo.get_all_as_dict.return_value = kvs or {}
    return repo


def _make_instance_repo(root_id: str | None = None, raises: Exception | None = None):
    """Build a mock ``SQLModelInstanceRepository`` returning the given root id."""
    repo = MagicMock()
    if raises is not None:
        repo.get_tree_root_id.side_effect = raises
    else:
        repo.get_tree_root_id.return_value = root_id
    return repo


@pytest.fixture
def base_prompt() -> str:
    """A minimal system prompt that the injection appends to."""
    return "# System\nYou are an agent."


# ─── Happy path: metadata exists ───────────────────────────────────────────────


class TestInjectionWithMetadata:
    """Tests for the path where ``get_all_as_dict`` returns rows."""

    def test_injects_kv_when_metadata_exists(self, base_prompt):
        """When the repo returns a KV dict, the prompt gains a metadata section."""
        repo = _make_repo({"project_scope": "LARGE", "priority": 1})

        result = append_shared_context_metadata(
            system_prompt=base_prompt,
            instance_id="inst-1",
            instance_repository=MagicMock(),
            shared_context_metadata_repo=repo,
        )

        # The base prompt is preserved at the head.
        assert result.startswith(base_prompt)
        # The metadata header is present.
        assert "# Shared Context" in result
        assert "## Metadata KV" in result
        # The KV payload appears as pretty-printed JSON somewhere in the
        # appended section. Order may vary across dict implementations
        # but both keys/values must be in the body.
        assert '"project_scope"' in result
        assert '"LARGE"' in result
        assert '"priority"' in result
        assert "1" in result

    def test_injection_payload_is_valid_json(self, base_prompt):
        """The KV payload after ``## Metadata KV`` parses as valid JSON."""
        kvs = {"a": 1, "b": [1, 2, 3], "c": {"nested": True}}
        repo = _make_repo(kvs)

        result = append_shared_context_metadata(
            system_prompt=base_prompt,
            instance_id="inst-1",
            instance_repository=MagicMock(),
            shared_context_metadata_repo=repo,
        )

        # Slice from the KV header to the closing separator.
        marker = "## Metadata KV"
        start = result.index(marker) + len(marker)
        # The payload sits between the marker and the trailing "---" / end.
        tail = result[start:].lstrip()
        # Take everything up to the next "\n\n---" (closing fence).
        end = tail.find("\n\n---")
        payload = tail[:end].strip() if end != -1 else tail.strip()

        parsed = json.loads(payload)
        assert parsed == kvs

    def test_injection_uses_separator_fences(self, base_prompt):
        """The injection is fenced with ``---`` separators above and below."""
        repo = _make_repo({"k": "v"})

        result = append_shared_context_metadata(
            system_prompt=base_prompt,
            instance_id="inst-1",
            instance_repository=MagicMock(),
            shared_context_metadata_repo=repo,
        )

        appended = result[len(base_prompt):]
        # Leading "---" and trailing "---" both present.
        assert appended.startswith("\n\n---\n\n")
        assert appended.rstrip().endswith("---")


# ─── No-metadata short-circuit ─────────────────────────────────────────────────


class TestInjectionNoMetadata:
    """Tests for the path where there is nothing to inject."""

    def test_returns_unchanged_when_no_metadata(self, base_prompt):
        """An empty KV dict → prompt is returned byte-for-byte unchanged."""
        repo = _make_repo({})

        result = append_shared_context_metadata(
            system_prompt=base_prompt,
            instance_id="inst-1",
            instance_repository=MagicMock(),
            shared_context_metadata_repo=repo,
        )

        assert result == base_prompt

    def test_returns_unchanged_for_unseen_context_key(self, base_prompt):
        """A freshly-seen ``context_key`` with no rows also short-circuits."""
        # No prior writes — repo returns the empty default.
        repo = _make_repo({})

        result = append_shared_context_metadata(
            system_prompt=base_prompt,
            instance_id="inst-fresh",
            instance_repository=MagicMock(),
            shared_context_metadata_repo=repo,
        )

        assert result == base_prompt


# ─── Context-key resolution ────────────────────────────────────────────────────


class TestContextKeyResolution:
    """The function must resolve ``context_key`` from the instance tree root.

    Root instances use their own ``instance_id``; children walk the
    tree via ``instance_repository.get_tree_root_id(parent_id)`` and
    fall back to ``parent_id`` when the lookup misses.
    """

    def test_resolves_context_key_for_root_instance(self):
        """``parent_id=None`` → ``context_key == instance_id``."""
        repo = _make_repo({"k": "v"})
        instance_repo = _make_instance_repo()  # get_tree_root_id never called

        append_shared_context_metadata(
            system_prompt="prompt",
            instance_id="root-1",
            instance_repository=instance_repo,
            shared_context_metadata_repo=repo,
        )

        # get_tree_root_id must NOT be called for a root instance.
        instance_repo.get_tree_root_id.assert_not_called()
        # Repo is queried with the root's own id.
        repo.get_all_as_dict.assert_called_once_with("root-1")

    def test_resolves_context_key_for_child_instance(self):
        """``parent_id=...`` → repo queried with the resolved tree root id."""
        repo = _make_repo({"k": "v"})
        instance_repo = _make_instance_repo(root_id="grandparent-1")

        append_shared_context_metadata(
            system_prompt="prompt",
            instance_id="child-1",
            instance_repository=instance_repo,
            shared_context_metadata_repo=repo,
            parent_id="parent-1",
        )

        # Walks the tree via the parent.
        instance_repo.get_tree_root_id.assert_called_once_with("parent-1")
        repo.get_all_as_dict.assert_called_once_with("grandparent-1")

    def test_fallback_when_tree_root_id_none(self):
        """When ``get_tree_root_id`` returns ``None``, fall back to ``parent_id``."""
        repo = _make_repo({"k": "v"})
        instance_repo = _make_instance_repo(root_id=None)

        append_shared_context_metadata(
            system_prompt="prompt",
            instance_id="child-1",
            instance_repository=instance_repo,
            shared_context_metadata_repo=repo,
            parent_id="parent-1",
        )

        # Still asked the tree repo, but the resolver fell back to parent_id.
        instance_repo.get_tree_root_id.assert_called_once_with("parent-1")
        repo.get_all_as_dict.assert_called_once_with("parent-1")


# ─── Failure paths ─────────────────────────────────────────────────────────────


class TestErrorHandling:
    """Repository failures must degrade gracefully — the prompt stays intact."""

    def test_error_handling_repo_failure(self, base_prompt):
        """A repo exception returns the prompt unchanged (no injection)."""
        repo = _make_repo(raises=RuntimeError("simulated DB failure"))

        result = append_shared_context_metadata(
            system_prompt=base_prompt,
            instance_id="inst-1",
            instance_repository=MagicMock(),
            shared_context_metadata_repo=repo,
        )

        assert result == base_prompt

    def test_error_handling_instance_repo_failure(self, base_prompt):
        """An instance_repo exception also returns the prompt unchanged."""
        repo = _make_repo({"k": "v"})
        instance_repo = _make_instance_repo(
            raises=RuntimeError("simulated tree failure")
        )

        result = append_shared_context_metadata(
            system_prompt=base_prompt,
            instance_id="inst-1",
            instance_repository=instance_repo,
            shared_context_metadata_repo=repo,
            parent_id="parent-1",
        )

        assert result == base_prompt