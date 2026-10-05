# Projects-Chat-Tab Closing Gate — feature/projects-chat-tab @ 2bf999db (2026-10-05)

## POST-MERGE PUSH GATE @ latest = 3903655084522c0449674ed4489ac67be20c4610 (2026-10-05) — ✅ GATES-PASS
- **Tree equivalence PROVEN**: `latest^{tree}` == `a175ebf1^{tree}` == `905b23d49f6e95bd5060f62d9034eada80e934fc` (--no-ff merge, parents `ac1bf7b9` + `a175ebf1`, zero content drift). Only delta vs gated `2bf999db` (`7c157926…`): commit `a175ebf1` = the F1 fix (7 insertions, `tests/unit/test_hide_kb_instances.py` only — `git diff --stat` verified). Prior gate results carry over.
- Gates re-run in the feature worktree staged **detached @ 39036550** (branch ref unmoved; main checkout untouched — live concurrent worker): be_suites **55/55** (42 non-PG 4.91s + 13 PG 9.89s, `-m postgres --override-ini` invocation, disposable `ensemble_test`) · FE tsc **exit 0** + touched Jest **570/570** (7.4s) · hidekb **20/20** (3.27s — F1 fix verified: was 13P/7F). All drift checks clean (porcelain empty, HEAD pinned 39036550). Workers: W12 ee7335fe (equivalence+staging) · W13 84334ae9 · W14 81b60873 · W15 09e83e26. **Verdict: GATES-PASS — push precondition satisfied.**


## VERDICT: ✅ READY-WITH-NOTES
Feature **works end-to-end on a real full stack**; **nothing pre-existing regressed** (every non-green signal base-proven or transient). One deterministic branch-introduced **test-fixture drift** (7 assertions in `tests/unit/test_hide_kb_instances.py`) requires a small test-side follow-up — production behavior is proven correct; the reds are stale `assert_called_once_with` expectations missing the new `source=None` kwarg.

- Worktree: `/home/nea/ensemble-src-wt-projects-chat-tab` @ `2bf999db` (base `ac1bf7b9` = origin/latest, 6 commits: 213a2c71→2bf999db). READ-ONLY held: **zero repo modifications, zero commits, tree byte-stable and CLEAN at close**.
- Workers: 11 (W0 e2fe162a env-prep · W1 897acc2a be-suites · W2 0bae39c9 unit-sweep · W3 cfca5b02 api-sweep + base-attribution · W4 3beb7246 fe-touched · W5 32ddf3d1 fe-full · W6 41a25abb e2e · W7 424b17bc concurrency · W8 4ac0d003 fe-jobs-base · W10 259b0614 unit-base-compare · W11 9af40004 retry-budget). All temp worktrees (`base-`, `base2-`, `base3-ac1bf7b9`) removed; live listeners 9797/7979 (and 8079, came up mid-run from a parallel commission) untouched throughout.

---

## A. Backend regression

### A1. Feature's own 4 suites — PASS 55/55
`timeout 300 bash /tmp/ens-chatgate/chatgate_be_suites_test.sh` (~14.2s):
- non-PG 42/42 in 4.05s (`test_instance_source_filter.py` 31 + `test_manager_list_instances_source.py` 5 + `test_list_instances_source_param.py` 6)
- PG 13/13 in 10.18s (`test_instance_source_filter_pg.py`, `-m postgres --override-ini="addopts="`, conftest disposable `ensemble_test` @ localhost:5432 — **gotcha**: pyproject addopts silently deselect PG without the override)

