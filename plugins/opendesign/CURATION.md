# OpenDesign Plugin — Curation Record (OQ3, slice ②)

**Date:** 2026-10-06 · **Authored by:** coder (slice ② build)
**Plugin:** `plugins/opendesign/` · **Pin:** `open-design-v0.23.0`
**Source of truth for the OQ3 record:** REC §1.2 (plugin_registry) + REC §9
(OD slice-② file curation) + ADR-PLUG-001 §3 (opendesign as plugin #1,
C-dominant with a B element for generation arriving at slice ⑤).

This document records every inclusion / exclusion decision for the
vendored tree at slice ② — the byte-fidelity evidence, the class-entry
counts vs the planning docs, and the explicit justification for any
deviation.  Per the slice ② dispatch: "deviations are findings, not
silent edits."

---

## 1. Pin and source

| Field | Value |
|---|---|
| Upstream repo | `https://github.com/nexu-io/open-design.git` (local checkout: `/home/nea/opt/open-design`) |
| Pin tag | `open-design-v0.23.0` (real upstream tag name; commit `c2aba14421c7b6ee1ace4bee3636d3bf64ee1fa0`) |
| Pin rationale | The slice ② dispatch says "if the plan is silent/open, pin **v0.23.0**" — the actual upstream tag is `open-design-v0.23.0` (the OD upstream prefixes its tags with the project name).  The spec also notes §8.5's `v0.23.0 ↔ v0.24.1` pair so slice ③'s second-tag pull is exactly the plan's dry-run. |
| Source path | Local OD checkout at `/home/nea/opt/open-design` (read-only, present since the earlier source-build commission).  The dispatch authorizes this: "search the home dir read-only: find/ls for opendesign/OpenDesign dirs; DO NOT modify the running daemon or its unit; reading its source tree is allowed".  The local checkout can resolve the pinned tag (verified: `git -C /home/nea/opt/open-design rev-parse open-design-v0.23.0` returns the commit). |
| Public-repo fallback | NOT needed.  The dispatch authorizes `/tmp` clone only if the local checkout is unusable; ours is usable. |

The vendored `MANIFEST.yaml` declares `open-design-v0.23.0` as the
`copy_freely` tag pin, matching the actual upstream ref.  The
slice-① manifest reader's `non_tag_pin` refusal set does not block
this name (no range marker, not a reserved literal, not a bare hex SHA).

---

## 2. Class mapping (REC §4.1) — actual vendored counts

| Class (CON §2) | OD upstream subtree | Spec expected (REC §9 + dispatch) | Actual vendored | Deviation |
|---|---|---|---|---|
| `copy_freely` (design systems) | `design-systems/` | 154 | **154 entries at top level** (152 brand DIRS + 1 `design-systems/_schema/` + 1 `design-systems/README.md`) | See finding F-1 below |
| `copy_freely` (design templates) | `design-templates/` | 115 | **115 entries at top level** (114 template DIRS + 1 `design-templates/AGENTS.md`) | See finding F-2 below |
| `copy_freely` (craft) | `craft/` | 13 | **13 top-level .md files** (11 guidance docs + `README.md` + `FUTURE_SECTIONS.md`) | None — exact match |
| `copy_freely` (prompt-templates) | `prompt-templates/{image,video}/*.json` | 106 | **106 JSON files** (48 image + 58 video) | None on count; see F-3 below for the excluded PNG |
| **Total class-entries** | — | **388** | **388** | **Exact match** |
| **Total file count (recursive)** | — | — | **4881 files** | n/a — byte-perfect copies of all files inside the 388 class-entries |

### Findings

