"""Tests for the memory system: inner_soul, loader, and access_memory tools."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestSlugify:
    """Tests for _slugify() function."""

    def test_normal_text_produces_hyphenated_slug(self):
        """Normal text with spaces becomes hyphenated."""
        from daemon.tools.inner_soul import _slugify
        assert _slugify("Hello World") == "hello-world"
        assert _slugify("This is a test") == "this-is-a-test"

    def test_max_60_characters(self):
        """Slug is truncated to 60 characters."""
        from daemon.tools.inner_soul import _slugify
        long_text = "a" * 100
        result = _slugify(long_text)
        assert len(result) == 60

    def test_non_ascii_characters_stripped(self):
        """Non-ASCII characters are replaced with hyphens then stripped."""
        from daemon.tools.inner_soul import _slugify
        # Unicode characters are replaced with hyphens, then trailing hyphens stripped
        result = _slugify("Hello 世界")
        # "hello-" after non-ASCII replacement, then trailing hyphen stripped
        assert result == "hello"
        
        # Another example with non-ASCII at the end
        result = _slugify("test_日本語")
        # "test-" after replacement, hyphen stripped
        assert result == "test"

    def test_empty_string_returns_memory(self):
        """Empty string returns default 'memory'."""
        from daemon.tools.inner_soul import _slugify
        assert _slugify("") == "memory"
        assert _slugify("   ") == "memory"

    def test_special_characters_only_returns_memory(self):
        """Only special characters returns 'memory'."""
        from daemon.tools.inner_soul import _slugify
        assert _slugify("!@#$%^&*()") == "memory"

    def test_mixed_alphanumeric_with_spaces(self):
        """Mixed alphanumeric with spaces produces proper hyphenation."""
        from daemon.tools.inner_soul import _slugify
        assert _slugify("Test 123 String") == "test-123-string"
        assert _slugify("ABC xyz 123") == "abc-xyz-123"

    def test_leading_trailing_hyphens_stripped(self):
        """Leading and trailing hyphens are stripped."""
        from daemon.tools.inner_soul import _slugify
        assert _slugify("  hello  ") == "hello"
        assert _slugify("hello---world") == "hello-world"

    def test_very_long_text_truncated(self):
        """Very long text is truncated to 60 chars."""
        from daemon.tools.inner_soul import _slugify
        long_text = "a" * 50 + " " + "b" * 50
        result = _slugify(long_text)
        assert len(result) == 60

    def test_numbers_preserved(self):
        """Numbers are preserved in slug."""
        from daemon.tools.inner_soul import _slugify
        assert _slugify("2024-04-01 meeting") == "2024-04-01-meeting"
        assert _slugify("test123") == "test123"

    def test_underscores_converted_to_hyphens(self):
        """Underscores are converted to hyphens."""
        from daemon.tools.inner_soul import _slugify
        assert _slugify("hello_world_test") == "hello-world-test"

    def test_multiple_consecutive_special_chars(self):
        """Multiple consecutive special chars become single hyphen."""
        from daemon.tools.inner_soul import _slugify
        assert _slugify("hello!!!world---test") == "hello-world-test"


class TestAccessMemoryTool:
    """Tests for the access_memory tool deprecation."""

    def test_reading_valid_memory_file_returns_deprecation(self, tmp_path):
        """Reading a valid memory file now returns deprecation message."""
        from daemon.tools.access_memory import create_access_memory_tool

        # Create agent directory structure
        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create a memory file
        memory_file = memories_dir / "20260401_1430-test-memory.md"
        memory_file.write_text("# Test Memory\n\nThis is test content.")

        # Mock registry at the source module
        mock_meta = MagicMock()
        mock_meta.path = agent_dir

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            tool = create_access_memory_tool("test-agent")
            result = tool.invoke({"filename": "20260401_1430-test-memory.md"})
            
            assert "DEPRECATED" in result
            assert "explore()" in result
            assert "experience()" in result

    def test_path_traversal_via_symlink_returns_deprecation(self, tmp_path):
        """Path traversal attempt now returns deprecation message (security test obsolete)."""
        from daemon.tools.access_memory import create_access_memory_tool

        # Create agent directory structure
        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create a memory file
        memory_file = memories_dir / "20260401_1430-test-memory.md"
        memory_file.write_text("# Test Memory")

        if os.name != 'nt':
            # Create a symlink that points outside memories directory
            outside_file = tmp_path / "outside-passwd.md"
            outside_file.write_text("# Not a memory")
            
            # Create symlink in memories pointing outside
            malicious_symlink = memories_dir / "malicious.md"
            malicious_symlink.symlink_to(outside_file)

            # Mock registry at the source module
            mock_meta = MagicMock()
            mock_meta.path = agent_dir

            with patch("daemon.registry.get_registry") as mock_get_registry:
                mock_registry = MagicMock()
                mock_registry.get.return_value = mock_meta
                mock_get_registry.return_value = mock_registry

                tool = create_access_memory_tool("test-agent")
                
                # Now returns deprecation message instead of "Access denied"
                result = tool.invoke({"filename": "malicious.md"})
                assert "DEPRECATED" in result
                assert "explore()" in result
                assert "experience()" in result

    def test_missing_file_returns_deprecation(self, tmp_path):
        """Missing file now returns deprecation message instead of not found list."""
        from daemon.tools.access_memory import create_access_memory_tool

        # Create agent directory structure
        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create existing memory files
        (memories_dir / "20260401_1430-existing.md").write_text("# Existing")
        (memories_dir / "20260401_1431-another.md").write_text("# Another")

        # Mock registry at the source module
        mock_meta = MagicMock()
        mock_meta.path = agent_dir

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            tool = create_access_memory_tool("test-agent")
            result = tool.invoke({"filename": "nonexistent.md"})
            
            assert "DEPRECATED" in result
            assert "explore()" in result
            assert "experience()" in result

    def test_missing_memories_directory_returns_deprecation(self, tmp_path):
        """Missing memories/ directory now returns deprecation message."""
        from daemon.tools.access_memory import create_access_memory_tool

        # Create agent directory WITHOUT memories
        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()

        # Mock registry at the source module
        mock_meta = MagicMock()
        mock_meta.path = agent_dir

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            tool = create_access_memory_tool("test-agent")
            result = tool.invoke({"filename": "anyfile.md"})
            
            assert "DEPRECATED" in result
            assert "explore()" in result
            assert "experience()" in result

    def test_path_components_in_filename_returns_deprecation(self, tmp_path):
        """Filename with path components now returns deprecation message."""
        from daemon.tools.access_memory import create_access_memory_tool

        # Create agent directory structure
        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create a memory file
        memory_file = memories_dir / "20260401_1430-test-memory.md"
        memory_file.write_text("# Test Memory Content")

        # Mock registry at the source module
        mock_meta = MagicMock()
        mock_meta.path = agent_dir

        with patch("daemon.registry.get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.get.return_value = mock_meta
            mock_get_registry.return_value = mock_registry

            tool = create_access_memory_tool("test-agent")
            
            # Try with path components
            result = tool.invoke({"filename": "subdir/20260401_1430-test-memory.md"})
            
            # Now returns deprecation message instead of file content
            assert "DEPRECATED" in result
            assert "explore()" in result
            assert "experience()" in result


class TestSymlinkHandling:
    """Tests for symlink safety in access_memory (now deprecated)."""

    def test_symlinks_returns_deprecation(self, tmp_path):
        """Symlink access now returns deprecation message."""
        from daemon.tools.access_memory import create_access_memory_tool

        # Create agent directory structure
        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create a real memory file
        real_file = memories_dir / "20260401_1430-real-memory.md"
        real_file.write_text("# Real Memory")

        # Create a symlink to it
        symlink_file = memories_dir / "symlink-memory.md"
        if os.name != 'nt':  # Symlinks don't work well on Windows
            symlink_file.symlink_to(real_file)

            # Mock registry at the source module
            mock_meta = MagicMock()
            mock_meta.path = agent_dir

            with patch("daemon.registry.get_registry") as mock_get_registry:
                mock_registry = MagicMock()
                mock_registry.get.return_value = mock_meta
                mock_get_registry.return_value = mock_registry

                tool = create_access_memory_tool("test-agent")
                
                # Read via symlink - now returns deprecation message
                result = tool.invoke({"filename": "symlink-memory.md"})
                assert "DEPRECATED" in result
                assert "explore()" in result
                assert "experience()" in result


class TestLoadRecentMemories:
    """Tests for load_recent_memories() function."""

    def test_returns_max_5_entries_sorted_reverse_alpha(self, tmp_path):
        """Returns max 5 entries sorted by name (reverse alphabetical = most recent first)."""
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create 7 memory files
        for i in range(7):
            (memories_dir / f"20260401_{1400 + i:04d}-memory-{i}.md").write_text(f"# Memory {i}")

        result = load_recent_memories(agent_dir)
        lines = result.strip().split("\n")
        
        # Should only have 5 entries
        assert len(lines) == 5

    def test_empty_directory_returns_empty_string(self, tmp_path):
        """Empty directory returns empty string."""
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        result = load_recent_memories(agent_dir)
        assert result == ""

    def test_missing_directory_returns_empty_string(self, tmp_path):
        """Missing memories directory returns empty string."""
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        # No memories directory

        result = load_recent_memories(agent_dir)
        assert result == ""

    def test_skips_symlinks(self, tmp_path):
        """Symlinks are skipped."""
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create real file
        real_file = memories_dir / "real-memory.md"
        real_file.write_text("# Real")

        if os.name != 'nt':
            # Create symlink
            symlink_file = memories_dir / "symlink-memory.md"
            symlink_file.symlink_to(real_file)

            result = load_recent_memories(agent_dir)
            
            assert "real-memory.md" in result
            assert "symlink-memory.md" not in result

    def test_only_md_files_included(self, tmp_path):
        """Only .md files are included."""
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create .md file
        (memories_dir / "memory.md").write_text("# Memory")
        # Create non-.md file
        (memories_dir / "readme.txt").write_text("Not a memory")

        result = load_recent_memories(agent_dir)
        
        assert "memory.md" in result
        assert "readme.txt" not in result

    def test_fewer_than_5_files_returns_all(self, tmp_path):
        """If fewer than 5 files, returns all of them."""
        from daemon.loader import load_recent_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        memories_dir = agent_dir / "memories"
        memories_dir.mkdir()

        # Create only 3 files
        for i in range(3):
            (memories_dir / f"20260401_1400-memory-{i}.md").write_text(f"# Memory {i}")

        result = load_recent_memories(agent_dir)
        lines = result.strip().split("\n")
        
        assert len(lines) == 3


class TestCacheInvalidation:
    """Tests for cache invalidation in _update_memories and _update_memory_md."""

    def test_update_memories_invalidates_cache(self, tmp_path):
        """_update_memories calls manager.prompt_cache.invalidate(agent_id)."""
        from daemon.tools.inner_soul import _update_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()

        # Create mock manager with mock cache
        mock_cache = MagicMock()
        mock_manager = MagicMock()
        mock_manager.prompt_cache = mock_cache

        classification = {
            "type": "event",
            "targets": ["memories"],
            "description": "Event or observation",
            "all_matches": []
        }

        result = _update_memories(
            agent_id="test-agent",
            agent_path=agent_dir,
            request="Test memory content",
            classification=classification,
            manager=mock_manager
        )

        assert result["success"] is True
        mock_cache.invalidate.assert_called_once_with("test-agent")

    def test_update_memory_md_wraps_invalidate_in_try_except(self, tmp_path):
        """_update_memory_md wraps invalidate in try/except (cache failure doesn't fail write)."""
        from daemon.tools.inner_soul import _update_memory_md

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()

        # Create mock manager that raises on invalidate
        mock_cache = MagicMock()
        mock_cache.invalidate.side_effect = Exception("Cache error!")
        mock_manager = MagicMock()
        mock_manager.prompt_cache = mock_cache

        rules = {"max_memory_words": 500}

        # Should not raise, should return success
        result = _update_memory_md(
            agent_id="test-agent",
            agent_path=agent_dir,
            request="Test memory content",
            rules=rules,
            manager=mock_manager
        )

        assert result["success"] is True
        assert result["target"] == "memory"
        mock_cache.invalidate.assert_called_once()

    def test_writing_new_memory_invalidates_cache(self, tmp_path):
        """Writing a new memory invalidates prompt cache so next load picks up the change."""
        from daemon.tools.inner_soul import _update_memories
        from daemon.loader import load_recent_memories, PromptCache

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()

        # Create mock manager with real cache
        cache = PromptCache()
        mock_manager = MagicMock()
        mock_manager.prompt_cache = cache

        # Pre-populate cache
        cache.set("test-agent", "old prompt", 100, {})

        classification = {
            "type": "event",
            "targets": ["memories"],
            "description": "Event or observation",
            "all_matches": []
        }

        _update_memories(
            agent_id="test-agent",
            agent_path=agent_dir,
            request="New memory content",
            classification=classification,
            manager=mock_manager
        )

        # Cache should be invalidated
        assert cache.get("test-agent") is None

    def test_cache_invalidation_with_none_manager(self, tmp_path):
        """Cache invalidation with None manager should not crash."""
        from daemon.tools.inner_soul import _update_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()

        classification = {
            "type": "event",
            "targets": ["memories"],
            "description": "Event or observation",
            "all_matches": []
        }

        # Should not raise
        result = _update_memories(
            agent_id="test-agent",
            agent_path=agent_dir,
            request="Test content",
            classification=classification,
            manager=None
        )

        assert result["success"] is True


class TestLoadGrowthRules:
    """Tests for _load_growth_rules() function."""

    def test_missing_growth_md_returns_default_2000_words(self, tmp_path):
        """Missing growth.md returns max_memory_words: 2000."""
        from daemon.tools.inner_soul import _load_growth_rules

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        # No growth.md file

        rules = _load_growth_rules(agent_dir)
        
        assert rules["max_memory_words"] == 2000
        assert rules["max_soul_chars"] == 2000

    def test_growth_md_with_custom_limit(self, tmp_path):
        """growth.md with 'memory.md ... 3000 words' returns 3000."""
        from daemon.tools.inner_soul import _load_growth_rules

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()

        growth_file = agent_dir / "growth.md"
        growth_file.write_text("# Growth Rules\n\nmemory.md max 3000 words\nsoul.md max 4000 characters")

        rules = _load_growth_rules(agent_dir)
        
        assert rules["max_memory_words"] == 3000
        assert rules["max_soul_chars"] == 4000


class TestImports:
    """Tests for module imports."""

    def test_create_access_memory_tool_import_succeeds(self):
        """from daemon.tools import create_access_memory_tool succeeds."""
        from daemon.tools import create_access_memory_tool
        assert callable(create_access_memory_tool)

    def test_create_access_memory_tool_in_all(self):
        """create_access_memory_tool is listed in __all__."""
        import daemon.tools as tools
        assert "create_access_memory_tool" in tools.__all__


class TestUpdateMemoriesFilename:
    """Integration tests for _update_memories filename generation."""

    def test_update_memories_produces_hyphen_based_filenames(self, tmp_path):
        """Created memory files use hyphens not underscores in the slug part."""
        from daemon.tools.inner_soul import _update_memories

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()

        mock_manager = MagicMock()
        mock_manager.prompt_cache = MagicMock()

        classification = {
            "type": "knowledge",
            "targets": ["memories"],
            "description": "Important knowledge",
            "all_matches": []
        }

        _update_memories(
            agent_id="test-agent",
            agent_path=agent_dir,
            request="User prefers TypeScript",
            classification=classification,
            manager=mock_manager
        )

        memories_dir = agent_dir / "memories"
        files = list(memories_dir.glob("*.md"))
        
        assert len(files) == 1
        filename = files[0].name
        
        # Should contain hyphenated slug
        assert "user-prefers-typescript" in filename
        # Should NOT contain underscores
        assert "prefers_TypeScript" not in filename

    def test_filename_format_with_timestamp(self, tmp_path):
        """Filename format is YYYYMMDD_HHMM-{slug}.md."""
        from daemon.tools.inner_soul import _update_memories
        import re

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()

        mock_manager = MagicMock()
        mock_manager.prompt_cache = MagicMock()

        classification = {
            "type": "event",
            "targets": ["memories"],
            "description": "Event",
            "all_matches": []
        }

        _update_memories(
            agent_id="test-agent",
            agent_path=agent_dir,
            request="test event",
            classification=classification,
            manager=mock_manager
        )

        memories_dir = agent_dir / "memories"
        files = list(memories_dir.glob("*.md"))
        
        assert len(files) == 1
        filename = files[0].name
        
        # Pattern: YYYYMMDD_HHMM-slug.md
        pattern = r"^\d{8}_\d{4}-[a-z0-9-]+\.md$"
        assert re.match(pattern, filename), f"Filename '{filename}' doesn't match expected pattern"


class TestComposeSystemPrompt:
    """Tests for compose_system_prompt with recent_memories."""

    def test_non_empty_recent_memories_shows_section(self):
        """When recent_memories is non-empty, '## Recent Memories' section appears."""
        from daemon.loader import compose_system_prompt

        prompts = {"soul": "I am a coder"}
        recent_memories = "- 20260401_1430-memory.md\n- 20260401_1400-another.md"

        result = compose_system_prompt(prompts, recent_memories=recent_memories)

        assert "## Recent Memories" in result
        assert "20260401_1430-memory.md" in result
        assert "20260401_1400-another.md" in result

    def test_empty_recent_memories_no_section(self):
        """When recent_memories is empty string, no '## Recent Memories' section."""
        from daemon.loader import compose_system_prompt

        prompts = {"soul": "I am a coder"}

        result = compose_system_prompt(prompts, recent_memories="")

        assert "## Recent Memories" not in result

    def test_none_recent_memories_no_section(self):
        """When recent_memories is None/empty, no '## Recent Memories' section."""
        from daemon.loader import compose_system_prompt

        prompts = {"soul": "I am a coder"}

        result = compose_system_prompt(prompts)

        assert "## Recent Memories" not in result


class TestProjectKnowledgeClassification:
    """Tests for project_knowledge classification that rejects project-specific info."""

    def test_classify_test_pack_project(self):
        """Test pack creation should be classified as project_knowledge."""
        from daemon.tools.inner_soul import _classify_request

        result = _classify_request("Created 8 timeout-enforced bash scripts in test/packs/")

        assert result["type"] == "project_knowledge"
        assert "REJECT" in result["targets"]

    def test_classify_llm_supervisor_proxy(self):
        """Specific project names should be classified as project_knowledge."""
        from daemon.tools.inner_soul import _classify_request

        result = _classify_request("Remember llm-supervisor-proxy uses timeout 120s")

        assert result["type"] == "project_knowledge"
        assert "REJECT" in result["targets"]

    def test_classify_kubernetes_infrastructure(self):
        """Tech stack mentions should be classified as project_knowledge."""
        from daemon.tools.inner_soul import _classify_request

        result = _classify_request("This project uses PostgreSQL on k8s")

        assert result["type"] == "project_knowledge"
        assert "REJECT" in result["targets"]

    def test_classify_docker_config(self):
        """Docker and infrastructure should be project_knowledge."""
        from daemon.tools.inner_soul import _classify_request

        result = _classify_request("Configured Docker deployment for the app")

        assert result["type"] == "project_knowledge"
        assert "REJECT" in result["targets"]

    def test_classify_env_config(self):
        """Config files should be project_knowledge."""
        from daemon.tools.inner_soul import _classify_request

        result = _classify_request("Updated .env with new database settings")

        assert result["type"] == "project_knowledge"
        assert "REJECT" in result["targets"]

    def test_allow_general_learning_patterns(self):
        """General learning patterns should NOT be project_knowledge."""
        from daemon.tools.inner_soul import _classify_request

        result = _classify_request("I learned that early testing catches bugs")

        assert result["type"] != "project_knowledge"
        assert result["type"] in ["knowledge", "pattern", "event"]

    def test_allow_self_knowledge(self):
        """Self-knowledge should NOT be project_knowledge."""
        from daemon.tools.inner_soul import _classify_request

        result = _classify_request("I noticed I often forget timeout edge cases")

        assert result["type"] != "project_knowledge"

    def test_format_rejection_message_legacy(self):
        """_format_rejection is a legacy function that still works if called directly."""
        from daemon.tools.inner_soul import _format_rejection

        classification = {
            "type": "project_knowledge",
            "description": "Project-specific knowledge - must NOT enter agent memory"
        }

        result = _format_rejection("Created test/packs/script.sh", classification)

        assert "REJECTED" in result
        assert "PROJECT KNOWLEDGE" in result
        assert "Created test/packs/script.sh" in result  # shows original request
        assert "does NOT belong" in result
        assert "Agent memory is for" in result
        assert "Agent memory is NOT for" in result
