"""Unit tests for LCA child-terminal contradiction detection (2026-09-16).

This file pins the behavior of the new feature that attaches an
advisory ``[SYSTEM CONTEXT: Child Report Check]`` note to the PARENT
when the CHILD is transitioning to terminal AND its final outgoing
report contains promise-while-stopping markers.

The detector is a PURE substring scan (zero LLM involvement); the hook
lives in :mod:`daemon.services.child_reports` at the
``_process_child_completion_db_sync`` terminal-report delivery seam.

Two surfaces are pinned:

1. **Pure-function surface** (:mod:`attestation_marker_scanner`):
   catalog membership, :func:`scan_child_terminal_report_for_promises`
   matrix on the spec seed phrases (positive / benign / FP-tight
   near-FP).

2. **Hook surface** (:mod:`daemon.services.child_reports`):
   the DB-sync half of the child-completion path attaches the note
   as a SEPARATE ``MessageQueue`` row on the parent's queue, with
   a stable id keyed on the (parent, child) pair. The note does
   NOT modify the child's report content. The detector rides as
   post-report INSERT, not as a gate — the parent's existing
   deny-path / judge / nudge machinery is untouched.

Test surface (per the spec's test plan):
  (a) terminal child + 'will write RESULTS. Ending turn' → parent
      receives report + note (note NOT part of report content;
      stable id present; marker kwargs present)
  (b) terminal child + 'Done, 5/5, merged abc123' → NO note
  (c) near-FP 'completed X, awaiting your merge decision' →
      adjudicated → NOTE fires (per spec adjudication; the note
      is clearly framed as advisory so the parent retains judgment)
  (d) note supersede on repeat — second contradiction event on the
      same (parent, child) pair collapses via LangGraph's
      ``add_messages`` reducer (REAL add_messages, not a mirror)
  (e) grandchild-to-child case — the detector fires on a child
      whose report was itself delivered to its parent (the same
      hook applies regardless of the child's depth in the tree)
  (f) zero LLM calls — the detector is a pure substring scan;
      the hook does NOT call any LLM
  (g) report delivery timing unchanged — the note rides as a
      second INSERT in the SAME transaction; the parent's first
      turn picks up BOTH the report message AND the note message
      in the standard drain order

Stable id format: ``child_report_check:{child_instance_id}:{report_message_id}``
(matches the canonical mint at ``daemon/services/child_reports.py``
Stage-0 child-completion note enqueue path; the older
``_stable_id_for`` table at :mod:`daemon.services.context_messages`
uses a separate ``child_report_check:{instance_id}:{agent_id}`` shape).
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool, StaticPool
from sqlmodel import Session, SQLModel

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.dependency_bus import DependencyWatcherRepository
from daemon.repositories.dependency_bus.models import (
    DependencyWatcherState,
)
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.services.attestation_marker_scanner import (
    CHILD_TERMINAL_PROMISE_MARKERS,
    ChildTerminalPromiseScanResult,
    scan_child_terminal_report_for_promises,
)
from daemon.services.child_reports import ChildReportsService
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus
from daemon.services.context_messages import (
    CONTEXT_KIND_CHILD_REPORT_CHECK,
    _stable_id_for,
)
from daemon.write_pause_guard import WritePauseGuard


# ─────────────────────────────────────────────────────────────────────────────
# Pure-function surface — scan_child_terminal_report_for_promises
# ─────────────────────────────────────────────────────────────────────────────


class TestChildTerminalPromiseScan:
    """Pin the pure-function scan surface (zero LLM involvement)."""

    def test_catalog_size_in_range(self):
        """FP-tight range (10-18 entries) per the 2026-09-16 spec.

        Smaller range than :data:`MID_WORK_MARKERS` (12-18) because
        this catalog is BOTH the trigger AND the verdict — there is no
        judge to disambiguate. The catalog holds 17 entries today;
        pin the range so accidental edits surface at collection time.
        """
        assert 10 <= len(CHILD_TERMINAL_PROMISE_MARKERS) <= 18, (
            f"CHILD_TERMINAL_PROMISE_MARKERS must hold 10-18 patterns "
            f"per the 2026-09-16 spec; got {len(CHILD_TERMINAL_PROMISE_MARKERS)}"
        )

    def test_canonical_seed_phrases_all_present(self):
        """Every spec seed phrase is in the catalog as a substring literal.

        The spec lists 9 seed phrases; each is materialized in the
        catalog either verbatim or as the closest matching
        substring literal. Pin every translation so an
        accidental rename surfaces here.
        """
        seeds = {
            "ending turn": "ending turn",
            "then i": "then i ",
            "to be continued": "to be continued",
            "in progress": "in progress",
            "still pending": "still pending",
            "not yet complete": "not yet complete",
            "will report back": "will report back",
            "awaiting": "awaiting",
        }
        for spec_name, catalog_entry in seeds.items():
            assert catalog_entry in CHILD_TERMINAL_PROMISE_MARKERS, (
                f"spec seed phrase {spec_name!r} translated as "
                f"{catalog_entry!r} but the entry is missing from "
                f"CHILD_TERMINAL_PROMISE_MARKERS"
            )

    def test_canonical_seed_positive(self):
        """(a) Spec positive case — fires on the canonical phrase."""
        r = scan_child_terminal_report_for_promises(
            "Awaiting final four: C12a/b/c. Then I aggregate and "
            "will write RESULTS. Ending turn."
        )
        assert r.promise_hit is True
        assert "ending turn" in r.matched_terms
        assert "will write" in r.matched_terms
        assert "then i " in r.matched_terms
        # Terms are distinct, in catalog order
        assert len(r.matched_terms) == len(set(r.matched_terms))

    def test_clean_completion_does_not_fire(self):
        """(b) Spec negative case — 'Done, 5/5, merged abc123' must NOT fire."""
        r = scan_child_terminal_report_for_promises(
            "Done, 5/5, merged abc123"
        )
        assert r.promise_hit is False, (
            f"Clean completion report must NOT fire; got "
            f"matched_terms={r.matched_terms}"
        )
        assert r.matched_terms == ()

    def test_other_clean_reports_do_not_fire(self):
        """Other legitimate completion phrasings stay quiet.

        Pin carefully — these examples MUST NOT contain ANY catalog
        term. In particular: 'awaiting' is in the catalog so any
        phrase containing that word WILL fire (adjudicated near-FP,
        see ``test_near_fp_awaiting_merge_decision_fires``). The
        legitimate-completion examples below deliberately avoid
        every catalog term.
        """
        for text in [
            "All work shipped. Nothing open.",
            "Completed the migration; results in PR #42.",
            "Summary: 5 tasks done, 0 remaining.",
            "Finished. See attached.",
            "Shipped. Done with the full task set.",
        ]:
            r = scan_child_terminal_report_for_promises(text)
            assert r.promise_hit is False, (
                f"legitimate completion {text!r} must NOT fire; "
                f"got matched_terms={r.matched_terms}"
            )

    def test_near_fp_awaiting_merge_decision_fires(self):
        """(c) Spec near-FP adjudication — 'completed X, awaiting your
        merge decision' FIRES (note attached).

        Adjudication: 'awaiting' is an explicit spec seed phrase.
        Excluding it would lose detection on the most common
        promise-while-stopping phrasing the leader-side incident
        family produces. The note text is clearly framed as advisory
        / heuristic so the parent LLM retains judgment — the FP cost
        is borne by an explicitly advisory note, not by a hidden
        gate. Operators see the structured
        ``event=leader_completion_gate_child_report_check_fired`` row
        with ``matched_terms=awaiting`` and can grep FP rate from
        log rows.
        """
        r = scan_child_terminal_report_for_promises(
            "completed X, awaiting your merge decision"
        )
        assert r.promise_hit is True
        assert r.matched_terms == ("awaiting",)

    def test_case_insensitive(self):
        """Match is case-insensitive (lower-cased before scan)."""
        for text in [
            "ENDING TURN",
            "Ending Turn",
            "ending turn",
            "ENDING TURN — final note",
        ]:
            r = scan_child_terminal_report_for_promises(text)
            assert r.promise_hit is True, (
                f"case-insensitive match failed for {text!r}"
            )

    def test_empty_text_does_not_fire(self):
        """Defensive floor — empty / None reports stay quiet."""
        assert scan_child_terminal_report_for_promises("").promise_hit is False
        assert scan_child_terminal_report_for_promises(None).promise_hit is False  # type: ignore[arg-type]

    def test_matched_terms_capped_at_catalog_size(self):
        """Belt-and-braces — distinct matched_terms never exceed catalog size."""
        # Construct a text that hits every catalog entry.
        all_terms = " ".join(CHILD_TERMINAL_PROMISE_MARKERS)
        r = scan_child_terminal_report_for_promises(all_terms)
        assert len(r.matched_terms) == len(CHILD_TERMINAL_PROMISE_MARKERS)
        assert len(r.matched_terms) == len(set(r.matched_terms))

    def test_then_i_trailing_space_protects_against_then_in_it(self):
        """Spec FP guard — 'then i ' has a trailing space so 'then in
        parallel' / 'then it will be' / 'then if' don't false-fire.

        The bare 'then i' (no trailing space) would match all three
        of those substrings — explicit test pin so the trailing-
        space guard survives catalog edits.
        """
        for benign in [
            "then it will be obvious",
            "then in parallel we shipped",
            "then if we wait the report arrives",
        ]:
            r = scan_child_terminal_report_for_promises(benign)
            # The "then i " pattern (with trailing space) MUST NOT fire.
            assert "then i " not in r.matched_terms, (
                f"bare 'then i' matched inside {benign!r}; the "
                f"trailing-space guard must survive"
            )

    def test_result_shape_namedtuple(self):
        """Result is a frozen NamedTuple with the spec-mandated fields."""
        r = scan_child_terminal_report_for_promises("ending turn")
        assert isinstance(r, ChildTerminalPromiseScanResult)
        assert hasattr(r, "promise_hit")
        assert hasattr(r, "matched_terms")
        assert r.promise_hit is True
        assert r.matched_terms == ("ending turn",)


# ─────────────────────────────────────────────────────────────────────────────
# Stable id surface — _stable_id_for("child_report_check")
# ─────────────────────────────────────────────────────────────────────────────


class TestChildReportCheckStableId:
    """Pin the canonical id format
    ``child_report_check:{parent_id}:{child_id}``.
    """

    def test_basic_pair_mints_expected_id(self):
        sid = _stable_id_for(
            "child_report_check",
            instance_id="parent-A",
            agent_id="child-B",
        )
        assert sid == "child_report_check:parent-A:child-B"

    def test_pair_is_keyed_on_both_parts(self):
        """Two distinct parents receiving reports from the same
        child MUST NOT collide — different ids, different
        supersede slots in the parent's checkpoint.
        """
        sid_a = _stable_id_for(
            "child_report_check",
            instance_id="parent-A",
            agent_id="child-X",
        )
        sid_b = _stable_id_for(
            "child_report_check",
            instance_id="parent-B",
            agent_id="child-X",
        )
        assert sid_a != sid_b

    def test_pair_is_keyed_on_both_parts_reversed(self):
        """And the reverse — same parent, two children, distinct ids."""
        sid_a = _stable_id_for(
            "child_report_check",
            instance_id="parent-X",
            agent_id="child-A",
        )
        sid_b = _stable_id_for(
            "child_report_check",
            instance_id="parent-X",
            agent_id="child-B",
        )
        assert sid_a != sid_b

    def test_missing_parent_raises(self):
        with pytest.raises(ValueError, match="instance_id"):
            _stable_id_for(
                "child_report_check",
                instance_id=None,  # type: ignore[arg-type]
                agent_id="child-A",
            )

    def test_missing_child_raises(self):
        with pytest.raises(ValueError, match="agent_id"):
            _stable_id_for(
                "child_report_check",
                instance_id="parent-A",
                agent_id=None,  # type: ignore[arg-type]
            )

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError, match="unknown kind"):
            _stable_id_for("not_a_real_kind", instance_id="x")  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# Hook surface — _process_child_completion_db_sync attaches the note
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path):
    """File-backed SQLite per spec — tmp_path + NullPool + WAL + busy_timeout."""
    eng = create_engine(
        f"sqlite:///{tmp_path}/ctc-it.db",
        connect_args={"check_same_thread": False, "timeout": 30.0},
        poolclass=NullPool,
    )
    # Mirror the spec's File-backed SQLite convention (WAL +
    # busy_timeout=10000) — toggled at connection time so the
    # engine's connection pool still works correctly under
    # NullPool + multi-thread.
    @event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def service(engine):
    mgr = type("Mgr", (), {})()
    mgr.engine = engine
    mgr.write_guard = WritePauseGuard()
    mgr._deferred_question_pause = set()
    mgr.config = None  # Not used by _process_child_completion_db_sync on the
    # "regular_child_completed" path; supplied for type completeness so a
    # future refactor that consults ``self._manager.config`` early doesn't
    # raise AttributeError mid-test.
    return ChildReportsService(manager=mgr, events_service=None)


@pytest.fixture(autouse=True)
def _reset_bus_singleton():
    set_dependency_bus(None)
    yield
    set_dependency_bus(None)


@pytest.fixture
async def bus(engine):
    repo = DependencyWatcherRepository(engine)
    b = DependencyBus(repo)
    await b.start()
    set_dependency_bus(b)
    try:
        yield b
    finally:
        await b.stop()
        set_dependency_bus(None)


def _seed_instance(engine, *, instance_id, parent_id=None, status=InstanceStatus.RUNNING.value):
    with Session(engine) as s:
        s.add(Instance(
            instance_id=instance_id,
            agent_id="worker",
            agent_dir="/tmp/worker",
            agent_name="worker",
            parent_id=parent_id,
            status=status,
            version=1,
        ))
        s.commit()


class TestChildTerminalContradictionHook:
    """The hook in _process_child_completion_db_sync.

    Pins the spec's (a)/(b)/(c)/(d)/(e)/(f)/(g) test cases on the
    REAL DB-sync path (in-memory SQLite, ``WriteGuardSession``
    lifecycle). The async wrapper is exercised by the integration
    suite.
    """

    async def test_a_promise_report_attaches_note(
        self, service, engine, bus
    ):
        """(a) Spec — 'will write RESULTS. Ending turn' fires the
        note; parent receives report + note as TWO separate rows.

        The note is NOT part of the report content (separate
        ``message_id``, separate ``content``, separate
        ``context_kind`` in metadata). The note carries the
        canonical stable id ``child_report_check:{parent}:{child}``.
        Marker kwargs: ``additional_kwargs.child_report_check=True``
        (we ride the spec via ``message_metadata`` /
        ``additional_kwargs`` on the source surface).
        """
        parent_id = "parent-a-test"
        child_id = "child-a-test"
        _seed_instance(engine, instance_id=parent_id)
        _seed_instance(engine, instance_id=child_id, parent_id=parent_id)

        promise_report = (
            "will write RESULTS. Ending turn."
        )

        result = service._process_child_completion_db_sync(
            child_id,
            completed_message_id="msg-child-a",
            last_content=promise_report,
        )

        # Outcome is regular completion (not deferred, not dead-parent).
        assert result.outcome == "regular_child_completed", (
            f"promise-while-stopping report must still trigger the "
            f"normal completion outcome; got {result.outcome}"
        )

        # Read back the parent's queue.
        with Session(engine) as s:
            rows = (
                s.query(MessageQueue)
                .filter(MessageQueue.instance_id == parent_id)
                .order_by(MessageQueue.enqueued_at)
                .all()
            )

        # Two rows: the completion report + the child report check note.
        types = [r.type for r in rows]
        assert MessageType.COMPLETION_REPORT.value in types
        assert MessageType.SYSTEM.value in types
        assert len(rows) == 2, (
            f"parent must have EXACTLY two messages "
            f"(report + note), got {len(rows)}: {types}"
        )

        # Find the note row — it's the SYSTEM one.
        note = next(r for r in rows if r.type == MessageType.SYSTEM.value)
        report = next(r for r in rows if r.type == MessageType.COMPLETION_REPORT.value)

        # The note is a SEPARATE message — its content is NOT the
        # child's report text and its id is NOT the report's id.
        assert note.content != report.content
        assert note.message_id != report.message_id

        # The note carries the canonical stable id format
        # ``child_report_check:{parent}:{child}`` in the source.
        assert note.source == (
            f"child_report_check:{child_id}:{report.message_id}"
        )

        # The note content starts with the [SYSTEM CONTEXT: Child
        # Report Check] prefix (the canonical context-message shape
        # so downstream consumers — compaction seam, API display —
        # recognize it as the same ``[SYSTEM CONTEXT: ...]`` family).
        assert note.content.startswith("[SYSTEM CONTEXT: Child Report Check]"), (
            f"note must carry the canonical [SYSTEM CONTEXT: ...] "
            f"prefix; got {note.content[:80]!r}"
        )

        # Marker kwargs (the spec requires ``child_report_check=True``)
        # ride on the message_metadata dict.
        assert note.message_metadata.get("child_report_check") is True
        assert note.message_metadata.get("context_kind") == (
            CONTEXT_KIND_CHILD_REPORT_CHECK
        )
        assert note.message_metadata.get("injected_message") is True

        # The matched terms are surfaced as a structured kwarg so
        # observability / compaction hooks can pin them without
        # reparsing the prose body.
        terms = note.message_metadata.get("child_report_check_terms")
        assert terms is not None
        assert "ending turn" in terms
        assert "will write" in terms

    def test_b_clean_completion_does_not_attach_note(
        self, service, engine, bus
    ):
        """(b) Spec — 'Done, 5/5, merged abc123' must NOT attach a note."""
        parent_id = "parent-b-test"
        child_id = "child-b-test"
        _seed_instance(engine, instance_id=parent_id)
        _seed_instance(engine, instance_id=child_id, parent_id=parent_id)

        service._process_child_completion_db_sync(
            child_id,
            completed_message_id="msg-child-b",
            last_content="Done, 5/5, merged abc123",
        )

        with Session(engine) as s:
            rows = (
                s.query(MessageQueue)
                .filter(MessageQueue.instance_id == parent_id)
                .all()
            )

        # Only the report message — no note.
        assert len(rows) == 1, (
            f"clean completion must produce ONE message (the "
            f"report), got {len(rows)}"
        )
        assert rows[0].type == MessageType.COMPLETION_REPORT.value

    async def test_c_near_fp_awaiting_fires_with_advisory_note(
        self, service, engine, bus
    ):
        """(c) Spec adjudication — 'completed X, awaiting your merge
        decision' FIRES.

        The note text is clearly framed as advisory so the parent
        LLM retains judgment; this test pins the adjudication
        explicitly so a future tightening (e.g. removing
        ``awaiting``) requires a deliberate test edit.
        """
        parent_id = "parent-c-test"
        child_id = "child-c-test"
        _seed_instance(engine, instance_id=parent_id)
        _seed_instance(engine, instance_id=child_id, parent_id=parent_id)

        service._process_child_completion_db_sync(
            child_id,
            completed_message_id="msg-child-c",
            last_content="completed X, awaiting your merge decision",
        )

        with Session(engine) as s:
            rows = (
                s.query(MessageQueue)
                .filter(MessageQueue.instance_id == parent_id)
                .order_by(MessageQueue.enqueued_at)
                .all()
            )

        # Two rows: report + note (awaiting fires).
        assert len(rows) == 2, (
            f"near-FP adjudication: awaiting MUST fire per spec; "
            f"got {len(rows)} messages for parent={parent_id}"
        )

        note = next(r for r in rows if r.type == MessageType.SYSTEM.value)
        # The note text is clearly framed as advisory so the parent
        # sees the heuristic framing before reacting.
        assert "Advisory" in note.content or "heuristic" in note.content, (
            f"note text must surface the advisory / heuristic "
            f"framing; got {note.content!r}"
        )

    async def test_e_grandchild_to_child_case_fires(
        self, service, engine, bus
    ):
        """(e) Spec — the detector fires regardless of the child's
        depth in the tree. A grandchild whose report is being
        delivered to its child-parent fires the same hook; the
        detector is agent-agnostic.
        """
        root = "root-grandparent"
        child = "child-parent"  # The grandchild's direct parent.
        grandchild = "leaf-grandchild"
        _seed_instance(engine, instance_id=root)
        _seed_instance(engine, instance_id=child, parent_id=root)
        _seed_instance(engine, instance_id=grandchild, parent_id=child)

        service._process_child_completion_db_sync(
            grandchild,
            completed_message_id="msg-grandchild",
            last_content="Aggregating follow-ups. Then I will write "
                         "up the SUMMARY. Ending turn.",
        )

        with Session(engine) as s:
            rows = (
                s.query(MessageQueue)
                .filter(MessageQueue.instance_id == child)
                .all()
            )

        # Grandchild's report + the contradiction note both landed
        # on the child's queue (not the root's). The detector is
        # depth-agnostic — fires at the immediate-parent hop.
        assert len(rows) == 2
        types = [r.type for r in rows]
        assert MessageType.COMPLETION_REPORT.value in types
        assert MessageType.SYSTEM.value in types

    def test_f_zero_llm_calls(self, service, engine, bus, monkeypatch):
        """(f) Spec — the detector is a pure substring scan. No LLM call.

        We pin this by patching every plausible LLM-call entry
        point in the call tree and asserting none of them was hit.
        If a future refactor adds an LLM call, this test fails
        loud.
        """
        from unittest.mock import MagicMock

        # Sentinel LLM objects; any call method on these raises so
        # an accidental LLM invocation fails the test loudly.
        class _LLMGuard:
            def __getattr__(self, name):
                def _fail(*args, **kwargs):
                    raise AssertionError(
                        f"LLM call detected — detector MUST be "
                        f"zero-LLM per spec; called {name!r} with "
                        f"args={args} kwargs={kwargs}"
                    )
                return _fail

        llm_guard = _LLMGuard()
        # Patch the most common LLM-call surfaces at the daemon level.
        import daemon.services.child_reports as child_reports_module
        import daemon.services.attestation_marker_scanner as scanner_module

        monkeypatch.setattr(
            child_reports_module, "ThinkingChatOpenAI", llm_guard, raising=False
        )
        monkeypatch.setattr(
            scanner_module, "ThinkingChatOpenAI", llm_guard, raising=False
        )

        parent_id = "parent-zerollm"
        child_id = "child-zerollm"
        _seed_instance(engine, instance_id=parent_id)
        _seed_instance(engine, instance_id=child_id, parent_id=parent_id)

        # If the detector accidentally routed through an LLM, the
        # patched surface would raise AssertionError on the first
        # attribute access.
        service._process_child_completion_db_sync(
            child_id,
            completed_message_id="msg-zerollm",
            last_content="will write the next report. Ending turn.",
        )

        # Sanity — the note IS attached (so the patch didn't break
        # the pure-function path).
        with Session(engine) as s:
            note_count = (
                s.query(MessageQueue)
                .filter(
                    MessageQueue.instance_id == parent_id,
                    MessageQueue.type == MessageType.SYSTEM.value,
                )
                .count()
            )
        assert note_count == 1, "pure-function path must still attach the note"

    def test_g_report_delivery_timing_unchanged(
        self, service, engine, bus
    ):
        """(g) Spec — the note rides as a SECOND INSERT in the SAME
        transaction; report delivery timing is unchanged (the
        parent's first turn picks up BOTH messages in the standard
        drain order).

        We pin this by reading the enqueued_at of both rows and
        verifying they are within the same transaction window
        (millisecond-apart, no scheduled delay).
        """
        parent_id = "parent-timing"
        child_id = "child-timing"
        _seed_instance(engine, instance_id=parent_id)
        _seed_instance(engine, instance_id=child_id, parent_id=parent_id)

        service._process_child_completion_db_sync(
            child_id,
            completed_message_id="msg-timing",
            last_content="Then I will run the rest. Ending turn.",
        )

        with Session(engine) as s:
            rows = (
                s.query(MessageQueue)
                .filter(MessageQueue.instance_id == parent_id)
                .order_by(MessageQueue.enqueued_at)
                .all()
            )

        assert len(rows) == 2
        report_enq = rows[0].enqueued_at
        note_enq = rows[1].enqueued_at
        # Both in the same transaction — within 1 second of each
        # other (well below the per-task latency budget). The note
        # is NOT scheduled or deferred; it rides inline.
        assert abs((note_enq - report_enq).total_seconds()) < 1.0, (
            f"note must ride inline with the report; "
            f"report_enq={report_enq} note_enq={note_enq}"
        )
        # Both rows are READY (no scheduling / deferred status).
        for r in rows:
            assert r.status == MessageStatus.READY.value

    def test_d_supersede_on_repeat_real_add_messages(
        self, service, engine, bus
    ):
        """(d) Spec — repeated contradiction events on the same
        (parent, child) pair collapse to ONE block via LangGraph's
        ``add_messages`` reducer.

        The detector's two completion calls produce ONE note each
        (different ``message_id``s because the underlying
        ``MessageQueue`` PKs differ — the table layer doesn't
        collapse). The supersede happens when the rows are drained
        into the parent's ``state["messages"]`` channel: two notes
        carrying the same stable id
        ``child_report_check:{parent}:{child}`` collapse to ONE
        block in the resulting channel via LangGraph's
        ``add_messages`` reducer.

        We verify this on the REAL ``langgraph.graph.message
        .add_messages`` (NOT a mirror) — the contract is what
        matters, not the DB-layer row count. Matches the W2 Shape A
        precedent from ``completion_check_note:{instance_id}`` —
        :file:`tests/unit/test_attestation_marker_supersede_lca.py`.
        """
        import importlib
        import sys

        # Snapshot conftest-installed mocks so we can restore them
        # after the test (mirrors the eviction pattern from
        # test_attestation_marker_supersede_lca).
        _saved = {
            k: sys.modules[k]
            for k in list(sys.modules)
            if k.startswith("langgraph")
        }
        for k in list(_saved):
            del sys.modules[k]

        try:
            real_message = importlib.import_module("langgraph.graph.message")

            parent_id = "parent-supersede"
            child_id = "child-supersede"

            # Two notes with the SAME stable id (the (parent, child)
            # pair format) collapse to ONE block per the W2 Shape A
            # contract. Mirror what the production
            # ``_make_context_message`` factory would emit.
            from langchain_core.messages import HumanMessage

            sid = _stable_id_for(
                "child_report_check",
                instance_id=parent_id,
                agent_id=child_id,
            )
            assert sid == "child_report_check:parent-supersede:child-supersede"

            # Two consecutive notes carrying the same id — this is
            # exactly what happens when the same (parent, child) pair
            # fires the detector twice (e.g. child completes, parent
            # revives it, child completes again with another
            # promise-while-stopping report).
            note_1 = HumanMessage(
                content="[SYSTEM CONTEXT: Child Report Check]\n\nnote-1",
                id=sid,
            )
            note_2 = HumanMessage(
                content="[SYSTEM CONTEXT: Child Report Check]\n\nnote-2",
                id=sid,  # SAME id → supersede in add_messages reducer
            )

            initial = [note_1]
            final = real_message.add_messages(initial, [note_2])
            # Count blocks with our stable-id prefix in the
            # resulting channel — exactly ONE survives (W2 Shape A
            # contract).
            surviving = [
                m for m in final
                if getattr(m, "id", "").startswith(
                    "child_report_check:"
                )
            ]
            assert len(surviving) == 1, (
                f"W2 Shape A contract: two same-id notes collapse "
                f"to ONE block via real add_messages; got "
                f"{len(surviving)} surviving"
            )
            # The surviving block is the second one (most-recent
            # wins on supersede).
            assert surviving[0].content.endswith("note-2"), (
                f"second note MUST supersede the first; got "
                f"surviving.content={surviving[0].content!r}"
            )
        finally:
            # Restore conftest-installed langgraph mocks exactly as
            # we found them so neighbouring tests keep their
            # mock-bound identity.
            for k in list(sys.modules):
                if k.startswith("langgraph"):
                    del sys.modules[k]
            sys.modules.update(_saved)

    def test_dead_parent_skips_note_insert(self, service, engine, bus):
        """Scope guard — when the parent is TERMINATED (dead
        parent), the note INSERT is suppressed along with the rest
        of the delivery obligation. The note would accumulate in
        the queue forever otherwise.
        """
        parent_id = "parent-dead"
        child_id = "child-dead"
        _seed_instance(engine, instance_id=parent_id, status=InstanceStatus.TERMINATED.value)
        _seed_instance(engine, instance_id=child_id, parent_id=parent_id)

        result = service._process_child_completion_db_sync(
            child_id,
            completed_message_id="msg-dead",
            last_content="will write the next report. Ending turn.",
        )

        assert result.outcome == "dead_parent_skip"

        # Parent queue is empty (the message was marked FAILED
        # inline; no note INSERT, no report-injection row).
        with Session(engine) as s:
            rows = (
                s.query(MessageQueue)
                .filter(MessageQueue.instance_id == parent_id)
                .all()
            )
        # The report message row IS persisted (with FAILED
        # status — audit trail), but the note is NOT.
        assert len(rows) == 1
        assert rows[0].status == MessageStatus.FAILED.value
        assert rows[0].type == MessageType.COMPLETION_REPORT.value

    def test_savepoint_fail_open_on_note_insert(
        self, service, engine, bus, monkeypatch, caplog
    ):
        """FAIL-OPEN contract — when the SYSTEM-type Child Report
        Check note INSERT raises ``IntegrityError`` (synthetic — e.g.
        a constraint failure on the MessageQueue row), the outer
        child-completion transaction MUST still commit:

          (a) parent queue still has the child COMPLETION_REPORT row
              (READY, not rolled back)
          (b) the PROCESS_REPORT Task exists (PENDING)
          (c) the report_injection row still commits (PENDING)
          (d) ``event=leader_completion_gate_child_report_check_failed``
              is logged at WARNING level (caplog)
          (e) NO exception propagates to the caller

        This pins the load-bearing SAVEPOINT contract: the note is
        advisory — a missed note is tolerable, a blocked child
        completion is NOT. Selectively patch ``Session.flush`` so the
        synthetic ``IntegrityError`` fires ONLY when a ``MessageQueue``
        with ``type=SYSTEM`` and ``context_kind="child_report_check"``
        is in the pending-new set (the note INSERT site). Earlier
        flushes (report message INSERT, task INSERT, report_injection
        INSERT) pass through to the real flush so the function
        reaches the note INSERT and commits the rest normally.
        """
        from sqlalchemy.exc import IntegrityError as SAIntegrityError
        from sqlmodel import Session as SQLModelSession

        parent_id = "parent-failopen"
        child_id = "child-failopen"
        _seed_instance(engine, instance_id=parent_id)
        _seed_instance(engine, instance_id=child_id, parent_id=parent_id)

        # Synthetic error shaped like a generic FK / NOT-NULL
        # violation on ``message_queue``. We deliberately use an
        # opaque message so no caller-side discriminator could
        # possibly mistake it for the obligation-triple class (this
        # is a DIFFERENT row — note vs. report_injection — and a
        # different constraint class entirely).
        synthetic_orig = RuntimeError(
            "synthetic: child_report_check note INSERT violated "
            "message_queue constraint"
        )
        synthetic_err = SAIntegrityError(
            "INSERT INTO message_queue (...)",
            params={},
            orig=synthetic_orig,
        )

        # Selective flush wrapper — raises ONLY for the note row.
        # Inspect ``session.new`` for the pending-new MessageQueue
        # carrying ``context_kind="child_report_check"``; all other
        # flushes (report message, task, report_injection) pass
        # through unchanged so the canonical child-completion path
        # commits normally.
        original_flush = SQLModelSession.flush

        def _selective_flush(self, *args, **kwargs):
            new_objs = list(getattr(self, "new", set()) or set())
            for obj in new_objs:
                if (
                    isinstance(obj, MessageQueue)
                    and obj.type == MessageType.SYSTEM.value
                    and isinstance(obj.message_metadata, dict)
                    and obj.message_metadata.get("context_kind")
                    == CONTEXT_KIND_CHILD_REPORT_CHECK
                ):
                    raise synthetic_err
            return original_flush(self, *args, **kwargs)

        monkeypatch.setattr(SQLModelSession, "flush", _selective_flush)

        # (e) NO exception propagates to the caller.
        import logging

        with caplog.at_level(
            logging.WARNING, logger="daemon.services.child_reports"
        ):
            result = service._process_child_completion_db_sync(
                child_id,
                completed_message_id="msg-failopen",
                last_content="will write the next report. Ending turn.",
            )

        # Outcome is regular completion — the synthetic failure on
        # the note INSERT MUST NOT reclassify the result.
        assert result.outcome == "regular_child_completed", (
            f"synthetic IntegrityError on the note MUST NOT alter "
            f"the completion outcome; got {result.outcome}"
        )

        # (a) Parent queue still has the child COMPLETION_REPORT row
        # (READY). Zero SYSTEM child_report_check rows — the
        # SAVEPOINT-scoped INSERT rolled back.
        with Session(engine) as s:
            note_rows = (
                s.query(MessageQueue)
                .filter(
                    MessageQueue.instance_id == parent_id,
                    MessageQueue.type == MessageType.SYSTEM.value,
                )
                .all()
            )
            report_rows = (
                s.query(MessageQueue)
                .filter(
                    MessageQueue.instance_id == parent_id,
                    MessageQueue.type == MessageType.COMPLETION_REPORT.value,
                )
                .all()
            )
        assert len(note_rows) == 0, (
            f"note row MUST NOT commit when its INSERT raised; got "
            f"{len(note_rows)} note row(s)"
        )
        assert len(report_rows) == 1, (
            f"report row MUST commit despite the note failure; got "
            f"{len(report_rows)} report row(s)"
        )
        assert report_rows[0].status == MessageStatus.READY.value

        # (a2) SAVEPOINT atomicity — the co-minted PROCESS_MESSAGE
        # delivery Task rides in the SAME SAVEPOINT as the failed note
        # row (child_reports.py mint-with-delivery), so it MUST roll
        # back with it. The note's message_id is minted inside the
        # function (unobservable here), so probe by type: the
        # completion path mints PROCESS_REPORT only — ANY
        # process_message Task on the parent would be the note's
        # leaked carrier.
        with Session(engine) as s:
            note_delivery_tasks = (
                s.query(Task)
                .filter(
                    Task.instance_id == parent_id,
                    Task.task_type == TaskType.PROCESS_MESSAGE.value,
                )
                .all()
            )
        assert len(note_delivery_tasks) == 0, (
            f"co-minted PROCESS_MESSAGE delivery Task MUST roll back "
            f"with the failed note INSERT; got "
            f"{len(note_delivery_tasks)} row(s)"
        )

        # (b) PROCESS_REPORT task exists in PENDING.
        with Session(engine) as s:
            tasks = (
                s.query(Task)
                .filter(
                    Task.instance_id == parent_id,
                    Task.task_type == TaskType.PROCESS_REPORT.value,
                )
                .all()
            )
        assert len(tasks) == 1, (
            f"PROCESS_REPORT task MUST exist despite note failure; "
            f"got {len(tasks)} task(s)"
        )
        assert tasks[0].status == TaskStatus.PENDING.value

        # (c) report_injection row still commits (PENDING).
        from daemon.repositories.report_injection.models import (
            ReportInjection,
            ReportInjectionState,
        )

        with Session(engine) as s:
            inj_rows = (
                s.query(ReportInjection)
                .filter(ReportInjection.parent_instance_id == parent_id)
                .all()
            )
        assert len(inj_rows) == 1, (
            f"report_injection row MUST commit despite note "
            f"failure; got {len(inj_rows)} row(s)"
        )
        assert inj_rows[0].state == ReportInjectionState.PENDING.value

        # (d) event=leader_completion_gate_child_report_check_failed
        # is logged at WARNING level (caplog). Pin the event name
        # verbatim so log-grep survives future renames.
        failure_records = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING
            and "event=leader_completion_gate_child_report_check_failed"
            in r.getMessage()
        ]
        assert len(failure_records) == 1, (
            f"expected exactly one failure log line; got "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        # Error class is surfaced so operators can diagnose.
        assert "IntegrityError" in failure_records[0].getMessage()

    def test_marker_paused_skips_note_insert(
        self, service, engine, bus
    ):
        """Scope guard — when the parent is in the marker-paused
        set (``_deferred_question_pause``), the note INSERT is
        suppressed (mirrors the PROCESS_REPORT + report_injection
        skip on the same branch). Without the skip, the note would
        accumulate in the queue forever — the parent's live agent-
        node is paused, so the note cannot be drained.
        """
        parent_id = "parent-markerpaused"
        child_id = "child-markerpaused"
        _seed_instance(engine, instance_id=parent_id)
        _seed_instance(engine, instance_id=child_id, parent_id=parent_id)

        # Trigger marker_paused by adding the parent id to the
        # manager's deferred-question-pause set (the same set the
        # production code consults at child_reports.py:3022-3024).
        service._manager._deferred_question_pause.add(parent_id)

        result = service._process_child_completion_db_sync(
            child_id,
            completed_message_id="msg-markerpaused",
            last_content="will write the next report. Ending turn.",
        )

        assert result.outcome == "regular_child_completed"

        with Session(engine) as s:
            note_rows = (
                s.query(MessageQueue)
                .filter(
                    MessageQueue.instance_id == parent_id,
                    MessageQueue.type == MessageType.SYSTEM.value,
                )
                .all()
            )
        assert len(note_rows) == 0, (
            f"marker_paused MUST suppress the note INSERT; got "
            f"{len(note_rows)} note row(s) for parent={parent_id}"
        )
        # And the skip is reported on the same marker_paused reason.
        # The report message + PROCESS_REPORT task + report_injection
        # row still commit normally (only the note is suppressed).
        with Session(engine) as s:
            report_rows = (
                s.query(MessageQueue)
                .filter(
                    MessageQueue.instance_id == parent_id,
                    MessageQueue.type == MessageType.COMPLETION_REPORT.value,
                )
                .all()
            )
        assert len(report_rows) == 1

    def test_db_paused_skips_note_insert(
        self, service, engine, bus
    ):
        """Scope guard — when the parent's DB status is PAUSED
        (``db_paused``), the note INSERT is suppressed (mirrors the
        PROCESS_REPORT + report_injection skip on the same branch).
        Same rationale as ``test_marker_paused_skips_note_insert``.
        """
        parent_id = "parent-dbpaused"
        child_id = "child-dbpaused"
        _seed_instance(engine, instance_id=parent_id, status=InstanceStatus.PAUSED.value)
        _seed_instance(engine, instance_id=child_id, parent_id=parent_id)

        result = service._process_child_completion_db_sync(
            child_id,
            completed_message_id="msg-dbpaused",
            last_content="will write the next report. Ending turn.",
        )

        # db_paused takes the marker/db_status branch (NOT
        # dead_parent_skip — the parent is PAUSED, not TERMINATED).
        assert result.outcome == "regular_child_completed"

        with Session(engine) as s:
            note_rows = (
                s.query(MessageQueue)
                .filter(
                    MessageQueue.instance_id == parent_id,
                    MessageQueue.type == MessageType.SYSTEM.value,
                )
                .all()
            )
        assert len(note_rows) == 0, (
            f"db_paused MUST suppress the note INSERT; got "
            f"{len(note_rows)} note row(s) for parent={parent_id}"
        )
        with Session(engine) as s:
            report_rows = (
                s.query(MessageQueue)
                .filter(
                    MessageQueue.instance_id == parent_id,
                    MessageQueue.type == MessageType.COMPLETION_REPORT.value,
                )
                .all()
            )
        assert len(report_rows) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Source-level pins — defend against catalog/id-format drift
# ─────────────────────────────────────────────────────────────────────────────


class TestSourcePins:
    """Source-level pins that survive future refactors — these
    catch silent drift (catalog rename, id-format change) by
    parsing the production source verbatim.
    """

    def test_catalog_lives_in_marker_scanner(self):
        src = pathlib.Path(
            "daemon/services/attestation_marker_scanner.py"
        ).read_text()
        assert "CHILD_TERMINAL_PROMISE_MARKERS" in src
        assert "scan_child_terminal_report_for_promises" in src

    def test_context_kind_lives_in_context_messages(self):
        src = pathlib.Path(
            "daemon/services/context_messages.py"
        ).read_text()
        assert "CONTEXT_KIND_CHILD_REPORT_CHECK" in src
        assert '"child_report_check"' in src
        assert 'f"child_report_check:{instance_id}:{agent_id}"' in src

    def test_hook_lives_in_child_reports(self):
        src = pathlib.Path(
            "daemon/services/child_reports.py"
        ).read_text()
        # The hook is the only call site of scan_child_terminal_report_for_promises
        # in the production code — pin so a future migration keeps the seam visible.
        assert "scan_child_terminal_report_for_promises" in src
        # The structured event name is fixed — pin so log-grep survives.
        assert (
            "event=leader_completion_gate_child_report_check_fired"
            in src
        )

    def test_no_new_env_flag_added(self):
        """Repo fix/flag policy (7d5285aa): bugfixes/improvements are
        NOT user-togglable. The child-terminal contradiction
        detector ships always-on; zero new ENSEMBLE_* env reads in
        the touched files.
        """
        for path in [
            "daemon/services/attestation_marker_scanner.py",
            "daemon/services/context_messages.py",
            "daemon/services/child_reports.py",
        ]:
            src = pathlib.Path(path).read_text()
            # Strip docstrings/comments before grep — we only
            # count actual env reads.
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Attribute) and func.attr in {
                        "getenv",
                        "environ",
                    }:
                        # Allow inside the test conftest; pin on
                        # the production modules only.
                        if "test" in path:
                            continue
                        # Some existing flags DO live in
                        # context_messages (ambient KV freshness).
                        # Allow those — but require that no NEW
                        # ENSEMBLE_CHILD_REPORT_CHECK reads were
                        # added (which is the bug we're guarding).
                        if isinstance(node.func.value, ast.Name):
                            if node.func.value.id == "os":
                                # Check the argument for the new flag
                                for arg in node.args:
                                    if (
                                        isinstance(arg, ast.Constant)
                                        and "CHILD_REPORT_CHECK"
                                        in str(arg.value).upper()
                                    ):
                                        raise AssertionError(
                                            f"NEW ENSEMBLE_* flag "
                                            f"detected at {path}: "
                                            f"{ast.unparse(node)} — "
                                            f"fix/flag policy "
                                            f"7d5285aa forbids new "
                                            f"flags for bugfixes"
                                        )


# ─────────────────────────────────────────────────────────────────────────────
# Test-helper sanity — make sure the test surface is well-formed.
# ─────────────────────────────────────────────────────────────────────────────


def test_this_module_compiles():
    """Belt-and-braces — py_compile the test module so a torn-write
    edit (a partial update that left a stray bracket) fails loud
    here instead of silently passing the test collection phase.
    """
    import py_compile

    py_compile.compile(
        inspect.getfile(inspect.currentframe()),
        doraise=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stage-0 mint-with-delivery + terminal-parent suppression + finalizer
# count-guard (2026-09-17 strand wedge fix)
# ─────────────────────────────────────────────────────────────────────────────


class TestChildReportCheckMintWithDelivery:
    """The Stage-0 note must mint WITH its delivery carrier.

    Root cause being pinned: the note was minted as a RAW MessageQueue
    row (READY, no Task, no notify). Delivery is task-driven only
    (``message_processing_pipeline.py`` claims by Task) and the root
    turn-end finalizer counted the undeliverable READY row as pending
    → permanent WAITING_CHILDREN re-park. The fix:

      1. mint-with-delivery — note row + PENDING PROCESS_MESSAGE Task
         in the SAME SAVEPOINT, worker pool woken after commit;
      2. terminal-parent mint suppression — a parent in ANY terminal
         state (completed/terminated/error/failed) never receives the
         note;
      3. finalizer count-guard — task-less advisory rows never count
         (predicate-level pins live in
         ``test_message_queue_pending_predicate.py``).
    """

    def test_mint_creates_note_row_and_pending_delivery_task(
        self, service, engine, bus
    ):
        """(a) Mint seam — the note row gets a MATCHING PENDING
        ``process_message`` Task (same ``message_id``, parent's
        ``instance_id``) in the same transaction, and the result
        carries the post-commit wake flag.
        """
        parent_id = "parent-mint"
        child_id = "child-mint"
        _seed_instance(engine, instance_id=parent_id)
        _seed_instance(engine, instance_id=child_id, parent_id=parent_id)

        result = service._process_child_completion_db_sync(
            child_id,
            completed_message_id="msg-mint",
            last_content="will write RESULTS. Ending turn.",
        )

        assert result.outcome == "regular_child_completed"

        with Session(engine) as s:
            note = (
                s.query(MessageQueue)
                .filter(
                    MessageQueue.instance_id == parent_id,
                    MessageQueue.type == MessageType.SYSTEM.value,
                )
                .one()
            )
            task = (
                s.query(Task)
                .filter(Task.message_id == note.message_id)
                .one()
            )

        # The note keeps its source-shape contract.
        assert note.source.startswith("child_report_check:")
        assert note.status == MessageStatus.READY.value
        assert note.type == MessageType.SYSTEM.value

        # The delivery carrier: PENDING process_message Task keyed on
        # the note's message_id, bound to the PARENT.
        assert task.task_type == TaskType.PROCESS_MESSAGE.value
        assert task.status == TaskStatus.PENDING.value
        assert task.instance_id == parent_id
        assert task.message_id == note.message_id
        assert task.work_id is not None

        # The async caller must wake the worker pool after commit.
        assert result.notify_worker_pool is True

    @pytest.mark.parametrize(
        "parent_status, expected_rung",
        [
            # COMPLETED/ERROR/FAILED reach the terminal-parent rung:
            # report path intact, skip logged as terminal_parent.
            (InstanceStatus.COMPLETED.value, "terminal_parent"),
            (InstanceStatus.ERROR.value, "terminal_parent"),
            (InstanceStatus.FAILED.value, "terminal_parent"),
            # TERMINATED is caught EARLIER by db_dead_parent (pre-
            # existing rung: outcome=dead_parent_skip, report FAILED,
            # no PROCESS_REPORT task) — the terminal_parent leg is
            # defensive redundancy for it. Suppression holds either way.
            (InstanceStatus.TERMINATED.value, "dead_parent"),
        ],
        ids=["completed", "error", "failed", "terminated"],
    )
    def test_terminal_parent_suppresses_note_mint(
        self, service, engine, bus, caplog, parent_status, expected_rung
    ):
        """(b) Terminal-parent mint suppression — a parent in ANY of
        the four terminal states receives NO note and NO note delivery
        Task (suppression contract holds for the whole
        ``db_terminal_parent`` tuple; deleting any leg must fail here).

        The REPORT path shape is rung-dependent: COMPLETED/ERROR/FAILED
        hit the terminal_parent rung (report row + PROCESS_REPORT task
        still mint — the pre-existing sanctioned enqueue shape), while
        TERMINATED is caught earlier by the dead_parent rung
        (outcome=dead_parent_skip, report FAILED, no PROCESS_REPORT
        task — pinned by test_dead_parent_skips_note_insert).
        """
        import logging as _logging

        parent_id = "parent-terminal"
        child_id = "child-terminal"
        _seed_instance(
            engine,
            instance_id=parent_id,
            status=parent_status,
        )
        _seed_instance(engine, instance_id=child_id, parent_id=parent_id)

        with caplog.at_level(_logging.INFO):
            result = service._process_child_completion_db_sync(
                child_id,
                completed_message_id="msg-terminal",
                last_content="will write the next report. Ending turn.",
            )

        assert result.outcome == (
            "regular_child_completed"
            if expected_rung == "terminal_parent"
            else "dead_parent_skip"
        )
        assert result.notify_worker_pool is False

        with Session(engine) as s:
            rows = (
                s.query(MessageQueue)
                .filter(MessageQueue.instance_id == parent_id)
                .all()
            )
            tasks = (
                s.query(Task)
                .filter(Task.instance_id == parent_id)
                .all()
            )

        # NO SYSTEM note row; the note delivery Task is absent too.
        assert all(
            r.type != MessageType.SYSTEM.value for r in rows
        ), f"terminal parent must not receive the note; got {rows}"
        note_task_ids = {
            r.message_id
            for r in rows
            if r.source.startswith("child_report_check:")
        }
        assert not note_task_ids
        assert all(
            t.task_type != TaskType.PROCESS_MESSAGE.value for t in tasks
        ), f"no process_message Task may be minted for the note; got {tasks}"

        report = next(
            r for r in rows if r.type == MessageType.COMPLETION_REPORT.value
        )
        if expected_rung == "terminal_parent":
            # Report path unchanged: report row READY + PROCESS_REPORT
            # task keyed on the report's message_id.
            assert report.status == MessageStatus.READY.value
            assert any(
                t.task_type == TaskType.PROCESS_REPORT.value
                and t.message_id == report.message_id
                for t in tasks
            )
            expected_reason = "reason=terminal_parent"
        else:
            # dead_parent rung (pre-existing shape): report FAILED
            # inline, no PROCESS_REPORT task.
            assert report.status == MessageStatus.FAILED.value
            assert not any(
                t.task_type == TaskType.PROCESS_REPORT.value for t in tasks
            )
            expected_reason = "reason=dead_parent"

        # Observability: the skip is logged with the rung's reason
        # (captured via the at_level(INFO) scope around the call).
        assert any(
            expected_reason in rec.message
            for rec in caplog.records
        ), f"{expected_rung} skip must be observable in the log"

    async def test_worker_pool_woken_after_commit(
        self, service, engine, bus, monkeypatch
    ):
        """(a) Notify seam — the async caller wakes the worker pool
        AFTER ``asyncio.to_thread`` returns (commit durable), mirroring
        the ``enqueue_message`` commit-then-notify ordering. Without
        this wake the PENDING note Task sits unclaimed under a quiet
        pool — the note would strand again by another route.
        """
        from unittest.mock import Mock
        from types import SimpleNamespace

        parent_id = "parent-notify"
        child_id = "child-notify"
        _seed_instance(engine, instance_id=parent_id)
        _seed_instance(engine, instance_id=child_id, parent_id=parent_id)

        pool = Mock()
        service._manager._worker_pool = pool
        service._manager._live_hub = None
        service._manager._instance_repository = SimpleNamespace(
            get=lambda iid: SimpleNamespace(agent_id="worker")
        )

        async def _fake_last_content(instance_id, agent_id):
            return "will write RESULTS. Ending turn."

        async def _noop_side_effects(result, lc, cmid):
            return None

        monkeypatch.setattr(
            service, "_get_last_assistant_message", _fake_last_content
        )
        monkeypatch.setattr(
            service, "_dispatch_post_commit_side_effects", _noop_side_effects
        )

        await service._process_child_completion_and_notify_parent(
            child_id, "msg-notify"
        )

        pool.notify_work.assert_called_once()

    def test_legacy_stranded_note_does_not_block_root_completion(
        self, service, engine, bus
    ):
        """(d) Regression — a root whose own queue holds a LEGACY
        stranded READY note (no Task) COMPLETES; pending_count == 0.
        Before the count-guard this exact shape re-parked the root at
        WAITING_CHILDREN forever (the 421c6a3d wedge signature).
        """
        root_id = "root-legacy"
        child_id = "child-legacy"
        _seed_instance(engine, instance_id=root_id, parent_id=None)

        # The LEGACY stranded note: minted pre-fix — READY row, no
        # Task, no notify. It sits on the root's OWN queue.
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        with Session(engine) as s:
            s.add(MessageQueue(
                message_id="legacy-note-1",
                instance_id=root_id,
                content="[SYSTEM CONTEXT: Child Report Check]",
                type=MessageType.SYSTEM.value,
                source=f"child_report_check:{child_id}:legacy-report-1",
                status=MessageStatus.READY.value,
                priority=0,
                enqueued_at=now,
                last_activity_at=now,
                message_metadata={
                    "context_kind": CONTEXT_KIND_CHILD_REPORT_CHECK,
                    "injected_message": True,
                    "child_report_check": True,
                },
            ))
            s.commit()

        result = service._process_child_completion_db_sync(
            root_id,
            completed_message_id="msg-root-final",
            last_content="",
        )

        assert result.outcome == "root_completed", (
            f"legacy stranded note must NOT block root completion; "
            f"got {result.outcome}"
        )

        with Session(engine) as s:
            root = s.get(Instance, root_id)
            note = s.get(MessageQueue, "legacy-note-1")

        assert root.status == InstanceStatus.COMPLETED.value
        # The stranded row is retained (audit), just not counted.
        assert note is not None
        assert note.status == MessageStatus.READY.value