- **F-1: design-systems count is 152 brand DIRS, not 154.**  The
  planning doc `.agents/shared/planning/od-native-design-subsystem/od-resource-layer.md`
  §2 row 1 says "**154 dirs**" — but upstream at the pinned tag
  actually has 152 brand DIRS (e.g. `airbnb/`, `apple/`, …).  The
  dispatch says "expected counts (154/115/13/106) vs actuals, and
  explicit justification for ANY deviation (zero deviations
  expected — deviations are findings, not silent edits)."  We chose
  the literal-faithful reading: the spec's `154` matches the upstream
  top-level entry count of `design-systems/` (152 brands + 1
  `_schema/` + 1 `README.md` = 154).  Vendored as-is.  The planning
  doc's loose use of "154 dirs" is OFF-BY-2 from the actual brand
  count.  Recommendation for the planning doc: change the count to
  152 brand DIRS or qualify "154 entries" (deferred — not a slice ②
  scope item).

- **F-2: design-templates count is 114 template DIRS, not 115.**  The
  planning doc row "115 dirs" similarly conflates: 114 template DIRS
  + 1 `design-templates/AGENTS.md` = 115 top-level entries.  Same
  resolution as F-1: literal-faithful 115.  The "115 dirs" wording is
  the planning-doc loose count.

- **F-3: 1 PNG excluded from `prompt-templates/`.**  Upstream has a
  single non-JSON file in the `prompt-templates/` subtree:
  `prompt-templates/image/notion-team-dashboard-live-artifact.preview.png`.
  The class map (§2 row 7) says "106 JSON" — the PNG is not a
  class-entry (it is a preview asset for a single prompt-template).
  The dispatch explicitly demands the 106 JSON-only count, so the PNG
  is excluded.  The vendoring script
  (`tools/vendor/od_vendor.py`) records the exclusion in its
  `skipped_entries` list and the vendored tree has exactly 106 JSONs.
  This is a deliberate, recorded deviation from raw upstream
  byte-perfection, justified by the count spec.

---

## 3. Excluded upstream assets (parity_boundary section of MANIFEST.yaml)

The manifest's `parity_boundary.intentionally_not_vendored` section
documents what was deliberately not vendored from upstream.  The list
is reproduced here for the audit trail:

| Upstream path | Reason | Reference |
|---|---|---|
| `apps/daemon/` | OD daemon + in-repo MCP forwarder; ENTANGLED | od-resource-layer §5 row 9; ADR-PLUG-001 §4 (retired at slice ⑦) |
| `apps/daemon/src/brands/` | Brand presets (19 modules + extraction loop + browser); ENTANGLED | od-resource-layer §5 row 9 |
| `apps/daemon/src/prompts/` | Daemon prompt stack (10 TS composer modules); LIGHTLY COUPLED | od-resource-layer §5 row 10; snapshot_with_drift_alarm lands at slice ⑤ |
| `packages/contracts/src/prompts/` | Published prompt mirror (17 files); two-tree drift is real | od-resource-layer §1.B; committed in divergence register at ⑤ |
| `apps/daemon/src/skills/` | Functional skills (165 dirs); STANDALONE-as-catalogue but execution needs upstream bundles | od-resource-layer §5 row 8; plugin-skill lands at slice ④ |
| `apps/daemon/src/artifacts/` | Internal lint / parse5 EOF / truncation detectors; own-outright | od-resource-layer §3; ported into generate adapter at slice ⑤ |
| `templates/` | Top-level HTML templates (3 entries); ORPHANED on upstream main | od-resource-layer §2; deprecated |

The dispatch specifically forbids including any of these at slice ②:
the B-element (prompt stack) and own-outright (compose-brief) lands at
slice ⑤; the daemon / brand flow lands at slice ⑦; the orphan templates
are out of scope.

---

## 4. Byte-fidelity evidence (clean-pull safety)

The dispatch requires: "every vendored file must equal upstream tag
content EXACTLY — verify by hash comparison (e.g. sha256 per file,
upstream@tag vs vendored) at vendoring time; RECORD the method + full
result."

### Method

1. **Enumerate** every file under the four copy-freely subtrees at the
   pinned tag using `git -C /home/nea/opt/open-design ls-tree -r
   open-design-v0.23.0 -- <subdir>/`.  Output: list of
   `(mode, type, blob_sha, upstream_path)` tuples.
