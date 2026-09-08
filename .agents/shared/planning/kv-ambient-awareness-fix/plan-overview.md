# Plan Overview: kv-ambient-awareness-fix

Date: 2026-09-07
Author: synthesis worker via plan-creation (capstone synthesis of three defect plans)
Status: Draft — dispatcher review
Companion artifact: `decisions.md` (D1-D11 — tie-break adjudications with evidence citations)

Inputs (primary sources, read end-to-end; frozen at adjudication — do not edit):
- `phase1-plan.md` (614 lines) — DEFECT 1 cadence + §Shared prerequisite stable-id scheme
- `phase2-plan.md` (472 lines) — DEFECT 2 spawned-child mispartition
- `phase3-plan.md` (523 lines) — DEFECT 3 system-default suppression removal

All three plans anchor at worktree `2750c815`; code anchors re-verified in the recovery worktree `agents-ensemble-wt-approver` at that commit during synthesis (context_messages.py:85-111, :1315-1360; instance_messaging.py:3573-3660; constants.py:594-624 + repo-wide flag-name collision grep). Phase files use **defect numbering**; this overview uses the canonical mapping of D9 (decisions.md).

---

## Objective

Make ambient `shared_meta_kv` metadata reliable for every agent turn and every tree position: the `[SYSTEM CONTEXT]` KV block binds to the correct tree-root partition (D2), renders for system-default projects too (D3), and refreshes with fresh data on every non-retry turn under a stable, supersede-safe message id (D1 + shared prerequisite). After the fix, governor `council_manifest`, project-manager `pm_leader_instances`, and the five worktree-aware agents get correct ambient KV as a reliable bonus, while explicit `context=` hand-offs and explicit KV reads remain the primary channel.

Testable completion sentence: *On `latest` post-deploy, a spawned default-project child's first turn carries a populated tree-root-bound KV block, and an external KV write is visible in that block on the child's next non-retry turn — each reversible per-defect via one env var + restart.*

---

## Background & Defect Summary

Shared context (explorer-verified, citations in phase files): all three defects sit on one read path — the `[SYSTEM CONTEXT: Related Project]` HumanMessage assembled once per instance by `assemble_context_messages` (context_messages.py:1134) at the injection seam (instance_messaging.py:3573-3657). Three independently-switchable kill-switches (D5/D8) cover the fixes; one branch carries them in dependency order (D2-here = decisions.md D2).

| Defect | Root cause (file:line @ 2750c815) | Fix approach | Kill-switch (shape, default) | Details |
|---|---|---|---|---|
| **D1 — snapshot cadence** (KV stale after turn 1) | `project_already_injected` short-circuit returns `[]` on turn 2+ (context_messages.py:1270-1314); block built once via `build_project_context_message` → `_fetch_kv_metadata` (context_messages.py:411, :964-997); `_make_context_message` mints `uuid4` per call (context_messages.py:85-111) so re-emits append, never supersede; `is_retry` gate at instance_messaging.py:3574 is correct-by-design | Split the block: stable project block (turn 1, `project:{instance_id}`) + KV block refreshed every non-retry turn via the unified host (D4/D7) under `kv:{context_key}`; `_make_context_message` gains optional `id_=` + `_stable_id_for` helper (C0 prereq) | `ENSEMBLE_AMBIENT_KV_FRESH` (Shape B, ON; `=0` → once-per-instance legacy) | phase1-plan.md:41-108 (root cause + seam table), :112-175 (prereq), :178-264 (fix), :353-484 (tests) |
| **D2 — spawned-child mispartition** (child reads own empty KV partition) | Hardcode `_persistent_parent_id: str \| None = None` at instance_messaging.py:3620; misleading comment at :3609-3619 describes an unreachable resolution; `parent_id=None` → `_resolve_tree_root_id` returns child's own id (context_messages.py:949-950) → `_fetch_kv_metadata` reads the child's empty partition; graph-side correct build discarded by design (graph.py:3866-3879 — anti-double-inject, do NOT un-discard) | Thread real `parent_id` from the already-fetched `_proj_row` (instance_messaging.py:3599-3601; one attribute access, no extra round-trip) inside the new flag; rewrite both comments to doc-truth | `ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT` (Shape B, ON; `=0` → hardcoded-`None` legacy) | phase2-plan.md:43-131 (root cause + minimal-fix fact), :134-207 (fix + doc-truth), :302-367 (tests) |
| **D3 — system-default suppression** (default-project trees get zero KV) | Unconditional skip `if not is_system_default:` around the KV fetch (context_messages.py:1339-1345); only KV renderer is `_format_kv_metadata_section` inside `build_project_context_message` (context_messages.py:460) — the scope-guide branch (:1347-1352) has no KV host, so a gate-flip alone is insufficient | New standalone host `build_shared_meta_kv_message` + `CONTEXT_KIND_SHARED_META_KV` enum; gate the fetch + host append on the new flag; empty-partition skip; flip the wrong-behavior pin `test_kv_metadata_not_fetched_for_system_default` (tests/unit/test_context_messages.py:1199-1233) | `ENSEMBLE_KV_AMBIENT_SYSTEM_DEFAULT_ENABLED` (Shape A, ON; `=0` → byte-identical pre-fix suppression) | phase3-plan.md:53-100 (root cause), :104-213 (host + gate), :269-392 (tests incl. the flip) |

