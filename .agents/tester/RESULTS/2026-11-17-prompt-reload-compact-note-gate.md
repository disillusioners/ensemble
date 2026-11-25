# Compact-Note Gate — `prompt_reload(compact, compact_note)`

**Lane:** `feature/prompt-reload-compact-note`
**Worktree under test:** `/home/nea/ensemble-src-wt-prompt-reload-compact`
**Branch tip validated against:** `e387d2297f404072b29a598b25a2e0f7d95e1f1c` (never pushed; no commit made by this gate)
**Base for dual-side legs:** `858a85e31489c7db9e9275955e63c021cfc5a2c4` @ `/home/nea/ensemble-src-wt-prompt-reload-compact-base`
**Gate date:** 2026-11-17 UTC
**Gate type:** feature-lane Tier-1 blast-radius gate + scoped regression + live-turn smoke (NOT a release gate)
**Fence:** `POSTGRES_DB=ensemble_dev`, `ENSEMBLE_SELF_ENV=dev`, `DAEMON_PORT=8079`, `PORT=8079`, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`, whole `ENSEMBLE_AUTH_*` family (7 names) unset — per-variable only (`env -u POSTGRES_*` is a silent no-op in bash). Ports 8088 (ensemble self-system) and 9797 (live ensemble-prod) never touched.

---

## 0. Headline

| # | Gate item | Verdict | Headline measurement |
|---|---|---|---|
| 3a | Registered lane pack `prompt_reload_compact_note_unit` | **PASS** | 80 passed / 0 F / 0 E, `RESULT: PASS`, exit 0, 8 s |
| 3b | Adjacent `tests/unit/services/` compaction family | **PASS** | 196/196 green; HEAD red set ∅ == base red set ∅ |
| 2 | Read-after-write pin — exists AND bites | **PASS** | ARM A green (unmutated clone) → ARM B counter-mutation REDDENS |
| 5 | Refusal classifier misclassification rate | **PASS** (disclosure accurate) | fail-open miss rate **14/46 = 30.4 %**; fail-closed 10/20 benign flagged |
| 6 | Default-path byte-identity (`compact=False`, no note) | **PASS** | byte-identical to base (9158 B, sha256 `e9756546…`), `cmp` exit 0 |
| 7 | Variant-B micro-window (crash after settle) | **CONFIRMED-BENIGN** | swapped channel already in checkpoint; arm consumed; 0 agent invocations on resume |
| E | ensure.md Core (6 executed + 1 by-reference) | **PASS** | 6/6 executed PASS, 0 FAIL, 0 non-quarantined failures |
| 1 | Live-turn smoke (a)–(e) | **PASS** (a)(c)(d) · (b) END recorded · (e) CANNOT-VERIFY | PINNED @ `e387d2297`; swap lands 4/4 turns; **daemon-rendered note occurrences = 0** (C1 REFUTED); receipt truthful but **omits the model id** |
| 4 | Group-atomicity, real parallel batch | **PASS** | real parallel batch (`prompt_reload` + `time` in ONE `AIMessage`); the sibling `ToolMessage` was **DROPPED with the group** (`ToolMessages=0`) |
| + | **PROBE #1** — announced span ≠ compacted span | **CONFIRMED** | Δ = 1 in **every** turn (A/B/C/D); exchange group COUNTED on the append side, STRIPPED on the swap side |
| + | **PROBE #4** — compaction claimed, nothing compacted | **CONFIRMED** | doc claims "summarized messages #1–#3" while **all 3 remain verbatim**; regime `len(groups) ≤ recent_window(10)`; clean refutation in the long regime |
| — | Affected-symbol widened set (leader deliverable) | **1 net-new red** | see §12 — 137-file direct set + 295-file focused slice (8163 nodes) + 1766-file transitive closure; the ONLY net-new red across the whole transitive set is the known-dispatched stale pin |
| — | Core-seam adjacency dual-side leg | **1 deterministic net-new** | the known-dispatched stale pin (3/3 red@HEAD, 3/3 green@BASE); **0** among the 3 adjudicated candidates — all FLAKY; 51 pre-existing CONFIRMED |

**Base red-set for the captured surfaces is ∅ (341 + 196 nodes).** Consequently ANY red on the branch leg is **net-new by construction** — nothing is inherited. Every red recorded below is classified against that rule.

### 0.1 COVERAGE BOUNDARY — "∅" is meaningless without it

Adopted as house practice (reviewer's view, 2026-11-17): scope by **affected symbol**, not file name; take the **transitive** set of test files that import or assert on those symbols; capture the base red-set over that set **at the merge-base**; name **every** red node individually; and **always state the coverage boundary** when a base red-set is reported as empty.

| surface | selection rule | files | nodes | runs | base red-set |
|---|---|---|---|---|---|
| **A** | name pattern `test_prompt_reload*` + `test_prompt_refresh_primitive` | 4 | 90 (42+20+15+13) | 2 | **∅** |
| **B** | registered pack `compaction_unit_test` → `tests/unit/test_compaction.py` | 1 | 130 | 3 | **∅** |
| **C** | 3 compaction-scoped files + `services/test_proactive_compaction_ns_trap_fix.py` | 4 | 121 | 3 | **∅** |
| **D** | `ls tests/unit/services/ \| grep -Ei 'compact\|proactive'` | 9 | 196 | 3 | **∅** |
| **E** | adjacency by seam entry points (`build_instance_graph`, `build_compaction_doc`, `_resolve_proactive_enabled`, `COMPACTION_TARGET_RATIO`, `InstanceMessagingService`), excluding `tests/integration/ postgres/ e2e/ message_queue_redesign/` | 97 | 2474 | 4 shards/leg | **52 reds** (named in §7) |

**What each ∅ does and does not cover:**
- **∅(A)** covers only 4 name-matched files / 90 nodes. It does **not** cover any test file that asserts on a changed symbol without matching a name pattern — which is precisely how the `REFUSAL_CODES` red was able to hide.
- **∅(D)** covers only the 9 `compact|proactive`-named files under `tests/unit/services/`. It does **not** cover the rest of `tests/unit/services/`.
- **∅(B) ∪ ∅(C)** cover the registered compaction pack and 3 compaction-scoped files.
- **Surfaces A–D together (341 + 196 = 537 nodes)** are a *name-pattern* selection, **not** an affected-symbol selection. They are a LOWER BOUND on the base red-set.
- **Surface E** is the widest base measurement taken (2474 nodes, dual-side), but it is an *adjacency* selection, not a transitive-symbol closure either.

**Path note that matters for the known red:** `test_prompt_refresh_primitive.py` lives at **`tests/unit/services/test_prompt_refresh_primitive.py`** (not `tests/unit/`) — verified present at base `858a85e31` and at tip. It was therefore **inside base surface A at 42 nodes, GREEN at base**. The "not matched by either pattern" statement is true of the *path* patterns, but the file was captured and green.

**The boundary is closed by the affected-symbol widening** (§12) — which takes the **transitive** closure of test files importing/asserting on the changed symbols, not a direct grep. That leg is now reported; its own residual boundary is stated in §12.4 (1471 of the 1766 transitive files enumerated but not measured, with the reason).

### 0.2 LEADER CORRECTION — verified, and it changes how the pack must be treated

**A relayed claim was FALSE and has been corrected by the leader before it reached this report**: the claim that the lane pack's file set *"is now DERIVED FROM THE CHANGED SYMBOLS (a symbol-driven grep → 11 files), not just its own files."* **This report never relayed that claim** (it records only "the lane pack lists only the 3 files at `e387d2297`" and "the in-flight fix adds the file"), but the correction is material and has been **verified directly against the tree**:

- At the amended tip `0c3ad907f`, `test/packs/prompt_reload_compact_note_unit.sh` carries the **false header claim in its own comment** — `:9-10`: *"The file set is selected by SYMBOL, not by name"* — over what is actually a **STATIC path list** (`:15-28` enumerating the files; the invocation at `:74+`). **No run-time derivation exists.**
- What is TRUE: the coverage at the amended tip is **correct** (the previously-missed `test_prompt_refresh_primitive.py` IS included, and the pack passes); the list was *rationaled* by symbol during the fix, but it is **hand-maintained**.
- **The symbol-driven selection and the pack's static list DISAGREE — a finding, per the leader's rule.** Running the symbol-driven selection over the lane's 43 changed symbols finds **one file still missing** from the pack: `tests/unit/test_symptom_repair_partition.py`, pulled in by `make_remove_all_sentinel` (the swap path calls it at `daemon/graph.py:13796`/`:13818` at `e387d2297`; that file pins the sentinel's shape at `:237-240` — `test_make_remove_all_sentinel_uses_langgraph_constant`, asserting `isinstance(s, RemoveMessage)` and `s.id == REMOVE_ALL_MESSAGES`). **Independently confirmed by this gate's own derived set**: the file IS in the widening worker's `transitive_all.txt` — the symbol-driven selection found it, the pack's static list does not, and adding it is already dispatched.

**Consequences applied by this gate:**
1. **The pack is NOT self-maintaining.** Its file set is a curated list; a future lane that changes a symbol must add the dependent test file **by hand**. Durable symbol-DERIVED selection is a follow-up, not shipped.
2. **The symbol-driven selection is used as a CHECK against the pack's list, not a description of it.** Disagreement is reported as a finding — see above.
3. **A coverage-boundary disclosure for this gate's own widening leg:** the widening worker *derived* `test_symptom_repair_partition.py` (it is in `transitive_all.txt`) but its **focused slice** ("transitive ∧ lane-vocabulary") **excluded it from every measured shard** — so the file's green/red status at `e387d2297` was *unmeasured*. **MEASURED since, at both legs, on a dedicated single-file pack: GREEN at both — 29 passed, 0 failed, 0 errors, 6/6 attempts (3× pinned HEAD + 3× base), `grep -cE '^(FAILED|ERROR) '` = 0 in every transcript.** The node of interest `test_symptom_repair_partition.py::TestP6SentinelElementZero::test_make_remove_all_sentinel_uses_langgraph_constant` **PASSED at both legs** (attribution verified with a supplementary `-v` run per leg, since `-q` cannot attribute counts to a single node). The classification is stronger than the counts: the file is **byte-identical across both legs** (sha256 `9c916274ef48b4386261ee0660ced32c0de4d7560c2fcaa59ef7734e166b610b`, 20259 bytes, node at `:237` in both), so its verdict is **structurally incapable of being net-new** — the branch changed nothing it asserts on. This is the **baseline** for the file the developer lane is adding to the pack.

**Current tree state (post-correction):** the branch tip is now **`0c3ad907f`** (reviewer-pinned: porcelain empty, never pushed, not merged), with a final commit in flight clearing **six** reviewer blockers: a committed debug print in the swap failure path (raw traceback to stdout — also a content-free-log-doctrine violation); an unpinned PROBE-#4 fix; a partial-write receipt string that cycle 2 made dishonest; a guideline sentence false under failover (announced `#1–#20` vs compacted `#1–#22` when the reply is served by a different model window); five unpinned agent-facing refusal messages; and the missing pack file. **This gate's interim leg remains measured against `e387d2297`; the final-tree leg will be dispatched on the amended tip.**

**Measurement expectations updated for the final leg** (per the leader): the announced span and the compacted span now **AGREE** in the normal case (both sides use `len(kept_msgs)`); span sizing is governed by the **10-group recent window**, not a token threshold; the real context window on this host is **700,000** (proxy aliases are not in the registry, so `get_model_context_limit` resolves them to `DEFAULT_CONTEXT_LIMIT`; `context_window=4000` is dead code — **observing 4000 is a resolver bug, not the designed fallback**). Note that at `e387d2297` the spans did NOT agree (Δ=1 in every turn — §6.4); the agreement claim describes the amended tip and must be re-verified there.

---

## 1. CONTAMINATION — the tree moved under the gate

**Anomaly `9c71500e` (critical).** During the gate, a concurrent developer lane wrote **uncommitted** modifications directly into the worktree under test. The branch TIP never moved (`e387d2297` throughout), but the working tree went clean → 11 modified tracked files:

```
daemon/compaction.py · daemon/config.py · daemon/constants.py · daemon/graph.py
daemon/services/prompt_refresh.py · daemon/tools/prompt_reload.py
test/packs/prompt_reload_compact_note_unit.sh
tests/unit/services/test_prompt_refresh_primitive.py
tests/unit/tools/test_compact_note_graph_wiring.py
tests/unit/tools/test_prompt_reload_compact_note.py
tests/unit/tools/test_prompt_reload_refusals.py
```

mtimes: 09:33:38, 09:33:56, 09:40:55 (the two production files), 09:41:28, later 09:43–09:49. Snapshot: `/tmp/compact-gate-contamination/CONTAMINATION_SNAPSHOT.txt`.

Consequences:
- Any run that imported `daemon` after **09:40:55** measured a tree that is NOT `e387d2297`.
- The dev daemon runs uvicorn with `--reload` (`dev.sh:159`), so it **hot-reloaded source mid-turn**.
- The first live smoke straddled the mutation: `tail_spliced=3` matches the writer's new `min_window=3` — i.e. that turn executed the developer's in-flight code.

Containment: a pinned tree at exactly `e387d2297` was built with `git archive e387d2297 | tar -x` (reads the COMMITTED object, physically unable to carry uncommitted bytes) + `PYTHONPATH` so `import daemon` resolves to the pin while third-party deps resolve from the worktree venv. **The developer's WIP was NOT reverted or cleaned** — that is their lane's call, and the brief forbids it.

### 1.1 LEG VALIDITY under the "a pack measured on a dirty tree is INVALID" rule

Leader ruling 2026-11-17: **a leg measured against a dirty worktree is INVALID** — report it as such, not as a pass and not as a failure of the code. Every leg of this gate is therefore classified by the tree it actually measured:

| leg | measured on | VALID? | basis |
|---|---|---|---|
| 3a lane pack | source worktree, **clean before AND after** | **VALID** | ran before the first writer mtime (09:33:38); clean/clean |
| 3b adjacent family | source worktree, **clean before AND after** | **VALID** | clean/clean; base leg from the clean base worktree |
| 2 read-after-write pin | **own clone at `e387d2297`** (ARM A + ARM B) | **VALID** | the A/B arms are the load-bearing evidence and they are pinned; the worktree run in that leg was dirty and is DISCARDED as redundant |
| 5 refusal classifier | source worktree (dirty after 09:40:55) | **VALID — by invariance, not by cleanliness** | the classifier body is **byte-identical** between the pin and the dirty tree (same 12 markers, same `[:120]` head slice, same `any(marker in head …)`); the measurement is invariant to the mutation |
| 6 default-path bytes | **PINNED** re-capture | **VALID** | pin porcelain empty before/during/after; `cmp` exit 0 vs base |
| 7 Variant-B micro-window | **pinned** `git archive` extraction | **VALID** | `daemon.graph.__file__` asserted under the scratch base |
| ensure C2 concurrency pack — 1st run | source worktree, **dirty (11 files)** | **INVALID** | dirty tree ⇒ leg invalid, NOT a code failure; re-run at the pin dispatched |
| ensure C2 concurrency pack — re-run | **PIN** | **VALID** | pending |
| graph-adj HEAD leg | source worktree, **dirty** | **INVALID** as a verdict on `e387d2297` | self-evidenced (reported the stale pin as `== 14`, true only of the dirty tree); 3× budget + 51-node re-run at pinned HEAD dispatched |
| graph-adj BASE leg | clean base worktree | **VALID** | clean/clean |
| 1(a,b,d,e) + 4 live — 1st run | source worktree, **straddled**, daemon `--reload` hot-reloaded | **INVALID** | ran the developer's in-flight code (`tail_spliced=3` matches their new `min_window=3`) |
| 1(c) note duplication | **pinned source** (`git show e387d2297:…`) | **VALID** | static/structural; independent of any live run |
| 1(a,b,d,e) + 4 live — pinned re-run | **PIN** | **VALID** | pending |

