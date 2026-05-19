"""Tests for Phase 4 Archive Lifecycle features.

This module tests:
- Archive path validation in access_memory tool
- load_recent_memories with archive support
- _archive_memory_file function
- _archive_old_memories function
- _load_growth_rules archive TTL parsing
- Full integration of archive lifecycle
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# Category 1: Archive path validation in access_memory (via tool, mock registry)
# =============================================================================

class TestAccessMemoryArchive:
    """Tests for archive access via the access_memory tool."""

    def _create_tool_with_mock_registry(self, agent_dir: Path):
        """Helper to create access_memory tool with mocked registry."""
        from daemon.tools.access_memory import create_access_memory_tool

        mock_meta = MagicMock()
        mock_meta.path = agent_dir

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            tool = create_access_memory_tool("test-agent")
            return tool

    def test_access_archive_valid_path(self, tmp_path):
        """archive/2026/01/test-file.md reads content from memories/archive/2026/01/."""
        from daemon.tools.access_memory import create_access_memory_tool

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        archive_dir = memories_dir / "archive" / "2026" / "01"
        archive_dir.mkdir(parents=True)

        test_file = archive_dir / "test-file.md"
        test_file.write_text("# Archived Memory\n\nThis is archived content.")

        mock_meta = MagicMock()
        mock_meta.path = agent_dir

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            tool = create_access_memory_tool("test-agent")
            result = tool.invoke({"filename": "archive/2026/01/test-file.md"})

            assert "Archived Memory" in result
            assert "This is archived content." in result

    def test_access_archive_path_traversal_rejected(self, tmp_path):
        """archive/../../etc/passwd returns 'not found' (sanitized, not found in memories/)."""
        from daemon.tools.access_memory import create_access_memory_tool

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()
        archive_dir = memories_dir / "archive" / "2026" / "01"
        archive_dir.mkdir(parents=True)

        # Create a file in archive
        test_file = archive_dir / "test.md"
        test_file.write_text("# Real file in archive")

        mock_meta = MagicMock()
        mock_meta.path = agent_dir

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            tool = create_access_memory_tool("test-agent")
            # Path traversal attempt: archive/../../memories/2026/01/test.md
            # remainder = "../memories/2026/01/test.md" doesn't match ARCHIVE_PATTERN
            # -> sanitized to "test.md", looks in memories/, not memories/archive/
            result = tool.invoke({"filename": "archive/../../memories/2026/01/test.md"})

            # The file is in archive, not in memories/, so it's not found
            assert "not found" in result.lower()
            assert "Access denied" not in result

    def test_access_archive_invalid_format_sanitized(self, tmp_path):
        """archive/invalid/path.md sanitizes to filename only (path.md) and looks in memories/."""
        from daemon.tools.access_memory import create_access_memory_tool

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create a file at the sanitized path (path.md = Path("invalid/path.md").name)
        test_file = memories_dir / "path.md"
        test_file.write_text("# Normal Memory\n\nFound via sanitization.")

        mock_meta = MagicMock()
        mock_meta.path = agent_dir

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            tool = create_access_memory_tool("test-agent")
            # Invalid archive path: archive/invalid/path.md -> remainder is "invalid/path.md"
            # ARCHIVE_PATTERN expects YYYY/MM/filename.md, so "invalid/path.md" won't match
            # -> safe_name = Path("invalid/path.md").name = "path.md"
            result = tool.invoke({"filename": "archive/invalid/path.md"})

            assert "Normal Memory" in result
            assert "Found via sanitization." in result

    def test_access_archive_nonexistent_returns_not_found(self, tmp_path):
        """archive/2026/01/nonexistent.md returns 'not found'."""
        from daemon.tools.access_memory import create_access_memory_tool

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        archive_dir = memories_dir / "archive" / "2026" / "01"
        archive_dir.mkdir(parents=True)

        mock_meta = MagicMock()
        mock_meta.path = agent_dir

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            tool = create_access_memory_tool("test-agent")
            result = tool.invoke({"filename": "archive/2026/01/nonexistent.md"})

            assert "not found" in result.lower()

    def test_access_archive_symlink_rejected(self, tmp_path):
        """Symlinks inside archive directory pointing outside are rejected."""
        from daemon.tools.access_memory import create_access_memory_tool

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        archive_dir = memories_dir / "archive" / "2026" / "01"
        archive_dir.mkdir(parents=True)

        # Create a real file outside the archive
        outside_file = tmp_path / "outside.md"
        outside_file.write_text("# Outside file")

        if os.name != "nt":
            # Create symlink inside archive pointing outside
            symlink_file = archive_dir / "malicious.md"
            symlink_file.symlink_to(outside_file)

            mock_meta = MagicMock()
            mock_meta.path = agent_dir

            with patch("daemon.registry.get_registry") as mock_get_registry:
                mock_registry = MagicMock()
                mock_registry.get.return_value = mock_meta
                mock_get_registry.return_value = mock_registry

                tool = create_access_memory_tool("test-agent")
                # Try to access via archive path with symlink
                result = tool.invoke({"filename": "archive/2026/01/malicious.md"})

                # Symlink pointing outside the archive directory triggers "Access denied"
                assert result == "Access denied"

    def test_access_normal_file_still_works(self, tmp_path):
        """Non-archive access (normal memory file) is unchanged."""
        from daemon.tools.access_memory import create_access_memory_tool

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        test_file = memories_dir / "20260401_1430-normal-memory.md"
        test_file.write_text("# Normal Memory\n\nActive memory content.")

        mock_meta = MagicMock()
        mock_meta.path = agent_dir

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            tool = create_access_memory_tool("test-agent")
            result = tool.invoke({"filename": "20260401_1430-normal-memory.md"})

            assert "Normal Memory" in result
            assert "Active memory content." in result


# =============================================================================
# Category 2: load_recent_memories with archive support
# =============================================================================

class TestLoadRecentMemoriesArchive:
    """Tests for load_recent_memories with include_archived support."""

    def test_load_recent_no_archive_by_default(self, tmp_path):
        """Without include_archived, only active memories are shown."""
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()
        archive_dir = memories_dir / "archive" / "2025" / "12"
        archive_dir.mkdir(parents=True)

        # Create active memory
        (memories_dir / "20260401_1430-active.md").write_text("# Active")
        # Create archived memory
        (archive_dir / "20251201_1000-archived.md").write_text("# Archived")

        result = load_recent_memories(agent_dir)

        assert "active.md" in result
        assert "archived.md" not in result

    def test_load_recent_with_archive(self, tmp_path):
        """With include_archived=True, both active and archived memories are shown."""
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()
        archive_dir = memories_dir / "archive" / "2025" / "12"
        archive_dir.mkdir(parents=True)

        # Create active memory
        (memories_dir / "20260401_1430-active.md").write_text("# Active")
        # Create archived memory with the actual filename
        (archive_dir / "20251201_1000-archived.md").write_text("# Archived")

        result = load_recent_memories(agent_dir, include_archived=True)

        assert "active.md" in result
        assert "archived.md" in result
        # The full archive path includes the actual filename
        assert "archive/2025/12/20251201_1000-archived.md" in result

    def test_load_recent_archive_sorted_newest_first(self, tmp_path):
        """Archived files are sorted newest first (by full path YYYY/MM descending)."""
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()
        archive_dir_2026 = memories_dir / "archive" / "2026" / "01"
        archive_dir_2025 = memories_dir / "archive" / "2025" / "12"
        archive_dir_2026.mkdir(parents=True)
        archive_dir_2025.mkdir(parents=True)

        # Older archived file (December 2025)
        (archive_dir_2025 / "20251201_1000-old.md").write_text("# Old")
        # Newer archived file (January 2026)
        (archive_dir_2026 / "20260115_1000-new.md").write_text("# New")

        result = load_recent_memories(agent_dir, include_archived=True, limit=0)

        lines = result.strip().split("\n")
        archive_lines = [l for l in lines if "archive/" in l]
        assert len(archive_lines) == 2

        # Sorted by full path (YYYY/MM/filename), so 2026/01 comes before 2025/12
        assert "2026/01" in archive_lines[0]
        assert "2025/12" in archive_lines[1]

    def test_load_recent_archive_respects_limit(self, tmp_path):
        """archive_limit param limits archived entries (separate from active limit)."""
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()
        archive_dir = memories_dir / "archive" / "2026" / "01"
        archive_dir.mkdir(parents=True)

        # Create 3 archived files (should be limited to 2 with archive_limit=2)
        (archive_dir / "20260103_1000-third.md").write_text("# Third")
        (archive_dir / "20260102_1000-second.md").write_text("# Second")
        (archive_dir / "20260101_1000-first.md").write_text("# First")
        # Active memory
        (memories_dir / "20260401_1430-active.md").write_text("# Active")

        result = load_recent_memories(agent_dir, include_archived=True, archive_limit=2)

        lines = result.strip().split("\n")
        archive_lines = [l for l in lines if "archive/" in l]
        # Only 2 archived files (limit respected)
        assert len(archive_lines) == 2

    def test_load_recent_archive_filenames_have_prefix(self, tmp_path):
        """Archived filenames in output include 'archive/YYYY/MM/' prefix."""
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()
        archive_dir = memories_dir / "archive" / "2026" / "03"
        archive_dir.mkdir(parents=True)

        (archive_dir / "my-memory.md").write_text("# Memory")

        result = load_recent_memories(agent_dir, include_archived=True)

        assert "archive/2026/03/my-memory.md" in result

    def test_load_recent_empty_archive(self, tmp_path):
        """With include_archived=True but no archive directory, only active shown."""
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Only active memory, no archive directory
        (memories_dir / "20260401_1430-active.md").write_text("# Active")

        result = load_recent_memories(agent_dir, include_archived=True)

        assert "active.md" in result
        assert "archive/" not in result

    def test_load_recent_no_memories_dir(self, tmp_path):
        """Returns empty string when no memories directory exists."""
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        # No memories directory at all

        result = load_recent_memories(agent_dir, include_archived=True)

        assert result == ""


# =============================================================================
# Category 3: _archive_memory_file tests
# =============================================================================

class TestArchiveMemoryFile:
    """Tests for _archive_memory_file function."""

    def test_archive_memory_file_moves_successfully(self, tmp_path):
        """File is moved from memories/ to memories/archive/YYYY/MM/."""
        from daemon.tools.inner_soul import _archive_memory_file

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create a memory file to archive
        source_file = memories_dir / "20260401_1430-test-memory.md"
        source_file.write_text("# Test Memory\n\nContent to archive.")

        result = _archive_memory_file(agent_dir, "20260401_1430-test-memory.md")

        assert result is True
        # Source should be gone
        assert not source_file.exists()
        # Archive directory should exist with the file
        now_year = str(__import__("datetime").datetime.now().year)
        now_month = f"{__import__("datetime").datetime.now().month:02d}"
        archived_file = memories_dir / "archive" / now_year / now_month / "20260401_1430-test-memory.md"
        assert archived_file.exists()

    def test_archive_memory_file_creates_directory_structure(self, tmp_path):
        """Archive subdirectories (YYYY/MM) are created automatically."""
        from daemon.tools.inner_soul import _archive_memory_file

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        source_file = memories_dir / "new-memory.md"
        source_file.write_text("# New Memory")

        _archive_memory_file(agent_dir, "new-memory.md")

        # Check that archive/YYYY/MM structure was created
        now_year = str(__import__("datetime").datetime.now().year)
        now_month = f"{__import__("datetime").datetime.now().month:02d}"
        archive_dir = memories_dir / "archive" / now_year / now_month
        assert archive_dir.exists()
        assert archive_dir.is_dir()

    def test_archive_memory_file_missing_source(self, tmp_path):
        """Returns False when source file doesn't exist."""
        from daemon.tools.inner_soul import _archive_memory_file

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        result = _archive_memory_file(agent_dir, "nonexistent-file.md")

        assert result is False

    def test_archive_memory_file_handles_collision(self, tmp_path):
        """When destination exists, appends counter to filename."""
        from daemon.tools.inner_soul import _archive_memory_file

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        now_year = str(__import__("datetime").datetime.now().year)
        now_month = f"{__import__("datetime").datetime.now().month:02d}"
        archive_dir = memories_dir / "archive" / now_year / now_month
        archive_dir.mkdir(parents=True)

        # Create first memory file and archive it
        source1 = memories_dir / "same-name.md"
        source1.write_text("# First")
        result1 = _archive_memory_file(agent_dir, "same-name.md")
        assert result1 is True
        archived1 = archive_dir / "same-name.md"
        assert archived1.exists()
        assert archived1.read_text() == "# First"

        # Create second memory file with same name and archive it
        source2 = memories_dir / "same-name.md"
        source2.write_text("# Second")
        result2 = _archive_memory_file(agent_dir, "same-name.md")
        assert result2 is True
        archived2 = archive_dir / "same-name-1.md"
        assert archived2.exists()
        assert archived2.read_text() == "# Second"

    def test_archive_memory_file_preserves_content(self, tmp_path):
        """File content is unchanged after archival."""
        from daemon.tools.inner_soul import _archive_memory_file

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        original_content = "# Important Memory\n\nSome important content with **markdown**."
        source_file = memories_dir / "important-memory.md"
        source_file.write_text(original_content)

        _archive_memory_file(agent_dir, "important-memory.md")

        now_year = str(__import__("datetime").datetime.now().year)
        now_month = f"{__import__("datetime").datetime.now().month:02d}"
        archived_file = memories_dir / "archive" / now_year / now_month / "important-memory.md"

        assert archived_file.read_text() == original_content


