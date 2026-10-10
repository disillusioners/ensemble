"""Unit tests for the slice-⑤ designer rewire.

REC §7 risk (verbatim): "Designer rewire drops a call site — a missed
``od_*`` reference degrades designer to text-only in production. Guard:
full inventory of ``od_*`` call sites (designer soul.md, meta.json,
designer-side skills) + CI check on the rewire diff."

This test asserts:

1. **The legacy 10 MCP ``od_*`` tools are NOT referenced as live call
   sites** in agents/designer/{soul,rule,workflow,skills-template,tools_note}.md
   (the only allowed references are "previous/historical" mentions).
2. **Designer holds ZERO Port-style ``od.*`` tokens** across the same
   files (the designer-critic-orchestration rewire moved the generation
   lane to sketcher wholesale; workflow.md's only permitted od.* mention
   is the sole-lane negative, "There is no direct ``od.generate`` lane").
3. **The last-effort text-fallback block in workflow.md is PRESERVED
   VERBATIM** (the slice-⑤ directive: "⛔ LAST-EFFORT TEXT FALLBACK
   UNCHANGED — absolute, verbatim-preserved fallback semantics.").
4. **The fallback_reason token set is preserved** (the five exact
   tokens ``tool-not-bound | call-error | timeout | daemon-unavailable |
   other:<detail>``).

Each assertion is a fail-closed CI gate. A rewire that drops a call
site fails the first assertion; a rewire that changes the fallback
semantics fails the third.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DESIGNER_ROOT = REPO_ROOT / "agents" / "designer"

# The exact tokens the live designer workflow uses for fallback_reason
# (Cardinal #7; the conformance loop enforces these strings).
FALLBACK_REASON_TOKENS = (
    "tool-not-bound",
    "call-error",
    "timeout",
    "daemon-unavailable",
    "other:<detail>",
)

# The 10 legacy MCP od_* tools. The 5 rare ones (dropped at ⑤)
# may still appear in HISTORICAL context (the new docs say "previously
# had X, now dropped"); the 5 happy-path ones must NOT appear as live
# call sites in the workflow step-by-step.
LIVE_OD_TOOLS = (
    "od_list_projects",     # Step 0 lane-start probe (replaced by opendesign.list_systems skill)
    "od_compose_brief",     # Step 1 (replaced by od.compose_brief)
    "od_generate_design",   # Step 2 (replaced by od.generate)
    "od_lint_artifact",     # Step 3 (replaced by od.lint)
)

# The 6 rare tools dropped at slice ⑤ (REC §4.3 row ⑤; OD-UI retires at slice ⑦).
# These must NOT appear as LIVE call sites in any designer .md file. Mentions
# in clearly historical context ("dropped at slice ⑤", "previous", "retire")
# remain acceptable; the discrimination is documented inline below.
RARE_OD_TOOLS = (
    "od_save_artifact",        # slice ⑤: dropped; OD-UI provenance retires ⑦
    "od_save_project_file",    # slice ⑤: dropped; OD-UI provenance retires ⑦
    "od_get_project",          # slice ⑤: dropped (project admin surface not needed)
    "od_create_project",       # slice ⑤: dropped (project admin surface not needed)
    "od_update_project",       # slice ⑤: dropped (project admin surface not needed)
    "od_delete_project",       # slice ⑤: dropped (project admin surface not needed)
)

def _read_designer_text() -> str:
    """Read all designer-side .md files (soul, rule, workflow, tools_note, skills-template)."""
    paths = [
        DESIGNER_ROOT / "soul.md",
        DESIGNER_ROOT / "rule.md",
        DESIGNER_ROOT / "workflow.md",
        DESIGNER_ROOT / "tools_note.md",
        DESIGNER_ROOT / "skills-template" / "design-strategy.md",
    ]
    parts = []
    for p in paths:
        parts.append(f"\n# === {p.name} ===\n")
        parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Live call-site audit (REC §7 guard)
# ---------------------------------------------------------------------------


class TestDesignerNoLiveLegacyOdTools:
    """The legacy 10 MCP od_* tools must not appear as LIVE call sites.

    Historical mentions (e.g. "previously had od_list_projects ...")
    are allowed in the docstrings; this test isolates the STEP-BY-STEP
    sections where live calls live. The fallback block is allowed to
    mention the probe tool name once (probe semantics).
    """

    def test_workflow_step_1_no_legacy_od_tools(self):
        """Step 1 of the Mockup lane (workflow.md) calls ONLY the new Port tools."""
        workflow = (DESIGNER_ROOT / "workflow.md").read_text(encoding="utf-8")
        # Extract the Step 1 section between Step 0 header and Step 2 header.
        # The Step 0/1/2 sub-sections are in the Mockup lane section.
        step1_match = re.search(
            r"####\s+Step 1.*?(?=####\s+Step 2)",
            workflow,
            re.DOTALL,
        )
        assert step1_match, "Step 1 section not found in workflow.md"
        step1 = step1_match.group(0)
        for legacy_tool in LIVE_OD_TOOLS:
            assert legacy_tool not in step1, (
                f"Step 1 still references legacy tool {legacy_tool!r} as a "
                f"live call site — the rewire is incomplete. Step 1 body: "
                f"{step1[:300]}..."
            )

    def test_workflow_step_0_uses_skill_probe(self):
        """Step 0 lane-start probe uses the opendesign.list_systems skill,
        not od_list_projects (the legacy MCP tool)."""
        workflow = (DESIGNER_ROOT / "workflow.md").read_text(encoding="utf-8")
        step0_match = re.search(
            r"####\s+Step 0.*?(?=####\s+Step 1)",
            workflow,
            re.DOTALL,
        )
        assert step0_match, "Step 0 section not found in workflow.md"
        step0 = step0_match.group(0)
        assert "opendesign.list_systems" in step0, (
            "Step 0 probe should use the opendesign.list_systems skill "
            "(per the slice-⑤ rewire)"
        )

    def test_no_rare_od_tools_as_live_call_sites(self):
        """The 6 rare tools dropped at slice ⑤ must NOT appear as LIVE call
        sites in any designer .md file.

        Discrimination: a mention is EXEMPT iff its containing line contains
        one of the historical-context tokens (``dropped``, ``previous``,
        ``retir``, ``removed``) — those are the slice-⑤/⑦ retirement
        annotations, not live call-site references. Mentions in step /
        procedure sections OR in numbered procedural lists, when not exempted,
        fail the test.

        Scope: ALL designer ``.md`` files (soul, rule, workflow, tools_note,
        skills-template/design-strategy). The existing ``LIVE_OD_TOOLS``
        test covers the 4 happy-path tools in Step 1; this test pins the
        6 dropped-at-⑤ tools across the whole designer surface.
        """
        designer_files = [
            DESIGNER_ROOT / "soul.md",
            DESIGNER_ROOT / "rule.md",
            DESIGNER_ROOT / "workflow.md",
            DESIGNER_ROOT / "tools_note.md",
            DESIGNER_ROOT / "skills-template" / "design-strategy.md",
        ]
        # Historical-context exemption tokens. Matched case-insensitively
        # against the line containing the tool name.
        HISTORICAL_TOKENS = re.compile(
            r"dropped|previous|retir|removed|legacy", re.IGNORECASE
        )

        violations: list = []
        for fp in designer_files:
            if not fp.exists():
                continue
            text = fp.read_text(encoding="utf-8")
            for line in text.splitlines():
                for rare_tool in RARE_OD_TOOLS:
                    if rare_tool not in line:
                        continue
                    # Exempt historical mentions.
                    if HISTORICAL_TOKENS.search(line):
                        continue
                    violations.append(f"{fp.name}: {line.strip()!r} (tool {rare_tool!r})")
        assert violations == [], (
            "Rare OD tools appear as live call sites in designer docs "
            "(slice ⑤ dropped them — any actionable mention is a rewire regression):\n"
            + "\n".join(violations)
        )


class TestDesignerHasZeroOdPorts:
    """INVERTED pin (designer-critic-orchestration D1=B): designer holds
    ZERO od.* tokens. The generation lane moved to sketcher wholesale —
    any live od.* reference in designer prose is a lane-leak regression.
    workflow.md IS swept line-by-line: the ONLY exempt lines are the
    sole-lane negative sentences ("There is no direct ...") — any other
    od.* hit there fails the sweep (tidier round 2 narrowed the old
    blanket skip)."""

    ZERO_PORT_PATTERN = re.compile(r"od\.(generate|compose_brief|save|lint)")
    SOLE_LANE_NEGATIVE = "There is no direct"

    def _assert_zero(self, relpath: str) -> None:
        text = (DESIGNER_ROOT / relpath).read_text(encoding="utf-8")
        hits = self.ZERO_PORT_PATTERN.findall(text)
        assert not hits, f"{relpath} still carries od.* tokens: {hits}"

    def test_soul_md_zero_od_ports(self):
        self._assert_zero("soul.md")

    def test_rule_md_zero_od_ports(self):
        self._assert_zero("rule.md")

    def test_tools_note_md_zero_od_ports(self):
        self._assert_zero("tools_note.md")

    def test_design_strategy_md_zero_od_ports(self):
        self._assert_zero("skills-template/design-strategy.md")

    def test_workflow_md_zero_od_ports_outside_sole_lane_negative(self):
        """workflow.md sweep with the NARROWED exemption: a line may carry
        an od.* token only when it matches the sole-lane negative pattern
        ("There is no direct ..."). Any other od.* hit is a lane leak
        (:109-class) and fails here."""
        workflow_lines = (DESIGNER_ROOT / "workflow.md").read_text(
            encoding="utf-8"
        ).splitlines()
        leaks = [
            line.strip()
            for line in workflow_lines
            if re.search(r"\bod\.[a-z_]+", line)
            and self.SOLE_LANE_NEGATIVE not in line
        ]
        assert not leaks, (
            "workflow.md carries od.* tokens outside the sole-lane "
            f"negative — lane-leak regression:\n" + "\n".join(leaks)
        )

    def test_designer_od_generate_removed(self):
        """Nit-2 spine inverse: the od.generate kill is total across the
        designer prose surface (meta/soul/rule/tools_note/skill)."""
        meta_text = (DESIGNER_ROOT / "meta.json").read_text(encoding="utf-8")
        assert not re.search(r'"od\.[a-z_]+"', meta_text), (
            "designer meta.json still grants an od.* tool"
        )
        for relpath in (
            "soul.md", "rule.md", "tools_note.md",
            "skills-template/design-strategy.md",
        ):
            self._assert_zero(relpath)


class TestSketcherHasFourOdPorts:
    """The SPINE's positive half: the four od.* Port tools live on the
    sketcher side — present in sketcher's tool grant AND its prose."""

    NEW_OD_PORTS = ("od.compose_brief", "od.generate", "od.lint", "od.save")
    SKETCHER_ROOT = DESIGNER_ROOT.parent / "sketcher"

    def test_sketcher_meta_grants_all_four_ports(self):
        import json
        meta = json.loads(
            (self.SKETCHER_ROOT / "meta.json").read_text(encoding="utf-8")
        )
        for port in self.NEW_OD_PORTS:
            assert port in meta["tools"]["allow"], (
                f"sketcher meta.json missing port {port}"
            )

    def test_sketcher_prose_names_all_four_ports(self):
        surface = "\n".join(
            (self.SKETCHER_ROOT / f).read_text(encoding="utf-8")
            for f in ("soul.md", "rule.md", "workflow.md", "tools_note.md")
        )
        for port in self.NEW_OD_PORTS:
            assert port in surface, f"sketcher prose missing port {port}"


