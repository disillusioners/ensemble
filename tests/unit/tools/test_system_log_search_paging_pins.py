"""Pins for the Component-B observability cure: ``ens_system_log_search``
gains an optional, additive ``start_line`` parameter.

These are mutation-backed pins (``_pins`` qualifier). They assert the
default path stays byte-identical, the explicit window replaces (never
extends) the tail window, the 1-based invariant, the bounds, and the
additive-only schema. They do NOT edit ``tests/test_system_log_tools.py``
(the byte-identity net), which must pass unchanged.
"""

from __future__ import annotations

import inspect

import pytest

from daemon.tools import _tool_registry

FROZEN_DEFAULT_LITERAL = (
    "File: ensemble.log | Pattern: revive | 1 match(es) | scanned 10 lines\n"
    "    39 >>> 2026-08-08 08:00:39 - daemon.graph - ERROR - revive marker at line 39"
)


def _make_manager():
    from unittest.mock import MagicMock

    manager = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    return manager


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    d.mkdir()
    monkeypatch.setenv("DAEMON_LOG_DIR", str(d))
    return d


@pytest.fixture
def tools(log_dir, monkeypatch):
    import daemon.tools.system_log_tools as m

    monkeypatch.setattr(m, "MAX_LINES_SCAN", 10)
    from daemon.tools.system_log_tools import create_system_log_tools

    return create_system_log_tools(_make_manager(), "test-instance-id")


