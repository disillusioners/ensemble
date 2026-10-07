"""Tests for the slice-⑥ sync-result trail + the subprocess-seam pin.

Trail (REC comp 13's data source): every completed sync appends its
frozen-shape ``sync_result`` to ``<plugin_dir>/sync_trail.jsonl`` —
dry-run and real pulls both, refusals included; best-effort (a trail
failure never alters the returned result).

Subprocess-seam pin (carry-forward): the current seam tests inject
failures POST-conversion (e.g. a faulty ``cat_file_blob``); these
tests pin the CONVERSION ITSELF at the ``subprocess.run`` seam — a
``CalledProcessError`` raised by the real subprocess boundary must
surface as ``UpstreamContentIncompleteError`` from the sync call.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict

import pytest

from daemon.plugin_subsystem import sync
from daemon.plugin_subsystem.sync_runner import (
    SYNC_TRAIL_FILENAME,
    LocalGitCheckout,
    UpstreamContentIncompleteError,
)
from tests.unit.plugin_subsystem.test_sync_runner import (
    _init_git_repo,
    _make_minimal_plugin,
    _tag,
)


def _read_trail(plugin_dir: Path) -> list[dict]:
    text = (plugin_dir / SYNC_TRAIL_FILENAME).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ══════════════════════════════════════════════════════════════════════════════
# Trail
# ══════════════════════════════════════════════════════════════════════════════


class TestSyncTrail:
    def _repo_and_plugin(self, tmp_path: Path):
        repo = tmp_path / "upstream"
        _init_git_repo(repo, {"data/a.txt": "A\n"})
        _tag(repo, "v1.0.0")
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        return repo, plugin_dir

    def test_every_completed_sync_appends_a_line(self, tmp_path):
        repo, plugin_dir = self._repo_and_plugin(tmp_path)
        sync("demo", "copy_freely", upstream_repo=str(repo), upstream_tag="v1.0.0",
             plugin_dir=plugin_dir, dry_run=True)
        sync("demo", "copy_freely", upstream_repo=str(repo), upstream_tag="v1.0.0",
             plugin_dir=plugin_dir, dry_run=False)
        trail = _read_trail(plugin_dir)
        assert len(trail) == 2
        actions = [t["sync_result"]["action"] for t in trail]
        assert actions == ["clean_pulled", "clean_pulled"]  # dry + real
        # frozen CON §5 shape inside the trail envelope
        result = trail[0]["sync_result"]
        assert set(result) >= {
            "plugin", "target_class", "upstream_tag", "action",
            "diff_summary", "staleness_age_days",
        }
        assert set(trail[0]) == {"recorded_at", "sync_result"}

    def test_refusals_are_trailed_too(self, tmp_path):
        repo, plugin_dir = self._repo_and_plugin(tmp_path)
        sync("demo", "own_outright", upstream_repo=str(repo),
             plugin_dir=plugin_dir, dry_run=True)
        trail = _read_trail(plugin_dir)
        assert trail[0]["sync_result"]["action"] == "refused"
        assert trail[0]["sync_result"]["refusal"]["code"] == "own_outright_mutation"

    def test_trail_failure_never_breaks_the_sync(self, tmp_path, monkeypatch):
        repo, plugin_dir = self._repo_and_plugin(tmp_path)
        # make the trail path UNWRITABLE (a directory where the file
        # would be) — the sync must still succeed
        (plugin_dir / SYNC_TRAIL_FILENAME).mkdir()
        result = sync("demo", "copy_freely", upstream_repo=str(repo),
                      upstream_tag="v1.0.0", plugin_dir=plugin_dir, dry_run=True)
        assert result.action == "clean_pulled"

    def test_staleness_flows_through_the_trail(self, tmp_path):
        repo, plugin_dir = self._repo_and_plugin(tmp_path)
        sync("demo", "copy_freely", upstream_repo=str(repo), upstream_tag="v1.0.0",
             plugin_dir=plugin_dir, dry_run=True)
        trail = _read_trail(plugin_dir)
        assert isinstance(trail[0]["sync_result"]["staleness_age_days"], int)
        assert trail[0]["sync_result"]["staleness_age_days"] >= 0


# ══════════════════════════════════════════════════════════════════════════════
# Subprocess-seam pin (carry-forward)
# ══════════════════════════════════════════════════════════════════════════════


class TestSubprocessSeamPin:
    """A ``CalledProcessError`` AT the ``subprocess.run`` boundary
    converts to ``UpstreamContentIncompleteError`` — pinned at the
    seam itself, not post-conversion."""

    @pytest.fixture
    def seam_env(self, tmp_path, monkeypatch):
        repo = tmp_path / "upstream"
        _init_git_repo(
            repo,
            {
                "data/a.txt": "A\n",
                "prompts/x.ts": "export const x = 1;\n",
            },
        )
        _tag(repo, "v1.0.0")
        copy_plugin = _make_minimal_plugin(
            tmp_path / "demo-copy", name="demo-copy", upstream_repo=str(repo),
            class_paths=["copy_freely/data/"]
        )
        snap_plugin = _make_minimal_plugin(
            tmp_path / "demo-snap", name="demo-snap", upstream_repo=str(repo),
            class_paths=["copy_freely/data/"], include_snapshot=True,
            include_upstream_paths=True,
        )
        # the snapshot class subtree must EXIST for the manifest
        # reader's tree validation (name_dir_mismatch otherwise)
        (snap_plugin / "snapshot_with_drift_alarm" / "prompts").mkdir(parents=True)
        calls: Dict[str, int] = {"n": 0}

        real_run = subprocess.run

        def failing_run(*args, **kwargs):
            calls["n"] += 1
            raise subprocess.CalledProcessError(
                returncode=128,
                cmd=args[0] if args else ["git"],
                stderr=b"fatal: simulated git failure at the seam",
            )

        monkeypatch.setattr(subprocess, "run", failing_run)
        return {
            "copy_plugin": copy_plugin,
            "snap_plugin": snap_plugin,
            "repo": repo,
            "calls": calls,
        }

    def test_seam_failure_converts_to_typed_refusal(self, seam_env):
        # The FIRST subprocess call in a copy_freely sync is the
        # tag-presence probe inside LocalGitCheckout — the conversion
        # chain: CalledProcessError → RuntimeError (in _run) →
        # UpstreamContentIncompleteError (in tag_present's caller /
        # list_tree) → the sync's typed ``tag_missing_upstream``
        # REFUSAL RESULT (never a raw shell error on the result
        # surface).
        result = sync("demo", "copy_freely",
                      upstream_repo=str(seam_env["repo"]), upstream_tag="v1.0.0",
                      plugin_dir=seam_env["copy_plugin"], dry_run=True)
        assert result.action == "refused"
        assert result.refusal is not None
        assert result.refusal.code == "tag_missing_upstream"
        assert seam_env["calls"]["n"] >= 1

    def test_seam_failure_in_localgitcheckout_run_raises_runtimeerror(self, seam_env):
        checkout = LocalGitCheckout(str(seam_env["repo"]), "v1.0.0")
        with pytest.raises(RuntimeError, match="simulated git failure"):
            checkout._run("rev-parse", "v1.0.0^{tag}")

    def test_seam_failure_in_cat_file_blob_converts(self, seam_env):
        checkout = LocalGitCheckout(str(seam_env["repo"]), "v1.0.0")
        with pytest.raises(UpstreamContentIncompleteError, match="simulated git failure"):
            checkout.cat_file_blob("0" * 40)

    def test_seam_failure_in_batch_check_converts(self, seam_env):
        checkout = LocalGitCheckout(str(seam_env["repo"]), "v1.0.0")
        with pytest.raises(UpstreamContentIncompleteError, match="simulated git failure"):
            checkout._assert_blobs_present(["0" * 40])

    def test_snapshot_sync_surfaces_the_seam_as_refusal_result(self, seam_env):
        # End-to-end: the drift-class sync converts the seam failure
        # into the typed refusal (action=refused, code=
        # tag_missing_upstream) — the observable contract.
        result = sync("demo", "snapshot_with_drift_alarm",
                      upstream_repo=str(seam_env["repo"]), upstream_tag="v1.0.0",
                      plugin_dir=seam_env["snap_plugin"], dry_run=True)
        assert result.action == "refused"
        assert result.refusal is not None
        assert result.refusal.code == "tag_missing_upstream"
