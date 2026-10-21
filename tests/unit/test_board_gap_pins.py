"""Board GAP PINS — the four coverage gaps the P9 recon found in the
ari-tools Phase 1 board tests (the "the Workbook" phase).

The Phase 1 board suite (``tests/unit/test_board_service.py`` +
``tests/unit/test_board_registration.py``) is green, but four seams
were DOCUMENTED and never PINNED. This file closes them — ONE new
file, no product change:

  1. ``due_at=""`` / ``due_before=""`` — the fail-closed empty-string
     rule documented at ``daemon/services/board_service.py:507-511``
     ("an EMPTY-STRING ``due_at`` is rejected fail-closed INTENTIONALLY
     (plan Q2) — ``due_at`` is replaceable-not-clearable, so ``""``
     would be ambiguous; it is a validation error, never a silent
     clear") was untested at BOTH the service and the tool boundary.
  2. The literal-tool-name bypass arm of the DUAL gate — the second
     gate is the tool body's ``_is_board_authorized`` check, and the
     privileged ``board`` CATEGORY gate is bypassed by a literal tool
     name in an allow list (``daemon/tools/instance.py``: a non-category
     allow entry is added VERBATIM). Both arms are pinned: the literal
     name grants VISIBILITY, the tool body still refuses AUTHORITY.
  3. ``_is_board_authorized(None)`` is False and
     ``BOARD_AUTHORIZED_AGENTS`` is EXACTLY ``frozenset({"ari"})`` —
     the authority set is a one-line constant, so a
     silent widening would be invisible to the rest of the suite.
  4. The commission's CORE goal — the board is ONE GLOBAL namespace:
     an item created by one Ari instance is visible to ANOTHER Ari
     instance, and that instance's note is stamped with ITS OWN
     instance id. Pinned through the REAL tool layer (two factories
     over ONE real service), plus the negative arm (a third,
     unauthorized factory sees an ``ERROR:`` envelope and changes
     NOTHING).
  5. The tool-layer query budget — ``board_list`` costs EXACTLY two
     statements (items + one batched tags), the ``limit + 1``
     ``has_more`` probe adds ZERO queries, and ``board_get`` on an
     item WITH notes costs at most three.
  6. The mixed-offset ``due_before`` INVERSION probe through the
     TOOL — a ``+07:00`` deadline compared as TEXT against a UTC
     stored row would silently invert the filter (return the LATER
     items, drop the EARLIER ones — no error, no visible symptom).
  7. ``tags`` are structurally NOT accepted by ``update_item`` — the
     immutability is a SHAPE property (the kwarg does not exist), not
     a runtime check, so it is pinned by the ``TypeError``.

MOCK DISCIPLINE (the project rule born from this phase's own review —
the F1 bug survived a mocked service): the REAL ``BoardService`` over
an in-memory SQLite engine EVERYWHERE. ZERO service mocks in this
file. The engine shape mirrors
``tests/unit/test_board_service.py::_make_engine`` (``sqlite://`` +
``StaticPool`` + ``check_same_thread=False`` + an explicit
``PRAGMA foreign_keys=ON`` connect listener); the allow-resolution
seam mirrors ``tests/unit/test_board_registration.py::TestToolVisibility``
(same imports, same call shape: a synthetic agent staged under
pytest's ``tmp_path``, a monkeypatched registry, and
``create_instance_tools(manager, instance_id, agent_id)``).

SAFETY FENCE: every path is either in-memory SQLite or pytest's
``tmp_path``. No HOME-derived or live-shaped path is ever resolved; no
port is contacted; no process is touched.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

# Importing the models registers the board tables (and the ``projects``
# table the board's project_id FK points at) on ``SQLModel.metadata``.
# Without the ``projects`` registration the FK cannot be emitted.
from daemon.models.board import (  # noqa: F401
    BoardItem,
    BoardItemNotFound,
    BoardItemTag,
    BoardNote,
    BoardValidationError,
)
from daemon.repositories.project.models import Project  # noqa: F401
from daemon.services.board_service import BoardService
from daemon.tools.board_tools import (
    BOARD_AUTHORIZED_AGENTS,
    _is_board_authorized,
    create_board_tools,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The four tools of the ``board`` category.
BOARD_TOOL_NAMES: frozenset[str] = frozenset({
    "board_create",
    "board_update",
    "board_list",
    "board_get",
})


# ── the engine + service (mirrors test_board_service.py::_make_engine) ────


def _make_engine(url: str = "sqlite://", *, fk_on: bool = True):
    """Build a SQLite engine with the daemon's PRAGMA posture.

    Mirrors ``tests/unit/test_board_service.py::_make_engine`` — the
    in-memory URL, the ``StaticPool`` (one shared connection, so the
    tool closures and the assertions see the same rows), the
    ``check_same_thread=False`` connect arg, and the explicit
    ``PRAGMA foreign_keys=ON`` listener.
    """
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    if fk_on:

        @event.listens_for(engine, "connect")
        def _set_pragma(dbapi_conn, _record):  # pragma: no cover - trivial
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


@pytest.fixture
def engine():
    eng = _make_engine()
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def service(engine):
    return BoardService(engine=engine)


# ── the tool layer (REAL tools over the REAL service) ────────────────────


def _tool_map(service, *, instance_id: str, agent_id: str) -> dict:
    """Build the four REAL board tools and index them by name."""
    tools = create_board_tools(
        service,
        current_instance_id=instance_id,
        agent_id=agent_id,
    )
    return {getattr(t, "name", "?"): t for t in tools}


def _count_rows(engine) -> int:
    """Count the board_items rows (a raw statement — no ORM overhead)."""
    with Session(engine) as session:
        return int(
            session.execute(text("SELECT COUNT(*) FROM board_items")).scalar_one()
        )


@contextlib.contextmanager
def _capture_statements(engine):
    """Collect every SQL statement the engine emits inside the block."""
    captured: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(_conn, _cursor, statement, _params, _ctx, _many):
        captured.append(statement)

    try:
        yield captured
    finally:
        event.remove(engine, "before_cursor_execute", _capture)


# ── the allow-resolution seam (mirrors test_board_registration.py) ───────


def _stage_synthetic_agent(
    tmp_path: Path, agent_id: str, tools_cfg: dict | None
) -> Path:
    """Stage a synthetic agent dir under tmp_path by cloning watcher's
    real meta.json shape with a replaced tools config.

    Identical to ``tests/unit/test_board_registration.py``'s helper —
    the synthetic agent never leaves ``tmp_path``, so no HOME-derived
    or live-shaped path is resolved.
    """
    real_meta = json.loads(
        (REPO_ROOT / "agents" / "watcher" / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    real_meta.pop("watchover", None)  # irrelevant for tool filtering
    real_meta["id"] = agent_id
    real_meta["name"] = agent_id.title()
    real_meta["team_members"] = []
    real_meta["innate_skills"] = []
    real_meta["tools"] = tools_cfg
    agents_dir = tmp_path / "agents"
    agent_dir = agents_dir / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "meta.json").write_text(json.dumps(real_meta), encoding="utf-8")
    return agents_dir


@pytest.fixture
def registry_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Factory fixture: stage a synthetic agent, install a registry that
    discovers it, and return a fresh tool-builder bound to it."""

    def _install(agents_dir: Path):
        import daemon.registry as dr
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(agents_dir)
        registry.discover()
        monkeypatch.setattr(dr, "_registry", registry)
        return registry

    return _install