**This gate's scope is an INTERIM leg against a superseded sha** (`e387d2297`). The branch is entering another fix cycle (two additional blockers plus the tidier's deferred set), so the final verdict belongs to the post-fix tree. Every measurement below records the sha it was taken at, and no leg was silently re-baselined.

**Lessons for future gates** (see `LESSONS/`):
- A pinned sha is NOT a pinned measurement while the working tree is dirty. `git status --porcelain` before AND after is load-bearing; record the sha too.
- The source-of-truth read for a pinned anchor is `git show <sha>:<path>` — it reads the committed blob and is immune to a dirty tree.
- The dev daemon's `--reload` turns a concurrent source write into a mid-turn behaviour change. For a gate, boot without `--reload`.
- **Classify each leg by its IMPORT WINDOW, not by its declared sha.** A leg that imported `daemon` before the last production-file write is valid; one that imported after it is not. Legs whose result is *invariant* to the mutation need no re-run (the refusal classifier was byte-identical between the pin and the dirty tree, so item 5 was not re-run).
- **A pinned tree is not cwd-independent** — see §5.2.1. Assert `daemon.__file__` and every subpackage's `__file__`, before and after, in every pinned lane.
- **Disk is a gate resource.** The root filesystem was at 98% / 6.1 G free; each clone+venv costs ~600 MB. One pin must serve every contaminated leg — re-running `uv sync` per leg is what burns the disk. The `git archive` + `PYTHONPATH` technique costs ~0 MB.
- **A dirty-file list from a dispatch briefing is a LOWER BOUND.** The clone worker found **11** dirty files where the brief named 6, with mtimes advancing *after* dispatch. Re-read `git status --porcelain` at the END of a pin task and diff every dirty file, not just the named ones.
- **A legacy gate script that invokes `bash` internally must be run with `bash <script>`, not `sh`** — `/bin/sh` rejects `set -o pipefail` as an illegal option.

---

## 2. Gate item 3a — registered lane pack

```
timeout 300 bash test/packs/prompt_reload_compact_note_unit.sh
```
- **80 passed, 1 warning in 1.49 s**, 0 FAILED, 0 ERROR; pack `RESULT: PASS`; script exit 0; outer wrapper exit 0; wall 8 s.
- Files actually run (3): `tests/unit/tools/test_prompt_reload_compact_note.py`, `tests/unit/tools/test_compact_note_graph_wiring.py`, `tests/unit/test_compaction_compact_note.py`.
- `tests/unit/services/test_prompt_refresh_primitive.py` is **NOT** in the pack at this sha — confirmed independently (the in-flight fix adds it).

**RC-capture discipline — the brief's attribution is a MIS-ATTRIBUTION.** The brief flagged "the lane pack … uses `set -euo pipefail` with an unguarded `RC=$?` ⇒ it can exit BEFORE printing `RESULT: FAIL`". Verified by static check at `e387d2297`:

| script | `set -` line | rc capture | verdict |
|---|---|---|---|
| `test/packs/prompt_reload_compact_note_unit.sh` | `:29 set -uo pipefail` (**no `-e`**) | `:59 RC=$?` on its own line | **CORRECT** — prints `RESULT: FAIL` |
| `test/packs/compaction_unit_test.sh` (sibling) | `:31 set -euo pipefail` | `:49 RC=$?` unguarded | **DEFECT** — `RESULT: FAIL` unreachable |
| `test/packs/concurrency_atomic_unit_test.sh` | `:18 set -euo pipefail` | `:75 EXIT_CODE=$?` unguarded | **DEFECT** — `RESULT: FAIL` unreachable |

The defect lives in the **sibling** packs, not the lane pack (the base capture already logged the `compaction_unit_test.sh` case as anomaly `992788fb`). The lane pack even documents the house rule it follows at `:25-26`. The gate rule `rc != 0 AND no RESULT line ⇒ FAIL` was applied to the packs that actually have the shape.

---

## 3. Gate item 3b — adjacent compaction family (regression leg)

Base red-set for this family is **∅** (`services_BASE_RED_SET.md`, 196 nodes × 3 runs at `858a85e31`, all green). HEAD leg: **196 passed, 0 FAILED, 0 ERROR, exit 0, 29.74 s**, collected 196 == base's declared 196 (executed == declared proven). HEAD red set ∅ == base red set ∅.

File set (9): `test_compact_executor.py`, `test_compact_executor_defect1_pause_resume_lifecycle.py`, `test_compact_executor_revive_brick_e2e.py`, `test_compact_fired_watchers_deliver_before_compact.py`, `test_proactive_compaction_fix_p1.py`, `test_proactive_compaction_fix_p1b.py`, `test_proactive_compaction_fix_p2.py`, `test_proactive_compaction_ns_trap_fix.py`, `test_proactive_compaction_symptom_acceptance.py`.

**QUARANTINE consequence:** the file carrying the Sept-2026 ×2 `proactive_compaction` anti-drift family (`test_proactive_compaction_fix_p1.py`) is **inside** this captured set and is green at both base and tip ⇒ that quarantine row is **STALE**, and was **not inherited**. Annotated in `QUARANTINE.md` row 121 (2026-11-17).

---

## 4. Gate item 2 — read-after-write pin (exists AND bites)

Pin nodes — `tests/unit/tools/test_prompt_reload_compact_note.py::TestReadAfterWrite` (class at `:725`):

| node | line | assertion |
|---|---|---|
| `::test_the_arm_is_readable_immediately_after_write` | 733 | `state.armed is True; state.note == note; state.sha256_12 == …` |
| `::test_the_pin_reddens_if_the_read_is_served_stale` | 743 | `not (state.armed and state.note == note)` |
| `::test_the_read_after_write_holds_for_the_sha_prefix_too` | 766 | `state.sha256_12 == pr.compact_note_sha256_12(note)` |

Production anchors: write `daemon/services/prompt_refresh.py:arm_compact_note` (`:468`) → `_write_instance_metadata` (`:417`) → `repository.set_metadata`; read `daemon/services/prompt_refresh.py:read_compact_note_state` (`:369`). Seam read-after-write: `daemon/graph.py:create_compact_note_tools_node` (~`:13520`) calls `read_compact_note_state` **after** `await inner_node(...)` (where the tool writes).

**A/B in ONE throwaway clone** (house method — one arm proves nothing):

- **ARM A (green, unmutated clone):** `3 passed, 48 deselected` — 0.47 s.
- **ARM B (red, counter-mutation):** `arm_compact_note` mutated to `return True` without calling `_write_instance_metadata` ⇒ `F.F  2 failed, 1 passed`. Verbatim:
  ```
  tests/.../test_prompt_reload_compact_note.py:739: in test_the_arm_is_readable_immediately_after_write
      assert state.armed is True
  E   AssertionError: assert False is True
  E    +  where False = CompactNoteState(armed=False, ...).armed
  tests/.../test_prompt_reload_compact_note.py:771: in test_the_read_after_write_holds_for_the_sha_prefix_too
      assert state.sha256_12 == pr.compact_note_sha256_12(note)
  E   AssertionError: assert '' == 'bfa91e0863ad'
  ```
**The pin has teeth.**

### 4.1 FINDING — both in-flight truthfulness defects are UNGUARDED

| defect | caught? | evidence |
|---|---|---|
| (a) `_write_instance_metadata` discards the repository's write result and reports success | **NOT CAUGHT** | the pin's `StubInstanceRepo` always has the instance present, so the instance-missing path is never exercised; **restoring the buggy discard-and-report-success keeps all 3 pin tests GREEN** |
| (b) `settle_compact_note_arm` returns `"landed"` when the arm was never consumed | **NOT CAUGHT** | `grep` finds **ZERO** test references to `settle_compact_note_arm` |

Both fixes are correct; both are **unguarded**. Suggested pins: (a) instance-missing ⇒ arm returns `False`; (b) clear-failure ⇒ settle returns `"landed_unsettled"`, not `"landed"`.

### 4.2 Structural guarantee, not convention
Three layers: (1) seam ordering — the read is called after `await inner_node(...)`, so the read cannot precede the write by construction; (2) repository freshness — `set_metadata`/`set_metadata_many` commit before returning and `get` opens a fresh session; (3) the in-memory stub shares a dict by reference. The pin guards (3); the real-DB path's freshness rests on (2), untested.

---

## 5. Gate items 5, 6, 7

### 5.1 Refusal classifier (item 5) — disclosure TRUE, now quantified

12 markers, `daemon/graph.py:13338` (pinned) — `i can't`, `i cannot`, `i can not`, `i won't`, `i will not`, `i'm unable`, `i am unable`, `unable to comply`, `i refuse`, `i'm not going to`, `i am not going to`, `no compaction`. Classifier `_compact_note_looks_like_refusal` at `:13354`; the head slice at `:13372` = `(text or "").strip().lower()[:120]`; the match at `:13375` = `any(marker in head …)`.

*Anchor note:* the same symbols sit at `:13374` / `:13390` in the DIRTY working tree — a **+36-line drift** caused by the concurrent writer. Cite the pinned anchors.

**Window confirmed by probe:** marker at char 0 flips; marker starting at char 112 flips; marker starting at char 113 does **not** ⇒ a genuine 120-char HEAD scan, not a whole-text scan.

**Measured rates** (corpus 66 = 46 refusals + 20 benign):

| direction | rate | notes |
|---|---|---|
| fail-open (missed refusal ⇒ swap fires) | **14/46 = 30.4 %** | e.g. "I'm sorry, I'm not able to help with that", "That's not something I can do", "I must decline this request", "I'm not allowed to do that", "I don't think I can do that" |
| fail-closed (benign flagged as refusal) | 10/20 flagged; **10/10** of benign texts that QUOTE a marker | the SAFE direction — arm stays armed, work re-runs |

**Fail-open consequence chain — measured, not asserted:** with a missed refusal reply the node returned `RemoveMessage(id='__remove_all__')` + the sentinel + preserved messages; `armed` went `True → False`; the settle was called with `outcome='compacted'`, `result='landed'` and logged `prompt_reload compact-note: … outcome=compacted path=append … tail_spliced=1 doc_id=compaction-global-…`. The audit row says `compacted` exactly as disclosed.

**The classifier examines the MODEL'S REPLY, not the note** — `daemon/graph.py:13780-13790` extracts `reply_text` from the last `AIMessage.content`; `:13867` calls the classifier on it; the note is used only for `note_chars`/`note_sha12` (`:13834`). Correct by design: the note is the REQUEST.

### 5.2 Default-path byte-identity (item 6) — PASS

| leg | file | bytes | sha256 |
|---|---|---|---|
| HEAD `e387d2297` | `default-head.txt` | 9158 | `e97565466a810190ac07f32d7f82ad726b009c4d5b983126025130d6829d0ed7` |
| BASE `858a85e31` | `default-base.txt` | 9158 | `e97565466a810190ac07f32d7f82ad726b009c4d5b983126025130d6829d0ed7` |

`cmp` exit 0 — **byte-identical**. Sensitivity proven: a one-character perturbation of the receipt or the channel changes the sha (`fad7d4cf…` / `da5c948e…`), and the control shas reproduce across both legs. Positive control: the non-default path capture differs (`0c9d5226…`, 8597 B) — the harness detects the feature's behaviour.

The un-normalised raw capture differs at byte offset 1058 — HEAD `"compact": false,\n "compact_note": "",\n "force": false` vs BASE `"force": false`. **Adjudication: serialization artifact, not behavioural** — the differing segment is the tool-call `args` echo inside the harness's synthetic `AIMessage`, i.e. the INPUT (HEAD's tool declares the two new params; base's does not), not the path's output. Normalisation disclosed: `tool_calls[].args` → `<invocation-args-normalized>`; message ids assigned explicitly (`m0..m3`); no timestamp/uuid/hostname in the capture.

