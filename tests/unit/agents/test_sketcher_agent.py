"""Sketcher agent tests (od-generate-agent-lane Stage 2).

Pins the new `agents/sketcher/` generation-worker shape and the Stage 2
wiring, per the Stage 2 dispatch:

1. **Meta shape** — vision pin, closed tools.allow fence (od.* + image +
   dynamic-skill; NO bash/filesystem/write categories, no tools.deny),
   default_queue, recursion multiplier 12, watchover timeout >= 60,
   skill_injection true, empty team (leaf).
2. **Prompt-guide grep gates** — zero system-internals tokens and zero
   ``.md`` path tokens in the prompt prose (mirrors the closure grep the
   prompt-writing guide §1/§3 mandates); <=7 Cardinals in rule.md; tone
   block present in soul.md; §7 exemption — an empty-team agent carries
   NO report-scrutiny guidance.
3. **Skill registration consistency** — skill-set entries <-> skills-
   template files <-> vendored plugin skills (registry loads with zero
   refusals); consumers name sketcher; vendored_references resolve inside
   the plugin tree; NO vendored prompt text duplicated into the skill
   body (V8 references-not-text).
4. **Fail-closed glob behavior** — anonymous consumers, tree traversal,
   and tag-pin divergence are refused at load.
5. **Designer wiring + critic pipeline** — team_members carries
   sketcher; the (d) report-scrutiny guidance survives. Post-rework
   contract (designer-critic-orchestration): sketcher is the SOLE OD
   generation lane (dual-run retired), designer prose holds zero od.*
   tokens, the critic pipeline pins survive (pinned_spec_sha mandatory,
   anchored verdict parse), and parity-runs.jsonl validates under schema
   v2 (lane = sketcher | critic).
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from daemon.plugin_subsystem.plugin_registry import load_registry
from daemon.plugin_subsystem.plugin_skill import PluginSkillRefusal, read_skill_file
from daemon.services.skill_seed_service import parse_skill_set_file

REPO_ROOT = Path(__file__).resolve().parents[3]
SKETCHER_DIR = REPO_ROOT / "agents" / "sketcher"
DESIGNER_DIR = REPO_ROOT / "agents" / "designer"
PLUGIN_DIR = REPO_ROOT / "plugins" / "opendesign"
PARITY_DIR = REPO_ROOT / ".agents" / "shared" / "planning" / "od-generate-agent-lane"

PROMPT_SURFACE = ("soul.md", "rule.md", "workflow.md", "tools_note.md")

# Guide §1 forbidden system-internals tokens (prompt prose must carry none).
SYSTEM_INTERNAL_TOKENS = (
    "meta.json",
    "tools.allow",
    "tools.deny",
    "daemon/",
    "_tool_registry",
    "skill-set.yaml",
    "innate_skills",
    "seed_all",
    "agent_id=",
    "default_agent_versions",
)

# The exact brief directive: the od.* names verified against the tool
# registry + designer's allow precedent; "image" is the category carrying
# explain_image / image_save / image_list / image_get (+ compare_images
# facade lives under design, NOT image).
EXPECTED_ALLOW = {
    "od.generate",
    "od.compose_brief",
    "od.lint",
    "od.save",
    "image",
    "dynamic-skill",
}

# Categories that must NEVER appear in the sketcher allow (the closed
# allow IS the fence — no shell, no filesystem, no write surface).
FORBIDDEN_ALLOW = {
    "bash",
    "proc",
    "filesystem",
    "instance",
    "service",
    "write",
}

# The truncation class (one bounded retry) vs the overflow class (zero).
TRUNCATION_CODES = ("truncation_detected", "missing_artifact_marker")
OVERFLOW_CODES = ("upstream_bad_request", "context_length_exceeded")

# Leader-set provisional pilot gates (stage2-addendum.md, verbatim tokens).
ADDENDUM_GATE_TOKENS = (
    "N >= 10 pages",
    "marker_pass >= 95%",
    "within 5pp of direct",
    "truncated <= direct + 5pp",
    "median latency <= 1.5x direct",
    "tokens/page <= 1.3x direct",
)

def _read(*parts: str) -> str:
    return (SKETCHER_DIR.joinpath(*parts)).read_text(encoding="utf-8")


def _meta() -> dict:
    return json.loads((SKETCHER_DIR / "meta.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. Meta shape
# ---------------------------------------------------------------------------


class TestSketcherMetaShape:
    def test_vision_pin(self):
        assert _meta()["llm_model"] == "vision"

    def test_skill_injection_enabled(self):
        assert _meta()["skill_injection"] is True

    def test_innate_skills_dynamic_skill_only(self):
        assert _meta()["innate_skills"] == ["dynamic-skill"]

    def test_allow_set_exact(self):
        allow = set(_meta()["tools"]["allow"])
        assert allow == EXPECTED_ALLOW

    def test_closed_allow_is_the_fence(self):
        """No shell/filesystem/write/instance surface — and no deny list
        (the closed allow is the fence; a deny list would be dead weight)."""
        allow = set(_meta()["tools"]["allow"])
        assert not (allow & FORBIDDEN_ALLOW)
        assert "deny" not in _meta()["tools"]

    def test_default_queue_system_parallel(self):
        assert _meta()["default_queue"] == "system_parallel_queue"

    def test_recursion_limit_multiplier_12(self):
        assert _meta()["recursion_limit_multiplier"] == 12

    def test_watchover_timeout_at_least_60(self):
        """Key shape mirrors the watchover carrier convention: a
        ``watchover`` block with a ``timeout_seconds`` int >= 60."""
        watchover = _meta()["watchover"]
        assert isinstance(watchover["timeout_seconds"], int)
        assert watchover["timeout_seconds"] >= 60

    def test_empty_team_leaf(self):
        assert _meta()["team_members"] == []


# ---------------------------------------------------------------------------
# 2. Prompt-guide gates
# ---------------------------------------------------------------------------


class TestSketcherPromptGuideGates:
    @pytest.mark.parametrize("token", SYSTEM_INTERNAL_TOKENS)
    def test_no_system_internals_in_prose(self, token):
        for name in PROMPT_SURFACE:
            prose = _read(name).lower()
            assert token.lower() not in prose, (
                f"system-internals token {token!r} found in sketcher/{name}"
            )

    def test_no_md_path_tokens_in_prose(self):
        """Closure grep (guide §3): zero ``.md`` tokens in the sketcher
        prompt surface. The new files carry none — operational-path
        exemptions are not needed."""
        for name in PROMPT_SURFACE:
            hits = re.findall(r"\S*\.md\b", _read(name))
            assert hits == [], f".md path tokens in sketcher/{name}: {hits}"

    def test_rule_md_at_most_seven_cardinals(self):
        rule = _read("rule.md")
        cardinals = re.findall(r"^\d+\. \*\*", rule, re.MULTILINE)
        assert 1 <= len(cardinals) <= 7

    def test_tone_block_in_soul(self):
        soul = _read("soul.md")
        assert "Voice to my orchestrator" in soul
        assert "Per-severity framing" in soul

    def test_no_adapted_from_provenance(self):
        for name in PROMPT_SURFACE:
            prose = _read(name).lower()
            for marker in ("adapted from", "migrated from", "verified from"):
                assert marker not in prose, f"{marker!r} in sketcher/{name}"

    def test_section7_exempt_no_scrutiny_guidance(self):
        """Empty-team agents are EXEMPT from the (d) report-scrutiny
        guidance (guide §7; the registry walk skips empty teams). The
        exemption is pinned: the sketcher surface carries neither the
        marker-conditioned directive nor a spawn fallback (§8 — no
        invented peer targets; the leaf holds no spawn surface)."""
        joined = "\n".join(_read(n) for n in PROMPT_SURFACE)
        assert "[REPORT SANITY:" not in joined
        assert "spawn_instance" not in joined
        for peer_pattern in ("spawn a `", "spawn another", "spawn `"):
            assert peer_pattern not in joined

    def test_retry_classes_in_workflow(self):
        """The bounded-regenerate contract is pinned with the exact
        envelope codes: truncation class retries once, overflow never."""
        workflow = _read("workflow.md")
        for code in TRUNCATION_CODES:
            assert code in workflow
        for code in OVERFLOW_CODES:
            assert code in workflow

    def test_envelope_metrics_fields_in_workflow(self):
        workflow = _read("workflow.md")
        for field in (
            "finish_reason",
            "usage",
            "truncated",
            "marker_pass",
            "error_code",
            "latency_s",
        ):
            assert field in workflow, f"envelope field {field!r} missing"


# ---------------------------------------------------------------------------
# 3. Skill registration consistency
# ---------------------------------------------------------------------------


class TestSkillRegistrationConsistency:
    def test_skill_set_entries_have_templates_and_match_versions(self):
        entries = parse_skill_set_file(SKETCHER_DIR / "skill-set.yaml")
        by_name = {e.name: e for e in entries}
        assert set(by_name) == {"opendesign.generate-pipeline", "opendesign.list_systems"}
        for name, entry in by_name.items():
            template = SKETCHER_DIR / "skills-template" / f"{name}.md"
            assert template.is_file(), f"missing template for {name}"
            front = template.read_text(encoding="utf-8").split("---")[1]
            front_version = yaml.safe_load(front)["version"]
            assert str(front_version) == entry.version, (
                f"version drift: skill-set says {entry.version}, "
                f"template frontmatter says {front_version} ({name})"
            )

    def test_registry_loads_both_plugin_skills_with_zero_refusals(self):
        reg = load_registry(PLUGIN_DIR.parent)
        assert "opendesign" in reg.names()
        assert not reg.has_failures()
        for skill_id in ("opendesign.generate-pipeline", "opendesign.list_systems"):
            assert reg.try_get_skill(skill_id) is not None

    def test_consumers_name_sketcher(self):
        reg = load_registry(PLUGIN_DIR.parent)
        for skill_id in ("opendesign.generate-pipeline", "opendesign.list_systems"):
            skill = reg.get_skill(skill_id)
            assert any(c == "sketcher" for c in skill.consumers), (
                f"{skill_id} consumers must name sketcher; got {skill.consumers}"
            )

    def test_generate_pipeline_vendored_refs_resolve_inside_tree(self):
        reg = load_registry(PLUGIN_DIR.parent)
        skill = reg.get_skill("opendesign.generate-pipeline")
        aliases = {r.alias: r for r in skill.vendored_references}
        assert {"prompt-contracts", "image-templates", "systems"} <= set(aliases)
        for ref in skill.vendored_references:
            resolved = (PLUGIN_DIR / ref.path).resolve()
            assert resolved.exists(), f"vendored ref {ref.alias} does not resolve"
            assert PLUGIN_DIR.resolve() in resolved.parents

    def test_generate_pipeline_pins_upstream_tag_per_class(self):
        reg = load_registry(PLUGIN_DIR.parent)
        skill = reg.get_skill("opendesign.generate-pipeline")
        # Both vendored classes the skill spans carry the parent pin.
        assert skill.upstream_tag["copy_freely"] == "open-design-v0.24.1"
        assert skill.upstream_tag["snapshot_with_drift_alarm"] == "open-design-v0.24.1"

    def test_no_vendored_prompt_text_in_skill_body(self):
        """V8 references-not-text: no vendored file line of substance may
        appear verbatim in the skill body (drift-alarm contract)."""
        reg = load_registry(PLUGIN_DIR.parent)
        body = reg.get_skill("opendesign.generate-pipeline").body_markdown
        vendored_files = [
            *PLUGIN_DIR.glob("snapshot_with_drift_alarm/prompts/contracts/*.ts"),
            *PLUGIN_DIR.glob("copy_freely/prompt-templates/image/*.json"),
        ]
        assert vendored_files, "vendored reference targets vanished"
        vendored_lines = {
            line.strip()
            for fp in vendored_files
            for line in fp.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(line.strip()) >= 40
        }
        body_lines = {line.strip() for line in body.splitlines()}
        overlap = vendored_lines & body_lines
        assert overlap == set(), (
            f"vendored prompt text duplicated into the skill body: "
            f"{sorted(overlap)[:3]}"
        )

    def test_template_mirrors_declare_canonical_home(self):
        """Honest-duplication rule (guide §2): the bank templates state
        the plugin skill as the canonical home instead of claiming to be
        single-source."""
        for name in ("opendesign.generate-pipeline", "opendesign.list_systems"):
            template = SKETCHER_DIR / "skills-template" / f"{name}.md"
            assert "Canonical home" in template.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. Fail-closed glob behavior (pinned from live verification)
# ---------------------------------------------------------------------------


def _load_poisoned(mutation):
    """Copy the plugin tree to tmp, apply ``mutation(text) -> str`` to the
    generate-pipeline skill YAML, and attempt a load. Returns the refusal
    code on PluginSkillRefusal, None if the load unexpectedly succeeded."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "opendesign"
        shutil.copytree(PLUGIN_DIR, tmp)
        skill_file = tmp / "skills" / "opendesign.generate-pipeline.yaml"
        skill_file.write_text(mutation(skill_file.read_text(encoding="utf-8")))
        try:
            read_skill_file(
                skill_file,
                plugin_root=tmp,
                plugin_name="opendesign",
                plugin_license="Apache-2.0",
                parent_schema_version="1.0.3",
                parent_tag_pins={
                    "copy_freely": "open-design-v0.24.1",
                    "snapshot_with_drift_alarm": "open-design-v0.24.1",
                },
            )
        except PluginSkillRefusal as refusal:
            return refusal.code
    return None


