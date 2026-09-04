"""cpv2 final-gate fold — AST pin for the ``CheckpointCleanupJob``
construction kwarg ``message_metadata_repo``.

Binding follow-up from the langgraph-checkpoint-perf-v2 final-gate
whole-branch review (Finding 🟡3, "Unpinned construction kwarg —
silent T5.19 revert class"): the SINGLE ``CheckpointCleanupJob(...)``
construction site lives in ``daemon/manager.py`` inside
``initialize()`` (~line 2300) and wires the T5.19 prune via
``message_metadata_repo=self._message_metadata_repo``.

``CheckpointCleanupJob.__init__`` declares
``message_metadata_repo: MessageMetadataRepository | None = None`` — a
backward-compatible default that SKIPS the T5.19 side-table prune in
``_cleanup_instance`` (the never-raise prune that runs AFTER
``adelete_thread`` and keeps a fully-cleaned instance at ZERO
``message_metadata`` rows). Dropping the kwarg — or passing ``None`` —
disables the prune with ZERO test failures; the only symptom is slow
``message_metadata`` table growth in production. This pin makes that
regression loud instead of silent (same silent-kwarg-drop class the
branch already pinned for the tap-slot kwargs).

Why AST + static (same rationale as the sibling pin
``test_message_metadata_lifecycle_wiring.py``): driving a fully armed
manager fixture through a real maintenance tick to observe the prune
is expensive and brittle, and the failure mode (a kwarg silently
dropped in a revert) is a SOURCE-shape regression. The AST pin catches
the kwarg dropped / None'd / repo handle severed at zero runtime cost.

Marker gating: NO ``integration`` marker — must run under default
``addopts`` (same property as the sibling wiring pin).
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANAGER_PATH = REPO_ROOT / "daemon" / "manager.py"

CONSTRUCTOR_NAME = "CheckpointCleanupJob"
MESSAGE_METADATA_KWARG = "message_metadata_repo"
EXPECTED_VALUE_ATTR = "_message_metadata_repo"


def _find_checkpoint_cleanup_job_calls(tree: ast.AST) -> list[ast.Call]:
    """Every ``CheckpointCleanupJob(...)`` call site in the module."""
    calls: list[ast.Call] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if isinstance(func, ast.Name) and func.id == CONSTRUCTOR_NAME:
                calls.append(node)
            self.generic_visit(node)

    _Visitor().visit(tree)
    return calls


def _describe(node: ast.AST) -> str:
    return f"{type(node).__name__}({ast.dump(node)[:80]}…)"


def _enclosing_function_info(
    tree: ast.AST, calls: list[ast.Call]
) -> dict[int, str]:
    """Map each call site's line to its enclosing method name.

    ``calls`` must come from the SAME parsed ``tree`` (node identity is
    what links a call to its enclosing function).
    """
    line_to_func: dict[int, str] = {}

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and child in calls:
                    line_to_func[child.lineno] = node.name
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

    _Visitor().visit(tree)
    return line_to_func


def _require_message_metadata_repo_wired(call: ast.Call) -> ast.Attribute:
    """Assert one call site passes ``message_metadata_repo=`` wired to
    ``self._message_metadata_repo``; return the value node.

    Shared by the pin tests below so the detection logic has exactly
    one home (the scratch negative-proof exercises THIS function, not a
    copy of it).
    """
    keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
    assert MESSAGE_METADATA_KWARG in keywords, (
        f"{CONSTRUCTOR_NAME} call at line {call.lineno} is MISSING the "
        f"``{MESSAGE_METADATA_KWARG}`` kwarg — the constructor default "
        "(None) silently SKIPS the T5.19 side-table prune in "
        "_cleanup_instance, so the message_metadata table grows "
        "without bound in production with ZERO test failures."
    )
    value = keywords[MESSAGE_METADATA_KWARG]
    assert isinstance(value, ast.Attribute) and (
        isinstance(value.value, ast.Name) and value.value.id == "self"
    ) and value.attr == EXPECTED_VALUE_ATTR, (
        f"``{MESSAGE_METADATA_KWARG}`` at line {call.lineno} must be "
        f"``self.{EXPECTED_VALUE_ATTR}``, got {_describe(value)} — "
        "passing None (or any other shape) silently disables the "
        "T5.19 message_metadata prune; the wiring must be provably "
        "never-None."
    )
    return value


class TestCheckpointCleanupJobWiring:
    """The ``initialize()`` construction site wires the T5.19 repo."""

    def setup_method(self):
        self.source = MANAGER_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.calls = _find_checkpoint_cleanup_job_calls(self.tree)

    def test_exactly_one_construction_site(self):
        """Exactly ONE ``CheckpointCleanupJob(...)`` construction site
        exists in ``daemon/manager.py`` — a second site cannot appear
        silently unwired (the count is part of the contract; adding one
        means extending this test)."""
        assert len(self.calls) == 1, (
            f"Expected exactly 1 ``{CONSTRUCTOR_NAME}`` construction "
            f"site in manager.py; found {len(self.calls)} at lines "
            f"{[c.lineno for c in self.calls]}. A second construction "
            f"site would not carry the ``{MESSAGE_METADATA_KWARG}`` "
            "wiring by construction — wire it and update this pin "
            "deliberately."
        )

    def test_message_metadata_repo_kwarg_wired(self):
        """The construction passes ``message_metadata_repo`` wired to
        ``self._message_metadata_repo`` — never a literal ``None`` or
        any other shape (statically proven never-None)."""
        for call in self.calls:
            _require_message_metadata_repo_wired(call)

    def test_wiring_lives_in_initialize_method(self):
        """The construction site lives inside ``initialize()`` — pins
        the intent (boot-path wiring), not just the count."""
        info = _enclosing_function_info(self.tree, self.calls)
        names = set(info.values())
        assert names == {"initialize"}, (
            f"{CONSTRUCTOR_NAME} construction now lives in {names} — "
            "expected {'initialize'}; if the boot path moved, update "
            "this pin deliberately."
        )