### A2. Broad unit sweep — 14,609/14,609 executed (9 partitions, all ≤270s internal; 1 partition re-split after timeout)
Totals: **14,410P / 117F / 23E / 59S** (wall ~27 min). Zero quarantined-family hits (`test_mcp_server_crud.py` PASSED in clean worktree); zero interpreter-rot collection errors.
Failure adjudication (W10 base-compare @ `ac1bf7b9`, PYTHONPATH-pinned temp worktree, daemon-resolution verified; W11 retry budget):
| Class | Count | Proof |
|---|---|---|
| PRE-EXISTING (base-identical) | 103F across 38 files | same failing ids + signatures at base (e.g. `find_near_instance` ×13 tuple-unpack stub rot; `originator_instance_id` ×8; governor/vision ×10; claim-lane ×6; archive ×5) |
| DOCUMENTED pre-existing errors | 23E | builtin_mcp 17 + context7 4 + webfetch 2 (`Mock object has no attribute 'service_tool'`) — known deferred debt |
| ENVIRONMENTAL-TRANSIENT | 7F | `test_llm_failover_v2_adversarial` ×5 + `test_p3_e2e_cycle` ×2 — 0F at base, no source diff, test-file md5 identical; **3/3 clean re-runs at branch** after audit-file restore |
| **BRANCH-CAUSED (deterministic)** | **7F** | `tests/unit/test_hide_kb_instances.py` — see Finding F1 |

### A3. API sweep — 67/69 (2 base-proven pre-existing)
`test_instance_ui_prefs_api.py:349,463` — `TypeError: _ManagerStandin.list_instances() got unexpected kwarg 'search'` @ instances.py:467. **PRE-EXISTING**: identical failures at base (`:445` there); `search=`/`order=` passed since before base; fixture predates branch (bbb976d0) and accepts neither. Branch only ADDS `source=`. Masking caveat: fixture fix must add `search`+`order`+`source`.

### A4. concurrency_atomic_unit_test (ensure.md Core #2/#3) — PASS baseline-exact
98P/0F/74S in 63.9s — repository/lane invariants hold with the feature's repository.py changes.

## B. Frontend regression
- **tsc --noEmit: exit 0** (sanctioned `frontend/node_modules/.bin/tsc -p tsconfig.app.json`).
- Touched 9 Jest specs: **570/570 PASS** (~10.5s).
- Full suite (103 spec files): **3679P/4F/3683** (22.3s). The 4 failures EXACT-MATCH base-proven pre-existing (W8 temp-worktree run @ ac1bf7b9, same ids + error literals): `jobs-grouping.model.spec.ts` ×3 ("ago" vs rendered date), `jobs-filter-state.model.spec.ts` ×1 (union drift "completed (gate escalated — unverified)"). Zero FE failures attributable to the branch.

## C. E2E — TrueAuto full-stack, 4/4 scenarios PASS (no skips, no mock fallback engaged)
Disposable stack: BE `daemon.api:app` @ 127.0.0.1:8179 (livez `{"status":"alive","version":"0.16.13"}`), FE `ng serve` @ 4299 with proxy repointed to 8179 (bundle 20.9s); seeded 8 instances (5 chat: telegram×2/slack/discord/whatsapp + webhook/scheduler/no-metadata) via direct DB insert (`InstanceCreate` has no metadata field — API cannot set source_type, see F4); verified `GET /api/instances` → 8, `?source=chat` → exactly the 5 chat ids. Playwright (chromium, cached):
1. **Tabs**: All + Chat both visible; close affordance count 0 on both (template omits `close-btn` on special tabs) — `shots/scenario1.png`
2. **Chat filter**: wire URL `…?limit=10&offset=0&exclude_kb=true&include_descendants=true&source=chat`; 5 chtg-* rows render; ncht-web-1/ncht-sch-1/nometa-1 absent — `scenario2.png`
3. **Context preservation**: row-click → `/projects/chat/instances/chtg-tg-1`; Chat tab still `.tab.active` — `scenario3.png`
4. **All unchanged**: next list request has NO source param; non-chat rows return — `scenario4.png`
Evidence: `/tmp/ens-chatgate/e2e-evidence.txt`, `shots/scenario{1-4}.png`, 13 captured URLs, `backend.log`, `fe.log`, `seed.sql`, spec+config.
Teardown verified: port-owned PIDs only killed after `ss -ltnp` ownership check; 8179/4299 freed; disposable PG `ensemble_e2e_chatgate` DROPPED.

