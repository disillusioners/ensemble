# Phase 5: Test Suite

## Objective

Create `tests/test_system_log_tools.py` with comprehensive tests following the four-lane pattern from `test_chart_tools.py` (Factory shape, Category registration, Invocation behavior, Security/Redaction), plus an integration test for end-to-end visibility through the factory + filter path. Tests use `tmp_path` fixtures for real file I/O (no mocking of filesystem — we want to verify actual file reading, path resolution, redaction, and byte-level output). All test methods are `def` (synchronous) since the tools are synchronous `def`, not `async def`.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Create `tests/test_system_log_tools.py` with imports, fixtures (mock manager, tmp log dir via `monkeypatch`), and shared helpers | Phase 2 complete | File exists, imports resolve, fixtures work |
| 2 | Write Factory lane tests — factory returns exactly 4 tools with correct names | Task 1 | All factory tests pass |
| 3 | Write Category registration tests — all 4 tools have `_tool_category == "system-log"`, not `"instance"` or other categories | Task 1 | All registration tests pass |
| 4 | Write Invocation + Security tests — list/read/search/tail behavior, path traversal rejection, size cap enforcement, redaction, missing/empty file handling, level filter, regex search, context lines | Tasks 2, 3 | All invocation + security tests pass |
| 5 | Write Integration test — verify tools survive `_apply_tool_filter` for an agent with `"system-log"` in tools.allow (end-to-end factory + filter path) | Tasks 2, 3 | Integration test passes |

## Detailed File Design

### `tests/test_system_log_tools.py`

**Structure (following test_chart_tools.py pattern):**

```python
"""Tests for ``daemon.tools.system_log_tools.create_system_log_tools``.

Four coverage lanes:

  1. **Factory** — ``create_system_log_tools(manager, current_instance_id)``
     returns a list with exactly four tools: ``ens_system_log_list``,
     ``ens_system_log_read``, ``ens_system_log_search``, ``ens_system_log_tail``.
  2. **Registration** — all four tools are tagged with ``_tool_category
     == "system-log"`` (via ``@register_tool_category``), NOT any other
     category (security: prevents implicit grant of other tool suites).
  3. **Invocation** — each tool reads/searches/tails/logs correctly:
     paging, regex, context lines, level filter, tail, byte caps, redaction.
  4. **Security** — path traversal (``../``, absolute paths, separators)
     is rejected; size caps are enforced; redaction masks API keys,
     tokens, and passwords; missing/empty files return informative errors.
  5. **Integration** — tools survive the full ``create_instance_tools``
     + ``_apply_tool_filter`` path for an agent with ``system-log`` in
     ``tools.allow``.

Uses ``tmp_path`` + ``monkeypatch`` to create real log files in a
temporary directory and patch ``DAEMON_LOG_DIR`` so tests are hermetic
(no interaction with the developer's ``data/logs/``).

All tests are synchronous (``def``) — the tools are now ``def``, not
``async def``, so we call ``tool.invoke({...})`` (LangChain ``@tool``
sync invocation pattern).
"""
```

#### Fixtures

```python
import os
import pytest
from unittest.mock import MagicMock


def _make_manager() -> MagicMock:
    """Build a mock manager (pattern parity with test_chart_tools.py)."""
    manager = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    return manager


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    """Create a temporary log directory and patch DAEMON_LOG_DIR."""
    d = tmp_path / "logs"
    d.mkdir()
    monkeypatch.setenv("DAEMON_LOG_DIR", str(d))
    return d


@pytest.fixture
def tools(log_dir):
    """Create system log tools with the patched log directory."""
    from daemon.tools.system_log_tools import create_system_log_tools
    return create_system_log_tools(_make_manager(), "test-instance-id")


@pytest.fixture
def log_file(log_dir):
    """Create a sample log file with known content."""
    f = log_dir / "ensemble.log"
    lines = [
        "2026-08-08 08:00:00 - daemon.api - INFO - Server started",
        "2026-08-08 08:00:01 - daemon.graph - INFO - Graph compiled",
        "2026-08-08 08:00:02 - daemon.api - WARNING - Deprecated endpoint hit",
        "2026-08-08 08:00:03 - daemon.tools - ERROR - Tool execution failed: KeyError",
        "2026-08-08 08:00:04 - daemon.api - INFO - Request processed",
        "2026-08-08 08:00:05 - daemon.graph - ERROR - Node timeout",
        "2026-08-08 08:00:06 - daemon.api - DEBUG - Cache hit",
    ]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f
```

#### Lane 1: Factory Tests

