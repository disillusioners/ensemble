# Clipboard-Image-Chat — FINAL MERGE GATE REPORT

- **Date:** 2026-09-19/20 (gate window 22:19Z → ~23:5xZ)
- **Branch:** `feature/clipboard-image-chat`, merge-base `307db932` (= chat-source-live-injection merge)
- **Pinned HEAD @ start:** `dc1a91733cb4ed92e85e978d38095d2bcc1e00ec` @ 22:24:03Z (Wave-0). N1 drift
  `dc1a9173 → b59bc58e` = ONE docs-only commit (docstring prose in `tmp_image_message_hook.py`;
  hunk-verified by every dispatch's pin gate). Gate-added TEST-ONLY commits: `792d9826`
  (kwarg-rot fix, +6) and `887c98f5` (M2-closure real-dispatch facade test, +141/−3).
  **Final gate HEAD: `887c98f5`.**
- **Gate executor:** tester (dispatcher) + 23 worker instances. All runs `uv run python -m pytest`
  or registered pack scripts; dual-layer timeouts throughout; live boots on disposable PG14.
- **Worktree state disclosed:** unstaged `M .agents/tidier/notes.md` (foreign tidier actor,
  untouched); tracked 0-byte relic `.agents/tester/PACKS.md.new` (pre-existing, untouched).

## VERDICT

**PASS WITH ONE HIGH DEFECT DISCLOSED (D1).** The ORIGINAL promise is verified live end-to-end.
One plan-#40 sub-expectation (mid-turn 202 leg) is not met by a pre-existing lane limitation the
branch makes deterministic — full characterization + fix directions below and in
LESSONS/2026-09-19-clipboard-gate-202-injection-stranding.md. Merge disposition = leader's call;
nothing else blocks.

### ORIGINAL-PROMISE verdict (the close-against-THIS test)

| Promise clause | Live evidence | Verdict |
|---|---|---|
| Paste → daemon tmp file (30-day) | Blob `{32hex}` + `.json` sidecar (sha256 `cc0839ff…`, GET byte-identical); cleanup service `interval=3600s retention=30d` boot line | ✅ |
| Image-reader converts to TEXT | Child `image-reader` instance, 1 VISION call, description text threaded into main HumanMessage | ✅ |
| Chat agent receives TEXT ONLY (never vision-routed) | **0 `Invoking LLM (VISION)` for the main instance across ALL scenarios** (paste, both reload legs, 4× concurrency, revive probe); STANDARD-only; assistant: "I'm relaying the Image Reader agents' analysis rather than viewing the image directly" | ✅ |
| Text persists as the message | GET /messages: conversion text in content; NO `data:image/` anywhere; refs in wire `images` via union | ✅ (idle/durable + multi-superstep legs) |
| Thumbnail in message list | `img[data-testid=message-image] src=/api/tmp_images/<32hex>` through 4199 proxy, naturalWidth>0, persists after reload | ✅ |
| Click → popup viewer; Esc closes | dialog opens/closes (screenshots 05/06) | ✅ |
| Legacy data-URI untouched | leg (c): main agent VISION-routed as designed, reply reads image content | ✅ |

## A. Full regression (pinned HEAD)

### A1 Backend census — 14 partition packs, ~21,238 P / 193 F / 51 E / ~259 S (+5 xfail)

| Partition | Result | F/E | Adjudication |
|---|---|---|---|
| unit_smaller_subdirs_routers | **PASS** | 0/0 | 830P — includes the feature's unit/routers suites |
| unit_tools | **PASS** | 0/0 | 2691P |
| chat_source_integration | FAIL→**PASS post-quarantine** | 1F→0F | saturation A2.2 flake, monitoring trigger fired, 1F/2P budget → quarantined; re-run 58P/0F |
| job_queue | FAIL | 7F | 4 caller-stated pre-existing + 3 watcher_events 'settled' drift — base-A/B @307db932 proven PRE-EXISTING |
| opencode_e2e | FAIL | 4F | all verbatim QUARANTINE rows |
| top_level_a_h | FAIL | 24F/2E | all pre-existing (2E identified: jsonb_migration setup, base-stable; base leg 21F+2E; perf ×5 = xdist flake, 12/12 serial) |
| top_level_i_q | FAIL | 60F | 59 pre-existing + **1 branch-caused kwarg rot → FIXED `792d9826`** |
| top_level_r_z_misc | FAIL | 15F | pre-existing (14 stable + 1 rotating context-flake; worker disclosed one benign log-capture re-run) |
| unit_loose_a_d | FAIL | 10F/21E | llm-stream-stall 74-node set |
| unit_loose_e_l | FAIL | 22F | pre-existing; gaia trio base-proven |
| unit_loose_m_r | FAIL | 5F | pre-existing singles |
| unit_loose_s_z | FAIL | 5F/2E | pre-existing |
| unit_services | FAIL | 8F | pre-existing (b1_wc env-defect + proxy_phase1 ×7) |
| integration | **wrapper-TIMEOUT 300s** (self-completed 313.97s, full data) | 32F/26E | 27F+26E pre-existing; 5 node-level new-suspects → 3 base-proven pre-existing, 2 partition-context flakes (quarantined) |

**Net branch-caused reds: 1** (kwarg rot, fixed). **All other census reds pre-existing**
(QUARANTINE families or base-A/B proven at `307db932` in detached worktree
`/tmp/ens-gate-base-307db932`). New-suspect ledger: 16 surfaced → 13 pre-existing, 1 fixed,
2 quarantined. `g7_unique_index_smoke_test` is NOT collected by any partition (out of census
scope by construction — its collection INTERNALERROR remains a known pre-existing item).

### A2 FE

- **Full jest (ad-hoc pack `fe_jest_full_clipgate_test.sh`, registered):** 3,381 P / **3 F** —
  single suite `jobs-grouping.model.spec.ts`, all 3 = timeAgo relative-vs-absolute boundary with
  hard-coded fixture `2026-09-10` vs run-date 09-19/20. **By-construction pre-existing**
  (model + spec + formatter byte-identical `307db932..HEAD`). Wall-clock fixture decay —
  test-debt follow-up (dynamic fixture date), not branch-caused.
- **tsc + build (registered pack):** PASS — 0 tsc errors; build 13.25s; warnings **10/10 known
  identities**, numeric deltas disclosed (jobs.scss +5.90kB prior-lineage; chat-interface
  +0.53kB).

## B. LIVE end-to-end (worktree daemon :8090, disposable PG :15433 `ensemble_gate`, FE :4199 via variant proxy; env-variant boots :8091/:15434 and :8092/:15435)

- **Boot:** HEALTHY — `Creating PostgreSQL engine …15433/ensemble_gate`, 4 reconciler anchors,
  tmp-image + cleanup boot lines, zero Traceback/CRITICAL. PG-side legacy-column backfill noise
  (non-fatal) disclosed. **Task 8 vision posture:** `.env` `OPENAI_MODEL_VISION=vision` (alias,
  proxy-mapped) + boot line `[Graph] Vision model configured: vision` @05:44:20 + LLM-HA line.
- **B4 Paste E2E (Playwright, synthetic ClipboardEvent): 14/14 PASS** — chip → send → upload
  (batch-of-1 JSON contract) → blob+sidecar → child conversion (VISION ×1 legit) → **main agent
  STANDARD, 0 VISION** → text persists, no data-URI leak → thumbnail → popup → Esc.
  Note: upload is deferred to send (chip shows `idle`) — plan nuance, not a defect.
- **B5 #40 reload smoke:** leg (b) idle/durable **PASS** (200 → wake → union `images` → reload
  render naturalWidth=320). leg (c) legacy data-URI **PASS** (main VISION-routed by design).
  **leg (a) mid-turn 202: FAIL → defect D1** (below).
