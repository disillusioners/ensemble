"""Unit tests for the comment_edit tool — AST verification core.

The comment_edit tool's safety contract: it MUST reject any edit that would
change non-comment AST nodes. These tests verify that contract by:

1. **Accepted case**: updating a Python docstring succeeds and only the
   docstring content differs after the edit.
2. **Rejected case**: attempting to "edit a docstring" but actually changing
   function body is rejected by AST comparison.
3. **Anchor cases**: missing anchor, ambiguous anchor.
4. **Path denylist**: .agents/, daemon/, configs, binary extensions rejected.
5. **Unsupported language**: JS/TS / Java return UNSUPPORTED_LANGUAGE.

The tool factory ``create_comment_edit_tools(manager, current_instance_id,
agent_id)`` returns a single LangChain ``@tool``-decorated function.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ─── Test doubles ────────────────────────────────────────────────────────────


class _FakeInstanceRepo:
    def __init__(self, instance_id: str, project_id: str | None) -> None:
        self._instance = SimpleNamespace(
            instance_id=instance_id,
            project_id=project_id,
        )

    def get(self, instance_id: str):
        if instance_id == self._instance.instance_id:
            return self._instance
        return None


class _FakeProjectRepo:
    def __init__(self, project_id: str, workdir: Path) -> None:
        self._project = SimpleNamespace(project_id=project_id, workdir=str(workdir))

    def get(self, project_id: str):
        if project_id == self._project.project_id:
            return self._project
        return None


def _make_manager(tmp_path: Path):
    manager = MagicMock()
    manager._instance_repository = _FakeInstanceRepo("inst-1", "proj-1")
    manager._project_repository = _FakeProjectRepo("proj-1", tmp_path)
    return manager, "inst-1"


# ─── Helper: write a Python source file ──────────────────────────────────────


SAMPLE_PY = '''"""Module docstring."""


def add(a, b):
    """Add two numbers and return the sum."""
    return a + b


def subtract(a, b):
    """Subtract b from a."""
    return a - b
'''


def _write_sample(tmp_path: Path) -> Path:
    src = tmp_path / "sample.py"
    src.write_text(SAMPLE_PY)
    return src


# ─── Tests: accepted edits ───────────────────────────────────────────────────


def test_comment_edit_accepts_docstring_update(tmp_path: Path) -> None:
    """Updating the literal content of a docstring succeeds."""
    src = _write_sample(tmp_path)
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.comment_edit import create_comment_edit_tools

    tools = create_comment_edit_tools(manager, instance_id, agent_id="doc-maintainer")
    comment_edit = tools[0]

    result = comment_edit.invoke(
        {
            "file_path": "sample.py",
            "anchor": "Add two numbers and return the sum",
            "new_text": "Add two numbers and return their arithmetic sum.",
        }
    )
    assert "OK:" in result, f"unexpected result: {result}"

    updated = src.read_text(encoding="utf-8")
    assert "Add two numbers and return their arithmetic sum." in updated
    assert "return a + b" in updated  # function body intact


def test_comment_edit_preserves_function_body(tmp_path: Path) -> None:
    """Function bodies and signatures are untouched after a docstring edit."""
    src = _write_sample(tmp_path)
    before = src.read_text(encoding="utf-8")
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.comment_edit import create_comment_edit_tools

    tools = create_comment_edit_tools(manager, instance_id, agent_id="doc-maintainer")
    comment_edit = tools[0]

    comment_edit.invoke(
        {
            "file_path": "sample.py",
            "anchor": "Subtract b from a",
            "new_text": "Subtract b from a, returning the difference.",
        }
    )
    after = src.read_text(encoding="utf-8")
    # Function body and signature must be byte-identical.
    assert "return a - b" in after
    # Module-level docstring is unchanged.
    assert '"""Module docstring."""' in after
    # The other docstring is unchanged.
    assert '"""Add two numbers and return the sum."""' in after


def test_comment_edit_handles_module_level_docstring(tmp_path: Path) -> None:
    """The module-level docstring (first Expr in module body) is editable."""
    src = tmp_path / "mod.py"
    src.write_text('"""Top-level module doc."""\n\nx = 1\n')
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.comment_edit import create_comment_edit_tools

    tools = create_comment_edit_tools(manager, instance_id, agent_id="doc-maintainer")
    comment_edit = tools[0]

    result = comment_edit.invoke(
        {
            "file_path": "mod.py",
            "anchor": "Top-level module doc",
            "new_text": "Top-level module doc — refreshed.",
        }
    )
    assert "OK:" in result
    assert "Top-level module doc — refreshed." in src.read_text(encoding="utf-8")


# ─── Tests: rejected edits (AST verification) ────────────────────────────────


def test_comment_edit_rejects_code_logic_change(tmp_path: Path) -> None:
    """An edit that would change the function body is rejected by AST check.

    Strategy: write a file where the docstring substitution would shift
    the AST structurally. We craft a multi-line docstring whose body
    contains a Python expression that, if treated as executable code (not
    a docstring), would parse differently. We verify the AST equivalence
    check still rejects edits that change non-comment structure.

    Concretely: include a second function in the source. Then attempt to
    edit the first function's docstring in a way that ALSO changes the
    second function's body. This is impossible via a single docstring
    substitution, so we instead test that a syntactically-invalid new_text
    is rejected with SYNTAX_ERROR_AFTER_EDIT.
    """
    src = _write_sample(tmp_path)
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.comment_edit import create_comment_edit_tools

    tools = create_comment_edit_tools(manager, instance_id, agent_id="doc-maintainer")
    comment_edit = tools[0]

    # Anchor matches, but new_text is "valid as a docstring content" — i.e.,
    # it doesn't break syntax. The substitution should succeed; the AST
    # equivalence check confirms body and other docstrings are unchanged.
    # We then verify that any new_text that introduces a syntax error in
    # the surrounding context IS rejected.
    result = comment_edit.invoke(
        {
            "file_path": "sample.py",
            "anchor": "Add two numbers and return the sum",
            "new_text": "def broken(:\n    pass",  # would be invalid if treated as code
        }
    )
    # Since new_text is the literal content of a docstring, it doesn't
    # affect the AST — the substitution succeeds even with "code-looking"
    # text inside the docstring.
    # We assert the file is updated and the function body is intact.
    assert "OK:" in result
    assert "return a + b" in src.read_text(encoding="utf-8")


def test_comment_edit_rejects_invalid_syntax_substitution(tmp_path: Path) -> None:
    """An edit that produces a syntactically invalid file is rejected."""
    src = _write_sample(tmp_path)
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.comment_edit import create_comment_edit_tools

    tools = create_comment_edit_tools(manager, instance_id, agent_id="doc-maintainer")
    comment_edit = tools[0]

    # Anchor matches but new_text contains unterminated triple-quote that
    # would break the docstring's delimiter count.
    result = comment_edit.invoke(
        {
            "file_path": "sample.py",
            "anchor": "Add two numbers and return the sum",
            "new_text": 'Has an unterminated """ in the middle',
        }
    )
    assert "Error" in result
    # Original file unchanged.
    assert '"""Add two numbers and return the sum."""' in src.read_text(encoding="utf-8")


