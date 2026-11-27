# Independent Delta Verification — cycle 1, `never-started-running` detector + `report_anomaly` truncation fix

- **Date**: 2026-11-26 (2026-11-26T08:00Z)
- **Branch**: `fix/never-started-detector-anomaly-truncation`
- **Worktree**: `/home/nea/ensemble-src-wt-never-started` · **HEAD**: `0f5af2a5a` · **Base**: `4a17398d2` (origin/latest)
- **Range adjudicated**: `4a17398d2..0f5af2a5a` — 16 files, +2783/−184, 10 commits. The **cycle** range `76bcbdfda..0f5af2a5a`
  holds **5** commits, not 4: the 5th (`9e2915b9c`) is the docs-only verification-record amendment, which predates
  the 4 code-changing ones (`0bfbe3a1c` detector · `a75d3a759` anomaly · `fdbfc1255` fixture · `0f5af2a5a` tidier notes).
- **Role**: independent delta test + verification (tester). No product code modified, nothing pushed, no release cut.
- **Verdict**: **Defect (1) `never-started-running` detector — PASS. Defect (2) `report_anomaly` truncation — PASS, with the
  prior cycle's §1b residual gap CLOSED by a live test. Lane: READY for merge, with 5 disclosed non-blocking items
  (1 of which needs another lane's action).**
- **Workers**: 15 dispatched (14 evidence legs + 1 setup/census). Every leg returned measured numbers.

## Scope decision

Full delta touches **6 production modules** (`daemon/config.py`, `daemon/repositories/anomaly/models.py`,
`daemon/services/anomaly_service.py`, `daemon/services/metrics_query/wiring/w2.py`, `daemon/tools/instance_read.py`,
`daemon/tools/nest_report.py`) + 6 test files + 1 frozen fixture + 2 docs. It touches **no core seam**
(`daemon/graph.py`, `daemon/manager.py`, `daemon/api.py`, `daemon/services/instance_messaging.py`,
`daemon/services/task_processor.py`, `daemon/services/job_queue_service.py`) ⇒ **ensure.md Gate Tier 1** (≤30 min,
fail-loud), not Tier 2. The whole `tests/unit` census measures **29,918 nodes** (~87 min at the measured rate) ⇒ a
**mechanically declared bounded set** was used for the wider leg (D11): every `tests/unit` file that is delta-touched
OR references a changed module/symbol, plus `test_metrics_family_adjudication.py` — **73 files / 2254 nodes** on HEAD,
72 files / 2223 nodes at base (`test_anomaly_report_description_roundtrip.py` is NEW in the delta, absent at base).
The developer's claimed "2408 passed / 20 failed" set is **UNVERIFIABLE** (no such command/set exists in the lane —
see D11).

## Results per task

| # | Task | Result | Evidence |
|---|---|---|---|
| D1 | Targeted set, per-file counts | ✅ **all 7 legs, every claim Δ=0** | §D1 |
| D2 | Detector case-level proof (a)–(h) + fence + negative control + 2 degradation branches | ✅ | §D2 |
| D3 | MUTATION PROOF, both load-bearing fixes | ✅ **both legs VALID RED → GREEN**, sha256-verified restore | §D3 |
| D4 | Timestamp frame | ⚠️ PASS with a **vacuous-leg finding** (session-tz half inert on SQLite) | §D4 |
| D5 | Freeze pin `22c144000` | ✅ **INTACT** (anchor preserved; pin file only *strengthened*) | §D5 |
| D6 | Fixture-refresh legitimacy | ✅ **LEGITIMATE** — exactly 1 changed leaf, source-traceable | §D6 |
| D7 | Strict read-only proof of the NEW probe path | ✅ **READ-ONLY CONFIRMED** | §D7 |
| D8 | Fixture pairing render (new row shape, >50 queued) | ✅ row captured **verbatim**, cap visibly exercised | §D8 |
| D9 | LIVE read-only render vs `ensemble_prod` | ✅ **0 flagged** — but a **LOWER BOUND**, not a confirmed 0 | §D9 |
| D10 | Scan coverage | ⚠️ substantive claim **CONFIRMED**; the task's literal wording **REFUTED** (field mis-described) | §D10 |
| D11 | Bounded wider regression, HEAD vs BASE | ✅ **RED SETS IDENTICAL** — 0 net-new | §D11 |
| D12 | Tester registry | ✅ 5 registrations + 2 evidence cells refreshed + QUARANTINE pointer re-pointed | §D12 |
| D13 | Frontend/UI surface | ✅ **none touched** — no web check required | §D13 |

## D1 — targeted set (every developer claim reproduced, Δ = 0)

