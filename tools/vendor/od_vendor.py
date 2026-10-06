#!/usr/bin/env python3
"""OD data vendoring — byte-perfect, hash-verified (slice ② OQ3).

Vendors the four copy-freely data classes from the OpenDesign upstream
git repo at a pinned tag into ``plugins/<plugin>/copy_freely/``:

  - design-systems/    (154 dirs)
  - design-templates/  (115 dirs)
  - craft/             (13 top-level .md files)
  - prompt-templates/  (106 .json files = 48 image + 58 video)

Byte-fidelity model.  Files are copied blob-by-blob through ``git cat-file``
so the vendored bytes are EQUAL to the upstream-tag bytes (no CRLF
translation, no re-encoding, no symlink resolution surprises).  After
copy, a sha256 is computed per file and the list is written to a
``sha256sum``-format file (default: ``plugins/<plugin>/copy_freely.HASHES.sha256``,
adjacent to — not inside — the copy_freely/ class subtree).

The hash file is the OFFLINE round-trip fixture for tests: re-hashing
the vendored tree and comparing to the recorded list proves the tree
is intact without contacting the upstream git.

**Sentinels (CON §7).**  No plugin code is imported; the script is
plain I/O + ``git`` CLI (or python ``dulwich`` fallback).  It writes
ONLY to the destination directory + the hash manifest.  No imports
from ``daemon.plugin_subsystem`` here — this is a build-time tool, not
a runtime path.

Usage (slice ②, from the worktree root):

    python tools/vendor/od_vendor.py \\
        --source /home/nea/opt/open-design \\
        --tag open-design-v0.23.0 \\
        --dest plugins/opendesign/copy_freely \\
        --hashes-out plugins/opendesign/copy_freely.HASHES.sha256 \\
        --json-summary /tmp/od-vendor-summary.json

    # or via env vars (defaults match the slice ② dispatch):
    OD_VENDOR_SOURCE=/home/nea/opt/open-design \\
    OD_VENDOR_TAG=open-design-v0.23.0 \\
    python tools/vendor/od_vendor.py

Verify the vendored tree (offline audit — exits 0 on clean tree,
exits 1 + `4881 listed files could not be read` on the obvious
mistake of running from `plugins/opendesign/` instead of
`plugins/opendesign/copy_freely/`):

    cd plugins/opendesign/copy_freely \\
        && sha256sum -c ../copy_freely.HASHES.sha256 --quiet \\
        && echo "OK: $(wc -l < ../copy_freely.HASHES.sha256) files verified"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


# The four copy-freely subdirs (CON §2; REC §4.1 row 1).
COPY_FREELY_CLASS_SUBDIRS: Tuple[str, ...] = (
    "design-systems",
    "design-templates",
    "craft",
    "prompt-templates",
)


@dataclass(frozen=True)
class VendoredFile:
    """One file's vendoring record (sha + size + upstream path)."""

    relpath: str  # path under <dest> (e.g. "design-systems/airbnb/manifest.json")
    upstream_relpath: str  # path under upstream repo root
    sha256: str
    size_bytes: int


@dataclass
class VendorSummary:
    """Aggregate result; JSON-serializable via :meth:`as_dict`."""

    source: str
    tag: str
    dest: str
    hashes_out: str
    files: List[VendoredFile] = field(default_factory=list)
    counts_per_class: Dict[str, int] = field(default_factory=dict)
    skipped_entries: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.files)

    def as_dict(self) -> Dict[str, object]:
        return {
            "source": self.source,
            "tag": self.tag,
            "dest": self.dest,
            "hashes_out": self.hashes_out,
            "total_files": self.total,
            "counts_per_class": dict(self.counts_per_class),
            "skipped_entries": [
                {"path": p, "reason": r} for p, r in self.skipped_entries
            ],
            "files": [
                {
                    "relpath": f.relpath,
                    "upstream_relpath": f.upstream_relpath,
                    "sha256": f.sha256,
                    "size_bytes": f.size_bytes,
                }
                for f in self.files
            ],
        }


# ─── git plumbing ────────────────────────────────────────────────────────────


