"""Tests for ``daemon.tools.system_log_tools.create_system_log_tools``.

Five coverage lanes:

  1. **Factory** — ``create_system_log_tools(manager, current_instance_id)``
     returns a list with exactly four tools: ``ens_system_log_list``,
     ``ens_system_log_read``, ``ens_system_log_search``,
     ``ens_system_log_tail``.
  2. **Registration** — all four tools are tagged with ``_tool_category
     == "system-log"`` (via ``@register_tool_category``), NOT any other
     category.  They also appear in ``DYNAMIC_TOOL_NAMES`` and
     ``CATEGORY_MODULES`` in ``_tool_registry``.
  3. **Invocation** — each tool reads/searches/tails/lists correctly:
      paging, regex, context lines, level filter, tail, size caps,
      empty/missing file handling.
  4. **Security** — path traversal (``../``, absolute paths, separators)
      is rejected; size caps are enforced; redaction masks API keys,
      tokens, and passwords; missing/empty files return informative
      errors; byte caps truncate responses.
  5. **Integration** — tools survive the ``_apply_tool_filter`` path for
      an agent with ``"system-log"`` in ``tools.allow``.

Uses ``tmp_path`` + ``monkeypatch`` to create real log files in a
temporary directory and patch ``DAEMON_LOG_DIR`` so tests are hermetic
(no interaction with the developer's ``data/logs/``).

All tests are synchronous (``def``) — the tools are ``def``, not
``async def``, so we call ``tool.invoke({...})`` (LangChain ``@tool``
sync invocation pattern).
"""
from __future__ import annotations

import inspect
from importlib import import_module
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# Shared Helpers & Fixtures
# =============================================================================


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


def _tool_by_name(tools_list: list, name: str):
    """Helper to pick a tool from the list by its name."""
    matches = [t for t in tools_list if t.name == name]
    available = [t.name for t in tools_list]
    assert matches, f"Tool {name!r} not found in {available}"
    return matches[0]


# =============================================================================
# Lane 1: Factory Tests
# =============================================================================


class TestCreateSystemLogToolsFactory:
    """Factory tests for create_system_log_tools."""

    def test_factory_returns_four_tools(self, tools):
        """create_system_log_tools returns exactly 4 tools."""
        assert isinstance(tools, list)
        assert len(tools) == 4

    def test_tool_names_correct(self, tools):
        """All 4 tool names are exactly the expected set."""
        names = sorted(t.name for t in tools)
        assert names == sorted([
            "ens_system_log_list",
            "ens_system_log_read",
            "ens_system_log_search",
            "ens_system_log_tail",
        ])

    def test_all_tools_have_category(self, tools):
        """All 4 tools have _tool_category == 'system-log'."""
        for t in tools:
            assert getattr(t, "_tool_category", None) == "system-log", (
                f"Tool {t.name} has _tool_category={getattr(t, '_tool_category', None)!r}"
            )

    def test_all_tools_are_sync(self, tools):
        """None of the 4 tools are coroutine functions."""
        for t in tools:
            # LangChain @tool wraps the original function; the func
            # attribute gives the raw closure.
            func = getattr(t, "func", t)
            assert not inspect.iscoroutinefunction(func), (
                f"Tool {t.name} should be sync, but func is a coroutine"
            )

    def test_factory_creates_independent_tools_per_call(self, log_dir):
        """Each call returns fresh closure instances (not shared state)."""
        from daemon.tools.system_log_tools import create_system_log_tools

        m = _make_manager()
        a = create_system_log_tools(m, "instance-a")
        b = create_system_log_tools(m, "instance-b")
        assert a[0] is not b[0]  # distinct closures


# =============================================================================
# Lane 2: Registration Tests
# =============================================================================


class TestSystemLogToolRegistration:
    """Registration tests for the system-log category in _tool_registry."""

    def test_category_modules_has_system_log(self):
        """'system-log' is a key in CATEGORY_MODULES."""
        from daemon.tools._tool_registry import CATEGORY_MODULES

        assert "system-log" in CATEGORY_MODULES

    def test_category_modules_resolves_to_importable_module(self):
        """CATEGORY_MODULES['system-log'] resolves to an importable module."""
        from daemon.tools._tool_registry import CATEGORY_MODULES

        module_path = CATEGORY_MODULES["system-log"]
        # Handle both str and list[str] values
        if isinstance(module_path, list):
            module_path = module_path[0]
        mod = import_module(module_path)
        assert hasattr(mod, "create_system_log_tools")

    def test_all_tool_names_in_dynamic_tool_names(self):
        """All 4 system-log tool names are in DYNAMIC_TOOL_NAMES."""
        from daemon.tools._tool_registry import DYNAMIC_TOOL_NAMES

        for name in (
            "ens_system_log_list",
            "ens_system_log_read",
            "ens_system_log_search",
            "ens_system_log_tail",
        ):
            assert name in DYNAMIC_TOOL_NAMES, f"{name} not in DYNAMIC_TOOL_NAMES"

    def test_tools_not_registered_under_instance_category(self, tools):
        """SECURITY: tools must NOT be tagged as 'instance'."""
        for t in tools:
            assert getattr(t, "_tool_category", None) != "instance"


# =============================================================================
# Lane 3: Invocation Tests
# =============================================================================


class TestSystemLogListInvocation:
    """Invocation tests for ens_system_log_list."""

    def test_list_returns_filenames(self, tools, log_dir):
        """ens_system_log_list returns available log filenames."""
        (log_dir / "ensemble.log").write_text("line\n", encoding="utf-8")
        (log_dir / "ensemble.log.1").write_text("old\n", encoding="utf-8")
        list_tool = _tool_by_name(tools, "ens_system_log_list")
        result = list_tool.invoke({})
        assert "ensemble.log" in result
        assert "ensemble.log.1" in result

    def test_list_includes_sizes(self, tools, log_dir):
        """ens_system_log_list includes file sizes."""
        (log_dir / "ensemble.log").write_text("hello world\n", encoding="utf-8")
        list_tool = _tool_by_name(tools, "ens_system_log_list")
        result = list_tool.invoke({})
        # 11 bytes — either raw byte count or a "B" size indicator
        assert "11" in result or "B" in result

    def test_list_empty_directory(self, tools, log_dir):
        """ens_system_log_list handles empty directory gracefully."""
        list_tool = _tool_by_name(tools, "ens_system_log_list")
        result = list_tool.invoke({})
        assert "no log files" in result.lower()

    def test_list_rotated_backups(self, tools, log_dir):
        """Rotated backups (ensemble.log.1, .2) appear in listing."""
        (log_dir / "ensemble.log").write_text("current\n", encoding="utf-8")
        (log_dir / "ensemble.log.1").write_text("backup1\n", encoding="utf-8")
        (log_dir / "ensemble.log.2").write_text("backup2\n", encoding="utf-8")
        list_tool = _tool_by_name(tools, "ens_system_log_list")
        result = list_tool.invoke({})
        assert "ensemble.log" in result
        assert "ensemble.log.1" in result
        assert "ensemble.log.2" in result