| Target | Measured | Claimed | Δ | Verdict |
|---|---|---|---|---|
| `test_instance_read_shakedown_defects.py::TestNeverStartedRunningDetector` | 22 passed / 0F / 0E / 0S (43.5s) | — | — | ✅ |
| `test_instance_read_shakedown_defects.py` (whole file) | 104 passed / **4 failed** / 0E (143.5s) | 104 + 4 pre-existing F12 | 0 | ✅ |
| `test_instance_ops_behaviour.py` | 58 passed (108.8s) | 58 | 0 | ✅ |
| `test_module_level_inputs_b.py` | **17 passed / 0 failed** (0.51s) | 17, all green | 0 | ✅ (prior net-new red CURED) |
| `test_module_level_inputs_a/c/d` | 43 + 18 + 10 = **71 passed** | 71 | 0 | ✅ |
| anomaly trio (roundtrip · scrub · registration) | 3 + 31 + 56 = **90 passed** | 90 | 0 | ✅ |
| `test_nest_report_rest_route.py` | 19 passed / **3 failed** (quarantined) | 19 + 3 | 0 | ✅ |
| `test_instance_read_husk_spawn_stamp.py` | **21 passed** (26.0s) | 21 | 0 | ✅ |

House invocation (used identically by every leg):
```
cd /home/nea/ensemble-src-wt-never-started && env -u ENSEMBLE_SELF_ENV -u ENSEMBLE_AUTH_ISSUER \
  -u ENSEMBLE_AUTH_DISCOVERY_URL -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_CLIENT_ID \
  -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE \
  -u DAEMON_PORT timeout 300 ./scripts/run_tests_scrubbed.sh <TARGET> -q --tb=short -rfE
```
**The 4 F12 reds are pre-existing and orthogonal** — proven, not assumed: all four are `TestF12StatusForwardSeam`
(`AttributeError: '_RecordingRepo' object has no attribute 'list_child_ids_for_parents'` at
`daemon/services/instance_lifecycle.py:5229`); the branch never touches `instance_lifecycle.py` nor the
`_RecordingRepo` double (diff grep = 0 hits); the real repository method exists at base
(`daemon/repositories/instance/repository.py:446`). Fix belongs to the F12 lane, not this branch.

**The 3 nest_report reds MATCH the QUARANTINED nodes exactly** (QUARANTINE.md row 11, `TestAM1TheRouteSetPin`) —
a deterministic pre-existing stale pin vs pre-lane drift `a67b74066`; the `22c144000` anchor resolves. No net-new red.

## D2 — detector case-level proof: every case (a)–(h) has a real, passing test

| Case | Node id (under `…::TestNeverStartedRunningDetector::`) | Verdict | Assertion |
|---|---|---|---|
| (a) positive flagged | `test_a_positive_wedge_is_flagged` | PASS | `assert self._flagged_ids(payload) == {"nsr-a"}` (L2429) |
| (a′) background task NOT excluded (C-1) | `test_a_background_task_is_surfaced_not_filtered` | PASS | `assert row["pending_task_is_background"] is True` (L2470) |
| (b) healthy w/ checkpoints not flagged | `test_b_healthy_long_running_with_checkpoints` | PASS | `assert "nsr-b" not in …` (L2486) |
| (c) young task not flagged | `test_c_a_young_pending_task_is_not_flagged` | PASS | `assert "nsr-c" not in …` (L2505) |
| (d) `started_at` set → not flagged | `test_d_task_with_started_at_is_not_flagged` | PASS | `assert "nsr-d" not in …` (L2523) |
| (e) not running → not flagged | `test_e_non_running_instance_is_not_flagged` | PASS | `assert "nsr-e" not in …` (L2546) |
| (f) pairing render | `test_f_rendered_finding_pairs_lock_with_queued_jobs` | PASS | lock ids `== ["lock-nsr"]` (L2601); queued job ids `== {"job-blocked-1","job-blocked-2"}` (L2606-2609); `row["context_state"] == "ok"` (L2614) |
| (g) nudged → STILL flagged | `test_nudged_instance_with_recent_last_activity_at_is_still_flagged` | PASS | `assert self._flagged_ids(payload) == {"nsr-nudge"}` (L2700) |
| (h) **fresh task on OLD instance → NOT flagged** (C-1 negative) | `test_h_a_fresh_task_on_an_old_instance_is_not_flagged` | PASS | `assert "nsr-fresh" not in …` (L2730); `count == 0` (L2731) |

**C-1 is structurally honoured**: the age comes from the **TASK** — `daemon/tools/instance_read.py:2081-2082`
`age = now - task_created` / `if age < NEVER_STARTED_MIN_AGE_DELTA` (a `timedelta`, never `int(seconds)`); the
**instance** age is reported but never gating (`:2482-2484`); the candidate SQL carries no timestamp comparison at
all (`:2044-2062`).