class TestFailClosedGlobBehavior:
    def test_anonymous_consumer_refused(self):
        assert (
            _load_poisoned(lambda t: t.replace('- "sketcher"', '- "*"'))
            == "consumption_by_anonymous"
        )

    def test_globbed_consumer_refused(self):
        assert (
            _load_poisoned(lambda t: t.replace('- "sketcher"', '- "worker/*"'))
            == "consumption_by_anonymous"
        )

    def test_traversal_outside_tree_refused(self):
        assert (
            _load_poisoned(
                lambda t: t.replace(
                    'path: "copy_freely/design-systems/"', 'path: "../../etc/"'
                )
            )
            == "vendored_reference_outside_tree"
        )

    def test_tag_pin_divergence_refused(self):
        assert (
            _load_poisoned(
                lambda t: t.replace(
                    'copy_freely: "open-design-v0.24.1"',
                    'copy_freely: "open-design-v0.23.0"',
                )
            )
            == "plugin_ref_pin_mismatch"
        )

    def test_clean_load_succeeds(self):
        assert _load_poisoned(lambda t: t) is None


# ---------------------------------------------------------------------------
# 5. Designer wiring + parity schema
# ---------------------------------------------------------------------------


class TestDesignerSketcherWiring:
    def test_designer_team_carries_sketcher(self):
        meta = json.loads((DESIGNER_DIR / "meta.json").read_text(encoding="utf-8"))
        assert "sketcher" in meta["team_members"]
        assert "worker" in meta["team_members"]

    def test_designer_carries_report_scrutiny_guidance(self):
        """The (d) guidance must survive on the non-empty-team orchestrator
        (registry-walk requirement; healed in Stage 2b)."""
        surface = "\n".join(
            (DESIGNER_DIR / f).read_text(encoding="utf-8")
            for f in ("rule.md", "workflow.md", "soul.md")
            if (DESIGNER_DIR / f).is_file()
        )
        lowered = surface.lower()
        for snippet in ("[report sanity:", "interim, not completion", "send_message"):
            assert snippet in lowered, f"designer missing scrutiny snippet {snippet!r}"

    def test_sketcher_exemption_only(self):
        """§7 exemption applies to the sketcher ONLY — the orchestrator
        keeps its scrutiny rule while the leaf stays clean."""
        sketcher = "\n".join(_read(n) for n in PROMPT_SURFACE)
        assert "[REPORT SANITY:" not in sketcher

    def test_invoke_wait_timeout_convention(self):
        workflow = (DESIGNER_DIR / "workflow.md").read_text(encoding="utf-8")
        assert "invoke_agent_and_wait" in workflow
        # §6.2 chain: wall 600 + 60s strict margin — the documented
        # explicit-wait floor is 660s (bumped from the Phase-1 400s;
        # the workflow may still cite 400s as the chain-violating
        # rationale, hence only the positive pin here).
        assert "≥ 660s" in workflow

    def test_addendum_exists_with_leader_gates(self):
        addendum = (PARITY_DIR / "stage2-addendum.md").read_text(encoding="utf-8")
        for token in ADDENDUM_GATE_TOKENS:
            assert token in addendum, f"gate token missing from addendum: {token!r}"

    def test_parity_runs_file_exists(self):
        assert (PARITY_DIR / "parity-runs.jsonl").is_file()

    def test_designer_no_dual_run_orchestrator(self):
        """The dual-run pilot is dead: designer's workflow carries NO
        Dual-Run Pilot section and pins the sole-lane orchestrator shape
        (sketcher dispatch -> critic review -> designer accept/save)."""
        workflow = (DESIGNER_DIR / "workflow.md").read_text(encoding="utf-8")
        assert "## Dual-Run Pilot" not in workflow, (
            "Dual-Run Pilot section must be deleted (designer-critic-orchestration D7)"
        )
        assert re.search(
            r"sketcher is the ONLY OD generation lane", workflow, re.IGNORECASE
        ), "sole-lane rule missing from designer workflow"
        assert re.search(
            "designer → sketcher → critic → designer accept/save", workflow
        ), "pipeline shape (designer → sketcher → critic → accept/save) missing"

    def test_critic_pipeline_pins_present(self):
        """The critic-pipeline pins survive in designer's rule surface:
        pinned_spec_sha mandatory, D4 severity-gated two-branch terminal,
        D3 amendment discipline (malformed-verdict -> critic re-dispatch)."""
        rule = (DESIGNER_DIR / "rule.md").read_text(encoding="utf-8")
        workflow = (DESIGNER_DIR / "workflow.md").read_text(encoding="utf-8")
        # pinned_spec_sha mandatory (D3 amendment 4) -- Cardinal #1.
        assert "pinned_spec_sha" in rule
        # D4 severity-gated two-branch trigger -- Guideline (h).
        assert "accept-with-disclosure" in rule
        assert "escalate-only" in rule
        assert "[REVIEW-CAVEAT]" in rule
        # D3 amendment discipline (amendments 2+3) -- workflow report handling.
        assert "prev_attempt_unparseable" in workflow
        assert "^verdict:\\s*(pass|needs-revision)" in workflow, (
            "regex-anchored verdict parse rule missing from designer workflow"
        )

    def test_parity_rows_validate_against_schema(self):
        """Validate any logged rows against the v2 schema
        (lane = sketcher | critic; 'direct' is no longer reachable).
        HTML-comment header lines (schema-evolution markers) are skipped
        so they never hit json.loads; a `critic_verdict` field is
        REJECTED — the verdict lives on the review itself, never as a
        parity-runs field."""
        path = PARITY_DIR / "parity-runs.jsonl"
        required = {
            "run_id", "ts", "page", "lane", "latency_s", "usage",
            "truncated", "gates", "marker_pass", "model",
        }
        rejected_fields = {"critic_verdict"}
        skipped = 0
        validated = 0
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("<!--"):
                skipped += 1
                continue
            row = json.loads(stripped)
            validated += 1
            missing = required - set(row)
            assert not missing, f"row {i} missing fields: {missing}"
            assert row["lane"] in ("sketcher", "critic"), (
                f"row {i} lane {row['lane']!r} outside v2 enum {{sketcher, critic}}"
            )
            bad = rejected_fields & set(row)
            assert not bad, f"row {i} carries rejected field(s): {bad}"
            unexpected = set(row) - required - {"notes"}
            assert not unexpected, f"row {i} unexpected fields: {unexpected}"
            assert isinstance(row["latency_s"], (int, float))
            assert isinstance(row["truncated"], bool)
            assert isinstance(row["marker_pass"], bool)
            assert set(row["usage"]) == {
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            }
            assert set(row["gates"]) == {"empty_response", "finish_reason", "eof_markers"}
        assert validated >= 1, "expected at least one data row (the historical smoke row)"

