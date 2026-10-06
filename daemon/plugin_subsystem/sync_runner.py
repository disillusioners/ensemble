"""Sync-runner (REC §1.2 component 6 — slice ③).

Tag-diff → classify → report; **sole writer** of ``copy_freely/`` and
``snapshot_with_drift_alarm/``; refuses ``own_outright/`` (hard rule, no
override — CON §5, REC §1.4).

**Frozen contract surface (CON §5):**

.. code-block:: yaml

    sync(plugin: "<name>",
         target_class: "copy_freely" | "snapshot_with_drift_alarm",
         dry_run: true)                  # default TRUE — shadow-before-apply is the cheap path

Manifest is re-read FRESH every call (no cache).  Copy-freely pulls
stage into temp + atomic rename (mid-pull crash = whole-or-nothing).
Divergence-register update is ATOMIC with the manifest (temp + rename;
never a torn manifest).

**Refusal-codes (CON §5 closed enum — EXACTLY these 7; refused ⇒
``sync_result.refusal``):**

- ``absent_execution_mode`` — manifest has no ``execution_mode`` (silence
  is not permission, CON §2)
- ``own_outright_mutation`` — caller asked sync to write to
  ``own_outright/`` (the hard rule; no override flag exists).
  REACHABLE via the public API: ``sync(..., target_class="own_outright")``
  refuses up-front with this code (CON §5: "sync into own_outright/
  ⇒ refuse")
- ``license_invalid`` — manifest's SPDX is not in the vendored validator
  list
- ``fence_missing`` — A-path without a complete ``fence_grant`` block
- ``non_tag_pin`` — manifest's per-class tag pin is not a git tag
  (offline-provable markers only; full branch-vs-tag discrimination
  rides the git consultation in this module)
- ``tag_missing_upstream`` — the upstream tag is absent at the
  remote (deleted) OR the local fetch disagrees with the manifest's
  expected commit (force-push); refuse + alert.  Fail-closed
  extension (CON §5 line 196 + review ruling): an UNOBSERVABLE tag
  IS a missing tag — a missing/unusable ``plugin_dir``, an empty
  upstream repo URL, or an unopenable upstream checkout (absent /
  not a git repository) all refuse with this code, the message
  naming the actual cause.  Further fail-closed extension (council
  ③ fix, 2026-10-06): an unobservable BLOB or SUBDIR at the pinned
  tag (e.g. ``git cat-file blob <sha>`` fails, ``git ls-tree <ref>:<subdir>/``
  fails) is the same family — refuse with this code, the message
  naming the cause.  **Dry-run parity is mandatory**: a dry_run
  call that would refuse under non-dry MUST also return
  action=refused with the same code (a dry-run is a truthful
  report of what the non-dry run would do).
- ``misclassified_at_vendoring`` — vendoring_classifier refuses
  (e.g. path letter not registered, execution_mode not in row's
  allowlist, fence-stripped A); VENDORING is refused, never runtime

**Adjudication flag (council ③ fix, 2026-10-06 — deferred to
reviewer):**

The fail-closed ``tag_missing_upstream`` mapping for "upstream
content incomplete at the pinned tag" (subdir or blob
unreadable) is intentionally NOT a new refusal code.  The
council question — "should ``upstream_content_incomplete`` become
an additive refusal code at the next 1.0.x epoch?" — is
**flagged for reviewer; NOT unilaterally added** here.  A new code
requires CON §8 row 11 (enum additions require a 1.x.0 minor
bump).  Until adjudicated, the existing 7-code enum is preserved
and the content-incomplete case rides ``tag_missing_upstream``
with a cause-bearing message.  Reviewer can promote the
additive code in a follow-up if they concur the diagnostic
distinctness warrants a separate surface.

**Operational mappings (deliberately OUTSIDE the refusal enum):**

- ``target_class="own_outright"`` → the ``own_outright_mutation``
  refusal above (a §5 contract refusal — reachable, tested)
- any other ``target_class`` outside the declared domain
  (``copy_freely`` | ``snapshot_with_drift_alarm`` |
  ``own_outright``) → :class:`ValueError` — API-signature misuse
  (a programmer error, not a §5 contract refusal); the diagnostic
  rides the exception message, never a refusal code

Enum additions require a 1.x.0 minor bump (CON §8 row 11) — none
were added here: this restores the §5 exact 7 (review ruling,
council-od-slice4-20261006-201428, 2026-10-06).

**Drift-alarm emission (CON §5; REC §9 trigger-engine probe):**

For ③, the drift facts produced by a sync are emitted into the
``sync_result.alarm`` field and a documented in-process sink
(:func:`emit_drift_event` is a no-op stub that REC §9 OQ
"trigger-engine payload acceptance" documents for slice ⑥ to
wire). The probe decision (envelope vs verbatim) is documented
in the deliverable; no trigger-engine EDIT happens here.

**Atomicity guarantee (CON §5):**

Copy-freely pulls stage into a sibling temp directory
(``<dest>.sync_stage.<pid>``) and rename atomically over
``<dest>`` on success. Mid-pull crash = ``<dest>`` is unchanged
(whole-or-nothing). The divergence-register update writes to
the same temp + atomic rename so a manifest with a new
divergence entry can never be left torn.

**Own-outright invariant (REC §1.4 + CON §5):**

The sync-runner NEVER writes to ``own_outright/`` — not via any
flag, not via any combination of arguments. The only way to
write to ``own_outright/`` is to edit it by hand. The
``own_outright_mutation`` refusal is the corresponding gate
(this module does the check up-front; the filesystem also

**Reader-passthrough namespacing (council ③ item (a), 2026-10-06):**

The ① manifest reader carries its own typed-refusal surface
(21 codes; ``manifest_reader.py``).  Earlier the sync-runner
passthrough at ``sync()`` returned the reader's bare code,
widening the observable ``SyncResult.refusal.code`` surface
beyond the CON §5 sync 7.  The ③ fix is a **clean, lossless
wrap**: if the reader's code IS one of the sync 7, pass
through verbatim (the overlap is intentional — the reader
re-uses the sync names when the diagnostic is the same
logical failure); otherwise namespace as
``manifest_reader:<code>``.  The sync 7 enum stays observably
closed; the reader's diagnostic distinctness is preserved
in the namespaced suffix.  See ``_SYNC_ENUM_7`` and the
reader-passthrough site for the discriminator.
refuses via the ``own_outright/`` directory being outside the
sync's write scope).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from daemon.plugin_subsystem.manifest_reader import (
    MANIFEST_FILENAME,
    ManifestRefusal,
    read_manifest,
    validate_manifest,
)
from daemon.plugin_subsystem.path_type_registry import PathTypeRegistry, load_default_registry
from daemon.plugin_subsystem.plugin_declaration import PluginDeclaration
from daemon.plugin_subsystem.vendoring_classifier import (
    ClassificationRefusal,
    classify,
)

__all__ = [
    "SyncAction",
    "SyncResult",
    "SyncRunner",
    "SyncRefusal",
    "sync",
    "emit_drift_event",
    "LOCALLY_OWNED_FILENAMES",
    "DEFAULT_GIT_REMOTE_NAME",
    "UpstreamContentIncompleteError",
]


# ─── refusal-codes (CON §5 closed enum) ────────────────────────────────────────

REFUSAL_ABSENT_EXECUTION_MODE = "absent_execution_mode"
REFUSAL_OWN_OUTRIGHT_MUTATION = "own_outright_mutation"
REFUSAL_LICENSE_INVALID = "license_invalid"
REFUSAL_FENCE_MISSING = "fence_missing"
REFUSAL_NON_TAG_PIN = "non_tag_pin"
REFUSAL_TAG_MISSING_UPSTREAM = "tag_missing_upstream"
REFUSAL_MISCLASSIFIED_AT_VENDORING = "misclassified_at_vendoring"


# The CON §5 sync-runner closed-enum 7.  Used at the
# reader-passthrough seam to decide whether a reader's typed
# refusal code is a same-named overlap (pass through verbatim)
# or a reader-only code (namespace as ``manifest_reader:<code>``).
# Kept as a frozenset for O(1) membership check and to make
# the closed-enum invariant explicit at the call site.
_SYNC_ENUM_7: frozenset = frozenset({
    REFUSAL_ABSENT_EXECUTION_MODE,
    REFUSAL_OWN_OUTRIGHT_MUTATION,
    REFUSAL_LICENSE_INVALID,
    REFUSAL_FENCE_MISSING,
    REFUSAL_NON_TAG_PIN,
    REFUSAL_TAG_MISSING_UPSTREAM,
    REFUSAL_MISCLASSIFIED_AT_VENDORING,
})


# Locally-owned filenames inside any class subtree that the sync-runner
# must preserve (never overwrite from upstream content, never report in
# diff_summary). HASHES.sha256 is the canonical example: a hash
# manifest is a build artifact, not an upstream file.
LOCALLY_OWNED_FILENAMES: Tuple[str, ...] = ("HASHES.sha256",)

# Default git remote name used by ``git ls-remote`` for the tag-missing
# check. Match upstream's typical convention; override per-call.
DEFAULT_GIT_REMOTE_NAME = "origin"


# ─── types ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SyncAction:
    """One sync-runner action's outcome (used by tests + the result type)."""

    name: str  # "clean_pulled" | "alarmed" | "refused" | "no_change"


