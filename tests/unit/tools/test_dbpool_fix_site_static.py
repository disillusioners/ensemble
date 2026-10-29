"""F8-a pin — no manager-engine transaction is open across an await (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F8.1–F8.9 and §5.2. This is the
static AST twin of the PG legs F8-b/c/d/e.

IT ALSO PINS THE STRICT CARRIER-FLAG CONSUMER (the A1 fix): the (c)
branch must consume the carrier flag with an IDENTITY test
(``live_carrier_exists is False``) — the LOOSE form
(``not live_carrier_exists``) treats an UNKNOWN flag (``None``: the
P0 snapshot never reached the (c) shape, so the check never ran) as
"no live carrier" and can enqueue a SECOND carrier for a message that
already has a live one (duplicate delivery). The twin fails if either
reconcile variant returns to the loose form.

THE THREE EXPECTED SITES (named here so a FOURTH is a deliberate
failure — the rule is a whole-tree invariant, and a new violation
anywhere in ``daemon/`` REDs this pack):

1. ``daemon/manager.py`` — ``_reconcile_deferred_report_async`` (the
   ROUTER-side reconcile): P0 read-decide scope → P1 content fetch
   (no manager-engine session open) → P2 write scope.
2. ``daemon/manager.py`` — ``_reconcile_deferred_report`` (the SWEEP-side
   twin): the same three-phase shape; the cross-thread bridge keeps its
   call shape and its 8.0 s timeout but moved OUT of the held scope
   (DR-26).
3. ``daemon/tools/instance.py`` — ``_register_child_completion_watcher``:
   the ``await _bus.watch(...)`` registration moved OUT of the
   ``WriteGuardSession`` scope (F8.5).

What this pin asserts: **no** ``ast.Await`` node, **no**
``run_coroutine_threadsafe(...).result(...)`` call, and **no** call to
the bridge-carrying fetch helpers (``_fetch_subshape_a_content_sync`` /
``_fetch_subshape_a_content_async``) inside the ``with``-body of a
manager-engine session scope anywhere in ``daemon/``.

The rule is scoped to the ``with``-body ONLY — a function-wide rule
would false-positive on the legitimate bridge calls that now live
OUTSIDE any scope.

Plantable mutation: insert an ``ast.Await`` (or a
``run_coroutine_threadsafe(...).result(...)``) inside the ``with``-body
of a manager-engine session scope in ``daemon/`` ⇒ RED.

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_fix_site_static.py --tb=short -q
"""
from __future__ import annotations

import ast
from pathlib import Path

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 6

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DAEMON_DIR = _REPO_ROOT / "daemon"

#: The manager-engine session scope forms (the rule's scope).
#:   ``with self._session_scope() as session:``      (manager / holder)
#:   ``with Session(<engine>) as session:``           (manager engine attr)
#:   ``with WriteGuardSession(...) as session:``      (the tool-lane proxy)
_SCOPE_MARKERS = ("_session_scope", "WriteGuardSession")

#: The fetch helpers whose bodies carry the cross-thread bridge — a
#: call to them INSIDE a held scope re-introduces the borrow-then-await.
_BRIDGE_CARRYING_HELPERS = (
    "_fetch_subshape_a_content_sync",
    "_fetch_subshape_a_content_async",
)


def _is_manager_session_scope(node: ast.With) -> bool:
    for item in node.items:
        expr = item.context_expr
        if isinstance(expr, ast.Call):
            func = expr.func
            if isinstance(func, ast.Attribute) and func.attr in _SCOPE_MARKERS:
                return True
            if isinstance(func, ast.Name) and func.id in _SCOPE_MARKERS:
                return True
            if isinstance(func, ast.Name) and func.id == "Session":
                for arg in expr.args:
                    if isinstance(arg, ast.Attribute) and arg.attr == "engine":
                        return True
    return False


def _violations_in_with_body(node: ast.With) -> list[str]:
    """The violation labels inside one scope's with-body."""
    found: list[str] = []
    for sub in ast.walk(node):
        if sub is node:
            continue
        if isinstance(sub, ast.Await):
            found.append(f"await at line {sub.lineno}")
            break
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "result":
            inner = sub.func.value
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "run_coroutine_threadsafe":
                found.append(f"run_coroutine_threadsafe(...).result(...) at line {sub.lineno}")
                break
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in _BRIDGE_CARRYING_HELPERS:
            found.append(
                f"bridge-carrying helper {sub.func.attr}() called at line {sub.lineno}"
            )
            break
    return found


