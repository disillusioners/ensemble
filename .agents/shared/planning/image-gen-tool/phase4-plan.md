# Phase 4: Test/E2E Matrix + Live Lanes + Seal Re-Pin (Tested Step)

## Objective

Prove the whole commission: a complete unit/integration/e2e matrix; a dev.sh-driven e2e
that produces ≥1 LIVE real image delivered natively through a chat source (Lane A: local
dev proxy with real MiniMax upstream), with Lane B (live proxy) gated on the user's
model-row registration and executed skip-with-disclosure if unavailable; total image
spend ≤5 images and ~$0.05 cap across ALL lanes, script-enforced; seal re-pin verified
as an executed, tested step.

## Environment Contract

- Boot via `dev.sh` (port 8079; env-fence requires `ENSEMBLE_SELF_ENV` — the worktree
  `.env` is already fenced by giter per shared-meta `wt.active.feature/image-gen-tool`).
- `E2E_BASE_URL` convention (`tests/e2e/test_e2e_workflows.py:91`).
- `MockSourceAdapter` first-class (`tests/e2e/mock_source_server.py:70`/:8099 — captures
  sends + `emit()` inbound).
- Test-fencing (CRITICAL, standing convention from the 2026-10-12 live-install deletion
  incident): test scripts NEVER resolve live-shaped paths (HOME-derived install dirs) —
  mktemp FAKE_HOME + escape guard in every script that touches HOME-shaped paths.
- TypeScript gate: `cd frontend && ./node_modules/.bin/tsc --noEmit` (never bare
  tsc/npx outside frontend/).
- In-process template: real TmpImageStore + real ResponseDispatcher, LLM/proxy stubbed
  (the chart e2e pattern).

## Lane Design (D6)

**Lane A — dev-proxy real-upstream e2e (UNBLOCKED, runs in this commission):**
- Local dev proxy at `localhost:4123` with its OWN LOCAL dev store
  (`~/.config/llm-supervisor-proxy/config.db`, currently empty).
- Seed the store with an image-gen model row (local-only; id `minimax-image-01` local
  equivalent).
- Credential: REUSE the minimax key staged in the proxy repo `.env-minimax` — NEVER mint
  a new key (commission prohibition).
- Real MiniMax upstream: n=1 latency ~17s documented; budget allows one n=2 probe.
- IMAGE_GEN_BASE_URL/IMAGE_GEN_API_KEY for the e2e run point at the dev proxy (env-scoped
  to the dev.sh run, not committed as values).

**Lane B — live-proxy e2e (USER-GATED):**
- Live proxy needs an image-gen model row registered via OAuth'd FE UI (suggested id
  `minimax-image-01`, kind `image-gen`, internal=true, internal_model=`image-01`, bound
  to the minimax credential) — USER-GATED, report-only for this commission.
- At test time: probe availability; if unregistered → SKIP-WITH-DISCLOSURE (recorded in
  the e2e results file as an explicit skipped lane with reason). If registered → run the
  same script with `--lane live`; live proxy 120s default deadline documented
  (`image_gen_timeout` knob; raising it is user-gated).