**Content-hash read-only fence — NON-VACUOUS, live-proven.** The fence is a test-side content digest over 5 seeded
tables (`tests/unit/test_instance_read_shakedown_defects.py:2387-2415`), compared before/after the detector
(`:3088-3110`). Live control in a throwaway scratch DB:

```
BEFORE content fingerprint    = e959d68904be2f00447107042422b2f5eed9135bccdb156d589e1549ecab8a5f
AFTER-DETECTOR fingerprint    = e959d68904be2f00447107042422b2f5eed9135bccdb156d589e1549ecab8a5f   → FENCE GREEN
NEG-CTRL(1 UPDATE) counts     = identical to before     | fingerprint moved? True  | FENCE RED? True
NEG-CTRL(2 DELETE) counts     = changed                 | fingerprint moved? True  | FENCE RED? True
```
An in-place UPDATE — invisible to a count fence — turns the **content** fence RED; the detector itself leaves both
GREEN. The count fence and the content fence are complementary, as documented.

**Degradation branches — both verified with captured values.**
- (i) context probe RAISES ⇒ finding **KEPT**, context **UNKNOWN**: `count=1`, `context_state='unknown'`,
  `context_degraded=True`, `held_job_locks=None`, `queued_jobs_behind_queue=None`,
  `detail` = `"… holds UNKNOWN job lock(s) [unknown] on queue(s) [unknown], UNKNOWN job(s) queued behind the held
  queue(s) — the actionability probe could not run, so nothing was measured"`; `'0 job lock' in detail` = **False**
  (no unmeasured zero is asserted).
- (ii) predicate probe FAILS ⇒ `count=0` **and** `evidence_legs='degraded'`; candidate probe → `None` likewise.

## D3 — MUTATION PROOF (both load-bearing fixes), isolated throwaway tree

Venue: `git worktree add --detach /tmp/ns-mut-20261126T074532Z 0f5af2a5a` from the lane tree, own venv
(`uv venv --python 3.13 && uv sync --frozen`), import isolation proven (`daemon.__file__` inside the scratch tree),
`git worktree remove --force` afterwards (`git worktree list | grep ns-mut` → 0). Lane tree HEAD + `git status`
**unchanged and empty** before and after.

**LEG A — the A3 truncation gate** (`daemon/services/anomaly_service.py:633-638`).
Original: `effective_original = max(original_len, len(scrubbed))` → `if mark_truncated and effective_original > cap:`
Mutation (one line): decide on the PRE-SCRUB length only (`original_len > cap`). Pre-mutation sha256 `77b81174…`.

| Run | Result |
|---|---|
| baseline | **34 passed** (20.4s) |
| **mutated** | **2 failed / 32 passed** |
| after restore (sha256 == pre-mutation) | **34 passed** (19.6s) |

Red set (exactly 2 of 34), both about the missing loud marker:
- `test_anomaly_report_description_roundtrip.py::test_envelope_truncated_flag_agrees_with_stored_marker` →
  `assert result3.truncated is True, "scrub-grew must be truncated"` → `assert False is True`
- `test_anomaly_report_scrub.py::TestTheScrubGrowthPastCap::test_scrubbed_longer_than_cap_while_original_le_cap_marker_present` →
  `assert truncated is True, "loud marker must be attached"` → `assert False is True`

⇒ **The scrub-expansion coverage DISCRIMINATES the gate — it is not untested.** This closes the prior cycle's §1b
residual gap (a registered secret's plaintext is SHORTER than its 52-char `__ENS_SECRET__` marker, so the scrub GROWS
the text past the cap; the gate must fire on the POST-SCRUB length).

**LEG B — the A1 task-clock** (`daemon/tools/instance_read.py:2074-2083`).
Original: `task_created = _parse_timestamp(row["task_created_at"])` → `age = now - task_created` → threshold compare.
Mutation (one line): read `instance_created_at` instead. Pre-mutation sha256 `731eae19…`.

| Run | Result |
|---|---|
| baseline | **22 passed** (44.0s) |
| **mutated** | **3 failed / 19 passed** |
| after restore (sha256 == pre-mutation) | **22 passed** (45.0s) |

Red set (exactly 3 of 22): **case (h)** `test_h_a_fresh_task_on_an_old_instance_is_not_flagged` →
`assert 'nsr-fresh' not in {'nsr-fresh'}`; plus `test_the_age_boundary_is_exact_to_the_sub_second`
(`{'nsr-edge','nsr-edge-young'}` ≠ `{'nsr-edge'}`) and `test_the_task_age_is_independent_of_the_session_timezone`
(`{'nsr-tz','nsr-tz-old'}` ≠ `{'nsr-tz-old'}`) — same root cause (their fixtures also pair an old instance with a
young task, so the young-task rows become false positives).