```python
class TestCreateSystemLogToolsFactory:
    """Factory tests for create_system_log_tools."""

    def test_factory_returns_exactly_four_tools(self, tools):
        assert isinstance(tools, list)
        assert len(tools) == 4

    def test_factory_returns_expected_tool_names(self, tools):
        names = [t.name for t in tools]
        assert "ens_system_log_list" in names
        assert "ens_system_log_read" in names
        assert "ens_system_log_search" in names
        assert "ens_system_log_tail" in names

    def test_factory_creates_independent_tools_per_call(self, log_dir):
        from daemon.tools.system_log_tools import create_system_log_tools
        m = _make_manager()
        a = create_system_log_tools(m, "instance-a")
        b = create_system_log_tools(m, "instance-b")
        assert a[0] is not b[0]  # distinct closures
```

#### Lane 2: Registration Tests

```python
class TestSystemLogToolRegistration:
    """Registration tests for the system-log category."""

    def test_all_tools_registered_under_system_log_category(self, tools):
        for t in tools:
            assert getattr(t, "_tool_category", None) == "system-log"

    def test_tools_not_registered_under_instance_category(self, tools):
        """SECURITY: tools must NOT be tagged as 'instance'."""
        for t in tools:
            assert getattr(t, "_tool_category", None) != "instance"
```

#### Lane 3: Invocation Tests

