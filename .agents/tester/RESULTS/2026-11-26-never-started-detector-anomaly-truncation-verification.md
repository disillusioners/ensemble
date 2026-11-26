# Independent Verification — `never-started-running` detector + `report_anomaly` truncation fix

- **Date**: 2026-11-26 (2026-11-26T02:04Z)
- **Branch**: `fix/never-started-detector-anomaly-truncation`
- **Worktree**: `/home/nea/ensemble-src-wt-never-started`
- **Base**: `4a17398d2` (origin/latest) · **Reviewed HEAD**: `9099badfb` · **Lane HEAD after T8**: `adbe840f3`
- **Adjudicated range**: `4a17398d2..adbe840f3` — 11 files, +992 −52 (the extra commit is the T8 docs-only PACKS.md line)
- **Role**: independent test + verification (tester). No product code modified, nothing pushed, no release cut.
- **Verdict**: **Defect (1) PASS. Defect (2) PASS on the normal path, with a CONFIRMED residual gap (silent-cut path — §1b).** Lane **NOT READY** — one net-new red (stale test fixture, test-side debt) outstanding.

## Scope decision

Full lane diff touches 5 production modules (`daemon/config.py`, `daemon/repositories/anomaly/models.py`,
`daemon/services/anomaly_service.py`, `daemon/tools/nest_report.py`, `daemon/tools/instance_read.py`)
+ 5 test files. Tier-1 blast-radius gate declared as **D1 + D2-core + D2-seam + D2-fe**;
**D2-wide (61 files / ~1950 nodes) was dropped** — its marginal files are incidental-mention pins
(incl. the 556-node `test_aegis_security_family.py`) and this diff changes no category map, so the
F-1 tool-artifact-drift trap does not apply. Recipe: `.agents/tester/skills/blast-radius-leg/` (§ Gate Tiers, ensure.md).

## Results per task

| # | Task | Result | Evidence |
|---|---|---|---|
| T1 | `TestNeverStartedRunningDetector` (9 nodes) | **PASS 9/9** | 16.74s; cases (a)–(f) + pairing + table-count fence + degraded-leg fail-closed all PASS |
| T2 | anomaly roundtrip + scrub + registration (86 nodes) | **PASS 86/86** (normal path) | 30.3s wall; 3000-char round trip byte-for-byte; loud marker with original length 8519. **Residual gap CONFIRMED — see §1b** |
| T3 | dual-side regression (D1+D2-core+D2-seam) | **FAIL — 1 net-new red** | see Findings §1 |
| T4 | read-only proof of detector | **PASS** | 3 independent legs; 25006 demonstrated |
| T5 | fixture pairing render | **PASS** | populated row, all 4 fields, negative control clean |
| T6 | live read-only render + ba4c32fe | **PASS (0 flagged)**, 2 disclosed RO-grant caveats | corroboration-based; see §Live |
| T7 | scan time-budget coverage | **FINDING** — can starve detector 8; skip is payload-visible, detector-silent | see Findings §2 |
| T8 | PACKS.md doc line 1139 | **DONE** | commit `adbe840f3` |
| T9 | frontend surface | **Daemon-side only** (0 FE files / routers / migrations) | FE jest leg NOT-RUNNABLE (no `node_modules`) — not required |

### T1 — detector unit class (PASS 9/9)

Per-test map: (a) `test_a_positive_wedge_is_flagged` PASS · (b) `test_b_healthy_long_running_with_checkpoints` PASS ·
(c) `test_c_young_instance_inside_pickup_tail` PASS · (d) `test_d_task_with_started_at_is_not_flagged` PASS ·
(e) `test_e_non_running_instance_is_not_flagged` PASS · (f) `test_nudged_instance_with_recent_last_activity_at_is_still_flagged` PASS ·
`test_f_rendered_finding_pairs_lock_with_queued_jobs` PASS · `test_read_only_path_writes_nothing` PASS ·
`test_failed_predicate_probe_does_not_flag` PASS. Liveness from task+checkpoint state only — never `last_activity_at`.

### T2 — truncation fix (PASS 86/86)