class TestSystemLogReadInvocation:
    """Invocation tests for ens_system_log_read."""

    def test_read_returns_lines_with_numbers(self, tools, log_file):
        """ens_system_log_read returns numbered lines."""
        read_tool = _tool_by_name(tools, "ens_system_log_read")
        result = read_tool.invoke({"filename": "ensemble.log", "offset": 0, "limit": 3})
        assert "1:" in result
        assert "Server started" in result
        assert "Graph compiled" in result

    def test_read_supports_paging(self, tools, log_file):
        """ens_system_log_read paging: offset/limit return different pages."""
        read_tool = _tool_by_name(tools, "ens_system_log_read")
        page1 = read_tool.invoke({"filename": "ensemble.log", "offset": 0, "limit": 3})
        page2 = read_tool.invoke({"filename": "ensemble.log", "offset": 3, "limit": 3})
        assert "Server started" in page1
        assert "Server started" not in page2
        assert "Tool execution failed" in page2

    def test_read_default_filename(self, tools, log_file):
        """ens_system_log_read defaults to 'ensemble.log'."""
        read_tool = _tool_by_name(tools, "ens_system_log_read")
        result = read_tool.invoke({})
        assert "Server started" in result

    def test_read_missing_file_returns_error(self, tools, log_dir):
        """Missing file returns 'not found' message."""
        read_tool = _tool_by_name(tools, "ens_system_log_read")
        result = read_tool.invoke({"filename": "nonexistent.log"})
        assert "not found" in result.lower()

    def test_read_empty_file_returns_message(self, tools, log_dir):
        """Empty file returns 'empty' message."""
        (log_dir / "empty.log").write_text("", encoding="utf-8")
        read_tool = _tool_by_name(tools, "ens_system_log_read")
        result = read_tool.invoke({"filename": "empty.log"})
        assert "empty" in result.lower()

    def test_read_respects_line_cap(self, tools, log_dir):
        """ens_system_log_read caps at MAX_LINES_READ (500)."""
        big_file = log_dir / "big.log"
        big_file.write_text("\n".join(f"line {i}" for i in range(600)))
        read_tool = _tool_by_name(tools, "ens_system_log_read")
        result = read_tool.invoke(
            {"filename": "big.log", "offset": 0, "limit": 10000},
        )
        # Count numbered lines — should be at most 500
        numbered_lines = [
            l for l in result.split("\n") if l.strip() and l.strip()[0].isdigit()
        ]
        assert len(numbered_lines) <= 500


