# Phase-2 Tier-1 Blast-Radius Gate — project skill source loader (delta run)

- **Date:** 2026-10-31
- **Commission:** Phase-2 Tier-1 blast-radius gate, delta run (skill-search fusion)
- **Worktree:** `/home/nea/ensemble-src-wt-project-skill-loader` (`feature/project-skill-loader`)
- **HEAD:** `0c4464ebe388da038750b70c17b4b8e4ba85dd4b` · **TRUE BASE:** `b56444244` · **Phase-2 delta base:** `1aacdb5c5` (Phase-1 gate evidence)
- **Delta:** 3 commits, 11 files, **+2089 / −16**; shipped-tree change = `daemon/services/skill_search_service.py` **+468/−7 (confirmed exactly)** + 4 new test files; **ZERO migrations** (confirmed empty)
- **Recipe:** `blast-radius-leg` **v1.0.1** + `failed-digest-equality-gate` **v1.0.1** — both loaded deterministically; v1.0.1 text VERIFIED to contain all three Phase-1 folds before running
- **Lanes:** `psl2-recon` 23155cdb · `psl2-legs` f1869d70 · `psl2-sem` 5d219d76 · `psl2-a7mut` f45bda90

---

## 1. Tier decision (taken BEFORE the run)

**Tier 1.** The mechanical escalation test was executed rather than eyeballed:

| Check | Result |
|---|---|
| `git diff --stat 1aacdb5c5..0c4464ebe` on the six premise files (`daemon/tools/skill_tools.py`, `manager.py`, `config.py`, `api.py`, `services/skill_injection_service.py`, `services/readiness.py`) | **empty** |
| `git log --oneline <range> --` same six paths | **empty** |
| Blob identity at both revs | identical for all six (`4d9dd5823a…`, `6bd0889b5f…`, `ce463df00b…`, `1d851df89e…`, `033b442aa9…`, `6d3a8b898d…`) |
| Core-seam list (`graph.py`, `manager.py`, `api.py`, `instance_messaging.py`, `task_processor.py`, `job_queue_service.py`) | **0 hits** |
| `daemon/services/skill_search_service.py` modified | yes — but **not on** the core-seam list ⇒ does **not** escalate |

**Consequence:** the fusion work lands entirely inside `skill_search_service.py`, and the tool layer (`skill_tools.py`) is deliberately untouched — the A8 guard is therefore *service-side by construction*, which §9 confirms behaviourally.

---

## 2. Declared set — recipe v1.0.1 two-class rule applied verbatim

The v1.0.1 recipe's own language was used this run (not an improvised equivalent). That is the usability test.

### D1 — lane-owned, HEAD-only (MUST PASS at HEAD; absent-at-BASE = expected by construction, never a failure, never a pass)

| Group | Files | Nodes |
|---|---|---|
| Phase-1 unit | `test_project_skill_source_{model,manifest,resolver,embedding,index,migrations}.py` | 123 |
| Phase-2 unit | `tests/services/test_skill_search_fusion.py` | 22 |
| | `tests/tools/test_skill_search_fusion_guard.py` (parametrized) | 2 |
| | `tests/unit/routers/test_skills_search_invisibility.py` | 5 |
| **D1 unit total** | | **152** |
| Phase-1 PG | `tests/postgres/test_project_skill_source_pg.py` | 12 |
| Phase-2 PG | `tests/postgres/test_project_skill_fusion_pg.py` | 3 |
| **D1 PG total** | | **15** |

### D2 — dual-side comparable (exists at both revs; carries the digest)
11 files, **273 nodes** — unchanged from the Phase-1 declaration, and per-file counts must match BASE exactly. The key witness is `tests/services/test_skill_search_service.py` (43), because `skill_search_service.py` is the one modified shipped file.

### Named pre-exclusion + compensating pin (recipe §f)
`tests/postgres/test_report_deferred_migration_pg.py` — **excluded** (29 pre-existing red nodes, now QUARANTINE.md **L171**). Compensating pin: the Phase-1 node-id digest `2fad8e8a775b66d7a35b4162e68f6627946d359e7dfe948fb8323b0658524c3d`, identical at both revs, `cmp` rc=0, both `comm` directions empty. Quarantine-aware per ensure.md ⇒ excluded, not re-litigated.

---

## 3. Slicing proof (executed == declared)

Executed by the replacement legs lane against two detached worktrees (`/home/nea/ensemble-src-wt-p2-base` @ `b56444244`, `…-head` @ `0c4464ebe`), per-tree `.venv` (Python 3.14.7), `daemon.__file__` asserted inside each tree.

