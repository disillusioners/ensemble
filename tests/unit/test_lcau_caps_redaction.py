"""LCA user-intent section U — cap + redaction witness unit tests.

Spec-driven (incident 4dfded83, 2026-09-18 — judge was intent-blind). Real
``assemble_fused_bundle`` + ``emit_resolver_eval_row`` calls; no mocks. The
U feature is ADDITIVE to the pre-existing A+B+C assembly: section U
caps at BUNDLE_U_SECTION_MAX (2000), the total bundle cap was raised
12000 → 14000 (exactly +2000 for U), and ALL id-bearing text (including
the user prose) is redacted via :func:`redact_ids`. A/B/C caps stay at
3000/6000/3000 — the legacy maxed A+B+C transcript must NOT newly clip.

Test mapping
------------
1. ``test_u_section_clipped_to_cap_with_redaction``
   U clipped to exactly 2000 chars + UUID redaction applied.
2. ``test_total_bundle_size_capped_at_14000``
   When all sections hit their caps, total ≤14000; the truncation
   suffix (34 bytes: 1 ellipsis + 1 newline + 31 bracketed text +
   1 newline) fires.
3. ``test_a_b_c_sections_untouched_by_u_presence`` +
   ``test_legacy_no_user_no_new_clipping``
   A/B/C rendering identical with vs without U; legacy maxed A+B+C
   (no user message) total ≤12032 (no new clipping).
4. ``test_u_witnesses_when_present`` + ``test_u_witnesses_when_absent``
   ``u_chars`` = post-clip U size; ``user_message_included`` = True/False.
5. ``test_emit_resolver_eval_row_carries_u_witnesses`` (×2 — present + absent)
   The eval-row log line carries ``bundle_u_chars=`` + ``user_message_included=``.

Tolerance breakdown (documented as required)
--------------------------------------------
The "+34 header tolerance" is the truncation suffix used when the natural
bundle length exceeds BUNDLE_TOTAL_MAX (14000):

    _truncation_suffix = "…\n[bundle truncated at total cap]\n"

Byte count:
    "…"                           → 1  (Unicode U+2026)
    "\n"                          → 1
    "[bundle truncated at total cap]" → 31
        ("[" 1 + "bundle" 6 + " " 1 + "truncated" 9 + " " 1 + "at" 2
         + " " 1 + "total" 5 + " " 1 + "cap" 3 + "]" 1)
    "\n"                          → 1
    ─────────────────────────────────
    TOTAL                         = 34 chars

Truncation math: ``text[: BUNDLE_TOTAL_MAX - 34] + suffix`` =
``text[: 13966] + 34-byte suffix`` = exactly 14000 chars.
"""
from __future__ import annotations

import logging
import re

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from daemon.services.attestation_resolver_activation import (
    BUNDLE_A_SECTION_MAX,
    BUNDLE_B_SECTION_MAX,
    BUNDLE_C_SECTION_MAX,
    BUNDLE_TOTAL_MAX,
    BUNDLE_U_SECTION_MAX,
    ActivationResult,
    ChildReportCheckEvidence,
    FusedBundle,
    ResolverEvalSnapshot,
    SourceASignals,
    SourceBSignals,
    SourceCSignals,
    assemble_fused_bundle,
    emit_resolver_eval_row,
)

# ─────────────────────────────────────────────────────────────────────────────
# Module-level constants (mirror the production internals we measure against).
# ─────────────────────────────────────────────────────────────────────────────

#: Section-U slot hint (mirrors ``_USER_INTENT_SLOT_HINT`` in production).
_USER_INTENT_SLOT_HINT: str = "user"

#: Truncation suffix used at the total-cap seam (mirrors the local
#: ``_truncation_suffix`` in :func:`assemble_fused_bundle`).
_TRUNCATION_SUFFIX: str = "…\n[bundle truncated at total cap]\n"

#: UUID-shape pattern (mirrors ``_UUID_TOKEN_RE`` in production).
_UUID_TOKEN_RE: re.Pattern[str] = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)

#: Logger name for the resolver-activation module (used by ``emit_resolver_eval_row``).
_RESOLVER_LOGGER_NAME: str = "daemon.services.attestation_resolver_activation"


