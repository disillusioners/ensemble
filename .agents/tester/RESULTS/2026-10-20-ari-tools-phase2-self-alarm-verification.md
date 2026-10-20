# ari-tools Phase 2 — `self_alarm` wrapper — INDEPENDENT VERIFICATION

Date: 2026-10-20
Tester lane: independent verification of the Phase-2 capability (not a re-run of the developer's suite)
Worktree: `/home/nea/ensemble-src-wt-ari-tools` (branch `feature/ari-tools`)
Developer's range: `99a28e4f3..bb2d109a7` (**6 commits** — the commission brief said "8"; git says 6)
Verification-wave commits on top: `bfff96933` (coverage-gap pins), `928dba2ae` (comment fix) ⇒ 8 commits total in `99a28e4f3..928dba2ae`
Binding spec: `.agents/shared/planning/ari-tools/phase2-plan.md` (+ `decisions.md` §C9 / §C9.4)

> **VERDICT: 🟢 PASS — 10 lanes + a final gate, ZERO PRODUCT DEFECTS.**
> The original capability was verified end-to-end against the **real** scheduling engine on **real
> PostgreSQL**: create→armed, the ≥30-minute floor at its knife-edge, dedupe/reuse with the original row
> provably unmutated, the ari-exclusive dual gate with zero side effects, the one-shot lifecycle with
> the engine-native `stopped` read, the fresh-instance contract, the `lateness_cap_seconds` echo in both
> response shapes, the marker-correct pin evidence, and a frozen Phase-1 interface.
>
> **Three coverage gaps were found by this verification and closed by it** (two unpinned spec
> properties + the 29m59s boundary). **One pin written by this verification over-claimed** — three
> assertions that could not fail — and was caught by mutation and repaired in place. **No production
> code was changed by any lane.**

---

## 1. Scope decision

The request was to verify "the ORIGINAL capability, end-to-end" across nine named focus areas. The
change set is 15 files: a NEW service (`daemon/services/self_alarm_service.py`, 722 lines), a NEW tool
module (`daemon/tools/self_alarm.py`, 1122 lines), registry + factory wiring
(`daemon/tools/_tool_registry.py`, `daemon/tools/instance.py`), `agents/ari/meta.json`, and 7 test
files.

**A full-suite run was NOT warranted and was not performed.** Rationale: the change set is additive,
touches NO migration, NO boot path, NO deadlock-sensitive code, and NO daemon core. The broad-regression
proxy used instead was the `tests/unit/tools/` census delta against the Phase-1 recorded baseline
(§3, N8) — which is where the change set's blast radius actually lands — plus the registered
`concurrency_atomic_unit_test` pack and the ensure.md Core statics (N7).

**ensure.md Release Gate NOT run** — it requires a booted daemon + real LLM calls + E2E flows, and the
change set touches none of those paths. Blast radius does not determine this change as big/critical.

---

## 2. Method

Ten lanes, one pack each, run in parallel on the dev fence. Each lane was handed exactly one skill
(`test-pack-execution`, `integration-test`, `quick-fix`, `ensure-validation`) and a self-contained brief.

**Fences (held by every lane, verified in each lane's own audit):**
- DEV fence only: PostgreSQL `ensemble_dev` @ 127.0.0.1:5432, daemon port 8079.
- Live install `:9797` / `ensemble_prod` — never contacted, signalled, restarted or killed (live pid
  `2296421` observed unchanged across lanes). Port `8088` (ensemble self-system) never touched.
- The ambient shell carries `POSTGRES_DB=ensemble_prod` @ 10.44.0.2 — overridden on every DB-touching
  command; the `concurrency_atomic_unit_test` pack additionally needs `env -u POSTGRES_DB`.
- No HOME-derived / live-install paths in any fixture (`tmp_path` / `mktemp -d` only) — the 2026-10-12
  live-install-deletion convention.
- No `git checkout` / `stash` / `reset` / `clean` in the shared worktree; targeted `git add` only.
- `.agents/shared/planning/designer-agent/install-audit.jsonl` (another lane's file) left exactly as found.
- Real-PG-first per the project mock rule; a counting spy is used only for call counts, never to
  substitute for the real service.

---

## 3. Per-lane results

### N1 — developer corpus baseline + F1–F9 coverage map — ✅ PASS
Pack: the 7 changed test files. Run **twice** — repo default `addopts` and `--override-ini="addopts="`.

| mode | collected | passed | failed | skipped | **deselected** | exit |
|---|---|---|---|---|---|---|
| default addopts | 230 | **230** | 0 | 0 | **0** | 0 |
| `addopts=""` | 230 | **230** | 0 | 0 | **0** | 0 |

Node-id **set** diff (not just counts): ∅ both directions ⇒ no silent deselection in these files.

**Coverage map (the primary deliverable):**

| focus | verdict |
|---|---|
| F1 create→armed | COVERED |
| F2 ≥30-min floor | COVERED (29m59s **not** a dedicated node — probe-confirmed correct) |
| F3 dedupe/reuse + mismatch | COVERED |
| F4 dual gate | **PARTIAL → closed by `bfff96933`** (D10 layer-3 literal allow was unpinned) |
| F5 one-shot lifecycle | COVERED |
| F6 fresh-instance contract | COVERED |
| F7 `lateness_cap_seconds` echo | **PARTIAL → closed by `bfff96933`** (reuse path was unpinned) |
| F8 pin evidence | PARTIAL (pin #5 is **deselected by default addopts** — see N5) |
| F9 no Phase-1 regression | COVERED |

**Mock-fidelity verdict: CLEAN.** `FakeRepo.update_source_config` returns a *fresh snapshot* and
documents why (the real repository returns a freshly loaded object from its own session, so a later
`update_source_status` write in another session does not alias it). No in-place-mutating double found.

### N2 — real-engine end-to-end (F1/2/3/6/7) — ✅ PASS — **the load-bearing lane**
Real `InstanceManager` built **in-process** over **real PostgreSQL `ensemble_dev`** (no `dev.sh` boot —
the exit-trap hazard was avoided entirely; `manager.initialize()` deliberately not called so no workers
or job-processors start). Real `SQLModelSourceRepository` / instance / project repos; real
`SourceRegistry` → the engine built + started a **real `SchedulerAdapter`**; the real
`daemon/services/scheduling_service.py` module (the manager's lazy property, `manager.py:13555`); the
real seam `instance.py:2340 create_self_alarm_tools_if_available(manager, instance_id, agent_id,
agent_tag)` → exactly **1** tool, invoked as `await tool.coroutine(**kwargs)`.

**Every row assertion was read back with raw SQL** (`SELECT … FROM source_configs`) — never on the
tool's echo. One-command reproduction: `/tmp/rt-p2-verify/run-all.sh` → **PACK RESULT: PASS, 55 s**.

**P1 — create → armed, real engine.** Persisted row: `name = self-alarm:tester-p2-p1-6653ec2e`,
`agent = ari`, `instance_mode = new_instance`, `recurrence = once`, `status = running` (armed),
`enabled = True`, `source_type = scheduler`; response `source_id` == row `source_id`; **stored armed
instant == requested instant, delta 0.000000 s**; `create_schedule` called **exactly once**,
`update_schedule` **zero** times. Stored message (verbatim from the DB):

```
[self-alarm] self-alarm:tester-p2-p1-6653ec2e
Scheduled fire: 2026-10-20T18:55:29.702158+07:00  (2026-10-20T11:55:29.702158+00:00)
Armed at: 2026-10-20T10:25:29.761097+00:00   Schedule: once | tz Asia/Ho_Chi_Minh
You are a fresh instance with no memory of this request; everything you need is in this message.
Action: Re-check the deploy status and post the result on the board.
```

**P2 — the floor.** `MIN_HORIZON_SECONDS = 1800` (`self_alarm_service.py:77`, enforced at
`self_alarm.py:745-749`).

| case | measured | outcome |
|---|---|---|
| `in 29m59s` (= 1799 s) | 1799 s | **REJECTED**, typed error, bash-sleep hint present, **0** create, **0** update, **no row**, label **not burnt** (still creatable; exactly 1 row after) |
| `in 30m` | **1800.000000 s** | **ACCEPTED** |
| `in 30m1s` | **1801.000000 s** | **ACCEPTED** |
| absolute now+1799 s | 1799 s − probe→tool skew (≤ 13.0 ms) | **REJECTED**, 0 calls, no row |
| below-floor **reuse** | — | **REJECTED**, **0** update, **0** create, stored row **byte-identical** (field-level diff: NO DIFFERENCES) |

*Method note:* the **relative** grammar is the only way to measure the boundary exactly — the plan and
the floor compare against the *same* `now`, so `first_fire_at_utc − armed_at_utc` **is** the compared
delta; an absolute instant is always offset by probe→tool skew.

**P3 — dedupe / reuse / mismatch / foreign-owned.** All proven by DB read-back, with engine-call counts:

| case | create | update | verdict |
|---|---|---|---|
| same label twice | 1 then **0** | 0 then **1** | **REUSED the SAME row** — same `source_id`, **exactly ONE** row in the DB; reuse payload carried `timezone='Asia/Ho_Chi_Minh'` + `paused=False` |
| family mismatch (once→daily) | 1 | **0** | `fallback_reason=recurrence_family_mismatch`, fresh label with the `-2` suffix, **ORIGINAL row byte-identical** |
| weekly-DOW mismatch | 1 | **0** | `fallback_reason=weekly_dow_mismatch`, original byte-identical, stored cron `30 6 * * 3` unchanged |
| cron-expression mismatch (bonus) | 1 | **0** | `fallback_reason=cron_expression_mismatch`, original byte-identical |
| foreign-owned label (a real `leader` row) | 1 | **0** | typed token **`label_owned_by_other_agent`** + fresh label; **leader row diff: NO DIFFERENCES** |

*Nuance vs. the brief's wording (spec-compliant, flagged for the leader):* the foreign-owned case
surfaces the token as **`fallback_reason`**, not as an `"ERROR: …"` envelope — the call *succeeds* with
a fresh `-2` label. That is exactly what the binding spec mandates (`phase2-plan.md:272-289` step-2
table + `decisions.md` P2-DEV-12). Intent satisfied: typed token + fresh label + zero updates + the
foreign row untouched.

**P4 — fresh-instance contract.** With `link_board_item="wi-1a2b3c4d"` the stored message carries the
board id **and** the exact instruction (*"call board_get with that id to load its title, body, status
and notes before acting"*), plus the fresh-instance note; without it the message is **still
self-contained** (no `None`, no empty `Board item:` line). The reuse-path message additionally carries
`| id <source_id>` as a correlation handle. **Measured** wrapper/engine payload split: the wrapper
supplies `cron_expression`, `label`, `weekday`, `when` + the composed message + `agent='ari'` +
`instance_mode='new_instance'`; the engine derives `local_time` / `run_at` and owns the row identity,
the `status` lifecycle, the daily/weekly cron construction, the adapter build/start and the
`next_run_at_*` echo.

**P5 — `lateness_cap_seconds`.** Source: `daemon/config.py:2617`
`SchedulingConfig.one_shot_max_lateness_seconds` (env prefix `ENSEMBLE_SCHEDULING_` at
`daemon/config.py:2609` ⇒ env seam `ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS`), read by the
wrapper at `self_alarm.py:798`. Unset ⇒ **CREATE `None` / REUSE `None`**, field **present in both**
shapes; env `=900` ⇒ **CREATE `900` (int) / REUSE `900` (int)** — both typed `int`, not `str`. No
production code edited; the config was set through the documented env seam.

**Mock discipline — the leader's exact concern, proven on the REAL engine.** The only double is a
**delegating** call-count spy installed **on the module** (`scheduling_service.create_schedule =
spy._create`), so the tool closure's `service.create_schedule` resolves to it and the counter is
*in the call path* — proven by the control arms (every accepted call shows the expected non-zero
count; every rejected call shows 0). It returns the engine's own object **by identity** and never
re-implements engine behaviour. **Fresh-object contract proven on the real reuse path**: the real
`repository.py:98 update_source_config` loaded the row in a fresh session and returned a **new**
`SourceConfig` (`returned_is_input: False`); the engine's downstream consumer
(`registry._create_adapter_from_config`) received `['SourceConfig', 'SourceConfig']` — the fresh
object.

**Cleanup proven:** 31 rows created → 31 deleted (prefix-scoped only, never a broad pattern, never
TRUNCATE) → `source_configs` total back to **0**; `schedule_executions` 0; `instance_mappings` 0;
`instances` unchanged at 34 (an existing instance row was reused read-only).

**Defects: none in product code.** This lane produced **zero repo changes**.

**Three advisories for the ledger (non-blocking, all real traps):**
1. **`starting` → `running` is asynchronous and adapter-owned.** Any before/after row diff on a
   scheduler row must settle **asynchronously** — a blocking `time.sleep` starves the supervisor task
   on the same loop. The lane's first run produced a **false-positive** diff on `status` only; after an
   async settle the diff is empty. (Filed as a `skill_fix`.)
2. **The reuse response `status` is the PRE-update status** (the plan's D4 / I3-BLOCK1a ruling,
   `phase2-plan.md:120-126`) — the reuse echo carried `status: "starting"` while the row was already
   re-armed. Anyone reading the reuse echo as "the current row status" will mis-read it.
3. **`label_owned_by_other_agent` is a `fallback_reason`, not an error envelope** — worth restating in
   the tool doc so an agent-facing reader does not hunt for an error string.

### N3 — dual gate, ari-EXCLUSIVE (F4) — ✅ PASS
All six probes against real PG `ensemble_dev` + the real `create_instance_tools` path.

- **P1 privileged category #10** — AST-parsed, source order: `PRIVILEGED_TOOL_CATEGORIES` DEF at
  `_tool_registry.py:259`, `self-alarm` at **:402 = entry #10 of 10**. `DYNAMIC_TOOL_NAMES` :90,
  `KNOWN_TOOL_NAMES` :1021, `CATEGORY_MODULES["self-alarm"] = "daemon.tools.self_alarm"` :789 (a
  `daemon.tools.*` path — the D2 registry contract). All five exact-set pins PASS.
- **P2 the literal-tool-name bypass arm** — a non-ari agent whose `tools.allow` carries the literal
  tool name `self_alarm` **does resolve the tool** (the bypass is real — `instance.py:478`
  `allowed_tools.add(item)`), and the **runtime gate refuses it**:
  `ERROR: self_alarm is available to the ari agent only (caller: syn-literal-allow).`
  ⇒ **0 engine calls** (counter `{}`), **0 rows** (0 → 0). Same for `agent_id="leader"`.
  An `allow:["read_file"]` agent and the REAL `worker` (193 tools) never resolve `self_alarm`.
- **P3 `agent_id=""` and `None`** ⇒ `ERROR: … (caller: unknown).`, **0 engine calls**, **0 rows**.
- **P4 allowed set is exactly `{ari}`** — `SELF_ALARM_AUTHORIZED_AGENT = 'ari'`
  (`self_alarm.py:96`), compared at `:647`, and the gate is the **first executable statement** of
  `_arm_self_alarm` — before project resolve, before label lookup, before any engine call.
  **Non-vacuity control:** `agent_id="ari"` ⇒ `ok:True`, `{'create_schedule': 1}`, and a **real row**
  in `ensemble_dev` (deleted after).
- **P5 the empty-allow universe never gets the tool** — REAL `agents/watcher/meta.json` (`allow: []`)
  ⇒ 193 tools, no `self_alarm`; a tools-less agent (the `agent_create` shape) ⇒ 193 tools, no
  `self_alarm`; **positive control** REAL `ari` ⇒ `self_alarm` resolves. Innate-skill expansion is
  fail-closed for privileged categories (`instance.py:205-206`).
- **P6 meta.json uniqueness** — `grep -rl "self-alarm" agents/*/meta.json` ⇒ **exactly 1**
  (`agents/ari/meta.json:38`). `grep -rn "self_alarm" agents/*/meta.json` ⇒ **zero** hits in allow
  **and** deny — no agent anywhere holds the literal tool name.

**Self-caught harness flaw (important, and worth recording):** the first harness wired the counting
spy to `manager.scheduling_service` while the tool body held the raw module — the counter read **0
calls while the engine actually wrote a real row**. A "zero side effects" proof built on that counter
would have been **false**. Fixed, and the lane added a permanent **spy-in-path self-check**
(`tool.coroutine.__closure__` contains the spy exactly once) plus the `ari` positive control so a
vacuous counter can never pass again.

**Real-vs-mock:** real PG, real `SQLModelSourceRepository` (no schema mutation), real
`scheduling_service.configure()` with the real repo + real `SourceRegistry`, real `AgentRegistry`
(real `watcher`/`ari` meta.json verbatim + synthetic probes in tmp), real
`create_instance_tools`/`_apply_tool_filter`. Only the InstanceManager shell is a `MagicMock` carrying
the real service/repos; the counter counts-and-delegates and returns the real return value verbatim.

Cleanup proven: prefix rows before 1 → after **0**; total `source_configs` back to **0**.

### N4 — one-shot lifecycle (F5) — ✅ PASS
Real PG `ensemble_dev` + the real `daemon.services.scheduling_service` + the real `SourceRegistry` +
a real `SchedulerAdapter` built by the real `registry._create_adapter_from_config(row)`.

- **P1 the wrapper NEVER cancels.** Static: the wrapper's entire engine-call surface is **3 lines**
  (`create_schedule` :906, `update_schedule` :1012, `update_schedule({paused: True})` :1096). All 9
  `cancel|delete|remove|drop|terminate` grep hits are comments, string literals, or the read-only
  comparison `stored.status == "cancelled"` (:231 — the burnt-label detector). Dynamic: **two
  independent counters** (engine-level `cancel_schedule` AND repo-level `cancel_source_config`, so any
  path to the forbidden write is caught) ⇒ **0 and 0 across all 7 reachable paths** (create, reuse,
  family-mismatch fallback, weekly-DOW fallback, assertion-failure fallback, below-floor, foreign-owned).
- **Path-5 critical invariant HOLDS** — `ARMED_ROWS_COUNT = 1` (only the fresh label); the mutated
  original ⇒ `status='stopped'`, `enabled=False`, `run_at` preserved; pause payload exactly
  `{'paused': True}`; **both** proof forms the plan requires (response `paused is True` **and** the
  repo-side row re-read — the pause response's own `status` was the stale pre-pause value, exactly as
  the plan predicts).
- **P2 engine-native `stopped`** — the engine's own fired transition was driven
  (`adapters/scheduler.py:599-609` → the registry-installed closure at `registry.py:514-526` ⇒
  `update_source_status(source_id, "stopped")` + `update_source_config(source_id, enabled=False)`):
  row `starting` → `stopped`, `enabled` `True` → `False`, `run_at` unchanged. The wrapper reads the
  **repo-side** state (`self_alarm.py:687-688` → `_read_stored_row` :151/:152 → fired check :732).
  **Decisive negative control:** re-requesting the stored `run_at` verbatim returns *"already fired
  (or was paused) … must be re-armed with a new, LATER time"* — reachable **only** through that
  repo-side read, with **zero** engine calls.
- **P3 label re-use** — a fired/`stopped` row **re-arms the same row** (same `source_id`, no `-2`,
  row count 1, `paused:False`, all three wrapper assertions satisfied); a **cancelled** row is burnt ⇒
  fresh `-2` with `fallback_reason=label_burned_cancelled` + an `untouched_existing` disclosure, the
  original left cancelled. Wrapper-attributable cancel calls: **0**.
- **Proof split, stated honestly:** the fired-transition end state and the wrapper's read/mapping are
  **executed**; the upstream half (`adapter → _on_complete_callback`) is **code-verified by citation,
  not executed** — no live 30-minute fire, per instruction.

Cleanup proven: 14 rows → **0**; `schedule_executions` for those source_ids = 0.

### N5 — pin evidence, marker-correct (F8) — ✅ PASS
| file | marker regime (measured) | true invocation | count |
|---|---|---|---|
| `test_service_tool_flag_off_byte_identical.py` | **9/9** functions `@pytest.mark.integration`, 0 module-level `pytestmark` | `-m integration` (REQUIRED vs the repo's default `addopts`) | **9 passed** |
| `test_maintenancer_spawn_resolves_tools.py` | **0** selection markers (only `@pytest.mark.parametrize`) | default selection | **13 passed** |

Controls: default addopts, no `-m` ⇒ `no tests collected (9 deselected)`, exit 5; forcing
`-m integration` on the maintenancer file ⇒ `13 deselected`, exit 5. Counts are DB-independent
(credential-stripped runs give identical counts).

**`decisions.md` §C9.4 (P2-PIN-1) evidence row: TRUE.** 16 of 18 elements VERIFIED; the 2
NOT-VERIFIABLE elements are the wall-clock timings and the provenance of the execution — neither is a
correctness claim. No wrong count, no wrong marker claim, no invocation that cannot produce the
claimed result.

**All five exact-set pins assert `== frozenset({10})`** — genuine exact equality, `self-alarm` genuinely
required, **no `len()` assertion in any of the five** (matching `phase2-plan.md:485`). The sixth,
off-plan pin `test_board_registration.py:178` *does* assert `len(...) == 10` — already disclosed in
`decisions.md` P2-DEV-8, so no live contradiction.

### N6 — frozen interface + seal tripwire (F9) — ✅ PASS
- **FROZEN-INTERFACE (binding)** — `git diff -G 'def list_items|def get_item' 99a28e4f3..HEAD` ⇒
  **empty (0 bytes, 0 lines, exit 0)**. The board module public surface is **IDENTICAL** base↔HEAD
  (board_service 1 signature line, board_tools 5, board_model 8).
- **SEAL TRIPWIRE** — stale-pin count **before 7 == after 7**, `NEW = []`, `RESOLVED = []`, 28 green /
  0 missing, content anchors **46/46**. Strongest form of proof: `git diff --name-only 99a28e4f3..HEAD --
  <35 sealed paths>` ⇒ **empty** ⇒ phase 2 touched **zero** sealed artifacts. All 7 stale pins are
  byte-identical to the range base. Bonus: the prompt-artifact manifest `sha256sum -c` ⇒ **161/161 OK**.
- **PROMPT SURFACE** — `git diff --stat 99a28e4f3..HEAD -- agents/ari/rule.md agents/ari/workflow.md` ⇒
  **empty**; `agents/ari/meta.json` is the only `agents/` file touched. `tools_note.md` is deliberately
  deferred to the conventions step per `phase2-plan.md` task 7 — and it **is** manifest-covered, so no
  re-stamp pass is owed.
- **BOARD CORPUS** — **110 passed** (56 / 34 / 20) — baseline-exact; node counts unchanged.

### N8 — `tests/unit/tools/` census delta — ✅ PASS (zero delta)
Primary: `13 failed, 3841 passed, 7 skipped, 5502 warnings in 87.46s` (exit 1 is the *pre-existing*
failure set, as expected). Reproducibility leg identical. Extended leg (after the tip commit) also
identical.

| metric | Phase-1 baseline | Phase-2 | Δ |
|---|---|---|---|
| collected | 3810 | **3861** | **+51** (all in `test_self_alarm_tools.py`, 0 → 51, all green) |
| passed | 3790 | **3841** | +51 |
| failed | 13 | **13** | **0** |
| skipped | 7 | **7** | 0 |
| deselected | 0 | **0** | 0 |

**NEW failures = ∅ · RESOLVED failures = ∅ · STABLE = 13** (byte-identical node ids to the baseline).
Per-file stability: `test_prompt_section_reference_integrity.py` 5 · `test_inner_soul_compaction.py` 3 ·
`test_instance_tools.py` 2 · `test_knowledge_tools.py` 2 · `test_snapshot_tools.py` 1. The documented
`--dist load` artifact did **not** appear under `--dist loadfile`. No silent collection loss.

The three phase-2-modified unit pin files are individually green (24 + 26 + 21 = 71 nodes) and the new
`test_self_alarm_tools.py` is green (51 nodes).

### N9 — quick fix: the stale privileged-category trajectory comment — ✅ PASS
Commit **`928dba2ae3c14d354c421d8d56d9d1bb2f4662e2`** — *"test(self-alarm): correct the stale
privileged-category trajectory comment (8 -> 10)"*; 1 file, +10/−2, **comment-only proven** (every
changed line is a comment; the `assert PRIVILEGED_TOOL_CATEGORIES == frozenset({...})` block is
byte-identical; the member set re-extracted = 10 members, unchanged).

The lane did **not** transcribe the reported value — it derived the truth from git
(`git log --reverse -L` on `PRIVILEGED_TOOL_CATEGORIES`) and found the sentence carries **two** claims:
the count *and* the provenance qualifier *"with the same-PR pin updates (D18/A14)"*. Appending
`→ 9 → 10` would have been numerically right but would have endorsed a **false provenance claim**: at
`152a9c93b` (7 = `secrets`, 2026-10-17) the set had 7 entries while the upgrade pin still asserted 6 —
**the pins were red at that commit** and closed 15 s later in `72fbd6126`. The new comment records that
exception explicitly.

Re-runs: edited file **13 passed**; combined `-m integration` **9 passed / 71 deselected** (the
deselection was anticipated and handled, not hidden); the three unit pins without `-m` **71 passed**;
the 4th pin alone **9 passed**; **all five pins together, zero deselected ⇒ 93 passed**; the off-plan
6th pin **20 passed**; post-commit re-run **13 passed**.

### N10 — adjudicate the tip commit's gap-pins — 🟡 QUALITY-GATE FAIL on the pin → ✅ REPAIRED IN PLACE (GAP-1 now CLOSED)
`tests/unit/tools/test_self_alarm_privilege_pins.py` **passes** (`3 passed, 33 warnings in 2.65s`, exit
0, 0 deselected) — but it does not prove what it claims on GAP-1.

**Per-node adjudication (the primary deliverable):**

| node | asserts | can it fail? | real vs mock | verdict |
|---|---|---|---|---|
| 1 `…_RESOLVES_the_tool_for_a_non_ari_agent` (L91-116) | a literal tool-name allow entry resolves the tool for a non-ari agent | **YES — mutation-proven** (M3 → RED at L113) | REAL `AgentRegistry` over a `tmp_path` agent cloned from `agents/watcher/meta.json`, REAL `create_instance_tools`, REAL allow-expansion | **sound** |
| 2 `…_REFUSES_a_literal_allow_non_ari_caller` (L119-169) | the gate refuses, with zero side effects | refusal **YES**; **the three side-effect assertions CANNOT FAIL** | REAL registry + REAL factory; the tool IS called | **the zero-side-effect half is UNPROVEN** |
| 3 `…_REUSE_path_echoes_lateness_cap_seconds` (L213-246) | the reuse branch echoes `lateness_cap_seconds` for both cap states | **YES — mutation-proven** (M2 → RED at L233) | REAL scheduling service (the spy IS the tool's service here), in-memory fake repo | **sound — GAP-2 CLOSED** |

**Root cause of the vacuous assertions (three independent wirings, all necessary):**
1. the `SpyService` was **constructed but never injected** — the tool binds `manager.scheduling_service`
   (the *real module*), proven by closure introspection (`is the SPY? False`, `is the REAL mod? True`);
2. `manager._instance_repository = None` ⇒ the tool refuses on *"cannot resolve project_id"* **before any
   engine call**;
3. `manager._source_repository` was never set ⇒ the MagicMock manager auto-creates it ⇒ the label probe
   sees a truthy mock ⇒ **every candidate label looks "taken"** ⇒ the flow can never reach a row write.

⇒ the flow was structurally incapable of reaching the engine, so `create_calls == []` / `rows == []`
were true by construction.

**Mutation evidence (3 mutations, each byte-exactly restored — sha256 before == after, `git diff --stat` empty):**

| # | mutation | result |
|---|---|---|
| M1 | `self_alarm.py:647` → `if False and agent_id != SELF_ALARM_AUTHORIZED_AGENT:` | node 2 RED **only at L165** (`"ari" in result` — an incidental substring); **L167/168/169 stayed GREEN** ⇒ the side-effect invariant was NOT detected |
| M2 | drop `"lateness_cap_seconds"` from the **reuse** dict only (`self_alarm.py:1055`) | node 3 RED at L233 with the intended message ⇒ GAP-2 is load-bearing |
| M3 | `instance.py:480` — a privileged-category member can no longer be named literally | nodes 1+2 RED at L113 with the intended message |

**Control (informative):** the *pre-existing* corpus node
`test_a_non_ari_caller_is_refused_with_no_engine_call` goes RED under M1 **with a success dict** (the
engine was reached) ⇒ that test's spy/repo wiring IS load-bearing; the new file's was not.

**Verdict:** GAP-1 **PARTIALLY CLOSED** — the *bypass premise* is now genuinely pinned (M3), but the
*zero-side-effect invariant* is not, on the literal-allow path, with a working probe. GAP-2 **CLOSED**.

**Fix authored and committed** — `63329e059b089f83703a1286d5ae81fea74ef37d` —
`tests/unit/tools/test_self_alarm_literal_allow_side_effect_pins.py` (234 lines, test-only): pins the
zero-side-effect invariant with **engine-reachable** wiring plus a **negative control** that neutralises
the gate in memory and asserts the engine **is** reached (1 create call, 1 written row) — so the pin
cannot silently degrade into a tautology again. Mutation-verified: under M1 it goes RED with a real
schedule created (`ok=True, source_id=src-created-1`).

**Follow-up (COMPLETE) — commit `8cb64ef631805f9ee94af9982bf175550443f1d5`**
*"test(self-alarm): make the literal-allow zero-side-effect assertions load-bearing"* —
`tests/unit/tools/test_self_alarm_privilege_pins.py` (+158/−35, test-only).

The repair is **additive** — the sibling `_manager()` helper is untouched (node 1 still uses it); a new
`_engine_reachable_manager(repo, service)` sets `_source_repository = repo`, gives `_instance_repository`
a mock that resolves a real `project_id`, and installs the **spy** as `manager.scheduling_service`
**before** the tools are built; a new `_stage_ari_agent()` stages the real `agents/ari/meta.json` so the
engine's target-agent validation passes; a new `literal_allow_env` fixture builds the `SpyService`
**first** and then the tools.

Node 2 was rewritten so the **side-effect assertions come FIRST** (a regression must report the engine
call / the written row, not a message difference), and the refusal assertion is now **gate-specific**:
`"only" in result`, the caller id in result, and explicitly **`"cannot resolve project_id" not in
result`** so the project-resolve refusal cannot masquerade as the gate.

A **negative-control node** was added —
`test_the_wiring_reaches_the_engine_so_the_pin_above_can_go_red` — which neutralises the gate in
memory (`monkeypatch.setattr(sa, "SELF_ALARM_AUTHORIZED_AGENT", …)`) and asserts the engine **is**
reached (**1 create call, 1 written row**), so the pin cannot silently degrade into a tautology again.

**M1 re-mutation now fires on the SIDE-EFFECT assertion**, not on the message substring:
```
>   assert service.create_calls == [], f"the engine create was called: {service.create_calls}"
E   AssertionError: the engine create was called: [({'label': 'self-alarm:probe', 'agent': 'ari',
E     'message': '[self-alarm] self-alarm:probe\nScheduled fire: …', 'when': '…',
E     'timezone': 'Asia/Ho_Chi_Minh', 'recurrence': 'once', 'project_id': 'proj-1',
E     'instance_mode': 'new_instance'}, …)]
tests/unit/tools/test_self_alarm_privilege_pins.py:244: AssertionError
```
Byte-exact restoration proven: `SHA_BEFORE == SHA_AFTER` (`8ec244d7…`), `git diff --stat -- daemon/tools/self_alarm.py` **empty**, `git status --porcelain -- daemon/tools/self_alarm.py` **empty**, and
`git diff HEAD --stat -- daemon/` **empty** post-commit.

Green re-runs: (a) the fixed file → **4 passed**; (b) `test_self_alarm_literal_allow_side_effect_pins.py` → **2 passed**; (c) the 5-file self_alarm corpus → **145 passed** (= 142 + the new file's 2 + the new negative-control 1); (d) the five exact-set pins, `addopts` overridden, **no marker filter** → **93 passed, no deselection**.

⇒ **GAP-1 is now CLOSED**: the bypass premise is pinned (M3), the gate's refusal is pinned and
gate-specific, and the **zero-side-effect invariant is pinned by assertions that are provably
red-capable** (M1) on an engine-reachable path.

**D2 (LOW, informational):** neither `test_self_alarm_tools.py` nor the new pin files are registered in
`.agents/tester/PACKS.md` — a pre-existing registration gap (not introduced by `bfff96933`). **Fixed by
the tester lane** (§7).

**Fences:** hermetic doubles — no DB contacted, no daemon booted; live `:9797` untouched. Note: the live
daemon's pid had moved (2296421, vs 969286 in the project context) — an **external** restart by another
actor during the session, not by any lane here.

### N7 — ensure.md Core validation — ✅ PASS

Release Gate **not run** (concurs with §1: additive change set, no migration / boot path /
deadlock-sensitive code; the gate needs a booted daemon + real LLM calls + E2E and would produce
signal unrelated to this diff while contending with the parallel lanes).

| # | Requirement | Sev | Status | Evidence |
|---|---|---|---|---|
| CRITICAL-0 | no regressions in changed packs | crit | **PASS** (resolved by the other lanes: every pack PASS) | — |
| CRITICAL-1 | deadlock / concurrency integrity | crit | **PASS** | `99P / 0F / 74S`, baseline-exact, **×2 independent runs** (74.16 s / 75.46 s), exit 0; node reconciliation 10+5+24+37+23 = **99** ✓ |
| CRITICAL-2 | no sync DB calls on the asyncio event loop | crit | **PASS** | 10 thread-identity + `asyncio.to_thread`-spy nodes + 5 gate-serialization nodes — all PASSED |
| CRITICAL-3 | `dev.sh` includes `--timeout-graceful-shutdown 10` | crit | **PASS** | `dev.sh:155` (flag), `:152` (comment) |
| IMPORTANT-1 | async callers await | imp | **PASS** | **10/10** production call sites awaited; **0** non-awaited (`_get_system_prompt_tokens` ×3, `_compute_context_usage` ×1, `get_queue_stats` ×6) |
| IMPORTANT-2 | parent→child→complete does not block | imp | **PASS** (caveat below) | 2 primary + 2 finalize + 2 child-completion nodes PASSED |
| NICE-TO-HAVE-1 | no dead code | nice | **PASS** | AST census: **0** unreferenced module-level functions in either new module; `ParsedWhen`/`FirePlan` are the public return contract (exported in `__all__`), not dead code; every registry entry resolves **at construction**; the no-service path returns `[]` |

Registered invocation, from `PACKS.md`:
`env -u POSTGRES_DB -u DATABASE_URL_POSTGRES timeout 300 bash test/packs/concurrency_atomic_unit_test.sh`
→ `99 passed, 74 skipped, 58 warnings in 74.16s` / `RESULT: PASS` / exit 0 (run 1) and
`75.46s` / exit 0 (run 2 — determinism re-run).

**⚠️ Caveat disclosed by the lane, not glossed:** the *cascade-completion* nodes that would cover the
full parent→child→complete race —
`tests/test_cascade_unified.py::test_root_after_all_children_resolved_is_complete`,
`tests/test_cascade_concurrency.py::test_three_concurrent_resolves_same_parent` and
`::test_five_concurrent_resolves_same_parent` — are **module-level skipped** by the same pre-existing
structural skip (`pytestmark = pytest.mark.skip(reason="Phase 5: CorrelationManager removed")`).
Those files are byte-untouched by the range and the skip count is baseline-exact ⇒ a **pack coverage
gap, not a defect of this change set** — but "IMPORTANT-2 PASS" must not be read as full race coverage.

**No ensure.md contradictions were encountered ⇒ no Improvement Notices for this commission.**

---

## 4. Findings for the leader (none is a product defect)

**ZERO product defects were found in the phase-2 change set.** Every focus area F1–F9 verified PASS
against the real engine on real PostgreSQL. What the verification DID find is listed below.

1. **Manifest correction.** The commission brief described `99a28e4f3..bb2d109a7` as "8 commits"; git
   measures **6** (`25b38387e`, `fc89ddcde`, `2265b0909`, `5782434f5`, `258aee4d5`, `bb2d109a7`). Two
   lanes measured this independently. With the five verification-wave commits the range to HEAD is 11.
2. **A spec-required pin file is silently deselected by the repo's default `addopts`.**
   `test_service_tool_flag_off_byte_identical.py` is entirely `@pytest.mark.integration`, so a *default*
   `pytest` run proves only **4 of the 5** exact-set pins. "All five pins green" is provable only with
   an explicit `-m integration` / addopts override. **This is a property of the repo's `addopts`, not of
   phase 2** — but any future claim of "all five pins green" must name its invocation.
3. **Two spec-required properties had NO test node in the developer's corpus** — the D10 layer-3
   literal-tool-name allow bypass, and the reuse-path `lateness_cap_seconds` echo. Both were closed by
   this verification (`bfff96933`), mutation-verified.
4. **A pin written by this verification over-claimed, and the adjudication lane caught it.** The first
   version of the GAP-1 pin asserted "zero side effects" via three assertions that **could not fail**
   (the spy was never injected; the project resolve refused first; the auto-MagicMock source repo made
   every label look taken) — the flow was *structurally incapable* of reaching the engine. The
   mutation proof (M1 → red only on an incidental `"ari" in result` substring) is in §3/N10. Repaired in
   place (`8cb64ef63`) so the corpus carries no over-claiming pin, with the side-effect assertions
   moved FIRST and a negative control added so the pin cannot degrade into a tautology again.
5. **The 29m59s below-floor boundary had no dedicated test node** (only 29 m). Production behaviour was
   measured correct on the real engine (`in 29m59s` → rejected, 0 engine calls, no row, label not
   burnt), and the boundary is now pinned and mutation-proved (`dc42fe670`).
6. **Doc-hygiene (closed).** A stale privileged-category trajectory comment survived the developer's
   "closed by this commit" claim — `decisions.md` P2-OK-7's "cosmetic naming half … is closed by this
   commit" was *nearly* true. Fixed in `928dba2ae` — and the fix recorded a **historical exception** the
   developer had not: at `152a9c93b` (7 = `secrets`, 2026-10-17) the set had 7 entries while the
   upgrade pin still asserted 6, i.e. **the pins were red at that commit** and closed 15 s later in
   `72fbd6126`. Every other step was genuinely same-commit.
7. **`decisions.md` P2-OK-7 phrasing.** Its claim *"the exact-set pins were never red between commits"*
   is scoped to phase 2 and is **true** there, but as a phrase it could be misread as a global
   invariant over the set's whole history — it is not, by 15 s, at `152a9c93b` (agent-secrets P1).
   Adjudicated: the two do not conflict, no `decisions.md` edit is required, and the historical fact is
   now recorded in the test file itself.
8. **Plan drift (plan owner's lane, not fixed here).** `phase2-plan.md` D10's pin-line table no longer
   matches HEAD (plan cites `upgrade :142-166` / `attestation :186-208` / `maintenancer :324-341` /
   `secrets :218-232`; actual `:152-189` / `:189-224` / `:334-364` / `:225-252`), and its
   `_tool_registry.py:239-338` citation is now `:259-403`.
9. **`PACKS.md` registration gap (closed by this lane).** `tests/unit/tools/test_self_alarm_tools.py`
   and the two new pin files were not registered in `PACKS.md` — they are now (see the
   `self_alarm_phase2_corpus_unit` / `self_alarm_privilege_pins_unit` rows).
10. **Mock-fidelity note (informational).** `FakeRegistry.start_adapter` writes `status='running'` where
    the real `SourceRegistry.start_adapter` writes `'starting'` (`registry.py:611`). The wrapper tolerates
    both, so there is no behavioural divergence — but a fake should reproduce the real value.
11. **Two spec-reading advisories for the tool doc (plan owner's lane).**
    (a) `label_owned_by_other_agent` is surfaced as a **`fallback_reason`**, not an `"ERROR: …"`
    envelope — the call *succeeds* with a fresh `-2` label (spec-mandated: `phase2-plan.md:272-289` +
    `decisions.md` P2-DEV-12), but an agent-facing reader hunting for an error string will not find one.
    (b) The **reuse** response's `status` is the **pre-update** status (the plan's D4 / I3-BLOCK1a
    ruling) — reading it as "the current row status" is wrong; the row is already re-armed.
12. **Live daemon restart observed during the window** — the live pid moved from 969286 to 2296421.
    This was an **external** restart by another actor; no lane in this verification contacted, signalled
    or restarted `:9797` (verified before/after in every lane's fence audit).

---

## 5. Code changes made by this verification lane

**All five commits are TEST-ONLY.** No lane changed a single line of production code
(`daemon/**`), and no lane touched `agents/ari/meta.json`, the Phase-1 board modules, any migration, or
any sealed artifact.

| commit | file | change | proof |
|---|---|---|---|
| `bfff96933` | `tests/unit/tools/test_self_alarm_privilege_pins.py` (new, 246 lines) | pins the two corpus gaps: the D10 layer-3 literal-tool-name allow bypass, and the reuse-path `lateness_cap_seconds` echo (None + 900) | mutation-verified by the author; **independently re-verified** by N10 |
| `928dba2ae` | `tests/integration/test_maintenancer_spawn_resolves_tools.py` (+10/−2) | comment-only: correct the privileged-category trajectory (8 → 10) and record the one same-PR-pin exception | assertion block byte-identical; member set re-extracted = 10 |
| `63329e05` | `tests/unit/tools/test_self_alarm_literal_allow_side_effect_pins.py` (new, 234 lines) | the zero-side-effect invariant on the literal-allow path with **engine-reachable** wiring + a **negative control** that neutralises the gate and asserts the engine IS reached | mutation-verified (red under M1 with a real schedule created) |
| `8cb64ef63` | `tests/unit/tools/test_self_alarm_privilege_pins.py` (+158/−35) | make the vacuous side-effect assertions **load-bearing**: spy injected before tool construction, `_source_repository` set, project-resolving `_instance_repository`, gate-specific refusal assertion, negative control, side-effect assertions moved first | M1 now fires on `assert service.create_calls == []` (line 244) with the real create payload |
| `dc42fe670` | `tests/unit/tools/test_self_alarm_tools.py` (+62) | pin the **29m59s** below-floor boundary — the knife-edge case that distinguishes `>= 1800` from a looser comparison | mutation-proved: `MIN_HORIZON_SECONDS = 1799` ⇒ the node dies at its first assertion; byte-exact restoration proven |

Documentation written by this lane (committed with the report):
- `.agents/tester/RESULTS/2026-10-20-ari-tools-phase2-self-alarm-verification.md` — this file.
- `.agents/tester/LESSONS/2026-10-20-self-alarm-phase2-verification-gotchas.md` — 11 gotchas banked.
- `.agents/tester/PACKS.md` — this commission's pack rows + the registration gap closed.

**No `.agents/tester/rules/` file was touched** (user-owned, read-only). `.agents/shared/planning/ari-tools/`
was **not** edited either — the plan/decision drift is reported to the leader, not unilaterally fixed.

---

## 6. Documentation

- `LESSONS/2026-10-20-self-alarm-phase2-verification-gotchas.md` — 8 gotchas banked.
- `PACKS.md` — this commission's pack rows (appended by the tester lane).
---

## 7. FINAL GATE — anchored to ONE tip (HEAD `dc42fe670`)

The tip moved **five times** during the verification window, so the commission's verdict is anchored to
a single HEAD by a dedicated final gate lane.

| check | expected | actual | exit | verdict |
|---|---|---|---|---|
| F1 tip identity | 11 commits, all 5 wave commits present, wave is tests-only | **11** · all present · tests-only | 0 | ✅ |
| F2 phase-2 corpus | 146 passed | `146 passed, 145 warnings in 9.72s` · **0 deselected** | 0 | ✅ |
| F3 five exact-set pins (addopts cleared, no `-m`) | 93 passed, 0 deselected | `93 passed, 641 warnings in 36.86s` · **0 deselected** | 0 | ✅ |
| F3 control C1 — `service_tool_flag_off` with `-m integration` | 9 passed | `9 passed, 1 warning in 7.48s` | 0 | ✅ |
| F3 control C2 — same file, **repo default addopts** | 9 deselected | `9 deselected, 1 warning in 0.60s` | 5 | ✅ (the trap, reproduced) |
| F3 control C3 — `maintenancer` with `-m integration` | 13 deselected | `13 deselected, 1 warning in 0.59s` | 5 | ✅ |
| F4 frozen Phase-1 interface | EMPTY diff | **0 lines, 0 bytes** | 0 | ✅ |
| F5 seal tripwire | exactly 7, `NEW=[]` | **exactly 7** · `NEW = []` | 1 (designed red) | ✅ |
| F5 content anchors | 46 | `46 passed` | 0 | ✅ |
| F5 sealed-path diff | EMPTY | **0 lines** (`SEALED_PATHS_CHANGED_BY_PHASE = []`) | 0 | ✅ |
| F6 Phase-1 board corpus | 110 (56+34+20) | `110 passed, 65 warnings in 11.19s` | 0 | ✅ |
| F7 verification wave is tests-only | 5 × `tests/**` | **5/5, zero non-test files** | 0 | ✅ |

**Provenance.** `daemon.__file__ = /home/nea/ensemble-src-wt-ari-tools/daemon/__init__.py` — the
worktree, not the live install.

**Developer-scope proof.** `git diff --name-only 99a28e4f3..HEAD -- daemon/ agents/ frontend/ plugins/`
returns **exactly 5 files and no test file**: `agents/ari/meta.json`,
`daemon/services/self_alarm_service.py`, `daemon/tools/_tool_registry.py`,
`daemon/tools/instance.py`, `daemon/tools/self_alarm.py`.

**DB-independence.** F2/F3/F6 were re-run with an explicit `ensemble_dev` fence and gave **identical**
counts (146 / 93 / 110) — and in fact no run connected to PostgreSQL at all (the F3 integration file
uses `sqlite:///{tempdir}` and `test_secrets_registration.py`'s `psycopg2` string is a fixture message).
No log contains `postgres` / `psycopg` / `10.44.0.2` / `ensemble_prod`.

**Fences.** Live install untouched — read-only `ps` self-check only, `ensemble-main` PID **2296104**,
never signalled, never contacted. Port 8088 untouched. Read-only git; HEAD unchanged before and after.

---

## 8. Overall status

```
Unit tests (phase-2 corpus)        : ✅ PASS 146/146
Exact-set privileged pins (5)      : ✅ PASS 93/93 (marker-correct, 0 deselected)
Real-engine e2e (real PG)         : ✅ PASS 5/5 probes
Dual gate (ari-exclusive)          : ✅ PASS 6/6 probes
One-shot lifecycle                 : ✅ PASS 3/3 probes
Frozen Phase-1 interface           : ✅ PASS (EMPTY diff; seal 7==7; NEW=[])
Phase-1 board corpus               : ✅ PASS 110/110
tests/unit/tools/ census delta     : ✅ PASS (NEW ∅, RESOLVED ∅, STABLE 13)
ensure.md Core                     : ✅ PASS 4/4 critical · 2/2 important · 1/1 nice-to-have
ensure.md Release Gate             : NOT RUN (not warranted — additive change set)
--------------------------------------------------------------------------------------------------
Product defects found              : 0
Coverage gaps found and closed     : 3   (bfff96933 ×2 + dc42fe670)
Over-claiming pins caught & fixed  : 1   (8cb64ef63)
Production-code changes by tester  : 0
```

### Testing Complete
- **Unit Tests**: PASS
- **Mock Tests**: n/a (no mock service — the real engine was used throughout)
- **ensure.md Validation**: PASS — Critical 4/4 · Important 2/2 · Nice-to-have 1/1
- **Overall Status**: 🟢 **READY** — the phase-2 capability is verified end-to-end against the real
  engine, the Phase-1 contract is provably untouched, and the corpus's coverage gaps are closed.

### Known caveats carried forward (not defects of this change set)
1. The **cascade-completion** nodes that would cover the full parent→child→complete race are
   module-level skipped (pre-existing, structural). "IMPORTANT-2 PASS" is not full race coverage.
2. The live 30-minute **fire** was never executed (explicitly forbidden): the fired-transition end
   state and the wrapper's read/mapping are *executed*; the upstream `adapter → _on_complete_callback`
   half is *code-verified by citation*. A real fire remains an unexercised path by design.
3. A default `pytest` run still proves only **4 of 5** exact-set pins — the repo's `addopts`
   deselects the fifth. Any future "all five pins green" claim must name its invocation.
4. The `phase2-plan.md` D10 pin-line table has drifted vs HEAD (plan owner's lane).
