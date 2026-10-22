# Approver Tracking — mcps-gateway-interop

Plan: MCPS Gateway Cross-Implementation Interop Proof (Phase D1)
Artifact: .agents/shared/planning/mcps-gateway-interop/plan.md (574 lines; research notes in same dir)
Branch: feature/mcps-gateway-interop (plan base 9632badd4; P0 commits fcc0f86ad + acdac8158 landed mid-review)

## Iteration 001 — 2026-10-22 — VERDICT: REJECTED

Workers (skill: plan-approval, section-parallel, cold-context):
- approve-worker-plan-core (ccb14f28-e008-4ff8-9f9b-0449bd7dd3f5) → REJECTED, 1 blocking
- approve-worker-plan-riders (ba8bddb3-8450-4337-a08e-0f43d91a18ee) → REJECTED, 2 blocking (1 duplicate of core's)

Both workers verified load-bearing claims against repo source (daemon, FE, tests, docs, Go client repo, SPEC.md). Anchor accuracy otherwise extraordinary (~90 line-exact anchors each).

### Blocking issues (aggregated, deduped: 3 raw → 2)
1. **D3c / Revision-1 B4 defect statement inverted** (plan.md:277, plan.md:538).
   Claim: `MemoryAuditSink.list_recent()` rows carry `timestamp`-shaped keys and NO `created_at` ("applying timestamp-from-created_at at both read sites would project every fake row to timestamp=None").
   Source truth: `daemon/services/mcps_gateway/audit.py:160` emits `"created_at": record.created_at`; memory rows lack **`id`**, not `created_at` (research-read-fe-riders.md:82-92 agrees: VERBATIM record.created_at, id absent, "" when unset). Flagged independently by BOTH workers.
   Required fix: restate defect as id-absence + empty-vs-synthetic stamp divergence + double-projection hazard; the normative contract (one shape-agnostic idempotent helper, two read sites, both-sinks equality acceptance test) is correct and unchanged.
2. **D4/B8d config-surface pin unsatisfiable** (plan.md:307, :542 vs B8a :308 and S2 :314/:321).
   Mandated unit test asserts kit's config surface is EXACTLY the five CONFORMANCE_* pointing keys, but B8a defines CONFORMANCE_BEGIN_HOOK/_CMD/_URL and S2 defines CONFORMANCE_STRICT_CLOSE_CODE → kit reads >=8 CONFORMANCE_* names → test red on day one or silently weakened.
   Required fix: pin the five *pointing* keys explicitly and state where the begin-hook and strict-close-code keys are pinned.

### Notes (non-blocking, carried for iteration 002)
- W4 test counts wrong (plan:399, Rev1 :553): unit files collect 27+9+53=89 (not 19+15+21=55); package = 34 at base -> 35 after P0. Def-site variant 24/9/53=86. Gate instruction unaffected (strictly MORE tests exist than claimed).
- Plan stale vs landed P0: plan creates interop/{tls_harness,go_client,test_real_pair}.py but P0 already landed tls_harness.py/go_client.py/p0_gate.py/test_p0_gate.py at tests/integration/mcps_gateway/ package root -> two tls_harness modules coexist unless plan states move-or-adopt. Also P0 already executed: p0-gate-evidence.md VERDICT: PASS (4 green runs, real WSS 56ms, real PG audit row) — R-1 empirically retired; §3 P0 row should be marked executed.
- Import-time env/DB resolution concern (riders worker, flagged to D1 scope; not independently confirmed by core worker): fake-client suite and interop test are both pytest.mark.integration -> share one pytest process under -m integration, may defeat D1 step 1 "patch env before daemon.api import" (p0_gate.py documents the trap; P0 solved it with a separate process). Plan should state the process-isolation mechanism.
- D4 touched-files missing pyproject.toml: new `conformance` marker required (only integration/postgres/no_xdist/slow registered at pyproject.toml:88-93).
- run.sh command plan:310 uses `python -m pytest` vs house rule scripts/run_tests_scrubbed.sh:95 (`.venv/bin/pytest`) — use uv run python -m pytest from worktree root.
- §8 boundary wording plan:524 names tls_harness.py as kit-sanctioned but it belongs to D1's interop lane; effective rule at plan:352 (no daemon.* import in kit) is correct — tighten wording.
- dev.sh citation drift (plan:126, :448/R-17b): set -a; source .env at dev.sh:74-75 (not :58-63); export PORT at :141. Substance verified (clobber trap real, .env:57).
- Assertions 17-19 dual-listed in P1 and P3 gate lists (plan:380, :382) — add an ownership clause.
- docs/mcps-gateway.md "re-read at call time" claim appears at BOTH :165-166 and :184-186 — D5 sweep should cover both.
- Minor citation drift: service.py:165 vs :167 (config snapshot line); substance correct.

Next: iteration 002 on revised plan (2 localized blocking fixes + note sweep). Max iterations: 3.

## Iteration 002 — 2026-10-22 — VERDICT: APPROVED

Workers (skill: plan-approval, section-parallel, cold-context; plan.md now 602 lines, Rev-2 changelog at :587):
- approve-worker-interop-core (2d5477c1-2bc1-43d0-aed3-f95e9b85ad05) → APPROVED, 0 blocking, 4 notes
- approve-worker-riders-kit (484fe5aa-e7bc-46ab-b39c-c718f4ccdbce) → APPROVED, 0 blocking, 6 notes

Both workers verified load-bearing claims against repo source (~60 + ~40 line-anchored citations across daemon/, tests/, frontend/, Go client repo, SPEC.md); test counts reproduced via --collect-only (unit 27+9+53=89; package 35=15+19+1).

### Iteration-001 blockers — both verified resolved by cold-context workers
1. D3c defect-statement inversion → FIXED. Worker B: Rev-2 restatement matches audit.py:160 verbatim (memory rows lack id, not created_at); read-path pins + storage-shape pin + write-path exclusion rationale all verified.
2. D4/B8d config-surface pin → FIXED. Worker B: 5 pointing keys + 2 begin-hook + 1 strict knob = 8, pinned as two assertions; internally consistent and satisfiable; conformance marker genuinely absent today; websockets>=13.0 + pytest-timeout declared.

### Notes (non-blocking, residual for implementer)
- AD6 vs D3c phrasing: state the helper installs at the router (shape-agnostic over two row sources); a literal AD6 reading (inside MemoryAuditSink.list_recent) would KeyError pin #12.
- Kit frame-size row is a CLIENT-side MUST (transport.go:63-90); don't build an unsatisfiable center-side red.
- D3c unset-stamp acceptance must insert the row directly (bypass record()'s now_utc_iso() substitution at repository.py:56) or stamps silently differ.
- §2/§8 seam statement: committed p0_gate.py also imports daemon.* (:303-304,658) — scope the "only tls_harness.py" rule to D1-new files or name p0_gate.py sanctioned.
- DRY_RUN/DESTRUCTIVE are no-op-pending-knob; protection rests on job_queue==0 + scoped assertions — don't present as effective gates.
- Stale evidence count: §2/§3 say "4 green runs"; current log holds 8 PASS + 4 FAIL (append-only §0) — refresh when convenient.
- Minor: §8.4 "add" should read "extend" (section exists); heartbeat ≥3× pin should cite protocol.go:266-278 (client.go:651-665 is the adopt-with-warning site); new projection test would create a second home for gateway unit tests (tests/unit/services/ vs tests/unit/test_mcps_gateway_service.py) — conscious choice; api.py module-level app constructed twice (:4836/:4839) — S3 single-engine assertion covers.
- "uv run python -m pytest" invocation sound (scrubbed wrapper uses .venv/bin/pytest) but not found as a formally documented house rule.

Final: APPROVED at iteration 002. Prior-carried 001 notes verified addressed (counts, P0 adoption at package root, process isolation, pyproject marker, ownership split 14+8+4, dev.sh anchors, both doc-claim sites) or absorbed above.