- **B6 Fail-fast:** **PASS** — vision-unset boot proven (`[Graph] No vision model configured`);
  ruled cases live: #1 image_refs→**400** INVALID_REQUEST (34ms), #4 legacy images→**400**
  byte-identical envelope, #5 both-channels→**422** XOR; #2/#3 are vision-SET mock paths frozen
  as unit tests (green via census). Daemon health 200 post-rejections; control 200 proves
  vision-gating not broken routing.
- **B7 Retention:** **PASS** — fresh preserved across 3 sweeps; expired (sidecar 40h) reaped
  next tick (blob+sidecar, log `[TmpImages] reaped 1 image(s) older than 1d`, freed_bytes=248);
  404 + gated `cleanup` block. **R10 floor = pydantic FAIL-FAST `ge=1` at config load** (not a
  silent clamp — stronger than plan wording; unit-pinned). FE onerror: spec leg 37/37 (fallback
  swap, idempotency, cross-seam invariants) + live 404 transcript.
- **B9 Concurrency N=4:** **PASS** — 4/4 converted (4 child VISION calls same second), 4/4
  text-only (VISION∩STANDARD id-sets = ∅), zero cross-wiring (per-image codes correct),
  starvation/error sweep clean (0 hits incl. 5xx/Traceback), max completion 77.35s vs 240s bound.

## C. Mock-quality audit + mutation checks

- **M1 (serializer union disabled):** 4 F3a tests bite ✅ (tautology flags: file-wide grep pin
  region + dedup test — hardening follow-ups).