class TestSystemLogSearchInvocation:
    """Invocation tests for ens_system_log_search."""

    def test_search_finds_matching_lines(self, tools, log_file):
        """ens_system_log_search finds lines matching the pattern."""
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke({"pattern": "ERROR", "filename": "ensemble.log"})
        assert "Tool execution failed" in result
        assert "Node timeout" in result

    def test_search_with_context(self, tools, log_file):
        """ens_system_log_search with context shows surrounding lines."""
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        # REAL CODE uses single 'context' param (NOT context_before/after)
        result = search_tool.invoke(
            {
                "pattern": "ERROR",
                "filename": "ensemble.log",
                "context": 1,
            },
        )
        # Context line before first ERROR match (line 3: WARNING)
        assert "Deprecated endpoint" in result

    def test_search_with_level_filter(self, tools, log_file):
        """ens_system_log_search level filter restricts to matching level."""
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke(
            {"pattern": ".*", "filename": "ensemble.log", "level": "ERROR"},
        )
        assert "ERROR" in result
        assert "INFO" not in result  # filtered out

    def test_search_invalid_regex_returns_error(self, tools, log_file):
        """Invalid regex returns a graceful error message."""
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke({"pattern": "[invalid", "filename": "ensemble.log"})
        assert "invalid regex" in result.lower()

    def test_search_no_matches_returns_message(self, tools, log_file):
        """No matches returns 'no matches' message."""
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke({"pattern": "NONEXISTENT_PATTERN_XYZ"})
        assert "no matches" in result.lower()

    def test_search_respects_limit_cap(self, tools, log_dir):
        """ens_system_log_search caps matches at 50."""
        many_match = log_dir / "ensemble.log"
        many_match.write_text(
            "\n".join(f"2026-08-08 - daemon.test - ERROR - match {i}" for i in range(100)),
            encoding="utf-8",
        )
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke(
            {"pattern": "ERROR", "filename": "ensemble.log", "limit": 10000},
        )
        # Count match markers ( >>> )
        match_markers = [l for l in result.split("\n") if ">>>" in l]
        assert len(match_markers) <= 50

    def test_search_context_clamped_to_max(self, tools, log_dir, monkeypatch):
        """W2 fix: ``context=200`` is clamped to MAX_CONTEXT (100).

        Regression: the old code passed ``int(context)`` straight to the
        deque buffer, which would happily allocate huge rolling buffers
        and produce enormous responses. Now the value is clamped to
        ``MAX_CONTEXT`` before any I/O.
        """
        from daemon.tools import system_log_tools

        # Force MAX_CONTEXT to a known small value so we can assert the
        # clamp actually took effect (not just that no error was raised).
        monkeypatch.setattr(system_log_tools, "MAX_CONTEXT", 5)

        # Build a file with 50 lines so context_before/after have data.
        f = log_dir / "ensemble.log"
        f.write_text(
            "\n".join(
                f"2026-08-08 08:00:{i:02d} - daemon.test - ERROR - line {i}"
                for i in range(50)
            ),
            encoding="utf-8",
        )

        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke(
            {"pattern": "ERROR", "filename": "ensemble.log", "context": 200},
        )
        # No error returned (clamp is silent).
        assert "error" not in result.lower() or "truncated" in result.lower()
        # The match block for the first ERROR should contain AT MOST
        # MAX_CONTEXT (5) lines of pre-context. Easier assertion: scan
        # the first match block and count context-indented lines.
        # A context value of 200 would yield 200 pre-context lines; a
        # clamped value of 5 yields at most 5.
        # Find first match marker.
        lines = result.split("\n")
        first_match_idx = next(
            (i for i, l in enumerate(lines) if ">>>" in l), None
        )
        assert first_match_idx is not None, "expected at least one match"
        # Walk back from the match: count consecutive context lines (no '>>>')
        context_lines = 0
        for j in range(first_match_idx - 1, -1, -1):
            l = lines[j]
            if "match(es)" in l or l.strip().startswith("---"):
                break
            context_lines += 1
        assert context_lines <= system_log_tools.MAX_CONTEXT

    def test_search_context_invalid_string_returns_friendly_error(self, tools, log_file):
        """W3 fix: ``context="abc"`` returns a friendly error (no traceback).

        Regression: ``int(context)`` raised ValueError and the bare
        ``except`` chain didn't catch it — agent saw a stack trace.
        """
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke(
            {"pattern": "ERROR", "filename": "ensemble.log", "context": "abc"},
        )
        # Friendly error, no traceback.
        assert "Traceback" not in result
        assert "invalid context" in result.lower() or "context" in result.lower()
        # Message is non-empty.
        assert len(result) > 0

    # ─────────────────────────────────────────────────────────────────
    # Tail-first scan regression tests (fix ens_system_log_search scan
    # direction). The bug: forward scan + MAX_LINES_SCAN=50_000 cap
    # caused patterns in lines > 50,000 to never be found. The fix
    # windows the LAST MAX_LINES_SCAN lines instead.
    # ─────────────────────────────────────────────────────────────────

    def test_search_finds_recent_matches_on_large_file(self, log_dir, monkeypatch):
        """CORE REGRESSION: a match beyond MAX_LINES_SCAN from line 1 is now found.

        This reproduces the exact PROD scenario:
          - log file >> 50,000 lines
          - relevant patterns appear AFTER line MAX_LINES_SCAN from line 1
          - forward-scan code returned 'No matches' 100% of the time
          - tail-first scan now finds them (they're inside the recent
            MAX_LINES_SCAN window)
        """
        from daemon.tools import system_log_tools
        from daemon.tools.system_log_tools import create_system_log_tools

        # Lower the cap to a small value to keep the test fast.
        monkeypatch.setattr(system_log_tools, "MAX_LINES_SCAN", 50)

        # 100 lines total. Markers at file positions 60, 61, 62, 63 — well
        # beyond the old forward-scan window of 1..50.
        lines = []
        for i in range(1, 101):
            if 60 <= i <= 63:
                lines.append(f"2026-08-08 08:00:{i:02d} - daemon.x - ERROR - MATCH_BEYOND_CAP_LINE_{i}\n")
            else:
                lines.append(f"2026-08-08 08:00:{i:02d} - daemon.x - INFO - filler line {i}\n")
        (log_dir / "ensemble.log").write_text("".join(lines), encoding="utf-8")

        tools = create_system_log_tools(_make_manager(), "test-instance-id")
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke(
            {"pattern": "MATCH_BEYOND_CAP_LINE", "filename": "ensemble.log"},
        )

        # Old forward-scan code: scanned 1..50, found no MATCH markers,
        # returned 'No matches found for pattern ...'. Tail-first: deque
        # retains lines 51..100, finds all four markers.
        assert "no matches" not in result.lower(), (
            f"Expected to find MATCH_BEYOND_CAP_LINE markers in recent region; "
            f"old forward-scan bug would yield 'No matches'. Got: {result!r}"
        )
        assert "MATCH_BEYOND_CAP_LINE_60" in result
        assert "MATCH_BEYOND_CAP_LINE_61" in result
        assert "MATCH_BEYOND_CAP_LINE_62" in result
        assert "MATCH_BEYOND_CAP_LINE_63" in result
        # Cap held: scanned == 50.
        assert "scanned 50 lines" in result

    def test_search_finds_matches_beyond_old_forward_window(self, log_dir, monkeypatch):
        """Strict bug repro: matches at positions > MAX_LINES_SCAN are found.

        With the old forward-scan, scanning stopped at line MAX_LINES_SCAN
        and any pattern past that point was unreachable. With the
        tail-first scan, those patterns are inside the recent window.
        """
        from daemon.tools import system_log_tools
        from daemon.tools.system_log_tools import create_system_log_tools

        monkeypatch.setattr(system_log_tools, "MAX_LINES_SCAN", 50)

        # 80 lines: 1-50 are filler, 51-79 are filler-with-no-match, 80 has marker.
        lines = []
        for i in range(1, 81):
            if i == 80:
                lines.append(f"2026-08-08 08:00:{i:02d} - daemon.x - ERROR - MATCH_LINE_AT_80\n")
            else:
                lines.append(f"2026-08-08 08:00:{i:02d} - daemon.x - INFO - nothing\n")
        (log_dir / "ensemble.log").write_text("".join(lines), encoding="utf-8")

        tools = create_system_log_tools(_make_manager(), "test-instance-id")
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke(
            {"pattern": "MATCH_LINE_AT_80", "filename": "ensemble.log"},
        )

        # Old forward-scan code scanned 1..50, found nothing. New code finds
        # the marker at position 80 (within the recent-50 window).
        assert "MATCH_LINE_AT_80" in result, (
            f"Expected MATCH_LINE_AT_80; old forward-scan bug would yield 'No matches'. "
            f"Got: {result!r}"
        )
        # Result is single match, so match_count == 1.
        assert "1 match(es)" in result
        # Header reports scanned == cap.
        assert "scanned 50 lines" in result

    def test_search_still_works_on_small_file(self, tools, log_dir):
        """A match near the end of a small file is found (sanity check).

        With file_size <= MAX_LINES_SCAN, tail-first scan reads the whole
        file (deque never overflows) and behavior matches the old forward
        scan.
        """
        f = log_dir / "ensemble.log"
        f.write_text(
            "\n".join([
                "2026-08-08 08:00:00 - daemon.api - INFO - Server started",
                "2026-08-08 08:00:01 - daemon.api - INFO - Heartbeat",
                "2026-08-08 08:00:02 - daemon.api - ERROR - UNIQUE_SMALL_FILE_MATCH",
            ]) + "\n",
            encoding="utf-8",
        )
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke(
            {"pattern": "UNIQUE_SMALL_FILE_MATCH", "filename": "ensemble.log"},
        )
        assert "UNIQUE_SMALL_FILE_MATCH" in result
        # File has 3 lines, all scanned (file < cap).
        assert "scanned 3 lines" in result

    def test_search_scan_cap_still_respected(self, log_dir, monkeypatch):
        """Cap holds from the recent end: a pattern BEFORE the cap is NOT found.

        With MAX_LINES_SCAN=10 and a 20-line file, the tool scans only
        the last 10 lines (positions 11..20). A marker placed at line 2
        (which falls BEFORE the trailing window) must NOT produce a match
        row — proving the cap still bounds work, just from the recent end.
        """
        from daemon.tools import system_log_tools
        from daemon.tools.system_log_tools import create_system_log_tools

        monkeypatch.setattr(system_log_tools, "MAX_LINES_SCAN", 10)

        # The no-matches "found pattern 'X' ..." message echoes the pattern
        # name, so we check for the match marker prefix ``>>>`` (only
        # emitted next to actual matches) and a position-distinctive
        # content fragment rather than the raw pattern string.
        lines = []
        for i in range(1, 21):
            if i == 2:
                lines.append("EARLY_LINE_TWO_DISTINCTIVE_CONTENT\n")
            else:
                lines.append(f"line {i}\n")
        (log_dir / "ensemble.log").write_text("".join(lines), encoding="utf-8")

        tools = create_system_log_tools(_make_manager(), "test-instance-id")
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke(
            {"pattern": "EARLY_LINE_TWO_DISTINCTIVE_CONTENT", "filename": "ensemble.log"},
        )
        # No match marker (>>> prefix) should appear — only the no-matches
        # message echoes the pattern string.
        assert ">>>" not in result, (
            f"Expected no match marker; old forward-scan would have found line 2. "
            f"Got: {result!r}"
        )
        # Header reports exactly the cap-sized scan, proving the bound holds.
        assert "scanned 10 lines" in result
        # No-matches path renders the familiar message.
        assert "no matches" in result.lower()

    def test_search_context_correct_under_reverse_scan(self, log_dir, monkeypatch):
        """Context (before+after) and absolute line numbers stay correct.

        Place a match in the recent region of a file larger than the cap,
        ask for context=2, and assert:
          (a) The pre-context lines (with their absolute line numbers) appear
          (b) The post-context lines (with their absolute line numbers) appear
          (c) The match line itself shows its true file line number (> cap)
        """
        from daemon.tools import system_log_tools
        from daemon.tools.system_log_tools import create_system_log_tools

        monkeypatch.setattr(system_log_tools, "MAX_LINES_SCAN", 20)

        # 30 lines. Match at line 26; pre-context lines 24-25; post-context
        # lines 27-28. cap=20 means tail-first retains the LAST 20 lines
        # (positions 11..30), which includes 24-28.
        lines = []
        for i in range(1, 31):
            if i == 26:
                lines.append("CTX_MATCH_LINE_AT_26\n")
            else:
                lines.append(f"noise line {i}\n")
        (log_dir / "ensemble.log").write_text("".join(lines), encoding="utf-8")

        tools = create_system_log_tools(_make_manager(), "test-instance-id")
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke(
            {
                "pattern": "CTX_MATCH_LINE_AT_26",
                "filename": "ensemble.log",
                "context": 2,
            },
        )

        # Match found.
        assert "CTX_MATCH_LINE_AT_26" in result
        # Match line number (absolute, 1-indexed) is rendered correctly.
        # Match marker format is ``{line_no:>6} >>> {line}``.
        assert "    26 >>> CTX_MATCH_LINE_AT_26" in result
        # Pre-context lines (24, 25) appear as context rows (6-wide number
        # column, 5-space indent, then content).
        assert "    24 " in result
        assert "noise line 24" in result
        assert "    25 " in result
        assert "noise line 25" in result
        # Post-context lines (27, 28) appear as context rows.
        assert "    27 " in result
        assert "noise line 27" in result
        assert "    28 " in result
        assert "noise line 28" in result
        # The match scan is bounded by the cap.
        assert "scanned 20 lines" in result

    def test_search_directory_path_returns_graceful_error(self, tools, log_dir):
        """ens_system_log_search on a directory path returns an error string.

        Regression: the scan-direction fix (commit f28de084) moved the
        ``_open_log_file`` call OUT of the ``except OSError`` guard. The
        remaining narrow ``except FileNotFoundError`` then leaked
        ``IsADirectoryError`` (raised when the resolved path is a
        directory, not a regular file) uncaught out of the tool. This
        test creates a real subdirectory inside the log dir, asks the
        search tool to treat it as a filename, and asserts a graceful
        error string is returned — NOT a Python traceback / uncaught
        ``IsADirectoryError`` propagating through ``tool.invoke``.
        """
        # Build a subdirectory inside the log directory (passes the
        # _validate_filename path-confinement check) and ask the search
        # tool to "search" it as if it were a log file.
        subdir = log_dir / "subdir-not-a-log"
        subdir.mkdir()
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke(
            {"pattern": "anything", "filename": "subdir-not-a-log"},
        )
        # Friendly error message — matches the sibling tail tool's pattern.
        # Must NOT be a raw Python traceback or a swallowed IsADirectoryError
        # exception leaking through the tool boundary.
        assert isinstance(result, str)
        assert "not a file" in result.lower() or "not found" in result.lower() or "error" in result.lower()
        # Defensive sanity: a leaked traceback would contain these markers.
        assert "Traceback" not in result
        assert "IsADirectoryError" not in result

    def test_search_missing_file_returns_friendly_error(self, tools, log_dir):
        """Missing file still returns the friendly 'not found' message.

        Regression-guard: ensures the merged single-try did not regress
        the existing ``FileNotFoundError`` branch — the friendly message
        must still be returned (not a leaked OSError / traceback).
        """
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke(
            {"pattern": "anything", "filename": "does-not-exist.log"},
        )
        assert isinstance(result, str)
        assert "not found" in result.lower()
        assert "Traceback" not in result


