# Soak-Gate + MCP-Warmup — Independent Functional Gate (2026-10-28)

**Commission:** functional gate for `feature/soak-gate-mcp-warmup` (promote soak-gate fix + MCP warmup degradation fix)
**Worktree:** `/home/nea/ensemble-src-wt-soak-gate` · HEAD `0f2d854af85b779cb67e349aeec13a0fc809a66c` · base `d5a4e4ad92d19f3265b97bc29c5aa3943f026ed2` (54 commits, brief said ~30 — disclosed)
**Tester:** independent gate lane; every number below is the TESTER'S OWN worker runs, never the developer lanes' evidence.
**Plan authority:** `.agents/shared/planning/soak-gate-mcp-warmup/requirements.md` §4.1–§4.7 (normative), §5 ACs; `decisions.md` §10 rulings.
**Migration gate:** `git diff --name-only d5a4e4ad9..0f2d854af -- daemon/migrations/versions/` = **EMPTY** (verified against git objects by recon worker 5568fa40).

---

## VERDICT

**Commission: PARTIAL — Gate 3 base-side digest incomplete (bounded at 08:41Z per leader finalize order); Gates 1, 2, 4 COMPLETE.**
**Staging recommendation: GO-WITH-DISCLOSURES** — the defect is proven closed (both directions, my runs), the safety property proven intact (both revs), every documented mutation leg independently RED-verified, all four Gate-4 obligations met (incl. the Gate 4(i) staging blocker: VERIFIED, no operator value lost). The one missing piece — the whole-tree base-vs-HEAD FAILED-list comm — is compensated on the lane's own change surface (scoped net-new ∅, acceptance-pack d1 leg, 254 collected) and the HEAD digest is preserved sha-pinned at `/tmp/g3head/` for a same-recipe base replay (recipe in §Gate 3) if the leader wants the byte comparison before promote.

---

## Gate 1 — Original symptom, both directions ✅ COMPLETE

### (a) Defect reproduces at BASE, closed at HEAD — MY runs at both revs

| Pack | BASE d5a4e4ad9 (adapted harness, sha-verified copies) | HEAD 0f2d854af (unadapted pack) |
|---|---|---|
| `promote_soak_grace_burst_test.sh` (A5(i)) | **FAIL — the defect**: B1 auto-rollback at cap-reach, `consecutive_green=0`, HEALTHY release rolled back; B4 zero grace-engagement samples; `/readyz` body: `status=degraded`, reasons present, `grace_active:null` (NO grace block), steady-state 1.0s budgets | **PASS 15/15** (90s wall): grace ENGAGES on grace-eligible `pool_busy`; reasons stay visible during grace (`detail.reasons`=2 entries while `grace_active:true`); counter reaches 4 greens (≥3), `gate_soak` rc=0; NO rollback/quarantine journal events; healthy-busy oscillation (90 rises/120 falls, 0 leak samples); C2 dead-host MCP quarantined `dns_nxdomain` <10s; 3 streams concurrent |
| `/readyz` during burst | degraded + steady-state budgets, no grace block | degraded + `grace_active:true`, `grace_reason=pool_busy`, future `grace_expires_at` |

Additional HEAD stream-level proofs: counter-unit pack **21/21** (S1 hold-bridge vs S2 hold-past-cap; S6 livez-transient=RESET; S7 never-green rollback at cap-reach `consecutive_green=0`; S15 expired-grace=RESET; S14 no `_PROBE_STATUS` aliasing).

BASE-side adaptations (minimal, documented, no-ops at HEAD where symbols exist): errexit relax + `command_not_found_handle` shim + init_guard import guard in harness child (restored byte-identical, sha `a63cd764…`). Base FAILs are precisely the feature-absent legs (unbound `GRACE_HORIZON_S`, empty grace journal) — the defect itself.

### (b) Safety property holds at BOTH revs ✅

