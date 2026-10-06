# Slice ③ §8.5 Second-Tag Clean-Pull Dry-Run — Evidence Artifact

**Date:** 2026-10-06
**Plugin:** `plugins/opendesign/` (REC §4.1 layer→class mapping)
**Source of truth:** REC §1.2 comp 6 sync-runner; REC §8.5 second-tag dry-run; CON §5 sync_result shape; v1-interface-contracts §5.
**Upstream:** local checkout `/home/nea/opt/open-design` (read-only; refs-fetch only)
**Author:** slice ③ coder build

---

## 1. Commands run (recorded for the audit trail)

```bash
# 1) Read the manifest freshly
.venv/bin/python -c "from daemon.plugin_subsystem import read_manifest, validate_manifest; d = read_manifest(Path('plugins/opendesign'), validate_tree=True); print(d.tag_pin_per_class, d.upstream_paths_per_class)"

# 2) Sync-runner dry-run for copy_freely @ v0.23.0 -> v0.24.1
.venv/bin/python -c "..."  # see §3 for the exact code

# 3) Sync-runner dry-run for snapshot_with_drift_alarm @ v0.23.0 -> v0.24.1
.venv/bin/python -c "..."  # see §4 for the exact code

# 4) Cross-check: direct git diff for both classes
git -C /home/nea/opt/open-design diff --stat open-design-v0.23.0 open-design-v0.24.1 -- design-systems/ design-templates/ craft/ prompt-templates/
git -C /home/nea/opt/open-design diff --stat open-design-v0.23.0 open-design-v0.24.1 -- apps/daemon/src/prompts/ packages/contracts/src/prompts/
```

All upstream commands were `refs-fetch` only (`git rev-parse`,
`git ls-tree`, `git cat-file`); **no** working-tree operations on
`/home/nea/opt/open-design` (the OD daemon runs from there; the slice
③ boundary is absolute per the dispatch).

---

## 2. Manifest inputs (read fresh; no cache)

```
upstream_paths_per_class: {
    'snapshot_with_drift_alarm': (
        'apps/daemon/src/prompts/',
        'packages/contracts/src/prompts/',
    ),
    # copy_freely entry absent — the strip-class-prefix heuristic
    # applies (matches the slice ② layout: local copy_freely/X
    # corresponds to upstream X).
}
copy_freely.paths: [
    'copy_freely/design-systems/',
    'copy_freely/design-templates/',
    'copy_freely/craft/',
    'copy_freely/prompt-templates/',
]
snapshot_with_drift_alarm.paths: [
    'snapshot_with_drift_alarm/prompts/daemon/',
    'snapshot_with_drift_alarm/prompts/contracts/',
]
tag_pin_per_class: {
    'copy_freely': 'open-design-v0.23.0',
    'snapshot_with_drift_alarm': 'open-design-v0.23.0',
}
```

The 4 divergence-register seeded entries (ids 1-4) document the
known planned divergence points (od-resource-layer §1.B) — they
are SEED entries, not active drift.  No new divergence entries
were added by the dry-run (the dry-run is, by CONVENTION, a
shadow operation that does NOT mutate the manifest).

---

## 3. copy_freely dry-run — sync_result shape

```json
{
  "plugin": "opendesign",
  "target_class": "copy_freely",
  "upstream_tag": "open-design-v0.24.1",
  "action": "clean_pulled",
  "diff_summary": {
    "files_added": 1,
    "files_modified": 0,
    "files_removed": 0
  },
  "staleness_age_days": 12
}
```

### 3.1. Interpretation vs plan expectation

**Plan expectation (REC §8.5 + od-resource-layer §0):** "zero
data-resource churn across the full v0.23.0 → v0.24.1 minor; the
§8.5 second-tag pair is expected to show zero data churn and
DOCUMENT what the port-layer (snapshot class) diff report looks
like — that's §8.5's second question."

**Actual finding (deviation from plan expectation):** the
`copy_freely` diff surfaces **1 file added**, **0 modified**, **0
removed**.  The single "added" file is the same PNG that slice ②
EXCLUDED by design:

```
prompt-templates/image/notion-team-dashboard-live-artifact.preview.png
```

