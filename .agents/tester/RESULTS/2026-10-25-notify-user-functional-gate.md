# notify_user (Ari voice) — INDEPENDENT FUNCTIONAL GATE — 2026-10-25

**Commission:** `feature/ari-notify-user` @ `4b8ab274a27deabc4c94324e5795261725400a2f` (12 commits over base `7af1d2632d9e9b4af8280be03a8737804a2e58b2`), worktree `/home/nea/ensemble-src-wt-notify-user`.
**Gate mandate:** re-derive what can be re-derived, trust nothing unverified. Original symptom closed by proof: an alarm-shaped, scheduler-originated Ari turn the user NEVER sees in chat.
**Gate dispatcher:** tester `ebf4a0e9`. **Gate workers:** inventory `d1e2ef2e`, G2 `0834645a`, G3 `294e0df0`, G4 `702149d9`, G1 `b0f2bbf2`, G5 `9d630b7f`, docs `cde6e237`.

## VERDICT: **ALL 6 GATES PASS — functional gate GO** (0 blocking findings; 2 disclosed harness defects for the owning lane; 1 stale gate-expectation corrected on evidence)

| Gate | Verdict | One line |
|---|---|---|
| G1 boot smoke | ✅ PASS | Full B1 pinned sequence end-to-end from the committed harness, 62s wall, live daemon untouched |
| G2 regression | ✅ PASS | Whole-tree FAILED-diff base vs HEAD, identical sharded commands: **net-new reds = 0 — claim VERIFIED** |
| G3 mutations | ✅ PASS | 8/8 legs RED→GREEN ×3 independent executions, restores sha256-identical, tree byte-clean |
| G4 ceremony | ✅ PASS | 7/7 sections; **true pin census = 16 sites / 10 files** (gate expectation "14" was stale, not the lane) |
| G5 unit pack | ✅ PASS | 55/55 (41 tool + 14 registration), rc 0, dual runs identical |
| G6 PACKS.md | ✅ DONE | Rows corrected 34/48→41/55, "5 legs"→8, +2 missing pack rows, misfiled row removed (this commit) |

---

## G1 — FORMAL BOOT-SMOKE GATE: PASS
- **Invocation** (disposable worktree `ensemble-src-wt-nu-gate-smoke` @ `4b8ab274a`, own `.venv`):
  `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_URL (-u PG*/-u DATABASE_URL/-u ENSEMBLE_SELF_ENV) PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=5432 PG_TEST_DB=<seed> PG_TEST_USER=<throwaway role> PG_TEST_PASSWORD=… ENSEMBLE_NOTIFY_SMOKE=1 ENSEMBLE_NOTIFY_SMOKE_UNIT=1 timeout 600 bash test/packs/notify_user_boot_smoke_test.sh`
- **Provisioning:** local scratch PG16 via `sudo -u postgres` — throwaway LOGIN role (CREATEDB) + seed DB; harness self-creates/drops `notify_smoke_<hex>` DBs; both dropped at teardown (0 residue). Remote `ensemble_prod`/`ensemble_dev` never touched.
- **Result:** rc 0, **62s wall** (pytest 51.54s) against the pack's declared 590s internal / 600s outer budget (deviation from the 300s house cap INTENTIONAL — single unsplittable E2E node; actual ≪ budget).
- **Pinned sequence — every assertion green:**
  - scratch TELEGRAM `source_configs` row → real adapter/registry handshake → `running_precondition: "SourceStatus.RUNNING"`
  - **negative case first**: pre-RUNNING probe → typed refusal `notify_source_disabled` ledgered (`refused` row, body_length 17), **zero sends**
  - `source="scheduler"` alarm-shaped wake (the colon-less dispatch source of the original symptom) → Ari turn on the inline mock-LLM seam (no real LLM; cites `test/packs/lcan_helpers/mock_llm.py` as precedent) → emits the `notify_user` tool call → REAL resolution chain + adapter send
  - **Sink received EXACTLY ONE sendMessage** (verbatim JSONL):
    `{"method": "sendMessage", "payload": {"chat_id": "424242", "text": "SMOKE-NOTIFY-CANARY: the 06:00 brief needs your eyes.", "parse_mode": "HTML"}}`
  - `notify_audit`: `refused` + `accepted`→`dispatched` (source_id `scratch-telegram`, body_length 53 == len(SMOKE_MESSAGE)); NO `delivery_unknown`/`delivery_failed`
  - cgroup isolation: in-unit `/proc/self/cgroup` = `0::/system.slice/notify-smoke-41829cd1.service`; outer assert ensemble-main absent; transient unit → `not-found` after teardown
  - **live fence IDENTICAL pre/post** (`systemctl show` only — zero 9797 contact): MainPID 1208616, NRestarts 0, ActiveEnter 2026-10-24 06:48:12Z
  - ports: sink 51995 + ephemeral mock/daemon ports from `free_port()` (re-rolls 9797/7979); daemon ran app-lifespan without binding a socket