@dataclass(frozen=True)
class DiffSummary:
    """Per-class diff summary (CON §5 sync_result.diff_summary)."""

    files_added: int = 0
    files_modified: int = 0
    files_removed: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "files_added": self.files_added,
            "files_modified": self.files_modified,
            "files_removed": self.files_removed,
        }


@dataclass(frozen=True)
class DriftAlarm:
    """Drift-alarm payload (CON §5 sync_result.alarm)."""

    divergence_register_entry: Mapping[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {"divergence_register_entry": dict(self.divergence_register_entry)}


@dataclass(frozen=True)
class SyncRefusal:
    """A typed refusal (CON §5 sync_result.refusal).

    The ``code`` is one of the CON §5 closed-enum codes listed at the
    top of this module; ``message`` is a one-line operator-readable
    description; ``location`` anchors the failure (manifest path, git
    ref, etc.) for grep-friendliness.
    """

    code: str
    message: str
    location: str = ""

    def as_dict(self) -> Dict[str, str]:
        return {"code": self.code, "message": self.message, "location": self.location}


@dataclass(frozen=True)
class SyncResult:
    """The frozen-shape result of a single ``sync(...)`` call (CON §5)."""

    plugin: str
    target_class: str
    upstream_tag: str
    action: str  # "clean_pulled" | "alarmed" | "refused" | "no_change"
    diff_summary: DiffSummary
    alarm: Optional[DriftAlarm] = None
    refusal: Optional[SyncRefusal] = None
    staleness_age_days: int = 0

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "plugin": self.plugin,
            "target_class": self.target_class,
            "upstream_tag": self.upstream_tag,
            "action": self.action,
            "diff_summary": self.diff_summary.as_dict(),
            "staleness_age_days": self.staleness_age_days,
        }
        if self.alarm is not None:
            out["alarm"] = self.alarm.as_dict()
        if self.refusal is not None:
            out["refusal"] = self.refusal.as_dict()
        return out


# ─── sync-runner ──────────────────────────────────────────────────────────────