def _build_instance_tools(agent_id: str, board_service) -> dict[str, object]:
    """Build the REAL instance tool set for ``agent_id``.

    Mirrors ``tests/unit/test_board_registration.py::_build_instance_tools``
    — a ``MagicMock`` manager with the four attributes the factory
    dereferences, PLUS the REAL ``board_service`` so the board tools
    are wired to a real service (never a mock).
    """
    from daemon.tools.instance import create_instance_tools

    manager = MagicMock(name="InstanceManager")
    manager.config.daemon.port = 0
    manager.config.llm.allowed_models = []
    # Tolerate a None-stub SecretService (the factory dereferences it
    # at call time only).
    manager._secret_service = None
    manager._instance_repository = None
    # The REAL board service — the board tools must not be mock-backed.
    manager.board_service = board_service
    tools = create_instance_tools(manager, f"inst-{agent_id}", agent_id)
    return {getattr(t, "name", "?"): t for t in tools}


# ── gap 1 — the empty-string deadline is fail-closed ─────────────────────


class TestGapEmptyStringDeadlineIsFailClosed:
    """The ``""`` deadline is a VALIDATION ERROR, never a silent clear.

    ``due_at`` is replaceable-not-clearable in v1, so an empty string
    is ambiguous — the service rejects it on BOTH the write path and
    the filter path, and the tool boundary turns that into the house
    ``ERROR:`` envelope.
    """

    def test_gap_service_create_rejects_empty_due_at(self, service):
        with pytest.raises(BoardValidationError):
            service.create_item(
                title="x",
                due_at="",
                created_by_instance="inst-a",
            )

    def test_gap_service_update_rejects_empty_due_at(self, service):
        item = service.create_item(
            title="x",
            due_at="2026-10-21T09:00:00+07:00",
            created_by_instance="inst-a",
        )
        with pytest.raises(BoardValidationError):
            service.update_item(
                item_id=item["id"],
                due_at="",
                author_instance="inst-a",
            )

    def test_gap_service_list_rejects_empty_due_before(self, service):
        with pytest.raises(BoardValidationError):
            service.list_items(due_before="")

    def test_gap_tool_create_rejects_empty_due_at(self, service, engine):
        tools = _tool_map(service, instance_id="inst-ari", agent_id="ari")
        before = _count_rows(engine)
        result = tools["board_create"].invoke({"title": "x", "due_at": ""})
        assert isinstance(result, str)
        assert result.startswith("ERROR:"), result
        assert _count_rows(engine) == before, (
            "a rejected create must not leave a row behind"
        )

    def test_gap_tool_update_rejects_empty_due_at(self, service, engine):
        tools = _tool_map(service, instance_id="inst-ari", agent_id="ari")
        created = tools["board_create"].invoke({"title": "x"})
        assert isinstance(created, dict)
        before = _count_rows(engine)
        result = tools["board_update"].invoke(
            {"id": created["id"], "due_at": ""}
        )
        assert isinstance(result, str)
        assert result.startswith("ERROR:"), result
        assert _count_rows(engine) == before, (
            "a rejected update must not create or drop a row"
        )


