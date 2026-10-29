# TEST GATE — checkpoint quick wins (Phase 1), `feature/checkpoint-quick-wins`

**Date:** 2026-10-29 · **Tester gate:** independent, read-only
**Branch:** `feature/checkpoint-quick-wins` · **Briefed HEAD:** `458720b26` · **Base:** `9bbef71c1` (v0.27.12)
**Actual HEAD at gate:** `67f9f1b03` = `458720b26` + a DOCS-ONLY commit (`A docs/runpoints…` → `A docs/runbooks/checkpoint-maintenance.md`, 480 markdown lines, **zero source/test change** — verified by `git diff --name-status`). Code under test is byte-identical to the briefed HEAD.
**Diff shape:** 33 files, +4515/−138 (16 production, 17 test). Zero DDL, zero migration delta.

## VERDICT: ❌ **NO-SHIP (narrow blocker — one 1-line test fix + two recommended cleanups)**

---

## 1. Targeted packs (17 new/changed test files) — 423 nodes, 420 PASS / 3 FAIL

Run twice (default `addopts` + `--override-ini='addopts='`) — **identical failure set, identical 423-node census**; no node is marker-hidden (zero integration/postgres/slow/conformance markers in the pack). Solo re-run reproduced all 3 (deterministic, 0 flakes). Import provenance proved at pytest level on every run (`CQ_IMPORT_PROOF daemon.__file__=<worktree>/daemon/__init__.py`, `inside_cwd_tree=True`).

| Node | Verdict |
|---|---|
| `tests/test_persistence.py::TestGetCheckpointer::test_get_checkpointer_postgres_dispatches_to_pg` | 🔴 **BRANCH-CAUSED** (the ONLY one; see §3) |
| `tests/unit/tools/test_instance_tools.py::TestRegistration::test_planner_and_tester_resolve_subtree_messages_via_registry` | 🟡 pre-existing — matches QUARANTINE row 2026-10-17 "planner subtree opt-in drift ×2"; `git diff 9bbef71c1..HEAD -- agents/` is empty |
| `…::TestSubtreeStatusRegistration::test_leader_planner_tester_opt_in_via_production_seam` | 🟡 same row |

## 2. New-test QUALITY (item 2) — 20 mutation cycles, 18 KILLED / 2 SURVIVED

Scratch-copy harness (`.pth` rewritten to the scratch, `__pycache__` purged, import-proof enforced per run, original worktree verified pristine, scratch deleted). **Every one of the 12 new/changed test files produced ≥1 predicted RED from a one-line production revert.** Notable kills: the EXISTS probe at all **three** layers (adapter / manager / retry-path-with-never-call-the-raw-saver), post-prune ANALYZE in **both** directions, the severed-knob ladder, `asyncio.gather`→sequential (killed via `peak in-flight=1` — a real concurrency assertion), cap 25→100, and the boot-log truthfulness pin.

**Proven vacuity/over-pinning findings:**
- **F1 (HIGH)** `test_readiness_queue_age_render.py` — reason labels asserted against the production constants imported into the test; aliasing all four to `"unknown"` left 7/7 green. Vocabulary unpinned; routing pinned.
- **F2 (HIGH)** `test_checkpoint_version_census.py::test_version_half_reuses_the_prune_scan` — **stays green with the entire version census deleted** (asserts only `await_count == 1` on a scan that pre-exists the census).
- **F3 (MED)** `test_metrics_route.py` — `_client()` builds its own FastAPI app; deleting the production mount `api.py:4848` left 3/3 green. **Mitigated:** the same deletion reddens `test_router_scope_manifest.py::test_census_pinned` + both `test_router_class_manifest.py` universe tests — the seam IS guarded, by different files.
- **F4 (MED)** leak-signal emitter→consumer wiring guarded by exactly 1 of 10 nodes.
- **F5/F6 (LOW)** self-constructed-DTO shape pin; forwarding pinned on kwargs without an exactly-once negative.
- **Tautology (partial)** `test_new_read_counters_appear_in_the_body`: 2 of 5 assertions match the unconditional `# HELP`/`# TYPE` headers (`render_metrics()` emits them header-always; `checkpoint_read_measurement_errors_total` also gets an always-rendered value line). The other 3 assertions pin real observed values.

## 3. Regression + pre-existing A/B (item 3)

**Broad regression (8 packs, 14,732 census nodes):** P1 services 2963 (2951P/12F) · P2 tools 4230 (4209P/13F/8S + 1 INTERNALERROR, pre-existing offender file untouched by the diff) · P3 routers 819 (819P) · P4 the two new dirs 100 (**100P**) · P5 93 (57P/21F/15E) · P6 concurrency pack 173 (94P/5F/74S) · P7 `tests/unit/*.py` 10,414 (10,269P/61F/21E/63S). **146 failing nodes classified: 115 DETERMINISTIC / 31 FLAKE** (pack-only; all 31 pass solo — 29 share one root cause: the `Table 'secrets' already defined` cross-import pollution class; 4 are xdist-order). Machine-readable set: `/tmp/cq-gate/w2/ALL_FAILING_NODES.txt`.

**A/B at base `9bbef71c1`** (detached worktree, own venv, import-proven per leg, removed cleanly): **24 of 25 claimed pre-existing nodes are byte-identically SAME-FAIL at base; 0 BASE-ONLY, 0 divergent mechanisms.** Every family proven by mechanism, not just parity.

