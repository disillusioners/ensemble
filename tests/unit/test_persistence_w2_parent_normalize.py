"""Unit tests for the W2 read-rebuild parent_id normalization fix.

Pin: ``daemon/persistence.py:899`` now normalizes legacy ``""``
parent rows to ``None`` before calling
``assemble_context_messages``. Without the fix, a legacy row with
``parent_id=""`` resolved ``context_key=""`` (an empty string) via
``_resolve_tree_root_id`` — a mispartition symptom analogous to the
messaging-path defect fixed in commit 80bb61dd.

The messaging path (instance_messaging.py:3683-3692, commit 80bb61dd)
and the restore path (instance_lifecycle.py:3984-3994) already
normalize this exact shape; this test mirrors those normalization
contracts on the read-rebuild surface.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.persistence import _build_context_dicts_for_response


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_messages_with_user_query(query: str) -> list:
    """Build a minimal checkpoint-messages list with a human turn.

    The read-rebuild helper scans ``reversed(messages)`` for the
    first ``type == 'human'`` message and uses its content as
    ``user_query`` — we need at least one such entry.
    """
    from langchain_core.messages import HumanMessage
    return [HumanMessage(content=query)]


def _build_instance_meta(parent_id_value, project_id, use_simple_namespace):
    """Construct the ``instance_meta`` stand-in for a single case.

    Three of the four cases use a ``MagicMock`` (a fully-shaped row);
    the missing-attribute case uses ``SimpleNamespace`` to simulate a
    pre-2026-08 row that lacks the ``parent_id`` attribute entirely.
    """
    if use_simple_namespace:
        return SimpleNamespace(
            project_id=project_id,
            instance_metadata={"project_id": project_id},
        )
    instance_meta = MagicMock()
    instance_meta.parent_id = parent_id_value
    instance_meta.project_id = project_id
    instance_meta.instance_metadata = {"project_id": project_id}
    return instance_meta


async def _run_w2_case(
    parent_id_value, expected_parent_id, instance_id, project_id, use_simple_namespace,
):
    """Drive a single W2 case through ``_build_context_dicts_for_response``.

    Captures the kwargs threaded to ``assemble_context_messages`` so the
    post-call assertion can pin the parent_id handed to the orchestrator.
    """
    captured: dict = {}

    async def _capture(*args, **kwargs):
        captured["kwargs"] = kwargs
        return ([], [])  # no synthetic context messages

    ctx = {
        "instance_meta": _build_instance_meta(
            parent_id_value, project_id, use_simple_namespace,
        ),
        "agent_meta": MagicMock(),
    }
    manager = MagicMock()
    manager._instance_repository = MagicMock()
    messages = _make_messages_with_user_query("any user query")

    with patch(
        "daemon.services.context_messages.assemble_context_messages",
        new=AsyncMock(side_effect=_capture),
    ):
        await _build_context_dicts_for_response(
            instance_id=instance_id,
            ctx=ctx,
            manager=manager,
            messages=messages,
        )

    assert captured, "orchestrator was never called"
    return captured["kwargs"].get("parent_id")


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestReadRebuildParentIdNormalization:
    """Pin the W2 ``or None`` normalization on the read-rebuild path."""

    @pytest.mark.parametrize(
        "parent_id_value, expected_parent_id, instance_id, project_id, use_simple_namespace",
        [
            # Legacy empty-string parent row → must collapse to None
            # (was the silent mispartition symptom).
            ("", None, "legacy-empty-parent", "proj-w2-1", False),
            # Non-empty parent_id → must thread unchanged.
            ("caller-tree-root", "caller-tree-root", "normal-parent", "proj-w2-2", False),
            # Canonical root instance (parent_id is None) → must thread None
            # (the ``or None`` is a no-op here; this guards against
            # accidental truthy-coercion regressions).
            (None, None, "root-instance", "proj-w2-3", False),
            # Pre-2026-08 row lacks the parent_id attribute entirely
            # → getattr(..., None) default + ``or None`` → None.
            (None, None, "legacy-no-parent-col", "proj-w2-4", True),
        ],
    )
    @pytest.mark.asyncio
    async def test_parent_id_normalization(
        self,
        parent_id_value,
        expected_parent_id,
        instance_id,
        project_id,
        use_simple_namespace,
    ):
        """Every W2 row shape must yield the expected ``parent_id`` at the orchestrator seam.

        Companion pins for the W2 fix at ``daemon/persistence.py:899``:

        * legacy ``""`` → ``None`` (empty-partition guard)
        * non-empty parent → unchanged (no-op for the canonical caller case)
        * canonical root ``None`` → unchanged (no-op for the root case)
        * missing attribute → graceful ``None`` (schema-drift tolerance)

        Same assertions as the four pre-parametrize tests
        (``test_legacy_empty_parent_id_normalizes_to_none``,
        ``test_normal_parent_id_threads_unchanged``,
        ``test_none_parent_id_threads_unchanged``,
        ``test_missing_parent_id_attribute_threads_none``); the
        parametrize simply collapses the 4× ~25-line setup duplication.
        """
        actual = await _run_w2_case(
            parent_id_value=parent_id_value,
            expected_parent_id=expected_parent_id,
            instance_id=instance_id,
            project_id=project_id,
            use_simple_namespace=use_simple_namespace,
        )
        if expected_parent_id is None:
            assert actual is None, (
                f"W2 normalization must produce parent_id=None for "
                f"parent_id_value={parent_id_value!r} "
                f"(use_simple_namespace={use_simple_namespace}); "
                f"got parent_id={actual!r}. See daemon/persistence.py:899."
            )
        else:
            assert actual == expected_parent_id, (
                f"non-empty parent_id must thread unchanged; expected "
                f"{expected_parent_id!r}, got {actual!r}."
            )