# ─────────────────────────────────────────────────────────────────────────────
# Input builders — sized so the per-section caps in production actually fire.
# ─────────────────────────────────────────────────────────────────────────────


def _make_uuid(i: int) -> str:
    """Deterministic 36-char UUID string for redaction tests."""
    return f"00000000-0000-0000-0000-{i:012d}"


def _make_user_message_with_uuids(user_chars: int) -> HumanMessage:
    """A real-user HumanMessage containing 2 UUIDs + non-UUID filler.

    The message MUST pass :func:`is_real_user_message` (no injection stamp,
    no system prefix, no special kwargs) AND contain UUIDs that get
    redacted by :func:`redact_ids`. Length = exactly ``user_chars``.
    """
    uuid1 = "11111111-2222-3333-4444-555555555555"
    uuid2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    prefix = f"See {uuid1} and {uuid2} "
    padding_len = user_chars - len(prefix)
    assert padding_len >= 0, (
        f"user_chars={user_chars} too small to hold 2 UUIDs + prefix"
    )
    content = prefix + ("x" * padding_len)
    assert len(content) == user_chars
    # Sanity: the content actually contains the seeded UUIDs.
    assert _UUID_TOKEN_RE.search(content) is not None
    return HumanMessage(content=content)


def _make_a_signals_maxed(note_count: int) -> SourceASignals:
    """Source A with ``note_count`` evidence entries (each at max excerpt).

    Each note's ``note_excerpt`` is 400 chars — the :func:`_excerpt`
    internal cap inside :func:`_build_a_section`. With note_count > 5
    (the :data:`A_EVIDENCE_NOTES_CAP` is enforced only in
    :func:`collect_source_a_signals`, NOT in :func:`assemble_fused_bundle`
    — so direct construction can exceed it), the A section exceeds the
    3000-char cap and gets clipped.
    """
    notes = tuple(
        ChildReportCheckEvidence(
            child_instance_id=_make_uuid(i + 1),
            matched_terms=("done", "complete"),
            note_excerpt="Z" * 400,  # _excerpt(body, 400) caps at 400.
            stable_id=f"child_report_check:p:c{i+1}",
            kwargs_surface_seen=True,
        )
        for i in range(note_count)
    )
    return SourceASignals(
        advisory_present=True,
        phrase_match=True,
        evidence=notes,
    )


def _make_b_signals_maxed(
    ai_msg_chars: int,
    marker_terms_chars: int = 1300,
) -> tuple[SourceBSignals, list[AIMessage]]:
    """Source B + AI tail messages sized to force the B-section external clip.

    3 AIMessages each clipped to 1500 internally (per the B-section's
    per-message cap) → 3×1505 = 4515 chars. The marker_terms is padded
    to push the B status line past 6000, so the external :func:`_clip`
    at BUNDLE_B_SECTION_MAX actually fires.
    """
    ai_messages = [
        AIMessage(content="Q" * ai_msg_chars),
        AIMessage(content="R" * ai_msg_chars),
        AIMessage(content="S" * ai_msg_chars),
    ]
    big_marker_terms = ("X" * marker_terms_chars,)
    b_signals = SourceBSignals(
        marker_hit=False,
        marker_terms=big_marker_terms,
        length_trigger=False,
        final_word_count=10,
        attested=False,
    )
    return b_signals, ai_messages


def _make_c_signals_maxed(row_count: int) -> tuple[SourceCSignals, list[dict]]:
    """Source C + tree rows sized to force the C-section external clip.

    The first 10 rows render (per :data:`BUNDLE_C_TREE_ROWS_MAX`); a
    hidden-count suffix is appended when ``row_count > 10``. Each row's
    ``status`` + ``agent_id`` are padded so the rendered C-section
    exceeds 3000 chars (forcing the external clip).
    """
    c_signals = SourceCSignals(
        pending_children=0,
        queued_or_expected_wakeups=0,
        live_descendants=row_count,
        busy_descendants=0,
        user_answer_pending=False,
    )
    padded_agent = "A" * 200
    padded_status = "B" * 200
    tree_rows = [
        {
            "instance_id": _make_uuid(i + 100),
            "status": padded_status,
            "agent_id": padded_agent,
        }
        for i in range(row_count)
    ]
    return c_signals, tree_rows