class SyncRunner:
    """The sync-runner façade.  Construction is dependency-free; every call
    re-reads the manifest (CON §5: "manifest re-read FRESH every call,
    no cache").  One instance can be reused for many calls; the
    ref-reading happens on every call.

    Parameters
    ----------
    registry : PathTypeRegistry
        Used for the vendoring-time classification step (CON §4
        path-type row).  Defaults to the vendored registry.
    spdx_ids : frozenset[str], optional
        Used for the license validator check (CON §5
        ``license_invalid``).  Defaults to the vendored list.
    upstream_git_factory : callable, optional
        Factory that takes (repo_url, ref) and returns an
        :class:`UpstreamGit` adapter.  Default uses a local
        :class:`LocalGitCheckout`.  Slice ③ ships the local
        adapter; slice ⑥ may add an HTTP one for upstream
        network access.
    clock : callable, optional
        Returns the current ``datetime`` (UTC, tz-aware).  Tests
        inject a fixed clock to make staleness deterministic.
    """

    def __init__(
        self,
        registry: Optional[PathTypeRegistry] = None,
        spdx_ids: Optional[frozenset] = None,
        upstream_git_factory: Optional[Any] = None,
        clock: Optional[Any] = None,
    ) -> None:
        self.registry = registry if registry is not None else load_default_registry()
        self._spdx_ids = spdx_ids  # resolved lazily from reader
        self._upstream_git_factory = upstream_git_factory or LocalGitCheckout
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- public API ------------------------------------------------------------

    def sync(
        self,
        plugin: str,
        target_class: str,
        *,
        upstream_repo: Optional[str] = None,
        upstream_tag: Optional[str] = None,
        plugin_dir: Optional[Path] = None,
        dry_run: bool = True,
    ) -> SyncResult:
        """One sync call.  See module docstring for the frozen contract."""
        if target_class == "own_outright":
            # CON §5: "sync into own_outright/ ⇒ refuse" — the hard
            # rule (REC §1.4, no override flag exists).  This is the
            # §5 enum code's REACHABLE emission site.
            return self._refuse(
                plugin=plugin,
                target_class=target_class,
                upstream_tag=upstream_tag or "",
                code=REFUSAL_OWN_OUTRIGHT_MUTATION,
                message=(
                    "refusing sync into own_outright/: own-outright content is "
                    "authored in-place and NEVER written by sync "
                    "(CON §5 hard rule; no override flag exists)"
                ),
                location="sync(...) call",
            )
        if target_class not in ("copy_freely", "snapshot_with_drift_alarm"):
            # Outside the declared domain = API-signature misuse (a
            # programmer error, not a §5 contract refusal) — raised,
            # never mapped onto the frozen refusal surface (review
            # ruling: the refusal enum stays EXACTLY CON §5's 7).
            raise ValueError(
                f"target_class {target_class!r} is outside the declared domain "
                "(copy_freely | snapshot_with_drift_alarm | own_outright); "
                "sync() refuses own_outright and syncs the other two — "
                "anything else is a caller bug, not a sync refusal"
            )

        plugin_dir = Path(plugin_dir) if plugin_dir is not None else None
        try:
            declaration = self._read_declaration(plugin, plugin_dir)
        except ManifestReaderError as exc:
            # Reader-passthrough seam (council ③ warning
            # adjudication, 2026-10-06).  The slice ① reader
            # carries its own typed-refusal surface (21 codes;
            # see ``manifest_reader.py``); the sync-runner
            # surface is the CON §5 closed-enum 7.  Without
            # this namespacing the passthrough widens the
            # observable ``SyncResult.refusal.code`` beyond the
            # sync 7 (e.g. ``manifest_unparseable``,
            # ``name_invalid``, ``upstream_paths_malformed``
            # leak through as bare values).  We use a lossless
            # overlap / namespace split:
            #
            # - If the reader's code is ALSO one of the
            #   CON §5 sync 7, pass through verbatim.  The
            #   reader intentionally re-uses the sync names
            #   (e.g. ``non_tag_pin``, ``license_invalid``,
            #   ``fence_missing``, ``absent_execution_mode``)
            #   when the diagnostic is the same logical
            #   failure — collapsing them to a single bare
            #   value is correct and lossless.
            # - Otherwise, namespace as
            #   ``manifest_reader:<code>`` to keep the
            #   reader's diagnostic distinct on the sync
            #   surface while preserving the full code
            #   string for the operator.
            #
            # Two frozen surfaces: sync 7-code enum (CON §5)
            # + reader manifest codes (①).  The passthrough
            # below is 1:1 by design; the namespacing is the
            # lossless discriminator that keeps the sync
            # surface observably closed.  (CON §8 row 11:
            # enum additions require a 1.x.0 minor bump —
            # the same flag applies to a future
            # "unify the two surfaces" move.)
            reader_code = exc.refusal.code
            if reader_code in _SYNC_ENUM_7:
                out_code = reader_code
            else:
                out_code = f"manifest_reader:{reader_code}"
            return self._refuse(
                plugin=plugin,
                target_class=target_class,
                upstream_tag=upstream_tag or "",
                code=out_code,
                message=exc.refusal.message,
                location=f"manifest_reader:{reader_code}",
            )
        if declaration is None:
            # Fail-closed (CON §5 line 196 + review ruling): with no
            # readable manifest the tag is unobservable, and an
            # unobservable tag IS a missing tag on the refusal
            # surface; the message keeps the actual cause.
            return self._refuse(
                plugin=plugin,
                target_class=target_class,
                upstream_tag=upstream_tag or "",
                code=REFUSAL_TAG_MISSING_UPSTREAM,
                message=(
                    "upstream tag unobservable: plugin_dir is required to read "
                    "the manifest (CON §5: re-read fresh every call)"
                ),
                location="sync(...) call",
            )

        # Resolve upstream ref from the manifest's per-class pin (caller
        # may override; the override is for tests + the dry-run probe).
        effective_tag = upstream_tag or declaration.pin_for_class(target_class)
        if not effective_tag:
            return self._refuse(
                plugin=plugin,
                target_class=target_class,
                upstream_tag=upstream_tag or "",
                code=REFUSAL_NON_TAG_PIN,
                message=(
                    f"no tag pin declared for {target_class!r} (and copy_freely pin absent)"
                ),
                location=(
                    f"{plugin}/{MANIFEST_FILENAME}#upstream.tag_pin_per_class.{target_class}"
                ),
            )

        # Classify (one-shot, vendoring-time; refuses the VENDORING on
        # misclassification, never the runtime).
        try:
            classify(declaration, self.registry)
        except ClassificationRefusal as exc:
            return self._refuse(
                plugin=plugin,
                target_class=target_class,
                upstream_tag=effective_tag,
                code=REFUSAL_MISCLASSIFIED_AT_VENDORING,
                message=exc.message,
                location=f"classify:{exc.failed_step}",
            )

        # Resolve upstream repo.
        effective_repo = upstream_repo or declaration.upstream_repo
        if not effective_repo:
            # Fail-closed: no repo ⇒ tag unobservable ⇒ tag-missing
            # (CON §5 line 196 + review ruling); message keeps the cause.
            return self._refuse(
                plugin=plugin,
                target_class=target_class,
                upstream_tag=effective_tag,
                code=REFUSAL_TAG_MISSING_UPSTREAM,
                message=(
                    "upstream tag unobservable: upstream repo URL is empty "
                    "(manifest plugin.upstream.repo)"
                ),
                location=f"{plugin}/{MANIFEST_FILENAME}#upstream.repo",
            )

        # Open upstream git adapter.  Fail-closed (CON §5 line 196 +
        # review ruling): checkout absent / not a git repository ⇒
        # the tag is unobservable ⇒ refused as tag-missing; the
        # message names the actual cause.
        try:
            upstream = self._upstream_git_factory(effective_repo, effective_tag)
        except Exception as exc:  # noqa: BLE001 - adapter errors land here
            return self._refuse(
                plugin=plugin,
                target_class=target_class,
                upstream_tag=effective_tag,
                code=REFUSAL_TAG_MISSING_UPSTREAM,
                message=(
                    f"upstream tag unobservable (could not open upstream git: {exc}); "
                    "an unobservable tag is refused as tag-missing (fail-closed)"
                ),
                location=f"upstream({effective_repo})@{effective_tag}",
            )

        # Confirm tag is present at the remote.  Tag-missing or
        # force-pushed ⇒ refuse + alert.
        present = upstream.tag_present()
        if not present:
            return self._refuse(
                plugin=plugin,
                target_class=target_class,
                upstream_tag=effective_tag,
                code=REFUSAL_TAG_MISSING_UPSTREAM,
                message=(
                    f"upstream tag {effective_tag!r} is missing or has been force-pushed"
                ),
                location=f"upstream({effective_repo})@{effective_tag}",
            )

        # Resolve per-class paths from the manifest.  The manifest's
        # `paths` are LOCAL (operator-facing) subdirs.  The matching
        # UPSTREAM subdirs come from `upstream_paths` when present
        # (additive CON §8 1.0.x; required for the snapshot class
        # which renames prompts/daemon/ from apps/daemon/src/prompts/
        # and prompts/contracts/ from packages/contracts/src/prompts/).
        # When `upstream_paths` is absent, the sync-runner falls back
        # to the strip-class-prefix heuristic (matches the copy_freely
        # layout where local and upstream share the trailing path).
        class_section = getattr(declaration, target_class, {}) or {}
        class_paths = list(class_section.get("paths", []) or ())
        explicit_upstream_paths = declaration.upstream_paths_for_class(target_class)
        if explicit_upstream_paths is not None:
            upstream_paths = list(explicit_upstream_paths)
        else:
            upstream_paths = [
                p[len(target_class) + 1:] if p.startswith(target_class + "/") else p
                for p in class_paths
            ]
        if not class_paths:
            # No paths declared for the class — nothing to sync; report
            # no_change with empty diff.
            return SyncResult(
                plugin=plugin,
                target_class=target_class,
                upstream_tag=effective_tag,
                action="no_change",
                diff_summary=DiffSummary(),
                staleness_age_days=upstream.tag_age_days(self._clock()),
            )

        # Diff the manifest paths vs the local target directory.
        local_target = (plugin_dir or declaration.source_dir) / target_class
        if not local_target.is_dir():
            local_target.mkdir(parents=True, exist_ok=True)
        # Fail-closed (council ③ fix, 2026-10-06): the diff
        # helper now PROPAGATES ``UpstreamContentIncompleteError``
        # (a subdir list_tree error or a stage-path escape —
        # both signal the upstream content at the pinned tag is
        # unobservable / untrustworthy).  We catch it here and
        # refuse; the dry-run parity rule (a dry-run is a
        # truthful report of what the non-dry run would do) is
        # preserved by handling BOTH dry-run and non-dry paths
        # the same way.  Earlier code (pre-③-fix) would have
        # silently swallowed the error inside the helper,
        # produced a partial diff, and reported ``clean_pulled``
        # — the CRITICAL #2 silent-partial-data-loss illusion.
        try:
            diff = _compute_class_diff(
                upstream=upstream,
                upstream_tag=effective_tag,
                target_class=target_class,
                upstream_paths=upstream_paths,
                local_paths=class_paths,
                local_target=local_target,
                explicit_upstream_paths=explicit_upstream_paths,
            )
        except UpstreamContentIncompleteError as exc:
            return self._refuse(
                plugin=plugin,
                target_class=target_class,
                upstream_tag=effective_tag,
                code=REFUSAL_TAG_MISSING_UPSTREAM,
                message=str(exc),
                location=f"upstream({declaration.upstream_repo})@{effective_tag}",
            )

        # Stale-tag check (CON §5 staleness_age_days) — informational,
        # not a refusal; surfaces via the result for promote-gate use.
        staleness = upstream.tag_age_days(self._clock())

        # Empty diff → no_change.
        if diff.added == 0 and diff.modified == 0 and diff.removed == 0:
            return SyncResult(
                plugin=plugin,
                target_class=target_class,
                upstream_tag=effective_tag,
                action="no_change",
                diff_summary=DiffSummary(0, 0, 0),
                staleness_age_days=staleness,
            )

        # Drift-alarm path: the snapshot_with_drift_alarm class
        # always surfaces an alarm when the diff is non-empty (CON §5
        # alarm is "iff alarmed").  The divergence register is
        # updated; the existing entry is incremented if one already
        # exists, or a new entry is appended.
        if target_class == "snapshot_with_drift_alarm":
            new_entry_id = _next_register_id(declaration)
            entry = {
                "id": new_entry_id,
                "files": diff.example_files,
                "delta": (
                    f"upstream drift observed on {effective_tag}: "
                    f"+{diff.added}/~{diff.modified}/-{diff.removed}"
                ),
                "rationale": (
                    "Snapshot pulled at sync time; divergence registered; "
                    "re-apply or drop, update the log either way (CON §2)"
                ),
                "pinning_test": (
                    f"tests/unit/plugin_subsystem/test_sync_runner.py::"
                    f"TestSyncSnapshotDrift::test_drift_alarm_at_{target_class}"
                ),
            }
            if not dry_run:
                _append_divergence_register(
                    plugin_dir=plugin_dir or declaration.source_dir,
                    new_entry=entry,
                )
            alarm = DriftAlarm(divergence_register_entry=entry)
            # Even with the alarm, dry-run is the default; clean_pull
            # of the bytes still happens iff not dry-run.
            if dry_run:
                return SyncResult(
                    plugin=plugin,
                    target_class=target_class,
                    upstream_tag=effective_tag,
                    action="alarmed",
                    diff_summary=DiffSummary(diff.added, diff.modified, diff.removed),
                    alarm=alarm,
                    staleness_age_days=staleness,
                )
            # Not dry-run: pull + alarm.
            # Fail-closed (council ③ fix, 2026-10-06): the
            # pull helper now propagates
            # ``UpstreamContentIncompleteError`` (a subdir
            # list_tree error, a blob cat_file_blob error, or
            # a stage-path escape).  We catch it here and
            # refuse — the staged temp dir is discarded by
            # the helper's own cleanup branch and the live
            # tree is unchanged (whole-or-nothing).
            try:
                _clean_pull_atomic(
                    upstream=upstream,
                    upstream_tag=effective_tag,
                    target_class=target_class,
                    upstream_paths=upstream_paths,
                    local_paths=class_paths,
                    local_target=local_target,
                    explicit_upstream_paths=explicit_upstream_paths,
                )
            except UpstreamContentIncompleteError as exc:
                return self._refuse(
                    plugin=plugin,
                    target_class=target_class,
                    upstream_tag=effective_tag,
                    code=REFUSAL_TAG_MISSING_UPSTREAM,
                    message=str(exc),
                    location=f"upstream({declaration.upstream_repo})@{effective_tag}",
                )
            emit_drift_event(plugin, target_class, entry, effective_tag)
            return SyncResult(
                plugin=plugin,
                target_class=target_class,
                upstream_tag=effective_tag,
                action="alarmed",
                diff_summary=DiffSummary(diff.added, diff.modified, diff.removed),
                alarm=alarm,
                staleness_age_days=staleness,
            )

        # copy_freely path: dry-run is the default.
        if dry_run:
            return SyncResult(
                plugin=plugin,
                target_class=target_class,
                upstream_tag=effective_tag,
                action="clean_pulled",
                diff_summary=DiffSummary(diff.added, diff.modified, diff.removed),
                staleness_age_days=staleness,
            )
        # Real pull: atomic stage + rename.
        # Fail-closed (council ③ fix, 2026-10-06): same
        # treatment as the snapshot-class pull above — any
        # upstream-content-incomplete condition is converted
        # to a refused SyncResult.  The helper's rename-aside
        # atomicity (W3 fix) preserves the live tree on
        # failure.
        try:
            _clean_pull_atomic(
                upstream=upstream,
                upstream_tag=effective_tag,
                target_class=target_class,
                upstream_paths=upstream_paths,
                local_paths=class_paths,
                local_target=local_target,
                explicit_upstream_paths=explicit_upstream_paths,
            )
        except UpstreamContentIncompleteError as exc:
            return self._refuse(
                plugin=plugin,
                target_class=target_class,
                upstream_tag=effective_tag,
                code=REFUSAL_TAG_MISSING_UPSTREAM,
                message=str(exc),
                location=f"upstream({declaration.upstream_repo})@{effective_tag}",
            )
        return SyncResult(
            plugin=plugin,
            target_class=target_class,
            upstream_tag=effective_tag,
            action="clean_pulled",
            diff_summary=DiffSummary(diff.added, diff.modified, diff.removed),
            staleness_age_days=staleness,
        )

    # -- helpers ---------------------------------------------------------------

    def _read_declaration(
        self, plugin: str, plugin_dir: Optional[Path]
    ) -> Optional[PluginDeclaration]:
        if plugin_dir is None:
            # plugin_dir is required by CON §5 (the manifest is
            # the source of truth and must be re-read fresh every
            # call).  Without plugin_dir we cannot read the
            # manifest; map this to a typed refusal rather than
            # raising — keeps the public API's "always returns
            # SyncResult" contract.
            return None  # caller handles None
        # Re-read FRESH every call (CON §5: no cache).
        try:
            validation = validate_manifest(plugin_dir, validate_tree=True)
        except Exception as exc:  # noqa: BLE001 - defensive
            # Any unexpected error (manifest_unreadable etc.)
            # surfaces as a refused SyncResult.
            raise SyncRefusal_or_RuntimeError(
                f"manifest reader raised unexpectedly: {exc}"
            )
        if validation.refusal is not None:
            # Reader refusals map to sync-runner refusal codes
            # (CON §5 closed enum).  Reader codes that overlap
            # with the sync-runner enum are propagated verbatim;
            # reader-only codes are propagated too (the
            # integration surface owns the full set).
            raise ManifestReaderError(validation.refusal)
        assert validation.declaration is not None  # noqa: S101 - invariant of ok=True
        return validation.declaration

    def _refuse(
        self,
        *,
        plugin: str,
        target_class: str,
        upstream_tag: str,
        code: str,
        message: str,
        location: str,
    ) -> SyncResult:
        return SyncResult(
            plugin=plugin,
            target_class=target_class,
            upstream_tag=upstream_tag,
            action="refused",
            diff_summary=DiffSummary(),
            refusal=SyncRefusal(code=code, message=message, location=location),
        )


