"""Tier-boundary sentinel tests (CON §7) — slice ① coverage item 7.

- Vocabulary confinement: the manifest-vocabulary strings appear ONLY under
  ``daemon/plugin_subsystem/**`` and ``plugins-convention/**`` — a SCOPED
  walk of the repo (tier-2 legitimately lives inside the daemon repo; the
  invariant is that tier-1 modules stay structurally blind). Excluded: the
  tests' own fixtures and the plugin-subsystem planning dir.
- No-import invariant: no module outside ``daemon/plugin_subsystem/``
  imports from ``plugins/``.
- No-runtime-loading: no ``importlib`` import / entry-point scanning in the
  new package (pluggy-tripwire).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

VOCABULARY_STRINGS = ("execution_mode", "lifted_symbol", "ipc_version", "hosted_runtime_deps")
TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".sh", ".md", ".ts", ".js", ".toml"}

# Scoped-walk exclusions: build/venv artifacts, the tests' own fixtures, and
# the planning source-of-truth dir (which DEFINES the vocabulary).
WALK_EXCLUDED_PARTS = {
    ".venv",
    ".git",
    "node_modules",
    "frontend",
    "backup",
    "test-results",
    "tests",
    "test",
    "plugin-subsystem",  # .agents/shared/planning/plugin-subsystem
    "plugins-convention",  # allowed zone
    "plugin_subsystem",  # allowed zone (handled explicitly below)
}
WALK_EXCLUDED_FILES = {"package-lock.json", "uv.lock"}


def _iter_repo_text_files():
    for path in REPO_ROOT.rglob("*"):
        if path.is_dir() or path.suffix not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(REPO_ROOT)
        if any(part in WALK_EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.name in WALK_EXCLUDED_FILES:
            continue
        yield relative, path


class TestVocabularyConfinement:
    """CON §7: vocabulary strings only under the two allowed zones."""

    def test_vocabulary_absent_from_scoped_repo_walk(self):
        violations = []
        for relative, path in _iter_repo_text_files():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:  # pragma: no cover - unreadable file
                continue
            for needle in VOCABULARY_STRINGS:
                if needle in text:
                    violations.append(f"{relative} contains {needle!r}")
        assert violations == [], "vocabulary leaked outside the allowed zones:\n" + "\n".join(violations)

    def test_allowed_zones_actually_carry_the_vocabulary(self):
        """The sentinel is meaningful only if the allowed zones DO contain the
        strings (guards against a silently-vacuous allowlist)."""
        plugin_subsystem = REPO_ROOT / "daemon" / "plugin_subsystem"
        convention = REPO_ROOT / "plugins-convention"
        joined = "\n".join(
            p.read_text(encoding="utf-8", errors="replace")
            for p in (*plugin_subsystem.glob("*.py"), *convention.glob("*"))
            if p.is_file()
        )
        for needle in VOCABULARY_STRINGS:
            assert needle in joined, f"sentinel would be vacuous: {needle!r} missing from allowed zones"


class TestNoImportInvariant:
    """CON §7: no module outside daemon/plugin_subsystem/ imports from plugins/."""

    def test_no_daemon_module_imports_plugins(self):
        pattern = re.compile(r"^\s*(?:from|import)\s+plugins\b", re.MULTILINE)
        daemon_root = REPO_ROOT / "daemon"
        violations = []
        for path in daemon_root.rglob("*.py"):
            relative = path.relative_to(REPO_ROOT)
            if relative.parts[:2] == ("daemon", "plugin_subsystem"):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if pattern.search(text):
                violations.append(str(relative))
        assert violations == [], f"daemon modules import from plugins/: {violations}"


class TestNoRuntimeLoading:
    """CON §7: no importlib.import_module / entry-point scanning in the registry
    package (pluggy-tripwire) — vocab/registry consumed as data + explicit
    validation code only."""

    def test_no_importlib_in_plugin_subsystem(self):
        pattern = re.compile(r"^\s*import\s+importlib\b|^\s*from\s+importlib\b", re.MULTILINE)
        package_root = REPO_ROOT / "daemon" / "plugin_subsystem"
        violations = [str(p.relative_to(REPO_ROOT)) for p in package_root.glob("*.py") if pattern.search(
            p.read_text(encoding="utf-8", errors="replace")
        )]
        assert violations == [], f"importlib usage in plugin_subsystem: {violations}"

    def test_no_entry_point_scanning_in_plugin_subsystem(self):
        pattern = re.compile(r"entry_points?\s*\(|iter_entry_points|pkg_resources|importlib\.metadata")
        package_root = REPO_ROOT / "daemon" / "plugin_subsystem"
        violations = [str(p.relative_to(REPO_ROOT)) for p in package_root.glob("*.py") if pattern.search(
            p.read_text(encoding="utf-8", errors="replace")
        )]
        assert violations == [], f"entry-point scanning in plugin_subsystem: {violations}"

    def test_no_importlib_string_in_convention_runner(self):
        """ci_runner.py is a thin argv wrapper — no dynamic import machinery."""
        text = (REPO_ROOT / "plugins-convention" / "ci_runner.py").read_text(encoding="utf-8")
        assert "importlib" not in text
        assert "entry_point" not in text