def _scan() -> list[tuple[str, int, str]]:
    """Scan ``daemon/`` for manager-session scopes holding an await."""
    violations: list[tuple[str, int, str]] = []
    for path in sorted(_DAEMON_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover — a syntax error fails elsewhere
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.With) and _is_manager_session_scope(node):
                for label in _violations_in_with_body(node):
                    violations.append((str(path.relative_to(_REPO_ROOT)), node.lineno, label))
    return violations


class TestF8aNoAwaitInsideManagerSessionScope:
    """F8-a — the borrow-then-await is gone tree-wide."""

    def test_no_manager_session_scope_holds_an_await(self):
        violations = _scan()
        assert not violations, (
            "F8-a: a manager-engine session scope holds an await / a "
            "cross-thread bridge / a bridge-carrying fetch call — the "
            "backend would sit idle-in-transaction across it: "
            f"{violations}"
        )

    def test_the_three_named_sites_are_clean(self):
        """The three expected sites are named in the module docstring so
        a FOURTH violation is a deliberate failure."""
        named = (
            "_reconcile_deferred_report_async",
            "_reconcile_deferred_report",
            "_register_child_completion_watcher",
        )
        source = (_DAEMON_DIR / "manager.py").read_text() + (
            _DAEMON_DIR / "tools" / "instance.py"
        ).read_text()
        for name in named:
            assert name in source, (
                f"F8-a: the named site {name} disappeared — re-derive the "
                "expected-site list"
            )

    def test_the_manager_reconcile_sites_split_into_three_phases(self):
        """The P0/P1/P2 split is visible in the source: the P1 fetch call
        must NOT be inside a manager-engine session scope."""
        manager_source = (_DAEMON_DIR / "manager.py").read_text()
        tree = ast.parse(manager_source)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == "_reconcile_deferred_report_async"
            ):
                continue
            # The fetch call must exist…
            fetch_calls = [
                sub
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "_fetch_subshape_a_content_async"
            ]
            assert fetch_calls, "F8-a: the async reconcile lost its fetch call"
            # …and the carrier check must be hoisted OUT of every scope.
            # It is reached as ``asyncio.to_thread(self._has_live_...)``
            # — the helper appears as an ATTRIBUTE value, not as the
            # call's own attribute.
            carrier_calls = [
                sub
                for sub in ast.walk(node)
                if (
                    isinstance(sub, ast.Attribute)
                    and sub.attr == "_has_live_process_report_carrier"
                )
                or (
                    isinstance(sub, ast.Name)
                    and sub.id == "_has_live_process_report_carrier"
                )
            ]
            assert carrier_calls, (
                "F8-a: the async reconcile lost its carrier check"
            )
            for scope in ast.walk(node):
                if isinstance(scope, ast.With) and _is_manager_session_scope(scope):
                    for sub in ast.walk(scope):
                        if (
                            isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Attribute)
                            and sub.func.attr
                            in ("_fetch_subshape_a_content_async",)
                        ):
                            pytest.fail(
                                "F8-a: the content fetch is still inside a "
                                "manager-engine session scope"
                            )
            return
        pytest.fail("F8-a: _reconcile_deferred_report_async not found")