- **M2 (facade drops `image_refs` forwarding in `enqueue_message_job`):** 🚨 **CRITICAL ESCAPE
  FOUND** — both real-dispatch integration tests stayed GREEN (their bodies drive the sibling
  `enqueue_message` facade; docstrings claimed the job chain; the production POST /messages
  route uses the job facade). Unit spies did bite (5 nodes) but the documented class is that
  unit mocks can stay green at this seam. **ESCAPE CLOSED:** `887c98f5` adds
  `TestImageRefsJobFacadeChain` (real InstanceManager→service→row, dual-column assert, omitted-
  kwarg default) — bite-proven (mutation FAIL `image_refs: None` → revert GREEN 17/17 + 6/6
  pack-flag). Discovery: F1 file carries NO `integration` marker → `-m integration` deselects it
  (registration nuance, disclosed).
- **M3 (FE merge naive-append):** 25/44 bite incl. all images pins + #40 wire-shapes ✅.

## ensure.md

- **Core: 5/5 PASS** (concurrency pack 98P/74S/0F canonical parity incl. thread-identity
  coverage; dev.sh :102 graceful-shutdown grep; 8/8 await sites verified; no dead code).
  Zero contradictions vs pack/timeout rules.
- **Release Gate:** full-suite item satisfied by the census (via packs, quarantine-aware).
  The 4 E2E-workflow items (require `./dev.sh`@8079 + real LLM, one-by-one) are **deferred with
  notice** — release-time gates per their prerequisites; this feature gate's live scenario suite
  (B4–B9) provides the live coverage for THIS change.

## Defects

| # | Sev | Defect | Repro / disposition |
|---|---|---|---|
| D1 | 🔴 HIGH | **Mid-turn 202 `image_refs` injection strands** on single-superstep turns: never lands in that turn's checkpoint; delivered only on NEXT send (unbounded latency, 14min observed) with **refs dropped on the recovery path** (wire `images: None` — h4-S1 union violated there); **permanent loss if daemon restarts while stranded** (RAM FIFO). Pre-existing lane limitation (plain-text 202s strand identically — proven), made **deterministic** by the branch's ~7s sync-in-POST conversion; branch-caused refs-drop in D2 leftover builder. Multi-superstep turns deliver mid-turn with refs intact (proven). | Repro: RUNNING single-superstep target + POST image_refs → 202 → drain → GET /messages absent; `/injection` pending:true. Fixes characterized (3 options) in LESSONS; #40 202-leg expectation unmet — leader disposition required. |
| D2 | 🟠 MEDIUM | FE deep-link mount flake: fresh loads of `/projects/:p/instances/:id` mount an inert stub 7/12 times; heals after 1–2 reloads. Degrades reload-smoke UX. Attribution unproven (app-shell routing; FE suites green) — likely pre-existing; quantify+fix follow-up. | probe7 + re-quantification transcripts in evidence bundle. |
| D3 | 🟡 LOW | `pending_count` (SQL) reads 0 while RAM FIFO holds a stranded injection — misleading during D1-class incidents. Observation only. | /injection vs instance-list during stranding. |
| D4 | 🟡 LOW | Test-debt: FE timeAgo wall-clock fixtures (decay again on next boundary); F3a grep-pin region + dedup tautology; stale F1 inline comment; `regression_integration` pack oversized (1019 collected, 314s > 300s wrapper — split due; timers 350/360 untouched); pack header count staleness (usr 539→830 actual). | Maintenance backlog; none block merge. |

## Quick fixes / commits by the gate (all TEST-ONLY, path-scoped)

- `792d9826` — kwarg-rot fix (+6): `image_refs=None` added to strict facade expectation.
- `887c98f5` — M2 closure (+141/−3): real-dispatch job-facade test + 2 docstring truth fixes.
- Quarantine edits (uncommitted until close-out): saturation row + consolidated skill_cross row
  in QUARANTINE.md; `--deselect` in `regression_chat_source_integration_test.sh` (verified,
  pack re-run green) and `regression_integration_test.sh` (bash -n verified).
- Ad-hoc pack: `test/packs/fe_jest_full_clipgate_test.sh` + PACKS.md registration.

## Scope Decision

Full suite run — **warranted**: BIG feature, final pre-merge gate, cross-module footprint
(daemon routers/services/graph/migration + FE phases 4–6). Blast radius covered by 14 census
partitions + FE full suite + 3 live boots.

## Cleanup

Live env fully torn down post-gate (instances hard-deleted, PGs stopped+removed, ports
8090-8092/4199/15433-15435 freed, detached base worktree removed, evidence bundle copied to
RESULTS/evidence/2026-09-19-clipboard-gate/, gate docs committed path-scoped) — see close-out
commit in git log and the final leader report. Ports 8079 (dev daemon) and 8088 untouched
throughout, verified.
