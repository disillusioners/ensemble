"""Agent group chat P6 — the wiring pins for the two P6 seams.

Structural pins (the AST shape the house uses for cross-module wiring
— see ``tests/integration/test_checkpoint_cleanup_job_wiring_pin.py``):

* **EXACTLY ONE ``RoomPostTurnCap(...)`` construction site** in the
  daemon tree (the manager) and **EXACTLY ONE ``RoomGcSweeper(...)``**
  — a second construction site would silently disable the wiring with
  every unit test still green (the silent-drop class).
* The manager owns the manager-level counter registry
  (``self._room_post_turn_counter``) — the P4 latch's shape.
* **THE GC JOB IS ON THE MAINTENANCE SEAM** — exactly one
  ``self._maintenance_service.register("room_gc_sweep", ...)`` call
  site, and the sweeper's ``execute`` is the registered callable (the
  job is not registered against a stale handle).
* **THE CAP CHOKEPOINT IS PRE-APPEND** — in ``daemon/tools/room.py``'s
  ``room_post``, the ``check(...)`` call on the cap appears BEFORE the
  ``append_message(...)`` call: the append is the commit point of the
  room log, so a budget refusal that came after it would leave a row
  behind (the pin would catch that reordering).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DAEMON_DIR = REPO_ROOT / "daemon"
MANAGER = DAEMON_DIR / "manager.py"
TOOLS_ROOM = DAEMON_DIR / "tools" / "room.py"


def _daemon_trees() -> list[tuple[Path, ast.Module]]:
    trees: list[tuple[Path, ast.Module]] = []
    for path in sorted(DAEMON_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        trees.append((path, ast.parse(path.read_text(encoding="utf-8"))))
    return trees


def _construction_sites(class_name: str) -> list[tuple[Path, ast.Call]]:
    """Every ``ClassName(...)`` construction site under ``daemon/``."""
    sites: list[tuple[Path, ast.Call]] = []
    for path, tree in _daemon_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else ""
            )
            if name == class_name:
                sites.append((path, node))
    return sites


def _attribute_assignment(path: Path, attribute: str) -> list[ast.AST]:
    """Every assignment to ``self.<attribute>`` (annotated or not)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == attribute
            ):
                hits.append(node)
    return hits


def test_the_post_cap_has_exactly_one_construction_site():
    sites = _construction_sites("RoomPostTurnCap")
    assert len(sites) == 1, (
        f"RoomPostTurnCap must be constructed EXACTLY once under daemon/ — "
        f"found {[(str(p.relative_to(REPO_ROOT)), n.lineno) for p, n in sites]}"
    )
    assert sites[0][0] == MANAGER


def test_the_gc_sweeper_has_exactly_one_construction_site():
    sites = _construction_sites("RoomGcSweeper")
    assert len(sites) == 1, (
        f"RoomGcSweeper must be constructed EXACTLY once under daemon/ — "
        f"found {[(str(p.relative_to(REPO_ROOT)), n.lineno) for p, n in sites]}"
    )
    assert sites[0][0] == MANAGER


def test_the_manager_owns_the_counter_registry():
    """The registry is a MANAGER attribute (the P4 latch's shape)."""
    assignments = _attribute_assignment(MANAGER, "_room_post_turn_counter")
    assert len(assignments) == 1, (
        "the manager must own exactly one `_room_post_turn_counter` "
        f"assignment — found {len(assignments)}"
    )


def test_the_gc_job_is_registered_on_the_maintenance_seam():
    tree = ast.parse(MANAGER.read_text(encoding="utf-8"))
    registrations: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "register":
            continue
        if not isinstance(func.value, ast.Attribute):
            continue
        if func.value.attr != "_maintenance_service":
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            if node.args[0].value == "room_gc_sweep":
                registrations.append(node)
    assert len(registrations) == 1, (
        "the room GC sweep must be registered EXACTLY once on the "
        f"maintenance seam — found {len(registrations)}"
    )
    # The registered callable is the sweeper's own execute, and the
    # interval is the shared 6h constant (architecture §2.2).
    call = registrations[0]
    assert isinstance(call.args[1], ast.Name)
    assert call.args[1].id == "ROOM_GC_INTERVAL_HOURS"
    assert isinstance(call.args[2], ast.Attribute)
    assert call.args[2].attr == "execute"
    assert isinstance(call.args[2].value, ast.Attribute)
    assert call.args[2].value.attr == "_room_gc_sweeper"


def _room_post_function(tree: ast.Module) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "room_post":
            return node
    raise AssertionError("room_post not found in daemon/tools/room.py")


def _call_line(function: ast.AsyncFunctionDef, attribute: str) -> int:
    """The line of the FIRST ``<something>.<attribute>(...)`` call."""
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == attribute:
            return node.lineno
    raise AssertionError(f"no .{attribute}(...) call inside {function.name}")


def _name_call_line(function: ast.AsyncFunctionDef, name: str) -> int:
    """The line of the FIRST ``<name>(...)`` call (a bare name, not a
    method attribute)."""
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == name:
            return node.lineno
    raise AssertionError(f"no {name}(...) call inside {function.name}")


def test_the_cap_chokepoint_sits_before_the_append():
    """The append is the COMMIT POINT of the room log — the budget
    check must precede it (the refusal must never leave a row), and —
    the M2 consume-on-success adjudication (A-P6-25) — the CHARGE must
    FOLLOW it: the slot is spent only after the append lands, so a
    failed append never consumes the budget."""
    tree = ast.parse(TOOLS_ROOM.read_text(encoding="utf-8"))
    function = _room_post_function(tree)
    check_line = _name_call_line(function, "_room_post_budget_check")
    append_line = _call_line(function, "append_message")
    charge_line = _name_call_line(function, "_room_post_budget_charge")
    assert check_line < append_line, (
        f"the cap check (line {check_line}) must precede the append "
        f"(line {append_line}) in room_post"
    )
    assert append_line < charge_line, (
        f"the budget charge (line {charge_line}) must follow the append "
        f"(line {append_line}) in room_post — the slot is spent only "
        f"after the append lands (A-P6-25, M2)"
    )