**🔴 The ONE branch-caused failure** — `tests/test_persistence.py::TestGetCheckpointer::test_get_checkpointer_postgres_dispatches_to_pg`:
`daemon/persistence.py` added kw-only `checkpoint_pool_max_size`/`checkpoint_pool_timeout_s` and `get_checkpointer` now forwards `10`/`5.0`; the test still pins `assert_awaited_once_with(config)`. **PASS at base → FAIL at HEAD** (A/B-proven 3×: W3 detached-worktree leg, W5 `git archive` leg, W2 source-level). The branch updated the *sibling* `prepare_threshold` assertion in the same file and missed this one. **It is in the DEFAULT marker suite** (W5 run #5: 1 failed, 35 passed) → it reddens every default gate run. Fix is 1 line (test-side).

**⚠️ Correction to a worker's classification (adjudicated by me, decisive):** W2 reported a 2nd "branch-introduced" failure, `tests/unit/test_post_restart_arm_notify_no_parallel.py::TestNoNewHttpEndpoint::test_no_new_http_endpoint_in_api_router`. Applying the test's **own regex** to both legs: `PRE_FEATURE_API_ROUTER_LIST` = **35** entries; BASE actual = **36** (extra: `wedge_capture_router`); HEAD actual = **37**. **The test was already red at base** — the 2026-10-22 wedge-capture lane never updated this pin. The branch did not flip it green→red; it appended a second item to an existing failure. **Pre-existing.** (Hygiene note: a lane that grows the router set should update every router census, not just the two manifest cages.)

## 4. Live dev checks

**4a — `/api/metrics` — PASS.** Mounted inside the same `api_router` cage (`api.py:4848`), **not** in the exact-public list, **no `scope_required`** (contrast `/api/wedge-captures` → `maintenance:read`). Auth-ON: 302 (browser)/401 (machine) unauthenticated, 200 with any valid bearer. Auth-OFF: 200 unauthenticated. Body is **Prometheus text-exposition, not JSON**; `route body == render_metrics()` exactly; 9 families, 38 non-zero lines, byte histogram present. **Leak audit: 0 uuid tokens, 0 32-hex, 0 paths, 0 instance mentions** — a traffic fingerprint only. Exposure is the *same posture* as `/api/resources`; no new auth class. 🟠 advisory: in the shipped default (`host 0.0.0.0`, `auth.enabled: false` — `config.yaml:112/:130`, pre-existing) the whole `/api` surface is unauthenticated; this adds one info surface.

**4b — boot log — PASS.** Verbatim: `INFO daemon.persistence:persistence.py:272 Checkpoint pool sizing applied: max_size=13 timeout_s=7.5 (explicit kwargs; … default tier 10/5.0)`. Independently re-proven with different values (17/9.25): **logged == actually-used** against the live pool kwargs, not merely requested. Precedence exercised live: yaml→env(19)→db(18); out-of-range db tier skipped → env; no tier → default 10.

**4c — post-prune ANALYZE — PASS.** Destructive: `ran=True`, `skipped_reason=None`, SQL = `ANALYZE checkpoints/checkpoint_blobs/checkpoint_writes`, dedicated engine built once, **shared engine statements = `[]`**. No-op: `ran=False`, `skipped_reason='no_rows_deleted'`, **no SQL, engine never built**. Other skips: `no_shared_engine`, `not_postgresql`, `engine_build_failed`; per-table failures swallowed.

**4d — TOAST reloptions — PASS.** `pg_toast_37191/37204/37212` all carry `['autovacuum_enabled=on','autovacuum_vacuum_scale_factor=0.1']`; parents carry the pre-existing `['autovacuum_vacuum_scale_factor=0.05','autovacuum_vacuum_threshold=1000']` (hook present at base `manager.py:9240+`; `git diff … daemon/migrations/` **empty** — the reloptions migration is pre-existing). Whole-DB: exactly 6 relations carry autovacuum options (3 TOAST ours + 3 parents pre-existing). Catalog fingerprints identical before/after. Notes: `autovacuum_enabled=on` is already the PG default (only scale_factor 0.2→0.1 changes behavior); re-appliable by non-superuser `ensemble` **only via the parent form** (`pg_toast` schema has `nspacl=NULL`); dev-scale = prevention, not a measured fix.

**4e — EXPLAIN A/B — PASS WITH ONE DEVIATION.** (i) `None` ⇒ **0** prepared statements after 4 and 20 reads, and on the real `AsyncConnectionPool` path. (ii) The brief's "3 under the old setting" is **mis-specified**: the old value was **`0`**, not the psycopg3 default `5` (diff: `- "prepare_threshold": 0`); preparation is one-per-distinct-statement-text, so `0` ⇒ **1** after the same query ×4 and **3** only for 3 distinct statements; pool path ⇒ 2 (the pool's empty `check_connection` probe). (iii) Direction PASS (un-prepared arm always slower, 0 never in any CI) but **magnitude FAIL**: measured **+0.229…+0.254 ms**, 95% CI **[+0.220, +0.261]** across 4 repetitions with varied leg order — the claimed **+0.405 ms lies outside every CI** (~40% high). (iv) **Strong PASS**: 0 non-timing leaf differences; shape identical across 10 EXPLAINs/leg, even after the prepared statement reached `generic_plans=5`.
🟠 **Shipped-source defect:** `daemon/persistence.py:376-383` asserts *"MEASURED… 0.96 ms → 1.40 ms — about +0.4 ms per read"*; same DB, same 9 rows → measured **0.27 → 0.50 ms ≈ +0.24 ms**. The comment's *qualitative* clause (both arms same plan) is confirmed. One-line comment fix.

