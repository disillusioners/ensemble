"""Tests for critical notes tools: add, list, remove, collision-rejection, cap, update-by-id.

Test scope (post-fix, 2026-09-10):
- Distinct-note adds still work (regression guard).
- Near-duplicate adds are REJECTED with a clear error naming the
  colliding entry's id and summary (no silent fuzzy-upsert).
- The three observed hijack shapes (token-overlap on generic vocabulary
  in same category) do not merge — distinct notes survive with their
  references intact.
- Cap behavior: at _MAX_ENTRIES, the tool REJECTS with eviction
  candidates named. No silent eviction.
- entry_id updates a specific row exactly (no fuzzy).
"""

import time
import pytest
from unittest.mock import MagicMock

from daemon.tools.critical_notes import (
    create_critical_notes_tools,
    _find_near_duplicate_entry,
    _normalize_summary,
    _MAX_ENTRIES,
    _MAX_SUMMARY_LEN,
)
from daemon.repositories.project.models import (
    CriticalNotes,
    CriticalNotesCategory,
    CriticalNotesPriority,
    CriticalNoteModel,
)


# =============================================================================
# Helper Functions
# =============================================================================


def create_entry(category: str, priority: str, summary: str, reference: str | None = None) -> CriticalNotes:
    """Create a CriticalNotes entry for testing."""
    return CriticalNotes(
        category=category,
        priority=priority,
        summary=summary,
        reference=reference,
        source_agent="test_agent",
    )


def make_mock_repo(initial_entries: list = None):
    """Create a properly isolated mock repository."""
    repo = MagicMock()
    project = MagicMock()
    repo.get.return_value = project

    # Create mutable storage for notes
    notes_storage = []

    # Initialize with any provided entries
    if initial_entries:
        for entry in initial_entries:
            if isinstance(entry, dict):
                notes_storage.append(CriticalNoteModel(**entry))
            elif isinstance(entry, CriticalNotes):
                notes_storage.append(CriticalNoteModel(**entry.to_dict()))
            elif isinstance(entry, CriticalNoteModel):
                notes_storage.append(entry)

    def list_critical_notes(pid):
        return list(notes_storage)

    def add_critical_note(pid, source_agent, category, priority, summary, reference=None):
        note = CriticalNoteModel(
            project_id=pid,
            source_agent=source_agent,
            category=category,
            priority=priority,
            summary=summary,
            reference=reference,
        )
        notes_storage.append(note)
        return note

    def update_critical_note(pid, entry_id, **updates):
        for note in notes_storage:
            if note.id == entry_id and note.project_id == pid:
                for key, value in updates.items():
                    if value is not None and hasattr(note, key):
                        setattr(note, key, value)
                return note
        return None

    def get_critical_note(pid, entry_id):
        for note in notes_storage:
            if note.id == entry_id and note.project_id == pid:
                return note
        return None

    def remove_critical_note(pid, entry_id):
        for i, note in enumerate(notes_storage):
            if note.id == entry_id and note.project_id == pid:
                notes_storage.pop(i)
                return True
        return False

    repo.list_critical_notes.side_effect = list_critical_notes
    repo.add_critical_note.side_effect = add_critical_note
    repo.update_critical_note.side_effect = update_critical_note
    repo.get_critical_note.side_effect = get_critical_note
    repo.remove_critical_note.side_effect = remove_critical_note

    return repo