# ─────────────────────────────────────────────────────────────────────────────
# 1. U at cap + id-redaction
# ─────────────────────────────────────────────────────────────────────────────


def test_u_section_clipped_to_cap_with_redaction():
    """Section U is clipped to BUNDLE_U_SECTION_MAX (2000) + UUIDs redacted.

    A real-user HumanMessage of 5000+ chars containing UUIDs is passed
    via ``user_intent_message``. The rendered U section in the bundle
    text MUST be ≤2000 chars AND contain NO raw UUID pattern (every
    id-bearing token replaced by ``<redacted-user-N>`` placeholders).
    """
    user_msg = _make_user_message_with_uuids(user_chars=5000)
    assert len(user_msg.content) >= 5000

    a_signals = _make_a_signals_maxed(note_count=5)
    b_signals, ai_messages = _make_b_signals_maxed(ai_msg_chars=2000)
    c_signals, tree_rows = _make_c_signals_maxed(row_count=12)

    bundle = assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        c_tree_rows=tree_rows,
        ai_tail_messages=ai_messages,
        user_intent_message=user_msg,
    )

    # ── Witness #1: u_chars = post-clip U size (== cap when clipped from >cap).
    assert bundle.u_chars == BUNDLE_U_SECTION_MAX == 2000, (
        f"u_chars={bundle.u_chars}, expected {BUNDLE_U_SECTION_MAX}"
    )
    # ── Witness #2: user_message_included=True (present case).
    assert bundle.user_message_included is True

    # ── Extract the rendered U section from the bundle text.
    # The text contains "=== SOURCE U: …" (the U header) followed by
    # redacted content + a blank line, then "=== SOURCE A: …". Splitting
    # on the SOURCE A marker yields the U block (header tail + content + blank).
    assert "=== SOURCE U:" in bundle.text, "U section header missing from bundle text"
    u_section = bundle.text.split("=== SOURCE U:")[1].split("=== SOURCE A:")[0]
    # The U block as it appears in the bundle text ≤ (2000 + trailing newline).
    assert len(u_section) <= BUNDLE_U_SECTION_MAX + 1, (
        f"U block in bundle text is {len(u_section)} chars, expected <= "
        f"{BUNDLE_U_SECTION_MAX + 1}"
    )

    # ── Redaction check: NO raw UUID pattern survives in the U section.
    surviving_uuids = _UUID_TOKEN_RE.findall(u_section)
    assert surviving_uuids == [], (
        f"U section still contains raw UUIDs after redaction: {surviving_uuids[:3]}"
    )
    # And at least one redacted placeholder IS present (proof that redaction ran).
    assert "<redacted-user-1>" in u_section or "<redacted-user-2>" in u_section, (
        "Neither <redacted-user-1> nor <redacted-user-2> found in U block; "
        "redaction did not produce expected placeholders"
    )


