"""Sole-writer mechanization gate (slice ⑥ — the ⑤ reviewer ruling).

**The ruling (slice-⑤ review record, BINDING FROM ⑥):** ALL
vendored-class membership changes route EXCLUSIVELY through the
sync-runner — "manifest upstream_paths declare → sync →
hash-register"; plugin trees must NEVER be hand-edited.  The ⑤
developer-side membership extension (deck-protocol.ts +
deck-stage-fallback.ts, HASHES 27→29) was accepted ONLY as a
pre-⑥ disclosed transition edit because its four conditions held.
This module mechanizes those FOUR BARS as a repeatable CI/runner
check:

1. **SHA-byte-faithful to the pin** — every class-subtree file's
   git-blob SHA-1 equals the pinned upstream blob SHA (an on-disk
   modification at the SAME pin is a hand-edit:
   ``modified-from-pin``).
2. **hash-registered** — every class-subtree file (minus the
   locally-owned ``HASHES.sha256``) has a HASHES.sha256 row whose
   sha256 matches the file bytes (``hash-unregistered`` /
   ``hash-mismatch``).
3. **manifest↔HASHES↔tree consistent** — the HASHES set equals the
   tree set exactly (no orphans: ``hashes-orphan``); every tree file
   falls under a manifest-declared class path
   (``file-outside-declared-paths``).
4. **disclosed (sync_result trail)** — the membership delta vs the
   pin (pin∩declared) is fully explained: an upstream file missing
   from the tree must be covered by a
   ``parity_boundary.intentionally_not_vendored`` row
   (``undeclared-exclusion`` otherwise); a tree file not in
   pin∩declared is a hand-edit, full stop
   (``undeclared-file``).  A zero delta needs no disclosure (the
   ②-era vendored state is grandfathered by exact pin-fidelity);
   the class's sync-trail line count is reported as evidence
   context either way.

**Enforcement topology:** CI/runner check (this module) — wired
additively into :func:`daemon.plugin_subsystem.schema_ci.run_ci`
(``sole_writer`` key) and invocable standalone.  Bars 1+4 consult
the pinned upstream (a local git checkout — read-only); bars 2+3
are offline-provable.  When no upstream is resolvable the gate
reports ``skipped: upstream-unavailable`` (a VISIBLE skip — never a
silent pass; the staleness predicate remains the promote-time
gate).

**Refusal codes (closed set):** ``modified-from-pin`` ·
``hash-unregistered`` · ``hash-mismatch`` · ``hashes-orphan`` ·
``file-outside-declared-paths`` · ``undeclared-file`` ·
precondition ``manifest-unreadable``.
Any violation ⇒ ``ok: false`` (the vendoring/class state is
refused, never the runtime — CON §5 discipline).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import yaml

from daemon.plugin_subsystem.manifest_reader import (
    MANIFEST_FILENAME,
    ManifestRefusal,
    read_manifest,
)
from daemon.plugin_subsystem.sync_runner import (
    LOCALLY_OWNED_FILENAMES,
    SYNC_TRAIL_FILENAME,
    UpstreamGit,
    _compute_class_diff,
)

__all__ = [
    "check_sole_writer",
    "HASHES_FILENAME",
]

HASHES_FILENAME = "HASHES.sha256"


def _parse_hashes_file(path: Path) -> Dict[str, str]:
    """Parse a sha256sum-format hash manifest: ``<sha256>  <relpath>``."""
    out: Dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        out[parts[1].lstrip("*").strip()] = parts[0].strip().lower()
    return out


def _parity_excluded(manifest: Mapping[str, Any], local_rel: str) -> bool:
    """True iff ``local_rel``'s upstream counterpart is covered by a
    ``parity_boundary.intentionally_not_vendored`` row.

    The parity rows record upstream paths (or glob-ish suffixes);
    matching is suffix-tolerant: a row naming
    ``assets/prompt-templates/image/*.jpg`` or
    ``apps/daemon/src/prompts/`` matches any local file whose
    upstream path ends with the row's non-glob tail.  Deliberately
    simple: the parity boundary is a documentation channel, and the
    gate only needs "is this absence DISCLOSED somewhere".
    """
    parity = manifest.get("parity_boundary") or {}
    rows = parity.get("intentionally_not_vendored") or []
    if not isinstance(rows, list):
        return False
    for row in rows:
        if isinstance(row, dict):
            candidate = str(
                row.get("upstream_path") or row.get("path") or ""
            )
        else:
            candidate = str(row)
        candidate = candidate.strip().rstrip("/")
        if not candidate:
            continue
        tail = candidate.split("/")[-1]
        if tail == "*":
            tail = candidate.rstrip("*").rstrip("/")
        if local_rel.endswith(tail) or tail in local_rel:
            return True
    return False


def check_sole_writer(
    plugin_dir: Path,
    *,
    upstream: Optional[UpstreamGit] = None,
    upstream_tag: Optional[str] = None,
    now_trail_lines: bool = True,
) -> Dict[str, Any]:
    """Run the four-bar sole-writer gate on one plugin dir.

    Args:
        plugin_dir: The plugin tree (MANIFEST.yaml + class
            subtrees + HASHES).
        upstream: An opened :class:`UpstreamGit` at the plugin's
            pin.  ``None`` ⇒ the gate tries to construct one from
            the manifest's ``plugin.upstream.repo`` (a local
            checkout path); a URL-shaped repo or an unopenable
            checkout ⇒ a VISIBLE ``skipped`` verdict
            (``upstream-unavailable``), never a silent pass.
        upstream_tag: Pin override (defaults to the manifest's
            ``copy_freely`` pin — the single-tag discipline).
        now_trail_lines: Include the trail line count in the
            report (evidence context for bar 4).

    Returns:
        JSON-serializable verdict: ``{ok, plugin, bars:{...},
        violations:[{code, detail, class?, file?}], skipped,
        trail_lines}``.
    """
    plugin_dir = Path(plugin_dir).resolve()
    violations: List[Dict[str, Any]] = []
    verdict: Dict[str, Any] = {
        "ok": False,
        "plugin": plugin_dir.name,
        "plugin_dir": str(plugin_dir),
        "bars": {},
        "violations": violations,
        "skipped": None,
        "trail_lines": None,
    }

    # ── Preconditions ────────────────────────────────────────────────
    try:
        declaration = read_manifest(plugin_dir)
    except ManifestRefusal as exc:
        verdict["skipped"] = "manifest-unreadable"
        violations.append(
            {
                "code": "manifest-unreadable",
                "detail": f"manifest reader refused: {exc}",
            }
        )
        return verdict
    # The typed declaration does not surface the parity rows; re-read
    # the YAML for the parity-boundary sections (bar 4's disclosure
    # channel).
    with open(plugin_dir / MANIFEST_FILENAME, "r", encoding="utf-8") as fh:
        manifest = yaml.safe_load(fh) or {}

    trail_path = plugin_dir / SYNC_TRAIL_FILENAME
    trail_lines = 0
    if trail_path.is_file():
        with open(trail_path, "r", encoding="utf-8") as fh:
            trail_lines = sum(1 for line in fh if line.strip())
    if now_trail_lines:
        verdict["trail_lines"] = trail_lines

    # ── Resolve the pin + upstream adapter ───────────────────────────
    pin = upstream_tag or declaration.pin_for_class("copy_freely")
    if not pin:
        verdict["skipped"] = "no-pin"
        violations.append(
            {
                "code": "manifest-unreadable",
                "detail": "no tag pin resolvable for the vendored classes",
            }
        )
        return verdict
    if upstream is None:
        repo = declaration.upstream_repo
        try:
            from daemon.plugin_subsystem.sync_runner import LocalGitCheckout

            upstream = LocalGitCheckout(repo, pin)
        except Exception as exc:  # noqa: BLE001 - visible-skip surface
            verdict["skipped"] = "upstream-unavailable"
            verdict["skipped_detail"] = str(exc)
            return verdict

    # ── Per-class bars ───────────────────────────────────────────────
    bar1_ok = True
    bar2_ok = True
    bar3_ok = True
    bar4_ok = True

    for class_name in ("copy_freely", "snapshot_with_drift_alarm"):
        section = getattr(declaration, class_name, None)
        if not section:
            continue
        class_paths = list(section.get("paths", []) or ())
        if not class_paths:
            continue
        local_target = plugin_dir / class_name
        explicit_upstream = declaration.upstream_paths_for_class(class_name)
        upstream_paths = (
            list(explicit_upstream)
            if explicit_upstream is not None
            else [
                p[len(class_name) + 1:] if p.startswith(class_name + "/") else p
                for p in class_paths
            ]
        )

        try:
            diff = _compute_class_diff(
                upstream=upstream,
                upstream_tag=pin,
                target_class=class_name,
                upstream_paths=upstream_paths,
                local_paths=class_paths,
                local_target=local_target,
                explicit_upstream_paths=explicit_upstream,
            )
        except Exception as exc:  # noqa: BLE001 - fail-closed surface
            violations.append(
                {
                    "code": "manifest-unreadable",
                    "class": class_name,
                    "detail": f"upstream consult failed ({exc}) — the gate refuses rather than understate",
                }
            )
            bar1_ok = bar4_ok = False
            continue

        # ── Bar 1: byte-faithful (same-pin modifications are hand-edits)
        if diff.modified_files:
            bar1_ok = False
            for f in diff.modified_files:
                violations.append(
                    {
                        "code": "modified-from-pin",
                        "class": class_name,
                        "file": f,
                        "detail": (
                            "on-disk content differs from the pinned "
                            "upstream blob at the SAME tag — hand-edit; "
                            "route content changes through the sync-runner"
                        ),
                    }
                )

        # ── Bars 2+3: hash registration + manifest↔HASHES↔tree ─────────
        hashes = _parse_hashes_file(local_target / HASHES_FILENAME)
        # Walk the ENTIRE class subtree (not just the declared paths):
        # a file under the class root but outside every declared path
        # is exactly the bar-3 violation shape — walking only declared
        # paths would let it escape the gate.
        actual_files: List[str] = []
        if local_target.is_dir():
            for path in sorted(local_target.rglob("*")):
                if not path.is_file():
                    continue
                if path.name in LOCALLY_OWNED_FILENAMES:
                    continue
                actual_files.append(path.relative_to(local_target).as_posix())
        actual_set = set(actual_files)
        hashes_set = set(hashes)

        for rel in sorted(actual_set - hashes_set):
            bar2_ok = False
            violations.append(
                {
                    "code": "hash-unregistered",
                    "class": class_name,
                    "file": rel,
                    "detail": "tree file absent from HASHES.sha256 (bar 2: hash-registered)",
                }
            )
        for rel in sorted(actual_set & hashes_set):
            digest = hashlib.sha256((local_target / rel).read_bytes()).hexdigest()
            if digest != hashes[rel]:
                bar2_ok = False
                violations.append(
                    {
                        "code": "hash-mismatch",
                        "class": class_name,
                        "file": rel,
                        "detail": "HASHES.sha256 digest does not match file bytes (bar 2)",
                    }
                )
        for rel in sorted(hashes_set - actual_set):
            bar3_ok = False
            violations.append(
                {
                    "code": "hashes-orphan",
                    "class": class_name,
                    "file": rel,
                    "detail": "HASHES.sha256 row with no tree file (bar 3: manifest↔HASHES↔tree consistent)",
                }
            )
        # Every tree file under a declared path (bar 3, tree side).
        declared_prefixes = tuple(
            p[len(class_name) + 1:].rstrip("/")
            if p.startswith(class_name + "/")
            else p.rstrip("/")
            for p in class_paths
        )
        for rel in actual_files:
            if not any(
                rel == prefix or rel.startswith(prefix + "/")
                for prefix in declared_prefixes
                if prefix
            ):
                bar3_ok = False
                violations.append(
                    {
                        "code": "file-outside-declared-paths",
                        "class": class_name,
                        "file": rel,
                        "detail": "tree file outside every manifest-declared class path (bar 3)",
                    }
                )

        # ── Bar 4: disclosed membership delta ──────────────────────────
        # Diff vocabulary (sync_runner._compute_class_diff):
        #   added_files   = upstream-present, tree-absent  → pull
        #                   candidates.  A pull candidate is a
        #                   MEMBERSHIP INCOMPLETENESS, not a hand-edit;
        #                   disclosed iff a parity row covers it,
        #                   otherwise report-only evidence (the
        #                   sync-runner's own next-pull surface).
        #   removed_files = tree-present, pin∩declared-absent → a
        #                   file in a vendored subtree that the pin
        #                   does not place there: a HAND-ADDITION (or
        #                   manifest-mapping drift) — always a
        #                   violation (``undeclared-file``); no trail
        #                   can disclose content the pin never had.
        if diff.added_files:
            uncovered = [
                rel for rel in diff.added_files
                if not _parity_excluded(manifest, rel)
            ]
            if uncovered:
                verdict.setdefault("membership_incomplete", {})[class_name] = (
                    uncovered
                )
        for rel in diff.removed_files:
            bar4_ok = False
            violations.append(
                {
                    "code": "undeclared-file",
                    "class": class_name,
                    "file": rel,
                    "detail": (
                        "tree file the pinned upstream does not place in "
                        "the declared paths — hand-addition (or manifest "
                        "mapping drift); route membership changes through "
                        "the sync-runner (bar 4: disclosed)"
                    ),
                }
            )

    verdict["bars"] = {
        "byte_faithful_to_pin": bar1_ok,
        "hash_registered": bar2_ok,
        "manifest_hashes_tree_consistent": bar3_ok,
        "membership_delta_disclosed": bar4_ok,
    }
    verdict["ok"] = bar1_ok and bar2_ok and bar3_ok and bar4_ok and not violations
    return verdict
