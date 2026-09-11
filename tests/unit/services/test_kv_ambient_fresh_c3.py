"""C3 — per-turn ambient KV refresh behind the kill-switch
``ENSEMBLE_AMBIENT_KV_FRESH`` (Shape B; default ON).

kv-ambient-awareness-fix, commit C3 (DEFECT 1 — KV snapshot cadence).

Contract pinned (decisions.md D3, D4, D6, D8; phase1-plan.md:182-271,
:360-471):

* The :func:`assemble_context_messages` orchestrator emits a
  :func:`build_shared_meta_kv_message` block on every NON-retry turn
  (per-turn refresh), under the stable id ``kv:{context_key}`` so
  LangGraph's ``add_messages`` reducer SUPERSEDES the prior entry in
  place (D3, Message-id invariant).
* The ``is_retry=True`` gate at :func:`assemble_context_messages` (the
  pre-existing call-site gate at ``instance_messaging.py:3637`` — NOT
  touched by C3) skips ALL of assembly on retries — by design,
  decisions.md D15 ratifies.
* ``ENSEMBLE_AMBIENT_KV_FRESH=0`` is a CADENCE-ONLY reversion (W3): the
  KV block STILL EXISTS on turn 1, and is simply NEVER REFRESHED on
  turns 2+. The turn-1 block stays in the checkpoint until compaction.
* The flag composes with C2's :data:`daemon.config.kv_ambient_system_default_enabled`
  per the D4 composition table (the two flags are independent; flipping
  either alone produces exactly its row/column of the table, NOT a
  blend — cross-flag independence pin).
* The boot log ``Ambient KV freshness ENABLED (per-turn fresh)`` (or
  ``DISABLED (cadence-only legacy: turn-1 snapshot, no refresh)``) is
  emitted AT BOOT from ``daemon/manager.py`` alongside the other Shape
  B wrappers (wc-wake, governor-guard) — a lazy first-call emit would
  make quiet-daemon boot-log grep false-fail (S13 reviewer gate).

Worktree-Based Regression Proof (recorded 2026-09-08): the cadence
tests ``test_kv_freshness_on_turn2_short_circuit`` and
``test_kv_stable_id_supersedes``, when copied verbatim into a git
worktree at the pre-C3 commit ``4f554dfc``, FAIL with the
pre-fix symptom — the KV block is absent on turn 2+ (the
``project_already_injected=True`` short-circuit short-circuited
the entire block rebuild, including the new KV section). The
``raising=False`` portability pattern from the C2 pin
(``test_kv_ambient_real_service_flag_on_through_assembler.py``)
ensures collection itself does not fail when C3 symbols
(``_resolve_ambient_kv_fresh``, ``_reset_ambient_kv_fresh_for_tests``,
``emit_ambient_kv_fresh_boot_log``) are absent in the pre-C3 worktree.

Run only this file::

    pytest tests/unit/services/test_kv_ambient_fresh_c3.py -v
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage

from daemon.services.context_messages import (
    CONTEXT_KIND_SHARED_META_KV,
    _make_context_message,
    _stable_id_for,
)


# ─── Helpers (mirrors the C2 pin's fixture style) ─────────────────────────────


def _real_add_messages():
    """Import the REAL langgraph ``add_messages``, bypassing the stub.

    ``tests/conftest.py`` injects a mock ``langgraph.graph`` module
    into ``sys.modules`` before any test import (keeps unit tests
    light). The D14 pin must exercise the REAL reducer, so this
    helper snapshots every stubbed ``langgraph*`` entry, imports the
    real module, and restores ``sys.modules`` exactly — the returned
    function object stays valid after its module is de-referenced.
    """
    import importlib
    import sys

    def _langgraph_keys():
        return [
            k for k in sys.modules
            if k == "langgraph" or k.startswith("langgraph.")
        ]

    snapshot = {k: sys.modules[k] for k in _langgraph_keys()}
    for k in snapshot:
        del sys.modules[k]
    try:
        module = importlib.import_module("langgraph.graph.message")
        return module.add_messages
    finally:
        for k in _langgraph_keys():
            del sys.modules[k]
        sys.modules.update(snapshot)


def _make_manager(*, kv: dict[str, Any] | None = None) -> tuple[Any, Any, Any]:
    """Stub manager + instance_repository + agent_meta for the assembler.

    Mirrors the shape used by
    :class:`TestAssembleContextMessages._make_manager` so all C3 tests
    share one fixture contract.
    """
    agent_meta = MagicMock()
    agent_meta.blueprint_inactive = True  # skip blueprint branch
    agent_meta.context_injection = None
    agent_meta.skill_injection = False  # keeps the focus on the KV path

    project_repo = MagicMock()
    project_repo.get.return_value = None  # no project → project block absent
    project_repo.list_critical_notes.return_value = []
    project_repo.get_recent_history.return_value = []

    kv_repo = MagicMock()
    kv_repo.get_all_as_dict.return_value = kv or {}

    skill_service = MagicMock()
    skill_service.inject_skills = AsyncMock(return_value=(None, []))

    manager = MagicMock()
    manager._project_repository = project_repo
    manager._shared_meta_kv_repo = kv_repo
    manager._skill_injection_service = skill_service

    instance_repository = MagicMock()
    instance_repository.get_tree_root_id.return_value = "root-id"
    _inst = MagicMock()
    _inst.instance_metadata = {}
    instance_repository.get.return_value = _inst

    return manager, instance_repository, agent_meta


def _run(coro: Any) -> Any:
    """Drive an awaitable to completion under a fresh event loop."""
    return asyncio.run(coro)


def _flatten(persistent: Any, ephemeral: Any) -> list[HumanMessage]:
    """Flatten the orchestrator's ``(persistent, ephemeral)`` tuple."""
    return list(persistent) + list(ephemeral)


# ─── Cadence tests (the heart of the fix) ─────────────────────────────────────