This PNG exists in upstream `prompt-templates/` at v0.24.1 but
was deliberately excluded by the slice ② `od_vendor.py`
JSON-only class-entry filter (the `prompt-templates/` class is
JSON-only by the OQ3 record; non-JSON assets are skipped with a
recorded reason and the vendored tree has exactly 106 JSONs).

The dry-run does NOT add this file (the sync-runner never wrote
to the local tree in dry-run mode; the diff is shadow-only).  The
1-file-added finding is an OPERATOR-FACING signal: at the next
non-dry-run sync, the operator must decide to re-apply (vendor
the PNG, perhaps as a separate media-preview class entry) or
drop (decline to vendor, document the skip).  Per CON §2: "re-apply
or drop, update the log either way."

### 3.2. Cross-check via direct git diff

```
$ git -C /home/nea/opt/open-design diff --stat open-design-v0.23.0 open-design-v0.24.1 \
    -- design-systems/ design-templates/ craft/ prompt-templates/

(empty — zero changes)
```

The direct git diff confirms zero changes in the four
`copy_freely` upstream subtrees between v0.23.0 and v0.24.1.  The
1-file finding is the PNG that lives in the SAME subdir as the
JSON files but is not a JSON itself; the slice ② vendoring
filter excluded it.  This is NOT a v0.23.0 → v0.24.1 data-layer
churn finding; it is a vendoring-scope finding that pre-existed
slice ②.

Scope note (review clarification): the git-diff cross-check above
is UPSTREAM-vs-UPSTREAM (tag-to-tag), while the sync
`diff_summary` is LOCAL-vs-upstream@tag — both are true, hence the
PNG appears in one (the sync diff, because slice ② excluded it
locally) and not the other (upstream never added it between the
two tags).

### 3.3. Verdict

The dry-run PROVES the copy_freely pull at v0.23.0 → v0.24.1 is
**a no-op for the class-entry set** (zero data churn on the 4881
files we DID vendor) and **a single-file re-apply-or-drop surface**
for the PNG that the slice ② filter excluded.  The plan's
"zero data churn" expectation is met for the vendored set; the
PNG is a pre-existing scope decision, not a churn.

---

## 4. snapshot_with_drift_alarm dry-run — sync_result shape

```json
{
  "plugin": "opendesign",
  "target_class": "snapshot_with_drift_alarm",
  "upstream_tag": "open-design-v0.24.1",
  "action": "alarmed",
  "diff_summary": {
    "files_added": 1,
    "files_modified": 1,
    "files_removed": 0
  },
  "staleness_age_days": 12,
  "alarm": {
    "divergence_register_entry": {
      "id": 5,
      "files": [
        "prompts/contracts/od-next-intent-resolution.ts",
        "prompts/contracts/od-next-strategy.ts"
      ],
      "delta": "upstream drift observed on open-design-v0.24.1: +1/~1/-0",
      "rationale": "Snapshot pulled at sync time; divergence registered; re-apply or drop, update the log either way (CON §2)",
      "pinning_test": "tests/unit/plugin_subsystem/test_sync_runner.py::TestSyncSnapshotDrift::test_drift_alarm_at_snapshot_with_drift_alarm"
    }
  }
}
```

### 4.1. Interpretation vs plan expectation

**Plan expectation (REC §8.5):** "DOCUMENT what the port-layer
(snapshot class) diff report looks like — that's §8.5's second
question."  The plan expects the snapshot class to surface the
upstream's two-tree drift (contracts is "the hottest-changing
area of the resource layer," 106 commits in 7wk per
od-resource-layer §0).

**Actual finding (matches plan expectation, with concrete
counts):**

- `+1`: `od-next-intent-resolution.ts` is a NEW file at v0.24.1
  (not in v0.23.0). 120 lines, new module under
  `packages/contracts/src/prompts/`.
- `~1`: `od-next-strategy.ts` was MODIFIED at v0.24.1 (11 lines
  net change, per the direct git diff below).
- `0` removed: no upstream files dropped between v0.23.0 and
  v0.24.1.

