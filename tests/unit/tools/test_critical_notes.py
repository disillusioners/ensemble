"""Tests for critical notes tools: add, list, remove, merge, eviction."""

import time
import pytest
from unittest.mock import MagicMock

from daemon.tools.critical_notes import (
    create_critical_notes_tools,
    _find_similar_entry,
    _merge_entries,
    _evict_if_needed,
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
    """Generate a summary with unique keywords to avoid merge.

    Uses completely different word sets so no two summaries share
    more than 1 keyword (>3 chars). Has 90 unique words to cover 30 entries.
    """
    # Large word list with 90 unique words (3 per entry x 30 entries)
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
        # 80-89: Final set
        "prism", "quartz", "river", "stone", "tower",
        "ultra", "vortex", "willow", "xylem", "yacht",
    ]
    # Pick 3 unique words based on index (no wrap-around with 90 words)
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
# Test Class: TestMergeLogic
# =============================================================================


class TestMergeLogic:
    """Tests for the merge logic (_find_similar_entry, _merge_entries)."""

    def test_merge_similar_entries(self, mock_repo):
        """Add A, then add B with same category + >=2 keyword overlap -> B merges into A."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")

        # Add first entry
        result1 = add_tool.invoke({
            "project_id": "test_project",
            "category": "pattern",
            "priority": "high",
            "summary": "Always use dependency injection for better testing",
        })
        original_id = result1["id"]
        original_created_at = result1["created_at"]

        # Add similar entry (same category, >=2 keyword overlap)
        result2 = add_tool.invoke({
            "project_id": "test_project",
            "category": "pattern",
            "priority": "high",
            "summary": "Dependency injection helps with unit testing",
        })

        # Should merge - same ID and updated_at should be newer
        assert result2["id"] == original_id
        assert result2["updated_at"] != original_created_at

    def test_merge_preserves_shorter_summary(self, mock_repo):
        """Existing has long summary, new has short -> merged has short summary."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")

        # Add first entry with long summary
        add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "medium",
            "summary": "This is a very long summary that should be replaced",
        })

        # Add entry with short summary
        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "medium",
            "summary": "Short summary",
        })

        assert result["summary"] == "Short summary"

    def test_merge_keeps_existing_id(self, mock_repo):
        """After merge, the entry ID is the original one."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")

        result1 = add_tool.invoke({
            "project_id": "test_project",
            "category": "decision",
            "priority": "high",
            "summary": "We decided to use PostgreSQL for the main database",
        })
        original_id = result1["id"]

        result2 = add_tool.invoke({
            "project_id": "test_project",
            "category": "decision",
            "priority": "high",
            "summary": "PostgreSQL is our chosen database solution",
        })

        assert result2["id"] == original_id

    def test_merge_no_overlap(self, mock_repo):
        """Add A, then add B with same category but 0 keyword overlap -> both entries exist."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        # Add first entry
        add_tool.invoke({
            "project_id": "test_project",
            "category": "constraint",
            "priority": "high",
            "summary": "Maximum 10 concurrent connections allowed",
        })

        # Add entry with completely different keywords
        add_tool.invoke({
            "project_id": "test_project",
            "category": "constraint",
            "priority": "high",
            "summary": "Must validate all user inputs thoroughly",
        })

        # Both entries should exist
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 2

    def test_merge_different_category(self, mock_repo):
        """Add A, then add B with different category but similar keywords -> both entries exist."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        # Add first entry
        add_tool.invoke({
            "project_id": "test_project",
            "category": "pattern",
            "priority": "high",
            "summary": "Always validate user input for security",
        })

        # Add entry with similar keywords but different category
        add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "high",
            "summary": "Validate input to prevent security issues",
        })

        # Both entries should exist (different category, no merge)
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 2

    def test_merge_updates_reference(self, mock_repo):
        """Existing has no reference, new has reference -> merged has new's reference."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")

        # Add entry without reference
        add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": "Follow coding standards consistently",
        })

        # Add similar entry with reference
        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": "Coding standards guide for consistent style",
            "reference": "https://style.guide.com",
        })

        assert result["reference"] == "https://style.guide.com"

    def test_merge_preserves_existing_reference(self, mock_repo):
        """Existing has reference, new has no reference -> merged keeps existing reference."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")

        # Add entry with reference
        add_tool.invoke({
            "project_id": "test_project",
            "category": "pattern",
            "priority": "medium",
            "summary": "Error handling best practices",
            "reference": "https://error-handling.example.com",
        })

        # Add similar entry without reference
        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "pattern",
            "priority": "medium",
            "summary": "Best practices for handling errors",
        })

        assert result["reference"] == "https://error-handling.example.com"

    def test_merge_new_higher_priority_wins(self, mock_repo):
        """Existing is medium, new is critical -> merged is critical."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")

        # Add entry with medium priority
        add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "medium",
            "summary": "Consider caching for performance improvement",
        })

        # Add similar entry with critical priority
        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "critical",
            "summary": "Caching improves application performance",
        })

        assert result["priority"] == "critical"

    def test_merge_new_lower_priority_keeps_existing(self, mock_repo):
        """Existing is critical, new is medium -> merged stays critical."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")

        # Add entry with critical priority
        add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "critical",
            "summary": "Never commit secrets to version control",
        })

        # Add similar entry with medium priority
        result = add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "medium",
            "summary": "Version control should not contain secrets",
        })

        assert result["priority"] == "critical"

    def test_merge_fewer_than_2_keywords(self, mock_repo):
        """Summary has only 1 keyword (word > 3 chars) -> no merge, new entry created."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        # Add first entry
        add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": "Code quality matters for maintainability",
        })

        # Add entry with only one significant keyword overlap
        add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "high",
            "summary": "The code",
        })

        # Both entries should exist (only "code" is significant >3 chars)
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 2