def test_u_section_redacts_uuids_in_bundle_text_overall():
    """Spot-check: the entire bundle text has no raw UUID (id-redaction
    uniformly applied — A-section child ids, C-section tree rows, B-section
    leader prose, AND U-section user prose all redacted)."""
    user_msg = _make_user_message_with_uuids(user_chars=5000)

    a_signals = _make_a_signals_maxed(note_count=5)
    b_signals, ai_messages = _make_b_signals_maxed(ai_msg_chars=2000)
    c_signals, tree_rows = _make_c_signals_maxed(row_count=12)

    bundle = assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        c_tree_rows=tree_rows,
        ai_tail_messages=ai_messages,
        user_intent_message=user_msg,
    )

    # The seeded UUIDs (uuid1, uuid2) MUST not survive in the rendered text.
    assert "11111111-2222-3333-4444-555555555555" not in bundle.text
    assert "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" not in bundle.text
    # And the regex-wide UUID scan finds zero hits.
    assert _UUID_TOKEN_RE.findall(bundle.text) == [], (
        f"Raw UUIDs still in bundle text: {_UUID_TOKEN_RE.findall(bundle.text)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Total cap accounting (incl. +34 tolerance)
# ─────────────────────────────────────────────────────────────────────────────


def test_tolerance_byte_count_is_34():
    """The truncation suffix is exactly 34 chars:

    ellipsis(1) + newline(1) + "[bundle truncated at total cap]"(31) + newline(1) = 34.

    Truncation math: ``text[:14000 - 34] + suffix`` = ``text[:13966] + 34-byte suffix``
    = exactly 14000 chars (BUNDLE_TOTAL_MAX).
    """
    assert len(_TRUNCATION_SUFFIX) == 34, (
        f"_TRUNCATION_SUFFIX len={len(_TRUNCATION_SUFFIX)}, expected 34. "
        f"Bytes: ellipsis(1) + \\n(1) + [bundle truncated at total cap](31) + \\n(1) = 34"
    )
    # Bracketed text component:
    bracketed = "[bundle truncated at total cap]"
    assert len(bracketed) == 31, (
        f"Bracketed suffix text len={len(bracketed)}, expected 31"
    )


def test_total_bundle_size_capped_at_14000():
    """When A/B/C are maxed (3000/6000/3000) AND U is at 2000, total ≤ 14000.

    Char accounting (with all sections AT their caps):
        31       header "[LCA FUSED EVIDENCE BUNDLE v1]\\n" (30 + 1 newline)
       2001      U section (2000 chars) + "\\n" separator
       3001      A section (3000 chars) + "\\n" separator
       6001      B section (6000 chars) + "\\n" separator
       3000      C section (3000 chars), no trailing separator
       ─────
      14034      natural total (exceeds BUNDLE_TOTAL_MAX by 34)

    Truncation math: text[:14000 - 34] + 34-byte suffix = exactly 14000.

    Without U (legacy shape): 31 + 3001 + 6001 + 3000 = 12033 chars — under
    the new 14000 cap, so NO truncation fires (U feature introduces no new
    clip step on the legacy transcript).
    """
    user_msg = _make_user_message_with_uuids(user_chars=5000)
    # 8 notes forces A natural > 3000 → external clip fires.
    a_signals = _make_a_signals_maxed(note_count=8)
    # marker_terms padded to 1300 forces B natural > 6000 → external clip fires.
    b_signals, ai_messages = _make_b_signals_maxed(
        ai_msg_chars=2000, marker_terms_chars=1300,
    )
    # 12 rows forces C natural > 3000 → external clip fires.
    c_signals, tree_rows = _make_c_signals_maxed(row_count=12)

    bundle = assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        c_tree_rows=tree_rows,
        ai_tail_messages=ai_messages,
        user_intent_message=user_msg,
    )

    # Total cap is honored.
    assert bundle.total_chars <= BUNDLE_TOTAL_MAX == 14000, (
        f"total_chars={bundle.total_chars} exceeds BUNDLE_TOTAL_MAX={BUNDLE_TOTAL_MAX}"
    )

    # U clipped to exactly the cap.
    assert bundle.u_chars == BUNDLE_U_SECTION_MAX == 2000, (
        f"u_chars={bundle.u_chars}, expected {BUNDLE_U_SECTION_MAX}"
    )
    # A/B/C each clipped to their caps (because inputs were sized to force it).
    assert bundle.a_chars == BUNDLE_A_SECTION_MAX == 3000, (
        f"a_chars={bundle.a_chars}, expected {BUNDLE_A_SECTION_MAX} (clipped)"
    )
    assert bundle.b_chars == BUNDLE_B_SECTION_MAX == 6000, (
        f"b_chars={bundle.b_chars}, expected {BUNDLE_B_SECTION_MAX} (clipped)"
    )
    assert bundle.c_chars == BUNDLE_C_SECTION_MAX == 3000, (
        f"c_chars={bundle.c_chars}, expected {BUNDLE_C_SECTION_MAX} (clipped)"
    )

    # Natural total (14033) > 14000 → truncation suffix applied.
    assert "[bundle truncated at total cap]" in bundle.text, (
        "Expected truncation suffix in bundle text (natural length should "
        "have exceeded BUNDLE_TOTAL_MAX); none found."
    )
    # sha256 witnesses the assembled text is the canonical form.
    assert len(bundle.sha256) == 64 and all(c in "0123456789abcdef" for c in bundle.sha256)


