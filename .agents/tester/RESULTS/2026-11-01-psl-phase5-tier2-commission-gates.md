# Phase-5 Tier-2 Commission Gates — project skill source loader (whole-tree)

- **Date:** 2026-11-01
- **Commission:** Phase-5 Tier-2 whole-tree commission gates (mechanical escalation: `daemon/manager.py` in the diff — declared, expected)
- **Worktree:** `/home/nea/ensemble-src-wt-project-skill-loader` (`feature/project-skill-loader`)
- **HEAD (gated):** `67966efa8d1be1dc697f6f492110a8eb2a19b2d3` · **BASE (full lane span):** `b56444244`
- **Lane span:** 27 commits, 66 files, +17,745/−128; **daemon Tier-2 surface = 19 files**
- **Recipes:** `blast-radius-leg` **v1.0.3** + `whole-tree-base-proven-diff` **v1.0.0** + `failed-digest-equality-gate` v1.0.1 (all loaded deterministically; v1.0.3 folds verified present before running)
- **Lanes:** recon `80de6dc1` · whole-tree `9d6a01a9` (stalled, terminated) → **`476a5e91`** (completed) · PG `5cf117ec` · matrix `e5ad63a0` · e2e `89c778af` · census `4b45cf1c` · quick-fix `48a2ad2b`

---

## 0. HEADLINE RESULTS

1. 🔴 **Live-DB census falsifier FIRED: SUPERSET ⇒ STOP for staging (A18.4).** Canon name `regression-ab-partition-run` is held by THREE ids on the live `skills` table: known `fd1e01dc-37d5-4a0b-a05e-1933d18a006d` + **foreign `978ae5cc-413b-4c03-a17c-d50e4fe58f56` and `08a4332f-c059-4886-92d2-9d4aea191ff8`**. Staging must not proceed on fusion/shadowing until these two rows are adjudicated.
2. 🔴 **Whole-tree found a GENUINE 2-node net-new defect** — `daemon/manager.py:2314` boot-time `AttributeError` (None-guard missing on the A10 extra-roots hygiene loop, commit `f664e1234`). Fix proven by the gate lane (+2/−2), landed via authorized quick-fix (§7) — **branch is net-new-clean only at/after the fix commit**.
3. ✅ Everything else green: PG dual-side net-new ∅, mutation matrix 14/14 + 6 hand-verifications, E2E chain 6/6 with fresh-disk proof, A5 live drain 8/8, W-4 fence clean, sentinel set 16/16.
4. ⏱ **Tier-2 case study: TOTAL ≈137 min vs the 90-min advisory (1.52×)**; legs themselves **62.0 min** (4.0–4.1× faster than the measured-rate projection) vs G2's 9h32m serial hand-roll.

---

## 1. Tier decision + premise

**Tier 2 — mechanical:** `daemon/manager.py` IS in the lane-span diff (19-file daemon surface). No judgment call.

Migration delta (staging rollback input): **exactly +3** — `20261031_120000_create_project_skill_files.sql`, `…120001_create_project_skill_embeddings.sql`, `…120002_create_project_skill_usage_records.sql`; all `CREATE TABLE IF NOT EXISTS`, **zero executable FK/REFERENCES**, distinct prefixes; counts 115→118; `known_schema_gen` = `20261031_120002`.

---

## 2. Whole-tree dual-side (`476a5e91`; prior lane `9d6a01a9` halted at the projection gate then stalled mid-probe-phase — terminated, work persisted and reused)

**Setup reused, zero re-provisioned.** 8 file-boundary shards (~4,009 HEAD nodes/shard), **100% boundary purity both legs**, same map both legs.

| Leg | Collected | Closure proof | Wall |
|---|---|---|---|
| BASE | 31,787 | 30,964P + 461F + 25E + 331SK + 6XF == 31,787 ✓ | 31.2 min (1,021 nodes/min) |
| HEAD | 32,069 | 31,253P + 452F + 27E + 331SK + 6XF == 32,069 ✓ | 30.5 min (1,050 nodes/min) |