Consumers improved (explorer-verified): governor `council_manifest` visibility (incl. default-project trees — phase3-plan.md:516), project-manager `pm_leader_instances`, five worktree-aware agents as a reliability bonus. No-regression surface: tool tree-root reads (shared_meta_kv_tools.py:109-122), `context=` enqueue-only forcing (daemon/tools/instance.py:2726-2759), governor council_manifest explicit-read restore (prompt-level), prompt byte-identity fences, obsolete-schema zero-hit sweeps.

---

## Canonical Sequencing (D9 mapping + dependency edges)

Ratified unchanged (all three plans converged independently: phase1-plan.md:575, phase2-plan.md:455, phase3-plan.md:493-505); rationale and rejected alternatives in decisions.md **D1**.

| Impl step | Commit | Defect # | Artifact | Dependency edges (why here) |
|---|---|---|---|---|
| 0 | C0 | — (shared prerequisite) | phase1-plan.md §Shared prerequisite (:112-175) | Unblocks C1-C3: without deterministic ids, re-emits APPEND (grow checkpoints) and id-less messages break MessageTapSlot + hit the moving-timestamp fallback (persistence.py:527-528) |
| 1 | C1 | DEFECT 2 | phase2-plan.md | Blocks on C0 (stable id for first-turn block — phase2-plan.md:183-193). Must precede C2: suppression binding to a wrong partition key is silently untestable (phase3-plan.md:460 Risk 5) |
| 2 | C2 | DEFECT 3 | phase3-plan.md | Blocks on C0 + C1 (phase3-plan.md:509-510). Must precede C3: refreshing a suppressed block is a no-op for default-project trees (phase3-plan.md:515) |
| 3 | C3 | DEFECT 1 | phase1-plan.md body (:178-264) | Blocks on C0 + C2 + the unified emission path (decisions.md D4): the refresh builder and the D3 host are ONE builder with composed flags |

Risk/dependency note: the order is also cheapest-first (C1 = one attribute-thread; C2 = one builder + gate + config field; C3 = the split refactor), which front-loads the highest-confidence win while branch-fresh against the 3-commit drift.

---

## Branch Strategy: ONE branch, sequenced commits

**Decision: one branch** — `feature/kv-ambient-awareness-fix` off `origin/latest`, in a NEW worktree (NOT `2750c815` — read-only context; MAIN checkout externally owned). Full rationale + rejected alternatives in decisions.md **D2**; commit sequence:

```
C0  prereq(defect-1-plan §Shared prerequisite): _stable_id_for + id_= kwarg
    + all three flag names RESERVED in constants.py:594-624  [no behavior change]
C1  fix(D2): instance_messaging parent_id threading + kill-switch + tests
C2  fix(D3): unified standalone KV host + system-default gate + kill-switch + tests
C3  fix(D1): split block + per-turn refresh via the same builder + kill-switch + tests
```