def test_total_chars_exact_14000_when_natural_overshoots():
    """With all sections at caps, total_chars lands at exactly 14000
    (= 13966 content + 34-byte suffix).
    """
    user_msg = _make_user_message_with_uuids(user_chars=5000)
    a_signals = _make_a_signals_maxed(note_count=8)
    b_signals, ai_messages = _make_b_signals_maxed(
        ai_msg_chars=2000, marker_terms_chars=1300,
    )
    c_signals, tree_rows = _make_c_signals_maxed(row_count=12)

    bundle = assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        c_tree_rows=tree_rows,
        ai_tail_messages=ai_messages,
        user_intent_message=user_msg,
    )

    # Natural length was 14033 (>14000); truncation clips to exactly 14000.
    assert bundle.total_chars == BUNDLE_TOTAL_MAX == 14000, (
        f"total_chars={bundle.total_chars}, expected {BUNDLE_TOTAL_MAX}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. A/B/C untouched by U
# ─────────────────────────────────────────────────────────────────────────────


def test_a_b_c_sections_untouched_by_u_presence():
    """A/B/C render at the same boundaries regardless of U presence. U's
    presence does NOT shrink or re-clip A/B/C.
    """
    a_signals = _make_a_signals_maxed(note_count=8)
    b_signals, ai_messages = _make_b_signals_maxed(
        ai_msg_chars=2000, marker_terms_chars=1300,
    )
    c_signals, tree_rows = _make_c_signals_maxed(row_count=12)
    user_msg = _make_user_message_with_uuids(user_chars=5000)

    # WITH user intent message.
    bundle_with_u = assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        c_tree_rows=tree_rows,
        ai_tail_messages=ai_messages,
        user_intent_message=user_msg,
    )
    # WITHOUT user intent message.
    bundle_without_u = assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        c_tree_rows=tree_rows,
        ai_tail_messages=ai_messages,
        user_intent_message=None,
    )

    # A/B/C chars byte-identical between the two runs (U presence is additive only).
    assert bundle_with_u.a_chars == bundle_without_u.a_chars, (
        f"A chars differ: with_u={bundle_with_u.a_chars}, "
        f"without_u={bundle_without_u.a_chars}"
    )
    assert bundle_with_u.b_chars == bundle_without_u.b_chars, (
        f"B chars differ: with_u={bundle_with_u.b_chars}, "
        f"without_u={bundle_without_u.b_chars}"
    )
    assert bundle_with_u.c_chars == bundle_without_u.c_chars, (
        f"C chars differ: with_u={bundle_with_u.c_chars}, "
        f"without_u={bundle_without_u.c_chars}"
    )

    # U differs as expected.
    assert bundle_with_u.u_chars == BUNDLE_U_SECTION_MAX
    assert bundle_without_u.u_chars == 0
    assert bundle_with_u.user_message_included is True
    assert bundle_without_u.user_message_included is False


