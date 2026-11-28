# Research Findings — Conventions & Observability Tool (revive-wedge lane)

Provenance: READ-ONLY research pass. Worktree `/home/nea/ensemble-src-wt-revive-wedge`, branch
`fix/revive-wedge-terminalization`, HEAD `35ec50e3c9b3fc9a487e4f2dbad23c15a0837f45` (= origin/latest).
All paths relative to that worktree. Captured 2026-11-25 ~08:00–08:25Z. The canonical checkout
`/home/nea/ensemble-src` was never read.

INSTRUMENT CAVEAT (anomaly `7db97170`, filed): `grep_files` silently returned "No matches" on
several file-scoped greps whose targets verifiably contain the pattern (verified by read_file):
`tests/test_system_log_tools.py` ("def ", "log_dir"), `tests/unit/tools/test_privileged_category_system_log.py`
("def test"), `scripts/upgrade/stage.sh` ("rollback_safe"). Single-file greps on `.agents/tester/PACKS.md`
succeeded. Every "no X found" claim below that relies on one of those greps is flagged `[instrument-limited]`.

---

## GOAL A — Observability tool: `ens_system_log_search`

### Implementation file
- `daemon/tools/system_log_tools.py` (719 lines). Module docstring :1-17. Factory
  `create_system_log_tools(manager, current_instance_id)` at :256, returns the 4-tool list at :719
  (closure-injection pattern mirroring `daemon.tools.chart_tools`; `ens_db_tools.py:760-761` cites
  "create_system_log_tools closure-injection pattern at daemon/tools/system_log_tools.py:256").
- `ens_system_log_search` closure def at **:414**, decorated `@register_tool_category("system-log")` :412
  + `@tool` :413. Sibling tools in the same factory: `ens_system_log_list` :278, `ens_system_log_read` :331,
  `ens_system_log_tail` :631.

### Args schema (signature-derived — THERE IS NO SEPARATE SCHEMA DICT)
Declared by the function signature (:414-420); the `@tool` decorator (`daemon/tools/_schema_registry.py`)
emits the JSON schema from it:
```python
def ens_system_log_search(
    pattern: str,                 # REQUIRED — regex, Python re syntax
    filename: str = "ensemble.log",
    level: str | None = None,     # case-insensitive " - LEVEL - " substring filter
    context=0,                    # DELIBERATELY UNANNOTATED (:418) — accepts int OR non-int,
                                  #   runtime-validated :458-465, clamped [0, MAX_CONTEXT=100]
    limit: int = 50,              # capped min(limit, 50) at :442
) -> str:
```
- Required: `pattern`. Optional with defaults: `filename`, `level`, `context`, `limit`.
- Docstring Args block :427-436 doubles as the LLM-facing documentation.
- Constants (:49-63): MAX_CONTEXT=100, MAX_LINES_READ=500, MAX_LINES_TAIL=200,
  MAX_BYTES_RESPONSE=12*1024, **MAX_LINES_SCAN=50_000**, MAX_LINE_LENGTH=2000, TAIL_SEEK_CHUNK=64KB.

### Tail-only scan implementation (the load-bearing detail)
"T TAIL-FIRST SCAN STRATEGY" (:469-511 comment, implementation :512-627):
1. ONE full streaming pass via `_open_log_file(filepath, log_dir)` (dir_fd + O_NOFOLLOW open,
   anti-TOCTOU/symlink-swap; all OSError subclasses → friendly error strings, :505-510, :520-525).
2. Feed `deque(maxlen=MAX_LINES_SCAN)` of `(0-indexed absolute line no, raw line)` (:512-519).
   After the pass the deque holds the **LAST 50,000 lines** — everything earlier is auto-evicted.
3. Iterate the deque in FORWARD (chronological) order applying unchanged match/context/level/limit/
   byte-cap logic (:529-627): truncate BEFORE regex (:544), redact AFTER (:547), level substring
   filter (:550-553), `>>>` match marker + 6-wide line numbers, `---` block separators, context
   before/after buffers, MAX_BYTES_RESPONSE truncation marker, `match_count >= limit` stop (:605).
