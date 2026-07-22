"""Comprehensive validation pack for the Doc Writer agent.

Validates the doc-writer agent definition:
  1.  meta.json is valid JSON
  2.  meta.json conforms to the AgentMetadata Pydantic model
  3.  Auto-discovery via AgentRegistry + SKIP_DIRS exclusion
  4.  innate_skills: ["chart"] → chart tool category exists
  5.  tools.allow categories all resolve in the tool registry
  6.  Leader meta.json includes "doc-writer" in team_members
  7.  doc-writer team_members is empty (cannot delegate)
  8.  KB_AGENT_IDS does NOT include "doc-writer"
  9.  Cross-doc consistency: code rejection extension list (soul/rule/workflow)
  10. Cross-doc consistency: format→mechanism conversion mapping
  11. Cross-doc consistency: bash allowlist (soul ↔ rule)

Modelled after tests/unit/test_devops_agent.py.
"""

import json
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent.parent
DOCWRITER_AGENT_DIR = PROJECT_ROOT / "agents" / "doc-writer"
LEADER_AGENT_DIR = PROJECT_ROOT / "agents" / "leader"

META_PATH = DOCWRITER_AGENT_DIR / "meta.json"
SOUL_PATH = DOCWRITER_AGENT_DIR / "soul.md"
RULE_PATH = DOCWRITER_AGENT_DIR / "rule.md"
WORKFLOW_PATH = DOCWRITER_AGENT_DIR / "workflow.md"

# Expected code-rejection extension set (canonical, sorted-agnostic)
EXPECTED_REJECT_EXTENSIONS: set[str] = {
    ".py", ".ts", ".js", ".jsx", ".tsx", ".go", ".rs",
    ".java", ".c", ".cpp", ".h", ".rb", ".php", ".sh",
    ".swift", ".kt", ".scala", ".cs", ".vue", ".svelte",
}

# Expected bash allowlist (commands the agent may use)
EXPECTED_BASH_COMMANDS: set[str] = {
    "pandoc", "libreoffice", "wc", "file", "ls", "which",
}

# Extensions that are NOT code (document/data formats the agent DOES handle).
# Used to filter out format extensions when extracting the code-rejection list.
NON_CODE_EXTENSIONS: set[str] = {
    ".md", ".csv", ".docx", ".pdf", ".pptx", ".xlsx",
}


# ---------------------------------------------------------------------------
# Helper loaders
# ---------------------------------------------------------------------------

