"""DF-4 pin (v) — the sweep's no-user-origin wake source is RESERVED.

Commission DF-4 (AGENT-ORIGIN provenance gate closure), phase P1.

The sweep (``daemon/services/upgrade_journal_sweep.py``) used to deliver a
wake that was armed with NO user origin on the bare user-origin literal
``"api"``. ``classify_user_origin("api")`` arms, so the dispatch funnel
STAMPED a DIRECT user-origin window on the woken instance — a forged
DIRECT root that ``_inherit_provenance_window`` then copied onto every
delegated child. DF-4 replaces that fallback with a reserved internal
literal (``SWEEP_NO_USER_ORIGIN_WAKE_SOURCE``), so the classifier fails it
closed and the funnel POPS the window instead of stamping one.

WHAT THIS FILE PINS
-------------------
* the literal exists, is module-level, and its NAME + VALUE are exact;
* the literal is RESERVED (``is_reserved_source`` → True) and classifies
  FAIL-CLOSED (``classify_user_origin`` → ok False);
* the bare ``or "api"`` shape is ABSENT from the sweep (both sites);
* a NON-EMPTY wake source still rides VERBATIM (the genuine path must not
  break — R3).

The file is deliberately source-anchored (the ``test_agent_restart.py``
house precedent style): a literal transcription of a shape that never
occurs would be a VACUOUS pin.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from daemon.constants import is_reserved_source
from daemon.services.instance_messaging import (
    _build_graph_input,
    _stamped_additional_kwargs,
)
from daemon.services.upgrade_journal_sweep import UpgradeJournalSweepService
from daemon.tools import upgrade_journal as uj
from daemon.tools.upgrade_journal import (
    RESTART_WAKE_SOURCE,
    SWEEP_NO_USER_ORIGIN_WAKE_SOURCE,
    classify_user_origin,
)
from tests.helpers.df4_wake_fixtures import (
    append_journal_history,
    make_install,
    make_pending_wake,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SWEEP_SOURCE = REPO_ROOT / "daemon" / "services" / "upgrade_journal_sweep.py"

EXPECTED_NAME = "SWEEP_NO_USER_ORIGIN_WAKE_SOURCE"
EXPECTED_VALUE = "system:sweep_no_user_origin_wake"


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """A tmp install dir with a real journal — never a live-shaped path."""
    return make_install(tmp_path)


class _RecordingManager:
    """Minimal delivery recorder for the SWEEP DELIVERY shape.

    Pin (v) pins the SOURCE the sweep hands to ``enqueue_message`` — the
    provenance chain itself is pinned elsewhere (pin (iii), the REAL-chain
    e2e). This recorder therefore captures the delivery kwargs verbatim
    and nothing else; it is NOT a stand-in for the manager in the
    provenance chain.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def enqueue_message(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return type("_R", (), {"message_id": "m-wake-1"})()


# ── the literal ────────────────────────────────────────────────────────────


class TestSweepReservedLiteral:
    def test_sweep_reserved_fallback_is_reserved(self) -> None:
        """DF-4 pin (v) — the canonical form.

        The literal is RESERVED, it classifies FAIL-CLOSED, and the bare
        ``or "api"`` shape is ABSENT from BOTH sweep sites.
        """
        assert is_reserved_source(SWEEP_NO_USER_ORIGIN_WAKE_SOURCE) is True
        ok, detail = classify_user_origin(SWEEP_NO_USER_ORIGIN_WAKE_SOURCE, None)
        assert ok is False
        assert detail == "reserved-internal"
        source = SWEEP_SOURCE.read_text(encoding="utf-8")
        assert 'or "api"' not in source
        assert source.count("or SWEEP_NO_USER_ORIGIN_WAKE_SOURCE") == 2

    def test_name_and_value_are_exact(self) -> None:
        """The NAME + VALUE are frozen — no variant spellings exist."""
        assert EXPECTED_NAME == "SWEEP_NO_USER_ORIGIN_WAKE_SOURCE"
        assert EXPECTED_VALUE == "system:sweep_no_user_origin_wake"
        assert getattr(uj, EXPECTED_NAME) == EXPECTED_VALUE

    def test_the_literal_is_declared_at_module_level(self) -> None:
        """MODULE level (importable), NOT nested inside a function."""
        assert EXPECTED_NAME in vars(uj), (
            f"{EXPECTED_NAME} must be a module-level attribute of "
            "daemon.tools.upgrade_journal"
        )

    def test_the_literal_carries_the_system_prefix(self) -> None:
        """The value MUST start with ``system:`` so the EXISTING reserved
        prefix family catches it — no new prefix family is introduced."""
        assert EXPECTED_VALUE.startswith("system:")

    def test_the_literal_is_reserved(self) -> None:
        """The literal is RESERVED by the ``system:`` prefix family."""
        assert is_reserved_source(SWEEP_NO_USER_ORIGIN_WAKE_SOURCE) is True

    def test_the_literal_fails_classification_closed(self) -> None:
        """``classify_user_origin`` fails it closed — the funnel POPs the
        window instead of stamping one."""
        ok, detail = classify_user_origin(SWEEP_NO_USER_ORIGIN_WAKE_SOURCE, None)
        assert ok is False
        assert detail == "reserved-internal"

    def test_the_api_literal_is_still_armed(self) -> None:
        """The genuine web-UI chat path is NOT touched — ``"api"`` STAYS
        armed (the classify arm set is frozen)."""
        ok, detail = classify_user_origin("api", None)
        assert ok is True
        assert detail == "exact:api"


# ── the sweep sites ────────────────────────────────────────────────────────


class TestSweepSitesCarryNoBareApiFallback:
    def test_the_bare_api_fallback_shape_is_absent(self) -> None:
        """The ``or "api"`` shape is ABSENT from the sweep file.

        This is the shape P1-AC-#1 greps for — a literal transcription of
        a shape that never occurs would be vacuous, so the pin greps for
        the shape the sweep ACTUALLY used.
        """
        source = SWEEP_SOURCE.read_text(encoding="utf-8")
        assert 'or "api"' not in source, (
            'the sweep must not retain the bare "api" fallback — DF-4 '
            "replaced it with the reserved literal"
        )

    def test_both_delivery_sites_use_the_reserved_literal(self) -> None:
        """BOTH sweep delivery sites (the ari fall-back arm and the direct
        arm) fall back to the reserved literal."""
        source = SWEEP_SOURCE.read_text(encoding="utf-8")
        assert source.count("or SWEEP_NO_USER_ORIGIN_WAKE_SOURCE") == 2, (
            "both sweep delivery sites must use the reserved literal"
        )

    def test_the_sweep_imports_the_literal_from_the_journal_module(self) -> None:
        """ONE import path — the sweep reuses the journal module's
        constant (no second definition, no local literal)."""
        source = SWEEP_SOURCE.read_text(encoding="utf-8")
        assert "SWEEP_NO_USER_ORIGIN_WAKE_SOURCE" in source
        assert (
            'source=f"system:sweep_no_user_origin_wake"' not in source
        ), "the literal must be imported, never re-spelled inline"


# ── the delivery behaviour ─────────────────────────────────────────────────


class TestSweepDeliverySource:
    @pytest.mark.asyncio
    async def test_empty_source_delivers_the_reserved_literal(
        self, install: Path
    ) -> None:
        """A wake armed with NO user origin (``source=""``) delivers on
        the RESERVED lane — NOT on the user-origin literal."""
        make_pending_wake(install, run_id="r-df4", source="")
        append_journal_history(install, "commit")
        manager = _RecordingManager()
        service = UpgradeJournalSweepService(install, manager=manager)
        await service.sweep_wake_records()
        assert manager.calls, "the sweep must have delivered the wake"
        delivered = manager.calls[-1]["source"]
        assert delivered == SWEEP_NO_USER_ORIGIN_WAKE_SOURCE
        # the constant's VALUE must never be the user-origin literal
        assert delivered != "api"

    @pytest.mark.asyncio
    async def test_non_empty_source_delivers_verbatim(
        self, install: Path
    ) -> None:
        """R3 — the genuine path must NOT break: a non-empty wake source
        rides VERBATIM. The reserved literal is the FALLBACK arm only."""
        make_pending_wake(install, run_id="r-df4", source="discord:user123")
        append_journal_history(install, "commit")
        manager = _RecordingManager()
        service = UpgradeJournalSweepService(install, manager=manager)
        await service.sweep_wake_records()
        assert manager.calls, "the sweep must have delivered the wake"
        assert manager.calls[-1]["source"] == "discord:user123"

    @pytest.mark.asyncio
    async def test_restart_kind_wake_still_rides_the_restart_literal(
        self, install: Path
    ) -> None:
        """The restart-kind arm is untouched — it keeps its own reserved
        literal (the R18 precedent)."""
        make_pending_wake(install, run_id="r-df4", source="", kind="restart")
        append_journal_history(install, "commit")
        manager = _RecordingManager()
        service = UpgradeJournalSweepService(install, manager=manager)
        await service.sweep_wake_records()
        assert manager.calls, "the sweep must have delivered the wake"
        # the constant's VALUE must never be the user-origin literal
        assert RESTART_WAKE_SOURCE == "system:post_restart_arm_notify"
        assert manager.calls[-1]["source"] == RESTART_WAKE_SOURCE


# ── pin (vi): the is_system_origin lane + the message-identity flip ────────


class TestSweepWakeTakesTheIsSystemOriginLane:
    """DF-4 pin (vi) — the wake takes the ``is_system_origin`` lane and its
    MESSAGE IDENTITY flips.

    Post-P1 the delivered source starts with ``system:``, so the dispatch
    lane at ``daemon/services/instance_messaging.py`` treats it as an
    internal notice: ``dispatch_source`` resolves from the instance's
    ``original_source`` metadata instead of the literal, and the durable
    ``original_source`` write no longer happens for these wakes.

    The identity flip is the load-bearing half: the ``system:`` literal is
    stamped ``injected_message=True`` by ``_stamped_additional_kwargs``,
    so the wake is no longer a bare HumanMessage. Three downstream
    effects follow (attestation-ladder exclusion, no repair-budget reset,
    compaction as an injected note) — the SAME shape the restart-kind wake
    already has in production. This pin asserts the SHAPE, so the
    disclosed behaviour is pinned rather than merely claimed.
    """

    def test_the_reserved_literal_takes_the_system_origin_lane(self) -> None:
        """The REAL lane selector is the ``system:`` prefix."""
        assert SWEEP_NO_USER_ORIGIN_WAKE_SOURCE.startswith("system:")
        source = (
            REPO_ROOT / "daemon" / "services" / "instance_messaging.py"
        ).read_text(encoding="utf-8")
        assert 'is_system_origin = message_source.startswith("system:")' in source

    def test_the_system_origin_lane_never_writes_original_source(self) -> None:
        """The durable ``original_source`` write lives in the EXTERNAL
        branch only — the system-origin lane READS the metadata, it
        never stamps itself into it.

        Analysed with ``ast`` so a comment or a docstring cannot
        mask/false-positive the branch structure.
        """
        source = (
            REPO_ROOT / "daemon" / "services" / "instance_messaging.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        branch = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            names = {
                sub.id
                for sub in ast.walk(node.test)
                if isinstance(sub, ast.Name)
            }
            if "is_system_origin" in names:
                branch = node
                break
        assert branch is not None, "the system-origin dispatch branch must exist"

        def _set_metadata_calls(body: list[ast.stmt]) -> list[int]:
            # The durable write is ``await asyncio.to_thread(
            # self._manager._instance_repository.set_metadata, ...)`` —
            # the attribute is an ARGUMENT of ``to_thread``, not the
            # callee — so scan for the attribute itself.
            return [
                sub.lineno
                for sub in ast.walk(ast.Module(body=list(body), type_ignores=[]))
                if isinstance(sub, ast.Attribute) and sub.attr == "set_metadata"
            ]

        assert _set_metadata_calls(branch.body) == [], (
            "the system-origin lane must not write original_source"
        )
        assert _set_metadata_calls(branch.orelse), (
            "the external-message branch owns the durable original_source write"
        )

    def test_the_delivered_wake_carries_the_injected_message_stamp(self) -> None:
        """The REAL stamp helper + the REAL graph-input builder.

        ``"api"`` produced a BARE HumanMessage; the reserved literal
        produces the stamped shape.
        """
        stamped = _stamped_additional_kwargs(SWEEP_NO_USER_ORIGIN_WAKE_SOURCE)
        assert stamped == {
            "injected_message": True,
            "source": SWEEP_NO_USER_ORIGIN_WAKE_SOURCE,
        }
        # the OLD literal produced a BARE HumanMessage — no stamp.
        assert _stamped_additional_kwargs("api") is None

        graph_input = _build_graph_input(
            content="wake body",
            message_id="m-df4-1",
            message_source=SWEEP_NO_USER_ORIGIN_WAKE_SOURCE,
        )
        messages = graph_input["messages"]
        assert messages, "the graph input must carry the user message"
        user_message = messages[-1]
        assert user_message.additional_kwargs == {
            "injected_message": True,
            "source": SWEEP_NO_USER_ORIGIN_WAKE_SOURCE,
        }

    def test_the_reserved_literal_is_stamped_and_the_user_literal_is_bare(self) -> None:
        """The contrast that matters: the user-origin literal stays BARE
        (the genuine web-UI path is untouched), the reserved literal is
        STAMPED — which is why the sweep must not use the user-origin
        literal as its no-user-origin fall-back."""
        assert _stamped_additional_kwargs("api") is None
        assert _stamped_additional_kwargs(
            SWEEP_NO_USER_ORIGIN_WAKE_SOURCE
        ) is not None
