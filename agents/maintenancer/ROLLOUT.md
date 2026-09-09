# Maintenancer — Rollout Runbook (Wave 3 deploy)

> **Audience:** operators deploying the Maintenancer agent to a running ensemble daemon. The Wave 3 deploy window is the **one-time restart** that brings the agent live; before it, the leader's team does not include the Maintenancer and the privileged categories are not yet gated.

This runbook is the sibling of `README.md` and is the operational counterpart of the architectural spec at `.agents/shared/planning/maintenancer-agent/`. It assumes the W1 + W2 waves have already landed and the W3 PR (this commit) has merged to `latest`. Do **not** start W3 until the W2 acceptance criteria have passed.

---

## Pre-flight (BEFORE W3 starts)

Verify W1 + W2 are landed and the W3 PR is merged:

```bash
git rev-parse --abbrev-ref HEAD         # expected: latest (or the release branch you cut)
git rev-parse --short HEAD
git log --oneline -1                    # HEAD must be the merge commit of the W3 PR
```

Pin tests that MUST pass before W3 starts (these run in CI; re-run locally for paranoia):

```bash
uv run pytest tests/unit/test_maintenancer_agent.py tests/unit/test_maintenancer_kb_coverage.py \
  tests/unit/test_maintenancer_fan_in_valve.py tests/unit/test_maintenancer_report_scrutiny.py \
  tests/unit/test_maintenancer_skill_versions_consistent.py \
  tests/unit/test_maintenancer_team_members_within_team.py \
  tests/integration/test_maintenancer_spawn_resolves_tools.py \
  tests/integration/test_maintenancer_upgrade_gate_refuses.py -v
```

Expected outcome: all green. If any red, fix BEFORE proceeding — the W3 deploy assumes the W1/W2 contract holds.

---

## W1 — File-disjoint agent scaffolding + tooling + KB (NO restart)

W1 lands three PRs in parallel (`P1 ∥ P2 ∥ P3`). The three PRs touch disjoint files; in particular `P1` and `P2` are sequenced by a merge-order invariant so a crash-restart between them lands coherent state.

### W1 Step 1 — Land P1 (agent scaffolding + leader team_members)

```bash
git checkout latest
git merge --no-ff feature/maintenancer-agent-p1
```

PR description carries the **MERGE-ORDER INVARIANT** block verbatim — this PR merges BEFORE the P2 PR. Verify before pushing the merge button.

After the merge, verify:

```bash
ls agents/maintenancer/{meta.json,soul.md,rule.md,workflow.md,tools_note.md,memory.md,growth.md,skill-set.yaml}
test "$(jq -r '.tools.allow | join(",")' agents/maintenancer/meta.json)" = "system-log,ens-db,knowledge,system_upgrade,db"
test "$(jq -r '.tools.deny | join(",")' agents/maintenancer/meta.json)" = "git_commit,edit_file,write_file,system_restart"
test "$(jq -r '.team_members | join(",")' agents/leader/meta.json | tr ',' '\n' | grep -c '^maintenancer$')" = "1"
```

### W1 Step 2 — Land P2 (tool layer + exclusivity + frozenset + dual pin updates)

```bash
git merge --no-ff feature/maintenancer-agent-p2
```

P2 lands:

- `daemon/tools/ens_db_tools.py` (new — `ens_db_*` tool factory)
- `daemon/tools/_tool_registry.py` (`CATEGORY_MODULES`, `DYNAMIC_TOOL_NAMES`, `PRIVILEGED_TOOL_CATEGORIES` update to `frozenset({"system_upgrade", "system-log", "ens-db"})`)
- `daemon/tools/instance.py` (`create_ens_db_tools` wiring inside `create_instance_tools`)
- `agents/developer/meta.json` + `agents/developer[v2]/meta.json` + `agents/wanderer/meta.json` + `agents/_baby_template/meta.json` — `system-log` removed (de-scope)
- Same-PR dual-pin updates: `tests/unit/tools/test_upgrade_registration.py:100` + `tests/unit/tools/test_attestation_registration.py:154`

After the merge, verify the frozenset and pin updates:

```bash
grep -A2 "PRIVILEGED_TOOL_CATEGORIES" daemon/tools/_tool_registry.py | head -10
grep "system_upgrade\|system-log\|ens-db" tests/unit/tools/test_upgrade_registration.py | head -5
grep "system_upgrade\|system-log\|ens-db" tests/unit/tools/test_attestation_registration.py | head -5
```

### W1 Step 3 — Land P3 (KB docs + kb-curator skill + KB INDEX in memory.md)

```bash
git merge --no-ff feature/maintenancer-agent-p3
```