- **Evidence bundle:** `/tmp/nu_gate_evidence/` (observations.json, sink_log.jsonl, mock_llm.jsonl, pack log).
- **DEFECT DISCLOSED (unfixed — fix budget exhausted):** the pack's opt-in gate reads the **ambient** env while its smoke flags are set only inside the pytest subprocess `env` — a caller who does not export `ENSEMBLE_NOTIFY_SMOKE=1` + `ENSEMBLE_NOTIFY_SMOKE_UNIT=1` gets `RESULT: SKIP` with **exit 0** (false-PASS shape). This gate's verdict is taken from the literal `RESULT: PASS` + the assertion table, so it is unaffected. Cosmetic: transient unit runs as root (`transient_unit_argv` omits `--uid/--gid`) → root-owned `__pycache__` residue needs `sudo rm` on worktree removal.

## G2 — REGRESSION / NET-NEW: PASS — **net-new reds = 0, VERIFIED**
- **Method:** ONE disposable worktree `ensemble-src-wt-nu-gate-reg`; leg A @ base `7af1d2632d`, leg B @ HEAD `4b8ab274a` (checkout switch mid-tree, `git status --porcelain` asserted empty between legs, `uv sync` per leg). Identical sharded commands both sides: `env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT TMPDIR=<fresh> timeout 300 .venv/bin/pytest --tb=short -q <shard files>` (default addopts — non-integration suite; explicit file lists only; no `-x`).
- **Scale:** ~1549 test files; leg A 20 top-level shards (+21 timeout→54 auto-splits + fix-up shards), leg B 19 + `shard_20_headonly`; **FAILED/ERROR: leg A 300/92, leg B 310/93**; head_only = `55 passed, 1 skipped` (notify pack green; boot-smoke integration-deselected).
- **Raw net-new (`comm -13`): 12 candidates → ALL adjudicated non-net-new:**
  - 11 = pre-existing deterministic reds (a 150-file base batch never ran — resume-seam coverage gap, self-found and CLOSED by mirror-exact re-coverage `legA_fix4`); 3× isolated probes: fail 3/3 at BOTH legs, byte-identical FAILED-id sets
  - 1 = net-new-**flaky** (`tests/unit/tools/test_upgrade_journal.py::test_real_spawn_env_and_process_group_independence`) — batch-red at HEAD once, green 3/3 isolated at BOTH legs → order/load-sensitive, no product signal (disclosed for the owning lane; not quarantined — outside the notify pack surface)
- **Disappeared reds (2):** both adjudicated flakes (dependency_bus 1/3 at HEAD isolation; worker_notification base-batch-only) — NOT fixes claimed.
- **Known-base-red corroboration:** the seal-tripwire family red at BOTH legs — `test_chart_image_delivery_e2e.py::…test_phase_d_does_not_modify_sealed_artifacts` (W1 stale `SEALED_SHA_BASELINES[daemon/constants.py]`; **1 chart-seal row observed, not ~2**) + 4 `test_charter_render_capture.py` rows (same family).
- **Symmetric anomalies (both legs, diff-neutral, disclosed):** `tests/packs/g7_unique_index_smoke_test.py` module-level `sys.exit(0)` INTERNALERROR; `tests/test_prompt_cache_invalidation.py` T12 live-guard hard-abort; 6 attestation files hang (pytest-timeout os._exit) — identical both legs; `tests/performance/test_message_api_cost.py` >300s solo rc=124 both legs; a foreign lane's dev daemon on :8079 died ~18:00Z → shard_11 e2e ran daemon-down symmetrically via `--continue-on-collection-errors` on both legs; 14 tainted base logs quarantined + fully re-covered mirror-exact.
- **Bonus corroboration:** before the tester's redirect arrived, this worker (which had been mis-sent the mutation addendum) executed the mutation pack once in a separate fence-clean disposable worktree: 8/8 RED→GREEN, hash-verified restores, worktree removed — a THIRD independent confirmation of G3.
- **Evidence:** `/tmp/nu-gate-g2/` (328 logs, 8.2 MB; legA/legB failed lists + adjudication logs). Worktree removed, 0 residue.

