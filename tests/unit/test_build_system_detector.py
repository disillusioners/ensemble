"""Unit tests for the build_system_detector.

The detector is a pure function (file-presence heuristic) — no subprocess
calls. It returns a BuildSystem or None based on which marker files exist
in the workdir.

Markers tested (in priority order):
  * package.json   → npm test
  * pyproject.toml → pytest -x
  * pytest.ini     → pytest -x
  * Makefile       → make test (only if `test:` target exists)
  * Cargo.toml     → cargo test
  * go.mod         → go test ./...
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.services.build_system_detector import (
    BuildSystem,
    detect,
    _makefile_has_test_target,
)


def test_detect_package_json(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}")
    bs = detect(tmp_path)
    assert bs is not None
    assert bs.name == "npm"
    assert bs.cmd == ["npm", "test"]


def test_detect_pyproject_toml(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest]")
    bs = detect(tmp_path)
    assert bs is not None
    assert bs.name == "pytest"
    assert bs.cmd == ["pytest", "-x"]


def test_detect_pytest_ini(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]")
    bs = detect(tmp_path)
    assert bs is not None
    assert bs.name == "pytest"


def test_detect_makefile_with_test_target(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("test:\n\tpytest\n")
    bs = detect(tmp_path)
    assert bs is not None
    assert bs.name == "make"
    assert bs.cmd == ["make", "test"]


def test_detect_makefile_without_test_target_returns_none(tmp_path: Path) -> None:
    """A Makefile with no `test:` target does NOT match."""
    (tmp_path / "Makefile").write_text("build:\n\techo build\n")
    bs = detect(tmp_path)
    assert bs is None


def test_detect_makefile_with_only_commented_test_target(tmp_path: Path) -> None:
    """A `test:` mention inside a comment does NOT count."""
    (tmp_path / "Makefile").write_text("# test:\nbuild:\n\techo build\n")
    bs = detect(tmp_path)
    assert bs is None


def test_detect_makefile_phony_test(tmp_path: Path) -> None:
    """.PHONY: test declaration counts as a test target."""
    (tmp_path / "Makefile").write_text(".PHONY: test build\ntest:\n\tpytest\nbuild:\n\techo\n")
    bs = detect(tmp_path)
    assert bs is not None
    assert bs.name == "make"


def test_detect_cargo_toml(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'")
    bs = detect(tmp_path)
    assert bs is not None
    assert bs.name == "cargo"
    assert bs.cmd == ["cargo", "test"]


def test_detect_go_mod(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module foo\n")
    bs = detect(tmp_path)
    assert bs is not None
    assert bs.name == "go"
    assert bs.cmd == ["go", "test", "./..."]


def test_detect_no_markers_returns_none(tmp_path: Path) -> None:
    """A docs-only or empty repo returns None (skip validation)."""
    bs = detect(tmp_path)
    assert bs is None


def test_detect_override_replaces_command(tmp_path: Path) -> None:
    """The project-metadata override replaces the detected command."""
    (tmp_path / "package.json").write_text("{}")
    bs = detect(tmp_path, override_cmd="npm run lint-docs")
    assert bs is not None
    assert bs.name == "override"
    assert bs.cmd == ["npm", "run", "lint-docs"]


def test_detect_override_with_no_markers(tmp_path: Path) -> None:
    """Override works even when no markers are present."""
    bs = detect(tmp_path, override_cmd="make check-docs")
    assert bs is not None
    assert bs.name == "override"
    assert bs.cmd == ["make", "check-docs"]


def test_detect_override_complex_shlex(tmp_path: Path) -> None:
    """Override commands are parsed via shlex (handles quoted args)."""
    bs = detect(tmp_path, override_cmd='bash -c "echo hello && exit 1"')
    assert bs is not None
    # shlex.split handles the quoted string properly.
    assert bs.cmd[0] == "bash"
    assert bs.cmd[1] == "-c"


def test_makefile_test_target_detection_helper(tmp_path: Path) -> None:
    """Direct test of _makefile_has_test_target for the boundary cases."""
    m = tmp_path / "Makefile"
    # Case 1: target present.
    m.write_text("test:\n\tpytest\n")
    assert _makefile_has_test_target(m) is True
    # Case 2: only in comment.
    m.write_text("# test:\nbuild:\n\techo\n")
    assert _makefile_has_test_target(m) is False
    # Case 3: missing file (unreadable).
    assert _makefile_has_test_target(tmp_path / "missing") is False


def test_build_system_default_timeout() -> None:
    """BuildSystem has a 300s default timeout."""
    bs = BuildSystem(name="x", cmd=["echo"])
    assert bs.timeout == 300
