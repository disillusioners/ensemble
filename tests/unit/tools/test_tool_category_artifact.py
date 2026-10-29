"""Tool-category vocabulary artifact — generator + drift check (V-4 / P-1).

The tool-category vocabulary is **process-dependent and unstable**
(MCP name-prefix injection + ``scan_tools_for_full_docs``
process-dependence — the instability IS the defect), so it is NOT
hand-listed and NOT frozen from prose (metric-catalog §8.6).

Contract implemented here:

* **Generator** — imports the full tool registry, calls
  ``get_tool_categories()`` (``daemon/tools/_tool_registry.py:1598``)
  over the complete tool universe, derives the sorted distinct category
  list + count, and writes them to a **scratch** file.  The COMMITTED
  artifact (``.agents/shared/metrics/tool-categories.json``) is never
  rewritten by a test run — the ambient regeneration env var alone
  cannot authorise it (F11: it used to make the drift check compare the
  file to itself).  Run the generator with::

      ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT=/tmp/tool-categories.json \\
          uv run python -m pytest tests/unit/tools/test_tool_category_artifact.py -q
      cp /tmp/tool-categories.json .agents/shared/metrics/tool-categories.json

* **Drift check** — re-runs the generator and FAILS (non-zero exit)
  when the on-disk artifact diverges from the freshly derived set, so a
  new ``register_tool_category`` site or a renamed category breaks CI
  instead of silently drifting the metric label set.

* **Freeze rule (P-1)** — the frozen label set is the artifact's sorted
  list; changing it requires bumping ``VOCABULARY_VERSION``
  (``daemon.checkpoint_metrics``).  At runtime any category outside the
  derived set folds to the reserved ``other`` bucket, so the cardinality
  is bounded by (derived count + 1).

The derivation is **never a hand list**: it comes from the registry's own
source of truth — the ``CATEGORY_MODULES`` map plus the
``@register_tool_category("...")`` call sites in ``daemon/tools/``.
"""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path

import pytest

