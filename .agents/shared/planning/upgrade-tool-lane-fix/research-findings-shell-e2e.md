# Research Findings — Shell Anchors, e2e Sizing, BSD Fences, House Patterns
**Mission:** v0.15.3 upgrade-tool-lane-fix · **Base:** latest @ 139ba352 (v0.15.2)
**Worktree inspected:** `/home/nea/ensemble-src` — `.git/HEAD` = `ref: refs/heads/feature/upgrade-tool-lane-fix` (verified on-disk; all citations below are from this branch's working tree). No git commands available in this session (read-only file tools only) — HEAD SHA not independently confirmed beyond the HEAD ref file.

---

## Q1. STAGE.SH RIDER — ROLLBACK_SAFE logic, manifest write, gate sites, prior accidents

### Current derivation (stage.sh:163–180)
```bash
# stage.sh:163-171
# ── Schema generation facts (manifest informational fields) ────────────────
KNOWN_SCHEMA_GEN="$(ls "$REPO_ROOT/daemon/migrations/versions" 2>/dev/null | sort | tail -1)"
[ -n "$KNOWN_SCHEMA_GEN" ] || KNOWN_SCHEMA_GEN="unknown"
if grep -liE 'DROP[[:space:]]+TABLE|DROP[[:space:]]+COLUMN' \
     "$REPO_ROOT"/daemon/migrations/versions/*.sql >/dev/null 2>&1; then
    CONTAINS_CONTRACT_PHASE=true
else
    CONTAINS_CONTRACT_PHASE=false
fi
# stage.sh:172-180
ROLLBACK_SAFE="${ENSEMBLE_ROLLBACK_SAFE:-}"
case "$ROLLBACK_SAFE" in
    1|true)  ROLLBACK_SAFE=true ;;
    0|false) ROLLBACK_SAFE=false ;;
    "")
        # derived default (D-FA4.5): destructive migrations ⇒ unsafe to roll back
        if [ "$CONTAINS_CONTRACT_PHASE" = "true" ]; then ROLLBACK_SAFE=false; else ROLLBACK_SAFE=true; fi
        ;;
esac
```
- Env case: `1|true` → true; `0|false` → false; unset (`""`) → **derived**: destructive-migration full-history grep (NOT delta-scoped; 61 migration files match DROP today) ⇒ silently defaults **false** for every real release from this repo.
- Header comment stage.sh:45–46: "ROLLBACK SAFETY DERIVATION (D-FA4.5): `rollback_safe` defaults to the release author's call via ENSEMBLE_ROLLBACK_SAFE={0,1}; when unset it is [derived]".

### Manifest write (stage.sh:277–293)
```bash
cat > "$STAGE_TMP/manifest.json" <<EOF
{
  "version": "$VERSION",
  ...
  "contains_contract_phase": $CONTAINS_CONTRACT_PHASE,
  "rollback_safe": $ROLLBACK_SAFE,        # :284
  ...
}
EOF
```
Final stage log :381 prints `rollback_safe=$ROLLBACK_SAFE known_schema_gen=… contains_contract_phase=…`.

### All rollback_safe consumer/gate sites
| Site | File:line | Semantics |
|---|---|---|
| promote.sh T5 auto-rollback | `promote.sh:299` (section), `:330` `PREV_SAFE="$(manifest_field "$PREV" rollback_safe 2>/dev/null)"`, halt-for-human `:334-336` | Gates ONLY the ROLLBACK target (journal.previous read at step 8b :295-297). NEVER reads the TARGET release's flag. |
| Manual rollback.sh | `rollback.sh:98-101` `SAFE="$(manifest_field "$TO_VERSION" rollback_safe …)"` → refuse + `journal_history_append halt` | Gates the rollback TARGET's manifest. |
| adopt_stale_txn sweep adoption | `lib.sh:1446-1449` `prev_safe="$(manifest_field "$prev" rollback_safe …)"` → halt-for-human, txn left in place | Same D-FA4.5 guard on the sweep-rollback target. |
| Launcher journal sweep | `launcher.sh:783-797` (comment :187) `prev_safe="$(_js_manifest_field …)"` → HALT-FOR-HUMAN, boot proceeds on current | Fourth identical gate (ADR-033 posture). |
| Tool layer (entry-time) | `daemon/tools/upgrade_tools.py` ~:2286-2293 + `_target_release_state` :1345-1370 (per KB source-verified note 2026-09-23 @ a0493ad1; region read confirms tool body at :2271+, armed path continues past :2299) | Emits `manifest-unsafe: target manifest rollback_safe=false (drop-release) — halt-for-human.` — checks ONLY the target manifest at entry. |
| status.sh display | `status.sh:88-90` | Read-only listing. |

### Prior rollback_safe=false accidents (1-line summaries)
- **v0.14.2** (2026-09-25): ACCIDENTAL override omission — zero schema drift vs v0.14.1 (identical known_schema_gen `20260915_212810`), both live+demo copies staged `rollback_safe=false` (staged 2026-09-25T15:27/15:19) → closed the auto-rollback net for the v0.15.0 promote and made manual rollback.sh to v0.14.2 refuse. (Source: KB rollback-safety-chain-state-2026-09-26; consumer semantics in KB v0141-staging-gate-finding-2026-09-24.)
- **v0.15.1** (2026-09-26): staged without override → `rollback_safe=false` as `previous`; critical note records v0.15.2 staging with "rollback net CLOSED (prev v0.15.1 unsafe → gate failure = halt-for-human)" and mitigation "idempotent re-stage v0.15.1 w/ ENSEMBLE_ROLLBACK_SAFE=1 pre-promote"; the v0.15.2 manifest comment "(v0.15.1 accident not repeated)" names the accident class explicitly.
- Background: demo ladder v0.13.9/v0.13.10 also derived false (KB rollback-safe-derivation-demo-inventory); runbook `upgrade-drills.md` §5 warning block documents "every real release derives rollback_safe=false until the derivation is delta-scoped".

---

## Q2. BSD PORTABILITY FENCE — invariants to respect + GNU-debt sites to fence OUT

### Canonical invariants (lib.sh)
1. **Header fence** `lib.sh:36-44`:
   ```
   # Bash 3.2 / BSD tools only (macOS). No flock(1).
   # NOTE on patterns: agent dir names contain glob metacharacters (e.g.
   # tidier[v2]) — every ${var#pattern} / ${var%pattern} string op that
   # interpolates such names MUST quote the interpolated part
   # (${var#*"${key}"}) so it matches literally. Do NOT add `set -f` here:
   # pathname globbing IS used (retention_evict release scan); the quoted-
   # pattern form is the load-bearing fix.
   ```
2. **mv family / atomic flip** — `lib.sh:1147-1171` (atomic_flip, THE portable pattern to copy):
   ```bash
   atomic_flip() {
       local ver="$1"
       local mv_args=""
       case "$(uname -s)" in
           Darwin|*BSD*|*bsd*)  mv_args="-h -f" ;;
           Linux|GNU*|*GNU*)    mv_args="-T -f" ;;
           *) _warn "atomic_flip: unrecognized platform '$(uname -s)' — refusing to flip (fail-closed; BSD or Linux required)"; return 1 ;;
       esac
       ln -sfn "releases/$ver" "$INSTALL_DIR/current.new.$$" || return 1
       # mv_args intentionally word-split (a single string of options)
       # shellcheck disable=SC2086
       if ! mv $mv_args "$INSTALL_DIR/current.new.$$" "$INSTALL_DIR/current"; then
   ```
   Rationale comment :1141-1146: uname-dispatch over `mv -h || mv -T` fallback so a REAL BSD failure isn't masked by a wrong-platform error; unrecognized platforms refuse.
3. **bash 3.2 chained-local splitting** — exemplified at `lib.sh:1148-1149` (`local ver="$1"` then a SEPARATE `local mv_args=""`) and `lib.sh:1251-1252` (`local total=0 name st` + separate `local entries=""`). Never `local a="$1" b="$a/…"` in one statement under bash 3.2.
4. **BSD date flag order** — `lib.sh:708-710`:
   ```bash
   # BSD date: -v adjustments must precede the [-f fmt date] operand
   until="$(date -ju -v+${COOLDOWN_S}S -f '%Y-%m-%dT%H:%M:%SZ' "$(_now_iso)" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" \
       || until="$(_now_iso)"
   ```
   (`-v…` BEFORE `-f fmt value`; trailing adjustments are silently ignored otherwise — KB bsd-date-format-gotcha.)

### GNU-debt sites — OUT OF SCOPE (fence explicitly in plan)
| Site | Exact location | Defect |
|---|---|---|
| `_iso_to_epoch` | `lib.sh:84-89` (BSD-only invocation at :86: `date -ju -f '%Y-%m-%dT%H:%M:%SZ' "$ts" +%s`) | Always fails on GNU → adopt_stale_txn + launcher sweep fail closed; 24h rollback window never rolls over. |
| Cooldown arm | `lib.sh:706-712` (BSD `date -ju -v+${COOLDOWN_S}S -f …` at :709) | On GNU falls back to `cooldown_until=now` → anti-flap inert; post-rollback promotes refused until journal surgery. |
| Retention eviction | `lib.sh:1238-1295` (`retention_evict`, called from `promote.sh:286`) — BSD mtime fallback `stat -f '%m' "$d"` at `:1279` | Garbled + NO-OP on GNU (stat -f fragments leak into release-name var; nothing evicted; conservative over-retention). `RETENTION_KEEP=3` at `lib.sh:69`; protocol-artifact skip list at `:1263-1265` (`rollback.lock.d|.stale.*|.staging.*`); epoch-normalized sort key :1269-1281. |

Fix family for all three (backlog): uname-dispatch like atomic_flip — NOT in this mission.

---

## Q3. E2E SIZING (ensure.md rule + lane sites + per-change classification)

### ensure.md (located: `.agents/tester/rules/ensure.md` — 53 lines, pack-mapped)
Key rules verbatim:
- :5 "Scoped by blast radius: validate only requirements relevant to the change set. Never run the full list unless blast-radius determines the change is big/critical/architecture…"
- :6 "Run as packs: every validation executes as a pack… NEVER as a bare, unbounded `pytest` command."
- :14 "No regressions in changed packs — every pack in the blast-radius change set returns PASS"
- :33+ Release Gate only for "big/critical/architecture changes".

### EXECUTION-LANE rule — verbatim citations
Primary definition (`.agents/tester/RESULTS/2026-09-25-job-pause-resume-tools.md:62-64`):
> "## 4. e2e execution-lane judgment: NOT TRIGGERED
> Judged by EXECUTION-LANE intersection, not file diff (per project convention). Diff `75d7e2d1..1c774667` = `daemon/tools/job_queue.py` (M), `daemon/tools/_tool_registry.py` (M), `agents/leader/meta.json` (M), `tests/unit/tools/test_job_pause_resume_tools.py` (A) — verified by git diff --name-status (jpr-discover). Execution lane = `daemon/services/task_processor.py` claim/dispatch (task_processor.py:267, claim_pending_task :464) and message_job_handler — **zero intersection**"

Lane-probe pack (`.agents/tester/PACKS.md:85`):
> "`lane_gate_boot_probe` (ad-hoc) | scrubbed `./dev.sh` boot, 30s must-not-crash, dev-DB engine-line verification, teardown | execution-lane intersection gate (resume spins message job) + Core #4 grep | ~2 min"

Historical origin (`.agents/shared/planning/self-restart-upgrade-phase2/decisions.md:224`, pre-freeze checklist row 4):
> "PR-time diff-based confirmation against the `ensure.md:44-53` trigger paths (`claim_pending_task`, `turn_transitions`, `reconcile_turn_mirror`, `job_processor`, `job_locks`): if the diff touches any of them, the FULL e2e release gate runs" (cites the pre-2026-09-26 ensure.md shape — superseded by the current 53-line pack-mapped doc).

### Lane sites verified in-tree (branch feature/upgrade-tool-lane-fix)
1. **`daemon/services/task_processor.py:267`** — inside `ProcessMessageProcessor.process_message`: the carve-out content-fetch warning at :266-273 feeding the message-completed notify `notify_work_watchers(... result_summary=carve_result_summary)` at :274-281 (the message-completed lane's notify seam).
2. **"claim_pending_task task_processor.py:464"** — :457-479 is the idempotency-guard comment block; :463-466 describes the re-claim: "`_resume_cascade_db_sync` re-arms the paused `process_message` task (PAUSED→PENDING), so the WorkerPool re-claims it" — i.e., the claim/re-claim lane INSIDE task execution (the repository `claim_pending_task` itself lives in `daemon/repositories/task/repository.py`).
3. **`message_job_handler.py:129`** — **FILE DOES NOT EXIST in this checkout** (`glob **/message_job_handler.py` → none). Precedent for the "equivalent" reading: `.agents/tester/RESULTS/2026-09-21-v0.13.9-phase-d-release-gate.md:59` — "message_job_handler.py:129 absent → equivalent cons[umed]" with the lane satisfied by the message-completed consumer/publisher/sink (`child_reports._process_child_completion_and_notify_parent` :2056 → `_dispatch_post_commit_side_effects` :3919, new gate :4130, failed-terminal publisher :4189-4231). `child_reports.py:1991` comments reference "task_processor / message_job_handler" jointly.

### Per-change lane classification (inputs for the integrator; e2e N/A-vs-required is THEIR call)
| Planned change | Modifies lane modules? | Runs inside lane task execution? | Notes |
|---|---|---|---|
| (i) manager.py executor spawn + async child-exit watcher (Popen/reaper) | NO (manager.py is not a lane module) | ADJACENT, not inside: fires from `drain_pending_system_execution` (:3762) at exact turn-end, `asyncio.shield`-wrapped by `instance_messaging.py:1591` — post-graph completion path AFTER `_graph_tasks` popped (:3765-3767). The reaper task itself is a detached asyncio task. | Closest lane touch = the shielded post-turn finally path; no claim/dispatch/notify code modified. |
| (ii) upgrade_tools.py boot-time + periodic reconcile_pending_op sweep | NO | NO — timer/boot-driven loop (JobLockSweep-style), outside any task execution | New service registration; zero lane files. |
| (iii) argv/env passthrough at spawn seam (manager.py:3801-3823) | NO | NO | Same post-turn context as (i); pure arg/env construction before `spawn_executor`. |
| (iv) pending_actions GC expansion (upgrade_journal.py) | NO (journal-file helpers only) | NO | `_gc_pending_actions` :789-821 is pure in-memory dict pruning + journal_write; callers are store/consume/any new sweep — none in lane modules. |

---

## Q4. TEST PACKS

### Common structure (both packs)
Thin bash wrappers: `set -euo pipefail` → SCRIPT_DIR/PROJECT_DIR resolution → dual-layer timeout (dispatcher `timeout 120s` outer + inner `timeout 110s .venv/bin/pytest <files> --tb=short -q`) → exit mapping `124→TIMEOUT / 0→PASS / else FAIL`. No uv, no unittest — direct `.venv/bin/pytest`.

### test/packs/upgrade_tool_interlock_unit_test.sh (58 lines)
Runs `tests/unit/tools/test_upgrade_journal.py` + `tests/unit/tools/test_upgrade_tools.py`. Pins (header :6-30):
- release_info 1:1 parity vs status.sh on a /tmp fixture written by REAL lib.sh;
- upgrade_status run_id round-trip (armed → in-flight → terminal, same run_id);
- **full refusal-token matrix** (every distinct `reason=<token>`, each its own test) for both actors + read-pair env gates + fail-open reads;
- **LIVE 3-factor gate under a FAKE live marker + /tmp fixture ONLY**: each factor missing alone refuses; spoofed origins stamp no window; fabricated user_confirmed alone refuses; full PASS consumes nonce + arms; replay refused; TTL expiry;
- sequencing: armed tools return with ZERO spawns; banners `RESTART SCHEDULED` / `UPGRADE ARMED`; second arm → pipeline-busy naming active run_id;
- dry_run default TRUE = zero journal mutation;
- journal primitives: kill -9 atomic-write safety, torn detection, BOTH lock stale-break branches, lib.sh interop both directions, ADR-034 splice tolerance;
- **executor spawn: env allowlist ("API-key-class + ENSEMBLE_UPGRADE_LIVE absent"), process-group independence, no-BashProcessRegistry static pin** (:28-29) ← this is the existing STILL-STRIPPED side of the two-sided contract.

Refusal-token assertion style: each distinct reason token = its own pytest test (exact-string refusal returns, not regex) per the pack header; fixtures /tmp-only, live never touched.

### test/packs/upgrade_registration_unit_test.sh (48 lines)
Runs `tests/unit/tools/test_upgrade_registration.py`. Pins: 4-step registration checklist (AST source discovery finds all 4 tools; CATEGORY_MODULES entry; DYNAMIC_TOOL_NAMES + KNOWN_TOOL_NAMES carry the 4; the CRITICAL `create_instance_tools` list-append greppable in source); functional default-deny via REAL `create_instance_tools()` with staged synthetic agents (tools.allow=["system_upgrade"] resolves all 4, without it none — incl. empty-allow); deny-wins; docs paths mirror execution (no system-prompt leak); real meta resolution (ari→4; worker/jober/watcher→none).

### Extending for the TWO-SIDED contract
- Still-stripped side is ALREADY pinned (interlock :28-29: ENSEMBLE_UPGRADE_LIVE absent from executor env). Add: (a) verified-arm tests asserting the CONDITIONED argv/env passthrough DOES carry `--f2-verified-closed` / `ENSEMBLE_UPGRADE_LIVE=1` when the gate factors verify (fake marker + /tmp fixture, same style as the existing LIVE-gate PASS case); (b) refusal-token tests for the new arm-preflight-before-nonce-burn; (c) child-exit watcher tests — **patch the spawn SEAM (`daemon.tools.upgrade_journal.spawn_executor`), never `subprocess.Popen`** (P2.2 testing gotcha: subprocess is a shared module; patching it breaks the test's own subprocess use).

---

## Q5. tests/unit/tools/test_upgrade_registration.py (424 lines)

Covers:
- Static 4-step registration checklist (`TestStaticRegistrationChecklist` :69-98): AST discovery (`discover_source_only_tool_names`), `CATEGORY_MODULES` mapping, `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` membership, source-greppable `create_upgrade_tools` list-append in `daemon/tools/instance.py` (:90-98: asserts `"from .upgrade_tools import create_upgrade_tools"`, `"tools.extend(upgrade_tool_list)"`).
- **PRIVILEGED_TOOL_CATEGORIES exact pin** (:100-117): `== frozenset({"system_upgrade", "system-log", "ens-db"})` — trio as of the 2026-09-16 override (service removed); docstring names the same-PR rule ("pin file 1 of 3").
- Checklist comment-block presence in the module (:119+).
- Re: **classify_user_origin (merge 4db90d74)** — this registration file does NOT cover classification. The registry-backed classification lives in `daemon/tools/upgrade_journal.py:1040-1083+` (`classify_user_origin`; full-string `"api"` + first-segment source_id resolved in sources registry with daemon-controlled chat `source_type`; fail-closed on unregistered/exception). Its HISTORY comment (:1041-1050) documents the superseded static whitelist (ADR-032) and why chat sources could never match. Behavioral classification tests (spoofed origins stamp no window, etc.) live in the interlock pack (test_upgrade_tools/test_upgrade_journal), per the pack header. The manager-side consumer is `manager.stamp_user_origin_window` (manager.py:3692-3754, calls `_uj.classify_user_origin` at :3729).

---

## Q6. HOUSE PATTERNS

### (a) Boot-time + periodic background task registration
- **Periodic sweep pattern (recommended piggyback):** `JobLockSweepService` — `daemon/services/job_lock_sweep.py:1-80`: ALWAYS-ON (no kill-switch, HARD POLICY), default interval 90s via pydantic `ServicesConfig.job_lock_sweep_interval_seconds` `Field(ge=1)` fail-fast at boot, loop exits via `task.cancel()` + CancelledError handler; docstring :61-63 "borrows the asyncio-task + cancel/await lifecycle pattern from `daemon/services/eligible_pending_sweep.py` (A3)".
  Wiring: `daemon/api.py:845-868` — constructed with `interval_seconds=job_lock_sweep_interval`, `.start()`, stored `app.state.job_lock_sweep`; shutdown at `api.py:1864`. `api.py:1847` also cites `tmp_image_cleanup` as a sibling periodic service. → **A new reconcile/GC sweep should register in the api.py lifespan the same way with its own ServicesConfig knob.**
- **Boot-time:** JobProcessor instantiated in the api.py lifespan (`app.state.job_processor`). Shell-side boot backstop = launcher journal sweep (ADR-012) — the daemon-side boot sweep for restart-kind pending_ops is referenced as "boot-sweep fallback" (manager.py:3846) but no daemon-side boot reconcile exists today (the open defect: reconcile is tool-entry-only, upgrade_tools.py:2027 + :2293).

### (b) asyncio subprocess patterns
- **The executor spawn (the seam being modified):** `_uj.spawn_executor` — `daemon/tools/upgrade_journal.py:1012-1037`: sync `subprocess.Popen(argv, stdin=DEVNULL, stdout=log_fh, stderr=STDOUT, cwd=install_dir, env=executor_env(extra_env), start_new_session=True, close_fds=True)`; stdio → `data/upgrade.log` (append); returns `proc.pid`; docstring: "Deliberately NOT registered in BashProcessRegistry… must survive BOTH tool-harness teardown and daemon death." **No `wait()` anywhere — the child exit is invisible (the defect being fixed).** No `asyncio.create_subprocess_exec` in this path — sync Popen + start_new_session is the house convention for daemonized executors (ADR-029, phase2 decisions.md:175-181).
- **Reaper/watcher conventions to copy:**
  - `manager.py:4148-4149`: `await asyncio.wait_for(asyncio.shield(task), timeout=timeout)` — wait-with-timeout that shields the wrapped task from the waiter's cancellation.
  - `instance_messaging.py:1581-1591`: drain of the armed executor is `asyncio.shield`-wrapped at exact turn-end ("asyncio.shield'd so a [failure] leaves the journal pending_op as the fallback").
  - `message_processing_pipeline.py:83,587,850,972-979`: background work = `asyncio.create_task(asyncio.shield(asyncio.to_thread(...)))` — the create_task(shield(...)) wrapping convention (FM-11 context).
  - `instance_lifecycle.py:2306`: `await asyncio.wait_for(asyncio.shield(graph_task), timeout=5.0)` — bounded quiesce wait.

---

## EXTRA ASK 1 — ADR INSERTION POINT

**ADR home: planning decisions.md files, NOT docs/.**
- `.agents/shared/planning/auto-restart-upgrade/decisions.md` (238 lines) = **ADR-001…015** (P1 architect council; header :3 amendments through ADR-015).
- `.agents/shared/planning/self-restart-upgrade-phase2/decisions.md` (321 lines) = **ADR-016…034** — header :6: "continues the numbering of `.agents/shared/planning/auto-restart-upgrade/decisions.md` (ADR-001…015)". Minting rule (:226): "confirm the current max ADR number in this file and mint sequentially above it — never reuse a minted number."

**Confirmed live-rung ADRs (all in phase2 decisions.md unless noted):**
| ADR | Location | One-liner |
|---|---|---|
| ADR-005 | auto-restart-upgrade/decisions.md (P1; constants mirrored lib.sh:54-61) | Rollback window / anti-flap + promote gate chain (300s soak default, `ROLLBACK_CAP_24H=3`, `COOLDOWN_S=600`). |
| ADR-017 | phase2 decisions.md:27-36 (RATIFIED :233) | Env-target permission model — demo/dev/sandbox free, LIVE enforced 3-factor runtime gate (user_confirmed + user-origin window + action-binding nonce), ENSEMBLE_SELF_ENV fail-closed derivation. |
| ADR-021 | phase2 decisions.md:79-87 (user-ruled N=3 :234) | N=3 clean demo cycles before live eligibility; staleness reset on release/manifest change. |
| ADR-028 | phase2 decisions.md:163-171 | Rollback-of-rollback = flip-forward recovery, manual + gated, never automatic. |
| ADR-029 | phase2 decisions.md:175-181 | Daemonized executor (subprocess start_new_session) for restart AND promote; exit-74 deferred; env allowlist R-SR09. |
| ADR-032 | phase2 decisions.md:205-211 | USER_ORIGIN_SOURCES whitelist (SUPERSEDED IN CODE by 4db90d74 registry-backed classify_user_origin — upgrade_journal.py:1041-1083 records the supersession). |
| ADR-033 | phase2 decisions.md:242 | Halt-semantics: halt paths boot-and-continue on degraded current BY DESIGN (all four rollback paths). |
| ADR-034 | phase2 decisions.md:243 | Splice escape-discipline: ≥2-occurrence divergence is hand-edit-only; never tighten to single-occurrence. |

**Recent-entry examples for style:** ADR-029 (:175-181, "minted 2026-08-23, P2.2 Dispatch B" format), ADR-031 (:195-201 PRIVILEGED_TOOL_CATEGORIES opt-in-only).

**Recommendation: mint ADR-035** (highest minted = ADR-034). Insert as a new `## ADR-035 (…): <title>` section in `.agents/shared/planning/self-restart-upgrade-phase2/decisions.md`, placed after the ADR-032 block / before the "Pre-Freeze Assumption-Closure Checklist" section (:215), or appended after the Standing Rulings block (:243) — the file mixes `## ADR-0NN` sections and bullet-style standing rulings; a full `##` section is appropriate for a policy of this weight.

**Format template to quote: ADR-017 (:27-36)** — `Context. → Options. → Decision (rec). → Recommended default / If the user picks otherwise.` The new ADR supersedes **ADR-017's tool-lane enforcement posture for verified arms** — cite ADR-017 explicitly as the policy superseded for the tool lane.

**Required content (verifier verdict fold-in, BOTH halves):**
1. *(i) Verifier-verdict evidence:* Q1 NO — merge 4db90d74 does NOT close F2; the unauth loopback forge lane remains (jobs_crud.py body-source pass-through `daemon/routers/jobs_crud.py:275-278`; messages.py `source="api"` stamp `daemon/routers/messages.py:391`; runbook §9 :495). Corroborated in-tree by upgrade_journal.py:1060-1073: "F2 forging itself remains the separately-fenced pre-existing exposure (executor env allowlist + --f2-verified-closed promote gate), not addressed here." Q2 YES — live is policy-gated user-executed-only: runbook §9 :501 "the live rung remains USER-EXECUTED — automation stops at demo permanently (ADR-017…; promotion-ladder.md S4–S6 are USER rows)"; four-part PRE-LIVE checklist = runbook §8.4(ii) :484 + §9 ledger + f2 gate + user-executed.
2. *(ii) User-ratified supersession (2026-09-26):* user nonce-echo via a registered chat source = F2-equivalent attestation for TOOL-LANE live promotes; the 3-factor arm ceremony = the accepted attestation; rationale "feature-first, revisit if threat model changes"; revisit anchor = ADR-017 (and the F2 fences: executor env allowlist + --f2-verified-closed + ledger §9). The ADR is the policy hook for a future stricter pass — both halves auditable.

## EXTRA ASK 2 — PENDING_ACTIONS GC CURRENT STATE

**Data model** (`daemon/tools/upgrade_journal.py`):
- `PendingAction` dataclass :759-786: run_id, nonce, kind, env, target, issued_at, `ttl_expires_at = iso_plus(now_iso(), NONCE_TTL_S)` (:770), issued_to_instance, consumed_at, consumed_by_message_id.
- Persisted in `$INSTALL_DIR/releases/state.json` under the `pending_actions` MAP keyed by run_id (:829-830). Nonce format `CONFIRM-` + 8 base32 chars (`NONCE_RE` :123; grouped `CONFIRM-XXXX-XXXX` accepted).
- **NONCE_TTL_S = 60 min (per ADR-036, 2026-09-30)** (:116). Expiry = timestamp comparison `now > parse_iso_utc(ttl_expires_at)`.

**Current GC** (`_gc_pending_actions`, :789-821) — quoted in full in the session; semantics:
```python
for run_id, entry in actions.items():
    if run_id == keep_run_id or not isinstance(entry, dict):
        kept[run_id] = entry  # exempt / unknown shape — do not judge it
        continue
    if entry.get("consumed_at"):
        continue  # consumed → single-use spent; audit lives in history
    ttl = parse_iso_utc(entry.get("ttl_expires_at"))
    if ttl is not None and now > ttl:
        continue  # past TTL → un-mintable
    kept[run_id] = entry
```
- **Prunes today:** consumed rows; rows with a PARSEABLE past-TTL `ttl_expires_at`.
- **Keeps today:** unconsumed+unexpired; unknown shapes; UNPARSEABLE/absent ttl (fail-safe: "GC only deletes what it can prove is dead"); the `keep_run_id` exemption (consume_pending_action passes the just-consumed record so replay gets `nonce-already-used`).
- **THE GAP:** GC is **opportunistic, write-triggered only** — called from `store_pending_action` (:831) and `consume_pending_action` (:871) and nowhere else. An expired-unconsumed nonce on a quiet install (no new mints) persists indefinitely. So "expired-unconsumed become prunable" is really "expired-unconsumed become prunable **on a schedule/boot**, not only on the next pending_actions write."

**The stale pending_op (r-20260926-100210-53ca family) is a DIFFERENT row:** the single `pending_op` field (PendingOp dataclass :697-729; `write_pending_op` :732-737). Expiry constants: `PENDING_OP_EXPIRE_RESTART_S = 30*60` (:104), `PENDING_OP_EXPIRE_PROMOTE_S = 10*60` (:105), `RECONCILE_GRACE_S = 10*60` (:106-107). `reconcile_pending_op` (:909-980) closes it ONLY on: (a) a terminal journal event (`_TERMINAL_EVENTS = ("commit", "rollback", "halt", "sweep_rollback", "sweep", "quarantine")` :890) at/after armed_at — note **`nonce_consumed` is NOT a terminal event** (the TERMINAL-while-pending_op-armed defect), or (b) no in_flight + past `expires_at + RECONCILE_GRACE_S`. If `in_flight` is a dict it returns None (:926-927 "promote.sh's txn is live"). **Callers today: upgrade_tools.py:2027 (system_restart entry) + :2293 (system_upgrade entry) — tool-entry ONLY**, hence the ~20-min starvation defect. The sweep expansion must add boot-time + periodic invocation so the live stale row clears once v0.15.3 is live (nobody touches live during this mission).

## EXTRA ASK 3 — E2E SIZING REVISIT (GC expansion)
Touches ONLY `daemon/tools/upgrade_journal.py` (pure journal-file helpers + `journal_write`). Lane modules = `daemon/services/task_processor.py` + the message_job_handler equivalent — **zero intersection**; the sweep runs on a timer/boot path, never inside lane task execution. Also no lane involvement via callers: arm/consume are tool-path, not task-path. **Inputs only — integrator sizes e2e N/A.**

## EXTRA ASK 4 — EXISTING GC SWEEP INTERVAL
**No existing periodic journal GC.** The only daemon periodic sweeps today: `JobLockSweepService` (90s default, api.py:845-868), `eligible_pending_sweep.py` (A3 — the pattern source), `tmp_image_cleanup` (api.py:1847 reference). Shell-side: launcher journal sweep (boot-time only, ADR-012) + watchdog-watcher. **The new pending_actions GC should piggyback the JobLockSweep-style lifespan registration** (own ServicesConfig interval knob, `Field(ge=1)`, start()/cancel lifecycle), or ride the new reconcile sweep's timer if it gets one.

## VERIFIER-VERDICT FOLD-IN B — RESIDUAL OPS GATES (NOT in code scope; plan.md out-of-code-scope / remaining-ops-gates)
1. **Scripts-bundling validation for frozen binaries** (FL-23): spec = runbook `docs/runbooks/upgrade-drills.md` §8.4(i) :482 (stage.sh bundles `scripts/upgrade/` → `releases/<ver>/scripts/upgrade/`; resolution `ENSEMBLE_UPGRADE_SCRIPTS_DIR` → release-local bundle → repo (dev only); `_resolve_scripts_dir` in upgrade_tools.py); checklist §8.4(ii) items (1)-(2) :484 (bundling implementation lands + ONE script-driven demo promote validates the bundled path e2e).
2. **3 fresh ari cycles on the bundled release** — runbook §8.4(ii)(3) :484 ("the qualifying cycles must run on the mechanism being certified"; staleness rule §7); provenance note §8.4(iii) :486 (cycles #1-#3 ran with disclosed `ENSEMBLE_UPGRADE_SCRIPTS_DIR` provisioning; `.env` restored bit-exact with cmp/md5 proofs). **Partial coverage caveat:** tester dev/demo e2e exercises parts of the mechanism but the runbook requires the cycles ON the bundled release specifically.
3. **Enforcement lines:** `scripts/upgrade/ledger_check.py` — `gate()` :277-296: `f2_state == "open"` → BLOCKED **before any cycle-count logic** ("F2-open: the unauthenticated loopback API user-origin forge lane is open — gate hard-blocked regardless of cycle count (runbook §9)"); count ≥ N_REQUIRED(=3, ADR-021) → ELIGIBLE; else NOT-READY. `classify()` :230-274 walks newest→oldest; version change supersedes older cycles. **ledger_check.py does NOT verify bundling or that cycles ran on the bundled mechanism** — those are runbook-checklist/evidence obligations (§8.4(ii)-(iii), decisions.md Gate Rulings & Fences items 1-7), i.e., genuinely ops-side.
4. `--f2-verified-closed` + user-executed-only are in-code (promote.sh:91/103 per runbook :499; `f2-not-verified` refusal token + `journal_mark_f2_verified` stamp) but remain operator-executed factors — listed for completeness.

---
## Session caveats
- Grep tool intermittently returned false "no matches" on single-file paths in this session (dir-scoped greps worked); every load-bearing citation above was verified by direct `read_file` of the cited region, not by grep alone.
- `upgrade_tools.py` manifest-unsafe refusal line numbers (:2286-2293, `_target_release_state` :1345-1370) are from the KB source-verified note @ tree a0493ad1 (2026-09-23); the current tree at :2260-2299 shows the system_upgrade tool body beginning at :2271 with the journal-read region — the armed-path manifest gate sits below :2299 (not re-read; verify exact lines when editing).
