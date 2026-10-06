"""PROBE (Phase A, diagnosis-only) — designer OD-lane MCP binding defect.

Discriminating repro for the spawn-lane asymmetry root cause:

  The agent-facing ``spawn_instance`` TOOL (daemon/tools/instance.py) calls
  the SYNC ``manager.spawn_instance()`` which performs NO MCP preload, while
  every healthy creation lane (HTTP API, sources mapper, job processor,
  invoke_agent_and_wait) routes through async ``spawn_instance_with_mcp()``
  which preloads ``McpService._tools_cache[instance_id]`` FIRST.

  Live consequence: designer instances spawned by leader dispatches (the
  only MCP-dependent class the leader spawns via the bare tool) bind ZERO
  mcp_* tools; the same class spawned via HTTP binds 17 (live log
  ensemble.log.4:9537-9539, instance a95d7884).

Tests:
  T1  BROKEN-LANE CONTRACT   — every bare ``manager.spawn_instance(`` call
      site in the agent-tool / router / slack-thread lanes must be
      accompanied (same enclosing function or an ancestor in the same
      file) by a preload seam (``ensure_mcp_preloaded`` or
      ``spawn_instance_with_mcp``).  FAILS ON BASE — this is the
      discriminating repro.  PASSES after the fix.
  T2  HEALTHY-LANE PIN      — the five healthy lanes call
      ``spawn_instance_with_mcp``; the wrapper preloads BEFORE the sync
      spawn.  PASSES ON BASE (no-regression guard).
  T3  CACHE-MECHANICS PIN   — preload idempotency makes ANY existing entry
      (even []) permanent; get_mcp_tools silently defaults to []; the
      "Loaded N MCP tools" log is guarded by ``if mcp_tools:``; lifecycle
      spawn is sync and contains no preload; get_instance short-circuits
      on in-memory BEFORE the cold-restore backfill.  PASSES ON BASE
      (pins why the failure is silent and why live instances stay broken).
  T4  ASYNC-CAPABILITY PIN  — the L1 tool is ``async def`` and ALREADY
      awaits a coroutine before its spawn call (resolve_spawn_snapshot),
      while manager.spawn_instance is sync and the preload seams are
      async.  PASSES ON BASE (proves fix shape (a)/(b-at-lane) is
      viable without any wrapper surgery).

Run:  python3 tests/unit/probe_designer_od_lane_binding.py
      (stdlib only — no daemon imports, no venv needed)
      ENS_PROBE_ROOT=/tmp/patched-copy python3 ... to prove pass@fix
      against a patched copy without touching daemon/.

UNCOMMITTED diagnosis artifact — Phase B (fix task) replaces this with a
behavioral pinned test driving the real tool with a FakeManager.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("ENS_PROBE_ROOT", Path(__file__).resolve().parents[2]))

PRELOAD_SEAMS = {"ensure_mcp_preloaded", "spawn_instance_with_mcp"}
BARE_SPAWN_TARGETS = {"spawn_instance"}  # attribute name only, NOT *_with_mcp

# (file, description) — lanes that spawn children via the bare sync facade.
BROKEN_LANE_FILES = [
    ("daemon/tools/instance.py", "L1 agent-tool spawn + councilor spawns"),
    ("daemon/routers/mappings.py", "L3 latent: agent-mappings router"),
    ("daemon/sources/adapters/slack/thread_manager.py", "L6 latent: slack thread create"),
]

HEALTHY_LANE_FILES = [
    ("daemon/routers/instances.py", "L2 HTTP POST /api/instances"),
    ("daemon/sources/mapper.py", "external chat-source sessions"),
    ("daemon/utils.py", "invoke_agent_and_wait (explore/experience/chart/compare)"),
    ("daemon/services/job_processor.py", "public Jobs lane"),
    ("daemon/services/job_feedback_observer.py", "observer retry lane"),
]


def _parse(rel: str) -> ast.AST:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"), filename=rel)


def _call_name(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    return getattr(f, "id", "")


def _bare_spawn_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """All Call sites whose attribute is exactly `spawn_instance` (bare sync
    facade) — excludes spawn_instance_with_mcp and lifecycle-internal calls
    (those use self._lifecycle_service / self.spawn_instance inside
    manager.py, which we never scan)."""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in BARE_SPAWN_TARGETS:
                recv = node.func.value
                recv_name = getattr(recv, "id", None) or getattr(recv, "attr", None)
                if recv_name in {"manager", "_manager", "_instance_manager"}:
                    hits.append((node.lineno, f"line {node.lineno}"))
    return hits


def _func_nodes(tree: ast.AST) -> list[ast.AST]:
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _enclosing_functions(tree: ast.AST, lineno: int) -> list[ast.AST]:
    """Function defs whose span contains lineno, outermost first."""
    return sorted(
        (f for f in _func_nodes(tree) if f.lineno <= lineno <= f.end_lineno),
        key=lambda f: f.lineno,
    )


def _subtree_refs_preload(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id in PRELOAD_SEAMS:
            return True
        if isinstance(node, ast.Attribute) and node.attr in PRELOAD_SEAMS:
            return True
    return False


def _await_calls(fn: ast.AST) -> list[str]:
    return [ _call_name(n.value) for n in ast.walk(fn)
             if isinstance(n, ast.Await) and isinstance(n.value, ast.Call) ]


# ────────────────────────────────────────────────────────────────────────
def t1_broken_lanes_preload() -> list[str]:
    """THE DISCRIMINATOR. Fails on base; passes after the fix."""
    errors: list[str] = []
    for rel, desc in BROKEN_LANE_FILES:
        tree = _parse(rel)
        for lineno, _ in _bare_spawn_calls(tree):
            enclosing = _enclosing_functions(tree, lineno)
            if any(_subtree_refs_preload(f) for f in enclosing):
                continue
            nearest = enclosing[-1].name if enclosing else "<module>"
            errors.append(
                f"{rel}:{lineno} — bare sync manager.spawn_instance() with NO "
                f"MCP preload (enclosing fn: {nearest}; lane: {desc})"
            )
    return errors


def t2_healthy_lanes_pin() -> list[str]:
    errors: list[str] = []
    for rel, desc in HEALTHY_LANE_FILES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        if "spawn_instance_with_mcp" not in src:
            errors.append(f"{rel} ({desc}) lost its spawn_instance_with_mcp call")
    # Wrapper ordering: ensure_mcp_preloaded awaited BEFORE the sync spawn.
    tree = _parse("daemon/manager.py")
    for fn in _func_nodes(tree):
        if fn.name == "spawn_instance_with_mcp":
            seq = [
                n.lineno
                for n in ast.walk(fn)
                if (isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                    and _call_name(n.value) == "ensure_mcp_preloaded")
                or (isinstance(n, ast.Call) and _call_name(n) == "spawn_instance")
            ]
            if len(seq) < 2 or seq[0] >= seq[1]:
                errors.append(
                    f"manager.py spawn_instance_with_mcp: preload must precede "
                    f"sync spawn (got sequence {seq})"
                )
    return errors


def t3_cache_mechanics_pin() -> list[str]:
    errors: list[str] = []
    src = (ROOT / "daemon/services/mcp_service.py").read_text(encoding="utf-8")
    # Sole writer + idempotency early-return (even [] entries are permanent).
    if "if instance_id in self._tools_cache:\n                return" not in src:
        errors.append("mcp_service.preload_mcp_tools: idempotency early-return drifted")
    if "def get_mcp_tools" not in src or ".get(instance_id, [])" not in src:
        errors.append("mcp_service.get_mcp_tools: silent-[] default drifted")
    # Consumer log guarded by `if mcp_tools:` — empty cache ⇒ zero log output.
    isrc = (ROOT / "daemon/tools/instance.py").read_text(encoding="utf-8")
    if "if mcp_tools:\n        # Extract MCP tool names" not in isrc:
        errors.append("instance.py: 'Loaded N MCP tools' guard drifted (must stay silent on empty cache)")
    # Lifecycle spawn is sync and preload-free (the violated assumption).
    ltree = _parse("daemon/services/instance_lifecycle.py")
    spawn_fn = next((f for f in _func_nodes(ltree) if f.name == "spawn_instance"), None)
    if spawn_fn is None:
        errors.append("instance_lifecycle.spawn_instance not found")
    else:
        if isinstance(spawn_fn, ast.AsyncFunctionDef):
            errors.append("instance_lifecycle.spawn_instance became async — re-check fix shape")
        if _subtree_refs_preload(spawn_fn):
            errors.append("instance_lifecycle.spawn_instance now references a preload seam (unexpected for sync path)")
    # get_instance: in-memory short-circuit BEFORE cold-restore backfill.
    get_fn = next((f for f in _func_nodes(ltree) if f.name == "get_instance"), None)
    if get_fn is None:
        errors.append("instance_lifecycle.get_instance not found")
    else:
        seq = [
            ("mem-short-circuit", n.lineno)
            for n in ast.walk(get_fn)
            if isinstance(n, ast.If) and "in self._manager.instances" in ast.unparse(n.test)
        ] + [
            ("preload", n.lineno)
            for n in ast.walk(get_fn)
            if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
            and _call_name(n.value) == "ensure_mcp_preloaded"
        ]
        seq.sort(key=lambda x: x[1])
        names = [s[0] for s in seq]
        if not ("mem-short-circuit" in names and "preload" in names
                and names.index("mem-short-circuit") < names.index("preload")):
            errors.append(
                f"get_instance ordering drifted: expected in-memory short-circuit "
                f"before cold-restore preload, got {names}"
            )
    return errors


def t4_async_capability_pin() -> list[str]:
    errors: list[str] = []
    itree = _parse("daemon/tools/instance.py")
    tool_fn = next(
        (f for f in _func_nodes(itree)
         if f.name == "spawn_instance" and isinstance(f, ast.AsyncFunctionDef)),
        None,
    )
    if tool_fn is None:
        errors.append("agent-tool spawn_instance is no longer `async def` — fix shape (a) verdict changes")
    else:
        pre_awaits = [name for name in _await_calls(tool_fn)]
        if not pre_awaits:
            errors.append("spawn_instance tool body contains no awaits — async-capability claim lost")
    mtree = _parse("daemon/manager.py")
    defs = {f.name: type(f).__name__ for f in _func_nodes(mtree)
            if f.name in {"spawn_instance", "ensure_mcp_preloaded", "spawn_instance_with_mcp"}}
    if defs.get("spawn_instance") != "FunctionDef":
        errors.append(f"manager.spawn_instance no longer sync def: {defs}")
    for seam in ("ensure_mcp_preloaded", "spawn_instance_with_mcp"):
        if defs.get(seam) != "AsyncFunctionDef":
            errors.append(f"manager.{seam} no longer async def: {defs}")
    return errors


TESTS = [
    ("T1 broken-lane preload contract (THE DISCRIMINATOR — fails@base)", t1_broken_lanes_preload),
    ("T2 healthy-lane pin (no-regression guard)", t2_healthy_lanes_pin),
    ("T3 cache-mechanics pin (silence + permanence + blocked backfill)", t3_cache_mechanics_pin),
    ("T4 async-capability pin (fix-shape verdict)", t4_async_capability_pin),
]


def main() -> int:
    print(f"probe root: {ROOT}")
    failed = False
    for name, fn in TESTS:
        try:
            errors = fn()
        except Exception as exc:  # noqa: BLE001
            errors = [f"probe crashed: {type(exc).__name__}: {exc}"]
        if errors:
            failed = True
            print(f"FAIL  {name}")
            for e in errors:
                print(f"      - {e}")
        else:
            print(f"PASS  {name}")
    print("RESULT:", "FAIL (defect present — expected on base)" if failed else "PASS (all lanes preload)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
