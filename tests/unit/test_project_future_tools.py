"""Unit tests for project future tools: add, list, remove.

Tests tool logic (validation, truncation, error handling, cap merge) with a
mocked store — the house idiom of test_project_history_tools.py.
"""

from unittest.mock import MagicMock
import pytest

from daemon.tools.project_future import (
    create_project_future_tools,
    _MAX_ENTRIES,
    _MAX_SUMMARY_LEN,
    _MAX_DETAILS_LEN,
)


# =============================================================================
# Helpers
# =============================================================================


def make_mock_store(initial_entries: list | None = None):
    """Create an isolated mock store for the wishlist lane.

    Returns a MagicMock store whose ``get`` returns a truthy project and
    whose add/list/remove methods behave like the real repository (dicts in,
    dicts out).
    """
    store = MagicMock()

    project = MagicMock()
    project.project_id = "test-project-id"
    project.name = "Test Project"
    store.get.return_value = project

    entries = initial_entries if initial_entries is not None else []
    entry_id_counter = [100]

    def add_future_entry_handler(
        project_id,
        summary,
        details=None,
        source_agent="",
        source_instance_id="",
        *,
        max_entries,
    ):
        entry_id_counter[0] += 1
        entry = {
            "id": f"future-{entry_id_counter[0]}",
            "project_id": project_id,
            "summary": summary,
            "details": details,
            "source_agent": source_agent,
            "source_instance_id": source_instance_id,
            "created_at": "2026-11-12T00:00:00",
            "seq": entry_id_counter[0],
            "evicted": [],
        }
        entries.append(entry)
        return entry

    def list_future_entries_handler(project_id, limit=20, offset=0):
        filtered = [e for e in entries if e["project_id"] == project_id]
        total = len(filtered)
        filtered = sorted(filtered, key=lambda x: x["seq"], reverse=True)
        return {
            "entries": filtered[offset : offset + limit],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def remove_future_entry_handler(entry_id, project_id=None):
        for i, e in enumerate(entries):
            if e["id"] == entry_id:
                if project_id is not None and e["project_id"] != project_id:
                    return None
                return entries.pop(i)
        return None

    store.add_future_entry.side_effect = add_future_entry_handler
    store.list_future_entries.side_effect = list_future_entries_handler
    store.remove_future_entry.side_effect = remove_future_entry_handler
    return store


@pytest.fixture
def store():
    return make_mock_store()


@pytest.fixture
def tools(store):
    """Factory called WITHOUT manager — defaults apply (deliberately unused)."""
    return create_project_future_tools(store, "inst-123", "coder-agent")


# =============================================================================
# AC8 — cap constant
# =============================================================================


class TestCapConstant:
    def test_max_entries_is_32(self):
        """AC8: _MAX_ENTRIES importable at module level and equals 32."""
        assert _MAX_ENTRIES == 32

    def test_length_caps_match_sibling(self):
        assert _MAX_SUMMARY_LEN == 300
        assert _MAX_DETAILS_LEN == 5000


# =============================================================================
# AC9 — factory shape
# =============================================================================


class TestFactoryShape:
    def test_factory_returns_exactly_three_tools(self, tools):
        """AC9: exactly the 3 wishlist tools, correct order."""
        names = [t.name for t in tools]
        assert names == [
            "project_future_add",
            "project_future_list",
            "project_future_remove",
        ]

    def test_each_tool_carries_category_and_full_doc(self, tools):
        """AC9: _tool_category == 'project_future' and _full_doc_ attached."""
        for t in tools:
            assert t._tool_category == "project_future"
            assert isinstance(t._full_doc_, str) and t._full_doc_


# =============================================================================
# AC1 — add happy path
# =============================================================================


class TestAdd:
    def test_add_happy_path_stamps_provenance(self, tools, store):
        """AC1: flat dict with seq, evicted == [], provenance stamped, no error key."""
        result = tools[0].invoke(
            {"project_id": "test-project-id", "summary": "Add a streaming retry path"}
        )
        assert "error" not in result
        assert result["seq"] == 101
        assert result["evicted"] == []
        assert result["source_agent"] == "coder-agent"
        assert result["source_instance_id"] == "inst-123"
        # store received the closure-stamped provenance + the REQUIRED keyword cap
        store.add_future_entry.assert_called_with(
            "test-project-id",
            "Add a streaming retry path",
            None,
            "coder-agent",
            "inst-123",
            max_entries=32,
        )

    def test_add_strips_whitespace_summary(self, tools, store):
        """W4 step 2: summary is stripped before validation/truncation."""
        tools[0].invoke(
            {"project_id": "test-project-id", "summary": "  padded idea  "}
        )
        assert store.add_future_entry.call_args[0][1] == "padded idea"

    def test_add_empty_summary_rejected(self, tools):
        """AC2: whitespace-only summary → Summary cannot be empty."""
        result = tools[0].invoke(
            {"project_id": "test-project-id", "summary": "   "}
        )
        assert result == {"error": "Summary cannot be empty"}

    def test_add_truncates_summary_and_details(self, tools, store):
        """AC3: truncate-not-reject at the tool layer (400→300, 6000→5000)."""
        tools[0].invoke(
            {
                "project_id": "test-project-id",
                "summary": "x" * 400,
                "details": "d" * 6000,
            }
        )
        args = store.add_future_entry.call_args[0]
        assert len(args[1]) == 300
        assert len(args[2]) == 5000

    def test_add_unknown_project(self, tools, store):
        """AC4 (add leg): unknown project → not-found error."""
        store.get.return_value = None
        result = tools[0].invoke({"project_id": "nope", "summary": "idea"})
        assert result == {"error": "Project 'nope' not found"}

    def test_add_eviction_disclosed_verbatim(self, store):
        """AC5: the store's evicted payload passes through unchanged."""
        evicted_item = {
            "id": "future-1",
            "summary": "oldest idea",
            "seq": 1,
            "created_at": "2026-11-12T00:00:00",
        }
        store.add_future_entry.side_effect = None
        store.add_future_entry.return_value = {
            "id": "future-99",
            "summary": "newest idea",
            "seq": 33,
            "evicted": [evicted_item],
        }
        tools = create_project_future_tools(store, "", "")
        result = tools[0].invoke(
            {"project_id": "test-project-id", "summary": "newest idea"}
        )
        assert result["evicted"] == [evicted_item]

    def test_add_concurrent_update_translated(self, store):
        """D16: ConcurrentWishlistUpdate → the stable retry message, no dialect text."""
        from daemon.repositories.project.repository import ConcurrentWishlistUpdate

        store.add_future_entry.side_effect = ConcurrentWishlistUpdate("p")
        tools = create_project_future_tools(store, "", "")
        result = tools[0].invoke(
            {"project_id": "test-project-id", "summary": "idea"}
        )
        assert result == {
            "error": "Concurrent wishlist update for project 'test-project-id' — please retry"
        }
        assert "UNIQUE" not in result["error"]

    def test_add_generic_exception_backstop(self, store):
        """W4 step 5: unexpected store failure → {'error': str} (single-key contract)."""
        store.add_future_entry.side_effect = RuntimeError("boom")
        tools = create_project_future_tools(store, "", "")
        result = tools[0].invoke(
            {"project_id": "test-project-id", "summary": "idea"}
        )
        assert result == {"error": "boom"}


# =============================================================================
# AC6 — list clamps + cap merge
# =============================================================================


class TestList:
    def test_list_clamps_limit_and_offset(self, tools, store):
        """AC6: limit 500→100, 0→1; negative offset→0; envelope verbatim + cap."""
        result = tools[1].invoke(
            {"project_id": "test-project-id", "limit": 500, "offset": -5}
        )
        assert store.list_future_entries.call_args == (("test-project-id", 100, 0),)
        assert result["limit"] == 100
        assert result["offset"] == 0

    def test_list_limit_zero_becomes_one(self, tools, store):
        store.list_future_entries.side_effect = None
        store.list_future_entries.return_value = {
            "entries": [], "total": 0, "limit": 1, "offset": 0,
        }
        result = tools[1].invoke({"project_id": "test-project-id", "limit": 0})
        assert store.list_future_entries.call_args == (("test-project-id", 1, 0),)
        assert result["limit"] == 1

    def test_list_merges_cap_key(self, tools):
        """AC6/O12a: the tool merges cap == 32 into the envelope."""
        result = tools[1].invoke({"project_id": "test-project-id"})
        assert result["cap"] == _MAX_ENTRIES == 32

    def test_list_cap_reads_patched_module_attribute(self, tools, store, monkeypatch):
        """S1: cap reads the (possibly patched) module attribute — no literal drift."""
        import daemon.tools.project_future as mod

        sentinel = 9999
        monkeypatch.setattr(mod, "_MAX_ENTRIES", sentinel)
        result = tools[1].invoke({"project_id": "test-project-id"})
        assert result["cap"] is sentinel

    def test_list_unknown_project(self, tools, store):
        """AC4 (list leg)."""
        store.get.return_value = None
        result = tools[1].invoke({"project_id": "nope"})
        assert result == {"error": "Project 'nope' not found"}


# =============================================================================
# AC7 — remove
# =============================================================================


class TestRemove:
    def test_remove_success_returns_removed_entry(self, tools, store):
        """AC7: success → {'success': True, 'removed': {...}} (D11 — item identified)."""
        seeded = {
            "id": "future-1",
            "project_id": "test-project-id",
            "summary": "obsolete idea",
            "seq": 1,
        }
        store.remove_future_entry.side_effect = None
        store.remove_future_entry.return_value = seeded
        result = tools[2].invoke(
            {"project_id": "test-project-id", "entry_id": "future-1"}
        )
        assert result == {"success": True, "removed": seeded}
        store.remove_future_entry.assert_called_with("future-1", "test-project-id")

    def test_remove_unknown_entry_error_wording(self, tools, store):
        """AC7: repo None → the :300 error wording, entry_id arg name."""
        store.remove_future_entry.side_effect = None
        store.remove_future_entry.return_value = None
        result = tools[2].invoke(
            {"project_id": "test-project-id", "entry_id": "missing-id"}
        )
        assert result == {
            "error": "Entry 'missing-id' not found in project 'test-project-id'"
        }

    def test_remove_unknown_project(self, tools, store):
        """AC4 (remove leg)."""
        store.get.return_value = None
        result = tools[2].invoke(
            {"project_id": "nope", "entry_id": "future-1"}
        )
        assert result == {"error": "Project 'nope' not found"}


# =============================================================================
# W6d — per-tool _FULL_DOCS contract components
# =============================================================================


class TestFullDocContract:
    @pytest.mark.parametrize("tool_name", [
        "project_future_add",
        "project_future_list",
        "project_future_remove",
    ])
    def test_full_doc_carries_contract_components(self, tools, tool_name):
        """W6d: EVERY tool's _full_doc_ states the contract — cap token, an
        eviction token, and a purpose phrase. A future edit that drops one names
        the offending tool."""
        tool = next(t for t in tools if t.name == tool_name)
        doc = tool._full_doc_
        assert "32" in doc, f"{tool_name}: missing cap token '32'"
        assert ("evict" in doc.lower()) or ("fifo" in doc.lower()), (
            f"{tool_name}: missing eviction token ('evict'/'FIFO')"
        )
        assert (
            ("critical notes" in doc.lower())
            or ("wishlist" in doc.lower())
            or ("future-improvement" in doc.lower())
        ), f"{tool_name}: missing purpose phrase ('critical notes'/'wishlist'/'future-improvement')"