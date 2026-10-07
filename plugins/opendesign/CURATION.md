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
   `plugins/opendesign/copy_freely/HASHES.sha256` in standard
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

## 5. Hash manifest location — `copy_freely/HASHES.sha256`

The dispatch says: "Write the hash manifest to a file under the plugin
tree (e.g. `plugins/opendesign/copy_freely.HASHES.sha256` or per
convention — pick a name + location that does NOT pollute the
`copy_freely/` class subtree; justify)."

**Slice ③ (2026-10-06) relocation: moved inside the class subtree.**
We previously placed the manifest at
`plugins/opendesign/copy_freely.HASHES.sha256` (adjacent to the
subtree) so sync-runner operations would never see it. The slice ③
carry-forward requirement reverses that choice: the manifest now lives
INSIDE the class subtree at `plugins/opendesign/copy_freely/HASHES.sha256`
because (a) the obvious `cd copy_freely && sha256sum -c HASHES.sha256`
audit command should work without path-munging, and (b) the slice-③
sync-runner documents the resolution: the hash manifest is a
**locally-owned file inside the sync tree** — sync treats it as a
non-upstream preserved file, never overwrites it from upstream
content, and never reports it in diff_summary (it is not part of the
class vendor set). The file `git mv`'d preserves history.

Justification (combined):

- **Auditability: `cd copy_freely && sha256sum -c HASHES.sha256` works.**
  The manifest records paths relative to `copy_freely/`, and now sits
  inside that directory. The previous adjacent layout required running
  `sha256sum -c ../copy_freely.HASHES.sha256` from inside `copy_freely/`,
  which is non-obvious.
- **Sync-runner resolution (slice ③):** the manifest is a
  locally-owned file (not part of the upstream vendor set); the
  sync-runner explicitly preserves it across pulls (treats
  `HASHES.sha256` as a non-upstream file in `copy_freely/`, never
  overwrites it from upstream content, never reports it in
  `diff_summary`). The hash manifest is therefore a
  second-class-residency file: it lives inside the class subtree for
  auditability but the sync-runner treats it as invisible to the
  vendor set.
- **Naming:** `HASHES.sha256` (uppercase, no class prefix) is the
  recommended name inside the class subtree; `snapshot_with_drift_alarm/`
  and `own_outright/` will gain their own `HASHES.sha256` files at
  slices ③ and ⑤ respectively. The class-prefix is implicit in the
  enclosing directory; per-class files do not collide.

### 5.1. Verifying the manifest offline

The hash manifest records paths relative to `copy_freely/`, so the
audit command runs **from inside `copy_freely/`** and references the
adjacent manifest directly. The natural command:

