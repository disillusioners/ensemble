# Decisions — skill-keeper confinement (urgent-fix lane)

**Lane:** `fix/skill-keeper-confinement` @ worktree `/home/nea/ensemble-src-wt-skill-keeper-fix`
**Base:** `66d8750448f52a1e40ac82c7e58cbc425c566122` (= origin/latest at claim time)
**Date:** 2026-10-22 · **Author:** coder (working lead)
**Sources:** `.agents/shared/planning/skill-system-audit/HANDOVER.md` §3-P0 (lines 41-43) + §8 recs 1/2/3/7, `incident-2026-10-21.md` §5 (L1/L2), §7 (enforcement gaps), §8 (ranked recs), plus the maintenancer ADDENDUM (feedback-producer evidence, resolve-endpoint guard, land.py / credential-hazard benefit surfaces).

Where the numbered commission items and HANDOVER §8 differ, the commission wins (per mandate); deviations from HANDOVER recs are flagged below.

---

## D1 — Evolution-lane gate: DEFAULT OFF (commission item 2)

**Decision:** `SkillEvolutionConfig.evolution_lane_enabled = False` (default) + dedicated kill-switch env `ENSEMBLE_SKILL_EVOLUTION_ENABLED` (per-call resolution, env wins over config, invalid env value → `ValueError` fail-closed). Mirrors the capture-gate shape exactly (`skill_job_dispatcher.py:74-108` precedent — same resolver contract, same no-op-returns-`None` admission contract).

**Why OFF:**
1. The incident proved the lane is an instruction-injection surface into a live executor: direction text is verbatim filer free text that becomes the Tier-3 prompt (incident §5 L2), and the burst produced real DB damage *without any repo write* (gen `2d1c3022` = −81% shrink under a false label — a DB-only mutation). Gating admission is therefore warranted independently of the L1 tool strip.
2. Asymmetry precedent: the CAPTURED lane got default-OFF discipline on day one (2026-09-17); the strictly-more-dangerous mutation lane had no switch at all. Default OFF restores the discipline the system already ratified.
3. Default OFF **is** the durable post-promote off-switch — an operator opting in (config field or env) is a deliberate, visible act. Default ON would mean "the ungated status quo's successor" ships enabled, and the incident's own quiet-period history (10-20 20:32, 10-21 10:15/10:43 pre-burst FIX runs) shows the lane fires unattended.

**Blast radius when OFF — producer coverage (CORRECTED at review iteration 1):** every producer of `skill_evolution` jobs is gated. The dispatcher is the primary chokepoint (all wired-dispatcher paths funnel through `enqueue_evolution`); the ONE path that bypasses the dispatcher — `InstanceManager._run_skill_metric_scan`'s dispatcher-unavailable fallback (`manager.py`, direct `job_repo.create`) — now consults the SAME kill-switch (env + configured default) before minting a Tier-3 row directly (review finding 1). Full table:

| Producer | Entry | Gated? | Mechanism |
|---|---|---|---|
| `skill_fix` tool (any agent holding dynamic-skill) | `skill_tools.py` → `dispatch_fix` → `enqueue_evolution` | YES | returns `None`; tool returns honest "⛔ NOT dispatched" envelope |
| Admin HTTP `POST /api/skills/{id}/fix` | `routers/skills.py` `post_fix` → `dispatch_fix` | YES | **503** with reason (was: fake 202 with `job_id: null`) |
| Trigger-row `evolve_fix` (Tier-1 scan follow-through, dispatcher wired) | `manager.py` `_run_skill_metric_scan` → `enqueue_evolution` | YES | returns `None`; flagged item logged, no job |
| Trigger-row `evolve_fix`, **dispatcher-unavailable fallback** (boot/unwired window; direct `job_repo.create`) | `manager.py` `_run_skill_metric_scan` else-branch | YES (as of review iteration 1 — previously an UNGATED direct-create bypass of the gate) | fallback consults `_resolve_evolution_enabled(config default)` before the direct create; refuses with warning + no row when OFF. Tier-2 `skill_analysis` rows via the same fallback stay live (parity with the dispatcher-side scope) |
| **Feedback-filed fix (the 19:31:18Z producer, ADDENDUM item 1)** | `skill_feedback` improvement_note → agent files `skill_fix` / metric-scan flags → the funnels above | YES | `dispatch_fix` → `None` (pinned: `test_feedback_filed_fix_request_refused_when_gate_off`); fallback path covered by the row above |

