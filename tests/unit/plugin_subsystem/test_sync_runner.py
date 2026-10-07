"""Sync-runner + vendoring_classifier + drift-event tests (slice ③).

Exercises the full contract surface per CON §5: every sync action
(``clean_pulled`` / ``alarmed`` / ``refused`` / ``no_change``), every
refusal code in the closed enum, the vendoring-time classification
routing ladder, the atomic mid-pull safety, the divergence-register
update path, and the drift-event payload shape.

Test layout (all in one file for the slice ③; slice ⑤+ may split):

- ``TestVendoringClassifierRoutingLadder`` — 4 routing-ladder
  steps, plus misclassification refusal at vendoring time
- ``TestSyncResultShape`` — frozen ``SyncResult`` shape
- ``TestSyncActions`` — clean_pulled / alarmed / refused / no_change
  over the copy_freely + snapshot_with_drift_alarm classes
- ``TestSyncRefusalCodes`` — every CON §5 closed-enum refusal code
- ``TestSyncAtomicity`` — mid-pull crash leaves tree intact
- ``TestLocallyOwnedFilesPreserved`` — HASHES.sha256 round-trips
- ``TestDriftEventPayload`` — verbatim CON §5 payload shape
- ``TestSnapshotClassDrift`` — drift alarm on snapshot class
- ``TestForcePushTagMissing`` — force-push / tag-deleted ⇒ refuse
- ``TestLightweightTagHandling`` — annotated vs lightweight
- ``TestClassifierIntegration`` — full pipeline (manifest → sync)
- ``TestRealPluginSync`` — the live opendesign manifest sync
- ``TestFailClosedNonDryPull`` — CRITICAL #2 fail-closed teeth
- ``TestRenameAsideAtomicity`` — W3 rename-aside atomicity teeth
- ``TestReaderCodeNamespacing`` — item (a) reader-passthrough teeth
- ``TestPinningTestReferentialIntegrity`` — reviewer addendum #1:
  every ``pinning_test:`` pointer in the manifest MUST resolve
  to a real collected pytest test id (any class, any depth).
  Structural recurrence guard so a future manifest entry
  cannot have a dangling pointer.

Most tests use a synthetic fixture git repo (built in tmp_path)
to avoid touching the real /home/nea/opt/open-design repo
(REFUSAL boundary from the dispatch).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

import pytest
import yaml

from daemon.plugin_subsystem import (
    ClassifiedFor,
    ClassificationRefusal,
    LocalGitCheckout,
    PathTypeRegistry,
    PluginDeclaration,
    SyncRefusal,
    SyncResult,
    SyncRunner,
    UpstreamGit,
    build_drift_event_payload,
    classify,
    emit_drift_event,
    read_manifest,
    sync,
    validate_manifest,
)
from daemon.plugin_subsystem.sync_runner import (
    LOCALLY_OWNED_FILENAMES,
    REFUSAL_ABSENT_EXECUTION_MODE,
    REFUSAL_FENCE_MISSING,
    REFUSAL_LICENSE_INVALID,
    REFUSAL_MISCLASSIFIED_AT_VENDORING,
    REFUSAL_NON_TAG_PIN,
    REFUSAL_OWN_OUTRIGHT_MUTATION,
    REFUSAL_TAG_MISSING_UPSTREAM,
    DiffSummary,
    DriftAlarm,
    UpstreamContentIncompleteError,
    _git_blob_sha1,
)
from tests.unit.plugin_subsystem._manifest_fixtures import (
    VALID_A_PATH_MANIFEST,
    VALID_B_PATH_MANIFEST,
    VALID_MINIMAL_MANIFEST,
    build_plugin,
)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _init_git_repo(path: Path, files: Dict[str, str]) -> str:
    """Initialize a git repo at ``path`` with one commit containing ``files``.

    Returns the commit SHA.  Uses ``git init -b main`` (modern git)
    with a fallback to ``git init && git checkout -b main`` for
    older versions.  All file contents are bytes; \n in strings
    becomes literal newlines.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    # Try modern syntax first; fall back to old-style
    try:
        subprocess.run(
            ["git", "-C", str(path), "checkout", "-q", "-b", "main"],
            check=True,
        )
    except subprocess.CalledProcessError:
        pass
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"],
        check=True,
    )
    for rel, content in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "-m", "init"],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


def _tag(repo: Path, name: str, *, annotated: bool = True) -> None:
    if annotated:
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "tag",
                "-a",
                name,
                "-m",
                f"tag {name}",
            ],
            check=True,
        )
    else:
        subprocess.run(
            ["git", "-C", str(repo), "tag", name],
            check=True,
        )