⇒ **Case (h) IS pinned.** Corroboration: the class docstring at
`tests/unit/test_instance_read_shakedown_defects.py:2292-2306` carries the repo's own PLANNED MUTATION list naming
this exact leg ("use the INSTANCE clock instead of the TASK clock → (h) fires → RED") — the observed red matches the
declared plan.

## D4 — timestamp frame

`test_the_task_age_is_independent_of_the_session_timezone` → **PASS** (1 passed, 3.71s; node id at
`test_instance_read_shakedown_defects.py:2762`).

- **FINDING (the test's session leg is VACUOUS):** the test declares the hostile frame
  (`_declare_session_timezone(fleet_db, "Asia/Bangkok")`, `:2782`) but the helper is an explicit **no-op on SQLite**
  (`:67-83` returns early unless `dialect.name == "postgresql"`) and the `fleet_db` fixture is SQLite (`:817`,
  `create_engine(f"sqlite:///…/fleet.db")`). Runtime probe: dialect = `sqlite`, the connect handler issues no
  `SET TIME ZONE`; patching only the dialect name makes the *same* handler emit `SET TIME ZONE 'Asia/Bangkok'` →
  SQLite syntax error. ⇒ the +07 frame is **declared-but-unapplied**; there is no `SHOW timezone` evidence.
- **But the load-bearing property is structurally pinned:** the probe SQL (`instance_read.py:2044-2064`) carries **no
  timestamp comparison** (`created_at` appears only in `SELECT` and `ORDER BY`; no `now()`), age is settled in Python
  (`:2074`, `:2081-2082`), `task.created_at` is **naive-UTC** (`_default_created_at_naive_utc`,
  `daemon/repositories/task/models.py:26-37` → `now_utc_naive()` → `datetime.now(timezone.utc).replace(tzinfo=None)`,
  used at `:273`), `_parse_timestamp` coerces naive → aware UTC (`instance_read.py:375-402`), and the other operand is
  aware (`datetime.now(timezone.utc)`, `:2556`) ⇒ **aware-minus-aware, no tz mixing**. The test's own structural
  asserts (`"now(" not in statement.lower()`, no `created_at` comparison regex) passed.
- ⇒ The hazard the leg nominally guards is **impossible in the current implementation**; the leg simply cannot fail.
  A PostgreSQL-backed leg would be needed to exercise it. **OPTIONAL finding, not a blocker.**

## D5 — freeze pin `22c144000`: INTACT

Repo-wide grep (`--exclude-dir=.git --exclude-dir=.venv --exclude-dir=node_modules`) → **5 hits, 4 files**; the live
pin is the only code hit:
```
tests/unit/test_nest_report_rest_route.py:421:
            ["git", "diff", "--name-only", "22c144000", "--", "daemon/routers/metrics.py"],
```
The other four hits are documentary (2 RESULTS files + QUARANTINE.md row 11). The anchor is **byte-identical to base**
and is **not** re-anchored to `76bcbdfda` / `0f5af2a5a` / `4a17398d2` / any HEAD sha; `22c144000` resolves
(`feat(anomaly): F23 metrics family — registry, roster and adjudication census`);
`daemon/routers/metrics.py` is untouched by the branch.

**⚠️ The pin FILE was touched by `a75d3a759`** — the task's "expect untouched" proof path
(`tests/unit/routers/test_nest_report_rest_route.py`) **does not exist**, so that expectation was vacuous. The real
change is a **fail-closed strengthening**, not a laundering: a `pytest.skip` when `.git` is absent (closing the
"base-side pass was vacuous under `git archive`" hole recorded in QUARANTINE row 11) plus a new
`assert completed.returncode == 0` leg (a git failure can no longer produce an empty stdout ⇒ vacuous pass). The
file's sha256 DID change (`88db0c63…` → `533137fb…`) — it must not be described as byte-identical.

## D6 — fixture refresh: LEGITIMATE (1 changed leaf, source-traceable)

`tests/unit/tools/fixtures/module_level_inputs_b_base.json`: **exactly 1 changed leaf** —
`[8][1]/function/description` of the `instance_health` tool:
`"Wedge report — seven read-only detectors…"` → `"Wedge report — eight read-only detectors…"`, character-identical
to the live tool doc at `daemon/tools/instance_read.py:1215` (the detector is the EIGHTH — `:1292-1294`). Roster
**15 → 15, order identical**; no keys added/removed; no type changes. The test file itself was **not** changed by the
branch (correct shape for a frozen-fixture refresh); the fixture was touched by exactly one commit (`fdbfc1255`).

The anticipated cap deltas (description 1000→8000, cause 500→2000) are **absent by construction** — this fixture's
15 tools contain no `report_anomaly` schema — **not** rubber-stamped. **No unjustified key.**

## D7 — strict read-only proof: CONFIRMED

Six statements on the detector path, **all SELECT**, zero DML/DDL (static scan of the whole module: no
`INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE` SQL literal): the oldest-first candidate probe (`:2044-2062`), the
checkpoint-thread probe (`:1708-1710`), the lock query (`:2151-2166`), the `ROW_NUMBER()`-windowed queued-jobs cap
(`:2199-2216`), the per-queue total (`:2217-2222`), the job-active probe (`:2577-2580`).

Three independent no-write legs on a real PG 16.15 scratch DB, session fenced
(`PGOPTIONS='-c default_transaction_read_only=on'`, `SHOW default_transaction_read_only -> 'on'` as the FIRST statement):
(a) table-count fence — every delta **0**; (b) write probe → `('ReadOnlySqlTransaction', '25006', 'cannot execute
INSERT in a read-only transaction')`, probe row did **not** land; (c) static inventory above. Supplementary content
fingerprint: identical before/after (`2031e0f6…`).

**Fail-closed on a raising probe:** forcing `_never_started_candidates` to raise produced
`{"count": 0, "evidence_legs": "degraded", "scope": "complete", "instances": []}` with **ESCAPED EXCEPTION: None** and
the log line `instance_read: never-started candidate probe failed — the never-started-running detector will NOT flag: …`.

## D8 — fixture pairing render (new row shape) — VERBATIM

Scratch DB `ensemble_rt_scratch_ns` + role `rt_verify_ns` on local PG 16 (password in a 0600 file, never printed);
full model surface via `import daemon.manager` + `create_all`; fixture = 1 running instance (6h old) + 1 pending task
(60 min old, `started_at` NULL) + **0 checkpoints** + 1 held job lock + **60 queued jobs** behind the queue.
Constants: `NEVER_STARTED_MIN_AGE_SECONDS = 45*60` (2700s) · `NEVER_STARTED_QUEUED_JOB_LIMIT = 50` ·
`NEVER_STARTED_CANDIDATE_LIMIT = 500`.

```json
{
  "instance_id": "inst-neverstarted-001",
  "agent_id": "worker",
  "status": "running",
  "idle_seconds": 21600,
  "pause_marker": null,
  "detail": "never started: running, zero checkpoint rows, first task 1 still pending with started_at NULL; pending task age 3600s (threshold 2700s), instance age 21600s; holds 1 job lock(s) [lock-ns-001] on queue(s) [default], 50 job(s) queued behind the held queue(s) (capped sample — 60 queued in total)",
  "age_minutes": 360.0,
  "pending_task_id": "1",
  "pending_task_age_seconds": 3600.0,
  "pending_task_age_minutes": 60.0,
  "pending_task_is_background": false,
  "pending_task_is_deferred": false,
  "pending_task_type": "task",
  "held_job_locks": [
    {"lock_id": "lock-ns-001", "queue_id": "q-default-ns", "queue_name": "default", "job_id": "job-ns-001",
     "project_id": "proj-ns", "agent_id": "worker", "acquired_at": "2026-11-26T07:57:59.135626", "lock_slot": 0}
  ],
  "queued_jobs_behind_queue": [
    {"job_id": "queued-job-59", "queue_id": "q-default-ns", "queue_name": "default", "agent_id": "worker",
     "admission_state": "queued", "created_at": "2026-11-26T07:57:00.135626", "age_seconds": 59},
    "… 38 more entries (queued-job-58 … queued-job-11, age_seconds 58 … 11) …",
    {"job_id": "queued-job-10", "queue_id": "q-default-ns", "queue_name": "default", "agent_id": "worker",
     "admission_state": "queued", "created_at": "2026-11-26T07:57:49.135626", "age_seconds": 10}
  ],
  "queued_jobs_total": 60,
  "queued_jobs_capped": true,
  "context_state": "ok",
  "context_degraded": false
}
```
Envelope: `{"detector": "never-started-running", "count": 1, "evidence_legs": "ok", "scope": "complete",
"disagreement_pin": null}`. **The cap is visibly exercised**: 50 sampled of **60** total, `queued_jobs_capped: true`.
**Negative control** (same fixture + ONE checkpoint row) → **0 findings**. Teardown verified (`pg_database` = 0,
`pg_roles` = 0, retired role refuses auth).

Every required field present: `age_minutes` · `pending_task_age_minutes` · `pending_task_id` · task flags
(`pending_task_is_background` / `_is_deferred` / `_type`) · `held_job_locks` · `queued_jobs_behind_queue` +
`queued_jobs_capped`/`queued_jobs_total` · `scope`.

## D9 — LIVE read-only render vs `ensemble_prod`

RO session (`ensemble_prod_ro`, `ensemble_prod`), `default_transaction_read_only=on` asserted as the FIRST statement;
password Fernet-decrypted from the `db_connections` row into a 0600 file, never printed; no write performed.

```json
{"detector": "never-started-running", "instances": [], "count": 0, "evidence_legs": "degraded", "scope": "complete", "disagreement_pin": null}
```
**This is a LOWER BOUND, not a confirmed 0** — `ensemble_prod_ro` has no SELECT on `checkpoints`, so the checkpoint leg
degrades and the detector **deliberately refuses to flag what it cannot verify** (correct fail-closed behaviour).
Independent predicate legs (hand-written SELECT, task clock, cutoff now−45min): (i) 20 running instances older than
45 min → (ii) **3** with a pending `started_at IS NULL` task aged >45 min → (iii) **grant gap, unreadable**.
Corroboration from readable tables upgraded 2 of the 3 to non-wedges (started tasks + completed `message_queue` rows
⇒ turns executed ⇒ checkpoints exist).

**⚠️ The third candidate is THIS GATE'S OWN WORKER** (`ff646965…` = the `ns-d1-husk-spawn-stamp` leg: pending task,
one unprocessed `ready` message, zero started turns) — a **verification artifact, not a product wedge**. The live
prod-shaped scan is therefore **self-contaminated by the verification fleet**, and a "confirmed 0" is unreachable from
the RO lane while the gate's own fleet runs. **Not escalated** (decided by the dispatcher; no privileged path used).

