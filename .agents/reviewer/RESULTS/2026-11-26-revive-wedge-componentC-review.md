# Targeted Code Review — Component C (C4″) + C5/A1 + C3b — `fix/revive-wedge-terminalization`

- **Date:** 2026-11-26 14:20Z · **Reviewer:** reviewer[v2] (direct static review — the orchestrator's "no sub-agents" constraint overrides the default dispatch workflow)
- **Reviewed object:** COMMITTED `HEAD 9fc39c0561c9243f2c6a817a7ad6000e768e1ea3` (base `35ec50e3c`), worktree `/home/nea/ensemble-src-wt-revive-wedge`
  - `daemon/services/child_reports.py` blob sha256 `01a161c2a62af2b4110cbf0e39f11f53cf9c1f82a675c5290409768766b346dc` (= the ledger's pre-mutation sha)
  - `tests/unit/services/test_parent_completion_guard_pins.py` blob sha256 `aae97db3445144235ce7b23b0adf9ba7dac75cb5c677f16679d53f3cda3aa57c`
- **Method:** static reading + control-flow reconstruction + `git` fence proofs + ONE no-database dialect compile. **No test was executed, nothing staged, no commit, no instance action.** Every `file:line` below was re-verified against the COMMITTED blob (`git show HEAD:<path>`), not the working tree.
- **Spec:** `plan.md §7 ### Component C` (13 rows), `plan.md §15.3/§15.3.1`, `gate-matrix.md §11`, `decisions.md §9` (D20–D32, §9.6 D-A..D-G).

## Verdict in one line

**Mergeable AS-IS on the evidence reviewed — 0 🔴 · 2 🟡 · 6 🟢.** All twelve review items PASS; every adjudicated ledger row matches the implementation (the two disclosed divergences, D-A and D-G, are within their rows). The tester's formal legs are NOT covered by this review and were observed **in flight** in this worktree during it (see §F).

## Commit map (`git log --oneline 35ec50e3c..HEAD`, 12 commits)

| sha | role | files (`git show --stat`) | fence |
|---|---|---|---|
| `9fc39c056` | **C3b** — Component C shared surfaces | `.agents/tester/PACKS.md` +16 · `CHANGELOG.md` +8 · `release-staging.md` · `plan.md` · `decisions.md` · `gate-matrix.md` · `.agents/reviewer/RESULTS/2026-11-26-revive-wedge-code-review.md` +210 (7 files, +414/−48) | OK — see note S-6 (a 7-file bundle: 3 C3b surfaces + the 4 planner-owned plan files + the review doc) |
| `e66d25062` | **C5/A1** — tool-invocation lane record + counter | `child_reports.py` +54 · pin file +76 | OK — owned paths only |
| `fed66f78d` | **C4″** — call-site split + D32 own-queue exclusion + PC-9 Arm B + PC-10 | `child_reports.py` +147 · pin file +303/−110 | OK — owned paths only |
| `14332ff30` | **C4 (pre-cure, SUPERSEDED by fed66f78d)** | `child_reports.py` +146 · pin file +850 (new) | OK — owned paths only; its recency-in-shared-helper defect is cured by `fed66f78d` |
| `a6745d8e9` `d7df4b070` `dba725030` | C3 (plan set / CHANGELOG / PACKS A+B) | docs | not in scope |
| `3b61f9779` `67898c26b` `e2eaf3386` | C2b / C2a / C1 (Component A) | A-owned | not in scope |
| `4bf4c459f` `97bcdbf62` | Component B pins / feature | B-owned | not in scope |

Whole-range proof: `daemon/migrations/` diff **EMPTY**; `.env*`/`pyproject.toml`/`daemon/__init__.py`/`uv.lock`/`tests/test_api.py`/`daemon/registry.py`/`agents/`/`config/` diff **EMPTY**; `daemon/repositories/` diff **EMPTY**; the 5 read-only deps (`stale_running_sweep`, `mission_strand_healer`, `waiting_children_watchdog`, `mission_live_guard`, `instance_lifecycle`) diff **EMPTY**; 24 files in range; code identical between the census HEAD `e66d25062` and the tip (`git diff --stat e66d25062..HEAD -- daemon/ tests/` empty).

## Per-item verdicts

| # | Item | Verdict | Evidence (committed blob) |
|---|---|---|---|
| 1 | Dialect portability | **PASS — independently compiled** | `_watchdog_notice_exists_expression()` `child_reports.py:445-460` = `or_(MessageQueue.source.in_(WATCHDOG_NOTICE_SOURCES), *(MessageQueue.message_metadata[k].as_string().is_not(None) for k in WATCHDOG_NOTICE_METADATA_KEYS))`, consumed in a correlated EXISTS at `:4080-4085`. No raw `?`, no `text()` with `?`, no `has_key`, no raw SQL added (diff scan: the only `?`/`text()` hit is the docstring at `:451-452` that forbids them). ORM attribute `message_metadata` → DB column `metadata` (`message_queue/models.py:74-77`); rendered SQL says `message_queue.metadata`. **My compile (SQLAlchemy 2.0.48, `postgresql.dialect()`/`sqlite.dialect()`, `literal_binds`, no DB):** PG `CAST((message_queue.metadata ->> 'watchdog_notice') AS VARCHAR) IS NOT NULL`; SQLite `JSON_EXTRACT(message_queue.metadata, '$."watchdog_notice"') IS NOT NULL` — both verbatim the ledger's MEASURED strings; zero `?`; the FULL helper statement (count + EXISTS + recency) compiles on both. PC-10 (`pin:925-980`) compiles the production factory's object (`factory()` at `:954`) with no DB. |
| 2 | Call-site split (D29) | **PASS** (mechanism per D-A; see W-1) | Same helper, keyword-only `apply_recency: bool \| None = None` `:4010-4016`; ContextVar fallback `:4106-4107`; conjunct appended only if truthy `:4108-4109`. ROOT `with _recency_call_site(True):` `:4461` → call `:4462`. NON-ROOT `with _recency_call_site(False):` `:5442` → call `:5443`, consumed `:5446`. Only two production callers repo-wide. PC-9 Arm B present `pin:852-878` (exact `== "child_still_running_defer"`); RED under M-C6 by construction. |
| 3 | Frame (D30) | **PASS** | `now_utc_naive` imported `:13` (`timestamps.py:51`); threshold `now_utc_naive() - timedelta(seconds=P_C_RECENCY_BOUND_SECONDS)` computed Python-side once `:4093-4094`; no aware threshold, no `func.now()`; PG literal renders naive (`'2026-01-01 00:00:00'`). |
| 4 | Own-queue exclusion (D32) | **PASS** | `_is_watchdog_notice_row()` `:463-479` (same two layers, `is not None`); applied `:4797` inside the ROOT `pending_count = sum(...)` `:4793-4798`; no own-queue leg on the non-root path. `finalizer_counts_as_pending` UNTOUCHED (`daemon/repositories/` diff empty) and NO module gained a parameter (D-G) ⇒ no caller to enumerate. PC-4 at both legs `pin:594-637`. M-C8 ⇒ PC-1 RED by construction (the READY notice row then counts — `predicates.py:227-232` "READY: always counts"). |
| 5 | PC-1 = maintainer's specimen | **PASS** | `pin:383-436`: READY notice + PENDING task, zero children/watchers ⇒ `root_completed` `:415` + status COMPLETED `:419` + two constant drift-guards `:423-436`. At base the SEAM defers ⇒ RED. Precision note S-4: "on BOTH legs" is a mechanism claim — the own-queue leg is never reached in the base run; its RED is shown by M-C8 at merge. |
| 6 | PC-9 Arm A READY row non-watchdog | **PASS** | `pin:826-833`: `source="internal_agent:caller"`, `metadata={}`, READY; task age = bound+60 `:834-839`; asserts `!= "root_completed"` and status `!= COMPLETED` `:845-850`. At merge the deferral comes from the own-queue backstop — exactly `plan.md §15.3.1`. |
| 7 | No new non-test module; public path | **PASS** | Only non-test modules in range: `child_reports.py` and B's `system_log_tools.py`. Pin imports only pre-existing modules `pin:69-101` and reaches the seam via `service._process_child_completion_db_sync(...)` (`pin:409`, `:463`, `:527`, `:629`, `:691`, `:776`, `:840`, `:869`, `:889`, `:913`, `:1028`). Base-leg import-safety itself = orchestrator's claim (not re-run). |
| 8 | C5 record + locked increment, exactly one | **PASS** on the pinned exit; **W-2** on the second | `_emit_tool_lane_suppression_record` `:242-277` wraps `_emit_mint_suppression_record` `:199-239` (same fields; bump under `_ARM_B_SUPPRESS_LOCK` `:191-196`; `total=` read under the lock `:227-228`). Emits at `:5323-5330` (rowcount==0 → `idempotency_skip`) and `:5339-5346` (→ `tool_invocation_completed` `:5347`), both under `if suppress_mint:`, mutually exclusive (each followed by `return`), after `session.commit()` `:5315`; the lane returns before the shared suppress branch `:5535-5569` ⇒ no double emit. Pin `pin:983-1057`: exactly 1 record `:1044`, `loss=1` `:1049`, counter delta == 1 `:1054`, outcome unchanged `:1034`. RED if the emit (0 records) or the increment (delta 0) is removed. |
| 9 | INJECTED unchanged; CAS single writer; no stranded suppressed mint | **PASS** | `injected_report_exists` `:4068-4073` + `~injected_report_exists` `:4103` are the base's three chained `.where`s re-expressed as a list; the only removed non-comment lines in the Component-C range are the import line, the def signature, those `.where`s and the two call lines. No added `status=`/`.values(`/`sa_update(`/`session.add(`; the two terminal CAS UPDATEs (`:5298-5314`, `:5488-5504`) are pre-existing. Trace with `suppress_mint=True`: passes `:5261` → tool lane (CAS → terminal, or rowcount==0 = already finalized elsewhere) | non-root defer `:5446-5464` (the plan-sanctioned defer) | fall-through CAS → rowcount==0 return `:5518-5523` | suppress branch `:5535-5569`. No other return in between. |
| 10 | Fence | **PASS** | Per-commit table above; migrations/config/repositories/read-only-dep diffs all EMPTY; index clean. |
| 11 | C3b PACKS / release note | **PASS** | PACKS block (`PACKS.md:1-17`) is **byte-identical** (`cmp`) to `gate-matrix.md §11.5`'s fenced block (15 lines); begin-marker appears once; names **EIGHT** mutations M-C1..M-C8 and `P-C-OWN-QUEUE-NOTICES-EXCLUDED` ("a watchdog notice NEVER defers completion, at the pending-task seam OR at the root's own-queue leg"); placed at the top = the file's newest-first convention (the base file also opens with a dated block). PG status cell is honest (`-rs` form: "15 passed / 0 skipped / 2 pre-existing setup errors" for the PRE-EXISTING wanderer module; "'0 skipped' is NOT the claim"; never a bare RC=0). Release note (j) `release-staging.md:200` ("NEVER defers completion at EITHER site"); CHANGELOG `[Unreleased]` Component C entry `CHANGELOG.md:24-29` ("at the pending-task seam OR at the root's own-queue leg alike"). `release-staging.md:215`'s "MUST NOT be cut before the own-queue exclusion is implemented" is superseded by `:216` ("D32 IS NOW IMPLEMENTED IN CODE … the precondition it states is MET"). |
| 12 | Body vs ledger, character-for-character | **PASS** — all 13 rows | See the row table below. |

### Item 12 — row-by-row

| Ledger row | Code | Match |
|---|---|---|
| `P-C-SEAM` | helper `:4010`; ROOT call `:4462`; NON-ROOT call `:5443` | ✓ (ledger anchors `:3819/:4198/:5148` are the `67898c26b` frame by its own framing) |
| `P-C-NOTICES-EXCLUDED` | `:445-460`, `:4080-4085`, `:4104` | ✓ verbatim form; rendering = the ledger's MEASURED strings; `is_not(None)` ✓ |
| `P-C-WATCHDOG-SOURCES` | `:398-401` = `('system:watchdog', 'system:watchdog:wedge')` | ✓ values (S-2: literals, not imported) |
| `P-C-WATCHDOG-METADATA-KEYS` | `:407-412` — the 4 keys, same order | ✓ |
| `P-C-RECENCY` | `:4091-4095` `and_(Task.is_deferred.is_(False), Task.created_at < now_utc_naive() - timedelta(seconds=P_C_RECENCY_BOUND_SECONDS))` | ✓ (`IS false` / `IS 0` ≡ `= false` on the NOT NULL column `task/models.py:146-147`; NULL-safe) |
| `P-C-RECENCY-ROOT-ONLY` | `:4461` True / `:5442` False | ✓ behaviour; transport = D-A; W-1 on the default |
| `P-C-RECENCY-BOUND` | `:419` = `DEFAULT_MIN_PENDING_AGE_SECONDS` (`eligible_pending_sweep.py:69` = 60), imported `:26` | ✓ |
| `P-C-COUNT` | `:4100-4109` | ✓ |
| `P-C-OWN-QUEUE-NOTICES-EXCLUDED` | `:4793-4798`, `:463-479` | ✓ (the row explicitly permits Python-side filtering) |
| `P-C-DEFER-LOG` | `:4466-4471` — no diff hunk touches the line | ✓ text unchanged |
| `P-C-ZERO-WATCHER-FIRE` | PC-5 seeds only the root instance `pin:648-655` | ✓ fixture carries no own-queue row |
| `P-C-DIALECT-PIN` | PC-10 `pin:925-980` | ✓ |
| `P-C-FENCE` | fence proofs above | ✓ |

A/B integrity re-derived with the plan's own corrected command: **32 rows, sha256 `549e540b660ef2650e66bea4200a69cacbb7d8111579c358fd38870a06fb692f`** ✓; Component-C table **13 rows** ✓. The §7 row edits in `9fc39c056` are the leader-confirmed cures (portable form, naive frame, ROOT-ONLY, DIALECT-PIN, OWN-QUEUE); divergences are disclosed in `decisions.md §9.6` (D-A, D-B, D-D, D-E, D-F, D-G), not rewritten into the rows. D-B (M-C6 trips PC-9 Arm B **and** PC-3(a)) is correctly left for adjudication — my control-flow reading agrees with it: under M-C6 the ROOT stops applying recency, so `PC-3(a)` (`pin:532`) REDs too.

## Findings

### 🔴 Critical — none.

### 🟡 Warning

**W-1 — the recency switch fails toward the PREMATURE direction for any unknown caller.** `daemon/services/child_reports.py:430-432` — `_COMPONENT_C_APPLY_RECENCY = ContextVar("component_c_apply_recency", default=True)`. The ledger's `P-C-RECENCY-ROOT-ONLY` is an allow-list ("ROOT call site ONLY"); the implementation makes it a deny-list (recency is ON unless a caller opts out). Both current sites are correct, but a future third call site — or any direct 2-argument call outside `_recency_call_site(False)` (two exist today: `tests/postgres/test_report_deferred_migration_pg.py:1502`, `:1520` — unaffected because their tasks are fresh) — silently gets the ROOT behaviour, which D29 names as the fleet-wide premature-report class; the accepted residual is "a delay, never a premature report", and this default fails the other way.
- **Fix:** `default=False` (one token). Both sites set the value explicitly, so behaviour at `:4461`/`:5442` is unchanged and all 11 pins stay GREEN; or, if the leader prefers the current direction, record the choice in `decisions.md §9.6` D-A. Re-run the pin file + `test_child_reports.py` after a flip.

**W-2 — C5 records a LOST-RACE suppression on the tool lane but the regular lane does not: an undisclosed ledger drift, and that exit is unpinned.** `daemon/services/child_reports.py:5316-5336` emits when the tool lane's CAS matches 0 rows. The ledger's emission point is "after the fall-through CAS **has succeeded** and committed" (`plan.md §3` steps 4–5; `P-ARM-B-SUPPRESS-LOG`), and the regular lane's equivalent exit `:5505-5523` stays silent — the prior code review's A2 documents that silence as plan-shaped. So `_ARM_B_SUPPRESS_LOSS_TOTAL` (presented as the R-K2 loss metric) now counts a lost race on one lane and not the other. The C5 pin drives only the rowcount≠0 exit, and `decisions.md §9.6` does not record this (D-A..D-G are all Component-C pin notes). The C5 brief said "mirror the suppress branch", which emits only after a successful CAS.
- **Fix (pick ONE definition, apply to BOTH lanes):** (i) ledger-conformant — delete the emit at `:5323-5330` (the lost-race under-count stays the disclosed A2 residual for both lanes) — **recommended**; or (ii) the leader redefines "actual suppression" to include lost races ⇒ keep the tool-lane emit, add the same emit at `:5518-5523`, record it as §9.6 D-K, and pin both exits. Observability-only; no control-flow impact ⇒ not blocking.

### 🟢 Suggestion

- **S-1 — own-queue filter order** (`child_reports.py:4796-4797`): `finalizer_counts_as_pending(_row, engine) and not _is_watchdog_notice_row(_row)` runs the finalizer first — one task-existence SELECT per READY row (`predicates.py:360-362`) and possibly the "task-less READY row … different bug class" WARNING (`predicates.py:374-385`) — for a row that is then discarded anyway (e.g. a stranded watchdog notice whose Task was cancelled, the R-C3/PC-8 steady state, on a later root re-evaluation). Reversing the conjunction is semantically identical and skips both.
- **S-2 — sources re-declared as literals** (`:398-401`) while the bound is imported (`:419`, "imported, never re-declared"). PC-1's drift guard (`pin:423-428`) covers it; import `WATCHDOG_SOURCE`/`WEDGE_SOURCE` if there is no import cycle, else leave.
- **S-3 — PC-9 Arm A accepts any non-`root_completed` outcome** (`pin:845-850`); the §15.3.1 clarification names exactly two admissible deferral outcomes (`deferred_waiting_children` | `root_waiting_children`). Tightening to that pair would also reject an unexpected `idempotency_skip`.
- **S-4 — ledger wording precision:** `gate-matrix.md §11.3` PC-8's base cell reads "n/a (new behaviour at merge)" while `plan.md §15.3` says GREEN, the pin says STAY-GREEN (`pin:757-760`), and the reported true-base run has PC-8 among the 8 passes. "RED at base on BOTH root legs" (PC-1) is a mechanism claim — the own-queue leg's RED is shown by M-C8 at merge, not by the base run.
- **S-5 — process:** the documentation lane (C3b) appended an ADDENDUM to the reviewer's RESULTS doc (`.agents/reviewer/RESULTS/2026-11-26-revive-wedge-code-review.md:200-210`) — clearly labelled and verdict-preserving, but not reviewer-authored. The orchestrator already holds this open for the leader (confirm or drop).
- **S-6 — attribution:** `9fc39c056` bundles C3b's three surfaces with the 4 planner-owned plan files (the leader-confirmed cures, previously uncommitted) and the review doc; inside the plan files the planner's cures and C3b's §15.8/§9.6/§11.5 edits are inseparable post-hoc. Sanctioned by the C3b brief; recorded so nobody reads the commit as single-author.

## F. Concurrency observation (not a finding against the branch)

During this review the working tree of the lane worktree was being **mutated in place by the gate's mutation legs** (gate-matrix §3 harness: "Apply mutation → `cd $WT …` → restore → verify sha256"):

| time (UTC) | working-tree `child_reports.py` sha | uncommitted content |
|---|---|---|
| review start | `01a161c2…` (= HEAD) | clean (only the 17 giter-owned `file:memdb*` rows) |
| 14:16:59 | `eddced9c…` | `MUTATION L2d D1` (COMPLETED dropped from the terminal-entry tuple) + `L2d D2` (CAS rowcount-0 return suppressed) |
| 14:20:10 | `5feb3caa…` | `MUTATION M-C5` (notices exclusion + recency conjunct removed; INJECTED kept — the D-D form) |

HEAD stayed `9fc39c056`, the index stayed clean, the pin file stayed `aae97db3…`. Every line cited above was re-verified against the COMMITTED blob (`git show HEAD:…`), so none of this review's evidence depends on the mutated working tree. **Before anything is staged from this worktree:** `sha256sum daemon/services/child_reports.py` must read `01a161c2a62af2b4110cbf0e39f11f53cf9c1f82a675c5290409768766b346dc`.

## Must be true before the tester's gate (the exact GREEN assertions)

1. **PC-10** — `test_pc10_dual_dialect_portability_compile`: `"->>" in pg_sql` (`pin:969`), `"JSON_EXTRACT" in sqlite_sql.upper()` (`:973`), `"?" not in pg_sql and "?" not in sqlite_sql` (`:977`). GREEN at merge; **RED under M-C7**.
2. **PC-9** — `test_pc9_two_arm_premature_terminal_protection`: Arm B `result_b.outcome == "child_still_running_defer"` (`:874`) GREEN at base AND merge, **RED under M-C6** (D-B: PC-3(a) REDs too — adjudicate); Arm A `!= "root_completed"` + status `!= COMPLETED` (`:845`, `:850`) GREEN at both legs, with the fixture row at `:826-833` staying NON-watchdog.
3. **PC-7** — `calls == [root_id, child_id]` (`:741`) GREEN at both legs.
4. **PC-3** — `:532` `root_completed` (**RED at base**), `:556` `child_still_running_defer`, `:577` `!= root_completed`; `:499-508` bound == `DEFAULT_MIN_PENDING_AGE_SECONDS` == 60.
5. **PC-4** — `:613` `child_still_running_defer` (seam) and `:634` `root_waiting_children` (own-queue leg) GREEN at both legs; **RED under M-C3**.
6. **PC-1** — `:415` `root_completed`, `:419` status COMPLETED, `:423-436` drift guards. **RED at base with PC-1's own message** (`:416-417`, not a collection error); GREEN at merge; **RED under M-C1, M-C5, M-C8**.
7. **PC-2** — `:469`/`:473` `regular_child_completed`, `:474` `parent_id`, `:476-482` a report row with the `internal_report:{child}:` prefix. RED at base; **RED under M-C1, M-C5**.
8. **PC-5** `:657-658`; **PC-6** `:697` `regular_child_completed` (**RED under M-C4**); **PC-8** `:783`, `:793-799` — stay-GREEN at both legs.
9. **C5/A1** — `test_c5_tool_invocation_lane_suppression_is_logged_and_counted`: `:1034` `tool_invocation_completed`, `:1044` exactly ONE record, `:1049` `loss=1`, `:1054` counter delta == 1; **RED if the emit (`:5339-5346`) or the increment (`:224-225`) is removed**.
10. **Neighbours** — `tests/unit/services/test_child_reports.py` 65 passed (the two 2-arg spies at `:2160-2167` / `:2254-2261` must keep working under the ContextVar transport); `tests/unit/test_ready_message_completion_report.py` 13 passed.
11. **True-base leg** (base tree + ONLY the pin file, identical command): exactly **3 failed / 8 passed**, the failures being PC-1, PC-2, PC-3(a), no collection/import error.
12. **M-C1..M-C8** each RED on their named pin(s); every restore byte-identical to `01a161c2…`.
13. **Fence re-proof at the merge base:** `daemon/migrations/` empty; config/version files unchanged; `daemon/repositories/` unchanged; no new non-test module.
14. **PG position:** Component C has NO postgres node — never accept or claim a PG leg on RC; the wanderer `-rs` figure is a no-regression claim about a pre-existing module only.
15. If W-1's default flip (or W-2 option (i)/(ii)) is taken: re-run 1–10 before the gate is stamped.

## Not verified by this review (explicit)

- Every pytest figure (11 passed; 65; 13; true-base 3/8; M-C1..M-C8; the wanderer PG run) is coder/dispatcher-reported — not re-run (static mandate; the mutation legs were live in the worktree).
- Base-leg import-safety of the pin file at `35ec50e3c` (claim).
- The identity of the concurrent writer (the mutations match gate-matrix §3(d) and §11.4 M-C5 verbatim).

## Mergeable as-is?

**Yes, on the committed evidence** — no 🔴; the implementation matches all 13 adjudicated rows; the fence holds; the dialect claim is independently reproduced; C5 closes the silent-suppression hole on the pinned exit. Conditions, none of which is a 🔴: (1) the working tree must be back at `01a161c2…` before anything is staged from it; (2) the formal legs 11–12 are still the tester's; (3) W-1 and W-2 are leader-adjudicable before or at the gate; (4) D-B's two-pin RED set awaits the leader's wording decision (already open).