**NOT gated (deliberate):** `enqueue_analysis` (Tier-2 read-only analysis jobs — the audit duty stays alive), `enqueue_metric_scan` (the scan itself continues; it just cannot admit FIX jobs), `enqueue_capture` (its own switch, default OFF since 2026-09-17), and the keeper's **direct execution tools** (`skill_evolve` / `analyze_skill` on an already-running keeper instance call `SkillEvolutionService.evolve_skill` inline — the gate stops job ADMISSION, not execution). Residual: an agent that can already run (i.e. was admitted some other way) can still execute an evolution inline. Closing that is HANDOVER rec 6 (master skill-system switch) — deferred, see D7.

**Env-vs-config resolution order:** `ENSEMBLE_SKILL_EVOLUTION_ENABLED` set (non-empty) → wins (live emergency-off, no config change, no cache — per-call `os.environ` read like capture); unset/empty → constructed `evolution_lane_enabled` wired from config at `manager.py` (`SkillJobDispatcher(..., evolution_lane_enabled=..., fix_cooldown_seconds=...)`). This honors the HANDOVER "no-new-ENSEMBLE_*-flags without owner authorization" policy note by making the config field the declarative default and the env the kill-switch — both surfaces documented in `SkillEvolutionConfig` field descriptions.

## D2 — FIX abuse controls: idempotency_key + per-(project,skill) cooldown (commission item 2)

- **`idempotency_key = "skill_fix:<normalized_project>:<skill_id>"`** on every FIX admission (HANDOVER §8 rec 2 "set idempotency_key" — implemented). Non-terminal duplicate collapses onto the live job at the DB layer (`JobQueueService.enqueue` → `create_or_get_by_idempotency_key`). Closes the concurrent re-file class.
- **In-process FIX cooldown**, keyed `(normalized_project_id, skill_id)` → monotonic timestamp of last ACCEPTED admission; default 1800s (`SkillEvolutionConfig.fix_cooldown_seconds`, 0 disables). Refusal → `None` + warning log. Stamp on BOTH outcomes (fresh row or dedup-hit). RAM-only by design — a rate limiter, not durable state; the idempotency key covers the non-terminal window across restarts. Kills the L4 amplifier (2026-10-21: same skill re-FIXed 3× in 38 min — 1800s ≥ the whole observed burst).
- Cooldown/idempotency are **FIX-only**: DERIVED/CAPTURED admissions via `enqueue_evolution` are gated by the kill-switch but not cooldown-keyed (the amplifier class is FIX re-filing; DERIVED creates a distinct variant row). Disclosed scope cut.

## D3 — Allowlist confinement: read-only surface chosen (commission item 1)

**New `tools.allow`:** `["read_file", "list_directory", "glob_files", "grep_files", "self", "help", "knowledge", "dynamic-skill", "skill-evolution", "shared_meta_kv"]`
**New `tools.deny`:** `["bash", "proc", "service", "write_file", "edit_file"]`
**meta version:** 0.1.0 → 0.2.0.

- Granular-tool spelling is the supported mechanism (critic precedent: `agents/critic/meta.json` mixes literal tool names + categories; `resolve_tool_filter` (`daemon/tools/instance.py:392`) expands category entries and adds unknown entries as literal tool names). The four granted names are the complete read half of the `filesystem` category (`list_directory`, `read_file`, `glob_files`, `grep_files` at `filesystem.py:584/657/748/1040`); `write_file`/`edit_file` are its only mutating tools — both denied.
- **Why the smallest set keeps audit duties functional:** the keeper reads skill content through `skill_view` (DB), and template/repo reads (the audit evidence path) through the four read tools. Nothing in its charter writes files.
- **DEVIATION from the commission's literal text (disclosed):** also stripped **`service`** (and denied it). `service_start(name, command: argv)` (`service_tools.py:198`) executes arbitrary argv — a bash-equivalent escape hatch (`git add/commit`, `python3 -c`, `bash -c` all work argv-shaped). A confinement that leaves an argv-execution primitive is not confinement; soul.md never granted a service duty (the tool section at :142 never mentioned it). Also denied **`proc`** (background-process spawn — same argv-execution class) for the same reason.
- **Deny list rationale:** `resolve_tool_filter` applies deny AFTER allow (deny wins), so the deny entries are structural: a future allow-list regression re-adding `"service"` would still not restore `service_start`. `filesystem` is NOT in deny (that would strip the four read tools too — category-level deny) — instead the two write tools are denied by literal name.
- **HANDOVER §8 rec 3 (widen `_check_prompt_artifact_write` to ALL files under `agents/<name>/`) — DEFERRED, not implemented.** The widening itself is small (extend `_depth2_match` past the `VERSIONED_PROMPT_ARTIFACTS` check), but it changes a fleet-wide shared guard for every agent holding `filesystem`, with refusal-message and drift-baseline (`agents/.prompt-artifacts.sha256`) implications, and it guards NOTHING for skill-keeper now that (a) write tools are absent from the allowlist and (b) bash is gone — defense-in-depth for a surface the agent no longer holds is pure scope. The incident's actual bypass (`skill-set.yaml` via bash) is dead at the allowlist layer. Routed as a standalone follow-up (its own review lane: all 40+ agents' write_file flows re-audited).