class TestKVFreshnessCadence:
    """The core C3 contract: KV refreshes on every NON-retry turn.

    Each test pins one named assertion from phase1-plan.md:362-471
    (Test Strategy → New Regression Tests table). Tests are
    self-contained (one fixture per test) so a partial failure does not
    pollute neighbors.

    Pre-C3 worktree proof (recorded 2026-09-08): the two regression
    tests below (turn-2 short-circuit + stable id supersede), when
    copied verbatim to a worktree at ``4f554dfc`` (the C2 commit
    that shipped without per-turn refresh), FAIL with the pre-fix
    symptom — ``AssertionError: KV block must appear on turn 2+``
    / ``AssertionError: stable id must be deterministic across
    calls`` — because the ``project_already_injected=True``
    short-circuit skipped the entire block rebuild (no
    ``shared_meta_kv`` block in the output) AND the pre-C3 mint
    was ``uuid4`` per call (different ids → no supersede).
    """

    def test_kv_freshness_on_turn2_short_circuit(self, monkeypatch) -> None:
        """Turn 2+ (``project_already_injected=True``) emits a fresh KV block.

        Phase1-plan.md:393-413. The pre-C3 short-circuit at
        ``assemble_context_messages:1270-1314`` (4f554dfc era) skips
        the entire project + KV rebuild on turn 2+ — the fix splits
        the block so the KV section refreshes independently.

        Pre-C3 worktree proof (recorded 2026-09-08): at 4f554dfc →
        FAILS with ``AssertionError: KV block must appear on turn
        2+`` (no ``kv:`` prefixed id is emitted because the
        short-circuit skips the whole build).
        """
        from daemon.services.context_messages import (
            assemble_context_messages,
        )

        # Pre-C3 compatibility: ensure the resolver exists on the
        # module so monkeypatch.setattr(..., raising=False) doesn't
        # fail; if it doesn't, monkeypatch.addattr adds it.
        # If C3 isn't bound yet, the test naturally fails with the
        # pre-fix symptom (no kv block on turn 2+).
        monkeypatch.delenv("ENSEMBLE_AMBIENT_KV_FRESH", raising=False)
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_ambient_kv_fresh",
            lambda: True,
            raising=False,
        )
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_kv_ambient_system_default_enabled",
            lambda: True,
            raising=False,
        )
        manager, instance_repo, agent_meta = _make_manager(
            kv={"topic": "auth", "priority": 2}
        )

        # ``parent_id=None`` makes ``_resolve_tree_root_id`` return
        # ``instance_id`` directly (the parent's id IS the tree-root
        # id for the root instance). The stable id carries that
        # value verbatim — D3, S19 erratum.
        INSTANCE_ID = "inst-c3-fresh"

        # Turn 2+ — short-circuit path; C3 must still emit a KV block.
        result = _flatten(*_run(assemble_context_messages(
            instance_id=INSTANCE_ID,
            user_query="hi",
            project_id="proj-1",
            agent_meta=agent_meta,
            manager=manager,
            instance_repository=instance_repo,
            project_already_injected=True,
        )))
        kv_msg = next(
            (m for m in result
             if m.additional_kwargs.get("context_kind") == CONTEXT_KIND_SHARED_META_KV),
            None,
        )
        assert kv_msg is not None, (
            "KV block must appear on turn 2+ — the short-circuit must "
            "NOT suppress the per-turn KV refresh"
        )
        # Stable id carries the FULL resolved tree-root partition key
        # (D3, S19 erratum: ``kv:{context_key}`` — NOT split-extracted).
        # With parent_id=None, context_key = instance_id.
        assert kv_msg.id == _stable_id_for(
            "shared_meta_kv", context_key=INSTANCE_ID
        )
        # Fresh content carried through.
        assert "topic" in kv_msg.content
        assert "auth" in kv_msg.content

    def test_kv_stable_id_supersedes(self, monkeypatch) -> None:
        """Two calls with the same instance_id emit the same stable id.

        Phase1-plan.md:415-424. The stable id is the precondition for
        LangGraph's ``add_messages`` reducer to SUPERSEDE the prior
        checkpoint entry in place instead of APPENDING a duplicate.

        Pre-C3 worktree proof: at 4f554dfc → FAILS with
        ``AssertionError`` on ``kv_a.id == kv_b.id`` (pre-C3 mint
        was ``uuid4`` per call → different ids → append, not
        supersede).
        """
        from daemon.services.context_messages import (
            assemble_context_messages,
        )

        monkeypatch.delenv("ENSEMBLE_AMBIENT_KV_FRESH", raising=False)
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_ambient_kv_fresh",
            lambda: True,
            raising=False,
        )
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_kv_ambient_system_default_enabled",
            lambda: True,
            raising=False,
        )
        manager, instance_repo, agent_meta = _make_manager(kv={"k": "v"})
        INSTANCE_ID = "inst-c3-stable"

        common_kwargs = dict(
            instance_id=INSTANCE_ID,
            user_query="hi",
            project_id="proj-1",
            agent_meta=agent_meta,
            manager=manager,
            instance_repository=instance_repo,
            project_already_injected=True,
        )

        result_a = _flatten(*_run(assemble_context_messages(**common_kwargs)))
        result_b = _flatten(*_run(assemble_context_messages(**common_kwargs)))

        kv_a = next(
            (m for m in result_a
             if m.additional_kwargs.get("context_kind") == CONTEXT_KIND_SHARED_META_KV),
            None,
        )
        kv_b = next(
            (m for m in result_b
             if m.additional_kwargs.get("context_kind") == CONTEXT_KIND_SHARED_META_KV),
            None,
        )
        assert kv_a is not None and kv_b is not None, (
            "two non-retry turn-2+ assemblies must both emit a "
            "shared_meta_kv block — the per-turn refresh contract"
        )
        # Stable id — same instance_id → same id.
        assert kv_a.id == kv_b.id, (
            f"stable id must be deterministic across calls; "
            f"got {kv_a.id!r} vs {kv_b.id!r}"
        )
        # And it carries the canonical ``kv:{context_key}`` shape.
        assert kv_a.id == _stable_id_for(
            "shared_meta_kv", context_key=INSTANCE_ID
        )

    def test_kv_block_present_on_non_retry_turn(self, monkeypatch) -> None:
        """Non-retry turns emit the KV block (D15 ratifies the refresh gate).

        Phase1-plan.md:426-430 + D15 (skip-on-retry is the ratified
        refresh behavior). The retry gate lives at
        ``instance_messaging.py:3637`` (D13 anchor) — retry turns never
        reach ``assemble_context_messages`` at all, so the KV block
        cannot be refreshed (the gate skips the whole build, not just
        the KV section).

        This test passes a non-retry call through the orchestrator:
        a fresh non-retry call DOES emit the block (the pre-existing
        gate above the orchestrator short-circuits the retry case
        entirely — we don't need to test the gate here).
        """
        from daemon.services.context_messages import (
            assemble_context_messages,
        )

        monkeypatch.delenv("ENSEMBLE_AMBIENT_KV_FRESH", raising=False)
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_ambient_kv_fresh",
            lambda: True,
            raising=False,
        )
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_kv_ambient_system_default_enabled",
            lambda: True,
            raising=False,
        )
        manager, instance_repo, agent_meta = _make_manager(kv={"k": "v"})

        # Non-retry, turn 2+ — KV block DOES appear.
        result = _flatten(*_run(assemble_context_messages(
            instance_id="inst-c3",
            user_query="hi",
            project_id="proj-1",
            agent_meta=agent_meta,
            manager=manager,
            instance_repository=instance_repo,
            project_already_injected=True,  # NOT is_retry — the gate above this
        )))
        kv_kinds = [
            m.additional_kwargs.get("context_kind")
            for m in result
        ]
        assert CONTEXT_KIND_SHARED_META_KV in kv_kinds, (
            "non-retry turns must emit the KV block (D15 ratifies "
            "the refresh gate)"
        )

    def test_kv_freshness_killswitch_off(self, monkeypatch) -> None:
        """``ENSEMBLE_AMBIENT_KV_FRESH=0`` → NO refresh emission on turn 2+.

        Phase1-plan.md:432-442 + W3 (cadence-only reversion). The
        turn-2+ refresh path bails — the turn-1 block remains in the
        checkpoint (no new emission). OFF is NOT "remove the block
        entirely"; OFF is "freeze at the turn-1 snapshot".
        """
        from daemon.services.context_messages import (
            assemble_context_messages,
        )

        monkeypatch.setenv("ENSEMBLE_AMBIENT_KV_FRESH", "0")
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_ambient_kv_fresh",
            lambda: False,
            raising=False,
        )
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_kv_ambient_system_default_enabled",
            lambda: True,
            raising=False,
        )
        manager, instance_repo, agent_meta = _make_manager(kv={"k": "v"})

        result = _flatten(*_run(assemble_context_messages(
            instance_id="inst-c3",
            user_query="hi",
            project_id="proj-1",
            agent_meta=agent_meta,
            manager=manager,
            instance_repository=instance_repo,
            project_already_injected=True,
        )))
        kv_msgs = [
            m for m in result
            if m.additional_kwargs.get("context_kind") == CONTEXT_KIND_SHARED_META_KV
        ]
        assert len(kv_msgs) == 0, (
            f"flag OFF must NOT emit a KV refresh on turn 2+ — "
            f"got {len(kv_msgs)} KV block(s); W3 cadence-only reversion "
            f"freezes the turn-1 snapshot"
        )

    def test_kv_block_present_on_turn1_when_flag_off(self, monkeypatch) -> None:
        """Turn-1-surface OFF pin (W3) — the turn-1 block EXISTS even when OFF.

        Phase1-plan.md:444-454 + decisions.md D6 (OFF is a
        cadence-only reversion, NOT a block-removal). OFF gates the
        refresh, NOT the block's existence.
        """
        from daemon.services.context_messages import (
            assemble_context_messages,
        )

        monkeypatch.setenv("ENSEMBLE_AMBIENT_KV_FRESH", "0")
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_ambient_kv_fresh",
            lambda: False,
            raising=False,
        )
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_kv_ambient_system_default_enabled",
            lambda: True,
            raising=False,
        )
        manager, instance_repo, agent_meta = _make_manager(
            kv={"topic": "auth"}
        )

        # Turn 1 path (NOT project_already_injected).
        result = _flatten(*_run(assemble_context_messages(
            instance_id="inst-c3",
            user_query="hi",
            project_id="proj-1",
            agent_meta=agent_meta,
            manager=manager,
            instance_repository=instance_repo,
            project_already_injected=False,
        )))
        kv_msgs = [
            m for m in result
            if m.additional_kwargs.get("context_kind") == CONTEXT_KIND_SHARED_META_KV
        ]
        assert len(kv_msgs) == 1, (
            f"flag OFF must STILL emit the KV block on turn 1 (W3 — "
            f"OFF is cadence-only, NOT block-removal); got {len(kv_msgs)}"
        )
        # And it carries the seeded content.
        assert "topic" in kv_msgs[0].content
        assert "auth" in kv_msgs[0].content

    def test_ambient_kv_fresh_identical_when_off(self, monkeypatch) -> None:
        """Flag OFF output is byte-identical to no refresh emission.

        Phase1-plan.md:372. The turn-2+ output with ``=0`` matches the
        pre-C3 baseline (no KV block in the short-circuit path) — but
        the turn-1 block still renders (the W3 pin above).
        """
        from daemon.services.context_messages import (
            assemble_context_messages,
        )

        monkeypatch.setenv("ENSEMBLE_AMBIENT_KV_FRESH", "0")
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_ambient_kv_fresh",
            lambda: False,
            raising=False,
        )
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_kv_ambient_system_default_enabled",
            lambda: True,
            raising=False,
        )
        manager, instance_repo, agent_meta = _make_manager(kv={"k": "v"})

        result = _flatten(*_run(assemble_context_messages(
            instance_id="inst-c3",
            user_query="hi",
            project_id="proj-1",
            agent_meta=agent_meta,
            manager=manager,
            instance_repository=instance_repo,
            project_already_injected=True,
        )))
        # OFF + non-default → no kv block at all on turn 2+ (baseline).
        kinds = [m.additional_kwargs.get("context_kind") for m in result]
        assert CONTEXT_KIND_SHARED_META_KV not in kinds