P3 lands:

- The six KB docs under `agents/maintenancer/knowledge/`
- `agents/maintenancer/skills-template/kb-curator.md` (first concrete skills-template file)
- The KB INDEX entries in `agents/maintenancer/memory.md`

After the merge, verify the KB INDEX, six KB docs, and kb-curator exist:

```bash
ls agents/maintenancer/knowledge/0*.md | wc -l              # expected: 6
test -f agents/maintenancer/skills-template/kb-curator.md
grep -c "^- 0[1-6]-" agents/maintenancer/memory.md           # expected: ≥ 6
```

### W1 Step 4 — Coherent-state invariant under mid-W1 crash-restart

If the daemon crashes and restarts between any of the W1 merges, the running process keeps the OLD `PRIVILEGED_TOOL_CATEGORIES = frozenset({"system_upgrade"})` — `system-log` is NOT yet privileged — explicit allows in `developer` / `wanderer` / `worker` still resolve — no exclusivity gap. The new category strings in `agents/maintenancer/meta.json` `tools.allow` (`"ens-db"`, `"system_upgrade"`, `"db"`, `"knowledge"`) are inert until P2 lands; they trigger a validation warning only (`daemon/registry.py:1178-1180`). No special action required; the W1 sequence is restart-safe.

---

## W2 — Skills + dispatcher pins (NO restart)

W2 lands P4 (the seven skills + dispatcher pattern refinement). W2 does NOT require a restart — the skills land in the bank and the dispatcher pattern is purely prompt prose.

### W2 Step 5 — Land P4

```bash
git merge --no-ff feature/maintenancer-agent-p4
```

P4 lands:

- Seven skill files under `agents/maintenancer/skills-template/` (kb-curator is already there from P3; P4 adds the remaining six: `log-forensics`, `job-mission-repair`, `ens-db-repair`, `restart-upgrade-ops`, `bug-advisory`, `health-check`)
- `agents/maintenancer/skill-set.yaml` populated with the seven skills (manifest matches frontmatter versions)
- `agents/maintenancer/rule.md` refined Cardinals #3 / #4
- `agents/maintenancer/workflow.md` refined dispatcher pattern

After the merge, verify skill manifest versions match `.md` frontmatter:

```bash
uv run pytest tests/unit/test_maintenancer_skill_versions_consistent.py -v
uv run pytest tests/unit/test_maintenancer_fan_in_valve.py -v
uv run pytest tests/unit/test_maintenancer_report_scrutiny.py -v
uv run pytest tests/unit/test_maintenancer_team_members_within_team.py -v
```

---

## W3 — P5 (docs + integration test) + ONE restart window

W3 is the final wave. It adds the docs + integration test (this PR) and then performs the one-time restart that brings the Maintenancer live. The restart is the boundary: before it, the leader's team does not include the Maintenancer; after it, the agent is reachable and the 3-factor gate is the only path that can arm `system_upgrade`.

### W3 Step 6 — Land P5 (this PR)

```bash
git merge --no-ff feature/maintenancer-agent
```

P5 adds:

- `agents/maintenancer/README.md` (humans-facing)
- `agents/maintenancer/ROLLOUT.md` (this file)
- `agents/leader/soul.md` + `agents/leader/workflow.md` — routing lines
- `tests/integration/test_maintenancer_end_to_end.py` (new e2e test)

After the merge, verify no prompt-surface drift:

```bash
uv run pytest tests/unit/tools/test_prompt_section_reference_integrity.py -v
```

### W3 Step 7 — Pause-first quiesce (checklist item 6)

Before the restart, quiesce any instance whose work overlaps the new agent. The Maintenancer does not run during W1/W2, but pause-first is a structural guard against in-flight tasks that might race the registry reload.

```bash
# Pause every RUNNING instance via the API.
curl -s -X POST http://localhost:8079/api/instances/<id>/pause
```

Confirm `is_paused=true` in the `instances` row (per pause-first convention; `daemon/routers/instances.py:647-669`):

```bash
psql -h <pg-host> -U <pg-user> -d ensemble_prod \
  -c "SELECT id, status, is_paused FROM instances WHERE status = 'RUNNING' AND is_paused = false;"
```

Expected: zero rows. If any row appears, pause it and re-check before proceeding.

### W3 Step 8 — Pool-config pre-flight on disposable PG (checklist item 5)

The `pool_recycle × pool_pre_ping × reset-on-return` interaction matters because the post-restart daemon re-opens PG connections against a database that has been continuously written to. Verify on a disposable PG instance **before** restarting the production daemon (architect §3.1, §10):

