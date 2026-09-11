"""Unit pin — ``terminal_reason_variants_for`` stays in lockstep with
``_STATUS_CANONICAL_MAP``.

The jobs status filter's per-kind SQL branches derive their accepted
``terminal_reason`` value-sets from the canonical map via this helper
(single source of truth). If the reverse index ever drifts from the
map — a hand-copied list, a missed rebuild, a partial copy — the SQL
filter and the read layer (``_derive_legacy_status`` →
``canonicalize_status``) disagree again and rows silently drop from
status filters (the 2026-09-10 defect class).

These assertions pin the map-parity contract itself:

* every variant canonicalizes back onto its token (soundness);
* every map key appears in its target's variant-set (completeness —
  no map entry is missed by the index);
* the discriminator aliases the defect was about are present;
* unknown tokens degrade to an empty tuple (honest-empty).
"""

from __future__ import annotations

from daemon.services.work_status import (
    _STATUS_CANONICAL_MAP,
    terminal_reason_variants_for,
)


class TestTerminalReasonVariantsFor:
    """Map-parity pins for the derived reverse index."""

    def test_cancelled_includes_the_three_discriminator_aliases(self) -> None:
        """The defect's exact aliases are present in the cancelled set."""
        variants = terminal_reason_variants_for("cancelled")
        assert "cancelled" in variants
        assert {"aborted", "orphan_retired", "watchover_terminated"} <= set(
            variants
        )

    def test_every_variant_canonicalizes_to_its_token(self) -> None:
        """Soundness: for every canonical token, each returned variant
        maps back onto that token through the map.
        """
        tokens = (
            "pending",
            "processing",
            "paused",
            "completed",
            "failed",
            "cancelled",
            "dead_letter",
        )
        for token in tokens:
            for variant in terminal_reason_variants_for(token):
                assert _STATUS_CANONICAL_MAP[variant] == token, (
                    f"variant {variant!r} of token {token!r} maps to "
                    f"{_STATUS_CANONICAL_MAP.get(variant)!r}"
                )

    def test_image_is_complete_no_map_entry_missed(self) -> None:
        """Completeness: EVERY map key appears in the variant-set of
        its own target — the index is a true partition of the map's
        domain, so a new alias added to the map flows through.
        """
        for src, tgt in _STATUS_CANONICAL_MAP.items():
            assert src in terminal_reason_variants_for(tgt), (
                f"map entry {src!r} → {tgt!r} missing from the "
                f"reverse index of {tgt!r}"
            )

    def test_completed_has_no_aliases_beyond_itself(self) -> None:
        """``completed`` currently has a single-element variant set —
        pinned so a future alias addition is a CONSCIOUS map edit
        picked up by the filter automatically.
        """
        assert terminal_reason_variants_for("completed") == ("completed",)

    def test_failed_carries_the_error_alias(self) -> None:
        """``error`` (Instance-status alias) folds onto ``failed`` —
        the failed branch's IN-list must include it.
        """
        variants = terminal_reason_variants_for("failed")
        assert "failed" in variants
        assert "error" in variants

    def test_unknown_token_returns_empty_tuple(self) -> None:
        """Tokens with no map image degrade honestly-empty — ``settled``
        is a per-kind dispatch artefact (completed + message), NOT a
        map target, so its variant-set is empty by design.
        """
        assert terminal_reason_variants_for("settled") == ()
        assert terminal_reason_variants_for("no-such-token") == ()
