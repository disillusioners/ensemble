"""Unit tests for the doc_write tool.

Covers path allowlist/denylist enforcement, mode validation, atomic write,
and the binary-rejection path.

The tool factory ``create_doc_write_tools(manager, current_instance_id,
agent_id)`` returns a list containing a single LangChain ``@tool``-decorated
function. We drive the tool as a regular function (it is not async).
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


def _make_manager(tmp_path: Path, project_id: str = "proj-1", instance_id: str = "inst-1"):
    manager = MagicMock()
    manager._instance_repository = _FakeInstanceRepo(instance_id, project_id)
    manager._project_repository = _FakeProjectRepo(project_id, tmp_path)
    return manager, instance_id


# ─── Tests ───────────────────────────────────────────────────────────────────


def test_doc_write_accepts_docs_path(tmp_path: Path) -> None:
    """docs/api.md is accepted and the file is written."""
    (tmp_path / "docs").mkdir()
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.doc_write import create_doc_write_tools

    tools = create_doc_write_tools(manager, instance_id, agent_id="doc-maintainer")
    assert len(tools) == 1
    doc_write = tools[0]

    result = doc_write.invoke(
        {"path": "docs/api.md", "content": "# API\nHello", "mode": "create"}
    )
    assert "OK:" in result, f"unexpected result: {result}"

    written = (tmp_path / "docs" / "api.md").read_text(encoding="utf-8")
    assert written == "# API\nHello"


def test_doc_write_rejects_agents_dir(tmp_path: Path) -> None:
    """Paths under .agents/ are rejected even if the file extension is .md."""
    (tmp_path / ".agents").mkdir()
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.doc_write import create_doc_write_tools

    tools = create_doc_write_tools(manager, instance_id, agent_id="doc-maintainer")
    doc_write = tools[0]

    result = doc_write.invoke(
        {"path": ".agents/shared/notes.md", "content": "x", "mode": "create"}
    )
    assert "PATH_REJECTED" in result
    assert not (tmp_path / ".agents" / "shared" / "notes.md").exists()


def test_doc_write_rejects_daemon_dir(tmp_path: Path) -> None:
    """daemon/ paths are rejected."""
    (tmp_path / "daemon").mkdir()
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.doc_write import create_doc_write_tools

    tools = create_doc_write_tools(manager, instance_id, agent_id="doc-maintainer")
    doc_write = tools[0]

    result = doc_write.invoke(
        {"path": "daemon/foo.md", "content": "x", "mode": "create"}
    )
    assert "PATH_REJECTED" in result
    assert not (tmp_path / "daemon" / "foo.md").exists()


def test_doc_write_rejects_binary_extension(tmp_path: Path) -> None:
    """Binary extensions (e.g., .png) are rejected even under docs/."""
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.doc_write import create_doc_write_tools

    tools = create_doc_write_tools(manager, instance_id, agent_id="doc-maintainer")
    doc_write = tools[0]

    result = doc_write.invoke(
        {"path": "docs/image.png", "content": "binary", "mode": "create"}
    )
    assert "BINARY_REJECTED" in result


def test_doc_write_rejects_create_when_file_exists(tmp_path: Path) -> None:
    """mode=create fails if the file already exists."""
    (tmp_path / "docs").mkdir()
    target = tmp_path / "docs" / "exists.md"
    target.write_text("original")

    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.doc_write import create_doc_write_tools

    tools = create_doc_write_tools(manager, instance_id, agent_id="doc-maintainer")
    doc_write = tools[0]

    result = doc_write.invoke(
        {"path": "docs/exists.md", "content": "new", "mode": "create"}
    )
    assert "MODE_REJECTED" in result
    # Original content preserved.
    assert target.read_text(encoding="utf-8") == "original"


def test_doc_write_update_overwrites(tmp_path: Path) -> None:
    """mode=update overwrites an existing file."""
    (tmp_path / "docs").mkdir()
    target = tmp_path / "docs" / "update.md"
    target.write_text("old")

    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.doc_write import create_doc_write_tools

    tools = create_doc_write_tools(manager, instance_id, agent_id="doc-maintainer")
    doc_write = tools[0]

    result = doc_write.invoke(
        {"path": "docs/update.md", "content": "new", "mode": "update"}
    )
    assert "OK:" in result
    assert target.read_text(encoding="utf-8") == "new"


def test_doc_write_rejects_absolute_path(tmp_path: Path) -> None:
    """Absolute paths are rejected."""
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.doc_write import create_doc_write_tools

    tools = create_doc_write_tools(manager, instance_id, agent_id="doc-maintainer")
    doc_write = tools[0]

    result = doc_write.invoke(
        {
            "path": str(tmp_path / "docs" / "absolute.md"),
            "content": "x",
            "mode": "create",
        }
    )
    assert "PATH_ABSOLUTE" in result


def test_doc_write_rejects_path_traversal(tmp_path: Path) -> None:
    """Paths that escape via ../ are rejected."""
    (tmp_path / "docs").mkdir()
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.doc_write import create_doc_write_tools

    tools = create_doc_write_tools(manager, instance_id, agent_id="doc-maintainer")
    doc_write = tools[0]

    # docs/../secrets.md resolves outside tmp_path → rejected.
    result = doc_write.invoke(
        {"path": "docs/../secrets.md", "content": "x", "mode": "create"}
    )
    assert "PATH_REJECTED" in result
    assert not (tmp_path / "secrets.md").exists()


def test_doc_write_accepts_top_level_markdown(tmp_path: Path) -> None:
    """Top-level *.md files are allowed (e.g., README.md)."""
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.doc_write import create_doc_write_tools

    tools = create_doc_write_tools(manager, instance_id, agent_id="doc-maintainer")
    doc_write = tools[0]

    result = doc_write.invoke(
        {"path": "README.md", "content": "# Project", "mode": "create"}
    )
    assert "OK:" in result
    assert (tmp_path / "README.md").exists()


def test_doc_write_rejects_nested_random_md(tmp_path: Path) -> None:
    """Nested *.md files outside docs/ are rejected."""
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.doc_write import create_doc_write_tools

    tools = create_doc_write_tools(manager, instance_id, agent_id="doc-maintainer")
    doc_write = tools[0]

    result = doc_write.invoke(
        {"path": "src/notes.md", "content": "x", "mode": "create"}
    )
    assert "PATH_REJECTED" in result


def test_doc_write_rejects_invalid_mode(tmp_path: Path) -> None:
    """mode='delete' is rejected (no delete mode exists)."""
    manager, instance_id = _make_manager(tmp_path)
    from daemon.tools.doc_write import create_doc_write_tools

    tools = create_doc_write_tools(manager, instance_id, agent_id="doc-maintainer")
    doc_write = tools[0]

    result = doc_write.invoke(
        {"path": "docs/x.md", "content": "x", "mode": "delete"}
    )
    assert "MODE_REJECTED" in result
