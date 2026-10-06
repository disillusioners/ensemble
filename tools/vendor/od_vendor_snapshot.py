"""Snapshot-class vendoring (slice ③ REC §4.1 layer→class row 2).

Vendors the prompt-code layer (10 daemon + 17 contracts TS files)
from the OpenDesign upstream at the pinned tag into
``plugins/<plugin>/snapshot_with_drift_alarm/``.

**Byte-fidelity model.** Mirrors the ``copy_freely`` vendoring tool:
``git cat-file`` per blob, no encoding transform, sha256 per file
written to ``snapshot_with_drift_alarm/HASHES.sha256``. The byte
identity between this run's hash and any future sync-runner pull
is what the slice ③ dry-run / tag-diff exercises verify.

**Snapshot-class discipline (CON §2):**

- The vendored bytes are SNAPSHOTTED — they are NOT executed.
  The Mode-P (Python-native compose) Provider at slice ⑤ re-expresses
  these modules in Python; the vendored TS bytes are the reference
  for the divergence register, not the runtime. (CON §7
  no-runtime-loading; REC §1.4 forbidden edges.)
- The divergence_register starts non-empty: the planning docs
  (od-resource-layer §1.B) identify real divergences between
  the daemon tree and the contracts mirror (core-slim asymmetry,
  two-tree drift on system.ts / media-contract.ts / directions.ts
  / discovery.ts). Each entry names the divergent file, the
  observed delta, and the rationale (CON §2: "re-apply or drop,
  update the log either way").

**Why pre-vendored for slice ③ (not at slice ⑤):**

The slice ③ dry-run (REC §8.5) tag-diffs the snapshot class from
v0.23.0 → v0.24.1 and reports a real ``diff_summary``. For that
to be possible, the local v0.23.0 snapshot must exist; the
sync-runner's "clean_pull" semantic requires the destination to
already be at the source-of-truth pin. The slice ⑤ Mode-P work
will consume the vendored bytes as the reference; slice ③ just
needs them on disk so the tag-diff has a starting point.

Usage (from the worktree root):

    python tools/vendor/od_vendor_snapshot.py \\
        --source /home/nea/opt/open-design \\
        --tag open-design-v0.23.0 \\
        --dest plugins/opendesign/snapshot_with_drift_alarm \\
        --hashes-out plugins/opendesign/snapshot_with_drift_alarm/HASHES.sha256

    # or via env vars (--source or $OD_VENDOR_SOURCE is REQUIRED):
    OD_VENDOR_SOURCE=/home/nea/opt/open-design \\
    python tools/vendor/od_vendor_snapshot.py
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


# The two snapshot-class subtrees (CON §4.1 row 2; REC §1.2): 10
# daemon TS composer modules + 17 contracts prompt mirror files.
SNAPSHOT_CLASS_SUBDIRS: Tuple[Tuple[str, str], ...] = (
    ("apps/daemon/src/prompts", "prompts/daemon"),
    ("packages/contracts/src/prompts", "prompts/contracts"),
)


@dataclass(frozen=True)
class VendoredFile:
    """One file's vendoring record (sha + size + upstream path)."""

    relpath: str  # path under <dest>
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
    counts_per_subtree: Dict[str, int] = field(default_factory=dict)
    skipped_entries: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.files)

    def as_dict(self) -> Dict[str, object]:
        return {
            "source": self.source,
            "tag": self.tag,
            "dest": str(self.dest),
            "hashes_out": str(self.hashes_out),
            "total_files": self.total,
            "counts_per_subtree": dict(self.counts_per_subtree),
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


def _run_git(source: Path, *args: str) -> str:
    cmd = ["git", "-C", str(source)] + list(args)
    result = subprocess.run(cmd, capture_output=True, check=True)
    return result.stdout.decode("utf-8", errors="replace")


def _list_tree_paths(source: Path, ref: str, subdir: str) -> List[Tuple[str, str, str, int, str]]:
    """Same shape as ``od_vendor.py`` (slice ②).

    The slice ③ sync-runner mirrors this exact shape so the dry-run
    and the live sync can share the file-listing logic in the
    future.
    """
    raw = _run_git(source, "ls-tree", "-l", "-r", ref, "--", f"{subdir}/")
    out: List[Tuple[str, str, str, int, str]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            head, path = line.split("\t", 1)
        except ValueError:
            continue
        parts = head.split()
        if len(parts) != 4:
            continue
        mode, obj_type, sha, size_str = parts
        if obj_type != "blob":
            continue
        try:
            size = int(size_str)
        except ValueError:
            size = 0
        out.append((mode, obj_type, sha, size, path))
    return out


def _cat_blob(source: Path, blob_sha: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(source), "cat-file", "blob", blob_sha],
        capture_output=True,
        check=True,
    )
    return result.stdout


def _safe_join_under(base: Path, relpath: str) -> Path:
    target = (base / relpath).resolve()
    base_resolved = base.resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:  # pragma: no cover
        raise ValueError(f"path {relpath!r} escapes dest {base_resolved}") from exc
    return target


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def vendor(
    *,
    source: Path,
    tag: str,
    dest: Path,
    hashes_out: Path,
    progress: bool = True,
) -> VendorSummary:
    """Vendor the snapshot-with-drift-alarm class from upstream at ``tag``."""
    source = Path(source)
    dest = Path(dest)
    hashes_out = Path(hashes_out)

    if not source.is_dir():
        raise SystemExit(f"ERROR: source is not a directory: {source}")
    if not (source / ".git").exists():
        raise SystemExit(f"ERROR: source is not a git checkout: {source}")
    try:
        ref_sha = _run_git(source, "rev-parse", f"{tag}^{{}}").strip()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"ERROR: cannot resolve tag {tag!r} in {source}: {exc}") from exc

    dest.mkdir(parents=True, exist_ok=True)

    summary = VendorSummary(
        source=str(source),
        tag=tag,
        dest=str(dest),
        hashes_out=str(hashes_out),
    )

    for upstream_subdir, local_subdir in SNAPSHOT_CLASS_SUBDIRS:
        try:
            entries = _list_tree_paths(source, ref_sha, upstream_subdir)
        except subprocess.CalledProcessError as exc:
            raise SystemExit(f"ERROR: cannot list {upstream_subdir} at {tag}: {exc}") from exc
        for mode, _obj_type, blob_sha, upstream_size, upstream_path in entries:
            # Symlink guard (CON §1): never vendor symlinks.
            if mode == "120000":
                summary.skipped_entries.append(
                    (
                        upstream_path,
                        "symlink (git mode 120000) excluded — vendored trees must not contain symlinks (CON §1)",
                    )
                )
                continue
            try:
                data = _cat_blob(source, blob_sha)
            except subprocess.CalledProcessError as exc:
                summary.skipped_entries.append((upstream_path, f"cat-file failed: {exc}"))
                continue
            sha = _hash_bytes(data)
            byte_size = len(data)
            size = byte_size if byte_size else upstream_size
            # relpath under dest = "<local_subdir>/<basename>"
            relpath = f"{local_subdir}/{Path(upstream_path).name}"
            try:
                target = _safe_join_under(dest, relpath)
            except ValueError as exc:
                summary.skipped_entries.append((upstream_path, str(exc)))
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(data)
            summary.files.append(
                VendoredFile(
                    relpath=relpath,
                    upstream_relpath=upstream_path,
                    sha256=sha,
                    size_bytes=size,
                )
            )
        if progress:
            n = sum(1 for f in summary.files if f.upstream_relpath.startswith(upstream_subdir + "/"))
            print(f"  {upstream_subdir} -> {local_subdir}: {n} file(s)", file=sys.stderr)

    summary.counts_per_subtree = {
        local: sum(1 for f in summary.files if f.relpath.startswith(local + "/"))
        for upstream, local in SNAPSHOT_CLASS_SUBDIRS
    }

    hashes_out.parent.mkdir(parents=True, exist_ok=True)
    sorted_files = sorted(summary.files, key=lambda f: f.relpath)
    with open(hashes_out, "w", encoding="utf-8") as fh:
        for f in sorted_files:
            fh.write(f"{f.sha256}  {f.relpath}\n")

    return summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="od_vendor_snapshot",
        description="OD snapshot-with-drift-alarm vendoring (slice ③; "
        "byte-perfect, hash-verified, snapshot class).",
    )
    parser.add_argument(
        "--source",
        default=os.environ.get("OD_VENDOR_SOURCE"),
        help="path to the local OD git checkout (REQUIRED: --source or "
        "$OD_VENDOR_SOURCE; no host-specific default for portability)",
    )
    parser.add_argument(
        "--tag",
        default=os.environ.get("OD_VENDOR_TAG", "open-design-v0.23.0"),
        help="git tag to vendor (default: $OD_VENDOR_TAG = open-design-v0.23.0)",
    )
    parser.add_argument(
        "--dest",
        default=os.environ.get(
            "OD_VENDOR_SNAPSHOT_DEST",
            "plugins/opendesign/snapshot_with_drift_alarm",
        ),
        help="destination dir for the vendored tree (default: "
        "$OD_VENDOR_SNAPSHOT_DEST = plugins/opendesign/snapshot_with_drift_alarm)",
    )
    parser.add_argument(
        "--hashes-out",
        default=os.environ.get(
            "OD_VENDOR_SNAPSHOT_HASHES",
            "plugins/opendesign/snapshot_with_drift_alarm/HASHES.sha256",
        ),
        help="hash manifest path INSIDE the class subtree "
        "(sha256sum format; default: $OD_VENDOR_SNAPSHOT_HASHES)",
    )
    parser.add_argument(
        "--json-summary",
        default=None,
        help="optional path to also write a JSON summary of the vendoring run",
    )
    args = parser.parse_args(argv)

    if not args.source:
        raise SystemExit(
            "ERROR: --source is REQUIRED (or set $OD_VENDOR_SOURCE). "
            "No host-specific default — the script is portable."
        )

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
    print(f"Per-subtree counts: {summary.counts_per_subtree}", file=sys.stderr)
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
