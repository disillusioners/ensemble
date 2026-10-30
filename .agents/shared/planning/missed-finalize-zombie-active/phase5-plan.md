# Phase 5 — House Gates + Stage (STAGE-ONLY)

## 1. Objective

Run the lane through the house gate ladder (code council → fix cycles → tidier → tester
functional verification), then giter-stage the release with the version RE-DERIVED at lane end
(expected `v0.27.16`), the migration-delta-EMPTY rollback adjudication, and staged verification —
**STAGE-ONLY: no promote, no nonce ceremony, no live contact.**

Design references: D-5 (migration delta EMPTY), D-6 (default posture disclosure at ceremony),
lane envelope (plan-overview §9).

## 2. Prerequisites

Phase 4 gate verdict `net_new=∅` + RESULTS doc; branch tip carrying code + packs + evidence;
standing FINALIZE discipline pre-check: fetch + verify `origin/latest` unmoved from the branch
base (`22fe83317`) — if moved, STOP and report per house MERGE/FINALIZE rules.

## 3. Decisions

| ID | Decision | Rationale (cite) | Rejected alternatives |
|----|----------|------------------|------------------------|
| PD-5.1 | Gate ladder order: code council (governor review → fix cycles until 0 blocking) → tidier pass → tester functional verification (independent, consumes the Phase-3 packs + Phase-4 RESULTS) → staged verification | House lane pattern (recent lanes: council cycles → tidier → tester functional → tester staged) | Skipping tidier (cheap pass, historically catches HIGH items); reordering tester before council (wasted verification on unfixed code) |
| PD-5.2 | Stage version RE-DERIVED at lane end per the standing rule: next free version strictly above all tags + `releases/` dirs at stage time — `v0.27.16` is EXPECTED (verified free at lane setup) but the stage-time census wins | Standing version-adjudication rule (multiple 2026-10 lanes raced slots; the census is authoritative) | Trusting the setup-time reservation (stale by stage time) |
| PD-5.3 | Rollback-safe adjudication = delta-empty-legitimate: migration delta EMPTY (expected 115==115 vs base) ⇒ `ENSEMBLE_ROLLBACK_SAFE=1 source=explicit` with the standing-rule reasoning recorded at stage; PLUS the shipped-binary schema-object check (new binary introduces no schema objects the old lacks — belt from the v0.27.13 posture) | v0.27.13/v0.27.14 precedent class ("delta-empty-legitimate ⇒ =1"); D-5 keeps the delta empty BY DESIGN | Reflex =0 (the standing rule explicitly retires reflex adjudication) |
| PD-5.4 | NO promote, NO nonce consumption, NO live daemon contact, NO daemon restart; `current` symlink + live `state.json` promote fields untouched (fence asserted pre/post stage); staging worktree KEPT with both provenance sidecars; branch pushed as the audit ref; tag policy per the CURRENT lane convention at stage time (f13a4f59e push-tags precedent — the giter decides per convention) | The user's 3-factor nonce ceremony owns the flip (standing policy); v0.25.2 provenance-gap avoided by keeping sidecars | Any scripted/live flip (out of the lane's authority entirely) |

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|------|--------------------|---------------------|------------|---------------|----|
| T5.1 | Code council submission (governor review) with the design-doc set + diff; fold blocking findings through fix cycles until 0 blocking; adjudicate advisories (fold vs disclose vs route) | Council verdict APPROVE (0 blocking) with disposition record | council evidence (lane docs) | P4 | R1-R6 (all surface here) | AC-5.1 |
| T5.2 | Tidier pass over the lane diff; fold findings (0 deferred among fold items; deferrals disclosed) | Tidier PASS record | lane docs | T5.1 | — | AC-5.2 |
| T5.3 | Tester functional verification (independent lane): consumes Phase-3 packs + Phase-4 RESULTS; re-runs the pack set + mutation-matrix spot verification + fixture end-to-end at the branch tip | Tester verdict (PASS / PASS-WITH-NOTES) with RESULTS doc | `.agents/tester/RESULTS/` | T5.2 | — | AC-5.3 |
| T5.4 | Migration-delta + schema-surface re-verification at the branch tip: `daemon/migrations/versions/` delta EMPTY (115==115 or the census-proven count), zero `*.sql` added/changed, no new SQLModel table anywhere in the lane diff; record as the D-5 invariant proof | Delta proof recorded (feeds PD-5.3) | repo diff evidence | T5.3 | — | AC-5.4 |
| T5.5 | Giter stage: version re-derivation census (PD-5.2) → merge `--no-ff` to `latest` per house MERGE discipline (clean worktree = commit point; PACKS.md keep-both on conflict) → bump (EXACTLY the 4-file house set; manual 8f47661fe semantics if bump tooling dies off-PATH — disclose) → tag per current convention → `stage.sh` with the v-prefixed VERSION (ADR-009 D3 guard) → push order per convention | Staged release dir + manifest + both provenance sidecars; 4-way sha match (disk==dist==manifest==sidecar); rollback adjudication journaled | `releases/v0.27.16` (expected) + staging worktree | T5.4 | — | AC-5.5 |
| T5.6 | Staged verification (tester): artifact integrity (sha re-hash, sidecars `git_head`/`git_dirty`), source-less payload hygiene, prompt-artifact baseline, ISOLATED boot smoke (scratch root derivation, per-variable env scrub, cgroup isolation vs the live daemon; banner == staged version; livez/readyz 200; the (h) knob trio visible in boot log; SIGTERM-only teardown), LIVE FENCE asserted (current symlink + state.json sha + live :9797 untouched pre/mid/post) | STAGED-VERIFIED verdict (PASS / PASS-WITH-NOTES) + RESULTS doc | `.agents/tester/RESULTS/` | T5.5 | R5 | AC-5.6 |
| T5.7 | Ceremony-readiness pack: promote command (env-first `ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.27.16 …` — the re-derived version), the D-6 posture disclosure (automated terminalizing writer default-ON; kill-switch env documented), and any stage-time disclosures handed to Phase 6's report | Ceremony notes recorded (NO ceremony performed) | lane docs | T5.6 | — | AC-5.7 |

