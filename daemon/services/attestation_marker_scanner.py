"""Mid-work marker scanner for the LCA leader completion gate (2026-09-11).

Companion to :mod:`daemon.services.attestation_scanner`. The marker
scanner is the TRIGGER half of a two-stage "marker → judge" disambiguator
on the gate's ALLOW path. The marker patterns are CHEAP, case-insensitive
substring matches over the LAST ``window`` AIMessages; a marker hit flips
the would-be allow into a judge call. The judge is the VERDICT half —
the existing inline-LLM judge service at
:mod:`daemon.services.attestation_report_judge` (Phase 6 fastfollow,
2026-09-07) is REUSED, untouched.

Why a separate trigger stage
----------------------------

The existing scanner at :mod:`daemon.services.attestation_scanner`
detects the ``attest_completion`` tool call in the window — an explicit
contract. There is a class of leader final messages where the leader
LLM has NOT called ``attest_completion`` AND the gate is otherwise
configured to ALLOW (no children dispatched since the last real user
message — the conditional gate is OFF; OR there IS a child dispatched
but the leader's last AIMessage reads "Awaiting final four: C12a/b/c +
blame-worker. Then I aggregate and write RESULTS. Ending turn." — the
leader's prose is mid-work, but the gate's allow path is open because
the conditional/wakeup arms permit it).

Incident **b08f40fe** (2026-09-11) is the canonical example: leader
``b08f40fe`` produced a final AIMessage with VERBATIM text
"Awaiting final four: C12a/b/c + blame-worker. Then I aggregate and
write RESULTS. Ending turn." while four grandchildren were IDLE-orphans
(never dispatched — zero work en route). The gate's conditional +
live-descendants arms all resolved to allow; the leader instance
completed silently. A marker hit would have triggered the judge →
judge-not-complete → CONVERT TO DENY + nudge, killing the bug class.

Pattern design (recall vs false-positive balance)
-------------------------------------------------

The pattern list is curated for HIGH RECALL on the incident family at
a tolerable false-positive rate. The judge is the verdict and is
explicitly CONSERVATIVE — false-positive marker hits cost at most one
extra judge call per would-be allow and the judge confirms/denies.
The judge verdict, not the marker hit, drives the routing.

A legitimate completion report may say "nothing pending, all shipped"
or "done." Those phrases do NOT appear in :data:`MID_WORK_MARKERS`;
they survive the scan and the gate continues to allow as before.

A genuine bare completion (no children dispatched) is a quick-answer
turn — the conditional gate is already OFF for it; the marker scan
runs (cheap), finds no markers, and the gate continues to allow. The
brief example "Ending turn, will continue after your reply" is a
self-correcting marker hit — the judge (judge-yes on that prose) would
ALLOW.

Marker hit does NOT modify the gate's primary decision value. The
``Decision`` enum is unchanged. The marker scan emits additive fields
on :class:`GateDecision` (``marker_hit``, ``marker_terms``,
``marker_path``) and is the entry point to the new routing — judge
verdict converts a marker-hit allow into one of:
  * (a) deny + nudge — nothing pending;
  * (b) allow + checkpoint-durable hint — real pending work;
  * (c) allow normally — judge confirmed a genuine report.

Kill-switch coupling
--------------------

The marker path respects the EXISTING judge kill-switch
``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`` (default ON). With
the judge disabled, marker hits are logged but NO judge call fires;
the gate falls through to plain ALLOW in ALL marker cases (the
rationale: marker-only signal is too weak to deny; allow + log).
"""
from __future__ import annotations

import logging
from typing import Iterable, NamedTuple

