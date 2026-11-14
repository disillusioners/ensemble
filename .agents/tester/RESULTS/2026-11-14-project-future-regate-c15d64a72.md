# Re-Gate Addendum: Project Future — PASS at merged tip `c15d64a72`

- **Date:** 2026-11-14 (UTC) — addendum to `RESULTS/2026-11-14-project-future-functional-gate.md`
- **Trigger:** original verdict pinned to `706837dbf`; branch moved (merge of `origin/latest` `5e6586cf3` = ENSEMBLE-32 ram-guard lane v0.30.5 era + lane polish).
- **Worktree:** `/home/nea/ensemble-src-wt-project-future` · branch `feature/project-future` · **HEAD `c15d64a729348bd2b50e68c2ef4074256eda4cbd`** (merge commit; parents `9f2448dcd` + `5e6586cf3`; 24 ahead / 0 behind `origin/latest`; clean, pushed)
- **Method:** focused re-gate — same pack designs re-executed on the merged tree (recon + 5 packs), zero live contact, read-only product code.

## VERDICT: ✅ **PASS** — 8/8 law clauses re-proven on `c15d64a72`; original advisory (C5) now FIXED and pinned; 0 lane-caused regressions; 0 merge-induced interference.

## Delta vs the originally gated tip `706837dbf`

| Area | Change | Effect on the law |
|---|---|---|
| Lane files changed | ONLY `daemon/tools/project_future.py` (W6d docs completion: list/remove `_FULL_DOCS` gained cap + eviction + purpose sentences; em-dash→`--` normalization) and `tests/unit/test_project_future_tools.py` (+`TestFullDocContract`, 3 parametrized nodes; 20→23 collected) | NONE on behavior; RESOLVES the original 🟠 advisory |
| `daemon/repositories/project/repository.py` | **byte-identical** vs gated tip | concurrency anchors stable (lock :1551/:1664-1665, guard :1589-1591) |
| Anchors shifted | `project_future.py` +14 lines (factory :121, tools :139/:187/:213; `_FULL_DOCS` 33-68 / 70-93 / 95-117) | accounted for by the docs hunks only |
| ENSEMBLE-32 merge | `daemon/tools/bash.py`, `proc_tools.py`, `api.py`, `services/ram_guard/*` enter the tree | zero cross-contamination: `project_future` diff hits only the docs hunks + the new pin |
| Census / baseline | unchanged: 261 names / 22 privileged / 130 migrations / baseline 217 rows, 0 drift | green |

## Per-law evidence at `c15d64a72`

| # | Clause | Verdict | Re-gate evidence |
|---|--------|---------|------------------|
| 1 | MAX 32 exact at boundaries | ✅ | A′: #1..#32 all `evicted==[]`, total 32; 35-row drift → add → 4 victims → exactly 32; `_MAX_ENTRIES==32`; positional `max_entries` → TypeError |
| 2 | FIFO canonical | ✅ | A′: #33→evicts #1 (exact 4-key dict, byte-equal summary), #34→#2 (CJK byte-equal), #35→#3; survivors #4..#35, min live seq 2 |
| 3 | Disclosure (incl. multi-row oldest-first, 300-char byte-equal) | ✅ | A′: self-heal victims seq [4,5,6,7] oldest-first, new seq 39, total 32; byte-equality ASCII + CJK/emoji (`true`/`true`) |
| 4 | Per-project independence | ✅ | A′: P1#33 evicts only P1#1; P2 untouched; symmetric |
| 5 | Seq determinism under identical timestamps | ✅ | A′: 32 rows identical `created_at` → #33 evicts exactly seq==1 |
| 6 | Concurrency invariants | ✅ | B′ 7/7: lock-first trace (`projects` SELECT stmt #0, FOR UPDATE absent on SQLite; PG dialect compile ends `FOR UPDATE`); **sabotage red/green** (green exit 0 / sab exit 1, false-success disclosure on the one-hunk-removed copy); unique-race → `ConcurrentWishlistUpdate`, count 32, victims restored; two-engine victims-staged window disclosed unforceable (SQLITE_BUSY); D18 narrowing (FK IntegrityError untranslated; `summary=None` → TypeError) |
| 7 | Tool surface / discoverability | ✅ | C′ 8/8 + C9 3/3: `tool_help(category)` section with trio only under `project_future` (no `project` name-prefix leakage); granted-14 keep 3 via both seams; ari/worker/giter/coder 0; deny wins both granularities; **per-doc contract now complete** (cap/eviction/purpose on all three docs) + `TestFullDocContract` 3/3; twins sha256 equal `3038efbf…`; census 261/22/not-privileged |
| 8 | Regression + census | ✅ | D′ reproduced EXACTLY: leg1 `26 failed, 339 passed in 139.03s` — failing set == the 26 collected nodes of `tests/test_project_history_api.py` (zero outside, 302 auth signature); leg2 with all 7 ambient `ENSEMBLE_AUTH_*` scrubbed → `26 passed`; no base-leg re-run needed (no new reds). E′: 80/80 (`TestFullDocContract` 3/3, both drift pins green). Recon: migrations 130 (full-prefix dup check empty), baseline `--check` OK 217/0 drift |

## Merge-interference check (new, ENSEMBLE-32)

- **Test-process interference: NONE.** Zero `ram_guard`/`ram_kill`/`ram_breach`/`SIGKILL`/exit-9 hits in any pytest log (A′/D′/E′/C′). All packs completed with expected exit codes.
- **Benign envelope only:** the bash TOOL itself appends `ram_breach_observed: true` / `ram_kill_refused: refused:leader-unreadable` to completed commands (daemon-side instrumentation, pre-existing observation from the original gate) — accounting noise, not interference.
- **Cross-contamination:** zero — `git diff 706837dbf..HEAD | grep project_future` hits only the lane's own docs hunks + pin.

## Disclosed limits (carried forward, unchanged)

1. **PG runtime — static/compile-only** (dialect compile shows `FOR UPDATE`; no live PG cluster, zero-contact discipline).
2. **SQLite victims-staged + collision two-engine window — physically unforceable** (RESERVED lock → `SQLITE_BUSY`); "victims restored" evidenced via the commit-raise path (same rollback code path).
3. **UI/web automation — N/A** (agent-tool-only surface; no daemon/frontend boot).
4. Boot-time allow-validation warnings unobservable without boot (warn-only; filter assertions cover both directions positively).

## Original advisory status

🟠 **RESOLVED** — per-tool `_FULL_DOCS` cap/eviction/purpose tokens now present on all three docs (C5 PASS), regression-pinned by `TestFullDocContract` (3/3). No open product defects.

## Artifacts (ephemeral /tmp)

`/tmp/pf-regate-a-3793811/` · `/tmp/pf-regate-b-3802131/` (b1 traces, b2 compiled, b3 responses, sab log) · `/tmp/pf-regate-c-3791373/` · `/tmp/pf-regate-d-20261114a/` (run1.log, run2.log, failed_ids.txt) · `/tmp/pf-regate-e-3794709/`

## Overall

- Law clauses 1–8 at `c15d64a72`: **8/8 PASS**
- New reds: **0** · Lane-caused regressions: **0** · Merge interference: **0**
- **Re-gate complete: READY**
