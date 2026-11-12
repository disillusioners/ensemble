# ENSEMBLE-31 "sonic team v1" — Tester Gate Report (Phase 2 + Phase 3)

- **Date:** 2026-11-12/13 (gate executed 21:46Z–23:0xZ UTC)
- **Tester gate owner:** tester agent (dispatcher); execution via 7 worker instances (prep-A/B/C/D, gate-1/2/3) + statics worker
- **Tree:** branch `feature/sonic-team-v1`, tip `e66e34264` (docs-only stage-truth commit); **build content commit `64211ff22`** (31 files); base `ac9849bf7` (= origin/latest at plan time). Worktree porcelain CLEAN before and after every gate leg (AC-P2-5 / AC-P3-4).
- **VERDICT: ✅ PASS — Phase 4 (staging) UNBLOCKED.**

## 1. Phase-2 gate — pack + AC legs + base-red adjudication

### 1.1 Sonic pack (G1 / AC-P2-1/2, A29)
`timeout 300 bash test/packs/sonic_agent_defs_unit_test.sh` at committed tree → **`RESULT: PASS`, exit 0, inner 12 s** (run twice: identical PASS — second run was the RAM-guard contingency re-run, clean).
Layers: static **A1–A11 all PASS** · self-test **11 GREEN / 22 RED** (all 22 leg ids hit expected assertion ids; leg list in `/tmp/ensemble-sonic-dryrun/evidence/prepA/pack-run.log` + `/tmp/gate1/pack-head.log`) · real-resolver **PASS** (`A2-real` leader spawns exactly the 4, coder deny-by-default; `A3-real` high-risk spawn negatives denied) · terminology pins **23 passed**.
PACKS.md registration: `grep -c` = 2; row at `:4869`, heading `:4865` (committed).

### 1.2 Non-vacuity (counter-mutation) proof — INDEPENDENT
- Prep (dirty tree, content == `64211ff22`): 5 real-file legs m1–m5 in a /tmp copy → all hit expected ids (`FAIL A4 … llm_model=coding`, `FAIL A6 missing handoff header "## What is UNTESTED"`, `FAIL A8 rule.md plane count=2`, `FAIL A7 blast block drift … (13/14, first diff daemon/routers/** vs scripts/**)`, `FAIL A11 Ari note heading/anchor missing`).
- Gate (committed bytes, fresh muttree snapshot `750f5f709`): s1 → `FAIL A4 sonic-reviewer.llm_model=coding`; s2 → `FAIL A7 … (13/14 …)` + secondary `FAIL A8 baseline row stale` (cross-proof). Post-restore baseline PASS again.
- Wiring proof: self-test and repo-run consume the SAME `ASSERTIONS` tuple (`sonic_agent_defs_checks.py:614–626`, resolved at `:899–901`); A11 matcher whitespace-normalizes both sides (`:218–220`, load-bearing for the line-wrapping anchor).
- **Zero vacuity findings.**

