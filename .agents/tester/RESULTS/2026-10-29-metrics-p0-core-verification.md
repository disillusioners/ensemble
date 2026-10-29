# Test Verification — metrics-collection P0-CORE (WS-B) @ `6a307f888`

Date: 2026-10-29
Branch: `feature/metrics-collection` @ HEAD `6a307f888` (base = approved plan `32e8d04ee`)
Worktree: `agents-ensemble-wt-metrics-v2` (claim `wt.claim.metrics-v2`)
Verdict: **SHIP-WITH-CLASSIFIED** — coverage `{6a307f888, 909c9f8e2, dc50ab993, b7bfa7928}` (full gate + 3 delta rounds + tip spot-check): every functional leg PASS, regression **0 branch-caused / 0 delta-caused / 0 rider-caused**. See §6, §12, §13.

Verification target: prove the P0-core foundation works end-to-end on this host, default-OFF, and breaks nothing.

## ⚠️ Verification boundary (READ FIRST)

The worktree was under **active concurrent edit by the implementer lane (giter)** during the E2E window
(19:36–19:41): `daemon/services/metrics_gate.py` rewritten 3× (`fe16ae77`→`27f286e2`→`c7f57692`),
`metrics_listener.py`→`f26352a8`, `daemon/api.py`→`ccfd6a84`, one new untracked test file
(`tests/unit/services/test_metrics_refresh_path.py`). **HEAD never moved** — all uncommitted.

Attribution of evidence to code state:
| Evidence | Code state it ran against |
|---|---|
| Unit legs 273/273 (worker A, ~19:10) | clean HEAD `6a307f888` (pre-edit window) |
| PG pack 10/10 + trap (worker B) | clean HEAD `6a307f888` |
| A/B vs base (worker C) | committed blobs only (edits irrelevant) |
| E2E phase 1 (DEFAULT+ENABLED, 19:16) | clean HEAD `6a307f888` |
| E2E phase 2 kill-switch matrix (19:29) | clean HEAD `6a307f888` (gate imports pre-19:36) |
| E2E phase 3 bind-failure 3a/3b | clean HEAD `6a307f888` (imports 19:35:44/19:36:00, first edit 19:36:15) |
| E2E phase 3 bind re-verify 3a'/3b' | `metrics_listener.py f26352a8` (byte-identical to current tree; bind path untouched by edits) |
| E2E phase 3 registry sanity | CURRENT tree (`gate c7f57692`, imported 15s after its last write) |
| Regression gate (worker E) | per-slice sha256 fingerprints recorded (rule injected mid-flight) |

⇒ The verdict below is for **committed HEAD `6a307f888`**. The implementer then landed `513273abb`
(pre-merge fix round F1/F3/F5/F6/F11, +629/−51) and `909c9f8e` DURING the verification window;
those commits are covered ONLY by the phase-3 re-verify + registry-sanity legs (which ran the
then-current listener/gate and passed). When the branch freezes for merge, re-run the kill-switch
matrix + unit legs + this gate at the frozen tip (worker E's matrix is reusable verbatim — only the
pinned SHA changes). See Follow-ups.

Side effect disclosed: every daemon boot appends KMS-issue audit rows to the git-tracked
`.agents/shared/planning/designer-agent/install-audit.jsonl` (+25 rows across our boots; pre-existing
daemon behavior, audit data — not reverted).

## 1. Unit legs at HEAD (item 1) — PASS

`scripts/run_tests_scrubbed.sh` (takes direct pytest args; `exec .venv/bin/pytest "$@"`), 10 files, `-q --tb=short -n 4`, `timeout 300` outer.

**RESULT: PASS — 273/273, 0 failed, 0 errors, 0 skipped, 0 deselected, 10s wall.** Import provenance proven in-worktree.

| file | collected | passed |
|---|---|---|
| test_checkpoint_metrics_registry.py | 45 | 45 |
| test_checkpoint_metrics_vocabulary.py | 18 | 18 |
| test_metrics_listener.py | 29 | 29 |
| test_metrics_listener_registry.py | 15 | 15 |
| test_metrics_census.py | 34 | 34 |
| test_metrics_gate.py | 35 | 35 |
| test_metrics_managed_keys.py | 65 | 65 |
| test_metrics_live_managed_values.py | 15 | 15 |
| test_metrics_worker_naming.py | 5 | 5 |
| test_metrics_api_wiring.py | 12 | 12 |