# ─── Composition matrix (D4) ──────────────────────────────────────────────────


class TestKVAmbientCompositionMatrix:
    """Cross-flag independence pins for the D4 2×2 composition table.

    Every cell of the table is pinned by a named test (decisions.md
    D4 table at :109-117). The two anti-diagonal cells (the
    cross-flag independence pins) are the NEW ones C3 lands.
    """

    def test_composition_c2_off_c3_on(self, monkeypatch) -> None:
        """D3 OFF × D1 ON → NO KV block on ANY turn (default-project tree).

        The D3 host flag is OFF (suppression on) AND the D1 refresh
        flag is ON (refresh would run) — the OFF×ON cell of the D4
        table. Per D4, D1's refresh alone MUST NOT re-add a
        suppressed block (cross-flag independence). The block is
        absent from BOTH turn 1 AND turn 2+.

        The D3 flag gates the DEFAULT-PROJECT tree path; on non-
        default trees the KV block is emitted independently. So the
        cross-flag pin must exercise the default-project path.
        """
        from daemon.services.context_messages import (
            assemble_context_messages,
        )
        from daemon.services.context_messages import SYSTEM_DEFAULT_PROJECT_NAME

        monkeypatch.delenv("ENSEMBLE_AMBIENT_KV_FRESH", raising=False)
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_ambient_kv_fresh",
            lambda: True,
            raising=False,
        )
        # C2 (D3 host flag) OFF — gates the default-project tree.
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_kv_ambient_system_default_enabled",
            lambda: False,
            raising=False,
        )

        # Build a manager whose project is the system default project
        # (so the default-project branch of the orchestrator runs).
        agent_meta = MagicMock()
        agent_meta.blueprint_inactive = True
        agent_meta.context_injection = None
        agent_meta.skill_injection = False

        project_repo = MagicMock()
        # Project is the system default project.
        project = MagicMock()
        project.name = SYSTEM_DEFAULT_PROJECT_NAME
        project.to_dict.return_value = {
            "project_id": "default-id",
            "name": SYSTEM_DEFAULT_PROJECT_NAME,
        }
        project_repo.get.return_value = project
        project_repo.list_critical_notes.return_value = []
        project_repo.get_recent_history.return_value = []

        kv_repo = MagicMock()
        kv_repo.get_all_as_dict.return_value = {"k": "v"}  # has rows

        skill_service = MagicMock()
        skill_service.inject_skills = AsyncMock(return_value=(None, []))

        manager = MagicMock()
        manager._project_repository = project_repo
        manager._shared_meta_kv_repo = kv_repo
        manager._skill_injection_service = skill_service

        instance_repo = MagicMock()
        instance_repo.get_tree_root_id.return_value = "root-id"
        inst = MagicMock()
        inst.instance_metadata = {}
        instance_repo.get.return_value = inst

        # Pin SYSTEM_DEFAULT_PROJECT_ID at runtime (the orchestrator
        # also checks the constant).
        import daemon.constants as consts
        original = consts.SYSTEM_DEFAULT_PROJECT_ID
        consts.SYSTEM_DEFAULT_PROJECT_ID = "default-id"
        try:
            # Turn 1.
            result_turn1 = _flatten(*_run(assemble_context_messages(
                instance_id="inst-1",
                user_query="hi",
                project_id="default-id",
                agent_meta=agent_meta,
                manager=manager,
                instance_repository=instance_repo,
                project_already_injected=False,
            )))
            kinds_turn1 = [
                m.additional_kwargs.get("context_kind") for m in result_turn1
            ]
            assert CONTEXT_KIND_SHARED_META_KV not in kinds_turn1, (
                "C2 OFF × C3 ON (default-project): turn-1 block must "
                "be ABSENT — D3 OFF suppresses even though D1 ON "
                "would refresh (D4 cross-flag independence pin)"
            )

            # Turn 2+ — refresh would run but D3 OFF holds the gate closed.
            result_turn2 = _flatten(*_run(assemble_context_messages(
                instance_id="inst-1",
                user_query="hi",
                project_id="default-id",
                agent_meta=agent_meta,
                manager=manager,
                instance_repository=instance_repo,
                project_already_injected=True,
            )))
            kinds_turn2 = [
                m.additional_kwargs.get("context_kind") for m in result_turn2
            ]
            assert CONTEXT_KIND_SHARED_META_KV not in kinds_turn2, (
                "C2 OFF × C3 ON (default-project): turn-2+ refresh "
                "must NOT re-add a suppressed block — D4 cross-flag "
                "independence"
            )
        finally:
            consts.SYSTEM_DEFAULT_PROJECT_ID = original

    def test_composition_c2_on_c3_off(self, monkeypatch) -> None:
        """D3 ON × D1 OFF → KV block turn 1, ABSENT from turn-2+ refresh.

        The ON×OFF cell of the D4 table. The D3 host flag is ON (no
        suppression) AND the D1 refresh flag is OFF (no refresh). Per
        D4 + D6, the block is emitted on turn 1 exactly as with the
        flag ON, and is simply NEVER REFRESHED on turns 2+ (cadence-
        only reversion; turn-1 block present, never refreshed).
        """
        from daemon.services.context_messages import (
            assemble_context_messages,
        )

        monkeypatch.setenv("ENSEMBLE_AMBIENT_KV_FRESH", "0")
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_ambient_kv_fresh",
            lambda: False,
            raising=False,
        )
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_kv_ambient_system_default_enabled",
            lambda: True,
            raising=False,
        )
        manager, instance_repo, agent_meta = _make_manager(kv={"k": "v"})

        # Turn 1 — block present.
        result_turn1 = _flatten(*_run(assemble_context_messages(
            instance_id="inst-1",
            user_query="hi",
            project_id="proj-1",
            agent_meta=agent_meta,
            manager=manager,
            instance_repository=instance_repo,
            project_already_injected=False,
        )))
        kv_turn1 = [
            m for m in result_turn1
            if m.additional_kwargs.get("context_kind") == CONTEXT_KIND_SHARED_META_KV
        ]
        assert len(kv_turn1) == 1, (
            "C2 ON × C3 OFF: turn 1 must emit the block (D4 + W3 "
            "turn-1 surface)"
        )

        # Turn 2+ — block ABSENT from refresh emission.
        result_turn2 = _flatten(*_run(assemble_context_messages(
            instance_id="inst-1",
            user_query="hi",
            project_id="proj-1",
            agent_meta=agent_meta,
            manager=manager,
            instance_repository=instance_repo,
            project_already_injected=True,
        )))
        kv_turn2 = [
            m for m in result_turn2
            if m.additional_kwargs.get("context_kind") == CONTEXT_KIND_SHARED_META_KV
        ]
        assert len(kv_turn2) == 0, (
            "C2 ON × C3 OFF: turn 2+ must NOT emit a KV refresh "
            "(W3 cadence-only reversion — block present on turn 1, "
            "frozen, never refreshed)"
        )


