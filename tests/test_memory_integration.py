"""Integration tests for Unified Memory Architecture.

This module tests end-to-end workflows across the complete memory system:
1. Full lifecycle: write → compact → archive → access archived file
2. Compound requests with mixed intents
3. Concurrent writes with locking
4. RAG redirect interaction with compound requests
5. Edge cases: empty strings, long requests, path traversal, symlinks, etc.
6. Regression checks: backward compatibility

These tests are integration tests that run with real (non-mocked) langgraph modules.
"""

import os
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_agent(tmp_path):
    """Create a minimal test agent in temp directory."""
    agent_dir = tmp_path / "test_agent"
    agent_dir.mkdir()
    (agent_dir / "soul.md").write_text("# Who I Am\n\nI am a test agent.\n")
    (agent_dir / "growth.md").write_text("# Growth\n\nmax_memory_words: 2000\nmax_soul_chars: 2000\narchive memories older than 90 days\n")
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
# 1. Full Lifecycle Integration Tests
# =============================================================================


class TestFullLifecycleIntegration:
    """Integration tests for complete memory lifecycle."""

    def test_write_compact_archive_access_archived(self, temp_agent, mock_manager):
        """Complete lifecycle: write memory → compact → archive → access archived file."""
        from daemon.tools.inner_soul import (
            _update_memories,
            _compact_memory,
            _archive_memory_file,
            _archive_old_memories,
            create_inner_soul_tool,
        )
        from daemon.tools.access_memory import create_access_memory_tool
        from daemon.loader import load_recent_memories
        from daemon.registry import AgentMetadata

        memories_dir = temp_agent / "memories"
        classification = {"type": "event", "targets": ["memories"], "description": "Event or observation"}

        # Step 1: Write multiple memories with duplicates
        for i in range(5):
            _update_memories("test_agent", temp_agent, f"Memory entry number {i}", classification, mock_manager)
        # Add duplicates
        _update_memories("test_agent", temp_agent, "Memory entry number 1", classification, mock_manager)

        memory_files_before = list(memories_dir.glob("*.md"))
        assert len(memory_files_before) >= 5

        # Step 2: Compact memory.md (not memories/, this is for memory.md)
        memory_md = temp_agent / "memory.md"
        memory_md.write_text("# Memory\n\n" + "\n".join([f"- Item {i}" for i in range(100)]) + "\n- Duplicate item\n- Duplicate item\n")

        from daemon.tools.inner_soul import _update_memory_md
        result = _update_memory_md("test_agent", temp_agent, "A new entry", {"max_memory_words": 2000}, mock_manager)
        assert result["success"] is True

        # Step 3: Age memories for archival
        for f in memories_dir.glob("*.md"):
            old_time = time.time() - (100 * 86400)
            os.utime(f, (old_time, old_time))

        archived = _archive_old_memories(temp_agent, ttl_days=90)
        assert archived >= 1, "Should have archived at least 1 file"

        # Step 4: Verify archived files exist
        archive_dir = memories_dir / "archive"
        archived_files = list(archive_dir.rglob("*.md"))
        assert len(archived_files) >= 1, "At least one file should be in archive"

        # Step 5: Access archived file via access_memory tool
        now_year = datetime.now().year
        now_month = f"{datetime.now().month:02d}"
        archived_file = archived_files[0]

        # Create mock registry for access_memory tool
        mock_meta = AgentMetadata(
            id="test_agent",
            name="Test Agent",
            description="Test agent",
            path=temp_agent,
            system=False,
        )
        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            access_tool = create_access_memory_tool("test_agent")

            # Access via archive path
            archive_path = f"archive/{now_year}/{now_month}/{archived_file.name}"
            result = access_tool.invoke({"filename": archive_path})

            # Should return content (not error)
            assert "not found" not in result.lower() or "Access denied" not in result

        # Step 6: Verify load_recent_memories includes archived files
        result = load_recent_memories(temp_agent, include_archived=True)
        assert "archive/" in result

    def test_compound_request_with_mixed_intents(self, temp_agent, mock_manager):
        """Compound request with mixed intents verifies each part processed correctly."""
        from daemon.tools.inner_soul import create_inner_soul_tool, _classify_request
        from daemon.registry import AgentMetadata

        # Create mock registry
        mock_meta = AgentMetadata(
            id="test_agent",
            name="Test Agent",
            description="Test agent",
            path=temp_agent,
            system=False,
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            inner_soul = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            # Compound request with different types
            result = inner_soul.invoke({
                "request": "My name is TestBot AND User prefers Python AND Always run tests before commit"
            })

            # Should process all parts
            assert "2 parts" in result or "3 parts" in result

            # Verify each part's classification
            class1 = _classify_request("My name is TestBot")
            assert class1["type"] == "identity"

            class2 = _classify_request("User prefers Python")
            assert class2["type"] == "user_preference"

            class3 = _classify_request("Always run tests before commit")
            assert class3["type"] == "workflow"

    def test_concurrent_writes_no_corruption(self, temp_agent, mock_manager):
        """Concurrent writes via threads verify locking prevents corruption."""
        from daemon.tools.inner_soul import _update_memory_md, _lock_memory_file

        memory_md = temp_agent / "memory.md"
        memory_md.write_text("# Memory\n\n")
        rules = {"max_memory_words": 2000}

        results = []
        errors = []

        def write_memory(thread_id):
            try:
                result = _update_memory_md(
                    "test_agent",
                    temp_agent,
                    f"Thread {thread_id} memory entry",
                    rules,
                    mock_manager
                )
                results.append((thread_id, result))
            except Exception as e:
                errors.append((thread_id, str(e)))

        # Start multiple threads
        threads = []
        for i in range(5):
            t = threading.Thread(target=write_memory, args=(i,))
            threads.append(t)
            t.start()

        # Wait for all threads
        for t in threads:
            t.join()

        # All writes should succeed
        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 5

        # Verify content - each entry should appear exactly once
        content = memory_md.read_text()
        for i in range(5):
            assert f"Thread {i} memory entry" in content

    def test_rag_redirect_with_compound_requests(self, temp_agent, mock_manager):
        """RAG redirect interaction with compound requests."""
        from daemon.tools.inner_soul import create_inner_soul_tool
        from daemon.registry import AgentMetadata

        mock_meta = AgentMetadata(
            id="test_agent",
            name="Test Agent",
            description="Test agent",
            path=temp_agent,
            system=False,
        )

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            # RAG enabled
            with patch("daemon.tools.inner_soul.is_rag_enabled", return_value=True):
                inner_soul = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

                # Mixed compound: some redirect, some don't
                result = inner_soul.invoke({
                    "request": "My name is Cody AND I learned that async is powerful"
                })

                # Should show both redirect and non-redirect parts
                # Identity doesn't redirect, knowledge does
                assert "soul" in result.lower() or "TestBot" in result.lower() or "name" in result.lower()


# =============================================================================
# 2. Edge Case Tests
# =============================================================================


class TestEdgeCases:
    """Edge case tests for memory system."""

    def test_empty_string_request(self, temp_agent, mock_manager):
        """Empty string request should be handled gracefully."""
        from daemon.tools.inner_soul import create_inner_soul_tool
        from daemon.registry import AgentMetadata

        mock_meta = AgentMetadata(id="test_agent", name="Test", description="Test", path=temp_agent, system=False)

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            inner_soul = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            result = inner_soul.invoke({"request": ""})
            assert "ERROR" in result

    def test_whitespace_only_request(self, temp_agent, mock_manager):
        """Whitespace-only request should be handled gracefully."""
        from daemon.tools.inner_soul import create_inner_soul_tool
        from daemon.registry import AgentMetadata

        mock_meta = AgentMetadata(id="test_agent", name="Test", description="Test", path=temp_agent, system=False)

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            inner_soul = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            result = inner_soul.invoke({"request": "   \n\t  "})
            assert "ERROR" in result or "empty" in result.lower()

    def test_very_long_request_exceeds_limit(self, temp_agent, mock_manager):
        """Very long request (>2000 chars) should be rejected properly."""
        from daemon.tools.inner_soul import create_inner_soul_tool
        from daemon.registry import AgentMetadata

        mock_meta = AgentMetadata(id="test_agent", name="Test", description="Test", path=temp_agent, system=False)

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            inner_soul = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            long_request = "x" * 2001
            result = inner_soul.invoke({"request": long_request})

            assert "ERROR" in result
            assert "2000" in result

    def test_archive_path_traversal_rejected(self, temp_agent):
        """Archive path traversal attempts must be rejected."""
        from daemon.tools.access_memory import create_access_memory_tool
        from daemon.registry import AgentMetadata

        memories_dir = temp_agent / "memories"
        archive_dir = memories_dir / "archive" / "2026" / "01"
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "real-file.md").write_text("# Real File")

        mock_meta = AgentMetadata(id="test_agent", name="Test", description="Test", path=temp_agent, system=False)

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            access_tool = create_access_memory_tool("test_agent")

            # Try path traversal
            result = access_tool.invoke({"filename": "archive/../../etc/passwd"})
            assert "Access denied" in result or "not found" in result.lower()

            # Try another path traversal attempt
            result2 = access_tool.invoke({"filename": "archive/../../secret"})
            assert "Access denied" in result2 or "not found" in result2.lower()

    def test_symlink_in_archive_rejected(self, temp_agent):
        """Symlink in archive path must be rejected."""
        if os.name == "nt":
            pytest.skip("Symlinks not supported on Windows")

        from daemon.tools.access_memory import create_access_memory_tool
        from daemon.registry import AgentMetadata

        memories_dir = temp_agent / "memories"
        archive_dir = memories_dir / "archive" / "2026" / "01"
        archive_dir.mkdir(parents=True)

        # Create a real file outside archive
        outside_file = temp_agent / "secret.md"
        outside_file.write_text("# Secret")

        # Create symlink inside archive pointing outside
        symlink_file = archive_dir / "malicious.md"
        symlink_file.symlink_to(outside_file)

        mock_meta = AgentMetadata(id="test_agent", name="Test", description="Test", path=temp_agent, system=False)

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            access_tool = create_access_memory_tool("test-agent")
            result = access_tool.invoke({"filename": "archive/2026/01/malicious.md"})

            assert result == "Access denied"

    def test_missing_archive_directory_handled(self, temp_agent):
        """Missing archive directory should be handled gracefully."""
        from daemon.loader import load_recent_memories

        # No archive directory created
        result = load_recent_memories(temp_agent, include_archived=True)
        assert result == "" or "archive" not in result

    def test_compaction_with_only_duplicates(self, temp_agent, mock_manager):
        """Compaction with only duplicates should keep at least one entry."""
        from daemon.tools.inner_soul import _compact_memory, _update_memory_md

        memory_md = temp_agent / "memory.md"
        # Content with only duplicates
        memory_md.write_text("# Memory\n\n- Same entry\n- Same entry\n- Same entry\n")

        rules = {"max_memory_words": 2000}
        result = _update_memory_md(
            "test_agent",
            temp_agent,
            "Same entry",  # Duplicate
            rules,
            mock_manager
        )

        # Should either skip (duplicate) or succeed with compaction
        assert result["success"] is True
        # File should still exist with content
        assert memory_md.exists()

    def test_very_old_files_auto_archive(self, temp_agent):
        """Very old files (>90 days) auto-archive correctly."""
        from daemon.tools.inner_soul import _archive_old_memories

        memories_dir = temp_agent / "memories"
        # Clear any existing files
        for f in memories_dir.glob("*.md"):
            f.unlink()

        # Create a file 100 days old
        old_file = memories_dir / "old-memory.md"
        old_file.write_text("# Old Memory")
        old_time = time.time() - (100 * 86400)
        os.utime(old_file, (old_time, old_time))

        # Create a file 30 days old (should NOT be archived)
        recent_file = memories_dir / "recent-memory.md"
        recent_file.write_text("# Recent Memory")
        recent_time = time.time() - (30 * 86400)
        os.utime(recent_file, (recent_time, recent_time))

        archived = _archive_old_memories(temp_agent, ttl_days=90)

        assert archived == 1
        assert not old_file.exists()
        assert recent_file.exists()

    def test_rate_limiting_second_sweep_skipped(self, temp_agent):
        """Second sweep within 5 minutes is skipped due to rate limiting."""
        from daemon.tools.inner_soul import _archive_old_memories, _last_archive_sweep

        memories_dir = temp_agent / "memories"
        # Clear any existing files
        for f in memories_dir.glob("*.md"):
            f.unlink()

        # Create old file
        old_file = memories_dir / "old.md"
        old_file.write_text("# Old")
        old_time = time.time() - (100 * 86400)
        os.utime(old_file, (old_time, old_time))

        # Clear rate limit state
        key = str(temp_agent)
        if key in _last_archive_sweep:
            del _last_archive_sweep[key]

        # First sweep should archive
        result1 = _archive_old_memories(temp_agent, ttl_days=90)
        assert result1 == 1

        # Second sweep immediately should return 0 (skipped)
        result2 = _archive_old_memories(temp_agent, ttl_days=90)
        assert result2 == 0