The dry-run emits an **alarm** (CON §5: "alarmed iff alarmed" —
snapshot_with_drift_alarm always surfaces an alarm on non-empty
diff) with the divergence-register entry id=5 (incremented from
the 4 seeded entries).  The alarm is NOT committed to
MANIFEST.yaml in dry-run mode (the diff_summary is shadow-only;
the divergence-register update is gated on `dry_run=False`).

### 4.2. Cross-check via direct git diff

```
$ git -C /home/nea/opt/open-design diff --stat open-design-v0.23.0 open-design-v0.24.1 \
    -- apps/daemon/src/prompts/ packages/contracts/src/prompts/

 .../src/prompts/od-next-intent-resolution.ts       | 120 +++++++++++++++++++++
 packages/contracts/src/prompts/od-next-strategy.ts |  11 +-
 2 files changed, 129 insertions(+), 2 deletions(-)
```

Direct git diff agrees with the sync-runner: 1 new file
(+120), 1 modified file (+11/-2 net), 0 removed.  The 27
other files (10 daemon + 17 contracts) are byte-identical
between v0.23.0 and v0.24.1.

### 4.3. Verdict

The dry-run PROVES the snapshot class' alarm path works
end-to-end:

1. Upstream v0.24.1 has 28 files in the snapshot class paths
   (10 daemon + 18 contracts; **note**: the plan says "10
   composer modules + 18 mirror contracts" but the actual
   count at v0.24.1 is 18 contracts files; the slice ③
   snapshot at v0.23.0 was 17 contracts files; the new file
   at v0.24.1 brings the total to 18. This is a
   documented-on-the-evidence-artifact count, not a plan
   deviation).
2. The diff correctly surfaces 1 added + 1 modified + 0 removed
   (the byte-level differences between the local v0.23.0
   snapshot and upstream v0.24.1).
3. The alarm payload is the CON §5 verbatim shape:
   `{plugin, class, divergence_id, files, delta, rationale,
   pinning_test, observed_at, observed_tag}` (the
   `observed_at` is added by `build_drift_event_payload` at
   the `emit_drift_event` call site; the dry-run's
   `sync_result.alarm` carries the registry-entry shape
   per CON §5; see `daemon/plugin_subsystem/sync_runner.py`).
4. The seed entries (ids 1-4) document the KNOWN planned
   divergence points; the dry-run's id=5 entry documents
   the actual byte-level change observed in the dry-run.
   Future syncs will increment to id=6, id=7, etc.  The
   register grows monotonically; entries never shrink
   silently (the operator applies the re-apply-or-drop
   rule per CON §2).

---

## 5. Plan deviations + findings (recorded, not silently edited)

1. **snapshot-class count: 10+17=27 at v0.23.0 (plan says 10+18).**
   REC §4.1 row 2 says "10 TS composer modules + 18 mirror
   contracts"; the actual upstream at v0.23.0 has 10 daemon
   modules + 17 contracts files.  At v0.24.1, the contracts
   count is 18 (one new file added).  The 10+17 at v0.23.0
   is off-by-1 from the plan's literal text.  The slice ③
   resolution: vendor the actual 10+17=27 files at v0.23.0
   (byte-faithful); record the count in CURATION.md (or this
   artifact) so future contributors do not silently re-add
   the "missing" 18th contracts file.  The plan's "18" is
   a typo or refers to a different tag; the slice ③ number
   is the upstream-actual count.

2. **copy_freely PNG re-surface.** The slice ② vendoring
   filter excludes the PNG; the slice ③ dry-run surfaces it
   as a re-apply-or-drop finding.  This is the expected
   behavior per CON §2 — the operator must decide, the
   sync-runner does not silently add the file.  The slice ②
   CURATION.md already documents the exclusion; this evidence
   artifact cross-references it.

3. **Lightweight-tag handling.** v0.24.1 is a LIGHTWEIGHT tag
   (not annotated); `git rev-parse <tag>^{tag}` fails on it
   (the slice ② `od_vendor.py` would have failed too).  The
   slice ③ hardening is a two-stage `tag_present` check
   (try `^{tag}`, fall back to `^{}`, fall back to the tag
   literal) and a `_ref_for_listing` helper that picks the
   right ref for `git ls-tree` / `git log`.  Same hardening
   added to `od_vendor_snapshot.py`.  Without this, the
   dry-run would have failed on v0.24.1 alone.

