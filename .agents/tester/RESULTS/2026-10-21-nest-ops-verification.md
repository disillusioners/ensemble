# TESTER VERIFICATION — nest-ops commission (instance_read + instance_ops privileged categories)

- **Date**: 2026-10-21 (UTC)
- **Target**: `/home/nea/ensemble-src-wt-nest-ops`, branch `feature/nest-ops-instance-ops`
- **HEAD**: `54b806dff8ba73fdc8b404495eea770e20f9e0e9` — base `8e6df58e445cd56b5e775dacebe0ef53f0add0b4`
- **Commits**: `5260bfa37` (impl + pin ceremony) → `431827842` (review advisories) → `54b806dff` (tidier fixes)
- **Lane**: independent tester gate, direct execution (dispatch instruction: "NO CHILDREN")
- **Verdict**: **READY-TO-STAGE** — 7/7 gates PASS (2 with disclosed notes), **net-new reds = 0**

---

## GATE G — Claims audit: **PASS**

| Claim | Evidence | Verdict |
|---|---|---|
| 3 commits linear | `git merge-base --is-ancestor` L1/L2/L3 all OK (8e6df58e4 → 5260bfa37 → 431827842 → 54b806dff) | ✅ |
| Tree clean | `git status --porcelain` → empty | ✅ |
| No version-bump files | diff vs base ∩ {pyproject.toml, daemon/__init__.py, tests/test_api.py, uv.lock} = **NONE**; `daemon/__init__.py` `__version__ = "0.26.3"` | ✅ |
| Migration prefix unique | `daemon/migrations/versions/*.sql` = **107** files; dup-prefix scan → **empty** (0 duplicates); exactly one `20261021_120000_create_instance_ops_audit.sql` | ✅ |
| Rollback-safe | `git grep instance_ops_audit 8e6df58e4` → **ZERO hits** (old binary never references the table) | ✅ |

## GATE A — Negative authorization + allow-list: **PASS**

**Spawn-path proof** (real spawn order: universe build → `scan_tools_for_full_docs` → `_apply_tool_filter` → `resolve_tool_filter` with the REAL registry meta — `daemon/tools/instance.py:6556-6690`):

| Agent | instance-family tools resolved |
|---|---|
| `watcher` (empty-allow) | **0 / 10** |
| `ari` | **0 / 10** |
| `leader` | **0 / 10** |
| `maintenancer` | **10 / 10** = 4 read (`fleet_capacity_summary`, `instance_get`, `instance_health`, `instance_list`) + 6 act (`instance_answer`, `instance_dismiss`, `instance_mark`, `instance_nudge`, `instance_reclaim`, `instance_terminate`) |

**Kill-switch** `ENSEMBLE_INSTANCE_OPS_ENABLED=0`: **6/6 acts return byte-identical `io.KILL_SWITCH_REFUSAL`**; `fleet.terminated == []` (zero mutations); reads still live (`instance_list` → rows). Off-spellings `0/false/no/off` pin in `test_off_spellings_disable`; the read module carries no switch (`test_the_read_module_has_no_kill_switch`).

**Inherited 3-red fold at 0**: `tests/integration/test_maintenancer_spawn_resolves_tools.py` → **13 passed** (1.73s).

## GATE B — AC-23 fixtures + budget rails: **PASS**

- **AC-23a (refusal)**: `test_direct_window_on_acting_instance_refused` — direct user-origin window on the acting instance vs a PROTECTED target → `reason=direct-provenance-insufficient`, `fleet.terminated == []`. Docstring states the **genuine operator-direct path is also refused (INTENDED — L-9)**. ✅
- **AC-23b (documented residual)**: `test_inherited_window_proceeds_ac23b` — forged direct window on the **SPAWNER** → inherited window on the actor → act **ADMITTED** (`terminated is True`) with audit row `provenance_class=inherited`, `root_source=api`, `root_message_id=root-msg-1`, `provenance_depth=1`. Docstring names **DF-4 / critical note `f4cf9c4f`** and warns *"DO NOT 'fix' this test to assert a refusal — that would certify a property the code does not have."* ✅
- **No test asserts 23b as a refusal** — the AC-23 surface in `tests/unit/test_instance_ops_behaviour.py` was enumerated in full (8 gate-ladder tests; the only inherited-window test is the admission one). **No false security property is certified.** ✅
- **Budgets** (`daemon/tools/instance_ops.py`): `TERMINATE_BUDGET_24H = 3` (:147), `PROTECTED_TERMINATE_BUDGET_24H = 1` (:149), `TARGET_MIN_AGE_SECONDS = 600` (:151); counting via `count_committed_terminates` / `count_committed_protected_terminates` (**committed-only**). Tests: `test_fourth_terminate_in_24h_refuses` (4th refuses `terminate-budget-exceeded`), `test_target_too_young` — both green.
- 🟡 **NOTE (non-blocking)**: the protected-target **N=1** refusal has no dedicated test; the mechanism is source-verified (`protected_count >= PROTECTED_TERMINATE_BUDGET_24H` → refuse) but is not test-pinned.

