"""Phase-1 unit pack — the ``refresh_instance_prompt`` primitive.

The primitive is tested against a STUBBED manager (instances dict +
prompt_cache + instance repo) with the loader imported through
``daemon.manager`` (the patch seam) monkeypatched — the phase-1 gate:
validation matrix green (live/dev shapes via PURE fixtures), the
three-fixture divergence matrix (AM-5), every code typed (9 REFUSED +
1 FAILED), no-op never pops, hash/bust + repo-probe + ``reloaded_db`` +
``unproven`` legs green.

The dev-daemon E2E legs (L1) and the mutation legs (M1–M6) belong to
the TESTER gate — this pack is the pure-unit substrate it consumes.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import daemon.services.prompt_refresh as pr
from daemon.loader import PromptCache
from daemon.services.prompt_refresh import (
    CODE_AGENT_DIR_UNRESOLVED,
    CODE_AGENT_META_INVALID,
    CODE_COMPOSE_FAILED,
    CODE_DB_UNAVAILABLE,
    CODE_DIR_INVALID,
    CODE_INTERNAL_ERROR,
    CODE_MANIFEST_UNREADABLE,
    DIVERGENCE_LINE_FALSE,
    DIVERGENCE_LINE_UNKNOWN,
    FAILURE_CODES,
    OUTCOME_FORCED_RELOAD,
    OUTCOME_NO_CHANGE,
    OUTCOME_RELOADED,
    OUTCOME_RELOADED_DB,
    OUTCOME_UNPROVEN,
    REFUSAL_CODES,
    SUCCESS_OUTCOMES,
    _loader_read_set,
    refresh_instance_prompt,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
ARI_DIR = REPO_ROOT / "agents" / "ari"


# ── Fixtures ────────────────────────────────────────────────────────────────


class StubInstanceRepo:
    def __init__(self, agent_id: str = "ari", mcp: list[str] | None = None):
        self.row = SimpleNamespace(
            agent_id=agent_id,
            instance_metadata={"mcp_tool_names": mcp} if mcp else {},
        )

    def get(self, instance_id: str):
        return self.row


class StubManager:
    """The stubbed manager surface the primitive reads."""

    def __init__(self, mcp: list[str] | None = None, agent_id: str = "ari"):
        self.instances = {"iid-1": (object(), str(ARI_DIR))}
        self.prompt_cache = PromptCache()
        self._instance_repository = StubInstanceRepo(agent_id, mcp)
        self._lifecycle_service = SimpleNamespace(
            _get_mcp_tool_names=lambda iid, stored: list(stored or mcp or [])
        )


def _dev_provenance(self_env: str = "dev", matches: str = "unknown"):
    """Pure-fixture release provenance — no upgrade_tools privates."""
    line = {
        "true": "",
        "false": DIVERGENCE_LINE_FALSE,
        "unknown": DIVERGENCE_LINE_UNKNOWN,
    }[matches]
    return {
        "self_env": self_env,
        "install_root": "/tmp/nonexistent-install" if self_env != "dev" else None,
        "running_release": None,
        "current_target": None,
        "current_matches_running": matches,
        "divergence_line": line,
        "daemon_version": "0.29.0",
    }


def _compose_stub(persona: str = "P1 persona"):
    """A load_and_cache_prompt stand-in writing a cache entry the way the
    loader does: (persona, tokens, mtimes)."""

    def _fake(agent_id, agent_dir, cache, mcp_names, version_tag=None, is_custom_agent=False):
        key = cache._make_key(agent_id, mcp_names, version_tag)
        if key in cache._cache:
            prompt, tokens, _mt = cache._cache[key]
            return prompt, tokens
        mtimes = {"soul.md": 1.0, "__prompt_versions_fingerprint__": ""}
        cache.set(agent_id, persona, 1, mtimes, mcp_names, version_tag)
        return persona, 1

    return _fake


@pytest.fixture(autouse=True)
def _clear_baseline():
    pr._BASELINE_MAP.clear()
    yield
    pr._BASELINE_MAP.clear()


async def _run(
    manager: StubManager,
    *,
    persona: str = "P1 persona",
    provenance=None,
    force: bool = False,
):
    with patch.object(pr, "_release_provenance", lambda: provenance or _dev_provenance()), patch(
        "daemon.manager.load_and_cache_prompt", _compose_stub(persona)
    ):
        return await refresh_instance_prompt(manager, "iid-1", force=force)


# ── AC1.1 — result shape frozen; the 10-code set literally enumerated ──────


class TestResultShapeAndVocabulary:
    def test_success_outcomes_are_the_d25_five(self) -> None:
        assert SUCCESS_OUTCOMES == {
            "no_change",
            "reloaded",
            "reloaded_db",
            "forced_reload",
            "unproven",
        }

    def test_refused_vocabulary_is_nine_plus_one_failed(self) -> None:
        assert len(REFUSAL_CODES) == 9
        assert FAILURE_CODES == {"prompt_reload_internal_error"}
        assert not (REFUSAL_CODES & FAILURE_CODES)
        # The one-for-one swap (AM-6): not_ari absent, manifest_unreadable present.
        assert "prompt_reload_not_ari" not in REFUSAL_CODES
        assert CODE_MANIFEST_UNREADABLE in REFUSAL_CODES

    def test_every_code_carries_its_envelope_kind(self) -> None:
        for code in REFUSAL_CODES:
            assert pr.CODE_ENVELOPES[code] == "REFUSED"
        for code in FAILURE_CODES:
            assert pr.CODE_ENVELOPES[code] == "FAILED"

    def test_info_enum_is_the_six_value_superset(self) -> None:
        # D25 set-distinction: INFO enum ⊃ marker set, refused present.
        assert set(pr.INFO_OUTCOMES) == SUCCESS_OUTCOMES | {"refused"}


# ── AC1.4 — the loader-read-set pin (AM-1: derived, never a glob) ───────────


class TestLoaderReadSet:
    def test_own_prompt_files_constant_equals_the_loaders_literal(self) -> None:
        source = (REPO_ROOT / "daemon" / "loader.py").read_text(encoding="utf-8")
        assert 'prompt_files = ["soul.md", "skill.md", "workflow.md", "rule.md", "memory.md"]' in source
        assert pr._OWN_PROMPT_FILES == ("soul.md", "skill.md", "workflow.md", "rule.md", "memory.md")

    def test_derived_manifest_relpaths_EQUAL_the_loader_key_mapping(self, tmp_path: Path) -> None:
        """C1.2/A3 (leader ruling): the pin is an EQUALITY, not an
        inclusion — the C1 defect (shared persona files keyed as
        ``<agent>/<name>`` → unmanifested → skipped unverified) was
        exactly what an inclusion-based pin cannot see.

        A controlled fixture agent dir makes the expected set fully
        enumerable: own artifacts + meta + tools_note + the three shared
        basenames (declared ONLY as ``_prompt_system/<name>``) + innate
        skills + memories. The derivation must produce EXACTLY this set.
        """
        agent_dir = tmp_path / "agents" / "fixture-ari"
        agent_dir.mkdir(parents=True)
        (agent_dir / "soul.md").write_text("s", encoding="utf-8")
        (agent_dir / "rule.md").write_text("r", encoding="utf-8")
        (agent_dir / "tools_note.md").write_text("t", encoding="utf-8")
        (agent_dir / "meta.json").write_text(
            json.dumps({"innate_skills": ["dynamic-skill"]}), encoding="utf-8"
        )
        (agent_dir / "memories").mkdir()
        (agent_dir / "memories" / "note.md").write_text("m", encoding="utf-8")
        innate = tmp_path / "agents" / "_prompt_system" / "innate-skills" / "dynamic-skill"
        innate.mkdir(parents=True)
        (innate / "skill.md").write_text("sk", encoding="utf-8")

        meta = json.loads((agent_dir / "meta.json").read_text(encoding="utf-8"))
        read_set = _loader_read_set(agent_dir, meta)
        relpaths = {pr._manifest_relpath(agent_dir.name, key) for key in read_set}

        expected = {
            "fixture-ari/soul.md",
            "fixture-ari/rule.md",
            "fixture-ari/tools_note.md",
            "fixture-ari/meta.json",
            "fixture-ari/memories/note.md",
            # The three SHARED files — agents/-relative under _prompt_system,
            # NEVER under the agent dir (the C1 mapping).
            "_prompt_system/project-experience.md",
            "_prompt_system/knowledge.md",
            "_prompt_system/innate-skills/dynamic-skill/skill.md",
        }
        assert relpaths == expected, relpaths ^ expected
        # And no shared basename may ever be requested under the agent dir.
        assert not any(
            rp.endswith(("fixture-ari/knowledge.md", "fixture-ari/project-experience.md"))
            for rp in relpaths
        )

    def test_no_force_explore_selects_the_other_knowledge_file(self) -> None:
        meta = {"no_force_explore": True}
        keys = set(_loader_read_set(ARI_DIR, meta))
        assert "knowledge_no_force_explore.md" in keys
        assert "knowledge.md" not in keys
        assert pr._manifest_relpath("ari", "knowledge_no_force_explore.md") == (
            "_prompt_system/knowledge_no_force_explore.md"
        )


# ── AC1.2 — three-fixture divergence matrix (AM-5: NO raise, NO refusal) ────


class TestDivergenceMatrix:
    @pytest.mark.parametrize(
        ("matches", "line"),
        [
            ("true", ""),
            ("false", DIVERGENCE_LINE_FALSE),
            ("unknown", DIVERGENCE_LINE_UNKNOWN),
        ],
    )
    async def test_matrix_never_refuses(self, matches, line) -> None:
        manager = StubManager()
        result = await _run(manager, provenance=_dev_provenance("dev", matches))
        assert result.ok, result.provenance
        assert result.provenance["current_matches_running"] == matches
        assert result.provenance["divergence_line"] == line

    def test_the_two_disclosure_lines_are_distinct(self) -> None:
        assert DIVERGENCE_LINE_FALSE != DIVERGENCE_LINE_UNKNOWN
        assert DIVERGENCE_LINE_FALSE and DIVERGENCE_LINE_UNKNOWN


# ── AC1.3 — validation matrix (pure fixtures) ───────────────────────────────


class TestValidationMatrix:
    async def test_dev_repo_path_passes(self) -> None:
        result = await _run(StubManager())
        assert result.ok
        assert result.provenance["manifest"] == "absent(dev)"

    async def test_dev_foreign_path_refused_typed(self) -> None:
        import daemon.registry as registry_mod

        manager = StubManager()
        with patch.object(
            pr, "_release_provenance", lambda: _dev_provenance()
        ), patch("daemon.manager.load_and_cache_prompt", _compose_stub()), patch.object(
            registry_mod,
            "get_registry",
            lambda: SimpleNamespace(
                get_resolved=lambda aid: SimpleNamespace(
                    path=Path("/tmp/foreign-agents/ari"), version_tag=None
                ),
                _agents_dir=REPO_ROOT / "agents",
            ),
        ):
            result = await refresh_instance_prompt(manager, "iid-1")
        assert result.code == CODE_DIR_INVALID

    async def test_live_shape_requires_releases_containment(self) -> None:
        import daemon.registry as registry_mod

        manager = StubManager()
        live_prov = _dev_provenance("live")
        live_prov["install_root"] = str(REPO_ROOT.parent / "nonexistent-install")
        with patch.object(pr, "_release_provenance", lambda: live_prov), patch(
            "daemon.manager.load_and_cache_prompt", _compose_stub()
        ), patch.object(
            registry_mod,
            "get_registry",
            lambda: SimpleNamespace(
                get_resolved=lambda aid: SimpleNamespace(
                    path=Path("/elsewhere/agents/ari"), version_tag=None
                ),
                _agents_dir=REPO_ROOT / "agents",
            ),
        ):
            result = await refresh_instance_prompt(manager, "iid-1")
        assert result.code == CODE_DIR_INVALID
        assert "releases" in result.message

    async def test_marker_unresolvable_is_dir_invalid_with_marker_message(self) -> None:
        manager = StubManager()
        none_env = _dev_provenance("dev")
        none_env["self_env"] = None
        with patch.object(pr, "_release_provenance", lambda: none_env), patch(
            "daemon.manager.load_and_cache_prompt", _compose_stub()
        ):
            result = await refresh_instance_prompt(manager, "iid-1")
        assert result.code == CODE_DIR_INVALID
        assert "self-env marker" in result.message


# ── AC1.6 — tuple-decision legs (AM-8 + Rev-3 B1/B4) ────────────────────────


class TestTupleDecision:
    async def test_cold_process_no_baseline_is_unproven_and_pops(self) -> None:
        manager = StubManager()
        result = await _run(manager)
        assert result.outcome == OUTCOME_UNPROVEN
        assert "iid-1" not in manager.instances  # force-like pop
        assert result.diff["persona_diff"] == "unavailable"
        assert result.provenance["baseline_present"] is False

    async def test_baseline_equal_is_no_change_and_never_pops(self) -> None:
        manager = StubManager()
        await _run(manager)  # seeds the baseline (unproven → pops)
        manager.instances["iid-1"] = (object(), str(ARI_DIR))  # re-present
        result = await _run(manager)
        assert result.outcome == OUTCOME_NO_CHANGE
        assert "iid-1" in manager.instances  # L2 assertion: no eviction

    async def test_files_changed_is_reloaded_and_pops(self) -> None:
        manager = StubManager()
        await _run(manager, persona="P1")
        # Same files, different persona → hash-equal but persona differs is
        # impossible against the real loader; here simulate the file change
        # by clearing only the baseline's hash for one file.
        key = next(iter(pr._BASELINE_MAP))
        hashes, fp = pr._BASELINE_MAP[key]
        hashes["soul.md"] = "0" * 64
        result = await _run(manager, persona="P1")
        assert result.outcome == OUTCOME_RELOADED
        assert "iid-1" not in manager.instances

    async def test_db_only_change_is_reloaded_db_and_pops(self) -> None:
        manager = StubManager()
        await _run(manager)
        key = next(iter(pr._BASELINE_MAP))
        hashes, fp = pr._BASELINE_MAP[key]
        pr._BASELINE_MAP[key] = (hashes, "dead" * 16)  # different db operand
        result = await _run(manager)
        assert result.outcome == OUTCOME_RELOADED_DB
        assert "iid-1" not in manager.instances

    async def test_force_on_unchanged_is_forced_reload_and_pops(self) -> None:
        manager = StubManager()
        await _run(manager)
        result = await _run(manager, force=True)
        assert result.outcome == OUTCOME_FORCED_RELOAD
        assert "iid-1" not in manager.instances

    async def test_forced_pop_also_writes_the_baseline(self) -> None:
        # Advisory 2 — else every post-force call returns unproven.
        manager = StubManager()
        await _run(manager)
        await _run(manager, force=True)
        result = await _run(manager)  # no force this time
        assert result.outcome == OUTCOME_NO_CHANGE

    async def test_baseline_written_only_by_the_primitive(self) -> None:
        manager = StubManager()
        await _run(manager)
        assert len(pr._BASELINE_MAP) == 1

    def test_baseline_map_is_bounded_and_fifo_evicted(self) -> None:
        """N6: the baseline map's bound is pinned (production-only refs
        otherwise) — 256 entries, FIFO eviction of the OLDEST key."""
        assert pr._BASELINE_MAX_ENTRIES == 256
        pr._BASELINE_MAP.clear()
        for i in range(pr._BASELINE_MAX_ENTRIES + 2):
            pr._record_baseline(("ari", f"tag{i}", ()), {"soul.md": "h"}, "")
        assert len(pr._BASELINE_MAP) == pr._BASELINE_MAX_ENTRIES
        # The two oldest keys were evicted; the newest survives.
        assert ("ari", "tag0", ()) not in pr._BASELINE_MAP
        assert ("ari", "tag1", ()) not in pr._BASELINE_MAP
        newest = f"tag{pr._BASELINE_MAX_ENTRIES + 1}"
        assert ("ari", newest, ()) in pr._BASELINE_MAP
        # No-promote (N6 commission): re-recording an EXISTING key must
        # NOT move it to the FIFO back — an LRU-style container would
        # silently pass the bound/eviction asserts above.
        head = ("ari", "tag2", ())
        assert next(iter(pr._BASELINE_MAP)) == head  # the oldest is the head
        pr._record_baseline(head, {"soul.md": "h"}, "")
        assert next(iter(pr._BASELINE_MAP)) == head  # unchanged — FIFO, not LRU
        pr._record_baseline(("ari", "fresh", ()), {"soul.md": "h"}, "")
        assert head not in pr._BASELINE_MAP  # evicted oldest-first despite the re-record


# ── AM-8 — the stale-cache bust (hash/mtime divergence) ─────────────────────


class TestStaleCacheBust:
    async def test_hash_mtime_divergence_busts_before_composing(self) -> None:
        manager = StubManager()
        # Seed the baseline with the CURRENT files (call 1 fills).
        await _run(manager, persona="P_old")
        manager.instances["iid-1"] = (object(), str(ARI_DIR))
        key = next(iter(pr._BASELINE_MAP))
        hashes, fp = pr._BASELINE_MAP[key]

        # Simulate an mtime-PRESERVING tamper: file hashes differ from the
        # baseline while the cached entry's stored mtimes still match the
        # files — the loader's own mtime check would HIT and serve stale.
        tampered = dict(hashes)
        tampered["soul.md"] = "e" * 64
        pr._BASELINE_MAP[key] = (tampered, fp)
        read_set = pr._loader_read_set(ARI_DIR, json.loads((ARI_DIR / "meta.json").read_text()))
        current_mtimes = {k: p.stat().st_mtime for k, p in read_set.items()}
        cache_key = manager.prompt_cache._make_key("ari", [], None)
        manager.prompt_cache._cache[cache_key] = ("P_old", 1, {**current_mtimes, pr.FP_KEY: ""})

        order: list[str] = []

        def _watching_compose(agent_id, agent_dir, cache, mcp, version_tag=None, is_custom_agent=False):
            order.append("compose")
            cache.set(agent_id, "P_new", 1, {**current_mtimes, pr.FP_KEY: ""}, mcp, version_tag)
            return "P_new", 1

        orig_invalidate = manager.prompt_cache.invalidate

        def _watching_invalidate(*args, **kwargs):
            order.append("invalidate")
            return orig_invalidate(*args, **kwargs)

        manager.prompt_cache.invalidate = _watching_invalidate  # type: ignore[method-assign]

        with patch.object(pr, "_release_provenance", lambda: _dev_provenance()), patch(
            "daemon.manager.load_and_cache_prompt", _watching_compose
        ):
            result = await refresh_instance_prompt(manager, "iid-1")

        assert result.outcome == OUTCOME_RELOADED
        assert order == ["invalidate", "compose"]  # bust fires BEFORE composing
        assert result.provenance["cache_busted"] is True
        assert result.provenance["hash_mtime_divergence"] is True
        assert "iid-1" not in manager.instances

    async def test_mtime_visible_change_needs_no_bust(self) -> None:
        manager = StubManager()
        await _run(manager, persona="P_old")
        key = next(iter(pr._BASELINE_MAP))
        hashes, fp = pr._BASELINE_MAP[key]
        pr._BASELINE_MAP[key] = ({**hashes, "soul.md": "e" * 64}, fp)
        # Cached entry's stored mtimes are STALE (a real edit bumps them).
        cache_key = manager.prompt_cache._make_key("ari", [], None)
        manager.prompt_cache._cache[cache_key] = ("P_old", 1, {"soul.md": 1.0, pr.FP_KEY: ""})
        manager.instances["iid-1"] = (object(), str(ARI_DIR))

        result = await _run(manager, persona="P_new")
        assert result.outcome == OUTCOME_RELOADED
        assert result.provenance["cache_busted"] is False  # loader self-heals


# ── AM-13/D23 — the claim-guard (tier-c on skew; M5 substrate) ───────────────


class TestClaimGuard:
    async def test_concurrent_refill_skew_downgrades_to_tier_c(self) -> None:
        manager = StubManager()
        await _run(manager, persona="P_old")
        manager.instances["iid-1"] = (object(), str(ARI_DIR))
        key = next(iter(pr._BASELINE_MAP))
        hashes, fp = pr._BASELINE_MAP[key]
        pr._BASELINE_MAP[key] = ({**hashes, "soul.md": "e" * 64}, fp)

        def _racing_compose(agent_id, agent_dir, cache, mcp, version_tag=None, is_custom_agent=False):
            # A third party refills the entry MID-CALL with foreign content.
            cache.set(agent_id, "P_FOREIGN", 1, {"soul.md": 9.0}, mcp, version_tag)
            return "P_new", 1

        with patch.object(pr, "_release_provenance", lambda: _dev_provenance()), patch(
            "daemon.manager.load_and_cache_prompt", _racing_compose
        ):
            result = await refresh_instance_prompt(manager, "iid-1")

        assert result.outcome == OUTCOME_RELOADED
        assert result.diff["persona_diff"] == "unavailable"  # tier-c — no claim
        assert result.provenance["claim_guard"] == "skew-or-absent"

    async def test_own_refill_keeps_tier_b_exact(self) -> None:
        manager = StubManager()
        await _run(manager, persona="P_old")
        manager.instances["iid-1"] = (object(), str(ARI_DIR))
        key = next(iter(pr._BASELINE_MAP))
        hashes, fp = pr._BASELINE_MAP[key]
        pr._BASELINE_MAP[key] = ({**hashes, "soul.md": "e" * 64}, fp)

        def _normal_compose(agent_id, agent_dir, cache, mcp, version_tag=None, is_custom_agent=False):
            cache.set(agent_id, "P_new", 1, {"soul.md": 9.0}, mcp, version_tag)
            return "P_new", 1

        with patch.object(pr, "_release_provenance", lambda: _dev_provenance()), patch(
            "daemon.manager.load_and_cache_prompt", _normal_compose
        ):
            result = await refresh_instance_prompt(manager, "iid-1")

        assert result.outcome == OUTCOME_RELOADED
        assert result.diff["persona_diff"] == "exact"
        assert result.provenance["claim_guard"] == "held"

    async def test_mcp_key_parity_mismatch_downgrades_to_tier_c(self) -> None:
        manager = StubManager(mcp=["srv_a"])
        # The persona-holding entry was keyed under a DIFFERENT mcp set.
        manager.prompt_cache.set("ari", "P_old", 1, {"soul.md": 1.0}, ["srv_b"], None)
        manager.prompt_cache.set("ari", "P_other", 1, {"soul.md": 1.0}, ["srv_a"], None)
        manager.instances["iid-1"] = (object(), str(ARI_DIR))

        def _normal_compose(agent_id, agent_dir, cache, mcp, version_tag=None, is_custom_agent=False):
            cache.set(agent_id, "P_new", 1, {"soul.md": 9.0}, mcp, version_tag)
            return "P_new", 1

        with patch.object(pr, "_release_provenance", lambda: _dev_provenance()), patch(
            "daemon.manager.load_and_cache_prompt", _normal_compose
        ):
            result = await refresh_instance_prompt(manager, "iid-1")

        assert result.ok
        assert result.provenance["parity_ok"] is False
        assert result.diff["persona_diff"] == "unavailable"

    async def test_w1_warm_cache_cold_process_is_unproven_tier_c_never_exact(self) -> None:
        """W1 (leader ruling): the ORDINARY first production call — a cold
        process with a WARM shared cache — must be ``unproven`` +
        ``persona_diff=unavailable``; a no-baseline call NEVER claims an
        exact delta."""
        manager = StubManager()
        # Warm cache, no baseline record.
        cache_key = manager.prompt_cache._make_key("ari", [], None)
        manager.prompt_cache._cache[cache_key] = (
            "P_old", 1, {"soul.md": 1.0, pr.FP_KEY: ""},
        )
        manager.instances["iid-1"] = (object(), str(ARI_DIR))

        result = await _run(manager, persona="P1 persona")
        assert result.outcome == OUTCOME_UNPROVEN
        assert result.diff["persona_diff"] == "unavailable"
        assert "no persona delta is claimed" in result.message

    async def test_n1_cold_start_busts_a_live_entry_so_the_compose_reads_disk(self) -> None:
        """N1.1 (leader-ratified Candidate A — REWRITES the old W2
        ``invalidations == []`` pin, which encoded the defect): with no
        baseline the entry's bytes are UNPROVABLE (an mtime-preserving
        edit is invisible to the loader's mtime-only cache-hit), so the
        cold path MUST bust the live entry — ``cache_busted`` is a
        correctness mechanism, not only a report flag. ``hash_mtime_
        divergence`` stays False (no fabricated divergence — W1)."""
        manager = StubManager()
        read_set = pr._loader_read_set(
            ARI_DIR, json.loads((ARI_DIR / "meta.json").read_text())
        )
        current_mtimes = {k: p.stat().st_mtime for k, p in read_set.items()}
        cache_key = manager.prompt_cache._make_key("ari", [], None)
        manager.prompt_cache._cache[cache_key] = (
            "P_old", 1, {**current_mtimes, pr.FP_KEY: ""},
        )
        invalidations: list[tuple] = []
        orig = manager.prompt_cache.invalidate

        def _watch(*args, **kwargs):
            invalidations.append(args)
            return orig(*args, **kwargs)

        manager.prompt_cache.invalidate = _watch  # type: ignore[method-assign]
        manager.instances["iid-1"] = (object(), str(ARI_DIR))

        result = await _run(manager, persona="P_old")
        assert result.outcome == OUTCOME_UNPROVEN
        assert len(invalidations) == 1  # the cold path MUST bust
        assert result.provenance["cache_busted"] is True
        assert result.provenance["hash_mtime_divergence"] is False  # no fabrication
        assert (
            result.provenance["cache_busted_reason"]
            == "cold_process_cannot_prove_entry_bytes"
        )

    async def test_n1_cold_regression_served_persona_AND_baseline_match_disk(self) -> None:
        """N1.5 deeper regression: cold + a live snapshot entry + an
        mtime-PRESERVING edit ⇒ the served persona must equal DISK truth
        AND the recorded baseline must describe that same compose's
        inputs (the N1 poisoning: recording disk hashes beside a
        cache-served stale persona makes every later call report
        `no_change` while serving stale)."""
        import hashlib as _hl

        manager = StubManager()
        read_set = pr._loader_read_set(
            ARI_DIR, json.loads((ARI_DIR / "meta.json").read_text())
        )
        # The "mtime-preserving edit": the entry carries the CURRENT
        # mtimes (so the loader's cache-hit would fire) but stale bytes.
        preserved_mtimes = {k: p.stat().st_mtime for k, p in read_set.items()}
        cache_key = manager.prompt_cache._make_key("ari", [], None)
        manager.prompt_cache._cache[cache_key] = (
            "STALE_PRE_EDIT_PERSONA", 1, {**preserved_mtimes, pr.FP_KEY: ""},
        )
        manager.instances["iid-1"] = (object(), str(ARI_DIR))

        def _disk_digest() -> str:
            rs = pr._loader_read_set(
                ARI_DIR, json.loads((ARI_DIR / "meta.json").read_text())
            )
            h = _hl.sha256()
            for key in sorted(rs):
                h.update(pr._file_sha256(rs[key]).encode())
            return "DISK:" + h.hexdigest()

        def _compose(agent_id, agent_dir, cache, mcp, version_tag=None, is_custom_agent=False):
            key = cache._make_key(agent_id, mcp, version_tag)
            if key in cache._cache:  # loader cache-hit shape (mtime equality)
                prompt, tokens, _ = cache._cache[key]
                return prompt, tokens
            persona = _disk_digest()  # the compose reads disk truth
            cache.set(agent_id, persona, 1, {**preserved_mtimes, pr.FP_KEY: ""}, mcp, version_tag)
            return persona, 1

        with patch.object(pr, "_release_provenance", lambda: _dev_provenance()), patch(
            "daemon.manager.load_and_cache_prompt", _compose
        ):
            await refresh_instance_prompt(manager, "iid-1")

        expected_disk = _disk_digest()
        served = manager.prompt_cache._cache[cache_key][0]
        assert served == expected_disk, (
            "the served persona must equal DISK truth — a cache-served "
            f"stale persona is the N1 defect (served={served[:32]}…)"
        )
        # The recorded baseline describes the SAME compose's inputs: the
        # per-file disk hashes.
        recorded_hashes, _fp = pr._BASELINE_MAP[("ari", None, ())]
        actual_disk_hashes = {k: pr._file_sha256(p) for k, p in read_set.items()}
        assert recorded_hashes == actual_disk_hashes, (
            "the baseline must describe the inputs of the compose that "
            "produced the served persona — never a separate later disk read"
        )


# ── AC1.7 — pop semantics (P3/AM-7/D22) ─────────────────────────────────────


class TestPopSeam:
    async def test_double_pop_is_a_no_op(self) -> None:
        manager = StubManager()
        await _run(manager)
        # First call popped; a second unproven/reload path pops an absent id.
        assert manager.instances.pop("iid-1", None) is None

    async def test_manager_instances_before_after_in_envelope(self) -> None:
        manager = StubManager()
        result = await _run(manager)
        assert result.provenance["manager_instances"] == "1->0"

    async def test_no_guard_flag_or_inflight_check_exists(self) -> None:
        source = (REPO_ROOT / "daemon" / "services" / "prompt_refresh.py").read_text(
            encoding="utf-8"
        )
        assert "in_flight" not in source.replace("in-flight", "")
        assert "restore_in_flight" not in source
        assert "_seam_guard" not in source

    async def test_forbidden_mechanisms_absent(self) -> None:
        source = (REPO_ROOT / "daemon" / "services" / "prompt_refresh.py").read_text(
            encoding="utf-8"
        )
        assert "_release_cached_instance" not in source  # P4
        assert "build_instance_graph" not in source  # P5 — never builds a graph


# ── AC1.5 — repo probe fail-closed; composition through the seam ────────────


class TestRepoProbe:
    async def test_raising_repo_refuses_db_unavailable_without_composition(self) -> None:
        manager = StubManager()
        calls = {"composed": False}

        def _boom(agent_id, agent_dir, cache, mcp, version_tag=None, is_custom_agent=False):
            calls["composed"] = True
            return "should not happen", 0

        class RaisingRepo:
            def get_effective_rows(self, agent_id, version_tag):
                raise RuntimeError("connection refused")

        with patch.object(pr, "_release_provenance", lambda: _dev_provenance()), patch(
            "daemon.manager.load_and_cache_prompt", _boom
        ), patch.object(pr, "_get_prompt_version_repo", lambda: RaisingRepo()):
            result = await refresh_instance_prompt(manager, "iid-1")
        assert result.code == CODE_DB_UNAVAILABLE
        assert "prompt-versions" in result.message
        assert calls["composed"] is False  # NO composition (AM-9)

    async def test_absent_repo_proceeds_with_absent_fingerprint(self) -> None:
        manager = StubManager()
        with patch.object(pr, "_release_provenance", lambda: _dev_provenance()), patch(
            "daemon.manager.load_and_cache_prompt", _compose_stub()
        ), patch.object(pr, "_get_prompt_version_repo", lambda: None):
            result = await refresh_instance_prompt(manager, "iid-1")
        assert result.ok
        assert result.provenance["db_fingerprint"] == "absent"

    async def test_module_never_reads_md_files_itself(self) -> None:
        # R2: no own .md reading for composition — the read-set paths feed
        # hashing/verification only; the compose import is the seam.
        source = (REPO_ROOT / "daemon" / "services" / "prompt_refresh.py").read_text(
            encoding="utf-8"
        )
        assert "load_agent_prompts" not in source
        assert "compose_system_prompt" not in source


# ── Remaining typed codes (AC1.6/L3 substrate) ──────────────────────────────


class TestTypedRefusals:
    async def test_registry_miss_is_agent_dir_unresolved(self) -> None:
        import daemon.registry as registry_mod

        manager = StubManager(agent_id="ghost-agent")
        with patch.object(pr, "_release_provenance", lambda: _dev_provenance()), patch(
            "daemon.manager.load_and_cache_prompt", _compose_stub()
        ), patch.object(
            registry_mod, "get_registry", lambda: SimpleNamespace(get_resolved=lambda aid: None)
        ):
            result = await refresh_instance_prompt(manager, "iid-1")
        assert result.code == CODE_AGENT_DIR_UNRESOLVED

    async def test_corrupt_meta_is_agent_meta_invalid(self, tmp_path: Path) -> None:
        import daemon.registry as registry_mod

        agent_dir = tmp_path / "agents" / "ari"
        agent_dir.mkdir(parents=True)
        (agent_dir / "meta.json").write_text("{ not json", encoding="utf-8")

        manager = StubManager()
        with patch.object(
            pr, "_release_provenance", lambda: _dev_provenance()
        ), patch("daemon.manager.load_and_cache_prompt", _compose_stub()), patch.object(
            registry_mod,
            "get_registry",
            lambda: SimpleNamespace(
                get_resolved=lambda aid: SimpleNamespace(path=agent_dir, version_tag=None),
                _agents_dir=tmp_path / "agents",
            ),
        ):
            result = await refresh_instance_prompt(manager, "iid-1")
        assert result.code == CODE_AGENT_META_INVALID

    async def test_compose_raise_is_compose_failed(self) -> None:
        manager = StubManager()

        def _raise(*a, **k):
            raise RuntimeError("boom")

        with patch.object(pr, "_release_provenance", lambda: _dev_provenance()), patch(
            "daemon.manager.load_and_cache_prompt", _raise
        ):
            result = await refresh_instance_prompt(manager, "iid-1")
        assert result.code == CODE_COMPOSE_FAILED

    async def test_unexpected_error_is_internal_error_failed_envelope(self) -> None:
        manager = StubManager()
        manager.prompt_cache = None  # mid-call structural surprise
        with patch.object(pr, "_release_provenance", lambda: _dev_provenance()), patch(
            "daemon.manager.load_and_cache_prompt", _compose_stub()
        ):
            result = await refresh_instance_prompt(manager, "iid-1")
        assert result.code == CODE_INTERNAL_ERROR
        assert result.code in FAILURE_CODES  # the FAILED-envelope leg