## 5. No-side-effect reconciliation (item 5) — ALL CLEAN

- **No daemon restart/boot**: nothing listened on 8079/8088/8092/4199 at start, mid-gate, and end; no worker started one.
- **No live env mutated**: dev-DB catalog fingerprints identical before/after; row counts unchanged (9/9/11/10); the only catalog change in the lane is the pre-existing reloptions (already at base).
- **No commits/pushes/merges by the gate**: HEAD/reflog contain only the branch's own commits.
- **Worktrees**: W3's detached base worktree removed cleanly (`git worktree list` clean, no residue); W4's scratch was a `cp -a` copy, deleted; no `--force` used anywhere.
- **FOREIGN file** `.agents/shared/planning/designer-agent/install-audit.jsonl`: our runs appended **75** `kms_issue` audit lines (W3's HEAD leg of `tests/unit/services/` ×18; W2's broad packs ×57 — both 100% attributed by timestamp window; root cause: `append_install_audit` resolves `workdir=os.getcwd()` and the kms tests mint with cwd = the worktree). **Restored byte-identically to the pre-gate state** (sha `807143b4…`, 382 lines/91206 B, `cmp` clean vs the pre-gate snapshot); `git diff --numstat` back to the foreign workstream's original **36/0**. Nothing of theirs was touched; the pre-gate prefix was provably intact throughout (verified 3×).

## ensure.md status (scoped to blast radius)

| Requirement | Status |
|---|---|
| Core-C1 no regressions in changed packs | ❌ **FAIL** — 1 branch-caused red (`tests/test_persistence.py:81`) in the changed set |
| Core-C2/C3 concurrency integrity + no sync DB on loop | ✅ PASS (quarantine-aware) — pack 94P/5F/74S; the 5 = `test_instance_metadata_atomic` `DetachedInstanceError`, pre-existing & already ledgered (QUARANTINE 2026-10-19 "5 standing ledgered reds") |
| Core-C4 `dev.sh --timeout-graceful-shutdown 10` | ✅ PASS (`dev.sh:155`) |
| Important await-conversion callers | ✅ covered by pack evidence (P1 green apart from the ledgered set); `_get_message_count` has **zero** remaining source references |
| Nice-to-have dead code removed | ✅ PASS (`_get_message_count` fully deleted, no stale callers) |

## Remediation (in priority order)