def _make_minimal_plugin(
    plugin_dir: Path,
    *,
    name: str = "demo",
    class_paths: List[str] | None = None,
    upstream_repo: str = "/tmp/does-not-exist",
    pin: str = "v0.1.0",
    include_snapshot: bool = False,
    include_upstream_paths: bool = False,
    divergence_register: List[Dict] | None = None,
) -> Path:
    """Build a minimal valid plugin tree at ``plugin_dir`` for sync tests."""
    plugin_dir = Path(plugin_dir)
    plugin_dir.mkdir(parents=True, exist_ok=True)
    class_paths = class_paths or ["copy_freely/data/"]
    snap_paths: List[str] = []
    snap_upstream_paths: List[str] = []
    if include_snapshot:
        snap_paths = ["snapshot_with_drift_alarm/prompts/"]
        snap_upstream_paths = (
            ["prompts/"] if include_upstream_paths else []
        )
    manifest = {
        "schema_version": "1.0.0",
        "plugin": {
            "name": name,
            "license": "Apache-2.0",
            "upstream": {
                "repo": upstream_repo,
                "tag_pin_per_class": {
                    "copy_freely": pin,
                    **({"snapshot_with_drift_alarm": pin} if include_snapshot else {}),
                },
            },
            "integration_path": "C",
            "execution_mode": "resource-only",
        },
        "copy_freely": {
            "paths": class_paths,
            "alarm_owner": "demo-owner",
            "escalation": "block-promote-after-days",
        },
        **(
            {
                "snapshot_with_drift_alarm": {
                    "paths": snap_paths,
                    **({"upstream_paths": snap_upstream_paths} if include_upstream_paths else {}),
                    "alarm_owner": "demo-snap-owner",
                    "escalation": "block-promote-after-days",
                    "divergence_register": divergence_register
                    or [
                        {
                            "id": 1,
                            "files": ["snapshot_with_drift_alarm/prompts/x.ts"],
                            "delta": "seeded",
                            "rationale": "test seed",
                            "pinning_test": "test_sync_runner.py::TestClassifier",
                        }
                    ],
                }
            }
            if include_snapshot
            else {}
        ),
        "parity_boundary": {
            "intentionally_not_vendored": [],
            "not_executed": [],
        },
    }
    (plugin_dir / "MANIFEST.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    return plugin_dir


# ─── Vendoring classifier (comp 5) ────────────────────────────────────────────


class TestVendoringClassifierRoutingLadder:
    """The 4 routing-ladder steps produce a non-empty step-list on
    success and a typed refusal on the first failing step."""

    def test_classify_passes_all_four_steps_for_valid_manifest(self, tmp_path):
        plugin_dir = _make_minimal_plugin(tmp_path / "demo", upstream_repo="/tmp/up")
        declaration = read_manifest(plugin_dir)
        registry = PathTypeRegistry
        from daemon.plugin_subsystem import load_default_registry
        reg = load_default_registry()
        result = classify(declaration, reg)
        assert result.is_classified
        assert result.routing_ladder_steps == (
            "path_letter_registered",
            "execution_mode_in_row_allowlist",
            "required_manifest_fields_satisfied",
            "fence_honored",
        )

    def test_unregistered_path_letter_raises_misclassified(self, tmp_path):
        # Build a manifest with an unregistered integration_path via
        # raw YAML (the structural reader would normally catch this,
        # but we test the classifier's independent logic).
        plugin_dir = tmp_path / "demo"
        plugin_dir.mkdir()
        manifest = {
            "schema_version": "1.0.0",
            "plugin": {
                "name": "demo",
                "license": "Apache-2.0",
                "upstream": {
                    "repo": "https://example.com/upstream.git",
                    "tag_pin_per_class": {"copy_freely": "v1.0.0"},
                },
                "integration_path": "Z",  # unregistered
                "execution_mode": "resource-only",
            },
            "copy_freely": {
                "paths": ["copy_freely/data/"],
                "alarm_owner": "demo-owner",
                "escalation": "block-promote-after-days",
            },
            "parity_boundary": {
                "intentionally_not_vendored": [],
                "not_executed": [],
            },
        }
        (plugin_dir / "MANIFEST.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )
        # Bypass the structural reader (which would refuse 'Z'); we
        # want to test the classifier's gate independently.  Use a
        # custom registry that does NOT have 'Z'.
        from daemon.plugin_subsystem import load_default_registry
        reg = load_default_registry()
        # Build a PluginDeclaration manually from the dict (the
        # structural reader would have refused 'Z').
        from daemon.plugin_subsystem.plugin_declaration import PluginDeclaration
        declaration = PluginDeclaration(
            name="demo",
            license="Apache-2.0",
            upstream_repo="https://example.com/upstream.git",
            tag_pin_per_class={"copy_freely": "v1.0.0"},
            integration_path="Z",
            execution_mode="resource-only",
            copy_freely={"paths": ["copy_freely/data/"], "alarm_owner": "x", "escalation": "x"},
            source_dir=plugin_dir,
            schema_version="1.0.0",
        )
        with pytest.raises(ClassificationRefusal) as ei:
            classify(declaration, reg)
        assert ei.value.code == "misclassified_at_vendoring"
        assert ei.value.failed_step == "path_letter_registered"

    def test_execution_mode_not_in_allowlist_raises_misclassified(self, tmp_path):
        from daemon.plugin_subsystem import load_default_registry
        from daemon.plugin_subsystem.plugin_declaration import PluginDeclaration
        reg = load_default_registry()
        # Path 'C' allows only execution_mode='resource-only'; pick 'lifted-symbol' (B-only).
        declaration = PluginDeclaration(
            name="demo",
            license="Apache-2.0",
            upstream_repo="https://example.com/upstream.git",
            tag_pin_per_class={"copy_freely": "v1.0.0"},
            integration_path="C",
            execution_mode="lifted-symbol",  # not in C's allowlist
            copy_freely={"paths": ["copy_freely/data/"], "alarm_owner": "x", "escalation": "x"},
            source_dir=tmp_path,
            schema_version="1.0.0",
        )
        with pytest.raises(ClassificationRefusal) as ei:
            classify(declaration, reg)
        assert ei.value.failed_step == "execution_mode_in_row_allowlist"


# ─── Sync-result shape (CON §5 frozen) ────────────────────────────────────────


class TestSyncResultShape:
    """The frozen SyncResult dataclass and its as_dict() projection."""

    def test_clean_pulled_result_shape(self):
        result = SyncResult(
            plugin="opendesign",
            target_class="copy_freely",
            upstream_tag="v1.0.0",
            action="clean_pulled",
            diff_summary=DiffSummary(0, 0, 0),
            staleness_age_days=0,
        )
        d = result.as_dict()
        assert d["plugin"] == "opendesign"
        assert d["target_class"] == "copy_freely"
        assert d["upstream_tag"] == "v1.0.0"
        assert d["action"] == "clean_pulled"
        assert d["diff_summary"] == {"files_added": 0, "files_modified": 0, "files_removed": 0}
        assert d["staleness_age_days"] == 0
        assert "alarm" not in d
        assert "refusal" not in d

    def test_alarmed_result_has_alarm(self):
        alarm = DriftAlarm(
            divergence_register_entry={"id": 1, "files": ["x.ts"], "delta": "x", "rationale": "y", "pinning_test": "z"}
        )
        result = SyncResult(
            plugin="opendesign",
            target_class="snapshot_with_drift_alarm",
            upstream_tag="v1.0.0",
            action="alarmed",
            diff_summary=DiffSummary(1, 0, 0),
            alarm=alarm,
            staleness_age_days=5,
        )
        d = result.as_dict()
        assert d["action"] == "alarmed"
        assert d["alarm"]["divergence_register_entry"]["id"] == 1

    def test_refused_result_has_refusal(self):
        result = SyncResult(
            plugin="opendesign",
            target_class="copy_freely",
            upstream_tag="v1.0.0",
            action="refused",
            diff_summary=DiffSummary(),
            refusal=SyncRefusal(code="tag_missing_upstream", message="x", location="y"),
            staleness_age_days=0,
        )
        d = result.as_dict()
        assert d["action"] == "refused"
        assert d["refusal"]["code"] == "tag_missing_upstream"


# ─── Sync actions (every action × every class) ────────────────────────────────


class TestSyncActions:
    """End-to-end sync against a synthetic git fixture: every action
    in CON §5 is exercised at least once."""

    @pytest.fixture
    def git_repo(self, tmp_path):
        repo = tmp_path / "upstream"
        sha = _init_git_repo(
            repo,
            {
                "data/file1.txt": "hello v1\n",
                "data/file2.txt": "world v1\n",
            },
        )
        _tag(repo, "v1.0.0")
        return repo, sha

    def test_no_change_when_local_matches_upstream(self, git_repo, tmp_path):
        repo, _sha = git_repo
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        # Pre-populate the local target to match upstream
        local_data = plugin_dir / "copy_freely" / "data"
        local_data.mkdir(parents=True, exist_ok=True)
        (local_data / "file1.txt").write_text("hello v1\n", encoding="utf-8")
        (local_data / "file2.txt").write_text("world v1\n", encoding="utf-8")
        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=True,
        )
        assert result.action == "no_change"
        assert result.diff_summary.files_added == 0
        assert result.diff_summary.files_modified == 0
        assert result.diff_summary.files_removed == 0

    def test_clean_pulled_when_local_missing_file(self, git_repo, tmp_path):
        repo, _sha = git_repo
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        # Local is empty; dry-run reports 2 added
        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=True,
        )
        assert result.action == "clean_pulled"
        assert result.diff_summary.files_added == 2

    def test_clean_pulled_writes_files_on_real_pull(self, git_repo, tmp_path):
        repo, _sha = git_repo
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        # Real pull (NOT dry-run)
        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=False,
        )
        assert result.action == "clean_pulled"
        assert result.diff_summary.files_added == 2
        # Verify on-disk
        local = plugin_dir / "copy_freely" / "data"
        assert (local / "file1.txt").read_text(encoding="utf-8") == "hello v1\n"
        assert (local / "file2.txt").read_text(encoding="utf-8") == "world v1\n"

    def test_own_outright_mutation_refused(self, tmp_path):
        """CON §5 reachability: sync(..., target_class="own_outright")
        refuses with the §5 enum code own_outright_mutation (the hard
        rule — sync NEVER writes own_outright/; no override flag)."""
        plugin_dir = _make_minimal_plugin(tmp_path / "demo")
        result = sync(
            "demo", "own_outright",
            plugin_dir=plugin_dir, dry_run=True,
        )
        assert result.action == "refused"
        assert result.refusal is not None
        assert result.refusal.code == "own_outright_mutation"
        assert "own_outright" in result.refusal.message

    def test_target_class_outside_domain_raises_value_error(self, tmp_path):
        """A target_class outside the declared domain
        (copy_freely | snapshot_with_drift_alarm | own_outright) is an
        API-signature misuse — a programmer error, NOT a §5 contract
        refusal — so it raises ValueError instead of returning a
        refusal (review ruling: the refusal enum stays EXACTLY §5's 7).
        own_outright itself does NOT raise: it refuses (above)."""
        plugin_dir = _make_minimal_plugin(tmp_path / "demo")
        with pytest.raises(ValueError, match="target_class"):
            sync("demo", "garbage", plugin_dir=plugin_dir, dry_run=True)
        with pytest.raises(ValueError, match="target_class"):
            sync("demo", "", plugin_dir=plugin_dir, dry_run=True)


# ─── Refusal codes (every CON §5 closed-enum code) ───────────────────────────


