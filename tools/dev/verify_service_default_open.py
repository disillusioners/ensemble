#!/usr/bin/env python3
"""Verify the service-tool default-open override for every agent meta.

Computes each agent's effective service-tool reach via the REAL filter
code (``create_instance_tools`` → ``resolve_tool_filter``) in two
separate subprocesses (one per worktree, so SQLModel metadata and
``daemon.*`` module cache don't collide):

* BEFORE — ``282c20b3`` worktree (default-deny, ``service`` is in
  ``PRIVILEGED_TOOL_CATEGORIES``, NO metas carry ``service`` in allow).
* AFTER  — current working tree (default-open; metas for bash/proc-
  capable agents include ``service`` in allow).

Invariants (asserted; harness exits non-zero on any deviation):

    delta := service-after − service-before
    == 5  iff agent has bash OR proc in its effective allow
            (post expansion, post deny-strip)
    == 0  otherwise (default-universe + deny-both edge flagged, not failed)

Run from repo root:

    uv run python tools/dev/verify_service_default_open.py

The BEFORE worktree is auto-created on first run.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKTREE = Path("/tmp/svc-before")

SERVICE_TOOL_NAMES = (
    "service_start",
    "service_stop",
    "service_status",
    "service_list",
    "service_logs",
)


_WORKER = """
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock


def main() -> None:
    repo_root = Path({repo_root_json})
    agent_names = json.loads({agent_names_json})
    metas = json.loads({metas_json})
    out_path = Path({out_path_json})

    # Per-pass imports — must run AFTER sys.path insertion.
    import daemon.registry as dr
    from daemon.registry import AgentRegistry
    from daemon.tools.instance import create_instance_tools

    SERVICE = set(json.loads({service_names_json}))

    results: dict[str, dict] = {{}}

    with tempfile.TemporaryDirectory() as td:
        for name in agent_names:
            meta = metas.get(name)
            if meta is None:
                continue
            tmp_path = Path(td)
            agents_dir = tmp_path / "agents"
            agent_dir = agents_dir / name
            agent_dir.mkdir(parents=True, exist_ok=True)
            (agent_dir / "meta.json").write_text(
                json.dumps(meta), encoding="utf-8"
            )
            registry = AgentRegistry(agents_dir)
            registry.discover()
            dr._registry = registry

            manager = MagicMock(name="InstanceManager")
            manager.config.daemon.port = 0
            tools = create_instance_tools(manager, f"inst-{{name}}", name)
            by_name = {{getattr(t, "name", "?"): t for t in tools}}

            service_names = SERVICE & set(by_name)
            categories = {{
                getattr(t, "_tool_category", None)
                for t in by_name.values()
            }}
            had_bash = "bash" in categories
            had_proc = "proc" in categories

            results[name] = {{
                "service_count": len(service_names),
                "service_names": sorted(service_names),
                "had_bash_proc": had_bash or had_proc,
                "tool_count": len(by_name),
            }}

    out_path.write_text(json.dumps(results), encoding="utf-8")


if __name__ == "__main__":
    main()
