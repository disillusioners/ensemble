# nest-ops shakedown defect patch — TESTER GATE (final code gate, branch verification)

**Verdict: 🟢 9/9 GATES PASS — GO for stage dispatch.** NO stage / promote / version-derivation
performed by this gate (the giter lane owns stage dispatch; the user's 3-factor nonce ceremony
owns the promote). Follow-up-ledger items (H1/H3, ML-1, DF-5, REST-ingress class) untouched.

- Lane: `fix/nest-ops-shakedown-defects` @ HEAD `2da0a3e924b984ae0d0efd7fcb63cec26efd8e0e`
  (tree clean — verified before AND after every lane; HEAD byte-identical at close).
- Lane shape (labels from `git rev-parse`/`rev-list`, never prose): base `b257842c9` → Phase-1
  tip `dfed5926f` → DF-4 sync merge `cdf348057` (parents `dfed5926f` + `074833b45`) → fold base
  `89e62959c` → HEAD `2da0a3e92`. Range `b257842c9..HEAD` = 204 commits (the DF-4 sync merge
  pulls the `074833b45` lineage into the range — informational).
- Gate date: 2026-10-24 UTC. Evidence bar per the code-review S6 ruling: planted-mutation
  DIGESTS + verbatim outputs + commit refs; zero bare claims. Raw logs: §10 index.

## 0. Gate verdicts

| # | Gate | Verdict | One-line evidence |
|---|------|---------|-------------------|
| 1 | 12-cycle mutation matrix (F1–F12) + structural gate cluster | ✅ PASS | **29/29 legs** plant→RED→restore→GREEN, every leg digested; post-matrix pin corpus 255 passed |
| 2 | Four ladder legs, IDENTICAL commands both sides | ✅ PASS | FAILED sets **sha256-identical** (`505d8a4f72bbebc7…84e6`) at all four endpoints → `net_new=∅` on leg 0 / A / B / containment; ERROR sets empty |
| 3 | Real-PG-only + fence discipline | ✅ PASS | Every lane: per-variable `POSTGRES_*` scrub, throwaway role+DB, FAKE_HOME fences, zero live contact (§8) |
| 4 | e2e through the REAL spawn order | ✅ PASS | Exact-ID husks via `spawn_instance_with_mcp`, DESC offsets **1001/1002**, `job_queue_items`=0 for both IDs |
| 5 | BEFORE/AFTER for all 12 defects | ✅ PASS | **12/12 REPRODUCED at base → 12/12 FLIPPED at HEAD**; all four required headlines confirmed both sides (§4) |
| 6 | Acceptance pack at 39/39 | ✅ PASS | **39/39 PASS in 135.77 s** (39 literal dots, 0 skips; neither timeout layer fired) |
| 7 | T4.8 re-measure vs D-20 | ✅ PASS | per-window `[1002, 5, 3]` deterministic ×3; **1002 == 1002 gate-equality EXPECTED** (zero headroom by construction); wall 2.854–3.522 s vs 10 s budget; `end_of_set` + honest `truncated=false` |
| 8 | Branch boot smoke | ✅ PASS | banner `Starting Ensemble v0.27.6`, livez+readyz 200 @ **19.2 s**, prompt-artifact baseline **161/161 clean**, verified-port teardown, live fence intact |
| 9 | RESULTS + evidence commit | ✅ | this file on branch `tester/nestops-shakedown-verification` (pushed; leader-sweep pending) |

---

## 1. Method summary

- **Four unique-SHA endpoint runs** (`base`/`p1`/`m`/`head` in throwaway `/tmp/ladder-*`
  worktrees, `uv sync --frozen`, Python **3.14.7 byte-matched** on all four) instead of 8
  overlapping runs; legs derived by pairwise `comm` on sorted FAILED/ERROR sets — identical
  SHA + identical command ⇒ identical set, mathematically equal to per-leg runs.