### 1.3 AC legs at HEAD (GATE-2; logs `/tmp/ensemble-sonic-dryrun/evidence/phase2-gate/`)
| Leg | Verdict | Evidence |
|---|---|---|
| L1 diff scope (A9/A27/A28) | PASS | `daemon/`+`scripts/` = 0; migrations = 0; `agents/` = exactly ari/rule.md + .prompt-artifacts.sha256 + 20 sonic files; no steady-team paths. Full manifest (44 files incl. plan set) all inside {agents/, tests/, test/packs/, .agents/} |
| L2 baseline (A14/A15) | PASS | `--check` rc=0 "197 … match"; count **197** |
| L3 seal (A17/A18) | PASS | 35 rows / 0 mismatches; `agents/ari/rule.md` = `8d6ca1642a34…515f9` (re-pinned as designed); scoped pytest 4 passed |
| L4 green suite (A21) | PASS | **116 passed / 27 skipped / 0 failed** (terminology 23 included) |
| L5 A22 adjudication | PASS (disclosure) | **5 failed at HEAD; ZERO new reds vs base; NONE sonic/ari-attributed.** Raw parametrize ids renumber ([workflow.md13]→[workflow.md16] etc.) because 4 sonic `.md` entered collection — failure-text maps 1:1 to the same 5 base conditions (charter/rule fence ×1, charter/workflow fences ×41, developer + developer[v2] bare-md ×2 each, install-mermaid-cli bare-md ×1). **Lesson recorded: gate manifests must compare file+condition, never parametrize-suffix ids.** Adjudication table: `phase2-gate/L5-adjudication.md` |
| L6 A23 duty pack | PASS | **4 failed; ids == 4-canary set: YES; count pins GREEN (50/48/197): YES** (7f/12p at base → 4f/15p at HEAD; base count-pin reds 46v45 / 44v43 / 181v177 all cured by registration) |
| L7 A20a duty block ×4 | PASS | `endswith(DUTY_BLOCK)` 4/4 TRUE (DUTY_BLOCK sha `da18f1f9cf2c…`, byte-identical to canonical) |
| L8 registration constants (A23–A26) | PASS | duty 50/48/197 · pok grant 50/49 · anomaly grant 50/48 · aegis `EXPECTED_BASELINE_ROWS`=197 (scoped `-k baseline` 12 passed) · snapshot pinned set == `[developer, developer[v2], sonic-reviewer, sonic-tester, tester]` (test passed) |
| L9 porcelain | PASS (external note) | Worktree EMPTY before; after = the concurrent leader docs-commit `e66e34264` (foreign, attributed, content-invariant); canonical fingerprint identical |

### 1.4 Base-red adjudication artifact (advisory #4 — PINNED; this file is that artifact)
**Base evidence (independently measured, pristine scratch worktree @ `ac9849bf7`, not trusted from the developer report):** `/tmp/ensemble-sonic-dryrun/base-red-manifest.md` + 3 logs.
- **A22 family — 5 pre-existing base reds (base-attributed, ride the gate):**
  1. `test_no_bare_md_filename_tokens_in_prompts[workflow.md13]` — agents/developer/workflow.md (2 bare tokens)
  2. `…[workflow.md20]` — agents/developer[v2]/workflow.md (2 bare tokens)
  3. `…[install-mermaid-cli.md]` — agents/charter/skills-template (1 bare token)
  4. `test_odd_fence_count_fails[rule.md5]` — agents/charter/rule.md (1 fence @L94)
  5. `test_odd_fence_count_fails[workflow.md5]` — agents/charter/workflow.md (41 fences)
  (id suffixes shown at base; they renumber at HEAD — compare by file+condition per §1.3 L5.)
- **Duty pack — exactly 4 canary-rooted reds (by design, D3/R16/A23):**
  `TestTheDutyBlockIsByteIdenticalEverywhere::test_every_duty_file_carries_the_byte_identical_block` · `::test_the_duty_block_is_the_last_top_level_section` · `TestTheForbiddenTokenClosure::test_no_duty_file_carries_a_forbidden_token_inside_its_duty_block` · `TestTheDutyAddsNoCardinalEntry::test_each_duty_file_keeps_its_cardinal_count`
  Root cause (proven at base): `agents/canary/rule.md` lacks the `## Reporting Anomalies` duty block (`grep -c` = 0). Base set was 7 reds = these 4 + 3 stale count pins (now green via registration). **QUARANTINE.md row deliberately NOT added** — deterministic base defect with a single documented root cause, not a flaky test; tracked here and by the plan.
- **Terminology pins base-green:** 23 passed at base → any tip red would have been sonic-caused; none occurred.