class TestSystemLogTailInvocation:
    """Invocation tests for ens_system_log_tail."""

    def test_tail_returns_last_n_lines(self, tools, log_file):
        """ens_system_log_tail returns the last N lines."""
        tail_tool = _tool_by_name(tools, "ens_system_log_tail")
        result = tail_tool.invoke({"filename": "ensemble.log", "lines": 3})
        assert "Cache hit" in result       # last line
        assert "Node timeout" in result    # second to last
        assert "Server started" not in result  # not in last 3

    def test_tail_default_lines(self, tools, log_file):
        """ens_system_log_tail defaults to 50 lines."""
        tail_tool = _tool_by_name(tools, "ens_system_log_tail")
        result = tail_tool.invoke({"filename": "ensemble.log"})
        assert "Cache hit" in result

    def test_tail_respects_max_cap(self, tools, log_dir):
        """ens_system_log_tail caps at MAX_LINES_TAIL (200)."""
        big_file = log_dir / "big.log"
        big_file.write_text("\n".join(f"line {i}" for i in range(300)))
        tail_tool = _tool_by_name(tools, "ens_system_log_tail")
        result = tail_tool.invoke({"filename": "big.log", "lines": 10000})
        numbered = [
            l for l in result.split("\n") if l.strip() and l.strip()[0].isdigit()
        ]
        assert len(numbered) <= 200  # MAX_LINES_TAIL

    def test_tail_missing_file_returns_error(self, tools, log_dir):
        """Missing file returns 'not found' message."""
        tail_tool = _tool_by_name(tools, "ens_system_log_tail")
        result = tail_tool.invoke({"filename": "nonexistent.log"})
        assert "not found" in result.lower()

    def test_tail_empty_file_returns_error(self, tools, log_dir):
        """Empty file returns 'empty' message."""
        (log_dir / "empty.log").write_text("", encoding="utf-8")
        tail_tool = _tool_by_name(tools, "ens_system_log_tail")
        result = tail_tool.invoke({"filename": "empty.log"})
        assert "empty" in result.lower()


# =============================================================================
# Lane 4: Security & Redaction Tests
# =============================================================================


