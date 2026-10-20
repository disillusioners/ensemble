"""Unit tests for the ``board_link`` tool (ari-tools Phase 3 — deliverable B).

Covers the phase3-plan §7 suite B matrix:

* T-BL1 — mints ``(board, "index.html")``; the five-line shape; the
  URL is ``/views/public/board/index.html``.
* T-BL2 — ``refresh=True`` bumps the generation and issues a NEW
  passcode.
* T-BL3 — the row is NOT mintable through ``view_link`` (``board`` is
  a RESERVED root name) — ``board_link`` is the sanctioned path.
* T-BL4 — the privilege posture, ALL THREE layers: the privileged
  category, the allow-list, and the IN-BODY gate (which fires with
  ZERO side effects — no passcode row minted).

MOCK DISCIPLINE
---------------
The passcode service is the REAL
:class:`daemon.services.view_passcode_service.ViewPasscodeService` on
a REAL SQLite database — so "no passcode row was minted" is a REAL
row count, not a mock's own return value.

SAFETY FENCE: every path comes from pytest's ``tmp_path`` — no
HOME-derived or live-shaped path is resolved anywhere in this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session

from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.services.live_views import (
    RESERVED_ROOT_NAMES,
    LiveViewsService,
)
from daemon.services.view_passcode_service import (
    ViewPasscodeCipher,
    ViewPasscodeService,
)
from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
)
from daemon.tools.board_link import (
    BOARD_REL_PATH,
    BOARD_ROOT_NAME,
    create_board_link_tools,
)
from daemon.tools.live_views import create_live_view_tools
from daemon.tools.view_write import create_view_write_tools
from daemon.tools.views_auth import (
    BOARD_LINK_AGENTS,
    VIEW_WRITE_AGENTS,
    _is_views_authorized,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

BOARD_LINK_CATEGORY = "board-link"
BOARD_LINK_MODULE = "daemon.tools.board_link"


class _ManagerDouble:
    """A minimal manager double exposing the two seams the tool reads."""

    def __init__(self, live_views_service, passcode_service):
        self.live_views_service = live_views_service
        self.view_passcode_service = passcode_service
        self._view_passcode_service = passcode_service


@pytest.fixture
def live_views_service(tmp_path: Path) -> LiveViewsService:
    """A REAL LiveViewsService (no 'reports' root needed here)."""
    cfg = LiveViewsConfig()
    cfg.roots["reports"] = LiveViewsRootConfig(
        type="filesystem",
        path=str((tmp_path / "reports").resolve()),
        allowed_extensions=["html"],
    )
    return LiveViewsService(config=cfg)


@pytest.fixture
def passcode_engine(tmp_path: Path):
    db_path = tmp_path / "passcodes.sqlite"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def passcode_service(passcode_engine) -> ViewPasscodeService:
    cipher = ViewPasscodeCipher(
        encryption_key=Fernet.generate_key().decode()
    )
    return ViewPasscodeService(engine=passcode_engine, cipher=cipher)


@pytest.fixture
def manager(live_views_service, passcode_service) -> _ManagerDouble:
    return _ManagerDouble(live_views_service, passcode_service)


def _passcode_row_count(passcode_service: ViewPasscodeService) -> int:
    from daemon.repositories.view_passcodes.models import LiveViewPasscode

    with Session(passcode_service._engine) as session:
        return len(list(session.query(LiveViewPasscode).all()))


def _call(tool, **kwargs) -> str:
    import asyncio

    return asyncio.run(tool.ainvoke(kwargs))


def _stage_synthetic_agent(tmp_path: Path, agent_id: str, allow: list) -> None:
    """Stage a synthetic agent dir under tmp_path (no live paths)."""
    import daemon.registry as dr
    from daemon.registry import AgentRegistry

    real_meta = json.loads(
        (REPO_ROOT / "agents" / "watcher" / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    real_meta.pop("watchover", None)
    real_meta["id"] = agent_id
    real_meta["name"] = agent_id.title()
    real_meta["team_members"] = []
    real_meta["innate_skills"] = []
    real_meta["tools"] = {"allow": allow, "deny": []}
    agents_dir = tmp_path / "agents"
    agent_dir = agents_dir / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "meta.json").write_text(
        json.dumps(real_meta), encoding="utf-8"
    )
    registry = AgentRegistry(agents_dir)
    registry.discover()
    dr._registry = registry


def _build_instance_tools_for(
    tmp_path: Path, agent_id: str, allow: list
) -> dict:
    from unittest.mock import MagicMock

    from daemon.tools.instance import create_instance_tools

    _stage_synthetic_agent(tmp_path, agent_id, allow)
    manager = MagicMock(name="InstanceManager")
    manager.config.daemon.port = 0
    manager.config.llm.allowed_models = []
    manager._secret_service = None
    manager._instance_repository = None
    tools = create_instance_tools(manager, f"inst-{agent_id}", agent_id)
    return {getattr(t, "name", "?"): t for t in tools}


# ─────────────────────────────────────────────────────────────────
# T-BL1 — the mint + the five-line shape
# ─────────────────────────────────────────────────────────────────


class TestMintAndShape:
    def test_bl1_the_row_is_minted_and_the_shape_is_five_lines(
        self, manager, passcode_service
    ) -> None:
        tool = create_board_link_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        out = _call(tool)
        assert not out.startswith("Error:"), out
        lines = out.splitlines()
        assert [line.split(":", 1)[0] for line in lines[:5]] == [
            "Public URL",
            "Passcode",
            "Authenticated URL",
            "Generation",
            "Locked",
        ]
        assert lines[0] == "Public URL: /views/public/board/index.html"
        assert lines[2] == "Authenticated URL: /views/board/index.html"
        assert "Generation: 1" in out
        assert "Locked: no" in out
        # The passcode is 6 chars.
        assert len(lines[1].split(": ", 1)[1]) == 6
        # The REAL row exists.
        row = passcode_service.get_row("board", "index.html")
        assert row is not None
        assert row.generation == 1
        assert _passcode_row_count(passcode_service) == 1

    def test_bl1_the_call_is_idempotent(self, manager) -> None:
        tool = create_board_link_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        first = _call(tool)
        second = _call(tool)
        assert first == second

    def test_bl1_the_rel_path_is_index_html(self) -> None:
        assert BOARD_ROOT_NAME == "board"
        assert BOARD_REL_PATH == "index.html"


# ─────────────────────────────────────────────────────────────────
# T-BL2 — refresh
# ─────────────────────────────────────────────────────────────────


class TestRefresh:
    def test_bl2_refresh_bumps_the_generation_and_the_passcode(
        self, manager, passcode_service
    ) -> None:
        tool = create_board_link_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        first = _call(tool)
        second = _call(tool, refresh=True)
        assert "Generation: 1" in first
        assert "Generation: 2" in second
        assert first.splitlines()[1] != second.splitlines()[1]
        row = passcode_service.get_row("board", "index.html")
        assert row is not None and row.generation == 2

    def test_bl2_a_first_call_with_refresh_still_works(self, manager) -> None:
        """A ``refresh=True`` on a never-minted row has nothing to bump —
        the tool mints the fresh link instead, so the lockout-recovery
        call works in ONE call."""
        tool = create_board_link_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        out = _call(tool, refresh=True)
        assert not out.startswith("Error:"), out
        assert "Passcode: " in out


# ─────────────────────────────────────────────────────────────────
# T-BL3 — the reserved-name impossibility
# ─────────────────────────────────────────────────────────────────


class TestReservedName:
    def test_bl3_board_is_a_reserved_root_name(self) -> None:
        assert "board" in RESERVED_ROOT_NAMES

    def test_bl3_view_link_cannot_mint_the_board(self, manager) -> None:
        """``view_link`` refuses a reserved root name — the board's
        link is NOT mintable through the ``view-views`` category
        (which the designer holds)."""
        tool = create_live_view_tools(manager, "inst-1")[0]
        out = _call(tool, root_name="board", path="index.html")
        assert out.startswith("Error:"), out
        assert "reserved" in out

    def test_bl3_the_board_link_module_is_separate_from_the_board_data_tools(
        self,
    ) -> None:
        """D13 — a SEPARATE module + a SEPARATE category: no coupling
        to the phase-1 ``board`` category."""
        assert CATEGORY_MODULES["board"] == "daemon.tools.board_tools"
        assert CATEGORY_MODULES[BOARD_LINK_CATEGORY] == BOARD_LINK_MODULE
        assert BOARD_LINK_CATEGORY != "board"


# ─────────────────────────────────────────────────────────────────
# T-BL4 — the privilege posture (THREE layers)
# ─────────────────────────────────────────────────────────────────


class TestPrivilege:
    def test_bl4i_the_category_is_privileged(self) -> None:
        assert BOARD_LINK_CATEGORY in PRIVILEGED_TOOL_CATEGORIES

    def test_bl4i_ari_and_leader_resolve_the_tool(
        self, tmp_path: Path
    ) -> None:
        by_name = _build_instance_tools_for(
            tmp_path, "syn-bl-allow", ["board-link"]
        )
        assert "board_link" in by_name
        # An empty-allow agent resolves ZERO (privileged default-deny).
        by_name = _build_instance_tools_for(tmp_path, "syn-bl-empty", [])
        assert "board_link" not in by_name
        # A non-empty allow list without the category resolves none.
        by_name = _build_instance_tools_for(
            tmp_path, "syn-bl-other", ["bash", "filesystem", "time", "help"]
        )
        assert "board_link" not in by_name

    def test_bl4ii_the_allow_list_is_ari_and_leader_only(self) -> None:
        for agent_id, expected in (
            ("ari", True),
            ("leader", True),
            ("designer", False),
            ("worker", False),
            ("tester", False),
            ("watcher", False),
        ):
            meta = json.loads(
                (REPO_ROOT / "agents" / agent_id / "meta.json").read_text(
                    encoding="utf-8"
                )
            )
            allowed = meta["tools"]["allow"]
            assert (BOARD_LINK_CATEGORY in allowed) is expected, agent_id

    @pytest.mark.parametrize(
        "agent_id", ["", "designer", "worker", "tester", "watcher"]
    )
    def test_bl4iii_the_body_gate_denies_with_zero_side_effects(
        self, manager, passcode_service, agent_id: str
    ) -> None:
        tool = create_board_link_tools(
            manager, current_instance_id="inst-1", agent_id=agent_id
        )[0]
        out = _call(tool)
        assert out.startswith("Error:"), out
        assert "restricted to the 'ari' and 'leader' agents" in out
        # ZERO side effects — no passcode row was minted.
        assert _passcode_row_count(passcode_service) == 0

    @pytest.mark.parametrize("agent_id", ["ari", "leader"])
    def test_bl4iii_ari_and_leader_both_mint(
        self, manager, passcode_service, agent_id: str
    ) -> None:
        tool = create_board_link_tools(
            manager, current_instance_id="inst-1", agent_id=agent_id
        )[0]
        out = _call(tool)
        assert not out.startswith("Error:"), out
        assert _passcode_row_count(passcode_service) == 1

    def test_bl4_the_gate_set_is_not_the_view_write_set(self) -> None:
        """The pin must FAIL if ``view_write``'s set leaks in."""
        assert BOARD_LINK_AGENTS == frozenset({"ari", "leader"})
        assert VIEW_WRITE_AGENTS == frozenset({"ari"})
        assert BOARD_LINK_AGENTS != VIEW_WRITE_AGENTS
        assert _is_views_authorized("leader", BOARD_LINK_AGENTS) is True
        assert _is_views_authorized("leader", VIEW_WRITE_AGENTS) is False