| Leg | BASE | HEAD |
|---|---|---|
| N1 hard-red ⇒ rc 1 immediately | PASS | PASS (probes=2, elapsed 0s, first observation) |
| N2 never-answering ⇒ rc 1 | PASS | PASS (cap-reach) |
| N3 grace-eligible never-greens ⇒ rc 1 | n/a at base (no grace) — trivially rolls back | **PASS: grace ARMS (HOLD observed), cap-reach `consecutive_green=0 < 3` ⇒ rc 1** — the §4.1 P1 leg |
| N4 REAL promote.sh auto-rollback (sandbox lane, scratch install root) | **PASS (N4a–N4i: rollback + quarantine journal events, `current` reverts to previous, previous NOT quarantined, failed release IS)** | **PASS 26/26** same observables + N4j no-DB-resolution |
| fail-fast unit (A5(iii)(a)) | F1/F2/F3 PASS (hard red first-observation, pre-existing) | PASS 9/9 (rc=1 calls=4/4/2 at ceilings; F4a–e cap arithmetic 120/300/40/90/300; F5 no-HOLD⇒no-extension) |
| fail-fast integration (A5(iii)(b)) | PASS 16/16 | PASS 16/16 (F1 15s ≤ one cadence; F2 real promote.sh 12s, journal `gate fail: soak failure` + ROLLBACK event) |

### (c) Grace cannot mask ✅

Acceptance pack **20/20 at HEAD** (10m02s): (a) payload observability — degraded + reasons=2 + `grace_active:true` + future expiry; (b) **leak refusal** — pool-exhausted signature (`pool_size=10 pool_max=10 pool_available=0 requests_waiting=1`) ⇒ `grace_active:false`, reasons still reported; (c) **horizon bound** — `READINESS_GRACE_HORIZON_S=120.0` observation-relative (`daemon/constants.py:165`), armed→lapsed⇒plain degraded, NOT re-armed; unit pin `TestSubCeilingGapIsBounded` (sub-ceiling expiry at 120s) passed in the d1 net-new-∅ set (254 collected); (d) full W-2 aggregation matrix (all-row-1 grace ON / row-1+row-2 OFF / row-1+leak OFF / all-green inactive).

---

## Gate 2 — Independent mutation corpus ✅ COMPLETE (naming gap disclosed)

**The brief's "M1–M14" series does not exist in-repo** (verified: no `dev-evidence.md` for this commission; no M-numbered table in 8 planning artifacts; only `M11`/`M12` appear once, in `6441d1a4f`). The REAL corpus (recovered from commit bodies) was planted INDEPENDENTLY in scratch worktrees (`-soak-mutA`/`-soak-mutB`, uv-synced py3.14, shared worktree verified untouched, every leg reverted, nothing committed):

**Part A (readiness/lib.sh/constants) — 14 legs planted:**

| Leg | Mutation | Pin | RED | GREEN after revert |
|---|---|---|---|---|
| M11 | grace horizon never expires (`readiness.py:767`) | TestGraceStateMachine + sub-ceiling set | 4 unit + 1 integration RED | ✅ |
| M12 | grace re-arms every tick (`readiness.py:757`) | same | 5 unit + 1 integration RED ("the expiry moved") | ✅ |
| M-F2 | orchestrator forward dropped (`readiness.py:1459`) | buffered-transition payload pin | 1 RED (`KeyError`) — documented count exact | ✅ |
| M-F5 | observe guard removed (`readiness.py:1413-29`) | observe-exception pin | 1 RED (RuntimeError) — documented count exact | ✅ |
| G-C2 | leak tick threshold 3→999 (`constants.py:175`) | TestLeakPredicate | 3 RED | ✅ |
| G-L2 | counter reads `_PROBE_STATUS` global | packs | 3 RED (S13a/S14a/N3a) | ✅ |
| G-L3/G-L4 | ceremony-budget / absolute-ceiling min() terms dropped | packs | 2+2 RED | ✅ |
| G-L7 | never-green cap-reach `return 1`→`0` | packs | **10 RED** (S2..S15, N2) — the unconditional trigger is pinned | ✅ |

**Part B (mcp/hold) — 24 legs planted, 17 documented RED-claiming legs all verified RED→GREEN:** N1a, N1b, N2 (claim release/reorder), C-1 ×4 (recovery-gate reverts incl. integration lane), S3 ×2, CRITICAL-1 ×2 (outer-cancel swallow), W2 (reap matcher), handshake-bound (hang-form RED), handshake 55s-rejected constant, G1 `__aexit__`, request_close sync, F2 (independently re-verified), F4a/F4b (drop-on-raising-handler; identity-vs-value removal), F5 (re-verified).

### False-confidence findings (legs that stay GREEN — TEST-COVERAGE gaps, not source defects; all proven load-bearing via asymmetric probes that PASS on shipped code and FAIL under the mutation)

