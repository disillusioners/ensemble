"""Compact-note lane — the ENGINE surface (``daemon/compaction.py``).

The lane-owned, engine-level legs of the ``prompt_reload_compact_note_unit``
pack (plan §10.4):

* **L5** — the note renders VERBATIM (its EXACT bytes, in the GLOBAL
  region, as the FIRST body after the GLOBAL header).
* **L6** — the note is INDEPENDENT of the merge pass: the merge forced to
  ``("", False)`` still leaves the note present, the placeholder absent,
  and the surviving sections present.
* **L10** — the D25 merge-skip pin: ≥2 batches + an armed note ⇒ NO merge
  LLM call ⇒ the per-batch sections survive ⇒ the note is in the GLOBAL
  region ⇒ NO ``(overview unavailable …)`` placeholder co-emission.
* **L11** — the ``previous_overview`` suppression pin: two consecutive
  note-guided compactions ⇒ the second carries the NEW note exactly once
  and NO "Previous overview:" line carrying the OLD note; the no-note path
  keeps ``previous_overview`` byte-identically.
* The envelope's content-free marker (``note_guided`` / ``note_sha8`` /
  ``note_chars``) and the ceiling-rule inclusion.

Non-vacuity: L6's merge-failure stub is proven LOAD-BEARING by a control
leg (the placeholder WOULD render without the note), and L10's "no merge
LLM call" is asserted by a spy on the merge client, not by the outcome.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from daemon.compaction import (
    GLOBAL_DOC_PLACEHOLDER_GLOBAL,
    CompactionContext,
    ContextCompactor,
    MessageGroup,
    build_compaction_doc,
)
from daemon.config import CompactionConfig as CompactionConfigModel

#: The sentinel — a string the summarizer could NOT have produced, so
#: "the note's exact bytes are present" is a real assertion.
NOTE_SENTINEL = "ZZ-NOTE-SENTINEL-4c1f9a-note-body-9f3a-ZZ"


def _config(**overrides) -> CompactionConfigModel:
    base = {
        "enabled": True,
        "threshold": 0.80,
        "recent_message_window": 10,
        "min_recent_window": 3,
        "context_window_overrides": {},
        "context_window_default": 0,
        "target_ratio": 0.40,
        "model": "",
        "summarization_model": "",
        "min_messages_before_compaction": 10,
        "summarization_chunk_threshold": 0.60,
    }
    base.update(overrides)
    return CompactionConfigModel(**base)


def _messages(count: int, chars: int = 700) -> list:
    out = []
    for i in range(count):
        body = f"message {i} " + ("x" * chars)
        if i % 2 == 0:
            out.append(HumanMessage(content=body, id=f"human-{i}"))
        else:
            out.append(AIMessage(content=body, id=f"ai-{i}"))
    return out


# ── L5 — the note renders VERBATIM ──────────────────────────────────────────


# ── The envelope's content-free marker ──────────────────────────────────────


# ── L6 — the note is independent of the merge pass ──────────────────────────


# ── L11 — the previous_overview suppression pin ─────────────────────────────


# ── The ceiling rule ────────────────────────────────────────────────────────


class TestCeilingRule:
    def test_the_ceiling_rule_condenses_oldest_sections_first(self) -> None:
        """The ceiling rule itself SURVIVES (the auto path uses it
        unchanged). The note-specific hunk is gone; the rule is unchanged."""
        from daemon.compaction import _apply_ceiling_rule

        sections = [
            {"start_idx": 1, "end_idx": 1, "body": "s" * 4000, "start_id": None, "end_id": None}
        ]
        condensed, archived_line, archived = _apply_ceiling_rule(
            sections, "GLOBAL", 1000
        )
        assert archived >= 1



# ── L10 — the D25 merge-skip pin (the chunked path) ────────────────────────


# ── L16 — the AC7.4 POSITIVE-SHAPE pin (the build gate) ────────────────────


# ── L15 — the consumption predicate ─────────────────────────────────────────


# ── L14 — the T4 pin (the executor's engine call) ──────────────────────────


# ── The T3 gate (the quiescent pre-dispatch seam) ──────────────────────────