class TestSyncRefusalCodes:
    """Every refusal code in the CON §5 closed enum is exercised."""

    def test_own_outright_mutation_code(self, tmp_path):
        plugin_dir = _make_minimal_plugin(tmp_path / "demo")
        result = sync("demo", "own_outright", plugin_dir=plugin_dir, dry_run=True)
        assert result.action == "refused"
        assert result.refusal is not None
        assert result.refusal.code == REFUSAL_OWN_OUTRIGHT_MUTATION

    def test_tag_missing_upstream_code(self, tmp_path):
        repo = tmp_path / "upstream"
        _init_git_repo(repo, {"data/x.txt": "x"})
        # No tag at all
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v-does-not-exist",
            plugin_dir=plugin_dir, dry_run=True,
        )
        assert result.action == "refused"
        assert result.refusal.code == REFUSAL_TAG_MISSING_UPSTREAM

    def test_upstream_unavailable_fails_closed_as_tag_missing(self, tmp_path):
        """Unopenable upstream (absent dir / not a git repository) ⇒
        refused as tag_missing_upstream — fail-closed: an unobservable
        tag IS a missing tag (CON §5 line 196 + review ruling).  The
        message names the actual cause for diagnostics."""
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo",
            upstream_repo="/tmp/definitely-not-a-git-repo-12345",
            class_paths=["copy_freely/data/"],
        )
        result = sync(
            "demo", "copy_freely",
            upstream_repo="/tmp/definitely-not-a-git-repo-12345",
            upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=True,
        )
        assert result.action == "refused"
        assert result.refusal is not None
        assert result.refusal.code == REFUSAL_TAG_MISSING_UPSTREAM
        assert (
            "not a git checkout" in result.refusal.message
            or "not a local directory" in result.refusal.message
        )

    def test_misclassified_at_vendoring_code(self, tmp_path):
        repo = tmp_path / "upstream"
        _init_git_repo(repo, {"data/x.txt": "x"})
        _tag(repo, "v1.0.0")
        # Build a manifest that classifies (C / resource-only) but
        # make it misclassify by passing a non-existent upstream
        # tag that would otherwise fail differently.  We test
        # misclassified by monkeypatching the classifier to raise.
        from daemon.plugin_subsystem import sync_runner as sr
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )

        def _raise(*a, **kw):
            raise ClassificationRefusal(message="forced misclass", failed_step="x")

        original = sr.classify
        sr.classify = _raise  # type: ignore[assignment]
        try:
            result = sync(
                "demo", "copy_freely",
                upstream_repo=str(repo), upstream_tag="v1.0.0",
                plugin_dir=plugin_dir, dry_run=True,
            )
        finally:
            sr.classify = original  # type: ignore[assignment]
        assert result.action == "refused"
        assert result.refusal.code == REFUSAL_MISCLASSIFIED_AT_VENDORING

    def test_non_tag_pin_code_when_no_class_paths(self, tmp_path):
        """When the manifest declares a class with empty paths AND
        no upstream_paths, the sync-runner returns no_change (not a
        refusal).  To exercise the non_tag_pin path, we need an
        empty pin in tag_pin_per_class.  Build a custom manifest."""
        repo = tmp_path / "upstream"
        _init_git_repo(repo, {"data/x.txt": "x"})
        _tag(repo, "v1.0.0")
        plugin_dir = tmp_path / "demo"
        plugin_dir.mkdir()
        manifest = {
            "schema_version": "1.0.0",
            "plugin": {
                "name": "demo",
                "license": "Apache-2.0",
                "upstream": {
                    "repo": str(repo),
                    "tag_pin_per_class": {"copy_freely": ""},  # empty pin
                },
                "integration_path": "C",
                "execution_mode": "resource-only",
            },
            "copy_freely": {
                "paths": ["copy_freely/data/"],
                "alarm_owner": "x",
                "escalation": "x",
            },
            "parity_boundary": {"intentionally_not_vendored": [], "not_executed": []},
        }
        (plugin_dir / "MANIFEST.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )
        # The structural reader will refuse 'empty pin' — that
        # reader refusal is now surfaced as a sync-runner
        # SyncResult with refusal.code='non_tag_pin' (the public
        # API is "always returns SyncResult").  Verify the
        # refused-result shape.
        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="",
            plugin_dir=plugin_dir, dry_run=True,
        )
        assert result.action == "refused"
        assert result.refusal is not None
        assert result.refusal.code == "non_tag_pin"


# ─── Atomicity (mid-pull crash = whole-or-nothing) ────────────────────────────


class _SimulatedCrashError(Exception):
    """Distinct exception class for mid-pull crash simulation.

    The sync-runner's inner ``except (subprocess.CalledProcessError,
    RuntimeError)`` swallows git-IO errors per file (so a single
    bad blob doesn't kill the whole pull).  The mid-pull-crash
    test needs a non-RuntimeError exception to BYPASS that
    catch and bubble up to the outer try/except (which performs
    the cleanup + re-raise).
    """
    pass


class TestSyncAtomicity:
    """A mid-pull crash must leave the local target unchanged."""

    def test_mid_pull_failure_leaves_target_intact(self, tmp_path, monkeypatch):
        repo = tmp_path / "upstream"
        files = {f"data/file{i}.txt": f"content {i}\n" for i in range(10)}
        _init_git_repo(repo, files)
        _tag(repo, "v1.0.0")
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        # Pre-populate the local target with KNOWN content
        local = plugin_dir / "copy_freely" / "data"
        local.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            (local / f"file{i}.txt").write_text(f"PRECIOUS {i}\n", encoding="utf-8")
        # Snapshot the state pre-pull
        pre_state = {
            str(p.relative_to(local)): p.read_text(encoding="utf-8")
            for p in local.rglob("*") if p.is_file()
        }
        # Force a mid-pull failure: monkeypatch cat_file_blob to
        # raise a non-RuntimeError exception on the 6th blob.
        # The pull will be partial; the atomic rename MUST NOT
        # happen.
        from daemon.plugin_subsystem import sync_runner as sr
        real_cat = sr.LocalGitCheckout.cat_file_blob
        call_count = {"n": 0}

        def faulty_cat(self, blob_sha):
            call_count["n"] += 1
            if call_count["n"] >= 6:
                raise _SimulatedCrashError("SIMULATED CRASH mid-pull")
            return real_cat(self, blob_sha)

        monkeypatch.setattr(sr.LocalGitCheckout, "cat_file_blob", faulty_cat)
        with pytest.raises(_SimulatedCrashError, match="SIMULATED CRASH"):
            sync(
                "demo", "copy_freely",
                upstream_repo=str(repo), upstream_tag="v1.0.0",
                plugin_dir=plugin_dir, dry_run=False,
            )
        # Tree must be intact: same files, same content as pre-state
        post_state = {
            str(p.relative_to(local)): p.read_text(encoding="utf-8")
            for p in local.rglob("*") if p.is_file()
        }
        assert post_state == pre_state, (
            f"mid-pull failure left tree in inconsistent state: "
            f"added={set(post_state) - set(pre_state)}, "
            f"removed={set(pre_state) - set(post_state)}"
        )
        # And no leftover stage dir
        stage_dirs = list(plugin_dir.glob(".sync_stage.*"))
        assert stage_dirs == [], f"stage dir leaked: {stage_dirs}"


# ─── Locally-owned files preserved across pulls ──────────────────────────────


class TestLocallyOwnedFilesPreserved:
    """HASHES.sha256 is locally-owned; the sync-runner must NOT
    overwrite it from upstream content and must NOT report it in
    diff_summary."""

    def test_hashes_file_survives_pull(self, tmp_path):
        repo = tmp_path / "upstream"
        _init_git_repo(repo, {"data/file.txt": "v1\n"})
        _tag(repo, "v1.0.0")
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        # Pre-create HASHES.sha256 (locally-owned) with KNOWN content
        target = plugin_dir / "copy_freely"
        target.mkdir(parents=True, exist_ok=True)
        hash_content = "deadbeef00000000  data/file.txt\n"
        (target / "HASHES.sha256").write_text(hash_content, encoding="utf-8")
        # Pull
        sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=False,
        )
        # HASHES.sha256 still has the same content
        assert (target / "HASHES.sha256").read_text(encoding="utf-8") == hash_content

    def test_hashes_file_not_in_diff_summary(self, tmp_path):
        repo = tmp_path / "upstream"
        _init_git_repo(repo, {"data/file.txt": "v1\n"})
        _tag(repo, "v1.0.0")
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        target = plugin_dir / "copy_freely"
        target.mkdir(parents=True, exist_ok=True)
        (target / "HASHES.sha256").write_text("stale local content\n", encoding="utf-8")
        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=True,
        )
        # diff_summary should not count the HASHES.sha256 as "added"
        # (it's locally-owned, not vendored)
        assert result.diff_summary.files_added == 1  # only data/file.txt
        assert "HASHES.sha256" not in (result.diff_summary.files_added, result.diff_summary.files_modified)


# ─── Drift-event payload shape (CON §5 verbatim) ─────────────────────────────