- File-existence matrix first: lane-new files absent at earlier endpoints recorded **N/A**
  (the lane evidence doc's own convention); **net-new claims ONLY from the identical-universe
  comparable core** (10 files present at all four endpoints).
- pytest-randomly NOT installed in any leg venv → no randomization to disable (uniform).
- No exit-124 anywhere in the ladder (runs 82/84/77/91 s) → chunked protocol never fired.
- `import daemon` verified resolving INSIDE each worktree before any run (never the shared
  checkout's editable install).

## 2. Gate 2 — the corrected net-new ladder (D-21)

Per-endpoint core runs (identical command, comparable core):

| Endpoint | Exit | Passed | Failed | Errors |
|---|---|---|---|---|
| base `b257842c9` | 1 | 195 | 6 | 0 |
| p1 `dfed5926f` | 1 | 199 | 6 | 0 |
| m `cdf348057` | 1 | 202 | 6 | 0 |
| head `2da0a3e92` | 1 | 224 | 6 | 0 |

FAILED list — **verbatim, identical at all four endpoints** (set sha256 `505d8a4f72bbebc7…84e6`):

```
FAILED tests/api/test_instance_ui_prefs_api.py::test_get_instances_list_includes_prefs_fields
FAILED tests/api/test_instance_ui_prefs_api.py::test_get_instances_list_includes_icon_tag
FAILED tests/test_job_queue_tools.py::TestResolverRoutedTools::test_watch_job_terminal_enriches_result_from_instance
FAILED tests/test_job_queue_tools.py::TestResolverRoutedTools::test_watch_job_terminal_no_manager_is_best_effort
FAILED tests/test_job_queue_tools.py::TestResolverRoutedTools::test_watch_job_terminal_manager_exception_is_swallowed
FAILED tests/test_job_queue_tools.py::TestResolverRoutedTools::test_watch_jobs_terminal_enriches_result_from_instance
```

- **LEG 0 (base→p1): net_new=∅, fixed=∅** · **LEG A (p1→m): net_new=∅, fixed=∅** ·
  **LEG B (m→head): net_new=∅, fixed=∅** · **CONTAINMENT (base→head): net_new=∅, fixed=∅**
  (errors likewise empty on every leg). Zero 3×-isolation candidates (no one-sided reds).
- Full exists-subset record runs: base 6F/195P · p1 6F/271P · m 6F/293P · head 6F/368P —
  FAILED lists identical to core at every endpoint (lane-new files green where they exist).
- **Pre-existing reds proven at base (named):**
  - `TestResolverRoutedTools` ×4 — the coder's named base debt; confirmed red at p1 AND m
    (and base) — ladder-neutral, as declared.
  - `test_orphan_watcher_cancelled_on_startup_sweep` ×1 (lives in
    `tests/postgres/test_06f500af_bug_class_eliminated_pg.py`, outside the 14-file universe) —
    coder-named; direct single-node runs RED at p1 (3.12 s) and m (2.79 s), identical both sides.
  - **NEWLY NAMED base debt (this gate):** `test_instance_ui_prefs_api` ×2 —
    `TypeError: _ManagerStandin.list_instances() got an unexpected keyword argument 'search'`
    — red at BASE `b257842c9` (predates the lane; NOT in the coder's named list). Routed as
    known-debt, not a lane regression.
  - `instance_tools_unit_test.sh` pack: 2F/205P **identical at all four endpoints**
    (`TestRegistration::test_planner_and_tester_resolve_subtree_messages_via_registry`,
    `TestSubtreeStatusRegistration::test_leader_planner_tester_opt_in_via_production_seam`)
    — pre-existing, both sides of every leg.
  - `self_heal_prefix_worktree_test.sh`: **INCONCLUSIVE on this host** — hard-codes a macOS
    `MAIN_REPO=/Users/...` and dies at `cd` (line 43) before creating anything; its leg-A
    merged-lane evidence is therefore NOT reproduced by this gate (foreign-lane debt;
    environment limitation, not a lane defect).
- Ladder worktrees removed; logs kept (`/tmp/nestops-ladder/`, 45 files).

## 3. Gate 1 — independent mutation matrix (29 legs)

Every leg: **plant → PREDICTED red set → measured RED (exact match, or superset/shortfall
explained) → `git checkout --` restore → porcelain EMPTY → re-run GREEN**. Full TSV:
`/tmp/nestops-mutation/matrix.tsv`; per-leg `.plant.diff` + `.red/.green.log` + sha256s.

| cycle | defect | plant (file) | planted sha256 | pred=meas | green sha256 |
|---|---|---|---|---|---|
| F1 | registry drift | `_tool_registry.py` KNOWN alias dropped | `70e0b40c0c733793` | 2=2 | `ef604c09af151714` |
| F2a | census (a) | `instance_read.py` windowed-loop revert | `b25be62cc16076c0` | 2=2 | `c6a3a75dd9b7f194` |
| F2b | census (b) | `total_rows = 0` | `883456e518c60f33` | 2=2 | `44334dd29bf1780d` |
| F2c | census (c) | `scan_truncated` hard-`True` | `8ac185038d2c9148` | 1=1 | `f7983dfea5b180f5` |
| F3a | F3 decisive | offset expression neutered (≠ SP-1) | `265b77e415e06882` | 4=4 | `5bce9008ceac06d0` |
| F3b | F3 §9.3 | `last_activity_at IS NULL` leg neutered | `6eb5bbbc1dafba43` | 1=1 | `631d58e12c10a62b` |
| F4a | truncated (a) | `truncated = False` | `56ebd82ba9c6b9ea` | 5=5 | `76daf14c4aa6ae69` |
| F4b | truncated (b) | empty-window `break` removed | `7f3b7e9fc38cc0f9` | 5=5 | `a4253c40d1e43a54` |
| F5 | dead-jobs | snapshot-table read (keys intact) | `5244a8e076c04dc2` | 2=2 | `722198f74323112c` |
| F6a | F6-i(a) | confirmation leg apply-only | `b8646a4442fe53bc` | 2=2 | `da1fa1f1f62f4afa` |
| F6b | F6-i(b) | provenance above the fork | `359aa486271849cd` | 5=5 | `c1102ccfe7b58d86` |
| F6c | F6-iv | guard-10 above budget rails | `addd5f818475ae25` | 2=2 | `f8ab351904a66401` |
| F6d | F6-ii | gate effect deleted on apply | `2f365c828d47aa26` | 2=2 | `c0c8bceb025e1326` |
| F6e | F6-iii | preview writes `committed` | `1135027c56b58c58` | 4=4 | `ba30371cd06985eb` |
| s1 | structural | hard-coded `would_refuse_with` | `3c893cb8659519ad` | 1=1 | `2d7079a210a497d7` |
| s2 | structural | bare-string gate return | `8e89600f4ffbe242` | 6=6 | `cfab2429e4d58869` |
| s3 | structural | token drift gate↔preview | `438b12873135483a` | 4=4 | `d900876a72a71326` |
| s4 | structural | hard-coded `act_gate_would_pass.reason` | `89c450c8af690f1a` | 1=1 | `afd2504483b51a93` |
| F7 | reflection | second home (marker-only) | `74b5083778ea9ca1` | 6→**5** ⚠ | `f9f49470e5fc7d21` |
| F8a | resolver (a) | `manager.get_instance` restored | `289db8c22b767136` | 1→**2** ⚠ | `00eeb6e1bc2c7d07` |
| F8b | resolver (b) | re-elevation write | `fe732d945db5b0e3` | 1=1 | `ae91caf63e44dbf1` |
| F9 | copy | old nudge-recommending copy | `e2ce081c2a3c52b6` | 1=1 | `fb23ca7dc4ddd88a` |
| F10 | echo | `audit_row_id` dropped (both sites) | `e8e1fe10516ba6e8` | 6=6 | `83ad38a0ed6fda80` |
| F11a | mint | forwarding dropped at enqueue (≠ SP-2) | `96d99b0afa796c85` | 3=3 | `cd7947a5c5394c48` |
| F11b | S3 pop | reserved-key pop removed | `e3273e3f27eaa618` | 3=3 | `e0285d4b6091b90b` |
| F11c | factory | agent-id mis-bound as instance-id | `e66e0648575bdc72` | 3→**4** ⚠ | `aaf8648ef22bad02` |
| F11d | expiry | caller-window compare removed | `b6d32be36eb4054d` | 2→**1** ⚠ | `0686e72e6cff6e6b` |
| F11e | depth-cap | cap check removed | `148aaf425f4b3b13` | 2=2 | `be5852a4ae9ff079` |
| F12 | status | `status=status` forward removed | `8a15d85d4c922c96` | 4=4 | `4fa08e668a29541d` |

Plants were **independently re-derived** (different seams than the coder's SP-1/SP-2 where
the map allowed). Declared anchor-neutral, NOT cycled (per the map): F11-b5 legacy chainless
row; `TestEnvironmentFence::test_ambient_snapshot_records_the_poison`.

**Disclosed anomalies (all with RED unit pins carrying coverage — reviewer-routed, not defects):**
1. **F7 shortfall (6→5):** the real-PG `test_f7_iv_static_plus_marker_apply_succeeds_with_disclosure`
   stayed GREEN — it asserts `audit_row_id` + marker + committed ledger but NOT the
   `already_protected`/`protection_reasons` echo; the unit F7-iv twin IS red. Coverage gap in
   the real-PG twin only.
2. **F8a superset (1→2):** extra red `test_fuzzy_miss_refuses_with_the_suggestion` also asserts
   `manager.get_instance_calls == []` — pin is STRONGER than predicted.
3. **F11c superset (3→4):** extra red `test_no_chain_without_a_caller_instance` — the mis-bind
   defeats the empty-instance guard. Pin stronger than predicted.
4. **F11d shortfall (2→1):** `daemon/tools/provenance.py::restart_provenance` re-checks window
   expiry INDEPENDENTLY of the mint, so the e2e b2 pin stays green when only the mint's compare
   is removed — defence-in-depth working as designed; the mint-level compare is proven only by
   the unit pin (`test_expired_caller_window_pops`, RED).
5. Method disclosure: two first-attempt cycles (F2a/F2b) re-run because `-m integration`
   deselected a unit node in a mixed set; recorded rows are the re-runs with `red_deselected=0`.

Post-matrix whole-corpus confirmation at clean HEAD: **255 passed** (182 unit + 39 real-PG +
registration/frozen-discovery) in 288.90 s. Worktree porcelain EMPTY, HEAD unchanged.

## 4. Gates 4+5 — BEFORE/AFTER for all 12 defects (the headline deliverable)

**Fixture (BOTH sides, real PG16 local, throwaway role+DB):** tier-1 husks via the FULL REAL
spawn order — verbatim `manager.spawn_instance_with_mcp(instance_id=<UUID>, agent_id='maintenancer')`
for `5ba21b2d-43b7-4378-be53-cca781f9c124` (12d backdate) and
`7ccd53c3-2448-4f1f-9bb2-969ee9d1f37f` (16d backdate), TEXT-ISO production format; tier-2 = 1,001
production-ORM filler rows (exact `_spawn_instance_db_sync` kwargs, one session, microsecond-spaced
ISO). Result both sides: 1,003 rows; husks at **DESC offsets 1001/1002** (outside the first
1,000-row window — asserted via OFFSET query both sides); `job_queue_items`=0 AND `message_queue`=0
AND `task`=0 for both husk IDs; row shape `idle / version=1 / last_activity_at=NULL`. **Nothing
was ever dispatched/messaged/enqueued to either husk ID.**

| # | BEFORE (base `b257842c9`) | AFTER (HEAD `2da0a3e92`) | Δ |
|---|---|---|---|
| F1 | alias absent from discovery/KNOWN/DYNAMIC; drift masked by the `tool_names \|= KNOWN` merge (`_tool_registry.py:808`) | alias in ALL three lists; **bidirectional drift pin** `set(KNOWN)==source-derived → True`, both diff sets `[]` | FLIPPED |
| F2 | `Σcounts=1000 ≠ total_matching=1003` (δ−3); **no `totals`, no `reconciled` key**; no idle liveness bucket | `totals={1003,1003,scan_truncated:false, reconciled:true}`; `Σ==total_matching==1003` δ0; idle bucket = 1003 | FLIPPED |
| F3 | **both husks UNFLAGGED** (`total_flagged=0`, `stale:false`), and **outside the scan window** (scan read 1000/1003; `INSIDE the scan window? False` ×2) | **both husks FLAGGED `never-dispatched-husk`** (`count=2`; A age 1,036,850 s / B 1,382,450 s vs 86,400 threshold; full evidence sentence: "no activity ever … no task, no job item, no messages, no children, no question pack, no pause marker") | FLIPPED ★ |
| F4 | `truncated=False` with 803 rows remaining (FLAG LIES ×2); health `scan_truncated=False` while 3 rows NEVER scanned | `truncated=True` on under-pages, `False` only at exact/exhausted (FLAG LIES? False ×4); scan `end_of_set`, `truncated=false`, both husks (offsets 1001/1002) REACHED | FLIPPED ★ |
| F5 | no `dead_jobs_total`, no `dlq_note` (5 dead seeded) | `dead_jobs_total=5 == Σper-queue(5)`; `dlq_depth(2) ≠ 5` **correctly** — the note names the two different tables | FLIPPED |
| F6 | unconfirmed → `protected-target-confirmation-required`; confirmed preview REFUSED `no-user-provenance`, **no `act_gate_would_pass`**, no preview outcome | unconfirmed → same refusal; confirmed → `outcome=previewed`, `act_gate_would_pass={ok:false, reason:no-user-provenance}`, `would_refuse_with=[no-user-provenance]` (STRUCTURAL), target row UNTOUCHED; apply path unchanged (`outcome=refused`) | FLIPPED ★ |
| F7 | leader previewed `already_protected:false`, no `protection_reasons` (marker-only home) | leader → `already_protected:true, protection_reasons:["leader"]`; marker → `["marker"]` | FLIPPED |
| F8 | `manager.get_instance` spy FIRED 1×; MCP `get_schemas_for_server` FIRED 1× | both spies **0×**; row byte-identical before/after | FLIPPED |
| F9 | copy recommends `instance_nudge` | copy names `instance_terminate`/`instance_health`/`instance_get`; "Do NOT nudge an idle, evidence-free instance" | FLIPPED |
| F10 | no `audit_row_id` in response (row existed, unechoed) | `audit_row_id=c6904c2d-…` echoed == committed ledger row id (all three act families pinned) | FLIPPED |
| F11 | genuine user-origin chain → job row source `agent:maintenancer`, `_inherited_provenance_windows=None`, protected terminate **REFUSED `no-user-provenance`**; expired indistinguishable (same token); control: with a live window the gate ADMITS (proves the LOST CHAIN is the cause — the audit-row-276b7571 live-refusal class) | genuine chain end-to-end (job_create → real MessageQueue claim → funnel) → **ADMITTED**, audit `inherited/depth=1/root_source=api/root_message_id=root-msg-0001`; fabricated (REST-injected, live window) → REFUSED (S3 pop strips); expired → REFUSED; agent-rooted/depth-cap → REFUSED | FLIPPED ★ |
| F12 | `status="running"` filter DROPPED: 200 rows returned (197 wrong), `total_matching=1003` ≠ 3 | `count=3 total_matching=3`, all rows `running`, `filters_applied={status:running}` | FLIPPED |

★ = the commission's four required headlines, all confirmed on BOTH sides.

**BEFORE-side disclosures:** the F11 BEFORE drove the full lane to a real job row (real
`job_create`, stored source `agent:maintenancer`) and invoked the funnel's REAL
`_inherit_provenance_window` with that exact source; the job→message source copy is evidenced
by base source (`job_processor.py:1327`) rather than a row (no worker pool in a driver) —
disclosed. The AFTER side drove the claim through `task_processor` on the real MessageQueue row
(the pins' declared no-worker-pool harness). AFTER-side F8 ran with built-in MCP servers
disabled (fence); the trigger is a local stdio server row, equivalent probe.

## 5. Gate 7 — T4.8 re-measure (D-20 arbiter)

Verbatim evidence-doc command ×3 (fresh process each; JSON records byte-identical except
`wall_time_seconds`; record-minus-wall sha256 `0c17d90ef07aa8ae…` all three):

| measure | R1 | R2 | R3 | gate |
|---|---|---|---|---|
| fixture rows | 1003 | 1003 | 1003 | — |
| statements_total / pre-loop | 1012 / 2 | 1012 / 2 | 1012 / 2 | — |
| per-window counts | **[1002,5,3]** | [1002,5,3] | [1002,5,3] | (i) ≤1002 per window |
| per-window max | 1002 | 1002 | 1002 | **1002 == 1002 — EXPECTED equality, zero headroom by construction** |
| wall (s) | 3.075 | 2.854 | 3.522 | (ii) ≤10 — max 35.2% of budget |
| stop/truncated | end_of_set/false | same | same | (iii) honest only at true end-of-set ✔ (1000+3=1003; empty terminator) |

Both husk IDs in `husks_flagged` (verified == `HUSK_A_ID`/`HUSK_B_ID` at
`tests/helpers/nestops_pg_fixtures.py:445-446`). Pin green ×3. **T48: PASS.**

## 6. Gate 6 — acceptance pack

`timeout 330 bash test/packs/nest_ops_shakedown_unit_test.sh` (dual-layer: pack-internal 300 s +
per-test 120 s; neither fired) → **`39 passed … in 135.77s`, `RESULT: PASS`, exit 0**. The 39-dot
progress line (0 `s`/`x`/`F`) proves every test RAN — the 39th (scratch-DB fence, the tidier
fold's 38→39) executed and passed: `resolved == ops_db.db_name` + scratch-shape +
`FORBIDDEN_DATABASES` checks. **Caveat (disclosed):** under the house per-variable scrub the
fence's ambient-inequality leg is inert (ambient absent); the pin's own docstring discloses this
— do not over-claim the ambient leg. Header self-describes "EXACTLY two files (39 tests)";
`grep -c "def test_"` corroborates 18+21=39. PACKS.md row (written pre-fold at 38/38 @
`d84518451`) refreshed by this gate's evidence commit. Worktree byte-unchanged.

## 7. Gate 8 — branch boot smoke

Throwaway worktree `/tmp/nestops-boot` @ `2da0a3e92` (lane worktree untouched; its `.env` points
at the shared dev DB — not used), scratch role/DB `ens_boot_ee68cfab`/`ens_boot_test_ee68cfab`,
direct uvicorn on **127.0.0.1:18079** (never `dev.sh` in a worktree — its fence guard refuses by
design), HOME fenced to a mktemp fake-home (resolved EMPTY — no HOME-derived path touched),
`ENSEMBLE_SELF_ENV=dev`, per-server MCP disable switches (no bare `MCP_DISABLE_BUILT_IN` exists).

- **time-to-livez-200 = 19.2 s; readyz-200 same iteration** (components all true;
  `inflight_turns=0`).
- **Banner verbatim:** `Starting Ensemble v0.27.6` (logged twice — pre-existing double
  `create_app()` at `daemon/api.py:4983/:4986`; branch does not touch `daemon/api.py`).
- **Baseline:** `PROMPT-BASELINE: OK — 161 versioned artifact(s) match .prompt-artifacts.sha256` (exit 0).
- **DB isolation proof:** `pg_stat_database` — the scratch DB was the ONLY one with backends
  (numbackends=8, 80 tables); **0 occurrences of `10.44.0.2` in boot.log** — the ambient
  `POSTGRES_*` on this shell points at REMOTE PROD and the per-variable scrub + explicit
  overrides neutralized it (this is why the glob-form scrub ban is a critical note).
- **Teardown:** ownership-verified SIGTERM (socket-owner pid 2117192 == expected; `/proc/<pid>/environ`
  cross-check), graceful shutdown, port freed, role+DB dropped (catalog 0/0), worktree removed
  (only the worker's 12 untracked scratch files ever present; fake-home EMPTY).
- **Live fence:** one read-only probe — livez 200 `v0.27.4` uptime-continuous, MainPID 1208616,
  NRestarts 0, epoch 06:48:12Z (predates the gate); 0 files modified under `/home/nea/agents-ensemble`.
- Benign anomalies catalogued: plane-MCP warmup errors (minimal-env artifact), pre-existing
  `git_commit` deny-entry warning (absent from the branch diff), KB bootstrap pass-through,
  boot-time `clear_all preserve_in_flight=True` sweeps (0 rows, scratch DB). Latent hazard
  noted: source-mode boot from the agent shell is NOT cgroup-isolated — the staged-binary lane
  owns that isolation. Observability wrinkle (pre-existing): `data/ensemble.json` persists
  PostgresConfig DEFAULTS — not evidence of the DB used (use `pg_stat_database` + `/proc`).

## 8. Gate 3 — fence compliance (all lanes)

Per-variable `POSTGRES_*` scrub on EVERY invocation across all 7 lanes (glob form never used —
it is a silent no-op); PG contact exclusively 127.0.0.1:5432 via self-provisioned throwaway
roles/DBs (`ens_shakedown_<hex>`/`ens_boot_<hex>`), each dropped with proof; `ensemble_prod`
never contacted (absent on the local cluster by that name; REMOTE prod ambient neutralized —
§7); `:9797` and `:8088` never contacted by any lane (one read-only livez probe excepted, §7);
no process killed except each lane's own verified children (boot worker); no daemon booted
except the fenced scratch smoke; the two husk IDs never dispatched/messaged/enqueued; FAKE_HOME
fences wherever HOME-derived resolution was possible; both historically-protected worktree
paths found ABSENT (lanes landed; constraint vacuous — nothing to protect); lane worktree
porcelain EMPTY + HEAD `2da0a3e92` verified at the close of every lane that touched it.

**Residue attribution:** every surviving/duplicated `ens_*` PG object observed mid-gate was
timestamp-attributed to OTHER lanes (pair-swaps proven, not leaks); zero gate objects survived
except one coder-lane leftover `ens_shakedown_test_3c83f13ca0` (18:12Z — predates this gate,
~4h before our first run) — reported, left in place. Two orphaned MCP children from the
before-base lane's first fixture run (pre-kill-switch) were left running per no-kill discipline
and reaped after ppid/port verification at gate close (commit-lane action).

## 9. Disclosures & routed items (none block the GO)

| Item | Class | Route |
|---|---|---|
| real-PG F7-iv lacks the `protection_reasons` echo assertion (unit twin covers) | coverage gap | reviewer / next pin pass |
| F11 mint-level expiry compare masked at e2e by `restart_provenance`'s independent re-check (unit pin covers) | defence-in-depth note | reviewer |
| `test_instance_ui_prefs_api` ×2 red at BASE (stale `_ManagerStandin` vs `search` kwarg) | pre-existing base debt (newly named) | test-debt lane |
| `instance_tools` pack 2 reds at BASE (subtree registry pair) | pre-existing base debt | test-debt lane |
| `self_heal` pack macOS-hard-coded MAIN_REPO | foreign-lane env debt | that lane |
| Boot banner double-log (`daemon/api.py:4983/:4986`) | pre-existing cosmetic | cleanup lane |
| Source-mode boot not cgroup-isolated | latent hazard | staged-binary lane owns isolation |
| `data/ensemble.json` persists Postgres defaults (not the DB used) | pre-existing observability wrinkle | future commission |
| Pack fence ambient-leg inert under house scrub | disclosed by the pin's own docstring | none (recorded) |
| `ens_shakedown_test_3c83f13ca0` coder-lane leftover | foreign residue | coder lane |
| hundreds of `ensemble_promptver_test_*`/`ensemble_blob_prune_*` DBs on local PG | housekeeping backlog | cleanup lane |
| HEAD-side fixture helper requires `SYSTEM_DEFAULT_PROJECT_ID` publish (driver-side) | harness note | KB-recorded |

## 10. Evidence index (raw logs, /tmp — ephemeral)

| Lane | Worker instance | Path |
|---|---|---|
| recon | `fd39626e-37a3-4ee1-95bd-bb193d36fa55` | (report only; 12/12 checks) |
| ladder | `84d014c7-e024-4fbf-9f16-8dd363eac6a7` | `/tmp/nestops-ladder/` (45 files) |
| mutation | `41b356b0-226a-4463-aaf0-bec5b6b7a54d` | `/tmp/nestops-mutation/` (matrix.tsv + 29×3 artifacts) |
| before-base | `d139739f-bcc1-46a6-b044-3357d628f7d9` | `/tmp/nestops-before/` (F00…F11 logs + 6 drivers) |
| e2e-head | `15b4ee75-8245-48e1-a6a7-295d1284b3dc` | `/tmp/nestops-after/` (A00…A04 + pin corpus logs) |
| t48 | `4d63a36d-f8b4-4649-95f4-a5ca94affc27` | `/tmp/nestops-t48/` (3× JSON + logs) |
| pack | `36c038e5-c571-4879-fad0-99ccd4438f4a` | `/tmp/nestops-pack/run.log` |
| boot | `2bcbae3e-7ffa-4838-bef8-2c299e3896af` | `/tmp/nestops-boot-gate8-evidence/` (sha256-recorded bundle) |

## 11. Conclusion

**All nine gates PASS on independent evidence.** The 12 defects are proven fixed in both
directions (base-repro → HEAD-flip, mutation-verified, pin-corpus green, ladder-clean, pack
39/39, scan-cost within gate, boot healthy, fences intact). The lane is **GO for stage
dispatch**. No code changes were made by this gate (quick-fix authorization was withheld —
none was needed). Evidence committed on branch `tester/nestops-shakedown-verification`
(this file + the PACKS.md row refresh), pushed for the leader sweep; the lane branch
`fix/nest-ops-shakedown-defects` remains at `2da0a3e92`, byte-clean, exactly as gated.
