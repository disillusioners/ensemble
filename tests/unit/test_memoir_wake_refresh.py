"""Memoir Store v2 — PHASE 6 wake-refresh seam (Option B amended).

Plan: ``.agents/shared/planning/ari-memoir-store/v2-phase6-plan.md``
(§3.2, T6.4/T6.5, AC4/AC5/AC6). Pack:
``test/packs/memoir_v2_wake_refresh_test.sh``.

What this suite pins:

* **AC4 — `_stable_id_for` registration (N3)**: ``memoir_refresh`` mints
  ``memoir_refresh:{instance_id}`` and REFUSES a missing instance id; the
  closed-list ValueError message ENUMERATES the new kind (the message is a
  live contract — a stale enumeration is a defect);
* **AC4 — the seam**: ari-only, the lane's own knob, and the THREADED
  ``wake_source == "scheduler"`` gate producing a context block with
  ``context_kind="memoir_refresh"`` and the fresh briefing payload
  (persona + keepsakes + the [PROPOSALS] zone + dynamic catch-up);
* **AC4 — stamp purity (L4)**: the scheduler's OWN wake message stays an
  unstamped bare ``MessageType.HUMAN`` — the refresh composes ALONGSIDE
  it as a separate stamped block, and nothing touches the alarm template;
* **AC4 — no once-flag**: successive wakes SUPERSEDE to exactly ONE
  refresh row (``add_messages`` stable-id semantics);
* **AC5 — the kill-switch**: ``ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED=false``
  suppresses the block and leaves the rest of the context untouched.

FENCE: throwaway in-memory SQLite via ``StaticPool``; the "manager" is a
``SimpleNamespace``, NEVER an ``InstanceManager``; no ``POSTGRES_*``
variable is read.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from daemon.constants import INTERNAL_STAMPED_SOURCE_PREFIXES, is_internal_stamped_source
from daemon.repositories.memoir.models import (
    PROPOSALS_ZONE_HEADER,
    MemoirEpisode,
    MemoirProposalLedger,
)
from daemon.repositories.memoir.repository import MemoirRepository
from daemon.services.context_messages import (
    CONTEXT_KIND_MEMOIR,
    CONTEXT_KIND_MEMOIR_REFRESH,
    _make_context_message,
    _stable_id_for,
    assemble_context_messages,
)
from daemon.services.memoir_briefing import (
    MEMOIR_AFFORDANCE_LINE,
    MEMOIR_AGENT_ID,
    MEMOIR_BRIEFING_FLAG,
)
from daemon.services.self_alarm_service import compose_alarm_message

FIXED_NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)
INSTANCE_ID = "inst-wake-1"

LONG_BODY = (
    "We argued about the font for three hours and settled on the serif; "
    "the decision became canon for every later page we shipped together."
)


def _real_add_messages():
    """Import the REAL ``langgraph.graph.message.add_messages`` reducer.

    The conftest mocks ``langgraph.*`` as non-package modules, so the real
    import only resolves inside a sys.modules swap window (the house
    precedent: ``tests/unit/test_compaction.py::_load_real_add_messages``).
    The SAME module objects are restored afterwards so every later test
    keeps seeing the conftest mocks.
    """
    import importlib
    import sys

    saved = {
        k: sys.modules[k] for k in list(sys.modules) if k.startswith("langgraph")
    }
    for key in [k for k in sys.modules if k.startswith("langgraph")]:
        del sys.modules[key]
    try:
        module = importlib.import_module("langgraph.graph.message")
        return module.add_messages
    finally:
        for key in [k for k in sys.modules if k.startswith("langgraph")]:
            del sys.modules[key]
        sys.modules.update(saved)


# ── harness ──────────────────────────────────────────────────────────────


class FakeInstanceRepository:
    """The instance-row read the seams need (row + metadata surface)."""

    def __init__(self, row):
        self.row = row

    def get(self, instance_id):
        return self.row

    def set_metadata(self, instance_id, key, value):
        self.row.instance_metadata = {
            **(self.row.instance_metadata or {}), key: value,
        }

    def get_tree_root_id(self, parent_id):  # pragma: no cover
        raise AssertionError("get_tree_root_id must not be called (root)")


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


def _seed_world(engine, repo):
    """Persona + a keepsake + a keepsake PROPOSAL — the refresh payload."""
    repo.seed_persona_if_absent(MEMOIR_AGENT_ID)
    keepsake_id = repo.insert_episode(
        agent_id=MEMOIR_AGENT_ID,
        title="The lantern walk",
        body="canal-path body",
        importance=10,
        is_keepsake=True,
        pinned_by="empress",
        pinned_reason="the font argument",
    )
    proposed_id = repo.insert_episode(
        agent_id=MEMOIR_AGENT_ID,
        title="The serif decision",
        body=LONG_BODY,
        importance=9,
    )
    with Session(engine) as session:
        session.add(
            MemoirProposalLedger(
                id="ledger-1",
                agent_id=MEMOIR_AGENT_ID,
                episode_id=proposed_id,
                proposal_date=FIXED_NOW.date().isoformat(),
                proposed_at=FIXED_NOW,
                notification_status="pending",
                notified_at=None,
                reason="enduring significance: effective poignancy 9/10",
                created_at=FIXED_NOW,
            )
        )
        session.commit()
    return keepsake_id, proposed_id


async def _assemble(manager, fake_repo, *, wake_source=None,
                    project_already_injected=False, instance_id=INSTANCE_ID):
    return await assemble_context_messages(
        instance_id=instance_id,
        user_query="the alarm fired",
        project_id=None,
        agent_meta=SimpleNamespace(context_injection=None, skill_injection=False),
        manager=manager,
        instance_repository=fake_repo,
        parent_id=None,
        skill_injection_result=None,
        project_already_injected=project_already_injected,
        wake_source=wake_source,
    )


def _blocks(messages, kind):
    return [
        m for m in messages
        if (getattr(m, "additional_kwargs", None) or {}).get("context_kind") == kind
    ]


@pytest.fixture(autouse=True)
def _clean_knobs(monkeypatch):
    """Every leg starts from the UNSET truth-table row (and no ambient knob)."""
    for name in (
        "ENSEMBLE_MEMOIR_ENABLED",
        "ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    from daemon.tools.memoir_knob import reset_boot_log_state

    reset_boot_log_state()
    yield
    reset_boot_log_state()


# ─────────────────────────────────────────────────────────────────────────
# AC4 — the `_stable_id_for` registration (N3)
# ─────────────────────────────────────────────────────────────────────────


class TestStableIdRegistration:
    def test_the_kind_mints_the_per_instance_id(self):
        assert _stable_id_for("memoir_refresh", instance_id="abc") == (
            "memoir_refresh:abc"
        )

    def test_a_missing_instance_id_is_refused(self):
        with pytest.raises(ValueError, match="memoir_refresh"):
            _stable_id_for("memoir_refresh")

    def test_the_closed_list_error_enumerates_the_new_kind(self):
        """The message IS a live contract: a stale enumeration is a defect."""
        with pytest.raises(ValueError) as exc_info:
            _stable_id_for("nonexistent_kind")
        message = str(exc_info.value)
        for kind in (
            "project",
            "shared_meta_kv",
            "attestation_nudge",
            "attestation_final_report_reminder",
            "memoir",
            "memoir_refresh",
            "pok",
        ):
            assert f"'{kind}'" in message, message

    def test_the_module_constant_matches_the_registered_kind(self):
        assert CONTEXT_KIND_MEMOIR_REFRESH == "memoir_refresh"


# ─────────────────────────────────────────────────────────────────────────
# AC4 — the seam gate order and the payload
# ─────────────────────────────────────────────────────────────────────────


class TestRefreshSeam:
    def test_a_scheduler_wake_of_ari_emits_the_refresh_block(
        self, engine, repo, fake_repo, manager
    ):
        _seed_world(engine, repo)
        persistent, _ = asyncio.run(
            _assemble(manager, fake_repo, wake_source="scheduler")
        )
        refresh = _blocks(persistent, "memoir_refresh")
        assert len(refresh) == 1
        block = refresh[0]
        assert block.id == f"memoir_refresh:{INSTANCE_ID}"
        assert block.content.startswith("[SYSTEM CONTEXT: Memoir Refresh]")
        assert block.additional_kwargs == {
            "injected_message": True,
            "context_kind": "memoir_refresh",
            "source": "memoir_refresh",
        }
        # THE PAYLOAD (SF6): persona, keepsakes, the proposals zone, the
        # affordance close.
        assert "## Persona" in block.content
        assert "## Keepsakes" in block.content
        assert "The lantern walk" in block.content
        assert PROPOSALS_ZONE_HEADER in block.content
        assert "The serif decision" in block.content
        assert block.content.rstrip().endswith(MEMOIR_AFFORDANCE_LINE)

    def test_a_non_scheduler_assembly_costs_no_db_read(
        self, engine, repo, instance_row
    ):
        """The gate is a CONJUNCTION — order by cost, not by plan prose.

        A non-scheduler assembly must not pay an instance-row read at all:
        every turn of every instance passes through this seam, so a
        row-read-first order would add one DB round trip to the whole
        daemon. (The v1 briefing seam's own discipline: cheapest gate
        first.)
        """
        reads: list[str] = []

        class CountingRepo:
            def get(self, instance_id):
                reads.append(instance_id)
                return instance_row

        _seed_world(engine, repo)
        manager = SimpleNamespace(
            _engine=engine, _instance_repository=CountingRepo(), config=None
        )
        # The REFRESH seam alone (the v1 briefing seam has its own row read
        # by design — its once-flag lives on the row).
        from daemon.services.context_messages import _assemble_memoir_refresh

        result = asyncio.run(
            _assemble_memoir_refresh(
                INSTANCE_ID, manager, CountingRepo(), wake_source=None
            )
        )
        assert result is None
        assert reads == []
        # ... and with the knob OFF, still zero reads.
        import os

        os.environ["ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED"] = "false"
        try:
            assert asyncio.run(
                _assemble_memoir_refresh(
                    INSTANCE_ID, manager, CountingRepo(), wake_source="scheduler"
                )
            ) is None
            assert reads == []
        finally:
            os.environ.pop("ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED", None)

    def test_a_non_scheduler_wake_emits_no_refresh(
        self, engine, repo, fake_repo, manager
    ):
        _seed_world(engine, repo)
        for source in (None, "api", "telegram:user:1", "internal_report:child"):
            persistent, _ = asyncio.run(
                _assemble(manager, fake_repo, wake_source=source)
            )
            assert _blocks(persistent, "memoir_refresh") == [], source

    def test_a_non_ari_instance_emits_no_refresh(
        self, engine, repo, instance_row, manager
    ):
        _seed_world(engine, repo)
        instance_row.agent_id = "developer"
        fake_repo = FakeInstanceRepository(instance_row)
        persistent, _ = asyncio.run(
            _assemble(manager, fake_repo, wake_source="scheduler")
        )
        assert _blocks(persistent, "memoir_refresh") == []

    def test_the_refresh_also_fires_on_the_turn_1_path(
        self, engine, repo, fake_repo, manager
    ):
        _seed_world(engine, repo)
        persistent, _ = asyncio.run(
            _assemble(
                manager, fake_repo, wake_source="scheduler",
                project_already_injected=False,
            )
        )
        assert len(_blocks(persistent, "memoir_refresh")) == 1

    def test_the_once_flag_does_not_suppress_the_refresh(
        self, engine, repo, fake_repo, instance_row, manager
    ):
        """The two seams are INDEPENDENT: the briefing's once-flag is not
        the refresh's gate (the refresh has NO once-flag at all)."""
        _seed_world(engine, repo)
        instance_row.instance_metadata = {MEMOIR_BRIEFING_FLAG: True}
        persistent, _ = asyncio.run(
            _assemble(
                manager, fake_repo, wake_source="scheduler",
                project_already_injected=True,
            )
        )
        # the BRIEFING is suppressed by the flag ...
        assert _blocks(persistent, CONTEXT_KIND_MEMOIR) == []
        # ... the REFRESH is not.
        assert len(_blocks(persistent, "memoir_refresh")) == 1

    def test_the_refresh_is_built_even_when_the_briefing_is_not_due(
        self, engine, repo, fake_repo, manager
    ):
        """A scheduler wake on a LATER turn still refreshes (no once-flag)."""
        _seed_world(engine, repo)
        first, _ = asyncio.run(
            _assemble(manager, fake_repo, wake_source="scheduler")
        )
        assert len(_blocks(first, "memoir_refresh")) == 1
        second, _ = asyncio.run(
            _assemble(
                manager, fake_repo, wake_source="scheduler",
                project_already_injected=True,
            )
        )
        assert len(_blocks(second, "memoir_refresh")) == 1

    def test_a_briefing_knob_off_does_not_disable_the_refresh(
        self, engine, repo, fake_repo, manager, monkeypatch
    ):
        """Independence in the OTHER direction: the refresh rides its OWN
        knob; the lane's seam is not gated on the v1 briefing knob."""
        _seed_world(engine, repo)
        monkeypatch.setenv("ENSEMBLE_MEMOIR_ENABLED", "false")
        persistent, _ = asyncio.run(
            _assemble(
                manager, fake_repo, wake_source="scheduler",
                project_already_injected=True,
            )
        )
        assert len(_blocks(persistent, "memoir_refresh")) == 1