# ── gap 2 — the literal-tool-name arm of the dual gate ───────────────────


class TestGapLiteralToolNameBypassArm:
    """The privileged CATEGORY gate is bypassed by a literal tool name.

    ``resolve_tool_filter`` adds a non-category allow entry VERBATIM,
    so ``tools.allow: ["board_list"]`` grants the VISIBILITY of one
    board tool even though the agent never named the ``board``
    category. The SECOND gate — the tool body's
    ``_is_board_authorized`` — is what refuses the call.
    """

    def test_gap_literal_allow_entry_grants_visibility_only(
        self, tmp_path: Path, registry_for, service, engine
    ):
        agents_dir = _stage_synthetic_agent(
            tmp_path, "worker", {"allow": ["board_list"]}
        )
        registry_for(agents_dir)
        by_name = _build_instance_tools("worker", service)

        assert "board_list" in by_name, (
            "a literal tool name in the allow list must resolve — the "
            "literal arm of the gate is a documented bypass"
        )
        for absent in ("board_create", "board_get", "board_update"):
            assert absent not in by_name, (
                f"{absent} must NOT resolve from allow=[board_list]"
            )

        # The second gate: the tool body refuses an unauthorized agent.
        before = _count_rows(engine)
        result = by_name["board_list"].invoke({})
        assert isinstance(result, str), (
            f"an unauthorized board_list must answer with the house "
            f"error envelope, got {type(result).__name__}"
        )
        assert result.startswith("ERROR:"), result
        assert _count_rows(engine) == before, (
            "a refused board_list must have zero side effects"
        )

    def test_gap_category_allow_entry_grants_all_four(
        self, tmp_path: Path, registry_for, service
    ):
        agents_dir = _stage_synthetic_agent(
            tmp_path, "ari", {"allow": ["board"]}
        )
        registry_for(agents_dir)
        by_name = _build_instance_tools("ari", service)
        assert BOARD_TOOL_NAMES <= set(by_name), (
            f"tools.allow=[board] must resolve all four, got "
            f"{sorted(set(by_name) & BOARD_TOOL_NAMES)}"
        )


