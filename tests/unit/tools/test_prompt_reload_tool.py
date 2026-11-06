"""Phase-3 unit pack — the ``prompt_reload`` TOOL surface + report
contract (D10/D14/D15/D16/D17, AM-4/AM-15).

Everything here runs against a stubbed manager with the primitive's
env/compose seams patched — the E2E dev-daemon legs (L1) belong to the
tester gate. Frozen surfaces pinned:

* the D14 golden report shape (L9 substrate);
* the AM-4 marker: EXACTLY ONE per success outcome ×5 (D25 set), token
  == structured outcome, ZERO on refusals AND FAILED paths;
* the S10-a/b honesty marks and the S10-c F8 line in the report;
* the D24 INFO line on EVERY terminal path with the pinned field
  order (T3.7);
* zero conversation-history writes (grep + behavioral);
* the kill-switch per-call semantics (D11/AM-11).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import daemon.services.prompt_refresh as pr
import daemon.tools.prompt_reload as toolmod
from daemon.loader import PromptCache
from daemon.services.prompt_refresh import (
    DIVERGENCE_LINE_UNKNOWN,
    OUTCOME_FORCED_RELOAD,
    OUTCOME_NO_CHANGE,
    OUTCOME_RELOADED,
    OUTCOME_RELOADED_DB,
    OUTCOME_UNPROVEN,
    SUCCESS_OUTCOMES,
)
from daemon.tools.prompt_reload import create_prompt_reload_tools

REPO_ROOT = Path(__file__).resolve().parents[3]
ARI_DIR = REPO_ROOT / "agents" / "ari"
MARKER_RE = re.compile(r"\[PROMPT-RELOADED outcome=(\S+) next_turn=true\]")

INFO_LINE_RE = re.compile(
    r"^prompt_reload: instance=(?P<instance>\S+) agent=(?P<agent>\S+) "
    r"outcome=(?P<outcome>no_change|reloaded_db|reloaded|forced_reload|unproven|refused) "
    r"code=(?P<code>\S+) release=(?P<release>\S+) "
    r"daemon_version=(?P<version>\S+) fingerprint_changed=(?P<fc>true|false) "
    r"hash_divergence=(?P<hd>true|false) cache_busted=(?P<cb>true|false) "
    r"current_matches_running=(?P<cmr>true|false|unknown) "
    r"persona_diff=(?P<pd>exact|unavailable|-) was_cached=(?P<wc>true|false) "
    r"manager_instances=(?P<mi>[^ ]+->[^ ]+)$"
)


class StubRepo:
    def get(self, instance_id: str):
        return SimpleNamespace(agent_id="ari", instance_metadata={})


class StubManager:
    def __init__(self):
        self.instances = {"iid-1": (object(), str(ARI_DIR))}
        self.prompt_cache = PromptCache()
        self._instance_repository = StubRepo()
        self._lifecycle_service = SimpleNamespace(
            _get_mcp_tool_names=lambda iid, stored: []
        )

    async def refresh_instance_prompt(self, instance_id: str, *, force: bool = False):
        with patch.object(pr, "_release_provenance", lambda: _prov()):
            return await pr.refresh_instance_prompt(self, instance_id, force=force)


def _prov():
    return {
        "self_env": "dev",
        "install_root": None,
        "running_release": None,
        "current_target": None,
        "current_matches_running": "unknown",
        "divergence_line": DIVERGENCE_LINE_UNKNOWN,
        "daemon_version": "0.29.0",
    }


def _compose(persona: str):
    def _fake(agent_id, agent_dir, cache, mcp, version_tag=None, is_custom_agent=False):
        key = cache._make_key(agent_id, mcp, version_tag)
        if key in cache._cache:
            prompt, tokens, _ = cache._cache[key]
            return prompt, tokens
        read_set = pr._loader_read_set(
            Path(agent_dir), json.loads((Path(agent_dir) / "meta.json").read_text())
        )
        mtimes = {k: p.stat().st_mtime for k, p in read_set.items()}
        mtimes[pr.FP_KEY] = ""
        cache.set(agent_id, persona, 1, mtimes, mcp, version_tag)
        return persona, 1

    return _fake


def _tool(manager: StubManager, agent_id: str = "ari"):
    return create_prompt_reload_tools(
        manager, current_instance_id="iid-1", agent_id=agent_id
    )[0]


async def _call(manager: StubManager, force: bool = False, agent_id: str = "ari"):
    with patch("daemon.manager.load_and_cache_prompt", _compose("P1 persona")):
        tool = _tool(manager, agent_id)
        return await tool.ainvoke({"force": force})


@pytest.fixture(autouse=True)
def _clear_baseline(monkeypatch):
    pr._BASELINE_MAP.clear()
    monkeypatch.delenv("ENSEMBLE_PROMPT_RELOAD_ENABLED", raising=False)
    yield
    pr._BASELINE_MAP.clear()


# ── AC2.1/AC3.5/AC3.6 — the marker ×5 + next-turn sentence ─────────────────


class TestMarkerContract:
    async def test_marker_on_unproven(self, caplog) -> None:
        manager = StubManager()
        with caplog.at_level(logging.INFO):
            out = await _call(manager)
        assert out.count("[PROMPT-RELOADED") == 1
        assert f"outcome={OUTCOME_UNPROVEN}" in MARKER_RE.search(out).group(0)
        assert "NEXT turn" in out

    async def test_marker_on_no_change(self) -> None:
        manager = StubManager()
        await _call(manager)
        manager.instances["iid-1"] = (object(), str(ARI_DIR))
        out = await _call(manager)
        assert out.count("[PROMPT-RELOADED") == 1
        assert f"outcome={OUTCOME_NO_CHANGE}" in out

    async def test_marker_on_forced_reload(self) -> None:
        manager = StubManager()
        await _call(manager)
        out = await _call(manager, force=True)
        assert out.count("[PROMPT-RELOADED") == 1
        assert f"outcome={OUTCOME_FORCED_RELOAD}" in out

    async def test_marker_on_reloaded(self) -> None:
        manager = StubManager()
        await _call(manager)
        key = next(iter(pr._BASELINE_MAP))
        hashes, fp = pr._BASELINE_MAP[key]
        pr._BASELINE_MAP[key] = ({**hashes, "soul.md": "e" * 64}, fp)
        out = await _call(manager)
        assert out.count("[PROMPT-RELOADED") == 1
        assert f"outcome={OUTCOME_RELOADED}" in out

    async def test_marker_on_reloaded_db(self) -> None:
        manager = StubManager()
        await _call(manager)
        key = next(iter(pr._BASELINE_MAP))
        hashes, fp = pr._BASELINE_MAP[key]
        pr._BASELINE_MAP[key] = (hashes, "d" * 64)
        out = await _call(manager)
        assert out.count("[PROMPT-RELOADED") == 1
        assert f"outcome={OUTCOME_RELOADED_DB}" in out

    async def test_marker_token_equals_structured_outcome_everywhere(self) -> None:
        """The five outcomes each render marker token == outcome (drift =
        pin failure) — single sweep over all five in one test."""
        seen: dict[str, str] = {}
        # unproven
        manager = StubManager()
        out = await _call(manager)
        seen[OUTCOME_UNPROVEN] = MARKER_RE.search(out).group(1)
        # no_change
        manager.instances["iid-1"] = (object(), str(ARI_DIR))
        out = await _call(manager)
        seen[OUTCOME_NO_CHANGE] = MARKER_RE.search(out).group(1)
        # forced
        out = await _call(manager, force=True)
        seen[OUTCOME_FORCED_RELOAD] = MARKER_RE.search(out).group(1)
        # reloaded (fresh manager+baseline tamper)
        manager2 = StubManager()
        await _call(manager2)
        key = next(iter(pr._BASELINE_MAP))
        hashes, fp = pr._BASELINE_MAP[key]
        pr._BASELINE_MAP[key] = ({**hashes, "soul.md": "e" * 64}, fp)
        out = await _call(manager2)
        seen[OUTCOME_RELOADED] = MARKER_RE.search(out).group(1)
        # reloaded_db
        pr._BASELINE_MAP[key] = (hashes, "d" * 64)
        out = await _call(manager2)
        seen[OUTCOME_RELOADED_DB] = MARKER_RE.search(out).group(1)
        assert set(seen) == set(SUCCESS_OUTCOMES)
        for outcome, token in seen.items():
            assert token == outcome

    async def test_zero_marker_on_refusals_and_failed(self) -> None:
        manager = StubManager()
        # kill-switch OFF → typed refusal, zero markers.
        with patch.dict("os.environ", {"ENSEMBLE_PROMPT_RELOAD_ENABLED": "false"}):
            out = await _call(manager)
        assert "prompt_reload REFUSED — reason=prompt_reload_disabled" in out
        assert "[PROMPT-RELOADED" not in out

        # internal FAILED path → FAILED envelope, zero markers.
        manager2 = StubManager()
        manager2.prompt_cache = None
        out = await _call(manager2)
        assert "prompt_reload FAILED — reason=prompt_reload_internal_error" in out
        assert "[PROMPT-RELOADED" not in out


# ── AC3.7 — the D24 INFO line on every terminal path ────────────────────────


class TestInfoLine:
    async def test_info_line_on_success(self, caplog) -> None:
        manager = StubManager()
        with caplog.at_level(logging.INFO):
            await _call(manager)
        lines = [r.message for r in caplog.records if r.message.startswith("prompt_reload:")]
        assert len(lines) == 1
        match = INFO_LINE_RE.match(lines[0])
        assert match, lines[0]
        assert match.group("outcome") == OUTCOME_UNPROVEN
        assert match.group("code") == "-"
        assert match.group("pd") == "unavailable"
        assert match.group("fc") == "false"  # no baseline → false

    async def test_info_line_on_refusal(self, caplog) -> None:
        manager = StubManager()
        with caplog.at_level(logging.INFO), patch.dict(
            "os.environ", {"ENSEMBLE_PROMPT_RELOAD_ENABLED": "0"}
        ):
            await _call(manager)
        lines = [r.message for r in caplog.records if r.message.startswith("prompt_reload:")]
        assert len(lines) == 1
        match = INFO_LINE_RE.match(lines[0])
        assert match.group("outcome") == "refused"
        assert match.group("code") == "prompt_reload_disabled"

    async def test_info_line_on_non_ari(self, caplog) -> None:
        manager = StubManager()
        with caplog.at_level(logging.INFO):
            out = await _call(manager, agent_id="worker")
        assert out.startswith("ERROR: prompt_reload is available to the ari agent only")
        lines = [r.message for r in caplog.records if r.message.startswith("prompt_reload:")]
        assert len(lines) == 1

    async def test_info_line_carries_no_persona_or_diff_content(self, caplog) -> None:
        manager = StubManager()
        with caplog.at_level(logging.INFO):
            await _call(manager)
        for record in caplog.records:
            if record.message.startswith("prompt_reload:"):
                assert "P1 persona" not in record.message

    async def test_fingerprint_changed_true_on_db_change(self, caplog) -> None:
        manager = StubManager()
        await _call(manager)
        key = next(iter(pr._BASELINE_MAP))
        hashes, fp = pr._BASELINE_MAP[key]
        pr._BASELINE_MAP[key] = (hashes, "d" * 64)
        caplog.clear()
        with caplog.at_level(logging.INFO):
            await _call(manager)
        line = [r.message for r in caplog.records if r.message.startswith("prompt_reload:")][0]
        assert "fingerprint_changed=true" in line
        assert "outcome=reloaded_db" in line


# ── AC3.1/AC3.3 — the golden D14 report (L9 substrate) ─────────────────────


class TestGoldenReport:
    def _result(self) -> pr.PromptRefreshResult:
        """A frozen tier-b result for the golden string."""
        return pr.PromptRefreshResult(
            outcome=OUTCOME_RELOADED,
            code=None,
            message="Reloaded: prompt files changed — recomposed and the cached graph was popped.",
            diff={
                "persona_diff": "exact",
                "lines_added": 2,
                "lines_removed": 1,
                "bytes_before": 100,
                "bytes_after": 140,
                "headings_added": ["## New Section"],
                "headings_removed": [],
                "files": [
                    {"path": "soul.md", "sha_before": "aaa…", "sha_after": "bbb…", "changed": True},
                    {"path": "rule.md", "sha_before": "ccc…", "sha_after": "ccc…", "changed": False},
                ],
            },
            provenance={
                "agent_id": "ari",
                "running_release": "v0.29.0",
                "current_matches_running": "true",
                "divergence_line": "",
                "daemon_version": "0.29.0",
                "manifest": "ok(6 checked, 0 unmanifested-skipped)",
                "db_fingerprint": "deadbeef",
                "mcp_tool_names_source": "empty",
                "was_cached": True,
                "manager_instances": "1->0",
                "db_rows": [
                    {
                        "artifact": "tools_note",
                        "class": "CORE",
                        "selected": "v3",
                        "honesty": pr.S10_A_TOOLS_NOTE_DEAD,
                    }
                ],
            },
        )

    def test_golden_shape(self) -> None:
        out = toolmod._render_report(self._result())
        assert MARKER_RE.search(out).group(1) == OUTCOME_RELOADED
        assert "prompt_reload reloaded — " in out
        assert "release: v0.29.0 (current_matches_running=true, daemon_version=0.29.0)" in out
        assert "manifest: ok(6 checked, 0 unmanifested-skipped)" in out
        assert "manager_instances: 1->0" in out
        assert "persona diff: +2/-1 lines, 100→140 bytes" in out
        assert "headings added: ## New Section" in out
        assert "files: 1 changed of 2 in the loader read set" in out
        assert "soul.md: sha256 aaa… → bbb…" in out
        # F7 honesty mark rides the db row (D15).
        assert pr.S10_A_TOOLS_NOTE_DEAD in out
        # F8 non-promise + next-turn + provider cache (D16/D17/AM-4).
        assert pr.S10_C_DISPLAY_DIVERGENCE in out
        assert pr.S10_D_NEXT_TURN in out
        assert pr.S10_E_PROVIDER_CACHE in out

    def test_tier_c_renders_unavailable_not_a_guess(self) -> None:
        result = self._result()
        result.diff = {"persona_diff": "unavailable", "files": result.diff["files"]}
        out = toolmod._render_report(result)
        assert "persona_diff=unavailable" in out
        assert "persona diff:" not in out  # no invented delta text

    def test_no_change_carries_the_file_level_wording_and_s10_j(self) -> None:
        result = self._result()
        result.outcome = OUTCOME_NO_CHANGE
        result.message = (
            "No changes to the prompt files since the last cache fill "
            "(file-level freshness — not a claim that the running closure "
            "persona is current)."
        )
        out = toolmod._render_report(result)
        assert pr.S10_J_NO_CHANGE_WORDING in out
        assert "closure persona is current" in result.message

    def test_heading_cap_triggers_and_k_more(self) -> None:
        diff = pr._persona_diff("", "\n".join(f"## H{i}" for i in range(15)))
        assert len(diff["headings_added"]) == 11
        assert diff["headings_added"][-1] == "…and 5 more"


# ── AC3.8 — canonical literals, one per S10 id, zero paraphrase families ────


class TestCanonicalLiterals:
    def test_every_s10_literal_is_a_module_constant(self) -> None:
        for name in (
            "S10_A_TOOLS_NOTE_DEAD",
            "S10_B_USER_GROWTH_NOT_IN_PROMPT",
            "S10_C_DISPLAY_DIVERGENCE",
            "S10_D_NEXT_TURN",
            "S10_E_PROVIDER_CACHE",
            "S10_F_INFLIGHT_SURVIVES",
            "S10_G_NODE_SET_HAZARD",
            "S10_H_FILE_TOCTOU",
            "S10_I_PROBE_TO_COMPOSE",
            "S10_J_NO_CHANGE_WORDING",
            "S10_K_MARKER_ERASURE",
            "S10_L_UNVERIFIED_ROUTED",
            "S10_M_OBSERVABILITY_ROUTED",
            "S10_N_COLD_START_DELIBERATE",
        ):
            value = getattr(pr, name)
            assert isinstance(value, str) and len(value) > 20, name

    def test_no_paraphrase_variants_of_the_report_literals(self) -> None:
        source = (REPO_ROOT / "daemon" / "tools" / "prompt_reload.py").read_text(
            encoding="utf-8"
        )
        # The RENDERER must reference the constants, not restate the
        # canonical literals (W5: one literal per id). The docstring /
        # _full_doc_ surfaces are allowed to mention consequences in
        # their own words — they are not the report surface.
        renderer = source.split("def _render_report", 1)[1].split("def create_prompt_reload_tools", 1)[0]
        assert "no display-parity promise" not in renderer
        assert "invalidates provider prompt caches" not in renderer
        assert "NEXT turn" not in renderer  # the constant carries it, not prose


# ── AC3.6 — zero conversation-history writes ────────────────────────────────


class TestNoHistoryWrites:
    def test_grep_no_history_mutating_calls_in_the_lane(self) -> None:
        for rel in ("daemon/tools/prompt_reload.py", "daemon/services/prompt_refresh.py"):
            source = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for banned in (
                "enqueue_message",
                "add_message",
                "inject_message",
                "SystemMessage(",
                "set_injection",
            ):
                assert banned not in source, (rel, banned)
