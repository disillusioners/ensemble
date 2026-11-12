# Test Gate Report — prompt-assent Phase 1.5 (designated channels, pinned-approver model)

**Date:** 2026-11-12 (UTC)
**Commission:** TEST GATE — every refusal cell re-proven for the new channel surface AND the DM cells unchanged.
**Worktree under test:** `/home/nea/ensemble-src-wt-assent-p15-channels` — branch `feature/assent-designated-channels`
**Exact sha tested:** `569eacdbbca00f80e1d154da9d03e941d6dfca8e` (`569eacdbb`) — the conditional "quality pass" commit never landed; HEAD was exactly the expected sha at every phase (W1 orientation, before/after every mutation cycle, final check).
**Base:** `efa8aaae28153b8e9c49b86abe19e92be5458ebe` (= origin/latest), provisioned as detached worktree `/home/nea/ensemble-src-wt-assent-p15-base` (removed after evidence capture).
**Worker instances:** W1-orient `ff835275` · W2-tier1 `f4d556a9` · W3-lane `f3545a0d` (skill: test-pack-execution) · W4-mutate `f7e60fe0` (skill: test-pack-execution) · W-final (commit/cleanup).

---

## VERDICT: **PASS** — 8/8 gate items proven, 0 net-new reds, 0 weak/missing cells, 7/7 spot-mutations reddened.

Non-blocking disclosures are recorded in §Disclosures; none affect the verdict.

---

## Per-item evidence

### 1. LANE SUITE GREEN — ✅ PASS
All runs from the worktree root, per-variable env scrub (17 vars incl. the `ENSEMBLE_AUTH_*` family — the runner shells carry `POSTGRES_DB=ensemble_prod` / `ENSEMBLE_SELF_ENV=live` ambient poison, so the scrub was genuinely protective), `timeout`-wrapped:

| Run | Target | Result | Counts | Wall |
|---|---|---|---|---|
| R1 | `test/packs/prompt_assent_phase3_unit_test.sh` (Layer-2 internal 300s) | RESULT: PASS, exit 0 | **204 passed / 0 failed** | 31s (25.12s pytest) |
| R2 | `tests/unit/test_prompt_assent_designation_parser.py` | PASS | **59 passed** (≥59 ✓) | 8s |
| R3 | `tests/unit/test_prompt_assent_designated_channel.py` | PASS | **38 passed** (=claimed ✓) | 14s |
| R4 | `tests/unit/tools/test_prompt_assent_registration.py` (census) | PASS | **53 passed** | 9s |

Census pins verified green: taxonomy `== 29` (27 existing + exactly `approver-not-pinned` + `designation-config-invalid`, D-8) · `PHASE2_PLACED_CODES == 8` unchanged · `PHASE1_5_PLACED_CODES == {approver-not-pinned, designation-config-invalid}` with equality + subset-of-taxonomy + referenced-in-module + disjoint-from-parent pins. PACKS.md phase3 row matches reality (204/204, both new files listed); matrix pack row carries the explicit OUT-OF-SCOPE marker and did NOT gain the files. Known freshness flake never fired (0 failures across all runs; retry-recording unnecessary).