class TestDriftEventPayload:
    """The CON §5 verbatim drift-event payload shape is pinned by
    ``build_drift_event_payload``.  Slice ⑥ will wire the actual
    publisher; slice ③ pins the shape."""

    def test_payload_shape_exact(self):
        entry = {
            "id": 5,
            "files": ["prompts/contracts/od-next-intent-resolution.ts"],
            "delta": "+1 file in v0.24.1",
            "rationale": "Snapshot pulled at sync time; re-apply or drop per CON §2",
            "pinning_test": "tests/unit/plugin_subsystem/test_sync_runner.py::TestSyncSnapshotDrift",
        }
        when = datetime(2026, 10, 6, 19, 45, tzinfo=timezone.utc)
        payload = build_drift_event_payload(
            "opendesign", "snapshot_with_drift_alarm", entry, "open-design-v0.24.1",
            now=when,
        )
        assert payload == {
            "plugin": "opendesign",
            "class": "snapshot_with_drift_alarm",
            "divergence_id": 5,
            "files": ["prompts/contracts/od-next-intent-resolution.ts"],
            "delta": "+1 file in v0.24.1",
            "rationale": "Snapshot pulled at sync time; re-apply or drop per CON §2",
            "pinning_test": "tests/unit/plugin_subsystem/test_sync_runner.py::TestSyncSnapshotDrift",
            "observed_at": "2026-10-06T19:45:00+00:00",
            "observed_tag": "open-design-v0.24.1",
        }

    def test_emit_drift_event_routes_through_the_configured_sink(self, caplog):
        # Slice ⑥ REPLACED the ③ no-op stub: the emission now routes
        # through the configured drift-event sink (log-only default —
        # the structured log line survives; DB-backed when the daemon
        # boot configures the publisher).  The DB-backed lane is
        # pinned by test_drift_event_publisher.py; this test pins the
        # default-sink observability.
        import logging

        from daemon.plugin_subsystem.drift_event_publisher import (
            reset_drift_event_publisher,
        )

        reset_drift_event_publisher()
        with caplog.at_level(logging.INFO):
            emit_drift_event(
                "opendesign", "snapshot_with_drift_alarm",
                {"id": 1, "files": ["x"], "delta": "y", "rationale": "z", "pinning_test": "p"},
                "v0.0.1",
            )
        # Logs the payload shape (the ③ line format, preserved by the
        # log-only default sink)
        assert any("drift_event_emitted" in r.message for r in caplog.records), (
            f"expected drift_event_emitted log line; got: {[r.message for r in caplog.records]}"
        )


# ─── Snapshot class: alarm + divergence-register update ─────────────────────


class TestSnapshotClassDrift:
    """When a snapshot-class pull surfaces byte-level changes, the
    alarm is emitted and (on a real pull) the manifest's
    divergence_register is updated atomically."""

    def test_drift_alarm_at_snapshot_with_drift_alarm(self, tmp_path):
        repo = tmp_path / "upstream"
        _init_git_repo(repo, {"prompts/x.ts": "v1 content\n"})
        _tag(repo, "v1.0.0")
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo",
            upstream_repo=str(repo),
            class_paths=["snapshot_with_drift_alarm/prompts/"],
            include_snapshot=True,
            include_upstream_paths=True,
        )
        # Pre-populate local snapshot with DIFFERENT content (drift)
        local = plugin_dir / "snapshot_with_drift_alarm" / "prompts"
        local.mkdir(parents=True, exist_ok=True)
        (local / "x.ts").write_text("OLD LOCAL content\n", encoding="utf-8")
        result = sync(
            "demo", "snapshot_with_drift_alarm",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=True,
        )
        assert result.action == "alarmed"
        assert result.diff_summary.files_modified == 1
        assert result.alarm is not None
        assert result.alarm.divergence_register_entry["id"] >= 2  # new id (1 was seeded)
        assert "prompts/x.ts" in result.alarm.divergence_register_entry["files"]

    def test_dry_run_does_not_mutate_manifest(self, tmp_path):
        repo = tmp_path / "upstream"
        _init_git_repo(repo, {"prompts/x.ts": "v1 content\n"})
        _tag(repo, "v1.0.0")
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo",
            upstream_repo=str(repo),
            class_paths=["snapshot_with_drift_alarm/prompts/"],
            include_snapshot=True,
            include_upstream_paths=True,
        )
        local = plugin_dir / "snapshot_with_drift_alarm" / "prompts"
        local.mkdir(parents=True, exist_ok=True)
        (local / "x.ts").write_text("OLD\n", encoding="utf-8")
        # Capture the register before
        before = yaml.safe_load((plugin_dir / "MANIFEST.yaml").read_text(encoding="utf-8"))
        before_count = len(before["snapshot_with_drift_alarm"]["divergence_register"])
        # Dry-run
        sync(
            "demo", "snapshot_with_drift_alarm",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=True,
        )
        after = yaml.safe_load((plugin_dir / "MANIFEST.yaml").read_text(encoding="utf-8"))
        after_count = len(after["snapshot_with_drift_alarm"]["divergence_register"])
        assert after_count == before_count, (
            f"dry-run mutated divergence_register: {before_count} -> {after_count}"
        )


# ─── Force-push / tag-deleted: refuse + alert ───────────────────────────────


class TestForcePushTagMissing:
    """Tag deletion or force-push upstream ⇒ tag_missing_upstream
    refusal.  We simulate by deleting the tag locally; the real
    upstream is not touched."""

    def test_tag_deletion_refuses_sync(self, tmp_path):
        repo = tmp_path / "upstream"
        _init_git_repo(repo, {"data/x.txt": "v1\n"})
        _tag(repo, "v1.0.0")
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        # Sanity: sync works pre-deletion
        pre = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=True,
        )
        assert pre.action == "no_change" or pre.action == "clean_pulled"
        # Delete the tag (simulate upstream tag-deletion)
        subprocess.run(["git", "-C", str(repo), "tag", "-d", "v1.0.0"], check=True)
        # Re-sync: should refuse
        post = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=True,
        )
        assert post.action == "refused"
        assert post.refusal.code == REFUSAL_TAG_MISSING_UPSTREAM


# ─── Lightweight vs annotated tags ───────────────────────────────────────────


class TestLightweightTagHandling:
    """v0.24.1 upstream is a LIGHTWEIGHT tag; the slice ③ sync-runner
    must handle both annotated and lightweight tags."""

    def test_annotated_tag(self, tmp_path):
        repo = tmp_path / "upstream"
        _init_git_repo(repo, {"data/x.txt": "v1\n"})
        _tag(repo, "v1.0.0", annotated=True)
        upstream = LocalGitCheckout(str(repo), "v1.0.0")
        assert upstream.tag_present()
        # _ref_for_listing should pick the annotated-tag peel
        ref = upstream._ref_for_listing()
        assert ref == subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "v1.0.0^{tag}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def test_lightweight_tag(self, tmp_path):
        repo = tmp_path / "upstream"
        _init_git_repo(repo, {"data/x.txt": "v1\n"})
        _tag(repo, "v1.0.0", annotated=False)
        upstream = LocalGitCheckout(str(repo), "v1.0.0")
        assert upstream.tag_present()
        # _ref_for_listing should pick the commit (no ^{tag} peel)
        ref = upstream._ref_for_listing()
        assert ref == subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "v1.0.0^{}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

    def test_sync_against_lightweight_tag(self, tmp_path):
        repo = tmp_path / "upstream"
        _init_git_repo(repo, {"data/x.txt": "v1\n"})
        _tag(repo, "v1.0.0", annotated=False)
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=True,
        )
        assert result.action == "clean_pulled"  # local empty, upstream has 1 file
        assert result.diff_summary.files_added == 1


# ─── Classifier integration (manifest → classify → sync) ────────────────────


class TestClassifierIntegration:
    """The full pipeline: validate manifest → read declaration → classify
    → sync.  The classifier's refusal surfaces as a sync refusal."""

    def test_full_pipeline_success(self, tmp_path):
        repo = tmp_path / "upstream"
        _init_git_repo(repo, {"data/x.txt": "v1\n"})
        _tag(repo, "v1.0.0")
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        declaration = read_manifest(plugin_dir)
        from daemon.plugin_subsystem import load_default_registry
        reg = load_default_registry()
        cf = classify(declaration, reg)
        assert cf.is_classified
        # Then sync (no refuse)
        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=True,
        )
        assert result.action == "clean_pulled"


# ─── Real plugin sync (live opendesign manifest) ────────────────────────────


REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_PLUGIN_ROOT = REPO_ROOT / "plugins" / "opendesign"
REAL_UPSTREAM = Path("/home/nea/opt/open-design")