`test_anomaly_report_description_roundtrip.py` 2/2 · `test_anomaly_report_scrub.py` 28/28 · `test_anomaly_report_tool_registration.py` 56/56.
- Round trip: `test_3000_char_description_round_trips_complete` — real SQLite engine + real migration UP sections + real
  `AnomalyService`, raw-SQL read-back; asserts `stored == description` byte-for-byte, `"TRUNCATED" not in stored`,
  `result.truncated is False`, column type `TEXT`.
- Loud marker: `test_over_cap_description_is_loud_in_the_stored_row` — input 8519 chars, stored row contains exactly
  `[TRUNCATED: original 8519 chars; stored prefix only]`, `len(stored) == cap` (marker measured INTO the cap),
  lost tail provably absent. The "filed and silently lost half" failure mode is gone.
- Caps confirmed: `description_max_chars` 1000→8000, `suspected_cause_max_chars` 500→2000 (`daemon/config.py`);
  `mark_truncated=True` at 4 authored-field call sites; **no migration** in the diff (TEXT column pre-existed).

### T4 — read-only proof (PASS)

- Session fence: `SHOW default_transaction_read_only` → `on`, asserted as the FIRST statement;
  per-variable sibling asserts `POSTGRES_DB == 'ensemble_dev'`, `ENSEMBLE_SELF_ENV == 'dev'`.
- Table-count fence: 8 tables, every delta `== 0` (instances 64, task 1, checkpoints 0, job_locks 1, job_queue_items 1203,
  job_queues 10, instance_hierarchy 3, message_queue 1).
- Failure mode demonstrated: write probe raised `psycopg.errors.ReadOnlySqlTransaction` — **SQLSTATE 25006**
  `cannot execute INSERT in a read-only transaction`; probe row never landed (delta still 0).
- Static: zero DML/DDL in `instance_read.py`.
- Verdict: **no write attempted.**

### T5 — fixture pairing render (PASS) — VERBATIM

Throwaway PG `ensemble_fix_98bcf0df` (dropped, verified gone). ACTUAL `_build_health_report` imported from the worktree.

```json
{
  "detector": "never-started-running",
  "instances": [
    {
      "instance_id": "inst-positive-neverstarted",
      "agent_id": "worker",
      "status": "running",
      "idle_seconds": 5401,
      "pause_marker": null,
      "detail": "never started: running, zero checkpoint rows, first task 1 still pending with started_at NULL; holds 1 job lock(s) [88245bb9-bd43-42e7-aba8-644877720dda] on queue(s) [default], 2 job(s) queued behind the held queue(s); age 5401s (threshold 2700s)",
      "age_minutes": 90.0,
      "pending_task_id": "1",
      "held_job_locks": [
        {
          "lock_id": "88245bb9-bd43-42e7-aba8-644877720dda",
          "queue_id": "9bc47855-9942-425e-94cf-818158e461ec",
          "queue_name": "default",
          "job_id": "d7e17e59-9dbd-4a12-acc7-a47441619447",
          "project_id": "proj-fixture",
          "agent_id": "worker",
          "acquired_at": "2026-11-25T23:35:58.618431+00:00",
          "lock_slot": 0
        }
      ],
      "queued_jobs_behind_queue": [
        {
          "job_id": "899e50ab-2ac0-4c49-bca2-ec79f47e3d45",
          "queue_id": "9bc47855-9942-425e-94cf-818158e461ec",
          "queue_name": "default",
          "agent_id": "worker",
          "admission_state": "queued",
          "created_at": "2026-11-26T00:55:58.618431+00:00",
          "age_seconds": 601
        },
        {
          "job_id": "effb8198-f508-4482-bd4e-fd20bc99431a",
          "queue_id": "9bc47855-9942-425e-94cf-818158e461ec",
          "queue_name": "default",
          "agent_id": "worker",
          "admission_state": "queued",
          "created_at": "2026-11-26T00:55:58.618431+00:00",
          "age_seconds": 601
        }
      ]
    }
  ],
  "count": 1,
  "evidence_legs": "ok",
  "disagreement_pin": null
}
```

Negative control (healthy long-running WITH a checkpoint row) correctly absent. `age_minutes` / `pending_task_id` /
`held_job_locks` / `queued_jobs_behind_queue` all populated.

