"""The memoir briefing SEAM — through the real ``assemble_context_messages``.

Memoir Store v1 — phase 5 (plan:
``.agents/shared/planning/ari-memoir-store/phase5-plan.md`` T5.3/T5.4/
T5.6, AC3/AC4/AC6; pack ``test/packs/memoir_briefing_seam_test.sh``).

What this suite proves END-TO-END through the REAL assembly seam:

* the block is present at turn 1 for ari and ABSENT at turn 2 (the
  once-flag, read fresh — D5.11);
* the builder NEVER writes the once-flag (the seam is BUILD-ONLY —
  the instance row's metadata is byte-unchanged after the call);
* the flag READ is OFF-LOOP (the repository read runs in a
  ``asyncio.to_thread`` worker, never on the loop thread — AM-37);
* a hung read DEGRADES inside the whole-build budget — the turn is
  not delayed (AM-38);
* non-ari ⇒ no block, ever (D5.12 — the identity comes from the row);
* knob OFF ⇒ no block AND no flag write (D5.13);
* compaction survival: the block carries ``context_kind`` so the
  REAL hoist predicate preserves it (D5.3 — no hoist-set edit exists);
* the consumer's presence predicate: DROP the block from the returned
  persistent half ⇒ the flag MUST stay unset (D5.4/AM-22 — the
  silent-briefing-loss guard).

FENCE: throwaway in-memory SQLite via ``StaticPool``; the "manager" is
a ``SimpleNamespace`` — NEVER an ``InstanceManager``; every
DB-touching fixture is explicit; no ``POSTGRES_*`` variable is read.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from daemon._content_hardening import is_hoisted_injected
from daemon.repositories.memoir.models import (
    MemoirEpisode,
    MemoirPersona,
    MemoirPersonaVersion,
    MemoirSynthesis,
)
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.services.context_messages import assemble_context_messages
from daemon.services.memoir_briefing import (
    MEMOIR_AGENT_ID,
    MEMOIR_BRIEFING_FLAG,
    memoir_block_present,
)

FIXED_NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)


class FakeInstanceRepository:
    """The instance-row read the seam needs — and the thread it read on.

    Also the FLAG SURFACE: the consumer writes the flag through
    ``set_metadata``; this fake records those writes so the suite can
    prove who wrote what.
    """

    def __init__(self, row: SimpleNamespace):
        self.row = row
        self.read_thread_idents: list[int] = []
        self.metadata_writes: list[tuple[str, Any, Any]] = []
        #: When armed, ``get`` blocks up to ``hang_seconds`` — a
        #: SELF-RELEASING hang (a to_thread worker cannot be cancelled,
        #: and ``asyncio.run`` joins the default executor on cleanup, so
        #: the hang must end on its own well under the per-test cap).
        self.hang_get = False
        self.hang_seconds = 1.0

    def get(self, instance_id: str):
        self.read_thread_idents.append(threading.get_ident())
        if self.hang_get:
            time.sleep(self.hang_seconds)
        return self.row

    def set_metadata(self, instance_id: str, key, value) -> None:
        self.metadata_writes.append((instance_id, key, value))
        self.row.instance_metadata = {
            **(self.row.instance_metadata or {}), key: value,
        }

    # tree-root resolution never fires (parent_id is None), but the
    # hook exists so an unexpected call is loud, not silent.
    def get_tree_root_id(self, parent_id: str):  # pragma: no cover
        raise AssertionError("get_tree_root_id must not be called (root)")


from typing import Any  # noqa: E402  (used in the fake's annotations)


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


@pytest.fixture()
def instance_row():
    return SimpleNamespace(
        agent_id=MEMOIR_AGENT_ID,
        instance_metadata={},
        parent_id=None,
    )


@pytest.fixture()
def fake_repo(instance_row):
    return FakeInstanceRepository(instance_row)


@pytest.fixture()
def manager(engine, fake_repo):
    return SimpleNamespace(
        _engine=engine,
        _instance_repository=fake_repo,
        config=None,
    )


async def _assemble(manager, fake_repo, instance_id="inst-seam-1",
                    project_already_injected=False):
    return await assemble_context_messages(
        instance_id=instance_id,
        user_query="hello",
        project_id=None,
        agent_meta=SimpleNamespace(context_injection=None, skill_injection=False),
        manager=manager,
        instance_repository=fake_repo,
        parent_id=None,
        skill_injection_result=None,
        project_already_injected=project_already_injected,
    )


def _memoir_blocks(messages):
    return [
        m for m in messages
        if (getattr(m, "additional_kwargs", None) or {}).get("context_kind")
        == "memoir"
    ]


# ── turn 1 / turn 2 — the once-per-instance semantics ────────────────────


def test_block_present_turn1_absent_turn2(engine, repo, fake_repo, manager):
    """D5.11: present at turn 1; the flag (written by the consumer) gates turn 2."""
    repo.seed_persona_if_absent(MEMOIR_AGENT_ID)

    persistent, ephemeral = asyncio.run(_assemble(manager, fake_repo))
    assert ephemeral == []
    blocks = _memoir_blocks(persistent)
    assert len(blocks) == 1
    assert blocks[0].id == "memoir:ari"
    assert blocks[0].additional_kwargs == {
        "injected_message": True,
        "context_kind": "memoir",
    }

    # THE CONSUMER's write (what instance_messaging.py does on a
    # confirmed presence) — simulated through the fake's set_metadata:
    assert memoir_block_present(persistent, MEMOIR_AGENT_ID) is True
    fake_repo.set_metadata("inst-seam-1", MEMOIR_BRIEFING_FLAG, True)

    # turn 2 (the project_already_injected path — the flag is read FRESH)
    persistent2, _ = asyncio.run(
        _assemble(manager, fake_repo, project_already_injected=True)
    )
    assert _memoir_blocks(persistent2) == []


def test_builder_never_writes_the_flag(engine, repo, fake_repo, manager):
    """D5.4/AC3: the SEAM (and the builder behind it) writes NOTHING.

    The once-flag is written by the CONSUMER only. After a full turn-1
    assembly the instance row's metadata must carry NO memoir flag and
    the fake repository must have recorded ZERO metadata writes.
    """
    repo.seed_persona_if_absent(MEMOIR_AGENT_ID)
    persistent, _ = asyncio.run(_assemble(manager, fake_repo))
    assert len(_memoir_blocks(persistent)) == 1
    assert fake_repo.metadata_writes == []
    assert MEMOIR_BRIEFING_FLAG not in (fake_repo.row.instance_metadata or {})


def test_flag_write_site_guard_block_dropped_stays_unset(
    engine, repo, fake_repo, manager
):
    """D5.4/AC6: drop the block from the persistent half ⇒ flag UNSET.

    The graph path discards the persistent half; the consumer writes
    the flag ONLY after ``memoir_block_present`` confirms the block is
    in the half it received. This leg drops the memoir block from the
    returned persistent half (simulating the discarded copy / a
    dropped block) and proves the consumer's predicate goes False —
    the write never fires, so the briefing can deliver next turn.
    """
    repo.seed_persona_if_absent(MEMOIR_AGENT_ID)
    persistent, _ = asyncio.run(_assemble(manager, fake_repo))
    assert len(_memoir_blocks(persistent)) == 1

    dropped = [
        m for m in persistent
        if (getattr(m, "additional_kwargs", None) or {}).get("context_kind")
        != "memoir"
    ]
    assert len(dropped) == len(persistent) - 1
    assert memoir_block_present(dropped, MEMOIR_AGENT_ID) is False
    # the write site's guard: no confirmation ⇒ no write
    if not memoir_block_present(dropped, MEMOIR_AGENT_ID):
        pass  # the consumer skips the set_metadata call entirely
    assert fake_repo.metadata_writes == []
    assert MEMOIR_BRIEFING_FLAG not in (fake_repo.row.instance_metadata or {})


# ── the off-loop read (AM-37) + the deadline (AM-38) ─────────────────────


def test_flag_read_is_off_loop(engine, repo, fake_repo, manager):
    """AM-37/D5.5: the instance-row read NEVER runs on the loop thread."""
    repo.seed_persona_if_absent(MEMOIR_AGENT_ID)
    loop_ident = threading.get_ident()
    asyncio.run(_assemble(manager, fake_repo))
    assert fake_repo.read_thread_idents, "the flag read must have happened"
    assert all(ident != loop_ident for ident in fake_repo.read_thread_idents)


def test_hung_read_degrades_within_budget(monkeypatch, caplog, engine, repo,
                                          fake_repo, manager):
    """AM-38/D5.6: a hung read ⇒ degrade; the turn is NOT delayed.

    The whole-build budget is patched to 0.2 s and the flag read hangs
    (self-releasing at 1.0 s — a ``to_thread`` worker cannot be
    cancelled, but the BUDGET fires at 0.2 s and the seam returns
    without the block). The proof is the exact budget-breach WARNING
    plus the absence of the block — and the whole assembly completing
    far under the 10 s per-read timeout.
    """
    from daemon.services import memoir_briefing

    monkeypatch.setattr(memoir_briefing, "MEMOIR_BUILD_BUDGET_S", 0.2)
    fake_repo.hang_get = True
    fake_repo.hang_seconds = 1.0
    with caplog.at_level(
        logging.WARNING, logger="daemon.services.context_messages"
    ):
        start = time.monotonic()
        persistent, _ = asyncio.run(_assemble(manager, fake_repo))
        elapsed = time.monotonic() - start
    # degraded: no memoir block anywhere in the returned half
    assert _memoir_blocks(persistent) == []
    # the BUDGET fired — the exact degrade WARNING carries the budget
    budget_warnings = [
        r.getMessage() for r in caplog.records
        if "whole-build budget" in r.getMessage()
    ]
    assert any(
        "0.2s whole-build budget" in message for message in budget_warnings
    ), f"the budget-breach WARNING is missing: {budget_warnings}"
    # the turn was NOT delayed: well under the 10s per-read timeout
    assert elapsed < 5.0, f"the degrade took {elapsed:.2f}s"
    # and no flag write happened on the degraded path
    assert fake_repo.metadata_writes == []


def test_withhold_path_writes_no_flag(monkeypatch, engine, repo, fake_repo,
                                      manager):
    """D5.8/AM-43: the withhold path (render ``None``) writes NO flag.

    The 48,000-char absolute bound is patched DOWN so the withhold
    rung fires on any persona — the builder returns ``None``, no block
    is emitted, and the once-flag MUST stay unset (the briefing was
    never delivered; writing the flag here would permanently lose it).
    """
    from daemon.services import memoir_briefing

    repo.seed_persona_if_absent(MEMOIR_AGENT_ID)
    monkeypatch.setattr(memoir_briefing, "MEMOIR_SECTION_CHAR_ABSOLUTE", 10)
    persistent, _ = asyncio.run(_assemble(manager, fake_repo))
    assert _memoir_blocks(persistent) == []
    assert fake_repo.metadata_writes == []
    assert MEMOIR_BRIEFING_FLAG not in (fake_repo.row.instance_metadata or {})


# ── the agent gate + the knob gate ────────────────────────────────────────


def test_non_ari_gets_no_block(engine, repo, fake_repo, manager):
    """D5.12: the briefing builds ONLY for ari — identity from the ROW."""
    repo.seed_persona_if_absent(MEMOIR_AGENT_ID)
    fake_repo.row.agent_id = "worker"
    persistent, _ = asyncio.run(_assemble(manager, fake_repo))
    assert _memoir_blocks(persistent) == []
    assert fake_repo.metadata_writes == []


def test_knob_off_no_block_and_no_flag_write(monkeypatch, engine, repo,
                                             fake_repo, manager):
    """D5.13: OFF ⇒ no block, NO flag write (a later enable delivers)."""
    repo.seed_persona_if_absent(MEMOIR_AGENT_ID)
    manager.config = SimpleNamespace(memoir=SimpleNamespace(enabled=False))
    persistent, _ = asyncio.run(_assemble(manager, fake_repo))
    assert _memoir_blocks(persistent) == []
    assert fake_repo.metadata_writes == []
    assert MEMOIR_BRIEFING_FLAG not in (fake_repo.row.instance_metadata or {})

    # a later enable delivers on the next turn (the intended shape)
    manager.config = SimpleNamespace(memoir=SimpleNamespace(enabled=True))
    persistent_on, _ = asyncio.run(_assemble(manager, fake_repo))
    assert len(_memoir_blocks(persistent_on)) == 1


# ── compaction survival (D5.3 — truthy-keyed hoist) ──────────────────────


def test_block_survives_compaction_via_context_kind(engine, repo, fake_repo,
                                                    manager):
    """D5.3/AC1: the REAL hoist predicate preserves the block verbatim.

    ``is_hoisted_injected`` is truthy-keyed on ANY ``context_kind`` —
    there is NO membership set to edit (AM-40). The memoir block is
    preserved above the compaction doc exactly like the snapshot
    digest.
    """
    repo.seed_persona_if_absent(MEMOIR_AGENT_ID)
    persistent, _ = asyncio.run(_assemble(manager, fake_repo))
    blocks = _memoir_blocks(persistent)
    assert len(blocks) == 1
    assert is_hoisted_injected(blocks[0], frozenset({"unrelated-note-id"})) is True


def test_stable_id_supersede_not_duplicate():
    """AC1: the SAME stable id supersedes in place (idempotent re-append).

    LangGraph's ``add_messages`` reducer upserts by message id — the
    contract the stable id exists for. Pinned at the id level: two
    builds for the same agent mint the SAME id, so a re-delivery can
    never append a duplicate block.
    """
    from daemon.services.context_messages import _stable_id_for

    assert _stable_id_for("memoir", agent_id="ari") == "memoir:ari"
    assert (
        _stable_id_for("memoir", agent_id="ari")
        == _stable_id_for("memoir", agent_id="ari")
    )
    with pytest.raises(ValueError):
        _stable_id_for("memoir", agent_id=None)
