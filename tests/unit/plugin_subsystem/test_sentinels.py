"""Tier-boundary sentinel tests (CON §7) — slice ① coverage item 7.

- Vocabulary confinement: the manifest-vocabulary strings appear ONLY under
  ``daemon/plugin_subsystem/**`` and ``plugins-convention/**`` — a SCOPED
  walk of the repo (tier-2 legitimately lives inside the daemon repo; the
  invariant is that tier-1 modules stay structurally blind). Excluded: the
  tests' own fixtures, the plugin-subsystem planning dir, and the
  authorized DATA-instance locations under ``plugins/*/`` (see
  ``PLUGIN_AUTHORIZED_DATA_FILES`` below — the manifest + curation record
  are CON §1 + REC §9 mandated, not vocabulary definition sites).
- No-import invariant: no module outside ``daemon/plugin_subsystem/``
  imports from ``plugins/``.
- No-runtime-loading: no ``importlib`` import / entry-point scanning in the
  new package (pluggy-tripwire).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

VOCABULARY_STRINGS = (
    "execution_mode",
    "lifted_symbol",
    "ipc_version",
    "hosted_runtime_deps",
    "fence_grant",
    "divergence_register",
)
TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".sh", ".md", ".ts", ".js", ".toml"}

# Scoped-walk exclusions: build/venv artifacts, the tests' own fixtures, and
# the planning source-of-truth dir (which DEFINES the vocabulary).
# Exclusions are TOP-LEVEL anchored (matched against the FIRST relative path
# component only — a nested dir named e.g. `tests/` deep in some tree is
# NOT excluded). The two allowed zones are excluded explicitly below:
# plugins-convention/ top-level, daemon/plugin_subsystem/ via prefix match.
WALK_EXCLUDED_TOP_LEVEL = {
    ".venv",
    ".git",
    "node_modules",
    "frontend",
    "backup",
    "test-results",
    "tests",
    "test",
    ".agents",  # incl. shared/planning/plugin-subsystem — defines the vocabulary
    "plugins-convention",  # allowed zone
}
WALK_ALLOWED_PREFIXES = {("daemon", "plugin_subsystem")}  # allowed zone (tier-2 home)
WALK_EXCLUDED_FILES = {"package-lock.json", "uv.lock"}

# Authorized DATA-instance filenames ANYWHERE under ``plugins/*/`` (CON §7
# last paragraph + the slice ② dispatch: "plugins/opendesign/MANIFEST.yaml
# is a DATA instance of the vocabulary, not a definition site").  These
# files are CON §1 / REC §9 mandated; they legitimately reference the
# vocabulary by name.  The set is matched on ``path.name`` so the rule
# applies uniformly to every plugin tree (``plugins/<name>/MANIFEST.yaml``,
# ``plugins/<name>/CURATION.md``).
PLUGIN_AUTHORIZED_DATA_FILENAMES = frozenset(
    {
        "MANIFEST.yaml",  # CON §1: every plugin tree has one
        "CURATION.md",  # REC §9: OQ3 record per plugin
    }
)


def _iter_repo_text_files():
    for path in REPO_ROOT.rglob("*"):
        if path.is_dir() or path.suffix not in TEXT_SUFFIXES:
            continue
        relative = path.relative_to(REPO_ROOT)
        if relative.parts[0] in WALK_EXCLUDED_TOP_LEVEL:
            continue
        if relative.parts[:2] in WALK_ALLOWED_PREFIXES:
            continue
        # Authorized DATA-instance filenames anywhere under plugins/*/.
        # Matched on the basename so the rule applies uniformly to every
        # plugin tree.  Files in copy_freely/ etc. are NOT authorized
        # (those are vendored upstream bytes; vocabulary is not a
        # legitimate concern there but we don't want to encourage it).
        if (
            relative.parts[0] == "plugins"
            and path.name in PLUGIN_AUTHORIZED_DATA_FILENAMES
        ):
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
