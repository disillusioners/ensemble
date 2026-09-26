"""PR1 — content-hardening extraction (agent-snapshot v1, Wave 1a).

Pins that move the corpus from ``daemon.compaction`` to the new canonical
home ``daemon._content_hardening`` is behavior-preserving and that the
back-compat ``_name`` aliases still resolve correctly. The house style for
this trio is the small, focused regression-target test referenced in the
build commission's "Compaction regression pack" section (a sibling to
``tests/unit/test_reasoning_content_*.py``).

What this proves
----------------

1. The public predicates live in :mod:`daemon._content_hardening`.
2. ``daemon.compaction`` re-exports each predicate under its original
   private ``_name`` and the alias is the SAME function object (zero
   semantic change — no thin wrapper, no copy).
3. A representative predicate, ``is_hoisted_injected``, returns the same
   answer via either the canonical home or the alias (same answer is
   guaranteed by identity for the public+alias pair).
4. The partition/hoist/absorb contract still round-trips through the
   kill-switch (off-path degenerates to legacy two-bucket behavior).
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

import daemon._content_hardening as ch
import daemon.compaction as compaction_legacy


class TestCanonicalHome:
    """Public predicates live at ``daemon._content_hardening``."""

    def test_canonical_module_exposes_public_names(self):
        for name in (
            "is_injected_message",
            "has_context_kind",
            "extract_text_from_content",
            "injected_note_absorbed_ids",
            "is_hoisted_injected",
            "partition_injected_for_compaction",
        ):
            assert hasattr(ch, name), f"_content_hardening missing {name}"
            assert callable(getattr(ch, name)), f"_content_hardening.{name} not callable"

    def test_dunder_all_lists_public_surface(self):
        # The explicit ``__all__`` keeps the canonical surface honest
        # for ``from daemon._content_hardening import *`` and tooling.
        for name in (
            "is_injected_message",
            "has_context_kind",
            "extract_text_from_content",
            "injected_note_absorbed_ids",
            "is_hoisted_injected",
            "partition_injected_for_compaction",
        ):
            assert name in ch.__all__, f"__all__ missing {name}"


class TestBackcompatAliases:
    """``daemon.compaction`` re-exports each predicate under the original ``_name``."""

    def test_private_aliases_resolve_to_canonical_functions(self):
        # Aliases are the SAME function object — no copy, no wrapper.
        # A future refactor that accidentally reintroduces a wrapper
        # would break this identity check (which is the point).
        assert (
            compaction_legacy._is_injected_message
            is ch.is_injected_message
        )
        assert (
            compaction_legacy._has_context_kind
            is ch.has_context_kind
        )
        assert (
            compaction_legacy._extract_text_from_content
            is ch.extract_text_from_content
        )
        assert (
            compaction_legacy._injected_note_absorbed_ids
            is ch.injected_note_absorbed_ids
        )
        assert (
            compaction_legacy._is_hoisted_injected
            is ch.is_hoisted_injected
        )
        assert (
            compaction_legacy._partition_injected_for_compaction
            is ch.partition_injected_for_compaction
        )


class TestPredicateBehavior:
    """Sanity probes — same answers via canonical or alias (identity-equal)."""

    def test_is_injected_message_truthy_flag(self):
        msg = HumanMessage(
            content="operator prompt",
            additional_kwargs={"injected_message": True},
        )
        assert ch.is_injected_message(msg) is True
        # Identity-equal: the alias returns the same answer.
        assert compaction_legacy._is_injected_message(msg) == ch.is_injected_message(msg)

    def test_has_context_kind_requires_both_flags(self):
        ctx = HumanMessage(
            content="[SYSTEM CONTEXT] ...",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "snapshot_digest",
            },
        )
        bare = HumanMessage(
            content="operator note",
            additional_kwargs={"injected_message": True},
        )
        assert ch.has_context_kind(ctx) is True
        assert ch.has_context_kind(bare) is False

    def test_extract_text_from_content_skip_image_blocks(self):
        content = [
            {"type": "text", "text": "Hello "},
            {"type": "image_url", "image_url": {"url": "http://x/y.png"}},
            {"type": "text", "text": "World"},
        ]
        assert ch.extract_text_from_content(content) == "Hello World"

    def test_is_hoisted_injected_with_context_kind(self):
        msg = HumanMessage(
            content="[SYSTEM CONTEXT] ...",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "snapshot_digest",
            },
        )
        # context_kind ALWAYS hoists, regardless of the absorbed set.
        assert ch.is_hoisted_injected(msg, frozenset()) is True
        assert ch.is_hoisted_injected(msg, frozenset({msg.id or "_"})) is True

    def test_is_hoisted_injected_idless_bare_note_preserved(self):
        # id-less bare notes are conservatively preserved (the
        # absorb contract can never silently drop an unresolvable id).
        msg = HumanMessage(
            content="operator note without an id",
            additional_kwargs={"injected_message": True},
        )
        assert ch.is_hoisted_injected(msg, frozenset()) is True


class TestKillSwitchContractPreserved:
    """Absorb kill-switch still flattens the partition; legacy two-bucket on OFF."""

    def test_kill_switch_off_returns_empty_absorbed_ids(self, monkeypatch):
        # Re-set the kill-switch resolver on the canonical home AND on
        # the legacy alias module to mirror the symptom-repair test
        # pattern (post-PR1 the consulted reference lives in
        # daemon._content_hardening).
        import daemon._content_hardening as ch_module
        import daemon.config as cfg_module

        # Drive the OFF branch via the canonical-home resolver.
        monkeypatch.setattr(
            ch_module, "resolve_injected_notes_absorb", lambda: False
        )
        # Keep the legacy alias in sync in case any test reads it.
        monkeypatch.setattr(
            cfg_module, "resolve_injected_notes_absorb", lambda: False
        )

        ai = AIMessage(content="response")
        note = HumanMessage(
            content="operator note",
            id="n1",
            additional_kwargs={"injected_message": True},
        )
        # Answered note (an AIMessage exists at a later index) — would
        # normally be absorbed and join the selectable pool, but the
        # kill-switch OFF forces it into the preserved bucket.
        selectable, preserved, absorbed = ch.partition_injected_for_compaction(
            [note, ai]
        )
        # Note: with the kill-switch OFF, the absorb contract degenerates
        # to "preserve everything injected" — note stays preserved.
        assert preserved and any(getattr(m, "id", None) == "n1" for m in preserved)
        assert absorbed == []
