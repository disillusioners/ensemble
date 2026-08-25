"""Flag A — import-level hard-fail: no saver imports / alist calls in routers.

Spec — ``.agents/shared/planning/langgraph-checkpoint-perf/phase1-plan.md``
lines 857-898 (LD-OQ2: enforcement lives in the standard test suite; NO
new CI infra).

Forbidden patterns in ``daemon/routers/**/*.py`` (Phase 1 scope):

1. ``from langgraph.checkpoint ...``   (any direct import)
2. ``import langgraph.checkpoint``     (any direct import)
3. ``saver.alist(``                    (any call, incl. ``await saver.alist(``)

Hard Constraint #3 (repository pattern): routers must not touch the raw
saver; factory injection only. A regression here is a test failure the
developer sees on the next standard test run.

Allowlist: ``tools/lint/allowlist.txt`` — one ``<file>:<lineno>`` (or a
bare ``<file>:``) entry per line suppresses violations for that file/line.
Phase 1 ships it EMPTY; adding entries requires an explicit justification
in the PR that adds them.

NOTE: docstring/comment mentions of "LangGraph checkpoint" in prose are
NOT violations — only actual import statements and ``saver.alist(`` call
sites are. The scan therefore uses AST-based import detection for the
import patterns (robust against multi-line imports) and an AST call-func
scan for ``.alist`` calls (receiver-agnostic).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ROUTERS_DIR = REPO_ROOT / "daemon" / "routers"
ALLOWLIST_PATH = REPO_ROOT / "tools" / "lint" / "allowlist.txt"

# The forbidden attribute name (W9): any ``ast.Call`` whose ``func`` is an
# ``ast.Attribute`` named ``alist`` is a violation, regardless of the
# receiver expression (``saver``, ``self._saver``, ``checkpointer``, …).
ALIST_ATTR = "alist"

IMPORT_MODULE_PREFIX = "langgraph.checkpoint"


def _load_allowlist() -> set[str]:
    """Read allowlist entries. Missing file → empty set (nothing suppressed)."""
    if not ALLOWLIST_PATH.exists():
        return set()
    entries = set()
    for raw in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.add(stripped)
    return entries


def _is_allowed(entry: str, allowlist: set[str]) -> bool:
    """True when ``<relpath>:<lineno>`` or ``<relpath>:`` is allowlisted."""
    if entry in allowlist:
        return True
    file_part = entry.rsplit(":", 1)[0]
    return file_part in allowlist


def _display_path(path: Path) -> str:
    """Repo-relative path when possible; absolute string otherwise.

    Synthetic violation fixtures live in pytest tmp dirs outside the
    repo — for those we fall back to the absolute path string so the
    violation entry is still stable and allowlistable.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _find_violations(router_files: list[Path]) -> list[tuple[str, int, str]]:
    """Scan files for forbidden patterns → list of (relpath, lineno, detail)."""
    violations: list[tuple[str, int, str]] = []
    allowlist = _load_allowlist()

    for path in router_files:
        relpath = _display_path(path)
        source = path.read_text(encoding="utf-8")

        # AST-based detection — catches multi-line/aliased imports and,
        # for the alist call, ANY receiver in call-func position (W9).
        try:
            tree = ast.parse(source)
        except SyntaxError:
            # Not our problem if a synthetic/invalid file is present; the
            # normal test gates would catch it. Both scans are AST-based,
            # so an unparseable file is skipped entirely here.
            tree = None

        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module.startswith(IMPORT_MODULE_PREFIX):
                        entry = f"{relpath}:{node.lineno}"
                        if not _is_allowed(entry, allowlist):
                            violations.append(
                                (relpath, node.lineno, f"from {module} import ...")
                            )
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith(IMPORT_MODULE_PREFIX):
                            entry = f"{relpath}:{node.lineno}"
                            if not _is_allowed(entry, allowlist):
                                violations.append(
                                    (relpath, node.lineno, f"import {alias.name}")
                                )
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == ALIST_ATTR
                ):
                    # W9 — receiver-agnostic call-func scan: flags
                    # ``saver.alist(…)``, ``self._saver.alist(…)``,
                    # ``checkpointer.alist(…)``, … alike. (A
                    # ``getattr(obj, "alist")(…)`` indirection is NOT
                    # flagged — outside the prescribed call-func scan.)
                    entry = f"{relpath}:{node.lineno}"
                    if not _is_allowed(entry, allowlist):
                        violations.append(
                            (relpath, node.lineno, f"{ast.unparse(node.func)}(...) call")
                        )

    return violations


def _router_files() -> list[Path]:
    """All .py files under daemon/routers/ (recursive)."""
    return sorted(ROUTERS_DIR.rglob("*.py"))


def _scan_with_extra_file(extra_file: Path) -> list[tuple[str, int, str]]:
    """Scan routers dir PLUS one synthetic extra file (for violation tests)."""
    files = _router_files() + [extra_file]
    return _find_violations(files)


