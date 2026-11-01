# Phase-3 Tier-1 Blast-Radius Gate — project skill source loader (activation run)

- **Date:** 2026-10-31
- **Commission:** Phase-3 Tier-1 blast-radius gate, third delta (pf: tool surface + `current_instance_id` ACTIVATION + STEP-0 + A14)
- **Worktree:** `/home/nea/ensemble-src-wt-project-skill-loader` (`feature/project-skill-loader`)
- **HEAD:** `c421b8ee61e3d8131c8034b0db74ec69fd05f34c` · **TRUE BASE:** `b56444244` · **delta base:** `f6b578237` (Phase-2 evidence)
- **Delta:** 4 commits, **14 files** (4 daemon + 6 tests + 4 tester docs). Daemon subset **+976/−22 exactly as declared** (`skill_injection_service.py` +215/−0 · `skill_tools.py` +719/−22 · `skill_evolution_tools.py` +25/−0 · `routers/skills.py` +17/−0). **ZERO migrations** (empty, confirmed).
- **Recipe:** `blast-radius-leg` **v1.0.2** + `failed-digest-equality-gate` — both verified to carry the v1.0.2 folds BEFORE running (sibling-env fence §Fences:113-119 with assertable form; `P{n}-Mn` namespace :121-125)
- **Lanes:** `psl3-recon` 5a4677c4 · `psl3-legs` 0471f225 · `psl3-sem` 56416be6 · `psl3-mut` 377194fd — **all dispatched with `load_skill="test-pack-execution"`** (Phase-2 attribution deviation corrected; `skill_feedback` filed by 2 of 3 execution lanes)

---

## 1. Tier decision (BEFORE the run)

Mechanical checks executed, not eyeballed:

| Check | Result |
|---|---|
| Six-file premise (`manager.py`, `config.py`, `services/readiness.py`, `services/skill_search_service.py`, `services/skill_clone_service.py`, `api.py`) | **zero diff / zero commits / blob-identical at both revs** (all six shas matched) |
| Core-seam list (6 paths) | **0 hits** — `skill_tools.py` and `skill_injection_service.py` are modified but NOT on the list ⇒ **no escalation** |
| `apply_skills`/`inject_skills` untouched claim | **`apply_skills` does not exist at either rev** (brief symbol error). The real claim holds via **`inject_skills` byte-identical** (114 lines, `diff` rc=0, positional shift 305→340 only); `inject_explicit_skill` IS modified **by design** (STEP-0, commit `4ccdad264`) |

---

## 2. Declared set (two-class, recipe v1.0.1 rule as carried in v1.0.2)

- **D1 (lane-owned, HEAD-only):** P1 six files (123) + fusion 24 + guard 4 + step0 20 + pf_surface 29 + pf_refusals 10 = **210 unit** (settle below) + **15 PG** (12 + 3).
- **D2 (dual-side):** the established 11-file corpus (**273 exact**) + **35 pre-existing neighbours** of the four touched modules = **46 files, 943 collected** (34 deselected by default addopts; 3 integration files collect 0).
- **Named pre-exclusion + compensating pin:** `test_report_deferred_migration_pg.py` — quarantined (29 ids); pin = one-shot HEAD re-digest vs the recorded `2fad8e8a…` (§6).

**Count adjudication (three numbers existed; the slicing proof settles it):** brief said **166**; my dispatch annotation said **≈205**; **mechanical collect-only says 210** — three files exceed their PACKS.md annotations by +2 each (guard 3→4 via parametrize, step0 18→20, refusals 8→10 via 3-way parametrize). **210 is authoritative; the annotations are stale.**

---

## 3. Slicing proof (executed == declared)

- D2: **943 collected at BASE and HEAD, per-file counts byte-identical** (diff empty). 11-file corpus subtotal 273 exact.
- D1 unit @ HEAD: **210**. D1 PG @ HEAD (`-m postgres`): **15**.
- All 13 D1 files **absent at BASE** — expected by construction ✓.
- Projection probe: `test_skills.py` 76 tests / 13 s wall ⇒ HEAD leg (1153) ≈ 200 s serial ⇒ far inside the cap. Proceeded.
- ⚠️ Executor-flagged label error (mine): my dispatch said "44 files / 33 neighbours" while the enumeration listed **46 / 35** — the executor ran the exact list and flagged the mismatch instead of smoothing it. The list was authoritative.

---

## 4. Dual-side legs (BASE then HEAD; one runner script, both legs)

