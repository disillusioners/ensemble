# Independent Verification — skill-keeper confinement fix (`fix/skill-keeper-confinement`)

- **Date**: 2026-10-22 (22:24Z → 23:2xZ)
- **Target**: `/home/nea/ensemble-src-wt-skill-keeper-fix` · branch `fix/skill-keeper-confinement` · HEAD `95b16b265604b1a4f0cbfaee20bc291191c2f107` · base `66d8750448f52a1e40ac82c7e58cbc425c566122` (= `origin/latest`) · 17 commits · 19 files · +2411/−113 · tree clean · nothing pushed
- **Method**: 1 recon lane + 9 checklist lanes + 1 severity-scoping lane + 1 independent fence audit + 6 scoped packs (one per worker, `timeout 300`, per-variable env scrub). Real `ensemble_dev` @ 127.0.0.1:5432 (PG 16.15) wherever a DB was touched; no daemon ever started (`:8079` empty throughout); prod `:9797` untouched.
- **VERDICT: 🔴 FAIL — one blocking defect (HIGH, execution-gate termination), independent of everything else.** 17 of 22 checklist items PASS, 4 PASS-with-caveat/deviation, 1 FAIL (items 7(i)+20, single root cause). Zero branch-caused failures in any scoped pack. **Not stage-ready; fixes go back to the developer.**

---

## 1. The blocking defect (items 7(i) & 20) — refusal that never terminates

**Claimed:** queued `skill_evolution` row with the lane flipped OFF → `FAILED` + truthful error, no spawn, no collateral.

**Observed (real PostgreSQL 16, real `JobProcessor` + `JobQueueService`, 6 drain passes + 2 injected probe failures):** the row stays **`queued`**, `failed_at=NULL`, `terminal_reason=NULL`, `retry_count=0` — refused **8×**, forever. The truthful WARNING is logged but **never persisted** (there is no `error_message` column to persist it into). Spawn count for the row = 0 (the passing half). The unrelated `task` job in the same drain completed normally (`queued → active → done`, spawn_calls=1), so no collateral damage — the failure is precisely "refuses forever instead of failing once".