## GATE C — Tidier echoes: **PASS**

- **H1** `test_spawned_by_is_the_parents_agent_not_the_targets`: `spawned_by.agent_id == "leader"` (the PARENT's agent) and `!= payload["agent_id"]` (never the target's own).
- **Root case** `test_spawned_by_root_is_null_with_explicit_marker`: `agent_id is None` + `note == "no-parent"`.
- **M2** `test_marker_write_then_get_round_trips_both_markers`: `instance_ops.protect` / `instance_ops.suspect` written by `instance_mark` round-trip through `instance_get` with flags `protected` / `suspected` (dotted keys survive the allowlist).
- **M5** `test_dismiss_cascade_failure_settles_error_not_pending`: audit row `outcome == "error"` (NOT `pending`) + typed token `dismiss-cascade-failed` in the refusal and in `error_message`.
- Bonus **M8**: `test_health_and_capacity_surface_scan_truncated` green.

## GATE D — Boot smoke: **PASS** (SQLite + PG)

**SQLite (scratch)**: ORM `create_all` (with `daemon.repositories.instance_ops.models` registered — the factory late-import path) → `instance_ops_audit` present (50 tables). `discover_migrations()` → **105 valid** (2 legacy files skipped for a missing `-- UP` section — pre-existing) → **dup-prefix guard PASSED** (no `DuplicateMigrationVersionError`). Only pre-existing blocker hit: **`20260714_000001`** (`ALTER TABLE … DROP CONSTRAINT IF EXISTS` → `near "EXISTS": syntax error` on SQLite) — **ledger-pre-marked per the documented workaround; NOT this commission's defect**. After pre-mark: 46 applied (100 ledger rows), **`20261021_120000` applied cleanly**, table carries all 18 audit columns (incl. `provenance_class/depth`, `root_source`, `root_message_id`, `error_message`, `preview_run_id`).

**PG (scratch, isolated)**: unit `nestops-gate-pg.service`; cgroup `/user.slice/user-1000.slice/user@1000.service/app.slice/nestops-gate-pg.service` — **outside** the live `/system.slice/ensemble-main.service`; scratch CWD `/tmp/nestops-gate-pg` + scratch DB `ens_nestops_g`@127.0.0.1:5432. Banner **"Starting Ensemble v0.26.3"** (unchanged — no bump). `livez` 200 `version=0.26.3`; `readyz` 200 (database/queue_freshness/services/checkpoint_saver all true, `draining=false`). `instance_ops_audit` present on the scratch PG (`information_schema` count = 1). **LIVE untouched**: `:9797` → 200 before **and** after; `ensemble-main` MainPID **528942 unchanged**; port 8091 freed; scratch role+DB dropped at teardown.

## GATE E — E2E HTTP dismiss: **PASS-WITH-NOTES**

