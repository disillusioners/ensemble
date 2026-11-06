"""Phase-1 unit pack — ``daemon/services/release_manifest.py`` (T1.3b).

The reader in isolation (absent → None; torn → typed raise; missing
top-level keys tolerated; unmanifested-requested → SKIP verdict) plus
the manifest VERIFICATION legs of the phase-1 gate (AC1.4), exercised
against scratch COPY trees only (D19 — never a real tree):
``manifest_mismatch`` on a tampered copy, ``manifest_missing_live`` on
a live-shaped release without a manifest, ``manifest_unreadable`` on
torn JSON, and the dev-absent skip disclosure.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import daemon.services.prompt_refresh as pr
from daemon.loader import PromptCache
from daemon.services.release_manifest import (
    ManifestError,
    load_release_manifest,
    verify_paths,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
ARI_DIR = REPO_ROOT / "agents" / "ari"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage_release(tmp_path: Path, version: str = "v9.9.9") -> Path:
    """A scratch LIVE-SHAPED release tree holding a copy of ari."""
    release_root = tmp_path / "install" / "releases" / version
    agents_dir = release_root / "agents"
    ari_dir = agents_dir / "ari"
    ari_dir.mkdir(parents=True)
    for item in sorted(ARI_DIR.iterdir()):
        if item.is_file():
            (ari_dir / item.name).write_bytes(item.read_bytes())
    (agents_dir / "_prompt_system").mkdir()
    for name in ("project-experience.md", "knowledge.md", "knowledge_no_force_explore.md"):
        src = REPO_ROOT / "agents" / "_prompt_system" / name
        if src.is_file():
            (agents_dir / "_prompt_system" / name).write_bytes(src.read_bytes())
    return release_root


def _write_manifest(release_root: Path, entries: dict[str, str]) -> None:
    (release_root / "manifest.json").write_text(
        json.dumps({"agents_manifest": entries, "version": "9.9.9"}), encoding="utf-8"
    )


def _agent_manifest_entries(release_root: Path) -> dict[str, str]:
    agents_dir = release_root / "agents"
    entries: dict[str, str] = {}
    for path in sorted(agents_dir.rglob("*")):
        if path.is_file():
            entries[str(path.relative_to(agents_dir))] = _hash(path)
    return entries


# ── The reader in isolation (T1.3b outcome) ─────────────────────────────────


class TestReader:
    def test_absent_returns_none(self, tmp_path: Path) -> None:
        assert load_release_manifest(tmp_path) is None

    def test_torn_json_raises_typed(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text("{ torn", encoding="utf-8")
        with pytest.raises(ManifestError):
            load_release_manifest(tmp_path)

    def test_non_object_root_raises_typed(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text("[1,2]", encoding="utf-8")
        with pytest.raises(ManifestError):
            load_release_manifest(tmp_path)

    def test_missing_agents_manifest_raises_typed(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text('{"plugins_manifest": {}}', encoding="utf-8")
        with pytest.raises(ManifestError):
            load_release_manifest(tmp_path)

    def test_missing_other_top_level_keys_tolerated(self, tmp_path: Path) -> None:
        # demo v0.16.3 lacks plugins_manifest — tolerated (AM-2).
        (tmp_path / "manifest.json").write_text(
            '{"agents_manifest": {"ari/meta.json": "' + "0" * 64 + '"}}', encoding="utf-8"
        )
        manifest = load_release_manifest(tmp_path)
        assert manifest is not None
        assert "plugins_manifest" not in manifest.raw

    def test_unmanifested_requested_paths_are_skip_never_mismatch(self, tmp_path: Path) -> None:
        (tmp_path / "manifest.json").write_text(
            '{"agents_manifest": {}}', encoding="utf-8"
        )
        manifest = load_release_manifest(tmp_path)
        verdict = verify_paths(tmp_path, manifest, ["ari/memories/anything.md"])
        assert verdict.ok
        assert verdict.skipped_unmanifested == 1
        assert verdict.checked == 0
        assert verdict.unexpected_skips == ()

    def test_unmanifested_outside_the_allow_list_is_a_refusal(self, tmp_path: Path) -> None:
        """C1.3 — a skip outside ``memories/`` can never be laundered as
        benign; it is a refusal-grade finding."""
        (tmp_path / "manifest.json").write_text(
            '{"agents_manifest": {}}', encoding="utf-8"
        )
        manifest = load_release_manifest(tmp_path)
        verdict = verify_paths(tmp_path, manifest, ["ari/knowledge.md"])
        assert not verdict.ok
        assert verdict.skipped_unmanifested == 0
        assert any("ari/knowledge.md" in s for s in verdict.unexpected_skips)

    def test_mismatch_detected_on_tampered_file(self, tmp_path: Path) -> None:
        release_root = _stage_release(tmp_path)
        entries = _agent_manifest_entries(release_root)
        _write_manifest(release_root, entries)
        manifest = load_release_manifest(release_root)
        # Tamper the COPY (D19 — never a real tree).
        target = release_root / "agents" / "ari" / "rule.md"
        target.write_text(target.read_text(encoding="utf-8") + "\nTAMPERED", encoding="utf-8")
        verdict = verify_paths(release_root, manifest, ["ari/rule.md"])
        assert not verdict.ok
        assert any("ari/rule.md" in m for m in verdict.mismatches)

    def test_manifested_but_missing_on_disk_is_mismatch(self, tmp_path: Path) -> None:
        release_root = _stage_release(tmp_path)
        entries = _agent_manifest_entries(release_root)
        _write_manifest(release_root, entries)
        (release_root / "agents" / "ari" / "rule.md").unlink()
        manifest = load_release_manifest(release_root)
        verdict = verify_paths(release_root, manifest, ["ari/rule.md"])
        assert not verdict.ok
        assert any("missing on disk" in m for m in verdict.mismatches)


# ── The primitive's manifest branch (AC1.4 legs, scratch trees only) ────────


class _Repo:
    def get(self, instance_id: str):
        return SimpleNamespace(agent_id="ari", instance_metadata={})


class _Manager:
    def __init__(self):
        self.instances = {}
        self.prompt_cache = PromptCache()
        self._instance_repository = _Repo()
        self._lifecycle_service = SimpleNamespace(
            _get_mcp_tool_names=lambda iid, stored: []
        )


def _live_prov(install_root: Path):
    return {
        "self_env": "live",
        "install_root": str(install_root),
        "running_release": "v9.9.9",
        "current_target": "/releases/v9.9.9",
        "current_matches_running": "true",
        "divergence_line": "",
        "daemon_version": "0.29.0",
    }


def _compose_ok(agent_id, agent_dir, cache, mcp, version_tag=None, is_custom_agent=False):
    key = cache._make_key(agent_id, mcp, version_tag)
    if key in cache._cache:
        prompt, tokens, _ = cache._cache[key]
        return prompt, tokens
    cache.set(agent_id, "P1", 1, {"soul.md": 1.0}, mcp, version_tag)
    return "P1", 1


def _registry_pointing_at(agent_dir: Path):
    import daemon.registry as registry_mod

    return patch.object(
        registry_mod,
        "get_registry",
        lambda: SimpleNamespace(
            get_resolved=lambda aid: SimpleNamespace(path=agent_dir, version_tag=None),
            _agents_dir=agent_dir.parent,
        ),
    )


async def _run_live(manager, tmp_path: Path):
    install_root = tmp_path / "install"
    with patch.object(pr, "_release_provenance", lambda: _live_prov(install_root)), patch(
        "daemon.manager.load_and_cache_prompt", _compose_ok
    ), _registry_pointing_at(
        install_root / "releases" / "v9.9.9" / "agents" / "ari"
    ):
        return await pr.refresh_instance_prompt(manager, "iid-1")


class TestPrimitiveManifestLegs:
    async def test_live_clean_release_passes_with_manifest_ok(self, tmp_path: Path) -> None:
        pr._BASELINE_MAP.clear()
        release_root = _stage_release(tmp_path)
        _write_manifest(release_root, _agent_manifest_entries(release_root))
        result = await _run_live(_Manager(), tmp_path)
        assert result.ok, result.message
        assert str(result.provenance["manifest"]).startswith("ok(")
        pr._BASELINE_MAP.clear()

    async def test_live_tampered_copy_is_manifest_mismatch(self, tmp_path: Path) -> None:
        pr._BASELINE_MAP.clear()
        release_root = _stage_release(tmp_path)
        _write_manifest(release_root, _agent_manifest_entries(release_root))
        target = release_root / "agents" / "ari" / "soul.md"
        target.write_text(target.read_text(encoding="utf-8") + "\nEVIL", encoding="utf-8")
        result = await _run_live(_Manager(), tmp_path)
        assert result.code == pr.CODE_MANIFEST_MISMATCH
        pr._BASELINE_MAP.clear()

    async def test_live_tampered_SHARED_persona_file_refuses(self, tmp_path: Path) -> None:
        """C1.4 — the council's end-to-end reproduction: tampering the
        SHARED ``_prompt_system/knowledge.md`` under an AUTHENTIC manifest
        must REFUSE. Before the fix the shared files were keyed under the
        agent dir → unmanifested → skipped → ``manifest=ok`` and the
        tampered persona was popped into effect."""
        pr._BASELINE_MAP.clear()
        release_root = _stage_release(tmp_path)
        _write_manifest(release_root, _agent_manifest_entries(release_root))
        shared = release_root / "agents" / "_prompt_system" / "knowledge.md"
        assert shared.is_file(), "the staged copy must carry the shared knowledge file"
        shared.write_text(
            shared.read_text(encoding="utf-8") + "\nTAMPERED SHARED PERSONA",
            encoding="utf-8",
        )
        manager = _Manager()
        manager.instances = {"iid-1": (object(), "x")}  # must SURVIVE a refusal
        result = await _run_live(manager, tmp_path)
        assert result.code == pr.CODE_MANIFEST_MISMATCH, result.message
        assert "_prompt_system/knowledge.md" in result.message
        # And NOTHING composed/popped for a refused call.
        assert "iid-1" in manager.instances
        pr._BASELINE_MAP.clear()

    async def test_live_unmanifested_shared_file_refuses(self, tmp_path: Path) -> None:
        """C1.3 end-to-end: a manifest missing a shared entry is an
        unexpected skip → refusal (never ``ok`` with a skip count)."""
        pr._BASELINE_MAP.clear()
        release_root = _stage_release(tmp_path)
        entries = _agent_manifest_entries(release_root)
        entries.pop("_prompt_system/knowledge.md")  # drop one shared entry
        _write_manifest(release_root, entries)
        result = await _run_live(_Manager(), tmp_path)
        assert result.code == pr.CODE_MANIFEST_MISMATCH
        assert "_prompt_system/knowledge.md" in result.message
        pr._BASELINE_MAP.clear()

    async def test_live_absent_manifest_is_missing_live(self, tmp_path: Path) -> None:
        pr._BASELINE_MAP.clear()
        _stage_release(tmp_path)  # no manifest written
        result = await _run_live(_Manager(), tmp_path)
        assert result.code == pr.CODE_MANIFEST_MISSING_LIVE
        pr._BASELINE_MAP.clear()

    async def test_live_torn_manifest_is_unreadable(self, tmp_path: Path) -> None:
        pr._BASELINE_MAP.clear()
        release_root = _stage_release(tmp_path)
        (release_root / "manifest.json").write_text("{ nope", encoding="utf-8")
        result = await _run_live(_Manager(), tmp_path)
        assert result.code == pr.CODE_MANIFEST_UNREADABLE
        pr._BASELINE_MAP.clear()

    async def test_sandbox_shaped_verifies_like_live(self, tmp_path: Path) -> None:
        pr._BASELINE_MAP.clear()
        release_root = _stage_release(tmp_path)
        # sandbox-shaped: manifest PRESENT and clean → verifies, proceeds.
        _write_manifest(release_root, _agent_manifest_entries(release_root))
        install_root = tmp_path / "install"
        manager = _Manager()
        prov = _live_prov(install_root)
        prov["self_env"] = "sandbox"
        with patch.object(pr, "_release_provenance", lambda: prov), patch(
            "daemon.manager.load_and_cache_prompt", _compose_ok
        ), _registry_pointing_at(install_root / "releases" / "v9.9.9" / "agents" / "ari"):
            result = await pr.refresh_instance_prompt(manager, "iid-1")
        assert result.ok
        assert str(result.provenance["manifest"]).startswith("ok(")
        pr._BASELINE_MAP.clear()
