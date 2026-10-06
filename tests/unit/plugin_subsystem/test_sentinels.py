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
        # Authorized DATA-instance filenames — STRICTLY at plugin-ROOT
        # depth (i.e. ``plugins/<name>/MANIFEST.yaml`` only).  Nested
        # ``plugins/<name>/<sub>/MANIFEST.yaml`` is a loud vocabulary-
        # confinement violation: a vendored upstream file legitimately
        # named MANIFEST.yaml deep in a class subtree MUST be flagged
        # (the ② guard test already asserts none exist today).  The
        # carry-forward (4) makes the allowlist depth-bounded.
        if (
            len(relative.parts) == 3
            and relative.parts[0] == "plugins"
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


class TestVendoredClassSubtreesCannotCarryDataInstanceFilenames:
    """The ``PLUGIN_AUTHORIZED_DATA_FILENAMES`` carve-out in
    :class:`TestVocabularyConfinement` matches DATA-instance filenames
    (``MANIFEST.yaml``, ``CURATION.md``) by basename at ANY depth under
    ``plugins/*/``.  That breadth is safe today — no vendored file
    shares those names — but a future vendored tree that drops one of
    those filenames deep inside a vendored class subtree would
    silently skip vocabulary confinement (the carve-out could be
    exploited by vendored data).  This guard pins the property:
    vendored class subtrees (``copy_freely/``,
    ``snapshot_with_drift_alarm/``, ``own_outright/``) cannot carry
    DATA-instance filenames.
    """

    # Vendored class subtree names per CON §2 — the three classes whose
    # contents are vendored upstream bytes, not plugin-authored data.
    VENDORED_CLASS_SUBDIRS = (
        "copy_freely",
        "snapshot_with_drift_alarm",
        "own_outright",
    )
    FORBIDDEN_NAMES_IN_VENDORED = frozenset({"MANIFEST.yaml", "CURATION.md"})

    def test_vendored_class_subtrees_cannot_carry_data_instance_filenames(self):
        plugins_root = REPO_ROOT / "plugins"
        if not plugins_root.is_dir():
            # CI portability: pass trivially if no plugins/ tree is
            # present yet (e.g. fresh checkout on a branch that hasn't
            # landed a plugin yet).
            return
        violations: list = []
        for plugin_dir in sorted(p for p in plugins_root.iterdir() if p.is_dir()):
            for class_subdir in self.VENDORED_CLASS_SUBDIRS:
                vendored_root = plugin_dir / class_subdir
                if not vendored_root.is_dir():
                    continue  # class subtree not yet populated — fine
                for path in vendored_root.rglob("*"):
                    if not path.is_file():
                        continue
                    if path.name in self.FORBIDDEN_NAMES_IN_VENDORED:
                        violations.append(str(path.relative_to(REPO_ROOT)))
        assert violations == [], (
            "vendored class subtrees must not carry DATA-instance filenames "
            "(would silently exploit the PLUGIN_AUTHORIZED_DATA_FILENAMES "
            "carve-out in TestVocabularyConfinement):\n" + "\n".join(violations)
        )


class TestAuthorizedDataInstanceCarveoutIsPluginRootOnly:
    """The ``PLUGIN_AUTHORIZED_DATA_FILENAMES`` carve-out is STRICTLY
    plugin-ROOT depth (slice ③ carry-forward 4) — ``plugins/<name>/MANIFEST.yaml``
    or ``plugins/<name>/CURATION.md``.  Anything deeper is a
    vocabulary-confinement violation that must be flagged, not silently
    allowlisted.

    A vendored upstream file legitimately named ``MANIFEST.yaml`` deep in
    a class subtree is a CONFIRMED loud violation (the ② guard above
    already pins no-such-file-today; this test pins the carve-out shape
    so a future vendoring cannot silently widen the allowlist).
    """

    def test_carveout_excludes_nested_data_instance_paths(self):
        # Build a synthetic repo tree on tmp_path, point REPO_ROOT at it,
        # and assert the carve-out refuses a nested file while accepting
        # the same filename at plugin-ROOT depth.
        from tempfile import TemporaryDirectory
        from unittest import mock

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "daemon" / "plugin_subsystem").mkdir(parents=True)
            (root / "plugins-convention").mkdir(parents=True)
            plugins = root / "plugins"
            plugins.mkdir()
            # Plugin-ROOT MANIFEST.yaml — allowed
            plugin_dir = plugins / "opendesign"
            plugin_dir.mkdir()
            (plugin_dir / "MANIFEST.yaml").write_text("schema_version: '1.0.0'\n", encoding="utf-8")
            # Nested vendored class subtree with a same-named file — disallowed
            nested = plugin_dir / "copy_freely" / "design-systems" / "airbnb"
            nested.mkdir(parents=True)
            nested_file = nested / "MANIFEST.yaml"
            nested_file.write_text("schema_version: '1.0.0'\n", encoding="utf-8")

            with mock.patch.object(__import__("tests.unit.plugin_subsystem.test_sentinels", fromlist=["REPO_ROOT"]), "REPO_ROOT", root):
                violations = []
                for relative, path in _iter_repo_text_files():
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    for needle in VOCABULARY_STRINGS:
                        if needle in text:
                            violations.append(f"{relative} contains {needle!r}")
                # The nested MANIFEST.yaml DOES contain the literal text
                # "schema_version" — wait, that's a STRUCTURAL field, not
                # a vocabulary string.  Use the actual vocabulary instead.
                assert violations == [], f"unexpected vocabulary leak: {violations}"

            # Re-assert the depth-bound: a nested MANIFEST.yaml with
            # vocabulary content must be flagged.  Use 'execution_mode'
            # because it IS a vocabulary string and the nested file's
            # content can carry it.
            nested_file.write_text("execution_mode: 'resource-only'\n", encoding="utf-8")
            with mock.patch.object(__import__("tests.unit.plugin_subsystem.test_sentinels", fromlist=["REPO_ROOT"]), "REPO_ROOT", root):
                violations = []
                for relative, path in _iter_repo_text_files():
                    try:
                        text = path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    for needle in VOCABULARY_STRINGS:
                        if needle in text:
                            violations.append(f"{relative} contains {needle!r}")
                # The nested file is flagged; the plugin-root one is allowed.
                nested_violations = [v for v in violations if "copy_freely" in v]
                root_allowed = not any("plugins/opendesign/MANIFEST.yaml" in v for v in violations)
                assert nested_violations != [], (
                    f"nested MANIFEST.yaml with vocabulary content must be flagged; got: {violations}"
                )
                assert root_allowed, (
                    f"plugin-ROOT MANIFEST.yaml was wrongly flagged: {violations}"
                )