Grant gaps met: `checkpoints`, `checkpoint_blobs`, `job_locks`, `instance_ui_prefs` (the last shimmed with a direct
`SELECT` over `instances`, disclosed). **ba4c32fe re-capture (report only, no action):** status **completed**
(created 2026-11-25T02:37:53Z, last_activity 2026-11-26T01:02:15Z); queue `system_background_queue`
(concurrency_limit=1); **0 jobs queued behind** that queue; its own job_queue_item `admission_state='done'`; the
`job_locks` row remains **unreadable to the RO role** ⇒ the prior cycle's reading (A) stale-lock vs (B) self-deadlock
is still **unresolved** and needs a role with SELECT on `job_locks`.

## D10 — scan coverage: substantive claim CONFIRMED, task's literal wording REFUTED

Fixture: **1201 instances** (1200 filler + 1 candidate) + 1 aged task + 0 checkpoints, so the candidate sits at
walk-offset **1200** — beyond the 1000-row window, exactly where a truncated walk drops rows.

| Variant | `count` | candidate present | `scope` | `scan_truncated` | `scan_stop_reason` |
|---|---|---|---|---|---|
| budget exhausted **before** window 1 (`FLEET_SCAN_TIME_BUDGET_SECONDS = 0`) | **1** | ✅ | `complete` | `true` | `time_budget` |
| budget exhausted **after** window 1 (walk read only **1000 of 1201**) | **1** | ✅ | `complete` | `true` | `time_budget` |
| control (budget not exhausted) | 1 | ✅ | `complete` | `false` | `end_of_set` |

