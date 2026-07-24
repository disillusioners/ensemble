"""Focused unit tests for ``WorkspaceGuard.resolve_strict()`` for vscode-folder.

These tests pin the path-validation behavior used by
``GET /api/projects/{project_id}/vscode-folder`` — the HTTP endpoint that
returns the validated workspace folder for the VS Code server's ``?folder=``
query. The router hands the project's ``main_directory`` to
``resolve_strict()`` and uses the result to:

    * return ``{"folder": ...}`` on success, OR
    * 403 the request when the resolved path escapes the workdir.

The bug class this guards against: a malicious or typo'd ``main_directory``
in the projects table letting the ``?folder=`` URL target arbitrary paths
on the host. ``resolve_strict()`` enforces containment unconditionally for
*every* path shape (absolute or relative), unlike ``resolve()`` which
exempts absolute paths.

Required scenarios (from vscode-server-editor review):

    * ``R3`` — ``/etc`` outside allowed root → error (this was the original
      bug; ``resolve()`` would have allowed it because of the absolute-path
      exemption).
    * ``../etc/passwd`` traversal → error.
    * ``/`` (root) → error.
    * ``/nonexistent`` → error.
    * symlink pointing outside workdir → error.
    * valid subdir inside workdir → resolved path.

Run only this file::

    python -m pytest tests/unit/test_vscode_path_validation.py -v
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from daemon.services.workspace_guard import WorkspaceGuard


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def workdir(tmp_path):
    """A scratch workdir with a small tree.

    Layout::

        workdir/
            project_root/      ← symlink target inside workdir
            deep_dir/
                nested_file.txt
    """
    root = tmp_path
    project_root = root / "project_root"
    project_root.mkdir()
    deep = root / "deep_dir"
    deep.mkdir()
    (deep / "nested_file.txt").write_text("hello", encoding="utf-8")
    # A second workdir completely outside ``workdir`` and outside any
    # tempdir, so symlink-escape tests have a guaranteed outside target.
    return root


@pytest.fixture
def outside_dir(tmp_path):
    """A second tempdir guaranteed to be outside ``workdir``.

    NOTE: on macOS ``TemporaryDirectory`` resolves through ``/var/folders/.../T/``
    whose canonical path is ``/private/tmp``, which the WorkspaceGuard
    explicitly allows via the tempdir exemption. Tests that need a real
    "outside" target disable that exemption with the ``disable_tempdir_allowance``
    fixture below.
    """
    outside = tmp_path.parent / "outside_for_vscode_guard"
    outside.mkdir(exist_ok=True)
    try:
        yield outside
    finally:
        # Cleanup the file/dirs we created (not the parent — pytest owns it).
        for child in outside.iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                # Best-effort — leave it if rmdir fails.
                try:
                    child.rmdir()
                except OSError:
                    pass
        try:
            outside.rmdir()
        except OSError:
            pass


@pytest.fixture
def disable_tempdir_allowance(monkeypatch):
    """Force ``_is_in_temp_dir`` to return False so strict-boundary tests
    can use a sibling ``outside_dir`` without the tempdir exemption
    sneaking the path through.
    """
    monkeypatch.setattr(
        WorkspaceGuard, "_is_in_temp_dir", staticmethod(lambda _: False)
    )


# ─────────────────────────────────────────────────────────────────────────────
# R3: absolute path outside workdir — the original bug
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveStrictAbsoluteOutside:
    """``resolve_strict()`` must reject absolute paths outside the workdir."""

    def test_etc_rejected(self, workdir):
        """R3 (regression): ``/etc`` is outside the workdir root → error.

        The pre-fix ``resolve()`` accepted ``/etc`` because of the
        absolute-path exemption. ``resolve_strict()`` enforces containment
        uniformly and rejects it. This is the headline regression test.
        """
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve_strict("/etc")
        assert resolved is None
        assert err is not None
        assert "escapes workdir" in err

    def test_etc_passwd_rejected(self, workdir):
        """Classic ``/etc/passwd`` is outside → error."""
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve_strict("/etc/passwd")
        assert resolved is None
        assert err is not None
        assert "escapes workdir" in err

    def test_tmp_etc_escapes_rejected(self, workdir, disable_tempdir_allowance):
        """``/tmp/...`` must be rejected by ``resolve_strict`` (no tempdir carve-out)."""
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve_strict("/tmp/secret.txt")
        assert resolved is None
        assert err is not None


# ─────────────────────────────────────────────────────────────────────────────
# Path traversal
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveStrictTraversal:
    """``resolve_strict()`` must reject ``..`` traversal."""

    def test_dotdot_traversal_rejected(
        self, workdir, outside_dir, disable_tempdir_allowance
    ):
        """``../<sibling>`` traversal is rejected even when the target exists."""
        bad = "../" + os.path.basename(str(outside_dir)) + "/secret.txt"
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve_strict(bad)
        assert resolved is None
        assert err is not None
        assert "escapes workdir" in err

    def test_multiple_dotdot_traversal_rejected(self, workdir):
        """``../../../etc/passwd`` is rejected.

        Mirrors the regression coverage in the existing workspace_guard
        tests; ``resolve_strict`` must reject traversal even when the
        original path string is purely relative.
        """
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve_strict("../../../etc/passwd")
        assert resolved is None
        assert err is not None
        assert "escapes workdir" in err


# ─────────────────────────────────────────────────────────────────────────────
# Root + non-existent
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveStrictRootAndMissing:
    """Special-path edge cases that the vscode-folder endpoint must catch."""

    def test_root_rejected(self, workdir):
        """``/`` resolves outside the workdir root → error.

        Without ``resolve_strict``'s boundary enforcement, ``/`` would be
        silently accepted (as an absolute path), letting the front-end
        point the VS Code server at the host root.
        """
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve_strict("/")
        assert resolved is None
        assert err is not None
        assert "escapes workdir" in err

    def test_nonexistent_path_rejected(self, workdir):
        """``/nonexistent`` resolves to a real OS path but is outside workdir → error.

        ``resolve_strict`` operates on the canonicalized path string — the
        target's existence is irrelevant to the boundary check.
        """
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve_strict("/nonexistent")
        assert resolved is None
        assert err is not None
        assert "escapes workdir" in err

    def test_empty_relative_path_rejected(self, workdir):
        """Empty relative path triggers the workdir-required branch."""
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve_strict("")
        assert resolved is None
        assert err is not None
        # Empty relative path errors out at ``_resolve_target`` with the
        # workdir-required message rather than the boundary message.
        assert "workdir is required" in err or "invalid" in err.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Symlinks
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveStrictSymlinks:
    """``resolve_strict`` follows symlinks and rejects escapes."""

    def test_symlink_to_outside_rejected(
        self, workdir, outside_dir, disable_tempdir_allowance
    ):
        """A symlink inside workdir pointing outside is rejected.

        ``Path.resolve()`` follows symlinks; the canonicalized target
        lands outside the workdir. With the tempdir exemption off, the
        boundary check fails.
        """
        outside_file = outside_dir / "secret.txt"
        outside_file.write_text("top secret", encoding="utf-8")

        link = workdir / "link_to_outside"
        try:
            link.symlink_to(outside_file)
            guard = WorkspaceGuard(str(workdir))
            resolved, err = guard.resolve_strict("link_to_outside")
            assert resolved is None
            assert err is not None
            assert "escapes workdir" in err
        finally:
            link.unlink(missing_ok=True)
            outside_file.unlink(missing_ok=True)

    def test_internal_symlink_allowed(self, workdir):
        """A symlink that resolves INSIDE the workdir is allowed through.

        Symlinks are only a security risk when their target escapes the
        boundary; ``Path.resolve()`` follows them and the boundary check
        operates on the canonical target.
        """
        target = workdir / "deep_dir" / "nested_file.txt"
        link = workdir / "alias"
        try:
            link.symlink_to(target)
            guard = WorkspaceGuard(str(workdir))
            resolved, err = guard.resolve_strict("alias")
            assert err is None
            assert resolved is not None
            assert resolved == target.resolve()
        finally:
            link.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────


class TestResolveStrictHappyPath:
    """``resolve_strict()`` succeeds for paths inside the workdir."""

    def test_relative_subdir_inside_workdir(self, workdir):
        """A relative path to an existing subdir returns the resolved Path."""
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve_strict("deep_dir")
        assert err is None
        assert resolved is not None
        assert resolved == (workdir / "deep_dir").resolve()

    def test_dot_resolves_to_workdir(self, workdir):
        """``./`` resolves to the workdir itself — a valid containment target."""
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve_strict("./")
        assert err is None
        assert resolved == guard.workdir

    def test_nested_file_inside_workdir(self, workdir):
        """A nested file inside the workdir is returned by resolved path."""
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve_strict("deep_dir/nested_file.txt")
        assert err is None
        assert resolved is not None
        assert resolved == (workdir / "deep_dir" / "nested_file.txt").resolve()