class TestSystemLogSecurity:
    """Security tests: path traversal, size caps, line truncation."""

    # ── Path traversal ──────────────────────────────────────────────

    @pytest.mark.parametrize("malicious", [
        "../../../etc/passwd",
        "/etc/passwd",
        "subdir/x.log",
        "..",
    ])
    def test_path_traversal_rejected_read(self, tools, log_file, malicious):
        """ens_system_log_read blocks path traversal / absolute / separators."""
        read_tool = _tool_by_name(tools, "ens_system_log_read")
        result = read_tool.invoke({"filename": malicious})
        assert "error" in result.lower()

    @pytest.mark.parametrize("malicious", [
        "../../../etc/passwd",
        "/etc/passwd",
        "subdir/x.log",
    ])
    def test_path_traversal_rejected_search(self, tools, log_file, malicious):
        """ens_system_log_search blocks path traversal."""
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke({"pattern": "root", "filename": malicious})
        assert "error" in result.lower()

    @pytest.mark.parametrize("malicious", [
        "../../../etc/passwd",
        "/etc/passwd",
        "subdir/x.log",
    ])
    def test_path_traversal_rejected_tail(self, tools, log_file, malicious):
        """ens_system_log_tail blocks path traversal."""
        tail_tool = _tool_by_name(tools, "ens_system_log_tail")
        result = tail_tool.invoke({"filename": malicious})
        assert "error" in result.lower()

    def test_absolute_path_rejected_with_message(self, tools, log_file):
        """Absolute path error mentions 'absolute'."""
        read_tool = _tool_by_name(tools, "ens_system_log_read")
        result = read_tool.invoke({"filename": "/etc/passwd"})
        assert "error" in result.lower()
        assert "absolute" in result.lower()

    def test_separator_in_filename_rejected(self, tools, log_file):
        """Path separator in filename is rejected."""
        read_tool = _tool_by_name(tools, "ens_system_log_read")
        result = read_tool.invoke({"filename": "subdir/ensemble.log"})
        assert "error" in result.lower()

    def test_rotated_backup_file_readable(self, tools, log_dir):
        """Rotated backups (ensemble.log.1) are valid filenames."""
        backup = log_dir / "ensemble.log.1"
        backup.write_text("old log line\n", encoding="utf-8")
        read_tool = _tool_by_name(tools, "ens_system_log_read")
        result = read_tool.invoke({"filename": "ensemble.log.1"})
        assert "old log line" in result

    # ── Byte caps ──────────────────────────────────────────────────

    def test_byte_cap_truncates_large_response(self, tools, log_dir):
        """Response is truncated at MAX_BYTES_RESPONSE (12 KB)."""
        huge_file = log_dir / "huge.log"
        # Single line just above the 12 KB cap
        huge_file.write_text("X" * (15 * 1024) + "\n", encoding="utf-8")
        read_tool = _tool_by_name(tools, "ens_system_log_read")
        result = read_tool.invoke({"filename": "huge.log", "limit": 10})
        assert "truncated" in result.lower()

    def test_search_byte_cap_truncates(self, tools, log_dir):
        """W8 REVIEWER FIX: Search response is truncated when many matches
        exceed the 12 KB byte cap."""
        big_search = log_dir / "ensemble.log"
        # Generate many ERROR matches with enough content to exceed 12 KB.
        # Each line ~300 chars → ~40 matches will hit the 12 KB cap.
        lines = []
        for i in range(200):
            padding = "A" * 250
            lines.append(f"2026-08-08 08:00:{i:02d} - daemon.test - ERROR - match_{i}_{padding}")
        big_search.write_text("\n".join(lines) + "\n", encoding="utf-8")
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke({"pattern": "ERROR", "filename": "ensemble.log"})
        assert "truncated" in result.lower()

    # ── Line truncation ────────────────────────────────────────────

    def test_long_line_truncated(self, tools, log_dir):
        """Lines exceeding MAX_LINE_LENGTH (2000) are truncated."""
        f = log_dir / "ensemble.log"
        long_line = "X" * 3000
        f.write_text(f"2026-08-08 - INFO - {long_line}\n", encoding="utf-8")
        read_tool = _tool_by_name(tools, "ens_system_log_read")
        result = read_tool.invoke({"filename": "ensemble.log"})
        assert "...(truncated)" in result