14 HEAD-only lane files = **282 nodes, 0 red** (entire project-skill-loader surface green).

**Digests — 6 (form, sha256) pairs:**

| leg | BARE-ID | GREP-FULL | NODE-ID-ONLY |
|---|---|---|---|
| BASE | `f02fb610…a79e3e` (541) | `0f24ba42…5970a` (541) | `6bd83109…53309` (486) |
| HEAD | `1361eada…ed4571` (532) | `6b683ecb…d15ed4` (532) | `d5f4cc7b…098dd5` (479) |

The naive forms carry 55/53 identical-composition LOG-noise ERROR lines (36× migrations.runner skip warnings etc.) — the NODE-ID-ONLY form is pure; F+E reconciles exactly with parsed summaries (486/479). **NET-NEW: BARE-ID and NODE-ID-ONLY agree exactly — 9 nodes.**

**3× isolation (168 rows, 28 nodes, serial, both trees):**
- 17/25 diff nodes: PASS 3/3 both trees ⇒ xdist-lottery flakes (not regressions).
- 2 nodes context-independent/context-sensitive flakes (both-tree behavior) ⇒ not net-new.
- **2 nodes = GENUINE NET-NEW** (webfetch pair; §7).

**Attribution — 470 stable both-side reds = 50 registered + 420 unregistered (all red-at-base ⇒ pre-existing):**
- Registered (node-id-normalized digest `3e29b72c…`): slash_commands_router 19 · defer_blocked_api 17 stable (+3 flake) · work_router 9 (+6 flake) · messages_router_ref_path 0 (fully resolved since the `2e74b835…` reference was minted — count drift disclosed; file-level membership is the classifier) · `test_skill_service_init` 3/3 exact · skill_evolution_config 2.
- **Unregistered pre-existing: 420 nodes, digest `c48f375f…`** — spawn_team_members 21, governor_integration 16, memory_integration 16, configure_builtin_idempotency 15, memory_system 7, coder_developer_migration 5, spawn_intelligence_tier 5, prompt_section_reference_integrity 5, instance_read_shakedown F12 ×4, defer TestConsistencyPin ×4 (attributed within the registered-family file), maintenancer_agent 3, in_progress_guard 2, load_skill 6, test_api::test_send_message_success 1, job_answer_tool 1, attestation_bound_escalation 1. Two probe-time candidates DROPPED (green in both legs).
- Supplementary: `test_inner_soul_compaction` ×3 = deterministic both-side reds on a no-xdist file ⇒ **pre-existing debt, registered by this gate** (was masked under `-n auto`).

---

## 3. PG corpus dual-side (`5cf117ec`) — PASS-WITH-PREEXISTING

- BASE 423 collected (18F/336P/34SK/2XF/33E) · HEAD 441 (+18 = the 3 lane PG files, **18/18 PASS**).
- Digests identical across legs in BOTH forms: BARE-ID `62e63664…` (51 ids) / GREP-FULL `06cc3aa2…`; `comm` both directions EMPTY ⇒ **net-new ∅**.
- The quarantined 29-id block reproduces **byte-exact at `2fad8e8a…` on both legs**.
- Finding: the PG pre-existing red surface is **51 ids, not 29** — 22 unregistered both-side reds (`metrics_census` ×8, `resource_monitor_flusher_bind` ×3, `readiness_pg` ×3, `chat_source_claim_lane` ×3, `report_delivery_recovery` ×2, `list_queues_admittable` ×2, `06f500af` ×1).
- Teardown: both scratch DBs/roles dropped, 0-row proofs, worktrees removed, porcelain byte-identical.

---

## 4. Consolidated mutation matrix (`e5ad63a0`) — PASS