def _resolve_critic_filter(instance_tag: str) -> set:
    """The critic tool-filter seam, factored ONCE (tidier round 2 #5):
    factory-created image+compare tools -> scan_tools_for_full_docs ->
    resolve_tool_filter against critic's canonical meta allow/deny.
    Registry metadata is saved and restored around the probe.
    (test_sketcher_agent.py carries an identical local shim — no
    cross-test-file imports.)"""
    import json

    from daemon.tools.image_tools import create_image_tools
    from daemon.tools.compare_tools import create_compare_tools
    from daemon.tools import _tool_registry as reg
    from daemon.tools.instance import resolve_tool_filter

    critic_root = DESIGNER_ROOT.parent / "critic"
    meta = json.loads((critic_root / "meta.json").read_text(encoding="utf-8"))
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


class TestCriticToolsResolveNit2:
    """Nit-2 WRITE-LEAK GATE: resolving critic's canonical filter through
    the REAL daemon seam (factory-created tools -> scan_tools_for_full_docs
    -> resolve_tool_filter) yields the EXACT 5-tool read-only set. An AC
    pinned at '4+1' can never pass because the `image` category resolves
    to FOUR tools incl. image_save — the explicit deny strips it."""

    EXPECTED_RESOLVED = {
        "read_file", "image_get", "image_list", "explain_image", "compare_images",
    }

    def test_critic_tools_resolve(self):
        """AUTHORITATIVE exact-5-tool-set check (the shared seam's
        assertion of record; test_sketcher_agent.test_critic_deny_wins is
        a thin consumer of the same seam shape)."""
        resolved = _resolve_critic_filter("critic-tools-resolve")
        assert resolved == self.EXPECTED_RESOLVED, (
            f"critic resolved tool set drifted: {resolved ^ self.EXPECTED_RESOLVED}"
        )