# ─────────────────────────────────────────────────────────────────
# Registration completeness
# ─────────────────────────────────────────────────────────────────


class TestRegistration:
    def test_the_three_step_seam(self) -> None:
        assert "board_link" in DYNAMIC_TOOL_NAMES
        assert "board_link" in KNOWN_TOOL_NAMES
        assert CATEGORY_MODULES[BOARD_LINK_CATEGORY] == BOARD_LINK_MODULE

    def test_the_construction_extend_exists(self) -> None:
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance.py"
        ).read_text(encoding="utf-8")
        assert "create_board_link_tools(" in source
        assert "agent_id=agent_id," in source


# ─────────────────────────────────────────────────────────────────
# The disabled-subsystem envelope — never a dead URL
# ─────────────────────────────────────────────────────────────────


class TestDisabled:
    def test_a_disabled_subsystem_returns_the_disabled_envelope(
        self, tmp_path: Path, passcode_service
    ) -> None:
        cfg = LiveViewsConfig()
        cfg.enabled = False
        service = LiveViewsService(config=cfg)
        manager = _ManagerDouble(service, passcode_service)
        tool = create_board_link_tools(
            manager, current_instance_id="inst-1", agent_id="ari"
        )[0]
        out = _call(tool)
        assert out == "Error: live-views subsystem is disabled"
        assert _passcode_row_count(passcode_service) == 0


def test_the_view_write_tool_and_the_board_link_tool_share_the_helper() -> None:
    """ONE shared helper, TWO call sites."""
    for module in ("view_write", "board_link"):
        source = (
            REPO_ROOT / "daemon" / "tools" / f"{module}.py"
        ).read_text(encoding="utf-8")
        assert "from .views_auth import" in source
        assert "def _is_views_authorized" not in source
