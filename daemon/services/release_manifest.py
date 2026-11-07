"""Release-manifest reader (prompt-reload lane, AM-2).

A tiny, stdlib-only reader over the staged-release ``manifest.json``
written by ``scripts/upgrade/stage.sh`` (per-file map writer = the
``agents_manifest`` heredoc key). NOTHING in ``daemon/`` read this
artifact before this module (verified by the lane survey, F6): the
upgrade tooling reads only identity fields, and the FE re-hashes the
map at staged-verification time. This module is the first RUNTIME
consumer — the ``prompt_reload`` primitive uses it to verify the
loader's read set for the calling agent (AM-1 scope), and the planned
second consumer is the arm-time scripts/provenance-guard family.

Contract (AM-2, frozen):

* ``load_release_manifest(release_root)`` — read
  ``<release_root>/manifest.json``. Absent file → ``None`` (the caller
  decides what absence means per self-env, AM-3). Torn / invalid JSON /
  non-object root / missing ``agents_manifest`` / non-string values →
  :class:`ManifestError` (a TYPED raise — the caller maps it to
  ``prompt_reload_manifest_unreadable``; torn JSON is a different
  failure with a different operator remedy than absence, D6 code 5).
  Missing TOP-LEVEL keys other than ``agents_manifest`` are TOLERATED
  (the demo v0.16.3 manifest predates ``plugins_manifest``).
* ``verify_paths(release_root, manifest, relpaths)`` — verify the
  on-disk sha256 of each requested path against the manifest map.
  Unmanifested requested paths are governed by an ALLOW-LIST: inside
  the agent-dir-relative class ``<agent>/memories/`` they are SKIP
  (Advisory 2: ``memories/*.md`` is inside the loader's read set but has
  ZERO entries in the real ``agents_manifest`` — 379 entries, 0
  memories, verified against releases/v0.29.0); anywhere else an
  unmanifested requested path is a REFUSAL-grade ``unexpected_skips``
  finding (C1 — a skip must never be laundered as benign). Manifested
  paths must exist on disk with a matching sha256. "On disk, not in
  manifest" is NEVER a mismatch for requested-path checks; the strict
  whole-tree mode is a different (default-OFF) policy that this module
  does not implement.

NO tree walk (only the caller-requested paths are opened), NO
``daemon.tools`` import, NO daemon imports at all — stdlib only, so
the scripts-side consumer can lift it verbatim.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

#: The one top-level key this reader consumes — the flat
#: ``{relpath: sha256}`` map over ``agents/`` (keys relative to
#: ``agents/``, values hex sha256). Written by stage.sh.
AGENTS_MANIFEST_KEY = "agents_manifest"

#: Read in 1 MiB blocks — manifests map SMALL files; one block covers
#: nearly every agent artifact, and the loop bounds memory for the
#: rare large one.
_HASH_BLOCK = 1024 * 1024


class ManifestError(Exception):
    """Manifest present but unreadable/invalid (torn JSON, wrong shape).

    Distinct from ``None`` (absent) on purpose: absence and corruption
    have different operator remedies (D6 codes 4 vs 5).
    """


@dataclass(frozen=True)
class ReleaseManifest:
    """The parsed manifest — only the ``agents_manifest`` map is load-bearing."""

    release_root: Path
    agents_manifest: dict[str, str]
    raw: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ManifestVerdict:
    """Result of ``verify_paths`` — counts + the mismatch list, no prose."""

    checked: int
    skipped_unmanifested: int
    mismatches: tuple[str, ...]
    #: C1 (leader ruling 2026-11-06): requested paths skipped for being
    #: unmanifested OUTSIDE the allow-listed class (``memories/``). A
    #: non-zero value is a REFUSAL-grade finding — a skip outside the
    #: allow-list can never again be laundered as benign.
    unexpected_skips: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.mismatches and not self.unexpected_skips


def load_release_manifest(release_root: Path) -> ReleaseManifest | None:
    """Load ``<release_root>/manifest.json``; ``None`` when absent.

    Raises:
        ManifestError: the file exists but is torn/invalid JSON, not a
            JSON object, lacks ``agents_manifest``, or the map values
            are not strings.
    """
    path = Path(release_root) / "manifest.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"manifest.json unreadable at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"manifest.json root is not an object at {path}")
    agents_map = raw.get(AGENTS_MANIFEST_KEY)
    if not isinstance(agents_map, dict):
        raise ManifestError(
            f"manifest.json lacks a valid '{AGENTS_MANIFEST_KEY}' map at {path}"
        )
    for key, value in agents_map.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ManifestError(
                f"manifest.json '{AGENTS_MANIFEST_KEY}' has a non-string entry "
                f"at {path}: {key!r}"
            )
    return ReleaseManifest(
        release_root=Path(release_root),
        agents_manifest=dict(agents_map),
        raw=raw,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(_HASH_BLOCK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _in_skippable_class(relpath: str, prefixes: tuple[str, ...]) -> bool:
    """True when ``relpath`` sits inside an allow-listed skip class
    WITHIN an agent directory (``<agent>/<class>/...``).

    N2 (leader ruling): the class is matched RELATIVE to the agent dir —
    never on the absolute first segment. The previous segment-anywhere
    match classed the artifacts of an agent literally named ``memories``
    (``memories/soul.md``) as skippable — the same class of defect as C1.
    Truth table (pinned): ``ari/memories/x.md`` → skippable;
    ``memories/soul.md`` → NOT skippable.
    """
    parts = [part for part in relpath.strip("/").split("/") if part]
    if len(parts) < 3:  # need at least <agent>/<class>/<file>
        return False
    for prefix in prefixes:
        class_parts = [part for part in prefix.strip("/").split("/") if part]
        if class_parts and parts[1 : 1 + len(class_parts)] == class_parts:
            return True
    return False


def verify_paths(
    release_root: Path,
    manifest: ReleaseManifest,
    relpaths: list[str] | tuple[str, ...],
    *,
    skippable_prefixes: tuple[str, ...] = ("memories/",),
) -> ManifestVerdict:
    """Verify requested ``agents/``-relative paths against the manifest.

    Per path: unmanifested → SKIP when the path is inside an
    ALLOW-LISTED class (``memories/`` — the real manifest carries zero
    memory entries, Advisory 2); unmanifested OUTSIDE the allow-list →
    ``unexpected_skips`` (C1: a skip must never be laundered as benign);
    manifested but missing on disk → MISMATCH (the release claims the
    file); manifested with a differing sha256 → MISMATCH (tamper /
    corruption — the exact class this check exists to catch).
    """
    root = Path(release_root)
    checked = 0
    skipped = 0
    mismatches: list[str] = []
    unexpected_skips: list[str] = []
    for relpath in relpaths:
        expected = manifest.agents_manifest.get(relpath)
        if expected is None:
            if _in_skippable_class(relpath, skippable_prefixes):
                skipped += 1
            else:
                unexpected_skips.append(
                    f"{relpath}: unmanifested outside the allow-listed "
                    f"skip class {skippable_prefixes}"
                )
            continue
        on_disk = root / "agents" / relpath
        if not on_disk.is_file():
            mismatches.append(f"{relpath}: missing on disk (manifest expects it)")
            continue
        checked += 1
        actual = _file_sha256(on_disk)
        if actual != expected:
            mismatches.append(
                f"{relpath}: sha256 mismatch (manifest {expected[:12]}…, "
                f"disk {actual[:12]}…)"
            )
    return ManifestVerdict(
        checked=checked,
        skipped_unmanifested=skipped,
        mismatches=tuple(mismatches),
        unexpected_skips=tuple(unexpected_skips),
    )