One-line rationale: all three fixes share one builder seam (context_messages.py:1319-1360) and one injection seam (instance_messaging.py:3573-3657), so parallel branches serialize at merge with triple conflict surface — while the three independent kill-switches already provide per-defect revert on the live daemon (faster than `git revert`, no redeploy), and the 3-commit anchor drift (injected-notes-absorb arc `bb4e3e89`/`4e1e6698`/`c2142c69`) is re-pinned exactly once at kickoff.

---

## Rollout

1. **Anchor re-pin (mandatory, kickoff).** Branch from `origin/latest` in a new worktree; re-grep every anchor in the three phase files against the drifted file set (injected-notes-absorb arc touched `daemon/compaction.py`, `daemon/config.py`, and possibly the implicated seams — phase1-plan.md:587-597, phase2-plan.md:374-379, phase3-plan.md:401-410). If semantic intent shifted (e.g. the arc already addressed a defect transitively), halt and route to planner (phase3-plan.md:465 Risk 10). Record actual final line numbers in the PR description.
2. **Implement** C0 → C1 → C2 → C3 per the table above; each commit carries its own tests (phase-internal orders: phase2-plan.md:383-390, phase3-plan.md:412-423, phase1-plan.md:499-515).
3. **Worktree-Based Regression Proof** per defect: each phase's new tests must FAIL at the pre-fix commit with the exact defect symptom, then PASS post-fix (phase1-plan.md:369-424, phase2-plan.md:321-327 + :364-366, phase3-plan.md:390-392). DB recipe for write surfaces: file-backed SQLite `tmp_path` + NullPool + WAL + busy_timeout (NOT StaticPool — phase2-plan.md:358-362, phase1-plan.md:443-458).
4. **Merge → deploy → restart-to-flip.** All three flags default ON (D8); restart activates. Boot-log verification (one grep-able line per flag):
   - `Ambient KV freshness ENABLED (per-turn fresh)` (phase1-plan.md:335-341, :518)
   - `[ContextPersistentKV] ENSEMBLE_CONTEXT_PERSISTENT_KV_TREE_ROOT resolver: True` (phase2-plan.md:291-293, :398)
   - `[ContextMessages] kv_ambient_system_default_enabled=True` (phase3-plan.md:253-258, :431)
5. **Post-deploy checks.**
   - Spawn leader → child via `send_message`; child's first-turn checkpoint block contains the leader's KV metadata, not an empty block (phase2-plan.md:399).
   - External KV write via `shared_meta_kv` tool → next non-retry turn reflects the update (phase1-plan.md:520).
   - Default-project instance (PM session / council): `GET /messages` shows scope guide AND `Shared Meta KV` blocks when partition non-empty (phase3-plan.md:432).
   - Negative test once in staging: flip each flag `=0` → confirm legacy behavior, flip back (phase2-plan.md:401).
   - **FE two-block check (owner: frontend, gate before ship)** — see risk register R1 and decisions.md **D10**.

