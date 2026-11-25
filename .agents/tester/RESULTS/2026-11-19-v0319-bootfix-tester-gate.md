# v0.31.10 boot-crash fix lane — TESTER GATE (pre-merge)

**Verdict: ✅ PASS-WITH-NOTES — net-new failures = 0 across the declared Tier-1 blast radius.**

| field | value |
|---|---|
| lane | `fix/v0.31.9-boot-crash` (v0.31.10 combined release target) |
| worktree | `/home/nea/ensemble-src-wt-v0319-boot-crash` |
| HEAD | `b2d54e849` (5 commits over base) |
| base | `28ec14338` (TRUE pre-fix base) |
| tier | **Tier 1** — scoped dual-side FAILED-digest diff (mechanical: neither changed path is on the ensure.md core-seam list) |
| recipes | `.agents/tester/skills/blast-radius-leg/`, `.agents/tester/skills/failed-digest-equality-gate/` (read by deterministic path, never `skill_search`) |
| date | 2026-11-19 |
| workers | `80fad510` (focused settings), `632335e5` (focused restart), `0ea86616` (discovery), `31f3218f` (mutation), `0e88a675` (Tier-1 dual-side leg) |

## 1. Scope decision (blast radius)

Change set (verbatim `git diff --name-only 28ec14338..HEAD`):

```
.agents/tester/PACKS.md
daemon/services/settings_service.py
daemon/services/upgrade_journal_sweep.py
scripts/upgrade/boot_smoke.sh
tests/test_settings_resolution.py
tests/unit/tools/test_agent_restart.py
```

Full requested; scoped to the changed modules' blast radius (Tier 1) — 6 files / 2 modules, no core-seam path, so a whole-tree Tier-2 leg was NOT warranted.