2. **Read** each blob via `git -C /home/nea/opt/open-design cat-file
   blob <sha>`.  This is the upstream bytes — no encoding transform,
   no CRLF translation, no symlink resolution.
3. **Compute** sha256 of the upstream bytes.
4. **Write** the bytes verbatim to the vendored location
   (`plugins/opendesign/copy_freely/<upstream_path>`) using
   `open(target, "wb")`.
5. **Record** the `(sha256, relpath)` pair in
   `plugins/opendesign/copy_freely.HASHES.sha256` in standard
   `sha256sum` format, sorted by relpath for stability.

The vendoring script lives at `tools/vendor/od_vendor.py` and is
re-runnable for slice ③'s second-tag pull.

### Result

| Metric | Count |
|---|---|
| Files vendored | **4881** |
| Files recorded in hash manifest | **4881** |
| Upstream bytes re-read and re-hashed at vendoring time | **4881** |
| Upstream → vendored byte mismatches | **0** |
| Files excluded (F-3, single PNG) | **1** |
| Files skipped (other reasons) | **0** |
| On-disk re-hash vs recorded (post-write verification) | **4881 / 4881 match** |

The triple-check (recorded sha256 of upstream bytes → write bytes →
on-disk re-hash → match recorded) is what the dispatch's
"upstream@tag vs vendored" verification requires.  Zero mismatches.

The 4881 file count is the total recursive file count inside the 388
class-entries.  The 154 / 115 / 13 / 106 numbers are the class-entry
counts at the top of each subtree; the larger 4881 number is every
file inside those entries (e.g. each design system has its own
`manifest.json`, `DESIGN.md`, `tokens.css`, `preview/*.html`, etc.).

---

## 5. Hash manifest location — `copy_freely.HASHES.sha256`

The dispatch says: "Write the hash manifest to a file under the plugin
tree (e.g. `plugins/opendesign/copy_freely.HASHES.sha256` or per
convention — pick a name + location that does NOT pollute the
`copy_freely/` class subtree; justify)."

We placed it at `plugins/opendesign/copy_freely.HASHES.sha256` — the
dispatch's suggested name and location.  Justification:

- **Adjacent to, not inside, the class subtree.**  The
  `copy_freely.HASHES.sha256` filename uses a `.` separator (matching
  the file naming convention used elsewhere in the repo) and sits at
  the plugin root, NOT inside `copy_freely/`.  Sync-runner operations
  (slice ③) will write to `copy_freely/`; the hash manifest is
  unaffected.
- **Discovers cleanly as the canonical "copy_freely verification
  artifact".**  Reading the file name left-to-right: "copy_freely
  hashes sha256" — the intent is unambiguous.
- **Alternative considered and rejected:** `plugins/opendesign/HASHES.sha256`
  (plugin-root, no class qualifier).  Rejected because the plugin
  will gain `snapshot_with_drift_alarm.HASHES.sha256` and
  `own_outright.HASHES.sha256` at slices ③ and ⑤ respectively;
  class-qualified names scale without collisions.

### 5.1. Verifying the manifest offline

The hash manifest records paths relative to `copy_freely/`, so the
audit command must be run **from inside `copy_freely/`** with a path
to the adjacent manifest.  The obvious `cd plugins/opendesign && sha256sum -c
copy_freely.HASHES.sha256` invocation fails with `4881 listed files
could not be read` and exit code 1 (paths are resolved relative to
the cwd, so `craft/FUTURE_SECTIONS.md` is looked up at
`plugins/opendesign/craft/FUTURE_SECTIONS.md`, which does not exist).

The correct, offline audit command:

```
cd plugins/opendesign/copy_freely \
    && sha256sum -c ../copy_freely.HASHES.sha256 --quiet
echo exit=$?
```

Expected result: silent success (no output), `exit=0`, all **4881**
files verified clean.  Non-zero exit or any `FAILED` line means the
vendored tree drifted from the pinned tag and slice ③'s clean-pull
dry-run / sync workflow must investigate before any further sync
action.