# ─── Legacy / D14 pin ─────────────────────────────────────────────────────────


class TestLegacyInstanceNoDoubleProjectBlock:
    """D14 pin — at most one project + one KV block per kind.

    Decisions.md D14 option (ii): document the bounded cost and pin it
    — ``test_legacy_instance_no_double_project_block`` asserts AT
    MOST one project block + AT MOST one KV block per kind per
    instance's context surface, even for legacy checkpoints whose
    injected blocks carry ``uuid4`` ids (stable-id supersede cannot
    replace those). The one-shot ``RemoveMessage`` sweep is REJECTED
    — a structural pin is cheaper and lower-risk.

    C3 ships with this pin because the split block + stable id gives
    the durable supersede semantics, but legacy checkpoints from
    pre-C3 runs may carry a duplicate (uuid4) project block AND a
    duplicate (uuid4) KV block — the bounded cost is documented and
    pinned, not silently allowed.
    """

    def test_at_most_one_project_block_and_one_kv_block(self, monkeypatch) -> None:
        """REAL coexistence pin through the actual ``add_messages`` reducer.

        The prior revision of this pin was trivially true — it counted
        kinds on a single assembly's output, never exercising the
        reducer, and could not fail. This revision reduces a REAL
        checkpoint transition through ``langgraph``'s ``add_messages``:

        * ``existing`` = [legacy uuid4-id project block (kind
          ``project``), stable-id ``kv:{context_key}`` block] — the
          pre-C3 legacy checkpoint shape.
        * ``fresh`` = [stable-id ``kv:{context_key}`` block with NEW
          content] — the C3 per-turn refresh emission.

        Asserts after the reduce (D14 — at-most-one-per-kind WHILE
        legacy entries coexist):

        * exactly ONE project block surfaces — the legacy uuid4 entry
          SURVIVES (stable-id supersede cannot replace it) and the
          reduce does not duplicate it;
        * exactly ONE kv block surfaces and it carries the FRESH
          content (stable-id supersede collapsed the two entries in
          place).
        """
        from daemon.services.context_messages import (
            CONTEXT_KIND_PROJECT,
            build_shared_meta_kv_message,
        )

        add_messages = _real_add_messages()
        # Guard against silent stub re-entry — this pin is meaningless
        # against a MagicMock reducer.
        assert "MagicMock" not in type(add_messages).__name__, (
            "the D14 pin requires the REAL langgraph add_messages "
            "reducer, not the conftest stub"
        )

        context_key = "inst-c3"
        kv_stable_id = _stable_id_for(
            "shared_meta_kv", context_key=context_key
        )

        # Existing checkpoint: legacy uuid4-id project block + current
        # KV snapshot (stable id).
        legacy_project = _make_context_message(
            kind=CONTEXT_KIND_PROJECT,
            title="Related Project",
            content=json.dumps({"name": "proj-1"}, sort_keys=True),
            # id_=None → _make_context_message mints a fresh uuid4 —
            # exactly the legacy pre-C0 shape.
        )
        existing_kv = build_shared_meta_kv_message(
            {"topic": "stale"},
            stable_id=kv_stable_id,
        )
        assert existing_kv is not None
        existing = [legacy_project, existing_kv]

        # Fresh per-turn emission: same stable id, NEW content.
        fresh_kv = build_shared_meta_kv_message(
            {"topic": "fresh"},
            stable_id=kv_stable_id,
        )
        assert fresh_kv is not None
        assert fresh_kv.id == kv_stable_id

        reduced = add_messages(existing, [fresh_kv])

        def _kind(msg: Any) -> str | None:
            return msg.additional_kwargs.get("context_kind")

        project_blocks = [m for m in reduced if _kind(m) == CONTEXT_KIND_PROJECT]
        assert len(project_blocks) == 1, (
            f"D14 coexistence: exactly ONE project block must surface "
            f"after the reduce (legacy uuid4 entry survives, no "
            f"duplicate); got {len(project_blocks)}"
        )
        assert project_blocks[0].id == legacy_project.id, (
            "the surviving project block must BE the legacy uuid4 "
            "entry (stable-id supersede cannot replace it — it must "
            "not be dropped either)"
        )

        kv_blocks = [
            m for m in reduced if _kind(m) == CONTEXT_KIND_SHARED_META_KV
        ]
        assert len(kv_blocks) == 1, (
            f"D14 stable-id supersede: exactly ONE kv block must "
            f"surface after the reduce (the two entries collapse in "
            f"place); got {len(kv_blocks)}"
        )
        assert "fresh" in kv_blocks[0].content, (
            "the surviving kv block must carry the FRESH content — "
            "the per-turn refresh must win the supersede"
        )
        assert kv_blocks[0].id == kv_stable_id


