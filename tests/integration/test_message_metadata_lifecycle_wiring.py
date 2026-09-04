"""PR3 fold item 2 — lifecycle wiring pin for the ``MessageTapSlot`` kwargs.

Binding follow-up from the PR2 external review: BOTH lifecycle paths
that build an instance graph pass ``message_tap_slot`` +
``compaction_tap_slot`` to ``build_instance_graph``:

* spawn path   — ``daemon/services/instance_lifecycle.py`` (~line 1284,
  inside ``spawn_instance``)
* restore path — ``daemon/services/instance_lifecycle.py`` (~line 3272,
  inside ``_restore_instance``)

Dropping either kwarg (or passing ``None``) would silently empty the
``message_metadata`` side table in production — the C1 read flip would
degrade every timestamp to the ``state.ts`` fallback with ZERO test
failures at the time of the drop. This test closes that hole: it
statically asserts both call sites pass BOTH slot kwargs, each
constructing a fresh ``MessageTapSlot`` against
``self._manager.message_metadata_repo`` (non-``None`` by shape).

Why AST + static (the "stronger/cheaper" pick from the PR3 brief): a
runtime spawn-path integration test would need a fully armed manager
fixture (repos, registry, config, DB) to assert rows land after a real
dispatch — expensive and brittle. The AST pin catches the exact
regression class (kwarg dropped / None'd / repo arg severed) at zero
runtime cost, for BOTH paths (the restore path is even harder to drive
in a test than spawn).

Marker gating: NO ``integration`` marker — must run under default
``addopts`` (collection count verified in the recorded PR3 gate run).
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LIFECYCLE_PATH = REPO_ROOT / "daemon" / "services" / "instance_lifecycle.py"

SLOT_KWARGS = ("message_tap_slot", "compaction_tap_slot")


def _find_build_instance_graph_calls(tree: ast.AST) -> list[ast.Call]:
    """Every ``build_instance_graph(...)`` call site in the module."""
    calls: list[ast.Call] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if isinstance(func, ast.Name) and func.id == "build_instance_graph":
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


class TestLifecycleTapSlotWiring:
    """Both ``build_instance_graph`` lifecycle sites pass BOTH slots."""

    def setup_method(self):
        self.source = LIFECYCLE_PATH.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.calls = _find_build_instance_graph_calls(self.tree)

    def test_exactly_two_lifecycle_call_sites(self):
        """The wiring pin covers BOTH paths — and a THIRD call site
        cannot appear silently unwired (the count is part of the
        contract; adding one means extending this test)."""
        assert len(self.calls) == 2, (
            f"Expected exactly 2 ``build_instance_graph`` call sites in "
            f"instance_lifecycle.py (spawn + restore); found "
            f"{len(self.calls)} at lines {[c.lineno for c in self.calls]}. "
            "A new call site MUST wire message_tap_slot + "
            "compaction_tap_slot and update this pin."
        )

    def test_spawn_and_restore_paths_both_wired(self):
        """Each call site passes BOTH slot kwargs, each a fresh
        ``MessageTapSlot(...)`` construction whose repo arg is the
        shared manager repo attribute — never a literal ``None``."""
        assert self.calls, "no build_instance_graph call sites found"
        for call in self.calls:
            keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
            for kwarg in SLOT_KWARGS:
                assert kwarg in keywords, (
                    f"build_instance_graph call at line {call.lineno} is "
                    f"MISSING the ``{kwarg}`` kwarg — dropping it empties "
                    "the message_metadata side table in production (the "
                    "write side silently stops; the C1 read flip degrades "
                    "to state.ts for every message)."
                )
                value = keywords[kwarg]
                assert isinstance(value, ast.Call), (
                    f"``{kwarg}`` at line {call.lineno} is not a "
                    f"MessageTapSlot construction ({_describe(value)}) — "
                    "passing None/a stale handle severs the tap wiring."
                )
                func = value.func
                assert (
                    isinstance(func, ast.Name) and func.id == "MessageTapSlot"
                ) or (
                    isinstance(func, ast.Attribute)
                    and func.attr == "MessageTapSlot"
                ), (
                    f"``{kwarg}`` at line {call.lineno} must construct a "
                    f"MessageTapSlot ({_describe(value)})"
                )
                # The repo argument: an attribute chain rooted at the
                # manager (``self._manager.message_metadata_repo``) —
                # definitely not a literal None.
                assert value.args, (
                    f"``{kwarg}`` at line {call.lineno} constructs "
                    "MessageTapSlot with NO repo argument"
                )
                repo_arg = value.args[0]
                assert not (
                    isinstance(repo_arg, ast.Constant) and repo_arg.value is None
                ), (
                    f"``{kwarg}`` at line {call.lineno} constructs "
                    "MessageTapSlot with repo=None — the tap would be a "
                    "silent no-op."
                )

    def test_wiring_lives_in_spawn_and_restore_methods(self):
        """The two sites are the spawn path and the restore path
        (``spawn_instance`` / ``_restore_instance``) — pins the intent,
        not just the count."""
        info = _enclosing_function_info(self.tree, self.calls)
        assert len(info) == 2
        names = set(info.values())
        assert names == {"spawn_instance", "_restore_instance"}, (
            f"build_instance_graph call sites now live in {names} — "
            "expected {'spawn_instance', '_restore_instance'}; if the "
            "methods were renamed, update this pin deliberately."
        )

    def test_source_labels_cover_both_slots(self):
        """The two slots at each site carry the two agent-node-side
        source labels (``SOURCE_AGENT_NODE_RETURN`` /
        ``SOURCE_COMPACTION_REACTIVE`` imports or literals) — the
        per-label AST gate in test_message_metadata_hook_placement.py
        depends on these constructions."""
        assert self.calls
        for call in self.calls:
            keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
            labels = set()
            for kwarg in SLOT_KWARGS:
                value = keywords.get(kwarg)
                assert isinstance(value, ast.Call)
                if len(value.args) >= 2:
                    label = value.args[1]
                    if isinstance(label, ast.Name):
                        labels.add(label.id)
                    elif isinstance(label, ast.Constant):
                        labels.add(label.value)
            assert labels == {
                "SOURCE_AGENT_NODE_RETURN",
                "SOURCE_COMPACTION_REACTIVE",
            }, (
                f"Slot source labels at line {call.lineno}: {labels} — "
                "expected SOURCE_AGENT_NODE_RETURN + "
                "SOURCE_COMPACTION_REACTIVE."
            )