class TestF8StrictCarrierFlagConsumer:
    """The (c) branch consumes the carrier flag with an IDENTITY test.

    The carrier flag has three possible values at the (c) branch:

    * ``True``  — the check RAN and a live PROCESS_REPORT carrier exists
      ⇒ never revive;
    * ``False`` — the check RAN and found no live carrier ⇒ revive
      (the wedge-fix recovery);
    * ``None``  — UNKNOWN: the P0 snapshot did not reach the (c) shape
      (the ``carrier_check_needed`` gate), so the check never ran ⇒
      MUST NOT revive (a revival would enqueue a second carrier for a
      message that may already have a live one).

    The loose form (``not live_carrier_exists``) folds ``None`` into
    "no live carrier" ⇒ it can revive on an unknown flag. This twin
    pins the strict consumption in BOTH reconcile variants.

    Plantable mutation: replace ``live_carrier_exists is False`` with
    ``not live_carrier_exists`` in either variant ⇒ RED.
    """

    #: The two reconcile variants that carry the (c) branch.
    _VARIANTS = (
        "_reconcile_deferred_report",
        "_reconcile_deferred_report_async",
    )

    def _variants(self) -> dict:
        tree = ast.parse((_DAEMON_DIR / "manager.py").read_text())
        found = {}
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in self._VARIANTS
            ):
                found[node.name] = node
        return found

    def _carrier_flag_nodes(self, fn):
        """Every place the carrier flag is READ inside one variant."""
        return [
            sub
            for sub in ast.walk(fn)
            if isinstance(sub, ast.Name) and sub.id == "live_carrier_exists"
        ]

    def _loose_uses(self, fn) -> list[int]:
        """Line numbers of the LOOSE form (``not live_carrier_exists``)."""
        loose = []
        for sub in ast.walk(fn):
            if (
                isinstance(sub, ast.UnaryOp)
                and isinstance(sub.op, ast.Not)
                and isinstance(sub.operand, ast.Name)
                and sub.operand.id == "live_carrier_exists"
            ):
                loose.append(sub.lineno)
        return loose

    def _strict_comparisons(self, fn) -> list[tuple[int, ast.Compare]]:
        """The carrier flag's IDENTITY comparisons inside one variant."""
        out = []
        for sub in ast.walk(fn):
            if not isinstance(sub, ast.Compare):
                continue
            operands = [sub.left, *sub.comparators]
            if any(
                isinstance(o, ast.Name) and o.id == "live_carrier_exists"
                for o in operands
            ):
                out.append((sub.lineno, sub))
        return out

    def test_both_variants_compare_the_flag_by_identity(self):
        found = self._variants()
        for name in self._VARIANTS:
            assert name in found, (
                f"F8 strict-consumer: {name} not found in daemon/manager.py"
            )
            comparisons = self._strict_comparisons(found[name])
            assert comparisons, (
                f"F8 strict-consumer: {name} does not compare "
                "live_carrier_exists at all — the carrier flag must be "
                "consumed by an identity test"
            )
            for lineno, compare in comparisons:
                assert all(
                    isinstance(op, (ast.Is, ast.IsNot))
                    for op in compare.ops
                ), (
                    f"F8 strict-consumer: {name}:{lineno} compares "
                    "live_carrier_exists with a non-identity operator — "
                    "the strict form must be `is False` / `is not True` "
                    "(the loose `not live_carrier_exists` folds the "
                    "UNKNOWN flag into 'no live carrier')"
                )

    def test_the_loose_form_is_absent_from_both_variants(self):
        found = self._variants()
        for name in self._VARIANTS:
            loose = self._loose_uses(found[name])
            assert not loose, (
                f"F8 strict-consumer: {name} uses the LOOSE carrier form "
                f"`not live_carrier_exists` at line(s) {loose} — an "
                "UNKNOWN flag (None) must never trigger a c_revival "
                "(a second carrier for a message that may already have "
                "a live one)"
            )

    def test_the_strict_comparison_guards_the_c_revival_branch(self):
        """The strict comparison must be the (c) branch's CONDITION — not
        an incidental comparison elsewhere in the function."""
        found = self._variants()
        for name in self._VARIANTS:
            fn = found[name]
            carrier_ifs = []
            for node in ast.walk(fn):
                if not isinstance(node, ast.If):
                    continue
                test_src = ast.unparse(node.test)
                if "live_carrier_exists" not in test_src:
                    continue
                body_src = ast.unparse(node)
                if "c_revival" in body_src or "_TaskRevival" in body_src:
                    carrier_ifs.append(node)
            assert carrier_ifs, (
                f"F8 strict-consumer: {name} has no (c) branch whose "
                "condition consumes live_carrier_exists — the strict "
                "comparison must guard the c_revival branch"
            )
            for node in carrier_ifs:
                test_src = ast.unparse(node.test)
                assert "is False" in test_src or "is not True" in test_src, (
                    f"F8 strict-consumer: {name}:{node.lineno} — the "
                    f"(c) branch condition is `{test_src}`; it must use "
                    "the strict identity form (`live_carrier_exists is "
                    "False` / `is not True`)"
                )