# Exception alias — keeps the helper signatures short without
# introducing a module-level alias for Exception.
SyncRefusal_or_RuntimeError = RuntimeError


class ManifestReaderError(Exception):
    """Internal carrier for ``ManifestRefusal`` from the slice ① reader.

    The sync-runner converts the reader's typed refusal into a
    ``SyncResult`` with a matching ``SyncRefusal`` (so the public
    API is "always returns a SyncResult, never raises on a
    documented refusal").  This exception is internal; callers
    of the sync-runner never see it.
    """

    def __init__(self, refusal: Any) -> None:
        self.refusal = refusal
        super().__init__(str(refusal))


class UpstreamContentIncompleteError(RuntimeError):
    """Internal carrier for "upstream content at the pinned tag is
    unobservable" (subdir tree listing failed, blob fetch failed,
    or the upstream ref's blob is missing/unreadable).

    This is a ``RuntimeError`` subclass so the existing generic
    ``except RuntimeError`` safety nets in callers do NOT fire on
    it (those nets are reserved for *expected* operational
    failures like a missing tag or unopenable checkout — the
    callers convert them to ``tag_missing_upstream`` refusals;
    they must NOT swallow a content-incomplete error silently).

    The :meth:`SyncRunner.sync` entry point catches this
    exception and converts it to a refused ``SyncResult`` with
    code ``tag_missing_upstream`` and a cause-bearing message
    naming the failing path or blob.  The exception is
    INTERNAL — callers of the public API never see it raised.
    """


# ─── upstream-git adapter ─────────────────────────────────────────────────────