def test_legacy_no_user_no_new_clipping():
    """Legacy maxed A+B+C transcript (no user) — no NEW clipping introduced.

    The U feature is ADDITIVE: with user_intent_message=None, the U section
    is omitted entirely (``u_chars=0``, ``user_message_included=False``)
    and the bundle length is the legacy natural length
    ``31 (header) + 3000 + 1 + 6000 + 1 + 3000 = 12033`` chars.

    Under the OLD pre-feature total cap of 12000, a natural length of
    12033 would have triggered the truncation suffix (clipped to 12000).
    Under the NEW post-feature total cap of 14000, 12033 < 14000 ⇒ NO
    truncation suffix fires. This is the spec's "no new clipping"
    guarantee: the U feature does not introduce a new clip step on the
    legacy transcript.
    """
    a_signals = _make_a_signals_maxed(note_count=8)
    b_signals, ai_messages = _make_b_signals_maxed(
        ai_msg_chars=2000, marker_terms_chars=1300,
    )
    c_signals, tree_rows = _make_c_signals_maxed(row_count=12)

    bundle = assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        c_tree_rows=tree_rows,
        ai_tail_messages=ai_messages,
        user_intent_message=None,
    )

    # No U → u_chars=0, user_message_included=False.
    assert bundle.u_chars == 0
    assert bundle.user_message_included is False

    # A/B/C caps preserved at pre-existing values (3000/6000/3000).
    assert bundle.a_chars == BUNDLE_A_SECTION_MAX == 3000
    assert bundle.b_chars == BUNDLE_B_SECTION_MAX == 6000
    assert bundle.c_chars == BUNDLE_C_SECTION_MAX == 3000

    # Natural legacy length = 31 + 3000 + 1 + 6000 + 1 + 3000 = 12033 chars.
    # 12033 < BUNDLE_TOTAL_MAX (14000) ⇒ NO truncation suffix fires.
    assert bundle.total_chars == 12033, (
        f"Legacy no-user total_chars={bundle.total_chars}, expected 12033 "
        f"(natural legacy length, no truncation under new cap)"
    )
    # The truncation suffix MUST NOT be present — this is the "no new
    # clipping" guarantee on the legacy transcript.
    assert "[bundle truncated at total cap]" not in bundle.text, (
        "Truncation suffix unexpectedly present in legacy transcript "
        "(natural length 12033 should NOT exceed new total cap 14000)."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Witnesses (u_chars + user_message_included on the FusedBundle)
# ─────────────────────────────────────────────────────────────────────────────


def test_u_witnesses_when_present():
    """Witness contract when user intent IS present: u_chars = post-clip
    U size; user_message_included = True."""
    user_msg = _make_user_message_with_uuids(user_chars=5000)
    a_signals = _make_a_signals_maxed(note_count=3)
    b_signals, ai_messages = _make_b_signals_maxed(ai_msg_chars=200)
    c_signals, tree_rows = _make_c_signals_maxed(row_count=5)

    bundle = assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        c_tree_rows=tree_rows,
        ai_tail_messages=ai_messages,
        user_intent_message=user_msg,
    )

    # Witness: u_chars == cap (post-clip; clipped from >cap).
    assert bundle.u_chars == BUNDLE_U_SECTION_MAX == 2000
    # Witness: user_message_included=True.
    assert bundle.user_message_included is True
    # And the bundle text contains the U section.
    assert "=== SOURCE U:" in bundle.text


def test_u_witnesses_when_absent():
    """Witness contract when user intent IS absent: u_chars = 0,
    user_message_included = False; no SOURCE U block in bundle text."""
    a_signals = _make_a_signals_maxed(note_count=3)
    b_signals, ai_messages = _make_b_signals_maxed(ai_msg_chars=200)
    c_signals, tree_rows = _make_c_signals_maxed(row_count=5)

    bundle = assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        c_tree_rows=tree_rows,
        ai_tail_messages=ai_messages,
        user_intent_message=None,
    )

    # Witness: u_chars=0.
    assert bundle.u_chars == 0
    # Witness: user_message_included=False.
    assert bundle.user_message_included is False
    # Bundle text has no SOURCE U section.
    assert "=== SOURCE U:" not in bundle.text


def test_fused_bundle_witnesses_type_contracts():
    """Type witnesses: u_chars is int, user_message_included is bool.
    Also: FusedBundle is the dataclass type returned (not a tuple/dict).
    """
    a_signals = _make_a_signals_maxed(note_count=2)
    b_signals, ai_messages = _make_b_signals_maxed(ai_msg_chars=100)
    c_signals, tree_rows = _make_c_signals_maxed(row_count=2)

    bundle_present = assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        c_tree_rows=tree_rows,
        ai_tail_messages=ai_messages,
        user_intent_message=_make_user_message_with_uuids(300),
    )
    bundle_absent = assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        c_tree_rows=tree_rows,
        ai_tail_messages=ai_messages,
        user_intent_message=None,
    )

    assert isinstance(bundle_present, FusedBundle)
    assert isinstance(bundle_absent, FusedBundle)
    assert isinstance(bundle_present.u_chars, int)
    assert isinstance(bundle_absent.u_chars, int)
    assert isinstance(bundle_present.user_message_included, bool)
    assert isinstance(bundle_absent.user_message_included, bool)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Eval-row fields carry U witnesses
