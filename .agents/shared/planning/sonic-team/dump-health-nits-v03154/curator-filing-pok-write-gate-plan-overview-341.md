# Curator filing — stale kb-importer figure in pok-write-gate `plan-overview.md:341`

**Addressed to:** the `pok-write-gate` lane owner / leader–project-manager curator.
**Filed by:** sonic-coder, module `citations`, commission `dump-health-nits-v03154` (follow-up slice).
**Date:** 2026-11-25.
**Nature:** ASSESS-AND-REPORT ONLY. This filing requests an edit to another lane's plan file; it does **not** make that edit. No POK write is part of this slice.

---

## Target

- **File:line:** `.agents/shared/planning/pok-write-gate/plan-overview.md:341`
- **STALE text (as it stands in that file):**
  `  **kb-importer = 1** active row, **experiencer = 0** active rows, vs **kb-writer = 131**`
  (under the "Write volume" bullet that opens "from the 499-active census @2026-11-23 12:29Z (sha256 `6737281c…`):"). The brief for this slice paraphrases the stale claim as "**kb-importer = 1** active row, from the 499-active census @2026-11-23 12:29Z".

## Corrected figure

**kb-importer = 5 active rows across 2 projects, +1 superseded** — re-derived read-only on 2026-11-25. The 2026-11-23 census figure of 1 was a stale snapshot. (3 of the 5 active rows are on project `83da04de…` agents-ensemble, 2 on the `a5f20438…` dev project; the +1 superseded row is `android-04-hints-and-longpress`, re-upserted 2026-11-24.)

## Evidence source

- Primary: `.agents/shared/planning/pok-write-gate/feeder-assessment-kb-importer-2026-11-25.md` — §1 states "Live re-derivation (2026-11-25) shows **5 active rows** across 2 projects" (plus 1 superseded), from `BEGIN READ ONLY` SELECTs against `ensemble_prod` @ `10.44.0.2:5432`, measured 2026-11-25T03:33Z.
- Per-row evidence: `.agents/shared/planning/pok-write-gate/evidence-rederiv-2026-11-25/kb-importer-active-rows.tsv` (5 active rows, 12 columns incl. body and `content_sha256`, sha256 verified against the stored value for each).
- Corroborating: item **1b** of `.agents/shared/planning/sonic-team/feeder-grants-dump-health/HANDOFF.md` (same 5-active / +1-superseded / 2-project figure, same read-only derivation).

## Requested edit (for the lane owner to apply)

1. Replace the kb-importer figure at `plan-overview.md:341` from **1** active row to the re-derived **5 active rows across 2 projects (+1 superseded, re-derived read-only 2026-11-25)**, with the evidence sources above.
2. Re-check the **"≤53 combined"** arithmetic: that figure was computed against the old per-feeder counts, so the lane owner should re-derive it under the corrected kb-importer figure rather than carry it forward unchanged.

## Fence statement

`plan-overview.md` was **NOT edited** by this slice — it is another lane's plan file and is never silently edited. This document is a filing/request only. No file under `.agents/shared/planning/pok-write-gate/` was touched, and no `pok_upsert` / POK write was made (the figure is read-only cited).