## G3 — MUTATION RE-EXECUTION: PASS (8/8, ×3 executions)
- **Authoritative leg source:** committed pack `test/packs/notify_user_mutation_legs.sh` (8 `run_leg` snippets + pin node ids); anchors verified `count==1` pre-run by the inventory lane. Executed by parsed-snippet run AND by the committed pack itself (dual), plus the G2 worker's corroborative pack run.
- **Legs:** 1 gate-concurrency (`notify_user.py:303` `_destination_lock`→no-op), 2 cross-instance-shared-cap (`:1045` target_hash+=instance_id), 3 budget-predicate (`notify_audit/repository.py:192` accepted-class filter dropped), 4 body-never-cleartext (`:455` digest+message), 5 doctrine-phrase-pin (`self_alarm.py:576`), 6 config-knob-bound (`daemon/config.py:2819` ge=0→ge=-1000 — the "item-3 leg now lives at daemon/config.py" hint CONFIRMED), 7 D10-timeout-conflation (`:1396`), 8 ledger-write-never-block (`:855` wait_for removed).
- **Per leg:** baseline all-8 GREEN pristine first (9.77s) → planted → RED (verbatim assertion evidence captured per leg) → restored → sha256 pre==post (worker's own + pack's) → GREEN. Final full surface 55 passed (17.01s); `git status --porcelain` EMPTY; worktree removed.
- **Advisory (non-blocking):** leg 3's RED surfaces as an incidental `TypeError` (`result["ok"]` on a string refusal envelope) before its purpose-built assertion renders — mutation-meaningful and deterministic, weak diagnostic; an `assert isinstance(result, dict)` pre-check would improve it.
- **Evidence:** `/tmp/nu-gate-g3/` (EVIDENCE.md, legs.tsv, per-leg RED/GREEN logs, baseline log).

## G4 — REGISTRATION / CEREMONY SPOT-VERIFY: PASS (7/7)
- **Pin census — TRUE COUNT = 16 sites / 10 files** (10 exact-set + 5 count + 1 allow-array trailing pin `test_board_registration.py:249`). The gate expectation "14" and plan W6's "12/9" are BOTH stale; lane prose at `_tool_registry.py:321/:612` already reads **SIXTEEN/TEN** (commit `8921630b9`), tree-wide sweep for stale "13/14" prose = 0 hits. All 10 exact-set literals byte-match the registry (n=16, missing=[] extra=[]). **No lane defect — corrected expectation, carried for the record.**
- **ari-EXACTLY:** `notify-user` in `agents/` → exactly one hit `agents/ari/meta.json:44`; in-body gate `NOTIFY_AUTHORIZED_AGENT="ari"` (`notify_user.py:180`, enforced `:702`); decorator order `@register_tool_category` above `@tool` (`:503-504`); registry wiring complete (PRIVILEGED set n=16 `:617`, DYNAMIC `:150`, KNOWN `:1347`, CATEGORY_MODULES `:1079`); factory extend before help-scan/filter (`instance.py:282/:2572/:6396`).
- **Manifest:** `PROMPT-BASELINE: OK — 161 artifacts` (house `generate_prompt_artifact_baseline.py --check`, system python3.12). Disclosure: base..HEAD manifest delta = **3 rows** — 2 intended re-stamps (ari tools_note/workflow) + 1 BASE-REPAIR (`agents/leader/tools_note.md` was already stale at base; regeneration repaired it; disclosed verbatim in the re-pin block `test_chart_image_delivery_e2e.py:1412-1420`).
- **SEALED rows:** 35 entries — 34 match; exactly 1 stale = `daemon/constants.py` (pre-existing, BASE-attributed: file sha identical at base and HEAD, pin unchanged from base — the lane did not touch it; owning lane owes the re-pin). Our re-pin `agents/ari/workflow.md` == recomputed sha, dated attribution present (block + inline).
- **Migration + N1:** exactly +1 `daemon/migrations/versions/20261025_000001_create_notify_audit.sql` (110 files, all prefixes unique; CREATE TABLE IF NOT EXISTS; TEXT columns; 0 inline `--` comment lines); `git grep notify_audit 7af1d2632d` = **0 hits** (rollback-inert premise holds); NotifyAudit imported before create_all (`repositories/factory.py:88`) AND late-import path (`daemon/api.py:1008-1009`).
- **Doctrine (D12):** all 3 `self_alarm.py` sites reworded; retracted-absolute sweep = 0 hits tree-wide; `## Notify-User` section in ari tools_note (:820); workflow.md patrol AMENDED not deleted; `test_self_alarm_registration.py` phrase-pins updated + new negative pin.