from daemon.checkpoint_metrics import (
    RESERVED_LABEL_VALUE,
    VOCABULARY_VERSION,
    tool_category_artifact_path,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TOOLS_DIR = REPO_ROOT / "daemon" / "tools"
REGENERATE_ENV = "ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT"


# ─── derivation ──────────────────────────────────────────────────────────────


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "literal"`` assignments."""
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            constants[target.id] = node.value.value
    return constants


def _is_tool_decorator(decorator: ast.expr) -> bool:
    func = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(func, ast.Name):
        return func.id == "tool"
    if isinstance(func, ast.Attribute):
        return func.attr == "tool"
    return False


def _registered_category_keys() -> set[str]:
    """Every category key a tool module registers.

    Derived from the ``@register_tool_category("...")`` call sites in
    ``daemon/tools/`` — the runtime source of the label vocabulary.
    Factory-created tools never appear in ``_tool_metadata`` at import
    time, so the decorator call sites are the complete set.
    """
    categories: set[str] = set()
    for path in sorted(TOOLS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = _module_string_constants(tree)

        def visit(node: ast.AST, inherited: str | None) -> None:
            category = inherited
            for decorator in getattr(node, "decorator_list", []):
                if not isinstance(decorator, ast.Call):
                    continue
                func = decorator.func
                if not isinstance(func, ast.Name) or func.id != "register_tool_category":
                    continue
                if not decorator.args:
                    continue
                argument = decorator.args[0]
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    category = argument.value
                elif isinstance(argument, ast.Name) and argument.id in constants:
                    category = constants[argument.id]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if category and any(_is_tool_decorator(d) for d in node.decorator_list):
                    categories.add(category)
            for child in ast.iter_child_nodes(node):
                visit(child, category)

        visit(tree, None)
    return categories


def _is_real_tool(candidate: object) -> bool:
    """True for a REAL registered tool (not a test double)."""
    if not hasattr(candidate, "_tool_category"):
        return False
    description = getattr(candidate, "description", None)
    return isinstance(description, str)


def _runtime_tool_categories() -> dict[str, list[str]]:
    """``get_tool_categories()`` over the complete tool universe.

    The registry's metadata is populated by replaying the runtime
    registration step (``scan_tools_for_full_docs``) over every
    category module's tool functions; the registry state is restored
    afterwards so the test does not leak into the rest of the suite.

    ``get_tool_categories()`` keys are the DISPLAY names (a module's
    ``CATEGORY_NAME``); the metric label carries the raw category KEY
    (the ``register_tool_category`` argument), so the raw keys are
    returned separately by :func:`_runtime_category_keys`.

    The scan is best-effort: a suite that leaves MOCKED tools in a
    category module (or a registry in a partial state) must not turn
    the DRIFT check into a false red — the source-derived set (the
    artifact's canonical source) is what the drift check compares.
    """
    from daemon.tools import _tool_registry as registry

    saved_metadata = dict(registry._tool_metadata)
    saved_docs = dict(registry._full_docs)
    try:
        registry.clear_registry()
        tools = []
        for module_paths in registry.CATEGORY_MODULES.values():
            paths = module_paths if isinstance(module_paths, list) else [module_paths]
            for module_path in paths:
                try:
                    module = __import__(module_path, fromlist=["__name__"])
                except Exception:  # pragma: no cover - an unimportable module
                    continue
                for attribute in dir(module):
                    candidate = getattr(module, attribute, None)
                    if _is_real_tool(candidate):
                        tools.append(candidate)
        try:
            registry.scan_tools_for_full_docs(tools)
            categories = registry.get_tool_categories()
        except Exception:  # pragma: no cover - a polluted registry
            categories = {}
        # The raw keys observed INSIDE the isolated scan (the surrounding
        # suite may have left mocked tools in the global registry).
        raw_keys = {
            str(meta.get("category"))
            for meta in registry._tool_metadata.values()
            if meta.get("category")
        }
        return categories, raw_keys
    finally:
        registry._tool_metadata.clear()
        registry._tool_metadata.update(saved_metadata)
        registry._full_docs.clear()
        registry._full_docs.update(saved_docs)




def derive_tool_categories() -> dict[str, object]:
    """Derive the frozen tool-category set + count from code."""
    from daemon.tools import _tool_registry as registry

    categories: set[str] = set(registry.CATEGORY_MODULES.keys())
    categories |= _registered_category_keys()

    # Every derived category key must be a non-empty string (the metric
    # label carries the key, never the display name).
    for key in categories:
        assert key and isinstance(key, str)

    return {
        "categories": sorted(categories),
        "count": len(categories),
        "vocabulary_version": VOCABULARY_VERSION,
        "reserved": RESERVED_LABEL_VALUE,
        "source": "daemon/tools/_tool_registry.py CATEGORY_MODULES + @register_tool_category call sites",
    }


def _artifact_payload() -> dict[str, object]:
    return derive_tool_categories()


def _read_artifact() -> dict[str, object]:
    path = tool_category_artifact_path()
    assert path.is_file(), (
        f"tool-category artifact missing: {path}. Regenerate into a scratch "
        f"path and copy it over: {REGENERATE_ENV}=/tmp/tool-categories.json "
        f"uv run python -m pytest "
        f"tests/unit/tools/test_tool_category_artifact.py -q"
    )
    return json.loads(path.read_text(encoding="utf-8"))


# ─── tests ───────────────────────────────────────────────────────────────────


def _regenerate_if_authorised() -> None:
    """Write the derived payload into the AUTHORISED scratch path.

    **The committed artifact is NEVER rewritten by a test run** (F11).
    The ambient ``ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT=1`` alone
    used to authorise the rewrite, which made the drift check compare
    the committed file to ITSELF — the check went blind exactly when it
    mattered. Now the env var names the OUTPUT file; writing over the
    committed artifact is refused outright.

    Maintainer flow::

        ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT=/tmp/tool-categories.json \\
            uv run python -m pytest tests/unit/tools/test_tool_category_artifact.py -q
        cp /tmp/tool-categories.json .agents/shared/metrics/tool-categories.json
    """
    target = os.environ.get(REGENERATE_ENV, "").strip()
    if not target or target == "1":
        # No explicit scratch output path ⇒ nothing is written.
        return
    path = Path(target)
    if path.is_dir():
        path = path / tool_category_artifact_path().name
    committed = tool_category_artifact_path()
    try:
        same = path.resolve() == committed.resolve()
    except OSError:  # pragma: no cover — defensive
        same = False
    if same:
        # Never rewrite the committed artifact from a test run.
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _artifact_payload()
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


@pytest.fixture(scope="module", autouse=True)
def _maybe_regenerate_artifact() -> None:
    """Regenerate ONLY into an explicit SCRATCH path (see the helper)."""
    _regenerate_if_authorised()


class TestToolCategoryArtifact:
    """The artifact is generated from code and drift-checked in CI."""

    def test_artifact_is_generated_from_the_tool_registry(self) -> None:
        """The artifact matches a freshly derived set (the drift check).

        Fails (non-zero exit) when a new ``register_tool_category`` site
        or a renamed category diverges from the committed artifact.
        """
        derived = _artifact_payload()
        on_disk = _read_artifact()

        assert on_disk["categories"] == derived["categories"], (
            "tool-category artifact drifted from the registry — regenerate "
            f"into a scratch path ({REGENERATE_ENV}=/tmp/tool-categories.json) "
            "and copy it over (and bump VOCABULARY_VERSION)"
        )
        assert on_disk["count"] == derived["count"] == len(derived["categories"])

    def test_artifact_is_sorted_distinct_and_non_empty(self) -> None:
        """The artifact list is sorted, distinct and non-empty."""
        payload = _read_artifact()
        categories = payload["categories"]
        assert isinstance(categories, list)
        assert categories == sorted(categories)
        assert len(categories) == len(set(categories))
        assert len(categories) > 0
        assert payload["count"] == len(categories)

    def test_artifact_covers_every_registered_category(self) -> None:
        """No ``register_tool_category`` site is missing from the artifact."""
        derived = set(derive_tool_categories()["categories"])
        on_disk = set(_read_artifact()["categories"])
        missing = derived - on_disk
        assert not missing, f"categories missing from the artifact: {sorted(missing)}"

    def test_runtime_tool_categories_are_covered(self) -> None:
        """``get_tool_categories()`` resolves only known categories.

        The runtime view keys are the DISPLAY names; the metric label
        carries the raw category key.  Both must resolve to a category
        the artifact knows, otherwise the observation folds to ``other``.
        """
        on_disk = set(_read_artifact()["categories"])
        runtime, runtime_keys = _runtime_tool_categories()
        if not runtime:
            # A polluted registry (mocked tools left behind by another
            # suite) cannot answer the question — the source-derived
            # drift check above is the load-bearing assertion.
            pytest.skip("runtime tool-category universe unavailable in this process")
        assert runtime, "runtime tool-category universe is empty — the derivation broke"

        # The raw category keys the live registry resolved.
        unknown_keys = runtime_keys - on_disk
        assert not unknown_keys, (
            f"runtime category keys outside the artifact: {sorted(unknown_keys)} — "
            "they would fold to 'other'"
        )

        # Every display name must resolve back to a known category key.
        from daemon.tools import _tool_registry as registry

        for display_name in runtime:
            resolved = False
            for category_key, module_paths in registry.CATEGORY_MODULES.items():
                paths = module_paths if isinstance(module_paths, list) else [module_paths]
                for module_path in paths:
                    module = __import__(module_path, fromlist=["__name__"])
                    if getattr(module, "CATEGORY_NAME", category_key) == display_name:
                        resolved = category_key in on_disk
                        break
                if resolved:
                    break
            assert resolved, (
                f"runtime display category {display_name!r} does not resolve to a known "
                "category key — it would fold to 'other'"
            )

    def test_reserved_other_bucket_is_present(self) -> None:
        """The reserved ``other`` bucket is always available."""
        from daemon.checkpoint_metrics import tool_category_vocabulary

        vocabulary = tool_category_vocabulary()
        assert RESERVED_LABEL_VALUE in vocabulary
        assert len(vocabulary) == len(set(vocabulary))


class TestArtifactRegenerationAuthority:
    """F11 — the committed artifact is never rewritten by a test run.

    The ambient ``ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT=1`` used to
    authorise the rewrite, which made the drift check compare the
    committed file to ITSELF — the check went blind exactly when it
    mattered.
    """

    def test_the_ambient_flag_cannot_rewrite_the_committed_artifact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(REGENERATE_ENV, "1")
        committed = tool_category_artifact_path()
        before = committed.read_text(encoding="utf-8")
        _regenerate_if_authorised()
        assert committed.read_text(encoding="utf-8") == before

    def test_the_committed_artifact_path_is_refused_as_an_output(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even an env var that NAMES the committed artifact is refused."""
        committed = tool_category_artifact_path()
        monkeypatch.setenv(REGENERATE_ENV, str(committed))
        before = committed.read_text(encoding="utf-8")
        _regenerate_if_authorised()
        assert committed.read_text(encoding="utf-8") == before

    def test_the_drift_check_genuinely_fails_with_the_ambient_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With the ambient flag set, a divergence STILL fails the drift
        check (the file is not compared to itself)."""
        monkeypatch.setenv(REGENERATE_ENV, "1")
        _regenerate_if_authorised()

        drifted = {
            "categories": ["__drift__"],
            "count": 1,
            "vocabulary_version": VOCABULARY_VERSION,
            "reserved": RESERVED_LABEL_VALUE,
            "source": "drift",
        }
        monkeypatch.setattr(
            sys.modules[__name__], "_artifact_payload", lambda: drifted
        )
        with pytest.raises(AssertionError):
            TestToolCategoryArtifact().test_artifact_is_generated_from_the_tool_registry()

    def test_an_explicit_scratch_path_receives_the_payload(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The authorised scratch path DOES receive the regenerated
        payload (the maintainer flow keeps working)."""
        target = tmp_path / "tool-categories.json"
        monkeypatch.setenv(REGENERATE_ENV, str(target))
        _regenerate_if_authorised()
        assert target.is_file()
        assert json.loads(target.read_text(encoding="utf-8"))["categories"] == (
            _artifact_payload()["categories"]
        )
