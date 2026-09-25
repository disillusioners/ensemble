"""Unit tests for the Agent Snapshot prompt module + R8 tags (PR4).

Covers:

* The R11 steering block is VERBATIM (design-exploration §6.3) and
  present in the composed persona (digest-quality drift = warm-start
  drift — the block is test-pinned).
* The extraction tuple has EXACTLY 8 fields and the dead-ends norm is
  a BESIDE-the-tuple norm, not a 9th field.
* D5 (digest-only): no message-history parameter exists anywhere in
  the prompt surface.
* :func:`daemon.services.snapshot_executor.parse_digest_markdown`
  maps the 8 R11 sections tolerantly (case/kebab-insensitive headers,
  bullets, task-summary prose, raw fallback).
* R8 tag machinery: auto-tag derivation (incl. the ``role:`` alias —
  BOTH emitted; ``from-snapshot:`` iff stamped) and the fail-loud
  judgment contract (fixed kind enum, kebab normalization, duplicate
  rejection, 2-4 window with hard cap 8).
"""

from __future__ import annotations

import inspect

import pytest

from daemon.services import snapshot_prompts as sp
from daemon.services.snapshot_executor import (
    KIND_ENUM,
    derive_auto_tags,
    normalize_judgment_tags,
    parse_digest_markdown,
)
from daemon.services.snapshot_prompts import DIGEST_KEYS, DIGEST_EXTRACTION_FIELDS


# ============================================================================
# R11 steering block + 8-tuple + dead-ends norm
# ============================================================================


class TestR11PromptModule:
    def test_steering_block_verbatim(self):
        """The block from design-exploration §6.3 R11 — byte-for-byte."""
        assert sp.R11_STEERING_BLOCK == (
            "Distill this instance's EXPERIENCE, not its transcript. "
            "Preserve: what worked and what wasted time — and why; "
            "gotchas and conventions discovered; mid-run workflow "
            "refinements you would apply next time; judgment calls and "
            "the reasoning behind them; dead ends worth remembering as "
            "dead ends. Do NOT restate results, logs, or outputs — "
            "reference them as artifacts (paths, job ids, commits). "
            "Flag timeless system knowledge for promotion "
            "(`promote-to-kb:`) and reusable procedures for skill "
            "graduation instead of embedding them. Capture only the "
            "delta over any inherited warm-start digest (already "
            "persisted). Prefer specific, reusable instincts over "
            "generic summary; when in doubt, keep the lesson, drop the "
            "detail. The ~25k ceiling is a ceiling, not a target."
        )

    def test_persona_contains_steering_block_and_norm(self):
        assert sp.R11_STEERING_BLOCK in sp.SNAPSHOT_SUMMARIZER_PERSONA
        assert sp.R11_DEAD_ENDS_NORM in sp.SNAPSHOT_SUMMARIZER_PERSONA
        assert "snapshot_prompts" not in sp.SNAPSHOT_SUMMARIZER_PERSONA

    def test_exactly_8_fields_and_norm_is_not_a_field(self):
        assert len(DIGEST_EXTRACTION_FIELDS) == 8
        assert DIGEST_KEYS == (
            "decisions",
            "gotchas",
            "conventions",
            "open_threads",
            "artifact_refs",
            "worked_vs_wasted",
            "workflow_refinements",
            "judgment_calls",
        )
        # The dead-ends norm lives BESIDE the tuple — no 9th field.
        assert "dead_ends" not in DIGEST_KEYS
        assert "dead end" in sp.R11_DEAD_ENDS_NORM.lower()

    def test_persona_instructs_all_8_sections(self):
        for _key, name, _inst in DIGEST_EXTRACTION_FIELDS:
            assert name in sp.SNAPSHOT_SUMMARIZER_PERSONA

    def test_d5_digest_only_no_message_history_param(self):
        """D5: no message-history parameter anywhere in the surface."""
        src = inspect.getsource(sp)
        assert "include_message_history" not in src
        assert "message_history" not in src


# ============================================================================
# Digest markdown parsing (R11 8-tuple)
# ============================================================================