- Harness `tests/packs/psl_fix_cycle_mutation_matrix.py` → **rc=0 in 190 s**, 14/14 legs (13 discriminating RED→GREEN + 1 declared belt leg `W5-off-guard`), TSV recorded.
- **6 independent hand-verifications, ZERO disagreements** with harness/PACKS: P1-M1 (FK smuggle → 2 pins RED), P2-M1 (budget-before-shadow → twin leak), P3-M3 (instance_id dropped → spy kwargs), P4-M1 (site-1 file_source dropped → 2-site count 1≠2), W4 (unconditional `source: db` → OFF byte-identity broken), A3a (delegation sentinel — the ONLY discriminating assertion while tolerant forms stayed green, exactly the false-confidence class it was built for).
- The 2 legs the harness does NOT carry (P1-M1, P2-M1) are now independently re-proven — the "harness could be self-deceiving" gap closed.

---

## 5. E2E + A5 + W-4 + sentinel (`89c778af`) — PASS

- **P9 chain 6/6** (ephemeral driver; real services + real tools): seed canon+DB twin → fused prefilter (ONE pf-addressed candidate) → real `skill_search` → pf entry + `shadows` stamp + twin retired (appears ONLY under shadows stamps) → real `skill_view` from disk → **fresh-disk proof: body file mutated post-search; reload served the mutated text, stale body absent**. *Disclosure: no committed P9 test exists at HEAD — phase5-plan task 11 unimplemented; the driver is evidence, not a substitute.*
- **A5 live teardown 8/8** on a REAL `InstanceManager` (fenced scratch, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned): real project+instance rows, resolver bound (`refused=False`), **aclose AWAITED** (latch: shutdown 0.166s ≥ 0.15s sleep), refresh tasks cancelled+awaited, second `shutdown()` = **0.000 s no-op**, SIGTERM-clean rc=0. Hit the documented `20260714_000001` fresh-SQLite blocker; resolved via the pinned ledger-pre-mark workaround, in-driver only.
- **W-4 fence**: 3 pins PASS + static census — all 3 allowlist sites exist (graph.py:886, instance_messaging ×3 lines, skill_tools `_get_project_id`); the only scope-adjacent additions are resolver CONSUMERS via `resolve_active_project`; **no new ad-hoc scope-reading site**.
- **Sentinel set 16/16**: P1 body sentinel ×2 · P3 leak 4-shape + R3 ×6 · P4 shadow ordering ×4 · anti-conflation ×4.

---

## 6. Live-DB collision census (`4b45cf1c`) — 🔴 SUPERSET ⇒ STOP

One session, 5 SELECTs, `transaction_read_only=on` proven in-session, `--single-transaction`, `-X`; porcelain untouched.

- Q1 (3 in-scope ids): **exactly 3 rows, canon names** ✓
- Q2 (08e67030): name = `ab-base-leg-provisioning-artifacts` ⇒ counts in-family per the name/count rule ✓
- Q3 (canon-name sweep): **6 rows — the known 4 PLUS two foreign ids both named `regression-ab-partition-run`**: `978ae5cc-413b-4c03-a17c-d50e4fe58f56`, `08a4332f-c059-4886-92d2-9d4aea191ff8` 🔴
- Q4: total skills = 507 (context).

**Gate input: STOP — fusion ON / shadowing gated re-adjudication required before staging (A18.4).** The live DB was NOT mutated.

---

## 7. Net-new defect + authorized quick-fix — LANDED

**Defect (proven by the whole-tree gate):** `daemon/manager.py` boot-time EXTRA_ROOTS hygiene loop read `getattr(self.config, "project_skill_source", None).extra_roots or []` without the None-guard its enclosing `_pfss_enabled` ternary has ⇒ any Manager boot with a config lacking the section raises `AttributeError: 'NoneType' object has no attribute 'extra_roots'`. Introduced by `f664e1234` (A10 six-site wiring). Deterministic: `tests/unit/test_webfetch_builtin.py::TestWebFetchBootstrapIntegration::{test_bootstrap_creates_webfetch_server, test_schema_drift_removes_stale_flag}` green at BASE, ERROR 3/3 at HEAD, green 3/3 when patched. *History note: this pair is a known manager-init canary — quarantined once 2026-10-14 under "Cluster C — InstanceManager init gap", fixed then, re-broken by this lane.*

