# HANDOFF — release-retention-eviction-gap

Commission: fix the RELEASE-RETENTION EVICTION GAP (a staged release dir could be deleted before its
ceremony, with NO journal row). Resequenced by Ari 2026-11-23T12:12Z: the LIB-HALF landed FIRST as a
scripts-only STOPGAP release (v0.31.43), the ceremony-surface half + pack followed in Phase 2 (this
release). Owner: sonic-developer. Integration branch: `fix/retention-phase2`.

## Files touched across modules

- **Module A — `retention-lib` (stopgap, branch `fix/retention-lib`, commits `7012680c1` base rewrite +
  `ea0477e45` stopgap amendment; landed to `origin/latest` via the stopgap branch `60d1de7afa`, tag
  v0.31.43):**
  - `scripts/upgrade/lib.sh` (+322/−35 net in the stopgap; the Phase-2 tree carries it unchanged) —
    `retention_evict()` rewritten (now at `scripts/upgrade/lib.sh:9689`), plus 4 new private helpers:
    `_retention_hist_lines` (`:9588`), `_retention_staged_line` (`:9600`), `_retention_liveness_holder`
    (`:9647`), and the eligibility classifier.
- **Module B — `retention-stage` (branch `fix/retention-stage-promote`, commit `d33b8ac08`):**
  - `scripts/upgrade/stage.sh` (+36) — the `staged` lifecycle row at `scripts/upgrade/stage.sh:1192`.
  - `scripts/upgrade/promote.sh` (+7/−1) — the honest `not-staged` refusal at `scripts/upgrade/promote.sh:375`.
- **Module C — `retention-pack` (branch `test/retention-pack`, commits `64890346c` → `853601e8d` →
  `c72bff3b0`):**
  - `tests/packs/release_retention_eviction_pack.sh` (new, 471 lines) — the 4-pin pack + mutation leg.
  - `tests/test_release_journal.sh` (+28/−3) — §9 fixture re-aligned to the evidence-affirmative contract
    (block at `tests/test_release_journal.sh:1603`).
  - `.agents/tester/PACKS.md` (+15) — pack registration (shared registry, both teams honor it).

## What changed