from langchain_core.messages import AIMessage, BaseMessage

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Marker pattern catalog — incident-grounded 2026-09-11
#
# Each entry is a lowercase substring literal matched case-insensitively
# over the lowercased AIMessage content. Substring semantics match the
# incident phrasing verbatim ("awaiting", "ending turn", "then i
# aggregate", ...) and the prose family the leader LLM commonly produces
# when it is mid-work. The judge is the verdict; markers are the trigger.
#
# Balance notes (do not change without rereview):
#   * "ending turn" / "ending my turn" — the canonical b08f40fe phrasing;
#     fired on the verbatim incident line.
#   * "then i aggregate" / "then i compile" / "will write" / "will aggregate"
#     — the b08f40fe sentence-tail pattern ("Awaiting final four: … Then I
#     aggregate and write RESULTS. Ending turn.").
#   * "awaiting" — broad but cheap; the judge filters false positives.
#   * "not a completion report" / "interim" / "in progress" / "not yet
#     complete" / "still pending" / "to be continued" / "will report back"
#     / "standby" — explicit mid-work self-declarations.
#
# Excluded patterns (would false-positive on legitimate completions):
#   * "done" / "completed" / "finished" / "shipped" — bare completion
#     tokens that mark genuine reports.
#   * "summary" — too broad; "in summary, …" is a common completion
#     wrapper.
#   * "results" alone — too broad; RESULTS sections are how the leader
#     writes its report. The compound "then i aggregate and write
#     RESULTS" is captured by "then i aggregate" / "will write".
# ─────────────────────────────────────────────────────────────────────────────

MID_WORK_MARKERS: tuple[str, ...] = (
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
)


#: Cap on the number of distinct marker terms returned in a single
#: :class:`MarkerScanResult.marker_terms` tuple. The list is bounded so
#: log fields stay grep-friendly and the marker_hit log line never
#: grows unbounded on a multi-marker message.
MARKER_TERMS_LIST_CAP: int = 8


class MarkerScanResult(NamedTuple):
    """The marker scanner's verdict + log-ready diagnostics.

    Attributes:
        marker_hit: True when at least one marker substring was found
            across the AIMessages inspected inside the window.
        marker_terms: Distinct marker substrings that fired (capped at
            :data:`MARKER_TERMS_LIST_CAP`; ordered by their first
            occurrence in :data:`MID_WORK_MARKERS`). Empty when
            ``marker_hit`` is False.
        messages_scanned: Number of AIMessages actually inspected
            (≤ window). Mirrors the attestation scanner's diagnostic so
            O8 / operator forensics see the same shape.
    """

    marker_hit: bool
    marker_terms: tuple[str, ...]
    messages_scanned: int


# ─────────────────────────────────────────────────────────────────────────────
# Length trigger — word-count signal on the LAST AIMessage content (2026-09-12)
#
# Mid-work ACKs are often SHORT ("Understood, continuing.", "OK, waiting on
# the tester.") — brevity is an INDEPENDENT signal that the turn is not a
# real completion report. Real completion reports are normally detailed
# (hundreds of words). The markers catch phrasing ("ending turn",
# "awaiting"); the length trigger catches brevity — orthogonal signals that
# the gate composes via ``OR``.
#
# Threshold rationale (user 2026-09-12):
# * < 150 words ⇒ trigger fires (the brevity class). Real completion
#   reports that the leader would put through this gate are routinely
#   detailed; very-short prose on an ALLOW path is suspicious.
# * The 150 word threshold is a MODULE-LEVEL CONSTANT — deliberately NOT
#   env-tunable. One knob fewer; revisit at soak. Operators wanting to
#   disable this trigger set ``ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_
#   ENABLED=0`` (the existing judge kill-switch) and accept the
#   marker-only signal as the trigger.
#
# Word count is whitespace-split on the flattened text (mirrors
# ``_flatten_ai_content`` for list-of-blocks content). Pure function; no
# I/O.
# ─────────────────────────────────────────────────────────────────────────────

#: Word-count threshold below which the last AIMessage is treated as a
#: brevity-class trigger on the gate's ALLOW paths. Module-level constant
#: (NOT env-tunable by design; see module-level length-trigger block).
SHORT_REPORT_WORD_THRESHOLD: int = 150