1. 🔴 **Required** — update `tests/test_persistence.py:81` to expect `create_postgres_checkpointer(config, checkpoint_pool_max_size=10, checkpoint_pool_timeout_s=5.0)` (or narrow to `assert_awaited_once` + explicit kwargs). One line, test-side. Unblocks Core-C1.
2. 🟠 **Recommended** — correct the stale measurement comment at `daemon/persistence.py:376-383` to a load-dependent "+0.24 ms measured on the dev DB".
3. 🟠 **Recommended** — tighten F1/F2/F3 (assert the four reason literals + a 4-way distinctness guard; tie the prune-scan pin to `census["scanned_pairs"]`/`max_versions`; build the metrics-route test's app via `daemon.api.create_app()`).
4. 🟢 **Optional** — add `metrics_router` to `PRE_FEATURE_API_ROUTER_LIST` (that census is already stale from the wedge-capture lane; a router-growing lane should update every census).

## House traps surfaced (recorded to KB)

1. **Install-audit cwd trap** — pytest in a worktree dirties a *tracked, foreign-owned* file via `kms_request()`. Attribution must be by appended-record `ts` vs each leg's process window (one worker mis-attributed its own child PID to an "external gate").
2. **Blueprint staleness** — the Core Architecture blueprint's "`pytest_sessionstart` prints `daemon.__file__`" claim is **false** in this tree (no such hook, no root conftest). The out-of-repo proof plugin used here is the working substitute.
3. **`tests/unit/` INTERNALERROR offender** — `test_upgrade_executor_systemd_service.py` monkeypatches `pathlib.Path.exists` at class level and kills xdist report-rendering; run that file solo/deselected (pre-existing).

---

# RE-GATE ADDENDUM — merged tip `958261c2c` (same day; not committed)

**Tree:** `feature/checkpoint-quick-wins` @ `958261c2c` (merge of our fix pass `43da16fe3` + resource-monitor lane `69d767b2a`).
**Merge integrity independently proven:** overlap = **0 files** (`comm` of both sides' changed-file lists); our 36 files byte-identical to `43da16fe3` in the merged tree; their 19 byte-identical to `69d767b2a`. The suspicious-looking `charge.py` change is the M-2 fix itself (`note_checkpoint_read(..., size=)`).

## VERDICT: ✅ **SHIP**

## 1. Targeted packs — T-1 blocker GONE; 443 nodes, 2 pre-existing only, 0 NEW
Both legs (default addopts + `--override-ini='addopts='`) = identical 443-node census, 441 P / 2 F (the standing planner-meta pair; `git diff 9bbef71c1..HEAD -- agents/` empty). `TestGetCheckpointer::test_get_checkpointer_postgres_dispatches_to_pg` passes solo AND in-pack. Ordering-pollution pair proven both shapes (whole-file → 2 fail `Table 'secrets' already defined`; class-scoped → 14 pass); reproduces only whole-file-alone.

## 2. Resource-monitor lane (merged side) — INTACT, 277/277
120 unit + 15 PG-gated + 37 + 15 pack-script + 26 mission_retire + 64 sampler/charge_seams. Real cross-lane seam identified: `charge.py` (ours) ↔ `sampler.py` (theirs) — **shared-package coupling, not file overlap**; proven backward-compatible (`size` kw-only default `None`; their sampler never calls `note_checkpoint_read`; the only production caller is our `checkpoint_adapter.py:239`). Legacy 2-arg path covered by execution (64 passed). 0 PG residue (4 scratch role+DB pairs dropped + verified). Evidence-rule learned: a read-only psql connection bumps DB-dir mtime — table counts, not mtimes, are the valid write-evidence.

## 3. Fix-pass verification — M-2 PASS · M-1 PASS · T-3 (mutation) 5/5 KILLED
- **M-2 one-walk** (counting-proven, 2×2 matrix): (charge on, perf on)=1 · (on, off)=1 · (off, on)=1 · (off, off)=**0**. Same size (1137) threaded to both consumers. Legacy `size=None` self-computes 1137 (not 0). **Brief's phrasing adjudicated:** "gates the whole RECORDING" = TRUE (counters frozen AND zero log lines); "suppresses the walk entirely" = TRUE only in cell 4.
- **M-1 auth matrix** (real minted bearers): auth-ON no-creds **401**; bearer without scope **403** `SCOPE_INSUFFICIENT required:["maintenance:read"]`; bearer with scope **200** + non-zero counters/histogram; auth-OFF **503** `AUTH_NOT_CONFIGURED` with **no metric leaked** (gate fires before render); auth-off-unconfigured **503**. Matches the prompts precedent exactly. Both manifest cages re-pinned + green.
- **T-3 mutation spot-check:** mount-deleted → exactly 1 in-file red (new `test_route_is_registered_on_the_real_app` via real `create_app()`) + 4 cage reds; census-deleted → 4 red at BOTH seams (the previously-vacuous node now KeyErrors); vocabulary-collapsed → 7 red incl. the new `assert 1 == 4` distinctness; M-2 second-walk → `assert 2 == 1`. All 5 KILLED, all cycles closed red→restore→green, original tree byte-identical.

## 4. Broad sanity — 0 NEW branch-caused, 2 fixed, 0 verdict changes
140 deterministic / 31 pack-flake / 1 unrunnable-pre-existing. 27 "new" nodes are all `tests/postgres/` files no prior pack covered, failing in the PG **fixture** (`ensemble_test` has no `public` schema) — control run of 3 pre-existing PG files failed 35/35 identically ⇒ environmental. Disappeared: T-1 pin + T-4 router-list pin (both green solo). P2's offender node is structurally unrunnable under pytest (class-level `pathlib.Path.exists` patch breaks pytest's own report builder) — pre-existing, 4230/4231 nodes verdicted. `git diff 43da16fe3 HEAD -- charge.py` empty ⇒ M-2 survived the merge.

## 5. A/B at base `9bbef71c1` — PRE-EXISTING-SET-CONFIRMED
**26 SAME-FAIL / 1 BASE-ONLY / 0 BRANCH-CAUSED / 0 DIVERGENT. Nothing green-at-base is red at HEAD.** The 26 = prior 24 + the 2 planner-meta nodes (outside the prior inventory, byte-identical both legs). The BASE-ONLY is the router-list pin — RED at base (`At index 28 diff: 'wedge_capture_router' != 'plane_router'`, pin 35 vs actual 36) → GREEN at HEAD (pin 37 = actual 37): **a fixed pre-existing red** (the 2026-10-22 wedge-capture staleness), not a new failure. The prior 25th (branch-caused) node is green on BOTH legs. Rows 19-20 are same-mechanism value deltas (set-repr ordering; api.py 5224→5235 — both ~3.3× over the `<1600` pin). Base worktree removed cleanly.

## 6. Live dev-DB — STILL-HOLDS
Comment correction A/B-proven (stale "+0.4 ms / 0.96→1.40" present at `67f9f1b03`, gone at HEAD; corrected band+CI+semantics in place; note the literal `0.405` never existed in the file — the stale text was the rounded block). Counting semantics exact (`None`→0; `0`→1 same-read / 3 distinct; default 5→1 at 6th). Plan parity **0 non-timing leaf diffs**, 14-node trees byte-identical, stable through generic-plan crossing. TOAST reloptions persist verbatim ×3, parents clean (0 `toast.*`), privilege posture unchanged. Naming correction adopted: the `checkpoint_writes` toast table is **`pg_toast_37208`** (37212 is the toast OID). **Magnitude caveat:** today's delta measured **+0.394 ms [0.368–0.452]** (median-of-medians ≈ +0.36 over 4 reps) vs the comment's +0.229–0.254 — load-dependent (the *prepared* arm reproduces prior absolutes; the excess sits entirely in the un-prepared arm's fresh parse of the 1045-char statement; measurement ran concurrently with the broad sweep). The comment hard-codes no magnitude and labels the cost LOAD-DEPENDENT ⇒ truthful, but the specific band understates a loaded machine.

## 7. No-side-effects — CLEAN (gate's own footprint)
- No daemon started/stopped (8079/8088/8092/4199 empty throughout).
- Foreign `install-audit.jsonl`: our runs appended **84** `kms_issue` lines (15:20:57Z–16:16:34Z, all inside the gate's pytest window; 0 non-kms). **Restored byte-identically** to the pre-gate baseline (sha `807143b4…`, 382/91206, `cmp` clean; `git diff --numstat` back to the foreign workstream's original **36/0**). R5's base-leg appends landed only in the throwaway base worktree's own copy (restored there, then removed).
- Worktrees: R5's base worktree removed cleanly (`git worktree list` clean of `regate-base`); R3's scratch was a `cp -a` copy, deleted.
- HEAD unchanged `958261c2c`; no commits/pushes by the gate.
- ⚠️ **Porcelain has 4 entries, 2 of them NOT ours:** `M CHANGELOG.md` (+2,159 B) and `M docs/runbooks/checkpoint-maintenance.md` (+752 B), written by a **concurrent sibling agent** running a docs pass in this shared worktree at 15:37:02Z — authorship proven by R4 via `ensemble.log:30899` naming the writer and the byte delta matching exactly, and independently corroborated by R5 (mtimes 22:34:30/22:34:40 local). **Not reverted** (would destroy another agent's intentional work). The gate owner must decide commit vs revert. Doc-integrity tests re-run post-write: `1 failed, 56 passed, 1 skipped`, the failure being the pre-existing `EXPECTED_RELEASE_TAG=v0.12.4` pin drift.
- R6 observed one concurrent dev-DB event (`idx_job_idempotency` OID rotation 38191683→38202418) — proven not R6's (read-only sessions, tuple-level diff, 784/785 rows identical).

## Non-blocking observations
1. `tests/postgres/` is unrunnable in this environment (no `public` schema in `ensemble_test`) — environmental, pre-existing, affects 27+35 nodes.
2. `test_upgrade_executor_systemd_service.py::…::test_swallowed_exception_classifies_unavailable_not_legacy` is structurally unrunnable under pytest (pre-existing; file byte-identical base→HEAD).
3. The `+0.229…+0.254 ms` band in `daemon/persistence.py` understates a loaded machine (+0.36 today); consider phrasing as a load-dependent range.
4. The `Table 'secrets' already defined` cross-import pollution remains an open repo-wide import-hygiene defect (order-dependent, not flaky).

## Harness traps recorded to KB this gate
1. **Worktree `.git`-pointer trap**: `cp -a` of a git worktree copies the `.git` *pointer file*; `git -C <scratch> checkout --` then restores **into the original reviewed worktree**, silently. Defuse by `rm -f .git && git init && git add -A && git commit` in the scratch. (Found by R3; my own recipe was unsafe as written.)
2. `find … -exec rm -rf {} +` leaves `.pyc` behind (4210 in one case); `-print0 | xargs -0 rm -rf` required.
3. Read-only psql bumps DB-dir mtime — table counts are the write-evidence, not mtimes.
4. Blueprint's "`pytest_sessionstart` prints `daemon.__file__`" claim remains stale (recorded in gate 1).

---

# FINAL LANDING GATE @ `e356d3657` — ✅ SHIP (merge into latest)

**Tree:** `feature/checkpoint-quick-wins` @ `e356d36577a74a978fa823539bacf1e0b2286f37`. Merge chain: `a86b38c0a` (our `3a1ba1ee2` + their WS-B `bf673fdbe`=latest) + 3 gate-fix commits (`cef870a1c`, `6b7cf8979`, `e356d3657`).

## 1. Merge integrity — PASS
`api.py` carries BOTH wirings (our `metrics_router` import :153 + mount :5029; their lifespan `metrics_gate`/`MetricsListener` :2304-2362). **0 conflict markers** across the touched daemon files; `py_compile` clean.

## 2. Registry semantics — PASS (103/103 independent checks; developer's gate-B tests NOT used as evidence)
(a) canonical≡alias = the SAME series: no `Metric` registered under an alias; identical sample sets for all 7 counters + 3 histogram sub-names; LIVE identity (increment canonical → alias moves 1→2); **`is`-identity holds for all 9 historical Python symbols** (pre-existing callers unaffected). (b) `reset_for_tests()` clears our 7 + theirs (11 seeded series → 0; **0 stale series_keys** across 13 families; route body zeroed). (c) **14/14** = 7 canonical + 7 alias of the re-homed family; the endpoint renders the WHOLE shared registry (**22 `# TYPE` families**). (d) integral vocabulary intact — `65536/131072/262144/524288/1048576/8388608/+Inf`, no scientific notation, no `.0`. (e) `_format_le` re-run by me against BOTH parents' pre-merge `f"{value:g}"`: **DEFAULT_LATENCY_BUCKETS 13 / CHECKPOINT_OP_BUCKETS 17 / EXPOSITION_BUCKETS 11 → 0 diffs each**; integral bounds render as plain integers through the actual exposition path. (f) join-by-label: histogram `le` set == bucket-hits `bucket` set, per-bound string equality, alias side identical.

## 3. Auth matrix — PASS (34/34) · 🟡 one NEW finding
Real `create_app()` + TestClient: (a) 401 `AUTH_REQUIRED` (browser-shape → 302 login redirect); (b) 403 `SCOPE_INSUFFICIENT required:["maintenance:read"]`; (c) 200 Prometheus, 18 non-zero families incl. BOTH `ensemble_checkpoint_read_ops_total{op="aget"} 1` and the alias; (d) 503 `AUTH_NOT_CONFIGURED`, body 88 B, **no `checkpoint`/`ensemble_`/`# HELP`/`# TYPE`**; (e) 503. Gate order proven two ways (source `:137→:138`; runtime spy = **0 render calls on refusal, exactly 1 on grant**).
**🟡 FINDING (WS-B's design, NOT merge-caused, OFF by default):** the `MetricsListener` is a separate stdlib server on **127.0.0.1:9798** — not an app route (`GET /metrics` on the app → 404; `/api/metrics` keeps its gate). Its only guard is a browser-origin check (**no `Origin`/`Referer` ⇒ ALLOW**, so curl/Prometheus always pass). The socket binds **even when the kill-switch is OFF** (switch gates only the body: OFF → 200 empty; ON → 200 with the whole 8,438-B registry, checkpoint metrics present, unauthenticated, loopback-only; non-loopback bind refused in the constructor). ⇒ enabling `ENSEMBLE_METRICS_ENABLED`/`limits.metrics_enabled` publishes the internal checkpoint counters on loopback:9798 with zero auth. Surface to the WS-B owner.

## 4. One-walk invariant — PASS (29/29)
2×2 = **1 / 1 / 1 / 0** (counting wrapper in all 3 referencing modules). Same number reaches both consumers (shared walk 1137 = charge `size=1137` = `ensemble_checkpoint_read_bytes_total` 1137 = charge-registry 1137 = independent re-walk 1137). No second consumer: all `bounded_deep_size` call sites enumerated; `git diff 3a1ba1ee2 e356d3657` over the size seam = **0 lines**.

## 5. Packs + A/B
- **PACK A** (developer's 17): **398/398** both legs, identical node-sets, 0 marker-deselected.
- **PACK B** (prior targeted 14): 405 census → 403 P / **2 F** = the standing planner-meta pair only; ordering-pollution pair proven both shapes again (whole-file 2F/22P, class-scoped 14P).
- **PACK C** (new PG census): **2 deterministic defects D1/D2 — proven PRE-EXISTING-ON-LATEST by execution** (F5: 6 legs; shape (b) byte-identical 0-line diff; **zero verdict differences**). D1 = the shared conftest fence intercepts the child run before the module-local fence → `FENCE_MARKER` unreachable in any env. D2 = the pack's scratch DB missing from the shared `ALLOWED_SCRATCH_DBS` → 10 ERROR → green-at-zero FAIL. Isolation: `PG_TEST_ALLOWED_DBS` export → 1 failed (=D1) / **9 passed against real PG** — the census module itself is healthy. **Not merge-caused; raise as a separate pre-existing-defect ticket on latest.**
- **Resource-monitor lane:** **277/277**; 0 PG residue; `charge.py`↔`sampler.py` seam green.
- **knowledge_tools ×2:** byte-identical at all 4 commits (`bf673fdbe`/`3a1ba1ee2`/`a86b38c0a`/`e356d3657`) — stale pins of the retired tool-level model-forwarding contract (migration `1a40bc56b` 2026-09-26, ancestor of both parents). Not a production bug.
- **F1 wide sweep:** 4,087 nodes → 14 reds, **A/B-proven identical on BOTH parents** (`git archive` legs, no worktree), all 14 fail solo → deterministic; **zero new reds from the merge**. NOTE: `api.py` is now **5,416** lines (5,235 at pre-merge tip) — the standing `<1600` size-census debt worsens with the WS-B merge.

## 6. Live dev-DB — STILL-HOLDS
ANALYZE both branches + threads-only cell re-proved (spy engine; `checkpoint_prune.py` blob-identical to the prior gate). TOAST reloptions persist verbatim ×3, parents clean, **all fingerprints identical across the whole session** (no concurrent OID rotation this time). `prepare_threshold=None` reaches the real connection; census **0** (shipped) vs **1** (old `0`) with same-session `backend_pid` proof; EXPLAIN 14-leaf trees **identical, 0 non-timing leaf diffs**. Prior latency numbers uncontradicted.
**Landscape fact:** `latest` does NOT contain the ANALYZE lane — the landing merge is what carries it in (expected; that is the point of landing).

## 7. No side effects — CLEAN
No daemon (8079/8088/8092/4199/**9798** all empty throughout). Foreign `install-audit.jsonl`: 27 `kms_issue` rows appended by OUR F1 sweeps (3×9 batches @ 18:00/18:09/18:12Z; cause `tests/unit/services/test_kms_lite.py` + `test_key_hardening.py`; 0 non-kms) → **restored byte-identically** to the 382/91206/`807143b4` baseline; `git diff` back to the foreign workstream's original **36/0**. All throwaway worktrees removed cleanly (`fin-ab`, `fin-pgab`); `git worktree list` clean of them. HEAD unchanged; no commits/pushes by the gate.

## Harness incidents (disclosed)
1. **The proof plugin was overwritten mid-gate by a concurrent agent** (F5 replaced `/tmp/cq-gate/cq_proof.py` with a print-only version, losing the hard-fail raise). All runs still pasted `inside_cwd_tree=true`, so the collected evidence holds; the **hard gate has been restored** (appended, preserving F5's extra diagnostics). F2b's earlier wrong-tree catch proves the raise matters.
2. F1 initially used bare `python3 -m pytest` (system 3.14); it re-ran **every** set through the house scrubbed wrapper with identical results. Self-corrected and disclosed.
3. F1 disclosed 4 self-inflicted probe FAILs it found and fixed (probe bugs, not product defects).
4. F2b caught a **wrong-tree run** via the proof (bash `cd` binding in a parallel shell) and re-ran it correctly; also flagged **ambient `POSTGRES_DB=ensemble_prod` (live prod)** reaching bare pack scripts that bypass the scrubbed wrapper — proven non-load-bearing (PG harness reads `PG_TEST_*` only) and all three bare packs re-run scrubbed green.

## Follow-ups (all non-blocking; NONE introduced by this merge)
1. 🔴→`latest` **D1/D2** pre-existing PG-census defects (separate ticket on latest; fixes: add `ensemble_metrics_census_pack` to the shared `ALLOWED_SCRATCH_DBS` or have the pack export `PG_TEST_ALLOWED_DBS`; align D1's `FENCE_MARKER` with the shared fence's actual refusal text — or bypass the shared fence in the child).
2. 🟠 MetricsListener unauthenticated loopback:9798 surface when enabled (WS-B owner).
3. 🟠 CHANGELOG has 0 occurrences of `ensemble_checkpoint_*` (metric names + alias shape undocumented — completeness).
4. 🟠 stale `architecture-recommendation.md:315/:532` anchors (pre-dates this delta; came in with the WS-B doc).
5. 🟢 `api.py` now 5,416 lines vs the `<1600` pin (standing re-baseline debt).
6. 🟢 endpoint-scope wording ("renders the whole registry") — not re-verified this gate.

## LANDING VERDICT: ✅ **SHIP** — `e356d3657` → `latest`
Zero branch-caused failures anywhere in this gate; both defect families found are proven pre-existing on `latest` by execution; every behavioral claim re-proved independently; no side effects.

---

# TEST GATE — PR-1 checkpoint read-cache SUBSTRATE @ `ada12ffb9` (base `f05d8e599`) — ❌ **NO-SHIP (one-line test fix)**

**Scope:** full verification, read-only. 4 workers (G1 identity+reds+PR-2 risk · G2 invariant mutations · G3 eviction+counters · G4 regression+20k-node sweep). Import proof enforced on every run (`CQ_IMPORT_PROOF_HARD_GATE inside_cwd_tree=True`); porcelain was **EMPTY** at gate start.

## The blocker — 5 branch-caused reds (verified at source by the tester, A/B-proven by G4, independently corroborated by the review council)
`tests/unit/tools/test_saver_probe_contention_class.py::TestSaverProbeContentionClass` ×5 — `TypeError: _FakeSaver.aget_tuple() got an unexpected keyword argument 'cache_bypass'`.
**Mechanism (first-hand):** the diff adds `cache_bypass=True` at `daemon/services/readiness.py:1956`; it updated ONE test double (`tests/test_readiness_checkpoint_saver.py` → `aget_tuple(self, config, **kwargs)`) but MISSED the second (`tests/unit/tools/test_saver_probe_contention_class.py:69` → `aget_tuple(self, config)`), whose file is **untouched by the diff**. **A/B:** base `f05d8e599` = 8 passed / HEAD = 5 failed + 3 passed. **Fix (one line, test-side):** add `**kwargs` to that double's `aget_tuple`. Same bug class as the quick-wins T-1 blocker: *a new kwarg at a shared call site requires updating EVERY test double that pins the old signature*.

## Everything else GREEN
- **Item 1 OFF-IDENTITY-CONFIRMED:** base/HEAD-unset/HEAD-=0 → **identical 7-node failing set**, normalized-identical signatures, exact **+52 green** delta (implementer's 243→295 was a narrower pack; G1's wider 362→414 reconciles). All 7 reds PRE-EXISTING: 6 × readiness probe tuple-vs-bare-bool drift (prior lane's enrichment, pins never followed); 1 × armed-absence alist guard — red on the proxy's PRE-EXISTING K2 forwarder delegation (base:680 → HEAD:890, lineno drift only).
- **Item 2 INVARIANTS-BITE (12/13):** hooks, ordering (per-forwarder resolution; skipped invalidation also fires the production post-write backstop), LRU-32 + the exact `>` comparison, the 64 MB budget, shallow-copy (GET-side and PUT-side separately), both vacuity self-checks, both control directions. **The one that doesn't (M3):** drifting the PRODUCTION anti-join DELETE into constant+concat with **identical runtime SQL** took the site out of the detector's reach with **all 52 tests green** — the BinOp-concat control pins a synthetic copy, not production. Also: `wrapper_attrs` is a whole-file scan → the `adelete_thread` leg is over-pinned (satisfied by the SQLite adapter class, not the proxy); the "7 sites" claim asserts only ≥1; the 4 PG + 3 SQLite HOOK claims are pinned only by the behavior pack (deleting a hook left 10/10 AST invariants green).
- **Item 3 INVALIDATION-COMPLETE (37/37):** 12/12 proxy write forwarders, 4/4 PG raw-SQL DELETE sites, SQLite twin (both shapes) all evict; invalidate-BEFORE-delegate proven with a delegate-time observer; post-write emptiness with no backstop ERROR; readiness bypass counts neither hit nor miss and does not populate; bystander thread's entry survives (correctly scoped); no cross-ns bleed; 7/7 documented containers independently copied. MINOR: `config["configurable"]` (2nd level) is shared — live-consumer risk assessed nil; consider copying one level deeper.
- **Item 4 COUNTERS:** canonical+alias render equal; reset clears; **OFF = observable all-misses**; **a HIT still charges** (registry `checkpoint_bytes` +2026 on the hit; the size is the stored PUT-time value, not a re-walk). Default-OFF re-proven by execution; the env var appears in no `.env`.
- **Item 5 (the sweep):** 20,043 nodes / 32 chunks. 147 failing: **OURS 5** · PRE-EXISTING 103 (every file re-run at base, identical node sets, 0 base-only) · ENVIRONMENTAL-PG 29 (`InvalidSchemaName`; proven via merge-untouched `test_smoke.py`; note the brief's second signature `UndefinedTable` did NOT appear) · KNOWN-STANDING 8 (planner-meta ×2 + readiness ×6, all base-leg-cited) · ORDERING 2 (both shapes). Metrics auth matrix all 5 cells green, render-spy 0-on-refusal/1-on-grant, no-leak greps clean; the NEW cache counters render 0.0 = the correct default-OFF observable state.
- **Item 6:** no daemon; no DB writes (code-only PR); audit file restored byte-identically to baseline (`881e133a…`/346/82602) after +37..67 kms_issue rows — 100% attributed to the gate's OWN 5 writer files (each verified by re-run); base worktree + scratch removed cleanly; HEAD unmoved; **final porcelain EMPTY**.

## PR-2 alist-guard risk (G1, premise corrected by source)
Unlike `adelete`, `alist` HAS a deliberate K2 forwarder (MRO fall-through defect; pregel consumes it via `async for`). PR-2 needs no new one, and the cache targets `aget_tuple`/`aget` while readiness bypasses → **the guard cannot newly bite PR-2**; it is already red for an orthogonal reason. Resolve the standing red OUTSIDE this lane before PR-5 flips defaults (scoped allowlist + vacuity companion + implement the promised-but-missing text-grep second pass — the AST scan is `getattr`-evadable).

## Follow-ups (non-blocking)
1. 🔴 the 5-red one-line test-double fix (the blocker).
2. 🟠 M3: re-point the BinOp-concat control at the PRODUCTION anti-join site + pin the count 7.
3. 🟠 scope `wrapper_attrs` to the `_SaverRetryProxy` class body (the `adelete_thread` leg is vacuous).
4. 🟠 add an AST invariant for the 7 `invalidate_cached_reads` seams (currently behavior-pack-only).
5. 🟢 copy `config["configurable"]` one level deeper, or document the 1-level limit.

## VERDICT: ❌ **NO-SHIP for `ada12ffb9` → latest** — land the one-line test-double fix and re-run the 5 nodes (everything else is green).

---

# RE-GATE — PR-1 fix pass @ `b18e13016` — ✅ **SHIP**

C1 **fixed** (8/8 at HEAD and base, solo and grouped; one-file test-only `**kwargs` hunk, verified verbatim + re-run by the tester). OFF-vs-base identity **held**: HEAD failing set == BASE failing set, node-id-identical, **zero new / zero disappeared** (9 = 6 readiness + 2 planner-meta + 1 alist; the alist red pre-existing — same node/assertion, lineno `:680`→`:911` only). The "314P/exactly-7" claim is a **scope artifact**: the read-path scope is 7; the specified pack measures 640P/577P reconciling **exactly by +63 new tests**, all green. `=0` ≡ unset proven.

**Fix-pass regressions all bite (8 mutations, 0 vacuous):** F1 bypass-removed → 3 red; F4 AFTER-half removed at one seam → exactly that leg reds (**per-seam resolution**); F2 charge-basis revert → red with the measured over-charge **2.65×** (matches the ~2.7× claim) — nuance: the pin's "miss" leg is actually a HIT, so hit-vs-miss equality is asserted by proxy (still discriminating; noted for the author). Invariant pack **13/13**; **M3** census 8≠7 reds; **M4** closes the prior gate's over-pin (dual-scan probe: OLD whole-file scan GREEN/vacuous, NEW class-scoped RED); **M5** closes the prior "10 old invariants stayed green under a deleted hook" (under the same mutation only the 2 new M5 tests red).

6-file regression green; ordering pair both shapes both legs; base worktree + scratch removed; audit file untouched (`881e133a…`/346/82602); porcelain = the one sanctioned RESULTS entry; no daemon.

**VERDICT: ✅ SHIP — `b18e13016` → latest.** (Non-blocking note for the author: the F2 pin's miss leg is a proxy assertion.)
