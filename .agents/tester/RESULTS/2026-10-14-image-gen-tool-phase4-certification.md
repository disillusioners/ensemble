# Phase-4 Certification — image-gen tool commission

**Verdict: 🟢 PASS** — full matrix green or explicitly classified (zero commission-caused failures); Lane A live generation proven end-to-end; Lane B resolved as SKIP-WITH-DISCLOSURE (user-gate); budget 2/5 real images (≤$0.007 of the ~$0.05 cap).

## Scope

Full certification matrix as commissioned — NO scope reduction (commission-level verification of the whole image-gen tool chain: tool → store → view → dispatcher → FE). Release Gate E2E NOT triggered (branch verification, not promote; per ensure.md blast-radius rule).

## Metadata

- **Date**: 2026-10-14 (22:5x UTC window)
- **Worktree**: `/home/ensemble-worktrees/image-gen-tool` · branch `feature/image-gen-tool` · HEAD `6eeed8694` · base `8c525315e`
- **Commit count discrepancy (noted, non-blocking)**: `8c525315e..HEAD` = **11 commits**, not the 12 stated in the commission. HEAD matches exactly. Merge-base ancestry verified (exit 0).
- **Approver/leader artifacts uncommitted on tree** (`M .agents/approver/active.md`, `?? .agents/approver/image-gen-tool-tracking.md`, `?? .agents/shared/planning/image-gen-tool/`) — no code impact, left for their owners.
- **Execution**: 10 workers (1 discovery + 9 pack lanes), 0 re-dispatches, 0 timeouts.
- **Workers**: discovery `0557f0c4` · U1 `7b7634af` · U2 `8a6c95b3` · U3 `f9b5df2a` · U5 `b907581c` · U6 `81b37efa` · U7 `5479910f` · FE `7572110a` · INT `8259e685` · LANE `83b8833f`

## 1. Unit matrix

| Pack | Scope | Result | Counts | Runtime |
|---|---|---|---|---|
| `image_gen_tool_unit` (ad-hoc) | tests/unit/tools/test_image_gen_tool.py + test_frozen_tool_name_discovery.py | ✅ PASS | 101/101 (94+7) | 4.79s |
| `sources_dispatcher_unit` (ad-hoc) | tests/test_sources_dispatcher.py | ✅ PASS | 108/108 (incl. TestExtractImageGenMarkersUnit) | 0.81s |
| `chart_trio` (ad-hoc, 2 invocations) | chart e2e + audit + outbound | ✅ PASS (sole red = classified seal test) | (a) default 76 ran: 75P/1F-classified; (b) full e2e file 28: 27P/1F-classified | 2.14s + 3.22s |
| `slack_adapter_regression` (ad-hoc) | 4 slack files | ✅ PASS | 187/187 (115+30+22+20) | 3.66s |
| `sources_unit_test` (registered) | test/packs/sources_unit_test.sh | ✅ PASS | 213/213 (+61 vs 152 baseline — ALL branch-added in test_sources_dispatcher.py; other 5 files unchanged) | 9.82s |
| `image_regression_test` (registered) | test/packs/image_regression_test.sh | ✅ PASS | 138/138 (+23 vs 115 baseline, tests added since 2026-07-29; 0F) | 10.7s |

