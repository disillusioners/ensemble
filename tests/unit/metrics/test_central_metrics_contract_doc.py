"""P1 contract doc — the frozen posture/contract artefact (registered pack
``central_metrics_contract_doc``).

The contract is a DOCUMENT, so its pins are document pins: the pack
parses the machine-checkable fenced blocks the contract doc carries and
asserts their exact contents.  Every behavioural pin is planted
independently and shown RED→GREEN (the mutation table for this leg):

* ``P1-M1`` — delete an activation-surface row ⇒ the surfaces pin reds;
* ``P1-M2`` — widen the availability reason set ⇒ the closed-set pin reds;
* ``P1-M3`` — drop a truth-table invariant ⇒ the invariants pin reds.

The doc is repo-only (``docs/`` is not part of the release payload), so
this pack is a Tier-0 changed-pack leg — it needs no dual-side leg.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CONTRACT_DOC = Path("docs/architecture/metrics-query-contract.md")

#: The six activation surfaces the contract must name (plan task 1.1).
EXPECTED_SURFACES = (
    "listener_body",
    "api_metrics_text",
    "query_routes",
    "census_db_work",
    "registry_accumulation",
    "fe_charts_polling",
)

#: The three query routes (the ONE door, D4).
EXPECTED_ROUTES = (
    "GET /api/metrics/families",
    "GET /api/metrics/snapshot",
    "GET /api/metrics/series",
)

#: The CLOSED availability reason set (plan §frozen contract).
EXPECTED_AVAILABILITY_REASONS = (
    "census_gated",
    "census_not_running",
    "capture_off",
    "series_absent",
    "listener_disabled",
)

#: The error vocabulary — every expected condition has a typed status.
#:
#: The 403 token is the SHIPPED UPPERCASE spelling (the council's D-c
#: ruling): ``daemon/auth/decorators.py`` emits ``SCOPE_INSUFFICIENT``,
#: and the contract doc carries that exact token.  The literal below
#: follows the ruling it pins — the DOC is the pinned artefact and the
#: wire is untouched.
EXPECTED_ERROR_VOCABULARY = (
    "404 unknown_family",
    "400 invalid_label_filter",
    "400 invalid_cursor",
    "503 registry_unavailable",
    "503 AUTH_NOT_CONFIGURED",
    "403 SCOPE_INSUFFICIENT",
)

#: The five pinned truth-table invariants (phase 1 task 1.6).
EXPECTED_INVARIANTS = (
    "auth_behaviour_identical_across_switch_states",
    "registry_series_set_identical_for_non_switch_derived_families",
    "only_permitted_body_differences",
    "check_site_census_equals_two",
    "no_direct_switch_read_in_query_package",
)

#: The nine registered gate-pack ids (approver N-a).
EXPECTED_PACK_IDS = (
    "central_metrics_contract_doc",
    "metrics_p2_dual_side",
    "metrics_p3_dual_side",
    "metrics_scope_cage_dual_side",
    "metrics_p4_w1_dual_side",
    "metrics_p4_w2_dual_side",
    "metrics_p4_w3_dual_side",
    "metrics_fe_dual_side",
    "central_metrics_ledger_reconciliation",
)


def _repo_root() -> Path:
    """The repository root (the doc path is repo-relative)."""
    return Path(__file__).resolve().parents[3]


def _doc_text() -> str:
    path = _repo_root() / CONTRACT_DOC
    assert path.is_file(), f"missing the contract doc: {CONTRACT_DOC}"
    return path.read_text(encoding="utf-8")


def _block(text: str, info_string: str) -> list[str]:
    """Return the lines of the fenced block carrying ``info_string``."""
    pattern = re.compile(
        r"^```" + re.escape(info_string) + r"\s*$(.*?)^```\s*$",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    assert match is not None, f"the contract doc has no ```{info_string}``` block"
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


class TestContractDocExists:
    def test_the_doc_is_committed_at_the_declared_path(self) -> None:
        assert (_repo_root() / CONTRACT_DOC).is_file()

    def test_the_doc_declares_its_provenance_and_status(self) -> None:
        text = _doc_text()
        assert "central-metrics-v1" in text
        assert "FROZEN" in text


class TestActivationSurfaces:
    """Plan task 1.1 — the doc names ALL SIX surfaces (mutation P1-M1)."""

    def test_the_surfaces_block_lists_exactly_the_six_surfaces(self) -> None:
        assert _block(_doc_text(), "activation-surfaces") == list(EXPECTED_SURFACES)

    def test_every_surface_is_named_in_prose(self) -> None:
        text = _doc_text()
        for surface in EXPECTED_SURFACES:
            assert surface in text, f"surface {surface} is not named"

    def test_the_switch_never_touches_authenticated_surfaces(self) -> None:
        assert "The switch never touches authenticated surfaces" in _doc_text()

    def test_the_listener_default_off_is_body_level(self) -> None:
        text = _doc_text()
        assert "at the BODY level" in text
        assert "the socket still binds" in text


class TestQueryRoutes:
    """D4 — one door, three routes."""

    def test_the_routes_block_lists_the_three_routes(self) -> None:
        assert _block(_doc_text(), "query-routes") == list(EXPECTED_ROUTES)

    def test_the_routes_carry_the_maintenance_read_scope(self) -> None:
        assert "maintenance:read" in _doc_text()


class TestAvailabilityModel:
    """The CLOSED reason set (mutation P1-M2 — widening it must red)."""

    def test_the_availability_set_is_closed_and_exact(self) -> None:
        assert _block(_doc_text(), "availability-reasons") == list(
            EXPECTED_AVAILABILITY_REASONS
        )

    def test_proc_unavailable_is_not_in_the_v1_set(self) -> None:
        assert "proc_unavailable" not in _block(
            _doc_text(), "availability-reasons"
        )

    def test_the_door_never_emits_listener_disabled(self) -> None:
        text = _doc_text()
        assert "the query door never emits it" in text


class TestErrorVocabulary:
    """Every expected condition has a typed status — never a 500."""

    def test_the_error_vocabulary_is_exact(self) -> None:
        assert _block(_doc_text(), "error-vocabulary") == list(
            EXPECTED_ERROR_VOCABULARY
        )

    def test_the_fail_closed_seam_precedes_any_registry_read(self) -> None:
        assert "before any registry read" in _doc_text()


class TestTruthTableInvariants:
    """Plan task 1.6 — the five invariants (mutation P1-M3)."""

    def test_the_invariants_block_lists_exactly_five(self) -> None:
        block = _block(_doc_text(), "truth-table-invariants")
        assert block == list(EXPECTED_INVARIANTS)

    def test_the_check_site_census_is_pinned_at_two(self) -> None:
        text = _doc_text()
        assert "remains exactly 2" in text
        assert "metrics_listener.py:419" in text
        assert "metrics_census.py:355" in text

    def test_the_query_package_never_reads_the_switch(self) -> None:
        assert "The query package NEVER reads the switch" in _doc_text()


class TestDisclosureAndPacks:
    """Plan task 1.7 — the disclosure text + the gate-pack declaration."""

    def test_the_pack_ids_block_lists_the_nine_registered_ids(self) -> None:
        assert _block(_doc_text(), "gate-pack-ids") == list(EXPECTED_PACK_IDS)

    def test_the_disclosure_names_the_token_revocation_lever(self) -> None:
        assert "token revocation" in _doc_text()

    def test_the_disclosure_carries_the_am19_corrections(self) -> None:
        text = _doc_text()
        assert "no metrics available yet" in text
        assert "metrics disabled" in text  # quoted as the forbidden copy
        assert "description-only" in text

    def test_the_scope_over_grant_is_disclosed(self) -> None:
        assert "checkpoint-cleanup-EXECUTE" in _doc_text()

    def test_the_mutation_namespace_and_slicing_proof_are_declared(self) -> None:
        text = _doc_text()
        assert "P{n}-Mn" in text
        assert "Slicing proof obligation" in text
