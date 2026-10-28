# Soak-Gate + MCP-Warmup — Independent Functional Gate (2026-10-28)

**Commission:** functional gate for `feature/soak-gate-mcp-warmup` (promote soak-gate fix + MCP warmup degradation fix)
**Worktree:** `/home/nea/ensemble-src-wt-soak-gate` · HEAD `0f2d854af85b779cb67e349aeec13a0fc809a66c` · base `d5a4e4ad92d19f3265b97bc29c5aa3943f026ed2` (54 commits, brief said ~30 — disclosed)
**Tester:** independent gate lane; every number below is the TESTER'S OWN worker runs, never the developer lanes' evidence.
**Plan authority:** `.agents/shared/planning/soak-gate-mcp-warmup/requirements.md` §4.1–§4.7 (normative), §5 ACs; `decisions.md` §10 rulings.
**Migration gate:** `git diff --name-only d5a4e4ad9..0f2d854af -- daemon/migrations/versions/` = **EMPTY** (verified against git objects by recon worker 5568fa40).

---

## VERDICT

**Commission: COMPLETE — Gates 1, 2, 3, 4 all delivered (Gate 3's late base-digest report arrived post-termination and was adjudicated in full).**
**Staging recommendation: GO-WITH-DISCLOSURES.** The defect is proven closed (both directions, my runs); the safety property proven intact (both revs incl. real-promote auto-rollback); all 21 documented mutation legs independently RED-verified; all four Gate-4 obligations met (Gate 4(i) staging blocker: VERIFIED — no operator value lost, env-preserving heal proven). Gate 3 whole-tree comm delivered: 9 net-new default reds / 0 integration → **adjudicated to ZERO behavior regressions** (8 stale pre-existing test assertions on a deliberate architect-ratified contract widening + 1 false-positive static pin with resolution alive at the new seam, mutation-verified). The 8 stale nodes + static re-pin are a test-side-only remediation package the developer lane must fold (they are still red at branch tip `19fc11c8b` and will redden future whole-tree/postgres gates). This gate is anchored at `0f2d854af`; the branch has since advanced (v0.27.11 bump) — coverage beyond the anchor is not claimed.

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

**BASE side: COMPLETE (worker 590fd172 — report delivered post-termination; ~8.5h wall).** Full coverage: **1336/1336 default files** (chunk-union == collection list, verified) across 14 chunk-runs; **74/74 integration files** across 4 chunk-runs. Base digests: `/tmp/g3base/failed_default.digest` **324 lines** (sha256 `234cd043973093d31f3340022369b583f32c2c1a97f3f12fb0b5c5c2094b8d53`), `failed_integration.digest` **33 lines** (sha256 `52113e9444fba6b5492bdd6c65f70502685d6af25a535d017298bb85806be052`). Totals — default: 30,043 passed / 324 failed / 296 skipped / 25 errors / 5 xfailed (30,693 collected, 1,019 deselected; >10,000 assert PASS); integration: 445/33/57 (561 collected; >5 assert PASS). Non-zero collection asserted on both lanes.

**comm comparison (delivered):**
- `comm -13 base head` default = **9 net-new reds**; integration = **0** (empty).
- `comm -23` (base-only / fixed) = 11 default, 2 integration.
- **3× isolated adjudication of the 9:** **7 deterministic HEAD regressions** (3/3 fail at HEAD, 3/3 pass at base) + 2 chunk-order flakes (3/3 pass at HEAD in isolation — not regressions).
  - **6× `tests/test_readiness_checkpoint_saver.py`** — HEAD's `make_checkpoint_saver_probe` (readiness.py) widened return `bool` → `tuple[bool, Optional[str]]`; the UNCHANGED pre-existing test asserts `is True`. Test file sha256 identical both legs ⇒ **test-contract drift on the lane's own surface** (adjudication in flight: worker c7233375).
  - **1× `test_secret_do_not_touch_lane_static`** — static pin counts `resolve_secret_markers` call sites in `daemon/mcp/connection_manager.py`: expected ≥2, found 0. Decisive question — did marker resolution MOVE to the rewritten spawn seam (stdio_wrapper/warmup_pool/init_guard) or is it GONE? Runtime behavior-pin run in flight (worker c7233375). **ADJUDICATED (worker c7233375): ALIVE-AT-NEW-SEAM — false-positive static pin.** Resolution moved behind a NEW shared helper `resolve_spawn_env` (`warmup_pool.py:121`, body calls `resolve_secret_markers` @ `:149`); all 3 effective spawn sites (connection_manager cold ×2 + warmup_pool) unchanged, now calling the helper; the AST name-count pin cannot follow the indirection. Runtime proof: behavior pin `tests/unit/test_mcp_secret_spawn.py` (10 nodes: warm pool + both cold spawns, plaintext→StdioServerParameters, fail-closed envelope, stored-config invariant, audit) **10/10 GREEN at BOTH revs**; mutation probe neutering `resolve_spawn_env` ⇒ 4/10 RED — the pin is load-bearing. **Remedy (routed, NOT applied): re-pin the static guard to the new seam; NEVER re-add direct `resolve_secret_markers` calls in connection_manager (double-resolve).**

### Net-new adjudication (worker c7233375) — ZERO behavior regressions

| Red | Class | Proof |
|---|---|---|
| 6× `test_readiness_checkpoint_saver.py` | **TEST-CONTRACT DRIFT** (stale pre-existing assertions) | `assert (True, None) is True`; `make_checkpoint_saver_probe`/`make_db_probe` widened `bool` → `tuple[bool, Optional[str]]` (deliberate, architect-ratified — failure-class taxonomy, decisions.md A1.2/A1.6). Production tuple-aware: `daemon/api.py:3940/3941/3958` → `_unpack` handles legacy bool AND tuple; `git grep "probe() is"` over daemon/ = zero bare-bool sites. Lane's own new pin asserts the new contract (`test_saver_probe_contention_class.py`). Test file untouched since before base. Dual-leg: BASE 20P / HEAD 6F+14P, identical sets ±SELF_ENV |
| +2 HIDDEN siblings `tests/postgres/test_readiness_pg.py` | same drift, **invisible to net-new** (postgres marker deselected by addopts) | scratch-PG proof: BASE 8P/1E / HEAD 2F+6P/1E (the 1 error is a both-legs scratch artifact) |
| 1× secrets static pin | **FALSE-POSITIVE** (resolution relocated) | see above |
| 2× order flakes | not regressions | 3/3 pass at HEAD in isolation |

**Remediation package (test-side only, zero daemon-code delta; NOT applied by this gate — developer lane owns):** update the 8 stale assertions to the tuple contract + re-pin the static guard to `resolve_spawn_env` (keep BOTH static and behavior guards — static still covers the do-not-touch invariants). **⚠️ Still unfixed at branch tip `19fc11c8b`** (diff on all three affected test files empty vs 0f2d854af) — they will redden any future whole-tree/postgres gate until folded.

**Base-side disclosures:** recipe adaptation was mandatory (same 4 adaptations as HEAD + `--timeout=120`; the unadapted recipe hangs async tests AND `os._exit(1)`-without-summary silently truncates digests — disclosed for reproducibility); 2 base files isolated as chunk-hangs (`test_attestation_attest_first_e2e.py`, `test_attestation_bound_escalation.py` — both pass standalone rc=0 at base; neither appears in the net-new set); chunk_00/02 split into reconstructible halves; base flake-band of 5 perf-sensitive lines between two independent full base run-sets — **the net-new set is identical across both**, verdict stable; 2 tracked files dirtied BY THE TESTS THEMSELVES (KMS audit + p0-gate-evidence appends) were backed up to `/tmp/g3base/sideeffect-backup/` and `git checkout --` restored; final worktree status = 10 untracked harness files only, zero tracked modifications; zero fence incidents (no prod contact, no protected ports, live daemon uptime-continuous).

**⚠️ HEAD-tip drift disclosure:** the HEAD digest was harvested at 03:37Z @ `0f2d854af`; the feature branch has since advanced (worker observed `19fc11c8b`, ~27 shipped-tree commits — post-gate lane activity). **This gate's verdict is anchored at `0f2d854af`**; the leader must re-derive coverage for commits beyond it.

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
| F-10 | 🟠 | **8 stale readiness-probe test nodes** (6 visible + 2 hidden postgres-marked siblings) asserting the OLD bare-bool contract — net-new reds, zero behavior impact; still unfixed at tip `19fc11c8b` | developer lane (test-side assertions) |
| F-11 | 🟠 | **Secrets static pin counts the OLD seam** (`EXPECTED_SPAWN_SITES` connection_manager×2) — resolution alive behind `resolve_spawn_env`; re-pin to the new seam, NEVER re-add direct calls (double-resolve) | developer lane (test-side re-pin) |
| F-12 | 🟡 | Branch tip advanced past the gate anchor: `19fc11c8b` ("bump 0.27.11", 13 commits incl. FC pins `8bfdfea9d`, this gate's evidence `c38771c33`, origin/latest merge). Gate anchored at `0f2d854af`; coverage beyond not claimed | leader (re-derive for the new tip) |

## ensure.md status (scoped to this change set)
- Critical "no regressions in changed packs": the six changed/lane packs ALL PASS at HEAD (my runs). ✅
- Whole-tree net-new-red (release-gate-equivalent for this big/critical change): Gate 3 — HEAD complete / base PARTIAL, comm NOT-PRODUCED (disclosed above; scoped net-new ∅ on change surface as compensation).
- Static checks (dev.sh flag etc.): unaffected surface; migration gate EMPTY (no schema change — nothing further gated by ensure.md Core on this change set).
- Contradiction notices: none (ensure.md methods compatible with pack form).

## Quarantine
1 WATCH entry added (B7 leg, 12P/1F over 13 baseline runs, re-run≥3× protocol) — see QUARANTINE.md.