**Independence holds on four grounds:** the probe runs *after* the walk loop (`:2939-2947` vs the loop's
`:2628-2802`); the probe body has **zero** time/deadline/budget references (only its own `LIMIT :cap`); the walk's
truncation lives in a **different field pair** (`scan_truncated`/`scan_stop_reason`, `:2981-2984`); and the code
states the intent at `:2791-2797` ("a truncated walk drops the OLDEST rows first — exactly where the aged
never-started candidates live").

⇒ **The prior cycle's §2 defect (a blown budget silently skipping the detector while the row stayed byte-identical to
a clean run) is FIXED and now PROVEN on real PostgreSQL.** But `scope` is the **candidate probe's** coverage
(`:2105` → `:2503` → `:2962`; docstring `:2344-2348`), not the walk's — the repo's own pins agree (`:2853` asserts
`scope == "complete"` under budget=0; `:2864-2869` asserts `"partial"` only when the **probe** hits its cap, which
the worker reproduced by forcing `NEVER_STARTED_CANDIDATE_LIMIT = 1`). **Adjudication: the task's premise
mis-describes the field; the implementation is intentional, documented and pinned.** Making "the walk was truncated"
visible *on the detector row* would be a NEW field, not a fix to `scope`.

## D11 — bounded wider regression: RED SETS IDENTICAL

Declared set (mechanical): delta-touched test files ∪ test files referencing the changed modules/symbols, plus
`test_metrics_family_adjudication.py` ⇒ **73 files / 2254 nodes** (HEAD) and **72 / 2223** (base; the NEW
`test_anomaly_report_description_roundtrip.py` is absent at base). Base leg ran in a **real detached worktree** at
`4a17398d2` with its own venv (a gitless `git archive` extraction would make git-shelling asserts vacuous AND would
make the freeze pin SKIP — the comparison would be corrupted either way).