**Fix (gate-proven form, landed by authorized quick-fix `48a2ad2b`):**

```diff
+            cfg = getattr(self.config, "project_skill_source", None)
             for _extra in (
-                getattr(self.config, "project_skill_source", None).extra_roots
-                or []
+                cfg.extra_roots if cfg is not None else []
             ):
```

**Commit `ccfac9c758e0d0933380b0e06eaceec2165cd36e`** (`fix(manager): None-guard project_skill_source extra_roots in boot hygiene (Phase-5 net-new webfetch pair)`, parent `67966efa8`, exactly 1 file +2/−2, scoped-staging proven, no push).

**Re-gate at the fix commit:** full `test_webfetch_builtin.py` **37 passed** (2.48 s) · the 2 target nodes **3/3 isolated green** (2.02/1.63/1.69 s) · sibling wiring pack `test_project_skill_source_phase4_wiring.py` **33 passed** (26.01 s). Main porcelain byte-identical before/after.

**Consequence: the branch's net-new set is ∅ at/after `ccfac9c75`** — any ceremony must gate on this commit. (No QUARANTINE row for the pair: it is a fixed regression, not pre-existing debt.)

---

## 8. Budget accounting — the doctrine's Tier-2 case study

| Measure | Value |
|---|---|
| Legs (whole-tree BASE+HEAD, 8 shards each) | **62.0 min** (31.2 + 30.5) at 1,021–1,050 nodes/min |
| Measured-rate projection for the legs | 247–256 min ⇒ legs ran **4.0–4.1× faster than projection** (projection rate ~250/min measured on probes; steady-state corpus rate is ~4× the probe rate) |
| **TOTAL gate wall** | **≈137 min (03:38→05:58 UTC) vs 90-min advisory = 1.52× (+47 min breach, mandated deliverable)**; ~46% of the authorized 4.5–5 h ceiling |
| Breach composition | ~44 min = the 168-probe serial isolation phase, inflated by the first lane's mid-phase death (a fresh run pays it once) |
| G2 counterfactual | same guarantee class, 9h32m serial hand-roll ⇒ **~4.2× faster wall, and a defect FOUND rather than buried in timeout noise** |
| Other lanes (parallel) | PG ≈22 min · matrix ≈15 min · e2e ≈23 min · census ~5 min |

**Recipe finding (v1.0.1 candidate for whole-tree-base-proven-diff):** the ≈9–15 min/leg calibration does NOT hold for this corpus/host; worse, the probe-measured rate (~250/min) under-projects steady-state by ~4× — the projection guard fired a false 3× breach and would have cancelled a 62-minute run. Fix: project from a probe of ≥1,000 nodes AND a shard-0 checkpoint re-projection (re-estimate after the first full shard), and restate the calibration as corpus-dependent.

---

## 9. Findings

### 🔴 Critical
- **C1 — census SUPERSET (§6):** two foreign live-DB rows named `regression-ab-partition-run`. Staging blocker per A18.4. Owner: whoever adjudicates live-DB skill rows (user-visible decision: retire/rename the foreign rows, or gate shadowing).
- **C2 — manager boot None-guard (§7):** genuine net-new 2-node defect; fixed via authorized quick-fix at `<fix-commit>`; **the branch is net-new-clean only at/after that commit** — any ceremony must gate on it.

### 🟠 Important
- **420 unregistered pre-existing unit reds** (digest `c48f375f…`) + **22 unregistered pre-existing PG reds** — real, byte-stable, red-at-base, and invisible to routine gates that trust the quarantine registry. Registered summarily by this gate; the registry needs a reconciliation pass.
- **P9 committed test missing** (phase5-plan task 11): the e2e chain is proven only by an ephemeral driver. The lane owes the committed test.
- **Reference-digest rot:** the `2e74b835…` 72-red reference predates fixes (slash 32→19, messages 3→0); file-level membership, not digest-count, is the working classifier. Reference digests need re-minting or count-free semantics.