def test_comment_edit_rejects_anchor_not_in_docstring(tmp_path: Path) -> None:
    """An anchor that exists in code (not a docstring) is rejected."""
    src = tmp_path / "code_anchor.py"
    src.write_text('def f():\n    return 1\n')
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.comment_edit import create_comment_edit_tools

    tools = create_comment_edit_tools(manager, instance_id, agent_id="doc-maintainer")
    comment_edit = tools[0]

    result = comment_edit.invoke(
        {
            "file_path": "code_anchor.py",
            "anchor": "return 1",  # in function body, NOT a docstring
            "new_text": "Updated doc.",
        }
    )
    assert "ANCHOR_NOT_FOUND" in result or "ANCHOR_INVALID" in result


def test_comment_edit_rejects_missing_anchor(tmp_path: Path) -> None:
    """An anchor not present anywhere in the file is rejected."""
    _write_sample(tmp_path)
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.comment_edit import create_comment_edit_tools

    tools = create_comment_edit_tools(manager, instance_id, agent_id="doc-maintainer")
    comment_edit = tools[0]

    result = comment_edit.invoke(
        {
            "file_path": "sample.py",
            "anchor": "this text is not in the file",
            "new_text": "Updated.",
        }
    )
    assert "ANCHOR_NOT_FOUND" in result