"""


def _ensure_worktree() -> Path:
    if WORKTREE.exists() and (WORKTREE / "daemon").exists():
        return WORKTREE
    print(
        f"[harness] creating BEFORE worktree at {WORKTREE} @ 282c20b3 ...",
        file=sys.stderr,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "worktree",
            "add",
            "--detach",
            str(WORKTREE),
            "282c20b3",
        ],
        check=True,
    )
    return WORKTREE


def _collect_agents() -> list[str]:
    out: list[str] = []
    for entry in sorted((REPO_ROOT / "agents").iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in {"_prompt_system"}:
            continue
        if not (entry / "meta.json").exists():
            continue
        out.append(entry.name)
    return out


def _load_metas(root: Path) -> dict[str, dict]:
    metas: dict[str, dict] = {}
    for entry in sorted((root / "agents").iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in {"_prompt_system"}:
            continue
        if not (entry / "meta.json").exists():
            continue
        metas[entry.name] = json.loads(
            (entry / "meta.json").read_text(encoding="utf-8")
        )
    return metas


def _run_pass(
    *,
    root: Path,
    agent_names: list[str],
    metas: dict[str, dict],
    out_path: Path,
) -> dict[str, dict]:
    """Spawn a subprocess that imports ``daemon`` from ``root`` and
    computes the harness results; return the parsed JSON."""
    script = _WORKER.format(
        repo_root_json=json.dumps(str(root)),
        agent_names_json=json.dumps(json.dumps(agent_names)),
        metas_json=json.dumps(json.dumps(metas)),
        out_path_json=json.dumps(str(out_path)),
        service_names_json=json.dumps(json.dumps(sorted(SERVICE_TOOL_NAMES))),
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(
            f"[harness] subprocess FAILED (cwd={root}, rc={proc.returncode})\n"
        )
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(2)
    return json.loads(out_path.read_text(encoding="utf-8"))


def _classify(meta: dict) -> tuple[str, bool, bool]:
    tools = meta.get("tools") or {}
    allow = tools.get("allow")
    deny = tools.get("deny") or []
    deny_bash = "bash" in deny
    deny_proc = "proc" in deny
    is_default_universe = allow is None or len(allow) == 0
    if is_default_universe:
        return ("default", deny_bash, deny_proc)
    if deny_bash and deny_proc:
        return ("deny-both", deny_bash, deny_proc)
    return ("non-empty", deny_bash, deny_proc)


def main() -> int:
    before_root = _ensure_worktree()
    agent_names = _collect_agents()
    after_metas = _load_metas(REPO_ROOT)
    before_metas = _load_metas(before_root)

    out_before = REPO_ROOT / "data" / ".harness_before.json"
    out_after = REPO_ROOT / "data" / ".harness_after.json"
    out_before.parent.mkdir(parents=True, exist_ok=True)

    print(f"[harness] running BEFORE pass @ {before_root} ...")
    before = _run_pass(
        root=before_root,
        agent_names=agent_names,
        metas=before_metas,
        out_path=out_before,
    )

    print(f"[harness] running AFTER pass @ {REPO_ROOT} ...")
    after = _run_pass(
        root=REPO_ROOT,
        agent_names=agent_names,
        metas=after_metas,
        out_path=out_after,
    )

    # ── Aggregate + assert ──
    rows: list[dict] = []
    failures: list[str] = []
    edges: list[dict] = []

    for name in agent_names:
        before_row = before.get(name, {})
        after_row = after.get(name, {})
        meta_after = after_metas.get(name) or {}
        shape, deny_bash, deny_proc = _classify(meta_after)

        before_n = before_row.get("service_count", 0)
        after_n = after_row.get("service_count", 0)
        had_bash_proc = after_row.get("had_bash_proc", False)
        delta = after_n - before_n

        row = {
            "agent": name,
            "shape": shape,
            "had_bash_proc": had_bash_proc,
            "before_n": before_n,
            "after_n": after_n,
            "delta": delta,
        }

        # Edge: default-universe + deny-both still gets 5 via default-open.
        allow = (meta_after.get("tools") or {}).get("allow")
        is_default_universe = allow is None or len(allow) == 0
        if is_default_universe and deny_bash and deny_proc and delta == 5:
            row["edge"] = (
                "default-universe + deny-both → 5 via default-open (documented)"
            )
            edges.append(row)

        rows.append(row)

        expected_delta = 5 if had_bash_proc else 0
        if delta != expected_delta and "edge" not in row:
            failures.append(
                f"{name}: delta={delta} expected={expected_delta} "
                f"(shape={shape}, had_bash_proc={had_bash_proc})"
            )

    # ── Print ──
    headers = ["agent", "shape", "had_bash_proc", "before_n", "after_n", "delta"]
    col_w = {
        h: max(len(h), *(len(str(r.get(h, ""))) for r in rows)) for h in headers
    }
    fmt = "  ".join(f"{h:<{col_w[h]}}" for h in headers)
    print()
    print(fmt)
    print("-" * len(fmt))
    for r in rows:
        cells = [f"{str(r.get(h, '')):<{col_w[h]}}" for h in headers]
        print("  ".join(cells))

    n_with = sum(1 for r in rows if (r.get("after_n") or 0) >= 5)
    n_without = sum(1 for r in rows if (r.get("after_n") or 0) == 0)
    print()
    print(f"agents receiving service under default-open: {n_with}")
    print(f"agents NOT receiving service:               {n_without}")

    if edges:
        print()
        print(f"flagged edges ({len(edges)}):")
        for r in edges:
            print(f"  - {r['agent']}: {r['edge']}")

    if failures:
        print()
        print(f"INVARIANT FAILURES ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print()
    print("OK — all agents match the meta-grant IFF invariant.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