class TestNoSaverImportsInRouters:
    """Flag A — the Phase 1 import-level hard-fail gate (LD-OQ2)."""

    def test_no_saver_imports_clean(self):
        """daemon/routers/** contains ZERO forbidden patterns."""
        files = _router_files()
        assert len(files) >= 20, (
            "Expected the full routers tree to be scanned — found too few files"
        )
        violations = _find_violations(files)
        assert not violations, (
            "Forbidden saver access in routers (Hard Constraint #3 — "
            "repository pattern, factory injection only):\n"
            + "\n".join(f"  {p}:{ln}: {detail}" for p, ln, detail in violations)
        )

    def test_no_saver_imports_fails_on_synthetic_violation(self, tmp_path):
        """A fixture router file with a langgraph.checkpoint import IS detected."""
        synthetic = tmp_path / "synthetic_router.py"
        synthetic.write_text(
            "from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver\n"
            "\n"
            "saver = None\n",
            encoding="utf-8",
        )
        violations = _scan_with_extra_file(synthetic)
        matching = [v for v in violations if v[0].endswith("synthetic_router.py")]
        assert matching, (
            "Synthetic `from langgraph.checkpoint.sqlite.aio import "
            "AsyncSqliteSaver` was NOT detected — the gate is broken."
        )
        assert any("langgraph.checkpoint" in detail for _, _, detail in matching)

    def test_no_alist_calls_fails_on_synthetic_violation(self, tmp_path):
        """A fixture router file calling ``await saver.alist(...)`` IS detected."""
        synthetic = tmp_path / "synthetic_alist_router.py"
        synthetic.write_text(
            "async def handler(saver, config):\n"
            "    async for ct in saver.alist(config, limit=1000):\n"
            "        pass\n",
            encoding="utf-8",
        )
        violations = _scan_with_extra_file(synthetic)
        matching = [v for v in violations if v[0].endswith("synthetic_alist_router.py")]
        assert matching, (
            "Synthetic `saver.alist(config, limit=1000)` call was NOT "
            "detected — the gate is broken."
        )
        assert any("alist" in detail for _, _, detail in matching)

    def test_alist_call_with_arbitrary_receiver_detected(self, tmp_path):
        """W9: ``self._saver.alist(...)`` (non-``saver`` receiver) IS detected.

        The pre-W9 ``\\bsaver\\.alist\\(`` regex only matched a literal
        ``saver`` receiver — this synthetic case proves the AST call-func
        scan is receiver-agnostic (the exact blind spot the review flagged).
        """
        synthetic = tmp_path / "synthetic_alist_receiver_router.py"
        synthetic.write_text(
            "class Handler:\n"
            "    def __init__(self, checkpointer):\n"
            "        self._saver = checkpointer.raw_saver\n"
            "\n"
            "    async def handler(self, config):\n"
            "        async for ct in self._saver.alist(config, limit=1000):\n"
            "            pass\n",
            encoding="utf-8",
        )
        violations = _scan_with_extra_file(synthetic)
        matching = [
            v
            for v in violations
            if v[0].endswith("synthetic_alist_receiver_router.py")
        ]
        assert matching, (
            "Synthetic `self._saver.alist(config, limit=1000)` call was "
            "NOT detected — the receiver-literal blind spot is back."
        )
        assert any(
            "self._saver.alist" in detail for _, _, detail in matching
        ), matching

    def test_allowlist_suppresses(self, tmp_path, monkeypatch):
        """Adding a line to allowlist.txt suppresses the violation.

        The allowlist is the documented escape hatch (plan risk table:
        "False positive blocks legitimate code in routers → Allowlist
        escape hatch (currently empty)"). This test proves the hatch
        actually works, using a synthetic file.
        """
        synthetic = tmp_path / "synthetic_allowed_router.py"
        synthetic.write_text(
            "from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver\n",
            encoding="utf-8",
        )

        # Compute the entry the violation WOULD produce: the synthetic file
        # is outside the repo, so the relpath is absolute-ish; use its
        # path:lineno as the allowlist entry directly.
        entry = f"{synthetic}:1"

        # Point the allowlist at a temp file containing that entry.
        allowlist_tmp = tmp_path / "allowlist.txt"
        allowlist_tmp.write_text(f"{entry}\n", encoding="utf-8")
        monkeypatch.setattr(
            "tests.integration.test_no_saver_imports_in_routers.ALLOWLIST_PATH",
            allowlist_tmp,
        )

        # The synthetic import is now suppressed.
        violations = _scan_with_extra_file(synthetic)
        matching = [v for v in violations if str(synthetic) in v[0]]
        assert not matching, (
            f"Allowlisted violation was NOT suppressed: {matching}"
        )

        # ...but the real routers tree is still fully scanned and clean
        # (allowlisting one file does not disable the gate).
        assert not _find_violations(_router_files())

    def test_allowlist_ships_empty_in_phase1(self):
        """tools/lint/allowlist.txt exists and is EMPTY (comments only allowed)."""
        assert ALLOWLIST_PATH.exists(), (
            f"{ALLOWLIST_PATH} must exist (Phase 1 ships it empty)"
        )
        content = ALLOWLIST_PATH.read_text(encoding="utf-8")
        active = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        assert not active, (
            f"Phase 1 allowlist must be empty; found active entries: {active}"
        )