class LengthScanResult(NamedTuple):
    """The length-trigger scanner's verdict + log-ready diagnostics.

    Attributes:
        length_trigger: True when the flattened last-AIMessage word
            count is strictly less than
            :data:`SHORT_REPORT_WORD_THRESHOLD` (i.e. brevity-class).
        final_word_count: Word count of the flattened LAST AIMessage
            content (whitespace-split on the flattened string).
            ``0`` when the message list is empty or the last AIMessage
            has empty/None content (defensive floor — a 0-word last
            AIMessage is treated as brevity and would fire; the gate's
            ``messages_scanned`` already gates that surface).
        messages_scanned: Number of AIMessages actually inspected
            (≤ window). Mirrors :class:`MarkerScanResult` so the
            operator log row carries the same shape from both halves
            of the trigger.
    """

    length_trigger: bool
    final_word_count: int
    messages_scanned: int


def _flatten_ai_content(content: object) -> str:
    """Normalize an AIMessage's content into a plain string.

    Mirrors :func:`daemon.services.attestation_report_judge._format_window_for_judge`
    semantics: list-of-blocks content (LangChain text + reasoning
    blocks) is flattened to plain text; everything else is coerced via
    ``str(...)``. The marker scan is content-only — it ignores
    tool_calls (a tool_call AIMessage is not the prose the leader
    would put markers in).
    """
    if isinstance(content, list):
        flat_parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                flat_parts.append(str(block.get("text", "")))
            else:
                flat_parts.append(str(block))
        return " ".join(flat_parts)
    return str(content) if content else ""


def _matches_any_marker(lower_content: str, markers: Iterable[str]) -> tuple[str, ...]:
    """Return the distinct markers that fired against ``lower_content``.

    Args:
        lower_content: The already-lowercased AIMessage content.
        markers: The marker pattern tuple to scan against (already
            lowercase).

    Returns:
        Tuple of matching markers (capped at
        :data:`MARKER_TERMS_LIST_CAP`; ordered by their first
        occurrence in :data:`MID_WORK_MARKERS`).
    """
    fired: list[str] = []
    for marker in markers:
        if marker in lower_content:
            fired.append(marker)
            if len(fired) >= MARKER_TERMS_LIST_CAP:
                break
    return tuple(fired)


def scan_for_mid_work_markers(
    messages: list[BaseMessage],
    window: int,
) -> MarkerScanResult:
    """Scan the last ``window`` AIMessages for mid-work markers.

    Pure function; no I/O. Case-insensitive substring match against
    :data:`MID_WORK_MARKERS`. The walk is BACKWARD through the message
    list and stops after ``window`` AIMessages (matching the
    attestation scanner's bounded-walk semantics).

    Args:
        messages: The in-node message list (``state["messages"]``).
            The caller passes the live LangGraph state — the scanner
            performs NO checkpoint access.
        window: How many tail AIMessages to inspect. Values < 1 are
            clamped to 1 (degenerate defensive floor).

    Returns:
        :class:`MarkerScanResult` — marker_hit, marker_terms (capped
        list of firing markers in catalog order), messages_scanned.
    """
    bounded_window = max(1, int(window) if window is not None else 1)
    scanned: list[str] = []
    fired_union: set[str] = set()
    # Reverse walk to mirror the attestation scanner's bounded semantics.
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, AIMessage):
            continue
        flat_content = _flatten_ai_content(getattr(message, "content", ""))
        lower_content = flat_content.lower()
        fired = _matches_any_marker(lower_content, MID_WORK_MARKERS)
        if fired:
            fired_union.update(fired)
        scanned.append(lower_content)
        if len(scanned) >= bounded_window:
            break
    # Order marker_terms by catalog order so log fields are
    # deterministic across calls (catalog iteration order is the
    # contract — the catalog is a tuple, not a set).
    ordered_terms: list[str] = []
    for marker in MID_WORK_MARKERS:
        if marker in fired_union:
            ordered_terms.append(marker)
            if len(ordered_terms) >= MARKER_TERMS_LIST_CAP:
                break
    return MarkerScanResult(
        marker_hit=bool(fired_union),
        marker_terms=tuple(ordered_terms),
        messages_scanned=len(scanned),
    )