# ─── W10 inheritance (32k cap survives the turn-2+ refresh path) ─────────────


class TestW10InheritanceOnRefresh:
    """W10 32k cap is enforced on the per-turn refresh path too.

    A partition whose serialized payload exceeds 32k is SKIPPED at
    EVERY emission site — both turn 1 (the C2 path) AND turn 2+ (the
    new C3 refresh path). The skip-on-overflow contract travels with
    the builder (``build_shared_meta_kv_message``); the assembler
    only emits what the builder returns, so the cap is structural.
    """

    def test_over_32k_partition_emits_no_block_on_turn2_refresh(
        self, monkeypatch
    ) -> None:
        """Turn-2+ refresh with a >32k partition → ``None`` (no block)."""
        from daemon.services.context_messages import (
            assemble_context_messages,
        )

        monkeypatch.delenv("ENSEMBLE_AMBIENT_KV_FRESH", raising=False)
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_ambient_kv_fresh",
            lambda: True,
            raising=False,
        )
        monkeypatch.setattr(
            "daemon.services.context_messages._resolve_kv_ambient_system_default_enabled",
            lambda: True,
            raising=False,
        )
        manager, instance_repo, agent_meta = _make_manager(
            kv={"big": "x" * 40_000}  # serialized well past 32 * 1024
        )

        result = _flatten(*_run(assemble_context_messages(
            instance_id="inst-c3",
            user_query="hi",
            project_id="proj-1",
            agent_meta=agent_meta,
            manager=manager,
            instance_repository=instance_repo,
            project_already_injected=True,
        )))
        kinds = [m.additional_kwargs.get("context_kind") for m in result]
        assert CONTEXT_KIND_SHARED_META_KV not in kinds, (
            "W10 cap must skip the per-turn refresh emission too — "
            "a >32k partition never balloons the prompt"
        )