### Live — T6 (read-only, ensemble_prod) — VERBATIM

`SHOW default_transaction_read_only` → `on` (first statement, via `ensemble_prod_ro`; no writable fallback; DSN never printed).

Detector row, verbatim:

```json
{"detector": "never-started-running", "instances": [], "count": 0, "evidence_legs": "degraded", "disagreement_pin": null}
```

`degraded` because `ensemble_prod_ro` lacks SELECT on `checkpoints`:
`permission denied for table checkpoints` → the count is a LOWER BOUND, not a verified 0.
Independent confirmation (per-leg, RO-readable): leg A (`status='running' AND created_at older than 45 min`) = **6**;
leg B (+ pending task with `started_at IS NULL`) = **2**; both candidates (`77128da1-…` ari, `ee1d0484-…` developer)
have **executed turns** (started tasks with `started_at`/`completed_at` set + delivered `message_queue` rows) ⇒
LangGraph checkpoints exist ⇒ the `NOT EXISTS(checkpoints)` conjunct is false ⇒ **predicate unsatisfiable ⇒ 0 confirmed**.

Scan budget on prod: two timed runs, wall **1.32s / 1.00s**, `scan_stop_reason='end_of_set'`, `scan_truncated=false` —
the 10s budget is **not** starved on the current prod fleet (the earlier `time_budget` render came from a different run).

**ba4c32fe actionability (REPORT ONLY — no action taken).** Instance `ba4c32fe-…` (blueprinter) status **COMPLETED**;
holder job `f433f265-…` `admission_state=done, terminal_reason=completed`; queued job `49af3c5c-…` still `queued`
(priority 9, source auto-scan, agent_dir `…/releases/v0.31.52/agents/blueprinter`), age ≈ 21.2 h (monotonic vs the
developer's ≈19.6 h capture); queue `system_background_queue` `concurrency_limit=1`. The `job_locks` row itself is
**unreadable to the RO role** (`permission denied for table job_locks`). Two readings remain: (A) stale lock row for a
completed job (the always-on 90s sweep should have deleted it, and the boot sweep logged no cleared-lock line);
(B) **self-deadlock** — the queued job's own lock occupies slot 0, so the sweep can never reclaim it (its job is `queued`,
not `{active,queued}`-excluded). (B) is the only reading consistent with boot-sweep-cleared-0 + sweep-never-reclaimed +
still-queued-21h-after-restart. Corroboration: prod-wide census finds exactly ONE queue with the signature
(≥1 queued, 0 active) — this one. A single decisive SELECT on `job_locks` (needs a granted role) separates (A) from (B).

## Findings

### §1 🔴 NET-NEW RED — stale test fixture (blocks the lane)

`tests/unit/tools/test_module_level_inputs_b.py::test_second_build_payloads_match_base_fixture` — **PASS@base, FAIL@HEAD**,
deterministic (3/3 FAIL at HEAD, 3/3 PASS at base). The branch changed the tool doc from "seven" to "eight" read-only
detectors; the test compares the live OpenAI payload against the **frozen fixture**
`tests/unit/tools/fixtures/module_level_inputs_b_base.json`, which still says "seven read-only detectors". The branch
touched neither the test nor the fixture. The branch's own eight-detector assertions
(`test_category_doc_says_eight_detectors`, `test_health_doc_states_eight_detectors`,
`test_health_report_has_eight_detectors_and_never_repairs`) all PASS at HEAD ⇒ **test-side debt, not a product defect**.
Remedy: regenerate that one fixture. Sibling probes `_a`/`_c`/`_d` pass 71/71 on both legs (blast radius confined to `_b`).
**Not applied by the verifier** — outside the granted write scope, and fixing the red under report would destroy the evidence.

### §1b 🟠 CONFIRMED residual gap — silent cut with NO marker and NO envelope flag (defect (2))

Found via the sibling tidier lane's deferred findings; **independently re-verified by me** (read-only, real SQLite +
real migration UP sections + real `AnomalyService` + `NestReportConfig()`). **This qualifies the defect-(2) PASS.**