def count_words(text: str) -> int:
    """Count whitespace-separated words in ``text``.

    Pure helper. The length-trigger scanner counts words on the
    FLATTENED last-AIMessage content (the same shape the marker
    scanner substring-matches against). Whitespace-split is the
    contract: ``text.split()`` with no args collapses any run of
    whitespace and strips leading/trailing whitespace. Empty / None
    inputs return ``0`` (defensive floor).

    Args:
        text: The flattened AIMessage content.

    Returns:
        int word count (≥0).
    """
    if not text:
        return 0
    return len(text.split())


def scan_for_short_final_ai(
    messages: list[BaseMessage],
    window: int,
) -> LengthScanResult:
    """Scan the last AIMessage for brevity (< SHORT_REPORT_WORD_THRESHOLD words).

    Pure function; no I/O. Walks BACKWARD through ``messages`` and
    inspects up to ``window`` AIMessages (mirroring the marker
    scanner's bounded-walk semantics), but only the FIRST AIMessage
    encountered (the newest in the tail) contributes its word count
    to the verdict — the length trigger is a single-message signal,
    not an aggregate. The walk is bounded so the function returns
    deterministically and ``messages_scanned`` matches the marker
    scanner's surface.

    Args:
        messages: The in-node message list (``state["messages"]``).
        window: How many tail AIMessages to inspect when locating the
            last AIMessage. Values < 1 are clamped to 1.

    Returns:
        :class:`LengthScanResult` — length_trigger (True when the
        last AIMessage word count is < :data:`SHORT_REPORT_WORD_
        THRESHOLD`), final_word_count (int, ≥0), messages_scanned.
    """
    bounded_window = max(1, int(window) if window is not None else 1)
    final_word_count: int = 0
    scanned: int = 0
    found_ai: bool = False
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, AIMessage):
            continue
        flat_content = _flatten_ai_content(getattr(message, "content", ""))
        final_word_count = count_words(flat_content)
        scanned += 1
        found_ai = True
        # The LAST (newest) AIMessage in the tail is the only one we
        # count. Break immediately after the first AIMessage hit —
        # the walk bound just protects against degenerate empty
        # message lists (where there's nothing to scan).
        break
    # Degenerate tail (no AIMessage at all — empty list or only
    # non-AI messages): length_trigger is False. The marker scanner
    # returns marker_hit=False on the same degenerate state; the
    # length trigger mirrors that contract. Without an AIMessage to
    # inspect there's nothing to measure — treating "no message" as
    # "0 words = brevity" would spuriously fire the judge on the
    # gate's most degenerate paths.
    if not found_ai:
        return LengthScanResult(
            length_trigger=False,
            final_word_count=0,
            messages_scanned=0,
        )
    length_trigger = final_word_count < SHORT_REPORT_WORD_THRESHOLD
    return LengthScanResult(
        length_trigger=length_trigger,
        final_word_count=final_word_count,
        messages_scanned=scanned,
    )


#: Re-export the catalog so tests and the gate can pin the literal
#: list from one source of truth. Re-exports avoid duplicating the
#: constant across modules.
ALL_MARKERS: tuple[str, ...] = MID_WORK_MARKERS


__all__ = [
    "MID_WORK_MARKERS",
    "MARKER_TERMS_LIST_CAP",
    "MarkerScanResult",
    "scan_for_mid_work_markers",
    "ALL_MARKERS",
    "SHORT_REPORT_WORD_THRESHOLD",
    "LengthScanResult",
    "count_words",
    "scan_for_short_final_ai",
]


# Sanity guard — the catalog should never be empty (the marker scan is
# the trigger half of a two-stage disambiguator; an empty catalog
# would make the scan a no-op). The marker count should be in the
# 12-18 range documented in the brief (balance recall vs false
# positives). Pinned by the unit suite. Raised as an explicit
# RuntimeError (not a bare ``assert``) so the catalog pin survives
# ``python -O`` (which strips assertions).
if not 12 <= len(MID_WORK_MARKERS) <= 18:
    raise RuntimeError(
        "MID_WORK_MARKERS must hold 12-18 patterns per the brief; "
        "see module docstring + tests/unit/test_attestation_marker_scanner.py"
    )