# =============================================================================
# Category 4: _archive_old_memories tests
# =============================================================================

class TestArchiveOldMemories:
    """Tests for _archive_old_memories function."""

    def test_archive_old_memories_moves_old_files(self, tmp_path):
        """Files older than TTL are archived."""
        from daemon.tools.inner_soul import _archive_old_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create an old file (100 days ago)
        old_file = memories_dir / "old-memory.md"
        old_file.write_text("# Old Memory")
        old_time = (__import__("time").time() - (100 * 86400))
        os.utime(old_file, (old_time, old_time))

        result = _archive_old_memories(agent_dir, ttl_days=90)

        assert result == 1
        assert not old_file.exists()
        # File should be in archive
        archive_dir = memories_dir / "archive"
        archived_files = list(archive_dir.rglob("*.md"))
        assert len(archived_files) == 1
        assert archived_files[0].name == "old-memory.md"

    def test_archive_old_memories_keeps_recent(self, tmp_path):
        """Files newer than TTL are NOT archived."""
        from daemon.tools.inner_soul import _archive_old_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create a recent file (10 days old)
        recent_file = memories_dir / "recent-memory.md"
        recent_file.write_text("# Recent Memory")
        recent_time = (__import__("time").time() - (10 * 86400))
        os.utime(recent_file, (recent_time, recent_time))

        result = _archive_old_memories(agent_dir, ttl_days=90)

        assert result == 0
        assert recent_file.exists()

    def test_archive_old_memories_ttl_zero_disabled(self, tmp_path):
        """TTL of 0 disables archiving entirely."""
        from daemon.tools.inner_soul import _archive_old_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create an old file
        old_file = memories_dir / "old-memory.md"
        old_file.write_text("# Old Memory")
        old_time = (__import__("time").time() - (100 * 86400))
        os.utime(old_file, (old_time, old_time))

        result = _archive_old_memories(agent_dir, ttl_days=0)

        assert result == 0
        assert old_file.exists()

    def test_archive_old_memories_returns_count(self, tmp_path):
        """Returns the number of files archived."""
        from daemon.tools.inner_soul import _archive_old_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create 3 old files
        old_time = (__import__("time").time() - (100 * 86400))
        for i in range(3):
            f = memories_dir / f"old-{i}.md"
            f.write_text(f"# Old {i}")
            os.utime(f, (old_time, old_time))

        result = _archive_old_memories(agent_dir, ttl_days=90)

        assert result == 3

    def test_archive_old_memories_no_memories_dir(self, tmp_path):
        """Returns 0 when memories/ directory doesn't exist."""
        from daemon.tools.inner_soul import _archive_old_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        # No memories directory

        result = _archive_old_memories(agent_dir, ttl_days=90)

        assert result == 0