def unique_summary(index: int) -> str:
    """Generate a summary with unique keywords to avoid collision.

    Uses completely different word sets so no two summaries normalize-equal.
    """
    # 150 unique words covering 50 entries x 3 words each.
    word_sets = [
        # 0-9: Fruits
        "apricot", "blueberry", "cherry", "dragonfruit", "elderberry",
        "fig", "grapefruit", "honeydew", "kiwi", "lemon",
        # 10-19: More fruits
        "mango", "nectarine", "orange", "papaya", "quince",
        "raspberry", "strawberry", "tangerine", "ugli", "vanilla",
        # 20-29: First animals
        "beaver", "camel", "dolphin", "eagle", "falcon",
        "giraffe", "hippo", "iguana", "jaguar", "kangaroo",
        # 30-39: More animals
        "lemur", "meerkat", "newt", "ocelot", "panda",
        "quail", "raven", "snake", "tiger", "urchin",
        # 40-49: Elements/forces
        "atom", "bridge", "castle", "delta", "ember",
        "flame", "glacier", "harbor", "island", "jewel",
        # 50-59: More elements
        "knight", "lagoon", "meadow", "nexus", "orbit",
        "portal", "quarry", "ridge", "summit", "temple",
        # 60-69: Objects/materials
        "vessel", "wharf", "xenon", "yard", "zephyr",
        "anchor", "beacon", "crown", "diamond", "emerald",
        # 70-79: More objects
        "forest", "garden", "haven", "ivory", "jungle",
        "kernel", "lantern", "marble", "nectar", "oasis",
        # 80-89: Final set (extra 60 entries below)
        "prism", "quartz", "river", "stone", "tower",
        "ultra", "vortex", "willow", "xylem", "yacht",
        # 90-99: More flavors/colors
        "amber", "bronze", "crimson", "denim", "ebony",
        "fuchsia", "golden", "hazel", "indigo", "jade",
        # 100-109: More textures/elements
        "krypton", "lilac", "magenta", "neon", "onyx",
        "pearl", "quartzite", "ruby", "sapphire", "topaz",
        # 110-119: More places
        "arctic", "beach", "canyon", "desert", "estuary",
        "fjord", "grove", "hilltop", "iceberg", "jungle2",
        # 120-129: More weather/phenomena
        "aurora", "blizzard", "cyclone", "drizzle", "eclipse",
        "frost", "gale", "hailstorm", "mistral", "northern",
        # 130-139: Music/sound
        "alto", "baritone", "chord", "duet", "encore",
        "fugue", "glissando", "harmony", "interlude", "jingle",
        # 140-149: Final extra
        "keystone", "lattice", "mosaic", "nimbus", "obelisk",
        "palisade", "quill", "rampart", "spire", "turret",
    ]
    base = index * 3
    return f"{word_sets[base]} {word_sets[base + 1]} {word_sets[base + 2]}"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_repo():
    """Mock repository with a project that has empty critical_notes."""
    return make_mock_repo()


@pytest.fixture
def cn_tools(mock_repo):
    """Create critical notes tools with mock repository."""
    return create_critical_notes_tools(mock_repo, agent_id="test_agent")


@pytest.fixture
def add_tool(cn_tools):
    """Get the project_cn_add tool."""
    for tool in cn_tools:
        if tool.name == "project_cn_add":
            return tool
    raise ValueError("project_cn_add tool not found")


@pytest.fixture
def list_tool(cn_tools):
    """Get the project_cn_list tool."""
    for tool in cn_tools:
        if tool.name == "project_cn_list":
            return tool
    raise ValueError("project_cn_list tool not found")


@pytest.fixture
def remove_tool(cn_tools):
    """Get the project_cn_remove tool."""
    for tool in cn_tools:
        if tool.name == "project_cn_remove":
            return tool
    raise ValueError("project_cn_remove tool not found")


# =============================================================================
# Test Class: TestProjectCNAdd
# =============================================================================