**Budget enforcement (DoD, script-level):** a lane-runner script counts every successful
generation across ALL lanes and hard-stops at 5 images total; expected spend ≈ n ×
$0.0035 ≤ ~$0.05. The counter is asserted in the lane script itself, not left to
discipline.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Unit matrix consolidation: P1 tool tests (`tests/unit/tools/test_image_gen_tool.py`), registry tests (category resolution, 6/6 grant, critic-NOT-granted, frozen-names), P2 dispatcher truth-tables + byte-compat + audit call-sites, P3 FE specs | P1-P3 | Full unit suite green; pytest markers integration/postgres respected (`pyproject.toml:67,86-95`) |
| 2 | Integration lane: in-process harness — MockSourceAdapter + real TmpImageStore + real ResponseDispatcher + stubbed image-gen HTTP (MockTransport): agent-turn emits marker → both seams deliver native attachment → blob deleted post-delivery → `/views/tmp-images/<id>` serves bytes (200, correct content-type) | P1-P3 | Harness test green; mirrors chart e2e structure |
| 3 | Lane A script (`test/packs/image_gen_lane_a.sh` + indexed entry in `.agents/tester/PACKS.md`): dev.sh boot → seed local dev-proxy store → run e2e scenario (agent calls generate_image n=1 → image arrives as native attachment in MockSourceAdapter capture + view_url serves) → optional single n=2 probe if budget remains | 1-2 | ≥1 live real image delivered natively; ≤5 images TOTAL counter enforced in-script; no key minting (env-minimax reused); FAKE_HOME fencing in script |
| 4 | Lane B gated script (`--lane live`): probe live-proxy model row → run or SKIP-WITH-DISCLOSURE with reason recorded in results | 3 | Script exits 0 on skip with disclosure artifact; runs real live e2e only when user-gate open |
| 5 | Slack files:write workspace-grant verification at smoke time: live Slack leg (or explicitly mocked grant probe) verifies `files_upload_v2` succeeds; if workspace grant missing → disclosed text-only degradation (graceful path `adapters/slack/adapter.py:638-676`) | 3 | Smoke result recorded (granted / degraded-with-disclosure) |
| 6 | Seal re-pin as TESTED step: after P1+P2 land their edits, run the full ceremony end-to-end — regen `.prompt-artifacts.sha256`, refresh 2 SHAs in `SEALED_SHA_BASELINES`, 2 history entries, then run `test_phase_d_does_not_modify_sealed_artifacts` + full chart corpus as the acceptance gate; record evidence in the results file | P1 task6 + P2 task7 | Seal test green; history entries present with evidence pointers; chart corpus green UNMODIFIED |

## Test Matrix (req#6 — every file + what it asserts)

**Unit:**
- `tests/unit/tools/test_image_gen_tool.py` — env fail-closed
  (`image_gen_not_configured` envelope, no network attempt); arg validation (prompt
  ≤1500; aspect/width-height mutex; pair-only; 512-2048 ÷8; n≤4 clamp/reject); wire
  shape via `httpx.MockTransport` (mock INSIDE the wrapper under test — canonical
  `tests/unit/test_llm_failover_v2.py:326-478`): POST `<base>/image_generation`, request
  JSON (`model`, `prompt`, sizing), identity headers, SINGLE attempt (10-RPM no-retry);
  response parsing truth-tables: single-object `data`, STRING counts coerced,
  `base_resp.status_code==0` sole success gate, ANY non-zero `base_resp.status_code` →
  error envelope (fail-closed, no code whitelisting `[consequential fix of AM1, with
  AM2]`), proxy 502 = upstream body VERBATIM → parse `base_resp.status_code`/
  `status_msg` from the 502 body, proxy 504 = OpenAI envelope → parse `error.message`
  `[consequential fix of AM1]`, partial success (`failed_count>0` + status 0 → fewer
  URLs + disclosure); OSS fetch immediacy + per-image timeout
  + 15MB guard; `image_save` provenance `feature="image-gen"` + retention normal;
  response payload shape (`ok`, `images[]` incl. `view_url`, `markers[]`, `usage_note`);
  wait_for wall-clock cap (hanging transport → timeout envelope).
- `tests/unit/tools/test_frozen_tool_name_discovery.py` — `generate_image` in
  KNOWN_TOOL_NAMES post-regen.
- Registry/allowlist spec — category `image-gen` resolves; exactly 6 agents granted;
  critic NOT granted; meta.json files parse.
- `tests/test_sources_dispatcher.py` — `TestExtractImageGenMarkersUnit` truth-table
  clone (+ ALL existing chart suites unmodified green).