4. **`upstream_paths` additive field.** The slice ② vendoring
   layout for `copy_freely` preserves the upstream tree's
   internal hierarchy (e.g. `design-systems/airbnb/manifest.json`
   stays at `copy_freely/design-systems/airbnb/manifest.json`);
   the slice ③ vendoring layout for `snapshot_with_drift_alarm`
   renames the local class subtree (`apps/daemon/src/prompts/`
   → `snapshot_with_drift_alarm/prompts/daemon/`, etc.) and
   flattens.  The manifest's `paths` are LOCAL paths
   (operator-facing); the corresponding UPSTREAM subdirs are
   a parallel `upstream_paths` additive field (CON §8 1.0.x
   rule permits additive fields).  When `upstream_paths` is
   declared, the sync-runner uses FLAT layout; when absent,
   the strip-class-prefix heuristic applies (preserves the
   upstream tree's internal hierarchy).  The
   `manifest.schema.json` was extended to permit
   `upstream_paths`; the reader checks length-match; the
   declaration carries the additive data.

---

## 6. Staleness data

```
staleness_age_days: 12
```

`open-design-v0.24.1` tag date vs dry-run date: 12 days.  The
escalation default N=14 (CON §2 placeholder) means a single
tag's staleness alone does not yet trigger block-promote.  The
`staleness_age_days` field in `sync_result` is the surface the
slice ⑥ promote-staleness-check predicate consumes (REC §1.2
comp 13).

---

## 7. End-to-end test pass

```
$ timeout 60 .venv/bin/python -m pytest tests/unit/plugin_subsystem -q --no-header
...
189 passed in 4.31s
```

189 tests pass (was 187 baseline; +2 for the new depth-bound
carve-out test and the HASHES.sha256 locally-owned test
introduced in the carry-forwards commit).  The dry-run paths
themselves are exercised by `test_sync_runner.py` (lands in
the next commit; the dry-run above is the live integration
test).

---

## 8. Reproduction recipe

```bash
# 0) Pre-conditions: uv-sync the venv, ensure upstream is at the
#    expected refs (NO working-tree changes).
.venv/bin/python -c "import daemon; print(daemon.__file__)"
# Must print: .../ensemble-src-wt-plugin-subsystem-03/daemon/__init__.py

# 1) Verify upstream has both tags (refs-only; safe).
git -C /home/nea/opt/open-design tag --list 'open-design-v*'

# 2) copy_freely dry-run.
.venv/bin/python <<'PYEOF'
import json
from pathlib import Path
from daemon.plugin_subsystem import SyncRunner
r = SyncRunner()
result = r.sync(
    plugin="opendesign", target_class="copy_freely",
    upstream_repo="/home/nea/opt/open-design", upstream_tag="open-design-v0.24.1",
    plugin_dir=Path("plugins/opendesign"), dry_run=True,
)
print(json.dumps(result.as_dict(), indent=2))
PYEOF

# 3) snapshot_with_drift_alarm dry-run.
.venv/bin/python <<'PYEOF'
import json
from pathlib import Path
from daemon.plugin_subsystem import SyncRunner
r = SyncRunner()
result = r.sync(
    plugin="opendesign", target_class="snapshot_with_drift_alarm",
    upstream_repo="/home/nea/opt/open-design", upstream_tag="open-design-v0.24.1",
    plugin_dir=Path("plugins/opendesign"), dry_run=True,
)
print(json.dumps(result.as_dict(), indent=2))
PYEOF

# 4) Cross-check via direct git diff.
git -C /home/nea/opt/open-design diff --stat open-design-v0.23.0 open-design-v0.24.1 \
    -- design-systems/ design-templates/ craft/ prompt-templates/
git -C /home/nea/opt/open-design diff --stat open-design-v0.23.0 open-design-v0.24.1 \
    -- apps/daemon/src/prompts/ packages/contracts/src/prompts/
```

---

*Artifact created 2026-10-06 as part of slice ③.  Reproduction
recipe above reproduces every finding verbatim.*