## 5. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC-5.1 | Council APPROVE with 0 blocking; dispositions recorded |
| AC-5.2 | Tidier PASS (deferrals disclosed) |
| AC-5.3 | Tester functional verdict recorded with RESULTS |
| AC-5.4 | Migration delta EMPTY proven at the branch tip; no schema surface anywhere in the lane diff |
| AC-5.5 | Staged release with 4-way sha match + rollback adjudication journaled + version census recorded |
| AC-5.6 | STAGED-VERIFIED verdict; isolated boot smoke green; LIVE FENCE intact (zero live contact) |
| AC-5.7 | Ceremony-readiness notes delivered; NO promote/nonce/live contact performed |

## 6. Verification method

Each gate's verdict artifact (council record, tidier record, tester RESULTS ×2, stage manifest +
sidecars). The live fence is asserted mechanically (symlink target + `state.json` sha + live
health check read-only) at three points: pre-stage, post-stage, post-smoke.

## 7. Risks & notes

- If `origin/latest` has moved by stage time: STOP per FINALIZE discipline — report the new
  tip, commit distance, merge-base, overlapping files; the leader adjudicates rebase-vs-merge.
  Recent lanes repeatedly hit concurrent-lane drift; expect it.
- Retention: `releases/` over-retention is machinery-owned — do NOT hand-evict; protect the
  live symlink target + pending-ceremony candidate.
- The stage-time tag push policy follows the CURRENT convention (the giter reads the convention
  at stage time — recent precedent f13a4f59e pushes tags HEAD:latest first; older lanes kept
  them local-only). Record whichever was applied and why.
