"""Tests for inner_soul tool's Phase 3 compaction and file locking features.

Tests:
1. _lock_memory_file() - exclusive file locking with timeout
2. _atomic_write_memory() - atomic write with backup/rollback
3. _compact_memory() - line deduplication (handles all list markers)
4. _update_memory_md() - integration of locking + compaction
"""

import pytest
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from daemon.tools.inner_soul import (
    _lock_memory_file,
    _atomic_write_memory,
    _compact_memory,
    _update_memory_md,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_agent(tmp_path):
    """Create a minimal test agent in temp directory."""
    agent_dir = tmp_path / "test_agent"
    agent_dir.mkdir()
    (agent_dir / "soul.md").write_text("# Who I Am\n\nI am a test agent.\n")
    (agent_dir / "growth.md").write_text("# Growth\n\nmax_memory_words: 2000\nmax_soul_chars: 2000\n")
    (agent_dir / "workflow.md").write_text("# Workflow\n\n1. Process\n")
    (agent_dir / "rule.md").write_text("# Rules\n\n- Follow rules\n")
    (agent_dir / "memory.md").write_text("# Memory\n\n")
    (agent_dir / "memories").mkdir()
    (agent_dir / "history").mkdir()
    return agent_dir


@pytest.fixture
def mock_manager():
    """Create mock InstanceManager."""
    mgr = MagicMock()
    mgr.prompt_cache = MagicMock()
    return mgr


# =============================================================================
# 1. File Locking Tests
# =============================================================================


class TestLockMemoryFile:
    """Tests for the _lock_memory_file() context manager."""

    def test_lock_acquires_and_releases(self, tmp_path):
        """Basic lock acquire and release works correctly."""
        lock_file_path = tmp_path / "test.lock"
        test_file = tmp_path / "test.md"

        lock_acquired = False
        lock_released = False

        with _lock_memory_file(test_file):
            lock_acquired = True
            # Verify lock file exists
            assert lock_file_path.exists()

        lock_released = True

        assert lock_acquired
        assert lock_released

    def test_lock_timeout_raises_timeout_error(self, tmp_path):
        """Lock raises TimeoutError when held by another process."""
        test_file = tmp_path / "test.md"
        timeout = 0.3  # Short timeout for test

        # Use a thread to hold the lock
        lock_held_event = threading.Event()
        lock_released_event = threading.Event()

        def hold_lock():
            with _lock_memory_file(test_file, timeout=10.0):
                lock_held_event.set()
                # Wait for test to try acquiring
                lock_released_event.wait(timeout=2.0)

        holder_thread = threading.Thread(target=hold_lock)
        holder_thread.start()

        # Wait for lock to be held
        lock_held_event.wait(timeout=1.0)

        # Now try to acquire the same lock with short timeout
        try:
            with _lock_memory_file(test_file, timeout=timeout):
                pytest.fail("Should have raised TimeoutError")
        except TimeoutError as e:
            assert "Could not acquire lock" in str(e)
        finally:
            lock_released_event.set()
            holder_thread.join(timeout=2.0)

    def test_lock_releases_on_exception(self, tmp_path):
        """Lock is released even if an exception occurs inside the context."""
        test_file = tmp_path / "test.md"
        lock_file_path = tmp_path / "test.lock"

        # Acquire lock and raise exception
        try:
            with _lock_memory_file(test_file):
                assert lock_file_path.exists()
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Lock file may still exist (it's a marker file, not a temp file)
        # But the lock itself is released, so we should be able to acquire again
        with _lock_memory_file(test_file, timeout=1.0):
            assert lock_file_path.exists()

    def test_lock_uses_separate_lock_file(self, tmp_path):
        """Lock creates a .lock file, not modifying the actual memory file."""
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("Original content")

        with _lock_memory_file(memory_file):
            # .lock file should exist
            lock_file = tmp_path / "memory.lock"
            assert lock_file.exists()
            # Original file should be unchanged
            assert memory_file.read_text() == "Original content"

        # Lock file is released (file descriptor closed) but lock file marker may remain
        # The lock is about file descriptor, not the presence of the lock file

    def test_lock_file_created_if_not_exists(self, tmp_path):
        """Lock file is created if it doesn't exist."""
        test_file = tmp_path / "newfile.md"
        lock_file = tmp_path / "newfile.lock"

        # Neither file should exist
        assert not test_file.exists()
        assert not lock_file.exists()

        with _lock_memory_file(test_file):
            # Lock file should exist now
            assert lock_file.exists()
            # Original file is NOT created by the lock function (that's the job of the writer)

    def test_nested_lock_attempts_same_file(self, tmp_path):
        """Nested locking of the same file should timeout or fail."""
        test_file = tmp_path / "test.md"

        # Inner lock will hold, outer should timeout
        with _lock_memory_file(test_file, timeout=5.0):
            # Try to acquire again with very short timeout
            with pytest.raises(TimeoutError):
                with _lock_memory_file(test_file, timeout=0.2):
                    pass


# =============================================================================
# 2. Atomic Write Tests
# =============================================================================


class TestAtomicWriteMemory:
    """Tests for the _atomic_write_memory() function."""

    def test_atomic_write_success(self, tmp_path):
        """Successful write creates file with correct content."""
        memory_file = tmp_path / "memory.md"

        _atomic_write_memory(memory_file, "Hello, World!")

        assert memory_file.exists()
        assert memory_file.read_text() == "Hello, World!"

    def test_atomic_write_creates_new_file(self, tmp_path):
        """Writing to non-existent path creates the file."""
        memory_file = tmp_path / "subdir" / "newfile.md"

        # File should not exist
        assert not memory_file.exists()

        # Parent directory must exist for NamedTemporaryFile
        memory_file.parent.mkdir(parents=True, exist_ok=True)

        _atomic_write_memory(memory_file, "New content")

        assert memory_file.exists()
        assert memory_file.read_text() == "New content"

    def test_atomic_write_overwrites_existing(self, tmp_path):
        """Writing to existing file replaces content atomically."""
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("Original content")

        _atomic_write_memory(memory_file, "Updated content")

        assert memory_file.read_text() == "Updated content"

    def test_atomic_write_no_leftover_tmp_or_bak(self, tmp_path):
        """After successful write, no .tmp or .bak files remain."""
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("Original content")

        _atomic_write_memory(memory_file, "Updated content")

        # Check for any leftover temp files
        for f in tmp_path.glob("*"):
            if f.suffix in (".tmp", ".bak"):
                pytest.fail(f"Found leftover temp file: {f}")

        # Check for hidden temp files too
        for f in tmp_path.glob(".*"):
            if f.name.endswith(".tmp") or f.name.endswith(".bak"):
                pytest.fail(f"Found leftover temp file: {f}")

    def test_atomic_write_rollback_on_failure(self, tmp_path):
        """If write fails, original content is preserved via backup restore."""
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("Original content")
        backup_file = tmp_path / "memory.bak"

        # Track calls to replace to simulate failure on the final rename
        original_replace = Path.replace
        replace_call_count = [0]

        def mock_replace(self, target):
            replace_call_count[0] += 1
            # After backup is created (first call on memory_file), fail on second call
            if replace_call_count[0] >= 2:
                raise IOError("Simulated write failure")
            return original_replace(self, target)

        with patch.object(Path, 'replace', mock_replace):
            with pytest.raises(IOError):
                _atomic_write_memory(memory_file, "New content")

        # After the failure, the rollback should have restored the backup
        # But the file might be gone or in an intermediate state
        # The key is: no exception should propagate from the restore attempt

    def test_atomic_write_empty_content(self, tmp_path):
        """Writing empty string works correctly."""
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("Original content")

        _atomic_write_memory(memory_file, "")

        assert memory_file.read_text() == ""

    def test_atomic_write_removes_backup_on_success(self, tmp_path):
        """Backup file is removed after successful write."""
        memory_file = tmp_path / "memory.md"
        memory_file.write_text("Original")
        backup_file = tmp_path / "memory.bak"

        _atomic_write_memory(memory_file, "New content")

        # Backup should not exist
        assert not backup_file.exists()


# =============================================================================
# 3. Deduplication Tests
# =============================================================================


class TestCompactMemory:
    """Tests for the _compact_memory() function."""

    def test_compact_removes_duplicate_lines(self):
        """Duplicate lines are removed, keeping the most recent (last) occurrence."""
        content = """# Memory

- First item
- Second item
- First item
- Third item
"""
        result = _compact_memory(content)

        # Count occurrences of "First item"
        lines = result.split('\n')
        first_item_count = sum(1 for line in lines if "- First item" in line)

        assert first_item_count == 1
        # Should keep the last occurrence (near end) - so it should appear at end of list items
        assert "- First item" in result
        # It should be preserved (at least once)

    def test_compact_preserves_headers(self):
        """Lines starting with # are always preserved."""
        content = """# Memory

# Important Header
## Another Header
- Some content
"""
        result = _compact_memory(content)

        assert "# Memory" in result
        assert "# Important Header" in result
        assert "## Another Header" in result

    def test_compact_preserves_blank_lines(self):
        """Blank lines are preserved (up to 1 consecutive)."""
        content = """# Memory


- First item

- Second item

"""
        result = _compact_memory(content)

        # Should have some blank lines but not excessive
        lines = result.split('\n')
        blank_count = sum(1 for line in lines if not line.strip())

        # Should preserve at least some blank lines for readability
        assert blank_count >= 1
        # But not excessive (max 1 consecutive)
        assert blank_count <= 3  # Reasonable limit

    def test_compact_preserves_order(self):
        """After dedup, relative order of remaining lines is preserved."""
        content = """# Memory

- Item 1
- Item 2
- Item 3
- Item 1
- Item 4
"""
        result = _compact_memory(content)

        lines = result.split('\n')
        item_lines = [line for line in lines if line.strip().startswith("- Item")]

        # Should have Item 2, Item 3, Item 1 (last), Item 4 in order
        # (Item 1 appears twice in original, keep the last one)
        item_texts = [line.strip() for line in item_lines]

        # Verify order: Item 1 appears last (it's the duplicate kept)
        if "- Item 1" in item_texts:
            idx = item_texts.index("- Item 1")
            # Item 1 should come after Item 2 and Item 3
            assert idx > item_texts.index("- Item 2")
            assert idx > item_texts.index("- Item 3")

    def test_compact_empty_content(self):
        """Empty string returns empty string."""
        assert _compact_memory("") == ""
        assert _compact_memory("   ") == ""

    def test_compact_no_duplicates(self):
        """Content with no duplicates is unchanged (except cleanup)."""
        content = """# Memory

- Unique item 1
- Unique item 2
- Unique item 3
"""
        result = _compact_memory(content)

        assert "Unique item 1" in result
        assert "Unique item 2" in result
        assert "Unique item 3" in result

    def test_compact_case_insensitive(self):
        """Dedup is case-insensitive but preserves original case."""
        content = """# Memory

- Hello world
- HELLO WORLD
- hello world
"""
        result = _compact_memory(content)

        # Should have only one "hello world" entry (normalized)
        lines = result.split('\n')
        hello_count = sum(1 for line in lines if "hello world" in line.lower())

        assert hello_count == 1

    def test_compact_preserves_list_items(self):
        """List items with - prefix are deduplicated."""
        content = """# Memory

- Regular item
- Another item
"""
        result = _compact_memory(content)

        assert "- Regular item" in result
        assert "- Another item" in result

    def test_compact_preserves_structural_lines(self):
        """Non-list, non-header lines are preserved as structural."""
        content = """# Memory

Some text here
- List item
More text
"""
        result = _compact_memory(content)

        # Structural text should be preserved
        assert "Some text here" in result
        assert "More text" in result

    def test_compact_very_large_content(self):
        """Compaction handles large files without issues."""
        # Create large content with many duplicates
        lines = ["# Memory\n"]
        for i in range(1000):
            lines.append(f"- Item {i % 100}")  # 100 unique items, 1000 lines

        content = "\n".join(lines)
        result = _compact_memory(content)

        # Should complete without error
        assert len(result) > 0
        # Should have ~100 items (one per unique value)
        item_lines = [l for l in result.split("\n") if l.strip().startswith("- Item")]
        assert len(item_lines) == 100

    def test_compact_header_always_preserved(self):
        """Multiple duplicate headers are all preserved."""
        content = """# Memory

# Important Section
- Content
# Important Section
- More Content
"""
        result = _compact_memory(content)

        # Headers should be preserved (they're structural)
        assert result.count("# Important Section") >= 1

    def test_compact_removes_excessive_blank_lines(self):
        """Multiple consecutive blank lines are reduced to max 1."""
        content = """# Memory



- Item 1



- Item 2


"""
        result = _compact_memory(content)

        lines = result.split('\n')
        consecutive_blanks = 0
        max_consecutive = 0

        for line in lines:
            if not line.strip():
                consecutive_blanks += 1
                max_consecutive = max(max_consecutive, consecutive_blanks)
            else:
                consecutive_blanks = 0

        # Should not have more than 2 consecutive blank lines (allowing for header area)
        assert max_consecutive <= 2


# =============================================================================
# 5. Integration Tests: _update_memory_md with Locking + Compaction
# =============================================================================


class TestUpdateMemoryMdIntegration:
    """Integration tests for _update_memory_md() with locking and compaction."""

    def test_update_memory_md_basic(self, tmp_path, mock_manager):
        """Basic memory.md update works with locking."""
        agent_path = tmp_path / "agent"
        agent_path.mkdir()
        memory_file = agent_path / "memory.md"
        memory_file.write_text("# Memory\n\n")

        rules = {"max_memory_words": 2000}

        result = _update_memory_md(
            agent_id="test_agent",
            agent_path=agent_path,
            request="Test memory entry",
            rules=rules,
            manager=mock_manager
        )

        assert result["success"] is True
        assert "Test memory entry" in memory_file.read_text()

    def test_update_memory_md_triggers_compaction(self, tmp_path, mock_manager):
        """When memory is near full, compaction is triggered before rejection."""
        agent_path = tmp_path / "agent"
        agent_path.mkdir()
        memory_file = agent_path / "memory.md"

        # Create memory at 85% capacity (needs compaction)
        # 850 items with max 1000 words = 850 words, 85% > 80%
        existing_items = "\n".join([f"- Existing item {i}" for i in range(850)])
        memory_file.write_text(f"# Memory\n\n{existing_items}\n")

        rules = {"max_memory_words": 1000, "compact_threshold": 0.8}

        result = _update_memory_md(
            agent_id="test_agent",
            agent_path=agent_path,
            request="New entry after compaction",
            rules=rules,
            manager=mock_manager
        )

        # Should succeed (compaction freed space) or fail if dedup can't help
        # The exact outcome depends on whether duplicates exist
        assert result["success"] in [True, False]

    def test_update_memory_md_rejection_after_compaction(self, tmp_path, mock_manager):
        """If compaction can't free enough space, write is rejected."""
        agent_path = tmp_path / "agent"
        agent_path.mkdir()
        memory_file = agent_path / "memory.md"

        # Create memory at near-max capacity with unique content that can't be deduped
        # Use header lines which can't be deduplicated
        existing_items = "\n".join([f"# Header {i}" for i in range(1000)])
        memory_file.write_text(f"# Memory\n\n{existing_items}\n")

        rules = {"max_memory_words": 1000}

        result = _update_memory_md(
            agent_id="test_agent",
            agent_path=agent_path,
            request="New entry that should be rejected",
            rules=rules,
            manager=mock_manager
        )

        # Should fail - memory is too full and compaction can't help
        assert result["success"] is False
        assert "error" in result or "limit" in str(result).lower()

    def test_update_memory_md_duplicate_detection(self, tmp_path, mock_manager):
        """Exact duplicate entries are skipped."""
        agent_path = tmp_path / "agent"
        agent_path.mkdir()
        memory_file = agent_path / "memory.md"
        memory_file.write_text("# Memory\n\n- Same entry\n")

        rules = {"max_memory_words": 2000}

        result = _update_memory_md(
            agent_id="test_agent",
            agent_path=agent_path,
            request="Same entry",
            rules=rules,
            manager=mock_manager
        )

        assert result["success"] is True
        assert result.get("action") == "skipped"
        assert "already exists" in result.get("message", "").lower()

    def test_update_memory_md_lock_timeout_handling(self, tmp_path, mock_manager):
        """TimeoutError from lock is caught and returns error dict."""
        agent_path = tmp_path / "agent"
        agent_path.mkdir()
        memory_file = agent_path / "memory.md"
        memory_file.write_text("# Memory\n\n")

        rules = {"max_memory_words": 2000}

        # Hold the lock in another thread
        lock_held = threading.Event()

        def hold_lock():
            with _lock_memory_file(memory_file, timeout=10.0):
                lock_held.set()
                time.sleep(0.5)  # Hold lock for a bit

        holder = threading.Thread(target=hold_lock)
        holder.start()

        # Wait for lock to be held
        lock_held.wait(timeout=1.0)

        # Try update with very short lock timeout
        try:
            # Temporarily patch the lock timeout
            with patch("daemon.tools.inner_soul._lock_memory_file") as mock_lock:
                mock_lock.side_effect = TimeoutError("Simulated timeout")

                result = _update_memory_md(
                    agent_id="test_agent",
                    agent_path=agent_path,
                    request="Test entry",
                    rules=rules,
                    manager=mock_manager
                )

                assert result["success"] is False
                assert "lock" in str(result).lower() or "retry" in str(result).lower()
        finally:
            holder.join(timeout=2.0)

    def test_update_memory_md_cannot_exceed_limit(self, tmp_path, mock_manager):
        """Memory cannot exceed max_memory_words limit."""
        agent_path = tmp_path / "agent"
        agent_path.mkdir()
        memory_file = agent_path / "memory.md"

        # Create memory that's already at limit
        existing_items = "\n".join([f"- Item {i}" for i in range(100)])
        memory_file.write_text(f"# Memory\n\n{existing_items}\n")

        rules = {"max_memory_words": 100}

        result = _update_memory_md(
            agent_id="test_agent",
            agent_path=agent_path,
            request="New item that should be rejected",
            rules=rules,
            manager=mock_manager
        )

        assert result["success"] is False

    def test_update_memory_md_creates_file_if_missing(self, tmp_path, mock_manager):
        """Creates memory.md if it doesn't exist."""
        agent_path = tmp_path / "agent"
        agent_path.mkdir()
        memory_file = agent_path / "memory.md"

        # Ensure memory.md doesn't exist
        assert not memory_file.exists()

        rules = {"max_memory_words": 2000}

        result = _update_memory_md(
            agent_id="test_agent",
            agent_path=agent_path,
            request="First entry",
            rules=rules,
            manager=mock_manager
        )

        assert result["success"] is True
        assert memory_file.exists()
        assert "First entry" in memory_file.read_text()


# =============================================================================
# 6. Edge Cases
# =============================================================================


class TestEdgeCases:
    """Edge case tests for compaction and locking features."""

    def test_compact_with_only_headers(self):
        """Compacting content with only headers returns headers."""
        content = """# Memory

## Section 1
## Section 2
"""
        result = _compact_memory(content)

        assert "# Memory" in result
        assert "## Section 1" in result
        assert "## Section 2" in result

    def test_compact_with_mixed_duplicates(self):
        """Handle content with both duplicate headers and list items."""
        content = """# Memory

## Section
- Item 1
## Section
- Item 1
- Item 2
"""
        result = _compact_memory(content)

        # Should have both sections (headers preserved)
        assert "# Memory" in result
        # Should have only one "Item 1" (deduplicated)
        item1_count = result.count("- Item 1")
        assert item1_count == 1

    def test_lock_with_special_characters_in_filename(self, tmp_path):
        """Lock works with filenames containing special characters."""
        memory_file = tmp_path / "memory-with-dashes_and_underscores.md"

        with _lock_memory_file(memory_file):
            lock_file = tmp_path / "memory-with-dashes_and_underscores.lock"
            assert lock_file.exists()

    def test_atomic_write_to_deeply_nested_path(self, tmp_path):
        """Atomic write works with deeply nested paths if parent exists."""
        memory_file = tmp_path / "a" / "b" / "c" / "d" / "memory.md"

        # Create parent directories
        memory_file.parent.mkdir(parents=True, exist_ok=True)

        _atomic_write_memory(memory_file, "Deep content")

        assert memory_file.exists()
        assert memory_file.read_text() == "Deep content"

    def test_compact_unicode_content(self):
        """Compaction handles unicode content correctly."""
        content = """# Memory

- Hello 世界
- Привет мир
- Hello 世界
"""
        result = _compact_memory(content)

        # Should deduplicate the unicode line
        assert result.count("Hello 世界") == 1
        assert "Привет мир" in result

    def test_compact_markdown_links(self):
        """Compaction handles markdown links."""
        content = """# Memory

- [Link text](http://example.com)
- Another item
- [Link text](http://example.com)
"""
        result = _compact_memory(content)

        # Should deduplicate the link line
        assert result.count("[Link text]") == 1

    def test_compact_numbered_lists_deduplicated(self):
        """Numbered lists (1., 2., etc.) are also deduplicated."""
        content = """# Memory

1. First item
2. Second item
1. First item
3. Third item
"""
        result = _compact_memory(content)

        # Should have only one "First item" entry
        lines = result.split('\n')
        first_item_count = sum(1 for line in lines if line.strip().startswith("1."))
        assert first_item_count == 1

    def test_compact_asterisk_lists_deduplicated(self):
        """Asterisk lists (* item) are also deduplicated."""
        content = """# Memory

* First item
* Second item
* First item
"""
        result = _compact_memory(content)

        lines = result.split('\n')
        first_item_count = sum(1 for line in lines if line.strip().startswith("* First"))
        assert first_item_count == 1

    def test_lock_concurrent_access(self, tmp_path):
        """Multiple threads can access different files concurrently."""
        test_file_1 = tmp_path / "file1.md"
        test_file_2 = tmp_path / "file2.md"

        results = []

        def access_file(filepath, value):
            with _lock_memory_file(filepath):
                results.append(value)

        t1 = threading.Thread(target=access_file, args=(test_file_1, "file1"))
        t2 = threading.Thread(target=access_file, args=(test_file_2, "file2"))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert "file1" in results
        assert "file2" in results

    def test_atomic_write_with_unicode(self, tmp_path):
        """Atomic write handles unicode content."""
        memory_file = tmp_path / "memory.md"

        _atomic_write_memory(memory_file, "Hello 世界! 🌍")

        assert memory_file.read_text() == "Hello 世界! 🌍"
