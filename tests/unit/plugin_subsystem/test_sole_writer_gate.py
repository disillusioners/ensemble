"""Tests for the sole-writer mechanization gate (slice ⑥).

The ⑤ reviewer ruling, binding FROM ⑥: ALL vendored-class membership
changes route EXCLUSIVELY through the sync-runner.  The gate
mechanizes the ruling's four-bar minimum:

1. SHA-byte-faithful to the pin        → ``modified-from-pin``
2. hash-registered                     → ``hash-unregistered`` / ``hash-mismatch``
3. manifest↔HASHES↔tree consistent     → ``hashes-orphan`` / ``file-outside-declared-paths``
4. membership delta disclosed          → ``undeclared-file`` (hand-addition)

Each bar is tested broken individually; the clean state passes; the
with-trail membership change (the sanctioned sync route) passes; the
unexplained hand-edit is refused.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Dict, List

import pytest
import yaml

from daemon.plugin_subsystem.sole_writer_gate import (
    HASHES_FILENAME,
    check_sole_writer,
)
from daemon.plugin_subsystem.sync_runner import LocalGitCheckout


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures (the test_sync_runner synthetic-repo pattern)
# ══════════════════════════════════════════════════════════════════════════════


def _init_git_repo(path: Path, files: Dict[str, str]) -> str:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    try:
        subprocess.run(["git", "-C", str(path), "checkout", "-q", "-b", "main"], check=True)
    except subprocess.CalledProcessError:
        pass
    subprocess.run(["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    for rel, content in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True, check=True, text=True,
    ).stdout.strip()


def _tag(repo: Path, name: str) -> None:
    subprocess.run(["git", "-C", str(repo), "tag", "-a", name, "-m", f"tag {name}"], check=True)


def _hashes_content(plugin_dir: Path, class_name: str, skip: set[str] | None = None) -> str:
    """Build a sha256sum-format HASHES.sha256 over the class subtree."""
    skip = skip or set()
    root = plugin_dir / class_name
    rows: List[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == HASHES_FILENAME:
            continue
        rel = path.relative_to(root).as_posix()
        if rel in skip:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"{digest}  {rel}")
    return "\n".join(rows) + ("\n" if rows else "")


def _make_plugin(
    plugin_dir: Path,
    *,
    upstream_repo: str,
    pin: str = "v1.0.0",
    class_name: str = "copy_freely",
    local_subdir: str = "data",
    register: list[dict] | None = None,
) -> Path:
    """A minimal single-class plugin tree (no vendored files yet)."""
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "schema_version": "1.0.0",
        "plugin": {
            "name": "demo",
            "license": "Apache-2.0",
            "upstream": {"repo": upstream_repo, "tag_pin_per_class": {"copy_freely": pin}},
            "integration_path": "C",
            "execution_mode": "resource-only",
        },
        class_name: {
            "paths": [f"{class_name}/{local_subdir}/"],
            "alarm_owner": "demo-owner",
            "escalation": "block-promote-after-days",
        },
        "parity_boundary": {"intentionally_not_vendored": [], "not_executed": []},
    }
    if class_name == "snapshot_with_drift_alarm":
        manifest[class_name]["divergence_register"] = register or [
            {
                "id": 1,
                "files": [f"{class_name}/{local_subdir}/x.ts"],
                "delta": "seeded",
                "rationale": "test seed",
                "pinning_test": "test_x.py::test_y",
                "status": "registered",
            }
        ]
    (plugin_dir / "MANIFEST.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    return plugin_dir


def _vendored_from_pin(
    plugin_dir: Path, upstream_repo: Path, pin: str, class_name: str, local_subdir: str
) -> None:
    """Vendor the pin's declared subtree byte-faithfully (the sanctioned
    initial state, mirroring what a clean pull lands)."""
    upstream_dir = upstream_repo / local_subdir
    class_root = plugin_dir / class_name / local_subdir
    for path in sorted(upstream_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(upstream_dir)
        target = class_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    (plugin_dir / class_name / HASHES_FILENAME).write_text(
        _hashes_content(plugin_dir, class_name), encoding="utf-8"
    )


def _codes(verdict: dict) -> set[str]:
    return {v["code"] for v in verdict["violations"]}


@pytest.fixture
def scenario(tmp_path):
    """An upstream repo at v1.0.0 with data/{a.txt,b.txt}; the plugin
    vendored byte-faithfully at the same pin (the clean state)."""
    upstream = tmp_path / "upstream"
    _init_git_repo(
        upstream,
        {"data/a.txt": "A\n", "data/b.txt": "B\n"},
    )
    _tag(upstream, "v1.0.0")
    plugin_dir = _make_plugin(tmp_path / "demo", upstream_repo=str(upstream))
    _vendored_from_pin(plugin_dir, upstream, "v1.0.0", "copy_freely", "data")
    return {
        "upstream": upstream,
        "plugin_dir": plugin_dir,
        "checkout": lambda: LocalGitCheckout(str(upstream), "v1.0.0"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# The four bars
# ══════════════════════════════════════════════════════════════════════════════


class TestCleanState:
    def test_byte_faithful_registered_consistent_passes(self, scenario):
        verdict = check_sole_writer(scenario["plugin_dir"], upstream=scenario["checkout"]())
        assert verdict["ok"] is True, verdict["violations"]
        assert verdict["bars"] == {
            "byte_faithful_to_pin": True,
            "hash_registered": True,
            "manifest_hashes_tree_consistent": True,
            "membership_delta_disclosed": True,
        }
        assert verdict["violations"] == []


class TestBar1ByteFaithful:
    def test_modified_file_at_same_pin_is_a_hand_edit(self, scenario):
        pdir = scenario["plugin_dir"]
        (pdir / "copy_freely" / "data" / "a.txt").write_text("TAMPERED\n", encoding="utf-8")
        # HASHES re-registered for the tampered content — bar 2 alone
        # must NOT absorb the violation; bar 1 catches the same-pin
        # modification.
        (pdir / "copy_freely" / HASHES_FILENAME).write_text(
            _hashes_content(pdir, "copy_freely"), encoding="utf-8"
        )
        verdict = check_sole_writer(pdir, upstream=scenario["checkout"]())
        assert "modified-from-pin" in _codes(verdict)
        assert verdict["bars"]["byte_faithful_to_pin"] is False
        assert verdict["ok"] is False


class TestBar2HashRegistered:
    def test_unregistered_file_refused(self, scenario):
        pdir = scenario["plugin_dir"]
        extra = pdir / "copy_freely" / "data" / "c.txt"
        extra.write_text("C\n", encoding="utf-8")
        verdict = check_sole_writer(pdir, upstream=scenario["checkout"]())
        assert "hash-unregistered" in _codes(verdict)
        assert verdict["bars"]["hash_registered"] is False

    def test_mismatched_digest_refused(self, scenario):
        pdir = scenario["plugin_dir"]
        hashes = pdir / "copy_freely" / HASHES_FILENAME
        rows = hashes.read_text().splitlines()
        tampered = [rows[0].replace(rows[0].split()[0], "0" * 64, 1)] + rows[1:]
        hashes.write_text("\n".join(tampered) + "\n", encoding="utf-8")
        verdict = check_sole_writer(pdir, upstream=scenario["checkout"]())
        assert "hash-mismatch" in _codes(verdict)


class TestBar3ManifestHashesTree:
    def test_orphan_hash_row_refused(self, scenario):
        pdir = scenario["plugin_dir"]
        hashes = pdir / "copy_freely" / HASHES_FILENAME
        digest = hashlib.sha256(b"ghost\n").hexdigest()
        hashes.write_text(hashes.read_text() + f"{digest}  data/ghost.txt\n", encoding="utf-8")
        verdict = check_sole_writer(pdir, upstream=scenario["checkout"]())
        assert "hashes-orphan" in _codes(verdict)
        assert verdict["bars"]["manifest_hashes_tree_consistent"] is False

    def test_file_outside_declared_paths_refused(self, scenario):
        pdir = scenario["plugin_dir"]
        stray = pdir / "copy_freely" / "stray.txt"
        stray.write_text("stray\n", encoding="utf-8")
        # register it in HASHES so bar 2 is not the (co-)violator under test
        hashes = pdir / "copy_freely" / HASHES_FILENAME
        digest = hashlib.sha256(stray.read_bytes()).hexdigest()
        hashes.write_text(hashes.read_text() + f"{digest}  stray.txt\n", encoding="utf-8")
        verdict = check_sole_writer(pdir, upstream=scenario["checkout"]())
        assert "file-outside-declared-paths" in _codes(verdict)


class TestBar4MembershipDeltaDisclosed:
    def test_hand_added_file_refused_even_when_hash_registered(self, scenario):
        # THE bar-4 teeth: a file the pin never had, hash-registered so
        # bars 2+3 stay quiet — the gate still refuses the hand-edit.
        pdir = scenario["plugin_dir"]
        hand = pdir / "copy_freely" / "data" / "hand-made.txt"
        hand.write_text("hand-authored\n", encoding="utf-8")
        hashes = pdir / "copy_freely" / HASHES_FILENAME
        digest = hashlib.sha256(hand.read_bytes()).hexdigest()
        hashes.write_text(hashes.read_text() + f"{digest}  data/hand-made.txt\n", encoding="utf-8")
        verdict = check_sole_writer(pdir, upstream=scenario["checkout"]())
        assert "undeclared-file" in _codes(verdict)
        assert verdict["bars"]["membership_delta_disclosed"] is False
        assert verdict["ok"] is False

    def test_membership_change_via_sync_route_passes_with_trail(self, scenario, tmp_path):
        # The SANCTIONED route: manifest declares a NEW upstream path,
        # a real sync pulls it (trail records the sync_result), the
        # gate passes — membership change WITH a sync_result trail.
        upstream = scenario["upstream"]
        (upstream / "more").mkdir()
        (upstream / "more" / "m.txt").write_text("M\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(upstream), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(upstream), "commit", "-q", "-m", "add more"], check=True)
        # move the tag onto the new commit (the fixture extends the
        # pinned branch in place; a real flow would cut v1.0.1)
        subprocess.run(
            ["git", "-C", str(upstream), "tag", "-f", "-a", "v1.0.0", "-m", "retag"],
            check=True,
        )
        pdir = scenario["plugin_dir"]
        # declare the new path + pull through the REAL sync-runner
        manifest_path = pdir / "MANIFEST.yaml"
        doc = yaml.safe_load(manifest_path.read_text())
        doc["copy_freely"]["paths"].append("copy_freely/more/")
        manifest_path.write_text(yaml.safe_dump(doc, sort_keys=False))
        from daemon.plugin_subsystem import sync

        result = sync(
            "demo", "copy_freely",
            upstream_repo=str(upstream), upstream_tag="v1.0.0",
            plugin_dir=pdir, dry_run=False,
        )
        assert result.action == "clean_pulled", result.as_dict()
        # the trail records the sanctioned change
        assert (pdir / "sync_trail.jsonl").is_file()
        # The sanctioned hash-registration step (bar 2 of the ruling's
        # four bars: "manifest upstream_paths declare → sync →
        # hash-register").  FINDING (recorded for the reviewer): the
        # sync-runner does not auto-hash-register pulled files — the
        # registration is a separate mechanical step today.
        (pdir / "copy_freely" / HASHES_FILENAME).write_text(
            _hashes_content(pdir, "copy_freely"), encoding="utf-8"
        )
        verdict = check_sole_writer(pdir, upstream=scenario["checkout"]())
        assert verdict["ok"] is True, verdict["violations"]
        # the new files are hash-registered by the sanctioned step
        assert "more/m.txt" in (pdir / "copy_freely" / HASHES_FILENAME).read_text()

    def test_sync_pulled_file_not_yet_hash_registered_is_reported(self, scenario):
        # The pull lands files but the hash-registration step hasn't
        # run yet — bar 2 flags it (the gate DETECTS the gap; that is
        # its job — the finding is recorded for the reviewer: the
        # sync-runner does not auto-hash-register).
        pdir = scenario["plugin_dir"]
        new_file = pdir / "copy_freely" / "data" / "new-from-pull.txt"
        new_file.write_text("pulled bytes\n", encoding="utf-8")
        # NOT registered in HASHES
        verdict = check_sole_writer(pdir, upstream=scenario["checkout"]())
        assert "hash-unregistered" in _codes(verdict)


class TestSkippedVerdicts:
    def test_url_shaped_upstream_is_a_visible_skip(self, scenario):
        # Point the manifest at a URL; no local checkout ⇒ skipped
        # (never a silent pass, never a fake green).
        pdir = scenario["plugin_dir"]
        manifest_path = pdir / "MANIFEST.yaml"
        doc = yaml.safe_load(manifest_path.read_text())
        doc["plugin"]["upstream"]["repo"] = "https://example.com/x.git"
        manifest_path.write_text(yaml.safe_dump(doc, sort_keys=False))
        verdict = check_sole_writer(pdir)  # no upstream injected
        assert verdict["skipped"] == "upstream-unavailable"
        assert verdict["violations"] == []

    def test_manifest_refusal_is_a_skip(self, tmp_path):
        pdir = tmp_path / "broken"
        pdir.mkdir()
        (pdir / "MANIFEST.yaml").write_text("schema_version: '9.9.9'\n", encoding="utf-8")
        verdict = check_sole_writer(pdir)
        assert verdict["skipped"] == "manifest-unreadable"
        assert _codes(verdict) == {"manifest-unreadable"}