4. Line numbers stay ABSOLUTE (i+1 from enumerate over the whole file) despite the windowed scan.
5. Header reports `File: {filename} | Pattern: {pattern} | {match_count} match(es) | scanned {scanned} lines`
   (:623-626); no-match message :617-621 includes `scanned {scanned} lines`.

So: the scan is bounded by line COUNT (50k) not bytes; a log with >50k lines silently searches only
the recent 50k while still printing true absolute line numbers.

### Existing tests
- `tests/test_system_log_tools.py` (1,426 lines) — 5 lanes per module docstring (:1-30), hermetic via
  `tmp_path` + `monkeypatch.setenv("DAEMON_LOG_DIR", ...)` (fixtures :53-84):
  - Lane 1 Factory `TestCreateSystemLogToolsFactory`: `test_factory_returns_four_tools`,
    `test_tool_names_correct`, `test_all_tools_have_category`, `test_all_tools_are_sync`,
    `test_factory_creates_independent_tools_per_call` (:100-142).
  - Lane 2 Registration `TestSystemLogToolRegistration`: `test_category_modules_has_system_log`,
    `test_category_modules_resolves_to_importable_module`, `test_all_tool_names_in_dynamic_tool_names`,
    `test_tools_not_registered_under_instance_category` (:150-185).
  - Lanes 3-5 Invocation / Security (path traversal, caps, redaction) / Integration
    (`_apply_tool_filter` survival). [test-method names below Lane 3 not enumerated — grep
    instrument-limited; read_file :190+ carries them.]
- `tests/unit/tools/test_privileged_category_system_log.py` (435 lines) — category exclusivity +
  de-scope pins: developer/developer[v2]/wanderer/watcher LOSE `system-log`; worker KEEPS it
  (designated break-glass); `_baby_template` allow-array contains zero privileged entries.
- Drift pin: `tests/unit/tools/test_frozen_tool_name_discovery.py::test_known_tool_names_matches_source_exactly_no_drift`
  (cited at `_tool_registry.py:1713-1714`) — adding a tool requires regenerating KNOWN_TOOL_NAMES via
  the documented command (`uv run python -c "from daemon.tools._tool_registry import
  discover_source_only_tool_names; print(sorted(discover_source_only_tool_names()))"` :1707) and
  pasting into the frozenset at :1719.