| Leg | Files / nodes | Passed | Failed | Skipped | Wall |
|---|---|---|---|---|---|
| HEAD `0f5af2a5a` | 73 / 2254 | 2237 | **16** | 1 | 176.7s |
| BASE `4a17398d2` | 72 / 2223 | 2206 | **16** | 1 | 174.8s |

Three-way diff: **HEAD-only ∅ · BASE-only ∅ · both = 16** — all pre-existing, all deterministically re-proven
**3× per leg** (no flakes, no masking). The 16: `test_metrics_family_adjudication.py::TestNoProducerInstrumentation::test_the_daemon_diff_is_confined_to_the_allowed_files` ·
`test_metrics_family_w3.py::TestF21Journal::test_the_conditionality_evidence_is_recorded_in_the_plan` ·
`test_engine_diff_audit.py::TestEngineDiffAudit::test_daemon_diff_is_confined_to_ownership_map` ·
`test_anomaly_report_duty_prompts.py::TestTheSealRePin::test_every_sealed_row_matches_the_real_bytes` ·
`test_anomaly_report_grant_pins.py` ×3 · `test_instance_read_shakedown_defects.py::TestF12StatusForwardSeam` ×4 ·
`test_nest_report_rest_route.py::TestAM1TheRouteSetPin` ×3 · `test_persona_neutrality.py` ×1 ·
`test_post_restart_arm_notify_no_parallel.py` ×1.

**`test_metrics_family_adjudication.py` classification (as commissioned): PRE-EXISTING RED ON BASE**, same node id
both legs — base *"Left contains 53 more items"* → HEAD *"Left contains 56 more items"* = **+3, exactly the
developer's claim**; the 3 new offenders are `daemon/repositories/anomaly/models.py`,
`daemon/services/anomaly_service.py`, `daemon/tools/nest_report.py` (`config.py` is allowlisted). **Reproducible.**

**The "2408 passed / 20 failed" claim: UNVERIFIABLE and suspect.** No such command/set exists in
`.agents/tidier/notes.md`, `.agents/shared/planning/` or the prior verification RESULTS; and the only `2408 passed`
string in the tree is a tester RESULTS note warning that **an xdist shard can print a valid-looking summary while
416/2832 nodes are lost** (masking FAILED ids) — the figure has the exact signature of an incomplete shard. My
measured set (73 files / 2254 nodes) is the honest comparison.

## D12 — tester registry changes (this lane's own writes)

1. **New newest-first block** at `.agents/tester/PACKS.md:1-19` — 5 named packs, with the mechanical census, the
   `.venv` precondition and the runner's entry-gate contract stated in the block preamble:
   `never_started_detector_unit_test` (108 nodes) · `anomaly_report_description_roundtrip` (3) ·
   `instance_read_husk_spawn_stamp_unit_test` (21) · `nest_report_rest_route_unit_test` (22) ·
   `module_level_inputs_unit_test` (88 = 43+17+18+10, plus the frozen `_b` fixture).
   *Expansion over the prior cycle's list of 3 is deliberate and disclosed*: the 3 prior-residual files plus
   `test_nest_report_rest_route.py` (changed by `a75d3a759`) and the `test_module_level_inputs_*` family (owner of
   the refreshed fixture).
2. **`anomaly_report_tool_registration` evidence cell refreshed** (`PACKS.md:1158`): 2026-11-05 **52/52** →
   2026-11-26 @ `0f5af2a5a` **56/56**, node count 52 → 56, with the staleness reason recorded.
3. **`anomaly_report_scrub` evidence cell refreshed** (`PACKS.md:1182`): 2026-11-05 **21/21** → 2026-11-26 **31/31**,
   node count 21 → 31, with the mutation-proof note.