---

## 6. Sentinel compliance (CON §7)

- **No-import invariant:** the vendored tree is data only.  No module
  under `daemon/` imports from `plugins/opendesign/`; the slice-①
  `TestNoImportInvariant` sentinel test continues to pass.
- **No-runtime-loading:** the vendored tree contains no `importlib`
  usage, no entry-point scanning, no plugin code loads.  Slice-①
  `TestNoRuntimeLoading` sentinels continue to pass.
- **Vocabulary confinement:** the manifest vocabulary
  (`execution_mode`, `integration_path`, `copy_freely`,
  `parity_boundary`, `alarm_owner`) appears ONLY under
  `daemon/plugin_subsystem/**` and `plugins-convention/**` PLUS the
  one authorized data instance at `plugins/opendesign/MANIFEST.yaml`.
  The slice-① `TestVocabularyConfinement` sentinel test is the
  authoritative guard; the MANIFEST.yaml is a data instance, not a
  definition site.

---

## 7. OQ3 verdict

**Class-modeling:** 154 / 115 / 13 / 106 — all match the spec literally
(the planning doc's loose "154 dirs" / "115 dirs" wording is off by 2
from the actual brand / template DIRS, but matches the top-level entry
count of each subtree).  The CURATION.md documents the resolution.

**Byte-fidelity:** 4881 / 4881 files byte-exact to upstream.  Zero
mismatches on triple-check.

**License carry:** Apache-2.0 vendored at
`plugins/opendesign/LICENSE` with attribution + change-statement
header (od-resource-layer §0: "license-copy + attribution +
change-statement").  License body byte-identical to upstream
`LICENSE` (201 lines, prepended with 15-line ensemble-side header).

**Exclusions:** 1 PNG (`notion-team-dashboard-live-artifact.preview.png`)
excluded from `prompt-templates/image/` — not a class-entry; deliberate
deviation, recorded in the vendoring script's `skipped_entries` and
in this document's finding F-3.

**W5 carry-forward (slice-④ added):** 11 preview JPGs under
upstream `assets/prompt-templates/image/` (the rendered previews of
the major image prompt templates) are excluded from the vendored set.
Per the slice-② review (W5: "11 preview JPGs ... are excluded from
the vendored set but undocumented"), this deviation is now recorded in
`MANIFEST.yaml`'s `parity_boundary.intentionally_not_vendored` row
referencing `assets/prompt-templates/image/*.jpg`.  The vendored
subtree is the 48 JSON files only; the 11 JPGs live ONLY upstream at
`/home/nea/opt/open-design/assets/prompt-templates/image/` (read-only
reference; do NOT modify upstream).  The slice-② planning count spec
was "106 JSON" (the JSON class-entry count); the JPGs are rendered
previews (renditions of what the prompt generates, not inputs to
generation) and are out of scope for the JSON-only vendoring policy
set in §2 above.  The JPG filenames (one per major image prompt
template) are listed in the manifest's parity row's reason for audit.

**Manifest:** `plugins/opendesign/MANIFEST.yaml` validates against the
slice-① reader (`validate_manifest(..., validate_tree=True)` returns
`ok=True`).  At slice ②: single class section (`copy_freely`);
`parity_boundary` with 7 `intentionally_not_vendored` rows +
1 `not_executed` row; SPDX `Apache-2.0`; execution_mode
`resource-only`; pin `open-design-v0.23.0`; integration_path `C`.
At slice ④ (this update): `parity_boundary.intentionally_not_vendored`
gains the W5 row (above) and the manifest gains a `skills` section
declaring `opendesign.list_systems` (CON §6) at
`skills/opendesign.list_systems.yaml` — the slice-④ plugin-skill
proves skill↔tree references and pin-visible-at-load end-to-end
(REC §4.3 row ④).  The skills-section addition is the ONLY other
manifest change; everything else is byte-exact to slice ②.