```bash
# 1. Boot a disposable PG (docker).
docker run -d --name pg-preflight -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16

# 2. Point a copy of the daemon at it via the per-field set (NOT POSTGRES_URL — factory.py reads
#    POSTGRES_* fields only).
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=55432
export POSTGRES_DB=postgres
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=test

# 3. Boot the daemon in disposable mode and run a small write/read burst.
./dev.sh &   # 30s soak
sleep 5
curl -s -X POST http://localhost:8079/api/instances/<id>/message \
  -H "content-type: application/json" \
  -d '{"role":"user","content":"ping"}'
sleep 30
# 4. Tear down.
docker rm -f pg-preflight
unset POSTGRES_HOST POSTGRES_PORT POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD
```

If the disposable burst surfaces any pool error (`PoolError`, `DisconnectionError`, `InvalidCachedStatementError`), the production daemon will surface it too. Fix the pool config before proceeding.

**PG role privilege check for the repair pool.** The Maintenancer's `ens_db_repair_execute` writes through a dedicated role. Verify the role has the privileges the tool needs and ONLY those:

```bash
PGPASSWORD=$POSTGRES_PASSWORD psql -h $POSTGRES_HOST -U $POSTGRES_USER -d ensemble_prod -c \
  "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = 'ens_repair';"
# expected: rolsuper=f, rolcreatedb=f, rolcreaterole=f
PGPASSWORD=$POSTGRES_PASSWORD psql -h $POSTGRES_HOST -U $POSTGRES_USER -d ensemble_prod -c \
  "SELECT has_table_privilege('ens_repair', 'repair_log', 'INSERT,SELECT'),
          has_table_privilege('ens_repair', 'repair_log', 'UPDATE'),
          has_table_privilege('ens_repair', 'repair_log', 'DELETE');"
# expected: t,t,f  (INSERT+SELECT, UPDATE; no DELETE — repairs are forward-only)
```

If any check returns unexpected, fix the role grant BEFORE proceeding.

**Docker availability for PG tests.** The pre-flight recipe uses docker. Verify docker is available on the operator host:

```bash
docker --version
docker info | grep "Server Version"
```

If docker is missing on this host, run the pre-flight on an operator host that has docker and then return here. The pre-flight is not optional — skipping it is a structural risk.

### W3 Step 9 — Restart the daemon (one-time)

The restart is required because `_registry` is an import-time singleton (`daemon/registry.py:1170-1175`); `_registry.discover()` runs once per process. The new agent + privileged categories are only picked up on a fresh process.

```bash
# Pause-first was Step 7; all RUNNING instances are paused.
./dev.sh restart    # or your operator-specific restart command
sleep 5             # let the daemon boot
grep "Creating PostgreSQL engine" data/logs/ensemble.log   # confirm fresh boot
```

If the boot line is missing, the daemon is still running on the old registry; investigate before proceeding.

### W3 Step 10 — Run all pin tests

The full pin-test matrix (W1 + W2 + W3):

```bash
# W1 pins
uv run pytest tests/unit/test_maintenancer_agent.py -v                          # P1 1.11
uv run pytest tests/unit/tools/test_ens_db_tools_select_only.py -v             # P2 2.10a
uv run pytest tests/unit/tools/test_ens_db_repair_audit.py -v                  # P2 2.10b
uv run pytest tests/unit/tools/test_ens_db_repair_idempotent.py -v             # P2 2.10c
uv run pytest tests/unit/tools/test_ens_db_repair_refuses_non_idempotent.py -v  # P2 2.10d
uv run pytest tests/unit/tools/test_privileged_category_system_log.py -v       # P2 2.10e
uv run pytest tests/integration/test_maintenancer_spawn_resolves_tools.py -v   # P2 2.10f
uv run pytest tests/integration/test_maintenancer_upgrade_gate_refuses.py -v   # P2 2.10g

# W2 pins
uv run pytest tests/unit/test_maintenancer_skill_versions_consistent.py -v
uv run pytest tests/unit/test_maintenancer_fan_in_valve.py -v
uv run pytest tests/unit/test_maintenancer_report_scrutiny.py -v
uv run pytest tests/unit/test_maintenancer_team_members_within_team.py -v

# W3 pin
uv run pytest tests/integration/test_maintenancer_end_to_end.py -v
```

Expected outcome: all green. If any red, the registry reload did not pick up the new agent — investigate before proceeding to the smoke-spawn.

### W3 Step 11 — Smoke-spawn