class TestProjectCNAdd:
    """Tests for the project_cn_add tool."""

    def test_add_to_empty_project(self, add_tool, mock_repo):
        """Add entry to project with empty critical_notes -> succeeds, returns dict with all fields."""
        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": "Use async/await for all database operations",
        })

        assert "id" in result
        assert "created_at" in result
        assert "updated_at" in result
        assert result["source_agent"] == "test_agent"
        assert result["category"] == "convention"
        assert result["priority"] == "high"
        assert result["summary"] == "Use async/await for all database operations"
        assert result["reference"] is None

    def test_add_all_categories(self, mock_repo):
        """Add entry with each of the 5 categories -> each valid."""
        categories = ["convention", "pattern", "risk", "decision", "constraint"]
        for category in categories:
            repo = make_mock_repo()
            tools = create_critical_notes_tools(repo, agent_id="test_agent")
            add_tool = next(t for t in tools if t.name == "project_cn_add")
            result = add_tool.invoke({
                "project_id": "test_project",
                "category": category,
                "priority": "medium",
                "summary": f"Test {category}",
            })
            assert "error" not in result
            assert result["category"] == category

    def test_add_all_priorities(self, mock_repo):
        """Add entry with each priority (critical, high, medium) -> each valid."""
        priorities = ["critical", "high", "medium"]
        for priority in priorities:
            repo = make_mock_repo()
            tools = create_critical_notes_tools(repo, agent_id="test_agent")
            add_tool = next(t for t in tools if t.name == "project_cn_add")
            result = add_tool.invoke({
                "project_id": "test_project",
                "category": "convention",
                "priority": priority,
                "summary": f"Test {priority} priority",
            })
            assert "error" not in result
            assert result["priority"] == priority

    def test_add_summary_too_long(self, add_tool, mock_repo):
        """Summary > 200 chars -> error dict with message."""
        long_summary = "A" * 201
        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": long_summary,
        })

        assert "error" in result
        assert f"Summary must be <= {_MAX_SUMMARY_LEN} chars" in result["error"]
        assert "201" in result["error"]

    def test_add_invalid_category(self, add_tool, mock_repo):
        """Invalid category -> error dict."""
        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "unknown",
            "priority": "high",
            "summary": "Test summary",
        })

        assert "error" in result
        assert "Invalid category 'unknown'" in result["error"]

    def test_add_invalid_priority(self, add_tool, mock_repo):
        """Invalid priority -> error dict."""
        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "unknown",
            "summary": "Test summary",
        })

        assert "error" in result
        assert "Invalid priority 'unknown'" in result["error"]

    def test_add_with_reference(self, add_tool, mock_repo):
        """Add with optional reference -> reference stored in returned dict."""
        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "pattern",
            "priority": "high",
            "summary": "Pattern observed in API calls",
            "reference": "https://docs.example.com/api-patterns",
        })

        assert "error" not in result
        assert result["reference"] == "https://docs.example.com/api-patterns"

    def test_add_without_reference(self, add_tool, mock_repo):
        """Add without reference -> reference is None in returned dict."""
        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "medium",
            "summary": "Some convention we follow",
        })

        assert "error" not in result
        assert result["reference"] is None

    def test_add_empty_summary(self, add_tool, mock_repo):
        """Empty/whitespace summary -> error dict."""
        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": "   ",
        })

        assert "error" in result
        assert "Summary cannot be empty" in result["error"]

    def test_add_project_not_found(self, add_tool, mock_repo):
        """Repo.get returns None -> error dict."""
        mock_repo.get.return_value = None

        result = add_tool.invoke({
            "project_id": "nonexistent",
            "category": "convention",
            "priority": "high",
            "summary": "Test summary",
        })

        assert "error" in result
        assert "Project 'nonexistent' not found" in result["error"]


# =============================================================================
# Test Class: TestStrictMatching — replaces TestMergeLogic
# =============================================================================