# ─────────────────────────────────────────────────────────────────────────────


def test_emit_resolver_eval_row_carries_u_witnesses_present(caplog):
    """``emit_resolver_eval_row`` logs ``bundle_u_chars=`` and
    ``user_message_included=`` fields with the bundle's witness values
    (present case: u_chars=2000, user_message_included=True)."""
    user_msg = _make_user_message_with_uuids(user_chars=5000)
    a_signals = _make_a_signals_maxed(note_count=3)
    b_signals, ai_messages = _make_b_signals_maxed(ai_msg_chars=200)
    c_signals, tree_rows = _make_c_signals_maxed(row_count=5)

    bundle = assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        c_tree_rows=tree_rows,
        ai_tail_messages=ai_messages,
        user_intent_message=user_msg,
    )

    activation = ActivationResult(
        fired=True,
        band="marker",
        terms_fired=("b_fires",),
        fail_open=False,
        meta_bypass=False,
        bypass_reason="none",
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        bundle=bundle,
    )
    snapshot = ResolverEvalSnapshot(
        result=activation,
        would_be_outcome="would_hint",
        old_decision_value="hint",
        agreement=True,
        instance_id="11111111-2222-3333-4444-555555555555",
        gate_location="test",
        leader_prompt_version="v1",
        mode="enforce",
    )

    with caplog.at_level(logging.INFO, logger=_RESOLVER_LOGGER_NAME):
        emit_resolver_eval_row(
            snapshot,
            judge_invoked=False,
            judge_verdict="<none>",
            resolver_outcome="allow_hint",
        )

    # Aggregate all captured messages and assert the additive U-witness
    # fields appear with the expected values.
    log_text = "\n".join(rec.message for rec in caplog.records)
    assert "bundle_u_chars=2000" in log_text, (
        f"bundle_u_chars=2000 not found in resolver_eval log:\n{log_text}"
    )
    assert "user_message_included=True" in log_text, (
        f"user_message_included=True not found in resolver_eval log:\n{log_text}"
    )
    # And the standard row anchors are present (sanity).
    assert "event=leader_completion_resolver_eval " in log_text, (
        f"Row anchor event=leader_completion_resolver_eval not found:\n{log_text}"
    )
    assert "bundle_sha256=" in log_text
    assert "bundle_size_chars=" in log_text


def test_emit_resolver_eval_row_carries_u_witnesses_absent(caplog):
    """``emit_resolver_eval_row`` logs ``bundle_u_chars=0`` and
    ``user_message_included=False`` when no user_intent_message is present."""
    a_signals = _make_a_signals_maxed(note_count=3)
    b_signals, ai_messages = _make_b_signals_maxed(ai_msg_chars=200)
    c_signals, tree_rows = _make_c_signals_maxed(row_count=5)

    bundle = assemble_fused_bundle(
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        c_tree_rows=tree_rows,
        ai_tail_messages=ai_messages,
        user_intent_message=None,
    )

    activation = ActivationResult(
        fired=True,
        band="marker",
        terms_fired=("b_fires",),
        fail_open=False,
        meta_bypass=False,
        bypass_reason="none",
        a_signals=a_signals,
        b_signals=b_signals,
        c_signals=c_signals,
        bundle=bundle,
    )
    snapshot = ResolverEvalSnapshot(
        result=activation,
        would_be_outcome="would_hint",
        old_decision_value="hint",
        agreement=True,
        instance_id=None,
        gate_location="test",
        leader_prompt_version="v1",
        mode="enforce",
    )

    with caplog.at_level(logging.INFO, logger=_RESOLVER_LOGGER_NAME):
        emit_resolver_eval_row(
            snapshot,
            judge_invoked=False,
            judge_verdict="<none>",
            resolver_outcome="allow_hint",
        )

    log_text = "\n".join(rec.message for rec in caplog.records)
    assert "bundle_u_chars=0" in log_text, (
        f"bundle_u_chars=0 not found in resolver_eval log:\n{log_text}"
    )
    assert "user_message_included=False" in log_text, (
        f"user_message_included=False not found in resolver_eval log:\n{log_text}"
    )