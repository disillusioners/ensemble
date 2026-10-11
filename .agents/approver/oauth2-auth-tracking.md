# Approver Tracking: oauth2-auth

Plan: OAuth2 Authentication Support (ensemble daemon)
Package: .agents/shared/planning/oauth2-auth/ (11 files, ~4.9k lines)
Mode: Plan Approval (plan-approval skill, 2 section-parallel workers)

## Iteration 001 — REJECTED (2026-10-10)
Workers: A approve-worker-plan-core acd23624-04d5-46d5-8f99-fccdc205c597 (plan-overview/requirements/decisions/architecture-recommendation) -> REJECTED, 3 blocking.
        B approve-worker-exec-phases fb3c6f49-c541-490a-8331-16e85352daf4 (technical-analysis/phase1-6) -> REJECTED, 2 blocking.
Merge: 5 blocking, 0 downgrades, 0 dedup (disjoint doc sets).

### Blocking issues
1. [A1] /vscode protection contradiction — requirements.md SS5.5/SS5.6 (PROTECTED, mounts=2) vs SS6.1 ENSEMBLE_AUTH_VSCODE_PROTECTED default false + FR-21 + OD-7 (loopback unchanged). SS7 consumer table never inventories /vscode -> machine-lockout claim unproven under matrix reading. Fix: align matrix to flag (protected only when flag=true; v1 default loopback unchanged), mounts=1, inventory consumers or mark N/A-loopback.
2. [A2] Default-OFF schema posture self-contradiction — requirements.md FR-2 (MUST NOT initialize auth_* tables) vs D17/NFR-8 (runner auto-applies auth .sql both backends, gating rejected: runner.py:107-113) + AC-S1 (tables empty = existence assumed) + plan-overview SS8 :253-256 self-contradicts in one sentence. Fix: FR-2 -> "tables exist as inert schema per D17, never read/written"; fix overview SS8 sentence.
3. [A3] Stale FR renumber anchors — requirements.md SS1.2 (15 cells), SS2.2 (matrix anchored to FR-14=logout), SS2.3/SS2.5 (health publicness anchored to FR-15=me-endpoint; real: FR-22/FR-22a/FR-23+NFR-3); S5/S10/S13/S14/S15 systematically pre-renumber. Coverage is real in SS3/SS8.1; the traceability layer misdirects. Fix: mechanical sweep re-point SS1.2/SS2.2/SS2.3/SS2.5.
4. [B1] FR-30/NFR-15 readiness sub-field auth_dependency_status has NO owning task in phase1-6 (grep-verified: zero refs to readiness.py / ReadyzResponse / composite in any phase plan; FR-30/NFR-15 anchors absent from all phases and plan-overview). phase5 AC-S3/T5.6 test it -> P5 gate fails as written. TA:989 (=fail) misaligned to FR-30 enum ("ok"|"degraded"|"unconfigured", 200). Fix: add implementation task (P3 wiring or P2/P3 task), align TA:989.
5. [B2] phase2 G2.2 (phase2-plan.md:80-81) infeasible — gate requires migration runner UP/DOWN apply/revert on scratch PG, but runner is deliberately NO-OP on non-SQLite (manager.py:6068, :7545; runner.py:721-726; blueprint invariant "runner is SQLite-only"). As written the gate can never pass, or induces a blueprint violation. Fix: reword to runner apply/revert on scratch SQLite + PG leg via SQLModel create_all on scratch PG with index-parity test (precedent: tests/migration/test_drift_events_index_parity.py). NFR-8's measurement already scopes SQLite correctly — only the phase gate is wrong.