### 1.5 ensure.md status (Tier-1 adjudication)
- **Tier:** 1 (feature-lane default; core seams untouched — 0 `daemon/`/`scripts/` lines; Release-Gate judgment trigger NOT satisfied by additive prompt content). Tier-1 shape executed: scoped dual-side FAILED-set diff over the declared blast radius, identical commands both legs, red-at-base ≠ net-new, one diff-node flake isolated 3×.
- **Core statics:** `dev.sh` carries `--timeout-graceful-shutdown 10` (dev.sh:159). Changed-pack PASS = §1.1. Concurrency pack NOT in the change set (zero daemon code) — scoped out per blast radius.
- **Quarantine-aware:** QUARANTINE.md active rows intersect NONE of tonight's scoped runs; the sonic pack carries no quarantine deselects.
- **Known flake recorded (follow-up, not a gate red):** `tests/unit/agents/test_aegis_security_family.py::TestFinalRoundSignalTermination::…[SIGTERM-143-bash]` — 1 fail in full-file run, isolation ×3 green; daemon/ untouched by this branch ⇒ not diff-causable.
- **Recipe-load disclosure:** the Tier-1 recipe body (`.agents/tester/skills/blast-radius-leg/`) was not formally loaded; the executed shape matches the tier contract (scoped, dual-side, budget-far-below-cap, fail-loud none needed). Compensating pin for this change's surface: the live Phase-3 dry-run (real runtime, real agents) — stronger than a mock E2E for a prompt-content change.

## 2. Phase-3 dry-run E2E (single permitted run) — PASS
Full report: `/tmp/ensemble-sonic-dryrun/evidence/DRYRUN-REPORT.md` (verbatim copy below condensed).
- **Run:** 22:29:00Z→22:45:26Z (16m26s of 180-min wall). **V0–V8 ALL PASS; zero F-class failures; single run (no retry — F1 carve-out unused).**
- V0 lane probe before dispatch (environ count=1, non-echoing; fingerprint in `evidence/redaction-summary.txt` only) · V2 HANDOFF 7/7 exact headers · V3 reviewer `REVIEW: PASS` · V4 sonic-tester scoped run `1 passed` · V5 real giter commit `0b2db327e` on `dryrun/sonic-team-e2e-20261112` (HANDOFF.md +44 / tiny_feature.py a−b→a+b exactly) · V6 dryrun-verify `RESULT: ALL PASS`, live pid 2147839 + demo pid 3547188 unchanged/alive, commission porcelain clean pre+post at `e66e34264` · V7 FD-ISOLATION clean ×3, zero sweep-kill lines, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned · V8 teardown residue-free (SIGTERM exact pid, ports freed, DB dropped count 0, claim removed, worktree+branch removed, scratch-llm.env shredded).
- **Pipeline audits:** 5 instances exactly (leader + coder/reviewer/tester/giter); **ZERO kb-writer spawns, ZERO `experience()` calls, ZERO `invoke_agent_and_wait`**; **one-wave topology proven** (reviewer 22:37:09.25 + tester 22:37:10.63 — 1.4 s apart); dispatch→commit ≈ 4.5 min vs 78-min p50 model.
- **Deviations (disclosed, tester-authorized):** D1a seed commit + D9a teardown removal by driver (not giter lane) — throwaway fixture branch, wall-clock protection; feature branch untouched. D8 NOT simulated — real scratch giter committed.
- **Hygiene:** AC-P3-3 pin greps all satisfied; scrub (pin l) 0 hits outside the sanctioned redaction line; no raw credential material anywhere in the evidence bundle.

## 3. Observations & follow-ups (non-blocking)
1. A22 parametrize-id renumbering lesson (§1.3 L5) — future gate manifests compare file+condition.
2. Aegis SIGTERM-timing flake — candidate for QUARANTINE.md by the owning lane if it recurs.
3. Pre-existing noise (base, untouched): `maintenancer` deny-entry `git_commit` warning during real-resolver import; langchain-core py3.14 deprecation warning.
4. RAM-guard ambient telemetry (`ram_breach_observed` on trivial commands, refusals only, zero actual kills across all 7 workers) — matches the v0.29.21 staged-guard profile; two anomalies filed (report_ids `45aabc68`, `56aba5ac`).
5. PACKS.md `:4869` row already carries the committed ✅ PASS status; no further row edit made (keeps the tree clean for staging).

## 4. Verdict
**Phase-2 gate PASS + Phase-3 dry-run PASS ⇒ Phase 4 (staging, promote-ready, NO promote) UNBLOCKED** per the plan's stage gate. All evidence under `/tmp/ensemble-sonic-dryrun/` (index: `prep/INDEX.md`; `evidence/` for gate + dry-run artifacts). Committing this file is the giter lane's act.

— tester (Test Leader), ENSEMBLE-31 gate lane