`/tmp/p3_gate_runner.sh` — written ONCE, per-var `unset` scrub + the literal v1.0.2 assert `[ -z "${POSTGRES_DB:-}" ] || [ "$POSTGRES_DB" != "ensemble_prod" ] || exit 1`. **Fence canary-tested both directions: a poisoned no-scrub invocation exits 1 (FENCE-FAIL); the benign path passes; the real ambient poison scrubs clean.** Every invocation printed `ENV-FENCE-OK … POSTGRES_DB observed=<unset>`.

| Leg | Files | Tests | Result | rc | pytest wall |
|---|---|---|---|---|---|
| BASE | D2 (46) | 943 | **940 P, 1 S, 2 F** | 1 | 32.36 s |
| HEAD | D2 + D1 (57) | 1153 | **1150 P, 1 S, 2 F** | 1 | 61.24 s |

Asymmetry per the recipe: BASE = D2 only; HEAD = D2 + D1 unit. **D1: 210/210 green.**

The 2 failures are **the same ids on both legs**: `test_context_freshness.py::TestSkillFreshness::test_skill_added_mid_session_visible_next_call` and `test_explorer_shared_context_paths.py::TestFlagOnChildTreeRootPartitionViaEnqueue::test_non_opted_in_agent_gets_no_shared_context_block`.

---

## 5. FAILED-list diff (v1.0.2 form `grep -E '^(FAILED|ERROR)'`)

- BASE sha256 = **`1e34b56f744187c17ab787d29e07de16bd0cf4996e84d4f127093cb30366beef`** == HEAD sha256
- `cmp` **identical** · `comm -23` **empty** · `comm -13` (**NET-NEW**) **empty**
- Both common reds **isolated-probed serially on BOTH trees: 2 fail at BASE, 2 fail at HEAD** ⇒ deterministic PRE-EXISTING-BOTH-SIDES (not xdist/order artifacts, not flaky). 3× flake protocol not triggered (no diff nodes).
- **The brief's "72-red quarantined routers family" did NOT materialize**: `tests/unit/routers/test_skills.py` = **0 reds on BOTH sides** (76/76 green at HEAD), and QUARANTINE.md contains no such row. The hypothesis is falsified in this tree — the actual pre-existing surface is exactly 2 integration reds.

---

## 6. Real-PG evidence

