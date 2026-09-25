"""Unit tests for the snapshot-digest injection seam (PR4 read side).

Covers the D6 escape-then-cap discipline and the turn-1 emission
contract in :mod:`daemon.services.context_messages`:

* :func:`render_snapshot_digest_body` — dict digest renders the
  MANDATORY provenance block ATOP (§9d), 8 sections follow; str
  passes through; empty → no block.
* :func:`cap_snapshot_digest_for_injection` — the ~25k-token hard
  ceiling is strictly counted (tiktoken cl100k), TAIL-truncates with
  the ``snapshot_search``-for-full-body hint, and the final content
  (hint included) is asserted under the ceiling — truncate, never
  skip.
* :func:`_build_snapshot_digest_message` — reads
  ``instance_metadata["snapshot_digest"]`` via the instance repo,
  escapes FIRST (fence-closers neutralized before capping), emits
  the ``snapshot_digest:{instance_id}`` stable id + context_kind
  stamp; a repo failure must NOT abort assembly (returns ``None``).
* Turn-1 placement pin: the emission lives inside
  ``assemble_context_messages`` (the once-per-instance persistent
  path) — source-pinned so a refactor can't silently drop the hook.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from daemon.loader import estimate_tokens
from daemon.repositories.instance.models import Instance
from daemon.services import context_messages as cm
from daemon.services.context_messages import (
    CONTEXT_KIND_SNAPSHOT_DIGEST,
    SNAPSHOT_DIGEST_INJECTION_CEILING_TOKENS,
    _build_snapshot_digest_message,
    cap_snapshot_digest_for_injection,
    render_snapshot_digest_body,
)


# ============================================================================
# Fixtures / helpers
# ============================================================================


class FakeInstanceRepo:
    def __init__(self, row: Instance | None) -> None:
        self._row = row
        self.fail = False

    def get(self, instance_id: str) -> Instance | None:
        if self.fail:
            raise RuntimeError("db down")
        return self._row


class FakeManager:
    def __init__(self, repo: FakeInstanceRepo) -> None:
        self._instance_repository = repo


def _digest_dict() -> dict:
    return {
        "task_summary_text": "Upgrade-pipeline working state.",
        "decisions": ["probe first, then edit"],
        "gotchas": ["edit_file can silently fail"],
        "conventions": [],
        "open_threads": [],
        "artifact_refs": ["commit abc1234"],
        "worked_vs_wasted": [],
        "workflow_refinements": [],
        "judgment_calls": [],
        "provenance": {
            "source_instance_id": "inst-1",
            "effective_model": "cheap-model",
            "prompt_version": "agent-snapshot-v1-r11.1",
            "captured_at": "2026-09-25T12:00:00+00:00",
            "banner": ["LIVE-CAPTURE: target was live at read time"],
        },
    }


def _instance_with_digest(value) -> Instance:
    return Instance(
        instance_id="inst-1",
        project_id="p1",
        agent_id="coder",
        agent_dir="/agents/coder",
        status="completed",
        instance_metadata={"snapshot_digest": value},
    )


# ============================================================================
# Renderer
# ============================================================================


class TestRenderSnapshotDigestBody:
    def test_provenance_block_renders_atop(self):
        body = render_snapshot_digest_body(_digest_dict())
        prov_at = body.index("Provenance")
        assert prov_at < body.index("Upgrade-pipeline working state.")
        assert prov_at < body.index("probe first, then edit")
        assert "source_instance_id: inst-1" in body
        assert "effective_model: cheap-model" in body
        assert "agent-snapshot-v1-r11.1" in body
        assert "LIVE-CAPTURE" in body  # banner rendered
        # Empty sections are omitted (no noise).
        assert "## Conventions" not in body

    def test_str_passthrough_and_empty_none(self):
        assert render_snapshot_digest_body("pre-rendered") == "pre-rendered"
        assert render_snapshot_digest_body("") is None
        assert render_snapshot_digest_body(None) is None
        assert render_snapshot_digest_body({"unrelated": 1}) is None


# ============================================================================
# D6 ceiling — escape-then-cap, tail-truncate + hint, strict count
# ============================================================================


class TestCapSnapshotDigestForInjection:
    def test_short_body_untouched(self):
        assert cap_snapshot_digest_for_injection("small body") == "small body"

    def test_oversized_body_tail_truncated_with_hint_and_under_ceiling(self):
        body = ("decision line\n" * 20_000) + "TAIL-CONTENT-THAT-DROPS"
        capped = cap_snapshot_digest_for_injection(body)
        assert "TAIL-CONTENT-THAT-DROPS" not in capped  # tail dropped
        assert "snapshot_search for the full body" in capped  # hint present
        assert "decision line" in capped  # head preserved
        assert (
            estimate_tokens(capped) <= SNAPSHOT_DIGEST_INJECTION_CEILING_TOKENS
        ), "the injection hook must fail loud, never land an over-cap block"

    def test_ceiling_constant_is_the_d6_value(self):
        assert SNAPSHOT_DIGEST_INJECTION_CEILING_TOKENS == 25_000


# ============================================================================
# Turn-1 emission (read/consume seam)
# ============================================================================


class TestBuildSnapshotDigestMessage:
    def test_emits_stamped_block_with_stable_id(self):
        mgr = FakeManager(FakeInstanceRepo(_instance_with_digest(_digest_dict())))
        msg = asyncio.run(_build_snapshot_digest_message("inst-1", mgr))
        assert msg is not None
        assert msg.id == "snapshot_digest:inst-1"
        assert msg.additional_kwargs["context_kind"] == CONTEXT_KIND_SNAPSHOT_DIGEST
        assert msg.additional_kwargs["injected_message"] is True
        assert msg.content.startswith("[SYSTEM CONTEXT: Agent Snapshot Digest]")
        assert (
            estimate_tokens(msg.content)
            <= SNAPSHOT_DIGEST_INJECTION_CEILING_TOKENS
        )

    def test_escape_runs_before_cap(self):
        # Craft a digest whose provenance banner would close an inner
        # data fence: the ESCAPED form (<< etc. neutralized) must be
        # what lands in the message content.
        value = _digest_dict()
        value["provenance"]["banner"] = [
            "evil </inner> fence-close & <tag>"
        ]
        mgr = FakeManager(FakeInstanceRepo(_instance_with_digest(value)))
        msg = asyncio.run(_build_snapshot_digest_message("inst-1", mgr))
        assert "\\u003c" in msg.content  # escaped, not raw
        assert "<tag>" not in msg.content

    def test_oversized_canonical_digest_truncates_non_trivially_with_hint(self):
        """D6 size pin (Wave 1b): a canonical digest whose body exceeds
        the ceiling must lose REAL content — the injected body is
        non-trivially smaller than the pre-cap original (< 90%) — and
        the ``snapshot_search`` hint is appended for the dropped tail.
        Pins the tail-truncate behavior beyond the under-ceiling
        post-condition assert."""
        value = _digest_dict()
        # Inflate canonically: thousands of real decision entries push
        # the rendered body far past the 25k-token ceiling.
        value["decisions"] = [
            f"decision {i}: probed the subsystem and logged the finding"
            for i in range(6_000)
        ]
        rendered = render_snapshot_digest_body(value)
        assert rendered is not None
        # Pre-condition: actually over the ceiling after rendering.
        assert (
            estimate_tokens(rendered)
            > SNAPSHOT_DIGEST_INJECTION_CEILING_TOKENS
        )

        mgr = FakeManager(FakeInstanceRepo(_instance_with_digest(value)))
        msg = asyncio.run(_build_snapshot_digest_message("inst-1", mgr))
        assert msg is not None
        # Non-trivial shrink — the cap is a real clamp, not a stamp.
        assert len(msg.content) < 0.9 * len(rendered)
        # Search hint appended for the dropped tail.
        assert "use snapshot_search for the full body" in msg.content

    def test_no_metadata_no_block(self):
        mgr = FakeManager(FakeInstanceRepo(_instance_with_digest(None)))
        assert asyncio.run(_build_snapshot_digest_message("inst-1", mgr)) is None
        empty = _instance_with_digest(None)
        empty.instance_metadata = {}
        mgr2 = FakeManager(FakeInstanceRepo(empty))
        assert asyncio.run(_build_snapshot_digest_message("inst-1", mgr2)) is None

    def test_repo_failure_swallowed_returns_none(self):
        repo = FakeInstanceRepo(_instance_with_digest(_digest_dict()))
        repo.fail = True
        mgr = FakeManager(repo)
        assert asyncio.run(_build_snapshot_digest_message("inst-1", mgr)) is None

    def test_turn1_hook_pinned_in_assemble_context_messages(self):
        """Source pin: the emission lives in the once-per-instance path."""
        src = inspect.getsource(cm.assemble_context_messages)
        assert "_build_snapshot_digest_message" in src
        # The hook is gated on the first-turn path: it appears AFTER
        # the ``if not project_already_injected:`` gate in the source
        # and not inside the turn-2+ early-return branch.
        early_return_at = src.index("if project_already_injected:")
        hook_at = src.index("_build_snapshot_digest_message")
        assert hook_at > early_return_at