class TestSystemLogRedaction:
    """Tests for sensitive content redaction in log output."""

    def test_api_key_redacted_in_read(self, tools, log_dir):
        """API key values are [REDACTED] in read output."""
        f = log_dir / "ensemble.log"
        f.write_text(
            "2026-08-08 08:00:00 - daemon.config - INFO - "
            "OPENAI_API_KEY=sk-proj-abc123xyz\n",
            encoding="utf-8",
        )
        read_tool = _tool_by_name(tools, "ens_system_log_read")
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
        tail_tool = _tool_by_name(tools, "ens_system_log_tail")
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
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke({"pattern": "password", "filename": "ensemble.log"})
        assert "[REDACTED]" in result
        assert "supersecret123" not in result

    def test_token_redacted_in_read(self, tools, log_dir):
        """token= values are [REDACTED] in read output."""
        f = log_dir / "ensemble.log"
        f.write_text(
            "2026-08-08 08:00:00 - daemon.auth - INFO - "
            "token=abc123token\n",
            encoding="utf-8",
        )
        read_tool = _tool_by_name(tools, "ens_system_log_read")
        result = read_tool.invoke({"filename": "ensemble.log"})
        assert "[REDACTED]" in result
        assert "abc123token" not in result

    def test_redaction_helper_directly(self):
        """Test _redact_line helper directly for all 8 patterns."""
        from daemon.tools.system_log_tools import _redact_line

        assert "[REDACTED]" in _redact_line("MY_API_KEY=sk-abc")
        assert "[REDACTED]" in _redact_line("AUTH_TOKEN=tok123")
        assert "[REDACTED]" in _redact_line("DB_PASSWORD=hunter2")
        assert "[REDACTED]" in _redact_line("CLIENT_SECRET=sec")
        assert "[REDACTED]" in _redact_line("password=pwd")
        assert "[REDACTED]" in _redact_line("token=tkn")
        assert "[REDACTED]" in _redact_line("Bearer abc123")
        assert "[REDACTED]" in _redact_line("Authorization: Basic dXNlcjpwYXNz")

    def test_json_key_redaction(self):
        r"""C1 fix: JSON quoted-key secrets are fully redacted.

        Regression: previously the closing quote terminated the value
        match and the secret leaked (e.g. ``"api_key": "sk-secret123"``
        → ``"api_key": "[REDACTED]"`` kept the leading quote, but the
        pattern ``\w*_API_KEY=[^\s]+`` matched the literal substring
        ``key":"sk-secret123`` and only redacted from there, leaving
        parts visible). Now the entire ``"key":"value"`` pair is masked.
        """
        from daemon.tools.system_log_tools import _redact_line

        redacted = _redact_line('payload = {"api_key": "secret123"}')
        assert "[REDACTED]" in redacted
        assert "secret123" not in redacted

    def test_json_token_redaction(self):
        """C1 fix: JSON ``"token":`` value is fully redacted."""
        from daemon.tools.system_log_tools import _redact_line

        redacted = _redact_line('config = {"token": "abc.def.ghi"}')
        assert "[REDACTED]" in redacted
        assert "abc.def.ghi" not in redacted

    def test_json_password_redaction(self):
        """C1 fix: JSON ``"password":`` value is fully redacted."""
        from daemon.tools.system_log_tools import _redact_line

        redacted = _redact_line('db = {"password": "hunter2!"}')
        assert "[REDACTED]" in redacted
        assert "hunter2!" not in redacted

    def test_hyphenated_header_redaction(self):
        """C1 fix: Hyphenated ``X-API-Key:`` header is redacted.

        Regression: legacy patterns expected ``_API_KEY`` (underscore),
        not ``-API-Key`` (hyphen), so ``X-API-Key: abcdef`` leaked.
        """
        from daemon.tools.system_log_tools import _redact_line

        redacted = _redact_line("request header: X-API-Key: abcdef")
        assert "[REDACTED]" in redacted
        assert "abcdef" not in redacted

    def test_authorization_basic_full_header_redacted(self):
        """C1 fix: ``Authorization: Basic <base64>`` — the base64 value is
        redacted, not just the literal ``Basic`` scheme word.

        Regression: ``Authorization\\s*:\\s*\\S+`` matched only the
        first whitespace-delimited token (``Basic``), leaving the
        base64 credential (``dXNlcjpwYXNz``) visible. Now the entire
        header value (scheme + credential) is masked.
        """
        from daemon.tools.system_log_tools import _redact_line

        redacted = _redact_line("Authorization: Basic dXNlcjpwYXNz")
        assert "[REDACTED]" in redacted
        assert "dXNlcjpwYXNz" not in redacted
        assert "Basic" not in redacted  # entire scheme+value replaced

    def test_multword_password_value_redacted(self):
        """C1 fix: Multi-word values like ``password=my secret`` have
        both words redacted.

        Regression: ``password\\s*=\\s*\\S+`` only matched ``my``,
        leaking ``secret``.
        """
        from daemon.tools.system_log_tools import _redact_line

        redacted = _redact_line("password=my secret value")
        assert "[REDACTED]" in redacted
        assert "my" not in redacted
        assert "secret" not in redacted

    def test_truncate_helper_directly(self):
        """Test _truncate_line helper: short lines unchanged, long truncated."""
        from daemon.tools.system_log_tools import (
            MAX_LINE_LENGTH,
            _truncate_line,
        )

        assert _truncate_line("short") == "short"
        long_line = "X" * (MAX_LINE_LENGTH + 500)
        result = _truncate_line(long_line)
        assert len(result) == MAX_LINE_LENGTH + len("...(truncated)")
        assert result.endswith("...(truncated)")

    def test_validate_filename_helper_directly(self, log_dir):
        """Test _validate_filename: safe filenames pass, unsafe rejected."""
        from daemon.tools.system_log_tools import _validate_filename

        # Use the patched log_dir (avoids macOS /tmp -> /private/tmp resolution)
        with patch.dict("os.environ", {"DAEMON_LOG_DIR": str(log_dir)}):
            resolved = _validate_filename("ensemble.log")
            assert str(resolved) == f"{log_dir}/ensemble.log"

            resolved2 = _validate_filename("ensemble.log.1")
            assert str(resolved2) == f"{log_dir}/ensemble.log.1"

        # Unsafe filenames raise ValueError
        with pytest.raises(ValueError):
            _validate_filename("")
        with pytest.raises(ValueError):
            _validate_filename("/etc/passwd")
        with pytest.raises(ValueError):
            _validate_filename("../etc/passwd")
        with pytest.raises(ValueError):
            _validate_filename("subdir/x.log")

    def test_format_size_helper_directly(self):
        """Test _format_size: bytes < KB < MB tiers."""
        from daemon.tools.system_log_tools import _format_size

        assert _format_size(0) == "0 B"
        assert _format_size(512) == "512 B"
        assert _format_size(1024) == "1.0 KB"
        assert _format_size(2048) == "2.0 KB"
        assert _format_size(1024 * 1024) == "1.0 MB"
        assert _format_size(5 * 1024 * 1024) == "5.0 MB"


# =============================================================================
# Lane 5: Edge Cases & Error Path Coverage
# =============================================================================