## G5 — UNIT PACK: PASS
- `env -u POSTGRES_* (per-var) timeout 300 bash test/packs/notify_user_unit_test.sh` in disposable worktree @ `4b8ab274a`, own `.venv`: **55 passed (41 tool + 14 registration), 0 failed/errors/deselected, rc 0**, 18.71s + 16.73s dual runs. Wrapper quirk (house-wide, pre-existing): `set -euo pipefail` makes `RESULT: FAIL` unreachable — gate on rc + summary line. Evidence `/tmp/nu-gate-g5/`.

## G6 — PACKS.md: DONE (this commit)
- Corrected `notify_user_unit_test` row: 34→41 tool tests, 48/48→55/55, "5 legs"→"8 legs", gate evidence appended; **removed the misfiled notify row from the watcher-loss (wlts) commission table** (it had been wedged into an unrelated commission's pack table); **added 2 missing rows**: `notify_user_mutation_legs` (8 legs, ×3 executions) and `notify_user_boot_smoke_test` (B1 mechanism + caller-side gate caveat).

## ensure.md (scoped by blast radius)
- Core critical "no regressions in changed packs": PASS directly (all 3 notify packs green). Broader Core items are subsumed by the G2 whole-tree leg-B evidence (net-new = 0 across ~19.5k–24.7k nodes/leg). Release Gate NOT triggered (feature lane, stage-only; no promote from this gate). No contradictions between ensure.md methods and this gate's execution.

## Residuals / disclosures (explicit, none blocking)
1. 🟠 **Boot-smoke pack ambient-gate defect** (SKIP + exit 0 false-PASS shape without caller-side exports) — owning lane's next touch; gate unaffected.
2. 🟡 Boot-smoke transient unit runs as root (no `--uid/--gid`) → root-owned `__pycache__` residue on worktree removal.
3. 🟡 `SEALED_SHA_BASELINES[daemon/constants.py]` stale — BASE-attributed (pre-existing; 1 chart-seal row + 4 charter_render rows red at both legs); owning lane owes re-pin; this lane correctly did not fix it.
4. 🟡 G4 census correction for the record: plan W6 said 12/9, mid-lane prose said 14/9; tree truth = **16/10** and the shipped prose already says SIXTEEN — no action needed on the branch.
5. 🟡 Net-new-flaky disclosed: `test_upgrade_journal.py::test_real_spawn_env_and_process_group_independence` (order/load-sensitive, green 3/3 isolated both legs) + 2 disappeared-red flakes (dependency_bus, worker_notification) — pre-existing flake families for the owning lane, outside the notify surface.
6. 🟡 G3 leg-3 diagnostic-quality advisory (TypeError masks the purpose-built assertion message).
7. Process note: one gate addendum was misdelivered to the G2 worker's instance id (spawn-result ordering ≠ call order); recovered via wake+redirect, zero gate contamination (the worker disclosed its one fence-clean corroborative mutation run; all G2 evidence re-derived under the redirect).

**Fences held across all lanes:** zero port-9797 contact anywhere (G1's fence is systemctl-show-only); per-var POSTGRES scrubs on every invocation; scratch-PG-only; disposable sibling worktrees `ensemble-src-wt-nu-gate-*` all removed; no push of any branch except this evidence branch `tester/notify-user-verification`; `feature/ari-notify-user` never written; main checkout WIP never touched.