**Declared dual-side set: 52 files** (symbol scan `settings_service|apply_db_settings_overrides|upgrade_journal_sweep|_render_applied_overrides|MANAGED_KEYS` + derived-symbol widening + name mapping + the registered `agent_restart_targeted_head_heavy` pack's files). All 52 exist at base ⇒ single comparable class (no HEAD-only files). Slicing proof: base **1537** nodes / head **1549** nodes (Δ=+12, attributed to the two lane files).

**Named out-of-leg (disclosed, not dropped):** `tests/test_settings_api.py` (postgres-marked, 37 deselected; needs `--override-ini="addopts=" -m postgres` + dev-DB writes — fenced by this commission), `tests/integration/test_notify_user_boot_smoke.py` + `test_wedge_capture_boot_smoke.py` (opt-in smokes, disclosed false-PASS shape), `tests/postgres/test_df4_*` (postgres-marked), `tests/packs/*` (script-registered; g7 `sys.exit(0)` collection trap), `tests/test_agent_restart_sim.sh`, `tests/test_supervision_twins.sh`, `tests/test_upgrade_overhaul_all.sh`, `tests/unit/tools/test_promote_cgroup_survivorship_python.py` + `test_supervision_python.py` (self-contained `__main__`, 0 collectable), `tests/fixtures/supervision_stub_daemon.py`, `scripts/upgrade/boot_smoke.sh` (**not executed by commission** — devops/tester run it against the staged artifact).

## 2. Gate 1 — focused suites (verbatim)

| suite | verbatim summary | rc | runtime |
|---|---|---|---|
| `tests/test_settings_resolution.py` | `47 passed, 1 warning in 4.22s` | 0 | 4.22s pytest / 10s wall |
| `tests/unit/tools/test_agent_restart.py` | `33 passed, 1 warning in 1.54s` | 0 | 1.54s pytest / 11s wall |

Expected 47 / 33 — **both exact**. Sole warning is the pre-existing `langchain_core` Pydantic-V1-on-3.14 `UserWarning` (environment-level, lane-innocent).

## 3. Gate 4 — mutation-verified regression pin (independent re-verification)

Mutation: both per-key `except` arms in `apply_db_settings_overrides` → `raise` (fail-soft removed), applied in a **throwaway** `/tmp/v0319-mutate` detached worktree (`uv sync --frozen`; `daemon.__file__` = `/tmp/v0319-mutate/daemon/__init__.py`).

- RED: `3 failed, 44 passed, 1 warning in 6.74s` — exactly the fail-soft pins:
  `TestFailSoftBootApply::test_wrong_path_nonexistent_attr_skipped`, `::test_wrong_path_section_root_skipped`, `::test_resolver_failure_isolated_per_key_boot_never_aborts` (3 reds, **0 extras**).
- RESTORE: sha256 PRE == POST = `d198898edc075ffcc914eeac3aac03bd72ec0ad9e354878927cd16431b230d66`.
- GREEN: `47 passed, 1 warning in 6.29s` (collected total identical).
- Teardown: worktree removed + pruned; `git worktree list` shows 0 matches.

**MUTATION-PIN: VERIFIED.**

## 4. Gates 2+3 — Tier-1 dual-side FAILED-digest diff

Per-file isolation (own pytest process per file), identical command and env on both legs, base leg FIRST, never concurrent. Env fence: per-variable `env -u` scrub → source worktree `.env` → assert `POSTGRES_DB=ensemble_dev` + `ENSEMBLE_SELF_ENV=dev` → run. Per-file `timeout 300`; pyproject per-test timeout as inner layer; `-p no:randomly -q --tb=short -rEf`; never `-x`. Python 3.14.7 both legs; `daemon.__file__` asserted inside each tree.

**Head leg: 52/52 files executed, 1549 nodes, 45 files rc=0, 7 files rc=1, 0 missing.**

### Digests — (form, sha256) pairs

| form | BASE | HEAD |
|---|---|---|
| D1 canon node-id list (31 → 13 lines) | `b95653d5083c4457c1153968ed0b40c28b75e160388e4a96b37afe344a2d03b7` | `a5dac46a4b3ebf7c0a9369f7f09506b74d257733cf30df642190b54bf7da9f09` |
| D1-literal (commission pipeline) (31) | `2e2b32caa2f5ba7a87f3941c76a8284e081cf537a51e4c18c2697aa53fb0c091` | `a5dac46a4b3ebf7c0a9369f7f09506b74d257733cf30df642190b54bf7da9f09` |
| **D2 node-id form** (25 → 13) | `fdd00d3e7b960c6468500489c897d911816b3716d2bc565c938a25180fb73b1f` | `a5dac46a4b3ebf7c0a9369f7f09506b74d257733cf30df642190b54bf7da9f09` |
| D3 grep-full sorted (31 → 13) | `802dc229dd2ce283b52ece54ac24a5504ff36ff356488dc5332293e4e9ef3bb9` | `1898fa5b96c33ec11cda58069a26ee3f1f97cec1035ed980567d75a237f67338` |

### Set diffs

- `comm -13 base.d2 head.d2` (**head-only = NET-NEW**) → **EMPTY**
- `comm -23 base.d2 head.d2` (base-only = fixed at head) → **12 node ids**: 10× `tests/test_settings_resolution.py::TestBootstrapApply::*` + `tests/test_config.py::TestWorkerPoolYamlShadow::test_valid_env_value_wins_over_yaml` + `tests/unit/test_instance_cache_bounding.py::test_boot_apply_mutates_config_and_publishes_resolution`
- `comm -13/comm -23` on D1 and D3 → same empty/17 result; independent Python set-diff reproduces (D1 base-only 17 / head-only 0; D2 12/0; D3 17/0).
- **3× isolated sequential adjudication** (no xdist) on the 12 fixed nodes: base 3/3 rc=1 (12 failed each), head 3/3 rc=0 (12 passed each) ⇒ deterministically fixed, not order/load-sensitive. Net-new set was empty ⇒ 0 nodes required adjudication; no flake evidence anywhere in the leg.

### Log-artifact masquerade (cleared, not regressions)

Base carried 6 `^(FAILED|ERROR)`-matched lines that were **pytest captured-log records**, not node ids (`settings_service.py:1087/1095/1365 [Settings] …`). Head carried **0**. All 6 owning nodes were base-RED and head-GREEN ⇒ the artifacts vanished because their owners went green (no captured-log section is printed for a passing test) and the lane reworded the fail-soft log lines. **0 artifacts whose owner was green at base.** D2 removes them; the verdict is computed on D2.

### Per-file node-id set delta (lane files)

- `tests/test_settings_resolution.py` 36 → 47 (**+11, zero removed/renamed**): `TestFailSoftBootApply` ×9 + `TestManagedKeysConsistency` ×2. The 10 base-red `TestBootstrapApply` ids are present at head and GREEN ⇒ **fixed-at-head**, not removed-by-lane.
- `tests/unit/tools/test_agent_restart.py` 32 → 33 (**+1, zero removed**): `TestCase5NotifyContents::test_render_applied_overrides_applied_false_renders_skipped`.
- Every other file's node count identical on both legs.

## 5. Gate 5 — QUARANTINE intersection

13 reds identical at **both** legs ⇒ pre-existing by construction. Failing files at HEAD ∩ lane diff = **EMPTY** (commission untouched).

| # | node id | ledger status |
|---|---|---|
| 1–5 | `tests/test_daemon_settings_api.py::TestPutDelete::test_put_actor_bearer_subject`, `::test_put_happy_actor_capture_auth_on`, `TestScopeGates::test_get_403_without_read_scope_200_with`, `::test_put_delete_403_without_write_scope`, `::test_read_scope_does_not_grant_write` (5 ERRORs) | **UN-LEDGERED** (0 hits in either QUARANTINE.md copy; the only settings-API row is the *different* file `tests/test_settings_api.py`) |
| 6 | `tests/unit/metrics/test_metrics_family_adjudication.py::TestNoProducerInstrumentation::test_the_daemon_diff_is_confined_to_the_allowed_files` | **UN-LEDGERED** |
| 7 | `tests/unit/services/test_auto_continue_boot_pass.py::TestLifespanEnvelope::test_api_py_wraps_pass_in_try_except` | **UN-LEDGERED** |
| 8 | `tests/unit/services/test_vgap_episode_hygiene.py::TestWedgeEpisodeEndPurges::test_wedge_entry_discarded_on_parent_left_wc` | **UN-LEDGERED** |
| 9 | `tests/unit/test_long_tool_nudge.py::TestU11NoticeStructure::test_length_within_1_5x_wedge_notice` | **UN-LEDGERED** |
| 10 | `tests/unit/tools/test_checkpoint_pool_config.py::TestCheckpointPoolConfigLadder::test_the_field_defaults_mirror_the_constants` | **UN-LEDGERED** |
| 11–13 | `tests/unit/test_maintenancer_agent.py::TestConventionCompliance::test_all_prompt_files_exist`, `::test_prompt_file_non_empty[memory.md]`, `::test_no_forbidden_system_tokens[memory.md]` | **LEDGERED** (row *agent-restart builder-stage base-parity set (consolidated)*, member `tests/unit/test_maintenancer_agent.py::TestConventionCompliance ×3`) |

No ledgered node was absent at head. **10 un-ledgered pre-existing reds = ledger gap** (none lane-attributable, none created or moved by the lane).

## 6. Notes / findings

- **N-1 (ledger gap, 🟠):** 10 pre-existing reds are absent from QUARANTINE.md — they will red any future pack that includes those files. QUARANTINE.md rows were NOT added: this commission's writes are restricted to `.agents/tester/RESULTS/`.
- **N-2 (pack not yet exercised, 🟠):** the lane's registered pack `boot_smoke_v0319` (packaged-artifact boot smoke) is **not executed** here — the packaged artifact does not exist pre-merge. Stays pending the devops/tester staged-artifact run; the v0319 PACKS.md row already says *REGISTERED — pending tester stage+run*.
- **N-3 (doctrine, 🟡):** the Tier-1 leg ran **46 m 48 s** (10:43:07Z → 11:29:55Z) against ensure.md's *≤30 min HARD* line — breached, fail-loud, authorized by the tester lead for this leg only. Root cause: per-file process isolation (52 processes/side ≈ 19 min of startup overhead) on a set that contained **no** in-process polluter. Anomaly `65bfb1a2` filed; proposal: multi-file legs default to the registered single-invocation form or LPT shards. (Ledger: `/tmp/v0319-base-logs/`, `/tmp/v0319-head-logs/`, `/tmp/base.d{1,2,3}.txt`, `/tmp/head.d{1,2,3}.txt`.)
- **N-4 (process, 🟢):** the declared set shipped with 2 wrong paths (`tests/test_settings_peak_hours.py`, `tests/test_maintenancer_agent.py` — the real files are under `tests/unit/`); caught by the leg worker's MISSING report and corrected before the head leg, so both legs ran the identical 52-file set.
- **N-5 (measurement, 🟢):** the HEAD slicing proof used one multi-file `--collect-only -q` (read-only) instead of 52 per-file invocations; per-file counts derived from the node-id listing, leg total from pytest's summary (`1549 tests collected in 8.83s`). The RUN form was per-file and byte-identical on both legs ⇒ no measurement impact.
- **N-6 (coverage gap, 🟡):** `tests/test_settings_api.py` (37 postgres-marked nodes, settings-API persistence) stayed out-of-leg: it needs `--override-ini="addopts=" -m postgres` plus dev-DB writes, which this commission fences (never CREATE DATABASE; `ensemble_dev_app` has rolcreatedb=FALSE). Needs a DB-write-authorized commission.
- **MK\* false positives:** `tests/unit/services/test_metrics_gate.py` (35) and `test_metrics_refresh_path.py` (12) match `metrics_gate.MANAGED_KEY` (a different constant), not the settings registry's `MANAGED_KEYS` — kept in the set (registered `metrics_p2_dual_side` row) but labelled; PASS on both legs.

## 7. Safety attestation

No daemon boot. No `ensemble_prod` contact (not even reads). No process killed (port 8088 untouched). No `CREATE DATABASE`; dev-family DB only. `scripts/upgrade/boot_smoke.sh` NOT executed. No source/test/PACKS.md modification in either tree: HEAD worktree `git status --porcelain` = 0 lines (byte-pristine, no `__pycache__` created); main-checkout porcelain sha before == after = `488f6522e8937a638ec576ada2195861c70cde9fc066dc89de17aefd8080c244`. Scratch worktree `/tmp/v0319-base-28ec14338` removed + pruned (0 matches). Writes confined to `/tmp` and this file. Per-variable `env -u` scrub everywhere (never the `POSTGRES_*` glob no-op).

## 8. Reproduction

```sh
cd /home/nea/ensemble-src-wt-v0319-boot-crash
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u PORT \
  bash -c 'set -a; source ./.env; set +a
    [ "$POSTGRES_DB" = ensemble_dev ] || exit 97
    timeout 300 .venv/bin/python -m pytest tests/test_settings_resolution.py -p no:randomly -q --tb=short -rEf'
# base leg: git worktree add --detach /tmp/v0319-base-28ec14338 28ec14338 && uv sync --frozen, same command, same order
# digest: grep -E '^(FAILED|ERROR)' … | sort | sed -E 's/^(FAILED|ERROR) //; s/ - .*$//'
```

## 9. Overall status

- Focused suites: ✅ PASS (47 + 33, verbatim)
- Tier-1 dual-side regression: ✅ PASS — net-new **0**, 12 fixed, 13 pre-existing (3 ledgered / 10 un-ledgered)
- Mutation pin: ✅ VERIFIED (3 RED → restore GREEN, sha PRE==POST)
- ensure.md Core: not in scope for this change set (no pack in the change set maps to `concurrency_atomic_unit_test`; `dev.sh --timeout-graceful-shutdown 10` grep — unchanged file, out of blast radius)
- `boot_smoke_v0319` packaged-artifact gate: ⏳ pending staged-artifact run (devops/tester)
- **Testing complete for the pre-merge gate: ✅ PASS-WITH-NOTES** (notes N-1..N-6 above; none is lane-attributable)

## 10. Leader adjudications (2026-11-19 — recorded, gate GREEN for merge)

Verdict accepted: **PASS-WITH-NOTES, net-new = 0 — gate GREEN for merge.**

1. **N-1 (ledger gap) — ROUTE, do not add.** QUARANTINE.md rows for the 10 pre-existing reds are out of this release's commissioned scope (config/boot defect + gate additions only); adding them on this branch would expand the release diff. Routed to the ledger owner **post-release**. **Handoff artifact = this file** — §5 carries the full node-id list. *Tester action taken: none, as directed (no QUARANTINE.md edit on this branch).*
2. **N-3 (ensure.md amendment) — YES, draft post-release.** "Multi-file legs default to single-invocation or LPT-shard form" is the right direction; draft it as a **proposed diff** AFTER the release lands, for the ensure.md owner's sign-off. The tester never edits ensure.md mid-release. *Tester action: deferred to the post-release cycle; rationale carried by anomaly `65bfb1a2` + POK `multifile-leg-form-shards-not-perfile`.*
3. **N-6 (postgres-marked settings API) — NO separate pre-merge leg.** The staged packaged-artifact boot smoke exercises the real `apply_db_settings_overrides` path against a real PG end-to-end — strictly stronger than the 37 postgres-marked unit nodes. N-6 stays a **disclosed gap**; revisit only if the boot smoke surprises. *Tester action: none.*

## 11. Next assignment (staged verification) — readiness notes

Re-dispatch scope (after devops stages v0.31.10): (a) boot smoke on the **packaged** v0.31.10 artifact, (b) the **S2 canary leg** — extract the staged `lib.sh` and execute it against the canary: zero commands executed, text intact, (c) **guard-unchanged proof** — the v0.31.8 guard-inversion closing fix rode into this lane unmodified; prove it is byte-identical and still denies all 10 shapes.

Carried-forward constraints: `boot_smoke.sh` is executed **only** against the staged artifact (never pre-merge, never against a live daemon); the registered `boot_smoke_v0319` contract clauses (cgroup isolation with no shared-cgroup fallback, both kill-switches pinned, DSN family scrubbed + asserted, house port range 19100-19999, FAKE_HOME + prefix-guarded `rm`, pinned config/agents, PyInstaller 2-PID process-group teardown + port-release assertion, `/livez` **and** `/readyz` components, DB row snapshot/restore, seeded rows `limits.worker_pool_size=7` / `wedge_notice_emission_enabled=true` / `smoke_unknown_legacy_key=true`); per-variable `env -u` scrub (never the `POSTGRES_*` glob no-op); dev-family scratch only, never CREATE DATABASE; no live-daemon contact; never kill port 8088.