### 🟡 Minor
- `inner_soul_compaction` ×3 deterministic pre-existing reds on a no-xdist file (masked under `-n auto`) — registered to QUARANTINE by this gate.
- Whole-tree stall episode #2 (provider class): lane `9d6a01a9` died mid-probe-phase; terminated + one replacement reused persisted state with zero work loss. The recipe's persistence design paid for itself.
- Probe-measured candidates `test_prompt_version_dualdriver` / `…content_mutations` were probe-time-only reds (green both legs) — probe reds are candidates, never evidence.

---

## 10. Verdict

### Per leg
| Leg | Verdict |
|---|---|
| Whole-tree dual-side | **FAIL at gated HEAD `67966efa8` (net-new = 2, genuine)** → **NET-NEW CLEAN at `ccfac9c75`** (fix landed + re-gated §7) |
| PG corpus dual-side | **PASS-WITH-PREEXISTING** — net-new ∅, digests identical both forms, 29-id block byte-exact, D1-PG 18/18 |
| Consolidated mutation matrix | **PASS** — 14/14 harness + 6 hand-verifications, zero disagreements |
| E2E chain + A5 live teardown | **PASS** — P9 6/6 (fresh-disk proof), A5 8/8 (aclose awaited, idempotent, SIGTERM-clean) |
| W-4 allowlist fence | **PASS** — 3 sites exist, no new ad-hoc scope reads |
| Sentinel/leak/shadow/anti-conflation | **PASS** — 16/16 |
| **Live-DB census** | 🔴 **SUPERSET ⇒ STOP** — staging blocker (A18.4), two foreign `regression-ab-partition-run` rows |

### Overall

# ✅ PASS-WITH-NOTES — regression gates clean at `ccfac9c75`; 🔴 STAGING STOP on the census

1. **The whole-tree guarantee holds only at/after `ccfac9c75`.** The gate at `67966efa8` found a genuine 2-node net-new defect (manager boot None-guard) — which is the Tier-2 gate doing its job — root-caused, fix-verified by the gate lane, landed via authorized quick-fix, and re-gated green. Any merge/ceremony decision must reference `ccfac9c75`, not `67966efa8`.
2. **Staging MUST NOT proceed on the current live-DB state (A18.4).** Two foreign skill rows named `regression-ab-partition-run` (`978ae5cc-…`, `08a4332f-…`) break the exact-set invariant the fusion/shadow design assumes. This is a user-visible adjudication (retire/rename the foreign rows, or gate shadowing), not a code fix.
3. **Rollback-safe input for staging:** migration delta exactly **+3**, additive `CREATE TABLE IF NOT EXISTS`, zero FK, `known_schema_gen` = `20261031_120002`.
4. Notes carried: P9 committed test still owed by the lane; 420+22 unregistered pre-existing reds now registered; reference-digest rot on `2e74b835…`; probe-vs-steady-state rate projection gap (recipe v1.0.1 candidate).

**Not authorised by this verdict:** merge, promote, or staging — regression evidence only, plus the two explicit gate inputs above (fix-commit dependency + census STOP).

---

## 11. Fences honoured (all lanes)

Per-variable scrubs + asserts in ONE runner per lane; never the `env -u POSTGRES_*` glob; zero contact with `ensemble_prod`/10.44.0.2 (except the census's authorized SELECT-only session)/:9797/ensemble-main/`current`/`releases/`; no daemon boots outside the fenced A5 scratch manager; no `-x`; explicit timeouts everywhere; `install-audit.jsonl` preserved-and-restored per recipe §0 where dirtied; MAIN porcelain `a1170f3c…` byte-identical before/after on every lane; all scratch worktrees/DBs/roles removed with proofs.