### Notes (non-blocking, merged)
- pyjwt undeclared: exists in uv.lock only transitively via mcp; declare pyjwt[crypto] explicitly in T2.7 (jsonschema-promotion precedent; pin-uv discipline).
- FOR UPDATE SKIP LOCKED (D2.4/T2.3) is PG-only vs D2.2/G2.1/T5.10 single-code-path SQLite promise -> needs dialect guard or portable batched delete (60s sweep tick).
- Router tally drift: T3.8 "28" (correct for api_router.include_router) vs phase4 D4.7 cage grep include_router( = 31 sites vs phase4 SS6 "27" vs overview amendment "31" -> pin the cage's exact match set.
- TA Debt #7 (canary bearer token "MUST") superseded by FR-22/FR-23 public availability endpoint + G5.3 unmodified canaries -> mark superseded in T6.5 sweep so no implementer "fixes" the script.
- TA lifespan-site staleness: manager.py:315-345 -> real pattern at api.py:1544-1555 (phase2 T2.4 Minor #2 already correct); apply same correction to TA.
- requirements SS7.2/SS7.4/AC-32/overview SS8/A6 aggregate count drift (7 vs 8 rows; 5 vs 6/7) — row-level table authoritative; counts cosmetic.
- Seam-count drift: NFR-11 "7 seams"/AC-36 vs SS10 = 20 / SS10.1 = 21; AC-38 skipped number; FR-38 has no AC and is absent from SS8.1 map (violates "every FR maps to >=1 AC").
- SS9.2–9.9 OQ-REQ status lines stale ("Decision required by") though folded DECIDED; apply OQ-REQ-1 RESOLVED banner pattern.
- SS5.2 /docs/oauth2-redirect row self-contradictory (verdict PROTECTED, rationale public-path-set) — moot in v1; reword/mark n/a.
- CSRF residue text (K7, NFR-6 detail, Glossary) still double-submit-only; D8 v1.2 supersedes.
- Stale tallies: overview "37 FRs" vs 38+FR-16a; "1,640 lines" vs 1,750.
- T4.1 bootstrap consumption "metadata OR sentinel row" — pick one (test target must be definite).
- phase1 T1.2 "12-row matrix" vs Size note "13-row".

### Positives recorded (context for iteration 002)
Citation accuracy exceptional (verified against live tree, incl. starlette 0.52.1 build_middleware_stack Mount-traversal proof); default-OFF engineered structurally (conditionally-registered middleware, AC-S1, G2.3, G5.5); health-probe publicness via construction-time frozenset + startup log + canaries; machine lane dual-credential + exhaustive consumer matrix; rollout/rollback coherent; R3 source-forgery correctly scoped out with filed follow-up. All 5 blocking issues are plan-text amendments — no architecture reversal.

Next: iteration 002 upon resubmission after textual fixes.

## Iteration 002 — APPROVED (2026-10-10)
Worker: approve-worker-plan 5997b19b-89dd-4196-9e5c-5c0f9444e45c (plan-approval, single whole-package worker — cross-file consistency configuration) -> APPROVED, 0 blocking.
Merge: 0 blocking, 0 downgrades, 0 dedup.

### Verdict basis (fresh pass — worker never saw iteration-001 history)
- All 5 iteration-001 blockers independently confirmed resolved by the fresh consistency trace: /vscode matrix alignment + consumer census (AMEND v1.3/F3, 12-vs-17 framing reconciled, §7.2 sums verified); FR-2/D17 inert-schema posture chain consistent (NFR-8 -> D17 -> K10 -> phase2 D2.2 -> G2.1/G2.3; §6.1 row removed, require_sqlite_retraction retracted everywhere); FR anchors re-pointed (FR-22 chain traced unbroken: §5.1 matrix -> D11 -> D18 -> phase3 D3.3/T3.5 -> AC-22 -> AC-S3 -> NFR-3 -> SC-4); FR-30 readiness field owned (AC-36/37/38 NEW v1.3/F6; FR-38 now mapped); phase2 G2.2 rewritten feasible (scratch SQLite UP/DOWN + PG SQLModel create_all + index-parity test, precedent test_drift_events_index_parity.py).
- Default-OFF zero-disruption structural (middleware conditionally registered, not no-op); health-probe publicness structural (public_path_set checked first; 5 public routes incl. maintenance canary — K3 closure D1+D18); 17-consumer matrix (8 unchanged / 7 config-only / 2 N/A); R3 source-forgery correctly scoped out with filed follow-up.
- PLAN-ONLY constraint satisfied (specifies implementation + verification; does not implement).

### Notes carried (non-blocking)
- requirements.md trailing duplicate tail (~lines 1798-1836) — cosmetic cleanup before dispatch handoff.
- Confirm-at-implementation items (plan-overview §12): D17 empty-SQL stub behavior; SPA Accept-header behavior; SSE mid-stream revocation posture.
- Unverified by plan pass (implementation slice-1 duties): host framework pins (Starlette 0.52.1 / FastAPI 0.133.1), consumer-inventory re-grep, D17 empty-UP/DOWN runner acceptance.

Final: APPROVED — plan ready for dispatch. Residual: R3 source-forgery follow-up ticket stays cross-linked (jobs_crud.py:595, messages.py:690).

## Delta cycle v1.4 (module-lanes) — Iteration D001 — APPROVED (2026-10-10)

Commission: leader-directed delta-conformance + consistency check of the v1.4 amendment (marker `AMEND v1.4 / module-lanes`, user ruling on module auth lanes). Fresh cycle, 2 section-parallel workers, cold-context prompts (ruling + target matrix + per-worker check areas only; no iteration/rejection history passed).

Workers: A approve-worker-v14-core 519630fe-c7db-4091-858d-e15794ce2842 (plan-overview/decisions/requirements/technical-analysis; checks A+B-core+D) -> APPROVED, 0 blocking.
        B approve-worker-v14-phases e592c7ae-dee2-4f95-9bf6-596143630bfd (phase3/5/6 + runbook phase; checks B-phases+C+D) -> APPROVED, 0 blocking.
Merge: 0 blocking, 0 downgrades, 1 dedup (phase4 T4.5 stale cross-ref flagged by both), 1 related-pair grouping (census phrasing A-n1 + B-n4).

### Verdict basis (cross-worker coverage)
- Ruling clause 1 (modules beyond OAuth2, own UI/key mgmt): MODULE-LANE class semantically distinct, invisible to OAuth2 in BOTH auth states; FR-42/AC-41 ON/OFF byte-equivalence; runbook T6.1 "OAuth2 does NOT govern module endpoints".
- Clause 2 (HARD lane isolation): D20 four layers (separate stores D20a / token namespaces D20b / middleware classification D20c / rejection semantics D20d) + AC-40 table-driven negative matrix; T5.11(a) all 4 negative legs explicit (webhook x MCP-KB, MCP-KB x webhook, both x general surface, OAuth2 cred x webhook = never); T3.10/D3.3 dispatch order places module-lane BEFORE bearer/cookie (pass-through untouched). Gate G5.6.
- Clause 3 (OAuth2 protects token-mgmt CRUD only): /api/sources* (webhook secrets) + NEW /api/mcp/kb/keys (T3.12) under OAuth2 human lane; zero OAuth2 config delta recorded (REQ SS6.6: no new ENSEMBLE_AUTH_* / AuthConfig field / SS6.3 row).
- MCP-KB no-key-concept confirm-at-implementation treatment judged SOUND by both workers: NF-1/NF-2 NOT-FOUND facts + ML-4 honest disclosure (kb_server.py:214-223 footprint), T3.11 minimal-build mechanism, T3.12 CRUD family, runbook operator note on current fully-open posture.
- Consistency: matrix censuses match target matrix (PUBLIC 12->11, row 17 flip + row 18 add, SS7.2 10+6+2=18 with 0-consumers-locked-out restated SS7.7); S-20 seam supersession explicit; D19/D20 <-> FR-39..42 <-> AC-40..42 <-> SS7 rows 17-18 <-> phase3/5/6 <-> runbook chain traced by the union of the two artifact sets.
- No regression: default-OFF structural (D3.2 conditional registration); /livez+/readyz public by construction (D18 derivation, regression cage); SPA zero-change (T3.4/NFR-18); 0 machine clients locked out; plan-only (12 .md incl. .bak, zero code files — verified by inspection).

### Notes carried (non-blocking, merged/deduped)
1. phase4-plan.md T4.5 stale dual-credential text — out of v1.4 dispatch scope, self-flagged at phase6:51; clean T6.1 Minor #12 parenthetical + phase4 T4.5 in same PR. [A+B merged]
2. Census phrasing hygiene: (a) SS5.6 PUBLIC=11 verbal subgrouping vs SS5.1 six families (requirements.md:787 vs :710-716); (b) module-lane "2 families" vs "3 paths" (plan-overview:118 vs phase3:88). Row inventories authoritative; counts consistent under each reading.
3. D18 retains 2-class wording; three-class extension lives in D4 v1.4 AMEND (decisions.md ~:400 vs :113) — content recorded, wrong header; amend D18 itself at pre-implementation sweep.
4. plan-overview:12 "TA untouched" contradicts 4 AMEND v1.4 markers in technical-analysis.md (all SUPERSEDED annotations, no rewrite).
5. T5.11(a) MCP-KB negative legs fixture-stubbed until T3.11 confirmation — stub must exercise lane-classification rejection routing (OAuth2 cred never reaches mount auth), not just absence-of-mechanism. Implementer attention.
6. T3.10/D3.3 dispatch order (module-lane between public and bearer) is load-bearing for isolation; must move with any future reorder.
7. Confirm-at-implementation residuals deliberately deferred + disclosed: webhook_secret masking (ML-3, SS12-4c, AC-42 round-trip test conditional); /api/mcp/kb/keys shape/scope name; module key-check mechanism location.
8. Pre-existing stylistic (not v1.4): FR-22 route list redundancy (requirements.md:388); {source_id} vs {source_name} (SS5.0:700 vs SS5.4:750).
Plus carried from iter 002: requirements.md trailing duplicate tail; R3 source-forgery follow-up ticket stays cross-linked.

Final: APPROVED — v1.4 delta conforms to the user ruling, internally consistent, no regression of approved core properties. Ready for dispatch; implementer notes 1-7 above.