- **D1 PG: 15/15 passed in 15.65 s**, serial, scratch role `p3_gate_head` + DB `ensemble_test_p3head` (local PG 16.15; role owns only the scratch DB; absence proofs pre+post).
- **29-id quarantined block comparison (HEAD-only):** 5 failed + 24 errors = 29 ids; **bare-id sorted sha256 = `2fad8e8a775b66d7a35b4162e68f6627946d359e7dfe948fb8323b0658524c3d` → SAME** as recorded. (Form note: matches in the **bare-id** form only — the recorded digest is prefix-stripped; a naive `^ERROR` grep also catches 5 non-summary LOG lines. Label the form; the recipe's digest-form discipline earned its keep a second time.)
- BASE PG = N/A by construction (no lane PG file at `b56444244`).
- Teardown: DB + role dropped, 0-row absence proofs, scratch password file removed, prod list verified intact.

---

## 7. Semantics claims (each with positive control / vacuity evidence)

| Claim | Result | Key evidence |
|---|---|---|
| 1 ACTIVATION e2e | **GREEN (6 nodes)** | call-spy asserts `kwargs.get("instance_id") == INSTANCE_ID` (errors on non-call — cannot pass silently); legacy kwargs preserved; fused `pf:` entry (`address == "pf:tester:alpha-skill"`, `source == "project-file"`, `degraded == "file_only"`); fresh-disk body at view (`assert "Beta body." in r`) and injection (`ids[0].startswith("pf-")`). Precision note: DB+file fusion composition is asserted at SERVICE level (`TestShadowing` 2/2) + tool surface, not one node — stated as found. |
| 2 STEP-0 ordering + None-skip | **GREEN** | spy **assert-verified**: `assert clone.calls == []` with a DB twin available (file-hit leg) and `clone.calls == [("gamma-skill","tester",TEST_PROJECT_ID)]` on the miss leg; None-skip proven **behaviorally** — `_ExplodingFileSource` registered then `svc._file_source = None`; DB path byte-identical (`ids == ["db-n8-1"]`) |
| 3 A14 + FR-P7-4 | **GREEN (10 REST/file + 4 tool)** | 3 evolution-wrapper refusals (`"git PR"`+`"P7"`, `assert_not_awaited`); non-pf positive control (`assert_awaited_once`); REST pf→400 dispatcher-never-invoked; uuid→202 dispatches; id-endpoints 404×3; metrics zero-stats 200; `skill_fix` pair. **FR-P7-4 all three legs**: vacuity (`"source" not in skill_create.args`) + invariant (refuses pf-shaped name, `create_skill.assert_not_called`) + refusal-acceptance controls |
| 4 A13 | **GREEN** | `assert calls, "resolver was never consulted"`; `calls[0]["explicit"] is None`; `instance_id == INSTANCE_ID`. Static: the allowlist trio (`graph.py`, `instance_messaging.py`, `task_processor.py`) diff = **EMPTY**. W-4 fence pin absent (Phase-5 scheduled) — not scored as failure |
| 5 riders R2 + R3 | **GREEN 4/4 + 2/2** | R2 parametrization **IDs now name the two mechanisms** (`outer-a8-seam-resolver-raises` / `per-root-layer1-corpus-raises`) — the F2 fold is in the code. R3 hard shape: `refused is True`, `file_included == 0`, **with B's root present** (proven by the control `file_included >= 1`), A's canon never leaks — the Phase-2 weakness is closed |
| 6 feedback companion | **GREEN 8/8** | companion table ONLY (`len(rows) == 1`, `skill_id.startswith("pf-")`); `metrics.record_feedback.assert_not_called()` + lineage table empty; `pf-` pseudo-id accepted; degraded leg. Both forms render |

**Zero wrong-reason passes found.**

---

## 8. Mutation re-proofs (P{n}-Mn namespace, 3/3)

| Label | Mutation | Expected pin | Observed RED |
|---|---|---|---|
| **P3-M3** | `skill_tools.py:639` — dropped `instance_id=current_instance_id,` from the main-path `service.search(...)` | `TestActivation::test_search_threads_instance_id` | `assert kwargs.get("instance_id") == INSTANCE_ID` → `assert None == 'inst-tools-1'` (kwarg absent — right reason) |
| **P3-M4** | `skill_tools.py:1165` — `return _pf_refusal("skill_fix")` → `pass` | `TestSkillFixRefusal::test_pf_refused_dispatcher_never_invoked` | `assert "git PR" in r` fails against the **dispatcher-path recording message** — the exact output the refusal exists to prevent (right reason) |
| **P3-M1** (optional, executed) | `skill_injection_service.py:526` — `if self._clone_service is None and self._file_source is not None:` | `TestStep0BareName::test_zero_db_writes_clone_never_invoked` | `assert ids[0].startswith("pf-")` → `'db-twin-1'` served (clone invoked + DB twin — matches the PACKS.md predicted mode) |

Each: exactly one RED per run, one first-failing assert, revert → porcelain clean → **GREEN after** (1.99 s / 2.10 s / 1.59 s). Post-teardown 3-node sweep on the reverted tree: 3 passed. Location correction banked: `skill_fix`'s pf: refusal lives in `skill_tools.py:1165`, not `skill_evolution_tools.py`.

---

## 9. Findings

### 🟠 Important
- **F1 — the "72-red quarantined routers family" does not exist.** QUARANTINE.md has no such row; `test_skills.py` is 0-red on BOTH legs (76/76 at HEAD). The brief's claim-7 premise is falsified in this tree. The actual pre-existing surface is exactly **2 deterministic integration reds** (both sides, isolated-probed). Recommendation: whoever owns that claim re-derives it; nothing here needs quarantining that isn't already recorded.
- **F2 — PACKS.md node-count annotations drift from collect-only truth** (+2 each on three Phase-3 packs: 3→4, 18→20, 8→10 — parametrization widening). The slicing proof is authoritative; the annotations should be refreshed or annotated "function count, not node count".

### 🟡 Minor
- **F3 (mine)** — dispatch label said "44 files / 33 neighbours" while the enumeration listed 46/35; the executor ran the list and flagged the mismatch. Lesson: the list is the contract; labels are prose.
- **F4 (brief)** — symbol/class-name drift: `apply_skills` nonexistent (real: `inject_skills`, byte-identical); `TestA13Resolver` (real: `TestSkillViewPf`); `TestFileCrossProjectLeak` (real: `TestCrossProjectLeak`); "8-pin pack" = 8 functions / 10 nodes; `skill_fix` refusal location. None changed an outcome; all consumed verifier time.
- **F5** — the 29-id digest matches in **bare-id form only**; cross-form comparison would fake a mismatch. (Recipe already mandates labeling; confirmed necessary again.)
- **F6** — W-4 allowlist fence pin is Phase-5 scheduled and absent from this delta (expected; recorded so nobody hunts for it).
- ⏱ No D7 rate-limit hits this phase (first clean phase); carried as a standing threat from Phase-2.

---

## 10. Wall-time accounting

| Lane | Wall |
|---|---|
| recon | ~2 min |
| legs (preflight 0.7 + setup 0.6 + slicing 2.5 + probe 13 s + BASE 37 s + HEAD 66 s + PG 2.3 + digest 0.5 + cleanup 0.7) | **≈14 min** |
| semantics (parallel) | ≈7.2 min |
| mutations (parallel) | ≈8.2 min |
| **Phase-3 window** | **≈18 min** (compute ≈17 min active across parallel lanes; calendar ≈28 min end-to-end incl. dispatch overhead) |

**Inside the 30-min cap on both measures — the first phase with no provider outage.**

---

## 11. Recipe v1.0.2 usability verdict

| Fold | Verdict |
|---|---|
| **Sibling-env-assertion fence** | ✅ **CLOSED and materially better** — applied LITERALLY: one runner, the assertable form from the recipe text, and **canary-tested in both directions** (poisoned→exit 1, benign→rc 0). This converts the fence from a convention into a tested mechanism. |
| **`P{n}-Mn` mutation namespace** | ✅ **CLOSED** — PACKS.md carries the full table (P1-M1..P4, P2-M1..M4, P3-M1..M5) mapping defect-site → discriminating pin; this gate used the registered labels with zero collision. The optional P3-M1 leg ran against its registered prediction and matched. |
| **Carried forward (v1.0.3 candidates)** | (a) PACKS.md node-count annotations should be marked "function count" or refreshed — the slicing proof keeps trumping them; (b) consider recording digests with an explicit FORM tag in the table itself (`bare-id` vs `raw`), since the 29-id pin only reproduces in bare-id form. |

---

## 12. Fences honoured

Per-variable scrubs + literal assert in one runner, both legs; never `env -u POSTGRES_*`; never `ensemble_prod` / `10.44.0.2` / `:9797` / `ensemble-main` / `current` / `releases/`; no daemon boots; main checkout porcelain `2c85a625…44f02` before == after (all lanes); gate worktree porcelain `dc9d2fdd…` unchanged (mut lane); all scratch worktrees removed (`grep -c p3-` = 0), all scratch DBs/roles dropped with 0-row proofs; no edits to any repo file.

---

## 13. Verdict

# ✅ PASS-WITH-NOTES

**Zero net-new failures. Zero net-new errors. Digest identical across legs. The entire lane-owned surface green on unit (210/210) and real PG (15/15). The quarantined 29-id block reproduces byte-exact.**

Five evidence lines that carry the verdict:

1. **Premise verified** — six files blob-identical; 0 core-seam hits; zero migrations; `inject_skills` byte-identical (the brief's `apply_skills` symbol does not exist; the real additivity claim holds and was proven by body-diff).
2. **D2 943/943 collected per-file-identical across trees; 940P/1S/2F at BASE, 1150P/1S/2F at HEAD; digest `1e34b56f…` identical; both `comm` directions empty; the 2 common reds isolated-probed deterministic on both sides.**
3. **D1 210/210 unit + 15/15 real PG green at HEAD** — the ACTIVATION, STEP-0, A14, FR-P7-4, A13, R2, R3, and feedback surfaces all green with assert-verified controls (§7).
4. **Mutations 3/3 discriminating** — P3-M3 (kwarg absent), P3-M4 (dispatcher path executes — the exact prevented output), P3-M1 (DB twin served). Each revert-proven GREEN-after.
5. **Fences canary-tested** — the v1.0.2 assert was proven to fire on poison, not merely assumed present; porcelain byte-identical before/after everywhere.

**Why not a clean PASS:** F1 (the brief's 72-red family premise is falsified — claim-7 needs re-derivation by its owner), F2 (PACKS node-count drift), F3/F4 (label and symbol drifts on both sides of the dispatch), F5 (digest form sensitivity). None affect the regression verdict; all affect future verification cost.

**Scope boundary:** regression safety of the declared blast radius only. NOT an acceptance sign-off for the loader feature, NOT authorisation to merge or promote.