class TestStrictMatching:
    """Strict collision detection: only normalized-equal summaries collide.

    Replaces the old TestMergeLogic. The fuzzy-upsert / merge code paths
    are gone; near-duplicates REJECT with an error naming the colliding
    entry's id and summary.
    """

    def test_distinct_summaries_both_persist(self, mock_repo):
        """Two clearly distinct summaries -> both entries exist, no merge."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        # Two distinct summaries with overlapping keywords (would have merged
        # under the OLD token-overlap matcher).
        add_tool.invoke({
            "project_id": "test_project",
            "category": "pattern",
            "priority": "high",
            "summary": "Always use dependency injection for better testing",
        })
        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "pattern",
            "priority": "high",
            "summary": "Dependency injection helps with unit testing",
        })

        assert "error" not in result, f"Distinct add unexpectedly rejected: {result}"
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 2

    def test_strict_collision_rejected(self, mock_repo):
        """Add A, then add A (exact text) -> second add REJECTED with id+summary."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        summary = "Always use dependency injection for better testing"
        r1 = add_tool.invoke({
            "project_id": "test_project",
            "category": "pattern",
            "priority": "high",
            "summary": summary,
        })
        assert "error" not in r1
        original_id = r1["id"]

        # Same summary text -> REJECTED
        r2 = add_tool.invoke({
            "project_id": "test_project",
            "category": "pattern",
            "priority": "high",
            "summary": summary,
        })

        assert "error" in r2
        assert "Near-duplicate critical note already exists" in r2["error"]
        assert f"id={original_id}" in r2["error"]
        assert summary in r2["error"]
        # Caller content must NEVER be silently dropped:
        # the existing row is unchanged and only 1 entry exists.
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 1
        assert list_result["entries"][0]["id"] == original_id
        assert list_result["entries"][0]["summary"] == summary

    def test_collision_rejected_case_insensitive(self, mock_repo):
        """Add A, then add A with different case -> REJECTED (normalized equality)."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        r1 = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": "Use PostgreSQL for the main database",
        })
        original_id = r1["id"]

        r2 = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": "USE postgresql FOR the main DATABASE",
        })

        assert "error" in r2
        assert "Near-duplicate" in r2["error"]
        assert f"id={original_id}" in r2["error"]
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 1

    def test_collision_rejected_whitespace_only(self, mock_repo):
        """Add A, then add A with extra/missing whitespace -> REJECTED (normalized equality)."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        r1 = add_tool.invoke({
            "project_id": "test_project",
            "category": "decision",
            "priority": "medium",
            "summary": "PostgreSQL is our chosen database solution",
        })
        original_id = r1["id"]

        # Extra whitespace / different spacing
        r2 = add_tool.invoke({
            "project_id": "test_project",
            "category": "decision",
            "priority": "medium",
            "summary": "  PostgreSQL   is  our chosen database solution  ",
        })

        assert "error" in r2
        assert "Near-duplicate" in r2["error"]
        assert f"id={original_id}" in r2["error"]
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 1

    def test_collision_across_categories_still_rejected(self, mock_repo):
        """Same summary in different categories -> still REJECTED (no category scoping).

        Defense-in-depth: even if a caller tries to add an identical summary
        under a different category, the strict matcher catches it.
        """
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")
        summary = "Reuse OpenAI-compatible LLM clients across projects"

        r1 = add_tool.invoke({
            "project_id": "test_project",
            "category": "pattern",
            "priority": "high",
            "summary": summary,
        })
        original_id = r1["id"]

        r2 = add_tool.invoke({
            "project_id": "test_project",
            "category": "decision",  # different category
            "priority": "high",
            "summary": summary,
        })

        assert "error" in r2
        assert "Near-duplicate" in r2["error"]
        assert f"id={original_id}" in r2["error"]
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 1

    def test_collision_error_names_summary_for_caller_decision(self, mock_repo):
        """Error message contains the colliding summary so caller can decide."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")

        summary = "Use repository pattern for all database access"
        add_tool.invoke({
            "project_id": "test_project",
            "category": "pattern",
            "priority": "high",
            "summary": summary,
        })

        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "pattern",
            "priority": "high",
            "summary": summary,
        })
        assert "error" in result
        # The summary must appear in the error so the caller can see what they collided with
        assert summary in result["error"]

    def test_collision_error_suggests_entry_id_for_update(self, mock_repo):
        """Error message tells caller how to update via entry_id."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")

        summary = "Pin httpx version to 0.28.1"
        r1 = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": summary,
        })
        original_id = r1["id"]

        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": summary,
        })
        assert "error" in result
        assert "entry_id" in result["error"]
        assert original_id in result["error"]

    def test_normalize_summary_helper(self):
        """_normalize_summary lowercases + collapses whitespace + strips."""
        assert _normalize_summary("Hello World") == "hello world"
        assert _normalize_summary("  Hello   World  ") == "hello world"
        assert _normalize_summary("HELLO\tworld\nfoo") == "hello world foo"
        assert _normalize_summary("Hello World") == _normalize_summary("hello world")
        # Different content stays different
        assert _normalize_summary("Hello World") != _normalize_summary("Hello Earth")


# =============================================================================
# Test Class: TestHijackPrevention — the three observed prod shapes
# =============================================================================


