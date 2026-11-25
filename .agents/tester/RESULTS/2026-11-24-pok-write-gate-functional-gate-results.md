# POK WRITE GATE — Functional Gate Verdict

**Gate:** agents-ensemble · lane `feature/pok-write-gate`
**Commissioned HEAD:** `46ba66eacd14b79f223e7bce25099dfe5ba7c358` (48 ahead of `origin/latest` `a551d19a2`, unpushed)
**Measured HEAD:** `34b980fa7ec4252398ee979c3fb24531c33f3014` (51 ahead; the 3 intervening commits are 2 docs-only + 1 `PACKS.md` count correction — `git diff --stat 46ba66eac..HEAD -- daemon tests agents scripts` is EMPTY, so the **code under test is byte-identical** to the commissioned SHA; every worker independently re-verified this)
**Lane BASE (the "before"):** `a1130e257a3c3bb3929455aec28cacafcbfaa1a7`
**Worktree:** `/home/nea/ensemble-src-wt-pok-write-gate` (shared, multi-lane — never mutated by this gate)
**Date:** 2026-11-24 · **Tester gate lead:** the dispatched tester instance · **12 worker legs, 12 worker instances**

---

## FINAL VERDICT

# ❌ NO-GO as it stands — convertible to GO by TWO mechanical fixes

**The Emperor's contract is satisfied, every commissioned gate leg passes, all four outcomes are proven, all 20 mutation legs bite, the PG dialect is proven on real PostgreSQL, and the hygiene fences hold. One thing blocks staging: the lane ships 2 deterministic, lane-caused net-new test failures — both assertion-count pins the lane's own growth crossed and left unfixed.**

### What must change for a GO (exactly two items)

