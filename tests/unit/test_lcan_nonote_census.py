"""LCA advisory-note-removal NO-NOTE verification (Job 4, 2026-09-18).

Proves the graves hold post-merge on branch
``feature/lca-remove-advisory-note @ 0a4fccb1`` (base ``858b1038``):

  (a) **CATALOG PIN** — ``CHILD_TERMINAL_PROMISE_MARKERS`` is exactly
      17 entries in the exact expected ordering; the scanner module
      source at HEAD equals the base ``858b1038`` version
      (sha256-verified). A silent drift in catalog/predicate text
      fails the gate loudly.

  (b) **NO-MINT CENSUS** — running a delegated-mission evaluation
      through ``collect_source_a_signals`` (resolver-level in-process
      scenario — NO graph fixture, NO LLM, NO DB; the function is a
      pure walker over the leader's ``state["messages"]`` projection)
      does NOT mint any message with
      ``context_kind == CONTEXT_KIND_CHILD_REPORT_CHECK`` anywhere —
      neither in the input, nor in any side-effect, nor in the
      produced evidence. The 17-pattern catalog still drives the LIVE
      A-signal via the ``internal_report:`` transcript scan; no
      separate advisory note is emitted.

  (c) **LEGACY READ-PATH PRESENCE** — the ``_is_child_report_check_note``
      defense-in-depth detector survives; a synthetic pre-removal
      advisory note (structured-kwargs surface AND content-prefix
      fallback) is correctly classified as an A-band source. Any
      historical checkpoint state carrying a Stage-0-minted note is
      still recognized for re-evaluation.

Companion to ``tests/unit/test_attestation_stage3_census.py`` (the
Stage-3 retirement negative-pin census) and
``tests/unit/test_child_terminal_contradiction.py`` (the catalog
membership pin; its Stage-0 producer tests are DELETED per the
delta).

Delta recap (git diff 858b1038..0a4fccb1 -- daemon/):
  * daemon/services/child_reports.py          -339 LoC (note-mint block)
  * daemon/services/context_messages.py        -47 LoC (stable-id branch
                                                  + docstring; enum KEPT)
  * daemon/services/attestation_resolver_activation.py +288 LoC
                                                  (A-signal from transcript
                                                   scan + legacy detector)
  * daemon/services/attestation_marker_scanner.py  UNCHANGED
"""
from __future__ import annotations

import hashlib
import subprocess
import uuid
from pathlib import Path
from typing import Sequence

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from daemon.services.attestation_marker_scanner import (
    CHILD_TERMINAL_PROMISE_MARKERS,
    scan_child_terminal_report_for_promises,
)
from daemon.services.attestation_resolver_activation import (
    SourceASignals,
    _is_child_report_check_note,
    _is_child_report_message,
    collect_source_a_signals,
)
from daemon.services.context_messages import CONTEXT_KIND_CHILD_REPORT_CHECK


# ─────────────────────────────────────────────────────────────────────────
# (a) CATALOG PIN — 17 patterns, byte-identical to base 858b1038
# ─────────────────────────────────────────────────────────────────────────


# The exact 17-entry catalog, written out verbatim in canonical order.
# Source of truth: daemon/services/attestation_marker_scanner.py:258-294
# (CHILD_TERMINAL_PROMISE_MARKERS tuple). If this tuple ever drifts from
# the production source, the assertion below fails LOUD — never silent.
_EXPECTED_CATALOG: tuple[str, ...] = (
    "ending turn",
    "ending my turn",
    "awaiting",
    "then i ",
    "then i'll",
    "will write",
    "will aggregate",
    "will compile",
    "will continue",
    "will report back",
    "to be continued",
    "in progress",
    "still pending",
    "not yet complete",
    "standby",
    "stand by",
    "interim",
)

# Pre-computed sha256 of attestation_marker_scanner.py at base 858b1038
# (and confirmed unchanged at HEAD 0a4fccb1 by the discovery lane).
_BASE_SCANNER_SHA256 = (
    "369342f52b9ab20c6aa8c2f5cadc7aa83b4e881de108b4df95cc6a3d843c9774"
)
_BASE_COMMIT = "858b1038"
_SCANNER_RELPATH = "daemon/services/attestation_marker_scanner.py"