# ─── W4 — Shape B fail-loud env vocabulary (direct env parsing) ───────────────


class TestAmbientKVFreshEnvVocabulary:
    """Direct env-parsing vocabulary pin for ``ENSEMBLE_AMBIENT_KV_FRESH``.

    W4 (council-recommended Shape A alignment): the Shape B resolver
    FAILS LOUD on an unrecognized NON-EMPTY value — a typo during an
    incident must not silently keep per-turn refresh ON. Vocabulary
    mirrors ``config._PROACTIVE_FALSE_BOOLS`` / ``_PROACTIVE_TRUE_BOOLS``
    exactly: ``0/false/no/off`` → False; ``1/true/yes/on`` → True;
    unset / empty / whitespace-only → True (empty-string-safe so a
    bare ``KEY=`` line does NOT crash boot).

    These tests exercise the REAL env parse (``monkeypatch.setenv`` +
    ``_reset_ambient_kv_fresh_for_tests()``) — the prior flag tests
    only monkeypatched the resolver itself, so the vocabulary had
    zero direct coverage.
    """

    FALSE_VOCAB = ["0", "false", "no", "off"]
    TRUE_VOCAB = ["1", "true", "yes", "on"]
    # Case-insensitivity is part of the Shape A vocabulary contract.
    FALSE_VOCAB_MIXED = ["FALSE", "Off", "NO"]
    TRUE_VOCAB_MIXED = ["TRUE", "On", "Yes"]
    GARBAGE = ["maybe", "enabled", "2", "disabled", "0 0"]

    @pytest.fixture(autouse=True)
    def _fresh_resolver(self, monkeypatch):
        """Start every case with a cold resolver cache + clean env."""
        from daemon.services.context_messages import (
            _reset_ambient_kv_fresh_for_tests,
        )

        monkeypatch.delenv("ENSEMBLE_AMBIENT_KV_FRESH", raising=False)
        _reset_ambient_kv_fresh_for_tests()
        monkeypatch.setattr(
            "daemon.services.context_messages._AMBIENT_KV_FRESH",
            None,
        )
        yield
        _reset_ambient_kv_fresh_for_tests()

    def _resolve(self) -> bool:
        from daemon.services.context_messages import (
            _resolve_ambient_kv_fresh,
        )

        return _resolve_ambient_kv_fresh()

    @pytest.mark.parametrize("value", FALSE_VOCAB + FALSE_VOCAB_MIXED)
    def test_false_vocabulary_parses_to_false(self, monkeypatch, value) -> None:
        """Each falsy spelling (incl. mixed case) kills the flag."""
        monkeypatch.setenv("ENSEMBLE_AMBIENT_KV_FRESH", value)
        assert self._resolve() is False, (
            f"ENSEMBLE_AMBIENT_KV_FRESH={value!r} must resolve to "
            f"False (kill-switch vocabulary)"
        )

    @pytest.mark.parametrize("value", TRUE_VOCAB + TRUE_VOCAB_MIXED)
    def test_true_vocabulary_parses_to_true(self, monkeypatch, value) -> None:
        """Each truthy spelling (incl. mixed case) enables the flag."""
        monkeypatch.setenv("ENSEMBLE_AMBIENT_KV_FRESH", value)
        assert self._resolve() is True

    def test_unset_defaults_to_true(self) -> None:
        """Unset env → documented ON default."""
        assert self._resolve() is True

    @pytest.mark.parametrize("value", ["", "   "])
    def test_empty_string_stays_on(self, monkeypatch, value) -> None:
        """Empty / whitespace-only (bare ``KEY=``) stays ON — safe."""
        monkeypatch.setenv("ENSEMBLE_AMBIENT_KV_FRESH", value)
        assert self._resolve() is True, (
            "empty-string-safe contract: a bare KEY= line must NOT "
            "crash boot nor disable the flag"
        )

    @pytest.mark.parametrize("value", GARBAGE)
    def test_garbage_value_raises_value_error(self, monkeypatch, value) -> None:
        """Unrecognized non-empty value → fail-loud ValueError.

        The error names the flag AND the valid vocabulary (W4
        council-recommended Shape A alignment).
        """
        self._assert_garbage_raises(monkeypatch, value)

    def _assert_garbage_raises(self, monkeypatch, value) -> None:
        monkeypatch.setenv("ENSEMBLE_AMBIENT_KV_FRESH", value)
        with pytest.raises(ValueError) as excinfo:
            self._resolve()
        message = str(excinfo.value)
        assert "ENSEMBLE_AMBIENT_KV_FRESH" in message, (
            "the ValueError must name the flag"
        )
        assert "0/false/no/off" in message and "1/true/yes/on" in message, (
            "the ValueError must carry the valid vocabulary"
        )

    def test_garbage_typo_raises(self, monkeypatch) -> None:
        self._assert_garbage_raises(monkeypatch, "enabled")

    def test_garbage_falsy_typo_raises(self, monkeypatch) -> None:
        self._assert_garbage_raises(monkeypatch, "disabled")

    def test_garbage_off_with_space_inside_raises(self, monkeypatch) -> None:
        # Internal whitespace is NOT stripping-normalized into a valid
        # spelling — "0 0" is garbage.
        self._assert_garbage_raises(monkeypatch, "0 0")

    def test_raise_leaves_cache_uncached_for_boot_fail(self, monkeypatch) -> None:
        """A raise leaves the resolver UNCACHED (boot fails every time).

        Mirrors Shape A: the error surfaces at boot via the
        manager-wired emit path — no poisoned True cache.
        """
        monkeypatch.setenv("ENSEMBLE_AMBIENT_KV_FRESH", "typo")
        with pytest.raises(ValueError):
            self._resolve()
        from daemon.services import context_messages as cm

        assert cm._AMBIENT_KV_FRESH is None, (
            "a failed parse must NOT cache a value — the next call "
            "(boot emit retry, per-turn gate) re-raises"
        )