```python
class TestSystemLogListInvocation:
    """Invocation tests for ens_system_log_list (W5)."""

    def test_list_returns_filenames(self, tools, log_dir):
        """ens_system_log_list returns available log filenames."""
        (log_dir / "ensemble.log").write_text("line\n", encoding="utf-8")
        (log_dir / "ensemble.log.1").write_text("old\n", encoding="utf-8")
        list_tool = [t for t in tools if t.name == "ens_system_log_list"][0]
        result = list_tool.invoke({})
        assert "ensemble.log" in result
        assert "ensemble.log.1" in result

    def test_list_includes_sizes(self, tools, log_dir):
        """ens_system_log_list includes file sizes."""
        (log_dir / "ensemble.log").write_text("hello world\n", encoding="utf-8")
        list_tool = [t for t in tools if t.name == "ens_system_log_list"][0]
        result = list_tool.invoke({})
        # Either raw byte count or a "B" / "KB" size indicator
        assert "11" in result or "B" in result

    def test_list_empty_directory(self, tools, log_dir):
        """ens_system_log_list handles empty directory gracefully."""
        list_tool = [t for t in tools if t.name == "ens_system_log_list"][0]
        result = list_tool.invoke({})
        assert "no log files" in result.lower() or "empty" in result.lower()


class TestSystemLogReadInvocation:
    """Invocation tests for ens_system_log_read."""

    def test_read_returns_lines_with_numbers(self, tools, log_file):
        read_tool = [t for t in tools if t.name == "ens_system_log_read"][0]
        result = read_tool.invoke({"filename": "ensemble.log", "offset": 0, "limit": 3})
        assert "1:" in result
        assert "Server started" in result
        assert "Graph compiled" in result

    def test_read_supports_paging(self, tools, log_file):
        read_tool = [t for t in tools if t.name == "ens_system_log_read"][0]
        page1 = read_tool.invoke({"filename": "ensemble.log", "offset": 0, "limit": 3})
        page2 = read_tool.invoke({"filename": "ensemble.log", "offset": 3, "limit": 3})
        # No overlap: page1 has lines 1-3, page2 has lines 4-6
        assert "Server started" in page1
        assert "Server started" not in page2
        assert "Tool execution failed" in page2

    def test_read_missing_file_returns_error(self, tools, log_dir):
        read_tool = [t for t in tools if t.name == "ens_system_log_read"][0]
        result = read_tool.invoke({"filename": "nonexistent.log"})
        assert "not found" in result.lower()

    def test_read_empty_file_returns_message(self, tools, log_dir):
        (log_dir / "empty.log").write_text("", encoding="utf-8")
        read_tool = [t for t in tools if t.name == "ens_system_log_read"][0]
        result = read_tool.invoke({"filename": "empty.log"})
        assert "empty" in result.lower()

    def test_read_respects_size_cap(self, tools, log_dir):
        # Create a file with > 500 lines
        big_file = log_dir / "big.log"
        big_file.write_text("\n".join(f"line {i}" for i in range(600)))
        read_tool = [t for t in tools if t.name == "ens_system_log_read"][0]
        result = read_tool.invoke(
            {"filename": "big.log", "offset": 0, "limit": 10000},  # request way more than cap
        )
        # Count numbered lines — should be at most 500
        numbered_lines = [l for l in result.split("\n") if l.strip() and l.strip()[0].isdigit()]
        assert len(numbered_lines) <= 500


class TestSystemLogSearchInvocation:
    """Invocation tests for ens_system_log_search."""

    def test_search_finds_matching_lines(self, tools, log_file):
        search_tool = [t for t in tools if t.name == "ens_system_log_search"][0]
        result = search_tool.invoke({"pattern": "ERROR", "filename": "ensemble.log"})
        assert "Tool execution failed" in result
        assert "Node timeout" in result

    def test_search_with_context(self, tools, log_file):
        search_tool = [t for t in tools if t.name == "ens_system_log_search"][0]
        result = search_tool.invoke(
            {
                "pattern": "ERROR",
                "filename": "ensemble.log",
                "context_before": 1,
                "context_after": 1,
            },
        )
        # Context line before first ERROR match (line 3: WARNING)
        assert "Deprecated endpoint" in result

    def test_search_with_level_filter(self, tools, log_file):
        search_tool = [t for t in tools if t.name == "ens_system_log_search"][0]
        result = search_tool.invoke(
            {"pattern": ".*", "filename": "ensemble.log", "level": "ERROR"},
        )
        assert "ERROR" in result
        assert "INFO" not in result  # filtered out

    def test_search_invalid_regex_returns_error(self, tools, log_file):
        search_tool = [t for t in tools if t.name == "ens_system_log_search"][0]
        result = search_tool.invoke({"pattern": "[invalid", "filename": "ensemble.log"})
        assert "invalid regex" in result.lower()

    def test_search_no_matches_returns_message(self, tools, log_file):
        search_tool = [t for t in tools if t.name == "ens_system_log_search"][0]
        result = search_tool.invoke({"pattern": "NONEXISTENT_PATTERN_XYZ"})
        assert "no matches" in result.lower()


class TestSystemLogTailInvocation:
    """Invocation tests for ens_system_log_tail."""

    def test_tail_returns_last_n_lines(self, tools, log_file):
        tail_tool = [t for t in tools if t.name == "ens_system_log_tail"][0]
        result = tail_tool.invoke({"filename": "ensemble.log", "lines": 3})
        assert "Cache hit" in result       # last line
        assert "Node timeout" in result    # second to last
        assert "Server started" not in result  # not in last 3

    def test_tail_with_level_filter(self, tools, log_file):
        tail_tool = [t for t in tools if t.name == "ens_system_log_tail"][0]
        result = tail_tool.invoke(
            {"filename": "ensemble.log", "lines": 10, "level": "ERROR"},
        )
        assert "Tool execution failed" in result
        assert "Node timeout" in result
        assert "INFO" not in result  # filtered

    def test_tail_respects_max_cap(self, tools, log_dir):
        big_file = log_dir / "big.log"
        big_file.write_text("\n".join(f"line {i}" for i in range(300)))
        tail_tool = [t for t in tools if t.name == "ens_system_log_tail"][0]
        result = tail_tool.invoke({"filename": "big.log", "lines": 10000})
        numbered = [l for l in result.split("\n") if l.strip() and l.strip()[0].isdigit()]
        assert len(numbered) <= 200  # MAX_LINES_TAIL

    def test_tail_missing_file_returns_error(self, tools, log_dir):
        tail_tool = [t for t in tools if t.name == "ens_system_log_tail"][0]
        result = tail_tool.invoke({"filename": "nonexistent.log"})
        assert "not found" in result.lower()
```

#### Lane 4: Security & Redaction Tests

