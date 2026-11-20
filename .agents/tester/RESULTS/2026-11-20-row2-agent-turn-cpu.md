# TESTER GATE — row2 agent-turn CPU lane (2A tier-A de-dup / 2B schema registry / 2C shape-aware chunked counting) — POST-FIX

**Date:** 2026-11-20 (UTC) · **Worktree:** `/home/nea/ensemble-src-wt-row2-int` · **Branch:** `feature/agent-turn-cpu-row2` · **Lane base:** `213ea6c1e`
**Verdict: PASS** — all eight gates executed; zero net-new reds; zero hard-exclusion hits; the packaged-artifact boot smoke RAN GREEN against the NEW staged binary (the lane's "unrunnable" claim was wrong and is disproven here); every mutation pin REDDENED under its mutation and was restored byte-identically; the tree is clean; LIVE is untouched.

**Gate tips (the tip moved once during the gate — every gate is anchored below):**
- `7f2fcc36a` — the tip the dispatch named; GATES 1, 1b, 3, 4, 6, 7 ran here.
- `775acf892` — the FINAL tip. The delta `7f2fcc36a..775acf892` is **docs-only** (`HANDOFF.md`, `.agents/tester/PACKS.md`, `CHANGELOG.md`; **zero executable file touched**), so every code-level gate above carries forward unchanged; GATE 2 was **re-run in full at `775acf892`** and GATE 5 was executed at `775acf892`.
- The doc-only delta `ef42a1e59` also closed the GATE-2 registry-formatting gap the tester found at `7f2fcc36a` (see §3.4) — the gap was **real at `7f2fcc36a`** and is **closed at `775acf892`**.

**Fences:** `.env` → `POSTGRES_DB=ensemble_dev` (NON-prod, asserted before every gate), `ENSEMBLE_SELF_ENV=dev`, `POSTGRES_USER=ensemble_dev_app`; worktree venv only (`.venv/bin/python`, CPython 3.14.7); PG/DSN family scrubbed **per-variable** (`env -u POSTGRES_*` is a silent NO-OP — bash does not glob env names); `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` and `ENSEMBLE_ORPHAN_F1_ENABLED=0` pinned **explicitly** (never `env -u` — that strips the guard and the boot sweep kills LIVE processes). **Zero edits to lane source** (all mutation plants reverted with `git checkout --`, sha256-verified). **No `git push`, no `git stash`, no `git clean`, no merge.** `git status --porcelain` = **empty** at the end.

**Evidence root:** `/tmp/row2-gate/` · **Gate scripts:** `/tmp/row2-gate/run_gate{1,1b_*,2,5,5b,6}.sh`, `/tmp/row2-gate/gate3_drift.py`, `/tmp/row2-gate/bench_prefix.py`, `/tmp/row2-gate/probe_payload_classes.py`

---

## 1. GATE 1 — packaged-artifact boot smoke on the NEW staged binary ✅

The lane declared this "NOT RUN (fail-closed on isolation: `systemd-run --user` has no user bus in an agent shell)". **That claim is false and is disproven here.** The user bus EXISTS — the agent shell only lacked `XDG_RUNTIME_DIR`.

**Invocation** (tip `7f2fcc36a`, binary `/home/nea/agents-ensemble/releases/v0.31.16/ensemble-prod`):

```
export XDG_RUNTIME_DIR=/run/user/1000
export ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 ENSEMBLE_ORPHAN_F1_ENABLED=0
set -a; . ./.env; set +a
# per-variable scrub: POSTGRES_URL DATABASE_URL PGHOST PGUSER PGPASSWORD PGPORT PGDATABASE
#                      SSL_CERT_FILE SSL_CERT_DIR PORT DAEMON_PORT
#                      ENSEMBLE_DATA_DIR ENSEMBLE_CONFIG ENSEMBLE_LAUNCHER_STATE_FILE
#                      ENSEMBLE_UPGRADE_SCRIPTS_DIR
timeout 400 bash scripts/upgrade/boot_smoke.sh \
  --binary /home/nea/agents-ensemble/releases/v0.31.16/ensemble-prod --timeout 90
```

**Result: `PASS: boot smoke OK` (rc=0) — executed TWICE** (unit `ensemble-boot-smoke-00346c` port 19114; unit `ensemble-boot-smoke-004e5b` port 19502, the second run with the script's `TMP_ROOT` preserved so the in-unit fence echo landed in the durable evidence).

| Fence | Observed | Verdict |
|---|---|---|
| Child cgroup | `0::/user.slice/user-1000.slice/user@1000.service/app.slice/ensemble-boot-smoke-004e5b.service` | ✅ isolated **app.slice** transient unit — **NOT** `ensemble-main.service` |
| `--allow-shared-cgroup` waiver | **not passed** (the `systemd-run --user` path was taken) | ✅ no waiver used |
| Launching shell cgroup | `0::/system.slice/ensemble-main.service` (isolation proven BY the child's own cgroup) | ✅ |
| `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` | `0` — pinned in the launching shell AND re-pinned + **asserted** inside the unit (`exit 78` if ≠ 0) | ✅ |
| `ENSEMBLE_ORPHAN_F1_ENABLED` | `0` — same | ✅ |
| DSN family in the boot env | `POSTGRES_URL=<absent> DATABASE_URL=<absent> PGHOST=<absent> PGUSER=<absent> PGPASSWORD= PGPORT=<absent> PGDATABASE=<absent>`; `dsn_family_absent=POSTGRES_URL,DATABASE_URL,PGHOST,PGUSER,PGPASSWORD,PGPORT,PGDATABASE` | ✅ |
| DB | `POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432 POSTGRES_DB=ensemble_dev_scratch POSTGRES_USER=ensemble_dev_app` | ✅ scratch DB, **no prod contact** |
| CREATEDB | **not needed** — `ensemble_dev_scratch` exists and authenticates as `ensemble_dev_app` (`rolcreatedb=false`) → `USE_FRESH=0`, **no scratch DB created**; post-flight query for `ensemble_dev_boot_smoke%` → **0 rows** | ✅ |
| Scratch HOME / DATA_DIR / CONFIG | `HOME=/tmp/ens-boot-smoke-home-rueKG2`, `ENSEMBLE_DATA_DIR=/tmp/ens-boot-smoke-…/data`, `ENSEMBLE_CONFIG=/tmp/ens-boot-smoke-…/config/config.yaml` | ✅ |
| Scratch port | `PORT=19502 DAEMON_PORT=19502` (house range 19100–19999) | ✅ |
| Banner | `Starting Ensemble v0.31.16` | ✅ |
| `/livez` body | `{"status":"alive","uptime_seconds":6.784,"version":"0.31.16"}` | ✅ 200 |
| `/readyz` body | `{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true}}` | ✅ all components true |
| Tracebacks / ERROR / CRITICAL in boot.log | **0 / 0 / 0**; clean graceful shutdown | ✅ |
| 2B boot warm | `[schema-registry] boot warm complete: 237 schema(s) memoised` | ✅ the new registry warms inside the packaged artifact |
| v0.31.8 settings-crash class | `[Settings] applied 2 db override(s)` — `limits.worker_pool_size` 5→7 and `wedge_notice_emission_enabled` → True applied; `smoke_unknown_legacy_key` correctly ignored | ✅ **did not recur** |
| LIVE untouched | `current` → `releases/v0.31.10`; livez 200 before and after | ✅ |

**Hygiene:** the preserved scratch `TMP_ROOT` held the scratch-DB password inside the unit's `boot.sh`; it was **redacted** into `g1_preserved_tmproot/boot.sh.redacted` and the original directory **deleted**. No credential left on disk.

### 1b. House NON-BOOT probe on the new binary ✅ (documented expected outcome)
`env -u POSTGRES_*` (per-variable) + `ENSEMBLE_SELF_ENV=dev` + fresh scratch tree + `PORT=19123` → **rc=3**, banner `Starting Ensemble v0.31.16`, then the documented fresh-SQLite-tree failure at the first PG-only migration:
`daemon.migrations.runner.MigrationError: Migration 20260714_000001 failed: (sqlite3.OperationalError) near "EXISTS": syntax error [SQL: ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS ck_job_queues_queue_type]`
This is the **known pre-existing** migration defect (fresh-SQLite boot breaker, PG unaffected) — **not** a lane regression.

---

## 2. GATE 2 — packs + units ✅

Re-run in full at the **final tip `775acf892`** (identical outcome to the `7f2fcc36a` run):

| Pack / suite | Result | Wall |
|---|---|---|
| `bash test/packs/compaction_tier_a_byte_identity_unit_test.sh` | **158 passed**, 181 warnings · `RESULT: PASS` | 54–59 s |
| `bash test/packs/schema_registry_bind_once_unit_test.sh` | **24 passed** · `RESULT: PASS` | 9–17 s |
| `bash test/packs/tier_b_token_drift_bound_unit_test.sh` | **38 passed** + the 13 shapes × 4 sizes table + the benchmark leg (`RESULT: PASS`) · `RESULT: PASS` | 137–140 s |
| `bash test/packs/frozen_tool_name_discovery_unit_test.sh` | **7 passed** · `RESULT: PASS` | 8 s |
| unit list, 5 files (dispatch's list, + `tests/unit/tools/test_schema_registry.py`) | **213 passed**, 461 warnings | 78–85 s |
| unit list, 4 files (handoff's list) | **196 passed**, 181 warnings | 72 s |

**Reconciliation of the two expected figures:** the dispatch's "196" corresponds to the handoff's **4-file** list (158 + 38); the dispatch's own 5-file command (which adds `test_schema_registry.py`) yields **213** (= 196 + the 17 new 2B nodes). Both were executed and both are green — this is a **figure-label difference, not a count drift**.

### 2.1 Registry (`.agents/tester/PACKS.md`) — house 5-column format + GFM rendering

| Row | 5 columns | GFM table (heading + header + `\|---\|` separator) |
|---|---|---|
| `schema_registry_bind_once_unit_test` (2B) | ✅ | ✅ `## ROW2-2B — schema registry + bind-once` |
| `compaction_tier_a_byte_identity_unit_test` (2A) | ✅ | ✅ `## ROW2-2A — tier-A byte-identical token de-dup` |
| `tier_b_token_drift_bound_unit_test` (2C) | ✅ | ✅ `## ROW2-2C — tier-B policy-gated chunked counting` |

**FINDING (raised at `7f2fcc36a`, CLOSED at `775acf892`):** at the tip the dispatch named, the 2A and 2C rows were **orphan table rows** — 5 columns each but sitting alone between two HTML comment blocks, with **no section heading, no header row and no `|---|` separator**, so they did **not** render as a GFM table (only the 2B row did). The pre-fix tree had the same gap, i.e. the fix pass did **not** close it. The mid-gate doc-only commit `ef42a1e59` ("…PACKS table headers") added the missing `## ROW2-2A` / `## ROW2-2C` headings plus the `| Pack | Invocation | Scope | Last run | Status |` header and separator to both blocks. **Re-verified structurally at `775acf892`: all three rows render as proper GFM tables.** (Rows are present → registered → gate-verifiable.)

### 2.2 MAIN registry cross-check (`/home/nea/ensemble-src/.agents/tester/PACKS.md`)
MAIN is **DETACHED at `bf4bfde6e`**, **559 commits behind** `origin/latest` (`6b4614daa`), **0 ahead** — the row2 registrations are **absent from MAIN's tree**, which is expected: the row2 lane lives on an **unpushed** branch, so "behind" is **not** "unregistered". The registration that ships with this lane is the one on the branch tip `775acf892` (§2.1). MAIN's working-tree `PACKS.md` additionally carries **18 uncommitted lines** belonging to **other** lanes (`systemctl_intent_guard_unit`, `instances_list_mcp_tool_names_wire_shape_unit_test`) — agent runtime state, untouched by this gate.

---

## 3. GATE 3 — DRIFT BOUND, BEFORE/AFTER per shape ✅ (52 rows, **0 violations**)

Methodology = the lane's own (`ROW2-DEEP-DIVE §2.1.4` / `scripts/bench_tier_b_token_drift.py`): 200 KB corpora, 4 KiB chunks, 16 KiB threshold, 13 shapes × 4 sizes. **BEFORE** = the pre-fix plain cut rule, reconstructed **byte-faithfully** from `190f7aa3f^` (`end = cut` — the boundary char carried into the next chunk); **AFTER** = the shipped shape-aware rule. The reference **EXACT** count is computed **independently** (raw `cl100k_base` encode), not from `estimate_tokens`.

**200 KB headline table** (`exact` = independent reference):

| shape | exact | BEFORE | BEFORE drift% | AFTER | AFTER drift% | over(BEFORE) | over(AFTER) | KILL==exact |
|---|---|---|---|---|---|---|---|---|
| prose | 46512 | 46512 | 0.0000 % | 46512 | 0.0000 % | 0 | 0 | ✅ |
| markdown | 65069 | 65079 | 0.0154 % | 65079 | 0.0154 % | 10 | 10 | ✅ |
| minified | 97955 | 97955 | 0.0000 % | 97955 | 0.0000 % | 0 | 0 | ✅ |
| dense_json | 71322 | 71351 | 0.0407 % | 71351 | 0.0407 % | 29 | 29 | ✅ |
| base64 | 21875 | 21875 | 0.0000 % | 21875 | 0.0000 % | 0 | 0 | ✅ |
| digits | 66667 | 66699 | 0.0480 % | 66699 | 0.0480 % | 32 | 32 | ✅ |
| lf_code | 68694 | 68695 | 0.0015 % | 68695 | 0.0015 % | 1 | 1 | ✅ |
| crlf_code | 67008 | 67009 | 0.0015 % | 67009 | 0.0015 % | 1 | 1 | ✅ |
| lone_cr_code | 73626 | 73627 | 0.0014 % | 73627 | 0.0014 % | 1 | 1 | ✅ |
| **lf_run** | 6250 | **6348** | **1.5680 %** | **6250** | **0.0000 %** | 98 | **0** | ✅ |
| **spaces_run** | 1563 | **1612** | **3.1350 %** | **1563** | **0.0000 %** | 49 | **0** | ✅ |
| letters_run | 100000 | 100000 | 0.0000 % | 100000 | 0.0000 % | 0 | 0 | ✅ |
| symbols_run | 25000 | 25000 | 0.0000 % | 25000 | 0.0000 % | 0 | 0 | ✅ |

**Machine-checked invariants over all 52 rows** (`/tmp/row2-gate/gate3_drift.json`):
- **over-count only (never smaller):** `AFTER >= exact` on **52/52** rows ✅ and `BEFORE >= exact` on **52/52** rows ✅
- **no shape worse after the fix:** `AFTER drift% <= BEFORE drift%` on **52/52** rows ✅ (strictly better on the two synthetic breaching shapes)
- **`ENSEMBLE_TOKEN_COUNT_EXACT=1` restores byte-identical exact counts:** `KILL == exact` on **52/52** rows ✅
- **below/at the 16 KiB threshold the default == exact:** all **26** rows at 4096 and 16384 chars → `default == exact` on **26/26** ✅
- `RESULT: PASS`, **0 violations**

---

## 4. GATE 4 — SELF-GATING BENCHMARK: non-vacuity + headline + pydantic ✅

### 4.1 The gate CAN go red (non-vacuity)
The lane's **own unmodified** benchmark (`scripts/bench_tier_b_token_drift.py`, executed via `runpy` with **only the chunker under test swapped**) run against the **pre-fix cut rule**:

```
RESULT: FAIL
  VIOLATION: lf_run@65536: drift 1.6113% > 0.1%
  VIOLATION: lf_run@65536: over-count 33 > n/CHUNK bound 16
  VIOLATION: lf_run@200000: drift 1.5680% > 0.1%
  VIOLATION: lf_run@200000: over-count 98 > n/CHUNK bound 49
  VIOLATION: spaces_run@65536: drift 3.3203% > 0.1%
  VIOLATION: spaces_run@65536: over-count 17 > n/CHUNK bound 16
  VIOLATION: spaces_run@200000: drift 3.1350% > 0.1%
# BENCH EXIT CODE AGAINST PRE-FIX RULE = 1
```
**7 violations, exit code 1** — exactly the lane's claimed figure. Against the **shipped** rule the same benchmark exits **0 with zero violations** (pack leg 2). A gate that cannot go red is not a gate: **non-vacuity PROVEN in both directions.**

### 4.2 The n² headline stays sub-second
| Path | 200 KB unbroken | Notes |
|---|---|---|
| EXACT (kill-switch) | **19.6 – 28.4 s** (shape-dependent: letters_run 19.64 s, spaces_run 27.11 s, lf_run 28.38 s, symbols_run 25.73 s) | the n² term the lane removes |
| CHUNKED (policy default) | **0.3244 – 0.4489 s** | |
| **GATE-D** (200 KB unbroken, chunked, `< 1.0 s` target) | **0.3261 s** (pack run) · **0.3247 s** (lane's published figure, reproduced verbatim in the mutated-pack run) · **0.3701 s** (pre-fix-rule run) | ✅ **sub-second**, ~60–85× faster than exact |

The cut-rule choice does **not** affect the headline: the n² term lives in the EXACT path; the chunked path is O(n) either way.

### 4.3 The pydantic per-assembly count stays near-zero
Independent probe (`/tmp/row2-gate/probe_payload_classes.py`, tree resolution **asserted**: `daemon.__file__` under the probed tree), runtime surface `n_tools=199`:

| Metric | BASE `213ea6c1e` | HEAD `775acf892` |
|---|---|---|
| payload path | `inline_convert_to_openai_tool` | `memoised_registry` |
| assembly 1 — model classes (surface build) | 1787 | 1787 |
| assembly 1 — **model classes in the payload pass** | **780** | **780** |
| assembly 2 — model classes (surface build) | **638** | **50** |
| assembly 2 — **model classes in the payload pass** | **780 (EVERY assembly)** | **0** |
| assembly 2 wall-clock | 0.6059 s | **0.0687 s** |
| `registry_stats` | n/a | `{entries 265, payloads 195, schema_builds 265, schema_hits 255, payload_misses 195, payload_hits 195}` |
| payloads identical across assemblies | ✅ | ✅ |

**"base 780 payload classes EVERY assembly → 0 on the 2nd assembly at HEAD" — CONFIRMED exactly.** The 780 figure is reproduced independently at base (and at the cold HEAD assembly).

---

## 5. GATE 5 — MUTATION PINS ✅ (all three REDDEN; all restores byte-identical)

Each mutation was planted by an exact-anchor textual edit (plant verified by sha256 change), the **owning pack** was run, the failing node ids were captured, the file was restored with `git checkout --` (**never** `git stash`), and the restore was **sha256-verified byte-identical** before a GREEN re-run.

| # | Mutation | Owning pack | Mutated | Restored | Restore sha256 |
|---|---|---|---|---|---|
| (a) | revert the shape-aware cut rule (`daemon/loader.py`) | `tier_b_token_drift_bound_unit_test` | **rc=1** — pytest leg **4 failed / 34 passed**; benchmark leg `RESULT: FAIL` (7 violations) | **rc=0** — 38 passed | `6fd39b4c…6577` PRE == POST ✅ |
| (b) | re-add a whole-list re-encode inside the `emergency_truncate` halving loop (`daemon/compaction.py`) | `compaction_tier_a_byte_identity_unit_test` | **rc=1** — **1 failed / 157 passed** | **rc=0** — 158 passed | `8d950b30…dfae` PRE == POST ✅ |
| (c) | revert the F3 fingerprint-before-count (`daemon/services/instance_messaging.py`) | `compaction_tier_a_byte_identity_unit_test` | **rc=1** — **2 failed / 156 passed** | **rc=0** — 158 passed | `f5026c67…e259` PRE == POST ✅ |

**Exact RED node ids:**
- **(a) — 4 REDs** (matches the lane's claim exactly):
  `test_loader_chunked_counting.py::TestGateBDriftBound::test_synthetic_shape_ceilings[lf_run]` · `[spaces_run]` · `::TestGateBDriftBound::test_absolute_over_count_bound` · `::TestGateCDirectionAndChunker::test_shape_aware_cut_keeps_long_whitespace_runs_whole`
- **(b) — 1 RED:** `test_compaction_tier_a_callcount_pin.py::TestEmergencyTruncateCountingWork::test_no_whole_list_reencode_inside_the_loops`
- **(c) — 2 REDs:** `test_usage_path_tier_a_dedup.py::TestF3FingerprintBeforeCount::test_f3_fingerprint_precedes_count_on_miss_path` · `::test_f3_fingerprint_precedes_count_on_count_mismatch_miss`

**DISCREPANCY (non-blocking, documentation-level) — the lane's "3 REDs" for (c) does not reproduce; the true count is 2.** The mutation applied was the **exact pre-fix shape**, taken verbatim from the fix commit `f6a86de86`'s diff (fingerprint moved back to after the count). Under that shape the **third** F3 pin, `test_f3_fingerprint_precedes_count_on_fingerprint_mismatch_miss`, **stays GREEN by design**: on the fingerprint-mismatch path the pre-fix code ALSO reads the fingerprint *before* the count (inside the cache-hit branch), so the ordering assertion holds. It is a **control pin**, not a third ordering pin. The gate is **non-vacuous regardless** (2 REDs, and the owning pack fails loudly). The lane's mutation-matrix figure should read **2 REDs** for the F3 revert.

**Final tree state after all mutation legs: `git status --porcelain` = 0 lines; tip `775acf892`; no stray commits.**

---

## 6. GATE 6 — QUARANTINE A/B (`tests/test_api.py`) ✅

Base `213ea6c1e` extracted with `git archive` to `/tmp/row2-gate/base-213ea6c1e`; the base leg ran `python -m pytest` **from that cwd** (CWD is `sys.path[0]`, so `import daemon` resolves to the base tree — **proven**: `daemon.__file__ = /tmp/row2-gate/base-213ea6c1e/daemon/__init__.py`; the base `daemon/loader.py` has **0** `_WHITESPACE_RUN_MIN` occurrences vs **2** at HEAD, so the trees are genuinely different).

| Leg | Result |
|---|---|
| HEAD `775acf892` | **41 failed, 6 passed** (47 collected) |
| BASE `213ea6c1e` | **41 failed, 6 passed** (47 collected) |
| **Sorted FAILED-id diff** | **EMPTY — 0 bytes, diff rc=0** |

The pre-fix measurement (41 failed / 6 passed on BOTH sides, empty sorted FAILED-id diff) **reproduces exactly at the final tip**. Attributed to the standing quarantine rows (`tests/test_api.py` stale-expectation ×6 + the whole-tree pre-existing population) — **pre-existing, not a release regression.**

---

## 7. GATE 7 — RELEASE + LIVE ✅

| Check | Value | Verdict |
|---|---|---|
| release-dir binary | `fb96dc01551b43bf136c5f346b5912fe14b21b5ad99ddd0e294acfe42bc00f3f` | ✅ |
| stage-dist binary | `fb96dc01551b43bf136c5f346b5912fe14b21b5ad99ddd0e294acfe42bc00f3f` | ✅ **4-way identical** |
| `manifest.json:binary_sha256` | `fb96dc01551b43bf136c5f346b5912fe14b21b5ad99ddd0e294acfe42bc00f3f` | ✅ |
| `ensemble-prod.build-provenance.json:artifact_sha256` | `fb96dc01551b43bf136c5f346b5912fe14b21b5ad99ddd0e294acfe42bc00f3f` | ✅ |
| sidecar `git_head` / `git_dirty` | `95dfe51a5da6948d092c376796391583e0b62ba4` / `false` | ✅ == the tagged commit |
| `manifest.version` / `binary_version` / `rollback_safe` | `v0.31.16` / `0.31.16` / `true` | ✅ |
| tag `v0.31.16` | annotated object `c7ef4d1f771ebaef11b669a0f32d8cd5e9987f4c` → `95dfe51a5da6948d092c376796391583e0b62ba4` | ✅ |
| tag pushed? | **NO** — `git ls-remote --tags origin 'v0.31.*'` contains **no** `v0.31.16`; **0** remote refs carry either sha | ✅ LOCAL ONLY |
| `origin/latest` | `026833336f7e0625d0b743656ae1ac5464ef4af2` — **unmoved at the time of this check (06:44Z)**. **A FOREIGN lane fast-forwarded it to `6b4614daa` at 06:57:33Z, AFTER this check** (`reflog: update by push`; `026833336` is an ancestor of `6b4614daa` → fast-forward, the "cleanup optional" wave). **This lane pushed NOTHING**: `refs/heads/feature/agent-turn-cpu-row2` → 0 remote refs; `refs/tags/v0.31.16*` → 0 remote refs; no remote v0.31.16 tag collision. | ✅ at check time — ⚠️ **see §8.9 (material for the ceremony)** |
| LIVE `current` | `releases/v0.31.10` | ✅ untouched |
| LIVE `/livez` · `/readyz` | **200** · **200** | ✅ |
| LIVE binary sha256 | `9f068a36adaaf8ae6c256708513f156447f10a94c580b22cb76c93a24934bb3d` | ✅ unchanged |

**No promote, no rollback, no flip, no tag push** — the ceremony owns the flip.

---

## 8. Disclosures / residuals (carried, non-blocking)

1. **The lane's "boot smoke NOT RUN" claim was false** — the user bus exists; only `XDG_RUNTIME_DIR` was missing. The house runbook for the staged-binary boot smoke should carry the `export XDG_RUNTIME_DIR=/run/user/1000` unlock.
2. **PACKS.md 2A/2C rows were not GFM tables at `7f2fcc36a`** — closed by the mid-gate doc-only commit `ef42a1e59`; re-verified at `775acf892`.
3. **F3 mutation-matrix figure is 2 REDs, not 3** (§5) — lane documentation correction; the third pin is a control.
4. **The branch tip moved during the gate** (`7f2fcc36a` → `775acf892`, docs-only). GATE 2 was re-run in full at the final tip; GATE 5 ran at the final tip; the remaining gates ran at `7f2fcc36a` against a tree whose **executable** content is byte-identical to `775acf892` (`git diff --name-only 7f2fcc36a..775acf892` = `HANDOFF.md`, `PACKS.md`, `CHANGELOG.md` only).
5. **The pre-fix binary's boot smoke** was run by the earlier tester wave (`/tmp/sonic-row2-bootsmoke.log`, 05:58Z); this gate ran the **NEW** binary twice, green.
6. Lane-declared untested items **2–12** of the handoff remain as declared (live LLM bind, boot-warm load test, 2A delta-maintained pre-call estimate, 2B residual 50 classes, the mirrored F3 staleness residual, the 41 quarantined reds).
7. **PG usage:** real PostgreSQL was used — `ensemble_dev_scratch` (shared scratch, house-sanctioned) for the boot smoke, `ensemble_dev` via the worktree `.env` for the unit/pack legs. **No production database and no live install were contacted.**
8. **`origin/latest` ADVANCED DURING THE GATE WINDOW (ceremony-relevant).** At the GATE-7 check (06:44Z) `origin/latest` was `026833336` — unmoved, as the dispatch expected. At **06:57:33Z** a **foreign lane** ("cleanup optional") fast-forwarded it to **`6b4614daa`** (`026833336` is an ancestor → fast-forward; 9 commits: cleanup C1/C2/C3/C4/C5). **This lane pushed nothing** — the branch and the `v0.31.16` tag both have **0** remote refs, and there is no remote `v0.31.16` tag collision. Consequences for the ceremony: (a) the L1 freshness baseline the payload was staged against (`origin/latest` = `026833336`) has moved further away, so a **re-stage** would still need the sanctioned `reason=non-tip-tree` override; (b) the **tag → branch-tip offset has grown** (tag `95dfe51a5` → tip `0845a1f14`, now 4 commits: the two doc-only corrections + the tester's results commit + the HANDOFF commit), all documentation-only, so the payload's `git_head` still matches the **tag** exactly; (c) the ceremony must still publish the lineage (`git push origin feature/agent-turn-cpu-row2:latest` + `git push origin v0.31.16`) — **that publish was deliberately NOT performed by this gate.**

---

## 9. Verdict

**PASS.** 8/8 gates executed, every gate anchored to a named tip, zero net-new reds inside the declared blast radius, every mutation pin proven non-vacuous with a byte-identical restore, the tree left clean, and LIVE untouched at `v0.31.10`.