class TestHijackPrevention:
    """Regression tests for the 2026-09-10 critical-notes hijack incident.

    Three observed hijack shapes (same category 'risk', shared generic
    vocabulary like 'fixed', 'restart', 'merge', 'pending', 'obsolete',
    'ambient'): an unrelated note got its summary/reference overwritten
    by a new note's content.

    Under the strict matcher, all three distinct notes must SURVIVE
    untouched, and no reference may be silently overwritten.
    """

    def test_hijack_shape_1_toctou_does_not_land_on_unrelated_residuals(self, mock_repo):
        """A new TOCTOU-flavored 'risk' note must NOT merge into the unrelated
        'Open residuals' note just because they share generic vocabulary."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        # Existing unrelated note (matches the style of prod entries)
        residuals_summary = (
            "Open residuals: re-arm CAS window un-fired watcher; "
            "TOCTOU terminal-vs-INSERT re-spawn absorbed by idempotency_skip; "
            "phantom-IntegrityError trigger unpinned"
        )
        residuals_reference = "https://example.com/review-ffbba744"
        r_existing = add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "high",
            "summary": residuals_summary,
            "reference": residuals_reference,
        })
        existing_id = r_existing["id"]

        # NEW note that uses generic vocabulary ("merge", "restart", "pending",
        # "obsolete") which the OLD matcher would have flagged as a token-overlap
        # collision with the residuals note (TOCTOU is in both, merge/restart/pending
        # are >3-char tokens shared between many entries).
        new_toctou = (
            "TOCTOU race in session-scoped re-read at CAS: cover "
            "the watcher re-arm gap; merge pending; restart deferred"
        )
        new_toctou_reference = "https://example.com/inc-2026-09-10-toctou"
        r_new = add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "high",
            "summary": new_toctou,
            "reference": new_toctou_reference,
        })

        # The new add must succeed — distinct summary, not a normalized duplicate
        assert "error" not in r_new, f"Distinct TOCTOU add rejected: {r_new}"
        new_id = r_new["id"]
        assert new_id != existing_id, "New entry must have its own id"

        # BOTH entries must survive intact (no merge swallowed the caller)
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 2

        by_id = {e["id"]: e for e in list_result["entries"]}

        # Original 'Open residuals' note MUST be unchanged
        original_now = by_id[existing_id]
        assert original_now["summary"] == residuals_summary, (
            "Open residuals note summary was overwritten — the hijack shape!"
        )
        assert original_now["reference"] == residuals_reference, (
            "Open residuals note reference was overwritten — the hijack shape!"
        )
        assert original_now["category"] == "risk"
        assert original_now["priority"] == "high"

        # The new TOCTOU note must be present with its OWN reference
        new_now = by_id[new_id]
        assert new_now["summary"] == new_toctou
        assert new_now["reference"] == new_toctou_reference
        assert new_now["category"] == "risk"
        assert new_now["priority"] == "high"

    def test_hijack_shape_2_fixed_merge_phrasing_does_not_hijack_ambient_kv(self, mock_repo):
        """A 'FIXED (merge ..., restart pending)' phrasing must not land on the
        unrelated 'ambient KV' note."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        ambient_kv_summary = (
            "FIXED (merge 02cf770a, restart pending): ambient KV now renders "
            "standalone Shared Meta KV block + per-turn refresh; 2 kill-switches "
            "default ON. Old 'use explicit reads only' advice obsolete."
        )
        ambient_kv_reference = "https://example.com/merge-02cf770a"
        r_existing = add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "high",
            "summary": ambient_kv_summary,
            "reference": ambient_kv_reference,
        })
        existing_id = r_existing["id"]

        # New note from a different caller that shares generic vocabulary
        # (merge, restart, pending, fixed, obsolete). Under the OLD matcher
        # this would have landed on the ambient-KV row.
        new_summary = (
            "FIXED (merge abc12345, restart pending): orchestrator restart "
            "ordering now respects settle-first; obsolete kill-switch removed"
        )
        new_reference = "https://example.com/merge-abc12345"
        r_new = add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "high",
            "summary": new_summary,
            "reference": new_reference,
        })

        assert "error" not in r_new, f"Distinct 'FIXED merge' add rejected: {r_new}"
        new_id = r_new["id"]
        assert new_id != existing_id

        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 2

        by_id = {e["id"]: e for e in list_result["entries"]}
        # Ambient KV note MUST be unchanged
        original_now = by_id[existing_id]
        assert original_now["summary"] == ambient_kv_summary, (
            "Ambient KV note summary was overwritten — the hijack shape!"
        )
        assert original_now["reference"] == ambient_kv_reference, (
            "Ambient KV note reference was overwritten — the hijack shape!"
        )
        # New note has its own reference
        new_now = by_id[new_id]
        assert new_now["reference"] == new_reference

    def test_hijack_shape_3_faithful_readd_does_not_hijack_api_jobs_note(self, mock_repo):
        """A faithful re-add of an ambient-KV note (exact words) is still
        rejected (not merged), forcing the caller to either remove or use
        entry_id. NEVER silently overwrite."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")
        remove_tool = next(t for t in tools if t.name == "project_cn_remove")

        # Two distinct unrelated notes
        api_jobs_summary = (
            "/api/jobs combo with 'settled' drops 'failed' rows; "
            "settled,failed=2 vs failed=3, live-measured. Panel Recent "
            "may hide failed receipts."
        )
        api_jobs_reference = "https://example.com/tester-2026-09-08"
        r1 = add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "high",
            "summary": api_jobs_summary,
            "reference": api_jobs_reference,
        })
        api_jobs_id = r1["id"]

        ambient_kv_summary = (
            "FIXED (merge 02cf770a, restart pending): ambient KV now renders "
            "standalone Shared Meta KV block + per-turn refresh."
        )
        ambient_kv_reference = "https://example.com/merge-02cf770a"
        r2 = add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "high",
            "summary": ambient_kv_summary,
            "reference": ambient_kv_reference,
        })
        ambient_id = r2["id"]

        # Caller naively re-adds the ambient-KV summary. Under OLD code, this
        # would have hijacked whichever row happened to come first (probably
        # the api-jobs one, since the matcher did first-wins). With the new
        # strict matcher, the caller sees a clear rejection.
        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "high",
            "summary": ambient_kv_summary,
            "reference": "https://example.com/different-ref",
        })

        assert "error" in result
        assert "Near-duplicate" in result["error"]
        assert f"id={ambient_id}" in result["error"]
        # The api-jobs note must NOT have been touched
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 2  # ambient + api-jobs, no silent third
        by_id = {e["id"]: e for e in list_result["entries"]}
        assert by_id[api_jobs_id]["summary"] == api_jobs_summary
        assert by_id[api_jobs_id]["reference"] == api_jobs_reference

        # Caller can recover by either removing+re-adding or using entry_id.
        # Verify the explicit-update path works.
        updated = add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "critical",
            "summary": ambient_kv_summary + " [priority bumped]",
            "reference": ambient_kv_reference,
            "entry_id": ambient_id,
        })
        assert "error" not in updated
        assert updated["id"] == ambient_id
        assert updated["priority"] == "critical"


# =============================================================================
# Test Class: TestUpdateByEntryId — explicit in-place update path
# =============================================================================


class TestUpdateByEntryId:
    """Explicit update-by-entry_id path: exact match, no fuzzy matching."""

    def test_update_existing_entry_by_id(self, mock_repo):
        """Pass entry_id -> that specific row is updated, no fuzzy logic."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        r1 = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "medium",
            "summary": "Original summary content here",
            "reference": "https://original.example.com",
        })
        entry_id = r1["id"]

        # Update by id with a COMPLETELY DIFFERENT summary
        r2 = add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",  # category is NOT updated by the explicit update path (only priority/summary/reference)
            "priority": "critical",
            "summary": "Totally different summary now",
            "reference": "https://new.example.com",
            "entry_id": entry_id,
        })

        assert "error" not in r2, f"Explicit update rejected: {r2}"
        assert r2["id"] == entry_id, "Explicit update must preserve the entry id"
        assert r2["summary"] == "Totally different summary now"
        assert r2["priority"] == "critical"
        assert r2["reference"] == "https://new.example.com"

        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 1, "Update must NOT create a duplicate entry"
        only = list_result["entries"][0]
        assert only["id"] == entry_id
        assert only["summary"] == "Totally different summary now"

    def test_update_by_id_not_found_returns_error(self, mock_repo):
        """Pass entry_id that doesn't exist -> clear error, no silent create."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": "Some summary",
            "entry_id": "this-id-does-not-exist",
        })

        assert "error" in result
        assert "this-id-does-not-exist" in result["error"]
        assert "not found" in result["error"]
        # No silent create
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 0

    def test_update_by_id_bypasses_collision_check(self, mock_repo):
        """Pass entry_id -> collision check is skipped (explicit intent)."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")

        r1 = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "medium",
            "summary": "Use PostgreSQL for the main database",
        })
        entry_id = r1["id"]

        # Without entry_id, the same exact summary would be rejected.
        # WITH entry_id, the update is explicit and proceeds.
        r2 = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": "Use PostgreSQL for the main database",
            "entry_id": entry_id,
        })
        assert "error" not in r2
        assert r2["id"] == entry_id
        assert r2["priority"] == "high"

    def test_update_by_id_empty_string_returns_error(self, mock_repo):
        """Pass entry_id='' -> error (defensive, since we accept None)."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")

        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": "Some summary",
            "entry_id": "",
        })
        assert "error" in result
        assert "entry_id" in result["error"]


# =============================================================================
# Test Class: TestCapBehavior — replaces TestEvictionLogic
# =============================================================================


class TestCapBehavior:
    """Cap behavior at _MAX_ENTRIES: REJECT, no silent eviction.

    Replaces the old TestEvictionLogic. Silent eviction was removed
    because callers lost data without warning.
    """

    def test_add_below_cap_succeeds(self, mock_repo):
        """49 entries -> add 50th distinct summary -> succeeds, count=50."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        for i in range(_MAX_ENTRIES - 1):
            result = add_tool.invoke({
                "project_id": "test_project",
                "category": "convention",
                "priority": "medium",
                "summary": unique_summary(i),
            })
            assert "error" not in result, f"Entry {i} unexpectedly rejected: {result}"
            time.sleep(0.002)  # ensure distinct created_at for ordering

        # Add the cap-filling entry
        last = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "medium",
            "summary": "elephant frontier garden helicopter",
        })
        assert "error" not in last, f"Cap-filling entry rejected: {last}"

        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == _MAX_ENTRIES

    def test_add_at_cap_is_rejected_with_eviction_candidates(self, mock_repo):
        """_MAX_ENTRIES entries -> add 51st distinct summary -> REJECTED with eviction candidates."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        # Fill to exactly _MAX_ENTRIES
        for i in range(_MAX_ENTRIES):
            add_tool.invoke({
                "project_id": "test_project",
                "category": "convention",
                "priority": "medium",
                "summary": unique_summary(i),
            })
            time.sleep(0.002)

        # Confirm we are at the cap
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == _MAX_ENTRIES

        # The +1 add must be REJECTED with cap error naming eviction candidates
        reject_result = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "medium",
            "summary": "porcupine quail rabbit salamander turtle",
        })

        assert "error" in reject_result
        assert f"cap of {_MAX_ENTRIES}" in reject_result["error"]
        # Error must name eviction candidates so the caller can act
        assert "eviction candidates" in reject_result["error"]
        assert "summary=" in reject_result["error"]
        # No silent eviction: count must NOT have decreased
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == _MAX_ENTRIES, (
            "Silent eviction detected — entries were removed without error"
        )

    def test_add_above_old_30_threshold_still_succeeds(self, mock_repo):
        """Live-store behavior: with new cap=50, entry #31 (which used to evict)
        is added normally — no silent eviction."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        # Fill to 30 (the OLD cap)
        for i in range(30):
            add_tool.invoke({
                "project_id": "test_project",
                "category": "convention",
                "priority": "medium",
                "summary": unique_summary(i),
            })
            time.sleep(0.002)

        # Add #31 — under old code this would have triggered silent eviction.
        # Under new code, this must succeed (cap is now 50).
        r31 = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "medium",
            "summary": "elephant frontier garden helicopter",
        })
        assert "error" not in r31, f"Entry #31 unexpectedly rejected: {r31}"

        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 31, "Live-store behavior must allow >30 entries"

    def test_cap_priority_order_in_eviction_candidates(self, mock_repo):
        """When at cap, the lowest-priority oldest entries are named first in
        the eviction-candidate list (so the caller can pick sensibly)."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")

        # Fill with 49 medium entries
        for i in range(49):
            add_tool.invoke({
                "project_id": "test_project",
                "category": "convention",
                "priority": "medium",
                "summary": unique_summary(i),
            })
            time.sleep(0.002)
        # Add 1 critical entry to fill to cap
        add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "critical",
            "summary": "Critical security vulnerability found",
        })

        # Next add must be rejected; eviction candidates should NOT lead with the critical
        reject = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "medium",
            "summary": "Another medium entry that should hit the cap",
        })

        assert "error" in reject
        # The critical entry should NOT be among the first eviction candidates
        # (lowest priority, oldest first)
        # Stronger assertion: the FIRST eviction candidate line should be a medium
        # priority entry, not critical.
        error_after_candidates = reject["error"].split("eviction candidates")[1]
        first_candidate_line = next(
            (line for line in error_after_candidates.splitlines() if line.strip().startswith("- id=")),
            None,
        )
        assert first_candidate_line is not None, "Error must list at least one eviction candidate"
        assert "priority=medium" in first_candidate_line, (
            f"First eviction candidate should be lowest priority (medium), got: {first_candidate_line}"
        )

    def test_explicit_update_by_id_at_cap_still_works(self, mock_repo):
        """At cap, adding new is rejected but explicit update by entry_id still works."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        # Add a target entry
        r0 = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "medium",
            "summary": unique_summary(0),
        })
        target_id = r0["id"]

        # Fill the rest to cap
        for i in range(1, _MAX_ENTRIES):
            add_tool.invoke({
                "project_id": "test_project",
                "category": "convention",
                "priority": "medium",
                "summary": unique_summary(i),
            })
            time.sleep(0.002)

        assert list_tool.invoke({"project_id": "test_project"})["count"] == _MAX_ENTRIES

        # Explicit update by id must work even at cap
        upd = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": unique_summary(0) + " [updated]",
            "entry_id": target_id,
        })
        assert "error" not in upd, f"Explicit update at cap rejected: {upd}"
        assert upd["id"] == target_id
        assert upd["priority"] == "high"
        # Count unchanged
        assert list_tool.invoke({"project_id": "test_project"})["count"] == _MAX_ENTRIES