def _run_git(source: Path, *args: str) -> str:
    """Run ``git -C <source> <args>`` and return stdout (decoded)."""
    cmd = ["git", "-C", str(source)] + list(args)
    result = subprocess.run(cmd, capture_output=True, check=True)
    return result.stdout.decode("utf-8", errors="replace")


def _resolve_tag_sha(source: Path, tag: str) -> str:
    return _run_git(source, "rev-parse", f"{tag}^{{}}").strip()


def _list_tree_paths(source: Path, ref: str, subdir: str) -> List[Tuple[str, str, str, int]]:
    """List every file under ``<ref>:<subdir>/`` as (mode, type, blob_sha, relpath).

    The output excludes subdirectories (only blobs are returned, so the
    caller can iterate files without recursing).  We use ``git ls-tree -r``
    for completeness — non-recursive listing would miss nested files.
    """
    raw = _run_git(source, "ls-tree", "-r", ref, "--", f"{subdir}/")
    out: List[Tuple[str, str, str, int]] = []
    for line in raw.splitlines():
        # Format: "<mode> <type> <object>\t<path>"
        if not line.strip():
            continue
        try:
            head, path = line.split("\t", 1)
        except ValueError:
            continue
        parts = head.split()
        if len(parts) != 3:
            continue
        mode, obj_type, sha = parts
        if obj_type != "blob":
            continue  # skip subtrees, symlinks (CON §1: no symlinks)
        try:
            size = int(_run_git(source, "cat-file", "-s", sha).strip())
        except (subprocess.CalledProcessError, ValueError):
            size = 0
        out.append((mode, obj_type, sha, path))
    return out


def _cat_blob(source: Path, blob_sha: str) -> bytes:
    """Read a blob's bytes verbatim — no encoding transform."""
    result = subprocess.run(
        ["git", "-C", str(source), "cat-file", "blob", blob_sha],
        capture_output=True,
        check=True,
    )
    return result.stdout


# ─── core vendoring ──────────────────────────────────────────────────────────


def _safe_join_under(base: Path, relpath: str) -> Path:
    """Resolve ``relpath`` under ``base`` and refuse any traversal escape."""
    target = (base / relpath).resolve()
    base_resolved = base.resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"path {relpath!r} escapes dest {base_resolved}") from exc
    return target


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _count_per_class(files: Sequence[VendoredFile]) -> Dict[str, int]:
    counts: Dict[str, int] = {cls: 0 for cls in COPY_FREELY_CLASS_SUBDIRS}
    for f in files:
        for cls in COPY_FREELY_CLASS_SUBDIRS:
            if f.upstream_relpath.startswith(cls + "/"):
                counts[cls] += 1
                break
    return counts


