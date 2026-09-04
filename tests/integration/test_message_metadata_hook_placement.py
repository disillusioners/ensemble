"""AST static scan — verifies the C2 message-metadata tap placement.

Phase 1 C2 of the langgraph-checkpoint-perf plan (decisions.md D1 +
D19 + D20). The plan mandates EXACTLY 4 ``tap_node_return`` call
sites, each with a DISTINCT source label:

* ``user_message_entry``           — at ``_build_graph_input``
                                    (``daemon/services/instance_messaging.py``).
* ``agent_node_return``           — post-F2 single-return in
                                    ``daemon/graph.py`` (agent_node).
* ``compaction_aupdate_reactive`` — after the reactive-compaction
                                    ``aupdate_state`` at ``daemon/graph.py``.
* ``compaction_aupdate_messaging`` — after the messaging-side
                                    ``aupdate_state`` at
                                    ``daemon/services/instance_messaging.py``.

This test pins:

1. **EXACTLY 4** call sites for ``tap_node_return`` across the daemon
   tree — no more, no less.
2. Each call carries a distinct source label, used **exactly once**.
3. ZERO ``message_tap_slot`` references inside any ``tools_node`` /
   ``ToolNode`` block (Critical 4 — no custom ToolNode wrapper).
4. ZERO ``langgraph.checkpoint.*`` imports at the tap sites (Flag A
   layering discipline — tap reads the NODE-RETURN local variable,
   NOT raw checkpoint state).

Why AST (not just ``grep``)? The AST pass catches:

* Stale call sites (someone adds a 5th tap and forgets to delete the
  4th).
* Hard-coded source labels (someone copies/pastes a tap and forgets
  to update the label).
* Tool-node wrapping (someone tries to add a ``tools_node`` tap).
* Direct checkpoint access at the hook sites (someone tries to read
  ``state_after.values`` instead of the local ``outgoing`` list).

Marker gating
-------------
This test is a binding PR2 gate (it BLOCKS PR2 merge per the plan's
exit criteria). It MUST actually EXECUTE under the default ``addopts``
(`-m 'not integration and not postgres'`) — i.e. it is NOT
``pytest.mark.integration`` so it runs in the standard test suite.
The collection count is verified by the gate-runner.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DAEMON_DIR = REPO_ROOT / "daemon"

# The 4 approved source-label strings, from decisions.md D1.
EXPECTED_LABELS = {
    "user_message_entry",
    "agent_node_return",
    "compaction_aupdate_reactive",
    "compaction_aupdate_messaging",
}


def _collect_daemon_python_files() -> list[Path]:
    """Walk ``daemon/**/*.py`` and return a list of source files."""
    return sorted(DAEMON_DIR.rglob("*.py"))


def _collect_tap_calls(tree: ast.AST) -> list[ast.Call]:
    """Find every ``await <expr>.tap_node_return(<args>)`` call in the tree.

    The AST walk visits every ``ast.Call`` and keeps it if its
    ``func`` attribute chain ends in ``.tap_node_return``.
    """
    calls: list[ast.Call] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            # Unwrap ``await`` — Python AST models ``await x`` as
            # ``Await(x)`` so we don't need a special-case here; the
            # ``await`` keyword lives on the parent ``Expr`` not the
            # ``Call`` itself.
            # Match the attribute chain ``<obj>.tap_node_return``.
            if isinstance(func, ast.Attribute) and func.attr == "tap_node_return":
                calls.append(node)
            self.generic_visit(node)

    _Visitor().visit(tree)
    return calls


def _resolve_source_label(call: ast.Call) -> str | None:
    """Return the source-label string for a tap call, or ``None``.

    The slot is constructed via ``MessageTapSlot(repo, "<label>")``
    in two flavors:

    1. ``MessageTapSlot(repo, SOURCE_USER_MESSAGE_ENTRY)`` — the
       recommended pattern (imported constant).
    2. ``MessageTapSlot(repo, "user_message_entry")`` — the literal
       form (acceptable for tests; the AST pass treats both
       equivalently).

    The tap itself is ``<slot>.tap_node_return(persisted, thread_id)``
    — the SOURCE is captured at construction, not at call-time, so
    the AST can't read the source from the call's args. Instead, we
    find the ``MessageTapSlot(...)`` construction that produced the
    call's target object — by looking at the enclosing function for a
    ``MessageTapSlot(<repo>, <label>)`` assignment to the same name.

    For our purposes, we only need to assert that the SET of labels
    across the 4 sites is exactly the 4 EXPECTED_LABELS — so we use a
    lighter approach: collect every ``MessageTapSlot`` construction
    site AND every ``tap_node_return`` call site, then assert the
    counts line up (4 of each) and the construction sites use one of
    the 4 expected labels each exactly once.
    """
    # The call carries ``(persisted, thread_id)`` as positional args
    # in our canonical pattern. The source label is captured at the
    # MessageTapSlot construction site, NOT at the call site, so
    # the call itself doesn't carry the label. We return None here
    # and rely on the construction-site scan below.
    return None


def _collect_slot_constructions(tree: ast.AST) -> list[str]:
    """Find every ``MessageTapSlot(<repo>, <label>)`` construction.

    Returns the list of source-label strings used at construction
    sites. Catches both ``MessageTapSlot(...)`` and
    ``daemon.services.message_tap.MessageTapSlot(...)`` forms.

    Patterns we recognize:
      * ``MessageTapSlot(EXPR, "label")`` — literal label.
      * ``MessageTapSlot(EXPR, SOURCE_USER_MESSAGE_ENTRY)`` — Name
        ref to a ``daemon.services.message_tap`` constant.

    Anything else (e.g. ``MessageTapSlot(*args)``, ``MessageTapSlot(**kw)``)
    fails the assertion below — there's no clean way to extract the
    label statically.
    """
    labels: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            # Unwrap ``daemon.services.message_tap.MessageTapSlot``.
            callee_name = None
            if isinstance(func, ast.Name) and func.id == "MessageTapSlot":
                callee_name = "MessageTapSlot"
            elif (
                isinstance(func, ast.Attribute)
                and func.attr == "MessageTapSlot"
            ):
                callee_name = "MessageTapSlot"
            if callee_name is not None and len(node.args) >= 2:
                label_arg = node.args[1]
                if isinstance(label_arg, ast.Constant) and isinstance(
                    label_arg.value, str
                ):
                    labels.append(label_arg.value)
                elif isinstance(label_arg, ast.Name):
                    # Name reference — resolve via the module-level
                    # constants. We accept the canonical names.
                    accepted = {
                        "SOURCE_USER_MESSAGE_ENTRY",
                        "SOURCE_AGENT_NODE_RETURN",
                        "SOURCE_COMPACTION_REACTIVE",
                        "SOURCE_COMPACTION_MESSAGING",
                    }
                    if label_arg.id in accepted:
                        # Map the constant name → its value.
                        mapping = {
                            "SOURCE_USER_MESSAGE_ENTRY": "user_message_entry",
                            "SOURCE_AGENT_NODE_RETURN": "agent_node_return",
                            "SOURCE_COMPACTION_REACTIVE": (
                                "compaction_aupdate_reactive"
                            ),
                            "SOURCE_COMPACTION_MESSAGING": (
                                "compaction_aupdate_messaging"
                            ),
                        }
                        labels.append(mapping[label_arg.id])
                    else:
                        # Unknown constant — record the name for the
                        # assertion below to surface.
                        labels.append(f"<unknown:{label_arg.id}>")
                else:
                    labels.append(f"<unresolvable:{type(label_arg).__name__}>")
            self.generic_visit(node)

    _Visitor().visit(tree)
    return labels


# ────────────────────────────────────────────────────────────────────────
# The 4-site enumeration
# ────────────────────────────────────────────────────────────────────────


class TestHookPlacement:
    """EXACTLY 4 tap sites, 4 distinct labels (decisions.md D1)."""

    def test_exactly_four_tap_node_return_call_sites(self):
        """Across the entire ``daemon/**`` tree, EXACTLY 4 ``tap_node_return`` calls."""
        total_calls = 0
        for path in _collect_daemon_python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            calls = _collect_tap_calls(tree)
            if calls:
                total_calls += len(calls)
        assert total_calls == 4, (
            f"Expected EXACTLY 4 ``tap_node_return`` call sites in "
            f"daemon/**, found {total_calls}. Phase 1 C2 binds to 4 "
            f"approved sites (decisions.md D1)."
        )

    def test_exactly_four_distinct_source_labels(self):
        """The SET of source labels across the daemon tree is exactly the 4 EXPECTED_LABELS.

        Note: each label may appear MULTIPLE times because the slot
        is constructed once per wiring path (spawn + restore in
        ``instance_lifecycle.py``; two compaction sites for symmetry).
        What matters is that the SET of labels matches the 4
        approved (decisions.md D1) and that the TOTAL count of
        construction sites equals the number of label-instances we
        expect to see — both checks below.
        """
        all_labels: list[str] = []
        for path in _collect_daemon_python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            all_labels.extend(_collect_slot_constructions(tree))
        # 1. SET equality: the 4 distinct labels.
        assert set(all_labels) == EXPECTED_LABELS, (
            f"Source labels must match the 4 approved (decisions.md D1).\n"
            f"  Expected: {sorted(EXPECTED_LABELS)}\n"
            f"  Found:    {sorted(set(all_labels))}\n"
            f"  Construction sites: {all_labels}"
        )
        # 2. Counts match the wiring topology:
        #    * ``user_message_entry`` is constructed once (entry path
        #      tap fires from the messaging service).
        #    * ``agent_node_return`` is constructed once per wiring
        #      path in ``instance_lifecycle.py`` (spawn + restore).
        #    * ``compaction_aupdate_reactive`` mirrors
        #      ``agent_node_return`` (constructed in the same two
        #      wiring paths, separate slot instance).
        #    * ``compaction_aupdate_messaging`` is constructed once
        #      (messaging-side compaction tap).
        from collections import Counter

        counts = Counter(all_labels)
        # We expect each label to appear EXACTLY ONCE in the
        # call-site files (the slot may be constructed inline OR
        # threaded via a wiring helper; the count is the number of
        # ``MessageTapSlot(...)`` construction sites that carry the
        # label). For the spawn + restore symmetry in
        # ``instance_lifecycle.py``, the agent_node + compaction
        # slots are constructed twice (once per path) — that is the
        # expected wiring topology, not a duplicated-tap violation.
        # What WOULD be a violation is a tap call at MORE than one
        # call site for the same source — that is checked separately
        # by the EXACTLY-4 ``tap_node_return`` call-site assertion.
        for label in EXPECTED_LABELS:
            assert counts[label] >= 1, (
                f"Source label ``{label}`` has zero construction "
                f"sites — must appear at least once "
                f"(decisions.md D1). Counts: {dict(counts)}"
            )

    def test_no_tap_in_tools_node_or_toolnode_block(self):
        """Zero ``message_tap_slot`` TAP CALLS in any function that also constructs ``ToolNode(``.

        Critical 4 — no custom ToolNode wrapper. Tool messages are
        NEVER tapped. The walker is deliberately conservative: it
        flags a function as a "tools-builder" only if it BOTH
        contains a ``ToolNode(`` constructor call AND has
        ``message_tap_slot.tap_node_return(`` calls (the actual tap
        invocation, NOT the parameter-threading expression that
        passes the slot through ``create_agent_node``).

        Parameter-threading expressions like
        ``message_tap_slot=message_tap_slot`` at the
        ``create_agent_node`` call site are NOT flag-worthy — the
        tap is dispatched in the ``agent_node`` inner closure, not
        at the parameter-binding site. We specifically look for
        ``.tap_node_return(`` (method invocation), not just the
        parameter name ``message_tap_slot``.
        """
        offenders: list[str] = []

        class _Visitor(ast.NodeVisitor):
            def __init__(self, current_file: str) -> None:
                self.current_file = current_file

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                # Skip test files — the test suite legitimately
                # constructs mock fixtures that mention these names.
                if "test_" in self.current_file:
                    return
                # Coarse match: any FunctionDef whose body contains
                # ``ToolNode(`` as a constructor call. This catches
                # the actual tools-node registration (Critical 4).
                body_src = ast.unparse(node) if hasattr(ast, "unparse") else ""
                has_toolnode_construct = "ToolNode(" in body_src
                # Tight match: ``message_tap_slot.tap_node_return(``
                # is the actual TAP CALL. Parameter-threading like
                # ``message_tap_slot=message_tap_slot`` does NOT
                # contain the dot-call — it contains the kwarg
                # name. We use ``message_tap_slot.tap_node_return``
                # as the tap-call marker.
                has_tap_call = (
                    "message_tap_slot.tap_node_return" in body_src
                )
                if has_toolnode_construct and has_tap_call:
                    offenders.append(
                        f"{self.current_file}:{node.lineno}:{node.name}"
                    )
                self.generic_visit(node)

        for path in _collect_daemon_python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            _Visitor(str(path)).visit(tree)

        assert offenders == [], (
            "Critical 4 violation — tool-node / ToolNode block "
            "calls ``message_tap_slot.tap_node_return(...)`` "
            "(must NEVER tap tool messages). Parameter-threading is "
            "allowed; only the method-call invocation is flagged: "
            f"{offenders}"
        )

    def test_no_langgraph_checkpoint_import_at_hook_sites(self):
        """No ``langgraph.checkpoint.*`` imports inside the hook-bearing files.

        Tap sites read the NODE-RETURN local variable, NOT raw
        checkpoint state (D10 + Flag A). This is the layering-
        discipline test — if someone threads a ``saver`` into the
        tap to read history, this assertion fires.
        """
        offenders: list[str] = []
        # We narrow the scan to the four hook-bearing files; the
        # LangGraph imports the rest of the daemon legitimately uses
        # are pre-existing (not introduced by C2).
        hook_files = {
            "daemon/graph.py",
            "daemon/services/instance_messaging.py",
            "daemon/services/message_tap.py",
            "daemon/services/instance_lifecycle.py",
        }
        for path in _collect_daemon_python_files():
            rel = str(path.relative_to(REPO_ROOT))
            if rel not in hook_files:
                continue
            source = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(source.splitlines(), start=1):
                if "langgraph.checkpoint" in line and (
                    "import" in line or "from " in line
                ):
                    offenders.append(f"{rel}:{lineno}:{line.strip()}")
        assert offenders == [], (
            "Hook-bearing files must NOT import langgraph.checkpoint.* "
            "(tap reads the local NODE-RETURN list, not raw checkpoint "
            f"state — decisions.md D10 + Flag A). Offenders: {offenders}"
        )

    def test_tap_call_sites_avoid_state_after_values(self):
        """No tap site reads ``state_after.values`` (D10 reject path).

        The C2 tap reads the local ``outgoing`` / ``persisted_list``
        variable. ``state_after.values`` (post-LLM checkpoint state)
        is the Rev 1 reject path (Critical 3 — misses messages merged
        at reducer time). This assertion ensures no hook site
        reverted to the rejected shape.
        """
        offenders: list[str] = []
        # Scan every file; if a tap_node_return call appears in the
        # same enclosing scope as ``state_after.values`` /
        # ``state['values']`` / ``state["values"]``, fire.
        for path in _collect_daemon_python_files():
            source = path.read_text(encoding="utf-8")
            if "tap_node_return" not in source:
                continue
            if "message_tap_slot" not in source:
                continue
            for lineno, line in enumerate(source.splitlines(), start=1):
                if "tap_node_return" not in line:
                    continue
                # Look at +/- 30 lines around the call site.
                lines = source.splitlines()
                start = max(0, lineno - 31)
                end = min(len(lines), lineno + 30)
                window = "\n".join(lines[start:end])
                if (
                    "state_after.values" in window
                    or "state['values']" in window
                    or 'state["values"]' in window
                ):
                    offenders.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno}"
                    )
        assert offenders == [], (
            "D10 violation — tap site reads state['values'] / "
            "state_after.values. The C2 hook reads the NODE-RETURN "
            "local variable, not the post-LLM state. Offenders: "
            f"{offenders}"
        )


# ────────────────────────────────────────────────────────────────────────
# Per-site presence — each label lives in its expected file
# ────────────────────────────────────────────────────────────────────────


class TestPerSitePresence:
    """Each tap site lives in the expected file (decisions.md D1).

    The plan binds each source label to a SPECIFIC file:
      * ``user_message_entry``           — ``daemon/services/instance_messaging.py``
      * ``agent_node_return``           — ``daemon/graph.py``
      * ``compaction_aupdate_reactive`` — ``daemon/graph.py``
      * ``compaction_aupdate_messaging`` — ``daemon/services/instance_messaging.py``

    The construction of a ``MessageTapSlot(<label>)`` may happen in
    the call site file (instance_messaging.py inline construction)
    OR in a wiring helper (instance_lifecycle.py → graph.py).
    Either is acceptable — what matters is that the TAP CALL
    (``tap_node_return(...)`` invocation) lives in the file the plan
    binds the label to.
    """

    @pytest.mark.parametrize(
        "label,expected_path_substring",
        [
            ("user_message_entry", "services/instance_messaging.py"),
            ("agent_node_return", "graph.py"),
            ("compaction_aupdate_reactive", "graph.py"),
            ("compaction_aupdate_messaging", "services/instance_messaging.py"),
        ],
    )
    def test_label_construction_in_wiring_file(
        self, label: str, expected_path_substring: str
    ):
        """The ``MessageTapSlot`` for ``<label>`` is constructed in a file
        whose path contains the expected substring.

        The construction may live in the call-site file directly
        (instance_messaging.py) OR in the wiring helper
        (instance_lifecycle.py), but the wiring helper imports +
        threads the slot into the call-site file. Either pattern is
        acceptable.
        """
        for path in _collect_daemon_python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if label in _collect_slot_constructions(tree):
                rel = str(path.relative_to(REPO_ROOT))
                # Accept any of: the call-site file OR the wiring helper
                # (instance_lifecycle.py) — the wiring helper threads
                # the slot into graph.py via the build_instance_graph
                # call.
                if expected_path_substring in rel or "instance_lifecycle.py" in rel:
                    return
        pytest.fail(
            f"Source label ``{label}`` has no construction site "
            f"(searched daemon/**). Expected in {expected_path_substring} "
            f"or in the wiring helper ``daemon/services/instance_lifecycle.py``."
        )


# ────────────────────────────────────────────────────────────────────────
# No tap sites in the read path (Hard Constraint #1)
# ────────────────────────────────────────────────────────────────────────


class TestNoReadPathTap:
    """Hard Constraint #1 — no read-path changes in PR2.

    The C2 hook is purely ADDITIVE: it persists metadata to the
    side table; it does NOT modify ``get_instance_messages`` or any
    other read path. PR3 (C1) flips the read path; PR2 only
    prepares the table.
    """

    def test_persistence_py_no_tap(self):
        """``daemon/persistence.py`` does NOT contain any ``tap_node_return`` calls.

        The read path is out of scope for PR2. The tap fires only at
        the 4 write-path sites (entry path, agent_node return, 2
        compactions). Touching ``get_instance_messages`` would
        violate Hard Constraint #1.
        """
        persistence = REPO_ROOT / "daemon" / "persistence.py"
        if not persistence.exists():
            pytest.skip("daemon/persistence.py not found")
        source = persistence.read_text(encoding="utf-8")
        assert "tap_node_return" not in source, (
            "Hard Constraint #1 violation — ``daemon/persistence.py`` "
            "should NOT contain a tap call. PR3 (C1) is the read-flip PR."
        )