- **BASE D2 = 273 collected** (rc=0): sqlite_guards 10 · legacy_ledger_shadow 2 · dup_prefix_guard 6 · skill_repository 103 · skill_embedding_service 50 · skill_store_service 30 · **skill_search_service 43** · ensure_deferred_schema_pin 7 · idle_gate_parity 1 · stored_watches_parity 6 · board_index_parity 15.
- **HEAD D2 = 273** — per-file counts **identical to BASE on all 11 files, diff-verified** (not eyeballed).
- **HEAD D1 unit = 152** (425 − 273): model 12 · manifest 48 · resolver 18 · embedding 7 · index 29 · migrations 9 · fusion 22 · fusion_guard 2 · invisibility 5.
- All 9 D1 files **absent at BASE** — confirmed on disk, as constructed (recorded as expected-by-construction per the v1.0.1 rule; not a failure, not a pass).
- Declared 273/273/152 → observed 273/273/152. **No deviations.**

---

## 4. Projection vs the 30-minute cap

Measured: collect 6 s (BASE) + 7 s (HEAD); unit legs BASE 18.04 s pytest / HEAD 40.74 s pytest. Projection confirmed trivially inside the cap — the §(c) "a scoped leg is MINUTES" wording held exactly. Actual gate-compute accounting in §11.

---

## 5. Dual-side legs (BASE first, then HEAD; identical command shape)

`timeout 600 env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_PASSWORD -u DATABASE_URL -u PGPASSWORD .venv/bin/python -m pytest <explicit files> -q --tb=short -p no:randomly -n 8`

| Leg | Files | Nodes | Result | rc | pytest wall |
|---|---|---|---|---|---|
| **BASE** | D2 (11) | 273 | **273 passed**, 0 failed, 0 errors, 9 warnings | 0 | 18.04 s |
| **HEAD** | D2 + D1 (20) | 425 | **425 passed** (D2 273 + D1 152), 0 failed, 0 errors, 9 warnings | 0 | 40.74 s |

Asymmetry stated per the v1.0.1 rule: BASE ran D2 only; HEAD ran D2 + D1 unit. The 9 warnings are the pre-existing pydantic-v1-on-3.14 `UserWarning`, identical on both legs (cosmetic). The **key witness** — `tests/services/test_skill_search_service.py`, 43 nodes, the pre-change corpus over the one modified shipped file — passed **identically on both legs**.

---

## 6. Real-PG evidence

### A7 pre-budget shadow — the load-bearing ordering claim (independent re-proof)

Exercised through the **production path** (`_merge_project_file_corpus` → `file_corpus_stats()`), not merely pass/fail:

| Leg | file_total | shadowing | file_included | budget_dropped | `injected[0].source` | twin in `shadows` | twin in entry ids |
|---|---|---|---|---|---|---|---|
| SQLite | **55** | **1** | **51** | **4** | `project-file` | **yes** `["fd1e01dc-…"]` | **no** |
| **REAL PG** | **55** | **1** | **51** | **4** | `project-file` | **yes** `["fd1e01dc-…"]` | **no** |