### Registration sites (complete list)
1. `daemon/tools/_tool_registry.py`:
   - `DYNAMIC_TOOL_NAMES` frozenset :26 — the four names at :53-56 ("created by per-instance
     factories rather than registered during module import... valid names in agent allow/deny
     config, so startup validation must know them").
   - `CATEGORY_MODULES` — `"system-log": "daemon.tools.system_log_tools"` at :1604.
   - `KNOWN_TOOL_NAMES` frozenset :1719 — the four names at :1836-1839.
   - `PRIVILEGED_TOOL_CATEGORIES` includes `"system-log"` (:488; prose block :385-505 — "daemon log
     forensics — designated break-glass for the worker agent"). ANY change to the privileged set
     touches SEVENTEEN pin files + mutation-pack STRING pairs (:407-473) — adding a PARAM does not
     touch this set; adding/removing a CATEGORY would.
2. Factory wiring: `daemon/tools/instance.py` :326 (import), :7835-7836 (`create_system_log_tools(...)`
   + `tools.extend(...)` inside `create_instance_tools`).
3. Loader: `daemon/loader.py` :507 (import), :588 (`create_system_log_tools(None, "")`), :651
   (wired into the tool assembly).
4. Docs/catalog: module docstring :1-17 + `CATEGORY_DOC` :37-47 feed the tool_help full-docs registry
   (`_full_docs` in `_tool_registry.py`, module docstring :1-9). No separate docs/ file.
5. Allow-lists: privileged category — reached ONLY via explicit `tools.allow: ["system-log"]`;
   empty-allow inherit never grants it (structurally enforced in `daemon/tools/instance`
   `resolve_tool_filter`, `_tool_registry.py:385-387`).

### Sibling pattern to copy for `start_line`
`ens_system_log_read` (:331-410) is the in-file precedent: `offset: int = 0` line-paging with
`if i < offset: continue` skip-stride (:385-387) and an honest recomputed header range (:405-409).
`ens_system_log_tail` (:631+) is the other tail reader (backward chunked seek, TAIL_SEEK_CHUNK=64KB).

### Where an optional `start_line` (and/or full-file mode) goes
1. **Schema entry**: add `start_line: int | None = None` to the signature — WITH a type annotation
   (contrast the deliberately unannotated `context`; annotation presence = schema emission). Insert
   after `filename` to keep positional order stable.
2. **Doc string**: extend the Args block (:427-436) + the Returns block if the header changes.
3. **Implementation branch**: in the deque feed loop (:513-519) — when `start_line` is not None,
   append only tuples with `i + 1 >= start_line` (same skip-stride idiom as read :386-387). For a
   FULL-FILE mode, `None`-vs-`0` semantics must be decided explicitly (recommend: keep `None` =
   current tail-window behavior, `start_line=1` = full file) — otherwise "full file" is
   indistinguishable from "default".
4. **Byte-identical invariants when the new param is absent (None)**: the deque maxlen window
   (50k-line eviction), absolute 1-indexed numbering, the `scanned {scanned} lines` header token
   and its exact format (:623-626), the no-match message (:617-621), `>>>`/`---`/context-block
   rendering (:557-606), all clamp/cap arithmetic (limit≤50, context≤100, 12KB, 2000-char lines),
   the error strings (invalid regex / missing file / not-a-file / OSError), and `context`'s
   unannotated-int quirk. Any of these drifting breaks the existing invocation-lane tests.
5. **Unchanged**: tool name (so DYNAMIC_TOOL_NAMES/KNOWN_TOOL_NAMES/CATEGORY_MODULES and the
   privileged-set pins all stay untouched); factory signature; loader wiring. Tests to extend:
   invocation lane of `tests/test_system_log_tools.py`; registration + frozen-name drift tests
   should pass untouched.

---

## GOAL B — Test + release conventions (quoted)

### B1. Genuine-red-at-base — the canon
**Canonical artifact**: `.agents/tester/skills/failed-digest-equality-gate/failed-digest-equality-gate.md`
(v1.0.3, 2026-11-18; "Base-vs-Head FAILED-Digest Equality Gate (project canon)"). Verbatim procedure:

```sh
# 1. Two throwaway DETACHED worktrees. NEVER the shared dirty checkout.
git -C /home/nea/ensemble-src worktree add --detach <BASE_WT> <BASE_SHA>
git -C /home/nea/ensemble-src worktree add --detach <HEAD_WT> <HEAD_SHA>

# 2. Per-tree venv. Assert the import resolves INSIDE that tree (a shared .venv
#    pins the other branch via the editable .pth).
cd <WT> && /home/nea/.local/bin/uv sync
.venv/bin/python -c "import daemon; print(daemon.__file__)"   # must be inside <WT>

# 3. IDENTICAL command, BASE LEG FIRST, then HEAD LEG. Never concurrently —
#    load coupling pollutes the measurement. Always under an explicit `timeout`.
cd <BASE_WT>
timeout 300 env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST \
  -u POSTGRES_PORT -u POSTGRES_PASSWORD -u DATABASE_URL \
  .venv/bin/pytest <SUITE> -q --tb=short -p no:randomly -n 8 > base.out 2>&1
# ...then the same in <HEAD_WT> -> head.out

# 4. Digest the SORTED failing-node-id list (BOTH FAILED and ERROR; strip " - <message>" suffix).
grep -E '^(FAILED|ERROR)' base.out | sort | sed -E 's/^(FAILED|ERROR) //; s/ - .*$//' > base.nodeids.txt
grep -E '^(FAILED|ERROR)' head.out | sort | sed -E 's/^(FAILED|ERROR) //; s/ - .*$//' > head.nodeids.txt
sha256sum base.nodeids.txt head.nodeids.txt

# 5. Verify BOTH ways: cmp AND comm in both directions.
cmp base.nodeids.txt head.nodeids.txt                 # rc=0 == byte-identical
comm -23 base.nodeids.txt head.nodeids.txt   # base-only  -> FIXED at head
comm -13 base.nodeids.txt head.nodeids.txt   # head-only  -> NET-NEW reds

# 6. Attribute any collected-count delta PER FILE:
.venv/bin/pytest tests/<dir>/<file>.py -q --collect-only -p no:randomly | grep -E '^[0-9]+(/[0-9]+)? tests collected'

# 7. 3x isolated SEQUENTIAL re-run of the diff reds under adjudication (no -n)...
#    Classification ladder: byte-identical to base => pre-existing; order/load-sensitive =>
#    3x per side; genuinely new => fix-and-rerun until net-new = empty, with a +N/-N fingerprint.

# 8. TEARDOWN (must not leave worktree metadata behind)
git -C /home/nea/ensemble-src worktree remove --force <BASE_WT>
git -C /home/nea/ensemble-src worktree remove --force <HEAD_WT>
git -C /home/nea/ensemble-src worktree prune
git -C /home/nea/ensemble-src worktree list | grep -c <slug>   # expect 0
```
Versioned traps in the skill: v1.0.1 — grep `^(FAILED|ERROR)` (bare `^FAILED ` silently drops
collection/setup ERRORs, so an additive lane can look digest-clean while BASE errored); v1.0.2 —
record digest FORM (`BARE-ID` vs `GREP-FULL`) alongside the sha, never a bare sha; v1.0.3 — strip
the ` - <message>` suffix on BOTH legs (pytest `-rEf` prints `FAILED <node-id> - <message>`).

Supporting canon:
- `.agents/shared/planning/designer-od-lane-fix/gate-surface-scope.md:8`: "run the **identical pytest
  command** on both sides of the diff (base vs HEAD), collect the sorted FAILED test-id lists, and
  diff them with `comm`: `comm -13 base-sorted head-sorted` = **net_new** … `comm -23 base-sorted
  head-sorted` = **fixed** … tests green at the comparison base can never appear in `fixed`".
- POK row `verify-02-snapshot-l3-red-set`: "A gitless base leg INVERTS git-dependent pins: assert-
  non-empty pins go RED at base … assert-EMPTY pins PASS VACUOUSLY and manufacture a NET-NEW
  verdict. Pre-flight: `test -e BASE/.git`."
- Independent expected-count declaration: `.agents/shared/planning/db-pool-idle-txn/expected-collected.md`
  — per-pack collected counts measured at base `19fc11c8b`, cells TBD until measured; the fence
  integer-validates EXPECTED before comparing (anti-tautology rule).
- BASE-still-red discipline (eventloop lane, `phase5-plan.md:43,56`): a harness repair that makes
  BASE green is "REJECTED OUTRIGHT" — the repaired instrument must still go RED at BASE on the same
  assertions.

### B2. PACKS.md — location, row format, registration
- Location: `.agents/tester/PACKS.md` (5,645 lines; newest registrations at the TOP).
- Format: each registration is a dated HTML-comment-delimited block followed by a title line and a
  blockquote, then a fixed 5-column table. Verbatim skeleton (dump-lane block, 2026-11-25):
```
<!-- dump-lane-health-probe-pack-registration-20261125-begin -->
## DUMP-LANE HEALTH PROBE — Operator-Probe Unit Registration (2026-11-25; lane `feature/sonic-dump-health`, base `9f464d726`)

> **What this block registers.** …
> **What is pinned.** …
> **Fence state — the pin is HERMETIC.** …
> **Mutation self-proof — the pins are NON-VACUOUS.** Eight mutations … each turned it RED and each restore was verified BYTE-IDENTICAL (sha256 …)

| Pack / leg | Invocation | Scope | Last run | Status |
|---|---|---|---|---|
| `dump_lane_staleness_probe_unit_test` (NEW 2026-11-25) | `cd $WT && ENSEMBLE_SELF_ENV=dev timeout 300 ./scripts/run_tests_scrubbed.sh tests/unit/test_dump_lane_staleness_probe.py -p no:randomly -q` | `tests/unit/test_dump_lane_staleness_probe.py` (**39 nodes** = 38 + the optional runbook-coverage node) — … **39 nodes** (mechanical `--collect-only` census at this freeze: 39 collected, RC=0, 0 collection errors). HERMETIC — no database connection, no write outside `tmp_path`. | 2026-11-25 @ base `d07cc64b8` (branch `feature/sonic-dhn-probe`) | ⚠️ **PASS 38/39 in-module** — … Mutation-proven: **8/8 mutations RED** …
<!-- dump-lane-health-probe-pack-registration-20261125-end -->
```
- **Dir-glob packs**: `[instrument-limited]` — scoped greps for `glob`, `dir:`, and
  `tests/unit/services/` across PACKS.md returned ZERO hits. The file's convention is EXPLICIT
  per-file scope: every row names exact test files and carries a mechanical `--collect-only` node
  census ("executed == declared"). This matches POK row `sched-02-pin-files-outside-acceptance-pack`
  ("acceptance pack does NOT select the 3 Phase-1 pin files … run them explicitly") and the house
  proof form (`--collect-only` slicing proof, `.agents/shared/planning/plane-pm-issue-tools/phase2-plan.md:285`).
  Runnable pack scripts (explicit pytest targets inside each `.sh`) live under `test/packs/`
  (e.g. `test/packs/child_reports_unit_test.sh`, `test/packs/metrics_read_mutation_matrix_test.py`).
  **Conclusion for the lane**: do NOT rely on directory globbing; a new test file must be
  (a) created under `tests/unit/services/`, (b) registered as ONE dated block + table row in
  PACKS.md with the exact invocation, the `--collect-only` census count, the fence statement
  (hermetic/no-DB) and a mutation self-proof summary, and (c) invoked explicitly by any acceptance
  pack that wants it.

### B3. Real-PG leg
- Harness: `tests/helpers/checkpoint_prune_pg.py` — `real_pg_checkpointer(dbname, dsn, *, pool=False)`
  (:240) and `real_pg_checkpointer_separate_pools` (:321): disposable database + checkpointer stack,
  torn down at context exit. Consumers: `tests/integration/test_message_metadata_*.py`,
  `test_maintenance_checkpoint_cleanup_api.py`, `test_nestops_ops_real_pg.py`,
  `test_nestops_husk_fleet_real_pg.py`, `test_armed_absence_alist.py`, etc.
- Invocation (VERBATIM from `tests/integration/test_nestops_ops_real_pg.py:48-53`):
```sh
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
    -u POSTGRES_HOST -u POSTGRES_PORT \
    .venv/bin/pytest tests/integration/test_nestops_ops_real_pg.py \
    --override-ini="addopts=" -m integration -q
```
  (`PG_TEST_PORT` env selects the test PG port, :161.)
- Safety fences D-13 (VERBATIM, :33-41): "Disposable LOGIN role + database on the LOCAL PostgreSQL,
  DROPPED at teardown; per-variable `POSTGRES_*` pinning (the ambient shell on this host carries
  `POSTGRES_DB=ensemble_prod` — never trusted). … Every table is TRUNCATEd between tests (the
  tests/postgres pattern); no `$HOME`-derived live path is ever resolved; `ensemble_prod` and the
  live daemon on `:9797` are never contacted."
- Unit tests never see ambient PG: `scripts/run_tests_scrubbed.sh` unsets the whole POSTGRES_*/URL
  family one-by-one and echo-verifies zero survivors; its ENSEMBLE_SELF_ENV gate refuses `live`
  with exit 78 (:22-42). (Cross-ref critical note: `env -u POSTGRES_*` globbing is a silent no-op —
  per-variable scrubbing is the rule.)
- **"SQL touched"**: no formal definition of the exact phrase exists in `docs/` or `.agents/tester/`
  [instrument-limited on the tester side]. In-repo anchors: `tests/migration/test_drift_events_index_parity.py:52`
  — "a real-PG run is not required for THIS regression" (index-parity check reproducible without
  PG); the destructive-DDL classifier `grep -liE 'DROP[[:space:]]+TABLE|DROP[[:space:]]+COLUMN'
  daemon/migrations/versions/*.sql` at `stage.sh:384-389`. Working reading: a lane owes a real-PG
  leg when it adds/changes SQL statements, migrations, or PG-dialect code paths (checkpoint
  lifecycle, repository SQL); pure-logic pins stay on SQLite/unit. Synthesis flag: this paragraph is
  reconstructed from anchors, not quoted from a single written rule.

### B4. Unit test invocation + naming
- House-scrubbed form (as registered in PACKS rows): `cd $WT && ENSEMBLE_SELF_ENV=dev timeout 300
  ./scripts/run_tests_scrubbed.sh tests/unit/services/test_<name>.py -p no:randomly -q`
- Raw form (failed-digest-gate step 3): `timeout 300 env -u POSTGRES_USER -u POSTGRES_DB -u
  POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_PASSWORD -u DATABASE_URL .venv/bin/pytest <SUITE> -q
  --tb=short -p no:randomly -n 8`
- venv: `.venv` via `/home/nea/.local/bin/uv sync` (per-tree when doing base-vs-head legs). Dev
  dependency group default-installs with bare sync (upgrade-drills.md §0.3 — pytest-timeout comes
  along). The lane claim KV records "venv: none (lazy venv rule)" for this worktree.
- Naming under `tests/unit/services/` (from directory glob): `test_<service>_<aspect>.py`, with
  `-pins`/`-variant` suffix families, e.g. `test_stale_running_sweep_pins.py`,
  `test_stale_running_sweep_rider_pins.py`, `test_stale_running_sweep_pg_selection_pins.py`,
  `test_shape_b_dedup_latch_pins.py`, `test_revive_non_replay.py`,
  `test_report_obligation_gate_pins.py`, `test_report_integrity_guard.py`,
  `test_w_b_watchdog_liveness_gate.py`, `test_resume_pending_redrive_pins.py`. A new file follows
  `test_<topic>_<qualifier>.py` (qualifier often `pins` when it is a mutation-backed pin pack).

---

## GOAL C — File ownership + release staging

### C1. Unmerged-branch pressure — EXECUTION LIMITATION + exact commands + prior data
**Limitation**: this Explorer instance has NO bash/shell tool — `git branch --no-merged`,
`git diff --stat`, `git log --oneline` could NOT be executed. Reading the main repo's `.git` was
forbidden (`/home/nea/ensemble-src` is off-limits). The collision table below therefore contains
PRIOR MEASUREMENTS + the exact commands for the lane to run; rows marked ⚠ are stale-by-base.

Exact command block (run from the worktree):
```sh
cd /home/nea/ensemble-src-wt-revive-wedge
git branch --no-merged origin/latest          # full list; count with | wc -l
for B in $(git branch --no-merged origin/latest --format='%(refname:short)'); do
  for P in daemon/services/child_reports.py daemon/services/stale_running_sweep.py \
           daemon/services/job_recovery_service.py daemon/services/instance_lifecycle.py \
           daemon/tools/system_log_tools.py daemon/tools/_tool_registry.py \
           tests/unit/services; do
    echo "== $B :: $P"
    git diff --stat origin/latest..."$B" -- "$P"
    git log --oneline origin/latest.."$B" -- "$P"
  done
done
```

Prior measured data (seed the table, then re-verify at base `35ec50e3c`):
| path | branch (tip) | commits | +/- | semantic/trivial | source & staleness |
|---|---|---|---|---|---|
| daemon/services/child_reports.py | fix/empty-job-completed-event @5368a12d4d | n/a ⚠ | n/a ⚠ | SEMANTIC — report-mint/completion-event logic; the revive-wedge root-cause commit 58fc594eb (v0.31.52) widened the report-mint guard in THIS file from anchor-exact to per-child PREFIX | critical note FILE-OWNERSHIP (giter 2026-11-17, base origin/latest **eca98397d**) + history note 2026-11-25; MUST re-measure at 35ec50e3c |
| daemon/services/stale_running_sweep.py | none recorded | 0 | 0 | no contention recorded; supporting pins exist (test_stale_running_sweep_{pins,rider_pins,pg_selection_pins}.py, test_shape_b_dedup_latch_pins.py) | absence-of-evidence, not evidence — confirm with the loop above |
| daemon/services/job_recovery_service.py | none recorded | 0 | 0 | no contention recorded | same caveat |
| daemon/services/instance_lifecycle.py | feature/langgraph-checkpoint-perf @c37c870c56 | n/a ⚠ | n/a ⚠ | semantic (checkpoint perf) | FILE-OWNERSHIP note @ eca98397d — re-measure |
| daemon/tools/system_log_tools.py | none recorded | 0 | 0 | not in any known contention list | same caveat |
| daemon/tools/_tool_registry.py | none named, but HIGH-CHURN by construction | ? | ? | registration prose + 17 privileged-set pin files point here; ANY branch adding a tool/category touches it | structural observation |
| tests/unit/services/ (dir) | unknown | ? | ? | pin-pack dir for this lane's new tests | re-measure with the loop |

Census context: 2026-11-25 ~07:08Z history milestone — READ-ONLY branch census: **1053 refs → 63
unmerged → 53 classified rows (6 landed / 6 superseded / 37 unique / 4 scratch)**; deliverable =
a decision substrate for a LATER deletion commission (find that artifact before re-classifying).
FILE-OWNERSHIP note also records: "8 of 13 wake-path core paths have ZERO unmerged-commit pressure".

### C2. Release staging (quoted)
**Stage usage (stage.sh:86-90, verbatim)**:
```
#   VERSION=v0.10.6 bash scripts/upgrade/stage.sh demo
#   bash scripts/upgrade/stage.sh demo --version v0.10.6
#   bash scripts/upgrade/stage.sh sandbox --version v1 --skip-build ./stub-prod
#   (sandbox also needs INSTALL_DIR=<dir> PORT=<port> [POSTGRES_DB=<db>])
```
**Live stage command shape** (precedent argv, v0.29.15 re-stage 2026-11-10, project metadata):
`ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 VERSION=v0.29.15 bash scripts/upgrade/stage.sh live --skip-build`
— run from the stage worktree; "live" additionally passes `require_live_guard` (stage.sh:190).
**Live promote command shape** (promote.sh:39-45 + v0.29.0 ceremony): `ENSEMBLE_UPGRADE_LIVE=1
VERSION=v0.29.0 bash scripts/upgrade/promote.sh live --f2-verified-closed` — TARGET=live requires
BOTH the env guard AND the explicit `--f2-verified-closed` operator flag (MINOR-4b: a second
factor, not a replacement). The revive-wedge lane is STAGED-ONLY, NO promote.

**rollback_safe derive-path and why it fails on legacy trees** (stage.sh:106-110 + 394-420, verbatim
core):
```
# ROLLBACK SAFETY DERIVATION (D-FA4.5): `rollback_safe` defaults to the release author's call via
# ENSEMBLE_ROLLBACK_SAFE={0,1}; when unset it is DERIVED — false iff the staged migration set
# contains destructive DDL (DROP TABLE / DROP COLUMN → contains_contract_phase=true), else true.
```
The unset-derive grep is `grep -liE 'DROP[[:space:]]+TABLE|DROP[[:space:]]+COLUMN'
"$REPO_ROOT"/daemon/migrations/versions/*.sql` (:384-385) — **full-history, NOT delta-scoped**
(:399-403: "the full-history destructive-DDL grep … is INFORMATIONAL — it is not delta-scoped and
fires on DDL applied before the previous release tag too"). Since the v0.15.3 D-FA4.5 supersession,
a match + unset env REFUSES with exit 78 (the old silent-false path "bit v0.14.2 on 2026-09-25 and
v0.15.1 on 2026-09-26"). Override legitimacy (:405-407): "only set ENSEMBLE_ROLLBACK_SAFE=1 when
the migration delta between the previous release tag and $VERSION is EMPTY (no schema drift
introduced by $VERSION itself)". **That is why legacy trees must pass the env explicitly**: the
derive path cannot distinguish pre-existing legacy destructive DDL from new destructive DDL.

**4-way sha check** (procedure + artifacts): the release binary is verified as FOUR equal sha256
values — (1) `releases/<v>/ensemble-prod` in the install dir, (2) the build output
`dist/ensemble-prod`, (3) `binary_sha256` in `releases/<v>/manifest.json`, (4)
`artifact_sha256` in the build-time sidecar `<artifact>.build-provenance.json`. Precedents:
v0.29.15 "4-way sha 0d542bd1…8451 verified"; v0.29.0 "binary_sha256 4bdb28ce… (4-way: release dir
== dist == manifest == sidecar)". stage.sh RE-HASHES the artifact and never trusts the sidecar's
recorded sha (L2 provenance, stage.sh:287 region; refusal tokens provenance-*/stale-provenance/
dirty-build/non-tip-tree → exit 78). Manifest = flat checksums: six top-level shas
(launcher/binary/config/agents/frontend/plugins) + `pipeline_scripts_floor_commit` + per-file maps
(stage.sh:50-55).

**Release notes**: `CHANGELOG.md` (repo root; Keep-a-Changelog 1.1.0 + SemVer; current entries
shaped `## [v0.31.54] — 2026-11-25` with `### Added/Fixed` subsections). There is NO `docs/releases/`
directory (glob: path does not exist). Consumption rule (deployment blueprint): bump tooling
consumes only the first UNDATED `[Unreleased]` section; post-merge heading-order check mandatory.
**Version-slot derivation**: `VERSION` must satisfy `git describe --tags --exact-match HEAD ==
VERSION` at stage (stage.sh:203-204, else exit 78; "no auto pull, no network fetch ANYWHERE");
tag namespace is `v`-prefixed `vX.Y.Z`; `binary_version` = tag minus leading `v` (stage.sh:430);
next free slot = next unused patch increment of the `v*` tag sequence (bump_version.py bump types
patch/minor/major; precedent metadata field "next_free_version_slot": v0.29.1). Slots are claimed
by cutting the local tag at stage time — never reserved in advance.

**Stage freshness guard** (stage.sh:92-104): refuses (a) non-tip staging tree (HEAD must ==
origin/latest), (b) missing/malformed/stale provenance sidecars. Override is argv-only
`--allow-stale-stage` (journaled `stage_freshness_override`, operator_accepted=true). Separate
lever: promote rides a PINNED scripts checkout (`PIPELINE_SCRIPTS_FLOOR_COMMIT`; escape
`ENSEMBLE_ALLOW_STALE_SCRIPTS=1`) — do not confuse the two.

---

## Gaps / honesty ledger
1. GOAL C1 git commands: NOT run (no shell). Exact commands provided; prior data carries base-staleness flags.
2. Test-method names below Lane 3 of tests/test_system_log_tools.py and the full privileged-category
   test method list: not enumerated (grep instrument-limited; files verified present with sizes).
3. "SQL touched" formal definition: not found; reconstruction flagged in B3.
4. PACKS.md dir-glob absence: instrument-limited negative (anomaly 7db97170).