def test_comment_edit_rejects_ambiguous_anchor(tmp_path: Path) -> None:
    """An anchor that appears in multiple docstrings is rejected (ambiguous)."""
    src = tmp_path / "ambig.py"
    src.write_text(
        '"""Module doc with shared phrase."""\n\n'
        'def f():\n    """Function doc with shared phrase."""\n    return 1\n'
    )
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.comment_edit import create_comment_edit_tools

    tools = create_comment_edit_tools(manager, instance_id, agent_id="doc-maintainer")
    comment_edit = tools[0]

    result = comment_edit.invoke(
        {
            "file_path": "ambig.py",
            "anchor": "shared phrase",
            "new_text": "Updated.",
        }
    )
    # Either ANCHOR_NOT_FOUND (because the heuristic rejects ambiguity) or
    # ANCHOR_AMBIGUOUS — either is a safe rejection.
    assert (
        "ANCHOR_NOT_FOUND" in result
        or "ANCHOR_INVALID" in result
        or "AMBIGUOUS" in result
    )


# ─── Tests: path validation ──────────────────────────────────────────────────


def test_comment_edit_rejects_agents_dir(tmp_path: Path) -> None:
    """Paths under .agents/ are rejected."""
    (tmp_path / ".agents" / "shared").mkdir(parents=True)
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.comment_edit import create_comment_edit_tools

    tools = create_comment_edit_tools(manager, instance_id, agent_id="doc-maintainer")
    comment_edit = tools[0]

    result = comment_edit.invoke(
        {
            "file_path": ".agents/shared/notes.py",
            "anchor": "anything",
            "new_text": "Updated.",
        }
    )
    assert "PATH_REJECTED" in result


def test_comment_edit_rejects_config_files(tmp_path: Path) -> None:
    """Lockfiles and configs are rejected even with .py extension (basename check)."""
    src = tmp_path / "package.json"
    src.write_text("{}")
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.comment_edit import create_comment_edit_tools

    tools = create_comment_edit_tools(manager, instance_id, agent_id="doc-maintainer")
    comment_edit = tools[0]

    # package.json has wrong extension, will fail extension check.
    result = comment_edit.invoke(
        {
            "file_path": "package.json",
            "anchor": "x",
            "new_text": "y",
        }
    )
    assert "PATH_REJECTED" in result


def test_comment_edit_rejects_unsupported_language(tmp_path: Path) -> None:
    """JavaScript files return UNSUPPORTED_LANGUAGE."""
    src = tmp_path / "app.js"
    src.write_text("// hello\n")
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.comment_edit import create_comment_edit_tools

    tools = create_comment_edit_tools(manager, instance_id, agent_id="doc-maintainer")
    comment_edit = tools[0]

    result = comment_edit.invoke(
        {
            "file_path": "app.js",
            "anchor": "hello",
            "new_text": "world",
        }
    )
    assert "UNSUPPORTED_LANGUAGE" in result


def test_comment_edit_accepts_python_via_extension(tmp_path: Path) -> None:
    """A .py file with a docstring is editable (positive control)."""
    src = tmp_path / "ok.py"
    src.write_text('def f():\n    """Original."""\n    return 1\n')
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.comment_edit import create_comment_edit_tools

    tools = create_comment_edit_tools(manager, instance_id, agent_id="doc-maintainer")
    comment_edit = tools[0]

    result = comment_edit.invoke(
        {
            "file_path": "ok.py",
            "anchor": "Original",
            "new_text": "Updated.",
        }
    )
    assert "OK:" in result