# =============================================================================
# Test Class: TestEvictionLogic
# =============================================================================


class TestEvictionLogic:
    """Tests for the eviction logic (_evict_if_needed)."""

    def test_eviction_at_max(self, mock_repo):
        """Fill to 30 entries, add 31st -> oldest lowest-priority evicted, total stays 30."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        # Fill to 30 entries with unique summaries (no keyword overlap)
        for i in range(_MAX_ENTRIES):
            add_tool.invoke({
                "project_id": "test_project",
                "category": "convention",
                "priority": "medium",
                "summary": unique_summary(i),
            })
            time.sleep(0.01)

        # Verify we have 30 entries
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == _MAX_ENTRIES

        # Add 31st entry with no overlap
        add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "medium",
            "summary": "elephant frontier garden helicopter",
        })

        # Should still have 30 entries
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == _MAX_ENTRIES

    def test_eviction_priority_order(self, mock_repo):
        """Mix of critical/high/medium -> medium evicted first."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        # Fill with 29 medium entries
        for i in range(29):
            add_tool.invoke({
                "project_id": "test_project",
                "category": "convention",
                "priority": "medium",
                "summary": f"Medium entry {i} unique {i}",
            })
            time.sleep(0.01)

        # Add 1 critical entry
        add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "critical",
            "summary": "Critical security vulnerability found",
        })
        time.sleep(0.01)

        # Now add 31st (medium) - should evict oldest medium, not critical
        add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "medium",
            "summary": "Another medium entry that should cause eviction",
        })

        list_result = list_tool.invoke({"project_id": "test_project"})

        # Should still have critical entry
        entries = list_result["entries"]
        critical_entries = [e for e in entries if e["priority"] == "critical"]
        assert len(critical_entries) == 1
        assert critical_entries[0]["summary"] == "Critical security vulnerability found"

    def test_eviction_all_critical(self, mock_repo):
        """30 critical entries, add 31st critical -> oldest critical evicted."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        # Fill with 30 critical entries with no keyword overlap
        for i in range(_MAX_ENTRIES):
            add_tool.invoke({
                "project_id": "test_project",
                "category": "risk",
                "priority": "critical",
                "summary": unique_summary(i),
            })
            time.sleep(0.01)

        # Add 31st critical entry with completely different words
        add_tool.invoke({
            "project_id": "test_project",
            "category": "risk",
            "priority": "critical",
            "summary": "mountain river valley bridge castle",
        })

        # Should have evicted the oldest (first) critical entry
        list_result = list_tool.invoke({"project_id": "test_project"})

        assert list_result["count"] == _MAX_ENTRIES
        # All entries should be critical
        for entry in list_result["entries"]:
            assert entry["priority"] == "critical"

    def test_no_eviction_under_max(self, mock_repo):
        """29 entries, add 30th -> no eviction, total 30."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        # Add 29 entries with no keyword overlap
        for i in range(29):
            add_tool.invoke({
                "project_id": "test_project",
                "category": "convention",
                "priority": "medium",
                "summary": unique_summary(i),
            })
            time.sleep(0.01)

        # Add 30th entry with no overlap
        add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "medium",
            "summary": "penguin jaguar koala llama monkey",
        })

        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == 30

    def test_eviction_oldest_of_same_priority(self, mock_repo):
        """Multiple medium entries -> oldest medium evicted."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        # Fill with 30 medium entries, keeping track of first
        first_summary = unique_summary(0)
        add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "medium",
            "summary": first_summary,
        })
        time.sleep(0.01)

        # Add 29 more medium entries with no keyword overlap with first
        for i in range(1, 30):
            add_tool.invoke({
                "project_id": "test_project",
                "category": "convention",
                "priority": "medium",
                "summary": unique_summary(i),
            })
            time.sleep(0.01)

        # Add 31st medium with no overlap
        add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "medium",
            "summary": "porcupine quail rabbit salamander turtle",
        })

        list_result = list_tool.invoke({"project_id": "test_project"})

        # First entry should be evicted
        summaries = [e["summary"] for e in list_result["entries"]]
        assert first_summary not in summaries

    def test_merge_at_max_no_eviction(self, mock_repo):
        """At 30 entries, add similar entry -> merge happens, no eviction needed."""
        tools = create_critical_notes_tools(mock_repo, agent_id="test_agent")
        add_tool = next(t for t in tools if t.name == "project_cn_add")
        list_tool = next(t for t in tools if t.name == "project_cn_list")

        # Fill to 30 entries with unique summaries (no keyword overlap)
        for i in range(_MAX_ENTRIES):
            add_tool.invoke({
                "project_id": "test_project",
                "category": "convention",
                "priority": "medium",
                "summary": unique_summary(i),
            })
            time.sleep(0.01)

        # Get the first entry's summary
        list_result = list_tool.invoke({"project_id": "test_project"})
        first_summary = list_result["entries"][0]["summary"]

        # Add a similar entry (same category + >=2 keyword overlap)
        add_tool.invoke({
            "project_id": "test_project",
            "category": "convention",
            "priority": "medium",
            "summary": first_summary + " with extra details",
        })

        # Should still have 30 entries (merge happened, no eviction)
        list_result = list_tool.invoke({"project_id": "test_project"})
        assert list_result["count"] == _MAX_ENTRIES


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

        # Add 3 entries with unique summaries (no keyword overlap)
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

    def test_max_entries_is_30(self):
        """Verify _MAX_ENTRIES is 30."""
        assert _MAX_ENTRIES == 30

    def test_max_summary_len_is_200(self):
        """Verify _MAX_SUMMARY_LEN is 200."""
        assert _MAX_SUMMARY_LEN == 200