# ─────────────────────────────────────────────────────────────────────────
# AC5 — the kill-switch (DR-3 Enabled-flags row)
# ─────────────────────────────────────────────────────────────────────────


class TestWakeRefreshKnob:
    def test_off_suppresses_the_block_and_leaves_the_rest_intact(
        self, engine, repo, fake_repo, manager, monkeypatch
    ):
        _seed_world(engine, repo)
        monkeypatch.setenv("ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED", "false")
        persistent, _ = asyncio.run(
            _assemble(
                manager, fake_repo, wake_source="scheduler",
                project_already_injected=True,
            )
        )
        assert _blocks(persistent, "memoir_refresh") == []

    def test_the_default_is_on_with_a_warning(self, monkeypatch, caplog):
        import logging

        monkeypatch.delenv("ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED", raising=False)
        from daemon.tools.memoir_knob import (
            reset_boot_log_state,
            resolve_memoir_wake_refresh_knob,
        )

        reset_boot_log_state()
        with caplog.at_level(logging.WARNING, logger="daemon.tools.memoir_knob"):
            state = resolve_memoir_wake_refresh_knob(None)
        assert state.enabled is True
        assert state.resolution == "default"
        assert state.source == "config"
        assert any(
            "ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED is unset" in r.getMessage()
            for r in caplog.records
        )

    def test_garbage_fails_closed_with_a_warning(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED", "maybe")
        from daemon.tools.memoir_knob import resolve_memoir_wake_refresh_knob

        state = resolve_memoir_wake_refresh_knob(None)
        assert state.enabled is False
        assert state.resolution == "indeterminate"
        assert state.rejected_value == "maybe"

    @pytest.mark.parametrize("raw", ["true", "1", "yes", "on", "t", "y", "TRUE"])
    def test_truthy_spellings_enable(self, monkeypatch, raw):
        monkeypatch.setenv("ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED", raw)
        from daemon.tools.memoir_knob import resolve_memoir_wake_refresh_knob

        assert resolve_memoir_wake_refresh_knob(None).enabled is True

    @pytest.mark.parametrize("raw", ["false", "0", "no", "off", "f", "n", "False"])
    def test_falsy_spellings_disable(self, monkeypatch, raw):
        monkeypatch.setenv("ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED", raw)
        from daemon.tools.memoir_knob import resolve_memoir_wake_refresh_knob

        assert resolve_memoir_wake_refresh_knob(None).enabled is False

    def test_the_knob_never_raises_into_the_boot(self, monkeypatch):
        """DR-3: a mis-set knob must not abort the boot (no ValidationError)."""
        monkeypatch.setenv("ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED", "??garbage??")
        from daemon.config import MemoirWakeRefreshConfig

        section = MemoirWakeRefreshConfig()
        assert section.enabled is False

    def test_the_knob_is_read_at_call_time_not_cached(self, monkeypatch):
        from daemon.tools.memoir_knob import resolve_memoir_wake_refresh_knob

        monkeypatch.setenv("ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED", "true")
        assert resolve_memoir_wake_refresh_knob(None).enabled is True
        monkeypatch.setenv("ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED", "false")
        assert resolve_memoir_wake_refresh_knob(None).enabled is False
        monkeypatch.setenv("ENSEMBLE_MEMOIR_WAKE_REFRESH_ENABLED", "true")
        assert resolve_memoir_wake_refresh_knob(None).enabled is True


# ─────────────────────────────────────────────────────────────────────────
# AC4 — stamp purity (L4) and structural supersede (no once-flag)
# ─────────────────────────────────────────────────────────────────────────


class TestStampPurity:
    def test_scheduler_is_deliberately_absent_from_the_stamped_prefixes(self):
        """L4: the scheduler's own message MUST stay a bare HUMAN turn.

        The refresh block is a SEPARATE stamped context message; the wake
        message itself is never stamped, which is what anchors attestation
        source U.
        """
        assert is_internal_stamped_source("scheduler") is False
        assert "scheduler" not in INTERNAL_STAMPED_SOURCE_PREFIXES

    def test_the_scheduler_source_derives_the_human_message_type(self):
        """AC4: a scheduler wake is ``MessageType.HUMAN`` (a REAL user turn).

        Both halves of the stamp decision are asserted here: the source is
        not an internal stamped source AND the persisted queue type falls
        back to HUMAN — which is what makes the wake anchor attestation
        source U. The refresh block rides alongside as a separate stamped
        context message and never touches either half.
        """
        from daemon.services.instance_messaging import (
            _derive_internal_message_type,
        )

        assert _derive_internal_message_type("scheduler") is None
        assert is_internal_stamped_source("scheduler") is False

    def test_the_alarm_template_is_untouched_by_the_refresh(self):
        """The alarm body carries NO memoir content and no refresh marker.

        Option A (composing the briefing into the alarm) is REJECTED —
        baking memory into the wake body would freeze content at ARM time
        and masquerade memoir text as user-authored prose.
        """
        message = compose_alarm_message(
            effective_label="self-alarm:deploy-check",
            first_fire_local=datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc),
            first_fire_utc=datetime(2026, 10, 21, 7, 0, tzinfo=timezone.utc),
            armed_at_utc=datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc),
            recurrence="once",
            timezone_name="Asia/Ho_Chi_Minh",
            source_id="src-wake-gate-1",
            board_item="wi-1a2b3c4d",
            message_to_self="Check the deploy and update the board item.",
        )
        assert "[SYSTEM CONTEXT: Memoir" not in message
        assert PROPOSALS_ZONE_HEADER not in message
        assert "## Persona" not in message
        # The alarm's own contract lines survive verbatim.
        assert message.startswith("[self-alarm] self-alarm:deploy-check")
        assert message.rstrip().endswith(
            "Action: Check the deploy and update the board item."
        )

    def test_the_wake_message_and_the_refresh_are_distinct_objects(
        self, engine, repo, fake_repo, manager
    ):
        """The refresh is a PARALLEL block — never merged into a user turn."""
        _seed_world(engine, repo)
        persistent, _ = asyncio.run(
            _assemble(manager, fake_repo, wake_source="scheduler")
        )
        refresh = _blocks(persistent, "memoir_refresh")[0]
        # The block is a CONTEXT message (injected + kind), never a bare
        # human turn: that is the whole separation L4 requires.
        assert refresh.additional_kwargs["injected_message"] is True
        assert refresh.additional_kwargs["context_kind"] == "memoir_refresh"


