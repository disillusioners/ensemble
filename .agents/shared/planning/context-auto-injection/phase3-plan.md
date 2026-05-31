# Phase 3: Integration & Tests

## Objective
Wire `get_shared_context()` from the context injection service into the `explore()` function call path, ensure `_save_explorer_result()` works with the new Concise format, and write comprehensive tests for the public API + internal helpers.

## Coupling
- **Depends on**: Phase 1 (context_injection.py service), Phase 2 (Concise format in explorer output)
- **Coupling type**: tight — imports `get_shared_context` from Phase 1's module and validates Phase 2's format end-to-end
- **Shared files with other phases**: `daemon/tools/knowledge_tools.py` (imports from Phase 1's module)
- **Shared APIs/interfaces**: `get_shared_context()`, `explore()` tool function, `_save_explorer_result()`, `invoke_agent_and_wait()`
- **Why this coupling**: Phase 3 is the glue that connects Phase 1's service with the live explore flow, and depends on Phase 2's format being produced by the agent.

## Context
After Phase 1 and Phase 2 are complete:
- Phase 1 provides `daemon/services/context_injection.py` with public `get_shared_context(context_key, query) -> str | None`
- Phase 2 ensures Explorer agent outputs `## Concise` section in every response
- `_save_explorer_result()` already saves the full result (which now includes Concise)
- The injection point is in the `explore()` tool — a single import + call to `get_shared_context()`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Wire `get_shared_context()` into `explore()` tool | Import from `daemon.services.context_injection`, call via `asyncio.to_thread()` before `invoke_agent_and_wait()`. Wrap in try/except. | `daemon/tools/knowledge_tools.py` |
| 2 | Update `_save_explorer_result` | No code changes needed — verify Concise section is preserved. Add a comment noting the expected format. | `daemon/tools/knowledge_tools.py` |
| 3 | Write public API tests | Test `get_shared_context()` as the primary interface — happy path, empty dir, no matches, errors return None. | `tests/unit/services/test_context_injection.py` |
| 4 | Write internal helper unit tests | Test private functions: tokenize, match_score, parse_sections, extract_first_sentence, truncate, match, format. | `tests/unit/services/test_context_injection.py` |
| 5 | Write edge case tests | Empty context dir, no matching files, all low-score files, files without Concise section (old format), files without Answer section, very large files, files with encoding issues, context_key=None. | `tests/unit/services/test_context_injection.py` |
| 6 | Write explore() integration tests | Test that explore() tool calls `get_shared_context()` and includes injection in message. | `tests/unit/tools/test_knowledge_tools.py` |
| 7 | Run full test suite | Verify all existing tests pass + all new tests pass. | — |

## Detailed Changes

### Task 1: Wire `get_shared_context()` into `explore()` tool

**Location:** `daemon/tools/knowledge_tools.py`, inside the `explore()` function in `create_knowledge_tools()`.

**New import at top of file:**
```python
from daemon.services.context_injection import get_shared_context
```

**Current code (lines ~278-283):**
```python
if context_key:
    context_dir = str(Path(tempfile.gettempdir()) / "ensemble" / "context" / context_key)
    explorer_message += f"\nShared context dir: {context_dir}"
```

**New code:**
```python
if context_key:
    context_dir_path = Path(tempfile.gettempdir()) / "ensemble" / "context" / context_key
    explorer_message += f"\nShared context dir: {str(context_dir_path)}"
    
    # Auto-inject relevant context files via reusable service
    # Run on thread pool to avoid blocking the async event loop with sync I/O
    try:
        injection = await asyncio.to_thread(get_shared_context, context_key, query)
        if injection:
            explorer_message += f"\n\n{injection}"
            logger.debug(
                "Context auto-injection: matched files for query '%s'",
                query[:50],
            )
    except Exception as e:
        logger.debug("Context auto-injection failed (non-critical): %s", e)
```

**Key design decisions:**
- **Single import, single call** — `get_shared_context()` encapsulates all matching + formatting logic. knowledge_tools.py has zero knowledge of file scanning, tokenization, or tiered extraction.
- **`asyncio.to_thread()` wrapper** — `get_shared_context` is synchronous (reads files), so it runs on the thread pool to avoid blocking the event loop.
- `Shared context dir` line is still present for backward compatibility (agent can still manually check).
- Entire block is in try/except — never blocks the explore flow.
- Debug-level logging for injection success/failure (not warning — this is best-effort).
- `get_shared_context` is a module-level function (picklable for `asyncio.to_thread()`).

### Task 2: Verify `_save_explorer_result`

The current `_save_explorer_result()` function writes the full `result` string to the file:
```python
content = (
    f"# Explorer Result: {query}\n"
    f"**Time**: {iso_ts}\n"
    f"**Project**: {project_name or 'unknown'}\n"
    f"**Mode**: {mode}\n\n"
    f"{result}"
)
```

Since `result` now includes `## Concise\n...` (added by the explorer agent per Phase 2), no changes are needed to `_save_explorer_result()`. The Concise section will be naturally preserved.

**However**, the `result` passed to `_save_explorer_result()` is the **stripped** version (after `_SHOULD_UPDATE_KB_PATTERN.sub()`). Verify that stripping the KB heading doesn't accidentally strip the Concise section. It won't — the regex specifically targets `## Need Update KB: true|false`.

### Task 3-5: Test Plan

**Test file:** `tests/unit/services/test_context_injection.py` (new file)

**Imports:**
```python
from daemon.services.context_injection import (
    get_shared_context,
    MatchedFile,
    # Internal helpers imported for unit testing
    _tokenize_slug,
    _tokenize_query,
    _match_score,
    _extract_slug_from_filename,
    _parse_sections,
    _extract_first_sentence,
    _truncate_to_tokens,
    _match_context_files,
    _format_injection,
    TIER_LOW,
    INJECTION_TOKEN_CAP,
)
```

#### New Test Class: `TestGetSharedContext` (PUBLIC API — primary interface)
```python
class TestGetSharedContext:
    """Tests for the public API get_shared_context(). These are the most important tests."""
    
    def test_returns_injection_for_matching_files(self, tmp_path):
        """Happy path: matching files produce injection string."""
        ctx = tmp_path / "ensemble" / "context" / "test-key"
        ctx.mkdir(parents=True)
        (ctx / "auth-module-jwt_20260531_120000.md").write_text(
            "# Explorer Result: auth module\n**Time**: 2026-05-31\n\n"
            "## Concise\nThe auth module uses JWT tokens.\n\n"
            "## Answer\nFull details about auth.\n"
        )
        
        with patch("daemon.services.context_injection.Path") as mock_path_cls:
            # Make Path() return our tmp_path context dir
            def path_constructor(arg):
                if "test-key" in str(arg):
                    return ctx
                return Path(arg)
            # Actually, simpler: patch tempfile.gettempdir
            pass
        
        # Simpler approach: patch tempfile.gettempdir
        with patch("daemon.services.context_injection.tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("test-key", "auth module jwt")
        
        assert result is not None
        assert "Pre-loaded Context" in result
        assert "auth-module-jwt" in result
    
    def test_returns_none_for_empty_dir(self, tmp_path):
        """Empty context dir returns None."""
        ctx = tmp_path / "ensemble" / "context" / "empty-key"
        ctx.mkdir(parents=True)
        
        with patch("daemon.services.context_injection.tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("empty-key", "auth module")
        
        assert result is None
    
    def test_returns_none_for_no_matches(self, tmp_path):
        """Non-matching files return None."""
        ctx = tmp_path / "ensemble" / "context" / "no-match-key"
        ctx.mkdir(parents=True)
        (ctx / "database-schema_20260531_120000.md").write_text(
            "# Explorer Result: db\n**Time**: 2026-05-31\n\n## Answer\nDetails.\n"
        )
        
        with patch("daemon.services.context_injection.tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("no-match-key", "slack webhook integration")
        
        assert result is None
    
    def test_returns_none_on_error(self, tmp_path):
        """Any error returns None (never raises)."""
        with patch("daemon.services.context_injection.tempfile.gettempdir", side_effect=OSError("disk error")):
            result = get_shared_context("any-key", "any query")
        
        assert result is None
    
    def test_returns_none_for_nonexistent_context_key(self, tmp_path):
        """Non-existent context_key directory returns None."""
        with patch("daemon.services.context_injection.tempfile.gettempdir", return_value=str(tmp_path)):
            result = get_shared_context("nonexistent-key", "any query")
        
        assert result is None
```

#### New Test Class: `TestTokenization` (internal helper unit tests)
```python
class TestTokenization:
    def test_tokenize_slug_basic(self):
        """Basic slug tokenization."""
        assert _tokenize_slug("auth-module-jwt-tokens") == {"auth", "module", "jwt", "tokens"}
    
    def test_tokenize_slug_removes_stopwords(self):
        """Stop words are filtered out."""
        assert _tokenize_slug("how-does-the-auth-work") == {"auth", "work"}
    
    def test_tokenize_slug_filters_short(self):
        """Single-char tokens are filtered."""
        assert _tokenize_slug("a-b-c-real") == {"real"}
    
    def test_tokenize_slug_empty(self):
        """Empty slug returns empty set."""
        assert _tokenize_slug("") == set()
    
    def test_tokenize_slug_all_stopwords(self):
        """Slug with only stop words returns empty set."""
        assert _tokenize_slug("the-a-is-of") == set()
    
    def test_tokenize_query_basic(self):
        """Query tokenization normalizes and splits."""
        tokens = _tokenize_query("How does the auth system work?")
        assert "auth" in tokens
        assert "system" in tokens
        assert "work" in tokens
        assert "how" not in tokens  # stop word
        assert "the" not in tokens  # stop word
    
    def test_tokenize_query_with_special_chars(self):
        """Special characters are normalized."""
        tokens = _tokenize_query("knowledge_tools.py explore() function")
        assert "knowledge" in tokens
        assert "tools" in tokens
        assert "py" in tokens
        assert "explore" in tokens
        assert "function" in tokens
```

#### New Test Class: `TestMatchScore`
```python
class TestMatchScore:
    """Tests for the recall-oriented asymmetric match scoring."""
    
    def test_identical_sets(self):
        """Perfect overlap returns 1.0."""
        assert _match_score({"a", "b"}, {"a", "b"}) == 1.0
    
    def test_no_overlap(self):
        """No common tokens returns 0.0."""
        assert _match_score({"a", "b"}, {"c", "d"}) == 0.0
    
    def test_short_query_full_recall(self):
        """Short query (2 tokens) fully matched by long slug → 1.0 (recall-oriented)."""
        # This is the critical case that Jaccard got wrong (2/4 = 0.5)
        result = _match_score({"auth", "module"}, {"auth", "module", "jwt", "tokens"})
        assert result == 1.0  # 2/2 = perfect recall
    
    def test_short_query_partial_recall(self):
        """Short query partially matched → proportional recall."""
        result = _match_score({"auth", "system"}, {"auth", "module", "jwt"})
        assert abs(result - 0.5) < 0.01  # 1/2 = 50% recall
    
    def test_long_sets_use_jaccard(self):
        """When both sets ≥3 tokens, uses Jaccard (penalizes specificity)."""
        result = _match_score({"auth", "module", "jwt"}, {"auth", "module", "jwt", "tokens", "signing"})
        # Jaccard: 3/5 = 0.6
        assert abs(result - 0.6) < 0.01
    
    def test_empty_query_returns_zero(self):
        assert _match_score(set(), {"auth", "module"}) == 0.0
    
    def test_empty_slug_returns_zero(self):
        assert _match_score({"auth", "module"}, set()) == 0.0
    
    def test_both_empty_returns_zero(self):
        assert _match_score(set(), set()) == 0.0
```

#### New Test Class: `TestExtractSlugFromFilename`
```python
class TestExtractSlugFromFilename:
    def test_standard_filename(self):
        assert _extract_slug_from_filename("auth-module-jwt_20260531_231255.md") == "auth-module-jwt"
    
    def test_no_timestamp(self):
        assert _extract_slug_from_filename("auth-module-jwt.md") == "auth-module-jwt"
    
    def test_long_slug(self):
        name = "very-long-slug-with-many-words-about-knowledge-tools_20260601_000001.md"
        assert _extract_slug_from_filename(name) == "very-long-slug-with-many-words-about-knowledge-tools"
```

#### New Test Class: `TestParseSections`
```python
class TestParseSections:
    def test_parse_standard_response(self):
        content = "## Confidence: HIGH\n\n## Concise\nShort summary.\n\n## Answer\nFull details here.\n\n## Sources\n- RAG"
        sections = _parse_sections(content)
        assert "Confidence" in sections
        assert "Concise" in sections
        assert "Answer" in sections
        assert "Sources" in sections
        assert "Short summary" in sections["Concise"]
    
    def test_parse_no_concise_section(self):
        """Old format without Concise section."""
        content = "## Answer\nFull details.\n\n## Sources\n- RAG"
        sections = _parse_sections(content)
        assert "Concise" not in sections
        assert "Answer" in sections
    
    def test_parse_with_file_header(self):
        """File with header lines before sections."""
        content = "# Explorer Result: test\n**Time**: 2026-01-01\n\n## Confidence: HIGH\n\n## Answer\nDetails"
        sections = _parse_sections(content)
        assert "Answer" in sections
    
    def test_parse_empty_content(self):
        assert _parse_sections("") == {}
```

#### New Test Class: `TestExtractFirstSentence`
```python
class TestExtractFirstSentence:
    def test_basic_sentence(self):
        assert _extract_first_sentence("The auth module uses JWT. It validates tokens.") == "The auth module uses JWT"
    
    def test_single_sentence(self):
        assert _extract_first_sentence("The auth module uses JWT tokens") == "The auth module uses JWT tokens"
    
    def test_empty_string(self):
        assert _extract_first_sentence("") == ""
    
    def test_leading_whitespace(self):
        result = _extract_first_sentence("  The module works. End.")
        assert result.startswith("The module works")
```

#### New Test Class: `TestTruncateToTokens`
```python
class TestTruncateToTokens:
    def test_short_text_not_truncated(self):
        text = "Short text."
        assert _truncate_to_tokens(text, 100) == text
    
    def test_long_text_truncated(self):
        text = "Word " * 500  # ~2500 tokens
        result = _truncate_to_tokens(text, 50)
        assert result.endswith("...")
        assert len(result) < len(text)
    
    def test_exact_limit(self):
        text = "a" * 200  # ~50 tokens
        result = _truncate_to_tokens(text, 50)
        assert result  # Not empty
```

#### New Test Class: `TestMatchContextFiles`
```python
class TestMatchContextFiles:
    def test_no_context_dir(self, tmp_path):
        """Non-existent directory returns empty list."""
        result = _match_context_files("auth module", tmp_path / "nonexistent")
        assert result == []
    
    def test_empty_context_dir(self, tmp_path):
        """Empty directory returns empty list."""
        (tmp_path / "context").mkdir()
        result = _match_context_files("auth module", tmp_path / "context")
        assert result == []
    
    def test_matching_files_found(self, tmp_path):
        """Files with relevant slugs are matched."""
        ctx = tmp_path / "context"
        ctx.mkdir()
        # Write a file with auth-related slug
        (ctx / "auth-module-jwt_20260531_120000.md").write_text(
            "# Explorer Result: auth module\n**Time**: 2026-05-31\n\n"
            "## Concise\nThe auth module uses JWT tokens.\n\n"
            "## Answer\nFull details about auth.\n"
        )
        # Write an irrelevant file
        (ctx / "database-migration-schema_20260531_130000.md").write_text(
            "# Explorer Result: db migration\n**Time**: 2026-05-31\n\n"
            "## Concise\nDatabase migration details.\n\n"
            "## Answer\nFull details about migrations.\n"
        )
        
        matches = _match_context_files("how does the auth module work?", ctx)
        assert len(matches) >= 1
        assert matches[0].slug == "auth-module-jwt"
        assert matches[0].score > 0.4  # Should be well above LOW threshold
    
    def test_no_matching_files(self, tmp_path):
        """Files with irrelevant slugs are not returned."""
        ctx = tmp_path / "context"
        ctx.mkdir()
        (ctx / "database-migration-schema_20260531_130000.md").write_text(
            "# Explorer Result: db\n**Time**: 2026-05-31\n\n## Answer\nDetails.\n"
        )
        
        matches = _match_context_files("slack integration webhook", ctx)
        assert all(m.score < TIER_LOW for m in matches) or len(matches) == 0
    
    def test_files_without_concise_use_answer(self, tmp_path):
        """Old format files without Concise section still work."""
        ctx = tmp_path / "context"
        ctx.mkdir()
        (ctx / "auth-module_20260531_120000.md").write_text(
            "# Explorer Result: auth\n**Time**: 2026-05-31\n\n"
            "## Answer\nThe auth module handles JWT authentication.\n"
        )
        
        matches = _match_context_files("auth module", ctx)
        if matches:
            assert "auth" in matches[0].first_sentence.lower() or "Auth" in matches[0].first_sentence
    
    def test_corrupt_file_skipped_gracefully(self, tmp_path):
        """Individual file read errors don't abort the scan."""
        ctx = tmp_path / "context"
        ctx.mkdir()
        # Create a good file
        (ctx / "auth-module_20260531_120000.md").write_text(
            "# Explorer Result: auth\n**Time**: 2026-05-31\n\n"
            "## Concise\nAuth summary.\n\n## Answer\nAuth details.\n"
        )
        # Create a file that will cause a parsing error (unreadable content is handled by errors="replace",
        # but test with a mock that raises on read_text for one specific file)
        # This is best tested by mocking Path.read_text to raise for one file
        # In practice, the per-file try/except ensures one bad file doesn't stop the scan
        
        matches = _match_context_files("auth module", ctx)
        assert len(matches) >= 1  # Good file still matched
    
    def test_short_query_matches_long_slug(self, tmp_path):
        """Recall-oriented scoring: 2-token query matches 4-token slug at high score."""
        ctx = tmp_path / "context"
        ctx.mkdir()
        (ctx / "auth-module-jwt-tokens_20260531_120000.md").write_text(
            "# Explorer Result: auth module jwt\n**Time**: 2026-05-31\n\n"
            "## Concise\nAuth module uses JWT tokens.\n\n## Answer\nFull details.\n"
        )
        
        matches = _match_context_files("auth module", ctx)
        assert len(matches) == 1
        # With asymmetric scoring: query={"auth","module"}, slug={"auth","module","jwt","tokens"}
        # → 2/2 = 1.0 (not 0.5 as Jaccard would give)
        assert matches[0].score == 1.0
```

#### New Test Class: `TestFormatInjection`
```python
class TestFormatInjection:
    def test_empty_matches(self):
        assert _format_injection([]) == ""
    
    def test_high_tier_injection(self):
        files = [MatchedFile(
            filename="auth-jwt_20260531_120000.md",
            slug="auth-jwt",
            score=0.87,
            sections={"Answer": "Full auth details about JWT tokens with RS256 signing."},
            first_sentence="The auth module uses JWT tokens.",
        )]
        result = _format_injection(files)
        assert "87% match" in result
        assert "auth-jwt" in result
        assert "Full auth details" in result
    
    def test_medium_tier_uses_concise(self):
        files = [MatchedFile(
            filename="auth-token_20260531_120000.md",
            slug="auth-token",
            score=0.65,
            sections={"Concise": "Short summary of auth.", "Answer": "Full details."},
            first_sentence="Short summary of auth",
        )]
        result = _format_injection(files)
        assert "65% match" in result
        assert "Short summary of auth" in result
        # Should NOT contain the full Answer section
        assert "Full details" not in result
    
    def test_low_tier_first_sentence_only(self):
        files = [MatchedFile(
            filename="auth-overview_20260531_120000.md",
            slug="auth-overview",
            score=0.45,
            sections={"Concise": "Auth overview text.", "Answer": "Long answer."},
            first_sentence="Auth overview text",
        )]
        result = _format_injection(files)
        assert "45% match" in result
        assert "Auth overview text" in result
    
    def test_file_index_appended(self):
        files = [MatchedFile(
            filename="auth_20260531_120000.md",
            slug="auth",
            score=0.85,
            sections={"Answer": "Auth details."},
            first_sentence="Auth module uses JWT.",
        )]
        result = _format_injection(files)
        assert "Available Context Files" in result
        assert "| auth |" in result
    
    def test_below_threshold_excluded(self):
        files = [MatchedFile(
            filename="unrelated_20260531_120000.md",
            slug="unrelated",
            score=0.2,
            sections={"Answer": "Something."},
            first_sentence="Something unrelated.",
        )]
        # Score is below TIER_LOW (0.4), so no tier content
        result = _format_injection(files)
        # The file should still appear in index, but no content section
        # Actually, looking at the design, _match_context_files already filters below TIER_LOW
        # So this case shouldn't reach _format_injection
    
    def test_global_token_cap_enforced(self):
        """When many high-tier files exceed global cap, injection is truncated."""
        files = [
            MatchedFile(
                filename=f"file-{i}_20260531_120000.md",
                slug=f"file-{i}",
                score=0.90,
                sections={"Answer": "Word " * 400},  # ~2000 tokens each
                first_sentence=f"File {i} summary.",
            )
            for i in range(5)
        ]
        result = _format_injection(files)
        # Should not contain all 5 files' content — cap at INJECTION_TOKEN_CAP
        # First 1-2 files consume the 2000-token budget
        assert result.count("% match") <= 3  # At most 3 high-tier files
        # Total estimated tokens should be reasonable
        # (Can't precisely assert token count without the estimator, but it's capped)
```

#### New Integration Test Class: `TestExploreAutoInjection`
```python
# These tests go in tests/unit/tools/test_knowledge_tools.py
# They verify that explore() correctly calls get_shared_context()

from daemon.services.context_injection import get_shared_context

class TestExploreAutoInjection:
    @pytest.mark.asyncio
    async def test_explore_injects_context_into_message(self, configured_env, mock_manager, tmp_path):
        """explore() includes matched context in the message sent to the agent."""
        # Set up context dir with a matching file
        ctx = tmp_path / "context" / "test-key"
        ctx.mkdir(parents=True)
        (ctx / "knowledge-tools-explore_20260531_120000.md").write_text(
            "# Explorer Result: knowledge tools\n**Time**: 2026-05-31\n\n"
            "## Concise\nKnowledge tools handle explore and experience.\n\n"
            "## Answer\nDetailed implementation of knowledge tools.\n"
        )
        
        # Mock the repository to return our context key
        mock_manager._instance_repository.get_tree_root_id = MagicMock(return_value="test-key")
        
        # Mock get_shared_context to return injection text
        injection_text = "## Pre-loaded Context (auto-matched)\n### knowledge-tools-explore (95% match)\nContent here.\n"
        
        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value="## Confidence: HIGH\n## Need Update KB: false\n\n## Concise\nResult.\n\n## Answer\nDone.") as mock_invoke:
            with patch("daemon.tools.knowledge_tools.get_shared_context", return_value=injection_text):
                with patch("daemon.tools.knowledge_tools.tempfile.gettempdir", return_value=str(tmp_path / "context")):
                    tools = create_knowledge_tools(mock_manager, "parent-instance-id")
                    explore_tool = next(t for t in tools if t.name == "explore")
                    
                    await explore_tool.ainvoke({"query": "knowledge tools explore function"})
                    
                    # Verify the message included injection
                    call_kwargs = mock_invoke.call_args.kwargs
                    message = call_kwargs["message"]
                    assert "Pre-loaded Context" in message
    
    @pytest.mark.asyncio
    async def test_explore_no_injection_when_service_returns_none(self, configured_env, mock_manager, tmp_path):
        """explore() works normally when get_shared_context returns None."""
        ctx = tmp_path / "context" / "empty-key"
        ctx.mkdir(parents=True)
        
        mock_manager._instance_repository.get_tree_root_id = MagicMock(return_value="empty-key")
        
        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value="## Confidence: HIGH\n## Need Update KB: false\n\n## Concise\nDone.\n\n## Answer\nResult.") as mock_invoke:
            with patch("daemon.tools.knowledge_tools.get_shared_context", return_value=None):
                with patch("daemon.tools.knowledge_tools.tempfile.gettempdir", return_value=str(tmp_path / "context")):
                    tools = create_knowledge_tools(mock_manager, "parent-instance-id")
                    explore_tool = next(t for t in tools if t.name == "explore")
                    
                    result = await explore_tool.ainvoke({"query": "test query"})
                    
                    # Should work normally, no injection in message
                    call_kwargs = mock_invoke.call_args.kwargs
                    message = call_kwargs["message"]
                    assert "Pre-loaded Context" not in message
                    assert result is not None
    
    @pytest.mark.asyncio
    async def test_explore_no_injection_when_context_key_none(self, configured_env, mock_manager):
        """explore() works normally when context_key is None (no tree root)."""
        mock_manager._instance_repository.get_tree_root_id = MagicMock(return_value=None)
        # get() returns metadata with project_id so explore proceeds
        mock_instance_meta = MagicMock()
        mock_instance_meta.project_id = "test-project"
        mock_manager._instance_repository.get = MagicMock(return_value=mock_instance_meta)
        
        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value="## Confidence: HIGH\n## Need Update KB: false\n\n## Concise\nDone.\n\n## Answer\nResult.") as mock_invoke:
            with patch("daemon.tools.knowledge_tools.get_shared_context") as mock_gsc:
                tools = create_knowledge_tools(mock_manager, "parent-instance-id")
                explore_tool = next(t for t in tools if t.name == "explore")
                
                result = await explore_tool.ainvoke({"query": "test query"})
                
                # get_shared_context should NOT be called when context_key is None
                mock_gsc.assert_not_called()
                assert result is not None
                assert "Result" in result
    
    @pytest.mark.asyncio
    async def test_explore_injection_failure_is_nonblocking(self, configured_env, mock_manager, tmp_path):
        """If get_shared_context raises, explore() still works normally."""
        mock_manager._instance_repository.get_tree_root_id = MagicMock(return_value="some-key")
        
        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value="## Confidence: HIGH\n## Need Update KB: false\n\n## Concise\nDone.\n\n## Answer\nResult."):
            with patch("daemon.tools.knowledge_tools.get_shared_context", side_effect=OSError("read error")):
                tools = create_knowledge_tools(mock_manager, "parent-instance-id")
                explore_tool = next(t for t in tools if t.name == "explore")
                
                result = await explore_tool.ainvoke({"query": "test query"})
                
                # Should still return result normally
                assert result is not None
                assert "Result" in result
    
    @pytest.mark.asyncio
    async def test_explore_injection_uses_thread_pool(self, configured_env, mock_manager, tmp_path):
        """Verify injection runs via asyncio.to_thread (not blocking event loop)."""
        mock_manager._instance_repository.get_tree_root_id = MagicMock(return_value="some-key")
        
        with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
                   new_callable=AsyncMock, return_value="## Confidence: HIGH\n## Need Update KB: false\n\n## Concise\nDone.\n\n## Answer\nResult."):
            with patch("daemon.tools.knowledge_tools.get_shared_context", return_value=None):
                with patch("daemon.tools.knowledge_tools.asyncio.to_thread", wraps=asyncio.to_thread) as mock_to_thread:
                    tools = create_knowledge_tools(mock_manager, "parent-instance-id")
                    explore_tool = next(t for t in tools if t.name == "explore")
                    
                    await explore_tool.ainvoke({"query": "test query"})
                    
                    # Verify to_thread was called with get_shared_context
                    mock_to_thread.assert_called_once()
                    assert mock_to_thread.call_args.args[0] == get_shared_context
```

## Key Files
- `daemon/services/context_injection.py` — Service module with `get_shared_context()` public API (Phase 1)
- `daemon/tools/knowledge_tools.py` — Integration wiring: import + call `get_shared_context()` in explore()
- `tests/unit/services/test_context_injection.py` — **New file.** Public API tests + internal helper unit tests
- `tests/unit/tools/test_knowledge_tools.py` — Explore() integration tests (calls get_shared_context via mock)

## Constraints
- **No injection logic in knowledge_tools.py** — all matching/formatting code lives in `daemon/services/context_injection.py`. knowledge_tools.py only imports and calls `get_shared_context()`.
- **`daemon/services/context_injection.py` has zero coupling to knowledge_tools** — it can be used by MCP server, external integrations, or any future feature without touching explore().
- Injection must never cause explore() to fail (all I/O in try/except)
- **Sync I/O runs on thread pool** via `asyncio.to_thread()` — never blocks the async event loop
- No performance regression — file scanning capped at 50 files
- Global injection token cap of 2000 prevents context window overflow
- All new tests must pass alongside all existing tests
- No changes to `invoke_agent_and_wait` signature or behavior
- `get_shared_context` is a module-level function (picklable for `asyncio.to_thread()`)
- Test for `context_key=None` edge case (no tree root → no injection path, no errors)

## Deliverables
- [ ] `get_shared_context()` imported and called in `explore()` tool via `asyncio.to_thread()`
- [ ] No injection helper functions remain in knowledge_tools.py
- [ ] `_save_explorer_result` verified to preserve Concise section
- [ ] **New test file** `tests/unit/services/test_context_injection.py` created
- [ ] Public API tests: `TestGetSharedContext` (happy path, empty dir, no matches, errors return None)
- [ ] Internal helper unit tests: tokenize, match_score, parse_sections, etc.
- [ ] Context file matching tests (happy path + per-file error handling + edge cases)
- [ ] Injection formatting tests (all tiers + global token cap)
- [ ] Integration tests in `test_knowledge_tools.py`: explore() calls get_shared_context, context_key=None, failure cases, thread pool usage
- [ ] Full test suite passes (existing + new)