**Kill-switch revert paths** (any defect, any time): set the flag `=0` in `.env` → `./scripts/upgrade/restart.sh` → verify the boot log line shows DISABLED/False. OFF semantics are byte/value-identical pins in each phase's test suite (phase2-plan.md:338-346; phase3-plan.md:310-322; phase1-plan.md:414-423). Combined-state semantics (which blocks render/refresh under which flag pair) are the D4 composition table (decisions.md).

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
| R2 | **Stale-anchor implementation against drifted `latest`** (3 newer commits on the implicated file set) | Medium | Medium | Mandatory re-pin at kickoff (Rollout step 1); fix designs are robust to line drift (C1 touches one hardcode + one call site + comments; phase2-plan.md:415 R7, phase3-plan.md:465 R10, phase1-plan.md:543 R5) | Developer at kickoff |
| R3 | **Stable-id contract violated by any new block** → MessageTapSlot drops metadata + moving-timestamp fallback breaks FE merge ordering (silent) | High | Low | C0 ships `_stable_id_for` as the single mint site; CI grep gate; test 1a asserts the id; failing it is a release blocker (phase3-plan.md:456 R1, :458 R3; phase2-plan.md:413 R5) | Reviewer + CI |
| R4 | **Sequencing slip** (a defect lands before its dependency) → silently wrong behavior (mispartitioned suppression; no-op refresh) | High | Low | Hard ordering C0→C1→C2→C3 on ONE branch (D2); each phase's tests fail meaningfully out of order (phase3-plan.md:460 R5) | Dispatcher + branch discipline |
| R5 | **Wrong-behavior pin flip misses** (`test_kv_metadata_not_fetched_for_system_default` must flip; scope-guide pins must stay green) | Medium | Low | Split into 1a (flip, flag ON) + 1b (kept-as-pin, flag OFF) (phase3-plan.md:271-322); 4 scope-guide pins + 6 tool-path pins enumerated (phase3-plan.md:353-363, phase2-plan.md:348-356) | Developer + reviewer |
| R6 | **Kill-switch polarity confusion post-ship** (operator sets `=0`, forgets; or expects stale-but-present and gets absent) | Low | Low | Boot-log lines make live state grep-able; OFF semantics documented in boot text + `.env.example` (phase2-plan.md:414 R6; phase1-plan.md:546 R8) | Operator docs |
| R7 | **Per-turn read contention** under high concurrency (`get_all_as_dict` serializes on the repo `_set_many_lock`) | Low | Low | Lock held only for the SELECT duration; sub-ms typical reads; bounded cost vs 700k window (phase1-plan.md:263, :614 OQ3) | Architect (noted, not blocking) |
| R8 | **Graph-side discard contract eroded by future commits** (anti-double-inject discard at graph.py:3866-3879) | Low | Low | Comment rewrite documents the partition-equivalence contract post-C1; reviewers preserve it (phase2-plan.md:202-204, :412 R4) | Reviewer |
| R9 | **Registry-discipline violation** (name bound in config.py without RESERVED entry, or name collision) | Low | Low | C0 pre-reserves all three names; repo-wide grep verified zero collisions at 2750c815; reviewer greps constants.py:594-624 + config.py together (phase3-plan.md:461 R6) | Reviewer |
| R10 | **Builder serialization failure** on non-JSON-serializable KV values | Low | Low | `json.dumps` wrapped try/except → `None` + WARNING, same outcome as empty partition (phase3-plan.md:464 R9) | Covered in C2 |

Dedup notes: phase1 R1/R4 (FE surface) + phase3 placement risks merged into R1; all three phases' re-anchor risks merged into R2; stable-id risks (phase3 R1/R3, phase2 R5) merged into R3; polarity/precedent risks (phase2 R6, phase1 R8) merged into R6. Phase-local risks with no cross-phase blast radius (e.g. phase2 R1/R2 cycle/race edge cases — mitigated by existing depth-cap + exception ladder) stay in their phase files and are not re-listed here.

---

## Success Criteria

