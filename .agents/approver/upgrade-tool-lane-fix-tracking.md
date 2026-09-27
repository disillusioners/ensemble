# Tracking: upgrade-tool-lane-fix (v0.15.3 Upgrade Tool-Lane Live Promote Fix)

Plan set: .agents/shared/planning/upgrade-tool-lane-fix/ (plan.md 358L primary; plan-overview.md 71L; phase1-plan.md 543L; phase2-plan.md ~491L; architecture-recommendation.md 330L)
Repo: /home/nea/ensemble-src @ feature/upgrade-tool-lane-fix

## Iteration 001 — 2026-09-26 — VERDICT: APPROVED

Mode: Plan Approval. Large-plan exception invoked (multi-phase, multi-module, >500 lines) — 3 section-partitioned workers, fan-in via todo_graph (all nodes done).

| Worker | Instance | Skill | Scope | Verdict | Blocking | Notes |
|--------|----------|-------|-------|---------|----------|-------|
| approve-tool-lane-p2 | 8110d95d-4210-4a67-8299-d3056faff63e | plan-approval | phase2-plan.md | APPROVED | 0 | 4 |
| approve-tool-lane-p1 | e0a171d1-01ac-422f-8f09-933c343d4a61 | plan-approval | phase1-plan.md | APPROVED | 0 | 8 |
| approve-tool-lane-master | 2ffb67bc-604a-40ec-87dd-4175f249c744 | plan-approval | plan.md + plan-overview.md + architecture-recommendation.md | APPROVED | 0 | 6 |

Aggregation: 0 blocking across all scopes → APPROVED. No downgrades exercised; no upgrades (prohibited); no new blocking introduced. Independent repo spot-verification by all three workers (line anchors, dataclass fields, test pins, BSD invariants, ADR numbering rule) — no skill-degradation signals; all reports carried skill format + skill_feedback contract.

Deduped non-blocking notes (most specific variant kept):
1. Stale cross-refs in plan.md (cosmetic drift, phase plans edited post-plan.md): Artifact Map "phase2-plan.md (469 lines)" at plan.md:19 (actual ~491; plan-overview.md already says 490/491); §9.1 cites phase1-plan.md:169-171 → actual :182-184; §8a cites phase1-plan.md:443 → actual :375. Implementers navigate by section; fix opportunistically.
2. Open Question — preflight refusal-token taxonomy: plan default mints 3 new tokens; architecture-recommendation §4b CORRECTs to 2 new + reuse existing `executor-scripts-unavailable` (upgrade_tools.py:2656-2663 already covers check #1; smaller surface). Caller-resolvable at implementation dispatch.
3. Implementation micro-additions: `errors="replace"` on the 4KB log-tail read (arch-rec M-7); boot-sweep lifespan site located via JobLockSweepService registration (api.py:845-868 — plan names placement constraints, not the callback); ServicesConfig `ge=60` field confirmed by symmetry not direct cite.
4. plan-overview.md residual-ops-gate clarity: add one sentence stating tool-lane live promote remains non-armable from the agent until FL-23 bundling + 3 fresh ari cycles + user-executed-only attestation are independently satisfied (currently implicit).
5. P1∥P2 loose coupling awareness: ADR-035's audit claim leans on P1 preserving the existing `ENSEMBLE_UPGRADE_SCRIPTS_DIR` + `--f2-verified-closed` argv/env chain; if P1's implementation diverges, the ADR weakens (plan's own framing accepts this).
6. Fresh-boot instance-match check (R-P1-3): no arms on first boot → no false positive (absence of pending_op); spell out at implementation.
7. ADR-034 max-claim technicality (transparency only): ADR-033/034 are bullet-style standing rulings at decisions.md:242-243, not `## ADR-0NN` sections; minting 035 after max=034 is correct per the minting rule at :226 (counts ADR numbers, not section style).
8. Open-question defaults carry worker endorsements: refusal-sub-token family (p1 N1), rename+grep+lockstep (p1 N2), 90s sweep interval w/ operator knob (p1 N3), lifespan placement (p1 N4/N8 sequencing refinement).

Unverified by design (workers, fenced): live-path behavior (no live touches this mission; 53ca row assumed inert — operator reboot instruction recorded in plan-overview.md Key Decisions #4); runbook §8.4/§9 + watchdog anchors trusted-not-inspected; concurrent phase-plan remediation edits out of master scope.

Prior rejections compared: none — first iteration.
