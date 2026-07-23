"""Pydantic schemas for the workspace viewer REST API.

Phase 1 of the Workspace Viewer — request/response shapes for the
filesystem-tree, file-content, and git-diff endpoints exposed under
``/api/workspace``. The schemas are intentionally decoupled from
the filesystem/git plumbing so the wire format can evolve without
touching the underlying ``pathlib``/``git``/``difflib`` code in
:mod:`daemon.routers.workspace`.

Module map (consumed by :mod:`daemon.routers.workspace`):

* :class:`FileTreeNode` — single node in a recursive file tree
  (self-referencing via ``children``).
* :class:`FileTreeResponse` — body for ``GET /api/workspace/tree``.
* :class:`FileContentResponse` — body for
  ``GET /api/workspace/file``.
* :class:`GitDiffResponse` — body for ``GET /api/workspace/diff``.
* :class:`WorkspaceErrorResponse` — uniform error envelope shared
  across all workspace endpoints.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FileTreeNode(BaseModel):
    """A single node in the workspace file tree.

    The model is recursive — ``children`` references ``FileTreeNode``
    again. ``children`` is ``None`` for leaf files and for
    directories that have not yet been expanded by the client; the
    router expands a directory on demand (server keeps the cost of
    walking bounded with depth/file-count limits surfaced via
    :attr:`FileTreeResponse.truncated`).

    Attributes:
        name: Display name of the entry (basename, not the full
            path).
        path: Path relative to the project's workdir. Uses forward
            slashes regardless of host OS.
        type: One of ``"file"``, ``"directory"``, or ``"symlink"``.
        size: Byte size of the entry; ``None`` for directories
            (size of a directory is platform-dependent) and
            symlinks (router resolves the target before reporting).
        children: Child nodes. ``None`` means the directory has not
            been expanded yet; an empty list means an empty
            directory.
    """

    name: str = Field(..., description="Display name (basename)")
    path: str = Field(..., description="Path relative to workdir")
    type: str = Field(..., description="file | directory | symlink")
    size: int | None = Field(
        default=None, description="Byte size for files only"
    )
    children: list[FileTreeNode] | None = Field(
        default=None,
        description="Child nodes; None means the directory has not been expanded",
    )


class FileTreeResponse(BaseModel):
    """Response body for ``GET /api/workspace/tree``.

    Attributes:
        project_id: Owning project ID.
        path: Root path this tree was built from (relative to
            workdir; ``""`` for the project root).
        tree: Top-level entries at ``path``. Empty if the directory
            is empty.
        truncated: ``True`` when the router hit the depth or
            file-count limit while walking; the client should warn
            the user and offer to widen the limit.
    """

    project_id: str = Field(..., description="Owning project ID")
    path: str = Field(..., description="Root path of this tree node")
    tree: list[FileTreeNode] = Field(
        default_factory=list, description="Top-level entries"
    )
    truncated: bool = Field(
        default=False,
        description="True if depth/file-count limit was hit",
    )


class FileContentResponse(BaseModel):
    """Response body for ``GET /api/workspace/file``.

    Attributes:
        project_id: Owning project ID.
        path: Path of the file relative to workdir.
        content: File contents decoded as UTF-8 with replacement.
            For binary files the field is empty and ``binary`` is
            ``True``.
        language: Detected programming language (extension-based),
            or ``None`` if unknown.
        total_lines: Total line count of the file (post-decode).
        offset: 1-indexed starting line of ``content``.
        limit: Maximum number of lines returned in ``content``.
        truncated: ``True`` when ``content`` does not cover the full
            file (either ``offset > 1`` or ``offset + limit - 1 <
            total_lines``).
        binary: ``True`` when the file was detected as binary;
            ``content`` will be empty in that case.
        size_bytes: Exact byte size of the file on disk.
    """

    project_id: str = Field(..., description="Owning project ID")
    path: str = Field(..., description="Path relative to workdir")
    content: str = Field(default="", description="File contents")
    language: str | None = Field(
        default=None, description="Detected language from extension"
    )
    total_lines: int = Field(..., description="Total line count of the file")
    offset: int = Field(default=1, description="1-indexed start line")
    limit: int = Field(default=2000, description="Maximum lines returned")
    truncated: bool = Field(
        default=False, description="True if content is a window, not the full file"
    )
    binary: bool = Field(
        default=False, description="True if the file was detected as binary"
    )
    size_bytes: int = Field(..., description="Exact byte size on disk")


class GitDiffResponse(BaseModel):
    """Response body for ``GET /api/workspace/diff``.

    When the project is not a git repository the response carries
    ``has_changes=False`` and a descriptive ``error`` string (e.g.
    ``"not_a_git_repo"``); the content fields stay ``None`` so the
    frontend can render a clean empty state without special-casing
    404s.

    Attributes:
        project_id: Owning project ID.
        path: Path of the file relative to workdir.
        has_changes: ``True`` if HEAD differs from the working tree
            at ``path``.
        diff: Unified diff text comparing HEAD to the working tree,
            or ``None`` when there are no changes / no git repo.
        head_content: HEAD version of the file (``None`` if the
            file is untracked or HEAD does not contain it).
        working_content: Working-tree version of the file (``None``
            if the file was deleted).
        error: Error discriminator (``"not_a_git_repo"``,
            ``"binary_file"``, ``"file_too_large"``, …). ``None``
            on success.
    """

    project_id: str = Field(..., description="Owning project ID")
    path: str = Field(..., description="Path relative to workdir")
    has_changes: bool = Field(
        ..., description="True if HEAD differs from the working tree"
    )
    diff: str | None = Field(
        default=None, description="Unified diff text (HEAD vs working tree)"
    )
    head_content: str | None = Field(
        default=None, description="HEAD version of the file"
    )
    working_content: str | None = Field(
        default=None, description="Working-tree version of the file"
    )
    error: str | None = Field(
        default=None, description="Error discriminator (e.g. not_a_git_repo)"
    )


class FileWriteRequest(BaseModel):
    """Request body for ``PUT /api/workspace/file``.

    Attributes:
        path: Path of the file relative to workdir (where to write).
        content: UTF-8 string content to write to the file. Empty
            string is permitted (creates an empty file).
    """

    path: str = Field(..., description="Path relative to workdir")
    content: str = Field(..., description="UTF-8 content to write")


class FileWriteResponse(BaseModel):
    """Response body for ``PUT /api/workspace/file``.

    Attributes:
        project_id: Owning project ID.
        path: Path of the file relative to workdir (as requested).
        size_bytes: Exact byte size of the written content (UTF-8
            encoded length of ``content``).
        saved: Always ``True`` on success; included so the client can
            treat the response as a discriminated success/failure
            envelope without inspecting status codes.
    """

    project_id: str = Field(..., description="Owning project ID")
    path: str = Field(..., description="Path relative to workdir")
    size_bytes: int = Field(..., description="Byte size of the written content")
    saved: bool = Field(default=True, description="True on successful write")


class WorkspaceErrorResponse(BaseModel):
    """Uniform error envelope for all workspace endpoints.

    Attributes:
        error: Short machine-readable error code (e.g.
            ``"path_outside_workdir"``, ``"file_not_found"``,
            ``"permission_denied"``).
        detail: Optional human-readable explanation suitable for
            surfacing in the UI.
    """

    error: str = Field(..., description="Short error code")
    detail: str | None = Field(default=None, description="Human-readable detail")


# Resolve the self-reference in ``FileTreeNode.children``. With
# ``from __future__ import annotations`` the annotation is a string
# at class-build time; ``model_rebuild()`` makes Pydantic v2 walk
# the model graph and rebuild the forward reference now that the
# class object is fully defined.
FileTreeNode.model_rebuild()


__all__ = [
    "FileTreeNode",
    "FileTreeResponse",
    "FileContentResponse",
    "GitDiffResponse",
    "FileWriteRequest",
    "FileWriteResponse",
    "WorkspaceErrorResponse",
]