```python
class TestSystemLogSecurity:
    """Security tests: path traversal, size caps, read-only enforcement."""

    def test_path_traversal_rejected_read(self, tools, log_file):
        """ens_system_log_read blocks '../' traversal."""
        read_tool = [t for t in tools if t.name == "ens_system_log_read"][0]
        result = read_tool.invoke({"filename": "../../../etc/passwd"})
        assert "error" in result.lower()
        assert "not allowed" in result.lower() or "outside" in result.lower()

    def test_path_traversal_rejected_search(self, tools, log_file):
        search_tool = [t for t in tools if t.name == "ens_system_log_search"][0]
        result = search_tool.invoke(
            {"pattern": "root", "filename": "../../etc/passwd"},
        )
        assert "error" in result.lower()

    def test_path_traversal_rejected_tail(self, tools, log_file):
        tail_tool = [t for t in tools if t.name == "ens_system_log_tail"][0]
        result = tail_tool.invoke({"filename": "../../../etc/passwd"})
        assert "error" in result.lower()

    def test_absolute_path_rejected(self, tools, log_file):
        read_tool = [t for t in tools if t.name == "ens_system_log_read"][0]
        result = read_tool.invoke({"filename": "/etc/passwd"})
        assert "error" in result.lower()
        assert "absolute" in result.lower()

    def test_separator_in_filename_rejected(self, tools, log_file):
        read_tool = [t for t in tools if t.name == "ens_system_log_read"][0]
        result = read_tool.invoke({"filename": "subdir/ensemble.log"})
        assert "error" in result.lower()
        assert "separator" in result.lower() or "not allowed" in result.lower()

    def test_rotated_backup_file_readable(self, tools, log_dir):
        """Rotated backups (ensemble.log.1) are valid filenames."""
        backup = log_dir / "ensemble.log.1"
        backup.write_text("old log line\n", encoding="utf-8")
        read_tool = [t for t in tools if t.name == "ens_system_log_read"][0]
        result = read_tool.invoke({"filename": "ensemble.log.1"})
        assert "old log line" in result

    def test_byte_cap_truncates_large_response(self, tools, log_dir):
        """Response is truncated at MAX_BYTES_RESPONSE (12 KB) even if under line cap."""
        huge_file = log_dir / "huge.log"
        # Single line just above the 12 KB cap
        huge_file.write_text("X" * (15 * 1024) + "\n", encoding="utf-8")
        read_tool = [t for t in tools if t.name == "ens_system_log_read"][0]
        result = read_tool.invoke({"filename": "huge.log", "limit": 10})
        assert "truncated" in result.lower()

    def test_long_line_truncated(self, tools, log_dir):
        """Lines exceeding MAX_LINE_LENGTH (2000) are truncated."""
        f = log_dir / "ensemble.log"
        long_line = "X" * 3000
        f.write_text(f"2026-08-08 - INFO - {long_line}\n", encoding="utf-8")
        read_tool = [t for t in tools if t.name == "ens_system_log_read"][0]
        result = read_tool.invoke({"filename": "ensemble.log"})
        assert "...(truncated)" in result


class TestSystemLogRedaction:
    """Tests for sensitive content redaction in log output (C3)."""

    def test_api_key_redacted_in_read(self, tools, log_dir):
        """API key values are [REDACTED] in read output."""
        f = log_dir / "ensemble.log"
        f.write_text(
            "2026-08-08 08:00:00 - daemon.config - INFO - "
            "OPENAI_API_KEY=sk-proj-abc123xyz\n",
            encoding="utf-8",
        )
        read_tool = [t for t in tools if t.name == "ens_system_log_read"][0]
        result = read_tool.invoke({"filename": "ensemble.log"})
        assert "[REDACTED]" in result
        assert "sk-proj-abc123xyz" not in result

    def test_bearer_token_redacted_in_tail(self, tools, log_dir):
        """Bearer tokens are [REDACTED] in tail output."""
        f = log_dir / "ensemble.log"
        f.write_text(
            "2026-08-08 08:00:00 - daemon.api - INFO - "
            "Authorization: Bearer eyJhbGciOiJIUzI1\n",
            encoding="utf-8",
        )
        tail_tool = [t for t in tools if t.name == "ens_system_log_tail"][0]
        result = tail_tool.invoke({"filename": "ensemble.log", "lines": 10})
        assert "[REDACTED]" in result
        assert "eyJhbGciOiJIUzI1" not in result

    def test_password_redacted_in_search(self, tools, log_dir):
        """Password values are [REDACTED] in search output."""
        f = log_dir / "ensemble.log"
        f.write_text(
            "2026-08-08 08:00:00 - daemon.db - INFO - "
            "password=supersecret123\n",
            encoding="utf-8",
        )
        search_tool = [t for t in tools if t.name == "ens_system_log_search"][0]
        result = search_tool.invoke({"pattern": "password", "filename": "ensemble.log"})
        assert "[REDACTED]" in result
        assert "supersecret123" not in result
```

**Note on size caps:** All tools enforce a hard cap of 500 lines per response (or 200 for tail) AND a 12 KB total byte cap. The 500-line tests above verify the line cap; the byte cap tests verify the byte cap; tests asserting "at most 500 lines" remain valid. The 50 KB byte cap has been reduced to 12 KB per the W7 revision.

#### Lane 5: Integration Test