4. **QUARANTINE.md row 11 pointer re-pointed**: `test :405-416` → `test :405-428` (the branch's +11 lines moved the
   region; the `22c144000` anchor untouched — the prior cycle's F-2).
5. **New LESSONS file**: `.agents/tester/LESSONS/2026-11-26-harness-traps-never-started-delta-gate.md` (7 durable
   harness traps).
6. **`.venv` precondition recorded** — the documented invocation needs `.venv/bin/python`, and **this worktree HAS one**
   (CPython 3.13.15, created 2026-11-26 03:24 by the prior cycle's phase-0 baseline, untracked/gitignored). The
   commission's "this worktree has no .venv" note is **stale**; the honest statement is that `.venv` is **not part of
   the repo**, so a fresh checkout needs `uv venv --python 3.13 && uv sync --frozen` — recorded in the new block.

Integrity: the pre-existing 5,758 lines of PACKS.md are **byte-identical** after the prepend (`cmp` of the tail
against a pre-edit backup); `git status` shows exactly `M .agents/tester/PACKS.md`, `M .agents/tester/QUARANTINE.md`
(+ the new LESSONS/RESULTS files) — **no product file touched**.

## D13 — frontend / UI surface: NONE TOUCHED

`git diff --name-only 4a17398d2..0f5af2a5a -- frontend/` → **(no output)**. All 16 changed files sit under `daemon/`,
`tests/`, `.agents/`; the "outside daemon/ + tests/ + .agents/" grep is empty; no migration, no template, no asset.
**A focused web check is NOT required.**

## Findings register

| ID | Sev | Finding | Location | Action |
|---|---|---|---|---|
| F-1 | INFO | The freeze-pin **file** was modified by `a75d3a759` (a fail-closed strengthening: `.git`-absent skip + `returncode == 0` assert). The commission's "expect untouched" proof path did not exist. | `tests/unit/test_nest_report_rest_route.py:405-428` | None — do not describe the file as byte-identical |
| F-2 | INFO | QUARANTINE row 11's line range was stale after the branch's +11 lines. | `.agents/tester/QUARANTINE.md:11` | **Fixed this cycle** → `:405-428` |
| F-3 | INFO | The `session_timezone` leg is declared-but-unapplied on the SQLite fixture ⇒ vacuous as a guard. | `tests/unit/test_instance_read_shakedown_defects.py:2762-2825` | Optional: a PG-backed leg |
| F-4 | INFO | The prior cycle's verification record predates the two cycle repairs and understates status ("Product code — not touched"; "1 net-new red"). | `.agents/tester/RESULTS/2026-11-26-…-verification.md` | Superseded by this record (kept as history) |
| F-5 | INFO | `agents/ari/tools_note.md:1015` still says "seven detector classes" while the live tool doc says eight. The branch deliberately touched no `agents/` file; the artifact is hash-pinned. | `agents/ari/tools_note.md:1015` | **Prompt-versioning lane** (DB-versioned, hash-pinned) — out of this branch's remit |
| F-6 | INFO | `held_job_locks`/`queued_jobs_behind_queue` render `null` both when the context probe **failed** and when it **measured zero**, contradicting the `_NeverStartedContext` docstring (`instance_read.py:1975-1978`); only `context_state`/`detail` disambiguate. | `daemon/tools/instance_read.py` | Out of this lane's predicate (anomaly filed by the D10 worker) |
| F-7 | INFO | `/home/nea/ensemble-src` (detached HEAD `bf4bfde6e`) carries uncommitted edits to its own `.agents/tester/PACKS.md` + `QUARANTINE.md` — the known by-design drift of that tree. My registry edits target the **lane** copy. | main checkout | None — disclosed |

## Side effects of this verification

- **No product code modified; no commit to any product path; nothing pushed; no release cut; no daemon booted; ports
  8079/8088 untouched.** The lane tree's only modified files are tester-owned (`.agents/tester/PACKS.md`,
  `.agents/tester/QUARANTINE.md`, this RESULTS file, the new LESSONS file).
- Scratch artefacts: throwaway worktrees `/tmp/ns-mut-20261126T074532Z` (mutation venue) and the D11 base worktree
  — both removed and verified removed; scratch PG databases `ensemble_rt_scratch_ns`, `ens_nsrt_scratch_test_*`,
  `nsr_scratch_*` — all dropped and verified gone (`pg_database`/`pg_roles` = 0), prefix-fenced, nothing else touched.
- **LIVE DB: read-only only.** No write to `ensemble_prod`; the RO password never printed; no DSN ever printed.

## Overall status

- **Defect (1) `never-started-running` detector**: ✅ **PASS** — 22/22 class, all cases (a)–(h) pinned, C-1 honoured
  structurally, read-only proven 3 ways, fail-closed proven on a raising probe, cap/dedup and the new row shape
  exercised live on real PostgreSQL, budget-independence proven, and the task-clock mutation drives case (h) RED.
- **Defect (2) `report_anomaly` truncation fix**: ✅ **PASS** — 90/90 trio, the prior §1b silent-cut residual now has a
  **passing** scrub-expansion pin, and the A3 gate mutation drives it RED ⇒ the pin has teeth.
- **Lane regression (D11)**: ✅ **PASS** — red sets IDENTICAL, 0 net-new, 16 pre-existing on both legs.
- **Testing complete**: ✅ **READY to merge** — with 5 disclosed non-blocking items, of which **F-5 needs the
  prompt-versioning lane** (the `agents/ari/tools_note.md` "seven detector classes" drift — explicitly out of this
  branch's remit) and **F-3/F-6 are optional hardening**.