### 2. THE 32 CELLS — ✅ PASS (32/32 mapped, named, green; 7/7 spot-mutations RED)
Full walk of `refusal-cell-matrix.md` §2/§3: **20 mint** (M-1…M-19 with M-17a/M-17b) + **12 approve** (A-1…A-12). Every cell has an asserting test that pins its expected outcome (code + token-row presence/absence + ledger/budget shape):
- 19 named new cells in `test_prompt_assent_designated_channel.py` (incl. the M-2 family cell `test_m2_a_dm_mint_by_a_distinct_human_records_that_human` per AC2's amended family rule, and dual-pin cells M-18 + A-12 which ALSO carry census-side pins at `registration.py:633/:977`);
- 10 pre-existing UNCHANGED pin assertions (M-1/M-3/M-4×2/M-5, A-1/A-2/A-3) in the byte-unchanged files;
- A-4 full provenance chain incl. `map_digest`.

**Spot-mutations (each: mutate source → mapped test RED → `git checkout --` restore → GREEN → clean):**

| Mut | Cell | Edit (source, one line) | RED proof |
|---|---|---|---|
| MUT-1 | M-18 | `mint_context.py:175` absent-key gate `is not False` → `is True` | guild turn MINTed; `assert 'reason=mint-requires-private-channel'` failed |
| MUT-2 | M-17a | `prompt_assent.py:1992` DM shield `not context.is_private` inverted (attempt 1 in-classifier hoist did not redden — see disclosure #2; complementary attempt at the load-bearing site) | DM mint flipped to designated ledger/INFO shape (`private=0` + `map_digest` present); `assert 'private=1'` failed; M-10 co-green→red consistent |
| MUT-3+4 | M-15 + M-17b (one edit) | `mint_context.py:168-177` map validation+pin lookup hoisted above GROUP_DM_SHAPE leg | **both** reddened; M-11 co-reddened (3 failed) |
| MUT-5 | A-9 | `prompt_assent.py:2364` 4f split removed (`if False:`) — new codes reach `_record_mismatch` | `_assert_no_burn` → `assert 1 == 0` (attempts moved); A-10 co-reddened |
| MUT-6 | A-11 | `prompt_assent.py:2348-2356` map validated before DM short-circuit honored (attempt 1 service-predicate edit did not redden — disclosure #2; complementary at tool 4f) | DM approve refused `designation-config-invalid`; `assert 'prompt_assent approve — OK'` failed |
| MUT-7 | A-12 | `prompt_assent.py:2364` over-applied split (`or private_code == "token-channel-mismatch"`) | `assert row.attempts == 1, "A-12 keeps the burn (D-17)"` → `0 == 1` — the over-split detector fired |

Structural pins verified present + green: PR-1 resolver-raises (runtime, non-vacuous; + census duplicate), PR-3 map-unset equivalence (byte-equal v0.29.18 envelope), MISMATCH_CODES equality pin (5-code set, disjoint from PHASE1_5 set), and all five named counter-mutations P-2b/P-2c/P-3/P-7/P-8 inside the 38-green file.

### 3. DM CELLS UNCHANGED (4 legs) — ✅ PROVEN
1. **8 DM-path pins green:** mint_gate 7, user_binding 8, group_dm_discriminator 6, kill_switch 22, lane 50, fe_unchanged 9, tool_refusals 7, audit_payload 6 = **115/115 on HEAD** and **115/115 on base** — files `git diff --stat`-empty vs base (byte-unchanged, AC7 mechanical proof).
2. **Map-unset equivalence:** `TestTheMapUnsetEquivalence::test_the_unset_map_is_the_v02918_posture` green — group-DM/absent-discriminator/undesignated-guild envelopes byte-equal `V02918_NOT_PRIVATE_ENVELOPE` + DM ledger shape + explicit-empty≡unset.
3. **FE surface unchanged:** `git diff efa8aaae2..HEAD -- frontend/` = **zero files**; `test_prompt_assent_fe_unchanged.py` 9/9 both sides.
4. **Envelope/ledger/budget identical to base:** identical commands run both sides (below); all 115 DM-path nodes green on base and HEAD with byte-identical sources ⇒ identical behavior at the test level. PR-1 proves by execution (resolver patched to RAISE; DM mint + DM approve + lane-disabled: no raise, identical outcomes).

### 4. AMBIENT SAFETY — ✅ PROVEN
- **Leg A:** pack run WITH `ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS='{"999888777":"112233445566"}'` ambient → **204/204 PASS** (pack line 75 body-level unset neutralizes it).
- **Leg B (discriminating proof, re-proven):** direct pytest on `test_prompt_assent_mint_gate.py` with the ambient map and NO unset → exit 1, **exactly one failure, the predicted node** `TestMintInANonPrivateChannel::test_a_guild_channel_is_refused` (def at :102, assert at :114) — the guild mint SUCCEEDED (live token `CONFIRM-LMXN-RONU`) where REFUSE is pinned.
- **Control:** identical direct run scrubbed → 7/7 green. FAIL-under-ambient + PASS-scrubbed = the unset is **load-bearing**. Conforms; no finding.

### 5. REGRESSION / NET-NEW REDS — ✅ 0 NET-NEW REDS
Tier-1 blast-radius leg per `.agents/tester/skills/blast-radius-leg/` (v1.0.3) + `failed-digest-equality-gate/` (v1.0.2). Two-class declaration (GAP-1):
- **D1 (HEAD-only, must-pass-at-HEAD):** the 2 new files, 97 nodes — 97/97 green.
- **D2 (dual-side digest-carrying):** 21× `tests/unit/test_prompt_assent_*.py` + `tests/unit/tools/test_prompt_assent_registration.py` + `tests/integration/test_prompt_assent_flow.py`. Collect-only slicing proof: base 249 nodes / HEAD 281 D2 nodes; only per-file delta = registration 21→53 (+32, the lane's own census expansion); 249+32=281 ✔.
- Identical commands both sides (shared runner, fence-asserted, 17-var per-variable scrub, `-ra -p no:randomly`, serial): **base 249 passed (35s) · HEAD 378 passed (48s) · 0 failed / 0 error / 0 skipped both sides.**
- **Failed-digest comparison (BARE-ID, `^(FAILED|ERROR)` → sort → sha256):** both digests = the canonical empty-set sha `e3b0c442…`; `cmp` identical; `comm -13` (net-new) = ∅; `comm -23` (fixed) = ∅. **Net-new reds = 0.** Flaky isolation not triggered (freshness node passed all legs). Budget 13 min of 30-min cap.
- Superseded v1 run disclosed: partial AUTH scrub produced 2 identical-both-sides reds (`kill_switch` config-wiring nodes, `auth.enabled=true but missing: ['issuer']` — ambient leak through pydantic-settings); fully-scrubbed v2 is authoritative; the v1 diff was also ∅ (environmental, diff-neutral).

### 6. BURN/NO-BURN PAIR — ✅ COMPLETE (the 4f-split regression detector holds)
One invocation, 8 nodes, 5.36s — **burn half 5 green** (A-5/A-6/A-7/A-8/A-12: `row.attempts == 1 and row.status == "live"` + `_budget_count() == 1`; A-12 carries the D-17 marker) · **no-burn half 3 green** (A-9/A-10 via `_assert_no_burn` `attempts == 0`/live/`_budget_count() == 0`; A-11 spends without burn). MUT-5 and MUT-7 independently redden both halves.

### 7. NO-SCHEMA-CHECK — ✅ PASS
`daemon/migrations/versions/*.sql` count = **128** (expected) · head = `20261110_221500_create_prompt_assent_tokens.sql` (expected) · `git diff efa8aaae2..HEAD -- daemon/migrations/` = **empty**. No 129th migration; `map_digest` lands in INFO line + audit payload only (D-15 control (b) honored).

### 8. MERGED-TREE CHECK — ✅ (final confirmation by W-final commit below)
Worktree clean at the tested sha after ALL legs (W3, W4 post-mutation sweep: full designated_channel re-run 38/38 after mutation churn; `git status --short` empty at every checkpoint). W-final records the post-evidence-commit status in its report; the evidence commit lands ON TOP of the tested sha and touches only this file.

---

## Weak / missing tests found

**None.** Every one of the 32 cells has an asserting test pinning its expected outcome. Three coverage nuances (nuisances, not defects):
1. A-1/A-2/A-3 (pre-existing pins) assert code + durable-counter but not the K-9 budget explicitly — family-covered by A-5…A-8 (`_budget_count()==1`), the user_binding lockout cell, and `test_prompt_assent_budget.py`.
2. A-1's consumed-row assertion rides A-11 (:858) + PR-3 leg 4 (identical DM-positive shape) — family-covered.
3. See disclosure #2 (mutation target depth) — coverage is sound; the nuance documents WHERE the load-bearing DM shield lives.

## Disclosures (non-blocking)

1. **Plan-era line citations have drifted vs HEAD** (expected: the lane moved code after the matrix was written): the 4f split lives at `prompt_assent.py:2338-2370` (matrix cites ~:2018-2036); the service predicate at `prompt_version_service.py:491-535` (cited ~:460-476). Test-file citations match exactly. A plan-doc refresh could fold this.
2. **Structural finding (from MUT-2/MUT-6 attempt-1):** the DM shield is enforced at the TOOL-GATE level (mint `prompt_assent.py:1992` `not context.is_private`; approve 4f `:2348` `context.is_private`) — the classifier's leg-1 DM short-circuit is defense-in-depth unreachable via the gates. A classifier-internal regression cannot flip the DM cells; a tool-gate regression can — and the suite catches that variant (both attempt-2 REDs). INVARIANT-DM is enforced at both layers; PR-1 remains the execution proof.
3. **PACKS.md row measurement sha `11c024685` is reflog-only** (amended away); its tree is byte-equivalent to `569eacdbb` except the PACKS.md row itself (test-inert). Cite `569eacdbb`.
4. `.agents/tester/PACKS.md.new` is a 0-byte stub (unexplained leftover, no conflict, left as-is).
5. W2's superseded v1 run (partial AUTH scrub) — see item 5; environmental, diff-neutral, disclosed for honesty.

## Integrity / fences

Canonical checkout `/home/nea/ensemble-src` never touched (porcelain sha identical before/after W2: `0af069d11af9c766…`). No daemon boots, no process kills, no ports approached, no pushes/branches/KV writes. All mutations reverted in-cycle; tree clean at the single tested sha throughout. Env scrubbing per-variable everywhere (no `env -u` globs). Per-variable scrub list used: POSTGRES_{DB,USER,PASSWORD,HOST,PORT}, DATABASE_URL, ENSEMBLE_SELF_ENV, ENSEMBLE_PROMPT_ASSENT_DESIGNATED_CHANNELS, PYTEST_XDIST_WORKER, ENSEMBLE_AUTH_{ENABLED,ISSUER,DISCOVERY_URL,CLIENT_ID,CLIENT_SECRET,ALLOWED_ORIGINS,EXTERNAL_REDIRECT_BASE}.

## ensure.md status (scoped, Tier 1)

- Core criticals: changed packs PASS (this report) · `dev.sh` `--timeout-graceful-shutdown 10` present (static ✓) · concurrency pack: **not in this lane's blast radius** (no concurrency-surface files changed; standing always-on item unchanged by this lane — flag for the next full-gate cycle if desired).
- Tier doctrine honored: Tier 1 (no core-seam file touched), recipes loaded from deterministic paths, budget 13/30 min.
- Release Gate: **not triggered** (feature lane, not promote-blocking; scope fence B5 — no live promotion in this lane).

## Overall Status

| Gate item | Verdict |
|---|---|
| 1 Lane suite | ✅ PASS |
| 2 The 32 cells + mutations | ✅ PASS (7/7 RED) |
| 3 DM cells unchanged (4 legs) | ✅ PROVEN |
| 4 Ambient safety | ✅ PROVEN |
| 5 Net-new reds | ✅ 0 |
| 6 Burn/no-burn pair | ✅ COMPLETE |
| 7 No-schema | ✅ PASS |
| 8 Merged-tree | ✅ CLEAN @ 569eacdbb |

**TESTING GATE: PASS — the lane is clear for its next phase (leader/ceremony owns what follows; scope fence B5 stands).**