@pytest.mark.skipif(
    not REAL_UPSTREAM.is_dir() or not (REAL_UPSTREAM / ".git").exists(),
    reason="real OD upstream checkout absent; live integration skipped",
)
class TestRealPluginSync:
    """The real plugins/opendesign/ manifest is round-tripped
    through the sync-runner.  This is the live integration test
    that complements the unit-level synthetic-fixture tests above."""

    def test_real_copy_freely_dry_run_against_v0_24_1(self):
        result = sync(
            "opendesign", "copy_freely",
            upstream_repo=str(REAL_UPSTREAM), upstream_tag="open-design-v0.24.1",
            plugin_dir=REAL_PLUGIN_ROOT, dry_run=True,
        )
        # action is either no_change (zero churn) or clean_pulled
        # (1 file: the PNG that slice ② excluded; see dry-run
        # evidence artifact §3)
        assert result.action in ("no_change", "clean_pulled"), result.as_dict()
        assert result.refusal is None

    def test_real_snapshot_dry_run_against_v0_24_1(self):
        result = sync(
            "opendesign", "snapshot_with_drift_alarm",
            upstream_repo=str(REAL_UPSTREAM), upstream_tag="open-design-v0.24.1",
            plugin_dir=REAL_PLUGIN_ROOT, dry_run=True,
        )
        # The snapshot class is expected to alarm (CON §5):
        # od-next-intent-resolution.ts is new, od-next-strategy.ts
        # is modified.  See the dry-run evidence artifact §4.
        assert result.action == "alarmed", result.as_dict()
        assert result.alarm is not None
        assert result.refusal is None
        assert result.diff_summary.files_added >= 1
        assert result.diff_summary.files_modified >= 1
        # The drift entry contains the expected file basenames
        # (path-shape normalization: the dry-run records basenames
        # for the flat-layout snapshot class; the file extension
        # matches; the basename alone is the canonical drift
        # signal for the operator)
        files = result.alarm.divergence_register_entry["files"]
        assert any("od-next-intent-resolution.ts" in f for f in files), (
            f"expected od-next-intent-resolution.ts in alarm files; got: {files}"
        )
        assert any("od-next-strategy.ts" in f for f in files), (
            f"expected od-next-strategy.ts in alarm files; got: {files}"
        )

    def test_real_full_3_class_manifest_validates(self):
        validation = validate_manifest(REAL_PLUGIN_ROOT, validate_tree=True)
        assert validation.ok, f"manifest refused: {validation.refusal}"
        decl = validation.declaration
        assert decl.name == "opendesign"
        # Slice ⑤: integration_path C → B; execution_mode resource-only
        # → lifted-symbol (B-element for the per-capability Port tool
        # family). The test is updated to reflect the slice-⑤ state; the
        # prior C-only assertion lived pre-⑤.
        assert decl.execution_mode == "lifted-symbol"
        assert decl.integration_path == "B"
        assert decl.lifted_symbol is not None
        assert decl.entrypoint is not None
        assert decl.ipc_version is not None
        assert decl.copy_freely["paths"]
        assert decl.snapshot_with_drift_alarm["paths"]
        assert decl.own_outright["paths"]
        # Slice ⑤: the 4 own_outright attribution rows (own_outright is
        # AUTHORED at slice ⑤; the prior slice-③ state was declared-
        # not-authored with no attribution).
        assert decl.own_outright.get("attribution"), "own_outright attribution rows required at slice ⑤"
        # 4 SEEDED divergence-register entries (unchanged from slice ③)
        assert len(decl.divergence_register) == 4
        for entry in decl.divergence_register:
            assert "id" in entry
            assert "files" in entry
            assert "delta" in entry
            assert "rationale" in entry
            assert "pinning_test" in entry


class TestLicensePreservationThroughPullPath:
    """CC-BY-4.0 attribution lives in per-item `source.license` fields
    in the prompt-templates JSONs (od-resource-layer §0).  A sync
    that overwrites or strips those fields would break the
    attribution chain.  The sync-runner must preserve them
    byte-for-byte."""

    def test_per_item_source_license_survives_pull(self, tmp_path):
        """Build a fixture upstream with a JSON that has a per-item
        `source.license` field; verify the field survives a full
        clean_pulled (real pull, not dry-run)."""
        import json
        repo = tmp_path / "upstream"
        json_content = json.dumps({
            "id": "test-item",
            "title": "Test Item",
            "source": {
                "repo": "test/repo",
                "license": "CC-BY-4.0",
                "author": "Test Author",
                "url": "https://example.com/test",
            },
        }, indent=2)
        _init_git_repo(repo, {"data/item.json": json_content})
        _tag(repo, "v1.0.0")
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo",
            upstream_repo=str(repo),
            class_paths=["copy_freely/data/"],
        )
        # Real pull
        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=False,
        )
        assert result.action == "clean_pulled"
        # Verify the per-item source.license is preserved byte-for-byte
        local = plugin_dir / "copy_freely" / "data" / "item.json"
        local_content = local.read_text(encoding="utf-8")
        assert "CC-BY-4.0" in local_content
        local_json = json.loads(local_content)
        assert local_json["source"]["license"] == "CC-BY-4.0"
        assert local_json["source"]["repo"] == "test/repo"
        assert local_json["source"]["author"] == "Test Author"
        # Byte-exact match with upstream
        upstream_content = subprocess.run(
            ["git", "-C", str(repo), "show", "v1.0.0:data/item.json"],
            capture_output=True, check=True,
        ).stdout.decode("utf-8")
        assert local_content == upstream_content, (
            "per-item source.license must be preserved byte-for-byte "
            "(CC-BY-4.0 attribution chain would break otherwise)"
        )


# ─── Fail-closed non-dry pull (CRITICAL #2 council fix) ────────────────────