# ── gap 3 — the authority set is exactly two agents ──────────────────────


class TestGapAuthorizationSet:
    """``BOARD_AUTHORIZED_AGENTS`` gates AUTHORITY, not visibility."""

    def test_gap_is_board_authorized_none_is_false(self):
        assert _is_board_authorized(None) is False

    def test_gap_authorized_set_is_exactly_ari(self):
        """ARI-ONLY (user ruling 2026-10-20)."""
        assert BOARD_AUTHORIZED_AGENTS == frozenset({"ari"})

    def test_gap_is_board_authorized_refuses_an_unknown_agent(self):
        assert _is_board_authorized("worker") is False
        assert _is_board_authorized("") is False
        assert _is_board_authorized("leader") is False
        assert _is_board_authorized("ari") is True


# ── gap 4 — ONE global namespace across instances ────────────────────────


class TestGapGlobalNamespace:
    """The board is ONE global namespace — there is no per-tree board.

    An item created by one Ari instance is visible to ANOTHER Ari
    instance, and that other instance's note is stamped with ITS OWN
    instance id. The item's ``created_by_instance`` stays the creator's.

    NOTE (user ruling 2026-10-20): the board categories are ARI-ONLY,
    so the second participant is a second ARI instance (a different
    instance id, the same agent) rather than the leader — the
    global-namespace property is unchanged by the audience narrowing.
    """

    def test_gap_item_created_by_one_ari_is_visible_to_another(
        self, service, engine
    ):
        ari = _tool_map(
            service, instance_id="inst-ari-ctx", agent_id="ari"
        )
        ari_two = _tool_map(
            service, instance_id="inst-ari-2-ctx", agent_id="ari"
        )

        created = ari["board_create"].invoke(
            {
                "title": "Reply to the invoice question",
                "tags": ["telegram"],
                "due_at": "2026-10-21T09:00:00+07:00",
            }
        )
        assert isinstance(created, dict), created
        item_id = created["id"]
        assert created["created_by_instance"] == "inst-ari-ctx"

        # The second Ari instance sees it — the same global namespace, no
        # per-tree scoping anywhere in the path.
        listed = ari_two["board_list"].invoke({})
        assert isinstance(listed, dict), listed
        ids = [item["id"] for item in listed["items"]]
        assert item_id in ids, (
            f"the second Ari instance must see the first's item; got {ids}"
        )

        # The second instance's note is stamped with ITS instance id.
        updated = ari_two["board_update"].invoke(
            {"id": item_id, "note": "from the second ari"}
        )
        assert isinstance(updated, dict), updated
        fetched = ari_two["board_get"].invoke({"id": item_id})
        assert isinstance(fetched, dict), fetched
        assert fetched["notes"], "the second instance's note must be on the timeline"
        assert fetched["notes"][0]["author_instance"] == "inst-ari-2-ctx"
        assert fetched["notes"][0]["text"] == "from the second ari"
        # The creator is still the FIRST Ari instance.
        assert fetched["created_by_instance"] == "inst-ari-ctx"

    def test_gap_unauthorized_factory_sees_nothing_and_changes_nothing(
        self, service, engine
    ):
        ari = _tool_map(
            service, instance_id="inst-ari-ctx", agent_id="ari"
        )
        worker = _tool_map(
            service, instance_id="inst-worker-ctx", agent_id="worker"
        )

        created = ari["board_create"].invoke({"title": "private-ish"})
        assert isinstance(created, dict), created
        before = _count_rows(engine)

        # Every call must carry a VALID input shape — the LangChain
        # input schema is validated BEFORE the tool body runs, so a
        # missing required field would raise instead of reaching the
        # authorization gate.
        calls = {
            "board_create": {"title": "x"},
            "board_update": {"id": created["id"]},
            "board_list": {},
            "board_get": {"id": created["id"]},
        }
        for name in sorted(BOARD_TOOL_NAMES):
            result = worker[name].invoke(calls[name])
            assert isinstance(result, str), (
                f"{name} must answer an unauthorized agent with the house "
                f"error envelope, got {type(result).__name__}"
            )
            assert result.startswith("ERROR:"), result

        assert _count_rows(engine) == before, (
            "an unauthorized factory must have zero side effects"
        )