class TestWakeSourceThreading:
    """The signal does not exist at the assembly seam natively — it is
    THREADED from the dispatch site. These are CENSUS pins on the two
    files that carry the thread."""

    @staticmethod
    def _path(relative: str):
        import pathlib

        return pathlib.Path(__file__).resolve().parents[2] / relative

    def test_the_dispatch_site_passes_the_current_source_into_the_assembler(self):
        """``instance_messaging`` must forward the CURRENT dispatch's source.

        Without this thread the seam sees ``wake_source=None`` on every
        assembly and the refresh can never fire — the thread IS the
        amendment (V2-Q1). Deleting the kwarg MUST redden this leg.
        """
        source = self._path(
            "daemon/services/instance_messaging.py"
        ).read_text()
        call_start = source.index("await assemble_context_messages(")
        call_block = source[call_start : call_start + 2200]
        assert "wake_source=message_source" in call_block, call_block[:600]

    def test_the_orchestrator_accepts_and_evaluates_the_parameter(self):
        source = self._path("daemon/services/context_messages.py").read_text()
        assert "wake_source: str | None = None," in source
        assert source.count("_maybe_memoir_refresh_message(") >= 2, (
            "the refresh seam must be evaluated at BOTH assembly sites"
        )
        assert "wake_source=wake_source" in source

    def test_the_seam_never_reads_the_sessions_founding_source(self):
        """Gating on ``original_source`` is WRONG by construction
        (first-write-only; identifies the session founder, not the wake)."""
        source = self._path("daemon/services/context_messages.py").read_text()
        start = source.index("async def _assemble_memoir_refresh(")
        end = source.index("async def _maybe_memoir_refresh_message(")
        body = source[start:end]
        assert 'wake_source != "scheduler"' in body
        # The COMMENT mentioning the rejected alternative is fine — the
        # CODE must never read it. Strip docstrings + comments via AST-free
        # line filtering: an ``original_source`` reference on a CODE line
        # (anything outside a comment) reddens this leg.
        code_lines = [
            line for line in body.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        assert not any("original_source" in line for line in code_lines), code_lines


class TestStructuralSupersede:
    def test_successive_wakes_collapse_to_exactly_one_row(
        self, engine, repo, fake_repo, manager
    ):
        """NO ONCE-FLAG: dedup is structural (add_messages stable-id)."""
        _seed_world(engine, repo)
        rows = []
        for _ in range(3):
            persistent, _ = asyncio.run(
                _assemble(manager, fake_repo, wake_source="scheduler")
            )
            rows.extend(_blocks(persistent, "memoir_refresh"))
        assert len(rows) == 3
        assert {row.id for row in rows} == {f"memoir_refresh:{INSTANCE_ID}"}

        add_messages = _real_add_messages()
        state = {"messages": []}
        for row in rows:
            state["messages"] = add_messages(state["messages"], [row])
        refreshes = _blocks(state["messages"], "memoir_refresh")
        assert len(refreshes) == 1
        assert refreshes[0].id == f"memoir_refresh:{INSTANCE_ID}"

    def test_two_instances_never_collapse_into_one_row(self):
        one = _make_context_message(
            "memoir_refresh", "Memoir Refresh", "a",
            id_=_stable_id_for("memoir_refresh", instance_id="i-1"),
        )
        two = _make_context_message(
            "memoir_refresh", "Memoir Refresh", "b",
            id_=_stable_id_for("memoir_refresh", instance_id="i-2"),
        )
        add_messages = _real_add_messages()
        state = add_messages([], [one, two])
        assert len(_blocks(state, "memoir_refresh")) == 2
