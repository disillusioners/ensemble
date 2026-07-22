"""Shared workspace path resolution and security boundary.

Extracted from daemon/tools/filesystem.py so both agent tools and HTTP
routers use the same path-traversal protection.
"""
import os
import re
import tempfile
from pathlib import Path


class WorkspaceGuard:
    """Path resolution + boundary checking for workspace file access.

    All HTTP workspace endpoints MUST route through this guard before
    touching the filesystem. The guard:
    1. Resolves relative paths against the project workdir
    2. Canonicalizes via Path.resolve() (resolves .., symlinks)
    3. Verifies target is within workdir (or allowed temp dirs)
    4. Returns (Path, None) on success, (None, error_msg) on failure
    """

    # Configurable limits
    MAX_FILE_SIZE_BYTES = 1_048_576  # 1 MB
    DEFAULT_TREE_DEPTH = 5
    IGNORE_PATTERNS = frozenset({
        ".git", "node_modules", "__pycache__", ".venv", "venv",
        "dist", "build", ".next", ".pytest_cache", ".mypy_cache",
        ".tox", "egg-info", ".eggs",
    })

    def __init__(self, workdir: str):
        self.workdir: Path = Path(workdir).expanduser().resolve()
        if not self.workdir.exists():
            raise ValueError(f"Working directory does not exist: {workdir}")

    def resolve(self, relative_path: str) -> tuple[Path | None, str | None]:
        """Resolve a relative path within the workspace. Returns (path, error).

        Mirrors the logic from _resolve_within_workdir / _resolve_target_path /
        _is_within_workdir / _normed_contains in the original filesystem.py.
        For absolute paths, the boundary check is intentionally skipped (trusted
        by design, same semantics as the agent tools).
        """
        target, base, err = self._resolve_target(relative_path)
        if err:
            return None, err
        if base is not None and target is not None and not self._contains(base, target):
            return None, f"ERROR: Path escapes workdir boundary: {relative_path}"
        return target, None

    def resolve_strict(self, path: str) -> tuple[Path | None, str | None]:
        """Resolve a path within the workspace, ALWAYS enforcing boundary check.

        Unlike ``resolve()`` which skips the boundary check for absolute paths,
        this method enforces containment for ALL paths. Use this for HTTP
        endpoints to prevent arbitrary file read via absolute paths such as
        ``/etc/passwd``. Agent tools that trust absolute paths should keep
        using ``resolve()``.
        """
        target, _base, err = self._resolve_target(path)
        if err:
            return None, err
        # Always enforce boundary check, regardless of whether path was absolute
        if target is not None and not self._contains(self.workdir, target):
            return None, f"ERROR: Path escapes workdir boundary: {path}"
        return target, None

    def is_within(self, target: Path) -> bool:
        """Check if a resolved path is within the workspace or allowed temp dirs."""
        return self._contains(self.workdir, target)

    # Internal helpers — ported verbatim from daemon/tools/filesystem.py

    _WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
    _WINDOWS_UNC_RE = re.compile(r"^[\\/]{2}")

    @classmethod
    def _is_absolute_path(cls, path: str) -> bool:
        """Return True if *path* is absolute on the current OS or matches a
        Windows absolute pattern (drive letter or UNC)."""
        if not path:
            return False
        try:
            if Path(path).is_absolute():
                return True
        except (OSError, ValueError):
            return False
        return bool(cls._WINDOWS_DRIVE_RE.match(path) or cls._WINDOWS_UNC_RE.match(path))

    def _resolve_target(
        self, path: str,
    ) -> tuple[Path | None, Path | None, str | None]:
        """Resolve *path* against the workspace workdir.

        Returns ``(target_path, base_path, error)``. ``base_path`` is the
        workdir when *path* is relative (boundary check applies), and ``None``
        when *path* is absolute (no boundary check).
        """
        if self._is_absolute_path(path):
            try:
                return Path(path).expanduser(), None, None
            except (OSError, RuntimeError) as e:
                return None, None, f"ERROR: Invalid absolute path: {e}"

        if not path or not path.strip():
            return (
                None, None,
                "ERROR: workdir is required for relative paths. "
                "Pass an absolute path if workdir is not applicable.",
            )

        base = self.workdir  # already resolved in __init__
        try:
            target = (base / path).expanduser().resolve()
        except (OSError, RuntimeError) as e:
            return None, None, f"ERROR: Invalid path: {e}"
        return target, base, None

    @staticmethod
    def _normed_contains(base: Path, target: Path) -> bool:
        """Check if *target* is within *base* using OS-appropriate case norm."""
        try:
            normed_target = Path(os.path.normcase(str(target.resolve())))
            normed_base = Path(os.path.normcase(str(base.resolve())))
            normed_target.relative_to(normed_base)
            return True
        except (ValueError, OSError):
            return False

    @classmethod
    def _is_in_temp_dir(cls, target: Path) -> bool:
        """Check if *target* is within any allowed temp directory."""
        temp_dirs = [
            Path(tempfile.gettempdir()).resolve(),
            Path("/tmp").resolve(),
            Path("/private/tmp").resolve(),
            Path("/var/tmp").resolve(),
        ]
        if os.name == "nt":
            system_drive = os.environ.get("SystemDrive", "C:")
            temp_dirs.extend([
                Path(os.environ.get("TEMP") or tempfile.gettempdir()).resolve(),
                Path(os.environ.get("TMP") or tempfile.gettempdir()).resolve(),
                Path(f"{system_drive}\\tmp").resolve(),
            ])
        return any(cls._normed_contains(td, target) for td in temp_dirs)

    def _contains(self, base: Path, target: Path) -> bool:
        """Check if *target* is within *base* OR an allowed temp directory."""
        if self._normed_contains(base, target):
            return True
        return self._is_in_temp_dir(target)