`shadowed_db_ids == [twin_id]` on both; `last_error == null`; twin ordered **LAST** (`z-ns/zz-twin-skill`) so a budget-first implementation would have truncated it. **The 55 / 1 / 51 / 4 claim is real**, and the twin is *retired* (stamped on the file twin's `shadows`), not merely budget-dropped.

PG ran inside the conftest fence: `PG_TEST_ALLOWED_DBS=ensemble_test_p2a7`, role `p2_a7` owning no live DB.

### HEAD-side lane PG corpus (real PG16.15, local 127.0.0.1)

- **rc=0 · 15 passed in 15.42 s** — `test_project_skill_source_pg.py` (12) + `test_project_skill_fusion_pg.py` (3), serial (no xdist), zero FAILED/ERROR, zero skips.
- Local server identity verified **before any mutation**: `inet_server_addr = 127.0.0.1/32` — **not a tunnel to 10.44.0.2**; the run connected only as the dedicated scratch role `p2_gate_head` on `ensemble_test_p2head`, declared exactly via `PG_TEST_ALLOWED_DBS`.
- BASE-side PG leg = **N/A by construction** (no lane PG file exists at `b56444244`; the only D2 PG file is quarantined/excluded). Stated, not fabricated around.
- Setup-auth deviation (disclosed): the dispatcher's bare `psql -h 127.0.0.1 -d postgres` failed (`fe_sendauth`) — no `~/.pgpass` on this host; the lane used the conftest-documented setup credential (`ensemble`/`ensemble_dev`, `conftest.py:68-73`) **for role/DB minting only**, then the test run used the scratch role exclusively. The lane's own fence guard also aborted once conservatively on a `127.0.0.1/32` netmask-vs-exact-string mismatch — **before** any mutation; identity then confirmed local. Both aborts are the fence doing its job.
- Teardown: `dropdb` + `DROP ROLE` rc=0; `\du` 0 rows, `\l` 0 rows, SQL cross-proof `role_rows=0, db_rows=0`; scratch password file deleted; both p2 worktrees removed (`worktree list | grep -c p2-` = 0); porcelain unchanged.

---

## 7. FAILED-list diff (recipe v1.0.1 GAP-2 form)

Digest form: sorted node ids with the `FAILED `/`ERROR ` prefixes stripped, captured with `grep -E '^(FAILED|ERROR)'` (v1.0.1 form — the old `^FAILED `-only grep is the exact blind spot this fold closes).

| | sha256 | meaning |
|---|---|---|
| BASE `/tmp/p2r-base.nodeids.txt` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | empty input (sha256 of empty) |
| HEAD `/tmp/p2r-head.nodeids.txt` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | empty input |

`cmp` rc=**0** (byte-identical) · `comm -23` (base-only → FIXED) = **EMPTY** · `comm -13` (head-only → **NET-NEW**) = **EMPTY**. No ERROR ids exist to explain.

**Diff set = ∅ ⇒ 3× flaky probes N/A by construction.** Disclosed: the semantics and A7+mutation lanes ran concurrently on this host; with zero diff nodes there is nothing whose determinism could be load-smeared.

---

## 8. Semantics claims — each with a live positive control

### Claim 1 — `file_source=None` ⇒ byte-identical · **PASS**
`test_file_source_none_is_byte_identical_shape` + `test_file_source_none_never_touches_resolver` PASS. `test_skill_search_service.py` **43 collected / 43 passed** (expected 43 ✓).

**Non-vacuity, measured (not asserted):**

| | WITHOUT `file_source` | WITH `file_source` |
|---|---|---|
| `file_corpus_stats()` | `{}` (never populated) | `file_total=1, included=1, refused=false, roots=[canon]` |
| injected key sets | `{'score','skill'}` exactly | DB `{'score','skill','source'}`; file adds `'address'` |
| injected | `alpha-db-skill` only | `alpha-db-skill` + `alpha-skill` @ `pf:tester:alpha-skill` (`source=project-file`) |

⚠️ **Methodology trap found and avoided:** with a 1–2 doc corpus, BM25 IDF goes negative and `if score > 0:` (`skill_search_service.py:1211`) filters every row — a naive harness shows **empty injected in BOTH branches** and would have produced a false "identical". Four decoy docs were required (N=5/df=1 vs N=6/df=2) before the comparison measured the seam. The two earlier empty runs were **not** reported as evidence.

### Claim 2 — A6 / REST invisibility · **PASS 3/3**
Control quoted: `assert any(e["source"] == "project-file" for e in result["injected"]), "positive control failed — fixture yields no file skills"`.

Mechanism: with `instance_id=None` the resolver derives `derived=None`, so any explicit `project_id` trips `explicit_project_id is not None and str(explicit_project_id) != (derived or None)` → `Resolution(project_id=None, roots=[], refused=True, refusal_reason="explicit_project_conflict")` (`project_skill_source/service.py:262-274`). The HTTP pin additionally asserts `file_corpus_stats()["refused"] is True` — proving the resolver was **consulted**, not merely yielding nothing. That is the non-vacuity hinge.

### Claim 5 — leak invariant, 4 shapes · **PASS 4/4**
Each node individually green; control quoted: `assert "alpha-secret-skill" in _entry_names(result)`.

| shape | names | file_total | file_included | refused | roots |
|---|---|---|---|---|---|
| (a) control A-inst→A | `['alpha-secret-skill']` | 1 | 1 | False | `[proj-a-root]` |
| (b) B-inst→B | `[]` | 0 | 0 | False | `[]` |
| (c) **A-inst → caller proj B** | `[]` | 0 | **0** | **True** | `[]` |
| (d) REST explicit A, no instance | `[]` | 0 | 0 | **True** | `[]` |

The hard case (c) is a **positive** observation (`refused=True` + `file_included==0`), not an absence.
⚠️ **Disclosed weakness in shape (b):** `refused=False, roots=[]` — absence comes from project-B having no root binding in the fixture, **not** from active exclusion of A's canon. It catches the "reuse A's root" leak class; it would **not** distinguish "B has its own canon but a policy excludes it".

### Claim 6 — A8 guard at the tool result surface · **PASS**
Both parametrizations + the service sibling green. Degradation genuinely occurred:

| mode | unguarded direct `_merge_project_file_corpus` | `search()` | `last_error` |
|---|---|---|---|
| `resolve` | **RAISES** `RuntimeError: root unreadable` | `['alpha-db-skill']` | truthy |
| `corpus` | does **not** raise | `['alpha-db-skill']` | truthy (`corpus raises`) |

Healthy control: `last_error=None, file_total=1` ⇒ a truthy `last_error` is discriminating. Tool result in both modes: `"ERROR: skill_search failed" in out` → **False**; payload `injected=['db-skill-1']`.
Escape hatch verified unmodified: `daemon/tools/skill_tools.py:167` still returns `f"ERROR: skill_search failed: {e}"`; last commit touching it is `c4cf04220` (2026-10-22), none in this lane.

---

## 9. Mutation re-proofs (2 of 4, independently re-run)

⚠️ **Label-collision hazard.** "M1–M4" already denote **four different Phase-1 mutations** (`2026-10-31-project-skill-loader-tier1-blast-radius-gate.md` §8). The Phase-2 labels used here are **P2-M1 / P2-M2** to prevent conflation.

| # | Mutation | Expected pin | Observed |
|---|---|---|---|
| **P2-M1** | `skill_search_service.py:841` — truncate to budget **before** the shadow pass: `budget_dropped = max(0, len(ordered) - FILE_CORPUS_BUDGET); ordered = ordered[:FILE_CORPUS_BUDGET]` | `test_over_budget_twin_still_shadowed` | **RED (SQLite)** `assert twin_id not in _entry_ids(result)` — leaked twin returned as `source='db'` (exactly the A7 defect) · **RED (PG)** `assert result["injected"][0]["source"] == "project-file"` → `'db'`. Revert → GREEN (SQLite 1 passed / PG 1 passed). |
| **P2-M2** | `skill_search_service.py:1153` — `except Exception as exc:` → `except ZeroDivisionError as exc:` (outer A8 guard neutered) | both parametrizations of `test_broken_file_source_never_surfaces_the_error_string` + service sibling | **RED `[resolve]`** `assert 'ERROR: skill_search failed' not in '…ERROR: skill_search failed: root unreadable (M3 fixture)'` · **sibling RED** `RuntimeError: file source exploded` escapes `search()`. ⚠️ **`[corpus]` stayed GREEN** — see finding F3. Revert → GREEN (3 passed). |

Both mutations produced the *specific* predicted defect, not merely *a* failure. Reverts proven clean (`git status --porcelain` empty) with GREEN-after evidence.

---

## 10. Findings

### 🟠 Important
- **F1 — Mutation labels are undocumented and colliding.** PACKS.md L24 claims "mutation legs M1-M4 RED→GREEN" and L25 "mutation M2 RED at the surface" for Phase-2, but **no Phase-2 per-mutation→test table exists** in the delta, PACKS.md, or `.agents/shared/planning/project-skill-loader/` (grep for `budget-before-shadow|shadowed-rows-kept|tagging-off` → zero hits). Meanwhile "M1–M4" already means four *different* Phase-1 mutations. A future reader will conflate them. Fix: add a per-phase mutation table, or adopt `P1-Mn` / `P2-Mn` naming in PACKS.md.
- **F2 — One pin name covers two different guard mechanisms.** `test_broken_file_source_never_surfaces_the_error_string[resolve]` exercises the **outer A8** `try/except`; `[corpus]` never reaches it — `corpus_for` is caught one layer down by the per-root handler (`skill_search_service.py:789-797`, "A8 layer 1"), which sets `last_error` and continues. Neutering the outer guard reddens `[resolve]` **only**. The suite is still mutation-meaningful, but `[corpus]` proves *per-root resilience*, not the outer seam guard. Fix: rename/split, and consider adding a case where the per-root handler itself is bypassed.
- **F3 — `TestCrossProjectLeak` shape (b) is a weaker pin than its name implies.** `refused=False, roots=[]` — absence is incidental (project B has no root binding in the fixture), not policy-driven. It catches root-reuse leaks; it would not catch "B has its own canon but a policy excludes it".

### 🟡 Minor
- **Dispatcher process deviation (own, disclosed):** both Phase-1 and Phase-2 lanes were dispatched with the recipe fully inlined in the task message rather than via `send_message(..., load_skill="test-pack-execution")`, so no 1:1 skill attribution / `skill_feedback` data was collected. Evidence quality was unaffected (the recipe text drove execution verbatim and every result above is independently re-derivable), but the dispatcher contract's attribution purpose went unserved. Correct on the next gate.
- Setup-auth wrinkle: no `~/.pgpass` on this host — the dispatcher's bare `psql` setup commands fail; the conftest-documented lane credential (`ensemble`/`ensemble_dev`) is required for role/DB minting (setup only). Worth one line in the recipe's Fences.
- Two of the four Phase-2 test paths in the commissioning brief were wrong (`tests/tools/…fusion_guard.py`, `tests/unit/routers/…invisibility.py`). PACKS.md records the correct paths.
- Phase-1 registration consolidated to **2 rows** (`project_skill_source_unit`, `project_skill_source_pg`) rather than 8 discrete rows; scope text names all files, so coverage is intact but the row count differs from a literal "8 rows" reading.
- `PG_TEST_ALLOWED_DBS` is **unset** in the ambient environment while the ambient env carries `POSTGRES_DB=ensemble_prod` — the fence's intended mechanism is absent by default and must be set explicitly per run.
- 🔴 carried from Phase-1, **still unfixed in the recipe**: v1.0.1's Fences section does not state *"never propagate a sibling worker's env assertion; re-check per invocation"*. The ambient env remains prod-poisoned (`ensemble_prod` @ `10.44.0.2`) and was re-confirmed by two independent lanes this run. This is the highest-value v1.0.2 fold.

### ⏱ Process
- Provider rate-limit storm (the known D7 proxy-side defect) killed/errored **3 of 4** Phase-2 lanes at least once (recon, legs, semantics, a7mut each interrupted; legs + semantics each consumed their single revive). All recovered with zero work loss via checkpointing, but it is a direct, measurable threat to a 30-min hard budget.

---

## 11. Wall-time accounting vs the 30-minute hard cap

**Two numbers, because they tell different stories:**

| Measure | Value | Meaning |
|---|---|---|
| **Gate compute** (sum of active lane execution) | **≈17 min** | recon ~2 + a7mut ~6 + sem ~6.6 + legs (replacement) ~1.5 + PG turn ~25 s + overhead | 
| **Calendar wall** (dispatch → verdict-ready) | **≈50 min** | inflated by the provider rate-limit outage, NOT by test cost |

Lane-level actuals: unit collect 6+7 s · unit legs 18.04 s + 40.74 s pytest · PG leg 15.42 s · a7mut 6 min (incl. mutations) · sem 6 m 39 s · replacement-legs turn ≈1.5 min + PG turn 25 s.

**Budget adjudication:** the gate's *own* cost is an order of magnitude inside the 30-min cap — the §(c) "a scoped leg is MINUTES" prior held on every lane. The calendar breach is an **environmental outage** (the known D7 proxy-side rate-limit defect): 3 of 4 lanes were killed on first pass; 2 consumed their single revive; the legs lane died twice and was replaced once. Treatment followed the fail-loud spirit — lanes were stopped, replaced with a *narrower* replacement, and never silently ground. **This outage is a direct, recurring threat to the Tier-1 budget model** and is user-owned (D7, proxy-side deprecated-row drop, already on the morning list).

---

## 12. Recipe v1.0.1 usability verdict — did GAP-1 / GAP-2 / FRICTION-1 close?

**Yes, all three closed — verified by running under them, not by reading them.**

| Fold | Verdict | Evidence |
|---|---|---|
| **GAP-1** two-class declaration | ✅ **CLOSED and usable as written** | The recipe's own D1/D2 language drove this gate. D1 (152 unit + 15 PG, HEAD-only MUST-PASS) and D2 (273, dual-side) were declared with zero improvisation, and the "asymmetry is not a broken identical-commands invariant" wording removed the exact ambiguity that cost judgment time in Phase-1. |
| **GAP-2** `^(FAILED\|ERROR)` digest | ✅ **CLOSED** | Both bodies carry the corrected grep plus "explain every ERROR id". Applied verbatim. |
| **FRICTION-1** budget table | ✅ **CLOSED** | The "a scoped leg is MINUTES" wording + measured 6-min/28-min parallel-shape is what made a 3-lane parallel fan-out an obvious choice rather than a gamble. |
| **New (v1.0.2 candidate)** | ⚠️ **OPEN** | Fences still lacks the sibling-env-assertion rule (Phase-1 🔴). Also worth folding: the **mutation-label namespace** problem (F1) belongs in whichever skill owns mutation-verification, since it now spans two phases. |

---

## 13. Fences honoured

- `env -u POSTGRES_*` never used as a glob; every scrub per-variable, re-checked per invocation.
- `ensemble_prod` / `10.44.0.2` / `:9797` / `ensemble-main` / `current` / `releases/` untouched; no daemon boots.
- Main checkout porcelain `2c85a625efe03ffdad72e6457ea935cc52dd34fd2f32ba603a3ac58c99544f02` before **and** after (all lanes).
- Gate worktree porcelain `dc9d2fdd4c6a5f35131cc3ec5c01e245cd440fdf15432b43c146ec269eb2b695` unchanged; gate HEAD still `0c4464ebe`.
- One scratch PG role/DB per lane, each dropped with `\du` / `\l` absence proof.
- No source/test/migration/PACKS.md edits anywhere.

---

## 14. Verdict

# ✅ PASS-WITH-NOTES

**Zero net-new failures, zero net-new errors, zero diff nodes — on the dual-side unit corpus AND with the entire lane-owned surface green on real PG.**

Five evidence lines that carry the verdict:

1. **Premise verified, not trusted** — six files blob-identical across the delta (`skill_tools.py`, `manager.py`, `config.py`, `api.py`, `skill_injection_service.py`, `readiness.py`); **0** core-seam hits; **zero migrations**; `skill_search_service.py` = **exactly +468/−7** as declared.
2. **Dual-side D2: 273/273 passed at BASE, 273/273 at HEAD, per-file counts identical on all 11 files**, including the key witness `test_skill_search_service.py` (43) over the one modified shipped file. Digest (v1.0.1 `^(FAILED|ERROR)` form) **empty on both legs**, `cmp` rc=0, both `comm` directions empty.
3. **Lane-owned D1: 152/152 unit + 15/15 real-PG green at HEAD** — the pre-change corpus shape is unchanged (`file_source=None` byte-identical, measured non-vacuously) and the fusion surface is green on real PostgreSQL 16.15 through the production code path.
4. **The load-bearing claims were re-proven with observable numbers, not pass/fail** — A7 pre-budget shadow on real PG: **file_total=55, shadowing=1, file_included=51, budget_dropped=4**, twin retired into `shadows` (ordered LAST, so a budget-first implementation would have truncated it); leak invariant 4/4 with the hard shape a *positive* observation (`refused=True, file_included==0`); A6 invisibility with the resolver provably consulted; A8 guard at the tool surface with the escape hatch verified unmodified (`skill_tools.py:167`, last touched 2026-10-22).
5. **Mutations discriminate** — P2-M1 (budget-before-shadow) reddened both the SQLite pin (`twin_id` leaks back as `source='db'`) and the PG pin (`'db' != 'project-file'`); P2-M2 (outer guard neutered) reddened `[resolve]` + the service sibling. Both reverted clean with GREEN-after proof.

**Why not a clean PASS — the notes:**
- 🟠 **F1** mutation labels "M1–M4" collide across phases and the Phase-2 table is undocumented;
- 🟠 **F2** one pin name covers two guard mechanisms — `[corpus]` proves per-root resilience, **not** the outer A8 seam;
- 🟠 **F3** leak shape (b) pins absence-by-no-root, not policy exclusion;
- ⏱ a provider rate-limit outage (known D7) killed 3 of 4 lanes and inflated the calendar wall to ~50 min against ~17 min of gate compute — budget-relevant and user-owned;
- 🟡 dispatcher skill-attribution deviation (own, disclosed in §10), plus the carried recipe gap: Fences still lacks the sibling-env-assertion rule.

**Recipe v1.0.1 usability verdict: GAP-1, GAP-2 and FRICTION-1 are all CLOSED and usable as written** (§12) — this gate ran under the recipe's own two-class language with zero improvisation, which is the proof the fold needed.

**Scope boundary:** regression safety of the declared blast radius only. NOT an acceptance sign-off for the loader feature, and NOT authorisation to merge or promote — those remain the leader's call.
