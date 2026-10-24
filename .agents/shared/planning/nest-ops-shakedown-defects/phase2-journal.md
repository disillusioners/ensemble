# Phase 2 journal — DF-4 synchronization gate

Worktree: `/home/nea/ensemble-src-wt-nestops-shakedown` — branch `fix/nest-ops-shakedown-defects`
Phase-1 tip (= `M^1` for the T2.1 merge): `dfed5926f`

This note is the **phase-journal** required by `phase2-plan.md` T2.1 ("preserve our content in the
phase-journal note", "Journal every remediation"). It records the dirty-scratch remediations, the
merge identity, the closure-marker and premise-check outputs (verbatim), the DF-4 pin counts, and
the post-merge authoritative anchor table (T2.4 — authoritative over every line citation in the
plan set and in `architecture-recommendation.md` §10).

---

## 1. Dirty-scratch remediation record (T2.1, house NO-STASH rule — `git stash` never used)

### R1 — `.agents/approver/active.md` (tracked, modified — approver-lane runtime state)

State before the merge: tracked file modified in the working tree (not staged). Its content was the
`nestops-shakedown-defects` approver-lane entry (iteration 003 FINAL, ESCALATED).

**Remediation (per T2.1(1), "take the upstream version at merge and preserve our content in the
phase-journal note"):** our pre-merge content is preserved VERBATIM in §1.1 below; the merge takes
the upstream version of the file.

Our pre-merge content of `.agents/approver/active.md` (verbatim, preserved here because the merge
resolves this path to the upstream version):

```markdown
# Approver Active State

Plan: nest-ops shakedown defect patch (12 defects F1-F12 on the live v0.27.1 instance_read/instance_ops privileged tool surface; 2 class-level design-doc duties + 1 statement-only cleanup note; per-defect pinned mutation-verified regressions; real-PG + real-spawn-order e2e; stage-only release, NO promote. Plan set: worktree /home/nea/ensemble-src-wt-nestops-shakedown/.agents/shared/planning/nest-ops-shakedown-defects/ — plan-overview.md, phase1-phase5-plan.md, decisions.md, technical-analysis-read-side.md, technical-analysis-ops-side.md, research-findings-harness.md, architecture-recommendation.md; branch fix/nest-ops-shakedown-defects)
Slug: nestops-shakedown-defects
Status: ESCALATED
Iteration: 003 (FINAL — max-3 cap reached 2026-10-24)
Started: 2026-10-24T12:56:00Z
Last Verdict: REJECTED (iteration 003, 2026-10-24 — 5 blocking, all narrow: G1 arch §9 check 5 unsatisfiable unscoped marker gate never folded back into the AUTHORITATIVE doc (authority clause resolves conflicts in favor of the broken form); S1 F9 exact-string divergence (pinned final wording exists nowhere); S2 ops-side §3.6:347 half-corrected whole-gate annotation + zero-headroom count-based G-7 discharge check that cannot detect it; S3 F4 truncated empty-page claim contradicts the mandated formula on the reachable overshoot case; S4 AC1.10 names nonexistent docstrings (real surface = 3 test method names). Iteration-001 and iteration-002 blockings all verified remediated. ESCALATED to Leader per max-3. Tracking: .agents/approver/nestops-shakedown-defects-tracking.md)
Mode: Plan Approval — iter 001: 3 section-parallel workers; iter 002: 2 (F6-chain + coherence); iter 003: 2 (gates audit + whole-set sweep). 7 worker dispatches total, all fresh/cold-context.
Note: superseded-from-active: hard-reboot-recovery APPROVED 001 FINAL 2026-10-19 (2/2 workers, 0 blocking; prior chain: resource-cost-control ESCALATED 003 FINAL 2026-10-18, settings-worker-slots 001 2026-10-18, db-backed-settings 002 2026-10-18, live-view-passcode 2026-10-17, image-gen-tool 2026-10-14, agent-base-v2 003 2026-10-13, oauth2-auth D001 2026-10-10, scheduled-tasks 2026-10-01, maintenance-console 2026-09-26, midflight-qa-channel 2026-09-21, clipboard-image-chat 2026-09-19, snapshot-uiux ESCALATED 2026-10-05)
```

### R2 — `.agents/approver/nestops-shakedown-defects-tracking.md`

**No action required.** T2.1(2) assumed the file was untracked; in this tree it was **already
committed by Phase 1** (`git ls-files` → present; `git ls-tree HEAD` → present), so the
same-path-collision hazard it guards against does not exist. Verified before the merge.

---

## 2. Merge identity (T2.1 / AC2.1 / AC2.5)

| Item | Value |
|---|---|
| `origin/latest` at merge time (journaled, NOT pinned to a snapshot) | `074833b45127462a1b7276baa09145681f40a3c4` |
| DF-4 merge SHA (`11c75357f`) — ancestry check | `git merge-base --is-ancestor 11c75357f origin/latest` → **rc=0** (IS an ancestor) |
| Merge form taken | **latest-absorbed** (the ancestry check PASSED — the `fix/df4-provenance-gate` fallback was NOT needed) |
| Merge commit **M** | `cdf348057388662fa2e5c07aebfa08208d8ef764` |
| `git rev-list --parents -n1 M` | `cdf348057388662fa2e5c07aebfa08208d8ef764 dfed5926fbc544752bffc62a49eb601933fffc69 074833b45127462a1b7276baa09145681f40a3c4` |
| **M^1** (our Phase-1 tip) | `dfed5926fbc544752bffc62a49eb601933fffc69` — **== the Phase-1 tip ✓ (D-21 leg-0 attribution intact)** |
| **M^2** (merged lane) | `074833b45127462a1b7276baa09145681f40a3c4` (= the journaled `origin/latest`) |
| base (ladder leg-0 lower bound) | `b257842c9049c9b5c48d2714aa250a859a430e7e` (per D-21) |
| Merge conflicts | **ZERO** — `git diff --name-only --diff-filter=U` empty after the merge |

**Conflict-watch outcome (both intents verified present post-merge):**

| File | Upstream change | Our Phase-1 change | Post-merge state |
|---|---|---|---|
| `daemon/tools/_tool_registry.py` | registry rewrite (+258/-106) | F1 alias `instance_fleet_summary` in the DYNAMIC list + the `KNOWN_TOOL_NAMES` list | **BOTH present** — `instance_fleet_summary` at `:108` (DYNAMIC) and `:1231` (KNOWN). §10 cited `:1180` for the KNOWN hit; upstream's rewrite shifted it to `:1231` — the §10 line cite is superseded, the FACT is not. |
| `tests/unit/test_instance_ops_registration.py` | DF-4's reduced token-mapping pin (+208) | our `READ_TOOL_NAMES` / "all eleven" method renames | **BOTH present** — `READ_TOOL_NAMES` at `:63`, `ALL_TOOL_NAMES` at `:82`, `test_step1_ast_discovery_finds_all_eleven` `:125`, `test_step3_dynamic_tool_names_contains_all_eleven` `:145`, `test_step3b_known_tool_names_contains_all_eleven` `:149`; DF-4's reduced pin at `:480-510` ("EXACTLY ONE provenance refusal token remains", the retired token reconstructed from its two halves — the construction-never-literal discipline). |

`.agents/approver/active.md` merged to the **upstream** version (`unit-secret-exposure`) per remediation R1; our
pre-merge content is preserved verbatim in §1.1.

---

## 3. The three closure markers (T2.2 / AC2.2) — verbatim outputs

### Marker (a) — `SWEEP_NO_USER_ORIGIN_WAKE_SOURCE`

```
$ grep -rn "SWEEP_NO_USER_ORIGIN_WAKE_SOURCE" daemon/
daemon/tools/provenance.py:129:    ``SWEEP_NO_USER_ORIGIN_WAKE_SOURCE`` — DF-4 / P1 — NOT the
daemon/tools/upgrade_tools.py:1263:# internal literal SWEEP_NO_USER_ORIGIN_WAKE_SOURCE (DF-4 / P1 — NOT the
daemon/tools/upgrade_journal.py:1191:SWEEP_NO_USER_ORIGIN_WAKE_SOURCE = "system:sweep_no_user_origin_wake"
daemon/services/upgrade_journal_sweep.py:72:    SWEEP_NO_USER_ORIGIN_WAKE_SOURCE,
daemon/services/upgrade_journal_sweep.py:1217:                    else (wake.source or SWEEP_NO_USER_ORIGIN_WAKE_SOURCE)
daemon/services/upgrade_journal_sweep.py:1748:                                else (head.source or SWEEP_NO_USER_ORIGIN_WAKE_SOURCE)
```

**PASS** — the literal is defined at `upgrade_journal.py:1191`, imported at `upgrade_journal_sweep.py:72`, and
used at **BOTH** sweep call sites `:1217` and `:1748`.

### Marker (b) — the scoped retired-token grep

```
$ git grep -c 'direct-provenance-insufficient' -- daemon/ tests/
rc=1   (no output — ZERO files)
```

**PASS** — the SCOPED form (`-- daemon/ tests/`) returns zero files. (The unscoped form is unsatisfiable by
design: it matches planning prose + `.agents/tester/RESULTS/**` at every rev — arch §9 check 5.)

### Marker (c) — `_protected_gate` body

```
$ (python3: locate def _protected_gate in daemon/tools/instance_ops.py)
def _protected_gate(...) -> str | None:   at line 336, body ends 397
  if confirmed_target_id is None:  -> _refusal(..., "protected-target-confirmation-required", ...)
  if confirmed_target_id != target_id: -> _refusal(..., "confirmed-target-mismatch", ...)
  if not verdict.ok: (line 373)       -> _refusal(..., "no-user-provenance", ...)

verdict.pclass in gate body?     ABSENT (PASS)
refusal-token set in gate body:  ['confirmed-target-mismatch', 'no-user-provenance', 'protected-target-confirmation-required']
EXACTLY the expected three?      True
```

**PASS** — `verdict.pclass` absent from the gate body; the refusal-token set is exactly
{`protected-target-confirmation-required`, `confirmed-target-mismatch`, `no-user-provenance`}.

---

## 4. The SIX F11 premise checks (arch §9) — verbatim outputs

### Check 1 — exactly two `agent:` mint sites (B-1 CORRECTED two-step census)

```
$ git grep -n 'f"agent:{caller_agent_id}"' -- 'daemon/*.py' 'daemon/**/*.py'
daemon/tools/job_queue.py:1319:                f"agent:{caller_agent_id}"
daemon/tools/job_queue.py:2062:                source=f"agent:{caller_agent_id}" if caller_agent_id else "internal_agent:unknown",
rc=0

$ git grep -c 'f"agent:{caller_agent_id}"' -- 'daemon/*.py' 'daemon/**/*.py'
daemon/tools/job_queue.py:2        <-- EXACTLY TWO lines
```

Documented exclusion (arch §9 check 1 — load-bearing, never broaden past it without re-adjudication):

```
$ git grep -n 'f"agent:{agent_id}"' -- 'daemon/*.py' 'daemon/**/*.py' | grep snapshot_executor
daemon/services/snapshot_executor.py:172:        f"agent:{agent_id}",
```

That hit is the **R8 snapshot auto-tag**, NOT a dispatch-source mint. **PASS — exactly two mint sites.**

### Check 2 — HTTP reserved-source gate

```
$ sed -n '525,545p' daemon/routers/jobs_crud.py
    if is_reserved_source(body.source):
        raise HTTPException(
            status_code=422,
            detail=JobValidationError(
                error="Validation Error",
                details=[{ "field": "source",
                    "message": (f"Source '{body.source}' is reserved for "
                                "internal callers and cannot be supplied "
                                f"via HTTP. Reserved origins: "
                                f"{sorted(RESERVED_SOURCE_PREFIXES)}.") }]))

$ grep -n "RESERVED_SOURCE_PREFIXES" daemon/constants.py
946:RESERVED_SOURCE_PREFIXES: frozenset[str] = frozenset({
    ... "system:", "internal_agent:", ..., "agent:", ...
```

**PASS** — a REST job create with `source="agent:…"` is rejected with **422**, and `"agent:"` is a member of
`RESERVED_SOURCE_PREFIXES` (`constants.py:946`).

### Check 3 — message-lane metadata census

Every `enqueue_message(… metadata=…)` call site was enumerated (16 hits). **None is `agent:`-sourced.**

| Call site | source | verdict |
|---|---|---|
| `daemon/manager.py:9696` | the facade (passes the caller's `metadata` through) | passthrough |
| `daemon/services/error_reporting.py:894` | `internal_error_report:{iid}` | not `agent:` |
| `daemon/services/instance_lifecycle.py:469` / `:5970` | `fu.source` (daemon-authored follow-up row) | not `agent:` |
| `daemon/services/long_tool_nudge.py:1471` | `system:long-tool-nudge` | not `agent:` |
| `daemon/services/report_integrity_guard.py:833` | `system:report-integrity-guard` | not `agent:` |
| `daemon/services/upgrade_journal_sweep.py:1203` / `:1731` | `system:post_restart_arm_notify` **or** `system:sweep_no_user_origin_wake` **or** the wake's own source | not `agent:` (DF-4's closure) |
| `daemon/services/waiting_children_watchdog.py:1201/:1249/:1299/:1642` | `system:watchdog` / `system:watchdog:wedge` | not `agent:` |
| `daemon/tools/chart_tools.py:544` | `internal_chart_reuse:{caller}` | not `agent:` |
| `daemon/tools/compare_tools.py:518` | `internal_comparator_reuse:{caller}` | not `agent:` |
| `daemon/tools/instance.py:4424` | `internal_agent:{current_instance_id}` (`task_context` only) | not `agent:` |
| `daemon/tools/job_queue.py:3272` (`job_inject`) | `internal_agent:{caller}` — **no metadata channel** | not `agent:` |

The ONLY `agent:`-sourced enqueue in the tree is `daemon/tools/job_queue.py:2062` (`job_continue`), and it calls
`manager.enqueue_message_job` — the **job seam**, not the message lane — with no metadata channel today.

```
$ grep -rn 'source=f\?"agent:' daemon/
daemon/tools/job_queue.py:2062:                source=f"agent:{caller_agent_id}" if caller_agent_id else "internal_agent:unknown",
```

**PASS** — the message lane carries no `agent:`-sourced metadata today. (This is exactly why the F11 LANE check —
control (b) of the three — is load-bearing once F11 lands.)

### Check 4 — remaining direct-window mint census

```
$ git grep -n 'source="api"' -- daemon/services/job_queue_service.py daemon/routers/jobs_crud.py
daemon/services/job_queue_service.py:2608:                    source="api",
rc=0
```

**DISCLOSED RESIDUAL (not a silent non-goal)** — the DF-5 defer-resend at `job_queue_service.py:2608` re-enqueues
with `source="api"`, which grants the child a **DIRECT** window — MORE authority than any delegation chain, with
no chain. Out of F11's scope (DF-5 owns it); recorded here per arch §9 check 4 and to be disclosed with an owner
in Phase 3's report.

### Check 5 — scoped marker count

Covered in §3 marker (b): **0 files**. **PASS.**

### Check 6 — real-PG pin run WITH COUNTS

```
$ env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_URL \
    -u DATABASE_URL_POSTGRES PG_TEST_HOST=127.0.0.1 PG_TEST_PORT=5432 \
    PG_TEST_DB=nestops_df4_scratch_test PG_TEST_USER=nestops_df4_scratch_role PG_TEST_PASSWORD=*** \
    ./.venv/bin/python -m pytest tests/postgres/test_df4_provenance_closure.py \
        tests/postgres/test_df4_ari_fallback_closure.py --override-ini="addopts=" -m postgres -p no:randomly -q

4 passed, 217 warnings in 32.21s
```

**PASS with a NONZERO count (4 passed)** — this is the false-green guard: on SQLite the postgres-marked files
collect **0 tests**. The DB was a throwaway scratch database owned by a throwaway LOGIN role on
`127.0.0.1:5432` (never `ensemble_prod`, never `:9797`), with the ambient `POSTGRES_*` scrubbed **per variable**
(the `env -u POSTGRES_*` glob form is a silent no-op — critical note).

---

## 5. DF-4's pin set + the Phase-1 suites, post-merge (T2.3 / T2.5 / AC2.3 / AC2.5)

| Command (all `-p no:randomly`, no `-x`, no whole-suite sweep) | Result |
|---|---|
| `pytest tests/postgres/test_df4_provenance_closure.py tests/postgres/test_df4_ari_fallback_closure.py --override-ini="addopts=" -m postgres` (real PG) | **4 passed** |
| `pytest tests/unit/tools/test_df4_sweep_reserved_fallback.py tests/unit/test_instance_ops_registration.py` | **43 passed** |
| `pytest tests/unit/test_instance_ops_behaviour.py tests/unit/test_instance_read_shakedown_defects.py` | **108 passed** |
| `pytest tests/unit/tools/test_frozen_tool_name_discovery.py tests/unit/test_pause_never_dispatched_ghosts.py tests/unit/test_instance_ops_prompt_layer.py` | **18 passed** |

The DF-4 renamed genuine-path pins (`test_genuine_direct_window_admits_protected_act`,
`test_genuine_inherited_window_admits_protected_act`) live in `tests/unit/test_instance_ops_behaviour.py:319` /
`:365` — inside the 108-passed run. The registry tripwire (`test_frozen_tool_name_discovery.py`) is green — the
merged lane's registry rewrite and our F1 alias insertions did not drift the name set.

**CORRECTED net-new ladder (D-21, arch §11.1) — leg labels derived at run time from the recorded parents:**

| Leg | Range | Owner |
|---|---|---|
| leg 0 | base `b257842c9` → M^1 `dfed5926f` | **OURS** (Phase 1) |
| leg A | M^1 `dfed5926f` → M `cdf34805` | **MERGED LANES + conflict resolution** (runs `test_frozen_tool_name_discovery.py` as the merged-lane registry tripwire) |
| leg B | M `cdf34805` → HEAD | **OURS** (Phase 3/4) |
| containment | base `b257842c9` → HEAD | whole-patch |

The retired "base vs M = merged lanes" attribution is **not** used (base→M contains OUR Phase-1 commits).

---

## 6. T2.4 — post-merge authoritative anchor table (supersedes every line cite in the plan set)

Re-grepped against the MERGED tree at `cdf34805`. **This table is authoritative over every doc citation in both
directions** (arch §10 and every plan/ops-side line cite).

| Block | Plan / §10 cite | **POST-MERGE (authoritative)** | Δ vs §10 |
|---|---|---|---|
| `instance_ops.py` `_protected_gate` | `:336-385` | **def `:336`**, body `:336-397`; `not verdict.ok` `:373` | body end +12 (the cite is the gate's own lines) |
| `instance_ops.py` `_is_protected_target` | `:297-~323` | **`:297`** | 0 |
| `instance_ops.py` `_refusal` | `:199-201` | **`:199`** | 0 |
| `instance_ops.py` module doc gate order | `:41-52` | **`:41-52`** | 0 |
| `instance_ops.py` `CATEGORY_DOC` | `:108-130` | **`:108-130`** | 0 |
| `instance_ops.py` terminate guards 1–7 | `:497-555` | **`:497-555`** (`instance_terminate` def `:497`) | 0 |
| `instance_ops.py` protected determination + gate call | `:557-583` | **`:557-583`** (`_protected_gate(` `:562`, `write_refusal` `:569`) | 0 |
| `instance_ops.py` budget / cascade | `:586-642` / `:644-679` | **`:586-642` / `:644-679`** | 0 |
| `instance_ops.py` `dry_run` fork / preview builder | `:696-737` | **`:696-737`** (`if dry_run:` `:696`, `write_preview(` `:699`) | 0 |
| `instance_ops.py` terminate `_full_doc_` | `:798-851` | **`:798-851`** | 0 |
| `instance_ops.py` reclaim / refusal / `_full_doc_` | `:893-987` / `:916-926` / `:989-1014` | **`:893-987`** (`instance_reclaim` def `:893`) / **`:916-926`** (`target-not-terminal` `:920`) / **`:989-1014`** | 0 |
| `instance_ops.py` nudge / `_full_doc_` | `:1046-1135` / `:1137-1155` | **`:1046-1135`** (`instance_nudge` def `:1046`) / **`:1137-1155`** | 0 |
| `instance_ops.py` mark / `already_protected` / marker token | `:1197-1364` / `:1230-1233` / `:1237` | **`:1197-1364`** (`instance_mark` def `:1197`) / **`:1230-1234`** / **`:1237`** | 0 |
| `instance_ops.py` park Tier-2 / Tier-1 audit writes | `:1279-1313` / `:1337-1348` | **`:1279-1313`** (`settle_audit(` `:1294`) / **`:1337-1348`** (`write_audit_and_act(` `:1337`) | 0 |
| `manager.py` `stamp_user_origin_window` | `:4891-4953` (clear `:4947`) | **`:4891`**; **clear branch `:4947`** (`self._user_origin_windows.pop(instance_id, None)`) | 0 |
| `manager.py` `_user_origin_last_stamp` | `:4930-4938` | **`:4930`** | 0 |
| `manager.py` `_inherit_provenance_window` | `:4955-5056` | **def `:4955`**; cap check `:5026` | 0 |
| `manager.py` depth-cap const | `:231` | **`:231`** (`_INHERITED_PROVENANCE_MAX_DEPTH: int = 4`) | 0 |
| `manager.py` funnel `_process_message_with_tracking` | `:9758/:9824/:9832` | **`:9758`** / **`:9824`** (stamp) / **`:9832`** (inherit) | 0 |
| `manager.py` other direct funnel caller | `:13263` | **`:13263`** | 0 |
| `manager.py` `enqueue_message_job` | `:2695` (instance_messaging) | **`:9710`** (manager facade) / **`:2695`** (`instance_messaging.py`) | 0 |
| `upgrade_journal.py` `classify_user_origin` | `:3390-3440` | **`:3390`** | 0 |
| `upgrade_journal.py` `SWEEP_NO_USER_ORIGIN_WAKE_SOURCE` | `:1191` | **`:1191`** | 0 |
| `upgrade_tools.py` factor-2 read / restart gate | `:3323` / `:2725` | **restart gate `:2725`** | 0 |
| `job_processor.py` dispatch sites | `:1176/:1268/:1548` | **`:1176` / `:1268` / `:1548`** | 0 |
| `job_queue_service.py` retry enqueue | `:2824` | **`:2824`** (`new_job = await self.enqueue(`) | 0 |
| `job_queue_service.py` `enqueue` def | `:1481` / `:1607` (create sites) | **`enqueue` def `:1344`** | 0 (create sites are inside the def) |
| `job_queue.py` (1319, 1422, 2056, 2062, 3272) | as cited | **`:1319` / `:1422` / `:2056` / `:2062` / `:3272`** | 0 |
| `task_processor.py` | `:496/:506/:555` | **`message_metadata` `:506` / `ProcessingContext(` `:555`** | 0 |
| `message_processing_pipeline.py` | `:119-175/:419-450` | **class `:120`** (`is_fresh_episode_user_message` `:175`) / **`_do_process` `:419`** | 0 |
| `instance_messaging.py` | `:400/:2695/:3059` | **`:2695`** (`enqueue_message_job`) / **`:3059`** (`job_queue_service.enqueue(`) / **`message_metadata=metadata` `:2206`** | 0 |
| `daemon/tools/_tool_registry.py` (F1 alias) | DYNAMIC `:108` / KNOWN `:1180` | **DYNAMIC `:108` / KNOWN `:1231`** | KNOWN **+51** (upstream's registry rewrite) — cite superseded |
| `tests/unit/test_instance_ops_registration.py` | `:316-328` | **`:480-510`** (DF-4's reduced token pin) | **+164** — cite superseded |

**Every other anchor in §10 held EXACTLY** — the merged lane's changes landed in other files, so no ops-side
anchor shifted. The only two cites superseded are the `_tool_registry.py` KNOWN-list line and the
`test_instance_ops_registration.py` token-pin line, both shifted by upstream's own rewrite.
