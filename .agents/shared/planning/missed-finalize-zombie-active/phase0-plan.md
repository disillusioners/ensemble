# Phase 0 — Baseline & Evidence

## 1. Objective

Reproduce the zombie-ACTIVE signature at BASE (`22fe83317`) on the dev PG fixture, PROVE the gap
(no admission writer terminalizes the shape across multiple drift cycles), record the lock-slot
symptom, and capture the TRUE pre-fix base FAILED list that Phase 4's net-new gate diffs against.
All under the lane fences, evidence-first.

Design references: technical-analysis §1 (signature + linkage semantics), §2 (coverage map /
the gap), findings-core Q3 (writer census), Q6 (P1 blindness).

## 2. Prerequisites

- Lane worktree `/home/nea/ensemble-src-wt-missed-finalize` @ `22fe83317`, clean, imports OK
  (`import daemon` resolves in-worktree — KV claim already setup-verified).
- Dev PG reachable (`POSTGRES_DB=ensemble_dev`) and/or the scratch PG fixture for the PG pack
  path (`PG_TEST_HOST/PORT/DB/USER/PASSWORD`, localhost:5432/ensemble_test/ensemble/ensemble_dev;
  session `drop_all`→`create_all`; SERIAL-ONLY under xdist).
- Run path: worktree has NO `.venv` — cd-worktree + main-venv interpreter by ABSOLUTE path
  (never bare `python`; never `uv run` in-worktree).

## 3. Decisions

| ID | Decision | Rationale (cite) | Rejected alternatives |
|----|----------|------------------|------------------------|
| PD-0.1 | Fixture is synthetic, seeded directly via repositories/models (JobItem + Task + Instance + `job_locks` row), driven by invoking `reconcile_drift_states` (test-harness style, real PG) — not by booting a daemon | Boots add unrelated surface; the f-arm test harness (`tests/job_queue/test_orphan_active_job_recovery.py` neighborhood) already drives the reconciler against fixtures; no daemon contact per the lane envelope | Full daemon boot on :8079 (heavier, no marginal evidence value for the gap proof) |
| PD-0.2 | Three fixture variants by backing-Task verdict: completed / failed / cancelled (one canonical primary + two variants) | The three tokens (D-3) are per-verdict; the BASE proof must show no writer closes ANY of the three shapes | completed-only (would leave failed/cancelled base behavior unproven) |
| PD-0.3 | Base FAILED-list capture = the exact command set Phase 4 will re-run at HEAD (verbatim, recorded) — lane corpus: daemon default shard + `tests/job_queue/` + the static pin pack + real-PG packs, explicit `tests/` lists only | Net-new discipline REQUIRES same commands both sides (Repo & Dev Conventions [TESTING]; worked example `.agents/tester/RESULTS/2026-10-29-dbpool-functional-gate.md`) | Ad-hoc per-directory runs (non-reproducible diff) |

## 4. Tasks

| ID | Task | Outcome (testable) | Touchpoints (files) | Depends on | Risks carried | AC |
|----|------|--------------------|---------------------|------------|---------------|----|
| T0.1 | Stand up the fenced test environment: per-variable env scrub (`env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT …` — `env -u POSTGRES_*` is a SILENT NO-OP), then explicit `POSTGRES_DB=ensemble_dev`, `PORT=8079`; verify the run root's untracked `data/ensemble.json` database pin (2nd poison vector); prove `ensemble_prod` is NOT resolvable from the run | Fence evidence recorded: scrubbed-env probe + resolved-DB name == `ensemble_dev` (or `ensemble_test` for PG packs) in the evidence doc | (no repo writes — evidence note only) | — | R1 (a mis-fenced run could hit prod — critical note ⛔ convention) | AC-0.1 |
| T0.2 | Build the zombie-ACTIVE fixture on dev/scratch PG: `job_type='task'`, `admission_state='active'`, `deleted_at IS NULL` JobItem; work_id-linked Task in each terminal status (PD-0.2 variants); a NON-TERMINAL instance (RUNNING) owning the job's `instance_id`; a `job_locks` row for the job (model-faithful: `project_id`/`queue_id`/`lock_slot`/`acquired_at`, `models.py:478-522`); timestamps aged ≥ 7200s (task `completed_at` in the past) | Fixture rows exist and are query-verified; `Task.work_id == JobItem.job_id` linkage asserted | scratch fixture script under the lane's test/evidence surface (NOT shipped tree) | T0.1 | — | AC-0.2 |
| T0.3 | Gap proof at BASE: drive ≥3 `reconcile_drift_states` cycles (and let the F-1 backstop + all f-arms run within them); assert the JobItem stays `active`, the `job_locks` row survives, and NO `terminal_reason` appears; capture the E1 verdict for the fixture instance (`evaluate_mission_live` without discount ⇒ `live=True` — the self-hold demonstrated) | Evidence note: per-cycle job state + lock row presence + guard verdict; the class produces ZERO P1 warnings (findings-core Q6) — confirm no drift-warning line names it | evidence doc (`.agents/tester/RESULTS/` convention destination, recorded by the executor) | T0.2 | — | AC-0.3 |
| T0.4 | Capture the TRUE pre-fix base FAILED list: run the lane corpus (PD-0.3) at BASE with explicit `tests/` lists, `tee LOG >/dev/null` + `tail` for console floods, explicit `--timeout` where real-PG legs need it (24.5-26s wall vs 30s ini precedent); store the verbatim commands + full FAILED list + collection counts | Baseline artifact: commands + FAILED list (byte-faithful) + counts, stored where Phase 4 can diff against | logs under the lane evidence surface | T0.1 | R4 (base capture quality determines Phase 4 validity) | AC-0.4 |

## 5. Acceptance criteria

| ID | Criterion |
|----|-----------|
| AC-0.1 | Fence proven: per-variable scrub + explicit dev DB + `ensemble_prod` unresolvable from the run root (evidence recorded, including the `data/ensemble.json` pin check) |
| AC-0.2 | Zombie fixture present on dev/scratch PG with all three task-verdict variants; linkage + age assertions query-verified |
| AC-0.3 | BASE gap proven: ≥3 drift cycles leave the JobItem `active`, lock row present, no `terminal_reason`; E1 self-hold verdict captured; zero P1 warnings for the class |
| AC-0.4 | TRUE base FAILED list + verbatim command set + collection counts stored (Phase 4 consumes them unchanged) |

## 6. Verification method

Direct query assertions against the fixture DB per cycle (admission_state, terminal_reason,
job_locks row existence); guard verdict logged verbatim; FAILED-list capture is mechanical
(same commands → same lists). The Phase-1 smoke re-uses AC-0.2's fixture shape as its
RED-side input (coupling map P0→P1).

## 7. Risks & notes

- The fixture is synthetic — it must stay MODEL-FAITHFUL (real repositories, real trigger suite
  on PG) or the Phase-3/4 evidence value collapses. Use the repository/service constructors, not
  raw SQL INSERTs where a service path exists.
- Do NOT commit the fixture script into the shipped tree; it lives in the lane's test/evidence
  surface only.
- If dev PG is unavailable, the PG-pack DSN fixture path is acceptable — record which was used.