**Chart quartet reconciliation (leader's 183/184)**: trio default-filter 75 pass + dispatcher 108 = **183/184** — exactly the expected number, with the ONE red being the classified seal test. ✅

**CHART_CAPABLE audit**: 46/46 green (the whole `tests/test_chart_image_delivery_audit.py` incl. parametrized CHART_CAPABLE agents / pin_17). The 4 integration-marked chart groups green in invocation (b).

### Classified pre-existing red — `test_phase_d_does_not_modify_sealed_artifacts` (8 stale seal pins, KNOWN/deferred)

Ran in BOTH invocations (not marker-excluded). EXACTLY 8 mismatched pins, no 9th, verified against `SEALED_SHA_BASELINES` (tests/test_chart_image_delivery_e2e.py:1295-1338). Drift origin = the agent-base-v2 + agent-prompt-immutability v0.20.x merges touching these files (predates branch; matches "proven stale at base"):

1. `agents/charter/skills-template/install-mermaid-cli.md` 344f206d…→4082a47f…
2. `agents/ari/workflow.md` d39ce087…→4ffa5bee…
3. `daemon/constants.py` 64c1dbe3…→7026ed31…
4. `agents/approver/rule.md` f8282f54…→f70d2a0b…
5. `agents/developer/rule.md` dba24812…→3cf7141f…
6. `agents/planner/rule.md` 135bc320…→40c21f9b…
7. `agents/reviewer/rule.md` dab86d16…→a9991052…
8. `agents/tidier/rule.md` f620c838…→ebe7410d…

No recorded 8-pin list found in RESULTS/*image-gen* or the planning dir (cross-checked) — the SEALED_SHA_BASELINES source itself is the ledger. Resolution = the deferred seal re-pin ceremony (phase4-plan Task 6, leader-deferred). Classified, not chased.

## 2. Frontend full gate

| Gate | Result | Counts |
|---|---|---|
| jest FULL suite (115 spec files, single run, no shard needed) | ✅ PASS-with-preexisting | 4007P/4F/4011 tests · 113/115 suites |
| `tsc --noEmit -p tsconfig.app.json` (frontend-local `./node_modules/.bin/tsc`) | ✅ PASS | exit 0, zero diagnostics, 7s |

The 4 FE reds are **base-proven pre-existing** (identical set at base `8c525315e`; both spec files `git diff latest..HEAD` empty): jobs-grouping ×3 (hardcoded 2026-09-10 date aged past the 7-day relative window → absolute-date fallback) + jobs-filter-state ×1 (`JOB_STATUS_VALUES` 8-element pin; source list grew at commit 7d4a3bd9, 2026-09-26). Matches the documented ledger pattern (PACKS.md :397, :447). **0 commission-caused.** Runtime 21.7s.

## 3. Integration lane (NEW harness, committed)

`tests/test_image_gen_delivery_integration.py` — 16 tests, commit `53c94ab9f` (`test(dispatcher): image-gen delivery integration harness…`, 1078 insertions, pathspec-guarded).

- Run 1 (`-m integration`, new file): **16/16 PASS**
- Run 2 (co-green with chart groups): **20/20 PASS** (4 chart integration groups incl. `TestAstreamLaneChartImageDelivery` + 16 new) — chart regression proven in the same harness
- Run 3 (non-interference): default addopts → 16 deselected, default suite unaffected

Coverage: FULL CHAIN (marker `<!-- ens-img:image-gen:<32hex> -->` → both-seam extraction → `OutgoingMessage.images` → mock adapter receives bytes → delete-after-delivery), web leg (API/no-colon source → marker survives verbatim), 4 degraded paths (missing blob / wrong content_type / provenance mismatch / foreign feature → text fallback + WARN, per-id isolation), delete-once ordering, duplicate-marker dedup, marker-format + ALLOWED_FEATURES sanity guards. In-process, real TmpImageStore + real ResponseDispatcher + MockSourceAdapter; no sockets; no product gaps found.

## 4. Lane A — dev.sh e2e, REAL MiniMax upstream (PASS)

- **Runner**: `test/packs/image_gen_lane_a.sh` (548 lines, commit `1a3f58d9e`) — dual-layer timeouts, FAKE_HOME + escape-guard allowlist, file-backed budget counter.
- **Boot**: `Starting Ensemble v0.20.3` on :8079 (worktree dev daemon, fenced .env honored).
- **Local dev proxy**: Go build required user-space Go 1.26 bootstrap to `/home/nea/sdk/go` (go.mod requires 1.26; system had 1.22.2 — one-time, user-space, not on $PATH). Proxy store seeded under FAKE_HOME (`/tmp/imagegen-fake-home-proxy.QXY4zz/.config/llm-supervisor-proxy/config.db` — stricter than the ~/.config allowance, zero live-shaped path resolution). Model row verified: `{"id":"minimax-image-01","internal_model":"image-01","enabled":true,"kind":"image-gen"}`; minimax credential reused from proxy-repo `.env-minimax` (never minted, never echoed).
- **Chain evidence**:
  - image_id: `5de377d4fdd742e8a4456b4f81a1684d`
  - sha256 (served == stored == on-disk, byte-exact): `7a1124fbdc2ae3a884a6715284f87f051a310b8e7437c200ffd6ff5ec760610f`
  - `GET /views/tmp-images/<id>` → 200, `image/jpeg`, 64196 bytes
  - tmp_images sidecar: `{"content_type":"image/jpeg","size_bytes":64196,"sha256_hex":"7a1124fb…","provenance":{"feature":"image-gen","source_agent":"worker"}}` ✅ feature=image-gen
  - marker in agent reply: `<!-- ens-img:image-gen:5de377d4fdd742e8a4456b4f81a1684d -->`
  - tool success payload verified (ok/images[]/view_url/markers[]); agent = worker (image-gen granted)
  - worktree `.env` (gitignored, verified): IMAGE_GEN_MODEL/BASE_URL/API_KEY appended (values never printed/committed)
- **Teardown**: :8079 + :4123 released after ownership verification; :8088 untouched.

## 5. Live budget — HARD CAP 5 (script-enforced counter, shown)

```
[budget] initialized at /tmp/imagegen_budget.count (hard cap = 5)
[budget] state at run start: counter=0
[budget] BEFORE lane-a: counter=0 / assert OK
[budget] AFTER lane-a: counter=1 (cap=5; remaining=4)
[budget] BEFORE lane-b-probe: counter=1 / assert OK
[budget] AFTER lane-b-probe: counter=1 elapsed=1s
[budget] FINAL state: counter=1
```

Hard-cap enforcement independently verified: pre-setting counter=5 → `[budget] ABORT: … HARD CAP exceeded` + exit **33**. **Total real images across ALL invocations: 2** (1 canonical lane-a + 1 during runner development) ≤ 5. No n=2 probe run. Spend ≈ $0.007 (documented $0.0035/image).

## 6. Lane B — live proxy (llm.ensem.dev) probe (SKIP-WITH-DISCLOSURE)

- ONE cheap probe, zero registrations/writes on the live proxy.
- HTTP **404** `{"error":{"type":"server_error","message":"unknown model \"minimax-image-01\" (image-gen models are served by /v1/image_generation only)"}}`
- → model row NOT registered on live proxy (user-gate closed) → **SKIP-WITH-DISCLOSURE**, exit 0, 0 images.
- Disclosure artifact: `/tmp/lane_b_disclosure_20261014T221758.md`

## 7. Artifacts

- `/tmp/imagegen-run/lane-all.log` — canonical Lane A+B run log (budget lines verbatim)
- `/tmp/lane_b_disclosure_20261014T221758.md` — Lane B disclosure
- `data_dev/tmp_images/5de377d4fdd742e8a4456b4f81a1684d{,.json}` — stored blob + sidecar (worktree dev data dir)
- `/tmp/lane_a_5de377d4fdd742e8a4456b4f81a1684d.bin` — bytes served by /views/tmp-images (sha256 match)
- Proxy store: `/tmp/imagegen-fake-home-proxy.QXY4zz/.config/llm-supervisor-proxy/config.db` (left intact)

## FAIL classification summary

| Red | Class | Evidence |
|---|---|---|
| seal test (8 stale pins) | pre-existing, base-proven, deferred (seal re-pin ceremony owed) | pins 1:1 vs SEALED_SHA_BASELINES drift set; agent-base-v2/immutability merges |
| FE jobs-grouping ×3 | pre-existing, base-attributed | identical at 8c525315e; spec diff empty; PACKS.md :397/:447 ledger |
| FE jobs-filter-state ×1 | pre-existing, base-attributed | same; source list grew in 7d4a3bd9 (pre-branch) |

**Commission-caused failures: ZERO.**

## Commits produced during certification

- `53c94ab9f` — test(dispatcher): image-gen delivery integration harness (INT worker)
- `1a3f58d9e` — test(packs): image-gen lane A/B runner with 5-image budget counter (LANE worker)
- (this commit) — test(tester): phase-4 certification results + PACKS.md registration

## Deferred / disclosed items (not blockers)

1. **Seal re-pin ceremony** (phase4-plan Task 6) remains owed — the 8-pin red persists until the ceremony runs (regen `.prompt-artifacts.sha256` + refresh 2 SHAs + 2 history entries).
2. **Slack live files:write grant smoke** (phase4-plan Task 5, "at smoke time") — deferred to promote-time smoke; slack adapter unit suite (187/187) covers the graceful-degradation code path.
3. **Lane B** stays closed until the user registers the image-gen model row on the live proxy via OAuth'd FE UI.
4. **Commit-count discrepancy**: 11 actual vs 12 stated (HEAD verified 6eeed8694).
5. Lane A native-attachment capture via a wired MockSourceAdapter was NOT exercised in the live lane (optional stretch skipped); the native-delivery behavior is proven in the INT harness (in-process) and the live lane proves tool→store→view→marker.
