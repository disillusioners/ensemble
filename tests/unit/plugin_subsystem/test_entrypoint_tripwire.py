"""Entrypoint ≤200-line tripwire unit tests (CON §2, slice ② carry-forward 4).

Constructs synthetic plugin trees in ``tmp_path`` with controlled
entrypoint file sizes and asserts each tripwire status. The mechanism
lands at slice ② even though real ``adapter/`` trees arrive at slice ⑤;
these offline tests prove the tripwire is wired correctly and the
thresholds behave as CON §2 prescribes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daemon.plugin_subsystem.entrypoint_tripwire import (
    ALARM_THRESHOLD,
    REFUSE_THRESHOLD,
    STATUS_ALARM,
    STATUS_MISSING,
    STATUS_NOT_APPLICABLE,
    STATUS_OK,
    STATUS_REFUSE,
    check_entrypoint,
    run_tripwire,
)
from daemon.plugin_subsystem.plugin_declaration import PluginDeclaration
from tests.unit.plugin_subsystem._manifest_fixtures import build_plugin


def _write_lines(path: Path, count: int) -> None:
    """Write ``count`` lines to ``path``; last line has a trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"line {i}" for i in range(count)) + "\n", encoding="utf-8")


def _make_b_plugin(tmp_path: Path, name: str, *, entrypoint_lines: int | None = None) -> Path:
    """Build a B-path plugin tree. ``entrypoint_lines`` is currently unused —
    kept for symmetry with future fixtures that may want to set up a default
    entrypoint file; callers write the file explicitly via ``_write_lines``
    when they need one."""
    plugin_dir = build_plugin(
        tmp_path,
        None,  # no manifest; we'll inject a fake declaration below
        name=name,
    )
    # build_plugin with manifest_text=None skips writing the manifest; the
    # check_entrypoint call needs a declaration, so we manually write a
    # B-path manifest to keep the reader happy in the tripwire CLI path,
    # but for check_entrypoint the declaration is passed directly.
    return plugin_dir


# -- check_entrypoint direct tests --------------------------------------------------


