# Phase 8 — Staged release (STAGE ONLY; NO promote — the Emperor's ceremony owns the flip)

**Depends on:** P1-P7.
**Owns:** the release-prep lane's own discipline (slot derivation, rollback adjudication, ceremony notes). **Does NOT promote.**

---

## 1. Objective

Turn the merged lane into a **staged, ceremony-ready release artifact** — release directory, manifest, provenance sidecars, tag — with:

1. the **version slot derived LIVE at stage time** (never hardcoded — house rule W9);
2. the **rollback-safe adjudication stated with its reason** (the delta is two additive `CREATE TABLE IF NOT EXISTS` files ⇒ `rollback_safe = true`, `source = explicit`);
3. the **loud, unmissable disclosure** that this release ships the engine **DORMANT** (registered, observing, executing nothing) and that it deliberately changes **zero** existing behavior;
4. **no promote**, no `current` flip, no `state.json` promote-field touch.

---

## 2. Scope

**In:** slot derivation; the stage invocation with the correct env binds; the tag + annotation content; the release notes / ceremony notes; the manifest verification (4-way sha); the migration-delta census (delta form); the rollback adjudication record; the promotion handoff block (what the Emperor's ceremony must check).

**Out:** `promote.sh` (never runs here); any `systemctl` action; any live-install mutation beyond the stage-time release-dir write; the version number itself (derived, not chosen).

---

## 3. Decisions

| ID | Decision | Rationale (anchor) | Rejected |
|---|---|---|---|
| D8.1 | **Version slot derived at stage time** from the live install's `releases/` census + the tag listing; never a literal in this plan | House rule W9 (`.agents/shared/planning/ari-memoir-store/plan-overview.md:43, :100`: "Version slot derived at stage time (W9) … never hardcoded"); precedent `.agents/shared/planning/skill-system-revival/phase1-plan.md:883`: "the implementer re-derives the next free slot at staging time and records it". Project metadata's `next_free_version_slot` is explicitly known-stale (the v0.29.0 ceremony left `v0.29.1` while later lanes consumed slots) | a planned version number (goes stale the moment a sibling lane stages) |
| D8.2 | Stage argv (canonical, env-first): `ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION=<v-derived> bash scripts/upgrade/stage.sh live --skip-build` — with the **`live` TARGET argument mandatory** | Verified in the aegis lane's adjudication (`.agents/shared/planning/aegis-security-family/phase6-plan.md:41`): `ENSEMBLE_UPGRADE_LIVE=1` is the **guard for TARGET=live, NOT the selector** — a bare invocation resolves to `demo`. And `ENSEMBLE_ROLLBACK_SAFE` must be **bound in the command env**: `stage.sh:395-417` derives it by grepping the staged migration set for destructive DDL, and that grep matches most historical files ⇒ exit 78 without an explicit choice. VERSION must carry the `v` prefix (ADR-009 D3) | a bare `stage.sh live` (demo default); letting the derivation run unbound (exit 78) |
| D8.3 | `rollback_safe = true`, **`source = explicit`**, with the reasoning recorded in BOTH the tag annotation AND the stage journal | The lane's own delta is exactly **+2 additive files** (`CREATE TABLE IF NOT EXISTS` ×2, no `DROP`, no `ALTER`, no column change); the pre-lane binary references **no** engine table (`engine_jobs`/`engine_runs` are named nowhere in the base tree — verified: zero hits across `daemon/` and `tests/` at `b71742fe3`). Consequence of a rollback: the two tables sit inert and the old binary ignores them — **fix-loss, never incompatibility** (the board / memoir-v2 inert-tables class) | deriving it implicitly (the grep is over the whole migration set, not this lane's delta — the adjudication must name the DELTA) |
| D8.4 | **Ceremony notes carry four mandatory disclosures**: (a) the engine ships dormant with zero registrations; (b) no existing periodic mechanism is modified; (c) the migration delta is +2 additive tables, inert on rollback; (d) `SERVICES_*_USE_ENGINE` flags exist, all default OFF, none wired to a module yet | The Emperor phase rule and the one-by-one rule mean **nothing changes behaviorally** in this release. A reader of the release notes must be able to verify that claim from the notes, not by reading 20k diff lines | a generic "new engine" note (invites the assumption that something migrated) |
| D8.5 | The **migration-delta census is stated at three levels** (source tree / staged artifact / live install) and in **delta form** | Precedent `.agents/shared/planning/canary-injection-defense/phase6-plan.md:49`: "count the `.sql` files in the staged binary and in the live binary and compare with the source tree … the adjudication must reason on the MERGED delta". Absolute counts rot: the tree has **134** `.sql` files at this base, while a prior lane measured 119 and a study note said 127 — **three different numbers for the same directory across three documents**. Delta + prefix uniqueness is the only stable assertion | quoting an absolute count |
| D8.6 | The release content is **the full branch** (P1-P7), staged as ONE artifact; no partial-release attempt | The phases are ordered and each depends on the previous; a partial stage would ship a table with no engine or an engine with no guardrails | splitting the stage |
| D8.7 | The handoff block names **exactly what the Emperor's ceremony must check** (nonce/source, 4-way sha, readyz/livez gates, soak) — and states explicitly that the lane performed **none** of it | Promote ceremony boundaries: the lane stages, the ceremony promotes. Naming the checks it did NOT perform is what keeps the handoff honest | claiming "verified ready" (a promote-time claim this lane cannot make) |

