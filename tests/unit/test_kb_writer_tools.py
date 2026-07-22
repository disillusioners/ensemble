"""Regression tests for the kb-writer agent's resolved tool set.

Ensures kb-writer receives only the narrowly-scoped allow-list declared in
`agents/kb-writer/meta.json`: `rag_insert_text`, `tool_help`, and `time`.
Any future widening of the tool set (e.g., adding the full `rag` category,
or replacing the literal `tool_help` with the `help` category) will be
caught here.

Uses the same `resolve_tool_filter` mechanism that the loader uses at
runtime (see `daemon/tools/instance.py:resolve_tool_filter`), with a
representative `TOOL_CATEGORIES` fixture that mirrors what the live tool
registry would expose for the modules relevant to this agent.
"""

import json
from pathlib import Path

import pytest


# Path constants
KB_WRITER_AGENT_DIR = Path(__file__).parent.parent.parent / "agents" / "kb-writer"
META_PATH = KB_WRITER_AGENT_DIR / "meta.json"

# Representative subset of the tool registry. Includes only the categories
# relevant to kb-writer's resolution path; the other categories (bash,
# filesystem, instance, etc.) intentionally absent to keep this test focused
# and avoid coupling to unrelated registry growth.
TOOL_CATEGORIES: dict[str, list[str]] = {
    # The `rag` category currently exposes ~16 tools (see rag_tools.py).
    # kb-writer is only granted `rag_insert_text` by literal name, so
    # none of these should ever end up in its resolved set.
    "rag": [
        "rag_insert_text",
        "rag_insert_texts",
        "rag_query",
        "rag_query_data",
        "rag_search_labels",
        "rag_get_graph",
        "rag_create_entity",
        "rag_get_entity",
        "rag_create_relation",
        "rag_update_entity",
        "rag_merge_entities",
        "rag_delete_entity",
        "rag_delete_relation",
        "rag_delete_docs",
        "rag_list_docs",
        "rag_track_status",
    ],
    # The `help` category exposes only `tool_help`. If a future maintainer
    # changes meta.json back to `"help"` (category) instead of
    # `"tool_help"` (literal), category expansion would still resolve to
    # just `tool_help` — but the resolved-set identity is the same, so
    # the regression we actually want to guard against is widening of
    # this category, not its name. We include multiple tools here so a
    # future category widening would be detected.
    "help": ["tool_help", "tool_help_extra_for_regression_check"],
    "time": ["time"],
}


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def kb_writer_meta() -> dict:
    """Load the kb-writer meta.json as a dict."""
    with open(META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def kb_writer_resolved_tools() -> set[str]:
    """Resolve kb-writer's meta.json allow-list through `resolve_tool_filter`.

    Mirrors how the loader resolves tools at runtime — categories expand
    to their member tools, while literal names pass through unchanged.
    """
    from daemon.tools.instance import resolve_tool_filter

    with open(META_PATH, "r", encoding="utf-8") as f:
        meta = json.load(f)

    allow = (meta.get("tools") or {}).get("allow") or []
    deny = (meta.get("tools") or {}).get("deny")

    resolved = resolve_tool_filter(
        allow=allow,
        deny=deny,
        tool_categories=TOOL_CATEGORIES,
    )
    assert resolved is not None, "kb-writer must have a resolved allow-list"
    return resolved


# =============================================================================
# meta.json shape
# =============================================================================


class TestKBWriterMetaJson:
    """Light meta.json sanity checks for kb-writer."""

    def test_meta_json_exists(self) -> None:
        assert META_PATH.exists(), f"missing {META_PATH}"

    def test_meta_json_loads(self, kb_writer_meta: dict) -> None:
        assert isinstance(kb_writer_meta, dict)
        assert kb_writer_meta.get("id") == "kb-writer"

    def test_tools_allow_is_list(self, kb_writer_meta: dict) -> None:
        allow = (kb_writer_meta.get("tools") or {}).get("allow")
        assert isinstance(allow, list)
        assert len(allow) > 0


# =============================================================================
# Resolved tool set
# =============================================================================


class TestKBWriterResolvedToolSet:
    """Verify kb-writer's resolved tool set matches its narrow contract."""

    def test_resolved_includes_rag_insert_text(
        self, kb_writer_resolved_tools: set[str]
    ) -> None:
        """`rag_insert_text` is the only knowledge-base tool kb-writer gets."""
        assert "rag_insert_text" in kb_writer_resolved_tools

    def test_resolved_includes_tool_help(
        self, kb_writer_resolved_tools: set[str]
    ) -> None:
        """The literal `tool_help` utility is resolved."""
        assert "tool_help" in kb_writer_resolved_tools

    def test_resolved_includes_time(
        self, kb_writer_resolved_tools: set[str]
    ) -> None:
        """The `time` utility is resolved."""
        assert "time" in kb_writer_resolved_tools

    @pytest.mark.parametrize(
        "forbidden_tool",
        [
            "rag_query",
            "rag_query_data",
            "rag_create_entity",
            "rag_create_relation",
            "rag_update_entity",
            "rag_merge_entities",
            "rag_delete_entity",
            "rag_delete_relation",
            "rag_search_labels",
            "rag_get_graph",
            "rag_get_entity",
            "rag_delete_docs",
            "rag_list_docs",
            "rag_insert_texts",
            "rag_track_status",
        ],
    )
    def test_resolved_excludes_rag_graph_tools(
        self, kb_writer_resolved_tools: set[str], forbidden_tool: str
    ) -> None:
        """No RAG graph/retrieval tool may leak into kb-writer's allow-set.

        kb-writer is write-only. Granting any of these would silently widen
        its abilities beyond the documented contract.
        """
        assert forbidden_tool not in kb_writer_resolved_tools, (
            f"{forbidden_tool} must NOT be in kb-writer's resolved tool set; "
            f"got {sorted(kb_writer_resolved_tools)}"
        )

    def test_resolved_does_not_use_help_category_globally(
        self, kb_writer_resolved_tools: set[str]
    ) -> None:
        """Guard against future widening of the `help` category.

        Our fixture lists a second `tool_help_extra_for_regression_check`
        inside the `help` category. If meta.json ever reverts to the
        literal category name `"help"` instead of the tool name
        `"tool_help"`, category expansion would drag in the extra tool
        and this assertion would fail.
        """
        assert "tool_help_extra_for_regression_check" not in kb_writer_resolved_tools, (
            "meta.json likely uses the `help` category instead of the "
            "literal `tool_help` tool name — category expansion pulled "
            f"in extra tools: {sorted(kb_writer_resolved_tools)}"
        )

    def test_resolved_set_is_exactly_expected(
        self, kb_writer_resolved_tools: set[str]
    ) -> None:
        """The full resolved set is exactly the three tools granted.

        Locks in the contract: any future change to kb-writer's
        allow-list must also update this test deliberately.
        """
        assert kb_writer_resolved_tools == {"rag_insert_text", "tool_help", "time"}