Default-path side effects, measured: arm consumed **NO**; arm state written **NO** (0 repo writes); channel length delta **NONE** (4 messages both legs); no `compaction_request` appended. The one difference is **one extra READ** (the seam's defensive `read_compact_note_state`) — a read, not a state change.

*Caveat (original):* the HEAD capture straddled the writer's edits (3 → 11 modified files during the capture).

**RE-CONFIRMED AT THE PINNED TREE — PASS.** The HEAD leg was re-captured against the immutable pinned clone (`/tmp/compact-gate-pin-4zUAXY/pin-e387d2297`, own venv, porcelain empty before/during/after):

| artifact | bytes | sha256 |
|---|---|---|
| NEW pinned HEAD capture | 9158 | `e97565466a810190ac07f32d7f82ad726b009c4d5b983126025130d6829d0ed7` |
| BASE capture (clean base worktree, contemporaneous) | 9158 | `e97565466a810190ac07f32d7f82ad726b009c4d5b983126025130d6829d0ed7` |
| NEW pinned NONDEFAULT (positive control) | 8597 | `a026710a6716d9e66d8a1e0d44b931bc297774c7a206314f3638ea4e571ef0ef` |

`cmp` exit **0** (NEW-pin-HEAD vs BASE) — byte-identical. Perturbation controls at the pin reproduce the prior shas exactly (`fad7d4cf…` receipt, `da5c948e…` channel) and both differ from the default capture ⇒ the byte-compare is non-vacuous. A determinism repeat leg at the pin produced an identical sha256 **and** an identical side-effect ledger (`cmp` exit 0). Side-effects re-measured at the pin: arm consumed **NO**, arm state written **NO**, repo writes **0**, repo reads **2** (both `get`), channel length delta **NONE** (2 → 4 normal turn growth, zero messages removed), `carrier_metadata` `{}`.

Two harness findings from the re-run, both disclosed by the worker:

- **The prior runner hard-coded ONE shared `CAPTURE_OUT_DIR`**, so the nondefault leg's perturbation controls were silently overwritten by the default leg's. The re-run uses a per-leg out-dir. (The control *values* were correct — they match the prior run — but the artifacts were at risk of clobbering.)
- **The nondefault capture is NOT byte-stable across runs** — it embeds wall-clock timestamps (`compact_note_armed_at`, `compact_note_last_applied_at`, `compacted_at`). The prior harness docstring's claim *"no timestamp reaches the capture"* holds for the **default** leg (verified: 0 ISO-8601 hits) and is **false for the nondefault** leg. After ISO-8601 normalisation the prior and new nondefault captures are `cmp`-identical, so the developer's WIP did not change the nondefault path either; only the default-mode control shas are stable across runs.

### 5.2.1 The pin is NOT cwd-independent — a trap for every pinned lane

The pin's editable install is a **path-based `.pth`** (`_editable_impl_ensemble.pth` → the pin path), **not** a MetaPathFinder. `sys.path[0]` (cwd / script dir) therefore **outranks** it. Measured: probing `"$PIN/.venv/bin/python" -c "import daemon; …"` with cwd = `/home/nea/ensemble-src` resolved `daemon` to `/home/nea/ensemble-src/daemon/__init__.py` — a **forbidden** tree. The byte-identity harness is immune (explicit `sys.path.insert(0, TREE)` + `cd "$TREE"` + assertion, and its false-pass guard fired for real when a wrong forbidden-list was supplied).

**Rule for every pinned lane:** ASSERT `daemon.__file__` **and** each subpackage's `__file__` (`daemon.graph`, `daemon.services.prompt_refresh`, `daemon.compaction`) resolve under the pin, BEFORE the run and AGAIN after — never trust the pin. The same ordering applies to the `git archive` + `PYTHONPATH` technique: `sys.path` order is [0] cwd/script-dir, THEN PYTHONPATH, then site-packages, so **cwd still outranks PYTHONPATH**.

### 5.3 Variant-B micro-window (item 7) — CONFIRMED-BENIGN

Measured with the house pinned method (`git archive e387d2297 | tar -x`, `daemon.graph.__file__` asserted under the scratch base) and a REAL compiled `StateGraph(SessionState)` + real `AsyncSqliteSaver` + real seam + real settle, with a hard `os._exit(97)` injected after the settle.

**Order proof** (pinned): swapped channel persisted **BEFORE** the settle —
`daemon/services/_compaction_persist_seam.py:255` `aupdate_state(config, {"messages": replacement}, as_node="agent")` → `:261` `aupdate_state(config, {"compacted_at": …})` → `daemon/graph.py:13906` `settle_compact_note_arm(repo, instance_id, note_sha12=…, outcome=APPEND_OUTCOME_COMPACTED, persisted=True, …)`. The empirical monotonic order log agrees (`003 messages` → `005 compacted_at` → `006 settle` → `007 agent-node RETURN`).

**Crash-after-settle checkpoint:** `messages_len` 11 (doc + 10 preserved); `next` = `None`; `pending_writes` = `[]`; `compacted_at` set; arm **consumed** (`compact_note_arm` absent, `last_applied_at`/`last_doc_id` set); channel structurally identical to control — same doc id `compaction-global-g7cmp-1`, same preserved tail `m03..m12` with identical content shas, the only difference being the run-specific timestamp embedded in the doc body.

| question | answer |
|---|---|
| (i) double-apply of the swap? | **NO** — resume runs `agent_node_invocations_on_resume = 0`; `next` is `None`; checkpoint fingerprint unchanged |
| (ii) lost arm? | **NO** — arm consumed, `last_applied_at`/`last_doc_id`/`last_sha256_12` set |
| (iii) channel differs from intended? | **NO** (structurally) — same doc id, same span, same preserved tail |
| (iv) none of the above | **YES ⇒ the window is benign** |

**Routing after the swap = END** (pinned `daemon/graph.py:4420` `return END`; `:14486` `agent_paths[END] = END`; `:14517` `add_conditional_edges("agent", agent_router, agent_paths)`), confirmed empirically (`should_continue -> '__end__'`). This **converges** with the live smoke's observed routing of END.

### 5.4 FINDING — the COMPLEMENTARY window DOES diverge, and nothing reconciles it

The crash-**before**-settle window was also measured: channel **already compacted** (`messages_len` 11), `compacted_at` = `None`, arm **still armed**, `last_applied_at` = `None`. The LangGraph checkpoint and the instance-metadata arm store are **two different stores with no atomic two-phase commit**, and there is **no restart reconciliation path** — `read_compact_note_state` is called only in the tools node, the agent node and `daemon/tools/prompt_reload.py`, **never on resume**. Measured consequence: on the next turn the tools node sees the arm still armed, appends a fresh compaction request, and the agent node swaps **again** — a second compaction over an already-compacted channel.

**This is a real, currently-unreconciled double-apply risk in the pre-settle window.** The in-flight `settle_compact_note_arm` fix hardens the settle's *internal* failure detection but does **not** close this cross-store window.

### 5.4.1 CORRECTION (2026-11-17, final leg) — this interim leg's EVIDENCE was partly VOID

Re-measured at the tip with a corrected harness, two defects in my own interim measurement were found and are disclosed here rather than left standing:

1. **The interim RESUME evidence was void.** The interim artifacts both record `"resume_error": "NotImplementedError: The SqliteSaver does not support async methods"` and `resume_returned: False` — **the interim resume never ran.** My interim reading *"resume runs 0 agent invocations"* was therefore an artifact of a **failed** resume, not evidence. Re-measured with `AsyncSqliteSaver`: `resume_returned=True`, **0 agent invocations**, channel and arm unchanged — so the **conclusion** (crash-after-settle is benign) survives, but the interim **evidence** for it was void and the interim report overstated that leg's strength.
2. **The interim harness's `FileRepo` violated the tip's carrier contract.** `daemon/services/prompt_refresh.py:430-460` treats `set_metadata_many(...) is None` as *"the instance does not exist"* (house contract, `daemon/repositories/instance/repository.py:3102`). The interim repo returned `None`, so at the tip every carrier write reports UNVERIFIED → the settle returns `landed_unsettled` and the arm is **not** consumed — which would have **masked the pre-settle window entirely**. The interim harness was measuring a broken carrier path.
3. **The interim harness's `tools → agent` edge was wrong** — it used a `should_continue` conditional edge, but the real graph routes **unconditionally** (`daemon/graph.py:14859` `add_edge("tools", "agent")`). That mis-model ends the turn after the tools node and **masks the re-trigger**.
4. **The interim "double-apply" claim was too strong.** The corrected measurement proves the **trigger** fires over the already-compacted channel, but the **landing** is refused by the seam's pre-write guard **identically in the crashed and the healthy control arm** (engine ids not a subset of the site-derived set; `agent_state` serves a stale 12-message view against the node's 15). **No landed double-apply is claimed in either direction.** The corrected leg ran the healthy control deliberately to kill the inference.
5. **The interim rate-limit bound was wrong.** This leg's §5.4 asserted the pre-settle double-apply was bounded by the rate limit. It is not: `_rate_limit_remaining_seconds(last_applied_at)` returns **0** when the clock is unset (`daemon/tools/prompt_reload.py:196-198`), and the call site refuses only `if _remaining() > 0` (`:468-477`). A pre-settle crash leaves `last_applied_at = None`, so the re-arm is **allowed immediately** — the landed-only clock provides **no** bound in that window, which is precisely what the tool's own docstring at `:187-191` already discloses (*"the rate limit does not protect against a note that never lands"*).

---

## 6. LIVE-TURN SMOKE at the PINNED tree — items 1(a)-(e), item 4, PROBE #1, PROBE #4

**The first live run's item-1(c) FAIL was a TEST-DESIGN CONFOUND, not the C1 bug.** The note literal necessarily appeared inside the instruction given to the agent, so the tail's preservation of that instruction reproduced it. Adjudicated against the **pinned** source (`git show e387d2297:…`, immune to the dirty tree), with every anchor independently re-verified by the tester:

| occurrence | surface | enclosing field | provenance |
|---|---|---|---|
| 1 | compaction doc, `Marker: \`…-AlphaBravo\`` | GLOBAL OVERVIEW | **MODEL-AUTHORED** — inside `global_overview=reply_text`, the agent's own distillation output |
| 2 | compaction doc, `3. The task: call … compact_note="…-AlphaBravo"` | GLOBAL OVERVIEW (model-authored session summary) | **MODEL-AUTHORED** echo of the instruction |
| 3 | preserved user task message | preserved verbatim tail | **PRESERVED ORIGINAL TASK** — contains the marker only because the test instruction contained it |

**Does the daemon render the note at all? NO — at most 0 times in the doc.** Pinned-source trace:

