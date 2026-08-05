"""Build/test system detector.

Pure function — no side effects, no subprocess calls. Given a project
workdir, it returns the build/test command that should be used to validate
doc-maintenance commits.

Detection is a file-presence heuristic. The result can be overridden via
the ``doc_maintenance_build_cmd`` project metadata field.

Returns ``None`` for docs-only repos (no recognizable build system) — in
that case the caller skips validation and proceeds straight to staging.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BuildSystem:
    """A detected build/test system."""

    name: str
    cmd: list[str]
    timeout: int = 300


def detect(workdir: str | Path, override_cmd: str | None = None) -> BuildSystem | None:
    """Detect the project's build/test system via file-presence heuristic.

    Args:
        workdir: Project root directory.
        override_cmd: Optional override command (string). If set, replaces
            the detected command. Parsed via ``shlex.split``.

    Returns:
        :class:`BuildSystem` if a recognizable build system is detected
        (or an override is provided); ``None`` for docs-only repos.

    Note:
        Sequential multi-language execution is deferred to a follow-up phase.
        The detector returns the FIRST matching build system in marker order.
    """
    workdir_path = Path(workdir)

    if override_cmd:
        return BuildSystem(
            name="override",
            cmd=shlex.split(override_cmd),
            timeout=300,
        )

    # Marker order matters — first match wins. Most-reliable markers first.
    markers: list[tuple[str, str, list[str]]] = [
        ("package.json", "npm", ["npm", "test"]),
        ("pyproject.toml", "pytest", ["pytest", "-x"]),
        ("pytest.ini", "pytest", ["pytest", "-x"]),
        ("Makefile", "make", ["make", "test"]),  # only if "test:" target exists
        ("Cargo.toml", "cargo", ["cargo", "test"]),
        ("go.mod", "go", ["go", "test", "./..."]),
    ]

    for marker, name, cmd in markers:
        if (workdir_path / marker).exists():
            if marker == "Makefile":
                # Only select "make test" if Makefile actually defines a test target.
                if not _makefile_has_test_target(workdir_path / "Makefile"):
                    continue
            return BuildSystem(name=name, cmd=cmd, timeout=300)

    return None


def _makefile_has_test_target(makefile: Path) -> bool:
    """Return True if the Makefile contains a ``test:`` target definition.

    A bare ``test:`` mention in a comment does not count — we look for an
    uncommented line starting with ``test:`` or ``.PHONY: test``.
    """
    try:
        text = makefile.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Match `test:` at start of line (target definition) OR `.PHONY: ... test ...`.
        if line.startswith("test:"):
            return True
        if line.startswith(".PHONY:"):
            tokens = line.split()
            if "test" in tokens[1:]:
                return True
    return False