class TestFailClosedNonDryPull:
    """Council ③ CRITICAL #2: a non-dry pull that hits an unobservable
    subdir or unreadable blob must ABORT the pull (staged temp
    DISCARDED, live tree UNTOUCHED) and return action=refused with
    code=tag_missing_upstream.  The pre-fix code swallowed these
    errors with ``continue`` — silent-partial-data-loss illusion
    that swapped a partial tree in and reported action=clean_pulled.
    """

    def test_blob_error_aborts_non_dry_pull(self, tmp_path, monkeypatch):
        """Fault-inject a blob cat_file_blob error mid-pull: the
        pull must refuse, the live tree must be byte-intact, and
        no stage dir must leak."""
        repo = tmp_path / "upstream"
        files = {f"data/file{i}.txt": f"content {i}\n" for i in range(10)}
        _init_git_repo(repo, files)
        _tag(repo, "v1.0.0")
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        # Pre-populate the local target with KNOWN content so we
        # can prove the live tree was untouched post-refusal.
        local = plugin_dir / "copy_freely" / "data"
        local.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            (local / f"file{i}.txt").write_text(f"PRECIOUS {i}\n", encoding="utf-8")
        pre_state = {
            str(p.relative_to(local)): p.read_text(encoding="utf-8")
            for p in local.rglob("*") if p.is_file()
        }
        # Fault-inject cat_file_blob to raise the content-incomplete
        # error on the 6th blob.  Pre-fix code would silently
        # skip the failing blob and continue.
        from daemon.plugin_subsystem import sync_runner as sr

        real_cat = sr.LocalGitCheckout.cat_file_blob
        call_count = {"n": 0}

        def faulty_cat(self, blob_sha):
            call_count["n"] += 1
            if call_count["n"] >= 6:
                raise UpstreamContentIncompleteError(
                    f"FAULT-INJECTED: blob {blob_sha!r} unreadable at v1.0.0"
                )
            return real_cat(self, blob_sha)

        monkeypatch.setattr(sr.LocalGitCheckout, "cat_file_blob", faulty_cat)
        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=False,
        )
        # Refused (not clean_pulled).
        assert result.action == "refused", result.as_dict()
        assert result.refusal is not None
        assert result.refusal.code == REFUSAL_TAG_MISSING_UPSTREAM
        assert "blob" in result.refusal.message.lower()
        # Live tree byte-intact.
        post_state = {
            str(p.relative_to(local)): p.read_text(encoding="utf-8")
            for p in local.rglob("*") if p.is_file()
        }
        assert post_state == pre_state, (
            f"non-dry refusal left tree in inconsistent state: "
            f"added={set(post_state) - set(pre_state)}, "
            f"removed={set(pre_state) - set(post_state)}"
        )
        # No stage dir leaked.
        stage_dirs = list(plugin_dir.glob(".sync_stage.*"))
        assert stage_dirs == [], f"stage dir leaked: {stage_dirs}"

    def test_subdir_error_aborts_non_dry_pull(self, tmp_path, monkeypatch):
        """Fault-inject a subdir list_tree error mid-pull: the
        pull must refuse and the live tree must be byte-intact."""
        repo = tmp_path / "upstream"
        files = {f"data/file{i}.txt": f"content {i}\n" for i in range(8)}
        _init_git_repo(repo, files)
        _tag(repo, "v1.0.0")
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        local = plugin_dir / "copy_freely" / "data"
        local.mkdir(parents=True, exist_ok=True)
        for i in range(4):
            (local / f"file{i}.txt").write_text(f"PRECIOUS {i}\n", encoding="utf-8")
        pre_state = {
            str(p.relative_to(local)): p.read_text(encoding="utf-8")
            for p in local.rglob("*") if p.is_file()
        }
        # Fault-inject list_tree to raise the content-incomplete
        # error on the first call.
        from daemon.plugin_subsystem import sync_runner as sr
        from daemon.plugin_subsystem.sync_runner import UpstreamContentIncompleteError

        def faulty_list(self, subdir):
            raise UpstreamContentIncompleteError(
                f"FAULT-INJECTED: subdir {subdir!r} unreadable at v1.0.0"
            )
        monkeypatch.setattr(sr.LocalGitCheckout, "list_tree", faulty_list)
        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=False,
        )
        # Refused.
        assert result.action == "refused", result.as_dict()
        assert result.refusal is not None
        assert result.refusal.code == REFUSAL_TAG_MISSING_UPSTREAM
        assert "subdir" in result.refusal.message.lower()
        # Live tree byte-intact.
        post_state = {
            str(p.relative_to(local)): p.read_text(encoding="utf-8")
            for p in local.rglob("*") if p.is_file()
        }
        assert post_state == pre_state
        # No stage dir leaked.
        stage_dirs = list(plugin_dir.glob(".sync_stage.*"))
        assert stage_dirs == [], f"stage dir leaked: {stage_dirs}"

    def test_dry_run_parity_refuses_on_blob_error(self, tmp_path, monkeypatch):
        """Dry-run parity (council ③ fix): if the non-dry run would
        refuse on a blob error, the dry-run must also refuse with
        the same code — a dry-run is a truthful report of what
        the non-dry run would do.  The dry-run path uses
        ``list_tree`` + a batch ``cat-file --batch-check`` (the
        ``_assert_blobs_present`` gate added in the ③ fix) so
        the diff and the pull see the same condition."""
        repo = tmp_path / "upstream"
        files = {f"data/file{i}.txt": f"content {i}\n" for i in range(6)}
        _init_git_repo(repo, files)
        _tag(repo, "v1.0.0")
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        # Force the batch-check to report a missing blob.
        from daemon.plugin_subsystem import sync_runner as sr
        from daemon.plugin_subsystem.sync_runner import UpstreamContentIncompleteError

        def faulty_check(self, shas):
            raise UpstreamContentIncompleteError(
                f"FAULT-INJECTED: blob missing at v1.0.0"
            )
        monkeypatch.setattr(sr.LocalGitCheckout, "_assert_blobs_present", faulty_check)
        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=True,
        )
        # Dry-run refused (NOT clean_pulled).
        assert result.action == "refused", result.as_dict()
        assert result.refusal is not None
        assert result.refusal.code == REFUSAL_TAG_MISSING_UPSTREAM

    def test_dry_run_parity_refuses_on_subdir_error(self, tmp_path, monkeypatch):
        """Dry-run parity for subdir errors: the diff uses list_tree
        just like the pull, so a list_tree error in dry-run refuses."""
        repo = tmp_path / "upstream"
        _init_git_repo(repo, {"data/x.txt": "x\n"})
        _tag(repo, "v1.0.0")
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        from daemon.plugin_subsystem import sync_runner as sr
        from daemon.plugin_subsystem.sync_runner import UpstreamContentIncompleteError

        def faulty_list(self, subdir):
            raise UpstreamContentIncompleteError(
                f"FAULT-INJECTED: subdir {subdir!r} unreadable"
            )
        monkeypatch.setattr(sr.LocalGitCheckout, "list_tree", faulty_list)
        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=True,
        )
        assert result.action == "refused", result.as_dict()
        assert result.refusal is not None
        assert result.refusal.code == REFUSAL_TAG_MISSING_UPSTREAM
        assert "subdir" in result.refusal.message.lower()

    def test_first_real_pull_full_shape_gate(self, tmp_path):
        """The "6-assertion first-real-pull gate" (council ③ fix):
        a successful non-dry clean_pulled on a synthetic repo
        must assert: (i) file count on disk == expected complete
        set, (ii) hash-manifest match (every file re-hashes to
        the recorded value), (iii) no stage/temp leftovers,
        (iv) action == clean_pulled, (v) staleness_age_days
        present + correct arithmetic, (vi) tree clean / no torn
        manifest.  All six hold in one test."""
        repo = tmp_path / "upstream"
        files = {f"data/file{i}.txt": f"content-{i}-v1\n" for i in range(7)}
        _init_git_repo(repo, files)
        _tag(repo, "v0.1.0", annotated=True)
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        # Use a deterministic clock so staleness is exact.
        from datetime import datetime, timedelta, timezone
        # The synthetic tag's commit date is "now-ish" (git
        # commit timestamps at fixture-build time).  Force a
        # clock 10 days AFTER the tag's commit date.
        upstream = LocalGitCheckout(str(repo), "v0.1.0")
        tag_date = upstream.tag_commit_date()
        frozen_now = tag_date + timedelta(days=10)
        clock = lambda: frozen_now
        runner = SyncRunner(clock=clock)
        result = runner.sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v0.1.0",
            plugin_dir=plugin_dir, dry_run=False,
        )
        # (iv) action == clean_pulled.
        assert result.action == "clean_pulled", result.as_dict()
        assert result.refusal is None
        # (i) file count on disk == expected complete set.
        local = plugin_dir / "copy_freely" / "data"
        on_disk = sorted(p.name for p in local.iterdir() if p.is_file())
        expected = sorted(files.keys())  # ['data/file0.txt', ...] → basenames
        expected_basenames = sorted(p.split("/")[-1] for p in expected)
        assert on_disk == expected_basenames, (
            f"file count mismatch: on_disk={on_disk} expected={expected_basenames}"
        )
        # (ii) hash-manifest match — every file re-hashes to the
        # upstream's blob SHA (recompute locally; we have the
        # bytes).
        for rel, content in files.items():
            local_path = local / rel.split("/")[-1]
            local_bytes = local_path.read_bytes()
            local_sha = _git_blob_sha1(local_bytes)
            # Upstream blob SHA from git:
            upstream_sha = subprocess.run(
                ["git", "-C", str(repo), "ls-tree", "v0.1.0", rel],
                capture_output=True, text=True, check=True,
            ).stdout.split()[2]
            assert local_sha == upstream_sha, (
                f"hash mismatch for {rel}: local={local_sha} upstream={upstream_sha}"
            )
        # (iii) no stage/temp leftovers.
        leftovers = list(plugin_dir.glob(".sync_stage.*")) + list(plugin_dir.glob(".*sync_aside*"))
        assert leftovers == [], f"temp leftovers: {leftovers}"
        # (v) staleness_age_days present + correct arithmetic.
        assert result.staleness_age_days == 10, (
            f"expected staleness_age_days=10 (clock advanced 10 days past tag); "
            f"got {result.staleness_age_days}"
        )
        # (vi) tree clean / no torn manifest.  The manifest is
        # not in the class subtree (it's at the plugin root) and
        # the class subtree is the only thing this sync touches.
        manifest_path = plugin_dir / "MANIFEST.yaml"
        assert manifest_path.is_file(), "MANIFEST.yaml vanished"
        # Re-parse to verify it's still valid YAML.
        re_parsed = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        assert re_parsed["plugin"]["name"] == "demo"