Spawn the Maintenancer via the leader and verify the agent loads its KB INDEX, reads a sample KB doc on demand, runs the first-turn RAG mirror, dispatches a worker loaded with `log-forensics`, and produces a clean Maintenance Report. Verify the resolution surface: `system-log` + `ens-db` + `system_upgrade` (non-`system_restart` subset per D20) + `knowledge` + `db` (external only) all resolved; `system_restart` and source-mutation tools (`write_file` / `edit_file` / `git_commit`) absent.

```bash
# Leader spawns the Maintenancer.
curl -s -X POST http://localhost:8079/api/instances \
  -H "content-type: application/json" \
  -d '{"agent_id":"maintenancer","message":"smoke-spawn: please run the verification flow"}'

# Wait for the spawned instance to surface in /api/instances.
sleep 5

# Inspect the resolved tool set on the spawned instance.
curl -s http://localhost:8079/api/instances/<id>/tools | jq '.resolved | sort'
# Expected: ens_db_inspect, ens_db_pool_status, ens_db_postgres_select, ens_db_repair_execute,
#           ens_system_log_list, ens_system_log_read, ens_system_log_search, ens_system_log_tail,
#           explore, experience, release_info, upgrade_status, system_upgrade,
#           db_conn_add, db_conn_delete, db_conn_list, db_conn_test, db_postgres_dml_select
# NOT present: system_restart, write_file, edit_file, git_commit
```

Then read the smoke-spawn report back:

```bash
curl -s http://localhost:8079/api/instances/<id>/messages | jq '.[-1].content'
```

Expected: a Maintenance Report that references the KB INDEX (one of the six KB doc names), the first-turn RAG mirror (or a "knowledge offline, skipping mirror" note), and the `log-forensics` dispatch (worker report attached).

### W3 Step 12 — Monitor the first 24 hours

Two log surfaces to watch:

```bash
# 1. System log for denials + 3-factor gate refusals.
tail -f data/logs/ensemble.log | grep -E "denied|factor_failures|user-confirmation-missing|nonce-mismatch"

# 2. Upgrade journal for promote-refusal events (architect §5.3).
tail -f $INSTALL_DIR/releases/state.json | jq '.'
```

If any denial appears in the first 24h, the resolution surface has a leak. If a `factor_failures` event appears for a legitimate user nonce, the 3-factor gate is broken. Both are stop-the-line.

### W3 Step 13 — Worker break-glass verified

The Maintenancer holds NO direct `bash` — incident-time raw-bash fallback goes through `worker` (which retains `system-log` as break-glass per OPEN B Option 1). Verify the break-glass path works end-to-end:

```bash
# Leader spawns a worker for raw bash.
curl -s -X POST http://localhost:8079/api/instances \
  -H "content-type: application/json" \
  -d '{"agent_id":"worker","message":"break-glass: tail -50 data/logs/ensemble.log"}'

# Verify the worker can read the log (system-log is break-glass).
sleep 5
curl -s http://localhost:8079/api/instances/<worker-id>/messages | jq '.[-1].content'
```

Expected: the worker returns a `tail -50` excerpt with timestamps. The Maintenancer would then read this excerpt via its own `system-log` tools and time-bracket the result.

If the Maintenancer itself errors out mid-incident, the leader re-spawns a fresh instance — replayable, no state carried across spawns.

---

## Rollback (per architect §2.4 — complete)

### Pre-P2 rollback (anytime in W1)

If a defect is found in W1 BEFORE P2 lands:

- **P1 rollback:** `git revert -m 1 <merge-sha>` — reverts the P1 merge. Verify the leader team table drops `maintenancer` and the meta's `tools.allow` reverts to the W0 form.
- **P2 rollback:** `git revert -m 1 <merge-sha>` — reverts the P2 merge. Verify the frozenset reverts to `frozenset({"system_upgrade"})` and the de-scopes on `developer` / `wanderer` / `_baby_template` revert.
- **P3 rollback:** `git revert -m 1 <merge-sha>` — reverts the P3 merge. Verify the six KB docs and `kb-curator` are gone.

In any of these, no restart is required — the pre-P2 state was import-time-coherent.

### Post-P2 rollback (after P2 has landed)

After P2 lands, a rollback reverts the full structural surface:

```bash
# 1. Revert the P1-P2-P3 merges in reverse order (P3 first, then P2, then P1).
git revert -m 1 <p3-merge-sha>
git revert -m 1 <p2-merge-sha>
git revert -m 1 <p1-merge-sha>

# 2. Re-apply the W2-P4 reversion (if landed).
git revert -m 1 <p4-merge-sha>

# 3. Re-apply the W3-P5 reversion (this PR).
git revert -m 1 <p5-merge-sha>
```

After the reverts, manually verify the structural surface:

