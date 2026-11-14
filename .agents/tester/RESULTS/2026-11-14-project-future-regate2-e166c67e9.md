# Re-Gate Addendum #2: Project Future — PASS at tip `e166c67e9`

- **Date:** 2026-11-14 (UTC) — second addendum to `RESULTS/2026-11-14-project-future-functional-gate.md`
- **Trigger:** branch moved to `e166c67e9` (merge of `origin/latest` `6ec7b31bd` = Sonic routing lane incl. `daemon/tools/pok.py` + `pok_injection.py` behavioural changes and a Sonic-side `.prompt-artifacts.sha256` re-stamp; our addendum commit `7746b22c7` included).
- **Worktree:** `/home/nea/ensemble-src-wt-project-future` · branch `feature/project-future` · **HEAD `e166c67e999e8b03f74e3da2c3bedae813eff3fd`** (clean; 26 ahead / 0 behind; version in tree 0.30.6)
- **Method:** quick re-gate — identical A′–E′ pack designs re-executed (state-pinned per pack), plus two merge-specific checks (baseline union; Sonic cross-effect scan).

## VERDICT: ✅ **PASS** — 8/8 law clauses re-proven at `e166c67e9`; zero deltas vs the `c15d64a72` re-gate; zero merge interference.

## Per-clause evidence

| # | Clause | Verdict | Q2 evidence |
|---|--------|---------|-------------|
| 1 | MAX 32 exact at boundaries | ✅ | A″ 10/10: #1..#32 keep (all `evicted==[]`); 35-row drift → 4 victims → exactly 32; `_MAX_ENTRIES==32`; positional `max_entries` → TypeError |
| 2 | FIFO canonical | ✅ | A″: #33→#1, #34→#2, #35→#3 (exact 4-key dicts); survivors #4..#35; strict seq DESC |
| 3 | Disclosure (multi-row oldest-first; 300-char byte-equal) | ✅ | A″: self-heal victims seq [4,5,6,7] oldest-first, new seq 39, final 32; ASCII + CJK/emoji byte-equality True |
| 4 | Per-project independence | ✅ | A″: P1#33 evicts only P1#1; P2#33 only P2#1 |
| 5 | Seq determinism under identical timestamps | ✅ | A″: 32 identical `created_at` → evicts exactly seq==1 |
| 6 | Concurrency invariants | ✅ | B″ 13/13: lock-first trace (`projects` SELECT stmt [0]; FOR UPDATE absent on SQLite; PG compile ends `FOR UPDATE`); **sabotage pair** green exit 0 / sabotaged exit 1 (one-hunk guard removal, false-success disclosure; provenance asserted to the /tmp copy); unique-race rollback restores the 32-id victim set; D18 narrowing (`_is_seq_unique_race`: PG named-index True / SQLite column-pair True / FK+NOT NULL False); lane repo files verified ABSENT from the Sonic change-set |
| 7 | Tool surface / discoverability | ✅ | C″ 27/27 + C9 3/3: help listing trio only under `project_future` (owners == `['project_future']`); leader 3/3 via both seams, ari/worker/giter/coder 0 via both; deny wins both granularities; per-doc contract complete; twins sha `3038efbf…` equal; census 261/22/not-privileged |
| 8 | Regression + census | ✅ | D″ reproduced EXACTLY: `26 failed, 339 passed in 137.25s`; failing set == the 26 collected nodes of `tests/test_project_history_api.py` (zero outside); leg 2 with all 7 ambient `ENSEMBLE_AUTH_*` scrubbed → `26 passed`. E″ 80/80 (`TestFullDocContract` 3/3; both drift pins green). C″: baseline `PROMPT-BASELINE: OK — 217 versioned artifact(s) match`, exit 0, 0 drift; migrations 130, no dup full-prefix |

## Merge-specific checks (new in this round)

**(a) Baseline union — PASS.** `--check --tree .` → `PROMPT-BASELINE: OK — 217 versioned artifact(s) match .prompt-artifacts.sha256` (exit 0, 0 drift). Our 3 rows verify exactly once each:
`9ee75e1f…  agents/leader/rule.md` · `3038efbf…  agents/reviewer/workflow.md` · `3038efbf…  agents/reviewer[v2]/workflow.md`.
Duplicate-path check (`awk '{print $NF}' … | sort | uniq -d`) → EMPTY (both lanes' re-stamps did not double-row any path).

**(b) Sonic `pok.py`/`pok_injection.py` cross-effect — NONE.** `git diff --name-only c15d64a72..HEAD` contains NONE of `daemon/repositories/project/*`, `daemon/tools/project_future.py`, `daemon/tools/_tool_registry.py`, `daemon/tools/instance.py`; grep of the full diff for `project_future|CATEGORY_MODULES|KNOWN_TOOL_NAMES` hits only RESULTS-type report lines (no production code). Census unchanged (261 tool names, 22 privileged, trio present).

**(c) Adjacent same-result — EXACT.** Same 26 auth-poison reds (identical classification: pre-existing, ambient `ENSEMBLE_AUTH_ENABLED` family 302s), 339 green; green under full scrub. No new reds; pre-existing-reds protocol not needed (zero failures outside the known file).

## Deltas vs the `c15d64a72` re-gate

- Surface harness grew 23 → 27 pack nodes (C10 baseline-union + C11 Sonic cross-effect are new scenario groups); all prior verdicts reproduce.
- Core law / concurrency / lane suite: identical results (10/10, 13/13 + sabotage pair, 80/80).
- **New minor finding (pre-existing at ALL three tips — surfaced by a tighter Q2 comparison, NOT merge-caused):** `created_at` wire-shape inconsistency — the add response captures `to_dict()` pre-commit (tz-aware → `+00:00`) while `list`/`evicted` re-reads are naive (no offset), so the same row reports two different `created_at` strings across add-vs-list. The naive-on-read shape is plan-documented (R8/risk 13); the deviation is the add-response side (B1 pre-commit capture). No law clause covers `created_at` byte-equality (law 3 is the 300-char summary — enforced). **🟠 minor, non-blocking**; fix = `timezone=True` on the column and/or normalize in `to_dict()`.

## Disclosed limits (unchanged)

1. PG runtime — static/compile-only (dialect compile proves `FOR UPDATE` emission; no live PG, zero-contact).
2. SQLite victims-staged + collision two-engine window — physically unforceable (RESERVED lock → `SQLITE_BUSY`); "victims restored" evidenced via the commit-raise path (same rollback code path).
3. UI/web automation — N/A (agent-tool-only surface).
4. Boot-time allow-validation warnings unobservable without boot (warn-only).

## CLEARED TO STAGE AT `e166c67e9`

All 8 law clauses PASS, zero new reds, zero merge interference, baseline union clean. The 🟠 `created_at` consistency nit is the only open observation and does not block staging.

## Artifacts (ephemeral /tmp)

`/tmp/pf-q2-a/` · `/tmp/pf-q2-b-3818372/` · `/tmp/pf-q2-c-*/` · `/tmp/pf-q2-d-3817312/` · `/tmp/pf-q2-e-3819051/`
