# Plan Overview: kv-ambient-awareness-fix

Date: 2026-09-07 (revision pass 2026-09-08 — reviewer REJECT → revision route: C1 re-adjudication D12, anchor refresh D13, Pause-First runbook, W3-W11 + S12/13/14/16/17/19 folds)
Author: synthesis worker via plan-creation (capstone synthesis of three defect plans); revision: plan worker per dispatcher adjudication
Status: Draft — revised per review (one revision pass; no re-planning of C0/C2/C3 designs)
Companion artifact: `decisions.md` (D1-D15 — tie-break adjudications with evidence citations; D12 = the C1 re-adjudication, D13 = anchor/drift refresh record)

Inputs (primary sources, read end-to-end; frozen at adjudication — **revised in place 2026-09-08** per the dispatcher's revision dispatch; the revision is surgical: errata/D-entries and the phase2 re-scope, no re-planning of C0/C2/C3 designs):
- `phase1-plan.md` — DEFECT 1 cadence + §Shared prerequisite stable-id scheme (W3/W4/W5 + S14/S19/S16 folds applied)
- `phase2-plan.md` — DEFECT 2 spawned-child mispartition (**re-framed per D12: FIXED AT BASE → C1′ verify-and-pin**)
- `phase3-plan.md` — DEFECT 3 system-default suppression removal (W6/W7/W10 + S12/S13 folds applied)

All three plans anchor at worktree `2750c815`; code anchors re-verified in the recovery worktree `agents-ensemble-wt-approver` at that commit during synthesis (context_messages.py:85-111, :1315-1360; instance_messaging.py:3573-3660; constants.py:594-624 + repo-wide flag-name collision grep). **Revision note (D13):** anchors were re-verified by grep at `9926bca0` and refreshed where drifted — the drift set is now 7 commits (`2750c815..9eebf3ff`), superseding the 3-commit injected-notes list; DEFECT 2's fix is landed at base (80bb61dd). Phase files use **defect numbering**; this overview uses the canonical mapping of D9 (decisions.md).

---

## Objective

Make ambient `shared_meta_kv` metadata reliable for every agent turn and every tree position: the `[SYSTEM CONTEXT]` KV block binds to the correct tree-root partition (D2), renders for system-default projects too (D3), and refreshes with fresh data on every non-retry turn under a stable, supersede-safe message id (D1 + shared prerequisite). After the fix, governor `council_manifest`, project-manager `pm_leader_instances`, and the five worktree-aware agents get correct ambient KV as a reliable bonus, while explicit `context=` hand-offs and explicit KV reads remain the primary channel.

Testable completion sentence: *On `latest` post-deploy, a spawned default-project child's first turn carries a populated tree-root-bound KV block, and an external KV write is visible in that block on the child's next non-retry turn — each reversible per-defect via one env var + restart.*

---

## Background & Defect Summary

Shared context (explorer-verified, citations in phase files): all three defects sit on one read path — the `[SYSTEM CONTEXT: Related Project]` HumanMessage assembled once per instance by `assemble_context_messages` (context_messages.py:1134) at the injection seam (instance_messaging.py — re-anchored: gate `:3637`, threaded block `:3671-3686`; D13). **Two** independently-switchable kill-switches (D5/D8) cover the remaining fixes — DEFECT 2 is FIXED AT BASE (80bb61dd) and needs none (D12); one branch carries them in dependency order (D2-here = decisions.md D2).

**Migration record (D13):** `instances.parent_id` exists since migration `daemon/migrations/versions/20260402_000001_rename_session_to_instance.sql` (2026-04-02; `ix_instances_parent_id` at :225) — the landed fix reads a long-established column; no migration risk.

| Defect | Root cause (file:line @ 2750c815) | Fix approach | Kill-switch (shape, default) | Details |
|---|---|---|---|---|
| **D1 — snapshot cadence** (KV stale after turn 1) | `project_already_injected` short-circuit returns `[]` on turn 2+ (context_messages.py:1270-1314); block built once via `build_project_context_message` → `_fetch_kv_metadata` (context_messages.py:411, :964-997); `_make_context_message` mints `uuid4` per call (context_messages.py:85-111) so re-emits append, never supersede; `is_retry` gate at instance_messaging.py:3637 (re-anchored; D13) is correct-by-design | Split the block: stable project block (turn 1, `project:{instance_id}`) + KV block refreshed every non-retry turn via the unified host (D4/D7) under `kv:{context_key}`; `_make_context_message` gains optional `id_=` + `_stable_id_for` helper (C0 prereq) | `ENSEMBLE_AMBIENT_KV_FRESH` (Shape B, ON; `=0` → cadence-only reversion: turn-1 block present, never refreshed — W3) | phase1-plan.md:41-108 (root cause + seam table), :112-175 (prereq), :178-264 (fix), :353-484 (tests) |
| **D2 — spawned-child mispartition** (child reads own empty KV partition) | Hardcode `_persistent_parent_id: str \| None = None` at instance_messaging.py:3620 (historical, @ 2750c815); misleading comment at :3609-3619 described an unreachable resolution; `parent_id=None` → `_resolve_tree_root_id` returns child's own id (context_messages.py:949-950) → `_fetch_kv_metadata` reads the child's empty partition; graph-side correct build discarded by design (anti-double-inject, do NOT un-discard) | **FIXED AT BASE by 80bb61dd** (merged via 36a46b01): threads `_proj_row.parent_id` from the already-fetched row (landed block: instance_messaging.py:3671-3686; call `:3695`), zero extra round-trips, exception-safe. C1′ = VERIFY-AND-PIN (tests-only; D12): coverage audit + gap-closing pins | **NONE — flag RETIRED (D12).** `ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT` struck from C0's registry pre-reservation and every flag table (avoid reserved-unused per B.S.8) | phase2-plan.md (re-framed: root cause kept as accurate history; design-of-record = the landed diff), :43-131 (root cause), tests/coverage-audit section |
| **D3 — system-default suppression** (default-project trees get zero KV) | Unconditional skip `if not is_system_default:` around the KV fetch (context_messages.py:1339-1345); only KV renderer is `_format_kv_metadata_section` inside `build_project_context_message` (context_messages.py:460) — the scope-guide branch (:1347-1352) has no KV host, so a gate-flip alone is insufficient | New standalone host `build_shared_meta_kv_message` + `CONTEXT_KIND_SHARED_META_KV` enum; gate the fetch + host append on the new flag; empty-partition skip; flip the wrong-behavior pin `test_kv_metadata_not_fetched_for_system_default` (tests/unit/test_context_messages.py:1199-1233) | `ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED` (Shape A, ON; `=0` → byte-identical pre-fix suppression) | phase3-plan.md:53-100 (root cause), :104-213 (host + gate), :269-392 (tests incl. the flip) |

Consumers improved (explorer-verified): governor `council_manifest` visibility (incl. default-project trees — phase3-plan.md:516), project-manager `pm_leader_instances`, five worktree-aware agents as a reliability bonus. No-regression surface: tool tree-root reads (shared_meta_kv_tools.py:109-122), `context=` enqueue-only forcing (daemon/tools/instance.py:2726-2759), governor council_manifest explicit-read restore (prompt-level), prompt byte-identity fences, obsolete-schema zero-hit sweeps.

---

## Canonical Sequencing (D9 mapping + dependency edges)

Ratified unchanged (all three plans converged independently: phase1-plan.md:575, phase2-plan.md:455, phase3-plan.md:493-505); rationale and rejected alternatives in decisions.md **D1**.

| Impl step | Commit | Defect # | Artifact | Dependency edges (why here) |
|---|---|---|---|---|
| 0 | C0 | — (shared prerequisite) | phase1-plan.md §Shared prerequisite (:112-175) | Unblocks C1′-C3: without deterministic ids, re-emits APPEND (grow checkpoints) and id-less messages break MessageTapSlot + hit the moving-timestamp fallback (persistence.py:527-528) |
| 1 | C1′ | DEFECT 2 | phase2-plan.md | Tests-only verify-and-pin (D12 — fix landed at base via 80bb61dd). Blocks on C0 (the partition pins ride the stable-id contract). Must precede C2: C1′ is the VERIFY GATE — the correct-partition contract is pinned before C2 builds on it (suppression binding to a wrong partition key is silently untestable — phase3-plan.md:460 Risk 5) |
| 2 | C2 | DEFECT 3 | phase3-plan.md | Blocks on C0 + C1′ (phase3-plan.md:509-510). Must precede C3: refreshing a suppressed block is a no-op for default-project trees (phase3-plan.md:515) |
| 3 | C3 | DEFECT 1 | phase1-plan.md body (:178-264) | Blocks on C0 + C1′ + C2 + the unified emission path (decisions.md D4): the refresh builder and the D3 host are ONE builder with composed flags; the correct-partition contract pinned by C1′ is what makes "fresh" mean "fresh from the RIGHT partition" (S12 fix: C1′ was omitted from this row) |

Risk/dependency note (updated by D12): the order remains cheapest-first — C1′ is now the cheapest step of all (tests-only against a landed fix), C2 is one builder + gate + config field, C3 is the split refactor — which front-loads the highest-confidence win while branch-fresh against the 7-commit drift (D13).

---

## Branch Strategy: ONE branch, sequenced commits

**Decision: one branch** — `feature/kv-ambient-awareness-fix` off `origin/latest`, in a NEW worktree (NOT `2750c815` — read-only context; MAIN checkout externally owned). Full rationale + rejected alternatives in decisions.md **D2**; commit sequence:

```
C0  prereq(defect-1-plan §Shared prerequisite): _stable_id_for + id_= kwarg
    + the TWO surviving flag names RESERVED in constants.py:594-624
    [no behavior change; ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT RETIRED — D12]
C1′ verify-and-pin(DEFECT 2): tests-only — coverage audit + gap-closing pins
    against the fix LANDED at base (80bb61dd); no daemon code, no flag [D12]
C2  fix(D3): unified standalone KV host + system-default gate + kill-switch + tests
C3  fix(D1): split block + per-turn refresh via the same builder + kill-switch + tests
```

One-line rationale: all three fixes share one builder seam (context_messages.py:1319-1360) and one injection seam (instance_messaging.py `:3637-3760`, D13), so parallel branches serialize at merge with triple conflict surface — while the two independent kill-switches already provide per-defect revert on the live daemon for C2/C3 (faster than `git revert`, no redeploy; DEFECT 2 needs no revert — it is landed, verified, pinned), and the anchor drift (7 commits, `2750c815..9eebf3ff` — D13) is re-pinned exactly once at kickoff.

---

## Rollout

1. **Anchor re-pin (mandatory, kickoff).** Branch from `origin/latest` in a new worktree; re-grep every anchor in the three phase files against the drifted file set — **7 commits, `2750c815..9eebf3ff`** (D13 — supersedes the stale 3-commit injected-notes list): `d348ad4e` (tidier doc-truth/comment fixes), `80bb61dd` (the DEFECT 2 fix), `d6e30d9d` + `7a899517` + `e321bdb3` + `f965345a` + `53baef57` (LCA arc: judge punch-list, inline LLM report judge, enqueue-lane stamping, nudge embeds, conditional attestation). If semantic intent shifted, halt and route to planner (phase3-plan.md:465 Risk 10). Record actual final line numbers in the PR description.
2. **Implement** C0 → C1′ → C2 → C3 per the table above; each commit carries its own tests (phase-internal orders: phase2-plan.md test-strategy section, phase3-plan.md:412-423, phase1-plan.md:499-515).
3. **Worktree-Based Regression Proof** per defect: each phase's new tests must FAIL at the pre-fix commit with the exact defect symptom, then PASS post-fix (phase1-plan.md:369-424, phase2-plan.md bug-exercising proof — landed in `test_fix_reverted_child_mispartitions_to_own_id`, phase3-plan.md:390-392). DB recipe for write surfaces: file-backed SQLite `tmp_path` + NullPool + WAL + busy_timeout (NOT StaticPool — phase2-plan.md:358-362, phase1-plan.md:443-458).
4. **Merge → deploy → restart-to-flip.** Both surviving flags default ON (D8); restart activates. Boot-log verification (one grep-able line per flag):
   - `Ambient KV freshness ENABLED (per-turn fresh)` (phase1-plan.md:335-341, :518)
   - `[ContextMessages] kv_ambient_system_default_enabled=True` (phase3-plan.md:253-258, :431)
   - **Emit-at-boot requirement (S13, adapted):** BOTH lines must be emitted at BOOT (Shape B via the `manager.py` wire-up, phase1-plan.md:345; Shape A at config-resolution time, phase3-plan.md:251). A lazy first-call resolver emit makes quiet-daemon boot-log grep false-fail (no traffic since restart → line never printed → operator misreads the flag as OFF). Reviewer gate: the C2/C3 diffs must wire the boot-log emission at boot, not lazily inside the per-turn resolver.
5. **Post-deploy checks.**
   - Spawn leader → child via `send_message`; child's first-turn checkpoint block contains the leader's KV metadata, not an empty block (phase2-plan.md post-deploy smoke; now a PIN — C1′'s real-service partition test).
   - External KV write via `shared_meta_kv` tool → next non-retry turn reflects the update (phase1-plan.md:520).
   - Default-project instance (PM session / council): `GET /messages` shows scope guide AND `Shared Meta KV` blocks when partition non-empty (phase3-plan.md:432).
   - Legacy-instance block hygiene (W8/D14): inspect one pre-deploy instance's context surface — at most ONE project block + at most ONE kv block per kind (pinned by `test_legacy_instance_no_double_project_block`).
   - Explorer-consumer observation (S17, out of scope for the plan): post-deploy, observe the ORDERING of injected blocks as seen by explorer-class consumers (the shared-context injection landed separately); this plan does not change injection ordering — note-only watch item.
   - **FE two-block check (owner: frontend, gate before ship)** — see risk register R1, the W9 acceptance spec below, and decisions.md **D10**.

### FE two-block acceptance spec (W9 — concretizes R1/D10)

Per-mode acceptance, run on staging before ship (owner: frontend; `GET /messages` is the read surface — the API re-runs assembly live at persistence.py `:905/:920-927`, so the FE sees the new block on deploy):

| Mode | Expected `[SYSTEM CONTEXT]` blocks (card count from `GET /messages`) | Content assertions |
|---|---|---|
| Default-project instance (partition NON-empty) | Scope Guide card + `Shared Meta KV` card = **2** context cards (project-JSON card absent by design) | Scope guide is first (`context_kind=project_scope_guide` at index < `shared_meta_kv`); KV card contains the written sentinel key/value; NO duplicate cards with identical `context_kind` |
| Default-project instance (partition EMPTY) | Scope Guide card only = **1** (identical to pre-fix) | No `shared_meta_kv` card, no empty-header card |
| Normal-project instance | Related Project card (project JSON, NO inline KV section after C3) + `Shared Meta KV` card = **2** | Project card renders project JSON/critical notes/history; KV card renders the same partition data as the tool path returns |
| Either mode, `context_kind` filter | Dedup/grouping must key on `context_kind` (+ message id), never on the `[SYSTEM CONTEXT]` title prefix | FE groups correctly when two cards share the prefix |

**Rollback criterion (which observation triggers the `=0` flip):** flip the offending flag `=0` + restart if EITHER (a) any instance's transcript renders duplicate/blank `[SYSTEM CONTEXT]` cards that persist across reload (merge-safe but presentation-broken), or (b) a default-project prompt measurably degrades (consumer confusion attributed to the new KV card). (a) alone is note-and-fix-frontend severity; (b) is flip severity. Single-restart revert; D4/D8.

**Synthetic-id enumerate-order pin (W9).** `persistence.py:937-949` assigns `synthetic-context-{context_kind}-{instance_id}-{idx}` ids during `GET /messages` enumeration (`for idx, msg in enumerate(context_msgs)`; id mint at `:949`). Pin: for a fixed message list, the `idx` suffix MUST match the message's position in the returned array (enumeration order = array order; no re-sort between enumerate and return), because the FE merge layer keys on these ids. New pin test asserted in C2's suite.



### Deploy runbook addendum — Pause-First Then Quiesce (CRITICAL 2)

Restarting the daemon with new ambient-rendering flags is a quiescence-requiring operation. Follow the repo's Pause-First Then Quiesce convention (mirroring `WatchoverService.activate_watchover`, the first proven consumer):

```
1. pause_instance_cascade            FIRST — pause target instances before any state change
2. bounded quiescence confirmation   wait (bounded) for in-flight tasks to reach the pause-cancelled/
                                     checkpointed boundary — do NOT proceed on an unconfirmed window
3. restart the daemon                ./scripts/upgrade/restart.sh (flags land with the new binary/code)
4. boot-log verification             grep BOTH remaining kill-switches in data/logs/ensemble.log:
                                       "Ambient KV freshness ENABLED"          (ENSEMBLE_AMBIENT_KV_FRESH)
                                       "kv_ambient_system_default_enabled="    (ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED)
                                     values must match the intended .env; both lines must be PRESENT
                                     (emit-at-boot requirement above — absence is a verification FAILURE)
5. resume cascade                    resume the paused instances (DB-only PAUSED → RUNNING)
```

**Operator choice at deploy time (no preference encoded — operator decides):**

| Option | Steps | Trade-off |
|---|---|---|
| (i) **Default-ON direct** | One restart with both flags ON (unset in `.env`); D8's rationale stays valid (behavior-BUG fix class) | Single restart, no flip step to forget; full new behavior active immediately; blast radius = the live daemon from the first turn |
| (ii) **Staged default-OFF-first flip** | Restart #1 with BOTH flags `=0` in `.env` → verify boot logs show DISABLED/False + run the post-deploy smoke checks → restart #2 with flags removed/ON | Bounds the blast radius on the live daemon (ambient surface verified in OFF state first); costs a second restart and re-introduces the WC-wake-style "do not forget the flip" hazard (D8) |

Both options pass through the identical Pause-First sequence above; (ii) simply runs the boot-log verification twice (once per flag state).

**Kill-switch revert paths** (any defect, any time): set the flag `=0` in `.env` → `./scripts/upgrade/restart.sh` → verify the boot log line shows DISABLED/False. OFF semantics are byte/value-identical pins in each flag's test suite (phase3-plan.md:310-322; phase1-plan.md:414-423 — OFF = cadence-only reversion per W3/D6: turn-1 block still present, never refreshed). DEFECT 2 has no revert path (no flag — D12). Combined-state semantics (which blocks render/refresh under which flag pair) are the D4 composition table (decisions.md), with named per-cell tests (W7).

---

## Interaction With Shipped Worktree-Aware Prompts (verdict)

**Verdict: immune by design; ambient becomes a reliable bonus; worktree-aware coordination stays PRIMARY; no agent prompt may describe this mechanism.**

- The worktree-aware agent coordination (merged `192dee4e`) uses **explicit `context=` hand-off + explicit KV reads** (daemon/tools/instance.py:2726-2759 enqueue-only forcing; shared_meta_kv_tools.py:109-122 tree-root reads). Those channels never depended on the ambient block, so all three defect fixes cannot regress them — they are pinned as no-regression surfaces in every phase (phase1-plan.md:33, phase2-plan.md:38, phase3-plan.md:47, :378-384).
- This initiative is a **daemon-only diff**: zero edits under `agents/{name}/` (`git diff agents/` must be empty — success criteria in all three phases: phase1-plan.md:564, phase2-plan.md:431, phase3-plan.md:484). The prompt byte-identity fences (`.agents/shared/planning/worktree-aware-prompts/verification-summary.md:24-34`) constrain prompt files only; they are untouched by construction and verified by that empty diff.
- Reliability upgrade, not a contract change: where the worktree-aware prompts today treat ambient KV as possibly-stale (the exact hazard this initiative fixes — worktree-aware-prompts architecture-recommendation.md §6), post-fix ambient becomes *reliable* and the five worktree-aware agents benefit as consumers; the explicit-read instructions in those prompts remain correct and primary either way.
- Documentation discipline: no agent prompt, tool note, or `agents/` file may describe the ambient mechanism (per the fences); operator-facing documentation lives in boot-log lines + `.env.example` flag comments only.

---

## Risk Register (roll-up; dedup across phases, highest-severity-wins)

| # | Risk | Impact | Likelihood | Mitigation | Owner / source |
|---|---|---|---|---|---|
| R1 | **FE two-block surface**: runtime + API read paths (persistence.py:914 re-runs assembly live) now surface the project block WITHOUT inline KV plus the new standalone `Shared Meta KV` block; FE may render duplicate/extra `[SYSTEM CONTEXT]` cards if it dedups by title | Medium | Medium | Named owner: **frontend check before ship**. Detection: staging `GET /messages` + FE transcript inspection on one default-project and one non-default-project instance; consumers must filter by `context_kind` (distinct enum value chosen for exactly this — D7). Rollback: kill-switch removes the new block in one restart (D10) | FE team / phase1-plan.md:542 (R4), :612 (OQ1); rolled into D10 |
| R2 | **Stale-anchor implementation against drifted `latest`** (**7 commits** `2750c815..9eebf3ff` on the implicated file set — D13 supersedes the stale 3-commit list: `d348ad4e`, `80bb61dd`, `d6e30d9d`, `7a899517`, `e321bdb3`, `f965345a`, `53baef57`) | Medium | Medium | Mandatory re-pin at kickoff (Rollout step 1); D13 carries the grep-verified anchor table as the starting point; fix designs are robust to line drift (C1′ is tests-only; C2 touches the gate + builder; C3 the split; phase2-plan.md:415 R7, phase3-plan.md:465 R10, phase1-plan.md:543 R5) | Developer at kickoff |
| R3 | **Stable-id contract violated by any new block** → MessageTapSlot drops metadata + moving-timestamp fallback breaks FE merge ordering (silent) | High | Low | C0 ships `_stable_id_for` as the single mint site; CI grep gate; test 1a asserts the id; failing it is a release blocker (phase3-plan.md:456 R1, :458 R3; phase2-plan.md:413 R5) | Reviewer + CI |
| R4 | **Sequencing slip** (a defect lands before its dependency) → silently wrong behavior (mispartitioned suppression; no-op refresh) | High | Low | Hard ordering C0→C1′→C2→C3 on ONE branch (D2); C1′ is the verify gate for C2 (D12); each phase's tests fail meaningfully out of order (phase3-plan.md:460 R5) | Dispatcher + branch discipline |
| R5 | **Wrong-behavior pin flip misses** (`test_kv_metadata_not_fetched_for_system_default` must flip; scope-guide pins must stay green) | Medium | Low | Split into 1a (flip, flag ON) + 1b (kept-as-pin, flag OFF) (phase3-plan.md:271-322); 4 scope-guide pins + 6 tool-path pins enumerated (phase3-plan.md:353-363, phase2-plan.md:348-356) | Developer + reviewer |
| R6 | **Kill-switch polarity confusion post-ship** (operator sets `=0`, forgets; or expects stale-but-present and gets absent) | Low | Low | Boot-log lines make live state grep-able; OFF semantics documented in boot text + `.env.example` (phase2-plan.md:414 R6; phase1-plan.md:546 R8) | Operator docs |
| R7 | **Per-turn read contention** under high concurrency (`get_all_as_dict` serializes on the repo `_set_many_lock`) | Low | Low | Lock held only for the SELECT duration; sub-ms typical reads; bounded cost vs 700k window (phase1-plan.md:263, :614 OQ3) | Architect (noted, not blocking) |
| R8 | **Graph-side discard contract eroded by future commits** (anti-double-inject discard at graph.py **:4059-4065**, re-anchored per D13) | Low | Low | The landed comment documents the do-NOT-un-discard contract; **W11: C2/C3 PR descriptions must carry an explicit grep gate on the discard block** (`grep -n "_ = _persistent_msgs" daemon/graph.py` → line present + "intentionally discarded" comment intact) — correctness comes from the INPUT (now landed), never from un-discarding the graph-side rebuild | Reviewer |
| R9 | **Registry-discipline violation** (name bound in config.py without RESERVED entry, or name collision) | Low | Low | C0 pre-reserves the TWO surviving names (D5/D12 — the retired DEFECT 2 name is deliberately NOT reserved: a reserved-unused entry contradicts B.S.8); repo-wide grep verified zero collisions at 2750c815; reviewer greps constants.py:594-624 + config.py together (phase3-plan.md:461 R6) | Reviewer |
| R10 | **Builder serialization failure** on non-JSON-serializable KV values | Low | Low | `json.dumps` wrapped try/except → `None` + WARNING, same outcome as empty partition (phase3-plan.md:464 R9) | Covered in C2 |

Dedup notes: phase1 R1/R4 (FE surface) + phase3 placement risks merged into R1; all three phases' re-anchor risks merged into R2; stable-id risks (phase3 R1/R3, phase2 R5) merged into R3; polarity/precedent risks (phase2 R6, phase1 R8) merged into R6. Phase-local risks with no cross-phase blast radius (e.g. phase2 R1/R2 cycle/race edge cases — mitigated by existing depth-cap + exception ladder) stay in their phase files and are not re-listed here.

---

## Success Criteria

Functional (each mapped to its phase's acceptance suite):

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| 1 | Child first turn reads tree-root partition | C1′ real-service partition pin (phase2-plan.md test strategy: sentinel KV through the REAL service + file-backed SQLite; supplements the landed `test_instance_messaging_parent_resolution.py` kwargs pins) | Parent's sentinel KV appears in child's block; the landed bug-exercising test (`test_fix_reverted_child_mispartitions_to_own_id`) already fails the pre-fix shape |
| 2 | Default-project first turn emits `Shared Meta KV` block iff partition non-empty | Phase3 tests 1a + 2 (phase3-plan.md:285-339) | Block present with rows / absent when empty; fetch count assertions exact |
| 3 | Wrong-behavior pin flipped, revert pin kept | Phase3 tests 1a (flip) + 1b (OFF identical, phase3-plan.md:310-322) | `call_count == 1` ON / `== 0` OFF |
| 4 | KV block refreshes every non-retry turn; `is_retry` excluded | Phase1 tests (phase1-plan.md:353-364) | Fresh content on turn 2+; same stable id across calls; no KV on retry turns |
| 5 | Stable-id supersede holds (no checkpoint growth) | `test_kv_stable_id_supersedes` + compaction pins (phase1-plan.md:163-170, :397-406) | Identical ids; constant 1-entry contribution |
| 6 | Flag-composition matrix correct | D4 2×2 table cells pinned by NAMED tests per cell (decisions.md D4 + W7): ON×ON = phase3 test 1a + phase1 refresh suite; ON×OFF = `test_composition_c2_on_c3_off`; OFF×ON = `test_composition_c2_off_c3_on`; OFF×OFF = phase3 1b + `test_kv_block_absent_when_flag_off` | All four observable states reachable and pinned |

Non-functional / process:

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| 7 | Per-defect revert works | Staging: each flag `=0` + restart → legacy behavior (phase3-plan.md:483, phase1-plan.md:525-531; DEFECT 2 needs no revert — no flag, D12) | Byte/value-identical legacy state per flag |
| 8 | Boot-log verification surface | `grep` the TWO markers in `data/logs/ensemble.log` post-restart (both emitted AT BOOT — S13) | Both present, values match env |
| 9 | No-regression surface stays green | Tool-path pins (6), scope-guide pins (4), `test_injection_api.py:401-411`, full suite (phase2-plan.md:427-428, phase3-plan.md:475-476, phase1-plan.md:558-560) | All pass; quarantined TestAccessMemoryArchive 5 remain out of scope (phase3-plan.md:523) |
| 10 | Prompt byte-identity intact | `git diff agents/` after merge | Empty diff |
| 11 | FE check gate | Owner-checked `GET /messages` + FE transcript on staging (R1) | Completed with verdict before ship; blocking only if duplicate-card rendering is broken, else note-and-ship |
| 12 | Per-turn overhead bounded | Log-hook measurement of `get_all_as_dict` calls (phase1-plan.md:561) | ≤ 1 extra call per non-retry turn |

---

## Research Insights That Shaped the Plan

- **One read path, three independent defects.** All three defects live on the `assemble_context_messages` KV path + the first-turn injection seam (context_messages.py:1319-1360; instance_messaging.py — originally anchored `:3573-3657` @ 2750c815, now `:3637-3760` per D13). That shared geography is what makes one branch + shared prerequisite the efficient shape (D2-here), and what created the cross-plan conflicts adjudicated in D3/D4.
- **The `uuid4`-per-call factory is the root enabler.** `_make_context_message` minting a fresh id per call (context_messages.py:85-111) is why nothing can supersede anything today; the auto-load precedent (stable id + REPLACE sweep, context_messages.py:683-693, instance_messaging.py:3725 region) proves the fix pattern already works in this codebase (phase1-plan.md:128).
- **D2's fix is a one-attribute thread because the row is already fetched** (draft cite instance_messaging.py:3599-3601 → landed block :3671-3686) — no new repo round-trip, no new public kwarg, no facade-forwarding audit (phase2-plan.md rejected Options B/C precisely to avoid the 7-keyword-call blast radius). **Landed at base by 80bb61dd — adjudicated in D12.**
- **D3 needs a host, not just a gate flip** — the scope-guide branch substitutes a different message with no KV parameter (phase3-plan.md:94); the standalone host (D7) is what makes the fix composable with D1's refresh (D4).
- **Ambient unreliability was operator-visible**: the worktree-aware planning explicitly routed around it via explicit reads (architecture-recommendation.md §6) — this initiative removes the need for that workaround without invalidating it.
- **Drift is real and recent**: the implicated-file drift since every phase anchor is now **7 commits** (D13 — tidier doc-truth, the DEFECT 2 fix, the LCA arc); re-pin-at-kickoff is a hard step, not hygiene theater (R2).

---

## Gaps

**NONE.** All dispatcher-required content is answerable from these two files plus the cited phase sections; cross-checks A-E are each resolved with a recorded decision (A→D4, B→D3, C→D9, D→D5, E→D10); the three phase plans' open questions are either resolved by adjudication (phase1-plan.md:610-614 OQ1→D10, OQ2→D7, OQ3→R7; phase2-plan.md:468-472 OQ1→D1-sequencing, OQ2→unchanged-by-design note in phase2 Open Questions stands as verified-correct, OQ3→D1-sequencing; phase3-plan.md has no open-questions section) or explicitly carried as tracked risks (R7).

One non-blocking watch item for the dispatcher (not a plan gap): phase2-plan.md OQ2's belief — "no edge case where a non-system-default child reads a system-default parent's KV partition" — remains a code-review checkpoint (system-default-ness is per-project, not per-partition; the landed 80bb61dd diff does not change this, and C1′'s partition pins should eyeball the assertion once against the threaded-parent code path).