# ---------------------------------------------------------------------------
# 6. Critic-agent Nit-2 gates (designer-critic-orchestration)
# ---------------------------------------------------------------------------

CRITIC_DIR = REPO_ROOT / "agents" / "critic"


def _critic_meta() -> dict:
    return json.loads((CRITIC_DIR / "meta.json").read_text(encoding="utf-8"))


def _resolve_critic_filter(instance_tag: str) -> set:
    """The critic tool-filter seam, shimmed locally (tidier round 2 #5;
    identical shape to test_designer_rewire._resolve_critic_filter — no
    cross-test-file imports): factory-created image+compare tools ->
    scan_tools_for_full_docs -> resolve_tool_filter against critic's
    canonical meta allow/deny, registry metadata saved/restored."""
    from daemon.tools.image_tools import create_image_tools
    from daemon.tools.compare_tools import create_compare_tools
    from daemon.tools import _tool_registry as reg
    from daemon.tools.instance import resolve_tool_filter

    meta = _critic_meta()
    saved = dict(reg._tool_metadata)
    try:
        tools = create_image_tools(None, instance_tag) + create_compare_tools(
            None, instance_tag
        )
        reg.scan_tools_for_full_docs(tools)
        return resolve_tool_filter(
            meta["tools"]["allow"], meta["tools"]["deny"],
            tool_categories=reg.list_tools_by_category(),
        )
    finally:
        reg._tool_metadata.clear()
        reg._tool_metadata.update(saved)


