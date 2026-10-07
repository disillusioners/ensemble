"""Schema-CI entry point tests — component 18 (manifests portion).

Reports are JSON-serializable; per-plugin refusals fail the aggregate
report fail-closed (a dir without MANIFEST.yaml is a reported refusal,
never a skip).
"""

from __future__ import annotations

import json

from daemon.plugin_subsystem.schema_ci import main, run_ci, validate_plugin_dir
from tests.unit.plugin_subsystem._manifest_fixtures import (
    VALID_MINIMAL_MANIFEST,
    build_plugin,
)


class TestValidatePluginDir:
    def test_valid_plugin_report(self, valid_minimal_plugin):
        report = validate_plugin_dir(valid_minimal_plugin)
        assert report["ok"] is True
        assert "refusal" not in report
        assert report["declaration"]["name"] == "test-plugin"
        assert report["declaration"]["execution_mode"] == "resource-only"
        json.dumps(report)  # must be JSON-serializable

    def test_invalid_plugin_report_carries_refusal(self, tmp_path):
        plugin_dir = build_plugin(tmp_path, VALID_MINIMAL_MANIFEST, name="test-plugin")
        (plugin_dir / "MANIFEST.yaml").write_text("schema_version: \"9.9.9\"\n", encoding="utf-8")
        report = validate_plugin_dir(plugin_dir)
        assert report["ok"] is False
        assert report["refusal"]["code"] == "schema_version_unsupported"
        json.dumps(report)


class TestRunCi:
    def test_mixed_root_fail_closed(self, tmp_path):
        build_plugin(tmp_path, VALID_MINIMAL_MANIFEST, name="good-plugin")
        bad = build_plugin(tmp_path, VALID_MINIMAL_MANIFEST.replace('"1.0.0"', '"2.0.0"'), name="bad-plugin")
        report = run_ci(tmp_path)
        assert report["checked"] == 2
        assert report["passed"] == 1
        assert report["failed"] == 1
        assert report["ok"] is False
        failed_names = [r["plugin"] for r in report["plugins"] if not r["ok"]]
        assert failed_names == ["bad-plugin"]
        json.dumps(report)

    def test_missing_manifest_dir_is_refusal_not_skip(self, tmp_path):
        build_plugin(tmp_path, None, name="empty-plugin")
        report = run_ci(tmp_path)
        assert report["ok"] is False
        assert report["plugins"][0]["refusal"]["code"] == "manifest_missing"

    def test_all_valid_root_passes(self, tmp_path):
        build_plugin(tmp_path, VALID_MINIMAL_MANIFEST, name="good-plugin")
        report = run_ci(tmp_path)
        assert report["ok"] is True
        assert report["failed"] == 0

    def test_absent_root_is_ok_empty(self, tmp_path):
        report = run_ci(tmp_path / "does-not-exist")
        assert report["ok"] is True and report["checked"] == 0


class TestCli:
    def test_cli_exit_code_1_on_refusal(self, tmp_path, capsys):
        build_plugin(tmp_path, VALID_MINIMAL_MANIFEST.replace('"1.0.0"', '"2.0.0"'), name="bad-plugin")
        exit_code = main([str(tmp_path)])
        assert exit_code == 1
        report = json.loads(capsys.readouterr().out)
        assert report["ok"] is False

    def test_cli_exit_code_0_when_all_valid(self, tmp_path, capsys):
        build_plugin(tmp_path, VALID_MINIMAL_MANIFEST, name="good-plugin")
        exit_code = main([str(tmp_path)])
        assert exit_code == 0
        report = json.loads(capsys.readouterr().out)
        assert report["ok"] is True


class TestCiRunnerEntrypointTripwireDefaultOn:
    """Slice-⑤ fix-loop-3 fold-in: the ``plugins-convention/ci_runner.py``
    wrapper runs the ≤200-line entrypoint tripwire BY DEFAULT.

    The original default-off rationale ("would surface missing findings on
    every C-path plugin we ship at slice ②") no longer holds: plugin #1
    (opendesign) is B-path with real ``adapter/`` files, and
    ``check_entrypoint`` returns ``not_applicable`` (not ``missing``) for
    plugins that declare no entrypoint. A default-off tripwire let the
    entrypoint guard silently rot. (Tripwire REFUSE-folding semantics on
    synthetic B-path fixtures are covered by test_entrypoint_tripwire.py.)
    """

    @staticmethod
    def _load_ci_runner():
        import importlib.util
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        path = repo_root / "plugins-convention" / "ci_runner.py"
        spec = importlib.util.spec_from_file_location("ci_runner_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _plugins_root():
        from pathlib import Path

        return Path(__file__).resolve().parents[3] / "plugins"

    def test_default_invocation_runs_tripwire(self, capsys, monkeypatch):
        module = self._load_ci_runner()
        monkeypatch.setattr("sys.argv", ["ci_runner", str(self._plugins_root())])
        exit_code = module.main()
        report = json.loads(capsys.readouterr().out)
        assert "entrypoint_tripwire" in report, (
            "ci_runner must run the entrypoint tripwire by DEFAULT"
        )
        assert report["entrypoint_tripwire"]["ok_overall"] is True
        assert exit_code == 0

    def test_opt_out_flag_skips_tripwire(self, capsys, monkeypatch):
        module = self._load_ci_runner()
        monkeypatch.setattr(
            "sys.argv",
            ["ci_runner", str(self._plugins_root()), "--no-entrypoint-tripwire"],
        )
        exit_code = module.main()
        report = json.loads(capsys.readouterr().out)
        assert "entrypoint_tripwire" not in report
        assert exit_code == 0
