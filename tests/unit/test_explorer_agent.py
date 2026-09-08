"""Meta-contract validation pack for the Explorer agent.

Validates the explorer agent definition:

  1. meta.json schema & required-field correctness.
  2. Tool-allowance security: ``explore`` remains DENIED (the
     explorer must not recursively spawn itself), RAG write tools
     ``rag_insert_text`` / ``rag_insert_texts`` / ``rag_query``
     remain DENIED, ``experience`` remains DENIED.
  3. Shared Context injection opt-in: the ``context_injection``
     block sets ``heuristic_match_shared_md_files=True`` (canonical
     opt-in flag; the explore tool no longer manually attaches
     Shared Context — see ``daemon/tools/knowledge_tools.py:706-714``
     for the system-driven contract).
  4. Convention compliance: prompt-file completeness.

Modelled after ``tests/unit/test_project_manager_agent.py:300-310``
(the meta-contract pin precedent for ``heuristic_match_shared_md_files``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Path constants — resolve against the REAL agents/ dir at repo root.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXPLORER_AGENT_DIR = PROJECT_ROOT / "agents" / "explorer"
META_PATH = EXPLORER_AGENT_DIR / "meta.json"
SOUL_PATH = EXPLORER_AGENT_DIR / "soul.md"
RULE_PATH = EXPLORER_AGENT_DIR / "rule.md"
WORKFLOW_PATH = EXPLORER_AGENT_DIR / "workflow.md"

PROMPT_FILES: tuple[Path, ...] = (
    SOUL_PATH,
    RULE_PATH,
    WORKFLOW_PATH,
)


def _load_meta() -> dict:
    """Load and return explorer/meta.json as a dict."""
    with open(META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _read(path: Path) -> str:
    """Read a UTF-8 text file."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Meta-contract tests
# ---------------------------------------------------------------------------


class TestExplorerMetaSchema:
    """Schema-level checks for ``agents/explorer/meta.json``."""

    def test_meta_file_exists(self) -> None:
        """``agents/explorer/meta.json`` must exist at the canonical path."""
        assert META_PATH.exists(), (
            f"explorer meta.json must exist at {META_PATH}; "
            f"got missing — repo layout invariant violated."
        )

    def test_id_matches_directory_name(self) -> None:
        """``id`` field must match the directory name."""
        meta = _load_meta()
        assert meta.get("id") == "explorer", (
            f"meta.id must equal directory name 'explorer'; got: {meta.get('id')!r}"
        )


class TestExplorerContextInjectionOptIn:
    """Pin the Shared Context injection opt-in flag.

    Migrated 2026-09: Shared Context (heuristic .md matching) is now
    driven by ``assemble_context_messages`` on the spawned explorer
    instance's first turn, gated on this flag (canonical opt-in
    pattern shared with leader / worker / coder / project-manager /
    etc.). If this assertion fails, the meta.json opt-in was lost
    and the explorer's first-turn Shared Context block silently
    drops.
    """

    def test_context_injection_heuristic_match_enabled(self) -> None:
        """``context_injection.heuristic_match_shared_md_files`` must be True.

        Modeled on ``tests/unit/test_project_manager_agent.py:300-310``.
        """
        meta = _load_meta()
        ci = meta.get("context_injection")
        assert isinstance(ci, dict), (
            f"context_injection must be a dict (object form), got: {ci!r}"
        )
        assert ci.get("heuristic_match_shared_md_files") is True, (
            f"context_injection.heuristic_match_shared_md_files must be True "
            f"so the explore tool's system-driven Shared Context block fires "
            f"on the spawned explorer's first turn. Got: {ci!r}"
        )


class TestExplorerToolSecurity:
    """Pin the tool-allowlist invariants for the explorer agent."""

    def test_explore_denied(self) -> None:
        """The ``explore`` tool must be in ``deny`` (no self-spawn)."""
        meta = _load_meta()
        deny = meta.get("tools", {}).get("deny", [])
        assert "explore" in deny, (
            f"explore must be DENIED so the explorer cannot recursively "
            f"spawn itself. Got deny list: {deny!r}"
        )

    def test_rag_write_tools_denied(self) -> None:
        """``rag_insert_text`` / ``rag_insert_texts`` / ``rag_query`` must be DENIED."""
        meta = _load_meta()
        deny = meta.get("tools", {}).get("deny", [])
        for tool_name in ("rag_insert_text", "rag_insert_texts", "rag_query"):
            assert tool_name in deny, (
                f"{tool_name} must be DENIED for the explorer (read-only RAG role). "
                f"Got deny list: {deny!r}"
            )

    def test_experience_denied(self) -> None:
        """``experience`` must be DENIED (write path is for kb-writer only)."""
        meta = _load_meta()
        deny = meta.get("tools", {}).get("deny", [])
        assert "experience" in deny, (
            f"experience must be DENIED for the explorer; the kb-writer agent "
            f"owns the knowledge-write path. Got deny list: {deny!r}"
        )


class TestExplorerPromptFiles:
    """Prompt-file completeness for the explorer agent."""

    @pytest.mark.parametrize("path", PROMPT_FILES)
    def test_prompt_file_exists_and_nonempty(self, path: Path) -> None:
        """Each prompt file exists and has non-trivial content."""
        assert path.exists(), f"{path.name} must exist for the explorer agent."
        content = _read(path).strip()
        assert content, f"{path.name} must be non-empty for the explorer agent."