GREEN-AT-ZERO: none (all ≥5 collected). No marker-deselected node.

**Lock-contention contract (brief 2g, synthetic)** — lives in `test_checkpoint_metrics_registry.py::TestWedgedWriter` (NOT in the listener file; brief premise corrected with grep evidence): 4/4 PASSED
(`test_render_returns_promptly_when_a_metric_lock_is_held` asserts `elapsed < 0.1` + `# … unavailable: lock_contended`; `test_many_wedged_families_still_render_within_the_budget` — see MINOR-T1 below) + 2 degraded-family outcome nodes in listener_registry PASSED.

## 2. PG pack at HEAD + abort-trap (item 1) — PASS

- **Leg 1 (real pack)**: `timeout 300 bash test/packs/metrics_census_pg_test.sh` → `RESULT: PASS`, exit 0, 8s, **10 passed / 0 F / 0 S / 0 D** (explicit N≥1 check satisfied). Residue independently verified: scratch DB + throwaway role **0 rows** post-run; `ensemble_dev`/`ensemble_prod` never written.
- **Leg 2 (injected abort on /tmp COPY)**: inner pytest target poisoned → **`RESULT: FAIL (rc=4, collected-count check: no "N passed" N>=1 …)`**, exit 1, 3s (bounded, no hang), exit-trap cleanup provably fired (residue 0/0). Loud-abort requirement MET. Design note: RESULT labels are emitted from the fail-closed check path after a `set +e`-captured leg (documented in the pack header), not echoed literally inside the bash trap function — equivalent semantics, recorded.
- **Leg 3 (vacuous-green signature)**: bare run of the module under default addopts → `no tests collected (10 deselected)` — documents why the pack pins `--override-ini="addopts=" -m postgres`.
- PACKS.md last-run row was stale (`eef7f457a`); re-verified PASS at `6a307f888` (row update pending commit — see Follow-ups; the tester's own PACKS.md edit was blocked by report-only discipline this run).

## 3. E2E contract on the fenced daemon (item 2) — PASS (all legs, 12 boots total)

Boot form: explicit `uvicorn daemon.api:app --host 127.0.0.1 --port 8091` from the worktree venv (`dev.sh` hard-codes PORT=8079 — prod port — and was NOT used; KB-recorded). DATA_DIR under /tmp. Boot wall-times 11–34s.

### 3a. DEFAULT (no env) — contract table
| case | observed |
|---|---|
| `/livez`, `/readyz` on 8091 | 200 / 200 (daemon boots normally; components all true) |
| `GET 127.0.0.1:9798/metrics` | **200, `text/plain; version=0.0.4`, body 0 bytes (valid EMPTY)** — never 503, never refused (m8/MA-15 held) |
| main port `/metrics` | **404** (`{"error":"Asset not found"}`) |
| no-Origin scrape | 200 |

### 3b. ENABLED (=1) — contract table
| case | observed |
|---|---|
| F0.1 | `ensemble_metrics_up 1` · `ensemble_metrics_scrape_requests_total{outcome="ok"}` · `ensemble_metrics_exposition_seconds` (histogram, 11 buckets + sum + count) |
| F0.2 | `ensemble_metrics_registry_measurements 9` · `ensemble_metrics_cap_rejected_total` (declared, 0 samples) · `ensemble_metrics_invalid_label_total` (declared, 0 samples) |
| F0.4 | `ensemble_metrics_vocabulary_version 1` |
| double scrape ≥1.5s apart | counter 0→1, exposition count 0→1, sum moves — **counters move** |
| origin guard | evil origin → **403 `origin_not_trusted`**; no Origin → 200; loopback origin → 200 (allowed) |
| main port | 404 (enabled too) |
| concurrent scrapes | 0.67–0.80 ms each; none hung |

Also present: 4 pre-existing checkpoint families + 2 deprecation aliases (each logs a one-time migration WARNING) + 5 frozen-vocabulary footer comments.

### 3c. Kill-switch semantics — 9 boots, 9/9 MATCH A14/NB-6
| env value (ps-proven) | 9798 | body | up | verdict |
|---|---|---|---|---|
| `true` | 200 | 3333 B | 1 | ON ✅ |
| `yes` | 200 | 3333 B | 1 | ON ✅ |
| `1` (phase 1) | 200 | series | 1 | ON ✅ |
| `0` | 200 | 0 B | absent | OFF ✅ |
| `false` | 200 | 0 B | absent | OFF ✅ |
| `off` | 200 | 0 B | absent | OFF ✅ |
| `` (set-but-EMPTY — ps token `ENSEMBLE_METRICS_ENABLED=`) | 200 | 0 B | absent | OFF ✅ |
| `on` (NOT truthy per A14 — sharpest edge) | 200 | 0 B | absent | OFF ✅ |
| UNSET (managed default false) | 200 | 0 B | absent | OFF ✅ |
| UNSET → PUT `limits.metrics_enabled=true` (settings API, live) | 200 | 4159 B | 1 | **ON within TTL 5s — no restart** ✅ |
| UNSET → PUT back false | 200 | 0 B | absent | OFF ✅ |
| EXPLICIT `0` + managed `true` (settings-API-proven `source:"db"`) | 200 | 0 B | absent | **OFF — explicit OFF wins** ✅ |

Settings-API surface used: `PUT/DELETE /api/settings/daemon/limits.metrics_enabled` (auth disabled in dev). `app_settings` restored to 0 rows after every leg.

### 3d. Origin guard — covered in 3b (403 `origin_not_trusted`; null/file/spoof-suffix hosts fail closed by construction per `_host_is_trusted` source check).

### 3e. Main port 404 — covered in 3a/3b.

### 3f. Bind-failure resilience — PASS (both DEFAULT and ENABLED=1 boots)
Occupy 9798 with a signature dummy → boot daemon:
- boot COMPLETES (livez 200, 34.3s / 18.6s), `/readyz` 200 all-components-true;
- log carries EXACTLY ONE degradation line, verbatim: `[MetricsListener] DISABLED — bind failed on 127.0.0.1:9798 (OSError: [Errno 48] Address already in use)`;
- 9798 still answered by the dummy (daemon did not steal/crash); 0 tracebacks; no retry storm (total listener lines = 1).

### 3g. Registry behavior — PASS
Live (clean boot, ENABLED=1): 4 scrapes, counter `1→2→3→4` monotonic, exposition histogram in lock-step, all ≈1.2 ms (~200× under `RENDER_TOTAL_BUDGET_SECONDS=0.25`), overlapping scrapes show no amplification, body stable (47 lines ×4). Synthetic contract unit-proven by `TestWedgedWriter` 4/4.

## 4. A/B at base `32e8d04ee` (item 3) — PASS (with premise inversion, reported as measured)

- Diff inventory: **32 files, +6670/−269**. daemon/: +3 new (`metrics_census.py`, `metrics_gate.py`, `metrics_listener.py`), 6 modified (`api.py`, `checkpoint_metrics.py`, `config.py`, `routers/schemas.py`, `services/settings_service.py`, `services/worker_pool.py`).
- **All 13 branch-added test files fail-to-exist at base (rc=128 each)** — branch-added proven.
- The 6 MODIFIED pre-existing test files are **green at BOTH base and HEAD with identical counts** (provenance proven at pytest level via session-start plugin; `.pth` trap handled). Their diffs are +32/−16, non-load-bearing ⇒ no A/B divergence, no regression surface. The brief's "or fail" alternative was not needed; the stronger equivalence property was measured instead.

## 5. Static / ensure.md checks — PASS
- `dev.sh` carries `--timeout-graceful-shutdown 10` (line 155) — ensure.md Core Critical #4 ✅
- No `daemon/` diff hunk touches `_get_system_prompt_tokens` / `_compute_context_usage` / `get_queue_stats` — ensure.md Important #1 N/A-PASS ✅
- WS-A conftest divergence (item 4 context): `tests/postgres/conftest.py` blob `48f1a455…` **byte-identical at base and HEAD** (fence markers absent both sides); `latest` carries the fence (`56b53581…`). Zero branch delta ⇒ latest's fenced blob wins cleanly at merge; postgres-lane outcomes are identical at base+HEAD by construction.

## 6. Regression gate (item 4) — PASS, 0 branch-caused

Executed in a **pristine detached worktree pinned at `6a307f888`** (`/tmp/metrics-head-reg`, symlinked shared venv, `UV_NO_SYNC=1`) after the live worktree proved to be moving (see boundary). 17 slices (14 registered packs + 3 gap-fills), each `timeout 300`, census-reconciled, source-fingerprinted STABLE on every slice.

**Totals: 30,220 nodes collected · 29,513 P · 299 F · 22 E · 381 S · 6 D. Green-at-zero: none.**
Every slice ≤ 245s (≤ 5-min cap held; the one projected over-budget slice was empirically under).

**Classification of all 294 unique red nodes (89 files):**
| class | count | evidence |
|---|---|---|
| (a) pre-existing/known | 285 | red at HEAD **and** base `32e8d04ee`, same node id; QUARANTINE.md / PACKS.md rows cited (Phase-C 2026-10-22 base-leg-proven set, LCA-gate 2026-09-06, SQLite-migration families, governor-vision, configure_builtin_idempotency ×15, process_message_metrics baseline, api_router size-pin — api.py 5224 lines at BASE vs 1600 threshold, pre-existing by construction, branch adds no include_router) |
| (b) base-divergence / base-attributed flake | 9 HEAD-only (+4 base-only) | all 9 disproven as branch-caused: solo ×3 PASS both legs + file-context ×3 with symmetric non-deterministic red sets across legs (every HEAD-only node in one rep is base-red in another) |
| (c) **branch-caused** | **0** | — |
| UNPROVEN-BASE | 0 | provenance-proven per session (plugin `[PROVENANCE] daemon.__file__` both legs) |

**Concurrency pack (ensure.md Core Critical #2/#3):** 173 nodes → 94P / 5F / 74S, 19s. All 5 reds (`test_instance_metadata_atomic.py`, DetachedInstanceError) are BASE-RED + QUARANTINE-cited (Phase-C row) ⇒ quarantine-aware PASS. Note: the pack's own `RESULT:` line never prints (known `set -euo pipefail` bug, PACKS.md row 347) — exit code is the verdict.

**Gate integrity events (both remediated):**
- **F1 moving tree** — implementer committed `513273abb` (+629/−51, F1/F3/F5/F6/F11) and `909c9f8e` DURING the gate; first live-tree pass discarded, whole matrix re-run pinned at `6a307f888` (stable by construction). ⇒ **This verdict covers `6a307f888` ONLY; `513273abb`/`909c9f8e` are NOT covered.**
- **F2 silent under-run** — `regression_unit_tools_test.sh` INTERNALERROR-aborted (quarantined session-aborter `test_upgrade_executor_systemd_service.py::TestDetectorContract::test_swallowed_exception_classifies_unavailable_not_legacy` not deselected by the pack), dropping 357 items with a plausible summary. Caught by census-vs-collected reconciliation; re-run with the aborter deselected on BOTH legs (4,218 vs 4,213 nodes, identical red sets). BASE reproduces the same INTERNALERROR ⇒ base-identical.
- Collateral (repaired + verified): `uv run` in the symlinked worktree re-synced the shared venv's `_editable_impl_ensemble.pth`; restored byte-exactly, dist-info RECORD sha corrected, import re-verified to main worktree; `UV_NO_SYNC=1` used thereafter.

Artifacts: `/tmp/ens_reggate/` (CLASSIFICATION.md 89 rows, final_diff.json with all 294 node ids both legs, 17 slice logs + fingerprints).

**Ensure.md scorecard (final):**
- Core Critical #1 changed packs PASS — ✅ (legs 273/273 + PG pack 10/10 + regression 0 branch-caused)
- Core Critical #2/#3 concurrency pack — ✅ quarantine-aware (5 base-red QUARANTINE-cited)
- Core Critical #4 dev.sh shutdown flag — ✅
- Core Important #1 async-await — ✅ N/A (no diff hunks touch the three functions)
- Release Gate: full non-integration suite — ✅ via 17 slices, 0 branch-caused
- Release Gate LLM-E2E items — SCOPED OUT (no job-orchestration files in diff; requires 8079 daemon held by untouchable prod pid 71556)

**ensure.md Improvement Notices (test-infra debt, none user-file):**
- ⚠️ `regression_unit_tools_test.sh` lacks the `--deselect` for the QUARANTINE'd session-aborter → every future gate silently under-covers 357 items unless census-reconciled. Suggested fix: add `--deselect tests/unit/tools/test_upgrade_executor_systemd_service.py::TestDetectorContract::test_swallowed_exception_classifies_unavailable_not_legacy` (queued — not applied mid-flight in the implementer's active worktree).
- ⚠️ `concurrency_atomic_unit_test.sh` emits no `RESULT:` line on failure (set -euo pipefail; PACKS.md row 347) — exit-code-only verdict invites misreading.

## 7. Minor findings ledger (none contract-breaching; for the merge gate)

| id | severity | finding | evidence |
|---|---|---|---|
| MINOR-1 | 🟢 | first scrape lags the scrape-counter by one (declared family, no sample line; self-documented in `metrics_listener._record_scrape`) | phase-1 scrape #1 vs #2 diff |
| MINOR-2 | 🟢 | `registry_measurements 9` vs 11 rendered `# HELP` families (9 canonical + 2 deprecation aliases; help text explains "NOT the number of series") | phase-1 body |
| MINOR-3 | 🟠 | deferral log line fires only at boot (`api.py:2351`); runtime managed-key flip stays silent — observability gap vs plan §5 "never mysterious" | phase-2 M1(d) grep ABSENT |
| MINOR-4 | 🟠 | top-level `restart_required` aggregate = true after a live-effective DB override of `limits.metrics_enabled` (pre-existing aggregate logic in untouched `routers/settings.py`, first EXPOSED by this branch; FE unaffected — reads per-key). Sub-point: per-entry `value` lags boot-applied truth (documented intentional) | phase-2 supplement |
| MINOR-T1 | 🟢 | budget test tolerance 4× contract: `test_many_wedged_families_still_render_within_the_budget` asserts `< 1.0s` vs 0.25s budget (matches external reviewer's "250ms budget is soft") | worker A node inspection |
| PROC-1 | 🟠 | concurrent implementer edits moved the tree mid-verification (see boundary section); boot-based evidence MUST be source-fingerprinted on this worktree | phase-3 fingerprints |
| PROC-2 | 🟢 | any daemon boot appends KMS audit rows to git-tracked `install-audit.jsonl` — expect dirty after live boots | +25 rows |

## 8. Scope decision

Full regression IS warranted (release-gate class): the branch is a cross-cutting foundation (api lifespan + config + constants + settings + worker naming + 3 new services). Scoped OUT: ensure.md Release-Gate LLM-E2E items (parent→child workflow E2E) — no job-orchestration files in the diff, and they require the 8079 dev daemon currently held by the untouchable prod pid 71556. ensure.md coverage: Core #1 (changed packs) ✅ via items 1-2; Core #2/#3 (concurrency pack) → worker E; Core #4 ✅; Release-Gate "full non-integration suite" → worker E.

## 9. Follow-ups
- [ ] MERGE GATE: re-run unit legs + kill-switch matrix + regression matrix at the frozen branch tip (post-`909c9f8e`; matrix reusable verbatim, re-pin SHA). Until then this verdict covers `6a307f888` only.
- [ ] Update PACKS.md rows when the worktree stabilizes: `metrics_census_pg_test` Last Run → PASS 10/10 @ `6a307f888`; `regression_unit_tools_test` → add the aborter `--deselect` (Improvement Notice); concurrency pack RESULT-emission bug row already documented.
- [ ] MINOR-3 (deferral log line boot-only) / MINOR-4 (restart_required aggregate) to the merge gate for triage.
- [ ] PROC-2: expect `.agents/shared/planning/designer-agent/install-audit.jsonl` dirty after any live daemon boot (KMS audit append; pre-existing).

## 11. Verdict rationale

**SHIP-WITH-CLASSIFIED @ `6a307f888`.** All functional legs PASS (unit 273/273; PG pack 10/10 + loud abort-trap + residue-clean; E2E contract 12/12 boots across default/enabled/kill-switch-matrix/origin/main-port/bind-failure/registry — 9/9 matrix match incl. the `on`-fails-closed edge and live managed-chain deferral + explicit-OFF precedence; A/B proven: 13 fail-to-exist + 6 base-equivalent; statics pass). Regression: 30,220 nodes, 294 reds, **all classified, 0 branch-caused**. No contract defect anywhere. Ship with the classified baseline-debt set attached + the boundary caveat (branch advanced to `909c9f8e` mid-verification — not covered).

## 10. Worker roster
- metrics-legs `d111187d` (test-pack-execution): legs 273/273 + census + premise correction
- metrics-pgpack `0dda46ec` (test-pack-execution): pack 10/10 + trap + vacuous-green signature
- metrics-ab-base `ac0d8910` (no skill): A/B + statics + WS-A evidence
- metrics-e2e `7e7e75a0` (e2e-test): 12 boots, phases 1-3, all contract legs + concurrent-edit attribution
- metrics-regress `eb29752b` (test-pack-execution): regression gate (pending)


---

# DELTA ROUNDS — coverage extended from `6a307f888` to the branch tip `b7bfa7928`

The implementer lane landed fix rounds DURING verification (documented in the boundary section). Rather than
deliver a verdict for a stale SHA, coverage was extended in three pinned rounds. Every round pins the exact
commit (detached worktree when the branch was hot; provenance proven per session), so no round's evidence is
staleable. Stop rule: if the branch moves past `b7bfa7928`, the merge gate re-pins (recipe below).

## Round 2 — `909c9f8e2` (delta `6a307f888..909c9f8e2`: 12 files, +798/−188; gate +234, listener +83, api +16, NEW services conftest +105, NEW test_metrics_refresh_path.py +195)

- **Unit legs: 306/306 PASS** (12 files; census shifts: listener +12, refresh_path +12 NEW, artifact +9; the new shared services conftest shifted nothing else). The F1 fix is unit-proven by its own file: `test_a_slow_provider_does_not_block_the_request_path`, `test_the_request_path_does_not_perform_the_read_itself`, `test_an_explicit_env_tier_makes_no_db_touch_through_the_listener`, `test_an_explicit_env_tier_never_calls_the_provider` ×6, single-flight (`exactly_one_refresh`, `never_stacked`), TTL-bounded — 12/12.
- **Delta-scoped regression (4 slices: services 3,177 / tools 4,227-with-aborter-deselected / a_h 1,703 ×3 reps / persistence 124): 71 reds, ALL in the classified 294-set; 4 non-294 reds = context-flakes** (jsonb shared-schema xdist trio — root cause: `fresh_pg_schema` DROP/CREATEs public schema on shared ensemble_test under `-n auto`, missing `no_xdist` marker; discord eviction solo-green both legs) → **DELTA-CAUSED 0**. Shared venv provably never written (`find .venv -newermt` empty; `.pth`/`RECORD`/`direct_url.json` shas unchanged) via a PATH-shim that forwards the packs' internal `uv run pytest` to the venv python and refuses all other uv subcommands.
- **E2E at the pinned tip** (the pin guard caught a mid-phase drift — api/listener/gate rewritten 21:39–21:41 — and EVERY leg re-ran on the re-pinned detached tree): **7/7 matrix MATCH** (`true`/`yes` ON; `0`/`off`/empty/`on` OFF; UNSET→OFF). Reduction of `1`/`false` justified by byte-identical truth-table evidence (`TRUTHY_VALUES = frozenset({"1","true","yes"})` + one `in` check, untouched by the delta). Managed chain 8-step flip green; explicit-`0`-beats-managed-ON green (settings-API-proven); origin 403/200/200; main-port 404; `app_settings` 0 rows final.
- **F1 no-DB-touch claim PROVEN live** — 3 regimes (explicit ON / explicit OFF / unset+managed ON), ~620k tight-psycopg samples: 30 scrapes → **0 new connections attributable to scrapes** in every regime; the only new connections are census ticks (15s cadence, `application_name='ensemble-metrics-census'`, ~7–10ms lifetime — a sampler slower than the connection's lifetime reports a false negative; documented).

## Round 3 — `dc50ab993` (rider, +75/−21, 8 files: api +2/−2 comment-only, gate +18 = L-NEW-F1 derive-before-lock atomicity, listener +7 positional-only header lookup, conftest ±21, live_managed_values +37)

- **Unit legs: 307/307 PASS** (+1 = the NEW L-NEW-F1 node `test_a_raising_derivation_leaves_the_cache_and_the_handler_consistent`, green; explosive-`bool()` provider keeps OLD cache values AND OLD gate flag). All F1/contract nodes re-green.
- **Services slice: 3,178 collected, 12 reds byte-identical to the 294-set — RIDER-CAUSED 0**; all 119 rider-touched metrics nodes green; the services red set is now identical across THREE independent pins (`6a307f888`, `909c9f8e2`, `dc50ab993`).
- Scope decision (stated, not silent): rider's a_h/tools/persistence slices NOT re-run — the rider's daemon surface is metrics-scoped and covered at the tip by the legs pack (incl. `test_metrics_api_wiring.py` + the artifact file) and the live boots.

## Round 4 — `b7bfa7928` (tip = rider + two TESTER-OWNED test-infra commits; branch had stopped moving)

- **Tester test-infra commits landed on the branch (report + hashes; NOT pushed — branch owner handles remote cadence):**
  - `00cdc6d4b` — `tests/migration/test_jsonb_migration.py` gains `pytestmark = pytest.mark.no_xdist` (+8 comment lines). Verified: solo ×3 = 8/8/8 PASS; `-n 3` ×3 = 8 skipped rc=0 (designed guard semantics — module skips under xdist workers; serial opt-in via `-m no_xdist`). Flake signature gone.
  - `b7bfa7928` — `test/packs/regression_unit_tools_test.sh` gains the QUARANTINE-mandated `--deselect` for the session-aborter node (+7 comment lines). Verified: pack census 4,222 = exact collect-only match, **0 INTERNALERROR, 0 dropped items**; the 13 remaining reds are the known pre-existing families; the aborter is absent from the failure inventory.
- **Tip spot-check (deliberate receipt for the exact merge candidate):** `true`→ON (200/3333B/up=1/11 families); `on`→OFF (200/0B — truth-table edge); managed chain 8-step sequence green ending `app_settings` 0 rows; **F1 live leg PROVEN at the tip** (92,316 samples; `sessions_delta`=3 over 32.1s = census ticks only; 30 scrapes → 0 attributable connections; `xact_commit`/`tup_returned` deltas show background work only). Shas identical before/after; HEAD did not move again during the run.

## Observations carried (non-defects, both reproduced at `909c9f8e2` AND `b7bfa7928` — rider did not change them)

- **Managed-flip settle shape:** the first post-PUT scrape can still serve the boot fallback even with a prior baseline scrape (refresher reads at most once per 5s TTL). Bounded by one refresher read → **within the "settles within TTL 5s" contract**. Operator mitigation: scrape once before relying on the flip.

## 13. Final coverage map

| SHA | what ran | result |
|---|---|---|
| `6a307f888` | FULL gate: 17 slices / 30,220 nodes + legs 273/273 + PG pack 10/10 (+trap+residue) + E2E 12 boots (default/enabled/9-cell matrix/bind-failure×2/registry) + A/B + statics | **PASS — 0 branch-caused** (294 reds all classified) |
| `909c9f8e2` | delta legs 306/306 + 4-slice regression (0 delta-caused) + E2E matrix 7/7 + chain/precedence + F1 live | **PASS — 0 delta-caused** |
| `dc50ab993` | rider legs 307/307 + services slice (12 reds = identical across 3 pins) | **PASS — 0 rider-caused** |
| `b7bfa7928` | tip spot-check (matrix edge pair + chain + F1 live) + 2 verified test-infra commits | **PASS** |

**If the branch moves past `b7bfa7928`:** re-pin (detached worktree + provenance plugin + sha fingerprints — the proven recipe), re-run {12-file legs pack, services slice, matrix spot + F1 leg} ≈ 15 min of worker time. The truth-table code path is two lines and has been byte-stable across all four SHAs.

## 14. Worker roster (final)

| worker | instance | rounds | skill |
|---|---|---|---|
| metrics-legs / F-revived | `d111187d` / `d8214372` | legs 273 @6a307; delta legs 306 @909c; rider legs 307 @dc50 | test-pack-execution |
| metrics-pgpack | `0dda46ec` | PG pack 10/10 + abort-trap + vacuous-green @6a307 | test-pack-execution |
| metrics-ab-base | `ac0d8910` | A/B + statics + WS-A evidence | — (infra) |
| metrics-e2e | `7e7e75a0` | E2E phases 1–3 (12 boots) @6a307 + concurrent-edit attribution | e2e-test |
| metrics-regress / H-revived | `eb29752b` / `1358ad8f` | full 17-slice gate @6a307; delta 4-slice @909c; rider services slice @dc50 | test-pack-execution |
| metrics-delta-e2e / G-revived | `de57549c` | delta matrix + F1 @909c (re-pinned); tip spot-check + F1 @b7bf | e2e-test |
| metrics-infra-fix | `0bbe4f54` | jsonb no_xdist + tools-pack deselect (commits `00cdc6d4b`, `b7bfa7928`) | quick-fix |

Skills created this session (worker-side): ad-hoc file-list pack census gate · regression-matrix-integrity-under-concurrent-edits · delta-scoped-regression-gate · live-daemon-kill-switch-matrix. One `skill_fix` filed (kill-switch-matrix-execution gap).