class TestCriticAgentNit2:
    """Nit-2 pattern list (architecture-recommendation.md §3 Nit 2):
    meta validation, deny-strips-allow, implicit team expansion, schema
    v2 validity, new-agent-dir discoverability."""

    def test_critic_meta(self):
        """Critic meta.json shape per the canonical D6 form: bare-name
        allow, image_save + mcp + hard refusals in deny, view-views
        absent, leaf team, vision lane."""
        meta = _critic_meta()
        assert meta["id"] == "critic"
        assert meta["team_members"] == []
        assert meta["tools"]["allow"] == ["read_file", "image", "design"]
        deny = set(meta["tools"]["deny"])
        for token in (
            "bash", "proc", "instance", "service", "midflight",
            "shared_meta_kv", "infra", "mcp", "image_save",
        ):
            assert token in deny, f"critic tools.deny missing {token!r}"
        assert "view-views" not in meta["tools"]["allow"]
        assert meta["llm_model"] == "vision"
        assert meta["skill_injection"] is True
        assert "default_queue" not in meta
        assert "watchover" not in meta

    def test_critic_team_implied(self):
        """DECLARED team_members is empty, but the `design` allow entry
        auto-extends EFFECTIVE team membership with image-comparator
        (daemon _auth TOOL_REQUIRED_AGENTS mapping)."""
        meta = _critic_meta()
        assert meta["team_members"] == []  # DECLARED
        from daemon.tools._auth import TOOL_REQUIRED_AGENTS
        assert "design" in meta["tools"]["allow"]
        assert TOOL_REQUIRED_AGENTS["design"] == ["image-comparator"], (
            "design category must auto-extend the effective team with "
            "image-comparator (spawn-time inheritance)"
        )

    def test_critic_deny_wins(self):
        """Thin consumer of the shared critic tool-filter seam (tidier
        round 2 #5; the AUTHORITATIVE exact-5-tool-set assertion lives in
        test_designer_rewire.TestCriticToolsResolveNit2): no allow entry
        can sneak a write capability past the explicit deny list —
        image_save stripped from the image category; no
        write_file/edit_file anywhere."""
        resolved = _resolve_critic_filter("critic-deny-wins")
        for write_tool in ("write_file", "edit_file", "image_save"):
            assert write_tool not in resolved, (
                f"write-capable tool {write_tool!r} leaked through critic's filter"
            )

    def test_parity_runs_v2_schema(self):
        """Lean Nit-2 surface (tidier round 2 #4): header-skip + smoke-row
        presence ONLY — the full v2 schema assertions (field set, lane
        enum, critic_verdict rejection, field types) are single-sourced
        in TestDesignerSketcherWiring.test_parity_rows_validate_against_schema."""
        parity = PARITY_DIR / "parity-runs.jsonl"
        assert parity.is_file()
        saw_data_row = False
        for line in parity.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("<!--"):
                continue  # header-skip: schema-evolution markers never hit json.loads
            json.loads(stripped)  # every data row must parse
            saw_data_row = True
        assert saw_data_row, "smoke row missing"

    def test_agent_registry_scan(self):
        """New-agent-dir discoverability (Nit-2 / R23): the real registry
        facade discovers agents/critic/ (counterpart to the tier-1
        boot-scan fix efc460262). Runs the probe in a subprocess pinned
        to the repo root; the venv interpreter is required on this host
        (system python3 lacks pydantic)."""
        import subprocess
        import sys

        probe = (
            "from daemon.registry import get_registry; "
            "assert get_registry().exists('critic'); "
            "assert get_registry().exists('sketcher')"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, (
            f"agent_registry_scan probe failed:\n{result.stderr[-2000:]}"
        )