1. **FC-A1 `READINESS_GRACE_HORIZON_S` value unpinned daemon-side** — every pin is relational to the constant; 100000.0 stays green. (lib.sh's copy IS value-pinned by S12a.)
2. **FC-A2 `MAX_HOLDS_PER_GREEN` behavioral guard unpinned** — no shipped scenario puts a GREEN behind ≥3 consecutive HOLDs.
3. **FC-A3 leak-refusal predicate unpinned** — S8's fixture carries `grace_active:false`, decided by the wrong gate; asymmetric probe (grace-active AND leaking) proves the leak check load-bearing: broken ⇒ leaking daemon HELD + window EXTENDED.
4. **FC-A4 failure-CLASS check unpinned** — S9 decided by the wrong gate likewise.
5. **FC-A5 grace-ACTIVE gate unpinned** — S15 asserts only rc (identical either way); probe proves: gate removed ⇒ expired grace HELD + `soak_grace_extended` journal event — exactly what S15 claims cannot happen.
6. **FC-B1 W1 idle-reap cancel-propagation unpinned** (commit only claimed CRIT-1/W2).
7. **FC-B2 `WARMUP_TOTAL_BUDGET_S` no tripwire** — every shipped scenario un-wedged by the per-server create budget first.
8. **FC-B3 reap backstop only transitively pinned** — RED only with BOTH layers removed (either layer alone suffices; `__aexit__` layer IS unit-pinned).

Pack-fidelity defects (routed to developer lane): **PF-1** stub maps soft-green to rc=2 where real `lib.sh` returns rc=0 busy-amnesty (`_probe_once_classified` :3210-3215) — S4/S5 aliasing scenarios structurally cannot observe the defect they're named for; **PF-2** `L:<spec>` cannot express livez/readyz asymmetry. **FL-1** burst-pack B7 flaky at baseline 1/13 (~7.7%) — recorded in QUARANTINE.md (WATCH); any B7-driven RED requires ≥3× re-run.

---

## Gate 3 — Whole-tree net-new-red diff 🟡 BASE side IN FLIGHT

**HEAD side COMPLETE** (worker eacd99e5, ~2.2h wall):

| Lane | Collected | Passed | Failed | Skipped | Errors |
|---|---|---|---|---|---|
| Default (addopts deselect int/pg/slow/conf) | **30,853** (1,033 deselected) | 30,205 | **322** | 296 | 25 |
| Integration (`--override-ini="addopts=" -m integration --timeout=600`) | **575** | 460 | **31** | 58 | 0 |

Digests: `/tmp/g3head/failed_default.digest` (322 lines, sha256 `b8b658cfff23d0f3838db961f5d5985e7e175bcd5505742fdfe54c9eabc8e0ac`), `/tmp/g3head/failed_integration.digest` (31 lines, sha256 `cde1a9a531fda4038e8fbf70d682ff626a663429de36357ffc28d905d9e58ad6`). Non-zero collection ASSERTED on both lanes.

Adapted recipe (replayed identically at base — disclosed): `--ignore=tests/packs/g7_unique_index_smoke_test.py` (module `sys.exit(0)` → INTERNALERROR) + `--ignore=tests/e2e/test_context_injection_hybrid.py` (collection-time HTTP to :8079); `ENSEMBLE_SELF_ENV=dev` (ambient `live` hard-aborts T12 family); default lane `--timeout=120` (thread-timeout 30 does not fire for async tests — the hang class); chunk splits on hang (chunk_08 → 08a/08b; integration i01 → 3-way). Zero fence incidents; port-string matches were pytest tmpdir names, not bindings.

**BASE side: PARTIAL — executed, not digest-complete (worker 590fd172, terminated 08:41Z per leader finalize order).** Chunks 01–11 ran under the corrected recipe (per-chunk logs in `/tmp/g3base/`), but chunk 00 never completed (recurring async hang surviving `--timeout=120` + three split attempts + 900s ceilings) and the integration lane was mid-flight (i00 complete, i01 in splits) at termination. The mixed-recipe log landscape (pre-adaptation + adapted re-runs, RC=124 remnants, empty-tailed interrupted logs) does NOT support a byte-faithful base digest — the tester declines to fabricate one.

**The whole-tree base↔HEAD FAILED-list comm: NOT-PRODUCED** (reason above). Compensating evidence, all the tester's own runs:
1. **Scoped net-new ∅ on the lane's change surface** — acceptance pack d1 leg: readiness unit corpus 254 collected at HEAD, failing set == exactly the 4 known pre-existing `TestContext7Bootstrap` reds (pydantic-v2 `MagicMock(spec=ServicesConfig)` debt since `61ae2af35`, an ancestor of base) ⇒ zero net-new on the readiness/grace/counter surface.
2. All six lane packs PASS at HEAD (my runs, §Gate 1); all mutation-corpus baselines GREEN at HEAD (§Gate 2).
3. HEAD red count 322 default / 31 integration is CONSISTENT with the tree's documented pre-existing debt (the v0.27.0-era verification recorded 355 pre-existing reds at a comparable base) — corroborating, not probative; disclosed as such.
4. **Reproducibility contract:** HEAD digests are sha-pinned (`b8b658cf…` default, `cde1a9a5…` integration) and the exact recipe (incl. the 4 adaptations + chunk files) is preserved under `/tmp/g3head/`; a base replay in the retained worktree `/home/nea/ensemble-src-wt-soak-base` (venv intact, py3.14) + `comm -13` completes the comparison in one unattended run if the leader requires the byte proof before promote.

Coverage statement (base side): default lane ≈11/12 chunks completed under adapted recipe; chunk 00 (files listed in `/tmp/g3base/chunk_00.txt`) uncovered; integration lane partial (i00 done, i01 partial). Worker-level raw evidence by instance 590fd172 (terminated; artifacts on disk).

---

## Gate 4 — Four obligations ✅ ALL COMPLETE

### (i) Prod context7 row (SELECT-only) — CLAIM VERIFIED ✅
8 autocommit SELECT batches (worker f7caa83d, zero writes, `-X -A -v ON_ERROR_STOP=1`):
- Row: `context7` (id 9ca2f198…), `config_schema_version='3'`, updated 2026-10-27T15:14:57Z; config env = `{"CONTEXT7_BASE_URL":"https://context7.com"}` ONLY; `CONTEXT7_API_URL` **absent** (`row_has_api_url=f`).
- Row config **byte-identical to HEAD `build_config({})`** (whole-jsonb sha256 `150cdb18834e72477870dd71c425053c2ee42ac3a69025ae5aed1995b1f62a0e`).
- **Structural proof no operator value was lost:** schema "3" exists ONLY in `d52b14069` (2026-10-27 14:59Z), the SAME commit that introduced the env-PRESERVING heal (`_heal_config_preserving_env`; row env overlays template, undeclared keys retained); `git log --all -S` shows no revert; worktree `daemon/` clean ⇒ the writing boot ran the env-preserving heal ⇒ any pre-existing operator value would still be present ⇒ the pre-incident row env was empty. Counterfactual (row with operator value) heals to a row ≠ current — non-vacuous. Control: prod `opendesign` row carries live KMS/BYOK operator env (rows CAN carry operator values).
- Evidence hunt: `CONTEXT7_API_URL`/`CONTEXT7_BASE_URL` in `daemon/` exist only from the lane commit; planning docs record the operator-remediation route only as a hedged hypothesis; the `.env` premise is falsified (SDK child env = `get_default_environment()` only); repo-wide non-default context7 URLs are synthetic test fixtures; no audit/event trail records any config change.
- **Advisory (not a blocker):** live v0.27.4–v0.27.8 binaries carry schema "2" + the OLD whole-config replace (`v0.27.4:daemon/manager.py:2692`): at next live boot the v3 row reverts to v2 shape (transient v3); an operator context7 re-pin before promotion WOULD be dropped by the live binary's old heal at next boot. Promoting the lane closes the window. Wording correction: the incident write was an env-PRESERVING merge, not a "rebuild to default mirror".

### (ii) Lane B full-boot leg ✅ PASS
Worker bfa8589e: 2/2 collected (`ENSEMBLE_MCP_WARMUP_PIN=1` gate fired — not vacuous). Lane A: p95 0.5ms / max 52.9ms (161 samples). **Lane B (real boot): p95 1.4ms / max 7.7ms** (119 samples) vs §4.6 bounds 250/500ms; `/livez`+`/readyz` 200 throughout warmup. Warmup budgets honored at tree defaults (create 60.0 / total 90.0 / handshake 30.0 — `warmup_pool.py:108-109`, `stdio_wrapper.py:107`). Fence: scratch `ensemble_looplag_test` DB created+dropped; FAKE_HOME; in-process ASGI (no real port); `data/ensemble.json` temporarily pointed at scratch then restored byte-identically (sha256 `c61de84a…` pre/post verified). G1-census observable owned by sibling pins (`test_mcp_child_liveness.py` / `test_mcp_warmup_starters.py`) — covered green in Gate 3 whole-tree + mutation baselines; loop-lag file's probe does not emit it (disclosed).

### (iii) Integration recovery leg on real scratch PG ✅ PASS
Worker f1cd23e5: integration 2/2 collected, 60.7s; unit twins 2/2, 0.6s. All anchors verified at exact lines (shrink `INIT_RECOVERY_TIMEOUT_S=1.0` :107; production gate `await pool._replenish(PROBE)` :212). Payload: `state_after_recovery='ok'`, `quarantined_after_recovery=False`, `recovered=True`, breaker-open during break (`init_failed`, 3 attempts). **No mock seam:** child runs plain `python -c` (no conftest ⇒ no injected mock modules) and resolves the real SDK (`mcp.client.stdio`); recovery is logically exclusive to the production gate chain (`warmup_pool.py:968` quarantine+can_attempt gate → `:574` claim_attempt → real handshake → breaker closes). Fence: own disposable DB/role dropped clean; livez uptime monotonic (94704s→94761s, same PID); ambient prod poison captured pre-scrub and scrubbed per-variable.

### (iv) F6 rc-capture semantics ✅ UNCHANGED
Worker c53bac5d: the "8 packs" in the brief = F6's **6** + 2 pre-existing already-guarded packs (`scheduled_tasks_acceptance.sh`, `g7_unique_index_smoke_test.sh`) — disclosed. Static census: 4 `|| rc=$?` + 7 prefixed `|| X_RC=$?` sites, ALL init-to-0-before-guard, ALL consumed by explicit equality-vs-expected-constant; zero legacy unguarded sites remain. Dynamic micro-proof (rc 0/1/2/124): guarded form preserves exact rc + continues under `set -euo pipefail`; unguarded control aborts. Real-leg: fail-fast pack PASS 9/9 with inner `gate_soak` rc=1 observed as DATA (pack exits its own verdict). No semantic difference from pre-hardening.

---

## Findings register (non-blocking, routed)

| # | Severity | Finding | Owner |
|---|---|---|---|
| F-1 | 🟠 | 6 lane packs UNREGISTERED in PACKS.md (both copies, 0 hits) | tester (registering now) + developer lane convention |
| F-2 | 🟠 | Acceptance pack declares a 2400s internal watchdog that is NOT implemented (single-layer timeout only) | developer lane (pack hygiene) |
| F-3 | 🟠 | Journal env-snapshot dumps live secrets (OPENAI_API_KEY, SYSTEM_ENCRYPTION_KEY…) into pack stdout → /tmp logs at 644 (pre-existing `upgrade_journal.py:2171` behavior; tester chmod 600'd its own evidence logs) | hygiene lane |
| F-4 | 🟡 | d3 gate in acceptance pack asserts name-presence only (cannot distinguish PASSED vs SKIPPED) — compensated by unit pin + mutation baseline | developer lane |
| F-5 | 🟡 | 8 false-confidence mutation legs + PF-1/PF-2 pack-stub fidelity defects (details Gate 2) — follow-up pin work | developer lane |
| F-6 | 🟡 | B7 burst leg flaky 1/13 at baseline — QUARANTINE.md WATCH entry | developer lane |
| F-7 | 🟡 | Live-binary v2 heal will revert the prod context7 v3 row at next live boot until promotion; operator re-pin window (Gate 4(i) advisory) | leader/user sequencing |
| F-8 | 🟢 | Worktree has a .venv (py3.14) — blueprint "worktrees lack .venv" note stale for this worktree | KB (recorded) |
| F-9 | 🟢 | Orphaned scratch DBs on local PG from earlier/killed runs (pre-date this gate; left untouched — concurrent lanes may own them) | hygiene |

## ensure.md status (scoped to this change set)
- Critical "no regressions in changed packs": the six changed/lane packs ALL PASS at HEAD (my runs). ✅
- Whole-tree net-new-red (release-gate-equivalent for this big/critical change): Gate 3 — HEAD complete / base PARTIAL, comm NOT-PRODUCED (disclosed above; scoped net-new ∅ on change surface as compensation).
- Static checks (dev.sh flag etc.): unaffected surface; migration gate EMPTY (no schema change — nothing further gated by ensure.md Core on this change set).
- Contradiction notices: none (ensure.md methods compatible with pack form).

## Quarantine
1 WATCH entry added (B7 leg, 12P/1F over 13 baseline runs, re-run≥3× protocol) — see QUARANTINE.md.