**Benefit surfaces closed by the strip (ADDENDUM items 3-4, cited for the record):**
- `/tmp/skillfix/land.py` direct-SQL landing class: structurally dead — no bash, no `python3`, no process spawn. (It never executed during the incident — paused mid-edit 19:53:01Z — but the capability existed.)
- Credential-hazard class: the keeper sourced repo `.env` into its shell and grepped `POSTGRES_*` (19:34:37-42Z). With no shell, no env-reading tool, and DB access only via skill-evolution service tools (which never surface connection material), this class is closed for this agent.

## D4 — Charter fencing (commission item 3)

`soul.md` rewritten at the two tool-permission sites:
- `### bash, filesystem` section (:142-143 pre-change) → `### filesystem — READ-ONLY subset only (...)` naming the four granted tools, stating `write_file`/`edit_file`/`bash`/`proc`/`service` are denied at the tool layer, and that repo-source mutation "is not granted to me at any layer".
- Must-NOT (:166 area) gains `❌ Mutate the repository — my allowlist grants NO bash, NO write_file, NO edit_file, and NO process/service spawning; the tools.deny list enforces this even against allow-list edits.`
- The old "run migrations when an audit demands shell-level evidence" line is DELETED (it granted shell in prose). Pinned by tests (`TestSkillKeeperCharterFencing`) asserting the old heading is gone, the new structural statements exist, and no line claims shell access.

## D5 — Resolve-endpoint guard (ADDENDUM item 2)

**Decision: service-level below-threshold refusal** in `SkillEvolutionService.check_ab_test_resolution` Path 0 (`skill_evolution_service.py`, forced-winner branch): `winner_id` provided while `comparisons < ab_sample_size` → `ValueError` (→ HTTP 400 at the router). Placed AFTER variant validation, BEFORE any side effect — refusal asserts zero repo calls (pinned).

**Why here (not router-only):** the service is the single chokepoint for forced resolves; the agent tool `skill_resolve_ab` never passes `winner_id` (agents were never able to force via the tool surface — the incident path was bash curl → admin endpoint, which the bash strip already kills structurally). Guarding the service covers the endpoint AND any future caller; the router maps the `ValueError` to 400 with the guard's message.

**Authority contract preserved:** forced resolve at/above sample size still works (`reason='forced_winner'`, pinned), and the `max_extensions` Path-3 force-resolve is untouched. The damage shape of the incident (winner forced at comparisons=2/10, incumbent arm killed) is exactly the refused shape.

**Residual (disclosed):** a caller that can reach the endpoint could still force-resolve at exactly `sample_size` with a cherry-picked winner. Accepted: reaching sample size requires real comparison data; "restrict to non-agent principals" was rejected because there is no agent/principal discriminator on this unauthenticated admin endpoint today (auth-ON deployments would need the AuthContext spine — same class as ticket `257488dc`, out of scope here).

## D6 — Handover items deliberately NOT implemented (with reasons)

- **Tier-3 FIX direction quarantine/approval (HANDOVER rec 2 tail + commission "implement or defer"):** DEFERRED. There is no approval surface in this lane (who approves, via what channel, with what storage) — a regex content-scan would be a false-safety half-measure (directions like "fix the leak in skill_search_service" are indistinguishable from legit content fixes by pattern). The structural controls (D1/D2/D3) close the incident class; direction-content approval is its own design commission (likely folds into the P3 master-switch work).
- **P1 items (test-stub fix in `test_llm_failover_v2_resilience`, `load_skill` body-injection bypass, DELETE de-classification, server-side agent_id derivation):** OUT OF SCOPE per commission — none trivially co-located (all live in `skill_search_service` / `skill_bank` router land, not the dispatcher/allowlist files this lane touches).
- **P2 gated DB cleanup, P2 system-scoped HIGH standing bypasses, P3 master skill-system switch, bank-clone provenance audit (rec 8), soul.md as enforcement (rec 7 — implemented as defense-in-depth ONLY, per rec):** out of scope, unchanged.
- **`current_instance_id` audit use (dispatch_fix param, deleted unused):** left as-is (documented reservation; wiring it into lineage is Phase-5 audit-hook work, no incident relevance).

