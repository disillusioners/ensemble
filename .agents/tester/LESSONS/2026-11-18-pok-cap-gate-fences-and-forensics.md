# POK-cap gate 2026-11-18 — three fence/verification lessons

Lane: `feature/pok-cap-curator-resolver` @ `47ecca4c6` (base `eca98397d`). Full evidence: `RESULTS/2026-11-18-pok-cap-512-curator-resolver-gate.md`.

## 1. PG-lane scratch-DB ownership fence: `ensemble_dev_app`-owned scratch is REFUSED — own it as `ensemble`

`tests/postgres/conftest.py::check_scratch_db_fence` rejects any scratch DB whose owner role **also owns a live-shaped DB**. On this host `ensemble_dev_app` owns `ensemble_dev` (forbidden-set member), so the *obvious* scratch name `ensemble_dev_scratch` — and any `-O ensemble_dev_app` DB — is refused with `the owner role 'ensemble_dev_app' … also owns the live database 'ensemble_dev'`. The local role `ensemble` owns **zero** forbidden DBs, so:

```bash
sudo -n -u postgres createdb -O ensemble ensemble_dev_scratch_<lane>   # passes the fence
PG_TEST_ALLOWED_DBS=ensemble_dev_scratch_<lane> PG_TEST_DB=… PG_TEST_USER=ensemble PG_TEST_PASSWORD=ensemble_dev
```

(`ensemble`/`ensemble_dev` is the conftest's own default pair on the LOCAL 16.x cluster — NOT the live remote 18.4 cluster at 10.44.0.2, whose `ensemble` password differs.) Also: pytest's `pg_engine` **drops every table of its target** at session setup — never share one scratch DB between a pytest PG pack and a seeded harness; use two DBs.

## 2. "Concurrent worktree mutation" alarms must be adjudicated against filesystem mtimes before acting

A recon worker reported a foreign lane planting `MUTATION M2` (a resolver revert) in the lane worktree mid-gate. Disproven in four checks: (a) no source file written after the lane's own commit (compare `git log -1 --format=%cI` vs `find -newermt`), (b) the named files' mtimes predate the gate window, (c) the marker string absent from **all** worktrees (`git worktree list --porcelain` loop), (d) the canonical checkout is *legitimately* pre-fix for this feature. Mechanism: the **CWD-default trap** — an un-`cd`'ed shell reads `daemon/**` from `/home/nea/ensemble-src` (or a base worktree, pre-fix by design) and the diff reads as a "planted revert". Rule: **mtime evidence outranks a worker's transient `git status` narrative**; and for any task that writes to production, run from a **freshly created detached worktree** (here `/tmp/pokcap-head` + own `uv sync --frozen`) so no other lane can share the working directory.

## 3. Live POK forensics: ALWAYS pin `project_id` — slug collisions across projects are real

Slug uniqueness is `(project_id, slug)` for ACTIVE rows only. `decision-client-contract-invariant-185-v187` exists ACTIVE in **two projects** (`83da04de…` agents-ensemble and `54debc61…` llm-supervisor-proxy); an unfiltered slug query returns both and manufactures a false "dual-active tie-break" premise. Every live POK query in a gate must carry `WHERE project_id = …`. Corollary: the resolver's tie-break branch **cannot** be exercised on real single-project data (`uq_pok_active_slug` makes dual-active impossible by construction) — prove it on throwaway SQLite with the partial index dropped there, never on the live store.