## Findings (reported, NOT fixed — read-only gate)
- **F1 🟠 BRANCH-CAUSED test drift (the only gate-relevant red)**: `tests/unit/test_hide_kb_instances.py` 7 deterministic failures — `assert_called_once_with(...)` lacks the new `source=None` kwarg (Expected: `...(search=None, order='pinned')` / Actual: `...(search=None, order='pinned', source=None)`). 0F at base; deterministic at branch. Fix = 7 assertion updates in ONE test file (test-code only, <10 lines). Production behavior proven correct (A1 suites + e2e + concurrency).
- **F2 🟡 pre-existing SQLite boot blocker**: migration `20260714_000001_widen_job_queue_type_constraint.sql` uses `DROP CONSTRAINT IF EXISTS` — rejected by SQLite (3.53.1) with `near "EXISTS": syntax error`; runner's swallow-list doesn't cover it → daemon cannot boot SQLite (predates branch; e2e used authorized PG fallback with brand-new disposable DB).
- **F3 🟡 test-debt leak**: unit tests append `kms_issue` lines to TRACKED `.agents/shared/planning/designer-agent/install-audit.jsonl` (29 lines during sweep; caused W6-window drift + the F-transient p3 failures). Restored to byte-identical baseline under strict diff-condition (W10 step 7); root fix = tests must write to tmp paths.
- **F4 🟢 API gap**: `POST /api/instances` cannot set `instance_metadata` (no metadata field on `InstanceCreate`) — seeding for e2e required direct DB insert.
- **F5 🟢 briefing divergences**: ensure.md is the 53-line pack-mapped doc (NOT a 4-line boot probe — grep-verified both main + branch copies, byte-identical); port 8079 was NOT listening at gate start (9797/7979 are the live listeners; a parallel commission brought 8079 up mid-run — never touched).
- **F6 🟢 pre-existing unit red backlog**: 103F/38 files + 23E documented families — recommend a dedicated test-debt/quarantine commission (out of gate scope; base-proof in `/tmp/ens-chatgate/evidence.txt` + W10 matrix).

## ensure.md validation (Core scoped; Release Gate NOT triggered — feature blast radius, not architecture/release)
- Core #1 changed packs: be_suites PASS, e2e PASS, fe PASS — **with F1 exception noted** (related-lane test drift) → satisfied-with-note.
- Core #2 + #3: concurrency pack PASS baseline-exact ✅
- Core #4 (dev.sh grep): OUT OF SCOPE — dev.sh untouched by branch. Important #2 (async-await callers): OUT OF SCOPE — none of the 3 named functions touched.
- No contradictions found → no Improvement Notices.

## Scope Decision
Closing-gate verification, not a full release: ran feature suites + broad unit/api/FE slices + concurrency (repository.py in change set) + focused e2e. Release-Gate e2e_workflows (parent→child LLM flows) NOT run — execution lane untouched by this branch (listing/filter only, per diff evidence: instances.py/manager.py/instance_lifecycle.py/repository.py + FE). Full test/packs inventory NOT re-run (documented baselines consulted instead).

## Code Changes Summary
**None.** Zero modifications, zero commits by the gate; worktree `status --porcelain` EMPTY at close; HEAD `2bf999db55ccae3f6f3e2b8147fae2afc0a851f0`. All artifacts in `/tmp/ens-chatgate/` (packs, logs, evidence, screenshots, base-comparison scripts, retry outputs).

## Overall Status
- Unit: ✅ (0 branch reds outside F1; F1 = 7 stale assertions, one file)
- Mock/Integration: n/a (SQLite-boot blocked by F2; PG-fallback e2e covers integration surface)
- E2E chat-tab: ✅ 4/4 real-stack
- ensure.md Core: ✅ (#1 with F1 note, #2/#3 exact)
- **Testing Complete: READY-WITH-NOTES — merge-ready once F1's 7-assertion test fix lands (or consciously accepted as immediate follow-up)**