# ── gap 5 — the tool-layer query budget ─────────────────────────────────


class TestGapToolQueryBudget:
    """The tool layer must not smuggle an N+1 back in.

    ``board_list`` costs EXACTLY two statements (the items + ONE
    batched tags), the ``limit + 1`` ``has_more`` probe adds ZERO
    queries, and ``board_get`` on an item WITH notes costs at most
    three (item + tags + notes).
    """

    def test_gap_board_list_costs_exactly_two_statements(self, service, engine):
        tools = _tool_map(service, instance_id="inst-ari", agent_id="ari")
        for i in range(3):
            tools["board_create"].invoke({"title": f"t{i}", "tags": ["x"]})

        with _capture_statements(engine) as captured:
            result = tools["board_list"].invoke({})
        assert isinstance(result, dict), result
        assert result["count"] == 3
        assert len(captured) == 2, (
            f"board_list must cost exactly 2 statements, got "
            f"{len(captured)}: {captured}"
        )

    def test_gap_has_more_probe_adds_no_extra_statement(self, service, engine):
        tools = _tool_map(service, instance_id="inst-ari", agent_id="ari")
        for i in range(3):
            tools["board_create"].invoke({"title": f"t{i}", "tags": ["x"]})

        with _capture_statements(engine) as captured:
            result = tools["board_list"].invoke({"limit": 1})
        assert isinstance(result, dict), result
        assert result["_pagination"]["has_more"] is True
        assert result["_pagination"]["returned"] == 1
        assert len(captured) == 2, (
            f"the limit+1 has_more probe must add ZERO queries, got "
            f"{len(captured)}: {captured}"
        )

    def test_gap_board_get_costs_at_most_three_statements(self, service, engine):
        tools = _tool_map(service, instance_id="inst-ari", agent_id="ari")
        created = tools["board_create"].invoke({"title": "t", "tags": ["x"]})
        tools["board_update"].invoke(
            {"id": created["id"], "note": "a note"}
        )

        with _capture_statements(engine) as captured:
            result = tools["board_get"].invoke({"id": created["id"]})
        assert isinstance(result, dict), result
        assert len(captured) <= 3, (
            f"board_get must cost at most 3 statements, got "
            f"{len(captured)}: {captured}"
        )


# ── gap 6 — the mixed-offset due_before inversion probe ──────────────────