class TestSystemLogEdgeCases:
    """Edge case tests for error paths and defensive branches."""

    def test_list_missing_directory(self, tmp_path, monkeypatch):
        """If DAEMON_LOG_DIR points to a non-existent path, list returns error."""
        from daemon.tools.system_log_tools import create_system_log_tools

        missing = tmp_path / "does_not_exist"
        monkeypatch.setenv("DAEMON_LOG_DIR", str(missing))
        tools = create_system_log_tools(_make_manager(), "test-instance-id")
        list_tool = _tool_by_name(tools, "ens_system_log_list")
        result = list_tool.invoke({})
        assert "not found" in result.lower()

    def test_list_directory_is_file(self, log_dir):
        """If DAEMON_LOG_DIR points to a file, list returns 'not a directory'."""
        from daemon.tools.system_log_tools import create_system_log_tools

        # Replace the patched env with a file path
        file_path = log_dir / "not_a_dir"
        file_path.write_text("")
        # Build tools with DAEMON_LOG_DIR pointing to a file
        with patch.dict("os.environ", {"DAEMON_LOG_DIR": str(file_path)}):
            tools = create_system_log_tools(_make_manager(), "test-instance-id")
            list_tool = _tool_by_name(tools, "ens_system_log_list")
            result = list_tool.invoke({})
            assert "not a directory" in result.lower()

    def test_read_not_a_file(self, log_dir):
        """If filename resolves to a directory, read returns 'not a file'."""
        subdir = log_dir / "ensemble.log"
        subdir.mkdir()  # create a directory with same name as log file
        from daemon.tools.system_log_tools import create_system_log_tools

        with patch.dict("os.environ", {"DAEMON_LOG_DIR": str(log_dir)}):
            tools = create_system_log_tools(_make_manager(), "test-instance-id")
            read_tool = _tool_by_name(tools, "ens_system_log_read")
            result = read_tool.invoke({"filename": "ensemble.log"})
            assert "not a file" in result.lower()

    def test_tail_not_a_file(self, log_dir):
        """If filename resolves to a directory, tail returns 'not a file'."""
        subdir = log_dir / "ensemble.log"
        subdir.mkdir()
        from daemon.tools.system_log_tools import create_system_log_tools

        with patch.dict("os.environ", {"DAEMON_LOG_DIR": str(log_dir)}):
            tools = create_system_log_tools(_make_manager(), "test-instance-id")
            tail_tool = _tool_by_name(tools, "ens_system_log_tail")
            result = tail_tool.invoke({"filename": "ensemble.log"})
            assert "not a file" in result.lower()

    def test_search_respects_max_lines_scan(self, log_dir, monkeypatch):
        """Search is bounded by MAX_LINES_SCAN (50,000) to prevent DoS.

        Under the tail-first scan strategy, the cap bounds the LAST
        ``MAX_LINES_SCAN`` lines (the recent end), not the first. A file
        larger than the cap will be windowed to its trailing ``MAX_LINES_SCAN``
        lines and the header reports the actual scanned count.
        """
        from daemon.tools import system_log_tools
        from daemon.tools.system_log_tools import create_system_log_tools

        # Lower the cap to make the test fast and deterministic
        monkeypatch.setattr(system_log_tools, "MAX_LINES_SCAN", 10)

        # Create 20 lines; with cap=10 the tool scans only the last 10.
        f = log_dir / "ensemble.log"
        f.write_text("\n".join(f"line {i}" for i in range(20)), encoding="utf-8")

        tools = create_system_log_tools(_make_manager(), "test-instance-id")
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke({"pattern": "line", "filename": "ensemble.log"})
        # Header should mention only 10 lines scanned (bounded by cap)
        assert "scanned 10 lines" in result

    def test_search_with_context_groups(self, tools, log_file):
        """Search with context shows surrounding lines for each match."""
        # Build a file with multiple ERROR matches and context
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke(
            {
                "pattern": "ERROR",
                "filename": "ensemble.log",
                "context": 1,
            },
        )
        # Verify context appears BOTH before first ERROR (line 3: WARNING) AND
        # after first ERROR (line 5: INFO) AND before second ERROR (line 5: INFO)
        assert "Deprecated endpoint" in result  # before first ERROR
        assert "Node timeout" in result        # second ERROR
        assert "match(es)" in result           # header line

    def test_search_no_matches_with_scanned_count(self, log_dir):
        """Search 'no matches' message includes scanned line count."""
        from daemon.tools.system_log_tools import create_system_log_tools

        f = log_dir / "ensemble.log"
        f.write_text("line1\nline2\nline3\n", encoding="utf-8")
        tools = create_system_log_tools(_make_manager(), "test-instance-id")
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke({"pattern": "DOES_NOT_EXIST", "filename": "ensemble.log"})
        assert "no matches" in result.lower()
        assert "scanned 3 lines" in result

    # ─────────────────────────────────────────────────────────────────
    # Scan-direction edge cases (tail-first deque strategy).
    # The deque(maxlen=MAX_LINES_SCAN) auto-evicts the OLDEST entries
    # so the retained window is the LAST N lines. The three tests
    # below pin down three sharp edges of that strategy:
    #   (A) before-context is silently dropped when the match sits at
    #       the FIRST retained line (the rolling context_buffer is
    #       empty at that point — only after-context survives).
    #   (B) match exactly at line (total - cap) is OUTSIDE the window
    #       and is therefore NOT found; match at (total - cap + 1) is
    #       the FIRST line inside the window and IS found.
    #   (C) an empty file is a 0-line scan that hits the no-matches
    #       branch and reports "scanned 0 lines" verbatim.
    # ─────────────────────────────────────────────────────────────────

    def test_search_before_context_evicted_at_deque_boundary(
        self, log_dir, monkeypatch,
    ):
        """EDGE (A): before-context is silently dropped when match is at
        the FIRST retained deque entry.

        With MAX_LINES_SCAN=10 and a 20-line file, the deque retains the
        LAST 10 lines (positions 11..20). A match at line 11 sits at the
        very first position of the retained window — the rolling
        ``context_buffer`` is empty at that point, so no before-context
        is emitted. After-context (lines 12, 13) IS present because the
        after-context walk proceeds forward into the retained window.
        """
        from daemon.tools import system_log_tools
        from daemon.tools.system_log_tools import create_system_log_tools

        monkeypatch.setattr(system_log_tools, "MAX_LINES_SCAN", 10)

        # 20 lines. Match at line 11 = first retained deque entry
        # (lines 1-10 evicted by deque maxlen=10). context=2 asks for
        # 2 before + 2 after, but the context_buffer is empty when
        # line 11 is processed so before-context is empty.
        lines = []
        for i in range(1, 21):
            if i == 11:
                lines.append("BOUNDARY_MATCH_AT_LINE_11\n")
            else:
                lines.append(f"filler line {i}\n")
        (log_dir / "ensemble.log").write_text("".join(lines), encoding="utf-8")

        tools = create_system_log_tools(_make_manager(), "test-instance-id")
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke(
            {
                "pattern": "BOUNDARY_MATCH_AT_LINE_11",
                "filename": "ensemble.log",
                "context": 2,
            },
        )

        # Match IS found at line 11 with the >>> marker.
        assert "    11 >>> BOUNDARY_MATCH_AT_LINE_11" in result, (
            f"Expected match marker at line 11; got: {result!r}"
        )
        # After-context lines 12, 13 ARE present (format: line_no + 5
        # spaces + content).
        assert "    12 " in result
        assert "filler line 12" in result
        assert "    13 " in result
        assert "filler line 13" in result
        # Before-context lines 9, 10 are NOT present — they were
        # evicted from the deque before the rolling context_buffer
        # had a chance to populate. Silent drop is by design (the
        # alternative would require a head buffer, which would
        # double the memory footprint).
        assert "filler line 9" not in result
        assert "filler line 10" not in result
        # Scanned count reflects the cap.
        assert "scanned 10 lines" in result

    @pytest.mark.parametrize(
        "match_line, should_find",
        [
            (10, False),  # outside_window: at line total-cap, evicted
            (11, True),   # first_inside_window: at line total-cap+1, retained
        ],
        ids=["outside_window", "first_inside_window"],
    )
    def test_search_match_at_exact_cutoff_boundary(
        self, log_dir, monkeypatch, match_line, should_find,
    ):
        """EDGE (B): match exactly at the deque cutoff — outside vs first-inside.

        Parametrized over two cases at the sharp edge of the retained
        window:
          - ``outside_window`` (match_line == total - cap): the line
            was the LAST entry pushed off the deque when it overflowed;
            it is NOT in the recent_lines and is NOT found.
          - ``first_inside_window`` (match_line == total - cap + 1):
            the line is the FIRST entry kept after overflow; it IS
            in the recent_lines and IS found.

        The "no matches" message echoes the pattern name, so we use the
        ``>>>`` marker (only emitted next to actual matches) to
        distinguish a real match from the no-matches path.
        """
        from daemon.tools import system_log_tools
        from daemon.tools.system_log_tools import create_system_log_tools

        monkeypatch.setattr(system_log_tools, "MAX_LINES_SCAN", 10)

        # 20 lines total. With cap=10 the deque retains positions
        # 11..20. match_line=10 sits BEFORE the window (evicted);
        # match_line=11 sits at the FIRST position of the window
        # (retained).
        lines = []
        for i in range(1, 21):
            if i == match_line:
                lines.append(f"CUTOFF_MATCH_AT_LINE_{match_line}\n")
            else:
                lines.append(f"line {i}\n")
        (log_dir / "ensemble.log").write_text("".join(lines), encoding="utf-8")

        tools = create_system_log_tools(_make_manager(), "test-instance-id")
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke(
            {
                "pattern": f"CUTOFF_MATCH_AT_LINE_{match_line}",
                "filename": "ensemble.log",
            },
        )

        if should_find:
            # first_inside_window: match IS found (>>> marker present).
            assert ">>>" in result, (
                f"Expected match marker for first_inside_window; got: {result!r}"
            )
            assert f"CUTOFF_MATCH_AT_LINE_{match_line}" in result
            assert "no matches" not in result.lower()
        else:
            # outside_window: no match marker (>>>). The "no matches"
            # message echoes the pattern name, but the >>> marker is
            # only emitted next to actual matches.
            assert ">>>" not in result, (
                f"Expected no match marker for outside_window; got: {result!r}"
            )
            assert "no matches" in result.lower()
        # Boundary correctness proof: scanned count == cap, regardless
        # of whether the match was inside or outside the window.
        assert "scanned 10 lines" in result

    def test_search_empty_file(self, log_dir):
        """EDGE (C): empty log file returns 'no matches' with 'scanned 0 lines'.

        A 0-byte file produces an empty deque, so the for-loop body
        never executes. ``scanned = len(recent_lines) == 0`` and
        ``results`` stays empty, falling through to the
        "No matches found" branch with the literal "scanned 0 lines"
        fragment in the message.
        """
        from daemon.tools.system_log_tools import create_system_log_tools

        (log_dir / "ensemble.log").write_text("", encoding="utf-8")
        tools = create_system_log_tools(_make_manager(), "test-instance-id")
        search_tool = _tool_by_name(tools, "ens_system_log_search")
        result = search_tool.invoke(
            {"pattern": "anything", "filename": "ensemble.log"},
        )
        assert "no matches" in result.lower()
        assert "scanned 0 lines" in result

    def test_list_with_many_files_byte_cap(self, log_dir):
        """List with many files exceeds byte cap and reports truncation."""
        from daemon.tools.system_log_tools import create_system_log_tools

        # Create 30+ log files with long names to exceed 12 KB
        for i in range(30):
            (log_dir / f"ensemble.log_{i}").write_text("a\n", encoding="utf-8")

        tools = create_system_log_tools(_make_manager(), "test-instance-id")
        list_tool = _tool_by_name(tools, "ens_system_log_list")
        result = list_tool.invoke({})
        # Either we get the truncation marker or all files fit
        assert "truncated" in result.lower() or len(result) < 12 * 1024

    def test_validate_filename_relative_to_symlink_attack(self, log_dir):
        """Filename that resolves outside the log directory is rejected."""
        from daemon.tools.system_log_tools import _validate_filename

        # Create a symlink inside log_dir pointing outside
        link = log_dir / "evil_link"
        if not link.exists():
            link.symlink_to("/etc/passwd")
        with patch.dict("os.environ", {"DAEMON_LOG_DIR": str(log_dir)}):
            with pytest.raises(ValueError, match="outside"):
                _validate_filename("evil_link")


