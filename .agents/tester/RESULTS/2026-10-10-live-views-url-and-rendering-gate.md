# Live-Views URL Resolution + Content-Aware Rendering — Feature Gate (2026-10-10)

**Verdict: ✅ PASS-with-preexisting** — zero branch-caused failures across the whole gate; all reds base-attributed at `0c040e5f8` (node-identical or ledger-exact); user-acceptance live smoke and browser verification fully green.

- **Lane:** worktree `/home/nea/ensemble-src-wt-liveviews-20261010` · branch `feature/live-views-url-and-rendering` · HEAD `8dbbcb6a1` (12 commits) · base `0c040e5f8` · venv 3.14.7 worktree-local (provenance-verified; no editable-install trap)
- **Commissioner goal A (regression):** full `tests/unit` (15834 collected, 14 gap-free slices + changed-area double-cover) — **0 branch-caused reds**; 72F+21E all pre-existing.
- **Goal B (live smoke, the user acceptance):** ALL SCENARIOS PASS with verbatim fully-qualified URLs.
- **Goal C (browser):** PASS on a real headless Chromium run (no fallback needed).
- **Goal D (mock honesty):** zero HTTP mocks anywhere; divergences documented (none material).
- **Gate window:** 2026-10-10 09:55Z → ~11:45Z · 20 worker instances · dispatcher: tester (this file's author).

---

## 1. Scope Decision

- **Full unit suite run — warranted**: explicit commission ("whole-repo regression is YOUR lane") + the diff spans middleware + routers + services + api + config (cross-module additive feature). `tests/unit` ran in full (15834 tests collected, 0 collection errors).
- **Out of blast radius (not run):** `tests/e2e`, `tests/job_queue`, `tests/migration`, `tests/mocks`, FE e2e — no reach from the 12-file live-views diff (verified empty diff-reach intersection, §3). Available on request.
- **ensure.md Release Gate: NOT run** — not a big/critical/architecture change per ensure.md blast-radius rule; release-gate items are job-workflow E2E requiring `./dev.sh` + real LLM calls, unrelated to live-views.
- **Registered live-views pack:** none exists for this branch (recon); changed-area ad-hoc pack per Phase-1 precedent (`lv_unit` @ 4f23313c2).

## 2. Unit Regression — per-slice results (all dual-layer timeout: outer 300s + inner 280s; env-fenced; `-n auto`; no `-x`)

| Pack (slice) | Result | Counts | Runtime | Red classification |
|---|---|---|---|---|
| `liveviews_changed_area` (7 files) | ✅ PASS | **315/315** (matches developer's scoped count) | 14.6s | — |
| `sweep_top_ag` test_[a-g]*.py | 🔴 FAIL (pre-existing) | 2482P / 39F / 21E / 1S | 53.7s | 9 families — **full-glob A/B at base: 39F+21E identical** (see §3) |
| `sweep_top_hi` test_[h-i]*.py | ✅ PASS | 185/185 | 13.2s | — (includes `test_host_capture_middleware.py`) |
| `sweep_top_jm` test_[j-m]*.py | 🔴 FAIL (pre-existing) | 2040P / 2F / 1S | 95.5s | both node-identical at base |
| `sweep_top_ns` test_[n-s]*.py | 🔴 FAIL (pre-existing) | 2317P / 8F / 41S | 39.4s | both families node-identical at base |
| `sweep_top_tz` test_[t-z]*.py | 🔴 FAIL (pre-existing) | 835P / 1F / 11S | 25.5s | terminal_reason registry — ledger-exact (2026-10-07) + empty diff-reach |
| `sweep_routers` routers/ | ✅ PASS | 563/563 (live-views router+markdown green) | 26.9s | — |
| `sweep_services_ai` services test_[a-i]* | ✅ PASS | 899/899 (`test_live_views_base_url.py` green) | 23.1s | — |
| `sweep_services_jr` services test_[j-r]* | 🔴 FAIL (pre-existing) | 1164P / 7F | 35.0s | `test_job_queue_proxy_phase1` ×7 — node-identical at base (also ledger-exact: 2026-10-07 "7 in-file quarantined") |
| `sweep_services_sz` services test_[s-z]* | 🔴 FAIL (pre-existing) | 530P / 2F | 29.9s | time-bomb (expired hardcoded `once` anchor) + `pid_dead` shape race — both node-identical at base |
| `sweep_tools_ai` tools test_[a-i]* | ✅ PASS | 788P / 5S | 25.6s | — |
| `sweep_tools_jr` tools test_[j-r]* | 🔴 FAIL (pre-existing) | 1954P / 9F | 56.4s | knowledge_tools ×2 node-identical; prompt_integrity ×7 **zero id-drift** at base |
| `sweep_tools_sz` tools test_[s-z]* | ✅ PASS | 692P / 2S | 22.0s | — |
| `sweep_small_dirs` (14 dirs) | 🔴 FAIL (pre-existing) | 1231P / 4F | 53.5s | plugin_subsystem ×4 — ledger-exact (2026-10-07: hash-count literal, policy no_change, MANIFEST dup ×2) + empty diff-reach |

**Totals:** 15,959 pass-records (315 changed-area double-cover included) · **72 FAILED + 21 ERRORS, 100% pre-existing, 0 branch-caused.** Every pack finished far under the 5-min cap (max 95.5s) — the 2026-10-07 `hm`-near-cap warning is resolved by the h-i/j-m split (validated: h-i is only 185 tests; the weight sits in j-m).

## 3. Failure Classification (A/B at base 0c040e5f8 — temp detached worktree, byte-identical invocation)

Diff-reach: 12-file diff × 18 failing test files = **EMPTY**; × their production modules = **EMPTY**; × live-views imports = **EMPTY**. Only shared file `daemon/api.py` (+45/−4, HostCapture wiring) — its one red (`test_api_module_is_small`, aspirational <1600-line limit) is red at base too (3260 base → 3301 branch, both ≫ limit).

- **Base-confirmed node-identical (67F + 21E):** ag 9 families (full-glob A/B; the 19E/2E vs 17E/4E builtin/context7 split is xdist bookkeeping — totals 21E conserved; standalone runs both sides agree), claim_lane ×6, projmgr ×2, llmprec ×1, tagpin ×1, promptint ×7 (zero id-drift), knowtools ×2, jqproxy ×7, sched ×1, svctool ×1.
- **Ledger-exact + empty diff-reach (5F):** tz terminal_reason ×1, plugin_subsystem ×4.
- **Location artifacts (excluded):** 5 filesystem pin reds appear only in the `/tmp` BASE worktree (tests hard-red by design under temp dirs — "loud-fail beats silent-skip"); proven GREEN at base-under-`/home` AND at branch. Neither pre-existing-bug nor branch-caused. **Future A/B base worktrees must live under `/home`, not `/tmp`** (LESSONS 2026-10-10).
- **🟢 Branch-caused: ZERO.**

Known-red registry handling follows the 2026-10-07 PACKS.md "effectively" ledger convention (deterministic base-attributed families annotated at gate headers; no skip-wiring exists for ad-hoc slices). Evidence logs: `/tmp/lvsweep_*.log`, `/tmp/lvbase_*.log`, `/tmp/lvbranch_*.log`.

## 4. Goal B — Live Smoke (user acceptance): ALL PASS (worker e7fbe288, port 18079)

Mint path = REAL service function `LiveViewsService.build_url(root, rel_path)` (`daemon/services/live_views.py:1138`) — the exact delegate of the `view_link` tool. Driver = same uvicorn app on a real socket (mirrors harness wiring; in-process `app.state.host_recorder` read-back). **Zero mocks; divergences: none** (only the WORKTREE-source note: `/tmp` drivers must set `LIVE_VIEWS_SMOKE_WORKTREE` — harness derives the tree from `__file__`).

**Verbatim minted URLs (fixture: `.agents/shared/planning/smoke/sample.md`, untracked):**
| Scenario | URL | Chain tier exercised |
|---|---|---|
| S2 baseline (no Host, no config) | `http://127.0.0.1:18079/views/planning/ens/smoke/sample.md` | bind-derived |
| S3(a) `Host: 127.0.0.1:18079` | `http://127.0.0.1:18079/views/planning/ens/smoke/sample.md` | Host-capture → fully-qualified |
| S3(b) `Host: liveviews.example.com` + `X-Forwarded-Proto: https` | `https://liveviews.example.com/views/planning/ens/smoke/sample.md` | Host-capture + proto upgrade |
| S5 config `ENSEMBLE_LIVE_VIEWS_EXTERNAL_BASE_URL=https://views.example.test` (Host still captured) | `https://views.example.test/views/planning/ens/smoke/sample.md` | **config wins over Host** |

S4 rendered page 11/11: 200 `text/html`; CSP `script-src 'self' https://cdn.jsdelivr.net 'nonce-…'`; both CDN tags (marked@12.0.2, dompurify@3.0.11) with `integrity=` + `crossorigin`; `Referrer-Policy: no-referrer`; escaped `<pre>` fallback carries the raw `.md` (payload as `&lt;script&gt;`); exactly **3** `<script>` occurrences, all accounted (2 CDN + 1 nonce bootstrap), zero content-derived.

Negatives: traversal ×3 variants → uniform 404 `{"error":"view not found"}`, no `root:` leak; disabled-root via unit (`-k disabled` → 2P; harness registers no disabled root — disables by removal); **32 MiB cap verified LIVE** (33,554,433-byte file → 404) + unit. Content-type matrix: `.html` raw passthrough (0 CDN tags), `.txt` → `text/plain`. Cleanup: port freed, PID↔port verified, scratch deleted.

## 5. Goal C — Browser (worker a3fdbab4, port 18080): PASS

Real headless Chromium (playwright 1.60.0 from sibling checkout + `~/.cache/ms-playwright` chromium-1223; CDN live 200). A1 `<h1>` rendered by marked ✅; A2 zero dialogs (alert/onerror inert) ✅; A3 script census = 2 CDN + 1 bootstrap, **0 content-derived** ✅; A4 sanitized `<img onerror>` inert ✅; A5 both CDN 200 (SRI implicitly passed) ✅. Screenshot `/tmp/liveviews-browser.png`.

Documented divergences (not defects): (1) project-scoped URLs require the `<shortname>/` prefix (`/views/planning/ens/...`); (2) DOMPurify **strips** the payload on the success path — escaped text exists only in the fail-closed `<pre>` server source (secure-by-design; the escaped-fallback assertion holds); (3) sanitized `<img src="x">` yields a cosmetic 404 console entry.

## 6. ensure.md Validation (worktree rules copy)

| Requirement | Tier | Status | Evidence |
|---|---|---|---|
| No regressions in changed packs | Critical | ✅ PASS | changed-area 315/315; routers/services/tools live-views slices green |
| Deadlock/concurrency integrity | Critical | ✅ PASS | `concurrency_atomic_unit_test` pack: 99P/74 conditional skips/0F, 67.0s |
| No sync DB calls on event loop | Critical | ✅ PASS | same pack (thread-identity tests) |
| dev.sh `--timeout-graceful-shutdown 10` | Critical | ✅ PASS | `dev.sh:142` |
| Await-correctness (3 async fns) | Important | ✅ PASS | 9/9 call sites awaited, 0 flagged; diff non-reach count 0 |
| Deadlock scenario (parent→child→complete) | Important | ✅ PASS | same concurrency pack |
| Dead code from fix | Nice-to-have | ℹ️ info | commit `8dbbcb6a1` itself drops dead `_recordated_at` field |
| Release Gate block | — | ⏭️ scoped out | not big/critical/architecture (§1); no contradiction notices this gate |

**Core: 4/4 Critical, 2/2 Important · Improvement Notices: none** (no ensure.md contradictions encountered).

## 7. Incidents / Notes

- No timeouts, no re-dispatches, no worker losses (20/20 reported). No port 8088 contact anywhere.
- Worktree pre-existing dirt (untouched): `.agents/tidier/notes.md` (M), `.agents/shared/planning/designer-agent/install-audit.jsonl` (M), `.agents/shared/planning/smoke/` (untracked fixtures).
- Quick fixes applied during gate: **none required** (zero branch-caused failures; no test-side quick-fix opportunities missed).

## 8. Workers

recon 178a6b21 · smoke e7fbe288 · browser a3fdbab4 · views eb362c81 · ag 4ae93ebc · hi cef80ae7 · jm 822559e3 · ns a6a762ee · tz 3f3f500a · routers 10a23c72 · svca a84b451e · svcb 16fd9e2e · svcz 5c9b115a · toolsa 39a37973 · toolsb 234c9d04 · toolsd 1a2f0595 · small 85fcdff9 · conc 3b8b8f80 · static ea4e49f0 · classify 80d152ce

### Overall Status
- Unit Regression (goal A): ✅ PASS-with-preexisting (0 branch-caused)
- Live Smoke (goal B): ✅ PASS
- Browser (goal C): ✅ PASS
- Mock honesty (goal D): ✅ satisfied (zero mocks, divergences documented)
- ensure.md Core: ✅ 4/4 Critical + 2/2 Important (Release Gate scoped out, justified)
- **Gate verdict: PASS-with-preexisting — feature is test-ready for the promote lane.**