- **Genuine handler** (not a re-bound helper): `POST /api/instances/{id}/question/dismiss` → `dismiss_question` → canonical `dismiss_question_pack(manager, instance_id, live_hub=…)` (`daemon/routers/instances.py:1246-1320`); typed-error mapping intact (503 / 404 / 409 / 500).
- **Documented envelope** (HTTP 200): `{"status":"dismissed","instance_id":…,"resume_info":{…}}` with `target_id`, `resumed_ids`, `skipped_ids`, `resume_results`.
- **Real resume executed**: log `route_outcome=answer_gate_existing_turn suspension_reason=awaiting_answer handle_work_id=gate-w-e1 target_work_id=turn-gate-1` → background processing scheduled → the dismissal HumanMessage (`[User dismissed the question without answering.]`) was **actually delivered** (captured on the instance SSE stream) and the resumed agent turn ran.
- **Honest 404 contract** observed incidentally (pre-hydration attempt): `INSTANCE_NOT_FOUND` + *"No question pack for instance … The user can only dismiss a question that was actually asked."*
- **Real boot hydration**: `QuestionManager.rehydrate_from_payloads: restored 1 pending question pack(s)` / `question-packs rehydrated: 1 (candidates=1)`.
- **Pre-existing e2e red byte-unchanged**: `tests/e2e/test_answer_dismiss_flow.py::test_full_answer_lifecycle_pause_answer_resume_complete` — base `8e6df58e4` vs HEAD `54b806dff` produce **byte-identical `E` assertion lines** (`AssertionError: Terminal Task MUST have JobItem admission_state='done'; got 'active'.` @ :599) and identical 164-line outputs (only the venv path string differs). **The extraction did not deepen it.** ✅
- 🟡 **NOTE 1**: the `question_pack` / `status="dismissed"` SSE event was **not captured** on the per-instance `/events` stream in my run (stream shows `connected`, `context_usage`, `user_message`, `tool_call`…). Emit-before-cascade is pinned green at unit level (`TestSseBeforeCascadeOrdering::test_route_emits_sse_before_the_cascade`); the event's channel routing was not adjudicated — **no defect claimed**.
- 🟡 **NOTE 2**: the `PAUSED → RUNNING` DB flip was **not observable** with the fabricated pause state (lifecycle log: *"Instance … is not paused (status=PAUSED), skipping"* — the cascade's pause bookkeeping requires a pause performed through the real pause path, not a raw status UPDATE). Cascade semantics are covered by the green unit pins (`TestRecordedSetRelease`, `TestSseBeforeCascadeOrdering`). Neither note bears on the extraction risk being gated (genuine handler proven by envelope + handle resolution + an executed resume).

## GATE F — Regression sweep: **PASS** (net-new reds = **0**)

| Suite (HEAD `54b806dff`) | Result | Base `8e6df58e4` | Classification |
|---|---|---|---|
| 4 nest-ops unit files (registration / prompt_layer / behaviour / dismiss_service) | **80 passed** | n/a (new) | new code green |
| `tests/integration/test_maintenancer_spawn_resolves_tools.py` | **13 passed** | — | green |
| `tests/integration/test_service_tool_flag_off_byte_identical.py` (`-m integration`) | **9 passed** | — | green |
| upgrade/restart lane + registration neighbourhood (agent_restart, post_restart_arm_notify_journal, upgrade_tools, attestation/secrets/upgrade/board/self_alarm registration) | **359 passed** | — | green |
| question/pause/resume lane + full e2e answer-dismiss file | 49 passed, **1 failed** | same 1 failed, **byte-identical** | **pre-existing** (known) |
| `tests/integration/test_maintenancer_end_to_end.py` | 13 passed, **6 failed** (`TestKBIndexLoadable::test_memory_md_lists_kb_doc[01..06]`) | **6 failed, identical FAILED list** | **pre-existing** (known) |
| `tests/test_api.py` + lifecycle/pause service subset | 96 passed, **6 failed** (`test_api.py`) | **6 failed, identical FAILED list + identical assertion content** | **pre-existing** (additionally discovered) |
| prompt-artifact baseline (`scripts/protection/generate_prompt_artifact_baseline.py --check`) | **OK — 161 artifacts match** | — | green |

**Delta: net-new reds = 0.** The 7 known pre-existing reds reproduce identically (6 KB reds base-proven; the e2e red byte-proven). **Additionally discovered pre-existing debt (base-proven, NOT caused by this branch): 6 × `tests/test_api.py`** (4 × `test_list_instances*` AssertionError + 2 × `TypeError: 'Mock' object …`) — same FAILED list and identical assertion content at base.

**Scope decision (explicit)**: full-suite NOT run. Blast radius = 3 commits touching `daemon/{tools,services,repositories,routers}` + `agents/maintenancer|ari` + 1 migration; the sweep was scoped to the touched neighbourhoods plus base legs for every red observed. **Not run wholesale** (disclosed): `tests/unit/services` (2932), `tests/job_queue` (2152), `tests/unit/tools` (3999) — the branch's own dev lane ran the whole-tree sweep; this lane is the independent touched-neighbourhood gate.

---

## Method notes / harness artifacts (NOT product defects)

1. **Fresh-DB bootstrapping**: the `.sql` audit trail assumes the ORM-created base schema; a bare runner over an empty DB fails at `20240101_000001` ("no such table: projects"). Correct recipe = register model modules → `SQLModel.metadata.create_all` → runner.
2. **DB-level NOT NULL without defaults** bit the task/ledger seeding three times (`task.retry_count`, `task.cancel_requested`, `schema_migrations.name`/`applied_at`) — the known model-vs-DB default gap (`daemon/repositories/task/models.py:133`).
3. First scratch boot failed on a missing `ENSEMBLE_CONFIG` (scratch CWD has no `config.yaml`) — harness var, fixed by pointing it at the worktree `config.yaml`.
4. **Disclosure — the dismissal dispatched a real agent turn** on the scratch daemon (LLM lane), which read the scratch daemon log. The only credential visible in that transcript was the **throwaway scratch DB role password**, invalidated by the `DROP ROLE` at teardown; the worktree fence `.env` was redacted by the worker's own `sed`. No `ensemble_prod` / live credential was exposed, no live install path was touched.
5. The agent wrapper runs under `/bin/sh`; one `pkill -f` pattern matched my own shell (exit -15) — harness-only, cleanup re-verified afterwards (base worktree removed, scratch DB+role dropped, port 8091 free, live 9797 = 200, MainPID 528942).

## Blocking items

**None.** The three disclosed notes (Gate B protected-N=1 test coverage, Gate E SSE capture, Gate E cascade DB-flip observability) are non-blocking and route to the developer lane if the maintainer wants them closed.

**Overall: READY-TO-STAGE** (stage only — promote remains the user's 3-factor nonce ceremony).