```
cd plugins/opendesign/copy_freely \
    && sha256sum -c HASHES.sha256 --quiet
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

---

## 8. Slice-⑤ provenance — runtime-subtree membership extension (reviewer-owed row)

**Date:** 2026-10-07 (row authored at slice ⑥; event is slice ⑤)
**Event:** developer-side vendored-class membership extension of
`snapshot_with_drift_alarm/` — `runtime/deck-protocol.ts` +
`runtime/deck-stage-fallback.ts` vendored at the SAME pin
(`open-design-v0.23.0`), `snapshot_with_drift_alarm/HASHES.sha256`
27 → 29 entries. This is the "CURATION provenance row" the slice-⑤
review ruling declared the real debt (register adjudication:
byte-faithful same-pin membership additions are NOT divergences —
no divergence-register entries were owed for this event).

**Why the two files exist:** the slice-⑤ F1 fix — the contracts
`deck-framework.ts` imports `../runtime/deck-protocol.js`; the
DECK_KIND prompt embeds `DECK_PROTOCOL_V1_INLINE_RUNTIME`, so the
runtime subtree (part of the same pinned contracts package) is
required for the extracted TS to evaluate. Only these two files were
required; the remaining 8 upstream `packages/contracts/src/runtime/`
files were NOT vendored (see §9 for the slice-⑥ completion of that
membership via the sanctioned sync route).

**Sole-writer disposition:** CON §5 names the sync-runner the ONLY
writer of the class subtrees; the sync-runner did not exist on the
⑤ branch (it merges at ⑥). The ⑤ ruling accepted this edit as a
pre-⑥ DISCLOSED TRANSITION edit because all four of its conditions
held — the same four bars the slice-⑥ sole-writer gate
(`daemon/plugin_subsystem/sole_writer_gate.py`) now mechanizes:

| Bar | Evidence for this event |
|---|---|
| 1. SHA-byte-faithful to the pin | both files' bytes = the pinned upstream blobs (`246e6f6d…` / `d701d30e…` at `open-design-v0.23.0`); verified by the ⑤ byte-equality fixture corpus |
| 2. hash-registered | both files carry `HASHES.sha256` rows (27 → 29; count reconciled in `test_snapshot_class_byte_fidelity.py`) |
| 3. manifest↔HASHES↔tree consistent | the manifest's `snapshot_with_drift_alarm.paths` declares `snapshot_with_drift_alarm/runtime/`; the reader validates ok @1.0.2 |
| 4. disclosed | this row (previously: commit message + manifest path comment + test count only) |

**From ⑥ onward (binding):** ALL vendored-class membership changes
route EXCLUSIVELY through the sync-runner — plugin #2 must NEVER
hand-edit a class subtree. The sole-writer gate enforces this
mechanically (hand-edit detection + disclosure trail).

---

## 9. Slice-⑥ provenance — real same-tag sync pull: runtime/ membership completed

**Date:** 2026-10-07 (slice ⑥ real-pull process gate, reviewer carry-forward #9)
**Command:** `sync("opendesign", "snapshot_with_drift_alarm", upstream_repo=
/home/nea/opt/open-design, plugin_dir=plugins/opendesign, dry_run=False)`
at the SAME pin `open-design-v0.23.0` — the first REAL (non-dry) sync on
the real plugin tree.

**What landed:** the 8 upstream `packages/contracts/src/runtime/` files
that slice ⑤ deliberately did not vendor (only the 2 deck-framework
imports were required then): `html-injection-points.ts`,
`membership-concurrency-limit.ts`, `model-window-limit.ts`,
`od-next-capability.ts`, `preview-build-focus.ts`, `preview-guards.ts`,
`preview-observability.ts`, `preview-runtime-state.ts`. Diff:
`+8/~0/-0` — additions only; the pre-existing 29 files were byte-touched
by NOTHING (`removed=0, changed=0` verified against the pre-pull SHA
snapshot).

**Route (the sanctioned sole-writer flow, first live exercise):**
manifest `upstream_paths` already declared `packages/contracts/src/runtime/`
(slice ⑤) → sync-runner real pull (atomic stage + rename; HASHES.sha256
preserved verbatim) → hash-registration of the +8 (the ruling's bar-2
step; HASHES 29 → 37; count reconciled in
`test_snapshot_class_byte_fidelity.py`) → the sync appended register
entry **id=5** (`status: "open"` emitted) → operator disposition
(**re-apply** — the pull itself is the re-apply) annotated onto id=5
with `status: "resolved"`.

**Deviations from the carry-forward's literal text (findings, not silent
edits):**

1. **"expect no_change" did NOT hold pre-pull.** The ⑤ tree vendored 2
   of 10 declared runtime files, so the same-tag diff was +8 (alarmed),
   not zero. The real pull COMPLETED the membership; post-pull re-syncs
   are true no-ops (dry and real — the idempotence assertion holds
   post-completion). The end state is the ideal gate state: tree ==
   pin∩declared exactly.
2. **"register +1 only if real divergence — none expected" — one entry
   WAS appended (id=5), and it is NOT a divergence** per the ⑤ register
   adjudication (byte-faithful pin-faithful files; a membership
   completion, not an upstream delta). The sync-runner's
   alarm-on-nonempty-diff semantics conflate membership-completion with
   drift — FLAGGED for the reviewer (a membership completion ideally
   records a provenance event, not a CON §2 re-apply-or-drop debt).
3. **copy_freely was NOT pulled.** Its same-tag diff is +1: the PNG
   (`prompt-templates/image/notion-team-dashboard-live-artifact.preview.png`,
   finding F-3 above). A real pull would land it and OVERRIDE the
   recorded slice-② exclusion — the sync-runner honors no
   parity-boundary exclusions (FLAGGED finding: parity rows are
   invisible to the sync path). copy_freely stays at the ② state;
   the exclusion remains the documented truth.
4. **The ③ sync-emitted `pinning_test` template named a nonexistent
   test class** (`TestSyncSnapshotDrift` vs the real
   `TestSnapshotClassDrift`) — latent until the first real append;
   caught by the reviewer's referential-integrity guard on this pull
   and fixed in ⑥ (template + this entry's pointer).

**6-assertion evidence (the gate):**

| # | Assertion | Result |
|---|---|---|
| a | landed-tree ≡ diff_summary per-file SHA | 37/37 files blob-SHA-identical to the pin; 0 mismatches |
| b | HASHES preserved + absent from summary | preserved verbatim through the rename; sha256sum -c clean (37/37); never in diff_summary |
| c | register +1 only if real divergence | +1 (id=5) — membership completion, not a divergence; disposition landed (resolved); deviation documented above |
| d | idempotence: re-sync ⇒ no_change | real + dry re-syncs both `no_change` |
| e | crash-injection windows | covered in-suite: `TestSyncAtomicity` (mid-pull whole-or-nothing) + `TestRenameAsideAtomicity` (W3) — green |
| f | pinning_test referential integrity | guard green post-fix (the ③ dangling-pointer bug closed) |

**Promote-gate note (honest end state):** the pin
`open-design-v0.23.0` is 16 days old as of this pull — the slice-⑥
predicate (`pin age > 14d ⇒ refuse`) REFUSES promotes for the current
tree state until the pin is refreshed via a real newer-tag sync, or the
operator takes the journaled `--allow-stale-plugins` override. That is
the gate working as designed: staleness is now VISIBLE at promote.