## D7 — Test evidence + disclosed pre-existing failures

**New pack:** `tests/unit/test_skill_evolution_lane_gate.py` — 43 tests, all pass. Covers: resolver semantics (env/config precedence, invalid→ValueError), gate OFF no-op for all three evolution types, the feedback-filed-fix refusal pin (ADDENDUM item 1), FIX idempotency-key shape, cooldown refuse/pass shapes (+ 0-disables), skill_fix truthfulness (docstring + both behavioral envelopes), allowlist pins (allow/deny + resolved-filter end-to-end with no write-capable tool), charter pins, forced-resolve guard (refusal with zero side effects / admission at sample size / unforced tree untouched / variant validation order).

**Existing packs updated (behavior intentionally changed by this lane):** `test_skill_job_dispatcher.py` (autouse env-enable fixtures on `TestEnqueueEvolution` + `TestRoutingInvariant`, mirroring the capture-killswitch precedent), `test_skill_evolution_service.py` meta-pin (bash/filesystem assertions flipped to confinement assertions), `tests/unit/routers/test_skills.py` (+ `test_fix_503_when_gate_refuses`).

**Ran (per-variable env scrub; unit-only, timeout-capped):** lane-gate 43/43 · dispatcher+metric-scan+skills-router 106/106 · skill_tools+killswitch-verification+config 100 pass / 2 PRE-EXISTING env-dependent failures (`embedding_base_url` resolved from ambient host env — reproduced identically with changes stashed) · evolution-service+killswitch 131-132 pass / 1 PRE-EXISTING flake (`IndexError` in SQLAlchemy cyextension resultproxy under concurrent `to_thread` on the shared StaticPool SQLite connection in the `asyncio.gather` side-effect path — reproduced identically on base with changes stashed; my diff does not touch that code path) · e2e/evolution-tools 23 pass + 49 deselected (live-DB markers). `py_compile` clean on all edited files. ruff not available in either venv on this host (not run).

**Review iteration 1 re-run:** lane-gate pack (now 46: +3 `TestManagerDispatcherWiring` pins) + metric-scan pack (now 11: +3 fallback pins — refuse-when-OFF, config-enabled admit, analysis-not-gated; the pre-existing FIX-fallback admission test now pins env-ON) → **57/57 PASS**; neighbor packs re-run (dispatcher + skills-router + skill_tools) → **135/135 PASS**.

## D8 — Deferred residuals (explicit)

1. HANDOVER rec 3 (prompt-artifact guard widening) — deferred, see D3.
2. Tier-3 direction quarantine/approval — deferred, see D6.
3. Inline `skill_evolve` execution on a running keeper — not gated (D1 residual); folds into the P3 master-switch.
4. `skill_resolve_ab` docstring decision-tree does not mention the Path-0 refusal (service docstring does) — cosmetic, next doc sweep.
5. REST-ingress `source='api'` class (cn 257488dc) untouched here.
6. **Metric-scan fallback cooldown/dedup parity (review iteration 1 disclosure):** the fallback branch is GATED (D1 row 4) but carries NO cooldown and NO idempotency dedup — `job_repo.create` inserts directly (plain INSERT; the dedup semantics live in `JobQueueService.enqueue` → `create_or_get_by_idempotency_key`, a different repo method). Achieving parity would mean refactoring the fallback onto the enqueue/create-or-get shape — exactly what the review scoped OUT. Residual exposure: in a dispatcher-unwired window WITH the lane explicitly enabled (config or env), repeated scans could mint repeat FIX rows for the same skill. Accepted because (a) the fallback exists only while `_skill_job_dispatcher` is None (early boot / broken wiring — not the production steady state), and (b) with the lane at its default OFF the fallback creates nothing at all.
7. **Review iteration 1 — wiring contract fix recorded:** `set_job_queue_service` previously coerced a configured `fix_cooldown_seconds=0` back to 1800 via `or 1800` (violating the config contract "0 disables the cooldown"); now only absent/None falls back to 1800. Pinned by `TestManagerDispatcherWiring` (explicit 0 survives, absent→1800, lane flag rides the wiring). Docstring of `check_ab_test_resolution` Path 0 rewritten — the stale "Skips the sample-size / threshold / extension gates entirely" clause contradicted the below-sample-size refusal.