**ROOT CAUSE (current tree @ origin/latest, pre-fix line numbers).** `retention_evict()` in
`scripts/upgrade/lib.sh` (pre-fix `:9566-9642`) decided eviction from a DIRECTORY LISTING: its pin set
was exactly `{journal current, journal previous}` (`:9595-9596`), every other release dir was
staged_at-ordered retention fodder, and the eviction loop (`:9636-9640`) executed `rm -rf` with a
`_log` line only — NO journal row, so `state.json` (the house's authoritative release record) LIED BY
OMISSION. Two compounding facts: (a) the journal carried NO lifecycle row naming a staged release
(stage.sh journaled only `stage-scripts-advance` + `warn` rows; 390-row live history confirmed zero
`staged` events), so "awaiting ceremony" was unknowable from the journal; (b) the sweep never read the
`releases/current` symlink's TARGET, so a mismatched/dangling pointer could survive an eviction.

**CALL SITES — reconciled against the giter attribution.** `retention_evict` is invoked at
`scripts/upgrade/promote.sh:1234` (commit seat), `scripts/upgrade/promote.sh:1498` (auto-rollback seat)
and `scripts/upgrade/rollback.sh:337`. `stage.sh` has NO retention_evict call (its "retention"
references are `_run_log_retention` = per-run LOG retention, plus comments; the lib.sh:7055/:7084
mentions are gate_soak audit-tail comments). Therefore the 2026-11-23 eviction of the staged v0.31.39 /
v0.31.40 dirs did NOT ride a sibling stage: the journal shows promote-start/commit pairs at
2026-11-23T03:10:20Z (v0.31.37) and 2026-11-23T10:24:12Z (v0.31.42) with NO promote in between, so the
sweep that removed those dirs rode the **v0.31.42 promote-commit seat at 10:24:12Z**. The giter's
"09:15Z / sibling stages" timestamp is a dir-mtime inference (09:15Z is when v0.31.42 was STAGED).
The phenomenon is unchanged and real: a staged release dir was deleted before its ceremony with zero
journal rows; the git tag survived (v0.31.39 is still a remote tag while its dir is gone).

**FIX (module A).** `retention_evict()` is now JOURNAL-ASSERTIVE:
1. **Pin set** = journal `current`, `previous`, `in_flight.target`, `pending_op.target` (null-safe).
2. **Evidence-affirmative eviction** — a dir may be evicted ONLY with journal evidence of supersession:
   its OWN lifecycle row (`staged` or `commit`) AND a LATER `commit` row for a version-greater W.
   The pre-fix `retention-oldest` / directory-listing path is GONE. Every other candidate is HELD with
   a reason token (`no-journal-evidence` | `staged-awaiting-ceremony-no-promoted-descendant` |
   `promoted-not-superseded`), named in a `retention_pin_hold` row plus a loud WARN — "nothing evicted"
   is never silent.
3. **Every eviction journals** a `retention_evict` row naming what/when/why:
   `version= staged_at= reason=superseded-by-promoted-<W> staged_row= pointer_target= journal_current=
   journal_previous=`. Refusals journal `retention_refuse` (`pointer-dangling` | `pointer-mismatch` |
   `journal-current-dir-missing` | `live-process-holding-release`).
4. **Pointer gate** — before any eviction the sweep reads the `current` symlink target; a dangling or
   mismatched pointer REFUSES the whole sweep (loud reason, zero evictions). The sweep NEVER writes the
   pointer (one writer per pointer: sibling lane 84edce22 owns it).
5. **agent_dir liveness probe** — before every `rm -rf`, `/proc/<pid>` is scanned (cwd/exe/root readlink
   prefix + cmdline containment) for a live holder inside the candidate release dir; a hit holds that
   dir (`live-process-holding-release`, naming pid:link); an unreadable `/proc` is FAIL-CLOSED (hold).
   Probe root overridable via `ENSEMBLE_RETENTION_PROC_ROOT` for testing.
6. **keep-3 itself is unchanged** (counting, ordering, epoch sort, BSD/GNU stat dispatch) — only
   ELIGIBILITY changed.

**FIX (module B).** `stage.sh:1192` appends the frozen lifecycle row
`staged` → `release <VERSION> staged at <REL> (rollback_safe=… source=…) — awaiting ceremony (promote.sh
owns current)`; the append is FAIL-LOUD (a staged release the journal does not know is unprotected from
retention). `promote.sh:375` — the absent-target refusal now names retention eviction as a possible
cause, points at the `retention_evict`/`retention_refuse` rows in `$INSTALL_DIR/releases/state.json`,
states the git tag survives + the re-stage path, and states explicitly that NO other version is promoted
instead. **Chosen safe behavior for fix-contract (c): REFUSE** (never resolve to a descendant) — an
Emperor ceremony names a version; substituting another one is exactly the "promotes something else
while the user believes the old slot was flipped" failure the commission warns about.

**PIN COVERAGE.** (i) staged-awaiting survives a sweep — the pack's P1/P1b hold legs; (ii) every
eviction journals a row — P2a/P2b + the MUTATION leg (stripping the `journal_history_append` from a
copy of lib.sh REDdens the row assertions while the eviction still occurs → non-vacuous); (iii) absent
target fails loudly — P3 (exit 78 + `reason=not-staged` + guidance + re-stage + refusal row + no flip);
(iv) post-sweep pointer resolves — P4a healthy / P4b dangling (refusal + zero evictions) / P4c mismatch
(refusal + zero evictions), plus P5 (live holder) and P6 (unreadable `/proc` → fail-closed).

## What is UNTESTED

- **The live promote/rollback seats were never exercised** — NO live promote was performed (the Emperor
  ceremony owns the flip). The live sweep path is proven only in scratch fixtures (pack) + the §9
  house-suite block.
- **The liveness probe against a REAL daemon whose exe sits inside a candidate release dir**: only
  synthetic holders were used (a background process with cwd inside the candidate). The live daemon's exe
  lives in the CURRENT release (`releases/v0.31.42/ensemble-prod`), which is pinned anyway.
- **The producer→consumer link between module B's `staged` row and module A's sweep anchor** is proven
  only synthetically (pack fixtures build the journal through the REAL helpers). The first REAL
  `staged` row lands with THIS release's stage (v0.31.45) — observable in the live journal afterwards.
- **No boot smoke** was run (scripts/upgrade-only change; the daemon payload is unchanged). Had one run
  from a daemon-child shell, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` would be pinned explicitly
  (`env -u` strips the ambient guard and the sweep KILLS live processes).
- **Long-run over-retention behavior** under sustained staging is unobserved: with no journal evidence a
  dir is HELD, so the tree can exceed keep-3 indefinitely (safe direction, but unmeasured).
- **The full house suite** `tests/test_release_journal.sh` was run on the merged tree; the remaining
  FAILs are the PRE-EXISTING GNU `date -j` / macOS-launchd debt (identical failure set as the TRUE
  base — module B measured 19=19 on base). Only §9 was re-baselined (it is the leg the contract change
  touches). The other suite legs were not re-verified against the amended lib.sh.
- **`ENSEMBLE_RETENTION_PROC_ROOT` fail-closed path**: proven via the test hook, not via an actually
  unreadable /proc.

## Known risks

- **LATE-ARRIVING PIN (disclosed per Ari's instruction).** The FOURTH pin (post-sweep pointer invariant)
  arrived from the ARI relay AFTER the first dispatch wave. It was folded in via a second bounce to
  module A (`ea0477e45`) — and that bounce changed the EVICTION CONTRACT (evidence-affirmative), which
  invalidated module C's already-authored pack, forcing a REV2 re-baseline (`c72bff3b0`) AND a house-suite
  §9 fixture re-alignment in the same turn. Any reviewer comparing the original briefs to the landed
  behavior must read the amendments, not the first briefs.
- **THE STOPGAP RELEASE ITSELF ADDED A releases/ DIR UNDER FIXED CODE.** v0.31.43 was staged by the
  stopgap, whose release contained module A only — so no `staged` row exists for v0.31.43 until THIS
  release's stage writes one. Until then v0.31.43 is HELD by rule (`no-journal-evidence`), and the live
  tree sits at 4 dirs vs keep-3 (v0.31.37, v0.31.41, v0.31.42, v0.31.43) — over-retention, the safe
  direction, and visible as `retention_pin_hold` rows.
- **NO OPERATOR "RELEASE THIS DIR" LEVER.** A dir with no journal evidence can only become evictable via
  supersession evidence (a promoted descendant). There is no marker/pin-file mechanism (none existed in
  the old code either) — deliberately not added here. Future work; disclosed rather than silently added.
- **THE STOPGAP CARRIED A KNOWN HOUSE-SUITE RED.** `tests/test_release_journal.sh` §9 (pre-fix line 1624)
  asserted "vOld IS evicted" from a fixture with no journal rows — under the new contract that dir is
  HELD, so §9 was RED in the v0.31.43 stopgap release and is closed by this release's §9 re-alignment.
- **POINTER OWNERSHIP.** The pointer repair is owned by sibling lane **84edce22** (their repair landed
  2026-11-23T12:08:44Z; the pointer was dangling → `v0.31.10` before that). This lane NEVER writes
  `releases/current` — module A only READS it and refuses on mismatch. If the pointer is ever left
  dangling/mismatched, the sweep refuses ALL evictions (over-retention) rather than risking a dangling
  pointer. Coordinate by name: **84edce22** (pointer repair + the "pointer == journal.current AND target
  exists" invariant).
- **THE POINTER GATE ENGAGES ONLY WHEN A `current` SYMLINK EXISTS** (pointer-absent layouts proceed with
  the journal `current` pinned). Deliberate reading — refusing on pointer-absent layouts would strand
  fresh installs and the house's own fixtures.
- **ROW-TOKEN COUPLING.** The pack asserts the frozen row strings verbatim; rewording any of them
  breaks the pack BY DESIGN (the pack IS the contract). `reasons=` on `retention_pin_hold` is
  COLON-separated (positionally parallel to `held=`).
- **LIVENESS PROBE VISIBILITY.** The probe trusts /proc; in a pid-namespaced container a holder could be
  invisible (fail-closed applies only when /proc is UNREADABLE, not when it is filtered).
- **THE V0.31.39 TAG SURVIVES WHILE ITS DIR IS GONE** — pre-existing, unchanged by this work; it is the
  defect's exact shape and the reason the ceremony surface refuses rather than resolving.
- **THE NEXT LIVE keep-3 SWEEP IS A LIVE EVENT.** Under the fixed code the next promote/rollback sweep
  will evict NOTHING (every live dir lacks journal evidence; v0.31.37 is journal `previous`) and will
  write `retention_pin_hold` rows instead — i.e. the fix's presence DEFER live retention rather than
  trigger it. No live eviction is triggered or blocked by this change; over-retention until the journal
  affirmatively releases a dir.

## How to verify

On the merged tree (this branch):

- `bash tests/packs/release_retention_eviction_pack.sh` → `RESULT: PASS=67 FAIL=0` (includes the
  MUTATION RED proof, P5 live-holder and P6 fail-closed legs).
- §9 house-suite block (extracted, run against this tree's lib.sh) → `SECTION9 SUMMARY: PASS=9 FAIL=0`.
- `bash -n scripts/upgrade/lib.sh scripts/upgrade/stage.sh scripts/upgrade/promote.sh` → clean.
- `bash tests/test_release_journal.sh` → §9 green; the remaining FAILs are the pre-existing GNU
  `date -j` / launchd debt (compare against the TRUE base failure list before blaming this change).

Live inspection (read-only):

- `ls -1 /home/nea/agents-ensemble/releases/` → expect v0.31.37, v0.31.41, v0.31.42, v0.31.43, v0.31.45.
- `readlink /home/nea/agents-ensemble/releases/current` → must resolve to an EXISTING dir equal to the
  journal's `current` (pin #4's postcondition).
- `grep -c '"event":"retention_evict"' /home/nea/agents-ensemble/releases/state.json` → 0 until a real
  superseding promote commits (then >0, one row per eviction).
- `grep '"event":"staged"' …/state.json` → the `staged` lifecycle row for the newest staged release.
- `grep '"event":"retention_pin_hold"' …/state.json` → names the held dirs + reasons when a sweep finds
  nothing evictable.

## Bash-raises used

none — no bash raise was consumed. The long PyInstaller/frontend builds ran under `proc` (background,
auto-kill budget), not as blocking bash commands; the sweep/pack runs were bounded inside the default
budget.

## Complexity self-assessment

**medium** — the verdict assessed at plan-open (after the single bounded explore and the module split)
and unchanged by the delivery. Rationale: three file-disjoint modules (one shell function rewritten, two
surgical edits, one new scratch-only pack + a fixture re-alignment), no daemon/ coupling, no schema or
migration change, no architectural seam. The work stayed inside one sonic-coder turn per module; the
late-arriving fourth pin and the liveness item arrived as bounded bounces (A-amendment, C-REV2) rather
than as a redesign, so no handover was raised. Note for the record: the EVIDENCE-AFFIRMATIVE rule was my
call (Ari specified the four items; the "journal must affirmatively release a dir, silence means hold"
strictness is mine, made so the stopgap actually CLOSES the v0.31.41 eviction window instead of merely
journaling it).
