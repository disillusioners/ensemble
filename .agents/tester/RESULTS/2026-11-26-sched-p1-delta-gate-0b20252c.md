# DELTA GATE — v0.31.61 final landing merge `0b20252c`

**Date:** 2026-11-26 · **Gater:** tester

| | |
|---|---|
| **Merge under test** | `0b20252cce3fcbc230afa6444597735d287c47ee` |
| **parent1** | `d7c15d45f65037844471c3d2ff2a06d4ed9afeec` (= origin/latest, declares 0.31.60) |
| **parent2** | `a032e3f452858a3dbb8e122f3ee633bcfb804266` (branch tip, declares 0.31.61) |
| **tree** | `32de87d0b1df7b1bd09915d4ebd69421a47539d7` |
| **Prior gate's head (superseded)** | `3ce82bde24934a2048f3bb0de77cf29cb24cd6a7` (tree `6f2ae69c1e4ccc845516a0ac12e93012c2a6ee27`) |

**Purpose:** close the gap from the prior full gate's head (`3ce82bde2`) to the FINAL landing sha
(`0b20252c`) **by proof, not assertion**. Chain closed:
`3ce82bde2` → `ac175d142` (fix round #4) → `0b20252c` (fix round #5 + restored PACKS.md hunk + merge body).

---

## 1. VERDICT

**READY** — the delta `3ce82bde2 → 0b20252c` is **outcome-neutral**, proved three independent ways
(path set, dual-tree failed-set identity, whole-tree collection identity). The prior gate's
whole-tree head leg **transfers byte-for-byte** to `0b20252c` (`cmp` exit 0 on 42,014 node ids).

Two carried-forward findings remain **open** (F-1, F-2). Neither is introduced by this delta —
proven by blob identity — and neither blocks it. Both were already adjudicated non-blocking by the
prior gate and remain the leader's call.

---

## 2. LEG RESULTS

| Leg | Proved | Result |
|---|---|---|
| **L1** | base-leg artifacts collected at exactly `d7c15d45f` | **PROVEN — base REUSED, not re-derived** |
| **L2** | own worktree + venv provenance | **PASS** (3/3 trees) |
| **L3** | fence: per-variable PG scrub, 7× `ENSEMBLE_AUTH_*` by name, boot-sweep **pinned** | **PASS** (fence self-asserts, exit 97 on violation) |
| **L4** | delta path set + dual-tree failed-set comparison + whole-tree transfer | **PASS — byte-identical** |
| **L5** | decisive legs re-measured at `0b20252c` | **PASS — 91/43/3/87/96/8 + pack 282, all 0F/0E** |
| **L6** | counter-mutation teeth | **PASS — 19/19 CAUGHT (RED)** |
| **L7** | behavioural probes M-A/M-B/M-C/M-D + 503 | **GREEN-MINUS-F-1 — 23/23** |
| **L8** | source-gc P11 guard non-vacuity | **TEETH PROVEN / STRUCTURALLY VACUOUS AT THE MERGE** |
| **L9** | version assertion (POK cutguard-02) | **PASS — 5/5 declare 0.31.61** |
| **L10** | refresh two stale KV claim rows | **DONE — corrected, not deleted** |

---

## 3. L1 — BASE PROOF (REUSED)

`origin/latest` has **not** moved, so `d7c15d45f` is still the correct base for this merge. The prior
gate's base-leg artifacts live under `/tmp/gate61/l3/`.

| Check | Stored | Live / recomputed | |
|---|---|---|---|
| Recorded sha | `FINAL_REPORT.txt:5` + `STATE.txt` collection row | `rev-parse HEAD` | MATCH |
| Tree | `8d327362fb1e077b1cc0e0fb433cd95efeddc5e8` | same | MATCH |
| Node count | 41946 | 41946 (re-collected) | MATCH |
| **Node-list sha256** | `613d39885f3a8942a63b31eb3c3bf4ea5ece4c71640e56a99068f36b73b2b54f` | identical (`cmp` exit 0, 0 diff lines) | **MATCH** |
| Per-shard closure | 22 DONE rows | all delta=0, **0 offenders** | PASS |
| Leg closure | 36582 + 5364 | = 41946 = collected | TRUE |

**Decisive step:** a **live re-collection at the same sha** (149 s) compared byte-for-byte with the
stored list — not a re-read of the report.

**5 NOVERDICT nodes** (never PASS/FAIL on either leg; excluded from both FAILED sets; **NOT VERIFIED**):
`test_post_restart_arm_notify_drill_smoke.py::test_drill_smoke` ·
`test_compaction_escape_hatches_round2.py::TestLane1DedupYieldsToOverBudget::test_overbudget_bypasses_dedup_window` ·
`::TestLane2IterativeHalving::test_half_tail_over_budget_iterative_halving_engages` ·
`::TestLane4AuditGapDedupActiveOverBudget::test_dedup_does_not_silence_over_budget_shrink` ·
`test_compaction_never_blocked.py::TestFExistingTestPackStaysGreen::test_phase2_p1_anti_refire_test_renamed_pattern_still_passes`

**Correction to the lane brief:** the brief asserted `tests/performance/test_message_api_cost.py`
"must be ABSENT" from the node list. It is **present with 12 nodes** — "pre-excluded" refers to sweep
*execution*, not collection. Verified symmetric: 12 in base, 12 in head. The brief was wrong, not the
artifact.

---

## 4. L4 — DELTA PROOF (the heart of this gate)

### 4.1 Delta path set — EXACTLY 6, all test/docs (37 insertions / 19 deletions)

```
M  .agents/tester/PACKS.md
M  CHANGELOG.md
M  tests/packs/scheduled_tasks_acceptance.sh
M  tests/unit/tools/fixtures/module_level_inputs_a_base.json
M  tests/unit/tools/fixtures/tool_full_doc_scheduling_base.json
M  tests/unit/tools/test_module_level_inputs_a.py
```

### 4.2 Production deltas — ZERO

`daemon/` **0** · `agents/` **0** · `scripts/` **0** · `plugins/` **0** · `frontend/` **0** ·
`pyproject.toml` **0** · `uv.lock` **0** paths.

### 4.3 Dual-tree run — FAILED-SETS BYTE-IDENTICAL

Same 17-file delta-sensitive set (369 collected) on both trees; census deviations **0/17**.

| Run | OLD `3ce82bde2` | NEW `0b20252c` |
|---|---|---|
| 17-file set | RC=1, 64 s — `2 failed, 367 passed, 26 warnings in 55.64s` | RC=1, 68 s — `2 failed, 367 passed, 26 warnings in 56.20s` |
| acceptance pack | RC=0, 60 s — `279 passed` (9th pin not yet wired at that tree) | RC=0, 66 s — `282 passed` (9th pin adds 3 nodes, all green) |

| Form | OLD sha256 | NEW sha256 | |
|---|---|---|---|
| set / bare-id | `10b9cb3d6dc94a5778c3010935773994a68a67059a846a5f88c421fc6698303e` | *same* | **IDENTICAL** |
| set / grep-full | `730fdc8ede1ab94792cf5120098c317c6db31bdc3456213a918fee3f232e5298` | *same* | **IDENTICAL** |
| pack / bare-id | `e3b0c442…b855` (**EXPLICITLY EMPTY**) | *same* | **IDENTICAL** |
| pack / grep-full | `e3b0c442…b855` (**EXPLICITLY EMPTY**) | *same* | **IDENTICAL** |

`comm -3` on the node-id sets = **EMPTY**. The empty digest is reported as explicitly empty, never as
a silent pass.

### 4.4 METHOD CORRECTION — the prescribed grep is NOT comparable across trees

A naive `grep -E '^(FAILED|ERROR) '` over a pytest capture **also matches loguru stdout rows**
(`ERROR␣␣␣␣logger:file:line msg` — four spaces, single colons, no `::`). Those rows embed random
UUIDs and the per-run pytest tmp path, so the **raw** forms differ between the trees by design:
set/grep-full 39 vs 39 rows (different bytes), set/bare-id 31 vs 31, pack/grep-full **36 vs 37** (one
extra log record), pack/bare-id 29 vs 29.

Stripping to the pytest level makes **every one** of them identical. The comparison basis is therefore
the pytest-level form, derived **two independent ways that must AGREE**: (a) node-id discriminator
`^(FAILED|ERROR) [^ ]+::`, (b) the pytest short-test-summary section. Both rules were cross-checked
against the prior lane's independently-derived digests — same bytes. The extractor carries an explicit
vacuity guard: `e3b0c442…b855` is surfaced as *EXPLICITLY EMPTY*, never as a pass.

### 4.5 The two failures are BASE-ATTRIBUTABLE — proven, not assumed

`tests/test_phase8_docs_gate.py::test_changelog_has_unreleased_section_naming_commission`
`tests/unit/test_persona_neutrality.py::test_the_shipped_trees_carry_no_persona_lore_titles`

* the delta touches only the `v0.31.61` CHANGELOG block — `Unreleased` count **10/10** identical,
  `resource-cost-control` **4/4** identical;
* lore-token counts identical — `CHANGELOG.md` **29/29** (case-insensitive `emperor`), `PACKS.md` **7/7**;
  the delta's added/removed lines carry **ZERO** lore tokens across all 6 paths;
* all 10 cited persona hit lines (32/51/52/59/65/67/71/82/88/89) are **byte-identical** on both sides.

### 4.6 Whole-tree collection TRANSFER PROOF

The prior gate's head leg ran at `3ce82bde2`. Re-collecting at `0b20252c`:

```
NEW  : 42014 lines  sha256 29e30ea0ac68a34474c3c15f93b719164b5f7056974f37add5199f86b5e6d029
PRIOR: 42014 lines  sha256 29e30ea0ac68a34474c3c15f93b719164b5f7056974f37add5199f86b5e7056974f86b5e6d029
cmp exit 0 → IDENTICAL   (42014/43295 tests collected, 1281 deselected, 162.65 s)
```

The prior gate's whole-tree head leg **transfers byte-for-byte** to the final merge.

---

## 5. L5 — DECISIVE LEGS, re-measured at `0b20252c`

| Leg | Expected | Measured | RC |
|---|---|---|---|
| nine pack-gated pin files | 91 | **91 passed** | 0 |
| `tests/unit/tools/test_module_level_inputs_a.py` | 43 | **43 passed** | 0 |
| `tests/unit/tools/test_tool_full_doc_scheduling.py` | 3 | **3 passed** | 0 |
| `tests/test_scheduler_api.py` | 87 | **87 passed** | 0 |
| `tests/test_scheduler_adapter.py` | 96 | **96 passed** | 0 |
| `tests/integration/test_scheduled_tasks_e2e.py -m integration` | 8 | **8 passed** | 0 |
| `timeout 300 bash tests/packs/scheduled_tasks_acceptance.sh` | 282 | **282 passed**, 26 warnings | 0 |

**Census of `tests/test_scheduler_api.py` = 87**, class split **`TestScheduleListingTruthRest` 2** +
**`TestSchedulePhase1GuardsRest` 12** — re-derived independently by the tester from the collected id
list (remainder: 73 nodes across 15 classes).

---

## 6. L6 — COUNTER-MUTATION TEETH: 19/19 CAUGHT

Baseline (unmutated, full pin set): **221 passed**, 0 failed. Every mutation was applied, went RED for
the *right* reason, and was restored with **PRE sha256 == POST sha256**. Scratch clone left clean at
`0b20252c`.

| id | file+function edited | pin that caught it | verbatim failure line | caught |
|---|---|---|---|---|
| M-01 | `scheduling_service.py::row_next_run` — `_compute_next_run(...)` → `None, None, ""` | listing_truth + phase1_guards | `assert None is not None` | YES |
| M-02 | `row_next_run` — chain-derivation block overriding `_local/_utc` | listing_truth | `assert False` (startswith mismatch) | YES |
| M-03 | `listing_view` — `"window": window.model_dump()…` → `None` | listing_truth | `assert None is not None` (window) | YES |
| M-04 | `live_label_conflict` — `bool(_live_rows(…))` → `bool(_rows_named(…))` | namespace | `ValueError: label already exists: reused-label` | YES |
| M-05 | `resolve_label_row` — live-preference block removed → first-match | label_ambiguous + namespace | `assert 200 == 409` | YES |
| M-06 | `maintenance_seed.py::build_canonical_config` — reverted to chain shape | seed_canonical | `assert 'once' == 'weekly'` | YES |
| M-07 | `update_schedule` — dropped re-read | phase1_guards | `assert 'running' == 'stopped'` | YES |
| M-08 | `create_schedule` — ungated `_compute_next_run(config_json)` | phase1_guards | `assert '…03:30:00+00:00' is None` | YES |
| M-09 | `is_armed` — `getattr(status,"value",status)` → `str(status)` | phase1_guards | `assert True is False` | YES |
| M-10 | `update_schedule` — own-name short-circuit made unconditional | phase1_guards | `assert 1 == 0` (scan_calls) | YES |
| M-11 | `maintenance_seed.py::_write_chain_config` — `if promoted is None:` → `if False:` | seed_selfheal | `assert 'reenabled' != 'reenabled'` | YES |
| M-12 | label normalisation (strip) removed across router+service+repo | test_scheduler_api | `assert 200 == 409` | YES |
| M-13 | `routers/schedules.py::update_schedule` — adapter `_get_next_trigger_time` fallback restored | test_scheduler_api + phase1_guards | `assert '1999-01-01T00:00:00Z' is None` | YES |
| M-14/15 | `resolve_label_row` — ambiguous label `raise` → `return live[0]` | label_ambiguous + namespace | `assert 200 == 409` | YES |
| **M-16** | `_rows_named` — `raise LabelNamespaceUnavailable(…)` → `return []` | namespace_fail_closed + phase1_guards (**6 nodes**) | `assert 400 == 503` | YES |
| **M-17** | `tools/scheduling.py` `_full_doc_` Args text (`fires.` → `fires!`) | tool_full_doc_scheduling | `create_scheduling_tools/task_schedule: the _full_doc_ text moved` | YES |
| **M-18** | `TaskScheduleInput.message` Field description (`fires` → `fires!`) | module_level_inputs_a | `create_scheduling_tools/task_schedule: the OpenAI tool payload moved` | YES |
| **M-19** (new) | `TaskScheduleCancelInput.label` description → old text `first match wins on collision` | module_level_inputs_a | `create_scheduling_tools/task_schedule_cancel: the OpenAI tool payload moved` | YES |

**The four commissioned new pins map onto:** (a)+(b) 409 `SCHEDULE_LABEL_AMBIGUOUS` GET/DELETE →
M-05 / M-14-15 · (c) 503 `SCHEDULE_LABEL_CHECK_UNAVAILABLE` POST/GET/DELETE → M-16 ·
(d) `_full_doc_` byte-identity → M-17 · (e) re-baselined OpenAI-payload → M-18 (message leaf) +
M-19 (the `label.description` leaf the fixture was re-baselined for).

**Caveat:** the full 221-node suite was not re-run after every restore (only the owning subsets, to keep
each run under 5 min). Restore is proven hash-identical per file, and the baseline-all run is the
unmutated reference.

---

## 7. L7 — BEHAVIOURAL PROBES: 23/23 (GREEN-MINUS-F-1)

| probe | assertions | result | wall |
|---|---|---|---|
| `probe_ma_disarmed` (M-A) | 8 | PASS 8/8 | 17.78 s |
| `probe_mc_rename` (M-C) | 5 | PASS 5/5 | 15.42 s |
| `probe_md_parity` (M-D) | 3 | PASS 3/3 | 16.75 s |
| `probe_mb_seed` (M-B) | 4 | PASS 4/4 | 10.89 s |
| `probe_503_failclosed` (503) | 3 | PASS 3/3 | 15.11 s |

23 functions, 23 PASS, 0 FAIL/skip/error; one fresh pytest process per probe file.

* **M-A** — cancelled/disabled row renders next-fire **present-with-null** on tool list, REST list,
  REST GET, REST PUT and the UPDATE echo; `enabled`/`armed` visible on every surface **except the
  CREATE echo**; REST PUT on a CANCELLED row refused `409 SCHEDULE_ALREADY_CANCELLED` with no echo;
  ARMED+ENABLED neighbour renders a real next-fire (asymmetry control).
* **M-C** — rename onto a LIVE label → `409 SCHEDULE_LABEL_CONFLICT`, no write; onto a CANCELLED
  label → 2xx allowed; to its own name → 2xx no-op with the namespace scan **not run**;
  whitespace-padded `'  taken-label  '` → 409 naming the **stripped** label
  (`label already exists: taken-label`); two LIVE rows sharing a label → `409 SCHEDULE_LABEL_AMBIGUOUS`
  on GET and DELETE, snapshots unchanged.
* **M-D** — one armed row's instant **identical** across all four surfaces and equal to
  `svc.row_next_run(stored config)`; sentinel `__PAST_LATENESS_CAP__` unreachable on all four;
  tokenized static check finds no `_get_next_trigger_time` in `daemon/routers/schedules.py`.
* **M-B** — `CANONICAL_SEED_SOURCE_ID == uuid5(NAMESPACE_DNS, CANONICAL_SEED_NAME)`; STOPPED-but-ENABLED
  canonical row self-heals (`action="reenabled"`, no chain/run_at graft); FAILED promotion **LOUD**;
  no-row-match **LOUD** (`MaintenanceSeedError`).
* **503** — POST/GET/DELETE each `503` with `SCHEDULE_LABEL_CHECK_UNAVAILABLE`; row-count and field
  snapshots **unchanged**; no write.

---

## 8. L8 — SOURCE-GC P11 GUARD: TEETH PROVEN, VACUOUS AT THE MERGE

Node (class qualifier required — an unqualified id yields rc=4, a false green):
`tests/unit/repositories/test_source_gc_repository.py::TestP11BootNoBehaviorChange::test_post_merge_span_exactly_sanctioned_hunk`
Span = `rev-list --first-parent --merges -1 HEAD` → `merge..HEAD`.

| Shape | Construction | Result |
|---|---|---|
| **A** | as-given (HEAD = the merge) | RC=0 `1 passed` — span **EMPTY** → **VACUOUS** |
| **B** | house shape: one empty commit above the merge (`7ee3c9d5d`) | RC=0 `1 passed` — still empty (an empty commit changes zero tree bytes) → **VACUOUS GREEN** |
| **C** | unsanctioned hunk in `daemon/services/scheduling_service.py` | **RC=1 RED** — `AssertionError: unexpected post-merge drift in NOT-TOUCHED paths:` + `5 +++++` → **TEETH PROVEN** |
| **D1** | same blanket path, hunk carries a `decision` marker | **still RED** — the blanket check is **marker-blind**; markers cannot buy a green there |
| **D2a/b** | `manager.py` hunk, unmarked → marked | **RED → GREEN** — the marker mechanism **discriminates** on its true home |

**Controls:** `parent1..merge` on `scheduling_service.py` = `1 file changed, 349 insertions(+), 17 deletions(-)`
— the merge's own drift in a blanket-checked path, **never evaluated** by the guard.
`merge..parent2` = structurally empty (parent2 is an ancestor of the merge).

**Correction to the prior gate's record:** `SANCTIONED_LANE_MARKERS` is **9** markers, not 3 —
`decision, memoir, group chat, compact-note, self-service-promote, checkpoint-retention-lane,
boot-recovery, tool-usage, central maintenance`. Shape C's hunk deliberately avoids all nine.

**Answer to the commission's question:** the guard **cannot** be made non-vacuous at `0b20252c` as a
check of the merge's *own* content — the span is `merge..HEAD`, empty by construction when HEAD *is*
the merge, while the merge's real 349+/17− lives in `parent1..merge`, a range the node never reads.
The cure requires a **test change** (evaluate `parent1..merge` / `d7c15d45..HEAD`) or
leader-sanctioning the merge's own `scheduling_service` range. But the guard is **not inert** — teeth
proven on every layer.

---

## 9. L9 — VERSION ASSERTION (POK cutguard-02)

All **5** version-bearing files declare **0.31.61** at `0b20252c`, each ≥ parent1's **0.31.60**:

| file | merge | parent1 |
|---|---|---|
| `daemon/__init__.py` | `3: __version__ = "0.31.61"` | `0.31.60` |
| `pyproject.toml` | `3: version = "0.31.61"` | `0.31.60` |
| `uv.lock` | `482: version = "0.31.61"` (project's own `name = "ensemble"` entry) | `0.31.60` |
| `tests/test_api.py` | `227: assert data["version"] == "0.31.61"` | `0.31.60` |
| `CHANGELOG.md` | `8: ## [v0.31.61] — 2026-11-26` | `8: ## [v0.31.60] — 2026-11-26` |

`git grep -n -E "0\.31\.5[0-9]|0\.31\.60" 0b20252c -- daemon/__init__.py pyproject.toml uv.lock tests/test_api.py`
→ **rc=1, empty** (PASS). CHANGELOG newest-first confirmed; `[v0.31.57]` absent.

---

## 10. L10 — STALE KV CLAIM ROWS CORRECTED (not deleted)

`wt.claim.sched-p1-base` and `wt.claim.sched-p1-gate` both asserted worktree paths that no longer
exist. Both rows now state per-path reality (**RECLAIMED / absent** vs **still present**) and that
their gates are **CLOSED**. `wt.claim.sched-p1-gate61b-delta` registered for this lane.

---

## 11. OPEN FINDINGS (carried, NOT delta-introduced)

### F-1 🟠 OPEN — `ScheduleCreateResponse` exposes no `enabled`/`armed`
`daemon/services/scheduling_service.py:178-184` — `ScheduleCreateResponse` carries
`source_id/label/status/next_run_at_local/next_run_at_utc/tz_warning`; `ScheduleUpdateResponse`
(`:275-284`) carries `enabled`/`armed`. The CREATE echo therefore cannot satisfy the commission's
"the create/update echo … exposes `enabled`+`armed`" clause. **PROVEN base-attributable** by blob
identity: `scheduling_service.py` = `bc58bf7729eb22a8b6b2d1de96cc7b3987ed05f7`,
`schedules.py` = `1db3a73ceabe5971e793a1944906cfcf03692f1c`,
`tools/scheduling.py` = `421cf3b8d91a60741f42a21fc4674e883efcaf41`,
`maintenance_seed.py` = `43acf9d36e53640e49129b7c7a6e5ea0bba35dbb` — **all four byte-identical** at
`3ce82bde2` and `0b20252c`. Leader's call (accept, or add 2 fields + a pin).

### F-2 🟠 OPEN — the P11 source-gc guard is structurally vacuous AT THE MERGE
See §8. Teeth proven; the merge's own range is never evaluated.

### F-3 🟢 NOTE — `PACKS.md` PACK-TIMEOUT MISMATCH
The PACKS.md sched-p1 block documents `timeout 300` for the pack while
`tests/packs/scheduled_tasks_acceptance.sh:13` declares `timeout 600`. Raised by the parallel
release-review council as MINOR-N2; not a behavioural risk (300 s bound is the tighter one) but the
ledger and the script disagree. Doc-only, leader's call.

---

## 12. EXPLICITLY NOT VERIFIED

1. The **5 NOVERDICT nodes** — never PASS/FAIL on either leg of the reused base; excluded from both
   FAILED sets. Inherited from the prior gate, not re-adjudicated here.
2. **The whole-tree sweep was not re-run** at `0b20252c`. The transfer rests on (a) the byte-identical
   42,014-node collection, (b) zero production-path delta, and (c) the dual-tree failed-set identity.
   A re-run would cost ~2 h and is not required by the transfer argument.
3. **The full 221-node pin suite was not re-run after every mutation restore** (owning subsets only,
   to keep each run under 5 min). Restore proven hash-identical per file.
4. **Push / tag / promote / stage / daemon restart were not exercised** — outside the fence.
5. **F-1's remedy was not designed or applied** — reported as an open product gap, not fixed.
6. `scripts/upgrade/**` was not exercised by this delta gate.

---

## 13. FENCES HONOURED

No push · no tag · no promote · no settings flip · no daemon restart · no live-system contact ·
no Docker · no worktree removal · no ref mutation in `/home/nea/ensemble-src` (HEAD still
`bf4bfde6e`) · no commit · no live/prod DB contact · port 8088 untouched · no process killed.
`ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned**, never `env -u`'d. PG family scrubbed
**per variable by name** (`env -u POSTGRES_*` is a silent no-op).

**Trees:** `/home/nea/ensemble-src-wt-gate61b-0b20252c` (mine, @ `0b20252c`, porcelain = this report
only) · `/home/nea/ensemble-src-wt-gate61-l3base` (@ `d7c15d45f`) ·
`/home/nea/ensemble-src-wt-gate61-gated` (@ `3ce82bde2`) · `/tmp/gate61b/scratch-mut` ·
`/tmp/gate61b/scratch-gc` · anchor `/home/nea/ensemble-src-wt-sched-p1-merge` @ `0b20252c` — **untouched**.

**Evidence root:** `/tmp/gate61b/` (`L1/ L2/ L4/ L4-old/ L4-new/ L5/ L6/ L7/ L8/ FS/` + `fence.sh`
+ `failedset.sh`).

*This report is UNTRACKED (no commit) — the fence forbids ref mutation and this gate changes no code.
Follows the prior gate's precedent.*