- Byte-compat guard fixtures — chart outputs identical pre/post.
- `tests/test_chart_image_delivery_audit.py` — ≥2 family call sites pinned.
- FE specs — constants regex; scan/inject; lightbox click; fallback SVG;
  chart-render non-regression; live-view-chip non-regression.

**Integration:**
- In-process dispatcher harness (MockSourceAdapter + real stores): both-seam family
  delivery, delete-after-delivery, API/no-colon skip, `/views/tmp-images/<id>` serving.

**E2E:**
- Lane A dev-proxy real-upstream (native attachment + view URL + optional n=2 within
  budget).
- Lane B live-proxy (gated / skip-with-disclosure).
- Slack grant smoke.
- Seal ceremony run + chart corpus acceptance.

**Packs:** `test/packs/image_gen_lane_a.sh` (+ optional `lane_b`) indexed in
`.agents/tester/PACKS.md`.

## Files Touched (complete list)

- `tests/unit/tools/test_image_gen_tool.py` — **NEW**
- `tests/unit/tools/test_frozen_tool_name_discovery.py` — regen fixture (from P1)
- `tests/test_sources_dispatcher.py`, `tests/test_chart_image_delivery_audit.py`,
  `tests/test_chart_image_delivery_e2e.py` (history + SHAs) — from P2 + this phase
- Integration harness test file — **NEW** under `tests/`
- FE spec files — from P3
- `test/packs/image_gen_lane_a.sh` (+ lane_b) — **NEW**; `.agents/tester/PACKS.md` index
  entry
- Evidence/results file under `.agents/tester/RESULTS/` (dated, referenced by seal
  history entries)

## Coupling

- **Fan-in of P1+P2+P3** — every DoD above consumes prior-phase artifacts.
- **Tight with dev.sh environment** — fenced boot, port 8079, E2E_BASE_URL.

## Risks (phase-specific)

- Lane A dev-proxy store seeding drift (wrong model row shape) → 400s from proxy.
  Mitigation: seed script asserts proxy `/health`/model-list before e2e; documented row
  shape (proxy parses only `model`; per-token allowed_models gate — empty=all).
- Budget counter bypass (manual runs outside the lane script). Mitigation: counter is
  the acceptance gate; results file records per-image count; commission report states
  total.
- Lane B unavailability discovered late. Mitigation: probe runs FIRST in the lane
  script; skip path is a first-class exit (0) with disclosure, not a failure.
- Seal ceremony executed against a drifted tree. Mitigation: Task 6 runs the diff audit
  + full corpus as its own acceptance; any drift → STOP before SHA refresh.

## Definition of Done (per-phase DoD)

1. Full unit matrix green (all files named above).
2. Integration harness green (both seams, delete-after-delivery, serving).
3. Lane A e2e green via dev.sh: ≥1 LIVE real image delivered NATIVELY through a chat
   source + view_url serves; ≤5 images TOTAL across all lanes; ~$0.05 cap enforced
   in-script.
4. Lane B resolved: either green, or skip-with-disclosure artifact recorded (user-gate
   status reported).
5. Slack grant smoke recorded (granted / disclosed degradation).
6. Seal ceremony executed and TESTED: seal test green, 2 history entries with evidence
   pointers, chart corpus green UNMODIFIED, `.prompt-artifacts.sha256` consistent.
7. `tsc --noEmit` clean (frontend-local binary only).
8. Results file written under `.agents/tester/RESULTS/` and referenced by both seal
   history entries.

## Seal-Trip Subsection

P4 itself touches no pinned source files, but it OWNS the verification of the two
re-pins from P1 (`agents/developer[v2]/tools_note.md`) and P2
(`daemon/sources/dispatcher.py`) — the seal test run is an explicit DoD item (Task 6),
with the evidence pointer wiring the results file into the seal history entries.

## Exit Criterion

The commission's full DoD chain is green: tool + dispatcher + FE proven, live real
image delivered within budget, seal integrity re-established and tested. Ready for
review (council) — NO live promote (user's 3-factor nonce ceremony).