```python
class TestSystemLogToolIntegration:
    """Integration test: tools are visible through create_instance_tools()
    + _apply_tool_filter() — the full factory + filter path (W6)."""

    def test_tools_visible_after_apply_tool_filter(self):
        """Verify system-log tools survive _apply_tool_filter for an agent
        with 'system-log' in tools.allow."""
        from daemon.tools.instance import create_instance_tools
        from daemon.tools._tool_registry import _apply_tool_filter

        # Build a mock manager sufficient for create_instance_tools.
        manager = MagicMock()
        manager._instance_repository = MagicMock()
        manager._instance_repository.get = MagicMock(return_value=None)
        # Add other attributes the factory may touch; if the test fails
        # with AttributeError, add the missing mock attribute here.

        all_tools = create_instance_tools(manager, "test-instance-id", "developer")
        filtered = _apply_tool_filter(all_tools, allow={"system-log"})
        tool_names = [t.name for t in filtered]
        assert "ens_system_log_list" in tool_names
        assert "ens_system_log_read" in tool_names
        assert "ens_system_log_search" in tool_names
        assert "ens_system_log_tail" in tool_names
```

**Note on integration test:** The exact signatures of `create_instance_tools` and `_apply_tool_filter` may differ across the codebase. The worker implementing this test should:
1. Verify the import paths — adjust if the actual module is `daemon.tools.instance` vs `daemon.tools._tool_registry`.
2. Verify the function signatures (positional vs keyword args for `allow=`) — adjust `_apply_tool_filter(all_tools, allow={"system-log"})` to match.
3. Add mock attributes to `manager` as needed (the test may need `manager.some_attr = MagicMock()` lines if the factory reads additional attributes).
4. The test's INTENT is to verify the full factory + filter path; the EXACT code may need light adjustment based on the actual API.

If the integration test cannot be made to work due to deeper signature issues, **fallback**: mark it as a known gap and add a comment explaining that manual end-to-end verification (Phase 4 exit criterion: "After daemon restart, instances of each of the four agents have all 4 tools") covers the same ground. Do NOT silently skip the test.

## Test Execution

```bash
# Run all system log tool tests
pytest tests/test_system_log_tools.py -v

# Run with coverage
pytest tests/test_system_log_tools.py --cov=daemon.tools.system_log_tools --cov-report=term-missing

# Run only security tests
pytest tests/test_system_log_tools.py::TestSystemLogSecurity -v

# Run only redaction tests
pytest tests/test_system_log_tools.py::TestSystemLogRedaction -v

# Run only integration test
pytest tests/test_system_log_tools.py::TestSystemLogToolIntegration -v
```

## Coupling

- **Tight with:** Phase 2 — tests import `create_system_log_tools` from the module and test each tool's behavior. Module must exist and be functional.
- **Loose with:** Phase 3 — some tests may assert registry entries (e.g., `CATEGORY_MODULES["system-log"]`), but the core test suite tests the module directly without depending on registry wiring.
- **Loose with:** Phase 4 — the integration test verifies that the `"system-log"` category is granted to an agent when present in `tools.allow`. Phase 4 sets up that grant for the four target agents; the test uses a synthetic developer profile.
- **Independent of:** Phase 1

## Risks

- **Flaky tests due to real file I/O:** Tests use `tmp_path` (pytest's built-in temp directory) which is isolated and cleaned up automatically. `monkeypatch.setenv("DAEMON_LOG_DIR", ...)` ensures the tools read from the temp dir, not the developer's `data/logs/`. No flakiness expected.
- **Test isolation:** Each test gets its own `tmp_path`, so there's no cross-test contamination. The `tools` fixture re-creates tools per test, ensuring fresh closures.
- **Integration test fragility:** The integration test depends on `create_instance_tools` and `_apply_tool_filter` signatures. If those signatures drift, the test will break. Mitigation: the note above guides the worker to adjust the test to the actual API. Fallback: manual end-to-end verification (Phase 4 exit criterion).
- **Sync invocation pattern:** All tests use `tool.invoke({...})` since the tools are now `def`, not `async def`. If the tools are reverted to `async def` in a future revision, all tests must be updated back to `await tool.coroutine(...)`. Mitigation: a comment at the top of the test file documents the sync pattern.

## Exit Criterion

- All tests in `tests/test_system_log_tools.py` pass
- Coverage of `daemon/tools/system_log_tools.py` is ≥ 95% line coverage
- No warnings or errors in test output
- Tests run in < 5 seconds (no slow I/O — tmp files are tiny)
- All four tools (`ens_system_log_list`, `ens_system_log_read`, `ens_system_log_search`, `ens_system_log_tail`) have at least one invocation test
- Redaction is verified for API keys, Bearer tokens, and passwords across read, search, and tail
- Integration test passes (or is marked as known gap with manual verification comment)