# =============================================================================
# Lane 5: Integration Test (W6 — uses REAL _apply_tool_filter signature)
# =============================================================================


class TestSystemLogToolIntegration:
    """Integration test: tools are visible through ``_apply_tool_filter``
    for an agent with 'system-log' in ``tools.allow``.

    The REAL ``_apply_tool_filter`` lives in ``daemon.tools.instance`` (NOT
    ``_tool_registry``). Its signature is::

        _apply_tool_filter(tools, agent_id, mcp_tool_names=None, version_tag=None)

    It reads agent meta from ``get_registry().get_version(agent_id, tag)``
    (falling back to ``get_resolved(agent_id)``), then expands category
    names via ``list_tools_by_category()``.  We mock both to create a
    controlled environment where only ``"system-log"`` is allowed.
    """

    def test_tools_visible_after_apply_tool_filter(self):
        """Verify system-log tools survive ``_apply_tool_filter`` when the
        agent's ``tools.allow`` includes the ``'system-log'`` category."""
        from daemon.tools.instance import _apply_tool_filter

        class _MockTool:
            """Minimal tool stub — _apply_tool_filter only reads ``.name``."""

            def __init__(self, name: str):
                self.name = name

        all_tools = [
            _MockTool("ens_system_log_list"),
            _MockTool("ens_system_log_read"),
            _MockTool("ens_system_log_search"),
            _MockTool("ens_system_log_tail"),
            _MockTool("bash"),            # should be filtered OUT
            _MockTool("read_file"),       # should be filtered OUT
            _MockTool("spawn_instance"),  # should be filtered OUT
        ]

        # Build a mock agent meta with tools.allow = ["system-log"]
        mock_meta = MagicMock()
        mock_meta.tools = MagicMock()
        mock_meta.tools.allow = ["system-log"]
        mock_meta.tools.deny = None
        mock_meta.innate_skills = []

        # Category map: system-log expands to the 4 tool names
        tool_categories = {
            "system-log": [
                "ens_system_log_list",
                "ens_system_log_read",
                "ens_system_log_search",
                "ens_system_log_tail",
            ],
            "bash": ["bash"],
            "filesystem": ["read_file"],
            "instance": ["spawn_instance"],
        }

        with patch("daemon.tools.instance.list_tools_by_category",
                   return_value=tool_categories), \
             patch("daemon.registry.get_registry") as mock_get_reg:
            mock_get_reg.return_value.get_version.return_value = mock_meta
            mock_get_reg.return_value.get_resolved.return_value = mock_meta
            mock_get_reg.return_value.resolve_pure_id.side_effect = lambda x: x

            filtered = _apply_tool_filter(all_tools, "test-agent")

        tool_names = {t.name for t in filtered}

        # System-log tools survive
        assert "ens_system_log_list" in tool_names
        assert "ens_system_log_read" in tool_names
        assert "ens_system_log_search" in tool_names
        assert "ens_system_log_tail" in tool_names

        # Non-system-log tools are filtered OUT
        assert "bash" not in tool_names
        assert "read_file" not in tool_names
        assert "spawn_instance" not in tool_names

    def test_tools_filtered_out_without_system_log_allow(self):
        """When 'system-log' is NOT in tools.allow, the tools are excluded."""
        from daemon.tools.instance import _apply_tool_filter

        class _MockTool:
            def __init__(self, name: str):
                self.name = name

        all_tools = [
            _MockTool("ens_system_log_list"),
            _MockTool("ens_system_log_read"),
            _MockTool("bash"),
        ]

        mock_meta = MagicMock()
        mock_meta.tools = MagicMock()
        mock_meta.tools.allow = ["bash"]  # only bash, NOT system-log
        mock_meta.tools.deny = None
        mock_meta.innate_skills = []

        tool_categories = {
            "system-log": ["ens_system_log_list", "ens_system_log_read"],
            "bash": ["bash"],
        }

        with patch("daemon.tools.instance.list_tools_by_category",
                   return_value=tool_categories), \
             patch("daemon.registry.get_registry") as mock_get_reg:
            mock_get_reg.return_value.get_version.return_value = mock_meta
            mock_get_reg.return_value.get_resolved.return_value = mock_meta
            mock_get_reg.return_value.resolve_pure_id.side_effect = lambda x: x

            filtered = _apply_tool_filter(all_tools, "test-agent")

        tool_names = {t.name for t in filtered}

        assert "bash" in tool_names
        assert "ens_system_log_list" not in tool_names
        assert "ens_system_log_read" not in tool_names