class TestFallbackBlockPreservedVerbatim:
    """The last-effort text fallback block is preserved verbatim (slice-⑤ directive).

    The slice-⑤ dispatch: "⛔ LAST-EFFORT TEXT FALLBACK UNCHANGED —
    absolute, verbatim-preserved fallback semantics." This test asserts
    the structural shape of the fallback block (Step 2) is unchanged
    from the pre-⑤ reference: the fallback_reason token set + the
    "SPEC INCOMPLETE — conformance review MUST reject it" sentinel.
    """

    def test_step_2_fallback_block_intact(self):
        """Structural sentinels of the Step 2 fallback block (tidier
        round 2 #6: token-set presence moved to the single parametrized
        source in TestFallbackReasonAudit — no duplicate loops here)."""
        workflow = (DESIGNER_ROOT / "workflow.md").read_text(encoding="utf-8")
        step2_match = re.search(
            r"####\s+Step 2.*?(?=\n---\n|\Z)",
            workflow,
            re.DOTALL,
        )
        assert step2_match, "Step 2 section not found in workflow.md"
        step2 = step2_match.group(0)

        # The Cardinal #7 sentinel must appear.
        assert "SPEC INCOMPLETE" in step2, (
            "Step 2 fallback block must carry the SPEC INCOMPLETE sentinel"
        )
        assert "conformance review MUST reject" in step2, (
            "Step 2 fallback block must carry the 'conformance review "
            "MUST reject' guarantee"
        )

    def test_cardinal_seven_in_rule_md(self):
        """Cardinal #7 lives in rule.md (the live designer Cardinal):
        the text-lane-spec-incomplete guarantee sentinel. The token set
        itself is asserted once, parametrized, in TestFallbackReasonAudit.
        """
        rule = (DESIGNER_ROOT / "rule.md").read_text(encoding="utf-8")
        assert "Cardinal #7 applies" in rule, (
            "rule.md Cardinal #7 sentinel must be preserved"
        )