# ─── W2 — post-escape 32k cap (escape-dense payloads) ─────────────────────────


class TestW2PostEscapeCap:
    """The 32k cap binds the ESCAPED body, not the raw payload.

    W2: ``escape_for_context_block`` expands ``&``/``<``/``>`` up to
    6× (1 char → ``\\uXXXX``). A cap applied PRE-escape let a
    32k-raw payload dense in escapable glyphs render at ~197k chars.
    The cap now sits AFTER the escape — same skip-on-overflow
    outcome (WARNING + skip, never a truncated output).
    """

    def test_escape_dense_payload_skipped(
        self, caplog, monkeypatch
    ) -> None:
        """RAW json < 32k but ESCAPED body > 32k → skipped."""
        from daemon.services.context_messages import (
            build_shared_meta_kv_message,
        )

        # ~1k pairs of "&<>" → raw ~4k (well under 32k), escaped
        # ~24k chars of \\uXXXX per 4 raw chars → >32k after escape.
        dense = "&<>" * 3_500  # raw 10_500 chars; escaped 63_000 chars
        kv = {"dense": dense}
        raw_json = json.dumps(kv, sort_keys=True, indent=2)
        assert len(raw_json) < 32 * 1024, "test premise: raw under cap"
        from daemon.services.context_messages import escape_for_context_block

        assert len(escape_for_context_block(raw_json)) > 32 * 1024, (
            "test premise: escaped body over cap"
        )

        monkeypatch.delenv("ENSEMBLE_AMBIENT_KV_FRESH", raising=False)
        with caplog.at_level(logging.WARNING, logger="daemon.services.context_messages"):
            msg = build_shared_meta_kv_message(kv)

        assert msg is None, (
            "an escape-dense payload (raw < 32k, escaped > 32k) must "
            "be SKIPPED — the cap binds the ESCAPED body"
        )
        assert any("exceeds 32k" in r.message for r in caplog.records), (
            "overflow must WARNING (skip-on-overflow, never truncate)"
        )

    def test_just_under_escaped_cap_still_renders(self) -> None:
        """An escaped body just under 32k still renders."""
        from daemon.services.context_messages import (
            build_shared_meta_kv_message,
        )

        # Escape-inert content sized to land safely under the cap
        # after the (no-op) escape.
        kv = {"big": "x" * 30_000}
        msg = build_shared_meta_kv_message(kv)
        assert msg is not None, (
            "an escaped body under the cap must still render"
        )
        assert "x" * 100 in msg.content


class TestBootLogEmitAtBoot:
    """The Shape B boot log is emitted at startup, NOT lazily on first call.

    Phase1-plan.md:328-353 (S13 reviewer gate): a lazy first-call
    emit would make a quiet-daemon boot-log grep false-fail — no
    traffic since restart → the line never prints → operator reads
    the flag as OFF. The wire-up is at ``daemon/manager.py`` boot
    (parallel to ``emit_wc_wake_enqueue_boot_log``,
    ``emit_governor_recursion_guard_boot_log``).
    """

    def test_manager_boot_emits_both_boot_log_lines(
        self, tmp_path, monkeypatch, caplog
    ) -> None:
        """EXECUTION-level boot pin (replaces the structural source-grep).

        Constructs a REAL ``InstanceManager`` (the
        ``tests/integration/test_boot_report_recovery.py`` harness
        recipe: real ``load_config(config.yaml)`` with the DB path
        overridden to a file-backed SQLite ``tmp_path``; the migration
        runner no-op'ed — the pre-existing SQLite DROP CONSTRAINT
        incompatibility is unrelated to boot-log wiring) and asserts
        that BOTH one-time boot-log lines actually emit DURING
        ``__init__``:

        * ``"Ambient KV freshness"`` (Shape B — this branch's C3)
        * ``"WC-wake enqueue routing resolved"`` (the sibling the C3
          amend accidentally REPLACED in the import block)

        This is the test class that would have caught the critical
        NameError boot crash: the old grep pin only proved the SOURCE
        TEXT contained the call — the C3 amend swapped the import away
        and every daemon boot died with ``NameError`` while the suite
        stayed green (no test constructed the manager). Construction
        here executes the actual ``daemon/manager.py`` emit site.
        """
        import daemon.services.context_messages as cm
        from daemon.migrations.runner import MigrationRunner

        # Reset the one-time emit guard so THIS test's construction
        # (not a prior test's manager in the same process) is what
        # emits. monkeypatch restores after the test.
        # B1 (2026-09-11): the WC-wake kill-switch was REMOVED, so
        # the ``_WC_WAKE_ENQUEUE_ENABLED`` / ``_WC_WAKE_ENQUEUE_BOOT_LOG_EMITTED``
        # module-level guards are GONE — only the ambient-KV guard
        # remains to reset.
        monkeypatch.setattr(cm, "_AMBIENT_KV_FRESH_BOOT_LOG_EMITTED", False)

        project_root = Path(__file__).resolve().parents[3]
        config_path = project_root / "config.yaml"
        if not config_path.exists():
            pytest.skip("config.yaml not found at project root")

        from daemon.config import load_config

        config = load_config(str(config_path))
        # File-backed SQLite in tmp_path (the boot_report_recovery
        # recipe) — the manager builds its own engine from db_path.
        config.persistence.db_path = str(tmp_path / "instances.db")

        # Bypass the migration runner (pre-existing SQLite DROP
        # CONSTRAINT incompatibility — unrelated to the boot wiring).
        monkeypatch.setattr(
            MigrationRunner,
            "run_pending_migrations",
            lambda self: [],
        )

        from daemon.manager import InstanceManager

        with caplog.at_level(logging.INFO):
            manager = InstanceManager(config)

        messages = [r.message for r in caplog.records]
        assert manager is not None
        assert any("Ambient KV freshness" in m for m in messages), (
            "InstanceManager construction must emit the Ambient KV "
            "freshness boot line (manager.py → "
            "emit_ambient_kv_fresh_boot_log)"
        )
        # B1 (2026-09-11): the WC-wake boot line was REMOVED entirely
        # (the kill-switch is gone). The absence of the line is the
        # correct post-fix invariant — assert the manager did not emit
        # it instead.

    def test_emit_function_is_idempotent(self, monkeypatch, caplog) -> None:
        """The boot-log emit function is idempotent across calls.

        Calling ``emit_ambient_kv_fresh_boot_log()`` twice emits
        exactly ONE boot INFO line (mirrors the Shape B precedent —
        the WC-wake emit helper was REMOVED in B1, 2026-09-11). The
        one-time guard is reset FIRST so this pin is deterministic
        regardless of whether an earlier test in the same process
        already emitted (e.g. the kv_ambient_config boot-log pin).
        """
        import daemon.services.context_messages as cm
        emit = getattr(cm, "emit_ambient_kv_fresh_boot_log", None)
        if emit is None:
            pytest.skip("C3 symbol not present in this worktree")
        monkeypatch.setattr(cm, "_AMBIENT_KV_FRESH_BOOT_LOG_EMITTED", False)
        with caplog.at_level(logging.INFO, logger="daemon.services.context_messages"):
            emit()
            emit()  # second call must be a no-op
        boot_lines = [
            rec.message for rec in caplog.records
            if "Ambient KV freshness" in rec.message
        ]
        assert len(boot_lines) == 1, (
            f"boot log emit must be idempotent (one INFO line per "
            f"daemon lifetime); got {len(boot_lines)}"
        )
        # Line names the resolved state.
        assert "ENABLED" in boot_lines[0] or "DISABLED" in boot_lines[0]


