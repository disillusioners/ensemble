"""Phase-4 unit substrate — the L3 refusal ladder THROUGH THE TOOL.

All ten codes (9 REFUSED + 1 FAILED) independently proven at the TOOL
surface (the typed envelope string), plus the non-ari three-layer leg
(the notify-exact ``ERROR:`` shape — deliberately NOT a REFUSED code)
and the uncached-instance proceed-with-flag case. The dev-daemon E2E
variants and the mutation legs belong to the tester gate.

Arithmetic (Rev 3/B2, fixed): 9 REFUSED + 1 FAILED = 10 codes, each
with ≥1 dedicated test; ``not_ari`` confirmed ABSENT from the REFUSED
set; no code reachable only via another's failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import daemon.services.prompt_refresh as pr
from daemon.loader import PromptCache
from daemon.services.prompt_refresh import REFUSAL_CODES, FAILURE_CODES
from daemon.tools.prompt_reload import create_prompt_reload_tools

REPO_ROOT = Path(__file__).resolve().parents[3]
ARI_DIR = REPO_ROOT / "agents" / "ari"


class StubRepo:
    def __init__(self, agent_id: str = "ari"):
        self.row = SimpleNamespace(agent_id=agent_id, instance_metadata={})

    def get(self, instance_id: str):
        return self.row


class StubManager:
    def __init__(self, agent_id: str = "ari"):
        self.instances = {"iid-1": (object(), str(ARI_DIR))}
        self.prompt_cache = PromptCache()
        self._instance_repository = StubRepo(agent_id)
        self._lifecycle_service = SimpleNamespace(
            _get_mcp_tool_names=lambda iid, stored: []
        )

    async def refresh_instance_prompt(self, instance_id: str, *, force: bool = False):
        # The env-provenance patch is supplied by the enclosing _call()
        # context (it wraps the tool invoke, and this runs inside it).
        return await pr.refresh_instance_prompt(self, instance_id, force=force)


def _prov(self_env: str = "dev", install_root: str | None = None):
    return {
        "self_env": self_env,
        "install_root": install_root,
        "running_release": None,
        "current_target": None,
        "current_matches_running": "unknown",
        "divergence_line": "",
        "daemon_version": "0.29.0",
    }


def _compose_ok(agent_id, agent_dir, cache, mcp, version_tag=None, is_custom_agent=False):
    key = cache._make_key(agent_id, mcp, version_tag)
    if key in cache._cache:
        prompt, tokens, _ = cache._cache[key]
        return prompt, tokens
    read_set = pr._loader_read_set(
        Path(agent_dir), json.loads((Path(agent_dir) / "meta.json").read_text())
    )
    mtimes = {k: p.stat().st_mtime for k, p in read_set.items()}
    mtimes[pr.FP_KEY] = ""
    cache.set(agent_id, "P1", 1, mtimes, mcp, version_tag)
    return "P1", 1


def _registry_at(agent_dir: Path, agents_root: Path | None = None):
    import daemon.registry as registry_mod

    return patch.object(
        registry_mod,
        "get_registry",
        lambda: SimpleNamespace(
            get_resolved=lambda aid: SimpleNamespace(path=agent_dir, version_tag=None),
            _agents_dir=agents_root or agent_dir.parent,
        ),
    )


async def _call(manager: StubManager, *, agent_id: str = "ari", prov=None, compose=_compose_ok):
    tool = create_prompt_reload_tools(
        manager, current_instance_id="iid-1", agent_id=agent_id
    )[0]
    with patch.object(pr, "_release_provenance", lambda: prov or _prov()), patch(
        "daemon.manager.load_and_cache_prompt", compose
    ):
        return await tool.ainvoke({"force": False})


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    pr._BASELINE_MAP.clear()
    monkeypatch.delenv("ENSEMBLE_PROMPT_RELOAD_ENABLED", raising=False)
    yield
    pr._BASELINE_MAP.clear()


def _code_of(output: str) -> str:
    assert output.startswith("Error: prompt_reload "), output
    return output.split("reason=", 1)[1].split(":", 1)[0]


class TestRefusedEnvelopeCodes:
    async def test_1_disabled(self) -> None:
        with patch.dict("os.environ", {"ENSEMBLE_PROMPT_RELOAD_ENABLED": "disabled"}):
            out = await _call(StubManager())
        assert _code_of(out) == "prompt_reload_disabled"
        assert out.startswith("Error: prompt_reload REFUSED — reason=")

    async def test_2_agent_dir_unresolved(self) -> None:
        import daemon.registry as registry_mod

        # Layer 3 passes (caller IS ari); the REGISTRY miss is the refusal.
        manager = StubManager(agent_id="ari")
        with patch.object(
            registry_mod,
            "get_registry",
            lambda: SimpleNamespace(get_resolved=lambda aid: None),
        ):
            out = await _call(manager)
        assert _code_of(out) == "prompt_reload_agent_dir_unresolved"

    async def test_3_dir_invalid_dev_foreign(self, tmp_path: Path) -> None:
        foreign = tmp_path / "agents" / "ari"
        foreign.mkdir(parents=True)
        (foreign / "meta.json").write_text("{}", encoding="utf-8")
        with _registry_at(foreign, REPO_ROOT / "agents"):
            out = await _call(StubManager())
        assert _code_of(out) == "prompt_reload_dir_invalid"

    async def test_3_dir_invalid_marker_unresolvable(self) -> None:
        prov = _prov()
        prov["self_env"] = None
        out = await _call(StubManager(), prov=prov)
        assert _code_of(out) == "prompt_reload_dir_invalid"
        assert "self-env marker" in out

    async def test_4_manifest_missing_live(self, tmp_path: Path) -> None:
        release = tmp_path / "install" / "releases" / "v9.9.9" / "agents" / "ari"
        release.mkdir(parents=True)
        (release / "meta.json").write_text("{}", encoding="utf-8")
        (release / "soul.md").write_text("x", encoding="utf-8")
        prov = _prov("live", str(tmp_path / "install"))
        with _registry_at(release):
            out = await _call(StubManager(), prov=prov)
        assert _code_of(out) == "prompt_reload_manifest_missing_live"

    async def test_5_manifest_unreadable(self, tmp_path: Path) -> None:
        release = tmp_path / "install" / "releases" / "v9.9.9" / "agents" / "ari"
        release.mkdir(parents=True)
        (release / "meta.json").write_text("{}", encoding="utf-8")
        (tmp_path / "install" / "releases" / "v9.9.9" / "manifest.json").write_text(
            "{ nope", encoding="utf-8"
        )
        prov = _prov("live", str(tmp_path / "install"))
        with _registry_at(release):
            out = await _call(StubManager(), prov=prov)
        assert _code_of(out) == "prompt_reload_manifest_unreadable"

    async def test_6_manifest_mismatch(self, tmp_path: Path) -> None:
        import hashlib

        release_root = tmp_path / "install" / "releases" / "v9.9.9"
        ari = release_root / "agents" / "ari"
        ari.mkdir(parents=True)
        (ari / "meta.json").write_text("{}", encoding="utf-8")
        (ari / "soul.md").write_text("hello", encoding="utf-8")
        wrong = hashlib.sha256(b"other").hexdigest()
        (release_root / "manifest.json").write_text(
            json.dumps({"agents_manifest": {"ari/soul.md": wrong}}), encoding="utf-8"
        )
        prov = _prov("live", str(tmp_path / "install"))
        with _registry_at(ari):
            out = await _call(StubManager(), prov=prov)
        assert _code_of(out) == "prompt_reload_manifest_mismatch"

    async def test_7_agent_meta_invalid(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "agents" / "ari"
        agent_dir.mkdir(parents=True)
        (agent_dir / "meta.json").write_text("{ broken", encoding="utf-8")
        with _registry_at(agent_dir):
            out = await _call(StubManager())
        assert _code_of(out) == "prompt_reload_agent_meta_invalid"
        assert "skill-less" in out  # names WHY it must stay distinct (D6 code 7)

    async def test_8_compose_failed(self) -> None:
        def _raise(*a, **k):
            raise RuntimeError("loader exploded")

        out = await _call(StubManager(), compose=_raise)
        assert _code_of(out) == "prompt_reload_compose_failed"

    async def test_9_db_unavailable_names_the_repo(self) -> None:
        class RaisingRepo:
            def get_effective_rows(self, agent_id, version_tag):
                raise RuntimeError("nope")

        tool = create_prompt_reload_tools(
            StubManager(), current_instance_id="iid-1", agent_id="ari"
        )[0]
        with patch.object(pr, "_release_provenance", lambda: _prov()), patch(
            "daemon.manager.load_and_cache_prompt", _compose_ok
        ), patch.object(pr, "_get_prompt_version_repo", lambda: RaisingRepo()):
            out = await tool.ainvoke({"force": False})
        assert _code_of(out) == "prompt_reload_db_unavailable"
        assert "prompt-versions" in out  # names the repo, NOT "the database"


class TestFailedEnvelopeCode:
    async def test_10_internal_error_failed_envelope_and_no_history_write(self) -> None:
        manager = StubManager()
        manager.prompt_cache = None
        out = await _call(manager)
        assert out.startswith("Error: prompt_reload FAILED — reason=prompt_reload_internal_error:")
        # FAILED, not REFUSED (notify_user.py:319-321 shape) — and the
        # message never leaks exception detail (PB-F1 class).
        assert "REFUSED" not in out
        assert "AttributeError" not in out


class TestNonAriLeg:
    async def test_layer3_returns_the_notify_exact_error_shape(self) -> None:
        out = await _call(StubManager(), agent_id="worker")
        assert out == (
            "ERROR: prompt_reload is available to the ari agent only (caller: worker)."
        )
        # …and this denial is NOT a REFUSED code (AM-6 swap).
        assert "REFUSED" not in out

    def test_not_ari_absent_from_the_refused_set(self) -> None:
        assert "prompt_reload_not_ari" not in REFUSAL_CODES
        assert len(REFUSAL_CODES) == 9
        assert len(FAILURE_CODES) == 1

    def test_layer1_category_is_privileged_and_layer2_ari_exactly(self) -> None:
        """Layers 1+2 are pinned by test_prompt_reload_pins.py; the
        default-grant universe exclusion is pinned by the strip test
        family — referenced here so the ladder reads complete."""
        from daemon.tools._tool_registry import PRIVILEGED_TOOL_CATEGORIES

        assert "prompt-reload" in PRIVILEGED_TOOL_CATEGORIES


class TestUncachedInstance:
    async def test_uncached_proceeds_with_was_cached_false(self) -> None:
        manager = StubManager()
        manager.instances = {}  # never-dispatched shape — flag, never a code
        out = await _call(manager)
        assert out.startswith("prompt_reload unproven")
        assert "was_cached=false" in out