---

## 4. Task decomposition

| ID | Task | Outcome | Anchors |
|---|---|---|---|
| **T8.1** | Derive the slot: census `<INSTALL_DIR>/releases/`, `git tag --list`, record the derivation in the lane notes | a version string with a `v` prefix, its derivation commands quoted, and the "no reservation" statement | W9 precedent `ari-memoir-store/plan-overview.md:43`; `skill-system-revival/phase1-plan.md:883` |
| **T8.2** | Merge/sync the lane branch onto the integration tip; confirm the tree is the `latest` tip at stage time | tip identity satisfied by construction (the freshness guard refuses a non-tip tree) | `scripts/upgrade/stage.sh:93-102` (freshness guard) |
| **T8.3** | Migration-delta census (three levels, delta form, prefix uniqueness) | `+2` new files, distinct prefixes `20261121_120000` / `20261121_120001`, no duplicate prefix in the merged set | `tests/migration/test_migration_dup_prefix_guard.py:213-220` (`test_real_versions_dir_has_no_duplicate_version_prefix`) |
| **T8.4** | Rollback adjudication record (delta-only reasoning) | `rollback_safe=true`, `source=explicit`, the reasoning text, and the rollback consequence (inert tables, fix-loss not incompatibility) | precedent `aegis-security-family/phase6-plan.md:41`; inert-tables class `canary-injection-defense/phase6-plan.md:49` |
| **T8.5** | Run the stage with the D8.2 argv (build provenance sidecars produced by `--skip-build` path or a real build — decided by the operator at stage time from the artifact state) | a release directory exists; manifest carries version, binary sha, tree hashes, `rollback_safe`; BOTH provenance sidecars kept | `scripts/upgrade/stage.sh:93-110` |
| **T8.6** | 4-way sha verification (release dir / `dist/` / manifest / sidecar) | four identical sha256 values recorded | house ceremony discipline |
| **T8.7** | Tag + annotation | an annotated tag whose text carries the D8.4 disclosures verbatim + the D8.5 delta census | |
| **T8.8** | Ceremony-notes file + handoff block | the notes state the four disclosures and the "what this lane did NOT do" list (`no promote`, `no current flip`, `no state.json promote fields`, `no systemctl`, `no live-DB mutation`) | |
| **T8.9** | Stage-journal entry | the journal records the live-dir write, the rollback adjudication, the slot derivation, and the listener/posture lines | `scripts/upgrade/journal_read.py` for verification |

---

## 5. Effort

| Bucket | Low | High | Basis |
|---|---|---|---|
| Slot derivation + tip sync + census (T8.1-T8.3) | 1.5 | 3 | |
| Rollback adjudication + notes + tag (T8.4, T8.7, T8.8) | 1.5 | 3 | |
| Stage run + 4-way verify + journal (T8.5, T8.6, T8.9) | 2 | 4 | build time dominates |
| **Phase total** | **5** | **10** | ~**7 mid** |

> **Effort note.** The brief's 162h anchor covers the *build*, not the ceremony. This phase's numbers are ceremony-only. A **build** is required if the stage uses `--skip-build`; the estimate above assumes a prebuilt artifact exists at the lane's tip (the usual case) — a cold frontend + binary build adds roughly the house build wall-clock, which is schedule, not engineering.

---

## 6. Fences that constrain this phase

