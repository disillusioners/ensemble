"""Unit tests for the agent-facing ``board`` tools (ari-tools Phase 1).

Drives ``create_board_tools`` (``daemon/tools/board_tools.py``) with a
MOCKED service — the four ``@tool`` callables are invoked directly
(house convention for sync tools, mirrors ``tests/unit/tools/
test_scheduling_tools.py``).

Pinned contract (phase1-plan §7):
  * the factory returns EXACTLY 4 tools
  * the four Input schemas — required vs optional fields and defaults
  * tools NEVER raise — every failure is an ``"ERROR: {msg}"`` string
  * the error envelopes: not-found, validation, and the generic
    unexpected-failure envelope (no ``str(exc)`` in the payload)
  * the BODY GATE — an ``agent_id`` outside ``{"ari", "leader"}`` gets
    an ``"ERROR: …"`` envelope and NEVER reaches the service
  * ``board_list(status="open")`` passes through
  * ``board_list`` still returns NO ``notes[]`` — the tool item shape
    is unchanged by the service's batched hydration
  * ``board_list`` composes a TRUTHFUL always-present ``_pagination``
    from the service's additive pagination keys — and a ``limit``
    beyond the 200 ceiling leaves a VISIBLE ``clamped`` marker, with
    NO ``offset=`` hint (review F1; the REAL-service regressions live
    in ``tests/unit/test_board_service.py``)
  * ``board_update`` appends a note and bumps ``updated_at``
  * ``board_update`` has NO tag parameter — asserted as a SCHEMA
    property of ``BoardUpdateInput`` (not merely as a runtime outcome)
  * ``board_create`` stamps ``created_by_instance`` from the factory
    closure — the agent cannot supply it

SAFETY FENCE: pure unit tests — mocks only, the daemon is NEVER booted
and no DB is ever touched, so no ambient ``POSTGRES_*`` can leak
anywhere.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from daemon.services.board_service import (
    BoardItemNotFound,
    BoardValidationError,
)
from daemon.tools.board_tools import (
    BOARD_AUTHORIZED_AGENTS,
    CATEGORY_DOC,
    CATEGORY_NAME,
    _is_board_authorized,
    create_board_tools,
)

CALLER_INSTANCE = "inst-caller-1"
CALLER_AGENT = "ari"

#: The canonical item shape (phase1-plan §7.2).
ITEM_FIELDS = {
    "id",
    "title",
    "body_md",
    "status",
    "tags",
    "project_id",
    "due_at",
    "priority",
    "linked_job_ids",
    "created_by_instance",
    "created_at",
    "updated_at",
}

TOOL_NAMES = {"board_create", "board_update", "board_list", "board_get"}


def _item(**overrides) -> dict:
    values = {
        "id": "wi-abcdef12",
        "title": "Ship the board",
        "body_md": "",
        "status": "todo",
        "tags": [],
        "project_id": None,
        "due_at": None,
        "priority": None,
        "linked_job_ids": [],
        "created_by_instance": CALLER_INSTANCE,
        "created_at": "2026-10-20T01:00:00.000000+00:00",
        "updated_at": "2026-10-20T01:00:00.000000+00:00",
    }
    values.update(overrides)
    return values


@pytest.fixture
def tool_env():
    """The four tools bound to a mocked service."""
    service = MagicMock()
    service.create_item = MagicMock(return_value=_item())
    service.update_item = MagicMock(
        return_value=_item(status="in_progress", note_appended=True)
    )
    service.list_items = MagicMock(
        return_value={
            "items": [_item()],
            "count": 1,
            "has_more": False,
            "applied_limit": 50,
        }
    )
    service.get_item = MagicMock(return_value=_item())
    tools = create_board_tools(
        service,
        current_instance_id=CALLER_INSTANCE,
        agent_id=CALLER_AGENT,
    )
    by_name = {t.name: t for t in tools}
    assert set(by_name) == TOOL_NAMES
    return by_name, service


# ── the factory surface ──────────────────────────────────────────────────


class TestFactorySurface:
    def test_factory_returns_exactly_four_tools(self, tool_env):
        by_name, _service = tool_env
        assert len(by_name) == 4
        assert set(by_name) == TOOL_NAMES

    def test_every_tool_carries_the_board_category(self, tool_env):
        by_name, _service = tool_env
        for tool in by_name.values():
            assert tool._tool_category == "board"

    def test_every_tool_carries_a_full_doc(self, tool_env):
        by_name, _service = tool_env
        for name, tool in by_name.items():
            assert getattr(tool, "_full_doc_", None), f"{name} has no _full_doc_"

    def test_category_constants(self):
        assert CATEGORY_NAME == "Board"
        assert "board_create" in CATEGORY_DOC
        assert "board_update" in CATEGORY_DOC
        assert "board_list" in CATEGORY_DOC
        assert "board_get" in CATEGORY_DOC
        # The open-items convention is documented in the category doc.
        assert "open" in CATEGORY_DOC


# ── the input schemas ────────────────────────────────────────────────────


class TestInputSchemas:
    def test_create_schema_is_the_create_only_field_set(self, tool_env):
        by_name, _service = tool_env
        fields = by_name["board_create"].args_schema.model_fields
        assert set(fields) == {
            "title",
            "body",
            "project_id",
            "tags",
            "due_at",
            "priority",
        }
        assert fields["title"].is_required()
        for optional in ("body", "project_id", "tags", "due_at", "priority"):
            assert not fields[optional].is_required()
        assert fields["body"].default == ""
        assert fields["project_id"].default is None
        assert fields["tags"].default is None
        assert fields["due_at"].default is None
        assert fields["priority"].default is None

    def test_update_schema_has_no_tag_parameter(self, tool_env):
        """``tags`` is IMMUTABLE post-create — the update shape can
        neither add nor remove a tag. Asserted as a SCHEMA property:
        the Input model exposes no ``tags`` field at all, so the whole
        create-only set {title, project_id, tags, priority} is not
        settable through the update shape."""
        by_name, _service = tool_env
        fields = by_name["board_update"].args_schema.model_fields
        assert "tags" not in fields
        for create_only in ("title", "project_id", "priority"):
            assert create_only not in fields
        assert set(fields) == {
            "id",
            "status",
            "note",
            "add_job_id",
            "body",
            "due_at",
        }
        assert fields["id"].is_required()
        for optional in ("status", "note", "add_job_id", "body", "due_at"):
            assert not fields[optional].is_required()

    def test_list_schema_defaults(self, tool_env):
        by_name, _service = tool_env
        fields = by_name["board_list"].args_schema.model_fields
        assert set(fields) == {
            "status",
            "project_id",
            "tag",
            "due_before",
            "limit",
        }
        assert fields["limit"].default == 50
        for optional in ("status", "project_id", "tag", "due_before"):
            assert fields[optional].default is None

    def test_get_schema(self, tool_env):
        by_name, _service = tool_env
        fields = by_name["board_get"].args_schema.model_fields
        assert set(fields) == {"id"}
        assert fields["id"].is_required()


# ── board_create ─────────────────────────────────────────────────────────


class TestBoardCreate:
    def test_returns_the_created_item(self, tool_env):
        by_name, service = tool_env
        result = by_name["board_create"].invoke(
            {"title": "Ship the board", "body": "b", "tags": ["deploy"]}
        )
        assert isinstance(result, dict)
        assert set(result) == ITEM_FIELDS
        service.create_item.assert_called_once()
        kwargs = service.create_item.call_args.kwargs
        assert kwargs["title"] == "Ship the board"
        # The bound input is ``body``; the stored field is ``body_md``.
        assert kwargs["body_md"] == "b"
        assert kwargs["tags"] == ["deploy"]

    def test_created_by_instance_comes_from_the_closure(self, tool_env):
        """The author is stamped by the factory closure — the agent
        cannot supply (or spoof) it."""
        by_name, service = tool_env
        by_name["board_create"].invoke({"title": "x"})
        kwargs = service.create_item.call_args.kwargs
        assert kwargs["created_by_instance"] == CALLER_INSTANCE
        # It is NOT an agent-supplied parameter.
        assert "created_by_instance" not in by_name[
            "board_create"
        ].args_schema.model_fields

    def test_service_error_becomes_an_error_string(self, tool_env):
        by_name, service = tool_env
        service.create_item = MagicMock(
            side_effect=BoardValidationError(
                "due_at must be a timezone-aware ISO-8601 string"
            )
        )
        result = by_name["board_create"].invoke({"title": "x"})
        assert result == (
            "ERROR: due_at must be a timezone-aware ISO-8601 string"
        )


# ── board_update ─────────────────────────────────────────────────────────


class TestBoardUpdate:
    def test_appends_a_note_and_bumps_updated_at(self, tool_env):
        by_name, service = tool_env
        service.update_item = MagicMock(
            return_value=_item(
                status="in_progress",
                updated_at="2026-10-20T02:00:00.000000+00:00",
                note_appended=True,
            )
        )
        result = by_name["board_update"].invoke(
            {"id": "wi-abcdef12", "status": "in_progress", "note": "a note"}
        )
        assert result["note_appended"] is True
        assert result["updated_at"] > _item()["updated_at"]
        kwargs = service.update_item.call_args.kwargs
        assert kwargs["item_id"] == "wi-abcdef12"
        assert kwargs["status"] == "in_progress"
        assert kwargs["note"] == "a note"
        assert kwargs["author_instance"] == CALLER_INSTANCE

    def test_not_found_envelope(self, tool_env):
        by_name, service = tool_env
        service.update_item = MagicMock(
            side_effect=BoardItemNotFound("board item not found: wi-abcdef12")
        )
        result = by_name["board_update"].invoke({"id": "wi-abcdef12"})
        assert result == "ERROR: board item not found: wi-abcdef12"

    def test_a_tags_key_cannot_reach_the_service(self, tool_env):
        """``tags`` is not part of the update schema, so a stray
        ``tags`` key in the input can never reach the service — the
        create-only set is not settable through the update shape and
        tags stay IMMUTABLE post-create."""
        by_name, service = tool_env
        service.update_item = MagicMock(
            return_value=_item(tags=["original"], note_appended=False)
        )
        result = by_name["board_update"].invoke(
            {"id": "wi-abcdef12", "tags": ["nope"]}
        )
        kwargs = service.update_item.call_args.kwargs
        assert "tags" not in kwargs
        assert result["tags"] == ["original"]


# ── board_list ───────────────────────────────────────────────────────────


class TestBoardList:
    def test_open_status_passes_through(self, tool_env):
        by_name, service = tool_env
        by_name["board_list"].invoke({"status": "open"})
        assert service.list_items.call_args.kwargs["status"] == "open"

    def test_returns_items_and_count_without_notes(self, tool_env):
        by_name, service = tool_env
        service.list_items = MagicMock(
            return_value={
                "items": [_item()],
                "count": 1,
                "has_more": False,
                "applied_limit": 50,
            }
        )
        result = by_name["board_list"].invoke({})
        assert result["count"] == 1
        # The tool item shape is UNCHANGED by the batched hydration —
        # board_list still returns no notes[].
        assert "notes" not in result["items"][0]

    def test_service_error_becomes_an_error_string(self, tool_env):
        by_name, service = tool_env
        service.list_items = MagicMock(
            side_effect=BoardValidationError("status must be one of: ...")
        )
        result = by_name["board_list"].invoke({"status": "bogus"})
        assert result == "ERROR: status must be one of: ..."


class TestBoardListPagination:
    """``_pagination`` is composed TRUTHFULLY from the service result.

    Review F1: the service page is ALREADY SQL-limited, so the old
    generic truncation pass could never fire. The tool now shapes the
    service's additive pagination keys into an always-present,
    truthful ``_pagination`` — and a limit beyond the 200 ceiling
    leaves a VISIBLE clamp marker in the payload instead of silently
    shrunken pages.
    """

    def test_pagination_is_always_present_and_truthful(self, tool_env):
        by_name, service = tool_env
        service.list_items = MagicMock(
            return_value={
                "items": [_item(), _item(id="wi-11111111")],
                "count": 2,
                "has_more": True,
                "applied_limit": 2,
            }
        )
        result = by_name["board_list"].invoke({"limit": 2})
        pagination = result["_pagination"]
        assert pagination["has_more"] is True
        assert pagination["returned"] == 2
        assert pagination["applied_limit"] == 2
        assert result["count"] == 2

    def test_a_clamped_limit_leaves_a_visible_marker(self, tool_env):
        by_name, service = tool_env
        service.list_items = MagicMock(
            return_value={
                "items": [],
                "count": 0,
                "has_more": False,
                "applied_limit": 200,
            }
        )
        result = by_name["board_list"].invoke({"limit": 500})
        pagination = result["_pagination"]
        assert pagination["applied_limit"] == 200
        assert pagination["clamped"] is True
        assert pagination["requested_limit"] == 500

    def test_an_in_bounds_limit_carries_no_clamp_marker(self, tool_env):
        by_name, _service = tool_env
        result = by_name["board_list"].invoke({"limit": 50})
        pagination = result["_pagination"]
        assert pagination["applied_limit"] == 50
        assert "clamped" not in pagination
        assert "requested_limit" not in pagination

    def test_no_offset_hint_is_ever_emitted(self, tool_env):
        """board_list has no offset cursor — the hint must not lie."""
        import json as _json

        by_name, service = tool_env
        service.list_items = MagicMock(
            return_value={
                "items": [_item()],
                "count": 1,
                "has_more": True,
                "applied_limit": 1,
            }
        )
        result = by_name["board_list"].invoke({"limit": 1})
        assert "offset" not in _json.dumps(result["_pagination"]).lower()


# ── board_get ────────────────────────────────────────────────────────────


class TestBoardGet:
    def test_returns_the_item_with_notes(self, tool_env):
        by_name, service = tool_env
        service.get_item = MagicMock(
            return_value=_item(
                notes=[{"ts": "2026-10-20T01:00:00.000000+00:00",
                        "author_instance": "i", "text": "n"}]
            )
        )
        result = by_name["board_get"].invoke({"id": "wi-abcdef12"})
        assert set(result) == ITEM_FIELDS | {"notes"}
        assert result["notes"][0]["text"] == "n"
        service.get_item.assert_called_once_with(item_id="wi-abcdef12")

    def test_not_found_envelope(self, tool_env):
        by_name, service = tool_env
        service.get_item = MagicMock(
            side_effect=BoardItemNotFound("board item not found: wi-abcdef12")
        )
        result = by_name["board_get"].invoke({"id": "wi-abcdef12"})
        assert result == "ERROR: board item not found: wi-abcdef12"


# ── the body gate ────────────────────────────────────────────────────────


class TestBodyGate:
    def test_the_authorized_set_is_ari_only(self):
        """ARI-ONLY (user ruling 2026-10-20) — leader is NOT authorized."""
        assert BOARD_AUTHORIZED_AGENTS == {"ari"}
        assert _is_board_authorized("ari")
        assert not _is_board_authorized("leader")
        assert not _is_board_authorized("worker")
        assert not _is_board_authorized("watcher")
        assert not _is_board_authorized("")

    @pytest.mark.parametrize(
        "agent_id", ["worker", "watcher", "tester", "designer", ""]
    )
    def test_unauthorized_agent_is_refused_without_reaching_the_service(
        self, agent_id
    ):
        service = MagicMock()
        tools = {
            t.name: t
            for t in create_board_tools(
                service, current_instance_id="inst", agent_id=agent_id
            )
        }
        calls = [
            tools["board_create"].invoke({"title": "x"}),
            tools["board_update"].invoke({"id": "wi-abcdef12"}),
            tools["board_list"].invoke({}),
            tools["board_get"].invoke({"id": "wi-abcdef12"}),
        ]
        for result in calls:
            assert isinstance(result, str)
            assert result.startswith("ERROR: ")
        # The service was NEVER reached.
        service.create_item.assert_not_called()
        service.update_item.assert_not_called()
        service.list_items.assert_not_called()
        service.get_item.assert_not_called()

    def test_leader_is_not_authorized(self):
        """ARI-ONLY (user ruling 2026-10-20) — leader is DENIED and the
        service is NEVER reached (zero side effects)."""
        service = MagicMock()
        service.list_items = MagicMock(
            return_value={
                "items": [],
                "count": 0,
                "has_more": False,
                "applied_limit": 50,
            }
        )
        tools = {
            t.name: t
            for t in create_board_tools(
                service, current_instance_id="inst", agent_id="leader"
            )
        }
        result = tools["board_list"].invoke({})
        assert isinstance(result, str), result
        assert result.startswith("ERROR:"), result
        service.list_items.assert_not_called()


# ── tools never raise ────────────────────────────────────────────────────


class TestToolsNeverRaise:
    @pytest.mark.parametrize(
        "tool_name,args",
        [
            ("board_create", {"title": "x"}),
            ("board_update", {"id": "wi-abcdef12"}),
            ("board_list", {}),
            ("board_get", {"id": "wi-abcdef12"}),
        ],
    )
    def test_unexpected_exception_is_swallowed(self, tool_name, args):
        service = MagicMock()
        for method in (
            "create_item",
            "update_item",
            "list_items",
            "get_item",
        ):
            getattr(service, method).side_effect = RuntimeError(
                "secret internals: /etc/passwd and a token"
            )
        tools = {
            t.name: t
            for t in create_board_tools(
                service, current_instance_id="inst", agent_id="ari"
            )
        }
        result = tools[tool_name].invoke(args)
        assert result == "ERROR: board operation failed"
        # The PB-F1 leak class: no exception text in the payload.
        assert "secret internals" not in result
        assert "Traceback" not in result
        assert "passwd" not in result
