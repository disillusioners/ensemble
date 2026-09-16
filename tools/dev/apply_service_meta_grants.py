#!/usr/bin/env python3
"""Append 'service' to tools.allow for bash/proc-capable agent metas.

Targeted text-manipulation approach (NOT a JSON round-trip) — preserves
the existing formatting style of each meta.json (single-line vs
multi-line arrays, key ordering, trailing whitespace) and produces a
minimal diff.

User-override (2026-09-16) meta-grant IFF policy:
  An agent gets service IFF its EFFECTIVE toolset can include bash OR
  proc (effective = post allow-expansion, post deny-strip).

For "non-empty" shape with bash OR proc in allow → append "service"
to the allow list (dedupe, minimal diff).
For "default" shape (no allow) → leave unchanged (service arrives
via default-open universe).
For "deny-both" shape (deny contains both bash AND proc) → leave
unchanged.
For non-bash/proc agents → leave unchanged.

Run from repo root:
    uv run python tools/dev/apply_service_meta_grants.py [--dry-run]

The script is idempotent: re-running it on an already-granted agent
detects the existing "service" entry and skips it.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"
SERVICE_CATEGORY = "service"
BASH_CATEGORY = "bash"
PROC_CATEGORY = "proc"


def _find_allow_array(source: str) -> tuple[int, int] | None:
    """Locate the ``"allow"`` array in tools config.

    Returns (start_offset, end_offset) where start is the opening ``[``
    and end is the closing ``]`` of the array. None if not found.

    Skips any array that appears in other roles (defensive — the only
    allow-array in meta.json is the one under ``tools.allow``).
    """
    # Match "allow": [...]  with the array on the same line OR
    # potentially across multiple lines.
    m = re.search(r'"allow"\s*:\s*\[', source)
    if not m:
        return None
    start = m.end() - 1  # the opening `[`
    # Find the matching `]` by scanning with depth tracking (arrays can
    # contain only strings/JSON primitives for our purposes, so a
    # simple bracket scan is sufficient).
    depth = 0
    i = start
    in_string = False
    escape = False
    while i < len(source):
        c = source[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return (start, i + 1)
        i += 1
    return None


def _parse_allow(source: str, start: int, end: int) -> list[str]:
    array_text = source[start:end]
    return json.loads(array_text)


def _format_allow_inline(allow: list[str]) -> str:
    """Render a single-line allow array (the repo's most common style)."""
    return ", ".join(json.dumps(s) for s in allow)


def _format_allow_multi(allow: list[str]) -> str:
    """Render a multi-line allow array (the worker-agent style)."""
    return ",\n".join(json.dumps(s) for s in allow)


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


def _patch_allow(source: str, start: int, end: int, new_allow: list[str]) -> tuple[str, int]:
    """Append a new item to the array. Minimal-diff: keep the array's
    existing layout verbatim.

    Two styles are detected from the existing array layout:

    1. **Inline / split-line** — the closing ``]`` is on the same
       line as the last item (no newline between them). We append
       ``, "service"`` immediately before the closing ``]``.

    2. **Multi-line** — the closing ``]`` is on its own line, with
       whitespace-only content between the last item and ``]``. We
       insert a new comma + newline + properly indented ``"service"``,
       then leave the closing ``]`` on its own line.

    Returns (new_source, delta_len).
    """
    new_item_text = json.dumps(SERVICE_CATEGORY)

    # The chunk between the array start and end. Find the last
    # non-whitespace char before ``]`` and what comes between it and
    # ``]``.
    body = source[start:end]
    # Walk backwards from end-1 (the ``]``) to find the last
    # non-whitespace character.
    trailing_ws = ""
    i = end - 2  # the character just before ``]``
    while i >= start and source[i] in " \t\n":
        trailing_ws = source[i] + trailing_ws
        i -= 1
    last_char_pos = i  # position of last non-whitespace char of body

    if trailing_ws == "":
        # Inline or split-line: append right before `]`.
        insertion = ", " + new_item_text
        new_source = source[:end - 1] + insertion + source[end - 1:]
    else:
        # Multi-line: the last item ends at last_char_pos. Insert
        # `,\n<item_indent><new_item>` right after that position,
        # then the trailing whitespace and `]` follow unchanged.
        # The item indent is the indent of items in the array — sample
        # the indent of the LAST item (its leading whitespace).
        # Walk backwards from last_char_pos to find the start of the
        # last item's line (newline).
        line_start = source.rfind("\n", start, last_char_pos + 1) + 1
        item_leading_ws_match = re.match(r"[ \t]*", source[line_start : last_char_pos + 1])
        item_indent = item_leading_ws_match.group(0) if item_leading_ws_match else ""
        # Trailing whitespace was sampled from the array body — it
        # is the newline + indent before `]`. But we want to insert
        # AFTER the last item but BEFORE the existing trailing ws.
        # So we insert at position last_char_pos + 1.
        insertion = ",\n" + item_indent + new_item_text

        # However: if the existing trailing ws is just whitespace
        # (not a newline), it means the array uses space padding —
        # unusual. Fall back to inline append.
        if "\n" not in trailing_ws:
            insertion = ", " + new_item_text
            new_source = source[:end - 1] + insertion + source[end - 1:]
        else:
            new_source = source[: last_char_pos + 1] + insertion + source[last_char_pos + 1 :]

    return new_source, len(new_source) - len(source)


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    rows: list[dict] = []
    changes: list[tuple[Path, str]] = []  # (path, new_source)

    for entry in sorted(AGENTS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name == "_prompt_system":
            continue
        if entry.name.startswith("zz-"):
            continue
        meta_path = entry / "meta.json"
        if not meta_path.exists():
            continue
        source = meta_path.read_text(encoding="utf-8")
        try:
            meta = json.loads(source)
        except json.JSONDecodeError as exc:
            rows.append({"agent": entry.name, "action": "ERROR", "msg": str(exc)})
            continue

        shape, deny_bash, deny_proc = _classify(meta)
        tools = meta.get("tools") or {}
        allow = tools.get("allow")

        has_bash = (allow is not None) and (BASH_CATEGORY in allow)
        has_proc = (allow is not None) and (PROC_CATEGORY in allow)
        has_service = (allow is not None) and (SERVICE_CATEGORY in allow)

        row = {
            "agent": entry.name,
            "shape": shape,
            "has_bash_proc": has_bash or has_proc,
            "has_service": has_service,
            "action": "skip",
        }

        if shape == "non-empty" and (has_bash or has_proc):
            if has_service:
                row["action"] = "skip (already has 'service')"
            else:
                new_allow = [*allow, SERVICE_CATEGORY]
                span = _find_allow_array(source)
                if span is None:
                    row["action"] = "ERROR: allow array not found"
                else:
                    start, end = span
                    new_source, delta = _patch_allow(source, start, end, new_allow)
                    row["action"] = f"append 'service' (delta +{delta} chars)"
                    changes.append((meta_path, new_source))
        elif shape == "default":
            row["action"] = "skip (default-universe → service auto-grant)"
        elif shape == "deny-both":
            row["action"] = "skip (deny contains both bash AND proc — documented edge)"
        else:
            row["action"] = "skip (no bash/proc in allow)"

        rows.append(row)

    # ── Print summary ──
    headers = ["agent", "shape", "has_bash_proc", "has_service", "action"]
    col_w = {
        h: max(len(h), *(len(str(r.get(h, ""))) for r in rows))
        for h in headers
    }
    fmt = "  ".join(f"{h:<{col_w[h]}}" for h in headers)
    print(fmt)
    print("-" * len(fmt))
    for r in rows:
        cells = [f"{str(r.get(h, '')):<{col_w[h]}}" for h in headers]
        print("  ".join(cells))

    print()
    print(f"agents to be modified: {len(changes)}")
    print("paths:")
    for p, _ in changes:
        print(f"  - {p.relative_to(REPO_ROOT)}")

    if dry_run:
        print()
        print("DRY RUN — no files modified.")
        return 0

    # ── Apply ──
    for meta_path, new_source in changes:
        # Preserve trailing newline if the original had one.
        old_source = meta_path.read_text(encoding="utf-8")
        if old_source.endswith("\n") and not new_source.endswith("\n"):
            new_source += "\n"
        meta_path.write_text(new_source, encoding="utf-8")
    print()
    print(f"OK — {len(changes)} files modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
