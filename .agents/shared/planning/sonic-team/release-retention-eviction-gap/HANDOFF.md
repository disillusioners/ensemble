# HANDOFF — release-retention-eviction-gap (RE-DISPATCH #1 refresh)

Commission: fix the RELEASE-RETENTION EVICTION GAP (a staged release dir could be deleted before its
ceremony, with NO journal row). Resequenced by Ari 2026-11-23T12:12Z: the LIB-HALF landed FIRST as a
scripts-only STOPGAP (v0.31.43); Phase 2 (v0.31.45) added the ceremony surface + 4-pin pack; this
refresh closes the two reviewer gaps from the one-wave verification (RE-DISPATCH #1) and re-stages as
v0.31.47. Owner: sonic-developer. Integration branch: `fix/retention-phase2`.

## Files touched across modules

- **Module A — `retention-lib`** (`7012680c1` base rewrite → `ea0477e45` stopgap amendment → **`dbab01d50`
  G1: append-failure HOLDS**): `scripts/upgrade/lib.sh` — `retention_evict()` (merged tree `:9689`),
  helpers `_retention_hist_lines`/`_retention_staged_line`/`_retention_liveness_holder`, the eligibility
  classifier, the G1 hold path, and the one-line `_splice_journal_path` resolution WARN (`:1928`).
- **Module B — `retention-stage`** (`d33b8ac08`): `scripts/upgrade/stage.sh:1192` (the `staged`
  lifecycle row, fail-loud) + `scripts/upgrade/promote.sh:375` (honest `not-staged` refusal).
- **Module C — `retention-pack`** (`64890346c` → `853601e8d` → `c72bff3b0` → **`800d1a18a` P2
  re-baseline**): `tests/packs/release_retention_eviction_pack.sh`, `tests/test_release_journal.sh`
  (§9 fixture at `:1603`), `.agents/tester/PACKS.md`.

## What changed

**ROOT CAUSE.** Pre-fix `retention_evict()` pinned only `{journal current, journal previous}` and
evicted everything else by staged_at from a DIRECTORY LISTING, `rm -rf` with a `_log` line only — no
journal row (state.json lied by omission). The journal carried no `staged` lifecycle row, so
"awaiting ceremony" was unknowable; the sweep never read the `current` symlink's target. **Call
sites:** `promote.sh:1234` (commit seat), `promote.sh:1498` (auto-rollback), `rollback.sh:337`;
stage.sh has NONE — the 2026-11-23 eviction of staged v0.31.39/.40 rode the **v0.31.42 promote-commit
seat at 10:24:12Z** (journal-proven; the giter's "09:15Z sibling stages" was a dir-mtime inference).
The v0.31.39 TAG survives while its dir is gone — the defect's exact shape.

**FIX.** Evidence-affirmative eviction (own lifecycle row + a LATER version-greater commit; the
directory-listing path is gone; everything else HELD with a reason token + `retention_pin_hold` row +
loud WARN) · every eviction journals `retention_evict` (what/when/why) · refusals journal
`retention_refuse` · pointer gate (dangling/mismatch/journal-current-missing → refuse ALL evictions;
never writes the pointer — sibling lane **84edce22** owns it) · agent_dir liveness probe (`/proc`
cwd/exe/root/cmdline; unreadable `/proc` → fail-closed hold; `ENSEMBLE_RETENTION_PROC_ROOT` test hook)
· keep-3 unchanged (only eligibility changed) · module B: the `staged` row at stage time + the
absent-target REFUSAL (exit 78, `reason=not-staged`, retention cause + journal rows + tag-survives +
re-stage path + explicit NO version fallback — the single chosen safe behavior for fix-contract (c)).

**G1 (RE-DISPATCH #1, reviewer item 3, leader-adjudicated CODE fix).** When the
`journal_history_append retention_evict` call FAILS, the sweep now HOLDS the dir — skips the `rm -rf`,
warns loudly ("refusing to evict without a journal record (fix-contract b)"), writes a best-effort
`retention_pin_hold` row with the NEW reason token `journal-append-failed`, and continues with the
other candidates; rc stays 0 (post-commit seat must not fail; over-retention is the safe direction).
A failed append now means the dir SURVIVES — the journal can never lose a release silently. The
fail-open phrase "eviction still performed" is gone.

**P2 re-baseline (consequence of G1).** The pack's mutation shim is now a FAILING stub
(`_retention_pack_mut_noop() { return 1; }`) so the stripped copy simulates append FAILURE: the RED
condition is **HOLD + row-absent + dir survives**, with the control run asserted explicitly
(unstripped ⇒ evicted + row present) and the staged/commit evidence rows asserted still-present
(non-vacuous).

**Splice-helper WARN (tester footgun, anomaly 4d3807bf — LANDED, one line).** `_splice_journal_path`
now WARNs when BOTH resolution anchors miss (a lib.sh sourced from a copy without its sibling
journal_splice.py) — the silent-empty-journal vacuity trap is closed at the source.

## What is UNTESTED

- Live promote/rollback seats (NO live promote — the Emperor ceremony owns the flip); the live sweep
  is proven only in scratch (pack, §9, and the tester's live-journal-copy harness).
- The liveness probe against a REAL daemon exe inside a candidate dir (synthetic holders only), and
  fail-closed via a genuinely unreadable `/proc` (test hook only).
- G1's failure mode was exercised via harness shims of `journal_history_append`, not against a
  genuinely torn/down journal.
- House-suite `tests/test_release_journal.sh` full-run totals (both trees time out at the cap at the
  same 17th section; FAIL lists 19=19 identical — the pre-existing GNU `date -j`/launchd debt;
  §9 itself is 9/0 and is the only re-baselined leg).
- No boot smoke (scripts/upgrade-only payload); had one run from a daemon-child shell,
  `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` would be pinned explicitly.
- Long-run over-retention under sustained staging (unmeasured; visible via repeated PIN HOLD rows).

## Known risks

- **G1 residual (adjudicated acceptable, disclosed):** the refuse/hold append paths
  (`retention_refuse`/`retention_pin_hold` writes) remain OBSERVABILITY-ONLY on failure — a failed
  refusal/hold row cannot block anything (there is nothing to block: those paths already evict
  nothing). Only the `retention_evict` append is load-bearing and now fails CLOSED (hold).
- **New hold reason token** `journal-append-failed` joins the
  frozen set (`no-journal-evidence` | `staged-awaiting-ceremony-no-promoted-descendant` |
  `promoted-not-superseded` | `live-process-holding-release`); it lands only when the eviction-row
  append failed but the journal is otherwise writable.
- **v0.31.45 is SUPERSEDED, never re-cut** (its tag is pushed): it is an honest intermediate. Its
  release DIR has a `staged` row (17:33:02Z) → awaiting-ceremony pinned; when v0.31.47 is promoted the
  sweep will see supersession evidence only if a version-greater commit exists after its staged row —
  **the fix itself HOLDs it (dogfooding)**: no eviction without evidence, exactly as designed.
- **G2 census (journal-derived, fresh at refresh time):** current=v0.31.42 (pinned), previous=v0.31.37
  (pinned); `staged` rows exist for v0.31.45 (17:33:02Z) and v0.31.46 (18:30:21Z — the sibling lane's
  stage ran with module B live) → both awaiting-ceremony pinned; HELD no-journal-evidence set =
  {v0.31.41, v0.31.43, v0.31.44}; after this re-stage the tree also carries v0.31.47 (its own `staged`
  row). NOTHING on live is evictable: the next live sweep evicts nothing and writes `retention_pin_hold`
  rows naming the held dirs (the fix DEFERS live retention; it neither triggers nor blocks a sweep).
- **The one dir that becomes evictable:** v0.31.37 (commit row 03:10:20Z + later version-greater commit
  v0.31.42 10:24:12Z) once it loses the `previous` pin at the next promote — then evicted WITH a row,
  guarded by the liveness probe (the .37-era agent_dirs risk Ari flagged).
- **NO operator "release this dir" lever** (a no-evidence dir is only released by supersession
  evidence) — deliberate, future work.
- **Reviewer-named residuals (carried):** `sort -V` is GNU-only — on BSD the comparator yields empty ⇒
  everything HELD (safe direction; this host is GNU). A `readlink` failure on an existing symlink
  silently disengages the pointer gate (journal `current` remains pinned — negligible). Cmdline
  containment matches paths strictly BELOW the dir (`*/dir/*`); argv ending exactly at the dir path is
  unmatched (cwd/exe/root cover running-from cases).
- **LATE-ARRIVING PIN (historical):** the 4th pin arrived after the first dispatch wave; folding it in
  changed the eviction contract (evidence-affirmative), forcing A's amendment + C's REV2 + the §9
  fixture re-alignment. The stopgap release carried a known §9 RED until Phase 2 closed it.
- **ROW-TOKEN COUPLING:** the pack asserts the frozen strings verbatim; rewording breaks the pack BY
  DESIGN. `reasons=` is COLON-separated.
- **POINTER OWNERSHIP:** lane **84edce22** owns the pointer repair + the "pointer == journal.current
  AND target exists" invariant (their lane landed live repair + `test_release_pointer_check.py`); this
  lane never writes `releases/current`.

## How to verify

- `bash tests/packs/release_retention_eviction_pack.sh` → `RESULT: PASS=73 FAIL=0` (mutation leg:
  control evicts+journals; stripped ⇒ HELD + row-absent + dir survives).
- §9 block against this tree's lib.sh → `PASS=9 FAIL=0`.
- `bash -n scripts/upgrade/{lib,stage,promote}.sh` → clean.
- Live census (journal-derived, read-only): `python3 -c "import json;d=json.load(open('/home/nea/agents-ensemble/releases/state.json'));print(d['current'],d['previous']);[print(r.get('event'),r.get('detail')[:90]) for r in d['history'] if r.get('event') in ('staged','retention_evict','retention_refuse','retention_pin_hold')]"` —
  expect current=v0.31.42/previous=v0.31.37; `staged` rows for v0.31.45, v0.31.46, v0.31.47; ZERO
  retention_evict rows until a real superseding promote commits.
- `ls -1 /home/nea/agents-ensemble/releases/ | grep '^v'` → v0.31.37, .41, .42, .43, .44, .45, .46, .47
  (8 dirs vs keep-3 — over-retention is the expected steady state under fixed code).
- `readlink /home/nea/agents-ensemble/releases/current` → resolves to an EXISTING dir == journal current.

## Bash-raises used

none — no bash raise consumed. Long builds (PyInstaller, vite) ran under `proc` (background,
auto-kill budget); all sweeps/pack runs stayed inside the default budget. One `/bin/sh` substitution
error (`${L:0:9}`) aborted a sync-merge command mid-chain; detected via ancestry check and re-run
dash-safe — no state was lost.

## Complexity self-assessment

**medium** — unchanged from plan-open. Three file-disjoint modules; no daemon/schema coupling; the
re-dispatch added one bounded bounce per owner (G1 code fix to A; P2 re-baseline to C). The
evidence-affirmative strictness and the G1 fail-closed hold are my calls under the leader's
adjudication; both are disclosed above.