# =============================================================================
# Test Class: TestProjectCNList
# =============================================================================


class TestProjectCNList:
    """Tests for the project_cn_list tool."""

    def test_list_empty_project(self, list_tool, mock_repo):
        """Returns dict with project_id, count=0, entries=[]."""
        result = list_tool.invoke({"project_id": "test_project"})

        assert result["project_id"] == "test_project"
        assert result["count"] == 0
        assert result["entries"] == []

    def test_list_with_entries(self, mock_repo):
        """Returns all entries with correct count."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        # Add 3 entries with unique summaries (no collision)
        for i in range(3):
            add_tool.invoke({
                "project_id": "test_project",
                "category": "convention",
                "priority": "high",
                "summary": unique_summary(i),
            })
            time.sleep(0.01)

        result = list_tool.invoke({"project_id": "test_project"})

        assert result["project_id"] == "test_project"
        assert result["count"] == 3
        assert len(result["entries"]) == 3

    def test_list_nonexistent_project(self, list_tool, mock_repo):
        """Returns error dict for nonexistent project."""
        mock_repo.get.return_value = None

        result = list_tool.invoke({"project_id": "nonexistent"})

        assert "error" in result
        assert "Project 'nonexistent' not found" in result["error"]


# =============================================================================
# Test Class: TestProjectCNRemove
# =============================================================================


class TestProjectCNRemove:
    """Tests for the project_cn_remove tool."""

    def test_remove_existing_entry(self, mock_repo):
        """Entry gone after remove, count decreases."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")
        remove_tool = next(t for t in tools if t.name == "project_cn_remove")

        # Add an entry
        add_result = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": "Entry to be removed",
        })
        entry_id = add_result["id"]

        # Verify it exists
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 1

        # Remove it
        remove_result = remove_tool.invoke({
            "project_id": "test_project",
            "entry_id": entry_id,
        })

        assert "error" not in remove_result
        assert remove_result["removed"] is True

        # Verify it's gone
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 0

    def test_remove_nonexistent_entry(self, remove_tool, mock_repo):
        """Returns error dict for nonexistent entry."""
        result = remove_tool.invoke({
            "project_id": "test_project",
            "entry_id": "nonexistent-id",
        })

        assert "error" in result
        assert "Entry 'nonexistent-id' not found" in result["error"]

    def test_remove_from_empty_list(self, remove_tool, mock_repo):
        """Returns error dict when removing from empty list."""
        result = remove_tool.invoke({
            "project_id": "test_project",
            "entry_id": "some-id",
        })

        assert "error" in result
        assert "Entry 'some-id' not found" in result["error"]

    def test_remove_nonexistent_project(self, remove_tool, mock_repo):
        """Returns error dict for nonexistent project."""
        mock_repo.get.return_value = None

        result = remove_tool.invoke({
            "project_id": "nonexistent",
            "entry_id": "some-id",
        })

        assert "error" in result
        assert "Project 'nonexistent' not found" in result["error"]

    def test_remove_returns_summary(self, mock_repo):
        """Remove returns dict with removed=True, entry_id, and summary."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        remove_tool = next(t for t in tools if t.name == "project_cn_remove")

        # Add an entry
        add_result = add_tool.invoke({
            "project_id": "test_project",
            "category": "pattern",
            "priority": "critical",
            "summary": "Important pattern discovered",
        })
        entry_id = add_result["id"]

        # Remove it
        result = remove_tool.invoke({
            "project_id": "test_project",
            "entry_id": entry_id,
        })

        assert result["removed"] is True
        assert result["entry_id"] == entry_id
        assert result["summary"] == "Important pattern discovered"


# =============================================================================
# Test Constants
# =============================================================================


class TestConstants:
    """Tests for module constants."""

    def test_max_entries_is_50(self):
        """Verify _MAX_ENTRIES is 50 (raised from 30 for live-store headroom)."""
        assert _MAX_ENTRIES == 50

    def test_max_summary_len_is_200(self):
        """Verify _MAX_SUMMARY_LEN is 200."""
        assert _MAX_SUMMARY_LEN == 200