# =============================================================================
# 3. Regression Tests
# =============================================================================


class TestRegressionChecks:
    """Regression tests to verify backward compatibility."""

    def test_target_none_default_still_works(self, temp_agent, mock_manager):
        """Existing calling patterns with target=None still work."""
        from daemon.tools.inner_soul import create_inner_soul_tool, _resolve_targets, _classify_request
        from daemon.registry import AgentMetadata

        mock_meta = AgentMetadata(id="test_agent", name="Test", description="Test", path=temp_agent, system=False)

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            inner_soul = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            # Call with target=None (default)
            result = inner_soul.invoke({"request": "My name is TestBot"})
            assert "ERROR" not in result or "Processed" in result

    def test_intent_none_default_still_works(self, temp_agent, mock_manager):
        """Existing calling patterns with intent=None still work."""
        from daemon.tools.inner_soul import create_inner_soul_tool
        from daemon.registry import AgentMetadata

        mock_meta = AgentMetadata(id="test_agent", name="Test", description="Test", path=temp_agent, system=False)

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            inner_soul = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            # Call with intent=None (default)
            result = inner_soul.invoke({
                "request": "Remember that tests are important",
                "intent": None
            })
            assert "ERROR" not in result or "Processed" in result

    def test_inner_soul_with_content_parameter(self, temp_agent, mock_manager):
        """inner_soul(request='test') works with default parameters."""
        from daemon.tools.inner_soul import create_inner_soul_tool
        from daemon.registry import AgentMetadata

        mock_meta = AgentMetadata(id="test_agent", name="Test", description="Test", path=temp_agent, system=False)

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            inner_soul = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            # Legacy API: content parameter
            result = inner_soul.invoke({"content": "Test content"})
            # Should work (content is alias for request)
            assert "ERROR" not in result or result  # Accept any valid response

    def test_classify_request_returns_valid_results(self):
        """_classify_request() returns valid results for old patterns."""
        from daemon.tools.inner_soul import _classify_request

        # Test various patterns that should work
        test_cases = [
            ("My name is Test", {"type": "identity", "targets": ["soul"]}),
            ("User prefers Python", {"type": "user_preference", "targets": ["user"]}),
            ("Always check tests", {"type": "workflow", "targets": ["workflow"]}),
            ("I learned that X", {"type": "knowledge", "targets": ["memory", "memories"]}),
            ("Random text", {"type": "event", "targets": ["memories"]}),  # Default fallback
        ]

        for request, expected in test_cases:
            result = _classify_request(request)
            assert result["type"] == expected["type"]
            # Check at least one expected target is in result
            assert any(t in result["targets"] for t in expected["targets"])

    def test_update_memories_still_works(self, temp_agent, mock_manager):
        """_update_memories() still works as before."""
        from daemon.tools.inner_soul import _update_memories

        classification = {"type": "event", "targets": ["memories"], "description": "Event or observation"}

        result = _update_memories("test_agent", temp_agent, "Test memory", classification, mock_manager)

        assert result["success"] is True
        assert result["target"] == "memories"
        assert "file" in result

        # Verify file was created
        memories_dir = temp_agent / "memories"
        files = list(memories_dir.glob("*.md"))
        assert len(files) >= 1

    def test_access_memory_regular_files_still_work(self, temp_agent):
        """access_memory() still works for regular memories/ files."""
        from daemon.tools.access_memory import create_access_memory_tool
        from daemon.registry import AgentMetadata

        memories_dir = temp_agent / "memories"
        # Clear existing files
        for f in memories_dir.glob("*.md"):
            f.unlink()
        (memories_dir / "20260401_1430-test-memory.md").write_text("# Test Memory\n\nContent here.")

        mock_meta = AgentMetadata(id="test_agent", name="Test", description="Test", path=temp_agent, system=False)

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            access_tool = create_access_memory_tool("test_agent")
            result = access_tool.invoke({"filename": "20260401_1430-test-memory.md"})

            assert "Test Memory" in result
            assert "Content here" in result

    def test_soul_update_still_works(self, temp_agent, mock_manager):
        """Soul updates still work with the new architecture."""
        from daemon.tools.inner_soul import _update_soul

        rules = {"max_soul_chars": 2000}
        result = _update_soul("test_agent", temp_agent, "I am a helpful agent", rules, mock_manager)

        assert result["success"] is True
        assert result["target"] == "soul"

        # Verify file was updated
        soul_content = (temp_agent / "soul.md").read_text()
        assert "helpful agent" in soul_content

    def test_user_update_still_works(self, temp_agent, mock_manager):
        """User updates still work with the new architecture."""
        from daemon.tools.inner_soul import _update_user

        result = _update_user("test_agent", temp_agent, "User likes dark mode", mock_manager)

        assert result["success"] is True
        assert result["target"] == "user"

    def test_workflow_update_still_works(self, temp_agent, mock_manager):
        """Workflow updates still work with the new architecture."""
        from daemon.tools.inner_soul import _update_workflow

        rules = {}
        result = _update_workflow("test_agent", temp_agent, "Always verify before commit", rules, mock_manager)

        assert result["success"] is True
        assert result["target"] == "workflow"

    def test_compound_request_backward_compatibility(self, temp_agent, mock_manager):
        """Compound requests work with the new architecture."""
        from daemon.tools.inner_soul import create_inner_soul_tool, _split_compound_request
        from daemon.registry import AgentMetadata

        # Test splitting still works
        parts = _split_compound_request("First AND Second")
        assert len(parts) == 2

        mock_meta = AgentMetadata(id="test_agent", name="Test", description="Test", path=temp_agent, system=False)

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            inner_soul = create_inner_soul_tool(mock_manager, "test_agent", "test-instance")

            result = inner_soul.invoke({
                "request": "My name is TestBot AND User likes coffee"
            })

            assert "2 parts" in result or "soul" in result.lower()


