# Harness traps hit while running the never-started-detector delta gate (2026-11-26)

Lane: `fix/never-started-detector-anomaly-truncation` · worktree `/home/nea/ensemble-src-wt-never-started` · HEAD `0f5af2a5a`.
These are **environment/harness** facts, not defects in the lane under test. Each one cost a worker a wrong turn;
each is cheap to re-learn but expensive to re-derive.

## 1. `InstanceManager` construction WIPES seeded task rows

`InstanceManager.__init__` runs the "env-poison family audit" wipe
(`TaskRepository.clear_all` / `MessageQueueRepository.clear_all`), which **deletes every seeded `task` row**.
Symptom: the detector silently reports `count 0` with `evidence_legs "ok"` — a false CLEAN, not a red.

**Rule: build the manager FIRST, then seed.** Or seed after the manager exists and before the detector call.

## 2. `-q` and `-v` CANCEL each other in pytest

Both are `action="count"` on the SAME `dest="verbose"`, so `-q … -v` nets back to verbosity **0** and prints **no**
test names — the run looks like it silently lost its per-node evidence.

**Rule: for per-test names use `-v` ALONE (drop `-q`).** Pair it with `--tb=short -rfE` for the digest.

## 3. `scripts/run_tests_scrubbed.sh` has an ENTRY GATE before its scrub

An ambient `ENSEMBLE_SELF_ENV` outside `''`/`dev`/`demo`/`sandbox` ⇒ **exit 78 before any unset or pytest spawn**.
A daemon-child shell carries `ENSEMBLE_SELF_ENV=live`, so the refusal fires on the naive invocation:

```
ENV-SCRUB FAILURE: ambient ENSEMBLE_SELF_ENV='live'
unset ENSEMBLE_SELF_ENV or run with ENSEMBLE_SELF_ENV=dev
```

Its family-wide LEAK GATE also fires on any surviving `POSTGRES_*` / legacy / libpq / Plane-workspace / `PORT`
var — **names only**, never values (`POSTGRES_FOO=probe` is enough to trip it). The runner self-scrubs
`POSTGRES_*`/libpq/`PORT` but **NOT** `ENSEMBLE_AUTH_*` or `DAEMON_PORT` ⇒ those 9 call-site `-u` scrubs are
load-bearing, not decoration.

**Rule: always `env -u ENSEMBLE_SELF_ENV` (or set it to `dev`) plus the 9 `-u` scrubs.**

## 4. The bash tool runs `/bin/sh` — `source` is a NO-OP

`source foo.sh` under the tool's `/bin/sh` silently does nothing (the fence file never gets exported).
**Rule: use `bash -c '…'` or POSIX `.` inside a `/bin/sh` script file.** Same reason: `PIPESTATUS` is a bashism —
capture an exit code by redirecting to a file and echoing `$?` with no pipe.

## 5. A BASE leg must be a REAL git worktree, never `git archive`

Two independent reasons, both live now:
- a gitless extraction makes git-shelling asserts pass **Vacuously** (empty stdout ⇒ empty diff ⇒ "unchanged");
- the freeze pin in `tests/unit/test_nest_report_rest_route.py` now **SKIPS** when `.git` is absent, so a gitless
  base leg silently changes that node's semantics between legs.

**Rule: `git worktree add --detach /tmp/<x> <base-sha>` + its own `uv venv --python 3.13 && uv sync --frozen`,
prove `daemon.__file__` resolves inside the scratch tree, then `git worktree remove --force`.**

## 6. The RO role's grant gap is a FIRST-CLASS result, not a failure

`ensemble_prod_ro` has SELECT on only: `db_connections, dump_lane_heartbeat, instance_hierarchy, instance_mappings,
instances, job_queue_items, job_queues, message_queue, task`. It has **no** SELECT on `checkpoints`,
`checkpoint_blobs`, `job_locks`, `instance_ui_prefs` ⇒ any detector leg reading `checkpoints` degrades and the row
renders `count=0, evidence_legs="degraded"` — a **LOWER BOUND**, never a verified 0. Corroborate from readable
tables instead: `task.started_at IS NOT NULL` + `message_queue` rows with `status='completed'` prove turns executed,
hence LangGraph checkpoints exist for that thread.

**Rule: report the degraded count as a lower bound with its corroboration. Never upgrade it to "confirmed clean".**

## 7. A live prod-shaped scan is CONTAMINATED by the verification fleet itself

Running the gate spawns worker instances that land in the same fleet the gate is scanning. In this cycle the only
residual live candidate was **the gate's own `ns-d1-husk-spawn-stamp` worker** (pending task, one unprocessed
`ready` message, zero started turns) — a VERIFICATION ARTIFACT, not a product wedge.

**Rule: before escalating a live candidate, check whether it is one of this gate's own instances. A "confirmed 0"
from a live read-only scan is unreachable while the gate's own fleet is running — say so.**