**F-24 — the `rollback.sh` / `manifest` contract reads `rollback_safe` by key.**
`scripts/upgrade/rollback.sh:174-177` reads `rollback_safe` from the manifest; `scripts/upgrade/lib.sh` exposes `manifest_field`/`manifest_path` (`:3061-3070`) and the adopt/rollback guard (`:7840-7843`). **Constraint:** the manifest's `rollback_safe` value and its `source` must be the ones the adjudication states; a defaulted (not explicit) value is a weaker record even when numerically identical.

**F-25 — the destructive-DDL derivation is over the WHOLE migration set, not this lane's delta.**
`scripts/upgrade/stage.sh:395-417` greps the staged migration set; it matches the vast majority of historical files. **Constraint:** `ENSEMBLE_ROLLBACK_SAFE=1` must be bound explicitly (D8.2) — otherwise the stage exits 78 regardless of this lane shipping zero destructive DDL. This is a mechanical fact about the tool, not a claim about the lane.

**F-26 — the promote-field fence.**
`state.json`'s promote fields are byte-compared pre/post in the house ceremony discipline (aegis lane B6: "the PROMOTE-FIELD fence is byte-identical pre/post"). **Constraint:** the stage writes the release dir + manifest + sidecars; it must leave `state.json` promote fields and the `current` symlink untouched. The lane asserts byte-identity and records the hashes.

**F-27 — the live-dir write is a stage-time, journaled, guarded action.**
`ENSEMBLE_UPGRADE_LIVE=1` permits the release-dir write into the live install's `releases/`; it is **not** a promote selector (the `live` TARGET argument is), and the action must be journaled durably. **Constraint:** D8.2's argv + T8.9's journal entry.

**F-28 — the dormant-posture and diff-audit legs (P7 D/E) are release-blocking.** A stage is not taken before both are green: shipping a release whose own gates are red is the failure mode the pack set exists to prevent.

---

## 7. Dependencies

- **Phase:** P1-P7 complete and merged; P7's legs D/E/G/H green.
- **External:** the sibling `fix/phase1-live-defects` lane (see phase3 §6.1 coupling facts — merge order is the decisions worker's recommendation; either order is mechanically possible because the `api.py` regions are disjoint).
- **Blocks:** the Emperor's promote ceremony.

## 8. Parallelism

- **Serial, and last.** Release prep cannot be parallelized against a moving tree (the freshness guard would refuse a non-tip tree).
- The lane must NOT race another lane's stage (a `pipeline-busy` refusal, `stage.sh` exit 78 class).

## 9. Risks / mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| A hardcoded version slot collides with a sibling lane | stage refuse / slot reuse | D8.1: derived at stage time, recorded with the derivation commands |
| `rollback_safe` defaulted instead of adjudicated | a weaker record; the rollback target's safety is asserted, not reasoned | D8.3: explicit env bind + the delta reasoning in tag + journal |
| The stage exits 78 on the destructive-DDL derivation | lost time mid-ceremony | F-25 named up front; the explicit bind is in the argv |
| The release notes imply something migrated | the Emperor's phase rule misread at promote time | D8.4's four mandatory disclosures + D8.7's "what this lane did NOT do" |
| An absolute migration count is quoted and rots | a future reader distrusts the note | D8.5 delta form (the tree already carries three different historical counts for the same directory) |
| The lane accidentally promotes | a ceremony-boundary violation | T8.8's explicit non-action list; `promote.sh` is never invoked in this phase's task set |

## 10. Acceptance gates (measurable)

| ID | Gate |
|---|---|
| AC1 | Slot derived live; derivation commands + output recorded; the version carries a `v` prefix; "no reservation" stated |
| AC2 | Migration delta == **+2**; both prefixes present and unique in the merged set; the dup-prefix guard green |
| AC3 | Manifest carries `rollback_safe=true` with `source=explicit`; the delta-only reasoning appears in BOTH the tag annotation and the stage journal |
| AC4 | 4-way sha: release dir == `dist/` == manifest == sidecar (four identical values recorded) |
| AC5 | Ceremony notes contain the four D8.4 disclosures verbatim and the D8.7 non-action list |
| AC6 | **No promote side effect:** `state.json` promote fields byte-identical pre/post (hashes recorded); the `current` symlink unchanged; no `systemctl` invocation; the live DB unmutated |
| AC7 | P7 legs D/E/G/H green **at the staged tip** (re-run, not carried from an earlier commit) |
| AC8 | Stage journal readable by `scripts/upgrade/journal_read.py` and containing the slot, the rollback adjudication, and the live-dir write record |