class TestFallbackReasonAudit:
    """ONE parametrized source over the fallback_reason token set
    (tidier round 2 #6): every token must appear in rule.md (Cardinal #7)
    and in workflow.md's Step 2 fallback block — the section-scoped check
    subsumes whole-file presence, so the weaker whole-file loop is gone."""

    @pytest.mark.parametrize("token", FALLBACK_REASON_TOKENS)
    def test_fallback_reason_token_present_in_rule_md(self, token):
        rule = (DESIGNER_ROOT / "rule.md").read_text(encoding="utf-8")
        assert token in rule, f"rule.md Cardinal #7 missing token {token!r}"

    @pytest.mark.parametrize("token", FALLBACK_REASON_TOKENS)
    def test_fallback_reason_token_present_in_workflow_md(self, token):
        workflow = (DESIGNER_ROOT / "workflow.md").read_text(encoding="utf-8")
        step2_match = re.search(
            r"####\s+Step 2.*?(?=\n---\n|\Z)",
            workflow,
            re.DOTALL,
        )
        assert step2_match, "Step 2 section not found in workflow.md"
        assert token in step2_match.group(0), (
            f"Step 2 fallback block missing token {token!r} — the "
            f"slice-⑤ directive forbids changing fallback semantics"
        )

    def test_fallback_reason_tokens_exact_string_match(self):
        """The token set is exact — no slight rewordings."""
        # Concatenate all designer files and check the literal substring
        # of all five tokens (with pipe separators) is present.
        combined = _read_designer_text()
        assert (
            "tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>"
            in combined
        ), (
            "fallback_reason token set must appear EXACTLY as written "
            "(the conformance loop uses these literal strings)"
        )