**Mechanism (verified, with the sibling claim's stated mechanism CORRECTED):**
- `daemon/services/anomaly_service.py:609` `original_len = len(value)` (PRE-scrub) → `:615` `if mark_truncated and
  original_len > cap:` decides the marker on the PRE-scrub length → `:625/:626` `scrubbed[:cap-len(marker)]+marker` /
  `scrubbed[:cap]` applies the cut to the POST-scrub text. **No cap is re-applied between the decision and the cut.**
- `daemon/services/secret_persistence_scrub.py:637-638` — `mint_marker` (`secret_service.py:177-190`) replaces a
  registered secret's plaintext with `__ENS_SECRET__<uuid4>__` = **52 chars**, with no re-cap before `scrubbed[:cap]`.
- **Correction to the sibling claim**: the growth is NOT from `redact_secret_shapes` — `daemon/_redact.py:283-288` masks
  to `***` and applies `limit` AFTER masking, so it SHRINKS and never emits a 52-char marker.

**Reproduction (real stored result):** description of 7999 chars (just under the 8000 cap) containing a **bare short
registered-secret plaintext** (14 chars, in the scrubber's secret set, matching no redaction regex):
- CASE 2 → +38 growth → 8037 post-scrub → `[:8000]` cuts 37 chars:
  (i) stored_len = **8000**; (ii) `[TRUNCATED:` **ABSENT**; (iii) envelope `truncated` = **False**; (iv) tail
  `"THE-LOST-TAIL"` **silently lost**. The cut also sliced *through* the 52-char marker → the stored row ends in a
  **broken partial marker** (`…a__ENS_SECRET__4cc33d1b-a7fa-4`) — a secondary redaction-integrity issue.
- CASE 1 (no registered scrubber, `api_key=` shape) → shrinks to 7977, no loss, no marker, `truncated=False` — correct.

⇒ The "filed and silently lost half" failure mode **survives on this path** and violates the fix's own contract
("a residual cut carries the loud marker"). Reachability: the filed description must quote a registered secret's
plaintext (e.g. an agent filing an anomaly that echoes a credential) — not exotic, but narrower than the normal path.
**Suggested fix (not applied — product code):** re-apply the cap and re-evaluate the marker decision AFTER scrubbing
(decide on the POST-scrub length), and re-run the secret scrubber over the marker-bearing suffix so the cut cannot
split a marker.

### §1c 🟡 CONFIRMED — duplicate `queued_jobs_behind_queue` entries (defect (1) render path)

`daemon/tools/instance_read.py:1994-2000` extends `queued_jobs_behind_queue` once per held lock with no dedup; the
render at `:2094/:2111/:2118` passes the list through unchanged. Fenced fixture (1 instance, **2 locks on ONE queue**,
1 queued job) → `len(queued_jobs_behind_queue) = 2`, job_ids `['queued1', 'queued1']`.
**My T5 fixture held exactly ONE lock ⇒ this path was outside my fixture's shape** (disclosed). Note the lane's own test
`tests/unit/test_instance_read_shakedown_defects.py:2415` uses a SET comprehension `{job["job_id"] for job in queued}`,
which MASKS the duplication. Fix: dedup by queue (or collect queues first, then one lookup per queue).

### §1d 🟢 CONFIRMED but fixture-only — `lock_id` NULL renders as the literal `"None"`

`daemon/tools/instance_read.py:1951` `"lock_id": str(row[1])` with no None guard (also `:2095` joins `str(lock.get("lock_id"))`).
Fenced fixture with a NULL `lock_id` rendered `repr = 'None'`, type `str`. **Reachability caveat: `job_locks.lock_id` is a
TEXT PRIMARY KEY** (`20260420_000001_add_job_system_improvements.sql:10`; `models.py:513`) ⇒ NOT NULL on production
PostgreSQL; a NULL row is constructible only on SQLite. Defense-in-depth nit, not a live defect.

### §1e 🟠 CONFIRMED — "seven detector classes" survives in the ARI prompt artifact (defect (1) doc surface)

`agents/ari/tools_note.md:1015` still reads `- **\`instance_health\`** — the wedge report (seven detector classes)` while the
branch's tool doc says eight (`daemon/tools/instance_read.py:95`, `:1190`, `:1211`). The artifact is hash-pinned at
`agents/.prompt-artifacts.sha256:32` (`99bd8726…  agents/ari/tools_note.md`); the file's current sha256 still equals the pin,
and `scripts/protection/generate_prompt_artifact_baseline.py --check` exits 1 on drift ⇒ **a prompt-versioning lane must
regenerate the baseline**. Enforcement is ADVISORY (boot `daemon/api.py:1783-1792` is WARN-only; stage
`scripts/upgrade/stage.sh:1040-1082` logs+journals, never gates).
⇒ Together with §1 this is ONE drift class: the branch changed a count-bearing string in the tool doc but did not refresh
the **frozen test fixture** (§1) or the **prompt artifact** (§1e).

### §2 🟠 Scan time-budget can starve the new detector; the skip is silent at detector level

The detector is registered **LAST** (`:2543-2570`); its row-local candidate leg sits inside the per-row walk, and the
budget check at `:2242-2245` breaks that walk. A blown 10s budget therefore skips the detector entirely. Payload-level
visibility exists (`scan_truncated`, `scan_stop_reason`), but **there is no skipped-detectors key** (0 hits repo-wide):
the detector row is byte-identical to a clean run (`count: 0, evidence_legs: "ok"`). A mid-walk stop yields **partial
counts reported as "ok"** (controlled run: 3/7 windows → `never-dispatched-husk` 7 vs full-scan 22, still "ok").
The batched predicate legs and the actionability-context probe run **after** the walk, outside the budget (`:2543-2556`)
— unbounded time after a blown budget. Suggested fix (not applied): a coverage marker on the row
(`candidates_evaluated`) or a distinct `not_evaluated` state when `stop_reason == "time_budget"`; do **not** overload
`evidence_legs: "degraded"` — it means "a predicate leg could not run" and is shared with detector 7.
**Does not reproduce on prod today** (1.0–1.3s, `end_of_set`).

### §3 🟠 Stale line-number seams in `metrics_query/wiring/w2.py`

The branch grew `daemon/tools/instance_read.py` by +379 lines; `w2.py` still cites raw line numbers, and both seams now
point at the wrong code:
- `:2019` — BASE `parent_id = info.get("parent_id") or None` (orphan leg) → HEAD **blank line inside the new
  `_never_started_flagged_rows` docstring**
- `:2092` — BASE `"metadata": {},` (zombie-pending row) → HEAD `ctx = (context or {}).get(iid) or {}` (inside the new loop)

No test asserts the seam content ⇒ no red; but observability attribution is now incorrect. Product code — not touched.

### §4 🟡 Test-registry gaps (reported, not fixed — write scope)

Three changed/new test files have **no PACKS.md row and no pack-script target**:
`test_anomaly_report_description_roundtrip.py` (2), `test_instance_read_shakedown_defects.py` (95),
`test_instance_read_husk_spawn_stamp.py` (21). They ride the broad alphabetical slices only — gate-inadmissible as
targeted coverage. Also a **pack-name trap**: `nest_ops_shakedown_unit_test` does **not** cover
`test_instance_read_shakedown_defects.py` (it targets the two PG integration files).

## Classification of every red (D2-core, 20 files / 521 HEAD nodes / 509 BASE nodes)

| Red | Class |
|---|---|
| `test_skill_capture_observability.py` × 3 (d-pin ×2, m2-pin) | pre-existing on base |
| `test_anomaly_report_models.py::TestTheMigrationCensus::test_the_census_is_121_files_with_121_distinct_prefixes` | pre-existing on base (141≠121) |
| `test_instance_read_shakedown_defects.py::TestF12StatusForwardSeam` × 4 | **pre-existing on base — developer claim verified independently** |
| `test_nest_report_rest_route.py::TestAM1TheRouteSetPin` × 3 | QUARANTINE row 11 — base-attributed, excluded from scoring |
| `test_module_level_inputs_b.py::test_second_build_payloads_match_base_fixture` | **NET-NEW — see §1** |

`^ERROR` count = 0 on both legs. Digest sha256 (GREP-FULL form): HEAD `13941ca6163dc48c…` (9) / BASE `6bf41e334e827056…` (8).
D1 red-receipt overlay: **18 of the 19 HEAD-only node ids are RED at base → GREEN at HEAD** (load-bearing proven);
the 19th (`test_the_default_scrub_is_silent_for_harvested_excerpts`) passes at both by construction — it is a
"must stay silent" negative guard, so base trivially satisfies it (expected, not a defect).

## Method notes (for the next gate dispatch of this lane)

- The D2-core set **cannot run serially inside `timeout 300`** (521 nodes ≈ 13 min serial; the mandated shape hit
  `rc=124` on BOTH legs). Recovery was the recipe-sanctioned `xdist -n 8` **applied identically to both legs**
  (HEAD 92s, BASE 84s) — identical command shape preserved, digests comparable. Carry `-n 8` in the mandated shape,
  or declare the set in shards.
- A **gitless BASE extraction** (`git archive | tar -x`) makes git-shelling tests **pass vacuously** at BASE
  (the row-11 node asserts `git diff` stdout is empty; no `.git` ⇒ empty ⇒ vacuous pass). BASE legs containing
  git-shelling tests need a real detached worktree. Filed as nest anomaly `25d5c587`.
- Two workers independently reported `pytest-timeout`/`pytest-randomly` absent from the canonical venv (one worker
  found pytest-timeout present) — the outer `timeout` carried the guard where the inner timer was unavailable.

## Side effects of this verification

- **T8 committed**: `adbe840f3` — `.agents/tester/PACKS.md` doc line 1139 (`anomaly_report_tool_registration` row):
  `1000-char description cap` → `64-char raw-category cap, the 2000-char cause cap and the 8000-char description cap —
  a residual cut carries the loud [TRUNCATED: original N chars; stored prefix only] marker`. Not pushed.
- Two workers created additive corpus skills (`instance-health-detector-coverage-gap-audit`,
  `prod-ro-role-grant-gap-evidence-capture`) — no product impact.
- A **sibling tidier lane** (code-quality review of the same branch) has an uncommitted
  `.agents/tidier/notes.md` edit in this worktree — **not mine, left untouched**. Its independent cross-check states the
  two commits above touch neither `daemon/` nor `tests/`, so all 10 reviewed product/test files are byte-identical and
  every line citation stands. Its 6 deferred correctness findings were re-verified by me where they touched my verdicts
  (§1b/§1c/§1d/§1e); the remainder remain its findings, not certified by me.
- No daemon booted; port 8079 untouched; no product code modified; nothing pushed; no release cut.
- Canonical checkout `/home/nea/ensemble-src` (detached `bf4bfde6e`) and lane
  `/home/nea/ensemble-src-wt-revive-wedge` untouched.

## Overall status

- **Defect (1) `never-started-running` detector**: ✅ PASS (render-path residuals §1c/§1d/§2, doc drift §1e)
- **Defect (2) `report_anomaly` truncation fix**: ⚠️ **PASS on the normal path** (T2 86/86) — **residual gap CONFIRMED**
  (§1b): a cut can occur with NO `[TRUNCATED: …]` marker and NO envelope `truncated=true` when a registered secret's
  short plaintext is substituted by the longer 52-char `__ENS_SECRET__` marker. The "filed and silently lost half"
  mode survives on that path; the normal path is correct.
- **Lane regression (T3)**: ❌ FAIL — 1 net-new red (stale fixture, §1), owner decision needed
- **ensure.md Tier-1 gate**: scoped dual-side leg executed (D1 + D2-core + D2-seam); D2-fe not runnable, not required;
  D2-wide dropped by scope decision
- **Testing complete**: ❌ NOT READY — three items:
  1. refresh `tests/unit/tools/fixtures/module_level_inputs_b_base.json` (§1), then re-run
     `tests/unit/tools/test_module_level_inputs_b.py` to green;
  2. decide the §1b silent-cut gap (fix = re-apply the cap and re-decide the marker AFTER scrubbing);
  3. prompt-versioning lane must refresh `agents/ari/tools_note.md` (§1e — still "seven detector classes", hash-pinned).
