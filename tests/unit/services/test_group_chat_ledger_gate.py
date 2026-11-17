"""The group-chat LEDGER GATE (the P6 approver gate, 2026-11-17).

The four ledger-gate checks (the leader's B-2..B-5 dispositions) were
DOCUMENTATION-LEVEL: no runnable pytest node read the group-chat
decision ledger, so a stale or over-claiming sentence in
``decisions.md`` could not redden anything — the claim was true only
as long as a human happened to re-read it.

This file turns those checks into a RUNNABLE gate. It reads
``.agents/shared/planning/group-chat/decisions.md`` and asserts, per
entry:

* **A-P6-11** — the room's content-free AUDIT ROWS belong to the
  instance's AUDIT TRAIL (NOT to its event stream), the room-resolved
  subscription is its OWN room-scoped content-free channel, and the
  pre-F-3 alias sentence is marked SUPERSEDED.
* **A-P6-12** — the dropped "(+ the census pin)" clause is ABSENT and
  the census's scope (the ``_gate`` CALL COUNT) is stated.
* **A-P6-22** — the C4/C5/C6 enumeration matches the implemented
  checks: the GUIDELINES + no-history claim (C4), the
  cursor-as-durable-record rationale plus the ONE content-free
  ``room_tag_delivery`` audit row (C5), and the PARSED unread COUNT
  (C6).
* **A-P6-2** — the P6 plan-row pointer is FULFILLED and the P6 plan
  row in ``plan-overview.md`` actually carries the §9 cost-cap
  enforcement.

DELIBERATELY non-brittle: every assertion is scoped to ONE entry
paragraph (the entry's own line) and asserts the PRESENCE of the
corrected claim plus the ABSENCE of the dropped clause — never a
whole-file hash (a whole-file hash would break on every unrelated
append, which is how a gate gets switched off).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

PLANNING = REPO_ROOT / ".agents" / "shared" / "planning" / "group-chat"
DECISIONS = PLANNING / "decisions.md"
PLAN_OVERVIEW = PLANNING / "plan-overview.md"

#: The LATEST entry number the ledger is expected to carry. The
#: "no gaps up to whatever max is" shape is BLIND to a deletion of
#: the highest row (the max just shrinks), so the node anchors the
#: expected latest number instead — and every future append MUST
#: bump this constant in the same change that appends the entry.
EXPECTED_LATEST = 36


def _entry(text: str, tag: str) -> str:
    """The ONE paragraph of an ``A-P6-NN`` entry.

    The ledger's entries are single long paragraphs, each opening with
    ``**A-P6-NN ·``. The paragraph is returned verbatim so the
    assertions below can be scoped to it — a change elsewhere in the
    file never breaks this gate, and a change INSIDE the entry always
    does.
    """
    marker = f"**A-P6-{tag} \u00b7"
    matches = [ln for ln in text.splitlines() if ln.startswith(marker)]
    assert len(matches) == 1, (
        f"expected exactly one '{marker}' paragraph in decisions.md, "
        f"found {len(matches)}"
    )
    return matches[0]


def _decisions() -> str:
    assert DECISIONS.exists(), f"missing {DECISIONS}"
    return DECISIONS.read_text(encoding="utf-8")


def test_the_a_p6_11_rationale_no_longer_claims_the_instance_event_stream() -> None:
    """B-2 \u2014 A-P6-11's rationale sentence.

    The old sentence said the room's content-free event rows "ride
    their instance's stream". They do NOT: the audit rows
    (``room_tag_delivery`` / ``room_purged``) are written against an
    ``instance_id`` and live in that instance's AUDIT TRAIL, and the
    room-resolved subscription is its OWN room-scoped content-free
    channel (A-P6-26).
    """
    entry = _entry(_decisions(), "11")
    assert "ride their instance's stream" not in entry, (
        "A-P6-11 still claims the room's event rows ride their "
        "instance's stream — they live in the instance's AUDIT TRAIL"
    )
    assert "AUDIT TRAIL" in entry, (
        "A-P6-11 must name the AUDIT TRAIL as the room rows' home"
    )
    assert "room-scoped CONTENT-FREE channel" in entry, (
        "A-P6-11 must name the room-resolved subscription's own "
        "room-scoped content-free channel"
    )
    assert "SUPERSEDED BY A-P6-26" in entry, (
        "A-P6-11's pre-F-3 alias sentence must be marked SUPERSEDED BY "
        "A-P6-26"
    )


def test_the_a_p6_12_census_clause_is_dropped() -> None:
    """B-3 \u2014 A-P6-12's mutation evidence.

    The census counts the ``_gate`` CALLS, so the "the gate is no
    longer the body's first logic" mutation (which keeps the call)
    leaves the census GREEN. The dropped clause must be ABSENT and the
    census's real scope stated.
    """
    entry = _entry(_decisions(), "12")
    assert "(+ the census pin)" not in entry, (
        "A-P6-12 still claims the census pin reddens when the gate "
        "stops being the first logic — the census counts the CALLS"
    )
    assert "CENSUS pin stays GREEN" in entry, (
        "A-P6-12 must state that the census stays GREEN under that "
        "mutation"
    )


def test_the_a_p6_22_enumeration_matches_the_implemented_checks() -> None:
    """B-4 \u2014 A-P6-22's C4/C5/C6 enumeration.

    C4: the GUIDELINES text + the no-history claim (the pre-roll drop
    and the ``ROOM_DIGEST_LAST_N`` bound). C5: the DELIVERED CURSOR as
    the durable RECORD (Y14) AND the ONE content-free
    ``room_tag_delivery`` audit row (the AUDIT TRAIL) — an inaccurate
    "no event" claim is as wrong as the original over-claim. C6: the
    PARSED unread COUNT (not the bare substring).
    """
    entry = _entry(_decisions(), "22")
    # C4 — the guidelines + the no-history half.
    assert "GUIDELINES text" in entry, "A-P6-22 C4 must name the guidelines text"
    assert "PRE-ROLL lines make the last-N window drop one" in entry, (
        "A-P6-22 C4 must state the pre-roll drop that proves the "
        "no-history claim"
    )
    assert "at most `ROOM_DIGEST_LAST_N` lines" in entry, (
        "A-P6-22 C4 must state the digest window bound"
    )
    # C5 — the cursor is the durable record; the audit row is the trail.
    assert "DELIVERED CURSOR" in entry, (
        "A-P6-22 C5 must name the delivered cursor as the durable record"
    )
    assert "advances at admission (Y14)" in entry, (
        "A-P6-22 C5 must cite the Y14 admission rule"
    )
    assert "CONTENT-FREE `room_tag_delivery` audit row" in entry, (
        "A-P6-22 C5 must state that the success path DOES emit one "
        "content-free room_tag_delivery audit row"
    )
    assert "the CURSOR is the durable RECORD, the row is the AUDIT TRAIL" in entry, (
        "A-P6-22 C5 must separate the durable record from the audit row"
    )
    # C6 — the parsed count.
    assert "PARSED and asserted equal to the room's actual unread count" in entry, (
        "A-P6-22 C6 must state that the count is parsed and compared"
    )
    assert "the loop-guard's empty second note is kept" in entry, (
        "A-P6-22 C6 must keep the loop-guard's empty second note"
    )


def test_the_a_p6_2_plan_row_pointer_is_landed() -> None:
    """B-5 \u2014 A-P6-2's plan-row pointer.

    The pointer is only FULFILLED if the P6 plan row actually carries
    the §9 cost-cap enforcement — a pointer into nothing is not a
    pointer.
    """
    entry = _entry(_decisions(), "2")
    assert "FULFILLED" in entry, (
        "A-P6-2 must record that the plan-row pointer is fulfilled"
    )
    plan = PLAN_OVERVIEW.read_text(encoding="utf-8")
    rows = [ln for ln in plan.splitlines() if ln.startswith("| **P6**")]
    assert len(rows) == 1, f"expected exactly one P6 plan row, found {len(rows)}"
    row = rows[0]
    assert "§9 per-turn post budget" in row, (
        "the P6 plan row must carry the §9 cost-cap enforcement "
        "(A-P6-2's pointer)"
    )
    assert "room_post" in row and "PRE-APPEND" in row, (
        "the P6 plan row must name the room_post pre-append chokepoint"
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_numbering(text: str) -> None:
    """The numbering integrity, as a PURE check over a given ledger text.

    Three teeth: every ``A-P6-NN`` tag opens exactly ONE paragraph; the
    numbers have no gaps; and the LATEST number is the EXPECTED one
    (``EXPECTED_LATEST``) — a deletion of the highest row shrinks the
    max instead of leaving a gap, so only the anchor catches it.
    """
    tags = re.findall(r"^\*\*A-P6-(\d+) \u00b7", text, flags=re.MULTILINE)
    duplicates = sorted({t for t in tags if tags.count(t) > 1})
    assert not duplicates, f"duplicate A-P6 entry numbers: {duplicates}"
    numbers = sorted({int(t) for t in tags})
    assert numbers == list(range(1, max(numbers) + 1)), (
        f"the A-P6 numbering has a gap: {numbers}"
    )
    assert max(numbers) == EXPECTED_LATEST, (
        f"the ledger's latest entry is A-P6-{max(numbers)}, expected "
        f"A-P6-{EXPECTED_LATEST} — an entry was DELETED, or an append "
        f"did not bump EXPECTED_LATEST in the same change"
    )


def test_every_a_p6_entry_number_is_used_exactly_once() -> None:
    """The numbering integrity the two lanes' overlapping proposals
    needed: each ``A-P6-NN`` tag opens exactly ONE paragraph, the
    numbers have no gaps, and the LATEST entry is the expected one."""
    _assert_numbering(_decisions())


def _without_highest_entry(text: str, tag: str) -> str:
    """A SCRATCH copy of the ledger text with one entry removed."""
    lines = text.splitlines(keepends=True)
    marker = f"**A-P6-{tag} \u00b7"
    hits = [i for i, ln in enumerate(lines) if ln.startswith(marker)]
    assert len(hits) == 1, f"expected one '{marker}' line, found {len(hits)}"
    del lines[hits[0]]
    return "".join(lines)


def test_the_numbering_node_reds_when_the_highest_row_is_deleted() -> None:
    """MUTATION LEG (A-P6-35 g): deleting the HIGHEST-numbered entry
    must RED the numbering node.

    The pre-fix shape ("no gaps up to whatever max is") is BLIND to
    exactly this deletion — the max simply shrinks and the check still
    passes. The EXPECTED_LATEST anchor closes it, and this leg proves
    the anchor bites. The ledger FILE is never written: the leg runs
    on a scratch copy, and the production bytes are sha-verified
    unchanged afterwards ("restored byte-identically", trivially —
    nothing was ever written)."""
    before = _sha256(DECISIONS)
    broken = _without_highest_entry(_decisions(), str(EXPECTED_LATEST))
    with pytest.raises(AssertionError):
        _assert_numbering(broken)
    assert _sha256(DECISIONS) == before


def test_the_a_p6_26_surface_split_is_recorded() -> None:
    """A-P6-35 (i) — the entry must reconcile its "NO names" claim BY
    SURFACE: the SSE room-channel FRAMES carry no names, while the DB
    AUDIT ROW carries ``member_name`` BY DESIGN (A-P6-34's four
    fields)."""
    entry = _entry(_decisions(), "26")
    assert "carry NO room or member NAMES" in entry, (
        "A-P6-26 must keep the frames' content-free claim"
    )
    assert "DB AUDIT ROW" in entry and "BY DESIGN" in entry, (
        "A-P6-26 must name the DB audit row as the surface that "
        "carries member_name by design"
    )
    assert "NEVER fanned to a room channel" in entry, (
        "A-P6-26 must state that the audit row is never fanned to a "
        "room channel"
    )