Functional (each mapped to its phase's acceptance suite):

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| 1 | Child first turn reads tree-root partition | `test_instance_messaging_first_turn_kv_partition.py` (real spawn + real send_message; phase2-plan.md:304-327) | Parent's sentinel KV appears in child's block; pre-fix worktree run FAILS with empty-partition symptom |
| 2 | Default-project first turn emits `Shared Meta KV` block iff partition non-empty | Phase3 tests 1a + 2 (phase3-plan.md:285-339) | Block present with rows / absent when empty; fetch count assertions exact |
| 3 | Wrong-behavior pin flipped, revert pin kept | Phase3 tests 1a (flip) + 1b (OFF identical, phase3-plan.md:310-322) | `call_count == 1` ON / `== 0` OFF |
| 4 | KV block refreshes every non-retry turn; `is_retry` excluded | Phase1 tests (phase1-plan.md:353-364) | Fresh content on turn 2+; same stable id across calls; no KV on retry turns |
| 5 | Stable-id supersede holds (no checkpoint growth) | `test_kv_stable_id_supersedes` + compaction pins (phase1-plan.md:163-170, :397-406) | Identical ids; constant 1-entry contribution |
| 6 | Flag-composition matrix correct | D4 table cells pinned by the three phases' flag-OFF/ON suites (decisions.md D4) | All four observable states reachable and pinned |

Non-functional / process:

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| 7 | Per-defect revert works | Staging: each flag `=0` + restart → legacy behavior (phase2-plan.md:401, phase3-plan.md:483, phase1-plan.md:525-531) | Byte/value-identical legacy state per flag |
| 8 | Boot-log verification surface | `grep` the three markers in `data/logs/ensemble.log` post-restart | All three present, values match env |
| 9 | No-regression surface stays green | Tool-path pins (6), scope-guide pins (4), `test_injection_api.py:401-411`, full suite (phase2-plan.md:427-428, phase3-plan.md:475-476, phase1-plan.md:558-560) | All pass; quarantined TestAccessMemoryArchive 5 remain out of scope (phase3-plan.md:523) |
| 10 | Prompt byte-identity intact | `git diff agents/` after merge | Empty diff |
| 11 | FE check gate | Owner-checked `GET /messages` + FE transcript on staging (R1) | Completed with verdict before ship; blocking only if duplicate-card rendering is broken, else note-and-ship |
| 12 | Per-turn overhead bounded | Log-hook measurement of `get_all_as_dict` calls (phase1-plan.md:561) | ≤ 1 extra call per non-retry turn |

---

## Research Insights That Shaped the Plan

- **One read path, three independent defects.** All three defects live on the `assemble_context_messages` KV path + the first-turn injection seam (context_messages.py:1319-1360; instance_messaging.py:3573-3657 — both re-verified at 2750c815). That shared geography is what makes one branch + shared prerequisite the efficient shape (D2-here), and what created the cross-plan conflicts adjudicated in D3/D4.
- **The `uuid4`-per-call factory is the root enabler.** `_make_context_message` minting a fresh id per call (context_messages.py:85-111) is why nothing can supersede anything today; the auto-load precedent (stable id + REPLACE sweep, context_messages.py:683-693, instance_messaging.py:3666) proves the fix pattern already works in this codebase (phase1-plan.md:128).
- **D2's fix is a one-attribute thread because the row is already fetched** (instance_messaging.py:3599-3601) — no new repo round-trip, no new public kwarg, no facade-forwarding audit (phase2-plan.md:105-131 rejected Options B/C precisely to avoid the 7-keyword-call blast radius).
- **D3 needs a host, not just a gate flip** — the scope-guide branch substitutes a different message with no KV parameter (phase3-plan.md:94); the standalone host (D7) is what makes the fix composable with D1's refresh (D4).
- **Ambient unreliability was operator-visible**: the worktree-aware planning explicitly routed around it via explicit reads (architecture-recommendation.md §6) — this initiative removes the need for that workaround without invalidating it.
- **Drift is real and recent**: the injected-notes-absorb arc (3 commits) postdates every phase anchor; re-pin-at-kickoff is a hard step, not hygiene theater (R2).

---

## Gaps

**NONE.** All dispatcher-required content is answerable from these two files plus the cited phase sections; cross-checks A-E are each resolved with a recorded decision (A→D4, B→D3, C→D9, D→D5, E→D10); the three phase plans' open questions are either resolved by adjudication (phase1-plan.md:610-614 OQ1→D10, OQ2→D7, OQ3→R7; phase2-plan.md:468-472 OQ1→D1-sequencing, OQ2→unchanged-by-design note in phase2 Open Questions stands as verified-correct, OQ3→D1-sequencing; phase3-plan.md has no open-questions section) or explicitly carried as tracked risks (R7).

One non-blocking watch item for the dispatcher (not a plan gap): phase2-plan.md:471 OQ2's belief — "no edge case where a non-system-default child reads a system-default parent's KV partition" — remains a code-review checkpoint at C1 (system-default-ness is per-project, not per-partition; the C1 diff does not change this, but the reviewer should eyeball the assertion once against the threaded-parent code path).
