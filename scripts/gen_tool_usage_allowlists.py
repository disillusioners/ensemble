#!/usr/bin/env python3
"""Generate the committed per-agent allow-list snapshot (schema 2).

Scans ``agents/*/meta.json`` ``tools.allow`` / ``tools.deny`` / ``innate_skills``
at BUILD/COMMIT time, derives the taxonomy via the house walker, and emits
schema-2 JSON. The digest (``tool_usage_digest``) reads this committed artifact
and NEVER scans the live ``agents/`` tree at run time.

Recorded command:

    python3 scripts/gen_tool_usage_allowlists.py --agents-dir agents/ \\
        --out .agents/shared/planning/tool-usage-metrics-p1/allowlists.json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT_MARKER = ".git"


def _find_repo_root(agents_dir: Path) -> Path:
    cur = agents_dir.resolve().parent
    while cur != cur.parent:
        if (cur / ".git").exists():
            return cur
        cur = cur.parent
    return Path.cwd()


def _derive_commit(repo_root: Path, paths: list[str], override: str | None) -> str:
    if override:
        return override.strip()
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_root), "log", "-1", "--format=%H", "--", *paths],
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


def _load_registry(repo_root: Path):
    reg_path = repo_root / "daemon" / "tools" / "_tool_registry.py"
    spec = importlib.util.spec_from_file_location("_tool_registry_gen", reg_path)
    mod = importlib.util.module_from_spec(spec)
    # avoid executing package __init__ side effects by loading module directly
    spec.loader.exec_module(mod)
    return mod


def _content_sha256_agents(agents_dir: Path) -> str:
    entries = []
    for meta_path in sorted(agents_dir.glob("*/meta.json")):
        h = hashlib.sha256()
        h.update(meta_path.read_bytes())
        rel = meta_path.relative_to(agents_dir.parent) if False else meta_path.name
        # build path\0sha256
        entries.append(f"{meta_path.relative_to(agents_dir.parent)}\0{h.hexdigest()}")
    # sort
    entries.sort()
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def build_payload(agents_dir: Path, tree_commit_override: str | None, agents_commit_override: str | None, taxonomy_commit_override: str | None) -> dict:
    agents_dir = agents_dir.resolve()
    repo_root = _find_repo_root(agents_dir)

    # Load registry by file path (no daemon package import)
    reg = _load_registry(repo_root)
    CATEGORY_MODULES = reg.CATEGORY_MODULES
    PRIVILEGED = sorted(reg.PRIVILEGED_TOOL_CATEGORIES)
    KNOWN = reg.KNOWN_TOOL_NAMES
    DYNAMIC = reg.DYNAMIC_TOOL_NAMES
    DYNAMIC_PREFIXES = sorted(reg.DYNAMIC_TOOL_PREFIXES)

    scan = reg.scan_category_module_sources_by_category()

    # categories: registry key -> sorted tool names
    categories = {}
    for cat, members in scan.items():
        categories[cat] = sorted(members.keys())
    # ensure all CATEGORY_MODULES keys present (empty -> runtime-only)
    for key in CATEGORY_MODULES.keys():
        categories.setdefault(key, [])
    # remove keys with empty? Keep them; runtime_only derived below.
    runtime_only = sorted([k for k in CATEGORY_MODULES.keys() if not categories.get(k)])
    # categories should only include non-empty? Spec: categories map registry key -> tool names.
    # Keep empty keys? The artifact example has categories with attributed keys. runtime_only separate.
    # Remove empty keys from categories to keep it clean? Spec says categories is map registry-key -> array.
    # But reader uses categories.keys() as category universe union runtime_only. Empty keys in categories harmless.
    # To match "63 attributed keys", keep only non-empty in categories.
    categories = {k: v for k, v in categories.items() if v}
    # sort
    categories = {k: sorted(v) for k, v in sorted(categories.items())}

    # dynamic_prefixes as list
    dynamic_prefixes = sorted(DYNAMIC_PREFIXES)

    # innate_skill_categories via AST literal extraction from instance.py
    instance_path = repo_root / "daemon" / "tools" / "instance.py"
    innate_map = _extract_innate(instance_path)

    # known_tool_names
    known_tool_names = sorted(set(KNOWN) | set(DYNAMIC))

    # agents
    agents_out: dict[str, dict] = {}
    unresolved_allow: list = []
    unresolved_deny: list = []
    vocab = set(KNOWN) | set(DYNAMIC)

    def is_category_or_tool_or_prefix(entry: str) -> bool:
        if entry in CATEGORY_MODULES:
            return True
        if entry in vocab:
            return True
        for p in DYNAMIC_PREFIXES:
            if entry.startswith(p):
                return True
        return False

    for meta_path in sorted(agents_dir.glob("*/meta.json")):
        try:
            with meta_path.open("r", encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"error: failed to read {meta_path}: {exc}", file=sys.stderr)
            raise ValueError(f"failed to read {meta_path}: {exc}") from exc
        tools = meta.get("tools")
        allow: list[str] = []
        deny: list[str] = []
        if isinstance(tools, dict):
            raw_allow = tools.get("allow", [])
            if isinstance(raw_allow, list):
                allow = [str(t) for t in raw_allow]
            raw_deny = tools.get("deny", [])
            if isinstance(raw_deny, list):
                deny = [str(t) for t in raw_deny]
        elif isinstance(tools, list):
            allow = [str(t) for t in tools]
        innate = meta.get("innate_skills", [])
        if not isinstance(innate, list):
            innate = []
        innate = [str(s) for s in innate]

        # unresolved ledger
        for e in sorted(set(allow)):
            if not is_category_or_tool_or_prefix(e):
                unresolved_allow.append({"agent": meta_path.parent.name, "entry": e})
        for e in sorted(set(deny)):
            if not is_category_or_tool_or_prefix(e):
                unresolved_deny.append({"agent": meta_path.parent.name, "entry": e})

        agents_out[meta_path.parent.name] = {
            "allow": sorted(set(allow)),
            "deny": sorted(set(deny)),
            "innate": sorted(set(innate)),
        }

    agents_out = dict(sorted(agents_out.items()))

    # unresolved as list of objects (spec example shows objects for deny)
    # spec: unresolved = {"allow": [...], "deny": [...]}.
    # Measured: allow 0, deny 1 (maintenancer/git_commit). Keep as list of objects.
    # But spec's "compared as a SET" and test may expect list of strings? 
    # Spec example: "deny": [{"agent": "maintenancer", "entry": "git_commit"}]
    # So objects. Good.

    # Sort unresolved entries for determinism
    unresolved_allow_sorted = sorted(unresolved_allow, key=lambda x: (x["agent"], x["entry"]))
    unresolved_deny_sorted = sorted(unresolved_deny, key=lambda x: (x["agent"], x["entry"]))

    # provenance
    agents_last_commit = _derive_commit(repo_root, ["agents"], agents_commit_override)
    taxonomy_last_commit = _derive_commit(repo_root, ["daemon/tools"], taxonomy_commit_override)

    # content sha256 agents
    agents_content_sha = _content_sha256_agents(agents_dir)

    # taxonomy content sha: canonical JSON of {categories, runtime_only, privileged, dynamic_prefixes, innate_skill_categories}
    taxonomy_obj = {
        "categories": categories,
        "runtime_only_categories": runtime_only,
        "privileged_categories": PRIVILEGED,
        "dynamic_prefixes": dynamic_prefixes,
        "innate_skill_categories": innate_map,
    }
    taxonomy_content_sha = hashlib.sha256(_canonical_json(taxonomy_obj).encode()).hexdigest()

    tree_commit = tree_commit_override if tree_commit_override else _derive_commit(repo_root, [], None)
    # tree_commit: use HEAD
    try:
        out = subprocess.check_output(["git", "-C", str(repo_root), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
        tree_commit = tree_commit_override if tree_commit_override else out.decode().strip()
    except Exception:
        if not tree_commit:
            tree_commit = "unknown"

    payload = {
        "schema_version": 2,
        "agents": agents_out,
        "categories": categories,
        "runtime_only_categories": runtime_only,
        "privileged_categories": PRIVILEGED,
        "dynamic_prefixes": dynamic_prefixes,
        "innate_skill_categories": innate_map,
        "known_tool_names": known_tool_names,
        "unresolved": {
            "allow": unresolved_allow_sorted,
            "deny": unresolved_deny_sorted,
        },
        "provenance": {
            "generator": "scripts/gen_tool_usage_allowlists.py",
            "generator_version": 2,
            "tree_commit": tree_commit,
            "inputs": {
                "agents": {"last_commit": agents_last_commit, "content_sha256": agents_content_sha},
                "taxonomy": {"last_commit": taxonomy_last_commit, "content_sha256": taxonomy_content_sha},
            },
        },
    }
    return payload


def _extract_innate(instance_path: Path) -> dict:
    # AST literal extraction
    import ast
    src = instance_path.read_text()
    tree = ast.parse(src)
    # find INNATE_SKILL_TOOL_CATEGORIES assignment
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "INNATE_SKILL_TOOL_CATEGORIES":
                    if isinstance(node.value, ast.Dict):
                        result = {}
                        for k, v in zip(node.value.keys, node.value.values):
                            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                if isinstance(v, ast.List):
                                    vals = []
                                    for elt in v.elts:
                                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                            vals.append(elt.value)
                                        else:
                                            raise ValueError("non-literal value in INNATE_SKILL_TOOL_CATEGORIES")
                                    result[k.value] = sorted(vals)
                                else:
                                    raise ValueError("non-literal value in INNATE_SKILL_TOOL_CATEGORIES")
                        # sort keys
                        return dict(sorted(result.items()))
    raise ValueError("INNATE_SKILL_TOOL_CATEGORIES not found")


def _verify(artifact_path: Path, payload: dict) -> int:
    # read existing
    try:
        existing = json.loads(artifact_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"verify: cannot read artifact: {exc}", file=sys.stderr)
        return 1
    # Part 1: semantic payload keys byte equality (canonical)
    payload_keys = ("agents", "categories", "runtime_only_categories", "privileged_categories", "dynamic_prefixes", "innate_skill_categories", "known_tool_names")
    for key in payload_keys:
        if json.dumps(existing.get(key), sort_keys=True, separators=(",", ":")) != json.dumps(payload.get(key), sort_keys=True, separators=(",", ":")):
            print(f"verify: drift in payload key {key}", file=sys.stderr)
            return 1
    # schema_version scalar gate
    if existing.get("schema_version") != 2:
        print("verify: schema_version != 2", file=sys.stderr)
        return 1
    if payload.get("schema_version") != 2:
        print("verify: regenerated schema_version != 2", file=sys.stderr)
        return 1
    # unresolved as set
    def _norm_unres(u):
        allow = u.get("allow", []) if isinstance(u, dict) else []
        deny = u.get("deny", []) if isinstance(u, dict) else []
        # convert to set of canonical tuples
        a_set = set()
        for e in allow:
            if isinstance(e, dict):
                a_set.add(("allow", e.get("agent"), e.get("entry")))
            else:
                a_set.add(("allow", None, str(e)))
        d_set = set()
        for e in deny:
            if isinstance(e, dict):
                d_set.add(("deny", e.get("agent"), e.get("entry")))
            else:
                d_set.add(("deny", None, str(e)))
        return a_set, d_set
    ea, ed = _norm_unres(existing.get("unresolved", {}))
    pa, pd = _norm_unres(payload.get("unresolved", {}))
    if ea != pa or ed != pd:
        print("verify: unresolved ledger drift", file=sys.stderr)
        return 1
    # Part 2: provenance
    ep = existing.get("provenance", {})
    pp = payload.get("provenance", {})
    # generator, generator_version compared
    if ep.get("generator") != pp.get("generator"):
        print("verify: generator drift", file=sys.stderr)
        return 1
    if ep.get("generator_version") != pp.get("generator_version"):
        print("verify: generator_version drift", file=sys.stderr)
        return 1
    # content hashes always
    ei = ep.get("inputs", {})
    pi = pp.get("inputs", {})
    if ei.get("agents", {}).get("content_sha256") != pi.get("agents", {}).get("content_sha256"):
        print("verify: agents content hash drift", file=sys.stderr)
        return 1
    if ei.get("taxonomy", {}).get("content_sha256") != pi.get("taxonomy", {}).get("content_sha256"):
        print("verify: taxonomy content hash drift", file=sys.stderr)
        return 1
    # last_commit compared only where corresponding content hash moved
    # agents
    if ei.get("agents", {}).get("content_sha256") != pi.get("agents", {}).get("content_sha256"):
        # already handled above (would have returned)
        pass
    # if content hash moved, compare last_commit
    if ei.get("agents", {}).get("content_sha256") == pi.get("agents", {}).get("content_sha256"):
        # content did not move; last_commit need not match
        pass
    else:
        if ei.get("agents", {}).get("last_commit") != pi.get("agents", {}).get("last_commit"):
            print("verify: agents last_commit drift", file=sys.stderr)
            return 1
    if ei.get("taxonomy", {}).get("content_sha256") == pi.get("taxonomy", {}).get("content_sha256"):
        pass
    else:
        if ei.get("taxonomy", {}).get("last_commit") != pi.get("taxonomy", {}).get("last_commit"):
            print("verify: taxonomy last_commit drift", file=sys.stderr)
            return 1
    # tree_commit excluded
    print("verify: OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-dir", default="agents/", help="agents/ directory")
    parser.add_argument("--out", help="output JSON path")
    parser.add_argument("--verify", action="store_true", help="verify mode")
    parser.add_argument("--tree-commit", default=None, help="override provenance.tree_commit")
    parser.add_argument("--commit", default=None, help="accepted alias/override for tree_commit")
    args = parser.parse_args(argv)

    agents_dir = Path(args.agents_dir)
    if not agents_dir.is_dir():
        print(f"error: agents dir not found: {agents_dir}", file=sys.stderr)
        return 2

    tree_override = args.tree_commit or args.commit
    payload = build_payload(agents_dir, tree_override, None, None)

    if args.verify:
        if not args.out:
            print("error: --verify requires --out", file=sys.stderr)
            return 2
        out_path = Path(args.out)
        return _verify(out_path, payload)

    if not args.out:
        print("error: --out is required", file=sys.stderr)
        return 2
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {out_path} ({len(payload['agents'])} agents)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

