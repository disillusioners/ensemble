"""Unit tests for ``LiveViewsRootConfig`` (HARDENING M11).

The validator on ``LiveViewsRootConfig`` is the loud-fail-at-load
gate the M11 hardening added: a structurally invalid root entry
(empty path on a directory-backed root, ``path`` set on a
``tmp_images`` root, ``required_rel_subpath`` on a non-project_scoped
type, or an empty segment in the subpath) must raise a
``ValidationError`` at config load — the resolver would catch most
of these at request time and serve the uniform 404, but a config
that boots with a broken root is operationally worse than one that
refuses to boot. First release has no legacy configs to break.

Test env: ``uv`` not on PATH; the convention is
``/home/nea/ensemble-src/.venv/bin/python -m pytest`` from the
worktree.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from daemon.config import LiveViewsConfig, LiveViewsRootConfig


# ── filesystem / project_scoped path-required gate ────────────────


class TestPathRequiredForDirectoryBackedTypes:
    """``filesystem`` and ``project_scoped`` roots MUST have a
    non-empty ``path``; the resolver cannot anchor a directory
    against ``None``."""

    def test_filesystem_root_with_empty_path_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(type="filesystem", path="")
        # The actionable message names the type and the bad
        # path value so an operator can find the misconfig
        # from a single log line.
        assert "filesystem" in str(excinfo.value)
        assert "path" in str(excinfo.value).lower()

    def test_filesystem_root_with_whitespace_path_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(type="filesystem", path="   ")
        assert "filesystem" in str(excinfo.value)

    def test_filesystem_root_with_none_path_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(type="filesystem", path=None)
        assert "filesystem" in str(excinfo.value)

    def test_project_scoped_root_with_empty_path_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(type="project_scoped", path="")
        assert "project_scoped" in str(excinfo.value)

    def test_filesystem_root_with_valid_path_passes(self, tmp_path):
        # Sanity: a real path does NOT trigger the validator.
        LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path / "root")
        )

    def test_project_scoped_root_with_valid_path_passes(self):
        LiveViewsRootConfig(
            type="project_scoped", path=".agents/shared/planning"
        )


# ── tmp_images path-must-be-unset gate ───────────────────────────


class TestTmpImagesPathUnset:
    """``tmp_images`` roots MUST NOT have ``path`` set — the
    resolver delegates to the per-app ``TmpImageStore``
    substrate. A ``path`` on a ``tmp_images`` root is almost
    certainly a miscopy from one of the other two types."""

    def test_tmp_images_root_with_path_raises(self, tmp_path):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(type="tmp_images", path=str(tmp_path / "x"))
        msg = str(excinfo.value)
        assert "tmp_images" in msg
        # The actionable message hints at the cure (switch
        # type), so an operator doesn't just delete the
        # path and ship a misconfigured filesystem root.
        assert "filesystem" in msg or "project_scoped" in msg

    def test_tmp_images_root_with_none_path_passes(self):
        # Sanity: the seeded tmp-images shape is the
        # default (no path).
        LiveViewsRootConfig(type="tmp_images", path=None)

    def test_tmp_images_root_with_empty_path_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(type="tmp_images", path="")
        assert "tmp_images" in str(excinfo.value)


# ── required_rel_subpath type + element gate ──────────────────────


class TestRequiredRelSubpathGating:
    """``required_rel_subpath`` is a project_scoped-only
    structural gate; setting it on filesystem / tmp_images is
    silently ignored at request time and a config bug we want
    to surface now."""

    def test_filesystem_root_with_required_rel_subpath_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(
                type="filesystem",
                path="/tmp/x",
                required_rel_subpath=["design", "mockups"],
            )
        msg = str(excinfo.value)
        assert "required_rel_subpath" in msg
        assert "project_scoped" in msg

    def test_tmp_images_root_with_required_rel_subpath_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(
                type="tmp_images",
                required_rel_subpath=["design", "mockups"],
            )
        msg = str(excinfo.value)
        assert "required_rel_subpath" in msg

    def test_empty_segment_in_required_rel_subpath_raises(self):
        with pytest.raises(ValidationError) as excinfo:
            LiveViewsRootConfig(
                type="project_scoped",
                path=".agents/shared/planning",
                required_rel_subpath=["design", ""],
            )
        msg = str(excinfo.value)
        # The actionable message points at the segment-level
        # bug, not just the field name.
        assert "empty segment" in msg or "empty" in msg

    def test_valid_required_rel_subpath_passes(self):
        # Sanity: the seeded designer-artifact shape.
        LiveViewsRootConfig(
            type="project_scoped",
            path=".agents/shared/planning",
            required_rel_subpath=["design", "mockups"],
        )


# ── LiveViewsConfig integration: seeded defaults still load ──────


class TestLiveViewsConfigSeeds:
    """The seeded Phase-1 roots must continue to load cleanly
    after the M11 validator lands. First-release breakage of
    the seeded defaults would be a HARD regression."""

    def test_seeded_designer_artifact_loads(self):
        # Force the seeds by constructing a LiveViewsConfig
        # with no operator override and reading the seeded
        # dict back. The seed function is canonical (it's
        # the operator-override-model source of truth in
        # ``daemon/config.py``).
        from daemon.config import _seed_phase1_roots

        seeds = _seed_phase1_roots()
        assert "designer-artifact" in seeds
        assert "planning" in seeds
        assert "tmp-images" in seeds
        # The validator ran on each seed entry as it was
        # constructed (the M11 invariant). If any seed
        # violates the new rules, this import would have
        # raised at module load.
        assert seeds["designer-artifact"].type == "project_scoped"
        assert seeds["designer-artifact"].path == ".agents/shared/planning"
        assert seeds["planning"].type == "project_scoped"
        assert seeds["tmp-images"].type == "tmp_images"
        assert seeds["tmp-images"].path is None
