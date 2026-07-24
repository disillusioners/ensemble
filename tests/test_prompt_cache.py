"""Backward-compatibility tests for PromptCache._make_key (D15 versioning).

The _make_key signature after Phase 1 is::

    _make_key(agent_id, mcp_tool_names, version_tag=None)

These tests guard the compatibility contract:
- When ``version_tag`` is None/empty, the key MUST be identical to the
  pre-D15 format ``f"{agent_id}::{normalized_mcp}"`` so legacy cached
  entries are still hit.
- When ``version_tag`` is set, the key MUST include the bracket suffix
  so base and tagged variants do not collide.
"""

from daemon.loader import PromptCache


class TestPromptCacheMakeKeyBackwardCompat:
    """Regression tests for the pre-D15 key format."""

    def test_make_key_no_mcp_no_tag(self) -> None:
        """Backwards-compat: no mcp, no tag → exactly 'agent_id::'."""
        cache = PromptCache()
        assert cache._make_key("dev", None, None) == "dev::"

    def test_make_key_no_tag_explicit_none(self) -> None:
        """Explicit None version_tag must match the legacy key format."""
        cache = PromptCache()
        assert cache._make_key("dev", None, None) == "dev::"

    def test_make_key_with_tag(self) -> None:
        """Tagged variant produces a distinct, bracketed key."""
        cache = PromptCache()
        assert cache._make_key("dev", None, "v2") == "dev[v2]::"

    def test_make_key_empty_string_tag_treated_as_no_tag(self) -> None:
        """Empty string version_tag is falsy and must be omitted from the key."""
        cache = PromptCache()
        # Empty tag → legacy format (no bracket suffix) for backward compat.
        assert cache._make_key("dev", None, "") == "dev::"

    def test_make_key_base_and_tag_produce_distinct_keys(self) -> None:
        """Base and tagged variants must never collide (D15 keystone)."""
        cache = PromptCache()
        base_key = cache._make_key("developer", None, None)
        tagged_key = cache._make_key("developer", None, "v2")
        assert base_key != tagged_key
        assert base_key == "developer::"
        assert tagged_key == "developer[v2]::"

    def test_make_key_mcp_tools_empty_when_none(self) -> None:
        """None mcp_tool_names yields an empty normalized suffix."""
        cache = PromptCache()
        assert cache._make_key("agent", None, None) == "agent::"

    def test_make_key_mcp_tools_sorted(self) -> None:
        """MCP tool names are sorted before joining for deterministic keys."""
        cache = PromptCache()
        # Sorted representation: ["a", "b", "c"] → "a,b,c"
        assert cache._make_key("agent", ["c", "a", "b"], None) == "agent::a,b,c"

    def test_make_key_mcp_tools_empty_list_treated_as_none(self) -> None:
        """Empty list version_tag is falsy and matches None behavior."""
        cache = PromptCache()
        assert cache._make_key("agent", [], None) == "agent::"

    def test_make_key_full_combo(self) -> None:
        """Full combination: agent_id, mcp tools, and version_tag."""
        cache = PromptCache()
        # Sorted MCP tools + version_tag suffix both present.
        assert cache._make_key(
            "developer", ["zw", "ab", "mn"], "v2"
        ) == "developer[v2]::ab,mn,zw"

    def test_make_key_round_trip_via_get(self) -> None:
        """Keys produced by _make_key are usable via get() with the same args."""
        cache = PromptCache()
        # Seed a tagged version.
        cache.set(
            "developer", "tagged prompt", 42, {"skill.md": 1.0},
            mcp_tool_names=None, version_tag="v2",
        )
        key = cache._make_key("developer", None, "v2")
        assert key in cache._cache
        # Lookup should round-trip.
        hit = cache.get("developer", None, "v2")
        assert hit is not None
        assert hit[0] == "tagged prompt"
        assert hit[1] == 42

    def test_make_key_invalidate_uses_same_key(self) -> None:
        """invalidate() computes the same key as _make_key()/get()."""
        cache = PromptCache()
        cache.set("dev", "p", 1, {}, mcp_tool_names=None, version_tag="v3")
        assert cache.get("dev", None, "v3") is not None
        cache.invalidate("dev", None, "v3")
        assert cache.get("dev", None, "v3") is None