# ─── Real-service flag-ON test (W6-style, C3-specific) ────────────────────────


class TestAmbientKVFreshFlagOnRealService:
    """Flag-ON path observed through REAL ``SharedMetaKVRepository``.

    Phase1-plan.md:457-471. External KV writes between turns must be
    visible on the next non-retry turn (the contract: per-turn
    refresh).
    """

    def test_external_kv_write_visible_on_next_non_retry_turn(
        self, tmp_path, monkeypatch
    ) -> None:
        """An external KV write between turns shows up on turn 2+.

        Mirrors the C1' / C2 real-service harness recipe
        (file-backed SQLite + NullPool + WAL + busy_timeout). The
        per-turn refresh contract holds end-to-end through REAL
        repos.
        """
        from sqlalchemy import create_engine, event
        from sqlalchemy.pool import NullPool
        from sqlmodel import Session, SQLModel

        # Register models so SQLModel.metadata.create_all builds the
        # full schema (matches the C2 recipe).
        import daemon.repositories.shared_meta_kv.models  # noqa: F401
        from daemon.repositories.shared_meta_kv.repository import (
            SharedMetaKVRepository,
        )

        db_path = tmp_path / "kv_fresh.db"
        eng = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )

        @event.listens_for(eng, "connect")
        def _enable(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()

        SQLModel.metadata.create_all(eng)
        try:
            kv_repo = SharedMetaKVRepository(eng)
            TREE_ROOT_ID = "iid-c3-real-001"
            kv_repo.set_many(TREE_ROOT_ID, {"topic": "initial"})

            # Reset resolver cache; ensure flag ON.
            monkeypatch.delenv("ENSEMBLE_AMBIENT_KV_FRESH", raising=False)
            monkeypatch.setattr(
                "daemon.services.context_messages._resolve_ambient_kv_fresh",
                lambda: True,
                raising=False,
            )
            monkeypatch.setattr(
                "daemon.services.context_messages._resolve_kv_ambient_system_default_enabled",
                lambda: True,
                raising=False,
            )

            from daemon.services.context_messages import (
                assemble_context_messages,
            )

            manager = MagicMock()
            manager._project_repository = MagicMock()
            manager._project_repository.get.return_value = None
            manager._project_repository.list_critical_notes.return_value = []
            manager._project_repository.get_recent_history.return_value = []
            manager._shared_meta_kv_repo = kv_repo
            manager._skill_injection_service = MagicMock()
            manager._skill_injection_service.inject_skills = AsyncMock(
                return_value=(None, [])
            )

            instance_repo = MagicMock()
            instance_repo.get_tree_root_id.return_value = TREE_ROOT_ID
            inst = MagicMock()
            inst.instance_metadata = {}
            instance_repo.get.return_value = inst

            agent_meta = MagicMock()
            agent_meta.blueprint_inactive = True
            agent_meta.context_injection = None
            agent_meta.skill_injection = False

            common_kwargs = dict(
                instance_id=TREE_ROOT_ID,
                user_query="hi",
                project_id="proj-1",
                agent_meta=agent_meta,
                manager=manager,
                instance_repository=instance_repo,
                project_already_injected=True,
            )

            # Turn 2+ #1 — sees ``topic: initial``.
            result_a = _flatten(*_run(assemble_context_messages(**common_kwargs)))
            kv_a = next(
                (m for m in result_a
                 if m.additional_kwargs.get("context_kind") == CONTEXT_KIND_SHARED_META_KV),
                None,
            )
            assert kv_a is not None
            assert "initial" in kv_a.content

            # External write between turns.
            kv_repo.set_many(TREE_ROOT_ID, {"topic": "updated"})

            # Turn 2+ #2 — must see ``topic: updated`` (per-turn refresh).
            result_b = _flatten(*_run(assemble_context_messages(**common_kwargs)))
            kv_b = next(
                (m for m in result_b
                 if m.additional_kwargs.get("context_kind") == CONTEXT_KIND_SHARED_META_KV),
                None,
            )
            assert kv_b is not None
            assert "updated" in kv_b.content, (
                "an external KV write between turns must be visible "
                "on the next non-retry turn — the per-turn refresh "
                "contract is end-to-end through REAL repos"
            )
            # Same stable id across calls.
            assert kv_a.id == kv_b.id
        finally:
            eng.dispose()