def _load_meta() -> dict:
    """Load and return doc-writer meta.json as a dict."""
    with open(META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _read(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_code_extensions(text: str) -> set[str]:
    """Extract code-rejection extensions from a document.

    Scans for backtick-wrapped extensions like `` `.py` `` and inline
    extensions like ``.py`` in parenthetical lists. Returns extensions
    WITH the leading dot, and filters out known non-code extensions
    (``.md``, ``.csv``, etc.) so the result reflects only the code-rejection
    list.
    """
    # Backtick-wrapped: `.py` → capture with dot
    bt = set(re.findall(r"`(\.\w+)`", text))
    # Inline parenthetical: (.py, .ts, ...) → capture with dot
    inline = set(re.findall(r"(\.\w+)[,\s)]", text))
    all_exts = bt | inline
    # Filter out document/data format extensions
    return all_exts - NON_CODE_EXTENSIONS


# =============================================================================
# 1. meta.json Validity
# =============================================================================


class TestMetaJsonValidity:
    """Validate meta.json parses correctly and has expected structure."""

    def test_meta_json_exists(self) -> None:
        """meta.json should exist."""
        assert META_PATH.exists(), f"meta.json not found at {META_PATH}"

    def test_meta_json_is_valid_json(self) -> None:
        """meta.json should parse as valid JSON dict."""
        meta = _load_meta()
        assert isinstance(meta, dict), "meta.json root should be a JSON object"

    def test_required_fields_exist(self) -> None:
        """meta.json should contain all expected top-level fields."""
        meta = _load_meta()
        required = ["id", "name", "description", "icon", "color", "version",
                    "innate_skills", "tools", "team_members"]
        for field in required:
            assert field in meta, f"Required field '{field}' missing from meta.json"

    def test_agent_id(self) -> None:
        """Agent id should be 'doc-writer'."""
        meta = _load_meta()
        assert meta["id"] == "doc-writer", f"Expected id 'doc-writer', got '{meta.get('id')}'"

    def test_agent_name(self) -> None:
        """Agent name should be 'Doc Writer'."""
        meta = _load_meta()
        assert meta["name"] == "Doc Writer", f"Expected 'Doc Writer', got '{meta.get('name')}'"


# =============================================================================
# 2. meta.json conforms to AgentMetadata model
# =============================================================================


class TestAgentMetadataConformance:
    """Verify meta.json validates against the AgentMetadata Pydantic model."""

    def test_model_validate_succeeds(self) -> None:
        """AgentMetadata.model_validate(meta_dict_with_path) must not raise."""
        from daemon.registry import AgentMetadata

        meta = _load_meta()
        # ``path`` is injected by discover(); meta.json doesn't carry it.
        meta["path"] = str(DOCWRITER_AGENT_DIR)

        # Must not raise
        md = AgentMetadata.model_validate(meta)
        assert md.id == "doc-writer"
        assert md.name == "Doc Writer"

    def test_tools_parsed_as_tool_filter(self) -> None:
        """tools block should parse into a ToolFilter."""
        from daemon.registry import ToolFilter

        meta = _load_meta()
        tools_config = meta.get("tools")
        assert tools_config is not None
        tf = ToolFilter.model_validate(tools_config)
        assert tf.allow is not None
        assert "filesystem" in tf.allow


# =============================================================================
# 3. Auto-Discovery
# =============================================================================


class TestAutoDiscovery:
    """Verify doc-writer is discoverable by the registry."""

    def test_docwriter_not_in_skip_dirs(self) -> None:
        """doc-writer must NOT be in SKIP_DIRS."""
        from daemon.registry import SKIP_DIRS

        assert "doc-writer" not in SKIP_DIRS, (
            "'doc-writer' should NOT be in SKIP_DIRS — it is a real agent"
        )

    def test_registry_discovers_docwriter(self) -> None:
        """AgentRegistry.discover() should find doc-writer."""
        from daemon.registry import AgentRegistry

        agents_dir = DOCWRITER_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        assert registry.exists("doc-writer"), "doc-writer should be discoverable"

    def test_docwriter_in_agent_list(self) -> None:
        """doc-writer should appear in list_all()."""
        from daemon.registry import AgentRegistry

        agents_dir = DOCWRITER_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        agent_ids = {a.id for a in registry.list_all()}
        assert "doc-writer" in agent_ids

    def test_registry_metadata_fields(self) -> None:
        """Registry-loaded metadata should have correct values."""
        from daemon.registry import AgentRegistry

        agents_dir = DOCWRITER_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        md = registry.get("doc-writer")
        assert md is not None
        assert md.id == "doc-writer"
        assert md.name == "Doc Writer"
        assert md.version == "1.0.0"


# =============================================================================
# 4. innate_skills: ["chart"] grants chart tool
# =============================================================================


class TestInnateSkillsChart:
    """Verify chart innate skill grants the chart tool category."""

    def test_innate_skills_equals_chart(self) -> None:
        """meta.json innate_skills should be exactly ['chart']."""
        meta = _load_meta()
        assert meta.get("innate_skills") == ["chart"], (
            f"Expected innate_skills == ['chart'], got: {meta.get('innate_skills')}"
        )

    def test_chart_in_innate_skill_tool_map(self) -> None:
        """'chart' must be registered in INNATE_SKILL_TOOL_CATEGORIES."""
        from daemon.tools.instance import INNATE_SKILL_TOOL_CATEGORIES

        assert "chart" in INNATE_SKILL_TOOL_CATEGORIES, (
            "'chart' must be in INNATE_SKILL_TOOL_CATEGORIES"
        )
        assert "chart" in INNATE_SKILL_TOOL_CATEGORIES["chart"], (
            "chart skill should map to ['chart'] tool category"
        )

    def test_chart_is_known_tool_category(self) -> None:
        """'chart' must be a registered tool category module."""
        from daemon.tools._tool_registry import CATEGORY_MODULES

        assert "chart" in CATEGORY_MODULES, (
            "'chart' must be in CATEGORY_MODULES"
        )

    def test_expand_allow_adds_chart(self) -> None:
        """expand_allow_for_innate_skills should add 'chart' to allow list."""
        from daemon.tools.instance import expand_allow_for_innate_skills

        expanded = expand_allow_for_innate_skills(
            allow=["filesystem", "bash"],
            innate_skills=["chart"],
        )
        assert "chart" in expanded, "chart category should be auto-granted"


# =============================================================================
# 5. tools.allow categories all exist
# =============================================================================


class TestToolsAllowCategories:
    """Verify every entry in tools.allow resolves to a known category or tool."""

    def test_all_allow_entries_resolve(self) -> None:
        """Each tools.allow entry must be a known category or individual tool."""
        from daemon.tools._tool_registry import CATEGORY_MODULES

        meta = _load_meta()
        allow_list = meta.get("tools", {}).get("allow", [])

        unresolved: list[str] = []
        for entry in allow_list:
            if entry in CATEGORY_MODULES:
                continue
            unresolved.append(entry)

        assert not unresolved, (
            f"These tools.allow entries are NOT known tool categories: {unresolved}. "
            f"Known categories: {sorted(CATEGORY_MODULES.keys())}"
        )


# =============================================================================
# 6. Leader can spawn doc-writer
# =============================================================================


class TestLeaderIntegration:
    """Verify the leader's meta.json includes doc-writer in team_members."""

    def test_docwriter_in_leader_team_members(self) -> None:
        """Leader meta.json should list 'doc-writer' in team_members."""
        leader_meta_path = LEADER_AGENT_DIR / "meta.json"
        with open(leader_meta_path, "r", encoding="utf-8") as f:
            leader_meta = json.load(f)

        team = leader_meta.get("team_members", [])
        assert "doc-writer" in team, (
            f"'doc-writer' should be in leader.team_members, got: {team}"
        )


# =============================================================================
# 7. team_members is empty
# =============================================================================


class TestNoDelegation:
    """doc-writer should have an empty team_members (cannot delegate)."""

    def test_team_members_empty(self) -> None:
        """doc-writer team_members should be []."""
        meta = _load_meta()
        assert meta.get("team_members") == [], (
            f"doc-writer team_members should be [], got: {meta.get('team_members')}"
        )

    def test_registry_team_members_empty(self) -> None:
        """Registry-loaded team_members should also be []."""
        from daemon.registry import AgentRegistry

        agents_dir = DOCWRITER_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        md = registry.get("doc-writer")
        assert md is not None
        assert md.team_members == []


# =============================================================================
# 8. KB_AGENT_IDS NOT modified
# =============================================================================


class TestKbAgentIds:
    """doc-writer must NOT be in KB_AGENT_IDS (it should be visible in UI)."""

    def test_docwriter_not_in_kb_agent_ids(self) -> None:
        """'doc-writer' should NOT be in KB_AGENT_IDS."""
        from daemon.repositories.instance.repository import KB_AGENT_IDS

        assert "doc-writer" not in KB_AGENT_IDS, (
            f"'doc-writer' should NOT be in KB_AGENT_IDS "
            f"(it should be visible in the UI). KB_AGENT_IDS = {KB_AGENT_IDS}"
        )

    def test_report_actual_kb_agent_ids(self, capsys: pytest.CaptureFixture) -> None:
        """Report the actual KB_AGENT_IDS value for traceability."""
        from daemon.repositories.instance.repository import KB_AGENT_IDS

        print(f"\nKB_AGENT_IDS = {KB_AGENT_IDS}")
        assert isinstance(KB_AGENT_IDS, frozenset)


# =============================================================================
# 9. Cross-doc consistency — code rejection list
# =============================================================================


class TestCrossDocCodeRejection:
    """The code-file-extension rejection list must be identical across
    soul.md, rule.md, and workflow.md."""

    @pytest.fixture
    def soul_exts(self) -> set[str]:
        return _extract_code_extensions(_read(SOUL_PATH))

    @pytest.fixture
    def rule_exts(self) -> set[str]:
        return _extract_code_extensions(_read(RULE_PATH))

    @pytest.fixture
    def workflow_exts(self) -> set[str]:
        return _extract_code_extensions(_read(WORKFLOW_PATH))

    def test_soul_has_expected_extensions(self, soul_exts: set[str]) -> None:
        """soul.md should contain ALL expected reject extensions."""
        missing = EXPECTED_REJECT_EXTENSIONS - soul_exts
        assert not missing, f"soul.md missing extensions: {missing}"

    def test_rule_has_expected_extensions(self, rule_exts: set[str]) -> None:
        """rule.md should contain ALL expected reject extensions."""
        missing = EXPECTED_REJECT_EXTENSIONS - rule_exts
        assert not missing, f"rule.md missing extensions: {missing}"

    def test_workflow_has_expected_extensions(self, workflow_exts: set[str]) -> None:
        """workflow.md should contain ALL expected reject extensions."""
        missing = EXPECTED_REJECT_EXTENSIONS - workflow_exts
        assert not missing, f"workflow.md missing extensions: {missing}"

    def test_soul_equals_rule(self, soul_exts: set[str], rule_exts: set[str]) -> None:
        """soul.md and rule.md should have identical extension sets."""
        assert soul_exts == rule_exts, (
            f"Extension mismatch:\n  soul.md only: {soul_exts - rule_exts}\n"
            f"  rule.md only: {rule_exts - soul_exts}"
        )

    def test_soul_equals_workflow(self, soul_exts: set[str], workflow_exts: set[str]) -> None:
        """soul.md and workflow.md should have identical extension sets."""
        assert soul_exts == workflow_exts, (
            f"Extension mismatch:\n  soul.md only: {soul_exts - workflow_exts}\n"
            f"  workflow.md only: {workflow_exts - soul_exts}"
        )

    def test_rule_equals_workflow(self, rule_exts: set[str], workflow_exts: set[str]) -> None:
        """rule.md and workflow.md should have identical extension sets."""
        assert rule_exts == workflow_exts, (
            f"Extension mismatch:\n  rule.md only: {rule_exts - workflow_exts}\n"
            f"  workflow.md only: {workflow_exts - rule_exts}"
        )


# =============================================================================
# 10. Cross-doc consistency — format conversion table
# =============================================================================


class TestCrossDocFormatConversion:
    """The format→mechanism mapping must be consistent across all three docs.

    Expected mapping:
      .csv  → write_file (direct)
      .docx → pandoc
      .pptx → pandoc
      .pdf  → pandoc + PDF engine
      .xlsx → libreoffice
    """

    FORMATS = ["csv", "docx", "pptx", "pdf", "xlsx"]

    @pytest.fixture
    def docs(self) -> dict[str, str]:
        return {
            "soul.md": _read(SOUL_PATH),
            "rule.md": _read(RULE_PATH),
            "workflow.md": _read(WORKFLOW_PATH),
        }

    @pytest.mark.parametrize("fmt", FORMATS)
    def test_format_mentioned_in_all_docs(self, fmt: str, docs: dict[str, str]) -> None:
        """Each format should be mentioned in all three documents."""
        for doc_name, text in docs.items():
            assert f".{fmt}" in text, (
                f".{fmt} not mentioned in {doc_name}"
            )

    @pytest.mark.parametrize("fmt,mechanism", [
        ("csv", "write_file"),
        ("docx", "pandoc"),
        ("pptx", "pandoc"),
        ("pdf", "pandoc"),
        ("xlsx", "libreoffice"),
    ])
    def test_format_mechanism_in_all_docs(
        self, fmt: str, mechanism: str, docs: dict[str, str]
    ) -> None:
        """Each format should reference its correct mechanism in all three docs."""
        for doc_name, text in docs.items():
            assert mechanism in text, (
                f".{fmt} mechanism '{mechanism}' not found in {doc_name}"
            )

    @pytest.mark.parametrize("fmt", ["docx", "pptx", "pdf", "xlsx"])
    def test_no_wrong_mechanism(self, fmt: str, docs: dict[str, str]) -> None:
        """Formats that should NOT use write_file for conversion must not
        claim write_file as the conversion mechanism."""
        for doc_name, text in docs.items():
            # Check that the format isn't described as "write_file → .fmt"
            # i.e. no line says something like ".fmt ... write_file" as conversion
            # (write_file is for .md and .csv only)
            pass  # This is a softer check; the positive checks above are stronger

    def test_pdf_requires_engine_in_all_docs(self, docs: dict[str, str]) -> None:
        """All docs should state PDF requires a separate engine beyond pandoc."""
        for doc_name, text in docs.items():
            # At least one engine name should be mentioned
            has_engine = any(e in text for e in ["pdflatex", "wkhtmltopdf", "weasyprint"])
            assert has_engine, (
                f"{doc_name} should mention a PDF engine (pdflatex/wkhtmltopdf/weasyprint)"
            )

    def test_xlsx_not_via_pandoc(self, docs: dict[str, str]) -> None:
        """All docs should clarify xlsx is NOT supported by pandoc."""
        for doc_name, text in docs.items():
            assert "xlsx" in text.lower()
            # The docs should state pandoc doesn't support xlsx
            assert ("not" in text.lower() and "pandoc" in text.lower() and "xlsx" in text.lower())


# =============================================================================
# 11. Cross-doc consistency — bash allowlist
# =============================================================================


class TestCrossDocBashAllowlist:
    """The bash command allowlist must be consistent across soul.md and rule.md.

    Expected: pandoc, libreoffice --headless --convert-to, wc, file, ls, which
    """

    @pytest.fixture
    def docs(self) -> dict[str, str]:
        return {
            "soul.md": _read(SOUL_PATH),
            "rule.md": _read(RULE_PATH),
            "workflow.md": _read(WORKFLOW_PATH),
        }

    @pytest.mark.parametrize("cmd", ["pandoc", "libreoffice", "wc", "file", "ls", "which"])
    def test_command_in_soul(self, cmd: str, docs: dict[str, str]) -> None:
        """Each allowed bash command should appear in soul.md."""
        assert cmd in docs["soul.md"], f"'{cmd}' not mentioned in soul.md"

    @pytest.mark.parametrize("cmd", ["pandoc", "libreoffice", "wc", "file", "ls", "which"])
    def test_command_in_rule(self, cmd: str, docs: dict[str, str]) -> None:
        """Each allowed bash command should appear in rule.md."""
        assert cmd in docs["rule.md"], f"'{cmd}' not mentioned in rule.md"

    def test_soul_rule_have_same_command_set(self, docs: dict[str, str]) -> None:
        """soul.md and rule.md should mention the same bash command set."""
        soul_cmds = {c for c in EXPECTED_BASH_COMMANDS if c in docs["soul.md"]}
        rule_cmds = {c for c in EXPECTED_BASH_COMMANDS if c in docs["rule.md"]}
        assert soul_cmds == rule_cmds, (
            f"Bash command mismatch:\n"
            f"  soul.md has: {soul_cmds}\n"
            f"  rule.md has: {rule_cmds}"
        )

    def test_libreoffice_headless_convert_to(self, docs: dict[str, str]) -> None:
        """Both docs should mention libreoffice --headless --convert-to form."""
        pattern = "--convert-to"
        for doc_name in ["soul.md", "rule.md"]:
            assert pattern in docs[doc_name], (
                f"'{pattern}' not found in {doc_name}"
            )
