"""Pure-function catalog tests for the LCA child-terminal contradiction
detection catalog (D-CTD-7, 2026-09-18).

The advisory ``[SYSTEM CONTEXT: Child Report Check]`` note was REMOVED
2026-09-18 (user decision): the note MESSAGE itself is gone, but the
17-pattern substring catalog
(:data:`daemon.services.attestation_marker_scanner.CHILD_TERMINAL_PROMISE_MARKERS`)
and the pure-function scanner
(:func:`daemon.services.attestation_marker_scanner.scan_child_terminal_report_for_promises`)
are KEPT. The catalog is a public module surface pinned by tests so a
future re-attachment of a different consumer (e.g. bundling matched
terms into the A-section of the fused-judge input) is unblocked.

This file pins the pure-function surface — the catalog membership, the
scanner matrix on the spec seed phrases (positive / benign / FP-tight
near-FP), and the case-insensitive / empty-input / trailing-space
guards. Note-mint tests are deleted with the note (D-CTD-7).
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from daemon.services.attestation_marker_scanner import (
    CHILD_TERMINAL_PROMISE_MARKERS,
    ChildTerminalPromiseScanResult,
    scan_child_terminal_report_for_promises,
)


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
# Source-level pins — defend against catalog/event-removal drift
# ─────────────────────────────────────────────────────────────────────────────


class TestSourcePins:
    """Source-level pins that survive future refactors — these
    catch silent drift (catalog rename, accidental note-mint
    resurrection) by parsing the production source verbatim.

    D-CTD-7 (2026-09-18) REMOVED the LCA ``[SYSTEM CONTEXT: Child
    Report Check]`` advisory note mint site. These pins are the
    resurrection-loud half of the contract: any future PR that
    re-introduces the mint code, the saved-point log rows, or a
    new ``ENSEMBLE_*_CHILD_REPORT_CHECK`` env flag will fail this
    test class. The catalog and the A-signal path are pinned
    positively (test_catalog_byte_identical) elsewhere in the
    attestation test family.
    """

    def test_catalog_lives_in_marker_scanner(self):
        """Catalog + scanner function live in
        ``daemon/services/attestation_marker_scanner.py`` —
        byte-identical surface the user pinned for preservation.
        """
        src = pathlib.Path(
            "daemon/services/attestation_marker_scanner.py"
        ).read_text()
        assert "CHILD_TERMINAL_PROMISE_MARKERS" in src
        assert "scan_child_terminal_report_for_promises" in src

    def test_note_mint_site_is_gone_from_child_reports(self):
        """D-CTD-7 resurrection pin — the note mint site is removed.

        Asserts the absence of the mint-site production-code markers
        in ``daemon/services/child_reports.py``. Any future PR that
        re-introduces the mint code (the SAVEPOINT, the
        ``child_report_check`` source prefix, the structured log
        events emitted at mint time, the ``child_report_check_terms`` /
        ``child_report_check`` kwargs) will fail this test loud.

        The pin targets production-code shapes only — comment-only
        occurrences (e.g. the removal-history block at the call-site
        comment) are permitted. Each needle is a verbatim string the
        old mint code emitted from inside an f-string / context-
        manager body, NOT a substring that could appear in a comment.
        """
        src = pathlib.Path(
            "daemon/services/child_reports.py"
        ).read_text()
        # Production-code shapes that uniquely identify the old
        # mint block (each one is what the mint code WROTE inside
        # an f-string, not what a comment can mention):
        #   * logger.info / logger.error / logger.warning followed by
        #     the event= prefix (would-be mint-time log calls),
        #   * the MessageQueue constructor with source=child_report_check:,
        #   * the Task constructor for the PROCESS_MESSAGE delivery,
        #   * session.begin_nested() (the SAVEPOINT),
        #   * _note_message.additional_kwargs[ (the marker kwargs),
        #   * _stable_id_for(...child_report_check) (the stable-id call).
        for needle in (
            "source=(\n                            f\"child_report_check:",
            "type=MessageType.SYSTEM.value,\n                        status=MessageStatus.READY.value,\n                        # priority=0 so the note preempts",
            "Task(\n                        task_type=TaskType.PROCESS_MESSAGE.value,\n                        instance_id=instance.parent_id,\n                        message_id=note_message_id,",
            "session.begin_nested()\n                    try:\n                        session.add(child_report_check_note_row)",
            "_note_message.additional_kwargs[\"child_report_check\"] = True",
            "_stable_id_for(\n                        \"child_report_check\",",
            "child_report_check_note_row = MessageQueue(",
            "note_delivery_task = Task(",
            "f\"event=leader_completion_gate_child_report_check_fired \"",
            "f\"event=leader_completion_gate_child_report_check_failed \"",
        ):
            assert needle not in src, (
                f"D-CTD-7: note mint code resurrected in "
                f"daemon/services/child_reports.py — found {needle!r}. "
                f"Re-introduction requires reopening D-CTD-7."
            )

    def test_no_new_env_flag_added(self):
        """Repo fix/flag policy (7d5285aa): bugfixes/improvements are
        NOT user-togglable. The child-terminal contradiction
        catalog / scanner ship always-on; zero new ENSEMBLE_*
        env reads referencing CHILD_REPORT_CHECK are permitted.
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