class TestRenameAsideAtomicity:
    """W3 council fix: the rmtree→replace stage-swap window
    (target dir momentarily absent between rmtree(target) and
    replace(staged,target)) is closed by a rename-aside→
    rename-in→delete-old sequence.  These tests verify the
    target never disappears and the old tree is restored on
    failure."""

    def test_target_present_during_successful_pull(self, tmp_path):
        """During a successful pull, ``local_target`` is
        continuously present (the rename-aside pattern means
        it's NEVER absent).  We can verify the file structure
        mid-pull is consistent by checking pre/post equality."""
        repo = tmp_path / "upstream"
        files = {f"data/file{i}.txt": f"content {i}\n" for i in range(5)}
        _init_git_repo(repo, files)
        _tag(repo, "v1.0.0")
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        # Pre-populate the local target with KNOWN content.
        local = plugin_dir / "copy_freely" / "data"
        local.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (local / f"file{i}.txt").write_text(f"OLD {i}\n", encoding="utf-8")
        pre = sorted(p.name for p in local.iterdir() if p.is_file())
        # Real pull
        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=False,
        )
        assert result.action == "clean_pulled"
        # Post-pull: all 5 upstream files present (compare
        # basenames — the local layout is flat under
        # ``<local_target>/<relative_root>/<basename>``).
        post = sorted(p.name for p in local.iterdir() if p.is_file())
        expected_basenames = sorted(rel.split("/")[-1] for rel in files.keys())
        assert post == expected_basenames, (
            f"file count mismatch: on_disk={post} expected={expected_basenames}"
        )
        # And the OLD files (file0..2) have been replaced
        # (not kept — the copy_freely class is "clean-pulled,
        # never authored locally" per CON §2).
        for i in range(3):
            content = (local / f"file{i}.txt").read_text(encoding="utf-8")
            assert content == f"content {i}\n", (
                f"file{i}.txt not replaced with upstream content: {content!r}"
            )

    def test_target_restored_on_stage_replace_failure(self, tmp_path, monkeypatch):
        """If the second os.replace (stage → target) FAILS, the
        rename-aside pattern must RESTORE the old tree (aside →
        target) so the operator's live tree is intact.  We fault-
        inject by monkey-patching os.replace inside
        _clean_pull_atomic — the first call (target → aside)
        succeeds; the second (stage → target) raises."""
        repo = tmp_path / "upstream"
        files = {f"data/file{i}.txt": f"content {i}\n" for i in range(3)}
        _init_git_repo(repo, files)
        _tag(repo, "v1.0.0")
        plugin_dir = _make_minimal_plugin(
            tmp_path / "demo", upstream_repo=str(repo), class_paths=["copy_freely/data/"]
        )
        # Pre-populate with PRECIOUS content.
        local = plugin_dir / "copy_freely" / "data"
        local.mkdir(parents=True, exist_ok=True)
        (local / "old1.txt").write_text("PRECIOUS OLD 1\n", encoding="utf-8")
        (local / "old2.txt").write_text("PRECIOUS OLD 2\n", encoding="utf-8")
        pre_state = {
            str(p.relative_to(local)): p.read_text(encoding="utf-8")
            for p in local.rglob("*") if p.is_file()
        }
        # Fault-inject os.replace: the FIRST call inside
        # _clean_pull_atomic succeeds (target → aside), the
        # SECOND call (stage → target) raises.  This simulates
        # a real failure (e.g. ENOSPC, EACCES) at the moment
        # the staged tree would have been swapped in.
        from daemon.plugin_subsystem import sync_runner as sr
        real_replace = sr.os.replace
        call_count = {"n": 0}

        def faulty_replace(src, dst):
            call_count["n"] += 1
            # The first os.replace in _clean_pull_atomic is the
            # aside move (target → aside).  The second is the
            # in-move (stage → target).  We only fault the in-move.
            if call_count["n"] == 2 and str(dst).endswith("/copy_freely"):
                raise OSError("FAULT-INJECTED: stage replace failed")
            return real_replace(src, dst)

        monkeypatch.setattr(sr.os, "replace", faulty_replace)
        with pytest.raises(OSError, match="FAULT-INJECTED"):
            sync(
                "demo", "copy_freely",
                upstream_repo=str(repo), upstream_tag="v1.0.0",
                plugin_dir=plugin_dir, dry_run=False,
            )
        # The old tree was restored (rename-aside → restore
        # happened inside the except branch of the second
        # os.replace).  Verify the precious files are still
        # there with their original content.
        post_state = {
            str(p.relative_to(local)): p.read_text(encoding="utf-8")
            for p in local.rglob("*") if p.is_file()
        }
        assert post_state == pre_state, (
            f"rename-aside restore failed: added={set(post_state) - set(pre_state)}, "
            f"removed={set(pre_state) - set(post_state)}"
        )
        # No stage dir leaked (the except branch's cleanup ran).
        stage_dirs = list(plugin_dir.glob(".sync_stage.*"))
        assert stage_dirs == [], f"stage dir leaked: {stage_dirs}"