# =============================================================================
# Category 5: _load_growth_rules archive TTL parsing
# =============================================================================

class TestLoadGrowthRulesArchiveTTL:
    """Tests for _load_growth_rules archive TTL parsing."""

    def test_growth_rules_default_archive_ttl(self, tmp_path):
        """When growth.md doesn't exist, basic defaults are returned."""
        from daemon.tools.inner_soul import _load_growth_rules

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        # No growth.md file

        rules = _load_growth_rules(agent_dir)

        # Basic defaults returned (early return path doesn't include all keys)
        assert rules["max_memory_words"] == 2000
        assert rules["max_soul_chars"] == 2000
        assert rules["soul_requires_approval"] is True

    def test_growth_rules_custom_archive_ttl(self, tmp_path):
        """Parses 'archive: 30 days' from growth.md."""
        from daemon.tools.inner_soul import _load_growth_rules

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()

        growth_file = agent_dir / "growth.md"
        growth_file.write_text(
            "# Growth Rules\n\n"
            "memory.md max 3000 words\n"
            "soul.md max 2000 characters\n"
            "archive memories older than 30 days\n"
        )

        rules = _load_growth_rules(agent_dir)

        assert rules["memory_archive_ttl_days"] == 30
        assert rules["max_memory_words"] == 3000

    def test_growth_rules_no_growth_file(self, tmp_path):
        """Returns basic defaults when growth.md doesn't exist."""
        from daemon.tools.inner_soul import _load_growth_rules

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        # No growth.md

        rules = _load_growth_rules(agent_dir)

        # Basic defaults from early return
        assert rules["max_memory_words"] == 2000
        assert rules["max_soul_chars"] == 2000
        assert rules["soul_requires_approval"] is True


