"""AC8 — promote-time root equivalence (FOUR surfaces).

Central Maintenance System (P5).  The artifact root resolves to the
PROMOTE-STABLE live-install INSTALL DIR (the ancestor that owns
``releases/``), NEVER a ``releases/<ver>/`` path, and the surfaces
(seeder helper, BE list route, live-views registration, writer skill
documentation) agree on ONE shared helper
(``daemon.repositories.source.maintenance_seed.maintenance_results_dir``).

SAFETY FENCE: no live-shaped paths; temp dirs only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel, Session

from daemon.repositories.source.maintenance_seed import (
    FINDING_KIND_REPORTLESS_WEEK,
    FINDING_KIND_RESULTS_ROOT_EMPTY,
    detect_reportless_week,
    detect_results_root_empty,
    maintenance_results_dir,
    resolve_live_install_root,
)
from daemon.routers.maintenance_reports import RESULTS_ROOT as BE_RESULTS_ROOT
from daemon.api import RESULTS_ROOT as API_RESULTS_ROOT

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def test_surface1_seeder_helper() -> None:
    p = maintenance_results_dir()
    assert isinstance(p, Path)
    s = str(p)
    assert s.endswith(".agents/maintenancer/RESULTS") or s.endswith(
        ".agents\\maintenancer\\RESULTS"
    )


def test_surface2_be_list_route_uses_shared_helper() -> None:
    src = _read("daemon/routers/maintenance_reports.py")
    assert "from daemon.repositories.source.maintenance_seed import maintenance_results_dir" in src
    assert BE_RESULTS_ROOT == maintenance_results_dir()


def test_surface3_api_live_views_uses_shared_helper() -> None:
    src = _read("daemon/api.py")
    assert "from daemon.routers.maintenance_reports import" in src
    assert "RESULTS_ROOT" in src
    assert API_RESULTS_ROOT == maintenance_results_dir()


def test_all_three_code_surfaces_identical() -> None:
    assert maintenance_results_dir() == BE_RESULTS_ROOT == API_RESULTS_ROOT


def test_surface4_writer_skill_documents_single_source() -> None:
    skill = _read("agents/maintenancer/skills-template/run-report-writer.md")
    low = skill.lower()
    # single-source statement (equivalent): live-install project root
    assert "live-install" in low or "live install" in low
    assert "worktree" in low
    # no independent path-derivation recipe that could drift
    forbidden = [
        "Path.cwd()",
        "os.path.dirname(__file__)",
        "sys.executable",
        "releases/<ver>",
        "releases/<version>",
    ]
    for f in forbidden:
        assert f not in skill, f"skill contains independent derivation recipe: {f}"
    assert not re.search(r"releases/\s*<", skill)
    # promote-stable semantics
    assert "promote" in low or "install root" in low


def test_root_is_promote_stable() -> None:
    root = maintenance_results_dir()
    root_str = str(root)
    # not a releases/<version> path
    assert not re.search(r"releases[/\\]v\d", root_str)
    assert not re.search(r"releases[/\\]<", root_str)
    assert root_str.endswith(".agents/maintenancer/RESULTS") or root_str.endswith(
        ".agents\\maintenancer\\RESULTS"
    )
    install = resolve_live_install_root()
    assert not re.match(r"v\d+\.\d+", install.name)


def test_fresh_root_guard_surfaces_moved_root_as_own_finding_kind(tmp_path) -> None:
    """The fresh-root guard is a DISTINCT finding kind, never a
    reportless week."""
    from daemon.repositories.source.models import SourceConfig, ScheduleExecution
    from daemon.repositories.source.repository import SQLModelSourceRepository

    engine = create_engine(
        f"sqlite:///{tmp_path / 'guard.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    SQLModel.metadata.create_all(
        engine, tables=[SourceConfig.__table__, ScheduleExecution.__table__]
    )
    source_id = "central-maintenance-weekly"
    with Session(engine) as s:
        row = SourceConfig(
            source_id=source_id,
            source_type="scheduler",
            name="central-maintenance-weekly",
            config={"agent": "maintenancer"},
            enabled=True,
            autostart=True,
            status="running",
            created_at="t",
            updated_at="t",
        )
        s.add(row)
        s.commit()

    repo = SQLModelSourceRepository(engine)
    repo.record_execution_start(source_id, "exec-1")

    empty_root = tmp_path / "results"
    empty_root.mkdir()

    finding = detect_results_root_empty(repo, source_id=source_id, results_root=empty_root)
    assert finding is not None
    assert finding["kind"] == FINDING_KIND_RESULTS_ROOT_EMPTY
    assert finding["kind"] != FINDING_KIND_REPORTLESS_WEEK

    # reportless-week detector returns None for the empty-root shape
    assert detect_reportless_week(repo, source_id=source_id, results_root=empty_root) is None

    # constants are distinct
    assert FINDING_KIND_RESULTS_ROOT_EMPTY != FINDING_KIND_REPORTLESS_WEEK
    assert FINDING_KIND_RESULTS_ROOT_EMPTY == "results_root_empty"
    assert FINDING_KIND_REPORTLESS_WEEK == "reportless_week"


def test_fresh_root_guard_de_duplicates_board_item(tmp_path) -> None:
    """The guard writes ONE de-duplicated board item across repeated boots."""
    from sqlalchemy import create_engine, select
    from sqlmodel import SQLModel, Session
    from daemon.repositories.source.models import SourceConfig, ScheduleExecution
    from daemon.repositories.source.repository import SQLModelSourceRepository
    from daemon.repositories.source.maintenance_seed import (
        _detect_and_surface_results_root_empty,
        BOARD_TITLE_ROOT_EMPTY,
    )
    from daemon.models.board import BoardItem, BoardItemTag

    engine = create_engine(
        f"sqlite:///{tmp_path / 'guard2.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[SourceConfig.__table__, ScheduleExecution.__table__, BoardItem.__table__, BoardItemTag.__table__],
    )
    source_id = "central-maintenance-weekly"
    with Session(engine) as s:
        s.add(
            SourceConfig(
                source_id=source_id,
                source_type="scheduler",
                name="central-maintenance-weekly",
                config={"agent": "maintenancer"},
                enabled=True,
                autostart=True,
                status="running",
                created_at="t",
                updated_at="t",
            )
        )
        s.commit()

    repo = SQLModelSourceRepository(engine)
    repo.record_execution_start(source_id, "exec-1")
    empty_root = tmp_path / "results2"
    empty_root.mkdir()

    _detect_and_surface_results_root_empty(repo, source_id=source_id, results_root=empty_root, engine=engine)
    _detect_and_surface_results_root_empty(repo, source_id=source_id, results_root=empty_root, engine=engine)

    with Session(engine) as s:
        items = s.exec(select(BoardItem)).all()
    assert len(items) == 1