def _write_fixture(log_dir, n=40):
    lines = [f"2026-08-08 08:00:{(i % 60):02d} - daemon.api - INFO - line number {i}" for i in range(1, n + 1)]
    # marker before the tail window (line 2) and inside the tail window (line 39)
    if n >= 2:
        lines[1] = "2026-08-08 08:00:02 - daemon.api - ERROR - revive marker at line 2"
    if n >= 39:
        lines[38] = "2026-08-08 08:00:39 - daemon.graph - ERROR - revive marker at line 39"
    (log_dir / "ensemble.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def _search(tools):
    return [t for t in tools if t.name == "ens_system_log_search"][0]


class TestSystemLogSearchStartLinePaging:
    def test_default_path_output_is_byte_identical_to_pre_change(self, log_dir, tools, monkeypatch):
        import daemon.tools.system_log_tools as m

        monkeypatch.setattr(m, "MAX_LINES_SCAN", 10)
        _write_fixture(log_dir, 40)
        search = _search(tools)
        out_absent = search.invoke({"pattern": "revive", "filename": "ensemble.log"})
        out_none = search.invoke({"pattern": "revive", "filename": "ensemble.log", "start_line": None})
        assert out_absent == FROZEN_DEFAULT_LITERAL
        assert out_none == FROZEN_DEFAULT_LITERAL
        assert out_absent == out_none

    def test_default_path_window_is_unchanged_tail_window(self, log_dir, tools, monkeypatch):
        import daemon.tools.system_log_tools as m

        monkeypatch.setattr(m, "MAX_LINES_SCAN", 10)
        # marker only before the tail window
        lines = [f"2026-08-08 08:00:{(i % 60):02d} - daemon.api - INFO - line number {i}" for i in range(1, 41)]
        lines[1] = "2026-08-08 08:00:02 - daemon.api - ERROR - revive marker at line 2"
        (log_dir / "ensemble.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        search = _search(tools)
        out = search.invoke({"pattern": "revive", "filename": "ensemble.log"})
        assert "No matches found" in out
        assert "scanned 10 lines" in out

    def test_explicit_window_finds_match_before_tail_window(self, log_dir, tools, monkeypatch):
        import daemon.tools.system_log_tools as m

        monkeypatch.setattr(m, "MAX_LINES_SCAN", 10)
        _write_fixture(log_dir, 40)
        search = _search(tools)
        default_out = search.invoke({"pattern": "revive", "filename": "ensemble.log"})
        assert "No matches found" in default_out or "revive marker at line 2" not in default_out
        explicit_out = search.invoke({"pattern": "revive", "filename": "ensemble.log", "start_line": 1})
        assert "    2 >>>" in explicit_out
        assert "revive marker at line 2" in explicit_out
        assert "window 1-10 of 40 lines" in explicit_out
        assert "revive marker at line 39" not in explicit_out

    def test_explicit_window_reports_window_range_and_total(self, log_dir, tools, monkeypatch):
        import daemon.tools.system_log_tools as m

        monkeypatch.setattr(m, "MAX_LINES_SCAN", 10)
        _write_fixture(log_dir, 40)
        search = _search(tools)
        out = search.invoke({"pattern": "revive", "filename": "ensemble.log", "start_line": 1})
        assert "window 1-10 of 40 lines" in out

    def test_explicit_window_reports_unsearched_region_and_resume_point(self, log_dir, tools, monkeypatch):
        import daemon.tools.system_log_tools as m

        monkeypatch.setattr(m, "MAX_LINES_SCAN", 10)
        _write_fixture(log_dir, 40)
        search = _search(tools)
        out = search.invoke({"pattern": "zzznomatch", "filename": "ensemble.log", "start_line": 1})
        assert "NOT SEARCHED: lines 11-40" in out
        assert "re-run with start_line=11" in out
        assert "window 1-10 of 40 lines" in out

    def test_explicit_window_respects_match_limit(self, log_dir, tools, monkeypatch):
        import daemon.tools.system_log_tools as m

        monkeypatch.setattr(m, "MAX_LINES_SCAN", 10)
        lines = [f"2026-08-08 08:00:00 - daemon.api - INFO - revive line {i}" for i in range(1, 11)]
        (log_dir / "ensemble.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        search = _search(tools)
        out = search.invoke({"pattern": "revive", "filename": "ensemble.log", "start_line": 1, "limit": 1})
        assert "match cap 1 reached" in out

    def test_explicit_window_starts_at_start_line_and_excludes_earlier_lines(self, log_dir, tools, monkeypatch):
        import daemon.tools.system_log_tools as m

        monkeypatch.setattr(m, "MAX_LINES_SCAN", 10)
        lines = [f"2026-08-08 08:00:{(i % 60):02d} - daemon.api - INFO - plain line {i}" for i in range(1, 41)]
        # unique markers at absolute 1-based lines 14, 15, 24, 25
        lines[13] = "2026-08-08 08:00:14 - daemon.api - INFO - marker BEFORE"
        lines[14] = "2026-08-08 08:00:15 - daemon.api - INFO - marker ATSTART"
        lines[23] = "2026-08-08 08:00:24 - daemon.api - INFO - marker LASTINWINDOW"
        lines[24] = "2026-08-08 08:00:25 - daemon.api - INFO - marker AFTER"
        (log_dir / "ensemble.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        search = _search(tools)
        out = search.invoke({"pattern": "marker", "filename": "ensemble.log", "start_line": 15})
        # ATSTART found at absolute line 15
        assert "    15 >>>" in out
        assert "ATSTART" in out
        # BEFORE (line 14) excluded by the lower-bound guard
        assert "BEFORE" not in out
        # AFTER (line 25) outside the window
        assert "AFTER" not in out
        # window + NOT-SEARCHED region + resume point
        assert "window 15-24 of 40" in out
        assert "NOT SEARCHED: lines 25-40" in out
        assert "re-run with start_line=25" in out

        # pagination loop: start_line = last + 1
        out2 = search.invoke({"pattern": "marker", "filename": "ensemble.log", "start_line": 25})
        assert "    25 >>>" in out2
        assert "AFTER" in out2

    def test_start_line_is_appended_last_in_the_signature(self, log_dir, tools):
        import inspect

        search = _search(tools)
        func = getattr(search, "func", None)
        assert func is not None
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        assert params == ["pattern", "filename", "level", "context", "limit", "start_line"], params

        schema = search.args_schema
        if hasattr(schema, "model_fields"):
            fields = list(schema.model_fields.keys())
        elif hasattr(schema, "__fields__"):
            fields = list(schema.__fields__.keys())
        else:
            fields = list(schema.model_json_schema().get("properties", {}).keys())
        assert fields == ["pattern", "filename", "level", "context", "limit", "start_line"], fields

    def test_start_line_zero_is_rejected_with_friendly_error(self, log_dir, tools, monkeypatch):
        import daemon.tools.system_log_tools as m

        monkeypatch.setattr(m, "MAX_LINES_SCAN", 10)
        _write_fixture(log_dir, 40)
        search = _search(tools)
        out = search.invoke({"pattern": "revive", "filename": "ensemble.log", "start_line": 0})
        assert "must be an integer >= 1" in out
        assert "start_line=1" in out
        assert ">>>" not in out

    def test_start_line_negative_is_rejected_with_friendly_error(self, log_dir, tools, monkeypatch):
        import daemon.tools.system_log_tools as m

        monkeypatch.setattr(m, "MAX_LINES_SCAN", 10)
        _write_fixture(log_dir, 40)
        search = _search(tools)
        out = search.invoke({"pattern": "revive", "filename": "ensemble.log", "start_line": -1})
        assert "must be an integer >= 1" in out

    def test_start_line_beyond_eof_returns_distinct_message(self, log_dir, tools, monkeypatch):
        import daemon.tools.system_log_tools as m

        monkeypatch.setattr(m, "MAX_LINES_SCAN", 10)
        lines = ["a", "b", "c"]
        (log_dir / "ensemble.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        search = _search(tools)
        out = search.invoke({"pattern": "x", "filename": "ensemble.log", "start_line": 10000})
        assert "beyond the end" in out
        assert "3 lines" in out
        assert "No matches found" not in out

    def test_start_line_one_is_bounded_at_scan_cap(self, log_dir, tools, monkeypatch):
        import daemon.tools.system_log_tools as m

        monkeypatch.setattr(m, "MAX_LINES_SCAN", 10)
        _write_fixture(log_dir, 40)
        search = _search(tools)
        out = search.invoke({"pattern": "zzznomatch", "filename": "ensemble.log", "start_line": 1})
        assert "scanned 10 lines" in out
        assert "NOT SEARCHED" in out
        assert "re-run with start_line=11" in out

    def test_explicit_window_keeps_absolute_line_numbers(self, log_dir, tools, monkeypatch):
        import daemon.tools.system_log_tools as m

        monkeypatch.setattr(m, "MAX_LINES_SCAN", 10)
        lines = [f"line {i}" for i in range(1, 21)]
        lines[4] = "revive target at absolute line 5"
        (log_dir / "ensemble.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        search = _search(tools)
        out = search.invoke({"pattern": "revive", "filename": "ensemble.log", "start_line": 3})
        assert "    5 >>>" in out
        assert "window 3-12 of 20 lines" in out

    def test_explicit_window_preserves_context_level_and_redaction(self, log_dir, tools, monkeypatch):
        import daemon.tools.system_log_tools as m

        monkeypatch.setattr(m, "MAX_LINES_SCAN", 10)
        lines = [
            "context before 1",
            "context before 2",
            '2026-08-08 08:00:00 - daemon.api - ERROR - payload = {"api_key": "supersecret"}',
            "context after 1",
            "context after 2",
        ]
        (log_dir / "ensemble.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        search = _search(tools)
        out = search.invoke(
            {
                "pattern": "payload",
                "filename": "ensemble.log",
                "start_line": 1,
                "context": 2,
                "level": "ERROR",
            }
        )
        assert "[REDACTED]" in out
        assert "supersecret" not in out
        assert "context before" in out
        assert "context after" in out

    def test_start_line_is_optional_in_the_schema_and_existing_params_unchanged(self, log_dir, tools):
        search = _search(tools)
        schema = search.args_schema.model_json_schema()
        props = schema["properties"]
        # start_line optional, defaulted null
        assert "start_line" in props
        sl = props["start_line"]
        assert sl.get("default") is None
        assert "required" not in schema or "start_line" not in schema.get("required", [])
        # existing params
        assert props["pattern"]["type"] == "string"
        assert "pattern" in schema.get("required", [])
        assert props["filename"]["type"] == "string"
        assert props["filename"].get("default") == "ensemble.log"
        assert props["level"].get("default") is None
        assert props["context"].get("default") == 0
        assert props["limit"]["type"] == "integer"
        assert props["limit"].get("default") == 50


class TestSystemLogSearchRegistrationUnchanged:
    def test_tool_name_and_category_are_unchanged(self, log_dir, tools):
        search = _search(tools)
        assert search.name == "ens_system_log_search"
        assert getattr(search, "_tool_category", None) == "system-log"
        assert "ens_system_log_search" in _tool_registry.DYNAMIC_TOOL_NAMES
        assert _tool_registry.CATEGORY_MODULES["system-log"] == "daemon.tools.system_log_tools"

    def test_privileged_category_set_is_unchanged(self):
        assert "system-log" in _tool_registry.PRIVILEGED_TOOL_CATEGORIES
        assert len(_tool_registry.PRIVILEGED_TOOL_CATEGORIES) == 23
