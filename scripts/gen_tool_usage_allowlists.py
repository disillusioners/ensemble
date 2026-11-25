#!/usr/bin/env python3
"""Generate the committed per-agent allow-list snapshot (A8 / U7).

Scans ``agents/*/meta.json`` ``tools.allow`` arrays at BUILD/COMMIT time,
sorts agents and their tool lists, and emits JSON carrying a
``generated_from_commit`` provenance field. The digest (``tool_usage_digest``)
reads this committed artifact and NEVER scans the live ``agents/`` tree at
run time.

Recorded command (see phase3-plan.md section 10 A8):

    python3 scripts/gen_tool_usage_allowlists.py --agents-dir agents/ \\
        --out .agents/shared/planning/tool-usage-metrics-p1/allowlists.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _find_repo_root(agents_dir: Path) -> Path:
    cur = agents_dir.resolve().parent
    while cur != cur.parent:
        if (cur / ".git").exists():
            return cur
        cur = cur.parent
    return Path.cwd()


def _derive_commit(repo_root: Path, agents_dir: Path, override: str | None) -> str:
    if override:
        return override.strip()
    try:
        rel = agents_dir.resolve().relative_to(repo_root.resolve())
    except ValueError:
        rel = Path(agents_dir.name)
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_root), "log", "-1", "--format=%H", "--", str(rel)],
            stderr=subprocess.DEVNULL,
        )
        sha = out.decode().strip()
        if sha:
            return sha
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        sha = out.decode().strip()
        if sha:
            return sha
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass
    return "unknown"


def build_payload(agents_dir: Path, commit_override: str | None) -> dict:
    agents: dict[str, list[str]] = {}
    for meta_path in sorted(agents_dir.glob("*/meta.json")):
        try:
            with meta_path.open("r", encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: failed to read {meta_path}: {exc}", file=sys.stderr)
            raise ValueError(f"failed to read {meta_path}: {exc}") from exc
        tools = meta.get("tools")
        allow: list[str] = []
        if isinstance(tools, dict):
            raw_allow = tools.get("allow", [])
            if isinstance(raw_allow, list):
                allow = [str(t) for t in raw_allow]
        elif isinstance(tools, list):
            allow = [str(t) for t in tools]
        agent_name = meta_path.parent.name
        agents[agent_name] = sorted(set(allow))

    agents = dict(sorted(agents.items()))
    repo_root = _find_repo_root(agents_dir)
    commit = _derive_commit(repo_root, agents_dir, commit_override)
    return {"agents": agents, "generated_from_commit": commit}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-dir", default="agents/", help="agents/ directory")
    parser.add_argument("--out", required=True, help="output JSON path")
    parser.add_argument(
        "--commit",
        default=None,
        help="override generated_from_commit (default: derived from git)",
    )
    args = parser.parse_args(argv)

    agents_dir = Path(args.agents_dir)
    if not agents_dir.is_dir():
        print(f"error: agents dir not found: {agents_dir}", file=sys.stderr)
        return 2

    payload = build_payload(agents_dir, args.commit)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {out_path} ({len(payload['agents'])} agents)")
    print(f"generated_from_commit={payload['generated_from_commit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