- `daemon/compaction.py` `build_compaction_doc(...)` — the signature has **no `note` parameter** (params: `instance_id, seq, mode, compacted_at, global_overview, sections, total_sections, summarized_start, summarized_end, preserved_count, dropped_spans, msg_timestamps, context_window, previous_overview, honest_truncation`). The GLOBAL element is solely the supplied `global_overview`.
- `daemon/graph.py:13806` and `:13852` — `global_overview=reply_text` (the model's reply, `reply_text = c or ""`).
- `daemon/graph.py:13719-13720` — the note is read ONLY for `note_chars = len(getattr(arm_state, "note", "") or "")` and `note_sha12 = getattr(arm_state, "sha256_12", "") or ""`.
- `daemon/compaction.py:1072` `build_compact_note_request(note, …)` inlines the note into the appended `compaction_request` HumanMessage — the **REQUEST to the model**, not the doc. That exchange is consumed and stripped by `_compact_note_strip_exchange` (`daemon/graph.py:13753`), so it never survives into the doc or the tail.
- `daemon/services/prompt_refresh.py` — the note is stored verbatim only in the arm carrier (`COMPACT_NOTE_ARM_KEY`) and read back for metadata; the daemon log emitted **0 note-body occurrences**, only `note_sha256=2c0c54584d9b`.

⇒ The observed duplication is **(model-authored echo of the note in its own distillation) + (the tail correctly preserving the original instruction, which contained the note text because of how the test was written)**. It is NOT the C1 bug. The "Marker: …" phrasing is model output, not a daemon-rendered field. **Item 1(c) = PASS.**

This verdict is **structural and static** — it does not depend on the contaminated live run.

### 6.0 The PINNED live re-run — provenance proven three ways

| item | value |
|---|---|
| PIN tree | `/tmp/compact-gate-pin-YCGTYj/tree` (377 MB, **git-archive content only** — no clone, no `uv sync`, no `.venv`) |
| sha validated | `e387d2297f404072b29a598b25a2e0f7d95e1f1c` |
| `sha256` `daemon/graph.py` | `84393209028b2553a7aac16aa3718f0e878c21b5368994d3d4d551dd508a14e7` — **MATCHES** the committed blob `a8a3211fa7d5c4952009238dca2b1ecdf8ab5f6b`; **DIFFERS** from the dirty worktree (`516c06db…`) |
| `sha256` `daemon/services/prompt_refresh.py` | `16d1e8c60d0c74d168aa350249dfddf43958b9b60103247f558a976cea823ec7` — MATCHES `fbad2d2b…`; DIFFERS from dirty (`008834f9…`) |
| boot | **uvicorn DIRECTLY** on `daemon.api:app`, **no `--reload`**, `127.0.0.1:8079`; version `0.30.13` (`GET /livez`), `readyz` 200 |
| DB | `ensemble_dev` @ `127.0.0.1:5432` (asserted; `ensemble_prod` absent) |

**Provenance — three independent proofs:**
1. **3 import probes** (before boot / daemon up / after run) — `daemon`, `daemon.graph`, `daemon.services.prompt_refresh`, `daemon.compaction` all resolve under the PIN and none into a forbidden tree.
2. **3-way cwd trap control** (same venv, same `PYTHONPATH`, only cwd varies): cwd=`$PIN` → ✅ correct; cwd=source worktree → ❌ forbidden tree; cwd=main checkout → ❌ forbidden tree. Confirms `sys.path[0]` (cwd) outranks `PYTHONPATH`, and that the editable install is a path-based `.pth`, not a MetaPathFinder. The boot used `cd "$PIN"` — `/proc/429450/cwd -> /tmp/compact-gate-pin-YCGTYj/tree`.
3. **The RUNNING DAEMON's own resolved path**: `GET /api/agents` → every `agent_dir` root = `/tmp/compact-gate-pin-YCGTYj/tree` (ari = `.../tree/agents/ari`); **zero** under the worktree. `agent_dir` derives from the daemon package `__file__`, so this proves the live process's import root.

**No measurement in this leg ran in the dirty source worktree; nothing here is INVALID.**

### 6.1 Item 1 — verdicts at the pinned tree

| item | verdict | measurement |
|---|---|---|
| **(a) swap lands** | **PASS** | daemon INFO line: `prompt_reload_compact_note: instance=2c2becfa… agent=- outcome=compacted path=append agent_model=agentic compaction_model_override_applied=false compaction_model_override_configured=- note_chars=7 note_sha256=cba66f0d93d3 tail_spliced=3 doc_id=compaction-global-2c2becfa-…-1`. The doc lands as a `SystemMessage` in the live channel (`GET /api/instances/{id}/messages`) and in the persisted checkpoint. **4/4 turns** landed. |
| **(b) routing after the swap** | **END** (recorded; both acceptable) | per-instance LLM invoke counts from the daemon file log: Turn A **2** invokes (`10:38:11 (4 msgs)` → tool call; `10:38:15 (7 msgs)` → compaction reply → swap fires), Turn D **2**, Turn C **10** (9 fillers/task + 1 compaction). **No invoke follows the swap** ⇒ the turn ENDS. The model's compaction reply is consumed by the swap and does not survive as an assistant message; its text lives inside the doc's GLOBAL OVERVIEW. |
| **(c) note rendered twice** | **PASS — C1 REFUTED** | §6.2 |
| **(d) receipt truthfulness** | **PASS with one GAP** | below |
| **(e) provider-cache claim** | **CANNOT-VERIFY** | below |

**(d) — receipt truthfulness.** Raw agent-facing receipt (verbatim):
```
prompt_reload armed — compact note ARMED; the note is a REQUEST your model will execute on its next invoke.
outcome=armed
note_chars=7
note_sha256=cba66f0d93d3
cap=12000
superseded_previous=false
path=append
selection=the model made the final selection
CONTEXT NOT YET COMPACTED — the note is a REQUEST your model will execute on its next invoke; until then this note remains armed.
```
The arm-time receipt truthfully says `outcome=armed` (not `compacted`); the daemon-side append settle reports `outcome=compacted`. Note body absent from every agent-readable surface: **0** in the 8 compact-note INFO/AUDIT lines (length + hash only), **0** in `instances.metadata` (the arm carrier is deleted after the swap; only `*_sha256_12` remains), **0** in any receipt.

⚠️ **THE GAP — the receipt carries NO model-id field.** The compaction ran on the agent's own model (`agent_model=agentic`, `compaction_model_override_applied=false`, `compaction_model_override_configured=-`), but that id exists only in the **daemon's append audit line, which the agent never sees**. Gate item 1(d) asks whether the receipt is truthful about *which model served it* — the receipt is truthful about everything it contains, but the model id is **not disclosed to the agent at all**. Flagged for the fix cycle: the receipt's own stated job is "only what is true", and the model identity is material to that.

**(e) — provider-cache claim: CANNOT-VERIFY (measured, not asserted).**
- `usage_metadata` is **`None`** on **every** `AIMessage` in every turn ⇒ **no token counts at all**, prefix or otherwise.
- **No cache counters exist in the daemon**: `grep -rn "cache_read|cached_tokens|cache_creation_input_tokens|prompt_cache_hit"` over `daemon/` → **0 hits**.
- The only cache-related byte observable is the provider **response** header `cache-control: no-cache, no-transform` — **response** caching, not prompt caching. Full header set: `cache-control`, `content-type: text/event-stream`, `x-accel-buffering: no`, `date`, `transfer-encoding: chunked`.
- **Effective model id on the compaction call vs the agent's model: IDENTICAL (`agentic` on both)** — the compaction IS the agent's own next invoke; there is no separate summarizer call to compare against.
⇒ **No cache-hit claim and no cache-miss claim can be made.** Per the leader's ruling, the context window resolved to **700000** (`get_model_context_limit('agentic') = 700000`, `('vision') = 700000`) and **no `4000` was ever observed** — the dead-code fallback was never reached.

### 6.2 Item 1(c) — the DISCRIMINATING experiment (C1 REFUTED)

**Turn A — note literal ABSENT from the instruction.** Token printed `ZED9QX4`; note used = **`4XQ9DEZ`** (the reverse). Asserted that `4XQ9DEZ` does **not** appear in the instruction. The agent really used it: receipt `note_sha256=cba66f0d93d3` matches the pre-computed hash of `4XQ9DEZ`, `note_chars=7`.

| surface | occurrences | enclosing field | classification |
|---|---|---|---|
| compaction doc — **daemon-rendered envelope** (header/boundary lines) | **0** | — | daemon-rendered |
| compaction doc — GLOBAL OVERVIEW body | **1** | `SystemMessage.content` | **model-authored** |
| **preserved user task message** | **0** | — | confound absent by construction |
| returned message list (`GET /messages`, 5 messages × content/tool_calls/thinking) | **1** (the same doc body) | `content` | model-authored |
| daemon compact-note INFO/AUDIT lines (8) | **0** | — | daemon-rendered, content-free |
| generic LLM DEBUG lines | 4 | LLM `reasoning_content` + tool-call args | the model's own bytes echoed by the generic logger |
| `instances.metadata` JSONB | **0** | arm carrier deleted after the swap | — |
| checkpoint channel (final) | **1** | `SystemMessage.content` | model-authored |
| checkpoint channel (historical) | 1/2/1/1 | `AIMessage.tool_calls[].args` and the `compaction_request` HumanMessage — both **stripped** from the final channel | model's args / the request itself |

**TOTAL across every agent-readable surface = 1.**

**Turn B — plain-marker comparison (literal PRESENT in the instruction).** Note `PLAINMARKER-GATE-20261117-BRAVO7`: doc body **3** (model-authored), daemon envelope **0**, preserved task **1** ← *the confound*. **TOTAL = 4**.
**Turn C (long, 8 fillers)** `LONGTURN-MARKER-20261117-KILO9`: doc body 1, envelope 0, preserved task 1 → **TOTAL 2**. **Turn D (parallel)** `PARALLELGROUP-MARKER-20261117-OSCAR2`: doc body 2, envelope 0, preserved task 1 → **TOTAL 3**.

**THE DELTA — the discriminator: Turn A = 1 (literal absent) vs Turn B = 4 (literal present).** The 3-occurrence difference is exactly what the confound predicts: the verbatim-preserved tail reproduces the instruction (1), and the model quotes the literal more often when it is salient in the instruction (2 more). **Daemon-rendered content occurrences: 0 in every turn.** ⇒ **Item 1(c) = PASS; C1 is REFUTED.** The first run's "2 occurrences" was a test-design confound.

### 6.3 Item 4 — group-atomicity: **PASS** (with a nuance)

A **real** parallel batch was constructed — both calls in ONE `AIMessage`:
```
AIMessage tool_calls=['prompt_reload', 'time']
  call prompt_reload args={'compact': True, 'compact_note': 'PARALLELGROUP-MARKER-20261117-OSCAR2'}
  call time          args={}
```
Pre-swap channel: `ToolMessage name=prompt_reload` + `ToolMessage name=time content='ISO: 2026-11-17T10:53:34.108675+00:00'`.

**The sibling `time` result was CONSUMED WITH THE GROUP — DROPPED.** Final checkpoints: `ToolMessages=0`, `AIMessage-with-tool_calls=0`. The sibling result is **not** carried into the tail and **not** summarized by the daemon.

**Nuance worth flagging:** the sibling's *text* does appear in the final channel — but only inside the **model-authored GLOBAL OVERVIEW body**, because the model quoted it in its own compaction summary before the swap consumed it. The code docstring says the sibling is "REMOVED from the context, not summarized into it"; at the **channel** level that holds, but the model can re-surface the bytes in the doc body. Group-atomic consumption is real and observable.

### 6.4 PROBE #1 — ANNOUNCED SPAN ≠ COMPACTED SPAN: **CONFIRMED**

| turn | announced span | actual span | Δ (announced_end − actual_end) |
|---|---|---|---|
| A (short, reversed token) | **#1–#4** | **#1–#3** | **1** |
| B (short, plain marker) | **#1–#4** | **#1–#3** | **1** |
| C (long, 8 fillers) | **#1–#10** | **#1–#9** | **1** |
| D (parallel batch) | **#1–#4** | **#1–#3** | **1** |

**Δ = 1 in EVERY turn — exactly the reported signature** (one message at the span END). **Turn C is decisive**: there the compaction was **non-empty** (9 groups actually compacted, 9 messages actually removed), so the divergence is **not** an artifact of the empty-compactable fallback.

**Mechanism (pinned code, `daemon/graph.py`):** the **append side** (`create_compact_note_tools_node`) computes the span from `len(state_msgs)` — the channel **including** the `prompt_reload` exchange `AIMessage`; the **swap side** (`create_compact_note_agent_node`) computes it from `len(kept_msgs)` — **after** `_compact_note_strip_exchange` strips that `AIMessage` + its `ToolMessage`. ⇒ **the exchange group is COUNTED on one side and STRIPPED on the other, in every turn.**

Observability: the announced span is visible in `daemon/compaction.py:build_compact_note_request` → the `compaction_request` HumanMessage (`"(compacted span: messages #1–#4; …)"`), read from the checkpoint history (the message is stripped from the live channel). The actual span is in the doc envelope (`summarized messages #1–#3`).

### 6.5 PROBE #4 — COMPACTION CLAIMED WHERE NOTHING WAS COMPACTED: **CONFIRMED** (short-context regime)

**Turn A (short context) — doc envelope, verbatim:**
```
[CONTEXT COMPACTION — mode=summary | summarized messages #1–#3 → global overview + 0/0 sections
 | compacted_at=2026-11-17T10:38:19.067620+00:00 | preserved verbatim: 3 most recent messages
 (below this notice) | self_id=compaction-global-2c2becfa-…-1]
```
**Claimed summarized span = #1–#3. Claimed preserved = 3.**

**Reality** — the 3 claimed messages mapped onto the actual kept channel and checked against the post-swap channel:
```
#1 HumanMessage 9c9c2da4-…  [SYSTEM CONTEXT: Project Scope Guide]   → IS STILL THERE
#2 HumanMessage memoir:ari   [SYSTEM CONTEXT: Memoir]                 → IS STILL THERE
#3 HumanMessage 1f14e3d8-…  Task: call the prompt_reload tool …        → IS STILL THERE
messages the doc CLAIMS to have summarized that are STILL PRESENT verbatim: 3 of 3
messages actually REMOVED from the claimed span: 0
```
⇒ **The doc claims "summarized messages #1–#3" while ALL 3 remain verbatim in the channel, and the receipt still says `outcome=compacted`.** Post-swap channel = 4 messages (3 kept + the doc).

**Observed parameters:** `len(groups)` = **4** (append side) / **3** (swap side, exchange stripped) — **≤ `recent_window`**; `recent_window = 10`, `min_recent_window = 3`, `threshold = 0.80` — read from `$PIN/config.yaml` (`compaction:` block) and confirmed at runtime by the pinned `select_compactable_groups` returning `window=10`. `select_compactable_groups` → **`compactable=0` groups** ⇒ the `else:` fallback (`span_start=1; span_end=len(kept_msgs)`) produces the claim.

**Control (Turn C, long context): `len(groups)=20` > 10 → `compactable=9` → doc claims #1–#9 and ALL 9 were actually removed (0 survivors).** ⇒ The defect is **regime-dependent**: it fires only when `len(groups) ≤ recent_window`. Clean refutation in the long regime.

### 6.6 Cleanup / hygiene of the pinned leg

| check | result |
|---|---|
| daemon stopped | ✅ SIGTERM (exit −15) |
| port 8079 | ✅ **free** |
| port 9797 (live ensemble-prod) | ✅ **untouched** — same pid 700753 before and after; never killed, never bound |
| port 8088 | ✅ no listener before, none after |
| orphan children in the spawned tree | ✅ none |
| repo mutations | ✅ none — HEAD still `e387d2297`; no commit/push/merge/rebase/checkout/reset/stash/clean |
| live install | ✅ never touched, live daemon never booted |
| source-tree footprint | the worktree went 11 → 14 tracked-modified files during the run — **all three attributable to the concurrent developer lane** (mtimes 10:33:32 / 10:34:26 / 10:34:44, a mode-change-only edit). The leg's only writes there were **13 gitignored `__pycache__/*.pyc`** bytecode byproducts of the trap-control import probes; **no tracked file was touched by it**. Flagged honestly. |

Evidence: 15 `.txt` files under `/tmp/compact-gate-pin-YCGTYj/evidence/` (`turn-A-counts.txt`, `turn-A-final-channel.txt`, `probe1-spans.txt`, `probe1-spans-CD.txt`, `probe4-claim-vs-reality.txt`, `item1d-receipt-and-body-audit.txt`, `item4-sibling-fate.txt`, …) plus `daemon-stdout.txt` and 4 message-list JSONs.

---

## 7. Core-seam adjacency dual-side leg — the regression verdict

The first run of this leg was **INVALID** as a verdict on `e387d2297` (its HEAD side was dirty-tree-derived; it self-evidenced this by reporting the stale pin as passing). The leg was therefore re-run in full — the **entire 97-file adjacency set**, 4 shards per leg — at PINNED HEAD and at BASE under identical controlled fences, with an in-run provenance guard (`-p provguard`) printing the actually-loaded paths at `pytest_sessionstart` on **every one of the 60+ pytest invocations** (`off_tree=0` throughout).

Derivation: grep the test tree for the seam entry points (`build_instance_graph`, `build_compaction_doc`, `_resolve_proactive_enabled`, `COMPACTION_TARGET_RATIO`, `InstanceMessagingService`), excluding `tests/integration/ postgres/ e2e/ message_queue_redesign/`. 2474 nodes collected, 2474 executed (44 skipped) on both legs.

```
red@PINNED-HEAD : 52      red@BASE : 53
shared (pre-existing)          : 51
red@PIN only  (net-new)       : 1
red@BASE only (apparent fix)   : 2   ← both proven FLAKY, not fixes
```

### 7.1 The 3× retry budget — all three candidates are FLAKY, none is a regression

Six attempts per leg (two rounds of 3), SOLO, sequential, **no xdist**, at pinned HEAD and at BASE:

| node | PINNED HEAD | BASE | classification |
|---|---|---|---|
| **R1** `tests/test_chart_tools_reuse_integration.py::TestT5FanOutScoping::test_same_caller_charter_continuity_across_caller_revival` | 4P/2F | 6P/0F | **FLAKY** — charter-busy **race** (`prior_status=running` → busy-reject), hitting different assertion points (L1432/L1433) on different attempts. `daemon/tools/chart_tools.py` **untouched** by the branch. |
| **R2** `tests/test_manager_shutdown.py::TestManagerShutdownDurableBashSweep::test_durable_sweep_sibling_alive_skip_and_dead_writer_swept` | 4P/2F | 4P/2F | **FLAKY** — mixed at **BOTH** legs ⇒ not branch-attributable. |
| **F1** `tests/tools/test_auto_kill_integration.py::TestPathADurableTombstoneReap::test_sibling_alive_row_skipped_by_terminal_lane` | 4P/2F | 3P/3F | **FLAKY** — mixed at **BOTH** legs; signature identical to QUARANTINE row 181. |

Verbatim signatures:
```
R2  tests/test_manager_shutdown.py:545
    E   AssertionError: live-sibling row must stay OPEN
    E   assert '2026-11-17T10:38:38.730546+00:00' is None
    WARNING daemon.services.bash_spawn_reaper:bash_spawn_reaper.py:522 env verify: marker
    ENSEMBLE_BASH_TRACKING_ID present at PID 442494 but value differs (PID likely recycled). Aborting.
    WARNING ...:1243 bash reaper: refused row 1 ... — refused:env-mismatch (swept by daemon pid 442339)

F1  tests/tools/test_auto_kill_integration.py:2173
    E   AssertionError: D8 violation: a live-sibling-owned row produced actions (1)
    E   assert 1 == 0
```

**⇒ 0 deterministic net-new regressions among the three candidates.**

### 7.2 The "FIXED" classification is OVERTURNED

F1 was previously reported **red@BASE / green@HEAD ⇒ "FIXED"**. **Wrong.** In the controlled full adjacency batch F1 was **red@BASE / green@PINNED-HEAD** — the **mirror image** of the prior reading — and in isolation it is mixed at both legs. Both prior readings were load-composition artifacts; the prior leg's "green@HEAD" had simply fallen on the flake's lucky side.

**Consequence for the ledger:** QUARANTINE row 181's claim that these nodes are *"3/3 GREEN isolated"* **does not reproduce** (4P/2F@HEAD, 3P/3F@BASE) ⇒ they are flaky **in isolation**, not only under load. The row's load-composition *class* rationale stands and the row stays QUARANTINED, but the isolation-evidence column is wrong. **Annotated in place** (anomaly `c49ef6ef`). R2 is of the same class and has been added to the annotation.

**Shared root cause (pre-existing at BASE — not this lane's to fix):** `daemon/services/bash_spawn_reaper.py:522` stamps `reaped_at` / `reap_reason='refused:env-mismatch'` onto a row it **declined to reap** ("Aborting"), which is what breaks both R2's "live-sibling row must stay OPEN" assertion and F1's D8 assertion. One behaviour, two test surfaces.

### 7.3 The 51 pre-existing reds — CONFIRMED

The 51 node ids from the prior leg's BASE FAILED list, re-run at PINNED HEAD (2 shards) and at BASE (2 shards): **49 red / 2 green at BOTH legs, byte-identical FAILED sets** — `comm -23` and `comm -13` **both empty ⇒ zero net-new**. The 2 greens (`tests/unit/test_graph_retry_integration.py::TestBuildInstanceGraphIntegration::{test_build_instance_graph_with_compactor, test_build_instance_graph_with_retry_config}`) are **order/load-dependent**, 3/3 GREEN in isolation at both legs and green at BASE in the identical batch — not candidate fixes. The third graph_retry node (`test_compaction_context_structure`) is **3/3 RED at both legs** ⇒ deterministically pre-existing. **The prior leg's 51 pre-existing count is CONFIRMED at pinned HEAD.**

### 7.4 The ONE deterministic net-new red — the known-dispatched stale pin, now independently confirmed

```
tests/unit/services/test_prompt_refresh_primitive.py::TestResultShapeAndVocabulary::test_refused_vocabulary_is_nine_plus_one_failed
```
**3/3 RED at PINNED HEAD, 3/3 GREEN at BASE.** File scope at pinned HEAD: **1 failed / 41 passed**; at BASE: **42 passed**.

```
tests/unit/services/test_prompt_refresh_primitive.py:146: assert len(REFUSAL_CODES) == 9
E   AssertionError: assert 14 == 9
```

**Mechanism (measured):** `REFUSAL_CODES` is **9** at BASE and **14** at `e387d2297` — the branch adds the compact-note refusal codes (`compact_disabled`, `compact_rate_limited`, `compose_failed`, `note_over_cap`, `note_required`, `note_requires_compact`) — while `tests/unit/services/test_prompt_refresh_primitive.py` is **untouched by the branch** and still pins `== 9`. **This is a TEST-SIDE stale pin against the branch's own intended feature, NOT a production-behaviour defect.** It was invisible to the prior leg because that leg's HEAD side ran the dirty tree — exactly the contamination blind spot.

**Independent corroboration (three separate routes, all agreeing):**
1. **Pinned dual-side execution** — 3/3 red@HEAD, 3/3 green@BASE (§7.4 above).
2. **Static blob analysis at `e387d2297`** — `daemon/services/prompt_refresh.py:178` defines the census with **14** members; its own docstring at `:495` states *"so the `REFUSAL_CODES` census stays at 14"*; the test at `:146` asserts `== 9`.
3. **Base capture** — the file is in base surface A at **42 nodes and GREEN at base**, so the red is **net-new at the branch**, not base-inherited.

### 7.5 Verdict

**Deterministic net-new regressions across the whole adjacency set = 1** — and it is the already-dispatched stale test pin, not a code defect.
**0** among the three adjudicated candidates (all FLAKY). **Flake class = 4** (R2, F1, + 2 order-dependent graph_retry). **Pre-existing = 51**, confirmed.
`daemon/graph.py` **is** touched by the branch (+720 lines) — the only plausible reach channel into the adjacency set — and the controlled dual-side shows **zero** net-new reds attributable to it.

### 7.6 A pytest-9 trap that can make a red run look GREEN

**pytest 9 REPLACES (not extends) the default report-char set on `-r`.** With `-rs` (skip-reasons) it prints **ZERO `FAILED` lines** — the failure summary is suppressed entirely. Every reporting run must use **`-rf`**. A leg run with `-rs` and classified by `grep '^FAILED'` would report **0 failures on a fully red run** — a silent false PASS. Filed as anomaly `77041eda`. All reporting runs in this leg used `-rf`.

---

## 8. ensure.md Core validation

ensure.md is **user-owned and read-only** — it was read, never modified.

**Core, in scope (7):** C1 no-regressions-in-changed-packs · C2 deadlock/concurrency integrity · C3 no sync DB on the event loop · C4 `dev.sh` graceful shutdown · I1 await correctness · I2 original deadlock scenario · N1 no dead code.
**Release Gate, OUT of scope (5):** full non-integration suite + 4 E2E items — blast radius = feature lane, not a release/promote gate (ensure.md:5, :54); the E2E items additionally need a live `:8079` daemon and real LLM calls.

| ID | Verdict | Measurement |
|---|---|---|
| C1 | VALIDATED-BY-REFERENCE | sibling legs: lane pack · adjacent family · affected-symbol widened set · core-seam adjacency dual-side |
| C2 | **PASS** (quarantine-adjudicated) | `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` ⇒ rc=1, **5 failed, 94 passed, 74 skipped**, 148.57 s; **0 non-quarantined failures** |
| C3 | **PASS** | same pack; thread-identity/`to_thread` nodes green (deadlock file 10/10 standalone) |
| C4 | **PASS** | `dev.sh:159` — `--timeout-graceful-shutdown 10` (comment `:156`) |
| I1 | **PASS** | AST sweep: 3 call sites in the changed files, **0 un-awaited**; repo-wide widened to 10 sites, 0 un-awaited |
| I2 | **PASS** | `timeout 200 .venv/bin/pytest tests/test_deadlock_fix.py --override-ini="addopts=" -q --tb=short -rs` ⇒ **10 passed**, 4.56 s, 0 skips (genuinely exercised) |
| N1 | **PASS** | 7 changed modules import OK + `compileall` OK; removed `def`s are in-place rewrites with live references; removed imports still present |

**C2 honesty:** the pack's raw rc is **1**, and there is **no `RESULT:` line** — the pack itself has the `set -euo pipefail` + unguarded-rc defect, so `echo "RESULT: FAIL"` is unreachable. Per the gate rule that is FAIL-as-a-pack-run; the requirement's intent is satisfied because the FAILED set is byte-identical to the quarantined set. Recorded as **PASS (quarantine-adjudicated)**, explicitly NOT a clean-pack PASS.

**C2 lane-innocence proof (independent of ledger appeal):** the branch's **entire** change to `daemon/services/instance_messaging.py` is **one whitespace-only line** (trailing spaces stripped on a blank line at `:1533`); `git diff -w` over that file produces an **empty stat**. The working tree does not touch that file; `tests/test_instance_metadata_atomic.py` is untouched on both sides; the branch modifies **no file under `daemon/repositories/`**, where the metadata/title session seam lives.

### 8.1 ensure.md Improvement Notices

- **⚠️ Tier-2 trigger FIRES.** `daemon/services/instance_messaging.py` is in ensure.md's mechanical core-seam list (ensure.md:19). Tier 2 = *whole-tree dual-side*. **A whole-tree sweep was NOT run**, and the reason is stated rather than hidden: (a) the leader scoped this gate and the tree was moving, so a whole-tree verdict against `e387d2297` would be void against a superseded sha; (b) the change to the triggering file is **whitespace-only** (`git diff -w` empty), so the trigger's semantic reach is nil; (c) the core-seam adjacency dual-side leg covers the changed seams. **A whole-tree dual-side leg belongs to the post-merge gate.**
- **⚠️ ensure.md:69 / :71 — Release-Gate contradiction (out of scope, not run, not edited).** Those items say *"Validation: same pattern"*, inheriting only the raw-file pytest form introduced at :67, which contradicts ensure.md's own header (:4 *"requirements reference packs in PACKS.md … NOT bare pytest commands"*; :6 *"every validation executes as a pack … NEVER as a bare, unbounded pytest command"*). Only :67 offers the pack form. Suggested rewrite: *"Validation: `timeout 300 bash test/packs/e2e_workflows_ensure_test.sh` (pack forwards `-k` node selection if needed); the raw-file pytest form is acceptable only as a secondary fallback."* ensure.md is user-owned — please update.
- **ℹ️ ensure.md:61 vs :6 ambiguity** — *"Run tests one by one"* (:61) reads as scoped to the real-LLM-call E2E items, while :6 says packs run in parallel. A clarifying clause would help.

---

## 9. The known-dispatched red — independently confirmed, and CORRECTED

The leader relayed: `tests/unit/services/test_prompt_refresh_primitive.py:146` asserts `len(REFUSAL_CODES) == 9` while the landed code has 14 ⇒ `AssertionError: assert 14 == 9`, 1 failed / 41 passed; the lane pack was green because it ran only the three lane files.

**Confirmed independently from the pinned blobs, with NO run:**

- `daemon/services/prompt_refresh.py:178` — `REFUSAL_CODES: frozenset[str] = frozenset({…})` with **14** members. The code's own docstring at `:495` states *"so the `REFUSAL_CODES` census stays at 14"* — **14 is deliberate**.
- `tests/unit/services/test_prompt_refresh_primitive.py:146` — `assert len(REFUSAL_CODES) == 9`.

⇒ At `e387d2297` the red is deterministic and structural. **The stale pin is the TEST, not the code.**

**Correction to the relay:** the red is **NET-NEW at the branch, not base-inherited**. The base capture's group A *did* include `test_prompt_refresh_primitive.py` — at **42 nodes**, green at base (341-node capture: 42 + 20 + 15 + 13 = 90). The base capture's file selection was name-based and therefore incomplete for OTHER files, but it was **not** incomplete for this one.

---

## 10. QUARANTINE.md maintenance (main checkout)

Three annotations written to `/home/nea/ensemble-src/.agents/tester/QUARANTINE.md` (the canonical checkout's copy — the worktree's copy is at a different commit and has different line numbering):

1. **Row 121** — the `proactive_compaction` T6 anti-drift **×2** member is **STALE** and was **not inherited**: it did not re-observe at base `858a85e31` (196/196 green ×3, `test_proactive_compaction_fix_p1.py` inside the captured set) and is green at `e387d2297` (196/196, 29.74 s). The row's other families are untouched by that finding.
2. **Row 152** — **RE-CONFIRMED 2026-11-17 at the PINNED tree** (`e387d2297`, immutable clone, import provenance asserted before *and* after the run): the `concurrency_atomic_unit_test` pack produced **5F / 94P / 74S in TWO runs with identical FAILED sets**; all 5 FAILED nodes are the row's members node-for-node with the identical `sqlalchemy.orm.exc.DetachedInstanceError` signature; **0 non-quarantined failures, 0 ERROR nodes** ⇒ the ensure.md Core C2 requirement is **PASS (quarantine-adjudicated)**. The pack's raw rc is **1** with **no `RESULT:` line** — the pack itself carries the `set -euo pipefail` (`:18`) + unguarded `EXIT_CODE=$?` (`:75`) defect, so `echo "RESULT: FAIL"` (`:84`) is unreachable ⇒ **FAIL as a pack run**, PASS as a requirement. The failing test exercises the Fix-C7 dialect-aware single-statement UPDATE seam in `daemon/repositories/instance/repository.py` (`:2990` set_metadata, `:1862` update_title) — a **session-boundary** defect, not a deadlock/atomicity failure; the *concurrency* class in the same file passed (`TestConcurrentMetadataWrites` ×3). That file is byte-identical base↔tip and the branch touches nothing under `daemon/repositories/`.
   **Corrected line refs — and a self-correction.** An earlier note in this gate claimed the row's cited refs were "stale by a uniform −4" and "corrected" `67/74/82/128/134` → `63/69/76/124/130`. **That correction was WRONG and has been REVERTED.** The row's refs are the pytest **FAILED-line ASSERTION** lines: `file.py:LINE::Class::method` reports the line of the failing `assert`, not the `def`. At the pinned sha, `:67` = `assert result.instance_metadata.get("foo") == "bar"`, `:74` = `assert result.instance_metadata["foo"] == "third"`, `:82` = `assert result.instance_metadata == {...}`, `:128` = `assert result.instance_metadata.get("title") == "My Title"`, `:134` = `assert result.instance_metadata.get("title") == "New"` — the `def` lines sit 4 lines above each. The original citations were correct and still match.
   **Lesson:** the −4 offset was a real *reading* error — the "def line" was compared against an "assert line" citation. A ledger citation in pytest nodeid form must be checked against the **assert** line, not the `def` line; and a line-drift claim must be verified by opening the cited line, not by inferring a uniform offset.

---

## 11. Open items / carried forward

- **Post-merge gate.** The giter's ruling stands: the PRE-merge 80/80 is **not** the post-merge verdict (latest refactored `daemon/services/context_messages.py`; the branch pack was validated against the base tree's context code). The pack must be re-run after the merge onto `origin/latest`.
- **Re-confirm on the landed sha.** C2 (concurrency pack) and the contaminated legs should be re-run once the developer's fix cycle is committed — a re-run at a later commit could legitimately differ.
- **POK store is at its 256 active-entry cap** — `pok_upsert` for the reusable RC-capture-defect fact was refused (`pok_active_cap`; anomaly `6d25d9a5`). Workers cannot capture new operating knowledge until a leader/PM archives a superseded entry via `pok_curate`.
- **Suggested new pins** for the two unguarded truthfulness defects (§4.1).
- **Suggested fix** for the unreconciled pre-settle crash window (§5.4): either an atomic two-phase commit across the checkpoint and the arm store, or a restart reconciliation that re-reads the checkpoint and settles/clears a stale arm.

---

## 12. Affected-symbol widening — the leader's deliverable (AFFECTED-SYMBOL scoping)

Derived **by symbol**, not by file name, with the **transitive** closure — per the reviewer's house practice adopted by the leader.

### 12.1 SELF-CORRECTION — the dispatcher's seed list over-claimed the branch's reach

My dispatch to this leg seeded the changed-symbol list by reading **diff hunk headers** — which show *surrounding context* — and treating them as changed symbols. The worker's AST diff refuted six of them, and I verified each against the pinned blobs:

| symbol I seeded as "changed" | BASE | TIP | verdict |
|---|---|---|---|
| `COMPACTION_TARGET_RATIO` | `float = 0.40` | `float = 0.40` | **UNCHANGED** |
| `INFO_OUTCOMES` | `tuple[str, ...] = (` | `tuple[str, ...] = (` | **UNCHANGED** |
| `CODE_COMPOSE_FAILED` | `"prompt_reload_compose_failed"` | `"prompt_reload_compose_failed"` | **UNCHANGED** |
| `CODE_ENVELOPES` | `dict[str, str] = {` | `dict[str, str] = {` | **UNCHANGED** |
| `format_info_line` | `def format_info_line(` | `def format_info_line(` | **UNCHANGED** |

**The branch does not touch these symbols**; the hunks merely *contained* them. The seed list also **omitted** genuinely-changed symbols: `build_compact_note_request`, `DEFAULT_COMPACT_NOTE_MAX_CHARS`, `_resolve_compact_note_max_chars`, `DELIBERATE_COMPACT_RATE_LIMIT_S`, `load_config`, and ~40 new `prompt_refresh` symbols. **Lesson: a changed-symbol set must come from an AST diff, never from diff hunk context** — hunk headers over-state reach and under-state omission simultaneously, and both errors bias the resulting test set in opposite directions (pulling in files that assert on untouched symbols, missing files that assert on new ones).

### 12.2 The authoritative derived set

- **Changed daemon modules (7):** `daemon/compaction.py`, `daemon/config.py`, `daemon/constants.py`, `daemon/graph.py`, `daemon/services/prompt_refresh.py`, `daemon/tools/prompt_reload.py`, `daemon/services/instance_messaging.py` (the last **whitespace-only**).
- **Direct symbol→file set (AST + module-bound reference grep): 137 files** (136 + 1 evictor), 3014 nodes. `FILE → SYMBOL` map persisted (`AFFECTED_SYMBOL_MAP.txt`, 294 lines).
- **Transitive import closure (BFS through intra-daemon imports): 1766 files** — degenerates because `daemon.config`/`constants`/`graph` are imported tree-wide.
- **Tight value-based set (lane-specific new literals): +0 files** beyond direct — i.e. no test outside the direct set asserts on a lane-specific new value.

### 12.3 Results

| set | files | nodes | BASE | PIN (`e387d2297`) | net-new |
|---|---|---|---|---|---|
| Direct set (per-file ISOLATED — authoritative) | 137 | 3014 | 21 files isolated: 16 inherited reds + 5 green | **17 RED files / 119 GREEN** | **1** (the known pin) |
| Focused slice (transitive-only ∧ lane-vocabulary), batch | 295 | 8163 | **58** distinct FAILED node ids | **75** distinct FAILED node ids | **0** after adjudication |
| Transitive closure | 1766 | — | enumerated, not measured | enumerated, not measured | — |

**Focused-slice adjudication of the 17 red@PIN-but-green@base candidates:**
- **10 are ENVIRONMENTAL — artifacts of MY pinning method, not branch reds:** `test_plugin_registry` (1), `test_filesystem_walk_guardrails` (4), `test_guidelines_registration` (5). These are **path-sensitive** — the `plugins` dir at repo root, the real data dir, and shipped guideline files — and the pin lives under `/tmp`. They are **GREEN at the base worktree** (a repo-shaped path), so the delta is purely the pin's location. **This is a measurement artifact my own method introduced, and it is disclosed as such.**
- **7 are inherited** (red at base too): `test_job_queue_tools` (4), `test_skill_fix_burst_guard` (1), `test_instance_tools` (2).

**⇒ NET-NEW IN THE FOCUSED SLICE AFTER ADJUDICATION: 0.**
**⇒ THE ONLY NET-NEW RED ACROSS THE ENTIRE 1766-FILE TRANSITIVE SET + 137-FILE DIRECT SET IS THE KNOWN-DISPATCHED STALE PIN** (`tests/unit/services/test_prompt_refresh_primitive.py::TestResultShapeAndVocabulary::test_refused_vocabulary_is_nine_plus_one_failed`, `assert 14 == 9`) — already fixed by the developer lane in `63b02e3a6`.

### 12.4 The 128 additional files the widening pulled in (beyond base surfaces A–D)

**16 RED — all INHERITED (pre-existing at base `858a85e31`, not branch-caused) — and 112 GREEN.** The 16, with their verbatim FAILED node ids: `test/packs/ri_e2e_capstone_real_engine_test.py` (2) · `tests/integration/test_attestation_revive_after_escalation.py` (1) · `tests/integration/test_explorer_shared_context_paths.py` (1) · `tests/integration/test_initiative_message_integration.py` (7) · `tests/integration/test_pause_race_w7_jobitem_skip.py` (1) · `tests/services/test_instance_messaging_queue_routing.py` (2) · `tests/test_chart_image_delivery_e2e.py` (1) · `tests/test_enqueue_shared.py` (2) · `tests/test_main_entry.py` (15) · `tests/test_message_job_serialization.py` (3) · `tests/unit/routers/test_slash_commands_router.py` (25) · `tests/unit/services/test_child_report_next_turn_execution.py` (3) · `tests/unit/test_graph_retry_integration.py` (1) · `tests/unit/test_llm_failover_v2_resilience.py` (2) · `tests/unit/test_llm_streaming_wire_verify.py` (1) · `tests/unit/test_phase4_manager_decomposition.py` (1).

**Composition artifacts (RED in the sharded run, GREEN in isolation — cross-file `sys.modules` langgraph eviction, NOT code failures):** `tests/test_language_check.py`, `tests/test_manager.py`, `tests/unit/test_compaction_escape_hatches_round2.py`, `tests/unit/test_symptom_repair_ladder.py`, `tests/unit/test_watchover_graph.py`.

### 12.5 The known red — a THIRD independent confirmation

`tests/unit/services/test_prompt_refresh_primitive.py::TestResultShapeAndVocabulary::test_refused_vocabulary_is_nine_plus_one_failed`
- At the PIN `e387d2297`: **1 failed, 41 passed** — `assert len(REFUSAL_CODES) == 9` ⇒ `AssertionError: assert 14 == 9`.
- At BASE `858a85e31`: **42 passed** (census 9).
- Reproduced **at both clean pins**, and independently via a `git archive` commit-tree extraction.
- The three independent routes now agree: **pinned dual-side execution** (3/3 vs 3/3), **static blob analysis** (`prompt_refresh.py:178` = 14 members; docstring `:495` = "stays at 14"), and **the widening leg's own both-ends measurement**.

### 12.6 Coverage boundary — what was NOT measured, stated

1. **1471 of the 1766 transitive files are UNMEASURED** (transitive-only, no lane-specific value reference). They could only be red if the branch changed an observable behavior they exercise; the branch's only observable changes are the new symbols/values, asserted **only** within the direct set (the tight value set adds +0 files). The residual risk is **theoretical and bounded**, and per-file isolation of 1471 files is not tractable in budget. **Stated, not hidden.**
2. The loose value-based set (974 files) and the transitive import closure (1766 files) both **degenerate** (~49 % and ~88 % of the tree) — reported as derivations, not measured sets.
3. Sharded runs of the direct set are **not reproducible** due to endemic cross-file `sys.modules` langgraph eviction (12+ evictor files in the derived set) — which is why the **per-file isolated** run is the authoritative measurement, and why `test_message_queue_e2e.py` is an evictor no-op (rc=5, all deselected).
4. Collect/run proof: collect-only at the PIN = 3014/3046 collected, 32 deselected, **0 collection errors**; a ±1 node-count discrepancy confined to shard 1, affecting no verdict.
5. One file differed between the PIN and an independent immutable extraction at the same sha (`test_llm_failover_v2_resilience.py`, a 30 s latency-cap timing test) — **environmental flake, not a semantic difference**.

### 12.7 Rulings on the three open questions

1. **The 16 inherited reds — OUT OF SCOPE for this gate's verdict, but named.** They are pre-existing at base and unrelated to the compact-note symbols (DB resolution fence, chart seal, watchover topology, slash-command routing, main-entry boot preflight). Pre-existing reds are not branch-caused; most are already QUARANTINE-listed and therefore do not fail a requirement. They are **listed by node id** in §12.4 so the ledger, not the gate, carries them.
2. **The 10 environmental PIN-location reds — a defect of MY measurement method, not of the branch.** They fail because the pin lives under `/tmp` and those tests are path-sensitive (repo-root `plugins/`, the real data dir, shipped guideline files); they are GREEN at the repo-shaped base worktree. **Disclosed as a method artifact.** For any future pinned leg, path-sensitive tests must be re-measured at a repo-shaped path or explicitly excluded — this is now a named requirement of the pin recipe.
3. **Do NOT re-run at `63b02e3a6` from this leg.** `e387d2297` is this interim leg's base and the deliverable is complete against it. The amended tip is the **final-tree leg's** subject and will be dispatched on the amended tip.

### 12.8 The tree moved during the leg

The source worktree advanced to **`63b02e3a6`** (3 commits on top of `e387d2297`; the known red is fixed there). **This gate's measurements remain against `e387d2297`**, at the immutable pin, with `git status --porcelain` empty before and after every leg. No measurement was taken against `63b02e3a6` and none is claimed for it.

---

## 13. FINAL-TREE LEG — the merge gate at `bf5c21efe`

**Tip:** `bf5c21efe375ecb007115b5d7f25ceaee6533025` · 53 commits from base `858a85e31` · never pushed · porcelain EMPTY before and after every leg
**Preflight `df -h /`:** 15 G free (94%) — the ~3 G bar cleared with margin; the stop-and-report rule was never triggered (lowest observed 14 G)
**Base:** `858a85e31` @ the clean base worktree

### 13.1 VERDICT TABLE AT `bf5c21efe`

| # | Item | Verdict | Headline |
|---|---|---|---|
| 1 | H6/H7 arm-write matrix — pin must BITE | **PASS** | ARM A green → ARM B reddens **2 of 3** variants (none-write, partial-write); `test_full_write_arms_cleanly` correctly stays green |
| 2 | Live-turn smoke (a)–(e) | **PASS** (a)(b)(c)(d) · (e) CANNOT-VERIFY | swap lands 2/2 legs; routing END; **zero daemon-rendered note occurrences** (designed) |
| 3 | The pack | **PASS** | **459 passed / 0 failed / 0 errors / 0 skipped** across **12 files**; `RESULT: PASS`; exit 0; 35.61 s |
| 4 | Message pins + the four NEW pins | **PASS** | six counter-mutation legs, **every pin BITES**, with per-variant discrimination |
| 5 | AST-derived widening + disagreement check | **FAIL** | a **deterministic net-new red** in a file the static pack omits (§13.5) |
| 6 | The receipt's model field | **PASS** (presence + wording) | `model_configured=agentic` on all 3 receipts; the **split** is proven statically, not behaviourally (§13.4) |
| 7 | The double-apply disclosure | **FAIL** | the disclosure **names** the consequence, but the bound it attaches is **FALSE** — and the guideline now **contradicts itself** (§13.6) |
| + | PROBE #1 announced vs compacted span | **PASS** | **Δ = 0** on both swap legs |
| + | PROBE #4 short context | **PASS** | no swap, **arm STILL ARMED**, honest receipt, zero "summarized messages" claims in the whole checkpoint history |
| 8 | Whole-tree Tier-2 | *see §13.8* | pending |

### 13.2 Items 1 + 4 — the mutation-bite legs (six, all PASS)

One archive, legs serialised, each mutation **restored and re-proven** by sha256 equality **and** a green re-run. Every mutation was deterministic (no 3× re-run needed).

| Leg | Pin | Mutation | Reddened | Stayed green |
|---|---|---|---|---|
| 1 | `TestN4ArmWriteVariantMatrix` | restore the interim buggy `_write_instance_metadata` (write result discarded, success reported) | none-write, partial-write | `test_full_write_arms_cleanly` |
| 2 | `test_the_five_refusal_message_templates_are_pinned` | **one character**: `"Nothing was armed."` → `"armedx."` | the pin | — |
| 3 | `test_f3_the_settle_warning_label_matches_the_actual_outcome` | hardcode the settle-warning label to `compacted` | the pin | — |
| 4 | `TestHonestTruncationMarker` | revert the honest marker to the lying *"SECTION DETAIL below remains complete"* | `never_claims_section_detail` | `default_marker_keeps_the_legacy_clause` |
| 5 | `TestRenderedRefusalMessages` | change the over-cap branch's **`.format()` binding** (`note_chars+1`) — template constant untouched | `note_over_cap` only | the other 4 branches |
| 6 | `test_the_receipt_carries_the_configured_model` | `_configured_model` returns the **vision** model instead of the configured one | the configured-model test | `test_the_audit_line_names_the_served_model` |

Leg 3's evidence is the sharpest: the captured audit row still read `outcome=channel_rebuild_failed` while the warning label said `compacted` — **the label alone lied**. Leg 5 targets the **wiring** rather than the template text, which is what the class actually claims to pin.

### 13.3 Item 3 — the pack at the tip

`RESULT: PASS` · exit 0 · **459 passed / 0 failed / 0 errors / 0 skipped** · 35.61 s pytest / 42 s wall.
- **Claimed vs actual: 459 expected, 459 actual.** Independently corroborated: `--collect-only -q` over the same 12 files ⇒ **459 collected == 459 passed** (nothing silently skipped or deselected).
- **Per-file growth accounting (447 → 459, +12):** +9 `test_prompt_reload_compact_note.py` (57→66), +1 `test_compact_note_graph_wiring.py` (37→38), +2 `test_compaction_compact_note.py` (1→3), **0** across the other nine — the claimed additions landed exactly where the tip said.
- The pack's RC discipline survived the fold-in: `set -uo pipefail` (`:63`, no `-e`), `RC=$?` on its own line (`:102`) ⇒ `RESULT: FAIL` reachable; dual-layer timeouts intact; per-variable `POSTGRES_*` self-scrub. The run did not dirty the tree (pack script md5 identical before and after).
- The stale `len(REFUSAL_CODES) == 9` red is **fixed, not carried**: `test_prompt_refresh_primitive.py:148` reads `== 14` and passes (base still `== 9` at `:146`).
- All 8 named pins green across 15 nodes.

### 13.4 Items 2 + 6, PROBE #1, PROBE #4 — the live smoke

- **(a) swap lands PASS:** channel 18 → 11, doc at `#1`, `compacted_at` advanced, `tail_spliced=10`.
- **(b) routing PASS:** the turn ENDED — instance `completed`, job finalised, **no re-fire, no second compaction invoke**.
- **(c) note occurrences PASS (designed):** **zero daemon-rendered** occurrences; the non-zero totals are the model quoting the note it was handed plus the preserved user message that carried the note text.
- **(d) receipt PASS:** raw receipt carries `outcome=armed`, `note_chars`, `note_sha256`, `model_configured=agentic`, `cap`, `superseded_previous`, `path=append`, `selection=…`, and the "CONTEXT NOT YET COMPACTED" clause. No note body anywhere agent-readable.
- **(e) provider-cache CANNOT-VERIFY BY CONSTRUCTION:** `usage_metadata` present but `input_token_details` is **empty `{}`**; no daemon instrumentation; a direct two-call probe with an identical ~1.8k-token prefix exposes **only** `['completion_tokens','prompt_tokens','total_tokens']` and **no** cache headers. The compaction reply's own usage is unobservable in principle (consumed by the swap, never checkpointed separately).
- **PROBE #1 PASS, Δ = 0** on both swap legs (announced `#1–#5` == compacted `#1–#5`; `preserved verbatim: 10` matches the 10-message tail). The H3 *"agree by construction"* claim is **supported** — both sites run the same `identify_boundary_groups` + `select_compactable_groups` over the same exchange-stripped set, with the same parameters. **One structural asymmetry named:** the swap side's `else` fallback was deleted as unreachable (F4), while the **append side keeps a reachable `else`** (`graph.py:13630-13631`, `span_end = len(kept_msgs) if kept_msgs else 1`) — reachable precisely in the short-context case, where it announces `#1–#5` over a set nothing will compact. So on that one path *"announced = selected"* does **not** hold (by design: the announcement must exist before the selection can be judged).
- **PROBE #4 PASS:** 4 messages before → 9 after, **nothing removed**; `grep -c "summarized messages"` over the **entire checkpoint history** = **0**; arm **STILL ARMED** (`compact_note_arm` holds the note, sha matches the receipt, `last_deferred="no_doc"`, `last_doc_id=""`, `tail_spliced=0`); daemon logs *"no compaction needed (`len(groups)=5 <= recent_window=10`) — the arm stays armed"*, `outcome=deferred`, *"settle outcome=deferred result=no_doc — the arm was NOT consumed"*.
- **Parallel batch PASS:** the sibling `time` ToolMessage was **consumed with the group** — `name=time` → **0** and its timestamp → **0** occurrences in the final channel.
- **Context window 700000** on every line; the `4000` last-resort is dead code and **no resolver bug** was observed.
- **Both boot traps disarmed** (the tier-2 ambient-DB refusal and the **silent SQLite fallback**), and this run disarmed the first with a persisted `{"database":"postgres"}` in a scratch data dir — **not** `ENSEMBLE_DB_AMBIENT_ALLOW=1`. Boot log: *"Loaded ensemble config: database=postgres"*. The worktree's `data/instances.db` mtime stayed 07:25 (the SQLite path was never taken).

**ITEM 6 — PASS on presence and on the documented wording, with the discriminating limit stated.** The receipt carries `model_configured=agentic` on all three receipts; the audit line carries `agent_model=agentic` and **never** `model_configured=`. **But both surfaces read `agentic` in this run**, because `agentic` is both the configured model and the model that answered — so **equal values cannot distinguish "two fields wired to two sources" from "one field printed twice"**. The split is therefore proven **statically and by pin**, not behaviourally: the receipt reads `manager.config.llm.model` (`_configured_model`, `prompt_reload.py:159-168`); the audit reads `_compact_note_effective_model(state_msgs, llm_config, llm_standard, reply_msg)` (`graph.py:13692-13719`), which **prefers `reply.response_metadata.model_name`** — different functions, different inputs, different call sites. Two pins assert the split (`test_the_receipt_carries_the_configured_model`; `test_the_audit_line_names_the_served_model`, which asserts `agent_model=vision` **and** that `model_configured=` is absent from that line). **The failover divergence is UNMEASURED** — inducing one would require touching config/network, outside "report only".

**⇒ the two surfaces DO diverge in the cases that matter, and the receipt's "sized from" gloss is imprecise under the vision switch.** The sizing call at `daemon/graph.py:13595-13597` passes **no `reply_msg`**, so `_compact_note_effective_model` applies the **vision switch** (scans `state_msgs` for `image_url` blocks and returns `model_vision`), and that value then feeds `get_model_context_limit`. The receipt's `_configured_model` returns `manager.config.llm.model` **unconditionally**. So on a **vision turn** the receipt names a model that is **not** the one the span was sized from — while `_configured_model`'s own docstring claims it is (*"The CONFIGURED model the compact-note span is SIZED from, at ARM time"*), and the guide's gloss *"the model the compaction is SIZED from"* inherits it. The **primary** distinction the bullet makes (configured vs served) is correct; the *"SIZED from"* gloss is inaccurate under the vision switch. *(This corrects my own interim characterisation, which held only for text-only turns.)*

### 13.5 Item 5 — FAIL: the static pack is green, the symbol-driven check is red

**The disagreement is the finding.** The tip's pack is **459/459 green**; the AST-derived affected-symbol set pulls in **127 files the static 12-file pack does not contain**, and one of them carries a **deterministic net-new red**:

```
FAILED tests/unit/services/test_proactive_compaction_ns_trap_fix.py::TestNsTrapCLETargeting::test_cle_guard_lineno_pin_secondary
  E   AssertionError: CLE empty-snapshot guard now sits at line 9926 (pinned: 9910) - re-compute _CLE_GUARD_LINE / _CLE_REPLACEMENT_LINE via an ast.If walk
     assert 9926 == 9910
FAILED tests/unit/services/test_proactive_compaction_ns_trap_fix.py::TestNsTrapCLETargeting::test_empty_replacement_lineno_pin_secondary
  E   AssertionError: CLE empty-replacement branch now sits at line 10088 (pinned: 10072) - re-compute _CLE_GUARD_LINE / _CLE_REPLACEMENT_LINE via an ast.If walk
     assert 10088 == 10072
```

**Independently re-verified by the tester with my own AST walk** over `daemon/graph.py` at both shas, using the test's own anchors (`[Compaction][CLE] empty snapshot read`, `[Compaction][CLE] empty replacement`):

| sha | guard-If lineno | replacement-If lineno |
|---|---|---|
| base `858a85e31` | **9910** | **10072** |
| tip `bf5c21efe` | **9926** | **10088** |

The test hardcodes `_CLE_GUARD_LINE = 9910` / `_CLE_REPLACEMENT_LINE = 10072` at `:342-343` and those constants are **identical at base and tip** — so the lane never touched the test; the lane's compact-note additions inserted a uniform **+16 lines above the CLE block**. And `grep -c test_proactive_compaction_ns_trap_fix` against the tip's pack script = **0**: **the file is not in the static pack.** That is why 459/459 is green and the tree is still red.

**Scope, stated honestly: this is a TEST-SIDE tripwire, not a production break.** These are the *secondary* drift tripwires; the file's own docstring says the content-anchored contract tests are *"the primary anchors and never break on line drift"* — and those pass. The CLE guard's behaviour is unchanged; the lane moved it 16 lines. The test's own docstring even prescribes the fix: *"the two `_CLE_*_LINE` constants must be re-computed deliberately (ast.If walk), not blindly bumped"* — done above: **9926 / 10088**.

**Everything else in the widening leg is clean:** base **67** distinct FAILED ids vs tip **69**; **BASE-only = 0**; **FINAL-only = exactly these 2 ns_trap ids**; every other red inherited and named. The fold-in's one new symbol (`_configured_model`) pulls exactly **one** additional file — `tests/unit/test_compaction_compact_note.py` — which **is** in the pack and green. Reverse check: the pack's two non-symbol-derived files are both green, so the pack is a **superset** there, not a miss.

**COVERAGE BOUNDARY (stated):** derivation = AST node-level source-segment diff of the 7 changed `daemon/` modules + module-bound reference grep. **Measured:** the direct set only (137 files, per-file **isolated**, both legs; tip 2986 node-executions, base 2878; collected==executed proven per file). **NOT measured:** the transitive-only set (1625 files, enumerated), the focused set (413 files) and the tight-value set (114 files) — derived, not run. So the **symbol-boundary** surface is covered; **text-only mentions, value-only assertions and import-only reach are not**, and a net-new red in those regions **cannot be excluded** by this gate. 13 files are RC5 no-ops (collect 0 tests alone — conftests/evictors), **not green**.
**Correction to my own interim method:** the interim's 295-file *"focused"* slice was **not reproducible** from any import/token rule; a 413-file rule was substituted and the substitution disclosed.

### 13.6 Item 7 — FAIL: the disclosure names the consequence but attaches a FALSE bound

**The half that landed:** `guideline:36` now says *"a bounded **DOUBLE-APPLY** that re-compacts the **already-compacted** channel"* — a real improvement over the interim text, which named only a "re-appl[ication]".

**The half that did not: the rate-limit bound is FALSE**, settled by the tip's own function called against the tip's own measured post-crash carriers (`daemon.tools.prompt_reload._rate_limit_remaining_seconds`, imported from the pin):

| carrier | `last_applied_at` | remaining | would refuse |
|---|---|---|---|
| crash **before** settle (post-crash) | `None` | **0** | **False** |
| crash **after** settle (post-crash) | `2026-11-17T14:02:10.890365+00:00` | 846 | True |
| healthy control (landed) | `2026-11-17T14:02:06.070554+00:00` | 841 | True |

Mechanism: `prompt_reload.py:209-210` `if not last_applied_at: return 0` and `:488` `if _remaining() > 0:` (the **only** refusal). A pre-settle crash stamps **no** clock, so `remaining = 0` and the re-arm is allowed **immediately**.

**THE TREE NOW CONTRADICTS ITSELF — inside ONE FILE.** `guideline:36` says the double-apply *"is BOUNDED by the adjacency gate … and by the landed-only rate limit"*; `guideline:24` (same file) says *"REAL EXPOSURE: the clock is stamped ONLY on a LANDED compaction, so a NON-LANDING arm can be re-armed each turn (the rate limit does not protect a note that never lands)"*; `prompt_reload.py:200-203` says the same. `:36` is **self-refuting** — its own parenthetical states the fact that makes the bound inoperative. The mechanism supports `:24` and `:200-203` and refutes `:36`.

**The adjacency half is true but mischaracterized:** it bounds *when in a turn* the swap may fire, not *whether it recurs*. The measured recurrence gate is **"the agent arms again"**, which the bullet does not name. The no-rearm leg proves it: 1 agent call, **nothing** appended, `seam_messages_writes=0`, arm untouched.

**Proposed wording for `guideline:36`:** replace *"a bounded DOUBLE-APPLY"* with *"an UNBOUNDED-IN-TIME DOUBLE-APPLY (it recurs on every re-arm until something else clears the arm)"*, and replace the bound sentence with: *"bounded only by the adjacency gate WITHIN a turn; the recurrence ACROSS turns is bounded only by the agent choosing to re-arm — the landed-only rate limit does NOT bound it, because a pre-settle crash stamps no clock (`prompt_reload.py:209-210`, `:488`) and the arm can be re-armed immediately."*

**Both crash windows re-measured at `bf5c21efe`** (real `StateGraph` + real `AsyncSqliteSaver` + the real seam + the real settle; the healthy control in the **same batch**):
- **crash AFTER the settle ⇒ BENIGN** — `messages_len=11`, `compacted_at` **SET**, `next=None`, `pending_writes=[]`, arm **absent**; the resume **ran**, **0 agent-node invocations**, channel and arm unchanged.
- **crash BEFORE the settle ⇒ STILL OPEN** — channel **already compacted**, `compacted_at=None`, arm **PRESENT and armed**, `last_applied_at=None`; the resume runs 0 invocations and reconciles **nothing** (`read_compact_note_state` has no caller on the revival/resume path). On the next turn **both halves fire** (the tools node appends a fresh request over the already-compacted channel; the agent node's adjacency predicate is satisfied and it builds a second doc) — then the seam **refuses** the write (`seam_messages_writes=0`, `last_deferred='append_failed'`, arm stays armed).
- **The healthy control produced the IDENTICAL refusal** ⇒ **no landed-double-apply is claimed in either direction.** What is measured: the **trigger** fires, and the **state** it leaves. The **landing** is not — and the residual is a **separate defect surface**, orthogonally present at both tips (the seam's pre-write guard refuses because the engine's ids are not a subset of the site-derived set; `agent_state` serves a 12-message view while the node holds 15).

### 13.7 The two FAILs, precisely

**FAIL 1 — item 5, a deterministic net-new red the pack cannot see.** `tests/unit/services/test_proactive_compaction_ns_trap_fix.py` (2 nodes): the lane's +16-line shift above the CLE block moved two If nodes; the test's line pins are stale. **Test-side tripwire; production behaviour intact; the content-anchored anchors pass.** Fix: re-compute the constants to **9926 / 10088** via the ast.If walk the test itself prescribes (or replace the line pins with content-anchored assertions, as the file's philosophy prefers), **and add the file to the static pack** so the pack stops omitting symbol surface it claims to guard.

**FAIL 2 — item 7, a false safety bound in the operator-facing guideline, now with a self-contradiction inside one file.** `guideline:36` asserts the rate limit bounds the double-apply; `guideline:24` and `prompt_reload.py:200-203` assert the opposite; the mechanism sides with the latter. Fix: the wording change in §13.6 — a clause, not a code change.

Neither is a production-behaviour defect. Both are **false or stale statements in shipped artefacts**, which is why I am reporting them as FAIL rather than as notes: the first is a red a gate must see, the second is a bound an operator would rely on.

### 13.8 The F1/F2 + settle-observability mutation legs — all PASS

One archive, legs serialised, every mutation restored and re-proven. Baseline `4 passed` (F1, F2, F3, H7).

| Leg | Pin | Mutation | Result |
|---|---|---|---|
| **F1** | `test_f1_an_unperformable_read_back_is_unverified` (`test_prompt_reload_compact_note.py:1031`) | `read_compact_note_state`'s DB-error branch: `state.read_ok = False` → **`True`** | **BITES.** The receipt claims *"nothing was armed"* — a confident known-negative over a read that never happened: `assert "UNVERIFIED" in out` → `AssertionError: assert 'UNVERIFIED' in 'Error: prompt_reload FAILED — reason=prompt_reload_internal_error: … nothing was armed'` |
| **F2 (a)** | `test_f2_an_older_note_is_named_not_advertised` (`:1038`) | restore the interim semantics: `new_is_present = bool(read_state.armed)` | **BITES.** The NEW digest `c591e0bcf439` is **advertised** as the armed note while the carrier still holds the OLD note `0bac9d3f9878` — the swap would execute the OLD note: `assert pr.compact_note_sha256_12("THE NEW NOTE") not in out` |
| **F2 (b)** | same pin | present the OLDER note as the current/armed one | **BITES.** The "did NOT land" disclosure disappears: `assert "did NOT land" in out` → the receipt says *"the compact note is ARMED — the carrier holds the armed note (sha256 prefix 0bac9d3f9878); it will execute on the next invoke."* |
| **settle (a)** | `test_h7_the_settle_reports_an_unsettled_landing` (`test_compact_note_graph_wiring.py`) | restore the interim bug: return `"landed"` when the arm was never consumed | **BITES.** `assert result != "landed"` → `AssertionError: assert 'landed' != 'landed'`; the *"arm was NOT consumed"* warning disappears and the audit row becomes `outcome=compacted` while the arm is still armed |
| **settle (b)** | `test_f3_the_settle_warning_label_matches_the_actual_outcome` (`:1260`) | break the label/outcome correspondence (hardcode `"compacted"`) | **BITES.** `assert "settle outcome=channel_rebuild_failed" in joined` — the captured row still said `result=channel_rebuild_failed` while the label said `compacted` |

**SEAM DISTINCTION worth putting in the lane's ledger:** the tip's `test_f3_…` is a **log-LABEL** guard; the **return-value** guard is `test_h7_the_settle_reports_an_unsettled_landing`. Two different pins cover the settle surface, and each mutation bit exactly one of them — a future reader must not treat `test_f3_…` as the return-value pin.

**Integrity — the strongest proof of the whole gate, and proven NON-VACUOUS:** the extracted tree was verified by comparing **every** extracted file's git-blob sha against the committed object (**ALL 16005 BLOBS MATCH**, DIFF-COUNT 0, byte total 336 747 148 == git total) — not merely a file count, which can match while a file is half-written. The checker was then **deliberately tampered** to show it reports `DIFF-COUNT 1` **naming the tampered path**, and restored to show `DIFF-COUNT 0`. Post-restore regression on the restored copy: **459 passed**, FAILED count 0.

**Concurrency note:** a concurrent lane wrote `.pytest_cache/v/cache/nodeids` in the source worktree at 14:19 (not this worker's — 0 occurrences of its node ids or scratch path in that file, and its `$CP` has no `.pytest_cache`, proving cwd=`$CP`). Its proof is immune regardless: `git archive` reads the **committed object**, so a concurrent lane's dirty files cannot contaminate it.

**Also observed:** the unmutated settle behaviour matches the tip's claim exactly — return value `'landed_unsettled'`, with both warnings (*"the carrier exposes NO arm delete helper … the arm was NOT consumed"* and *"the swap landed but the arm was NOT consumed … the compaction will re-run"*), audit INFO `outcome=landed_unsettled`, and the arm still armed.

### 13.9 Whole-tree Tier-2 dual-side — **8 DETERMINISTIC NET-NEW REDS**

`HEAD bf5c21efe` vs `BASE 858a85e31`. 38,738 HEAD nodes / 38,629 BASE nodes executed; **closure exact on both legs** (per shard `P+F+E+S+XF == nodes`; HEAD 37,821+547+22+342+6 = 38,738 == collected; BASE 37,721+538+22+342+6 = 38,629 == collected). Method = the in-repo recipe `.agents/tester/skills/whole-tree-base-proven-diff/` (recipe won on method; conflicts disclosed §13.9.3). 16 file-boundary shards (1,295–3,034 nodes, cap `timeout 1750`), probe-measured 824 nodes/min ⇒ 47 min/leg projection, advisory breach disclosed (~4 h 15 m total, one re-scope per the recipe).

**The 3× solo adjudication ran 21 candidates × 3 runs × 2 legs = 126 solo runs** — 7 of the 15 net-new candidates and **all 6** "fixed" candidates dissolved under isolation (flakes / red-at-both-legs import-order artifacts). **8 survive.**

**NET-NEW — 8 deterministic reds (3/3 red HEAD, 3/3 green BASE), all lane-caused, all NOT QUARANTINED:**

| nodes | mechanism |
|---|---|
| `test_proactive_compaction_ns_trap_fix.py::TestNsTrapCLETargeting::{test_cle_guard_lineno_pin_secondary, test_empty_replacement_lineno_pin_secondary}` | `daemon/graph.py` grew **+16 lines above the CLE block** ⇒ the AST line pins (9910/10072) are stale. **Independently re-verified by this leg's own AST walk: guard 9910→9926, replacement 10072→10088; the test's constants unchanged at both shas.** |
| `test_source_gc_repository.py::TestP11BootNoBehaviorChange::test_post_merge_span_exactly_sanctioned_hunk` | `daemon/constants.py \| 13 +++++++++++++` — *"unexpected post-merge drift in NOT-TOUCHED paths"*. The P11 sanctioned-span guard requires a sanctioned marker on the hunks (POK: `source-gc-drift-guards-branch-tip-sensitive`). |
| `test_guidelines_registration.py::{TestParserHappyPath::test_c1_listing_serves_the_five_shipped_files, test_every_granted_agent_can_list_and_read[ari\|devops\|leader\|sonic-leader]}` (5 nodes) | the shipped-guidelines **count pin says 5, the lane added a 6th** (`guideline-prompt-reload-compact-note.md`) ⇒ `assert 6 == 5`. |

**All three mechanisms are the branch's own additions shifting pins that assert on adjacency (line numbers, file-count, sanctioned-span) rather than on behaviour.** The **109 lane-added nodes ran separately and ALL 109 PASSED** — the lane's own tests are green; these 8 are *other lanes'* pins reacting to the branch's insertions.

**FIXED: 0 genuine fixes** — all 6 candidates (atomic_dequeue, perf ×2, drill_smoke, bash_ram_guard, frozen_tool) dissolved under 3× solo; each was a timing/concurrency flake red in the BASE shard.

**PRE-EXISTING: 554 red node ids, every one named** (with the quarantine adjudication: 216 direct row matches, 248 family matches, 105 with no row matched — and an honesty note that the family matcher has a known false-negative, so `[NOT-Q]` means "no row matched by my matcher", not "no row exists").

#### 13.9.1 Coverage boundary
INCLUDED: the whole `tests/` tree minus two recipe exclusions (`tests/packs/**` — the g7 `sys.exit` collection trap, compensating pin = packs run via their registered scripts; `tests/e2e/test_context_injection_hybrid.py` — import-time HTTP GET, compensating pin = `e2e_workflows_ensure_test`). 1,271 nodes deselected by the repo's own `addopts` marker filter — **identical count on both legs**. 1 BASE-only node not executed (`test_refused_vocabulary_is_nine_plus_one_failed` — present at BASE, **removed at HEAD**, no counterpart, cannot be net-new or fixed). **Disclosed deviation:** the recipe's §1 collection command omits the `tests/performance/test_message_api_cost.py` exclusion its own pre-exclusion table mandates; followed literally, its 12 nodes ran, 6 red at BOTH legs (pre-existing) so the diff is unaffected.

#### 13.9.2 Material conflict, disclosed (recipe won on method)
The task fence (`POSTGRES_DB=ensemble_dev`) **HARD-ABORTS 4 shards** — the repo's env-poison 3-guard session fences allow only `{ensemble_dev_test_only, ensemble_test_only}` or unset, and under xdist the abort surfaces as `INTERNALERROR rc=3` with a *random* crashitem. Those 4 shards were re-run **serially with `POSTGRES_*` scrubbed per recipe §3, identically on both legs** — the diff stays apples-to-apples and no shard was dropped. **Recipe amendments proposed:** state the POSTGRES-scrub requirement in §3, and add the `test_message_api_cost.py` exclusion to §1, so the next lane does not rediscover the 4-shard abort.

#### 13.9.3 Hygiene
Porcelain 0 on both worktrees before and after; no commit/push/merge/rebase/checkout/reset/stash/clean anywhere. Disk 14 G → 13 G free; never below 3 G; **no clone, no uv sync, nothing skipped for space**. The reviewer's parallel confirmation was observed running against the same BASE worktree — handled by the mandatory 3× solo adjudication (7 candidates dissolved), and no reviewer process was disturbed.

---

## 14. FINAL MERGE VERDICT at `bf5c21efe`

### 14.1 Verdict table

| Item | Verdict |
|---|---|
| 1 — H6/H7 arm-write matrix bites | **PASS** |
| 2 — live smoke (a)(b)(c)(d) · (e) CANNOT-VERIFY by construction | **PASS** |
| 3 — the pack: 459/459, 12 files, `RESULT: PASS` | **PASS** |
| 4 — message pins + all four new pins (11 counter-mutation legs) | **PASS** |
| 5 — AST widening + disagreement check | **FAIL** |
| 6 — the receipt's `model_configured` | **PASS** (split proven statically; failover divergence unmeasured) |
| 7 — the double-apply disclosure | **FAIL** |
| 8 — whole-tree Tier-2 | **FAIL** — 8 deterministic net-new reds |
| PROBE #1 / PROBE #4 | **PASS** (Δ = 0; no swap, arm armed, honest receipt) |

### 14.2 The verdict: **NOT APPROVED AS-IS — 9 specific, cheap, named defects; production behaviour intact**

**Production behaviour is clean.** The 109 lane-added nodes all pass; the live smoke passes end-to-end (swap lands, routing END, Δ = 0, short-context no-op with the arm armed and an honest receipt, group-atomic sibling consumption, zero daemon-rendered note occurrences, `model_configured` present); eleven counter-mutation legs all bite; the pack is 459/459.

**What blocks is 9 items — 8 deterministic net-new test pins and 1 false disclosure — every one of them a mechanical fix, none of them a code change:**

1–2. **ns_trap line pins** (`test_proactive_compaction_ns_trap_fix.py`): re-pin **9910→9926** and **10072→10088** via the ast.If walk the test itself prescribes (or replace with content-anchored assertions), **and add the file to the static pack**.
3. **P11 sanctioned-span pin** (`test_source_gc_repository.py`): `daemon/constants.py` +13 needs a sanctioned marker, or the pin must be updated.
4–8. **Shipped-guidelines count pin** (`test_guidelines_registration.py`, 5 nodes): the count is **6** now, not 5 — update the pin (the lane added a sixth shipped guideline).
9. **`guideline:36`'s rate-limit bound is FALSE** and the file now contradicts itself (`:36` vs `:24`): replace *"a bounded DOUBLE-APPLY … BOUNDED by … the landed-only rate limit"* with the wording in §13.6 — an **unbounded-in-time** double-apply, bounded only by the adjacency gate *within* a turn, with the recurrence bounded only by the agent choosing to re-arm.

**Two secondary items, not blocking but recorded:** the receipt's *"SIZED from"* gloss is inaccurate under the vision switch (§13.4); and the append side keeps a reachable `else` fallback so on the short-context path announced ≠ selected (§13.4).

### 14.3 Recommendations to the lane owner
- Fix the 8 pins in-lane (all mechanical; two are already in the lane's own PACKS.md surface) rather than routing to test-debt — they are *this lane's* insertions that stale them.
- Add `test_proactive_compaction_ns_trap_fix.py` to the static pack.
- Apply the `guideline:36` wording fix.
- Amend the whole-tree recipe per §13.9.2.

**Durable record:** `.agents/tester/RESULTS/2026-11-17-prompt-reload-compact-note-gate.md`

---

## 15. NARROW DELTA CHECK at `08c0d1d98` (the pre-merge check)

**Tip:** `08c0d1d981a8f5518673d2fe55d812c2dffd54f7` · 54 commits from base · porcelain 0 before and after · **preflight `df -h /`: 12 G free (95%)** — above the ~3 G bar
**Scope:** NARROW — the nine items + the pack count + the validator. Not a full gate.
**The delta commit touches exactly 6 files with NO production code** (verified): `.agents/tester/PACKS.md`, the guideline, the pack script, `test_source_gc_repository.py`, `test_proactive_compaction_ns_trap_fix.py`, `test_guidelines_registration.py`.

### 15.1 The nine items — verified statically by the tester at `08c0d1d98`

**Items 1–2 (ns_trap line pins) — VERIFIED, and the re-derivation is real.**
- The constants read `_CLE_GUARD_LINE = 9926` / `_CLE_REPLACEMENT_LINE = 10088`.
- **The tester's own AST walk at `08c0d1d98` returns exactly 9926 / 10088 — MATCH** (same method: the test's own anchors, an `ast.If` walk over `daemon/graph.py`). The values are correct by independent derivation, not a pasted number.
- **The pin is genuinely walk-driven:** `node = self._if_node_with_body_constant(self._GUARD_ANCHOR)` → `assert node.lineno == self._CLE_GUARD_LINE` — the WALK locates the node by the anchor string in its subtree; the constant is only the expected value. Not a copied line number in disguise.
- The history comment documents the full hop chain (9574/9736 → 9872/10034 → 9910/10072 → 9926/10088, citing the 2026-11-11 re-pin precedents) and states **the PINNED CODE is byte-identical base-vs-head — only the line anchors drifted.**

**Item 3 (P11 sanctioned-span marker) — VERIFIED statically; the leak check is the mutation task.**
`COMPACT_NOTE_LANE_SANCTIONED_PATH = "daemon/constants.py"` and `COMPACT_NOTE_LANE_MARKER = "compact-note"` at `:754-755`, added to the **existing** `SANCTIONED_LANE_MARKERS = ("decision", "memoir", "group chat", "compact-note")` — composing with the three precedent lanes rather than replacing them. The file pins **the exact added lines** of the sanctioned hunk as a content pin, and the comment states the scoping intent: *"any OTHER lane's constants.py drift still REDs."* Whether that holds under an actually-unsanctioned hunk is the mutation check (§15.2).

**Items 4–8 (the shipped-guidelines count) — VERIFIED, the derive is real.**
`EXPECTED_GUIDELINE_COUNT = len(tuple(REPO_DOCS_GUIDELINES.glob("*.md")))` at `:543` — a genuine filesystem derive, used by **both** asserts (`:560`, `:758`), with the rationale documented in place ("a new guideline file moves this automatically… the old hardcoded 5 had to become a derivation"). The stale "five" test names are gone.

**Item 9 (the disclosure) — VERIFIED, the file now agrees with itself.**
`guideline:36` now reads: *"a **DOUBLE-APPLY** that re-compacts the **already-compacted** channel. It is **UNBOUNDED IN TIME**: bounded only by the **adjacency gate WITHIN a turn** … and its **recurrence is bounded only by the agent choosing to re-arm** — the rate limit does NOT engage for a non-landing arm (the 900 s clock is stamped only on a LANDED compaction, so a crashed carrier's clock stays unset and the next arm is never refused)."*
`:24` still holds the truth it already held. **No contradiction remains.** The wording matches the tester's proposed fix (§13.6) in substance: unbounded-in-time, adjacency bounds it within a turn only, the recurrence gate is the re-arm, and the rate limit does not bound it.

### 15.2 The three execution checks — all PASS (my own runs, not inherited)

**TASK 1 — the pack: PASS.** `RESULT: PASS`, script exit 0, **471 passed / 0 failed / 0 errors / 0 skipped** (47 s wall; in-pack pytest 36.66 s). Collect-only corroboration: **471 expected, 471 actual** (collected == passed, nothing silently skipped). **File count = 13**, and `tests/unit/services/test_proactive_compaction_ns_trap_fix.py` **is among them** (12 nodes). Targeted: **ns_trap 12 passed · guidelines 43 passed · P11 guard 37 passed** — all green, zero `^FAILED` lines. The PACKS.md row reads "PASS 471/471 (13 files)". Dual-layer timeout confirmed intact; the script self-scrubs per-variable and its rc capture is red-reportable.

**TASK 2 — the validator: PASS, exact match.** `.agents/shared/planning/prompt-reload-compact-note/validate_ownership_chain.py` → **`checks: 76  FAIL: 0`, exit 0, VERDICT: PASS** — claimed 76/0/exit 0, actual identical.

**TASK 3 — item 3's leak test: THE SANCTION DOES NOT OVER-SANCTION.**
*Method disclosure:* the P11 pins diff a **committed span** (`last-first-parent-merge..HEAD`) and are working-tree blind — a bare archive copy would have died on `assert merge, "no first-parent merge found"`, a **false red**. The worker therefore built **synthetic git history inside the throwaway copy only** (plumbing: `git init` → C1 pre-drift → M first-parent merge with a distinct side parent → D = the tip tree), whose `M..HEAD` span exactly mirrors the real one (`daemon/constants.py` 13 insertions only). No real tree was touched.
- Integrity: `find` 16006 vs `git ls-tree` 16005 — **delta exactly 1 = the copied `.env`**; both mutated files' bytes verified == `git show 08c0d1d98:<path>` before and after.
- **Baseline: both pins GREEN** (`test_post_merge_span_exactly_sanctioned_hunk` and `test_the_compact_note_lane_constants_drift_is_confined_to_sanctioned_hunks`).
- **Counter-mutation:** an **unsanctioned, markerless** `constants.py` hunk (`SCRATCH_PROBE_TELEMETRY_INTERVAL_S: int = 42`, self-checked — zero occurrences of any of the four markers) → **the scoped pin WENT RED**, verbatim:
  ```
  FAILED tests/unit/repositories/test_source_gc_repository.py::TestP11BootNoBehaviorChange::test_the_compact_note_lane_constants_drift_is_confined_to_sanctioned_hunks
  ```
  with the assert body naming the exact added lines. **⇒ an unsanctioned constants.py hunk is NOT excused.** (The blanket span pin stays green under this mutation **by design** — `constants.py` is excluded from its others-check; the scoped pin is the designated teeth. Not a leak.)
- **Secondary:** with the lane's own sanctioned `constants.py` content present, the scoped pin is GREEN — the sanctioned hunk is excused as intended.
- Restoration proven by sha256 equality and a green re-run (2 passed); provenance asserted before and after.

### 15.3 NARROW DELTA CHECK — VERDICT

| Check | Result |
|---|---|
| Item 1 — guard pin re-derived | **PASS** (my own AST walk: 9926 — match) |
| Item 2 — replacement pin re-derived | **PASS** (my own AST walk: 10088 — match) |
| Item 3 — P11 sanction scoped, no leak | **PASS** (unsanctioned hunk REDs the scoped pin; sanctioned hunk excused) |
| Items 4–8 — derived guideline count | **PASS** (a real `glob()` derive, both asserts use it) |
| Item 9 — the honest bound | **PASS** ("UNBOUNDED IN TIME"; `:24` and `:36` now agree) |
| The pack | **PASS** — 471/471 across 13 files, own run, collect-only corroborated |
| The validator | **PASS** — 76 checks / 0 FAIL / exit 0, exact match |
| Preflight `df -h` | 12 G free (95%) — above the bar; nothing skipped for space |

**APPROVE-FOR-MERGE.**