class TestGapDueBeforeOffsetThroughTheTool:
    """A ``+07:00`` deadline must not invert the ``due_before`` filter.

    ``item A`` is due ``2026-10-21T09:00:00+07:00`` (= ``2026-10-21
    T02:00:00Z``); ``item B`` is due ``2026-10-20T20:00:00+00:00``.
    A raw agent string compared as TEXT against the UTC-stored rows
    would ALSO return the LATER item (A) and drop the EARLIER one (B) —
    no error, no visible symptom. The filter is STRICTLY before (the
    service docstring and the tool doc both say so), so a filter that
    lands exactly ON an item's deadline excludes that item.
    """

    def test_gap_tool_due_before_does_not_invert(self, service):
        tools = _tool_map(service, instance_id="inst-ari", agent_id="ari")
        a = tools["board_create"].invoke(
            {
                "title": "A",
                "due_at": "2026-10-21T09:00:00+07:00",
            }
        )
        b = tools["board_create"].invoke(
            {
                "title": "B",
                "due_at": "2026-10-20T20:00:00+00:00",
            }
        )
        assert isinstance(a, dict) and isinstance(b, dict)
        # Both deadlines are stored NORMALISED to UTC, fixed width.
        assert a["due_at"] == "2026-10-21T02:00:00.000000+00:00"
        assert b["due_at"] == "2026-10-20T20:00:00.000000+00:00"

        # (1) The filter instant sits STRICTLY BETWEEN B and A
        #     (2026-10-21T04:00:00+07:00 == 2026-10-20T21:00:00Z):
        #     only B is strictly before it. A raw TEXT comparison
        #     against the UTC-stored rows would ALSO return A (the
        #     LATER item) — the inversion defect, invisible to the
        #     caller.
        between = tools["board_list"].invoke(
            {"due_before": "2026-10-21T04:00:00+07:00"}
        )
        assert isinstance(between, dict), between
        assert [item["id"] for item in between["items"]] == [b["id"]], (
            "the strictly-before filter must return exactly item B — an "
            "inverted comparison would also return A and drop B"
        )

        # (2) The filter instant LANDS EXACTLY ON B's deadline
        #     (2026-10-21T03:00:00+07:00 == 2026-10-20T20:00:00Z).
        #     ``due_before`` is STRICTLY before (the service docstring
        #     and the tool doc both say so), so B — sitting exactly ON
        #     the instant — is NOT returned, and A (later) is not
        #     either. A raw TEXT comparison would return BOTH items
        #     here, so this discriminates the inversion too.
        on_the_instant = tools["board_list"].invoke(
            {"due_before": "2026-10-21T03:00:00+07:00"}
        )
        assert isinstance(on_the_instant, dict), on_the_instant
        assert on_the_instant["items"] == [], (
            "a filter that lands exactly on an item's deadline excludes "
            "it (strictly-before) — and it must never return the LATER "
            "item A"
        )

        # (3) A later filter returns both items.
        wide = tools["board_list"].invoke(
            {"due_before": "2026-10-21T10:00:00+07:00"}
        )
        assert isinstance(wide, dict), wide
        assert sorted(item["id"] for item in wide["items"]) == sorted(
            [a["id"], b["id"]]
        ), (
            "a later filter must return both items — the mixed-offset "
            "normalisation dropped one of them"
        )


# ── gap 7 — tags are structurally immutable ─────────────────────────────


class TestGapTagsAreStructurallyImmutable:
    """``tags`` cannot be re-set — the kwarg does not exist.

    The immutability is a SHAPE property (the parameter is absent), so
    the pin is the ``TypeError`` itself, not a runtime rejection.
    """

    def test_gap_update_item_rejects_a_tags_kwarg(self, service):
        item = service.create_item(
            title="x", tags=["a"], created_by_instance="inst-a"
        )
        with pytest.raises(TypeError):
            service.update_item(
                item_id=item["id"],
                author_instance="inst-a",
                tags=["b"],
            )

    def test_gap_update_tool_schema_has_no_tags_field(self):
        tools = _tool_map(
            BoardService(engine=_make_engine()), instance_id="i", agent_id="ari"
        )
        schema = tools["board_update"].args_schema
        assert schema is not None
        assert "tags" not in schema.model_fields, (
            "the board_update input schema must not accept tags — the "
            "tool must not be able to express a re-tag"
        )