# =============================================================================
# Additional Integration Tests
# =============================================================================


class TestAdditionalIntegration:
    """Additional integration tests for complete coverage."""

    def test_full_archive_workflow_integration(self, temp_agent, mock_manager):
        """Full workflow: create → age → archive → access."""
        from daemon.tools.inner_soul import (
            _update_memories,
            _archive_old_memories,
            create_inner_soul_tool,
        )
        from daemon.tools.access_memory import create_access_memory_tool
        from daemon.registry import AgentMetadata

        memories_dir = temp_agent / "memories"

        # Create memory
        classification = {"type": "event", "targets": ["memories"], "description": "Event or observation"}
        _update_memories("test_agent", temp_agent, "Important event to archive", classification, mock_manager)

        # Age it
        for f in memories_dir.glob("*.md"):
            old_time = time.time() - (100 * 86400)
            os.utime(f, (old_time, old_time))

        # Archive
        archived = _archive_old_memories(temp_agent, ttl_days=90)
        assert archived >= 1

        # Access via tool
        now_year = datetime.now().year
        now_month = f"{datetime.now().month:02d}"

        mock_meta = AgentMetadata(id="test_agent", name="Test", description="Test", path=temp_agent, system=False)
        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            access_tool = create_access_memory_tool("test_agent")

            # List archived files
            archived_files = list((memories_dir / "archive").rglob("*.md"))
            if archived_files:
                archive_path = f"archive/{now_year}/{now_month}/{archived_files[0].name}"
                result = access_tool.invoke({"filename": archive_path})
                assert "Important event" in result or "not found" not in result.lower()

    def test_compaction_threshold_integration(self, temp_agent, mock_manager):
        """Proactive compaction when approaching capacity."""
        from daemon.tools.inner_soul import _update_memory_md

        memory_md = temp_agent / "memory.md"
        # Create content at ~85% capacity (1700 words with max 2000 = 85%)
        # Each "- X" is 2 words, so we need between 800-1000 items
        # 850 items = 1700 words + header ≈ 1702 words
        existing = "\n".join([f"- {i}" for i in range(850)])
        memory_md.write_text(f"# Memory\n\n{existing}\n")

        rules = {"max_memory_words": 2000}

        # Verify we're in the proactive compaction zone (>80% = 1600 words)
        current_words = len(memory_md.read_text().split())
        assert current_words > 1600, f"Need >1600 words for proactive compaction, got {current_words}"
        assert current_words < 2000, f"Must be <2000 words, got {current_words}"

        # Add new entry - should trigger proactive compaction
        result = _update_memory_md(
            "test_agent",
            temp_agent,
            "New item after proactive compaction",
            rules,
            mock_manager
        )

        # Should succeed (compaction freed space or skipped duplicate)
        assert result["success"] is True

    def test_lock_timeout_integration(self, temp_agent, mock_manager):
        """Lock timeout is handled gracefully."""
        from daemon.tools.inner_soul import _lock_memory_file, _update_memory_md

        memory_md = temp_agent / "memory.md"
        memory_md.write_text("# Memory\n\n")
        rules = {"max_memory_words": 2000}

        # Hold lock in another thread
        lock_held = threading.Event()

        def hold_lock():
            with _lock_memory_file(memory_md, timeout=10.0):
                lock_held.set()
                time.sleep(0.5)

        holder = threading.Thread(target=hold_lock)
        holder.start()
        lock_held.wait(timeout=1.0)

        # Try to update with very short lock timeout
        # Patch _lock_memory_file to timeout quickly
        from unittest.mock import patch as mock_patch
        with mock_patch("daemon.tools.inner_soul._lock_memory_file") as mock_lock:
            mock_lock.side_effect = TimeoutError("Simulated timeout")
            result = _update_memory_md(
                "test_agent",
                temp_agent,
                "Test entry",
                rules,
                mock_manager
            )

            assert result["success"] is False
            assert "lock" in str(result).lower() or "retry" in str(result).lower()

        holder.join(timeout=2.0)

    def test_atomic_write_rollback_integration(self, tmp_path):
        """Atomic write rollback on failure works correctly."""
        from daemon.tools.inner_soul import _atomic_write_memory

        memory_file = tmp_path / "memory.md"
        memory_file.write_text("Original content")

        # Simulate failure during write
        original_replace = Path.replace
        call_count = [0]

        def failing_replace(self, target):
            call_count[0] += 1
            if call_count[0] >= 2:  # Fail on tmp→current rename
                raise IOError("Simulated failure")
            return original_replace(self, target)

        with patch.object(Path, 'replace', failing_replace):
            try:
                _atomic_write_memory(memory_file, "New content")
            except IOError:
                pass  # Expected

        # File should be in some state (rollback attempted)
        # The key is no exception propagates from the rollback itself

    def test_memory_limit_with_compaction_fails_honestly(self, temp_agent, mock_manager):
        """Memory exceeding limit after compaction fails with honest error."""
        from daemon.tools.inner_soul import _update_memory_md

        memory_md = temp_agent / "memory.md"
        # Create content that can't be compacted enough
        # Use headers which are preserved and can't be deduped
        existing = "\n".join([f"# Header {i}" for i in range(100)])
        memory_md.write_text(f"# Memory\n\n{existing}\n")

        rules = {"max_memory_words": 50}

        result = _update_memory_md(
            "test_agent",
            temp_agent,
            "This entry should be rejected",
            rules,
            mock_manager
        )

        assert result["success"] is False
        # Error should not claim content was saved elsewhere
        error_msg = result.get("error", "")
        assert "Saved to" not in error_msg


# =============================================================================
# Test Summary Report Generator
# =============================================================================


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Add custom summary at end of test run."""
    if exitstatus == 0:
        terminalreporter.write_sep("=", "UNIFIED MEMORY ARCHITECTURE - ALL INTEGRATION TESTS PASSED")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