class TestParseDigestMarkdown:
    def test_maps_all_8_headers_and_task_summary(self):
        text = (
            "A short working-state summary paragraph.\n"
            "\n"
            "## Decisions\n- Chose SQLite for the probe\n"
            "## Gotchas\n- edit_file can silently fail\n"
            "## Conventions\n- explicit-path git add only\n"
            "## Open threads\n- verify on live next week\n"
            "## Artifact refs\n- commits: abc1234\n"
            "## Worked-vs-wasted\n- probe-first worked; blind grep wasted\n"
            "## Mid-run workflow refinements\n- boot probe before full suite\n"
            "## Judgment calls\n- skipped PG mirror: new tables only\n"
        )
        digest = parse_digest_markdown(text)
        assert digest["task_summary_text"].startswith("A short working-state")
        assert digest["decisions"] == ["Chose SQLite for the probe"]
        assert digest["gotchas"] == ["edit_file can silently fail"]
        assert digest["conventions"] == ["explicit-path git add only"]
        assert digest["open_threads"] == ["verify on live next week"]
        assert digest["artifact_refs"] == ["commits: abc1234"]
        assert digest["worked_vs_wasted"] == [
            "probe-first worked; blind grep wasted"
        ]
        assert digest["workflow_refinements"] == [
            "boot probe before full suite"
        ]
        assert digest["judgment_calls"] == [
            "skipped PG mirror: new tables only"
        ]
        assert "raw_markdown" not in digest

    def test_case_and_kebab_insensitive_headers(self):
        text = "## Gotchas\n- one\n## worked-vs-wasted\n- two\n"
        digest = parse_digest_markdown(text)
        assert digest["gotchas"] == ["one"]
        assert digest["worked_vs_wasted"] == ["two"]

    def test_unparseable_output_falls_back_to_raw(self):
        digest = parse_digest_markdown("just some prose with no headers")
        assert digest["raw_markdown"] == "just some prose with no headers"
        assert all(digest[key] == [] for key in DIGEST_KEYS)


# ============================================================================
# R8 auto tags
# ============================================================================


class TestDeriveAutoTags:
    def test_full_auto_set_both_agent_and_role_emitted(self):
        tags = derive_auto_tags(
            project_id="p1",
            agent_id="coder",
            lineage_root_id="root-9",
            git_branch="latest",
            spawned_from_snapshot_id="snap-1",
            runtime_version="0.14.2",
        )
        assert tags == [
            "project:p1",
            "agent:coder",
            "role:coder",  # alias — BOTH emitted (drop-vs-deprecate deferred)
            "lineage:root-9",
            "branch:latest",
            "from-snapshot:snap-1",
            "runtime:0.14.2",
        ]

    def test_conditional_tags_omitted_when_absent(self):
        tags = derive_auto_tags(
            project_id="p1",
            agent_id="tester",
            lineage_root_id=None,
            git_branch=None,
            spawned_from_snapshot_id=None,
            runtime_version="0.14.2",
        )
        assert tags == ["project:p1", "agent:tester", "role:tester", "runtime:0.14.2"]


# ============================================================================
# R8 judgment tags — fail-loud contract
# ============================================================================


class TestNormalizeJudgmentTags:
    def test_valid_minimal_set_kind_plus_two(self):
        out = normalize_judgment_tags(
            ["kind:implementation", "subsystem:upgrade-pipeline", "topic:Live Promote"]
        )
        assert out == [
            "kind:implementation",
            "subsystem:upgrade-pipeline",
            "topic:live-promote",  # kebab-normalized
        ]

    def test_kind_must_be_in_fixed_enum(self):
        with pytest.raises(ValueError, match="not in the fixed enum"):
            normalize_judgment_tags(
                ["kind:exploration", "subsystem:x", "topic:y"]
            )
        assert "exploration" not in KIND_ENUM
        assert "defect-verification" in KIND_ENUM

    def test_exactly_one_kind_required(self):
        with pytest.raises(ValueError, match="exactly one kind"):
            normalize_judgment_tags(["subsystem:x", "topic:y"])
        with pytest.raises(ValueError, match="exactly one kind"):
            normalize_judgment_tags(
                ["kind:review", "kind:refactor", "subsystem:x"]
            )

    def test_unknown_dim_rejected(self):
        with pytest.raises(ValueError, match="dim 'flavor' unknown"):
            normalize_judgment_tags(
                ["kind:review", "flavor:x", "topic:y"]
            )

    def test_missing_colon_rejected(self):
        with pytest.raises(ValueError, match="not dim:value"):
            normalize_judgment_tags(["just-a-tag", "kind:review", "topic:y"])

    def test_duplicate_after_normalization_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            normalize_judgment_tags(
                ["kind:review", "Topic:Live Promote", "topic:live-promote"]
            )

    def test_count_window_fail_loud(self):
        # Below the 2-floor: kind alone (1 judgment tag) is invalid.
        with pytest.raises(ValueError, match="outside"):
            normalize_judgment_tags(["kind:review"])
        # kind + one free-form = 2 = the valid floor.
        assert len(normalize_judgment_tags(["kind:review", "topic:x"])) == 2
        # Hard cap 8: nine tags fail loud.
        nine = ["kind:review"] + [f"topic:t{i}" for i in range(8)]
        with pytest.raises(ValueError, match="outside"):
            normalize_judgment_tags(nine)
        # Exactly 8 passes (cap boundary).
        eight = ["kind:review"] + [f"topic:t{i}" for i in range(7)]
        assert len(normalize_judgment_tags(eight)) == 8