def vendor(
    *,
    source: Path,
    tag: str,
    dest: Path,
    hashes_out: Path,
    progress: bool = True,
) -> VendorSummary:
    """Vendor the four copy-freely classes from ``source`` @ ``tag`` into ``dest``.

    Returns a :class:`VendorSummary` whose :meth:`as_dict` is JSON-serializable.
    The caller decides whether to write the hash file (this function does
    it as a side-effect for convenience; tests can pass a tmp path).
    """
    source = Path(source)
    dest = Path(dest)
    hashes_out = Path(hashes_out)

    if not source.is_dir():
        raise SystemExit(f"ERROR: source is not a directory: {source}")
    if not (source / ".git").exists():
        raise SystemExit(f"ERROR: source is not a git checkout: {source}")

    # 1) Resolve tag to a SHA; refuse ambiguous / missing refs.
    try:
        ref_sha = _resolve_tag_sha(source, tag)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"ERROR: cannot resolve tag {tag!r} in {source}: {exc}")

    # 2) Prepare dest tree.
    dest.mkdir(parents=True, exist_ok=True)

    summary = VendorSummary(
        source=str(source),
        tag=tag,
        dest=str(dest),
        hashes_out=str(hashes_out),
    )

    # 3) Iterate per class — each gets its own subtree.
    for cls in COPY_FREELY_CLASS_SUBDIRS:
        try:
            entries = _list_tree_paths(source, ref_sha, cls)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"ERROR: cannot list {cls} at {tag}: {exc}")
        for mode, _obj_type, blob_sha, upstream_path in entries:
            # Class-entry filter: prompt-templates/ is the JSON-only class
            # (od-resource-layer §2: 106 JSON). Any non-JSON asset in that
            # subtree (e.g. a stray preview PNG) is NOT a class-entry and
            # is excluded by construction. The exclusion is recorded as a
            # skipped entry so CURATION.md can document the deliberate
            # deviation from raw-upstream-byte-perfect.
            if cls == "prompt-templates" and not upstream_path.endswith(".json"):
                summary.skipped_entries.append(
                    (
                        upstream_path,
                        "non-JSON asset in prompt-templates/ excluded (106 = JSON-only class)",
                    )
                )
                continue
            # Read blob bytes.
            try:
                data = _cat_blob(source, blob_sha)
            except subprocess.CalledProcessError as exc:
                summary.skipped_entries.append((upstream_path, f"cat-file failed: {exc}"))
                continue
            sha = _hash_bytes(data)
            # relpath under dest = full upstream path
            relpath = upstream_path
            # Refuse any path that tries to escape the dest via "..".
            try:
                target = _safe_join_under(dest, relpath)
            except ValueError as exc:
                summary.skipped_entries.append((upstream_path, str(exc)))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            # Byte-perfect write: open in binary mode, no text encoding.
            with open(target, "wb") as fh:
                fh.write(data)
            summary.files.append(
                VendoredFile(
                    relpath=relpath,
                    upstream_relpath=upstream_path,
                    sha256=sha,
                    size_bytes=len(data),
                )
            )
        if progress:
            n = sum(1 for f in summary.files if f.upstream_relpath.startswith(cls + "/"))
            print(f"  {cls}: {n} file(s)", file=sys.stderr)

    summary.counts_per_class = _count_per_class(summary.files)

    # 4) Write the hash manifest (sha256sum format, sorted for stability).
    hashes_out.parent.mkdir(parents=True, exist_ok=True)
    sorted_files = sorted(summary.files, key=lambda f: f.relpath)
    with open(hashes_out, "w", encoding="utf-8") as fh:
        for f in sorted_files:
            fh.write(f"{f.sha256}  {f.relpath}\n")

    return summary


# ─── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="od_vendor",
        description="OD data vendoring (slice ② OQ3; copy-freely, byte-perfect, hash-verified)",
    )
    parser.add_argument(
        "--source",
        default=os.environ.get("OD_VENDOR_SOURCE", "/home/nea/opt/open-design"),
        help="path to the local OD git checkout (default: $OD_VENDOR_SOURCE)",
    )
    parser.add_argument(
        "--tag",
        default=os.environ.get("OD_VENDOR_TAG", "open-design-v0.23.0"),
        help="git tag to vendor (default: $OD_VENDOR_TAG = open-design-v0.23.0)",
    )
    parser.add_argument(
        "--dest",
        default=os.environ.get("OD_VENDOR_DEST", "plugins/opendesign/copy_freely"),
        help="destination dir for the vendored tree (default: $OD_VENDOR_DEST)",
    )
    parser.add_argument(
        "--hashes-out",
        default=os.environ.get(
            "OD_VENDOR_HASHES",
            "plugins/opendesign/copy_freely.HASHES.sha256",
        ),
        help="hash manifest path (sha256sum format; default: $OD_VENDOR_HASHES)",
    )
    parser.add_argument(
        "--json-summary",
        default=None,
        help="optional path to also write a JSON summary of the vendoring run",
    )
    args = parser.parse_args(argv)

    summary = vendor(
        source=Path(args.source),
        tag=args.tag,
        dest=Path(args.dest),
        hashes_out=Path(args.hashes_out),
    )
    print(
        f"Vendored {summary.total} file(s) from {args.tag} -> {args.dest}",
        file=sys.stderr,
    )
    print(f"Per-class counts: {summary.counts_per_class}", file=sys.stderr)
    if summary.skipped_entries:
        print(
            f"WARNING: {len(summary.skipped_entries)} skipped entry/entries:",
            file=sys.stderr,
        )
        for p, r in summary.skipped_entries:
            print(f"  - {p}: {r}", file=sys.stderr)
    if args.json_summary:
        Path(args.json_summary).write_text(
            json.dumps(summary.as_dict(), indent=2), encoding="utf-8"
        )
        print(f"JSON summary: {args.json_summary}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