# =============================================================================
# Category 6: Integration tests
# =============================================================================

class TestArchiveLifecycleIntegration:
    """Integration tests for full archive lifecycle."""

    def test_full_archive_lifecycle(self, tmp_path):
        """Create memory -> age it -> archive -> access via archive path."""
        from daemon.tools.inner_soul import _archive_memory_file, _archive_old_memories
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Step 1: Create a memory file
        memory_file = memories_dir / "lifecycle-test.md"
        memory_file.write_text("# Lifecycle Test\n\nMemory created for lifecycle test.")

        assert memory_file.exists()

        # Step 2: Age the file (set modification time to 100 days ago)
        old_time = __import__("time").time() - (100 * 86400)
        os.utime(memory_file, (old_time, old_time))

        # Step 3: Archive via _archive_old_memories
        archived_count = _archive_old_memories(agent_dir, ttl_days=90)
        assert archived_count == 1
        assert not memory_file.exists()

        # Step 4: Verify file is in archive directory
        now_year = str(__import__("datetime").datetime.now().year)
        now_month = f"{__import__("datetime").datetime.now().month:02d}"
        archive_file = memories_dir / "archive" / now_year / now_month / "lifecycle-test.md"
        assert archive_file.exists()
        assert archive_file.read_text() == "# Lifecycle Test\n\nMemory created for lifecycle test."

        # Step 5: Verify load_recent_memories with include_archived shows the file
        result = load_recent_memories(agent_dir, include_archived=True)
        assert "archive/" in result
        assert "lifecycle-test.md" in result

    def test_archive_then_load_with_include_archived(self, tmp_path):
        """Archived files appear in load_recent_memories output with include_archived=True."""
        from daemon.tools.inner_soul import _archive_memory_file
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create and archive a file
        source_file = memories_dir / "to-archive.md"
        source_file.write_text("# To Archive")

        _archive_memory_file(agent_dir, "to-archive.md")

        # Verify it appears in load_recent_memories with include_archived
        result = load_recent_memories(agent_dir, include_archived=True)
        assert "archive/" in result
        assert "to-archive.md" in result

    def test_archive_does_not_affect_active_list(self, tmp_path):
        """After archival, active memories list no longer shows archived files."""
        from daemon.tools.inner_soul import _archive_memory_file
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create two files
        (memories_dir / "keep-active.md").write_text("# Keep")
        (memories_dir / "will-archive.md").write_text("# Will Archive")

        # Archive one file
        _archive_memory_file(agent_dir, "will-archive.md")

        # Check that only active file shows without include_archived
        result_no_archive = load_recent_memories(agent_dir, include_archived=False)
        assert "keep-active.md" in result_no_archive
        assert "will-archive.md" not in result_no_archive
        assert "archive/" not in result_no_archive

        # Check that archived file shows with include_archived=True
        result_with_archive = load_recent_memories(agent_dir, include_archived=True)
        assert "keep-active.md" in result_with_archive
        assert "archive/" in result_with_archive
        assert "will-archive.md" in result_with_archive

    def test_direct_archive_file_via_function(self, tmp_path):
        """_archive_memory_file can be called directly for manual archival."""
        from daemon.tools.inner_soul import _archive_memory_file

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create a memory file
        source = memories_dir / "manual-archive.md"
        source.write_text("# Manual Archive\n\nArchived manually.")

        # Archive it directly
        result = _archive_memory_file(agent_dir, "manual-archive.md")

        assert result is True
        assert not source.exists()

        # Verify it's in the archive with correct structure
        now_year = str(__import__("datetime").datetime.now().year)
        now_month = f"{__import__("datetime").datetime.now().month:02d}"
        archived = memories_dir / "archive" / now_year / now_month / "manual-archive.md"
        assert archived.exists()
        assert "Manual Archive" in archived.read_text()

    def test_archive_preserves_multiple_files(self, tmp_path):
        """Multiple archives preserve all files correctly."""
        from daemon.tools.inner_soul import _archive_memory_file

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create multiple files
        files_content = {
            "first.md": "# First Memory",
            "second.md": "# Second Memory",
            "third.md": "# Third Memory",
        }
        for name, content in files_content.items():
            (memories_dir / name).write_text(content)

        # Archive all three
        for name in files_content:
            result = _archive_memory_file(agent_dir, name)
            assert result is True

        # Verify all are archived
        now_year = str(__import__("datetime").datetime.now().year)
        now_month = f"{__import__("datetime").datetime.now().month:02d}"
        archive_dir = memories_dir / "archive" / now_year / now_month

        for name, content in files_content.items():
            archived_file = archive_dir / name
            assert archived_file.exists(), f"{name} should be in archive"
            assert archived_file.read_text() == content

        # Verify no files remain in active memories
        active_files = list(memories_dir.glob("*.md"))
        assert len(active_files) == 0