**Root cause (two independent proofs; the branch's own pin cannot see it because it mocks `complete_job`):**
- `daemon/services/job_processor.py:1177` runs the lane gate **before** `start_job` (`:1274`), so the row is still `admission_state='queued'`;
- it then calls `complete_job(..., FAILED, error=…)` at `:1210`;
- `_finalize_terminal` only transitions `active` rows — a queued row sets `_dispatch_skipped = True` (`daemon/services/job_queue_service.py:3081`) and returns `(job_id, "")`; `complete_job` returns `None` at `:4799-4807` → **no write, error dropped**. This is the same class (`B3`) `start_job` already fixed by routing queued→done through `atomic_transition(from_status='queued', …)`.

**Second defect (derived): head-of-line starvation.** `job = pending[0]` (`job_processor.py:1156`) then `continue` (`:1217`) advances the **queue** loop, never the job loop. Selection is `ORDER BY priority DESC, created_at ASC` (`repository.py:1613-1619`); `priority` is write-once at INSERT and `created_at` immutable ⇒ **the stuck row is permanently position 0** and starves every later `skill_evolution` row on `system_parallel_queue` (a second probe job appeared **0 times** in the whole run).

**Severity: HIGH** (source-only scoping lane, corroborating the runtime observation):
1. **No reaper selects `queued` for this row.** Every terminal writer that admits `queued` is `job_type='message'`-scoped (`finalize_mirror_job_at_completion` `repository.py:2317/2364-2372`, `reconcile_terminal_message_mirrors` `:2569/2577-2584`), `instance_id`-scoped (instance-termination cascade `instance_lifecycle.py:5341`; observer finalize `job_feedback_observer.py:4333` → `repository.py:604` — `skill_evolution` rows carry `instance_id=NULL`, `skill_job_dispatcher.py:499-510`), gated on the legacy `status` column **dropped** by migration `20260628_000002` (boot backfill `manager.py:8398-8420` raises `UndefinedColumn`, swallowed at DEBUG), or operator-only (`cancel_job` `repository.py:3859`; `batch_cancel_queued` `:4022` — sole caller `POST /api/jobs/cleanup`, wired to **no periodic sweep**). `mission_strand_healer.py` does not touch `job_queue_items` at all; `boot_epoch.py` / `message_queue_boot_recovery.py` are message-queue-only; `JobRecoveryService` selects on `ACTIVE` (`repository.py:1563`).
2. **No attempt counter can ever move.** `atomic_retry` (`repository.py:2003-2021`) requires `admission_state='done'` + `failed_at IS NOT NULL`; both decision-layer guards (`job_retry_engine.py:311-315`, `job_queue_service.py:2801-2803`) reject `queued`/`failed_at=NULL` at the first predicate. No dead-letter, no retry budget, no escalation — the only exit is an operator action.
3. **Unbounded churn:** the loop wakes every `_poll_interval` (default 30 s) → one WARNING + one no-op `complete_job` per stuck row, per wake, forever.
4. **Reachable in realistic windows** (producers are fully gated when the lane is OFF, so the row must pre-date the flip):
   - **(a) lane ON → flip OFF + restart** — the queued row survives the restart (plain PG row) and is then refused forever;
   - **(b) upgrade-in-place onto this build** — **the live build (`origin/latest` `66d8750448`) has NO gate at all** (verified: `git show origin/latest:daemon/services/skill_job_dispatcher.py` — capture switch only; `job_processor.py` / `manager.py` — zero `skill_evolution`/`evolution_lane_enabled` hits), so any `skill_evolution` row queued by the current live daemon becomes stranded at promote. **Aggravating, host-specific:** `system_parallel_queue` is PAUSED right now, so rows pile up `queued`; **unpausing after the promote is what converts the pile into permanently stranded rows.**
5. **Aggravating (semantic):** because the row stays `queued`, the kill-switch does **not neutralise** already-admitted Tier-3 mutation jobs — it *defers* them; they execute on a later lane re-enable.

**Suggested fix shape (for the developer; nothing was changed by this commission):** route the refusal through the queued-row terminal path `start_job` already uses (`atomic_transition(from_status='queued', …)`) instead of `complete_job` — i.e. write FAILED + a truthful `terminal_reason` for the queued row, so the row exits the queue exactly once. A pin that does **not** mock `complete_job` (assert the row's terminal state in a real DB, across ≥3 passes, plus a second job proving no starvation) should accompany it. The council flagged this class as F1-HIGH; this commission confirms it on real PostgreSQL and adds the starvation + promote-window aggravators.

---

## 2. Per-item results (all 22 checklist items)

| # | Item | Result | Key evidence |
|---|---|---|---|
| 1 | Lane OFF default | **PASS** | `enqueue_evolution` → `None`; `job_queue_items` 1142→1142; `skill_evolution` 0→0; cooldown `{}`; **0 SQL statements even attempted** (SQLAlchemy cursor recorder); 0 affirmative admit markers at root DEBUG (the only "admit" string in the module is the *negated* refusal; no success-path log exists) |
| 2 | Invalid env values | **PASS-WITH-DEVIATION** | `banana` → `ValueError` ✓; ON tokens `1/true/TRUE/yes/on` all accepted (bare `1` accepted; each created exactly 1 real row). **`""` and `" "` do NOT raise** — they silently defer to the config default (`strip()`→empty→return default, `skill_job_dispatcher.py:104-108`). With the shipped default `False` they cannot enable the lane, but with config `True` they ADMIT (proved). Operator footgun recorded |
| 3 | Precedence matrix | **PASS** | all five cases via the real `load_config`: env `0`/`false` beat config `True`; env `1` beats config `False`; key absent / section absent → REFUSE |
| 4 | Dedup-hit cooldown stamp | **PASS** | stamp `None → 314299.691…` on dedup-hit (second leg `Δ=1.222 s` at 1 s cooldown); next in-window FIX refused **by the cooldown** with the DB enqueue counter unchanged 2→2 (cause isolated from dedup); cross-producer: tool stamps → HTTP **503**, `manager._skill_job_dispatcher is router._skill_job_dispatcher` → **True** (same object, shared RAM state); control (different pair) → **202** |
| 5 | `fix_cooldown_seconds=0` via real wiring | **PASS** | env → config 0 → wiring log `fix_cooldown_seconds=0` → `dispatcher._fix_cooldown_seconds=0`, no post-construction patch; two back-to-back FIXes both admitted (2 enqueue calls); contrast at 1800 → second refused, counter stays 1. Nuance: at cooldown 0 the 2nd collapses onto the 1st row via the same `idempotency_key` while non-terminal (1 row; 2 rows only after terminal + `#<hex8>` suffix) |
| 6 | Key shape | **PASS** | `skill_fix:<project>:<skill>` exact; differing project/skill → different keys; `project_id=None` → real system-default project token `71931ae0-…` (not literal `"None"`; a project literally named `None` keys on its own uuid); non-FIX (`skill_analysis`, `DERIVED`) → `idempotency_key=None`. `normalize_project_id` is identity for real ids (case preserved, whitespace NOT stripped, underscores untouched); padded ids fail loudly at enqueue |
| 7 | Lane OFF after queueing | **FAIL (i)** / PASS (ii) / PASS (iii) | (i) row never terminal — §1; (ii) unrelated task job completed normally in the same drain; (iii) `retry_job` carries `job_type` (`skill_evolution`→`skill_evolution`) and preserves the key as prefix + `#<hex8>` suffix (documented intent, `job_queue_service.py:2836-2841`) |
| 8 | Fallback parity (dispatcher unwired) | **PASS** | ON: cycle 1 → 1 row; cycle 2 → same 1 row; cooldown forced 0 → 2 more cycles → still 1 row (key-only dedup). OFF: total **0** across 2 cycles. ⚠️ parity gap: the fallback duplicates the key f-string (`manager.py:6547-6548`) and does **not** call `normalize_project_id` — different key space for an un-normalized id (unreachable today: falsy ids `continue`), but M-4 asked for equivalent behaviour |
| 9 | Forced-resolve guard via real router | **PASS** | `comparisons=2` → **HTTP 400** `{"detail":{"error":"Forced A/B resolve refused … comparisons=2 < ab_sample_size=10 …"}}` (not 500/403); DB-level zero side effects (all counts identical, `winner`/`resolved_at` NULL, both skill rows untouched); boundary 9→400, 10→**200** with `forced_winner` + `resolved_at` persisted; residue 0; counts back to baseline. Router mounted from the real module (TestClient, no port bind); DB identity printed from the same engine before/after |
| 10 | Bound set via real factory | **PASS** | **31 tools** pinned (sorted list in §4); 0 forbidden names; `access_memory` present; chain identity `bound == universe ∩ resolve_tool_filter(...)` → True; registry-derived proven (synthetic tool injection changed the resolved set); 0 phantoms for skill-keeper |
| 11 | Deny bites | **PASS** | scratch meta re-adding `bash`+`service` → **identical set** (symmetric difference `[]`); `resolve(allow, deny=[])` *contains* them ⇒ deny is the cause |
| 12 | `read_file` absolute-path residual | **PASS (residual recorded)** | succeeds on `/tmp` file and `/etc/hostname`; **every absolute path reachable**, no allowlist/scope, symlinks followed; relative paths ARE fenced; `~` not an escape vector. Accepted documented residual (M-5) |
| 13 | Real keeper toolset | **PASS** | all 5 tools absent from bound surface *and* model-facing schema; real `ToolNode` dispatch of forged calls → `Error: <name> is not a valid tool` (status=error); `access_memory` read returns real content |
| 14 | soul.md machine-check | **PASS** | 83 tokens classified; 32 tool/category tokens resolved via the production path → **0 mismatches**; `access_memory`/`explore`/`experience` bound via category expansion of `self`/`knowledge`; `todo` is a category via innate expansion; `shared_meta_kv` bound-but-unnamed (informational). `skill_analysis`/`skill_evolution` proven to be job_type literals |
| 15 | `TestCheckABTestResolution` ≥20 clean | **PASS-WITH-NOTE** (bar NOT met) | 20 consecutive class runs → **16 clean / 4 failing** (longest streak 7); +26 solo → 11 failures / 46 runs. All 11 = ONE intra-test race (3× `asyncio.to_thread` over one StaticPool connection, `skill_evolution_service.py:1447-1450`); **guard exonerated on 4 independent grounds**: guard message 0 hits in 49 outputs; the class never passes `winner_id=`; branch delta on the service = 2 docstrings + the guard at `:1364` (outside the gather); `repository.py` 0 diff lines; class body byte-identical base↔HEAD. Dead-port LLM fence reproduced the same failure ⇒ not env-shaped. QUARANTINE rows 45/56/90 are **wrong** that it "passes solo" — `threshold_met` failed 6/13 solo |
| 16 | Seal | **PASS** | rc=**0**, `PROMPT-BASELINE: OK — 161 versioned artifact(s) match`; non-mutating (status empty, baseline sha identical); re-pin = **exactly 1 row of 161**, and it is *correct* (HEAD blob `da02449a…` = new pin; base blob `4b1ef4e6…` = old pin) ⇒ warranted, not blind. (Dash `${PIPESTATUS[0]}` false-rc trap documented by the lane) |
| 17 | Mutation meaningfulness | **PASS-WITH-NOTE** | 4/5 mutants RED→GREEN (table §5); the 5th (`>0`→truthiness) is an **equivalent mutant** — `max(0,int(x))` clamp at `:354` makes the domain `0`-or-positive where `x>0 ≡ bool(x)`; the 0-boundary IS pinned (supplementary probe b2 → 2 RED nodes). Recommendation: drop the redundant `>0` or drop the clamp and pin a negative |
| 18 | Refusal-path write purity | **PASS** | **all 80 public tables delta NONE** + `pg_stat_user_tables` counters zero (no insert-then-delete, no hidden UPDATE) across lane-OFF / cooldown / dedup-hit refusals; **positive control** (+1 row on the accepted admission) proves the apparatus detects writes; the old `enqueue.await_count==0` pin is *sufficient* for these three shapes |
| 19 | `inner_soul` denied → memories write refused | **PASS** | `agents/skill-keeper/` tree sha256 identical pre/post; probe file never created; git clean; bound set contains **zero filesystem writers**; `access_memory` read alive |
| 20 | Execution-time refusal semantics | **FAIL (primary)** / PASS (fail-closed + no-collateral) | terminal semantics — §1. Fail-closed: env `maybe` → `ValueError` → refusing; injected probe `RuntimeError` → refusing. Latent residual: a manager **without** the probe attribute admits + starts (fail-open) — unreachable through the shipped manager today |
| 21 | Base-proven failures | **PASS** | dev's "2 failures" = **2 classes / 4 nodes / 3 root causes**, ALL reproduced at `66d8750448`: F1/F2 (A/B race — solo 1/15, class 14/48 at base; base code byte-identical `:1366-1380`↔HEAD `:1395-1409`, zero concurrency lines in the lane diff, `conftest.py` unchanged); F3 `embedding_base_url` (identical signature; cause = ambient `EMBEDDING_BASE_URL`, `DATABASE_URL` unset); F4 `ab_sample_size 20≠10` (identical; **deterministic, not ambient** — survives the embedding scrub; `daemon/config.py:4092` default 20 vs `config.yaml:549`/test 10). Interleaved A/B reversed the sequential reading (BASE 4/12 vs HEAD 2/12) ⇒ load noise, not lane-attributable. HEAD corpus 255 = 60+13+22+84+76 (no deselection) |
| 22 | Shared switch parser | **PASS-WITH-CAVEAT** | AST: both switches call only `_resolve_switch` (`:83`) and reference the **same frozenset objects** (`is` → True); token sets byte-identical `{1,true,yes,on}`/`{0,false,no,off}`; invalid value → **2 loud ERROR logs at `load_config`** + config stays False + call-time `ValueError`. Caveat: the "loud load-time check" is an ERROR **log**, not a raise |

### New finding surfaced by item 22's lane (not in the commission's model)
`SkillEvolutionConfig` is a pydantic `BaseSettings` with `env_prefix="SKILL_EVOLUTION_"` (`daemon/config.py:4051`), so **`SKILL_EVOLUTION_EVOLUTION_LANE_ENABLED=true` alone turns the lane ON** with the canonical `ENSEMBLE_*` var unset. Not a bypass (the canonical env still wins, proved in item 3), but a **second, undocumented ON surface** — neither variable appears in `.env.example`/`.env.prod.example` despite 20 other `SKILL_EVOLUTION_*` keys being documented there.

---

## 3. Pack results (ensure.md blast-radius scope)

Change set = 9 daemon files across services/tools/routers/config/manager + `agents/skill-keeper/*` ⇒ cross-module, pack net warranted; **Release Gate not warranted** (isolated feature fix, no release/architecture change).

| Pack | Result | Counts | Attribution |
|---|---|---|---|
| `skill_evolution_unit_test` | **PASS** | 47/47, 3.15 s | baseline-exact; pack files untouched by branch |
| `skill_capture_killswitch_unit_test` | **PASS** | 118/118, 6.90 s | +23 nodes vs the stale header (95) = the branch's new kill-switch suite 26→49; capture switch asserted in all 5 files; evolution switch only ON-pinned here (OFF/token semantics covered by `lane_gate`, which L1 ran green: 60+49=109) |
| `concurrency_atomic_unit_test` (ensure.md Core #2/#3) | **FAIL rc=1 → PASS-by-quarantine-attribution** | 94P / 5F / 74S, 87.61 s | all 5 = `test_instance_metadata_atomic.py` `DetachedInstanceError` — **exact node-set match** to QUARANTINE.md:134 (executed base-leg A/B at `639b7129b`, deterministic); test file empty diff; exercised repository **absent from the diff**; 0 branch-caused. Caveat: base-leg proof is at `639b7129b`, not `66d8750448` |
| `skill_services_gzip_regression_unit_test` | **FAIL rc=1 → PASS-by-attribution** | 177P / 3F, 14.88 s | all 3 = migration `20260714_000001` SQLite family (base-evidenced, QUARANTINE row 89); `run_pending_migrations` exists at base `manager.py:807`; **0 test functions changed** on all 4 pack files base↔HEAD; signature token drift `near "CONSTRAINT"`→`near "EXISTS"` = SQLite 3.53.1 parsing, same defect. The 4th baseline member (A/B) passed — **not a masked regression**: the branch never edits `TestCheckABTestResolution`, and every A/B path uses `comparisons=10` vs `sample_size=10` so the new guard cannot fire in that suite |
| `skill_evolution_pg_test` | **FAIL rc=4 → pre-existing broken pack** | 0 checks ran | stale path (`tests/unit/test_auto_load_skills.py` deleted in `eeef8845`); `RESULT:` line is dead code under `set -e`; PACKS.md row 2165 inaccurate (claims it runs `pg_skill_schema_check.py` — it only echoes the name). A naive path fix would give a **vacuous PASS** (no `postgres`-marked tests in the surviving files). ⚠️ `test/pg_skill_schema_check.py` itself **hardcodes a DSN and `drop_all`s the target DB** — never wire it in unrepaired |
| `job_queue_unit_test` | **TIMEOUT rc=124 (pack-budget defect, base-A/B proven) → regression content: 0 branch-caused** | shipped pack killed at its own 120 s cap, **identically at base** (`66d8750448`, same kill point, `real 2m0.07s`); diagnostic full run of the same selection: `16 failed, 2179 passed, 38 skipped, 3 deselected` in 428.22 s (2233 collected) | all 16 base-inherited: the exact 16 node IDs re-run at base → **16/16 identical signatures**; `git diff 66d8750448..HEAD -- tests/job_queue/` EMPTY. Families: mirror-finalize MagicMock-JSON ×3 (QUARANTINE cluster-D, red-since-v0.13.10, in *task* completion not the branch's hunks), in_progress_guard ×2, jober ×3, watcher ×2, terminal_write_census ×2, +5 singles incl. one NEW-to-ledger `terminate_instance audit_sink=None` kwarg (nest-ops, merged into base 2026-10-21, `manager.py:11919` — no branch hunk touches it). **Changed-surface callout: the pack's retry/idempotency/atomic-transition coverage (`test_job_retry_engine`, `test_retry_*`, `test_idempotent_enqueue[_atomic]`, `test_job_repository_atomic_transition`) is GREEN at HEAD; zero failures in finalize/`complete_job`/`retry_job`/idempotency-key tests.** Budget root cause: 2233 tests need ~430 s vs the 120 s internal cap (top-25 durations ≈90 s; the f1 file spawns nested pytest subprocesses with their own 180 s child timeouts); `pytest.ini timeout=30` is inert (pytest-timeout not installed) |

**ensure.md Core Criticals**: #1 no-regressions-in-changed-packs — 0 branch-caused failures across all executed packs; #2/#3 concurrency + no-sync-DB-on-loop — PASS-by-quarantine-attribution (above); #4 `dev.sh --timeout-graceful-shutdown 10` — static check deferred to the commit lane (recorded in §7).

---

## 4. The pinned bound set (item 10)

31 tools (sorted): `access_memory, experience, explore, glob_files, grep_files, list_directory, read_file, shared_meta_kv, skill_analyze, skill_create, skill_evolve, skill_execute_capture, skill_feedback, skill_fix, skill_get_metrics, skill_list, skill_resolve_ab, skill_search, skill_view, todo_clear, todo_graph_add_edge, todo_graph_add_subtask, todo_graph_create, todo_graph_remove_edge, todo_graph_remove_subtask, todo_graph_update, todo_graph_update_subtask, todo_list_create, todo_list_update, todo_view, tool_help`

Deny `['bash','proc','service','write_file','edit_file','inner_soul']` registry-expands to the **14 names actually removed** (bash · proc_list/logs/run/status/stop · service_list/logs/start/status/stop · write_file · edit_file · inner_soul). The dev's "20-tool" figure omits the 11 `todo_*` tools from innate expansion (20+11=31). **Conditional:** `explore`/`experience` exist only when RAG is enabled (31→29, **no registry warning fires** when absent) — if 31 is the intended contract, say so in the charter.

---

## 5. Mutation table (item 17)

Baseline 171 green (own scratch worktree, own venv, `daemon.__file__` asserted inside the tree before/after every run).

| Mutant | file:line | RED nodes | Signature | Meaningful |
|---|---|---|---|---|
| (a) remove `if is_fix and job_id:` cooldown stamp | `skill_job_dispatcher.py:673-678` | 2 | `re-FIX inside the cooldown window must refuse` / `assert 'job-1' is None` | **YES** |
| (b) `> 0` → truthiness | `skill_job_dispatcher.py:641` | **0** | — | **NO — equivalent mutant** (`max(0,int(x))` clamp at `:354` makes `x>0 ≡ bool(x)`) |
| (b2) *supplementary:* `max(0,int(x))` → `int(x) or 1800` | `skill_job_dispatcher.py:354` | 2 | `assert None == 'job-1'` / `assert 1800 == 0` | YES (proves the 0-boundary IS pinned) |
| (c) resolve guard moved after `asyncio.gather` | `skill_evolution_service.py:1380-1387` → past `:1395-1409` | 1 | `Expected 'deactivate' to not have been called. Called 1 times` | **YES** |
| (d) remove `except HTTPException: raise` | `daemon/routers/skills.py:1400-1401` | 1 | `assert 500 == 503` | **YES** |
| (e) delete manager fallback gate | `daemon/manager.py:6519-6534` | 1 | `assert 1 == 0` (fallback must not mint a row while lane OFF) | **YES** |

Every mutant restored (`git checkout --`) → 171 green, tree byte-identical to `95b16b265`.

---

## 6. Fence audit — prod contact PROVEN-NONE

Independent census (sockets/fds/artefacts/source): all **14** established `10.44.0.2:5432` connections are owned by two **pre-existing systemd daemons** (`ensemble-main` pid 2199261 → `ensemble_prod`, started 17.4 h before the window; pid 1147 → `ensemble_demo` 87.3 h — the demo daemon, *not* a second prod client despite the binary name); **zero** processes with a worktree cwd; **zero** DSNs/driver logs targeting prod in any lane artefact (the sole driver-shaped hit is an assertion *of absence*); 42/42 observed server identities `('127.0.0.1/32', 5432)`; every lane DB script fences on `127.0.0.1` + `ensemble_dev` fail-closed. Limits disclosed: no server-side PG logs, no `conntrack` (flows closed >60 s invisible), TIME-WAIT unattributable, one L1 detail (`import psycopg2` death) unverified-but-uncontradicted.

**Standing hazard recorded:** the ambient shell env on this host is **prod-shaped** (`POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2`, `ENSEMBLE_SELF_ENV=live`, `ENSEMBLE_UPGRADE_LIVE=1`). Every lane was alerted mid-flight and re-proved its DB identity; the per-variable scrub (`env -u POSTGRES_DB -u … -u ENSEMBLE_SELF_ENV -u ENSEMBLE_UPGRADE_LIVE`) is mandatory on this host — `env -u POSTGRES_*` is a silent NO-OP.

---

## 7. Gaps, disclosures, routed follow-ups

**Could not execute / incomplete**
- `job_queue_unit_test` as shipped can return neither PASS nor FAIL on any current tree (120 s internal cap vs ~430 s needed, base-identical) — its regression content was recovered via a diagnostic full run of the same selection with a base A/B (above). **Routed: split this pack into sub-5-min packs** (test-architecture commission; not done here — this commission is verification-only and commits docs only).
- The pack contains **zero** `skill_evolution` references, so the execution gate and the M-2 retry-carry are not covered by it — both are covered elsewhere in this commission (`lane_gate` 60 nodes green via L1; runtime gate evidence in §1; retry carry proved in item 7(iii)).
- `ensure.md` Core #4 static — PASS: `dev.sh:155: $PYTHON -m uvicorn daemon.api:app --host "$HOST" --port "$PORT" --reload --log-level "$LOG_LEVEL" --no-access-log --timeout-graceful-shutdown 10` (checked by the commit lane).
- L3b runtime reaper confirmation is **code-reading**, not observation (fence: no DB in that lane); it corroborates the two runtime proofs rather than replacing them.
- Whole-tree base-vs-HEAD regression diff and the dev's "neighbor packs 362 / retry 59" claims — out of item-21 scope, not run.
- The dev's `read_only_surface = 20 tools` claim is superseded: the real runtime figure is **31** (§4).

**Routed follow-ups (owner: developer / test-maintainer, not this commission)**
1. **BLOCKING** — fix the execution-gate termination (§1) + add a non-mocked pin (real DB, ≥3 passes, second-job no-starvation assert).
2. `TestCheckABTestResolution` StaticPool shared-connection race is a **reproducible pre-existing product defect** (intra-test, 3 sessions on one connection) — deserves its own defect commission, not permanent quarantine; QUARANTINE rows 45/56/90 need correcting ("passes solo" is false; it is not class-pollution).
3. `test/pg_skill_schema_check.py` hardcodes a DSN and `drop_all`s the target — must be env-driven + self-provisioned scratch before anyone wires it into a pack. Repair `skill_evolution_pg_test.sh` (stale path, dead `RESULT:` line, vacuous-PASS trap) and PACKS.md row 2165.
4. **Systemic pack-script defect**: under `set -euo pipefail`, at least `skill_evolution_unit_test`, `skill_evolution_pg_test`, `concurrency_atomic_unit_test` abort before their `RESULT:` block on failure — they can never report FAIL/TIMEOUT. Gates must read exit codes; packs should emit their terminal token before exiting.
5. PACKS.md baselines are stale (`skill_capture_killswitch` header 95 vs 118; `skill_services_gzip` 142P/4F vs 177P/3F after intervening history).
6. Document `ENSEMBLE_SKILL_EVOLUTION_ENABLED` and the second ON surface `SKILL_EVOLUTION_EVOLUTION_LANE_ENABLED` in `.env.example`/`.env.prod.example`; decide whether empty/whitespace should mean "defer to config" (current) or "off".
7. Fallback/duplicate key f-string: unify on `normalize_project_id` (M-4 parity).
8. `_fix_admission_cooldown` never evicts (unbounded growth per (project, skill); written-but-never-read at cooldown 0).
9. Fail-open residual: manager without the lane-probe attribute admits + starts (unreachable via the shipped manager; harden or document).

**Process disclosures (this commission's own hygiene)**
- 🔴 **Three ambient keys were printed into worker transcripts by probe scripts**: `LIGHTRAG_API_KEY` (L5), `EMBEDDING_API_KEY` + `BLUEPRINT_EMBEDDING_API_KEY` (L9). Same class as the 2026-10-03 PB-F1 incident. Recommendation: treat as exposed / rotate if live; add env-dump redaction to probe templates (values never reached /tmp artefacts — the blast radius is the conversation transcripts).
- Eight worker instances died on `minimax-m3.1-flash-free` deprecation mid-run; all recovered via revive/replacement except two pack attempts (one re-dispatched successfully). No evidence any died mid-mutation: every mutation lane ended byte-identical to HEAD.
- Scratch hygiene: L9's and L8's own trees removed with proof; `/tmp/sk-mut-85cb8d6c` (L8's first tree, abandoned after the fence alert) remains registered — **owner: L8/giter to reap**.
- Zero rows/schema/table drift in `ensemble_dev` at the end of every DB-touching lane (before/after counts recorded); primary worktree `git status --porcelain` empty at every lane close; HEAD never moved from `95b16b265`.

---

## 8. Scope decision

Full-suite run was **not** warranted: the change set (9 daemon files + skill-keeper agent config) is cross-module but confined to the skill-evolution admission/execution lane and one agent's toolset. The scoped net = 6 packs + 9 checklist lanes + severity scoping + fence audit. Release Gate skipped (no release/architecture change; this branch stages as its own release candidate later, where the gate runs).

---

*Report: `.agents/tester/RESULTS/2026-10-22-skill-keeper-confinement-verification.md` · Evidence dirs: `/tmp/l1probe`, `/tmp/lane-l2`, `/tmp/l3`, `/tmp/l4`, `/tmp/l5_probe`, `/tmp/l6*`, `/tmp/l7`, `/tmp/l9*`, `/tmp/sk-mut-l8-2ae2b592` (removed; outputs retained under `/tmp`), `/tmp/pkconc2.out`. Docs-only commit: see §7 row 1 status and the commit SHA in the final section.*