def test_catalog_length_is_17() -> None:
    """Catalog size is exactly 17 (D-CTD-7 contract)."""
    assert len(CHILD_TERMINAL_PROMISE_MARKERS) == 17, (
        f"CHILD_TERMINAL_PROMISE_MARKERS must hold exactly 17 patterns; "
        f"got {len(CHILD_TERMINAL_PROMISE_MARKERS)}"
    )


def test_catalog_contents_byte_identical() -> None:
    """Catalog tuple is the exact expected ordering."""
    assert CHILD_TERMINAL_PROMISE_MARKERS == _EXPECTED_CATALOG, (
        "CHILD_TERMINAL_PROMISE_MARKERS drifted from the 17-pattern "
        "catalog the D-CTD-7 contract pinned. Re-author the constant "
        "AND update _EXPECTED_CATALOG in this test together (single PR)."
    )


def test_attestation_marker_scanner_byte_identical_to_base() -> None:
    """attestation_marker_scanner.py at HEAD equals base 858b1038 (sha256).

    Discovery (other lane) proved this earlier; we re-verify live so
    silent drift in catalog text or predicate logic fails the gate
    LOUDLY. Skipped if git is unavailable in the test environment
    (e.g. packaged wheel without .git).
    """
    try:
        base_proc = subprocess.run(
            ["git", "show", f"{_BASE_COMMIT}:{_SCANNER_RELPATH}"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        pytest.skip(f"git unavailable or base unreachable: {exc!r}")

    base_sha256 = hashlib.sha256(base_proc.stdout.encode("utf-8")).hexdigest()
    worktree_path = (
        Path(__file__).resolve().parents[2] / _SCANNER_RELPATH
    )
    worktree_sha256 = hashlib.sha256(worktree_path.read_bytes()).hexdigest()

    assert worktree_sha256 == base_sha256, (
        f"attestation_marker_scanner.py drifted from base {_BASE_COMMIT}: "
        f"worktree sha256={worktree_sha256[:16]}... "
        f"base sha256={base_sha256[:16]}..."
    )
    # Belt-and-braces — pin the known hash from prior discovery.
    assert worktree_sha256 == _BASE_SCANNER_SHA256, (
        f"sha256 mismatch against prior-discovery pin: "
        f"worktree={worktree_sha256} expected={_BASE_SCANNER_SHA256}"
    )


def test_scan_function_still_detects_all_seeds() -> None:
    """Spot-check the scanner still fires on the spec seeds (catalog live).

    Companion to ``test_child_terminal_contradiction.py``'s
    ``test_spec_seeds_in_catalog`` — verifies the live scan path
    still surfaces every catalog entry. Distinct from the catalog
    membership pin because it tests the scan loop, not just tuple
    equality.
    """
    seeds = {
        "ending turn": "ending turn",
        "ending my turn": "ending my turn",
        "awaiting": "awaiting",
        "then i ": "then i ",
        "then i'll": "then i'll",
        "will write": "will write",
        "will aggregate": "will aggregate",
        "will compile": "will compile",
        "will continue": "will continue",
        "will report back": "will report back",
        "to be continued": "to be continued",
        "in progress": "in progress",
        "still pending": "still pending",
        "not yet complete": "not yet complete",
        "standby": "standby",
        "stand by": "stand by",
        "interim": "interim",
    }
    for spec_name, catalog_entry in seeds.items():
        r = scan_child_terminal_report_for_promises(
            f"Reporter body content. {catalog_entry} (test)"
        )
        assert r.promise_hit is True, (
            f"scanner failed to detect {spec_name!r} via "
            f"catalog entry {catalog_entry!r}"
        )
        assert catalog_entry in r.matched_terms, (
            f"matched_terms missing {catalog_entry!r}; got {r.matched_terms}"
        )


# ─────────────────────────────────────────────────────────────────────────
# (b) NO-MINT CENSUS — runtime proof the graves hold
#
# Approach: RESOLVER-LEVEL IN-PROCESS SCENARIO (no graph fixture, no
# LLM, no DB). ``collect_source_a_signals`` is a pure function over the
# leader's ``state["messages"]`` projection — it walks the messages,
# applies the 17-pattern scan over the LIVE ``internal_report:`` stamped
# rows, and returns a ``SourceASignals`` value. We construct an input
# that mimics a delegated-mission evaluation mid-flight:
#
#   * one user request
#   * one assistant dispatch
#   * one LIVE child-report HumanMessage stamped by the report-injection
#     drain (source="internal_report:<child>:<completed_msg_id>") with
#     a body containing the canonical promise markers
#
# Assertions (three invariants):
#
#   1. INPUT INVARIANT — none of the input messages carry the minted
#      kind (no seeding bias).
#   2. OUTPUT INVARIANT — the catalog hit IS detected, evidence comes
#      from the LIVE ``internal_report:`` path, and ``advisory_present``
#      is True (semantics preserved).
#   3. STATE INVARIANT — input messages are NOT mutated by the call
#      (no ``context_kind=child_report_check`` injected after the
#      call returns; the function is pure, no I/O).
#
# If ANY production code path tried to re-introduce the mint site, an
# ``_is_child_report_check_note`` check elsewhere in the suite would
# also fire — but the strongest proof is the runtime census itself:
# the catalog drives the A-signal via the live stamp; no note is
# ever minted to deliver it.
# ─────────────────────────────────────────────────────────────────────────


def _make_live_child_report(
    child_uuid: str,
    body: str,
    completed_msg_id: str | None = None,
) -> HumanMessage:
    """Synthetic LIVE child-report ``HumanMessage`` (post-D-CTD-7 stamp).

    Mirrors the stamp format at ``daemon/graph.py:6950-6967`` (the
    report-injection drain) and the fallback
    ``_stamped_additional_kwargs`` path at
    ``daemon/services/instance_messaging.py:525``. The ``source`` key
    is the discriminator the resolver A-scan reads.
    """
    if completed_msg_id is None:
        completed_msg_id = str(uuid.uuid4())
    return HumanMessage(
        content=body,
        id=f"internal-report-{child_uuid}-{completed_msg_id}",
        additional_kwargs={
            "injected_message": True,
            "source": f"internal_report:{child_uuid}:{completed_msg_id}",
        },
    )


def test_no_mint_during_live_path_evaluation() -> None:
    """Graves hold at runtime — no ``context_kind=child_report_check``
    appears anywhere in the messages list as a side-effect of
    ``collect_source_a_signals``.
    """
    child_uuid = str(uuid.uuid4())
    completed_msg_id = str(uuid.uuid4())
    child_report = _make_live_child_report(
        child_uuid,
        "Done with sub-task 3/5. Then I will write RESULTS. Ending turn.",
        completed_msg_id,
    )
    user_msg = HumanMessage(content="Please dispatch the work.")
    assistant_msg = AIMessage(content="Dispatched. Awaiting child report.")

    messages: Sequence[BaseMessage] = [user_msg, assistant_msg, child_report]

    # INPUT INVARIANT — none of the inputs carry the minted kind.
    for m in messages:
        kwargs = getattr(m, "additional_kwargs", None) or {}
        assert kwargs.get("context_kind") != CONTEXT_KIND_CHILD_REPORT_CHECK, (
            f"input messages must NOT carry "
            f"context_kind={CONTEXT_KIND_CHILD_REPORT_CHECK!r}; "
            f"found on {m!r}"
        )

    result: SourceASignals = collect_source_a_signals(messages)

    # OUTPUT INVARIANT — the catalog hit IS detected via the live stamp.
    assert result.advisory_present is True, (
        f"live path must detect the catalog hit; got {result!r}"
    )
    assert result.phrase_match is True
    assert result.evidence, "evidence tuple must be non-empty for catalog-hit"
    # The 17-pattern catalog still drives the matched_terms.
    flat_terms = {t for ev in result.evidence for t in ev.matched_terms}
    assert "ending turn" in flat_terms
    assert "will write" in flat_terms
    # The LIVE stamp MUST show up in the evidence (stable_id round-trips).
    live_stable_ids = {
        ev.stable_id for ev in result.evidence if ev.stable_id is not None
    }
    assert child_report.id in live_stable_ids, (
        f"live child report's stable_id must appear in evidence; "
        f"got stable_ids={live_stable_ids!r}"
    )

    # STATE INVARIANT — input messages are NOT mutated; the function
    # is pure (no I/O, no DB, no message-emit side-effect).
    for m in messages:
        kwargs = getattr(m, "additional_kwargs", None) or {}
        assert kwargs.get("context_kind") != CONTEXT_KIND_CHILD_REPORT_CHECK, (
            f"collect_source_a_signals must NOT inject "
            f"context_kind={CONTEXT_KIND_CHILD_REPORT_CHECK!r} onto "
            f"input messages; found on {m!r} after the call"
        )
    # And no message was added to the list (sequence identity preserved).
    assert len(messages) == 3, (
        f"input message sequence must be unchanged; got len={len(messages)}"
    )


def test_no_mint_with_pure_clean_report() -> None:
    """Negative sanity — a clean completion (no markers) yields NO
    A-band signal AND the call is side-effect-free (no note minted).
    """
    child_uuid = str(uuid.uuid4())
    completed_msg_id = str(uuid.uuid4())
    clean = _make_live_child_report(
        child_uuid,
        "All work shipped. PR #42 merged. Done with the full task set.",
        completed_msg_id,
    )
    result = collect_source_a_signals([clean])
    assert result.advisory_present is False
    assert result.phrase_match is False
    assert result.contradiction_flag is False
    assert result.word_count_below_threshold is False
    assert result.promise_terms_total == 0
    assert result.evidence == ()
    # Side-effect-free.
    kwargs = getattr(clean, "additional_kwargs", None) or {}
    assert kwargs.get("context_kind") != CONTEXT_KIND_CHILD_REPORT_CHECK


# ─────────────────────────────────────────────────────────────────────────
# (c) LEGACY READ-PATH PRESENCE — defense-in-depth detector survives
#
# The deleted Stage-0 producer no longer mints advisory notes. But the
# resolver-side ``_is_child_report_check_note`` detector survives as
# defense-in-depth: any historical checkpoint state carrying a note
# minted BEFORE the 6a695b8f removal (which rode above compaction via
# its permanent-hoist flag) must still be recognized for re-evaluation.
#
# These tests are PURE UNIT CALLS to the detector — the dedicated
# legacy-lane test owns the full checkpoint scenario; this census only
# pins that the detector branch EXISTS and CLASSIFIES correctly on both
# surfaces (structured-kwargs AND content-prefix fallback).
# ─────────────────────────────────────────────────────────────────────────


def _make_synthetic_legacy_note(
    body: str = "child_id=abc-123 terms=['awaiting']",
) -> HumanMessage:
    """Synthetic pre-removal advisory note (the deleted producer's shape)."""
    return HumanMessage(
        content=f"[SYSTEM CONTEXT: Child Report Check]\n\n{body}",
        id=f"child_report_check:abc-123:{uuid.uuid4()}",
        additional_kwargs={
            "injected_message": True,
            "context_kind": CONTEXT_KIND_CHILD_REPORT_CHECK,
            "child_report_check": True,
            "child_report_check_terms": ["awaiting"],
            "child_instance_id": "abc-123",
        },
    )


def test_legacy_note_detector_still_works_structured_kwargs() -> None:
    """Detector (path 1 — structured kwargs) classifies a pre-removal note
    as A-band. This is the canonical shape produced by the deleted
    Stage-0 producer (``child_report_check=True`` +
    ``context_kind=child_report_check`` + ``child_report_check_terms``).
    """
    legacy = _make_synthetic_legacy_note()
    assert _is_child_report_check_note(legacy) is True, (
        "legacy detector must classify a structured-kwargs pre-removal "
        "note as A-band (defense-in-depth guarantee)"
    )


def test_legacy_note_via_content_prefix_only() -> None:
    """Detector (path 2 — content-prefix fallback) classifies a note
    whose structured kwargs were LOST (simulating a long-running
    leader whose checkpoint survived the upgrade with only the
    ``[SYSTEM CONTEXT: Child Report Check]`` prefix intact).
    """
    legacy_prefix_only = HumanMessage(
        content=(
            "[SYSTEM CONTEXT: Child Report Check]\n\n"
            "child_id=def-456 terms=['will continue']"
        ),
        id="some-compaction-survived-id",
        additional_kwargs={},  # kwargs lost
    )
    assert _is_child_report_check_note(legacy_prefix_only) is True, (
        "content-prefix fallback path must still classify a "
        "kwarg-stripped legacy note as A-band"
    )


def test_non_legacy_messages_are_not_classified() -> None:
    """Negative sanity — unrelated messages must NOT trip the legacy
    detector (no false-positive on a regular user or assistant
    message, or on a LIVE internal_report stamp).
    """
    user_msg = HumanMessage(content="Please dispatch the work.")
    assistant_msg = AIMessage(content="Dispatched.")
    live = _make_live_child_report(
        str(uuid.uuid4()),
        "Then I will write RESULTS. Ending turn.",
    )
    assert _is_child_report_check_note(user_msg) is False
    assert _is_child_report_check_note(assistant_msg) is False
    # The LIVE stamp does NOT carry the legacy context_kind; the
    # detector must correctly reject it (live messages get picked up
    # by _is_child_report_message, not by the legacy detector).
    assert _is_child_report_check_note(live) is False


def test_live_child_report_detector_recognizes_internal_report_stamp() -> None:
    """Sanity — the LIVE ``_is_child_report_message`` detector
    recognizes the ``internal_report:`` stamp (the post-D-CTD-7 sole
    A-signal source) and rejects everything else.
    """
    child_uuid = str(uuid.uuid4())
    completed_msg_id = str(uuid.uuid4())
    live = _make_live_child_report(child_uuid, "Anything.", completed_msg_id)
    assert _is_child_report_message(live) is True
    plain = HumanMessage(content="Just a plain user message.")
    assert _is_child_report_message(plain) is False
    # AIMessage does not carry the stamp.
    ai_msg = AIMessage(content="Assistant response.")
    assert _is_child_report_message(ai_msg) is False


def test_kind_enum_is_still_defined() -> None:
    """``CONTEXT_KIND_CHILD_REPORT_CHECK`` stays defined as the legacy
    detector reads it. Removing the enum would break defense-in-depth
    without any test catching it — this is the explicit pin.
    """
    assert CONTEXT_KIND_CHILD_REPORT_CHECK == "child_report_check"


def test_legacy_and_live_both_classified_in_same_evaluation() -> None:
    """Combined scenario — when a leader state carries BOTH a historical
    legacy note (rare survivor instance post-upgrade) AND a fresh live
    child report, BOTH evidence rows appear. This pins that the
    defense-in-depth legacy detector is ADDITIVE, not mutually
    exclusive with the live path.

    Discriminator: ``stable_id`` (each ``HumanMessage`` carries a
    distinct LangGraph id we control).
    """
    child_uuid = str(uuid.uuid4())
    completed_msg_id = str(uuid.uuid4())
    live = _make_live_child_report(
        child_uuid,
        "Awaiting your merge decision. Then I will compile results. Ending turn.",
        completed_msg_id,
    )
    legacy = _make_synthetic_legacy_note()

    result = collect_source_a_signals([legacy, live])

    assert result.advisory_present is True
    assert len(result.evidence) == 2, (
        f"both legacy + live evidence rows must be present; "
        f"got {result.evidence!r}"
    )
    stable_ids = {ev.stable_id for ev in result.evidence}
    assert live.id in stable_ids, (
        f"live child report's stable_id must appear in evidence; "
        f"got stable_ids={stable_ids!r}"
    )
    assert legacy.id in stable_ids, (
        f"legacy note's stable_id must appear in evidence (defense-"
        f"in-depth); got stable_ids={stable_ids!r}"
    )