class UpstreamGit:
    """Abstract adapter: read-only view of an upstream git ref.

    Implementations:

    - :class:`LocalGitCheckout` — wraps a local ``.git`` checkout
      (the slice ③ default; uses ``git ls-remote`` + ``git
      cat-file`` against the local repo).  Suitable for both
      network-disconnected (offline) and online use.
    """

    def tag_present(self) -> bool:
        raise NotImplementedError

    def list_tree(self, subdir: str) -> List[Tuple[str, str, str, int]]:
        """Return ``(mode, type, blob_sha, size, path)`` rows for files
        under ``<ref>:<subdir>/`` (mirrors ``tools/vendor/od_vendor.py``)."""
        raise NotImplementedError

    def cat_file_blob(self, blob_sha: str) -> bytes:
        raise NotImplementedError

    def tag_commit_date(self) -> datetime:
        """Return the tag's underlying commit date (UTC, tz-aware)."""
        raise NotImplementedError

    def tag_age_days(self, now: datetime) -> int:
        delta = now - self.tag_commit_date()
        return max(0, delta.days)


class LocalGitCheckout(UpstreamGit):
    """Adapter for a local git checkout used as the upstream source.

    The slice ③ default.  The "remote" is the local repo; ``git
    ls-remote`` against a local checkout works the same as against a
    network remote (it returns the refs the local repo knows).  The
    ``tag_present`` check uses ``git rev-parse <tag>^{tag}`` — the
    peel-to-tag operator — so a force-pushed tag whose commit is the
    same as the previous one still resolves (the operator
    distinguishes tag identity from commit identity; a deleted tag
    raises).
    """

    def __init__(self, repo_url: str, tag: str) -> None:
        self.repo_url = repo_url
        self.tag = tag
        self._source = self._resolve_local_source(repo_url)

    @staticmethod
    def _resolve_local_source(repo_url: str) -> Path:
        # Slice ③: the only upstream source we support is a local
        # checkout (the slice ② dispatch authorizes
        # ``/home/nea/opt/open-design`` as the working upstream for
        # the dry-run).  A URL-shaped source is refused up-front so a
        # network round-trip is never attempted silently.
        p = Path(repo_url)
        if not p.is_dir():
            raise RuntimeError(
                f"upstream repo is not a local directory: {repo_url} "
                "(slice ③ LocalGitCheckout only; HTTP adapter lands later if needed)"
            )
        if not (p / ".git").exists():
            raise RuntimeError(f"upstream is not a git checkout: {repo_url}")
        return p

    def _run(self, *args: str) -> str:
        cmd = ["git", "-C", str(self._source)] + list(args)
        try:
            result = subprocess.run(cmd, capture_output=True, check=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"git {' '.join(args)} failed: {exc.stderr.decode('utf-8', errors='replace').strip()}"
            ) from exc
        return result.stdout.decode("utf-8", errors="replace")

    def tag_present(self) -> bool:
        # Two-stage check that handles BOTH annotated and lightweight
        # tags.  ``git rev-parse <tag>^{tag}`` peels an annotated tag
        # to its tag object; a missing annotated tag raises.  A
        # lightweight tag has no tag object — ``^{tag}`` is invalid
        # for it — so we fall back to ``<tag>^{}`` (commit) or
        # ``<tag>`` directly.  The fallback chain is explicit so a
        # future reflog/playground oddity can be diagnosed via the
        # captured error rather than a silent miss.
        try:
            self._run("rev-parse", f"{self.tag}^{{tag}}")
            return True
        except RuntimeError:
            pass
        # Lightweight tag (or branch name — refused earlier by the
        # offline manifest reader, but we still probe defensively).
        try:
            self._run("rev-parse", f"{self.tag}^{{}}")
            return True
        except RuntimeError:
            return False

    def _ref_for_listing(self) -> str:
        """The ref to pass to ``git ls-tree`` for this tag.

        Annotated tags: the peeled commit hash.  Lightweight tags:
        the commit-ish directly.  Force-annotated: the
        ``<tag>^{tag}`` peel.
        """
        try:
            return self._run("rev-parse", f"{self.tag}^{{tag}}").strip()
        except RuntimeError:
            pass
        try:
            return self._run("rev-parse", f"{self.tag}^{{}}").strip()
        except RuntimeError:
            return self.tag

    def list_tree(self, subdir: str) -> List[Tuple[str, str, str, int, str]]:
        # Mirrors ``tools/vendor/od_vendor.py`` size-on-tree
        # strategy (``git ls-tree -l -r``) — no per-file forks.
        #
        # Fail-closed (council ③ fix, 2026-10-06): a RuntimeError
        # from ``_run`` here means the upstream SUB-DIRECTORY at
        # the pinned tag is unobservable (e.g. the path doesn't
        # exist in the upstream tree, or ``git ls-tree`` itself
        # fails).  Re-raise as ``UpstreamContentIncompleteError``
        # so the sync-runner aborts the diff or the pull rather
        # than silently skipping the subdir and reporting a
        # partial diff / swapping in a partial tree.
        #
        # Dry-run parity (council ③ fix, 2026-10-06): after
        # listing, the diff path does NOT call ``cat_file_blob``
        # (the comment at the diff site documents the
        # "no-per-blob-forks" optimization).  A missing blob
        # would therefore NOT be detected by the diff alone,
        # violating the "dry-run is a truthful report of what
        # the non-dry run would do" rule.  We close that gap
        # here with a single ``git cat-file --batch-check``
        # call (streaming protocol: O(1) subprocess, O(N) SHA
        # parse) that asserts every SHA in the listing is
        # present in the upstream object store.  Any "missing"
        # answer raises ``UpstreamContentIncompleteError`` —
        # the same refusal both diff and pull would otherwise
        # see, so dry-run and non-dry are parity-clean.
        ref = self._ref_for_listing()
        try:
            raw = self._run("ls-tree", "-l", "-r", ref, "--", f"{subdir}/")
        except RuntimeError as exc:
            raise UpstreamContentIncompleteError(
                f"upstream tag content incomplete: subdir {subdir!r} "
                f"unreadable at {self.tag!r} ({exc})"
            ) from exc
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
        # Dry-run parity check: assert every SHA in the listing
        # is present in the object store.  Skipped if the
        # listing is empty (no blobs to check).
        if out:
            self._assert_blobs_present([sha for _m, _t, sha, _s, _p in out])
        return out

    def _assert_blobs_present(self, shas: Sequence[str]) -> None:
        """Batch-verify every SHA exists in the upstream object
        store (dry-run parity gate, council ③ fix 2026-10-06).

        Uses ``git cat-file --batch-check``: a streaming protocol
        that takes SHAs from stdin and writes ``<sha> <type> <size>``
        to stdout.  Cost is one subprocess call regardless of
        SHA count; missing objects produce a ``missing`` line
        (vs ``blob`` for present blobs).  Raises
        :class:`UpstreamContentIncompleteError` on the first
        missing SHA.
        """
        if not shas:
            return
        try:
            proc = subprocess.run(
                ["git", "-C", str(self._source), "cat-file", "--batch-check"],
                input=("\n".join(shas) + "\n").encode("utf-8"),
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise UpstreamContentIncompleteError(
                f"upstream tag content incomplete: cat-file --batch-check "
                f"failed at {self.tag!r} ({(exc.stderr or b'').decode('utf-8', errors='replace').strip() or exc})"
            ) from exc
        # Parse the batch output; the first line of stdout that
        # is "missing" (or any line whose type column is not
        # "blob") indicates a content-incomplete condition.
        for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            sha, obj_type = parts[0], parts[1]
            if obj_type != "blob":
                raise UpstreamContentIncompleteError(
                    f"upstream tag content incomplete: blob {sha!r} "
                    f"missing or unreadable at {self.tag!r} (cat-file --batch-check: {line})"
                )

    def cat_file_blob(self, blob_sha: str) -> bytes:
        # Fail-closed (council ③ fix, 2026-10-06): a missing or
        # unreadable BLOB at the pinned tag is the same family as
        # an unobservable subdir (above) — the upstream content
        # is incomplete.  Re-raise as ``UpstreamContentIncompleteError``
        # so the sync-runner aborts the pull rather than silently
        # skipping the blob and swapping in a partial tree.
        try:
            result = subprocess.run(
                ["git", "-C", str(self._source), "cat-file", "blob", blob_sha],
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
            raise UpstreamContentIncompleteError(
                f"upstream tag content incomplete: blob {blob_sha!r} "
                f"unreadable at {self.tag!r} ({stderr or exc})"
            ) from exc
        return result.stdout

    def tag_commit_date(self) -> datetime:
        ref = self._ref_for_listing()
        raw = self._run("log", "-1", "--format=%cI", f"{ref}^{{}}").strip()
        # ``%cI`` = ISO 8601 strict, e.g. ``2026-10-02T12:34:56+02:00``.
        return datetime.fromisoformat(raw)


# ─── internal helpers ─────────────────────────────────────────────────────────


@dataclass
class _ClassDiff:
    added: int
    modified: int
    removed: int
    example_files: List[str] = field(default_factory=list)


def _safe_join_under(base: Path, relpath: str) -> Path:
    """Resolve ``relpath`` under ``base`` and refuse any traversal escape."""
    target = (base / relpath).resolve()
    base_resolved = base.resolve()
    try:
        target.relative_to(base_resolved)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"path {relpath!r} escapes dest {base_resolved}") from exc
    return target


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_blob_sha1(data: bytes) -> str:
    """Compute the git blob SHA-1 for the given raw bytes.

    Mirrors ``git hash-object``: SHA-1 over the header
    ``blob {len}\\0`` prepended to the data.  Used to compare a
    local file against the upstream ``git ls-tree`` blob SHA-1
    without spawning ``git cat-file`` per file (the slow path).
    """
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def _compute_class_diff(
    *,
    upstream: UpstreamGit,
    upstream_tag: str,
    target_class: str,
    upstream_paths: Sequence[str],
    local_paths: Sequence[str],
    local_target: Path,
    explicit_upstream_paths: Optional[Sequence[str]] = None,
) -> _ClassDiff:
    """Compare upstream tag vs local target directory.

    Walks every upstream path that matches one of the manifest's
    ``upstream_paths`` entries, hashes its bytes, and compares to
    the local file's hash under the corresponding ``local_paths``
    entry (parallel arrays).  Returns the (added, modified,
    removed) counts plus a small sample of changed file paths for
    the alarm payload.
    """
    if len(upstream_paths) != len(local_paths):
        raise ValueError(
            f"upstream_paths ({len(upstream_paths)}) and local_paths "
            f"({len(local_paths)}) must have the same length"
        )
    upstream_files: Dict[str, str] = {}  # local_rel (under local_target) → blob_sha
    for upstream_subdir, local_path_root in zip(upstream_paths, local_paths):
        upstream_subdir = upstream_subdir.rstrip("/")
        local_path_root = local_path_root.rstrip("/")
        # Strip the target_class prefix (which is implicit in
        # local_target) so the local relpath is the trailing
        # portion of the local path root (e.g. "design-systems/"
        # for the copy_freely class).
        if local_path_root == target_class:
            relative_root = ""
        elif local_path_root.startswith(target_class + "/"):
            relative_root = local_path_root[len(target_class) + 1:]
        else:
            relative_root = local_path_root
        # Fail-closed (council ③ fix, 2026-10-06): a RuntimeError
        # from ``list_tree`` here is now an
        # ``UpstreamContentIncompleteError`` (the ``list_tree``
        # adapter re-raises it) — the upstream subdir at the
        # pinned tag is unobservable.  We PROPAGATE (no silent
        # skip): a partial diff that swallows a subdir would
        # understate the file count and let a partial pull pass
        # the dry-run check while leaving files missing on disk.
        # ``UpstreamContentIncompleteError`` is a ``RuntimeError``
        # subclass; a bare ``except RuntimeError: continue`` here
        # would catch it and re-introduce the bug.  We catch ONLY
        # the upgradable RuntimeError from older callers, and let
        # the new content-incomplete error propagate.
        try:
            entries = upstream.list_tree(upstream_subdir)
        except UpstreamContentIncompleteError:
            # Re-raise verbatim; the sync() entry point catches
            # this and refuses with tag_missing_upstream.
            raise
        except RuntimeError as exc:
            # Backstop for non-upgraded adapters (or for an
            # unanticipated RuntimeError source): fail-closed by
            # re-raising as the same content-incomplete family
            # rather than silently skipping.  The message names
            # the cause for operator diagnostics.
            raise UpstreamContentIncompleteError(
                f"upstream tag content incomplete: subdir {upstream_subdir!r} "
                f"unreadable at {upstream_tag!r} ({exc})"
            ) from exc
        # Layout policy:
        #
        # When `upstream_paths` is DECLARED (the snapshot class
        # case), the local class subtree is RENAMED relative to
        # upstream (e.g. apps/daemon/src/prompts/X.ts →
        # prompts/daemon/X.ts).  The local layout is FLAT — each
        # upstream file is placed at
        # ``<relative_root>/<upstream_file_basename>``.  This is
        # what the slice ③ od_vendor_snapshot.py vendoring tool
        # does (matches the slice ③ audit layout: the prompts/
        # subtree has one level of file names, no upstream
        # internal hierarchy).
        #
        # When `upstream_paths` is ABSENT (the copy_freely case),
        # the local class subtree SHARES the upstream subdir
        # name (copy_freely/design-systems/ vs design-systems/),
        # so the local layout PRESERVES the upstream tree's
        # internal hierarchy (e.g. design-systems/airbnb/manifest.json
        # → copy_freely/design-systems/airbnb/manifest.json).
        is_renamed = explicit_upstream_paths is not None
        for mode, _obj_type, blob_sha, _size, upstream_file_path in entries:
            if mode == "120000":
                continue
            if is_renamed:
                # Flat: drop the upstream subdir prefix, keep the
                # basename.  For files at the upstream subdir root
                # (``apps/daemon/src/prompts/core-slim.ts``) this
                # is just the basename; for files in a subdir
                # (``apps/daemon/src/prompts/sub/file.ts``) we keep
                # the relative-from-upstream-subdir path
                # (``sub/file.ts``) to avoid name collisions while
                # still dropping the upstream subdir prefix.
                if upstream_file_path.startswith(upstream_subdir + "/"):
                    subpath = upstream_file_path[len(upstream_subdir) + 1:]
                else:
                    subpath = upstream_file_path
                if relative_root:
                    local_rel = f"{relative_root}/{subpath}"
                else:
                    local_rel = subpath
            else:
                # Preserve hierarchy: the local file is at
                # ``<local_target>/<relative_root>/<upstream_subpath>``
                # where upstream_subpath is the upstream file path
                # under the upstream subdir.
                if upstream_file_path.startswith(upstream_subdir + "/"):
                    upstream_subpath = upstream_file_path[len(upstream_subdir) + 1:]
                else:
                    upstream_subpath = upstream_file_path
                if relative_root:
                    local_rel = f"{relative_root}/{upstream_subpath}"
                else:
                    local_rel = upstream_subpath
            upstream_files[local_rel] = blob_sha

    added = 0
    modified = 0
    removed = 0
    examples: List[str] = []

    # Added / modified (upstream has; we may or may not).
    # Compare the local file's git blob SHA-1 (computed in
    # Python: SHA-1 over "blob {size}\\0" + raw bytes) against
    # the upstream blob SHA-1 from `git ls-tree`.  Both are the
    # same canonical hash, so byte-identical vendoring matches
    # for free; modified files differ; we never have to read
    # the upstream blob bytes during the diff (the slow part
    # of the original implementation).  This is the dry-run-
    # performance path the slice ③ real-plugin test needs
    # (4881-file copy_freely diff completes in <1s).
    for rel, blob_sha in upstream_files.items():
        try:
            local_path = _safe_join_under(local_target, rel)
        except ValueError as exc:
            # Fail-closed (council ③ fix, 2026-10-06): the
            # ``rel`` is constructed from upstream's tree path
            # (we strip the upstream subdir prefix and join to
            # the local root).  A path that ESCAPES the local
            # target means upstream's content layout is bad
            # (e.g. a path with traversal segments).  Earlier
            # code swallowed this with ``continue`` — a silent
            # skip that would understate the diff and let a
            # pathological pull pass dry-run while leaving
            # files unhandled.  Re-raise as content-incomplete
            # so sync() refuses.
            raise UpstreamContentIncompleteError(
                f"upstream tag content incomplete: file path {rel!r} "
                f"escapes local target {local_target} ({exc})"
            ) from exc
        if not local_path.is_file():
            added += 1
            examples.append(rel)
            continue
        try:
            local_bytes = local_path.read_bytes()
        except OSError:
            # A local file we cannot read (permissions,
            # vanished mid-walk) is treated as "different" for
            # diff purposes: the upstream version will need to
            # be written.  This is NOT a content-incomplete
            # condition (the upstream is intact) — it's a
            # transient local-IO problem that the next pull
            # will resolve.  Council ③ review: this exception
            # is documented and judged acceptable (NOT a
            # silent-skip; the "added" counting is the
            # truthful diff for the operator).
            added += 1
            examples.append(rel)
            continue
        local_blob_sha = _git_blob_sha1(local_bytes)
        if local_blob_sha != blob_sha:
            modified += 1
            if len(examples) < 10:
                examples.append(rel)

    # Removed (local has; upstream does not) — only files that
    # belong to the manifest's class_paths.  We walk the local
    # target with a bounded recursion and skip LOCALLY_OWNED
    # filenames (CON §1 + slice ③ resolution).
    local_seen: set = set()
    for local_path_root in local_paths:
        local_path_root = local_path_root.rstrip("/")
        if local_path_root == target_class:
            relative_root = ""
        elif local_path_root.startswith(target_class + "/"):
            relative_root = local_path_root[len(target_class) + 1:]
        else:
            relative_root = local_path_root
        try:
            local_root = _safe_join_under(local_target, relative_root) if relative_root else local_target
        except ValueError as exc:
            # Fail-closed (council ③ fix, 2026-10-06): the
            # ``local_path_root`` comes from the manifest's
            # ``class_paths`` (operator-authored).  A path
            # that escapes the local target is a manifest
            # bug, but the truthful fail-closed response is
            # to refuse rather than silently understate the
            # removed count.  Re-raise as content-incomplete
            # (the operator-facing surface is the same:
            # refuse, don't continue with a partial diff).
            raise UpstreamContentIncompleteError(
                f"upstream tag content incomplete: manifest local path "
                f"{local_path_root!r} escapes local target {local_target} ({exc})"
            ) from exc
        if not local_root.is_dir():
            continue
        for path in local_root.rglob("*"):
            if not path.is_file():
                continue
            if path.name in LOCALLY_OWNED_FILENAMES:
                continue
            try:
                rel = path.relative_to(local_target).as_posix()
            except ValueError as exc:
                # Defensive: ``path`` came from
                # ``local_root.rglob()`` and ``local_root``
                # was resolved under ``local_target``, so a
                # path that is NOT under ``local_target`` is
                # truly impossible.  Council ③ review: re-
                # raise as content-incomplete (fail-closed)
                # rather than silent-skip — preserves the
                # invariant that we never understate the
                # diff.
                raise UpstreamContentIncompleteError(
                    f"upstream tag content incomplete: local walk path "
                    f"{path!r} escapes local target {local_target} ({exc})"
                ) from exc
            local_seen.add(rel)
            if rel not in upstream_files:
                removed += 1
                if len(examples) < 10:
                    examples.append(rel)
    return _ClassDiff(added=added, modified=modified, removed=removed, example_files=examples)


def _clean_pull_atomic(
    *,
    upstream: UpstreamGit,
    upstream_tag: str,
    target_class: str,
    upstream_paths: Sequence[str],
    local_paths: Sequence[str],
    local_target: Path,
    explicit_upstream_paths: Optional[Sequence[str]] = None,
) -> None:
    """Stage a copy-freely pull into a sibling temp dir + atomic rename.

    Mid-pull crash leaves ``local_target`` unchanged (whole-or-nothing).
    The locally-owned files (HASHES.sha256 et al.) are PRESERVED
    across the rename: we copy them from the existing ``local_target``
    into the staging dir before the rename, so the move preserves
    them.

    The ``upstream_paths`` and ``local_paths`` are parallel arrays
    (the manifest's ``paths`` and the optional ``upstream_paths``
    additive field).  For each pair, the upstream tree is read
    and the local target is the corresponding local root; the
    vendored layout flattens to a single-level directory under
    the local root (the upstream tree's internal hierarchy is
    collapsed — matches the slice ②/③ vendoring tool's behavior).

    NOTE: on rename, the existing ``local_target`` is REPLACED; any
    files inside the staging dir that DID exist in the old
    ``local_target`` AND are NOT in the upstream set are dropped (the
    upstream tag is the new ground truth).  This is the
    ``copy_freely`` class's contract: "clean-pulled, never authored
    locally" (CON §2).
    """
    if len(upstream_paths) != len(local_paths):
        raise ValueError(
            f"upstream_paths ({len(upstream_paths)}) and local_paths "
            f"({len(local_paths)}) must have the same length"
        )
    parent = local_target.parent
    pid = os.getpid()
    # Use a stable, single-temp pattern (per-call pid) so concurrent
    # syncs don't clobber each other.  ``mkdtemp`` is atomic at the
    # filesystem level.
    stage_dir = Path(tempfile.mkdtemp(prefix=f".sync_stage.{pid}.", dir=str(parent)))
    # ``aside_dir`` is set in the rename-aside sequence below (W3
    # fix, 2026-10-06 — closes the rmtree→replace window where
    # ``local_target`` is briefly absent).  None while the
    # existing-target is being staged, set once the old tree is
    # moved aside atomically.
    aside_dir: Optional[Path] = None
    try:
        # 1) Copy upstream files into stage.
        #
        # Fail-closed (council ③ fix, 2026-10-06): a subdir listing
        # error, a blob fetch error, or a stage-path traversal
        # escape here is the upstream content being unobservable /
        # untrustworthy at the pinned tag.  Earlier code swallowed
        # each of these with ``continue`` — a partial stage would
        # then be renamed over ``local_target`` (CRITICAL #2:
        # silent partial-data-loss illusion).  We PROPAGATE
        # (UpstreamContentIncompleteError re-raises from the
        # adapter and is caught by sync()'s refusal path; the
        # ``except (subprocess.CalledProcessError, RuntimeError)``
        # that was here would have caught the new
        # UpstreamContentIncompleteError — we removed it to fix
        # the silent-skip).
        for upstream_subdir, local_path_root in zip(upstream_paths, local_paths):
            upstream_subdir = upstream_subdir.rstrip("/")
            local_path_root = local_path_root.rstrip("/")
            # Strip the target_class prefix (which is implicit in
            # local_target) so the stage path is under
            # ``<stage>/<relative_root>/<subpath>``.
            if local_path_root == target_class:
                relative_root = ""
            elif local_path_root.startswith(target_class + "/"):
                relative_root = local_path_root[len(target_class) + 1:]
            else:
                relative_root = local_path_root
            try:
                entries = upstream.list_tree(upstream_subdir)
            except UpstreamContentIncompleteError:
                # Re-raise verbatim; sync() catches and refuses.
                raise
            except RuntimeError as exc:
                # Backstop for non-upgraded adapters: refuse
                # fail-closed rather than silently skipping the
                # subdir.
                raise UpstreamContentIncompleteError(
                    f"upstream tag content incomplete: subdir {upstream_subdir!r} "
                    f"unreadable at {upstream_tag!r} ({exc})"
                ) from exc
            is_renamed = explicit_upstream_paths is not None
            for mode, _obj_type, blob_sha, _size, upstream_file_path in entries:
                if mode == "120000":
                    continue
                if upstream_file_path.startswith(upstream_subdir + "/"):
                    subpath = upstream_file_path[len(upstream_subdir) + 1:]
                else:
                    subpath = upstream_file_path
                if relative_root:
                    stage_rel = f"{relative_root}/{subpath}"
                else:
                    stage_rel = subpath
                try:
                    stage_path = _safe_join_under(stage_dir, stage_rel)
                except ValueError as exc:
                    # Fail-closed: an upstream-derived stage
                    # path that escapes the staging dir is
                    # pathological.  Earlier code skipped with
                    # ``continue``; we refuse instead.
                    raise UpstreamContentIncompleteError(
                        f"upstream tag content incomplete: file path "
                        f"{stage_rel!r} escapes stage dir {stage_dir} ({exc})"
                    ) from exc
                stage_path.parent.mkdir(parents=True, exist_ok=True)
                # ``cat_file_blob`` re-raises
                # ``UpstreamContentIncompleteError`` for
                # ``subprocess.CalledProcessError``; we let it
                # propagate (the bare ``except (...)`` that
                # silently swallowed it is GONE).
                data = upstream.cat_file_blob(blob_sha)
                with open(stage_path, "wb") as fh:
                    fh.write(data)
        # 2) Preserve locally-owned files (HASHES.sha256, ...) by
        #    copying them from the existing local_target into stage.
        if local_target.is_dir():
            for name in LOCALLY_OWNED_FILENAMES:
                src = local_target / name
                if src.is_file():
                    shutil.copy2(src, stage_dir / name)
        # 3) Replace the existing target with the staged tree
        #    using the rename-aside pattern (W3 fix,
        #    2026-10-06).
        #
        # Earlier code did ``shutil.rmtree(local_target)`` then
        # ``os.replace(stage_dir, local_target)`` — leaving a
        # brief window where ``local_target`` did not exist on
        # disk.  A crash in that window (or an external process
        # reading the target) would observe a missing tree.
        # The new sequence is:
        #
        #   a) ``os.replace(local_target, aside_dir)`` — ATOMIC
        #      on POSIX, never absent; if it fails, ``local_target``
        #      is unchanged and we propagate the failure.
        #   b) ``os.replace(stage_dir, local_target)`` — ATOMIC;
        #      if it fails, we restore ``aside_dir → local_target``
        #      so the operator sees the old tree.
        #   c) success path: rmtree ``aside_dir``.
        #
        # POSIX guarantees ``rename(2)`` is atomic on the same
        # filesystem; the stage dir is created in ``parent`` (the
        # same parent as ``local_target``) so this holds.
        if local_target.exists():
            aside_dir = local_target.parent / (
                f".{local_target.name}.sync_aside.{pid}"
            )
            os.replace(local_target, aside_dir)
        try:
            os.replace(stage_dir, local_target)
        except Exception:
            # Restore aside → target so the operator's tree
            # is intact.  Best-effort: if this restore
            # itself fails (filesystem-level catastrophe),
            # the original target is lost — but the
            # earlier rm+replace had the same risk profile
            # (the data is just in a different name), and
            # the operator can re-run the sync against the
            # stage dir if the rmtree cleanup below left
            # it.  Stage cleanup below.
            if aside_dir is not None and aside_dir.is_dir() and not local_target.exists():
                try:
                    os.replace(aside_dir, local_target)
                except Exception:  # pragma: no cover - best-effort
                    pass
            raise
        # Success path — clean up the aside.
        if aside_dir is not None:
            shutil.rmtree(aside_dir, ignore_errors=True)
            aside_dir = None
    except Exception:
        # On any failure, attempt to remove the stage dir (best
        # effort) so it doesn't accumulate.  If the
        # rename-aside sequence already ran (aside_dir is set
        # and target is restored) we still try to remove the
        # aside; the success path is unreachable from this
        # except branch because the success path's os.replace
        # raised and the restore happened inside its inner
        # try/except — by the time we reach THIS except,
        # aside_dir has been consumed (restored to target).
        try:
            if stage_dir.is_dir():
                shutil.rmtree(stage_dir, ignore_errors=True)
        except Exception:  # pragma: no cover - cleanup
            pass
        raise


def _next_register_id(declaration: PluginDeclaration) -> int:
    """Compute the next divergence-register id (1-indexed, monotonic)."""
    register = list(declaration.divergence_register or ())
    if not register:
        return 1
    return 1 + max(int(e.get("id", 0)) for e in register)


def _append_divergence_register(*, plugin_dir: Path, new_entry: Mapping[str, Any]) -> None:
    """Append a divergence-register entry to the plugin's MANIFEST.yaml
    (in place).  Uses a temp + rename so the manifest is never torn.
    """
    manifest_path = plugin_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise RuntimeError(f"manifest not found for divergence-register append: {manifest_path}")
    import yaml  # local import; the rest of the module is yaml-free at runtime

    with open(manifest_path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    section = doc.setdefault("snapshot_with_drift_alarm", {})
    register = section.setdefault("divergence_register", [])
    register.append(dict(new_entry))
    fd, tmp_name = tempfile.mkstemp(prefix=".MANIFEST.", dir=str(plugin_dir))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with open(tmp_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh, sort_keys=False)
        os.replace(tmp_path, manifest_path)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


# ─── drift-event emission (REC §9 trigger-engine probe) ───────────────────────


def emit_drift_event(
    plugin: str,
    target_class: str,
    entry: Mapping[str, Any],
    observed_tag: str,
) -> None:
    """Emit a drift event for slice ⑥ to consume.

    **Probe decision (slice ③/⑥ OQ per REC §9):** the payload is
    emitted in CON §5's VERBATIM shape — ``{plugin, class,
    divergence_id, files, delta, rationale, pinning_test, observed_at,
    observed_tag}`` — keyed on the divergence_id.  The trigger
    engine is rule-based and does not currently accept ad-hoc
    events; slice ⑥ will either (a) add a new ``condition_type`` to
    :mod:`daemon.services.skill_trigger_seed` and have the engine
    treat each emitted drift entry as a candidate against that
    condition, OR (b) land a dedicated drift-event store the
    resolver polls.  No trigger-engine EDIT happens in ③.

    For ③ this function is a NO-OP STUB that records the event
    shape in the call site (via a dedicated log line) so the slice
    ⑥ wire-up can be the smallest possible change.  Tests assert
    the payload shape via :func:`build_drift_event_payload`.
    """
    payload = build_drift_event_payload(plugin, target_class, entry, observed_tag)
    import logging
    logging.getLogger(__name__).info(
        "drift_event_emitted (slice ③ stub; slice ⑥ wires the trigger engine): %s",
        payload,
    )


def build_drift_event_payload(
    plugin: str,
    target_class: str,
    entry: Mapping[str, Any],
    observed_tag: str,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build the CON §5 verbatim drift-event payload.

    Used by :func:`emit_drift_event` and by tests that pin the
    payload shape.  The ``observed_at`` is UTC ISO 8601 (the
    trigger engine's row format).
    """
    when = now or datetime.now(timezone.utc)
    return {
        "plugin": plugin,
        "class": target_class,
        "divergence_id": int(entry.get("id", 0)),
        "files": list(entry.get("files", [])),
        "delta": str(entry.get("delta", "")),
        "rationale": str(entry.get("rationale", "")),
        "pinning_test": str(entry.get("pinning_test", "")),
        "observed_at": when.isoformat(),
        "observed_tag": observed_tag,
    }


# ─── module-level convenience ─────────────────────────────────────────────────


def sync(
    plugin: str,
    target_class: str,
    *,
    upstream_repo: Optional[str] = None,
    upstream_tag: Optional[str] = None,
    plugin_dir: Optional[Path] = None,
    dry_run: bool = True,
    runner: Optional[SyncRunner] = None,
) -> SyncResult:
    """Module-level convenience (CON §5 entry point)."""
    r = runner or SyncRunner()
    return r.sync(
        plugin=plugin,
        target_class=target_class,
        upstream_repo=upstream_repo,
        upstream_tag=upstream_tag,
        plugin_dir=plugin_dir,
        dry_run=dry_run,
    )