class TestCheckEntrypoint:
    def test_no_entrypoint_returns_not_applicable(self, tmp_path: Path):
        # Resource-only plugin: no entrypoint declared.
        plugin_dir = _make_b_plugin(tmp_path, "rp")
        declaration = PluginDeclaration(
            name="rp",
            license="Apache-2.0",
            upstream_repo="https://example.com/rp.git",
            tag_pin_per_class={"copy_freely": "v1.0.0"},
            integration_path="C",
            execution_mode="resource-only",
            source_dir=plugin_dir,
        )
        result = check_entrypoint(plugin_dir, declaration)
        assert result.status == STATUS_NOT_APPLICABLE
        assert result.line_count is None
        assert result.entrypoint is None
        assert not result.is_failure

    def test_missing_entrypoint_file_refused(self, tmp_path: Path):
        # B-path with entrypoint declared but the file is absent.
        plugin_dir = _make_b_plugin(tmp_path, "mp")
        declaration = PluginDeclaration(
            name="mp",
            license="MIT",
            upstream_repo="https://example.com/mp.git",
            tag_pin_per_class={"copy_freely": "v2.0.0"},
            integration_path="B",
            execution_mode="lifted-symbol",
            entrypoint="adapter/entry.ts",
            lifted_symbol="generate",
            ipc_version=1,
            source_dir=plugin_dir,
        )
        result = check_entrypoint(plugin_dir, declaration)
        assert result.status == STATUS_MISSING
        assert not result.is_failure  # missing is not the refuse threshold
        assert result.message  # surfaces a clear reason

    def test_under_threshold_is_ok(self, tmp_path: Path):
        plugin_dir = _make_b_plugin(tmp_path, "ok-plugin")
        _write_lines(plugin_dir / "adapter" / "entry.ts", 200)
        declaration = PluginDeclaration(
            name="ok-plugin",
            license="MIT",
            upstream_repo="https://example.com/ok.git",
            tag_pin_per_class={"copy_freely": "v2.0.0"},
            integration_path="B",
            execution_mode="lifted-symbol",
            entrypoint="adapter/entry.ts",
            lifted_symbol="generate",
            ipc_version=1,
            source_dir=plugin_dir,
        )
        result = check_entrypoint(plugin_dir, declaration)
        assert result.status == STATUS_OK
        assert result.line_count == 200
        assert not result.is_failure

    def test_at_alarm_threshold_is_alarm(self, tmp_path: Path):
        plugin_dir = _make_b_plugin(tmp_path, "alarm-plugin")
        _write_lines(plugin_dir / "adapter" / "entry.ts", ALARM_THRESHOLD + 1)
        declaration = PluginDeclaration(
            name="alarm-plugin",
            license="MIT",
            upstream_repo="https://example.com/alarm.git",
            tag_pin_per_class={"copy_freely": "v2.0.0"},
            integration_path="B",
            execution_mode="lifted-symbol",
            entrypoint="adapter/entry.ts",
            lifted_symbol="generate",
            ipc_version=1,
            source_dir=plugin_dir,
        )
        result = check_entrypoint(plugin_dir, declaration)
        assert result.status == STATUS_ALARM
        assert result.line_count == ALARM_THRESHOLD + 1
        assert not result.is_failure  # alarm does not fail the run

    def test_at_refuse_threshold_is_refuse(self, tmp_path: Path):
        plugin_dir = _make_b_plugin(tmp_path, "refuse-plugin")
        _write_lines(plugin_dir / "adapter" / "entry.ts", REFUSE_THRESHOLD + 1)
        declaration = PluginDeclaration(
            name="refuse-plugin",
            license="MIT",
            upstream_repo="https://example.com/refuse.git",
            tag_pin_per_class={"copy_freely": "v2.0.0"},
            integration_path="B",
            execution_mode="lifted-symbol",
            entrypoint="adapter/entry.ts",
            lifted_symbol="generate",
            ipc_version=1,
            source_dir=plugin_dir,
        )
        result = check_entrypoint(plugin_dir, declaration)
        assert result.status == STATUS_REFUSE
        assert result.line_count == REFUSE_THRESHOLD + 1
        assert result.is_failure

    def test_thresholds_default_to_con_values(self):
        # CON §2 line 52: alarm 220, refuse 300. The module exports the
        # CON-prescribed values; the test pins them so any silent change
        # is a visible test failure (the values are FROZEN per CON §2).
        assert ALARM_THRESHOLD == 220
        assert REFUSE_THRESHOLD == 300

    def test_custom_thresholds_overridden(self, tmp_path: Path):
        plugin_dir = _make_b_plugin(tmp_path, "custom")
        _write_lines(plugin_dir / "adapter" / "entry.ts", 5)
        declaration = PluginDeclaration(
            name="custom",
            license="MIT",
            upstream_repo="https://example.com/custom.git",
            tag_pin_per_class={"copy_freely": "v2.0.0"},
            integration_path="B",
            execution_mode="lifted-symbol",
            entrypoint="adapter/entry.ts",
            lifted_symbol="generate",
            ipc_version=1,
            source_dir=plugin_dir,
        )
        # 5 lines is OK at CON defaults but REFUSE at custom threshold=3.
        result = check_entrypoint(plugin_dir, declaration, alarm_threshold=2, refuse_threshold=3)
        assert result.status == STATUS_REFUSE
        assert result.threshold_alarm == 2
        assert result.threshold_refuse == 3

    def test_empty_file_counts_as_zero_lines(self, tmp_path: Path):
        plugin_dir = _make_b_plugin(tmp_path, "empty")
        (plugin_dir / "adapter").mkdir(parents=True, exist_ok=True)
        (plugin_dir / "adapter" / "entry.ts").write_text("", encoding="utf-8")
        declaration = PluginDeclaration(
            name="empty",
            license="MIT",
            upstream_repo="https://example.com/empty.git",
            tag_pin_per_class={"copy_freely": "v2.0.0"},
            integration_path="B",
            execution_mode="lifted-symbol",
            entrypoint="adapter/entry.ts",
            lifted_symbol="generate",
            ipc_version=1,
            source_dir=plugin_dir,
        )
        result = check_entrypoint(plugin_dir, declaration)
        assert result.status == STATUS_OK
        assert result.line_count == 0

    def test_trailing_partial_line_counts_as_one(self, tmp_path: Path):
        plugin_dir = _make_b_plugin(tmp_path, "partial")
        (plugin_dir / "adapter").mkdir(parents=True, exist_ok=True)
        (plugin_dir / "adapter" / "entry.ts").write_text("a\nb\nc", encoding="utf-8")
        declaration = PluginDeclaration(
            name="partial",
            license="MIT",
            upstream_repo="https://example.com/partial.git",
            tag_pin_per_class={"copy_freely": "v2.0.0"},
            integration_path="B",
            execution_mode="lifted-symbol",
            entrypoint="adapter/entry.ts",
            lifted_symbol="generate",
            ipc_version=1,
            source_dir=plugin_dir,
        )
        result = check_entrypoint(plugin_dir, declaration)
        assert result.line_count == 3  # 2 LF + 1 trailing partial

    def test_as_dict_is_json_serializable(self, tmp_path: Path):
        plugin_dir = _make_b_plugin(tmp_path, "js")
        _write_lines(plugin_dir / "adapter" / "entry.ts", 10)
        declaration = PluginDeclaration(
            name="js",
            license="MIT",
            upstream_repo="https://example.com/js.git",
            tag_pin_per_class={"copy_freely": "v2.0.0"},
            integration_path="B",
            execution_mode="lifted-symbol",
            entrypoint="adapter/entry.ts",
            lifted_symbol="generate",
            ipc_version=1,
            source_dir=plugin_dir,
        )
        result = check_entrypoint(plugin_dir, declaration)
        # must round-trip through json.dumps (CI wires this into a report)
        json.dumps(result.as_dict())