| # | The red | Where | The fix |
|---|---|---|---|
| 1 | `tests/unit/services/test_context_module_split.py::test_context_messages_is_under_three_thousand_lines` — 0/8 red at BASE, **8/8 red at HEAD** | `daemon/services/context_messages.py` grew **2998 → 3015 lines** (lane commit `bf23c8c10`; upstream `a551d19a2` = 2998, green) | **Preferred: split the module** — extract the POK context block into its own file, returning the module under 3000. The pin exists to force module splits, so a silent re-pin erodes the discipline. A re-pin to the new bound is acceptable **only with the pin owner's explicit sign-off**. |
| 2 | `tests/test_settings_resolution.py::TestManagedKeysConsistency::test_every_entry_is_consistent` — 0/8 red at BASE, **8/8 red at HEAD** | `MANAGED_KEYS` in `daemon/services/settings_service.py` grew **22 → 25** entries (the lane's three new ManagedKeySpecs) | **Re-pin `== 22` → `== 25`.** The three new ManagedKeySpecs ARE the feature; the consistency pin is a registry tracker and must follow it. This one is a plain pin update. |

**Re-verification after the fixes (the lane's own two nodes, one command each):**
```bash
cd /home/nea/ensemble-src-wt-pok-write-gate && \
  env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
      -u DATABASE_URL -u ENSEMBLE_DB_DSN -u PORT -u DAEMON_PORT \
      ENSEMBLE_SELF_ENV=dev ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python -m pytest tests/unit/services/test_context_module_split.py \
      tests/test_settings_resolution.py::TestManagedKeysConsistency -q -p no:randomly --tb=short
```
With those two green, this gate's verdict becomes **GO-WITH-DISCLOSURES** (the disclosures below are all non-blocking under the shipped shadow-default-OFF, but three of them are **pre-enforcement obligations**).

---

## 1. Scope decision

The commission named 10 legs plus the pack matrix. I scoped it as a **Tier-2 dual-side gate**: the lane diff touches `daemon/manager.py` (+63 lines), which is a **mechanical core-seam trigger** per `.agents/tester/rules/ensure.md` §Gate Tiers, so the whole-tree base-proven-diff shape applies — which is also exactly what the commission's L8 asked for. L8b ran **isolated last** so its base-vs-HEAD measurement could not be load-coupled by the other 11 legs; the two sweep legs were additionally **disjoint in time** (base 15:57→16:46, head 16:48→17:37) per the `failed-digest-equality-gate` recipe.

**Fences held on every leg (verified per worker, per run):** no live-store writes (scratch in-memory SQLite + the deterministic stub only; a read-only prod fingerprint was taken once, server-enforced `default_transaction_read_only=on`, byte-identical before/after); no promote; no push; no live config change; no daemon restart; nothing signalled on :8088; the canonical checkout `/home/nea/ensemble-src` byte-identical before and after; `.agents/shared/planning/**` and the guideline read-only; the shared lane worktree porcelain-clean before and after every leg; every mutation executed only in a throwaway `git archive`/detached-worktree extract with a per-tree `uv sync` and an in-tree import assertion.

---

## 2. Per-leg results

| Leg | Verdict | One-line evidence |
|---|---|---|
| **L1** The Emperor's Contract | **PASS** | 44 assertions / 2 legs. Symptom **ALIVE at BASE** (`reason` absent from the schema; `pok_upsert` without `reason` **writes a row**; `reason=""` silently ignored) and **DEAD at HEAD** (required schema param; empty/whitespace → typed `pok_reason_required`, 0 rows; REJECT routable with the Emperor's verbatim trace phrase). Identical harness, two trees, per-tree venv, in-tree import asserted. |
| **L2** Four Outcomes | **PASS** | 14/14 nodes + a deliberate non-vacuity RED. ACCEPT = exactly 1 active row + the 2-row plain chain; MERGE no-delta = **0 new rows**, verify_count 0→1; MERGE delta = asks first, missing confirm writes nothing, confirm = 1 row + prior superseded; ASK = one-turn answer, owner-scoped (agent AND instance), **six** typed refusals, no default-accept, TTL 1200 s expiry = no write + `ASK_EXPIRED` ×1 + second pass +0; REJECT = `route="rag"` + `rag_insert_text` named, parking proven both ways. |
| **L3** Audit Semantics | **PASS** | 12/12. EXECUTION rows same-transaction (failed write → 0 rows; committed → exactly 1); VERDICT best-effort never-raise; the partial unique index predicate **byte-identical across all 4 sites** (sha256 `8ddae94b…ffb0`); credential scrub at all 3 write sites + gate-side (raw secret absent on read-back); appender idempotency; the 8-phase vocabulary triple-homed. |
| **L4** Cache + Shadow | **PASS** | 6/6 + 108 of the lane's own nodes. Mode-match replay in **both** directions (re-judge after the switch flips; same-mode replays); cached ASK replays as ASK with the gate_id re-minted; answered ≠ cached accept; no-op short-circuits **before** the gate (call counter 0); both switches `(False, False)`; mechanical enforcement ON while semantic OFF. |
| **L5** Librarian Proposal-Only | **PASS** | 5/5. Zero POK-store writes proven **twice** (reachability + a write-counting harness **with a non-vacuity control**: the counter reports 2 when the harness itself writes); ruled bases only (age-only and access_count-only → **0** proposals); the ceiling **moves** with the working set (5 distinct values); cold start 499 rows → **0** proposals while the control proves 2 qualify; drift `delta=52` surfaced in the report and the stored payload; one bounded board item per project-pass. |
| **L6** Grant Removal + Caller Side | **PASS** | 6/6. kb-writer `pok` grant **gone**; the absence pin **bites** (re-add → 2 RED / 12 pass, sha-identical restore → 14 pass); census **54→53** re-derived end-to-end; `tool-categories.json` byte-untouched; capture-duty guidance on **exactly** ari/devops/leader/project-manager; **prompt baseline 217/217**; the lane changed 4 **versioned** artifacts and **no sealed** artifact. |
| **L7a** Mutation (core) | **PASS** | **6 valid REDs** — gate-call removal (23 RED), reason-validation removal (2 RED), grant re-add (2 RED), carry-over break (3 RED), stamp carry-over (1 RED), family-count break (5 RED). Every restore sha256-verified; every re-run green; no collection errors. |
| **L7b** Mutation (policy) | **PASS** | **14 valid REDs** (7 commissioned + 7 second-half sub-legs). **The C-1 and W-1 fixes at `7d37d4888` ARE pinned — each at both halves**, with the C-1 defect shape reproduced exactly (mutation: 1 active → **2**; HEAD: 1 → 1). Mode-match, TTL (both halves), the unique index, all three consolidation-basis guards, and all three dormancy legs RED correctly. |
| **L8a** Pack Matrix | **PASS** | family-key **275**, W1 **172**, W2 **255** — **12 runs** (3 + fence probe per pack), 0 failed / 0 skipped / 0 errors, counts identical every run; the fence probe set every `POSTGRES_*` to `ensemble_prod` + dead port `:1` and the counts did not move. Live-LLM file: 3 collected / 3 deselected. The stale registry rows were corrected in commit `34b980fa7ec4252398ee979c3fb24531c33f3014`. |
| **L8b** Red-Set Comparison | **FAIL (lane verdict)** | The leg completed in full. **NET-NEW REDS = 2** (both lane-owned assertion-count pins). 45 sweep-level head-only reds collapse to 2 under 3×/8× isolated adjudication: 2 net-new · 1 fixed-at-head · 19 pre-existing (red at both legs) · 4 flaky · 43 parallel-shard artefacts. |
| **L9** PostgreSQL Dialect | **PASS** | Real **PostgreSQL 16.15**. The manager statement is the **only** thing that creates the index on PG (`DROP INDEX → create_all → still absent → statement → present`); it **enforces** (23505, `uq_pok_ledger_gate_chain`); the partial predicate is honoured; the predicate digest matches byte-exactly; `FOR UPDATE` on PG and not SQLite with a live `55P03`; the supersede is **count-neutral** on PG; a two-session race leaves exactly **1 active**; all 5 CHECKs refuse with 23514. **No L9 gap — the one gap both code reviews left open is closed.** |
| **L10** Segregation + Hygiene | **PASS** | 5/5. LLM path deselected by default and the deselection is **load-bearing**; **1572 engine constructions, 100% `sqlite://`, zero socket connect attempts** (with a real proxy URL+key ambient, so not an unset-env artefact); **150 stub calls across 6 processes collapse to one digest** `89fe9c5d…`; a read-only prod fingerprint **byte-identical** before/after and `pok_write_gate_ledger` absent in prod; 0 new/gone listeners, 0 orphans. |

---

## 3. The authoritative net-new figure — and its method

# **NET-NEW REDS = 2**

Both are **assertion-count pins**, both **lane-owned** (upstream `a551d19a2` is green on both), both deterministic over 8 isolated runs per side:

1. `tests/unit/services/test_context_module_split.py::test_context_messages_is_under_three_thousand_lines` — base 0/8 red, head **8/8 red**. `daemon/services/context_messages.py`: 2998 → **3015** lines (lane commit `bf23c8c10`).
2. `tests/test_settings_resolution.py::TestManagedKeysConsistency::test_every_entry_is_consistent` — base 0/8 red, head **8/8 red**. `MANAGED_KEYS` in `daemon/services/settings_service.py`: 22 → **25** entries.

**Method (the whole shape, so the figure is auditable):** two throwaway detached worktrees (`/tmp/pok-gate-L8b/{base,head}` at `a1130e257` and the measured HEAD), per-tree `uv sync` with the mandatory import assertion (`daemon.__file__` resolves inside each tree), collection with the recipe's pre-exclusion registry (`--ignore=tests/packs --ignore=tests/e2e/test_context_injection_hybrid.py`, node list built with `grep '::'`), 13 shards per leg at 3300 nodes, **identical command shape on both legs** with an identical LLM fence (`OPENAI_BASE_URL=http://127.0.0.1:9/v1`, `OPENAI_REQUEST_TIMEOUT=5`, the retry trims, and a per-variable `env -u` scrub of every `POSTGRES_*`/`PG*`/`DATABASE_URL`/`ENSEMBLE_DB_DSN`/`PORT`/`DAEMON_PORT` + the whole `ENSEMBLE_AUTH_*` family — **no globs**), per-shard `timeout 1750`, **base leg first, then head, never concurrently**. Authoritative red set = **`grep '^FAILED'` only**, the prefix **and** the ` - <message>` suffix stripped, `LC_ALL=C sort -u`. Comparison both ways (`cmp` + `comm -23` + `comm -13`). Classification ladder: **every diff node re-run 3× in isolation, sequential, no `-n`**, per side, borderline nodes extended to 8 runs per side; attribution by `git show <rev>:<file>` and `git diff BASE MERGE^1` (lane-own) vs `git diff BASE MERGE^2` (upstream-drift).

**Slicing proof (executed == declared):** BASE 41137/42415 collected (1278 deselected) → 41137 ids; HEAD 41675/42956 (1281 deselected) → 41675 ids. HEAD closes **exactly** (40720P + 527F + 26E + 396S + 6xF = 41675); BASE 41138 vs 41137 (one node double-reported inside shard-000; 0 duplicate ids in the collected list; disclosed).

**Digest pairs (form, sha) — never a bare sha:**
- BARE-ID (FAILED-only, suffix-stripped): base `166f75ca9c1b864a9437db166d97219671b256b2548baacfd4bc208bf9537c1a` (506 ids) · head `21da20e2f762b53006d747981c5f288672e41500fc11a8c127d57955e2d51568` (527 ids)
- GREP-FULL (`^(FAILED|ERROR)` incl. LOG lines): base `83f762280cc95abff2ad74c041c4af8a7c21d8dee5edd4c7c217072e9d279472` (538) · head `bc88ac4a6d5ef878334eb8be5b85fd7710c9e19d939da263609664fae118f85d` (566)

**Sweep-level → adjudicated:** 45 head-only (net-new candidates) + 24 base-only (fixed candidates) → after isolation: **2 net-new**, **1 fixed-at-head** (`TestThePromptArtifactBaseline::test_the_baseline_matches_the_real_bytes` — the lane regenerated the prompt-artifact baseline), 19 pre-existing, 4 flaky, 43 parallel-shard artefacts (green 3/3 in isolation on **both** legs).

### The "46 pre-existing failures in `tests/unit/tools/`" claim: **REFUTED**

Measured at BASE `a1130e257` with a named method (an isolated subtree run of the 5802 collected `tests/unit/tools/` nodes → 24 failed / 0 errors; then a 3× isolated sequential re-run of the 25 candidates → 24 red in all three, the 25th green in all three): **24 deterministic** pre-existing reds in `tests/unit/tools/` (25 with the one load-flaky node). No reading of that scope yields 46 — 25 (parallel sweep) / 24 (deterministic) / 5 (distinct failing files) / 192 (all of `tests/unit/`) / 506 (whole tree). The Wave-2 review's Q2 ruling that dropped the bare figure was **correct**.

### The migration ERROR line — the phantom-red mechanism, measured

The repeating line is `ERROR daemon.migrations.runner:runner.py:723 Migration 20260714_000001 failed: (sqlite3.OperationalError) near "EXISTS": syntax error` — **36 occurrences at BASE and 36 at HEAD, identical**. It is a **pre-existing** broken SQLite migration prefix, already tracked in the project's critical notes. **The lane's two new migrations (`20261124_060000_create_pok_write_gate_ledger.sql`, `20261124_093000_add_pok_ledger_gate_chain_unique_index.sql`) produce ZERO ERROR lines.** The commission's phantom-red hypothesis (lane migrations invent repeating reds) is therefore **refuted by measurement** — the risk is real, but it comes from the pre-existing `20260714_000001` prefix, identically on both legs, and `grep '^FAILED'`-only classification is the correct guard. The `^ERROR` lines were captured **separately** and reported: 4 genuinely new ERROR entries at head are setup errors in `tests/e2e/test_p2_8_list_instances_consumer_pin.py` — an **upstream** file, absent from both FAILED sets — plus 4 new daemon LOG lines.

---

## 4. Mutation-leg table (L7a + L7b — 20 legs, all valid REDs)

All executed in throwaway extracts (`git archive` / detached worktree + per-tree `uv sync` + in-tree import assertion), **never** in the shared worktree, **never** via `git stash`. Copy → mutate → RED → restore from the pristine COPY with a sha256 check → GREEN.

| Leg | Mutation | RED | Valid |
|---|---|---|---|
| 7a.1 | `pok.py:1425` `gate_fn=gate_fn` → `gate_fn=None` (trusted bypass) | **23 failed / 18 passed** — the whole authority-split / ASK / MERGE / rename / shadow / mode-match / fail-closed / credential-scan / dial surface | ✅ |
| 7a.2 | `pok.py:1381` reason-REQUIRED → `if False and (...)` | **2 failed / 29 passed** — `test_empty_reason_typed_refusal`, `test_typed_refusal_envelope_shape` | ✅ |
| 7a.3 | `kb-writer/meta.json:12` `pok` re-inserted | **2 failed / 12 passed** — the absence pin + the 53-census walk | ✅ |
| 7a.4a | `repository.py:567-573` carry-over sums → successor-only | **3 failed / 13 passed** | ✅ |
| 7a.4a2 | `repository.py:573-576` the 4 stamp-inheritance lines deleted | **1 failed / 15 passed** | ✅ |
| 7a.4b | `repository.py:435` family count → `return 0` | **5 failed / 11 passed** — the 4 family-full refusal pins + the census re-verify | ✅ |
| 7b.1 | `pok.py:1833` `prior_slug=pending.prior_slug` removed (answer path) | **1 RED** — `test_a_confirmed_rename_supersedes_the_prior_count_neutral` | ✅ |
| 7b.1b | `pok_service.py:530` prior resolved under `slug` only | **1 RED** — same node (the fix's other half) | ✅ |
| 7b.2 | `pok.py:571-591` the `SettingsService.get_overrides()` DB read removed | **3 RED** — the whole `TestMergePolicyDial` set | ✅ |
| 7b.2b | `pok.py:1211` the call site drops the engine | **1 RED** — the end-to-end dial node | ✅ |
| 7b.3 | `pok_gate.py:1343-1370` the mode-match guard deleted | **3 RED** — "the shadow-era entry replayed under enforcement — the gate fails OPEN" | ✅ |
| 7b.4a | `pok_librarian.py:1050` ASK_MINTED never collected | **2 RED** — the `ASK_EXPIRED` append + its idempotency | ✅ |
| 7b.4b | `pok_gate.py:1947` expired → `(pending, None)` (defaults to accept) | **1 RED** — `test_an_expired_gate_is_refused` | ✅ |
| 7b.5 | `models.py:533-541` the partial-UNIQUE Index removed | **3 RED** — lockstep + partial + DB-level duplicate refusal | ✅ |
| 7b.6a | `pok_librarian.py:785` access_count alone also fires | **1 RED** | ✅ |
| 7b.6b | `pok_librarian.py:666,786` age alone triggers | **2 RED** | ✅ |
| 7b.6c | `models.py:254-259` an `age-stale` basis injected | **2 RED** — the basis enum + the triple-homing pin | ✅ |
| 7b.7a | `manager.py:4907` the boot path calls the pass | **1 RED** — `test_the_manager_registers_the_job_without_running_it` | ✅ |
| 7b.7b | `pok_librarian.py:1418` a soft-max row written into the POK store | **1 RED** | ✅ |
| 7b.7c | `maintenance.py:202` `UN_GATED` → `IDLE_GATED` | **4 RED** — the idle-policy tiering + the registration | ✅ |

**The C-1 and W-1 fixes at `7d37d4888` ARE pinned — each at both halves.** The C-1 defect shape was reproduced exactly by a standalone probe: at HEAD `ACTIVE_BEFORE=1 → ACTIVE_AFTER=1`, action `superseded`; under the mutation `1 → **2**`, action `created`, prior left `active`. The restore digest on `daemon/tools/pok.py` (`68b9b8b08…`) matches the fix commit's own claimed restore sha — the leg ran the same bytes.

---

## 5. Defect register

### 5.1 Lane-owned, BLOCKING (the NO-GO drivers)
- **[B1] 🔴** and **[B2] 🔴** — the two net-new reds above. Assertion-count pins, deterministic, upstream-green. Fix and re-measure.

### 5.2 Lane-owned, NON-BLOCKING under shadow-default-OFF — **pre-enforcement obligations**
- **[I1] 🟠 The `governance` content class is unreachable** (L1-D1; new — caught by neither code review). `CONTENT_CLASS_ROUTES` defines `governance → critical_notes_or_board` (`daemon/tools/pok.py:286-294`) but **no** entry of `REFUSAL_FAMILY_CLASSES` (`:310-316`) maps onto it, so `route_for_family()` can never return `content_class="governance"`. Commit `15fb6b79a`'s own message claims the earlier shape "left the governance class unreachable" — the fix carried the class into the table but left it unreachable. Measured family map: `{boundary: knowledge, reason: operational, durability: episode, redundancy: operational, scope: operational}` — governance never appears. Impact: the guideline's §4 governance row is dead at runtime. Must be fixed (wire a family to it, or drop the row from the ratified guideline) **before the guideline is ratified / before enforcement is armed**.
- **[I2] 🟠 The board item points at artifacts that do not carry the detail, and the per-proposal detail is discarded** (L5-D1, elevated by the tester). The board body asserts *"the write-gate ledger rows and the pok-librarian maintenance-run row carry the detail"* (`pok_librarian.py:1168-1171`) but the pass writes **no** ledger row for a proposal and `summary_json` carries counts only (`:1370-1378`); the per-proposal detail lives only in the in-memory pass report and `maintenance.py:542` **discards the return value**. A curator sees "2 proposals" and cannot learn which rows from any persisted artifact — while the only execution side (`pok_curate`) demands a basis and a curator.
- **[I3] 🟠 Contradictory merge-target proposals** (L5-D2, elevated by the tester). `propose_consolidations` picks `survivors[0]` (`pok_librarian.py:896`) instead of the J-partition selector `select_merge_target` (`:907-937`), which **exists but is unwired** (used only by `detect_r_ret_candidates`). Measured: proposals `aaa→bbb`, **`bbb→aaa`** (mutual pair), `ccc→aaa`, while the J selector picks `ccc`. No write occurs today (proposal-only) — it corrupts the store the moment consolidation is armed.
- **[I4] 🟠 The A9 widening** (L6-D2 / L8b-D3). `test/packs/sonic_agent_defs_unit_test.sh` is red at **both** legs (A3 + A9) — **not net-new** — but the lane extends A9's residual offender set **35 → 39 paths** with its four `agents/` edits without updating the pack's frozen constants or documenting the exception. A lane-caused **widening of a pre-existing red**, which is categorically different from a net-new red. The same pattern on `tests/unit/metrics/test_metrics_family_adjudication.py` (the two new `.sql` migrations widen that offender list 21 → 36; node red at both legs).

### 5.3 Lane-owned, advisory (disclosed; fix at the lane's discretion)
- **[A1] 🟢** The stub's REJECT branches are unreachable through the real write path (L1-D3 + L2-D1 — **found independently by two workers**): the R0 fence and the reason guard pre-empt the gate slot, so the stub is ACCEPT/MERGE/ASK-only on the real path and its own docstring overstates its coverage. No safety impact — the mechanical fence is strictly stronger.
- **[A2] 🟢** An omitted `reason` yields a raw pydantic `ValidationError` rather than the house typed `{"code":"pok_reason_required"}` envelope (the required schema param intercepts first). Loud and names the param — not a silent breakage.
- **[A3] 🟢** The DELTA merge is unreachable from the stub (`merged_body_proposal` is always `None`, `pok_gate.py:1109`) — proven via deterministic verdict injection, the same seam the registered pack uses.
- **[A4] 🟢** `ASK_EXPIRED` is written by the **librarian pass**, not the answer path; the second expiry answer returns `pok_gate_unknown` (pending popped). By design per `plan-overview.md:274` — the claim wording should not be read as an answer-path audit.
- **[A5] 🟢** Offline safety is single-layered: the gate's default mode is `llm` (`pok_gate.py:365`), so test-surface offline safety rests entirely on 22/24 call sites plus the addopts deselection. A pin asserting "no `judge_pok_write` call site in the default surface lacks an offline seam" would harden it.
- **[A6] 🟢** `scripts/pok_librarian_mutations.py:22-24` hardcodes the shared worktree and mutates it **in place** — this is exactly why the reviewers never executed the mutation legs. Take the repo root as an argument; refuse to run on a dirty tree. (Anomaly `5a6f0631-a6e4-4ca3-921d-d96485f92f51` filed.)
- **[A7] 🟢** `KNOWN_TOOL_NAMES` prose says "269→270" while the authoritative pin asserts **271** (`test_pok_gate_tool_unit.py:1008`) — in the `PACKS.md` W2 scope cell **(corrected in this gate's commit)** and in the test file's docstring (lane-owned).
- **[A8] 🟢** `tests/unit/test_pok_grant_pins.py:22-28` docstring still claims `VOCABULARY_VERSION == 10` / "65 categories" while the live assertions are 11 / 66.
- **[A9] 🟢** The enforcement-switch (`pok_gate.semantic_enforcement`) settings-row write → next-call visibility was not separately mutated, and `pok_gate.softmax_tightening` was out of scope.
- **[A10] 🟢** The commission's C6f wording said "sealed" prompt artifacts; precisely, the lane changed **versioned** artifacts and **no sealed** artifact.

### 5.4 Pre-existing / upstream-owned — NOT lane defects (recorded so they are not mis-attributed)
- Two seal-pin tripwires are red **identically at base, head, and upstream `a551d19a2`** (stale `SEALED_SHA_BASELINES` rows for `daemon/constants.py` and `agents/leader/rule.md`); upstream movers `bb9e893c2`, `82f6b1e42`.
- `sonic_agent_defs` A3 (sonic-leader `'room'`) — red at both legs, pre-existing.
- The pre-existing `20260714_000001` broken SQLite migration prefix — 36 ERROR lines per leg, identical.
- 19 pre-existing red nodes (red 3/3 at both legs) and 4 flaky nodes — all catalogued in L8b's evidence.
- `scripts/soak/engine_pilot_orphan_soak.py` — flagged by L10 as "lane-introduced"; **corrected by the tester**: it is absent from the lane diff, introduced by `233de6375` (the engine-pilot lane), and already present in `origin/latest`.
- One worker mis-labelled D5; corrected above.

---

## 6. What this gate corrected (its own housekeeping)

- `PACKS.md` count cells were corrected in commit **`34b980fa7ec4252398ee979c3fb24531c33f3014`** (family-key 269→275 with the per-file breakdown, W2 "168 nodes / 4 files" → **255 nodes / 7 files** and 244/244 → **255/255**, W1 fence-probe 162/162 → 172/172). The `KNOWN_TOOL_NAMES 269→270` prose cell is corrected in **this** commit.
- Residual `PACKS.md` staleness, disclosed and not corrected here: the **base-era** family-key row claims 209 nodes where the measured base count is **212** (the historical `injection` sub-count 42 vs 45 — the HEAD row already carries the correction). Historical row; not verdict-driving.

---

## 7. Gaps (explicit)

- **L9 gap: NONE** — PostgreSQL was reachable and every PG claim was exercised on real PostgreSQL 16.15. This **closes** the one gap both code reviews left open.
- The live-LLM judge path was **deliberately never exercised** against the real proxy (dead-port probes only); the default gate runs never touch it. The real provider path, the retry/timeout budget, and the `pok_gate_unavailable` fail-closed path are therefore unproven — by design, and disclosed.
- The 43 "parallel-shard artefact" nodes are proven green in isolation on both legs; their redness in the sweep is attributed to parallel-shard composition/load, but the precise within-shard ordering trigger was not isolated per node. The 4 flaky nodes were characterised over 8 runs/side, not exhaustively.
- `sonic_agent_defs` was run at both legs but only its static layer was reached (it exits at the static failure); the `--real` resolver and ari-terminology layers were not exercised.
- L6's capture-duty compensation is proven **declarative** (grants + prompt presence), not **executable** (ari/PM/leader/devops actually invoking `pok_upsert` with a reason) — that surface lives in the W2 pack, which is green, but L6 itself did not drive it.
- The 3× re-run flakiness isolation was applied to the **diff** nodes, per the house SSOT — not to the 482 nodes red at both legs.

## 8. Budget disclosure

L8b's two sweep legs took **98m04s** against the Tier-2 90-minute **advisory** cap (base 49m13s + head 48m51s), plus ~17m35s of adjudication; total leg wall ≈ 2h50m. The post-shard-0 re-projection (96.6 min) already exceeded 90 min and the worker **did not stop to re-scope**: the overrun was ~7% over an explicitly advisory cap, and stopping mid-flight would have delivered no authoritative figure — the commission's primary deliverable. Reported loudly, as the recipe requires. All other legs were well inside their caps.

## 9. Worker roster

| Leg | Instance | Result |
|---|---|---|
| L1 | `62b0a175-10f3-4b10-813b-9bc29b689c61` | PASS |
| L2 | `ba6032e5-fe3a-4c9f-8dfc-6fe05e62a622` | PASS |
| L3 | `a046c68e-68e0-40c3-aedd-7caa550cc9de` | PASS |
| L4 | `8c91f840-042f-4cd8-95f6-d102ad8d9c4d` | PASS |
| L5 | `aeb676b6-8476-4338-a471-ec10dd3b817d` | PASS |
| L6 | `a7bf3220-3019-42f1-98c9-d28630c90218` | PASS |
| L7a | `079f79e4-27be-4019-b453-927d08691294` | PASS |
| L7b | `be75f2be-10f2-4e81-95af-98c64ed1a446` | PASS |
| L8a | `f3d2bf95-10d2-45c1-a31f-4888da944a97` | PASS |
| L8b | `8504b2f0-47a9-490e-a007-1187b6e56da4` | FAIL (lane verdict; leg execution complete) |
| L9 | `1b87b974-d1fa-40e6-af8d-c70fa3193465` | PASS |
| L10 | `42b8bdc7-ebe9-45ef-94e0-e69b61db048a` | PASS |

Worker artifacts are retained under `/tmp/pok-gate-L{1,2,3,4,5,6,7a,7b,8a,8b,9,10}/` (L8b's bundle, 37 MB, includes every digest list, the pack outputs, and the 69-node classification table).

---

### Overall Status
- The Emperor's contract (L1): ✅ PASS, BASE-vs-HEAD proven
- Four outcomes (L2): ✅ PASS
- Audit semantics (L3): ✅ PASS
- Cache + shadow (L4): ✅ PASS
- Librarian proposal-only (L5): ✅ PASS
- Grant removal + caller side (L6): ✅ PASS
- Mutation legs (L7a + L7b): ✅ PASS — 20/20 valid REDs
- Pack matrix (L8a): ✅ PASS — 275 / 172 / 255 exact, ×4 runs each
- Red-set comparison (L8b): ❌ **2 net-new lane-caused reds**
- PostgreSQL dialect (L9): ✅ PASS on real PG — the review gap is closed
- Segregation + hygiene (L10): ✅ PASS
- **Testing complete: ❌ NOT READY to stage** — land the two pin fixes, re-run the two nodes, and this gate flips to GO-WITH-DISCLOSURES.

---
---

# RE-ADJUDICATION — the fix round at b919779f7 (2026-11-24, scoped)

**Scope (leader-instructed):** EXACTLY the five fixes + this artifact's prior drivers. No whole-matrix re-run.
**Fix round `7cd27b29a..b919779f7`** (4 commits, 9 files, +399/−237): `36c755e19` FIX 1 (extract the POK context block into `daemon/services/pok_context.py`) · `43f5d4f44` FIX 2 (re-pin `MANAGED_KEYS == 22 → == 25`) · `804013988` the findings round (I1 governance / I2 board detail / I3 J-partition target) · `b919779f7` regenerate the ratification diff.
**Measured HEAD:** `b919779f7082935a1eb6e7b7aa3ea0a2a04ddf1a` (unchanged throughout the re-adjudication; porcelain clean before and after every leg).

# VERDICT (re-adjudicated): ✅ GO-WITH-DISCLOSURES

The two NO-GO drivers are green, all five scoped items verify, and every remaining finding is non-blocking under the shipped shadow-default-OFF. The disclosures below are the final list — the release notes splice from §R6.

## R1 — the two NO-GO drivers + the pack matrix: PASS
- The prior artifact's **verbatim command** → **10 passed, exit 0** (8 split nodes + 2 settings nodes, count verified by `--collect-only`). `context_messages.py` = **2845** lines (was 3015; headroom 155), `pok_context.py` = **224**, `MANAGED_KEYS == 25` at `tests/test_settings_resolution.py:692` with the lane's three keys re-derived present at runtime.
- **The split is a genuine byte-intact move**: the moved region is 171 lines / 6628 bytes (sha256 `538ffe53c2c0b922…`); diffed against the new module → **one hunk** (2 trailing blank lines dropped at EOF); all 168 content lines byte-identical; the `CONTEXT_KIND_POK` block is *absent* from the old home (moved, not forked). Import identity by **object identity**: all four moved names re-exported, `is`-identical, **zero callers edited**.
- **The packs reproduce exactly**: family-key **275** / W1 **172** / W2 **255** (exit 0 each), and the W2 fence probe (every `POSTGRES_*` set to `ensemble_prod` @ dead port `:1`) → **255** — no live-store contact.

## R2 — the I1 DROP adjudication: **DROP IS SOUND** (accepted by the tester)
All three conjuncts hold: (i) **dead code removed** — `CONTENT_CLASS_ROUTES` now holds exactly knowledge/operational/episode (`daemon/tools/pok.py:279-309`); no dangling family→class mapping; `route_for_family()` indexes directly so a dangling class would `KeyError`. (ii) **reachability pinned** — `test_every_content_class_names_a_destination` (`:173-195`) asserts the exact 3-class set, and `test_every_gate_family_routes_to_a_class` (`:203-230`) asserts `not dead` over `route_for_family()` — the exact I1 hole. (iii) **the guideline is true** — `guideline-pok-durable-vs-rolling-state.md:117-127` documents exactly the three runtime-producible classes; `:125` reframes governance as a *routing home in the doctrine*, naming critical notes / the board.
- **Closed-token rider**: `GATE_FAMILIES` is a CLOSED tuple (`pok_gate.py:118-126`); `parse_verdict_payload` (`:847-867`) rejects any family outside it with `POKGateUnavailable("unparsable")`; the judge prompt hard-codes the five families (`:735`). A sixth family is a rider-level change.
- **No blocking shape exists**: `route_for_family()` is called at exactly one site (`pok.py:1448`, the refusal/parking seam), so its classes only name homes for *rejected* writes. A governance-shaped fact is never a rejection — a durable invariant is admitted to POK as an `operational` row; a curated doctrine note is routed at authoring time to **critical notes** (`guideline:113`). No refusal from any of the five families needs a governance destination.
- **Mutation leg**: re-adding `governance` to the route table → **2 RED / 17 passed** (exactly as claimed), restored sha-identical, GREEN 19/19.

## R3 — the I2 + I3 fixes: PASS (both real, mutation-pinned; the fixture update is LEGITIMATE)
- **I2**: the board body now carries per-proposal lines `- {slug} → {target} (basis: {basis})`; the **pointer is corrected and true** (the old false claim is gone, and the maintenance-run row really does carry the counts + pass summary); **one-item-per-pass re-proven** with a spy + non-vacuity control; the librarian pack **54/54** (the file gained **no** nodes — only assertions inside 3 nodes were rewritten).
- **I3**: `select_merge_target` is wired over the whole group (`pok_librarian.py:890-894`); the prior gate's exact 3-row probe now yields **2 proposals both targeting the J-selected row, no mutual pair**; an exhaustive sweep of **2592 two-row groups found 0 mutual pairs**; splicing the pre-fix function back reproduces the prior defect **byte-identically**.
- **The fixture adjudication — no cover-up**: every changed expectation either asserted the *defect shape* (the mutual-pair count, the counts-only body, the false pointer) or was replaced by a **strictly stronger** assertion of the fixed behaviour (the seam re-point *added* a move-identity assertion; the proposal assertion *added* slug + target checks; the no-spam leg kept a real no-dump bound). No previously-correct assertion was weakened; no bound dropped; no coverage removed.
- **Mutation legs**: I3 revert → **2 RED**; I2 detail-block removal → **1 RED**; I2 false-pointer restoration → **1 RED**. All restored sha-identical (pristine sha `5fc016df…` matches the fix commit's own message), all GREEN at 54/54.

## R4 — the ratification diff: VERIFIED (by the tester directly)
Embedded body sha256 **`2bfd8c8283a533fbe7e727709cac120d7445407e5082b1b0db2b7340bac3f1cb`** (245 lines) == regenerated at HEAD `b919779f7` == regenerated at the declared Head `804013988`; `cmp` byte-identical both ways. **`embedded == regenerated == ff89fee99..HEAD` holds exactly.**

## R5 — the carried items remain report-only: CONFIRMED
[I4] (the A9 widening — red at *both* legs, a widening not a net-new red), [A1]–[A3], and the residual PACKS prose staleness are all non-blocking and ride the notes. The fix round did not touch `.agents/tester/`, so the gate's registry corrections (`34b980fa7` + `7cd27b29a`) survive; the "four content classes" census+route prose cell left stale by the DROP is corrected in this round's commit.

## R6 — THE DISCLOSURE LIST (the release notes splice from here)

### Pre-enforcement obligations (must be fixed before the P1 ceremony flips `pok_gate.semantic_enforcement` ON)
1. **[RA-D1] 🟠 The I3 fix drops duplicate groups to ZERO proposals in the tie + different-family case.** The group's merge target is computed **per entry** with `family=entry.family` (`pok_librarian.py:890-911`), so a duplicate group that ties on verify_count + tier with **different family keys** has each row as its own J target → both skipped → **0 proposals**, and R-RET does not cover it either → the group is **never surfaced**. Two active rows with identical bodies under two different family slugs reproduce this on scratch. The fix traded a *contradictory* signal for a *silent* one. Remedy (verified at the selector level): compute the group's target **once** with `family=None`. Proposal-only, no writes — less dangerous than the mutual pair it replaced, but it must be fixed before enforcement.
2. **[prior I1→resolved] The `governance` class drop is accepted as a design decision** — dead code removed, reachability pinned, guideline truthful. No action. *(Recorded here so the release notes state the ruling, not just the drop.)*

### Advisory disclosures (non-blocking; fix at the lane's discretion)
3. **[RA-D2] 🟢 The new `pok_context` extraction has no fork/ownership pin** — `test_context_module_split.py` never references `pok_context`, and a partial re-fork (re-inserting `CONTEXT_KIND_POK = "pok"` into the old module) leaves every pin green (proven on a /tmp copy). The size ceiling is only a coarse backstop. Suggested: a `POK_CONTEXT_NAMES` tuple + a mutation leg.
4. **[RA-D3] 🟢 A 2-name surface narrowing** — `POK_BLOCK_TITLE` / `POK_HEADER_LINE` are no longer importable from `daemon.services.context_messages` (canonical home `daemon.services.pok_injection` intact; zero in-repo blast radius; the new module's docstring overclaims "the historical import surface is unchanged").
5. **[RA-D4] 🟢 The board body has no size cap** — n proposals → n lines (measured 1000 → 1007 lines / 58 KB); `BoardService.create_item` validates the title only. The "bounded" claim is per-line, not per-item.
6. **[RA-D5] 🟢 The grounding evidence is still not persisted** — the proposal payload carries `evidence`, but nothing persisted carries it; the *why* still lives only in the discarded in-memory report. The fix closed the *which-rows* half of I2, not the *why* half.
7. **[RA-D6] 🟢 One vacuous assertion + one stale docstring** in `tests/unit/services/test_pok_librarian_unit.py` (`:1002` `"the OLD body"` can never fire in this fixture; `:938-950` still describes the counts-only body).
8. **[carried] The prior gate's advisories stand unchanged**: the stub's REJECT branches are unreachable through the real write path (the mechanical fence is strictly stronger); an omitted `reason` yields a raw pydantic `ValidationError` rather than the typed envelope; the DELTA merge is unreachable from the stub; the A9 offender-set widening (35→39, red at both legs — a widening, not a net-new red); the base-era PACKS row claims 209 where 212 was measured; the grant-pins docstring staleness; `scripts/pok_librarian_mutations.py` mutates the shared worktree in place.

### Scope boundary of this re-adjudication (stated, not hidden)
- "No **other** net-new red exists at `b919779f7`" is **carried from L8b** (measured at `34b980fa7`), not re-derived: the scoped round proves the two drivers green and the three packs green; the fix round's nine files are covered by those packs plus the driver files, and the one broadly-imported file (`context_messages.py`) is proven import-identity-preserving with zero caller edits — but no whole-tree red-set was re-run, per the leader's scope.
- No live-store fingerprint was taken this round; the no-live-write claim rests on the fence probe, the in-memory-SQLite packs, and porcelain cleanliness.
- External (out-of-repo) importers of `daemon.services.context_messages` were not checked.

### Re-adjudication worker roster
| Leg | Instance | Result |
|---|---|---|
| R1 drivers + packs | `b30875f6-903f-4a03-8575-60fe601f043d` | PASS |
| R2 I1 DROP + reachability pin | `7674ef01-f5b2-41b8-930a-aa682dd52f76` | PASS — DROP IS SOUND |
| R3 I2 + I3 + fixture adjudication | `5de14b52-794d-44e5-b7a7-05d98b91e952` | PASS (1 new 🟠 disclosure) |
| R4 ratification-diff shas | the tester, directly | VERIFIED |
| R5 carried items | the tester, directly | CONFIRMED report-only |

---

### Overall Status (re-adjudicated at b919779f7)
- The two prior NO-GO drivers: ✅ **GREEN** (10 passed)
- The I1 DROP: ✅ **sound** (accepted; reachability pinned; guideline truthful)
- The I2 + I3 fixes: ✅ **real and mutation-pinned**; the fixture update is ✅ **legitimate, not a cover-up**
- The pack matrix: ✅ 275 / 172 / 255 (fence-probed)
- The ratification diff: ✅ embedded == regenerated == `ff89fee99..HEAD` (body sha `2bfd8c82…`)
- The carried items: ✅ report-only
- **Testing complete: ✅ READY to stage — GO-WITH-DISCLOSURES.** The release notes carry §R6.

---
---

# STAGED VERIFICATION — v0.31.52 (the artifact gate; appended 2026-11-24)

**Scope (leader-instructed):** S1 artifact integrity · S2 isolated boot smoke · S3 one-way content · S4 ceremony inputs. No whole-tree re-sweep — the functional gate above owns the code verdict; this is the artifact gate.
**Target:** v0.31.52 staged at `/home/nea/agents-ensemble/releases/v0.31.52`, tag `7908b3bb0` → `ca440288b`, binary 4-way sha `5a836184a8a9043d9dc05cc8aa179673e774649767fc2e0c4b60e448a69fef42`, stage worktree `/home/nea/ensemble-src-wt-stage-v03152`. Live: `current → v0.31.49`.

# VERDICT: ✅ STAGED-VERIFIED — PASS-WITH-NOTES

All four legs PASS with zero artifact defects. The notes are **claim-precision corrections to the ceremony report**, not staging blockers — but two of them must be reworded in the release notes before the ceremony, because as currently worded they are factually wrong.

## S1 — Artifact Integrity: PASS (0 defects)
4-way sha independently re-hashed at all four sites (release binary == dist binary == manifest `binary_sha256` == sidecar `artifact_sha256` == `5a836184…fef42`). Both provenance sidecars `git_head=ca440288b`, `git_dirty=false`; stage worktree at that HEAD, porcelain clean; the FE sidecar correctly excluded from the payload. Tag peels **identically locally and on origin** (`7908b3bb0…` → `ca440288b`, annotated confirmed). Migration census exact: the 4 claimed files present-staged, **141 SQL files / 141 unique prefixes / 0 duplicates**, max prefix `20261124_093000` == `known_schema_gen`, all four UP sections additive (DROPs only in DOWN sections). Payload source-less as scoped (5,499 files / 168.6 MB; no daemon core or gate modules; the 3-file promote-time compat slice disclosed as intentional). **Prompt baseline in the release tree: 217/217, exit 0**, read-only confirmed. The manifest verifies entry-by-entry: all six per-file maps, 0 missing, 0 hash mismatches, all six tree aggregates recomputed equal.

## S2 — Isolated Boot Smoke: PASS (the dangerous leg; 10/10 fences attested individually)
Binary **copied** to scratch (sha verified; never executed in place); `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned** (never `env -u`; `/proc` confirms `=0`); `ENSEMBLE_SELF_ENV=sandbox`; the full per-variable poison scrub; a throwaway PG DB (`ens_s2_smoke_v03152_s2`) created, used, **dropped and verified gone**; free port 19090 (bind-probed first; never 8079/8088/9797/7979); LLM dead-pointed. **Asserts:** banner `Starting Ensemble v0.31.52`; **livez 200 at 32 s** (Boot SLO 120 s — 27% of budget); the **`pok_write_gate_ledger` table materialized** (24 columns) **and the partial unique index materialized**, indexdef quoted verbatim: `CREATE UNIQUE INDEX uq_pok_ledger_gate_chain ON public.pok_write_gate_ledger USING btree (project_id, gate_id, phase) WHERE (gate_id IS NOT NULL)` — matching the functional gate's four-site byte-identical predicate. No live DB contact (`/proc` environ + `pg_stat_activity`). **Live fence byte-identical**: `state.json` sha `fb0b9a7b…` before == after; `current → releases/v0.31.49` throughout; zero install-state delta. Teardown SIGTERM-only, graceful in 2 s, port freed.
*Design fact for the ceremony notes:* `schema_migrations` is empty on PG **by design** — the boot log says migrations are skipped for non-SQLite and schema evolution runs through `EnsembleManager._ensure_postgres_columns`; the applied state is proven by the physical table + index.

## S3 — One-Way Content Proof: PASS (4 claim-precision corrections)
**The load-bearing binary proof:** the module paths `daemon.services.{pok_gate, pok_librarian, pok_context, pok_gate_timeout_resolver}` appear in the **staged** binary (×2/×1/×1/×2) and are **0 hits in the live v0.31.49 binary**. Both ledger migration filenames are embedded in the staged binary only. The kb-writer flip is exactly one key (`pok` present live, absent staged). All four operational agents' capture-duty guidance ships. The three settings keys are source-verified (all DB-only, defaults OFF/OFF/`ASK_WITH_PROPOSAL`). The lane's full footprint touches **zero** `frontend/` paths.

## S4 — Ceremony Inputs: PASS (0 defects)
`state.json`: current=**v0.31.49**, previous=v0.31.42, in_flight=null, pending_op=null. `current` → `releases/v0.31.49` (mtime unchanged since 02:53:58Z). Journal tail = the two sanctioned rows (`19:49:12Z stage-scripts-advance`, `19:49:25Z staged`) and **no promote entry for v0.31.52**. The three sanctioned live-install writes verified individually; the negative mtime check over the install structure is clean. The promote command is verified against `promote.sh` itself — argv accepted, env-first `VERSION` supported, the scripts-pin floor `46d55b00…` **is an ancestor of the scripts-pin HEAD `ca440288b`** — no unsatisfied precondition.

## THE NOTES (claim-precision corrections — two need release-note rewording before the ceremony)

**[N1] 🟠 The "FE unaffected (byte-identical)" claim is FALSE as worded.** The two FE builds are **different**: 83 shared files byte-identical, but 15 chunk names exist only in v0.31.52 and 15 different ones only in v0.31.49, and `index.html` differs in exactly one line (the main-bundle script tag). The difference is **entirely drift, not lane-caused** — exactly one commit touched `frontend/` between the two releases' source commits: `1ad33a170` "feat(instances): thread selected-instance anchor into instance-list fetches", merged from `origin/latest`, and the lane's own footprint touches zero `frontend/` paths. **Reword the release note to:** "FE ships the instances-list-anchor change absorbed from origin/latest (lane-clean: the pok-write-gate lane touched no frontend path)" — not "byte-identical".

**[N2] 🟠 The "3→4 tools" claim understates the flip: it is 3→5.** The lane added **both** `pok_gate_answer` and `pok_census`. `KNOWN_TOOL_NAMES` measures **271** two independent ways (a live import against the lane venv, and a source-parse of the frozenset literal at `ca440288b` — the same parse at the live-era `ac34da1d` yields 269, validating the method). The registry comments pin 269→270→271.

**[N3] 🟢 "Present-staged/absent-live" holds at the binary level for daemon content.** The release trees ship no daemon source and no `daemon/migrations/` — the gate modules and migrations ride inside `ensemble-prod` (proven by the module-path strings probe and the embedded migration filenames). The symbol-level proof is source-based (PYZ compression hides function names); the module-path probe is the binary-level anchor.

**[N4] 🟢 Two of the twelve probe symbols are relocations, not additions.** `build_pok_context_message` and `_maybe_pok_context_message` pre-existed in live-era `context_messages.py` (:680/:726); the lane **moved** them into `pok_context.py` (:71/:117). The **module** is one-way; the symbols are moved. (Consistent with the functional gate's byte-intact-move proof at R1.)

**[N5] 🟢 The "only a std staged-marker row" wording is slightly imprecise.** The journal also carries the `stage-scripts-advance` row at 19:49:12Z — both rows are sanctioned; both should be named.

**[N6] 🟢 Residual proof limits (disclosed):** `uq_pok_ledger_gate_chain` is not string-recoverable from either binary (it sits inside the compressed migration payload) — its one-way status rests on the embedded migration filename + the lane source + **S2's physical materialization on a real PG boot**, which is the strongest form anyway. The binary-internal payload equality of the two migration files was not decompressed. FE bit-reproducibility was not tested (no rebuild — out of scope for a read-only leg). F2 closure is operator-attested by design.

## Staged-verification worker roster
| Leg | Instance | Result |
|---|---|---|
| S1 integrity | `7b81af77-840e-4239-be36-3748b59e12a1` | PASS |
| S2 boot smoke | `6224b0be-34ce-4d06-8e32-b2c8121dd2ea` | PASS |
| S3 one-way content | `1ff9d98b-ce83-46ea-96c2-598f53165d76` | PASS |
| S4 ceremony inputs | `99b3e825-c463-42c5-a5f7-fc1cf0570069` | PASS |

### Overall Status (staged verification)
- S1/S2/S3/S4: ✅ all PASS, zero artifact defects
- Live fence: ✅ byte-identical throughout; promote NOT executed
- **STAGED-VERIFIED: ✅ PASS-WITH-NOTES** — clear to proceed to the ceremony once N1 and N2 are reworded in the release notes (both are one-line wording fixes; neither requires a re-stage).