```bash
# Frozenset reverts to the pre-P2 form.
grep "PRIVILEGED_TOOL_CATEGORIES" daemon/tools/_tool_registry.py
# expected: frozenset({"system_upgrade"})

# CATEGORY_MODULES regen reverts (no ens-db, no system-log-priv).
grep "ens-db\|system-log" daemon/tools/_tool_registry.py

# DYNAMIC_TOOL_NAMES / KNOWN_TOOL_NAMES regen reverts.
grep "DYNAMIC_TOOL_NAMES\|KNOWN_TOOL_NAMES" daemon/tools/_tool_registry.py

# De-scopes revert (developer / developer[v2] / wanderer / _baby_template get system-log back).
jq '.tools.allow' agents/developer/meta.json agents/developer\[v2\]/meta.json \
  agents/wanderer/meta.json agents/_baby_template/meta.json

# agents/maintenancer/meta.json allow-list additions revert (or the file is deleted).
test ! -f agents/maintenancer/meta.json

# tools.deny entries removed (system_restart is no longer in deny — D20 enforcement lifted).
jq '.tools.deny' agents/maintenancer/meta.json 2>/dev/null

# create_ens_db_tools wiring reverts (no ens_db_* in the instance tool factory).
grep "ens_db_tools\|ens_db_postgres_select\|ens_db_repair_execute" daemon/tools/instance.py

# Same-PR dual pin updates revert (test_upgrade_registration.py:100 + test_attestation_registration.py:154).
grep "system-log\|ens-db" tests/unit/tools/test_upgrade_registration.py tests/unit/tools/test_attestation_registration.py
```

After all checks pass, **restart the daemon** to pick up the reverted state (the registry singleton re-discovers):

```bash
./dev.sh restart
sleep 5
```

### Executed repairs have NO structural rollback

If the Maintenancer has already executed `ens_db_repair_execute` and written repair rows to `ensemble_prod`, those rows are **not** reverted by the rollback recipe above. Repairs are forward-only; recovery uses:

1. **Snapshot before repair** — every repair execution captures a pre-repair snapshot into `repair_log.snapshot` (column-stamped JSON).
2. **Idempotent `DO$$` forward-fix** — if a repair was wrong, write a NEW repair row (with a new audit stamp + dry-run-first) that fixes the previous one. The `repair_log.created_by` column tracks the lineage.

The structural rollback above reverts the *agent surface* (meta, frozenset, deny-list, wiring). It does NOT touch repair rows — those are the durable record of what the agent did, and reversing them requires a new repair row, not a rollback commit.

---

## Kill-switch: `ENSEMBLE_REPAIR_ENABLED`

The `ens_db_repair_execute` tool ships with a kill-switch that defaults to **ON**. To disable (e.g., during a freeze window or after a security review):

```bash
export ENSEMBLE_REPAIR_ENABLED=0
./dev.sh restart
```

With the kill-switch OFF, every `ens_db_repair_execute` call returns a refusal summary listing the kill-switch as the cause. SELECT-only reads (`ens_db_postgres_select`, `ens_db_inspect`, `ens_db_pool_status`) are NOT affected — those are read-side and do not need the kill-switch.

To re-enable:

```bash
unset ENSEMBLE_REPAIR_ENABLED
./dev.sh restart
```

The kill-switch is per-process; restart is required for the flip to take effect.

---

## Acceptance summary

Wave 3 deploy is complete when ALL of the following are true:

- [ ] W1 (P1, P2, P3) merged — file-disjoint, no in-wave restart.
- [ ] W2 (P4) merged — no restart, pin tests green.
- [ ] W3 (P5, this PR) merged.
- [ ] Pause-first quiesce verified — zero RUNNING + unpaused rows.
- [ ] Pool-config pre-flight on disposable PG passed.
- [ ] PG role privilege check on `ens_repair` passed (no superuser, INSERT+SELECT+UPDATE only).
- [ ] Docker availability verified.
- [ ] Daemon restarted; fresh boot line in `data/logs/ensemble.log`.
- [ ] All W1 + W2 + W3 pin tests green.
- [ ] Smoke-spawn resolved the expected tool surface (no `system_restart`, no source-mutation tools).
- [ ] Smoke-spawn produced a Maintenance Report with KB INDEX, RAG mirror, and `log-forensics` dispatch.
- [ ] 24h monitor window elapsed with no denials or `factor_failures` events.
- [ ] Worker break-glass verified — `worker` with `system-log` returned a `tail -50` excerpt.
- [ ] Rollback recipe dry-run (operator rehearsed, no actual rollback executed).

If any item is unchecked, do not declare Wave 3 complete. Investigate and re-run.
