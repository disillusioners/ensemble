"""Unit tests for the LCA mid-work marker scanner (incident b08f40fe).

Tests the pure-function surface of
:mod:`daemon.services.attestation_marker_scanner`:

* :class:`MarkerScanResult` shape — every field is present and the
  dataclass is immutable (frozen-NamedTuple semantics).
* :data:`MID_WORK_MARKERS` catalog size (12-18 per the brief balance).
* :func:`scan_for_mid_work_markers` matrix:
  - **Positives** — the VERBATIM incident phrase "Awaiting final four:
    C12a/b/c + blame-worker. Then I aggregate and write RESULTS.
    Ending turn." (must fire), each catalog entry (must fire when
    standing alone in a message), multi-marker cap at 8.
  - **Benign negatives** — "nothing pending, all shipped", "done",
    "completed", "I delivered the report", plain completion reports;
    must NOT fire.
  - **Case-insensitivity** — uppercase, mixed case, lowercase all
    fire on the same marker.
  - **Window semantics** — only the last ``window`` AIMessages are
    inspected (a marker in an older AIMessage is invisible).
  - **Non-AI messages** — HumanMessages / ToolMessages / SystemMessages
    never contribute (the scan is AIMessage-only).
  - **List-content flattening** — list-of-blocks content
    (LangChain text + reasoning blocks) is flattened to plain text
    before scanning.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from daemon.services.attestation_marker_scanner import (
    MARKER_TERMS_LIST_CAP,
    MID_WORK_MARKERS,
    MarkerScanResult,
    scan_for_mid_work_markers,
)


# ─────────────────────────────────────────────────────────────────────────────
# Catalog + result-shape pins (catalog size, marker_terms cap)
# ─────────────────────────────────────────────────────────────────────────────


def test_catalog_size_is_within_brief_balance_range():
    """Brief: 12-18 markers (balance recall vs false positives)."""
    assert 12 <= len(MID_WORK_MARKERS) <= 18, (
        f"MID_WORK_MARKERS has {len(MID_WORK_MARKERS)} entries; "
        "must be 12-18 per the brief balance"
    )


def test_marker_terms_list_cap_is_pinned():
    """The marker_terms list is capped at 8 distinct entries."""
    assert MARKER_TERMS_LIST_CAP == 8


def test_catalog_has_no_duplicate_entries():
    """No duplicates — duplicates would inflate marker_terms silently."""
    assert len(set(MID_WORK_MARKERS)) == len(MID_WORK_MARKERS)


def test_catalog_entries_are_lowercase_strings():
    """Every entry is a non-empty lowercase string (case-insensitive
    match requires lowercase catalog)."""
    for marker in MID_WORK_MARKERS:
        assert isinstance(marker, str)
        assert marker == marker.lower(), (
            f"marker {marker!r} is not lowercase — case-insensitive "
            "match requires lowercase catalog entries"
        )
        assert marker.strip() == marker, (
            f"marker {marker!r} has leading/trailing whitespace"
        )


def test_marker_scan_result_default_is_no_hit():
    """Default MarkerScanResult fields are pin-able."""
    result = MarkerScanResult(
        marker_hit=False,
        marker_terms=(),
        messages_scanned=0,
    )
    assert result.marker_hit is False
    assert result.marker_terms == ()
    assert result.messages_scanned == 0


# ─────────────────────────────────────────────────────────────────────────────
# Positives — the VERBATIM incident phrase + catalog fire matrix
# ─────────────────────────────────────────────────────────────────────────────


def test_scan_fires_on_verbatim_b08f40fe_phrase():
    """The incident VERBATIM line MUST fire (incident-class killer).

    Note: the incident line contains "will write" only as a sub-phrase
    of "will write RESULTS" — substring scan on "will write" still
    matches; the test pins the markers that DO fire rather than
    asserting on every catalog entry.
    """
    verbatim = (
        "Awaiting final four: C12a/b/c + blame-worker. "
        "Then I aggregate and write RESULTS. Ending turn."
    )
    result = scan_for_mid_work_markers(
        [AIMessage(content=verbatim)], window=3
    )
    assert result.marker_hit is True
    # "ending turn", "then i aggregate", and "awaiting" all match as
    # substrings of the verbatim line. Pin the marker set is non-empty
    # and includes the load-bearing phrases.
    expected = {"ending turn", "then i aggregate", "awaiting"}
    assert expected.issubset(set(result.marker_terms)), (
        f"expected {expected} subset of marker_terms, got "
        f"{result.marker_terms!r}"
    )
    assert result.messages_scanned == 1


def test_scan_fires_on_every_catalog_entry():
    """Every catalog entry, standing alone in a single AIMessage,
    must fire (catalog-coverage pin)."""
    for marker in MID_WORK_MARKERS:
        # Single-marker message; the same lowercased text.
        result = scan_for_mid_work_markers(
            [AIMessage(content=marker.upper())], window=3
        )
        assert result.marker_hit is True, (
            f"catalog entry {marker!r} did not fire on a "
            f"message containing it (case-insensitive)"
        )
        assert marker in result.marker_terms, (
            f"catalog entry {marker!r} fired but missing from "
            f"marker_terms (got {result.marker_terms!r})"
        )


def test_scan_marker_terms_in_catalog_order():
    """marker_terms is ordered by catalog order (deterministic across calls)."""
    # Two markers that appear in catalog order — verify ordering.
    result = scan_for_mid_work_markers(
        [
            AIMessage(
                content="Awaiting standby. Then I aggregate. Will write."
            )
        ],
        window=3,
    )
    terms = list(result.marker_terms)
    # "awaiting" comes first in catalog; "standby"/"stand by" comes later.
    # "then i aggregate" is index 3; "will write" is index 5.
    assert "awaiting" in terms
    if "standby" in terms and "then i aggregate" in terms:
        # Catalog position: awaiting=2, then i aggregate=3, will write=5,
        # standby=14, stand by=15.
        awaiting_idx = terms.index("awaiting")
        aggregate_idx = terms.index("then i aggregate")
        assert awaiting_idx < aggregate_idx
    # Order matches the catalog — explicit membership check.
    catalog_order = [m for m in MID_WORK_MARKERS if m in terms]
    assert terms == catalog_order


def test_scan_marker_terms_list_is_capped():
    """marker_terms is capped at MARKER_TERMS_LIST_CAP entries."""
    # Construct a message that triggers many markers.
    big_text = " ".join(m.upper() for m in MID_WORK_MARKERS)
    result = scan_for_mid_work_markers(
        [AIMessage(content=big_text)], window=3
    )
    assert result.marker_hit is True
    assert len(result.marker_terms) <= MARKER_TERMS_LIST_CAP


# ─────────────────────────────────────────────────────────────────────────────
# Case-insensitivity
# ─────────────────────────────────────────────────────────────────────────────


def test_scan_is_case_insensitive():
    """Uppercase / mixed-case / lowercase all fire on the same marker."""
    base = "Ending turn — awaiting your reply"
    upper = scan_for_mid_work_markers(
        [AIMessage(content=base.upper())], window=3
    )
    mixed = scan_for_mid_work_markers(
        [AIMessage(content="ENDING Turn — Awaiting your reply")], window=3
    )
    lower = scan_for_mid_work_markers(
        [AIMessage(content=base.lower())], window=3
    )
    for result in (upper, mixed, lower):
        assert result.marker_hit is True
        assert "ending turn" in result.marker_terms or "awaiting" in result.marker_terms


# ─────────────────────────────────────────────────────────────────────────────
# Benign negatives — legitimate completions must NOT fire
# ─────────────────────────────────────────────────────────────────────────────


def test_scan_does_not_fire_on_legitimate_completion_phrases():
    """Genuine completion prose must NOT fire any marker.

    Note: the marker scan is the TRIGGER half; the judge is the
    VERDICT. Marker hits on ambiguous prose are EXPECTED (the judge
    filters them). The test pins the unambiguous-completion phrases
    that the operator log row would NEVER surface a marker hit for
    (these are the "no judge call needed" tail of the distribution).
    """
    benign_messages = [
        "All work shipped. Done.",
        "I've completed the task as requested.",
        "Nothing pending — everything is finished and out the door.",
        "Summary delivered above; ready for next request.",
        "All four patches landed; C12a/b/c merged with passing tests.",
        "The RESULTS are in: X, Y, Z succeeded with evidence; "
        "no follow-ups required.",
        "Report delivered above; attesting completion.",
    ]
    for text in benign_messages:
        result = scan_for_mid_work_markers(
            [AIMessage(content=text)], window=3
        )
        assert result.marker_hit is False, (
            f"benign message fired a marker: {text!r} -> "
            f"marker_terms={result.marker_terms!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Window semantics — only the LAST ``window`` AIMessages are inspected
# ─────────────────────────────────────────────────────────────────────────────


def test_scan_only_inspects_last_window_ai_messages():
    """A marker in an older AIMessage (outside the window) is invisible."""
    old_marker = AIMessage(content="Ending turn — old message")
    plain = AIMessage(content="Just plain prose, no markers here.")
    messages = [old_marker, plain, plain, plain]
    # window=2 — only the last 2 AIMessages are inspected; the
    # marker-bearing AIMessage is OUTSIDE the window.
    result = scan_for_mid_work_markers(messages, window=2)
    assert result.marker_hit is False
    assert result.messages_scanned == 2


def test_scan_inspects_inside_window_messages():
    """A marker in a window-eligible AIMessage DOES fire."""
    plain = AIMessage(content="Just plain prose.")
    marker_msg = AIMessage(content="Awaiting final reply.")
    messages = [plain, marker_msg]
    result = scan_for_mid_work_markers(messages, window=3)
    assert result.marker_hit is True
    assert "awaiting" in result.marker_terms


def test_scan_window_clamped_to_at_least_one():
    """window<1 is clamped to 1 (degenerate defensive floor)."""
    marker_msg = AIMessage(content="Awaiting reply.")
    messages = [marker_msg]
    # window=0 must NOT skip the scan entirely.
    result = scan_for_mid_work_markers(messages, window=0)
    assert result.messages_scanned == 1
    assert result.marker_hit is True


def test_scan_messages_scanned_reflects_actual_ai_inspections():
    """messages_scanned is the count of AIMessages actually walked."""
    messages = [
        HumanMessage(content="user-1"),
        AIMessage(content="ending turn"),
        ToolMessage(content="tool-1", tool_call_id="t1"),
        SystemMessage(content="sys"),
        AIMessage(content="just plain prose"),
    ]
    result = scan_for_mid_work_markers(messages, window=10)
    # Two AIMessages are walked.
    assert result.messages_scanned == 2


# ─────────────────────────────────────────────────────────────────────────────
# Non-AI messages are invisible
# ─────────────────────────────────────────────────────────────────────────────


def test_scan_ignores_human_tool_and_system_messages():
    """The marker substring scan operates ONLY on AIMessage content."""
    messages = [
        HumanMessage(content="ending turn"),  # would fire IF scanned
        ToolMessage(content="ending turn", tool_call_id="t1"),
        SystemMessage(content="ending turn"),
        AIMessage(content="plain prose, no markers"),
    ]
    result = scan_for_mid_work_markers(messages, window=10)
    assert result.marker_hit is False
    assert result.messages_scanned == 1


# ─────────────────────────────────────────────────────────────────────────────
# List-content flattening (LangChain text + reasoning blocks)
# ─────────────────────────────────────────────────────────────────────────────


def test_scan_flattens_list_of_blocks_content():
    """An AIMessage with list-of-blocks content is flattened to plain text."""
    blocks = [
        {"type": "text", "text": "Awaiting"},
        {"type": "reasoning", "text": "thinking through the next step"},
        {"type": "text", "text": "your reply."},
    ]
    result = scan_for_mid_work_markers(
        [AIMessage(content=blocks)], window=3
    )
    assert result.marker_hit is True
    assert "awaiting" in result.marker_terms


def test_scan_handles_empty_content_gracefully():
    """An AIMessage with empty/None content does NOT fire (defensive)."""
    result = scan_for_mid_work_markers(
        [AIMessage(content="")], window=3
    )
    assert result.marker_hit is False
    assert result.messages_scanned == 1


# ─────────────────────────────────────────────────────────────────────────────
# Empty / non-AI tail behavior
# ─────────────────────────────────────────────────────────────────────────────


def test_scan_returns_no_hit_on_empty_message_list():
    """An empty message list yields marker_hit=False, messages_scanned=0."""
    result = scan_for_mid_work_markers([], window=3)
    assert result.marker_hit is False
    assert result.marker_terms == ()
    assert result.messages_scanned == 0


def test_scan_returns_no_hit_on_only_non_ai_messages():
    """A tail of only HumanMessages / ToolMessages yields no hit."""
    messages = [
        HumanMessage(content="ending turn"),
        ToolMessage(content="ending turn", tool_call_id="t1"),
    ]
    result = scan_for_mid_work_markers(messages, window=3)
    assert result.marker_hit is False
    assert result.messages_scanned == 0


# ─────────────────────────────────────────────────────────────────────────────
# Multi-marker message
# ─────────────────────────────────────────────────────────────────────────────


def test_scan_returns_distinct_markers_for_multi_marker_message():
    """A message with multiple distinct markers returns each one once."""
    text = (
        "Awaiting your reply. Then I aggregate the results. "
        "Will write the report."
    )
    result = scan_for_mid_work_markers(
        [AIMessage(content=text)], window=3
    )
    assert result.marker_hit is True
    # Distinct markers, in catalog order.
    assert "awaiting" in result.marker_terms
    assert "then i aggregate" in result.marker_terms
    assert "will write" in result.marker_terms
    # No duplicates.
    assert len(result.marker_terms) == len(set(result.marker_terms))


def test_scan_walks_backward_through_messages():
    """Backward walk: messages are inspected newest-first within window."""
    messages = [
        AIMessage(content="plain"),
        AIMessage(content="plain"),
        AIMessage(content="Ending turn."),
    ]
    # window=2 — only the last 2 AIMessages are walked; the FIRST
    # AIMessage is invisible. The "ending turn" lives on the last
    # message, which IS in the window.
    result = scan_for_mid_work_markers(messages, window=2)
    assert result.marker_hit is True
    assert "ending turn" in result.marker_terms


# ─────────────────────────────────────────────────────────────────────────────
# Marker-pattern catalog content pins (anti-drift)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "marker",
    [
        "ending turn",
        "ending my turn",
        "awaiting",
        "then i aggregate",
        "then i compile",
        "will write",
        "will aggregate",
        "not a completion report",
        "interim",
        "in progress",
        "not yet complete",
        "still pending",
        "to be continued",
        "will report back",
        "standby",
        "stand by",
    ],
)
def test_catalog_marker_pin(marker: str):
    """The full catalog is pinned — guards against silent drift."""
    assert marker in MID_WORK_MARKERS