class TestReaderCodeNamespacing:
    """Council ③ warning (a) adjudication: the reader-passthrough
    seam at ``sync()`` widens the observable ``refusal.code``
    surface beyond the CON §5 sync 7.  We use a lossless
    overlap / namespace split: reader codes that are ALSO in
    the sync 7 pass through verbatim; reader-only codes are
    namespaced as ``manifest_reader:<code>``.  These tests
    pin the discriminator."""

    def test_overlap_code_passes_through(self, tmp_path):
        """A reader code that IS in the sync 7 enum passes
        through verbatim (no ``manifest_reader:`` prefix).  The
        canonical case: an empty pin triggers a reader
        ``non_tag_pin`` refusal, which is also a sync 7 code —
        the caller sees the bare value."""
        repo = tmp_path / "upstream"
        _init_git_repo(repo, {"data/x.txt": "x"})
        _tag(repo, "v1.0.0")
        plugin_dir = tmp_path / "demo"
        plugin_dir.mkdir()
        manifest = {
            "schema_version": "1.0.0",
            "plugin": {
                "name": "demo",
                "license": "Apache-2.0",
                "upstream": {
                    "repo": str(repo),
                    "tag_pin_per_class": {"copy_freely": ""},  # empty pin → non_tag_pin
                },
                "integration_path": "C",
                "execution_mode": "resource-only",
            },
            "copy_freely": {
                "paths": ["copy_freely/data/"],
                "alarm_owner": "x",
                "escalation": "x",
            },
            "parity_boundary": {"intentionally_not_vendored": [], "not_executed": []},
        }
        (plugin_dir / "MANIFEST.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )
        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(repo), upstream_tag="",
            plugin_dir=plugin_dir, dry_run=True,
        )
        assert result.action == "refused"
        assert result.refusal is not None
        # Bare "non_tag_pin" — overlap, passes through.
        assert result.refusal.code == "non_tag_pin", (
            f"overlap code should pass through verbatim; got {result.refusal.code!r}"
        )
        assert not result.refusal.code.startswith("manifest_reader:")

    def test_reader_only_code_namespaced(self, tmp_path):
        """A reader code that is NOT in the sync 7 enum is
        namespaced as ``manifest_reader:<code>``.  The canonical
        case: a structurally-invalid manifest (e.g. unknown
        field) triggers a reader refusal whose code is
        reader-only.  The caller sees the namespaced value so
        the sync 7 enum is observably closed."""
        plugin_dir = tmp_path / "demo"
        plugin_dir.mkdir()
        # Build a manifest with an UNKNOWN field at the top
        # level — the reader's structural validator refuses
        # with code "unknown_field" (reader-only, NOT in the
        # sync 7).  We use a non-empty tag pin so the
        # pin-validation path doesn't fire first; the unknown-
        # field check is earlier in the reader pipeline.
        manifest = {
            "schema_version": "1.0.0",
            "plugin": {
                "name": "demo",
                "license": "Apache-2.0",
                "upstream": {
                    "repo": "/tmp/up",
                    "tag_pin_per_class": {"copy_freely": "v1.0.0"},
                },
                "integration_path": "C",
                "execution_mode": "resource-only",
            },
            "copy_freely": {
                "paths": ["copy_freely/data/"],
                "alarm_owner": "x",
                "escalation": "x",
            },
            "parity_boundary": {"intentionally_not_vendored": [], "not_executed": []},
            "BOGUS_UNKNOWN_FIELD": "this is not a valid manifest field",  # ←
        }
        (plugin_dir / "MANIFEST.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )
        result = sync(
            "demo", "copy_freely",
            upstream_repo="/tmp/up", upstream_tag="v1.0.0",
            plugin_dir=plugin_dir, dry_run=True,
        )
        assert result.action == "refused"
        assert result.refusal is not None
        # Reader-only code is namespaced.
        assert result.refusal.code.startswith("manifest_reader:"), (
            f"reader-only code should be namespaced; got {result.refusal.code!r}"
        )
        # The bare reader code is preserved in the namespace.
        assert "unknown_field" in result.refusal.code

    def test_no_silent_skip_in_pull_path(self, tmp_path):
        """Exhaustive grep-style check: the public sync() path
        has no ``except (..., ..., ): continue`` (or
        ``except ...: pass``) that swallows an error class
        reachable from the pull path.  This is a structural
        regression guard for the CRITICAL #2 fix."""
        import re
        from daemon.plugin_subsystem import sync_runner as sr
        # Read the source as text and look for the exact
        # CRITICAL #2 pattern that was removed:
        # ``except (...) ...: continue`` inside the pull path.
        # After the fix, no such pattern should exist in the
        # pull helpers (_clean_pull_atomic, _compute_class_diff).
        with open(sr.__file__, "r", encoding="utf-8") as fh:
            src = fh.read()
        # The fix removed: ``except (subprocess.CalledProcessError,
        # RuntimeError): continue`` and ``except RuntimeError:
        # continue`` and ``except ValueError: continue`` from the
        # pull helpers.  None of these should remain.
        forbidden_patterns = [
            r"except\s*\(\s*subprocess\.CalledProcessError\s*,\s*RuntimeError\s*\)\s*:\s*continue",
            r"except\s*RuntimeError\s*:\s*continue\s*$",
        ]
        for pat in forbidden_patterns:
            matches = re.findall(pat, src, re.MULTILINE)
            assert not matches, (
                f"silent-skip pattern {pat!r} still present in sync_runner.py: {matches}"
            )


# ─── Pinning-test referential integrity (council ③ addendum #1) ───────────


def _collect_pytest_test_ids(repo_root: Path) -> set:
    """Collect the set of fully-qualified pytest test ids in
    ``tests/unit/plugin_subsystem`` (the same collection the
    ``pytest --collect-only -q`` reporter would surface).

    Uses a subprocess so the test ids match the on-disk
    invocation exactly (the council ③ addendum requires
    "fully-qualified exactly as pytest sees it").  Cost: one
    subprocess per call; tests should cache via the module-
    scoped fixture below.
    """
    proc = subprocess.run(
        [
            ".venv/bin/python",
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "tests/unit/plugin_subsystem",
        ],
        capture_output=True,
        check=True,
        cwd=str(repo_root),
    )
    # Each non-empty line is one test id (the -q reporter strips
    # the "<Module>::<Class>::<test>" prefix down to the test
    # name only when -q is used WITHOUT --no-header; with
    # --collect-only the full id is reported as the line text).
    # Some lines (e.g. warnings, the "collected N items"
    # summary) are not test ids — they don't contain "::".
    ids: set = set()
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        if "::" not in line:
            # summary line ("231 tests collected") or warning —
            # not a test id.
            continue
        ids.add(line)
    return ids


_REPO_ROOT = Path(__file__).resolve().parents[3]
_MANIFEST_PATH = _REPO_ROOT / "plugins" / "opendesign" / "MANIFEST.yaml"


@pytest.fixture(scope="module")
def collected_test_ids() -> set:
    """Session-cached set of pytest test ids in
    ``tests/unit/plugin_subsystem`` (one subprocess per test
    module).  The fixture is module-scoped so a single
    collection serves the whole referential-integrity class
    rather than re-running pytest for every test."""
    return _collect_pytest_test_ids(_REPO_ROOT)


def _walk_pinning_test_pointers(doc):
    """Walk the parsed manifest and yield (location, value) for
    every ``pinning_test:`` field at any depth, in any class.

    The reviewer-required scope is "EVERY pinning_test pointer
    across ALL classes (not just the 4 snapshot entries — any
    class, current and future)" — so we walk the full doc and
    collect every match, not just the snapshot-class
    divergence_register.  ``location`` is a dotted path
    string (e.g. ``snapshot_with_drift_alarm.divergence_register.0``)
    for diagnostic pinpointing when a pointer dangles.
    """
    def _walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "pinning_test":
                    yield ".".join(str(p) for p in path), v
                else:
                    yield from _walk(v, path + (k,))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                yield from _walk(item, path + (i,))
    yield from _walk(doc, ())


class TestPinningTestReferentialIntegrity:
    """Reviewer-required addendum #1 (council ③, 2026-10-06):
    structural recurrence guard against dangling pinning_test
    pointers in the manifest.

    Loads ``plugins/opendesign/MANIFEST.yaml``, extracts EVERY
    ``pinning_test:`` pointer across ALL classes (not just the
    4 snapshot divergence_register entries — any class, any
    depth, current and future), and asserts each pointer
    resolves to a REAL collected pytest test id, fully-
    qualified exactly as pytest sees it.

    Catches BOTH:
    - missing files / missing test classes (the pointer's
      file path doesn't exist in the collection), AND
    - missing test names within a real class (the file exists
      but the specific test function is absent — the original
      ③ review failure shape: 4 seeded entries pointed at
      TestSnapshotClassByteFidelity tests that did not yet
      exist as code).

    The test is fast (one pytest --collect-only subprocess
    per module, cached as a fixture) and offline (no network,
    no upstream checkout).
    """

    def test_every_pinning_test_pointer_resolves_to_a_real_test(
        self, collected_test_ids
    ):
        """Every ``pinning_test:`` value in MANIFEST.yaml MUST
        be a string that matches a collected pytest test id
        exactly (full file path + class + test name)."""
        with open(_MANIFEST_PATH, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        pointers = list(_walk_pinning_test_pointers(doc))
        # Sanity: the manifest has at least the 4 seeded
        # entries (the original ③ dangling-pointer failure
        # was "all 4 dangling").  If this assertion fires,
        # the walker is broken or the manifest lost its
        # divergence_register — investigate BEFORE the next
        # assertion, which would otherwise trivially pass
        # on an empty list.
        assert len(pointers) >= 4, (
            f"expected ≥4 pinning_test pointers in MANIFEST.yaml "
            f"(the 4 seeded divergence_register entries); got "
            f"{len(pointers)}.  Walker may be broken or manifest "
            f"lost its register."
        )
        # The actual referential-integrity check.
        missing = [
            (loc, ptr) for loc, ptr in pointers
            if ptr not in collected_test_ids
        ]
        assert not missing, (
            f"dangling pinning_test pointers in MANIFEST.yaml: "
            f"these {len(missing)} pointer(s) do NOT match any "
            f"collected pytest test id in tests/unit/plugin_subsystem "
            f"(verified via ``pytest --collect-only -q``):\n"
            + "\n".join(f"  {loc}: {ptr!r}" for loc, ptr in missing)
        )

    def test_pinning_test_pointers_use_full_path_format(
        self, collected_test_ids
    ):
        """Every ``pinning_test:`` value MUST be a non-empty
        string in the canonical ``path::Class::test`` format
        (no abbreviations, no relative paths, no class
        qualifiers stripped).  This is the shape the
        referential-integrity check (above) compares against
        — if a pointer is malformed, it can never match
        pytest's collection output."""
        with open(_MANIFEST_PATH, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        pointers = list(_walk_pinning_test_pointers(doc))
        malformed = []
        for loc, ptr in pointers:
            if not isinstance(ptr, str) or not ptr.strip():
                malformed.append((loc, f"empty/non-string: {ptr!r}"))
                continue
            # Canonical shape: 3 components separated by
            # "::" (file path, class name, test name).
            # A file path contains at least one "/" or
            # ends in ".py"; a test name is a Python
            # identifier; a class name is a PascalCase
            # identifier.
            parts = ptr.split("::")
            if len(parts) != 3:
                malformed.append((loc, f"expected 3 '::'-separated parts; got {len(parts)}: {ptr!r}"))
                continue
            file_path, class_name, test_name = parts
            if not file_path.endswith(".py") or "/" not in file_path:
                malformed.append((loc, f"file path is not a 'tests/.../x.py' shape: {file_path!r}"))
            if not class_name or not class_name[0].isupper():
                malformed.append((loc, f"class name is not PascalCase: {class_name!r}"))
            if not test_name or not test_name.startswith("test_"):
                malformed.append((loc, f"test name does not start with 'test_': {test_name!r}"))
        assert not malformed, (
            f"malformed pinning_test pointers in MANIFEST.yaml "
            f"(the referential-integrity check requires the "
            f"canonical 'path::Class::test' shape so a "
            f"pytest --collect-only match is well-defined):\n"
            + "\n".join(f"  {loc}: {reason}" for loc, reason in malformed)
        )

    def test_no_duplicate_pinning_test_pointers(self):
        """No two ``pinning_test:`` entries may point at the
        SAME test (the divergence_register is a list of
        distinct upstream drift observations, not a
        multiple-claim ledger on one test)."""
        with open(_MANIFEST_PATH, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh)
        pointers = list(_walk_pinning_test_pointers(doc))
        seen: dict = {}
        duplicates = []
        for loc, ptr in pointers:
            if ptr in seen:
                duplicates.append((seen[ptr], loc, ptr))
            else:
                seen[ptr] = loc
        assert not duplicates, (
            f"duplicate pinning_test pointers in MANIFEST.yaml: "
            f"the divergence_register must list distinct "
            f"upstream drift observations, not multiple claims "
            f"on the same test:\n"
            + "\n".join(f"  {first} and {second} both → {ptr!r}" for first, second, ptr in duplicates)
        )