# -- run_tripwire aggregate tests ---------------------------------------------------


class TestRunTripwire:
    def _declaration(self, plugin_dir: Path, name: str, *, entrypoint: str | None) -> PluginDeclaration:
        return PluginDeclaration(
            name=name,
            license="MIT",
            upstream_repo=f"https://example.com/{name}.git",
            tag_pin_per_class={"copy_freely": "v2.0.0"},
            integration_path="B" if entrypoint else "C",
            execution_mode="lifted-symbol" if entrypoint else "resource-only",
            entrypoint=entrypoint,
            lifted_symbol="generate" if entrypoint else None,
            ipc_version=1 if entrypoint else None,
            source_dir=plugin_dir,
        )

    def test_aggregate_over_mixed_statuses(self, tmp_path: Path):
        # Three plugins: one ok, one alarm, one refuse.
        ok_dir = tmp_path / "ok"
        ok_dir.mkdir()
        _write_lines(ok_dir / "adapter" / "entry.ts", 100)
        ok_decl = self._declaration(ok_dir, "ok", entrypoint="adapter/entry.ts")

        alarm_dir = tmp_path / "alarm"
        alarm_dir.mkdir()
        _write_lines(alarm_dir / "adapter" / "entry.ts", ALARM_THRESHOLD + 5)
        alarm_decl = self._declaration(alarm_dir, "alarm", entrypoint="adapter/entry.ts")

        refuse_dir = tmp_path / "refuse"
        refuse_dir.mkdir()
        _write_lines(refuse_dir / "adapter" / "entry.ts", REFUSE_THRESHOLD + 5)
        refuse_decl = self._declaration(refuse_dir, "refuse", entrypoint="adapter/entry.ts")

        report = run_tripwire({"ok": ok_decl, "alarm": alarm_decl, "refuse": refuse_decl})
        assert report["checked"] == 3
        assert report["alarm"] == 1
        assert report["refused"] == 1
        assert report["ok"] == 1
        assert report["ok_overall"] is False  # refuse fails the aggregate
        statuses = {r["plugin"]: r["status"] for r in report["results"]}
        assert statuses == {"ok": STATUS_OK, "alarm": STATUS_ALARM, "refuse": STATUS_REFUSE}

    def test_empty_declarations_map(self):
        report = run_tripwire({})
        assert report["checked"] == 0
        assert report["ok_overall"] is True

    def test_skips_declarations_without_source_dir(self):
        decl = PluginDeclaration(
            name="nosrc",
            license="MIT",
            upstream_repo="https://example.com/nosrc.git",
            tag_pin_per_class={"copy_freely": "v2.0.0"},
            integration_path="C",
            execution_mode="resource-only",
            source_dir=None,
        )
        report = run_tripwire({"nosrc": decl})
        assert report["checked"] == 0
        assert report["ok_overall"] is True
